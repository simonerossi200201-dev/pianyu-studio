# -*- coding: utf-8 -*-
"""Re-exec in-process Qt tests in a fresh interpreter.

Windows has no ``os.fork`` (pytest-forked is unusable here). These test
files import PySide6 or ``script_stitcher`` (which imports PySide6 at module
level) into the pytest parent; that is what makes full-suite results
depend on collection order and abort in Qt6Core with 0xC0000409.

The parent never imports those modules. Each listed file runs once in a
child pytest that calls ``init_torch_runtime()`` before collection.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
import types
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import pytest

ENV = "CUTORA_PYTEST_ISOLATED"

# Files that load PySide6 (or script_stitcher → PySide6) in the pytest parent.
# Already-subprocess UI tests (import only inside run()/run_suite()) stay out.
ISOLATE_FILES = frozenset({
    "test_q212_levels.py",
    "test_q211_mask_layer.py",
    "test_q198_volume_preview.py",
    "test_q204a_av_link.py",
    "test_q204b_fades.py",
    "test_q126_text_context_bar.py",
    "test_q127_wysiwyg_text.py",
    "test_q128_multi_align_style.py",
    "test_q129_bg_grow_handles.py",
    "test_q130_export_perf.py",
    "test_q131_stitch_nested_drop.py",
    "test_q134_batch_preview_bg.py",
    "test_q135_share_style_word_mode.py",
    "test_q139_subtitle_text_identity.py",
    "test_q137_reflow.py",
    "test_q138_legacy_preset_import.py",
    "test_q140_share_keep_duration.py",
    "test_q141_143_batch.py",
    "test_q144_central_monitor.py",
    "test_q145_canvas_shortcuts.py",
    "test_q145b_timeline_shortcuts.py",
    "test_q145c_text_runs.py",
    "test_q146_link_image.py",
    "test_q149_layer_panel.py",
    "test_q150_locate_progress.py",
    "test_q151_lens_split.py",
    "test_q152g_layer_panel.py",
    "test_q152a_keyframe_editor.py",
    "test_q152a2_keyable_motion.py",
    "test_q153_batch_template_column.py",
    "test_q152b_masks_keyer.py",
    "test_q152d_ai_matte.py",
    "test_q152d2_matte_polish.py",
    "test_q152e_tracking.py",
    "test_q154_clip_groups.py",
    "test_q154_link_groups.py",
    "test_q154_monitor_safety.py",
    "test_q155_backdrop.py",
    "test_q156_monitor_gpu_safe.py",
    "test_q158_sync_overlays.py",
    "test_q159_undo_scope.py",
    "test_q160_segment_export.py",
    "test_q162_batch_asr.py",
    "test_q163_unified_stack.py",
    "test_q164_combo_popup.py",
    "test_q165_keyer_despill_checker.py",
    "test_q166_ai_matte_batch_merge.py",
    "test_q167_bezier_mask.py",
    "test_q169_timeline_colors.py",
    "test_q170_font_picker.py",
    "test_q171_batch_key.py",
    "test_q172_monitor_layer_drag.py",
    "test_q173_monitor_layer_ops.py",
    "test_q174_adjust_track.py",
    "test_q175_playback_adjust3.py",
    "test_q176_track_cleanup.py",
    "test_q177_stack_order.py",
    "test_q178_blend_adjust.py",
    "test_q179_adjust_multi_effect.py",
    "test_q180_export_replay_parallel.py",
    "test_q181_gpu_fx.py",
    "test_q152f_nodes.py",
    "test_q125_text_handles.py",
    "test_q124_text_box.py",
    "test_q120_text_batch.py",
    "test_q121_text_preview.py",
    "test_q122_smooth.py",
    "test_q118_library_names.py",
    "test_q117_bg_fill.py",
    "test_q116_split_canvas_pick.py",
    "test_q115_track_dnd.py",
    "test_q111_link_import.py",
    "test_q96_scatter_dialog.py",
    "test_q96_bin_menu_order.py",
    "test_q96_karaoke_no_words.py",
    "test_q96_missing_report.py",
    "test_api_bridge.py",
    "test_export_guard.py",
    "test_preview_seek.py",
    "test_script_stitcher_edge.py",
    "test_script_stitcher_rescue.py",
    "test_bug06_07_stitcher.py",
    "test_t20b_missing_grade.py",
    "test_t20c_transitions.py",
    "test_t20d_edit.py",
    "test_dock_pages_never_overflow.py",
    "test_t20e_library_stitch.py",
    "test_t24_gif.py",
    "test_t25_stitch_ux.py",
    "test_asr_coverage_floor.py",
    "test_startup_order.py",
    "test_stitcher_duplicate_sentence.py",
    "test_t12e_apostrophe.py",
    "test_t12e_multilang.py",
    "test_t04f6b1_cyan.py",
    "test_t04f6b1_hit.py",
    "test_t05c_snap.py",
    "test_t05d_markers.py",
    "test_t05e_ripple.py",
    "test_t05f_jkl.py",
    "test_t05f_waveform.py",
    "test_t05g_track.py",
    "test_bug04_delete.py",
    "test_bug01_painterror.py",
    "test_qw_batch.py",
    "test_t06a_offline.py",
    "test_t06b_autosave.py",
    "test_t06c_pack.py",
    "test_t07a_audio_mute.py",
    "test_t08a_export_params.py",
    "test_t08b_export_profile.py",
    "test_bug09_razor.py",
    "test_t22_timeline_tools.py",
    "test_ui_golden.py",
    "test_t04_auto_relayout.py",
    "test_t04b_layout.py",
    "test_command_bus.py",
    "test_mcp_stdio.py",
    "test_bug27_long_audio_wired.py",
    "test_bug37_autosave_after_paint.py",
    "test_bug13_subs_paint.py",
    "test_harden_preserves_explicit.py",
    "test_serial_export_fallback.py",
    "test_g19_pos_frames.py",
    "test_e1_timing_closure.py",
    "test_pipe_thread.py",
    "test_line_bar_panel_color.py",
    "test_e5_prompt_never_fires_headless.py",
    "test_e5_canvas_mismatch_prompt.py",
    "test_t18_no_dead_controls.py",
    "test_t19_fill_signal.py",
    "test_t20_stroke_signal.py",
    "test_q42_sliderspin.py",
    "test_q45_states.py",
    "test_q46_tokens.py",
    "test_ui_invariants.py",
    "test_q53_clip_colors.py",
    "test_q51_highlight.py",
    "test_q52_text_panel.py",
    "test_q66_toggle.py",
    "test_q60_dead_controls.py",
    "test_q64_subtitle_panel.py",
    "test_q57_g19b2_no_black.py",
    "test_q11_compose_parallel.py",
    "test_t25_inspector.py",
    "test_t23_wrap_width.py",
    "test_t29_transcript_source.py",
    "test_t30_spatial.py",
    "test_t28_fps_meter.py",
    "test_t23b_batch_preview.py",
    "test_q81_batch_canvas.py",
    "test_q54_open.py",
    "test_q71_open.py",
    "test_q14_alpha.py",
    "test_q49_alpha.py",
    "test_q33_timeline.py",
    "test_q63_track_height.py",
    "test_q61_assets.py",
    "test_q67_alpha.py",
    "test_q72_parallel_compose.py",
    "test_q76_glass_backdrop.py",
    "test_q77_timeline.py",
    "test_q78_canvas_aspect.py",
    "test_q87_canvas_gif.py",
    "test_q86_split_feather.py",
    "test_t34_split.py",
    "test_q192_inwindow.py",
    "test_q69_timeline.py",
    "test_q70_preview_export.py",
    "test_q82_persist.py",
    "test_q82_batch_gif.py",
    "test_q85_delete_transition.py",
    "test_q88.py",
    "test_q92.py",
    "test_q95_stylepack_preset.py",
    "test_q102_console_badge.py",
    "test_q103_folder_group.py",
    "test_q104_home_stitch.py",
    "test_q108_language_capability.py",
    "test_q109_mms_align.py",
    "test_q110_import_async.py",
    "test_q200_batch_audio_tab.py",
})

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FFMPEG = os.path.join(_REPO, "runtime", "ffmpeg")
_CACHE: dict[str, dict] = {}


@dataclass(frozen=True)
class TestSpec:
    item_name: str
    junit_key: str


def child_env(base=None) -> dict:
    env = dict(base or os.environ)
    env[ENV] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONPATH"] = _REPO + os.pathsep + env.get("PYTHONPATH", "")
    # Other test modules set CUTORA_GPU_FX=0 at import; that must not leak
    # into a child that expects a local backend (q181).
    env.pop("CUTORA_GPU_FX", None)
    session_ud = (env.get("CUTORA_PYTEST_SESSION_UD") or "").strip()
    if session_ud:
        env["CUTORA_USER_DATA"] = session_ud
    path = env.get("PATH", "")
    if os.path.normcase(_FFMPEG) not in os.path.normcase(path):
        env["PATH"] = _FFMPEG + os.pathsep + path
    return env


_QT_PRODUCT_PREFIXES = (
    "PySide6",
    "cutora.batch.script_stitcher",
    "cutora.render.readers",
    "cutora.render.frame_renderer",
    "cutora.render.parallel",
    "cutora.render.render_worker",
    "cutora.asr.align_worker",
    "cutora.asr.detect_worker",
    "cutora.audio.waveform",
    "cutora.api.bridge",
)


def _name_pulls_qt(name: str) -> bool:
    if not name:
        return False
    if name == "PySide6" or name.startswith("PySide6."):
        return True
    if "script_stitcher" in name:
        return True
    for prefix in _QT_PRODUCT_PREFIXES:
        if name == prefix or name.startswith(prefix if prefix.endswith(".") else prefix + "."):
            return True
    return False


def _is_qt_import(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(_name_pulls_qt(a.name) for a in node.names)
    if isinstance(node, ast.ImportFrom):
        mod = node.module or ""
        if _name_pulls_qt(mod):
            return True
        if mod == "cutora.batch":
            return any(a.name == "script_stitcher" for a in node.names)
        if mod == "cutora.render":
            return any(
                a.name in ("readers", "frame_renderer", "parallel", "render_worker")
                for a in node.names
            )
        if mod == "cutora.asr":
            return any(a.name in ("align_worker", "detect_worker") for a in node.names)
        if mod.startswith("cutora.ui"):
            return True
    return False


def module_level_qt_import(path) -> bool:
    """True if importing this test module would load PySide6 into the parent."""
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    return any(_is_qt_import(node) for node in tree.body)


def iter_test_specs(path) -> list[TestSpec]:
    tree = ast.parse(open(path, encoding="utf-8").read())
    specs: list[TestSpec] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            specs.append(TestSpec(node.name, node.name))
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if (
                    isinstance(child, ast.FunctionDef)
                    and child.name.startswith("test_")
                ):
                    key = "%s::%s" % (node.name, child.name)
                    specs.append(TestSpec(key, key))
    return specs


def _junit_key(classname: str, name: str) -> str:
    short = classname.rsplit(".", 1)[-1]
    if short and short[0].isupper():
        return "%s::%s" % (short, name)
    return name


def _junit_body(node) -> str:
    """Assertion text from a junit <failure> or <error> element."""
    if node is None:
        return ""
    message = (node.get("message") or "").strip()
    body = (node.text or "").strip()
    if message and body and body not in message:
        return (message + "\n" + body)[:8000]
    return (message or body)[:8000]


def parse_junit(path: str) -> dict[str, str]:
    """Map junit_key → 'passed' | 'failed' | 'skipped' | 'error'.

    Failure and error nodes also store their message under
    ``__detail__:<key>`` so the parent log shows the assertion, not only
    ``isolated child failed``.
    """
    out: dict[str, str] = {}
    if not os.path.isfile(path):
        return out
    root = ET.parse(path).getroot()
    suites = root.findall("testsuite")
    if root.tag == "testsuite":
        suites = [root]
    for suite in suites:
        for case in suite.findall("testcase"):
            key = _junit_key(case.get("classname") or "", case.get("name") or "")
            skipped = case.find("skipped")
            failure = case.find("failure")
            error = case.find("error")
            if skipped is not None:
                out[key] = "skipped"
            elif failure is not None:
                out[key] = "failed"
                out["__detail__:" + key] = _junit_body(failure)
            elif error is not None:
                out[key] = "error"
                out["__detail__:" + key] = _junit_body(error)
            else:
                out[key] = "passed"
    return out


def _decode_rc(rc: int) -> str:
    if rc == 3221226505 or rc == -1073740791:
        return "0xC0000409 STATUS_STACK_BUFFER_OVERRUN"
    if rc == 3221225477 or rc == -1073741819:
        return "0xC0000005 ACCESS_VIOLATION"
    if rc < 0:
        return "%s (0x%08X)" % (rc, rc & 0xFFFFFFFF)
    return str(rc)


class IsolatedFailure(Exception):
    """Child pytest failed or native-aborted."""


def _isolated_detail(results: dict, junit_key: str) -> str:
    """Junit assertion text for this node, including parametrized ids."""
    chunks = []
    exact = results.get("__detail__:" + junit_key)
    if exact:
        chunks.append(exact)
    prefix = "__detail__:" + junit_key + "["
    for key, text in results.items():
        if key.startswith(prefix) and text and text not in chunks:
            chunks.append("%s\n%s" % (key[len("__detail__:"):], text))
    return "\n".join(chunks)


def run_isolated_file(relpath: str, timeout: int = 600) -> dict:
    cached = _CACHE.get(relpath)
    if cached is not None:
        return cached
    tmp = tempfile.mkdtemp(prefix="cutora_qt_iso_")
    junit = os.path.join(tmp, "junit.xml")
    log_path = os.path.join(tmp, "child.txt")
    cmd = [
        sys.executable, "-m", "pytest", relpath,
        "-q", "--tb=short", "--junitxml", junit,
        "-p", "no:cacheprovider",
    ]
    try:
        proc = subprocess.run(
            cmd, cwd=_REPO, env=child_env(), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        text = (proc.stdout or "") + "\n" + (proc.stderr or "")
        rc = proc.returncode
    except subprocess.TimeoutExpired as exc:
        # One hang must not restart the child for every test in the file.
        text = "%s\n%s\n" % (exc.stdout or "", exc.stderr or "")
        text += "isolated child timed out after %ss: %s" % (timeout, " ".join(cmd))
        rc = 124
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    results = parse_junit(junit)
    results["__rc__"] = str(rc)
    results["__log__"] = log_path
    results["__out__"] = text[-4000:]
    results["__cmd__"] = " ".join(cmd)
    _CACHE[relpath] = results
    return results


class IsolatedItem(pytest.Item):
    def __init__(self, name, parent, spec: TestSpec, relpath: str):
        super().__init__(name, parent)
        self.spec = spec
        self.relpath = relpath

    def runtest(self):
        results = run_isolated_file(self.relpath)
        rc = int(results["__rc__"])
        status = results.get(self.spec.junit_key)
        if status is None:
            prefix = self.spec.junit_key + "["
            param_keys = [
                k for k in results
                if k.startswith(prefix) and not k.startswith("__")]
            if param_keys:
                if any(results[k] == "failed" for k in param_keys):
                    status = "failed"
                elif any(results[k] == "error" for k in param_keys):
                    status = "error"
                elif all(results[k] == "skipped" for k in param_keys):
                    status = "skipped"
                elif all(results[k] in ("passed", "skipped") for k in param_keys):
                    status = "passed"
        if status == "passed" or (status == "skipped" and rc in (0, 1)):
            if status == "skipped":
                pytest.skip("skipped in isolated child")
            return
        if rc not in (0, 1, 2, 3, 4, 5):
            raise IsolatedFailure(
                "isolated child native-aborted rc=%s\n%s\n%s"
                % (_decode_rc(rc), results["__cmd__"], results["__out__"])
            )
        if status in (None, "failed", "error"):
            detail = _isolated_detail(results, self.spec.junit_key)
            raise IsolatedFailure(
                "isolated child %s for %s (rc=%s)\n%s\n%s"
                % (status or "missing", self.spec.junit_key, rc,
                   results["__cmd__"], detail or results["__out__"])
            )
        raise IsolatedFailure("isolated child status=%s rc=%s" % (status, rc))

    def repr_failure(self, excinfo):
        if excinfo.errisinstance(IsolatedFailure):
            return str(excinfo.value)
        return super().repr_failure(excinfo)

    def reportinfo(self):
        return self.path, None, self.name


class IsolatedModule(pytest.Module):
    def _getobj(self):
        mod = types.ModuleType(self.name)
        mod.__file__ = str(self.path)
        return mod

    def collect(self):
        relpath = os.path.relpath(str(self.path), _REPO).replace("\\", "/")
        for spec in iter_test_specs(self.path):
            yield IsolatedItem.from_parent(
                self, name=spec.item_name, spec=spec, relpath=relpath,
            )
