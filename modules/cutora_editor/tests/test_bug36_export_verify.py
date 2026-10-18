# -*- coding: utf-8 -*-
"""BUG-36: export verify rejects truncated files and frame-count mismatch."""
from __future__ import annotations

import os
import subprocess

import pytest

from cutora.core.config import FFMPEG_PATH
from cutora.render.export_verify import verify_export

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ffmpeg():
    return FFMPEG_PATH


def _make_mp4(path, duration=2.0, fps=30, size="64x64"):
    r = subprocess.run(
        [
            _ffmpeg(), "-y", "-f", "lavfi",
            "-i", "color=c=red:s=%s:d=%.2f:r=%d" % (size, duration, fps),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            path,
        ],
        capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        pytest.skip("ffmpeg cannot encode test clip: %s" % (r.stderr or "")[-200:])
    assert os.path.isfile(path) and os.path.getsize(path) > 2000


def test_export_verify_rejects_truncated(tmp_path):
    src = str(tmp_path / "ok.mp4")
    _make_mp4(src, duration=2.0, fps=25)
    data = open(src, "rb").read()
    cut = str(tmp_path / "trunc.mp4")
    open(cut, "wb").write(data[: max(64, int(len(data) * 0.70))])
    ok, why = verify_export(cut, expected_duration=2.0, expected_frames=50, fps=25)
    assert ok is False, why
    assert why, "truncated file must fail with a reason"


def test_export_verify_rejects_frame_count_mismatch(tmp_path):
    src = str(tmp_path / "short.mp4")
    _make_mp4(src, duration=2.0, fps=25)
    ok, why = verify_export(src, expected_duration=2.0, expected_frames=400, fps=25)
    assert ok is False, why
    assert any("frame" in s.lower() for s in why), why


def test_nb_frames_probed_before_count_frames(tmp_path, monkeypatch):
    src = str(tmp_path / "good.mp4")
    _make_mp4(src, duration=2.0, fps=25)
    argv_log = []
    real = __import__("cutora.render.export_verify", fromlist=["_run"])._run

    def wrap(argv, timeout=60):
        argv_log.append(list(argv))
        return real(argv, timeout=timeout)

    monkeypatch.setattr("cutora.render.export_verify._run", wrap)
    from cutora.render.export_verify import probe_duration_and_frames
    dur, frames = probe_duration_and_frames(src)
    assert frames is not None and frames > 0, (dur, frames)
    joined = [" ".join(a) for a in argv_log]
    nb = [s for s in joined if "nb_frames" in s]
    assert nb, joined
    assert "-count_frames" not in nb[0], nb[0]
    count = [s for s in joined if "-count_frames" in s]
    assert count == [], "count_frames ran even though nb_frames worked: %s" % count


def test_export_verify_accepts_intact(tmp_path):
    src = str(tmp_path / "good.mp4")
    _make_mp4(src, duration=2.0, fps=25)
    ok, why = verify_export(src, expected_duration=2.0, fps=25)
    assert ok is True, why


def _method_body(src: str, class_name: str, method_name: str) -> str:
    marker = "class %s" % class_name
    i = src.find(marker)
    assert i >= 0, "missing %s" % class_name
    rest = src[i:]
    sig = "def %s(" % method_name
    j = rest.find(sig)
    assert j >= 0, "missing %s.%s" % (class_name, method_name)
    body = rest[j:]
    lines = body.splitlines(True)
    head = lines[0]
    indent = len(head) - len(head.lstrip(" "))
    out = [head]
    for line in lines[1:]:
        if not line.strip():
            out.append(line)
            continue
        cur = len(line) - len(line.lstrip(" "))
        if cur <= indent and line.lstrip().startswith("def "):
            break
        if cur <= indent and line.lstrip().startswith("class "):
            break
        out.append(line)
    return "".join(out)


def test_mad_skipped_when_early_checks_fail(tmp_path, monkeypatch):
    """MAD must not run when duration/frame checks already failed (T06 cost gate)."""
    src = str(tmp_path / "short.mp4")
    _make_mp4(src, duration=2.0, fps=25)
    calls = []

    def _boom(*_a, **_k):
        calls.append(1)
        return "MAD ran"

    monkeypatch.setattr("cutora.render.export_verify.mad_vs_preview", _boom)
    dummy = __import__("numpy").zeros((64, 64, 3), dtype="uint8")
    ok, why = verify_export(
        src, expected_duration=2.0, expected_frames=400, fps=25,
        preview_frames=[(0.5, dummy)], width=64, height=64)
    assert ok is False, why
    assert calls == [], "MAD ran after frame-count failure: %s" % why
    assert any("frame" in s.lower() for s in why), why


def test_mad_runs_when_early_checks_pass(tmp_path, monkeypatch):
    src = str(tmp_path / "good.mp4")
    _make_mp4(src, duration=2.0, fps=25, size="64x64")
    calls = []

    def _ok(*_a, **_k):
        calls.append(1)
        return None

    monkeypatch.setattr("cutora.render.export_verify.mad_vs_preview", _ok)
    dummy = __import__("numpy").zeros((64, 64, 3), dtype="uint8")
    ok, why = verify_export(
        src, expected_duration=2.0, fps=25,
        preview_frames=[(0.5, dummy)], width=64, height=64)
    assert calls == [1], "MAD skipped on a passing duration/frame check"
    assert ok is True, why


def test_serial_path_also_verifies():
    """Parallel-failed jobs retry via RenderWorker.run, which must verify_export."""
    rw_path = os.path.join(REPO, "cutora", "render", "render_worker.py")
    mw_path = os.path.join(REPO, "cutora", "ui", "main_window.py")
    rw = open(rw_path, encoding="utf-8").read()
    mw = open(mw_path, encoding="utf-8").read()

    run_body = _method_body(rw, "RenderWorker", "run")
    assert "verify_export" in run_body, run_body[-800:]
    assert "self.output_path" in run_body

    finished = _method_body(mw, "MainWindow", "_on_parallel_finished")
    assert "process_next_export" in finished
    assert "batch_queue" in finished and "failed_rows" in finished

    nxt = _method_body(mw, "MainWindow", "process_next_export")
    assert "run_export_worker" in nxt

    export = _method_body(mw, "MainWindow", "run_export_worker")
    assert "RenderWorker" in export
