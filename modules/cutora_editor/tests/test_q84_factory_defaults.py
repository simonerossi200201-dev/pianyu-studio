# -*- coding: utf-8 -*-
"""Q84 factory defaults: one source for new overlays, clips, tapes, tracks.

Image position/size is the fitted, centered rect — not a constant, and not
the old 100,100,300,300 box.
"""
from __future__ import annotations

from cutora.model.factory_defaults import (
    clip_defaults,
    image_fit_rect,
    overlay_defaults,
    preset_param_defaults,
    track_defaults,
    transition_defaults,
)
from cutora.model.timeline_model import Clip, Track, TransitionItem
from cutora.render.grade import identity_grade
from cutora.render.split_layout import default_split
from cutora.render.track_transform import track_props_of


def test_q84_image_fit_never_upscales_and_centers():
    # Wider than the canvas: scale down, center. 400x100 into 200x200 → 200x50.
    x, y, w, h = image_fit_rect(400, 100, 200, 200)
    assert (w, h) == (200, 50), (w, h)
    assert abs(x - 0.0) < 1e-9
    assert abs(y - 75.0) < 1e-9
    # Smaller than the canvas: stay at native size, centered. Not a constant.
    x, y, w, h = image_fit_rect(80, 40, 320, 180)
    assert (w, h) == (80, 40)
    assert abs(x - 120.0) < 1e-9
    assert abs(y - 70.0) < 1e-9


def test_q84_overlay_defaults_image_uses_fit_rect():
    ov = overlay_defaults(
        "image", canvas_w=320, canvas_h=180, image_wh=(80, 40), path="pic.png",
        start=1.0, end=6.0, track_id="T", ov_id="ov_fixed")
    x, y, w, h = image_fit_rect(80, 40, 320, 180)
    # Overlay box is the canvas. The fitted, centered rect is the element.
    assert (ov["x"], ov["y"], ov["w"], ov["h"]) == (0, 0, 320, 180)
    el = ov["elements"][0]
    assert el["path"] == "pic.png"
    assert (el["x"], el["y"], el["w"], el["h"]) == (x, y, w, h)
    assert ov["id"] == "ov_fixed"
    assert ov["_track_id"] == "T"
    assert ov["scale"] == 1.0 and ov["lock_aspect"] is True
    assert "content" not in ov


def test_q84_overlay_defaults_text_is_the_creation_dict():
    ov = overlay_defaults("text", canvas_w=1080, canvas_h=1920, end=8.0)
    assert ov["type"] == "text"
    assert ov["fontsize"] == 80
    assert ov["font_family"] == "Arimo"
    assert ov["bold"] is True
    assert ov["use_stroke"] is True
    assert ov["stroke_width"] == 4
    assert ov["bg_enabled"] is False
    # Q124：文本框不再是整块画布 —— 宽 78%、居中、落在下三分之一，
    # 高先按一行估，第一次绘制 auto_height 改成真实文字高度。
    from cutora.model.factory_defaults import default_text_box
    assert (ov["x"], ov["y"], ov["w"], ov["h"]) == default_text_box(1080, 1920)
    assert 0 < ov["x"] and ov["w"] < 1080 and ov["h"] < 1920
    assert ov["fit_mode"] == "auto_height"
    assert ov["lock_aspect"] is False
    assert ov["end"] == 8.0
    assert ov["content"] == "Generato da IA"
    assert ov["text_layout"]["align"] == "center"
    assert ov["text_layout"]["valign"] == "top"
    assert ov["text_layout"]["line_spacing"] == 0


def test_q84_clip_defaults_match_constructor_and_identity_grade():
    clip = Clip("src", 0.0, 1.0, 0.0)
    d = clip_defaults()
    assert d["fit_mode"] == clip.fit_mode == "fill"
    assert d["x"] == clip.x == 0.0
    assert d["y"] == clip.y == 0.0
    assert d["scale"] == clip.scale == 1.0
    assert d["rotation"] == clip.rotation == 0.0
    assert d["flip_x"] is clip.flip_x is False
    assert d["speed"] == clip.speed == 1.0
    assert d["alpha_mode"] == clip.alpha_mode == "straight"
    assert d["transition_preset"] == clip.transition_preset
    assert d["transition_duration"] == clip.transition_duration
    assert d["opacity"] == 255
    assert d["crop_left"] == 0 and d["feather"] == 0
    assert d["grade"] == identity_grade()
    assert d["grade"]["exposure"] == 0.0
    assert abs(d["grade"]["pivot"] - 0.18) < 1e-9


def test_q84_media_source_alpha_mode_matches_clip(monkeypatch):
    monkeypatch.setattr(
        "cutora.model.timeline_model.probe_duration_sec", lambda _path: 0.0)
    from cutora.model.timeline_model import MediaSource
    src = MediaSource("missing.mp4")
    assert src.alpha_mode == clip_defaults()["alpha_mode"]
    assert src.alpha is False
    assert src.has_video is True and src.has_audio is True


def test_q84_transition_defaults_follow_constructor_and_preset():
    bare = transition_defaults("cross_dissolve")
    item = TransitionItem(0.0, bare["duration"], "cross_dissolve")
    assert bare["zoom_scale_start"] == item.zoom_scale_start == 0.7
    assert bare["motion_blur"] == item.motion_blur == 0.4
    assert bare["intensity"] == 1.0
    assert bare["easing"] == "ease_in_out"
    assert bare["feather"] == 0.0
    assert bare["direction"] == "left"
    angle = transition_defaults("wipe_angle")
    sig = preset_param_defaults("wipe_angle")
    assert sig["direction"] == 32.0
    assert angle["direction"] == 32.0
    assert angle["feather"] == 0.0
    smash = transition_defaults("smash_zoom")
    assert smash["easing"] == "ease_in"
    assert abs(smash["motion_blur"] - 0.4) < 1e-9


def test_q84_track_defaults_match_constructor_and_split():
    track = Track("video")
    d = track_defaults()
    assert d["props"] == track_props_of(track)
    assert d["props"]["x"] == 0.0 and d["props"]["scale"] == 1.0
    assert d["props"]["opacity"] == 1.0
    assert d["props"]["blend_mode"] == "normal"
    assert d["blend_mode"] == track.blend_mode
    assert d["split_cell"] is None
    assert d["name"] == ""
    split = d["split"]
    assert split["enabled"] is False
    assert split["layout"] == default_split()["layout"] == "top_bottom"
    assert abs(split["dividers"][0]["pos"] - 0.5) < 1e-9
    assert split["dividers"][0]["rotation_deg"] == 0.0
    assert split["dividers"][0]["feather_px"] == 24.0
    assert split["border"]["style"] == "none"
    assert split["border"]["width_px"] == 12
    assert split["border"]["curve"] == "straight"
    assert split["border"]["shadow_px"] == 12
    assert d["grade"] == identity_grade()
