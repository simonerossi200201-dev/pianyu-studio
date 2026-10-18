# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q161 · 历史记录里每一步写清楚干了什么（「移动片段 → 12.30 s」而不是光秃秃的「移动片段」）。

纯函数：命令名 + 参数 → 一行中文。拿不准就退回命令的中文标题。"""
from __future__ import annotations


def _tc(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    m, s = divmod(max(0.0, f), 60.0)
    return "%d:%05.2f" % (int(m), s) if m >= 1 else "%.2f s" % s


def _short(text, n=12) -> str:
    t = str(text or "").replace("\n", " ").strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def _num(v, nd=2):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return ("%%.%df" % nd % f).rstrip("0").rstrip(".") if nd else "%d" % round(f)


# 身份参数：同一个命令、同一批身份参数、短时间内连续发生 → 历史里合成一步（滑杆 / 拖动 / 连按方向键）
IDENTITY_KEYS = ("media_id", "track_id", "clip_index", "clip_indices", "seg_id", "seg_ids", "idx", "index",
                 "kind", "list", "path", "key", "divider_id", "marker_id", "name", "overlay_id", "target",
                 "targets", "group_id", "node_id", "host")
MERGE_SEC = 1.2
# 这些命令连着来一般是一个连续手势（拖滑杆 / 微移 / 拖关键帧），合成一步
MERGEABLE = frozenset({
    "track.set_props", "keyframe.set", "keyframe.move", "split.set_divider", "grade.set",
    "overlay.set_fade", "subtitle.trim_word", "subtitle.move_segments", "subtitle.retime_segments",
    "timeline.move_clip", "timeline.move_clips", "timeline.trim_clip", "timeline.trim_clips",
    "timeline.set_clip_fades",
    "marker.move", "fx.mask_update",
    "fx.node", "group.set", "audio.volume_master", "timeline.set_transition",
})


def identity_of(command, params) -> tuple:
    params = params if isinstance(params, dict) else {}
    return (str(command or ""),) + tuple(
        (k, repr(params.get(k))) for k in IDENTITY_KEYS if k in params)


def describe_command(command, params, fallback="") -> str:
    """一行中文。例：移动片段 → 12.30 s；改字幕文本「你好…」；关键帧 x @ 1.20 s。"""
    name = str(command or "")
    p = params if isinstance(params, dict) else {}
    base = str(fallback or name or "操作")
    try:
        if name == "timeline.move_clip" or name == "timeline.move_clips":
            if "start_t" in p:
                return "移动片段 → %s" % _tc(p.get("start_t"))
            if "delta" in p or "dt" in p:
                d = float(p.get("delta", p.get("dt")) or 0.0)
                return "移动片段 %s%s" % ("+" if d >= 0 else "−", _tc(abs(d)))
            return "移动片段"
        if name == "timeline.trim_clip" or name == "timeline.trim_clips":
            side = {"left": "开头", "right": "结尾", "start": "开头", "end": "结尾"}.get(str(p.get("side") or p.get("edge") or ""), "")
            t = p.get("t", p.get("time", p.get("new_t")))
            return "裁剪片段%s%s" % (side, (" → %s" % _tc(t)) if t is not None else "")
        if name == "timeline.unlink_clips":
            return "解绑音视频"
        if name == "timeline.link_clips":
            return "重新绑定音视频"
        if name == "timeline.set_clip_fades":
            return "设置淡化"
        if name == "timeline.split_at" or name == "timeline.razor":
            t = p.get("t", p.get("time"))
            return "切开片段%s" % ((" @ %s" % _tc(t)) if t is not None else "")
        if name == "timeline.delete_clip":
            return "删除片段"
        if name == "timeline.ripple_delete":
            n = len(p.get("targets") or []) or 1
            return "波纹删除 %d 项" % n
        if name == "timeline.ripple_insert":
            return "波纹插入空隙 %s" % _tc(p.get("duration", p.get("dur", 0)))
        if name == "timeline.close_gaps":
            return "闭合间隙"
        if name == "timeline.add_track":
            return "添加%s轨道" % {"video": "视频", "audio": "音频", "text": "文字", "image": "图片", "subs": "字幕"}.get(str(p.get("type") or p.get("track_type") or ""), "")
        if name == "timeline.remove_track":
            return "删除轨道"
        if name == "timeline.move_clip_to_track":
            return "片段换轨"
        if name == "timeline.duplicate_clips":
            return "复制片段"
        if name in ("timeline.set_track_mute", "timeline.set_track_locked"):
            on = bool(p.get("mute", p.get("locked", p.get("on", True))))
            return ("%s轨道" % ("静音" if name.endswith("mute") else "锁定")) if on else ("取消%s轨道" % ("静音" if name.endswith("mute") else "锁定"))
        if name == "timeline.track.solo":
            return "独奏轨道" if bool(p.get("on", True)) else "取消独奏轨道"
        if name == "timeline.set_track_height":
            return "轨道高度 %s" % _num(p.get("height"), 0)
        if name == "timeline.add_transition":
            return "添加转场 %s" % (p.get("preset") or "")
        if name == "timeline.set_transition":
            return "转场时长 %s" % _tc(p.get("duration", 0))
        if name == "timeline.delete_transition":
            return "删除转场"
        if name == "timeline.join_clips":
            return "合并接缝两侧"
        if name == "subtitle.set_text":
            return "改字幕「%s」" % _short(p.get("text"))
        if name == "subtitle.delete":
            return "删除一条字幕"
        if name == "subtitle.delete_segments":
            return "删除 %d 条字幕" % len(p.get("seg_ids") or [])
        if name == "subtitle.merge":
            return "合并字幕"
        if name == "subtitle.split":
            return "拆分字幕"
        if name == "subtitle.move_segments":
            d = p.get("delta", p.get("dt"))
            return "移动字幕%s" % ((" %s%s" % ("+" if float(d) >= 0 else "−", _tc(abs(float(d))))) if d is not None else "")
        if name == "subtitle.retime_segments":
            return "字幕变速"
        if name == "subtitle.trim_segment":
            return "裁剪字幕段 → %s" % _tc(p.get("t", p.get("time", 0)))
        if name == "subtitle.trim_word":
            return "微调词边界"
        if name == "subtitle.duplicate_segments":
            return "复制字幕段"
        if name == "subtitle.replace":
            return "替换「%s」→「%s」" % (_short(p.get("query"), 8), _short(p.get("replacement"), 8))
        if name == "subtitle.apply_preset" or name == "style.apply_subtitle_preset":
            return "套用字幕预设「%s」" % _short(p.get("name"), 10)
        if name == "style.apply_text_preset":
            return "套用文本预设「%s」" % _short(p.get("name"), 10)
        if name == "subtitle.lock":
            return "锁定字幕" if p.get("locked", p.get("on", True)) else "解锁字幕"
        if name == "keyframe.set":
            return "关键帧 %s @ %s" % (p.get("path") or p.get("key") or "", _tc(p.get("t", 0)))
        if name == "keyframe.delete":
            return "删除关键帧 %s" % (p.get("path") or "")
        if name == "keyframe.move":
            return "移动关键帧 %s → %s" % (p.get("path") or "", _tc(p.get("t_to", p.get("t", 0))))
        if name == "keyframe.enable":
            return "%s关键帧 %s" % ("启用" if p.get("on", True) else "关闭", p.get("path") or "")
        if name == "keyframe.set_interp":
            return "关键帧缓动 %s" % (p.get("mode") or p.get("interp") or "")
        if name == "keyframe.paste":
            return "粘贴关键帧"
        if name == "track.set_props":
            patch = p.get("patch") if isinstance(p.get("patch"), dict) else {}
            keys = [k for k in ("x", "y", "scale", "rotation", "opacity", "blend_mode", "name") if k in patch]
            if keys:
                return "轨道 %s" % "、".join("%s=%s" % (k, _num(patch[k]) if k not in ("blend_mode", "name") else patch[k]) for k in keys[:3])
            return "设置轨道属性"
        if name == "track.set_cell":
            return "轨道进 %s 格" % (p.get("cell") or "—")
        if name == "split.set_layout":
            if p.get("layout"):
                from cutora.render.split_layout import layout_label_for
                return "分屏布局：%s" % layout_label_for(str(p.get("layout")))
            if p.get("enabled") is False:
                return "关闭分屏"
            if isinstance(p.get("backdrop"), dict):
                return "分屏背景：%s" % {"each": "每格各自", "full": "单个视频撑满"}.get(str(p["backdrop"].get("mode") or ""), "调整")
            if isinstance(p.get("border"), dict):
                return "分界线样式"
            return "分屏设置"
        if name == "split.set_divider":
            if p.get("aspect") is not None:
                return "调椭圆宽高比 %s" % _num(p.get("aspect"), 2)
            if p.get("r") is not None:
                return "调圆圈大小 %s" % _num(p.get("r"), 2)
            if p.get("rotation_deg") is not None and p.get("pos") is None:
                return "调外框旋转 %s°" % _num(p.get("rotation_deg"), 1)
            return "调分界线 %s" % _num(p.get("pos"), 2) if p.get("pos") is not None else "调分界线"
        if name == "grade.set":
            return "调色"
        if name == "overlay.set_fade":
            return "叠层四边渐隐"
        if name == "overlay.split":
            return "切开%s @ %s" % ({"text": "文字", "image": "图片"}.get(str(p.get("kind") or ""), "覆层"), _tc(p.get("t", 0)))
        if name == "overlay.insert_sticker":
            return "插入贴纸"
        if name == "marker.add":
            return "添加标记 @ %s" % _tc(p.get("t", 0))
        if name == "marker.remove":
            return "删除标记"
        if name == "marker.rename":
            return "标记改名「%s」" % _short(p.get("name") or p.get("label"), 8)
        if name == "marker.move":
            return "移动标记 → %s" % _tc(p.get("t", 0))
        if name == "marker.set_color":
            return "标记颜色"
        if name == "group.create":
            return "预合成%s" % (("「%s」" % _short(p.get("name"), 8)) if p.get("name") else "")
        if name == "group.ungroup":
            return "解除预合成"
        if name == "group.join":
            return "加入预合成组"
        if name == "group.detach":
            return "从预合成组分离"
        if name == "group.set":
            return "预合成组设置"
        if name.startswith("fx.mask"):
            return {"fx.mask_add": "添加遮罩", "fx.mask_update": "修改遮罩", "fx.mask_delete": "删除遮罩",
                    "fx.mask_reorder": "调整遮罩顺序"}.get(name, base)
        if name == "fx.keyer_set":
            return "蓝绿幕抠像"
        if name == "fx.node":
            return "效果节点：%s" % (p.get("op") or p.get("action") or "改")
        if name == "fx.link":
            return "效果节点连线"
        if name == "media.remove":
            return "移除媒体"
        if name == "media.reorder":
            return "重排媒体"
        if name == "audio.volume_master":
            parts = ["%s %s" % (k, _num(p[k])) for k in ("src", "bgm", "sfx") if p.get(k) is not None]
            return "音量总控 %s" % " ".join(parts) if parts else "音量总控"
    except Exception:
        return base
    return base
