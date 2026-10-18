# -*- coding: utf-8 -*-
"""T08a: every export.* param must change ffmpeg argv or the output path."""
from __future__ import annotations

import os

from cutora.core.commands import CommandError, get_bus, get_registry
from cutora.core.history import (
    _load_gpu_enabled,
    _save_app_settings,
    _save_gpu_enabled,
)
from cutora.render.export_params import (
    apply_honor_io,
    audio_mix_trim_tail,
    batch_output_path,
    build_rawvideo_ffmpeg_cmd,
    cap_duration,
    export_trim_window,
    export_verify_expect,
    resolve_export_run_output,
    should_honor_io,
    verify_window_for_task,
)
from cutora.ui.command_bodies import CommandBodies

# Params that remain in the registry, and how they are proven.
# Commands with no params (export.settings / export.status) are listed empty.
ACCOUNTED = {
    "export.run": {
        "out": "path", "out_dir": "path", "honor_io": "argv",
        "profile": "argv", "allow_offline": "gate",
    },
    "export.batch": {
        "out_dir": "path", "project_paths": "path", "allow_offline": "gate",
    },
    "export.settings": {},
    "export.status": {},
    "export.profile.list": {},
    "export.profile.save": {
        "id": "path", "name": "path", "container": "argv",
        "vcodec": "argv", "quality": "argv", "audio_codec": "argv",
    },
    "export.profile.delete": {"id": "path"},
}


def _export_specs():
    return {s.name: s for s in get_registry().list() if s.name.startswith("export.")}


def _spec_params(spec):
    return set((spec.params_schema.get("properties") or {}).keys())


class _FakeExportWin(CommandBodies):
    def __init__(self, tasks):
        self.tasks = list(tasks)
        self.curr_idx = 0 if self.tasks else -1
        self.exports = []
        self._batch_export_out_dir = None
        self.batch_started = False

    def run_export_worker(self, task, target, is_batch=False):
        self.exports.append({"task": task, "out": target, "is_batch": is_batch})

    def toggle_batch_export(self):
        self.batch_started = True
        if self.tasks:
            out = batch_output_path(
                self.tasks[0], getattr(self, "_batch_export_out_dir", None))
            self.exports.append({"task": self.tasks[0], "out": out, "is_batch": True})


def test_every_export_param_is_accounted():
    specs = _export_specs()
    assert set(specs) == set(ACCOUNTED), specs.keys()
    extra = []
    missing = []
    for name, spec in specs.items():
        got = _spec_params(spec)
        want = set(ACCOUNTED[name])
        extra.extend("%s.%s" % (name, p) for p in sorted(got - want))
        missing.extend("%s.%s" % (name, p) for p in sorted(want - got))
    assert extra == [], "export.* param exists but is not proven (delete or wire): %s" % extra
    assert missing == [], "accounted param missing from spec: %s" % missing
    assert "profile" in _spec_params(specs["export.run"])


def test_export_run_out_and_out_dir_change_output_path(tmp_path):
    task = {"video": os.path.join(str(tmp_path), "clip.mp4")}
    a = resolve_export_run_output(task, out=str(tmp_path / "a.mp4"))
    b = resolve_export_run_output(task, out=str(tmp_path / "b.mp4"))
    assert a != b
    dir_a = tmp_path / "dir_a"
    dir_b = tmp_path / "dir_b"
    pa = resolve_export_run_output(task, out_dir=str(dir_a))
    pb = resolve_export_run_output(task, out_dir=str(dir_b))
    assert pa != pb
    assert os.path.normcase(os.path.dirname(pa)) == os.path.normcase(str(dir_a))
    assert os.path.isdir(dir_a) and os.path.isdir(dir_b)
    win = _FakeExportWin([task])
    win.command_export_run(out=str(tmp_path / "cmd.mp4"), allow_offline=True)
    assert win.exports[0]["out"].endswith("cmd.mp4")
    win2 = _FakeExportWin([task])
    win2.command_export_run(out_dir=str(tmp_path / "from_cmd"), allow_offline=True)
    assert os.path.normcase(os.path.dirname(win2.exports[0]["out"])) == os.path.normcase(
        str(tmp_path / "from_cmd"))


def test_export_run_honor_io_changes_trim_and_ffmpeg_atrim():
    task = {
        "video": "clip.mp4",
        "in_point": 1.25,
        "out_point": 4.5,
        "duration": 10.0,
    }
    start_off, end_off = export_trim_window(task, honor_io=False)
    start_on, end_on = export_trim_window(task, honor_io=True)
    assert (start_off, end_off) != (start_on, end_on)
    assert start_on == 1.25 and end_on == 4.5
    tail_off = audio_mix_trim_tail(start_off, end_off)
    tail_on = audio_mix_trim_tail(start_on, end_on)
    assert tail_off == ""
    assert "atrim=" in tail_on
    assert "start=1.25" in tail_on
    assert "end=4.5" in tail_on
    assert cap_duration(10.0, end_on) == 4.5
    assert cap_duration(10.0, end_off) == 10.0
    on_task = apply_honor_io(task, True)
    off_task = apply_honor_io(task, False)
    assert on_task is not task
    assert off_task is task
    win = _FakeExportWin([dict(task)])
    win.command_export_run(out="a.mp4", honor_io=False, allow_offline=True)
    win.command_export_run(out="b.mp4", honor_io=True, allow_offline=True)
    assert win.exports[0]["task"].get("trim_start_sec") in (None, 0, 0.0)
    assert win.exports[1]["task"]["trim_start_sec"] == 1.25
    assert win.exports[1]["task"]["trim_end_sec"] == 4.5
    assert should_honor_io(task) is True
    assert should_honor_io({"video": "x.mp4"}) is False
    from cutora.render.export_params import honor_io_dialog_line
    line = honor_io_dialog_line({
        "in_point": 12.4, "out_point": 105.9, "duration": 200.0,
    })
    assert "00:12.400" in line and "01:45.900" in line and "03:20.000" in line
    assert line.startswith("将只导出入出点区间")
    assert honor_io_dialog_line({"video": "x.mp4"}) == ""


def test_verify_expect_uses_frames_after_trim_start():
    """入点不是 0 时，写出来的帧数比整条时间线少。验收必须用这个数。"""
    dur, n = export_verify_expect(193.856, 30, 2.799263249999998)
    assert n == 5732
    assert dur == 5732 / 30
    dur2, n2 = export_verify_expect(191.402667, 30, 3.9157759999999975)
    assert n2 == 5625
    assert dur2 == 187.5
    full, full_n = export_verify_expect(10.0, 30, 0)
    assert full_n == 300 and full == 10.0
    seconds, frames, fps = verify_window_for_task({
        "duration": 193.856,
        "trim_start_sec": 2.799263249999998,
        "trim_end_sec": 193.856,
        "timeline": {"fps": 30},
    })
    assert frames == 5732 and seconds == 5732 / 30 and fps == 30.0
    assert verify_window_for_task({"video": "x.mp4"}) == (0.0, 0, 30.0)


def test_export_batch_out_dir_uses_that_directory(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    video = str(src / "talk.mp4")
    task = {"video": video}
    default = batch_output_path(task, None)
    dest = tmp_path / "batch_out"
    custom = batch_output_path(task, str(dest))
    assert default != custom
    assert os.path.normcase(os.path.dirname(default)) == os.path.normcase(
        str(src / "字幕成品"))
    assert os.path.normcase(os.path.dirname(custom)) == os.path.normcase(str(dest))
    assert os.path.isdir(dest)
    win = _FakeExportWin([task])
    result = win.command_export_batch(out_dir=str(dest), allow_offline=True)
    assert win.batch_started is True
    assert os.path.isdir(dest)
    assert os.path.normcase(result["out_dir"]) == os.path.normcase(str(dest))
    assert os.path.normcase(os.path.dirname(win.exports[0]["out"])) == os.path.normcase(
        str(dest))


def test_export_batch_project_paths_change_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path))
    from cutora.core.paths import ensure_user_layout, refresh_cached_paths
    refresh_cached_paths()
    ensure_user_layout()
    a = str(tmp_path / "a.asproj.json")
    b = str(tmp_path / "b.asproj.json")
    dest = str(tmp_path / "out")
    win = _FakeExportWin([{"video": str(tmp_path / "x.mp4")}])
    win.command_export_batch(project_paths=[a], out_dir=dest, allow_offline=True)
    from cutora.batch.cli import load_queue
    q1 = [os.path.normcase(i["project"]) for i in (load_queue() or {}).get("items") or []]
    win.command_export_batch(project_paths=[b], out_dir=dest, allow_offline=True)
    q2 = [os.path.normcase(i["project"]) for i in (load_queue() or {}).get("items") or []]
    assert q1 != q2
    assert os.path.normcase(os.path.abspath(a)) in q1
    assert os.path.normcase(os.path.abspath(b)) in q2


def test_export_settings_reads_real_parallel_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path))
    from cutora.core.paths import ensure_user_layout, refresh_cached_paths
    refresh_cached_paths()
    ensure_user_layout()
    _save_app_settings({
        "export_video_mode": "compressed",
        "parallel_export_enabled": True,
        "parallel_export_workers": 6,
        "gpu_enabled": False,
    })
    got = get_bus().execute("export.settings", {}, source="cli")
    assert got.get("parallel_export_enabled") is True
    assert got.get("parallel_export_workers") == 6
    assert got.get("export_video_mode") == "compressed"
    assert got.get("gpu_enabled") is False
    assert "parallel_export" not in got
    assert "parallel_workers" not in got


def test_gpu_persisted_setting_flips_nvenc_vs_x264(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path))
    monkeypatch.setattr(
        "cutora.render.encoder_probe.pick_h264",
        lambda want: ("h264_nvenc", "test") if want else ("libx264", "off"),
    )
    from cutora.core.paths import ensure_user_layout, refresh_cached_paths
    refresh_cached_paths()
    ensure_user_layout()
    _save_gpu_enabled(True)
    assert _load_gpu_enabled() is True
    gpu_cmd = build_rawvideo_ffmpeg_cmd(
        1080, 1920, 30.0, "t.mp4",
        use_gpu=_load_gpu_enabled(),
        export_mode="compressed",
        ffmpeg_path="ffmpeg",
    )
    _save_gpu_enabled(False)
    assert _load_gpu_enabled() is False
    cpu_cmd = build_rawvideo_ffmpeg_cmd(
        1080, 1920, 30.0, "t.mp4",
        use_gpu=_load_gpu_enabled(),
        export_mode="compressed",
        ffmpeg_path="ffmpeg",
    )
    assert gpu_cmd != cpu_cmd
    assert "h264_nvenc" in gpu_cmd
    assert "libx264" in cpu_cmd
    assert "libx264" not in gpu_cmd
    assert "h264_nvenc" not in cpu_cmd


def test_every_export_param_actually_changes_argv_or_path(tmp_path, monkeypatch):
    """Lock: each remaining export.* param changes ffmpeg argv or output path."""
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path))
    from cutora.core.paths import ensure_user_layout, refresh_cached_paths
    refresh_cached_paths()
    ensure_user_layout()
    task = {
        "video": str(tmp_path / "clip.mp4"),
        "in_point": 1.0,
        "out_point": 3.0,
    }
    effects = {
        ("export.run", "out"): (
            resolve_export_run_output(task, out=str(tmp_path / "one.mp4")),
            resolve_export_run_output(task, out=str(tmp_path / "two.mp4")),
        ),
        ("export.run", "out_dir"): (
            resolve_export_run_output(task, out_dir=str(tmp_path / "d1")),
            resolve_export_run_output(task, out_dir=str(tmp_path / "d2")),
        ),
        ("export.run", "honor_io"): (
            audio_mix_trim_tail(*export_trim_window(task, False)),
            audio_mix_trim_tail(*export_trim_window(task, True)),
        ),
        ("export.batch", "out_dir"): (
            batch_output_path(task, str(tmp_path / "b1")),
            batch_output_path(task, str(tmp_path / "b2")),
        ),
        ("export.batch", "project_paths"): (
            tuple(os.path.abspath(p) for p in [str(tmp_path / "a.asproj.json")]),
            tuple(os.path.abspath(p) for p in [str(tmp_path / "b.asproj.json")]),
        ),
    }
    ghost = str(tmp_path / "missing_media.mp4")
    win_block = _FakeExportWin([{"video": ghost, "path": ghost}])
    blocked = "blocked"
    try:
        win_block.command_export_run(out=str(tmp_path / "x.mp4"), allow_offline=False)
        blocked = "ran"
    except CommandError:
        blocked = "blocked"
    win_allow = _FakeExportWin([{"video": ghost, "path": ghost}])
    allowed = "blocked"
    try:
        win_allow.command_export_run(out=str(tmp_path / "y.mp4"), allow_offline=True)
        allowed = "ok" if win_allow.exports else "no-export"
    except CommandError:
        allowed = "blocked"
    effects[("export.run", "allow_offline")] = (blocked, allowed)
    b_block = _FakeExportWin([{"video": ghost, "path": ghost}])
    b0 = "blocked"
    try:
        b_block.command_export_batch(out_dir=str(tmp_path / "bo"), allow_offline=False)
        b0 = "ran"
    except CommandError:
        b0 = "blocked"
    b_allow = _FakeExportWin([{"video": ghost, "path": ghost}])
    b1 = "blocked"
    try:
        b_allow.command_export_batch(out_dir=str(tmp_path / "b1ok"), allow_offline=True)
        b1 = "ok" if b_allow.batch_started else "no"
    except CommandError:
        b1 = "blocked"
    effects[("export.batch", "allow_offline")] = (b0, b1)

    from cutora.batch.cli import load_queue, make_queue
    make_queue([str(tmp_path / "a.asproj.json")])
    q_a = tuple(i["project"] for i in load_queue()["items"])
    make_queue([str(tmp_path / "b.asproj.json")])
    q_b = tuple(i["project"] for i in load_queue()["items"])
    effects[("export.batch", "project_paths")] = (q_a, q_b)

    from cutora.render.export_profile import (
        delete_profile, get_profile, list_profiles, save_profile,
    )
    social = get_profile("social-vertical")
    hevc = get_profile("hevc-high")
    effects[("export.run", "profile")] = (
        build_rawvideo_ffmpeg_cmd(
            1080, 1920, 30.0, "t.mp4", use_gpu=False,
            ffmpeg_path="ffmpeg", profile=social),
        build_rawvideo_ffmpeg_cmd(
            1080, 1920, 30.0, "t.mp4", use_gpu=False,
            ffmpeg_path="ffmpeg", profile=hevc),
    )
    a = save_profile({"id": "t08a.a", "name": "A", "container": "mp4",
                      "vcodec": "h264", "quality": "compressed", "audio_codec": "aac"})
    b = save_profile({"id": "t08a.b", "name": "B", "container": "mov",
                      "vcodec": "hevc", "quality": "high", "audio_codec": "pcm_s16le"})
    effects[("export.profile.save", "id")] = (a["id"], b["id"])
    effects[("export.profile.save", "name")] = (a["name"], b["name"])
    effects[("export.profile.save", "container")] = (a["container"], b["container"])
    effects[("export.profile.save", "vcodec")] = (a["vcodec"], b["vcodec"])
    effects[("export.profile.save", "quality")] = (a["quality"], b["quality"])
    effects[("export.profile.save", "audio_codec")] = (a["audio_codec"], b["audio_codec"])
    user_before = tuple(p["id"] for p in list_profiles() if not p.get("builtin"))
    delete_profile("t08a.a")
    after_a = tuple(p["id"] for p in list_profiles() if not p.get("builtin"))
    save_profile(a)
    delete_profile("t08a.b")
    after_b = tuple(p["id"] for p in list_profiles() if not p.get("builtin"))
    effects[("export.profile.delete", "id")] = (after_a, after_b)
    assert user_before  # both existed before deletes

    specs = _export_specs()
    for name, spec in specs.items():
        for param in _spec_params(spec):
            pair = effects.get((name, param))
            assert pair is not None, "%s %s has no effect proof" % (name, param)
            left, right = pair
            assert left != right, "%s %s does not change argv/path: %r" % (
                name, param, left)
