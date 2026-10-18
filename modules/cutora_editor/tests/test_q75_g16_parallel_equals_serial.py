# -*- coding: utf-8 -*-
"""Q75: g16 parallel exports must match the g11/g12/g13 serial files.

Golden only says "same hash as the day we recorded". This says the three
parallel products are the serial products: md5 of each g16 worker output
against g11, g12, g13 expected.mp4. A parallel-path drift goes red here
without waiting for the golden suite.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join(REPO, "tests", "golden")

# g16 task order is the fx mirror of g11, g12, g13 (serial, workers=1).
PAIRS = (
    (0, "g11_vertical_basic_fx"),
    (1, "g12_vertical_fx_path"),
    (2, "g13_horizontal_it_fx"),
)


def _gc():
    path = os.path.join(REPO, "tools", "golden_check.py")
    spec = importlib.util.spec_from_file_location("cutora_golden_check_q75", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q75-g16"])


def test_q75_g16_parallel_equals_serial(qapp):
    gc = _gc()
    host, _app = gc.boot_host()
    case_dir = os.path.join(GOLDEN, "g16_parallel_fx")
    manifest = gc.load_manifest(case_dir)
    project = os.path.join(case_dir, manifest.get("project", "project.asproj.json"))
    _data, tasks = gc.load_tasks(host, project)
    assert len(tasks) == 3, len(tasks)
    gc.reset_case_caches()
    outs = gc.render_parallel(
        host,
        tasks,
        os.path.join(os.environ.get("TEMP") or os.environ.get("TMP") or ".", "q75_g16_par"),
        manifest.get("mode") or "quality",
        bool(manifest.get("gpu", False)),
        int(manifest.get("workers") or 3),
    )
    assert len(outs) == 3, outs
    for index, serial_case in PAIRS:
        serial = os.path.join(GOLDEN, serial_case, "expected.mp4")
        assert os.path.isfile(serial), "serial product missing: %s" % serial
        got = _md5(outs[index])
        exp = _md5(serial)
        assert got == exp, (
            "g16 parallel out_%02d md5 %s != %s serial %s"
            % (index, got, serial_case, exp)
        )
