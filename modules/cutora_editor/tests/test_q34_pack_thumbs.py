# -*- coding: utf-8 -*-
"""Q34-5: each builtin pack has a 64x64 rendered thumb; content change updates md5."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess

import sys
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
if not os.path.isfile(VENV_PY):
    VENV_PY = sys.executable


def test_q34_pack_thumbs(tmp_path):
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env["PYTHONUNBUFFERED"] = "1"
    env["CUTORA_Q34_THUMB_TMP"] = str(tmp_path)
    proc = subprocess.run(
        [VENV_PY, "-u", os.path.abspath(__file__), "--run"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    assert proc.returncode == 0, out
    assert "Q34_PACK_THUMBS_OK" in (proc.stdout or ""), out


def run() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.subtitle.stylepack import (
        SAMPLE_EN, builtin_dir, ensure_pack_thumb, list_pack_dirs, load_pack,
    )

    app = QApplication.instance() or QApplication(["q34-pack-thumbs"])
    load_embedded_fonts()
    root = builtin_dir().replace("\\", "/")
    packs = [
        p for p in list_pack_dirs()
        if p.replace("\\", "/").startswith(root)
    ]
    assert len(packs) >= 14, [os.path.basename(p) for p in packs]
    for path in packs:
        dest = ensure_pack_thumb(path)
        assert dest and os.path.isfile(dest), path
        img = QImage(dest)
        assert not img.isNull(), dest
        assert img.width() == 64 and img.height() == 64, (dest, img.width(), img.height())
        # not all black
        ink = 0
        for y in range(64):
            for x in range(64):
                c = img.pixelColor(x, y)
                if max(c.red(), c.green(), c.blue()) > 16:
                    ink += 1
        assert ink > 20, "%s thumb is all-black ink=%d" % (path, ink)

    tmp = os.environ.get("CUTORA_Q34_THUMB_TMP") or "."
    src = packs[0]
    copy = os.path.join(tmp, os.path.basename(src))
    if os.path.isdir(copy):
        shutil.rmtree(copy)
    shutil.copytree(src, copy)
    first = ensure_pack_thumb(copy, force=True)
    md5_a = hashlib.md5(open(first, "rb").read()).hexdigest()
    style_path = os.path.join(copy, "style.json")
    body = open(style_path, encoding="utf-8").read()
    if "#F4EBD8" in body:
        body = body.replace("#F4EBD8", "#22D3EE", 1)
    elif "#F4F7FB" in body:
        body = body.replace("#F4F7FB", "#FF3B30", 1)
    else:
        body = body.replace("{", '{"_q34": 1,', 1)
    open(style_path, "w", encoding="utf-8").write(body)
    second = ensure_pack_thumb(copy, force=False)
    md5_b = hashlib.md5(open(second, "rb").read()).hexdigest()
    assert md5_a != md5_b, (md5_a, os.path.basename(src), SAMPLE_EN)
    print("Q34_PACK_THUMBS_OK", flush=True)
    return 0


if __name__ == "__main__":
    import sys
    if "--run" in sys.argv:
        raise SystemExit(run())
    raise SystemExit("use pytest")
