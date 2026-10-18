# -*- coding: utf-8 -*-
"""Spec 85 part2a: batch_desc + server route. Discovers real helper names at runtime."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(r"D:\0_Dev\Orante")
BAK = ".bak-0921switches"


def backup(p: Path) -> None:
    b = Path(str(p) + BAK)
    if not b.exists():
        shutil.copy2(p, b)
        print("backup", b.name)


def write_lf(p: Path, text: str) -> None:
    p.write_bytes(text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))


def discover_images_api(text: str) -> dict:
    """Find real helper / error / lock names from images.py source."""
    out = {}
    # helpers
    for name in ("_task_dir", "_task_path", "_dir_of", "_find_im", "_find_image",
                 "_norm_p", "_norm_key", "patch", "Manifest"):
        if re.search(rf"\bdef {name}\b", text) or re.search(rf"\b{name}\b", text[:3000]):
            out[name] = name
    # which task dir helper
    m = re.search(r"def (_task_\w+)\(", text)
    # prefer _task_dir
    if "def _task_dir(" in text:
        out["task_dir"] = "_task_dir"
    else:
        cands = re.findall(r"def (_\w*task\w*)\(", text)
        out["task_dir"] = cands[0] if cands else "_task_dir"
    if "def _find_im(" in text:
        out["find"] = "_find_im"
    elif "def _find_image(" in text:
        out["find"] = "_find_image"
    else:
        out["find"] = "_find_im"
    if "def _norm_p(" in text:
        out["norm"] = "_norm_p"
    elif "def _norm_key(" in text:
        out["norm"] = "_norm_key"
    else:
        out["norm"] = "_norm_p"
    # patch by= keyword
    m = re.search(r"def patch\(im:.*?\):(.*?)(?=\ndef )", text, re.S)
    out["patch_by"] = "user"
    if m and 'by="user"' in m.group(0):
        out["patch_by"] = "user"
    elif m and "by='user'" in m.group(0):
        out["patch_by"] = "user"
    # OranteError import path used in this file
    m = re.search(r"from (\.+errors) import (\w+)", text)
    if m:
        out["err_mod"], out["err"] = m.group(1), m.group(2)
    else:
        out["err_mod"], out["err"] = "..errors", "OranteError"
    m = re.search(r"from (\.+filelock) import ([^\n]+)", text)
    if m:
        out["lock_mod"] = m.group(1)
        out["lock_imports"] = m.group(2).strip()
    else:
        out["lock_mod"] = "..filelock"
        out["lock_imports"] = "manifest_lock"
    # tasks module
    if "from .. import tasks" in text or "from ..tasks import" in text:
        out["tasks"] = "tasks"
    else:
        out["tasks"] = "tasks"
    # ensure_writable name via reading tasks later
    return out


def patch_images() -> None:
    p = ROOT / "orante" / "services" / "images.py"
    backup(p)
    text = p.read_text(encoding="utf-8")
    if "def batch_desc(" in text:
        print("images.batch_desc already present")
        return
    api = discover_images_api(text)
    print("images api", api)

    # Read tasks for ensure_writable / get
    tsrc = (ROOT / "orante" / "tasks.py").read_text(encoding="utf-8")
    ensure = "ensure_writable" if "def ensure_writable(" in tsrc else "ensure_writable"
    getfn = "get"

    # Manifest save/load
    err = api["err"]
    td = api["task_dir"]
    find = api["find"]
    norm = api["norm"]
    by = api["patch_by"]

    fn = f'''

def batch_desc(items, desc: str, voice=None, mode: str = "fill_empty") -> dict:
    """跨任务批量写入描述语（规格 85）。

    items: [{{"tid": "...", "ps": ["p01", ...]}}, ...]
    mode: fill_empty | overwrite
    voice: None=不动音色；字符串（含空串）=写入
    每个任务一次拿锁、一次 save。拿不到锁的任务进 skipped，其余照写。
    """
    from {api["err_mod"]} import {err}
    from {api["lock_mod"]} import manifest_lock
    from ..models import Manifest
    from .. import tasks as taskmod

    desc = "" if desc is None else str(desc)
    if not str(desc).strip():
        raise {err}("描述语不能为空", "先在右边写好要填的描述语。")
    if mode not in ("fill_empty", "overwrite"):
        raise {err}("mode 只能是 fill_empty 或 overwrite")
    if not isinstance(items, list) or not items:
        raise {err}("请至少选一个任务和图片")

    for raw in items:
        if not isinstance(raw, dict):
            continue
        tid = str(raw.get("tid") or "").strip()
        if not tid:
            continue
        try:
            trow = taskmod.{getfn}(tid)
        except Exception:
            raise {err}(f"没有任务 {{tid}}")
        if (trow.get("status") or "draft") == "draft":
            raise {err}(
                f"任务 {{tid}} 还是草稿",
                "草稿要先准备好才能填描述语。请从列表里去掉草稿任务。",
            )

    changed = []
    skipped = []
    touched_tasks = set()

    for raw in items:
        if not isinstance(raw, dict):
            continue
        tid = str(raw.get("tid") or "").strip()
        ps = list(raw.get("ps") or [])
        if not tid or not ps:
            continue
        try:
            taskmod.{ensure}(tid)
        except {err} as e:
            why = getattr(e, "msg", None) or getattr(e, "args", [str(e)])[0]
            for pth in ps:
                skipped.append({{"tid": tid, "p": {norm}(pth) or str(pth), "why": str(why)}})
            continue

        d = {td}(tid)
        try:
            lock = manifest_lock(d, timeout=0.35)
            lock.acquire()
        except TypeError:
            # older signature without timeout kw
            try:
                lock = manifest_lock(d)
                # temporarily shrink? just try acquire; may wait full timeout — use FileLock directly
                from {api["lock_mod"]} import FileLock
                lock = FileLock(Path(d) / ".manifest.lock", timeout=0.35)
                lock.acquire()
            except {err}:
                for pth in ps:
                    skipped.append({{"tid": tid, "p": {norm}(pth) or str(pth), "why": "清单正被占用"}})
                continue
        except {err}:
            for pth in ps:
                skipped.append({{"tid": tid, "p": {norm}(pth) or str(pth), "why": "清单正被占用"}})
            continue

        try:
            m = Manifest.load(d)
            wrote = False
            for raw_p in ps:
                pth = {norm}(raw_p)
                if not pth:
                    continue
                im = {find}(m, pth)
                if not im:
                    skipped.append({{"tid": tid, "p": pth, "why": "不存在"}})
                    continue
                if (im.get("status") or "") == "missing":
                    skipped.append({{"tid": tid, "p": pth, "why": "已删除"}})
                    continue
                old_desc = im.get("desc") or ""
                old_voice = im.get("voice") or ""
                if mode == "fill_empty" and str(old_desc).strip():
                    skipped.append({{"tid": tid, "p": pth, "why": "已有描述语"}})
                    continue
                rec = {{
                    "tid": tid,
                    "p": im.get("p") or pth,
                    "desc_old": old_desc,
                    "voice_old": old_voice,
                    "desc_manual_old": bool(im.get("desc_manual")),
                    "voice_manual_old": bool(im.get("voice_manual")),
                }}
                patch(im, {{"desc": desc}}, by="{by}")
                if voice is not None:
                    patch(im, {{"voice": str(voice)}}, by="{by}")
                changed.append(rec)
                wrote = True
            if wrote:
                m.save(d)
                touched_tasks.add(tid)
        finally:
            try:
                lock.release()
            except Exception:
                pass

    return {{
        "ok": True,
        "n_tasks": len(touched_tasks),
        "n_images": len(changed),
        "changed": changed,
        "skipped": skipped,
    }}


def restore_batch_desc(changed) -> dict:
    """按 batch_desc 返回的 changed 旧值跨任务回滚。"""
    from {api["lock_mod"]} import manifest_lock
    from ..models import Manifest
    from .. import tasks as taskmod

    if not isinstance(changed, list) or not changed:
        return {{"ok": True, "n": 0}}
    by_tid: dict[str, list] = {{}}
    for it in changed:
        if not isinstance(it, dict):
            continue
        tid = str(it.get("tid") or "").strip()
        if tid:
            by_tid.setdefault(tid, []).append(it)
    n = 0
    for tid, items in by_tid.items():
        taskmod.{ensure}(tid)
        d = {td}(tid)
        with manifest_lock(d):
            m = Manifest.load(d)
            for it in items:
                pth = {norm}(it.get("p"))
                im = {find}(m, pth) if pth else None
                if not im:
                    continue
                fields = {{}}
                if "desc_old" in it:
                    fields["desc"] = it.get("desc_old") or ""
                if "voice_old" in it:
                    fields["voice"] = it.get("voice_old") or ""
                if fields:
                    patch(im, fields)
                if "desc_manual_old" in it:
                    im["desc_manual"] = bool(it.get("desc_manual_old"))
                    if not im["desc_manual"]:
                        im.pop("desc_stale", None)
                if "voice_manual_old" in it:
                    im["voice_manual"] = bool(it.get("voice_manual_old"))
                n += 1
            m.save(d)
    return {{"ok": True, "n": n}}
'''
    # Need Path import in images if used — FileLock path uses Path
    if "from pathlib import Path" not in text and "import pathlib" not in text:
        text = "from pathlib import Path\n" + text
    write_lf(p, text.rstrip() + "\n" + fn)
    print("patched images.py")


def patch_server() -> None:
    p = ROOT / "orante" / "server.py"
    backup(p)
    text = p.read_text(encoding="utf-8")
    if "/api/images/batch-desc" in text:
        print("server route exists")
        return
    needle = '@app.post("/api/tasks/{tid}/images/copy-desc")'
    i = text.find(needle)
    if i < 0:
        # try alternate
        m = re.search(r'@app\.post\("/api/tasks/\{tid\}/images/copy-desc"\)', text)
        if not m:
            raise SystemExit("copy-desc route not found")
        i = m.start()
    j = text.find("\n@app.", i + 10)
    if j < 0:
        raise SystemExit("no next route")
    route = '''
@app.post("/api/images/batch-desc")
async def images_batch_desc(req: Request):
    """跨任务批量填描述语（规格 85）。body.restore=changed 列表则撤销。"""
    from .services import images as imgmod
    d = await body(req) or {}
    rest = d.get("restore")
    if isinstance(rest, list):
        return ok(**imgmod.restore_batch_desc(rest))
    return ok(**imgmod.batch_desc(
        d.get("items") or [],
        d.get("desc") or "",
        voice=d["voice"] if "voice" in d else None,
        mode=d.get("mode") or "fill_empty",
    ))

'''
    text = text[:j] + route + text[j:]
    write_lf(p, text)
    print("patched server.py")


if __name__ == "__main__":
    patch_images()
    patch_server()
    print("part2a done")