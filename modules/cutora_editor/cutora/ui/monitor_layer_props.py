# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q177 · 中央监视器右键「当前图层」：选中哪个图层，就显示哪个图层自己的属性（滑杆 / 开关 / 下拉），
连同选中的其他视频里「同一批」的那一层一起改；以及这一层所在轨道的叠放顺序（上移 / 下移 / 置顶 / 置底）。

「当前图层」怎么定：右键点在画面里的文字 / 图片上 → 它；否则右侧图层面板里选中了一行 → 那一行
（调整图层选「整个画面」时画面上点不中，就在面板里选）；否则点中的那一格视频 → 那条视频轨。

「同一批」：文字 / 图片 / 调整图层 = monitor_model.sibling_overlay（同 id → 同批次 → 同类第 N 条）；
视频 = layer_slot（主视频对主视频、第 N 条外部视频对第 N 条外部视频）。

改值是实时的（60 ms 合一次，只作废画面、看得见的格子由调度器补）；菜单关上时记成一步撤销。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QCheckBox, QFormLayout, QLabel, QWidget, QWidgetAction

from cutora.batch import layer_model as lm
from cutora.batch import monitor_model as mm
from cutora.core.log import get_logger

_LOG = get_logger("cutora.ui.monitor_layer_props")

KIND_WORD = {"video": "视频", "text": "文字", "image": "图片", "adjust": "调整图层"}
RESTACK = (("up", "上移一层"), ("down", "下移一层"), ("top", "置顶（盖在最上面）"), ("bottom", "置底（垫在最下面）"))


class LayerRef:
    __slots__ = ("tile", "kind", "obj", "track")

    def __init__(self, tile, kind, obj, track):
        self.tile, self.kind, self.obj, self.track = tile, kind, obj, track


# ───────────────────────────── 属性表（没有 Qt） ─────────────────────────────
# (键, 名字, 控件, 下限, 上限, 步长, 小数位)；控件：slider / check / combo:<选项>

def _media_clips(track):
    return [c for c in getattr(track, "clips", None) or []
            if getattr(c, "type", "media") == "media"
            and not getattr(c, "is_thumbnail_leadin", False) and not getattr(c, "is_intro_video_leadin", False)]


def prop_specs(kind: str) -> list:
    from cutora.render import adjust_layer as AL
    if kind == "video":
        return [("opacity", "不透明度 %", "slider", 0, 100, 1, 0), ("scale", "缩放 %", "slider", 5, 500, 1, 0),
                ("x", "位置 X", "slider", -3000, 3000, 1, 0), ("y", "位置 Y", "slider", -3000, 3000, 1, 0),
                ("rotation", "旋转 °", "slider", -360, 360, 0.5, 1)]
    if kind == "text":
        return [("opacity", "不透明度 %", "slider", 0, 100, 1, 0), ("scale", "缩放 %", "slider", 10, 500, 1, 0),
                ("rotation", "旋转 °", "slider", -360, 360, 0.5, 1),
                ("x", "位置 X", "slider", -3000, 6000, 1, 0), ("y", "位置 Y", "slider", -3000, 6000, 1, 0)]
    if kind == "image":
        blend = "combo:" + ";".join("%s=%s" % pair for pair in (
            ("normal", "正常"), ("multiply", "正片叠底"), ("screen", "滤色"),
            ("overlay", "叠加"), ("additive", "相加")))
        return [("opacity", "不透明度 %", "slider", 0, 100, 1, 0),
                ("scale", "缩放 %", "slider", 5, 500, 1, 0),
                ("x", "位置 X", "slider", -3000, 3000, 1, 0),
                ("y", "位置 Y", "slider", -3000, 3000, 1, 0),
                ("rotation", "旋转 °", "slider", -360, 360, 0.5, 1),
                ("blend_mode", "混合", blend, 0, 0, 0, 0)]
    if kind == "adjust":
        ranges = ";".join("%s=%s" % (k, v) for k, v in AL.RANGES)
        # Q179：效果多选 —— 每种一个勾（effect.use_<种类>），各自的强度
        uses = [("effect.use_" + k, lb, "check", 0, 1, 1, 0) for k, lb in AL.EFFECT_KINDS]
        return [("effect.on", "效果", "check", 0, 1, 1, 0)] + uses + [
                ("effect.amount_lens_blur", "镜头模糊强度", "slider", 0, 100, 1, 0),
                ("effect.amount_blur", "高斯模糊强度", "slider", 0, 100, 1, 0),
                ("effect.amount_mosaic", "马赛克大小", "slider", 0, 100, 1, 0),
                ("effect.grade.exposure", "调色·曝光", "slider", -2, 2, 0.01, 2),
                ("effect.grade.saturation", "调色·饱和", "slider", -1, 2, 0.01, 2),
                ("effect.mix", "效果混合 %", "slider", 0, 100, 1, 0),
                ("xform.on", "画面变换", "check", 0, 1, 1, 0), ("xform.scale", "画面缩放 %", "slider", 5, 800, 1, 0),
                ("xform.x", "画面位置 X", "slider", -4000, 4000, 1, 0),
                ("xform.y", "画面位置 Y", "slider", -4000, 4000, 1, 0),
                ("xform.rotation", "画面旋转 °", "slider", -360, 360, 0.5, 1),
                ("solid.on", "纯色", "check", 0, 1, 1, 0), ("solid.opacity", "填充不透明度 %", "slider", 0, 100, 1, 0),
                ("feather", "边缘羽化 px", "slider", 0, 300, 1, 0), ("opacity", "整体不透明度 %", "slider", 0, 100, 1, 0),
                ("range", "范围", "combo:" + ranges, 0, 0, 0, 0)]
    return []


# 显示值 ↔ 存储值：百分比的几项要换算
_PCT255 = {"opacity"}
_PCT1 = {"scale", "effect.mix", "xform.scale", "solid.opacity"}


def _to_store(kind, key, v):
    if key in _PCT255:
        return int(round(float(v) * 255.0 / 100.0))
    if key in _PCT1:
        return float(v) / 100.0
    return v


def _to_show(kind, key, v):
    if v is None:
        return None
    try:
        if key in _PCT255:
            return float(v) * 100.0 / 255.0
        if key in _PCT1:
            return float(v) * 100.0
    except (TypeError, ValueError):
        return None
    return v


def read_prop(ref_kind, obj, track, key):
    """当前值（显示用的单位）。视频读第一段片段。"""
    from cutora.render import adjust_layer as AL
    if ref_kind == "video":
        clips = _media_clips(track)
        if not clips:
            return None
        v = getattr(clips[0], key, 255 if key == "opacity" else (1.0 if key == "scale" else 0.0))
        return _to_show(ref_kind, key, v)
    if not isinstance(obj, dict):
        return None
    if ref_kind == "image":
        if key == "scale":
            return _to_show(ref_kind, key, obj.get("scale", 1.0))
        if key == "blend_mode":
            return str(obj.get("blend_mode") or "normal")
        els = [e for e in obj.get("elements") or [] if isinstance(e, dict)]
        src = els[0] if els else obj
        return _to_show(ref_kind, key, src.get(key, 255 if key == "opacity" else 0.0))
    if ref_kind == "adjust":
        head, _, tail = key.partition(".")
        if head == "effect":
            eff = AL.effect_of(obj)
            if tail.startswith("use_"):
                return tail[4:] in eff["kinds"]
            if tail.startswith("grade."):
                return eff["grade"].get(tail[6:])
            return _to_show(ref_kind, key, eff.get(tail))
        if head == "xform":
            return _to_show(ref_kind, key, AL.xform_of(obj)[tail])
        if head == "solid":
            return _to_show(ref_kind, key, AL.solid_of(obj)[tail])
        if key == "range":
            return AL.range_of(obj)
        if key == "feather":
            return float(obj.get("feather", 0.0) or 0.0)
        return _to_show(ref_kind, key, obj.get(key, 255))
    return _to_show(ref_kind, key, obj.get(key, 255 if key == "opacity" else (1.0 if key == "scale" else 0.0)))


def _write_path(obj, path, value, t_local=None):
    """写一个参数；这条路径开了关键帧就在 t_local 记关键帧（不改静态值）。"""
    from cutora.render.keyframe import path_enabled, set_point, write_path
    if t_local is not None and path_enabled(obj, path):
        set_point(obj, path, t_local, value, ease="linear")
        return
    if isinstance(obj, dict):
        write_path(obj, path, value)
    else:
        setattr(obj, path, value)


def write_prop(ref_kind, obj, track, key, shown_value, t=None) -> int:
    """把一个参数写到这一层（视频：轨道上每段片段）。返回改了几处。"""
    from cutora.render import adjust_layer as AL
    from cutora.render.keyframe import local_time
    v = _to_store(ref_kind, key, shown_value)
    if ref_kind == "video":
        n = 0
        for c in _media_clips(track):
            _write_path(c, key, float(v) if key != "opacity" else int(v), local_time(c, t) if t is not None else None)
            n += 1
        return n
    if not isinstance(obj, dict):
        return 0
    tl_local = local_time(obj, t) if t is not None else None
    if ref_kind == "image":
        if key in ("scale", "blend_mode"):
            obj[key] = v
            return 1
        els = [e for e in obj.get("elements") or [] if isinstance(e, dict)]
        if els:
            for e in els:
                e[key] = v
        else:
            _write_path(obj, key, v, tl_local)
        return 1
    if ref_kind == "adjust":
        head, _, tail = key.partition(".")
        if head == "effect":
            AL.migrate_effect(obj)             # Q179：老的单选存法先换成多选的
            if tail.startswith("use_"):
                return int(AL.set_effect_kind(obj, tail[4:], bool(v)))
            if tail.startswith("grade.") or tail.startswith("amount_"):
                eff = dict(obj.get("effect") or {})
                g = dict(AL.effect_of(obj)["grade"])
                g.update(eff.get("grade") or {})
                eff["grade"] = g
                obj["effect"] = eff
                _write_path(obj, key, float(v), tl_local)
                return 1
        if head in ("effect", "xform", "solid"):
            cur = {"effect": AL.effect_of, "xform": AL.xform_of, "solid": AL.solid_of}[head](obj)
            if tail in ("on",):
                cur[tail] = bool(v)
                obj[head] = cur
                return 1
            if tail == "kind":
                cur[tail] = str(v)
                obj[head] = cur
                return 1
            obj[head] = cur                    # 先补齐默认值，再按路径写（可能是关键帧）
            _write_path(obj, key, float(v), tl_local)
            return 1
        if key == "range":
            obj["range"] = str(v)
            return 1
        _write_path(obj, key, float(v) if key == "feather" else int(v), tl_local)
        return 1
    _write_path(obj, key, v, tl_local)
    return 1


# ───────────────────────────── 监视器 ─────────────────────────────

class LayerPropsMixin:
    """MonitorGrid 用。"""

    # ---- 当前图层 ----
    def current_layer_ref(self, tile, kind=None, payload=None):
        if tile is None:
            return None
        if kind == "overlay" and payload:
            ov = payload[1]
            return LayerRef(tile, mm.overlay_kind(ov), ov, lm.track_of_overlay(tile.task, ov))
        # 画面上或图层面板里点中的视频层。右键参数跟这一层，不跟它下面的格子。
        vids = [(i, tid) for i, tid in (getattr(self, "video_sel", None) or []) if i == tile.idx and tid]
        if len(vids) == 1:
            tr = self._track_by_id(tile.task, vids[0][1]) if hasattr(self, "_track_by_id") else None
            if tr is not None and getattr(tr, "type", "") == "video":
                return LayerRef(tile, "video", None, tr)
        panel = getattr(self, "layer_panel", None)
        if panel is not None and getattr(panel, "_tile", None) is tile:
            try:
                rows = panel.chosen_layers(strict=True)
            except Exception:
                rows = []
            if len(rows) == 1:
                ref = self.layer_ref_from_row(tile, rows[0])
                if ref is not None:
                    return ref
        if kind == "cell" and payload and payload[0] is not None:
            return LayerRef(tile, "video", None, payload[0])
        return None

    @staticmethod
    def layer_ref_from_row(tile, row):
        if row is None or row.get("kind") in lm.AUDIO_KINDS:
            return None
        track = lm.track_of_row(tile.task, row)
        if (row.get("locator") or {}).get("where") == "clip":
            if track is None or getattr(track, "type", "") != "video":
                return None
            return LayerRef(tile, "video", None, track)
        ov = lm.resolve(tile.task, row.get("locator"))
        if not isinstance(ov, dict):
            return None
        return LayerRef(tile, mm.overlay_kind(ov), ov, track)

    def sibling_layers(self, ref, targets) -> list:
        """[(格子, 对象, 轨道)]：点中的这一层 + 选中的其他视频里同一批的那一层（锁住的跳过）。"""
        out = []
        slot = mm.layer_slot(ref.tile.task, ref.track) if ref.kind == "video" and ref.track is not None else None
        tiles = [ref.tile] + [t for t in targets or [] if t is not ref.tile]
        for t in tiles:
            if t is ref.tile:
                obj, tr = ref.obj, ref.track
            elif ref.kind == "video":
                obj, tr = None, (mm.track_for_slot(t.task, slot) if slot else None)
            else:
                obj = mm.sibling_overlay(ref.tile.task, ref.obj, t.task)
                tr = lm.track_of_overlay(t.task, obj) if obj is not None else None
            if tr is None or (ref.kind != "video" and obj is None):
                continue
            if getattr(tr, "locked", False) or (isinstance(obj, dict) and obj.get("locked")):
                continue
            out.append((t, obj, tr))
        return out

    def layer_title(self, ref) -> str:
        if ref.kind == "video":
            return "%s · %s" % (KIND_WORD["video"], mm.track_display_name(ref.tile.task, ref.track))
        ov = ref.obj or {}
        if ref.kind == "text":
            name = str(ov.get("content") or "").replace("\n", " ").strip()[:12] or "文字"
        elif ref.kind == "adjust":
            from cutora.batch.layer_transfer import _adjust_label
            name = _adjust_label(ov)
        else:
            from cutora.batch.layer_transfer import _base_name, _image_path
            name = _base_name(_image_path(ov), "图片")
        return "%s · %s" % (KIND_WORD.get(ref.kind, "图层"), name)

    # ---- 菜单 ----
    def add_layer_props_menu(self, menu, ref, targets, *, include_restack=True):
        """「当前图层：xxx（同一批 N 条）」子菜单：属性滑杆 + 叠放顺序。视频层的翻转 / 混合 / 调色在 Q173 那一组。"""
        pairs = self.sibling_layers(ref, targets)
        n = len(pairs)
        sub = menu.addMenu("当前图层：%s%s" % (self.layer_title(ref), "（同一批 %d 条）" % n if n > 1 else ""))
        sub.setToolTipsVisible(True)
        if not pairs:
            a = sub.addAction("这一层锁住了（先在图层面板里解锁）")
            a.setEnabled(False)
            return sub
        self.add_props_widget(sub, ref, pairs)
        if include_restack:
            self.add_restack_menu(sub, ref, pairs)
        return sub

    def add_restack_menu(self, menu, ref, pairs):
        om = menu.addMenu("叠放顺序（时间线轨道%s）" % ("，同一批 %d 条" % len(pairs) if len(pairs) > 1 else ""))
        om.setToolTipsVisible(True)
        vis = lm.stack_tracks(ref.tile.task)
        pos = vis.index(ref.track) if ref.track in vis else -1
        for how, label in RESTACK:
            a = om.addAction(label, lambda h=how, lb=label: self.layer_restack_tracks(pairs, h, lb))
            if pos >= 0 and ((how in ("up", "top") and pos == len(vis) - 1) or (how in ("down", "bottom") and pos == 0)):
                a.setToolTip("这一条（点中的这条视频里）已经在%s了，其他视频里的同一批还会照常挪" % (
                    "最上面" if how in ("up", "top") else "最下面"))
        return om

    def layer_restack_tracks(self, pairs, how, label):
        """同一批的轨道一起上移 / 下移 / 置顶 / 置底。一步撤销。"""
        tiles, done = [], 0
        for t, _o, _tr in pairs:
            if t not in tiles:
                tiles.append(t)
        if not tiles:
            return 0
        tok = self.history.begin([t.task for t in tiles], snap=mm.snapshot_layers)
        seen = set()
        for t, _o, tr in pairs:
            if id(tr) in seen:
                continue
            seen.add(id(tr))
            try:
                done += int(lm.move_track(t.task, tr, how))
            except Exception:
                _LOG.exception("挪轨道失败")
        if not done:
            self.history.cancel(tok)
            self.status.emit("%s：已经到头了，没有可挪的" % label)
            return 0
        self._changed(tiles, video=True, overlays=True, visible_only=True)
        self._commit(tok, "叠放顺序：%s" % label, tiles)
        panel = getattr(self, "layer_panel", None)
        if panel is not None:
            try:
                panel.sync()
            except Exception:
                pass
        self.status.emit("叠放顺序：%s（%d 条视频）" % (label, len(tiles)))
        return done

    def add_props_widget(self, menu, ref, pairs):
        def _sync_check(cb, on):
            if cb is not None and cb.isChecked() != on:
                cb.blockSignals(True)
                cb.setChecked(on)
                cb.blockSignals(False)

        from cutora.ui.widgets.inspector import SliderSpin, ValueCombo
        w = QWidget()
        w.setObjectName("layerPropsPanel")
        form = QFormLayout(w)
        from cutora.ui.theme.tokens import SPACE_1H, SPACE_2
        form.setContentsMargins(SPACE_2, SPACE_1H, SPACE_2, SPACE_1H)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        head = QLabel("拖动即时生效（%d 条一起改），关菜单记一步撤销" % len(pairs))
        head.setObjectName("formHint")
        form.addRow(head)
        state = {"tok": None, "tiles": [], "pending": {}, "label": self.layer_title(ref)}
        timer = QTimer(w)
        timer.setSingleShot(True)
        timer.setInterval(60)
        widgets = {}
        for key, label, ctl, lo, hi, step, dec in prop_specs(ref.kind):
            cur = read_prop(ref.kind, ref.obj, ref.track, key)
            if ctl == "slider":
                sw = SliderSpin(lo, hi, step, dec)
                sw.setMinimumWidth(220)
                if cur is not None:
                    sw.setValue(float(cur))
                sw.valueChanged.connect(lambda v, k=key: self._props_queue(state, timer, k, float(v)))
            elif ctl == "check":
                sw = QCheckBox()
                sw.setChecked(bool(cur))
                sw.toggled.connect(lambda on, k=key: self._props_queue(state, timer, k, bool(on)))
                if key.startswith("effect.use_"):
                    # 勾上一种效果会顺手打开「效果」总开关（数据里已经开了，界面跟上）
                    sw.toggled.connect(lambda on, ws=widgets: on and _sync_check(ws.get("effect.on"), True))
            else:
                items = [tuple(x.split("=", 1)) for x in ctl.split(":", 1)[1].split(";")]
                sw = ValueCombo(items)
                if cur is not None:
                    sw.setCurrentText(str(cur))
                sw.valueChanged.connect(lambda v, k=key: self._props_queue(state, timer, k, str(v)))
            widgets[key] = sw
            form.addRow(label, sw)
        timer.timeout.connect(lambda: self._props_flush(state, pairs, ref))
        act = QWidgetAction(menu)
        act.setDefaultWidget(w)
        menu.addAction(act)

        def _closed():
            if timer.isActive():
                timer.stop()
                self._props_flush(state, pairs, ref)
            if state["tok"] is not None:
                self._commit(state["tok"], "图层属性：%s" % state["label"], state["tiles"],
                             overlays=ref.kind != "video")
                state["tok"] = None
        menu.aboutToHide.connect(_closed)
        w._props_state = state
        w._props_widgets = widgets
        w._props_flush = lambda: (timer.stop(), self._props_flush(state, pairs, ref))
        w._props_close = _closed
        return w

    def _props_queue(self, state, timer, key, value):
        state["pending"][key] = value
        timer.start()

    def _props_flush(self, state, pairs, ref):
        pending, state["pending"] = dict(state["pending"]), {}
        if not pending:
            return 0
        tiles = []
        for t, _o, _tr in pairs:
            if t not in tiles:
                tiles.append(t)
        if state["tok"] is None:
            snap = mm.snapshot_layers if ref.kind == "video" else None
            state["tok"] = (self.history.begin([t.task for t in tiles], snap=snap) if snap
                            else self.history.begin([t.task for t in tiles]))
            state["tiles"] = tiles
        n = 0
        for t, obj, tr in pairs:
            for key, val in pending.items():
                try:
                    n += write_prop(ref.kind, obj, tr, key, val, t=getattr(t, "t", None))
                except Exception:
                    _LOG.exception("图层属性写入失败 %s", key)
        video = ref.kind == "video"
        self._changed(tiles, video=video, overlays=True, visible_only=True)
        return n
