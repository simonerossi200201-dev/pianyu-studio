# -*- coding: utf-8 -*-
"""T09 font manifest: every family has a license file; only allowed licenses."""
from __future__ import annotations

import os

from cutora.core import safe_json

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_DIR = os.path.join(REPO, "resources", "fonts")
MANIFEST = os.path.join(FONT_DIR, "FONTS_MANIFEST.json")
ALLOWED = {"OFL-1.1", "Apache-2.0", "UFL-1.0", "Bitstream-Vera"}


def test_fonts_manifest_exists_and_allowed():
    assert os.path.isfile(MANIFEST)
    data = safe_json.load(MANIFEST)
    assert data.get("font_count", 0) >= 214
    by_lic = data.get("by_license") or {}
    for lic in by_lic:
        if lic not in ALLOWED:
            # quarantined entries may list UNKNOWN in by_license if not moved
            assert lic in ALLOWED, lic
    assert not data.get("missing_license_file")
    assert not data.get("quarantined")
    fonts = data.get("fonts") or []
    assert len(fonts) == data["font_count"]
    for item in fonts:
        assert item.get("license") in ALLOWED, item
        assert item.get("has_license_file"), item
        assert item.get("sha256")


UI_REGISTERED = {
    "fonts_pending/Inter/Interopsz_wght.ttf",
    "fonts_pending/JetBrains_Mono/JetBrainsMonowght.ttf",
}


def test_pending_fonts_are_staged_not_registered():
    data = safe_json.load(MANIFEST)
    pending = [f for f in (data.get("fonts") or [])
               if str(f.get("file") or "").replace("\\", "/").startswith("fonts_pending/")]
    assert pending, "FONTS_MANIFEST.json must list fonts_pending/ entries"
    pending_dir = os.path.join(REPO, "resources", "fonts_pending")
    assert os.path.isdir(pending_dir)
    assert os.path.abspath(pending_dir) != os.path.abspath(FONT_DIR)
    ui_seen = set()
    for item in pending:
        rel = str(item.get("file") or "").replace("\\", "/")
        if rel in UI_REGISTERED:
            assert item.get("status") == "registered (UI)", item
            ui_seen.add(rel)
        else:
            assert item.get("status") == "staged, not registered", item
        assert item.get("has_license_file"), item
        assert item.get("sha256")
        assert str(item.get("download_url") or "").startswith("https://"), item
        # Must not live under the registered FONT_DIR (sibling fonts_pending/).
        abs_file = os.path.abspath(
            os.path.join(REPO, "resources", item["file"].replace("/", os.sep)))
        assert os.path.isfile(abs_file), abs_file
        font_root = os.path.abspath(FONT_DIR)
        assert abs_file != font_root
        assert not abs_file.startswith(font_root + os.sep)
    assert ui_seen == UI_REGISTERED


def test_third_party_licenses_exists():
    path = os.path.join(REPO, "resources", "THIRD_PARTY_LICENSES.md")
    assert os.path.isfile(path)
    text = open(path, encoding="utf-8").read()
    assert "whisperX" in text
    assert "OFL" in text or "OFL-1.1" in text


def test_third_party_licenses_is_readable():
    """T04f-5-fix / T04g: index stays short; full texts live under licenses/."""
    path = os.path.join(REPO, "resources", "THIRD_PARTY_LICENSES.md")
    raw = open(path, "rb").read()
    assert len(raw) <= 512 * 1024, len(raw)
    text = raw.decode("utf-8")
    bad = [i for i, ch in enumerate(text) if ord(ch) < 32 and ch not in "\t\n\r"]
    assert bad == [], bad[:8]
    assert "## Python packages" in text
    assert "Noto Color Emoji" in text
    assert "PySide6" in text
    assert "PyQt6" not in text
    header = [ln for ln in text.splitlines() if ln.startswith("| Name |")]
    assert header and header[0].count("|") == 6
    sp_rows = [ln for ln in text.splitlines() if ln.startswith("| sentencepiece |")]
    assert sp_rows and "UNKNOWN" not in sp_rows[0]
    assert "Apache-2.0" in sp_rows[0]


def test_license_texts_shipped():
    """T04g: every index row has a local license file; files stay printable."""
    md = os.path.join(REPO, "resources", "THIRD_PARTY_LICENSES.md")
    lic_dir = os.path.join(REPO, "resources", "licenses")
    assert os.path.isdir(lic_dir)
    text = open(md, encoding="utf-8").read()
    rows = []
    in_pkg = False
    for ln in text.splitlines():
        if ln.startswith("## Python packages"):
            in_pkg = True
            continue
        if in_pkg and ln.startswith("## "):
            break
        if in_pkg and ln.startswith("| ") and not ln.startswith("| Name |") and not ln.startswith("|---"):
            rows.append(ln)
    assert len(rows) >= 50
    for ln in rows:
        cells = [c.strip() for c in ln.strip("|").split("|")]
        assert len(cells) == 5, ln
        rel = cells[4].strip("`")
        path = os.path.join(REPO, rel.replace("/", os.sep))
        assert os.path.isfile(path), path
        raw = open(path, "rb").read()
        assert len(raw) <= 512 * 1024, (path, len(raw))
        body = raw.decode("utf-8")
        bad = [i for i, ch in enumerate(body) if ord(ch) < 32 and ch not in "\t\n\r"]
        assert bad == [], (path, bad[:8])
