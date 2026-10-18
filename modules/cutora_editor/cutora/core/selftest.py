# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless pack self-test: imports + model_paths + 215 fonts + GPU detect.

Invoked as ``python -m cutora --selftest``. Must not open a window.
"""
from __future__ import annotations

import os
import sys

EXPECTED_FONTS = 240


def run_selftest() -> int:
    from cutora.core.paths import get_model_paths, RESOURCES
    from cutora.asr.models import (
        ALIGN_NOT_BUNDLED_NOTE,
        packable_model_rows,
        required_pack_dirnames,
        verify_model,
    )
    from cutora.ui.panels.fonts import _iter_font_files
    from cutora.core.config import detect_gpu_config, DEVICE, COMPUTE_TYPE

    errors = []
    fonts = _iter_font_files()
    print("FONTS_COUNT", len(fonts))
    if len(fonts) != EXPECTED_FONTS:
        errors.append("fonts %d != %d" % (len(fonts), EXPECTED_FONTS))

    paths = get_model_paths()
    print("MODEL_PATHS", paths)
    for key in ("asr_path", "align_path"):
        p = paths.get(key)
        if not p or not os.path.isdir(p):
            errors.append("missing %s: %s" % (key, p))

    required = required_pack_dirnames()
    print("REQUIRED_DIRNAMES", required)
    packable = packable_model_rows()
    print("PACKABLE", [r.get("id") for r in packable])
    for leaf in required:
        row = None
        for r in packable:
            if str(r.get("dirname") or r.get("id")) == leaf:
                row = r
                break
        if row is None:
            errors.append("required dirname not packable: %s" % leaf)
            continue
        status = verify_model(row)
        print("VERIFY", row.get("id"), status)
        if status != "ready":
            errors.append("verify %s -> %s" % (row.get("id"), status))

    print("ALIGN_NOT_BUNDLED_NOTE", ALIGN_NOT_BUNDLED_NOTE)
    pin = os.path.join(RESOURCES, "data", "models.json")
    if not os.path.isfile(pin):
        errors.append("missing models.json pin")
    align_pin = os.path.join(RESOURCES, "data", "align_models.json")
    if not os.path.isfile(align_pin):
        errors.append("missing align_models.json pin")

    device, ctype, reason = detect_gpu_config(verbose=False)
    print("GPU_DETECT", device, ctype, reason)
    print("GPU_CONFIG_GLOBALS", DEVICE, COMPUTE_TYPE)
    hidden = (os.environ.get("CUDA_VISIBLE_DEVICES") or "") == ""
    if hidden and "CUDA_VISIBLE_DEVICES" in os.environ:
        if device != "cpu":
            errors.append(
                "CUDA_VISIBLE_DEVICES empty but detect_gpu_config=%s (%s)"
                % (device, reason))
        else:
            print("CPU_FALLBACK_OK", reason)

    try:
        import PySide6  # noqa: F401
        from PySide6 import __version__ as pyside_ver
        print("PYSIDE6", pyside_ver)
    except Exception as exc:
        errors.append("PySide6 import failed: %s" % exc)

    try:
        import faster_whisper  # noqa: F401
        print("FASTER_WHISPER ok")
    except Exception as exc:
        errors.append("faster_whisper import failed: %s" % exc)

    if errors:
        for item in errors:
            sys.stderr.write("SELFTEST_FAIL %s\n" % item)
        sys.stderr.flush()
        os._exit(1)
    print("SELFTEST_OK", flush=True)
    # Skip Qt/CUDA interpreter teardown — it can hang the pack child.
    os._exit(0)
