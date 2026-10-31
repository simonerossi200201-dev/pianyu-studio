# -*- coding: utf-8 -*-
"""Unit tests for the Aviation-Grade Blackbox Flight Recorder, Hardware Advisor,
and Post-Mortem Crash/Hard-Lock Forensics system (`cutora.core.blackbox`).
"""
from __future__ import annotations

import json
import os
import zipfile

from cutora.core.blackbox import (
    BREADCRUMBS_FILE,
    FlightRecorder,
    LAST_UNCLEAN_SESSION_FILE,
    SESSION_STATE_FILE,
    analyze_hardware_and_advise,
    analyze_post_mortem,
    export_blackbox_zip,
)


def test_flight_recorder_breadcrumbs_and_clean_exit(tmp_path):
    logs_dir = str(tmp_path / "logs")
    rec = FlightRecorder(base_logs_dir=logs_dir)
    prev = rec.start_session(start_monitor=False)
    assert prev is None

    state_file = os.path.join(rec.dir_path, SESSION_STATE_FILE)
    assert os.path.isfile(state_file)
    with open(state_file, "r", encoding="utf-8") as fh:
        st = json.load(fh)
    assert st["clean_exit"] is False

    # Record an operation via context manager
    with rec.operation("test.heavy_op", {"file": "demo.mp4"}, sync=True, include_gpu=False):
        with open(state_file, "r", encoding="utf-8") as fh:
            mid_st = json.load(fh)
        assert len(mid_st["active_ops"]) == 1
        assert mid_st["active_ops"][0]["op"] == "test.heavy_op"

    # After exiting context manager, active_ops is empty and perf summary updated
    perf = rec.performance_summary()
    assert "test.heavy_op" in perf["operations"]
    assert perf["operations"]["test.heavy_op"]["count"] == 1
    assert perf["operations"]["test.heavy_op"]["fail_count"] == 0

    # Mark clean exit
    rec.mark_clean_exit("unit_test_done")
    with open(state_file, "r", encoding="utf-8") as fh:
        end_st = json.load(fh)
    assert end_st["clean_exit"] is True


def test_unclean_power_off_post_mortem_detection(tmp_path):
    """Simulate a PC hard-lock / forced power-off mid-operation (`begin_op` without `end_op`
    and without `mark_clean_exit`), then verify the next session automatically pinpoints
    the exact unclosed operation.
    """
    logs_dir = str(tmp_path / "logs_crash")
    rec1 = FlightRecorder(base_logs_dir=logs_dir)
    rec1.start_session(start_monitor=False)

    # Start a risky GPU operation and simulate a hard power-off (no end_op, no mark_clean_exit)
    op_id = rec1.begin_op(
        "asr.load_whisperx_model",
        {"tier": "large-v3", "device": "cuda", "compute": "float16"},
        sync=True,
        include_gpu=False,
    )
    assert op_id.startswith("asr.load_whisperx_model#")
    rec1.record_ui_stall(4.2, "  cutora/asr/engine.py:312 get_whisperx_model")

    # Next boot: new FlightRecorder instance starts on the same directory
    rec2 = FlightRecorder(base_logs_dir=logs_dir)
    # Force different PID simulation by tweaking the saved PID so pid_alive doesn't think rec1 is still running
    state_path = rec1.session_state_path
    with open(state_path, "r", encoding="utf-8") as fh:
        crashed_state = json.load(fh)
    crashed_state["pid"] = 99999999
    with open(state_path, "w", encoding="utf-8") as fh:
        json.dump(crashed_state, fh)

    prev = rec2.start_session(start_monitor=False)
    assert prev is not None
    assert os.path.isfile(os.path.join(rec2.dir_path, LAST_UNCLEAN_SESSION_FILE))

    pm = analyze_post_mortem(base_logs_dir=logs_dir, include_win_events=False)
    assert pm["status"] == "CRASH_OR_FREEZE_DETECTED"
    assert any(op["op"] == "asr.load_whisperx_model" for op in pm["unclosed_operations"])
    codes = [rc["code"] for rc in pm["root_causes"]]
    assert "UNCLOSED_FLIGHT_OPERATION" in codes
    rec2.mark_clean_exit("test_done")


def test_hardware_advisor_and_export_zip(tmp_path):
    logs_dir = str(tmp_path / "logs_export")
    out_dir = str(tmp_path / "out")
    rec = FlightRecorder(base_logs_dir=logs_dir)
    rec.start_session(start_monitor=False)

    hw = analyze_hardware_and_advise(base_logs_dir=logs_dir)
    assert "recommended_profile" in hw
    assert "developer_findings" in hw
    assert len(hw["developer_findings"]) >= 1

    bundle = export_blackbox_zip(dest_dir=out_dir, base_logs_dir=logs_dir, include_win_events=False)
    zip_path = bundle["zip_path"]
    assert os.path.isfile(zip_path)
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        assert "00_【必看】黑匣子总览与根源诊断报告.md" in names
        assert "blackbox/hardware_and_advisor.json" in names
        assert "blackbox/post_mortem_diagnosis.json" in names
        md_text = zf.read("00_【必看】黑匣子总览与根源诊断报告.md").decode("utf-8")
        assert "给开发者的针对性升级与优化建议" in md_text
        assert "黑匣子事故追凶与根源定责报告" in md_text

    rec.mark_clean_exit("done")


def test_command_bus_blackbox_commands(tmp_path):
    from cutora.core.commands import get_bus, reset_bus_for_tests
    reset_bus_for_tests()
    bus = get_bus()
    res_status = bus.execute("blackbox.status", {"include_win_events": False}, source="mcp")
    assert "hardware_advisor" in res_status
    assert "post_mortem" in res_status

    out_dir = str(tmp_path / "mcp_zip")
    res_exp = bus.execute(
        "blackbox.export",
        {"dest_dir": out_dir, "include_win_events": False},
        source="mcp",
    )
    assert os.path.isfile(res_exp["zip_path"])

