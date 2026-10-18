# -*- coding: utf-8 -*-
"""工程文件：把做完的任务连同元数据归档到批次目录，从库里移出；需要时再导回来。

不打包视频、不打包图片——它们本来就在同一个目录里。只记相对路径 + 大小 + sha256。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from .. import __version__, db, events, tasks as taskmod
from ..errors import OranteError

FORMAT = 1
# 工程文件名：2026-09-09A.orproj.json，和 AutoSub 的 .asproj.json 一个风格
ORPROJ_SUFFIX = ".orproj.json"

TASK_FIELDS = (
    "id", "name", "engine", "account_id", "type", "copy", "image_source",
    "duration_pref", "expand_account_id", "status", "dir", "batch_id",
    "row_order", "priority", "meta", "created", "updated",
)
BATCH_FIELDS = ("id", "root", "day", "version", "dir", "created")
JOB_FIELDS = ("id", "task_id", "kind", "status")

_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’\-][A-Za-z0-9]+)*")
_CJK_RE = re.compile(r"[一-鿿]")


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def sha256_file(p: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def copy_words(text: str) -> int:
    """口播词数：英文按词，中文按字。跟拆段同一口径，导不进来也不致命。"""
    t = str(text or "")
    try:
        from .. import paths
        paths.add_legacy_to_sys_path()
        import flow_split  # type: ignore
        return int(flow_split.word_count(t))
    except Exception:
        return len(_WORD_RE.findall(t)) + len(_CJK_RE.findall(t))


def _mp4_dur(p: Path) -> float:
    try:
        from .checks import mp4_duration
        d = mp4_duration(p)
        return round(float(d), 2) if d else 0.0
    except Exception:
        return 0.0


def _task_row(t: dict) -> dict:
    """tasks 表一整行，meta 里导入时写的校验标记不进工程文件。"""
    meta = t.get("meta") if isinstance(t.get("meta"), dict) else db.unj(t.get("meta"), {})
    meta = dict(meta or {})
    meta.pop("missing_dir", None)
    meta.pop("changed_files", None)
    out = {}
    for k in TASK_FIELDS:
        if k == "meta":
            out[k] = meta
        elif k == "priority":
            out[k] = int(t.get("priority") or 0)
        else:
            out[k] = t.get(k)
    return out


def _batch_row(b: dict) -> dict:
    return {k: b.get(k) for k in BATCH_FIELDS}


def _job_row(j: dict) -> dict:
    return {k: j.get(k) for k in JOB_FIELDS}


def orproj_name(batch: dict) -> str:
    return f"{batch.get('day') or ''}{batch.get('version') or ''}{ORPROJ_SUFFIX}"


def orproj_path(batch: dict) -> Path:
    return Path(batch["dir"]) / orproj_name(batch)


def _batch_of(t: dict) -> dict | None:
    """任务所属批次。库里有就用；没有就按任务目录的上一级猜。"""
    bid = (t.get("batch_id") or "").strip()
    if bid:
        b = db.one("SELECT * FROM batches WHERE id=?", (bid,))
        if b:
            return b
    d = t.get("dir") or ""
    if not d:
        return None
    parent = Path(d).parent
    b = db.one("SELECT * FROM batches WHERE dir=?", (str(parent),))
    if b:
        return b
    # 目录还在、库里没登记：用文件夹名拼一份，好写出工程文件
    if parent.is_dir():
        version = parent.name
        day = parent.parent.name
        return {
            "id": "",
            "root": str(parent.parent.parent),
            "day": day,
            "version": version,
            "dir": str(parent),
            "created": "",
        }
    return None


def _load_manifest(t: dict):
    d = t.get("dir") or ""
    if not d or not Path(d).is_dir():
        return None
    try:
        from ..models import Manifest
        return Manifest.load(Path(d))
    except Exception:
        return None


def _clip_entries(task_dir: Path, m) -> list[dict]:
    """只记真正落盘的片子：相对路径 + 大小 + sha256 + 时长。不读进内存当 base64。"""
    out = []
    for sg in (m.segments or []):
        takes = sg.get("takes") or []
        rel = ""
        if takes:
            rel = (takes[-1].get("file") or "").replace("\\", "/")
        if not rel:
            try:
                rel = f"clips/{m.clip_name(sg)}"
            except Exception:
                continue
        p = task_dir / rel
        if not p.is_file():
            continue
        try:
            nbytes = p.stat().st_size
        except OSError:
            continue
        out.append({
            "s": sg.get("s"),
            "file": rel,
            "bytes": nbytes,
            "sha256": sha256_file(p),
            "dur": _mp4_dur(p),
        })
    return out


def _seg_brief(m) -> list[dict]:
    """导入后文件夹不在了也能画出段表：不抄提示词全文。"""
    out = []
    for sg in (m.segments or []):
        out.append({
            "s": sg.get("s"),
            "duration": sg.get("duration"),
            "image": sg.get("image") or "",
            "status": sg.get("status") or "",
            "text": (sg.get("text") or "")[:200],
        })
    return out


def _summary_of(t: dict, m) -> dict:
    d = Path(t["dir"]) if t.get("dir") else None
    clips = _clip_entries(d, m) if d and m else []
    counts = m.counts() if m else {}
    images = list(m.images or []) if m else []
    copy_text = ""
    if m and isinstance(m.copy, dict):
        copy_text = m.copy.get("text") or ""
    if not copy_text:
        copy_text = t.get("copy") or ""
    return {
        "segments": len(m.segments or []) if m else 0,
        "images": len(images),
        "image_ids": [im.get("p") for im in images if im.get("p")],
        "clips": clips,
        "copy_words": copy_words(copy_text),
        "track": dict(m.track or {}) if m else {},
        "counts": counts,
        "segs": _seg_brief(m) if m else [],
    }


def _select_tasks(task_ids=None, batch_ids=None, all_finished=False) -> list[dict]:
    out: list[dict] = []
    seen = set()

    def add(t):
        if not t or t["id"] in seen:
            return
        seen.add(t["id"])
        out.append(t)

    if task_ids:
        for tid in task_ids:
            tid = str(tid or "").strip()
            if not tid:
                continue
            row = db.one("SELECT * FROM tasks WHERE id=?", (tid,))
            if not row:
                raise OranteError(f"没有任务 {tid}")
            add(taskmod._decorate(row))
        return out
    if batch_ids:
        for bid in batch_ids:
            for row in db.rows("SELECT * FROM tasks WHERE batch_id=?", (bid,)):
                add(taskmod._decorate(row))
        return out
    if all_finished:
        for row in db.rows("SELECT * FROM tasks WHERE status='finished'"):
            add(taskmod._decorate(row))
        return out
    raise OranteError("没有指定要导出的任务", "传 task_ids / batch_ids，或 all_finished。")


def _read_orproj(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    return d


def _merge_orproj(old: dict | None, new: dict) -> dict:
    """已存在同名文件 → 合并；同 id 任务以本次为准。"""
    if not old:
        return new
    tasks = {t.get("id"): t for t in (old.get("tasks") or []) if t.get("id")}
    manifests = dict(old.get("manifests") or {})
    jobs = {j.get("id"): j for j in (old.get("jobs") or []) if j.get("id")}
    for t in new.get("tasks") or []:
        if t.get("id"):
            tasks[t["id"]] = t
    manifests.update(new.get("manifests") or {})
    for j in new.get("jobs") or []:
        if j.get("id"):
            jobs[j["id"]] = j
    out = dict(new)
    out["tasks"] = list(tasks.values())
    out["manifests"] = manifests
    out["jobs"] = list(jobs.values())
    return out


def _write_orproj(path: Path, payload: dict) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")
    return path.stat().st_size


def export(task_ids=None, batch_ids=None, all_finished=False) -> dict:
    """按批次分组写 .orproj.json。返回 {files, warnings}。"""
    rows = _select_tasks(task_ids, batch_ids, all_finished)
    if not rows:
        return {"files": [], "warnings": ["没有可导出的任务"]}
    groups: dict[str, dict] = {}
    warnings: list[str] = []
    for t in rows:
        b = _batch_of(t)
        if not b or not b.get("dir"):
            warnings.append(f"{t['id']} 找不到批次目录，跳过")
            continue
        key = str(Path(b["dir"]).resolve()) if Path(b["dir"]).exists() else str(b["dir"])
        g = groups.setdefault(key, {"batch": b, "tasks": [], "manifests": {}, "jobs": []})
        g["tasks"].append(t)
        m = _load_manifest(t)
        g["manifests"][t["id"]] = _summary_of(t, m)
        for j in db.rows(
            "SELECT id,task_id,kind,status FROM jobs WHERE task_id=?", (t["id"],)
        ):
            g["jobs"].append(_job_row(j))

    files = []
    for g in groups.values():
        batch = g["batch"]
        dest = orproj_path(batch)
        payload = {
            "format": FORMAT,
            "orante": __version__,
            "exported": _now_iso(),
            "batch": _batch_row(batch),
            "tasks": [_task_row(t) for t in g["tasks"]],
            "manifests": g["manifests"],
            "jobs": g["jobs"],
        }
        old = _read_orproj(dest)
        if old is not None and old.get("format") not in (None, FORMAT):
            warnings.append(f"{dest.name} 格式不是 {FORMAT}，仍按本次合并")
        merged = _merge_orproj(old, payload)
        merged["exported"] = payload["exported"]
        merged["orante"] = __version__
        merged["format"] = FORMAT
        nbytes = _write_orproj(dest, merged)
        n_this = len(g["tasks"])
        files.append({"path": str(dest), "tasks": n_this, "bytes": nbytes})
    return {"files": files, "warnings": warnings}


def _ids_in_orproj(path: Path) -> set[str]:
    d = _read_orproj(path)
    if not d:
        return set()
    return {t.get("id") for t in (d.get("tasks") or []) if t.get("id")}


def missing_export(task_ids: list[str]) -> list[str]:
    """这些任务所在批次还没有包含它们的 .orproj.json → 返回还没归档的 id。"""
    need = []
    for tid in task_ids:
        row = db.one("SELECT * FROM tasks WHERE id=?", (tid,))
        if not row:
            need.append(tid)
            continue
        t = taskmod._decorate(row)
        b = _batch_of(t)
        if not b:
            need.append(tid)
            continue
        p = orproj_path(b)
        if tid not in _ids_in_orproj(p):
            need.append(tid)
    return need


def _delete_rows(task_ids: list[str]) -> int:
    """只删 tasks / jobs 行，一个磁盘文件都不动。"""
    if not task_ids:
        return 0
    n = 0
    for tid in task_ids:
        db.run("DELETE FROM jobs WHERE task_id=?", (tid,))
        had = db.one("SELECT 1 FROM tasks WHERE id=?", (tid,))
        db.run("DELETE FROM tasks WHERE id=?", (tid,))
        if had:
            n += 1
    return n


def clear(task_ids=None, force: bool = False) -> dict:
    """从库里移出。force=True：还没导出的先导出再删。绝不允许无归档就删记录。"""
    ids = [str(x).strip() for x in (task_ids or []) if str(x).strip()]
    if not ids:
        raise OranteError("没有指定要清空的任务")
    for tid in ids:
        if not db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
            raise OranteError(f"没有任务 {tid}")
    need = missing_export(ids)
    if need and not force:
        return {
            "ok": False,
            "need_export": need,
            "error": f"这 {len(need)} 个任务还没导出，先导出再清空",
            "hint": "点「现在就导出并清空」，或先点「导出选中」。",
        }
    warnings = []
    if need and force:
        r = export(task_ids=need)
        warnings.extend(r.get("warnings") or [])
        still = missing_export(ids)
        if still:
            return {
                "ok": False,
                "need_export": still,
                "error": f"这 {len(still)} 个任务导出失败，记录没动",
                "hint": "看警告；修完再清空。",
                "warnings": warnings,
            }
    removed = _delete_rows(ids)
    events.emit("tasks.changed")
    return {"ok": True, "removed": removed, "warnings": warnings}


def _verify_clips(task_dir: Path, summary: dict) -> list[dict]:
    """sha256 / 大小对不上 → 标「文件已改动」，不阻止导入。"""
    changed = []
    for c in summary.get("clips") or []:
        rel = (c.get("file") or "").replace("\\", "/")
        s = c.get("s")
        p = task_dir / rel
        if not p.is_file():
            changed.append({"s": s, "file": rel, "why": "missing"})
            continue
        try:
            nbytes = p.stat().st_size
        except OSError:
            changed.append({"s": s, "file": rel, "why": "missing"})
            continue
        if c.get("bytes") and int(c["bytes"]) != nbytes:
            changed.append({"s": s, "file": rel, "why": "changed"})
            continue
        expect = (c.get("sha256") or "").strip()
        if expect:
            try:
                got = sha256_file(p)
            except OSError:
                changed.append({"s": s, "file": rel, "why": "changed"})
                continue
            if got.lower() != expect.lower():
                changed.append({"s": s, "file": rel, "why": "changed"})
    return changed


def _bump_counter(name: str, prefix: str, ident: str) -> None:
    """导入的 id 比计数器大，就把计数器抬上去，免得以后发号撞上。"""
    if not ident or not ident.startswith(prefix):
        return
    tail = ident[len(prefix):]
    if not tail.isdigit():
        return
    n = int(tail)
    r = db.one("SELECT value FROM counters WHERE name=?", (name,))
    cur = int(r["value"]) if r else 0
    if n > cur:
        db.run("INSERT OR REPLACE INTO counters(name,value) VALUES(?,?)", (name, n))


def _insert_batch(b: dict) -> None:
    if not b or not b.get("id"):
        return
    if db.one("SELECT 1 FROM batches WHERE id=?", (b["id"],)):
        return
    db.run(
        "INSERT INTO batches(id,root,day,version,dir,created) VALUES(?,?,?,?,?,?)",
        (b.get("id"), b.get("root"), b.get("day"), b.get("version"),
         b.get("dir"), b.get("created") or db.now()),
    )
    _bump_counter("batch", "B", b["id"])


def _insert_job(j: dict) -> None:
    if not j or not j.get("id"):
        return
    if db.one("SELECT 1 FROM jobs WHERE id=?", (j["id"],)):
        return
    db.run(
        "INSERT INTO jobs(id,task_id,kind,status,started,finished,log,error) VALUES(?,?,?,?,?,?,?,?)",
        (j.get("id"), j.get("task_id"), j.get("kind"), j.get("status") or "done",
         None, None, None, None),
    )
    _bump_counter("job", "J", j["id"])


def _insert_task(row: dict, summary: dict, missing_dir: bool, changed: list) -> None:
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else db.unj(row.get("meta"), {})
    meta = dict(meta or {})
    meta["archive"] = summary or {}
    meta["missing_dir"] = bool(missing_dir)
    meta["changed_files"] = list(changed or [])
    cols = [
        "id", "name", "engine", "account_id", "type", "copy", "image_source",
        "duration_pref", "expand_account_id", "status", "dir", "batch_id",
        "row_order", "created", "updated", "meta", "priority", "archived",
    ]
    vals = {
        "id": row.get("id"),
        "name": row.get("name"),
        "engine": row.get("engine"),
        "account_id": row.get("account_id"),
        "type": row.get("type") if row.get("type") in ("long", "short") else "long",
        "copy": row.get("copy"),
        "image_source": row.get("image_source"),
        "duration_pref": row.get("duration_pref"),
        "expand_account_id": row.get("expand_account_id"),
        "status": "finished",          # 导入后进「过时」档
        "dir": row.get("dir"),
        "batch_id": row.get("batch_id"),
        "row_order": int(row.get("row_order") or 0),
        "created": row.get("created") or db.now(),
        "updated": row.get("updated") or db.now(),
        "meta": db.j(meta),
        "priority": int(row.get("priority") or 0),
        "archived": 1,
    }
    db.run(
        f"INSERT INTO tasks({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
        tuple(vals[c] for c in cols),
    )
    _bump_counter("task", "T", row["id"])


def do_import(paths: list[str]) -> dict:
    """多选 .orproj.json。库里已有同 id → 跳过，不覆盖正在做的任务。"""
    if not paths:
        raise OranteError("没有指定工程文件", "选一个或多个 .orproj.json。")
    imported = 0
    skipped: list[dict] = []
    missing_dir: list[str] = []
    changed_files: list[dict] = []
    warnings: list[str] = []
    for raw in paths:
        p = Path(raw)
        if not p.is_file():
            warnings.append(f"找不到文件 {raw}")
            continue
        data = _read_orproj(p)
        if not data:
            warnings.append(f"{p.name} 不是合法 JSON")
            continue
        if data.get("format") not in (None, FORMAT, 1):
            warnings.append(f"{p.name} 格式 {data.get('format')} 不认识，仍尝试导入")
        _insert_batch(data.get("batch") or {})
        manifests = data.get("manifests") or {}
        for t in data.get("tasks") or []:
            tid = (t.get("id") or "").strip()
            if not tid:
                continue
            if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
                skipped.append({"id": tid, "why": "库里已有同 id"})
                continue
            summary = manifests.get(tid) or {}
            d = Path(t["dir"]) if t.get("dir") else None
            gone = not (d and d.is_dir())
            changed = [] if gone else _verify_clips(d, summary)
            if gone:
                missing_dir.append(tid)
            for c in changed:
                changed_files.append({"id": tid, **c})
            _insert_task(t, summary, gone, changed)
            imported += 1
        for j in data.get("jobs") or []:
            _insert_job(j)
    events.emit("tasks.changed")
    return {
        "imported": imported,
        "skipped": skipped,
        "missing_dir": missing_dir,
        "changed_files": changed_files,
        "warnings": warnings,
    }


def unfreeze(task_ids: list[str]) -> dict:
    """清掉 archived，状态退回 collected，又能重生。"""
    ids = [str(x).strip() for x in (task_ids or []) if str(x).strip()]
    if not ids:
        raise OranteError("没有指定要解冻的任务")
    n = 0
    for tid in ids:
        t = taskmod.get(tid)
        if not t.get("archived"):
            continue
        # 解冻后按活任务走；缺目录标记仍留在 meta 里让界面提示
        db.run(
            "UPDATE tasks SET archived=0, status=?, updated=? WHERE id=?",
            ("collected", db.now(), tid),
        )
        n += 1
    events.emit("tasks.changed")
    return {"ok": True, "unfrozen": n}


def stub_manifest(t: dict) -> dict:
    """文件夹不在了：用工程摘要拼一份够列表/段表用的假 manifest。"""
    meta = t.get("meta") if isinstance(t.get("meta"), dict) else {}
    arch = (meta or {}).get("archive") or {}
    segs = []
    for sg in arch.get("segs") or []:
        segs.append({
            "s": sg.get("s"),
            "text": sg.get("text") or "",
            "duration": sg.get("duration") or 0,
            "image": sg.get("image") or "",
            "status": sg.get("status") or "collected",
            "error": None,
        })
    if not segs:
        for c in arch.get("clips") or []:
            segs.append({
                "s": c.get("s"),
                "text": "",
                "duration": int(round(c.get("dur") or 0)) or 0,
                "image": "",
                "status": "collected",
                "error": None,
            })
    changed = {x.get("s") for x in (t.get("changed_files") or meta.get("changed_files") or [])}
    for sg in segs:
        if sg.get("s") in changed:
            sg["error"] = "文件已改动"
    return {
        "version": 2,
        "task_id": t.get("id"),
        "name": t.get("name") or "",
        "engine": t.get("engine") or "",
        "account": "",
        "type": t.get("type") or "",
        "copy": {"text": t.get("copy") or ""},
        "images": [{"p": p, "file": "", "role": "主体", "status": "ok"} for p in (arch.get("image_ids") or [])],
        "split": {"rule": {}},
        "segments": segs,
        "track": arch.get("track") or {},
        "prompts_snapshot": {},
        "gapfill": {},
    }


def review_stub(t: dict) -> dict:
    """收片面板：目录没了也能看元数据。"""
    m = stub_manifest(t)
    segs = []
    for sg in m["segments"]:
        segs.append({
            **sg,
            "exists": False,
            "mark": False,
            "takes": [],
            "check": {"issues": []},
        })
    return {
        "segments": segs,
        "images": m["images"],
        "marks": {},
        "engine": m["engine"],
        "name": m["name"],
        "task_id": m["task_id"],
        "status": t.get("status"),
        "sync_log": [],
        "verifying": False,
        "verify_done": 0,
        "verify_total": 0,
        "archived": int(t.get("archived") or 0),
        "missing_dir": True,
    }


def _parse_updated(s: str) -> datetime | None:
    if not s:
        return None
    s = str(s).strip().replace("Z", "")
    for n, fmt in (
        (19, "%Y-%m-%d %H:%M:%S"),
        (19, "%Y-%m-%dT%H:%M:%S"),
        (10, "%Y-%m-%d"),
    ):
        try:
            return datetime.strptime(s[:n], fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


ARCHIVE_DEDUP_SEC = 300.0  # 5 分钟内第二个窗口不再跑自动归档


def auto_archive_on_startup() -> dict | None:
    """启动时：finished 且 updated 超过 N 天 → 导出并从库里移出。默认关。

    永远只动 finished；collected 还没验收完，不动。已经 archived=1 的是导回来看的，也不动。
    多开：只有第一个窗口跑，settings.archive.last_run + 5 分钟窗口去重。
    """
    from .. import config
    if not config.get("archive.auto_on"):
        return None
    now_ts = time.time()
    try:
        last_ts = float(config.get("archive.last_run") or 0)
    except (TypeError, ValueError):
        last_ts = 0.0
    if last_ts and (now_ts - last_ts) < ARCHIVE_DEDUP_SEC:
        return {"skipped": True, "reason": "recent"}
    config.set("archive.last_run", now_ts, touch_rev=False)
    try:
        days = int(config.get("archive.auto_days") or 7)
    except Exception:
        days = 7
    if days < 1:
        days = 1
    cutoff = datetime.now() - timedelta(days=days)
    ids = []
    dates = []
    for row in db.rows("SELECT * FROM tasks WHERE status='finished'"):
        t = taskmod._decorate(row)
        if t.get("archived"):
            continue
        dt = _parse_updated(t.get("updated") or t.get("created") or "")
        if not dt or dt > cutoff:
            continue
        ids.append(t["id"])
        dates.append(dt.date().isoformat())
    if not ids:
        return None
    r = export(task_ids=ids)
    still = missing_export(ids)
    done = [i for i in ids if i not in still]
    if done:
        _delete_rows(done)
        events.emit("tasks.changed")
    dates.sort()
    a = dates[0] if dates else ""
    b = dates[-1] if dates else ""
    if a and b and a[:7] == b[:7] and a != b:
        span = f"{a} ~ {b[5:]}"
    elif a and b and a != b:
        span = f"{a} ~ {b}"
    else:
        span = a
    msg = f"自动归档了 {len(done)} 个任务（{span}），要看的话「过时」里导入"
    if done:
        # emit 的第一个参数就是事件名 kind，不能再传 kind=（前端 toast 用 text/hint）
        events.emit("toast", text=msg, hint="")
    return {
        "archived": len(done),
        "failed": still,
        "span": span,
        "message": msg,
        "files": r.get("files") or [],
        "warnings": r.get("warnings") or [],
    }
