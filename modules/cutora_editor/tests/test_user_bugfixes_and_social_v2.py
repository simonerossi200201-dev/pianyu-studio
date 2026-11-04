# -*- coding: utf-8 -*-
"""Comprehensive regression tests for user-reported timeline/crash bugs & v2 social features."""
from __future__ import annotations

import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STUDIO_ROOT = os.path.dirname(os.path.dirname(REPO))
VENV_PY = os.path.join(STUDIO_ROOT, "runtime", "venv", "Scripts", "python.exe")
FFMPEG_DIR = os.path.join(STUDIO_ROOT, "runtime", "ffmpeg")


def _child_env(tmp_ud: str | None = None) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["QT_QPA_PLATFORM"] = "offscreen"
    path = env.get("PATH", "")
    if os.path.normcase(FFMPEG_DIR) not in os.path.normcase(path):
        env["PATH"] = FFMPEG_DIR + os.pathsep + path
    if tmp_ud:
        env["CUTORA_USER_DATA"] = str(tmp_ud)
        env["CUTORA_PYTEST_SESSION_UD"] = str(tmp_ud)
    return env


def _make_sample_mp4(path: str, dur: float = 4.0, color: str = "steelblue") -> str:
    ff = os.path.join(FFMPEG_DIR, "ffmpeg.exe")
    if not os.path.isfile(ff):
        ff = "ffmpeg"
    subprocess.run(
        [
            ff, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={color}:s=320x180:r=25:d={dur}",
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={dur}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
            "-c:a", "aac", "-shortest", path,
        ],
        check=True,
    )
    return path


def test_bug1_toxic_colrv1_fonts_removed_and_blocked():
    """Toxic COLRv1 fonts that triggered DirectWrite OOM are removed and pre-blocked."""
    from cutora.ui import font_probe
    from cutora.core.paths import RESOURCES

    fonts_dir = os.path.join(RESOURCES, "fonts")
    toxic_dirs = (
        "Bitcount_Prop_Single_Ink",
        "Honk",
        "Kalnia_Glaze",
        "Nabla",
        "Sixtyfour_Convergence",
    )
    for d in toxic_dirs:
        assert not os.path.exists(os.path.join(fonts_dir, d)), f"Toxic font dir still exists: {d}"

    for fam in ("Bitcount Prop Single Ink", "Honk", "Kalnia Glaze", "Nabla", "Sixtyfour Convergence"):
        assert fam in font_probe.KNOWN_BAD_FAMILIES


def test_bug4_symmetric_transition_resize_and_inspector_sfx(tmp_path):
    """Transition resize via handle, inspector spinbox, and command_set_transition is symmetric around seam."""
    code = r"""
from cutora.core.runtime import init_torch_runtime
init_torch_runtime(render_child=False)
from PySide6.QtWidgets import QApplication
from cutora.model.timeline import Timeline
from cutora.model.timeline_model import Clip, MediaSource, Track, TransitionItem
from cutora.ui.timeline.widget import TimelineWidget
from cutora.ui.panels.property import PropertyPanel

app = QApplication.instance() or QApplication(['bug4'])

tl = Timeline(1080, 1920, 30)
s1 = MediaSource('a.mp4'); s1.duration = 5.0; s1.has_video = True
s2 = MediaSource('b.mp4'); s2.duration = 5.0; s2.has_video = True
tl.sources[s1.id] = s1
tl.sources[s2.id] = s2

tr = Track('video')
c1 = Clip(s1.id, 0.0, 4.0, 0.0)
c2 = Clip(s2.id, 0.0, 4.0, 4.0)
tr.clips.extend([c1, c2])
# Transition centered on seam_t = 4.0 with duration 1.0 -> start_t = 3.5
t_item = TransitionItem(start_t=3.5, duration=1.0, preset='cross_dissolve', domain='visual')
tr.transitions.append(t_item)
tl.tracks.append(tr)

w = TimelineWidget()
w.timeline = tl
w.pixels_per_second = 100.0
w._trans_drag_obj = t_item
w._trans_drag_side = 'right'
w._trans_orig_start = 3.5
w._trans_orig_dur = 1.0
w._trans_drag_seam = 4.0
w._trans_drag_lo = 0.0
w._trans_drag_hi = 8.0

# Dragging right handle to t=5.0 (real_x=500.0) -> half=1.0 -> duration=2.0, start_t=3.0, center stays 4.0
w._apply_transition_handle_live(500.0, asymmetric=False)
assert abs(t_item.duration - 2.0) < 1e-6, t_item.duration
assert abs(t_item.start_t - 3.0) < 1e-6, t_item.start_t
assert abs((t_item.start_t + t_item.duration * 0.5) - 4.0) < 1e-6

# Dragging left handle to t=3.2 (real_x=320.0) -> half=0.8 -> duration=1.6, start_t=3.2, center stays 4.0
w._trans_drag_side = 'left'
w._apply_transition_handle_live(320.0, asymmetric=False)
assert abs(t_item.duration - 1.6) < 1e-6, t_item.duration
assert abs(t_item.start_t - 3.2) < 1e-6, t_item.start_t
assert abs((t_item.start_t + t_item.duration * 0.5) - 4.0) < 1e-6

# PropertyPanel inspector duration change also keeps center at 4.0 and exposes 100 SFX
prop = PropertyPanel()
prop.load_data('transition', (tr, t_item))
assert hasattr(prop, 'trans_sfx_cb')
assert prop.trans_sfx_cb.count() >= 102, prop.trans_sfx_cb.count()
assert hasattr(prop, 'trans_sfx_preview_btn')
prop.trans_dur_sp.setValue(2.4)
assert abs(t_item.duration - 2.4) < 1e-6, t_item.duration
assert abs((t_item.start_t + t_item.duration * 0.5) - 4.0) < 1e-6, (t_item.start_t, t_item.duration)
print('BUG4_OK')
"""
    r = subprocess.run(
        [VENV_PY if os.path.isfile(VENV_PY) else sys.executable, "-c", code],
        cwd=REPO, env=_child_env(str(tmp_path)),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=90,
    )
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    assert r.returncode == 0 and "BUG4_OK" in (r.stdout or ""), out[-4000:]


def test_bugs_1_2_3_and_batch_transitions_e2e(tmp_path):
    """End-to-end test for:
    - Bug 2A: Dropping external video directly onto empty timeline auto-creates task & retains video
    - Bug 1B: New project + manual video track + dropping external video promotes main video & doesn't crash
    - Bug 2B: Cross-track dragging a video clip with linked audio onto the main video track works smoothly
    - Bug 3: First time right-trimming a video clip extends its linked audio clip simultaneously
    - Feature 6: Batch applying a transition from TransitionPanel to seams, video/split-screen, and subtitles/text
    """
    v1 = _make_sample_mp4(str(tmp_path / "clip1.mp4"), dur=3.0, color="navy")
    v2 = _make_sample_mp4(str(tmp_path / "clip2.mp4"), dur=4.0, color="crimson")

    code = f"""
from cutora.core.runtime import init_torch_runtime
init_torch_runtime(render_child=False)
import time
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication
from cutora.ui.main_window import MainWindow
from cutora.render.frame_renderer import FrameRenderer

app = QApplication.instance() or QApplication(['e2e-bugs'])
win = MainWindow()
win._timeline_template = {{"width": 1080, "height": 1920, "fps": 30.0}}
win.show()
QApplication.processEvents()

def wait_probes(tl_obj, timeout=5.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        QApplication.processEvents()
        srcs = list((getattr(tl_obj, 'sources', None) or {{}}).values())
        if srcs and all(getattr(s, 'duration', None) is not None for s in srcs):
            break
        time.sleep(0.02)
    QApplication.processEvents()

v1 = {v1!r}
v2 = {v2!r}

# --- Scenario A (Bug 2A): Empty timeline (curr_idx < 0), drop external video directly onto timeline ---
win.tasks.clear()
win.file_list.clear()
win.curr_idx = -1
win._handle_timeline_drop_batch({{"paths": [v1], "time": 0.0, "track": None, "new_track": False}})
assert win.curr_idx == 0, f'Expected curr_idx=0 after timeline drop, got {{win.curr_idx}}'
task = win.tasks[0]
tl = task.get('timeline')
wait_probes(tl)
assert task.get('video') == v1, f'Expected task video promoted to v1, got {{task.get("video")}}'
v_tracks = [t for t in tl.tracks if t.type == 'video']
assert len(v_tracks) >= 1 and len(v_tracks[0].clips) == 1, v_tracks

# --- Scenario B (Bug 1B): New project + manual video track + drop external video ---
win.tasks.clear()
win.file_list.clear()
win.curr_idx = -1
win._timeline_template = {{"width": 1080, "height": 1920, "fps": 30.0}}
QApplication.processEvents()
win.shortcut_add_track('video')
QApplication.processEvents()
assert win.curr_idx == 0
tl = win.tasks[0]['timeline']
v_tracks = [t for t in tl.tracks if t.type == 'video']
assert len(v_tracks) >= 1
win._handle_timeline_drop_batch({{"paths": [v1], "time": 0.0, "track": v_tracks[0], "new_track": False}})
wait_probes(tl)
assert win.tasks[0].get('video') == v1
assert v_tracks[0].is_main_video is True
assert len(v_tracks[0].clips) == 1

# --- Scenario C (Bug 2B): Drop second video on a second video track, then cross-track drag it to main video track ---
win.shortcut_add_track('video')
QApplication.processEvents()
v_tracks = [t for t in tl.tracks if t.type == 'video']
assert len(v_tracks) >= 2
sec_v_track = [t for t in v_tracks if t is not v_tracks[0]][0]
win._handle_timeline_drop_batch({{"paths": [v2], "time": 3.0, "track": sec_v_track, "new_track": False}})
wait_probes(tl)
assert len(sec_v_track.clips) == 1
moved_clip = sec_v_track.clips[0]
# Select the clip (which has linked audio) and commit a cross-track move to v_tracks[0]
tl_w = win.timeline
tl_w._select_video_clip(sec_v_track, moved_clip)
assert len(tl_w._clip_sel) >= 2, f'Expected linked AV selection, got {{tl_w._clip_sel}}'
tl_w._clip_group_begin(moved_clip)
tl_w._drag_target_track = v_tracks[0]
tl_w._clip_group_apply(0.0)
assert tl_w._clip_group_commit(sec_v_track, moved_clip, 3.0) is True
assert moved_clip in v_tracks[0].clips, 'Video clip must move to main video track'
assert moved_clip not in sec_v_track.clips

# --- Scenario D (Bug 3): Trim video clip shorter, then first-time right-trim to restore length -> linked audio extends too ---
a_tracks = [t for t in tl.tracks if t.type == 'audio']
linked_audio = None
for atr in a_tracks:
    for ac in atr.clips:
        if getattr(ac, 'link_group_id', None) and ac.link_group_id == getattr(moved_clip, 'link_group_id', None):
            linked_audio = (atr, ac)
            break
assert linked_audio is not None, 'Moved video clip must have linked audio clip'
atr, ac = linked_audio
moved_clip.out_t = 2.0
ac.out_t = 2.0
tl_w._select_video_clip(v_tracks[0], moved_clip, keep_group=True)
tl_w._begin_av_linked_trim(moved_clip, 'right')
assert tl_w._av_trim_partners is not None, 'First right-trim must find linked audio partner'
# Simulate extending out_t to 3.8s
moved_clip.out_t = 3.8
tl_w._apply_av_trim_partners_live(moved_clip)
assert abs(ac.out_t - 3.8) < 1e-6, ac.out_t

# --- Scenario E (Feature 6): Batch apply transition to seams, video/split-screen, and subtitles/text ---
win.tasks[0]['segments'] = [
    {{'start': 0.0, 'end': 2.0, 'text': '第一句测试字幕'}},
    {{'start': 2.2, 'end': 4.5, 'text': '第二句测试字幕'}},
]
win.tasks[0]['text_overlays'] = [
    {{'type': 'text', 'content': '标题文字', 'start': 0.0, 'end': 3.0, 'x': 100, 'y': 100, 'w': 400, 'h': 100}},
]
win.tasks[0]['image_overlays'] = [
    {{'type': 'image', 'content': '', 'start': 0.0, 'end': 3.0, 'x': 100, 'y': 300, 'w': 300, 'h': 300}},
]

tp = win.transition_panel
assert tp.video_count() == 145, f'Expected 145 visual transitions, got {{tp.video_count()}}'
tp.set_active_preset('cam_push_drift_pull_drift')
tp._on_batch_apply_seams()
assert len(v_tracks[0].transitions) >= 1
assert v_tracks[0].transitions[0].preset == 'cam_push_drift_pull_drift'

tp._on_batch_apply_video_split()
assert all(getattr(c, 'transition_preset', '').startswith('cam_push_drift_pull_drift') for c in v_tracks[0].clips)
assert win.tasks[0]['image_overlays'][0]['transition_preset'] == 'cam_push_drift_pull_drift_both'

tp._on_batch_apply_subs_text()
assert all(s.get('transition_preset') == 'cam_push_drift_pull_drift_both' for s in win.tasks[0]['segments'])
assert win.tasks[0]['text_overlays'][0]['transition_preset'] == 'cam_push_drift_pull_drift_both'

# Verify FrameRenderer.draw_subtitle renders a subtitle with transition_preset cleanly
canvas_img = QImage(640, 360, QImage.Format.Format_ARGB32_Premultiplied)
canvas_img.fill(0xFF112233)
p = QPainter(canvas_img)
try:
    rect = FrameRenderer.draw_subtitle(
        p, win.tasks[0]['segments'][0], dict(win.default_style),
        0.15, 640, 360,
    )
finally:
    p.end()

print('E2E_ALL_OK')
"""
    r = subprocess.run(
        [VENV_PY if os.path.isfile(VENV_PY) else sys.executable, "-c", code],
        cwd=REPO, env=_child_env(str(tmp_path)),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120,
    )
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    assert r.returncode == 0 and "E2E_ALL_OK" in (r.stdout or ""), out[-4000:]
