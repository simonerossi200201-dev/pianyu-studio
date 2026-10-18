# -*- coding: utf-8 -*-
"""BUG-29/30: ASR worker stderr drain, no pipe deadlock, no thread leak."""
from __future__ import annotations

import os
import sys
import textwrap
import threading
import time

import pytest

from cutora.asr.clip_worker import AsrClient
from cutora.asr.tier import WatchdogTimeout

_NOISY_WORKER = textwrap.dedent(
    r"""
    import json, sys, time, os
    sys.stdout.reconfigure(encoding="utf-8") if hasattr(sys.stdout, "reconfigure") else None
    sys.stderr.reconfigure(encoding="utf-8") if hasattr(sys.stderr, "reconfigure") else None
    mode = os.environ.get("CUTORA_TEST_WORKER_MODE", "noisy")
    for raw in sys.stdin:
        line = (raw or "").strip()
        if not line:
            continue
        job = json.loads(line)
        cmd = str(job.get("cmd") or "")
        if cmd == "quit":
            sys.stdout.write('{"ok":true}\n')
            sys.stdout.flush()
            break
        if cmd == "hello":
            sys.stdout.write('{"ok":true,"tier":"E"}\n')
            sys.stdout.flush()
            continue
        if cmd != "transcribe":
            sys.stdout.write('{"ok":false,"error":"unknown"}\n')
            sys.stdout.flush()
            continue
        if mode == "hang":
            time.sleep(3600)
        if mode == "noisy":
            chunk = ("N" * 1023) + "\n"
            for _ in range(1024):
                sys.stderr.write(chunk)
            sys.stderr.write("DRAIN_MARK worker-stderr-line\n")
            sys.stderr.flush()
        sys.stdout.write('{"ok":true,"words":[["hi",0.0,0.5]],"incomplete":false,"timing_tier":2}\n')
        sys.stdout.flush()
    """
).lstrip()


def _patch_worker(tmp_path, monkeypatch, mode="noisy"):
    script = tmp_path / "fake_asr_worker.py"
    script.write_text(_NOISY_WORKER, encoding="utf-8")
    monkeypatch.setenv("CUTORA_TEST_WORKER_MODE", mode)
    import cutora.asr.clip_worker as cw

    real_popen = cw.subprocess.Popen

    def _popen(args, **kwargs):
        return real_popen([sys.executable, "-u", str(script)], **kwargs)

    monkeypatch.setattr(cw.subprocess, "Popen", _popen)
    return script


def test_worker_survives_noisy_stderr(tmp_path, monkeypatch):
    """Child writes 1 MB to stderr before JSON; parent must still get the result.

    Before the drain-thread fix this deadlocks on the Windows pipe buffer and
    the watchdog fires. This test is required to go red first.
    """
    _patch_worker(tmp_path, monkeypatch, mode="noisy")
    logs = []
    client = AsrClient(log=logs.append)
    try:
        client.start("en")
        t0 = time.perf_counter()
        words, incomplete, tier = client.transcribe("clip.wav", 1.0, "en", timeout_sec=8.0)
        elapsed = time.perf_counter() - t0
    finally:
        client.close()
    assert words == [("hi", 0.0, 0.5)]
    assert elapsed < 8.0
    assert any("超过 %d KB" % 64 in s or "超过 64 KB" in s for s in logs)


def test_stderr_is_drained_to_log(tmp_path, monkeypatch):
    _patch_worker(tmp_path, monkeypatch, mode="noisy")
    logs = []
    client = AsrClient(log=logs.append)
    try:
        client.start("en")
        pid = client.proc.pid if client.proc else None
        client.transcribe("clip.wav", 1.0, "en", timeout_sec=8.0)
        deadline = time.time() + 2.0
        while time.time() < deadline and not any("DRAIN_MARK" in s for s in logs):
            time.sleep(0.05)
    finally:
        client.close()
    assert any("DRAIN_MARK" in s for s in logs), logs[-8:]
    ud = os.environ.get("CUTORA_USER_DATA") or str(tmp_path)
    log_dir = os.path.join(ud, "logs", "asr")
    files = []
    if os.path.isdir(log_dir):
        files = os.listdir(log_dir)
    assert any(n.startswith("worker_") and n.endswith(".log") for n in files), files
    if pid:
        path = os.path.join(log_dir, "worker_%s.log" % pid)
        if os.path.isfile(path):
            body = open(path, encoding="utf-8", errors="replace").read()
            assert "DRAIN_MARK" in body


def test_no_thread_leak_on_watchdog(tmp_path, monkeypatch):
    _patch_worker(tmp_path, monkeypatch, mode="hang")
    client = AsrClient(log=lambda s: None)
    n0 = threading.active_count()
    try:
        for _ in range(20):
            if client.proc is None:
                client.start("en")
            with pytest.raises(WatchdogTimeout):
                client.transcribe("clip.wav", 1.0, "en", timeout_sec=0.3)
    finally:
        client.close()
    deadline = time.time() + 5.0
    while time.time() < deadline and abs(threading.active_count() - n0) > 1:
        time.sleep(0.05)
    assert abs(threading.active_count() - n0) <= 1, (
        n0, threading.active_count(), threading.enumerate())


def test_close_reaps_process(tmp_path, monkeypatch):
    _patch_worker(tmp_path, monkeypatch, mode="noisy")
    client = AsrClient(log=lambda s: None)
    client.start("en")
    proc = client.proc
    assert proc is not None
    client.close()
    assert proc.poll() is not None
    assert proc.stdin is None or proc.stdin.closed
    assert proc.stdout is None or proc.stdout.closed
    assert proc.stderr is None or proc.stderr.closed
