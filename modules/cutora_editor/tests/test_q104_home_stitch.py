# -*- coding: utf-8 -*-
"""Q104: welcome page keeps its old controls and gains a stitch band."""
from __future__ import annotations

import os
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q104"])


def _touch(folder, name):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, "wb") as fh:
        fh.write(b"x")
    return path


def _drop(widget, paths):
    from PySide6.QtCore import QMimeData, QPointF, QUrl, Qt
    from PySide6.QtGui import QDropEvent
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
    event = QDropEvent(
        QPointF(12, 12),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.dropEvent(event)


def _page():
    from cutora.ui.welcome import WelcomePage
    page = WelcomePage()
    return page


def test_old_welcome_controls_stay():
    """Every pre-existing welcome control is still there and still clicks."""
    from PySide6.QtWidgets import QLabel, QListWidget, QPushButton
    from cutora.model.canvas import CANVAS_PRESETS
    app = _app()
    page = _page()
    page.resize(1280, 900)
    page.show()
    app.processEvents()

    presets = [(int(w), int(h)) for w, h, _lab in CANVAS_PRESETS]
    assert page.btn_portrait.objectName() == "btnPrimary"
    assert page.btn_portrait.isEnabled()
    assert "新建项目" in page.btn_portrait.text()
    assert page.btn_portrait is page._size_buttons[presets[0]]
    size_buttons = [page._size_buttons[wh] for wh in presets[1:]]
    assert len(size_buttons) == 3
    assert page.btn_custom.isEnabled()
    for btn in size_buttons + [page.btn_custom, page.btn_open, page.btn_v6, page.btn_portrait]:
        assert btn.isEnabled(), btn.text()

    got = []
    page.new_with_size.connect(lambda w, h: got.append((w, h)))
    for wh in presets:
        page._size_buttons[wh].click()
    assert got == presets
    custom = []
    page.new_custom.connect(lambda: custom.append(1))
    page.btn_custom.click()
    assert custom == [1]
    opened = []
    page.open_project.connect(lambda: opened.append(1))
    page.btn_open.click()
    assert opened == [1]
    assert "打开项目" in page.btn_open.text()
    imported = []
    page.import_v6.connect(lambda: imported.append(1))
    page.btn_v6.click()
    assert imported == [1]
    assert "AutoSub v6" in page.btn_v6.text()

    labels = [w.text() for w in page.findChildren(QLabel)]
    assert any("把视频拖到这里，将新建项目" in t for t in labels)
    # Q105: 底部 1/2/3 三张说明卡片按 Luca 要求去掉了（「没有用处」）。
    # 其余老区域一个都不许少 —— 下面那些断言仍然是这条铁律的守门员。
    assert "导入视频" not in labels
    assert "自动字幕" not in labels
    assert "导出成片" not in labels
    assert isinstance(page.recent_list, QListWidget)
    assert page.recent_list.isEnabled()
    assert page.actions_panel.isEnabled()
    # 只管左边那栏：拼接列里的「开始拼接」本来就该铺满整列。
    for btn in page.actions_panel.findChildren(QPushButton):
        assert btn.width() <= 380, (btn.text(), btn.width())

    dropped = []
    page.files_dropped.connect(lambda paths: dropped.append(list(paths)))
    _drop(page, [os.path.join(REPO, "README_INTERNAL.md")])
    assert dropped and dropped[0]


def test_drop_file_folder_and_three_folders(tmp_path):
    app = _app()
    one = tmp_path / "单条"
    _touch(str(one), "clip.mp4")
    page = _page()
    page.show()
    app.processEvents()
    leaked = []
    page.files_dropped.connect(lambda paths: leaked.append(paths))
    _drop(page.home_stitch, [str(one / "clip.mp4")])
    assert leaked == []
    assert page.home_stitch.group_count() == 1
    assert page.home_stitch.clip_count() == 1
    assert page.home_stitch.table.rowCount() == 1

    folder = tmp_path / "一整夹"
    _touch(str(folder), "a.mp4")
    _touch(str(folder), "b.mp4")
    page2 = _page()
    _drop(page2.home_stitch, [str(folder)])
    assert page2.home_stitch.group_count() == 1
    assert page2.home_stitch.clip_count() == 2
    assert page2.home_stitch.table.rowCount() == 1

    three = []
    for name in ("甲", "乙", "丙"):
        path = tmp_path / name
        _touch(str(path), "v.mp4")
        three.append(str(path))
    page3 = _page()
    _drop(page3.home_stitch, three)
    assert page3.home_stitch.group_count() == 3
    assert page3.home_stitch.clip_count() == 3
    assert page3.home_stitch.table.rowCount() == 3


def test_mixed_drop_dedups(tmp_path):
    app = _app()
    _app()
    box = tmp_path / "盒"
    _touch(str(box), "a.mp4")
    _touch(str(box), "b.mp4")
    outside = tmp_path / "外"
    loose = _touch(str(outside), "c.mp4")
    paths = [str(box), str(box / "a.mp4"), loose]
    page = _page()
    page.show()
    app.processEvents()
    _drop(page.home_stitch, paths)
    groups = page.home_stitch.group_count()
    clips = page.home_stitch.clip_count()
    assert groups == 2
    assert clips == 3
    _drop(page.home_stitch, paths + paths)
    assert page.home_stitch.group_count() == groups
    assert page.home_stitch.clip_count() == clips
    seen = []
    for members in page.home_stitch.groups().values():
        for path in members:
            key = os.path.normcase(os.path.normpath(path))
            assert key not in seen
            seen.append(key)
    assert len(seen) == 3


def test_group_mode_matches_scan(tmp_path):
    from cutora.batch.script_stitcher import scan_groups_multi
    app = _app()
    left = tmp_path / "组甲"
    _touch(str(left), "01a.mp4")
    _touch(str(left), "01b.mp4")
    _touch(str(left), "随便.mp4")
    right = tmp_path / "组乙"
    _touch(str(right), "02a.mp4")
    folders = [os.path.normpath(str(left)), os.path.normpath(str(right))]
    page = _page()
    page.show()
    app.processEvents()
    band = page.home_stitch
    _drop(band, folders)
    assert band.btn_mode_folder.isChecked()
    folder_groups, _fo, _bo, _notes = scan_groups_multi(
        folders, "", "", group_by_folder=True)
    assert band.groups() == folder_groups

    band.btn_mode_prefix.click()
    app.processEvents()
    assert band.btn_mode_prefix.isChecked()
    gm = band.ed_group_mark.text().strip()
    om = band.ed_order_mark.text().strip()
    prefix_groups, _fo, _bo, _notes = scan_groups_multi(
        folders, gm, om, group_by_folder=False)
    assert band.groups() == prefix_groups
    assert "随便.mp4" not in [
        os.path.basename(p) for paths in band.groups().values() for p in paths]

    band.btn_mode_folder.click()
    app.processEvents()
    assert band.groups() == folder_groups


def test_script_column_and_badge(tmp_path):
    from cutora.batch.script_stitcher import PasteTable
    app = _app()
    for name in ("一组", "二组"):
        _touch(str(tmp_path / name), "v.mp4")
    page = _page()
    page.show()
    app.processEvents()
    band = page.home_stitch
    _drop(band, [str(tmp_path / "一组"), str(tmp_path / "二组")])
    assert band.table.rowCount() == 2
    for row in range(2):
        assert band.table.item(row, 2).text() == "—"
    assert "已填 0" in band.btn_paste.text()
    assert "共 2" in band.btn_paste.text()

    dialog = band.make_paste_dialog()
    assert isinstance(dialog.table, PasteTable)
    dialog.table.item(0, 1).setText("只有第一组有文案")
    band.apply_paste_table(dialog.table)
    marks = [band.table.item(row, 2).text() for row in range(2)]
    assert marks.count("✓ 已有") == 1
    assert marks.count("✗ 缺") == 1
    assert "已填 1" in band.btn_paste.text()
    assert "共 2" in band.btn_paste.text()


def test_missing_opens_report_with_finder_path(monkeypatch):
    from PySide6.QtCore import QObject, Signal
    import cutora.batch.script_stitcher as stitcher
    app = _app()
    revealed = []
    report = os.path.join("D:\\missing-proof", "缺失文案清单.txt")

    def _find(_dirs):
        return report

    def _reveal(path):
        revealed.append(path)
        return True

    monkeypatch.setattr(stitcher, "find_missing_report", _find)
    monkeypatch.setattr(stitcher, "reveal_in_explorer", _reveal)

    class Fake(QObject):
        progress = Signal(int)
        log = Signal(str)
        missing = Signal(object)
        done = Signal(object)
        failed = Signal(str)
        preflight_ready = Signal(object)
        analyzed = Signal(object)

        def busy(self):
            return False

    page = _page()
    page.show()
    app.processEvents()
    band = page.home_stitch
    band.bind_session(Fake(page))
    band._session.missing.emit({
        "甲": {"missing": [{"text": "one"}]},
        "乙": {"missing": [{"text": "two"}, {"text": "three"}]},
    })
    app.processEvents()
    assert band._stack.currentWidget().objectName() == "homeStitchResult"
    assert band.missing_list.rowCount() == 2
    assert band.btn_open_missing.isEnabled()
    assert "打开缺失清单所在文件夹" in band.btn_open_missing.text()
    band.btn_open_missing.click()
    assert revealed == [report]


def test_done_opens_editor_with_tasks(tmp_path):
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QWidget
    from cutora.batch.timeline_stitch import projects_from_cutlist
    from cutora.ui.main_window import MainWindow
    from cutora.ui.panels.media_list import DraggableListWidget
    from cutora.ui.welcome import WelcomePage
    app = _app()
    cuts = {
        "G": [{"path": "a.mp4", "keep": True, "segs": [[0.0, 1.0]], "name": "a"}],
    }
    paths = projects_from_cutlist(
        cuts, str(tmp_path), text_by_group={"G": "hello from the sheet"})
    assert paths and os.path.isfile(paths[0])

    class Host(QWidget):
        def __init__(self):
            super().__init__()
            self.file_list = DraggableListWidget()
            self.tasks = []
            self.curr_idx = -1
            self.project_path = None
            self.editor_shown = False
            self.page = WelcomePage(self)

        def show_editor(self):
            self.editor_shown = True

        def _relabel_library_rows(self):
            return None

        def start_batch_ai(self):
            return None

        def _prompt_canvas_if_sources_mismatch(self):
            return None

        def _announce_text_layer_migrate(self, data):
            MainWindow._announce_text_layer_migrate(self, data)

        def apply_project_data(self, data):
            self.tasks = list(data.get("tasks") or [])
            self.curr_idx = int(data.get("curr_idx") or 0)
            MainWindow._populate_library(self, self.tasks)

        def _import_stitch_timeline_projects(self, asprojs):
            MainWindow._import_stitch_timeline_projects(self, asprojs)

        def _on_stitcher_done(self, results, auto_ai):
            MainWindow._on_stitcher_done(self, results, auto_ai)

    class Fake(QObject):
        progress = Signal(int)
        log = Signal(str)
        missing = Signal(object)
        done = Signal(object)
        failed = Signal(str)
        preflight_ready = Signal(object)
        analyzed = Signal(object)

        def busy(self):
            return False

    host = Host()
    host.show()
    app.processEvents()
    host.page.home_stitch.bind_session(Fake(host))
    host.page.home_stitch._session.done.emit({
        "kind": "timeline",
        "asproj": paths[0],
        "asprojs": paths,
        "n": 1,
    })
    app.processEvents()
    assert host.editor_shown
    assert host.tasks


def test_welcome_construct_does_not_import_stitcher():
    code = (
        "import os, sys\n"
        "os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')\n"
        "from PySide6.QtWidgets import QApplication\n"
        "app = QApplication.instance() or QApplication(['q104-mod'])\n"
        "from cutora.ui.welcome import WelcomePage\n"
        "page = WelcomePage()\n"
        "page.show()\n"
        "app.processEvents()\n"
        "bad = [k for k in ('cutora.batch.script_stitcher', 'whisperx') if k in sys.modules]\n"
        "assert not bad, bad\n"
        "print('ok')\n"
    )
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, (proc.stdout or "") + "\n" + (proc.stderr or "")
    assert "ok" in (proc.stdout or "")


def test_narrow_window_keeps_three_regions():
    from PySide6.QtWidgets import QLabel
    app = _app()
    page = _page()
    page.resize(1100, 700)
    page.show()
    app.processEvents()
    assert page.actions_panel.isVisible()
    assert page.recent_list.isVisible()
    assert page.home_stitch.isVisible()
    # Q105: 它现在是右半边的一整列，管宽度而不是管高度。
    assert page.home_stitch.minimumWidth() >= 380
    assert page.home_stitch.width() >= 380
    labels = [w.text() for w in page.findChildren(QLabel)]
    assert "导入视频" not in labels
    bar = page.scroll_area.horizontalScrollBar()
    assert bar.maximum() == bar.minimum()
    assert not bar.isVisible()


def test_plan_job_matches_dialog_outputs(tmp_path):
    from cutora.batch.script_stitcher import StitcherDialog, scan_groups_multi
    from cutora.batch.stitch_session import plan_job
    left = tmp_path / "A"
    right = tmp_path / "B"
    _touch(str(left), "01a.mp4")
    _touch(str(left), "01b.mp4")
    _touch(str(right), "02a.mp4")
    _touch(str(right), "note.txt")
    folders = [os.path.normpath(str(left)), os.path.normpath(str(right))]
    scanned, folder_of, base_of, _notes = scan_groups_multi(
        folders, "", "", group_by_folder=False)
    scripts = {key: "line %s" % key for key in scanned}
    groups = {k: v for k, v in scanned.items() if k in scripts}
    expect_dirs = {
        k: os.path.join(folder_of.get(k, folders[0]), "_stitched") for k in groups}
    expect_names = {k: base_of.get(k, k) for k in groups}

    planned = plan_job(folders, "", "", False, scripts)
    assert planned["groups"] == groups
    assert planned["out_dirs"] == expect_dirs
    assert planned["out_names"] == expect_names

    dlg = StitcherDialog(None, lambda: None, lambda *a: None, lambda *a: None)
    try:
        dlg._add_paths(folders)
        for row in range(dlg.tbl_script.rowCount()):
            key = dlg.tbl_script.item(row, 0).text()
            dlg.tbl_script.item(row, 1).setText(scripts[key])
        armed_groups, armed_dirs, armed_names, _out = dlg._arm_job(scripts, folders)
        assert armed_groups == groups
        assert armed_dirs == expect_dirs
        assert armed_names == expect_names
    finally:
        dlg.close()

    loose = tmp_path / "乱"
    _touch(str(loose), "随便起的名字.mp4")
    _touch(str(loose), "a2.mp4")
    loose_folders = [os.path.normpath(str(loose))]
    scanned_f, folder_f, base_f, _n = scan_groups_multi(
        loose_folders, "Tik", "v", group_by_folder=True)
    scripts_f = {key: "文案" for key in scanned_f}
    groups_f = {k: v for k, v in scanned_f.items() if k in scripts_f}
    expect_dirs_f = {
        k: os.path.join(folder_f.get(k, loose_folders[0]), "_stitched")
        for k in groups_f}
    expect_names_f = {k: base_f.get(k, k) for k in groups_f}
    planned_f = plan_job(loose_folders, "Tik", "v", True, scripts_f)
    assert planned_f["groups"] == groups_f
    assert planned_f["out_dirs"] == expect_dirs_f
    assert planned_f["out_names"] == expect_names_f
    assert list(planned_f["groups"]) == ["乱"]
    assert [os.path.basename(p) for p in planned_f["groups"]["乱"]] == [
        "a2.mp4", "随便起的名字.mp4"]
