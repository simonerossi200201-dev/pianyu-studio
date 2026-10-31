# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Long-lived ASR subprocess. Parent can kill it when a clip hangs on CUDA.

Protocol (JSONL on stdin/stdout; logs on stderr):
  → {"cmd":"hello"}
  ← {"ok":true,"tier":"B"}
  → {"cmd":"transcribe","path":"...","dur":8.0,"language":"en"}
  ← {"ok":true,"words":[["many",0.0,0.74], ...]}
  → {"cmd":"quit"}
"""
from __future__ import annotations

import atexit
import json
import os
import queue
import subprocess
import sys
import threading
from typing import Callable

from cutora.asr.tier import (
    WatchdogTimeout,
    handle_oom,
    is_oom_error,
    kill_process_tree,
    watchdog_timeout_sec,
)
from cutora.core.proc import popen as _no_window_popen, run as _no_window_run

STDERR_WARN_KB = 64
ASR_LOAD_BUDGET_SEC = 30.0
ASR_TIMEOUT_STREAK_LIMIT = 3
CIRCUIT_STOP_MSG = "ASR 子进程连续无响应，已停止；日志见 UserData/logs/asr/"

_LIVE: set["AsrClient"] = set()
_ATEXIT_ON = False
_LIVE_LOCK = threading.Lock()


class AsrCircuitOpen(RuntimeError):
    """Three consecutive clip timeouts; stop the batch."""


class ClipWatchdogFailed(RuntimeError):
    """This clip timed out twice; skip it and continue the batch."""


class TimeoutCircuit:
    """Count consecutive clip-level watchdog failures."""

    def __init__(self, limit: int = ASR_TIMEOUT_STREAK_LIMIT):
        self.limit = int(limit)
        self.streak = 0
        self.failed_paths: list[str] = []

    def record_ok(self) -> None:
        self.streak = 0

    def record_fail(self, path: str) -> None:
        self.streak += 1
        self.failed_paths.append(path)
        if self.streak >= self.limit:
            raise AsrCircuitOpen(CIRCUIT_STOP_MSG)


def close_all_asr_clients() -> None:
    with _LIVE_LOCK:
        clients = list(_LIVE)
    for client in clients:
        try:
            client.close()
        except Exception:
            pass


def _ensure_atexit() -> None:
    global _ATEXIT_ON
    if _ATEXIT_ON:
        return
    atexit.register(close_all_asr_clients)
    _ATEXIT_ON = True


def _worker_log_path(pid: int) -> str | None:
    try:
        from cutora.core.paths import ensure_user_layout, user_logs_dir
        ensure_user_layout()
        folder = os.path.join(user_logs_dir(), "asr")
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, "worker_%s.log" % int(pid))
    except Exception:
        return None


def transcribe_with_downgrade(client, path, dur, language, log=None, circuit=None):
    """One clip via AsrClient. Second timeout → empty; 3 in a row → AsrCircuitOpen."""

    def _say(msg: str) -> None:
        if callable(log):
            log(msg)

    timeout = watchdog_timeout_sec(dur)
    if getattr(client, "needs_load_budget", False):
        timeout += ASR_LOAD_BUDGET_SEC
        client.needs_load_budget = False
    try:
        out = client.transcribe(path, dur, language, timeout)
        if circuit is not None:
            circuit.record_ok()
        return out
    except WatchdogTimeout:
        _say("    ⚠ 看门狗：片段超过 %.1fs，杀子进程并降档" % timeout)
        nxt = client.drop_and_restart(language)
        if nxt is None:
            client.start(language)
        timeout2 = watchdog_timeout_sec(dur)
        if getattr(client, "needs_load_budget", False):
            timeout2 += ASR_LOAD_BUDGET_SEC
            client.needs_load_budget = False
        try:
            out = client.transcribe(path, dur, language, timeout2)
            if circuit is not None:
                circuit.record_ok()
            return out
        except WatchdogTimeout:
            _say("    ⚠ 二次超时，该片记为失败，继续下一片")
            if circuit is not None:
                circuit.record_fail(path)
            raise ClipWatchdogFailed("二次超时")
        except Exception as e:
            if is_oom_error(e) and client.drop_and_restart(language):
                timeout3 = watchdog_timeout_sec(dur)
                if getattr(client, "needs_load_budget", False):
                    timeout3 += ASR_LOAD_BUDGET_SEC
                    client.needs_load_budget = False
                return client.transcribe(path, dur, language, timeout3)
            raise
    except Exception as e:
        if is_oom_error(e) and client.drop_and_restart(language):
            timeout_o = watchdog_timeout_sec(dur)
            if getattr(client, "needs_load_budget", False):
                timeout_o += ASR_LOAD_BUDGET_SEC
                client.needs_load_budget = False
            return client.transcribe(path, dur, language, timeout_o)
        raise


def _serve() -> None:
    real_out = sys.stdout
    sys.stdout = sys.stderr
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from cutora.asr.engine import (
        get_align_model, get_whisperx_model, release_gpu_models, trim_gpu_cache,
    )
    from cutora.batch.script_stitcher import (
        load_audio_16k, probe_media, transcribe_clip,
    )
    from cutora.asr import engine as asr_engine

    model = None
    align_model = None
    metadata = None
    language = "en"

    def reply(obj):
        real_out.write(json.dumps(obj, ensure_ascii=False) + "\n")
        real_out.flush()

    def _clip_log(s):
        text = str(s or "")
        stripped = text.lstrip()
        if stripped.startswith(("⚠", "FATAL", "❌", "[VRAM]")):
            print(text, file=sys.stderr)

    for raw in sys.stdin:
        line = (raw or "").strip()
        if not line:
            continue
        try:
            job = json.loads(line)
        except Exception as e:
            reply({"ok": False, "error": "bad json: %s" % e})
            continue
        cmd = str(job.get("cmd") or "")
        if cmd == "quit":
            try:
                release_gpu_models()
                trim_gpu_cache("worker quit")
            except Exception:
                pass
            reply({"ok": True})
            return
        if cmd == "hello":
            if model is None:
                language = str(job.get("language") or "en")
                model = get_whisperx_model(log=lambda s: print(s, file=sys.stderr))
                if not job.get("defer_align"):
                    try:
                        align_model, metadata = get_align_model(language)
                    except TypeError:
                        align_model, metadata = get_align_model()
            tier = getattr(model, "_cutora_tier", None)
            reply({"ok": True, "tier": tier})
            continue
        if cmd == "detect":
            if model is None:
                reply({"ok": False, "error": "not loaded"})
                continue
            path = str(job.get("path") or "")
            try:
                # 可选窗口：只解码一小段（字幕轨道转录用前 30 s 判语种，别把整条长片读进内存）
                if job.get("win_end") is not None:
                    audio = load_audio_16k(
                        path, start=float(job.get("win_start") or 0.0),
                        end=float(job.get("win_end")))
                else:
                    audio = load_audio_16k(path)
                fw = getattr(model, "model", None) or model
                _segments, info = fw.transcribe(audio, language=None)
                reply({
                    "ok": True,
                    "language": getattr(info, "language", None),
                    "language_probability": getattr(info, "language_probability", None),
                })
            except Exception as e:
                reply({"ok": False, "error": str(e)[:400]})
            continue
        if cmd == "load_align":
            if model is None:
                reply({"ok": False, "error": "not loaded"})
                continue
            language = str(job.get("language") or "")
            try:
                align_model, metadata = get_align_model(language)
                reply({"ok": True, "language": language})
            except Exception as e:
                reply({"ok": False, "error": str(e)[:400]})
            continue
        if cmd != "transcribe":
            reply({"ok": False, "error": "unknown cmd %s" % cmd})
            continue
        if model is None:
            reply({"ok": False, "error": "not loaded"})
            continue
        path = str(job.get("path") or "")
        language = str(job.get("language") or language)
        try:
            dur = float(job.get("dur") or 0.0)
            if dur <= 0:
                info = probe_media(path)
                dur = float(info.get("duration") or 0.0)
            win_end = job.get("win_end")
            win_start = float(job.get("win_start") or 0.0)
            if win_end is not None:
                win_end = float(win_end)
                # 只解码用户圈定的那几秒。词的时间加回窗口起点，才是成片上的时间。
                audio = load_audio_16k(path, start=win_start, end=win_end)
                dur = max(0.2, win_end - win_start)
            else:
                audio = load_audio_16k(path)
                win_start = 0.0
            status = {}
            words = transcribe_clip(
                model, align_model, metadata, audio, dur, language,
                asr_engine.DEVICE, log=_clip_log, status=status)
            payload = [[w[0], float(w[1]) + win_start, float(w[2]) + win_start]
                       for w in words]
            if win_end is not None and job.get("trim_after"):
                try:
                    trim_gpu_cache("locate-window")
                except Exception:
                    pass
            print("[clip] %s %.1fs → %d words" % (
                os.path.basename(path), dur, len(payload)), file=sys.stderr)
            sents = [[sg["text"], float(sg["start"]) + win_start, float(sg["end"]) + win_start]
                     for sg in (status.get("raw_segments") or [])]
            reply({
                "ok": True,
                "words": payload,
                "segments": sents,                       # Q190：带标点的原句
                "incomplete": bool(status.get("incomplete")),
                "timing_tier": int(status.get("timing_tier") or 2),
            })
        except Exception as e:
            reply({
                "ok": False,
                "oom": is_oom_error(e),
                "error": str(e)[:400],
            })


class AsrClient:
    """Parent-side handle. Killable. Restarts at a lower tier on OOM/hang."""

    def __init__(self, log: Callable[[str], None] | None = None):
        self.log = log
        self.proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self.tier_id = None
        self.needs_load_budget = False
        self.last_segments: list = []                    # Q190：上一次 transcribe 的原句（带标点）
        self._out_q: queue.Queue | None = None
        self._out_th: threading.Thread | None = None
        self._err_th: threading.Thread | None = None
        self._clip_stderr_bytes = 0
        self._stderr_warned = False
        self._closed_proc: subprocess.Popen | None = None

    def _say(self, msg: str) -> None:
        if callable(self.log):
            self.log(msg)

    def _register(self) -> None:
        _ensure_atexit()
        with _LIVE_LOCK:
            _LIVE.add(self)

    def _unregister(self) -> None:
        with _LIVE_LOCK:
            _LIVE.discard(self)

    def _start_readers(self, proc: subprocess.Popen) -> None:
        self._out_q = queue.Queue()
        self._clip_stderr_bytes = 0
        self._stderr_warned = False
        log_path = _worker_log_path(int(proc.pid or 0))

        def _read_stdout():
            q = self._out_q
            try:
                while True:
                    line = proc.stdout.readline() if proc.stdout else ""
                    if line == "":
                        break
                    if q is not None:
                        q.put(line)
            except Exception:
                pass
            finally:
                if q is not None:
                    q.put(None)

        def _read_stderr():
            fh = None
            try:
                if log_path:
                    fh = open(log_path, "a", encoding="utf-8")
            except Exception:
                fh = None
            try:
                while True:
                    line = proc.stderr.readline() if proc.stderr else ""
                    if line == "":
                        break
                    n = len(line.encode("utf-8", "replace"))
                    self._clip_stderr_bytes += n
                    if (not self._stderr_warned
                            and self._clip_stderr_bytes > STDERR_WARN_KB * 1024):
                        self._stderr_warned = True
                        self._say("⚠ ASR worker stderr 本片超过 %d KB" % STDERR_WARN_KB)
                    text = line.rstrip("\r\n")
                    if text:
                        self._say(text)
                    if fh is not None:
                        try:
                            fh.write(line)
                            fh.flush()
                        except Exception:
                            pass
            except Exception:
                pass
            finally:
                if fh is not None:
                    try:
                        fh.close()
                    except Exception:
                        pass

        self._out_th = threading.Thread(
            target=_read_stdout, name="asr-stdout-%s" % proc.pid, daemon=True)
        self._err_th = threading.Thread(
            target=_read_stderr, name="asr-stderr-%s" % proc.pid, daemon=True)
        self._out_th.start()
        self._err_th.start()

    def start(self, language: str = "en", defer_align: bool = False) -> None:
        self.close()
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        env = dict(os.environ)
        env["PYTHONPATH"] = repo + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONUNBUFFERED"] = "1"
        env["TQDM_DISABLE"] = "1"
        py = sys.executable
        self.proc = _no_window_popen(
            [py, "-m", "cutora.asr.clip_worker", "--serve"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=repo,
            env=env,
            text=True,
            bufsize=1,
        )
        self._start_readers(self.proc)
        self._register()
        self.needs_load_budget = True
        try:
            hello = self._roundtrip({
                "cmd": "hello",
                "language": language,
                "defer_align": bool(defer_align),
            }, timeout_sec=180)
        except Exception:
            self.close()
            raise
        if not hello or not hello.get("ok"):
            self.close()
            raise RuntimeError("ASR worker 启动失败: %s" % ((hello or {}).get("error")))
        self.tier_id = hello.get("tier")

    def detect(self, path: str, timeout_sec: float = 180,
               win_start=None, win_end=None) -> dict:
        """First-clip language. Align model is not loaded yet."""
        job = {"cmd": "detect", "path": path}
        if win_end is not None:
            job["win_start"] = float(win_start or 0.0)
            job["win_end"] = float(win_end)
        out = self._roundtrip(job, timeout_sec=timeout_sec)
        if out is None:
            raise WatchdogTimeout("ASR worker 无响应")
        if not out.get("ok"):
            raise RuntimeError(out.get("error") or "detect failed")
        return out

    def load_align(self, language: str, timeout_sec: float = 180) -> None:
        """Load the aligner once, after the batch language is locked."""
        out = self._roundtrip(
            {"cmd": "load_align", "language": language}, timeout_sec=timeout_sec)
        if out is None:
            raise WatchdogTimeout("ASR worker 无响应")
        if not out.get("ok"):
            raise RuntimeError(out.get("error") or "load_align failed")

    def transcribe(self, path: str, dur: float, language: str, timeout_sec: float,
                   win_start=None, win_end=None):
        self._clip_stderr_bytes = 0
        self._stderr_warned = False
        job = {
            "cmd": "transcribe",
            "path": path,
            "dur": float(dur),
            "language": language,
        }
        if win_end is not None:
            job["win_start"] = float(win_start or 0.0)
            job["win_end"] = float(win_end)
            job["trim_after"] = True
        out = self._roundtrip(job, timeout_sec=timeout_sec)
        if out is None:
            raise WatchdogTimeout("ASR worker 无响应")
        if out.get("oom"):
            raise RuntimeError("CUDA OOM: %s" % out.get("error"))
        if not out.get("ok"):
            raise RuntimeError(out.get("error") or "transcribe failed")
        words = [(str(t), float(a), float(b)) for t, a, b in out.get("words") or []]
        try:    # Q190：最近一次的原句（带标点）。老 worker 没有这一项就是空列表
            self.last_segments = [(str(t), float(a), float(b)) for t, a, b in out.get("segments") or []]
        except (TypeError, ValueError):
            self.last_segments = []
        return words, bool(out.get("incomplete")), int(out.get("timing_tier") or 2)

    def drop_and_restart(self, language: str = "en") -> str | None:
        nxt = handle_oom(self.tier_id or "S", log=self._say)
        self.close()
        if nxt is None:
            return None
        os.environ["CUTORA_ASR_TIER"] = nxt
        self.start(language)
        return nxt

    def close(self, force: bool = False) -> None:
        proc = self.proc
        self.proc = None
        if proc is None:
            self._unregister()
            return
        self._closed_proc = proc
        try:
            if force:
                kill_process_tree(proc.pid)
                try:
                    proc.kill()
                except Exception:
                    pass
            elif proc.poll() is None and proc.stdin and not getattr(proc.stdin, "closed", False):
                try:
                    proc.stdin.write(json.dumps({"cmd": "quit"}) + "\n")
                    proc.stdin.flush()
                except Exception:
                    pass
                try:
                    proc.stdin.close()
                except Exception:
                    pass
            try:
                proc.wait(timeout=1 if force else 5)
            except Exception:
                kill_process_tree(proc.pid)
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=3)
                except Exception:
                    pass
        finally:
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                if pipe is None:
                    continue
                try:
                    pipe.close()
                except Exception:
                    pass
            try:
                proc.wait(timeout=1)
            except Exception:
                pass
            for th in (self._out_th, self._err_th):
                if th is not None and th.is_alive():
                    th.join(timeout=2)
            self._out_th = None
            self._err_th = None
            self._out_q = None
            self._unregister()

    def _roundtrip(self, job: dict, timeout_sec: float) -> dict | None:
        proc = self.proc
        q = self._out_q
        if proc is None or proc.stdin is None or q is None:
            raise RuntimeError("ASR worker 未启动")
        with self._lock:
            try:
                proc.stdin.write(json.dumps(job, ensure_ascii=False) + "\n")
                proc.stdin.flush()
            except Exception as e:
                raise RuntimeError("ASR worker stdin: %s" % e) from e
            try:
                line = q.get(timeout=float(timeout_sec))
            except queue.Empty:
                self.close(force=True)
                raise WatchdogTimeout(
                    "ASR 超过 %.1fs 未返回（已杀子进程 %s）" % (
                        float(timeout_sec), proc.pid))
            if not line:
                return None
            try:
                return json.loads(line)
            except Exception:
                return {"ok": False, "error": str(line)[:200]}


def transcribe_path_watched(path, dur, language, log=None):
    """One-shot helper: start worker, one clip, close. Used by tests."""
    client = AsrClient(log=log)
    try:
        client.start(language)
        return client.transcribe(
            path, dur, language, timeout_sec=watchdog_timeout_sec(dur))
    finally:
        client.close()


if __name__ == "__main__":
    if "--serve" in sys.argv:
        _serve()
    else:
        sys.stderr.write("usage: python -m cutora.asr.clip_worker --serve\n")
        sys.exit(2)
