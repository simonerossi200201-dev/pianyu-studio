# -*- coding: utf-8 -*-
"""账号（一个账号 = 一个 Flow 或 Grok 登录态 = 一个 Chrome profile + 一个端口）。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import db, paths, config
from .engines import chrome
from .errors import OranteError

# 规格 82：Google 限流冷却。第一次 20 分钟，30 分钟内再被限就翻倍，上限 2 小时。
THROTTLE_FIRST = 20 * 60
THROTTLE_CAP = 2 * 3600
THROTTLE_DOUBLE_WINDOW = 30 * 60
THROTTLE_SUCCESS_RESET = 5
NETWORK_THROTTLE_WINDOW = 10 * 60
NETWORK_THROTTLE_PAUSE = 30 * 60
# 规格 91：几个不同账号在窗口内接连受限，才认为是"整条网络被限"。
# 原来是 2 个——太容易误判：两个账号先后各自超了一次，跟网络没关系，
# 却会把所有 Flow 账号一起按住，而此时浏览器里明明还能生成。
NETWORK_THROTTLE_ACCOUNTS = 3
THROTTLE_PROBE_EVERY = 4 * 60      # 冷却期间每隔这么久，放一次"探路"提交

FLOW_HOME = "https://flow.google.com/"
GROK_HOME = "https://grok.com/"


def _profile_dir(acc: dict) -> Path:
    prof = acc.get("profile") or ""
    p = Path(prof)
    if p.is_absolute():
        return p                          # 从老泳道导入的，沿用原 profile（不用重登）
    return paths.CHROME_PROFILES / (prof or acc["id"])


def profile_dir(acc: dict) -> Path:
    return _profile_dir(acc)


_ALIVE: dict[str, bool] = {}

# ---- 规格 98：本地 AI 算力网关（LTX-2.5 22B / FLUX.2 4B）虚拟账号（作为可选项，优先使用付费 Flow/Grok 账号）----
STUDIO_ID = "STUDIO"
STUDIO_NAME = "本地 AI · LTX-2.5 / FLUX.2 (可选项)"
# 任务表里填的账号名也认这些写法
STUDIO_ALIASES = {
    "studio", "本地模型", "本地ai", "本地 ai", "studio zero",
    "本地模型 · studio zero", "本地模型·studio zero",
    "本地 ai · ltx-2.5 / flux.2 (可选项)", "ltx", "ltx-2.5", "ltx25", "flux", "flux2",
}
_STUDIO_HEALTH: dict = {"at": 0.0, "h": {}}
_STUDIO_HEALTH_TTL = 15.0


def is_virtual(acc_or_id) -> bool:
    aid = acc_or_id.get("id") if isinstance(acc_or_id, dict) else acc_or_id
    return str(aid or "") == STUDIO_ID


def is_studio_name(name) -> bool:
    return str(name or "").strip().lower() in STUDIO_ALIASES or str(name or "").strip() == STUDIO_ID


def studio_enabled() -> bool:
    return bool(config.get("engine.studio.enabled", False))


def studio_health(force: bool = False) -> dict:
    """本地模型接口的健康状态，缓存 15 秒（/api/state 会被轮询，别每次都去探）。没启用就不探。"""
    import time as _t
    if not studio_enabled():
        return {"ok": False, "comfy": False, "disabled": True}
    now = _t.time()
    if force or now - _STUDIO_HEALTH["at"] > _STUDIO_HEALTH_TTL:
        from .engines import studio as _st
        _STUDIO_HEALTH["h"] = _st.health(timeout=1.0)
        _STUDIO_HEALTH["at"] = now
    return dict(_STUDIO_HEALTH["h"] or {})


def studio_account() -> dict:
    try:
        mc = int(config.get("engine.studio.max_inflight", 2) or 2)
    except (TypeError, ValueError):
        mc = 2
    return {
        "id": STUDIO_ID, "name": STUDIO_NAME, "engine": "studio",
        "port": None, "profile": "", "project_url": "",
        "model": str(config.get("engine.studio.quality", "balanced") or "balanced"), "aspect": "9:16",
        "max_concurrent": mc, "enabled": 1 if studio_enabled() else 0,
        "extra": {}, "login_state": "", "virtual": True, "created": "",
    }


def studio_public(probe: bool = True) -> dict:
    """给前端的本地模型「账号」：任务表选账号、重生菜单、账号抽屉都用它（/api/accounts 会把它
    放在列表最后，virtual=True）。数据库里没有这一行，list_all() 也不含它——后端那些假定账号背后有
    Chrome 的地方（预设导出、扩图选账号……）看不到它。"""
    a = studio_account()
    h = studio_health() if probe else dict(_STUDIO_HEALTH["h"] or {})
    a["alive"] = bool(h.get("ok") and h.get("comfy"))
    a["api_up"] = bool(h.get("ok"))
    a["busy"] = bool(h.get("busy"))
    a["queued"] = int(h.get("queued") or 0)
    a["quality"] = str(config.get("engine.studio.quality", "balanced") or "balanced")
    return a


def _no_virtual(acc_id: str, what: str = "改") -> None:
    if is_virtual(acc_id):
        raise OranteError(f"本地模型（Studio Zero）不是浏览器账号，不能{what}",
                          "开关和出片模式在「账号」抽屉最下面的本地模型那一栏。")


def list_all(probe: bool = True) -> list[dict]:
    """probe=False 时不探 Chrome 端口，浅层刷新用上次结果。"""
    out = db.rows("SELECT * FROM accounts ORDER BY engine, created")
    for a in out:
        a["extra"] = db.unj(a.get("extra"), {}) or {}
        if probe and a.get("port"):
            alive = chrome.port_alive(a["port"])
            _ALIVE[a["id"]] = alive
            a["alive"] = alive
        else:
            a["alive"] = _ALIVE.get(a["id"], False) if a.get("port") else False
        a["login_state"] = a["extra"].get("login_state") or ""
    return out


def get(acc_id: str) -> dict:
    if is_virtual(acc_id):
        return studio_account()
    a = db.one("SELECT * FROM accounts WHERE id=?", (acc_id,))
    if not a:
        raise OranteError(f"没有这个账号：{acc_id}", "到「账号」抽屉里先添加。")
    a["extra"] = db.unj(a.get("extra"), {}) or {}
    a["login_state"] = a["extra"].get("login_state") or ""
    return a


def set_login_state(acc_id: str, state: str) -> dict:
    """imported / need_relogin / 空。空 = 清掉标记。"""
    _no_virtual(acc_id, "改登录状态")
    a = get(acc_id)
    extra = dict(a.get("extra") or {})
    if state:
        extra["login_state"] = state
    else:
        extra.pop("login_state", None)
    return update(acc_id, extra=extra)


def flow_video_models() -> list[dict]:
    """设置 → 账号下拉：免费档可选，其余标「扣额度」并在未开 allow_paid 时禁用。"""
    from .engines import flow_api
    paid_ok = flow_api.allow_paid_models()
    out = []
    for name in flow_api.MODEL_FAMILY:
        free = name in flow_api.FLOW_FREE_VIDEO_NAMES
        out.append({"id": name, "paid": not free, "enabled": bool(free or paid_ok)})
    return out


def _check_flow_model(engine: str, model: str) -> None:
    if engine != "flow" or not model:
        return
    from .engines import flow_api
    if model not in flow_api.MODEL_FAMILY:
        return
    if model in flow_api.FLOW_FREE_VIDEO_NAMES:
        return
    if flow_api.allow_paid_models():
        return
    raise OranteError(
        "这个模型会扣额度，已拦下",
        f"{model} 不在免费白名单。设置 flow.allow_paid=true 才可选。",
    )


def create(name: str, engine: str, project_url: str = "", model: str = "", **extra) -> dict:
    name = (name or "").strip()
    if not name:
        raise OranteError("账号得有个名字", "比如 我的flow1、工作室-1。")
    if engine not in ("flow", "grok"):
        raise OranteError("引擎只能是 flow 或 grok")
    if db.one("SELECT 1 FROM accounts WHERE name=?", (name,)):
        raise OranteError(f"已经有叫 {name} 的账号了")
    taken = {r["port"] for r in db.rows("SELECT port FROM accounts") if r["port"]}
    port = chrome.free_port(9300, taken)
    aid = db.next_id("account", "A", 3)
    model = model or (config.get("engine.flow.model") if engine == "flow" else "")
    _check_flow_model(engine, model)
    mc = config.get("engine.flow.max_concurrent") if engine == "flow" else config.get("engine.grok.max_concurrent")
    db.run("INSERT INTO accounts(id,name,engine,port,profile,project_url,model,aspect,max_concurrent,enabled,extra,created)"
           " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
           (aid, name, engine, port, aid, project_url or "", model, "9:16", mc, 1, db.j(extra), db.now()))
    return get(aid)


def update(acc_id: str, **fields) -> dict:
    if is_virtual(acc_id):
        # 本地模型的「账号设置」：出片模式（像 Flow 账号选模型）、启用开关，存在设置里
        from .engines import studio as _st
        bad = [k for k in ("name", "project_url", "profile", "port") if fields.get(k) not in (None, "")]
        if bad:
            _no_virtual(acc_id, "改" + "、".join(bad))
        if "model" in fields or "quality" in fields:
            q = str(fields.get("quality") or fields.get("model") or "").strip().lower()
            if q not in _st.QUALITIES:
                raise OranteError("本地模型没有这个出片模式", "只能选 极速 / 均衡 / 高质 / 精品。")
            config.set("engine.studio.quality", q)
        if "enabled" in fields:
            v = fields.get("enabled")
            on = v not in (0, False, None, "", "0", "false", "False", "off")
            config.set("engine.studio.enabled", on)
            _STUDIO_HEALTH["at"] = 0.0
        if "max_concurrent" in fields:
            try:
                n = max(1, min(4, int(fields.get("max_concurrent") or 2)))
            except (TypeError, ValueError):
                n = 2
            config.set("engine.studio.max_inflight", n)
        return studio_account()          # extra（限流 / 探路记录等）本地模型没有，静默忽略
    allowed = {"name", "project_url", "model", "max_concurrent", "enabled", "extra", "profile", "port"}  # 规格 84：aspect 不再可改
    a = get(acc_id)
    if "model" in fields:
        _check_flow_model(a.get("engine") or "", fields.get("model") or "")
    sets, args = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "extra":
            v = db.j(v)
        sets.append(f"{k}=?")
        args.append(v)
    if sets:
        db.run(f"UPDATE accounts SET {', '.join(sets)} WHERE id=?", (*args, acc_id))
    return get(acc_id)


def delete(acc_id: str) -> None:
    _no_virtual(acc_id, "删除")
    used = db.one("SELECT COUNT(*) n FROM tasks WHERE account_id=? AND status NOT IN ('finished','trash')", (acc_id,))
    if used and used["n"]:
        raise OranteError("这个账号还有没完成的任务", "先把任务改到别的账号，或者移到回收。")
    db.run("DELETE FROM accounts WHERE id=?", (acc_id,))


def open_login(acc_id: str) -> dict:
    """拉起这个账号的 Chrome 让人登录（或直接到项目页）。"""
    _no_virtual(acc_id, "登录")
    a = get(acc_id)
    url = (a.get("project_url") or FLOW_HOME) if a["engine"] == "flow" else GROK_HOME
    fresh = chrome.launch(a["port"], _profile_dir(a), url)
    return {"ok": True, "port": a["port"], "url": url, "launched": fresh,
            "hint": "在弹出的 Chrome 里登录一次即可；以后不用再登。别关它，出片时要用。"}


def health(acc_id: str) -> dict:
    if is_virtual(acc_id):
        h = studio_health(force=True)
        ok = bool(h.get("ok") and h.get("comfy"))
        return {"ok": ok, "state": "ready" if ok else ("comfy_down" if h.get("ok") else "closed"),
                "note": "" if ok else ("ComfyUI 没开" if h.get("ok") else "本地模型没开"), "port": None}
    a = get(acc_id)
    port = a["port"]
    extra = dict(a.get("extra") or {})
    if not chrome.port_alive(port):
        out = {"ok": False, "state": "closed", "note": "Chrome 没开", "port": port}
        _attach_flow_flags(out, a, extra)
        return out
    pages = chrome.cdp_pages(port)
    urls = [p.get("url", "") for p in pages]
    if a["engine"] == "flow":
        on = [u for u in urls if "flow.google.com" in u or "labs.google" in u]
        login = any("accounts.google.com" in u for u in urls)
        state = "login" if login else ("ready" if on else "open")
    else:
        on = [u for u in urls if "grok.com" in u]
        state = "ready" if on else "open"
    out = {"ok": state == "ready", "state": state, "port": port, "pages": urls[:6]}
    if a["engine"] == "flow" and state == "ready" and (a.get("project_url") or ""):
        try:
            from .engines.flow_api import probe_account_project_page
            pp = probe_account_project_page(a)
            out["project_page"] = pp
            extra["project_page"] = pp
        except Exception as e:  # noqa: BLE001
            pp = extra.get("project_page")
            if pp:
                out["project_page"] = pp
            out["project_page_note"] = str(e)[:160]
    _attach_flow_flags(out, a, extra)
    return out


def _attach_flow_flags(out: dict, a: dict, extra: dict | None = None) -> None:
    extra = extra if extra is not None else dict(a.get("extra") or {})
    pp = extra.get("project_page")
    if pp and "project_page" not in out:
        out["project_page"] = pp
    left = throttle_remaining(a)
    if left > 0:
        out["throttled"] = True
        out["throttled_until"] = extra.get("throttled_until")
        out["throttled_left"] = int(left)
        out["throttled_left_min"] = max(1, int((left + 59) // 60))
    nt = network_throttle_state()
    if nt.get("on"):
        out["network_throttled"] = True


def patch_extra(acc_id: str, **fields) -> dict:
    """合并 extra 里的键，不把别的字段冲掉。"""
    if not acc_id:
        return {}
    if is_virtual(acc_id):
        return studio_account()          # 本地模型没有限流 / 探路这些记录，静默忽略
    a = get(acc_id)
    extra = dict(a.get("extra") or {})
    for k, v in fields.items():
        if v is None:
            extra.pop(k, None)
        else:
            extra[k] = v
    return update(acc_id, extra=extra)


def _parse_ts(s) -> float | None:
    if s is None or s == "":
        return None
    if isinstance(s, (int, float)):
        try:
            return float(s)
        except (TypeError, ValueError):
            return None
    raw = str(s).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw).timestamp()
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw[:19], fmt).timestamp()
        except Exception:
            continue
    return None


def _iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else datetime.now().timestamp()
                                  ).isoformat(timespec="seconds")


def throttle_remaining(acc) -> float:
    """冷却还剩几秒。0 = 没在冷却。acc 可以是 id 或账号 dict。"""
    if isinstance(acc, str):
        try:
            acc = get(acc)
        except OranteError:
            return 0.0
    extra = (acc or {}).get("extra") or {}
    until = _parse_ts(extra.get("throttled_until")) or extra.get("throttled_until_ts")
    try:
        until = float(until) if until is not None else None
    except (TypeError, ValueError):
        until = _parse_ts(until)
    if not until:
        return 0.0
    left = until - datetime.now().timestamp()
    return left if left > 0 else 0.0


def is_throttled(acc) -> bool:
    return throttle_remaining(acc) > 0


def network_throttle_state() -> dict:
    rec = config.get("flow.network_throttle") or {}
    if not isinstance(rec, dict):
        rec = {}
    until = _parse_ts(rec.get("until")) or rec.get("until_ts")
    try:
        until = float(until) if until is not None else 0.0
    except (TypeError, ValueError):
        until = 0.0
    now = datetime.now().timestamp()
    on = bool(until and until > now)
    left = max(0.0, (until or 0) - now) if on else 0.0
    return {
        "on": on,
        "until": rec.get("until") or "",
        "until_ts": until or 0,
        "left": left,
        "left_min": max(1, int((left + 59) // 60)) if on else 0,
        "accounts": list(rec.get("accounts") or []),
        "at": rec.get("at") or "",
        "reason": rec.get("reason") if isinstance(rec.get("reason"), dict) else {},
        "cleared_at": rec.get("cleared_at") or "",
        "cleared_by": rec.get("cleared_by") or "",
    }


def network_throttled() -> bool:
    return bool(network_throttle_state().get("on"))


def net_probe_due(*, every: float | None = None, now: float | None = None) -> bool:
    """全网暂停期间，到了该探一次路的时候。

    规格 94：全网暂停原来是死等 30 分钟，一次都不试——而这 30 分钟同样是 Orante 自己定的。
    和单账号一样：每隔几分钟真交一次，成功就当场解除。
    """
    st = network_throttle_state()
    if not st.get("on"):
        return False
    rec = config.get("flow.network_throttle") or {}
    if not isinstance(rec, dict):
        return False
    now = datetime.now().timestamp() if now is None else float(now)
    every = float(every if every is not None else THROTTLE_PROBE_EVERY)
    if every <= 0:
        return False
    last = _parse_ts(rec.get("probe_at")) or rec.get("probe_at_ts") or 0.0
    try:
        last = float(last)
    except (TypeError, ValueError):
        last = 0.0
    base = last or (_parse_ts(rec.get("at")) or 0.0)
    return (now - base) >= every


def note_net_probe(*, now: float | None = None) -> None:
    rec = config.get("flow.network_throttle")
    if not isinstance(rec, dict) or not rec:
        return
    now = datetime.now().timestamp() if now is None else float(now)
    rec = dict(rec)
    rec["probe_at"] = _iso(now)
    rec["probe_at_ts"] = now
    config.set("flow.network_throttle", rec, touch_rev=False)


def mark_network_throttle(acc_ids: list[str] | None = None,
                          *, reason: dict | None = None) -> dict:
    now = datetime.now().timestamp()
    rec = {
        "until": _iso(now + NETWORK_THROTTLE_PAUSE),
        "until_ts": now + NETWORK_THROTTLE_PAUSE,
        "accounts": list(acc_ids or []),
        "at": _iso(now),
        "reason": dict(reason or {}),
    }
    config.set("flow.network_throttle", rec, touch_rev=False)
    return rec


def mark_throttled(acc_id: str, *, now: float | None = None,
                   reason: dict | None = None) -> dict:
    """记冷却。第一次 20 分钟；冷却结束后 30 分钟内再被限 → 翻倍，上限 2 小时。

    reason 是这次到底为什么被限：{code, rpc, err_n, raw, seg}。规格 91 起必须传，
    没有它界面上只能写"账号限流中"，用户无从判断是不是误判。

    若 10 分钟内又有别的 Flow 账号被限、凑够 NETWORK_THROTTLE_ACCOUNTS 个 → 记全局网络限流。
    """
    a = get(acc_id)
    extra = dict(a.get("extra") or {})
    now = datetime.now().timestamp() if now is None else float(now)
    prev_min = int(extra.get("throttle_minutes") or (THROTTLE_FIRST // 60))
    last_until = _parse_ts(extra.get("throttled_until")) or extra.get("throttled_until_ts")
    try:
        last_until = float(last_until) if last_until is not None else 0.0
    except (TypeError, ValueError):
        last_until = 0.0
    # 已经在冷却中：不要把档位再翻一倍，沿用原来的 until
    if last_until and now < last_until:
        return a
    # 冷却期结束后 30 分钟内又被限 → 翻倍；否则从 20 分钟起
    if last_until and now < last_until + THROTTLE_DOUBLE_WINDOW:
        minutes = min(max(prev_min, THROTTLE_FIRST // 60) * 2, THROTTLE_CAP // 60)
    else:
        minutes = THROTTLE_FIRST // 60
    until = now + minutes * 60
    extra["throttled_until"] = _iso(until)
    extra["throttled_until_ts"] = until
    extra["throttle_minutes"] = minutes
    extra["last_throttled_at"] = _iso(now)
    extra["throttle_success"] = 0
    extra["throttle_times"] = int(extra.get("throttle_times") or 0) + 1
    extra.pop("throttle_probe_at", None)          # 新一轮冷却，探路计时重新开始
    rec = dict(reason or {})
    rec.update({"at": _iso(now), "at_ts": now, "minutes": minutes})
    extra["throttle_reason"] = rec
    hist = list(extra.get("throttle_log") or [])
    hist.append(rec)
    extra["throttle_log"] = hist[-5:]             # 只留最近 5 次，够看规律了
    updated = update(acc_id, extra=extra)

    last = config.get("flow.last_throttle") or {}
    if not isinstance(last, dict):
        last = {}
    last_id = last.get("id") or ""
    last_at = _parse_ts(last.get("at")) or last.get("at_ts") or 0
    try:
        last_at = float(last_at)
    except (TypeError, ValueError):
        last_at = 0.0
    recent = [x for x in (last.get("recent") or [])
              if isinstance(x, dict) and (now - float(x.get("at_ts") or 0)) < NETWORK_THROTTLE_WINDOW]
    if last_id and last_id != acc_id and last_at and (now - last_at) < NETWORK_THROTTLE_WINDOW:
        recent.append({"id": last_id, "at_ts": last_at})
    recent.append({"id": acc_id, "at_ts": now})
    ids = []
    for x in recent:
        if x.get("id") and x["id"] not in ids:
            ids.append(x["id"])
    if len(ids) >= NETWORK_THROTTLE_ACCOUNTS:
        mark_network_throttle(ids, reason=rec)
    config.set("flow.last_throttle",
               {"id": acc_id, "at": _iso(now), "at_ts": now, "recent": recent[-6:]},
               touch_rev=False)
    return updated


def clear_throttle(acc_id: str, *, who: str = "user", also_network: bool = True) -> dict:
    """手动 / 探路成功之后解除冷却。

    who="user"：用户在界面上点的「现在就试」；who="probe"：Orante 自己探路成功。
    冷却档位也一并清回 20 分钟——既然现在能用，就别拿上一次的惩罚继续压着。
    """
    a = get(acc_id)
    extra = dict(a.get("extra") or {})
    for k in ("throttled_until", "throttled_until_ts", "throttle_probe_at"):
        extra.pop(k, None)
    extra["throttle_minutes"] = THROTTLE_FIRST // 60
    extra["throttle_success"] = 0
    extra["throttle_cleared_at"] = _iso()
    extra["throttle_cleared_by"] = who
    out = update(acc_id, extra=extra)
    if also_network:
        clear_network_throttle(who=who)
    return out


def clear_network_throttle(*, who: str = "user") -> dict:
    config.set("flow.network_throttle",
               {"cleared_at": _iso(), "cleared_by": who}, touch_rev=False)
    config.set("flow.last_throttle", {}, touch_rev=False)
    return network_throttle_state()


def probe_due(acc, *, every: float | None = None, now: float | None = None) -> bool:
    """冷却中，但到了该"探一次路"的时候。

    Google 那边常常早就放行了（浏览器里能正常生成），冷却却是 Orante 自己定的时长。
    所以冷却期间每隔几分钟真的提交一次：成功就立刻解除，失败就继续等。
    """
    if isinstance(acc, str):
        try:
            acc = get(acc)
        except OranteError:
            return False
    if throttle_remaining(acc) <= 0:
        return False
    extra = (acc or {}).get("extra") or {}
    now = datetime.now().timestamp() if now is None else float(now)
    every = float(every if every is not None else THROTTLE_PROBE_EVERY)
    if every <= 0:
        return False
    last = _parse_ts(extra.get("throttle_probe_at")) or extra.get("throttle_probe_at_ts")
    try:
        last = float(last) if last is not None else 0.0
    except (TypeError, ValueError):
        last = 0.0
    started = _parse_ts(extra.get("last_throttled_at")) or 0.0
    base = last or started or 0.0
    return (now - base) >= every


def note_probe(acc_id: str, *, now: float | None = None) -> dict | None:
    """记下"这一次探路是什么时候发的"，免得每一轮都去撞。"""
    try:
        a = get(acc_id)
    except OranteError:
        return None
    extra = dict(a.get("extra") or {})
    now = datetime.now().timestamp() if now is None else float(now)
    extra["throttle_probe_at"] = _iso(now)
    extra["throttle_probe_at_ts"] = now
    return update(acc_id, extra=extra)


def throttle_info(acc) -> dict:
    """一份给人看的冷却说明：谁、为什么、到什么时候、这个时长是谁定的。"""
    if isinstance(acc, str):
        try:
            acc = get(acc)
        except OranteError:
            return {}
    extra = (acc or {}).get("extra") or {}
    left = throttle_remaining(acc)
    rec = extra.get("throttle_reason") if isinstance(extra.get("throttle_reason"), dict) else {}
    return {
        "account_id": (acc or {}).get("id") or "",
        "account_name": (acc or {}).get("name") or "",
        "on": left > 0,
        "left": left,
        "left_min": max(1, int((left + 59) // 60)) if left > 0 else 0,
        "minutes": int(extra.get("throttle_minutes") or (THROTTLE_FIRST // 60)),
        "times": int(extra.get("throttle_times") or 0),
        "code": rec.get("code") or "",
        "rpc": rec.get("rpc") or "",
        "err_n": rec.get("err_n"),
        "raw": (rec.get("raw") or "")[:300],
        "seg": rec.get("seg") or "",
        "at": rec.get("at") or extra.get("last_throttled_at") or "",
        "submitted_before": rec.get("submitted_before"),
        "since_last_sec": rec.get("since_last_sec"),
        "first_shot": _first_shot(rec),
        "probe_at": extra.get("throttle_probe_at") or "",
        "cleared_at": extra.get("throttle_cleared_at") or "",
        "cleared_by": extra.get("throttle_cleared_by") or "",
    }


FIRST_SHOT_GAP = 10 * 60   # 离上一发超过这么久、且这轮还没成功交过 → 算"第一发就被拒"


def _first_shot(rec: dict) -> bool | None:
    """这次被拒是不是"这轮第一发就被拒"（不是连打打出来的）。信息不够返回 None。"""
    if not isinstance(rec, dict) or "submitted_before" not in rec:
        return None
    try:
        n = int(rec.get("submitted_before") or 0)
    except (TypeError, ValueError):
        return None
    if n > 0:
        return False
    since = rec.get("since_last_sec")
    if since is None:
        return True
    try:
        return int(since) >= FIRST_SHOT_GAP
    except (TypeError, ValueError):
        return True


def first_shot_lines(info: dict) -> list[str]:
    """规格 95：第一发就被拒 ≠ 提交太快。把可能的病因和验证办法写出来。"""
    if not info or info.get("first_shot") is not True:
        return []
    if (info.get("code") or "") != "PUBLIC_ERROR_UNUSUAL_ACTIVITY":
        return []
    return [
        "     · 注意：这轮第一发就被拒了，不是提交太快。多半是下面两种之一：",
        "       ① Google 判这个专用 Chrome 环境「异常」（刚装好的空白浏览器最常见，reCAPTCHA 分数低）。"
        "验证：到「账号」点「打开」，在那个专用 Chrome 里手动生成一条；能出片 → 回来点「现在就试」",
        "       ② 账号没有无限模型资格（Google 新规：只有 Ultra 本人，家庭组成员不算）。"
        "验证：在 Flow 网页的模型下拉里看有没有「Veo 3.1 - Lite [Lower Priority]」；没有就换账号",
    ]


def throttle_lines(acc, *, probe_every: float | None = None) -> list[str]:
    """写进作业日志的那几行。把"为什么"和"这个时长谁定的"讲清楚。"""
    info = throttle_info(acc)
    if not info:
        return []
    every = int((probe_every if probe_every is not None else THROTTLE_PROBE_EVERY) // 60)
    code = info["code"] or "未知错误码"
    rpc = f"，接口 {info['rpc']}" if info["rpc"] else ""
    errn = f"，err={info['err_n']}" if info.get("err_n") is not None else ""
    out = [
        f"  ⏸ 账号「{info['account_name']}」冷却中 · 还剩 {info['left_min']} 分钟",
        f"     · 起因：{info['at']} 提交时 Google 返回 {code}{rpc}{errn}"
        + (f"（{info['seg']}）" if info["seg"] else ""),
        f"     · 这 {info['minutes']} 分钟是 Orante 自己定的冷却（第 {info['times']} 次受限，"
        f"20 分钟起、连着再犯翻倍），不是 Google 说要等这么久",
    ]
    out.extend(first_shot_lines(info))
    if every > 0:
        out.append(f"     · 冷却期间每 {every} 分钟会真的试提交一次；一次成功就立刻解除")
    out.append("     · 浏览器里能正常生成？到 ④ 生成 顶部点「现在就试」，立刻解除冷却继续出片")
    return out


def note_flow_success(acc_id: str) -> dict | None:
    """冷却过后连续成功提交 5 段 → 冷却档位清回 20 分钟。"""
    if not acc_id:
        return None
    try:
        a = get(acc_id)
    except OranteError:
        return None
    if is_throttled(a):
        return a
    extra = dict(a.get("extra") or {})
    n = int(extra.get("throttle_success") or 0) + 1
    extra["throttle_success"] = n
    if n >= THROTTLE_SUCCESS_RESET:
        extra["throttle_minutes"] = THROTTLE_FIRST // 60
        extra["throttle_success"] = 0
        extra.pop("last_throttled_at", None)
        extra.pop("throttled_until", None)
        extra.pop("throttled_until_ts", None)
    return update(acc_id, extra=extra)


def flow_alts(except_id: str | None = None) -> list[dict]:
    """同引擎、未在冷却、已登录、能换过去的 Flow 账号。"""
    out = []
    for a in list_all(probe=False):
        if a.get("engine") != "flow":
            continue
        if except_id and a.get("id") == except_id:
            continue
        if not a.get("enabled", 1):
            continue
        if is_throttled(a):
            continue
        if (a.get("login_state") or "") == "need_relogin":
            continue
        out.append(a)
    return out


def use_project_url(acc_id: str) -> dict:
    """用户点了「改成项目地址」：去掉 /collection/<uuid>。没点不改。"""
    from .engines.flow_api import strip_collection_url
    a = get(acc_id)
    url = a.get("project_url") or ""
    new = strip_collection_url(url)
    if new == url:
        return a
    extra = dict(a.get("extra") or {})
    pp = dict(extra.get("project_page") or {})
    if pp:
        pp["ok"] = True
        pp.pop("why", None)
        pp["at"] = datetime.now().isoformat(timespec="seconds")
        extra["project_page"] = pp
    return update(acc_id, project_url=new, extra=extra)


def import_legacy_lanes() -> list[dict]:
    """把开发机上 0-长文案\\lanes.json 里的泳道导进来当账号（沿用原 profile，不用重登）。"""
    root = paths.find_legacy_root()
    if not root:
        raise OranteError("这台机器上没有老流水线目录", "分发版不需要这一步。")
    lanes_file = root / "0-长文案" / "lanes.json"
    if not lanes_file.is_file():
        raise OranteError("找不到 lanes.json")
    lanes = json.loads(lanes_file.read_text(encoding="utf-8"))
    added = []
    for lane, rec in lanes.items():
        if rec.get("disabled"):
            continue
        name = lane
        if db.one("SELECT 1 FROM accounts WHERE name=?", (name,)):
            continue
        prof = root / "Chrome_Profile" / rec["chrome_profile"]
        aid = db.next_id("account", "A", 3)
        db.run("INSERT INTO accounts(id,name,engine,port,profile,project_url,model,aspect,max_concurrent,enabled,extra,created)"
               " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
               (aid, name, rec["engine"], int(rec["port"]), str(prof), rec.get("project_url", ""),
                rec.get("model", ""), rec.get("aspect", "9:16"), int(rec.get("max_concurrent", 5) or 5), 1,
                db.j({"legacy_lane": lane, "account": rec.get("account")}), db.now()))
        added.append(get(aid))
    return added
