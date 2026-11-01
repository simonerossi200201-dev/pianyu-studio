# -*- coding: utf-8 -*-
"""「0 多账号」后端：Google Flow 多账号池、分组管理、Hedra 备份双向导入导出、
浏览器缓存智能清理（保留 Cookie/登录态）、Google+Flow 自动登录（含 2FA TOTP）、
积分探测与账号组最大 3 浏览器并发轮转（MultiAccountGroupRunner）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from . import crypto_vault, db, events, paths
from .engines import chrome
from .engines.flow_api import FLOW_HOME, normalize_flow_mode, read_credits
from .errors import OranteError

log = logging.getLogger("orante.multi_accounts")

MAX_CONCURRENT_BROWSERS = 3
DEFAULT_FLOW_URL = "https://labs.google/fx/zh/tools/flow"
DEFAULT_MODEL = "Veo 3.1 - Lite [Lower Priority]"

# 安全清理目录：只删缓存，绝不碰 Cookie / Local Storage / IndexedDB / Login Data
SAFE_CACHE_SUBDIRS = (
    "Cache",
    "Code Cache",
    "GPUCache",
    "Service Worker/CacheStorage",
    "Service Worker/ScriptCache",
    "DawnCache",
    "GrShaderCache",
    "ShaderCache",
    "GPUPersistentCache",
    "Crashpad",
)
DEEP_CACHE_SUBDIRS = (
    "optimization_guide_model_store",
    "component_crx_cache",
)
PRESERVED_LOGIN_ITEMS = frozenset({
    "cookies",
    "cookies-journal",
    "local storage",
    "indexeddb",
    "login data",
    "login data-journal",
    "web data",
    "web data-journal",
    "preferences",
    "secure preferences",
    "local state",
    "session storage",
    "sessions",
    "network",
})


def sanitize_email_dir(email: str) -> str:
    """把邮箱转成安全的文件夹名：保留 @ . _ - 与字母数字。"""
    s = (email or "").strip().lower()
    s = re.sub(r"[^a-z0-9@._\-]+", "_", s)
    return s or "unknown_account"


def multi_profiles_root() -> Path:
    paths.reset()
    d = paths.DATA / "multi_chrome" / "Profiles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def profile_dir_for(acc: dict) -> Path:
    raw = (acc.get("profile_dir") or "").strip()
    if raw:
        p = Path(raw)
        if p.is_absolute():
            return p
        return paths.DATA / raw
    return multi_profiles_root() / sanitize_email_dir(acc.get("email") or acc.get("id") or "default")


def _allocate_port() -> int:
    taken: set[int] = set()
    try:
        for r in db.rows("SELECT port FROM accounts WHERE port IS NOT NULL"):
            if r.get("port"):
                taken.add(int(r["port"]))
        for r in db.rows("SELECT port FROM multi_accounts WHERE port IS NOT NULL"):
            if r.get("port"):
                taken.add(int(r["port"]))
    except Exception:
        pass
    return chrome.free_port(9400, taken)


def _decorate_account(row: dict, groups_by_id: dict[str, dict] | None = None,
                      include_secrets: bool = False, probe: bool = False) -> dict:
    a = dict(row)
    a["meta"] = db.unj(a.get("meta"), {}) or {}
    a["is_favorite"] = bool(int(a.get("is_favorite") or 0))
    a["flow_mode"] = normalize_flow_mode(a.get("flow_mode"), default="frames")
    a["model"] = a.get("model") or DEFAULT_MODEL
    a["flow_credits"] = int(a.get("flow_credits") if a.get("flow_credits") is not None else -1)
    pw_enc = a.get("password_enc") or ""
    totp_enc = a.get("totp_secret_enc") or ""
    a["has_password"] = bool(pw_enc)
    a["has_totp"] = bool(totp_enc)
    a["password_masked"] = "••••••••" if pw_enc else ""
    a.pop("password_enc", None)
    a.pop("totp_secret_enc", None)
    if include_secrets:
        a["password"] = crypto_vault.decrypt_secret(pw_enc)
        a["totp_secret"] = crypto_vault.decrypt_secret(totp_enc)

    gid = a.get("group_id") or ""
    if groups_by_id is None:
        grp = db.one("SELECT * FROM multi_account_groups WHERE id=?", (gid,)) if gid else None
    else:
        grp = groups_by_id.get(gid)
    a["group_name"] = (grp or {}).get("name") or ""
    a["group_color"] = (grp or {}).get("color") or ""

    port = int(a.get("port") or 0)
    alive = bool(port and chrome.port_alive(port)) if probe else False
    a["alive"] = alive
    if a.get("flow_credits") == 0:
        a["status"] = "quota_exhausted"
    elif alive:
        a["status"] = "running"
    elif a.get("status") == "running" and probe and not alive:
        a["status"] = "idle"
    else:
        a["status"] = a.get("status") or "idle"
    return a


# ---------------------------------------------------------------------------
# 分组管理
# ---------------------------------------------------------------------------

def list_groups() -> list[dict]:
    groups = db.rows("SELECT * FROM multi_account_groups ORDER BY sort_order, created_at, id")
    accs = db.rows("SELECT id, group_id, flow_credits, status FROM multi_accounts")
    by_gid: dict[str, list[dict]] = {}
    for a in accs:
        by_gid.setdefault(a.get("group_id") or "", []).append(a)
    out = []
    for g in groups:
        g = dict(g)
        g["meta"] = db.unj(g.get("meta"), {}) or {}
        members = by_gid.get(g["id"], [])
        avail = [
            m for m in members
            if int(m.get("flow_credits") if m.get("flow_credits") is not None else -1) != 0
            and (m.get("status") or "") != "quota_exhausted"
        ]
        known_credits = [
            int(m["flow_credits"]) for m in members
            if m.get("flow_credits") is not None and int(m["flow_credits"]) >= 0
        ]
        g["account_count"] = len(members)
        g["available_count"] = len(avail)
        g["total_credits"] = sum(known_credits) if known_credits else -1
        out.append(g)
    return out


def create_group(name: str, color: str = "#0969da", group_id: str | None = None,
                 created_at: int | None = None) -> dict:
    name = (name or "").strip()
    if not name:
        raise OranteError("分组名称不能为空")
    existing = db.one("SELECT * FROM multi_account_groups WHERE name=?", (name,))
    if existing:
        if color and color != existing.get("color"):
            db.run("UPDATE multi_account_groups SET color=? WHERE id=?", (color, existing["id"]))
            existing["color"] = color
        return existing
    gid = (group_id or "").strip() or f"grp_{int(time.time() * 1000)}_{os.urandom(3).hex()}"
    if db.one("SELECT 1 FROM multi_account_groups WHERE id=?", (gid,)):
        gid = f"grp_{int(time.time() * 1000)}_{os.urandom(3).hex()}"
    ts = int(created_at if created_at is not None else int(time.time() * 1000))
    cnt = (db.one("SELECT COUNT(*) n FROM multi_account_groups") or {}).get("n") or 0
    db.run(
        "INSERT INTO multi_account_groups(id,name,color,created_at,sort_order,meta) VALUES(?,?,?,?,?,?)",
        (gid, name, color or "#0969da", ts, int(cnt), "{}"),
    )
    return db.one("SELECT * FROM multi_account_groups WHERE id=?", (gid,)) or {}


def update_group(gid: str, **fields) -> dict:
    g = db.one("SELECT * FROM multi_account_groups WHERE id=?", (gid,))
    if not g:
        raise OranteError(f"找不到分组：{gid}")
    sets, args = [], []
    if "name" in fields and (fields["name"] or "").strip():
        sets.append("name=?")
        args.append(fields["name"].strip())
    if "color" in fields and fields["color"]:
        sets.append("color=?")
        args.append(str(fields["color"]).strip())
    if "sort_order" in fields and fields["sort_order"] is not None:
        sets.append("sort_order=?")
        args.append(int(fields["sort_order"]))
    if sets:
        db.run(f"UPDATE multi_account_groups SET {', '.join(sets)} WHERE id=?", (*args, gid))
    return db.one("SELECT * FROM multi_account_groups WHERE id=?", (gid,)) or {}


def delete_group(gid: str) -> dict:
    db.run("UPDATE multi_accounts SET group_id='' WHERE group_id=?", (gid,))
    db.run("DELETE FROM multi_account_groups WHERE id=?", (gid,))
    return {"ok": True, "deleted_id": gid}


# ---------------------------------------------------------------------------
# 账号增删改查与批量导入
# ---------------------------------------------------------------------------

def list_accounts(include_secrets: bool = False, probe: bool = False) -> list[dict]:
    groups = {g["id"]: g for g in db.rows("SELECT * FROM multi_account_groups")}
    rows = db.rows("SELECT * FROM multi_accounts ORDER BY is_favorite DESC, sort_order ASC, created ASC, id ASC")
    return [_decorate_account(r, groups, include_secrets=include_secrets, probe=probe) for r in rows]


def get_account(acc_id_or_email: str, include_secrets: bool = False, probe: bool = False) -> dict:
    key = (acc_id_or_email or "").strip()
    r = db.one("SELECT * FROM multi_accounts WHERE id=? OR lower(email)=lower(?)", (key, key))
    if not r:
        raise OranteError(f"找不到多账号：{acc_id_or_email}")
    return _decorate_account(r, include_secrets=include_secrets, probe=probe)


def add_account(
    email: str,
    password: str = "",
    totp_secret: str = "",
    backup_email: str = "",
    group_id: str = "",
    is_favorite: bool | int = 0,
    project_url: str = "",
    flow_mode: str = "frames",
    model: str = DEFAULT_MODEL,
    note: str = "",
    meta: dict | None = None,
    account_id: str | None = None,
) -> dict:
    email_clean = (email or "").strip()
    if not email_clean:
        raise OranteError("账号邮箱不能为空")
    existing = db.one("SELECT * FROM multi_accounts WHERE lower(email)=lower(?)", (email_clean,))
    if not existing and account_id:
        existing = db.one("SELECT * FROM multi_accounts WHERE id=?", (str(account_id).strip(),))
    now = db.now()
    norm_mode = normalize_flow_mode(flow_mode, default="frames")
    if existing:
        aid = existing["id"]
        pw_enc = crypto_vault.encrypt_secret(password) if password else (existing.get("password_enc") or "")
        totp_enc = crypto_vault.encrypt_secret(totp_secret) if totp_secret else (existing.get("totp_secret_enc") or "")
        b_email = (backup_email or "").strip() or (existing.get("backup_email") or "")
        gid = group_id if group_id != "" else (existing.get("group_id") or "")
        fav = int(bool(is_favorite)) if is_favorite is not None else int(existing.get("is_favorite") or 0)
        purl = (project_url or "").strip() or (existing.get("project_url") or "")
        mdl = (model or "").strip() or (existing.get("model") or DEFAULT_MODEL)
        nt = note if note != "" else (existing.get("note") or "")
        cur_meta = db.unj(existing.get("meta"), {}) or {}
        if isinstance(meta, dict):
            cur_meta.update(meta)
        db.run(
            """UPDATE multi_accounts
                  SET email=?, password_enc=?, totp_secret_enc=?, backup_email=?,
                      group_id=?, is_favorite=?, project_url=?, flow_mode=?, model=?,
                      note=?, meta=?, updated=?
                WHERE id=?""",
            (
                email_clean, pw_enc, totp_enc, b_email,
                gid, fav, purl, norm_mode, mdl,
                nt, db.j(cur_meta), now, aid,
            ),
        )
        return get_account(aid)

    aid = (str(account_id).strip() if account_id else "") or db.next_id("multi_account", "M", 4)
    port = _allocate_port()
    prof_rel = f"multi_chrome/Profiles/{sanitize_email_dir(email_clean)}"
    pw_enc = crypto_vault.encrypt_secret(password)
    totp_enc = crypto_vault.encrypt_secret(totp_secret)
    cnt = (db.one("SELECT COUNT(*) n FROM multi_accounts") or {}).get("n") or 0
    db.run(
        """INSERT INTO multi_accounts(
             id, email, password_enc, totp_secret_enc, backup_email,
             group_id, is_favorite, project_url, flow_mode, model,
             port, profile_dir, flow_credits, credits_updated_at,
             status, note, meta, sort_order, created, updated
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            aid, email_clean, pw_enc, totp_enc, (backup_email or "").strip(),
            (group_id or "").strip(), int(bool(is_favorite)), (project_url or "").strip(),
            norm_mode, (model or DEFAULT_MODEL).strip(),
            port, prof_rel, -1, "",
            "idle", (note or "").strip(), db.j(meta or {}), int(cnt), now, now,
        ),
    )
    return get_account(aid)


def parse_account_line(line: str) -> dict | None:
    """解析一行账号文本（支持 Tab / 竖线 / 逗号 / 多空格分隔）：
    格式：邮箱  密码  2FA密钥  辅助邮箱
    """
    raw = (line or "").strip()
    if not raw or raw.startswith("#"):
        return None
    if "\t" in raw:
        parts = [p.strip() for p in raw.split("\t")]
    elif "|" in raw:
        parts = [p.strip() for p in raw.split("|")]
    elif "----" in raw:
        parts = [p.strip() for p in raw.split("----")]
    else:
        parts = [p.strip() for p in re.split(r"\s{2,}|,", raw) if p.strip()]
        if len(parts) == 1 and " " in raw:
            toks = raw.split()
            if len(toks) >= 2 and "@" in toks[0]:
                email = toks[0]
                password = toks[1]
                rest = toks[2:]
                backup = ""
                if rest and "@" in rest[-1]:
                    backup = rest[-1]
                    rest = rest[:-1]
                totp = " ".join(rest)
                parts = [email, password, totp, backup]
    if not parts or not parts[0]:
        return None
    email = parts[0]
    password = parts[1] if len(parts) > 1 else ""
    totp = parts[2] if len(parts) > 2 else ""
    backup = parts[3] if len(parts) > 3 else ""
    return {
        "email": email,
        "password": password,
        "totp_secret": totp,
        "backup_email": backup,
    }


def import_tsv_lines(text: str, group_id: str = "", flow_mode: str = "frames",
                     model: str = DEFAULT_MODEL, default_group_id: str = "",
                     default_flow_mode: str = "", default_model: str = "") -> dict:
    gid = group_id or default_group_id or ""
    fmode = default_flow_mode or flow_mode or "frames"
    mdl = default_model or model or DEFAULT_MODEL
    added = 0
    updated = 0
    out_accounts = []
    for line in (text or "").splitlines():
        parsed = parse_account_line(line)
        if not parsed:
            continue
        existed = bool(db.one("SELECT 1 FROM multi_accounts WHERE lower(email)=lower(?)", (parsed["email"],)))
        acc = add_account(
            email=parsed["email"],
            password=parsed["password"],
            totp_secret=parsed["totp_secret"],
            backup_email=parsed["backup_email"],
            group_id=gid,
            flow_mode=fmode,
            model=mdl,
        )
        if existed:
            updated += 1
        else:
            added += 1
        out_accounts.append(acc)
    return {"added": added, "updated": updated, "count": added + updated, "accounts": out_accounts}


def create_account(data: dict | None = None, **kw) -> dict:
    d = dict(data or {})
    d.update(kw)
    email = d.get("email") or d.get("loginAccount") or d.get("login_account") or d.get("name") or ""
    acc = add_account(
        email=email,
        password=d.get("password") or d.get("loginPassword") or "",
        totp_secret=d.get("totp_secret") or d.get("totpSecret") or "",
        backup_email=d.get("backup_email") or d.get("backupEmail") or "",
        group_id=d.get("group_id") or d.get("groupId") or "",
        is_favorite=d.get("is_favorite", False),
        project_url=d.get("project_url") or d.get("loginUrl") or "",
        flow_mode=d.get("flow_mode") or d.get("flowMode") or "frames",
        model=d.get("model") or DEFAULT_MODEL,
        note=d.get("note") or "",
        meta=d.get("meta") if isinstance(d.get("meta"), dict) else None,
        account_id=str(d["id"]).strip() if d.get("id") else None,
    )
    if "flow_credits" in d or "credits" in d:
        cval = d.get("flow_credits") if "flow_credits" in d else d.get("credits")
        if cval is not None:
            acc = update_account(acc["id"], flow_credits=int(cval))
    return acc


def update_account(acc_id: str, fields_dict: dict | None = None, **fields) -> dict:
    if isinstance(fields_dict, dict):
        merged = dict(fields_dict)
        merged.update(fields)
        fields = merged
    if "credits" in fields and "flow_credits" not in fields:
        fields["flow_credits"] = fields["credits"]
    if "twoFactorCode" in fields and "totp_secret" not in fields:
        fields["totp_secret"] = fields["twoFactorCode"]
    if "totpSecret" in fields and "totp_secret" not in fields:
        fields["totp_secret"] = fields["totpSecret"]
    if "loginPassword" in fields and "password" not in fields:
        fields["password"] = fields["loginPassword"]
    r = db.one("SELECT * FROM multi_accounts WHERE id=? OR lower(email)=lower(?)", (acc_id, acc_id))
    if not r:
        raise OranteError(f"找不到多账号：{acc_id}")
    acc_id = r["id"]
    sets, args = [], []
    if "email" in fields and (fields["email"] or "").strip():
        sets.append("email=?")
        args.append(fields["email"].strip())
    if "password" in fields and fields["password"] is not None:
        sets.append("password_enc=?")
        args.append(crypto_vault.encrypt_secret(str(fields["password"])))
    if "totp_secret" in fields and fields["totp_secret"] is not None:
        sets.append("totp_secret_enc=?")
        args.append(crypto_vault.encrypt_secret(str(fields["totp_secret"])))
    if "backup_email" in fields and fields["backup_email"] is not None:
        sets.append("backup_email=?")
        args.append(str(fields["backup_email"]).strip())
    if "group_id" in fields and fields["group_id"] is not None:
        sets.append("group_id=?")
        args.append(str(fields["group_id"]).strip())
    if "is_favorite" in fields and fields["is_favorite"] is not None:
        sets.append("is_favorite=?")
        args.append(1 if fields["is_favorite"] else 0)
    if "project_url" in fields and fields["project_url"] is not None:
        sets.append("project_url=?")
        args.append(str(fields["project_url"]).strip())
    if "flow_mode" in fields and fields["flow_mode"] is not None:
        sets.append("flow_mode=?")
        args.append(normalize_flow_mode(str(fields["flow_mode"]), default="frames"))
    if "model" in fields and fields["model"]:
        sets.append("model=?")
        args.append(str(fields["model"]).strip())
    if "flow_credits" in fields and fields["flow_credits"] is not None:
        c_val = int(fields["flow_credits"])
        sets.append("flow_credits=?")
        args.append(c_val)
        sets.append("credits_updated_at=?")
        args.append(db.now())
        if c_val == 0 and "status" not in fields:
            sets.append("status=?")
            args.append("quota_exhausted")
        elif c_val > 0 and r.get("status") == "quota_exhausted" and "status" not in fields:
            sets.append("status=?")
            args.append("idle")
    if "status" in fields and fields["status"]:
        sets.append("status=?")
        args.append(str(fields["status"]).strip())
    if "note" in fields and fields["note"] is not None:
        sets.append("note=?")
        args.append(str(fields["note"]))
    if "meta" in fields and isinstance(fields["meta"], dict):
        cur_meta = db.unj(r.get("meta"), {}) or {}
        cur_meta.update(fields["meta"])
        sets.append("meta=?")
        args.append(db.j(cur_meta))
    if sets:
        sets.append("updated=?")
        args.append(db.now())
        db.run(f"UPDATE multi_accounts SET {', '.join(sets)} WHERE id=?", (*args, acc_id))
    return get_account(acc_id)


def delete_accounts(ids: list[str]) -> dict:
    clean_ids = [str(x).strip() for x in (ids or []) if str(x).strip()]
    for aid in clean_ids:
        db.run("DELETE FROM multi_accounts WHERE id=? OR lower(email)=lower(?)", (aid, aid))
    return {"ok": True, "deleted": len(clean_ids)}


def delete_account(aid: str, delete_profile: bool = False) -> dict:
    if delete_profile:
        try:
            acc = get_account(aid)
            pdir = profile_dir_for(acc)
            if pdir.is_dir():
                shutil.rmtree(pdir, ignore_errors=True)
        except Exception:
            pass
    return delete_accounts([aid])


def save_group(data: dict | None = None, **kw) -> dict:
    d = dict(data or {})
    d.update(kw)
    gid = str(d.get("id") or "").strip()
    name = str(d.get("name") or gid or "默认分组").strip()
    color = str(d.get("color") or "#0969da").strip()
    existing = db.one("SELECT * FROM multi_account_groups WHERE id=?", (gid,)) if gid else None
    if existing:
        grp = update_group(gid, name=name, color=color, sort_order=d.get("sort_order"))
    else:
        grp = create_group(name=name, color=color, group_id=gid or None)
    acc_ids = d.get("account_ids") or d.get("accountIds")
    if isinstance(acc_ids, list):
        for aid in acc_ids:
            try:
                update_account(str(aid), group_id=grp["id"])
            except Exception:
                pass
    return grp


def batch_set_group(
    ids: list[str],
    group_id: str | None = None,
    is_favorite: bool | int | None = None,
    flow_mode: str | None = None,
    model: str | None = None,
) -> dict:
    clean_ids = [str(x).strip() for x in (ids or []) if str(x).strip()]
    updated = 0
    for aid in clean_ids:
        kw = {}
        if group_id is not None:
            kw["group_id"] = group_id
        if is_favorite is not None:
            kw["is_favorite"] = is_favorite
        if flow_mode is not None:
            kw["flow_mode"] = flow_mode
        if model is not None:
            kw["model"] = model
        if kw:
            try:
                update_account(aid, **kw)
                updated += 1
            except Exception:
                pass
    return {"ok": True, "updated": updated}


def batch_update_accounts(account_ids: list[str], patch: dict) -> int:
    res = batch_set_group(
        account_ids,
        group_id=patch.get("group_id"),
        is_favorite=patch.get("is_favorite"),
        flow_mode=patch.get("flow_mode"),
        model=patch.get("model"),
    )
    return int(res.get("updated") or 0)


def get_totp(acc_id: str, ts: float | None = None) -> dict:
    r = db.one("SELECT id, email, totp_secret_enc FROM multi_accounts WHERE id=? OR lower(email)=lower(?)",
               (acc_id, acc_id))
    if not r:
        raise OranteError(f"找不到多账号：{acc_id}")
    secret = crypto_vault.decrypt_secret(r.get("totp_secret_enc") or "")
    res = crypto_vault.generate_totp(secret, ts=ts)
    return {
        "account_id": r["id"],
        "email": r["email"],
        "code": res["code"],
        "remaining_seconds": res["remaining_seconds"],
    }


get_account_totp = get_totp


def get_secret(acc_id: str, field: str = "password") -> dict:
    r = db.one("SELECT * FROM multi_accounts WHERE id=? OR lower(email)=lower(?)", (acc_id, acc_id))
    if not r:
        raise OranteError(f"找不到多账号：{acc_id}")
    if field == "totp_secret":
        val = crypto_vault.decrypt_secret(r.get("totp_secret_enc") or "")
    elif field == "backup_email":
        val = r.get("backup_email") or ""
    else:
        val = crypto_vault.decrypt_secret(r.get("password_enc") or "")
    return {"account_id": r["id"], "email": r["email"], "field": field, "value": val}


def reveal_account_secrets(acc_id: str) -> dict:
    r = db.one("SELECT * FROM multi_accounts WHERE id=? OR lower(email)=lower(?)", (acc_id, acc_id))
    if not r:
        raise OranteError(f"找不到多账号：{acc_id}")
    pw = crypto_vault.decrypt_secret(r.get("password_enc") or "")
    totp = crypto_vault.decrypt_secret(r.get("totp_secret_enc") or "")
    return {
        "id": r["id"],
        "account_id": r["id"],
        "email": r["email"],
        "password": pw,
        "loginPassword": pw,
        "totp_secret": totp,
        "totpSecret": totp,
        "backup_email": r.get("backup_email") or "",
    }


# ---------------------------------------------------------------------------
# Hedra 插件配置 (hedra-config-backup-*.json) 100% 双向兼容导入导出
# ---------------------------------------------------------------------------

def import_hedra_config(payload: dict, overwrite: bool = True) -> dict:
    """导入 hedra-config-backup-*.json（100% 兼容原有分组、颜色、收藏、账号四列 TSV 与附加设置）。"""
    if not isinstance(payload, dict):
        raise OranteError("配置文件格式不正确（需要 JSON 对象）")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    ls = data.get("localStorage") if isinstance(data.get("localStorage"), dict) else {}

    # 1. 解析 __GROUP_TAGS_DETAIL__
    raw_detail = ls.get("__GROUP_TAGS_DETAIL__")
    if isinstance(raw_detail, str):
        try:
            groups_detail = json.loads(raw_detail)
        except Exception:
            groups_detail = []
    elif isinstance(raw_detail, list):
        groups_detail = raw_detail
    else:
        groups_detail = []

    imported_groups = 0
    for g in groups_detail:
        if not isinstance(g, dict) or not g.get("name"):
            continue
        gid = str(g.get("id") or "").strip()
        name = str(g.get("name") or "").strip()
        color = str(g.get("color") or "#0969da").strip()
        created_at = int(g.get("createdAt") or int(time.time() * 1000))
        existing_g = db.one("SELECT * FROM multi_account_groups WHERE id=? OR name=?", (gid, name))
        if existing_g:
            db.run("UPDATE multi_account_groups SET name=?, color=? WHERE id=?",
                   (name, color, existing_g["id"]))
        else:
            create_group(name=name, color=color, group_id=gid, created_at=created_at)
            imported_groups += 1

    # 2. 解析 __GROUP_TAGS__ 与 accountMeta
    raw_tags = ls.get("__GROUP_TAGS__")
    if isinstance(raw_tags, str):
        try:
            group_tags = json.loads(raw_tags)
        except Exception:
            group_tags = {}
    elif isinstance(raw_tags, dict):
        group_tags = raw_tags
    else:
        group_tags = {}

    acc_meta = data.get("accountMeta") if isinstance(data.get("accountMeta"), dict) else {}

    # 3. 解析 accounts 列表
    raw_accounts = data.get("accounts") or []
    imported_accounts = 0
    for item in raw_accounts:
        if isinstance(item, str):
            parsed = parse_account_line(item)
            if not parsed:
                continue
            email = parsed["email"]
            meta_row = acc_meta.get(email) or acc_meta.get(email.lower()) or {}
            gid = str(meta_row.get("group") or group_tags.get(email) or "").strip()
            fav = bool(meta_row.get("favorite", False))
            add_account(
                email=email,
                password=parsed["password"],
                totp_secret=parsed["totp_secret"],
                backup_email=parsed["backup_email"],
                group_id=gid,
                is_favorite=fav,
            )
            imported_accounts += 1
        elif isinstance(item, dict) and (item.get("email") or item.get("loginAccount")):
            email = str(item.get("email") or item.get("loginAccount")).strip()
            meta_row = acc_meta.get(email) or {}
            gid = str(item.get("group_id") or item.get("groupId") or meta_row.get("group") or group_tags.get(email) or "").strip()
            fav = bool(item.get("is_favorite", meta_row.get("favorite", False)))
            add_account(
                email=email,
                password=str(item.get("password") or item.get("loginPassword") or ""),
                totp_secret=str(item.get("totp_secret") or item.get("totpSecret") or ""),
                backup_email=str(item.get("backup_email") or item.get("backupEmail") or ""),
                group_id=gid,
                is_favorite=fav,
                project_url=str(item.get("project_url") or item.get("loginUrl") or ""),
                flow_mode=str(item.get("flow_mode") or item.get("flowMode") or "frames"),
                model=str(item.get("model") or DEFAULT_MODEL),
                account_id=str(item["id"]).strip() if item.get("id") else None,
            )
            imported_accounts += 1

    # 3b. 支持顶层 multiAccountGroups 列表格式
    extra_groups = data.get("multiAccountGroups") or payload.get("multiAccountGroups") or []
    for g in extra_groups:
        if isinstance(g, dict) and (g.get("name") or g.get("id")):
            save_group(g)
            imported_groups += 1

    # 4. 保存 settings / bookmarks 以备无损导出
    extra_preserve = {
        "settings": data.get("settings") if isinstance(data.get("settings"), dict) else {},
        "bookmarks": data.get("bookmarks") if isinstance(data.get("bookmarks"), list) else [],
        "localStorage_extra": {
            k: v for k, v in ls.items()
            if k not in ("__GROUP_TAGS__", "__GROUP_TAGS_DETAIL__")
        },
    }
    db.run(
        "INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)",
        ("multi_accounts.hedra_extra", db.j(extra_preserve)),
    )
    return {
        "ok": True,
        "imported_accounts": imported_accounts,
        "imported_groups": imported_groups,
        "groups": list_groups(),
        "accounts": list_accounts(),
    }


def export_hedra_config() -> dict:
    """导出与 hedra-config-backup-*.json 100% 兼容的 JSON 配置结构。"""
    groups = db.rows("SELECT * FROM multi_account_groups ORDER BY sort_order, created_at, id")
    accs = list_accounts(include_secrets=True, probe=False)

    accounts_lines: list[str] = []
    account_meta: dict[str, dict] = {}
    group_tags: dict[str, str] = {}

    for a in accs:
        email = a["email"]
        pw = a.get("password") or ""
        totp = a.get("totp_secret") or ""
        backup = a.get("backup_email") or ""
        accounts_lines.append(f"{email}\t{pw}\t{totp}\t{backup}")
        gid = a.get("group_id") or ""
        fav = bool(a.get("is_favorite"))
        if gid or fav:
            m_entry: dict = {}
            if gid:
                m_entry["group"] = gid
                group_tags[email] = gid
            m_entry["favorite"] = fav
            account_meta[email] = m_entry

    groups_detail = [
        {
            "id": g["id"],
            "name": g["name"],
            "color": g.get("color") or "#0969da",
            "createdAt": int(g.get("created_at") or int(time.time() * 1000)),
        }
        for g in groups
    ]

    saved_extra_row = db.one("SELECT value FROM settings WHERE key='multi_accounts.hedra_extra'")
    saved_extra = db.unj((saved_extra_row or {}).get("value"), {}) or {}
    ls_out = dict(saved_extra.get("localStorage_extra") or {})
    ls_out["__GROUP_TAGS__"] = json.dumps(group_tags, ensure_ascii=False)
    ls_out["__GROUP_TAGS_DETAIL__"] = json.dumps(groups_detail, ensure_ascii=False)

    default_settings = {
        " fillDelay": 500,
        "fillDelay": 500,
        "autoFill": True,
        "loopMode": True,
        "currentIndex": 0,
    }
    settings_out = saved_extra.get("settings") or default_settings
    bookmarks_out = saved_extra.get("bookmarks") or []

    return {
        "version": "1.0",
        "exportTime": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": {
            "accounts": accounts_lines,
            "accountMeta": account_meta,
            "settings": settings_out,
            "localStorage": ls_out,
            "bookmarks": bookmarks_out,
        },
    }


# ---------------------------------------------------------------------------
# 浏览器 Profile 智能清理缓存（保留 Cookie 与登录态）
# ---------------------------------------------------------------------------

def _dir_size(path: Path) -> int:
    total = 0
    try:
        if path.is_file():
            return path.stat().st_size
        if path.is_dir():
            for root, _dirs, files in os.walk(path):
                for f in files:
                    fp = Path(root) / f
                    try:
                        total += fp.stat().st_size
                    except OSError:
                        pass
    except OSError:
        pass
    return total


def _clean_single_profile_dir(prof_dir: Path, deep: bool = False) -> int:
    if not prof_dir.is_dir():
        return 0
    freed = 0
    target_subdirs = list(SAFE_CACHE_SUBDIRS) + (list(DEEP_CACHE_SUBDIRS) if deep else [])
    # Profile 根目录和 Default/ 子目录都检查缓存目录
    bases = [prof_dir, prof_dir / "Default"]
    for base in bases:
        if not base.is_dir():
            continue
        for rel in target_subdirs:
            cand = base / rel
            if not cand.exists():
                continue
            # 安全保护：绝不误删包含登录态的名字
            if cand.name.lower() in PRESERVED_LOGIN_ITEMS:
                continue
            sz = _dir_size(cand)
            try:
                if cand.is_dir():
                    shutil.rmtree(cand, ignore_errors=True)
                else:
                    cand.unlink(missing_ok=True)
                freed += sz
            except OSError:
                pass
    return freed


def clean_profile_caches(account_ids: list[str] | Path | str | None = None, deep: bool = False) -> dict:
    """清理指定或全部账号的 Chrome 缓存目录，严格保留 Cookies / Local Storage / IndexedDB / Login Data。"""
    paths.reset()
    dirs_to_clean: list[tuple[str, Path]] = []
    if isinstance(account_ids, (Path, str)):
        p = Path(account_ids)
        if p.is_dir():
            dirs_to_clean.append((p.name, p))
            account_ids = None
        else:
            account_ids = [str(account_ids)]
    if account_ids:
        for aid in account_ids:
            r = db.one("SELECT * FROM multi_accounts WHERE id=? OR lower(email)=lower(?)", (aid, aid))
            if r:
                dirs_to_clean.append((r["email"], profile_dir_for(r)))
            else:
                a = db.one("SELECT * FROM accounts WHERE id=?", (aid,))
                if a:
                    from . import accounts as accmod
                    dirs_to_clean.append((a["name"], accmod._profile_dir(a)))
    elif not dirs_to_clean:
        for r in db.rows("SELECT * FROM multi_accounts"):
            dirs_to_clean.append((r["email"], profile_dir_for(r)))
        mroot = multi_profiles_root()
        seen_paths = {p.resolve() for _, p in dirs_to_clean if p.exists()}
        if mroot.is_dir():
            for child in mroot.iterdir():
                if child.is_dir() and child.resolve() not in seen_paths:
                    dirs_to_clean.append((child.name, child))
        if paths.CHROME.is_dir():
            for child in paths.CHROME.iterdir():
                if child.is_dir() and child.resolve() not in seen_paths:
                    dirs_to_clean.append((child.name, child))

    cleaned_count = 0
    total_freed = 0
    details = []
    for label, pdir in dirs_to_clean:
        if not pdir.is_dir():
            continue
        freed = _clean_single_profile_dir(pdir, deep=deep)
        cleaned_count += 1
        total_freed += freed
        details.append({
            "account": label,
            "profile_dir": str(pdir),
            "freed_bytes": freed,
            "freed_mb": round(freed / (1024 * 1024), 2),
        })
    return {
        "ok": True,
        "cleaned_accounts": cleaned_count,
        "freed_bytes": total_freed,
        "freed_mb": round(total_freed / (1024 * 1024), 2),
        "details": details,
    }


clean_all_profile_caches = clean_profile_caches


# ---------------------------------------------------------------------------
# Google + Flow 自动登录、积分探测与最多 3 个浏览器并发管控
# ---------------------------------------------------------------------------

def running_multi_accounts() -> list[dict]:
    """返回当前端口存活的多账号列表。"""
    alive_list = []
    for r in db.rows("SELECT * FROM multi_accounts WHERE port IS NOT NULL"):
        port = int(r.get("port") or 0)
        if port and chrome.port_alive(port):
            alive_list.append(_decorate_account(r, probe=False))
    return alive_list


def _close_chrome_on_port(port: int) -> bool:
    if not port or not chrome.port_alive(port):
        return False
    try:
        with chrome.Browser(port) as br:
            try:
                cdp = br.ctx.new_cdp_session(br.ctx.pages[0] if br.ctx.pages else br.ctx.new_page())
                cdp.send("Browser.close")
            except Exception:
                br.browser.close()
    except Exception:
        pass
    for _ in range(15):
        if not chrome.port_alive(port):
            return True
        time.sleep(0.2)
    return not chrome.port_alive(port)


def close_multi_account_flow(ids: list[str] | str | None = None, auto_clean: bool = True,
                             clean_cache: bool | None = None) -> dict:
    """关闭指定或全部多账号的 Chrome 实例，并在关闭后自动清理缓存目录释放空间。"""
    if clean_cache is not None:
        auto_clean = bool(clean_cache)
    if isinstance(ids, str):
        ids = [ids]
    if ids:
        rows = [
            db.one("SELECT * FROM multi_accounts WHERE id=? OR lower(email)=lower(?)", (x, x))
            for x in ids
        ]
        rows = [r for r in rows if r]
    else:
        rows = db.rows("SELECT * FROM multi_accounts WHERE port IS NOT NULL")
    closed_ids = []
    for r in rows:
        port = int(r.get("port") or 0)
        if port and chrome.port_alive(port):
            _close_chrome_on_port(port)
            closed_ids.append(r["id"])
        st = "quota_exhausted" if int(r.get("flow_credits") if r.get("flow_credits") is not None else -1) == 0 else "idle"
        db.run("UPDATE multi_accounts SET status=?, updated=? WHERE id=?", (st, db.now(), r["id"]))
    clean_res = clean_profile_caches([r["id"] for r in rows]) if (auto_clean and rows) else {"freed_mb": 0.0}
    return {
        "ok": True,
        "closed_ids": closed_ids,
        "closed_count": len(closed_ids),
        "freed_mb": clean_res.get("freed_mb", 0.0),
    }


def ensure_google_and_flow_logged_in(page, acc: dict) -> dict:
    """在 Playwright 页面上自动检测并完成 Google 登录（含密码 + 2FA TOTP）与 Flow 引导页进入。"""
    email = (acc.get("email") or "").strip()
    password = (acc.get("password") or "").strip()
    totp_secret = (acc.get("totp_secret") or "").strip()
    backup_email = (acc.get("backup_email") or "").strip()
    result = {"logged_in": False, "url": "", "credits": None, "project_url": ""}

    try:
        for _step in range(8):
            url = page.url or ""
            result["url"] = url
            # 1. Google 登录页：邮箱输入
            if "accounts.google.com" in url:
                email_in = page.locator('input[type="email"], input#identifierId')
                if email_in.count() > 0 and email_in.first.is_visible():
                    email_in.first.fill(email)
                    page.keyboard.press("Enter")
                    time.sleep(2.5)
                    continue
                # 2. Google 登录页：密码输入
                pw_in = page.locator('input[type="password"], input[name="Passwd"]')
                if pw_in.count() > 0 and pw_in.first.is_visible() and password:
                    pw_in.first.fill(password)
                    page.keyboard.press("Enter")
                    time.sleep(3.0)
                    continue
                # 3. Google 登录页：2FA TOTP 验证码输入
                totp_in = page.locator('input[name="totpPin"], input#totpPin, input[type="tel"][autocomplete="one-time-code"]')
                if totp_in.count() > 0 and totp_in.first.is_visible() and totp_secret:
                    code_info = crypto_vault.generate_totp(totp_secret)
                    if code_info.get("code"):
                        totp_in.first.fill(code_info["code"])
                        page.keyboard.press("Enter")
                        time.sleep(3.0)
                        continue
                # 4. 辅助邮箱确认
                rec_in = page.locator('input[name="knowledgePreregisteredEmailResponse"], input#knowledge-preregistered-email-response')
                if rec_in.count() > 0 and rec_in.first.is_visible() and backup_email:
                    rec_in.first.fill(backup_email)
                    page.keyboard.press("Enter")
                    time.sleep(3.0)
                    continue

            # 5. Flow 欢迎页 / 引导页：点击 Create with Flow / 开始使用 / New project
            if "labs.google" in url or "flow.google.com" in url:
                w = {}
                try:
                    w = page.evaluate("() => { const g = window.WIZ_global_data||{}; return {SNlM0e: g.SNlM0e}; }") or {}
                except Exception:
                    pass
                if w.get("SNlM0e"):
                    result["logged_in"] = True
                    if "/project/" in url:
                        result["project_url"] = url
                    credits = read_credits(page)
                    result["credits"] = credits
                    break
                # 尝试点击引导按钮
                for btn_txt in ("Create with Flow", "New project", "开始使用", "新建项目", "Sign in", "登录"):
                    try:
                        loc = page.get_by_role("button", name=re.compile(re.escape(btn_txt), re.I))
                        if loc.count() > 0 and loc.first.is_visible():
                            loc.first.click(timeout=5000)
                            time.sleep(2.5)
                            break
                    except Exception:
                        pass
            time.sleep(1.5)
    except Exception as e:
        result["error"] = str(e)[:200]
    return result


def open_multi_account_flow(acc_id: str, auto_login: bool = True,
                            enforce_cap: bool = True, probe_credits: bool = True) -> dict:
    """打开单个多账号的 Chrome 并进入 Flow（受最多 3 个并发浏览器上限保护）。"""
    acc = get_account(acc_id, include_secrets=True, probe=True)
    port = int(acc.get("port") or 0)
    if not port:
        port = _allocate_port()
        db.run("UPDATE multi_accounts SET port=? WHERE id=?", (port, acc["id"]))
        acc["port"] = port

    if enforce_cap and not chrome.port_alive(port):
        alive_now = [a for a in running_multi_accounts() if a["id"] != acc["id"]]
        if len(alive_now) >= MAX_CONCURRENT_BROWSERS:
            # 优先自动关掉已用尽额度或最早打开的空闲实例
            exhausted = [a for a in alive_now if a.get("flow_credits") == 0]
            victim = exhausted[0] if exhausted else alive_now[0]
            close_multi_account_flow([victim["id"]], auto_clean=True)

    pdir = profile_dir_for(acc)
    pdir.mkdir(parents=True, exist_ok=True)
    target_url = (acc.get("project_url") or "").strip() or DEFAULT_FLOW_URL
    launched = chrome.launch(port, pdir, target_url, tab_if_alive=True)
    db.run("UPDATE multi_accounts SET status='running', updated=? WHERE id=?", (db.now(), acc["id"]))

    login_info = {"logged_in": False, "credits": None}
    if auto_login and chrome.port_alive(port):
        try:
            with chrome.Browser(port) as br:
                pg = None
                for p in br.ctx.pages:
                    u = p.url or ""
                    if "flow.google" in u or "labs.google" in u or "accounts.google" in u:
                        pg = p
                        break
                if pg is None:
                    pg = br.ctx.new_page()
                    pg.goto(target_url, wait_until="domcontentloaded", timeout=45000)
                login_info = ensure_google_and_flow_logged_in(pg, acc)
                if login_info.get("project_url"):
                    update_account(acc["id"], project_url=login_info["project_url"])
                if login_info.get("credits") is not None:
                    update_account(acc["id"], flow_credits=int(login_info["credits"]))
        except Exception as e:
            login_info["error"] = str(e)[:200]

    return {
        "ok": True,
        "launched": launched,
        "account": get_account(acc["id"], probe=True),
        "login": login_info,
    }


def open_batch(ids: list[str] | None = None, auto_login: bool = True,
               account_ids: list[str] | None = None, probe_credits: bool = True,
               max_concurrent: int = MAX_CONCURRENT_BROWSERS) -> dict:
    """批量打开多账号（最多同时打开 MAX_CONCURRENT_BROWSERS = 3 个）。"""
    target_ids = ids if ids is not None else (account_ids or [])
    cap = min(int(max_concurrent or MAX_CONCURRENT_BROWSERS), MAX_CONCURRENT_BROWSERS)
    clean_ids = [str(x).strip() for x in target_ids if str(x).strip()][:cap]
    results = []
    for aid in clean_ids:
        try:
            results.append(open_multi_account_flow(aid, auto_login=auto_login, enforce_cap=True, probe_credits=probe_credits))
        except Exception as e:
            results.append({"ok": False, "id": aid, "error": str(e)})
    return {
        "ok": True,
        "max_concurrent_browsers": MAX_CONCURRENT_BROWSERS,
        "opened": len([r for r in results if r.get("ok")]),
        "results": results,
    }


def probe_credits_batch(ids: list[str] | None = None, group_id: str | None = None,
                        account_ids: list[str] | None = None, auto_open: bool = False) -> dict:
    """批量检测账号剩余 Flow 积分（如浏览器已开则直接读，未开则不强行弹窗或按需静默探测）。"""
    if ids is None and account_ids is not None:
        ids = account_ids
    if ids:
        target_rows = [
            db.one("SELECT * FROM multi_accounts WHERE id=? OR lower(email)=lower(?)", (x, x))
            for x in ids
        ]
        target_rows = [r for r in target_rows if r]
    elif group_id:
        target_rows = db.rows("SELECT * FROM multi_accounts WHERE group_id=? ORDER BY sort_order, id", (group_id,))
    else:
        target_rows = db.rows("SELECT * FROM multi_accounts ORDER BY sort_order, id")

    probed = []
    for r in target_rows:
        port = int(r.get("port") or 0)
        credits = None
        if port and chrome.port_alive(port):
            try:
                with chrome.Browser(port) as br:
                    for p in br.ctx.pages:
                        if "flow.google" in (p.url or "") or "labs.google" in (p.url or ""):
                            credits = read_credits(p)
                            if credits is not None:
                                break
            except Exception:
                credits = None
        if credits is not None:
            upd = update_account(r["id"], flow_credits=int(credits))
        else:
            upd = get_account(r["id"], probe=True)
        probed.append(upd)
    return {
        "ok": True,
        "probed_count": len(probed),
        "accounts": probed,
        "groups": list_groups(),
    }


# ---------------------------------------------------------------------------
# MultiAccountGroupRunner：账号组最多 3 浏览器并发轮转 + 0 积分自动关浏览器清缓存换号
# ---------------------------------------------------------------------------

def resolve_group(group_spec: str) -> dict | None:
    """把 `group:<id_or_name>` / `GRP:<id>` / 组名 / 组 ID 解析成分组记录。"""
    s = (group_spec or "").strip()
    if not s:
        return None
    for prefix in ("group:", "GRP:", "grp_name:"):
        if s.lower().startswith(prefix.lower()):
            s = s[len(prefix):].strip()
            break
    return db.one("SELECT * FROM multi_account_groups WHERE id=? OR name=?", (s, s))


def to_orante_engine_account(macc: dict) -> dict:
    """把 multi_accounts 行转换成 FlowEngine / tasks / generate 可直接消费的 account 字典。"""
    pdir = profile_dir_for(macc)
    flow_mode = normalize_flow_mode(macc.get("flow_mode"), default="frames")
    return {
        "id": macc["id"],
        "name": macc["email"],
        "engine": "flow",
        "port": int(macc.get("port") or _allocate_port()),
        "profile": str(pdir),
        "project_url": macc.get("project_url") or f"{FLOW_HOME.rstrip('/')}/project/00000000-0000-0000-0000-000000000000",
        "model": macc.get("model") or DEFAULT_MODEL,
        "aspect": "9:16",
        "max_concurrent": 4,
        "enabled": 1,
        "flow_mode": flow_mode,
        "extra": {
            "flow_mode": flow_mode,
            "multi_account_id": macc["id"],
            "group_id": macc.get("group_id") or "",
            "flow_credits": macc.get("flow_credits", -1),
        },
        "login_state": "",
        "multi_account": True,
    }


def resolve_as_orante_account(spec: str) -> dict | None:
    """供 accounts.get() 和 tasks._acc_by_name() 调用：支持 M0001、邮箱、或 group:<组名>。"""
    s = (spec or "").strip()
    if not s:
        return None
    grp = resolve_group(s)
    if grp:
        runner = MultiAccountGroupRunner(grp["id"])
        picked = runner.select_next_account(raise_on_exhausted=False)
        if picked:
            return to_orante_engine_account(picked)
        # 即使组内暂时全部用尽，也返回一个带组标识的虚拟账号字典，真正跑生成时触发告警
        return {
            "id": f"group:{grp['id']}",
            "name": f"账号组:{grp['name']}",
            "engine": "flow",
            "port": None,
            "profile": "",
            "project_url": "",
            "model": DEFAULT_MODEL,
            "aspect": "9:16",
            "max_concurrent": 3,
            "enabled": 1,
            "flow_mode": "frames",
            "extra": {"group_id": grp["id"], "group_name": grp["name"]},
            "login_state": "",
            "is_group": True,
        }
    r = db.one("SELECT * FROM multi_accounts WHERE id=? OR lower(email)=lower(?)", (s, s))
    if r:
        return to_orante_engine_account(_decorate_account(r))
    return None


class MultiAccountGroupRunner:
    """账号组并发调度器：
    - 组内最多同时开启 MAX_CONCURRENT_BROWSERS (3) 个浏览器实例
    - 自动跳过 flow_credits == 0（已耗尽）的账号
    - 某个账号积分归零或报额度耗尽时：立即更新数据库为 0 积分、关闭该账号 Chrome、
      自动清理该账号缓存目录（保留 Cookie 与登录态），并无缝切换到组内下一个有余量的账号
    - 当组内所有账号均已耗尽时：发射 `group.quota_exhausted` SSE 事件并抛出明确提示
    """

    def __init__(self, group_id_or_name: str, max_browsers: int = MAX_CONCURRENT_BROWSERS,
                 max_concurrent: int | None = None):
        grp = resolve_group(group_id_or_name)
        if not grp:
            raise OranteError(f"找不到账号组：{group_id_or_name}")
        self.group = grp
        self.group_id = grp["id"]
        self.group_name = grp["name"]
        cap = max_concurrent if max_concurrent is not None else max_browsers
        self.max_browsers = min(int(cap or MAX_CONCURRENT_BROWSERS), MAX_CONCURRENT_BROWSERS)
        self._exhausted_ids: set[str] = set()
        self.active_ids: list[str] = []
        self._rr_idx: int = 0

    def candidate_accounts(self) -> list[dict]:
        rows = db.rows(
            "SELECT * FROM multi_accounts WHERE group_id=? ORDER BY is_favorite DESC, sort_order ASC, id ASC",
            (self.group_id,),
        )
        out = []
        for r in rows:
            acc = _decorate_account(r, probe=False)
            if acc["id"] in self._exhausted_ids:
                continue
            if acc["flow_credits"] == 0 or acc.get("status") == "quota_exhausted":
                continue
            out.append(acc)
        return out

    def _fill_active_window(self) -> None:
        cands = self.candidate_accounts()
        cand_map = {a["id"]: a for a in cands}
        self.active_ids = [aid for aid in self.active_ids if aid in cand_map]
        for a in cands:
            if len(self.active_ids) >= self.max_browsers:
                break
            if a["id"] not in self.active_ids:
                try:
                    open_multi_account_flow(a["id"], auto_login=True, probe_credits=True)
                except Exception:
                    pass
                self.active_ids.append(a["id"])

    def acquire_account(self) -> dict | None:
        self._fill_active_window()
        if not self.active_ids:
            return self.select_next_account(raise_on_exhausted=False)
        aid = self.active_ids[self._rr_idx % len(self.active_ids)]
        self._rr_idx += 1
        return get_account(aid)

    def report_credits(self, acc_id: str, credits: int) -> dict | None:
        update_account(acc_id, flow_credits=int(credits))
        if int(credits) <= 0:
            self._exhausted_ids.add(acc_id)
            if acc_id in self.active_ids:
                self.active_ids.remove(acc_id)
            try:
                close_multi_account_flow(acc_id, clean_cache=True)
            except Exception:
                pass
            events.emit(
                "multi_account.rotated",
                group_id=self.group_id,
                group_name=self.group_name,
                exhausted_account_id=acc_id,
                reason="quota_exhausted",
            )
            self._fill_active_window()
            if not self.active_ids:
                return self.select_next_account(raise_on_exhausted=False)
            return get_account(self.active_ids[0])
        return get_account(acc_id)

    def select_next_account(self, exclude_ids: set[str] | None = None,
                             raise_on_exhausted: bool = True) -> dict | None:
        excl = set(exclude_ids or ()) | self._exhausted_ids
        cands = [a for a in self.candidate_accounts() if a["id"] not in excl]
        if not cands:
            msg = f"账号组「{self.group_name}」内的全部账号今日额度已用完！"
            events.emit(
                "group.quota_exhausted",
                group_id=self.group_id,
                group_name=self.group_name,
                message=msg,
            )
            if raise_on_exhausted:
                raise OranteError(msg, "请补充账号积分或更换其他账号组。")
            return None
        return cands[0]

    def mark_account_exhausted(self, acc_id: str, reason: str = "quota_exhausted") -> dict | None:
        """将当前耗尽积分的账号标为 0 积分 → 关闭其浏览器 → 清理缓存 → 切换到组内下一个账号。"""
        self._exhausted_ids.add(acc_id)
        try:
            update_account(acc_id, flow_credits=0, status="quota_exhausted")
        except Exception:
            pass
        try:
            close_multi_account_flow([acc_id], auto_clean=True)
        except Exception:
            pass
        events.emit(
            "multi_account.rotated",
            group_id=self.group_id,
            group_name=self.group_name,
            exhausted_account_id=acc_id,
            reason=reason,
        )
        return self.select_next_account(exclude_ids=self._exhausted_ids, raise_on_exhausted=True)
