@echo off
rem Re-run just the test files touched by the last fixes (one process per file).
rem Log: docs\plan\queue\_log\_retest.log   (the full-run log is left alone)
rem Pure ASCII on purpose.
title CUTORA retest
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PY=%~dp0runtime\venv\Scripts\python.exe"
if not exist "%PY%" (
  echo  venv python not found: %PY%
  pause
  exit /b 1
)
"%PY%" tools\pytest_each.py --name retest ^
  tests\test_q265_follow_title_label.py tests\test_q262_gate0_cache.py tests\test_q110_import_async.py tests\test_t18_no_dead_controls.py tests\test_q71_open.py tests\test_t03_shell.py tests\test_q213_rest.py tests\test_q212_levels.py tests\test_q211_mask_layer.py tests\test_q210_holy_preview.py tests\test_q209_isolate.py tests\test_q208_speed_curve.py tests\test_q207b_duck.py tests\test_q207a_silence.py tests\test_q206_denoise.py tests\test_q205_audio_speed.py tests\test_q202_menu_cleanup.py tests\test_q204b_fades.py tests\test_q201_scatter_tab.py tests\test_q204a_av_link.py tests\test_q200_batch_audio_tab.py tests\test_q199_stem_cache.py tests\test_q198_volume_preview.py tests\test_q197_audio_tracks.py tests\test_q196_link_image_sheet_fill.py tests\test_q194_link_sheet_fill.py tests\test_q193_lowspec.py tests\test_q192_inwindow.py tests\test_q102_console_badge.py tests\test_q118_library_names.py tests\test_q191_rooms.py tests\test_q81_batch_canvas.py tests\test_q96_scatter_dialog.py tests\test_q144_central_monitor.py tests\test_q190_title_locate.py tests\test_q120_text_batch.py tests\test_q150_locate_progress.py tests\test_q153_batch_template_column.py tests\test_q189i_timeline_cjk.py tests\test_q189h_idle_wake.py tests\test_q189g_idle_preview.py tests\test_q188_preview_decoder_pool.py tests\test_q187_gui_no_torch.py tests\test_q183_nvenc_autoprobe.py tests\test_q181_gpu_fx.py tests\test_q180_export_replay_parallel.py tests\test_q179_adjust_multi_effect.py tests\test_q178_blend_adjust.py tests\test_q177_stack_order.py tests\test_q176_track_cleanup.py tests\test_q175_playback_adjust3.py tests\test_q174_adjust_track.py tests\test_q173_monitor_layer_ops.py tests\test_q172_monitor_layer_drag.py tests\test_q171_batch_key.py tests\test_q170_font_picker.py tests\test_q169_timeline_colors.py tests\test_q53_clip_colors.py tests\test_q69_timeline.py tests\test_bug10_thumbs.py tests\test_ui_golden.py tests\test_q163_unified_stack.py tests\test_q164_combo_popup.py tests\test_q165_keyer_despill_checker.py tests\test_q166_ai_matte_batch_merge.py tests\test_q167_bezier_mask.py tests\test_q162_batch_asr.py tests\test_q85_delete_transition.py tests\test_q78_canvas_aspect.py tests\test_t23b_batch_preview.py tests\test_q160_segment_export.py tests\test_q155_backdrop.py tests\test_q156_monitor_gpu_safe.py tests\test_q158_sync_overlays.py tests\test_q159_undo_scope.py tests\test_q145b_timeline_shortcuts.py tests\test_t06b_autosave.py tests\test_q154_monitor_safety.py tests\test_q154_clip_groups.py tests\test_q154_link_groups.py tests\test_q152f_nodes.py tests\test_q152e_tracking.py tests\test_q152d2_matte_polish.py tests\test_q152d_ai_matte.py tests\test_q152b_masks_keyer.py tests\test_q153_batch_template_column.py ^
  tests\test_q120_text_batch.py ^
  tests\test_q121_text_preview.py ^
  tests\test_q152a2_keyable_motion.py ^
  tests\test_t04f_fx_panel.py ^
  tests\test_q145_canvas_shortcuts.py ^
  tests\test_q152a_keyframe_editor.py ^
  tests\test_q62_keyframes.py ^
  tests\test_q31_keyframes.py ^
  tests\test_ui_actions_smoke.py ^
  tests\test_dock_resize.py ^
  tests\test_command_bus_undo.py ^
  tests\test_q46_tokens.py ^
  tests\test_q152g_layer_panel.py ^
  tests\test_q149_layer_panel.py ^
  tests\test_q149_layer_transfer.py ^
  tests\test_q151_lens_split.py ^
  tests\test_q115_track_dnd.py ^
  tests\test_t34_split.py ^
  tests\test_q117_bg_fill.py ^
  tests\test_q130_export_perf.py ^
  tests\test_q44_overlay_fx.py ^
  tests\test_q116_split_canvas_pick.py ^
  tests\test_q86_split_feather.py ^
  tests\test_q144_central_monitor.py ^
  tests\test_q141_143_batch.py ^
  tests\test_q146_link_image.py ^
  tests\test_q81_batch_canvas.py ^
  tests\test_q84_reset.py ^
  tests\test_qw_batch.py ^
  tests\test_t19_fill_signal.py ^
  tests\test_command_portable.py ^
  tests\test_commands_registry.py ^
  tests\test_no_third_party_names.py ^
  tests\test_t03_theme.py ^
  tests\test_golden_check.py
echo.
echo ================================================================
echo  Done. Tell Claude "retest done" - log: docs\plan\queue\_log\_retest.log
echo ================================================================
pause
