# -*- coding: utf-8 -*-
"""T03c polish: toolbar, batch menu, media-bin, theme — grows per section."""
from __future__ import annotations

import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")

BATCH_MENU_ORDER = [
    "片段智能拼接…",
    "片段散落…",
    "批量加载…",
    "中央监视器…",
    "---",
    "从选中视频拷贝字幕样式",
    "从选中视频共享轨道…",
    "---",
    "工程队列…",
    "复制署名到队列每条视频",
    "配方：工程队列署名…",
    "自动拆分工程…",
    "---",
    "开始录制配方",
    "停止录制配方",
    "套用配方到文件夹…",
]


def _qt_child(code: str, timeout: int = 180) -> str:
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONUNBUFFERED", "1")
    proc = subprocess.run(
        [VENV_PY, "-c", code],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    assert proc.returncode == 0, out
    assert "ok" in (proc.stdout or ""), out
    return proc.stdout or ""


def test_s1_batch_menu_order_in_source():
    src = open(
        os.path.join(REPO, "cutora", "ui", "main_window.py"), encoding="utf-8"
    ).read()
    start = src.find('batch_menu = menu.addMenu(tr("批量"))')
    assert start != -1
    chunk = src[start:start + 4000]
    pos = 0
    for title in BATCH_MENU_ORDER:
        if title == "---":
            needle = "setSeparator(True)"
        else:
            needle = title
        at = chunk.find(needle, pos)
        assert at != -1, needle
        pos = at + 1
    assert "open_volume_master_dialog" not in src
    assert 'addMenu(tr("音频"))' not in src
    assert "批量配乐…" not in chunk
    assert "音量总控…" not in chunk
    assert 'top_layout.addWidget(self.chk_gpu)' not in src
    assert 'top_layout.addWidget(self.btn_batch_sync)' not in src
    assert 'top_layout.addWidget(self.spin_src_vol)' not in src
    assert 'top_layout.addWidget(self.btn_batch_intro)' not in src
    assert "GPU加速" in src  # hidden state widget still exists


def test_s1_toolbar_widgets_and_batch_menu():
    _qt_child(
        "from cutora.core.runtime import init_torch_runtime\n"
        "init_torch_runtime(render_child=False)\n"
        "from PySide6.QtWidgets import QApplication, QAbstractButton, QDialog, QMenu\n"
        "from cutora.app import apply_ui_locale\n"
        "from cutora.ui.main_window import MainWindow\n"
        "from cutora.ui.settings_dialog import SettingsDialog\n"
        "apply_ui_locale()\n"
        "app = QApplication.instance() or QApplication(['t03c-s1'])\n"
        "win = MainWindow()\n"
        "assert not hasattr(win, 'pro_toolbar')\n"
        "assert not hasattr(win, 'progress_toolbar')\n"
        "assert not hasattr(win, 'btn_batch_export')\n"
        "assert win.act_bexp is not None\n"
        "assert win.act_bexp.text() == '批量导出'\n"
        "exp = None\n"
        "for act in win.menuBar().actions():\n"
        "    if act.text() == '导出':\n"
        "        exp = act.menu()\n"
        "        break\n"
        "assert exp is not None\n"
        "exp_titles = [a.text() for a in exp.actions() if not a.isSeparator()]\n"
        "assert '批量导出' in exp_titles, exp_titles\n"
        "assert '导出设置' in exp_titles, exp_titles\n"
        "assert not win.chk_gpu.isVisibleTo(win)\n"
        "batch = None\n"
        "for act in win.menuBar().actions():\n"
        "    if act.text() == '批量':\n"
        "        batch = act.menu()\n"
        "        break\n"
        "assert batch is not None\n"
        "titles = []\n"
        "for act in batch.actions():\n"
        "    titles.append('---' if act.isSeparator() else act.text())\n"
        "expect = %r\n"
        "assert titles == expect, titles\n"
        "dlg = SettingsDialog(win, initial_tab=2)\n"
        "assert dlg.chk_gpu is not None\n"
        "assert '性能' in [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]\n"
        "print('ok')\n"
        "from cutora.core.runtime import shutdown_qapplication\n"
        "shutdown_qapplication()\n"
        % BATCH_MENU_ORDER
    )


def test_s2_bin_targets_copy_style_undo_and_menu_text():
    _qt_child(
        "from cutora.core.runtime import init_torch_runtime\n"
        "init_torch_runtime(render_child=False)\n"
        "from PySide6.QtCore import QPoint, Qt\n"
        "from PySide6.QtWidgets import QApplication, QMenu\n"
        "from cutora.app import apply_ui_locale\n"
        "from cutora.ui.main_window import MainWindow\n"
        "QMenu.exec = lambda *a, **k: None\n"
        "import cutora.ui.panels.media_list as _ml\n"
        "_ml.QMenu.exec = lambda *a, **k: None\n"
        "apply_ui_locale()\n"
        "app = QApplication.instance() or QApplication(['t03c-s2'])\n"
        "win = MainWindow()\n"
        "for name in ('T007.mp4', 'T008.mp4', 'T009.mp4'):\n"
        "    win.file_list.addItem(name)\n"
        "    win.tasks.append({'video': name, 'text': '', 'style': {'fontsize': 12}, 'segments': []})\n"
        "win.curr_idx = 0\n"
        "win.tasks[0]['style'] = {'fontsize': 40}\n"
        "assert win.file_list.selectionMode() == __import__('PySide6.QtWidgets', fromlist=['QAbstractItemView']).QAbstractItemView.SelectionMode.ExtendedSelection\n"
        "checked = win.resolve_bin_targets()\n"
        "assert checked == [0, 1, 2], checked\n"
        "for it in win.file_list._get_all_leaves():\n"
        "    it.setCheckState(0, Qt.CheckState.Unchecked)\n"
        "assert win.resolve_bin_targets() == []\n"
        "win.file_list.item(1).setSelected(True)\n"
        "win.file_list.item(2).setSelected(True)\n"
        "assert sorted(win.resolve_bin_targets()) == [1, 2]\n"
        "menu = QMenu()\n"
        "act_s, act_h = win.add_bin_batch_menu_actions(menu, win.file_list.item(0))\n"
        "assert act_s.text() == '共享字幕风格（2 次共享）', act_s.text()\n"
        "assert act_h.text() == '共享轨道（2 次共享）…', act_h.text()\n"
        "assert act_s.isEnabled()\n"
        "for it in win.file_list.selectedItems():\n"
        "    it.setSelected(False)\n"
        "menu2 = QMenu()\n"
        "a2, h2 = win.add_bin_batch_menu_actions(menu2, win.file_list.item(0))\n"
        "assert not a2.isEnabled()\n"
        "assert '请先勾选或选中目标视频' in a2.toolTip()\n"
        "win.file_list.selectAll()\n"
        "assert len(win.file_list.selectedItems()) == 3\n"
        "win.push_undo('baseline')\n"
        "win.push_undo('before copy')\n"
        "n = win.copy_subtitle_style(0, [1, 2], None)\n"
        "assert n == 2\n"
        "assert win.tasks[1]['style']['fontsize'] == 40\n"
        "snap = win.history_mgr.history[win.history_mgr.current_index]['state']\n"
        "assert snap['tasks'][1]['style']['fontsize'] == 12\n"
        "win.tasks[1]['style'] = snap['tasks'][1]['style']\n"
        "assert win.tasks[1]['style']['fontsize'] == 12\n"
        "win.file_list.show_context_menu = lambda *a, **k: None\n"
        "win.file_list.show_context_menu(QPoint(8, 12))\n"
        "print('ok')\n"
        "from cutora.core.runtime import shutdown_qapplication\n"
        "shutdown_qapplication()\n"
    )


def test_s3_s4_progress_and_word_appear():
    _qt_child(
        "from cutora.core.runtime import init_torch_runtime\n"
        "init_torch_runtime(render_child=False)\n"
        "from PySide6.QtWidgets import QApplication\n"
        "from cutora.app import apply_ui_locale\n"
        "from cutora.ui.main_window import MainWindow\n"
        "from cutora.ui.widgets.dock_panels import CollapsibleSection\n"
        "apply_ui_locale()\n"
        "app = QApplication.instance() or QApplication(['t03c-s34'])\n"
        "win = MainWindow()\n"
        "win.show()\n"
        "win.show_editor()\n"
        "assert win.prog_frame.isHidden()\n"
        "assert win.prog_main is not None\n"
        "win.update_progress_value(45)\n"
        "assert not win.prog_frame.isHidden()\n"
        "assert '45%' in win.prog_label.text()\n"
        "assert '识别' in win.prog_label.text()\n"
        "win.tabs.setCurrentWidget(win.sub_panel)\n"
        "titles = [s._title_label.text() for s in win.sub_panel.findChildren(CollapsibleSection) if s.isVisible()]\n"
        "assert '基本排版' in titles, titles\n"
        "assert '配色方案' in titles, titles\n"
        "assert '字幕排版' in titles, titles\n"
        "assert '发光' in titles, titles\n"
        "assert '投影' in titles, titles\n"
        "p = win.sub_panel\n"
        "assert p.sub_word_mode is not None\n"
        "win.set_ui_mode('pro', persist=False)\n"
        "assert win.prog_frame.isHidden()\n"
        "print('ok')\n"
        "from cutora.core.runtime import shutdown_qapplication\n"
        "shutdown_qapplication()\n"
    )


def test_s5_s6_s62_dock_guard_timeline_defaults():
    _qt_child(
        "from cutora.core.runtime import init_torch_runtime\n"
        "init_torch_runtime(render_child=False)\n"
        "from PySide6.QtWidgets import QApplication\n"
        "from cutora.app import apply_ui_locale\n"
        "from cutora.ui.main_window import MainWindow, _DockEdgeHandle\n"
        "from cutora.render.export_guard import guard_window_class\n"
        "from cutora.model.timeline import Timeline\n"
        "from cutora.model.timeline_model import Track\n"
        "apply_ui_locale()\n"
        "app = QApplication.instance() or QApplication(['t03c-s562'])\n"
        "win = MainWindow()\n"
        "from cutora.ui.theme.tokens import DOCK_RIGHT_MAX, DOCK_RIGHT_MIN\n"
        "assert win.dock_right.minimumWidth() == DOCK_RIGHT_MIN\n"
        "assert win.dock_right.maximumWidth() == DOCK_RIGHT_MAX\n"
        "for dock in (win.dock_left, win.dock_right, win.dock_timeline):\n"
        "    h = dock.titleBarWidget()\n"
        "    assert isinstance(h, _DockEdgeHandle)\n"
        "    assert h.height() == 4\n"
        "GuardWindow = guard_window_class()\n"
        "gw = GuardWindow(__import__('tempfile').mktemp())\n"
        "assert gw.minimumWidth() >= 380\n"
        "assert gw.minimumHeight() >= 190\n"
        "assert '白板' in gw.lbl_hint.toolTip()\n"
        "assert '强关' in gw.lbl_hint.toolTip()\n"
        "assert gw.btn_stop.text() == '停止导出'\n"
        "assert '强关' in gw.btn_stop.toolTip()\n"
        "from cutora.ui.main_window import get_or_create_track\n"
        "tl = Timeline(1080, 1920, 30)\n"
        "get_or_create_track(tl, 'video')\n"
        "get_or_create_track(tl, 'subs')\n"
        "get_or_create_track(tl, 'audio')\n"
        "types = [t.type for t in tl.tracks]\n"
        "assert types.count('video') == 1, types\n"
        "assert types.count('subs') == 1, types\n"
        "assert types.count('audio') == 1, types\n"
        "qss = open(__import__('os').path.join(%r, 'resources', 'theme', 'theme_dark.qss'), encoding='utf-8').read()\n"
        "assert 'QMainWindow::separator' in qss\n"
        "print('ok')\n"
        "from cutora.core.runtime import shutdown_qapplication\n"
        "shutdown_qapplication()\n"
        % REPO.replace("\\", "\\\\")
    )


def test_s8_apply_theme_switches_tokens_and_qss():
    _qt_child(
        "from cutora.core.runtime import init_torch_runtime\n"
        "init_torch_runtime(render_child=False)\n"
        "from PySide6.QtWidgets import QApplication\n"
        "from cutora.app import apply_ui_locale\n"
        "from cutora.ui.main_window import MainWindow\n"
        "from cutora.ui.theme.apply import apply_theme\n"
        "from cutora.ui.theme.tokens import ACCENT, current, load_theme\n"
        "from cutora.ui.settings_dialog import SettingsDialog\n"
        "apply_ui_locale()\n"
        "app = QApplication.instance() or QApplication(['t03c-s8'])\n"
        "win = MainWindow()\n"
        "try:\n"
        "    apply_theme('amethyst')\n"
        "    assert current().theme_id == 'amethyst'\n"
        "    assert current().accent != ACCENT\n"
        "    qss = app.styleSheet()\n"
        "    assert current().accent.lower() in qss.lower()\n"
        "    dlg = SettingsDialog(win, initial_tab=0)\n"
        "    assert dlg.tabs.tabText(0) == '外观'\n"
        "finally:\n"
        "    apply_theme('roma-gold')\n"
        "assert current().accent == ACCENT\n"
        "print('ok')\n"
        "from cutora.core.runtime import shutdown_qapplication\n"
        "shutdown_qapplication()\n"
    )


def test_s_b_toolbar_progress_and_bexp_text():
    _qt_child(
        "from cutora.core.runtime import init_torch_runtime\n"
        "init_torch_runtime(render_child=False)\n"
        "from PySide6.QtCore import Qt\n"
        "from PySide6.QtWidgets import QApplication, QToolBar\n"
        "from PySide6.QtTest import QTest\n"
        "from cutora.app import apply_ui_locale\n"
        "from cutora.ui.main_window import MainWindow\n"
        "apply_ui_locale()\n"
        "app = QApplication.instance() or QApplication(['t03c-sb'])\n"
        "win = MainWindow()\n"
        "win.show()\n"
        "win.resize(1600, 900)\n"
        "win.show_editor()\n"
        "win.set_ui_mode('pro', persist=False)\n"
        "app.processEvents()\n"
        "vis = [tb.objectName() for tb in win.findChildren(QToolBar) if tb.isVisible() and tb.objectName() != 'pageBar']\n"
        "assert vis == [], vis\n"
        "assert win.prog_frame.isHidden()\n"
        "win.update_progress_value(0)\n"
        "assert not win.prog_frame.isHidden()\n"
        "win.update_progress_value(100)\n"
        "assert win.prog_label.text() == '完成'\n"
        "QTest.qWait(1600)\n"
        "assert win.prog_frame.isHidden()\n"
        "win.show_progress_error('自动字幕失败：nope')\n"
        "assert not win.prog_frame.isHidden()\n"
        "assert 'nope' in win.prog_label.text()\n"
        "assert win.prog_label.property('mode') == 'err'\n"
        "QTest.mouseClick(win.prog_frame, Qt.MouseButton.LeftButton)\n"
        "assert win.prog_frame.isHidden()\n"
        "win._set_batch_export_text('停止批量导出')\n"
        "assert win.act_bexp.text() == '停止批量导出'\n"
        "win.update_batch_export_btn_text()\n"
        "assert win.act_bexp.text() in ('批量导出', '无可导出项')\n"
        "print('ok')\n"
        "from cutora.core.runtime import shutdown_qapplication\n"
        "shutdown_qapplication()\n"
    )


def test_r3_preview_tools_visible_and_menus():
    _qt_child(
        "from cutora.core.runtime import init_torch_runtime\n"
        "init_torch_runtime(render_child=False)\n"
        "from PySide6.QtWidgets import QApplication\n"
        "from cutora.app import apply_ui_locale\n"
        "from cutora.ui.main_window import MainWindow\n"
        "apply_ui_locale()\n"
        "app = QApplication.instance() or QApplication(['t03c-r3-btns'])\n"
        "win = MainWindow()\n"
        "win.show()\n"
        "win.show_editor()\n"
        "app.processEvents()\n"
        "names = ('btn_ai', 'btn_exp', 'btnAddText', 'btnAddImage')\n"
        "for name in names:\n"
        "    btn = getattr(win, name)\n"
        "    assert btn.isVisible(), (name, btn.isVisible(), btn.parentWidget())\n"
        "    assert not btn.icon().isNull(), name\n"
        "assert getattr(win, 'btnAddAudio', None) is None\n"
        "assert getattr(win, 'mixer_bar', None) is not None\n"
        "assert win.mixer_bar.isVisible()\n"
        "from PySide6.QtWidgets import QSlider\n"
        "for role in ('main', 'bgm', 'sfx'):\n"
        "    sl = win.findChild(QSlider, 'roleFader_%s' % role)\n"
        "    assert sl is not None and sl.isVisible(), role\n"
        "    cluster = sl.parent()\n"
        "    assert cluster is not None and cluster.maximumWidth() <= 90, (role, cluster.maximumWidth())\n"
        "assert not win.btn_exp_stop.icon().isNull()\n"
        "assert not win.btn_exp_stop.isVisible()\n"
        "win.is_exporting = True\n"
        "win.update_ui_state()\n"
        "assert win.btn_exp_stop.isVisible()\n"
        "assert win.preview_tools.parent() is win.editor_host\n"
        "have = {(a.text() or '').strip() for a in win.findChildren(__import__('PySide6.QtGui', fromlist=['QAction']).QAction)}\n"
        "assert '添加文本' in have\n"
        "assert '添加图片' in have\n"
        "assert '添加配乐…' in have\n"
        "assert '添加背景音乐' not in have\n"
        "assert '音频' not in [a.text() for a in win.menuBar().actions()]\n"
        "assert win.act_add_text.shortcut().toString() == 'Ctrl+T'\n"
        "print('ok')\n"
        "from cutora.core.runtime import shutdown_qapplication\n"
        "shutdown_qapplication()\n"
    )


def test_r3_text_track_and_prune():
    _qt_child(
        "from cutora.core.runtime import init_torch_runtime\n"
        "init_torch_runtime(render_child=False)\n"
        "from PySide6.QtWidgets import QApplication\n"
        "from cutora.app import apply_ui_locale\n"
        "from cutora.ui.main_window import (\n"
        "    MainWindow, pick_or_create_overlay_track, prune_padded_tracks, track_is_empty)\n"
        "from cutora.model.timeline import Timeline\n"
        "from cutora.model.timeline_model import Track, Clip\n"
        "apply_ui_locale()\n"
        "app = QApplication.instance() or QApplication(['t03c-r3-tr'])\n"
        "win = MainWindow()\n"
        "win.show_editor()\n"
        "win.add_text_ov()\n"
        "assert win.curr_idx >= 0\n"
        "task = win.tasks[win.curr_idx]\n"
        "tl = task['timeline']\n"
        "assert any(t.type == 'text' for t in tl.tracks), [t.type for t in tl.tracks]\n"
        "ov = task['text_overlays'][0]\n"
        "assert ov.get('_track_id')\n"
        "first_tid = ov['_track_id']\n"
        "win.add_text_ov()\n"
        "ov2 = task['text_overlays'][1]\n"
        "assert ov2.get('_track_id') != first_tid, 'overlap should create a new track'\n"
        "assert sum(1 for t in tl.tracks if t.type == 'text') == 2\n"
        "n = len(win.tasks[win.curr_idx]['timeline'].tracks)\n"
        "win.push_undo('marker')\n"
        "win.add_timeline_track('video')\n"
        "assert len(win.tasks[win.curr_idx]['timeline'].tracks) == n + 1\n"
        "win.undo()\n"
        "assert len(win.tasks[win.curr_idx]['timeline'].tracks) == n\n"
        "task = win.tasks[win.curr_idx]\n"
        "tl = task['timeline']\n"
        "empty = Track('text')\n"
        "tl.add_track(empty)\n"
        "assert track_is_empty(empty, task)\n"
        "win.remove_timeline_track(empty)\n"
        "assert empty not in tl.tracks\n"
        "pad = Timeline(1080, 1920, 30)\n"
        "v1 = Track('video'); v1.clips.append(Clip('s', 0, 1, 0)); pad.add_track(v1)\n"
        "for _ in range(4):\n"
        "    pad.add_track(Track('video'))\n"
        "for _ in range(4):\n"
        "    pad.add_track(Track('image'))\n"
        "for _ in range(3):\n"
        "    pad.add_track(Track('text'))\n"
        "pad.add_track(Track('subs'))\n"
        "a1 = Track('audio'); a1.clips.append(Clip('s', 0, 1, 0)); pad.add_track(a1)\n"
        "pad.add_track(Track('audio')); pad.add_track(Track('audio'))\n"
        "prune_padded_tracks(pad, {'timeline': pad, 'text_overlays': [], 'image_overlays': [], 'segments': []})\n"
        "types = [t.type for t in pad.tracks]\n"
        "assert types.count('video') == 1, types\n"
        "assert types.count('image') == 0, types\n"
        "assert types.count('text') == 0, types\n"
        "assert types.count('audio') == 1, types\n"
        "assert types.count('subs') == 0, types\n"
        "from cutora.ui.timeline.header import TrackHeaderPanel\n"
        "assert TrackHeaderPanel.BTN_DEFS[-1][2] == '导入素材到此轨'\n"
        "print('ok')\n"
        "from cutora.core.runtime import shutdown_qapplication\n"
        "shutdown_qapplication()\n"
    )


def test_r3_separator_qss_idle_is_bg0():
    """Idle splitter grip is bg0, 8px. Width is the rendered token, not a raw literal.

    Q46 replaced ``width: 8px`` with ``width: {{ space_2 }}px`` (space_2 is 8).
    Asserting the template literal misses a grip that renders as 12px.
    """
    src = open(
        os.path.join(REPO, "cutora", "ui", "theme", "theme_dark.qss.j2"),
        encoding="utf-8",
    ).read()
    chunk = src[src.find("QMainWindow::separator") : src.find("QMainWindow::separator") + 280]
    assert "background: {{ bg0 }}" in chunk
    assert "background: {{ sep }}" not in chunk
    assert "{{ accent }}" in chunk
    from cutora.ui.theme.qss import render
    rendered = render()
    start = rendered.find("QMainWindow::separator")
    assert start >= 0
    idle = rendered[start:].split("QMainWindow::separator:hover", 1)[0]
    assert "width: 8px" in idle, idle
    assert "height: 8px" in idle, idle


def test_r4_no_ellipsis_glyph_in_ui_source():
    ui_root = os.path.join(REPO, "cutora", "ui")
    hits = []
    for dirpath, _dirs, files in os.walk(ui_root):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            text = open(path, encoding="utf-8").read()
            if "⋯" in text:
                hits.append(os.path.relpath(path, REPO))
    assert hits == [], hits


def test_r4_transport_bar_and_preset_menu():
    _qt_child(
        "from cutora.core.runtime import init_torch_runtime\n"
        "init_torch_runtime(render_child=False)\n"
        "from PySide6.QtWidgets import QApplication, QToolButton\n"
        "from cutora.app import apply_ui_locale\n"
        "from cutora.ui.main_window import MainWindow\n"
        "apply_ui_locale()\n"
        "app = QApplication.instance() or QApplication(['t03c-r4'])\n"
        "win = MainWindow()\n"
        "win.show()\n"
        "win.resize(1600, 900)\n"
        "win.show_editor()\n"
        "app.processEvents()\n"
        "assert win.preview_tools.height() == 40\n"
        "assert win.preview_tools.parent() is win.editor_host\n"
        "assert not hasattr(win, '_play_ctrl')\n"
        "assert isinstance(win.btn_start, QToolButton)\n"
        "assert win.btn_start.objectName() == 'btn_play'\n"
        "assert win.btn_start.size().width() == 32\n"
        "assert win.btn_start.isVisible()\n"
        "assert not win.btn_start.icon().isNull()\n"
        "assert win.btn_skip_start.isVisible() and not win.btn_skip_start.icon().isNull()\n"
        "assert win.btn_skip_end.isVisible() and not win.btn_skip_end.icon().isNull()\n"
        "assert win.lbl_timecode.isVisible()\n"
        "assert '/' in win.lbl_timecode.text()\n"
        "win._timeline_ms = 3450\n"
        "win._refresh_timecode()\n"
        "assert win.lbl_timecode.text().startswith('00:00:03.45')\n"
        "win.btn_skip_start.click()\n"
        "assert win._timeline_ms == 0\n"
        "win.btn_skip_end.click()\n"
        "assert win._timeline_ms == int(win.timeline.duration)\n"
        "assert win._preview_icon_specs[win.btn_start][0] == 'play'\n"
        "win.toggle_play()\n"
        "assert win._timeline_playing is True\n"
        "assert win._preview_icon_specs[win.btn_start][0] == 'pause'\n"
        "win.toggle_play()\n"
        "assert win._timeline_playing is False\n"
        "assert win._preview_icon_specs[win.btn_start][0] == 'play'\n"
        "win.resize(900, 900)\n"
        "app.processEvents()\n"
        "if win.width() <= 900:\n"
        "    assert not win.lbl_timecode.isVisible(), (win.width(), win.lbl_timecode.isVisible())\n"
        "win.resize(1600, 900)\n"
        "app.processEvents()\n"
        "assert win.lbl_timecode.isVisible(), win.width()\n"
        "assert win.preview_tools.isVisible()\n"
        "assert win.btn_start.isVisible()\n"
        "btn = win.sub_panel._menu_btn\n"
        "assert btn.text() == ''\n"
        "assert not btn.icon().isNull()\n"
        "assert btn.width() <= 32 and btn.height() <= 32\n"
        "acts = [a for a in win.sub_panel._style_menu.actions() if not a.isSeparator()]\n"
        "assert acts[0].text() == '设为默认预设'\n"
        "labels = [a.text() for a in acts]\n"
        "assert '新增预设' in labels and '导入预设' in labels\n"
        "for a in acts:\n"
        "    assert not a.icon().isNull(), a.text()\n"
        "    assert '⭐' not in a.text() and '➕' not in a.text()\n"
        "pbtn = win.prop_panel.ov_preset_menu_btn\n"
        "assert '预设' in (pbtn.text() or '')\n"
        "pacts = [a for a in win.prop_panel.ov_preset_menu.actions() if not a.isSeparator()]\n"
        "assert pacts[0].text() == '设为默认预设'\n"
        "assert all(not a.icon().isNull() for a in pacts)\n"
        "print('ok')\n"
        "from cutora.core.runtime import shutdown_qapplication\n"
        "shutdown_qapplication()\n"
    )

