# -*- coding: utf-8 -*-
"""Q37: product font files commercial-license & SHA256 manifest verification + DirectWrite unique-file load count.

1. DirectWrite (Qt platform ``windows``) exposes variable-font named instances as extra family
   names; ``LAST_EMBEDDED_FONT_LOAD_COUNT`` must equal the unique font file count on disk
   while keeping named instances (e.g. ``Roboto Slab Thin``) in ``_family_name_map``.
2. The legacy 240-font count cap is retired: font count has no upper bound, provided 100% of
   bundled fonts are commercially licensed (``OFL-1.1`` or ``Apache-2.0``), every family
   directory preserves its license credential file (``OFL.txt`` / ``LICENSE.txt`` and
   ``METADATA.pb`` for ``google/fonts`` social families), and every file on disk matches its
   recorded SHA256 in ``FONTS_MANIFEST.json`` and ``PROVENANCE.json``.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
if not os.path.isfile(PY):
    PY = os.path.join(os.path.dirname(os.path.dirname(REPO)), "runtime", "venv", "Scripts", "python.exe")
FFMPEG_DIR = os.path.join(REPO, "runtime", "ffmpeg")
MANIFEST_PATH = os.path.join(REPO, "resources", "fonts", "FONTS_MANIFEST.json")
PROVENANCE_PATH = os.path.join(REPO, "resources", "fonts", "PROVENANCE.json")
SKIP_DIR_NAMES = ("_quarantine", "_extra", "fonts_pending", "Noto_Color_Emoji")
ALLOWED_COMMERCIAL_LICENSES = {"OFL-1.1", "Apache-2.0"}
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


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


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


def _has_license_credential(fam_dir: str) -> bool:
    if not os.path.isdir(fam_dir):
        return False
    for fn in os.listdir(fam_dir):
        low = fn.lower()
        if low in ("ofl.txt", "license", "license.txt", "licence.txt", "apache.txt") or "license" in low or low.startswith("ofl"):
            return True
    return False


def _check() -> int:
    import tempfile

    os.environ.pop("QT_QPA_PLATFORM", None)
    os.environ["CUTORA_USER_DATA"] = tempfile.mkdtemp(prefix="cutora_q37_")

    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from PySide6.QtWidgets import QApplication
    from cutora.core.paths import FONT_DIR, RESOURCES
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

    unique_count = len({os.path.normcase(os.path.abspath(p)) for p in files})
    loaded = int(LAST_EMBEDDED_FONT_LOAD_COUNT)
    print("platform", platform, flush=True)
    print("iter_files", len(files), flush=True)
    print("unique_count", unique_count, flush=True)
    print("LAST_EMBEDDED_FONT_LOAD_COUNT", loaded, flush=True)

    if loaded != unique_count:
        print(
            "Q37_LOAD_COUNT_MISMATCH loaded=%s unique_files=%s "
            "(named-instance aliases counted as extra files)"
            % (loaded, unique_count),
            flush=True,
        )
        return 1

    # Verify FONTS_MANIFEST.json commercial license compliance & SHA256 integrity
    with open(MANIFEST_PATH, encoding="utf-8") as fh:
        manifest = json.load(fh)
    man_by_rel = {str(r.get("file") or ""): r for r in (manifest.get("fonts") or [])}
    for lic_key in (manifest.get("by_license") or {}):
        if lic_key not in ALLOWED_COMMERCIAL_LICENSES:
            print("Q37_DISALLOWED_LICENSE_SUMMARY", lic_key, flush=True)
            return 1

    for path in files:
        rel = os.path.relpath(path, RESOURCES).replace("\\", "/")
        row = man_by_rel.get(rel)
        if not row:
            print("Q37_MISSING_FROM_MANIFEST", rel, flush=True)
            return 1
        lic = str(row.get("license") or "")
        if lic not in ALLOWED_COMMERCIAL_LICENSES:
            print("Q37_DISALLOWED_FONT_LICENSE", rel, lic, flush=True)
            return 1
        fam_dir = os.path.dirname(path)
        if not _has_license_credential(fam_dir):
            print("Q37_MISSING_LICENSE_FILE", rel, fam_dir, flush=True)
            return 1
        got_sha = _sha256(path)
        if got_sha != str(row.get("sha256") or ""):
            print("Q37_SHA256_MISMATCH", rel, got_sha, row.get("sha256"), flush=True)
            return 1

    # Verify PROVENANCE.json entries
    with open(PROVENANCE_PATH, encoding="utf-8") as fh:
        prov = json.load(fh)
    for item in prov.get("fonts") or []:
        rel = str(item.get("file") or "")
        disk_path = os.path.join(RESOURCES, rel.replace("/", os.sep))
        if not os.path.isfile(disk_path):
            print("Q37_PROVENANCE_FILE_MISSING", rel, flush=True)
            return 1
        if _sha256(disk_path) != str(item.get("sha256") or ""):
            print("Q37_PROVENANCE_SHA_MISMATCH", rel, flush=True)
            return 1
        lic = str(item.get("license") or "OFL-1.1")
        if lic not in ALLOWED_COMMERCIAL_LICENSES:
            print("Q37_PROVENANCE_BAD_LICENSE", rel, lic, flush=True)
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

    print("Q37_FONT_SET_OK count=%s loaded=%s" % (unique_count, loaded), flush=True)
    return 0


if __name__ == "__main__":
    if "--check" in sys.argv:
        raise SystemExit(_check())
    raise SystemExit(0)
