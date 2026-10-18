"""跨窗口实时信号总线与共享数据库底座 (SpineDB & EventBus)。

这是「片语工作室 (D:\\0_LTX)」将四大软件（表格工具、Orante、CUTORA、Robota）与本地 AI 算力网关
紧密咬合成一个整体的核心神经中枢：

1. 信号一 (`table.sync_to_cutora`)：Orante 任务表与文案直接同步给 CUTORA 智能拼接，零粘贴。
2. 信号二 (`stitch.gap_detected`)：CUTORA 智能拼接发现缺片时，立即回写 `<task_dir>/clips/_stitched/缺失文案清单.txt`
   并通过总线通知 Orante (`gapfill.py`) 标红对应分镜，同时唤醒 Robota L0 零 Token 状态机自动安排补生。
3. 信号三 (`engine.image_blocked`)：Flow / Grok 垫图被卡审核时，自动转发至 Studio Gateway (`127.0.0.1:7870`)
   使用本地 **LTX-2.5 22B** 补生（不包含 MiniMax H3）。
4. 信号四 (`render.completed`)：CUTORA 导出成片完成后，自动将 Orante 任务标为 `finished`，
   触发「表格工具」双阶段原子重命名，并调用 `rclone` 持续后台上传至 Google Drive 写回分享链接。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from . import DATA_DIR, load_studio_config
from .table_sync import discover_orante_batch_tasks, write_order_lock_for_cutora

SPINE_DB_PATH = DATA_DIR / "studio_spine.db"


class SpineDB:
    """基于 SQLite WAL 的跨进程/跨窗口实时信号与状态共享库。"""

    def __init__(self, db_path: Path = SPINE_DB_PATH) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS spine_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT NOT NULL,
                    source TEXT NOT NULL,
                    target TEXT NOT NULL DEFAULT '*',
                    task_id TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_spine_events_id ON spine_events(id);
                CREATE INDEX IF NOT EXISTS idx_spine_events_topic ON spine_events(topic, status);

                CREATE TABLE IF NOT EXISTS gap_reports (
                    task_id TEXT PRIMARY KEY,
                    task_dir TEXT NOT NULL,
                    report_file TEXT NOT NULL,
                    missing_segments TEXT NOT NULL DEFAULT '[]',
                    missing_text_items TEXT NOT NULL DEFAULT '[]',
                    auto_regen_triggered INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'open',
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS delivery_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    source_video TEXT NOT NULL,
                    renamed_video TEXT NOT NULL,
                    drive_folder_url TEXT NOT NULL DEFAULT '',
                    drive_file_link TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'queued',
                    error TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS robota_recipes (
                    recipe_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    steps_json TEXT NOT NULL DEFAULT '[]',
                    zero_token_l0 INTEGER NOT NULL DEFAULT 1,
                    run_count INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL
                );
                DELETE FROM gap_reports WHERE task_id = 'T9999';
                DELETE FROM delivery_queue WHERE task_id = 'T9999';
                DELETE FROM spine_events WHERE task_id = 'T9999';
                """
            )

    def emit(
        self,
        topic: str,
        source: str,
        payload: dict[str, Any],
        target: str = "*",
        task_id: str = "",
    ) -> int:
        """向跨窗口总线写入一条持久化实时信号。"""
        now = time.time()
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO spine_events (topic, source, target, task_id, payload, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?)
                """,
                (topic, source, target, task_id, json.dumps(payload, ensure_ascii=False), now),
            )
            return int(cur.lastrowid or 0)

    def emit_event(self, topic: str, source: str, target: str, payload: dict[str, Any], task_id: str = "") -> int:
        return self.emit(topic=topic, source=source, payload=payload, target=target, task_id=task_id)

    def _scan_active_workspace_metrics(self) -> dict[str, Any]:
        """实时扫描 D:\\1_PROJECT 下当前活跃日期与批次的真实素材、片段、工程、导出及 .renamer_project.json 上传闭环状态。"""
        try:
            from .robota_guide import DailyWorkflowConductor
            conductor = DailyWorkflowConductor()
            stats = conductor.get_live_workspace_stats()
            lc = stats.get("lifecycle") or {}
            prev_sum = stats.get("prev_completed_summary") or {}
            return {
                "active_day": stats.get("day_str", ""),
                "active_batch": stats.get("active_batch", "A"),
                "stage_label": stats.get("stage_label", "待启动"),
                "main_videos_count": int(stats.get("vid_count", 0)),
                "clips_count": int(lc.get("clips_count", 0)),
                "cutora_tasks_count": int(lc.get("cutora_tasks_count", 0)),
                "exported_count": int(lc.get("exported_mp4_count", 0)),
                "uploaded_ok_count": int(lc.get("uploaded_ok_count", 0)),
                "prev_completed_day": str(stats.get("prev_completed_day") or ""),
                "prev_uploaded_ok": int(prev_sum.get("uploaded_ok_count", 0)),
            }
        except Exception:
            import datetime
            ws_root = Path(load_studio_config().get("workspace_root", r"D:\1_PROJECT"))
            today_str = datetime.date.today().strftime("%Y-%m-%d")
            return {
                "active_day": today_str,
                "active_batch": "A",
                "stage_label": "待启动",
                "main_videos_count": 0,
                "clips_count": 0,
                "cutora_tasks_count": 0,
                "exported_count": 0,
                "uploaded_ok_count": 0,
                "prev_completed_day": "",
                "prev_uploaded_ok": 0,
            }

    def get_dashboard_summary(self) -> dict[str, Any]:
        now = time.time()
        day_ago = now - 86400.0
        with self._connect() as conn:
            db_tasks = int(conn.execute("SELECT COUNT(DISTINCT task_id) FROM spine_events WHERE task_id != '' AND created_at > ?", (day_ago,)).fetchone()[0])
            gaps_pending = int(conn.execute("SELECT COUNT(*) FROM gap_reports WHERE status != 'resolved'").fetchone()[0])
            uploads_pending = int(conn.execute("SELECT COUNT(*) FROM delivery_queue WHERE status = 'queued'").fetchone()[0])
            events_24h = int(conn.execute("SELECT COUNT(*) FROM spine_events WHERE created_at > ?", (day_ago,)).fetchone()[0])

        ws = self._scan_active_workspace_metrics()
        tasks_live = ws["cutora_tasks_count"] or ws["main_videos_count"] or db_tasks
        clips_live = ws["clips_count"]
        exports_live = ws["uploaded_ok_count"] or ws["exported_count"] or ws["prev_uploaded_ok"] or uploads_pending

        return {
            "tasks_total": tasks_live,
            "clips_count": clips_live,
            "gaps_pending": gaps_pending,
            "uploads_pending": exports_live,
            "unacked_events": events_24h,
            "active_day": ws["active_day"],
            "active_batch": ws["active_batch"],
            "stage_label": ws["stage_label"],
            "main_videos_count": ws["main_videos_count"],
            "cutora_tasks_count": ws["cutora_tasks_count"],
            "exported_count": ws["exported_count"],
            "uploaded_ok_count": ws["uploaded_ok_count"],
            "prev_completed_day": ws["prev_completed_day"],
            "prev_uploaded_ok": ws["prev_uploaded_ok"],
        }

    def get_latest_event(self) -> dict[str, Any] | None:
        """获取最新一条总线信号（按 ID 倒序）。"""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM spine_events ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["payload"] = json.loads(d["payload"] or "{}")
        except Exception:
            d["payload"] = {}
        return d

    def poll_events(self, since_id: int = 0, after_id: int | None = None, target: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """供各窗口模块轮询或 SSE 流式推送最新信号。"""
        if after_id is not None:
            since_id = after_id
        with self._connect() as conn:
            if target and target != "*":
                rows = conn.execute(
                    """
                    SELECT * FROM spine_events
                    WHERE id > ? AND (target = '*' OR target = ?)
                    ORDER BY id ASC LIMIT ?
                    """,
                    (since_id, target, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM spine_events WHERE id > ? ORDER BY id ASC LIMIT ?",
                    (since_id, limit),
                ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d["payload"] or "{}")
            except Exception:
                d["payload"] = {}
            out.append(d)
        return out

    # --- 闭环信号 1：Orante 任务表 -> CUTORA 智能拼接一键装载 ---
    def export_batch_for_cutora_stitch(
        self,
        batch_dir: Path | None = None,
        batch_date: str = "",
        batch_no: str = "",
    ) -> dict[str, Any]:
        """直接将 Orante 批次任务表导出为 CUTORA 智能拼接可直接消费的组与文案字典，并写入 _order.json。"""
        tasks = discover_orante_batch_tasks(
            batch_or_workspace_dir=batch_dir,
            batch_date=batch_date,
            batch_no=batch_no,
        )
        scripts_map: dict[str, str] = {}
        task_list: list[dict[str, Any]] = []
        for t in tasks:
            if t.task_dir and Path(t.task_dir).is_dir():
                write_order_lock_for_cutora(Path(t.task_dir))
            scripts_map[t.name] = t.copy_text
            scripts_map[t.task_id] = t.copy_text
            task_list.append(t.to_dict())

        self.emit(
            topic="table.synced_to_cutora",
            source="orante_desk",
            target="cutora_editor",
            payload={"count": len(task_list), "batch_date": batch_date, "batch_no": batch_no},
        )
        return {
            "ok": True,
            "count": len(task_list),
            "tasks": task_list,
            "scripts_by_group": scripts_map,
        }

    # --- 闭环信号 2：CUTORA 智能拼接检出缺片 -> 实时回流 Orante + 触发 Robota 0-Token 自动补生 ---
    def report_stitch_gaps_to_orante(
        self,
        task_id: str,
        task_dir: Path,
        missing_entries: list[dict[str, Any]],
        missing_segments: list[int] | None = None,
        auto_regenerate: bool | None = None,
    ) -> dict[str, Any]:
        """当 CUTORA 智能拼接检测到缺词/缺片时调用：
        1. 确保 `<task_dir>/clips/_stitched/缺失文案清单.txt` 按 Orante `gapfill.py` 规范写出；
        2. 写入 `studio_spine.db` 的 `gap_reports` 表；
        3. 若开启了 `realtime_gapfill_feedback` / `robota_zero_token_replenish`，自动通知 Orante 标红并触发补生！
        """
        cfg_sig = load_studio_config().get("signals", {})
        if auto_regenerate is None:
            auto_regenerate = bool(cfg_sig.get("robota_zero_token_replenish", True))

        stitched_dir = task_dir / "clips" / "_stitched"
        stitched_dir.mkdir(parents=True, exist_ok=True)
        report_file = stitched_dir / "缺失文案清单.txt"

        # 若 CUTORA 尚未写出 缺失文案清单.txt，则按 Orante gapfill.py 的标准语法生成
        if not report_file.is_file() and missing_entries:
            lines = [f"【组 {task_id}】"]
            for idx, item in enumerate(missing_entries, 1):
                w_start = int(item.get("word_start", 1))
                w_end = int(item.get("word_end", w_start + 3))
                txt = str(item.get("text", "")).strip()
                lines.append(f"  缺失{idx} (文案第 {w_start} ~ {w_end} 词)")
                lines.append(f"    {txt}")
            report_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        segs = missing_segments or []
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO gap_reports (task_id, task_dir, report_file, missing_segments, missing_text_items, auto_regen_triggered, status, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'open', ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    task_dir=excluded.task_dir,
                    report_file=excluded.report_file,
                    missing_segments=excluded.missing_segments,
                    missing_text_items=excluded.missing_text_items,
                    auto_regen_triggered=excluded.auto_regen_triggered,
                    status='open',
                    updated_at=excluded.updated_at
                """,
                (
                    task_id,
                    str(task_dir),
                    str(report_file),
                    json.dumps(segs),
                    json.dumps(missing_entries, ensure_ascii=False),
                    1 if auto_regenerate else 0,
                    now,
                ),
            )

        event_payload = {
            "task_id": task_id,
            "task_dir": str(task_dir),
            "report_file": str(report_file),
            "missing_segments": segs,
            "missing_count": len(missing_entries),
            "auto_regenerate": auto_regenerate,
        }
        self.emit(
            topic="stitch.gap_detected",
            source="cutora_editor",
            target="orante_desk",
            task_id=task_id,
            payload=event_payload,
        )

        orante_notified = _notify_orante_gapfill(task_id, segs, auto_regenerate=bool(auto_regenerate))
        return {
            "ok": True,
            "task_id": task_id,
            "report_file": str(report_file),
            "missing_segments": segs,
            "orante_notified": orante_notified,
            "auto_regenerate": auto_regenerate,
        }

    # --- 闭环信号 3：Flow/Grok 垫图被封禁 -> 自动转发至本地 LTX-2.5 22B 补生 ---
    def handoff_blocked_segment_to_ltx25(
        self,
        task_id: str,
        segment_no: int,
        image_path: str,
        prompt_text: str,
        seconds: float = 8.0,
    ) -> dict[str, Any]:
        """当云端引擎拒绝某张参考图时，自动转发至本机 Studio Gateway (127.0.0.1:7870) 用 LTX-2.5 生成。"""
        cfg = load_studio_config()
        port = int(cfg.get("network", {}).get("studio_gateway_port", 7870))
        payload = {
            "engine": "ltx25",
            "image_path": image_path,
            "direction": prompt_text,
            "line": "",
            "seconds": seconds,
            "quality": "balanced",
            "client_ref": f"{task_id}:s{segment_no:03d}:blocked_fallback",
        }
        self.emit(
            topic="engine.blocked_fallback_ltx25",
            source="orante_desk",
            target="studio_gateway",
            task_id=task_id,
            payload={"segment": segment_no, "engine": "ltx25"},
        )
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/videos",
                data=data,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                return {"ok": True, "gateway_job": res}
        except Exception as exc:
            return {"ok": False, "error": f"Studio Gateway (LTX-2.5) 提交排队待启动: {exc}"}

    # --- 闭环信号 4：CUTORA 导出成片完成 -> 自动按表格重命名 + rclone 持续上传 Google Drive ---
    def on_cutora_render_completed(
        self,
        task_id: str,
        exported_video_path: Path,
        target_filename: str = "",
        drive_folder_url: str = "",
    ) -> dict[str, Any]:
        """当 CUTORA 导出成片到 `<task_dir>/字幕成品/*.mp4` 时触发：
        1. 若指定了表格命名规则 (`target_filename`)，执行原子防冲突重命名；
        2. 将成片加入 `delivery_queue`；
        3. 发送 `render.completed` 信号通知 Orante 将任务标为 `finished` 并唤醒 TableTool 的 rclone 上传器。
        """
        final_path = exported_video_path
        if target_filename:
            if not target_filename.lower().endswith(".mp4"):
                target_filename += ".mp4"
            cand = exported_video_path.parent / target_filename
            if cand != exported_video_path and exported_video_path.is_file() and not cand.exists():
                temp_path = exported_video_path.parent / f"__rnm_{os.getpid()}__{exported_video_path.name}"
                try:
                    exported_video_path.rename(temp_path)
                    temp_path.rename(cand)
                    final_path = cand
                except OSError:
                    if temp_path.exists() and not exported_video_path.exists():
                        temp_path.rename(exported_video_path)

        now = time.time()
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO delivery_queue (task_id, source_video, renamed_video, drive_folder_url, status, updated_at)
                VALUES (?, ?, ?, ?, 'queued', ?)
                """,
                (task_id, str(exported_video_path), str(final_path), drive_folder_url, now),
            )
            delivery_id = int(cur.lastrowid or 0)

        self.emit(
            topic="render.completed",
            source="cutora_editor",
            target="*",
            task_id=task_id,
            payload={
                "delivery_id": delivery_id,
                "task_id": task_id,
                "final_video": str(final_path),
                "drive_folder_url": drive_folder_url,
            },
        )
        return {
            "ok": True,
            "delivery_id": delivery_id,
            "final_video": str(final_path),
            "queued_for_drive_upload": bool(drive_folder_url),
        }


import os


def _notify_orante_gapfill(task_id: str, missing_segments: list[int], auto_regenerate: bool) -> bool:
    """通过本机 Loopback 通知正在运行的 Orante 2.0 刷新 gapfill 状态（带 Session Cookie 握手）。"""
    cfg = load_studio_config()
    port = int(cfg.get("network", {}).get("orante_port", 9400))
    base = f"http://127.0.0.1:{port}"
    try:
        import http.cookiejar
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        # 先访问 GET / 获取 orante_session cookie
        req_root = urllib.request.Request(f"{base}/", headers={"Host": f"127.0.0.1:{port}"})
        opener.open(req_root, timeout=2.0).read()

        # 触发 GET /api/tasks/{tid}/gapfill 让 Orante 解析 缺失文案清单.txt
        req_gap = urllib.request.Request(
            f"{base}/api/tasks/{task_id}/gapfill",
            headers={"Host": f"127.0.0.1:{port}", "Origin": base},
        )
        gap_resp = json.loads(opener.open(req_gap, timeout=5.0).read().decode("utf-8"))

        if auto_regenerate:
            segs_to_redo = list(missing_segments)
            if not segs_to_redo and isinstance(gap_resp, dict):
                for item in gap_resp.get("items") or []:
                    if item.get("kind") in ("real", "mismatch") and item.get("segments"):
                        segs_to_redo.extend(item["segments"])
            segs_to_redo = sorted(set(segs_to_redo))
            if segs_to_redo:
                body = json.dumps({"segments": segs_to_redo}).encode("utf-8")
                req_regen = urllib.request.Request(
                    f"{base}/api/tasks/{task_id}/regenerate",
                    data=body,
                    method="POST",
                    headers={
                        "Host": f"127.0.0.1:{port}",
                        "Origin": base,
                        "Content-Type": "application/json",
                    },
                )
                opener.open(req_regen, timeout=5.0).read()
        return True
    except Exception:
        return False


_SPINE_SINGLETON: SpineDB | None = None


def get_spine_db() -> SpineDB:
    global _SPINE_SINGLETON
    if _SPINE_SINGLETON is None:
        _SPINE_SINGLETON = SpineDB()
    return _SPINE_SINGLETON

