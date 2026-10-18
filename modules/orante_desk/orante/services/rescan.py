# -*- coding: utf-8 -*-
"""按磁盘重建图库：靠内容认亲，描述语跟着走，编号按文件名重排。

浅层刷新只 stat，不算哈希。点「重扫目录」才走这里。
先 preview 给人看，确认后再 apply；apply 前自动备份 manifest。
"""
from __future__ import annotations

import shutil
import time
from collections import defaultdict
from pathlib import Path

from .. import events, tasks as taskmod
from ..errors import OranteError
from ..models import Manifest
from . import images as imgmod
from .track import LOCKED_STATES, fmt_p

DELETED_NOTE = "配图已删除，去编排重新配"
# 认亲后继承这些字段；p / file / sha 按磁盘重写
_KEEP = (
    "desc", "pose", "role", "status", "voice", "eye", "scene",
    "measure", "origin", "src", "edits", "flipped", "expand_ref",
    "cov", "origin_from",
)


def _task_dir(tid: str) -> Path:
    t = taskmod.get(tid)
    if not t.get("dir"):
        raise OranteError("这个任务还没准备", "先在任务面板点「准备」。")
    d = Path(t["dir"])
    if not d.is_dir():
        raise OranteError("任务文件夹不在了", str(d))
    return d


def _lab(s) -> str:
    try:
        return "s%03d" % int(s)
    except (TypeError, ValueError):
        return str(s)


def _posix(rel: str) -> str:
    return (rel or "").replace("\\", "/")


def _offscan_rel(rel: str) -> bool:
    """文件落在扫描会跳过的目录：空镜头/ 或任意 _ 开头的子目录。"""
    parts = [p for p in _posix(rel).split("/") if p]
    if not parts:
        return False
    if parts[0] == "空镜头":
        return True
    return any(p.startswith("_") for p in parts[:-1])


def _rel_of(task_dir: Path, fp: Path) -> str:
    try:
        return _posix(str(fp.relative_to(task_dir)))
    except ValueError:
        return _posix(str(fp))


def _old_hash(task_dir: Path, im: dict) -> tuple[str, str]:
    """返回 (sha256, sha1)。旧库没记 sha256 时，文件还在就补算。"""
    s256 = (im.get("sha256") or "").strip()
    s1 = (im.get("sha1") or "").strip()
    if s256 and s1:
        return s256, s1
    rel = _posix(im.get("file") or "")
    fp = (task_dir / rel) if rel else None
    if fp is not None and fp.is_file():
        h = imgmod.hash_file_cached(fp)
        return s256 or h["sha256"], s1 or h["sha1"]
    return s256, s1


def _next_p(used: set[str], start: int = 1, prefix: str = "p") -> tuple[str, int]:
    n = start
    while True:
        code = "%s%02d" % (prefix, n)
        n += 1
        if code not in used:
            return code, n


def _refs(m: Manifest, p: str) -> list[dict]:
    """哪些段还指着这张图（配图或接片锚点）。"""
    out = []
    for sg in m.segments or []:
        if (sg.get("image") or "") == p:
            out.append(sg)
            continue
        br = sg.get("bridge") if isinstance(sg.get("bridge"), dict) else {}
        if (br.get("to_image") or "") == p:
            out.append(sg)
    return out


def build_plan(task_dir: Path, m: Manifest) -> dict:
    """只算不写。返回预览结构 + 应用时要用的 new_images / remap。"""
    task_dir = Path(task_dir)
    files = imgmod.list_library_files(task_dir / "images")
    infos = []
    for fp in files:
        h = imgmod.hash_file_cached(fp)
        infos.append({
            "path": fp,
            "name": fp.name,
            "rel": _rel_of(task_dir, fp),
            "sha1": h["sha1"],
            "sha256": h["sha256"],
            "size": h["size"],
            "mtime": h["mtime"],
        })

    by256: dict[str, list] = defaultdict(list)
    by1: dict[str, list] = defaultdict(list)
    for im in m.images or []:
        s256, s1 = _old_hash(task_dir, im)
        if s256:
            by256[s256].append(im)
        if s1:
            by1[s1].append(im)

    used_old: set[int] = set()
    match_of: dict[str, dict] = {}  # rel → old im

    def _take(cands: list, name: str):
        left = [x for x in cands if id(x) not in used_old]
        if not left:
            return None
        same = [x for x in left
                if Path(_posix(x.get("file") or "")).name.lower() == name.lower()]
        pick = (same or left)[0]
        used_old.add(id(pick))
        return pick

    for info in infos:
        old = None
        if info["sha256"]:
            old = _take(by256.get(info["sha256"]) or [], info["name"])
        if old is None and info["sha1"]:
            old = _take(by1.get(info["sha1"]) or [], info["name"])
        if old is not None:
            match_of[info["rel"]] = old

    leftover = [im for im in (m.images or []) if id(im) not in used_old]
    kept_broll = []
    deleted = []
    for im in leftover:
        rel = _posix(im.get("file") or "")
        fp = (task_dir / rel) if rel else None
        # 空镜头/ 和下划线子目录（_候选 / _弃用 / …）不在这次扫描范围里。
        # 文件还在就原样留下，扩图待审图不能被静默重扫当成「已删除」。
        if fp is not None and fp.is_file() and (
                im.get("role") == "空镜" or _offscan_rel(rel)):
            kept_broll.append(im)
        else:
            deleted.append(im)

    # 已出片还指着被删的图：占住原来的编号，绝不把这个号发给另一张活图
    reserved: set[str] = set()
    for im in deleted:
        old_p = im.get("p") or ""
        if not old_p:
            continue
        if any((sg.get("status") or "") in LOCKED_STATES for sg in _refs(m, old_p)):
            reserved.add(old_p)

    new_images: list[dict] = []
    remap: dict[str, str] = {}
    recognized = []
    added = []
    used_ps: set[str] = set()
    next_n = 1

    for info in infos:
        # 跳过已出片占住的号，那些号留给缺失 stub
        new_p, next_n = _next_p(used_ps | reserved, next_n, "p")
        used_ps.add(new_p)
        old = match_of.get(info["rel"])
        rec = {
            "p": new_p,
            "file": info["rel"],
            "sha1": info["sha1"],
            "sha256": info["sha256"],
            "role": "主体",
            "status": "ok",
            "desc": "",
            "voice": "",
            "eye": "闭眼",
            "scene": "常规",
            "measure": {},
            "origin": "local",
            "src": info["name"],
            "pose": {},
        }
        if old:
            for k in _KEEP:
                if k in old:
                    rec[k] = old[k]
            if rec.get("status") == "missing":
                rec["status"] = "ok"
            if rec.get("role") not in ("主体", "空镜"):
                rec["role"] = "主体"
            old_p = old.get("p") or ""
            if old_p:
                remap[old_p] = new_p
            old_name = Path(_posix(old.get("file") or "")).name
            recognized.append({
                "file": info["name"],
                "old_p": old_p,
                "new_p": new_p,
                "renamed": old_name.lower() != info["name"].lower() or old_p != new_p,
                "desc_kept": bool((old.get("desc") or "").strip()),
            })
        else:
            added.append({"file": info["name"], "new_p": new_p})
        new_images.append(rec)

    for im in kept_broll:
        rec = dict(im)
        old_p = rec.get("p") or ""
        if old_p in used_ps or old_p in reserved:
            prefix = "b" if str(old_p).lower().startswith("b") else "p"
            new_p, next_n = _next_p(used_ps | reserved, next_n, prefix)
            rec["p"] = new_p
            if old_p:
                remap[old_p] = new_p
            used_ps.add(new_p)
        else:
            if old_p:
                remap[old_p] = old_p
                used_ps.add(old_p)
        new_images.append(rec)

    deleted_view = []
    stubs = []
    stub_map: dict[str, str] = {}
    deleted_ps: list[str] = []
    for im in deleted:
        old_p = im.get("p") or ""
        if old_p:
            deleted_ps.append(old_p)
        refs = _refs(m, old_p) if old_p else []
        locked = [sg for sg in refs if (sg.get("status") or "") in LOCKED_STATES]
        unlocked = [sg for sg in refs if sg not in locked]
        if locked:
            rec = dict(im)
            rec["status"] = "missing"
            if old_p and old_p not in used_ps:
                stub_p = old_p
            else:
                stub_p, next_n = _next_p(used_ps, next_n, "p")
            rec["p"] = stub_p
            if old_p:
                stub_map[old_p] = stub_p
            used_ps.add(stub_p)
            stubs.append(rec)
            new_images.append(rec)
        deleted_view.append({
            "old_p": old_p,
            "file": Path(_posix(im.get("file") or "")).name,
            "refs": [_lab(sg.get("s")) for sg in unlocked],
            "locked_refs": [_lab(sg.get("s")) for sg in locked],
        })

    # 内容完全相同的磁盘文件
    by_hash: dict[str, list] = defaultdict(list)
    for info in infos:
        if info["sha256"]:
            by_hash[info["sha256"]].append(info)
    dupes = []
    for s256, group in by_hash.items():
        if len(group) < 2:
            continue
        dupes.append({
            "sha256": s256,
            "files": [x["name"] for x in group],
            "bytes": group[0]["size"],
        })

    # 编号变化会动哪些段 / 钉
    remap_segs = []
    frozen = []
    will_clear = []
    deleted_set = set(deleted_ps)

    def _preview_new(old: str, locked: bool) -> str:
        if not old:
            return old
        if old in deleted_set:
            return stub_map.get(old, "") if locked else ""
        if old in remap:
            return remap[old]
        return old

    for sg in m.segments or []:
        locked = (sg.get("status") or "") in LOCKED_STATES
        old = sg.get("image") or ""
        new = _preview_new(old, locked)
        if old in deleted_set:
            if locked:
                frozen.append(_lab(sg.get("s")))
            else:
                will_clear.append(_lab(sg.get("s")))
        elif new != old:
            remap_segs.append(_lab(sg.get("s")))
            if locked:
                frozen.append(_lab(sg.get("s")))
        br = sg.get("bridge") if isinstance(sg.get("bridge"), dict) else {}
        to = br.get("to_image") or ""
        if to and _preview_new(to, locked) != to and _lab(sg.get("s")) not in remap_segs:
            remap_segs.append(_lab(sg.get("s")))

    pins = (m.track or {}).get("pins") or {}
    remap_pins = 0
    for v in pins.values():
        if v in deleted_set:
            remap_pins += 1
        elif v in remap and remap[v] != v:
            remap_pins += 1

    return {
        "disk_n": len(infos),
        "library_n": len(m.images or []),
        "recognized": recognized,
        "added": added,
        "deleted": deleted_view,
        "dupes": dupes,
        "remap": remap,
        "stub_map": stub_map,
        "deleted_ps": deleted_ps,
        "remap_segs": remap_segs,
        "remap_pins": remap_pins,
        "frozen": frozen,
        "will_clear": will_clear,
        "new_images": new_images,
        "stubs": [s.get("p") for s in stubs],
    }


def _resolve(p: str, locked: bool, remap: dict, stub_map: dict, deleted_ps: set) -> str:
    """一张旧编号在这次重扫后该写成什么。删除的：已出片挂 stub，其余清空。"""
    if not p:
        return ""
    if p in deleted_ps:
        return stub_map.get(p, "") if locked else ""
    if p in remap:
        return remap[p]
    return p


def _apply_remap(m: Manifest, plan: dict) -> None:
    """按旧编号 → 新编号改写段、钉、图池、接片锚点。删除的清空并打标记。"""
    remap = plan.get("remap") or {}
    stub_map = plan.get("stub_map") or {}
    deleted_ps = set(plan.get("deleted_ps") or [])
    for sg in m.segments or []:
        locked = (sg.get("status") or "") in LOCKED_STATES
        old = sg.get("image") or ""
        new = _resolve(old, locked, remap, stub_map, deleted_ps)
        if old in deleted_ps:
            if locked:
                sg["image"] = new or old
            else:
                sg["image"] = ""
                sg["image_note"] = DELETED_NOTE
        elif new != old:
            sg["image"] = new
            if not locked:
                sg.pop("image_note", None)
        br = sg.get("bridge") if isinstance(sg.get("bridge"), dict) else None
        if br:
            to = br.get("to_image") or ""
            nt = _resolve(to, locked, remap, stub_map, deleted_ps)
            if nt != to:
                br["to_image"] = nt
                sg["bridge"] = br

    tr = dict(m.track or {})
    pins = dict(tr.get("pins") or {})
    new_pins = {}
    for k, v in pins.items():
        try:
            sn = int(k)
        except (TypeError, ValueError):
            sn = 0
        sg = m.seg(sn) if sn else None
        locked = bool(sg) and (sg.get("status") or "") in LOCKED_STATES
        nv = _resolve(v, locked, remap, stub_map, deleted_ps)
        if nv:
            new_pins[k] = nv
        # 删掉的钉（未出片）直接丢掉，不改挂别的图
    tr["pins"] = new_pins

    pools = []
    for z in tr.get("pools") or []:
        if not isinstance(z, dict):
            continue
        imgs = []
        for x in (z.get("images") or z.get("pool") or []):
            fp = fmt_p(x) or x
            if not fp or fp in deleted_ps:
                continue
            nv = remap.get(fp, fp)
            if nv and nv not in imgs:
                imgs.append(nv)
        if imgs:
            pools.append({**z, "images": imgs})
    tr["pools"] = pools
    if tr.get("pool"):
        npool = []
        for x in tr["pool"]:
            fp = fmt_p(x) or x
            if not fp or fp in deleted_ps:
                continue
            nv = remap.get(fp, fp)
            if nv and nv not in npool:
                npool.append(nv)
        tr["pool"] = npool
    m.track = tr


def _backup(task_dir: Path) -> Path:
    src = Path(task_dir) / "manifest.json"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = Path(task_dir) / ("manifest.bak-rescan-%s.json" % stamp)
    n = 1
    while dest.exists():
        dest = Path(task_dir) / ("manifest.bak-rescan-%s-%d.json" % (stamp, n))
        n += 1
    shutil.copy2(src, dest)
    return dest


def preview(tid: str) -> dict:
    d = _task_dir(tid)
    m = Manifest.load(d)
    plan = build_plan(d, m)
    return _public(plan)


def apply(tid: str) -> dict:
    taskmod.ensure_writable(tid)
    d = _task_dir(tid)
    m = Manifest.load(d)
    plan = build_plan(d, m)
    bak = _backup(d)
    m.images = plan["new_images"]
    _apply_remap(m, plan)
    m.save(d)
    events.emit("tasks.changed")
    out = _public(plan)
    out["backup"] = bak.name
    return out


def _public(plan: dict) -> dict:
    """给前端的预览，不带 new_images 整表。"""
    renamed = [x for x in plan["recognized"] if x.get("renamed")]
    return {
        "disk_n": plan["disk_n"],
        "library_n": plan["library_n"],
        "recognized": plan["recognized"],
        "renamed": renamed,
        "added": plan["added"],
        "deleted": plan["deleted"],
        "dupes": plan["dupes"],
        "remap": {k: v for k, v in (plan["remap"] or {}).items() if v and v != k},
        "remap_segs": plan["remap_segs"],
        "remap_pins": plan["remap_pins"],
        "frozen": plan["frozen"],
        "will_clear": plan["will_clear"],
        "stubs": plan.get("stubs") or [],
    }


def adopt_image(tid: str, p: str, from_p: str) -> dict:
    """「文件不在」卡片：从图库挑一张，把文件拷到这条的位置。编号不变。"""
    from . import expand as expandmod
    taskmod.ensure_writable(tid)
    p = (p or "").strip()
    from_p = (from_p or "").strip()
    if not p or not from_p or p == from_p:
        raise OranteError("请挑另一张图来替换")
    d = _task_dir(tid)
    m = Manifest.load(d)
    im = m.image(p)
    src_im = m.image(from_p)
    if not im:
        raise OranteError(f"没有图片 {p}")
    if not src_im:
        raise OranteError(f"没有图片 {from_p}")
    src_rel = _posix(src_im.get("file") or "")
    src = d / src_rel if src_rel else None
    if src is None or not src.is_file():
        raise OranteError(f"{from_p} 的文件也不在", "换一张盘上还在的图")
    dest_rel = _posix(im.get("file") or "")
    if not dest_rel:
        dest_rel = "images/%s%s" % (p, src.suffix.lower() or ".jpg")
    dest = d / dest_rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.resolve() == src.resolve():
        raise OranteError("这两张已经是同一个文件")
    # 覆盖前若目标还在，先丢进 _replaced，和翻转/裁切同一套
    if dest.is_file() and dest.resolve() != src.resolve():
        try:
            expandmod._move_file(dest, d / expandmod.REPL_DIR / dest.name)
        except Exception:
            dest.unlink()
    shutil.copy2(src, dest)
    h = imgmod.hash_file_cached(dest)
    im["file"] = dest_rel
    im["sha1"] = h["sha1"]
    im["sha256"] = h["sha256"]
    if im.get("status") == "missing":
        im["status"] = "ok"
    im.pop("exists", None)
    m.save(d)
    events.emit("tasks.changed")
    out = dict(im)
    out["exists"] = True
    out["mtime"] = h["mtime"]
    out["bytes"] = h["size"]
    return out
