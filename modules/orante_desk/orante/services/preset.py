# -*- coding: utf-8 -*-
"""预设：把「我的一整套配置」导出 / 导入。整包 AES-256-GCM 加密。

头部明文（不含 key / cookie / 账号名），后面一段密文。
登录态失败只标「需重登」，不要让整次导入失败。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
from datetime import datetime
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .. import __version__, accounts, config, db, paths
from ..engines import chrome
from ..errors import OranteError
from . import aikeys, chrome_cookies, desc_cards as cardsmod, prompts as promptsmod

MAGIC = b"ORANTE-PRESET\n"
FORMAT = 1
KDF_ITER = 200_000
SALT_LEN = 16
NONCE_LEN = 12
KEY_LEN = 32
BAD_PASSWORD = "密码不对，或者这个文件不是这台机器能认的预设"

GROUPS = ("settings", "prompts", "accounts", "ai_providers", "counters", "desc_cards")
# cookies 单独一面旗，不跟 groups 混（界面也是单独一行）

PROMPT_KEYS = (
    "prompts.desc", "prompts.camera", "prompts.tail", "prompts.problem",
    "prompts.expand", "prompts.refill_clause", "prompts.format_version",
    "prompts.emotion_preset",
)


def _b64(b: bytes) -> str:
    import base64
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    import base64
    return base64.b64decode(s)


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _machine() -> str:
    try:
        return socket.gethostname() or "orante"
    except Exception:
        return "orante"


def _safe_name(s: str) -> str:
    bad = '\\/:*?"<>|\r\n\t'
    out = "".join("_" if c in bad else c for c in (s or "").strip())
    return (out or "orante")[:40]


def default_export_path() -> Path:
    root = Path(config.get("workspace.root") or paths.DEFAULT_WORKSPACE)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        root = paths.DEFAULT_WORKSPACE
        root.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    return root / f"Orante-预设-{_safe_name(_machine())}-{day}.orpreset"


def _settings_rows() -> dict:
    """settings 表全部行。prompts.* 单独成组，这里去掉。"""
    out = {}
    for r in db.rows("SELECT key,value FROM settings"):
        k = r["key"]
        if k.startswith("prompts."):
            continue
        out[k] = db.unj(r["value"])
    return out


# prompts.* 里这几个是程序自己的状态，不是提示词内容：
# 世代号、极简模式前的开关快照、语种包的出厂备份。
# 它们**不能进套装**，也不能被套装覆盖或清掉——否则换一次套装，
# 「还原成改之前」的备份就没了，提示词世代还会倒回去。
PROMPT_INTERNAL_KEYS = ("prompts.rev", "prompts.switches_before_minimal",
                        "prompts.lang_backup")


def _prompt_rows() -> dict:
    out = {}
    for r in db.rows("SELECT key,value FROM settings WHERE key LIKE 'prompts.%'"):
        if r["key"] in PROMPT_INTERNAL_KEYS:
            continue
        out[r["key"]] = db.unj(r["value"])
    return out


def _account_rows() -> list[dict]:
    rows = []
    for a in accounts.list_all():
        extra = a.get("extra") if isinstance(a.get("extra"), dict) else db.unj(a.get("extra"), {})
        rows.append({
            "id": a["id"], "name": a["name"], "engine": a["engine"],
            "port": a.get("port"), "profile": a.get("profile") or a["id"],
            "project_url": a.get("project_url") or "",
            "model": a.get("model") or "",
            "aspect": a.get("aspect") or "9:16",
            "max_concurrent": a.get("max_concurrent") or 5,
            "enabled": a.get("enabled", 1),
            "extra": extra or {},
            "created": a.get("created") or db.now(),
        })
    return rows


def _provider_rows() -> list[dict]:
    # 完整 key，不打码。这是进密文的。
    return [dict(p) for p in aikeys.list_all()]


def _counter_rows() -> dict:
    return {r["name"]: int(r["value"] or 0) for r in db.rows("SELECT name,value FROM counters")}


def preview() -> dict:
    """当前配置各组条数，给导出对话框用。"""
    accs = _account_rows()
    n_ck = 0
    for a in accs:
        if chrome_cookies.cookies_path(_profile(a)):
            n_ck += 1
    pr = _prompt_rows()
    return {
        "counts": {
            "settings": len(_settings_rows()),
            "prompts": len(pr) or len(promptsmod.EDITABLE),
            "accounts": len(accs),
            "ai_providers": len(_provider_rows()),
            "counters": len(_counter_rows()),
            "desc_cards": len(cardsmod.list_all()),
            "cookies": n_ck,
        },
        "default_path": str(default_export_path()),
        "machine": _machine(),
    }


def _profile(acc: dict) -> Path:
    return accounts.profile_dir(acc)


def collect(groups: list[str], with_cookies: bool) -> tuple[dict, list[str]]:
    """收集要进密文的数据。warnings 是人话。"""
    want = set(groups or GROUPS)
    payload: dict = {"groups": sorted(want)}
    warnings: list[str] = []
    if "settings" in want:
        payload["settings"] = _settings_rows()
    if "prompts" in want:
        payload["prompts"] = _prompt_rows()
    if "accounts" in want:
        payload["accounts"] = _account_rows()
    if "ai_providers" in want:
        payload["ai_providers"] = _provider_rows()
    if "counters" in want:
        payload["counters"] = _counter_rows()
    if "desc_cards" in want:
        payload["desc_cards"] = cardsmod.dump_rows()
    if with_cookies:
        cookies = {}
        for a in _account_rows():
            prof = _profile(a)
            if chrome.port_alive(a.get("port") or 0):
                # 仍尝试读（复制 Cookies 通常能成功）；读失败再警告
                pass
            bundle = chrome_cookies.extract_account(prof)
            cookies[a["id"]] = bundle
            if not bundle.get("ok"):
                warnings.append(f"{a['id']} 登录态没带上：{bundle.get('error') or '未知原因'}")
            elif bundle.get("error"):
                warnings.append(f"{a['id']}：{bundle['error']}")
        payload["cookies"] = cookies
    return payload, warnings


def _header_counts(payload: dict, with_cookies: bool) -> dict:
    return {
        "settings": len(payload.get("settings") or {}),
        "prompts": len(payload.get("prompts") or {}),
        "accounts": len(payload.get("accounts") or []),
        "ai_providers": len(payload.get("ai_providers") or []),
        "counters": len(payload.get("counters") or {}),
        "desc_cards": len(payload.get("desc_cards") or []),
        "cookies": sum(1 for v in (payload.get("cookies") or {}).values() if v.get("ok"))
        if with_cookies else 0,
    }


def _encrypt(obj: dict, password: str) -> tuple[dict, dict, bytes]:
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    salt = os.urandom(SALT_LEN)
    nonce = os.urandom(NONCE_LEN)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, KDF_ITER, dklen=KEY_LEN)
    ct = AESGCM(key).encrypt(nonce, raw, None)
    kdf = {"algo": "pbkdf2-sha256", "iter": KDF_ITER, "salt": _b64(salt)}
    cipher = {"algo": "aes-256-gcm", "nonce": _b64(nonce)}
    return kdf, cipher, ct


def _decrypt(header: dict, ct: bytes, password: str) -> dict:
    try:
        kdf = header.get("kdf") or {}
        cipher = header.get("cipher") or {}
        if (kdf.get("algo") or "") != "pbkdf2-sha256" or (cipher.get("algo") or "") != "aes-256-gcm":
            raise ValueError("algo")
        salt = _b64d(kdf["salt"])
        nonce = _b64d(cipher["nonce"])
        iters = int(kdf.get("iter") or KDF_ITER)
        if iters < 1 or iters > 5_000_000:
            raise ValueError("iter")
        key = hashlib.pbkdf2_hmac(
            "sha256", (password or "").encode("utf-8"), salt, iters, dklen=KEY_LEN)
        raw = AESGCM(key).decrypt(nonce, ct, None)
        return json.loads(raw.decode("utf-8"))
    except OranteError:
        raise
    except Exception:
        raise OranteError(BAD_PASSWORD)


def _parse_file(path: Path) -> tuple[dict, bytes]:
    if not path.is_file():
        raise OranteError("找不到这个预设文件", str(path))
    data = path.read_bytes()
    if not data.startswith(MAGIC):
        raise OranteError("这不是 Orante 预设文件",
                          "文件开头应该是 ORANTE-PRESET。")
    rest = data[len(MAGIC):]
    nl = rest.find(b"\n")
    if nl < 0:
        raise OranteError("预设文件头坏了")
    try:
        header = json.loads(rest[:nl].decode("utf-8"))
    except Exception:
        raise OranteError("预设文件头不是合法 JSON")
    if not isinstance(header, dict):
        raise OranteError("预设文件头坏了")
    # 头部绝对不许带 key / cookie / 账号名 —— 读的时候也别把它们回给前端
    return {
        "format": header.get("format"),
        "orante": header.get("orante"),
        "created": header.get("created"),
        "machine": header.get("machine"),
        "has_cookies": bool(header.get("has_cookies")),
        "counts": header.get("counts") or {},
        "kdf": header.get("kdf") or {},
        "cipher": header.get("cipher") or {},
    }, rest[nl + 1:]


def _write_file(path: Path, header: dict, ct: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    hb = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    path.write_bytes(MAGIC + hb + b"\n" + ct)


def _check_password(password: str) -> str:
    password = password or ""
    if not password:
        raise OranteError("请设一个密码", "这份文件里有 API key 和登录态，必须加密。")
    if len(password) < 4:
        raise OranteError("密码太短", "至少 4 个字符。")
    return password


def export(groups: list[str] | None, cookies: bool, password: str, path: str | None) -> dict:
    password = _check_password(password)
    groups = [g for g in (groups or list(GROUPS)) if g in GROUPS]
    if not groups and not cookies:
        raise OranteError("至少勾一组")
    dest = Path(path) if path else default_export_path()
    if dest.suffix.lower() != ".orpreset":
        dest = dest.with_suffix(dest.suffix + ".orpreset") if dest.suffix else dest.with_suffix(".orpreset")
    payload, warnings = collect(groups, bool(cookies))
    kdf, cipher, ct = _encrypt(payload, password)
    header = {
        "format": FORMAT,
        "orante": __version__,
        "created": _now_iso(),
        "machine": _machine(),
        "has_cookies": bool(cookies),
        "counts": _header_counts(payload, bool(cookies)),
        "kdf": kdf,
        "cipher": cipher,
    }
    try:
        _write_file(dest, header, ct)
    except OSError as e:
        raise OranteError("预设文件写不进去", str(e))
    return {
        "path": str(dest),
        "bytes": dest.stat().st_size,
        "warnings": warnings,
        "meta": {k: header[k] for k in
                 ("format", "orante", "created", "machine", "has_cookies", "counts")},
    }


def inspect(path: str) -> dict:
    header, _ct = _parse_file(Path(path))
    return {"meta": {k: header[k] for k in
                     ("format", "orante", "created", "machine", "has_cookies", "counts")
                     if k in header}}


def _load(path: str, password: str) -> tuple[dict, dict]:
    header, ct = _parse_file(Path(path))
    payload = _decrypt(header, ct, password)
    if not isinstance(payload, dict):
        raise OranteError(BAD_PASSWORD)
    return header, payload


def _local_prompt_overridden() -> set[str]:
    names = set()
    for n in promptsmod.EDITABLE:
        if db.one("SELECT 1 FROM settings WHERE key=?", (f"prompts.{n}",)):
            names.add(n)
    return names


def diff(path: str, password: str) -> dict:
    header, payload = _load(path, password)
    local_set = _settings_rows()
    incoming_set = payload.get("settings") or {}
    set_over = set_add = set_same = 0
    for k, v in incoming_set.items():
        if k not in local_set:
            set_add += 1
        elif local_set[k] != v:
            set_over += 1
        else:
            set_same += 1

    local_pr = _prompt_rows()
    incoming_pr = payload.get("prompts") or {}
    pr_over, pr_add, pr_same = 0, 0, 0
    overwritten = []
    local_changed = _local_prompt_overridden()
    for k, v in incoming_pr.items():
        short = k.split(".", 1)[-1]
        if k not in local_pr:
            pr_add += 1
        elif local_pr[k] != v:
            pr_over += 1
            overwritten.append(short)
        else:
            pr_same += 1

    local_acc = {a["id"]: a for a in _account_rows()}
    local_by_name = {a["name"]: a for a in local_acc.values()}
    acc_add, acc_conflict = 0, []
    for a in payload.get("accounts") or []:
        if a["id"] in local_acc or a.get("name") in local_by_name:
            hit = local_acc.get(a["id"]) or local_by_name.get(a["name"])
            acc_conflict.append({"id": a["id"], "name": a.get("name"),
                                 "local_id": hit["id"], "local_name": hit["name"]})
        else:
            acc_add += 1

    local_prov = {p["id"]: p for p in _provider_rows()}
    local_pn = {p["name"]: p for p in local_prov.values()}
    pv_add, pv_conflict = 0, []
    for p in payload.get("ai_providers") or []:
        if p.get("id") in local_prov or p.get("name") in local_pn:
            hit = local_prov.get(p.get("id")) or local_pn.get(p.get("name"))
            pv_conflict.append({"id": p.get("id"), "name": p.get("name"),
                                "local_id": hit["id"]})
        else:
            pv_add += 1

    local_ct = _counter_rows()
    incoming_ct = payload.get("counters") or {}
    ct_over = sum(1 for k, v in incoming_ct.items() if k in local_ct and local_ct[k] != v)
    ct_add = sum(1 for k in incoming_ct if k not in local_ct)

    local_cards = {r["name"]: r for r in cardsmod.list_all()}
    dc_over = dc_add = dc_same = 0
    for raw in payload.get("desc_cards") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        hit = local_cards.get(name)
        if not hit:
            dc_add += 1
        elif (hit.get("prompt") or "") != str(raw.get("prompt") or ""):
            dc_over += 1
        else:
            dc_same += 1

    ck = payload.get("cookies") or {}
    return {
        "meta": {k: header[k] for k in
                 ("format", "orante", "created", "machine", "has_cookies", "counts")
                 if k in header},
        "settings": {"overwrite": set_over, "add": set_add, "same": set_same},
        "prompts": {"overwrite": pr_over, "add": pr_add, "same": pr_same,
                    "overwritten_names": overwritten,
                    "local_changed": sorted(local_changed)},
        "accounts": {"add": acc_add, "conflict": acc_conflict},
        "ai_providers": {"add": pv_add, "conflict": pv_conflict},
        "counters": {"overwrite": ct_over, "add": ct_add},
        "desc_cards": {"overwrite": dc_over, "add": dc_add, "same": dc_same},
        "cookies": {"accounts": len(ck),
                    "ok": sum(1 for v in ck.values() if v.get("ok"))},
    }


def _apply_settings(incoming: dict, mode: str) -> dict:
    applied = {"overwrite": 0, "add": 0, "skip": 0}
    if mode == "replace":
        db.run("DELETE FROM settings WHERE key NOT LIKE 'prompts.%'")
    local = _settings_rows()
    for k, v in (incoming or {}).items():
        if k.startswith("prompts."):
            continue
        if k == "workspace.root":
            p = Path(str(v or ""))
            if str(v) and not p.is_dir():
                applied["skip"] += 1
                continue
        if k in local:
            if mode == "merge_local":
                applied["skip"] += 1
                continue
            config.set(k, v)
            applied["overwrite"] += 1
        else:
            config.set(k, v)
            applied["add"] += 1
    return applied


def _apply_prompts(incoming: dict, mode: str) -> dict:
    applied = {"overwrite": 0, "add": 0, "skip": 0}
    if mode == "replace":
        db.run("DELETE FROM settings WHERE key LIKE 'prompts.%'")
    local = _prompt_rows()
    for k, v in (incoming or {}).items():
        if k in local:
            if mode == "merge_local":
                applied["skip"] += 1
                continue
            config.set(k, v)
            applied["overwrite"] += 1
        else:
            config.set(k, v)
            applied["add"] += 1
    return applied


def _free_port(prefer, taken: set) -> int:
    if prefer and int(prefer) not in taken and not chrome.port_alive(int(prefer)):
        return int(prefer)
    return chrome.free_port(9300, taken)


def _insert_account(row: dict, taken: set) -> str:
    aid = (row.get("id") or "").strip()
    if not aid or db.one("SELECT 1 FROM accounts WHERE id=?", (aid,)):
        aid = db.next_id("account", "A", 3)
    else:
        _ensure_counter_past("account", aid, "A")
    name = (row.get("name") or aid).strip()
    if db.one("SELECT 1 FROM accounts WHERE name=?", (name,)):
        name = f"{name}-{aid}"
    port = _free_port(row.get("port"), taken)
    taken.add(port)
    extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
    extra = dict(extra or {})
    extra.pop("legacy_lane", None)  # 换机后老路径没用
    extra.pop("login_state", None)  # 登录态以这次 cookie 写入为准
    engine = row.get("engine") if row.get("engine") in ("flow", "grok") else "flow"
    profile = aid  # 相对路径：data/chrome/<id>，不带本机绝对路径
    db.run(
        "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,aspect,"
        "max_concurrent,enabled,extra,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (aid, name, engine, port, profile,
         row.get("project_url") or "", row.get("model") or "",
         row.get("aspect") or "9:16", int(row.get("max_concurrent") or 5),
         int(row.get("enabled", 1) or 0), db.j(extra),
         row.get("created") or db.now()))
    return aid


def _ensure_counter_past(name: str, ident: str, prefix: str) -> None:
    """预设带来的 A006 要比计数器大时，把计数器抬过去，免得下次发号撞车。"""
    if not ident or not ident.startswith(prefix):
        return
    try:
        n = int(ident[len(prefix):])
    except ValueError:
        return
    cur = db.one("SELECT value FROM counters WHERE name=?", (name,))
    v = int(cur["value"]) if cur else 0
    if n > v:
        db.run("INSERT OR REPLACE INTO counters(name,value) VALUES(?,?)", (name, n))


def _update_account(local_id: str, row: dict) -> None:
    a = accounts.get(local_id)
    extra = dict(a.get("extra") or {})
    incoming = row.get("extra") if isinstance(row.get("extra"), dict) else {}
    extra.update(incoming or {})
    fields = {
        "project_url": row.get("project_url"),
        "model": row.get("model"),
        "aspect": row.get("aspect"),
        "max_concurrent": row.get("max_concurrent"),
        "enabled": row.get("enabled"),
        "extra": extra,
    }
    new_name = (row.get("name") or "").strip()
    if new_name and new_name != a["name"]:
        if not db.one("SELECT 1 FROM accounts WHERE name=? AND id!=?", (new_name, local_id)):
            fields["name"] = new_name
    accounts.update(local_id, **{k: v for k, v in fields.items() if v is not None or k == "extra"})


def _apply_accounts(incoming: list, mode: str) -> tuple[dict, dict]:
    """返回 (applied, id_map preset_id -> local_id)。"""
    applied = {"add": 0, "overwrite": 0, "skip": 0}
    id_map: dict[str, str] = {}
    taken = {r["port"] for r in db.rows("SELECT port FROM accounts") if r["port"]}
    if mode == "replace":
        db.run("DELETE FROM accounts")
        taken = set()
        keep_ids = set()
        for row in incoming or []:
            lid = _insert_account(row, taken)
            id_map[row.get("id") or lid] = lid
            keep_ids.add(lid)
            applied["add"] += 1
        # 整包替换：不属于新账号的专用 profile 清掉（绝对路径的老泳道不动）
        if paths.CHROME_PROFILES.is_dir():
            for child in list(paths.CHROME_PROFILES.iterdir()):
                if child.name not in keep_ids:
                    try:
                        if child.is_dir():
                            shutil.rmtree(child)
                        else:
                            child.unlink()
                    except OSError:
                        pass
        return applied, id_map

    local = {a["id"]: a for a in _account_rows()}
    by_name = {a["name"]: a for a in local.values()}
    for row in incoming or []:
        pid = row.get("id") or ""
        hit = local.get(pid) or by_name.get(row.get("name") or "")
        if hit:
            id_map[pid] = hit["id"]
            if mode == "merge_local":
                applied["skip"] += 1
            else:
                _update_account(hit["id"], row)
                applied["overwrite"] += 1
        else:
            lid = _insert_account(row, taken)
            id_map[pid or lid] = lid
            applied["add"] += 1
    return applied, id_map


def _insert_provider(row: dict) -> str:
    pid = (row.get("id") or "").strip()
    if not pid or db.one("SELECT 1 FROM ai_providers WHERE id=?", (pid,)):
        pid = db.next_id("aiprovider", "K", 4)
    else:
        _ensure_counter_past("aiprovider", pid, "K")
    extra = row.get("extra") or {}
    if isinstance(extra, dict):
        extra = db.j(extra)
    db.run(
        "INSERT INTO ai_providers(id,name,kind,base_url,api_key,model,priority,"
        "max_inflight,daily_limit,vision,enabled,row_order,extra,created)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (pid, row.get("name") or pid, row.get("kind") or "gemini",
         row.get("base_url") or "", row.get("api_key") or "",
         row.get("model") or "", int(row.get("priority") or 1),
         int(row.get("max_inflight") or 2), int(row.get("daily_limit") or 0),
         int(row.get("vision") if row.get("vision") is not None else 1),
         int(row.get("enabled") if row.get("enabled") is not None else 1),
         int(row.get("row_order") or 0), extra or "{}",
         row.get("created") or db.now()))
    return pid


def _apply_providers(incoming: list, mode: str) -> dict:
    applied = {"add": 0, "overwrite": 0, "skip": 0}
    if mode == "replace":
        db.run("DELETE FROM ai_leases")
        db.run("DELETE FROM ai_state")
        db.run("DELETE FROM ai_providers")
    local = {p["id"]: p for p in aikeys.list_all()}
    by_name = {p["name"]: p for p in local.values()}
    for row in incoming or []:
        hit = local.get(row.get("id")) or by_name.get(row.get("name") or "")
        if hit:
            if mode == "merge_local":
                applied["skip"] += 1
                continue
            d = dict(row)
            d.pop("id", None)
            try:
                aikeys.update(hit["id"], d)
                applied["overwrite"] += 1
            except OranteError:
                applied["skip"] += 1
        else:
            try:
                _insert_provider(row)
                applied["add"] += 1
            except Exception:
                applied["skip"] += 1
    return applied


def _apply_counters(incoming: dict, mode: str) -> dict:
    applied = {"overwrite": 0, "add": 0, "skip": 0}
    if not incoming:
        return applied
    local = _counter_rows()
    for name, value in incoming.items():
        try:
            value = int(value)
        except (TypeError, ValueError):
            continue
        if name in local:
            if mode == "merge_local":
                applied["skip"] += 1
                continue
            db.run("INSERT OR REPLACE INTO counters(name,value) VALUES(?,?)", (name, value))
            applied["overwrite"] += 1
        else:
            db.run("INSERT OR REPLACE INTO counters(name,value) VALUES(?,?)", (name, value))
            applied["add"] += 1
    return applied


def _apply_cookies(bundles: dict, id_map: dict, warnings: list) -> list[str]:
    need_relogin = []
    if not bundles:
        return need_relogin
    for pid, bundle in bundles.items():
        lid = id_map.get(pid) or pid
        try:
            acc = accounts.get(lid)
        except OranteError:
            warnings.append(f"{pid} 登录态对不上账号，跳过")
            need_relogin.append(pid)
            continue
        alive = chrome.port_alive(acc.get("port") or 0)
        if not bundle.get("ok"):
            accounts.set_login_state(lid, "need_relogin")
            need_relogin.append(acc["id"])
            warnings.append(f"{acc['id']} 需重登：{bundle.get('error') or '预设里没有可用登录态'}")
            continue
        res = chrome_cookies.inject_account(_profile(acc), bundle, chrome_alive=alive)
        if res.get("ok"):
            accounts.set_login_state(lid, "imported")
        else:
            accounts.set_login_state(lid, "need_relogin")
            need_relogin.append(acc["id"])
            warnings.append(f"{acc['id']} 需重登：{res.get('error')}")
    return need_relogin


def _backup_current(password: str, tag: str) -> Path:
    dest = paths.DATA / f"预设备份-{tag}-{_stamp()}.orpreset"
    export(list(GROUPS), True, password, str(dest))
    return dest


def do_import(path: str, password: str, mode: str, groups: list[str] | None) -> dict:
    if mode not in ("merge_preset", "merge_local", "replace"):
        raise OranteError("合并方式只能是 merge_preset / merge_local / replace")
    password = _check_password(password)
    _header, payload = _load(path, password)
    if groups is None:
        want = set(payload.get("groups") or GROUPS)
        with_cookies = bool(payload.get("cookies"))
    else:
        want = set(groups)
        with_cookies = "cookies" in want

    warnings: list[str] = []
    backup = None
    if mode == "replace":
        try:
            backup = str(_backup_current(password, "替换前"))
        except OranteError as e:
            raise OranteError("整包替换前自动备份失败，没有改任何东西", e.message)

    applied = {}
    id_map: dict[str, str] = {}
    if "settings" in want:
        applied["settings"] = _apply_settings(payload.get("settings") or {}, mode)
    if "prompts" in want:
        applied["prompts"] = _apply_prompts(payload.get("prompts") or {}, mode)
    if "accounts" in want:
        applied["accounts"], id_map = _apply_accounts(payload.get("accounts") or [], mode)
        for a in payload.get("accounts") or []:
            pid = a.get("id")
            if pid and pid not in id_map:
                id_map[pid] = pid
    if "ai_providers" in want:
        applied["ai_providers"] = _apply_providers(payload.get("ai_providers") or [], mode)
    if "counters" in want:
        applied["counters"] = _apply_counters(payload.get("counters") or {}, mode)
    if "desc_cards" in want:
        applied["desc_cards"] = cardsmod.apply_rows(payload.get("desc_cards") or [], mode)

    need_relogin: list[str] = []
    if with_cookies and payload.get("cookies"):
        if not id_map:
            id_map = {a["id"]: a["id"] for a in _account_rows()}
        need_relogin = _apply_cookies(payload.get("cookies") or {}, id_map, warnings)

    return {
        "applied": applied,
        "need_relogin": need_relogin,
        "warnings": warnings,
        "backup": backup,
    }


def factory_reset(confirm: str, password: str = "") -> dict:
    if (confirm or "").strip() != "确认清空":
        raise OranteError("请输入「确认清空」四个字", "这是为了防止手滑。")
    password = password or f"factory-{_stamp()}"
    try:
        backup = str(_backup_current(password, "出厂前"))
    except OranteError as e:
        raise OranteError("恢复出厂前自动备份失败，没有改任何东西", e.message)

    warnings = []
    running = []
    for a in _account_rows():
        if chrome.port_alive(a.get("port") or 0):
            running.append(a["id"])
    if running:
        raise OranteError(
            "还有账号的 Chrome 开着，先关掉再恢复出厂",
            "开着的：" + "、".join(running))

    db.run("DELETE FROM accounts")
    db.run("DELETE FROM ai_leases")
    db.run("DELETE FROM ai_state")
    db.run("DELETE FROM ai_providers")
    db.run("DELETE FROM settings")
    db.run("DELETE FROM desc_cards")
    try:
        cardsmod.seed_if_empty()
    except Exception:
        pass
    # tasks / jobs / batches / counters 保留
    chrome_dir = paths.CHROME_PROFILES
    if chrome_dir.is_dir():
        for child in list(chrome_dir.iterdir()):
            try:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            except OSError as e:
                warnings.append(f"删不掉 {child.name}：{e}")
    return {
        "backup": backup,
        "backup_password": password,
        "warnings": warnings,
    }


# ---------------- 套装分组（规格 76）：只换用法，不另写一份序列化 ----------------
# 套装五组的 payload 全部从这里的 collect / apply 进出。suites.py 只负责存表、
# 切当前、拦活跃 job，不许自己拼 prompts / coverage / 卡片 / gen / 账号 JSON。

SUITE_GROUPS = ("prompts", "coverage", "desc_cards", "gen", "accounts")
SUITE_COLORS = (
    "#D9A34F", "#6BB2AF", "#D9705F", "#8FB06A",
    "#7FA7D9", "#C08497", "#B8956A", "#9B8EC4",
)
# 提示词组不含扩图覆盖库（那是 coverage）。和界面「提示词 9 块」对齐。
_PROMPT_SKIP = frozenset({"expand"})
_GEN_EXACT = ("generate.mode", "flow.image_model_key")
_GEN_PREFIXES = ("split.", "track.", "expand.")
_ACCOUNT_SETTING_KEYS = (
    "engine.flow.model",
    "engine.flow.max_concurrent",
    "engine.grok.max_concurrent",
)


def _canon(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)


def same_payload(a, b) -> bool:
    return _canon(a) == _canon(b)


def gen_keys() -> list[str]:
    """generate.mode / split.* / track.* / expand.* / flow.image_model_key。"""
    keys = list(_GEN_EXACT)
    for k in config.DEFAULTS:
        if any(k.startswith(p) for p in _GEN_PREFIXES) and k not in keys:
            keys.append(k)
    return keys


def collect_suite_group(grp: str):
    """当前配置的一组，格式和 26 号分组产物一致。"""
    if grp not in SUITE_GROUPS:
        raise OranteError(f"套装没有叫 {grp} 的组",
                          "可选：" + " / ".join(SUITE_GROUPS))
    if grp == "prompts":
        out = {}
        for name in promptsmod.EDITABLE:
            if name in _PROMPT_SKIP:
                continue
            out[f"prompts.{name}"] = promptsmod.get_block(name)
        out["prompts.emotion_preset"] = promptsmod.emotion_preset_name()
        out["prompts.format_version"] = promptsmod.PROMPT_FORMAT_VERSION
        for k, v in _prompt_rows().items():
            if k == "prompts.expand":
                continue
            if k not in out:
                out[k] = v
        return out
    if grp == "coverage":
        rows = _prompt_rows()
        if "prompts.expand" in rows:
            return rows["prompts.expand"]
        return promptsmod.get_coverage()
    if grp == "desc_cards":
        return cardsmod.dump_rows()
    if grp == "gen":
        return {k: config.get(k) for k in gen_keys()}
    # accounts：只记选用，不记账号实体、不碰登录态。probe=False：切换是瞬间的，别去敲 Chrome 端口。
    accs = accounts.list_all(probe=False)
    enabled = {a["id"]: int(bool(a.get("enabled", 1))) for a in accs}
    defaults: dict[str, str] = {}
    for eng in ("flow", "grok"):
        aid = str(config.get(f"engine.{eng}.default_account") or "")
        if aid and aid not in enabled:
            aid = ""
        if not aid:
            for a in accs:
                if a.get("engine") == eng and a.get("enabled"):
                    aid = a["id"]
                    break
        defaults[eng] = aid
    settings = {k: config.get(k) for k in _ACCOUNT_SETTING_KEYS}
    settings["engine.flow.default_account"] = defaults.get("flow") or ""
    settings["engine.grok.default_account"] = defaults.get("grok") or ""
    settings["expand.account_id"] = str(config.get("expand.account_id") or "")
    return {
        "enabled": enabled,
        "defaults": defaults,
        "settings": settings,
    }


def _bump_prompt_rev() -> None:
    """规格 93：套装换了提示词 → 提示词世代 +1，还没出片的段下一轮会按新的重拼。"""
    try:
        from . import prompts as _pm
        _pm.bump_rev("suite")
    except Exception:  # noqa: BLE001
        pass


def apply_suite_group(grp: str, payload) -> dict:
    """把一组套装 payload 写进当前配置。账号组只改 enabled / 几个设置，不碰 profile。"""
    if grp not in SUITE_GROUPS:
        raise OranteError(f"套装没有叫 {grp} 的组")
    if grp == "prompts":
        incoming = payload if isinstance(payload, dict) else {}
        local = _prompt_rows()
        applied = {"overwrite": 0, "add": 0, "skip": 0}
        for k in list(local):
            if k == "prompts.expand":
                continue
            if k not in incoming:
                config.reset(k)
                applied["skip"] += 1
        for k, v in incoming.items():
            if k == "prompts.expand" or k in PROMPT_INTERNAL_KEYS:
                continue
            config.set(k, v)
            applied["overwrite"] += 1
        _bump_prompt_rev()
        return applied
    if grp == "coverage":
        if payload is None:
            return {"overwrite": 0}
        config.set("prompts.expand", payload)
        _bump_prompt_rev()
        return {"overwrite": 1}
    if grp == "desc_cards":
        rows = payload if isinstance(payload, list) else []
        return cardsmod.apply_rows(rows, "replace")
    if grp == "gen":
        incoming = payload if isinstance(payload, dict) else {}
        applied = {"overwrite": 0}
        for k, v in incoming.items():
            if k not in gen_keys():
                continue
            config.set(k, v)
            applied["overwrite"] += 1
        return applied
    # accounts
    incoming = payload if isinstance(payload, dict) else {}
    skipped: list[str] = []
    applied = {"overwrite": 0, "skip": 0}
    enabled = incoming.get("enabled") if isinstance(incoming.get("enabled"), dict) else {}
    for aid, flag in enabled.items():
        aid = str(aid or "").strip()
        if not aid:
            continue
        try:
            accounts.update(aid, enabled=1 if flag else 0)
            applied["overwrite"] += 1
        except OranteError:
            skipped.append(aid)
            applied["skip"] += 1
    settings = incoming.get("settings") if isinstance(incoming.get("settings"), dict) else {}
    defaults = incoming.get("defaults") if isinstance(incoming.get("defaults"), dict) else {}
    if defaults:
        if "engine.flow.default_account" not in settings:
            settings = dict(settings)
            settings["engine.flow.default_account"] = defaults.get("flow") or ""
        if "engine.grok.default_account" not in settings:
            settings = dict(settings)
            settings["engine.grok.default_account"] = defaults.get("grok") or ""
    have = {a["id"] for a in accounts.list_all(probe=False)}
    for k, v in settings.items():
        if k in ("engine.flow.default_account", "engine.grok.default_account", "expand.account_id"):
            aid = str(v or "").strip()
            if aid and aid not in have:
                skipped.append(aid)
                applied["skip"] += 1
                continue
        if k in _ACCOUNT_SETTING_KEYS or k in (
            "engine.flow.default_account", "engine.grok.default_account", "expand.account_id",
        ):
            config.set(k, v)
            applied["overwrite"] += 1
    # 去重跳过的账号，保持出现顺序
    seen = set()
    uniq = []
    for a in skipped:
        if a in seen:
            continue
        seen.add(a)
        uniq.append(a)
    applied["skipped_accounts"] = uniq
    return applied


def suite_counts(groups: dict) -> dict:
    """面板上那行「提示词 N 块 · 卡片 M 张 · 账号 K 个」。"""
    prompts = groups.get("prompts") if isinstance(groups.get("prompts"), dict) else {}
    n_prompts = 0
    for name in promptsmod.EDITABLE:
        if name in _PROMPT_SKIP:
            continue
        if f"prompts.{name}" in prompts:
            n_prompts += 1
    cards = groups.get("desc_cards") if isinstance(groups.get("desc_cards"), list) else []
    acc = groups.get("accounts") if isinstance(groups.get("accounts"), dict) else {}
    enabled = acc.get("enabled") if isinstance(acc.get("enabled"), dict) else {}
    n_acc = sum(1 for v in enabled.values() if v)
    return {"prompts": n_prompts, "cards": len(cards), "accounts": n_acc}


def preset_payload_from_suite_groups(groups: dict) -> dict:
    """套装五组 → 26 号预设密文结构。不含账号实体、cookie、API key。"""
    payload: dict = {"groups": []}
    prompts = dict(groups.get("prompts") or {})
    if "coverage" in groups and groups.get("coverage") is not None:
        prompts["prompts.expand"] = groups["coverage"]
    if prompts:
        payload["prompts"] = prompts
        payload["groups"].append("prompts")
    cards = groups.get("desc_cards")
    if cards is not None:
        payload["desc_cards"] = cards if isinstance(cards, list) else []
        payload["groups"].append("desc_cards")
    settings = {}
    if isinstance(groups.get("gen"), dict):
        settings.update(groups["gen"])
    acc = groups.get("accounts") if isinstance(groups.get("accounts"), dict) else {}
    if isinstance(acc.get("settings"), dict):
        settings.update(acc["settings"])
    if settings:
        payload["settings"] = settings
        payload["groups"].append("settings")
    return payload


def suite_groups_from_preset_payload(payload: dict) -> dict:
    """26 号预设 → 套装五组。账号只抽选用，登录态丢掉。"""
    payload = payload if isinstance(payload, dict) else {}
    out: dict = {}
    raw_pr = payload.get("prompts") if isinstance(payload.get("prompts"), dict) else {}
    prompts = {k: v for k, v in raw_pr.items() if k != "prompts.expand"}
    if prompts:
        out["prompts"] = prompts
    if "prompts.expand" in raw_pr:
        out["coverage"] = raw_pr["prompts.expand"]
    if "desc_cards" in payload:
        cards = payload.get("desc_cards")
        out["desc_cards"] = cards if isinstance(cards, list) else []
    raw_set = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
    gen = {k: v for k, v in raw_set.items() if k in gen_keys()}
    if gen:
        out["gen"] = gen
    acc_sel = {"enabled": {}, "defaults": {}, "settings": {}}
    for k in _ACCOUNT_SETTING_KEYS:
        if k in raw_set:
            acc_sel["settings"][k] = raw_set[k]
    for k in ("engine.flow.default_account", "engine.grok.default_account", "expand.account_id"):
        if k in raw_set:
            acc_sel["settings"][k] = raw_set[k]
    for a in payload.get("accounts") or []:
        if not isinstance(a, dict):
            continue
        aid = str(a.get("id") or "").strip()
        if not aid:
            continue
        acc_sel["enabled"][aid] = int(bool(a.get("enabled", 1)))
        eng = a.get("engine")
        if eng in ("flow", "grok") and a.get("enabled") and not acc_sel["defaults"].get(eng):
            acc_sel["defaults"][eng] = aid
    if acc_sel["enabled"] or acc_sel["settings"]:
        out["accounts"] = acc_sel
    return out


def export_payload(payload: dict, password: str, path: str | None) -> dict:
    """已经收集好的 payload 写成 .orpreset。套装导出走这里，不重读当前库。"""
    password = _check_password(password)
    dest = Path(path) if path else default_export_path()
    if dest.suffix.lower() != ".orpreset":
        dest = dest.with_suffix(dest.suffix + ".orpreset") if dest.suffix else dest.with_suffix(".orpreset")
    kdf, cipher, ct = _encrypt(payload, password)
    with_cookies = bool(payload.get("cookies"))
    header = {
        "format": FORMAT,
        "orante": __version__,
        "created": _now_iso(),
        "machine": _machine(),
        "has_cookies": with_cookies,
        "counts": _header_counts(payload, with_cookies),
        "kdf": kdf,
        "cipher": cipher,
    }
    try:
        _write_file(dest, header, ct)
    except OSError as e:
        raise OranteError("预设文件写不进去", str(e))
    return {
        "path": str(dest),
        "bytes": dest.stat().st_size,
        "warnings": [],
        "meta": {k: header[k] for k in
                 ("format", "orante", "created", "machine", "has_cookies", "counts")},
    }


def load_preset(path: str, password: str) -> tuple[dict, dict]:
    """读 .orpreset，给套装导入用。错密码仍是那一句人话。"""
    return _load(path, password)
