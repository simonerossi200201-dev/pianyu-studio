# -*- coding: utf-8 -*-
"""T04g: real-platform startup still (not offscreen)."""
from __future__ import annotations

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

IMG_DIR = os.path.join(REPO, "docs", "plan", "reports", "img")


def main() -> int:
    # 真机：默认 Windows QPA，禁止离屏。
    os.environ.pop("QT_QPA_PLATFORM", None)
    if os.environ.get("QT_QPA_PLATFORM"):
        raise SystemExit("QT_QPA_PLATFORM still set")
    if "CUTORA_USER_DATA" not in os.environ:
        os.environ["CUTORA_USER_DATA"] = tempfile.mkdtemp(prefix="cutora_t04g_boot_")

    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from cutora.app import (
        _enable_faulthandler,
        _log_startup_identity,
        apply_ui_locale,
        prepare_faulthandler_log,
    )
    from cutora.core.log import setup_logging
    from cutora.core.paths import ensure_user_layout, user_logs_dir

    ensure_user_layout()
    setup_logging()
    prepare_faulthandler_log(os.path.join(user_logs_dir(), "faulthandler.log"))
    _enable_faulthandler()
    _log_startup_identity()

    apply_ui_locale()
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QStyleFactory
    from cutora.render import export_guard
    from cutora.ui.main_window import MainWindow
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont

    export_guard.disable_console_quickedit()
    app = QApplication.instance() or QApplication(["t04g-startup"])
    plugin = app.platformName()
    print("T04G_PLATFORM", plugin, flush=True)
    if plugin == "offscreen":
        raise SystemExit("refusing offscreen platform")
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)

    win = MainWindow()
    win.show()
    app.processEvents()

    def _shot():
        os.makedirs(IMG_DIR, exist_ok=True)
        path = os.path.join(IMG_DIR, "T04g_startup_real.png")
        pix = win.grab()
        pix.save(path, "PNG")
        print(
            "T04G_STARTUP_STILL", path, pix.width(), "x", pix.height(),
            "visible", win.isVisible(), "platform", plugin, flush=True,
        )
        win.close()
        app.quit()

    QTimer.singleShot(1200, _shot)
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
