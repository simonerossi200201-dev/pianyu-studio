# -*- coding: utf-8 -*-
"""On-demand align download: one language, then stitch continues."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_PINNED = frozenset(
    "fr de es nl uk pt cs ru pl hu fi el tr ca no nn sk sl ro eu ka lv tl sv".split()
)
_BLOCKED = frozenset("da hr gl vi".split())


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["align-fetch"])


def test_every_fetchable_language_has_its_own_script():
    from cutora.asr.align_fetch import all_download_scripts, download_script
    scripts = all_download_scripts()
    by_code = {row["language"]: row for row in scripts}
    assert set(by_code) == _PINNED
    for code in _BLOCKED:
        assert download_script(code) is None
    assert download_script("en") is None
    assert download_script("it") is None
    assert download_script("zh") is None
    assert download_script("auto") is None
    for row in scripts:
        assert row["id"].startswith("align-")
        assert row["repo"]
        assert len(row["revision"]) >= 12
        assert row["size_bytes"] > 0
        assert row["name_zh"]
        seen = set()
        for item in row["files"]:
            assert item["filename"] and item["filename"] not in seen
            seen.add(item["filename"])
            assert len(item["sha256"]) == 64
            assert item["size"] > 0


def test_fetch_plan_is_only_the_missing_language(monkeypatch):
    import cutora.asr.align_fetch as fetch
    monkeypatch.setattr(fetch, "weights_present", lambda row: row.get("language") != "nl")
    plan = fetch.fetch_plan("nl")
    assert plan["id"] == "align-nl"
    assert plan["language"] == "nl"
    assert plan["name_zh"] == "荷兰语"
    assert fetch.fetch_plan("de") is None
    assert fetch.fetch_plan("ru") is None
    assert fetch.fetch_plan("da") is None
    assert fetch.fetch_plan("zh") is None


def test_dialog_shows_progress_then_says_ready():
    from PySide6.QtCore import QObject, QTimer, Signal
    from PySide6.QtWidgets import QDialog
    from cutora.ui.align_fetch_dialog import AlignFetchDialog
    app = _app()
    seen = []

    class Worker(QObject):
        progress = Signal(int, int, str)
        succeeded = Signal()
        failed = Signal(str)

        def start(self):
            seen.append("start")
            QTimer.singleShot(0, self._tick)

        def _tick(self):
            self.progress.emit(600 * 1024 * 1024, 1200 * 1024 * 1024, "pytorch_model.bin")
            self.succeeded.emit()

        def cancel(self):
            seen.append("cancel")

        def isRunning(self):
            return False

        def wait(self, _ms=0):
            return True

    plan = {
        "id": "align-nl",
        "language": "nl",
        "name_zh": "荷兰语",
        "repo": "example/nl",
        "license": "Apache-2.0",
        "size_bytes": 1200 * 1024 * 1024,
        "files": [],
    }

    class Idle(QObject):
        progress = Signal(int, int, str)
        succeeded = Signal()
        failed = Signal(str)

        def start(self):
            return None

        def cancel(self):
            return None

        def isRunning(self):
            return False

        def wait(self, _ms=0):
            return True

    dlg = AlignFetchDialog(plan, worker_factory=lambda _p: Worker(), hold_ms=0)
    dlg.show()
    app.processEvents()
    for _ in range(20):
        app.processEvents()
        if dlg.result() == QDialog.DialogCode.Accepted:
            break
    assert dlg.result() == QDialog.DialogCode.Accepted
    text = "\n".join([
        dlg.title.text(), dlg.lead.text(), dlg.status.text(), dlg.percent.text(),
        dlg.windowTitle(), dlg.file_line.text(),
    ])
    assert "荷兰语" in text
    assert "请不要关闭软件" in dlg.lead.text() or "已经下载完毕" in text
    assert "100%" in dlg.percent.text()
    assert "已经下载完毕" in dlg.title.text()
    assert "已经下载完毕" in dlg.status.text()
    assert seen == ["start"]
    # The lead is replaced once the download finishes. The opening copy
    # is what the user sees while bytes are moving, so replay one tick.
    opening = AlignFetchDialog(plan, worker_factory=lambda _p: Idle(), hold_ms=60000)
    opening.show()
    app.processEvents()
    opening._on_progress(100 * 1024 * 1024, 1200 * 1024 * 1024, "pytorch_model.bin")
    assert "请不要关闭软件" in opening.lead.text()
    assert "只下载这一种语言" in opening.lead.text()
    assert "pytorch_model.bin" in opening.file_line.text()
    assert opening.percent.text() != "0%"
    assert opening.prog.ratio > 0
    assert "MB" in opening.status.text()
    opening.close()


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


def _select(band, code):
    for i in range(band.cmb_lang.count()):
        if band.cmb_lang.itemData(i) == code:
            band.cmb_lang.setCurrentIndex(i)
            return
    raise AssertionError(code)


def test_home_start_downloads_that_language_then_stitches(tmp_path, monkeypatch):
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QDialog
    from cutora.ui.welcome import WelcomePage
    import cutora.asr.align_fetch as fetch
    import cutora.ui.align_fetch_dialog as dlgmod

    app = _app()
    folder = tmp_path / "一组"
    folder.mkdir()
    (folder / "01a.mp4").write_bytes(b"x")
    page = WelcomePage()
    page.show()
    app.processEvents()
    band = page.home_stitch
    _drop(band, [str(folder)])
    key = next(iter(band.groups()))
    band._scripts = {key: "一行文案"}
    band._pasted = True
    for i in range(band.cmb_lang.count()):
        if band.cmb_lang.itemData(i) == "nl":
            band.cmb_lang.setCurrentIndex(i)
            break
    else:
        raise AssertionError("nl missing from picker")

    def only_nl(code):
        if code != "nl":
            return None
        return {
            "id": "align-nl", "language": "nl", "name_zh": "荷兰语",
            "repo": "example/nl", "license": "Apache-2.0",
            "size_bytes": 10, "files": [],
        }

    monkeypatch.setattr(fetch, "fetch_plan", only_nl)
    calls = []

    class Dlg:
        def __init__(self, plan, parent=None, **_k):
            calls.append(dict(plan))

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(dlgmod, "AlignFetchDialog", Dlg)

    class Sess(QObject):
        progress = Signal(int)
        log = Signal(str)
        missing = Signal(object)
        preflight_ready = Signal(object)
        analyzed = Signal(object)
        done = Signal(object)
        failed = Signal(str)

        def __init__(self):
            super().__init__()
            self.started = 0
            self.fetcher = None

        def busy(self):
            return False

        def set_align_fetcher(self, fn):
            self.fetcher = fn

        def set_job(self, *args):
            self.job = args

        def start_preflight(self, *args, **kwargs):
            self.started += 1
            return None

    sess = Sess()
    band.bind_session(sess)
    notes = []
    real_log = band._on_log

    def grab(text):
        notes.append(str(text))
        real_log(text)

    band._on_log = grab
    band.btn_start.click()
    app.processEvents()
    assert [row["language"] for row in calls] == ["nl"]
    assert calls[0]["id"] == "align-nl"
    assert sess.started == 1
    assert any("已经下载完毕" in line and "开始拼接" in line for line in notes)


def test_home_start_does_not_stitch_when_download_is_cancelled(tmp_path, monkeypatch):
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QDialog
    from cutora.ui.welcome import WelcomePage
    import cutora.asr.align_fetch as fetch
    import cutora.ui.align_fetch_dialog as dlgmod

    app = _app()
    folder = tmp_path / "一组"
    folder.mkdir()
    (folder / "01a.mp4").write_bytes(b"x")
    page = WelcomePage()
    page.show()
    app.processEvents()
    band = page.home_stitch
    _drop(band, [str(folder)])
    key = next(iter(band.groups()))
    band._scripts = {key: "一行文案"}
    band._pasted = True
    _select(band, "ru")

    def only_ru(code):
        if code != "ru":
            return None
        return {
            "id": "align-ru", "language": "ru", "name_zh": "俄语",
            "repo": "example/ru", "license": "Apache-2.0",
            "size_bytes": 10, "files": [],
        }

    monkeypatch.setattr(fetch, "fetch_plan", only_ru)

    class Dlg:
        def __init__(self, plan, parent=None, **_k):
            self.plan = plan

        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(dlgmod, "AlignFetchDialog", Dlg)

    class Sess(QObject):
        progress = Signal(int)
        log = Signal(str)
        missing = Signal(object)
        preflight_ready = Signal(object)
        analyzed = Signal(object)
        done = Signal(object)
        failed = Signal(str)

        def busy(self):
            return False

        def set_align_fetcher(self, fn):
            self.fetcher = fn

        def set_job(self, *args):
            self.job = args

        def start_preflight(self, *args, **kwargs):
            raise AssertionError("cancelled download must not start stitch")

    band.bind_session(Sess())
    band.btn_start.click()
    app.processEvents()
    assert "没有下载完成" in band.lbl_status.text()
    assert "俄语" in band.lbl_status.text()


def test_worker_asks_for_one_language_then_continues(tmp_path, monkeypatch):
    import cutora.asr.align_fetch as fetch
    from cutora.batch.script_stitcher import AnalyzeWorker

    state = {"n": 0}

    def plan(code):
        if code != "nl":
            return None
        state["n"] += 1
        if state["n"] == 1:
            return {"id": "align-nl", "language": "nl", "name_zh": "荷兰语"}
        return None

    monkeypatch.setattr(fetch, "fetch_plan", plan)
    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"a")
    worker = AnalyzeWorker(
        {"1": [str(clip)]}, {"1": "hello"}, lambda: None, lambda *a, **k: None,
        language="nl", edge_refine=False, out_dir="")
    asked = []

    def slot(code):
        asked.append(code)
        worker.align_fetch_finished(True)

    worker._align_fetcher = lambda code: True
    worker.need_align.connect(slot)
    worker._ensure_align_downloaded("nl")
    assert asked == ["nl"]
    worker._ensure_align_downloaded("de")
