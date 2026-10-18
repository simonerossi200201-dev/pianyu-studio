# -*- coding: utf-8 -*-
"""落盘体检：时长、音轨、md5、空文件。

从 Orante1 app/checks.py 原样搬来，只改 import 和 ffmpeg 查找顺序。
只做「不碰浏览器、不烧额度」的那几条。首帧 pHash 对源图、whisper 转写这两条
留给原脚本 —— grok_pipe.py 的 cmd_verify 已经写好了，不在这里重写一遍。

音轨这一条是纯 python 读 mp4 的 box 树，不依赖 ffprobe：
有没有 soun 轨、音频轨多长，比人耳在 4 倍速下听要靠谱得多。
"""
from __future__ import annotations

import hashlib
import os
import struct
import re

from .procutil import run_quiet

_CACHE = {}
_FFMPEG = None


def clear_mem_cache() -> None:
    """深度刷新：丢掉进程内 probe() 的 (路径,大小,mtime) 缓存。"""
    _CACHE.clear()


def _sig(path):
    try:
        st = os.stat(path)
        return (path, st.st_size, int(st.st_mtime))
    except OSError:
        return None


# ------------------------------------------------------------ mp4 box 树

def _boxes(f, start, end):
    """逐个 box 产出 (类型, 内容起点, box 终点)。"""
    pos = start
    while pos < end - 8:
        f.seek(pos)
        head = f.read(8)
        if len(head) < 8:
            return
        size = struct.unpack(">I", head[:4])[0]
        typ = head[4:8]
        if size == 1:
            ext = f.read(8)
            if len(ext) < 8:
                return
            size = struct.unpack(">Q", ext)[0]
            body = pos + 16
        elif size == 0:
            size = end - pos
            body = pos + 8
        else:
            body = pos + 8
        if size < 8:
            return
        yield typ, body, pos + size
        pos += size


def _find(f, start, end, want):
    for typ, body, stop in _boxes(f, start, end):
        if typ == want:
            return body, stop
    return None, None


def _mvhd(f, body):
    f.seek(body)
    blob = f.read(32)
    if len(blob) < 20:
        return None
    if blob[0] == 1:
        scale, dur = struct.unpack(">IQ", blob[20:32])
    else:
        scale, dur = struct.unpack(">II", blob[12:20])
    return (dur / scale) if scale else None


def _mdhd(f, body):
    """和 mvhd 同样的排布，返回这条轨道的秒数。"""
    return _mvhd(f, body)


def parse_mp4(path):
    """返回 {duration, tracks:[{kind, duration}]}。读不动就返回 None。"""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            end = f.tell()
            moov_b, moov_e = _find(f, 0, end, b"moov")
            if moov_b is None:
                return None
            out = {"duration": None, "tracks": []}
            mv_b, _ = _find(f, moov_b, moov_e, b"mvhd")
            if mv_b is not None:
                out["duration"] = _mvhd(f, mv_b)
            for typ, tb, te in _boxes(f, moov_b, moov_e):
                if typ != b"trak":
                    continue
                md_b, md_e = _find(f, tb, te, b"mdia")
                if md_b is None:
                    continue
                kind, dur = "", None
                h_b, _ = _find(f, md_b, md_e, b"hdlr")
                if h_b is not None:
                    f.seek(h_b + 8)
                    kind = (f.read(4) or b"").decode("latin-1").strip()
                d_b, _ = _find(f, md_b, md_e, b"mdhd")
                if d_b is not None:
                    dur = _mdhd(f, d_b)
                out["tracks"].append({"kind": kind, "duration": dur})
            return out
    except Exception:
        return None


def mp4_duration(path):
    info = parse_mp4(path)
    return (info or {}).get("duration")


def md5_of(path, chunk=1 << 20):
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            while True:
                b = f.read(chunk)
                if not b:
                    break
                h.update(b)
    except OSError:
        return ""
    return h.hexdigest()


def probe(path):
    """{size, duration, audio_dur, has_audio, md5}，按 (路径,大小,mtime) 缓存。"""
    sig = _sig(path)
    if sig is None:
        return None
    hit = _CACHE.get(sig)
    if hit:
        return hit
    info = parse_mp4(path) or {}
    tracks = info.get("tracks") or []
    audio = [t for t in tracks if t.get("kind") == "soun"]
    out = {
        "size": sig[1],
        "duration": info.get("duration"),
        "has_audio": bool(audio),
        "audio_dur": (audio[0].get("duration") if audio else None),
        "md5": md5_of(path),
    }
    _CACHE[sig] = out
    return out


# ------------------------------------------------------------ 静音检测

FFMPEG_CFG_KEY = "tools.ffmpeg"
FFMPEG_PIP = "imageio-ffmpeg"
_FFMPEG_HOW = ""
_FFMPEG_MISS_AT = 0.0
_FFMPEG_MISS_TTL = 20.0   # 没找到不长期记住：用户随时可能装好 / 拷进 bin


def _ffmpeg_candidates() -> list[tuple[str, str]]:
    """按顺序列出可能的 ffmpeg 位置：(路径, 来源)。只列，不判断。

    规格 95：设置里填的路径 → ROOT/bin → pip 装的 imageio-ffmpeg → PATH
    → Windows 常见安装位置 → 老 grok_pipe。
    """
    import shutil as _shutil
    from .. import paths as _paths
    out: list[tuple[str, str]] = []
    try:
        from .. import config as _cfg
        c = str(_cfg.get(FFMPEG_CFG_KEY, "") or "").strip().strip('"')
    except Exception:
        c = ""
    if c:
        if os.path.isdir(c):
            for name in ("ffmpeg.exe", "ffmpeg"):
                out.append((os.path.join(c, name), "设置"))
                out.append((os.path.join(c, "bin", name), "设置"))
        else:
            out.append((c, "设置"))
    for name in ("ffmpeg.exe", "ffmpeg"):
        out.append((str(_paths.ROOT / "bin" / name), "Orante/bin"))
    try:
        import imageio_ffmpeg as _iio  # type: ignore
        try:
            out.append((str(_iio.get_ffmpeg_exe()), "imageio-ffmpeg"))
        except Exception:
            pass
    except Exception:
        pass
    which = _shutil.which("ffmpeg")
    if which:
        out.append((which, "PATH"))
    if os.name == "nt":
        env = os.environ
        home = env.get("USERPROFILE") or ""
        spots = [
            os.path.join(env.get("LOCALAPPDATA") or "", "Microsoft", "WinGet", "Links", "ffmpeg.exe"),
            os.path.join(env.get("ProgramData") or "", "chocolatey", "bin", "ffmpeg.exe"),
            os.path.join(home, "scoop", "shims", "ffmpeg.exe"),
            os.path.join(env.get("ProgramFiles") or "", "ffmpeg", "bin", "ffmpeg.exe"),
            os.path.join(env.get("LOCALAPPDATA") or "", "Programs", "ffmpeg", "bin", "ffmpeg.exe"),
            r"C:\ffmpeg\bin\ffmpeg.exe",
            r"D:\ffmpeg\bin\ffmpeg.exe",
        ]
        for s in spots:
            if s and not s.startswith(os.sep):
                out.append((s, "常见位置"))
    try:
        _paths.add_legacy_to_sys_path()
        import grok_pipe as _gp  # type: ignore
        found = _gp.find_ffmpeg()
        if found:
            out.append((str(found), "旧配置"))
    except Exception:
        pass
    return out


def find_ffmpeg():
    """找 ffmpeg。找到就记住；没找到只记 20 秒（装好 / 拷进 bin 立刻生效）。"""
    global _FFMPEG, _FFMPEG_HOW, _FFMPEG_MISS_AT
    import time as _t
    if _FFMPEG:
        if os.path.isfile(_FFMPEG):
            return _FFMPEG
        _FFMPEG = None
    if _FFMPEG == "" and (_t.monotonic() - _FFMPEG_MISS_AT) < _FFMPEG_MISS_TTL:
        return ""
    for p, how in _ffmpeg_candidates():
        try:
            if p and os.path.isfile(p):
                _FFMPEG = p
                _FFMPEG_HOW = how
                return _FFMPEG
        except Exception:
            continue
    _FFMPEG = ""
    _FFMPEG_HOW = ""
    _FFMPEG_MISS_AT = _t.monotonic()
    return ""


def reset_ffmpeg_cache() -> None:
    global _FFMPEG, _FFMPEG_HOW, _FFMPEG_MISS_AT
    _FFMPEG = None
    _FFMPEG_HOW = ""
    _FFMPEG_MISS_AT = 0.0


FFMPEG_FIX_HINT = ("尾帧接龙 / 锚点接片要靠 ffmpeg 抽尾帧。修法任选一：① 设置 → 工具 → 点「一键安装 ffmpeg」"
                   "（pip 装 imageio-ffmpeg，约 30 MB）；② 把 ffmpeg.exe 放进 Orante\\bin；"
                   "③ 设置里填 ffmpeg.exe 的完整路径。")


def ffmpeg_status() -> dict:
    """给设置 / 自检 / ④ 生成看的：{ok, path, how, hint, pip}。"""
    reset_ffmpeg_cache()
    p = find_ffmpeg()
    pip_ok = False
    try:
        import imageio_ffmpeg  # noqa: F401
        pip_ok = True
    except Exception:
        pip_ok = False
    return {
        "ok": bool(p),
        "path": p or "",
        "how": _FFMPEG_HOW if p else "",
        "pip": pip_ok,
        "hint": "" if p else FFMPEG_FIX_HINT,
    }


def install_ffmpeg(log=None, timeout: int = 600) -> dict:
    """一键装：本 venv 里 pip install imageio-ffmpeg，装完重新找。返回 ffmpeg_status()。"""
    import subprocess
    import sys
    py = sys.executable or "python"
    # pythonw 没有控制台，pip 在它下面偶尔挂住；同目录有 python.exe 就换它跑
    if py.lower().endswith("pythonw.exe"):
        cand = py[:-len("pythonw.exe")] + "python.exe"
        if os.path.isfile(cand):
            py = cand
    cmd = [py, "-m", "pip", "install", "-q", "--disable-pip-version-check", FFMPEG_PIP]
    if callable(log):
        try:
            log("· pip install %s（约 30 MB，可能要一两分钟）" % FFMPEG_PIP)
        except Exception:
            pass
    try:
        r = run_quiet(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        st = ffmpeg_status()
        st["error"] = "pip 超时（%d 秒）" % timeout
        return st
    except Exception as e:  # noqa: BLE001
        st = ffmpeg_status()
        st["error"] = str(e)[:200]
        return st
    st = ffmpeg_status()
    if r.returncode != 0 and not st["ok"]:
        err = (r.stderr or b"").decode("utf-8", "ignore").strip()[-400:]
        st["error"] = err or ("pip 退出码 %s" % r.returncode)
    return st


_MEAN_RE = re.compile(r"mean_volume:\s*(-?[0-9.]+) dB")
_MAX_RE = re.compile(r"max_volume:\s*(-?[0-9.]+) dB")


def loudness(path):
    """用 ffmpeg volumedetect 量一下响度。没有 ffmpeg 就返回 None。

    -vn 只解音频，一条 10 秒的片子大约几十毫秒。
    """
    exe = find_ffmpeg()
    if not exe:
        return None
    try:
        r = run_quiet(
            [exe, "-hide_banner", "-nostats", "-i", path, "-vn",
             "-af", "volumedetect", "-f", "null", "-"],
            timeout=60)
        err = (r.stderr or b"").decode("utf-8", "ignore")
    except Exception:
        return None
    m, x = _MEAN_RE.search(err), _MAX_RE.search(err)
    if not m:
        return None
    return {"mean_db": float(m.group(1)),
            "max_db": float(x.group(1)) if x else None}


# ------------------------------------------------------------ 判定

DUR_TOLERANCE = 1.6
AUDIO_SHORT_RATIO = 0.7
SILENT_MEAN_DB = -50.0
QUIET_MEAN_DB = -38.0


def issues_for(seg, info, md5_owner, loud=None):
    """一段的问题清单。md5_owner: md5 -> 第一个用它的段标签。"""
    out = []
    if not seg.get("exists"):
        return [{"level": "miss", "text": "缺片"}]
    if not info or not info.get("size"):
        return [{"level": "bad", "text": "空文件"}]
    if info["size"] < 20000:
        out.append({"level": "bad", "text": "文件过小 %d B" % info["size"]})

    want = seg.get("dur_expect")
    got = info.get("duration")
    if want and got and abs(got - want) > DUR_TOLERANCE:
        out.append({"level": "bad",
                    "text": "时长不符 档位%.0fs 实际%.1fs" % (want, got)})

    if not info.get("has_audio"):
        out.append({"level": "bad", "text": "没有音轨"})
    else:
        ad, vd = info.get("audio_dur"), info.get("duration")
        if ad and vd and vd > 0 and ad < vd * AUDIO_SHORT_RATIO:
            out.append({"level": "bad",
                        "text": "音频只有 %.1fs / 画面 %.1fs" % (ad, vd)})

    if loud and loud.get("mean_db") is not None:
        mv = loud["mean_db"]
        if mv <= SILENT_MEAN_DB:
            out.append({"level": "bad", "text": "整段静音 %.0f dB" % mv})
        elif mv <= QUIET_MEAN_DB:
            out.append({"level": "warn", "text": "声音很轻 %.0f dB" % mv})

    md5 = info.get("md5")
    if md5:
        first = md5_owner.get(md5)
        if first and first != seg.get("label"):
            out.append({"level": "bad", "text": "与 %s 是同一个视频" % first})
    return out
