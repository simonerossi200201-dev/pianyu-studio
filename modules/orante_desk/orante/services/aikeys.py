# -*- coding: utf-8 -*-
"""多模态接口池的「名册」：增删改查 + 从老的单条 gemini.api_key 迁移过来。

一条记录 = 一个能看图的接口（Gemini 官方、任何 OpenAI 兼容的服务、本地 Ollama）。
条数不限，类型不限；池子的调度在 aipool.py，实际发请求在 ai.py。

字段的意思：
  kind          gemini | openai | ollama
  base_url      openai/ollama 用；gemini 留空走官方地址
  model         模型名，必填（本地的比如 qwen2.5vl:7b）
  priority      1 最优先。先把高优先级那一档用满，才轮到下一档（同档之间均衡）
  max_inflight  这条同时最多接几个活（默认 2）。全池并发 = 各条相加，再卡总上限
  daily_limit   自己知道的每日上限，0 = 不设。到了就自动歇到明天（免费额度用这个）
  vision        支不支持看图。0 的只接纯文字的活
"""
from __future__ import annotations

import json

from .. import db
from ..errors import OranteError

KINDS = ("gemini", "openai", "ollama")
DEFAULT_BASE = {
    "gemini": "",
    "openai": "https://api.openai.com/v1",
    "ollama": "http://127.0.0.1:11434",
}


def _row(r: dict) -> dict:
    r = dict(r)
    r["extra"] = db.unj(r.get("extra"), {}) or {}
    for k in ("priority", "max_inflight", "daily_limit", "vision", "enabled", "row_order"):
        r[k] = int(r.get(k) or 0)
    return r


def list_all(enabled_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM ai_providers"
    if enabled_only:
        sql += " WHERE enabled=1"
    sql += " ORDER BY priority, row_order, created, id"
    return [_row(r) for r in db.rows(sql)]


def get(pid: str) -> dict:
    r = db.one("SELECT * FROM ai_providers WHERE id=?", (pid,))
    if not r:
        raise OranteError(f"没有这个接口 {pid}")
    return _row(r)


def _clean(d: dict, old: dict | None = None) -> dict:
    old = old or {}
    kind = str(d.get("kind") or old.get("kind") or "gemini").strip().lower()
    if kind not in KINDS:
        raise OranteError(f"未知的接口类型 {kind}", "只支持 " + " / ".join(KINDS))
    name = str(d.get("name") or old.get("name") or "").strip()
    model = str(d.get("model") or old.get("model") or "").strip()
    if not name:
        raise OranteError("给这个接口起个名字", "比如「Gemini-主」「本地 Qwen」")
    if not model:
        raise OranteError(f"「{name}」还没填模型名")
    base = str(d.get("base_url", old.get("base_url")) or "").strip().rstrip("/")
    if not base and kind != "gemini":
        base = DEFAULT_BASE[kind]
    key = d.get("api_key", old.get("api_key"))
    key = str(key or "").strip()
    if kind == "gemini" and not key:
        raise OranteError(f"「{name}」还没填 API Key")

    def num(field, lo, hi, dflt):
        v = d.get(field, old.get(field, dflt))
        try:
            v = int(v)
        except Exception:
            v = dflt
        return max(lo, min(hi, v))

    return {
        "name": name, "kind": kind, "base_url": base, "api_key": key, "model": model,
        "priority": num("priority", 1, 99, 1),
        "max_inflight": num("max_inflight", 1, 32, 2),
        "daily_limit": num("daily_limit", 0, 10_000_000, 0),
        "vision": 1 if int(d.get("vision", old.get("vision", 1)) or 0) else 0,
        "enabled": 1 if int(d.get("enabled", old.get("enabled", 1)) or 0) else 0,
        "row_order": num("row_order", 0, 9999, int(old.get("row_order") or 0)),
        "extra": db.j(d.get("extra", old.get("extra")) or {}),
    }


def add(d: dict) -> dict:
    v = _clean(d)
    pid = db.next_id("aiprovider", "K", 4)
    if not v["row_order"]:
        n = db.one("SELECT COUNT(*) AS n FROM ai_providers") or {"n": 0}
        v["row_order"] = int(n["n"]) + 1
    cols = ",".join(v)
    db.run(f"INSERT INTO ai_providers(id,{cols},created) VALUES(?,{','.join('?' * len(v))},?)",
           (pid, *v.values(), db.now()))
    return get(pid)


def update(pid: str, d: dict) -> dict:
    old = get(pid)
    # 前端不回传 api_key 时保留原值（界面上只显示打码的尾巴，别把 key 洗掉）
    if "api_key" in d and str(d.get("api_key") or "").strip() in ("", "****"):
        d = dict(d)
        d.pop("api_key")
    v = _clean(d, old)
    sets = ",".join(f"{k}=?" for k in v)
    db.run(f"UPDATE ai_providers SET {sets} WHERE id=?", (*v.values(), pid))
    return get(pid)


def delete(pid: str) -> None:
    get(pid)
    db.run("DELETE FROM ai_providers WHERE id=?", (pid,))
    db.run("DELETE FROM ai_state WHERE provider_id=?", (pid,))
    db.run("DELETE FROM ai_leases WHERE provider_id=?", (pid,))


def reorder(ids: list[str]) -> None:
    for i, pid in enumerate(ids or []):
        db.run("UPDATE ai_providers SET row_order=? WHERE id=?", (i + 1, pid))


def masked(p: dict) -> dict:
    """给界面用：key 只留尾巴。"""
    p = dict(p)
    k = str(p.get("api_key") or "")
    p["api_key"] = ("…" + k[-4:]) if len(k) > 4 else ("****" if k else "")
    p["has_key"] = bool(k)
    return p


def migrate_from_settings() -> dict | None:
    """老版本只有一条 settings['gemini.api_key']。名册是空的就把它搬进来，
    这样升级完不用重填，行为跟以前一模一样（一条 key、慢慢跑）。"""
    if db.one("SELECT id FROM ai_providers LIMIT 1"):
        return None
    r = db.one("SELECT value FROM settings WHERE key='gemini.api_key'")
    if not r:
        return None
    try:
        key = json.loads(r["value"])
    except Exception:
        key = r["value"]
    key = str(key or "").strip()
    if not key:
        return None
    m = db.one("SELECT value FROM settings WHERE key='gemini.model'")
    try:
        model = json.loads(m["value"]) if m else ""
    except Exception:
        model = (m or {}).get("value") or ""
    c = db.one("SELECT value FROM settings WHERE key='gemini.concurrency'")
    try:
        inflight = int(json.loads(c["value"])) if c else 2
    except Exception:
        inflight = 2
    return add({"name": "Gemini", "kind": "gemini", "api_key": key,
                "model": str(model or "gemini-3.5-flash-lite"),
                "priority": 1, "max_inflight": max(1, min(32, inflight)), "vision": 1})
