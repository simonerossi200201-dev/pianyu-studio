# -*- coding: utf-8 -*-
"""T04f-5-fix stills: About dialog + in-app third-party licenses."""
from __future__ import annotations

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

IMG_DIR = os.path.join(REPO, "docs", "plan", "reports", "img")


def _save(widget, name):
    os.makedirs(IMG_DIR, exist_ok=True)
    path = os.path.join(IMG_DIR, name)
    pix = widget.grab()
    pix.save(path, "PNG")
    print("wrote", path, pix.width(), "x", pix.height(), flush=True)
    return path


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if "CUTORA_USER_DATA" not in os.environ:
        os.environ["CUTORA_USER_DATA"] = tempfile.mkdtemp(prefix="cutora_t04f5fix_")
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from cutora.app import apply_ui_locale
    apply_ui_locale()
    from PySide6.QtWidgets import QApplication, QStyleFactory
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
    from cutora.ui.panels.about import AboutDialog, ThirdPartyLicensesDialog

    app = QApplication.instance() or QApplication(["t04f5-fix-stills"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)

    about = AboutDialog()
    about.resize(600, 420)
    about.show()
    app.processEvents()
    _save(about, "T04f-5_fix_about_third_party_btn.png")

    lic = ThirdPartyLicensesDialog(about)
    lic.resize(720, 520)
    lic.show()
    app.processEvents()
    _save(lic, "T04f-5_fix_licenses_third_party.png")
    about.close()
    lic.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
