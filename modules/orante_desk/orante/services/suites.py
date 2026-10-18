# -*- coding: utf-8 -*-
"""模板套装：本机内一键换整套风格。

序列化全走 preset.collect_suite_group / apply_suite_group / export_payload。
这里只管家（建套装、切换、拦活跃 job），不自己拼分组 JSON。
账号登录态目录一个字节都不许动。
"""
from __future__ import annotations

from . import preset

from .. import config, db
from ..errors import OranteError

FACTORY_ID = "_factory"
BUILTIN_NAME = "默认套装"
BUILTIN_NOTE = "出厂的那一套"
DEFAULT_COLOR = preset.SUITE_COLORS[0]


def ensure_builtin() -> dict:
    """表空就用当前配置种一份出厂套装。已经有套装则只补 suite.current。"""
    db.conn().execute(
        "CREATE TABLE IF NOT EXISTS suites("
        "id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, note TEXT, color TEXT,"
        "builtin INTEGER DEFAULT 0, sort INTEGER DEFAULT 0, used_at TEXT,"
        "created TEXT, updated TEXT)")
    db.conn().execute(
        "CREATE TABLE IF NOT EXISTS suite_data("
        "suite_id TEXT NOT NULL, grp TEXT NOT NULL, payload TEXT,"
        "PRIMARY KEY(suite_id, grp))")
    db.conn().commit()
    n = db.one("SELECT COUNT(*) n FROM suites WHERE id!=?", (FACTORY_ID,))
    if not n or int(n["n"] or 0) == 0:
        return _seed_builtin()
    cur = str(config.get("suite.current") or "")
    if cur and db.one("SELECT 1 FROM suites WHERE id=?", (cur,)):
        return get(cur)
    hit = db.one("SELECT id FROM suites WHERE builtin=1 AND id!=? ORDER BY id",
                 (FACTORY_ID,))
    if not hit:
        hit = db.one("SELECT id FROM suites WHERE id!=? ORDER BY sort, id",
                     (FACTORY_ID,))
    sid = (hit or {}).get("id") or ""
    if sid:
        config.set("suite.current", sid)
        return get(sid)
    return _seed_builtin()


def _seed_builtin() -> dict:
    now = db.now()
    sid = db.next_id("suite", "S", 3)
    db.run(
        "INSERT INTO suites(id,name,note,color,builtin,sort,used_at,created,updated)"
        " VALUES(?,?,?,?,1,1,?,?,?)",
        (sid, BUILTIN_NAME, BUILTIN_NOTE, DEFAULT_COLOR, now, now, now))
    _write_groups(sid, list(preset.SUITE_GROUPS))
    _copy_groups(sid, FACTORY_ID)
    config.set("suite.current", sid)
    return get(sid)


def _write_groups(sid: str, groups: list[str], *, snapshot: bool = False) -> None:
    """把当前配置（或已有套装的拷贝）写入 suite_data。"""
    now = db.now()
    for g in groups:
        payload = preset.collect_suite_group(g)
        db.run(
            "INSERT OR REPLACE INTO suite_data(suite_id,grp,payload) VALUES(?,?,?)",
            (sid, g, db.j(payload)))
    if not snapshot and sid != FACTORY_ID:
        db.run("UPDATE suites SET updated=? WHERE id=?", (now, sid))


def _copy_groups(src: str, dst: str) -> None:
    _copy_selected(src, dst, None)


def _copy_selected(src: str, dst: str, want: list[str] | None) -> None:
    """把 src 的分组 JSON 原样拷到 dst。独立副本，不是引用。"""
    db.run("DELETE FROM suite_data WHERE suite_id=?", (dst,))
    allow = set(want) if want else None
    for r in db.rows("SELECT grp,payload FROM suite_data WHERE suite_id=?", (src,)):
        if allow is not None and r["grp"] not in allow:
            continue
        db.run(
            "INSERT INTO suite_data(suite_id,grp,payload) VALUES(?,?,?)",
            (dst, r["grp"], r["payload"]))


def _ensure_factory_data() -> None:
    """出厂快照丢了才按当前活配置重种，只写 _factory，不动别的套装。"""
    if db.one("SELECT 1 FROM suite_data WHERE suite_id=?", (FACTORY_ID,)):
        return
    snap = db.one("SELECT 1 FROM suites WHERE id=?", (FACTORY_ID,))
    if not snap:
        now = db.now()
        db.run(
            "INSERT INTO suites(id,name,note,color,builtin,sort,created,updated)"
            " VALUES(?,?,?,?,2,0,?,?)",
            (FACTORY_ID, "_factory", "", DEFAULT_COLOR, now, now))
    _write_groups(FACTORY_ID, list(preset.SUITE_GROUPS), snapshot=True)


def _fill_from(sid: str, src: str, want: list[str]) -> None:
    """按 from 来源写入套装内容。current=活配置，factory=出厂快照，其它=某套装 id。"""
    src = (src or "current").strip() or "current"
    if src == "current":
        _write_groups(sid, want)
        return
    if src in ("factory", FACTORY_ID):
        _ensure_factory_data()
        _copy_selected(FACTORY_ID, sid, want)
        return
    get(src)
    _copy_selected(src, sid, want)


def _groups_of(sid: str) -> dict:
    out = {}
    for r in db.rows("SELECT grp,payload FROM suite_data WHERE suite_id=?", (sid,)):
        out[r["grp"]] = db.unj(r["payload"])
    return out


def _item(row: dict) -> dict:
    sid = row["id"]
    groups = _groups_of(sid)
    return {
        "id": sid,
        "name": row.get("name") or "",
        "note": row.get("note") or "",
        "color": row.get("color") or DEFAULT_COLOR,
        "builtin": int(row.get("builtin") or 0),
        "sort": int(row.get("sort") or 0),
        "counts": preset.suite_counts(groups),
        "used_at": row.get("used_at") or "",
        "created": row.get("created") or "",
        "updated": row.get("updated") or "",
        "groups": sorted(groups.keys()),
    }


def current_id() -> str:
    ensure_builtin()
    return str(config.get("suite.current") or "")


def get(sid: str) -> dict:
    r = db.one("SELECT * FROM suites WHERE id=?", (sid,))
    if not r or sid == FACTORY_ID:
        raise OranteError(f"没有这个套装：{sid}")
    return _item(r)


def list_all() -> dict:
    ensure_builtin()
    rows = db.rows(
        "SELECT * FROM suites WHERE id!=? ORDER BY sort, created, id", (FACTORY_ID,))
    items = [_item(r) for r in rows]
    cur = str(config.get("suite.current") or "")
    if cur and not any(x["id"] == cur for x in items) and items:
        cur = items[0]["id"]
        config.set("suite.current", cur)
    return {
        "current": cur,
        "items": items,
        "colors": list(preset.SUITE_COLORS),
        "autosave_on_switch": bool(config.get("suite.autosave_on_switch", True)),
        "groups": [
            {"id": g, "on": True} for g in preset.SUITE_GROUPS
        ],
    }


def _unique_name(name: str, exclude: str = "") -> str:
    name = (name or "").strip()
    if not name:
        raise OranteError("套装要有名字")
    q = db.one("SELECT id FROM suites WHERE name=? AND id!=?", (name, exclude or ""))
    if q:
        raise OranteError(f"已经有叫「{name}」的套装")
    return name


def _color(v: str | None) -> str:
    c = (v or "").strip() or DEFAULT_COLOR
    if c.upper() not in {x.upper() for x in preset.SUITE_COLORS}:
        # 允许用户色卡里的大小写；不在表里就退回默认
        if not (c.startswith("#") and len(c) in (4, 7)):
            return DEFAULT_COLOR
    for x in preset.SUITE_COLORS:
        if x.upper() == c.upper():
            return x
    return c if c.startswith("#") else DEFAULT_COLOR


def _next_sort() -> int:
    r = db.one("SELECT MAX(sort) n FROM suites WHERE id!=?", (FACTORY_ID,))
    return int((r or {}).get("n") or 0) + 1


def create(name: str, note: str = "", color: str = "",
           groups: list[str] | None = None, *, from_: str = "current",
           adopt: bool = False) -> dict:
    """新建套装。from_：current=当前活配置 / factory=出厂快照 / 某套装 id。
    默认不切换（前端建完再调 switch）。adopt=True 只改当前指针，不应用内容。
    """
    ensure_builtin()
    name = _unique_name(name)
    want = [g for g in (groups or list(preset.SUITE_GROUPS)) if g in preset.SUITE_GROUPS]
    if not want:
        want = list(preset.SUITE_GROUPS)
    sid = db.next_id("suite", "S", 3)
    now = db.now()
    db.run(
        "INSERT INTO suites(id,name,note,color,builtin,sort,used_at,created,updated)"
        " VALUES(?,?,?,?,0,?,?,?,?)",
        (sid, name, (note or "").strip(), _color(color), _next_sort(),
         now if adopt else "", now, now))
    _fill_from(sid, from_, want)
    if adopt:
        config.set("suite.current", sid)
    return get(sid)


def save_current(sid: str) -> dict:
    """把当前配置写回这个套装已有的组。切换前自动调的就是它。"""
    item = get(sid)
    groups = item.get("groups") or list(preset.SUITE_GROUPS)
    if not groups:
        groups = list(preset.SUITE_GROUPS)
    _write_groups(sid, groups)
    return get(sid)


def is_dirty(sid: str) -> bool:
    item = get(sid)
    stored = _groups_of(item["id"])
    for g, payload in stored.items():
        live = preset.collect_suite_group(g)
        if not preset.same_payload(live, payload):
            return True
    return False


def _apply(sid: str) -> tuple[list[str], list[str]]:
    skipped: list[str] = []
    reloaded: list[str] = []
    for g in preset.SUITE_GROUPS:
        row = db.one(
            "SELECT payload FROM suite_data WHERE suite_id=? AND grp=?", (sid, g))
        if not row:
            continue
        r = preset.apply_suite_group(g, db.unj(row["payload"]))
        reloaded.append(g)
        for a in (r.get("skipped_accounts") or []):
            if a not in skipped:
                skipped.append(a)
    return skipped, reloaded


def switch(sid: str, save=None) -> dict:
    """切到 sid。生成进行中也可以切：窗口和套装互不拦。

    同一账号同时只跑一个生成，由账号锁排队，不在这里拦。
    另一个窗口用另一套装、另一个账号接着生成，是允许的。
    autosave 开则先存回当前；关且有改动则返回要确认。
    """
    ensure_builtin()
    target = get(sid)
    cur = str(config.get("suite.current") or "")
    autosave = bool(config.get("suite.autosave_on_switch", True))
    if cur and cur != sid and db.one("SELECT 1 FROM suites WHERE id=?", (cur,)):
        dirty = is_dirty(cur)
        # 显式 save=False 连 autosave 开着也跳过（另存为「原套装回滚」要用）
        if save is False:
            pass
        elif autosave or save is True:
            save_current(cur)
        elif dirty:
            cur_row = db.one("SELECT name FROM suites WHERE id=?", (cur,))
            raise OranteError(
                "当前套装有改动",
                "关掉了「切换时自动保存」，要先决定这批改动存不存回去。",
                code="dirty",
                extra={
                    "needs_save": True,
                    "current": {"id": cur, "name": (cur_row or {}).get("name") or cur},
                    "target": {"id": target["id"], "name": target["name"]},
                },
            )
    skipped, reloaded = _apply(sid)
    now = db.now()
    db.run("UPDATE suites SET used_at=?, updated=? WHERE id=?", (now, now, sid))
    config.set("suite.current", sid)
    config.bump_rev(f"切到了「{target['name']}」")
    return {
        "ok": True,
        "current": sid,
        "name": target["name"],
        "color": target["color"],
        "skipped_accounts": skipped,
        "reloaded": reloaded,
    }


def patch(sid: str, *, name=None, note=None, color=None) -> dict:
    item = get(sid)
    fields = {}
    if name is not None:
        fields["name"] = _unique_name(name, exclude=sid)
    if note is not None:
        fields["note"] = str(note)
    if color is not None:
        fields["color"] = _color(color)
    if not fields:
        return item
    sets = ", ".join(f"{k}=?" for k in fields)
    db.run(
        f"UPDATE suites SET {sets}, updated=? WHERE id=?",
        (*fields.values(), db.now(), sid))
    return get(sid)


def duplicate(sid: str) -> dict:
    src = get(sid)
    base = f"{src['name']} 副本"
    name = base
    n = 2
    while db.one("SELECT 1 FROM suites WHERE name=?", (name,)):
        name = f"{base} {n}"
        n += 1
    nid = db.next_id("suite", "S", 3)
    now = db.now()
    db.run(
        "INSERT INTO suites(id,name,note,color,builtin,sort,used_at,created,updated)"
        " VALUES(?,?,?,?,0,?,?,?,?)",
        (nid, name, src.get("note") or "", src.get("color") or DEFAULT_COLOR,
         _next_sort(), "", now, now))
    _copy_groups(sid, nid)
    return get(nid)


def restore_builtin(sid: str) -> dict:
    item = get(sid)
    if not item.get("builtin"):
        raise OranteError("只有出厂套装能恢复出厂内容")
    _ensure_factory_data()
    _copy_groups(FACTORY_ID, sid)
    db.run("UPDATE suites SET updated=? WHERE id=?", (db.now(), sid))
    if str(config.get("suite.current") or "") == sid:
        _apply(sid)
    return get(sid)


def delete(sid: str) -> dict:
    item = get(sid)
    if item.get("builtin"):
        raise OranteError("出厂套装不能删", "改乱了可以恢复出厂内容。")
    if str(config.get("suite.current") or "") == sid:
        raise OranteError("正在用的套装不能删", "先切到别的套装再删。")
    db.run("DELETE FROM suite_data WHERE suite_id=?", (sid,))
    db.run("DELETE FROM suites WHERE id=?", (sid,))
    return {"id": sid}


def export_suite(sid: str, password: str, path: str | None) -> dict:
    item = get(sid)
    groups = _groups_of(sid)
    payload = preset.preset_payload_from_suite_groups(groups)
    return preset.export_payload(payload, password, path)


def import_suite(path: str, password: str, name: str = "") -> dict:
    ensure_builtin()
    _header, payload = preset.load_preset(path, password)
    groups = preset.suite_groups_from_preset_payload(payload)
    if not groups:
        raise OranteError("这份预设里没有能当成套装的内容",
                          "至少要有提示词、覆盖库、换图卡片或生成参数。")
    want_name = (name or "").strip()
    if not want_name:
        meta = _header or {}
        want_name = f"导入 {(meta.get('machine') or '预设').strip()}"
        if db.one("SELECT 1 FROM suites WHERE name=?", (want_name,)):
            want_name = f"{want_name} {db.now()}"
    want_name = _unique_name(want_name)
    sid = db.next_id("suite", "S", 3)
    now = db.now()
    db.run(
        "INSERT INTO suites(id,name,note,color,builtin,sort,used_at,created,updated)"
        " VALUES(?,?,?,?,0,?,?,?,?)",
        (sid, want_name, "", DEFAULT_COLOR, _next_sort(), "", now, now))
    for g, data in groups.items():
        if g not in preset.SUITE_GROUPS:
            continue
        db.run(
            "INSERT OR REPLACE INTO suite_data(suite_id,grp,payload) VALUES(?,?,?)",
            (sid, g, db.j(data)))
    return get(sid)
