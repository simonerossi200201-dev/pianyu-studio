# -*- coding: utf-8 -*-
"""落盘自证：每次下载成功后立刻核对「这是不是本段该拿的那份」。

4a 全任务 sha256 唯一 —— 抓同一生成物被下了两遍。
4b 首帧对配图（16×16 均值哈希，汉明距离）—— 抓拿错片。
4c 同图段位靠 4a；配图不同但首帧几乎一样 → 黄旗 W_FRAME_TWIN。
阈值按 T0013 实测，不要改：对的 0~7，错的 23~64。

2026-10-06（T0124）：均值哈希对「Grok 调了色调 / 画幅差一点」很敏感——对的片子算出距 17~79，
被当成拿错作废，又反复下到同一个文件。所以均值哈希说「对不上」时，再用画面结构（梯度相关，
画幅对齐）复核一次：跟自己的配图结构一致、又不更像别的图，就收下（frame_rescued）。
两张配图本来就几乎一样时，像哪张都算对。结构也对不上，才按原规则作废。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from pathlib import Path

from ..errors import OranteError
from ..models import Manifest
from .procutil import run_quiet

# 实测阈值，规格写死，不要自己改
DIST_OK = 10          # ≤10 通过
DIST_SOFT = 16        # 11~16 黄旗，不拦；>16 红旗当拿错
TWIN_DIST = 4         # 首帧距离 <4 且配图不是同一张 → 黄旗

E_DUP_ASSET = "E_DUP_ASSET"
E_WRONG_CLIP = "E_WRONG_CLIP"
W_FRAME_SOFT = "W_FRAME_SOFT"
W_FRAME_TWIN = "W_FRAME_TWIN"
VERIFY_CODES = {E_DUP_ASSET, E_WRONG_CLIP, W_FRAME_SOFT, W_FRAME_TWIN}

# 查漏补缺交叉印证只认这两种「片子不对」
BAD_VERDICTS = {"wrong_clip", "dup"}


# ---- 结构复核（2026-10-06）：均值哈希判错时的第二意见 ----
STRUCT_OK = 0.80        # 片子首帧和自己配图的结构相关 ≥ 这个才可能收下（实测对的片 0.87~1.00）
STRUCT_MARGIN = 0.10    # 而且要比最像的别的图高出这么多（同一人不同姿势的别的图最高 0.90）
STRUCT_SAME_PIC = 0.90  # 两张配图之间这么像 = 几乎是同一张图，片子像哪张都算对
STRUCT_TAIL_OK = 0.85   # 参考是上一段尾帧时（没有「别的图」可比）
_STRUCT_N = 32
_GRAD_CACHE: dict = {}


def _grad_vec(im, n: int = _STRUCT_N) -> list[float]:
    """灰度图 → (n+1)² 缩略 → 横竖两向差分 → 去均值、单位化。对亮度 / 对比度 / 色调变化不敏感。"""
    from PIL import Image
    g = im.convert("L").resize((n + 1, n + 1), Image.BILINEAR)
    px = g.tobytes()
    w = n + 1
    v = []
    for y in range(n):
        row = y * w
        for x in range(n):
            v.append(float(px[row + x + 1] - px[row + x]))
    for y in range(n):
        row = y * w
        for x in range(n):
            v.append(float(px[row + w + x] - px[row + x]))
    mean = sum(v) / len(v)
    v = [a - mean for a in v]
    norm = sum(a * a for a in v) ** 0.5 or 1.0
    return [a / norm for a in v]


def _crop_aspect(im, ar: float):
    w, h = im.size
    if not h or not ar:
        return im
    r = w / h
    if abs(r - ar) < 1e-3:
        return im
    if r > ar:
        nw = h * ar
        x0 = (w - nw) / 2.0
        return im.crop((int(round(x0)), 0, int(round(x0 + nw)), h))
    nh = w / ar
    y0 = (h - nh) / 2.0
    return im.crop((0, int(round(y0)), w, int(round(y0 + nh))))


def struct_sim(frame, ref) -> float:
    """片子首帧 vs 参考图的结构相关（-1~1）。画幅不一样时试两种对齐取高的：
    参考图按片子画幅居中裁（Flow 的做法）；两边都裁到较窄的画幅（Grok 有时按近似画幅出）。"""
    try:
        fw, fh = frame.size
        rw, rh = ref.size
        far = fw / fh if fh else 1.0
        rar = rw / rh if rh else 1.0
        cands = [(frame, _crop_aspect(ref, far))]
        lo = min(far, rar)
        if abs(lo - far) > 1e-3 or abs(lo - rar) > 1e-3:
            cands.append((_crop_aspect(frame, lo), _crop_aspect(ref, lo)))
        best = -1.0
        for a, b in cands:
            va, vb = _grad_vec(a), _grad_vec(b)
            best = max(best, sum(x * y for x, y in zip(va, vb)))
        return float(best)
    except Exception:  # noqa: BLE001
        return -1.0


def _first_frame_image(path: Path, ffmpeg: str = "", width: int = 192):
    """片子第 0 帧（缩到 width 宽）。拿不到返回 None。"""
    import io
    ffmpeg = ffmpeg or find_ffmpeg()
    sig = _sig(path)
    if not ffmpeg or sig is None:
        return None
    key = ("frame0", sig, width)
    if key in _GRAD_CACHE:
        return _GRAD_CACHE[key]
    im = None
    try:
        r = run_quiet([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(path),
                       "-an", "-sn", "-dn", "-frames:v", "1", "-vf", f"scale={int(width)}:-2",
                       "-f", "image2pipe", "-vcodec", "png", "pipe:1"], timeout=45)
        data = r.stdout or b""
        if data:
            from PIL import Image
            im = Image.open(io.BytesIO(data))
            im.load()
            im = im.convert("L")
    except Exception:  # noqa: BLE001
        im = None
    _GRAD_CACHE[key] = im
    return im


def _small_image(path: Path, width: int = 192):
    sig = _sig(path)
    if sig is None:
        return None
    key = ("img", sig, width)
    if key in _GRAD_CACHE:
        return _GRAD_CACHE[key]
    im = None
    try:
        from PIL import Image
        with Image.open(path) as src:
            src.draft("L", (width * 2, width * 2))
            im = src.convert("L")
            im.thumbnail((width * 2, width * 2))
            im.load()
    except Exception:  # noqa: BLE001
        im = None
    if len(_GRAD_CACHE) > 4000:
        _GRAD_CACHE.clear()
    _GRAD_CACHE[key] = im
    return im


def struct_recheck(m: Manifest, task_dir: Path, seg: dict, clip: Path,
                   ref: Path | None, ref_kind: str, ffmpeg: str = "") -> dict | None:
    """均值哈希说「首帧对不上」时的第二意见。返回
    {ok, sim, other, other_p, same_pic, why}；拿不到首帧 / 参考图返回 None（按原规则走）。"""
    if ref is None or not Path(ref).is_file():
        return None
    fr = _first_frame_image(Path(clip), ffmpeg)
    rim = _small_image(Path(ref))
    if fr is None or rim is None:
        return None
    own = struct_sim(fr, rim)
    out = {"ok": False, "sim": round(own, 3), "other": None, "other_p": "", "same_pic": False, "why": ""}
    if ref_kind != "image":
        out["ok"] = own >= STRUCT_TAIL_OK
        out["why"] = "tail_ok" if out["ok"] else "tail_low"
        return out
    my_p = seg.get("image") or ""
    best, best_p = -1.0, ""
    for im in (m.images or []):
        p = im.get("p") or ""
        if not p or p == my_p:
            continue
        ip = _image_path(Path(task_dir), m, p)
        if ip is None:
            continue
        oim = _small_image(ip)
        if oim is None:
            continue
        v = struct_sim(fr, oim)
        if v > best:
            best, best_p = v, p
    out["other"] = round(best, 3) if best_p else None
    out["other_p"] = best_p
    if own < STRUCT_OK:
        out["why"] = "low"
        return out
    if not best_p or own - best >= STRUCT_MARGIN:
        out["ok"] = True
        out["why"] = "own"
        return out
    # 别的图跟自己差不多像甚至更像：只有这两张配图本来就几乎是同一张，才算对
    oip = _image_path(Path(task_dir), m, best_p)
    oim = _small_image(oip) if oip else None
    pair = struct_sim(rim, oim) if oim is not None else -1.0
    if pair >= STRUCT_SAME_PIC:
        out["ok"] = True
        out["same_pic"] = True
        out["why"] = "same_pic"
        return out
    out["why"] = "other"
    return out


def _rescue_line(sn: int, d, rc: dict, ref_lab: str) -> str:
    if rc.get("same_pic"):
        return (f"  · {_label(sn)} 首帧指纹差得多（距 {d}），但画面就是 {ref_lab}"
                f"（结构相关 {rc['sim']:.2f}；它和 {rc['other_p']} 本来就几乎同一张图），收下")
    return (f"  · {_label(sn)} 首帧指纹差得多（距 {d}，多半是调了色调 / 画幅差一点），"
            f"但画面结构和 {ref_lab} 一致（相关 {rc['sim']:.2f}，最像的别的图只有 "
            f"{(rc.get('other') if rc.get('other') is not None else 0):.2f}），收下")


def _mismatch_detail(sn: int, rc: dict, ref_lab: str) -> str:
    if rc.get("other_p") and (rc.get("why") == "other"
                              or (rc.get("other") or 0) >= max(STRUCT_OK, (rc.get("sim") or 0) + STRUCT_MARGIN)):
        return (f"    结构复核：首帧更像 {rc['other_p']}（{rc['other']:.2f}），"
                f"不像 {ref_lab}（{rc['sim']:.2f}）——真拿错了")
    return f"    结构复核：和 {ref_lab} 也对不上（相关 {rc['sim']:.2f}）"


def restore_voided(m: Manifest, task_dir: Path, segs: list, log=None) -> list[int]:
    """开跑前先翻一遍 clips/_错件、clips/_弃用：以前被均值哈希误判作废、其实是对的片子，
    结构复核过了就挪回来标收片，省得再花一次额度重生（T0124 有 6 段、T0122 有十几段这样的）。

    只认：段还没收片（planned / failed）、文件名里的图号就是这段现在的配图、
    参考是配图（不是上一段尾帧）、跟别的段已收的片子不是同一个文件。返回找回的段号。

    2026-10-06 事故：用户点「重生」时旧片也是挪进 _弃用 的，这里把它当成误判又找了回来，
    重生瞬间失效。所以现在**只认校验器自己作废的那几个文件**（seg['voided_sha'] 里记着的 sha），
    用户重生挪走的（seg['user_discarded'] 里记着）一律不碰。"""
    import re as _re
    task_dir = Path(task_dir)
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return []
    junk = [task_dir / "clips" / "_错件", task_dir / "clips" / "_弃用"]
    files = [f for j in junk if j.is_dir() for f in j.iterdir() if f.is_file() and f.suffix.lower() == ".mp4"]
    if not files:
        return []
    taken_sha = set()
    for sg in m.segments:
        for tk in sg.get("takes") or []:
            if tk.get("sha256"):
                taken_sha.add(str(tk["sha256"]).lower())
    tid = getattr(m, "task_id", "") or ""
    out = []
    for sg in segs:
        if sg.get("status") not in ("planned", "failed") or sg.get("takes"):
            continue
        voided = {str(x).strip().lower() for x in (sg.get("voided_sha") or []) if x}
        if not voided:
            continue                      # 校验器没作废过这段的片子：错件里的不是误判，不碰
        discarded = {str(x).strip().lower() for x in (sg.get("user_discarded") or []) if x}
        sn = int(sg.get("s") or 0)
        p = sg.get("image") or ""
        if not p:
            continue
        ref, kind, lab = _first_frame_ref(task_dir, m, sg)
        if kind != "image" or ref is None:
            continue
        pat = _re.compile(rf"{_re.escape(tid)}-s{sn:03d}-{_re.escape(p)}(-\d+)?\.mp4$", _re.I)
        cands = sorted((f for f in files if pat.search(f.name)), key=lambda f: f.stat().st_mtime, reverse=True)
        for f in cands:
            try:
                digest = sha256_file(f).lower()
            except OSError:
                continue
            if digest in taken_sha or digest not in voided or digest in discarded:
                continue
            rc = struct_recheck(m, task_dir, sg, f, ref, "image", ffmpeg)
            if not rc or not rc.get("ok"):
                continue
            dest = task_dir / "clips" / m.clip_name(sg)
            if dest.exists():
                dest = dest.with_name(dest.stem + f"-{int(time.time())}.mp4")
            shutil.move(str(f), str(dest))
            st = dest.stat()
            vh = ahash_video(dest, ffmpeg)
            ih = ahash_image(ref, ffmpeg)
            d = hamming(vh, ih) if (vh is not None and ih is not None) else None
            sg["takes"] = [{
                "file": str(dest.relative_to(task_dir)).replace("\\", "/"),
                "gen_id": "", "at": time.strftime("%Y-%m-%d %H:%M:%S"), "asset_url": "",
                "sha256": digest, "bytes": int(st.st_size), "mtime": int(st.st_mtime),
                "frame_ahash": ahash_hex(vh) if vh is not None else "", "frame_dist": d,
                "frame_sim": rc["sim"], "frame_rescued": True, "verdict": "ok",
                "restored_from": f.parent.name + "/" + f.name,
            }]
            sg["status"] = "collected"
            sg["error"] = None
            sg["gen_id"] = None
            for k in ("wrongclip_count", "voided_sha", "mismatch_count", "fail_count", "check",
                      "orphan_conv", "gen_health", "dl_retry"):
                sg.pop(k, None)
            taken_sha.add(digest)
            files.remove(f)
            out.append(sn)
            if callable(log):
                try:
                    log(f"  ↺ {_label(sn)} 在 {f.parent.name} 里找回之前误判作废的片子"
                        f"（画面和 {lab} 一致，结构相关 {rc['sim']:.2f}），直接收下，不重生")
                except Exception:  # noqa: BLE001
                    pass
            break
    return out


def take_verdict(sg: dict) -> str:
    """只读最后一条 take 上已经写下的 verdict，不重算、不碰文件。"""
    tks = sg.get("takes") or []
    if not tks:
        return ""
    return str((tks[-1] or {}).get("verdict") or "").strip().lower()


def verdicts_of(m: Manifest) -> dict[int, str]:
    """段号 → 落盘校验结论。没有凭证的段不出现。"""
    out: dict[int, str] = {}
    for sg in m.segments:
        sn = int(sg.get("s") or 0)
        v = take_verdict(sg)
        if v:
            out[sn] = v
    return out

# (路径, 大小, mtime) → 中间结果，进程内缓存
_CACHE: dict = {}

# 配图哈希落盘：data/ahash-cache.json
_DISK: dict = {}
_DISK_LOADED = False
_DISK_LOCK = threading.Lock()
_DISK_DIRTY = False

# ffmpeg 调用计数（验收用，验完留着）
_FFMPEG_CALLS = 0
_FFMPEG_LOCK = threading.Lock()

# 同一段连续作废次数（日志合并用，进程内、按 seg 对象）
_VOID_STREAK: dict[int, int] = {}


def ffmpeg_calls() -> int:
    """_ffmpeg_gray16 累计调用次数。"""
    return _FFMPEG_CALLS


def reset_ffmpeg_calls() -> None:
    """验收前清零。"""
    global _FFMPEG_CALLS
    with _FFMPEG_LOCK:
        _FFMPEG_CALLS = 0


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def hamming(a: int, b: int) -> int:
    return (int(a) ^ int(b)).bit_count()


def ahash_hex(bits: int) -> str:
    return f"{int(bits) & ((1 << 256) - 1):064x}"


def ahash_from_hex(s: str) -> int | None:
    if not s:
        return None
    try:
        return int(s, 16)
    except (TypeError, ValueError):
        return None


def _ahash_raw(data: bytes) -> int | None:
    if len(data) < 256:
        return None
    pix = data[:256]
    avg = sum(pix) / 256.0
    bits = 0
    for i, p in enumerate(pix):
        if p >= avg:
            bits |= 1 << (255 - i)
    return bits


def find_ffmpeg() -> str:
    from . import checks
    return checks.find_ffmpeg() or ""


def _ffmpeg_gray16(ffmpeg: str, src: Path, video: bool) -> int | None:
    """抽第 0 帧（或图片本身）→ 灰度 16×16 → 256 bit 均值哈希。"""
    global _FFMPEG_CALLS
    if not ffmpeg or not src or not Path(src).is_file():
        return None
    vf = "scale=16:16:flags=bicubic,format=gray"
    if video:
        # 不解音频/字幕/数据轨；-ss 0 写在 -i 前没有意义，去掉
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error",
               "-i", str(src),
               "-an", "-sn", "-dn",
               "-vf", vf, "-frames:v", "1", "-f", "rawvideo", "pipe:1"]
    else:
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error",
               "-i", str(src),
               "-an", "-sn", "-dn",
               "-vf", vf, "-frames:v", "1", "-f", "rawvideo", "pipe:1"]
    with _FFMPEG_LOCK:
        _FFMPEG_CALLS += 1
        n = _FFMPEG_CALLS
    print(f"[verify] ffmpeg #{n} {'video' if video else 'image'} {Path(src).name}",
          flush=True)
    try:
        r = run_quiet(cmd, timeout=45)
    except Exception:
        return None
    return _ahash_raw(r.stdout or b"")


def _pillow_gray16(path: Path) -> int | None:
    """静态图走 Pillow，不开 ffmpeg。失败返回 None。"""
    try:
        from PIL import Image
        with Image.open(path) as src:
            im = src.convert("L").resize((16, 16), Image.BICUBIC)
            raw = im.tobytes()
        return _ahash_raw(raw)
    except Exception:
        return None


def ahash_image(path: Path, ffmpeg: str = "") -> int | None:
    """配图哈希：优先 Pillow，失败才退回 ffmpeg。没有 ffmpeg 也能算。"""
    ffmpeg = ffmpeg or find_ffmpeg()
    return _cached_ahash(path, ffmpeg, video=False)


def has_audio(path: Path, ffmpeg: str = "") -> bool | None:
    """视频里有没有音轨。看不出来返回 None。"""
    ffmpeg = ffmpeg or find_ffmpeg()
    if not ffmpeg or not Path(path).is_file():
        return None
    try:
        r = run_quiet([ffmpeg, "-hide_banner", "-i", str(path)], timeout=30)
    except Exception:
        return None
    err = (r.stderr or b"").decode("utf-8", "replace")
    if "Stream #" not in err:
        return None
    return "Audio:" in err


def strip_audio(path: Path, ffmpeg: str = "") -> bool:
    """把视频里的音轨去掉（画面原样拷贝，不重新编码）。去掉了返回 True。

    为什么（T0122，09-28）：Flow 网页的「Return silent videos」**不是**"出无声视频"，
    而是"声音被审核过滤时，照样把没声音的片子给我"。声音没被过滤的那些片子照样带声音
    （T0122 已收的 66 条里 17 条有声）。不说话的任务要的是无声视频，所以下载后统一去掉音轨。"""
    ffmpeg = ffmpeg or find_ffmpeg()
    path = Path(path)
    if has_audio(path, ffmpeg) is not True:
        return False
    tmp = path.with_name(path.stem + ".noaudio.tmp.mp4")
    try:
        r = run_quiet([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
                       "-map", "0:v", "-c", "copy", "-an", "-movflags", "+faststart", str(tmp)],
                      timeout=120)
        if r.returncode != 0 or not tmp.is_file() or tmp.stat().st_size < 1024:
            return False
        if has_audio(tmp, ffmpeg) is not False:
            return False
        os.replace(tmp, path)
        return True
    except Exception:
        return False
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def ahash_image_cropped(path: Path) -> int | None:
    """配图按交给 Flow 的规则居中裁成精确 16:9（横 / 正方形）或 9:16（竖）以后的哈希。
    已经是精确画幅就返回 None（跟整张图一样，不用再比）。"""
    sig = _sig(path)
    if sig is None:
        return None
    key = ("ahash_crop", sig)
    if key in _CACHE:
        return _CACHE[key]
    val = None
    try:
        from PIL import Image
        with Image.open(path) as src:
            w, h = src.size
            target = (16 / 9) if w >= h else (9 / 16)
            ratio = (w / h) if h else target
            if abs(ratio - target) > 1e-3:
                if ratio > target:
                    nw = h * target
                    x0 = (w - nw) / 2.0
                    box = (int(round(x0)), 0, int(round(x0 + nw)), h)
                else:
                    nh = w / target
                    y0 = (h - nh) / 2.0
                    box = (0, int(round(y0)), w, int(round(y0 + nh)))
                im = src.convert("L").crop(box).resize((16, 16), Image.BICUBIC)
                val = _ahash_raw(im.tobytes())
    except Exception:
        val = None
    _CACHE[key] = val
    return val


def ahash_video(path: Path, ffmpeg: str = "") -> int | None:
    ffmpeg = ffmpeg or find_ffmpeg()
    return _cached_ahash(path, ffmpeg, video=True)


def _sig(path: Path):
    try:
        st = Path(path).stat()
        return (str(path), st.st_size, int(st.st_mtime))
    except OSError:
        return None


def _abs_key(path: Path) -> str:
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(path)


def _cache_path() -> Path:
    from .. import paths
    return paths.DATA / "ahash-cache.json"


def _ensure_disk_locked() -> None:
    """调用方必须已经拿着 _DISK_LOCK。"""
    global _DISK_LOADED
    if _DISK_LOADED:
        return
    p = _cache_path()
    data = {}
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
    _DISK.clear()
    _DISK.update(data)
    _DISK_LOADED = True


def _persist_disk_locked() -> None:
    """先写 .tmp 再 os.replace，避免并发窗口写坏文件。"""
    global _DISK_DIRTY
    p = _cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    on_disk = {}
    if p.is_file():
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                on_disk = loaded
        except Exception:
            on_disk = {}
    on_disk.update(_DISK)
    _DISK.clear()
    _DISK.update(on_disk)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(_DISK, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    os.replace(str(tmp), str(p))
    _DISK_DIRTY = False


def flush_ahash_cache() -> None:
    """把内存里的配图哈希落到 data/ahash-cache.json。"""
    with _DISK_LOCK:
        _ensure_disk_locked()
        if _DISK_DIRTY:
            _persist_disk_locked()


def clear_mem_cache() -> None:
    """深度刷新：丢掉进程内 (路径,大小,mtime) → 哈希 的缓存。"""
    _CACHE.clear()


def prune_ahash_cache() -> int:
    """清掉 data/ahash-cache.json 里 size/mtime 已经对不上（或文件没了）的条目。

    返回丢掉的条数。命中的留下，下次轻量校验不用重算。
    """
    global _DISK_DIRTY
    dropped = 0
    with _DISK_LOCK:
        _ensure_disk_locked()
        for key in list(_DISK.keys()):
            rec = _DISK.get(key)
            p = Path(key)
            drop = False
            if not isinstance(rec, dict) or not p.is_file():
                drop = True
            else:
                try:
                    st = p.stat()
                except OSError:
                    drop = True
                else:
                    if rec.get("size") != st.st_size or rec.get("mtime") != int(st.st_mtime):
                        drop = True
            if drop:
                _DISK.pop(key, None)
                dropped += 1
        if dropped:
            # 以剪完的表为准落盘，不能再和旧文件合并（否则删掉的又回来）
            p = _cache_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text(json.dumps(_DISK, ensure_ascii=False, separators=(",", ":")),
                           encoding="utf-8")
            os.replace(str(tmp), str(p))
            _DISK_DIRTY = False
    return dropped


def _disk_lookup(path: Path) -> int | None:
    sig = _sig(path)
    if sig is None:
        return None
    key = _abs_key(path)
    with _DISK_LOCK:
        _ensure_disk_locked()
        rec = _DISK.get(key)
    if not isinstance(rec, dict):
        return None
    if rec.get("size") != sig[1] or rec.get("mtime") != sig[2]:
        return None
    return ahash_from_hex(rec.get("hash") or "")


def _disk_store(path: Path, bits: int) -> None:
    global _DISK_DIRTY
    sig = _sig(path)
    if sig is None or bits is None:
        return
    key = _abs_key(path)
    rec = {"size": sig[1], "mtime": sig[2], "hash": ahash_hex(bits)}
    with _DISK_LOCK:
        _ensure_disk_locked()
        _DISK[key] = rec
        _DISK_DIRTY = True
        _persist_disk_locked()


def _cached_ahash(path: Path, ffmpeg: str, video: bool) -> int | None:
    sig = _sig(path)
    if sig is None:
        return None
    key = ("ahash", sig, video)
    if key in _CACHE:
        return _CACHE[key]
    if not video:
        hit = _disk_lookup(path)
        if hit is not None:
            _CACHE[key] = hit
            return hit
        val = _pillow_gray16(path)
        if val is None:
            val = _ffmpeg_gray16(ffmpeg, path, video=False)
        _CACHE[key] = val
        if val is not None:
            _disk_store(path, val)
        return val
    val = _ffmpeg_gray16(ffmpeg, path, video=True)
    _CACHE[key] = val
    return val


def _clip_path(task_dir: Path, m: Manifest, seg: dict) -> Path | None:
    takes = seg.get("takes") or []
    if takes:
        rel = (takes[-1].get("file") or "").replace("\\", "/")
        if rel:
            p = task_dir / rel
            return p if p.is_file() else None
    p = task_dir / "clips" / m.clip_name(seg)
    return p if p.is_file() else None


def _thresholds_for(seg: dict | None) -> tuple[int | None, int]:
    """按「参考是什么」返回 (ok, soft)。d≤ok 通过；ok<d≤soft 黄旗；d>soft 红旗拿错。

    红线为 None 表示没有红线（尾帧接龙：像不像只标黄，不作废）。
    接片对上一段尾帧：
      grok-single → DIST_OK 10 / DIST_SOFT 16（单图起片本来就是 0~7）
      Flow 双图 / 旧 grok 双图 → 红线 pipeline.BRIDGE_MATCH_DIST（30）；
        双图对上是 16~30，红线以内都算对上（距 25 → ok）。黄线 DIST_SOFT 不单独拦。
    非接片对配图：DIST_OK / DIST_SOFT。
    after_download 和 score_collected_bridge 都调这里，常量只留这一处选择。
    """
    seg = seg or {}
    role = seg.get("role") or ""
    eng = str(seg.get("bridge_engine") or "").lower()
    if role == "chain":
        # 接龙：没有红线。距离照算，>16 标黄，不进 wrong_clip。
        return None, DIST_SOFT
    if role == "bridge" and eng == "grok-single":
        return DIST_OK, DIST_SOFT
    if role == "bridge":
        # Flow 双图、旧 grok 双图、还没打标的接片：红线 30。
        from . import pipeline as pipemod
        red = int(pipemod.BRIDGE_MATCH_DIST)
        # DIST_SOFT 是黄线：双图 ≤30 都算对上，黄段不单独拦。
        _ = DIST_SOFT
        return red, red
    return DIST_OK, DIST_SOFT


def _first_frame_ref(task_dir: Path, m: Manifest, seg: dict) -> tuple[Path | None, str, str]:
    """首帧该对谁。锚点模式的接片对上一段尾帧；其余对配图。

    返回 (路径, 种类 tail|image, 给人看的标签)。
    """
    task_dir = Path(task_dir)
    role = seg.get("role") or ""
    mode = getattr(m, "mode", None)
    if mode is None and isinstance(m, dict):
        mode = m.get("mode")
    if role == "chain" or mode == "tail_chain":
        ch = seg.get("chain") if isinstance(seg.get("chain"), dict) else {}
        rel = (ch.get("ref_file") or "").replace("\\", "/")
        if rel:
            p = task_dir / rel
            if p.is_file():
                kind = ch.get("ref_kind") or "tail"
                lab = ch.get("ref_from") or ("起头图" if kind == "start" else "上一段尾帧")
                return p, kind, str(lab)
        return None, "tail", "上一段尾帧"
    if mode == "anchor_bridge" and role == "bridge":
        br = seg.get("bridge") if isinstance(seg.get("bridge"), dict) else {}
        rel = (br.get("tail_frame") or "").replace("\\", "/")
        if rel:
            p = task_dir / rel
            if p.is_file():
                from_s = br.get("from_seg")
                lab = ("s%03d 尾帧" % int(from_s)) if from_s else "上一段尾帧"
                return p, "tail", lab
        # 接片没有尾帧就跳过 4b，别拿自己的配图去比（那是结束状态，不是首帧）
        return None, "tail", "上一段尾帧"
    img = _image_path(task_dir, m, seg.get("image") or "")
    return img, "image", (seg.get("image") or "配图")


def _image_path(task_dir: Path, m: Manifest, p: str) -> Path | None:
    im = m.image(p or "")
    if not im:
        return None
    rel = (im.get("file") or "").replace("\\", "/")
    if not rel:
        return None
    path = task_dir / rel
    if path.is_file():
        return path
    # 扩展名对不上时按主干再找一次
    stem = path.stem.lower()
    if path.parent.is_dir():
        for cand in path.parent.iterdir():
            if cand.is_file() and cand.stem.lower() == stem:
                return cand
    return None


def _issue(code: str, text: str, level: str) -> dict:
    return {"level": level, "text": text, "code": code}


def issues_from_result(r: dict) -> list[dict]:
    out = []
    code = r.get("code") or ""
    if code == E_DUP_ASSET:
        other = r.get("dup_with")
        lb = f"s{int(other):03d}" if other else "?"
        out.append(_issue(E_DUP_ASSET, f"与 {lb} 是同一个视频", "bad"))
    elif code == E_WRONG_CLIP:
        d = r.get("frame_dist")
        extra = f"（距 {d}）" if d is not None else ""
        if r.get("ref_kind") == "tail":
            lab = r.get("ref_label") or "上一段尾帧"
            out.append(_issue(E_WRONG_CLIP, f"拿错片：首帧不是{lab}{extra}", "bad"))
        else:
            out.append(_issue(E_WRONG_CLIP, f"拿错片：首帧不是这张配图{extra}", "bad"))
    elif code == W_FRAME_SOFT:
        d = r.get("frame_dist")
        extra = f"（距 {d}）" if d is not None else ""
        if r.get("ref_kind") == "tail":
            lab = r.get("ref_label") or "上一段尾帧"
            out.append(_issue(W_FRAME_SOFT, f"首帧和{lab}不太像{extra}", "warn"))
        else:
            out.append(_issue(W_FRAME_SOFT, f"首帧和配图不太像{extra}", "warn"))
    twins = r.get("twins") or []
    for t in twins:
        lb = f"s{int(t):03d}"
        out.append(_issue(W_FRAME_TWIN, f"首帧和 {lb} 几乎一样，请人工确认", "warn"))
    return out


def _merge_issues(sg: dict, new: list[dict]) -> None:
    chk = dict(sg.get("check") or {})
    old = [i for i in (chk.get("issues") or [])
           if (i.get("code") not in VERIFY_CODES)]
    chk["issues"] = old + new
    sg["check"] = chk


def _void_to(task_dir: Path, dest: Path, folder: str) -> Path | None:
    """把文件挪进 clips/<folder>/，返回新路径。"""
    dest = Path(dest)
    if not dest.is_file():
        return None
    junk = Path(task_dir) / "clips" / folder
    junk.mkdir(parents=True, exist_ok=True)
    name = f"{int(time.time())}-{dest.name}"
    target = junk / name
    n = 1
    while target.exists():
        target = junk / f"{int(time.time())}-{n}-{dest.name}"
        n += 1
    shutil.move(str(dest), str(target))
    return target


def void_clip(task_dir: Path, dest: Path) -> Path | None:
    """把拿错/重复的文件挪进 clips/_错件/，返回新路径。"""
    return _void_to(task_dir, dest, "_错件")


def _label(s: int) -> str:
    return f"s{int(s):03d}"


def after_download(m: Manifest, task_dir: Path, seg: dict, dest: Path,
                   *, asset_url: str = "", gen_id: str = "",
                   log=None, apply: bool = True) -> dict:
    """下载刚落盘后立刻跑。apply=True 时撞车/拿错会挪走：
    非接片打回 submitted 重新取地址；接片打回 planned 重新提交。"""
    def _log(msg: str):
        if log:
            try:
                log(msg)
            except Exception:
                pass

    dest = Path(dest)
    task_dir = Path(task_dir)
    sn = int(seg.get("s") or 0)
    ffmpeg = find_ffmpeg()
    try:
        rel_file = str(dest.relative_to(task_dir)).replace("\\", "/")
    except ValueError:
        rel_file = dest.name
    take = {
        "file": rel_file,
        "gen_id": gen_id or seg.get("gen_id") or "",
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "asset_url": asset_url or "",
        "sha256": "",
        "bytes": 0,
        "mtime": 0,
        "frame_ahash": "",
        "frame_dist": None,
        "verdict": "ok",
    }
    result = {
        "s": sn, "verdict": "ok", "code": "", "frame_dist": None,
        "dup_with": None, "twins": [], "take": take, "voided": False,
        "sha256": "",
    }
    try:
        if not dest.is_file():
            result["verdict"] = "wrong_clip"
            result["code"] = E_WRONG_CLIP
            return result

        st = dest.stat()
        take["bytes"] = int(st.st_size)
        take["mtime"] = int(st.st_mtime)
        digest = sha256_file(dest)
        take["sha256"] = digest
        result["sha256"] = digest

        # 4a：先比大小，只有同样大小的才算撞车。
        # 只认 take 上已经记过的 bytes，不去盘上扫别的文件
        # （否则测试里同模板合成的小 mp4 会被当成撞车，生成任务死循环）。
        same: list[tuple[dict, dict, int]] = []
        for sg in m.segments:
            osn = int(sg.get("s") or 0)
            if osn == sn:
                continue
            for tk in sg.get("takes") or []:
                ob = tk.get("bytes")
                if ob is None or ob == "":
                    continue
                try:
                    ob = int(ob)
                except (TypeError, ValueError):
                    continue
                if ob == take["bytes"]:
                    same.append((sg, tk, osn))
                    break
        if same:
            for _sg, tk, osn in same:
                other = (tk.get("sha256") or "").strip().lower()
                if not other:
                    rel = (tk.get("file") or "").replace("\\", "/")
                    op = (task_dir / rel) if rel else None
                    if op is not None and op.is_file():
                        other = sha256_file(op)
                        tk["sha256"] = other
                if other and other == digest.lower():
                    take["verdict"] = "dup"
                    result["verdict"] = "dup"
                    result["code"] = E_DUP_ASSET
                    result["dup_with"] = osn
                    _log(f"✗ {_label(sn)} 下载到的和 {_label(osn)} 是同一个文件，已作废，重新取地址")
                    if apply:
                        _apply_void(m, task_dir, seg, dest, result, log=_log)
                    return result

        # 4b 首帧对参考图。接片对上一段尾帧，其余对配图。
        vhash = ahash_video(dest, ffmpeg) if ffmpeg else None
        if vhash is not None:
            take["frame_ahash"] = ahash_hex(vhash)
            img, ref_kind, ref_lab = _first_frame_ref(task_dir, m, seg)
            result["ref_kind"] = ref_kind
            result["ref_label"] = ref_lab
            ih = ahash_image(img, ffmpeg) if img else None
            if ih is not None:
                d = hamming(vhash, ih)
                if ref_kind == "image" and d > 0:
                    # 配图不是精确 16:9 / 9:16（比如正方形）时，交给 Flow 的是居中裁过的那一块，
                    # 片子首帧也就是那一块。整张图压成 16×16 去比会差很远（T0122：对的片算出距 105）。
                    # 所以再拿"裁过的那一块"比一次，取近的那个。换成别的图照样差得远，不会放错片。
                    ic = ahash_image_cropped(img)
                    if ic is not None:
                        d = min(d, hamming(vhash, ic))
                take["frame_dist"] = d
                result["frame_dist"] = d
                ok_th, soft_th = _thresholds_for(seg)
                if (seg.get("role") == "chain"
                        or getattr(m, "mode", None) == "tail_chain"):
                    ch = seg.get("chain") if isinstance(seg.get("chain"), dict) else {}
                    ch["head_dist"] = d
                    seg["chain"] = ch
                if ok_th is None:
                    # 接龙：没有红线。距 > soft 只标黄，不进 wrong_clip，不 _apply_void。
                    if soft_th is not None and d > soft_th:
                        take["verdict"] = "soft"
                        result["verdict"] = "soft"
                        result["code"] = W_FRAME_SOFT
                elif d > soft_th:
                    rc = struct_recheck(m, task_dir, seg, dest, img, ref_kind, ffmpeg)
                    if rc is not None:
                        take["frame_sim"] = rc["sim"]
                        result["frame_sim"] = rc["sim"]
                    if rc is not None and rc.get("ok"):
                        # 均值哈希被色调 / 画幅骗了：画面结构对得上，收下（不作废、不重下）
                        take["frame_rescued"] = True
                        result["frame_rescued"] = True
                        _log(_rescue_line(sn, d, rc, ref_lab))
                    else:
                        take["verdict"] = "wrong_clip"
                        result["verdict"] = "wrong_clip"
                        result["code"] = E_WRONG_CLIP
                        if apply:
                            _apply_void(m, task_dir, seg, dest, result, log=_log)
                        else:
                            _log_void_line(_log, seg, sn, ref_kind, ref_lab, d, False)
                        if rc is not None:
                            _log(_mismatch_detail(sn, rc, ref_lab))
                        return result
                elif ok_th is not None and d > ok_th:
                    take["verdict"] = "soft"
                    result["verdict"] = "soft"
                    result["code"] = W_FRAME_SOFT

        # 4c 同图段位的兜底：配图不同但首帧几乎一样
        if vhash is not None:
            my_p = seg.get("image") or ""
            twins = []
            for sg in m.segments:
                osn = int(sg.get("s") or 0)
                if osn == sn:
                    continue
                if (sg.get("image") or "") == my_p:
                    continue
                for tk in sg.get("takes") or []:
                    oh = ahash_from_hex(tk.get("frame_ahash") or "")
                    if oh is None:
                        continue
                    if hamming(vhash, oh) < TWIN_DIST:
                        twins.append(osn)
                        break
            if twins:
                result["twins"] = twins

        # 收片过关：清连续作废计数，下一轮从完整日志重新起
        clear_void_streak(seg)
        return result
    finally:
        result["issues"] = issues_from_result(result)


def clear_void_streak(seg: dict | None = None) -> None:
    """收片成功后清连续作废计数。seg 为空则全清（测试用）。"""
    if seg is None:
        _VOID_STREAK.clear()
        return
    _VOID_STREAK.pop(id(seg), None)


def _next_void_tier(action: str) -> str:
    """submitted → planned → failed。同一个文件再来一次，跳一档。"""
    if action == "submitted":
        return "planned"
    if action == "planned":
        return "failed"
    return action


def _log_void_line(log, seg: dict, sn: int, ref_kind: str, ref_lab: str,
                   dist, same_file: bool) -> None:
    """同一段连续作废：第一次打完整行，之后合并成 × N 次。"""
    if not callable(log):
        return
    n = int(_VOID_STREAK.get(id(seg)) or 0) + 1
    _VOID_STREAK[id(seg)] = n
    lab = _label(sn)
    ref = ref_lab or "配图"
    dtxt = f"距 {dist}" if dist is not None else "距 ?"
    if n == 1:
        if ref_kind == "tail":
            log(f"✗ {lab} 接片首帧对不上{ref}（{dtxt}），作废，重新提交")
        else:
            log(f"✗ {lab} 首帧对不上{ref}（{dtxt}），已作废，重新取地址")
        return
    same = "，同一个文件" if same_file else ""
    log(f"✗ {lab} 首帧对不上 {ref}（{dtxt}）× {n} 次{same}")


def _apply_void(m: Manifest, task_dir: Path, seg: dict, dest: Path, result: dict,
                log=None) -> None:
    is_bridge = (seg.get("role") == "bridge")
    take = result.get("take") or {}
    digest = (result.get("sha256") or take.get("sha256") or "").strip().lower()
    dest = Path(dest)
    # 挪走之前先记下 sha：命中 voided_sha 说明地址/内容根本没变，重试无意义
    if not digest and dest.is_file():
        digest = sha256_file(dest)
        result["sha256"] = digest
        take["sha256"] = digest
        result["take"] = take
    prev = [str(x).strip().lower() for x in (seg.get("voided_sha") or []) if x]
    same_file = bool(digest) and digest in prev
    result["same_file"] = same_file
    if digest:
        prev.append(digest)
        seg["voided_sha"] = prev[-3:]

    # 接片：片子错了，挪 _弃用。非接片：可能拿错邻居的片，挪 _错件。
    _void_to(task_dir, dest, "_弃用" if is_bridge else "_错件")
    result["voided"] = True
    takes = list(seg.get("takes") or [])
    rel = take.get("file") or ""
    if rel:
        takes = [tk for tk in takes if (tk.get("file") or "").replace("\\", "/") != rel]
    seg["takes"] = takes

    sn = int(seg.get("s") or result.get("s") or 0)
    dist = result.get("frame_dist")
    verdict = (result.get("verdict") or "").strip().lower()

    if same_file and callable(log):
        short = digest[:8] if digest else "?"
        log(f"✗ {_label(sn)} 又下到同一个文件（{short}），重试无意义，直接换一档")

    if is_bridge:
        # 53：对不上单独计数。fail_count 仍加（图的 gen_fail / blocked 还在用），
        # 但「两次出局」只看 mismatch_count。
        fail = int(seg.get("fail_count") or 0) + 1
        mis = int(seg.get("mismatch_count") or 0) + 1
        seg["fail_count"] = fail
        seg["mismatch_count"] = mis
        action = "failed" if mis >= 2 else "planned"
        if same_file:
            action = _next_void_tier(action)
        if action == "failed":
            seg["status"] = "failed"
            seg["error"] = "接片两次首帧都对不上（检查台可重试）"
            if mis < 2:
                seg["mismatch_count"] = 2
        else:
            seg["status"] = "planned"
            seg["error"] = None
        seg["gen_id"] = None
        seg.pop("gen_conv", None)
        result["void_action"] = action
        _merge_issues(seg, issues_from_result(result))
        _log_void_line(log, seg, sn, "tail",
                       result.get("ref_label") or "上一段尾帧", dist, same_file)
        return

    # 非接片：wrong_clip 计数；1 次重新取地址，2 次换 gen_id 重提，4 次停
    if verdict == "wrong_clip":
        n = int(seg.get("wrongclip_count") or 0) + 1
        seg["wrongclip_count"] = n
        if n >= 4:
            action = "failed"
        elif n >= 2:
            action = "planned"
        else:
            action = "submitted"
    else:
        action = "submitted"
    if same_file:
        action = _next_void_tier(action)
    result["void_action"] = action
    if action == "failed":
        seg["status"] = "failed"
        seg["gen_id"] = None
        seg.pop("gen_conv", None)
        if verdict == "wrong_clip":
            dtxt = dist if dist is not None else "?"
            seg["error"] = f"首帧一直对不上配图（距 {dtxt}），换图或手动检查"
        else:
            seg["error"] = "同一文件反复作废"
    elif action == "planned":
        seg["status"] = "planned"
        seg["gen_id"] = None
        seg.pop("gen_conv", None)
        seg["error"] = None
    else:
        # 生成物还在那边，打回 submitted，gen_id 保留，重新取 URL
        seg["status"] = "submitted"
    _merge_issues(seg, issues_from_result(result))
    if verdict == "wrong_clip":
        _log_void_line(
            log, seg, sn, result.get("ref_kind") or "image",
            result.get("ref_label") or (seg.get("image") or "配图"),
            dist, same_file)


def _fill_twins(results: list[dict], m: Manifest) -> None:
    """本任务内任意两段首帧距离 <4 且配图不是同一张 → 互相打黄旗。"""
    by_s = {int(r["s"]): r for r in results}
    hashes = {}
    images = {}
    for sg in m.segments:
        sn = int(sg.get("s") or 0)
        images[sn] = sg.get("image") or ""
        r = by_s.get(sn)
        hx = (r or {}).get("take", {}).get("frame_ahash") if r else ""
        if not hx:
            tks = sg.get("takes") or []
            hx = (tks[-1].get("frame_ahash") if tks else "") or ""
        bits = ahash_from_hex(hx)
        if bits is not None:
            hashes[sn] = bits
    keys = sorted(hashes)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if images.get(a) == images.get(b):
                continue
            if hamming(hashes[a], hashes[b]) < TWIN_DIST:
                for x, y in ((a, b), (b, a)):
                    r = by_s.get(x)
                    if r is None:
                        continue
                    twins = list(r.get("twins") or [])
                    if y not in twins:
                        twins.append(y)
                    r["twins"] = twins


def _tk_stale(tk: dict, st) -> bool:
    """大小或 mtime 对不上（缺 mtime 也算对不上）→ 要重算。"""
    if tk.get("bytes") != int(st.st_size):
        return True
    if tk.get("mtime") != int(st.st_mtime):
        return True
    return False


def needs_recompute(m: Manifest, task_dir: Path, only=None) -> bool:
    """clips 有变动、缺视频哈希、配图缓存未命中、或同大小缺 sha256 → 要跑。"""
    task_dir = Path(task_dir)
    want = {int(x) for x in only} if only else None
    sizes: dict[int, list[tuple[int, dict, bool]]] = {}
    for sg in m.segments:
        sn = int(sg.get("s") or 0)
        if want is not None and sn not in want:
            continue
        path = _clip_path(task_dir, m, sg)
        if not path:
            continue
        tks = sg.get("takes") or []
        tk = tks[-1] if tks else {}
        try:
            st = path.stat()
        except OSError:
            continue
        stale = _tk_stale(tk, st)
        if stale or not tk.get("frame_ahash"):
            return True
        img, _kind, _lab = _first_frame_ref(task_dir, m, sg)
        if img is not None and _disk_lookup(img) is None:
            return True
        sizes.setdefault(int(st.st_size), []).append((sn, tk, stale))
    for recs in sizes.values():
        if len(recs) < 2:
            continue
        for _sn, tk, stale in recs:
            if stale or not tk.get("sha256"):
                return True
    return False


def verify_task(m: Manifest, task_dir: Path, only=None, apply: bool = False,
                log=None, progress=None) -> list[dict]:
    """对已落盘的 takes 跑 4a/4b/4c。默认只报告+写凭证，不挪文件。"""
    task_dir = Path(task_dir)
    want = None
    if only:
        want = {int(x) for x in only}
    ffmpeg = find_ffmpeg()
    results = []
    computed: dict[int, dict] = {}
    for sg in m.segments:
        sn = int(sg.get("s") or 0)
        if want is not None and sn not in want:
            continue
        path = _clip_path(task_dir, m, sg)
        if not path:
            continue
        tks = list(sg.get("takes") or [])
        tk = dict(tks[-1]) if tks else {
            "file": str(path.relative_to(task_dir)).replace("\\", "/"),
            "gen_id": sg.get("gen_id") or "",
            "at": "",
        }
        st = path.stat()
        stale = _tk_stale(tk, st)
        tk["bytes"] = int(st.st_size)
        tk["mtime"] = int(st.st_mtime)
        computed[sn] = {"sg": sg, "path": path, "tk": tk, "stale": stale}

    # 4a：先按大小分组，只有撞车的才读全文 sha256
    by_size: dict[int, list[int]] = {}
    for sn, rec in computed.items():
        by_size.setdefault(int(rec["tk"]["bytes"]), []).append(sn)
    collide = {sn for sns in by_size.values() if len(sns) > 1 for sn in sns}

    sha_owner: dict[str, int] = {}
    n_all = len(computed)
    n_done = 0
    for sn, rec in computed.items():
        tk = rec["tk"]
        if sn in collide:
            if rec["stale"] or not tk.get("sha256"):
                tk["sha256"] = sha256_file(rec["path"])
        digest = (tk.get("sha256") or "").lower()
        if digest and digest not in sha_owner:
            sha_owner[digest] = sn
        if rec["stale"] or not tk.get("frame_ahash"):
            vh = ahash_video(rec["path"], ffmpeg)
            if vh is not None:
                tk["frame_ahash"] = ahash_hex(vh)
        n_done += 1
        if progress:
            try:
                progress(n_done, n_all)
            except Exception:
                pass

    def rec_stale(sn: int) -> bool:
        return bool((computed.get(sn) or {}).get("stale"))

    for sn, rec in computed.items():
        sg, path, tk = rec["sg"], rec["path"], rec["tk"]
        digest = (tk.get("sha256") or "").lower()
        result = {
            "s": sn, "verdict": "ok", "code": "", "frame_dist": None,
            "dup_with": None, "twins": [], "take": tk, "voided": False,
            "sha256": digest,
        }
        owner = sha_owner.get(digest)
        if digest and owner is not None and owner != sn:
            tk["verdict"] = "dup"
            result["verdict"] = "dup"
            result["code"] = E_DUP_ASSET
            result["dup_with"] = owner
            if apply:
                _apply_void(m, task_dir, sg, path, result)
            _write_take(sg, tk)
            results.append(result)
            continue

        vh = ahash_from_hex(tk.get("frame_ahash") or "")
        img, ref_kind, ref_lab = _first_frame_ref(task_dir, m, sg)
        result["ref_kind"] = ref_kind
        result["ref_label"] = ref_lab
        ih = ahash_image(img, ffmpeg) if img else None
        if vh is not None and ih is not None:
            d = hamming(vh, ih)
            if ref_kind == "image" and d > 0:
                ic = ahash_image_cropped(img)
                if ic is not None:
                    d = min(d, hamming(vh, ic))
            tk["frame_dist"] = d
            result["frame_dist"] = d
            ok_th, soft_th = _thresholds_for(sg)
            if tk.get("human_ok"):
                # 人工看过、认定内容是对的（比如 T0122 s001~s004 带黑边的片）：不再判错件
                tk["verdict"] = "ok"
                result["verdict"] = "ok"
                _write_take(sg, tk)
                results.append(result)
                continue
            if d > soft_th:
                rescued = bool(tk.get("frame_rescued")) and not rec_stale(sn)
                if not rescued:
                    rc = struct_recheck(m, task_dir, sg, path, img, ref_kind, ffmpeg)
                    if rc is not None:
                        tk["frame_sim"] = rc["sim"]
                        rescued = bool(rc.get("ok"))
                        tk["frame_rescued"] = rescued or None
                if rescued:
                    # 结构复核过了（均值哈希被色调 / 画幅骗了）：不再判错件
                    tk["verdict"] = "ok"
                    result["verdict"] = "ok"
                    result["frame_rescued"] = True
                    _write_take(sg, tk)
                    results.append(result)
                    continue
                tk["verdict"] = "wrong_clip"
                result["verdict"] = "wrong_clip"
                result["code"] = E_WRONG_CLIP
                if apply:
                    _apply_void(m, task_dir, sg, path, result)
                _write_take(sg, tk)
                results.append(result)
                continue
            if d > ok_th:
                tk["verdict"] = "soft"
                result["verdict"] = "soft"
                result["code"] = W_FRAME_SOFT
            else:
                tk["verdict"] = "ok"
                result["verdict"] = "ok"
        else:
            tk.setdefault("verdict", tk.get("verdict") or "ok")
            result["verdict"] = tk.get("verdict") or "ok"
        _write_take(sg, tk)
        results.append(result)

    _fill_twins(results, m)
    for r in results:
        r["issues"] = issues_from_result(r)
        if r.get("voided"):
            continue
        sg = m.seg(int(r["s"]))
        if sg:
            _merge_issues(sg, r["issues"])
    return results


def apply_results(m: Manifest, results: list[dict]) -> None:
    """把校验结果写进一份刚从盘上读出来的 manifest，避免盖掉用户刚改的标记。"""
    for r in results or []:
        sg = m.seg(int(r["s"]))
        if not sg:
            continue
        tk = r.get("take")
        if tk:
            _write_take(sg, tk)
        _merge_issues(sg, r.get("issues") or issues_from_result(r))


def _write_take(sg: dict, tk: dict) -> None:
    takes = list(sg.get("takes") or [])
    rel = (tk.get("file") or "").replace("\\", "/")
    if takes and (takes[-1].get("file") or "").replace("\\", "/") == rel:
        takes[-1] = tk
    elif takes:
        # 字段补到最后一条
        cur = dict(takes[-1])
        cur.update({k: tk[k] for k in ("sha256", "bytes", "mtime", "frame_ahash",
                                       "frame_dist", "frame_sim", "frame_rescued", "verdict", "asset_url")
                    if k in tk and tk[k] not in (None, "")})
        takes[-1] = cur
    else:
        takes.append(tk)
    sg["takes"] = takes


def verify_dir(task_dir, only=None, apply: bool = False, log=None) -> list[dict]:
    d = Path(task_dir)
    m = Manifest.load(d)
    return verify_task(m, d, only=only, apply=apply, log=log)


def light_check(m: Manifest, task_dir: Path, only=None, progress=None) -> list[dict]:
    """收片面板打开 / 收片完成时：sha256 + 首帧，不做响度、不做时长重扫。"""
    return verify_task(m, task_dir, only=only, apply=False, log=None,
                       progress=progress)


def run_api(tid: str, only=None, apply: bool = False) -> dict:
    from .. import tasks as taskmod
    t = taskmod.get(tid)
    if not t.get("dir"):
        raise OranteError("这个任务还没准备", "先在任务面板点「准备」。")
    d = Path(t["dir"])
    m = Manifest.load(d)
    results = verify_task(m, d, only=only, apply=apply)
    m.save(d)
    return {"results": results}
