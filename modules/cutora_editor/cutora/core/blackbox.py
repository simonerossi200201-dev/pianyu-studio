# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Aviation-Grade Flight Data Recorder ("黑匣子" / Blackbox), Hardware Adaptation
& Developer Optimization Advisor, and Post-Mortem Hard-Lock / Crash Forensics.

Designed with three guarantees:
1. Survives forced power-off / GPU TDR / BSOD:
   - ``session_state.json`` is armed (`clean_exit: False`) and fsynced at startup.
   - ``flight_breadcrumbs.jsonl`` writes `START` before heavy C++/CUDA/FFmpeg/Qt
     operations with immediate ``os.fsync()``, and `END` when finished. If the
     machine freezes and the user holds the power button, the last unclosed `START`
     pinpoints the exact operation, parameters, and resource state at the moment of lockup.
   - ``telemetry_anomalies.jsonl`` records resource spikes (low RAM, high pagefile
     commit charge, high VRAM, GPU thermal/power limit, UI stall > 500ms) with ``os.fsync()``.
2. Hardware Adaptation & Developer Optimization Advisor:
   - Profiles CPU, RAM, Pagefile/Commit, GPU(s), Disk type/free space, Rust core,
     FFmpeg encoders, and ASR compute_type.
   - Computes both a safe optimal runtime profile for the current machine AND concrete
     engineering recommendations for the developer on how to optimize the software
     for this specific hardware model.
3. One-Click & Automatic Blackbox Zip Bundle:
   - Packages human-readable Markdown + machine-readable JSON reports, logs,
     breadcrumbs, anomalies, and Windows Event Log forensics into a single `.zip`.
"""
from __future__ import annotations

import contextlib
import ctypes
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import uuid
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from cutora.core.log import get_logger, redact_text
from cutora.core.paths import REPO_ROOT, resolve_user_data, user_logs_dir

logger = get_logger("cutora.core.blackbox")
IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"
TZ8 = timezone(timedelta(hours=8))

BLACKBOX_DIR_NAME = "blackbox"
SESSION_STATE_FILE = "session_state.json"
LAST_UNCLEAN_SESSION_FILE = "last_unclean_session.json"
BREADCRUMBS_FILE = "flight_breadcrumbs.jsonl"
PREV_BREADCRUMBS_FILE = "prev_flight_breadcrumbs.jsonl"
ANOMALIES_FILE = "telemetry_anomalies.jsonl"
PREV_ANOMALIES_FILE = "prev_telemetry_anomalies.jsonl"
HARDWARE_ADVISOR_JSON = "hardware_and_advisor.json"
HARDWARE_ADVISOR_MD = "developer_optimization_report.md"
POST_MORTEM_JSON = "post_mortem_diagnosis.json"
POST_MORTEM_MD = "post_mortem_diagnosis.md"

_MAX_JSONL_LINES = 600


def _now_iso() -> str:
    return datetime.now(TZ8).isoformat(timespec="milliseconds")


def blackbox_dir(base_logs_dir: str | None = None) -> str:
    root = base_logs_dir or user_logs_dir()
    path = os.path.join(root, BLACKBOX_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def _fsync_write_json(path: str, data: dict[str, Any]) -> None:
    """Atomically write JSON with os.fsync so it survives power loss."""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp.%d" % os.getpid()
    raw = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    with open(tmp, "wb") as fh:
        fh.write(raw)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


def _fsync_append_jsonl(path: str, record: dict[str, Any], sync: bool = True) -> None:
    """Append one JSONL line and optionally os.fsync immediately."""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    with open(path, "ab") as fh:
        fh.write(line)
        fh.flush()
        if sync:
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass


def _trim_jsonl_file(path: str, max_lines: int = _MAX_JSONL_LINES) -> None:
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        if len(lines) <= max_lines:
            return
        kept = lines[-max_lines:]
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.writelines(kept)
            fh.flush()
    except Exception:
        pass


def _read_jsonl(path: str, max_items: int = 500) -> list[dict[str, Any]]:
    if not path or not os.path.isfile(path):
        return []
    out: list[dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        for line in lines[-max_items:]:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    out.append(obj)
            except Exception:
                continue
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# Fast, Zero-Torch System Resource Snapshot
# ---------------------------------------------------------------------------

def _macos_mem_snapshot(res: dict[str, float]) -> None:
    """Populate physical Unified Memory, swap usage, and process RSS on macOS."""
    g = 1024.0 ** 3
    try:
        p_tot = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=2)
        tot_bytes = int((p_tot.stdout or "").strip() or 0)
        if tot_bytes > 0:
            tot_p = tot_bytes / g
            res["ram_total_gb"] = round(tot_p, 2)
            p_vm = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=2)
            out = p_vm.stdout or ""
            page_size = 16384 if platform.machine().lower() in ("arm64", "aarch64") else 4096
            for line in out.splitlines():
                if "page size of" in line:
                    for tok in line.split():
                        if tok.isdigit():
                            page_size = int(tok)
                            break
            avail_pages = 0
            for line in out.splitlines():
                if any(k in line for k in ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable")):
                    parts = line.split(":")
                    if len(parts) == 2:
                        val = parts[1].strip().rstrip(".")
                        if val.isdigit():
                            avail_pages += int(val)
            avl_p = (avail_pages * page_size) / g if avail_pages > 0 else tot_p * 0.5
            res["ram_avail_gb"] = round(avl_p, 2)
            res["ram_used_pct"] = round(max(0.0, (tot_p - avl_p) * 100.0 / tot_p), 1)
    except Exception:
        pass
    try:
        # Query vm.swapusage: "vm.swapusage: total = 2048.00M  used = 512.00M  free = 1536.00M  (encrypted)"
        p_swp = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True, timeout=2)
        swp_out = (p_swp.stdout or "").strip()
        swap_tot_gb = 0.0
        swap_free_gb = 0.0
        for part in swp_out.replace("=", " ").split():
            if part.endswith("M") or part.endswith("G"):
                pass
        import re
        m_tot = re.search(r"total\s*=\s*([0-9.]+)([MG])", swp_out)
        m_free = re.search(r"free\s*=\s*([0-9.]+)([MG])", swp_out)
        if m_tot:
            swap_tot_gb = float(m_tot.group(1)) / (1024.0 if m_tot.group(2) == "M" else 1.0)
        if m_free:
            swap_free_gb = float(m_free.group(1)) / (1024.0 if m_free.group(2) == "M" else 1.0)
        tot_c = res["ram_total_gb"] + swap_tot_gb
        avl_c = res["ram_avail_gb"] + swap_free_gb
        res["commit_total_gb"] = round(tot_c, 2)
        res["commit_avail_gb"] = round(avl_c, 2)
        if tot_c > 0:
            res["commit_used_pct"] = round(max(0.0, (tot_c - avl_c) * 100.0 / tot_c), 1)
    except Exception:
        pass
    try:
        import resource
        ru = resource.getrusage(resource.RUSAGE_SELF)
        # ru_maxrss is in bytes on macOS
        res["proc_rss_mb"] = round(float(ru.ru_maxrss) / (1024.0 * 1024.0), 1)
    except Exception:
        pass


def query_memory_snapshot() -> dict[str, float]:
    """Return physical RAM and Windows Commit Charge (pagefile) in GB, plus process RSS."""
    res = {
        "ram_total_gb": 0.0,
        "ram_avail_gb": 0.0,
        "ram_used_pct": 0.0,
        "commit_total_gb": 0.0,
        "commit_avail_gb": 0.0,
        "commit_used_pct": 0.0,
        "proc_rss_mb": 0.0,
    }
    try:
        if IS_WINDOWS:
            class _MEMSTAT(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            st = _MEMSTAT()
            st.dwLength = ctypes.sizeof(_MEMSTAT)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                g = 1024.0 ** 3
                tot_p = st.ullTotalPhys / g
                avl_p = st.ullAvailPhys / g
                tot_c = st.ullTotalPageFile / g
                avl_c = st.ullAvailPageFile / g
                res["ram_total_gb"] = round(tot_p, 2)
                res["ram_avail_gb"] = round(avl_p, 2)
                res["ram_used_pct"] = round(float(st.dwMemoryLoad), 1)
                res["commit_total_gb"] = round(tot_c, 2)
                res["commit_avail_gb"] = round(avl_c, 2)
                if tot_c > 0:
                    res["commit_used_pct"] = round((tot_c - avl_c) * 100.0 / tot_c, 1)

            # Process RSS via GetProcessMemoryInfo
            class _PMC(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.c_ulong),
                    ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            pmc = _PMC()
            pmc.cb = ctypes.sizeof(_PMC)
            hproc = ctypes.windll.kernel32.GetCurrentProcess()
            if ctypes.windll.psapi.GetProcessMemoryInfo(hproc, ctypes.byref(pmc), pmc.cb):
                res["proc_rss_mb"] = round(pmc.WorkingSetSize / (1024.0 * 1024.0), 1)
        elif IS_MACOS:
            _macos_mem_snapshot(res)
    except Exception:
        pass
    return res


_GPU_SNAP_CACHE: dict[str, Any] = {"t": 0.0, "data": None}


def _macos_gpu_snapshot() -> dict[str, Any] | None:
    """Query Apple Silicon Metal GPU and Unified Memory telemetry via sysctl + ioreg."""
    try:
        p_brand = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True, text=True, timeout=2,
        )
        chip_name = (p_brand.stdout or "").strip() or "Apple Silicon GPU"
        mem = query_memory_snapshot()
        # Apple Silicon allows up to ~75% of Unified Memory to be wired by Metal GPU
        tot_gb = round(mem.get("ram_total_gb", 0.0) * 0.75, 2)
        free_gb = round(min(tot_gb, mem.get("ram_avail_gb", 0.0)), 2)
        used_gb = round(max(0.0, tot_gb - free_gb), 2)
        gpu_util = 0.0
        try:
            p_io = subprocess.run(
                ["ioreg", "-r", "-d", "1", "-w", "0", "-c", "IOAccelerator"],
                capture_output=True, text=True, timeout=2,
            )
            io_out = p_io.stdout or ""
            import re
            m_util = re.search(r'"Device Utilization %"\s*=\s*(\d+)', io_out)
            if m_util:
                gpu_util = float(m_util.group(1))
            m_mem = re.search(r'"In use system memory"\s*=\s*(\d+)', io_out)
            if m_mem:
                used_gb = round(int(m_mem.group(1)) / (1024.0 ** 3), 2)
                # Apple Silicon 统一内存：可用 GPU 内存绝不能超过当前系统真实可用物理内存 (ram_avail_gb)
                free_gb = round(min(max(0.0, tot_gb - used_gb), mem.get("ram_avail_gb", 0.0)), 2)
        except Exception:
            pass
        return {
            "name": "%s (Unified Memory + VideoToolbox)" % chip_name,
            "driver": "Metal/VideoToolbox",
            "vram_total_gb": tot_gb,
            "vram_used_gb": used_gb,
            "vram_free_gb": free_gb,
            "vram_used_pct": round(used_gb * 100.0 / tot_gb, 1) if tot_gb > 0 else 0.0,
            "gpu_util_pct": gpu_util,
            "temp_c": 0.0,
            "power_w": 0.0,
            "power_limit_w": 0.0,
        }
    except Exception:
        return None


def query_gpu_snapshot(max_age: float = 2.0) -> dict[str, Any] | None:
    """Query nvidia-smi (or macOS Apple Silicon Metal/Unified Memory) for GPU telemetry."""
    now = time.monotonic()
    if _GPU_SNAP_CACHE["data"] is not None and (now - _GPU_SNAP_CACHE["t"]) < max_age:
        return _GPU_SNAP_CACHE["data"]
    if IS_MACOS:
        data = _macos_gpu_snapshot()
        _GPU_SNAP_CACHE["t"] = now
        _GPU_SNAP_CACHE["data"] = data
        return data
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu,power.draw,power.limit",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=flags,
        )
        if proc.returncode != 0 or not (proc.stdout or "").strip():
            _GPU_SNAP_CACHE["t"] = now
            _GPU_SNAP_CACHE["data"] = None
            return None
        first = proc.stdout.strip().splitlines()[0]
        parts = [p.strip() for p in first.split(",")]
        if len(parts) < 5:
            return None

        def _f(idx: int, scale: float = 1.0) -> float:
            if idx >= len(parts):
                return 0.0
            try:
                return round(float(parts[idx]) * scale, 2)
            except Exception:
                return 0.0

        tot_gb = _f(2, 1.0 / 1024.0)
        used_gb = _f(3, 1.0 / 1024.0)
        free_gb = _f(4, 1.0 / 1024.0)
        data = {
            "name": parts[0],
            "driver": parts[1],
            "vram_total_gb": tot_gb,
            "vram_used_gb": used_gb,
            "vram_free_gb": free_gb,
            "vram_used_pct": round(used_gb * 100.0 / tot_gb, 1) if tot_gb > 0 else 0.0,
            "gpu_util_pct": _f(5),
            "temp_c": _f(6),
            "power_w": _f(7),
            "power_limit_w": _f(8),
        }
        _GPU_SNAP_CACHE["t"] = now
        _GPU_SNAP_CACHE["data"] = data
        return data
    except Exception:
        _GPU_SNAP_CACHE["t"] = now
        _GPU_SNAP_CACHE["data"] = None
        return None


def compact_resource_snapshot(include_gpu: bool = True) -> dict[str, Any]:
    snap = query_memory_snapshot()
    snap["py_threads"] = threading.active_count()
    if include_gpu:
        gpu = query_gpu_snapshot(max_age=2.5)
        if gpu:
            snap["vram_used_gb"] = gpu.get("vram_used_gb", 0.0)
            snap["vram_free_gb"] = gpu.get("vram_free_gb", 0.0)
            snap["gpu_util_pct"] = gpu.get("gpu_util_pct", 0.0)
            snap["gpu_temp_c"] = gpu.get("temp_c", 0.0)
    return snap


# ---------------------------------------------------------------------------
# Flight Recorder Singleton (Pre-Crash Breadcrumbs + Anomaly Ring Buffer)
# ---------------------------------------------------------------------------

class FlightRecorder:
    """Persistent flight recorder for CUTORA sessions."""

    def __init__(self, base_logs_dir: str | None = None) -> None:
        self._base_logs_dir = base_logs_dir
        self._dir = blackbox_dir(base_logs_dir)
        self.session_id = datetime.now(TZ8).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.pid = os.getpid()
        self.started_at = _now_iso()
        self._lock = threading.Lock()
        self._active_ops: dict[str, dict[str, Any]] = {}
        self._cmd_stats: dict[str, dict[str, Any]] = {}
        self._ui_stall_count = 0
        self._max_ui_stall_s = 0.0
        self._anomaly_count = 0
        self._monitor_stop = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._unclean_prev_session: dict[str, Any] | None = None
        self._post_mortem_report: dict[str, Any] | None = None

    @property
    def dir_path(self) -> str:
        return self._dir

    @property
    def session_state_path(self) -> str:
        return os.path.join(self._dir, SESSION_STATE_FILE)

    @property
    def breadcrumbs_path(self) -> str:
        return os.path.join(self._dir, BREADCRUMBS_FILE)

    @property
    def anomalies_path(self) -> str:
        return os.path.join(self._dir, ANOMALIES_FILE)

    def start_session(self, start_monitor: bool = True) -> dict[str, Any] | None:
        """Check if previous session crashed/hard-locked, rotate logs, and arm current session."""
        with self._lock:
            prev = self._check_previous_session()
            if prev is not None:
                self._unclean_prev_session = prev
            # Rotate current breadcrumbs/anomalies to prev_* if previous session was unclean
            if os.path.isfile(self.breadcrumbs_path):
                if prev is not None:
                    try:
                        shutil.copy2(
                            self.breadcrumbs_path,
                            os.path.join(self._dir, PREV_BREADCRUMBS_FILE),
                        )
                    except Exception:
                        pass
                _trim_jsonl_file(self.breadcrumbs_path, max_lines=300)
            if os.path.isfile(self.anomalies_path):
                if prev is not None:
                    try:
                        shutil.copy2(
                            self.anomalies_path,
                            os.path.join(self._dir, PREV_ANOMALIES_FILE),
                        )
                    except Exception:
                        pass
                _trim_jsonl_file(self.anomalies_path, max_lines=300)

            self._main_window_ready = False
            state = {
                "session_id": self.session_id,
                "pid": self.pid,
                "hostname": socket.gethostname(),
                "started_at": self.started_at,
                "last_heartbeat": _now_iso(),
                "main_window_ready": False,
                "clean_exit": False,
                "active_ops": [],
                "ui_stall_count": 0,
                "max_ui_stall_s": 0.0,
                "anomaly_count": 0,
                "initial_resources": compact_resource_snapshot(include_gpu=True),
            }
            _fsync_write_json(self.session_state_path, state)
            _fsync_append_jsonl(
                self.breadcrumbs_path,
                {
                    "ts": _now_iso(),
                    "session_id": self.session_id,
                    "event": "SESSION_START",
                    "pid": self.pid,
                    "res": state["initial_resources"],
                },
                sync=True,
            )

        if prev is not None:
            try:
                self._post_mortem_report = analyze_post_mortem(
                    unclean_session=prev,
                    base_logs_dir=self._base_logs_dir,
                    include_win_events=start_monitor,
                )
            except Exception:
                logger.exception("自动生成上次非正常退出黑匣子诊断报告失败")

        if start_monitor:
            self._start_background_monitor()
        return prev

    def _check_previous_session(self) -> dict[str, Any] | None:
        if not os.path.isfile(self.session_state_path):
            return None
        try:
            with open(self.session_state_path, "r", encoding="utf-8") as fh:
                prev = json.load(fh)
            if not isinstance(prev, dict):
                return None
            if prev.get("clean_exit", True):
                return None
            # Check if that PID is still alive (another instance)
            prev_pid = int(prev.get("pid") or 0)
            if prev_pid > 0 and prev_pid != self.pid:
                try:
                    from cutora.render.export_guard import pid_alive
                    if pid_alive(prev_pid):
                        return None
                except Exception:
                    pass
            # Archive unclean session state
            last_unclean_path = os.path.join(self._dir, LAST_UNCLEAN_SESSION_FILE)
            _fsync_write_json(last_unclean_path, prev)
            return prev
        except Exception:
            return None

    def mark_main_window_ready(self) -> None:
        self._main_window_ready = True
        with self._lock:
            try:
                state = {}
                if os.path.isfile(self.session_state_path):
                    with open(self.session_state_path, "r", encoding="utf-8") as fh:
                        state = json.load(fh) or {}
                state["main_window_ready"] = True
                state["main_window_ready_at"] = _now_iso()
                _fsync_write_json(self.session_state_path, state)
                _fsync_append_jsonl(
                    self.breadcrumbs_path,
                    {
                        "ts": _now_iso(),
                        "session_id": self.session_id,
                        "event": "MAIN_WINDOW_READY",
                        "pid": self.pid,
                    },
                    sync=True,
                )
            except Exception:
                pass

    def mark_clean_exit(self, reason: str = "normal") -> None:
        self._monitor_stop.set()
        with self._lock:
            try:
                state = {}
                if os.path.isfile(self.session_state_path):
                    with open(self.session_state_path, "r", encoding="utf-8") as fh:
                        state = json.load(fh) or {}
                if state.get("clean_exit") and reason == "atexit":
                    return
                main_ready = bool(getattr(self, "_main_window_ready", False) or state.get("main_window_ready"))
                # 若仅由 atexit 触发但主窗口从未就绪，不能算作正常 GUI 会话退出
                is_clean = True if (reason != "atexit" or main_ready) else False
                eff_reason = reason if is_clean else "atexit_before_main_window_ready"
                state["clean_exit"] = is_clean
                state["ended_at"] = _now_iso()
                state["exit_reason"] = eff_reason
                state["active_ops"] = []
                state["ui_stall_count"] = self._ui_stall_count
                state["max_ui_stall_s"] = round(self._max_ui_stall_s, 2)
                state["anomaly_count"] = self._anomaly_count
                _fsync_write_json(self.session_state_path, state)
                _fsync_append_jsonl(
                    self.breadcrumbs_path,
                    {
                        "ts": _now_iso(),
                        "session_id": self.session_id,
                        "event": "SESSION_CLEAN_EXIT" if is_clean else "SESSION_EARLY_EXIT",
                        "reason": eff_reason,
                        "res": compact_resource_snapshot(include_gpu=False),
                    },
                    sync=True,
                )
            except Exception:
                pass

    def begin_op(
        self,
        op_name: str,
        detail: dict[str, Any] | str | None = None,
        *,
        sync: bool = True,
        include_gpu: bool = True,
    ) -> str:
        """Record OP_START with immediate fsync BEFORE entering a heavy/risky call."""
        op_id = "%s#%s" % (op_name, uuid.uuid4().hex[:8])
        snap = compact_resource_snapshot(include_gpu=include_gpu)
        if isinstance(detail, dict):
            safe_detail = {str(k): redact_text(str(v))[:240] for k, v in detail.items()}
        elif detail is not None:
            safe_detail = {"info": redact_text(str(detail))[:240]}
        else:
            safe_detail = {}

        entry = {
            "ts": _now_iso(),
            "session_id": self.session_id,
            "event": "OP_START",
            "op_id": op_id,
            "op": op_name,
            "detail": safe_detail,
            "res": snap,
            "t0": time.perf_counter(),
        }
        with self._lock:
            self._active_ops[op_id] = {
                "op_id": op_id,
                "op": op_name,
                "started_at": entry["ts"],
                "detail": safe_detail,
                "res_at_start": snap,
                "t0": entry["t0"],
            }
            self._flush_active_ops_to_session_locked()
            disk_entry = {k: v for k, v in entry.items() if k != "t0"}
            _fsync_append_jsonl(self.breadcrumbs_path, disk_entry, sync=sync)
        return op_id

    def end_op(
        self,
        op_id: str,
        *,
        ok: bool = True,
        error: str | None = None,
        extra: dict[str, Any] | None = None,
        sync: bool = False,
    ) -> None:
        """Record OP_END and update performance statistics."""
        with self._lock:
            started = self._active_ops.pop(op_id, None)
            t0 = started.get("t0", time.perf_counter()) if started else time.perf_counter()
            op_name = started.get("op", op_id.split("#")[0]) if started else op_id.split("#")[0]
            ms = round((time.perf_counter() - t0) * 1000.0, 2)
            snap = compact_resource_snapshot(include_gpu=False)

            stat = self._cmd_stats.setdefault(
                op_name,
                {"count": 0, "fail_count": 0, "total_ms": 0.0, "max_ms": 0.0, "samples_ms": []},
            )
            stat["count"] += 1
            if not ok:
                stat["fail_count"] += 1
            stat["total_ms"] = round(stat["total_ms"] + ms, 2)
            if ms > stat["max_ms"]:
                stat["max_ms"] = ms
            samples = stat["samples_ms"]
            samples.append(ms)
            if len(samples) > 50:
                del samples[0]

            self._flush_active_ops_to_session_locked()
            record: dict[str, Any] = {
                "ts": _now_iso(),
                "session_id": self.session_id,
                "event": "OP_END",
                "op_id": op_id,
                "op": op_name,
                "ok": bool(ok),
                "ms": ms,
                "res": snap,
            }
            if error:
                record["error"] = redact_text(str(error))[:400]
            if extra:
                record["extra"] = extra
            _fsync_append_jsonl(self.breadcrumbs_path, record, sync=sync or (not ok))

    @contextlib.contextmanager
    def operation(
        self,
        op_name: str,
        detail: dict[str, Any] | str | None = None,
        *,
        sync: bool = True,
        include_gpu: bool = True,
    ):
        op_id = self.begin_op(op_name, detail, sync=sync, include_gpu=include_gpu)
        try:
            yield op_id
        except Exception as exc:
            self.end_op(op_id, ok=False, error="%s: %s" % (type(exc).__name__, exc), sync=True)
            raise
        else:
            self.end_op(op_id, ok=True, sync=False)

    def record_ui_stall(self, stall_seconds: float, stack_text: str) -> None:
        """Called by StallWatchdog when the Qt main thread freezes."""
        with self._lock:
            self._ui_stall_count += 1
            if stall_seconds > self._max_ui_stall_s:
                self._max_ui_stall_s = stall_seconds
            self._anomaly_count += 1
            snap = compact_resource_snapshot(include_gpu=True)
            active = [
                {"op": v["op"], "started_at": v["started_at"], "detail": v["detail"]}
                for v in self._active_ops.values()
            ]
            rec = {
                "ts": _now_iso(),
                "session_id": self.session_id,
                "type": "UI_STALL",
                "stall_s": round(float(stall_seconds), 2),
                "active_ops": active,
                "res": snap,
                "stack": (stack_text or "")[-1800:],
            }
            _fsync_append_jsonl(self.anomalies_path, rec, sync=True)
            self._flush_active_ops_to_session_locked()

    def record_anomaly(
        self,
        anomaly_type: str,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Record any hardware/overload anomaly (e.g., low RAM, pagefile exhaustion, VRAM full)."""
        with self._lock:
            self._anomaly_count += 1
            snap = compact_resource_snapshot(include_gpu=True)
            active = [
                {"op": v["op"], "started_at": v["started_at"], "detail": v["detail"]}
                for v in self._active_ops.values()
            ]
            rec = {
                "ts": _now_iso(),
                "session_id": self.session_id,
                "type": anomaly_type,
                "message": message,
                "detail": detail or {},
                "active_ops": active,
                "res": snap,
            }
            _fsync_append_jsonl(self.anomalies_path, rec, sync=True)
            self._flush_active_ops_to_session_locked()

    def _flush_active_ops_to_session_locked(self) -> None:
        try:
            active_list = [
                {
                    "op_id": v["op_id"],
                    "op": v["op"],
                    "started_at": v["started_at"],
                    "detail": v["detail"],
                    "res_at_start": v["res_at_start"],
                }
                for v in self._active_ops.values()
            ]
            state = {
                "session_id": self.session_id,
                "pid": self.pid,
                "hostname": socket.gethostname(),
                "started_at": self.started_at,
                "last_heartbeat": _now_iso(),
                "main_window_ready": getattr(self, "_main_window_ready", False),
                "clean_exit": False,
                "active_ops": active_list,
                "ui_stall_count": self._ui_stall_count,
                "max_ui_stall_s": round(self._max_ui_stall_s, 2),
                "anomaly_count": self._anomaly_count,
                "latest_resources": compact_resource_snapshot(include_gpu=False),
            }
            _fsync_write_json(self.session_state_path, state)
        except Exception:
            pass

    def _start_background_monitor(self) -> None:
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            return
        self._monitor_stop.clear()
        t = threading.Thread(target=self._monitor_loop, name="blackbox-telemetry", daemon=True)
        self._monitor_thread = t
        t.start()

    def _monitor_loop(self) -> None:
        last_anomaly_ts = 0.0
        while not self._monitor_stop.wait(5.0):
            try:
                mem = query_memory_snapshot()
                gpu = query_gpu_snapshot(max_age=4.5)
                now = time.monotonic()

                # Check overload thresholds
                reasons = []
                ram_avail = mem.get("ram_avail_gb", 99.0)
                commit_pct = mem.get("commit_used_pct", 0.0)
                if 0 < ram_avail < 2.2:
                    reasons.append("物理可用内存极低 (仅剩 %.2f GB)" % ram_avail)
                if commit_pct >= 90.0:
                    reasons.append("系统虚拟内存提交量接近极限 (%.1f%%)" % commit_pct)
                if gpu:
                    vram_free = gpu.get("vram_free_gb", 99.0)
                    vram_pct = gpu.get("vram_used_pct", 0.0)
                    temp_c = gpu.get("temp_c", 0.0)
                    if gpu.get("vram_total_gb", 0) > 0 and (vram_free < 0.45 or vram_pct >= 95.0):
                        reasons.append("显卡显存接近耗尽 (已用 %.1f%%, 仅剩 %.2f GB)" % (vram_pct, vram_free))
                    if temp_c >= 86.0:
                        reasons.append("显卡温度过高 (%.0f°C，存在降频或驱动重置风险)" % temp_c)

                if reasons and (now - last_anomaly_ts) >= 15.0:
                    last_anomaly_ts = now
                    self.record_anomaly(
                        "RESOURCE_PRESSURE",
                        "；".join(reasons),
                        {"mem": mem, "gpu": gpu or {}},
                    )
                else:
                    with self._lock:
                        self._flush_active_ops_to_session_locked()
            except Exception:
                pass

    def performance_summary(self) -> dict[str, Any]:
        with self._lock:
            ops_summary = {}
            for name, st in sorted(self._cmd_stats.items()):
                samples = sorted(st.get("samples_ms") or [])
                p95 = samples[int(len(samples) * 0.95)] if samples else 0.0
                avg = (st["total_ms"] / st["count"]) if st["count"] else 0.0
                ops_summary[name] = {
                    "count": st["count"],
                    "fail_count": st["fail_count"],
                    "avg_ms": round(avg, 2),
                    "p95_ms": round(p95, 2),
                    "max_ms": round(st["max_ms"], 2),
                }
            return {
                "session_id": self.session_id,
                "ui_stall_count": self._ui_stall_count,
                "max_ui_stall_s": round(self._max_ui_stall_s, 2),
                "anomaly_count": self._anomaly_count,
                "operations": ops_summary,
            }


_RECORDER: FlightRecorder | None = None
_RECORDER_LOCK = threading.Lock()


def get_recorder(base_logs_dir: str | None = None) -> FlightRecorder:
    global _RECORDER
    with _RECORDER_LOCK:
        if _RECORDER is None:
            _RECORDER = FlightRecorder(base_logs_dir=base_logs_dir)
        return _RECORDER


def install_blackbox(start_monitor: bool = True, base_logs_dir: str | None = None) -> FlightRecorder:
    """Initialize session flight recorder, register atexit clean-shutdown hook."""
    import atexit
    rec = get_recorder(base_logs_dir=base_logs_dir)
    rec.start_session(start_monitor=start_monitor)
    atexit.register(lambda: rec.mark_clean_exit("atexit"))
    return rec


# ---------------------------------------------------------------------------
# Hardware Adaptation & Developer Performance Advisor
# ---------------------------------------------------------------------------

def _probe_cpu_info() -> dict[str, Any]:
    info: dict[str, Any] = {
        "processor": platform.processor() or "unknown",
        "machine": platform.machine() or "unknown",
        "logical_cores": os.cpu_count() or 1,
        "name": "",
    }
    if IS_WINDOWS:
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\Description\System\CentralProcessor\0",
            ) as key:
                val, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                info["name"] = str(val or "").strip()
        except Exception:
            pass
    elif IS_MACOS:
        try:
            proc = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=2,
            )
            info["name"] = (proc.stdout or "").strip()
        except Exception:
            pass
    if not info["name"]:
        info["name"] = info["processor"]
    return info


def _probe_all_gpus() -> list[dict[str, Any]]:
    gpus: list[dict[str, Any]] = []
    if IS_MACOS:
        snap = _macos_gpu_snapshot()
        if snap:
            gpus.append({
                "vendor": "Apple",
                "index": "0",
                "name": snap.get("name", "Apple Silicon GPU"),
                "driver": snap.get("driver", "Metal/VideoToolbox"),
                "vram_total_gb": snap.get("vram_total_gb", 0.0),
                "vram_free_gb": snap.get("vram_free_gb", 0.0),
                "compute_cap": "mps",
                "power_limit_w": "",
            })
        return gpus
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,driver_version,memory.total,memory.free,compute_cap,power.limit",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=4,
            creationflags=flags,
        )
        if proc.returncode == 0 and (proc.stdout or "").strip():
            for line in proc.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 5:
                    try:
                        vram_tot = round(float(parts[3]) / 1024.0, 2)
                    except Exception:
                        vram_tot = 0.0
                    try:
                        vram_free = round(float(parts[4]) / 1024.0, 2)
                    except Exception:
                        vram_free = 0.0
                    gpus.append({
                        "vendor": "NVIDIA",
                        "index": parts[0],
                        "name": parts[1],
                        "driver": parts[2],
                        "vram_total_gb": vram_tot,
                        "vram_free_gb": vram_free,
                        "compute_cap": parts[5] if len(parts) > 5 else "",
                        "power_limit_w": parts[6] if len(parts) > 6 else "",
                    })
    except Exception:
        pass
    return gpus


def _probe_disk_info(target_path: str | None = None) -> dict[str, Any]:
    path = target_path or REPO_ROOT
    try:
        usage = shutil.disk_usage(path)
        g = 1024.0 ** 3
        drive = os.path.splitdrive(os.path.abspath(path))[0] or path
        return {
            "drive": drive,
            "total_gb": round(usage.total / g, 2),
            "used_gb": round(usage.used / g, 2),
            "free_gb": round(usage.free / g, 2),
            "free_pct": round(usage.free * 100.0 / usage.total, 1) if usage.total > 0 else 0.0,
        }
    except Exception:
        return {"drive": "", "total_gb": 0.0, "used_gb": 0.0, "free_gb": 0.0, "free_pct": 0.0}


def _probe_rust_core_status() -> dict[str, Any]:
    try:
        import cutora_core  # type: ignore
        return {
            "loaded": True,
            "version": getattr(cutora_core, "__version__", "active"),
            "path": getattr(cutora_core, "__file__", ""),
        }
    except Exception as exc:
        return {"loaded": False, "error": str(exc)}


def analyze_hardware_and_advise(base_logs_dir: str | None = None) -> dict[str, Any]:
    """Profile hardware, evaluate current software adaptation, and generate
    concrete tuning & optimization recommendations for both the runtime and developer.
    """
    cpu = _probe_cpu_info()
    mem = query_memory_snapshot()
    gpus = _probe_all_gpus()
    disk = _probe_disk_info(REPO_ROOT)
    rust_status = _probe_rust_core_status()

    try:
        from cutora.core.config import detect_gpu_config
        raw_cfg = detect_gpu_config(verbose=False)
        if isinstance(raw_cfg, tuple) and len(raw_cfg) >= 3:
            gpu_cfg = {"device": str(raw_cfg[0]), "compute_type": str(raw_cfg[1]), "reason": str(raw_cfg[2])}
        elif isinstance(raw_cfg, dict):
            gpu_cfg = raw_cfg
        else:
            gpu_cfg = {"device": "cpu", "compute_type": "int8", "reason": str(raw_cfg)}
    except Exception as exc:
        gpu_cfg = {"device": "cpu", "compute_type": "int8", "reason": str(exc)}

    rec = get_recorder(base_logs_dir=base_logs_dir)
    perf = rec.performance_summary()

    # Classify hardware tier & compute optimal runtime profile
    ram_total = mem.get("ram_total_gb", 0.0)
    ram_avail = mem.get("ram_avail_gb", 0.0)
    commit_total = mem.get("commit_total_gb", 0.0)
    commit_avail = mem.get("commit_avail_gb", 0.0)
    logical_cores = int(cpu.get("logical_cores", 4) or 4)

    primary_gpu = gpus[0] if gpus else None
    vram_total = float(primary_gpu.get("vram_total_gb", 0.0)) if primary_gpu else 0.0
    compute_cap = str(primary_gpu.get("compute_cap", "") or "") if primary_gpu else ""
    gpu_name = str(primary_gpu.get("name", "无独立 NVIDIA 显卡")) if primary_gpu else "无独立 NVIDIA 显卡"

    cc_float = 0.0
    try:
        cc_float = float(compute_cap)
    except Exception:
        cc_float = 0.0

    # Determine tier:
    # - Tier S (Workstation / Flagship): RAM >= 32GB, VRAM >= 12GB, CC >= 8.0 (or Apple Silicon >= 32GB Unified Memory)
    # - Tier A (High-End Mainstream): RAM >= 24GB, VRAM >= 8GB, CC >= 7.5 (or Apple Silicon 16-24GB Unified Memory)
    # - Tier B (Legacy / Mid-Range GPU like GTX 1070/1080 or 6GB VRAM / Apple Silicon 8GB): RAM >= 16GB, VRAM 6-8GB
    # - Tier C (Entry / Low RAM / iGPU): RAM < 16GB or VRAM < 6GB
    is_apple_silicon = IS_MACOS and (
        platform.machine().lower() in ("arm64", "aarch64")
        or (primary_gpu and primary_gpu.get("vendor") == "Apple")
    )
    if is_apple_silicon:
        if ram_total >= 32:
            tier = "S (Apple Silicon 旗舰工作站级 · Unified Memory + VideoToolbox)"
            rec_workers = 1 if ram_avail < 6.0 else min(4, max(1, int((ram_avail - 4.0) // 3.0)))
            rec_nvenc_slots = rec_workers
            rec_preview_fps = 30
            rec_asr_batch = 8 if ram_avail < 8.0 else 16
        elif ram_total >= 16:
            tier = "A (Apple Silicon 16GB 统一内存均衡级 · Unified Memory + VideoToolbox)"
            # 16GB 统一内存机型严格按当前可用内存预算调度，可用内存 < 6.5GB 时锁定单 worker，防止并发挤压交换区
            rec_workers = 1 if ram_avail < 6.5 else min(2, max(1, int((ram_avail - 4.0) // 3.0)))
            rec_nvenc_slots = rec_workers
            rec_preview_fps = 24 if ram_avail < 4.0 else 30
            rec_asr_batch = 4 if ram_avail < 4.5 else 8
        else:
            tier = "B (Apple Silicon 紧凑统一内存级 · 8GB Unified Memory + VideoToolbox)"
            rec_workers = 1
            rec_nvenc_slots = 1
            rec_preview_fps = 24
            rec_asr_batch = 4
    elif ram_total >= 32 and vram_total >= 12 and cc_float >= 8.0:
        tier = "S (旗舰工作站级)"
        rec_workers = min(4, max(2, int((ram_avail - 6.0) // 3.0)))
        rec_nvenc_slots = 3
        rec_preview_fps = 30
        rec_asr_batch = 16
    elif ram_total >= 24 and vram_total >= 8 and cc_float >= 7.0:
        tier = "A (高性能主力级)"
        rec_workers = min(3, max(1, int((ram_avail - 6.0) // 3.0)))
        rec_nvenc_slots = 2
        rec_preview_fps = 30
        rec_asr_batch = 12
    elif vram_total >= 6 and ram_total >= 16:
        tier = "B (主流/经典独显级，如 Pascal GTX 10系 / 8GB 显存机型)"
        rec_workers = min(2, max(1, int((ram_avail - 6.0) // 3.2)))
        rec_nvenc_slots = 1 if cc_float < 7.0 else 2
        rec_preview_fps = 24
        rec_asr_batch = 8
    else:
        tier = "C (轻量/核显/紧凑内存级)"
        rec_workers = 1
        rec_nvenc_slots = 1 if vram_total >= 4 else 0
        rec_preview_fps = 20
        rec_asr_batch = 4

    recommended_profile = {
        "hardware_tier": tier,
        "device": gpu_cfg.get("device", "cpu"),
        "asr_compute_type": gpu_cfg.get("compute_type", "int8"),
        "recommended_parallel_export_workers": max(1, rec_workers),
        "recommended_nvenc_concurrent_sessions": rec_nvenc_slots,
        "recommended_asr_batch_size": rec_asr_batch,
        "recommended_preview_target_fps": rec_preview_fps,
        "startup_nvenc_stress_probe": False,
        "gpu_fx_lazy_on_demand": True,
        "smart_blur_occlusion_skip": True,
    }

    # Build targeted Developer Optimization Recommendations ("给开发者的针对性升级与优化建议")
    dev_findings: list[dict[str, str]] = []

    # 1. Apple Silicon / Pascal / TensorCore / CPU architecture
    if is_apple_silicon:
        dev_findings.append({
            "severity": "INFO",
            "category": "GPU_ARCH_APPLE_SILICON",
            "title": "检测到 Apple Silicon 统一内存架构 (%s, 总内存 %.1f GB / 当前可用 %.2f GB)" % (gpu_name, ram_total, ram_avail),
            "hardware_adapt": (
                "已启用分离式异构加速架构：导出编码优先调用 Apple Media Engine (h264_videotoolbox / hevc_videotoolbox)，"
                "PyTorch 分割/对齐按需调用 Metal MPS，CTranslate2/faster-whisper 采用 ARM64 NEON/Accelerate CPU int8。"
            ),
            "developer_advice": (
                "【开发者优化方向】Apple Silicon 统一内存 (Unified Memory) 由 CPU 与 GPU 共享物理内存池："
                "① 16GB 机型在可用内存低于 6GB 时应保持单 worker 导出与较低 ASR batch (%d)，避免多进程模型同时驻留触发内存压缩；"
                "② 对齐或分割完成后及时调用 `torch.mps.empty_cache()` 归还 Metal 缓存页。" % rec_asr_batch
            ),
        })
    elif primary_gpu and 0 < cc_float < 7.0:
        dev_findings.append({
            "severity": "HIGH",
            "category": "GPU_ARCH_PASCAL",
            "title": "检测到 Pascal 架构显卡 (%s, Compute Capability %s)" % (gpu_name, compute_cap),
            "hardware_adapt": "已自动将 CTranslate2/WhisperX 精度锁定为 int8（避免 sm_61 无原生 TensorCore FP16 导致显存翻倍与算力退化），并将并发 NVENC 会话限制为 1 路，严禁启动时并发打满显卡。",
            "developer_advice": (
                "【开发者优化方向】Pascal 架构 (GTX 1060/1070/1080) 的硬件编码器与 CUDA 上下文切换开销较大："
                "① 保持启动零 NVENC 压力探测（已实现）；"
                "② 在执行 WhisperX 对齐后、启动 FFmpeg NVENC 导出前，务必调用 release_gpu_models() + torch.cuda.empty_cache() 腾出至少 2.5GB 显存余量；"
                "③ 预览合成避免每帧走 PCIe 往返拷贝，优先使用 Rust SIMD (cutora_core) + OpenCV CPU 贴图路径。"
            ),
        })
    elif primary_gpu and cc_float >= 7.0:
        dev_findings.append({
            "severity": "INFO",
            "category": "GPU_ARCH_TENSORCORE",
            "title": "检测到支持 TensorCore 的现代显卡 (%s, sm_%s, 显存 %.1f GB)" % (gpu_name, compute_cap.replace(".", ""), vram_total),
            "hardware_adapt": "已启用 %s 加速推理与硬件 NVENC 编码，推荐并行导出进程数 %d（NVENC 并发 %d 路）。" % (
                gpu_cfg.get("compute_type", "float16"), rec_workers, rec_nvenc_slots
            ),
            "developer_advice": (
                "【开发者优化方向】该机型具备高速 FP16 TensorCore 与多路 NVENC 能力："
                "① 单视频导出可开启分段并行导出 (segment_export) 把导出帧率推向 300+ fps；"
                "② 保持智能背景模糊遮挡跳过（不露黑边时不跑高斯模糊），将算力完全留给编码器。"
            ),
        })
    else:
        dev_findings.append({
            "severity": "WARN",
            "category": "GPU_NONE_OR_CPU",
            "title": "未检测到可用 NVIDIA 独显，当前处于 CPU / 核显模式",
            "hardware_adapt": "已自动回退至 CPU int8 推理与 libx264 veryfast 编码，并行 worker 锁定为 1。",
            "developer_advice": (
                "【开发者优化方向】针对无独显机型，建议在 FFmpeg 导出时探测 Intel QSV (h264_qsv) 或 AMD AMF (h264_amf) 硬编后备路径，"
                "并在剪气口/字幕识别时采用 16kHz 单声道内存流直接喂给 Silero VAD 以减少磁盘 I/O。"
            ),
        })

    # 2. Windows Pagefile / Commit Charge check (crucial even on 64GB RAM machines if pagefile is disabled)
    pagefile_size_gb = max(0.0, commit_total - ram_total)
    if IS_WINDOWS and ram_total > 0 and pagefile_size_gb < 4.0:
        dev_findings.append({
            "severity": "HIGH",
            "category": "WINDOWS_PAGEFILE_TOO_SMALL",
            "title": "系统虚拟内存 (Pagefile) 仅 %.1f GB（物理内存 %.1f GB）" % (pagefile_size_gb, ram_total),
            "hardware_adapt": "已收紧子进程并发上限以防止 Windows Commit Charge 耗尽。",
            "developer_advice": (
                "【开发者优化方向】即使物理内存高达 64GB，PyTorch CUDA DLL (`torch_cuda.dll`, `cublas64`) 加载时仍向 Windows 申请数十 GB 的虚拟地址提交配额 (Commit Charge)。"
                "当用户禁用了系统分页文件或 C 盘空间不足时，多个子进程 import torch 会直接触发系统黑屏或 MemoryError 1455。"
                "务必保证 render-job 子进程不 import torch（已隔离），并在 doctor 体检中提醒用户开启『系统管理大小』的分页文件。"
            ),
        })

    # 3. Rust Core Acceleration check
    if not rust_status.get("loaded"):
        if IS_MACOS:
            dev_findings.append({
                "severity": "INFO",
                "category": "RUST_CORE_OPTIONAL",
                "title": "可选 Rust 原生扩展 (cutora_core) 未加载 · 已使用标准 Python/NumPy 引擎",
                "hardware_adapt": "macOS 环境下已自动启用标准 Python/NumPy 矢量化后备实现，不影响主面板启动与剪辑导出。",
                "developer_advice": (
                    "【开发者优化方向】如需在 macOS ARM64 上进一步加速时间线重采样，可针对 `aarch64-apple-darwin` 与 Python 3.11 构建 `.so` 扩展包。"
                ),
            })
        else:
            dev_findings.append({
                "severity": "HIGH",
                "category": "RUST_CORE_MISSING",
                "title": "Rust 高性能计算核心 (cutora_core) 未加载",
                "hardware_adapt": "当前已自动回退至 Python/NumPy 后备实现。",
                "developer_advice": (
                    "【开发者优化方向】当前机器未能加载 `cutora_core.pyd` (%s)，这会导致时间线波形重采样、字幕 ROI 合成与哈希计算退回 Python 循环。"
                    "请检查目标机器 Python ABI 版本与 MSVC 运行库是否匹配。" % rust_status.get("error", "")
                ),
            })
    else:
        dev_findings.append({
            "severity": "INFO",
            "category": "RUST_CORE_ACTIVE",
            "title": "Rust 原生加速核心 (cutora_core) 已激活",
            "hardware_adapt": "热点路径（图层混合、帧缓冲处理、高性能计算）已由 Rust 原生代码接管，彻底规避 Python GIL 抖动。",
            "developer_advice": "【开发者优化方向】后续可将更多长视频音频包络扫描与批量字幕时间轴重映射进一步下沉至 `cutora_core`。",
        })

    # 4. Disk Free Space check
    if 0 < disk.get("free_gb", 99.0) < 15.0:
        dev_findings.append({
            "severity": "WARN",
            "category": "DISK_LOW_SPACE",
            "title": "所在磁盘 (%s) 剩余空间不足 (仅剩 %.1f GB)" % (disk.get("drive"), disk.get("free_gb", 0.0)),
            "hardware_adapt": "建议清理缓存目录，防止分段并行导出临时文件写满磁盘。",
            "developer_advice": "【开发者优化方向】在启动大批量导出前，预估输出体积并与磁盘剩余空间对比，不足 5GB 时提前弹窗预警而非导出中途失败。",
        })

    # 5. Runtime Observed Slow Operations / UI Stalls
    slow_ops = [
        (op, st)
        for op, st in (perf.get("operations") or {}).items()
        if st.get("max_ms", 0.0) >= 1500.0
    ]
    if slow_ops or perf.get("ui_stall_count", 0) > 0:
        slow_desc = ", ".join(
            "%s (最大 %.0f ms, P95 %.0f ms)" % (op, st["max_ms"], st["p95_ms"])
            for op, st in slow_ops[:5]
        )
        dev_findings.append({
            "severity": "WARN",
            "category": "RUNTIME_HOTSPOTS",
            "title": "本机运行期捕获到耗时操作或界面微卡顿 (UI 卡顿 %d 次，最长 %.2f 秒)" % (
                perf.get("ui_stall_count", 0), perf.get("max_ui_stall_s", 0.0)
            ),
            "hardware_adapt": "黑匣子已记录完整调用栈与当时资源快照。",
            "developer_advice": "【开发者优化方向】重点优化以下实测高耗时命令，将其彻底移出 Qt 主线程或增加分块流式处理：%s" % (
                slow_desc or "详见 telemetry_anomalies.jsonl 中的 UI_STALL 栈快照"
            ),
        })

    report = {
        "generated_at": _now_iso(),
        "hostname": socket.gethostname(),
        "os": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "python": sys.version.split()[0],
        },
        "cpu": cpu,
        "memory": mem,
        "gpus": gpus,
        "disk": disk,
        "rust_core": rust_status,
        "gpu_decision": gpu_cfg,
        "recommended_profile": recommended_profile,
        "developer_findings": dev_findings,
        "runtime_performance": perf,
    }

    bdir = blackbox_dir(base_logs_dir)
    try:
        _fsync_write_json(os.path.join(bdir, HARDWARE_ADVISOR_JSON), report)
        md_path = os.path.join(bdir, HARDWARE_ADVISOR_MD)
        with open(md_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(format_hardware_advisor_markdown(report))
    except Exception:
        logger.exception("写入硬件适配与开发者优化报告失败")

    return report


def format_hardware_advisor_markdown(report: dict[str, Any]) -> str:
    cpu = report.get("cpu") or {}
    mem = report.get("memory") or {}
    gpus = report.get("gpus") or []
    disk = report.get("disk") or {}
    prof = report.get("recommended_profile") or {}
    rust = report.get("rust_core") or {}
    perf = report.get("runtime_performance") or {}

    lines = [
        "# 🛠️ 片语 Studio · 设备自适应评估与开发者性能调优报告",
        "",
        "- **生成时间**：`%s`" % report.get("generated_at", ""),
        "- **计算机名**：`%s` (`%s %s`)" % (
            report.get("hostname", ""),
            (report.get("os") or {}).get("system", ""),
            (report.get("os") or {}).get("version", ""),
        ),
        "- **设备性能分级**：**%s**" % prof.get("hardware_tier", "未知"),
        "",
        "## 一、硬件底座指纹",
        "| 维度 | 检测结果 | 状态与余量 |",
        "| :--- | :--- | :--- |",
        "| **处理器 (CPU)** | `%s` | %s 逻辑核心 |" % (cpu.get("name", ""), cpu.get("logical_cores", 0)),
        "| **物理内存 (RAM)** | 总计 `%.2f GB` | 当前可用 `%.2f GB` (占用 `%.1f%%`) |" % (
            mem.get("ram_total_gb", 0.0),
            mem.get("ram_avail_gb", 0.0),
            mem.get("ram_used_pct", 0.0),
        ),
        "| **虚拟内存提交 (Commit)** | 上限 `%.2f GB` | 剩余可提交 `%.2f GB` (已用 `%.1f%%`) |" % (
            mem.get("commit_total_gb", 0.0),
            mem.get("commit_avail_gb", 0.0),
            mem.get("commit_used_pct", 0.0),
        ),
    ]
    if gpus:
        for g in gpus:
            lines.append(
                "| **独立显卡 (GPU #%s)** | `%s` (驱动 `%s`, 架构 `sm_%s`) | 显存 `%.2f GB` (空闲 `%.2f GB`) |" % (
                    g.get("index", "0"),
                    g.get("name", ""),
                    g.get("driver", ""),
                    str(g.get("compute_cap", "")).replace(".", ""),
                    g.get("vram_total_gb", 0.0),
                    g.get("vram_free_gb", 0.0),
                )
            )
    else:
        lines.append("| **独立显卡 (GPU)** | 未检测到 NVIDIA 独显 | 使用 CPU / 核显模式 |")

    lines.extend([
        "| **存储磁盘 (%s)** | 总计 `%.1f GB` | 剩余可用 `%.1f GB` (`%.1f%%`) |" % (
            disk.get("drive", ""),
            disk.get("total_gb", 0.0),
            disk.get("free_gb", 0.0),
            disk.get("free_pct", 0.0),
        ),
        "| **Rust 加速引擎** | `cutora_core` | `%s` |" % (
            "已激活 (%s)" % rust.get("version", "ok") if rust.get("loaded") else "未加载 (%s)" % rust.get("error", "")
        ),
        "",
        "## 二、软件当前自适应策略（最佳性能且防卡死参数）",
        "- **AI 推理设备与精度**：`%s` / `%s`（原因：%s）" % (
            prof.get("device", "cpu"),
            prof.get("asr_compute_type", "int8"),
            (report.get("gpu_decision") or {}).get("reason", ""),
        ),
        "- **推荐并行导出 Worker 数**：`%d` 个进程（预留 6GB 系统与桌面 DWM 保护内存）" % prof.get("recommended_parallel_export_workers", 1),
        "- **推荐 NVENC 并发编码上限**：`%d` 路" % prof.get("recommended_nvenc_concurrent_sessions", 1),
        "- **推荐语音识别 Batch Size**：`%d`" % prof.get("recommended_asr_batch_size", 8),
        "- **推荐实时预览目标帧率**：`%d fps`" % prof.get("recommended_preview_target_fps", 24),
        "",
        "## 三、给开发者的针对性升级与优化建议",
    ])

    for idx, item in enumerate(report.get("developer_findings") or [], 1):
        lines.extend([
            "### %d. [%s] %s" % (idx, item.get("severity", "INFO"), item.get("title", "")),
            "- **本机自适应动作**：%s" % item.get("hardware_adapt", ""),
            "- **开发者升级建议**：%s" % item.get("developer_advice", ""),
            "",
        ])

    ops = perf.get("operations") or {}
    if ops:
        lines.extend([
            "## 四、运行期指令耗时统计 (Top 耗时操作)",
            "| 操作 / 命令 | 调用次数 | 失败次数 | 平均耗时 (ms) | P95 耗时 (ms) | 最大耗时 (ms) |",
            "| :--- | :---: | :---: | :---: | :---: | :---: |",
        ])
        sorted_ops = sorted(ops.items(), key=lambda kv: kv[1].get("max_ms", 0.0), reverse=True)
        for op_name, st in sorted_ops[:15]:
            lines.append(
                "| `%s` | %d | %d | %.1f | %.1f | %.1f |" % (
                    op_name,
                    st.get("count", 0),
                    st.get("fail_count", 0),
                    st.get("avg_ms", 0.0),
                    st.get("p95_ms", 0.0),
                    st.get("max_ms", 0.0),
                )
            )
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Post-Mortem Hard-Lock / Crash Forensics ("失事原因自动定责分析器")
# ---------------------------------------------------------------------------

def _query_macos_crash_events(max_events: int = 10) -> list[dict[str, Any]]:
    """Scan ~/Library/Logs/DiagnosticReports for recent .ips / .crash reports on macOS."""
    if not IS_MACOS:
        return []
    diag_dir = os.path.expanduser("~/Library/Logs/DiagnosticReports")
    if not os.path.isdir(diag_dir):
        return []
    cutoff = time.time() - 48 * 3600.0
    out: list[dict[str, Any]] = []
    try:
        entries = []
        for fname in os.listdir(diag_dir):
            if not (fname.endswith(".ips") or fname.endswith(".crash")):
                continue
            low = fname.lower()
            if not any(k in low for k in ("python", "cutora", "ffmpeg", "qt")):
                continue
            fpath = os.path.join(diag_dir, fname)
            try:
                mtime = os.path.getmtime(fpath)
                if mtime >= cutoff:
                    entries.append((mtime, fname, fpath))
            except OSError:
                continue
        entries.sort(reverse=True)
        for mtime, fname, fpath in entries[:max_events]:
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                    head = fh.read(2500)
                low_head = head.lower()
                is_our_proc = any(
                    marker in low_head
                    for marker in ("片语工作室", "cutora", "runtime/venv", "runtime/python", "orante")
                )
                out.append({
                    "id": "MACOS_IPS_STUDIO" if is_our_proc else "MACOS_IPS_EXTERNAL",
                    "provider": "DiagnosticReports",
                    "level": "Error" if is_our_proc else "Info",
                    "associated": is_our_proc,
                    "time": datetime.fromtimestamp(mtime, TZ8).isoformat(timespec="seconds"),
                    "message": redact_text(("%s: %s" % (fname, head.strip()))[:350]),
                })
            except Exception:
                continue
    except Exception:
        pass
    return out


def _query_windows_crash_events(max_events: int = 15) -> list[dict[str, Any]]:
    """Query Windows Event Log for GPU TDR (nvlddmkm 4101/153), Kernel-Power 41,
    RAM Exhaustion (2004), and Application Error/Hang (1000/1002) in the last 48h.
    On macOS, delegates to _query_macos_crash_events().
    """
    if IS_MACOS:
        return _query_macos_crash_events(max_events=max_events)
    if not IS_WINDOWS:
        return []
    ps_script = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        "$ErrorActionPreference='SilentlyContinue';"
        "$since=(Get-Date).AddHours(-48);"
        "$ev=Get-WinEvent -FilterHashtable @{LogName=@('System','Application');StartTime=$since} -MaxEvents 400 | "
        "Where-Object { $_.Id -in @(41,153,1000,1001,1002,2004,4101) -or $_.ProviderName -match 'nvlddmkm|Display|WHEA|Resource-Exhaustion|Kernel-Power' } | "
        "Select-Object -First %d TimeCreated,Id,LevelDisplayName,ProviderName,Message;"
        "if ($ev) { $ev | ConvertTo-Json -Compress }"
    ) % int(max_events)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=6,
            creationflags=flags,
        )
        raw = (proc.stdout or "").strip()
        if not raw:
            return []
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            parsed = [parsed]
        if not isinstance(parsed, list):
            return []
        out = []
        for item in parsed:
            if isinstance(item, dict):
                out.append({
                    "id": item.get("Id"),
                    "provider": str(item.get("ProviderName") or ""),
                    "level": str(item.get("LevelDisplayName") or ""),
                    "time": str(item.get("TimeCreated") or ""),
                    "message": redact_text(str(item.get("Message") or "")[:350]),
                })
        return out
    except Exception:
        return []


def _extract_last_faulthandler_crash(base_logs_dir: str | None = None) -> str:
    log_dir = base_logs_dir or user_logs_dir()
    path = os.path.join(log_dir, "faulthandler.log")
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        from cutora.app import split_faulthandler_sessions
        sessions = split_faulthandler_sessions(text)
        for sess in reversed(sessions):
            lines = [ln.strip() for ln in sess.splitlines() if ln.strip()]
            # If session has more than the header banner, a native crash occurred
            if len(lines) > 1:
                return "\n".join(lines)[:2000]
    except Exception:
        pass
    return ""


def analyze_post_mortem(
    unclean_session: dict[str, Any] | None = None,
    base_logs_dir: str | None = None,
    include_win_events: bool = True,
) -> dict[str, Any]:
    """Correlate unclosed flight breadcrumbs, resource anomalies, native faulthandler
    stacks, and Windows Event Log to pinpoint the root cause of a crash, freeze, or forced power-off.
    """
    bdir = blackbox_dir(base_logs_dir)
    if unclean_session is None:
        unclean_path = os.path.join(bdir, LAST_UNCLEAN_SESSION_FILE)
        if os.path.isfile(unclean_path):
            try:
                with open(unclean_path, "r", encoding="utf-8") as fh:
                    unclean_session = json.load(fh)
            except Exception:
                unclean_session = None

    # Read breadcrumbs (prefer prev_flight_breadcrumbs if examining previous unclean session)
    prev_bc_path = os.path.join(bdir, PREV_BREADCRUMBS_FILE)
    cur_bc_path = os.path.join(bdir, BREADCRUMBS_FILE)
    breadcrumbs = _read_jsonl(prev_bc_path if os.path.isfile(prev_bc_path) else cur_bc_path, max_items=250)

    prev_anom_path = os.path.join(bdir, PREV_ANOMALIES_FILE)
    cur_anom_path = os.path.join(bdir, ANOMALIES_FILE)
    anomalies = _read_jsonl(prev_anom_path if os.path.isfile(prev_anom_path) else cur_anom_path, max_items=150)

    # Identify unclosed operations from breadcrumbs or unclean_session
    started_ops: dict[str, dict[str, Any]] = {}
    for entry in breadcrumbs:
        ev = entry.get("event")
        op_id = str(entry.get("op_id") or "")
        if ev == "OP_START" and op_id:
            started_ops[op_id] = entry
        elif ev == "OP_END" and op_id:
            started_ops.pop(op_id, None)

    unclosed_ops = list(started_ops.values())
    if not unclosed_ops and isinstance(unclean_session, dict):
        unclosed_ops = list(unclean_session.get("active_ops") or [])

    native_crash = _extract_last_faulthandler_crash(base_logs_dir)
    win_events = _query_windows_crash_events() if include_win_events else []

    # Root-cause classification engine
    root_causes: list[dict[str, str]] = []

    # Check 1: Unclosed operation at the exact moment of crash/power-off
    if unclosed_ops:
        last_op = unclosed_ops[-1]
        op_name = str(last_op.get("op") or "未知操作")
        op_detail = last_op.get("detail") or {}
        op_res = last_op.get("res") or last_op.get("res_at_start") or {}
        root_causes.append({
            "confidence": "极高 (直接证据)",
            "code": "UNCLOSED_FLIGHT_OPERATION",
            "summary": "系统在执行「%s」中途失联（未收到结束标记）" % op_name,
            "explanation": (
                "黑匣子在 `%s` 启动了操作 `%s`（参数：`%s`），当时可用物理内存 `%.2f GB`、剩余虚拟内存提交 `%.2f GB`。"
                "该操作在完成前进程终止或整机断电，说明根源直接位于该操作内部调用的底层模块。"
                % (
                    last_op.get("ts") or last_op.get("started_at") or "",
                    op_name,
                    json.dumps(op_detail, ensure_ascii=False),
                    float(op_res.get("ram_avail_gb", 0.0)),
                    float(op_res.get("commit_avail_gb", 0.0)),
                )
            ),
        })

    # Check 2: Native C++/CUDA/Qt segfault in faulthandler.log
    if native_crash:
        root_causes.append({
            "confidence": "极高 (原生调用栈)",
            "code": "NATIVE_SEGV_OR_ABORT",
            "summary": "捕获到 C++ / CUDA / Qt 原生层崩溃调用栈 (faulthandler)",
            "explanation": "底层动态库发生非法内存访问或 Abort，最后原生线程栈如下：\n```\n%s\n```" % native_crash[-800:],
        })

    # Check 3: Windows Event Log correlation (GPU TDR / Kernel-Power 41 / Resource-Exhaustion 2004)
    for ev in win_events:
        eid = ev.get("id")
        prov = ev.get("provider", "")
        msg = ev.get("message", "")
        if eid in (4101, 153) or "nvlddmkm" in prov.lower():
            root_causes.append({
                "confidence": "高 (Windows 内核日志)",
                "code": "GPU_TDR_DRIVER_RESET",
                "summary": "NVIDIA 显卡驱动发生超时重置或死锁 (Event ID %s / %s)" % (eid, prov),
                "explanation": (
                    "Windows 记录到显卡驱动无响应超过 2 秒触发 TDR 重置或总线挂起（%s）。"
                    "这正是导致『鼠标动不了、风扇狂转、只能长按电源键关机』的典型显卡硬锁死信号。"
                    "解决方案：降低并发 NVENC 编码路数、关闭启动期显卡并发压测、将旧架构显卡锁定为 int8。"
                ) % msg[:200],
            })
            break

    for ev in win_events:
        if ev.get("id") == 2004 or "resource-exhaustion" in ev.get("provider", "").lower():
            root_causes.append({
                "confidence": "高 (Windows 内核日志)",
                "code": "COMMIT_CHARGE_EXHAUSTED",
                "summary": "Windows 虚拟内存提交配额耗尽 (Resource-Exhaustion Event 2004)",
                "explanation": "系统虚拟内存 (Commit Charge) 被占满导致 DWM 桌面合成器与应用程序冻结：%s" % ev.get("message", "")[:220],
            })
            break

    for ev in win_events:
        if ev.get("id") == 41 or "kernel-power" in ev.get("provider", "").lower():
            root_causes.append({
                "confidence": "高 (Windows 内核日志)",
                "code": "FORCED_POWER_OFF_KERNEL_41",
                "summary": "检测到非正常断电重启 (Kernel-Power Event 41)",
                "explanation": "电脑曾发生整机硬锁死并被强制断电重启，结合上方最后未闭合的黑匣子操作即可锁定触发死机的动作。",
            })
            break

    for ev in win_events:
        if ev.get("id") == "MACOS_IPS_STUDIO":
            root_causes.append({
                "confidence": "高 (macOS DiagnosticReports · 已关联本软件解释器)",
                "code": "MACOS_DIAGNOSTIC_CRASH_REPORT",
                "summary": "检测到与片语工作室关联的 macOS 原生崩溃报告 (.ips / .crash)",
                "explanation": "macOS DiagnosticReports 记录了本软件内置进程异常崩溃：%s" % ev.get("message", "")[:240],
            })
            break

    # Check 4: Resource / UI Stall anomalies recorded before death
    if anomalies:
        last_anom = anomalies[-1]
        root_causes.append({
            "confidence": "中 (运行期异常轨迹)",
            "code": str(last_anom.get("type") or "ANOMALY"),
            "summary": "黑匣子在异常前捕获到：%s" % (last_anom.get("message") or last_anom.get("type")),
            "explanation": "发生时间 `%s`，当时状态：`%s`" % (
                last_anom.get("ts", ""),
                json.dumps(last_anom.get("res") or {}, ensure_ascii=False),
            ),
        })

    # Check 5: Parent-child launch state & child stdout/stderr startup failures (e.g., missing pandas before install_blackbox)
    ltx_root = os.path.abspath(os.path.join(REPO_ROOT, "..", ".."))
    launch_state_path = os.path.join(ltx_root, "data", "logs", "cutora_launch_state.json")
    child_stdout_path = os.path.join(ltx_root, "data", "logs", "cutora_stdout.log")
    launch_state_data: dict[str, Any] = {}
    if os.path.isfile(launch_state_path):
        try:
            with open(launch_state_path, "r", encoding="utf-8", errors="ignore") as fh:
                launch_state_data = json.load(fh) or {}
        except Exception:
            launch_state_data = {}

    if launch_state_data.get("stage") in ("ENV_CHECK_FAILED", "STARTUP_FAILED", "LOCK_FAILED"):
        root_causes.append({
            "confidence": "极高 (启动状态信标直接记录)",
            "code": str(launch_state_data.get("stage")),
            "summary": "剪辑台在启动阶段提前退出 (%s): %s" % (
                launch_state_data.get("stage"),
                launch_state_data.get("error") or "启动自检未通过",
            ),
            "explanation": "来自 `data/logs/cutora_launch_state.json` 的记录：`%s`" % json.dumps(
                launch_state_data.get("detail") or {}, ensure_ascii=False
            ),
        })
    elif os.path.isfile(child_stdout_path):
        try:
            with open(child_stdout_path, "r", encoding="utf-8", errors="ignore") as fh:
                tail_txt = fh.read()[-4000:]
            if "CRITICAL ERROR: Environment is missing packages!" in tail_txt and not launch_state_data.get("main_window_ready"):
                import re as _re
                m_miss = _re.findall(r"Missing:\s*([^\r\n]+)", tail_txt)
                miss_str = m_miss[-1].strip() if m_miss else "未知依赖"
                root_causes.append({
                    "confidence": "极高 (子进程标准输出原始日志)",
                    "code": "MISSING_DEPENDENCY_ON_STARTUP",
                    "summary": "剪辑台因缺少核心依赖包 (%s) 在启动自检阶段退出 (退出码 1)" % miss_str,
                    "explanation": "在 `data/logs/cutora_stdout.log` 中捕获到 `CRITICAL ERROR: Environment is missing packages! Missing: %s`。" % miss_str,
                })
        except Exception:
            pass

    cur_session_path = os.path.join(bdir, SESSION_STATE_FILE)
    cur_session_data: dict[str, Any] = {}
    if os.path.isfile(cur_session_path):
        try:
            with open(cur_session_path, "r", encoding="utf-8") as fh:
                cur_session_data = json.load(fh) or {}
        except Exception:
            cur_session_data = {}

    if (
        cur_session_data
        and not cur_session_data.get("clean_exit", True)
        and not cur_session_data.get("main_window_ready", False)
        and cur_session_data.get("exit_reason") == "atexit_before_main_window_ready"
    ):
        root_causes.append({
            "confidence": "极高 (会话生命周期记录)",
            "code": "STARTUP_FAILED",
            "summary": "剪辑台进程在主窗口就绪 (MAIN_WINDOW_READY) 前提前退出",
            "explanation": "会话 `%s` 已启动但未到达主界面展示阶段即触发退出，请检查启动期依赖或单实例锁状态。" % cur_session_data.get("session_id", ""),
        })

    # Check 6: Full-Studio Three Applications Snapshot (Orante 2.0 + CUTORA + 右键表格工具 + 总控台)
    studio_apps_snapshot: dict[str, Any] = {}
    try:
        if ltx_root not in sys.path:
            sys.path.insert(0, ltx_root)
        from core.launch_state import collect_studio_apps_snapshot
        studio_apps_snapshot = collect_studio_apps_snapshot(ltx_root)
    except Exception:
        studio_apps_snapshot = {}

    apps_map = studio_apps_snapshot.get("apps") or {}
    for app_key, app_label in (
        ("orante", "Orante 2.0 制片台"),
        ("table_tool", "右键表格工具"),
        ("launcher", "片语工作室总控台"),
    ):
        ainfo = apps_map.get(app_key) or {}
        lstate = ainfo.get("launch_state") or {}
        astage = str(lstate.get("stage") or "")
        aerr = str(lstate.get("error") or "")
        if astage in ("ENV_CHECK_FAILED", "STARTUP_FAILED", "LOCK_FAILED"):
            root_causes.append({
                "confidence": "极高 (全工作室启动信标)",
                "code": "%s_%s" % (app_key.upper(), astage),
                "summary": "%s在启动阶段报错 (%s): %s" % (app_label, astage, aerr or "启动未通过"),
                "explanation": "来自 `data/logs/%s_launch_state.json` 的记录：`%s`" % (
                    app_key,
                    redact_text(json.dumps(lstate.get("detail") or {}, ensure_ascii=False)),
                ),
            })

    tb_reg = (apps_map.get("table_tool") or {}).get("registry_check") or {}
    if IS_WINDOWS and tb_reg:
        if tb_reg.get("legacy_recycle_bin_found"):
            root_causes.append({
                "confidence": "高 (Windows 右键注册表巡检)",
                "code": "TABLE_TOOL_LEGACY_CONTEXT_MENU",
                "summary": "检测到旧版残留的『打开表格工具 (回收站图标)』右键菜单项",
                "explanation": "残留注册表路径：`%s`，启动总控台或运行 `install_studio.py` 即可自动清除。" % ", ".join(tb_reg.get("legacy_keys") or []),
            })
        elif not tb_reg.get("canonical_registered", True):
            root_causes.append({
                "confidence": "中 (Windows 右键注册表巡检)",
                "code": "TABLE_TOOL_NOT_REGISTERED",
                "summary": "Windows 右键菜单尚未注册『片语工作室 - 表格工具』或目标解释器缺失",
                "explanation": "当前注册状态：`%s`" % redact_text(json.dumps(tb_reg, ensure_ascii=False)),
            })

    has_any_valid_session = bool(
        cur_session_data.get("main_window_ready")
        or any(b.get("event") == "MAIN_WINDOW_READY" for b in breadcrumbs)
        or launch_state_data.get("main_window_ready")
        or any(
            ((apps_map.get(k) or {}).get("launch_state") or {}).get("main_window_ready")
            for k in ("orante", "cutora", "table_tool", "launcher")
        )
    )

    if not root_causes:
        if unclean_session is not None:
            status = "UNCLEAN_NO_SPECIFIC_FAULT"
            summary_text = "检测到上次会话未正常走完退出流程（可能被外部终止或主面板未完成初始化），未发现底层崩溃栈。"
        elif not has_any_valid_session:
            status = "NO_SESSION_DATA"
            summary_text = "尚未记录到已完成主面板初始化的工作室运行会话（无有效会话记录，不能据此判定软件曾正常启动或正常关闭）。"
        else:
            status = "CLEAN"
            summary_text = "全工作室三款核心软件（Orante 2.0 制片台 / 片语剪辑台 CUTORA / 右键表格工具）运行正常，未检测到崩溃或硬锁死事故。"
    else:
        if any("FAILED" in str(rc.get("code", "")) or "MISSING_DEPENDENCY" in str(rc.get("code", "")) for rc in root_causes):
            status = "STARTUP_FAILED"
        else:
            status = "CRASH_OR_FREEZE_DETECTED"
        summary_text = "；".join(rc["summary"] for rc in root_causes)

    diagnosis = {
        "generated_at": _now_iso(),
        "status": status,
        "summary": summary_text,
        "unclean_session": unclean_session,
        "unclosed_operations": unclosed_ops,
        "root_causes": root_causes,
        "recent_anomalies": anomalies[-20:],
        "recent_breadcrumbs": breadcrumbs[-30:],
        "windows_crash_events": win_events,
        "has_native_crash_stack": bool(native_crash),
        "studio_apps": studio_apps_snapshot,
    }

    try:
        _fsync_write_json(os.path.join(bdir, POST_MORTEM_JSON), diagnosis)
        with open(os.path.join(bdir, POST_MORTEM_MD), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(format_post_mortem_markdown(diagnosis))
    except Exception:
        logger.exception("写入失事原因定责报告失败")

    return diagnosis


def format_post_mortem_markdown(diagnosis: dict[str, Any]) -> str:
    lines = [
        "# 🛩️ 片语 Studio · 黑匣子事故追凶与根源定责报告 (Flight Recorder Post-Mortem)",
        "",
        "- **生成时间**：`%s`" % diagnosis.get("generated_at", ""),
        "- **诊断状态**：`%s`" % diagnosis.get("status", ""),
        "- **核心结论**：**%s**" % diagnosis.get("summary", ""),
        "",
    ]

    causes = diagnosis.get("root_causes") or []
    if causes:
        lines.append("## 一、根源定责分析 (Root Cause Analysis)")
        for idx, rc in enumerate(causes, 1):
            lines.extend([
                "### %d. [%s] %s (`%s`)" % (
                    idx,
                    rc.get("confidence", ""),
                    rc.get("summary", ""),
                    rc.get("code", ""),
                ),
                "%s" % rc.get("explanation", ""),
                "",
            ])

    unclosed = diagnosis.get("unclosed_operations") or []
    if unclosed:
        lines.extend([
            "## 二、断电/崩溃瞬间正在执行的未闭合操作 (Unclosed Operations)",
            "| 启动时间 | 操作代号 | 操作详情 | 启动时可用内存 (GB) | 启动时剩余提交 (GB) |",
            "| :--- | :--- | :--- | :---: | :---: |",
        ])
        for op in unclosed:
            res = op.get("res") or op.get("res_at_start") or {}
            lines.append(
                "| `%s` | `%s` | `%s` | %.2f | %.2f |" % (
                    op.get("ts") or op.get("started_at") or "",
                    op.get("op", ""),
                    json.dumps(op.get("detail") or {}, ensure_ascii=False),
                    float(res.get("ram_avail_gb", 0.0)),
                    float(res.get("commit_avail_gb", 0.0)),
                )
            )
        lines.append("")

    anomalies = diagnosis.get("recent_anomalies") or []
    if anomalies:
        lines.extend([
            "## 三、近期捕获的满负载 / 卡顿异常轨迹",
            "| 时间 | 类型 | 描述 / 耗时 | 可用内存 (GB) | 虚拟内存占用 (%) |",
            "| :--- | :--- | :--- | :---: | :---: |",
        ])
        for a in anomalies[-15:]:
            res = a.get("res") or {}
            desc = a.get("message") or ("界面卡顿 %.2fs" % float(a.get("stall_s", 0.0)))
            lines.append(
                "| `%s` | `%s` | %s | %.2f | %.1f%% |" % (
                    a.get("ts", ""),
                    a.get("type", ""),
                    desc,
                    float(res.get("ram_avail_gb", 0.0)),
                    float(res.get("commit_used_pct", 0.0)),
                )
            )
        lines.append("")

    bcs = diagnosis.get("recent_breadcrumbs") or []
    if bcs:
        lines.extend([
            "## 四、最后 15 条黑匣子飞行轨迹 (Flight Breadcrumbs)",
            "| 时间 | 事件 | 操作 | 耗时 (ms) | 状态 |",
            "| :--- | :--- | :--- | :---: | :---: |",
        ])
        for bc in bcs[-15:]:
            lines.append(
                "| `%s` | `%s` | `%s` | %s | %s |" % (
                    bc.get("ts", ""),
                    bc.get("event", ""),
                    bc.get("op", "-"),
                    bc.get("ms", "-"),
                    "OK" if bc.get("ok", True) else ("FAIL: %s" % bc.get("error", "")),
                )
            )
        lines.append("")

    studio_apps = (diagnosis.get("studio_apps") or {}).get("apps") or {}
    if studio_apps:
        lines.extend([
            "## 五、全工作室三款核心软件与总控台运行档案 (Orante 2.0 / CUTORA / 右键表格工具)",
            "| 软件模块 | 最近生命周期阶段 | 主界面已就绪 | 最近更新时间 | 错误/补充说明 |",
            "| :--- | :--- | :---: | :--- | :--- |",
        ])
        for k in ("launcher", "orante", "cutora", "table_tool"):
            item = studio_apps.get(k) or {}
            ls = item.get("launch_state") or {}
            st_stage = ls.get("stage") or "未启动/无记录"
            ready_flag = "✅ 是" if ls.get("main_window_ready") else "—"
            upd_at = ls.get("updated_at") or "—"
            err_or_detail = ls.get("error") or json.dumps(ls.get("detail") or {}, ensure_ascii=False)
            lines.append(
                "| **%s** (`%s`) | `%s` | %s | `%s` | %s |" % (
                    item.get("label", k),
                    k,
                    st_stage,
                    ready_flag,
                    upd_at,
                    redact_text(str(err_or_detail)[:140]),
                )
            )
        lines.append("")
        orante_db = (studio_apps.get("orante") or {}).get("db_health") or {}
        if orante_db:
            lines.append(
                "- **Orante 2.0 数据库与作业摘要**：数据库存在=`%s` ｜ 最近作业数=`%d` ｜ 最近异常段数=`%d`"
                % (
                    orante_db.get("db_exists", False),
                    len(orante_db.get("recent_jobs") or []),
                    len(orante_db.get("recent_failed_shots") or []),
                )
            )
        tb_reg = (studio_apps.get("table_tool") or {}).get("registry_check") or {}
        if tb_reg:
            lines.append(
                "- **右键表格工具注册表体检**：标准右键菜单已注册=`%s` ｜ 目标解释器存在=`%s` ｜ 旧版回收站残影键已清零=`%s`"
                % (
                    tb_reg.get("canonical_registered", False),
                    tb_reg.get("target_python_exists", False),
                    not bool(tb_reg.get("legacy_recycle_bin_found", False)),
                )
            )
        lines.append("")

    return "\n".join(lines)


def _write_redacted_file_to_zip(zf: zipfile.ZipFile, src_path: str, arc_name: str, max_bytes: int = 4 * 1024 * 1024) -> bool:
    """Read a text/log/JSON file, scrub all secrets/cookies/tokens/user paths, and write to zip."""
    if not os.path.isfile(src_path):
        return False
    try:
        size = os.path.getsize(src_path)
        with open(src_path, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
            raw = fh.read()
        text = raw.decode("utf-8", errors="replace")
        clean_text = redact_text(text)
        zf.writestr(arc_name, clean_text.encode("utf-8"))
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# One-Click Blackbox Zip Bundle Exporter
# ---------------------------------------------------------------------------

def export_blackbox_zip(
    dest_dir: str | None = None,
    *,
    base_logs_dir: str | None = None,
    include_win_events: bool = True,
) -> dict[str, Any]:
    """Generate fresh hardware optimization + post-mortem reports and package all
    blackbox telemetry, logs, and crash forensics for all 3 studio apps into a single `.zip` archive.
    All logs, JSONs, and paths are privacy-scrubbed before writing to the zip.
    """
    import hashlib
    log_dir = base_logs_dir or user_logs_dir()
    bdir = blackbox_dir(log_dir)

    hw_report = analyze_hardware_and_advise(base_logs_dir=log_dir)
    pm_report = analyze_post_mortem(base_logs_dir=log_dir, include_win_events=include_win_events)

    if not dest_dir:
        try:
            from cutora.core.bootstrap import default_desktop_dir
            dest_dir = default_desktop_dir()
        except Exception:
            dest_dir = os.path.join(os.path.expanduser("~"), "Desktop")
    if not os.path.isdir(dest_dir):
        dest_dir = bdir
    os.makedirs(dest_dir, exist_ok=True)

    raw_host = socket.gethostname() or "PC"
    anon_host = "HOST_" + hashlib.sha256(raw_host.encode("utf-8", errors="ignore")).hexdigest()[:6].upper()
    stamp = datetime.now(TZ8).strftime("%Y%m%d_%H%M%S")
    zip_name = "片语工作室_黑匣子_%s_%s.zip" % (anon_host, stamp)
    zip_path = os.path.join(dest_dir, zip_name)

    combined_md = redact_text("\n\n---\n\n".join([
        format_hardware_advisor_markdown(hw_report),
        format_post_mortem_markdown(pm_report),
    ]))

    files_added: list[str] = []
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("00_【必看】黑匣子总览与根源诊断报告.md", combined_md.encode("utf-8"))
        files_added.append("00_【必看】黑匣子总览与根源诊断报告.md")

        # Include full-studio 3-apps snapshot JSON
        studio_apps_data = pm_report.get("studio_apps") or {}
        if studio_apps_data:
            zf.writestr(
                "01_全工作室三款软件运行档案_studio_apps.json",
                redact_text(json.dumps(studio_apps_data, ensure_ascii=False, indent=2)).encode("utf-8"),
            )
            files_added.append("01_全工作室三款软件运行档案_studio_apps.json")

        # Include all files in blackbox/ (privacy-redacted)
        for fname in sorted(os.listdir(bdir)):
            fpath = os.path.join(bdir, fname)
            if os.path.isfile(fpath) and not fname.endswith(".tmp"):
                arc = "blackbox/" + fname
                if _write_redacted_file_to_zip(zf, fpath, arc):
                    files_added.append(arc)

        # Include CUTORA standard logs (privacy-redacted)
        for lname in (
            "cutora.log",
            "cutora.log.1",
            "faulthandler.log",
            "commands.jsonl",
            "seek_trace.jsonl",
        ):
            lpath = os.path.join(log_dir, lname)
            if os.path.isfile(lpath):
                arc = "cutora_logs/" + lname
                if _write_redacted_file_to_zip(zf, lpath, arc):
                    files_added.append(arc)

        # Include studio parent-child stdout/stderr, launch states & unified breadcrumbs in <LTX_ROOT>/data/logs and <LTX_ROOT>/logs
        ltx_root = os.path.abspath(os.path.join(REPO_ROOT, "..", ".."))
        for sub_dir, arc_prefix in (
            (os.path.join(ltx_root, "data", "logs"), "studio_logs/"),
            (os.path.join(ltx_root, "logs"), "launcher_logs/"),
        ):
            if os.path.isdir(sub_dir):
                for sname in (
                    "studio_flight_breadcrumbs.jsonl",
                    "launcher_launch_state.json",
                    "orante_launch_state.json",
                    "cutora_launch_state.json",
                    "table_tool_launch_state.json",
                    "cutora_stdout.log",
                    "cutora_stderr.log",
                    "orante_stdout.log",
                    "orante_stderr.log",
                    "table_tool.log",
                    "table_tool_stdout.log",
                    "table_tool_stderr.log",
                    "macos_install.log",
                    "macos_app_launch.log",
                    "launcher_error.log",
                ):
                    spath = os.path.join(sub_dir, sname)
                    if os.path.isfile(spath):
                        arc = arc_prefix + sname
                        if _write_redacted_file_to_zip(zf, spath, arc):
                            files_added.append(arc)

        # Include Orante 2.0 dedicated logs (modules/orante_desk/data/logs/orante.log & crash.log)
        orante_logs_dir = os.path.join(ltx_root, "modules", "orante_desk", "data", "logs")
        if os.path.isdir(orante_logs_dir):
            for oname in ("orante.log", "orante.log.1", "crash.log", "faulthandler.log"):
                opath = os.path.join(orante_logs_dir, oname)
                if os.path.isfile(opath):
                    arc = "orante_logs/" + oname
                    if _write_redacted_file_to_zip(zf, opath, arc):
                        files_added.append(arc)

        # Include install health report from <LTX_ROOT>/data or REPO_ROOT
        for rpath, arc in (
            (os.path.join(ltx_root, "data", "install_health_report.json"), "install/install_health_report.json"),
            (os.path.join(REPO_ROOT, "install_health_report.json"), "install/cutora_install_health_report.json"),
            (os.path.join(REPO_ROOT, "黑匣子_安装体检报告.txt"), "install/黑匣子_安装体检报告.txt"),
        ):
            if os.path.isfile(rpath) and arc not in files_added:
                if _write_redacted_file_to_zip(zf, rpath, arc):
                    files_added.append(arc)

        # Include sanitized runtime_state files
        try:
            udata = resolve_user_data()
            for uname in ("worker_rss.json", "nvenc_probe.json", "export_state.json"):
                upath = os.path.join(udata, uname)
                if os.path.isfile(upath):
                    arc = "runtime_state/" + uname
                    if _write_redacted_file_to_zip(zf, upath, arc):
                        files_added.append(arc)
        except Exception:
            pass

    return {
        "zip_path": zip_path,
        "files_count": len(files_added),
        "files": files_added,
        "hardware_tier": (hw_report.get("recommended_profile") or {}).get("hardware_tier", ""),
        "post_mortem_status": pm_report.get("status", ""),
        "post_mortem_summary": pm_report.get("summary", ""),
        "developer_findings_count": len(hw_report.get("developer_findings") or []),
    }
