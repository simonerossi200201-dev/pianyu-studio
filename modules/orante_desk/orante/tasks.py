# -*- coding: utf-8 -*-
"""任务表（SQLite tasks）——任务面板那张表的后端。段状态不在这里，在各自 manifest。"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import threading
import time
from pathlib import Path

from . import db, accounts, paths
from .errors import OranteError
from .models import MANIFEST, Manifest

COLUMNS = ["name", "copy", "image_source", "type", "account", "duration_pref", "gen_mode"]
TYPE_ALIASES = {"长文案": "long", "long": "long", "长": "long", "l": "long",
                "短文案": "short", "short": "short", "短": "short", "normal": "short", "s": "short"}
STATUS_ORDER = ["draft", "prepared", "described", "arranged", "generating", "collected", "finished", "trash"]


def _acc_by_name(name: str) -> dict | None:
    name = (name or "").strip()
    if not name:
        return None
    if accounts.is_studio_name(name):
        return accounts.studio_account()    # 规格 98：本地模型也能当任务账号（虚拟，不在库里）
    r = db.one("SELECT * FROM accounts WHERE name=? OR id=?", (name, name))
    return r


def _decorate(t: dict) -> dict:
    """补 archived / 缺目录标记，meta 解成 dict。list_all 和 get 共用。"""
    t["meta"] = db.unj(t.get("meta"), {}) or {}
    t["archived"] = int(t.get("archived") or 0)
    t["gen_mode"] = t.get("gen_mode") or ""
    rv = t.get("run_to_completion")
    if rv is None:
        t["run_to_completion"] = 1
    else:
        try:
            t["run_to_completion"] = 0 if int(rv) == 0 else 1
        except (TypeError, ValueError):
            t["run_to_completion"] = 1
    meta = t["meta"] if isinstance(t["meta"], dict) else {}
    t["missing_dir"] = bool(meta.get("missing_dir"))
    t["changed_files"] = list(meta.get("changed_files") or [])
    if not t["missing_dir"] and t.get("dir") and not Path(t["dir"]).is_dir():
        t["missing_dir"] = True
    return t


# manifest counts 缓存：key = 规范化路径，值 = ((mtime_ns, size), counts)
_counts_lock = threading.Lock()
_counts_cache: dict[str, tuple] = {}

# seg.changed 节流：同一任务 500ms 内最多一条；收尾 force 绕过
SEG_EMIT_GAP = 0.5
_seg_lock = threading.Lock()
_seg_last: dict[str, float] = {}
_seg_timer: dict[str, threading.Timer] = {}
_seg_payload: dict[str, dict] = {}


def _manifest_path(task_dir) -> Path:
    return Path(task_dir) / MANIFEST


def _cache_key(task_dir) -> str:
    return os.path.normpath(str(_manifest_path(task_dir)))


def _mtime_sig(st) -> int:
    ns = getattr(st, "st_mtime_ns", None)
    if ns is not None:
        return int(ns)
    return int(st.st_mtime * 1e9)


def decorate_counts(m: Manifest) -> dict:
    """段计数 + images_ready。生成面板折叠行只靠这份，不拉整份 manifest。"""
    c = m.counts()
    segs = m.segments or []
    # 每一段都有配图 = 编排其实已经应用过；状态若还停在 prepared/described，
    # 生成面板靠这个把任务兜底列出来，免得无声消失。
    c["images_ready"] = bool(segs) and all(str(sg.get("image") or "").strip() for sg in segs)
    # ④ 生成行尾「缺 N 张描述语」靠这份，不拉整份图列表
    try:
        from .services import simple86 as _s86
        if not _s86.has_copy(m):
            # 没文案时空镜也要描述语，否则生成行上看不到「去填」。
            miss = _s86.missing_desc_for_no_copy(m, m)
        else:
            from .services.describe import missing_subject_ps
            miss = missing_subject_ps(m)
    except Exception:
        miss = [im.get("p") for im in (m.images or [])
                if im.get("role") == "主体"
                and (im.get("status") or "") not in ("pending_review", "missing")
                and not str(im.get("desc") or "").strip()]
    c["desc_missing"] = len(miss)
    c["desc_missing_ps"] = miss[:12]
    try:
        from .services import pipeline
        c["mode"] = pipeline.mode_of(m)
        cells, waits, roles = [], [], []
        for sg in segs:
            roles.append(sg.get("role") or "")
            st = pipeline.cell_status(m, sg)
            cells.append(st)
            if st == "waiting":
                try:
                    waits.append(int(sg["s"]))
                except (TypeError, ValueError, KeyError):
                    pass
        c["cells"] = cells
        c["waiting"] = len(waits)
        c["waits"] = waits
        c["roles"] = roles
        try:
            from .services import bench
            c.update(bench.counts_of(m))
        except Exception:
            c.setdefault("bench_red", 0)
            c.setdefault("bench_yellow", 0)
            c.setdefault("bench_green", 0)
    except Exception:
        c.setdefault("mode", getattr(m, "mode", None) or "first_frame")
        c.setdefault("waiting", 0)
        c.setdefault("cells", [])
        c.setdefault("waits", [])
        c.setdefault("roles", [])
        c.setdefault("bench_red", 0)
        c.setdefault("bench_yellow", 0)
        c.setdefault("bench_green", 0)
    return c


def publish_counts(task_id: str, task_dir, manifest) -> None:
    """描述语写盘后，把「缺几张描述语」推给生成页。推失败不影响已经存好的内容。"""
    try:
        c = remember_counts(task_dir, manifest)
        emit_seg_changed(str(task_id), c, force=True)
    except Exception:
        pass


def remember_counts(task_dir, m: Manifest) -> dict:
    """写盘后把 counts 塞进缓存，list_all 就不用立刻再读一遍 JSON。"""
    c = decorate_counts(m)
    p = _manifest_path(task_dir)
    try:
        st = p.stat()
        with _counts_lock:
            _counts_cache[_cache_key(task_dir)] = ((_mtime_sig(st), st.st_size), dict(c))
    except OSError:
        pass
    return c


def clear_counts_cache() -> None:
    with _counts_lock:
        _counts_cache.clear()


def _counts_from_manifest_file(task_dir) -> dict | None:
    p = _manifest_path(task_dir)
    try:
        st = p.stat()
    except OSError:
        return None
    sig = (_mtime_sig(st), st.st_size)
    key = _cache_key(task_dir)
    with _counts_lock:
        hit = _counts_cache.get(key)
        if hit and hit[0] == sig:
            return dict(hit[1])
    try:
        m = Manifest.load(Path(task_dir))
        c = decorate_counts(m)
    except Exception:
        return None
    with _counts_lock:
        _counts_cache[key] = (sig, dict(c))
    return c


def list_all(include_finished=False) -> list[dict]:
    rows = db.rows("SELECT * FROM tasks ORDER BY row_order, created")
    out = []
    for t in rows:
        t = _decorate(t)
        if not include_finished:
            if t["status"] in ("finished", "trash") or t["archived"]:
                continue
        t["counts"] = _counts(t)
        out.append(t)
    return out


def _counts(t: dict) -> dict | None:
    # 归档任务优先用工程文件里的摘要，打开过时档时不用逐个读 manifest。
    meta = t.get("meta") if isinstance(t.get("meta"), dict) else {}
    if int(t.get("archived") or 0):
        arch = (meta or {}).get("archive") or {}
        c = arch.get("counts")
        if isinstance(c, dict) and c:
            out = dict(c)
            out.setdefault("images_ready", True)
            return out
    if not t.get("dir"):
        return None
    if not Path(t["dir"]).is_dir():
        return None
    return _counts_from_manifest_file(t["dir"])


def counts_of(task_or_id) -> dict | None:
    """单任务 counts。给 /api/tasks/{id}/counts 和 Job 收尾广播用。"""
    t = task_or_id if isinstance(task_or_id, dict) else get(task_or_id)
    return _counts(t)


def _flush_seg(task_id: str) -> None:
    with _seg_lock:
        _seg_timer.pop(task_id, None)
        c = _seg_payload.pop(task_id, None)
        _seg_last[task_id] = time.monotonic()
    if c:
        from . import events
        events.emit("seg.changed", task_id=task_id, counts=c)


def emit_seg_changed(task_id: str, counts: dict | None = None, force: bool = False) -> None:
    """段落状态写盘后广播 counts。默认 500ms 节流；force 立刻发（Job 收尾）。"""
    if not task_id:
        return
    if counts is None:
        try:
            counts = counts_of(task_id)
        except Exception:
            return
    if not counts:
        return
    counts = dict(counts)
    with _seg_lock:
        _seg_payload[task_id] = counts
        if not force:
            now = time.monotonic()
            last = _seg_last.get(task_id, 0.0)
            wait = SEG_EMIT_GAP - (now - last)
            if wait > 0:
                if task_id not in _seg_timer:
                    tmr = threading.Timer(wait, _flush_seg, args=(task_id,))
                    tmr.daemon = True
                    _seg_timer[task_id] = tmr
                    tmr.start()
                return
        tmr = _seg_timer.pop(task_id, None)
        if tmr:
            try:
                tmr.cancel()
            except Exception:
                pass
        c = _seg_payload.pop(task_id, counts)
        _seg_last[task_id] = time.monotonic()
    from . import events
    events.emit("seg.changed", task_id=task_id, counts=c)


def get(task_id: str) -> dict:
    t = db.one("SELECT * FROM tasks WHERE id=?", (task_id,))
    if not t:
        raise OranteError(f"没有任务 {task_id}")
    return _decorate(t)


def is_archived(task_or_id) -> bool:
    if isinstance(task_or_id, dict):
        return int(task_or_id.get("archived") or 0) == 1
    t = db.one("SELECT archived FROM tasks WHERE id=?", (task_or_id,))
    return bool(t) and int(t.get("archived") or 0) == 1


def ensure_writable(task_id: str) -> dict:
    """归档只读：生成 / 重生 / 改配图之前先过这一关。"""
    t = get(task_id)
    if t["archived"]:
        raise OranteError("这个任务已归档，只读", "先到生成面板「过时」档点「解冻」。")
    return t


def normalize_row(row: dict) -> dict:
    """把表格一行（人填的）整理成能入库的字段；有问题抛 OranteError。"""
    name = (row.get("name") or "").strip()
    copy = (row.get("copy") or "").strip()
    src = (row.get("image_source") or "").strip()
    typ = TYPE_ALIASES.get((row.get("type") or "").strip().lower(), None)
    acc_name = (row.get("account") or "").strip()
    if not copy and not src and not name:
        return {}                                   # 空行
    # 规格 86：文案可以空（不说话的任务，一图一段）；图片来源仍然必填
    if not src:
        raise OranteError(f"「{name or copy[:12] or '（未命名）'}」缺图片链接或文件夹")
    if typ is None:
        if copy:
            raise OranteError(f"「{name or copy[:12]}」任务类型要选「长文案」或「短文案」")
        typ = "long"                                # 没文案时类型没意义，给个合法值占位
    acc = _acc_by_name(acc_name)
    if not acc:
        raise OranteError(f"「{name or copy[:12]}」没选账号或账号不存在：{acc_name!r}",
                          "先在右上角「账号」里添加，再在这一行选。")
    if not name:
        name = re.sub(r"\s+", " ", copy)[:16]
    if not name:
        name = Path(src.rstrip("/\\")).name[:16] or "不说话"
    out = {"name": name, "copy": copy, "image_source": src, "type": typ,
           "account_id": acc["id"], "engine": acc["engine"],
           "duration_pref": (row.get("duration_pref") or "").strip()}
    if "gen_mode" in row:
        from .services import pipeline
        out["gen_mode"] = pipeline.parse_gen_mode(row.get("gen_mode"))
    return out


_IMG_META_KEYS = ("image_src_recursive", "image_src_mode")


def patch_meta(tid: str, patch: dict | None) -> dict:
    """合并任务 meta。只动图片来源那几个键，别把 subjects / brolls 冲掉。"""
    t = get(tid)
    meta = dict(t.get("meta") or {}) if isinstance(t.get("meta"), dict) else {}
    src = patch if isinstance(patch, dict) else {}
    if "image_src_recursive" in src:
        meta["image_src_recursive"] = bool(src["image_src_recursive"])
    if "image_src_mode" in src:
        v = str(src.get("image_src_mode") or "").strip()
        if v in ("copy", "copy_mark", "move"):
            meta["image_src_mode"] = v
        else:
            meta.pop("image_src_mode", None)
    db.run("UPDATE tasks SET meta=?, updated=? WHERE id=?",
           (db.j(meta), db.now(), tid))
    return meta


def _merge_img_meta(tid: str, row: dict) -> None:
    src = {}
    m = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    for k in _IMG_META_KEYS:
        if k in m:
            src[k] = m[k]
        elif k in row:
            src[k] = row[k]
    if src:
        patch_meta(tid, src)


def upsert_rows(rows: list[dict]) -> dict:
    """任务面板「保存表格」：按 id 更新，没 id 的新建。返回 {saved, errors}。"""
    saved, errors = [], []
    for i, row in enumerate(rows):
        try:
            norm = normalize_row(row)
        except OranteError as e:
            errors.append({"row": i, "error": e.message, "hint": e.hint})
            continue
        if not norm:
            continue
        tid = (row.get("id") or "").strip()
        if tid and db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
            cur = get(tid)
            expected = row.get("updated")
            force = bool(row.get("force"))
            if expected and not force:
                cur_upd = cur.get("updated") or ""
                if str(expected) != str(cur_upd):
                    errors.append({
                        "row": i,
                        "error": "这一行在另一个窗口被改过",
                        "hint": "选「用我的」覆盖，或「用它的」改用那边的内容。",
                        "code": "conflict",
                        "conflict": True,
                        "theirs": {
                            "id": cur["id"],
                            "name": cur.get("name") or "",
                            "copy": cur.get("copy") or "",
                            "image_source": cur.get("image_source") or "",
                            "updated": cur.get("updated") or "",
                            "account_id": cur.get("account_id") or "",
                            "account": (accounts.STUDIO_NAME if accounts.is_virtual(cur.get("account_id"))
                                        else ((db.one("SELECT name FROM accounts WHERE id=?",
                                                      (cur.get("account_id"),)) or {}).get("name") or "")),
                            "type": cur.get("type") or "",
                            "duration_pref": cur.get("duration_pref") or "",
                            "gen_mode": cur.get("gen_mode") or "",
                        },
                    })
                    continue
            if cur["status"] not in ("draft",) and (cur["copy"] != norm["copy"]):
                errors.append({"row": i, "error": f"{tid} 已经准备过了，不能再改文案", "hint": "要改文案就新开一行，或把它移到回收再建。"})
                continue
            src_changed = (cur.get("image_source") or "") != norm["image_source"]
            gm = norm["gen_mode"] if "gen_mode" in norm else (cur.get("gen_mode") or "")
            db.run("UPDATE tasks SET name=?,copy=?,image_source=?,type=?,account_id=?,engine=?,duration_pref=?,gen_mode=?,row_order=?,updated=? WHERE id=?",
                   (norm["name"], norm["copy"], norm["image_source"], norm["type"], norm["account_id"], norm["engine"],
                    norm["duration_pref"], gm, i, db.now(), tid))
            # 图源改了：前面的下图/编排/出片都作废，打回 draft 重新准备。
            if src_changed and cur["status"] not in ("draft", "trash"):
                set_status(tid, "draft", force=True)
            _sync_mode_after_upsert(tid, cur, norm, gm)
            _merge_img_meta(tid, row)
        else:
            tid = db.next_id("task", "T")
            gm = norm["gen_mode"] if "gen_mode" in norm else ""
            db.run("INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,duration_pref,gen_mode,status,row_order,created,updated)"
                   " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (tid, norm["name"], norm["engine"], norm["account_id"], norm["type"], norm["copy"], norm["image_source"],
                    norm["duration_pref"], gm, "draft", i, db.now(), db.now()))
            _merge_img_meta(tid, row)
        saved.append(tid)
    return {"saved": saved, "errors": errors}


def rtc_on(task_or_id) -> bool:
    """跑到全部出片：默认开。关了才停在 blocked。"""
    t = task_or_id if isinstance(task_or_id, dict) else get(task_or_id)
    v = t.get("run_to_completion")
    if v is None:
        return True
    try:
        return int(v) != 0
    except (TypeError, ValueError):
        return bool(v)


def set_rtc(task_id: str, on: bool) -> int:
    get(task_id)
    v = 1 if on else 0
    db.run("UPDATE tasks SET run_to_completion=?, updated=? WHERE id=?",
           (v, db.now(), task_id))
    from . import events
    events.emit("tasks.changed")
    return v


def rtc_state(task_or_id) -> dict:
    t = task_or_id if isinstance(task_or_id, dict) else get(task_or_id)
    meta = t.get("meta") if isinstance(t.get("meta"), dict) else {}
    st = meta.get("rtc")
    return dict(st) if isinstance(st, dict) else {}


def set_rtc_state(task_id: str, st: dict | None) -> None:
    t = get(task_id)
    meta = dict(t.get("meta") or {}) if isinstance(t.get("meta"), dict) else {}
    if st:
        meta["rtc"] = dict(st)
    else:
        meta.pop("rtc", None)
    db.run("UPDATE tasks SET meta=?, updated=? WHERE id=?",
           (db.j(meta), db.now(), task_id))


def set_gen_mode(task_id: str, raw) -> str:
    """把任务表 gen_mode 列写成解析后的值。空 = 跟随引擎。"""
    from .services import pipeline
    stored = pipeline.parse_gen_mode(raw)
    get(task_id)
    db.run("UPDATE tasks SET gen_mode=?, updated=? WHERE id=?", (stored, db.now(), task_id))
    return stored


def apply_gen_mode(tid: str, mode: str, preview: bool = False) -> dict:
    """④ 生成点单选 / 任务表改方式。preview 只算 hint。正在跑的拒绝改。"""
    from .services import pipeline, broll
    ensure_writable(tid)
    if (not preview) and task_is_running(tid):
        raise OranteError(f"{tid} 正在跑，没改（停止后再改）")
    tdir, m, _sync = broll.load_synced(tid)
    want = mode or ""
    if preview:
        mode_n = want if want in pipeline.MODES else pipeline.MODE_FIRST
        return {"mode": mode_n, "hint": pipeline.mode_switch_hint(m, mode_n), "preview": True}
    r = pipeline.set_mode(m, want)
    m.save(tdir)
    set_gen_mode(tid, r["mode"])
    remember_counts(tdir, m)
    from . import events
    events.emit("tasks.changed")
    return {
        "mode": r["mode"],
        "hint": r.get("hint") or "",
        "roles": [sg.get("role") for sg in m.segments],
    }


def _write_manifest_mode(tid: str, mode: str, remember: bool = False) -> None:
    """已准备的任务：把 manifest.mode 对齐。正在跑的不动。"""
    if task_is_running(tid):
        return
    t = get(tid)
    d = t.get("dir")
    if not d or not Path(d).is_dir():
        return
    from .services import pipeline
    try:
        m = Manifest.load(Path(d))
    except OranteError:
        return
    if pipeline.mode_of(m) == mode:
        return
    pipeline.set_mode(m, mode, remember=remember)
    m.save(Path(d))
    remember_counts(d, m)


def _sync_mode_after_upsert(tid: str, cur: dict, norm: dict, gm: str) -> None:
    """换账号或改了生成方式列时，决定要不要动已有 manifest。

    跟随引擎 + 换了 Grok↔Flow → 模式跟着变。
    用户显式选过 → 不动（前端那行会打提示图标）。
    """
    from .services import pipeline
    old_eng = cur.get("engine") or ""
    new_eng = norm.get("engine") or old_eng
    old_gm = pipeline.parse_gen_mode(cur.get("gen_mode"))
    stored = pipeline.parse_gen_mode(gm)
    if stored in pipeline.MODES:
        if old_gm != stored:
            _write_manifest_mode(tid, stored, remember=False)
        return
    if old_eng != new_eng:
        _write_manifest_mode(tid, pipeline.default_mode_for_engine(new_eng), remember=False)


def parse_tsv(text: str) -> list[dict]:
    """从 Google Sheet 复制来的 TSV/CSV 文本 → 行字典。第一行若像表头就跳过。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    dialect = "excel-tab" if "\t" in text else "excel"
    reader = csv.reader(io.StringIO(text), dialect=dialect)
    rows = [r for r in reader if any((c or "").strip() for c in r)]
    if not rows:
        return []
    head = [c.strip().lower() for c in rows[0]]
    keymap = {"任务名": "name", "名称": "name", "name": "name", "文案": "copy", "copy": "copy", "内容": "copy",
              "图片": "image_source", "图片链接": "image_source", "链接": "image_source", "image": "image_source",
              "类型": "type", "任务类型": "type", "type": "type", "账号": "account", "account": "account",
              "时长": "duration_pref", "duration": "duration_pref",
              "生成方式": "gen_mode", "方式": "gen_mode", "gen_mode": "gen_mode"}
    if any(h in keymap for h in head):
        cols = [keymap.get(h, None) for h in head]
        rows = rows[1:]
    else:
        cols = COLUMNS[: len(rows[0])]
    out = []
    for r in rows:
        d = {}
        for c, v in zip(cols, r):
            if c:
                d[c] = v
        out.append(d)
    return out


def _rank(st: str) -> int:
    try:
        return STATUS_ORDER.index(st)
    except ValueError:
        return -1


def _note(msg: str, task_id: str | None = None) -> None:
    """状态单调性的记录：进 orante.log，顺手写到该任务正在跑的 Job 日志。"""
    print(msg, flush=True)
    if not task_id:
        return
    try:
        from .scheduler import scheduler
        for j in list(scheduler.jobs.values()):
            if j.task_id == task_id and j.status == "running":
                try:
                    j.log(msg)
                except Exception:
                    pass
    except Exception:
        pass


def set_status(task_id: str, status: str, force: bool = False) -> str:
    """把任务状态推进到 status。默认只许沿 STATUS_ORDER 往前走。

    往回退是真实需求（打回重做、进回收站），但必须显式 force=True，
    免得补描述语这种小活儿把整个任务的进度拽回去（0909 实际事故）。
    返回真正生效的状态。
    """
    if status not in STATUS_ORDER:
        raise OranteError(f"未知状态 {status}")
    cur = get(task_id)
    if cur.get("archived") and not force:
        raise OranteError("这个任务已归档，只读", "先到生成面板「过时」档点「解冻」。")
    old = cur.get("status") or "draft"
    if old not in STATUS_ORDER:
        old = "draft"
    if old == status:
        return old
    going_back = _rank(status) < _rank(old)
    # trash 是横切：任何时候都能进，不占单调性名额。
    if status == "trash":
        db.run("UPDATE tasks SET status=?, updated=? WHERE id=?", (status, db.now(), task_id))
        return status
    if force:
        if going_back or old == "trash":
            _note(f"状态回退 {task_id} {old} → {status}（force）", task_id)
        db.run("UPDATE tasks SET status=?, updated=? WHERE id=?", (status, db.now(), task_id))
        return status
    if not going_back:
        db.run("UPDATE tasks SET status=?, updated=? WHERE id=?", (status, db.now(), task_id))
        return status
    _note(f"跳过状态回退 {task_id} {old} ← {status}", task_id)
    return old


def set_priority(task_id: str, prio) -> int:
    """插旗子。0 = 自动（排在所有插了旗的后面）；>0 = 旗子上的序号，越小越优先。

    序号不封顶，也可以重复——重复只在「同一个账号」里才有意义（同号先到先得）。
    不同账号各排各的队，用同一个号完全没问题。
    """
    try:
        n = int(prio)
    except Exception:
        raise OranteError("优先级要是个整数")
    if n < 0:
        n = 0
    if n > 999:
        raise OranteError("旗子序号最大 999")
    get(task_id)                                  # 不存在就抛
    db.run("UPDATE tasks SET priority=?, updated=? WHERE id=?", (n, db.now(), task_id))
    return n


def bump_top(task_id: str) -> int:
    """置顶：在同账号里排到最前面。

    能不动别人就不动：同账号最小的旗子是 3，我就插 2。最小已经是 1 了，才把
    同账号插了旗的整体 +1（相对顺序不变），自己占 1。
    """
    t = get(task_id)
    rows_ = db.rows(
        "SELECT id, priority FROM tasks WHERE account_id=? AND id<>?"
        " AND status NOT IN ('finished','trash')",
        (t.get("account_id"), task_id))
    used = [int(r.get("priority") or 0) for r in rows_ if (r.get("priority") or 0) > 0]
    if not used:
        return set_priority(task_id, 1)
    if min(used) > 1:
        return set_priority(task_id, min(used) - 1)
    for r in rows_:
        n = int(r.get("priority") or 0)
        if n > 0:
            db.run("UPDATE tasks SET priority=?, updated=? WHERE id=?",
                   (min(999, n + 1), db.now(), r["id"]))
    return set_priority(task_id, 1)


def set_expand_account_id(task_id: str, account_id: str | None) -> None:
    """钉扩图账号。None = 跟随任务账号。"""
    ensure_writable(task_id)
    aid = (account_id or "").strip() or None
    db.run("UPDATE tasks SET expand_account_id=?, updated=? WHERE id=?",
           (aid, db.now(), task_id))


def set_account(task_id: str, account_id: str) -> dict:
    """换任务的出片账号。只许换同一引擎（Flow 换 Flow），人声不能混。"""
    ensure_writable(task_id)
    t = get(task_id)
    acc = accounts.get(account_id)
    old_eng = (t.get("engine") or "").strip()
    new_eng = (acc.get("engine") or "").strip()
    if old_eng and new_eng and old_eng != new_eng:
        raise OranteError(
            "只能换同一个引擎的账号",
            "Flow 和 Grok 的人声不一样，同一条片子里混用会跳戏。")
    db.run("UPDATE tasks SET account_id=?, engine=?, updated=? WHERE id=?",
           (acc["id"], new_eng or old_eng, db.now(), task_id))
    return get(task_id)


def set_dir(task_id: str, d: Path, batch_id: str) -> None:
    db.run("UPDATE tasks SET dir=?, batch_id=?, updated=? WHERE id=?", (str(d), batch_id, db.now(), task_id))


def delete(task_id: str, hard=False) -> None:
    if hard:
        db.run("DELETE FROM tasks WHERE id=?", (task_id,))
    else:
        set_status(task_id, "trash")


def task_is_running(task_id: str) -> bool:
    """有 running 的 Job 就不许删。本进程调度器 + 库里的 jobs 表都认。"""
    if not task_id:
        return False
    try:
        from .scheduler import scheduler
        for j in list(scheduler.jobs.values()):
            if j.task_id == task_id and j.status == "running":
                return True
    except Exception:
        pass
    r = db.one("SELECT 1 FROM jobs WHERE task_id=? AND status='running'", (task_id,))
    return bool(r)


def _is_draft_record(t: dict | None) -> bool:
    """没有任务号，或仍是草稿且没建文件夹 → 真删。有 dir 的一律当已开工。"""
    if not t:
        return True
    st = (t.get("status") or "draft")
    return st == "draft" and not t.get("dir")


def reorder(ids: list[str]) -> list[str]:
    """按传入的全量 id 顺序重写 row_order。不在名单里的（回收/过时）不动。"""
    seen: list[str] = []
    got = set()
    for tid in ids or []:
        tid = (tid or "").strip()
        if not tid or tid in got:
            continue
        if not db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
            continue
        seen.append(tid)
        got.add(tid)
    now = db.now()
    c = db.conn()
    for i, tid in enumerate(seen):
        c.execute("UPDATE tasks SET row_order=?, updated=? WHERE id=?", (i, now, tid))
    c.commit()
    return seen


def batch_delete(hard_ids: list[str], trash_ids: list[str]) -> dict:
    """批量删：草稿真删 SQLite 行，已开工只改 trash。磁盘文件一个都不动。

    正在跑的拦下来放 blocked。前端分类只是给人看的，这里按库里的真实状态再判一次。
    """
    blocked, did_hard, did_trash = [], [], []
    # 去重、保序；同一 id 若两边都出现，以 hard 名单为准再被下面的真实状态纠正。
    ordered: list[str] = []
    seen = set()
    for tid in list(hard_ids or []) + list(trash_ids or []):
        tid = (tid or "").strip()
        if not tid or tid in seen:
            continue
        seen.add(tid)
        ordered.append(tid)
    for tid in ordered:
        if task_is_running(tid):
            blocked.append(tid)
            continue
        t = db.one("SELECT * FROM tasks WHERE id=?", (tid,))
        if not t:
            continue
        if _is_draft_record(t):
            delete(tid, hard=True)
            did_hard.append(tid)
        else:
            delete(tid, hard=False)
            did_trash.append(tid)
    return {"hard": did_hard, "trash": did_trash, "blocked": blocked}


def _insert_restored(row: dict) -> str:
    """把真删掉的草稿按原 id 插回。账号按名字或 id 认。"""
    tid = (row.get("id") or "").strip()
    if not tid:
        raise OranteError("恢复草稿缺任务号")
    name = (row.get("name") or "").strip()
    copy = (row.get("copy") or "").strip()
    src = (row.get("image_source") or "").strip()
    typ = TYPE_ALIASES.get(str(row.get("type") or "").strip().lower(), row.get("type") or "short")
    if typ not in ("long", "short"):
        typ = "short"
    acc = _acc_by_name(row.get("account") or row.get("account_id") or "")
    acc_id = acc["id"] if acc else (row.get("account_id") or None)
    engine = (acc["engine"] if acc else None) or row.get("engine") or ""
    dur = (row.get("duration_pref") or "").strip()
    st = row.get("status") or "draft"
    if st not in STATUS_ORDER:
        st = "draft"
    prio = 0
    try:
        prio = int(row.get("priority") or 0)
    except Exception:
        prio = 0
    now = db.now()
    from .services import pipeline
    gm = pipeline.parse_gen_mode(row.get("gen_mode"))
    db.run(
        "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,duration_pref,"
        "gen_mode,status,dir,row_order,created,updated,priority,meta)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, name, engine, acc_id, typ, copy, src, dur, gm,
         st, row.get("dir") or None, int(row.get("row_order") or 0),
         row.get("created") or now, now, prio, "{}"),
    )
    return tid


def undelete(hard_rows: list[dict], trash: list[dict]) -> dict:
    """撤销删除：真删的整行插回，进回收的改回原状态。不碰磁盘。"""
    restored, untrashed = [], []
    for row in hard_rows or []:
        if not isinstance(row, dict):
            continue
        tid = (row.get("id") or "").strip()
        if not tid:
            continue
        cur = db.one("SELECT * FROM tasks WHERE id=?", (tid,))
        if cur:
            # 已经在就按原状态改回去（可能撤销前被别人又建了同号）
            want = row.get("status") or "draft"
            if want in STATUS_ORDER:
                set_status(tid, want, force=True)
            restored.append(tid)
            continue
        try:
            _insert_restored(row)
            restored.append(tid)
        except OranteError:
            raise
    for item in trash or []:
        if not isinstance(item, dict):
            continue
        tid = (item.get("id") or "").strip()
        if not tid:
            continue
        cur = db.one("SELECT * FROM tasks WHERE id=?", (tid,))
        if not cur:
            continue
        want = item.get("status") or "draft"
        if want not in STATUS_ORDER:
            want = "draft"
        set_status(tid, want, force=True)
        untrashed.append(tid)
    return {"restored": restored, "untrashed": untrashed}


# ---------------- 崩溃草稿快照（规格 42） ----------------
# 只是一坨 JSON，不校验、不建任务、不发号。只留最新一份。

def draft_path() -> Path:
    return paths.DATA / "draft-tasks.json"


def write_draft(rows) -> dict:
    """原样落盘。rows 原样写入，带时间戳和内容哈希。"""
    rows = list(rows or [])
    canon = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    h = hashlib.sha1(canon.encode("utf-8")).hexdigest()[:16]
    payload = {"at": time.time(), "hash": h, "rows": rows}
    p = draft_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
    return {"at": payload["at"], "hash": h, "n": len(rows)}


def read_draft() -> dict | None:
    p = draft_path()
    if not p.is_file():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def clear_draft() -> None:
    p = draft_path()
    try:
        if p.is_file():
            p.unlink()
    except OSError:
        pass


def draft_info() -> dict:
    """快照 + 和库对比：没有 id、或 id 不在库里的算「没保存的行」。"""
    d = read_draft()
    if not d:
        return {"exists": False, "rows": [], "missing": [], "missing_n": 0, "at": 0, "hash": ""}
    rows = d.get("rows") if isinstance(d.get("rows"), list) else []
    ids = {r["id"] for r in db.rows("SELECT id FROM tasks")}
    missing = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("id") or "").strip()
        if not rid or rid not in ids:
            missing.append(r)
    return {
        "exists": True,
        "at": float(d.get("at") or 0),
        "hash": str(d.get("hash") or ""),
        "rows": rows,
        "missing": missing,
        "missing_n": len(missing),
    }
