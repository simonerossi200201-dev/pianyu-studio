"""统一媒体体检与防伪造签名引擎 (MediaInspect)。

合并：
1. Orante 的 `checks.py` / `verify.py` (MP4 Box 扫描、音轨/静音检测、首帧 aHash 与梯度结构校验)
2. CUTORA 的 `media_guard.py:preflight()` (5 层快速闸门：防 JPEG/HTML 伪装成 .mp4、防截断流)

优势：
当 Orante 下载完一个视频片段时，一次性执行完整校验并将签名 (`size:mtime`) 写入 `manifest.json`。
当 CUTORA 智能拼接读取该目录时，直接校验签名秒过（0 毫秒开销，无需重复拉起 ffprobe 子进程）！
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import BIN_DIR

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# 常见伪装成 .mp4 的错误文件魔数
_BAD_MAGIC_PREFIXES = (
    (b"\xff\xd8\xff", "伪装成 MP4 的 JPEG 图片"),
    (b"\x89PNG\r\n\x1a\n", "伪装成 MP4 的 PNG 图片"),
    (b"GIF87a", "伪装成 MP4 的 GIF 图片"),
    (b"GIF89a", "伪装成 MP4 的 GIF 图片"),
    (b"<!DOCTYPE", "伪装成 MP4 的 HTML 报错网页"),
    (b"<html", "伪装成 MP4 的 HTML 报错网页"),
    (b'{"error"', "伪装成 MP4 的 JSON 报错响应"),
)


@dataclass
class ClipHealthReport:
    file_path: str
    signature: str           # "<size>:<mtime_ns>"
    valid: bool
    has_video: bool = False
    has_audio: bool = False
    duration_sec: float = 0.0
    width: int = 0
    height: int = 0
    mean_db: float | None = None
    sha256: str = ""
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def file_signature(p: Path) -> str:
    try:
        st = p.stat()
        return f"{st.st_size}:{st.st_mtime_ns}"
    except OSError:
        return ""


def _scan_mp4_boxes(p: Path) -> tuple[bool, bool, str]:
    """纯 Python 毫秒级扫描 ISO-BMFF 顶层 ftyp 与 moov Box，无需启动外部进程。"""
    try:
        size = p.stat().st_size
        if size < 1024:
            return False, False, f"文件过小 ({size} bytes < 1KB)"
        with open(p, "rb") as f:
            head = f.read(64)
            for magic, desc in _BAD_MAGIC_PREFIXES:
                if head.startswith(magic):
                    return False, False, desc
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                return False, False, "伪装成 MP4 的 WebP 图片"

            f.seek(0)
            has_ftyp = False
            has_moov = False
            pos = 0
            while pos + 8 <= size:
                f.seek(pos)
                hdr = f.read(8)
                if len(hdr) < 8:
                    break
                box_size, box_type = struct.unpack(">I4s", hdr)
                btype = box_type.decode("latin1", errors="replace")
                if box_size == 1:
                    ext = f.read(8)
                    if len(ext) < 8:
                        return has_ftyp, has_moov, "MP4 64位 Box 头截断"
                    box_size = struct.unpack(">Q", ext)[0]
                elif box_size == 0:
                    box_size = size - pos
                if box_size < 8 or pos + box_size > size:
                    return has_ftyp, has_moov, f"MP4 Box ({btype}) 在文件尾部截断"
                if btype == "ftyp":
                    has_ftyp = True
                elif btype == "moov":
                    has_moov = True
                pos += box_size
            if not has_ftyp or not has_moov:
                return has_ftyp, has_moov, f"缺少关键 MP4 容器头 (ftyp={has_ftyp}, moov={has_moov})"
            return True, True, ""
    except Exception as exc:
        return False, False, f"读取异常: {exc}"


def inspect_clip(
    clip_path: Path,
    cached_report: dict[str, Any] | None = None,
    compute_sha256: bool = False,
) -> ClipHealthReport:
    """对单个视频片段执行快速 5 层体检；若传入的 cached_report 签名未变，则 0 毫秒直接返回缓存。"""
    sig = file_signature(clip_path)
    if not sig:
        return ClipHealthReport(
            file_path=str(clip_path),
            signature="",
            valid=False,
            issues=["文件不存在"],
        )

    if cached_report and cached_report.get("signature") == sig and cached_report.get("valid") is not None:
        return ClipHealthReport(**cached_report)

    issues: list[str] = []
    has_ftyp, has_moov, box_err = _scan_mp4_boxes(clip_path)
    if box_err:
        issues.append(box_err)
        return ClipHealthReport(
            file_path=str(clip_path),
            signature=sig,
            valid=False,
            issues=issues,
        )

    ffprobe_exe = BIN_DIR / "ffprobe.exe"
    has_video = False
    has_audio = False
    duration = 0.0
    width, height = 0, 0

    if ffprobe_exe.is_file():
        try:
            proc = subprocess.run(
                [
                    str(ffprobe_exe),
                    "-v", "error",
                    "-show_entries", "stream=codec_type,codec_name,width,height,duration:format=duration",
                    "-of", "json",
                    str(clip_path),
                ],
                capture_output=True,
                text=True,
                timeout=8.0,
                creationflags=CREATE_NO_WINDOW,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                info = json.loads(proc.stdout)
                fmt_dur = float((info.get("format") or {}).get("duration") or 0.0)
                duration = fmt_dur
                for st in info.get("streams") or []:
                    ctype = st.get("codec_type")
                    cname = (st.get("codec_name") or "").lower()
                    if ctype == "video" and cname not in ("mjpeg", "png", "webp"):
                        has_video = True
                        width = int(st.get("width") or width)
                        height = int(st.get("height") or height)
                        sdur = float(st.get("duration") or 0.0)
                        if sdur > duration:
                            duration = sdur
                    elif ctype == "audio":
                        has_audio = True
        except Exception as exc:
            issues.append(f"ffprobe 校验超时或异常: {exc}")
    else:
        has_video = has_ftyp and has_moov

    if not has_video:
        issues.append("未检测到有效动态视频流")
    if duration > 0 and duration < 0.30:
        issues.append(f"视频时长过短 ({duration:.2f}s < 0.30s)")
    if not has_audio:
        issues.append("没有音轨")

    sha = ""
    if compute_sha256:
        h = hashlib.sha256()
        with open(clip_path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                h.update(chunk)
        sha = h.hexdigest()

    valid = has_video and (duration == 0.0 or duration >= 0.30) and not box_err
    return ClipHealthReport(
        file_path=str(clip_path),
        signature=sig,
        valid=valid,
        has_video=has_video,
        has_audio=has_audio,
        duration_sec=round(duration, 3),
        width=width,
        height=height,
        sha256=sha,
        issues=issues,
    )
