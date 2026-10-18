# -*- coding: utf-8 -*-
"""描述语作业：看图写描述语、看图测量；扫问题句；成品提示词预览。

0923：不再自动判空镜（太慢）。空镜由用户自己在面板上标。

kind=describe / scan_problems 都不占账号。preview_prompt 是普通函数，给 HTTP 用。
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .. import paths, tasks as taskmod, events, config
from ..errors import OranteError
from ..jobs import register
from ..models import Manifest, IMG_ROLES
from ..scheduler import JobCancelled
from . import ai, aikeys, broll, prompts, lens, tails

paths.add_legacy_to_sys_path()
from longtik_images import parse_sidecar_text  # type: ignore  # noqa: E402


def _cfg(key, default=None):
    try:
        return config.get(key, default)
    except Exception:
        from ..config import DEFAULTS
        return DEFAULTS.get(key, default)


def _task_dir(tid: str) -> Path:
    t = taskmod.get(tid)
    if not t.get("dir"):
        raise OranteError("这个任务还没准备", "先在任务面板点「准备」。")
    d = Path(t["dir"])
    if not d.is_dir():
        raise OranteError("任务文件夹不在了", str(d))
    return d


def _parse_seg_n(s) -> int:
    raw = str(s or "").strip()
    if raw.lower().startswith("s"):
        raw = raw[1:]
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise OranteError(f"没有段 {s}", "段号是 s001 或 1。")
    if n < 1:
        raise OranteError(f"没有段 {s}")
    return n


def _voice_line(parsed: dict, raw: str) -> str:
    """sidecar 第二行音色：优先原文【音色】行，否则从 dict 拼回。"""
    for ln in (raw or "").splitlines():
        s = ln.strip()
        if s.startswith("【音色】"):
            return s
    v = parsed.get("voice") or {}
    if isinstance(v, str):
        return v.strip()
    if v:
        return "【音色】%s|%s|%s|%s|%s" % (
            v.get("ethnic", ""), v.get("gender", ""),
            v.get("age", ""), v.get("tone", ""), v.get("accent", ""))
    return ""


def _desc_line(parsed: dict, raw: str) -> str:
    """第一行描述语（含机读标签）。parse_sidecar_text 会把标签剥掉，这里补回去。"""
    for ln in (raw or "").splitlines():
        s = ln.strip()
        if s and not s.startswith("【音色】"):
            return s
    desc = (parsed.get("desc") or "").strip()
    if not desc:
        return ""
    eye = parsed.get("eye") or "闭眼"
    scene = parsed.get("scene") or "常规"
    if "【" not in desc[-24:]:
        desc = desc + "【%s|%s】" % (eye, scene)
    return desc


def _apply_sidecar(im: dict, raw: str) -> None:
    parsed = parse_sidecar_text(raw)
    im["desc"] = _desc_line(parsed, raw)
    im["voice"] = _voice_line(parsed, raw)
    im["eye"] = parsed.get("eye") or im.get("eye") or "闭眼"
    im["scene"] = parsed.get("scene") or im.get("scene") or "常规"
    # 机读标签判出的空镜先记下；文件搬迁由空镜判定那一步做
    if parsed.get("role") == "空镜":
        im["role"] = "空镜"
        im["eye"] = "空镜"


def preview_prompt(task_id: str, s) -> str:
    """成品提示词：要发给 Flow/Grok 的完整字符串。"""
    d = _task_dir(task_id)
    m = Manifest.load(d)
    n = _parse_seg_n(s)
    seg = m.seg(n)
    if not seg:
        raise OranteError(f"没有段 s{n:03d}", "先拆段。")
    return prompts.build_final_prompt(m, seg)


def update_image(task_id: str, p: str, fields: dict) -> dict:
    """改一张图的 desc / voice / role；role 变了就搬家。

    by="user" 时把 desc/voice 标成手改（desc_manual / voice_manual）。
    也可以直接传 desc_manual / voice_manual（右键「恢复为自动生成」清掉）。
    """
    p = (p or "").strip()
    d = _task_dir(task_id)
    m = Manifest.load(d)
    im = m.image(p)
    if not im:
        raise OranteError(f"没有图片 {p}")
    fields = fields or {}
    by = fields.get("by")
    allowed = {k: fields[k] for k in ("desc", "voice", "role") if k in fields}
    flags = {k: fields[k] for k in ("desc_manual", "voice_manual") if k in fields}
    if not allowed and not flags:
        return im
    if "role" in allowed:
        role = allowed["role"]
        if role not in IMG_ROLES:
            raise OranteError(f"role 只能是 主体 或 空镜，收到 {role!r}")
        broll.relocate(d, im, role)
        if role == "空镜":
            im["eye"] = "空镜"
        elif im.get("eye") == "空镜":
            im["eye"] = "闭眼"
    if "desc" in allowed:
        im["desc"] = allowed["desc"] if allowed["desc"] is not None else ""
        if by == "user":
            im["desc_manual"] = True
            im.pop("desc_stale", None)
    if "voice" in allowed:
        im["voice"] = allowed["voice"] if allowed["voice"] is not None else ""
        if by == "user":
            im["voice_manual"] = True
    if "desc_manual" in flags:
        im["desc_manual"] = bool(flags["desc_manual"])
        if not im["desc_manual"]:
            im.pop("desc_stale", None)
    if "voice_manual" in flags:
        im["voice_manual"] = bool(flags["voice_manual"])
    if "desc" in allowed or "voice" in allowed:
        raw = (im.get("desc") or "").rstrip()
        if im.get("voice"):
            raw = raw + "\n" + str(im["voice"]).strip()
        parsed = parse_sidecar_text(raw)
        if parsed.get("eye"):
            # 手改 role=空镜 时不要被 sidecar 的「主体」冲掉
            if im.get("role") != "空镜":
                im["eye"] = parsed["eye"]
            elif parsed.get("eye") == "空镜":
                im["eye"] = "空镜"
        if parsed.get("scene"):
            im["scene"] = parsed["scene"]
        if "voice" not in allowed and parsed.get("voice"):
            im["voice"] = _voice_line(parsed, raw)
    m.save(d)
    if "desc" in allowed or "voice" in allowed:
        taskmod.publish_counts(task_id, d, m)
    from . import images as imgmod
    return imgmod.annotate_images(d, [im])[0]


def missing_subject_ps(m: Manifest, *, include_pending: bool = False) -> list[str]:
    """还没有描述语的主体图编号。待审图默认不算（还没进图库）。"""
    out = []
    for im in m.images or []:
        if im.get("role") != "主体":
            continue
        st = im.get("status") or ""
        if st == "missing":
            continue
        if st == "pending_review" and not include_pending:
            continue
        if not str(im.get("desc") or "").strip():
            out.append(str(im.get("p") or "?"))
    return out


def is_desc_manual(im: dict) -> bool:
    """这张图的描述语/音色是用户手改的，自动写要跳过。"""
    return bool(im and (im.get("desc_manual") or im.get("voice_manual")))


SKIP_MANUAL_LOG = "· {p} 描述语是手改的，跳过（要重写请在卡片上点\"恢复自动\"）"


def describe_one(job, m: Manifest, d: Path, im: dict, gemini=None, *,
                 save: bool = True, force: bool = False) -> dict:
    """给一张主体图写描述语 + 看图测量（不判空镜）。补图 / 替补新图之后自动调用，
    用户不用再手动点「写描述语」。返回 measure；Gemini 没配好会抛 OranteError。

    desc_manual 为真且 force 为假 → 不调 Gemini、不改内容，只打一行跳过日志。
    """
    p = im.get("p") or "?"
    if is_desc_manual(im) and not force:
        job.log(SKIP_MANUAL_LOG.format(p=p))
        return im.get("measure") or {}
    if gemini is None:
        gemini = ai.client(job)
    desc_prompt = prompts.get_block("desc")
    if not isinstance(desc_prompt, str) or not desc_prompt.strip():
        raise OranteError("描述语提示词是空的", "到多模态面板恢复默认。")
    img_path = d / (im.get("file") or "")
    if not img_path.is_file():
        raise OranteError(f"{p} 的文件不在盘上", im.get("file") or "")
    raw = gemini.ask_image(desc_prompt + tails.POSE_TRAILER, img_path)
    job.check()
    pose, raw = tails.peel_pose(raw)
    _apply_sidecar(im, raw)
    if pose:
        im["pose"] = pose
    meas = lens.measure(img_path, gemini=gemini)
    job.check()
    im["measure"] = meas
    if save:
        m.save(d)
    bits = f"{meas.get('shot_size') or '—'}·{meas.get('facing') or '—'}·{meas.get('people') or 0}人"
    pose_bit = (pose or {}).get("text") or ""
    extra = f"；姿态 {pose_bit}" if pose_bit else ""
    # Gemini 写的不算手改；force 重写之后锁也解开
    im["desc_manual"] = False
    im.pop("desc_stale", None)
    job.log(f"  · {p} 描述语已自动写好；测量 {bits}{extra}（姿态与描述语同一次调用）")
    return meas


def describe_file(job, d: Path, path, gemini=None) -> dict:
    """给任意一张图写描述语。复用 describe_one 的提示词和解析，对象是文件不是图池。"""
    path = Path(path)
    if gemini is None:
        gemini = ai.client(job)
    desc_prompt = prompts.get_block("desc")
    if not isinstance(desc_prompt, str) or not desc_prompt.strip():
        raise OranteError("描述语提示词是空的", "到多模态面板恢复默认。")
    if not path.is_file():
        raise OranteError("要写描述语的文件不在", str(path))
    raw = gemini.ask_image(desc_prompt + tails.POSE_TRAILER, path)
    if job is not None:
        job.check()
    pose, raw = tails.peel_pose(raw)
    im = {
        "p": path.stem, "file": str(path),
        "desc": "", "voice": "", "eye": "闭眼", "scene": "常规",
    }
    _apply_sidecar(im, raw)
    if pose:
        im["pose"] = pose
    return {
        "desc": im.get("desc") or "",
        "voice": im.get("voice") or "",
        "pose": pose or {},
        "eye": im.get("eye") or "",
        "scene": im.get("scene") or "",
    }


@register("describe")
def describe(job, force: bool = False, ps=None):
    """对每张 desc 为空的图：写描述语 → 看图测量。

    0923：不再问空镜那一题（用户自己标）。
    desc_manual 的图默认跳过；force=True 才连手改过的一起重写。
    ps 有值时只处理这些编号（卡片上「按新图重写」）。
    """
    if not job.task_id:
        raise OranteError("describe 需要 task_id")
    force = bool(force)
    want = None
    if ps:
        want = {str(x).strip() for x in ps if str(x).strip()}
    t = taskmod.get(job.task_id)
    d, m, sync_log = broll.load_synced(job.task_id)
    for ln in sync_log:
        job.log(ln)
    # 没接口在进线程池之前就抛，前端能拿到 OranteError 原话
    desc_prompt = prompts.get_block("desc")
    if not isinstance(desc_prompt, str) or not desc_prompt.strip():
        raise OranteError("描述语提示词是空的", "到多模态面板恢复默认。")

    from . import simple86 as _s86
    no_copy = (not _s86.has_copy(t)) and (not _s86.has_copy(m))
    if no_copy:
        # 不说话的空镜头：空镜也要能写描述语，否则后面没提示词可出片。
        subjects = [im for im in m.images
                    if (im.get("status") or "") not in ("missing", "blocked")]
    else:
        subjects = [im for im in m.images if im.get("role") == "主体"]
    if want is not None:
        subjects = [im for im in subjects if str(im.get("p") or "") in want]
    if not subjects:
        if no_copy:
            raise OranteError("没有图片", "先准备至少一张图再写描述语。空镜也可以。")
        raise OranteError("没有主体图", "先准备几张主体图再写描述语。")
    skipped_manual = []
    todo = []
    n_had = 0
    for im in subjects:
        p = str(im.get("p") or "?")
        has = bool(str(im.get("desc") or "").strip())
        if is_desc_manual(im) and not force:
            skipped_manual.append(p)
            job.log(SKIP_MANUAL_LOG.format(p=p))
            continue
        if has and not (force and is_desc_manual(im)):
            n_had += 1
            continue
        todo.append(im)
    job.log(f"要写描述语的图 {len(todo)} / {len(subjects)} 张" + ("" if no_copy else "主体")
            + (f"（已有 {n_had} 张，跳过）" if n_had else "")
            + (f"；手改跳过 {len(skipped_manual)} 张" if skipped_manual else ""))
    job.set_progress(phase="describe", done=n_had, total=len(subjects), current="")
    if not todo:
        if want is None:
            _mark_described(t["id"], m)
        return {"described": 0, "broll": 0, "total": len(m.images), "skipped": n_had,
                "skipped_manual": skipped_manual}

    n_keys = len(aikeys.list_all(enabled_only=True))
    if not n_keys:
        raise OranteError(
            "还没有可用的多模态接口",
            "到「设置 → AI 接口」里加一条（Gemini / OpenAI 兼容 / 本地 Ollama 都行）。")
    gemini = ai.client(job)
    n_workers = ai.workers(int(_cfg("ai.max_workers", 16) or 16))
    n_workers = max(1, min(n_workers, len(todo)))
    job.log(f"本轮用 {n_keys} 条接口、{n_workers} 个线程")
    save_lock = threading.Lock()
    log_lock = threading.Lock()
    counts = {"described": 0, "broll": 0}

    def log(msg: str) -> None:
        with log_lock:
            job.log(msg)

    def one(im: dict) -> None:
        job.check()
        p = im.get("p") or "?"
        rel = im.get("file") or ""
        img_path = d / rel
        if not img_path.is_file():
            raise OranteError(f"{p} 的文件不在盘上", rel)
        raw = gemini.ask_image(desc_prompt + tails.POSE_TRAILER, img_path)
        job.check()
        pose, raw = tails.peel_pose(raw)
        _apply_sidecar(im, raw)
        if pose:
            im["pose"] = pose

        # 0923：不再自动判空镜（那是一张图一次额外的 Gemini 调用，太慢）。
        # 哪张是空镜由用户自己在面板上标；这里只认已经标好的 role。
        moved = bool(im.get("role") == "空镜")
        if moved:
            im["role"] = "空镜"
            im["eye"] = "空镜"

        meas = lens.measure(img_path, gemini=gemini)
        job.check()

        with save_lock:
            job.check()
            if moved:
                broll.relocate(d, im, "空镜")
            im["measure"] = meas
            # Gemini 写的不算手改；force 重写之后锁也解开
            im["desc_manual"] = False
            im.pop("desc_stale", None)
            m.save(d)
            counts["described"] += 1
            if moved:
                counts["broll"] += 1
            job.set_progress(done=n_had + counts["described"], current=p)
        bits = f"{meas.get('shot_size') or '—'}·{meas.get('facing') or '—'}·{meas.get('people') or 0}人"
        pose_bit = ((im.get("pose") or {}).get("text") or "")
        extra = f"；姿态 {pose_bit}" if pose_bit else ""
        if moved:
            log(f"{p} 标着空镜，已移到 {im['file']}；测量 {bits}{extra}")
        else:
            log(f"{p} 描述语已写；测量 {bits}{extra}（姿态与描述语同一次调用）")

    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futs = [pool.submit(one, im) for im in todo]
        for fut in as_completed(futs):
            try:
                fut.result()
            except JobCancelled:
                raise
            except OranteError as e:
                errors.append(e.message)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{type(e).__name__}: {e}")
    if errors:
        raise OranteError(
            f"写描述语有 {len(errors)} 张失败",
            "；".join(errors[:5]))
    if want is None:
        _mark_described(t["id"], m)
    n_pose = sum(1 for im in m.images if isinstance(im.get("pose"), dict) and im["pose"].get("text"))
    job.log(f"✓ 写了 {counts['described']} 张，其中空镜 {counts['broll']} 张；"
            f"姿态 {n_pose} 张；多模态累计 {ai.calls()} 次（姿态未另开调用）")
    if skipped_manual:
        job.log("  · 手改跳过：" + "、".join(skipped_manual))
    return {"described": counts["described"], "broll": counts["broll"],
            "total": len(m.images), "pose": n_pose, "ai_calls": ai.calls(),
            "skipped": n_had, "skipped_manual": skipped_manual}


POSE_ONLY_ASK = (
    "看这张讲道照片里的人物。请用 JSON 回答，不要 markdown、不要解释：\n"
    '{"head":"头的朝向","eyes":"视线落点","hands":"双手位置与动作",'
    '"body":"身姿","expr":"表情","text":"一句话概括人物此刻的进入状态"}\n'
    "各字段用中文短句。"
)


def fill_missing_poses(job, m: Manifest, d: Path, gemini=None) -> int:
    """只给还没有 pose 的主体图补姿态。已有描述语的图不会重写描述语。

    新写描述语走 describe_one，姿态已经同一次调用带出来，不会走到这里。
    """
    if gemini is None:
        gemini = ai.client(job)
    n = 0
    for im in m.images:
        if im.get("role") != "主体":
            continue
        if isinstance(im.get("pose"), dict) and str(im["pose"].get("text") or "").strip():
            continue
        img_path = d / (im.get("file") or "")
        if not img_path.is_file():
            continue
        job.check()
        raw = gemini.ask_image(POSE_ONLY_ASK, img_path, json_mode=True)
        pose = tails.norm_pose(tails.parse_json_obj(raw) or {})
        if pose.get("text") or pose.get("head"):
            im["pose"] = pose
            n += 1
            job.log(f"  · {im.get('p')} 补了姿态：{pose.get('text') or pose.get('head')}")
    if n:
        m.save(d)
    return n


def _mark_described(tid: str, m: Manifest) -> None:
    left = missing_subject_ps(m, include_pending=True)
    if left:
        raise OranteError(
            f"还有 {len(left)} 张主体图没有描述语",
            "、".join(left[:8]))
    # 只把低于 described 的任务推进到 described。已经 arranged / generating
    # 的不许拽回去——靠 set_status 的单调性，补跑描述语本身仍算成功。
    taskmod.set_status(tid, "described")
    events.emit("tasks.changed")


@register("scan_problems")
def scan_problems(job):
    """让 Gemini 扫当前任务文案，新句子加进问题库。"""
    if not job.task_id:
        raise OranteError("scan_problems 需要 task_id")
    t = taskmod.get(job.task_id)
    d = _task_dir(job.task_id)
    m = Manifest.load(d)
    text = ((m.copy or {}).get("text") if isinstance(m.copy, dict) else "") or t.get("copy") or ""
    if not str(text).strip():
        raise OranteError("这份任务没有文案", "先在任务面板填文案并准备。")

    before = prompts.get_block("problem") or {}
    old = {
        _nospace(p.get("value") or "").lower()
        for p in (before.get("patterns") or [])
        if isinstance(p, dict)
    }
    job.log("让 Gemini 扫文案里的问题句…")
    found = prompts.gemini_scan_problems(text)
    after = prompts.get_block("problem") or {}
    added = []
    for p in (after.get("patterns") or []):
        if not isinstance(p, dict):
            continue
        key = _nospace(p.get("value") or "").lower()
        if key and key not in old:
            added.append(p.get("value") or "")
            old.add(key)
    job.log(f"扫到 {len(found)} 句，新加 {len(added)} 句")
    return {"added": added, "found": found}


def _nospace(s: str) -> str:
    import re
    return re.sub(r"\s+", "", s or "")
