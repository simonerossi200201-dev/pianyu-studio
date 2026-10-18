# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless health check: dead keys, packs, fonts, models, ffmpeg.

``python -m cutora --doctor`` prints a report and exits 1 on errors.
The GUI kicks this off on a daemon thread and hangs a status-bar badge.
"""
from __future__ import annotations

import ast
import os
import sys
from dataclasses import dataclass

from cutora.core.log import get_logger
from cutora.core.paths import FFMPEG, REPO_ROOT, RESOURCES, get_model_paths
from cutora.subtitle.style_schema import (
    DEPRECATED_UNPAINTED_KEYS,
    LIVE_CANONICAL_KEYS,
    PACK_STYLE_EXTRAS,
)

logger = get_logger("cutora.core.doctor")

PANEL_PATH = os.path.join(
    REPO_ROOT, "cutora", "ui", "panels", "subtitle_style.py")
RENDER_DIRS = (
    os.path.join(REPO_ROOT, "cutora", "render", "textfx"),
)
EXPECTED_FONTS = 240
FONT_FLOOR = 200


@dataclass
class Finding:
    code: str
    message: str
    severity: str = "error"
    key: str = ""


def _subscript_const(node) -> str | None:
    if not isinstance(node, ast.Subscript):
        return None
    sl = node.slice
    if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
        return sl.value
    return None


def scan_deprecated_writes(source: str, path: str = "") -> list[Finding]:
    """Assignments / setdefault of DEPRECATED_UNPAINTED_KEYS."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        logger.error("doctor cannot parse %s: %s", path or "<src>", exc)
        return [Finding("parse", "cannot parse %s: %s" % (path or "source", exc))]
    hits: list[Finding] = []
    seen: set[tuple[str, int]] = set()

    def _note(key: str, lineno: int, how: str) -> None:
        if key not in DEPRECATED_UNPAINTED_KEYS:
            return
        mark = (key, lineno)
        if mark in seen:
            return
        seen.add(mark)
        loc = "%s:%s" % (path or "<src>", lineno)
        hits.append(Finding(
            "dead_write",
            "panel writes deprecated key %s via %s at %s" % (key, how, loc),
            key=key,
        ))

    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign) and node.target is not None:
            targets = [node.target]
        elif isinstance(node, ast.AugAssign):
            targets = [node.target]
        for tgt in targets:
            key = _subscript_const(tgt)
            if key:
                _note(key, node.lineno, "assign")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "setdefault" and node.args:
                arg0 = node.args[0]
                if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                    _note(arg0.value, node.lineno, "setdefault")
    return hits


def _read_gets(source: str) -> set[str]:
    keys: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return keys
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "get" and node.args:
                arg0 = node.args[0]
                if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                    keys.add(arg0.value)
        key = _subscript_const(node) if isinstance(node, ast.Subscript) else None
        if key:
            keys.add(key)
    return keys


def scan_panel_vs_render(panel_src: str | None = None) -> list[Finding]:
    """Panel writes nobody reads / render reads nobody writes (v1 key shape)."""
    findings: list[Finding] = []
    if panel_src is None:
        try:
            panel_src = open(PANEL_PATH, encoding="utf-8").read()
        except OSError as exc:
            logger.error("doctor cannot read panel: %s", exc)
            return [Finding("panel", "cannot read subtitle_style.py: %s" % exc)]
    findings.extend(scan_deprecated_writes(panel_src, path="subtitle_style.py"))

    render_keys: set[str] = set()
    for root in RENDER_DIRS:
        if not os.path.isdir(root):
            continue
        for dirpath, _, files in os.walk(root):
            if os.path.basename(dirpath) == "__pycache__":
                continue
            for name in files:
                if not name.endswith(".py"):
                    continue
                fpath = os.path.join(dirpath, name)
                try:
                    blob = open(fpath, encoding="utf-8").read()
                except OSError:
                    logger.exception("doctor cannot read %s", fpath)
                    continue
                render_keys |= _read_gets(blob)
    dead_reads = sorted(k for k in render_keys if k in DEPRECATED_UNPAINTED_KEYS)
    for key in dead_reads:
        findings.append(Finding(
            "dead_read",
            "render still reads deprecated key %s" % key,
            key=key,
        ))
    return findings


def check_packs() -> list[Finding]:
    from cutora.subtitle.stylepack import (
        apply_pack_to_task, builtin_dir, list_pack_dirs, load_pack,
    )
    live = set(LIVE_CANONICAL_KEYS)
    extra = set(PACK_STYLE_EXTRAS)
    root = builtin_dir().replace("\\", "/")
    findings: list[Finding] = []
    n = 0
    for path in list_pack_dirs():
        if not path.replace("\\", "/").startswith(root):
            continue
        n += 1
        try:
            pack = load_pack(path)
        except Exception as exc:
            logger.exception("doctor pack load failed %s", path)
            findings.append(Finding(
                "pack_load", "cannot load pack %s: %s" % (os.path.basename(path), exc)))
            continue
        st = dict(pack.get("style") or {})
        st.update(pack.get("animation") or {})
        reset = sorted(live - set(st) - extra)
        leftover = sorted(k for k in st if k in DEPRECATED_UNPAINTED_KEYS)
        pid = pack.get("id") or os.path.basename(path)
        if reset:
            findings.append(Finding(
                "pack_reset",
                "pack %s reset=%s" % (pid, ",".join(reset))))
        if leftover:
            findings.append(Finding(
                "pack_leftover",
                "pack %s leftover=%s" % (pid, ",".join(leftover))))
        try:
            task = {"style": {"legacy_junk": 1}, "dims": [1080, 1920], "segments": []}
            apply_pack_to_task(task, pack)
            style = task.get("style") or {}
            applied_left = sorted(set(style) - live - extra)
            applied_reset = sorted(live - set(style))
            if applied_left:
                findings.append(Finding(
                    "pack_apply_leftover",
                    "apply %s leftover=%s" % (pid, ",".join(applied_left))))
            if applied_reset:
                findings.append(Finding(
                    "pack_apply_reset",
                    "apply %s reset=%s" % (pid, ",".join(applied_reset))))
        except Exception as exc:
            logger.exception("doctor apply_pack failed %s", pid)
            findings.append(Finding(
                "pack_apply", "apply_pack %s failed: %s" % (pid, exc)))
    if n == 0:
        findings.append(Finding("pack_count", "no builtin style packs found"))
    elif n != 14:
        findings.append(Finding(
            "pack_count", "builtin packs %d != 14" % n, severity="warn"))
    return findings


def check_fonts() -> list[Finding]:
    root = os.path.join(RESOURCES, "fonts")
    n = 0
    if os.path.isdir(root):
        for dirpath, _, files in os.walk(root):
            for name in files:
                if name.lower().endswith((".ttf", ".otf")):
                    n += 1
    if n < FONT_FLOOR:
        return [Finding(
            "fonts",
            "bundled fonts %d < %d (expected %d)" % (n, FONT_FLOOR, EXPECTED_FONTS))]
    if n != EXPECTED_FONTS:
        return [Finding(
            "fonts",
            "bundled fonts %d != %d" % (n, EXPECTED_FONTS),
            severity="warn")]
    return []


def check_models() -> list[Finding]:
    findings: list[Finding] = []
    try:
        paths = get_model_paths()
    except Exception as exc:
        logger.exception("doctor get_model_paths failed")
        return [Finding("models", "get_model_paths failed: %s" % exc)]
    for key in ("asr_path", "align_path"):
        p = paths.get(key)
        if not p or not os.path.isdir(p):
            findings.append(Finding("models", "missing %s: %s" % (key, p)))
    return findings


def check_ffmpeg() -> list[Finding]:
    if os.path.isfile(FFMPEG):
        return []
    import shutil
    which = shutil.which("ffmpeg")
    if which:
        return []
    return [Finding("ffmpeg", "ffmpeg not found at %s" % FFMPEG)]


def run_doctor() -> list[Finding]:
    findings: list[Finding] = []
    for fn in (
        scan_panel_vs_render,
        check_packs,
        check_fonts,
        check_models,
        check_ffmpeg,
    ):
        try:
            findings.extend(fn())
        except Exception as exc:
            logger.exception("doctor check %s failed", fn.__name__)
            findings.append(Finding(
                "crash", "%s crashed: %s" % (fn.__name__, exc)))
    return findings


def format_report(findings: list[Finding]) -> str:
    if not findings:
        return "DOCTOR OK"
    lines = ["DOCTOR %d" % len(findings)]
    for item in findings:
        lines.append("[%s] %s %s" % (item.severity, item.code, item.message))
    return "\n".join(lines)


def run_cli() -> int:
    findings = run_doctor()
    text = format_report(findings)
    sys.stdout.write(text + "\n")
    sys.stdout.flush()
    errors = [f for f in findings if f.severity == "error"]
    if errors:
        logger.error("doctor found %d error(s)", len(errors))
        return 1
    if findings:
        logger.warning("doctor found %d warning(s)", len(findings))
    else:
        logger.info("doctor ok")
    return 0
