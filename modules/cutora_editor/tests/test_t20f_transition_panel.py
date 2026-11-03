# -*- coding: utf-8 -*-
"""T20-f: left-dock transition panel lists video + audio libraries."""
from __future__ import annotations

import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
FFMPEG_DIR = os.path.join(REPO, "runtime", "ffmpeg")


def _child_env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["QT_QPA_PLATFORM"] = "offscreen"
    path = env.get("PATH", "")
    if os.path.normcase(FFMPEG_DIR) not in os.path.normcase(path):
        env["PATH"] = FFMPEG_DIR + os.pathsep + path
    return env


def test_transition_panel_lists_video_and_audio():
    from cutora.render.anim_engine import (
        AUDIO_TRANSITION_LIBRARY,
        STANDALONE_TRANSITION_LIBRARY,
    )

    assert len(STANDALONE_TRANSITION_LIBRARY) >= 11
    assert len(AUDIO_TRANSITION_LIBRARY) == 5
    audio_presets = [p for _lab, p in AUDIO_TRANSITION_LIBRARY]
    assert audio_presets == [
        "audio_xfade_equal",
        "audio_xfade_linear",
        "audio_fade_in",
        "audio_fade_out",
        "audio_seam_fade",
    ]
    audio_labels = [lab for lab, _p in AUDIO_TRANSITION_LIBRARY]
    assert "等功率交叉淡化" in audio_labels
    assert "线性交叉淡化" in audio_labels
    assert "淡入" in audio_labels
    assert "淡出" in audio_labels
    assert "接缝淡化" in audio_labels

    code = r"""
from cutora.core.runtime import init_torch_runtime
init_torch_runtime(render_child=False)
from PySide6.QtWidgets import QApplication, QLabel
from cutora.ui.panels.transition_panel import TransitionPanel, AUDIO_ITEMS, VIDEO_ITEMS
from cutora.render.anim_engine import (
    AUDIO_TRANSITION_LIBRARY, STANDALONE_TRANSITION_LIBRARY,
)
app = QApplication.instance() or QApplication(['t20f'])
p = TransitionPanel()
p.show()
assert p.objectName() == 'transitionPanel'
assert VIDEO_ITEMS == list(STANDALONE_TRANSITION_LIBRARY)
assert AUDIO_ITEMS == list(AUDIO_TRANSITION_LIBRARY)
labels = [w.text() for w in p.findChildren(QLabel) if w.text()]
blob = ' '.join(labels)
assert '视频转场' in blob, blob
assert '音频转场' in blob, blob
assert p.video_count() == len(STANDALONE_TRANSITION_LIBRARY)
assert p.video_count() >= 11
assert p.audio_count() == len(AUDIO_TRANSITION_LIBRARY) == 5
print('ok')
"""
    r = subprocess.run(
        [VENV_PY if os.path.isfile(VENV_PY) else sys.executable, "-c", code],
        cwd=REPO, env=_child_env(),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=90,
    )
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    assert r.returncode == 0 and "ok" in (r.stdout or ""), out[-4000:]


def test_audio_transition_fades_export_records():
    from cutora.model.seams import apply_audio_transition_fades
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track, TransitionItem

    tl = Timeline(1080, 1920, 30)
    tr = Track("audio")
    left = Clip("src-a", 0.0, 2.0, 0.0)
    right = Clip("src-b", 0.0, 2.0, 2.0)
    left.is_main = right.is_main = True
    left.audio_role = right.audio_role = "main"
    tr.clips.extend([left, right])
    tr.transitions.append(TransitionItem(
        start_t=1.825, duration=0.35, preset="audio_xfade_equal",
        domain="audio",
    ))
    tl.tracks.append(tr)
    recs = apply_audio_transition_fades({"timeline": tl})
    assert len(recs) == 2
    assert recs[0]["fade_out_dur"] >= 0.35 - 1e-9
    assert recs[1]["fade_in_dur"] >= 0.35 - 1e-9
    assert recs[0]["fade_curve"] == "esin"
    assert recs[1]["fade_curve"] == "esin"
    assert recs[0]["clip"] is left
    assert recs[1]["clip"] is right


def _audio_xfade_task(wav_a, wav_b):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track, TransitionItem

    tl = Timeline(1080, 1920, 30)
    src_a = MediaSource(wav_a)
    src_b = MediaSource(wav_b)
    src_a.has_video = src_b.has_video = False
    src_a.has_audio = src_b.has_audio = True
    src_a.duration = src_b.duration = 2.0
    tl.sources[src_a.id] = src_a
    tl.sources[src_b.id] = src_b
    tr = Track("audio")
    left = Clip(src_a.id, 0.0, 2.0, 0.0)
    right = Clip(src_b.id, 0.0, 2.0, 2.0)
    left.is_main = right.is_main = True
    left.audio_role = right.audio_role = "main"
    tr.clips.extend([left, right])
    tr.transitions.append(TransitionItem(
        start_t=1.825, duration=0.35, preset="audio_xfade_equal",
        domain="audio",
    ))
    tl.tracks.append(tr)
    return {"timeline": tl, "audio_fx": {"spatial_enabled": False}}, left, right


def test_preview_mix_applies_audio_transition_fades(tmp_path):
    """mix_rows_for_task must pick up TransitionItem fades + esin, matching export."""
    import wave
    import struct
    from cutora.asr.timeline_wav import mix_rows_for_task
    from cutora.model.seams import apply_audio_transition_fades
    from cutora.render.render_worker import RenderWorker

    def _tone(path):
        n = 48000 * 2
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(48000)
            w.writeframes(b"".join(struct.pack("<h", 8000) for _ in range(n)))
        return path

    wav_a = _tone(str(tmp_path / "a.wav"))
    wav_b = _tone(str(tmp_path / "b.wav"))
    task, left, right = _audio_xfade_task(wav_a, wav_b)
    recs = apply_audio_transition_fades(task)
    rows = mix_rows_for_task(task)
    assert len(rows) == 2
    assert rows[0]["fade_out_dur"] >= 0.35 - 1e-9
    assert rows[1]["fade_in_dur"] >= 0.35 - 1e-9
    assert rows[0]["fade_curve"] == "esin"
    assert rows[1]["fade_curve"] == "esin"
    assert recs[0]["fade_curve"] == rows[0]["fade_curve"]
    assert abs(recs[0]["fade_out_dur"] - rows[0]["fade_out_dur"]) < 1e-9
    rw = RenderWorker.__new__(RenderWorker)
    rw.export_audio_rate = 48000
    tracks = rw._build_audio_tracks(task)
    assert tracks[0]["fade_curve"] == "esin"
    assert abs(tracks[0]["fade_out_dur"] - rows[0]["fade_out_dur"]) < 1e-9


def test_preview_wav_filter_uses_esin_curve(tmp_path, monkeypatch):
    import struct
    import subprocess
    import wave
    from cutora.asr.timeline_wav import render_preview_wav

    def _tone(path):
        n = 48000 * 2
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(48000)
            w.writeframes(b"".join(struct.pack("<h", 8000) for _ in range(n)))
        return path

    wav_a = _tone(str(tmp_path / "a.wav"))
    wav_b = _tone(str(tmp_path / "b.wav"))
    task, _l, _r = _audio_xfade_task(wav_a, wav_b)
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = list(cmd)
        script = cmd[cmd.index("-filter_complex_script") + 1]
        captured["fc"] = open(script, encoding="utf-8").read()
        dest = cmd[-1]
        n = 48000
        with wave.open(dest, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(48000)
            w.writeframes(b"\x00\x00" * n * 2)
        class R:
            returncode = 0
            stderr = b""
        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    render_preview_wav(task, str(tmp_path / "out.wav"))
    cmd = captured["cmd"]
    fc = captured["fc"]
    assert "-filter_complex" not in cmd
    assert "curve=esin" in fc, fc
    assert "afade=t=in" in fc, fc
    assert "afade=t=out" in fc, fc


def _run_qt(code: str, tmp_ud: str | None = None) -> None:
    env = _child_env()
    if tmp_ud:
        env["CUTORA_USER_DATA"] = str(tmp_ud)
        env["CUTORA_PYTEST_SESSION_UD"] = str(tmp_ud)
    r = subprocess.run(
        [VENV_PY if os.path.isfile(VENV_PY) else sys.executable, "-c", code],
        cwd=REPO, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=90,
    )
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    assert r.returncode == 0 and "ALL_OK" in (r.stdout or ""), out[-4000:]


def test_transition_samples_and_static_thumbnails(tmp_path):
    """samples_available() is True and every video card renders a 128x72 16:9 sample frame."""
    code = r"""
from cutora.core.runtime import init_torch_runtime
init_torch_runtime(render_child=False)
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication
from cutora.ui.panels.transition_panel import (
    THUMB_H, THUMB_W, TransitionPanel, samples_available,
)

app = QApplication.instance() or QApplication(['t20f-samples'])
assert samples_available() is True, 'real 16:9 transition samples must be available'
p = TransitionPanel()
p.resize(470, 680)
p.show()
QApplication.processEvents()

assert len(p._video_cards) >= 55
for card in p._video_cards:
    assert card.thumb.size() == QSize(THUMB_W, THUMB_H) == QSize(128, 72)
    pix = card.thumb.static_pixmap()
    assert pix is not None and not pix.isNull(), f'null pixmap for {card.preset}'
    dpr = max(1.0, float(pix.devicePixelRatio()))
    assert int(round(pix.width() / dpr)) == 128, (card.preset, pix.width(), dpr)
    assert int(round(pix.height() / dpr)) == 72, (card.preset, pix.height(), dpr)
    img = pix.toImage()
    colors = {
        img.pixel(x, y)
        for y in range(0, img.height(), 4)
        for x in range(0, img.width(), 4)
    }
    assert len(colors) >= 50, f'{card.preset} thumbnail is too uniform ({len(colors)} colors)'
print('ALL_OK')
"""
    _run_qt(code, str(tmp_path))


def test_transition_category_tabs_and_search(tmp_path):
    """Category tabs (hot/all/camera/rhythm/graphic/light/audio/fav) and zh/en/pinyin search."""
    code = r"""
from cutora.core.runtime import init_torch_runtime
init_torch_runtime(render_child=False)
from PySide6.QtWidgets import QApplication
from cutora.ui.panels.transition_panel import (
    AUDIO_ITEMS, VIDEO_ITEMS, TransitionPanel, pinyin_initials,
)

app = QApplication.instance() or QApplication(['t20f-tabs-search'])
p = TransitionPanel()
p.resize(470, 680)
p.show()
QApplication.processEvents()

for cat in ('hot', 'all', 'camera', 'rhythm', 'graphic', 'light', 'audio', 'fav'):
    assert cat in p._cat_buttons, f'missing category tab {cat}'
    p._set_category(cat)
    QApplication.processEvents()
    assert p.active_category() == cat
    assert p._cat_buttons[cat].isChecked() is True
    for other, btn in p._cat_buttons.items():
        if other != cat:
            assert btn.isChecked() is False
    matched = p.matching_presets('', cat)
    if cat == 'all':
        assert len(matched) == len(VIDEO_ITEMS) + len(AUDIO_ITEMS)
        assert p._video_box.isVisible() is True
        assert p._audio_box.isVisible() is True
    elif cat == 'audio':
        assert len(matched) == len(AUDIO_ITEMS) == 5
        assert p._video_box.isVisible() is False
        assert p._audio_box.isVisible() is True
    elif cat == 'fav':
        assert matched == []
        assert p._empty_lbl.isVisible() is True
    else:
        assert len(matched) >= 2, f'category {cat} should not be empty'
        assert p._video_box.isVisible() is True
        assert p._audio_box.isVisible() is False

# Chinese name search
zh_hits = p.matching_presets('溶解', 'all')
assert 'cross_dissolve' in zh_hits and 'blur_dissolve' in zh_hits, zh_hits

# English ID search
en_hits = p.matching_presets('whip_pan_left', 'all')
assert 'whip_pan_left' in en_hits, en_hits
en_audio = p.matching_presets('audio_xfade_equal', 'all')
assert 'audio_xfade_equal' in en_audio, en_audio

# Pinyin initials search
assert pinyin_initials('交叉溶解') == 'jcrj'
py_hits = p.matching_presets('jcrj', 'all')
assert 'cross_dissolve' in py_hits, py_hits
py_sj = p.matching_presets('sjrj', 'all')
assert 'bokeh_dissolve' in py_sj, py_sj

# UI search box updates visible cards and empty state
p._set_category('all')
p._search_edit.setText('jcrj')
QApplication.processEvents()
vis_presets = [c.preset for c in p._video_cards if c.isVisible()]
assert 'cross_dissolve' in vis_presets
assert p._empty_lbl.isVisible() is False

p._search_edit.setText('nonexistent_transition_xyz_999')
QApplication.processEvents()
assert p._empty_lbl.isVisible() is True
p._search_edit.setText('')
QApplication.processEvents()
print('ALL_OK')
"""
    _run_qt(code, str(tmp_path))


def test_transition_favorites_recent_and_sfx_persistence(tmp_path):
    """set_favorite, note_used, and sfx_enabled / set_sfx_enabled persist across panels."""
    code = r"""
from cutora.core.runtime import init_torch_runtime
init_torch_runtime(render_child=False)
from PySide6.QtWidgets import QApplication
from cutora.ui.panels.transition_panel import (
    RECENT_MAX, TransitionPanel, set_sfx_enabled, sfx_enabled,
)

app = QApplication.instance() or QApplication(['t20f-persist'])
p = TransitionPanel()
p.resize(470, 680)
p.show()
QApplication.processEvents()

# Favorites persistence
p.set_favorite('cross_dissolve', True)
p.set_favorite('whip_pan_left', True)
assert p.favorites() == ['cross_dissolve', 'whip_pan_left']
p._set_category('fav')
QApplication.processEvents()
assert set(p.matching_presets('', 'fav')) == {'cross_dissolve', 'whip_pan_left'}
assert p._empty_lbl.isVisible() is False

p.set_favorite('cross_dissolve', False)
assert p.favorites() == ['whip_pan_left']

# Recently used persistence & cap at RECENT_MAX
presets = [c.preset for c in p._video_cards[:RECENT_MAX + 3]]
for pr in presets:
    p.note_used(pr, 'visual')
p._after_recent_change()
QApplication.processEvents()
assert len(p.recent()) == RECENT_MAX
assert p.recent()[0] == presets[-1]
# Re-using an existing preset moves it to index 0 without duplication
target = p.recent()[-1]
p.note_used(target, 'visual')
p._after_recent_change()
assert p.recent()[0] == target
assert len(p.recent()) == len(set(p.recent())) == RECENT_MAX

p._set_category('hot')
QApplication.processEvents()
assert p._recent_box.isVisible() is True

# SFX toggle
set_sfx_enabled(True)
assert sfx_enabled() is True
set_sfx_enabled(False)
assert sfx_enabled() is False
p._sfx_check.setChecked(True)
QApplication.processEvents()
assert p.sfx_enabled() is True
assert sfx_enabled() is True

# Second panel instance restores favorites, recent, active tab, and sfx state
p2 = TransitionPanel()
assert p2.favorites() == ['whip_pan_left']
assert p2.recent() == p.recent()
assert p2.active_category() == 'hot'
assert p2.sfx_enabled() is True
p2._sfx_check.setChecked(False)
QApplication.processEvents()
assert p2.sfx_enabled() is False
assert sfx_enabled() is False
print('ALL_OK')
"""
    _run_qt(code, str(tmp_path))


def test_transition_hover_frame_lru_and_perf_stats(tmp_path):
    """_FRAMES LRU enforces entry/byte caps and perf_stats() tracks hover/paint metrics."""
    code = r"""
from cutora.core.runtime import init_torch_runtime
init_torch_runtime(render_child=False)
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication
from cutora.ui.panels.transition_panel import (
    ANIM_FRAMES, _FRAMES, _FrameLRU, TransitionPanel, perf_stats, reset_perf,
)

app = QApplication.instance() or QApplication(['t20f-lru-perf'])

# Direct unit test of _FrameLRU entry cap and byte cap
lru = _FrameLRU(max_entries=3, max_bytes=1024 * 1024)
fake_pix = QPixmap(32, 32)
for i in range(5):
    k = ('preset_%d' % i, 'camera', False, 1.0)
    lst = lru.frames(k, 4)
    lru.store(k, 0, fake_pix)
    assert len(lru) <= 3, f'LRU exceeded max_entries: {len(lru)}'
assert ('preset_0', 'camera', False, 1.0) not in lru._d
assert ('preset_1', 'camera', False, 1.0) not in lru._d
assert ('preset_4', 'camera', False, 1.0) in lru._d

# Touching preset_2 moves it to MRU so preset_3 is evicted next
lru.frames(('preset_2', 'camera', False, 1.0), 4)
lru.frames(('preset_5', 'camera', False, 1.0), 4)
assert ('preset_2', 'camera', False, 1.0) in lru._d
assert ('preset_3', 'camera', False, 1.0) not in lru._d

# Byte cap eviction
tiny_lru = _FrameLRU(max_entries=100, max_bytes=20000)
big_pix = QPixmap(64, 64)  # ~16 KB at 32-bit depth
for i in range(4):
    k = ('b_%d' % i, 'camera', False, 1.0)
    tiny_lru.frames(k, 2)
    tiny_lru.store(k, 0, big_pix)
assert tiny_lru.total_bytes <= 20000
assert len(tiny_lru) == 1

# Panel hover playback populates _FRAMES and perf_stats()
_FRAMES.clear()
reset_perf()
st0 = perf_stats()
assert st0['tick']['n'] == 0 and st0['paint']['n'] == 0
assert st0['anim_cache_presets'] == 0

p = TransitionPanel()
p.resize(470, 680)
p.show()
QApplication.processEvents()

card = p._video_cards[0]
card.thumb.set_playing(True)
for _ in range(6):
    card.thumb._advance(step=1)
card.thumb.repaint()
QApplication.processEvents()
card.thumb.set_playing(False)

st = perf_stats()
assert st['tick']['n'] >= 6, st
assert st['paint']['n'] >= 1, st
assert st['anim_cache_presets'] >= 1, st
assert st['anim_cache_bytes'] > 0, st
assert st['static_cache_count'] >= 1, st
assert st['static_cache_bytes'] > 0, st
assert st['sample_bytes'] > 0, st
assert len(_FRAMES) <= _FRAMES.max_entries
assert _FRAMES.total_bytes <= _FRAMES.max_bytes
print('ALL_OK')
"""
    _run_qt(code, str(tmp_path))

