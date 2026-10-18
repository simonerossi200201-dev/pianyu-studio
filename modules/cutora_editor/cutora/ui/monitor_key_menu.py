# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q171 · 中央监视器右键：批量蓝绿幕抠像 / 批量开关分屏 / 批量取消。

挂在 ``MonitorGrid`` 上的 mixin（central_monitor.py 已经很长）。数据层在 ``cutora/batch/batch_key.py``。

性能约定（一批几十上百条也不卡）：
* 改参数只是改片段上的一个 dict —— 毫秒级；撤销只记片段的抠像 / 遮罩 / AI 抠像 / 节点图四样（``mm.snapshot_fx``）。
* 改完只作废画面，**不当场解码**：监视器原来的调度器在屏幕里一格一格补（看不见的格子不解码），
  抠像本身在渲染线程里算，界面线程不碰像素。
* 「自动识别屏幕色」要看画面：后台一条低优先级线程，一次只起一个 ffmpeg、只抽一帧、直接缩到 192×192，
  同一素材同一时刻只抽一次（本次打开监视器期间记住结果）；全部认完在界面线程里一次写入、一步撤销。
"""
from __future__ import annotations

import threading

import numpy as np

from PySide6.QtCore import QThread, Signal

from cutora.batch import batch_key as BK
from cutora.batch import monitor_model as mm
from cutora.core.log import get_logger

_LOG = get_logger("cutora.ui.monitor_key")

DESPILL_STRENGTH = (("弱", 0.5), ("中", 0.8), ("强", 1.0))
VIEW_LABELS = (("comp", "合成"), ("matte", "Matte（黑白）"), ("source", "原图"), ("spill", "只看溢色抑制"))


class KeyTuneWorker(QThread):
    """后台全自动精调：一个片段一个片段来 —— 抽 3 张小帧（一次只起一个 ffmpeg）→ 拾色 + 搜参数 + 定阈值 → 交回界面线程。

    每个片段算完就发 ``one``，界面那边当场写进这一条（看得见的格子随即重画），不等整批。"""

    progress = Signal(int, int)
    one = Signal(object, object)          # (key, result | None)

    def __init__(self, jobs, parent=None):
        super().__init__(parent)
        self._jobs = list(jobs)
        self._stop = threading.Event()
        self.setObjectName("cutora-key-tune")

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:  # noqa: D401
        n = len(self._jobs)
        for i, (key, path, times) in enumerate(self._jobs):
            if self._stop.is_set():
                break
            res = None
            try:
                frames = []
                for t in times:
                    if self._stop.is_set():
                        break
                    frames.append(BK.grab_frame_fit(path, t))
                if self._stop.is_set():
                    break
                res = BK.tune_frames(frames)
                if res is not None:
                    mid = [f for f in frames if f is not None]
                    res["frame"] = _small(mid[len(mid) // 2]) if mid else None
                del frames
            except Exception:
                _LOG.exception("自动精调失败 %s", path)
            self.one.emit(key, res)
            self.progress.emit(i + 1, n)
            self.msleep(15)               # 给解码线程 / 界面留一口气


class MatteHistWorker(QThread):
    """色阶条上的直方图：抽一帧小图、按这条现在的屏幕色 / 增益算原始 Matte，统计每种透明度多少像素。"""

    ready = Signal(object, object)        # (counts, frame)

    def __init__(self, path, t, keyer, frame=None, parent=None):
        super().__init__(parent)
        self._path, self._t, self._k, self._frame = path, t, dict(keyer or {}), frame
        self.setObjectName("cutora-key-hist")

    def run(self) -> None:  # noqa: D401
        try:
            from cutora.ui.widgets.key_levels import alpha_histogram
            f = self._frame if self._frame is not None else _small(BK.grab_frame_fit(self._path, self._t, 320))
            if f is None:
                self.ready.emit(None, None)
                return
            a = BK._raw_alpha(np.multiply(f, np.float32(1.0 / 255.0), dtype=np.float32),
                              str(self._k.get("color") or "#1EC81E"), float(self._k.get("gain") or 1.2),
                              float(self._k.get("preblur") or 0.0))
            self.ready.emit(alpha_histogram(a), f)
        except Exception:
            _LOG.exception("读 Matte 直方图失败")
            self.ready.emit(None, None)


def _small(frame, side: int = 240):
    if frame is None:
        return None
    import cv2
    h, w = frame.shape[:2]
    s = float(side) / max(h, w)
    if s >= 1.0:
        return np.ascontiguousarray(frame)
    return cv2.resize(frame, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)


class KeyMenuMixin:
    """MonitorGrid 用：右键「蓝绿幕抠像」「分屏开关」「批量取消」。"""

    # ─────────── 状态 ───────────

    def _key_state(self) -> dict:
        st = self.__dict__.get("_q171_key")
        if st is None:
            st = {"target": BK.load_target(), "cell": None, "clip": None, "worker": None, "cache": {}}
            self.__dict__["_q171_key"] = st
        return st

    def key_target(self) -> tuple[str, str | None]:
        st = self._key_state()
        return st["target"], st["cell"]

    def set_key_target(self, target: str, cell: str | None = None) -> None:
        st = self._key_state()
        if cell:
            st["cell"] = str(cell)
        else:
            st["cell"] = None
            st["target"] = target if target in dict(BK.TARGETS) else BK.DEFAULT_TARGET
            BK.save_target(st["target"])
        self.status.emit("抠像作用于：%s" % self._key_target_label())

    def _key_target_label(self) -> str:
        target, cell = self.key_target()
        if cell:
            return "分屏里的 %s 格" % cell
        return dict(BK.TARGETS).get(target, target).replace("（默认）", "")

    def key_shutdown(self) -> None:
        st = self.__dict__.get("_q171_key")
        if not st:
            return
        w = st.get("worker")
        if w is not None:
            try:
                w.stop()
                w.wait(3000)
            except Exception:
                pass
            st["worker"] = None
        for hw in st.get("hist_workers") or []:
            try:
                hw.wait(3000)
            except Exception:
                pass
        st["hist_workers"] = []
        try:
            self._key_tune_finish()          # 中途关窗：已经抠好的那部分照样记撤销 / 通知主窗口
        except Exception:
            _LOG.exception("关窗时收尾全自动抠像失败")

    # ─────────── 右键菜单 ───────────

    def _add_key_menu(self, menu, tile, kind, payload, targets, suffix):
        sub = menu.addMenu("蓝绿幕抠像%s" % suffix)
        sub.setToolTipsVisible(True)
        target, cell = self.key_target()
        tasks = [t.task for t in targets]
        state = BK.keyer_state(tasks, target, cell)

        where = sub.addMenu("作用于：%s" % self._key_target_label())
        where.setToolTipsVisible(True)
        for key, label in BK.TARGETS:
            act = where.addAction(label, lambda k=key: self.set_key_target(k))
            act.setCheckable(True)
            act.setChecked(cell is None and target == key)
            act.setToolTip(BK.TARGET_HINTS.get(key, ""))
        hit_cell = payload[1] if (kind == "cell" and payload and len(payload) > 1 and payload[1]
                                   and mm.split_on(tile.task)) else None
        if hit_cell or cell:
            where.addSeparator()
            c = hit_cell or cell
            act = where.addAction("只抠右键点中的格子 %s（这次打开监视器有效）" % c,
                                  lambda _c=c: self.set_key_target(target, _c))
            act.setCheckable(True)
            act.setChecked(cell == c)
            act.setToolTip("选中的每条视频都抠分屏里同一格的视频")
        info = sub.addAction("范围内 %d 个视频片段 · 已抠 %d 个" % (state["clips"], state["on"]))
        info.setEnabled(False)
        sub.addSeparator()

        busy = self._key_state().get("worker") is not None
        if busy:
            sub.addAction("停止全自动抠像（已经抠好的保留）", self._key_stop_auto)
        auto = sub.addAction("全自动抠像（每条自己拾色 + 自动阈值，去掉半透明灰）", lambda: self.key_auto(targets))
        auto.setEnabled(not busy and state["clips"] > 0)
        auto.setToolTip("每个片段各抽 3 张小图：认绿 / 蓝 → 在它自己的屏幕色附近加减红绿蓝试色、配增益，"
                        "找到人和背景分得最开的那个 → 阈值落在两者之间。后台一个一个来，算完一条亮一条，不卡界面")
        self._add_levels(sub, tile, targets, state)
        from cutora.fx import keyer as K
        for key, label in K.PRESET_LABELS:
            a = sub.addAction(label, lambda k=key, lb=label: self.key_apply(targets, BK.op_preset(k), lb))
            a.setEnabled(state["clips"] > 0)
        sub.addSeparator()

        has_key = state["on"] > 0
        sp = sub.addMenu("消除溢色")
        sp.setEnabled(has_key)
        sp.addAction("开 · 只处理边缘 / 半透明处",
                     lambda: self.key_apply(targets, BK.op_patch({"despill_on": True, "despill_scope": "edges"}),
                                            "消除溢色：边缘"))
        sp.addAction("开 · 整个画面",
                     lambda: self.key_apply(targets, BK.op_patch({"despill_on": True, "despill_scope": "all"}),
                                            "消除溢色：整个画面"))
        for name, v in DESPILL_STRENGTH:
            sp.addAction("强度：%s" % name,
                         lambda _v=v, _n=name: self.key_apply(
                             targets, BK.op_patch({"despill_on": True, "despill": _v}), "溢色强度：%s" % _n))
        sp.addSeparator()
        sp.addAction("关", lambda: self.key_apply(targets, BK.op_patch({"despill_on": False}), "关闭消除溢色"))

        ed = sub.addMenu("边缘")
        ed.setEnabled(has_key)
        for text, key, sign in (("收缩一点（去绿边）", "erode", 1), ("扩张一点（找回发丝）", "erode", -1),
                                ("更柔一点", "blur", 1), ("更硬一点", "blur", -1),
                                ("去斑点 +1", "despot", 1), ("抠得更狠一点", "gain", 1), ("抠得轻一点", "gain", -1)):
            ed.addAction(text, lambda k=key, s=sign, tx=text: self.key_apply(targets, BK.op_step(k, s), tx))
        ed.addSeparator()
        ed.addAction("边缘恢复默认", lambda: self.key_apply(targets, BK.op_edges_default(), "边缘恢复默认"))

        vw = sub.addMenu("查看")
        vw.setEnabled(has_key)
        cur_view = (state.get("first") or {}).get("view")
        for key, label in VIEW_LABELS:
            a = vw.addAction(label, lambda k=key, lb=label: self.key_apply(targets, BK.op_patch({"view": k}),
                                                                         "查看：%s" % lb))
            a.setCheckable(True)
            a.setChecked(cur_view == key)
        sub.addSeparator()

        sub.addAction("复制这一条的抠像设置", lambda: self.key_copy(tile))
        clip = self._key_state().get("clip")
        pa = sub.addAction("粘贴抠像设置到选中的 %d 条" % len(targets), lambda: self.key_paste(targets))
        pa.setEnabled(clip is not None)
        sub.addSeparator()

        ms = sub.addMenu("遮罩")
        ms.setEnabled(state["masks"] + state["masks_off"] > 0)
        ms.addAction("全部开启", lambda: self.key_apply(targets, None, "开启遮罩", which="masks", on=True))
        ms.addAction("全部关闭", lambda: self.key_apply(targets, None, "关闭遮罩", which="masks", on=False))
        ai = sub.addMenu("AI 抠像")
        a1 = ai.addAction("开启（用已经算好的结果）",
                          lambda: self.key_apply(targets, None, "开启 AI 抠像", which="ai", on=True))
        a1.setEnabled(state["ai_off"] > 0)
        a2 = ai.addAction("关闭", lambda: self.key_apply(targets, None, "关闭 AI 抠像", which="ai", on=False))
        a2.setEnabled(state["ai"] > 0)
        ai.addSeparator()
        fine = ai.addAction("在主窗口精修这一条（点选对象、计算）", lambda: self.open_task.emit(tile.task))
        fine.setToolTip("AI 抠像要在画面上点选要保留的人 / 物，必须一条一条点；在主窗口右侧「AI 抠像」里做")
        sub.addSeparator()

        off = sub.addAction("关闭抠像（保留参数）", lambda: self.key_apply(targets, BK.op_enable(False), "关闭抠像"))
        off.setEnabled(has_key)
        on = sub.addAction("重新开启抠像", lambda: self.key_apply(targets, BK.op_enable(True), "重新开启抠像"))
        on.setEnabled(state["off_with_params"] > 0)
        rm = sub.addAction("移除抠像设置", lambda: self.key_apply(targets, BK.op_remove(), "移除抠像"))
        rm.setEnabled(has_key or state["off_with_params"] > 0)
        return sub

    # ─────────── 执行 ───────────

    def key_apply(self, targets, fn, label, *, which="keyer", on=True, detect=None):
        """一批视频一次改完、一步撤销。detect：{clip id: op}（自动识别用，每个片段各自的屏幕色）。"""
        targets = [t for t in (targets or []) if t is not None]
        if not targets:
            self.status.emit("先选中要抠像的视频")
            return None
        target, cell = self.key_target()
        tok = self.history.begin([t.task for t in targets], snap=mm.snapshot_fx)
        total = {"clips": 0, "changed": 0, "locked": 0, "graph_none": 0, "none_tasks": 0}
        changed_tiles = []
        for t in targets:
            try:
                if which == "masks":
                    r = BK.set_masks_enabled(t.task, target, on, cell)
                elif which == "ai":
                    r = BK.set_ai_matte_enabled(t.task, target, on, cell)
                elif detect is not None:
                    r = {"clips": 0, "changed": 0, "locked": 0, "graph_none": 0}
                    clips, st = BK.target_clips(t.task, target, cell)
                    r["clips"], r["locked"] = len(clips), st["locked"]
                    for c in clips:
                        op = detect.get(id(c))
                        if op is None:
                            continue
                        res = BK._write(c, op)
                        r["changed"] += int(res == "ok")
                        r["graph_none"] += int(res == "graph_none")
                else:
                    r = BK.apply_keyer(t.task, target, fn, cell)
            except Exception as exc:
                _LOG.exception("%s 失败 #%d", label, t.idx + 1)
                self.status.emit("%s：#%02d 失败（%s）" % (label, t.idx + 1, exc))
                continue
            for k in ("clips", "changed", "locked", "graph_none"):
                total[k] += r.get(k, 0)
            if not r.get("clips"):
                total["none_tasks"] += 1
            if r.get("changed"):
                changed_tiles.append(t)
        if changed_tiles:
            self._changed(changed_tiles, video=True, overlays=False, visible_only=True)
            self._commit(tok, label, changed_tiles, overlays=False)
        msg = self._key_summary(label, len(targets), total, len(changed_tiles))
        self.status.emit(msg)
        return total

    def _key_summary(self, label, n_tasks, total, n_changed) -> str:
        scope = self._key_target_label()
        if total["changed"]:
            msg = "%s：%d 条视频里 %d 个片段已改（%s）" % (label, n_changed, total["changed"], scope)
        elif total["clips"]:
            msg = "%s：%d 个片段本来就是这样，没改（%s）" % (label, total["clips"], scope)
        else:
            msg = "%s：选中的 %d 条里没有「%s」可抠" % (label, n_tasks, scope)
        extra = []
        if total["none_tasks"] and total["clips"]:
            extra.append("%d 条没有%s，跳过" % (total["none_tasks"], scope))
        if total["locked"]:
            extra.append("%d 个片段在锁定的轨道上，没动" % total["locked"])
        if total["graph_none"]:
            extra.append("%d 个片段用了节点图但图里没有抠像节点，没动" % total["graph_none"])
        if extra:
            msg += "；" + "；".join(extra)
        return msg

    def key_copy(self, tile):
        target, cell = self.key_target()
        clips, _st = BK.target_clips(tile.task, target, cell)
        from cutora.fx import keyer as K
        for c in clips:
            nodes = BK._graph_keyer_nodes(c)
            specs = ([K.normalize((n.get("params") or {}).get("keyer")) for n in nodes] if nodes is not None
                     else [K.normalize(getattr(c, "keyer", None))])
            for s in specs:
                if s["enabled"]:
                    self._key_state()["clip"] = dict(s)
                    self.status.emit("已复制抠像设置（屏幕色 %s）—— 选中别的视频右键「粘贴抠像设置」" % s["color"])
                    return True
        self.status.emit("这一条的「%s」没有开着的抠像，没东西可复制" % self._key_target_label())
        return False

    def key_paste(self, targets):
        params = self._key_state().get("clip")
        if not params:
            self.status.emit("还没复制抠像设置")
            return None
        return self.key_apply(targets, BK.op_paste(params), "粘贴抠像设置")

    # ─────────── 自动识别 ───────────

    def key_auto(self, targets):
        """全自动抠像：每个片段各自拾色 + 搜参数 + 定阈值。后台一个一个来，算完一条写一条，整批一步撤销。"""
        st = self._key_state()
        if st.get("worker") is not None:
            self.status.emit("全自动抠像正在进行，等这一批做完")
            return None
        targets = [t for t in (targets or []) if t is not None]
        target, cell = self.key_target()
        by_key, jobs = {}, []
        for t in targets:
            clips, _st = BK.target_clips(t.task, target, cell)
            for c in clips:
                pt = BK.tune_points(t.task, c)
                if pt is None:
                    continue
                key = (pt[0], round(float(c.in_t or 0.0), 1), round(float(c.out_t or 0.0), 1))
                if key not in by_key:
                    by_key[key] = []
                    if key not in st["cache"]:
                        jobs.append((key, pt[0], pt[1]))
                by_key[key].append((t, c))
        if not by_key:
            self.status.emit("全自动抠像：选中的 %d 条里没有「%s」可抠" % (len(targets), self._key_target_label()))
            return None
        run = {"token": self.history.begin([t.task for t in targets], snap=mm.snapshot_fx),
               "map": by_key, "done": 0, "total": len(by_key), "clean": 0, "warn": [], "miss": 0,
               "changed": set(), "pending_tiles": []}
        st["run"] = run
        for key in list(by_key):
            if key in st["cache"]:
                self._key_tune_one(key, st["cache"][key], from_cache=True)
        if not jobs:
            self._key_tune_finish()
            return None
        w = KeyTuneWorker(jobs, self)
        st["worker"] = w
        w.one.connect(self._key_tune_one)
        w.finished.connect(self._key_worker_gone)
        self.status.emit("全自动抠像：0 / %d（后台一个一个来，算完一条亮一条）…" % run["total"])
        w.start(QThread.Priority.LowPriority)
        return w

    def _key_tune_one(self, key, res, from_cache=False):
        st = self._key_state()
        run = st.get("run")
        if run is None:
            return
        if not from_cache:
            st["cache"][key] = res
            while len(st["cache"]) > 400:
                st["cache"].pop(next(iter(st["cache"])))
        run["done"] += 1
        pairs = run["map"].get(key) or []
        if not res:
            run["miss"] += 1
        else:
            op = BK.op_tuned(res["params"])
            for t, c in pairs:
                if BK._write(c, op) == "ok":
                    run["changed"].add(id(t))
                    if t not in run["pending_tiles"]:
                        run["pending_tiles"].append(t)
            if res.get("quality", {}).get("clean"):
                run["clean"] += 1
            else:
                for t, _c in pairs:
                    run["warn"].append(t.idx + 1)
        if not from_cache:
            name = ""
            if pairs:
                name = str(pairs[0][0].label or "")
            q = (res or {}).get("quality") or {}
            mark = "没认出绿 / 蓝幕" if not res else ("干净" if q.get("clean") else "可能要看一眼")
            self.status.emit("全自动抠像 %d / %d：%s %s" % (run["done"], run["total"], name, mark))
        self._key_flush_tiles(force=from_cache)

    def _key_flush_tiles(self, force=False):
        """算完的格子攒一下再一起作废重画（不是每条都打断调度器）。"""
        run = self._key_state().get("run")
        if run is None or not run["pending_tiles"]:
            return
        timer = self.__dict__.get("_q171_flush")
        if timer is None:
            from PySide6.QtCore import QTimer
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(250)
            timer.timeout.connect(self._key_flush_now)
            self.__dict__["_q171_flush"] = timer
        if force:
            self._key_flush_now()
        elif not timer.isActive():
            timer.start()

    def _key_flush_now(self):
        run = self._key_state().get("run")
        if run is None or not run["pending_tiles"]:
            return
        tiles, run["pending_tiles"] = run["pending_tiles"], []
        self._changed(tiles, video=True, overlays=False, visible_only=True)

    def _key_worker_gone(self):
        st = self._key_state()
        w = st.get("worker")
        st["worker"] = None
        if w is not None:
            w.deleteLater()
        self._key_tune_finish()

    def _key_stop_auto(self):
        st = self._key_state()
        w = st.get("worker")
        if w is not None:
            w.stop()
            self.status.emit("正在停止全自动抠像（已经抠好的保留）…")

    def _key_tune_finish(self):
        st = self._key_state()
        run = st.get("run")
        if run is None:
            return
        st["run"] = None
        self._key_flush_now_run(run)
        tiles = [t for t in self.tiles if id(t) in run["changed"]]
        if tiles:
            self._commit(run["token"], "全自动抠像", tiles, overlays=False)
        msg = "全自动抠像完成：%d 条视频、%d 个素材片段" % (len(tiles), run["done"])
        if run["clean"]:
            msg += "；%d 个干净" % run["clean"]
        if run["warn"]:
            nums = sorted(set(run["warn"]))
            msg += "；%d 条可能要看一眼（#%s）" % (len(nums), ", #".join("%02d" % n for n in nums[:12]))
        if run["miss"]:
            msg += "；%d 个没认出绿 / 蓝幕，没动" % run["miss"]
        if run["done"] < run["total"]:
            msg += "（中途停止，%d 个没做）" % (run["total"] - run["done"])
        self.status.emit(msg)
        run["warn_tiles"] = sorted(set(run["warn"]))
        self.__dict__["_q171_last_run"] = run

    def _key_flush_now_run(self, run):
        if run["pending_tiles"]:
            tiles, run["pending_tiles"] = run["pending_tiles"], []
            self._changed(tiles, video=True, overlays=False, visible_only=True)

    # ─────────── 阈值（色阶） ───────────

    def _key_first_spec(self, tile):
        """右键点中的这一条：范围内第一个开着的抠像（色阶条的起始值）。返回 (spec, clip) 或 (None, None)。"""
        from cutora.fx import keyer as K
        target, cell = self.key_target()
        clips, _st = BK.target_clips(tile.task, target, cell)
        for c in clips:
            nodes = BK._graph_keyer_nodes(c)
            specs = ([K.normalize((n.get("params") or {}).get("keyer")) for n in nodes] if nodes is not None
                     else [K.normalize(getattr(c, "keyer", None))])
            for s in specs:
                if s["enabled"]:
                    return s, c
        return None, None

    def _add_levels(self, sub, tile, targets, state):
        from PySide6.QtWidgets import QWidgetAction
        from cutora.ui.widgets.key_levels import KeyLevelsPanel
        spec, clip = self._key_first_spec(tile)
        if spec is None:
            hint = sub.addAction("阈值（色阶）：先抠像（标准绿幕 / 全自动）才能调")
            hint.setEnabled(False)
            return None
        panel = KeyLevelsPanel(spec["black"], spec["white"], len(targets),
                               apply_cb=lambda b, w, mode, b0, w0: self.key_levels_apply(targets, b, w, mode, b0, w0),
                               preview_cb=lambda b, w, _t=tile: self._key_levels_preview(_t, b, w))
        act = QWidgetAction(sub)
        act.setDefaultWidget(panel)
        sub.addAction(act)
        self._key_levels_hist(panel, tile, clip, spec)

        def _closed(_t=tile):
            # 拖到一半就关了菜单（Esc / 点外面）：预览的改动撤掉，不留没记撤销的改动
            if self._key_state().get("levels_preview") is not None:
                self._key_levels_undo_preview()
                self._changed([_t], video=True, overlays=False, prio=0)

        sub.aboutToHide.connect(_closed)
        return panel

    def _key_levels_hist(self, panel, tile, clip, spec):
        st = self._key_state()
        pt = BK.tune_points(tile.task, clip)
        if pt is None:
            panel.slider.set_histogram(None, "读不到这条的素材")
            return
        key = (pt[0], round(float(clip.in_t or 0.0), 1), round(float(clip.out_t or 0.0), 1))
        cached = (st["cache"].get(key) or {}).get("frame")
        times = pt[1]
        w = MatteHistWorker(pt[0], times[len(times) // 2], spec, frame=cached, parent=self)

        def _done(counts, frame, _p=panel, _w=w):
            if frame is not None and key in st["cache"] and st["cache"][key] is not None \
                    and st["cache"][key].get("frame") is None:
                st["cache"][key]["frame"] = frame
            try:
                _p.slider.set_histogram(counts, "" if counts is not None else "读不到这条的画面")
            except RuntimeError:
                pass                       # 菜单已经关了
            _w.deleteLater()

        w.ready.connect(_done)
        w.start(QThread.Priority.LowPriority)
        st.setdefault("hist_workers", []).append(w)
        st["hist_workers"] = [x for x in st["hist_workers"] if x.isRunning() or x is w]

    def _key_levels_preview(self, tile, b, w):
        """拖的时候只预览右键点中的那一条（直接改数值、只重画这一格）；松手才按选中的全部写入并记撤销。"""
        st = self._key_state()
        prev = st.get("levels_preview")
        target, cell = self.key_target()
        if prev is None or prev["tile"] is not tile:
            from cutora.fx import keyer as K
            saved = []
            for c in BK.target_clips(tile.task, target, cell)[0]:
                saved.append((c, K.normalize(getattr(c, "keyer", None)) if getattr(c, "keyer", None) else None))
            prev = {"tile": tile, "saved": saved}
            st["levels_preview"] = prev
        for c, k in prev["saved"]:
            if k and k["enabled"] and BK._graph_keyer_nodes(c) is None:
                c.keyer = dict(k, black=b, white=w)
        self._changed([tile], video=True, overlays=False, prio=0)

    def _key_levels_undo_preview(self):
        st = self._key_state()
        prev = st.pop("levels_preview", None)
        if prev is None:
            return
        for c, k in prev["saved"]:
            if k is not None and BK._graph_keyer_nodes(c) is None:
                c.keyer = k

    def key_levels_apply(self, targets, b, w, mode="rel", b0=None, w0=None):
        self._key_levels_undo_preview()
        if mode == "abs" or b0 is None or w0 is None:
            fn = BK.op_patch({"black": float(b), "white": float(w)})
            label = "阈值 %.2f / %.2f" % (b, w)
        else:
            fn = BK.op_threshold(float(b) - float(b0), float(w) - float(w0))
            label = "阈值一起挪 %+.2f / %+.2f" % (float(b) - float(b0), float(w) - float(w0))
        return self.key_apply(targets, fn, label)

    # ─────────── 分屏开关（批量） ───────────

    @staticmethod
    def _split_off_with_layout(task) -> bool:
        raw = task.get("split") if isinstance(task, dict) else None
        return isinstance(raw, dict) and not mm.split_on(task) and bool(raw.get("layout")) and bool(raw.get("cells"))

    def _add_split_quick(self, menu, targets, suffix):
        on = [t for t in targets if mm.split_on(t.task)]
        restorable = [t for t in targets if self._split_off_with_layout(t.task)]
        if on:
            menu.addAction("取消分屏%s" % ("（%d 条）" % len(on) if len(targets) > 1 else ""),
                           lambda tg=on: self._apply_layout(tg, None))
        if restorable:
            a = menu.addAction("恢复分屏（关掉前的布局）%s" % ("（%d 条）" % len(restorable) if len(targets) > 1 else ""),
                               lambda tg=restorable: self.restore_split(tg))
            a.setToolTip("重新打开关掉的分屏：布局、分界线位置、每格放哪条视频都和关之前一样")

    def restore_split(self, targets):
        targets = [t for t in (targets or []) if t is not None and self._split_off_with_layout(t.task)]
        if not targets:
            self.status.emit("选中的视频里没有关掉的分屏可以恢复")
            return 0
        from cutora.render.split_layout import apply_split_to_task
        tok = self.history.begin([t.task for t in targets], snap=mm.snapshot_split)
        ok = 0
        for t in targets:
            try:
                apply_split_to_task(t.task, enabled=True)
                ok += 1
            except Exception as exc:
                _LOG.exception("恢复分屏失败 #%d", t.idx + 1)
                self.status.emit("恢复分屏：#%02d 失败（%s）" % (t.idx + 1, exc))
        self._changed(targets, overlays=False, visible_only=True)
        self._commit(tok, "恢复分屏", targets, overlays=False)
        self.status.emit("已恢复 %d 条的分屏（关掉前的布局）" % ok)
        return ok

    # ─────────── 批量取消（一个地方关掉 / 去掉） ───────────

    def _add_undo_menu(self, menu, tile, targets, suffix):
        from cutora.render import backdrop as BG
        sub = menu.addMenu("批量取消%s" % suffix)
        sub.setToolTipsVisible(True)
        tasks = [t.task for t in targets]
        on = [t for t in targets if mm.split_on(t.task)]
        a = sub.addAction("取消分屏", lambda tg=on: self._apply_layout(tg, None))
        a.setEnabled(bool(on))
        bordered = [t for t in on if str((mm.split_of(t.task).get("border") or {}).get("style") or "none") != "none"]
        a = sub.addAction("去掉分界线", lambda tg=bordered: self._apply_border(tg, "none"))
        a.setEnabled(bool(bordered))
        a = sub.addAction("背景填充改回「无」", lambda: self._apply_bg_fill(targets, "none"))
        a.setToolTip("画面没铺满时露黑底（出厂设置）")
        ext = [t for t in targets if BG.has_external(t.task)]
        a = sub.addAction("去掉外部背景（图片 / 视频）", lambda tg=ext: self._apply_backdrop(tg, "each"))
        a.setEnabled(bool(ext))
        a = sub.addAction("重置全部格子的平移 / 缩放", lambda: self._apply_each(
            targets, self._reset_all_cells, "重置画面"))
        sub.addSeparator()
        target, cell = self.key_target()
        state = BK.keyer_state(tasks, target, cell)
        a = sub.addAction("关闭蓝绿幕抠像（%s）" % self._key_target_label(),
                          lambda: self.key_apply(targets, BK.op_enable(False), "关闭抠像"))
        a.setEnabled(state["on"] > 0)
        a = sub.addAction("关闭遮罩", lambda: self.key_apply(targets, None, "关闭遮罩", which="masks", on=False))
        a.setEnabled(state["masks"] > 0)
        a = sub.addAction("关闭 AI 抠像", lambda: self.key_apply(targets, None, "关闭 AI 抠像", which="ai", on=False))
        a.setEnabled(state["ai"] > 0)
        sub.addSeparator()
        can_undo = self.history.can_undo()
        a = sub.addAction("撤销上一步  Ctrl+Z", self.undo)
        a.setEnabled(can_undo)
        return sub
