# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q174 · 右侧属性面板里的「调整图层」：纯色（填充 / 不透明度 / 圆角 / 边框）+ 效果（镜头模糊 / 高斯模糊 /
马赛克 / 调色，作用于它下面的全部画面）+ 羽化。位置 / 大小 / 旋转 / 不透明度用覆层通用的「变换」那一组。"""
from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QCheckBox, QGridLayout, QHBoxLayout, QLabel, QWidget

from cutora.render import adjust_layer as AL
from cutora.ui.widgets.collapsible import CollapsibleSection
from cutora.ui.widgets.inspector import ColorSwatch, InspectForm, SliderSpin, ValueCombo, add_inspect_row


class AdjustPanel(CollapsibleSection):
    changed = Signal(str)            # 改了哪一项（"solid" / "effect" / "feather"）

    def __init__(self, parent=None):
        super().__init__("调整图层", parent)
        self.setObjectName("adjustPanel")
        self._ov = None
        self._loading = False
        self._emit_timer = QTimer(self)
        self._emit_timer.setSingleShot(True)
        self._emit_timer.setInterval(60)
        self._emit_timer.timeout.connect(self._flush)
        self._pending = ""
        self._undo_open = False
        body = QWidget(self)
        f = InspectForm(body)
        hint = QLabel("作用于它下面的所有图层（视频 / 图片 / 文字 / 字幕）。范围 = 这块形状或整个画面，"
                      "遮罩面板里画的遮罩再收窄。每一项都能打关键帧（行右边的秒表）。"
                      "要跟着画面里的东西走：选中那条视频片段 →「AI 跟踪」→「应用到」里选这块调整图层。")
        hint.setObjectName("formHint")
        hint.setWordWrap(True)
        f.addWidget(hint)
        modes = QHBoxLayout()
        self.solid_on = QCheckBox("纯色")
        self.effect_on = QCheckBox("效果")
        self.solid_on.setToolTip("填一块颜色（可以当色卡 / 底板 / 遮挡用）")
        self.effect_on.setToolTip("对这块形状里、它下面的全部画面做效果（关掉纯色就只剩效果）")
        modes.addWidget(self.solid_on)
        modes.addWidget(self.effect_on)
        modes.addStretch(1)
        mw = QWidget()
        mw.setLayout(modes)
        D, E = AL.SOLID_DEFAULTS, AL.EFFECT_DEFAULTS

        def _row(label, w, reset):
            return add_inspect_row(f, label, w, on_reset=reset)

        def _both():
            self.solid_on.setChecked(D["on"])
            self.effect_on.setChecked(E["on"])
        _row("启用", mw, _both)
        from PySide6.QtWidgets import QPushButton
        self.btn_match = QPushButton("匹配时间线时长")
        self.btn_match.setToolTip("这条调整条从 0 秒铺到时间线结尾（最远的片段 / 渲染终点）；关键帧位置不变")
        self.btn_match.clicked.connect(self.match_timeline)
        _row("时长", self.btn_match, None)
        self.range = ValueCombo([(k, lb) for k, lb in AL.RANGES])
        self.range.setToolTip("整个画面 = 全局调整图层（运镜推拉 / 晃动 / 全画面调色）；遮罩可以再收窄。"
                              "整个画面时在画布上点不中它（不挡下面的东西），在时间线 / 图层面板里选")
        _row("范围", self.range, lambda: self.range.setCurrentText("shape"))
        # Q175 画面变换：移动 / 缩放 / 旋转它下面的画面
        X = AL.XFORM_DEFAULTS
        self.xf_on = QCheckBox("移动 / 缩放 / 旋转下面的画面")
        r_xon = _row("画面变换", self.xf_on, lambda: self.xf_on.setChecked(X["on"]))
        self.xf_x = SliderSpin(-4000.0, 4000.0, 1.0, 0)
        self.xf_y = SliderSpin(-4000.0, 4000.0, 1.0, 0)
        self.xf_scale = SliderSpin(5.0, 800.0, 1.0, 0)
        self.xf_rot = SliderSpin(-360.0, 360.0, 0.5, 1)
        self.xf_edge = ValueCombo([(k, lb) for k, lb in AL.XFORM_EDGES])
        self.xf_edge.setToolTip("画面挪开 / 缩小后露出来的边：黑边、镜像补上、或者把最外一圈像素延伸出去")
        self._xf_rows = [_row("画面位置 X", self.xf_x, lambda: self.xf_x.setValue(0.0)),
                         _row("画面位置 Y", self.xf_y, lambda: self.xf_y.setValue(0.0)),
                         _row("画面缩放 %", self.xf_scale, lambda: self.xf_scale.setValue(100.0)),
                         _row("画面旋转 °", self.xf_rot, lambda: self.xf_rot.setValue(0.0)),
                         _row("露出的边", self.xf_edge, lambda: self.xf_edge.setCurrentText("black"))]
        self._xf_on_row = r_xon
        # 纯色
        self.color = ColorSwatch(D["color"])
        r_color = _row("填充颜色", self.color, lambda: self.color.set_hex(D["color"]))
        self.solid_op = SliderSpin(0, 100, 1, 0)
        r_op = _row("填充不透明度", self.solid_op, lambda: self.solid_op.setValue(round(D["opacity"] * 100)))
        self.radius = SliderSpin(0, 50, 1, 0)
        _row("圆角 %", self.radius, lambda: self.radius.setValue(round(D["radius"] * 100)))
        brow = QHBoxLayout()
        self.border_on = QCheckBox("显示")
        self.border_color = ColorSwatch(D["border_color"])
        brow.addWidget(self.border_on)
        brow.addWidget(self.border_color)
        brow.addStretch(1)
        bw = QWidget()
        bw.setLayout(brow)

        def _border_reset():
            self.border_on.setChecked(D["border_on"])
            self.border_color.set_hex(D["border_color"])
        r_border = _row("边框", bw, _border_reset)
        self.border_w = SliderSpin(0, 100, 1, 0)
        r_bw = _row("边框宽度", self.border_w, lambda: self.border_w.setValue(D["border_width"]))
        # Q175 描边生成动画
        self.stroke_in = SliderSpin(0.0, 10.0, 0.1, 1)
        self.stroke_in.setToolTip("这一段开头用几秒把边框沿形状一笔画出来；0 = 一开始就是完整的")
        r_sin = _row("描边生成 秒", self.stroke_in, lambda: self.stroke_in.setValue(D["stroke_in"]))
        self.stroke_out = SliderSpin(0.0, 10.0, 0.1, 1)
        self.stroke_out.setToolTip("结尾用几秒把边框收回去；0 = 不收")
        r_sout = _row("描边收回 秒", self.stroke_out, lambda: self.stroke_out.setValue(D["stroke_out"]))
        self.stroke_start = ValueCombo([(k, lb) for k, lb in AL.STROKE_STARTS])
        r_sst = _row("起笔位置", self.stroke_start, lambda: self.stroke_start.setCurrentText(D["stroke_start"]))
        self.stroke_dir = ValueCombo([(k, lb) for k, lb in AL.STROKE_DIRS])
        r_sdir = _row("描边方向", self.stroke_dir, lambda: self.stroke_dir.setCurrentText(D["stroke_dir"]))
        self.fill_follow = QCheckBox("描边快画完时填充再淡入")
        r_ff = _row("填充", self.fill_follow, lambda: self.fill_follow.setChecked(D["fill_follow"]))
        self._stroke_rows = [r_sin, r_sout, r_sst, r_sdir, r_ff]
        self._solid_rows = [r_color, r_op, r_border, r_bw]
        # 效果（Q179：可以多选，同时起作用）
        kgrid = QGridLayout()
        kgrid.setContentsMargins(0, 0, 0, 0)
        self.kind_checks = {}
        for i, (k, lb) in enumerate(AL.EFFECT_KINDS):
            cb = QCheckBox(lb)
            cb.setToolTip("可以同时勾好几种：按「马赛克 → 高斯模糊 → 镜头模糊 → 调色」的顺序叠起来做"
                          "（比如镜头模糊 + 调色压暗）")
            self.kind_checks[k] = cb
            kgrid.addWidget(cb, i // 2, i % 2)
        kw = QWidget()
        kw.setLayout(kgrid)

        def _kinds_reset():
            for k, cb in self.kind_checks.items():
                cb.setChecked(k == E["kind"])
        r_kind = _row("效果（可多选）", kw, _kinds_reset)
        self.amt_lens = SliderSpin(0, 100, 1, 0)
        self.amt_blur = SliderSpin(0, 100, 1, 0)
        self.amt_mosaic = SliderSpin(0, 100, 1, 0)
        self._amount_rows = {
            "lens_blur": _row("镜头模糊强度", self.amt_lens, lambda: self.amt_lens.setValue(E["amount"])),
            "blur": _row("高斯模糊强度", self.amt_blur, lambda: self.amt_blur.setValue(E["amount"])),
            "mosaic": _row("马赛克大小", self.amt_mosaic, lambda: self.amt_mosaic.setValue(E["amount"])),
        }
        self.highlights = SliderSpin(0, 100, 1, 0)
        self._hl_row = _row("光斑 %", self.highlights, lambda: self.highlights.setValue(round(E["highlights"] * 100)))
        self.highlights.setToolTip("镜头模糊里亮点散成六边形光斑的程度（默认 35）")
        self.g_exp = SliderSpin(-4.0, 4.0, 0.01, 2)
        self.g_con = SliderSpin(-1.0, 3.0, 0.01, 2)
        self.g_sat = SliderSpin(-1.0, 4.0, 0.01, 2)
        self.g_tmp = SliderSpin(-5500.0, 8000.0, 10.0, 0)
        self._grade_rows = [_row("曝光", self.g_exp, lambda: self.g_exp.setValue(0.0)),
                            _row("对比", self.g_con, lambda: self.g_con.setValue(0.0)),
                            _row("饱和", self.g_sat, lambda: self.g_sat.setValue(0.0)),
                            _row("色温", self.g_tmp, lambda: self.g_tmp.setValue(0.0))]
        self.mix = SliderSpin(0, 100, 1, 0)
        self.mix.setToolTip("所有效果叠完之后整体和原画面的混合比例")
        r_mix = _row("效果混合 %", self.mix, lambda: self.mix.setValue(round(E["mix"] * 100)))
        self._effect_rows = [r_kind, r_mix]
        # 范围
        self.feather = SliderSpin(0, 300, 1, 0)
        _row("边缘羽化 px", self.feather, lambda: self.feather.setValue(AL.FEATHER_DEFAULT))
        body.setLayout(f)
        self.addWidget(body)
        for w, key in ((self.solid_on.toggled, "solid"), (self.effect_on.toggled, "effect"),
                       (self.color.changed, "solid"), (self.solid_op.valueChanged, "solid"),
                       (self.radius.valueChanged, "solid"), (self.border_on.toggled, "solid"),
                       (self.border_color.changed, "solid"), (self.border_w.valueChanged, "solid"),
                       (self.stroke_in.valueChanged, "solid"), (self.stroke_out.valueChanged, "solid"),
                       (self.stroke_start.valueChanged, "solid"), (self.stroke_dir.valueChanged, "solid"),
                       (self.fill_follow.toggled, "solid"),
                       (self.amt_lens.valueChanged, "effect"), (self.amt_blur.valueChanged, "effect"),
                       (self.amt_mosaic.valueChanged, "effect"),
                       (self.mix.valueChanged, "effect"), (self.highlights.valueChanged, "effect"), (self.g_exp.valueChanged, "effect"),
                       (self.g_con.valueChanged, "effect"), (self.g_sat.valueChanged, "effect"),
                       (self.g_tmp.valueChanged, "effect"), (self.feather.valueChanged, "feather"),
                       (self.range.valueChanged, "range"), (self.xf_on.toggled, "xform"),
                       (self.xf_x.valueChanged, "xform"), (self.xf_y.valueChanged, "xform"),
                       (self.xf_scale.valueChanged, "xform"), (self.xf_rot.valueChanged, "xform"),
                       (self.xf_edge.valueChanged, "xform")):
            w.connect(lambda *_a, k=key: self._edit(k))
        for cb in self.kind_checks.values():
            cb.toggled.connect(self._on_kind_toggled)

    # ---- 读 ----
    def load(self, ov) -> None:
        self._ov = ov if AL.is_adjust(ov) else None
        if self._ov is None:
            return
        AL.migrate_effect(ov)          # Q179：老的单选效果换成多选的存法（画面不变，关键帧跟着走）
        sol, eff = AL.solid_of(ov), AL.effect_of(ov)
        self._loading = True
        try:
            self.solid_on.setChecked(sol["on"])
            self.effect_on.setChecked(eff["on"])
            self.color.set_hex(sol["color"], emit=False)
            self.solid_op.setValue(round(sol["opacity"] * 100))
            self.radius.setValue(round(sol["radius"] * 100))
            self.border_on.setChecked(sol["border_on"])
            self.border_color.set_hex(sol["border_color"], emit=False)
            self.border_w.setValue(sol["border_width"])
            self.stroke_in.setValue(sol["stroke_in"])
            self.stroke_out.setValue(sol["stroke_out"])
            self.stroke_start.setCurrentText(sol["stroke_start"])
            self.stroke_dir.setCurrentText(sol["stroke_dir"])
            self.fill_follow.setChecked(sol["fill_follow"])
            for k, cb in self.kind_checks.items():
                cb.setChecked(k in eff["kinds"])
            self.amt_lens.setValue(eff["amount_lens_blur"])
            self.amt_blur.setValue(eff["amount_blur"])
            self.amt_mosaic.setValue(eff["amount_mosaic"])
            self.highlights.setValue(round(eff["highlights"] * 100))
            self.mix.setValue(round(eff["mix"] * 100))
            g = eff["grade"]
            self.g_exp.setValue(g.get("exposure", 0.0))
            self.g_con.setValue(g.get("contrast", 0.0))
            self.g_sat.setValue(g.get("saturation", 0.0))
            self.g_tmp.setValue(g.get("temperature", 0.0))
            self.feather.setValue(float(ov.get("feather", 0.0) or 0.0))
            self.range.setCurrentText(AL.range_of(ov))
            xf = AL.xform_of(ov)
            self.xf_on.setChecked(xf["on"])
            self.xf_x.setValue(xf["x"])
            self.xf_y.setValue(xf["y"])
            self.xf_scale.setValue(xf["scale"] * 100.0)
            self.xf_rot.setValue(xf["rotation"])
            self.xf_edge.setCurrentText(xf["edge"])
        finally:
            self._loading = False
        self._sync_visibility()

    @staticmethod
    def _combo_value(combo, items, default):
        v = str(combo.currentText() or "")
        return v if v in dict(items) else default

    def _kinds_value(self) -> list:
        return [k for k in AL.EFFECT_CHAIN if self.kind_checks[k].isChecked()]

    def _on_kind_toggled(self, checked: bool) -> None:
        # 勾上一种效果却没开「效果」总开关：顺手打开（一次编辑、一步撤销）
        if checked and not self._loading and not self.effect_on.isChecked():
            self.effect_on.blockSignals(True)
            self.effect_on.setChecked(True)
            self.effect_on.blockSignals(False)
        self._edit("effect")

    def _sync_visibility(self) -> None:
        solid = self.solid_on.isChecked()
        eff = self.effect_on.isChecked()
        for r in self._solid_rows:
            r.setEnabled(solid)
        self.border_w.setEnabled(solid and self.border_on.isChecked())
        bord = solid and self.border_on.isChecked()
        for r in self._stroke_rows:
            r.setVisible(bord)
        for r in self._effect_rows:
            r.setEnabled(eff)
        kinds = set(self._kinds_value())
        for r in self._grade_rows:
            r.setVisible("grade" in kinds)
            r.setEnabled(eff)
        for k, r in self._amount_rows.items():
            r.setVisible(k in kinds)
            r.setEnabled(eff)
        self._hl_row.setVisible("lens_blur" in kinds)
        self._hl_row.setEnabled(eff)
        for r in self._xf_rows:
            r.setVisible(self.xf_on.isChecked())

    # ---- 写 ----
    def _edit(self, key: str) -> None:
        if self._loading or self._ov is None:
            return
        ov = self._ov
        if not self._undo_open:
            self._undo_open = self._undo_begin()      # 改之前拍一张（一次连续调整 = 一步撤销）
        sol = dict(AL.solid_of(ov))
        sol.update({"on": self.solid_on.isChecked(), "color": self.color.hex() or sol["color"],
                    "opacity": float(self.solid_op.value()) / 100.0, "radius": float(self.radius.value()) / 100.0,
                    "border_on": self.border_on.isChecked(), "border_color": self.border_color.hex() or "#FFFFFF",
                    "border_width": float(self.border_w.value()),
                    "stroke_in": float(self.stroke_in.value()), "stroke_out": float(self.stroke_out.value()),
                    "stroke_start": self._combo_value(self.stroke_start, AL.STROKE_STARTS, "top_left"),
                    "stroke_dir": self._combo_value(self.stroke_dir, AL.STROKE_DIRS, "cw"),
                    "fill_follow": self.fill_follow.isChecked()})
        eff = AL.effect_of(ov)
        kinds = self._kinds_value()
        eff.update({"on": self.effect_on.isChecked(), "kinds": kinds,
                    "amount_lens_blur": float(self.amt_lens.value()), "amount_blur": float(self.amt_blur.value()),
                    "amount_mosaic": float(self.amt_mosaic.value()), "mix": float(self.mix.value()) / 100.0,
                    "highlights": float(self.highlights.value()) / 100.0})
        if kinds:
            eff["kind"] = kinds[0]           # 老版本只认单选：给它留第一种
        eff["grade"] = {"exposure": float(self.g_exp.value()), "contrast": float(self.g_con.value()),
                        "saturation": float(self.g_sat.value()), "temperature": float(self.g_tmp.value())}
        xf = AL.xform_of(ov)
        xf.update({"on": self.xf_on.isChecked(), "x": float(self.xf_x.value()), "y": float(self.xf_y.value()),
                   "scale": float(self.xf_scale.value()) / 100.0, "rotation": float(self.xf_rot.value()),
                   "edge": self._combo_value(self.xf_edge, AL.XFORM_EDGES, "black")})
        ov["solid"] = sol
        ov["effect"] = eff
        ov["xform"] = xf
        ov["range"] = self._combo_value(self.range, AL.RANGES, "shape")
        ov["feather"] = float(self.feather.value())
        self._key_changed_paths(ov)
        self._sync_visibility()
        self._pending = key
        self._emit_timer.start()

    # 能打关键帧的参数：(控件属性名, 参数路径, 存储值 = 控件值 × 系数)。属性面板按这张表挂秒表、
    # 播放头移动时把控件换成那一刻的值；这里改值时如果这条路径开了关键帧，就在播放头处记一个（同一步撤销）。
    KF_PATHS = (
        ("xf_x", "xform.x", 1.0), ("xf_y", "xform.y", 1.0), ("xf_scale", "xform.scale", 0.01),
        ("xf_rot", "xform.rotation", 1.0),
        ("amt_lens", "effect.amount_lens_blur", 1.0), ("amt_blur", "effect.amount_blur", 1.0),
        ("amt_mosaic", "effect.amount_mosaic", 1.0),
        ("mix", "effect.mix", 0.01), ("highlights", "effect.highlights", 0.01),
        ("g_exp", "effect.grade.exposure", 1.0), ("g_con", "effect.grade.contrast", 1.0),
        ("g_sat", "effect.grade.saturation", 1.0), ("g_tmp", "effect.grade.temperature", 1.0),
        ("solid_op", "solid.opacity", 0.01), ("radius", "solid.radius", 0.01),
        ("border_w", "solid.border_width", 1.0), ("feather", "feather", 1.0),
    )

    def _playhead_local(self, ov) -> float:
        from cutora.render.keyframe import local_time
        win = self.window()
        tlw = getattr(win, "timeline", None) if win is not None else None
        t = float(getattr(tlw, "position", 0) or 0) / 1000.0 if tlw is not None else 0.0
        return local_time(ov, t)

    def _key_changed_paths(self, ov) -> int:
        from cutora.render.keyframe import path_enabled, read_path, set_point, value_at
        n = 0
        t_local = None
        for attr, path, factor in self.KF_PATHS:
            if not path_enabled(ov, path):
                continue
            if t_local is None:
                t_local = self._playhead_local(ov)
            v = float(getattr(self, attr).value()) * factor
            fb = read_path(ov, path)
            try:
                cur = float(value_at(ov, path, t_local, fb if fb is not None else v))
            except (TypeError, ValueError):
                continue
            if abs(cur - v) > 1e-6 * max(1.0, abs(v)) + 1e-9:
                set_point(ov, path, t_local, v, ease="linear")
                n += 1
        return n

    def match_timeline(self) -> bool:
        """「匹配时间线时长」：一步撤销。"""
        ov = self._ov
        win = self._win()
        if ov is None or win is None:
            return False
        tasks = getattr(win, "tasks", None) or []
        ci = int(getattr(win, "curr_idx", -1))
        if not (0 <= ci < len(tasks)):
            return False
        win.push_undo("调整条匹配时间线时长")
        if not AL.match_timeline_duration(ov, tasks[ci]):
            hm = getattr(win, "history_mgr", None)
            if hm is not None and hasattr(hm, "set_after"):
                win.capture_undo_after()         # 什么都没变：这一步会被当成空操作丢掉
            bar = getattr(win, "statusBar", None)
            if callable(bar):
                bar().showMessage("这条调整条已经和时间线一样长了", 3000)
            return False
        win.capture_undo_after()
        self.changed.emit("timing")
        bar = getattr(win, "statusBar", None)
        if callable(bar):
            bar().showMessage("调整条已铺满时间线：0 – %.2f 秒" % float(ov.get("end", 0.0)), 4000)
        return True

    def _win(self):
        w = self.window()
        return w if callable(getattr(w, "push_undo", None)) else None

    def _undo_begin(self) -> bool:
        win = self._win()
        if win is None:
            return False
        try:
            win.push_undo("调整图层")
            return True
        except Exception:
            return False

    def _flush(self) -> None:
        if self._undo_open:
            self._undo_open = False
            win = self._win()
            cap = getattr(win, "capture_undo_after", None) if win is not None else None
            if callable(cap):
                cap()
        if self._pending:
            k, self._pending = self._pending, ""
            self.changed.emit(k)
