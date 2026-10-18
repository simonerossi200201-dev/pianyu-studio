# -*- coding: utf-8 -*-
"""Render resources/theme/theme_dark.qss from the Jinja2 template + tokens.

Run from the repo root:

    runtime/venv/Scripts/python.exe tools/build_qss.py
"""
from __future__ import annotations

import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

TEMPLATE = os.path.join(REPO, "cutora", "ui", "theme", "theme_dark.qss.j2")
OUTPUT = os.path.join(REPO, "resources", "theme", "theme_dark.qss")
THEME_DIR = os.path.join(REPO, "resources", "theme")
LUCIDE_DIR = os.path.join(REPO, "resources", "icons", "lucide")

# Lucide stems tinted into resources/theme/ for QSS ``image: url(...)``.
QSS_ICONS = (
    ("check.svg", "qss_check.svg", "fg0"),
    ("check.svg", "qss_check_disabled.svg", "fg_muted"),
    ("chevron-up.svg", "qss_chevron_up.svg", "fg1"),
    ("chevron-down.svg", "qss_chevron_down.svg", "fg1"),
)

_JINJA_RE = re.compile(r"\{\{.*?\}\}|\{#.*?#\}|\{%.*?%\}", re.S)
_PX_RE = re.compile(r"(-?\d+)px")
_RADIUS_PROP_RE = re.compile(
    r"border(?:-[a-z]+)*-radius\s*:\s*([^;]+)", re.I)
_MIN_H_PROP_RE = re.compile(r"min-height\s*:\s*([^;]+)", re.I)
_PAD_PROP_RE = re.compile(r"(?:padding|margin)(?:-[a-z]+)?\s*:\s*([^;]+)", re.I)


def check_template_bare_px(src: str) -> list[str]:
    """Fail on ``Npx`` outside Jinja, except 0px / 1px hairlines."""
    protected = _JINJA_RE.sub(" ", src)
    fails: list[str] = []
    for i, line in enumerate(protected.splitlines(), 1):
        for match in _PX_RE.finditer(line):
            n = int(match.group(1))
            if abs(n) <= 1:
                continue
            fails.append("template:%d bare %spx" % (i, match.group(1)))
    return fails


def check_rendered_qss(text: str) -> list[str]:
    """Closed sets for radius, control min-height, and padding/margin."""
    from cutora.ui.theme.tokens import (
        MIN_HEIGHT_ALLOWED,
        RADIUS_ALLOWED,
        SPACE_ALLOWED,
    )

    fails: list[str] = []
    radius_ok = set(RADIUS_ALLOWED) | {0}
    min_h_ok = set(MIN_HEIGHT_ALLOWED) | {0}
    for i, line in enumerate(text.splitlines(), 1):
        for match in _RADIUS_PROP_RE.finditer(line):
            for raw in _PX_RE.findall(match.group(1)):
                val = int(raw)
                if val not in radius_ok:
                    fails.append("output:%d border-radius %spx" % (i, raw))
        for match in _MIN_H_PROP_RE.finditer(line):
            for raw in _PX_RE.findall(match.group(1)):
                val = int(raw)
                if val not in min_h_ok:
                    fails.append("output:%d min-height %spx" % (i, raw))
        for match in _PAD_PROP_RE.finditer(line):
            for raw in _PX_RE.findall(match.group(1)):
                val = abs(int(raw))
                if val not in SPACE_ALLOWED:
                    fails.append("output:%d padding/margin %spx" % (i, raw))
    return fails


def render_qss(ctx: dict | None = None) -> str:
    from cutora.ui.theme.qss import render
    from cutora.ui.theme.tokens import qss_context

    data = dict(qss_context() if ctx is None else ctx)
    return render(data)


def write_qss_icons(ctx: dict | None = None, dest_dir: str | None = None) -> list[str]:
    """Write token-tinted Lucide SVGs next to theme_dark.qss (or ``dest_dir``)."""
    from cutora.ui.theme.qss import write_qss_icons as _write

    return _write(ctx, dest_dir or THEME_DIR)


def write_qss(path: str | None = None) -> str:
    from cutora.ui.theme.tokens import DEFAULT_THEME_ID, load_theme, qss_context

    src = open(TEMPLATE, encoding="utf-8").read()
    bare = check_template_bare_px(src)
    if bare:
        raise SystemExit("QSS template has bare px:\n  " + "\n  ".join(bare[:40]))

    load_theme(DEFAULT_THEME_ID)
    ctx = qss_context()
    text = render_qss(ctx)
    closed = check_rendered_qss(text)
    if closed:
        raise SystemExit("rendered QSS token violations:\n  " + "\n  ".join(closed[:40]))

    out = path or OUTPUT
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    write_qss_icons(ctx)
    return out


def main() -> int:
    path = write_qss()
    print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
