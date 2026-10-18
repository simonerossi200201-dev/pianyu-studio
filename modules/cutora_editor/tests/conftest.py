# -*- coding: utf-8 -*-
"""Ensure repo root is on sys.path so `import cutora` works in pytest."""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

_TESTS = os.path.dirname(os.path.abspath(__file__))
if _TESTS not in sys.path:
    sys.path.insert(0, _TESTS)
from qt_isolate import (  # noqa: E402
    ENV as _QT_ISO_ENV,
    ISOLATE_FILES,
    IsolatedModule,
)

_REPO = os.path.dirname(_TESTS)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def pytest_configure(config):
    """Child processes: torch/CUDA/MSVCP140 before any PySide6 import."""
    if os.environ.get(_QT_ISO_ENV) != "1":
        return
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)


def pytest_pycollect_makemodule(module_path, parent):
    if os.environ.get(_QT_ISO_ENV) == "1":
        return None
    if module_path.name in ISOLATE_FILES:
        return IsolatedModule.from_parent(parent, path=module_path)
    return None


def pytest_collection_finish(session):
    if os.environ.get(_QT_ISO_ENV) == "1":
        return
    leaked = [
        m for m in sys.modules
        if m == "PySide6" or m.startswith("PySide6.")
    ]
    if leaked:
        raise RuntimeError(
            "PySide6 leaked into the pytest parent during collection: %s. "
            "Add the importing file to tests.qt_isolate.ISOLATE_FILES."
            % leaked
        )

# Session dir for IsolatedModule / nested pytest children. In-process tests still
# get a per-test tmp so dock layout cannot poison later GUI tests.
_PYTEST_UD = os.path.join(tempfile.gettempdir(), "cutora_pytest_user_data")
os.makedirs(_PYTEST_UD, exist_ok=True)
os.environ.setdefault("CUTORA_USER_DATA", _PYTEST_UD)
# Q193：没有 N 卡的测试机上，GPU 提示框会把 GUI 测试卡住；要测「没有显卡」的用 CUTORA_FAKE_NO_GPU=1（优先）
os.environ.setdefault("CUTORA_FAKE_GPU", "1")
_SESSION_UD_ENV = "CUTORA_PYTEST_SESSION_UD"


@pytest.fixture(scope="session", autouse=True)
def _session_user_data(tmp_path_factory):
    from cutora.core.paths import ensure_user_layout, refresh_cached_paths
    inherited = (os.environ.get(_SESSION_UD_ENV) or "").strip()
    if inherited and os.path.isdir(inherited):
        os.environ["CUTORA_USER_DATA"] = inherited
        refresh_cached_paths()
        yield inherited
        return
    ud = str(tmp_path_factory.mktemp("cutora_user_data"))
    os.environ[_SESSION_UD_ENV] = ud
    os.environ["CUTORA_USER_DATA"] = ud
    refresh_cached_paths()
    ensure_user_layout()
    settings = os.path.join(ud, "app_settings.json")
    if not os.path.isfile(settings):
        with open(settings, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("{}\n")
    yield ud


@pytest.fixture(scope="session", autouse=True)
def _embedded_fonts_must_load():
    """After the session, a live QApplication must still have its fonts.

    Callers used to ignore a False from load_embedded_fonts(), so an empty
    scan showed up later as fallback glyphs. This does not create an
    application and does not initialize torch: that order belongs to the
    test, and doing it here deadlocks the isolated startup child.
    """
    yield
    from PySide6.QtWidgets import QApplication
    if QApplication.instance() is None:
        return
    from cutora.ui.panels import fonts as fonts_mod
    ok = fonts_mod.load_embedded_fonts()
    assert ok, (
        "load_embedded_fonts() returned False (count=%s dir=%s)"
        % (fonts_mod.LAST_EMBEDDED_FONT_LOAD_COUNT, fonts_mod.FONT_DIR)
    )


@pytest.fixture(autouse=True)
def _isolate_user_data(tmp_path, monkeypatch):
    from cutora.core.paths import refresh_cached_paths
    if os.environ.get(_QT_ISO_ENV) == "1":
        session = (os.environ.get(_SESSION_UD_ENV) or "").strip()
        if session:
            monkeypatch.setenv("CUTORA_USER_DATA", session)
        refresh_cached_paths()
        yield
        refresh_cached_paths()
        return
    ud = tmp_path / "user_data"
    ud.mkdir()
    monkeypatch.setenv("CUTORA_USER_DATA", str(ud))
    refresh_cached_paths()
    yield
    refresh_cached_paths()
