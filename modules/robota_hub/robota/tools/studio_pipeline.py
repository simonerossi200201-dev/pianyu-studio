"""End-to-end Studio Pipeline tools for Robota (Orante + CUTORA + TableTool + Google Drive).

Provides full agent autonomy matching the Antigravity studio workflow:
1. parse_studio_table       — Parse TSV/multiline request table (image folder link, vNN, title, video link, caption)
2. studio_download_batch    — Download & magic-byte verify images (vNN(1)(2).jpg) and main videos (vNN.mp4)
3. cutora_auto_project      — Build face-safe .cutora split-screen project(s) with ASR title alignment, phone sticker, AI credit, and contact sheets
4. cutora_batch_export      — Run high-speed (250–350 fps target) parallel batch export of .cutora project(s)
5. drive_upload_and_verify  — Upload exported finals to Google Drive folder via rclone and run one-way MD5 check
6. studio_full_pipeline     — Execute or resume the complete end-to-end studio workflow
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

STUDIO_ROOT = Path(r"D:\0_LTX")
STUDIO_PYTHON = STUDIO_ROOT / "runtime" / "venv" / "Scripts" / "python.exe"
CUTORA_ROOT = STUDIO_ROOT / "modules" / "cutora_editor"
RCLONE_EXE = STUDIO_ROOT / "runtime" / "bin" / "rclone.exe"
PROJECT_ROOT = Path(r"D:\1_PROJECT")
TIKTOK_PIC_DIR = PROJECT_ROOT / "Tiktok_Pic"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _extract_drive_id(url: str) -> tuple[str, str]:
    """Return (kind, drive_id) where kind is 'folder' or 'file'."""
    u = (url or "").strip()
    m_f = re.search(r"/folders/([a-zA-Z0-9_-]{15,})", u)
    if m_f:
        return "folder", m_f.group(1)
    m_d = re.search(r"/file/d/([a-zA-Z0-9_-]{15,})", u)
    if m_d:
        return "file", m_d.group(1)
    m_id = re.search(r"[?&]id=([a-zA-Z0-9_-]{15,})", u)
    if m_id:
        return "file", m_id.group(1)
    return "unknown", ""


def _resolve_phone_sticker(caption_text: str = "", explicit_path: str = "") -> str:
    if explicit_path and os.path.isfile(explicit_path):
        return str(Path(explicit_path).resolve())
    if not TIKTOK_PIC_DIR.is_dir():
        return ""
    m_phone = re.search(r"\+?\d{10,15}", caption_text or "")
    phone_digits = m_phone.group(0).lstrip("+") if m_phone else ""
    pngs = sorted(TIKTOK_PIC_DIR.glob("*.png"))
    if phone_digits:
        for p in pngs:
            if phone_digits in p.stem:
                return str(p.resolve())
    return str(pngs[0].resolve()) if pngs else ""


def parse_studio_table(table_text: str) -> dict[str, Any]:
    """Parse a studio TSV/multiline batch table into structured rows.

    Each row has: code (e.g. 'v18'), image_folder_url, image_folder_id,
    title, video_url, video_file_id, caption, phone_sticker.
    """
    raw = (table_text or "").strip()
    if not raw:
        return {"ok": False, "error": "empty table_text", "rows": []}
    reader = csv.reader(io.StringIO(raw), delimiter="\t")
    rows: list[dict[str, Any]] = []
    for raw_row in reader:
        cols = [c.strip() for c in raw_row if c is not None]
        if not any(cols):
            continue
        code_idx = -1
        for idx, val in enumerate(cols):
            if re.match(r"^[vV]\d{1,4}$", val.strip()):
                code_idx = idx
                break
        if code_idx < 0:
            continue
        code = cols[code_idx].strip().lower()
        img_url = cols[code_idx - 1].strip() if code_idx >= 1 else ""
        title = cols[code_idx + 1].strip() if code_idx + 1 < len(cols) else ""
        vid_url = cols[code_idx + 2].strip() if code_idx + 2 < len(cols) else ""
        caption = cols[code_idx + 3].strip() if code_idx + 3 < len(cols) else ""
        _, img_id = _extract_drive_id(img_url)
        _, vid_id = _extract_drive_id(vid_url)
        rows.append({
            "code": code,
            "image_folder_url": img_url,
            "image_folder_id": img_id,
            "title": title,
            "video_url": vid_url,
            "video_file_id": vid_id,
            "caption": caption,
            "phone_sticker": _resolve_phone_sticker(caption),
        })
    return {
        "ok": len(rows) > 0,
        "count": len(rows),
        "first_code": rows[0]["code"] if rows else "",
        "last_code": rows[-1]["code"] if rows else "",
        "phone_sticker": rows[0]["phone_sticker"] if rows else _resolve_phone_sticker(""),
        "rows": rows,
    }


def cutora_auto_project(
    batch_dir: str,
    videos_dir: str = "",
    clips_manifest: str = "",
    table_path_or_text: str = "",
    phone_img: str = "",
    parts: int = 2,
    project_name: str = "",
) -> dict[str, Any]:
    """Build face-safe CUTORA split-screen project(s) (.cutora) with ASR title alignment,
    phone sticker, AI credit, and face-safe verification contact sheets.
    """
    bdir = Path(batch_dir).resolve()
    if not bdir.is_dir():
        return {"ok": False, "error": f"batch_dir does not exist: {bdir}"}

    if not videos_dir:
        cand_v = sorted([d for d in bdir.iterdir() if d.is_dir() and "视频" in d.name])
        videos_dir = str(cand_v[0]) if cand_v else ""
    if not clips_manifest:
        cand_m = sorted(bdir.glob("T*/manifest.json"))
        clips_manifest = str(cand_m[-1]) if cand_m else ""

    tmp_table_file = None
    table_arg = table_path_or_text
    if table_path_or_text and not os.path.isfile(table_path_or_text):
        fd, tmp_table_file = tempfile.mkstemp(prefix="robota_table_", suffix=".txt")
        os.close(fd)
        Path(tmp_table_file).write_text(table_path_or_text, encoding="utf-8")
        table_arg = tmp_table_file

    if not phone_img:
        phone_img = _resolve_phone_sticker(table_path_or_text if not os.path.isfile(table_path_or_text) else "")

    cmd = [
        str(STUDIO_PYTHON),
        "-m",
        "cutora.batch.auto_project",
        "--batch-dir",
        str(bdir),
        "--parts",
        str(max(1, int(parts))),
    ]
    if videos_dir:
        cmd += ["--videos", str(videos_dir)]
    if clips_manifest:
        cmd += ["--clips-manifest", str(clips_manifest)]
    if table_arg and os.path.isfile(table_arg):
        cmd += ["--table", str(table_arg)]
    if phone_img and os.path.isfile(phone_img):
        cmd += ["--phone-img", str(phone_img)]
    if project_name:
        cmd += ["--project-name", str(project_name)]

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(CUTORA_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
            stdin=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        )
    finally:
        if tmp_table_file and os.path.isfile(tmp_table_file):
            try:
                os.remove(tmp_table_file)
            except OSError:
                pass

    lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    for ln in reversed(lines):
        if ln.startswith("{") and ln.endswith("}"):
            try:
                res = json.loads(ln)
                res.setdefault("ok", proc.returncode == 0)
                return res
            except Exception:
                pass
    return {
        "ok": proc.returncode == 0,
        "exit": proc.returncode,
        "stdout_tail": "\n".join(lines[-20:]),
        "stderr_tail": "\n".join((proc.stderr or "").splitlines()[-20:]),
    }


def cutora_batch_export(
    project_path: str,
    out_dir: str = "",
    workers: int = 10,
    gpu_workers: int = 10,
    skip_existing: bool = True,
) -> dict[str, Any]:
    """Batch export a .cutora project (or comma-separated list of .cutora projects) at high speed (250–350 fps target)."""
    proj_list = [p.strip() for p in (project_path or "").split(";") if p.strip()]
    if not proj_list:
        return {"ok": False, "error": "project_path is empty"}

    results = []
    all_ok = True
    for p_str in proj_list:
        p = Path(p_str).resolve()
        if not p.is_file():
            return {"ok": False, "error": f"project file not found: {p}"}
        dest = Path(out_dir).resolve() if out_dir else (p.parent / "成品")
        dest.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(STUDIO_PYTHON),
            "-m",
            "cutora.batch.export_cli",
            "--project",
            str(p),
            "--out",
            str(dest),
            "--workers",
            str(max(1, int(workers))),
            "--gpu-workers",
            str(max(0, int(gpu_workers))),
        ]
        if skip_existing:
            cmd.append("--skip-existing")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(
            cmd,
            cwd=str(CUTORA_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=7200,
            stdin=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        )
        lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
        parsed = None
        for ln in reversed(lines):
            if ln.startswith("{") and ln.endswith("}"):
                try:
                    parsed = json.loads(ln)
                    break
                except Exception:
                    pass
        if not parsed:
            parsed = {
                "ok": proc.returncode == 0,
                "project": str(p),
                "out_dir": str(dest),
                "exit": proc.returncode,
                "stdout_tail": "\n".join(lines[-15:]),
            }
        if not parsed.get("ok"):
            all_ok = False
        results.append(parsed)

    return {
        "ok": all_ok,
        "projects_count": len(results),
        "results": results,
    }


def drive_upload_and_verify(
    local_dir: str,
    drive_folder_url_or_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Upload all finished .mp4 files in local_dir to a Google Drive folder by URL/ID and run one-way MD5 check."""
    ldir = Path(local_dir).resolve()
    if not ldir.is_dir():
        return {"ok": False, "error": f"local_dir not found: {ldir}"}
    _, fid = _extract_drive_id(drive_folder_url_or_id)
    if not fid and re.match(r"^[a-zA-Z0-9_-]{15,}$", (drive_folder_url_or_id or "").strip()):
        fid = drive_folder_url_or_id.strip()
    if not fid:
        return {"ok": False, "error": f"Could not extract Google Drive folder ID from: {drive_folder_url_or_id}"}

    exe = str(RCLONE_EXE) if RCLONE_EXE.is_file() else "rclone"
    copy_args = [
        exe,
        "copy",
        str(ldir),
        "gdrive:",
        "--drive-root-folder-id",
        fid,
        "--transfers",
        "8",
        "--checkers",
        "8",
        "--stats-one-line",
        "-v",
    ]
    if dry_run:
        copy_args.append("--dry-run")

    r_copy = subprocess.run(
        copy_args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=3600,
        stdin=subprocess.DEVNULL,
        creationflags=_NO_WINDOW,
    )
    if r_copy.returncode != 0:
        return {
            "ok": False,
            "step": "copy",
            "exit": r_copy.returncode,
            "stderr_tail": "\n".join((r_copy.stderr or "").splitlines()[-20:]),
        }

    if dry_run:
        return {"ok": True, "dry_run": True, "drive_folder_id": fid, "local_dir": str(ldir)}

    check_args = [
        exe,
        "check",
        str(ldir),
        "gdrive:",
        "--drive-root-folder-id",
        fid,
        "--one-way",
    ]
    r_chk = subprocess.run(
        check_args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        stdin=subprocess.DEVNULL,
        creationflags=_NO_WINDOW,
    )
    chk_tail = "\n".join((r_chk.stderr or r_chk.stdout or "").splitlines()[-15:])
    return {
        "ok": r_chk.returncode == 0,
        "drive_folder_id": fid,
        "local_dir": str(ldir),
        "verified_zero_diff": r_chk.returncode == 0,
        "check_summary": chk_tail,
    }
