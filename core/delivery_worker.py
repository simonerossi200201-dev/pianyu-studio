"""成片自动命名与 Google Drive 持续上传守护器 (DeliveryWorker)。

打通「CUTORA 导出成片 -> 右键表格工具命名与 rclone 上传 -> 写回任务表分享链接」最后一公里闭环。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from . import BIN_DIR
from .spine_db import SpineDB

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
RCLONE_REMOTE = "gdrive"


def extract_drive_folder_id(url: str) -> str | None:
    if not url:
        return None
    m = re.search(r"(?:id=|/folders/)([A-Za-z0-9_-]{10,})", str(url).strip())
    return m.group(1) if m else None


def verify_remote_upload(rclone_exe: Path, folder_id: str, filename: str, local_size: int) -> tuple[bool, str]:
    """与右键表格工具 `verify_upload` 完全一致的远端字节级严格校验。"""
    try:
        r = subprocess.run(
            [
                str(rclone_exe),
                "lsjson",
                f"{RCLONE_REMOTE}:",
                "--drive-root-folder-id",
                folder_id,
                "--files-only",
                "--no-modtime",
            ],
            capture_output=True,
            timeout=90,
            creationflags=CREATE_NO_WINDOW,
        )
        if r.returncode != 0:
            return False, f"rclone lsjson 返回码 {r.returncode}"
        items = json.loads((r.stdout or b"").decode("utf-8", "replace") or "[]")
        same = [it for it in items if it.get("Name") == filename]
        if len(same) != 1:
            return False, f"远端同名文件数量异常 ({len(same)})"
        rsize = int(same[0].get("Size") or -1)
        if rsize != local_size:
            return False, f"字节大小不一致 (本地 {local_size} != 远端 {rsize})"
        fid = same[0].get("ID") or same[0].get("Id") or ""
        return True, f"https://drive.google.com/file/d/{fid}/view?usp=sharing"
    except Exception as exc:
        return False, str(exc)


def process_pending_deliveries() -> list[dict[str, Any]]:
    """扫描 `studio_spine.db` 的 `delivery_queue` 并执行持续后台上传。"""
    spine = SpineDB()
    rclone_exe = BIN_DIR / "rclone.exe"
    processed: list[dict[str, Any]] = []

    with spine._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM delivery_queue WHERE status = 'queued' ORDER BY id ASC LIMIT 20"
        ).fetchall()

    for row in rows:
        did = int(row["id"])
        video_path = Path(row["renamed_video"] or row["source_video"])
        folder_url = str(row["drive_folder_url"] or "").strip()

        if not folder_url:
            with spine._lock, spine._connect() as conn:
                conn.execute(
                    "UPDATE delivery_queue SET status='renamed_local', updated_at=? WHERE id=?",
                    (time.time(), did),
                )
            processed.append({"id": did, "status": "renamed_local", "file": str(video_path)})
            continue

        folder_id = extract_drive_folder_id(folder_url)
        if not folder_id or not video_path.is_file() or not rclone_exe.is_file():
            with spine._lock, spine._connect() as conn:
                conn.execute(
                    "UPDATE delivery_queue SET status='failed', error=?, updated_at=? WHERE id=?",
                    ("缺少有效的 Google Drive 文件夹 ID 或本地文件", time.time(), did),
                )
            continue

        try:
            proc = subprocess.run(
                [
                    str(rclone_exe),
                    "copy",
                    str(video_path),
                    f"{RCLONE_REMOTE}:",
                    "--drive-root-folder-id",
                    folder_id,
                    "--local-encoding",
                    "None",
                    "--no-traverse",
                    "--drive-chunk-size",
                    "64M",
                ],
                capture_output=True,
                timeout=1800,
                creationflags=CREATE_NO_WINDOW,
            )
            if proc.returncode == 0:
                ok, link_or_err = verify_remote_upload(
                    rclone_exe, folder_id, video_path.name, video_path.stat().st_size
                )
                if ok:
                    with spine._lock, spine._connect() as conn:
                        conn.execute(
                            "UPDATE delivery_queue SET status='uploaded', drive_file_link=?, updated_at=? WHERE id=?",
                            (link_or_err, time.time(), did),
                        )
                    spine.emit(
                        topic="delivery.uploaded",
                        source="table_tool",
                        task_id=str(row["task_id"]),
                        payload={"delivery_id": did, "file": video_path.name, "drive_link": link_or_err},
                    )
                    processed.append({"id": did, "status": "uploaded", "drive_link": link_or_err})
                else:
                    with spine._lock, spine._connect() as conn:
                        conn.execute(
                            "UPDATE delivery_queue SET status='failed', error=?, updated_at=? WHERE id=?",
                            (link_or_err, time.time(), did),
                        )
        except Exception as exc:
            with spine._lock, spine._connect() as conn:
                conn.execute(
                    "UPDATE delivery_queue SET status='failed', error=?, updated_at=? WHERE id=?",
                    (str(exc)[:300], time.time(), did),
                )

    return processed


class DeliveryWorker:
    def sync_pending_uploads_to_drive(self) -> dict[str, Any]:
        items = process_delivery_queue()
        return {"uploaded": len(items), "items": items}


_DELIVERY_SINGLETON = DeliveryWorker()


def get_delivery_worker() -> DeliveryWorker:
    return _DELIVERY_SINGLETON

