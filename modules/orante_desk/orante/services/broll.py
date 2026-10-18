# -*- coding: utf-8 -*-
"""空镜判定、搬家、准备阶段重编号、与磁盘同步。

判定提示词写死在这里：景别优先。大全景一律空镜，大屏幕上的脸不算主体。
prepare 筛完会重编号（主体 p01… / 空镜 b01…）；之后改 role 只搬家不改号。
"""
from __future__ import annotations

import json
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ..errors import OranteError
from ..models import sha1_file
from ..scheduler import JobCancelled

IMG_DIR = "images"
BROLL_DIR = "空镜头"
CAND_DIR = "images/_候选"
SHOT_SIZES = ("远景", "全景", "中景", "近景", "特写")
IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
SKIP_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}

# 景别优先：远景/大全景一律空镜，哪怕有人面朝镜头、哪怕大屏幕上有人脸。
BROLL_PROMPT = (
    "看这张图，判断是不是空镜（b-roll / 气氛镜头），不要写描述语。\n"
    "只输出一个 JSON 对象，不要 markdown、不要解释：\n"
    '{"is_broll": true或false, "reason": "一句话原因", '
    '"shot_size": "远景|全景|中景|近景|特写", '
    '"people": 整数, "crowd": true或false, '
    '"subject_height": 0到1的小数}\n\n'
    "判定顺序：先判景别，再判有没有可拍的主体。\n"
    "1. 远景 / 大全景：看得见整个场地、看台、几百上千人；"
    "主要人物（真人，不是屏幕上的影像）身高不足画面高度的 1/4 "
    "→ 一律 is_broll=true。哪怕有人面朝镜头，哪怕大屏幕上放着人脸。"
    "大屏幕上的脸不算主体。\n"
    "2. 观众席、一排排背影或后脑勺、空场地、器物特写、人人平均没有主角 "
    "→ is_broll=true。\n"
    "3. 只有当一个人本身（不是屏幕上的影像）占画面主要面积"
    "（身高 ≥ 画面高度的 1/4，或景别在中景及以内）并且被明确拍摄时 "
    "→ is_broll=false。\n"
    "subject_height 是主要人物（真人）身高占画面高度的估计，没有人就填 0。\n"
    "只回 JSON。"
)


def _cfg(key, default=None):
    try:
        from .. import config
        return config.get(key, default)
    except Exception:
        from ..config import DEFAULTS
        return DEFAULTS.get(key, default)


def _posix(rel: str) -> str:
    return (rel or "").replace("\\", "/")


def _move(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_file() and src.resolve() != dst.resolve():
        if dst.is_file():
            dst.unlink()
        shutil.move(str(src), str(dst))


def parse_broll(raw: str) -> dict:
    """把 Gemini 的 JSON 收成判定结果。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text) if isinstance(raw, str) else (raw or {})
    except Exception:
        raise OranteError("Gemini 没有返回合法 JSON", (raw or "")[:200])
    # Gemini 有时把对象包在数组里：[{...}]，取第一个
    if isinstance(data, list):
        data = next((x for x in data if isinstance(x, dict)), None)
    if not isinstance(data, dict):
        raise OranteError("空镜判定不是 JSON 对象", (raw or "")[:200])
    shot = str(data.get("shot_size") or "").strip()
    if shot not in SHOT_SIZES:
        shot = "全景"
    people = data.get("people")
    try:
        people = max(0, int(people))
    except (TypeError, ValueError):
        people = 0
    try:
        height = float(data.get("subject_height"))
    except (TypeError, ValueError):
        height = 0.0
    height = max(0.0, min(1.0, height))
    return {
        "is_broll": bool(data.get("is_broll")),
        "reason": str(data.get("reason") or "").strip(),
        "shot_size": shot,
        "people": people,
        "crowd": bool(data.get("crowd")),
        "subject_height": height,
    }


def judge(gemini, img_path) -> dict:
    """问 Gemini 这张是不是空镜。不写描述语、不测量、不搬家。"""
    raw = gemini.ask_image(BROLL_PROMPT, Path(img_path), json_mode=True)
    return parse_broll(raw)


def relocate(task_dir: Path, im: dict, role: str) -> None:
    """按 role 把文件在 images/ 与 空镜头/ 之间搬，同步 file 字段。不改编号。"""
    name = Path(_posix(im.get("file") or "")).name
    if not name:
        name = (im.get("p") or "p00") + ".jpg"
    dest_rel = f"{BROLL_DIR}/{name}" if role == "空镜" else f"{IMG_DIR}/{name}"
    cur = _posix(im.get("file") or "")
    if cur == dest_rel:
        im["role"] = role
        return
    src = Path(task_dir) / (im.get("file") or dest_rel)
    dst = Path(task_dir) / dest_rel
    _move(src, dst)
    im["file"] = dest_rel
    im["role"] = role


def next_code(images, prefix: str) -> str:
    """p / b 系列下一个号。"""
    n = 0
    plen = len(prefix)
    for im in images or []:
        raw = str(im.get("p") or "")
        if raw.lower().startswith(prefix.lower()):
            try:
                n = max(n, int(raw[plen:]))
            except (TypeError, ValueError):
                pass
    return f"{prefix}{n + 1:02d}"


def segments_using_broll(manifest) -> list[dict]:
    """seg.image 指向一张现在 role=空镜 的图。"""
    broll_ps = {im.get("p") for im in (manifest.images or [])
                if im.get("role") == "空镜"}
    out = []
    for sg in manifest.segments or []:
        if sg.get("image") in broll_ps:
            out.append(sg)
    return out


def _eye_from_sidecar(im: dict) -> str:
    """从描述语/音色行恢复眼态；空镜标签当作默认闭眼。"""
    raw = (im.get("desc") or "").rstrip()
    if im.get("voice"):
        raw = raw + "\n" + str(im["voice"]).strip()
    if not raw.strip():
        return "闭眼"
    try:
        from .. import paths
        paths.add_legacy_to_sys_path()
        from longtik_images import parse_sidecar_text  # type: ignore
        parsed = parse_sidecar_text(raw)
        eye = parsed.get("eye") or "闭眼"
    except Exception:
        eye = "闭眼"
    if eye == "空镜":
        return "闭眼"
    return eye


def _apply_check(im: dict, result: dict) -> None:
    im["broll_check"] = result
    im["broll_reason"] = result.get("reason") or ""
    if result.get("is_broll"):
        im["role"] = "空镜"
        im["eye"] = "空镜"


def screen(job, task_dir: Path, images: list) -> list:
    """images.fetch 之后、拆段之前：有 Key 才判，判完重编号。

    名册里一条可用接口都没有时不报错，记下「留到描述语阶段判」原样返回。
    """
    images = list(images or [])
    if not images:
        return images
    try:
        from . import ai, aikeys
        # ai.client() 构造不再因缺 key 抛错，空名册要自己拦，行为跟以前一致
        if not aikeys.list_all(enabled_only=True):
            raise OranteError("还没有可用的多模态接口")
        gemini = ai.client()
    except OranteError:
        job.log("未配置多模态接口，空镜留到描述语阶段判")
        return images

    n_workers = int(_cfg("gemini.concurrency", 4) or 4)
    n_workers = max(1, min(n_workers, len(images)))
    lines = [""] * len(images)

    def one(i: int, im: dict) -> None:
        job.check()
        p = im.get("p") or "?"
        rel = _posix(im.get("file") or "")
        img_path = Path(task_dir) / rel
        if not img_path.is_file():
            lines[i] = f"{p} 文件不在，跳过空镜判定"
            return
        try:
            result = judge(gemini, img_path)
        except JobCancelled:
            raise
        except OranteError as e:
            lines[i] = f"{p} 空镜判定失败（{e.message}），暂当主体"
            return
        except Exception as e:  # noqa: BLE001
            lines[i] = f"{p} 空镜判定失败（{type(e).__name__}: {e}），暂当主体"
            return
        job.check()
        _apply_check(im, result)
        why = result.get("reason") or result.get("shot_size") or ""
        if result.get("is_broll"):
            lines[i] = f"{p} → 空镜（{why}）"
        else:
            lines[i] = f"{p} 主体"

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futs = [pool.submit(one, i, im) for i, im in enumerate(images)]
        for fut in as_completed(futs):
            fut.result()
    for ln in lines:
        if ln:
            job.log(ln)

    for im in images:
        if im.get("role") == "空镜":
            relocate(task_dir, im, "空镜")

    images = renumber(task_dir, images)
    n_subj = sum(1 for im in images if im.get("role") != "空镜")
    n_broll = sum(1 for im in images if im.get("role") == "空镜")
    job.log(f"✓ 主体 {n_subj} 张，空镜 {n_broll} 张")
    return images


def renumber(task_dir: Path, images: list) -> list:
    """主体按原顺序连续编 p01…，空镜按原顺序编 b01…，文件名同步改。

    两段改名，避免 p03→p02 时撞上还在的 p02。
    """
    task_dir = Path(task_dir)
    plan = []
    si = bi = 1
    for im in images:
        ext = Path(_posix(im.get("file") or "x.jpg")).suffix or ".jpg"
        if ext.lower() not in IMG_EXTS:
            ext = ".jpg"
        if im.get("role") == "空镜":
            new_p = f"b{bi:02d}"
            dest_rel = f"{BROLL_DIR}/{new_p}{ext}"
            bi += 1
        else:
            new_p = f"p{si:02d}"
            dest_rel = f"{IMG_DIR}/{new_p}{ext}"
            si += 1
        plan.append((im, new_p, dest_rel))

    temps = []
    for im, new_p, dest_rel in plan:
        src = task_dir / _posix(im.get("file") or dest_rel)
        dst = task_dir / dest_rel
        tmp = dst.with_name(".renum-" + dst.name)
        tmp.parent.mkdir(parents=True, exist_ok=True)
        if src.is_file():
            if tmp.exists() and tmp.resolve() != src.resolve():
                tmp.unlink()
            if src.resolve() != tmp.resolve():
                _move(src, tmp)
            temps.append((im, new_p, dest_rel, tmp))
        else:
            im["p"] = new_p
            im["file"] = dest_rel
            temps.append((im, new_p, dest_rel, None))

    for im, new_p, dest_rel, tmp in temps:
        dst = task_dir / dest_rel
        if tmp is not None and tmp.is_file():
            _move(tmp, dst)
        im["p"] = new_p
        im["file"] = dest_rel
    return images


def _list_img_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    out = []
    for p in folder.iterdir():
        if not p.is_file():
            continue
        if p.name.startswith(".") or p.name.lower() in SKIP_NAMES:
            continue
        if p.suffix.lower() not in IMG_EXTS:
            continue
        out.append(p)
    out.sort(key=lambda x: x.name.lower())
    return out


def _find_by_name(task_dir: Path, name: str) -> dict:
    """按文件名在三处找：空镜头 / images / images/_候选。"""
    found = {}
    if not name:
        return found
    b = task_dir / BROLL_DIR / name
    if b.is_file():
        found["broll"] = b
    i = task_dir / IMG_DIR / name
    if i.is_file():
        found["images"] = i
    c = task_dir / CAND_DIR / name
    if c.is_file():
        found["cand"] = c
    return found


def _new_manual(task_dir: Path, p: str, rel: str, role: str) -> dict:
    path = Path(task_dir) / rel
    return {
        "p": p,
        "file": _posix(rel),
        "sha1": sha1_file(path) if path.is_file() else "",
        "role": role,
        "status": "ok",
        "desc": "",
        "voice": "",
        "eye": "空镜" if role == "空镜" else "闭眼",
        "scene": "常规",
        "measure": {},
        "origin": "manual",
        "src": Path(rel).name,
    }


def sync_with_disk(task_dir, manifest) -> list[str]:
    """manifest 的 role 以磁盘为准。返回变更日志行。

    已有配图轨时不改编号，只改 role / file。新出现的文件另编下一个 p/b 号。
    """
    task_dir = Path(task_dir)
    images = list(manifest.images or [])
    logs: list[str] = []

    for im in images:
        rel = _posix(im.get("file") or "")
        name = Path(rel).name
        recorded = task_dir / rel if rel else None
        if recorded is not None and recorded.is_file():
            if im.get("exists") is False:
                im.pop("exists", None)
            continue
        found = _find_by_name(task_dir, name)
        p = im.get("p") or "?"
        if "broll" in found:
            new_rel = f"{BROLL_DIR}/{name}"
            im["file"] = new_rel
            im["role"] = "空镜"
            im["eye"] = "空镜"
            im["origin_sync"] = "manual"
            im.pop("exists", None)
            logs.append(f"{p} 用户已手动移到 空镜头/")
        elif "images" in found:
            new_rel = f"{IMG_DIR}/{name}"
            im["file"] = new_rel
            im["role"] = "主体"
            im["eye"] = _eye_from_sidecar(im)
            im["origin_sync"] = "manual"
            im.pop("exists", None)
            logs.append(f"{p} 用户已手动移回 images/")
        elif "cand" in found:
            new_rel = f"{CAND_DIR}/{name}"
            im["file"] = new_rel
            if im.get("role") == "空镜":
                im["role"] = "主体"
                im["eye"] = _eye_from_sidecar(im)
            im["origin_sync"] = "manual"
            im.pop("exists", None)
            logs.append(f"{p} 用户已手动移到 images/_候选/")
        else:
            im["exists"] = False

    registered = {Path(_posix(im.get("file") or "")).name.lower()
                  for im in images if im.get("file")}

    for fp in _list_img_files(task_dir / BROLL_DIR):
        if fp.name.lower() in registered:
            continue
        code = next_code(images, "b")
        rel = f"{BROLL_DIR}/{fp.name}"
        rec = _new_manual(task_dir, code, rel, "空镜")
        images.append(rec)
        registered.add(fp.name.lower())
        logs.append(f"{fp.name} 新登记为 {code}")

    for fp in _list_img_files(task_dir / IMG_DIR):
        if fp.name.lower() in registered:
            continue
        code = next_code(images, "p")
        rel = f"{IMG_DIR}/{fp.name}"
        rec = _new_manual(task_dir, code, rel, "主体")
        images.append(rec)
        registered.add(fp.name.lower())
        logs.append(f"{fp.name} 新登记为 {code}")

    manifest.images = images

    # 给前端用：缺文件标 exists=false，在的清掉 false
    for im in images:
        rel = _posix(im.get("file") or "")
        on_disk = bool(rel) and (task_dir / rel).is_file()
        if on_disk:
            if im.get("exists") is False:
                im.pop("exists", None)
        else:
            im["exists"] = False
    return logs


def load_synced(task_id: str):
    """读 manifest 并按磁盘同步。有变更就落盘。返回 (dir, manifest, sync_log)。"""
    from .. import tasks as taskmod
    from ..models import Manifest
    t = taskmod.get(task_id)
    if not t.get("dir"):
        raise OranteError("这个任务还没准备", "先在任务面板点「准备」。")
    d = Path(t["dir"])
    if not d.is_dir():
        raise OranteError("任务文件夹不在了", str(d))
    m = Manifest.load(d)
    restored_logs: list[str] = []
    img_src_dir = Path(t["image_source"]) if t.get("image_source") else None
    fallback_src_dir = d.parent.parent / "01_右键表格_下载素材库" / "图片"
    for im in (m.images or []):
        rel = _posix(im.get("file") or "")
        if not rel or (d / rel).is_file():
            continue
        src_name = im.get("src") or ""
        if not src_name:
            continue
        for sdir in (img_src_dir, fallback_src_dir):
            if sdir and sdir.is_dir() and (sdir / src_name).is_file():
                dst = d / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(sdir / src_name, dst)
                im.pop("exists", None)
                restored_logs.append(f"{im.get('p') or '?'} 从原图库自动恢复到 {rel}")
                break
    logs = restored_logs + sync_with_disk(d, m)
    from . import pipeline
    migrated = pipeline.migrate_bridge_acct_53(m)
    if migrated:
        pipeline.stamp_waits(m)
    if logs or migrated:
        m.save(d)
    return d, m, logs
