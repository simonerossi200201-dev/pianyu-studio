# -*- coding: utf-8 -*-
"""T04g: as_bgra on PySide6 memoryview. Parent stays Qt-free."""
from __future__ import annotations

import os
import subprocess
import sys

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
FFMPEG_DIR = os.path.join(REPO, "runtime", "ffmpeg")


def _child_env() -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("PYTEST") or key == "CUTORA_PYTEST_ISOLATED":
            env.pop(key, None)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONUNBUFFERED"] = "1"
    path = env.get("PATH", "")
    if os.path.normcase(FFMPEG_DIR) not in os.path.normcase(path):
        env["PATH"] = FFMPEG_DIR + os.pathsep + path
    return env


def test_as_bgra_memoryview_child():
    r = subprocess.run(
        [PY, "-u", os.path.join(REPO, "tests", "test_t04g_as_bgra.py"), "--as-bgra"],
        cwd=REPO,
        env=_child_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    assert r.returncode == 0 and "T04G_AS_BGRA_OK" in (r.stdout or ""), out[-6000:]


def run_as_bgra_suite() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from PySide6.QtGui import QColor, QImage
    from cutora.render.textfx.raster import as_bgra

    fmt = QImage.Format.Format_ARGB32_Premultiplied

    # (a) values match a constructed array (opaque, so premul is a no-op)
    img = QImage(4, 3, fmt)
    img.fill(QColor(10, 20, 30, 255))
    owned, view = as_bgra(img)
    assert view.shape == (3, 4, 4), view.shape
    assert view.dtype == np.uint8
    # little-endian ARGB32 is B, G, R, A in memory
    assert np.all(view[:, :, 0] == 30), view[0, 0]
    assert np.all(view[:, :, 1] == 20), view[0, 0]
    assert np.all(view[:, :, 2] == 10), view[0, 0]
    assert np.all(view[:, :, 3] == 255), view[0, 0]
    expected = np.empty((3, 4, 4), dtype=np.uint8)
    expected[:] = (30, 20, 10, 255)
    assert np.array_equal(view, expected)
    print("T04G_AS_BGRA_A_OK", flush=True)

    # (b) writing the view mutates the QImage (not a copy)
    view[1, 2] = (1, 2, 3, 255)
    owned2, view2 = as_bgra(owned)
    assert tuple(int(x) for x in view2[1, 2]) == (1, 2, 3, 255), view2[1, 2]
    rgb = owned.pixel(2, 1)
    # QRgb is 0xAARRGGBB; we wrote B=1 G=2 R=3 A=255
    assert (rgb >> 24) & 0xFF == 255
    assert (rgb >> 16) & 0xFF == 3
    assert (rgb >> 8) & 0xFF == 2
    assert rgb & 0xFF == 1
    print("T04G_AS_BGRA_B_OK", flush=True)

    # (c) bytesPerLine > width*4: row padding is sliced off, pixels still line up
    w, h = 5, 3
    bpl = 32  # 5 * 4 = 20, 12 bytes of padding
    assert bpl != w * 4
    buf = bytearray(bpl * h)
    for y in range(h):
        for x in range(w):
            i = y * bpl + x * 4
            buf[i:i + 4] = bytes((x, y, 7, 255))  # BGRA
        for p in range(w * 4, bpl):
            buf[y * bpl + p] = 0xEE  # poison padding
    padded = QImage(buf, w, h, bpl, fmt)
    assert padded.bytesPerLine() == bpl, padded.bytesPerLine()
    owned_p, view_p = as_bgra(padded)
    assert owned_p.bytesPerLine() == bpl, owned_p.bytesPerLine()
    assert view_p.shape == (h, w, 4), view_p.shape
    for y in range(h):
        for x in range(w):
            got = tuple(int(v) for v in view_p[y, x])
            assert got == (x, y, 7, 255), (x, y, got)
    assert not np.any(view_p == 0xEE)
    print("T04G_AS_BGRA_C_OK", flush=True)
    print("T04G_AS_BGRA_OK", flush=True)
    return 0


if __name__ == "__main__":
    if "--as-bgra" in sys.argv:
        raise SystemExit(run_as_bgra_suite())
    raise SystemExit("usage: test_t04g_as_bgra.py --as-bgra")
