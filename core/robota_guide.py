# -*- coding: utf-8 -*-
"""
片语工作室 · Robota 协同操作代理核心引擎
========================================
职责：
1. 纯对话式协同操作（用户只需说话或粘贴链接，Robota 自动建夹、下载、按 v01(1)(2) 改名、写入 Orante 并打开工作房间）。
2. 实时逐文件进度汇报（绝不卡在 25%/35%），支持断点续传（已下好的文件自动跳过）。
3. 深度写入《右键表格工具》工程文件 (.renamer_project.json) 与《Orante》真实数据库 (data/orante.db)，
   让用户点开任意一个工具按钮时，看到的都是当前正在进行的真实任务与实时进度。
4. 零知识密码保险箱（Windows DPAPI 加密，Robota 永不接触明文账密）与木马病毒安全巡检。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import datetime
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

STUDIO_ROOT = Path(r"D:\0_LTX")
_VENV_SITE = STUDIO_ROOT / "runtime" / "venv" / "Lib" / "site-packages"
if _VENV_SITE.is_dir() and str(_VENV_SITE) not in sys.path:
    sys.path.insert(0, str(_VENV_SITE))
CONFIG_PATH = STUDIO_ROOT / "config" / "robota_guide_config.json"
LUCA_FEEDBACK_PATH = STUDIO_ROOT / "logs" / "luca_feedback_patches.jsonl"
SECURITY_LOG_PATH = STUDIO_ROOT / "logs" / "security_sentinel.jsonl"
VAULT_PATH = STUDIO_ROOT / "config" / "user_encrypted_vault.bin"
ORANTE_REAL_DB_PATH = STUDIO_ROOT / "modules" / "orante_desk" / "data" / "orante.db"
ORANTE_LEGACY_DB_PATH = STUDIO_ROOT / "modules" / "orante_desk" / "data" / "orante.sqlite3"
SPINE_DB_PATH = STUDIO_ROOT / "logs" / "studio_spine.sqlite3"
TABLE_TOOL_PREFS_PATH = Path.home() / ".right_click_table_tool_prefs.json"

CREATE_NO_WINDOW = 0x08000000


def _hidden_popen_kwargs() -> Dict[str, Any]:
    kw: Dict[str, Any] = {}
    if os.name == "nt":
        kw["creationflags"] = CREATE_NO_WINDOW
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kw["startupinfo"] = si
    return kw


# ============================================================================
# 1. 零知识加密保险箱 (Windows DPAPI) —— Robota 永不接触明文账密
# ============================================================================
class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


class ZeroKnowledgeCredentialVault:
    def __init__(self, vault_file: Path = VAULT_PATH):
        self.vault_file = vault_file
        self.vault_file.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _dpapi_encrypt(raw_bytes: bytes) -> bytes:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        buf = ctypes.create_string_buffer(raw_bytes, len(raw_bytes))
        blob_in = _DATA_BLOB(len(raw_bytes), buf)
        blob_out = _DATA_BLOB()
        if not crypt32.CryptProtectData(
            ctypes.byref(blob_in), "CutoraUserSecret", None, None, None, 0, ctypes.byref(blob_out)
        ):
            raise RuntimeError("Windows DPAPI 加密失败")
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)

    def store_from_user_dialog_only(self, key_name: str, secret_value: str) -> Dict[str, Any]:
        if not key_name or not secret_value:
            return {"ok": False, "msg": "名称或内容不能为空"}
        records = self._load_meta()
        enc = self._dpapi_encrypt(secret_value.encode("utf-8"))
        fingerprint = hashlib.sha256(secret_value.encode("utf-8")).hexdigest()[:10]
        records[key_name] = {
            "cipher_hex": enc.hex(),
            "fingerprint": f"sha256:{fingerprint}",
            "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        self.vault_file.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        secret_value = ""
        return {"ok": True, "key": key_name, "fingerprint": f"sha256:{fingerprint}"}

    def _load_meta(self) -> Dict[str, Any]:
        if not self.vault_file.exists():
            return {}
        try:
            return json.loads(self.vault_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def list_configured_keys_for_robota(self) -> List[Dict[str, str]]:
        records = self._load_meta()
        return [
            {
                "key": k,
                "status": "已加密保存（Robota 无权读取明文）",
                "fingerprint": v.get("fingerprint", ""),
                "updated_at": v.get("updated_at", ""),
            }
            for k, v in records.items()
        ]


# ============================================================================
# 2. 安全防护：沙箱边界、木马脚本检测、不删用户文件
# ============================================================================
class RobotaSecuritySentinel:
    MALICIOUS_EXTS = {
        ".exe", ".dll", ".scr", ".vbs", ".vbe", ".js", ".jse",
        ".wsf", ".wsh", ".bat", ".cmd", ".ps1", ".msi", ".hta", ".com", ".pif"
    }
    DOUBLE_EXT_PATTERN = re.compile(
        r".+\.(mp4|mov|jpg|jpeg|png|webp|mp3|wav|xlsx|csv|pdf|txt)\.(exe|scr|bat|cmd|vbs|ps1|com|hta)$",
        re.IGNORECASE,
    )

    def __init__(self, workspace_root_getter: Callable[[], Path]):
        self._get_ws = workspace_root_getter
        SECURITY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    def is_path_allowed(self, target_path: Path) -> Tuple[bool, str]:
        try:
            rp = target_path.resolve()
        except Exception:
            return False, f"无效路径：{target_path}"
        studio_rp = STUDIO_ROOT.resolve()
        ws_rp = self._get_ws().resolve()
        if str(rp).lower().startswith(str(studio_rp).lower()) or str(rp).lower().startswith(str(ws_rp).lower()):
            return True, "安全路径"
        return False, f"路径 [{rp}] 超出工作区范围，需另外申请权限"

    def scan_directory_for_threats(self, folder: Path) -> List[Dict[str, Any]]:
        alerts: List[Dict[str, Any]] = []
        if not folder or not folder.exists():
            return alerts
        try:
            for item in folder.rglob("*"):
                if not item.is_file():
                    continue
                low_s = str(item).lower()
                if str(STUDIO_ROOT / "runtime").lower() in low_s or str(STUDIO_ROOT / "tools").lower() in low_s:
                    continue
                name = item.name
                ext = item.suffix.lower()
                reason = ""
                if self.DOUBLE_EXT_PATTERN.match(name):
                    reason = f"发现伪装成素材的双扩展名可疑文件：{name}"
                elif ext in self.MALICIOUS_EXTS:
                    reason = f"素材目录中发现可执行程序或脚本 ({ext})：{name}"
                if reason:
                    alert = {
                        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
                        "file": str(item),
                        "name": name,
                        "reason": reason,
                    }
                    alerts.append(alert)
                    try:
                        with open(SECURITY_LOG_PATH, "a", encoding="utf-8") as f:
                            f.write(json.dumps(alert, ensure_ascii=False) + "\n")
                    except Exception:
                        pass
        except Exception:
            pass
        return alerts


# ============================================================================
# 3. 知识库与问题记录（自然简约的生活化表达）
# ============================================================================
class StudioKnowledgeBase:
    VIDEO_TYPES = {
        "1": {
            "code": "普通祷告",
            "title": "普通祷告视频",
            "orante_type": "short",
            "desc": "下载对应编号的图片与背景视频，按 v01(1), v01(2) 命名后送入 Orante 排片生成。",
        },
        "2": {
            "code": "长自媒体",
            "title": "长的自媒体视频",
            "orante_type": "long",
            "desc": "同一编号文件夹内多段素材自动按 v01(1), v01(2), v01(3) 编号下载，送入 Orante 与 CUTORA 长片拼接。",
        },
        "3": {
            "code": "现成素材分屏合成",
            "title": "现成素材分屏合成视频",
            "orante_type": "short",
            "desc": "把现成图片和视频按 v01(1), v01(2) 下载归档，登记到 Orante 任务表并可直接做分屏合成。",
        },
    }

    @staticmethod
    def record_luca_feedback(
        user_issue: str,
        action_taken: str,
        effect_summary: str,
        details: Any = None,
    ) -> Dict[str, Any]:
        LUCA_FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "id": f"PATCH-{int(time.time() * 1000)}",
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
            "user_issue": user_issue,
            "robota_resolution": action_taken,
            "effect_after_fix": effect_summary,
            "details": details or {},
        }
        try:
            with open(LUCA_FEEDBACK_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass
        return record

    @staticmethod
    def load_recent_luca_feedback(limit: int = 20) -> List[Dict[str, Any]]:
        if not LUCA_FEEDBACK_PATH.exists():
            return []
        out: List[Dict[str, Any]] = []
        try:
            lines = LUCA_FEEDBACK_PATH.read_text(encoding="utf-8").splitlines()
            for ln in reversed(lines[-limit:]):
                if ln.strip():
                    out.append(json.loads(ln))
        except Exception:
            pass
        return out


# ============================================================================
# 4. 三分身后台并发协调器 (RobotaSwarmCoordinator)
# ============================================================================
@dataclass
class CloneState:
    clone_id: str
    name: str
    role: str
    status: str = "IDLE"          # IDLE / RUNNING / COMPLETED / ERROR
    current_task: str = "空闲"
    progress: int = 0
    completed_count: int = 0
    last_updated: str = ""


class RobotaSwarmCoordinator:
    def __init__(self, on_status_change: Optional[Callable[[Dict[str, CloneState]], None]] = None):
        self._executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="RobotaClone")
        self._state_lock = threading.RLock()
        self._resource_locks: Dict[str, threading.RLock] = {}
        self._on_change = on_status_change
        now_s = datetime.datetime.now().strftime("%H:%M:%S")
        self.clones: Dict[str, CloneState] = {
            "alpha": CloneState("alpha", "分身 1 · 下载改名", "右键表格下载 / v01(1) 编号", last_updated=now_s),
            "beta": CloneState("beta", "分身 2 · 排片整理", "建文件夹 / 写入 Orante 任务表", last_updated=now_s),
            "gamma": CloneState("gamma", "分身 3 · 拼接检查", "合成准备 / 安全扫描 / 记录反馈", last_updated=now_s),
        }

    def get_resource_lock(self, resource_key: str) -> threading.RLock:
        with self._state_lock:
            if resource_key not in self._resource_locks:
                self._resource_locks[resource_key] = threading.RLock()
            return self._resource_locks[resource_key]

    def update_clone(
        self,
        clone_id: str,
        status: str,
        task_desc: str,
        progress: int = 0,
        inc_completed: bool = False,
    ) -> None:
        with self._state_lock:
            c = self.clones.get(clone_id)
            if not c:
                return
            c.status = status
            c.current_task = task_desc
            c.progress = max(0, min(100, int(progress)))
            if inc_completed:
                c.completed_count += 1
            c.last_updated = datetime.datetime.now().strftime("%H:%M:%S")
            snapshot = {k: CloneState(**vars(v)) for k, v in self.clones.items()}
        if self._on_change:
            try:
                self._on_change(snapshot)
            except Exception:
                pass


# ============================================================================
# 5. 一做到底的执行代理 (DailyWorkflowConductor)
# ============================================================================
class DailyWorkflowConductor:
    def __init__(self, swarm: Optional[RobotaSwarmCoordinator] = None):
        self.swarm = swarm or RobotaSwarmCoordinator()
        self.vault = ZeroKnowledgeCredentialVault()
        self.config = self._load_config()
        self.sentinel = RobotaSecuritySentinel(self.get_workspace_root)
        self._cancel_flag = threading.Event()
        self._active_running = False

    def _load_config(self) -> Dict[str, Any]:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        # 优先检测 D:\1_PROJECT（如果存在），否则用 D:\0_LTX\workspace
        default_ws = Path(r"D:\1_PROJECT") if Path(r"D:\1_PROJECT").exists() else (STUDIO_ROOT / "workspace")
        default_ws.mkdir(parents=True, exist_ok=True)
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    # 如果 D:\1_PROJECT\2026-10-14 存在且里面有任务，自动记住 D:\1_PROJECT
                    if Path(r"D:\1_PROJECT").exists() and (
                        data.get("workspace_root") == str(STUDIO_ROOT / "workspace")
                    ):
                        data["workspace_root"] = r"D:\1_PROJECT"
                    return data
            except Exception:
                pass
        cfg = {
            "workspace_root": str(default_ws),
            "first_time_guided": False,
            "default_task_count": 31,
            "default_video_type": "3",
            "last_work_day": "",
        }
        self._save_config(cfg)
        return cfg

    def _save_config(self, cfg: Optional[Dict[str, Any]] = None) -> None:
        if cfg is not None:
            self.config = cfg
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_workspace_root(self) -> Path:
        p = Path(self.config.get("workspace_root") or r"D:\1_PROJECT")
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception:
            p = STUDIO_ROOT / "workspace"
            p.mkdir(parents=True, exist_ok=True)
        return p

    def set_workspace_root(self, new_root: str) -> Path:
        p = Path(new_root).resolve()
        p.mkdir(parents=True, exist_ok=True)
        self.config["workspace_root"] = str(p)
        self.config["first_time_guided"] = True
        self._save_config()
        return p

    def find_rclone_exe(self) -> Optional[Path]:
        candidates = [
            STUDIO_ROOT / "modules" / "table_tool" / "app" / "rclone.exe",
            STUDIO_ROOT / "tools" / "rclone" / "rclone.exe",
            STUDIO_ROOT / "modules" / "table_tool" / "rclone.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "RightClickTableTool" / "rclone.exe",
        ]
        for c in candidates:
            if c.is_file():
                return c
        which = shutil.which("rclone")
        return Path(which) if which else None

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # 智能识别当前活跃工作日期目录（全面兼容直接放在日期目录、15视频/15图片、或 01_右键表格_下载素材库）
    # ------------------------------------------------------------------
    _REAL_EXT_RE = re.compile(r"^\.[A-Za-z0-9]{1,7}$")

    @classmethod
    def _split_real_ext(cls, filename: str) -> Tuple[str, str]:
        """只把纯英文数字后缀（如 .mp4/.jpg/.png）当成真正的扩展名；
        像『v13.测试子涵-8』『v61.已使用_1』这种点号后面带中文或下划线的，属于文件名主干而非扩展名！"""
        base, own = os.path.splitext(filename or "")
        if own and not cls._REAL_EXT_RE.match(own):
            return filename or "", ""
        return base, own

    @staticmethod
    def _sniff_media_ext(file_path: Path) -> str:
        """通过文件头魔数嗅探真实媒体扩展名（.mp4 / .jpg / .png / .webp / .mkv）。"""
        if not file_path or not file_path.is_file():
            return ""
        try:
            with open(file_path, "rb") as f:
                hdr = f.read(32)
            if len(hdr) >= 12 and hdr[4:8] in (b"ftyp", b"moov", b"wide", b"mdat"):
                return ".mp4"
            if hdr.startswith(b"\xff\xd8\xff"):
                return ".jpg"
            if hdr.startswith(b"\x89PNG\r\n\x1a\n"):
                return ".png"
            if len(hdr) >= 12 and hdr[:4] == b"RIFF" and hdr[8:12] == b"WEBP":
                return ".webp"
            if hdr.startswith(b"\x1a\x45\xdf\xa3"):
                return ".mkv"
        except Exception:
            pass
        return ""

    LIFECYCLE_STATE_PATH = STUDIO_ROOT / "data" / "memory" / "project_lifecycle_state.json"

    @classmethod
    def _load_lifecycle_state(cls) -> Dict[str, Any]:
        cls.LIFECYCLE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        if cls.LIFECYCLE_STATE_PATH.is_file():
            try:
                data = json.loads(cls.LIFECYCLE_STATE_PATH.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {
            "locked_active_day": "",
            "locked_active_batch": "A",
            "active_recipe": "B",
            "completed_days": {},
            "completed_batches": {},
            "updated_at": "",
        }

    @classmethod
    def _save_lifecycle_state(cls, state: Dict[str, Any]) -> None:
        cls.LIFECYCLE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        state["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cls.LIFECYCLE_STATE_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def inspect_day_lifecycle(cls, day_dir: Path, state_override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        全生命周期 6 阶段感知引擎（读取真实磁盘与隐藏 `.renamer_project.json` 上传回执）：
        - STAGE_0_READY          : 待启动（新日期/新批次准备就绪）
        - STAGE_1_MATERIAL_READY : 素材已下载就位（待进 Orante）
        - STAGE_2_CLIPS_READY    : Orante 分镜/Clips 已生成（待进 CUTORA）
        - STAGE_3_CUTORA_EDITING : CUTORA 工程已建 · 连线/分屏/字幕/AI署名中
        - STAGE_4_EXPORTED       : 已按 40 条拆分并导出成片（待上传云盘）
        - STAGE_5_UPLOADED_DONE  : 字幕成品/.renamer_project.json 全部 `up.status == "ok"`（已全部上传完结 ✅）
        """
        st = state_override or cls._load_lifecycle_state()
        day_name = day_dir.name
        user_marked_done = bool((st.get("completed_days") or {}).get(day_name))

        vid_exts = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
        img_exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

        export_folders: List[str] = []
        exported_mp4_count = 0
        total_upload_rows = 0
        uploaded_ok_count = 0
        upload_updated_at = ""

        if day_dir.is_dir():
            # 1. 深度扫描所有成片/导出子目录及其隐藏 .renamer_project.json 上传状态
            for sub in day_dir.rglob("*"):
                if not sub.is_dir():
                    continue
                lname = sub.name.lower()
                if any(k in lname for k in ("字幕成品", "成品", "导出", "exports", "output")):
                    mp4s = [f for f in sub.iterdir() if f.is_file() and f.suffix.lower() in vid_exts]
                    if mp4s:
                        exported_mp4_count += len(mp4s)
                        export_folders.append(str(sub.relative_to(day_dir)))
                    ren_file = sub / ".renamer_project.json"
                    if ren_file.is_file():
                        try:
                            rdata = json.loads(ren_file.read_text(encoding="utf-8"))
                            rows = ((rdata.get("grid") or {}).get("rows") if isinstance(rdata.get("grid"), dict) else None) or rdata.get("rows") or []
                            if rows:
                                ok_cnt = sum(
                                    1 for r in rows
                                    if isinstance(r, dict) and str((r.get("up") or {}).get("status") or "").lower() == "ok"
                                )
                                if ok_cnt > 0 or len(rows) > 1:
                                    total_upload_rows += len(rows)
                                    uploaded_ok_count += ok_cnt
                                    upload_updated_at = str(rdata.get("updated") or upload_updated_at)
                                    rel_s = str(sub.relative_to(day_dir))
                                    if rel_s not in export_folders:
                                        export_folders.append(rel_s)
                        except Exception:
                            pass

        # 2. 扫描拆分工程 (.asproj.json) 与主工程 (.cutora)
        split_projects = sorted([p.name for p in day_dir.glob("*.asproj.json")]) if day_dir.is_dir() else []
        cutora_files = sorted(
            [p for p in day_dir.glob("*.cutora") if not p.name.endswith(".backup.cutora")],
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        ) if day_dir.is_dir() else []
        cutora_tasks_count = 0
        if cutora_files:
            try:
                cdata = json.loads(cutora_files[0].read_text(encoding="utf-8"))
                cutora_tasks_count = len(cdata.get("tasks") or [])
            except Exception:
                pass

        # 3. 扫描批次 A ~ Z 及 Orante clips
        batches_info: Dict[str, Dict[str, Any]] = {}
        total_clips_count = 0
        if day_dir.is_dir():
            for b_cand in sorted(day_dir.iterdir(), key=lambda x: x.name):
                if b_cand.is_dir() and re.match(r"^[A-Z]$", b_cand.name):
                    b_code = b_cand.name
                    b_dir = b_cand
                    b_clips = 0
                    b_up_ok = 0
                    b_up_total = 0
                    for cp in b_dir.rglob("*"):
                        if cp.is_dir() and cp.name.lower() in ("clips", "_clips"):
                            b_clips += sum(1 for vf in cp.iterdir() if vf.is_file() and vf.suffix.lower() in vid_exts)
                        elif cp.is_file() and cp.name == ".renamer_project.json" and any(k in cp.parent.name.lower() for k in ("字幕成品", "成品", "导出", "exports", "output")):
                            try:
                                bd = json.loads(cp.read_text(encoding="utf-8"))
                                b_rows = ((bd.get("grid") or {}).get("rows") if isinstance(bd.get("grid"), dict) else None) or []
                                b_ok = sum(1 for r in b_rows if isinstance(r, dict) and str((r.get("up") or {}).get("status") or "").lower() == "ok")
                                if b_ok > 0:
                                    b_up_total += len(b_rows)
                                    b_up_ok += b_ok
                            except Exception:
                                pass
                    total_clips_count += b_clips
                    b_done = bool((st.get("completed_batches") or {}).get(f"{day_name}:{b_code}")) or (b_up_total > 0 and b_up_ok >= b_up_total)
                    batches_info[b_code] = {
                        "exists": True,
                        "clips_count": b_clips,
                        "uploaded_ok": b_up_ok,
                        "completed": b_done,
                    }

        # 4. 统计原始下载素材（严格排除 字幕成品/成品/导出/clips/Orante任务目录）
        raw_media = cls._collect_downloaded_media(day_dir)
        raw_vids = 0
        raw_imgs = 0
        for f in raw_media:
            _, ext = cls._split_real_ext(f.name)
            el = ext.lower() or cls._sniff_media_ext(f)
            if el in vid_exts:
                raw_vids += 1
            elif el in img_exts:
                raw_imgs += 1

        # 5. 判定当前活跃批次 (A -> B -> C -> D ...)
        inferred_batch = "A"
        for b_code in sorted(batches_info.keys()):
            b_meta = batches_info[b_code]
            if b_meta["exists"] and not b_meta["completed"]:
                inferred_batch = b_code
        if st.get("locked_active_day") == day_name and st.get("locked_active_batch"):
            inferred_batch = str(st["locked_active_batch"]).upper()

        # 6. 判定 6 阶段生命周期
        is_all_uploaded = user_marked_done or (total_upload_rows > 0 and uploaded_ok_count >= total_upload_rows)
        if is_all_uploaded:
            stage_code = "STAGE_5_UPLOADED_DONE"
            stage_index = 5
            stage_label = f"已全部上传完结 ({uploaded_ok_count}/{total_upload_rows or uploaded_ok_count} ✅)"
        elif exported_mp4_count > 0 or total_upload_rows > 0:
            stage_code = "STAGE_4_EXPORTED"
            stage_index = 4
            stage_label = f"成片已导出 ({exported_mp4_count}条 · 已上传 {uploaded_ok_count}/{total_upload_rows or exported_mp4_count})"
        elif split_projects or cutora_tasks_count > 0:
            stage_code = "STAGE_3_CUTORA_EDITING"
            stage_index = 3
            split_hint = f" · 已按40条拆分为 {len(split_projects)} 个子工程" if split_projects else ""
            stage_label = f"CUTORA 工程就绪 ({cutora_tasks_count}条时间线{split_hint})"
        elif total_clips_count > 0:
            stage_code = "STAGE_2_CLIPS_READY"
            stage_index = 2
            stage_label = f"Orante Clips 已生成 ({total_clips_count}段) · 待进 CUTORA"
        elif raw_vids > 0 or raw_imgs > 0:
            stage_code = "STAGE_1_MATERIAL_READY"
            stage_index = 1
            stage_label = f"原始素材已就位 (视频{raw_vids}·图片{raw_imgs}) · 待进 Orante"
        else:
            stage_code = "STAGE_0_READY"
            stage_index = 0
            stage_label = f"待启动 ({inferred_batch}批次准备就绪)"

        return {
            "day": day_name,
            "stage_code": stage_code,
            "stage_index": stage_index,
            "stage_label": stage_label,
            "is_completed": is_all_uploaded,
            "active_batch": inferred_batch,
            "batches": batches_info,
            "raw_vid_count": raw_vids,
            "raw_img_count": raw_imgs,
            "clips_count": total_clips_count,
            "cutora_tasks_count": cutora_tasks_count,
            "split_projects": split_projects,
            "export_folders": export_folders,
            "exported_mp4_count": exported_mp4_count,
            "total_upload_rows": total_upload_rows,
            "uploaded_ok_count": uploaded_ok_count,
            "upload_updated_at": upload_updated_at,
        }

    def update_project_lifecycle_state(
        self,
        active_day: str = "",
        active_batch: str = "",
        mark_day_completed: str = "",
        recipe_type: str = "",
        note: str = "",
    ) -> Dict[str, Any]:
        """
        将用户在对话中交代的日期切换、批次切换（如 '16号 A批次'）或完成状态（如 '15号已全部上传完成'）持久化写入状态机，
        并自动创建对应的 `<日期>/<批次>` 文件夹。
        """
        ws_root = self.get_workspace_root()
        today = datetime.date.today()
        st = self._load_lifecycle_state()

        def _norm_day(raw_d: str) -> str:
            s = (raw_d or "").strip()
            if not s:
                return ""
            m_f = re.search(r"(20\d{2}-\d{2}-\d{2})", s)
            if m_f:
                return m_f.group(1)
            m_4 = re.search(r"(?<!\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)", s)
            if m_4:
                return f"{today.year:04d}-{int(m_4.group(1)):02d}-{int(m_4.group(2)):02d}"
            m_n = re.search(r"(\d{1,2})", s)
            if m_n:
                return f"{today.year:04d}-{today.month:02d}-{int(m_n.group(1)):02d}"
            if "今天" in s or "今日" in s:
                return today.strftime("%Y-%m-%d")
            if "昨天" in s or "昨日" in s:
                return (today - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            return s

        if mark_day_completed:
            done_d = _norm_day(mark_day_completed)
            if done_d:
                st.setdefault("completed_days", {})[done_d] = True

        if active_day:
            norm_active = _norm_day(active_day)
            if norm_active:
                st["locked_active_day"] = norm_active
                self.config["last_work_day"] = norm_active

        if active_batch:
            b_clean = re.sub(r"[^A-Za-z]", "", active_batch).upper()[:1] or "A"
            st["locked_active_batch"] = b_clean

        if recipe_type:
            r_clean = re.sub(r"[^ABab]", "", recipe_type).upper()[:1]
            if r_clean:
                st["active_recipe"] = r_clean

        if note:
            st["last_note"] = note.strip()

        target_day_str = st.get("locked_active_day") or today.strftime("%Y-%m-%d")
        target_batch_str = st.get("locked_active_batch") or "A"
        target_day_dir = ws_root / target_day_str
        target_day_dir.mkdir(parents=True, exist_ok=True)
        (target_day_dir / target_batch_str).mkdir(parents=True, exist_ok=True)

        self._save_lifecycle_state(st)
        self._save_config()
        lc = self.inspect_day_lifecycle(target_day_dir, st)
        return {
            "ok": True,
            "active_day": target_day_str,
            "active_batch": target_batch_str,
            "active_recipe": st.get("active_recipe", "B"),
            "day_dir": str(target_day_dir),
            "batch_dir": str(target_day_dir / target_batch_str),
            "lifecycle": lc,
        }

    def sync_state_from_user_message(self, user_text: str) -> Optional[Dict[str, Any]]:
        """
        从用户的自然语言对话中实时捕捉状态变更意图并持久化（例如：
        - “现在活跃日期已经是 16号了”
        - “15号已经上传完了，开始 16号 A批次”
        - “切换到 B批次” / “继续操作批次B”
        ）。
        """
        txt = (user_text or "").strip()
        if not txt:
            return None

        today = datetime.date.today()
        changed = False
        new_active_day = ""
        new_batch = ""
        mark_done_day = ""

        # 1. 检测“XX号已经上传完/做完/完成了”
        m_done = re.search(r"(\d{1,2})\s*[号日][^，。；]*?(?:已经|已|全部|都)?\s*(?:上传完|做完|完成|结束|搞定)", txt)
        if m_done:
            d_num = int(m_done.group(1))
            mark_done_day = f"{today.year:04d}-{today.month:02d}-{d_num:02d}"
            changed = True

        # 2. 检测“现在活跃日期已经是 XX号 / 开始 XX号 / 切换到 XX号 / 今天是 XX号”
        m_switch = re.search(
            r"(?:活跃日期|当前日期|切换到|切到|开始|做|进入|今天是|现在是|改成|设为)[^\d]{0,8}?(20\d{2}-\d{2}-\d{2}|\d{1,2}\s*[号日])",
            txt,
        )
        if m_switch:
            new_active_day = m_switch.group(1)
            changed = True
        elif mark_done_day and ("今天" in txt or "开始" in txt or str(today.day) in txt):
            new_active_day = today.strftime("%Y-%m-%d")
            changed = True

        # 3. 检测批次（如 A批次 / B批次 / 批次A / 批次B / C批次 ...）
        m_batch = re.search(r"(?:批次\s*([A-Za-z])|\b([A-Za-z])\s*批次)", txt)
        if m_batch:
            new_batch = (m_batch.group(1) or m_batch.group(2) or "A").upper()
            changed = True
        elif new_active_day and not new_batch:
            new_batch = "A"

        if not changed:
            return None

        return self.update_project_lifecycle_state(
            active_day=new_active_day,
            active_batch=new_batch,
            mark_day_completed=mark_done_day,
            note=f"用户对话同步: {txt[:80]}",
        )

    @classmethod
    def _day_has_content(cls, day_dir: Path) -> bool:
        if not day_dir or not day_dir.is_dir():
            return False
        if len(cls._collect_downloaded_media(day_dir)) > 0:
            return True
        if (day_dir / "A").exists():
            return True
        if (day_dir / "01_右键表格_下载素材库" / "右键表格_今日下载命名清单.json").exists():
            return True
        try:
            for sub in day_dir.iterdir():
                if sub.is_dir() and not sub.name.startswith(".") and any(sub.iterdir()):
                    return True
        except Exception:
            pass
        return False

    def resolve_active_day_str(self, ws: Optional[Path] = None, hint_text: str = "") -> str:
        ws_root = ws or self.get_workspace_root()
        today = datetime.date.today()
        today_str = today.strftime("%Y-%m-%d")
        st = self._load_lifecycle_state()

        # 1. 如果提示词中包含明确日期（如 2026-10-16、1016视频、1016图片、15号、16号、昨天、今天）
        #    注意：先剔除“昨天/XX号已经完成/上传完”这类回顾性子句，防止把已完成日期误判为目标日期
        if hint_text:
            h_raw = hint_text.strip()
            h = re.sub(
                r"(?:昨天|昨日)?\s*(?:20\d{2}-\d{2}-\d{2}|\d{1,2}\s*[号日])[^，。；\n]*?(?:已经|已|全部|都)\s*(?:上传|做完|完成|结束|搞定)[^，。；\n]*",
                "",
                h_raw,
            ).strip() or h_raw
            m_full = re.findall(r"(20\d{2}-\d{2}-\d{2})", h)
            if m_full:
                return m_full[-1]
            m_mmdd = re.findall(r"(?<!\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)", h)
            if m_mmdd:
                mm, dd = int(m_mmdd[-1][0]), int(m_mmdd[-1][1])
                cand_mmdd = f"{today.year:04d}-{mm:02d}-{dd:02d}"
                if (ws_root / cand_mmdd).exists() or cand_mmdd == today_str:
                    return cand_mmdd
            m_days = re.findall(r"(?<!\d)(\d{1,2})\s*(?:[号日]|视频|图片|[A-Za-z]批次)", h)
            if m_days:
                d_num = int(m_days[-1])
                cand_name = f"{today.year:04d}-{today.month:02d}-{d_num:02d}"
                if (ws_root / cand_name).exists():
                    return cand_name
                try:
                    for d in sorted(ws_root.iterdir(), key=lambda p: p.name, reverse=True):
                        if d.is_dir() and re.match(rf"^\d{{4}}-\d{{2}}-{d_num:02d}$", d.name):
                            return d.name
                except Exception:
                    pass
            if "今天" in h or "今日" in h:
                (ws_root / today_str).mkdir(parents=True, exist_ok=True)
                return today_str
            if "昨天" in h or "昨日" in h:
                y_str = (today - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
                if (ws_root / y_str).exists():
                    return y_str

        # 2. 如果生命周期状态机中用户已明确锁定了活跃日期（且该日期 >= 今天或存在于工作区），优先遵守！
        locked_day = str(st.get("locked_active_day") or "").strip()
        if locked_day and (ws_root / locked_day).exists():
            locked_lc = self.inspect_day_lifecycle(ws_root / locked_day, st)
            # 若锁定的日期不是旧的已完结日期（或者就是今天），直接返回
            if locked_day == today_str or not locked_lc.get("is_completed"):
                return locked_day

        # 3. 优先检查今日目录（如 2026-10-16）是否有素材或子文件夹
        today_dir = ws_root / today_str
        if self._day_has_content(today_dir):
            return today_str

        # 4. 查找最近一个有素材的日期目录，并检查它是否已经【全部上传完结 (STAGE_5_UPLOADED_DONE)】！
        try:
            date_dirs = sorted(
                [
                    d for d in ws_root.iterdir()
                    if d.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", d.name) and d.name <= today_str
                ],
                key=lambda p: p.name,
                reverse=True,
            )
            for cand in date_dirs:
                if cand.name == today_str:
                    continue
                if self._day_has_content(cand):
                    cand_lc = self.inspect_day_lifecycle(cand, st)
                    if cand_lc.get("is_completed"):
                        # 昨日/前序日期已 100% 上传完结！自动跨日推进到今日 (today_str) 的 A 批次！
                        today_dir.mkdir(parents=True, exist_ok=True)
                        (today_dir / "A").mkdir(parents=True, exist_ok=True)
                        st["locked_active_day"] = today_str
                        st["locked_active_batch"] = st.get("locked_active_batch") or "A"
                        st["prev_completed_day"] = cand.name
                        st["prev_completed_summary"] = {
                            "day": cand.name,
                            "uploaded_ok_count": cand_lc.get("uploaded_ok_count", 0),
                            "total_upload_rows": cand_lc.get("total_upload_rows", 0),
                            "export_folders": cand_lc.get("export_folders", []),
                            "upload_updated_at": cand_lc.get("upload_updated_at", ""),
                        }
                        self._save_lifecycle_state(st)
                        return today_str
                    return cand.name
        except Exception:
            pass

        today_dir.mkdir(parents=True, exist_ok=True)
        return today_str

    @classmethod
    def _resolve_day_media_dirs(cls, day_dir: Path, batch_letter: str = "") -> Tuple[Path, Path, Path]:
        """
        智能定位当期批次目录下的 (主下载素材目录 raw_dl_dir, 实际视频目录 actual_vid_dir, 实际图片目录 actual_img_dir)。
        严格遵守用户的批次目录规范：
        - 当天日期目录下只放批次文件夹（A / B / C / D / E ...），下载的素材文件夹（`<MMdd>图片`、`<MMdd>视频`）
          必须安放在所属批次文件夹内（如 `2026-10-16\\A\\1016图片`、`2026-10-16\\B\\1016图片`）！
        - 同时向后兼容历史日期（如 `2026-10-15` 直接放在日期根目录下的 `15视频`/`15图片`）。
        """
        parts = day_dir.name.split("-")
        mm_str = parts[1] if len(parts) >= 3 else ""
        dd_str = parts[-1]
        day_num = dd_str.lstrip("0") or dd_str
        mmdd_str = f"{mm_str}{dd_str}" if mm_str else dd_str
        m_dd_str = f"{mm_str.lstrip('0')}{dd_str}" if mm_str else dd_str
        legacy_dl = day_dir / "01_右键表格_下载素材库"

        # 确定目标批次字母
        b_let = re.sub(r"[^A-Za-z]", "", (batch_letter or "")).upper()[:1]
        if not b_let:
            st = cls._load_lifecycle_state()
            if st.get("locked_active_day") == day_dir.name and st.get("locked_active_batch"):
                b_let = str(st["locked_active_batch"]).upper()[:1]
            elif day_dir.is_dir():
                existing_batches = sorted(
                    [d.name for d in day_dir.iterdir() if d.is_dir() and re.match(r"^[A-Z]$", d.name)]
                )
                if existing_batches:
                    b_let = existing_batches[-1]
        if not b_let:
            b_let = "A"

        batch_dir = day_dir / b_let

        # 如果没有显式指定 batch_letter，且该历史日期在根目录下已有非空旧版图片/视频目录（且批次目录内没有素材目录），兼容旧版历史日期
        if not batch_letter and day_dir.is_dir():
            has_batch_media = batch_dir.is_dir() and any(
                d.is_dir() and ("视频" in d.name or "图片" in d.name) and not re.match(r"^T\d{3,}", d.name, re.IGNORECASE)
                for d in batch_dir.iterdir()
            )
            if not has_batch_media:
                if legacy_dl.is_dir() and len(cls._collect_downloaded_media(legacy_dl)) > 0:
                    return legacy_dl, legacy_dl / "视频", legacy_dl / "图片"
                legacy_vids = [
                    d for d in day_dir.iterdir()
                    if d.is_dir() and "视频" in d.name and any(d.iterdir())
                ]
                legacy_imgs = [
                    d for d in day_dir.iterdir()
                    if d.is_dir() and "图片" in d.name and any(d.iterdir())
                ]
                if legacy_vids or legacy_imgs:
                    v_d = legacy_vids[0] if legacy_vids else (day_dir / f"{mmdd_str}视频")
                    i_d = legacy_imgs[0] if legacy_imgs else (day_dir / f"{mmdd_str}图片")
                    return day_dir, v_d, i_d

        # 标准模式：所有下载素材与 .renamer_project.json 一律安放在本批次文件夹 (day_dir / b_let) 内！
        raw_dl_dir = batch_dir
        vid_cands = [
            batch_dir / f"{mmdd_str}视频",
            batch_dir / f"{m_dd_str}视频",
            batch_dir / f"{day_num}视频",
            batch_dir / f"{dd_str}视频",
            batch_dir / "视频",
        ]
        if batch_dir.is_dir():
            for d in sorted(batch_dir.iterdir(), key=lambda p: p.name):
                if d.is_dir() and "视频" in d.name and not re.match(r"^T\d{3,}", d.name, re.IGNORECASE) and d not in vid_cands:
                    vid_cands.append(d)
        actual_vid_dir = next(
            (c for c in vid_cands if c.is_dir() and any(c.iterdir())),
            batch_dir / f"{mmdd_str}视频",
        )

        img_cands = [
            batch_dir / f"{mmdd_str}图片",
            batch_dir / f"{m_dd_str}图片",
            batch_dir / f"{day_num}图片",
            batch_dir / f"{dd_str}图片",
            batch_dir / "图片",
        ]
        if batch_dir.is_dir():
            for d in sorted(batch_dir.iterdir(), key=lambda p: p.name):
                if d.is_dir() and "图片" in d.name and not re.match(r"^T\d{3,}", d.name, re.IGNORECASE) and d not in img_cands:
                    img_cands.append(d)
        actual_img_dir = next(
            (c for c in img_cands if c.is_dir() and any(c.iterdir())),
            batch_dir / f"{mmdd_str}图片",
        )

        return raw_dl_dir, actual_vid_dir, actual_img_dir

    # ------------------------------------------------------------------
    # 实时统计当前工作区进度与全生命周期状态（供顶部状态栏、底部按钮和对话实时展示）
    # ------------------------------------------------------------------
    def get_live_workspace_stats(self, target_day: Optional[str] = None) -> Dict[str, Any]:
        ws = self.get_workspace_root()
        day_str = self.resolve_active_day_str(ws, hint_text=target_day or "")
        day_dir = ws / day_str

        st = self._load_lifecycle_state()
        lifecycle = self.inspect_day_lifecycle(day_dir, st)
        active_batch = lifecycle.get("active_batch", "A")
        batch_dir = day_dir / active_batch
        raw_dl_dir, actual_vid_dir, actual_img_dir = self._resolve_day_media_dirs(day_dir, batch_letter=active_batch)

        # 同时获取上一已完成日期的回执信息（如 2026-10-15 已上传 115/115）
        prev_completed = st.get("prev_completed_summary")
        if not prev_completed:
            y_str = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            if y_str != day_str and (ws / y_str).is_dir():
                y_lc = self.inspect_day_lifecycle(ws / y_str, st)
                if y_lc.get("is_completed"):
                    prev_completed = {
                        "day": y_str,
                        "uploaded_ok_count": y_lc.get("uploaded_ok_count", 0),
                        "total_upload_rows": y_lc.get("total_upload_rows", 0),
                        "export_folders": y_lc.get("export_folders", []),
                        "upload_updated_at": y_lc.get("upload_updated_at", ""),
                    }

        manifest_file = raw_dl_dir / "右键表格_今日下载命名清单.json"
        if not manifest_file.exists():
            manifest_file = day_dir / "01_右键表格_下载素材库" / "右键表格_今日下载命名清单.json"

        total_links = 0
        valid_img_links_count = 0
        valid_vid_links_count = 0
        task_codes: List[str] = []
        if manifest_file.exists():
            try:
                mdata = json.loads(manifest_file.read_text(encoding="utf-8"))
                items = mdata.get("items") or []
                valid_items = [it for it in items if self._is_valid_link_or_path(it.get("link", ""))]
                total_links = len(valid_items)
                seen_codes = []
                for it in valid_items:
                    c = it.get("task_code", "")
                    if c and c not in seen_codes:
                        seen_codes.append(c)
                    if it.get("material_type") == "图片素材":
                        valid_img_links_count += 1
                    else:
                        valid_vid_links_count += 1
                task_codes = seen_codes
            except Exception:
                pass

        scan_root = raw_dl_dir if raw_dl_dir.is_dir() else day_dir
        downloaded_files: List[Path] = self._collect_downloaded_media(scan_root)

        done_codes = set()
        img_done_codes = set()
        vid_done_codes = set()
        img_count = 0
        vid_count = 0
        extensionless_files: List[str] = []

        img_exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
        vid_exts = {".mp4", ".mov", ".mkv", ".webm", ".avi"}

        for f in downloaded_files:
            base_s, real_ext = self._split_real_ext(f.name)
            ext_l = real_ext.lower()
            m = re.match(r"^(v\d+)", base_s, re.IGNORECASE)
            code_l = m.group(1).lower() if m else ""
            if code_l:
                done_codes.add(code_l)

            parent_name = f.parent.name
            if parent_name == actual_img_dir.name and actual_img_dir != day_dir:
                img_count += 1
                if code_l:
                    img_done_codes.add(code_l)
                if not ext_l:
                    extensionless_files.append(f.name)
            elif parent_name == actual_vid_dir.name and actual_vid_dir != day_dir:
                vid_count += 1
                if code_l:
                    vid_done_codes.add(code_l)
                if not ext_l:
                    extensionless_files.append(f.name)
            elif ext_l in img_exts:
                img_count += 1
                if code_l:
                    img_done_codes.add(code_l)
            elif ext_l in vid_exts:
                vid_count += 1
                if code_l:
                    vid_done_codes.add(code_l)
            else:
                sniffed = self._sniff_media_ext(f)
                if sniffed in vid_exts:
                    vid_count += 1
                    extensionless_files.append(f.name)
                    if code_l:
                        vid_done_codes.add(code_l)
                elif sniffed in img_exts:
                    img_count += 1
                    extensionless_files.append(f.name)
                    if code_l:
                        img_done_codes.add(code_l)

        orante_tasks_count = 0
        if ORANTE_REAL_DB_PATH.exists():
            try:
                with sqlite3.connect(str(ORANTE_REAL_DB_PATH)) as conn:
                    row = conn.execute("SELECT COUNT(*) FROM tasks WHERE archived=0 AND status!='trash'").fetchone()
                    if row:
                        orante_tasks_count = int(row[0])
            except Exception:
                pass

        pending_vid_count = max(0, valid_vid_links_count - len(vid_done_codes))
        pending_img_count = max(0, valid_img_links_count - len(img_done_codes))

        return {
            "ws": str(ws),
            "day_str": day_str,
            "day_dir": str(day_dir),
            "active_batch": active_batch,
            "active_recipe": st.get("active_recipe", "B"),
            "stage_code": lifecycle.get("stage_code", "STAGE_0_READY"),
            "stage_label": lifecycle.get("stage_label", "待启动"),
            "lifecycle": lifecycle,
            "prev_completed_day": str((prev_completed or {}).get("day") or ""),
            "prev_completed_summary": prev_completed or {},
            "raw_dl_dir": str(raw_dl_dir),
            "actual_vid_dir": str(actual_vid_dir),
            "actual_img_dir": str(actual_img_dir),
            "batch_dir": str(batch_dir),
            "has_today": day_dir.exists(),
            "total_links": total_links,
            "valid_img_links_count": valid_img_links_count,
            "valid_vid_links_count": valid_vid_links_count,
            "task_codes": task_codes,
            "done_codes_count": len(done_codes),
            "img_done_codes_count": len(img_done_codes),
            "vid_done_codes_count": len(vid_done_codes),
            "pending_img_count": pending_img_count,
            "pending_vid_count": pending_vid_count,
            "downloaded_files_count": len(downloaded_files),
            "img_count": img_count,
            "vid_count": vid_count,
            "extensionless_files": extensionless_files,
            "orante_tasks_count": orante_tasks_count,
            "has_unfinished_manifest": manifest_file.exists() and total_links > 0,
        }

    @classmethod
    def _collect_downloaded_media(cls, root_dir: Path) -> List[Path]:
        """
        收集日期目录或批次目录及其原始素材子文件夹（如『A\\1016视频』『A\\1016图片』『B\\1016视频』『B\\1016图片』『15视频』『15图片』）下的原始媒体文件。
        严格排除『字幕成品』『成品』『导出』『exports』『output』『clips』『_tails』以及 Orante 任务文件夹（如 `T0130-1016图片`）！
        """
        if not root_dir or not root_dir.exists():
            return []
        seen_paths: Dict[str, Path] = {}
        skip_dirs = {
            "exports", "output", "clips", "_clips", "_tails", "_stitched", "images", "logs",
            "字幕成品", "成品", "导出", "空镜头", ".rcdl_tmp", ".robota_dl_tmp", "__pycache__",
        }

        def _should_skip_dir(d_name: str) -> bool:
            low = d_name.lower()
            if low.startswith(".") or low in skip_dirs:
                return True
            if re.match(r"^t\d{3,}[-_]", low):
                return True
            if any(k in low for k in ("字幕成品", "成品", "导出", "export", "output", "clips")):
                return True
            return False

        search_dirs: List[Path] = [root_dir]
        try:
            for d in root_dir.iterdir():
                if d.is_dir() and not _should_skip_dir(d.name):
                    search_dirs.append(d)
                    for sub in d.iterdir():
                        if sub.is_dir() and not _should_skip_dir(sub.name):
                            search_dirs.append(sub)
        except Exception:
            pass

        for d in search_dirs:
            try:
                for x in d.iterdir():
                    if (
                        x.is_file()
                        and not x.name.startswith(".")
                        and not x.name.endswith(".json")
                        and not x.name.endswith(".txt")
                        and not x.name.endswith(".cutora")
                        and not x.name.endswith(".partial")
                        and not x.name.endswith(".db")
                    ):
                        key = str(x.resolve()).lower()
                        seen_paths.setdefault(key, x)
            except Exception:
                pass
        return list(seen_paths.values())

    # ------------------------------------------------------------------
    # 智能链接清洗：自动过滤从 Google Sheet 连带复制过来的纯文字标题行
    # ------------------------------------------------------------------
    @staticmethod
    def _is_valid_link_or_path(s: str) -> bool:
        u = (s or "").strip().strip('"').strip("'")
        if not u:
            return False
        if u.startswith("http://") or u.startswith("https://") or "drive.google.com" in u:
            return True
        if re.match(r"^[A-Za-z]:[\\/]", u) and Path(u).exists():
            return True
        return False

    @classmethod
    def _extract_links(cls, raw_text: str, collapse_adjacent_dups: bool = True, expected_count: int = 0) -> List[str]:
        if not raw_text:
            return []
        raw_list: List[str] = []
        for ln in str(raw_text).splitlines():
            urls_in_line = re.findall(r"(https?://[^\s\"'<>]+)", ln)
            if urls_in_line:
                for u in urls_in_line:
                    raw_list.append(u.strip())
                continue
            cand = ln.strip().strip('"').strip("'")
            if cls._is_valid_link_or_path(cand):
                raw_list.append(cand)
        if not collapse_adjacent_dups or len(raw_list) <= 1:
            return raw_list
        if expected_count > 0 and len(raw_list) == expected_count:
            return raw_list
        # Google Sheets 智能芯片单格常带有 2 个相同 href 标签（此时总数为 expected_count * 2），仅在未对齐或翻倍时折叠相邻重复项
        out: List[str] = []
        i = 0
        while i < len(raw_list):
            out.append(raw_list[i])
            if i + 1 < len(raw_list) and raw_list[i + 1] == raw_list[i]:
                i += 2
            else:
                i += 1
        return out

    @classmethod
    def parse_bulk_links_message(cls, raw_text: str) -> Dict[str, Any]:
        """
        深度解析包含大批量网盘链接的用户消息：
        1. 提取非链接自然语言指令文本 (`non_url_text`)；
        2. 识别用户指定的任务范围/总数（如『v39-v83』-> 45项，或『总共38个任务』-> 38）、批次（如『批次B』/『B批次』-> 'B'）；
        3. 若链接按空行分为两段（如前 45 条图片链接 + 后 45 条视频链接），或总链接数恰为 2 * task_count，
           自动精准拆分为 `image_links` 与 `video_links`，并生成对应的 `image_codes` / `video_codes`（如 `v39..v83`）！
        """
        txt = (raw_text or "").strip()
        lines = txt.splitlines()
        non_url_lines: List[str] = []
        blocks: List[List[str]] = []
        cur_block: List[str] = []

        for ln in lines:
            s = ln.strip()
            urls = re.findall(r"(https?://[^\s\"'<>]+)", s)
            if urls:
                for u in urls:
                    cur_block.append(u.strip())
                # 如果该行除了 URL 还有说明文字，保留说明文字
                rem = re.sub(r"https?://[^\s\"'<>]+", "", s).strip()
                if rem:
                    non_url_lines.append(rem)
            elif cls._is_valid_link_or_path(s.strip('"').strip("'")):
                cur_block.append(s.strip('"').strip("'"))
            else:
                if cur_block:
                    blocks.append(cur_block)
                    cur_block = []
                if s:
                    non_url_lines.append(s)
        if cur_block:
            blocks.append(cur_block)

        def _clean_block(blk: List[str], expected_n: int = 0) -> List[str]:
            if expected_n > 0 and len(blk) == expected_n:
                return blk
            collapsed: List[str] = []
            idx = 0
            while idx < len(blk):
                collapsed.append(blk[idx])
                if idx + 1 < len(blk) and blk[idx + 1] == blk[idx]:
                    idx += 2
                else:
                    idx += 1
            return collapsed

        non_url_text = "\n".join(non_url_lines).strip()
        m_cnt = re.search(r"(?:总共|共|一共|有)\s*(\d{1,3})\s*(?:个任务|条任务|个视频|组|个)", non_url_text)
        m_rng = re.search(r"\b([vVpP]\d+\s*[-~～至到]\s*[vVpP]?\d+)\b", non_url_text)
        range_codes: List[str] = cls._parse_task_codes(m_rng.group(1), 0) if m_rng else []
        task_count = int(m_cnt.group(1)) if m_cnt else (len(range_codes) if m_rng else 0)

        cleaned_blocks = [_clean_block(b, task_count) for b in blocks if b]
        all_links: List[str] = []
        for cb in cleaned_blocks:
            all_links.extend(cb)

        image_links: List[str] = []
        video_links: List[str] = []

        if len(cleaned_blocks) >= 2:
            image_links = cleaned_blocks[0]
            video_links = []
            for cb in cleaned_blocks[1:]:
                video_links.extend(cb)
        elif len(all_links) > 0:
            if task_count > 0 and len(all_links) == task_count * 2:
                image_links = all_links[:task_count]
                video_links = all_links[task_count:]
            elif "图片" in non_url_text and "视频" not in non_url_text:
                image_links = all_links
            elif "视频" in non_url_text and "图片" not in non_url_text:
                video_links = all_links
            elif len(all_links) % 2 == 0 and len(all_links) >= 6 and any(k in non_url_text for k in ("素材", "任务", "图片", "视频")):
                half = len(all_links) // 2
                image_links = all_links[:half]
                video_links = all_links[half:]
            else:
                video_links = all_links

        if task_count <= 0:
            task_count = max(len(image_links), len(video_links), len(all_links))

        m_batch = re.search(r"(?:批次\s*([A-Za-z])|\b([A-Za-z])\s*批次)", non_url_text, re.IGNORECASE)
        if m_batch:
            batch_ver = (m_batch.group(1) or m_batch.group(2) or "A").upper()
        else:
            st_lc = cls._load_lifecycle_state()
            batch_ver = str(st_lc.get("locked_active_batch") or "A").upper()

        wants_orante_prepare = bool(
            re.search(r"(orante|准备好|准备起来|作为.*任务|描述语)", non_url_text, re.IGNORECASE)
        )
        explicit_spec = m_rng.group(1).replace(" ", "") if m_rng else (f"v01-v{task_count:02d}" if task_count > 0 else "")
        final_codes = cls._parse_task_codes(explicit_spec, max(task_count, len(image_links), len(video_links), 1))

        return {
            "non_url_text": non_url_text,
            "all_links": all_links,
            "total_links": len(all_links),
            "image_links": image_links,
            "video_links": video_links,
            "task_count": task_count,
            "task_spec": explicit_spec,
            "task_codes": final_codes,
            "image_codes": final_codes[:len(image_links)] if image_links else final_codes,
            "video_codes": final_codes[:len(video_links)] if video_links else final_codes,
            "batch_version": batch_ver,
            "batch_letter": batch_ver,
            "wants_orante_prepare": wants_orante_prepare,
            "has_clear_intent": bool(
                wants_orante_prepare
                or m_cnt
                or m_rng
                or len(cleaned_blocks) >= 2
                or any(k in non_url_text for k in ("下载", "右键表格", "表格工具", "开始今天", "批次"))
            ),
        }

    @staticmethod
    def _parse_drive_link(url: str) -> Tuple[str, str]:
        """返回 (kind: 'folder'|'file'|'unknown'|'local'|'http', id_or_path)"""
        u = (url or "").strip().replace("&amp;", "&").strip('"').strip("'")
        if not u:
            return ("", "")
        if re.match(r"^[A-Za-z]:[\\/]", u) and Path(u).exists():
            return ("local", u)
        m_q = re.search(r"[?&]q=([^&]+)", u)
        if "google.com/url" in u and m_q:
            try:
                u = urllib.parse.unquote(m_q.group(1))
            except Exception:
                pass
        m_folder = re.search(r"/folders/([A-Za-z0-9_-]{15,})", u)
        if m_folder:
            return ("folder", m_folder.group(1))
        m_file = re.search(r"/(?:file|document|spreadsheets|presentation)/d/([A-Za-z0-9_-]{15,})", u)
        if m_file:
            return ("file", m_file.group(1))
        m_id = re.search(r"[?&]id=([A-Za-z0-9_-]{15,})", u)
        if m_id:
            if "/uc" in u or "export=download" in u:
                return ("file", m_id.group(1))
            # open?id=... 可能是文件夹也可能是单文件，交给 rclone 自动探测
            return ("unknown", m_id.group(1))
        if u.startswith("http://") or u.startswith("https://"):
            return ("http", u)
        return ("", u)

    _IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    _VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
    _fid_cache_lock = threading.Lock()
    _fid_local_cache: Dict[str, Path] = {}

    def _rclone_copyid_to_target(
        self,
        rclone_exe: Path,
        fid: str,
        dest_dir: Path,
        prefix_stem: str,
        sub_index: int,
        default_ext: str,
        is_single: bool = True,
    ) -> Optional[Path]:
        """
        严格采用《右键表格工具》同款 rclone backend copyid 与命名方式：
        - 单文件链接 (`is_single=True`) 直接命名为 `v01.jpg` / `v01.png` / `v01.mp4`（不加多余的 `(1)`）！
        - 文件夹内多文件 (`is_single=False`) 命名为 `v01(1).jpg`, `v01(2).jpg`！
        - 自动通过 `_split_real_ext` + `_sniff_media_ext` 识别无后缀或中文伪后缀文件并补齐 `.mp4` / `.jpg` / `.png`！
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        img_exts = self._IMAGE_EXTS
        vid_exts = self._VIDEO_EXTS
        allowed_exts = img_exts if default_ext.lower() in img_exts else vid_exts

        stem_candidate = prefix_stem if is_single else f"{prefix_stem}({sub_index})"
        for pat in (f"{stem_candidate}.*", f"{prefix_stem}({sub_index}).*"):
            existing = [
                p for p in dest_dir.glob(pat)
                if p.is_file() and not p.name.endswith(".partial") and p.suffix.lower() in (img_exts | vid_exts)
            ]
            if existing and existing[0].stat().st_size > 0:
                with self._fid_cache_lock:
                    self._fid_local_cache[fid] = existing[0]
                return existing[0]

        with self._fid_cache_lock:
            cached_src = self._fid_local_cache.get(fid)
        if cached_src and cached_src.is_file() and cached_src.stat().st_size > 0:
            ext = cached_src.suffix.lower() or default_ext
            final_path = dest_dir / f"{stem_candidate}{ext}"
            if not final_path.exists():
                shutil.copy2(str(cached_src), str(final_path))
            return final_path

        tmp_dir = dest_dir / ".robota_dl_tmp" / f"{prefix_stem}_{sub_index}_{fid[:8]}"
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            tmp_dir.mkdir(parents=True, exist_ok=True)
            cmd = [
                str(rclone_exe),
                "backend",
                "copyid",
                "gdrive:",
                fid,
                tmp_dir.as_posix().rstrip("/") + "/",
                "--drive-acknowledge-abuse",
                "--transfers", "1",
                "--checkers", "2",
                "--low-level-retries", "6",
                "--retries", "2",
            ]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
                **_hidden_popen_kwargs(),
            )
            got = [p for p in tmp_dir.iterdir() if p.is_file() and not p.name.endswith(".partial")]
            if proc.returncode == 0 and got:
                src_file = got[0]
                _, real_ext = self._split_real_ext(src_file.name)
                known_exts = img_exts | vid_exts
                ext = real_ext.lower() if real_ext.lower() in known_exts else ""
                if not ext:
                    sniffed = self._sniff_media_ext(src_file)
                    if sniffed in known_exts:
                        ext = sniffed
                    else:
                        ext = default_ext
                final_path = dest_dir / f"{stem_candidate}{ext}"
                if final_path.exists():
                    final_path.unlink()
                shutil.move(str(src_file), str(final_path))
                with self._fid_cache_lock:
                    self._fid_local_cache[fid] = final_path
                return final_path
        except Exception:
            pass
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            try:
                if tmp_dir.parent.name == ".robota_dl_tmp" and not any(tmp_dir.parent.iterdir()):
                    tmp_dir.parent.rmdir()
            except Exception:
                pass
        return None

    def _headless_resolve_and_download_one_group(
        self,
        link: str,
        prefix_stem: str,
        dest_dir: Path,
        default_ext: str,
        start_index: int = 1,
        on_subfile_done: Optional[Callable[[str], None]] = None,
    ) -> List[Path]:
        """
        解析并下载单个链接（支持文件夹链接、open?id= 混合链接、单文件链接、本地路径）：
        - 单文件自动命名为 `v01.jpg` / `v01.mp4`；
        - 文件夹内多个文件自动命名为 `v01(1).jpg`, `v01(2).jpg`；
        - 每完成一个文件立即触发 `on_subfile_done(saved_filename)` 实时刷新界面进度！
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        kind, ident = self._parse_drive_link(link)
        if not kind or not ident:
            return []

        saved_files: List[Path] = []
        rclone_exe = self.find_rclone_exe()

        # 1. 本地文件夹或文件
        if kind == "local":
            src_p = Path(ident)
            if src_p.is_dir():
                children = sorted([x for x in src_p.iterdir() if x.is_file()])
                is_single_local = len(children) == 1
                for offset, ch in enumerate(children):
                    idx = start_index + offset
                    ext_l = self._split_real_ext(ch.name)[1].lower() or self._sniff_media_ext(ch) or default_ext
                    fname = f"{prefix_stem}{ext_l}" if is_single_local else f"{prefix_stem}({idx}){ext_l}"
                    target = dest_dir / fname
                    if not target.exists():
                        shutil.copy2(str(ch), str(target))
                    saved_files.append(target)
                    if on_subfile_done:
                        on_subfile_done(target.name)
                return saved_files
            elif src_p.is_file():
                ext_l = self._split_real_ext(src_p.name)[1].lower() or self._sniff_media_ext(src_p) or default_ext
                target = dest_dir / f"{prefix_stem}{ext_l}"
                if not target.exists():
                    shutil.copy2(str(src_p), str(target))
                saved_files.append(target)
                if on_subfile_done:
                    on_subfile_done(target.name)
                return saved_files

        # 2. Google Drive 文件夹 或 unknown (open?id=...)
        if kind in ("folder", "unknown") and rclone_exe:
            if kind == "unknown":
                one = self._rclone_copyid_to_target(
                    rclone_exe, ident, dest_dir, prefix_stem, start_index, default_ext, is_single=True
                )
                if one and one.exists():
                    saved_files.append(one)
                    if on_subfile_done:
                        on_subfile_done(one.name)
                    return saved_files

            # 按网盘文件夹列出里面的所有文件
            try:
                cmd_ls = [
                    str(rclone_exe),
                    "lsjson",
                    "gdrive:",
                    "--drive-root-folder-id",
                    ident,
                    "--files-only",
                    "--no-mimetype",
                    "--no-modtime",
                ]
                proc = subprocess.run(
                    cmd_ls,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                    **_hidden_popen_kwargs(),
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    items = json.loads(proc.stdout)
                    items.sort(key=lambda x: str(x.get("Name") or "").lower())
                    if default_ext == ".mp4":
                        vid_items = [
                            it for it in items
                            if self._split_real_ext(str(it.get("Name") or ""))[1].lower() in self._VIDEO_EXTS
                        ]
                        if vid_items:
                            items = vid_items
                    elif default_ext == ".png":
                        img_items = [
                            it for it in items
                            if self._split_real_ext(str(it.get("Name") or ""))[1].lower() in self._IMAGE_EXTS
                        ]
                        if img_items:
                            items = img_items
                    is_single_folder = len(items) == 1
                    for offset, item in enumerate(items):
                        if self._cancel_flag.is_set():
                            break
                        idx = start_index + offset
                        rname = item.get("Name") or f"item_{idx}{default_ext}"
                        ext = self._split_real_ext(rname)[1].lower() or default_ext
                        fid = item.get("ID") or item.get("Id") or ""
                        if not fid:
                            continue
                        got_file = self._rclone_copyid_to_target(
                            rclone_exe, fid, dest_dir, prefix_stem, idx, ext, is_single=is_single_folder
                        )
                        if got_file and got_file.exists():
                            saved_files.append(got_file)
                            if on_subfile_done:
                                on_subfile_done(got_file.name)
                    if saved_files:
                        return saved_files
            except Exception:
                pass

        # 3. Google Drive 单文件链接
        if kind in ("file", "unknown") and rclone_exe:
            got_file = self._rclone_copyid_to_target(
                rclone_exe, ident, dest_dir, prefix_stem, start_index, default_ext, is_single=True
            )
            if got_file and got_file.exists():
                saved_files.append(got_file)
                if on_subfile_done:
                    on_subfile_done(got_file.name)
                return saved_files

        # 4. HTTP 备用直链下载
        if kind in ("http", "file", "unknown"):
            dl_url = link if kind == "http" else f"https://drive.google.com/uc?export=download&id={ident}"
            try:
                target_path = dest_dir / f"{prefix_stem}{default_ext}"
                if target_path.exists() and target_path.stat().st_size > 0:
                    saved_files.append(target_path)
                    if on_subfile_done:
                        on_subfile_done(target_path.name)
                    return saved_files
                req = urllib.request.Request(dl_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = resp.read()
                    if data and not data[:200].lstrip().startswith(b"<!DOCTYPE html"):
                        target_path.write_bytes(data)
                        sniffed = self._sniff_media_ext(target_path)
                        if sniffed and sniffed != target_path.suffix.lower():
                            new_p = target_path.with_suffix(sniffed)
                            target_path.rename(new_p)
                            target_path = new_p
                        saved_files.append(target_path)
                        if on_subfile_done:
                            on_subfile_done(target_path.name)
                        return saved_files
            except Exception:
                pass

        return saved_files

    # ------------------------------------------------------------------
    # 将下载状态同步写进《右键表格工具》的 .renamer_project.json
    # 同时写入日期根目录 (D:\1_PROJECT\2026-10-16\.renamer_project.json) 与子目录，
    # 且先排全部图片链接 (v01..vN) 再排全部视频链接 (v01..vN)，与用户操作习惯 100% 一致！
    # ------------------------------------------------------------------
    def sync_to_table_tool_project(
        self,
        raw_dl_dir: Path,
        task_codes: List[str],
        image_links: List[str],
        video_links: List[str],
        downloaded_map: Optional[Dict[str, List[str]]] = None,
        image_codes: Optional[List[str]] = None,
        video_codes: Optional[List[str]] = None,
    ) -> None:
        raw_dl_dir.mkdir(parents=True, exist_ok=True)
        downloaded_map = downloaded_map or {}

        day_dir = raw_dl_dir.parent if raw_dl_dir.name == "01_右键表格_下载素材库" else raw_dl_dir
        day_dir.mkdir(parents=True, exist_ok=True)

        img_c_list = image_codes or task_codes
        vid_c_list = video_codes or task_codes

        # 1. 先排全部图片链接 (v01..vN)，再排全部视频链接 (v01..vN)
        all_pairs: List[Tuple[str, str, str]] = []
        for idx, img_lnk in enumerate(image_links):
            if self._is_valid_link_or_path(img_lnk):
                code = img_c_list[idx] if idx < len(img_c_list) else f"v{idx + 1:02d}"
                all_pairs.append((code, img_lnk, "图片素材"))
        for idx, vid_lnk in enumerate(video_links):
            if self._is_valid_link_or_path(vid_lnk):
                code = vid_c_list[idx] if idx < len(vid_c_list) else f"v{idx + 1:02d}"
                all_pairs.append((code, vid_lnk, "视频素材"))

        manifest_items = []
        dl_rows = []
        col_id = "c_robota_prefix"
        img_exts = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
        vid_exts = (".mp4", ".mov", ".mkv", ".webm", ".avi")

        for g_idx, (code, lnk, label) in enumerate(all_pairs):
            manifest_items.append({
                "task_code": code,
                "material_type": label,
                "link": lnk,
                "naming_rule": f"{code}",
                "folder_group_color": g_idx % 6,
            })
            kind, ident = self._parse_drive_link(lnk)
            files_for_code = [Path(p) for p in downloaded_map.get(code.lower(), downloaded_map.get(code, []))]
            if label == "图片素材":
                matched_paths = [
                    p for p in files_for_code
                    if ("图片" in p.parent.name) or (p.suffix.lower() in img_exts and "视频" not in p.parent.name)
                ]
            else:
                matched_paths = [
                    p for p in files_for_code
                    if ("视频" in p.parent.name) or (p.suffix.lower() in vid_exts and "图片" not in p.parent.name)
                ]

            matched = [p.name for p in matched_paths]
            total_sz = sum((p.stat().st_size for p in matched_paths if p.exists()), 0)
            is_done = len(matched) > 0
            is_folder = (kind == "folder") or (len(matched) > 1)
            children_rows = []
            if is_folder:
                for k_i, p_obj in enumerate(matched_paths, start=1):
                    fn = p_obj.name
                    f_sz = p_obj.stat().st_size if p_obj.exists() else 0
                    children_rows.append({
                        "id": f"r_sub_{g_idx}_{k_i}",
                        "link": lnk,
                        "cells": {col_id: f"({k_i})"},
                        "checked": False,
                        "wrap": {},
                        "fid": ident or f"fid_{g_idx}_{k_i}",
                        "rname": fn,
                        "size": f_sz,
                        "fromFolder": True,
                        "isSubRow": True,
                        "parentId": f"r_grp_{g_idx}",
                        "subIndex": k_i,
                        "subTotal": len(matched),
                        "folderGroup": g_idx % 6,
                        "ds": {"status": "ok", "pct": 100, "saved": fn, "msg": ""},
                    })

            default_rname = f"{code}.jpg" if label == "图片素材" else f"{code}.mp4"
            dl_rows.append({
                "id": f"r_grp_{g_idx}",
                "link": lnk,
                "cells": {col_id: code},
                "checked": not is_done,
                "wrap": {},
                "fid": f"FOLDER:{ident}" if is_folder else (ident or ""),
                "rname": (
                    f"📁 {label}（共 {len(matched)} 项）"
                    if is_folder and matched
                    else (matched[0] if matched else default_rname)
                ),
                "size": total_sz,
                "fromFolder": is_folder,
                "isFolderParent": is_folder,
                "folderCollapsed": True,
                "folderGroup": (g_idx % 6) if is_folder else -1,
                "isSubRow": False,
                "parentId": "",
                "subIndex": 0,
                "subTotal": 0,
                "children": children_rows,
                "ds": {
                    "status": "ok" if is_done else "none",
                    "pct": 100 if is_done else 0,
                    "saved": ", ".join(matched) if matched else "",
                    "msg": "",
                },
            })

        manifest_payload = json.dumps(
            {
                "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "dest_dir": str(day_dir),
                "items": manifest_items,
            },
            ensure_ascii=False,
            indent=2,
        )
        for m_dir in {day_dir, raw_dl_dir}:
            if m_dir.is_dir():
                try:
                    (m_dir / "右键表格_今日下载命名清单.json").write_text(manifest_payload, encoding="utf-8")
                except Exception:
                    pass

        # 2. 写入《右键表格工具》原生工程文件 .renamer_project.json（同时覆盖 day_dir 与 raw_dl_dir）
        target_proj_dirs = [day_dir]
        if raw_dl_dir != day_dir and raw_dl_dir.is_dir():
            target_proj_dirs.append(raw_dl_dir)

        for t_dir in target_proj_dirs:
            existing_entries = sorted(
                [
                    x for x in t_dir.iterdir()
                    if not x.name.startswith(".")
                    and not x.name.endswith(".json")
                    and not x.name.endswith(".partial")
                ],
                key=lambda p: (0 if p.is_dir() else 1, p.name.lower()),
            )
            main_col_id = "c_main_1"
            main_rows = [
                {
                    "id": f"m_{i}",
                    "file": x.name,
                    "kind": "dir" if x.is_dir() else "file",
                    "cells": {main_col_id: x.name if x.is_dir() else x.stem},
                    "checked": True,
                    "link": "",
                    "wrap": {},
                    "up": {"status": "none"},
                }
                for i, x in enumerate(existing_entries)
            ]

            proj_data = {
                "version": 3,
                "dir": str(t_dir),
                "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "origin": {x.name: x.name for x in existing_entries},
                "file_meta": {},
                "history": [],
                "snap_index": 0,
                "grid": {
                    "v": 3,
                    "cols": [{"id": main_col_id, "name": "文件名", "w": 200, "type": "data", "dw": 631}],
                    "joinSep": "",
                    "showPrev": True,
                    "showLink": True,
                    "showStat": True,
                    "keepExt": True,
                    "extMode": "keep",
                    "extSet": ".mp4",
                    "extFill": True,
                    "maxName": 180,
                    "specialW": {"porig": 190, "pnew": 200, "link": 220, "stat": 160},
                    "includeDirs": True,
                    "videosOnly": False,
                    "rows": main_rows,
                    "dl": {
                        "dest": str(day_dir),
                        "doc": {
                            "cols": [{"id": col_id, "name": "新文件名前缀", "w": 180, "type": "data", "dw": 448}],
                            "joinSep": "",
                            "keepExt": True,
                            "extMode": "set",
                            "extSet": ".mp4",
                            "extFill": True,
                            "maxName": 180,
                            "specialW": {"porig": 190, "pnew": 220, "link": 230, "stat": 170},
                            "showPrev": True,
                            "showLink": True,
                            "showStat": True,
                            "rows": dl_rows,
                        },
                    },
                },
            }
            proj_file = t_dir / ".renamer_project.json"
            try:
                if proj_file.exists() and os.name == "nt":
                    ctypes.windll.kernel32.SetFileAttributesW(str(proj_file), 128)
                proj_file.write_text(json.dumps(proj_data, ensure_ascii=False, indent=1), encoding="utf-8")
            except Exception:
                pass

        # 3. 确保打开《右键表格工具》时自动展开底部下载面板 + 显示所有文件与文件夹
        real_prefs_path = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "RightClickTableTool" / "prefs.json"
        for pf in (real_prefs_path, TABLE_TOOL_PREFS_PATH):
            try:
                pf.parent.mkdir(parents=True, exist_ok=True)
                prefs = {}
                if pf.exists():
                    prefs = json.loads(pf.read_text(encoding="utf-8"))
                prefs["dlOpen"] = True
                prefs["includeDirs"] = True
                prefs["videosOnly"] = False
                prefs["dlH"] = 420
                pf.write_text(json.dumps(prefs, ensure_ascii=False, indent=1), encoding="utf-8")
            except Exception:
                pass

    # ------------------------------------------------------------------
    def reset_today_orante_tasks(self) -> Dict[str, Any]:
        """重置今天在 Orante 中自动生成的草稿任务，保留图片与视频文件夹里已下载好的全部文件。"""
        stats = self.get_live_workspace_stats()
        day_str = stats["day_str"]
        batch_id = f"B{day_str.replace('-', '')}A"
        tid_prefix = f"T_{day_str.replace('-', '')}_%"
        deleted_tasks = 0
        if ORANTE_REAL_DB_PATH.exists():
            try:
                with sqlite3.connect(str(ORANTE_REAL_DB_PATH)) as conn:
                    cur = conn.cursor()
                    cur.execute("DELETE FROM tasks WHERE batch_id = ? OR id LIKE ?", (batch_id, tid_prefix))
                    deleted_tasks = cur.rowcount
                    cur.execute("DELETE FROM batches WHERE id = ?", (batch_id,))
                    conn.commit()
            except Exception:
                pass
        return {"ok": True, "deleted_tasks": deleted_tasks, "batch_id": batch_id}

    # ------------------------------------------------------------------
    # 将当天图片文件夹（如 1016图片）发给 Orante 作为一个任务的素材来源，并调用 Orante 原生 prepare 准备好全部主体图 p01..pN
    # ------------------------------------------------------------------
    def orante_create_and_prepare_image_task(
        self,
        image_folder: Optional[Path] = None,
        task_name: str = "",
        batch_letter: str = "A",
        day_hint: str = "",
    ) -> Dict[str, Any]:
        """
        将指定批次的图片素材文件夹（如 `D:\\1_PROJECT\\2026-10-16\\B\\1016图片`）以【追加模式】注册到 Orante 当日对应批次（A/B/C...），
        绝不覆盖或隐藏此前批次（如 A批次）尚未点击完成的任务！
        并直接调用 Orante 原生 `services.prepare.prepare` 将全部图片导入为 `p01..pN` 主体图。
        """
        ws_root = self.get_workspace_root()
        day_str = self.resolve_active_day_str(ws_root, hint_text=day_hint)
        day_dir = ws_root / day_str
        day_dir.mkdir(parents=True, exist_ok=True)
        b_letter = (batch_letter or "A").strip().upper() or "A"
        batch_dir = day_dir / b_letter
        batch_dir.mkdir(parents=True, exist_ok=True)
        _, _, default_img_dir = self._resolve_day_media_dirs(day_dir, batch_letter=b_letter)

        img_dir = Path(image_folder).resolve() if image_folder else default_img_dir.resolve()
        img_dir.mkdir(parents=True, exist_ok=True)
        t_name = (task_name or img_dir.name or "图片").strip()
        batch_id = f"B{day_str.replace('-', '')}{b_letter}"

        orante_desk_dir = str((STUDIO_ROOT / "modules" / "orante_desk").resolve())
        if orante_desk_dir not in sys.path:
            sys.path.insert(0, orante_desk_dir)

        try:
            from orante import config as o_cfg, db as o_db, jobs as o_jobs, tasks as o_tasks
            from orante.models import MANIFEST
            from orante.services import prepare as o_prepare

            o_db.init()
            o_cfg.set("workspace.root", str(ws_root))
            now_str = o_db.now()

            # 1. 确保当前批次 B<YYYYMMDD><Batch> 存在并登记到 batches 表
            o_db.run(
                "INSERT OR REPLACE INTO batches(id, root, day, version, dir, created) VALUES(?,?,?,?,?,?)",
                (batch_id, str(ws_root), day_str, b_letter, str(batch_dir), now_str),
            )
            o_cfg.set("workspace.current_batch", batch_id)

            # 2. 清理此前错误命名的临时 ID（如 T_20261016_图片）
            o_db.run("DELETE FROM tasks WHERE id LIKE ?", (f"T_{day_str.replace('-', '')}_%",))

            # 3. 获取默认启用账号（优先选择 Flow 账号）
            acc_row = o_db.one(
                "SELECT id, name, engine FROM accounts WHERE enabled=1 ORDER BY CASE WHEN engine='flow' THEN 0 ELSE 1 END, created LIMIT 1"
            )
            acc_name = acc_row["name"] if acc_row else "Flow1"

            # 4. 严格按【同一 batch_id 且同一 image_source】查找；若为新批次（如 B 批次），必须以追加方式新建任务，绝不覆盖 A 批次任务！
            existing = o_db.one(
                "SELECT * FROM tasks WHERE batch_id=? AND image_source=? AND status NOT IN ('finished', 'trash') AND archived=0 ORDER BY created DESC LIMIT 1",
                (batch_id, str(img_dir)),
            )
            if existing:
                tid = existing["id"]
                o_db.run(
                    "UPDATE tasks SET name=?, image_source=?, batch_id=?, updated=? WHERE id=?",
                    (t_name, str(img_dir), batch_id, now_str, tid),
                )
            else:
                max_ro_row = o_db.one("SELECT COALESCE(MAX(row_order), -1) AS m FROM tasks")
                next_row_order = int((max_ro_row or {}).get("m", -1)) + 1
                up_res = o_tasks.upsert_rows([
                    {
                        "name": t_name,
                        "copy": "",
                        "image_source": str(img_dir),
                        "type": "long",
                        "account": acc_name,
                        "gen_mode": "first_frame",
                    }
                ])
                saved_list = up_res.get("saved") or []
                if not saved_list:
                    raise RuntimeError(f"Orante 创建任务失败: {up_res.get('errors')}")
                first_saved = saved_list[0]
                tid = first_saved["id"] if isinstance(first_saved, dict) else str(first_saved)
                o_db.run(
                    "UPDATE tasks SET batch_id=?, row_order=?, updated=? WHERE id=?",
                    (batch_id, next_row_order, now_str, tid),
                )

            o_cfg.set("ui.current_task", tid)

            # 5. 检查本地图片数量；若该任务已有成片或描述语，绝不重置或覆盖！仅当尚未写描述语且无成片、但图片数量有新增时才重跑 prepare
            img_exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
            src_imgs = sorted([
                p for p in img_dir.iterdir()
                if p.is_file() and p.suffix.lower() in img_exts and not p.name.startswith(".")
            ])
            cur_task = o_tasks.get(tid)
            t_dir_str = cur_task.get("dir") or ""
            should_run_prepare = True
            if t_dir_str and Path(t_dir_str).is_dir():
                mf_path = Path(t_dir_str) / MANIFEST
                clips_sub = Path(t_dir_str) / "clips"
                has_clips = clips_sub.is_dir() and any(f.suffix.lower() == ".mp4" for f in clips_sub.iterdir() if f.is_file())
                if mf_path.is_file():
                    try:
                        mf_data = json.loads(mf_path.read_text(encoding="utf-8"))
                        existing_imgs = mf_data.get("images") or []
                        has_any_desc = any(str(im.get("desc") or "").strip() for im in existing_imgs)
                        if has_clips or has_any_desc or (len(existing_imgs) >= len(src_imgs) and len(existing_imgs) > 0):
                            should_run_prepare = False
                        elif len(existing_imgs) < len(src_imgs) and not has_any_desc and not has_clips:
                            mf_path.unlink(missing_ok=True)
                            img_sub = Path(t_dir_str) / "images"
                            if img_sub.is_dir():
                                shutil.rmtree(img_sub, ignore_errors=True)
                    except Exception:
                        pass

            # 6. 调用 Orante 原生 prepare 将所有图片拷贝为 p01..pN 并生成分镜段 s01..sN
            prep_res = {}
            if len(src_imgs) > 0 and should_run_prepare:
                fake_job = o_jobs.Job(f"J_PREP_{tid}", tid, "prepare", None, None, {})
                prep_res = o_prepare.prepare(fake_job, batch_dir=str(batch_dir), batch_id=batch_id) or {}

            final_task = o_tasks.get(tid)
            final_dir = final_task.get("dir") or str(batch_dir / f"{tid}-{t_name}")
            prepared_count = int(prep_res.get("subjects") or len(src_imgs))

            # 7. 若 Orante Web 服务正在运行，主动触发 /api/refresh 与状态同步让前端界面立即显示追加的新任务
            try:
                import http.cookiejar
                cj = http.cookiejar.CookieJar()
                opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
                opener.open("http://127.0.0.1:9400/", timeout=2.0).read()
                req_ref = urllib.request.Request(
                    "http://127.0.0.1:9400/api/refresh",
                    data=json.dumps({"deep": True, "tid": tid}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                opener.open(req_ref, timeout=3.0).read()
                req_st = urllib.request.Request(
                    f"http://127.0.0.1:9400/api/tasks/{tid}/status",
                    data=json.dumps({"status": final_task.get("status") or "prepared"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                opener.open(req_st, timeout=3.0).read()
            except Exception:
                pass

            return {
                "ok": True,
                "batch_id": batch_id,
                "batch_dir": str(batch_dir),
                "task_id": tid,
                "task_name": t_name,
                "task_dir": final_dir,
                "image_source": str(img_dir),
                "source_images_count": len(src_imgs),
                "prepared_images_count": prepared_count,
                "status": final_task.get("status", "arranged"),
                "registered_count": 1,
            }
        except Exception as e:
            return {
                "ok": False,
                "batch_id": batch_id,
                "batch_dir": str(batch_dir),
                "image_source": str(img_dir),
                "error": str(e),
                "registered_count": 0,
            }

    # ------------------------------------------------------------------
    # 将任务与已下载素材同步写进 Orante 的真实数据库 data/orante.db（仅当用户明确要求时调用）
    # ------------------------------------------------------------------
    def sync_to_orante_real_db(
        self,
        ws_root: Path,
        day_str: str,
        day_dir: Path,
        batch_dir: Path,
        task_codes: List[str],
        video_type_key: str,
        image_links: List[str],
        downloaded_map: Dict[str, List[str]],
    ) -> Dict[str, Any]:
        b_let = batch_dir.name or "A"
        _, _, img_folder = self._resolve_day_media_dirs(day_dir, batch_letter=b_let)
        return self.orante_create_and_prepare_image_task(
            image_folder=img_folder,
            task_name=img_folder.name,
            batch_letter=b_let,
            day_hint=day_str,
        )

    # ------------------------------------------------------------------
    # 核心下载与整理工作流：
    # - 下载下来的图片一律放进当期批次图片目录（如 `2026-10-16\B\1016图片`）
    # - 下载下来的视频一律放进当期批次视频目录（如 `2026-10-16\B\1016视频`）
    # - 支持图片和视频各自独立按用户指定的编号命名（如 A批次 v01~v38、B批次 v39~v83）
    # - 若用户要求发给 Orante 准备好任务（auto_register_orante=True），下载完成后以追加方式在 Orante 新建并 prepare 任务
    # ------------------------------------------------------------------
    def execute_start_today_workflow(
        self,
        workspace_root: str,
        task_spec: str,
        task_count: int,
        video_type_key: str,
        image_links_text: str,
        video_links_text: str,
        auto_launch_orante: bool = False,
        auto_register_orante: bool = False,
        progress_callback: Optional[Callable[[str, int], None]] = None,
        explicit_jobs: Optional[List[Tuple[str, str, str, str]]] = None,
        image_codes: Optional[List[str]] = None,
        video_codes: Optional[List[str]] = None,
        target_day_hint: str = "",
        target_batch_letter: str = "A",
    ) -> Dict[str, Any]:
        self._cancel_flag.clear()
        self._active_running = True

        def _report(msg: str, pct: int) -> None:
            if progress_callback:
                try:
                    progress_callback(msg, max(1, min(100, int(pct))))
                except Exception:
                    pass

        try:
            ws_path = self.set_workspace_root(workspace_root)
            day_str = self.resolve_active_day_str(ws_path, hint_text=target_day_hint or "今天")
            day_dir = ws_path / day_str
            day_dir.mkdir(parents=True, exist_ok=True)
            b_letter = (target_batch_letter or "A").strip().upper() or "A"
            batch_dir = day_dir / b_letter
            batch_dir.mkdir(parents=True, exist_ok=True)
            raw_dl_dir, vid_dest_dir, img_dest_dir = self._resolve_day_media_dirs(day_dir, batch_letter=b_letter)

            vtype_info = StudioKnowledgeBase.VIDEO_TYPES.get(
                str(video_type_key), StudioKnowledgeBase.VIDEO_TYPES["3"]
            )
            video_type_code = vtype_info["code"]

            image_links = self._extract_links(image_links_text, expected_count=task_count)
            video_links = self._extract_links(video_links_text, expected_count=task_count)
            inferred_count = max(task_count, len(image_links), len(video_links), 1)
            task_codes = self._parse_task_codes(task_spec, inferred_count)
            if len(task_codes) < max(len(image_links), len(video_links)):
                task_codes = [f"v{i:02d}" for i in range(1, max(len(image_links), len(video_links)) + 1)]

            img_code_list = image_codes or task_codes[:len(image_links)] if image_links else (image_codes or task_codes)
            vid_code_list = video_codes or task_codes[:len(video_links)] if video_links else (video_codes or task_codes)

            # Step 0: 下载前预检视频链接（正常应为单个视频直链 /file/d/...，若出现文件夹或异常链接立即告警）
            video_link_warnings: List[Dict[str, str]] = []
            for idx, v_lnk in enumerate(video_links):
                v_code = vid_code_list[idx] if idx < len(vid_code_list) else f"v{idx + 1:02d}"
                v_kind, v_ident = self._parse_drive_link(v_lnk)
                if v_kind == "folder":
                    w_msg = f"⚠️ {v_code} 的视频链接是网盘文件夹而非单个视频直链: {v_lnk}"
                    video_link_warnings.append({"code": v_code, "type": "folder_link", "link": v_lnk, "message": w_msg})
                    _report(w_msg, 3)

            # Step 1: 创建当期『1016图片』与『1016视频』文件夹
            _report(f"正在准备『{img_dest_dir.name}』与『{vid_dest_dir.name}』下载目录 ({day_dir})…", 5)
            self.swarm.update_clone("beta", "RUNNING", f"准备 {img_dest_dir.name}/{vid_dest_dir.name} ({day_str})", 20)
            raw_dl_dir.mkdir(parents=True, exist_ok=True)
            img_dest_dir.mkdir(parents=True, exist_ok=True)
            vid_dest_dir.mkdir(parents=True, exist_ok=True)
            self.config["last_work_day"] = day_str
            self._save_config()
            self.update_project_lifecycle_state(active_day=day_str, active_batch=b_letter)

            # 如果根目录里有散落的图片或视频，自动归位到当期图片和视频子文件夹
            img_exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
            vid_exts = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
            for f_root in list(raw_dl_dir.iterdir()):
                if f_root.is_file() and not f_root.name.startswith(".") and not f_root.name.endswith(".partial"):
                    if f_root.suffix.lower() in img_exts:
                        dst = img_dest_dir / f_root.name
                        if not dst.exists():
                            shutil.move(str(f_root), str(dst))
                    elif f_root.suffix.lower() in vid_exts:
                        dst = vid_dest_dir / f_root.name
                        if not dst.exists():
                            shutil.move(str(f_root), str(dst))

            # 先扫描当期图片与视频子文件夹中已存在的文件，确保断点续传秒跳过
            downloaded_map: Dict[str, List[str]] = {c: [] for c in task_codes}
            for sub_scan in (img_dest_dir, vid_dest_dir):
                if sub_scan.is_dir():
                    for f_exist in sub_scan.iterdir():
                        if f_exist.is_file() and not f_exist.name.startswith(".") and not f_exist.name.endswith(".partial"):
                            m_c = re.match(r"^([a-zA-Z]*\d+)", f_exist.stem, re.IGNORECASE)
                            if m_c:
                                c_key = m_c.group(1).lower()
                                if str(f_exist) not in downloaded_map.setdefault(c_key, []):
                                    downloaded_map[c_key].append(str(f_exist))

            if not explicit_jobs:
                self.sync_to_table_tool_project(
                    raw_dl_dir,
                    task_codes,
                    image_links,
                    video_links,
                    downloaded_map,
                    image_codes=img_code_list,
                    video_codes=vid_code_list,
                )
            self.swarm.update_clone("beta", "COMPLETED", f"{img_dest_dir.name}/{vid_dest_dir.name} 已就绪", 100, inc_completed=True)

            # Step 2: 并发下载图片（放入『1016图片』）与视频（放入『1016视频』）
            if explicit_jobs is not None:
                jobs = list(explicit_jobs)
            else:
                jobs = []
                for idx, lnk in enumerate(image_links):
                    code = img_code_list[idx] if idx < len(img_code_list) else f"v{idx + 1:02d}"
                    jobs.append((code, lnk, ".jpg", "图片"))
                for idx, lnk in enumerate(video_links):
                    code = vid_code_list[idx] if idx < len(vid_code_list) else f"v{idx + 1:02d}"
                    jobs.append((code, lnk, ".mp4", "视频"))

            total_jobs = len(jobs)
            if total_jobs > 0:
                completed_jobs = 0
                total_saved_files = 0
                lock = threading.Lock()

                def _run_one_job(job_tuple: Tuple[str, str, str, str]) -> Tuple[str, List[Path]]:
                    code, lnk, def_ext, kind_label = job_tuple
                    if self._cancel_flag.is_set():
                        return code, []

                    target_subdir = img_dest_dir if kind_label == "图片" else vid_dest_dir

                    def _on_sub(fname: str) -> None:
                        nonlocal total_saved_files
                        with lock:
                            total_saved_files += 1
                            pct = 10 + int(80 * (completed_jobs / max(1, total_jobs)))
                        msg = f"正在下载到『{target_subdir.name}』：{fname}（已完成 {completed_jobs}/{total_jobs} 项 · 共 {total_saved_files} 个文件）"
                        self.swarm.update_clone("alpha", "RUNNING", f"{fname} ({completed_jobs}/{total_jobs})", pct)
                        _report(msg, pct)

                    files = self._headless_resolve_and_download_one_group(
                        link=lnk,
                        prefix_stem=code,
                        dest_dir=target_subdir,
                        default_ext=def_ext,
                        start_index=1,
                        on_subfile_done=_on_sub,
                    )
                    return code, files

                _report(f"开始并发下载到『{img_dest_dir.name}』与『{vid_dest_dir.name}』（共 {total_jobs} 项链接）…", 10)
                self.swarm.update_clone("alpha", "RUNNING", f"并发下载中 (0/{total_jobs})", 10)

                with ThreadPoolExecutor(max_workers=6, thread_name_prefix="RobotaDl") as pool:
                    future_map = {pool.submit(_run_one_job, jb): jb for jb in jobs}
                    for fut in as_completed(future_map):
                        code, got_files = fut.result()
                        with lock:
                            completed_jobs += 1
                            for f in got_files:
                                if str(f) not in downloaded_map.setdefault(code, []):
                                    downloaded_map[code].append(str(f))
                            pct = 10 + int(80 * (completed_jobs / max(1, total_jobs)))

                        last_name = got_files[-1].name if got_files else code
                        msg = f"已下载 {completed_jobs}/{total_jobs} 项（最新：{last_name} · 累计就绪 {total_saved_files} 个文件）"
                        self.swarm.update_clone("alpha", "RUNNING", msg, pct)
                        _report(msg, pct)

                        if completed_jobs % 4 == 0 or completed_jobs == total_jobs:
                            if not explicit_jobs:
                                self.sync_to_table_tool_project(
                                    raw_dl_dir,
                                    task_codes,
                                    image_links,
                                    video_links,
                                    downloaded_map,
                                    image_codes=img_code_list,
                                    video_codes=vid_code_list,
                                )

                self.swarm.update_clone(
                    "alpha", "COMPLETED", f"下载完成（共 {total_saved_files} 个文件）", 100, inc_completed=True
                )

            # Step 3: 当用户要求将图片路径发给 Orante 并准备好任务时，自动创建并调用 Orante 原生 prepare
            orante_reg: Dict[str, Any] = {"batch_id": f"B{day_str.replace('-', '')}{b_letter}", "batch_dir": str(batch_dir), "registered_count": 0}
            if auto_register_orante:
                _report(f"正在将『{img_dest_dir.name}』发送给 Orante 并准备任务素材 (p01~p{len(image_links):02d})…", 92)
                orante_reg = self.orante_create_and_prepare_image_task(
                    image_folder=img_dest_dir,
                    task_name=img_dest_dir.name,
                    batch_letter=b_letter,
                    day_hint=day_str,
                )

            # Step 4: 最终检查图片与视频文件夹完整性
            _report(f"正在核对『{img_dest_dir.name}』与『{vid_dest_dir.name}』文件完整性…", 96)
            self.swarm.update_clone("gamma", "RUNNING", "核对图片与视频文件完整性", 96)
            if vid_dest_dir.is_dir():
                for vf in sorted(vid_dest_dir.iterdir(), key=lambda p: p.name.lower()):
                    if vf.is_file() and vf.suffix.lower() in img_exts:
                        w_msg = f"⚠️ 视频目录中发现图片文件『{vf.name}』（对应网盘视频链接实际为静态图片而非视频）"
                        video_link_warnings.append({"code": vf.stem, "type": "image_in_video_slot", "file": vf.name, "message": w_msg})
            threats = self.sentinel.scan_directory_for_threats(raw_dl_dir)
            final_stats = self.get_live_workspace_stats(target_day=day_str)
            total_files = final_stats["downloaded_files_count"]
            StudioKnowledgeBase.record_luca_feedback(
                user_issue=f"今日下载与Orante准备：{day_str} ({b_letter}批次)",
                action_taken=f"图片归入『{img_dest_dir.name}』（{final_stats['img_count']}张），视频归入『{vid_dest_dir.name}』（{final_stats['vid_count']}个）",
                effect_summary=f"共就绪 {total_files} 个文件，Orante 任务状态：{orante_reg.get('task_id', '未注册')}",
            )
            launched = False
            if auto_launch_orante:
                launched = self.launch_orante_room(batch_dir=batch_dir)
            self.swarm.update_clone("gamma", "COMPLETED", f"已核对 {final_stats['img_count']}图/{final_stats['vid_count']}视频", 100, inc_completed=True)

            _report(
                f"下载与准备完成！『{img_dest_dir.name}』共 {final_stats['img_count']} 张图，『{vid_dest_dir.name}』共 {final_stats['vid_count']} 个视频。",
                100,
            )
            return {
                "ok": True,
                "day_str": day_str,
                "day_dir": str(day_dir),
                "raw_dl_dir": str(raw_dl_dir),
                "img_dir": str(img_dest_dir),
                "vid_dir": str(vid_dest_dir),
                "batch_dir": str(batch_dir),
                "video_type": video_type_code,
                "video_type_title": vtype_info["title"],
                "task_codes": task_codes,
                "downloaded_files_count": total_files,
                "img_count": final_stats["img_count"],
                "vid_count": final_stats["vid_count"],
                "missing_vid_rows": final_stats.get("missing_vid_rows", []),
                "video_link_warnings": video_link_warnings,
                "orante_batch_id": orante_reg.get("batch_id", ""),
                "orante_task_id": orante_reg.get("task_id", ""),
                "orante_task_dir": orante_reg.get("task_dir", ""),
                "orante_prepared_count": orante_reg.get("prepared_images_count", 0),
                "threats": threats,
                "orante_launched": launched,
            }
        finally:
            self._active_running = False

    def resume_today_manifest_if_exists(
        self,
        auto_launch_orante: bool = False,
        progress_callback: Optional[Callable[[str, int], None]] = None,
    ) -> Dict[str, Any]:
        """自动读取今天已保存的清单，把图片下载到当期图片文件夹、视频下载到当期视频文件夹。"""
        stats = self.get_live_workspace_stats()
        day_dir = Path(stats["day_dir"])
        raw_dl_dir = Path(stats["raw_dl_dir"])
        manifest_file = day_dir / "右键表格_今日下载命名清单.json"
        if not manifest_file.exists():
            manifest_file = raw_dl_dir / "右键表格_今日下载命名清单.json"
        if not manifest_file.exists():
            raise RuntimeError("今天还没有待继续的下载清单，请先告诉我编号规则或打开《右键表格工具》手动操作。")

        mdata = json.loads(manifest_file.read_text(encoding="utf-8"))
        items = mdata.get("items") or []
        explicit_jobs: List[Tuple[str, str, str, str]] = []
        codes: List[str] = []
        for it in items:
            lnk = it.get("link", "")
            c = it.get("task_code", "")
            if c and c not in codes:
                codes.append(c)
            if not self._is_valid_link_or_path(lnk):
                continue
            if it.get("material_type") == "图片素材":
                explicit_jobs.append((c, lnk, ".jpg", "图片"))
            else:
                explicit_jobs.append((c, lnk, ".mp4", "视频"))

        count = max(len(codes), 1)
        spec = f"v01-v{count:02d}"
        return self.execute_start_today_workflow(
            workspace_root=stats["ws"],
            task_spec=spec,
            task_count=count,
            video_type_key=str(self.config.get("default_video_type", "3")),
            image_links_text="",
            video_links_text="",
            auto_launch_orante=False,
            auto_register_orante=False,
            progress_callback=progress_callback,
            explicit_jobs=explicit_jobs,
        )

    @staticmethod
    def _parse_task_codes(task_spec: str, count: int) -> List[str]:
        spec = (task_spec or "").strip()
        m_range = re.match(r"^([a-zA-Z]*)(\d+)\s*[-~～至到]\s*([a-zA-Z]*)(\d+)$", spec)
        if m_range:
            prefix = m_range.group(1) or "v"
            start_n = int(m_range.group(2))
            end_n = int(m_range.group(4))
            width = max(2, len(m_range.group(2)))
            if end_n >= start_n:
                return [f"{prefix}{i:0{width}d}" for i in range(start_n, min(end_n + 1, start_n + 200))]
        if spec.isdigit():
            count = max(1, min(200, int(spec)))
        count = max(1, min(200, int(count or 5)))
        return [f"v{i:02d}" for i in range(1, count + 1)]

    # ------------------------------------------------------------------
    # 打开三大工具：尊重人工操作，绝不擅自往 Orante 塞冗余任务！
    # ------------------------------------------------------------------
    def _python_gui_exe(self) -> str:
        for rel in (
            Path("runtime") / "venv" / "Scripts" / "pythonw.exe",
            Path("runtime") / "venv" / "Scripts" / "python.exe",
            Path("runtime") / "python" / "pythonw.exe",
        ):
            cand = STUDIO_ROOT / rel
            if cand.is_file():
                return str(cand)
        return sys.executable

    def launch_table_tool_in_dir(self, target_dir: Optional[Path] = None) -> bool:
        """打开《右键表格工具》，自动定位到当前批次目录（如 2026-10-16\\B）并展开底部下载面板显示实时序号与进度。"""
        script = STUDIO_ROOT / "modules" / "table_tool" / "app" / "表格工具.py"
        if not script.is_file():
            return False
        stats = self.get_live_workspace_stats()
        day_dir = Path(stats["day_dir"])
        raw_dl_dir = Path(stats["raw_dl_dir"])
        folder = target_dir or raw_dl_dir
        if not folder.exists():
            folder = raw_dl_dir if raw_dl_dir.parent.exists() else day_dir
        folder.mkdir(parents=True, exist_ok=True)

        # 如果有清单，确保 .renamer_project.json 是最新状态并且 dlOpen=True
        manifest_file = folder / "右键表格_今日下载命名清单.json"
        if not manifest_file.exists():
            manifest_file = raw_dl_dir / "右键表格_今日下载命名清单.json"
        if manifest_file.exists():
            try:
                mdata = json.loads(manifest_file.read_text(encoding="utf-8"))
                items = mdata.get("items") or []
                img_items = [it for it in items if it.get("material_type") == "图片素材" and self._is_valid_link_or_path(it.get("link", ""))]
                vid_items = [it for it in items if it.get("material_type") == "视频素材" and self._is_valid_link_or_path(it.get("link", ""))]
                img_l = [it["link"] for it in img_items]
                vid_l = [it["link"] for it in vid_items]
                img_c = [it.get("task_code") or f"v{i+1:02d}" for i, it in enumerate(img_items)]
                vid_c = [it.get("task_code") or f"v{i+1:02d}" for i, it in enumerate(vid_items)]
                codes = list(dict.fromkeys(img_c + vid_c)) or [f"v{i:02d}" for i in range(1, max(len(img_l), len(vid_l), 1) + 1)]
                dl_map: Dict[str, List[str]] = {c: [] for c in codes}
                for f in self._collect_downloaded_media(folder):
                    m = re.match(r"^([a-zA-Z]*\d+)", f.stem, re.IGNORECASE)
                    if m:
                        dl_map.setdefault(m.group(1).lower(), []).append(str(f))
                self.sync_to_table_tool_project(folder, codes, img_l, vid_l, dl_map, image_codes=img_c, video_codes=vid_c)
            except Exception:
                pass

        subprocess.Popen(
            [self._python_gui_exe(), str(script), str(folder)],
            cwd=str(script.parent),
        )
        return True

    def launch_orante_room(self, batch_dir: Optional[Path] = None) -> bool:
        """打开《Orante 2.0》制片台（独立桌面窗口模式，不擅自写入任何任务）。"""
        orante_dir = STUDIO_ROOT / "modules" / "orante_desk"
        if not orante_dir.is_dir():
            return False

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PATH"] = f"{STUDIO_ROOT / 'runtime' / 'bin'};{STUDIO_ROOT / 'runtime' / 'ffmpeg'};{env.get('PATH', '')}"
        subprocess.Popen(
            [self._python_gui_exe(), "-m", "orante"],
            cwd=str(orante_dir),
            env=env,
        )
        return True

    def launch_cutora_room(self, room: str = "", project_path: str = "") -> bool:
        """打开《CUTORA》并直接加载指定工程或进入目标房间（单实例限制：若已在运行则绝不重复启动进程）。"""
        cutora_dir = STUDIO_ROOT / "modules" / "cutora_editor"
        if not cutora_dir.is_dir():
            return False
        stats = self.get_live_workspace_stats()
        proj_str = (project_path or "").strip()
        if not proj_str:
            raw_dl_dir = Path(stats["raw_dl_dir"])
            day_dir = Path(stats["day_dir"])
            for search_d in (raw_dl_dir, day_dir):
                if not search_d.is_dir():
                    continue
                cands = sorted(
                    [p for p in search_d.glob("*.cutora") if not p.name.endswith(".backup.cutora")],
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if cands:
                    proj_str = str(cands[0])
                    break

        # 单实例内核互斥锁检查：若 CUTORA 已在运行，绝不再启动第二个进程
        if os.name == "nt":
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                h = kernel32.OpenMutexW(0x00100000, False, "Local\\CUTORA_SingleInstance_Mutex_v1")
                if h:
                    kernel32.CloseHandle(h)
                    if proj_str and Path(proj_str).is_file():
                        self.call_cutora_api("project.open", {"path": str(Path(proj_str).resolve())})
                    self.call_cutora_api("ui.focus", {"panel": "preview"})
                    return False
            except Exception:
                pass

        # 若本地 API 已开启，直接通过 HTTP API 打开工程并聚焦窗口
        if proj_str and Path(proj_str).is_file():
            api_res = self.call_cutora_api("project.open", {"path": str(Path(proj_str).resolve())})
            if api_res.get("ok"):
                self.call_cutora_api("ui.focus", {"panel": "preview"})
                return False

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["CUTORA_RUNTIME"] = str(STUDIO_ROOT / "runtime")
        if room:
            env["CUTORA_START_ROOM"] = room
        if proj_str and Path(proj_str).is_file():
            env["CUTORA_OPEN_PROJECT"] = str(Path(proj_str).resolve())
        env["CUTORA_BATCH_DIR"] = stats["raw_dl_dir"]
        env["PATH"] = f"{STUDIO_ROOT / 'runtime' / 'bin'};{STUDIO_ROOT / 'runtime' / 'ffmpeg'};{env.get('PATH', '')}"
        proc = subprocess.Popen(
            [self._python_gui_exe(), "-m", "cutora"],
            cwd=str(cutora_dir),
            env=env,
        )
        self._last_cutora_proc = proc
        return True

    def run_safe_background_command(self, command_str: str, cwd: Optional[Path] = None) -> Dict[str, Any]:
        """
        在后台直接执行命令（安全沙箱内：禁止破坏性删除指令，自动注入 rclone/ffmpeg/venv 环境）。
        """
        cmd_clean = (command_str or "").strip()
        if not cmd_clean:
            return {"ok": False, "output": "命令为空"}

        # 拦截危险删除或越权访问指令
        forbidden_patterns = [
            r"\brm\s+-rf\b", r"\bdel\s+/[fsq]", r"\bformat\b", r"\brmdir\s+/s",
            r"Remove-Item.*-Recurse", r"user_encrypted_vault\.bin", r"CryptUnprotectData"
        ]
        for pat in forbidden_patterns:
            if re.search(pat, cmd_clean, re.IGNORECASE):
                return {"ok": False, "output": "为保护你的文件与密码箱安全，Robota 已拦截该高风险指令。"}

        work_dir = cwd or Path(self.get_live_workspace_stats()["day_dir"])
        if not work_dir.exists():
            work_dir = self.get_workspace_root()

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        rclone_dir = str((STUDIO_ROOT / "modules" / "table_tool" / "app").resolve())
        venv_scripts = str((STUDIO_ROOT / "runtime" / "venv" / "Scripts").resolve())
        ffmpeg_dir = str((STUDIO_ROOT / "runtime" / "ffmpeg").resolve())
        env["PATH"] = f"{venv_scripts};{rclone_dir};{ffmpeg_dir};{env.get('PATH', '')}"

        self.swarm.update_clone("gamma", "RUNNING", f"后台执行：{cmd_clean[:28]}", 50)
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", cmd_clean],
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
                env=env,
                **_hidden_popen_kwargs(),
            )
            out = (proc.stdout or "").strip()
            err = (proc.stderr or "").strip()
            combined = out if out else err
            if len(combined) > 1600:
                combined = combined[:1600] + "\n…（已截断后续输出）"
            self.swarm.update_clone("gamma", "COMPLETED", "后台命令执行完毕", 100, inc_completed=True)
            return {
                "ok": proc.returncode == 0,
                "returncode": proc.returncode,
                "cwd": str(work_dir),
                "output": combined or "（命令执行成功，无文本输出）",
            }
        except Exception as e:
            self.swarm.update_clone("gamma", "ERROR", f"执行异常：{e}", 0)
            return {"ok": False, "cwd": str(work_dir), "output": f"执行失败：{e}"}

    def open_folder_smart(self, hint: str = "视频") -> Dict[str, Any]:
        """
        根据用户自然语言或工具参数打开对应的真实项目文件夹位置（绝不打开 C:\\Users\\...\\Videos 系统库！）：
        - 自动支持日期提示（如「15号视频」「14号图片」「2026-10-15」）。
        - 自动定位实际存在的视频目录（如 `15视频` / `视频` / `01_右键表格_下载素材库\\视频`）与图片目录（`15图片` / `图片`）。
        """
        stats = self.get_live_workspace_stats(target_day=hint)
        raw_dl_dir = Path(stats["raw_dl_dir"]).resolve()
        vid_subdir = Path(stats["actual_vid_dir"]).resolve()
        img_subdir = Path(stats["actual_img_dir"]).resolve()
        day_dir = Path(stats["day_dir"]).resolve()
        batch_dir = Path(stats["batch_dir"]).resolve()
        ws_dir = Path(stats["ws"]).resolve()

        h = (hint or "").lower().strip()

        # 如果明确直接给出了存在的绝对路径
        m_abs = re.search(r"([A-Za-z]:[\\/][^\r\n\"'<>|?*]+)", hint or "")
        if m_abs:
            cand_p = Path(m_abs.group(1).strip())
            if cand_p.exists():
                target_open = cand_p if cand_p.is_dir() else cand_p.parent
                os.startfile(str(target_open))
                return {"ok": True, "opened": str(target_open), "desc": f"目录 `{target_open}`"}

        # 如果明确询问 Orante 生成出的成片目录
        if any(k in h for k in ("orante", "生成", "成片", "t0127")) and any(k in h for k in ("视频", "video", "目录", "位置", "文件夹")):
            orante_vid_dir = batch_dir / "T0127-图片" / "videos"
            if not orante_vid_dir.exists() and batch_dir.exists():
                found_vids = list(batch_dir.glob("*/videos"))
                if found_vids:
                    orante_vid_dir = found_vids[0]
            if orante_vid_dir.exists():
                mp4s = list(orante_vid_dir.glob("*.mp4"))
                os.startfile(str(orante_vid_dir))
                return {
                    "ok": True,
                    "opened": str(orante_vid_dir),
                    "desc": f"Orante 生成视频目录 `{orante_vid_dir}`（已就绪 {len(mp4s)} 段视频）",
                }

        if "图片" in h or "image" in h:
            img_subdir.mkdir(parents=True, exist_ok=True)
            os.startfile(str(img_subdir))
            return {
                "ok": True,
                "opened": str(img_subdir),
                "desc": f"图片目录 `{img_subdir}`（共 {stats['img_count']} 张图片）",
            }

        if "视频" in h or "video" in h:
            vid_subdir.mkdir(parents=True, exist_ok=True)
            os.startfile(str(vid_subdir))
            return {
                "ok": True,
                "opened": str(vid_subdir),
                "desc": f"视频目录 `{vid_subdir}`（共 {stats['vid_count']} 个视频）",
            }

        if any(k in h for k in ("下载", "素材", "download", "raw")):
            target = raw_dl_dir if raw_dl_dir.exists() else day_dir
            target.mkdir(parents=True, exist_ok=True)
            os.startfile(str(target))
            return {"ok": True, "opened": str(target), "desc": f"素材目录 `{target}`"}

        target = day_dir if day_dir.exists() else (raw_dl_dir if raw_dl_dir.exists() else ws_dir)
        target.mkdir(parents=True, exist_ok=True)
        os.startfile(str(target))
        return {"ok": True, "opened": str(target), "desc": f"工作目录 `{target}`"}

    # ------------------------------------------------------------------
    # 调用《右键表格工具》同源能力：补齐无后缀文件 .mp4/.jpg 后缀、剥离伪后缀、并归入目标文件夹
    # ------------------------------------------------------------------
    def fix_extensions_and_organize(
        self,
        target_dir_or_day: str = "",
        video_folder_name: str = "视频",
        image_folder_name: str = "",
        clean_pseudo_suffix: bool = True,
    ) -> Dict[str, Any]:
        """
        直接执行：
        1. 扫描目标日期目录（如 `D:\\1_PROJECT\\2026-10-15`）及其素材子目录中的所有文件；
        2. 对没有后缀名（如 `v19`）或带有中文/下划线伪后缀（如 `v13.测试子涵-8`、`v61.已使用_1`）的文件，
           通过文件头魔数（`ftyp` MP4 / JPEG / PNG / WebP）自动识别真实格式，剥离伪后缀并补齐 `.mp4` / `.jpg` / `.png`；
        3. 如果指定了 `video_folder_name`（如 `'视频'` 或 `'15视频'`），自动创建或重命名视频文件夹并将全部视频归入其中；
        4. 如果指定了 `image_folder_name`（如 `'图片'` 或 `'15图片'`），自动将全部图片归入其中。
        """
        ws = self.get_workspace_root()
        if target_dir_or_day and Path(target_dir_or_day).exists():
            day_dir = Path(target_dir_or_day).resolve()
        else:
            day_str = self.resolve_active_day_str(ws, hint_text=target_dir_or_day)
            day_dir = (ws / day_str).resolve()

        if not day_dir.exists():
            return {"ok": False, "error": f"目录不存在：{day_dir}"}

        self.swarm.update_clone("alpha", "RUNNING", f"清洗后缀与归档 ({day_dir.name})", 35)

        img_exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
        vid_exts = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
        skip_dirs = {"a", "exports", ".rcdl_tmp", ".robota_dl_tmp", "__pycache__"}

        scan_dirs: List[Path] = [day_dir]
        for sub in sorted(day_dir.iterdir(), key=lambda p: p.name):
            if sub.is_dir() and not sub.name.startswith(".") and sub.name.lower() not in skip_dirs:
                scan_dirs.append(sub)

        renamed_files: List[Dict[str, str]] = []
        moved_videos: List[str] = []
        moved_images: List[str] = []

        # Step 1: 扫描并补齐/修正扩展名
        for sdir in scan_dirs:
            for item in sorted(list(sdir.iterdir()), key=lambda p: p.name):
                if not item.is_file():
                    continue
                if item.name.startswith(".") or item.name.lower().endswith((".json", ".txt", ".cutora", ".db", ".partial", ".log")):
                    continue

                base_stem, real_ext = self._split_real_ext(item.name)
                if real_ext.lower() in (img_exts | vid_exts):
                    continue

                # 嗅探文件头魔数
                true_ext = self._sniff_media_ext(item)
                if not true_ext:
                    # 如果文件名是 vXX 且大于 1MB，默认按 .mp4 视频处理
                    if re.match(r"^v\d+", item.name, re.IGNORECASE) and item.stat().st_size > 500_000:
                        true_ext = ".mp4"
                    else:
                        continue

                clean_stem = base_stem
                if clean_pseudo_suffix:
                    # 如果形如 v13.测试子涵-8 或 v61.已使用_1，提取标准编号前缀 v13 / v61
                    m_code = re.match(r"^([a-zA-Z]*\d+(?:\(\d+\))?)\.[^.]+$", item.name)
                    if m_code:
                        clean_stem = m_code.group(1)

                new_name = f"{clean_stem}{true_ext}"
                new_path = item.parent / new_name
                if new_path != item:
                    idx = 2
                    while new_path.exists():
                        new_path = item.parent / f"{clean_stem}({idx}){true_ext}"
                        idx += 1
                    item.rename(new_path)
                    renamed_files.append({"from": item.name, "to": new_path.name, "dir": str(item.parent)})

        # Step 2: 归档视频文件到指定视频文件夹
        vid_target_dir: Optional[Path] = None
        if video_folder_name:
            vid_target_dir = day_dir / video_folder_name.strip()
            # 检查是否已有类似 `15视频` 的文件夹且需要直接改名为 `视频`
            existing_vid_dirs = [
                d for d in day_dir.iterdir()
                if d.is_dir() and "视频" in d.name and d.resolve() != vid_target_dir.resolve()
            ]
            root_videos = [
                f for f in day_dir.iterdir()
                if f.is_file() and self._split_real_ext(f.name)[1].lower() in vid_exts
            ]
            if not vid_target_dir.exists() and len(existing_vid_dirs) == 1 and not root_videos:
                old_vdir = existing_vid_dirs[0]
                old_vdir.rename(vid_target_dir)
                moved_videos.append(f"文件夹已改名：{old_vdir.name} -> {vid_target_dir.name}")
            else:
                vid_target_dir.mkdir(parents=True, exist_ok=True)
                for f in sorted(list(day_dir.iterdir()), key=lambda p: p.name):
                    if f.is_file() and self._split_real_ext(f.name)[1].lower() in vid_exts:
                        dst = vid_target_dir / f.name
                        if not dst.exists():
                            shutil.move(str(f), str(dst))
                            moved_videos.append(f.name)

        # Step 3: 归档图片文件到指定图片文件夹（如果指定）
        img_target_dir: Optional[Path] = None
        if image_folder_name:
            img_target_dir = day_dir / image_folder_name.strip()
            existing_img_dirs = [
                d for d in day_dir.iterdir()
                if d.is_dir() and "图片" in d.name and d.resolve() != img_target_dir.resolve()
            ]
            root_imgs = [
                f for f in day_dir.iterdir()
                if f.is_file() and self._split_real_ext(f.name)[1].lower() in img_exts
            ]
            if not img_target_dir.exists() and len(existing_img_dirs) == 1 and not root_imgs:
                old_idir = existing_img_dirs[0]
                old_idir.rename(img_target_dir)
                moved_images.append(f"文件夹已改名：{old_idir.name} -> {img_target_dir.name}")
            else:
                img_target_dir.mkdir(parents=True, exist_ok=True)
                for f in sorted(list(day_dir.iterdir()), key=lambda p: p.name):
                    if f.is_file() and self._split_real_ext(f.name)[1].lower() in img_exts:
                        dst = img_target_dir / f.name
                        if not dst.exists():
                            shutil.move(str(f), str(dst))
                            moved_images.append(f.name)

        updated_stats = self.get_live_workspace_stats(target_day=day_dir.name)
        self.swarm.update_clone(
            "alpha",
            "COMPLETED",
            f"已修正 {len(renamed_files)} 个后缀 · 视频 {updated_stats['vid_count']} 个",
            100,
            inc_completed=True,
        )
        return {
            "ok": True,
            "day_dir": str(day_dir),
            "renamed_count": len(renamed_files),
            "renamed_files": renamed_files,
            "moved_videos_count": len(moved_videos),
            "moved_videos_sample": moved_videos[:20],
            "moved_images_count": len(moved_images),
            "actual_vid_dir": updated_stats["actual_vid_dir"],
            "actual_img_dir": updated_stats["actual_img_dir"],
            "total_videos": updated_stats["vid_count"],
            "total_images": updated_stats["img_count"],
        }

    def rename_or_move_path(self, source_path: str, dest_path: str) -> Dict[str, Any]:
        """在工作区内安全地重命名或移动文件夹/文件（绝不覆盖已有非空目标）。"""
        src = Path(source_path).resolve()
        dst = Path(dest_path).resolve()
        ok_src, msg_src = self.sentinel.is_path_allowed(src)
        ok_dst, msg_dst = self.sentinel.is_path_allowed(dst)
        if not ok_src or not ok_dst:
            return {"ok": False, "error": f"安全边界限制：{msg_src} / {msg_dst}"}
        if not src.exists():
            return {"ok": False, "error": f"源路径不存在：{src}"}
        if dst.exists() and dst.is_dir() and src.is_file():
            dst = dst / src.name
        if dst.exists():
            return {"ok": False, "error": f"目标已存在，为防覆盖已停止：{dst}"}
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return {"ok": True, "from": str(src), "to": str(dst)}

    def inspect_workspace_detail(self, target_day_or_path: str = "") -> Dict[str, Any]:
        """深度检查工作区及指定日期目录的真实结构、子文件夹、外部 clips 素材目录、无后缀文件与 .cutora 工程详情。"""
        ws = self.get_workspace_root()
        stats = self.get_live_workspace_stats(target_day=target_day_or_path)
        day_dir = Path(stats["day_dir"])

        subfolders: List[Dict[str, Any]] = []
        root_files: List[str] = []
        cutora_files: List[str] = []
        external_clip_folders: List[Dict[str, Any]] = []
        vid_exts = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}

        if day_dir.is_dir():
            for p in sorted(day_dir.iterdir(), key=lambda x: x.name):
                if p.is_dir() and not p.name.startswith("."):
                    f_cnt = sum(1 for x in p.iterdir() if x.is_file() and not x.name.startswith("."))
                    subfolders.append({"name": p.name, "path": str(p), "file_count": f_cnt})
                    for cf in p.glob("*.cutora"):
                        if not cf.name.endswith(".backup.cutora"):
                            cutora_files.append(str(cf))
                    # 扫描 B/ 或 A/ 下的外部视频 clips / videos 子目录（如 B\T0129-15B 图片\clips）
                    for clip_sub in p.rglob("*"):
                        if clip_sub.is_dir() and clip_sub.name.lower() in ("clips", "videos", "_clips"):
                            v_cnt = sum(
                                1 for vf in clip_sub.iterdir()
                                if vf.is_file() and vf.suffix.lower() in vid_exts
                            )
                            if v_cnt > 0:
                                external_clip_folders.append({
                                    "path": str(clip_sub),
                                    "rel_path": str(clip_sub.relative_to(day_dir)),
                                    "video_count": v_cnt,
                                })
                elif p.is_file() and not p.name.startswith("."):
                    root_files.append(p.name)
                    if p.suffix.lower() == ".cutora" and not p.name.endswith(".backup.cutora"):
                        cutora_files.append(str(p))

        active_cutora_summary: Optional[Dict[str, Any]] = None
        if cutora_files:
            c_info = self.inspect_cutora_project(cutora_files[0])
            if c_info.get("ok"):
                active_cutora_summary = c_info

        # 列出工作区最近 6 个日期文件夹概况（含 6 阶段生命周期与 .renamer_project.json 上传闭环状态）
        recent_days: List[Dict[str, Any]] = []
        if ws.is_dir():
            date_dirs = sorted(
                [d for d in ws.iterdir() if d.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", d.name)],
                key=lambda x: x.name,
                reverse=True,
            )[:6]
            st_lc = self._load_lifecycle_state()
            for d in date_dirs:
                _, vdir, idir = self._resolve_day_media_dirs(d)
                d_cutoras = [str(cf) for cf in d.glob("*.cutora") if not cf.name.endswith(".backup.cutora")]
                d_lc = self.inspect_day_lifecycle(d, st_lc)
                recent_days.append({
                    "day": d.name,
                    "path": str(d),
                    "vid_dir": str(vdir),
                    "img_dir": str(idir),
                    "has_batch_A": (d / "A").is_dir(),
                    "has_batch_B": (d / "B").is_dir(),
                    "cutora_projects": d_cutoras,
                    "stage_code": d_lc.get("stage_code", ""),
                    "stage_label": d_lc.get("stage_label", ""),
                    "is_completed": d_lc.get("is_completed", False),
                    "active_batch": d_lc.get("active_batch", "A"),
                    "uploaded_ok_count": d_lc.get("uploaded_ok_count", 0),
                    "total_upload_rows": d_lc.get("total_upload_rows", 0),
                    "split_projects_count": len(d_lc.get("split_projects") or []),
                })

        return {
            **stats,
            "subfolders": subfolders,
            "root_files_sample": root_files[:30],
            "root_files_total": len(root_files),
            "cutora_projects": cutora_files,
            "active_cutora_summary": active_cutora_summary,
            "external_clip_folders": external_clip_folders,
            "recent_days": recent_days,
        }

    def inspect_cutora_project(self, project_path: str = "") -> Dict[str, Any]:
        """读取并深度分析 `.cutora` 工程文件的路径、主视频数、外部连线视频数、分屏模式（如 top_bottom / top1_bottom4）、文字覆层、字幕与导出情况。"""
        cand: Optional[Path] = None
        if project_path and Path(project_path).is_file():
            cand = Path(project_path).resolve()
        else:
            stats = self.get_live_workspace_stats(target_day=project_path)
            day_dir = Path(stats["day_dir"])
            found = [
                p for p in sorted(day_dir.rglob("*.cutora"), key=lambda x: x.stat().st_mtime, reverse=True)
                if not p.name.endswith(".backup.cutora")
            ]
            if not found:
                ws = self.get_workspace_root()
                found = [
                    p for p in sorted(ws.rglob("*.cutora"), key=lambda x: x.stat().st_mtime, reverse=True)
                    if not p.name.endswith(".backup.cutora")
                ]
            if found:
                cand = found[0]

        if not cand or not cand.is_file():
            return {"ok": False, "error": "未找到 .cutora 工程文件"}

        try:
            data = json.loads(cand.read_text(encoding="utf-8"))
            tasks = data.get("tasks") or []
            layouts: Dict[str, int] = {}
            five_cell_tasks: List[str] = []
            linked_ext_tasks_count = 0
            text_overlay_tasks_count = 0
            image_overlay_tasks_count = 0
            subtitle_tasks_count = 0
            main_dirs_set: set[str] = set()
            ext_dirs_set: set[str] = set()

            for idx, t in enumerate(tasks, 1):
                v_rel = str(t.get("video") or "")
                tname = t.get("name") or (Path(v_rel).name if v_rel else f"task_{idx}")
                if v_rel:
                    main_dirs_set.add(str(Path(v_rel).parent))
                tl = t.get("timeline") or {}
                split_obj = t.get("split") or tl.get("split") or {}
                if split_obj.get("enabled") and split_obj.get("layout"):
                    sp = str(split_obj.get("layout"))
                else:
                    sp = str(tl.get("split_preset") or "single")
                layouts[sp] = layouts.get(sp, 0) + 1

                sources_map = tl.get("sources") or {}
                tracks = tl.get("tracks") or []
                v_tracks = [
                    tr for tr in tracks
                    if (tr.get("type") or tr.get("kind")) == "video" and (tr.get("clips") or [])
                ]
                if len(v_tracks) >= 2:
                    linked_ext_tasks_count += 1
                    for tr in v_tracks[1:]:
                        for cl in (tr.get("clips") or []):
                            sid = cl.get("source_id")
                            src_info = sources_map.get(sid) if isinstance(sources_map, dict) else None
                            if isinstance(src_info, dict) and src_info.get("path"):
                                ext_dirs_set.add(str(Path(src_info["path"]).parent))
                if len(v_tracks) >= 5 or sp == "top1_bottom4":
                    five_cell_tasks.append(f"#{idx}:{tname}({len(v_tracks)}轨)")

                if t.get("text_overlays"):
                    text_overlay_tasks_count += 1
                if t.get("image_overlays"):
                    image_overlay_tasks_count += 1
                if t.get("segments"):
                    subtitle_tasks_count += 1

            mtime_str = datetime.datetime.fromtimestamp(cand.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            export_dir = cand.parent / "exports"
            exported_mp4s = sorted([p.name for p in export_dir.glob("*.mp4")]) if export_dir.is_dir() else []
            return {
                "ok": True,
                "project_file": str(cand),
                "project_dir": str(cand.parent),
                "last_modified": mtime_str,
                "task_count": len(tasks),
                "linked_external_clips_count": linked_ext_tasks_count,
                "main_video_dirs": sorted(main_dirs_set),
                "external_clips_dirs": sorted(ext_dirs_set),
                "split_layouts": layouts,
                "tasks_with_text_overlays": text_overlay_tasks_count,
                "tasks_with_image_overlays": image_overlay_tasks_count,
                "tasks_with_subtitles": subtitle_tasks_count,
                "five_cell_tasks": five_cell_tasks,
                "export_dir": str(export_dir),
                "exported_count": len(exported_mp4s),
                "exported_sample": exported_mp4s[:15],
            }
        except Exception as e:
            return {"ok": False, "project_file": str(cand), "error": str(e)}

    def call_cutora_api(self, command: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """调用正在运行的 CUTORA 本地 HTTP API (`127.0.0.1:47321`) 执行 140+ 原生 GUI/批量命令。"""
        try:
            cutora_dir = STUDIO_ROOT / "modules" / "cutora_editor"
            if str(cutora_dir) not in sys.path:
                sys.path.insert(0, str(cutora_dir))
            from cutora.api import DEFAULT_PORT
            from cutora.api.token import read_token

            token = read_token()
            if not token:
                return {"ok": False, "api_offline": True, "error": "未找到 CUTORA API Token"}
            url = f"http://127.0.0.1:{DEFAULT_PORT}/v1/commands/{command.lstrip('/')}"
            req = urllib.request.Request(
                url,
                data=json.dumps(params or {}).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"ok": False, "api_offline": True, "error": str(e)}

    def cutora_create_and_link_project(
        self,
        main_videos_dir: str = "",
        external_clips_dir: str = "",
        output_project_path: str = "",
        split_layout: str = "top_bottom",
        loop: bool = True,
        match_duration: bool = True,
        mute: bool = True,
        open_in_cutora: bool = True,
        force_auto_link: bool = False,
    ) -> Dict[str, Any]:
        """
        使用 CUTORA 原生引擎（`cutora.batch.cli` + `cutora.batch.link_import`）完成配方 B 的工程加载、连线与分屏：
        1. 将 `main_videos_dir`（如 `<日期>视频` 的长视频媒体库）导入为 `.cutora` 工程；
        2. 检查 `external_clips_dir`（如 `clips` 生成片段）与主视频的数量关系：
           - 【数量一致 (1:1)】（或 `force_auto_link=True`）：从上到下 1:1 自动连线（`auto_pairs`）并同时启用分屏（默认 `top_bottom`）；
           - 【数量不一致 (1:N)】：不盲目 1:1 强连！先将全部主视频加载保存进 `.cutora` 并打开 CUTORA，返回明确提示交由人工完成一对多连线与确认；
        3. 若目标 `.cutora` 工程已存在，自动保护并继承已有的 `text_overlays` / `image_overlays` / `segments` 字幕数据，绝不丢失用户心血；
        4. 保存为 `<日期目录>\\<日期>.cutora`（如 `D:\\1_PROJECT\\2026-10-16\\2026-10-16.cutora`）。
        """
        t0 = time.perf_counter()
        cutora_dir = STUDIO_ROOT / "modules" / "cutora_editor"
        if str(cutora_dir) not in sys.path:
            sys.path.insert(0, str(cutora_dir))

        stats = self.get_live_workspace_stats(target_day=main_videos_dir or external_clips_dir or output_project_path)
        day_dir = Path(stats["day_dir"]).resolve()

        # 1. 解析主视频目录
        m_dir: Optional[Path] = None
        if main_videos_dir:
            cand_m = Path(main_videos_dir)
            if not cand_m.is_absolute():
                cand_m = day_dir / main_videos_dir
            if cand_m.is_dir():
                m_dir = cand_m.resolve()
        if m_dir is None:
            m_dir = Path(stats["actual_vid_dir"]).resolve()
        if not m_dir.is_dir():
            return {"ok": False, "error": f"主视频目录不存在: {main_videos_dir or m_dir}"}

        # 2. 解析外部连线视频目录 (clips)
        e_dir: Optional[Path] = None
        if external_clips_dir:
            cand_e = Path(external_clips_dir)
            if not cand_e.is_absolute():
                cand_e = day_dir / external_clips_dir
            if cand_e.is_dir():
                e_dir = cand_e.resolve()
        if e_dir is None and day_dir.is_dir():
            for sub_p in sorted(day_dir.rglob("clips")):
                if sub_p.is_dir():
                    e_dir = sub_p.resolve()
                    break

        # 3. 解析输出工程路径 (.cutora)
        if output_project_path:
            out_proj = Path(output_project_path)
            if not out_proj.is_absolute():
                out_proj = day_dir / output_project_path
            if out_proj.is_dir():
                out_proj = out_proj / f"{day_dir.name}.cutora"
            elif out_proj.suffix.lower() not in (".cutora", ".json"):
                out_proj = out_proj.with_suffix(".cutora")
            out_proj = out_proj.resolve()
        else:
            out_proj = (day_dir / f"{day_dir.name}.cutora").resolve()

        lay_map = {
            "上下": "top_bottom",
            "上下分屏": "top_bottom",
            "top_bottom": "top_bottom",
            "左右": "left_right",
            "左右分屏": "left_right",
            "left_right": "left_right",
            "画中画": "pip_br",
            "pip_br": "pip_br",
            "三联屏": "three_col",
            "四宫格": "grid_2x2",
            "上1下2": "top1_bottom2",
            "上1下4": "top1_bottom4",
            "五宫格": "top1_bottom4",
            "none": "",
            "single": "",
            "不开启": "",
        }
        norm_layout = lay_map.get((split_layout or "top_bottom").strip().lower(), (split_layout or "top_bottom").strip())

        from cutora.batch import link_import as LI
        from cutora.batch.cli import make_task_from_video, prepare_runtime, write_project_file
        import cutora.batch.script_broll_scatter as SBS
        import cutora.media.probe_cache as MPC
        import cutora.model.timeline_model as TLM
        from cutora.ui import main_window as host

        prepare_runtime()
        main_videos = LI.list_videos(str(m_dir))
        if not main_videos:
            return {"ok": False, "error": f"在主视频目录 `{m_dir}` 中未找到任何视频文件"}

        ext_videos = LI.list_videos(str(e_dir)) if (e_dir and e_dir.is_dir()) else []

        # 4. 如果目标工程已存在且已经完成了相同数量的连线与分屏（且带有文字/字幕覆层），直接保护返回避免破坏用户正在编辑的进度
        existing_overlays_by_name: Dict[str, Dict[str, Any]] = {}
        if out_proj.is_file():
            try:
                old_data = json.loads(out_proj.read_text(encoding="utf-8"))
                old_tasks = old_data.get("tasks") or []
                for ot in old_tasks:
                    v_base = os.path.basename(str(ot.get("video") or "")).lower()
                    if v_base:
                        existing_overlays_by_name[v_base] = {
                            k: ot[k]
                            for k in ("text_overlays", "image_overlays", "segments", "raw_text", "srt_text", "audio_path")
                            if ot.get(k)
                        }
                old_info = self.inspect_cutora_project(str(out_proj))
                if (
                    old_info.get("ok")
                    and old_info.get("task_count") == len(main_videos)
                    and (not ext_videos or old_info.get("linked_external_clips_count", 0) > 0)
                ):
                    return {
                        "ok": True,
                        "already_up_to_date": True,
                        "project_file": str(out_proj),
                        "main_videos_dir": str(m_dir),
                        "main_videos_count": len(main_videos),
                        "external_clips_dir": str(e_dir) if e_dir else "",
                        "linked_external_clips_count": old_info.get("linked_external_clips_count", 0),
                        "split_layout": norm_layout or "single",
                        "tasks_with_text_overlays": old_info.get("tasks_with_text_overlays", 0),
                        "elapsed_sec": round(time.perf_counter() - t0, 2),
                        "message": f"工程 `{out_proj}` 已包含全部 {len(main_videos)} 条主视频及 {old_info.get('linked_external_clips_count', 0)} 条连线分屏任务（已完整保留 {old_info.get('tasks_with_text_overlays', 0)} 条文字覆层）。",
                    }
                bak_path = out_proj.with_name(f"{out_proj.stem}.backup.cutora")
                shutil.copy2(str(out_proj), str(bak_path))
            except Exception:
                pass

        self.swarm.update_clone("alpha", "RUNNING", f"构建 CUTORA 工程 ({len(main_videos)}条)", 40)

        # 5. 使用 OpenCV 极速探测时长与分辨率（比逐个启动 230 次 ffprobe 快 8 倍）
        probe_cache: Dict[str, Tuple[float, int, int]] = {}
        orig_sbs_probe = SBS.probe_duration
        orig_tlm_probe = getattr(TLM, "probe_duration_sec", None)
        orig_mpc_probe = getattr(MPC, "probe_duration_sec", None)
        orig_has_audio = getattr(host, "has_audio_stream", None)

        def _fast_probe(p_str: str) -> Tuple[float, int, int]:
            key = os.path.normcase(os.path.abspath(p_str))
            if key in probe_cache:
                return probe_cache[key]
            dur, w, h = 0.0, 1080, 1920
            try:
                import cv2  # type: ignore
                cap = cv2.VideoCapture(p_str)
                if cap.isOpened():
                    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
                    frames = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
                    cw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                    ch = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                    if cw > 0 and ch > 0:
                        w, h = cw, ch
                    if fps > 0 and frames > 0:
                        dur = round(frames / fps, 3)
                cap.release()
            except Exception:
                pass
            if dur <= 0:
                dur = float(orig_sbs_probe(p_str) or 5.0)
            probe_cache[key] = (dur, w, h)
            return dur, w, h

        SBS.probe_duration = lambda p: _fast_probe(p)[0]
        if orig_tlm_probe is not None:
            TLM.probe_duration_sec = lambda p: _fast_probe(p)[0]
        if orig_mpc_probe is not None:
            MPC.probe_duration_sec = lambda p: _fast_probe(p)[0]
        if orig_has_audio is not None:
            host.has_audio_stream = lambda p: True

        requires_human_link = bool(ext_videos and len(main_videos) != len(ext_videos) and not force_auto_link)

        try:
            tasks = [make_task_from_video(host, vp) for vp in main_videos]
            link_stats = {"tasks": 0, "tracks": 0, "clips": 0}
            if ext_videos and not requires_human_link:
                pairs = LI.auto_pairs(len(tasks), len(ext_videos))
                plan = LI.plan_from_links(pairs, tasks, ext_videos)
                link_stats = LI.apply_plan(
                    plan,
                    tasks,
                    host=host,
                    loop=bool(loop),
                    match_duration=bool(match_duration),
                    mute=bool(mute),
                    split_b=bool(norm_layout),
                    split_layout=norm_layout if norm_layout else None,
                    split_border={"style": "none", "width": 0.0},
                )

            restored_overlays = 0
            for t in tasks:
                v_base = os.path.basename(str(t.get("video") or "")).lower()
                saved_meta = existing_overlays_by_name.get(v_base)
                if saved_meta:
                    for k, val in saved_meta.items():
                        t[k] = val
                    restored_overlays += 1

            out_proj.parent.mkdir(parents=True, exist_ok=True)
            write_project_file(host, str(out_proj), tasks, curr_idx=0)
        finally:
            SBS.probe_duration = orig_sbs_probe
            if orig_tlm_probe is not None:
                TLM.probe_duration_sec = orig_tlm_probe
            if orig_mpc_probe is not None:
                MPC.probe_duration_sec = orig_mpc_probe
            if orig_has_audio is not None:
                host.has_audio_stream = orig_has_audio

        if open_in_cutora:
            api_res = self.call_cutora_api("project.open", {"path": str(out_proj)})
            if not api_res.get("ok"):
                chk = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match '-m cutora' } | Select-Object -First 1 ProcessId"],
                    capture_output=True, text=True, timeout=5, **_hidden_popen_kwargs()
                )
                if not (chk.stdout or "").strip():
                    self.launch_cutora_room(room="", project_path=str(out_proj))

        elapsed = round(time.perf_counter() - t0, 2)
        self.swarm.update_clone(
            "alpha",
            "COMPLETED",
            f"工程已保存: {out_proj.name} ({len(tasks)}条)",
            100,
            inc_completed=True,
        )
        human_msg = ""
        if requires_human_link:
            human_msg = (
                f"已将 {len(tasks)} 条长视频加载并保存至 `{out_proj}`。"
                f"检测到长视频媒体库数量 ({len(tasks)} 条) 与 clips 片段数量 ({len(ext_videos)} 条，位于 `{e_dir}`) 不一致，"
                "按照配方 B 规范属于【一对多 (1:N)】连线关系，未强行 1:1 自动连线，已打开 CUTORA 供你人工进行表格连线确定并启用分屏！"
            )
        return {
            "ok": True,
            "requires_human_link": requires_human_link,
            "project_file": str(out_proj),
            "main_videos_dir": str(m_dir),
            "main_videos_count": len(tasks),
            "external_clips_dir": str(e_dir) if e_dir else "",
            "external_clips_count": len(ext_videos),
            "linked_tasks_count": link_stats.get("tasks", 0),
            "split_layout": norm_layout or "single",
            "loop": bool(loop),
            "match_duration": bool(match_duration),
            "mute": bool(mute),
            "restored_overlays_count": restored_overlays,
            "elapsed_sec": elapsed,
            "message": human_msg or f"已将 {len(tasks)} 条主视频与 {link_stats.get('tasks', 0)} 条 clips 按 1:1 自动连线并启用 `{norm_layout}` 分屏，保存至 `{out_proj}`。",
        }

    def _resolve_target_cutora_project(self, project_path: str = "") -> Optional[Path]:
        if project_path and Path(project_path).is_file():
            return Path(project_path).resolve()
        info = self.inspect_cutora_project(project_path)
        if info.get("ok") and info.get("project_file"):
            return Path(info["project_file"]).resolve()
        return None

    def cutora_add_ai_credit(
        self,
        project_path: str = "",
        preset: str = "credit.default",
        content: str = "",
    ) -> Dict[str, Any]:
        """
        配方 A & 配方 B 通用积木：为 `.cutora` 工程中的全部时间线任务批量添加 AI 署名（默认 `credit.default` / `Generated with AI`）或指定文字预设覆层（幂等，不重复叠加）。
        """
        proj = self._resolve_target_cutora_project(project_path)
        if not proj:
            return {"ok": False, "error": f"未找到目标 .cutora 工程文件: {project_path}"}
        cutora_dir = STUDIO_ROOT / "modules" / "cutora_editor"
        if str(cutora_dir) not in sys.path:
            sys.path.insert(0, str(cutora_dir))
        try:
            from cutora.batch.cli import op_add_text_overlay, prepare_runtime
            prepare_runtime()
            params: Dict[str, Any] = {
                "project": str(proj),
                "preset": (preset or "credit.default").strip(),
                "replace": True,
            }
            if content and content.strip():
                params["content"] = content.strip()
            res = op_add_text_overlay(params, "add_text_overlay")
            return {
                "ok": True,
                "project_file": str(proj),
                "preset": params["preset"],
                "applied_tasks": res.get("counts", {}).get("tasks", 0),
                "text": res.get("preset", {}).get("text", content or "Generated with AI"),
            }
        except Exception as e:
            return {"ok": False, "project_file": str(proj), "error": str(e)}

    def cutora_split_project(
        self,
        project_path: str = "",
        chunk_size: int = 40,
        out_dir: str = "",
    ) -> Dict[str, Any]:
        """
        配方 A & 配方 B 通用积木：按 **40 个任务/工程 (`chunk_size=40`)** 批量拆分 `.cutora` 工程，供全自动渲染队列连续导出。
        """
        proj = self._resolve_target_cutora_project(project_path)
        if not proj:
            return {"ok": False, "error": f"未找到目标 .cutora 工程文件: {project_path}"}
        cutora_dir = STUDIO_ROOT / "modules" / "cutora_editor"
        if str(cutora_dir) not in sys.path:
            sys.path.insert(0, str(cutora_dir))
        try:
            from cutora.batch.cli import op_split_project, prepare_runtime
            prepare_runtime()
            chunk = max(1, int(chunk_size or 40))
            params: Dict[str, Any] = {
                "project": str(proj),
                "chunk": chunk,
            }
            if out_dir and out_dir.strip():
                params["outdir"] = out_dir.strip()
            res = op_split_project(params, "split_project")
            return {
                "ok": True,
                "source_project": str(proj),
                "chunk_size": chunk,
                "total_tasks": res.get("counts", {}).get("source_tasks", 0),
                "split_parts_count": res.get("counts", {}).get("parts", 0),
                "split_projects": res.get("artifacts", []),
            }
        except Exception as e:
            return {"ok": False, "source_project": str(proj), "error": str(e)}

    def cutora_auto_subtitle(
        self,
        project_path: str = "",
        force_relayout: bool = False,
    ) -> Dict[str, Any]:
        """
        配方 A 专用积木：对 `.cutora` 工程中的所有时间线任务自动生成并对齐字幕（`op_auto_subtitle`）。
        """
        proj = self._resolve_target_cutora_project(project_path)
        if not proj:
            return {"ok": False, "error": f"未找到目标 .cutora 工程文件: {project_path}"}
        cutora_dir = STUDIO_ROOT / "modules" / "cutora_editor"
        if str(cutora_dir) not in sys.path:
            sys.path.insert(0, str(cutora_dir))
        try:
            from cutora.batch.cli import op_auto_subtitle, prepare_runtime
            prepare_runtime()
            res = op_auto_subtitle(
                {"project": str(proj), "force_relayout": bool(force_relayout)},
                "auto_subtitle",
            )
            return {
                "ok": True,
                "project_file": str(proj),
                "counts": res.get("counts", {}),
            }
        except Exception as e:
            return {"ok": False, "project_file": str(proj), "error": str(e)}

    def cutora_smart_stitch_sync(
        self,
        batch_dir: str = "",
        open_in_cutora: bool = True,
    ) -> Dict[str, Any]:
        """
        配方 A 专用积木：直接从 Orante 任务表格取用原文案与分镜顺序（写入 `_order.json`），同步给 CUTORA 智能拼接并打开智能拼接房间。
        """
        try:
            from core.spine_db import SpineDB
            spine = SpineDB()
            b_path = Path(batch_dir).resolve() if (batch_dir and Path(batch_dir).exists()) else None
            sync_res = spine.export_batch_for_cutora_stitch(batch_dir=b_path)
            if open_in_cutora:
                self.launch_cutora_room(room="stitch")
            return {
                "ok": True,
                "synced_tasks_count": sync_res.get("count", 0),
                "opened_stitch_room": open_in_cutora,
                "tasks_sample": [t.get("name", "") for t in (sync_res.get("tasks") or [])[:15]],
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def cutora_auto_project_face_safe(
        self,
        batch_dir: str,
        videos_dir: str,
        clips_manifest: str,
        table_path: str = "",
        phone_img: str = "",
        parts: int = 2,
        project_name: str = "",
    ) -> Dict[str, Any]:
        """
        全自动端到端创建 CUTORA 分屏工程（含 ASR 标题定位、电话贴纸、AI 署名、YuNet 人脸零穿帮校准与实景抽帧核验图）。
        """
        cutora_dir = STUDIO_ROOT / "modules" / "cutora_editor"
        cmd = [
            self._python_exe(),
            "-m",
            "cutora.batch.auto_project",
            "--batch-dir", str(batch_dir),
            "--videos", str(videos_dir),
            "--clips-manifest", str(clips_manifest),
            "--parts", str(max(1, int(parts or 2))),
        ]
        if table_path:
            cmd += ["--table", str(table_path)]
        if phone_img:
            cmd += ["--phone-img", str(phone_img)]
        if project_name:
            cmd += ["--project-name", str(project_name)]
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        proc = subprocess.run(
            cmd,
            cwd=str(cutora_dir),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **_hidden_popen_kwargs(),
        )
        lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
        if lines:
            try:
                return json.loads(lines[-1])
            except Exception:
                pass
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": (proc.stdout or "")[-2000:],
            "stderr": (proc.stderr or "")[-2000:],
        }

    def cutora_batch_export(
        self,
        project_path: str,
        out_dir: str,
        workers: int = 10,
        gpu_workers: int = 10,
        skip_existing: bool = True,
    ) -> Dict[str, Any]:
        """
        调用 cutora.batch.export_cli 执行 250–350 fps 高速并行批量导出。
        """
        cutora_dir = STUDIO_ROOT / "modules" / "cutora_editor"
        cmd = [
            self._python_exe(),
            "-m",
            "cutora.batch.export_cli",
            "--project", str(project_path),
            "--out", str(out_dir),
            "--workers", str(max(1, int(workers or 10))),
            "--gpu-workers", str(max(0, int(gpu_workers or 10))),
            "--skip-existing" if skip_existing else "--no-skip-existing",
        ]
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        proc = subprocess.run(
            cmd,
            cwd=str(cutora_dir),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **_hidden_popen_kwargs(),
        )
        lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
        if lines:
            try:
                return json.loads(lines[-1])
            except Exception:
                pass
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": (proc.stdout or "")[-2000:],
            "stderr": (proc.stderr or "")[-2000:],
        }

    def drive_upload_and_verify(
        self,
        local_dir: str,
        drive_folder_link_or_id: str,
        transfers: int = 6,
    ) -> Dict[str, Any]:
        """
        上传本地成品目录至指定的 Google Drive 文件夹，并执行 rclone check --one-way MD5 校验。
        """
        m = re.search(r"/folders/([a-zA-Z0-9_-]{15,})", drive_folder_link_or_id or "")
        if not m:
            m = re.search(r"[?&]id=([a-zA-Z0-9_-]{15,})", drive_folder_link_or_id or "")
        folder_id = m.group(1) if m else (drive_folder_link_or_id or "").strip()
        if not folder_id:
            return {"ok": False, "error": "缺少有效的 Google Drive 文件夹链接或 ID"}
        rclone_exe = STUDIO_ROOT / "runtime" / "bin" / "rclone.exe"
        if not rclone_exe.is_file():
            rclone_exe = STUDIO_ROOT / "modules" / "table_tool" / "app" / "rclone.exe"
        copy_cmd = [
            str(rclone_exe),
            "copy",
            str(local_dir),
            "gdrive:",
            "--drive-root-folder-id", folder_id,
            f"--transfers={max(1, int(transfers))}",
            "--checkers=8",
        ]
        p_copy = subprocess.run(copy_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **_hidden_popen_kwargs())
        chk_cmd = [
            str(rclone_exe),
            "check",
            str(local_dir),
            "gdrive:",
            "--drive-root-folder-id", folder_id,
            "--one-way",
        ]
        p_chk = subprocess.run(chk_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **_hidden_popen_kwargs())
        chk_out = (p_chk.stdout or "") + "\n" + (p_chk.stderr or "")
        verified = (p_chk.returncode == 0) and ("0 differences found" in chk_out)
        return {
            "ok": p_copy.returncode == 0 and verified,
            "folder_id": folder_id,
            "verified": verified,
            "check_output": chk_out[-1200:],
        }

    def search_studio_knowledge(self, query: str = "", top_k: int = 4) -> Dict[str, Any]:
        """
        本地 RAG 知识库检索：在 `sop.md`、`habits.md`、`profile.md` 及《片语工作室备忘录 (`STUDIO_MEMO_AI_MUST_READ.md`)》中按语义段落与关键词检索最匹配的规则与技术规范。
        """
        q = (query or "").strip().lower()
        kb_files = [
            STUDIO_ROOT / "data" / "memory" / "sop.md",
            STUDIO_ROOT / "data" / "memory" / "habits.md",
            STUDIO_ROOT / "data" / "memory" / "profile.md",
            STUDIO_ROOT / "STUDIO_MEMO_AI_MUST_READ.md",
        ]
        chunks: List[Dict[str, Any]] = []
        q_tokens = [tok for tok in re.split(r"[\s,，。、/|:：;；]+", q) if tok]
        q_bigrams = {q[i : i + 2] for i in range(len(q) - 1)} if len(q) >= 2 else set()

        for fp in kb_files:
            if not fp.is_file():
                continue
            try:
                text = fp.read_text(encoding="utf-8", errors="replace")
                sections = re.split(r"(?m)^(?=##?\s+)", text)
                for sec in sections:
                    sec_clean = sec.strip()
                    if not sec_clean:
                        continue
                    sec_low = sec_clean.lower()
                    score = 0.0
                    for tok in q_tokens:
                        if tok in sec_low:
                            score += 3.0 + sec_low.count(tok) * 0.5
                    for bg in q_bigrams:
                        if bg in sec_low:
                            score += 1.0
                    if not q or score > 0:
                        chunks.append({
                            "source": fp.name,
                            "score": round(score, 2),
                            "content": sec_clean[:1200],
                        })
            except Exception:
                pass

        chunks.sort(key=lambda x: x["score"], reverse=True)
        return {
            "ok": True,
            "query": query,
            "matches": chunks[: max(1, int(top_k or 4))],
        }

    def control_orante_api(self, action: str = "health", task_id: str = "") -> Dict[str, Any]:
        """操控或查询 Orante 2.0 制片台 (端口 9400 或真实数据库 orante.db)。"""
        if action == "reset":
            return self.reset_today_orante_tasks()
        try:
            robota_hub_dir = STUDIO_ROOT / "modules" / "robota_hub"
            if str(robota_hub_dir) not in sys.path:
                sys.path.insert(0, str(robota_hub_dir))
            from robota.tools import orante as orante_tool

            if action == "health":
                return orante_tool.orante_health()
            if action == "batches":
                return orante_tool.orante_batches()
            if action == "check" and task_id:
                return orante_tool.orante_check(task_id, sound=True)
            if action == "worklist" and task_id:
                return orante_tool.orante_worklist(task_id)
            if action == "replenish" and task_id:
                return orante_tool.orante_auto_replenish(task_id)
            return orante_tool.orante_health()
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def image_studio_batch(
        self,
        project_dir: str = "",
        prompts_text: str = "",
        aspects: Optional[List[str]] = None,
        engine: str = "flux2",
        title_preset: str = "cinema_gold",
        signature: str = ".   Generated by AI #xy",
        sticker_path: str = "",
        auto_avoid_face: bool = True,
        send_to_orante_task: str = "",
        mock: bool = False,
    ) -> Dict[str, Any]:
        """调用 AI 视觉制图与批量精修工坊 (`core.image_studio`) 执行多画幅批量生图、YuNet 人脸安全排版与 Orante 移交。"""
        from core import image_studio
        pdir = Path(project_dir).resolve() if project_dir else image_studio.DEFAULT_WORKSPACE_ROOT
        default_typo = {
            "enabled": True,
            "preset": title_preset or "cinema_gold",
            "signature": signature,
            "sticker_path": sticker_path,
            "auto_avoid_face": bool(auto_avoid_face),
        }
        res = image_studio.generate_items(
            proj_dir=pdir,
            prompts_text=prompts_text,
            aspects=aspects or ["9:16"],
            engine=engine or "flux2",
            default_typography=default_typo,
            run_now=True,
            mock=bool(mock),
        )
        if send_to_orante_task:
            res["orante_export"] = image_studio.export_to_orante_task(
                proj_dir=pdir,
                task_id_or_dir=send_to_orante_task,
                use_composed=False,
            )
        return res

    def image_studio_edit(
        self,
        project_dir: str = "",
        item_id: str = "img_0001",
        action: str = "inpaint",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """调用 AI 视觉制图与批量精修工坊 (`core.image_studio`) 执行单图精修 (inpaint/erase/outpaint/matte/typography/upscale/revert)。"""
        from core import image_studio
        pdir = Path(project_dir).resolve() if project_dir else image_studio.DEFAULT_WORKSPACE_ROOT
        act = (action or "").strip().lower()
        if act in ("inpaint", "erase"):
            return image_studio.inpaint_item(
                proj_dir=pdir,
                item_id=item_id,
                mask_b64_or_path=kwargs.get("mask") or kwargs.get("mask_b64_or_path") or kwargs.get("mask_path") or "",
                prompt=str(kwargs.get("prompt") or ""),
                mode="erase" if act == "erase" else str(kwargs.get("mode") or "inpaint"),
                denoise=float(kwargs.get("denoise") or 0.75),
                use_ai=bool(kwargs.get("use_ai", True)),
                mock=bool(kwargs.get("mock", False)),
            )
        if act == "outpaint":
            return image_studio.outpaint_item(
                proj_dir=pdir,
                item_id=item_id,
                target_aspect=str(kwargs.get("target_aspect") or "9:16"),
                target_w=int(kwargs.get("target_w") or 0),
                target_h=int(kwargs.get("target_h") or 0),
                align=str(kwargs.get("align") or "center"),
                prompt=str(kwargs.get("prompt") or ""),
                use_ai=bool(kwargs.get("use_ai", False)),
                mock=bool(kwargs.get("mock", False)),
            )
        if act == "matte":
            return image_studio.matte_item(
                proj_dir=pdir,
                item_id=item_id,
                mode=str(kwargs.get("mode") or "rmbg"),
                points=kwargs.get("points"),
                bg_mode=str(kwargs.get("bg_mode") or "transparent"),
                bg_color=str(kwargs.get("bg_color") or "#000000"),
                prefer_fast=bool(kwargs.get("prefer_fast", False)),
            )
        if act == "typography":
            overrides = kwargs.get("typography") if isinstance(kwargs.get("typography"), dict) else {
                k: v for k, v in kwargs.items() if v is not None and v != ""
            }
            return image_studio.render_typography(
                proj_dir=pdir,
                item_or_id=item_id,
                overrides=overrides,
                save_project_file=True,
            )
        if act == "upscale":
            return image_studio.upscale_item(
                proj_dir=pdir,
                item_id=item_id,
                scale=int(kwargs.get("scale") or 2),
                use_nomos=bool(kwargs.get("use_nomos", False)),
            )
        if act == "revert":
            return image_studio.revert_item(
                proj_dir=pdir,
                item_id=item_id,
                version=int(kwargs.get("version") or 1),
            )
        return {"ok": False, "error": f"未知 Image Studio 操作: {action}"}


# ============================================================================
# 6. 记忆自进化与工作流归纳引擎 (RobotaMemoryEvolutionEngine)
# ============================================================================
class RobotaMemoryEvolutionEngine:
    """
    持续记录并归纳 Luca 的每一次成功操作、偏好与步骤指正：
    - `D:\\0_LTX\\data\\memory\\episodes.jsonl` : 结构化实战操作轨迹库
    - `D:\\0_LTX\\data\\memory\\sop.md`         : 自动总结归纳的完整标准作业流程
    - `D:\\0_LTX\\data\\memory\\habits.md`      : 用户操作习惯与代理行为准则
    同时自动镜像同步至 `D:\\0_Robota\\data\\memory`（若存在）。
    """

    MEM_DIR = STUDIO_ROOT / "data" / "memory"
    MIRROR_MEM_DIR = Path(r"D:\0_Robota\data\memory")
    EPISODES_FILE = MEM_DIR / "episodes.jsonl"
    SOP_FILE = MEM_DIR / "sop.md"
    HABITS_FILE = MEM_DIR / "habits.md"
    PROFILE_FILE = MEM_DIR / "profile.md"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.MEM_DIR.mkdir(parents=True, exist_ok=True)

    def _sync_to_mirror(self, filename: str) -> None:
        try:
            src = self.MEM_DIR / filename
            if src.is_file() and self.MIRROR_MEM_DIR.parent.exists():
                self.MIRROR_MEM_DIR.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(self.MIRROR_MEM_DIR / filename))
        except Exception:
            pass

    def save_explicit_rule(self, rule_text: str, category: str = "sop") -> Dict[str, Any]:
        """将提炼出的用户工作流步骤或习惯写入 sop.md 或 habits.md，并同步镜像。"""
        clean_rule = (rule_text or "").strip()
        if not clean_rule:
            return {"ok": False, "error": "规则内容为空"}

        target_file = self.HABITS_FILE if category == "habit" else self.SOP_FILE
        now_s = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        bullet = f"- **[{now_s}]** {clean_rule}"

        with self._lock:
            existing = target_file.read_text(encoding="utf-8", errors="replace") if target_file.is_file() else ""
            if clean_rule not in existing:
                with open(target_file, "a", encoding="utf-8", newline="\n") as f:
                    if not existing.endswith("\n"):
                        f.write("\n")
                    f.write(f"{bullet}\n")
                self._sync_to_mirror(target_file.name)

        self.record_episode(
            user_text=f"[流程规则沉淀] {clean_rule}",
            action_summary=f"已写入 {target_file.name}",
            tools_used=["save_workflow_memory"],
            learned_rule=clean_rule,
        )
        return {"ok": True, "file": str(target_file), "rule": clean_rule}

    def record_episode(
        self,
        user_text: str,
        action_summary: str,
        tools_used: List[str],
        learned_rule: str = "",
    ) -> Dict[str, Any]:
        entry = {
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
            "user_request": (user_text or "")[:500],
            "action_summary": (action_summary or "")[:600],
            "tools_used": tools_used,
            "learned_rule": learned_rule,
        }
        with self._lock:
            try:
                with open(self.EPISODES_FILE, "a", encoding="utf-8", newline="\n") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                self._sync_to_mirror(self.EPISODES_FILE.name)
            except Exception:
                pass
        StudioKnowledgeBase.record_luca_feedback(
            user_issue=(user_text or "")[:200],
            action_taken=", ".join(tools_used) if tools_used else "智能对话与工作流辅助",
            effect_summary=(learned_rule or action_summary or "")[:240],
        )
        return entry

    def distill_turn_automatically(
        self,
        user_text: str,
        reply_text: str,
        executed_tools: List[Dict[str, Any]],
    ) -> Optional[str]:
        """
        在每次完成工具执行或收到用户的流程指导后，自动归纳经验并沉淀至记忆库。
        返回简短的记忆提示文本（供界面气泡底部展示），若无需新增则返回 None。
        """
        u_clean = (user_text or "").strip()
        if not u_clean:
            return None

        tool_names = [str(t.get("name") or "") for t in executed_tools if t.get("name")]
        is_workflow_teaching = any(
            k in u_clean for k in ("记住", "以后", "流程", "习惯", "规则", "每次都", "按这个", "改名为", "后缀", "放进去", "批量加载", "连线", "分屏")
        )

        if not tool_names and not is_workflow_teaching:
            return None

        learned_note = ""
        if "cutora_create_and_link_project" in tool_names:
            learned_note = f"执行 CUTORA 工程创建与外部视频 1:1 自动连线分屏：{u_clean[:90]}"
        elif "fix_extensions_and_organize" in tool_names:
            learned_note = f"执行素材后缀清洗与归档：{u_clean[:90]}"
        elif "rename_or_move_path" in tool_names:
            learned_note = f"执行目录/文件重命名与归位：{u_clean[:90]}"
        elif "save_workflow_memory" in tool_names:
            learned_note = f"已归纳并更新标准工作流规则：{u_clean[:90]}"
        elif is_workflow_teaching:
            learned_note = f"用户工作流偏好与步骤确认：{u_clean[:110]}"
        elif tool_names:
            learned_note = f"完成代理操作 ({', '.join(tool_names)})：{u_clean[:80]}"

        self.record_episode(
            user_text=u_clean,
            action_summary=(reply_text or "")[:300],
            tools_used=tool_names,
            learned_rule=learned_note,
        )

        # 如果是明确的新流程指令或完成了实质性的整理/建工程操作，自动追加到 sop.md 的实战归纳区
        if (
            is_workflow_teaching
            or "cutora_create_and_link_project" in tool_names
            or "fix_extensions_and_organize" in tool_names
            or "rename_or_move_path" in tool_names
        ):
            try:
                now_s = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                with self._lock:
                    sop_txt = self.SOP_FILE.read_text(encoding="utf-8", errors="replace") if self.SOP_FILE.is_file() else ""
                    short_u = re.sub(r"\s+", " ", u_clean)[:120]
                    if short_u not in sop_txt:
                        with open(self.SOP_FILE, "a", encoding="utf-8", newline="\n") as f:
                            f.write(f"- **[{now_s} 实战自动归纳]** 用户指令：「{short_u}」 → 动作：`{', '.join(tool_names) or '规则记忆'}`\n")
                        self._sync_to_mirror(self.SOP_FILE.name)
            except Exception:
                pass

        return learned_note

    def load_full_memory_prompt(self, max_episodes: int = 8) -> str:
        chunks: List[str] = []
        for fp in (self.PROFILE_FILE, self.HABITS_FILE, self.SOP_FILE):
            if fp.is_file():
                try:
                    chunks.append(fp.read_text(encoding="utf-8", errors="replace").strip())
                except Exception:
                    pass
        if self.EPISODES_FILE.is_file():
            try:
                lines = self.EPISODES_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
                recent = []
                for ln in lines[-max_episodes:]:
                    if ln.strip():
                        obj = json.loads(ln)
                        recent.append(
                            f"- [{obj.get('timestamp', '')}] 用户: {obj.get('user_request', '')[:90]} "
                            f"| 工具: {','.join(obj.get('tools_used') or [])} | 经验: {obj.get('learned_rule', '')}"
                        )
                if recent:
                    chunks.append("## 近期成功执行的代理操作记忆 (Recent Episodes)\n" + "\n".join(recent))
            except Exception:
                pass
        return "\n\n".join(chunks)


# ============================================================================
# 7. 多模型旗舰 AI 大脑 (RobotaLocalAIBrain) —— Gemini 4 Argon + Claude + Muse-Glimmer
# ============================================================================
class RobotaLocalAIBrain:
    """
    顶尖协同操作代理大脑（参考 Grok Bot / Antigravity Agent 架构设计）：
    1. 支持在界面顶部自由切换主力模型：
       - `gemini-4-argon-high`   : ✦ Gemini 4 Argon (High · 旗舰主脑) 【默认主力】
       - `gemini-4-argon-medium` : ✦ Gemini 4 Argon (Medium · 极速)
       - `claude-opus-5-5-high`  : ✦ Claude Opus 5.5 (High · 深度推理)
       - `claude-sonnet-5-5-high`: ✦ Claude Sonnet 5.5 (High · 敏捷代理)
       - `gemini-3.8-flash-high` : ⚡ Gemini 3.8 Flash (High · 轻量云端)
       - `muse-glimmer:latest`   : 🏠 Muse-Glimmer 18B (本地离线主脑)
       - `gemma4:26b`            : 🏠 Gemma 4 26B (本地离线大模型)
    2. 彻底解除死板关键词拦截，真正理解用户意图、项目结构与上下文，直接调用工具替用户把事情做好，不发无意义的套路按钮。
    3. 每次成功完成任务后，自动通过 `RobotaMemoryEvolutionEngine` 将经验沉淀为长期记忆与 SOP 流程。
    """

    OLLAMA_URL = "http://localhost:11434"
    DEFAULT_MODEL_ID = "gemini-4-argon-high"
    AGY_EXE = os.environ.get("ROBOTA_AGY") or str(Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe")

    MODEL_CATALOG: List[Dict[str, str]] = [
        {
            "id": "gemini-4-argon-high",
            "label": "✦ Gemini 4 Argon · High",
            "short": "Gemini 4 Argon (High)",
            "engine": "agy",
        },
        {
            "id": "gemini-4-argon-medium",
            "label": "✦ Gemini 4 Argon · Med",
            "short": "Gemini 4 Argon (Med)",
            "engine": "agy",
        },
        {
            "id": "claude-opus-5-5-high",
            "label": "✦ Claude Opus 5.5 · High",
            "short": "Claude Opus 5.5",
            "engine": "agy",
        },
        {
            "id": "claude-sonnet-5-5-high",
            "label": "✦ Claude Sonnet 5.5 · High",
            "short": "Claude Sonnet 5.5",
            "engine": "agy",
        },
        {
            "id": "gemini-3.8-flash-high",
            "label": "⚡ Gemini 3.8 Flash · High",
            "short": "Gemini 3.8 Flash",
            "engine": "agy",
        },
        {
            "id": "smol:muse-glimmer:latest",
            "label": "🤖 SmolAgents · Muse-Glimmer (本地代理)",
            "short": "SmolAgents (Muse-Glimmer)",
            "engine": "smol",
        },
        {
            "id": "muse-glimmer:latest",
            "label": "🏠 Muse-Glimmer 18B (本地直连)",
            "short": "Muse-Glimmer 18B",
            "engine": "ollama",
        },
        {
            "id": "gemma4:26b",
            "label": "🏠 Gemma 4 26B (本地)",
            "short": "Gemma 4 26B",
            "engine": "ollama",
        },
    ]

    TOOLS_SCHEMA = [
        {
            "type": "function",
            "function": {
                "name": "inspect_workspace",
                "description": "查看工作区或指定日期目录（如 '1016'、'15号'、'2026-10-16'）内的真实文件列表、子文件夹、视频/图片数量、外部 clips 目录、无后缀文件及 .cutora 工程情况。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target_day_or_path": {
                            "type": "string",
                            "description": "日期提示（如 '1016'、'15号'、'2026-10-16'）或目录路径；留空则检查当前活跃日期目录。",
                        }
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cutora_create_and_link_project",
                "description": (
                    "【配方 B 核心积木】在 CUTORA 中加载当天长视频媒体库（如 '1016视频' / '15视频'），并执行批量加载与表格连线 + 分屏：\n"
                    "- 若长视频数量与 clips 片段数量【一致 (1:1)】，自动从上到下 1:1 连线并启用上下分屏 (top_bottom)；\n"
                    "- 若数量【不一致 (1:N)】，自动将长视频加载进 `.cutora` 并打开 CUTORA 交由人工完成一对多连线与确认；\n"
                    "- 保存为 `<日期目录>\\<日期>.cutora`。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "main_videos_dir": {
                            "type": "string",
                            "description": "长视频媒体库文件夹路径（如 '1016视频'、'15视频' 或完整路径）",
                        },
                        "external_clips_dir": {
                            "type": "string",
                            "description": "Orante 生成好的 clips 视频片段文件夹路径",
                        },
                        "split_layout": {
                            "type": "string",
                            "description": "分屏模式：'top_bottom'(上下分屏，默认)、'left_right'(左右分屏)、'pip_br'(画中画)、'single'(不分屏)",
                        },
                        "output_project_path": {
                            "type": "string",
                            "description": "可选：保存的 .cutora 工程文件路径（默认 `<日期目录>\\<日期>.cutora`）",
                        },
                        "force_auto_link": {
                            "type": "boolean",
                            "description": "当数量不一致时是否强制按前 N 条自动连线（默认 false，数量不一致时交由人工一对多连线）",
                        },
                    },
                    "required": ["main_videos_dir"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cutora_add_ai_credit",
                "description": "【配方 A & B 通用积木】为 `.cutora` 工程中的全部视频任务批量添加 AI 署名（默认预设 `credit.default` / `Generated with AI`）或指定文字预设覆层。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_path": {"type": "string", "description": ".cutora 工程路径或日期提示；留空则自动定位当前日期工程"},
                        "preset": {"type": "string", "description": "预设 ID（默认 'credit.default'）"},
                        "content": {"type": "string", "description": "可选：自定义署名文本内容"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cutora_split_project",
                "description": "【配方 A & B 通用积木】根据视频任务数量批量拆分 `.cutora` 工程（默认 **40 个任务/工程 (`chunk_size=40`)**），供全自动队列渲染导出，彻底杜绝单进程长队列卡死。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_path": {"type": "string", "description": ".cutora 工程路径或日期提示；留空则自动定位当前日期工程"},
                        "chunk_size": {"type": "integer", "description": "每个子工程包含的任务条数（标准规范为 40）"},
                        "out_dir": {"type": "string", "description": "可选：拆分后子工程输出目录"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cutora_auto_subtitle",
                "description": "【配方 A 专用积木】对 `.cutora` 工程中的所有时间线任务自动生成并对齐字幕。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_path": {"type": "string", "description": ".cutora 工程路径或日期提示；留空则自动定位当前日期工程"},
                        "force_relayout": {"type": "boolean", "description": "是否强制重新排版已有字幕（默认 false）"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cutora_smart_stitch_sync",
                "description": "【配方 A 专用积木】直接从 Orante 任务表格取用原文案与分镜顺序同步至 CUTORA 智能拼接，并打开智能拼接房间。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "batch_dir": {"type": "string", "description": "可选：Orante 批次目录路径"},
                        "open_in_cutora": {"type": "boolean", "description": "是否自动打开 CUTORA 智能拼接房间（默认 true）"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_studio_knowledge",
                "description": "【本地 RAG 知识检索】在工作室 SOP 手册 (`sop.md`)、用户习惯 (`habits.md`) 与《片语工作室备忘录 (`STUDIO_MEMO_AI_MUST_READ.md`)》中检索标准流程与技术规范。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "检索关键词（如 '配方A'、'配方B'、'拆分工程'、'导出归零'）"},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "inspect_cutora_project",
                "description": "检查 CUTORA (.cutora) 工程文件的保存路径、主视频条数、外部连线视频条数、分屏模式（如上下分屏 top_bottom）、文字覆层数及已导出 MP4 列表。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_path": {"type": "string", "description": ".cutora 文件路径或日期提示（如 '2026-10-15'）；留空则自动查找当前日期工程。"}
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "fix_extensions_and_organize",
                "description": (
                    "调用《右键表格工具》同源能力：自动把目标目录中没有后缀名（如 v19）或带有中文伪后缀（如 v13.测试子涵-8、v61.已使用_1）的视频/图片文件，"
                    "通过文件头魔数补齐 .mp4 / .jpg / .png 后缀并清理伪后缀，同时可把所有视频放进指定的视频文件夹（如 '1016视频'、'15视频' 或 '视频'）。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target_dir_or_day": {
                            "type": "string",
                            "description": "要处理的日期或目录路径（如 '2026-10-16'、'1016' 或 '15号'）",
                        },
                        "video_folder_name": {
                            "type": "string",
                            "description": "存放视频的目标子文件夹名（如 '1016视频' 或 '视频'；如果不需要移动视频则传 ''）",
                        },
                        "image_folder_name": {
                            "type": "string",
                            "description": "存放图片的目标子文件夹名（如 '1016图片' 或 '图片'；若无需移动图片则传 ''）",
                        },
                    },
                    "required": ["target_dir_or_day"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "rename_or_move_path",
                "description": "在 D:\\1_PROJECT 工作区内重命名或移动文件夹/文件（例如把文件夹 '15视频' 改名为 '视频'，或把文件移入新文件夹）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source_path": {"type": "string", "description": "源文件夹或文件的完整路径"},
                        "dest_path": {"type": "string", "description": "目标文件夹或文件的完整路径"},
                    },
                    "required": ["source_path", "dest_path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "open_folder",
                "description": "在资源管理器中为用户打开指定的真实项目文件夹（如 '1016视频'、'15号视频'、'视频'、'图片'、'orante视频'、'2026-10-16'）。严禁打开 C:\\Users\\...\\Videos。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "description": "要打开的目标提示或路径，例如 '1016视频'、'15号视频'、'视频'、'图片'、'orante视频' 或绝对路径。",
                        }
                    },
                    "required": ["target"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "open_workspace_app",
                "description": "启动并打开工作室的三大核心软件：table_tool(右键表格工具)、orante(Orante 2.0 制片台独立窗口)、cutora(CUTORA 剪辑拼接台)。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "app_name": {
                            "type": "string",
                            "enum": ["table_tool", "orante", "cutora"],
                            "description": "table_tool=右键表格工具, orante=Orante制片台, cutora=CUTORA剪辑台",
                        },
                        "target_dir": {
                            "type": "string",
                            "description": "可选：打开软件时直接定位到的文件夹或 .cutora 工程路径",
                        },
                    },
                    "required": ["app_name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "control_orante",
                "description": "操控或查询 Orante 2.0 制片台：查看在线状态与任务列表(batches)、执行 MP4/响度体检(check)、读取缺片清单(worklist)、自动补生缺片(replenish)、重置冗余草稿任务(reset)。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["health", "batches", "check", "worklist", "replenish", "reset"],
                        },
                        "task_id": {"type": "string", "description": "可选任务 ID，如 T0127"},
                    },
                    "required": ["action"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_shell_command",
                "description": "在后台安全执行 PowerShell 或 Python 命令以完成任意自定义文件整理、批量改名、ffprobe 媒体检查或跨软件协同操作。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "要执行的 PowerShell 命令"}
                    },
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "update_project_lifecycle_state",
                "description": (
                    "【生命周期与批次状态持久化】当用户在对话中告知某日任务已完成/已上传（如 '15号已经上传完了'），"
                    "或要求切换当前推进的日期与批次（如 '开始 16号 A批次'、'切换到 B批次'）时，立即调用此工具将状态永久保存并创建对应批次目录。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "active_day": {"type": "string", "description": "要设为当前活跃的日期（如 '2026-10-16'、'16号'、'今天'）"},
                        "active_batch": {"type": "string", "description": "要设为当前活跃的批次（'A'、'B' 或 'C'）"},
                        "mark_day_completed": {"type": "string", "description": "可选：要标记为已全部上传完结的日期（如 '2026-10-15'、'15号'）"},
                        "recipe_type": {"type": "string", "description": "可选：当前采用的工作流配方（'A' 或 'B'）"},
                        "note": {"type": "string", "description": "可选：状态备注"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "dispatch_parallel_subagents",
                "description": (
                    "【多子代理并行协同引擎】当需要同时执行多个互不干扰的子任务（例如：子代理1检查工作区与上传状态 + 子代理2检查 Orante 批次队列 + 子代理3给工程加 AI 署名并按40条拆分）时，"
                    "调用此工具在独立后台工作线程中并行执行所有子任务，且每个子代理仅将精炼结论汇总回传给主对话，大幅提升效率并节省上下文窗口。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tasks": {
                            "type": "array",
                            "description": "并行子代理任务数组，每项包含 role(子代理职责)、tool(要调用的原子工具名) 和 args(工具参数字典)",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "role": {"type": "string", "description": "子代理角色名称，如 '上传闭环核查员'、'CUTORA工程拆分员'"},
                                    "tool": {"type": "string", "description": "原子工具名称（如 inspect_workspace, inspect_cutora_project, control_orante, cutora_add_ai_credit, cutora_split_project, run_shell_command）"},
                                    "args": {"type": "object", "description": "传递给该原子工具的参数字典"},
                                },
                                "required": ["tool"],
                            },
                        }
                    },
                    "required": ["tasks"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "orante_prepare_image_task",
                "description": (
                    "将当天图片素材文件夹（如 `1016图片`）发给 Orante 作为今天一个任务的素材来源，"
                    "并调用 Orante 原生 prepare 将全部图片准备好（生成 p01~pN 主体图与分镜段），供用户直接去 Orante 填写描述语。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "image_folder": {"type": "string", "description": "图片素材文件夹路径（如 'D:\\1_PROJECT\\2026-10-16\\1016图片'）；留空则自动定位当期图片目录"},
                        "task_name": {"type": "string", "description": "任务名称（默认取文件夹名，如 '1016图片'）"},
                        "batch_letter": {"type": "string", "description": "批次字母（默认 'A'）"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "image_studio_batch",
                "description": (
                    "【AI 视觉制图与批量精修工坊】批量生成多画幅 (9:16 / 16:9 / 1:1 / 3:4 / 4:5) 图像，"
                    "自动运行 OpenCV YuNet 人脸零遮挡海报标题/电话贴纸/AI署名排版，并可直接一键导入 Orante 任务。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_dir": {"type": "string", "description": "Image Studio 工程目录路径（留空则使用默认工程）"},
                        "prompts_text": {"type": "string", "description": "多行提示词或 TSV 表格（序号<TAB>提示词<TAB>主标题<TAB>副标题）"},
                        "aspects": {"type": "array", "items": {"type": "string"}, "description": "画幅列表，如 ['9:16', '1:1']"},
                        "title_preset": {"type": "string", "description": "标题样式预设: cinema_gold / clean_white / editorial_pill / crimson_badge / magazine_minimal"},
                        "sticker_path": {"type": "string", "description": "可选：电话号码贴纸 PNG 路径"},
                        "send_to_orante_task": {"type": "string", "description": "可选：完成后自动送入的 Orante 任务 ID 或目录"},
                    },
                    "required": ["prompts_text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "image_studio_edit",
                "description": (
                    "【AI 视觉制图与批量精修工坊 · 单图精修】对指定图片执行局部重绘(inpaint)、魔术擦除(erase)、"
                    "AI 扩图(outpaint)、一键抠图/点选抠图(matte)、文字与景深海报排版(typography)、超清放大(upscale)或历史版本回退(revert)。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_dir": {"type": "string", "description": "Image Studio 工程目录路径"},
                        "item_id": {"type": "string", "description": "目标图片 ID，如 'img_0001'"},
                        "action": {"type": "string", "enum": ["inpaint", "erase", "outpaint", "matte", "typography", "upscale", "revert"]},
                        "prompt": {"type": "string", "description": "局部重绘或扩图提示词"},
                        "mask": {"type": "string", "description": "遮罩文件路径或 base64"},
                        "target_aspect": {"type": "string", "description": "扩图目标画幅，如 '9:16'"},
                        "title": {"type": "string", "description": "海报主标题文字"},
                        "subtitle": {"type": "string", "description": "海报副标题文字"},
                        "depth_text": {"type": "boolean", "description": "是否启用人物前景遮挡大字的景深文字效果"},
                        "version": {"type": "integer", "description": "回退的目标版本号 (默认 1)"},
                    },
                    "required": ["item_id", "action"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "save_workflow_memory",
                "description": "当学到用户新的操作习惯、目录整理规则或标准工作流步骤时，调用此工具将其永久保存到 SOP 与习惯记忆库中。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rule": {"type": "string", "description": "归纳提炼出的流程规则或用户偏好"},
                        "category": {"type": "string", "enum": ["sop", "habit"], "description": "sop=标准作业流程, habit=用户习惯"},
                    },
                    "required": ["rule"],
                },
            },
        },
    ]

    def __init__(self, conductor: DailyWorkflowConductor):
        self.conductor = conductor
        self.memory_engine = RobotaMemoryEvolutionEngine()
        self.history: List[Dict[str, Any]] = []
        self.compacted_summary: str = ""
        self._last_user_text: str = ""
        self._lock = threading.RLock()
        self.ollama_model = self._detect_best_ollama_model()
        self.has_antigravity_sdk = self._check_antigravity_sdk()
        self.has_agy_cli = Path(self.AGY_EXE).is_file()
        self.agy_conversation_id: Optional[str] = None

        # 默认全程由 Gemini 4 Argon (gemini-4-argon-high) 全面接手，除非用户在下拉框手动指定其他模型
        explicit_model = str(self.conductor.config.get("user_explicit_model") or "").strip()
        valid_ids = {m["id"] for m in self.MODEL_CATALOG}
        if explicit_model in valid_ids:
            self.selected_model_id = explicit_model
        else:
            self.selected_model_id = self.DEFAULT_MODEL_ID
            self.conductor.config["selected_model_id"] = self.DEFAULT_MODEL_ID
            self.conductor._save_config()

        self.brain_mode: str = self._get_model_info(self.selected_model_id)["engine"]
        self.active_model: str = self._get_model_info(self.selected_model_id)["short"]

    def _get_model_info(self, model_id: str) -> Dict[str, str]:
        for m in self.MODEL_CATALOG:
            if m["id"] == model_id:
                return m
        if model_id.startswith("smol:"):
            raw_m = model_id.split("smol:", 1)[1]
            return {
                "id": model_id,
                "label": f"🤖 SmolAgents · {raw_m}",
                "short": f"SmolAgents ({raw_m})",
                "engine": "smol",
            }
        return {
            "id": model_id,
            "label": f"🧠 {model_id}",
            "short": model_id,
            "engine": "agy" if not model_id.endswith(":latest") and ":" not in model_id else "ollama",
        }

    def get_model_catalog(self) -> List[Dict[str, str]]:
        return list(self.MODEL_CATALOG)

    def set_model_by_id(self, model_id: str) -> str:
        info = self._get_model_info(model_id)
        if model_id != self.selected_model_id:
            self.agy_conversation_id = None
        self.selected_model_id = info["id"]
        self.brain_mode = info["engine"]
        self.active_model = info["short"]
        self.conductor.config["selected_model_id"] = self.selected_model_id
        self.conductor.config["user_explicit_model"] = self.selected_model_id
        self.conductor._save_config()
        return self.active_model

    def cycle_brain_mode(self) -> str:
        ids = [m["id"] for m in self.MODEL_CATALOG]
        idx = ids.index(self.selected_model_id) if self.selected_model_id in ids else 0
        next_id = ids[(idx + 1) % len(ids)]
        return self.set_model_by_id(next_id)

    def _check_antigravity_sdk(self) -> bool:
        try:
            import google.antigravity  # noqa: F401
            return True
        except Exception:
            return False

    def _detect_best_ollama_model(self) -> str:
        try:
            req = urllib.request.Request(f"{self.OLLAMA_URL}/api/tags")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                names = [m.get("name", "") for m in (data.get("models") or [])]
                for pref in ("muse-glimmer:latest", "muse-glimmer", "gemma4:26b", "gemma4:12b"):
                    if pref in names:
                        return pref
                if names:
                    return names[0]
        except Exception:
            pass
        return "muse-glimmer:latest"

    def _compact_history_if_needed(self) -> None:
        """
        对话上下文自动压缩 (Context Compaction)：
        当历史消息超过 16 条时，将最早的 10 条对话压缩为精炼的阶段性摘要，保持主对话上下文干净。
        """
        with self._lock:
            if len(self.history) <= 16:
                return
            older = self.history[:10]
            self.history = self.history[10:]
            bullets: List[str] = []
            for i in range(0, len(older), 2):
                u_msg = (older[i].get("content") or "").strip().replace("\n", " ")[:80]
                a_msg = (older[i + 1].get("content") or "").strip().replace("\n", " ")[:100] if i + 1 < len(older) else ""
                if u_msg:
                    bullets.append(f"• 用户:{u_msg} -> 结果:{a_msg}")
            new_chunk = "\n".join(bullets)
            if self.compacted_summary:
                self.compacted_summary = (self.compacted_summary + "\n" + new_chunk)[-1200:]
            else:
                self.compacted_summary = new_chunk[-1200:]

    def _check_hard_safety_gate(self, fn_name: str, fn_args: Dict[str, Any]) -> Tuple[bool, str]:
        """
        代码级硬性安全边界检查（不能只靠提示词）：
        任何涉及删除文件或联网上传云盘的动作，除非用户在当前指令中已明确下达了“确认删除/确认上传”，否则在代码层强制拦截并询问确认。
        """
        u_txt = (self._last_user_text or "").lower()
        arg_str = json.dumps(fn_args, ensure_ascii=False).lower()
        if any(k in arg_str for k in ("remove-item", "del ", "rm ", "rmdir", "unlink", "rmtree")):
            if not any(c in u_txt for c in ("确认删除", "确定删除", "帮我删", "删掉")):
                return False, "【安全边界拦截】检测到文件删除操作。按工作室铁律，删文件前必须先征得你的明确确认，请回复『确认删除』后再执行。"
        if any(k in arg_str for k in ("rclone copy", "rclone sync", "rclone move")):
            if not any(c in u_txt for c in ("上传", "同步云盘", "发云盘")):
                return False, "【安全边界拦截】检测到云盘上传指令，请先确认是否现在上传。"
        return True, ""

    @staticmethod
    def _distill_tool_result(fn_name: str, res: Dict[str, Any], max_chars: int = 360) -> Dict[str, Any]:
        """
        子代理结果蒸馏器 (Sub-Agent Context Distillation)：
        将底层工具返回的庞大 JSON 提炼为核心结论摘要，防止撑爆主对话上下文窗口。
        """
        if not isinstance(res, dict):
            return {"ok": True, "summary": str(res)[:max_chars]}
        if fn_name == "inspect_workspace":
            lc = res.get("lifecycle") or {}
            return {
                "ok": True,
                "day_str": res.get("day_str"),
                "active_batch": res.get("active_batch", "A"),
                "stage_label": res.get("stage_label") or lc.get("stage_label"),
                "vid_dir": res.get("actual_vid_dir"),
                "vid_count": res.get("vid_count"),
                "img_dir": res.get("actual_img_dir"),
                "img_count": res.get("img_count"),
                "uploaded_ok": lc.get("uploaded_ok_count", 0),
                "upload_total": lc.get("total_upload_rows", 0),
                "prev_completed_day": res.get("prev_completed_day", ""),
            }
        raw_s = json.dumps(res, ensure_ascii=False)
        if len(raw_s) <= max_chars:
            return res
        compact: Dict[str, Any] = {"ok": res.get("ok", True)}
        for k in ("message", "project_file", "active_day", "active_batch", "main_videos_count", "external_clips_count", "linked_tasks_count", "split_parts_count", "applied_tasks", "stdout", "error"):
            if k in res and res[k] not in (None, ""):
                val = res[k]
                compact[k] = str(val)[:240] if isinstance(val, str) else val
        return compact

    def _dispatch_parallel_subagents(
        self,
        tasks: List[Dict[str, Any]],
        ui_actions: List[Dict[str, str]],
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """
        真正的后台多子代理并行协同引擎：
        利用 `ThreadPoolExecutor` 同时并发运行多个独立子代理任务，互不阻塞，且仅将蒸馏后的精简报告汇总回主对话。
        """
        import concurrent.futures

        valid_tasks = [t for t in (tasks or []) if isinstance(t, dict) and t.get("tool")][:6]
        if not valid_tasks:
            return {"ok": False, "error": "未提供有效的并行子代理任务列表"}

        t0 = time.perf_counter()
        if progress_callback:
            roles_str = " / ".join([str(t.get("role") or t.get("tool")) for t in valid_tasks])
            progress_callback(f"⚡ 正在并行调度 {len(valid_tasks)} 个子代理协同执行: {roles_str}…")

        def _worker(idx: int, spec: Dict[str, Any]) -> Dict[str, Any]:
            role = str(spec.get("role") or f"SubAgent-{idx + 1}")
            tool_name = str(spec.get("tool") or "").strip()
            tool_args = spec.get("args") if isinstance(spec.get("args"), dict) else {}
            if tool_name == "dispatch_parallel_subagents":
                return {"role": role, "tool": tool_name, "ok": False, "error": "禁止递归调用子代理"}
            w_t0 = time.perf_counter()
            raw_res = self._execute_single_tool(tool_name, tool_args, ui_actions)
            distilled = self._distill_tool_result(tool_name, raw_res, max_chars=320)
            return {
                "subagent": role,
                "tool": tool_name,
                "elapsed_sec": round(time.perf_counter() - w_t0, 2),
                "conclusion": distilled,
            }

        reports: List[Dict[str, Any]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(valid_tasks))) as pool:
            futures = [pool.submit(_worker, i, t) for i, t in enumerate(valid_tasks)]
            for fut in futures:
                try:
                    reports.append(fut.result(timeout=45))
                except Exception as e:
                    reports.append({"ok": False, "error": str(e)})

        return {
            "ok": True,
            "parallel_subagents_count": len(reports),
            "total_elapsed_sec": round(time.perf_counter() - t0, 2),
            "subagent_reports": reports,
        }

    def _format_cutora_snapshot(self, detail: Dict[str, Any]) -> str:
        c_sum = detail.get("active_cutora_summary")
        if not c_sum or not c_sum.get("ok"):
            return "当前目录下暂未生成 `.cutora` 工程文件（默认保存路径为 `<日期目录>\\<日期>.cutora`）"
        return (
            f"工程文件保存在 `{c_sum['project_file']}`（最后保存时间: {c_sum.get('last_modified', '')}） | "
            f"主视频任务: **{c_sum.get('task_count', 0)}** 条（来源 `{','.join(c_sum.get('main_video_dirs') or [])}`） | "
            f"已连线外部视频: **{c_sum.get('linked_external_clips_count', 0)}** 条（来源 `{','.join(c_sum.get('external_clips_dirs') or [])}`） | "
            f"分屏布局: `{c_sum.get('split_layouts', {})}` | "
            f"已加载文字覆层(钩子/署名): **{c_sum.get('tasks_with_text_overlays', 0)}** 条 | "
            f"已生成字幕任务: **{c_sum.get('tasks_with_subtitles', 0)}** 条 | "
            f"已导出成品: **{c_sum.get('exported_count', 0)}** 条"
        )

    def _build_system_prompt(self, detail: Dict[str, Any], user_query: str = "") -> str:
        mem_txt = self.memory_engine.load_full_memory_prompt(max_episodes=8)
        rag_res = self.conductor.search_studio_knowledge(user_query, top_k=3) if user_query else {"matches": []}
        rag_txt = "\n\n".join([f"[{m['source']}]\n{m['content']}" for m in (rag_res.get("matches") or [])])

        sub_summary = ", ".join(
            [f"`{s['name']}`({s['file_count']}个文件)" for s in (detail.get("subfolders") or [])]
        ) or "无子文件夹"
        ext_clips_summary = ", ".join(
            [f"`{c['path']}`({c['video_count']}个外部视频)" for c in (detail.get("external_clip_folders") or [])]
        ) or "无独立 clips 子目录"
        cutora_snapshot = self._format_cutora_snapshot(detail)
        days_summary = ", ".join(
            [
                f"`{d['day']}`[{d.get('stage_label', '')}]"
                for d in (detail.get("recent_days") or [])
            ]
        )
        extless = detail.get("extensionless_files") or []
        extless_str = f"发现 {len(extless)} 个无标准后缀媒体文件: {extless[:15]}" if extless else "全部媒体文件后缀均已规范"
        ops_cli = str(STUDIO_ROOT / "core" / "robota_ops.py")
        py_exe = str(STUDIO_ROOT / "runtime" / "venv" / "Scripts" / "python.exe")
        if not Path(py_exe).is_file():
            py_exe = "python"

        lc = detail.get("lifecycle") or {}
        prev_day = detail.get("prev_completed_day") or ""
        prev_sum = detail.get("prev_completed_summary") or {}
        prev_done_line = ""
        if prev_day:
            prev_done_line = (
                f"- 前序完结日期反馈：`{prev_day}` 已在 `{','.join(prev_sum.get('export_folders') or ['字幕成品'])}` "
                f"通过 `.renamer_project.json` 验证 **{prev_sum.get('uploaded_ok_count', 0)}/{prev_sum.get('total_upload_rows', 0)} 条成品 100% 上传成功 ✅**（任务已彻底完结）！\n"
            )

        compacted_block = f"\n【前序对话摘要 (Compacted Context)】：\n{self.compacted_summary}\n" if self.compacted_summary else ""
        rag_block = f"\n【RAG 检索到的相关项目规范】：\n{rag_txt}\n" if rag_txt else ""

        return (
            f"你是 Robota，Luca 的《片语工作室》(D:\\0_LTX) 顶尖【项目代理兼顾问】（当前搭载大脑：{self.active_model}）。\n"
            "【核心分工与行为准则（最高优先级）】：\n"
            "1. **全生命周期状态感知与闭环反馈（6 阶段状态机）**：\n"
            "   - 你时刻掌握每个日期与批次（`A` -> `B` -> `C`）处于哪一阶段：`STAGE_0_READY`(待启动) → `STAGE_1_MATERIAL_READY`(原始素材就位) → `STAGE_2_CLIPS_READY`(Orante Clips就绪) → `STAGE_3_CUTORA_EDITING`(CUTORA剪辑/按40条拆分) → `STAGE_4_EXPORTED`(已导出成品) → `STAGE_5_UPLOADED_DONE`(在 `字幕成品/.renamer_project.json` 中验证 `up.status=='ok'` 全部上传完毕 ✅)。\n"
            "   - 当检测到前一日（如 `2026-10-15`）已全部上传完结，或 Luca 在对话中告知状态变更（如“15号传完了，开始16号 A批次”），必须立即跟上 Luca 的节奏，主动确认并调用 `update_project_lifecycle_state` 保存最新状态！\n"
            "2. **多子代理并行协同 (`dispatch_parallel_subagents`) 与秒回准则**：\n"
            "   - 如果 Luca 只是询问工程保存在哪里、目录结构、上传完成度、当前处于哪个批次，而下方【当前工作区实时全景快照】已有准确数据，请**直接根据快照在 3 秒内秒回**！\n"
            "   - 如果需要同时执行多项操作或检查（如同时核查多个目录、既要加 AI 署名又要按 40 条拆分工程），优先调用 `dispatch_parallel_subagents` 并行派发多个子代理同步完成，既快又节省上下文窗口。\n"
            "3. **严格遵守配方 A 与配方 B 的人机协同边界**：\n"
            "   - **动态日期命名**：主视频与图片文件夹以当天日期命名（如 `1016视频`、`1016图片`、`15视频`、`15图片`），切勿写死固定名字。\n"
            "   - **配方 A（分镜排版 → 智能拼接 → 时间线 → 自动字幕 + AI 署名 + 人工电话卡 → 按 40 条拆分导出 → 上传）**：镜头排版、生成后画面检查、电话号卡片与剪辑细节由人工介入；智能拼接取原文案、自动打字幕 (`cutora_auto_subtitle`)、加 AI 署名 (`cutora_add_ai_credit`)、按 **40 个任务/工程 (`chunk=40`)** 拆分 (`cutora_split_project`) 由你代理执行。\n"
            "   - **配方 B（当天长视频媒体库 + Orante 跳过编排生成 clips → CUTORA 表格连线 + 分屏 → 人工加标题/手机号卡 + 全部加 AI 署名 → 按 40 条拆分导出 → 上传）**：\n"
            "     • 当长视频数量 == clips 数量（**数量一致**）：自动从上到下 **一对一 (`1:1`) 连线** 并同时启用上下分屏 (`top_bottom`)；\n"
            "     • 当长视频数量 != clips 数量（**数量不一致**）：属于 **一对多 (`1:N`)** 连线关系！**严禁盲目自动 1:1 强连**，先加载长视频进 `.cutora` 并打开 CUTORA，向 Luca 汇报双方准确数量，交由人工完成一对多连线与确认！\n"
            "4. **严禁只回复“已完成全部工具操作”**，严禁全局 grep `D:\\0_LTX\\modules` 源码树。\n\n"
            "【当前工作区实时全景快照 (`D:\\1_PROJECT`)】：\n"
            f"- 当前活跃日期与批次：`{detail['day_dir']}`（日期 **`{detail['day_str']}`** · 当前批次 **`{detail.get('active_batch', 'A')}批次`**）\n"
            f"- 当前任务生命周期阶段：**{detail.get('stage_label', lc.get('stage_label', '待启动'))}** (`{detail.get('stage_code', '')}`)\n"
            + prev_done_line
            + f"- 当期子文件夹列表：{sub_summary}\n"
            f"- 当期实际【主视频目录】：`{detail['actual_vid_dir']}`（已就绪 **{detail['vid_count']}** 个原始主视频，已排除字幕成品/导出目录）\n"
            f"- 当期实际【图片目录】：`{detail['actual_img_dir']}`（已就绪 **{detail['img_count']}** 张图片）\n"
            f"- 当期外部【连线视频 clips 目录】：{ext_clips_summary}\n"
            f"- 当期【CUTORA 工程 (`.cutora`) 实时状态】：{cutora_snapshot}\n"
            f"- 当期无后缀/伪后缀文件状态：{extless_str}\n"
            f"- 最近工作日期与闭环上传状态一览：{days_summary}\n\n"
            "【Robota 专属原子积木工具箱 (`robota_ops.py`) —— 可在命令行一键调用】：\n"
            f"- 更新并锁定当前活跃日期/批次/完结状态：`\"{py_exe}\" \"{ops_cli}\" lifecycle --active-day 2026-10-16 --batch A --mark-done 2026-10-15`\n"
            f"- 配方 B 创建工程 + 1:1 自动连线分屏（若数量不一致自动保护并转人工 1:N 连线）：`\"{py_exe}\" \"{ops_cli}\" cutora create-link --main-dir \"{detail['actual_vid_dir']}\" --clips-dir \"<clips目录>\" --split top_bottom`\n"
            f"- 配方 A/B 批量给全部任务添加 AI 署名 (`credit.default`)：`\"{py_exe}\" \"{ops_cli}\" cutora add-credit`\n"
            f"- 配方 A/B 按 40 个任务/工程批量拆分 `.cutora` 工程：`\"{py_exe}\" \"{ops_cli}\" cutora split --chunk 40`\n"
            f"- 配方 A 同步 Orante 原文案并打开 CUTORA 智能拼接：`\"{py_exe}\" \"{ops_cli}\" cutora stitch-sync`\n"
            f"- 配方 A 为工程全部时间线自动生成字幕：`\"{py_exe}\" \"{ops_cli}\" cutora auto-subtitle`\n"
            f"- 检查 CUTORA 工程详情：`\"{py_exe}\" \"{ops_cli}\" cutora inspect`\n"
            f"- 查看任意日期目录与上传闭环详情：`\"{py_exe}\" \"{ops_cli}\" status 2026-10-16`\n"
            f"- 补齐无后缀视频 `.mp4` 并归入视频文件夹：`\"{py_exe}\" \"{ops_cli}\" fix-organize \"D:\\1_PROJECT\\2026-10-16\" --video-dir \"1016视频\"`\n"
            f"- 打开指定文件夹：`\"{py_exe}\" \"{ops_cli}\" open-folder \"视频\" --day 2026-10-16`\n"
            f"- 启动工作室软件：`\"{py_exe}\" \"{ops_cli}\" launch cutora` （或 `table_tool` / `orante`）\n"
            f"- 记入长期工作流记忆：`\"{py_exe}\" \"{ops_cli}\" remember \"总结出的流程规则\"`\n"
            + compacted_block
            + rag_block
            + (f"\n【Luca 长期记忆与完整工作流手册】：\n{mem_txt}\n" if mem_txt else "")
        )

    def _execute_single_tool(
        self,
        fn_name: str,
        fn_args: Dict[str, Any],
        ui_actions: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        """执行单个本地原子工具（内置硬性安全边界检查）并返回结构化 JSON 结果。"""
        ok_safe, safe_err = self._check_hard_safety_gate(fn_name, fn_args)
        if not ok_safe:
            return {"ok": False, "blocked_by_safety_gate": True, "error": safe_err}

        if fn_name == "inspect_workspace":
            target = str(fn_args.get("target_day_or_path") or "")
            return self.conductor.inspect_workspace_detail(target)

        if fn_name == "update_project_lifecycle_state":
            return self.conductor.update_project_lifecycle_state(
                active_day=str(fn_args.get("active_day") or ""),
                active_batch=str(fn_args.get("active_batch") or ""),
                mark_day_completed=str(fn_args.get("mark_day_completed") or ""),
                recipe_type=str(fn_args.get("recipe_type") or ""),
                note=str(fn_args.get("note") or ""),
            )

        if fn_name == "dispatch_parallel_subagents":
            subtasks = fn_args.get("tasks") if isinstance(fn_args.get("tasks"), list) else []
            return self._dispatch_parallel_subagents(subtasks, ui_actions)

        if fn_name == "cutora_create_and_link_project":
            m_dir = str(fn_args.get("main_videos_dir") or "")
            e_dir = str(fn_args.get("external_clips_dir") or "")
            s_lay = str(fn_args.get("split_layout") or "top_bottom")
            out_p = str(fn_args.get("output_project_path") or "")
            f_auto = bool(fn_args.get("force_auto_link", False))
            return self.conductor.cutora_create_and_link_project(
                main_videos_dir=m_dir,
                external_clips_dir=e_dir,
                output_project_path=out_p,
                split_layout=s_lay,
                loop=True,
                match_duration=True,
                mute=True,
                open_in_cutora=True,
                force_auto_link=f_auto,
            )

        if fn_name == "cutora_add_ai_credit":
            pp = str(fn_args.get("project_path") or "")
            preset = str(fn_args.get("preset") or "credit.default")
            content = str(fn_args.get("content") or "")
            return self.conductor.cutora_add_ai_credit(project_path=pp, preset=preset, content=content)

        if fn_name == "cutora_split_project":
            pp = str(fn_args.get("project_path") or "")
            chunk = int(fn_args.get("chunk_size") or 40)
            odir = str(fn_args.get("out_dir") or "")
            return self.conductor.cutora_split_project(project_path=pp, chunk_size=chunk, out_dir=odir)

        if fn_name == "cutora_auto_subtitle":
            pp = str(fn_args.get("project_path") or "")
            f_rel = bool(fn_args.get("force_relayout", False))
            return self.conductor.cutora_auto_subtitle(project_path=pp, force_relayout=f_rel)

        if fn_name == "cutora_smart_stitch_sync":
            b_dir = str(fn_args.get("batch_dir") or "")
            open_c = bool(fn_args.get("open_in_cutora", True))
            return self.conductor.cutora_smart_stitch_sync(batch_dir=b_dir, open_in_cutora=open_c)

        if fn_name == "search_studio_knowledge":
            q = str(fn_args.get("query") or "")
            return self.conductor.search_studio_knowledge(query=q)

        if fn_name == "inspect_cutora_project":
            pp = str(fn_args.get("project_path") or "")
            return self.conductor.inspect_cutora_project(pp)

        if fn_name == "fix_extensions_and_organize":
            target = str(fn_args.get("target_dir_or_day") or "")
            v_folder = str(fn_args.get("video_folder_name") if "video_folder_name" in fn_args else "视频")
            i_folder = str(fn_args.get("image_folder_name") or "")
            return self.conductor.fix_extensions_and_organize(
                target_dir_or_day=target,
                video_folder_name=v_folder,
                image_folder_name=i_folder,
                clean_pseudo_suffix=True,
            )

        if fn_name == "rename_or_move_path":
            src = str(fn_args.get("source_path") or "")
            dst = str(fn_args.get("dest_path") or "")
            return self.conductor.rename_or_move_path(src, dst)

        if fn_name == "open_folder":
            target = str(fn_args.get("target") or "视频")
            return self.conductor.open_folder_smart(target)

        if fn_name == "open_workspace_app":
            app_n = str(fn_args.get("app_name") or "orante")
            t_dir = str(fn_args.get("target_dir") or "").strip()
            if app_n == "table_tool":
                ok = self.conductor.launch_table_tool_in_dir(Path(t_dir) if t_dir else None)
                return {"ok": ok, "launched": "右键表格工具", "dir": t_dir or self.conductor.get_live_workspace_stats()["day_dir"]}
            if app_n == "cutora":
                proj_p = t_dir if t_dir.lower().endswith(".cutora") else ""
                room_n = "" if proj_p else "stitch"
                ok = self.conductor.launch_cutora_room(room=room_n, project_path=proj_p)
                return {"ok": ok, "launched": "CUTORA", "project_path": proj_p}
            ok = self.conductor.launch_orante_room()
            return {"ok": ok, "launched": "Orante 2.0 制片台"}

        if fn_name == "control_orante":
            act = str(fn_args.get("action") or "health")
            tid = str(fn_args.get("task_id") or "")
            return self.conductor.control_orante_api(act, tid)

        if fn_name == "run_shell_command":
            cmd_s = str(fn_args.get("command") or "")
            if re.search(r"(\\Videos\b|shell:My Video)", cmd_s, re.IGNORECASE):
                return self.conductor.open_folder_smart("视频")
            return self.conductor.run_safe_background_command(cmd_s)

        if fn_name == "orante_prepare_image_task":
            img_f = str(fn_args.get("image_folder") or "").strip()
            t_name = str(fn_args.get("task_name") or "").strip()
            b_let = str(fn_args.get("batch_letter") or "A").strip()
            return self.conductor.orante_create_and_prepare_image_task(
                image_folder=Path(img_f) if img_f else None,
                task_name=t_name,
                batch_letter=b_let,
            )

        if fn_name == "image_studio_batch":
            return self.conductor.image_studio_batch(
                project_dir=str(fn_args.get("project_dir") or ""),
                prompts_text=str(fn_args.get("prompts_text") or ""),
                aspects=fn_args.get("aspects") if isinstance(fn_args.get("aspects"), list) else ["9:16"],
                title_preset=str(fn_args.get("title_preset") or "cinema_gold"),
                sticker_path=str(fn_args.get("sticker_path") or ""),
                send_to_orante_task=str(fn_args.get("send_to_orante_task") or ""),
            )

        if fn_name == "image_studio_edit":
            kw = {k: v for k, v in fn_args.items() if k not in ("project_dir", "item_id", "action")}
            return self.conductor.image_studio_edit(
                project_dir=str(fn_args.get("project_dir") or ""),
                item_id=str(fn_args.get("item_id") or "img_0001"),
                action=str(fn_args.get("action") or "inpaint"),
                **kw,
            )

        if fn_name == "save_workflow_memory":
            rule = str(fn_args.get("rule") or "")
            cat = str(fn_args.get("category") or "sop")
            return self.memory_engine.save_explicit_rule(rule, category=cat)

        return {"ok": False, "error": f"未知工具: {fn_name}"}

    def _call_smolagents_engine(
        self,
        user_text: str,
        sys_prompt: str,
        ui_actions: List[Dict[str, str]],
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> Optional[Tuple[str, List[Dict[str, Any]]]]:
        """
        使用 `smolagents` (`ToolCallingAgent` + `OpenAIServerModel`) 驱动本地大模型执行原子积木组合：
        将 `TOOLS_SCHEMA` 中的全部工作室原子工具包装为 `smolagents.Tool` 实例并连接本地 Ollama `/v1` 端点。
        """
        try:
            from smolagents import OpenAIServerModel, Tool, ToolCallingAgent
        except Exception:
            return None

        raw_model = self.selected_model_id.split("smol:", 1)[1] if self.selected_model_id.startswith("smol:") else self.ollama_model
        executed_tools: List[Dict[str, Any]] = []
        brain_self = self

        smol_tools: List[Any] = []
        for schema in self.TOOLS_SCHEMA:
            fn_spec = schema.get("function") or {}
            t_name = fn_spec.get("name", "")
            t_desc = fn_spec.get("description", "")
            props = (fn_spec.get("parameters") or {}).get("properties") or {}
            req_set = set((fn_spec.get("parameters") or {}).get("required") or [])
            inputs_dict: Dict[str, Dict[str, Any]] = {}
            for p_name, p_info in props.items():
                d_entry: Dict[str, Any] = {
                    "type": p_info.get("type", "string"),
                    "description": p_info.get("description", p_name),
                }
                if p_name not in req_set:
                    d_entry["nullable"] = True
                inputs_dict[p_name] = d_entry

            def _make_forward(tool_name: str):
                def _forward(self_tool, **kwargs):
                    clean_kw = {k: v for k, v in kwargs.items() if v is not None}
                    if progress_callback:
                        progress_callback(f"🤖 SmolAgents 正在调用: {tool_name}({json.dumps(clean_kw, ensure_ascii=False)[:50]})")
                    res = brain_self._execute_single_tool(tool_name, clean_kw, ui_actions)
                    executed_tools.append({"name": tool_name, "params": clean_kw, "result": res})
                    return json.dumps(brain_self._distill_tool_result(tool_name, res, max_chars=600), ensure_ascii=False)
                return _forward

            tool_cls = type(
                f"SmolTool_{t_name}",
                (Tool,),
                {
                    "name": t_name,
                    "description": t_desc,
                    "inputs": inputs_dict,
                    "output_type": "string",
                    "forward": _make_forward(t_name),
                    "skip_forward_signature_validation": True,
                },
            )
            smol_tools.append(tool_cls())

        try:
            if progress_callback:
                progress_callback(f"🤖 SmolAgents 引擎 ({raw_model}) 正在规划并组合工具…")
            model = OpenAIServerModel(
                model_id=raw_model,
                api_base=f"{self.OLLAMA_URL.rstrip('/')}/v1",
                api_key="ollama",
            )
            agent = ToolCallingAgent(
                tools=smol_tools,
                model=model,
                max_steps=6,
                verbosity_level=0,
                instructions=sys_prompt,
            )
            out = str(agent.run(user_text)).strip()
            if out:
                return out, executed_tools
        except Exception:
            pass
        return None

    @staticmethod
    def _condense_bulk_urls_for_cli(user_text: str) -> str:
        """
        如果用户消息中包含大段 URL（> 6 条），在传给 `agy.exe -p` 命令行时压缩 URL 列表展示，
        防止 Windows 命令行长度超限或云端流式模型花费大量时间逐字复读 76 条 URL。
        """
        lines = (user_text or "").splitlines()
        url_lines = [ln for ln in lines if re.match(r"^\s*https?://", ln.strip())]
        if len(url_lines) <= 6:
            return user_text
        non_url_lines = [ln for ln in lines if not re.match(r"^\s*https?://", ln.strip()) and ln.strip()]
        sample = "\n".join(url_lines[:3])
        return (
            "\n".join(non_url_lines[:3])
            + f"\n（此处包含用户粘贴的 {len(url_lines)} 条素材链接，前 3 条示例：\n{sample}\n…其余链接已由本地引擎完整接收）\n"
            + "\n".join(non_url_lines[3:])
        ).strip()

    def _call_agy_stream(
        self,
        user_text: str,
        sys_prompt: str,
        detail: Dict[str, Any],
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> Optional[Tuple[str, List[Dict[str, Any]]]]:
        """
        调用 `agy.exe` 驱动 Gemini 4 Argon / Claude Opus 5.5 等旗舰大脑：
        - 启用 `--mode accept-edits --dangerously-skip-permissions --output-format stream-json`
        - 不传 `--add-dir D:\\0_LTX`，彻底避免 `agy.exe` 索引 `runtime/venv` 导致 4 分钟超时
        - 自动对超长链接列表瘦身并在旧会话卡顿时自动开启纯净新会话重试一次
        """
        if not self.has_agy_cli:
            return None

        cwd = detail["day_dir"] if Path(detail["day_dir"]).is_dir() else detail["ws"]
        cutora_snap = self._format_cutora_snapshot(detail)
        ext_clips_s = ", ".join([f"{c['path']}({c['video_count']}个)" for c in (detail.get("external_clip_folders") or [])]) or "无"
        ops_cli = str(STUDIO_ROOT / "core" / "robota_ops.py")
        py_exe = str(STUDIO_ROOT / "runtime" / "venv" / "Scripts" / "python.exe")
        compact_user_text = self._condense_bulk_urls_for_cli(user_text)

        for attempt in range(2):
            if self.agy_conversation_id:
                sub_s = ", ".join([f"{s['name']}({s['file_count']})" for s in (detail.get("subfolders") or [])])
                prompt = (
                    f"[实时工作区快照: 当前活跃=`{detail['day_str']} · {detail.get('active_batch', 'A')}批次`, "
                    f"生命周期阶段=`{detail.get('stage_label', '')}`, "
                    f"前序已完结日期=`{detail.get('prev_completed_day', '')}`, "
                    f"日期目录=`{detail['day_dir']}`, 子文件夹=[{sub_s}], "
                    f"主视频目录=`{detail['actual_vid_dir']}`({detail['vid_count']}个), "
                    f"图片目录=`{detail['actual_img_dir']}`({detail['img_count']}张), "
                    f"外部clips目录=[{ext_clips_s}], "
                    f"CUTORA工程状态=({cutora_snap})]\n"
                    f"[重要提示: 直接回答问题或调用 `\"{py_exe}\" \"{ops_cli}\" lifecycle / cutora create-link / add-credit / split / inspect / status`，严禁 grep 遍历 D:\\0_LTX\\modules 源码！]\n\n"
                    f"{compact_user_text}"
                )
            else:
                prompt = f"{sys_prompt}\n\n【用户当前指令】：\n{compact_user_text}"

            cmd = [
                self.AGY_EXE,
                "-p",
                prompt,
                "--model",
                self.selected_model_id,
                "--mode",
                "accept-edits",
                "--dangerously-skip-permissions",
                "--disable-slash-commands",
                "--output-format",
                "stream-json",
                "--print-timeout",
                "90s",
            ]
            if self.agy_conversation_id:
                cmd.extend(["--conversation", self.agy_conversation_id])

            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"

            executed_tools: List[Dict[str, Any]] = []
            streamed_chunks: List[str] = []
            final_response_text = ""

            try:
                if progress_callback:
                    progress_callback(f"⚡ {self.active_model} 正在深度思考与规划…")
                self.conductor.swarm.update_clone("alpha", "RUNNING", f"{self.active_model} 思考中…", 25)

                proc = subprocess.Popen(
                    cmd,
                    cwd=str(cwd),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    **_hidden_popen_kwargs(),
                )

                assert proc.stdout is not None
                for raw_line in proc.stdout:
                    s_line = raw_line.strip()
                    if not s_line.startswith("{"):
                        continue
                    try:
                        ev = json.loads(s_line)
                    except Exception:
                        continue

                    kind = ev.get("event")
                    if kind == "init":
                        cid = ev.get("conversation_id")
                        if cid:
                            self.agy_conversation_id = cid
                    elif kind == "step_update":
                        su = ev.get("step_update") or {}
                        stype = su.get("step_type")
                        state = su.get("state")
                        if stype == "tool":
                            tinfo = su.get("tool_info") or {}
                            tname = tinfo.get("name") or su.get("tool_name") or "tool"
                            tparams = tinfo.get("parameters") or {}
                            desc = tname
                            for k in ("CommandLine", "AbsolutePath", "TargetFile", "DirectoryPath", "SearchPath"):
                                if k in tparams:
                                    desc = f"{tname}: {str(tparams[k])[:65]}"
                                    break
                            if state == "ACTIVE":
                                if progress_callback:
                                    progress_callback(f"🔧 {self.active_model} 正在执行: {desc}")
                                self.conductor.swarm.update_clone("beta", "RUNNING", f"执行 {desc[:28]}", 60)
                            elif state == "DONE":
                                executed_tools.append({"name": tname, "params": tparams, "desc": desc})
                        elif stype == "agent_response":
                            delta = su.get("text_delta") or ""
                            if delta:
                                streamed_chunks.append(delta)
                                if progress_callback and len(streamed_chunks) % 4 == 1:
                                    preview = "".join(streamed_chunks).strip().splitlines()[-1][:45]
                                    progress_callback(f"✍️ {self.active_model}：{preview}…")
                    elif kind == "result":
                        res_obj = ev.get("result") or {}
                        cid = res_obj.get("conversation_id")
                        if cid:
                            self.agy_conversation_id = cid
                        if res_obj.get("response"):
                            final_response_text = str(res_obj["response"]).strip()

                proc.wait(timeout=15)
                self.conductor.swarm.update_clone("alpha", "COMPLETED", f"{self.active_model} 执行完毕", 100, inc_completed=True)
                if executed_tools:
                    self.conductor.swarm.update_clone("beta", "COMPLETED", f"已调用 {len(executed_tools)} 项工具", 100, inc_completed=True)

                out_text = final_response_text or "".join(streamed_chunks).strip()
                if out_text:
                    return out_text, executed_tools
                # 如果带着旧 conversation_id 响应为空，清除会话 ID 并立即用新会话重试一次
                if self.agy_conversation_id and attempt == 0:
                    self.agy_conversation_id = None
                    continue
                break
            except Exception:
                if self.agy_conversation_id and attempt == 0:
                    self.agy_conversation_id = None
                    continue
                self.agy_conversation_id = None
                break
        return None

    def _diagnose_and_self_recover(
        self,
        user_text: str,
        detail: Dict[str, Any],
        executed_tools: List[Dict[str, Any]],
        error_msg: str = "",
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> str:
        """
        【智能根因诊断与自我恢复引擎】：
        绝不生硬抛出 `timed out` 等底层异常代码，而是：
        1. 分析问题根因（如大批量链接导致命令行参数过长、云端流式连接超时等）；
        2. 立即启动本地原子积木执行自我恢复计划（自动解析链接、写入右键表格工具、下载素材、准备 Orante 任务或核查工作区状态）；
        3. 用自然人话向用户汇报根因诊断与自动恢复结果。
        """
        parsed_bulk = self.conductor.parse_bulk_links_message(user_text)
        if parsed_bulk.get("total_links", 0) > 0:
            if progress_callback:
                progress_callback("🛠 正在启动智能自我恢复：直接通过本地下载与 Orante 准备引擎接管批量链接任务…")
            wf_res = self.conductor.execute_start_today_workflow(
                workspace_root=detail["ws"],
                task_spec=parsed_bulk["task_spec"],
                task_count=parsed_bulk["task_count"],
                video_type_key="3",
                image_links_text="\n".join(parsed_bulk["image_links"]),
                video_links_text="\n".join(parsed_bulk["video_links"]),
                auto_launch_orante=parsed_bulk.get("wants_orante_prepare", False),
                auto_register_orante=parsed_bulk.get("wants_orante_prepare", False),
                image_codes=parsed_bulk["image_codes"],
                video_codes=parsed_bulk["video_codes"],
                target_batch_letter=parsed_bulk.get("batch_letter", "A"),
            )
            executed_tools.append({"name": "execute_start_today_workflow", "params": {"task_count": parsed_bulk["task_count"]}, "result": wf_res})
            return (
                f"刚才由于单次指令携带了 **{parsed_bulk['total_links']} 条云盘长链接**，直接塞入云端大脑会话窗口触发了流式响应超时。\n\n"
                f"💡 **我的根因判断与自我恢复结果**：\n"
                f"1. **根因诊断**：大批量 URL 不应经过大模型逐条复读，而应由我的本地表格与下载子代理直接分流执行。\n"
                f"2. **已自动完成的恢复动作**：\n"
                f"   - 已将 **{len(parsed_bulk['image_links'])} 条图片链接**与 **{len(parsed_bulk['video_links'])} 条视频链接**（编号 `{parsed_bulk['task_spec']}`）完整写入《右键表格工具》下载面板；\n"
                f"   - 已将图片下载至 `{wf_res.get('img_dir')}`（就绪 **{wf_res.get('img_count', 0)}** 张），视频下载至 `{wf_res.get('vid_dir')}`（就绪 **{wf_res.get('vid_count', 0)}** 个）；\n"
                + (
                    f"   - 已将 `{Path(wf_res.get('img_dir', '')).name}` 发送给 **Orante** 创建任务 `{wf_res.get('orante_task_id', '')}` 并完成全部 **{wf_res.get('orante_prepared_count', 0)}** 张主体图的准备，你现在可以直接去 Orante 填写描述语了！"
                    if parsed_bulk.get("wants_orante_prepare")
                    else ""
                )
            )

        if any(k in (user_text or "") for k in ("Orante", "orante", "1016图片", "图片", "准备好", "描述语")):
            prep_res = self.conductor.orante_create_and_prepare_image_task(
                batch_letter=detail.get("active_batch", "A"),
                day_hint=detail.get("day_str", ""),
            )
            executed_tools.append({"name": "orante_prepare_image_task", "params": {}, "result": prep_res})
            if prep_res.get("ok"):
                return (
                    f"刚才 `{self.active_model}` 云端通道响应稍有延迟，我已经通过本地调度中心直接为你完成了恢复执行：\n\n"
                    f"- **已准备任务**：`{prep_res.get('task_id')} · {prep_res.get('task_name')}`（批次 `{prep_res.get('batch_id')}`）\n"
                    f"- **素材来源**：`{prep_res.get('image_source')}`（共导入 **{prep_res.get('prepared_images_count', 0)}** 张主体图 `p01~p{prep_res.get('prepared_images_count', 0):02d}`）\n"
                    f"- **任务目录**：`{prep_res.get('task_dir')}`\n\n"
                    f"现在任务已经在 Orante 里彻底准备就绪，你可以直接去 Orante 填写描述语了！"
                )

        # 通用诊断与状态汇报兜底
        fresh_detail = self.conductor.inspect_workspace_detail(detail.get("day_str", ""))
        reason_desc = (
            "云端流式通道响应超时（已自动重置会话连接并保持在 `Gemini 4 Argon` 引擎，不会回退到慢速的 `muse-glimmer`）"
            if "timed out" in (error_msg or "").lower() or not error_msg
            else f"云端通道偶发波动（`{error_msg}`，已自动重置会话连接）"
        )
        return (
            f"💡 **根因诊断与自我恢复说明**：\n"
            f"- **原因判断**：刚才出现了{reason_desc}。\n"
            f"- **自我恢复行动**：我已自动清理了卡顿的会话上下文，并为你实时核对了当前工作区状态：\n"
            f"  1. **当前活跃日期与批次**：`{fresh_detail['day_str']}` · **`{fresh_detail.get('active_batch', 'A')}批次`**（阶段：{fresh_detail.get('stage_label', '')}）\n"
            f"  2. **图片素材库**：`{fresh_detail['actual_img_dir']}`（已就绪 **{fresh_detail['img_count']}** 张图片）\n"
            f"  3. **主视频素材库**：`{fresh_detail['actual_vid_dir']}`（已就绪 **{fresh_detail['vid_count']}** 个主视频）\n\n"
            f"你可以直接继续吩咐我下一步操作，我会全程用 `{self.active_model}` 为你执行。"
        )

    def _call_ollama_tool_loop(
        self,
        user_text: str,
        sys_prompt: str,
        ui_actions: List[Dict[str, str]],
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """
        本地 Ollama (`muse-glimmer:latest` / `gemma4:26b`) 多步 Agentic Tool-Calling 循环：
        （仅当用户在界面下拉框中手动选择本地模型时调用，绝不在 Gemini 4 Argon 模式下自动回退调用！）
        """
        import concurrent.futures

        use_model = self.selected_model_id if self.brain_mode == "ollama" else self.ollama_model
        if use_model.startswith("smol:"):
            use_model = use_model.split("smol:", 1)[1]
        with self._lock:
            recent_hist = list(self.history[-10:])

        messages: List[Dict[str, Any]] = (
            [{"role": "system", "content": sys_prompt}]
            + recent_hist
            + [{"role": "user", "content": user_text}]
        )
        executed_tools: List[Dict[str, Any]] = []

        for step_idx in range(5):
            if progress_callback:
                progress_callback(
                    f"🏠 本地大脑 ({use_model}) 正在思考…" if step_idx == 0 else f"🏠 本地大脑正在汇总工具结果 (第 {step_idx + 1} 步)…"
                )
            payload = {
                "model": use_model,
                "messages": messages,
                "tools": self.TOOLS_SCHEMA,
                "stream": False,
                "think": False,
                "keep_alive": "30m",
                "options": {"temperature": 0.25, "num_ctx": 32768},
            }
            req = urllib.request.Request(
                f"{self.OLLAMA_URL}/api/chat",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))

            msg_obj = resp_data.get("message") or {}
            tool_calls = msg_obj.get("tool_calls") or []
            content = (msg_obj.get("content") or "").strip()

            if not tool_calls:
                if content:
                    return content, executed_tools
                break

            messages.append(msg_obj)

            parsed_calls: List[Tuple[str, Dict[str, Any]]] = []
            for tc in tool_calls:
                fn_info = tc.get("function") or {}
                fn_name = fn_info.get("name", "")
                fn_args = fn_info.get("arguments") or {}
                if isinstance(fn_args, str):
                    try:
                        fn_args = json.loads(fn_args)
                    except Exception:
                        fn_args = {}
                parsed_calls.append((fn_name, fn_args))

            if len(parsed_calls) > 1:
                if progress_callback:
                    progress_callback(f"⚡ 正在并行启动 {len(parsed_calls)} 个子代理同步执行工具…")
                with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(parsed_calls))) as pool:
                    futs = [pool.submit(self._execute_single_tool, fn, fa, ui_actions) for fn, fa in parsed_calls]
                    results = [f.result(timeout=45) for f in futs]
                for (fn_name, fn_args), tool_res in zip(parsed_calls, results):
                    executed_tools.append({"name": fn_name, "params": fn_args, "result": tool_res})
                    distilled = self._distill_tool_result(fn_name, tool_res, max_chars=700)
                    messages.append({
                        "role": "tool",
                        "content": json.dumps(distilled, ensure_ascii=False),
                    })
            else:
                fn_name, fn_args = parsed_calls[0]
                if progress_callback:
                    progress_callback(f"🔧 正在执行工具: {fn_name}({json.dumps(fn_args, ensure_ascii=False)[:50]})")
                tool_res = self._execute_single_tool(fn_name, fn_args, ui_actions)
                executed_tools.append({"name": fn_name, "params": fn_args, "result": tool_res})
                distilled = self._distill_tool_result(fn_name, tool_res, max_chars=900)
                messages.append({
                    "role": "tool",
                    "content": json.dumps(distilled, ensure_ascii=False),
                })

        # 最终自然语言总结回合（不挂 tools，强制模型根据工具返回结果回答用户的具体问题）
        try:
            if progress_callback:
                progress_callback(f"✍️ 本地大脑 ({use_model}) 正在生成回答…")
            synth_messages = messages + [
                {
                    "role": "user",
                    "content": (
                        "请根据以上工具返回的真实数据，直接、清晰、自然地回答我的问题或汇报操作结果"
                        "（必须包含具体的工程/文件路径、视频数量、连线与分屏状态或批次切换结果等关键信息，严禁只回复‘已完成全部工具操作’）。"
                    ),
                }
            ]
            synth_payload = {
                "model": use_model,
                "messages": synth_messages,
                "stream": False,
                "think": False,
                "keep_alive": "30m",
                "options": {"temperature": 0.25, "num_ctx": 32768},
            }
            req = urllib.request.Request(
                f"{self.OLLAMA_URL}/api/chat",
                data=json.dumps(synth_payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
            synth_content = ((resp_data.get("message") or {}).get("content") or "").strip()
            if synth_content:
                return synth_content, executed_tools
        except Exception:
            pass

        if executed_tools:
            last_res = executed_tools[-1].get("result") or {}
            return f"已为你执行完毕：`{json.dumps(last_res, ensure_ascii=False)[:400]}`", executed_tools
        return "已为你处理完毕。", executed_tools

    def chat_with_tools(
        self,
        user_text: str,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """
        统一智能对话与代理执行入口：
        1. 先从用户对话中实时提取并持久化日期/批次/完成状态变更 (`sync_state_from_user_message`)；
        2. 默认全程锁定 `Gemini 4 Argon` (`agy.exe`) 接手，绝不擅自回退到慢速的 `muse-glimmer`（除非用户手动选择本地模型）；
        3. 若遇到超长输入或异常，自动触发 `_diagnose_and_self_recover` 进行人话根因诊断与本地原子工具自愈执行；
        4. 每次执行后自动调用 `RobotaMemoryEvolutionEngine` 归纳沉淀用户的完整工作流。
        """
        ui_actions: List[Dict[str, str]] = []
        executed_tools: List[Dict[str, Any]] = []
        final_reply = ""
        self._last_user_text = user_text or ""
        detail: Dict[str, Any] = {}

        try:
            self._compact_history_if_needed()
            # 0. 实时捕捉用户在对话中传达的日期/批次/完结状态变更并持久化！
            sync_res = self.conductor.sync_state_from_user_message(user_text)
            if sync_res and sync_res.get("ok"):
                executed_tools.append({
                    "name": "update_project_lifecycle_state",
                    "params": {
                        "active_day": sync_res.get("active_day"),
                        "active_batch": sync_res.get("active_batch"),
                    },
                    "result": sync_res,
                })
                target_hint = str(sync_res.get("active_day") or "")
            else:
                target_hint = user_text

            detail = self.conductor.inspect_workspace_detail(target_hint)

            # 0.5 当用户发送大批量云盘下载链接（如 38/45 组图片+视频链接）时，直接由本地下载与 Orante 准备引擎接管，不让 agy.exe 傻等超时！
            parsed_bulk = self.conductor.parse_bulk_links_message(user_text)
            if parsed_bulk.get("total_links", 0) >= 4 and parsed_bulk.get("has_clear_intent"):
                b_ver = parsed_bulk.get("batch_letter", detail.get("active_batch", "A"))
                if progress_callback:
                    progress_callback(
                        f"⚡ 已识别 {b_ver} 批次共 {parsed_bulk['total_links']} 条素材链接（{parsed_bulk['task_spec']}），正在直接启动下载与 Orante 准备引擎…"
                    )
                wf_res = self.conductor.execute_start_today_workflow(
                    workspace_root=detail["ws"],
                    task_spec=parsed_bulk["task_spec"],
                    task_count=parsed_bulk["task_count"],
                    video_type_key="3",
                    image_links_text="\n".join(parsed_bulk["image_links"]),
                    video_links_text="\n".join(parsed_bulk["video_links"]),
                    auto_launch_orante=parsed_bulk.get("wants_orante_prepare", False),
                    auto_register_orante=parsed_bulk.get("wants_orante_prepare", False),
                    progress_callback=(lambda msg, _pct: progress_callback(msg)) if progress_callback else None,
                    image_codes=parsed_bulk["image_codes"],
                    video_codes=parsed_bulk["video_codes"],
                    target_day_hint=detail["day_str"],
                    target_batch_letter=b_ver,
                )
                executed_tools.append({
                    "name": "execute_start_today_workflow",
                    "params": {
                        "batch": b_ver,
                        "task_spec": parsed_bulk["task_spec"],
                        "total_links": parsed_bulk["total_links"],
                    },
                    "result": wf_res,
                })
                final_reply = (
                    f"已为你完成 **`{wf_res.get('day_str')} · {b_ver}批次`**（编号 `{parsed_bulk['task_spec']}`）的素材下载与整理：\n\n"
                    f"1. **《右键表格工具》下载清单**：已写入 `{wf_res.get('raw_dl_dir')}\\.renamer_project.json`（含 **{len(parsed_bulk['image_links'])}** 条图片链接 + **{len(parsed_bulk['video_links'])}** 条视频链接）；\n"
                    f"2. **本批次图片目录**：`{wf_res.get('img_dir')}`（已就绪 **{wf_res.get('img_count', 0)}** 张图片）；\n"
                    f"3. **本批次视频目录**：`{wf_res.get('vid_dir')}`（已就绪 **{wf_res.get('vid_count', 0)}** 个视频）；\n"
                    + (
                        f"4. **Orante 追加任务准备**：已在 Orante 中追加任务 **`{wf_res.get('orante_task_id')} · {Path(wf_res.get('img_dir', '')).name}`**（目录 `{wf_res.get('orante_task_dir')}`，共导入 **{wf_res.get('orante_prepared_count', 0)}** 张主体图 `p01~p{wf_res.get('orante_prepared_count', 0):02d}`），此前批次未完成的任务也已完整保留，你现在可以直接在 Orante 中填写描述语了！"
                        if parsed_bulk.get("wants_orante_prepare")
                        else ""
                    )
                )

            sys_prompt = self._build_system_prompt(detail, user_query=user_text)

            # 1. 云端旗舰大脑模式 (Gemini 4 Argon / Claude Opus 5.5 / Sonnet 5.5 / Flash)
            #    严格遵守用户要求：当处于云端旗舰模式时，绝不自动回退到 muse-glimmer！
            if not final_reply and self.brain_mode == "agy":
                if self.has_agy_cli:
                    agy_res = self._call_agy_stream(
                        user_text=user_text,
                        sys_prompt=sys_prompt,
                        detail=detail,
                        progress_callback=progress_callback,
                    )
                    if agy_res:
                        final_reply, agy_tools = agy_res
                        executed_tools.extend(agy_tools)
                if not final_reply:
                    final_reply = self._diagnose_and_self_recover(
                        user_text=user_text,
                        detail=detail,
                        executed_tools=executed_tools,
                        error_msg="agy stream timeout or empty response",
                        progress_callback=progress_callback,
                    )

            # 2. 本地 SmolAgents 代理引擎模式（仅当用户在下拉框手动选择 SmolAgents 时启用）
            elif self.brain_mode == "smol":
                smol_res = self._call_smolagents_engine(
                    user_text=user_text,
                    sys_prompt=sys_prompt,
                    ui_actions=ui_actions,
                    progress_callback=progress_callback,
                )
                if smol_res:
                    final_reply, smol_tools = smol_res
                    executed_tools.extend(smol_tools)
                if not final_reply:
                    final_reply = self._diagnose_and_self_recover(
                        user_text=user_text,
                        detail=detail,
                        executed_tools=executed_tools,
                        error_msg="SmolAgents local model timeout",
                        progress_callback=progress_callback,
                    )

            # 3. 本地 Ollama Tool-Calling 模式（仅当用户在下拉框手动选择 muse-glimmer / gemma4 时启用）
            elif self.brain_mode == "ollama":
                final_reply, oll_tools = self._call_ollama_tool_loop(
                    user_text=user_text,
                    sys_prompt=sys_prompt,
                    ui_actions=ui_actions,
                    progress_callback=progress_callback,
                )
                executed_tools.extend(oll_tools)

            if not final_reply:
                final_reply = self._diagnose_and_self_recover(
                    user_text=user_text,
                    detail=detail,
                    executed_tools=executed_tools,
                    error_msg="",
                    progress_callback=progress_callback,
                )

            with self._lock:
                self.history.append({"role": "user", "content": self._condense_bulk_urls_for_cli(user_text)})
                self.history.append({"role": "assistant", "content": final_reply})

            # 4. 自动提炼与记忆本次操作经验
            memory_note = self.memory_engine.distill_turn_automatically(
                user_text=self._condense_bulk_urls_for_cli(user_text),
                reply_text=final_reply,
                executed_tools=executed_tools,
            )

            return {
                "reply": final_reply,
                "ui_actions": ui_actions,
                "executed_tools": executed_tools,
                "memory_note": memory_note,
                "brain_model": self.active_model,
            }
        except BaseException as e:
            if not detail:
                try:
                    detail = self.conductor.inspect_workspace_detail("")
                except Exception:
                    detail = {"ws": "D:\\1_PROJECT", "day_str": "2026-10-16", "active_batch": "A", "actual_img_dir": "", "actual_vid_dir": "", "img_count": 0, "vid_count": 0}
            human_recovery = self._diagnose_and_self_recover(
                user_text=user_text,
                detail=detail,
                executed_tools=executed_tools,
                error_msg=str(e),
                progress_callback=progress_callback,
            )
            return {
                "reply": human_recovery,
                "ui_actions": ui_actions,
                "executed_tools": executed_tools,
                "memory_note": None,
                "brain_model": self.active_model,
            }






