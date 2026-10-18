# -*- coding: utf-8 -*-
"""尾帧提取器 + 锚点/接片角色分配。

接片不是额外插进去的过渡镜头，就是文案里正常的一段，只是多了
「首帧 = 上一段尾帧、尾帧 = 下一段锚点图」两个约束。
每段一个 role：anchor / bridge。默认交替，最后一段强制 anchor；
偶数段时末尾排成 bridge, anchor。
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from ..errors import OranteError
from ..jobs import register
from ..models import Manifest, sha1_file
from .procutil import run_quiet

# 探针 C 定的范围：倒数第 3~8 帧里挑拉普拉斯方差最大的那一格
FROM_END_LO = 3
FROM_END_HI = 8
# 抽帧时多看 1~10，方便探针和异常时回退
FROM_END_SCAN = 10
JPEG_QUALITY = 95
SHARP_RATIO = 0.60          # 低于本任务尾帧锐度中位数的 60% 才标 sharp:false
# 单图起于尾帧：糊到这个数以下就换下一候选。报告里写实测分布。
TAIL_MIN_SHARP = 60.0
# 覆盖 clips/_tails/sNNN.jpg 之前，旧图挪到 _旧/，最多留这么多份
TAIL_OLD_KEEP = 3
# 尾帧接龙反复修，3 份不够回溯
TAIL_OLD_KEEP_CHAIN = 10
# 接龙「严重穿帮」：灰度标准差低于这个 → 几乎纯色/黑场
TAIL_SEVERE_STD = 8.0
# 所有候选锐度都低于 TAIL_MIN_SHARP/3 才算糊到没法当首帧
TAIL_SEVERE_SHARP = TAIL_MIN_SHARP / 3.0

POSE_KEYS = ("head", "eyes", "hands", "body", "expr", "text")

# ② 写描述语时顺带要姿态：拼在用户描述语提示词后面，同一次 ask_image
POSE_TRAILER = (
    "\n\n——以上是描述语正文。请严格按上面的格式先写描述语和【音色】行，"
    "不要改成 JSON，不要省略。"
    "全部写完之后另起一行，只多输出下面这一行（必须是合法 JSON，字段用中文短句）：\n"
    'POSE_JSON={"head":"头的朝向，如略偏左","eyes":"视线落点","hands":"双手位置与动作",'
    '"body":"身姿","expr":"表情","text":"一句话概括人物此刻的进入状态"}'
)

TAIL_ASK = (
    "这是一段讲道视频靠近结尾的一帧。请用 JSON 回答，不要 markdown、不要解释：\n"
    "{\n"
    '  "face_ok": true或false,\n'
    '  "in_frame": true或false,\n'
    '  "pose": {\n'
    '    "head": "头的朝向",\n'
    '    "eyes": "视线落点",\n'
    '    "hands": "双手位置与动作",\n'
    '    "body": "身姿",\n'
    '    "expr": "表情",\n'
    '    "text": "一句话概括"\n'
    "  }\n"
    "}\n\n"
    "face_ok：脸是否完整、没有变形、五官清晰可见。\n"
    "in_frame：人物的**头部**是否完整地在画面内（不是问身体是否完整）。\n"
    "近景、特写、只拍到上半身，都算 in_frame = true。\n"
    "只有当头顶/下巴/半张脸被画幅切掉，或者人几乎已经移出画面时，才算 false。\n"
    "pose 各字段用中文短句。"
)

GAP_ASK = (
    "两张图分别是讲道视频相邻两段的结束状态（第一张）和目标进入状态（第二张）。"
    "请用 JSON 回答，不要 markdown、不要解释：\n"
    '{"gap_score": 0到5的整数, "gap_why": "一句话说明要从第一张过渡到第二张需要改什么"}\n'
    "0=几乎一样，1-2=小动作（手、视线），3=中等（转身、换手），"
    "4-5=大动作、走位或景别差很多。"
)

_POSE_LINE = re.compile(r"POSE_JSON\s*=\s*(\{.*\})", re.S)
_JSON_OBJ = re.compile(r"\{.*\}", re.S)


# ---------------------------------------------------------------- 角色
def assign_roles(segments: list[dict]) -> list[dict]:
    """给每段写 role=anchor|bridge，并给接片段备好空的 bridge 字典。

    旧 manifest 里 role 是 主体/空镜，挪到 img_role，不丢。
    段数 < 3 时没法构成「上一段尾帧 + 下一段锚点」，全部当 anchor。
    """
    n = len(segments or [])
    for i, sg in enumerate(segments or []):
        old = sg.get("role")
        if old in ("主体", "空镜"):
            sg["img_role"] = old
        if n < 3:
            sg["role"] = "anchor"
        elif i == n - 1:
            sg["role"] = "anchor"          # 最后一段强制锚点
        elif n % 2 == 0 and i == n - 2:
            sg["role"] = "bridge"          # 偶数段：末尾排成 bridge, anchor
        else:
            sg["role"] = "anchor" if i % 2 == 0 else "bridge"
        if sg["role"] == "bridge":
            prev = segments[i - 1] if i > 0 else None
            nxt = segments[i + 1] if i + 1 < n else None
            br = sg.get("bridge") if isinstance(sg.get("bridge"), dict) else {}
            br.setdefault("from_seg", int(prev["s"]) if prev else None)
            br.setdefault("to_image", (nxt or {}).get("image") or "")
            br.setdefault("tail_frame", None)
            br.setdefault("tail_pose", None)
            br.setdefault("tail_checks", None)
            br.setdefault("gap_score", None)
            br.setdefault("gap_why", None)
            br.setdefault("auto_swapped_from", None)
            sg["bridge"] = br
        else:
            # 锚点段不留空的 bridge 字典
            if isinstance(sg.get("bridge"), dict) and not sg["bridge"].get("tail_frame"):
                sg.pop("bridge", None)
    return segments


def ensure_roles(m: Manifest) -> None:
    assign_roles(m.segments)


# ---------------------------------------------------------------- 姿态 JSON
def _as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v or "").strip().lower()
    return s in ("true", "1", "yes", "y", "是", "对", "完整", "ok", "good")


def _as_int(v, lo=0, hi=5, default=None):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def norm_pose(d) -> dict:
    src = d if isinstance(d, dict) else {}
    out = {}
    for k in POSE_KEYS:
        out[k] = str(src.get(k) or "").strip()
    return out


def parse_json_obj(text: str) -> dict | None:
    raw = (text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.M).strip()
    m = _JSON_OBJ.search(raw)
    if not m:
        return None
    blob = m.group(0)
    try:
        d = json.loads(blob)
        return d if isinstance(d, dict) else None
    except Exception:
        blob2 = blob.replace("true", "true").replace("false", "false")
        blob2 = re.sub(r"，", ",", blob2)
        try:
            d = json.loads(blob2)
            return d if isinstance(d, dict) else None
        except Exception:
            return None


def peel_pose(raw: str) -> tuple[dict | None, str]:
    """从描述语回复里剥出 POSE_JSON，剩下的还给 sidecar 解析。"""
    text = raw or ""
    m = _POSE_LINE.search(text)
    if m:
        pose = parse_json_obj(m.group(1))
        rest = (text[:m.start()] + text[m.end():]).strip()
        return (norm_pose(pose) if pose else None), rest
    # 模型有时把整段回成一个带 pose 字段的 JSON
    d = parse_json_obj(text)
    if d and isinstance(d.get("pose"), dict):
        return norm_pose(d["pose"]), ""
    if d and all(k in d for k in ("head", "eyes", "text")):
        return norm_pose(d), ""
    return None, text


# ---------------------------------------------------------------- ffmpeg
def _ffmpeg() -> str:
    from . import checks
    exe = checks.find_ffmpeg() or ""
    if not exe:
        raise OranteError("找不到 ffmpeg", checks.FFMPEG_FIX_HINT)
    return exe


_INFO_CACHE: dict = {}


def video_info(path: Path) -> dict:
    """宽、高、帧率、时长、总帧数。全部走 run_quiet。"""
    path = Path(path)
    try:
        key = (str(path), path.stat().st_mtime, path.stat().st_size)
    except OSError:
        key = (str(path), 0, 0)
    if key in _INFO_CACHE:
        return _INFO_CACHE[key]
    exe = _ffmpeg()
    ffprobe = exe.replace("ffmpeg.exe", "ffprobe.exe").replace("ffmpeg", "ffprobe")
    if not Path(ffprobe).is_file():
        ffprobe = exe  # 有的发行版 ffmpeg 也能 -f ffprobe，下面走 ffmpeg -i 兜底
        cmd = [exe, "-hide_banner", "-i", str(path)]
        r = run_quiet(cmd, timeout=30)
        err = (r.stderr or b"").decode("utf-8", "ignore")
        w = h = 0
        fps = 24.0
        dur = 0.0
        m = re.search(r"(\d{2,5})x(\d{2,5})", err)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
        m = re.search(r"(\d+(?:\.\d+)?)\s*fps", err)
        if m:
            fps = float(m.group(1))
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
        if m:
            dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        nframes = int(round(dur * fps)) if dur and fps else 0
        info = {"width": w, "height": h, "fps": fps, "duration": dur, "nframes": nframes}
        _INFO_CACHE[key] = info
        return info

    cmd = [ffprobe, "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=width,height,r_frame_rate,nb_frames,avg_frame_rate",
           "-show_entries", "format=duration", "-of", "json", str(path)]
    r = run_quiet(cmd, timeout=30)
    if r.returncode != 0:
        raise OranteError("ffprobe 读视频失败", (r.stderr or b"").decode("utf-8", "ignore")[:200])
    try:
        data = json.loads((r.stdout or b"").decode("utf-8", "ignore") or "{}")
    except Exception as e:
        raise OranteError("ffprobe 输出不是 JSON", str(e))
    st = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}
    w = int(st.get("width") or 0)
    h = int(st.get("height") or 0)
    fps = _parse_fps(st.get("avg_frame_rate") or st.get("r_frame_rate") or "24/1")
    try:
        dur = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        dur = 0.0
    try:
        nframes = int(st.get("nb_frames") or 0)
    except (TypeError, ValueError):
        nframes = 0
    if nframes <= 0 and dur > 0 and fps > 0:
        nframes = int(round(dur * fps))
    info = {"width": w, "height": h, "fps": fps, "duration": dur, "nframes": nframes}
    _INFO_CACHE[key] = info
    return info


def _parse_fps(s) -> float:
    t = str(s or "24").strip()
    if "/" in t:
        a, b = t.split("/", 1)
        try:
            return float(a) / float(b) if float(b) else 24.0
        except (TypeError, ValueError, ZeroDivisionError):
            return 24.0
    try:
        return float(t) or 24.0
    except (TypeError, ValueError):
        return 24.0


def gray_std(path: Path) -> float:
    """整张图灰度像素标准差。纯色/黑场接近 0。"""
    from PIL import Image
    im = Image.open(path).convert("L")
    w, h = im.size
    if max(w, h) > 480:
        im.thumbnail((480, 480), Image.BILINEAR)
        w, h = im.size
    raw = im.tobytes()
    n = len(raw)
    if n <= 1:
        return 0.0
    mean = sum(raw) / n
    var = sum((p - mean) ** 2 for p in raw) / n
    return var ** 0.5


def laplacian_var(path: Path) -> float:
    """拉普拉斯方差（越大越锐）。纯 PIL，不用 numpy / cv2。"""
    from PIL import Image
    im = Image.open(path).convert("L")
    w, h = im.size
    # 长边压到 480：相对排序不变，纯 Python 卷积才跑得动
    if max(w, h) > 480:
        im.thumbnail((480, 480), Image.BILINEAR)
        w, h = im.size
    pix = im.load()
    acc = acc2 = 0.0
    n = 0
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            v = (pix[x, y - 1] + pix[x, y + 1] + pix[x - 1, y] + pix[x + 1, y]
                 - 4 * pix[x, y])
            acc += v
            acc2 += v * v
            n += 1
    if n <= 0:
        return 0.0
    mean = acc / n
    return acc2 / n - mean * mean


def extract_from_end(video: Path, k: int, dest: Path) -> Path:
    """抽出倒数第 k 帧（k=1 是最后一帧）。分辨率与视频相同。"""
    exe = _ffmpeg()
    info = video_info(video)
    fps = info["fps"] or 24.0
    # -sseof 以文件尾为原点；取该帧中点，避免踩到文件尽头
    sseof = -((k - 0.5) / fps)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # 先无损 PNG，再转 JPEG 95，避免 ffmpeg mjpeg 量化把锐度比歪
    fd, tmp = tempfile.mkstemp(suffix=".png")
    import os
    os.close(fd)
    tmp_p = Path(tmp)
    try:
        cmd = [exe, "-hide_banner", "-loglevel", "error",
               "-sseof", f"{sseof:.4f}", "-i", str(video),
               "-frames:v", "1", "-an", "-sn", "-dn",
               "-y", str(tmp_p)]
        r = run_quiet(cmd, timeout=60)
        if r.returncode != 0 or not tmp_p.is_file() or tmp_p.stat().st_size < 100:
            err = (r.stderr or b"").decode("utf-8", "ignore")[:200]
            raise OranteError(f"抽倒数第 {k} 帧失败", err or str(video))
        from PIL import Image
        im = Image.open(tmp_p).convert("RGB")
        im.save(dest, "JPEG", quality=JPEG_QUALITY)
    finally:
        try:
            tmp_p.unlink()
        except Exception:
            pass
    return dest


def scan_tail_candidates(video: Path, n: int = FROM_END_SCAN, work: Path | None = None) -> list[dict]:
    """抽倒数 1..n 帧，算锐度。返回 [{k, path, sharp}, ...]，k=1 是最后一帧。"""
    video = Path(video)
    work = Path(work) if work else video.parent / "_tail_scan"
    work.mkdir(parents=True, exist_ok=True)
    out = []
    for k in range(1, n + 1):
        dest = work / f"from_end_{k:02d}.jpg"
        try:
            extract_from_end(video, k, dest)
            sh = laplacian_var(dest)
        except Exception as e:  # noqa: BLE001
            out.append({"k": k, "path": None, "sharp": 0.0, "error": str(e)[:120]})
            continue
        out.append({"k": k, "path": dest, "sharp": sh, "error": None})
    return out


def pick_sharpest(cands: list[dict], lo: int = FROM_END_LO, hi: int = FROM_END_HI,
                  min_sharp: float | None = None) -> dict | None:
    """在 [lo, hi]（含）里挑锐度最高的；范围内全失败就在全部成功的里面挑。

    Laplacian 方差低于 min_sharp（默认 TAIL_MIN_SHARP）的跳过。
    全部候选都不及格 → 仍返回最锐的那张，并打 below_min=True。
    """
    # 锐度≈0 的多半是文件尽头抽出的黑帧，不参与挑选
    if min_sharp is None:
        min_sharp = TAIL_MIN_SHARP
    ok = [c for c in cands if c.get("path") and float(c.get("sharp") or 0) > 1.0]
    band = [c for c in ok if lo <= int(c["k"]) <= hi]
    pool = band or ok
    if not pool:
        return None
    good = [c for c in pool if float(c.get("sharp") or 0) >= float(min_sharp)]
    if good:
        return max(good, key=lambda c: float(c.get("sharp") or 0))
    picked = dict(max(pool, key=lambda c: float(c.get("sharp") or 0)))
    picked["below_min"] = True
    return picked


# ---------------------------------------------------------------- 路径
def tails_dir(task_dir: Path) -> Path:
    p = Path(task_dir) / "clips" / "_tails"
    p.mkdir(parents=True, exist_ok=True)
    return p


def tail_path(task_dir: Path, s: int) -> Path:
    return tails_dir(task_dir) / f"s{int(s):03d}.jpg"


def old_tails_dir(task_dir: Path) -> Path:
    p = tails_dir(task_dir) / "_旧"
    p.mkdir(parents=True, exist_ok=True)
    return p


def archive_old_tail(dest: Path, keep: int = TAIL_OLD_KEEP) -> Path | None:
    """覆盖 sNNN.jpg 之前，把旧文件挪到 clips/_tails/_旧/sNNN-<时间戳>.jpg。

    同一段最多留 keep 份，多的删最旧的。没有旧文件就什么都不做。
    """
    dest = Path(dest)
    if not dest.is_file():
        return None
    old_dir = dest.parent / "_旧"
    old_dir.mkdir(parents=True, exist_ok=True)
    # 微秒进文件名，同一秒归档多次也不会撞，按名字排≈按时间排
    ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    suffix = dest.suffix or ".jpg"
    target = old_dir / ("%s-%s%s" % (dest.stem, ts, suffix))
    n = 1
    while target.exists():
        n += 1
        target = old_dir / ("%s-%s-%d%s" % (dest.stem, ts, n, suffix))
    try:
        dest.replace(target)
    except OSError:
        try:
            shutil.move(str(dest), str(target))
        except OSError:
            return None
    existing = [p for p in old_dir.glob(dest.stem + "-*") if p.is_file()]
    existing.sort(key=lambda p: (p.stat().st_mtime, p.name))
    while len(existing) > max(1, int(keep)):
        dead = existing.pop(0)
        try:
            dead.unlink()
        except OSError:
            pass
    return target


def stamp_tail_identity(br: dict, tail_file: Path | None) -> None:
    """给 bridge 写 tail_ahash（16×16 aHash 十六进制）和 tail_at（ISO）。

    路径是固定的 sNNN.jpg，覆盖之后看不出换没换；指纹才是身份。
    算不出就留空串，cascade 会按「缺失」打回。
    """
    from . import verify as verifymod
    hx = ""
    if tail_file and Path(tail_file).is_file():
        bits = verifymod.ahash_image(Path(tail_file))
        if bits is not None:
            hx = verifymod.ahash_hex(bits)
    br["tail_ahash"] = hx
    br["tail_at"] = datetime.now().isoformat(timespec="seconds")


def heads_dir(task_dir: Path) -> Path:
    p = Path(task_dir) / "clips" / "_heads"
    p.mkdir(parents=True, exist_ok=True)
    return p


def head_path(task_dir: Path, s: int) -> Path:
    return heads_dir(task_dir) / f"s{int(s):03d}.jpg"


def extract_head(task_dir, m: Manifest, sg: dict) -> Path | None:
    """抽出接片第一帧，存 clips/_heads/sNNN.jpg。给检查台三格用。"""
    clip = clip_of(task_dir, m, sg)
    if not clip or not Path(clip).is_file():
        return None
    dest = head_path(task_dir, int(sg.get("s") or 0))
    exe = _ffmpeg()
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-ss", "0",
           "-i", str(clip), "-frames:v", "1", "-an", "-sn", "-dn",
           "-y", str(dest)]
    r = run_quiet(cmd, timeout=30)
    if r.returncode != 0 or not dest.is_file() or dest.stat().st_size < 100:
        return None
    return dest


def clip_of(task_dir: Path, m: Manifest, sg: dict) -> Path | None:
    d = Path(task_dir)
    takes = sg.get("takes") or []
    if takes:
        rel = (takes[-1].get("file") or "").replace("\\", "/")
        if rel:
            p = d / rel
            if p.is_file():
                return p
    name = m.clip_name(sg)
    p = d / "clips" / name
    return p if p.is_file() else None


# ---------------------------------------------------------------- 多模态（一次三问）
def _client(job=None):
    from . import ai
    return ai.client(job, tag="tails")


def inspect_tail(img: Path, gemini=None, job=None) -> dict:
    """一次调用：face_ok / in_frame / pose。has_person 有就带上（缺省 = 未知）。"""
    gemini = gemini or _client(job)
    raw = gemini.ask_image(TAIL_ASK, img, json_mode=True)
    d = parse_json_obj(raw) or {}
    pose_src = d.get("pose") if isinstance(d.get("pose"), dict) else d
    hp = d.get("has_person")
    if hp is None:
        has_person = None
    else:
        has_person = _as_bool(hp)
        # 原文明确「没有人 / false」才算 False；「有 / true」才算 True
        s = str(hp).strip().lower()
        if s in ("false", "0", "no", "n", "否", "没有", "无人", "空"):
            has_person = False
        elif s in ("true", "1", "yes", "y", "是", "有", "有人"):
            has_person = True
    return {
        "face_ok": _as_bool(d.get("face_ok", True)),
        "in_frame": _as_bool(d.get("in_frame", True)),
        "has_person": has_person,
        "pose": norm_pose(pose_src),
        "raw": (raw or "")[:400],
    }


def judge_tail_severe(dest: Path | None, cands: list | None = None,
                      inspect_info: dict | None = None) -> str:
    """接龙尾帧「严重穿帮」——就这三条，别加。

    1. 几乎是纯色/黑场：灰度 std < 8
    2. 糊到没法当首帧：所有候选 Laplacian 方差都 < TAIL_MIN_SHARP/3（=20）
    3. 画面里没有人：inspect_tail 明确返回 has_person=False
       拿不到结论（没问、没密钥、字段缺失）→ 不算命中

    不算严重的（防止以后有人手痒）：脸小、侧脸、近景、in_frame=false、
    眼睛闭着、嘴半张、和上一段构图不同、光线变了、手势变了。
    这些全是黄旗或者干脆不标。
    """
    dest = Path(dest) if dest else None
    if dest and dest.is_file():
        try:
            if gray_std(dest) < TAIL_SEVERE_STD:
                return "几乎黑场"
        except Exception:
            pass
    pool = [c for c in (cands or []) if c and (c.get("path") or c.get("sharp") is not None)]
    if pool:
        sharps = []
        for c in pool:
            try:
                sharps.append(float(c.get("sharp") or 0))
            except (TypeError, ValueError):
                sharps.append(0.0)
        if sharps and all(v < TAIL_SEVERE_SHARP for v in sharps):
            return "糊到没法当首帧"
    info = inspect_info if isinstance(inspect_info, dict) else {}
    if info.get("error"):
        return ""
    hp = info.get("has_person")
    if hp is False:
        return "没有人"
    return ""


def score_gap(tail_img: Path, dest_img: Path, tail_pose: dict | None = None,
              dest_pose: dict | None = None, gemini=None, job=None) -> tuple[int | None, str]:
    """跨度 0~5。优先两张图问一次；失败就用姿态字段启发式。"""
    if dest_img and Path(dest_img).is_file() and Path(tail_img).is_file():
        try:
            gemini = gemini or _client(job)
            raw = gemini.ask_images(GAP_ASK, [Path(tail_img), Path(dest_img)], json_mode=True)
            d = parse_json_obj(raw) or {}
            sc = _as_int(d.get("gap_score"), 0, 5, None)
            why = str(d.get("gap_why") or "").strip()
            if sc is not None:
                return sc, why or "（模型没写原因）"
        except Exception:
            pass
    return _heuristic_gap(tail_pose or {}, dest_pose or {})


def _heuristic_gap(a: dict, b: dict) -> tuple[int, str]:
    diffs = []
    labels = {"head": "头", "eyes": "视线", "hands": "手", "body": "身姿", "expr": "表情"}
    for k, lab in labels.items():
        sa = re.sub(r"\s+", "", str(a.get(k) or ""))
        sb = re.sub(r"\s+", "", str(b.get(k) or ""))
        if sa and sb and sa != sb:
            diffs.append(lab)
    if not diffs:
        return 0, "姿态几乎一样"
    score = min(5, len(diffs))
    return score, "需要调整：" + "、".join(diffs)


def apply_sharp_flags(m: Manifest, task_dir: Path) -> None:
    """相对阈值：低于本任务所有已抽尾帧锐度中位数的 60% 才标 sharp:false。"""
    scores = []
    for sg in m.segments:
        br = sg.get("bridge") if isinstance(sg.get("bridge"), dict) else None
        if not br:
            continue
        rel = br.get("tail_frame")
        p = Path(task_dir) / rel if rel else None
        if p and p.is_file():
            sh = float(br.get("tail_sharp") or 0) or laplacian_var(p)
            br["tail_sharp"] = sh
            scores.append(sh)
    if not scores:
        return
    xs = sorted(scores)
    mid = xs[len(xs) // 2]
    thresh = mid * SHARP_RATIO
    for sg in m.segments:
        br = sg.get("bridge") if isinstance(sg.get("bridge"), dict) else None
        if not br:
            continue
        sh = br.get("tail_sharp")
        if sh is None:
            continue
        checks = br.get("tail_checks") if isinstance(br.get("tail_checks"), dict) else {}
        checks["sharp"] = float(sh) >= thresh
        br["tail_checks"] = checks


def _fill_bridge(m: Manifest, task_dir: Path, bridge_sg: dict, src_sg: dict,
                 picked: dict, inspect: dict, gemini=None, job=None) -> None:
    """把上一段抽出的尾帧写进接片段的 bridge 字典。"""
    ensure_roles(m)
    br = bridge_sg.get("bridge") if isinstance(bridge_sg.get("bridge"), dict) else {}
    nxt = m.seg(int(bridge_sg["s"]) + 1)
    to_image = (nxt or {}).get("image") or br.get("to_image") or ""
    rel = str(tail_path(task_dir, src_sg["s"]).relative_to(Path(task_dir))).replace("\\", "/")
    pose = inspect.get("pose") or {}
    checks = {
        "face_ok": bool(inspect.get("face_ok")),
        "in_frame": bool(inspect.get("in_frame")),
        "sharp": True,   # 相对阈值在 apply_sharp_flags 里重算
    }
    br.update({
        "from_seg": int(src_sg["s"]),
        "to_image": to_image,
        "tail_frame": rel,
        "tail_pose": pose,
        "tail_checks": checks,
        "tail_from_end": int(picked.get("k") or 0),
        "tail_sharp": float(picked.get("sharp") or 0),
        "auto_swapped_from": br.get("auto_swapped_from"),
    })
    # 路径没变，靠指纹区分「现在这张」和「接片当时用的那张」
    stamp_tail_identity(br, Path(task_dir) / rel)
    if picked.get("below_min"):
        br["tail_warn"] = "尾帧偏糊"
    else:
        br.pop("tail_warn", None)
    dest_im = m.image(to_image) if to_image else None
    dest_file = None
    if dest_im and dest_im.get("file"):
        dest_file = Path(task_dir) / dest_im["file"]
    tail_file = Path(task_dir) / rel
    if dest_file and dest_file.is_file():
        sc, why = score_gap(tail_file, dest_file, pose, dest_im.get("pose") if dest_im else None,
                            gemini=gemini, job=job)
        br["gap_score"] = sc
        br["gap_why"] = why
    bridge_sg["bridge"] = br
    # 给 37 号调度用：上一段抽好了、质检过
    # in_frame 不挡放行（近景常被误判）；脸没拍到才闸。apply_gap_gate 会再写一遍。
    src_sg["bridge_ready"] = checks.get("face_ok") is not False


# ---------------------------------------------------------------- 对外
def extract(task_dir, seg, gemini=None, job=None, inspect: bool = True,
            keep: int | None = None) -> dict:
    """抽一段的尾帧：按 3~8 范围选最锐的，存 clips/_tails/sNNN.jpg。

    返回 {k, sharp, path, checks, pose, ...}。不写 manifest（由 after_collect / extract_all 写）。
    """
    task_dir = Path(task_dir)
    if isinstance(seg, int):
        m = Manifest.load(task_dir)
        sg = m.seg(seg)
        if not sg:
            raise OranteError(f"没有段 s{int(seg):03d}")
        seg = sg
        clip = clip_of(task_dir, m, seg)
    else:
        # 调用方已经有 manifest 时不要再 load，避免覆盖别人的内存对象
        m = None
        clip = None
        try:
            m = Manifest.load(task_dir)
            clip = clip_of(task_dir, m, seg)
        except Exception:
            pass
        if clip is None:
            rel = ""
            takes = (seg or {}).get("takes") or []
            if takes:
                rel = takes[-1].get("file") or ""
            if rel:
                p = task_dir / rel
                clip = p if p.is_file() else None

    if not clip or not Path(clip).is_file():
        raise OranteError(
            f"s{int(seg.get('s') or 0):03d} 还没有成片，抽不了尾帧",
            "等这一段收片后再抽。")

    s = int(seg["s"])
    _ffmpeg()   # 没有 ffmpeg 就在这里说清楚，别抽 10 次各报一遍
    work = tails_dir(task_dir) / f"_scan_s{s:03d}"
    cands = scan_tail_candidates(clip, FROM_END_SCAN, work)
    picked = pick_sharpest(cands, FROM_END_LO, FROM_END_HI)
    if not picked or not picked.get("path"):
        errs = list(dict.fromkeys(c["error"] for c in cands if c.get("error")))
        why = "；".join(errs)[:200]
        raise OranteError(f"s{s:03d} 抽尾帧失败", why or "ffmpeg 没抽出任何一帧")

    dest = tail_path(task_dir, s)
    archive_old_tail(dest, keep=TAIL_OLD_KEEP if keep is None else int(keep))
    from PIL import Image
    im = Image.open(picked["path"]).convert("RGB")
    im.save(dest, "JPEG", quality=JPEG_QUALITY)
    # 扫描中间帧可以扔了，只留选中的那张
    try:
        shutil.rmtree(work, ignore_errors=True)
    except Exception:
        pass

    info = {
        "s": s,
        "k": int(picked["k"]),
        "sharp": float(picked["sharp"] or 0),
        "path": dest,
        "rel": str(dest.relative_to(task_dir)).replace("\\", "/"),
        "candidates": [{"k": c["k"], "sharp": c.get("sharp"), "error": c.get("error")}
                       for c in cands],
        "why": f"倒数第 {picked['k']} 帧在 {FROM_END_LO}~{FROM_END_HI} 里锐度最高"
               f"（拉普拉斯方差 {float(picked['sharp'] or 0):.1f}）",
        "below_min": bool(picked.get("below_min")),
    }
    if info["below_min"]:
        info["why"] += "，全部低于门槛，先用最锐的"
    if inspect:
        try:
            looked = inspect_tail(dest, gemini=gemini, job=job)
        except Exception as e:  # noqa: BLE001
            looked = {"face_ok": True, "in_frame": True, "pose": {},
                      "raw": "", "error": str(e)[:160]}
        info["checks"] = {
            "face_ok": bool(looked.get("face_ok")),
            "in_frame": bool(looked.get("in_frame")),
            "sharp": True,
        }
        info["pose"] = looked.get("pose") or {}
        info["has_person"] = looked.get("has_person")
        info["inspect_error"] = looked.get("error")
    return info


def extract_k(task_dir, seg, k: int, gemini=None, inspect: bool = True, job=None,
              keep: int | None = None) -> dict:
    """手挑：抽出倒数第 k 帧当尾帧（不走 3~8 自动挑）。

    扫描目录 _scan_sNNN 里若已经有这一帧就直接拷，避免滑块来回抽。
    """
    task_dir = Path(task_dir)
    if isinstance(seg, int):
        m = Manifest.load(task_dir)
        sg = m.seg(seg)
        if not sg:
            raise OranteError("没有段 s%03d" % int(seg))
        seg = sg
        clip = clip_of(task_dir, m, seg)
    else:
        m = None
        clip = None
        try:
            m = Manifest.load(task_dir)
            clip = clip_of(task_dir, m, seg)
        except Exception:
            pass
        if clip is None:
            takes = (seg or {}).get("takes") or []
            rel = (takes[-1].get("file") or "") if takes else ""
            if rel:
                p = task_dir / rel
                clip = p if p.is_file() else None
    if not clip or not Path(clip).is_file():
        raise OranteError(
            "s%03d 还没有成片，抽不了尾帧" % int(seg.get("s") or 0),
            "等这一段收片后再抽。")
    s = int(seg["s"])
    k = max(1, int(k))
    work = tails_dir(task_dir) / ("_scan_s%03d" % s)
    cached = work / ("from_end_%02d.jpg" % k)
    dest = tail_path(task_dir, s)
    archive_old_tail(dest, keep=TAIL_OLD_KEEP if keep is None else int(keep))
    if cached.is_file() and cached.stat().st_size > 100:
        from PIL import Image
        im = Image.open(cached).convert("RGB")
        im.save(dest, "JPEG", quality=JPEG_QUALITY)
        try:
            sh = laplacian_var(dest)
        except Exception:
            sh = 0.0
    else:
        extract_from_end(clip, k, dest)
        try:
            sh = laplacian_var(dest)
        except Exception:
            sh = 0.0
        work.mkdir(parents=True, exist_ok=True)
        try:
            from PIL import Image
            Image.open(dest).convert("RGB").save(cached, "JPEG", quality=JPEG_QUALITY)
        except Exception:
            pass
    info = {
        "s": s, "k": k, "sharp": float(sh or 0), "path": dest,
        "rel": str(dest.relative_to(task_dir)).replace("\\", "/"),
        "why": "手挑倒数第 %d 帧（拉普拉斯方差 %.1f）" % (k, float(sh or 0)),
    }
    if inspect:
        try:
            looked = inspect_tail(dest, gemini=gemini, job=job)
        except Exception as e:  # noqa: BLE001
            looked = {"face_ok": True, "in_frame": True, "pose": {},
                      "raw": "", "error": str(e)[:160]}
        info["checks"] = {
            "face_ok": bool(looked.get("face_ok")),
            "in_frame": bool(looked.get("in_frame")),
            "sharp": True,
        }
        info["pose"] = looked.get("pose") or {}
        info["has_person"] = looked.get("has_person")
        info["inspect_error"] = looked.get("error")
    return info


# ---------------------------------------------------------------- 规格 95：尾帧登记成新图
TAIL_IMG_ORIGIN = "tail"


def is_tail_image(im: dict | None) -> bool:
    return bool(isinstance(im, dict) and im.get("origin") == TAIL_IMG_ORIGIN)


def tail_image_of(m: Manifest, s) -> dict | None:
    """sNNN 的尾帧登记成的那张图（没有就 None）。"""
    try:
        s = int(s)
    except (TypeError, ValueError):
        return None
    for im in m.images or []:
        if not is_tail_image(im):
            continue
        try:
            if int(im.get("tail_of") or 0) == s:
                return im
        except (TypeError, ValueError):
            continue
    return None


def tail_image_name(p: str, s: int) -> str:
    """p02-s001尾帧.jpg：p 号顺着图池排，名字里写明来自哪一段。"""
    return "%s-s%03d尾帧.jpg" % (p, int(s))


def sync_tail_image(task_dir: Path, m: Manifest, s: int) -> dict | None:
    """尾帧文件变了（重抽 / 手挑 / 提清）→ 把登记图的副本也换掉。没登记就不动。"""
    im = tail_image_of(m, s)
    if not im:
        return None
    src = tail_path(Path(task_dir), int(s))
    if not src.is_file():
        return im
    dest = Path(task_dir) / (im.get("file") or "").replace("\\", "/")
    if not im.get("file"):
        return im
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dest))
        im["sha1"] = sha1_file(dest)
        im.pop("exists", None)
    except Exception:
        pass
    return im


def register_tail_image(task_dir: Path, m: Manifest, seg: dict, *, log=None,
                        policy: str = "never", job=None, gemini=None) -> dict | None:
    """把 sNNN 的尾帧登记成图池里的下一张图（p 号顺延），下一段就配这张图。

    描述语：policy=always 现在就给这张新图写；否则沿用本段用的描述语。
    重抽同一段只更新那张图，不再新开号。
    """
    def _log(msg: str) -> None:
        if callable(log):
            try:
                log(msg)
            except Exception:
                pass

    from . import pipeline
    from .broll import next_code, IMG_DIR
    task_dir = Path(task_dir)
    s = int(seg.get("s") or 0)
    src = tail_path(task_dir, s)
    if not src.is_file():
        return None
    ch = seg.get("chain") if isinstance(seg.get("chain"), dict) else {}
    src_im = m.image(seg.get("image") or "") or {}
    im = tail_image_of(m, s)
    fresh = im is None
    if fresh:
        p = next_code(m.images, "p")
        rel = "%s/%s" % (IMG_DIR, tail_image_name(p, s))
        im = {
            "p": p,
            "file": rel,
            "sha1": "",
            "role": "主体",
            "status": "ok",
            "desc": "",
            "voice": src_im.get("voice") or "",
            "eye": src_im.get("eye") or "闭眼",
            "scene": src_im.get("scene") or "常规",
            "measure": dict(src_im.get("measure") or {}),
            "origin": TAIL_IMG_ORIGIN,
            "src": m.clip_name(seg),
            "tail_of": s,
            "pose": {},
        }
        m.images.append(im)
    else:
        p = im.get("p")
    dest = task_dir / (im.get("file") or "").replace("\\", "/")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src), str(dest))
    im["sha1"] = sha1_file(dest)
    im["status"] = "ok"
    im.pop("exists", None)
    if ch.get("tail_pose"):
        im["pose"] = dict(ch.get("tail_pose") or {})

    # 描述语：沿用 / 重写
    prev_desc = str(ch.get("desc") or src_im.get("desc") or "").strip()
    wrote = False
    if str(policy or "").lower() == "always":
        try:
            from . import describe as descmod
            r = descmod.describe_file(job, task_dir, dest, gemini=gemini)
            if r.get("desc"):
                im["desc"] = r["desc"]
                im["desc_from"] = "gemini"
                if r.get("pose"):
                    im["pose"] = r["pose"]
                if r.get("eye"):
                    im["eye"] = r["eye"]
                if r.get("scene"):
                    im["scene"] = r["scene"]
                wrote = True
                _log("  · %s（s%03d 尾帧）已写新描述语" % (p, s))
        except Exception as e:  # noqa: BLE001
            _log("  · %s（s%03d 尾帧）写描述语失败（%s），沿用 %s 的"
                 % (p, s, str(e)[:80], src_im.get("p") or "上一段"))
    if not wrote and not im.get("desc_manual"):
        im["desc"] = prev_desc
        im["desc_from"] = src_im.get("p") or ""
        if fresh:
            _log("  · s%03d 尾帧登记为 %s（%s），描述语沿用 %s"
                 % (s, p, Path(im["file"]).name, src_im.get("p") or "上一段"))
    elif fresh:
        _log("  · s%03d 尾帧登记为 %s（%s）" % (s, p, Path(im["file"]).name))

    # 下一段（接龙、没换图起头）配这张图。尾帧严重穿帮的不配——提交时会退到更早的尾帧 / 起头图
    nxt = pipeline.next_of(m, seg)
    if (nxt and not pipeline.is_chain_restart(nxt)
            and nxt.get("status") in ("planned", "failed")
            and not ch.get("tail_severe")):
        if nxt.get("image") != p:
            nxt["image"] = p
            nxt["img_role"] = "主体"
            _log("  · s%03d 改配 %s（s%03d 的尾帧）" % (int(nxt["s"]), p, s))
    return im


def chain_desc_policy(m: Manifest) -> str:
    from . import pipeline
    return str(pipeline.chain_cfg(m).get("desc_policy") or "never")


def after_collect(task_dir: Path, m: Manifest, seg: dict, log=None, gemini=None,
                  job=None) -> dict | None:
    """主片收片后自动触发：抽这一段的尾帧；下一段若是接片就把 bridge 填上。"""
    def _log(msg: str) -> None:
        if callable(log):
            try:
                log(msg)
            except Exception:
                pass

    from . import pipeline
    if pipeline.is_chain_mode(m):
        return after_collect_chain(task_dir, m, seg, log=log, gemini=gemini, job=job)

    ensure_roles(m)
    try:
        info = extract(task_dir, seg, gemini=gemini, inspect=True)
    except OranteError as e:
        _log(f"  · s{int(seg.get('s') or 0):03d} 尾帧：{e.message}")
        return None
    _log(f"  · s{info['s']:03d} 尾帧 {info['why']}")

    nxt = m.seg(int(seg["s"]) + 1)
    if nxt and nxt.get("role") == "bridge":
        _fill_bridge(m, task_dir, nxt, seg, info,
                     {"face_ok": (info.get("checks") or {}).get("face_ok"),
                      "in_frame": (info.get("checks") or {}).get("in_frame"),
                      "pose": info.get("pose") or {}},
                     gemini=gemini)
        _log(f"  · s{nxt['s']:03d} 接片已接上 s{seg['s']:03d} 的尾帧")
        from . import pipeline
        gate = pipeline.apply_gap_gate(task_dir, m, seg, log=_log, gemini=gemini)
        if gate.get("gap") is not None:
            _log(f"  · s{nxt['s']:03d} 跨度 {gate['gap']}"
                 + ("，可推进" if gate.get("ready") else "，先不提"))
    apply_sharp_flags(m, task_dir)
    return info


def after_collect_chain(task_dir: Path, m: Manifest, seg: dict, log=None,
                        gemini=None, job=None) -> dict | None:
    """接龙收片：抽尾帧、判严重穿帮、写下 chain 元数据、把尾帧登记成下一张图。

    这一段照收，链条不停。抽不出来时把原因写进 chain.tail_error，检查台和等待理由都能看到。
    """
    def _log(msg: str) -> None:
        if callable(log):
            try:
                log(msg)
            except Exception:
                pass

    from . import pipeline
    ch0 = seg.get("chain") if isinstance(seg.get("chain"), dict) else {}
    try:
        info = extract(task_dir, seg, gemini=gemini, inspect=True,
                       keep=TAIL_OLD_KEEP_CHAIN)
    except OranteError as e:
        why = e.message + ("：" + e.hint if e.hint else "")
        ch0["tail_error"] = why[:200]
        seg["chain"] = ch0
        _log("  ✗ s%03d 尾帧：%s" % (int(seg.get("s") or 0), why))
        return None
    ch0.pop("tail_error", None)
    seg["chain"] = ch0
    _log("  · s%03d 尾帧 %s" % (info["s"], info["why"]))

    looked = {
        "face_ok": (info.get("checks") or {}).get("face_ok"),
        "in_frame": (info.get("checks") or {}).get("in_frame"),
        "has_person": info.get("has_person"),
        "error": info.get("inspect_error"),
        "pose": info.get("pose") or {},
    }
    # inspect_tail 的 has_person 在 extract 里没往外传，再读一次太贵；
    # 严重判定 3 只在明确 False 时命中。extract 的 inspect_error 视同未知。
    severe = judge_tail_severe(
        info.get("path"), info.get("candidates") or [], looked)
    ch = seg.get("chain") if isinstance(seg.get("chain"), dict) else {}
    ch["tail_file"] = info.get("rel") or ""
    ch["tail_sharp"] = float(info.get("sharp") or 0)
    ch["tail_severe"] = severe
    ref_rel = (ch.get("ref_file") or "").replace("\\", "/")
    if ref_rel and info.get("path"):
        from . import pipeline as pipemod
        ch["tail_dist"] = pipemod._ahash_dist(
            Path(task_dir) / ref_rel, Path(info["path"]))
    else:
        ch.setdefault("tail_dist", None)
    if info.get("pose"):
        ch["tail_pose"] = info.get("pose")
    stamp_tail_identity(ch, info.get("path"))
    seg["chain"] = ch

    try:
        hp = extract_head(task_dir, m, seg)
        if hp:
            ch["head_file"] = str(hp.relative_to(task_dir)).replace("\\", "/")
            seg["chain"] = ch
    except Exception:
        pass

    # 规格 95：尾帧登记成下一张图（p 号顺延），下一段配它；描述语按策略沿用 / 重写
    try:
        register_tail_image(task_dir, m, seg, log=log, policy=chain_desc_policy(m),
                            job=job, gemini=gemini)
    except Exception as e:  # noqa: BLE001
        _log("  · s%03d 尾帧登记成图失败：%s" % (info["s"], str(e)[:120]))

    if severe:
        nxt = pipeline.next_of(m, seg)
        nxt_lab = pipeline._lab(nxt["s"]) if nxt else "下一段"
        # 下一段参考会在提交时 resolve_chain_ref 退级
        fallback = None
        for prev in reversed(list(m.segments or [])):
            try:
                ps = int(prev.get("s") or 0)
            except (TypeError, ValueError):
                continue
            if ps >= int(seg.get("s") or 0):
                continue
            pch = prev.get("chain") if isinstance(prev.get("chain"), dict) else {}
            if prev.get("status") == "collected" and not pch.get("tail_severe"):
                if tail_path(task_dir, ps).is_file():
                    fallback = pipeline._lab(ps)
                    break
        if fallback:
            _log("⚠ s%03d 尾帧%s，%s 改从 %s 尾帧接；s%03d 可到检查台重生" % (
                info["s"], severe, nxt_lab, fallback, info["s"]))
        else:
            start_p = pipeline.chain_cfg(m).get("start_image") or "起头图"
            _log("⚠ s%03d 尾帧%s，%s 改从 %s 接；s%03d 可到检查台重生" % (
                info["s"], severe, nxt_lab, start_p, info["s"]))
    return info


def list_tails(tid: str) -> dict:
    """⑤ 尾帧一览：每段 {s, file, mtime, sharp, dist, severe, upscaled, head_file}。"""
    from .. import tasks as taskmod
    from . import pipeline
    t = taskmod.get(tid)
    d = Path(t["dir"]) if t.get("dir") else None
    if not d or not d.is_dir():
        raise OranteError("任务文件夹不在了")
    m = Manifest.load(d)
    items = []
    used_by = {}
    for sg in m.segments or []:
        ch = sg.get("chain") if isinstance(sg.get("chain"), dict) else {}
        ref_from = str(ch.get("ref_from") or "")
        if ref_from.startswith("s"):
            try:
                src = int(ref_from[1:])
            except (TypeError, ValueError):
                src = 0
            if src:
                used_by.setdefault(src, []).append(int(sg.get("s") or 0))
    for sg in m.segments or []:
        s = int(sg.get("s") or 0)
        ch = sg.get("chain") if isinstance(sg.get("chain"), dict) else {}
        tp = tail_path(d, s)
        hp = head_path(d, s)
        head_rel = (ch.get("head_file") or "").replace("\\", "/")
        if not head_rel and hp.is_file():
            try:
                head_rel = str(hp.relative_to(d)).replace("\\", "/")
            except ValueError:
                head_rel = str(hp)
        rel = ""
        if tp.is_file():
            try:
                rel = str(tp.relative_to(d)).replace("\\", "/")
            except ValueError:
                rel = str(tp)
        w = h = 0
        if tp.is_file():
            try:
                from PIL import Image
                with Image.open(tp) as im:
                    w, h = im.size
            except Exception:
                pass
        up = ch.get("tail_upscaled") if isinstance(ch.get("tail_upscaled"), dict) else None
        items.append({
            "s": s,
            "file": rel,
            "mtime": int(tp.stat().st_mtime) if tp.is_file() else 0,
            "sharp": ch.get("tail_sharp"),
            "dist": ch.get("head_dist") if ch.get("head_dist") is not None else ch.get("tail_dist"),
            "severe": ch.get("tail_severe") or "",
            "upscaled": up,
            "head_file": head_rel if (hp.is_file() or head_rel) else "",
            "w": w, "h": h,
            "upstream_changed": bool(ch.get("upstream_changed")),
            "stale_from": ch.get("stale_from") or sg.get("stale_from"),
            "ref_from": ch.get("ref_from") or "",
            "ref_kind": ch.get("ref_kind") or "",
            "used_by": used_by.get(s) or [],
            "status": sg.get("status") or "",
        })
    return {
        "tid": tid,
        "mode": pipeline.mode_of(m),
        "items": items,
        "n": len(items),
    }


def extract_all(task_dir, m: Manifest | None = None, log=None, gemini=None,
                inspect: bool = True) -> list[dict]:
    """对任务里所有已收片段抽尾帧，填接片段的 bridge。"""
    task_dir = Path(task_dir)
    if m is None:
        m = Manifest.load(task_dir)
    ensure_roles(m)
    gemini = gemini or (_client(None) if inspect else None)

    def _log(msg: str) -> None:
        if callable(log):
            try:
                log(msg)
            except Exception:
                pass

    from . import pipeline as _pipe
    chain = _pipe.is_chain_mode(m)
    results = []
    for sg in m.segments:
        if sg.get("status") != "collected" and not (sg.get("takes") or []):
            continue
        if chain:
            # 规格 95：接龙走收片那条路——抽尾帧 + 登记成图 + 下一段配图
            info = after_collect_chain(task_dir, m, sg, log=log, gemini=gemini)
            if not info:
                ch = sg.get("chain") if isinstance(sg.get("chain"), dict) else {}
                results.append({"s": sg["s"], "error": ch.get("tail_error") or "抽尾帧失败"})
            else:
                results.append(info)
            continue
        try:
            info = extract(task_dir, sg, gemini=gemini, inspect=inspect)
        except OranteError as e:
            _log(f"s{sg['s']:03d} 失败：{e.message}")
            results.append({"s": sg["s"], "error": e.message})
            continue
        results.append(info)
        _log(f"s{info['s']:03d} {info['why']}")
        nxt = m.seg(int(sg["s"]) + 1)
        if nxt and nxt.get("role") == "bridge" and inspect:
            _fill_bridge(m, task_dir, nxt, sg, info,
                         {"face_ok": (info.get("checks") or {}).get("face_ok"),
                          "in_frame": (info.get("checks") or {}).get("in_frame"),
                          "pose": info.get("pose") or {}},
                         gemini=gemini)
            from . import pipeline
            pipeline.apply_gap_gate(task_dir, m, sg, log=_log, gemini=gemini)
    apply_sharp_flags(m, task_dir)
    if chain:
        _pipe.stamp_waits(m, task_dir)
    m.save(task_dir)
    return results


@register("extract_tails")
def extract_tails_job(job):
    """手动重跑尾帧提取（主片收片已经会自动跑，一般不用点）。"""
    from .. import tasks as taskmod
    if not job.task_id:
        raise OranteError("extract_tails 需要 task_id")
    t = taskmod.get(job.task_id)
    d = Path(t["dir"])
    m = Manifest.load(d)
    job.log("开始抽尾帧…")
    rows = extract_all(d, m, log=job.log, inspect=True)
    n_ok = sum(1 for r in rows if not r.get("error"))
    job.log(f"✓ 抽了 {n_ok}/{len(rows)} 段尾帧")
    return {"ok": n_ok, "total": len(rows), "rows": [
        {k: v for k, v in r.items() if k not in ("path", "candidates")}
        for r in rows
    ]}


@register("upscale_tail", account_bound=True)
def upscale_tail_job(job, s=None):
    """尾帧一览「提清这一帧」：对 tails/sNNN.jpg 跑 66 号提清。"""
    from .. import tasks as taskmod
    from . import pipeline
    if not job.task_id:
        raise OranteError("upscale_tail 需要 task_id")
    if s is None:
        raise OranteError("提清尾帧要指定段号", "body 里带 s。")
    t = taskmod.get(job.task_id)
    d = Path(t["dir"])
    m = Manifest.load(d)
    job.log("提清 %s 尾帧…" % pipeline._lab(s))
    r = pipeline.upscale_chain_tail(job, m, d, int(s), auto=False)
    m.save(d)
    return r
