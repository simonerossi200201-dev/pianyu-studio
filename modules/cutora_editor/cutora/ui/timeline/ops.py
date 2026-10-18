# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q145 第二期 · 时间线快捷操作（TimelineWidget 继承）。

键（时间线有焦点时）
  V / C / R            选择 / 切刀 / 变速工具（主窗口里也是全局键）
  Delete / Backspace    删除选中的全部（片段、文字 / 图片、字幕），一次撤销
  Shift+Delete          波纹删除：删掉并把后面同轨的片段、字幕、标记前移
  Q / W                 把播放头所在片段的开头 / 结尾剪到播放头，后面跟着前移（主流剪辑软件同款）
  ↑ / ↓                 跳到上一个 / 下一个剪辑点
  , / .                 选中项（片段、字幕、文字 / 图片）微移 1 帧；Shift ×10
  Ctrl+C / X / V        复制 / 剪切 / 在播放头粘贴（片段、文字 / 图片、字幕可以混着选）
  Ctrl+D                原地复制：副本紧跟在选中内容后面
  Ctrl+= / Ctrl+−       时间线放大 / 缩小（以播放头为中心）
鼠标
  Ctrl+滚轮             以光标为中心缩放（原来以播放头为中心）
  拖空白处 / 标尺       擦洗：播放头跟着走，期间预览半分辨率，松手回全分辨率
  Ctrl+点文字 / 图片    多选；多选后拖其中一个，整组一起移动（片段同样）

设计见 docs/plan/Q145_SHORTCUTS_DESIGN.md 4.3。
"""
from __future__ import annotations

import copy
import time
import uuid

from PySide6.QtCore import Qt

from cutora.core.log import get_logger

_LOG = get_logger("cutora.ui.timeline.ops")

NUDGE_SHIFT_FRAMES = 10


def _tl_of(task):
    tl = task.get("timeline") if isinstance(task, dict) else None
    if tl is not None and isinstance(task, dict):
        tl._task = task
    return tl


def _plain_ov_copy(ov: dict) -> dict:
    out = copy.deepcopy({k: v for k, v in ov.items()
                         if not str(k).startswith("_") or k == "_track_id"})
    return out


class TimelineOpsMixin:
    """需要宿主（TimelineWidget）提供 task_data、position、selected_* 各通道、_clip_sel、
    _bus_execute、can_edit_track、_track_of_clip、_get_audio_clip_list、_publish_selection、
    _reset_selection、_timeline_fps、seek_request、item_selected、item_dragged。"""

    def _ops_init(self):
        self._ov_multi: list = []          # 多选的文字 / 图片（按对象身份）
        self._scrubbing = False             # 拖空白 / 标尺擦洗中（主窗口据此半分辨率预览）
        self._tl_clipboard = None
        self._tl_clip_task = None
        self._clip_group_orig = None        # 多选片段整组拖动：[(track, clip, start)]
        self._clip_group_ctrl = False       # 这次按下是 Ctrl+点（加减多选），松手不收成单选
        self._ov_group_ctrl = False
        self._ov_group_orig = None          # 多选文字 / 图片整组拖动：[(ov, start, end)]
        self._ov_press_state = None         # 覆层拖动撤销：[(ov, start, end, track_id)]
        self._trim_undo_pushed = False

    # ─────────────────────────── 撤销 ───────────────────────────

    def _host_window(self):
        win = self.window()
        return win if (win is not None and callable(getattr(win, "push_undo", None))) else None

    def _one_undo(self, label, fn):
        """fn 里可以连着执行多条总线命令 / 直接改数据，整体只记一次撤销。fn 返回假 = 没改，撤掉这条。"""
        win = self._host_window()
        if win is None:
            return fn()
        win.push_undo(label, scope="task")       # Q159：时间线上的操作只动当前这条视频
        win._undo_hold = int(getattr(win, "_undo_hold", 0) or 0) + 1
        try:
            out = fn()
        except Exception:
            win._undo_hold -= 1
            try:
                win.history_mgr.pop_last()
            except Exception:
                pass
            raise
        win._undo_hold -= 1
        if out:
            try:
                win.capture_undo_after()
                win.mark_dirty()
            except Exception:
                _LOG.exception("撤销收尾失败")
        else:
            try:
                win.history_mgr.pop_last()
            except Exception:
                pass
        return out

    def _trim_push_undo(self, label: str) -> None:
        """裁剪头 / 尾手柄：一次拖动只在第一次移动前记一条撤销。"""
        if self._trim_undo_pushed:
            return
        win = self._host_window()
        if win is None:
            return
        try:
            win.push_undo(label, scope="task")
            self._trim_undo_pushed = True
        except Exception:
            _LOG.exception("裁剪撤销记录失败")

    def _trim_capture_undo(self) -> None:
        if not self._trim_undo_pushed:
            return
        self._trim_undo_pushed = False
        win = self._host_window()
        if win is None:
            return
        try:
            win.capture_undo_after()
            win.mark_dirty()
        except Exception:
            _LOG.exception("裁剪撤销收尾失败")

    def _after_edit(self):
        """直接改数据以后：时间线重画、预览刷新、主窗口面板同步。"""
        self._snap_edge_cache = None
        self._publish_selection()
        self.update()
        try:
            self.item_dragged.emit()
        except Exception:
            pass

    # ─────────────────────────── 选中内容 ───────────────────────────

    def _tracks_by_id(self) -> dict:
        tl = _tl_of(self.task_data)
        return {str(getattr(tr, "id", "")): tr for tr in getattr(tl, "tracks", []) or []}

    def _sel_clips(self) -> list:
        """选中的全部视频 / 音频片段 [(track, clip)]，去重。"""
        out = []
        seen = set()
        by_id = self._tracks_by_id()

        def add(tr, clip):
            if tr is None or clip is None or id(clip) in seen:
                return
            if clip not in (getattr(tr, "clips", []) or []):
                return
            seen.add(id(clip))
            out.append((tr, clip))

        keys = [(str(t), int(i)) for t, i in list(getattr(self, "_clip_sel", []) or [])]
        for tid, idx in keys:
            tr = by_id.get(tid)
            clips = list(getattr(tr, "clips", []) or []) if tr is not None else []
            if 0 <= idx < len(clips):
                add(tr, clips[idx])

        def primary_ok(tr, clip):
            # Ctrl+点把主选中那条从多选里减掉以后，它仍挂在 selected_video_clip 上 —— 不算选中
            if not keys:
                return True
            return (str(getattr(tr, "id", "")), self._clip_index_of(tr, clip)) in keys

        if self.selected_video_clip is not None:
            tr, clip = self.selected_video_clip
            if primary_ok(tr, clip):
                add(tr, clip)
        if self.selected_audio_idx is not None:
            ac = self._get_audio_clip_list()
            if 0 <= self.selected_audio_idx < len(ac):
                clip, _path = ac[self.selected_audio_idx]
                tr = self._track_of_clip(clip)
                if tr is not None and primary_ok(tr, clip):
                    add(tr, clip)
        return out

    def _sel_overlays(self) -> list:
        """选中的文字 / 图片 [(list_name, ov)]：主选中 + 多选。"""
        out = []
        task = self.task_data or {}
        if self.selected_overlay is not None:
            name, idx = self.selected_overlay
            rows = task.get(name) or []
            if name and isinstance(idx, int) and 0 <= idx < len(rows):
                out.append((name, rows[idx]))
        for ov in list(self._ov_multi or []):
            for name in ("text_overlays", "image_overlays"):
                if any(o is ov for o in task.get(name) or []):
                    if not any(o is ov for _n, o in out):
                        out.append((name, ov))
                    break
        return out

    def _sel_seg_ids(self) -> list:
        segs = (self.task_data or {}).get("segments") or []
        return sorted(int(i) for i in (self.selected_indices or []) if 0 <= int(i) < len(segs))

    def ov_is_selected(self, list_name, idx, ov) -> bool:
        if self.selected_overlay == (list_name, idx):
            return True
        return any(o is ov for o in (self._ov_multi or []))

    def _editable_clip(self, tr) -> bool:
        return not getattr(tr, "locked", False) and self.can_edit_track(tr)

    def _editable_overlay(self, list_name, ov=None) -> bool:
        if isinstance(ov, dict) and ov.get("type") == "adjust":
            # Q174：调整图层看它自己那条调整轨（不是第一条图片轨）
            tr = self._tracks_by_id().get(ov.get("_track_id"))
            return tr is None or self.can_edit_track(tr)
        return self.can_edit_track("text" if list_name == "text_overlays" else "images")

    # ─────────────────────────── 键盘 ───────────────────────────

    def _ops_key(self, event) -> bool:
        if not isinstance(self.task_data, dict) or not self.task_data:
            return False
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        busy_special = (getattr(self, "selected_seam", None) is not None
                        or getattr(self, "selected_transition", None) is not None)
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and not ctrl and not alt:
            if busy_special:
                return False
            # Q157 达芬奇习惯：Delete = 波纹删除（片段连同空隙一起去掉，后面前移）；
            # Backspace = 普通删除（留空隙）。Shift 加在哪个上都算波纹。
            if shift or key == Qt.Key.Key_Delete:
                if self._sel_clips() or self._sel_seg_ids() or self._sel_overlays():
                    return self.ripple_delete_selection()
                return False
            if self._sel_clips() or self._sel_overlays():
                return self.delete_selection()
            return False
        if ctrl or alt:
            if ctrl and not alt:
                if key == Qt.Key.Key_G:          # Q154：Ctrl+G 预合成 / Ctrl+Shift+G 解除
                    return self.ungroup_selection() if shift else self.group_selection()
                if key == Qt.Key.Key_C:
                    return self.copy_selection()
                if key == Qt.Key.Key_X:
                    return self.cut_selection()
                if key == Qt.Key.Key_V:
                    return self.paste_at_playhead()
                if key == Qt.Key.Key_D:
                    return self.duplicate_selection()
                if key in (Qt.Key.Key_Equal, Qt.Key.Key_Plus):
                    self.zoom_at_playhead(1.25)
                    return True
                if key == Qt.Key.Key_Minus:
                    self.zoom_at_playhead(0.8)
                    return True
            return False
        if key in (Qt.Key.Key_Comma, Qt.Key.Key_Period):
            n = (-1 if key == Qt.Key.Key_Comma else 1) * (NUDGE_SHIFT_FRAMES if shift else 1)
            self.nudge_frames(n)
            return True
        if shift:
            # Q157 达芬奇：Shift+[ 删掉播放头左边（到片段开头），Shift+] 删掉右边（到片段结尾），后面前移
            if key == Qt.Key.Key_BracketLeft:
                self.ripple_trim_to_playhead("start")
                return True
            if key == Qt.Key.Key_BracketRight:
                self.ripple_trim_to_playhead("end")
                return True
            return False
        if key == Qt.Key.Key_Q:
            self.ripple_trim_to_playhead("start")
            return True
        if key == Qt.Key.Key_W:
            self.ripple_trim_to_playhead("end")
            return True
        if key == Qt.Key.Key_Up:
            self.jump_edit(-1)
            return True
        if key == Qt.Key.Key_Down:
            self.jump_edit(1)
            return True
        if key in (Qt.Key.Key_V, Qt.Key.Key_C, Qt.Key.Key_R) and not event.isAutoRepeat():
            self.set_tool({Qt.Key.Key_V: "select", Qt.Key.Key_C: "razor",
                           Qt.Key.Key_R: "speed"}[key])
            return True
        return False

    # ─────────────────────────── Q154：预合成（原位分组） ───────────────────────────

    def _group_refs(self, clips, overlays=None) -> list:
        out = []
        for tr, c in clips or ():
            bag = list(getattr(tr, "clips", []) or [])
            if c in bag:
                out.append({"track_id": str(getattr(tr, "id", "")), "clip_index": bag.index(c)})
        task = self.task_data or {}
        for lname, ov in overlays or ():
            rows = task.get(lname) or []
            idx = next((i for i, o in enumerate(rows) if o is ov), -1)
            if idx >= 0:
                out.append({"overlay_list": str(lname), "overlay_index": idx})
        return out

    def _group_keys_for(self, clip) -> list:
        """锁住的组：这条所在组的全部成员 [(track_id, clip_index)]（含重复份）；没锁 / 不在组里 = []。"""
        from cutora.model import clip_groups as CG
        if not CG.is_locked_member(clip):
            return []
        tl = _tl_of(self.task_data)
        if tl is None:
            return []
        out = []
        for tr, c in CG.members(tl, CG.gid_of(clip)):
            if isinstance(c, dict):
                continue
            bag = getattr(tr, "clips", []) or []
            if c in bag:
                out.append((str(getattr(tr, "id", "")), bag.index(c)))
        return out

    def _group_overlays_for(self, item) -> list:
        """锁住的组：这条所在组的全部覆层成员 [ov, …]。"""
        from cutora.model import clip_groups as CG
        if not CG.is_locked_member(item):
            return []
        tl = _tl_of(self.task_data)
        if tl is None:
            return []
        return [c for _tr, c in CG.members(tl, CG.gid_of(item)) if isinstance(c, dict)]

    def _expand_group_keys(self, keys) -> list:
        """多选里凡是锁住的组的成员，把整组都带上（顺序保持，不重复；同组覆层也一并选上）。"""
        tl = _tl_of(self.task_data)
        if tl is None:
            return list(keys)
        by_id = self._tracks_by_id()
        out = list(keys)
        seen = set(out)
        ov_members = list(self._ov_multi or [])
        for tid, idx in list(keys):
            tr = by_id.get(str(tid))
            bag = list(getattr(tr, "clips", []) or []) if tr is not None else []
            if not (0 <= int(idx) < len(bag)):
                continue
            clip_obj = bag[int(idx)]
            for k in self._group_keys_for(clip_obj):
                if k not in seen:
                    seen.add(k)
                    out.append(k)
            for ov in self._group_overlays_for(clip_obj):
                if not any(o is ov for o in ov_members):
                    ov_members.append(ov)
        if ov_members != (self._ov_multi or []):
            self._ov_multi = ov_members
        return out

    def _expand_av_link_keys(self, keys) -> list:
        """Q204a：把同 link_group_id 的伴侣（仅 group_size>1）带进选择。"""
        from cutora.model.av_link import partners
        tl = _tl_of(self.task_data)
        if tl is None:
            return list(keys)
        index = getattr(self, "_av_link_index", None)
        if not index:
            from cutora.model.av_link import index_link_groups
            index = index_link_groups(tl)
            self._av_link_index = index
        by_id = self._tracks_by_id()
        out = list(keys)
        seen = set(out)
        for tid, idx in list(keys):
            tr = by_id.get(str(tid))
            bag = list(getattr(tr, "clips", []) or []) if tr is not None else []
            if not (0 <= int(idx) < len(bag)):
                continue
            for p_tr, p_c in partners(index, bag[int(idx)]):
                pbag = getattr(p_tr, "clips", []) or []
                if p_c not in pbag:
                    continue
                k = (str(getattr(p_tr, "id", "")), pbag.index(p_c))
                if k not in seen:
                    seen.add(k)
                    out.append(k)
        return out

    def _sel_groups(self) -> list:
        from cutora.model import clip_groups as CG
        out = []
        for _tr, c in self._sel_clips():
            gid = CG.gid_of(c)
            if gid and gid not in out:
                out.append(gid)
        for _ln, ov in self._sel_overlays():
            gid = CG.gid_of(ov)
            if gid and gid not in out:
                out.append(gid)
        return out

    def _group_notice(self, text: str):
        win = self.window()
        bar = getattr(win, "statusBar", None)
        if callable(bar):
            try:
                bar().showMessage(text, 3000)
            except Exception:
                pass

    def group_selection(self, *, name: str = "", loop_fill: bool = False) -> bool:
        """Ctrl+G：选中的片段 / 调整图层 / 覆层打成一个预合成组（已经在组里的会搬进新组）。"""
        from cutora.model import clip_groups as CG
        clips = [(tr, c) for tr, c in self._sel_clips()
                 if self._editable_clip(tr) and not CG.is_repeat(c)]
        ovs = [(ln, ov) for ln, ov in self._sel_overlays()
               if self._editable_overlay(ln, ov) and not CG.is_repeat(ov)]
        total = len(clips) + len(ovs)
        if not total:
            self._group_notice("先选中要预合成的片段或调整图层（可按住 Ctrl 多选或跨轨道框选）")
            return False
        gids = {CG.gid_of(c) for _t, c in clips} | {CG.gid_of(ov) for _ln, ov in ovs}
        if total == 1 and len(gids) == 1 and "" not in gids:
            self._group_notice("这条已经在组里了")
            return False
        refs = self._group_refs(clips, ovs)
        ok = self._bus_execute("group.create", {"clips": refs, "name": name,
                                                "loop_fill": bool(loop_fill)})
        if ok:
            self._group_notice("已预合成 %d 项：点一条 = 选整组；组内调整图层将仅作用于同组素材" % total)
            first_item = clips[0][1] if clips else ovs[0][1]
            self._reselect_group_of(first_item)
        return bool(ok)

    def ungroup_selection(self) -> bool:
        gids = self._sel_groups()
        if not gids:
            self._group_notice("选中的片段不在任何预合成组里")
            return False
        ok = True
        for gid in gids:
            ok = self._bus_execute("group.ungroup", {"group": gid}) and ok
        if ok:
            self._group_notice("已解除 %d 个预合成组" % len(gids))
            self._reset_selection()
            self._publish_selection()
            self.update()
        return bool(ok)

    def detach_selection(self) -> bool:
        from cutora.model import clip_groups as CG
        clips = [(tr, c) for tr, c in self._sel_clips() if CG.gid_of(c) and not CG.is_repeat(c)]
        ovs = [(ln, ov) for ln, ov in self._sel_overlays() if CG.gid_of(ov) and not CG.is_repeat(ov)]
        if not clips and not ovs:
            return False
        ok = self._bus_execute("group.detach", {"clips": self._group_refs(clips, ovs)})
        if ok:
            self._reset_selection()
            self._publish_selection()
            self.update()
        return bool(ok)

    def join_group(self, gid: str) -> bool:
        from cutora.model import clip_groups as CG
        clips = [(tr, c) for tr, c in self._sel_clips() if not CG.is_repeat(c) and CG.gid_of(c) != gid]
        ovs = [(ln, ov) for ln, ov in self._sel_overlays() if not CG.is_repeat(ov) and CG.gid_of(ov) != gid]
        if not clips and not ovs:
            return False
        ok = self._bus_execute("group.join", {"group": gid, "clips": self._group_refs(clips, ovs)})
        if ok:
            first_item = clips[0][1] if clips else ovs[0][1]
            self._reselect_group_of(first_item)
        return bool(ok)

    def set_group_meta(self, gid: str, **patch) -> bool:
        ok = self._bus_execute("group.set", {"group": gid, "patch": dict(patch)})
        if ok:
            self.update()
        return bool(ok)

    def _reselect_group_of(self, item):
        """命令跑完对象没变（原位改），把整组重新选上，画面立刻能看出是一组。"""
        from cutora.model import clip_groups as CG
        tl = _tl_of(self.task_data)
        if tl is None:
            return
        gid = CG.gid_of(item)
        self._reset_selection()
        if gid:
            clip_keys = []
            ov_list = []
            first_vc = None
            for tr, c in CG.members(tl, gid):
                if isinstance(c, dict):
                    ov_list.append(c)
                elif tr is not None:
                    bag = list(getattr(tr, "clips", []) or [])
                    if c in bag:
                        clip_keys.append((str(getattr(tr, "id", "")), bag.index(c)))
                        if first_vc is None and getattr(tr, "domain", "visual") == "visual":
                            first_vc = (tr, c)
            self._clip_sel = clip_keys
            self._ov_multi = ov_list
            if first_vc is not None:
                self.selected_video_clip = first_vc
                self.selected_track = first_vc[0]
            elif ov_list:
                task = self.task_data or {}
                for lname in ("image_overlays", "text_overlays"):
                    rows = task.get(lname) or []
                    j = next((i for i, o in enumerate(rows) if o is ov_list[0]), -1)
                    if j >= 0:
                        self.selected_overlay = (lname, j)
                        break
        self._publish_selection()
        self.update()

    # ─────────────────────────── 删除 ───────────────────────────

    def _delete_inner(self, ripple: bool) -> bool:
        clips = [(tr, c) for tr, c in self._sel_clips() if self._editable_clip(tr)]
        ovs = [(n, o) for n, o in self._sel_overlays() if self._editable_overlay(n, o)]
        segs = self._sel_seg_ids() if self.can_edit_track("subs") else []
        if not (clips or ovs or segs):
            return False
        changed = False
        if ripple and (clips or segs):
            targets = []
            for tr, c in clips:
                bag = list(getattr(tr, "clips", []) or [])
                if c in bag:
                    targets.append({"kind": "clip", "track_id": str(getattr(tr, "id", "")),
                                    "clip_index": bag.index(c)})
            targets.extend({"kind": "seg", "seg_id": int(i)} for i in segs)
            if targets and self._bus_execute("timeline.ripple_delete", {"targets": targets}):
                changed = True
        else:
            by_track = {}
            for tr, c in clips:
                by_track.setdefault(id(tr), (tr, []))[1].append(c)
            for tr, cs in by_track.values():
                bag = list(getattr(tr, "clips", []) or [])
                for idx in sorted((bag.index(c) for c in cs if c in bag), reverse=True):
                    if self._bus_execute("timeline.delete_clip", {
                            "track_id": str(getattr(tr, "id", "")), "clip_index": int(idx)}):
                        changed = True
            if segs and self._bus_execute("subtitle.delete_segments", {"seg_ids": segs}):
                changed = True
        task = self.task_data
        for name, ov in ovs:
            rows = task.get(name) or []
            for i, o in enumerate(rows):
                if o is ov:
                    del rows[i]
                    changed = True
                    break
        return changed

    def _clear_all_selection(self):
        self._reset_selection()
        self._ov_multi = []
        self._publish_selection()
        try:
            self.item_selected.emit("", None)
        except Exception:
            pass

    def delete_selection(self) -> bool:
        if self._one_undo("删除", lambda: self._delete_inner(False)):
            self._clear_all_selection()
            self._after_edit()
        return True

    def ripple_delete_selection(self) -> bool:
        if not (self._sel_clips() or self._sel_seg_ids() or self._sel_overlays()):
            self._status("先选中要波纹删除的片段或字幕")
            return True
        if self._one_undo("波纹删除", lambda: self._delete_inner(True)):
            self._clear_all_selection()
            self._after_edit()
            self._status("波纹删除：后面的内容已前移补位（Ctrl+Z 撤销）")
        return True

    # ─────────────────────────── 复制 / 剪切 / 粘贴 / 原地复制 ───────────────────────────

    def _snapshot_selection(self):
        from cutora.model.project_io import _RJ_CLIP_KEYS
        clips = []
        for tr, c in self._sel_clips():
            row = {
                "track_id": str(getattr(tr, "id", "")),
                "source_id": getattr(c, "source_id", None),
                "in_t": float(getattr(c, "in_t", 0.0)),
                "out_t": float(getattr(c, "out_t", 0.0)),
                "start_t": float(getattr(c, "start_t", 0.0)),
            }
            for k in _RJ_CLIP_KEYS:
                if hasattr(c, k):
                    row[k] = copy.deepcopy(getattr(c, k))
            clips.append(row)
        ovs = [(n, _plain_ov_copy(o)) for n, o in self._sel_overlays()]
        all_segs = (self.task_data or {}).get("segments") or []
        segs = [copy.deepcopy(all_segs[i]) for i in self._sel_seg_ids()]
        starts, ends = [], []
        from cutora.ui.timeline.snap import clip_span
        for tr, c in self._sel_clips():
            s, e = clip_span(c)
            starts.append(s)
            ends.append(e)
        for _n, o in ovs:
            starts.append(float(o.get("start") or 0.0))
            ends.append(float(o.get("end") or 0.0))
        for sg in segs:
            starts.append(float(sg.get("start") or 0.0))
            ends.append(float(sg.get("end") or 0.0))
        if not starts:
            return None
        return {"clips": clips, "overlays": ovs, "segments": segs,
                "t0": min(starts), "t1": max(ends)}

    def _task_key(self):
        """这条视频的身份：撤销会把任务整个换成深拷贝，所以不能用对象身份。"""
        task = self.task_data if isinstance(self.task_data, dict) else {}
        return str(task.get("video") or task.get("path") or id(task))

    def _arm_cross_video(self) -> bool:
        """顺手把选中的层放进和中央监视器共用的「图层剪贴板」（Q149，Grok 做的）：
        换一条视频再 Ctrl+V 就按原时间贴过去。本视频里 Ctrl+V 仍是贴到播放头。"""
        fn = getattr(self, "_copy_selected_layers", None)
        if not callable(fn):
            return False
        try:
            ok = bool(fn())
        except Exception:
            _LOG.exception("图层剪贴板失败")
            ok = False
        if not ok:
            self._layer_paste_armed = False
        return ok

    def copy_selection(self) -> bool:
        snap = self._snapshot_selection()
        if snap is None:
            return False
        self._tl_clipboard = snap
        self._tl_clip_task = self._task_key()
        cross = self._arm_cross_video()
        n = len(snap["clips"]) + len(snap["overlays"]) + len(snap["segments"])
        if cross:
            self._status("已复制 %d 项 —— 本视频里 Ctrl+V 贴到播放头；换一条视频 Ctrl+V 按原时间贴过去"
                         "（中央监视器里也能批量粘贴）" % n)
        else:
            self._status("已复制 %d 项 —— 移动播放头后 Ctrl+V 粘贴" % n)
        return True

    def _paste_snapshot(self, snap, at_t) -> bool:
        """把快照按原来的相对位置贴到 at_t（快照里最早的开头对齐 at_t）。"""
        delta = float(at_t) - float(snap["t0"])
        changed = False
        if snap["clips"]:
            c0 = min(float(r.get("start_t") or 0.0) for r in snap["clips"])
            items = copy.deepcopy(snap["clips"])
            if self._bus_execute("timeline.duplicate_clips",
                                 {"items": items, "time": max(0.0, c0 + delta)}):
                changed = True
        if snap["segments"] and self.can_edit_track("subs"):
            s0 = min(float(s.get("start") or 0.0) for s in snap["segments"])
            if self._bus_execute("subtitle.duplicate_segments", {
                    "items": copy.deepcopy(snap["segments"]), "time": max(0.0, s0 + delta)}):
                changed = True
        task = self.task_data
        tracks = self._tracks_by_id()
        new_ovs = []
        for name, ov in snap["overlays"]:
            if not self._editable_overlay(name, ov if ov.get("_track_id") in tracks else None):
                continue
            new = copy.deepcopy(ov)
            dur = max(self.MIN_DUR, float(new.get("end") or 0) - float(new.get("start") or 0))
            new["start"] = max(0.0, float(new.get("start") or 0) + delta)
            new["end"] = new["start"] + dur
            old_id = new.get("id")
            if isinstance(old_id, int) and not isinstance(old_id, bool):
                new["id"] = int(time.time() * 1000000) + len(new_ovs)
            else:
                new["id"] = "ov_%s" % uuid.uuid4().hex[:12]
            if new.get("_track_id") not in tracks:
                kind = "text" if name == "text_overlays" else (
                    "adjust" if new.get("type") == "adjust" else "image")
                first = next((t for t in tracks.values() if getattr(t, "type", "") == kind), None)
                if first is None and kind == "adjust":
                    # 这条视频还没有调整轨：建一条（叠在最上面），不能挂到图片轨上
                    from cutora.model.timeline_model import Track
                    tl = _tl_of(task)
                    if tl is not None:
                        first = Track("adjust")
                        tl.add_track(first)
                        tracks = self._tracks_by_id()
                new["_track_id"] = getattr(first, "id", None) if first is not None else None
            task.setdefault(name, []).append(new)
            new_ovs.append(new)
            changed = True
        if new_ovs:
            self._ov_multi = list(new_ovs)
        return changed

    def paste_at_playhead(self) -> bool:
        snap = self._tl_clipboard
        other_video = snap is not None and getattr(self, "_tl_clip_task", None) != self._task_key()
        if (not snap or other_video) and getattr(self, "_layer_paste_armed", False):
            # 别的视频复制来的（或中央监视器里复制的）：片段的素材来源在那条视频里，
            # 走 Q149 的跨视频贴法（按原时间，素材来源一起带过来）
            paste = getattr(self, "_paste_copied_layers", None)
            if callable(paste) and paste():
                return True
        if not snap:
            self._status("剪贴板是空的：先选中内容按 Ctrl+C")
            return True
        if other_video and snap.get("clips"):
            self._status("片段是从别的视频复制的，这里贴不了片段：先在中央监视器里用「粘贴图层」")
            snap = dict(snap, clips=[])
            if not (snap["overlays"] or snap["segments"]):
                return True
        at = float(self.position or 0) / 1000.0
        if self._one_undo("粘贴", lambda: self._paste_snapshot(snap, at)):
            self._after_edit()
        return True

    def cut_selection(self) -> bool:
        snap = self._snapshot_selection()
        if snap is None:
            return False
        self._tl_clipboard = snap
        self._tl_clip_task = self._task_key()
        self._arm_cross_video()
        if self._one_undo("剪切", lambda: self._delete_inner(False)):
            self._clear_all_selection()
            self._after_edit()
            self._status("已剪切 —— 移动播放头后 Ctrl+V 粘贴")
        return True

    def duplicate_selection(self) -> bool:
        snap = self._snapshot_selection()
        if snap is None:
            return False
        if self._one_undo("原地复制", lambda: self._paste_snapshot(snap, snap["t1"])):
            self._after_edit()
            self._status("已复制一份，放在选中内容后面")
        return True

    # ─────────────────────────── 微移 ───────────────────────────

    def nudge_frames(self, frames: int) -> None:
        fps = self._timeline_fps()
        delta = float(frames) / fps
        clips = [(tr, c) for tr, c in self._sel_clips() if self._editable_clip(tr)]
        ovs = [(n, o) for n, o in self._sel_overlays() if self._editable_overlay(n, o)]
        segs = self._sel_seg_ids() if self.can_edit_track("subs") else []
        if not (clips or ovs or segs):
            return
        # 整组一起动：最靠前的不能越过 0
        starts = [float(getattr(c, "start_t", 0.0)) for _t, c in clips]
        starts += [float(o.get("start") or 0.0) for _n, o in ovs]
        all_segs = (self.task_data or {}).get("segments") or []
        starts += [float(all_segs[i].get("start") or 0.0) for i in segs]
        if starts and min(starts) + delta < 0:
            delta = -min(starts)
        if abs(delta) < 1e-12:
            return

        def fn():
            changed = False
            if clips:
                items = []
                for tr, c in clips:
                    bag = list(getattr(tr, "clips", []) or [])
                    items.append({"track_id": str(getattr(tr, "id", "")), "clip_index": bag.index(c),
                                  "start": float(getattr(c, "start_t", 0.0)) + delta})
                if self._bus_execute("timeline.move_clips", {"clips": items}):
                    changed = True
            if segs:
                rows = [{"seg_id": i, "start": float(all_segs[i].get("start") or 0.0) + delta}
                        for i in segs]
                if self._bus_execute("subtitle.move_segments", {"segments": rows}):
                    changed = True
            for _n, o in ovs:
                o["start"] = float(o.get("start") or 0.0) + delta
                o["end"] = float(o.get("end") or 0.0) + delta
                changed = True
            return changed

        if self._one_undo("微移 %+d 帧" % frames, fn):
            self._after_edit()

    # ─────────────────────────── 波纹修剪到播放头（Q / W） ───────────────────────────

    def _clip_at_playhead(self):
        from cutora.ui.timeline.snap import clip_span
        t = float(self.position or 0) / 1000.0
        eps = 1e-6

        def contains(c):
            s, e = clip_span(c)
            return s + eps < t < e - eps

        for tr, c in self._sel_clips():
            if contains(c):
                return tr, c
        tl = _tl_of(self.task_data)
        tracks = list(getattr(tl, "tracks", []) or [])
        order = []
        if self.selected_track is not None and getattr(self.selected_track, "type", "") in ("video", "audio"):
            order.append(self.selected_track)
        order += [tr for tr in tracks if getattr(tr, "type", "") == "video" and getattr(tr, "is_main_video", False)]
        order += [tr for tr in tracks if getattr(tr, "type", "") in ("video", "audio")]
        for tr in order:
            if not getattr(tr, "visible", True):
                continue
            for c in getattr(tr, "clips", []) or []:
                if contains(c):
                    return tr, c
        return None, None

    def ripple_trim_to_playhead(self, side: str) -> None:
        from cutora.model.ripple import apply_delete_range
        from cutora.model.track_guard import assert_ripple_unlocked
        from cutora.ui.timeline.snap import clip_span
        tr, clip = self._clip_at_playhead()
        if clip is None:
            self._status("播放头下面没有片段（Q / W 修剪的是播放头所在的片段）")
            return
        t = float(self.position or 0) / 1000.0
        s, e = clip_span(clip)
        t0, t1 = (s, t) if side == "start" else (t, e)
        if t1 - t0 < self.MIN_DUR:
            return
        try:
            assert_ripple_unlocked(self.task_data, track_id=getattr(tr, "id", None))
        except ValueError as exc:
            self._status(str(exc))
            return

        def fn():
            apply_delete_range(self.task_data, t0, t1, getattr(tr, "id", None))
            return True

        if self._one_undo("波纹修剪开头" if side == "start" else "波纹修剪结尾", fn):
            self._after_edit()
            if side == "start":
                self.seek_request.emit(int(round(s * 1000)))    # 播放头停在新的剪辑点
            self._status("已%s %.2f 秒，后面跟着前移" % ("剪掉开头" if side == "start" else "剪掉结尾",
                                                    t1 - t0))

    # ─────────────────────────── 跳剪辑点（↑ / ↓） ───────────────────────────

    def edit_points(self) -> list:
        from cutora.ui.timeline.snap import clip_span
        pts = {0.0}
        tl = _tl_of(self.task_data)
        for tr in getattr(tl, "tracks", []) or []:
            if getattr(tr, "type", "") not in ("video", "audio") or not getattr(tr, "visible", True):
                continue
            for c in getattr(tr, "clips", []) or []:
                s, e = clip_span(c)
                pts.add(round(float(s), 4))
                pts.add(round(float(e), 4))
        return sorted(pts)

    def jump_edit(self, direction: int) -> None:
        t = float(self.position or 0) / 1000.0
        fps = self._timeline_fps()
        half = 0.5 / fps
        pts = self.edit_points()
        if direction > 0:
            nxt = [p for p in pts if p > t + half]
            if not nxt:
                return
            target = nxt[0]
        else:
            prv = [p for p in pts if p < t - half]
            if not prv:
                return
            target = prv[-1]
        self.seek_request.emit(int(round(target * 1000)))

    # ─────────────────────────── 以光标为中心缩放 / 擦洗 ───────────────────────────

    def zoom_at_x(self, factor: float, widget_x: float) -> None:
        """以窗口 x 处的时间为不动点缩放。"""
        old_pps = float(self.pixels_per_second)
        new_pps = max(1.0, min(300.0, old_pps * float(factor)))
        t = (float(widget_x) + float(self.scroll_x)) / old_pps
        self.pixels_per_second = new_pps
        self.scroll_x = max(0.0, t * new_pps - float(widget_x))
        qv = getattr(self, "_queue_visible_thumbs", None)
        if callable(qv):
            qv()
        parent = self.parent()
        bar = getattr(parent, "scrollbar", None) if parent is not None else None
        if bar is not None:
            bar.blockSignals(True)
            try:
                if hasattr(parent, "update_scroll_range"):
                    parent.update_scroll_range()
                bar.setValue(int(max(0, round(self.scroll_x))))
            finally:
                bar.blockSignals(False)
        self.update()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            d = event.angleDelta().y() or event.angleDelta().x()
            if d:
                self.zoom_at_x(1.25 if d > 0 else 0.8, event.position().x())
            event.accept()
            return
        event.ignore()          # 普通 / Shift 滚轮交给外层容器滚动

    def _scrub_to(self, real_x: float) -> None:
        self._scrubbing = True
        self.seek_request.emit(max(0, int(real_x / self.pixels_per_second * 1000)))

    def _scrub_end(self, real_x: float) -> None:
        if not self._scrubbing:
            return
        self._scrubbing = False
        # 松手再请求一次全分辨率帧
        self.seek_request.emit(max(0, int(real_x / self.pixels_per_second * 1000)))

    # ─────────────────────────── 覆层多选 / 整组拖 ───────────────────────────

    def _ov_group_begin(self, primary_ov):
        members = [o for _n, o in self._sel_overlays()]
        self._ov_press_state = [(o, float(o.get("start") or 0.0), float(o.get("end") or 0.0),
                                 o.get("_track_id")) for o in members] or None
        if len(members) > 1 and any(o is primary_ov for o in members):
            self._ov_group_orig = [(o, float(o.get("start") or 0.0), float(o.get("end") or 0.0))
                                   for o in members if o is not primary_ov]
        else:
            self._ov_group_orig = None

    def _ov_group_apply(self, delta: float) -> None:
        rows = self._ov_group_orig or []
        if not rows:
            return
        lo = min(s for _o, s, _e in rows)
        if lo + delta < 0:
            delta = -lo
        for o, s, e in rows:
            o["start"] = s + delta
            o["end"] = e + delta

    def _ov_drag_commit(self, label: str) -> bool:
        """覆层拖动松手：有变化就记一次撤销（先退回原样拍快照，再放回结果）。返回是否改了。"""
        state = self._ov_press_state
        self._ov_press_state = None
        self._ov_group_orig = None
        if not state:
            return False
        final = [(o, o.get("start"), o.get("end"), o.get("_track_id")) for o, *_r in state]
        changed = any(abs(float(f[1] or 0) - s) > 1e-6 or abs(float(f[2] or 0) - e) > 1e-6 or f[3] != tid
                      for f, (_o, s, e, tid) in zip(final, state))
        if not changed:
            return False
        for o, s, e, tid in state:
            o["start"], o["end"], o["_track_id"] = s, e, tid

        def fn():
            for o, s, e, tid in final:
                o["start"], o["end"], o["_track_id"] = s, e, tid
            return True

        self._one_undo(label, fn)
        return True

    def _clip_group_begin(self, primary_clip, ctrl: bool = False):
        self._clip_group_ctrl = bool(ctrl)
        clips = self._sel_clips()
        if len(clips) > 1 and any(c is primary_clip for _t, c in clips):
            self._clip_group_orig = [(tr, c, float(getattr(c, "start_t", 0.0)))
                                     for tr, c in clips if c is not primary_clip
                                     and self._editable_clip(tr)]
        else:
            self._clip_group_orig = None

    def _clip_group_apply(self, delta: float) -> float:
        rows = self._clip_group_orig or []
        if not rows:
            return delta
        lo = min(s for _t, _c, s in rows)
        if lo + delta < 0:
            delta = -lo
        for _t, c, s in rows:
            c.start_t = s + delta
        return delta

    def _clip_group_commit(self, primary_track, primary_clip, primary_orig_start) -> bool:
        """整组拖动松手：全部退回原位，一条 move_clips 命令写入（一次撤销）。"""
        rows = self._clip_group_orig
        self._clip_group_orig = None
        if not rows:
            return False
        final = [(t, c, float(getattr(c, "start_t", 0.0))) for t, c, _s in rows]
        final.append((primary_track, primary_clip, float(getattr(primary_clip, "start_t", 0.0))))
        origs = [(t, c, s) for t, c, s in rows] + [(primary_track, primary_clip, float(primary_orig_start))]
        if all(abs(f[2] - o[2]) < 1e-6 for f, o in zip(final, origs)):
            for t, c, s in origs:
                c.start_t = s
            return True
        for t, c, s in origs:
            c.start_t = s
        items = []
        for t, c, s in final:
            bag = list(getattr(t, "clips", []) or [])
            if c in bag:
                items.append({"track_id": str(getattr(t, "id", "")), "clip_index": bag.index(c), "start": s})
        if items:
            self._bus_execute("timeline.move_clips", {"clips": items})
        return True
