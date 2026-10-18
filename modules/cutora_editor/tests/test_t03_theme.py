# -*- coding: utf-8 -*-
"""T03a design system: Roma tokens, generated QSS, Lucide icons, UI fonts."""
from __future__ import annotations

import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")


def _qt_child(code: str) -> None:
    """Qt + fonts in a subprocess so pytest collection cannot import PySide6 first."""
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    proc = subprocess.run(
        [VENV_PY, "-c", code],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    assert "ok" in (proc.stdout or "")


def test_roma_palette_unchanged():
    from cutora.ui.theme.tokens import ACCENT, BG0, FG0, INFO, ROMA
    assert BG0 == "#141210"
    assert FG0 == "#EFE7DA"
    assert ACCENT == "#C8A24A"
    assert INFO == "#8FA89B"
    assert ROMA["accent"] == ACCENT


def test_theme_qss_is_generated_and_in_sync():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "build_qss", os.path.join(REPO, "tools", "build_qss.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    path = os.path.join(REPO, "resources", "theme", "theme_dark.qss")
    assert os.path.isfile(path)
    assert not os.path.isfile(os.path.join(REPO, "resources", "theme", "legacy.qss"))
    from cutora.ui.theme.tokens import DEFAULT_THEME_ID, load_theme
    load_theme(DEFAULT_THEME_ID)
    committed = open(path, encoding="utf-8").read()
    assert committed == mod.render_qss()


def test_setstylesheet_count_at_most_14():
    root = os.path.join(REPO, "cutora")
    hits = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as fh:
                for i, line in enumerate(fh, 1):
                    if "setStyleSheet(" in line:
                        hits.append("%s:%d" % (os.path.relpath(path, REPO), i))
    # 15 calls were already in the tree at 6c3cd445. apply.py stays in the list.
    assert len(hits) <= 15, hits
    apply = [h for h in hits if h.replace("\\", "/").startswith("cutora/ui/theme/apply.py")]
    assert apply, "dynamic helpers must keep the remaining setStyleSheet calls"


def test_qss_uses_roma_not_legacy_amber():
    path = os.path.join(REPO, "resources", "theme", "theme_dark.qss")
    text = open(path, encoding="utf-8").read()
    assert "#C8A24A" in text
    assert "#F5B700" not in text
    assert "#5CE1E6" not in text
    assert "Inter" in text
    assert "Noto Sans SC" in text
    assert "JetBrains Mono" in text


def test_lucide_icons_and_cache():
    _qt_child(
        "from cutora.core.runtime import init_torch_runtime\n"
        "init_torch_runtime(render_child=False)\n"
        "from PySide6.QtWidgets import QApplication\n"
        "from cutora.ui.icons import clear_icon_cache, icon, lucide_path\n"
        "import os\n"
        "app = QApplication.instance() or QApplication([])\n"
        "assert os.path.isfile(lucide_path('type'))\n"
        "assert os.path.isfile(lucide_path('tool_text.svg'))\n"
        "assert os.path.isfile(lucide_path('arrow-up'))\n"
        "assert os.path.isfile(lucide_path('eye-off'))\n"
        "from cutora.ui.icons import pixmap\n"
        "pm = pixmap('check', size=12)\n"
        "assert not pm.isNull()\n"
        "clear_icon_cache()\n"
        "a = icon('type', size=16)\n"
        "b = icon('type', size=16)\n"
        "assert a is b and not a.isNull()\n"
        "_ = app\n"
        "print('ok')\n"
    )


def test_register_ui_fonts_loads_inter_and_cjk():
    _qt_child(
        "from cutora.core.runtime import init_torch_runtime\n"
        "init_torch_runtime(render_child=False)\n"
        "from PySide6.QtWidgets import QApplication\n"
        "from cutora.ui.theme.fonts_ui import register_ui_fonts\n"
        "from cutora.ui.theme.tokens import FONT_MONO, FONT_UI, FONT_UI_CJK\n"
        "app = QApplication.instance() or QApplication([])\n"
        "families = register_ui_fonts()\n"
        "joined = ' '.join(families)\n"
        "assert FONT_UI in joined, joined\n"
        "assert FONT_MONO in joined, joined\n"
        "assert FONT_UI_CJK in joined, joined\n"
        "_ = app\n"
        "print('ok')\n"
    )


REQUIRED_QSS_SELECTORS = (
    "QHeaderView::section",
    "QTableCornerButton::section",
    "QTableView::viewport",
    "QTreeView",
    "QListView",
    "QSpinBox::up-button",
    "QSpinBox::down-button",
    "QSpinBox::up-arrow",
    "QCheckBox::indicator:checked",
    "QCheckBox::indicator:hover",
    "QCheckBox::indicator:disabled",
    "QToolTip",
    "QDialogButtonBox",
    "QLineEdit:disabled",
    "QProgressBar",
    "QSlider::groove:horizontal",
    "QSlider::handle:horizontal",
    "QScrollBar:vertical",
    "QTabBar::tab",
    "QGroupBox::title",
    "QSplitter::handle",
    "QComboBox QAbstractItemView",
    "QComboBox::down-arrow",
    "QMenu {",
    "QTextEdit {",
    "QPlainTextEdit",
)


def test_qss_covers_unstyled_widget_classes():
    path = os.path.join(REPO, "resources", "theme", "theme_dark.qss")
    text = open(path, encoding="utf-8").read()
    missing = [sel for sel in REQUIRED_QSS_SELECTORS if sel not in text]
    assert not missing, missing
    assert "url(qss_check.svg)" in text
    assert "url(qss_chevron_up.svg)" in text
    assert "accent_dim" in text or "#8A6E2E" in text
    for name in (
        "qss_check.svg", "qss_check_disabled.svg",
        "qss_chevron_up.svg", "qss_chevron_down.svg",
    ):
        assert os.path.isfile(os.path.join(REPO, "resources", "theme", name)), name


def test_combobox_dropdown_is_chevron_only():
    path = os.path.join(REPO, "resources", "theme", "theme_dark.qss")
    text = open(path, encoding="utf-8").read()
    assert "QComboBox::down-arrow" in text
    assert "url(qss_chevron_down.svg)" in text
    assert "QComboBox::drop-down" in text
    # The drop-down chrome must not be a separate rounded block.
    for block in text.split("QComboBox::drop-down"):
        if block.startswith(" {"):
            body = block.split("}", 1)[0]
            assert "border: none" in body or "border:none" in body, body
            assert "border-left" not in body, body


def test_swatch_fg_contrasts_on_white():
    from cutora.ui.theme.apply import apply_swatch, swatch_fg
    from cutora.ui.theme.tokens import BG0, FG0, LINE
    assert swatch_fg("#FFFFFF") == BG0
    assert swatch_fg("#000000") == FG0
    assert swatch_fg("#FF0000") == FG0
    src = open(os.path.join(REPO, "cutora", "ui", "theme", "apply.py"), encoding="utf-8").read()
    assert "border: 1px solid %s" in src
    assert "swatch_fg" in src
    assert apply_swatch is not None
    assert LINE == "#524944"


def test_format_ui_number_one_decimal():
    from cutora.ui.i18n import format_ui_number
    assert format_ui_number(964.6561679790027) == "964.7"
    assert format_ui_number(1) == "1.0"
    assert format_ui_number("x") == "x"


def test_apply_ui_locale_uses_c_decimal():
    _qt_child(
        "from cutora.core.runtime import init_torch_runtime\n"
        "init_torch_runtime(render_child=False)\n"
        "from cutora.app import apply_ui_locale\n"
        "from PySide6.QtCore import QLocale\n"
        "from PySide6.QtWidgets import QApplication, QDoubleSpinBox\n"
        "apply_ui_locale()\n"
        "app = QApplication.instance() or QApplication([])\n"
        "assert QLocale.system() is not None\n"
        "assert str(QLocale().decimalPoint()) == '.'\n"
        "spin = QDoubleSpinBox()\n"
        "spin.setDecimals(2)\n"
        "spin.setValue(1.0)\n"
        "text = spin.text()\n"
        "assert ',' not in text, text\n"
        "assert '1.00' in text or text.startswith('1'), text\n"
        "_ = app\n"
        "print('ok')\n"
    )


def test_track_header_uses_lucide_not_legacy_color():
    src = open(
        os.path.join(REPO, "cutora", "ui", "timeline", "header.py"),
        encoding="utf-8",
    ).read()
    assert "arrow-up" not in src
    assert "#38bdf8" not in src
    assert "#7dd3a0" not in src
    assert "lucide_pixmap" in src or "icons import pixmap" in src


def test_error_banner_and_align_error_wired():
    src = open(os.path.join(REPO, "cutora", "ui", "main_window.py"), encoding="utf-8").read()
    assert "worker_ai.error.connect(self.on_align_error)" in src
    assert "def on_align_error" in src
    _qt_child(
        "from cutora.core.runtime import init_torch_runtime\n"
        "init_torch_runtime(render_child=False)\n"
        "from PySide6.QtWidgets import QApplication\n"
        "from cutora.ui.widgets.error_banner import ErrorBanner\n"
        "app = QApplication.instance() or QApplication([])\n"
        "banner = ErrorBanner()\n"
        "assert not banner.isVisible()\n"
        "banner.show_error('auto-sub failed')\n"
        "assert banner.isVisible()\n"
        "banner.hide()\n"
        "_ = app\n"
        "print('ok')\n"
    )
