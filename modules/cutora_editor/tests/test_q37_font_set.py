# -*- coding: utf-8 -*-
"""Q37: product font *files* must match tests/golden/fonts_digest.txt.

The 240 vs 607 split is not extra files. DirectWrite (Qt platform
``windows``) exposes variable-font named instances as extra family
names, and FontManager.register() used to call addApplicationFont once
per (family, file) pair. That made the log say 607 while the unique
file set stayed 240 — the same set golden pins.

This test must run on the windows plugin. QT_QPA_PLATFORM=offscreen
collapses named instances, so LAST_EMBEDDED_FONT_LOAD_COUNT is already
240 and the defect never shows.
"""
from __future__ import annotations

import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
FFMPEG_DIR = os.path.join(REPO, "runtime", "ffmpeg")
DIGEST_PATH = os.path.join(REPO, "tests", "golden", "fonts_digest.txt")
SKIP_DIR_NAMES = ("_quarantine", "_extra", "fonts_pending", "Noto_Color_Emoji")
# DirectWrite named instance of Roboto Slab; absent under offscreen.
NAMED_INSTANCE = "Roboto Slab Thin"


def _windows_env() -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("PYTEST") or key == "CUTORA_PYTEST_ISOLATED":
            env.pop(key, None)
    env.pop("QT_QPA_PLATFORM", None)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    path = env.get("PATH", "")
    if os.path.normcase(FFMPEG_DIR) not in os.path.normcase(path):
        env["PATH"] = FFMPEG_DIR + os.pathsep + path
    return env


def test_q37_font_set_matches():
    r = subprocess.run(
        [PY, "-u", os.path.abspath(__file__), "--check"],
        cwd=REPO,
        env=_windows_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    assert r.returncode == 0 and "Q37_FONT_SET_OK" in (r.stdout or ""), out[-8000:]


def _read_pinned() -> tuple[int, str]:
    count = None
    digest = ""
    with open(DIGEST_PATH, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if line.startswith("count="):
                count = int(line.split("=", 1)[1])
            elif len(line) == 64 and all(c in "0123456789abcdef" for c in line.lower()):
                digest = line.lower()
    if count is None or not digest:
        raise AssertionError("fonts_digest.txt missing count= or sha256: %s" % DIGEST_PATH)
    return count, digest


def _check() -> int:
    import tempfile

    os.environ.pop("QT_QPA_PLATFORM", None)
    os.environ["CUTORA_USER_DATA"] = tempfile.mkdtemp(prefix="cutora_q37_")

    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from PySide6.QtWidgets import QApplication
    from cutora.core.paths import FONT_DIR
    from cutora.ui.panels.fonts import (
        _iter_font_files,
        get_font_manager,
        load_embedded_fonts,
    )

    app = QApplication.instance() or QApplication(["q37-font-set"])
    platform = str(app.platformName() or "")
    if platform == "offscreen":
        print("Q37_WRONG_PLATFORM offscreen — DirectWrite named instances never appear", flush=True)
        return 2
    if platform != "windows":
        print("Q37_WRONG_PLATFORM %s — expected windows" % platform, flush=True)
        return 2

    files = _iter_font_files()
    leaked = []
    for path in files:
        parts = set(os.path.normpath(path).replace("\\", "/").split("/"))
        hit = [name for name in SKIP_DIR_NAMES if name in parts]
        if hit:
            leaked.append("%s in %s" % (",".join(hit), path))
    if leaked:
        print("Q37_SKIP_LEAK", len(leaked), flush=True)
        print("\n".join(leaked[:20]), flush=True)
        return 1

    emoji = os.path.join(FONT_DIR, "Noto_Color_Emoji", "NotoColorEmoji-COLRv1.ttf")
    if not os.path.isfile(emoji):
        print("Q37_EMOJI_MISSING", emoji, flush=True)
        return 1
    emoji_abs = os.path.normcase(os.path.abspath(emoji))
    if any(os.path.normcase(os.path.abspath(p)) == emoji_abs for p in files):
        print("Q37_EMOJI_IN_SCAN", emoji, flush=True)
        return 1

    if not load_embedded_fonts():
        print("Q37_LOAD_FAILED", flush=True)
        return 1

    from cutora.ui.panels.fonts import LAST_EMBEDDED_FONT_LOAD_COUNT
    import importlib.util

    gc_path = os.path.join(REPO, "tools", "golden_check.py")
    spec = importlib.util.spec_from_file_location("cutora_golden_check_q37", gc_path)
    gc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gc)
    _fonts_digest = gc._fonts_digest

    pinned_count, pinned_digest = _read_pinned()
    got_count, got_digest = _fonts_digest(files)
    loaded = int(LAST_EMBEDDED_FONT_LOAD_COUNT)
    print("platform", platform, flush=True)
    print("iter_files", len(files), flush=True)
    print("digest_count", got_count, flush=True)
    print("digest", got_digest, flush=True)
    print("pinned_count", pinned_count, flush=True)
    print("pinned_digest", pinned_digest, flush=True)
    print("LAST_EMBEDDED_FONT_LOAD_COUNT", loaded, flush=True)

    if got_count != pinned_count:
        print(
            "Q37_COUNT_MISMATCH unique_files=%s pinned=%s" % (got_count, pinned_count),
            flush=True,
        )
        return 1
    if got_digest != pinned_digest:
        print(
            "Q37_DIGEST_MISMATCH got=%s pinned=%s" % (got_digest, pinned_digest),
            flush=True,
        )
        return 1
    if loaded != pinned_count:
        print(
            "Q37_LOAD_COUNT_MISMATCH loaded=%s pinned=%s "
            "(named-instance aliases counted as extra files)"
            % (loaded, pinned_count),
            flush=True,
        )
        return 1

    mgr = get_font_manager()
    mapped = mgr._family_name_map
    if NAMED_INSTANCE not in mapped:
        print(
            "Q37_NAMED_INSTANCE_DROPPED %r not in _family_name_map after unique-file register"
            % NAMED_INSTANCE,
            flush=True,
        )
        return 1

    print("Q37_FONT_SET_OK count=%s digest=%s loaded=%s" % (got_count, got_digest, loaded), flush=True)
    return 0


if __name__ == "__main__":
    if "--check" in sys.argv:
        raise SystemExit(_check())
    raise SystemExit(0)
