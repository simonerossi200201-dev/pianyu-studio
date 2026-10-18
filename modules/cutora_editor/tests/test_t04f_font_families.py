# -*- coding: utf-8 -*-
"""Builtin stylepacks and golden fixtures must resolve without fallback."""
from __future__ import annotations

import json
import os
import subprocess

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


def _walk_font_fields(obj, trail: str, out: list) -> None:
    if isinstance(obj, dict):
        for key, val in obj.items():
            path = "%s.%s" % (trail, key) if trail else key
            if key in ("font_family", "body", "emphasis") and isinstance(val, str) and val:
                if key == "font_family" or (
                    key in ("body", "emphasis") and "fonts" in trail.split(".")[-2:]
                ):
                    out.append((path, val))
            _walk_font_fields(val, path, out)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _walk_font_fields(item, "%s[%d]" % (trail, i), out)


def _walk_json_tree(root: str, rel: str, rows: list) -> None:
    path = os.path.join(root, rel) if rel else root
    if os.path.isdir(path):
        for name in sorted(os.listdir(path)):
            if name.startswith("."):
                continue
            _walk_json_tree(path, os.path.join(rel, name) if rel else name, rows)
        return
    low = path.lower()
    if not (low.endswith(".json") or low.endswith(".asproj.json")
            or low.endswith(".cutorarecipe")):
        return
    try:
        data = json.loads(open(path, encoding="utf-8").read())
    except (OSError, json.JSONDecodeError):
        return
    found = []
    _walk_font_fields(data, rel.replace("\\", "/"), found)
    rows.extend(found)


def collect_font_fields() -> list[tuple[str, str]]:
    rows = []
    _walk_json_tree(os.path.join(REPO, "resources", "stylepacks"), "", rows)
    _walk_json_tree(os.path.join(REPO, "tests", "golden"), "", rows)
    _walk_json_tree(os.path.join(REPO, "resources", "data"), "", rows)
    from cutora.subtitle.stylepack import default_subtitle_style
    from cutora.ui.panels.fonts import DEFAULT_FONT_FAMILY
    from cutora.ui.panels.subtitle_style import copy_style_full
    rows.append(("code/default_subtitle_style", default_subtitle_style()["font_family"]))
    rows.append(("code/DEFAULT_FONT_FAMILY", DEFAULT_FONT_FAMILY))
    rows.append(("code/copy_style_full(None)", copy_style_full(None)["font_family"]))
    return rows


def test_builtin_font_families_do_not_fallback():
    r = subprocess.run(
        [PY, os.path.join(REPO, "tests", "test_t04f_font_families.py"), "--check"],
        cwd=REPO, env=_child_env(), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    assert r.returncode == 0 and "T04F_FONT_FAMILIES_OK" in (r.stdout or ""), out[-6000:]


def _check() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import tempfile
    os.environ.setdefault(
        "CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_fontfam_"))
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from PySide6.QtWidgets import QApplication
    from cutora.ui.panels.fonts import load_embedded_fonts, resolve_font_request

    app = QApplication.instance() or QApplication(["t04f-font-families"])
    assert load_embedded_fonts()
    _ = app
    bad = []
    for path, value in collect_font_fields():
        rec = resolve_font_request(value)
        if rec.get("kind") == "fallback":
            bad.append("%s = %r -> %s" % (path, value, rec.get("family")))
    if bad:
        print("FALLBACK", len(bad), flush=True)
        print("\n".join(bad[:40]), flush=True)
        return 1
    print("T04F_FONT_FAMILIES_OK", len(collect_font_fields()), flush=True)
    return 0


if __name__ == "__main__":
    import sys
    if "--check" in sys.argv:
        raise SystemExit(_check())
    raise SystemExit(0)
