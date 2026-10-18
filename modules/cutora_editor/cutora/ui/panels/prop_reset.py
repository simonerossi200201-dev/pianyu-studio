# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Resolve inspector ``reset_key`` values for PropertyPanel.

One function per key, all going through the same save path a user edit
uses (``on_change_ov`` / ``on_change_video`` / ``on_change_transition`` /
``_commit_track_props`` / grade ``changed``). Callers pass the key; they
do not each invent a lambda.
"""
from __future__ import annotations

import os

from PySide6.QtWidgets import QComboBox

from cutora.core.log import get_logger
from cutora.model.factory_defaults import (
    clip_defaults,
    image_fit_rect,
    overlay_defaults,
    text_overlay_fx_defaults,
    track_defaults,
    transition_defaults,
)
from cutora.render.anim_engine import DEFAULT_TRANSITION_DURATION
from cutora.ui.widgets.inspector import SegmentedChoice, ValueCombo

_LOG = get_logger("cutora.ui.panels.prop_reset")


def resolve_reset_key(panel, key: str) -> None:
    """Apply ``key`` inside one undo, unless a section reset already opened one."""
    opened = False
    if not getattr(panel, "_reset_undo_open", False):
        opener = getattr(panel, "_open_reset_undo", None)
        if callable(opener):
            opened = bool(opener("重置"))
    try:
        apply_reset_key(panel, key)
    finally:
        if opened:
            closer = getattr(panel, "_close_reset_undo", None)
            if callable(closer):
                closer()


def apply_reset_key(panel, key: str) -> None:
    fn = _APPLIERS.get(str(key or ""))
    if fn is None:
        _LOG.warning("unknown inspector reset_key %r", key)
        return
    fn(panel)


def reset_header_toggle(panel, sec) -> None:
    """描边 / 背景 / 发光 / 投影 capsules back to the creation fx."""
    if getattr(panel, "current_type", None) != "overlay":
        return
    ov = getattr(panel, "current_data", None)
    if not isinstance(ov, dict) or ov.get("type") != "text":
        return
    fx = text_overlay_fx_defaults()
    pairs = (
        ("ov_stroke_grp", "ov_strk", "stroke"),
        ("ov_bg_grp", "ov_bg_enable", "bg"),
        ("ov_glow_grp", "ov_glow_enable", "glow"),
        ("ov_shadow_grp", "ov_shadow_enable", "shadow"),
    )
    for sec_name, widget_name, family in pairs:
        if getattr(panel, sec_name, None) is not sec:
            continue
        widget = getattr(panel, widget_name, None)
        if widget is None:
            return
        block = fx.get(family) if isinstance(fx.get(family), dict) else {}
        _assign_check(widget, bool(block.get("enabled")))
        panel.on_change_ov()
        return


def extra_section_reset(panel, sec) -> None:
    """Controls that are not an InspectorRow: wheels, LUT file, scope, split switch.

    Editing a divider forces split on (that command's own rule). After the
    rows have restored positions, put the switch back to the factory off.
    """
    if sec is getattr(panel, "_sec_split", None) and hasattr(panel, "trk_split_enable"):
        _assign_check(panel.trk_split_enable, False)
        panel._exec_split("split.set_layout", {"enabled": False})
    for name in ("vid_grade", "trk_grade", "prj_grade"):
        grade = getattr(panel, name, None)
        fn = getattr(grade, "reset_extra", None)
        if callable(fn):
            fn(sec)


def _canvas_of(panel) -> tuple[int, int]:
    task = None
    current = getattr(panel, "_win_and_task", None)
    if callable(current):
        _win, task = current()
    if isinstance(task, dict):
        dims = task.get("dims")
        if isinstance(dims, (list, tuple)) and len(dims) >= 2:
            try:
                return max(1, int(dims[0])), max(1, int(dims[1]))
            except (TypeError, ValueError):
                pass
        tl = task.get("timeline")
        w = getattr(tl, "width", None) if tl is not None else None
        h = getattr(tl, "height", None) if tl is not None else None
        if w and h:
            try:
                return max(1, int(w)), max(1, int(h))
            except (TypeError, ValueError):
                pass
    return 1080, 1920


def _image_wh_of(ov) -> tuple[int, int]:
    path = ""
    if isinstance(ov, dict):
        path = str(ov.get("path") or ov.get("content") or "")
        els = ov.get("elements") or []
        if els and isinstance(els[0], dict):
            path = str(els[0].get("path") or path)
        if path and os.path.isfile(path):
            from cutora.assets.user import _image_wh
            return _image_wh(path)
        ratio = ov.get("aspect_ratio")
        if isinstance(ratio, (int, float)) and float(ratio) > 0:
            ih = 200
            return max(1, int(round(float(ih) * float(ratio)))), ih
    return 200, 200


def _image_rect(panel, ov):
    cw, ch = _canvas_of(panel)
    iw, ih = _image_wh_of(ov if isinstance(ov, dict) else {})
    return image_fit_rect(iw, ih, cw, ch), (iw, ih)


def _text_box(panel):
    cw, ch = _canvas_of(panel)
    ov = overlay_defaults("text", canvas_w=cw, canvas_h=ch)
    return ov


def _host_ov(panel):
    ov = getattr(panel, "current_data", None)
    return ov if isinstance(ov, dict) else None


def _place_primary_element(ov, rect) -> None:
    """Picture element back on the fitted canvas rect. Overlay box stays the canvas."""
    if not isinstance(ov, dict):
        return
    els = ov.get("elements") or []
    if not els or not isinstance(els[0], dict):
        return
    el = els[0]
    if el.get("sticker_id"):
        return
    if el.get("kind") not in (None, "image"):
        return
    x, y, w, h = rect
    el["x"] = x
    el["y"] = y
    el["w"] = w
    el["h"] = h


def _assign_check(widget, on: bool) -> None:
    widget.blockSignals(True)
    try:
        widget.setChecked(bool(on))
    finally:
        widget.blockSignals(False)


def _assign_number(widget, value) -> None:
    widget.blockSignals(True)
    try:
        widget.setValue(value)
    finally:
        widget.blockSignals(False)


def _assign_spin_slider(spin, slider, value) -> None:
    spin.blockSignals(True)
    if slider is not None:
        slider.blockSignals(True)
    try:
        spin.setValue(value)
        if slider is not None:
            parent = spin.parent()
            scale = getattr(parent, "_scale", None) if parent is not None else None
            if scale:
                raw = int(round(float(spin.value()) * float(scale)))
            else:
                raw = int(round(float(spin.value())))
            slider.setValue(raw)
    finally:
        spin.blockSignals(False)
        if slider is not None:
            slider.blockSignals(False)


def _assign_combo(widget, value) -> None:
    widget.blockSignals(True)
    try:
        if isinstance(widget, (ValueCombo, SegmentedChoice)):
            widget.setCurrentText("" if value is None else str(value))
            return
        if isinstance(widget, QComboBox):
            idx = widget.findData(value)
            if idx < 0 and value is not None:
                idx = widget.findData(str(value))
            if idx >= 0:
                widget.setCurrentIndex(idx)
                return
            if value is not None:
                widget.setCurrentText(str(value))
            return
        if hasattr(widget, "setCurrentText"):
            widget.setCurrentText("" if value is None else str(value))
    finally:
        widget.blockSignals(False)


def _clear_keyframes(panel, *paths) -> None:
    from cutora.core.commands import get_bus
    from cutora.render.keyframe import disable_path, path_enabled

    host = panel._kf_host() if hasattr(panel, "_kf_host") else None
    if host is None:
        return
    for path in paths:
        if not path_enabled(host, path):
            continue
        params = panel._kf_cmd_params(host) if hasattr(panel, "_kf_cmd_params") else None
        done = False
        if params:
            payload = dict(params)
            payload.update({
                "path": str(path),
                "enabled": False,
                "t": float(panel._playhead_local(host)),
                "v": 0,
            })
            try:
                get_bus().execute("keyframe.enable", payload, source="ui")
                done = True
            except Exception:
                _LOG.exception("reset clear keyframes failed path=%s", path)
        if not done:
            disable_path(host, path)
    refresh = getattr(panel, "_refresh_kf_watches", None)
    if callable(refresh):
        refresh()


def _commit_ov(panel) -> None:
    panel.on_change_ov()
    sync = getattr(panel, "_sync_mirror_sliders", None)
    if callable(sync):
        sync()


def _ov_pos(panel) -> None:
    ov = _host_ov(panel)
    if ov is None:
        return
    if ov.get("type") == "image":
        rect, _wh = _image_rect(panel, ov)
        _place_primary_element(ov, rect)
        x, y = 0.0, 0.0
    elif getattr(panel, "current_type", None) == "sticker" and ov.get("kind") == "image":
        parent = getattr(panel, "_ov_parent_overlay", None) or ov
        (x, y, _w, _h), _wh = _image_rect(panel, parent)
    else:
        box = _text_box(panel)
        x, y = box["x"], box["y"]
    _assign_number(panel.ov_x, int(round(float(x))))
    _assign_number(panel.ov_y, int(round(float(y))))
    _commit_ov(panel)


def _ov_size(panel) -> None:
    ov = _host_ov(panel)
    if ov is None:
        return
    sticker = getattr(panel, "current_type", None) == "sticker" and ov.get("kind") == "image"
    if ov.get("type") == "image" or sticker:
        src = ov if ov.get("type") == "image" else (getattr(panel, "_ov_parent_overlay", None) or ov)
        rect, (iw, ih) = _image_rect(panel, src)
        ov["aspect_ratio"] = (iw / float(ih)) if ih else 1.0
        if ov.get("type") == "image":
            _place_primary_element(ov, rect)
            w, h = _canvas_of(panel)
        else:
            _x, _y, w, h = rect
    else:
        box = _text_box(panel)
        w, h = box["w"], box["h"]
    _assign_number(panel.ov_w, int(w))
    _assign_number(panel.ov_h, int(h))
    if hasattr(panel, "ov_keep_aspect"):
        _assign_check(panel.ov_keep_aspect, True)
    if hasattr(panel, "ov_lock_btn"):
        _assign_check(panel.ov_lock_btn, True)
        from cutora.ui.widgets.inspector import bind_icon_safe
        bind_icon_safe(panel.ov_lock_btn, "link", "accent")
    _commit_ov(panel)


def _ov_rotation(panel) -> None:
    _assign_number(panel.ov_rot, 0)
    _commit_ov(panel)


def _ov_tscale(panel) -> None:
    _assign_spin_slider(panel.ov_tscale, getattr(panel, "ov_tscale_sl", None), 1.0)
    _commit_ov(panel)


def _ov_opacity(panel) -> None:
    _assign_number(panel.ov_op, 255)
    _commit_ov(panel)


def _ov_transition(panel) -> None:
    _assign_combo(panel.ov_transition, "none")
    _commit_ov(panel)


def _ov_transition_dur(panel) -> None:
    _assign_number(panel.ov_transition_dur, float(DEFAULT_TRANSITION_DURATION))
    _commit_ov(panel)


def _ov_sticker_in(panel) -> None:
    _assign_combo(panel.ov_sticker_anim_in, "fade")
    _commit_ov(panel)


def _ov_sticker_out(panel) -> None:
    _assign_combo(panel.ov_sticker_anim_out, "fade")
    _commit_ov(panel)


def _ov_font(panel) -> None:
    from cutora.ui.panels.fonts import sanitize_font_family
    fam = sanitize_font_family(overlay_defaults("text")["font_family"])
    panel.ov_font.blockSignals(True)
    try:
        panel.ov_font.setCurrentText(fam)
    finally:
        panel.ov_font.blockSignals(False)
    _commit_ov(panel)


def _ov_fontsize(panel) -> None:
    _assign_number(panel.ov_size, int(overlay_defaults("text")["fontsize"]))
    _commit_ov(panel)


def _ov_align(panel) -> None:
    # Q124：新建文本默认居中 + 顶对齐（画面上的文字几乎都是居中的，
    # 「左对齐」是文档编辑器的习惯，不是视频的）
    _assign_combo(panel.ov_text_align, "center")
    if hasattr(panel, "ov_text_valign"):
        _assign_combo(panel.ov_text_valign, "top")
    _commit_ov(panel)


def _ov_writing(panel) -> None:
    _assign_combo(panel.ov_writing_mode, "horizontal")
    _commit_ov(panel)


def _ov_fit(panel) -> None:
    _assign_combo(panel.ov_fit_mode, "auto_height")
    _commit_ov(panel)


def _ov_glyph(panel) -> None:
    base = overlay_defaults("text")
    _assign_check(panel.ov_bold, bool(base["bold"]))
    _assign_check(panel.ov_italic, bool(base["italic"]))
    _commit_ov(panel)


def _ov_line_spacing(panel) -> None:
    _assign_number(panel.ov_line_spacing, 0)
    _commit_ov(panel)


def _ov_pad_x(panel) -> None:
    _assign_number(panel.ov_pad_l, 0)
    _assign_number(panel.ov_pad_r, 0)
    _commit_ov(panel)


def _ov_pad_y(panel) -> None:
    _assign_number(panel.ov_pad_t, 0)
    _assign_number(panel.ov_pad_b, 0)
    _commit_ov(panel)


def _ov_first_indent(panel) -> None:
    _assign_number(panel.ov_first_indent, 0)
    _commit_ov(panel)


def _ov_para(panel) -> None:
    _assign_number(panel.ov_para_spacing, 0)
    _commit_ov(panel)


def _ov_scale(panel) -> None:
    _assign_spin_slider(panel.ov_scale, getattr(panel, "ov_scale_sl", None), 1.0)
    _commit_ov(panel)


def _ov_flip(panel) -> None:
    _assign_check(panel.ov_flip_x, False)
    _assign_check(panel.ov_flip_y, False)
    _commit_ov(panel)


def _ov_blend(panel) -> None:
    _assign_combo(panel.ov_blend, "normal")
    panel._on_change_ov_blend("normal")


def _fade(panel, prefix: str, key: str) -> None:
    spin = getattr(panel, "%s_%s" % (prefix, key))
    slider = getattr(panel, "%s_%s_sl" % (prefix, key), None)
    _assign_spin_slider(spin, slider, 0)
    if prefix == "vid":
        panel.on_change_video()
    else:
        _commit_ov(panel)


def _fade_sync(panel, prefix: str) -> None:
    _assign_check(getattr(panel, "%s_fade_sync" % prefix), False)
    hide = getattr(panel, "_set_fade_master_visible", None)
    if callable(hide):
        hide(prefix, False)
    if prefix == "vid":
        panel.on_change_video()
    else:
        _commit_ov(panel)


def _fade_master(panel, prefix: str) -> None:
    master = getattr(panel, "%s_fade_master" % prefix, None)
    if master is not None:
        blocked = master.blockSignals(True)
        try:
            master.setValue(0)
        finally:
            master.blockSignals(blocked)
    sync = getattr(panel, "%s_fade_sync" % prefix, None)
    if sync is None or not sync.isChecked():
        return
    for key in ("fade_top", "fade_bottom", "fade_left", "fade_right"):
        spin = getattr(panel, "%s_%s" % (prefix, key))
        slider = getattr(panel, "%s_%s_sl" % (prefix, key), None)
        _assign_spin_slider(spin, slider, 0)
    if prefix == "vid":
        panel.on_change_video()
    else:
        _commit_ov(panel)


def _vid(panel) -> None:
    panel.on_change_video()


def _vid_fit(panel) -> None:
    _assign_combo(panel.vid_fit, clip_defaults()["fit_mode"])
    _vid(panel)


def _vid_bg_fill(panel, *keys) -> None:
    """Q117：背景填充的 ↺。不给 keys 就整段回默认。"""
    from cutora.model.project_schema import BG_FILL_DEFAULTS as DEFAULTS
    widgets = {
        "mode": panel.vid_bg_mode,
        "blur": panel.vid_bg_blur,
        "blur_style": panel.vid_bg_style,
        "highlights": panel.vid_bg_hl,
        "dim": panel.vid_bg_dim,
        "scale": panel.vid_bg_scale,
        "x": panel.vid_bg_x,
        "y": panel.vid_bg_y,
        "rotation": panel.vid_bg_rot,
    }
    for key in (keys or tuple(widgets)):
        w = widgets.get(key)
        if w is None:
            continue
        if key in ("mode", "blur_style"):
            _assign_combo(w, DEFAULTS[key])
        elif key == "highlights":
            _assign_spin_slider(w, panel.vid_bg_hl_sl, int(round(DEFAULTS["highlights"] * 100.0)))
        elif key == "dim":
            # 这两行是 SliderSpin（decimals=0）：整数，而且滑杆要一起归位
            _assign_spin_slider(w, panel.vid_bg_dim_sl, int(round(DEFAULTS["dim"] * 100.0)))
        elif key == "blur":
            _assign_spin_slider(w, panel.vid_bg_blur_sl, int(round(DEFAULTS["blur"])))
        else:
            _assign_number(w, float(DEFAULTS[key]))
    panel._on_vid_bg_fill()


def _vid_pos(panel) -> None:
    d = clip_defaults()
    _clear_keyframes(panel, "x", "y")
    _assign_number(panel.vid_x, d["x"])
    _assign_number(panel.vid_y, d["y"])
    _vid(panel)


def _vid_scale(panel) -> None:
    d = clip_defaults()
    _clear_keyframes(panel, "scale")
    _assign_number(panel.vid_scale, d["scale"])
    clip = getattr(panel, "_video_clip", None)
    if clip is not None and not isinstance(clip, dict):
        clip.scale_y = d["scale_y"]
        clip.lock_aspect = d["lock_aspect"]
    if hasattr(panel, "vid_lock_btn"):
        _assign_check(panel.vid_lock_btn, bool(d["lock_aspect"]))
        from cutora.ui.widgets.inspector import bind_icon_safe
        bind_icon_safe(
            panel.vid_lock_btn, "link" if d["lock_aspect"] else "unlink",
            "accent" if d["lock_aspect"] else "fg2")
    _vid(panel)


def _vid_rotation(panel) -> None:
    _clear_keyframes(panel, "rotation")
    _assign_number(panel.vid_rotation, clip_defaults()["rotation"])
    _vid(panel)


def _vid_opacity(panel) -> None:
    _clear_keyframes(panel, "opacity")
    _assign_spin_slider(panel.vid_opacity, getattr(panel, "vid_opacity_sl", None), 255)
    _vid(panel)


def _vid_transition(panel) -> None:
    _assign_combo(panel.vid_transition, clip_defaults()["transition_preset"])
    _vid(panel)


def _vid_transition_dur(panel) -> None:
    _assign_number(panel.vid_transition_dur, clip_defaults()["transition_duration"])
    _vid(panel)


def _vid_speed(panel) -> None:
    _assign_number(panel.vid_speed, clip_defaults()["speed"])
    _vid(panel)


def _vid_flip(panel) -> None:
    d = clip_defaults()
    _assign_check(panel.vid_flip_x, d["flip_x"])
    _assign_check(panel.vid_flip_y, d["flip_y"])
    _vid(panel)


def _vid_play(panel) -> None:
    d = clip_defaults()
    _assign_check(panel.vid_loop, d["loop"])
    _assign_check(panel.vid_match_dur, d["match_duration"])
    _vid(panel)


def _vid_alpha(panel) -> None:
    _assign_combo(panel.vid_alpha_mode, clip_defaults()["alpha_mode"])
    _vid(panel)


def _vid_crop_x(panel) -> None:
    _assign_number(panel.vid_crop_l, 0)
    _assign_number(panel.vid_crop_r, 0)
    _vid(panel)


def _vid_crop_y(panel) -> None:
    _assign_number(panel.vid_crop_t, 0)
    _assign_number(panel.vid_crop_b, 0)
    _vid(panel)


def _vid_blend(panel) -> None:
    _assign_combo(panel.vid_blend, clip_defaults()["blend_mode"])
    panel.on_change_video_blend(clip_defaults()["blend_mode"])


def _current_preset(panel) -> str:
    clip = getattr(panel, "_video_clip", None)
    preset = str(getattr(clip, "preset", "") or "")
    combo = getattr(panel, "trans_preset_cb", None)
    if combo is not None:
        data = combo.currentData()
        if data:
            preset = str(data)
    return preset or "cross_dissolve"


def _trans(panel) -> None:
    panel.on_change_transition()


def _trans_dur(panel) -> None:
    _assign_number(panel.trans_dur_sp, transition_defaults(_current_preset(panel))["duration"])
    _trans(panel)


def _trans_zoom(panel) -> None:
    _assign_number(
        panel.trans_zoom_start_sp,
        transition_defaults(_current_preset(panel))["zoom_scale_start"])
    _trans(panel)


def _trans_blur(panel) -> None:
    _assign_number(
        panel.trans_motion_blur_sp,
        transition_defaults(_current_preset(panel))["motion_blur"])
    _trans(panel)


def _trans_dir(panel) -> None:
    direction = transition_defaults(_current_preset(panel))["direction"]
    if isinstance(direction, (int, float)) and not isinstance(direction, bool):
        return
    _assign_combo(panel.trans_dir_cb, direction)
    _trans(panel)


def _trans_angle(panel) -> None:
    d = transition_defaults(_current_preset(panel))
    direction = d["direction"]
    angle = d["angle"]
    if isinstance(direction, (int, float)) and not isinstance(direction, bool):
        angle = float(direction)
    _assign_number(panel.trans_angle_sp, float(angle))
    sl = getattr(panel, "trans_angle_sl", None)
    if sl is not None:
        sl.blockSignals(True)
        sl.setValue(int(round(float(panel.trans_angle_sp.value()))))
        sl.blockSignals(False)
    _trans(panel)


def _trans_intensity(panel) -> None:
    _assign_number(
        panel.trans_intensity_sp,
        transition_defaults(_current_preset(panel))["intensity"])
    _trans(panel)


def _trans_feather(panel) -> None:
    panel.trans_feather.blockSignals(True)
    try:
        panel.trans_feather.setValue(
            float(transition_defaults(_current_preset(panel))["feather"]))
    finally:
        panel.trans_feather.blockSignals(False)
    _trans(panel)


def _trans_easing(panel) -> None:
    _assign_combo(panel.trans_easing_cb, transition_defaults(_current_preset(panel))["easing"])
    _trans(panel)


def _trk_props(panel) -> None:
    panel._commit_track_props()


def _trk_pos(panel) -> None:
    d = track_defaults()["props"]
    _assign_number(panel.trk_x, d["x"])
    _assign_number(panel.trk_y, d["y"])
    _trk_props(panel)


def _trk_scale(panel) -> None:
    _assign_number(panel.trk_scale, track_defaults()["props"]["scale"])
    _trk_props(panel)


def _trk_rotation(panel) -> None:
    _assign_number(panel.trk_rotation, track_defaults()["props"]["rotation"])
    _trk_props(panel)


def _trk_flip(panel) -> None:
    d = track_defaults()["props"]
    _assign_check(panel.trk_flip_x, d["flip_x"])
    _assign_check(panel.trk_flip_y, d["flip_y"])
    _trk_props(panel)


def _trk_opacity(panel) -> None:
    _assign_number(panel.trk_opacity, track_defaults()["props"]["opacity"])
    _trk_props(panel)


def _trk_blend(panel) -> None:
    _assign_combo(panel.trk_blend, track_defaults()["props"]["blend_mode"])
    _trk_props(panel)


def _split_on(panel) -> None:
    _assign_check(panel.trk_split_enable, False)
    panel._on_split_enable()


def _split_layout(panel) -> None:
    key = track_defaults()["split"]["layout"]
    group = panel.trk_split_layout_group
    group.blockSignals(True)
    try:
        btn = panel.trk_split_layout_btns.get(key)
        if btn is not None:
            btn.setChecked(True)
    finally:
        group.blockSignals(False)
    panel._exec_split("split.set_layout", {
        "layout": key,
        "enabled": bool(panel.trk_split_enable.isChecked()),
    })


def _split_cell(panel) -> None:
    panel.trk_split_cell.blockSignals(True)
    try:
        idx = panel.trk_split_cell.findData(None)
        panel.trk_split_cell.setCurrentIndex(idx if idx >= 0 else 0)
    finally:
        panel.trk_split_cell.blockSignals(False)
    panel._on_split_cell()


def _split_div(panel, attr: str, value) -> None:
    _assign_number(getattr(panel, attr), value)
    panel._commit_split_divider()


def _split_border_field(panel, widget, value, numeric: bool) -> None:
    if numeric:
        _assign_number(widget, value)
    else:
        _assign_combo(widget, value)
    panel._commit_split_border()


def _aud_vol(panel) -> None:
    _clear_keyframes(panel, "gain_db")
    _assign_spin_slider(panel.audio_vol, getattr(panel, "audio_vol_sl", None), 1.0)
    panel.on_change_audio()


def _aud_number(panel, spin_name, slider_name, value) -> None:
    _assign_spin_slider(getattr(panel, spin_name), getattr(panel, slider_name, None), value)
    panel.on_change_audio()


def _aud_spatial(panel) -> None:
    box = panel.audio_spatial_enable
    # on_change_audio only writes spatial when the checkbox is the sender.
    if box.isChecked():
        box.setChecked(False)
        return
    box.stateChanged.emit(0)


_APPLIERS = {
    "ov.pos": _ov_pos,
    "ov.size": _ov_size,
    "ov.rotation": _ov_rotation,
    "ov.tscale": _ov_tscale,
    "ov.opacity": _ov_opacity,
    "ov.transition": _ov_transition,
    "ov.transition_dur": _ov_transition_dur,
    "ov.sticker_in": _ov_sticker_in,
    "ov.sticker_out": _ov_sticker_out,
    "ov.font": _ov_font,
    "ov.fontsize": _ov_fontsize,
    "ov.align": _ov_align,
    "ov.writing": _ov_writing,
    "ov.fit": _ov_fit,
    "ov.glyph": _ov_glyph,
    "ov.line_spacing": _ov_line_spacing,
    "ov.pad_x": _ov_pad_x,
    "ov.pad_y": _ov_pad_y,
    "ov.first_indent": _ov_first_indent,
    "ov.para_spacing": _ov_para,
    "ov.scale": _ov_scale,
    "ov.flip": _ov_flip,
    "ov.blend": _ov_blend,
    "ov.fade_sync": lambda p: _fade_sync(p, "ov"),
    "ov.fade_master": lambda p: _fade_master(p, "ov"),
    "ov.fade_top": lambda p: _fade(p, "ov", "fade_top"),
    "ov.fade_bottom": lambda p: _fade(p, "ov", "fade_bottom"),
    "ov.fade_left": lambda p: _fade(p, "ov", "fade_left"),
    "ov.fade_right": lambda p: _fade(p, "ov", "fade_right"),
    "ov.fade_top_offset": lambda p: _fade(p, "ov", "fade_top_offset"),
    "ov.fade_bottom_offset": lambda p: _fade(p, "ov", "fade_bottom_offset"),
    "ov.fade_left_offset": lambda p: _fade(p, "ov", "fade_left_offset"),
    "ov.fade_right_offset": lambda p: _fade(p, "ov", "fade_right_offset"),
    "vid.fit": _vid_fit,
    "vid.bg_mode": lambda p: _vid_bg_fill(p, "mode"),
    "vid.bg_blur": lambda p: _vid_bg_fill(p, "blur"),
    "vid.bg_style": lambda p: _vid_bg_fill(p, "blur_style"),
    "vid.bg_hl": lambda p: _vid_bg_fill(p, "highlights"),
    "vid.bg_dim": lambda p: _vid_bg_fill(p, "dim"),
    "vid.bg_scale": lambda p: _vid_bg_fill(p, "scale"),
    "vid.bg_pos": lambda p: _vid_bg_fill(p, "x", "y"),
    "vid.bg_rot": lambda p: _vid_bg_fill(p, "rotation"),
    "vid.pos": _vid_pos,
    "vid.scale": _vid_scale,
    "vid.rotation": _vid_rotation,
    "vid.opacity": _vid_opacity,
    "vid.transition": _vid_transition,
    "vid.transition_dur": _vid_transition_dur,
    "vid.speed": _vid_speed,
    "vid.flip": _vid_flip,
    "vid.play": _vid_play,
    "vid.alpha": _vid_alpha,
    "vid.crop_x": _vid_crop_x,
    "vid.crop_y": _vid_crop_y,
    "vid.blend": _vid_blend,
    "vid.fade_sync": lambda p: _fade_sync(p, "vid"),
    "vid.fade_master": lambda p: _fade_master(p, "vid"),
    "vid.fade_top": lambda p: _fade(p, "vid", "fade_top"),
    "vid.fade_bottom": lambda p: _fade(p, "vid", "fade_bottom"),
    "vid.fade_left": lambda p: _fade(p, "vid", "fade_left"),
    "vid.fade_right": lambda p: _fade(p, "vid", "fade_right"),
    "vid.fade_top_offset": lambda p: _fade(p, "vid", "fade_top_offset"),
    "vid.fade_bottom_offset": lambda p: _fade(p, "vid", "fade_bottom_offset"),
    "vid.fade_left_offset": lambda p: _fade(p, "vid", "fade_left_offset"),
    "vid.fade_right_offset": lambda p: _fade(p, "vid", "fade_right_offset"),
    "trans.dur": _trans_dur,
    "trans.zoom": _trans_zoom,
    "trans.blur": _trans_blur,
    "trans.dir": _trans_dir,
    "trans.angle": _trans_angle,
    "trans.intensity": _trans_intensity,
    "trans.feather": _trans_feather,
    "trans.easing": _trans_easing,
    "trk.pos": _trk_pos,
    "trk.scale": _trk_scale,
    "trk.rotation": _trk_rotation,
    "trk.flip": _trk_flip,
    "trk.opacity": _trk_opacity,
    "trk.blend": _trk_blend,
    "trk.split_on": _split_on,
    "trk.split_layout": _split_layout,
    "trk.split_cell": _split_cell,
    "trk.split_pos": lambda p: _split_div(p, "trk_split_pos", 0.5),
    "trk.split_rot": lambda p: _split_div(p, "trk_split_rot", 0.0),
    "trk.split_feather": lambda p: _split_div(p, "trk_split_feather", 24.0),
    "trk.split_border_style": lambda p: _split_border_field(p, p.trk_split_border_style, "none", False),
    "trk.split_border_width": lambda p: _split_border_field(
        p, p.trk_split_border_width,
        track_defaults()["split"]["border"]["width_px"], True),
    "trk.split_border_curve": lambda p: _split_border_field(p, p.trk_split_border_curve, "straight", False),
    "trk.split_backdrop": lambda p: (_assign_combo(p.trk_split_backdrop, "each"), p._commit_split_backdrop()),
    "trk.split_border_shadow": lambda p: _split_border_field(p, p.trk_split_border_shadow, 12, True),
    "aud.vol": _aud_vol,
    "aud.pitch": lambda p: _aud_number(p, "audio_pitch", "audio_pitch_sl", 0.0),
    "aud.fade_in": lambda p: _aud_number(p, "audio_fade_in_dur", "audio_fade_in_dur_sl", 0.0),
    "aud.fade_out": lambda p: _aud_number(p, "audio_fade_out_dur", "audio_fade_out_dur_sl", 0.0),
    "aud.spatial": _aud_spatial,
    "aud.wet": lambda p: _aud_number(p, "audio_spatial_amount", "audio_spatial_amount_sl", 0.0),
}
