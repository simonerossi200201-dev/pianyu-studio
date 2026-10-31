# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Command-bus body methods mixed into MainWindow. No dialogs."""
from __future__ import annotations

import copy
import os

from cutora.core.commands import CommandError


def _tl(task):
    if not isinstance(task, dict):
        return None
    return task.get("timeline")


def _idx(win, media_id):
    if media_id is None:
        idx = getattr(win, "curr_idx", -1)
    else:
        idx = int(media_id)
    tasks = getattr(win, "tasks", None) or []
    if idx < 0 or idx >= len(tasks):
        raise CommandError("没有这条媒体（id=%s）" % media_id)
    return idx, tasks[idx]


def _seg_i(task, seg_id) -> int:
    from cutora.subtitle.seg_ids import ensure_segment_ids, resolve_seg_index
    ensure_segment_ids(task)
    try:
        return resolve_seg_index(task, seg_id)
    except ValueError as exc:
        raise CommandError(str(exc)) from exc


class CommandBodies:
    """Body half of D2 splits. UI wrappers live on MainWindow and call the bus."""

    def command_set_theme(self, theme_id):
        from cutora.ui.theme.apply import apply_theme
        apply_theme(str(theme_id or "roma-gold"))
        return {"theme_id": str(theme_id or "roma-gold")}

    def command_set_ui_mode(self, mode):
        self.set_ui_mode("pro")
        return {"mode": "pro"}

    def command_project_new(self):
        from cutora.model.canvas import default_new_canvas_wh
        from cutora.model.timeline import DEFAULT_TIMELINE_FPS
        w, h = default_new_canvas_wh()
        self._timeline_template = {
            "width": int(w), "height": int(h), "fps": DEFAULT_TIMELINE_FPS,
        }
        tpl = self._ensure_timeline_template()
        if not tpl:
            raise CommandError("无法确定画布模板")
        self._reload_default_subtitle_style()
        self.tasks = []
        self.file_list.clear()
        self.curr_idx = -1
        self._task_data = None
        self.media_player.stop()
        self.text_input.blockSignals(True)
        self.text_input.setText("")
        self.text_input.blockSignals(False)
        self.timeline.set_data(0, None)
        self.sub_panel.load_data(self.default_style)
        self.prop_panel.load_data(None, None)
        self.project_path = None
        self.project_glossary = []
        self.update_ui_state()
        if hasattr(self, "show_editor"):
            self.show_editor()
        return {"ok": True}

    def command_project_open(self, path):
        import json
        from cutora.core.brand import format_window_title
        from cutora.core.history import _record_recent_project
        from cutora.media.probe_cache import (
            collect_project_media_paths, prefetch_paths, yielding_to_ui,
        )
        from cutora.model.project_io import (
            PROJECT_VERSION, PROJECT_VERSION_MIN, _normalize_project_data,
        )
        if not path or not os.path.isfile(path):
            raise CommandError("找不到工程文件: %s" % path)
        t_deb = getattr(self, "_autosave_debounce", None)
        if t_deb is not None:
            t_deb.stop()
        restored_data = None
        if hasattr(self, "_offer_autosave_restore"):
            try:
                restored_data = self._offer_autosave_restore(path, before_open=True)
            except Exception:
                restored_data = None
        begin = getattr(self, "_begin_open_progress", None)
        if callable(begin):
            begin()
        frozen = []
        try:
            with yielding_to_ui():
                # Freeze leftover timeline paint so a previous project's media
                # is not probed (or decoded) while this one opens.
                tl = getattr(self, "timeline", None)
                hdr = getattr(getattr(self, "timeline_container", None), "track_header", None)
                for w in (tl, hdr):
                    if w is not None and hasattr(w, "setUpdatesEnabled"):
                        w.setUpdatesEnabled(False)
                        frozen.append(w)
                if hasattr(self, "show_editor"):
                    self.show_editor()
                tick = getattr(self, "_tick_open_progress", None)
                if callable(tick):
                    tick(8, "正在打开工程…")
                if isinstance(restored_data, dict):
                    data = restored_data
                else:
                    with open(path, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                file_ver = data.get("version", 1)
                if not (PROJECT_VERSION_MIN <= file_ver <= PROJECT_VERSION):
                    raise CommandError("项目版本 v%s 不受支持" % file_ver)
                self.project_path = path
                if callable(tick):
                    tick(10, "正在打开工程…")
                try:
                    from cutora.model.project_media import resolve_relative_paths
                    resolve_relative_paths(data.get("tasks") or [], path)
                except Exception:
                    pass
                media = collect_project_media_paths(data)
                n_media = len(media)

                def _on_probe(done, total, _tick=tick):
                    if not callable(_tick) or not total:
                        return
                    pct = 10 + int(35 * int(done) / int(total))
                    _tick(pct, "探测媒体 %d/%d" % (int(done), int(total)))

                if n_media and callable(tick):
                    tick(10, "探测媒体 0/%d" % n_media)
                prefetch_paths(media, on_progress=_on_probe)
                _normalize_project_data(data)
                if callable(tick):
                    tick(50, "正在打开工程…")
                if hasattr(self, "_announce_text_layer_migrate"):
                    self._announce_text_layer_migrate(data)
                self.apply_project_data(data)
                if isinstance(restored_data, dict):
                    self._persist_restored_project(path)
                lib = getattr(self, "asset_library", None)
                if lib is not None and hasattr(lib, "_on_cat"):
                    try:
                        lib._on_cat(lib.tabs.currentIndex())
                    except Exception:
                        pass
                from cutora.subtitle.track_source import log_subtitle_source
                tasks = getattr(self, "tasks", None) or []
                idx = int(getattr(self, "curr_idx", -1) or -1)
                if 0 <= idx < len(tasks):
                    log_subtitle_source("project.open", tasks[idx])
                if hasattr(self, "_prompt_canvas_if_sources_mismatch"):
                    self._prompt_canvas_if_sources_mismatch()
                _record_recent_project(path)
                self.setWindowTitle(format_window_title(self.project_path))
                return {"path": os.path.abspath(path), "restored": isinstance(restored_data, dict)}
        finally:
            for w in frozen:
                try:
                    w.setUpdatesEnabled(True)
                except RuntimeError:
                    pass
            end = getattr(self, "_end_open_progress", None)
            if callable(end):
                end()

    def command_project_save(self):
        if not self.project_path:
            raise CommandError("当前工程尚未选择保存位置，请使用「另存工程」指定保存路径。")
        return self._save_project_quiet(self.project_path)

    def command_project_save_as(self, path):
        if not path:
            raise CommandError("请选择工程文件的保存路径。")
        from cutora.core.brand import ensure_save_extension
        self.project_path = ensure_save_extension(path)
        return self._save_project_quiet(self.project_path)

    def _save_project_quiet(self, path):
        if not getattr(self, "_timeline_template", None) and hasattr(self, "_ensure_timeline_template"):
            try:
                self._ensure_timeline_template()
            except Exception:
                pass
        from cutora.model.project_media import collect_missing
        missing = collect_missing(getattr(self, "tasks", None) or [])
        if missing:
            # Quiet on purpose: the command path (API / MCP / 队列) must not
            # open a dialog. The window wires _warn_missing_media for UI saves.
            warn = getattr(self, "_warn_missing_media", None)
            if callable(warn):
                warn(missing)
        data = self.collect_project_data()
        try:
            from cutora.model.project_media import relativize_under_project
            relativize_under_project(data.get("tasks") or [], path)
        except Exception:
            pass
        self._write_project_json(path, data)
        from cutora.core.autosave import clear_snapshot, untitled_project_path
        clear_snapshot(path)
        clear_snapshot(untitled_project_path())      # Q154：存成正式工程了，未命名那份快照不用留
        from cutora.core.history import _record_recent_project
        from cutora.core.brand import format_window_title
        _record_recent_project(path)
        self.setWindowTitle(format_window_title(path))
        return {"path": os.path.abspath(path)}

    def command_project_close(self):
        self.command_project_new()
        if hasattr(self, "show_welcome"):
            self.show_welcome()
        return {"ok": True}

    def command_project_info(self):
        tasks = self.tasks or []
        tpl = getattr(self, "_timeline_template", None) or {}
        return {
            "path": self.project_path,
            "count": len(tasks),
            "curr_idx": self.curr_idx,
            "width": tpl.get("width"),
            "height": tpl.get("height"),
            "fps": tpl.get("fps"),
        }

    def command_media_import(self, paths):
        if not paths:
            raise CommandError("paths 不能为空")
        before = len(self.tasks or [])
        if hasattr(self, "show_editor"):
            self.show_editor()
        self.on_drop(list(paths))
        return {"media_ids": list(range(before, len(self.tasks or [])))}

    def command_media_remove(self, ids):
        rows = sorted({int(i) for i in ids}, reverse=True)
        self.media_player.stop()
        self.file_list.blockSignals(True)
        removed = []
        for row in rows:
            if 0 <= row < len(self.tasks):
                del self.tasks[row]
                self.file_list.takeItem(row)
                removed.append(row)
        self.file_list.blockSignals(False)
        self._after_media_removed()
        return {"removed": list(reversed(removed))}

    def _after_media_removed(self):
        if not self.tasks:
            self.curr_idx = -1
            self._task_data = None
            self.text_input.blockSignals(True)
            self.text_input.setText("")
            self.text_input.blockSignals(False)
            self.timeline.set_data(0, None)
            self.sub_panel.load_data(self.default_style)
            self.prop_panel.load_data(None, None)
            self.update_ui_state()
            return
        idx = min(max(self.curr_idx, 0), len(self.tasks) - 1)
        self.file_list.setCurrentRow(idx)
        self.curr_idx = idx
        self.on_click(self.file_list.item(idx))

    def command_media_list(self):
        rows = []
        for i, task in enumerate(self.tasks or []):
            rows.append({
                "id": i,
                "name": os.path.basename(task.get("video") or "") or "未命名",
                "path": task.get("video"),
                "duration": task.get("duration"),
                "segments": len(task.get("segments") or []),
            })
        return {"media": rows}

    def command_media_info(self, media_id):
        idx, task = _idx(self, media_id)
        tl = _tl(task)
        return {
            "id": idx,
            "path": task.get("video"),
            "text_len": len(task.get("text") or ""),
            "segments": len(task.get("segments") or []),
            "width": getattr(tl, "width", None),
            "height": getattr(tl, "height", None),
        }

    def command_media_select(self, ids):
        ids = [int(i) for i in ids]
        if not ids:
            raise CommandError("ids 不能为空")
        self.file_list.clearSelection()
        for i in ids:
            if 0 <= i < self.file_list.count():
                self.file_list.item(i).setSelected(True)
        first = ids[0]
        if 0 <= first < self.file_list.count():
            self.file_list.setCurrentRow(first)
            self.on_click(self.file_list.item(first))
        return {"ids": ids}

    def command_media_relink(self, source_id, new_path):
        from cutora.model.offline import relink_source, suggest_relink
        n = 0
        last_err = None
        for task in self.tasks:
            try:
                n += relink_source(task, str(source_id or ""), str(new_path or ""))
            except ValueError as e:
                last_err = e
        if n == 0:
            hints = []
            proj = os.path.dirname(self.project_path or "") or None
            for task in self.tasks:
                p = task.get("video") or task.get("path")
                hints.extend(suggest_relink(p, proj))
            raise CommandError(
                str(last_err) if last_err else "没有匹配的 source_id。候选: %s" % (hints or "无"))
        return {"relinked": n, "source_id": source_id, "new_path": os.path.abspath(new_path)}

    def command_media_relink_folder(self, old_root, new_root):
        from cutora.model.offline import relink_folder
        try:
            n = relink_folder(self.tasks, old_root, new_root)
        except ValueError as e:
            raise CommandError(str(e)) from e
        return {
            "relinked": n,
            "old_root": os.path.abspath(old_root),
            "new_root": os.path.abspath(new_root),
        }

    def command_subtitle_auto(
        self, media_id=None, language=None, model=None, glossary=None,
        force=False, skip_if_present=False,
    ):
        from cutora.asr.detect_plan import normalize_subtitle_languages
        langs = normalize_subtitle_languages(language)
        if langs:
            from cutora.asr.languages import set_asr_language
            if langs[0] != "auto":
                set_asr_language(langs[0])
            elif len(langs) == 1:
                set_asr_language("auto")
        if glossary is not None:
            from cutora.asr.glossary import parse_terms
            self._run_glossary_extra = parse_terms(glossary)
        from cutora.asr.models import get_model, resolve_asr_model_id, status_row
        if model:
            want = resolve_asr_model_id(model)
            row = get_model(want) if want else None
            if row is None or row.get("kind") != "asr":
                raise CommandError("未知识别档位: %s" % model)
            st = status_row(row)
            if st.get("status") != "ready":
                raise CommandError(
                    "识别模型未就绪，请先 asr.models.download id=%s" % want)
            self._run_asr_id = want
        if skip_if_present and not force:
            if media_id is not None:
                idx, task = _idx(self, media_id)
                if task.get("segments"):
                    return {"skipped": True, "media_id": idx}
            else:
                tasks = list(getattr(self, "tasks", None) or [])
                if tasks and all(t.get("segments") for t in tasks):
                    return {"skipped": True}
        self.batch_queue = []
        if media_id is not None:
            idx, task = _idx(self, media_id)
            if force or (not task.get("segments") and (task.get("text") or "").strip()):
                self.batch_queue.append(idx)
        else:
            self._fill_auto_queue(force=force)
        if not self.batch_queue:
            return {"queued": 0, "message": "没有需要打轴的条目"}
        self._preflight_auto_queue(self.batch_queue)
        if not getattr(self, "_run_asr_id", None):
            asr = get_model("asr-small")
            if asr is not None:
                st = status_row(asr)
                if st.get("status") != "ready":
                    raise CommandError(
                        "识别模型未就绪，请先 asr.models.download id=asr-small")
        if langs and len(langs) > 1:
            for idx in list(self.batch_queue):
                self.tasks[idx]["asr_lang_queue"] = list(langs)
                self.tasks[idx]["asr_language"] = langs[0]
        elif langs and langs[0] != "auto":
            for idx in list(self.batch_queue):
                self.tasks[idx]["asr_language"] = langs[0]
        queued = list(self.batch_queue)
        if media_id is not None and len(queued) == 1:
            idx = queued[0]
            self.curr_idx = idx
            file_list = getattr(self, "file_list", None)
            if file_list is not None and file_list.count() > idx:
                file_list.setCurrentRow(idx)
            self.is_batch_running = False
            self.run_align(is_batch=False)
            return {
                "queued": 1,
                "media_id": idx,
                "language": language,
                "model": model,
            }
        self.is_batch_running = True
        async_prep = getattr(self, "_prepare_batch_detect_async", None)
        if callable(async_prep):
            async_prep(queued)
        else:
            self.process_next_batch()
        return {
            "queued": len(queued),
            "language": language,
            "model": model,
            "detect_pending": True,
        }

    def _preflight_auto_queue(self, indices):
        """Fail fast before language detect / ASR. Product function, not a test stub."""
        from cutora.batch.media_guard import (
            PreflightError,
            preflight_for_auto_subtitle,
        )
        for idx in list(indices or []):
            try:
                idx_i = int(idx)
            except (TypeError, ValueError):
                continue
            if idx_i < 0 or idx_i >= len(self.tasks or []):
                continue
            path = self.tasks[idx_i].get("video") or ""
            try:
                preflight_for_auto_subtitle(path)
            except PreflightError as exc:
                raise CommandError(
                    "自动字幕前体检失败：%s" % exc, code="preflight") from exc

    def _fill_auto_queue(self, force=False):
        unaligned = [i for i, t in enumerate(self.tasks)
                     if not t.get("segments") and (t.get("text") or "").strip()]
        if unaligned and not force:
            self.batch_queue.extend(unaligned)
            return
        if force:
            self.batch_queue.extend(
                i for i, t in enumerate(self.tasks) if (t.get("text") or "").strip())

    def command_subtitle_list(self, media_id=None):
        idx, task = _idx(self, media_id)
        from cutora.subtitle.seg_ids import ensure_segment_ids
        ensure_segment_ids(task)
        segs = []
        for i, seg in enumerate(task.get("segments") or []):
            segs.append({
                "seg_id": seg.get("id") or i,
                "start": seg.get("start"),
                "end": seg.get("end"),
                "text": seg.get("text") or "",
                "role": seg.get("role"),
            })
        return {"media_id": idx, "segments": segs}

    def command_subtitle_set_text(self, seg_id, text, media_id=None):
        idx, task = _idx(self, media_id)
        segs = task.get("segments") or []
        i = _seg_i(task, seg_id)
        segs[i]["text"] = str(text)
        self._refresh_task_view(idx)
        return {"media_id": idx, "seg_id": segs[i].get("id") or i, "text": str(text)}

    def command_subtitle_merge(self, seg_id, media_id=None):
        idx, task = _idx(self, media_id)
        segs = list(task.get("segments") or [])
        i = _seg_i(task, seg_id)
        if i < 0 or i >= len(segs) - 1:
            raise CommandError("无法与下一段合并")
        a, b = segs[i], segs[i + 1]
        a["text"] = (a.get("text") or "") + (b.get("text") or "")
        a["end"] = max(float(a.get("end") or 0), float(b.get("end") or 0))
        words = list(a.get("words") or []) + list(b.get("words") or [])
        if words:
            a["words"] = words
        del segs[i + 1]
        from cutora.subtitle.track_source import set_segments
        set_segments(task, segs)
        self._refresh_task_view(idx)
        return {"media_id": idx, "seg_id": a.get("id") or i, "count": len(segs)}

    def command_subtitle_split(self, seg_id, time=None, media_id=None):
        idx, task = _idx(self, media_id)
        segs = list(task.get("segments") or [])
        i = _seg_i(task, seg_id)
        seg = segs[i]
        start = float(seg.get("start") or 0)
        end = float(seg.get("end") or 0)
        mid = float(time) if time is not None else (start + end) / 2.0
        if mid <= start or mid >= end:
            raise CommandError("拆分点必须落在分段内")
        text = str(seg.get("text") or "")
        cut = max(1, len(text) // 2)
        left = dict(seg)
        right = dict(seg)
        left["end"] = mid
        left["text"] = text[:cut].strip()
        right["start"] = mid
        right["text"] = text[cut:].strip()
        from cutora.subtitle.seg_ids import new_seg_id
        right["id"] = new_seg_id()
        segs[i:i + 1] = [left, right]
        from cutora.subtitle.track_source import set_segments
        set_segments(task, segs)
        self._refresh_task_view(idx)
        return {"media_id": idx, "seg_id": left.get("id") or i, "count": len(segs)}

    def command_subtitle_delete(self, seg_id, media_id=None):
        idx, task = _idx(self, media_id)
        segs = list(task.get("segments") or [])
        i = _seg_i(task, seg_id)
        removed = segs[i].get("id") if isinstance(segs[i], dict) else i
        del segs[i]
        from cutora.subtitle.track_source import set_segments
        set_segments(task, segs)
        self._refresh_task_view(idx)
        return {"media_id": idx, "removed": removed, "count": len(segs)}

    def command_subtitle_apply_preset(self, name, media_id=None):
        return self.command_apply_subtitle_preset(name, media_id=media_id)

    def command_word_appear_set(self, on):
        panel = getattr(self, "sub_panel", None)
        chk = getattr(panel, "chk_word_appear", None) if panel is not None else None
        if chk is not None:
            chk.setChecked(bool(on))
            return {"on": bool(on)}
        idx, task = _idx(self, None)
        style = dict(task.get("style") or {})
        style["anim_in_type"] = "metronome" if on else "none"
        task["style"] = style
        return {"on": bool(on)}

    def command_apply_subtitle_preset(self, name, media_id=None):
        from cutora.subtitle.stylepack import (
            apply_animation_for, apply_pack_to_task, find_pack_by_name,
        )
        pack = find_pack_by_name(name)
        if not pack:
            raise CommandError("没有名为 %s 的字幕预设" % name)
        idx, task = _idx(self, media_id)
        apply_pack_to_task(
            task, pack, apply_animation=apply_animation_for(self, task))
        if idx == self.curr_idx and hasattr(self, "sub_panel"):
            self.sub_panel.load_data(task["style"])
            canvas = getattr(self, "video_canvas", None)
            if canvas is not None:
                canvas.update()
        return {"name": name, "media_id": idx, "path": pack.get("path")}

    def command_apply_text_preset(self, name):
        from cutora.subtitle.text_preset import resolve_preset, template_from_preset
        row = resolve_preset(name)
        if not row:
            raise CommandError("没有名为 %s 的文本预设" % name)
        style = template_from_preset(row)
        return {"name": name, "id": row.get("id"), "style": style}

    def command_apply_keyword_preset(self, name):
        from cutora.subtitle.presets import load_keyword_presets
        data = load_keyword_presets()
        rules = (data.get("presets") or {}).get(name)
        if rules is None:
            raise CommandError("没有名为 %s 的关键词预设" % name)
        idx, task = _idx(self, None)
        task["keyword_rules"] = copy.deepcopy(rules)
        return {"name": name, "media_id": idx}

    def command_save_style_as(self, name):
        from cutora.subtitle.stylepack import StylepackError, save_task_as_pack
        _idx_i, task = _idx(self, None)
        try:
            out = save_task_as_pack(task, name)
        except StylepackError as exc:
            raise CommandError(str(exc)) from exc
        return {"name": out.get("name") or name, "path": out.get("path")}

    def command_timeline_info(self):
        idx, task = _idx(self, None)
        tl = _tl(task)
        tracks = []
        if tl is not None:
            for tr in getattr(tl, "tracks", []) or []:
                tracks.append({
                    "id": getattr(tr, "id", None),
                    "type": getattr(tr, "type", None),
                    "clips": len(getattr(tr, "clips", []) or []),
                })
        return {
            "media_id": idx,
            "width": getattr(tl, "width", None),
            "height": getattr(tl, "height", None),
            "fps": getattr(tl, "fps", None),
            "playhead_ms": int(getattr(self, "_timeline_ms", 0) or 0),
            "tracks": tracks,
        }

    def command_add_track(self, track_type):
        from cutora.model.timeline_model import Track
        allowed = {"video", "text", "image", "audio", "adjust"}
        if track_type not in allowed:
            raise CommandError("不支持的轨道类型 %s" % track_type)
        task = self._ensure_task_for_overlay()
        if task is None:
            raise CommandError("没有可添加轨道的视频")
        from cutora.ui.main_window import _tl_of
        tl = _tl_of(task)
        if tl is None:
            tl = self._build_default_timeline()
            task["timeline"] = tl
        tl.add_track(Track(track_type))
        self.timeline.update()
        if hasattr(self, "timeline_container"):
            self.timeline_container.track_header.update()
        return {"type": track_type}

    def reorder_timeline_track(self, src, dst, after=False):
        """把 src 挪到 dst 前面（after=True：后面）。Q169 轨道头把手拖动按插入线落位。"""
        tl = None
        if self.curr_idx >= 0 and self.curr_idx < len(self.tasks):
            tl = self.tasks[self.curr_idx].get("timeline")
        if tl is None:
            return
        tracks = list(getattr(tl, "tracks", []) or [])
        if src not in tracks or dst not in tracks:
            return
        # 视觉轨 / 音频轨各自一块，不能互相穿插（Q163：视觉轨之间的上下 = 画面里的叠压）
        if (getattr(src, "domain", "visual") == "audio") != (getattr(dst, "domain", "visual") == "audio"):
            return
        si = tracks.index(src)
        di = tracks.index(dst)
        if si == di:
            return
        tracks.pop(si)
        if si < di:
            di -= 1
        if after:
            di += 1
        tracks.insert(di, src)
        if tracks == list(getattr(tl, "tracks", []) or []):
            return                      # 落回原位：什么都没改，不记撤销
        try:
            self.push_undo("调整轨道顺序", scope="task")
        except TypeError:
            self.push_undo("调整轨道顺序")
        except Exception:
            pass
        tl.tracks[:] = tracks
        try:
            self.capture_undo_after()
            self.mark_dirty()
        except Exception:
            pass
        if hasattr(self, "timeline"):
            self.timeline.update()
        hdr = getattr(getattr(self, "timeline_container", None), "track_header", None)
        if hdr is not None:
            hdr.update()
        # 叠压顺序变了，画面马上重画
        present = getattr(self, "_present_preview_frame", None)
        if callable(present):
            try:
                present()
            except Exception:
                pass
        canvas = getattr(self, "video_canvas", None)
        if canvas is not None:
            canvas.update()

    def command_remove_track(self, track_id):
        from cutora.ui.main_window import track_is_empty
        idx, task = _idx(self, None)
        tl = _tl(task)
        if tl is None:
            raise CommandError("当前视频没有时间线")
        track = None
        for tr in tl.tracks:
            if getattr(tr, "id", None) == track_id:
                track = tr
                break
        if track is None:
            raise CommandError("找不到轨道 %s" % track_id)
        if not track_is_empty(track, task):
            raise CommandError("只能删除空轨道")
        tl.remove_track(track)
        self.timeline.update()
        return {"removed": track_id}

    def command_split_at(self, time):
        idx, task = _idx(self, None)
        tl = _tl(task)
        if tl is None:
            raise CommandError("当前视频没有时间线")
        t = float(time)
        from cutora.ui.timeline.snap import clip_span
        for tr in tl.tracks:
            if not getattr(tr, "locked", False):
                continue
            for clip in getattr(tr, "clips", []) or []:
                start, end = clip_span(clip)
                if start < t < end:
                    self._require_unlocked(tr)
        n = 0
        lefts = []
        rights = []
        for tr in tl.tracks:
            clips = list(getattr(tr, "clips", []) or [])
            for clip in clips:
                right = clip.split_at(float(time)) if hasattr(clip, "split_at") else None
                if right is not None:
                    tr.clips.append(right)
                    lefts.append((tr, clip))
                    rights.append((tr, right))
                    n += 1
        if lefts:
            from cutora.model.av_link import rebind_link_groups_after_split
            rebind_link_groups_after_split(tl, t, lefts, rights)
        self.timeline.update()
        return {"split": n, "time": float(time)}

    def command_join_clips(self, track_id, left_index, right_index):
        from cutora.model.seams import JoinError, join_clips
        tr, clips = self._clip_ref(track_id, int(left_index))
        self._clip_ref(track_id, int(right_index))
        idx, task = _idx(self, None)
        tl = _tl(task)
        fps = float(getattr(tl, "fps", 30.0) or 30.0) if tl is not None else 30.0
        try:
            join_clips(tr, int(left_index), int(right_index), fps=fps)
        except JoinError as exc:
            raise CommandError(str(exc)) from exc
        self.timeline.update()
        return {
            "track_id": track_id,
            "left_index": int(left_index),
            "right_index": int(right_index),
        }

    def command_add_transition(self, track_id, start_t, duration, preset, domain="visual"):
        from cutora.model.timeline_model import TransitionItem
        from cutora.render.anim_engine import audio_transition_duration
        idx, task = _idx(self, None)
        tl = _tl(task)
        if tl is None:
            raise CommandError("当前视频没有时间线")
        tr = None
        for cand in tl.tracks:
            if str(getattr(cand, "id", "") or "") == str(track_id or ""):
                tr = cand
                break
        if tr is None:
            raise CommandError("找不到轨道 %s" % track_id)
        self._require_unlocked(tr, track_id)
        domain = "audio" if str(domain or "visual") == "audio" else "visual"
        dur = float(duration)
        if dur <= 0:
            dur = audio_transition_duration(preset) if domain == "audio" else 0.35
        t0 = float(start_t)
        t1 = t0 + dur
        bag = getattr(tr, "transitions", None)
        if bag is None:
            tr.transitions = []
            bag = tr.transitions
        replaced = None
        for existing in list(bag):
            e0 = float(getattr(existing, "start_t", 0.0) or 0.0)
            e1 = e0 + float(getattr(existing, "duration", 0.0) or 0.0)
            same_dom = str(getattr(existing, "domain", "visual") or "visual") == domain
            if same_dom and e0 < t1 and t0 < e1:
                existing.start_t = t0
                existing.duration = dur
                existing.preset = str(preset)
                existing.domain = domain
                replaced = existing
                break
        if replaced is None:
            replaced = TransitionItem(
                start_t=t0, duration=dur, preset=str(preset), domain=domain)
            bag.append(replaced)
        self.timeline.update()
        return {
            "track_id": track_id,
            "start_t": t0,
            "duration": dur,
            "preset": str(preset),
            "domain": domain,
        }

    def command_set_transition(self, track_id, start_t=None, duration=None,
                               transition_id=None, preset=None):
        """Resize/move one TransitionItem. Opposite-edge-stays is applied by the UI
        before this command runs; here we just write the final start/duration.
        Min duration is one frame. Dragging past source handles is allowed
        (freeze-frame covers the gap).
        """
        idx, task = _idx(self, None)
        tl = _tl(task)
        if tl is None:
            raise CommandError("当前视频没有时间线")
        tr = None
        for cand in tl.tracks:
            if str(getattr(cand, "id", "") or "") == str(track_id or ""):
                tr = cand
                break
        if tr is None:
            raise CommandError("找不到轨道 %s" % track_id)
        self._require_unlocked(tr, track_id)
        bag = list(getattr(tr, "transitions", []) or [])
        found = None
        tid = str(transition_id or "")
        if tid:
            for item in bag:
                if str(getattr(item, "id", "") or "") == tid:
                    found = item
                    break
        if found is None and start_t is not None:
            want = float(start_t)
            for item in bag:
                if abs(float(getattr(item, "start_t", 0.0) or 0.0) - want) < 1e-4:
                    found = item
                    break
        if found is None and bag:
            found = bag[0]
        if found is None:
            raise CommandError("找不到转场")
        fps = float(getattr(tl, "fps", 30.0) or 30.0) if tl is not None else 30.0
        min_dur = 1.0 / max(1.0, fps)
        if duration is not None:
            found.duration = max(min_dur, float(duration))
        if start_t is not None:
            found.start_t = max(0.0, float(start_t))
        if preset is not None:
            found.preset = str(preset)
        if found.duration < min_dur:
            found.duration = min_dur
        self.timeline.update()
        return {
            "track_id": track_id,
            "start_t": float(found.start_t),
            "duration": float(found.duration),
            "preset": str(getattr(found, "preset", "") or ""),
        }

    def command_delete_transition(self, track_id, start_t=None,
                                  transition_id=None, domain=None):
        """Remove one TransitionItem. Undo is the bus snapshot, not an inverse."""
        idx, task = _idx(self, None)
        tl = _tl(task)
        if tl is None:
            raise CommandError("当前视频没有时间线")
        tr = None
        for cand in tl.tracks:
            if str(getattr(cand, "id", "") or "") == str(track_id or ""):
                tr = cand
                break
        if tr is None:
            raise CommandError("找不到轨道 %s" % track_id)
        self._require_unlocked(tr, track_id)
        bag = getattr(tr, "transitions", None)
        if not bag:
            raise CommandError("找不到转场")
        found = None
        tid = str(transition_id or "")
        if tid:
            for item in bag:
                if str(getattr(item, "id", "") or "") == tid:
                    found = item
                    break
        if found is None and start_t is not None:
            want = float(start_t)
            dom = str(domain or "")
            for item in bag:
                if abs(float(getattr(item, "start_t", 0.0) or 0.0) - want) >= 1e-4:
                    continue
                if dom and str(getattr(item, "domain", "visual") or "visual") != dom:
                    continue
                found = item
                break
        if found is None or found not in bag:
            raise CommandError("找不到转场")
        bag.remove(found)
        self.timeline.update()
        return {
            "track_id": str(track_id or ""),
            "transition_id": str(getattr(found, "id", "") or ""),
            "start_t": float(getattr(found, "start_t", 0.0) or 0.0),
            "duration": float(getattr(found, "duration", 0.0) or 0.0),
            "preset": str(getattr(found, "preset", "") or ""),
            "domain": str(getattr(found, "domain", "visual") or "visual"),
        }

    def command_overlay_split(self, kind, idx, t, media_id=None):
        from cutora.core.cmds.light import cmd_overlay_split
        from cutora.core.commands import Context
        return cmd_overlay_split(
            Context(window=self, source="ui"),
            {"kind": kind, "idx": int(idx), "t": float(t), "media_id": media_id},
        )

    def command_timeline_razor(self, time, clips=None, overlays=None, subtitles=None):
        t = float(time)
        n = 0
        lefts = []
        rights = []
        clip_rows = sorted(list(clips or []), key=lambda r: int((r or {}).get("clip_index") or 0), reverse=True)
        for row in clip_rows:
            track_id = str((row or {}).get("track_id") or "")
            ci = int((row or {}).get("clip_index") or 0)
            tr, bag = self._clip_ref(track_id, ci)
            clip = bag[ci]
            right = clip.split_at(t) if hasattr(clip, "split_at") else None
            if right is not None:
                bag.insert(ci + 1, right)
                lefts.append((tr, clip))
                rights.append((tr, right))
                n += 1
        ov_rows = sorted(list(overlays or []), key=lambda r: int((r or {}).get("idx") or 0), reverse=True)
        for row in ov_rows:
            self.command_overlay_split(
                kind=str((row or {}).get("kind") or "image"),
                idx=int((row or {}).get("idx") or 0),
                t=t,
            )
            n += 1
        sub_rows = sorted(list(subtitles or []), key=lambda r: int((r or {}).get("idx") or 0), reverse=True)
        for row in sub_rows:
            self.command_subtitle_split(
                seg_id=int((row or {}).get("idx") or 0),
                time=t,
            )
            n += 1
        if lefts:
            from cutora.model.av_link import rebind_link_groups_after_split
            idx, task = _idx(self, None)
            tl = _tl(task)
            if tl is not None:
                rebind_link_groups_after_split(tl, t, lefts, rights)
        self.timeline.update()
        return {"split": n, "time": t}

    def command_delete_clip(self, track_id, clip_index):
        tr, clips = self._clip_ref(track_id, clip_index)
        del clips[clip_index]
        self.timeline.update()
        return {"track_id": track_id, "clip_index": clip_index}

    def command_move_clip(self, track_id, clip_index, start):
        tr, clips = self._clip_ref(track_id, clip_index)
        clips[clip_index].start_t = float(start)
        self.timeline.update()
        return {"track_id": track_id, "clip_index": clip_index, "start": float(start)}

    def command_trim_clip(self, track_id, clip_index, in_t, out_t, start_t, speed=None):
        tr, clips = self._clip_ref(track_id, clip_index)
        clip = clips[clip_index]
        clip.in_t = float(in_t)
        clip.out_t = float(out_t)
        clip.start_t = float(start_t)
        if speed is not None:
            clip.speed = max(0.1, min(10.0, float(speed)))
            if str(getattr(tr, "type", "") or "") == "video":
                from cutora.model.av_link import sync_linked_constant_speed
                _i, task = _idx(self, None)
                sync_linked_constant_speed(_tl(task), clip)
        self.timeline.update()
        return {
            "track_id": track_id, "clip_index": clip_index,
            "in_t": float(clip.in_t), "out_t": float(clip.out_t),
            "start_t": float(clip.start_t), "speed": float(clip.speed),
        }

    def command_trim_clips(self, clips):
        rows = clips if isinstance(clips, list) else []
        if not rows:
            raise CommandError("缺少 clips")
        trimmed = []
        for row in rows:
            if not isinstance(row, dict):
                raise CommandError("clips 项必须是对象")
            tid = str(row.get("track_id") or "")
            idx = int(row["clip_index"])
            tr, clist = self._clip_ref(tid, idx)
            clip = clist[idx]
            clip.in_t = float(row["in_t"])
            clip.out_t = float(row["out_t"])
            clip.start_t = float(row["start_t"])
            if row.get("speed") is not None:
                clip.speed = max(0.1, min(10.0, float(row["speed"])))
                if str(getattr(tr, "type", "") or "") == "video":
                    from cutora.model.av_link import sync_linked_constant_speed
                    _i, task = _idx(self, None)
                    sync_linked_constant_speed(_tl(task), clip)
            trimmed.append({
                "track_id": tid, "clip_index": idx,
                "in_t": float(clip.in_t), "out_t": float(clip.out_t),
                "start_t": float(clip.start_t), "speed": float(clip.speed),
            })
        self.timeline.update()
        return {"trimmed": trimmed}

    def command_unlink_clips(self, clips):
        rows = clips if isinstance(clips, list) else []
        if not rows:
            raise CommandError("缺少 clips")
        cleared = []
        for row in rows:
            if not isinstance(row, dict):
                raise CommandError("clips 项必须是对象")
            tid = str(row.get("track_id") or "")
            idx = int(row["clip_index"])
            tr, clist = self._clip_ref(tid, idx)
            clist[idx].link_group_id = None
            cleared.append({"track_id": tid, "clip_index": idx})
        self.timeline.update()
        return {"unlinked": cleared}

    def command_link_clips(self, clips):
        import uuid as _uuid
        rows = clips if isinstance(clips, list) else []
        if len(rows) < 2:
            raise CommandError("重新绑定需要至少两条片段")
        gid = _uuid.uuid4().hex
        linked = []
        for row in rows:
            if not isinstance(row, dict):
                raise CommandError("clips 项必须是对象")
            tid = str(row.get("track_id") or "")
            idx = int(row["clip_index"])
            tr, clist = self._clip_ref(tid, idx)
            clist[idx].link_group_id = gid
            linked.append({"track_id": tid, "clip_index": idx})
        self.timeline.update()
        return {"linked": linked, "link_group_id": gid}

    def command_set_clip_fades(self, track_id, clip_index, fade_in_dur=None, fade_out_dur=None):
        tr, clips = self._clip_ref(track_id, clip_index)
        clip = clips[clip_index]
        if fade_in_dur is not None:
            clip.fade_in_dur = max(0.0, float(fade_in_dur))
        if fade_out_dur is not None:
            clip.fade_out_dur = max(0.0, float(fade_out_dur))
        self.timeline.update()
        return {
            "track_id": track_id, "clip_index": clip_index,
            "fade_in_dur": float(getattr(clip, "fade_in_dur", 0.0) or 0.0),
            "fade_out_dur": float(getattr(clip, "fade_out_dur", 0.0) or 0.0),
        }

    def command_move_clips(self, clips):
        rows = clips if isinstance(clips, list) else []
        if not rows:
            raise CommandError("缺少 clips")
        moved = []
        for row in rows:
            if not isinstance(row, dict):
                raise CommandError("clips 项必须是对象")
            tid = str(row.get("track_id") or "")
            idx = int(row["clip_index"])
            start = float(row["start"])
            tr, clist = self._clip_ref(tid, idx)
            clist[idx].start_t = start
            moved.append({"track_id": tid, "clip_index": idx, "start": start})
        self.timeline.update()
        return {"moved": moved}

    def command_duplicate_clips(self, clips=None, items=None, time=0.0):
        import uuid as _uuid
        from cutora.model.timeline_model import Clip
        from cutora.model.project_io import _RJ_CLIP_KEYS
        idx, task = _idx(self, None)
        tl = _tl(task)
        if tl is None:
            raise CommandError("当前视频没有时间线")
        payload = list(items or [])
        if not payload:
            for row in clips or []:
                if not isinstance(row, dict):
                    continue
                tr, clist = self._clip_ref(
                    str(row.get("track_id") or ""), int(row["clip_index"]))
                src = clist[int(row["clip_index"])]
                data = {
                    "track_id": getattr(tr, "id", None),
                    "source_id": getattr(src, "source_id", None),
                    "in_t": float(getattr(src, "in_t", 0.0)),
                    "out_t": float(getattr(src, "out_t", 0.0)),
                    "start_t": float(getattr(src, "start_t", 0.0)),
                }
                for k in _RJ_CLIP_KEYS:
                    if hasattr(src, k):
                        data[k] = getattr(src, k)
                payload.append(data)
        if not payload:
            raise CommandError("没有可复制的片段")
        t0 = min(float(p.get("start_t") or 0.0) for p in payload)
        delta = float(time) - t0
        added = []
        gid_map = {}
        for row in payload:
            dest_id = str(row.get("track_id") or "")
            dest = None
            for tr in getattr(tl, "tracks", []) or []:
                if str(getattr(tr, "id", "") or "") == dest_id:
                    dest = tr
                    break
            if dest is None:
                raise CommandError("找不到轨道 %s" % dest_id)
            self._require_unlocked(dest, dest_id)
            clip = Clip(
                row.get("source_id"),
                float(row.get("in_t") or 0.0),
                float(row.get("out_t") or 0.0),
                max(0.0, float(row.get("start_t") or 0.0) + delta),
            )
            for k in _RJ_CLIP_KEYS:
                if k in row and k not in ("uid", "group"):     # Q154：副本是新片段，不带进原来的组
                    if k == "link_group_id":
                        continue
                    setattr(clip, k, row.get(k))
            old_gid = row.get("link_group_id")
            if old_gid:
                key = str(old_gid)
                if key not in gid_map:
                    gid_map[key] = _uuid.uuid4().hex
                clip.link_group_id = gid_map[key]
            dest.clips.append(clip)
            added.append({"track_id": dest_id, "clip_index": len(dest.clips) - 1})
        self.timeline.update()
        return {"added": added}

    def command_move_clip_to_track(self, track_id, clip_index, dest_track_id, start):
        src, clips = self._clip_ref(track_id, clip_index)
        idx, task = _idx(self, None)
        tl = _tl(task)
        dest = None
        want_dest = str(dest_track_id or "")
        for tr in getattr(tl, "tracks", []) or []:
            if str(getattr(tr, "id", "") or "") == want_dest:
                dest = tr
                break
        if dest is None:
            raise CommandError("找不到目标轨道 %s" % dest_track_id)
        self._require_unlocked(dest, dest_track_id)
        if getattr(src, "type", None) != getattr(dest, "type", None):
            raise CommandError("不能跨类型换轨")
        clip = clips.pop(int(clip_index))
        clip.start_t = float(start)
        if getattr(dest, "type", None) in ("audio", "bgm", "sfx") or getattr(dest, "domain", None) == "audio":
            dest_role = str(getattr(dest, "audio_role", "") or "")
            if dest_role:
                clip.audio_role = dest_role
                clip.is_main = bool(dest_role == "main")
                if dest_role != "main":
                    clip.spatial_enabled = False
        dest.clips.append(clip)
        self.timeline.update()
        return {
            "track_id": track_id, "clip_index": clip_index,
            "dest_track_id": dest_track_id, "start": float(start),
        }

    def command_ripple_delete(self, targets):
        from cutora.model.ripple import ripple_delete_targets
        from cutora.model.track_guard import (
            assert_ripple_unlocked, assert_type_unlocked,
        )
        idx, task = _idx(self, None)
        try:
            for row in targets or []:
                if not isinstance(row, dict):
                    continue
                if str(row.get("kind") or "clip") == "seg":
                    assert_type_unlocked(task, "subs")
                else:
                    assert_ripple_unlocked(task, track_id=row.get("track_id"))
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        out = ripple_delete_targets(task, targets or [])
        if hasattr(self, "timeline"):
            self.timeline.update()
        return out

    def command_ripple_insert(self, t, duration, track_id=None):
        from cutora.model.ripple import apply_insert
        idx, task = _idx(self, None)
        tid = track_id
        if not tid:
            tr = getattr(getattr(self, "timeline", None), "selected_track", None)
            tid = getattr(tr, "id", None) if tr is not None else None
        if not tid:
            raise CommandError("缺少 track_id")
        from cutora.model.track_guard import assert_ripple_unlocked
        try:
            assert_ripple_unlocked(task, track_id=tid)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        out = apply_insert(task, float(t), float(duration), tid)
        names = [str(n) for n in (out.get("nudged") or []) if n]
        if names:
            win = self
            bar = getattr(win, "statusBar", None)
            if callable(bar):
                bar().showMessage(
                    "插入点已移到『%s』之前" % names[0], 3500)
        if hasattr(self, "timeline"):
            self.timeline.update()
        return out

    def command_close_gaps(self, track_id=None, at_t=None):
        from cutora.model.ripple import close_gaps
        idx, task = _idx(self, None)
        tid = track_id
        if not tid:
            tr = getattr(getattr(self, "timeline", None), "selected_track", None)
            tid = getattr(tr, "id", None) if tr is not None else None
        if not tid:
            raise CommandError("缺少 track_id")
        from cutora.model.track_guard import assert_ripple_unlocked
        try:
            assert_ripple_unlocked(task, track_id=tid)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        try:
            out = close_gaps(task, tid, at_t=at_t)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        if hasattr(self, "timeline"):
            self.timeline.update()
        return out

    def command_ripple_intro(self, in_t, out_t, start_t, intro_duration=None):
        idx, task = _idx(self, None)
        if not isinstance(task, dict):
            raise CommandError("没有可操作的视频")
        state = task.get("_intro_video_state")
        if not isinstance(state, dict) or not state.get("enabled"):
            raise CommandError("当前视频没有开场视频")
        intro_path = state.get("intro_path")
        if not intro_path or not os.path.isfile(intro_path):
            raise CommandError("开场视频文件不存在")
        from cutora.ui.main_window import _find_intro_video_clips
        from cutora.model.ripple import apply_delete_range, apply_insert
        from cutora.ui.timeline.snap import clip_span
        v_tr, v_clip, a_tr, a_clip = _find_intro_video_clips(task)
        if v_clip is None:
            raise CommandError("找不到开场视频片段")
        self._require_unlocked(v_tr, getattr(v_tr, "id", None))
        if a_tr is not None:
            self._require_unlocked(a_tr, getattr(a_tr, "id", None))
        from cutora.model.track_guard import assert_ripple_unlocked
        try:
            assert_ripple_unlocked(task, track_id=getattr(v_tr, "id", None))
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        dur = (float(intro_duration) if intro_duration is not None
               else max(0.01, float(out_t) - float(in_t)))
        _os, old_e = clip_span(v_clip)
        skip = [v_clip]
        v_clip.in_t = float(in_t)
        v_clip.out_t = float(in_t) + dur
        v_clip.start_t = float(start_t)
        if a_clip is not None:
            a_clip.in_t = float(in_t)
            a_clip.out_t = float(in_t) + dur
            a_clip.start_t = float(start_t)
            skip.append(a_clip)
        _ns, new_e = clip_span(v_clip)
        tid = getattr(v_tr, "id", None)
        if tid and new_e < old_e - 1e-9:
            apply_delete_range(task, new_e, old_e, tid, skip_clips=skip)
        elif tid and new_e > old_e + 1e-9:
            apply_insert(task, old_e, new_e - old_e, tid, skip_clips=skip)
        state["effective_duration"] = dur
        sid = getattr(v_clip, "source_id", None)
        if hasattr(self, "_trigger_waveform_generation") and sid:
            self._trigger_waveform_generation(sid, intro_path)
        offset_ms = 0
        if hasattr(self, "_get_video_time_offset_ms"):
            offset_ms = int(self._get_video_time_offset_ms() or 0)
        player = getattr(self, "media_player", None)
        base = int(player.duration() or 0) if player is not None else 0
        total_ms = base + offset_ms if offset_ms > 0 else base
        self.timeline.set_data(total_ms, task)
        if hasattr(self, "sync_ui"):
            self.sync_ui()
        if hasattr(self, "video_canvas"):
            self.video_canvas.update()
        return {
            "ok": True, "in_t": float(in_t), "out_t": float(out_t),
            "start_t": float(start_t), "intro_duration": dur,
        }

    def _clip_ref(self, track_id, clip_index):
        idx, task = _idx(self, None)
        tl = _tl(task)
        if tl is None:
            raise CommandError("当前视频没有时间线")
        want = str(track_id or "")
        for tr in tl.tracks:
            if str(getattr(tr, "id", "") or "") != want:
                continue
            clips = getattr(tr, "clips", []) or []
            if clip_index < 0 or clip_index >= len(clips):
                raise CommandError("clip_index 越界")
            if getattr(tr, "locked", False):
                raise CommandError("轨道已锁定：%s" % (
                    getattr(tr, "type", None) or track_id))
            return tr, clips
        raise CommandError("找不到轨道 %s" % track_id)

    def _require_unlocked(self, tr, track_id=None):
        if tr is None:
            if track_id is not None and str(track_id) != "":
                raise CommandError("找不到轨道 %s" % track_id)
            return
        if getattr(tr, "locked", False):
            raise CommandError("轨道已锁定：%s" % (
                getattr(tr, "type", None) or track_id or getattr(tr, "id", "")))

    def _apply_preview_track_mute(self):
        """Mute preview outputs for persist-mute / same-domain solo. JKL reverse wins.

        Main / BGM / SFX follow clip.audio_role on Track("audio").
        Same live list as export (solo only here).
        """
        jkl_rev = getattr(self, "_jkl_rate", 0) < 0
        tasks = getattr(self, "tasks", None) or []
        idx = getattr(self, "curr_idx", -1)
        task = tasks[idx] if 0 <= idx < len(tasks) else None
        solo_ids = getattr(getattr(self, "timeline", None), "_solo_ids", set()) or set()
        from cutora.audio.sfx_library import clip_audio_role
        from cutora.model.track_guard import compute_audio_tracks, iter_tracks
        present = set()
        for tr in iter_tracks(task):
            if getattr(tr, "domain", "visual") != "audio":
                continue
            for clip in getattr(tr, "clips", []) or []:
                present.add(clip_audio_role(clip))
        live = {clip_audio_role(c) for c in compute_audio_tracks(
            task, solo_ids=solo_ids)}
        audio_mute = "main" in present and "main" not in live
        bgm_mute = "bgm" in present and "bgm" not in live
        sfx_mute = "sfx" in present and "sfx" not in live
        mixer = getattr(self, "_stem_mixer", None)
        stems_on = False
        if mixer is not None:
            try:
                stems_on = bool(mixer.stems_ready())
            except Exception:
                stems_on = False
            try:
                mixer.mute["main"] = bool(audio_mute)
                mixer.mute["bgm"] = bool(bgm_mute)
                mixer.mute["sfx"] = bool(sfx_mute)
            except Exception:
                pass
        proxy_ready = bool(getattr(self, "_proxy_ready", False))
        # 代理 wav 是全混：角色 mute 靠 wav 重建，禁止掐整台扬声器（JKL 倒放除外）
        # stem 就绪且在播 / 批量试听：代理必须静音，角色 mute 是 stem × 0
        try:
            sink_live = (
                stems_on
                and (bool(getattr(self, "_timeline_playing", False))
                     or bool(getattr(self, "_batch_audition", False)))
            )
            if sink_live:
                self.audio_output.setMuted(True)
            else:
                self.audio_output.setMuted(bool(jkl_rev if proxy_ready else (jkl_rev or audio_mute)))
        except Exception:
            pass
        try:
            self.bgm_audio_output.setMuted(bool(bgm_mute) and not proxy_ready)
        except Exception:
            pass
        try:
            fx = getattr(self, "_preview_fx_out", None)
            if fx is not None:
                fx.setMuted(bool(jkl_rev if proxy_ready else (jkl_rev or audio_mute)))
        except Exception:
            pass
        sync = getattr(self, "_sync_sfx_preview", None)
        if callable(sync):
            sync()

    def command_set_track_mute(self, track_id, on):
        from cutora.model.track_guard import find_track
        idx, task = _idx(self, None)
        tr = find_track(task, track_id=track_id)
        if tr is None:
            raise CommandError("找不到轨道 %s" % track_id)
        tr.mute = bool(on)
        self._apply_preview_track_mute()
        if hasattr(self, "timeline"):
            self.timeline.update()
        if hasattr(self, "timeline_container"):
            self.timeline_container.track_header.update()
        if hasattr(self, "video_canvas"):
            self.video_canvas.update()
        return {"track_id": track_id, "mute": bool(on)}

    def command_set_track_solo(self, track_id, on):
        from cutora.model.track_guard import find_track
        idx, task = _idx(self, None)
        tr = find_track(task, track_id=track_id)
        if tr is None:
            raise CommandError("找不到轨道 %s" % track_id)
        tid = str(getattr(tr, "id", "") or track_id)
        tlw = getattr(self, "timeline", None)
        solos = getattr(tlw, "_solo_ids", None) if tlw is not None else None
        if solos is None:
            if tlw is not None:
                tlw._solo_ids = set()
                solos = tlw._solo_ids
            else:
                solos = set()
        if on:
            solos.add(tid)
        else:
            solos.discard(tid)
        self._apply_preview_track_mute()
        if hasattr(self, "timeline"):
            self.timeline.update()
        if hasattr(self, "timeline_container"):
            self.timeline_container.track_header.update()
        if hasattr(self, "video_canvas"):
            self.video_canvas.update()
        return {"track_id": tid, "solo": bool(on)}

    def command_set_track_locked(self, track_id, on):
        from cutora.model.track_guard import find_track
        idx, task = _idx(self, None)
        tr = find_track(task, track_id=track_id)
        if tr is None:
            raise CommandError("找不到轨道 %s" % track_id)
        tr.locked = bool(on)
        if hasattr(self, "timeline"):
            self.timeline.update()
        if hasattr(self, "timeline_container"):
            self.timeline_container.track_header.update()
        return {"track_id": track_id, "locked": bool(on)}

    def command_set_track_height(self, track_id, height, all_tracks=False):
        from cutora.model.timeline_model import clamp_track_height
        from cutora.model.track_guard import find_track, iter_tracks
        _idx_n, task = _idx(self, None)
        h = clamp_track_height(height)
        if all_tracks:
            targets = iter_tracks(task)
            if not targets:
                raise CommandError("没有轨道")
        else:
            tr = find_track(task, track_id=track_id)
            if tr is None:
                raise CommandError("找不到轨道 %s" % track_id)
            targets = [tr]
        for tr in targets:
            tr.height = h
        mark = getattr(self, "mark_dirty", None)
        if callable(mark):
            mark()
        if hasattr(self, "timeline"):
            self.timeline.update()
        container = getattr(self, "timeline_container", None)
        if container is not None:
            if hasattr(container, "update_scroll_range"):
                container.update_scroll_range()
            header = getattr(container, "track_header", None)
            if header is not None:
                header.update()
        return {
            "track_id": track_id,
            "height": h,
            "all": bool(all_tracks),
            "count": len(targets),
        }

    def command_set_track_props(self, track_ids, patch):
        from cutora.core.log import get_logger
        from cutora.render.track_transform import apply_track_props_patch
        idx, task = _idx(self, None)
        try:
            result = apply_track_props_patch(task, track_ids, patch)
        except CommandError:
            get_logger("cutora.ui.command_bodies").exception(
                "track.set_props 失败 track_ids=%s patch=%s", track_ids, patch)
            if hasattr(self, "statusBar") and callable(self.statusBar):
                self.statusBar().showMessage("设置轨道属性失败", 4000)
            raise
        if hasattr(self, "timeline"):
            self.timeline.update()
        if hasattr(self, "timeline_container"):
            self.timeline_container.track_header.update()
        if hasattr(self, "video_canvas"):
            self.video_canvas.update()
        if hasattr(self, "update_preview"):
            self.update_preview()
        panel = getattr(self, "prop_panel", None)
        if panel is not None and getattr(panel, "current_type", None) == "track":
            cur = getattr(panel, "_track_obj", None)
            if cur is not None:
                panel.load_data("track", cur)
        return result

    def _refresh_split_views(self):
        if hasattr(self, "timeline"):
            self.timeline.update()
        if hasattr(self, "timeline_container"):
            self.timeline_container.track_header.update()
        if hasattr(self, "update_preview"):
            self.update_preview()
        elif hasattr(self, "_present_preview_frame"):
            try:
                self._present_preview_frame()
            except Exception:
                pass
        if hasattr(self, "video_canvas"):
            self.video_canvas.update()
        panel = getattr(self, "prop_panel", None)
        if panel is not None and getattr(panel, "current_type", None) == "track":
            cur = getattr(panel, "_track_obj", None)
            if cur is not None:
                panel.load_data("track", cur)

    def command_split_set_layout(self, layout=None, enabled=None, border=None, backdrop=None):
        from cutora.core.log import get_logger
        from cutora.render.split_layout import apply_split_to_task
        idx, task = _idx(self, None)
        try:
            result = apply_split_to_task(
                task, layout=layout, enabled=enabled, border=border, backdrop=backdrop)
        except CommandError:
            get_logger("cutora.ui.command_bodies").exception(
                "split.set_layout 失败 layout=%s enabled=%s", layout, enabled)
            if hasattr(self, "statusBar") and callable(self.statusBar):
                self.statusBar().showMessage("设置分屏布局失败", 4000)
            raise
        self._refresh_split_views()
        return {"layout": result.get("layout"), "enabled": result.get("enabled")}

    def command_split_set_divider(self, divider_id, patch):
        from cutora.core.log import get_logger
        from cutora.render.split_layout import set_divider_on_task
        idx, task = _idx(self, None)
        try:
            result = set_divider_on_task(task, divider_id, patch)
        except CommandError:
            get_logger("cutora.ui.command_bodies").exception(
                "split.set_divider 失败 id=%s patch=%s", divider_id, patch)
            if hasattr(self, "statusBar") and callable(self.statusBar):
                self.statusBar().showMessage("设置分界线失败", 4000)
            raise
        self._refresh_split_views()
        return {"id": divider_id, "patch": dict(patch or {}), "layout": result.get("layout")}

    def command_track_set_cell(self, track_id, cell):
        from cutora.core.log import get_logger
        from cutora.render.split_layout import set_track_cell
        idx, task = _idx(self, None)
        try:
            result = set_track_cell(task, track_id, cell)
        except CommandError:
            get_logger("cutora.ui.command_bodies").exception(
                "track.set_cell 失败 track_id=%s cell=%s", track_id, cell)
            if hasattr(self, "statusBar") and callable(self.statusBar):
                self.statusBar().showMessage("设置分屏格子失败", 4000)
            raise
        self._refresh_split_views()
        return result

    def command_set_playhead(self, time):
        ms = int(float(time) * 1000)
        self.on_seek_request(ms)
        return {"time": float(time), "ms": ms}

    def command_set_in_point(self, t):
        idx, task = _idx(self, None)
        task["in_point"] = float(t)
        if hasattr(self, "timeline"):
            self.timeline.update()
        return {"in_point": float(t)}

    def command_set_out_point(self, t):
        idx, task = _idx(self, None)
        task["out_point"] = float(t)
        if hasattr(self, "timeline"):
            self.timeline.update()
        return {"out_point": float(t)}

    def command_clear_io_points(self, which="both"):
        idx, task = _idx(self, None)
        w = str(which or "both")
        if w in ("in", "both"):
            task.pop("in_point", None)
        if w in ("out", "both"):
            task.pop("out_point", None)
        if hasattr(self, "timeline"):
            self.timeline.update()
        return {"which": w}

    def command_marker_add(self, t, name="", color=None):
        from cutora.model.markers import add_marker
        idx, task = _idx(self, None)
        rec = add_marker(task, float(t), name=name or "", color=color)
        if hasattr(self, "timeline"):
            self.timeline.update()
        return {
            "id": rec["id"], "t": rec["t"],
            "name": rec["name"], "color": rec["color"],
        }

    def command_marker_remove(self, ids):
        from cutora.model.markers import remove_markers
        idx, task = _idx(self, None)
        try:
            removed = remove_markers(task, ids)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        if hasattr(self, "timeline"):
            self.timeline.update()
        return {"removed": removed}

    def command_marker_rename(self, mid, name):
        from cutora.model.markers import rename_marker
        idx, task = _idx(self, None)
        try:
            rec = rename_marker(task, mid, name)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        if hasattr(self, "timeline"):
            self.timeline.update()
        return {"id": rec["id"], "name": rec["name"]}

    def command_marker_move(self, mid, t):
        from cutora.model.markers import move_marker
        idx, task = _idx(self, None)
        try:
            rec = move_marker(task, mid, float(t))
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        if hasattr(self, "timeline"):
            self.timeline.update()
        return {"id": rec["id"], "t": rec["t"]}

    def command_marker_set_color(self, mid, color):
        from cutora.model.markers import set_marker_color
        idx, task = _idx(self, None)
        try:
            rec = set_marker_color(task, mid, color)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        if hasattr(self, "timeline"):
            self.timeline.update()
        return {"id": rec["id"], "color": rec["color"]}

    def command_spatial_set(self, preset=None, wet=None, width=None, apply_all=False):
        try:
            from cutora.audio import sfx_library as sfx
        except Exception as exc:
            raise CommandError("音效模块未加载") from exc
        if preset is not None or width is not None:
            fx = sfx.load_fx_settings()
            if preset is not None:
                fx["spatial_preset"] = str(preset)
            if width is not None:
                fx["spatial_width"] = float(width)
            sfx.save_fx_settings(fx)
        indices = list(range(len(self.tasks))) if apply_all else [self.curr_idx]
        n = 0
        for i in indices:
            if i < 0 or i >= len(self.tasks):
                continue
            n += self._spatial_on_task(sfx, self.tasks[i], preset, wet, width)
        return {"updated": n}

    def _spatial_on_task(self, sfx, task, preset, wet, width):
        n = 0
        if not isinstance(task, dict):
            return 0
        if preset is not None or width is not None:
            tfx = task.get("audio_fx") if isinstance(task.get("audio_fx"), dict) else {}
            tfx = dict(tfx)
            if preset is not None:
                tfx["spatial_preset"] = str(preset)
            if width is not None:
                tfx["spatial_width"] = float(width)
            task["audio_fx"] = tfx
        tl = _tl(task)
        if tl is None:
            return n
        for tr in getattr(tl, "tracks", []) or []:
            if getattr(tr, "type", None) != "audio":
                continue
            for clip in getattr(tr, "clips", []) or []:
                if not (getattr(clip, "is_main", False) or getattr(clip, "audio_role", "") == "main"):
                    continue
                if wet is not None:
                    clip.spatial_amount = float(wet)
                    clip.spatial_enabled = float(wet) > 0
                n += 1
        return n

    def command_sfx_apply(self, current_only=True):
        try:
            from cutora.audio import sfx_library as sfx
        except Exception as exc:
            raise CommandError("音效模块未加载") from exc
        if not self.tasks:
            raise CommandError("请先导入视频")
        self._ensure_sfx_library()
        fx = sfx.load_fx_settings()
        indices = [self.curr_idx] if current_only else list(range(len(self.tasks)))
        placed = 0
        for i in indices:
            if i < 0 or i >= len(self.tasks):
                continue
            sfx.apply_spatial_defaults_to_task(self.tasks[i], fx)
            r = sfx.place_sfx_on_task(self.tasks[i], fx)
            placed += int((r or {}).get("placed") or 0)
        try:
            self.timeline.update()
            self.sync_ui()
        except Exception:
            pass
        return {"placed": placed, "tasks": len(indices)}

    def command_volume_master(self, src=None, bgm=None, sfx=None, scope="all"):
        if src is not None and hasattr(self, "spin_src_vol"):
            self.spin_src_vol.setValue(float(src))
        if bgm is not None and hasattr(self, "spin_bgm_vol"):
            self.spin_bgm_vol.setValue(float(bgm))
        if sfx is not None and hasattr(self, "spin_sfx_vol"):
            self.spin_sfx_vol.setValue(float(sfx))
        self.batch_apply_volume(scope=scope)
        return {"scope": scope}

    def command_audio_denoise(self, preset="off", strength="mid", scope="current",
                              track_id=None, clip_index=None):
        from cutora.audio.voice_presets import assign_denoise
        scope = str(scope or "current")
        tasks = getattr(self, "tasks", None) or []
        if scope == "all":
            indices = list(range(len(tasks)))
        else:
            indices = [int(getattr(self, "curr_idx", -1))]
        one = scope == "clip"
        n = 0
        for i in indices:
            if i < 0 or i >= len(tasks):
                continue
            n += assign_denoise(
                tasks[i], preset, strength,
                track_id=track_id if one else None,
                clip_index=clip_index if one else None,
            )
        try:
            self.timeline.update()
        except Exception:
            pass
        rebuild = getattr(self, "_rebuild_stems_now", None)
        if callable(rebuild):
            try:
                rebuild()
            except Exception:
                pass
        return {"updated": n}

    def _silence_indices(self, scope):
        tasks = getattr(self, "tasks", None) or []
        if str(scope or "current") == "all":
            return list(range(len(tasks)))
        return [int(getattr(self, "curr_idx", -1))]

    def command_detect_silence(self, scope="current"):
        from cutora.audio.silence import detect_silences
        tasks = getattr(self, "tasks", None) or []
        found = 0
        for i in self._silence_indices(scope):
            if i < 0 or i >= len(tasks) or not isinstance(tasks[i], dict):
                continue
            rows = detect_silences(tasks[i])
            analysis = tasks[i].get("analysis")
            analysis = dict(analysis) if isinstance(analysis, dict) else {}
            analysis["silences"] = rows
            tasks[i]["analysis"] = analysis
            found += len(rows)
        try:
            self.timeline.update()
        except Exception:
            pass
        return {"found": found}

    def command_remove_silences(self, min_dur=0.8, scope="current", start=None, end=None):
        from cutora.audio.silence import remove_silences
        tasks = getattr(self, "tasks", None) or []
        removed = 0
        for i in self._silence_indices(scope):
            if i < 0 or i >= len(tasks) or not isinstance(tasks[i], dict):
                continue
            removed += remove_silences(
                tasks[i], min_dur=min_dur, start=start, end=end)
        try:
            self.timeline.update()
        except Exception:
            pass
        return {"removed": removed}

    def command_duck_set(self, enabled=False, depth_db=None, release_ms=None, scope="current"):
        tasks = getattr(self, "tasks", None) or []
        n = 0
        for i in self._silence_indices(scope):
            if i < 0 or i >= len(tasks) or not isinstance(tasks[i], dict):
                continue
            cur = tasks[i].get("audio_duck")
            cur = cur if isinstance(cur, dict) else {}
            depth = -12 if depth_db is None else depth_db
            release = 250 if release_ms is None else release_ms
            if depth_db is None and "depth_db" in cur:
                depth = cur.get("depth_db")
            if release_ms is None and "release_ms" in cur:
                release = cur.get("release_ms")
            tasks[i]["audio_duck"] = {
                "enabled": bool(enabled),
                "depth_db": float(depth),
                "release_ms": int(release),
            }
            n += 1
        try:
            from cutora.audio.stem_cache import get_stem_mixer
            mixer = get_stem_mixer()
            raw = None
            idx = int(getattr(self, "curr_idx", -1))
            if 0 <= idx < len(tasks) and isinstance(tasks[idx], dict):
                raw = tasks[idx].get("audio_duck")
            mixer.duck = raw if isinstance(raw, dict) and raw.get("enabled") else None
        except Exception:
            pass
        try:
            self.timeline.update()
        except Exception:
            pass
        return {"updated": n}

    def command_auto_fades(self, dur=0.03, scope="current"):
        from cutora.audio.ducking import apply_auto_fades
        tasks = getattr(self, "tasks", None) or []
        n = 0
        for i in self._silence_indices(scope):
            if i < 0 or i >= len(tasks) or not isinstance(tasks[i], dict):
                continue
            n += apply_auto_fades(tasks[i], dur=dur)
        try:
            self.timeline.update()
        except Exception:
            pass
        return {"updated": n}

    def command_audio_isolate(self, mode="vocals", scope="current",
                              track_id=None, clip_index=None):
        """Queue a separate process. This method does not import the model."""
        import subprocess
        import sys
        from cutora.audio.stem_worker import build_isolate_jobs, plan_isolate
        from cutora.core.proc import popen as _no_window_popen
        from cutora.core.commands import CommandError
        exporting = bool(
            getattr(self, "is_exporting", False)
            or getattr(self, "is_batch_exporting", False)
        )
        tasks = getattr(self, "tasks", None) or []
        indices = [
            i for i in self._silence_indices(scope)
            if 0 <= i < len(tasks) and isinstance(tasks[i], dict)
        ]
        if not indices:
            raise CommandError("没有当前任务")
        planned = plan_isolate(None, exporting=exporting, mode=mode)
        if not planned.get("ok"):
            raise CommandError(planned.get("reason") or "人声分离没有开始")
        from cutora.core.paths import REPO_ROOT, resolve_user_data
        from cutora.ui.gpu_notice import confirm, declined, has_gpu
        cache = os.path.join(resolve_user_data(), "cache", "stems")
        device = "" if has_gpu() else "cpu"
        raw_index = clip_index
        one = None if raw_index is None or raw_index == "" else int(raw_index)
        jobs = []
        for i in indices:
            jobs.extend(build_isolate_jobs(
                tasks[i], planned.get("mode") or mode, planned.get("weights"),
                cache, sys.executable,
                track_id=track_id, clip_index=one, device=device,
            ))
        if not jobs:
            raise CommandError("没有可分离的原声")
        if declined("stem_split"):
            return {"ok": False, "reason": "已取消", "queued": 0}
        if not confirm(self, "stem_split"):
            return {"ok": False, "reason": "已取消", "queued": 0}
        kwargs = {
            "cwd": REPO_ROOT,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        pending = list(getattr(self, "_isolate_jobs", []) or [])
        for job in jobs:
            job["proc"] = _no_window_popen(job["argv"], **kwargs)
            pending.append(job)
        self._isolate_jobs = pending
        self._arm_isolate_poll()
        try:
            from cutora.core.log import get_logger
            get_logger("cutora.ui.command_bodies").info(
                "[Audio] isolate queued %s", len(jobs))
        except Exception:
            pass
        return {"ok": True, "queued": len(jobs), "mode": planned.get("mode")}

    def _arm_isolate_poll(self):
        try:
            from PySide6.QtCore import QTimer
        except Exception:
            return
        timer = getattr(self, "_isolate_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setInterval(800)
            timer.timeout.connect(self._poll_isolate_jobs)
            self._isolate_timer = timer
        if not timer.isActive():
            timer.start()

    def _poll_isolate_jobs(self):
        from cutora.audio.stem_worker import apply_sidecar
        pending = []
        changed = False
        for job in list(getattr(self, "_isolate_jobs", []) or []):
            proc = job.get("proc")
            if proc is None or proc.poll() is None:
                pending.append(job)
                continue
            if proc.returncode == 0 and apply_sidecar(job.get("clip"), job.get("mode"), job.get("dest")):
                changed = True
            else:
                try:
                    from cutora.core.log import get_logger
                    get_logger("cutora.ui.command_bodies").info(
                        "[Audio] isolate exit %s", getattr(proc, "returncode", None))
                except Exception:
                    pass
        self._isolate_jobs = pending
        if not pending:
            timer = getattr(self, "_isolate_timer", None)
            if timer is not None:
                timer.stop()
        if not changed:
            return
        try:
            self.timeline.update()
        except Exception:
            pass
        rebuild = getattr(self, "_rebuild_stems_now", None)
        if callable(rebuild):
            try:
                rebuild()
            except Exception:
                pass

    def command_export_run(self, out=None, out_dir=None, honor_io=False, profile=None, allow_offline=False):
        from cutora.render.export_params import apply_honor_io, resolve_export_run_output
        from cutora.render.export_profile import resolve_profile
        from cutora.model.offline import require_online
        if self.curr_idx < 0:
            raise CommandError("请先选中一条视频")
        require_online([self.tasks[self.curr_idx]], allow_offline=bool(allow_offline))
        task = apply_honor_io(self.tasks[self.curr_idx], honor_io)
        if out_dir:
            task = dict(task)
            task["_export_dest_folder"] = os.path.abspath(out_dir)
        if profile:
            task = dict(task)
            task["_export_profile_id"] = str(profile)
        prof = resolve_profile(explicit=profile, task=task)
        target = resolve_export_run_output(task, out=out, out_dir=out_dir, profile=prof)
        if not target:
            raise CommandError("缺少 out 或 out_dir")
        self.run_export_worker(task, target, is_batch=False)
        return {"out": target, "honor_io": bool(honor_io), "profile": prof.get("id")}

    def command_export_batch(self, project_paths=None, out_dir=None, allow_offline=False):
        from cutora.model.offline import require_online
        require_online(self.tasks, allow_offline=bool(allow_offline))
        dest = (out_dir or "").strip() or None
        if dest:
            os.makedirs(dest, exist_ok=True)
            self._batch_export_out_dir = os.path.abspath(dest)
        else:
            self._batch_export_out_dir = None
        queued = []
        if project_paths:
            from cutora.batch.cli import make_queue
            queued = list(project_paths)
            make_queue(queued, restart_between=True, auto_subtitle=True)
        if hasattr(self, "toggle_batch_export"):
            self.toggle_batch_export()
        return {
            "out_dir": self._batch_export_out_dir,
            "started": True,
            "project_paths": queued,
        }

    def command_export_status(self):
        return {
            "exporting": bool(getattr(self, "is_exporting", False)),
            "batch": bool(getattr(self, "is_batch_exporting", False)),
            "current": getattr(self, "_batch_export_current", 0),
            "total": getattr(self, "_batch_export_total", 0),
        }

    def command_bgm_assign(self, folder, ids=None):
        if not folder or not os.path.isdir(folder):
            raise CommandError("找不到音频文件夹: %s" % folder)
        if ids:
            indices = [int(i) for i in ids]
        else:
            indices = list(range(len(self.tasks or [])))
        self.assign_bgm_for_tasks(indices, folder, mode="batch", push_undo=False)
        return {"folder": folder, "count": len(indices)}

    def command_copy_subtitle_style(self, src_id, target_ids):
        n = self.copy_subtitle_style(int(src_id), list(target_ids), None)
        self.timeline.update()
        self.video_canvas.update()
        return {"applied": n}

    def command_share_tracks(self, src_id, target_ids, track_types=None,
                             track_ids=None, keep_duration_track_ids=None,
                             auto_fit_duration=True):
        """Q140：「自动匹配目标视频时长」以前只在对话框里读了，没有传进命令，
        命令体里写死 True —— 不勾也照样拉伸。现在逐条轨道传过来：
        ``keep_duration_track_ids`` 里的轨道保持源时长，其余按
        ``auto_fit_duration``（默认 True，和以前一样）。``track_ids`` 按轨道
        精确挑（同类型两条轨道只勾一条时，按类型筛会把两条都带上）。"""
        src = self.tasks[int(src_id)]
        src_tl = _tl(src)
        if src_tl is None:
            raise CommandError("源视频没有时间线")
        from cutora.ui.main_window import get_batch_tracks
        batch_tracks = get_batch_tracks(src_tl)
        items = []
        want = set(track_types or [])
        want_ids = {str(x) for x in (track_ids or [])}
        keep = {str(x) for x in (keep_duration_track_ids or [])}
        for tr in batch_tracks:
            tid = str(getattr(tr, "id", "") or "")
            if want_ids:
                if tid not in want_ids:
                    continue
            elif want and getattr(tr, "type", None) not in want:
                continue
            fit = bool(auto_fit_duration) and tid not in keep
            items.append({"track": tr, "auto_fit_duration": fit, "enabled": True})
        if not items:
            raise CommandError("源视频没有可共享的轨道")
        n = self.share_tracks(int(src_id), list(target_ids), {"items": items})
        return {"applied": n}

    def command_project_pack(self, out_dir, mode="copy"):
        from cutora.model.project_pack import pack_tasks
        if not out_dir:
            raise CommandError("缺少 out_dir")
        data = self.collect_project_data() if hasattr(self, "collect_project_data") else {"version": 2, "tasks": list(self.tasks or [])}
        try:
            report = pack_tasks(data.get("tasks") or self.tasks, out_dir, mode=mode)
        except ValueError as e:
            raise CommandError(str(e)) from e
        data["tasks"] = report["tasks"]
        dest = os.path.join(os.path.abspath(out_dir), "project.cutora")
        if hasattr(self, "_write_project_json"):
            self._write_project_json(dest, data)
        else:
            import json
            os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
            with open(dest, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
        return {
            "out_dir": os.path.abspath(out_dir),
            "project": dest,
            "files": report["files"],
            "bytes": report["bytes"],
            "missing": report["missing"],
            "mode": report["mode"],
        }

    def command_autosave_status(self):
        from cutora.core.autosave import status
        return status(self.project_path)

    def _persist_restored_project(self, path=None):
        """恢复自动备份后立即将这份备份写入正式工程文件，并在此基础上建立新会话的自动保存快照。"""
        from cutora.core.autosave import is_untitled, write_snapshot
        t_deb = getattr(self, "_autosave_debounce", None)
        if t_deb is not None:
            t_deb.stop()
        target = path or getattr(self, "project_path", None)
        if not target or is_untitled(target) or not hasattr(self, "collect_project_data"):
            return
        try:
            saved_data = self.collect_project_data()
            try:
                from cutora.model.project_media import relativize_under_project
                relativize_under_project(saved_data.get("tasks") or [], target)
            except Exception:
                pass
            if hasattr(self, "_write_project_json"):
                self._write_project_json(target, saved_data)
            write_snapshot(target, saved_data)
            if hasattr(self, "clear_dirty"):
                self.clear_dirty()
            bar = getattr(self, "statusBar", None)
            if callable(bar):
                bar().showMessage("已恢复自动备份并同步保存为正式工程：%s" % os.path.basename(target), 10000)
        except Exception:
            from cutora.core.log import get_logger
            get_logger("cutora.autosave").exception("恢复自动备份后落盘正式工程失败")

    def command_autosave_restore(self, path=None):
        from cutora.core.autosave import (
            is_untitled, leftover_for, list_backups, project_payload, read_snapshot, untitled_leftover,
        )
        t_deb = getattr(self, "_autosave_debounce", None)
        if t_deb is not None:
            t_deb.stop()
        src = (
            path
            or getattr(self, "_autosave_leftover", None)
            or leftover_for(self.project_path)
            or untitled_leftover()
        )
        if not src or not os.path.isfile(src):
            baks = list_backups(self.project_path)
            src = baks[0] if baks else None
        if not src or not os.path.isfile(src):
            raise CommandError("没有找到可恢复的自动保存备份文件。")
        raw = read_snapshot(src)
        data = project_payload(raw)
        meta = raw.get("_autosave") or {}
        meta_proj = str(meta.get("project") or "").strip()
        if meta_proj and not is_untitled(meta_proj) and not self.project_path:
            self.project_path = meta_proj
        if self.project_path:
            try:
                from cutora.model.project_media import resolve_relative_paths
                resolve_relative_paths(data.get("tasks") or [], self.project_path)
            except Exception:
                pass
        try:
            from cutora.model.project_io import _normalize_project_data
            _normalize_project_data(data)
        except Exception:
            pass
        if not getattr(self, "_timeline_template", None) and hasattr(self, "_ensure_timeline_template"):
            try:
                self._ensure_timeline_template()
            except Exception:
                pass
        if hasattr(self, "show_editor"):
            try:
                self.show_editor()
            except Exception:
                pass
        if hasattr(self, "apply_project_data"):
            self.apply_project_data(data)
        if hasattr(self, "show_editor"):
            try:
                self.show_editor()
            except Exception:
                pass
        self._persist_restored_project(self.project_path)
        try:
            from cutora.core.brand import format_window_title
            if hasattr(self, "setWindowTitle"):
                self.setWindowTitle(format_window_title(self.project_path, dirty=not bool(self.project_path)))
            bar = getattr(self, "statusBar", None)
            if callable(bar):
                n_tasks = len(getattr(self, "tasks", None) or [])
                bar().showMessage(
                    "已恢复自动备份（共 %d 条视频）%s" % (
                        n_tasks,
                        "" if self.project_path else "，请按 Ctrl+S 保存为正式工程文件",
                    ),
                    12000,
                )
        except Exception:
            pass
        return {"path": src, "restored": True, "project": self.project_path}

    def _show_autosave_failure(self, exc):
        from cutora.core.log import get_logger
        from PySide6.QtGui import QColor, QPalette
        msg = "自动保存失败：%s" % exc
        get_logger("cutora.autosave").error(msg)
        bar_fn = getattr(self, "statusBar", None)
        if not callable(bar_fn):
            return
        try:
            bar = bar_fn()
            pal = bar.palette()
            pal.setColor(QPalette.ColorRole.WindowText, QColor("#e53935"))
            bar.setPalette(pal)
            bar.showMessage(msg, 20000)
        except Exception:
            get_logger("cutora.autosave").exception("status bar autosave warning failed")

    def _autosave_now(self, force=False, *, throttle=True):
        """自动保存。force：当场同步写完（关窗 / 退出）。否则：没到节流时间直接返回（不整理数据）；
        到了就在界面线程整理一份快照（深拷贝），编码 + 写盘丢到后台线程（Q159：以前整个过程都在
        每条命令里同步做，大工程每一步卡一两秒）。"""
        from cutora.core import autosave as A
        real = getattr(self, "project_path", None)
        if not hasattr(self, "collect_project_data"):
            return None
        if not real and not (getattr(self, "tasks", None) or []):
            return None                      # 空工程没什么可存的
        path = A.effective_project_path(real)  # Q154：未命名工程也落盘（用户数据目录）
        if not force:
            if (throttle and not A.due()) or getattr(self, "_autosave_inflight", False):
                return None
        try:
            data = self.collect_project_data()
        except Exception as e:
            self._show_autosave_failure(e)
            return None
        if force:
            try:
                out = A.write_snapshot(path, data)
            except Exception as e:
                self._show_autosave_failure(e)
                return None
            if out:
                self._note_autosaved(real)
            return out
        return self._autosave_in_background(path, data, real)

    def _autosave_in_background(self, path, data, real):
        from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
        from cutora.core import autosave as A

        class _Sig(QObject):
            done = Signal(str)
            fail = Signal(object)

        class _Job(QRunnable):
            def __init__(self, sig):
                super().__init__()
                self.sig = sig
                self.setAutoDelete(True)

            def run(self):
                sig_ref = self.sig
                try:
                    out = A.write_snapshot(path, data)
                    if sig_ref is not None:
                        sig_ref.done.emit(str(out or ""))
                except RuntimeError:
                    pass
                except Exception as exc:          # noqa: BLE001
                    try:
                        if sig_ref is not None:
                            sig_ref.fail.emit(exc)
                    except RuntimeError:
                        pass
                finally:
                    self.sig = None

        sig = _Sig()
        self._autosave_sig = sig                  # 别被回收
        keep = getattr(self, "_keep_bg_sig", None)
        drop = getattr(self, "_drop_bg_sig", None)
        if callable(keep):
            keep(sig)
        self._autosave_inflight = True
        A.mark_started()

        def _done(out, _s=sig):
            if callable(drop):
                drop(_s)
            self._autosave_inflight = False
            if out:
                self._note_autosaved(real)

        def _fail(exc, _s=sig):
            if callable(drop):
                drop(_s)
            self._autosave_inflight = False
            try:
                self._show_autosave_failure(exc)
            except Exception:
                pass
        sig.done.connect(_done)
        sig.fail.connect(_fail)
        QThreadPool.globalInstance().start(_Job(sig))
        return path

    def _schedule_autosave(self, delay_ms=1500):
        """命令跑完不当场保存：攒 1.5 秒，一串操作只整理一次，而且落在手停下来的空档里。"""
        from PySide6.QtCore import QTimer
        t = getattr(self, "_autosave_debounce", None)
        if t is None:
            t = QTimer(self if hasattr(self, "startTimer") else None)
            t.setSingleShot(True)
            t.timeout.connect(lambda: self._autosave_now(force=False))
            self._autosave_debounce = t
        t.start(int(delay_ms))

    def _note_autosaved(self, real_path):
        lab = getattr(self, "_status_save", None)
        if lab is None:
            return
        import time as _time
        try:
            lab.setText("已自动保存 %s%s" % (_time.strftime("%H:%M"), "" if real_path else "（未命名工程，Ctrl+S 另存）"))
        except Exception:
            pass

    def _offer_untitled_restore(self):
        """Q154：启动时发现上一次会话留下的「未命名工程」快照（崩了 / 没存就关了）→ 问要不要恢复。"""
        try:
            from cutora.core.autosave import preserve_foreign_snapshot, untitled_leftover
            left = untitled_leftover()
        except Exception:
            return
        if not left or getattr(self, "project_path", None):
            return
        backup = preserve_foreign_snapshot(left)
        self._autosave_leftover = backup or left
        act = getattr(self, "act_autosave_restore", None)
        if act is not None:
            act.setEnabled(True)
        # 启动时保持空白工作区：不再自动弹窗 / 自动载入任何工程，只在状态栏提示一次，
        # 需要时到「文件 → 恢复上次自动保存… / 打开自动保存文件夹」里手动找回。
        try:
            self.statusBar().showMessage(
                "检测到上次未保存的工程快照，已安全备份。如需找回：文件 → 恢复上次自动保存…", 15000)
        except Exception:
            pass
        return

    def _restart_autosave_timer(self):
        from cutora.core.autosave import interval_minutes
        t = getattr(self, "_autosave_timer", None)
        if t is None:
            return
        t.stop()
        t.start(max(1, interval_minutes()) * 60 * 1000)

    def _offer_autosave_restore(self, target_path=None, *, before_open=False):
        """Q118：开机不再弹「上次未正常退出，是否恢复？」。

        Q145/崩溃恢复增强：打开工程前（before_open=True）检测到上次崩溃/未保存快照时，
        先将崩溃快照新增备份（绝不覆盖），再仅弹窗一次询问用户。
        若用户选择恢复，直接返回快照工程数据（不加载旧工程文件），并在打开后立刻保存为正式工程。
        """
        path = target_path or getattr(self, "project_path", None)
        self._autosave_leftover = None
        try:
            from cutora.core.autosave import leftover_for
            self._autosave_leftover = leftover_for(path)
        except Exception:
            from cutora.core.log import get_logger
            get_logger("cutora.autosave").exception("查残留快照失败")
        act = getattr(self, "act_autosave_restore", None)
        if act is not None:
            act.setEnabled(bool(self._autosave_leftover))
        try:
            from cutora.core.autosave import preserve_foreign_snapshot, unsaved_leftover
            left = unsaved_leftover(path)
        except Exception:
            from cutora.core.log import get_logger
            get_logger("cutora.autosave").exception("查未保存的快照失败")
            left = None
        if not left:
            return None
        backup = preserve_foreign_snapshot(left)
        self._autosave_leftover = backup or left
        if act is not None:
            act.setEnabled(True)
        src = str(getattr(self, "_bus_source", None) or "ui")
        if src != "ui" or not self.isVisible():
            try:
                self.statusBar().showMessage(
                    "这个工程有没保存的改动，已备份：%s（文件 → 恢复上次自动保存）" % (backup or left), 15000)
            except Exception:
                pass
            return None
        if before_open:
            return self._ask_restore_unsaved(left, backup, before_open=True)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self._ask_restore_unsaved(left, backup))
        return None

    def _ask_restore_unsaved(self, left, backup=None, untitled=False, *, before_open=False):
        import os
        from datetime import datetime
        from PySide6.QtWidgets import QMessageBox
        if getattr(self, "_autosave_prompt_active", False):
            return None
        target = os.path.normcase(os.path.abspath(str(backup or left or "")))
        prompted = getattr(self, "_autosave_prompted_targets", None)
        if prompted is None:
            prompted = set()
            self._autosave_prompted_targets = prompted
        try:
            mtime_key = int(os.path.getmtime(backup or left))
        except OSError:
            mtime_key = 0
        dedup_key = (target, mtime_key)
        if dedup_key in prompted:
            return None
        prompted.add(dedup_key)
        self._autosave_prompt_active = True
        try:
            try:
                when = datetime.fromtimestamp(os.path.getmtime(backup or left)).strftime("%m-%d %H:%M")
            except OSError:
                when = "?"
            note = ("\n\n不恢复也不会丢：这份崩溃备份已独立另存在\n%s" % backup) if backup else ""
            head = ("上次有一个没保存过的工程留下了自动保存（%s）。" % when) if untitled else (
                "这个工程上次有没保存的改动（自动保存于 %s，比工程文件新）。" % when)
            r = QMessageBox.question(
                self, "有没保存的改动",
                head + "\n要直接以这份自动备份为准恢复并作为正式工程吗？%s" % note,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if r == QMessageBox.StandardButton.Yes:
                if before_open:
                    from cutora.core.autosave import project_payload, read_snapshot
                    return project_payload(read_snapshot(backup or left))
                self.command_autosave_restore(path=backup or left)
        finally:
            self._autosave_prompt_active = False
        return None

    def restore_autosave_from_menu(self):
        """「文件 → 恢复上次自动保存…」：用户主动来找，才问一句。"""
        from PySide6.QtWidgets import QMessageBox
        from cutora.core.autosave import leftover_for
        left = (getattr(self, "_autosave_leftover", None)
                or leftover_for(getattr(self, "project_path", None)))
        if not left:
            QMessageBox.information(self, "自动保存", "没有可恢复的自动保存快照。")
            return
        r = QMessageBox.question(
            self, "自动保存",
            "用这份自动保存的快照覆盖当前工程？\n\n%s" % left,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if r == QMessageBox.StandardButton.Yes:
            self.command_autosave_restore(path=left)

    def _on_command_executed(self, event):
        """UI refresh after a command. Do not touch widgets inside command bodies."""
        name = (event or {}).get("name")
        from cutora.core.autosave import should_skip_command
        if not should_skip_command(name):
            try:
                self._schedule_autosave()
            except Exception:
                pass
        cmd_name = str(name or "")
        if cmd_name.startswith(("timeline.", "audio.", "overlay.", "subtitle.")):
            sync_faders = getattr(self, "_sync_role_faders_from_task", None)
            if callable(sync_faders):
                try:
                    sync_faders()
                except Exception:
                    pass
            apply_mute = getattr(self, "_apply_preview_track_mute", None)
            if callable(apply_mute):
                try:
                    apply_mute()
                except Exception:
                    pass
            rebuild = getattr(self, "_rebuild_preview_wav", None)
            if callable(rebuild):
                imm = cmd_name in (
                    "timeline.delete_clip",
                    "timeline.ripple_delete",
                    "audio.volume_master",
                    "timeline.track_flag",
                )
                rebuild(immediate=imm)
        if str(name or "").startswith(("timeline.", "group.", "clip.", "grade.", "fx.", "keyframe.")):
            # Q154：锁住的预合成组 → 选中那条的改动抄给整组；循环铺满的组重算重复份
            sync = getattr(self, "group_sync", None)
            if callable(sync):
                sel = getattr(getattr(self, "timeline", None), "selected_video_clip", None)
                sync(sel[1] if sel else None)
        if name == "stylepack.apply" and hasattr(self, "sub_panel"):
            idx = getattr(self, "curr_idx", -1)
            tasks = getattr(self, "tasks", None) or []
            if 0 <= idx < len(tasks):
                from cutora.subtitle.stylepack import bind_default_paint_style
                style = bind_default_paint_style(tasks[idx])
                self.sub_panel.load_data(style)
        if name != "stylepack.apply":
            return
        result = (event or {}).get("result") or {}
        pack_name = str(result.get("name") or result.get("id") or "")
        mgr = getattr(self, "history_mgr", None)
        if mgr is not None and getattr(mgr, "history", None):
            idx = getattr(mgr, "current_index", -1)
            if 0 <= idx < len(mgr.history):
                entry = mgr.history[idx]
                if entry.get("command") == "stylepack.apply" and pack_name:
                    from cutora.ui.i18n import tr
                    entry["desc"] = tr("应用风格包 %s") % pack_name
        self._reload_style_from_task()

    def _reload_style_from_task(self):
        from cutora.subtitle.io import window_curr_idx
        idx = window_curr_idx(self)
        tasks = getattr(self, "tasks", None) or []
        if not (0 <= idx < len(tasks)):
            return
        task = tasks[idx]
        style = task.get("style") if isinstance(task, dict) else None
        panel = getattr(self, "sub_panel", None)
        if panel is not None and isinstance(style, dict):
            panel.blockSignals(True)
            try:
                panel.load_data(style)
            finally:
                panel.blockSignals(False)
        pos = float(getattr(self, "_timeline_ms", 0) or 0) / 1000.0
        canvas = getattr(self, "video_canvas", None)
        if canvas is not None:
            canvas.set_data(task, pos)
            canvas.update()
        timeline = getattr(self, "timeline", None)
        if timeline is not None:
            timeline.update()

    def _refresh_task_view(self, idx, force=False):
        if idx == self.curr_idx:
            self.sync_ui()
            self.timeline.update()
            if not getattr(self, "_opening_project", False):
                self.video_canvas.update()
            panel = getattr(self, "transcript_panel", None)
            if panel is not None:
                panel.reload(force=force)
            try:
                from cutora.subtitle.stylepack import diag, style_snapshot
                task = (self.tasks or [None])[idx] if 0 <= idx < len(self.tasks or []) else None
                sub = getattr(self, "sub_panel", None)
                cur = getattr(sub, "current_data", None) if sub is not None else None
                sty = (task or {}).get("style") if isinstance(task, dict) else None
                canvas = getattr(self, "video_canvas", None)
                ctask = getattr(canvas, "_task_data", None) if canvas is not None else None
                diag(
                    "refresh_task_view",
                    idx=idx,
                    curr_idx=self.curr_idx,
                    panel_is_task_style=cur is sty,
                    task_style=style_snapshot(sty, (task or {}).get("layout_rules")),
                    panel_style=style_snapshot(cur),
                    canvas_is_task=ctask is task,
                    canvas_style=style_snapshot(
                        (ctask or {}).get("style") if isinstance(ctask, dict) else None),
                )
            except Exception:
                pass
