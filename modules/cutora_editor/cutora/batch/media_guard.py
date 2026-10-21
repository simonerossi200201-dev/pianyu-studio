# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""
media_guard.py — 拼接前强制体检 (fail-fast)
============================================
存在的理由 (2026-08-23 的真实事故):

  「视频输出」里的 `53 (1).mp4` 和 `91 (1).mp4` 根本不是视频, 是 Google
  生成式 AI 返回的 JPG 静态图, 只是被下载器按 .mp4 存了盘。
  文件头是 `FF D8 FF E0 JFIF`, 不是 mp4 的 `....ftyp`。

  旧流程对此**完全沉默**:
    · scan_groups() 只看扩展名 → 图片被当成合法素材收进组里
    · probe_media() 整个函数包在 try/except 里, 出错就返回 duration=0.0
    · 分析阶段拿到 0 秒 → 转录失败 → 该组被跳过
    · 最终 _stitched/ 里少了 53.mp4 和 91.mp4, 界面上不报任何错

  结果就是: 少了两条成品, 而且不看目录你根本不知道少了。

本模块的唯一职责: 在**动手拼接之前**把素材全部验一遍, 一旦发现致命问题
立刻中止并抛 PreflightError, 绝不"跳过继续"。宁可停下来让人看一眼,
也不要安静地少产出两条片子。

分三级:
  FATAL  文件根本不能用 (不是视频/截断/无视频流/静帧/时长为0/读不了)
         → 硬中止 + 隔离到 _invalid/, 无法绕过
  BLOCK  能读但结果几乎必然出错 (无音轨/编号断号/组内分辨率或帧率不一致)
         → 中止, 但调用方可以在明确告知用户后选择放行
  INFO   统计信息

用法:
    # 命令行 (可独立运行, 不依赖 PyQt / torch)
    python media_guard.py "D:\\...\\视频输出"
    python media_guard.py "D:\\...\\视频输出" --quarantine   # 同时隔离坏文件

    # 代码里
    from media_guard import preflight, PreflightError
    rep = preflight(folder, groups=groups, quarantine=True)
    if rep.fatal:
        raise PreflightError(rep.text())
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from cutora.core.proc import popen as _no_window_popen, run as _no_window_run

# ---------------------------------------------------------------- 配置

VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".mkv")
GROUP_RE = re.compile(r"^(\d+)\s*(?:([a-zA-Z]+)|\(\s*(\d+)\s*\))?$")

# 时长下限: 比这更短的片段不可能承载一句文案, 基本是坏文件或误放的封面
MIN_DURATION = 0.30

# ffprobe 允许的容器。注意 mp4/mov/m4v 会被识别为 "mov,mp4,m4a,3gp,3g2,mj2"
OK_FORMATS = {
    "mov", "mp4", "m4a", "3gp", "3g2", "mj2",
    "matroska", "webm",
}

# 这些"视频编码"其实是静态图: 出现即说明文件是图片而不是视频
STILL_CODECS = {
    "mjpeg", "png", "bmp", "gif", "webp", "tiff", "targa",
    "ppm", "pgm", "jpeg2000", "jpegls",
}

# 单个文件 ffprobe 超时 (秒)。笔记本高并发或杀毒扫描时给足缓冲。
PROBE_TIMEOUT = 45

# 文件头魔数 → 人类可读的真实类型
_MAGICS = [
    (b"\xff\xd8\xff",                 "JPEG 图片"),
    (b"\x89PNG\r\n\x1a\n",            "PNG 图片"),
    (b"GIF87a",                       "GIF 图片"),
    (b"GIF89a",                       "GIF 图片"),
    (b"BM",                           "BMP 图片"),
    (b"II*\x00",                      "TIFF 图片"),
    (b"MM\x00*",                      "TIFF 图片"),
    (b"PK\x03\x04",                   "ZIP 压缩包"),
    (b"Rar!",                         "RAR 压缩包"),
    (b"%PDF",                         "PDF 文档"),
    (b"\x1a\x45\xdf\xa3",             "Matroska/WebM 容器"),
    (b"RIFF",                         "RIFF 容器 (AVI/WAV/WebP)"),
    (b"ID3",                          "MP3 音频"),
    (b"OggS",                         "Ogg 音频"),
    (b"fLaC",                         "FLAC 音频"),
    (b"<!DOCTYPE",                    "HTML 网页 (多半是下载时拿到了错误页)"),
    (b"<html",                        "HTML 网页 (多半是下载时拿到了错误页)"),
    (b"{",                            "JSON/文本 (多半是 API 返回的错误信息)"),
]

FATAL, BLOCK, INFO = "FATAL", "BLOCK", "INFO"


class PreflightError(RuntimeError):
    """素材体检未通过。拼接流程必须在此中止, 不允许继续。"""


class Issue(object):
    __slots__ = ("level", "code", "target", "msg", "hint")

    def __init__(self, level, code, target, msg, hint=""):
        self.level = level
        self.code = code
        self.target = target      # 文件路径, 或组号, 或目录
        self.msg = msg
        self.hint = hint

    @property
    def name(self):
        return os.path.basename(self.target) if os.sep in str(self.target) \
            or "/" in str(self.target) else str(self.target)

    def as_dict(self):
        return {"level": self.level, "code": self.code,
                "target": self.target, "msg": self.msg, "hint": self.hint}


# ---------------------------------------------------------------- 工具

def _si():
    """Windows 下隐藏子进程黑框。"""
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return si
    return None


def ffprobe_works(exe):
    """真跑一次 -version。光看文件在不在是不够的:
    便携版目录里躺着的是 ffprobe.exe (Windows 二进制), 在别的系统上
    os.path.exists 为真、执行却报 Exec format error。这个坑第一次跑就踩到了,
    结果 1064 个文件报出 1064 条一模一样的假错误。"""
    try:
        r = _no_window_run([exe, "-version"], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, startupinfo=_si(), timeout=10)
        return r.returncode == 0
    except Exception:
        return False


import threading
_FFPROBE_CACHE = [None]
_FFPROBE_LOCK = threading.Lock()


def resolve_ffprobe():
    """找一个**真的能跑**的 ffprobe。刻意不 import config —— 那会连带拉起
    torch/CUDA 检测, 让本该几秒跑完的体检变成半分钟启动。"""
    if _FFPROBE_CACHE[0]:
        return _FFPROBE_CACHE[0]
    with _FFPROBE_LOCK:
        if _FFPROBE_CACHE[0]:
            return _FFPROBE_CACHE[0]
        from cutora.core.paths import FFPROBE, FFMPEG_DIR
        names = ("ffprobe.exe", "ffprobe") if os.name == "nt" else ("ffprobe", "ffprobe.exe")
        cands = [FFPROBE]
        for n in names:
            cands.append(os.path.join(FFMPEG_DIR, n))
        w = shutil.which("ffprobe")
        if w:
            cands.append(w)
        for c in cands:
            if os.path.exists(c) and ffprobe_works(c):
                _FFPROBE_CACHE[0] = c
                return c
        return "ffprobe"


def sniff_type(head):
    """按文件头猜真实类型。返回 (是否ISO容器, 人类可读类型名)。"""
    if len(head) >= 12 and head[4:8] in (b"ftyp", b"moov", b"wide", b"mdat", b"free", b"skip"):
        return True, "ISO-BMFF (mp4/mov)"
    for magic, label in _MAGICS:
        if head.startswith(magic):
            return False, label
    return False, "未知格式"


def scan_iso_boxes(path, limit_boxes=4096):
    """走一遍 ISO-BMFF 顶层 box 表。

    返回 (box类型集合, 错误信息或None)。
    下载中断的 mp4 最典型的症状就是 moov 缺失 —— 播放器打不开, ffprobe
    也解不出时长, 但文件头看着完全正常。这里必须查出来。
    """
    try:
        total = os.path.getsize(path)
        found = set()
        with open(path, "rb") as f:
            off = 0
            for _ in range(limit_boxes):
                if off + 8 > total:
                    break
                f.seek(off)
                hdr = f.read(8)
                if len(hdr) < 8:
                    break
                if hdr == b"\x00" * 8 and "moov" in found and "mdat" in found:
                    # 尾部零字节填充 (部分转码器/网盘网关会在文件末尾补齐块)
                    break
                size = int.from_bytes(hdr[0:4], "big")
                btype = hdr[4:8]
                hdr_len = 8
                if size == 1:                      # 64 位大 box
                    ext = f.read(8)
                    if len(ext) < 8:
                        if "moov" in found and "mdat" in found:
                            break
                        return found, "box 头被截断"
                    size = int.from_bytes(ext, "big")
                    hdr_len = 16
                elif size == 0:                    # 一直到文件尾
                    size = total - off
                if size < hdr_len:
                    if "moov" in found and "mdat" in found:
                        break
                    return found, "box 长度非法 (size=%d @ 偏移 %d)" % (size, off)
                try:
                    found.add(btype.decode("latin1"))
                except Exception:
                    return found, "box 类型非法 @ 偏移 %d" % off
                if off + size > total:
                    bname = btype.decode("latin1", "replace")
                    if bname not in ("moov", "mdat", "ftyp") and "moov" in found and "mdat" in found:
                        break
                    return found, ("box '%s' 声称长度 %d, 超出文件末尾 —— "
                                   "文件被截断 (下载没下完)"
                                   % (bname, size))
                off += size
        return found, None
    except Exception as e:
        return set(), "读取 box 表失败: %s" % e


def probe(path, ffprobe):
    """ffprobe 严格版: 出错就如实返回错误, **不**吞异常返回默认值。

    返回 (info_dict, 错误信息或None)。
    """
    cmd = [ffprobe, "-v", "error", "-of", "json",
           "-show_entries",
           "format=format_name,duration:"
           "stream=codec_type,codec_name,width,height,r_frame_rate,"
           "duration,pix_fmt",
           path]
    try:
        res = _no_window_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             startupinfo=_si(), timeout=PROBE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, "ffprobe 超时 %ds 无响应 (文件多半已损坏)" % PROBE_TIMEOUT
    except FileNotFoundError:
        return None, "找不到 ffprobe (%s)" % ffprobe
    except Exception as e:
        return None, "ffprobe 调用失败: %s" % e

    err = res.stderr.decode("utf-8", "ignore").strip()
    if res.returncode != 0:
        return None, "ffprobe 解析失败: %s" % (err.splitlines()[0] if err else
                                              "返回码 %d" % res.returncode)
    try:
        data = json.loads(res.stdout.decode("utf-8", "ignore") or "{}")
    except Exception as e:
        return None, "ffprobe 输出不是合法 JSON: %s" % e

    fmt = data.get("format") or {}
    info = {
        "format_name": (fmt.get("format_name") or "").strip(),
        "duration": 0.0, "width": 0, "height": 0, "fps": 0.0,
        "vcodec": "", "acodec": "", "has_audio": False, "has_video": False,
        "pix_fmt": "",
    }
    try:
        info["duration"] = float(fmt.get("duration") or 0.0)
    except Exception:
        info["duration"] = 0.0

    for st in data.get("streams") or []:
        kind = st.get("codec_type")
        if kind == "video" and not info["has_video"]:
            info["has_video"] = True
            info["vcodec"] = st.get("codec_name") or ""
            info["width"] = int(st.get("width") or 0)
            info["height"] = int(st.get("height") or 0)
            info["pix_fmt"] = str(st.get("pix_fmt") or "")
            fr = st.get("r_frame_rate") or "0/1"
            try:
                num, den = fr.split("/")
                info["fps"] = (float(num) / float(den)) if float(den) else 0.0
            except Exception:
                info["fps"] = 0.0
            if not info["duration"]:
                try:
                    info["duration"] = float(st.get("duration") or 0.0)
                except Exception:
                    pass
        elif kind == "audio":
            info["has_audio"] = True
            info["acodec"] = info["acodec"] or (st.get("codec_name") or "")
    return info, None


# ---------------------------------------------------------------- 单文件

def inspect_clip(path, ffprobe=None):
    """体检一个素材文件。返回 (issues列表, info或None)。"""
    ffprobe = ffprobe or resolve_ffprobe()
    issues = []
    name = os.path.basename(path)

    # --- 1. 存在 / 非空 / 可读 -------------------------------------
    if not os.path.exists(path):
        return [Issue(FATAL, "E_MISSING", path, "文件不存在")], None
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return [Issue(FATAL, "E_STAT", path, "无法读取文件属性: %s" % e)], None
    if size == 0:
        return [Issue(FATAL, "E_EMPTY", path, "文件是 0 字节 (下载失败留下的空壳)")], None

    try:
        with open(path, "rb") as f:
            head = f.read(32)
            f.seek(max(0, size - 4))
            f.read(4)                      # 真读一次尾部, 顺带验证可寻址
    except Exception as e:
        return [Issue(FATAL, "E_UNREADABLE", path,
                      "打不开或读不了: %s" % e,
                      "文件可能被其他程序占用, 或在断线的网络盘上")], None

    # --- 2. 文件头魔数: 扩展名骗不了人 ------------------------------
    is_iso, real_type = sniff_type(head)
    ext = os.path.splitext(name)[1].lower()
    if ext in (".mp4", ".mov", ".m4v"):
        if not is_iso:
            return [Issue(FATAL, "E_NOT_VIDEO", path,
                          "扩展名是 %s, 但文件头显示这其实是【%s】" % (ext, real_type),
                          "生成/下载环节返回了图片或错误页, 却按视频存了盘。"
                          "改回正确后缀就能直接打开看内容。")], None
    elif ext == ".mkv":
        if not head.startswith(b"\x1a\x45\xdf\xa3"):
            return [Issue(FATAL, "E_NOT_VIDEO", path,
                          "扩展名是 .mkv, 但文件头显示这其实是【%s】" % real_type)], None

    # --- 3. ISO 容器完整性: moov 在不在 -----------------------------
    if is_iso:
        boxes, box_err = scan_iso_boxes(path)
        if box_err:
            return [Issue(FATAL, "E_TRUNCATED", path,
                          "mp4 结构损坏: %s" % box_err,
                          "重新下载/重新生成这一条")], None
        if "moov" not in boxes:
            return [Issue(FATAL, "E_NO_MOOV", path,
                          "mp4 缺少 moov 索引 (顶层 box: %s)"
                          % (", ".join(sorted(boxes)) or "无"),
                          "典型的下载没下完。播放器也打不开, 必须重下。")], None

    # --- 4. ffprobe 严格解析 ---------------------------------------
    info, err = probe(path, ffprobe)
    if err:
        return [Issue(FATAL, "E_PROBE_FAIL", path, err)], None

    fmt_ok = any(x.strip() in OK_FORMATS
                 for x in (info["format_name"] or "").split(","))
    if not fmt_ok:
        return [Issue(FATAL, "E_BAD_FORMAT", path,
                      "ffprobe 把它识别成容器【%s】, 不是视频容器"
                      % (info["format_name"] or "未知"))], info

    if not info["has_video"]:
        return [Issue(FATAL, "E_NO_VIDEO_STREAM", path, "文件里没有视频流")], info

    if info["vcodec"] in STILL_CODECS:
        return [Issue(FATAL, "E_STILL_IMAGE", path,
                      "视频编码是 %s —— 这是静态图, 不是视频" % info["vcodec"])], None

    if info["duration"] < MIN_DURATION:
        return [Issue(FATAL, "E_ZERO_DURATION", path,
                      "时长只有 %.3f 秒 (下限 %.2f 秒)"
                      % (info["duration"], MIN_DURATION))], info

    if not info["width"] or not info["height"]:
        return [Issue(FATAL, "E_NO_SIZE", path, "读不出画面尺寸")], info

    if info["fps"] <= 0:
        issues.append(Issue(BLOCK, "W_NO_FPS", path, "读不出帧率, 编码时只能靠猜"))

    # --- 5. 音轨: 这个流程靠转录对齐文案, 没声音必然出错 --------------
    if not info["has_audio"]:
        issues.append(Issue(BLOCK, "W_NO_AUDIO", path,
                            "没有音轨 —— 转录不出任何词, 这段必然被判成"
                            "「与文案无关」而丢进回收站",
                            "确认是有意的静音素材再放行"))
    return issues, info


# ---------------------------------------------------------------- 报告

class Report(object):
    def __init__(self, folder):
        self.folder = folder
        self.issues = []
        self.n_clips = 0
        self.n_groups = 0
        self.quarantined = []
        self.elapsed = 0.0

    def add(self, issue):
        self.issues.append(issue)

    @property
    def fatal(self):
        return [i for i in self.issues if i.level == FATAL]

    @property
    def blocking(self):
        return [i for i in self.issues if i.level == BLOCK]

    @property
    def ok(self):
        return not self.fatal and not self.blocking

    @staticmethod
    def _render(issues, max_each, mark):
        """按错误码归并再打印。同一个码出现上百次时逐条列出没有信息量,
        只会把别的问题挤出屏幕 —— 这正是第一版跑出 1064 条相同报错时的教训。"""
        out = []
        by_code = {}
        for i in issues:
            by_code.setdefault(i.code, []).append(i)
        for code, lst in sorted(by_code.items(), key=lambda kv: -len(kv[1])):
            show = lst if len(lst) <= max_each else lst[:5]
            if len(lst) > max_each:
                out.append("  [%s] × %d 个文件, 列出前 %d 个:" % (code, len(lst), len(show)))
            for i in show:
                out.append("  %s %s" % (mark, os.path.basename(str(i.target))))
                out.append("      %s  [%s]" % (i.msg, i.code))
                if i.hint:
                    out.append("      → %s" % i.hint)
            if len(lst) > max_each:
                out.append("      ... 同类还有 %d 个, 见 preflight_report.json"
                           % (len(lst) - len(show)))
        return out

    def text(self, max_each=40):
        L = []
        L.append("=" * 62)
        L.append("素材体检 — %s" % self.folder)
        L.append("检查 %d 组 / %d 个片段, 耗时 %.1fs" %
                 (self.n_groups, self.n_clips, self.elapsed))
        L.append("=" * 62)
        f, b = self.fatal, self.blocking
        if not f and not b:
            L.append("全部通过, 可以拼接。")
            return "\n".join(L)

        if f:
            L.append("")
            L.append("【致命】%d 项 —— 拼接已中止, 这些文件不可能产出成品:" % len(f))
            L.extend(self._render(f, max_each, "✗"))
        if b:
            L.append("")
            L.append("【需确认】%d 项 —— 能跑, 但结果几乎肯定不对:" % len(b))
            L.extend(self._render(b, max_each, "!"))
        if self.quarantined:
            L.append("")
            L.append("已隔离 %d 个坏文件到 _invalid/ :" % len(self.quarantined))
            for src, dst in self.quarantined[:max_each]:
                L.append("  %s → %s" % (os.path.basename(src), os.path.basename(dst)))
        L.append("")
        L.append("修好上述问题后重新开始。绝不跳过继续 —— 跳过就等于"
                 "安静地少产出几条成品, 而你事后未必发现得了。")
        return "\n".join(L)

    def as_dict(self):
        return {
            "folder": self.folder,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "n_groups": self.n_groups,
            "n_clips": self.n_clips,
            "elapsed_sec": round(self.elapsed, 2),
            "fatal": [i.as_dict() for i in self.fatal],
            "blocking": [i.as_dict() for i in self.blocking],
            "quarantined": [{"from": a, "to": b} for a, b in self.quarantined],
        }


# ---------------------------------------------------------------- 主入口

def scan_groups(folder):
    """与 script_stitcher.scan_groups 同规则的独立实现 (便于本模块单独运行)。"""
    groups = {}
    for nm in os.listdir(folder):
        base, ext = os.path.splitext(nm)
        if ext.lower() not in VIDEO_EXTS:
            continue
        m = GROUP_RE.match(base.strip())
        if not m:
            continue
        num = m.group(1)
        paren = m.group(3)
        suffix = (1, "", int(paren)) if paren is not None \
            else (0, (m.group(2) or "").lower(), 0)
        groups.setdefault(num, []).append((suffix, os.path.join(folder, nm)))
    return {n: [p for _, p in sorted(v, key=lambda x: x[0])]
            for n, v in sorted(groups.items(), key=lambda kv: int(kv[0]))}


def quarantine(paths, folder):
    """把坏文件移进 <folder>/_invalid/ 。只移动, 绝不删除。

    返回 (成功移动列表, 移动失败列表)。失败的必须回报 —— 悄悄留在原地,
    下次跑又会被当成素材扫进来。"""
    moved, failed = [], []
    if not paths:
        return moved, failed
    inv = os.path.join(folder, "_invalid")
    os.makedirs(inv, exist_ok=True)
    for p in paths:
        if not os.path.exists(p):
            continue
        base = os.path.basename(p)
        dst = os.path.join(inv, base)
        stem, ext = os.path.splitext(base)
        n = 1
        while os.path.exists(dst):
            dst = os.path.join(inv, "%s__%d%s" % (stem, n, ext))
            n += 1
        try:
            shutil.move(p, dst)
            moved.append((p, dst))
        except Exception as e:
            failed.append((p, str(e)))
    return moved, failed


def preflight(folder, groups=None, ffprobe=None, quarantine_bad=False,
              check_gaps=True, log=None):
    """拼接前的强制体检。

    folder          素材文件夹
    groups          已扫好的 {组号: [路径,...]}; 不给则自己扫
    quarantine_bad  True → 把致命文件移进 _invalid/
    check_gaps      True → 检查组号断号 (24 那次就是这么漏掉的)
    log             可选回调, 用于把进度打到 GUI 日志框

    返回 Report。**不抛异常** —— 由调用方看 rep.fatal / rep.blocking 决定。
    """
    t0 = time.time()
    ffprobe = ffprobe or resolve_ffprobe()
    rep = Report(folder)
    say = log or (lambda s: None)

    if groups is None:
        groups = scan_groups(folder)
    rep.n_groups = len(groups)
    rep.n_clips = sum(len(v) for v in groups.values())

    # --- 先验工具本身。ffprobe 坏了就不是素材的问题, 一句话说清, --------
    # --- 不要让一千个文件各报一遍同样的错把真问题淹掉。 -----------------
    if not ffprobe_works(ffprobe):
        rep.add(Issue(FATAL, "E_NO_FFPROBE", ffprobe,
                      "ffprobe 不可用 (%s) —— 无法验证任何素材" % ffprobe,
                      "确认 runtime/ffmpeg/ffprobe.exe 存在且能运行, 或把 ffprobe 加进 PATH"))
        rep.elapsed = time.time() - t0
        return rep

    # --- 输出目录可写? 跑完两小时才发现写不进去最气人 ------------------
    for d in (folder, os.path.join(folder, "_stitched")):
        tag = os.path.join(d, ".cutora_write_test")
        try:
            os.makedirs(d, exist_ok=True)
            with open(tag, "w") as f:
                f.write("ok")
        except Exception as e:
            rep.add(Issue(FATAL, "E_NOT_WRITABLE", d,
                          "目录不可写: %s" % e,
                          "只读盘 / 权限不足 / 被杀毒软件锁住"))
        else:
            # 能写就够了。删不掉只说明这个盘禁止删除 (某些网络盘/受控目录),
            # 拼接照样能进行 —— 别把它误报成"不可写"。固定文件名, 最多留一个。
            try:
                os.remove(tag)
            except Exception:
                pass

    # --- 逐个文件 -----------------------------------------------------
    bad_paths = []
    geo = {}                         # 组号 → [(w,h,fps,path), ...]
    done = 0
    for gnum, paths in groups.items():
        for p in paths:
            issues, info = inspect_clip(p, ffprobe)
            for i in issues:
                rep.add(i)
                if i.level == FATAL:
                    bad_paths.append(p)
            if info and info.get("width"):
                geo.setdefault(gnum, []).append(
                    (info["width"], info["height"], round(info["fps"], 3), p))
            done += 1
            if done % 50 == 0:
                say("  体检中 %d/%d ..." % (done, rep.n_clips))

    # --- 组内一致性: 分辨率/帧率打架的话拼出来会抽帧或变形 ---------------
    for gnum, lst in geo.items():
        sizes = {(w, h) for w, h, _, _ in lst}
        fpss = {f for _, _, f, _ in lst}
        if len(sizes) > 1:
            rep.add(Issue(BLOCK, "W_MIXED_RES", gnum,
                          "组 %s 内分辨率不一致: %s" %
                          (gnum, " / ".join("%dx%d" % s for s in sorted(sizes))),
                          "成品会以第一段为准强行缩放, 其余段可能变形或裁切"))
        if len(fpss) > 1:
            rep.add(Issue(BLOCK, "W_MIXED_FPS", gnum,
                          "组 %s 内帧率不一致: %s" %
                          (gnum, " / ".join("%.3g" % f for f in sorted(fpss)))))

    # --- 组号断号: 24 就是这么无声无息消失的 ---------------------------
    if check_gaps and groups:
        nums = sorted(int(k) for k in groups.keys())
        missing = [n for n in range(nums[0], nums[-1] + 1) if n not in set(nums)]
        if missing:
            head = ", ".join(str(n) for n in missing[:30])
            rep.add(Issue(BLOCK, "W_GAP", folder,
                          "编号在 %d~%d 之间断了 %d 个: %s%s"
                          % (nums[0], nums[-1], len(missing), head,
                             " ..." if len(missing) > 30 else ""),
                          "确认这些编号本来就不该有素材, 再放行"))

    # --- 隔离 ---------------------------------------------------------
    if quarantine_bad and bad_paths:
        rep.quarantined, failed = quarantine(sorted(set(bad_paths)), folder)
        for p, why in failed:
            rep.add(Issue(FATAL, "E_QUARANTINE_FAIL", p,
                          "坏文件移不进 _invalid/: %s" % why,
                          "请手动把它移出素材文件夹, 否则下次扫描又会把它算进来"))

    rep.elapsed = time.time() - t0

    # --- 落盘存档 ------------------------------------------------------
    if not rep.ok:
        try:
            outd = os.path.join(folder, "_invalid")
            os.makedirs(outd, exist_ok=True)
            with open(os.path.join(outd, "preflight_report.txt"),
                      "w", encoding="utf-8") as f:
                f.write(rep.text(max_each=10000))
            with open(os.path.join(outd, "preflight_report.json"),
                      "w", encoding="utf-8") as f:
                json.dump(rep.as_dict(), f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    return rep


def assert_ok(folder, groups=None, allow_blocking=False, **kw):
    """严格版: 不通过直接抛 PreflightError。给不想自己判分支的调用方用。"""
    rep = preflight(folder, groups=groups, **kw)
    if rep.fatal or (rep.blocking and not allow_blocking):
        raise PreflightError(rep.text())
    return rep


def preflight_for_auto_subtitle(path, ffprobe=None):
    """GUI 自动字幕前的单文件体检。

    无音轨、编码异常、时长为 0（以及其它 FATAL）立刻抛 PreflightError，
    不要让用户等识别跑完才失败。读不出帧率不算阻断（ASR 不需要 fps）。
    """
    issues, info = inspect_clip(path, ffprobe=ffprobe)
    bad = [i for i in issues if i.level == FATAL or i.code == "W_NO_AUDIO"]
    if not bad:
        return info
    lines = []
    for item in bad:
        lines.append("%s：%s" % (item.name, item.msg))
        if item.hint:
            lines.append("  %s" % item.hint)
    raise PreflightError("\n".join(lines))


# ---------------------------------------------------------------- CLI

def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    flags = {a for a in argv[1:] if a.startswith("--")}
    if args:
        folder = args[0].strip().strip('"').strip("'")
    else:
        folder = input("素材文件夹路径: ").strip().strip('"').strip("'")
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        print("不是一个文件夹: %s" % folder)
        return 2

    rep = preflight(folder,
                    quarantine_bad=("--quarantine" in flags),
                    check_gaps=("--no-gap-check" not in flags),
                    log=print)
    print()
    print(rep.text())
    if rep.fatal:
        return 1
    if rep.blocking:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
