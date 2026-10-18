#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T01 one-shot splitter: AutoSub_debug.py top-level nodes → cutora/ modules.

Uses ast.get_source_segment (never ast.unparse) so comments and formatting
stay byte-identical to the monolith. Mapping is the source of truth for
where each symbol went; --dump-map writes the CSV the report attaches.

Usage:
    python tools/split_monolith.py --dump-map
    python tools/split_monolith.py --dry-run
    python tools/split_monolith.py --write   # actually emit modules (step 3)
"""
from __future__ import annotations

import argparse
import ast
import csv
import os
import sys
import textwrap

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONOLITH = os.path.join(REPO_ROOT, "core", "AutoSub_debug.py")

# symbol name → dotted module under cutora (no "cutora." prefix in values
# except we store the path relative to cutora/, posix).
# Classes and functions only; globals are listed in GLOBAL_MAP.
SYMBOL_MAP: dict[str, str] = {
    # --- asr/engine.py ---
    "_LazyModule": "ui/widgets/lazy.py",  # task: lazy.py; also used by asr
    "self_check_dependencies": "app.py",
    "torch": "asr/engine.py",
    "whisperx": "asr/engine.py",
    "get_align_model": "asr/engine.py",
    "get_whisperx_model": "asr/engine.py",
    "_cuda_mem_gb": "asr/engine.py",
    "trim_gpu_cache": "asr/engine.py",
    "release_gpu_models": "asr/engine.py",
    "_sync_device_globals": "asr/engine.py",
    "prepare_task_audio_for_export": "audio/sfx_library.py",
    # --- core ---
    "_get_app_settings_path": "core/history.py",
    "_load_app_settings": "core/history.py",
    "_save_app_settings": "core/history.py",
    "_load_export_video_mode": "core/history.py",
    "_save_export_video_mode": "core/history.py",
    "_default_parallel_workers": "render/parallel.py",
    "_load_parallel_settings": "render/parallel.py",
    "_save_parallel_settings": "render/parallel.py",
    "_get_recent_projects_file": "core/history.py",
    "_record_recent_project": "core/history.py",
    "_get_recent_projects": "core/history.py",
    "HistoryManager": "core/history.py",
    "resource_path": "core/paths.py",
    "get_model_paths": "core/paths.py",
    # --- fonts (ui, not asr) ---
    "load_font_metadata": "ui/panels/fonts.py",
    "save_font_metadata": "ui/panels/fonts.py",
    "_iter_font_files": "ui/panels/fonts.py",
    "scan_fonts_from_dir": "ui/panels/fonts.py",
    "_get_default_font_family": "ui/panels/fonts.py",
    "register_font_combo": "ui/panels/fonts.py",
    "refresh_font_combo": "ui/panels/fonts.py",
    "refresh_all_font_combos": "ui/panels/fonts.py",
    "FontManager": "ui/panels/fonts.py",
    "get_font_manager": "ui/panels/fonts.py",
    "load_embedded_fonts": "ui/panels/fonts.py",
    "sanitize_font_family": "ui/panels/fonts.py",
    "force_allowed_font": "ui/panels/fonts.py",
    "FontManagerDialog": "ui/panels/fonts.py",
    # --- asr/align_worker ---
    "apply_time_offset": "asr/align_worker.py",
    "fix_segment_durations": "asr/align_worker.py",
    "merge_short_segments": "asr/align_worker.py",
    "AlignmentWorker": "asr/align_worker.py",
    # --- model/project_io ---
    "_normalize_project_data": "model/project_io.py",
    "serialize_render_task": "model/project_io.py",
    "deserialize_render_task": "model/project_io.py",
    "_rj_json_safe": "model/project_io.py",
    "_rj_serialize_clip": "model/project_io.py",
    "_rj_serialize_transition": "model/project_io.py",
    "_rj_serialize_track": "model/project_io.py",
    "_rj_serialize_source": "model/project_io.py",
    "_rj_serialize_timeline": "model/project_io.py",
    "_rj_deserialize_timeline": "model/project_io.py",
    # --- timeline helpers used by MainWindow / canvas; keep with main_window
    # until T03 unless they clearly belong to project_io. Conservative: main_window
    # owns task-mutating helpers that the UI calls.
    "get_or_create_track": "ui/main_window.py",
    "get_or_create_bgm_track": "ui/main_window.py",
    "get_or_create_sfx_track": "ui/main_window.py",
    "track_supports_batch": "ui/main_window.py",
    "get_batch_tracks": "ui/main_window.py",
    "_tl_of": "ui/main_window.py",
    "_audio_clips_from_track": "ui/main_window.py",
    "match_intro_media": "ui/main_window.py",
    "extract_first_two_sentences": "ui/main_window.py",
    "_clip_audio_role": "ui/main_window.py",
    "_is_bgm_clip": "ui/main_window.py",
    "_is_sfx_clip": "ui/main_window.py",
    "_find_main_video_track": "ui/main_window.py",
    "_find_thumbnail_clip": "ui/main_window.py",
    "_find_main_content_clip": "ui/main_window.py",
    "_get_main_video_duration": "ui/main_window.py",
    "_prepare_thumbnail_text_meta": "ui/main_window.py",
    "_build_thumbnail_text_overlay": "ui/main_window.py",
    "_find_thumbnail_text_overlay": "ui/main_window.py",
    "_ensure_thumbnail_text_overlay": "ui/main_window.py",
    "_remove_thumbnail_text_overlay": "ui/main_window.py",
    "_extract_thumbnail_text_style": "ui/main_window.py",
    "_apply_thumbnail_text_style": "ui/main_window.py",
    "_verify_first_frame_readable": "ui/main_window.py",
    "apply_thumbnail_leadin_to_task": "ui/main_window.py",
    "remove_thumbnail_leadin_from_task": "ui/main_window.py",
    "update_thumbnail_leadin_duration": "ui/main_window.py",
    "_build_thumbnail_shift_backup": "ui/main_window.py",
    "_shift_task_for_thumbnail": "ui/main_window.py",
    "_restore_task_from_thumbnail_backup": "ui/main_window.py",
    "_find_intro_video_clips": "ui/main_window.py",
    "_build_intro_video_shift_backup": "ui/main_window.py",
    "_shift_task_for_intro_video": "ui/main_window.py",
    "apply_intro_video_to_task": "ui/main_window.py",
    "remove_intro_video_from_task": "ui/main_window.py",
    "update_intro_video_duration": "ui/main_window.py",
    "update_intro_video_settings": "ui/main_window.py",
    "shift_task_timeline": "ui/main_window.py",
    "remap_bgm_to_full_duration": "ui/main_window.py",
    "fit_track_to_duration": "ui/main_window.py",
    "_scale_overlay_for_canvas": "ui/main_window.py",
    "insert_intro_media": "ui/main_window.py",
    "copy_style_full": "ui/panels/subtitle_style.py",
    "apply_text_template_to_overlay": "ui/panels/subtitle_style.py",
    # --- ui lists ---
    "TaskTreeWidgetItem": "ui/panels/media_list.py",
    "DraggableListWidget": "ui/panels/media_list.py",
    # --- audio waveform worker (lives with waveform.py) ---
    "_WaveformWorker": "audio/waveform.py",
    # --- render ---
    "compute_render_order": "render/frame_renderer.py",
    "_resolve_blend_mode": "render/frame_renderer.py",
    "get_cached_image": "render/frame_renderer.py",  # monolith copy; utils.py has another
    "get_dbi_video_frame": "render/frame_renderer.py",
    "cleanup_dbi_video_caps": "render/frame_renderer.py",
    "_render_se_to_painter": "render/frame_renderer.py",
    "FrameRenderer": "render/frame_renderer.py",
    "ExportRenderer": "render/export_renderer.py",
    "_cv2_frame_to_qimage": "render/readers.py",
    "_probe_video_has_alpha": "render/readers.py",
    "_RawARGBMOVReader": "render/readers.py",
    "_FFmpegFrameReader": "render/readers.py",
    "_is_frame_corrupt": "render/readers.py",
    "_open_video_capture": "render/readers.py",
    "AsyncVideoDecoder": "render/readers.py",
    "RenderWorker": "render/render_worker.py",
    "PreviewFxWorker": "render/render_worker.py",
    "run_render_job": "render/parallel.py",
    "ParallelExportManager": "render/parallel.py",
    # --- ui canvas ---
    "InlineTextEdit": "ui/canvas.py",
    "MultiGridTextEdit": "ui/canvas.py",
    "VideoCanvas": "ui/canvas.py",
    # --- probe helpers used by render + UI; first user is RenderWorker path ---
    "probe_bitrates": "render/readers.py",
    "probe_duration_sec": "render/readers.py",
    "_parse_ratio_str": "render/readers.py",
    "_ratio_to_float": "render/readers.py",
    "probe_video_export_params": "render/readers.py",
    "probe_sample_aspect_ratio": "render/readers.py",
    "_get_sar_from_cap_or_path": "render/readers.py",
    "_apply_sar_correction": "render/readers.py",
    "has_audio_stream": "render/readers.py",
    "load_svg_icon_hidpi": "ui/main_window.py",
    "load_svg_icon": "ui/main_window.py",
    # --- subtitle text drawing (used by FrameRenderer + canvas) ---
    "tokenize_for_wrap": "subtitle/text_utils.py",
    "split_token_by_width": "subtitle/text_utils.py",
    "wrap_tokens_by_pixel_width": "subtitle/text_utils.py",
    "wrap_tokens_with_indices": "subtitle/text_utils.py",
    "wrap_text_by_pixel_width": "subtitle/text_utils.py",
    "_rich_plain_text": "subtitle/text_utils.py",
    "_rich_default_layout": "subtitle/text_utils.py",
    "_normalize_text_align": "subtitle/text_utils.py",
    "_qt_align_flag_for_text": "subtitle/text_utils.py",
    "_align_x_for_block": "subtitle/text_utils.py",
    "_iter_line_segments_for_draw": "subtitle/text_utils.py",
    "_visible_lines": "subtitle/text_utils.py",
    "_build_text_bg_brush": "subtitle/text_utils.py",
    "_draw_thumbnail_text_on_rect": "subtitle/text_utils.py",
    "_rich_span_with_fallback": "subtitle/text_utils.py",
    "_rich_apply_base_to_spans": "subtitle/text_utils.py",
    "_rich_apply_effects_to_spans": "subtitle/text_utils.py",
    "_rich_cache_key": "subtitle/text_utils.py",
    "_clear_rich_cache_in_task": "subtitle/text_utils.py",
    "_rich_tokenize": "subtitle/text_utils.py",
    "_rich_layout_lines": "subtitle/text_utils.py",
    "_rich_measure_lines": "subtitle/text_utils.py",
    "_draw_rich_lines": "subtitle/text_utils.py",
    "_editor_set_spans": "ui/panels/text_editors.py",
    "_editor_to_spans": "ui/panels/text_editors.py",
    "_fit_font_size_to_rect": "subtitle/text_utils.py",
    "_plain_layout_lines": "subtitle/text_utils.py",
    "_doc_layout_lines": "subtitle/text_utils.py",
    "resegment_words_by_style": "subtitle/text_utils.py",
    # --- timeline / panels ---
    "TimelineWidget": "ui/timeline/widget.py",
    "TrackHeaderPanel": "ui/timeline/header.py",
    "TimelineContainer": "ui/timeline/container.py",
    "SubtitleEditDialog": "ui/panels/subtitle_style.py",
    "SubtitleStylePanel": "ui/panels/subtitle_style.py",
    "RichTextEditorDialog": "ui/panels/text_editors.py",
    "SimpleTextEditorDialog": "ui/panels/text_editors.py",
    "RoleMappingPanel": "ui/panels/role_mapping.py",
    "WordBlockPanel": "ui/panels/word_block.py",
    "KeywordRuleRow": "ui/panels/keyword_rules.py",
    "KeywordRulesWidget": "ui/panels/keyword_rules.py",
    "ColorOptionPanel": "ui/panels/property.py",
    "PropertyPanel": "ui/panels/property.py",
    "ToggleSwitch": "ui/widgets/collapsible.py",
    "CollapsibleSection": "ui/widgets/collapsible.py",
    "MainWindow": "ui/main_window.py",
    "run_self_test": "app.py",
    "_qt_message_handler": "app.py",
}

# Module-level globals: first class/function that uses them owns them.
# Listed explicitly so the report can dump 全局变量去向.
GLOBAL_MAP: dict[str, str] = {
    "_WHISPERX_MODEL": "asr/engine.py",
    "_WHISPERX_DEVICE": "asr/engine.py",
    "_ALIGN_CACHE": "asr/engine.py",
    "ALIGN_MODEL_BY_LANG": "asr/engine.py",
    "EXPORT_VIDEO_MODE": "render/render_worker.py",
    "IS_RENDER_CHILD": "render/parallel.py",
    "PARALLEL_EXPORT_ENABLED": "render/parallel.py",
    "PARALLEL_EXPORT_WORKERS": "render/parallel.py",
    "EXPORT_ENC_THREADS": "render/parallel.py",
    "BASE_DIR": "core/paths.py",
    "ICON_DIR": "core/paths.py",
    "FONT_DIR": "ui/panels/fonts.py",
    "FONT_ROOT": "ui/panels/fonts.py",
    "FONT_METADATA_PATH": "ui/panels/fonts.py",
    "FONT_FILES": "ui/panels/fonts.py",
    "FONT_FILE_FAMILIES": "ui/panels/fonts.py",
    "FONT_METADATA": "ui/panels/fonts.py",
    "FONT_MAP": "ui/panels/fonts.py",
    "ALLOWED_FONTS": "ui/panels/fonts.py",
    "DEFAULT_FONT_FAMILY": "ui/panels/fonts.py",
    "FONT_MANAGER": "ui/panels/fonts.py",
    "FONT_COMBOS": "ui/panels/fonts.py",
    "STYLE_KEYS": "ui/panels/subtitle_style.py",
    "_RT_PROP_STROKE_COLOR": "subtitle/text_utils.py",
    "_RT_PROP_STROKE_WIDTH": "subtitle/text_utils.py",
    "_RT_PROP_SHADOW_COLOR": "subtitle/text_utils.py",
    "_RT_PROP_SHADOW_OPACITY": "subtitle/text_utils.py",
    "_RT_PROP_SHADOW_BLUR": "subtitle/text_utils.py",
    "_RT_PROP_SHADOW_OFF_X": "subtitle/text_utils.py",
    "_RT_PROP_SHADOW_OFF_Y": "subtitle/text_utils.py",
    "ENABLE_RICH_TEXT": "subtitle/text_utils.py",
    "_RICH_PARSE_FAST_LIMIT": "subtitle/text_utils.py",
    "_RICH_PARSE_BLOCK_LIMIT": "subtitle/text_utils.py",
    "IMG_EXT": "ui/main_window.py",
    "VID_EXT": "ui/main_window.py",
    "AUD_EXT": "ui/main_window.py",
    "DEFAULT_TIMELINE_WIDTH": "model/timeline.py",
    "DEFAULT_TIMELINE_HEIGHT": "model/timeline.py",
    "DEFAULT_TIMELINE_FPS": "model/timeline.py",
    "DEFAULT_TIMELINE_TEMPLATE": "model/timeline.py",
    "DEFAULT_VIDEO_FIT_MODE": "model/timeline.py",
    "PROJECT_VERSION": "model/project_io.py",
    "PROJECT_VERSION_MIN": "model/project_io.py",
    "_THUMB_TEXT_STYLE_KEYS": "ui/main_window.py",
    "_TEXT_TEMPLATE_KEYS": "ui/panels/subtitle_style.py",
    "BLEND_MODES": "render/frame_renderer.py",
    "_BLEND_NORMAL": "render/frame_renderer.py",
    "_DEFAULT_RENDER_ORDER": "render/frame_renderer.py",
    "_IMAGE_CACHE": "render/frame_renderer.py",
    "_DBI_VIDEO_CAPS": "render/frame_renderer.py",
    "_DBI_VIDEO_FRAME_CACHE": "render/frame_renderer.py",
    "_DBI_VIDEO_EXTS": "render/frame_renderer.py",
    "_SE_SUB_CACHE": "render/frame_renderer.py",
    "_SE_OV_CACHE": "render/frame_renderer.py",
    "_RJ_CLIP_KEYS": "model/project_io.py",
    # DEVICE / COMPUTE_TYPE / LANGUAGE are imported from config then
    # re-bound by _sync_device_globals; they must live in asr/engine.py
    "DEVICE": "asr/engine.py",
    "COMPUTE_TYPE": "asr/engine.py",
    "LANGUAGE": "asr/engine.py",
    "MODEL_SIZE": "asr/engine.py",
    "FFMPEG_PATH": "core/paths.py",
    "FFPROBE_PATH": "core/paths.py",
}


def load_monolith() -> tuple[str, ast.Module]:
    # CRLF source; newline="" keeps original newlines (通用规范 §1).
    with open(MONOLITH, "r", encoding="utf-8", newline="") as f:
        src = f.read()
    tree = ast.parse(src)
    return src, tree


def iter_symbols(tree: ast.Module):
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, type(node).__name__, node.lineno, getattr(node, "end_lineno", None)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    yield t.id, "Assign", node.lineno, getattr(node, "end_lineno", None)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            yield node.target.id, "AnnAssign", node.lineno, getattr(node.target, "end_lineno", None)


# Names mutated at runtime; extracted readers must not keep a stale copy.
LIVE_NAMES = {
    "DEVICE", "COMPUTE_TYPE", "LANGUAGE",
    "EXPORT_VIDEO_MODE", "EXPORT_ENC_THREADS",
}

# Appendix C: FONT_DIR must go through cutora.core.paths before load_embedded_fonts
# moves, or a missing-font QMessageBox can deadlock a headless --render-job.
_FONTS_FILE_FIXES = (
    (
        'FONT_DIR = resource_path("fonts")',
        'from cutora.core.paths import FONT_DIR',
    ),
)

# ParallelExportManager.__file__ must keep pointing at core/ until -m cutora.
_PARALLEL_FILE_FIXES = (
    (
        'state_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_data")',
        'from cutora.core.paths import CORE_DIR as _T01_CORE_DIR\n'
        '            state_dir = os.path.join(_T01_CORE_DIR, "user_data")',
    ),
    (
        '        return os.path.abspath(__file__)',
        '        from cutora.core.paths import CORE_DIR as _T01_CORE_DIR\n'
        '        return os.path.join(_T01_CORE_DIR, "AutoSub_debug.py")',
    ),
)

_HOST_HELPER = '''def _t01_host():
    """Monolith module (partially or fully loaded). T01 extract plumbing.

    Render children launch `python core/AutoSub_debug.py --render-job`, so the
    monolith is ``__main__`` rather than ``AutoSub_debug``.
    """
    import sys as _sys
    m = _sys.modules.get("core.AutoSub_debug") or _sys.modules.get("AutoSub_debug")
    if m is None:
        m = _sys.modules.get("__main__")
        if m is None:
            raise RuntimeError("T01: AutoSub_debug is not in sys.modules")
        _f = (getattr(m, "__file__", "") or "").replace("\\\\", "/")
        if "AutoSub_debug.py" not in _f:
            raise RuntimeError("T01: AutoSub_debug is not in sys.modules")
    return m

def _t01_attr(name):
    """Resolve a name that may already live on cutora.ui.main_window.

    Leaf modules (canvas / timeline / render_worker) used to bind helpers from
    the host at import time. After those helpers move into main_window.py, the
    host import of main_window has not returned yet, so getattr(host, name)
    fails. Prefer main_window.__dict__ if the name is already defined there.
    """
    import sys as _sys
    mw = _sys.modules.get("cutora.ui.main_window")
    if mw is not None and name in mw.__dict__:
        return mw.__dict__[name]
    return getattr(_t01_host(), name)
'''

_BUILTIN = set(dir(__builtins__)) if not isinstance(__builtins__, dict) else set(__builtins__)


def dump_map(path: str) -> None:
    src, tree = load_monolith()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    rows = []
    unmapped = []
    for name, kind, start, end in iter_symbols(tree):
        dest = SYMBOL_MAP.get(name) or GLOBAL_MAP.get(name) or ""
        if not dest:
            unmapped.append((name, kind, start, end))
        rows.append({
            "name": name,
            "kind": kind,
            "start": start,
            "end": end or "",
            "module": dest or "UNMAPPED",
        })
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        w = csv.DictWriter(f, fieldnames=["name", "kind", "start", "end", "module"])
        w.writeheader()
        w.writerows(rows)
    print("wrote %s (%d symbols, %d unmapped)" % (path, len(rows), len(unmapped)))
    for name, kind, start, end in unmapped:
        print("UNMAPPED %s %s %s-%s" % (kind, name, start, end))


def _dest_of(name: str) -> str:
    return SYMBOL_MAP.get(name) or GLOBAL_MAP.get(name) or ""


def _node_names(node: ast.AST) -> list[str]:
    names = []
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        names.append(node.name)
    elif isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name):
                names.append(t.id)
            elif isinstance(t, ast.Tuple):
                for e in t.elts:
                    if isinstance(e, ast.Name):
                        names.append(e.id)
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        names.append(node.target.id)
    return names


def _nodes_for_module(tree: ast.Module, module_rel: str) -> list[ast.AST]:
    out = []
    seen = set()
    for node in tree.body:
        names = _node_names(node)
        if not names:
            continue
        dests = {_dest_of(n) for n in names}
        if module_rel in dests:
            nid = id(node)
            if nid not in seen:
                seen.add(nid)
                out.append(node)
    return out


def _line_is_comment_or_blank(line: str) -> bool:
    s = line.strip()
    return (not s) or s.startswith("#")


def _block_range(node: ast.AST, prev_end: int) -> tuple[int, int]:
    """1-based inclusive (start, end) including leading comments."""
    start = node.lineno
    end = getattr(node, "end_lineno", node.lineno) or node.lineno
    # leading comments: walk up to prev_end, keep # / blank immediately above
    return start, end  # comments filled by caller with src_lines


def _leading_comment_start(src_lines: list[str], node: ast.AST, prev_end: int) -> int:
    start = node.lineno
    i = start - 1  # 1-based line above node
    floor = max(prev_end, 0)
    while i > floor:
        line = src_lines[i - 1]
        if _line_is_comment_or_blank(line):
            i -= 1
            continue
        break
    # skip leading blanks so we don't steal the previous node's trailing blank
    grab = i + 1
    while grab < start and not src_lines[grab - 1].strip():
        grab += 1
    # if the first non-blank is a comment, include from there; else just the node
    if grab < start and src_lines[grab - 1].lstrip().startswith("#"):
        return grab
    return start


def _slice_lines(src_lines: list[str], start: int, end: int) -> str:
    return "".join(src_lines[start - 1:end])


def _import_alias_names(node: ast.AST) -> list[str]:
    names = []
    if isinstance(node, ast.Import):
        for a in node.names:
            names.append(a.asname or a.name.split(".")[0])
    elif isinstance(node, ast.ImportFrom):
        for a in node.names:
            if a.name == "*":
                names.append("*")
            else:
                names.append(a.asname or a.name)
    return names


def _try_assigned_names(node: ast.Try) -> list[str]:
    names = []
    for part in [node] + list(node.handlers) + ([node.orelse] if False else []):
        body = part.body if part is node else (part.body if hasattr(part, "body") else part)
        if part is node:
            body = node.body
        for stmt in body:
            names.extend(_node_names(stmt))
            names.extend(_import_alias_names(stmt))
    for h in node.handlers:
        for stmt in h.body:
            names.extend(_node_names(stmt))
            names.extend(_import_alias_names(stmt))
        if h.name:
            names.append(h.name)
    return names


def _free_names(nodes: list[ast.AST]) -> set[str]:
    """Module-level free names loaded by extracted defs/assigns."""
    defined = set()
    for n in nodes:
        defined.update(_node_names(n))

    loaded = set()

    class V(ast.NodeVisitor):
        def __init__(self):
            self.env = [set()]  # function local stacks; empty = module

        def _push(self, extra=None):
            self.env.append(set(extra or ()))

        def _pop(self):
            self.env.pop()

        def _bind(self, name):
            self.env[-1].add(name)

        def _load(self, name):
            for frame in reversed(self.env):
                if name in frame:
                    return
            if name in defined or name in _BUILTIN:
                return
            loaded.add(name)

        def visit_FunctionDef(self, node):
            # Nested function name is bound in the enclosing frame.
            self._bind(node.name)
            args = set()
            a = node.args
            for g in (a.posonlyargs, a.args, a.kwonlyargs):
                for arg in g:
                    args.add(arg.arg)
            if a.vararg:
                args.add(a.vararg.arg)
            if a.kwarg:
                args.add(a.kwarg.arg)
            self._push(args)
            for d in node.args.defaults + node.args.kw_defaults:
                if d is not None:
                    self.visit(d)
            for stmt in node.body:
                self.visit(stmt)
            self._pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node):
            if len(self.env) == 1:
                self._bind(node.name)
            for b in node.bases:
                self.visit(b)
            # class body: methods get their own frames; other names are module
            for stmt in node.body:
                self.visit(stmt)

        def visit_Global(self, node):
            for n in node.names:
                # treat as module-level
                pass

        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Load):
                self._load(node.id)
            elif isinstance(node.ctx, ast.Store):
                if len(self.env) > 1:
                    self._bind(node.id)

        def visit_Import(self, node):
            for a in node.names:
                self._bind(a.asname or a.name.split(".")[0])

        def visit_ImportFrom(self, node):
            for a in node.names:
                if a.name != "*":
                    self._bind(a.asname or a.name)

        def _visit_comp(self, node):
            # generators bind targets before the elt is evaluated
            self._push()
            for gen in node.generators:
                self.visit(gen)
            self.visit(node.elt)
            if isinstance(node, ast.DictComp):
                self.visit(node.key)
                self.visit(node.value)
            self._pop()

        def visit_ListComp(self, node):
            self._push()
            for gen in node.generators:
                self.visit(gen)
            self.visit(node.elt)
            self._pop()

        def visit_SetComp(self, node):
            self.visit_ListComp(node)

        def visit_GeneratorExp(self, node):
            self.visit_ListComp(node)

        def visit_DictComp(self, node):
            self._push()
            for gen in node.generators:
                self.visit(gen)
            self.visit(node.key)
            self.visit(node.value)
            self._pop()

        def visit_Lambda(self, node):
            args = set()
            a = node.args
            for g in (a.posonlyargs, a.args, a.kwonlyargs):
                for arg in g:
                    args.add(arg.arg)
            if a.vararg:
                args.add(a.vararg.arg)
            if a.kwarg:
                args.add(a.kwarg.arg)
            self._push(args)
            self.visit(node.body)
            self._pop()

        def visit_ExceptHandler(self, node):
            if node.name:
                self._bind(node.name)
            self.generic_visit(node)

    v = V()
    for n in nodes:
        v.visit(n)
    return loaded


def _module_defines(path: str) -> set[str]:
    if not os.path.isfile(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        if not src.strip():
            return set()
        tree = ast.parse(src)
    except Exception:
        return set()
    names = set()
    for node in tree.body:
        names.update(_node_names(node))
        names.update(_import_alias_names(node))
    return names


def _rel_to_mod(module_rel: str) -> str:
    return "cutora." + module_rel.replace("/", ".").removesuffix(".py")


def _collect_import_nodes(tree: ast.Module) -> list[ast.AST]:
    out = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            out.append(node)
        elif isinstance(node, ast.Try):
            out.append(node)
    return out


def _unused_import_trim(header: str, body: str) -> str:
    """Drop whole import statements whose aliases never appear in body."""
    try:
        tree = ast.parse(header + "\n" + body)
    except SyntaxError:
        return header
    used = set()

    class U(ast.NodeVisitor):
        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Load):
                used.add(node.id)

        def visit_Attribute(self, node):
            self.visit(node.value)

    U().visit(tree)
    keep_lines = set()  # 1-based lines of header to keep (non-import always kept)
    header_tree = ast.parse(header) if header.strip() else ast.parse("pass")
    header_lines = header.splitlines(keepends=True)
    drop_ranges = []
    if not header.strip():
        return header
    try:
        ht = ast.parse(header)
    except SyntaxError:
        return header
    for node in ht.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            aliases = _import_alias_names(node)
            if "*" in aliases or any(a in used for a in aliases):
                continue
            drop_ranges.append((node.lineno, getattr(node, "end_lineno", node.lineno)))
        elif isinstance(node, ast.Try):
            aliases = _try_assigned_names(node)
            if any(a in used for a in aliases):
                continue
            drop_ranges.append((node.lineno, getattr(node, "end_lineno", node.lineno)))
    if not drop_ranges:
        return header
    drop = set()
    for a, b in drop_ranges:
        for i in range(a, b + 1):
            drop.add(i)
    kept = []
    for i, line in enumerate(header_lines, 1):
        if i not in drop:
            kept.append(line)
    return "".join(kept)


def _dedent_keep(func_src: str) -> tuple[str, str]:
    """Return (indent, dedented). indent is the common leading whitespace of def."""
    indent = _func_indent(func_src)
    if not indent:
        return "", func_src
    lines = func_src.splitlines(keepends=True)
    out = []
    for line in lines:
        if line.startswith(indent):
            out.append(line[len(indent):])
        else:
            out.append(line.lstrip(" ") if line.strip() else line)
    return indent, "".join(out)


def _insert_after_signature(func_src: str, extra: str) -> str:
    """Insert extra after def/docstring/global — never before a global stmt."""
    prefix, dedented = _dedent_keep(func_src)
    try:
        tree = ast.parse(dedented)
    except SyntaxError:
        return extra + func_src
    node = tree.body[0]
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return extra + func_src
    lines = dedented.splitlines(keepends=True)
    body = node.body
    if not body:
        return func_src
    idx = 0
    first = body[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(getattr(first, "value", None), ast.Constant)
        and isinstance(first.value.value, str)
    ):
        idx = 1
    while idx < len(body) and isinstance(body[idx], (ast.Global, ast.Nonlocal)):
        idx += 1
    if idx < len(body):
        insert_at = body[idx].lineno
    else:
        last = body[-1]
        insert_at = (getattr(last, "end_lineno", last.lineno) or last.lineno) + 1
    nl = "\r\n" if func_src.endswith("\r\n") or "\r\n" in func_src[:80] else "\n"
    extra_line = extra if extra.endswith(("\n", "\r\n")) else extra + nl
    # extra is written with full indent (class+def). Strip prefix so it matches dedented.
    if prefix and extra_line.startswith(prefix):
        extra_line = extra_line[len(prefix):]
    lines.insert(insert_at - 1, extra_line)
    rebuilt = "".join(lines)
    if not prefix:
        return rebuilt
    return "".join(
        (prefix + ln) if ln.strip() else ln for ln in rebuilt.splitlines(keepends=True)
    )


def _func_indent(func_src: str) -> str:
    for line in func_src.splitlines():
        if line.strip().startswith(("def ", "async def ")):
            return line[: len(line) - len(line.lstrip())]
    return ""


def _live_insert_for_function(
    func_src: str, live_needed: set[str], current_mod: str
) -> str:
    if not live_needed:
        return func_src
    _prefix, dedented = _dedent_keep(func_src)
    try:
        tree = ast.parse(dedented)
        node = tree.body[0]
    except Exception:
        return func_src
    used = set()

    class N(ast.NodeVisitor):
        def visit_Name(self, n):
            if isinstance(n.ctx, ast.Load) and n.id in live_needed:
                used.add(n.id)

    N().visit(node)
    if not used:
        return func_src
    indent = _func_indent(func_src) + "    "
    # group by owner module
    from_host = []
    by_mod = {}
    for name in sorted(used):
        # LIVE names are mutated on the host (or asr.engine). A `from x import N`
        # inside the method is a value copy and desyncs writes (EXPORT_VIDEO_MODE).
        if name in LIVE_NAMES:
            from_host.append(name)
            continue
        owner = _dest_of(name)
        owner_path = os.path.join(REPO_ROOT, "cutora", owner.replace("/", os.sep))
        if owner and owner != current_mod and name in _module_defines(owner_path):
            by_mod.setdefault(owner, []).append(name)
        else:
            from_host.append(name)
    extras = []
    for owner, names in by_mod.items():
        extras.append(
            "%sfrom %s import %s"
            % (indent, _rel_to_mod(owner), ", ".join(names))
        )
    for name in from_host:
        extras.append("%s%s = _t01_host().%s" % (indent, name, name))
    if not extras:
        return func_src
    nl = "\r\n" if "\r\n" in func_src[:120] else "\n"
    block = nl.join(extras) + nl
    return _insert_after_signature(func_src, block)


def analyze_extract(module_rel: str) -> dict:
    src, tree = load_monolith()
    src_lines = src.splitlines(keepends=True)
    nodes = _nodes_for_module(tree, module_rel)
    if not nodes:
        raise SystemExit("no symbols mapped to %s" % module_rel)
    # skip Assign nodes for LIVE names that are also stored outside this extract
    remaining_stores = set()
    extracted_ids = {id(n) for n in nodes}
    for node in tree.body:
        if id(node) in extracted_ids:
            continue
        class S(ast.NodeVisitor):
            def visit_Name(self, n):
                if isinstance(n.ctx, ast.Store):
                    remaining_stores.add(n.id)
            def visit_Global(self, n):
                remaining_stores.update(n.names)
        S().visit(node)
    kept_nodes = []
    skipped_live = []
    for node in nodes:
        names = _node_names(node)
        if (
            isinstance(node, ast.Assign)
            and any(n in LIVE_NAMES and n in remaining_stores for n in names)
        ):
            skipped_live.append(names)
            continue
        kept_nodes.append(node)
    nodes = kept_nodes
    free = _free_names(nodes)
    return {
        "src": src,
        "src_lines": src_lines,
        "tree": tree,
        "nodes": nodes,
        "free": free,
        "skipped_live": skipped_live,
    }


def _cutora_import_lines(names: list[str], module_rel: str, nl: str) -> str:
    mod = _rel_to_mod(module_rel)
    if len(names) <= 3:
        return "from %s import %s%s" % (mod, ", ".join(names), nl)
    inner = "," + nl + "    "
    return "from %s import (%s%s%s%s)%s" % (
        mod, nl + "    ", inner.join(names), ",", nl, nl,
    )


def extract_module(module_rel: str, dry_run: bool) -> int:
    info = analyze_extract(module_rel)
    src = info["src"]
    src_lines = info["src_lines"]
    tree = info["tree"]
    nodes = info["nodes"]
    free = info["free"]
    nl = "\r\n" if src.startswith("\r") or "\r\n" in src[:200] else "\n"
    dest_rel = "cutora/" + module_rel
    dest_path = os.path.join(REPO_ROOT, dest_rel.replace("/", os.sep))

    # preceding-end for comment grab: previous top-level node's end
    body = tree.body
    idx_of = {id(n): i for i, n in enumerate(body)}
    ranges = []  # (start, end, node)
    for node in nodes:
        i = idx_of[id(node)]
        prev_end = body[i - 1].end_lineno if i > 0 else 0
        start = _leading_comment_start(src_lines, node, prev_end)
        end = getattr(node, "end_lineno", node.lineno)
        ranges.append((start, end, node))
    ranges.sort(key=lambda x: x[0])

    names_in_order = []
    seen_n = set()
    for _s, _e, node in ranges:
        for n in _node_names(node):
            if n not in seen_n:
                seen_n.add(n)
                names_in_order.append(n)

    print("extract %s (%d nodes, %d names)" % (module_rel, len(nodes), len(names_in_order)))
    print("  names: %s" % ", ".join(names_in_order[:20]) + ("..." if len(names_in_order) > 20 else ""))
    if info["skipped_live"]:
        print("  skipped live assigns: %s" % info["skipped_live"])

    # classify free names
    extracted_defined = set(names_in_order)
    import_nodes = _collect_import_nodes(tree)
    import_provided = set()
    for inode in import_nodes:
        if isinstance(inode, ast.Try):
            import_provided.update(_try_assigned_names(inode))
        else:
            import_provided.update(_import_alias_names(inode))

    from_cutora = {}  # module_rel -> [names]
    host_early = []
    host_late = []
    live_from_elsewhere = set()
    first_import_line = ranges[0][0]
    symbol_def_line = {}
    for node in tree.body:
        for n in _node_names(node):
            symbol_def_line.setdefault(n, node.lineno)

    toplevel = set()
    for node in tree.body:
        toplevel.update(_node_names(node))
        toplevel.update(_import_alias_names(node))
        if isinstance(node, ast.Try):
            toplevel.update(_try_assigned_names(node))

    for name in sorted(free):
        if name in extracted_defined or name in _BUILTIN:
            continue
        # Comprehension/loop leftovers: not a monolith global, ignore.
        if name not in toplevel and _dest_of(name) == "":
            continue
        dest = _dest_of(name)
        dest_file = os.path.join(REPO_ROOT, "cutora", dest.replace("/", os.sep)) if dest else ""
        if name in LIVE_NAMES and dest != module_rel:
            live_from_elsewhere.add(name)
            continue
        if dest and dest != module_rel and name in _module_defines(dest_file):
            from_cutora.setdefault(dest, []).append(name)
            continue
        if name in import_provided:
            continue
        def_line = symbol_def_line.get(name, 10**9)
        if def_line < first_import_line:
            host_early.append(name)
        else:
            host_late.append(name)

    print("  from other cutora: %s" % dict(from_cutora) if from_cutora else "  from other cutora: (none)")
    print("  host early: %s" % host_early)
    print("  host late:  %s" % host_late)
    print("  live elsewhere: %s" % sorted(live_from_elsewhere))

    # build extracted body (LF for new/updated cutora modules)
    body_parts = []
    for start, end, node in ranges:
        chunk = _slice_lines(src_lines, start, end)
        # normalize to LF in dest
        chunk = chunk.replace("\r\n", "\n")
        if not chunk.endswith("\n"):
            chunk += "\n"
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            chunk = _live_insert_for_function(
                chunk, live_from_elsewhere,
                module_rel,
            )
        elif isinstance(node, ast.ClassDef):
            # apply per-method: rebuild via parsing the class? cheaper: apply on
            # each method source inside the class by walking methods
            try:
                ctree = ast.parse(chunk)
                cls = ctree.body[0]
                # work on LF lines
                clines = chunk.splitlines(keepends=True)
                # insert from the bottom so line numbers stay valid
                methods = [
                    s for s in cls.body
                    if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                for meth in reversed(methods):
                    msrc = "".join(clines[meth.lineno - 1: meth.end_lineno])
                    new_m = _live_insert_for_function(
                        msrc,
                        live_from_elsewhere,
                        module_rel,
                    )
                    if new_m != msrc:
                        clines[meth.lineno - 1: meth.end_lineno] = [new_m]
                chunk = "".join(clines)
            except SyntaxError:
                pass
        body_parts.append(chunk.rstrip() + "\n")
    extracted_body = "\n".join(body_parts)

    if module_rel == "render/parallel.py":
        for old, new in _PARALLEL_FILE_FIXES:
            if old not in extracted_body:
                print("WARNING: parallel __file__ pattern not found: %r" % old)
            else:
                extracted_body = extracted_body.replace(old, new, 1)

    if module_rel == "ui/panels/fonts.py":
        for old, new in _FONTS_FILE_FIXES:
            if old not in extracted_body:
                print("WARNING: fonts FONT_DIR pattern not found: %r" % old)
            else:
                extracted_body = extracted_body.replace(old, new, 1)
                host_early = [n for n in host_early if n != "resource_path"]
                print("  patched FONT_DIR -> cutora.core.paths.FONT_DIR")

    if module_rel == "core/history.py":
        old = (
            'def _get_app_settings_path():\n'
            '    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_data", "app_settings.json")\n'
        )
        new = (
            'def _get_app_settings_path():\n'
            '    from cutora.core.paths import USER_DATA\n'
            '    return os.path.join(USER_DATA, "app_settings.json")\n'
        )
        if old not in extracted_body:
            print("WARNING: history app_settings path pattern not found")
        else:
            extracted_body = extracted_body.replace(old, new, 1)
            print("  patched _get_app_settings_path -> cutora.core.paths.USER_DATA")

    if module_rel == "core/paths.py":
        extracted_body = extracted_body.replace(
            '        base = os.path.dirname(os.path.abspath(__file__))',
            '        base = CORE_DIR',
            1,
        )
        extracted_body = extracted_body.replace(
            'BASE_DIR = os.path.dirname(os.path.abspath(__file__))',
            'BASE_DIR = CORE_DIR',
            1,
        )
        print("  patched resource_path/BASE_DIR -> CORE_DIR")

    # header: copied imports that provide used names, plus generated cutora imports
    used_for_imports = set(free) | extracted_defined
    header_chunks = []
    seen_src = set()
    for inode in import_nodes:
        raw = ast.get_source_segment(src, inode)
        if not raw:
            raw = _slice_lines(
                src_lines, inode.lineno, getattr(inode, "end_lineno", inode.lineno)
            ).replace("\r\n", "\n")
        raw = raw.replace("\r\n", "\n").rstrip() + "\n"
        if raw in seen_src:
            continue
        seen_src.add(raw)
        if isinstance(inode, ast.Try):
            aliases = _try_assigned_names(inode)
        else:
            aliases = _import_alias_names(inode)
        if "*" in aliases or any(a in free for a in aliases):
            header_chunks.append(raw)
    for dest, ns in sorted(from_cutora.items()):
        header_chunks.append(
            "from %s import %s\n" % (_rel_to_mod(dest), ", ".join(ns))
        )

    need_host = bool(host_early or host_late or (
        live_from_elsewhere and not any(
            _dest_of(n) and n in _module_defines(
                os.path.join(REPO_ROOT, "cutora", _dest_of(n).replace("/", os.sep))
            )
            for n in live_from_elsewhere
        )
    ))
    # host helper if any host bind
    if host_early or host_late or any(
        n not in _module_defines(
            os.path.join(REPO_ROOT, "cutora", (_dest_of(n) or "x").replace("/", os.sep))
        )
        for n in live_from_elsewhere
    ):
        need_host = True
    else:
        need_host = bool(host_early or host_late)

    helper = _HOST_HELPER if (host_early or host_late or any(
        not _module_defines(os.path.join(REPO_ROOT, "cutora", (_dest_of(n) or "").replace("/", os.sep)))
        for n in live_from_elsewhere
    )) else ""

    early_bind = ""
    if host_early:
        early_bind = (
            "_h = _t01_host()\n"
            + "".join("%s = _h.%s\n" % (n, n) for n in host_early)
        )

    header = "".join(header_chunks)
    header = _unused_import_trim(header, helper + early_bind + extracted_body)

    banner = (
        "# Extracted from core/AutoSub_debug.py by tools/split_monolith.py (T01).\n"
        "# Original lines: %s\n\n"
        % ", ".join("%d-%d" % (s, e) for s, e, _ in ranges)
    )
    new_mod = banner + header
    if helper:
        if not new_mod.endswith("\n\n"):
            new_mod = new_mod.rstrip() + "\n\n"
        new_mod += helper + "\n"
    if early_bind:
        new_mod += early_bind + "\n"
    new_mod += extracted_body
    if not new_mod.endswith("\n"):
        new_mod += "\n"

    # append vs create
    existing = ""
    if os.path.isfile(dest_path):
        with open(dest_path, "r", encoding="utf-8") as f:
            existing = f.read()
    if existing.strip():
        # satellite already there — append extracted symbols
        final_mod = existing.rstrip() + "\n\n# --- extracted from AutoSub_debug.py (T01) ---\n"
        # only append helper/binds/body, not duplicate huge imports if possible
        extra = ""
        if helper and "_t01_host" not in existing:
            extra += helper + "\n"
        extra += early_bind + extracted_body
        # still need imports used by the appended body
        extra_header = header
        extra_header = _unused_import_trim(extra_header, extra)
        final_mod += extra_header + extra
        if not final_mod.endswith("\n"):
            final_mod += "\n"
    else:
        final_mod = new_mod

    # patch monolith: replace first range with import, delete other ranges
    import_stmt = _cutora_import_lines(names_in_order, module_rel, nl)
    bind_back = ""
    if host_late:
        bind_mod = _rel_to_mod(module_rel).replace(".", "_")
        alias = "_t01_bind_" + module_rel.replace("/", "_").replace(".py", "")
        bind_back = (
            "import %s as %s%s"
            % (_rel_to_mod(module_rel), alias, nl)
            + "".join("%s.%s = %s%s" % (alias, n, n, nl) for n in host_late)
        )
        # insert bind_back after the last late symbol definition
        last_late_line = 0
        for node in tree.body:
            for n in _node_names(node):
                if n in host_late:
                    last_late_line = max(last_late_line, getattr(node, "end_lineno", node.lineno))

    # mark lines for deletion / replacement
    new_lines = list(src_lines)
    # process ranges from the bottom so indices stay valid
    for i, (start, end, node) in enumerate(reversed(ranges)):
        is_first = (start, end, node) == ranges[0]
        if is_first:
            replacement = import_stmt
            if not replacement.endswith(("\n", "\r\n")):
                replacement += nl
            new_lines[start - 1:end] = [replacement]
        else:
            new_lines[start - 1:end] = []

    if host_late and last_late_line:
        # last_late_line refers to ORIGINAL line numbers; after deletions they shifted.
        # Re-parse strategy: find the last late name's `def`/`assign` still in the file
        # by searching the text for the bind point after the remaining definition.
        text = "".join(new_lines)
        # Insert bind_back after the last occurrence of each late name's remaining def.
        # Simpler: append bind_back immediately after the new import_stmt (too early).
        # Find each late name in the remaining monolith by original source of that node
        # which we did NOT extract, so the def is still at a shifted line.
        # Compute shift: all extracted ranges after last_late_line don't affect it;
        # extracted ranges before last_late_line shorten the file.
        shift = 0
        for start, end, node in ranges:
            if end <= last_late_line:
                # first range replaced by 1 line (import), others deleted
                orig_len = end - start + 1
                new_len = 1 if (start, end, node) == ranges[0] else 0
                shift += orig_len - new_len
        insert_at = last_late_line - shift  # 1-based last kept line of last late def
        bb_lines = [ln if ln.endswith(("\n", "\r\n")) else ln + nl for ln in bind_back.splitlines(keepends=True)]
        if not bb_lines[-1].endswith(("\n", "\r\n")):
            bb_lines[-1] += nl
        new_lines[insert_at:insert_at] = bb_lines

    new_src = "".join(new_lines)
    # 通用规范 §1: AutoSub_debug.py stays CRLF until T01 deletes it.
    new_src = new_src.replace("\r\n", "\n").replace("\n", "\r\n")

    if dry_run:
        print("DRY-RUN dest %s (%d bytes, existing=%s)" % (
            dest_rel, len(final_mod), "yes" if existing.strip() else "no"))
        print("---- dest head ----")
        print("\n".join(final_mod.splitlines()[:80]))
        print("---- import stmt ----")
        print(import_stmt)
        if bind_back:
            print("---- bind-back ----")
            print(bind_back)
        return 0

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(final_mod)
    with open(MONOLITH, "w", encoding="utf-8", newline="") as f:
        f.write(new_src)
    if module_rel == "ui/main_window.py":
        _postprocess_main_window(dest_path)
    print("wrote %s and patched core/AutoSub_debug.py" % dest_rel)
    return 0


# Leaf UI / render modules bind helpers at import time via _t01_host().
# main_window.py owns those helpers, so importing canvas/timeline/render_worker
# in the header (before the helpers exist) AttributeErrors. Defer those imports
# until after the helper defs, immediately before class MainWindow.
_MAIN_WINDOW_CYCLIC_MODULES = {
    "cutora.ui.canvas",
    "cutora.ui.timeline.widget",
    "cutora.ui.timeline.header",
    "cutora.ui.timeline.container",
    "cutora.render.render_worker",
    "cutora.render.frame_renderer",
}


def _postprocess_main_window(path: str) -> None:
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    src = _defer_cyclic_imports(src)
    src = _fix_export_video_mode_writeback(src)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    print("  postprocess: deferred cyclic imports + EXPORT_VIDEO_MODE host writeback")


def _defer_cyclic_imports(src: str) -> str:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        print("WARNING: main_window postprocess parse failed")
        return src
    lines = src.splitlines(keepends=True)
    move = []  # (start, end) 1-based inclusive
    for node in tree.body:
        mod = None
        if isinstance(node, ast.ImportFrom):
            mod = node.module
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name in _MAIN_WINDOW_CYCLIC_MODULES:
                    mod = a.name
                    break
        if mod in _MAIN_WINDOW_CYCLIC_MODULES:
            move.append((node.lineno, getattr(node, "end_lineno", node.lineno) or node.lineno))
    class_line = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            class_line = node.lineno
            break
    if not move or class_line is None:
        return src
    # Only move imports that currently sit *before* class MainWindow.
    move = [(a, b) for a, b in move if b < class_line]
    if not move:
        return src
    chunks = []
    for a, b in move:
        chunk = "".join(lines[a - 1:b])
        if not chunk.endswith("\n"):
            chunk += "\n"
        chunks.append(chunk)
    drop = set()
    for a, b in move:
        for i in range(a, b + 1):
            drop.add(i)
    kept = [ln for i, ln in enumerate(lines, 1) if i not in drop]
    # class_line shifted after deletions
    shift = sum(b - a + 1 for a, b in move)
    new_class = class_line - shift
    block = (
        "# Cyclic imports deferred until helpers (_tl_of, IMG_EXT, ...) exist.\n"
        "# Leaf modules bind those names at import time (appendix C pit 1).\n"
        + "".join(chunks)
        + "\n"
    )
    kept[new_class - 1:new_class - 1] = [block]
    return "".join(kept)


def _fix_export_video_mode_writeback(src: str) -> str:
    """MainWindow.open_export_settings must mutate the host LIVE name, not a copy."""
    old = "            EXPORT_VIDEO_MODE = new_mode"
    new = "            _t01_host().EXPORT_VIDEO_MODE = new_mode"
    if old not in src:
        print("WARNING: EXPORT_VIDEO_MODE = new_mode not found in main_window")
        return src
    return src.replace(old, new, 1)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="T01 monolith splitter")
    p.add_argument("--dump-map", action="store_true")
    p.add_argument(
        "--map-csv",
        default=os.path.join(REPO_ROOT, "docs", "plan", "reports", "T01_symbol_map.csv"),
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--write", action="store_true", help="emit ALL modules (not used; prefer --extract)")
    p.add_argument("--extract", metavar="MODULE", help="e.g. subtitle/text_utils.py")
    p.add_argument("--analyze", metavar="MODULE", help="print free names / deps, no write")
    args = p.parse_args(argv)
    if args.analyze:
        info = analyze_extract(args.analyze)
        print("nodes", len(info["nodes"]))
        print("names", [_node_names(n) for n in info["nodes"]])
        print("free", sorted(info["free"]))
        print("skipped_live", info["skipped_live"])
        return 0
    if args.extract:
        return extract_module(args.extract, dry_run=args.dry_run)
    if args.dump_map or (args.dry_run and not args.extract):
        dump_map(args.map_csv)
        return 0
    if args.write:
        print("use --extract MODULE for slice-by-slice write (appendix B)")
        return 2
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())

