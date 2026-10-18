# -*- coding: utf-8 -*-
"""Q102: log file, console window, minimize-to-badge.

Qt imports stay inside the tests. This module is listed in qt_isolate so the
parent pytest process never loads PySide6.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys


def _app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication(["q102"])
    return app


def _flush_log_files() -> None:
    for handler in logging.getLogger().handlers:
        try:
            handler.flush()
        except Exception:
            pass


def _file_handler(path: str):
    want = os.path.normcase(os.path.abspath(path))
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            have = os.path.normcase(os.path.abspath(getattr(handler, "baseFilename", "")))
            if have == want:
                return handler
    return None


def test_ring_buffer_keeps_last_5000():
    from cutora.core.log import RING_CAPACITY, RingBufferHandler
    handler = RingBufferHandler()
    assert handler.capacity == 5000
    assert RING_CAPACITY == 5000
    for i in range(6000):
        record = logging.LogRecord(
            name="q102.ring",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="n=%d" % i,
            args=(),
            exc_info=None,
        )
        handler.emit(record)
    kept = handler.records()
    assert len(kept) == 5000
    assert kept[0].getMessage() == "n=1000"
    assert kept[-1].getMessage() == "n=5999"


def test_file_handler_redacts_xai_key(tmp_path, monkeypatch):
    dest = tmp_path / "redact_ud"
    dest.mkdir()
    monkeypatch.setenv("CUTORA_USER_DATA", str(dest))
    from cutora.core.paths import refresh_cached_paths
    refresh_cached_paths()
    from cutora.core.log import log_file_path, setup_logging
    setup_logging()
    path = log_file_path()
    handler = _file_handler(path)
    assert handler is not None, path
    secret = "xai-xxxxxxxx"
    record = logging.LogRecord(
        name="q102.redact",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="carrier %s tail" % secret,
        args=(),
        exc_info=None,
    )
    handler.handle(record)
    handler.flush()
    text = open(path, encoding="utf-8").read()
    assert secret not in text
    assert "carrier *** tail" in text


def test_null_stdio_print_reaches_ring():
    from cutora.core.log import ring_buffer, setup_logging
    old_out, old_err = sys.stdout, sys.stderr
    try:
        sys.stdout = None
        sys.stderr = None
        setup_logging()
        ring = ring_buffer()
        ring.clear()
        print("hi")
        sys.stdout.flush()
        messages = [rec.getMessage() for rec in ring.records()]
        assert "hi" in messages
    finally:
        sys.stdout = old_out
        sys.stderr = old_err


def test_qt_warning_reaches_ring():
    _app()
    from PySide6.QtCore import qWarning
    from cutora.core.log import ring_buffer, setup_logging
    setup_logging()
    ring = ring_buffer()
    ring.clear()
    qWarning("boom")
    messages = [rec.getMessage() for rec in ring.records()]
    assert any("boom" in msg for msg in messages), messages


def test_badge_window_flags():
    _app()
    from PySide6.QtCore import Qt
    from cutora.ui.floating_badge import TOPMOST_MS, FloatingBadge
    badge = FloatingBadge()
    flags = badge.windowFlags()
    # Tool includes the Window bit. A plain ``flags & Tool`` stays true for any
    # window, so the whole flag has to match.
    assert (flags & Qt.WindowType.Tool) == Qt.WindowType.Tool
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert badge.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert badge._top_timer.interval() == TOPMOST_MS
    assert TOPMOST_MS == 5000
    badge.hide()
    badge.deleteLater()


def test_badge_enabled_defaults_true_when_key_missing(tmp_path, monkeypatch):
    ud = tmp_path / "fresh_settings"
    ud.mkdir()
    (ud / "app_settings.json").write_text(
        '{"export_video_mode": "compressed"}\n', encoding="utf-8")
    monkeypatch.setenv("CUTORA_USER_DATA", str(ud))
    from cutora.core.history import _load_app_settings
    from cutora.core.paths import refresh_cached_paths
    from cutora.ui.badge_prefs import KEY_ENABLED, badge_enabled
    refresh_cached_paths()
    loaded = _load_app_settings() or {}
    assert KEY_ENABLED not in loaded
    assert badge_enabled() is True


def test_badge_master_switch_hides_and_uses_taskbar(monkeypatch):
    monkeypatch.setenv("CUTORA_SKIP_ENCODER_PROBE", "1")
    from PySide6.QtTest import QTest
    from cutora.ui.badge_prefs import set_badge_enabled
    from cutora.ui.main_window import MainWindow
    from cutora.ui.settings_dialog import SettingsDialog
    set_badge_enabled(True)
    app = _app()
    win = MainWindow()
    dlg = None
    try:
        win._suppress_close_prompt = True
        win.show()
        app.processEvents()
        win.showMinimized()
        app.processEvents()
        QTest.qWait(20)
        assert win.isHidden(), "minimize with the switch on should hide the main window"
        assert win._badge.isVisible()
        dlg = SettingsDialog(win)
        dlg.chk_badge.setChecked(False)
        app.processEvents()
        assert win._badge.isHidden()
        assert win.isMinimized()
        assert not win.isHidden()
        win.showNormal()
        app.processEvents()
        win.showMinimized()
        app.processEvents()
        QTest.qWait(20)
        assert not win._badge.isVisible()
        assert win.isMinimized()
        assert not win.isHidden()
        dlg.chk_badge.setChecked(True)
        win.showNormal()
        app.processEvents()
        win.showMinimized()
        app.processEvents()
        QTest.qWait(20)
        assert win.isHidden()
        assert win._badge.isVisible()
    finally:
        if dlg is not None:
            dlg.close()
        win._suppress_close_prompt = True
        if win._badge is not None:
            win._badge.hide()
        win.close()
        app.processEvents()


def test_edge_checkbox_disables_without_changing_value():
    _app()
    from cutora.ui.badge_prefs import badge_edge_hide, set_badge_edge_hide, set_badge_enabled
    from cutora.ui.settings_dialog import SettingsDialog
    set_badge_enabled(True)
    set_badge_edge_hide(True)
    dlg = SettingsDialog(None)
    try:
        assert dlg.chk_badge_edge.isChecked()
        assert dlg.chk_badge_edge.isEnabled()
        dlg.chk_badge.setChecked(False)
        assert dlg.chk_badge_edge.isEnabled() is False
        assert dlg.chk_badge_edge.isChecked() is True
        assert badge_edge_hide() is True
        dlg.chk_badge.setChecked(True)
        assert dlg.chk_badge_edge.isEnabled() is True
        assert dlg.chk_badge_edge.isChecked() is True
        assert badge_edge_hide() is True
    finally:
        dlg.close()
        set_badge_enabled(True)


def test_minimize_shows_badge_click_restores(monkeypatch):
    monkeypatch.setenv("CUTORA_SKIP_ENCODER_PROBE", "1")
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest
    from cutora.ui.badge_prefs import set_badge_enabled
    from cutora.ui.main_window import MainWindow
    set_badge_enabled(True)
    app = _app()
    win = MainWindow()
    try:
        win._suppress_close_prompt = True
        win.show()
        app.processEvents()
        win.showMinimized()
        app.processEvents()
        QTest.qWait(20)
        badge = win._badge
        assert win.isHidden()
        assert badge.isVisible()
        local = QPointF(8, 8)
        global_pt = QPointF(badge.mapToGlobal(QPoint(8, 8)))
        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            local,
            global_pt,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        release = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            local,
            global_pt,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        badge.mousePressEvent(press)
        badge.mouseReleaseEvent(release)
        app.processEvents()
        assert not win.isHidden()
        assert win.isVisible()
        assert badge.isHidden()
    finally:
        win._suppress_close_prompt = True
        if win._badge is not None:
            win._badge.hide()
        win.close()
        app.processEvents()


def test_drag_does_not_restore():
    _app()
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from cutora.ui.floating_badge import FloatingBadge
    badge = FloatingBadge()
    badge.show()
    _app().processEvents()
    restored = []
    badge.restore_requested.connect(lambda: restored.append(1))
    origin = badge.pos()
    local = QPointF(8, 8)
    press_g = badge.mapToGlobal(QPoint(8, 8))
    move_g = QPoint(press_g.x() + 20, press_g.y())
    buttons = Qt.MouseButton.LeftButton
    badge.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress,
        local,
        QPointF(press_g),
        buttons,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    ))
    badge.mouseMoveEvent(QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(28, 8),
        QPointF(move_g),
        Qt.MouseButton.NoButton,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    ))
    badge.mouseReleaseEvent(QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(28, 8),
        QPointF(move_g),
        buttons,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    ))
    assert restored == []
    assert badge.x() == origin.x() + 20
    badge.hide()
    badge.deleteLater()


def test_wheel_clamps_side():
    _app()
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from cutora.ui.badge_prefs import SIZE_MAX, SIZE_MIN
    from cutora.ui.floating_badge import FloatingBadge
    badge = FloatingBadge()

    def roll(notches: int) -> None:
        event = QWheelEvent(
            QPointF(4, 4),
            QPointF(4, 4),
            QPoint(0, 0),
            QPoint(0, 120 * notches),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        badge.wheelEvent(event)

    for _ in range(100):
        roll(1)
    assert badge.side() == SIZE_MAX
    assert badge.side() == 256
    for _ in range(100):
        roll(-1)
    assert badge.side() == SIZE_MIN
    assert badge.side() == 48
    badge.hide()
    badge.deleteLater()


def test_offscreen_position_is_clamped_on_show(tmp_path, monkeypatch):
    ud = tmp_path / "clamp_ud"
    ud.mkdir()
    (ud / "app_settings.json").write_text(
        '{"ui.badge.x": -5000, "ui.badge.y": -5000, "ui.badge.size": 96}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CUTORA_USER_DATA", str(ud))
    from cutora.core.paths import refresh_cached_paths
    refresh_cached_paths()
    from PySide6.QtGui import QGuiApplication
    from cutora.ui.floating_badge import FloatingBadge
    app = _app()
    badge = FloatingBadge()
    badge.show()
    app.processEvents()
    rect = badge.frameGeometry()
    screens = QGuiApplication.screens()
    assert screens
    assert any(
        screen.availableGeometry().intersected(rect).width() > 0
        and screen.availableGeometry().intersected(rect).height() > 0
        for screen in screens
    ), rect
    badge.hide()
    badge.deleteLater()


def test_edge_hide_peeks_and_returns_when_disabled():
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QGuiApplication, QMouseEvent
    from PySide6.QtTest import QTest
    from cutora.ui.badge_prefs import (
        badge_edge_hide,
        set_badge_edge_hide,
        set_badge_enabled,
    )
    from cutora.ui.floating_badge import PEEK, FloatingBadge
    set_badge_enabled(True)
    set_badge_edge_hide(True)
    assert badge_edge_hide() is True
    app = _app()
    badge = FloatingBadge()
    badge.set_side(96)
    badge.show()
    app.processEvents()
    screen = QGuiApplication.primaryScreen()
    assert screen is not None
    ag = screen.availableGeometry()
    side = badge.side()
    # Centre sits 5px from the left edge (inside the 12px trigger).
    badge.move(ag.left() + 5 - side // 2, ag.top() + 80)
    origin = badge.pos()
    press_g = badge.mapToGlobal(QPoint(4, 4))
    move_g = QPoint(press_g.x() + 6, press_g.y())
    buttons = Qt.MouseButton.LeftButton
    badge.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(4, 4),
        QPointF(press_g),
        buttons,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    ))
    badge.mouseMoveEvent(QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(10, 4),
        QPointF(move_g),
        Qt.MouseButton.NoButton,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    ))
    badge.mouseReleaseEvent(QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(10, 4),
        QPointF(move_g),
        buttons,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    ))
    target = ag.left() - side + PEEK
    assert PEEK == 8
    for _ in range(40):
        if badge.x() == target:
            break
        QTest.qWait(30)
    assert badge.x() == target, (badge.x(), target, origin)
    set_badge_edge_hide(False)
    badge.on_edge_setting_changed()
    rect = badge.frameGeometry()
    assert ag.contains(rect), rect
    assert badge.x() >= ag.left()
    badge.hide()
    badge.deleteLater()
    set_badge_edge_hide(False)


def test_console_follow_and_filter():
    from PySide6.QtWidgets import QApplication
    from cutora.core.log import get_logger, ring_buffer, setup_logging
    from cutora.ui.panels.console_view import ConsoleView
    from cutora.ui.theme.tokens import SPACE_6
    app = _app()
    setup_logging()
    log = get_logger("q102.console")
    log.setLevel(logging.DEBUG)
    view = ConsoleView()
    view.resize(SPACE_6 * 16, SPACE_6 * 8)
    view.show()
    app.processEvents()
    view.btn_clear.click()
    app.processEvents()
    assert view.shown_line_count() == 0
    for i in range(40):
        log.info("scroll-line-%02d" % i)
    for _ in range(5):
        app.processEvents()
    bar = view.editor.verticalScrollBar()
    assert bar.maximum() > 0, "the log did not overflow the console"
    assert view.follows_tail()
    assert bar.value() == bar.maximum()
    bar.setValue(0)
    app.processEvents()
    assert view.follows_tail() is False
    log.info("after-up")
    app.processEvents()
    assert bar.value() < bar.maximum()
    assert "after-up" in view.editor.toPlainText()
    bar.setValue(bar.maximum())
    app.processEvents()
    assert view.follows_tail() is True
    log.info("after-bottom")
    app.processEvents()
    assert bar.value() == bar.maximum()

    view.btn_clear.click()
    app.processEvents()
    log.debug("c-debug")
    log.info("c-info")
    log.warning("c-warn")
    log.error("c-error")
    app.processEvents()
    view.level_combo.setCurrentIndex(view.level_combo.findData(logging.WARNING))
    app.processEvents()
    text = view.editor.toPlainText()
    own = [
        line for line in text.splitlines()
        if any(token in line for token in ("c-debug", "c-info", "c-warn", "c-error"))
    ]
    assert own == [line for line in own if ("c-warn" in line or "c-error" in line)]
    assert len(own) == 2, own
    assert "c-debug" not in text
    assert "c-info" not in text
    view.close()


def test_log_path_follows_cutora_user_data(tmp_path, monkeypatch):
    dest = tmp_path / "portable_ud"
    dest.mkdir()
    monkeypatch.setenv("CUTORA_USER_DATA", str(dest))
    from cutora.core.paths import refresh_cached_paths, user_logs_dir
    refresh_cached_paths()
    from cutora.core.log import get_logger, log_file_path, setup_logging
    setup_logging()
    path = log_file_path()
    assert os.path.normcase(path).startswith(os.path.normcase(str(dest)))
    assert os.path.normcase(os.path.dirname(path)) == os.path.normcase(user_logs_dir())
    get_logger("q102.portable").info("portable-marker-q102")
    _flush_log_files()
    assert os.path.isfile(path)
    assert "portable-marker-q102" in open(path, encoding="utf-8").read()
