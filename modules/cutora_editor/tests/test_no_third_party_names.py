# -*- coding: utf-8 -*-
"""Shipped surfaces must not claim third-party product names.

Scope matches T04f-1d-4: cutora/, resources/, README*, NOTICE*.
docs/ is out of scope. Dependency names (PySide6, FFmpeg, faster-whisper)
are not in BANNED — those are required attributions.

This test blocks us *calling ourselves* by someone else's name. Ordinary
English words, dependency names, and technical identifiers (Qt,
HuggingFace, pathlib) are out of scope. When a rule hits normal code,
change the rule, not the code.
"""
from __future__ import annotations

import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BANNED = (
    ("davinci", re.compile(r"\bdavinci\b", re.IGNORECASE)),
    # Product phrase only. Bare "resolve" is ordinary English / pathlib.
    ("davinci-resolve", re.compile(r"davinci[\s_-]*resolve", re.IGNORECASE)),
    ("capcut", re.compile(r"\bcapcut\b", re.IGNORECASE)),
    ("jianying", re.compile(r"剪映")),
    ("premiere", re.compile(r"\bpremiere\b", re.IGNORECASE)),
    ("final-cut", re.compile(r"final[\s_-]*cut", re.IGNORECASE)),
    ("videokit", re.compile(r"\bvideokit\b", re.IGNORECASE)),
    ("wav2lip", re.compile(r"\bwav2lip\b", re.IGNORECASE)),
)

TEXT_EXT = {
    ".py", ".ps1", ".bat", ".cmd", ".json", ".txt", ".md", ".in", ".toml",
    ".yml", ".yaml", ".cfg", ".ini", ".qss", ".svg", ".xml", ".html",
    ".css", ".js", ".j2", ".lock",
}

SKIP_DIR_NAMES = {
    "__pycache__", ".git", "fonts", "fonts_pending",
}

# Word-frequency tables (English vocabulary, not product claims).
SKIP_FILE_NAMES = {"wenan_words.txt"}
SKIP_FILE_PREFIXES = ("linebreak_", "stopwords_")

# migrate_user_data.py must still mention the old key substring to rewrite it.
WHITELIST_REL = {
    os.path.join("cutora", "core", "migrate_user_data.py"),
}


def _rel(path: str) -> str:
    return os.path.relpath(path, REPO)


def _iter_scan_files():
    roots = [
        os.path.join(REPO, "cutora"),
        os.path.join(REPO, "resources"),
    ]
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
            for name in filenames:
                ext = os.path.splitext(name)[1].lower()
                if ext not in TEXT_EXT:
                    continue
                if name in SKIP_FILE_NAMES:
                    continue
                if name.endswith(".json") and name.startswith(SKIP_FILE_PREFIXES):
                    continue
                yield os.path.join(dirpath, name)
    for name in os.listdir(REPO):
        if name.startswith("README") or name.startswith("NOTICE"):
            path = os.path.join(REPO, name)
            if os.path.isfile(path):
                yield path


def test_no_third_party_product_names_in_shipped_files():
    hits = []
    for path in _iter_scan_files():
        rel = _rel(path)
        rel_norm = rel.replace("/", os.sep)
        if rel_norm in WHITELIST_REL:
            continue
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for label, cre in BANNED:
            for i, line in enumerate(text.splitlines(), 1):
                if cre.search(line):
                    hits.append("%s:%d: [%s] %s" % (
                        rel.replace("\\", "/"), i, label, line.strip()[:160]))
    assert hits == [], "third-party product names in shipped files:\n" + "\n".join(hits)


def test_cutora_ui_skip_is_debug_not_print():
    """Skip diagnostics must not use print.

    f99327b (Q60(5), inspector completeness) removed the
    ``[CUTORA UI] skipped:`` debug line from subtitle_style.py. Requiring
    that string back would resurrect a log the rewrite deleted on purpose.
    What still matters: this panel has no bare print, and no CUTORA UI print.
    """
    path = os.path.join(REPO, "cutora", "ui", "panels", "subtitle_style.py")
    src = open(path, encoding="utf-8").read()
    assert "[DaVinci UI]" not in src
    assert "apply_davinci_style" not in src
    assert "davinci_panels" not in src
    assert 'print(f"[CUTORA UI]' not in src
    assert 'print("[CUTORA UI]' not in src
    bare = []
    for i, line in enumerate(src.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("print(") or stripped.startswith("print ("):
            bare.append("%d: %s" % (i, stripped[:160]))
    assert bare == [], "bare print in subtitle_style.py:\n" + "\n".join(bare)


def test_dock_panels_module_exists():
    path = os.path.join(REPO, "cutora", "ui", "widgets", "dock_panels.py")
    assert os.path.isfile(path)
    src = open(path, encoding="utf-8").read()
    assert "def add_fx_row(" in src
    assert "apply_davinci_style" not in src
    assert "apply_dock_style" not in src
    gone = os.path.join(REPO, "cutora", "ui", "widgets", "davinci_panels.py")
    assert not os.path.isfile(gone)
