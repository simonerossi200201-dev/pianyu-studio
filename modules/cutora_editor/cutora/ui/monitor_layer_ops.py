# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q173 · 中央监视器右键「图层」：对点中的那一层（以及选中的其他视频里「同一批」的那一层）批量做
左右 / 上下翻转、混合模式、调色、叠放顺序（上移 / 下移 / 置顶 / 置底当背景）、显示隐藏。

「同一批」= ``mm.layer_slot``：主视频 ↔ 主视频、第 N 条外部视频 ↔ 第 N 条外部视频（分屏时 = 同一格）。
一批一次改完、一步撤销；改完只作废画面，看得见的格子由调度器一格一格补。
"""
from __future__ import annotations

import copy

from cutora.batch import monitor_model as mm
from cutora.core.log import get_logger
from cutora.ui.widgets.inspector import LUMA_MATTE_TIP, VIDEO_BLEND_ITEMS

_LOG = get_logger("cutora.ui.monitor_layer_ops")

# 和剪辑页视频属性的混合模式是同一份。改一边，另一边读到的是同一个 clip.blend_mode。
BLEND_ITEMS = VIDEO_BLEND_ITEMS

# 调色快捷：每点一下在现有调色上加减（和右侧调色面板同一套数值）
GRADE_STEPS = (
    ("亮一点", {"exposure": 0.15}), ("暗一点", {"exposure": -0.15}),
    ("对比 +", {"contrast": 0.1}), ("对比 −", {"contrast": -0.1}),
    ("饱和 +", {"saturation": 0.15}), ("饱和 −", {"saturation": -0.15}),
    ("暖一点", {"temperature": 300.0}), ("冷一点", {"temperature": -300.0}),
)
GRADE_LIMITS = {"exposure": (-4.0, 4.0), "contrast": (-1.0, 3.0), "saturation": (-1.0, 4.0),
                "temperature": (-5500.0, 8000.0)}


# ───────────────────────────── 数据（没有 Qt） ─────────────────────────────

def _media_clips(track):
    return [c for c in getattr(track, "clips", None) or []
            if getattr(c, "type", "media") == "media"
            and not getattr(c, "is_thumbnail_leadin", False) and not getattr(c, "is_intro_video_leadin", False)]


def flip(track, axis: str = "x") -> int:
    key = "flip_x" if axis == "x" else "flip_y"
    n = 0
    for c in _media_clips(track):
        setattr(c, key, not bool(getattr(c, key, False)))
        n += 1
    return n


def set_blend(track, mode: str) -> int:
    """写到片段、轨道和轨道合成。剪辑页混合模式下拉读的是轨道上的值，三处必须一样。"""
    mode = str(mode or "normal")
    n = 0
    for c in _media_clips(track):
        if str(getattr(c, "blend_mode", "normal") or "normal") != mode:
            c.blend_mode = mode
            n += 1
        else:
            c.blend_mode = mode
    if str(getattr(track, "blend_mode", "normal") or "normal") != mode:
        n = max(n, 1)
    track.blend_mode = mode
    props = getattr(track, "props", None)
    if isinstance(props, dict):
        if str(props.get("blend_mode") or "normal") != mode:
            n = max(n, 1)
        props["blend_mode"] = mode
    return n


def grade_step(track, patch: dict) -> int:
    from cutora.render.grade import normalize_grade
    n = 0
    for c in _media_clips(track):
        g = normalize_grade(getattr(c, "grade", None))
        for k, d in patch.items():
            lo, hi = GRADE_LIMITS.get(k, (-1e9, 1e9))
            g[k] = round(max(lo, min(hi, float(g.get(k, 0.0)) + float(d))), 4)
        c.grade = g
        n += 1
    return n


def grade_set(track, grade) -> int:
    from cutora.render.grade import identity_grade, normalize_grade
    n = 0
    for c in _media_clips(track):
        c.grade = normalize_grade(copy.deepcopy(grade)) if grade is not None else identity_grade()
        n += 1
    return n


def grade_bw(track) -> int:
    return grade_set_field(track, "saturation", -1.0)


def grade_set_field(track, key, value) -> int:
    from cutora.render.grade import normalize_grade
    n = 0
    for c in _media_clips(track):
        g = normalize_grade(getattr(c, "grade", None))
        g[key] = value
        c.grade = g
        n += 1
    return n


def first_grade(track):
    from cutora.render.grade import identity_grade, normalize_grade
    for c in _media_clips(track):
        g = normalize_grade(getattr(c, "grade", None))
        if g != identity_grade():
            return g
    return None


def _visual_tracks(task):
    """参与叠放的视频轨（不含整幅背景轨），按 tl.tracks 的顺序（下 → 上）。"""
    return mm.video_tracks(task)


def restack(task, track, how: str) -> bool:
    """how: up / down / top / bottom。Q177：在全部画面轨（视频 / 文字 / 图片 / 调整 / 字幕）之间挪 ——
    和时间线、图层面板是同一套叠压顺序；整幅背景轨永远垫最底，不动。"""
    from cutora.batch import layer_model as lm
    return lm.move_track(task, track, how)


# ───────────────────────────── 监视器 ─────────────────────────────

class LayerOpsMixin:
    """MonitorGrid 用。"""

    def _layer_tracks(self, targets, tile, track, cid=None):
        """[(tile, track)]：点中的这一层 + 其他目标视频里同一批 / 同一格的那一层。"""
        slot = None if cid else mm.layer_slot(tile.task, track)
        out = []
        for t in targets:
            if t is tile:
                tr = track
            elif cid:
                tr = mm.cell_track(t.task, cid)
            else:
                tr = mm.track_for_slot(t.task, slot) if slot else None
            if tr is not None and not getattr(tr, "locked", False):
                out.append((t, tr))
        return out

    def _layer_title(self, tile, track, cid):
        name = mm.track_display_name(tile.task, track)
        if cid:
            return "格子 %s · %s" % (cid, name)
        slot = mm.layer_slot(tile.task, track)
        if slot and slot[0] == "main":
            return "媒体库视频 · %s" % name
        return "外部视频 · %s" % name

    def add_layer_ops_menu(self, menu, tile, track, cid, targets):
        """右键里的「图层」子菜单。track = 点中的那一层。"""
        if track is None:
            return None
        pairs = self._layer_tracks(targets, tile, track, cid)
        n = len(pairs)
        sub = menu.addMenu("图层：%s%s" % (self._layer_title(tile, track, cid),
                                           "（同一批 %d 条）" % n if n > 1 else ""))
        sub.setToolTipsVisible(True)
        sub.addAction("左右翻转", lambda: self.layer_apply(pairs, lambda tr: flip(tr, "x"), "左右翻转"))
        sub.addAction("上下翻转", lambda: self.layer_apply(pairs, lambda tr: flip(tr, "y"), "上下翻转"))
        sub.addSeparator()
        bm = sub.addMenu("混合模式")
        cur = str(getattr((_media_clips(track) or [None])[0], "blend_mode", "normal") or "normal")
        for key, label in BLEND_ITEMS:
            a = bm.addAction(label, lambda k=key, lb=label: self.layer_apply(
                pairs, lambda tr: set_blend(tr, k), "混合模式：%s" % lb))
            a.setCheckable(True)
            a.setChecked(cur == key)
            if key in ("luma_matte", "luma_matte_inv"):
                a.setToolTip(LUMA_MATTE_TIP)
        gm = sub.addMenu("调色")
        gm.setToolTipsVisible(True)
        for label, patch in GRADE_STEPS:
            gm.addAction(label, lambda p=patch, lb=label: self.layer_apply(
                pairs, lambda tr: grade_step(tr, p), "调色：%s" % lb))
        gm.addAction("黑白", lambda: self.layer_apply(pairs, lambda tr: grade_set_field(tr, "saturation", -1.0),
                                                     "调色：黑白"))
        gm.addSeparator()
        cp = gm.addAction("复制这一层的调色", lambda: self.layer_copy_grade(tile, track))
        cp.setToolTip("连曲线 / HSL / LUT 一起复制（右侧调色面板里调好的那一套）")
        clip = self.__dict__.get("_q173_grade")
        pa = gm.addAction("粘贴调色到同一批（%d 条）" % n,
                          lambda: self.layer_apply(pairs, lambda tr: grade_set(tr, self.__dict__.get("_q173_grade")),
                                                   "粘贴调色"))
        pa.setEnabled(clip is not None)
        gm.addAction("清除调色", lambda: self.layer_apply(pairs, lambda tr: grade_set(tr, None), "清除调色"))
        om = sub.addMenu("叠放顺序（时间线轨道）")
        for how, label in (("up", "上移一层"), ("down", "下移一层"), ("top", "置顶"),
                           ("bottom", "置底（垫在最下面当背景）")):
            om.addAction(label, lambda h=how, lb=label: self.layer_restack(pairs, h, lb))
        # Q177：这一层的属性（不透明度 / 缩放 / 位置 / 旋转），同一批一起改
        add_props = getattr(self, "add_props_widget", None)
        if callable(add_props) and pairs:
            from cutora.ui.monitor_layer_props import LayerRef
            pm = sub.addMenu("属性（同一批 %d 条）" % n if n > 1 else "属性")
            add_props(pm, LayerRef(tile, "video", None, track), [(t, None, tr) for t, tr in pairs])
        sub.addSeparator()
        clips0 = _media_clips(track)
        child = bool(clips0 and str(getattr(clips0[0], "matte_for", "") or ""))
        inv = bool(clips0 and getattr(clips0[0], "matte_invert", False))
        a_on = sub.addAction(
            "作为上一条视频的蒙版",
            lambda: self._apply_child_matte(pairs, "on"))
        a_on.setCheckable(True)
        a_on.setChecked(child)
        a_on.setToolTip(
            "黑白视频只挖紧挨在上面的那一条视频。黑色透明，白色保留。"
            "不挖已经画好的整幅画面。透明边不挖。")
        a_off = sub.addAction(
            "取消蒙版关系",
            lambda: self._apply_child_matte(pairs, "off"))
        a_off.setEnabled(child)
        a_inv = sub.addAction(
            "蒙版反相",
            lambda: self._apply_child_matte(pairs, "invert"))
        a_inv.setCheckable(True)
        a_inv.setChecked(child and inv)
        a_inv.setToolTip("白色变透明，黑色保留。和「作为蒙版」那个整幅挖空不是同一个开关。")
        sub.addSeparator()
        hidden = all(not getattr(tr, "visible", True) for _t, tr in pairs)
        sub.addAction("显示这一层" if hidden else "隐藏这一层",
                      lambda: self.layer_apply(pairs, lambda tr: self._set_visible(tr, hidden),
                                               "显示图层" if hidden else "隐藏图层"))
        return sub

    def _apply_child_matte(self, pairs, mode):
        """上一条 = 这一条视频自己叠放顺序里更高的那条。每条视频各自重算，不共用一个轨道 id。"""
        from cutora.render.child_matte import set_track_matte, video_above
        label = {"on": "作为上一条视频的蒙版", "off": "取消蒙版关系",
                 "invert": "蒙版反相"}.get(mode, "蒙版")

        def _fn(tr, _pairs=pairs, _mode=mode):
            for t, x in _pairs:
                if x is not tr:
                    continue
                if _mode == "off":
                    return set_track_matte(tr, "", False)
                parent = video_above(t.task, tr)
                if parent is None:
                    return 0
                pid = str(getattr(parent, "id", "") or "")
                clips = _media_clips(tr)
                linked = bool(clips and str(getattr(clips[0], "matte_for", "") or ""))
                cur_inv = bool(clips and getattr(clips[0], "matte_invert", False))
                if _mode == "invert":
                    cur_inv = (not cur_inv) if linked else True
                elif not linked:
                    cur_inv = False
                return set_track_matte(tr, pid, cur_inv)
            return 0

        return self.layer_apply(pairs, _fn, label)

    @staticmethod
    def _set_visible(track, on):
        track.visible = bool(on)
        return 1

    def layer_apply(self, pairs, fn, label):
        pairs = [(t, tr) for t, tr in (pairs or []) if t is not None and tr is not None]
        if not pairs:
            self.status.emit("没有可以改的图层（锁住了？）")
            return 0
        tiles = []
        for t, _tr in pairs:
            if t not in tiles:
                tiles.append(t)
        tok = self.history.begin([t.task for t in tiles], snap=mm.snapshot_layers)
        done = 0
        for t, tr in pairs:
            try:
                done += int(bool(fn(tr)))
            except Exception as exc:
                _LOG.exception("%s 失败 #%d", label, t.idx + 1)
                self.status.emit("%s：#%02d 失败（%s）" % (label, t.idx + 1, exc))
        if not done:
            self.status.emit("%s：没有需要改的" % label)
            return 0
        self._changed(tiles, video=True, overlays=False, visible_only=True)
        self._commit(tok, label, tiles, overlays=False)
        self.status.emit("%s：%d 条视频" % (label, len(tiles)))
        return done

    def layer_restack(self, pairs, how, label):
        moved = [0]

        def _fn(tr, _pairs=pairs):
            for t, x in _pairs:
                if x is tr:
                    ok = restack(t.task, tr, how)
                    moved[0] += int(ok)
                    return ok
            return False

        return self.layer_apply(pairs, _fn, label)

    def layer_copy_grade(self, tile, track):
        g = first_grade(track)
        if g is None:
            self.status.emit("这一层还没有调色")
            return False
        self.__dict__["_q173_grade"] = g
        self.status.emit("已复制这一层的调色 —— 右键「粘贴调色到同一批」")
        return True
