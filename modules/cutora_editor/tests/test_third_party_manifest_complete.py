# -*- coding: utf-8 -*-
"""T10a: every sampled binary basename must appear in THIRD_PARTY.md.

Scope — do **not** walk ``runtime/venv`` (site-packages is multi-GB). Scan only:

- ``runtime/ffmpeg/`` (non-recursive): ``.dll .pyd .exe .onnx .bin .ttf``
- ``runtime/models/``: top-level entry names, plus those extensions under the
  models tree (weights such as ``model.bin`` / ``pytorch_model.bin``)
- ``resources/fonts/``: ``.ttf`` only

If none of those trees exist (CI without the pack), fall back to
``tests/fixtures/third_party_sample/``.

A basename matches if it occurs as a substring of ``THIRD_PARTY.md``.
"""
from __future__ import annotations

import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(REPO, "THIRD_PARTY.md")
EXTS = {".dll", ".pyd", ".exe", ".onnx", ".bin", ".ttf"}

FFMPEG_DIR = os.path.join(REPO, "runtime", "ffmpeg")
MODELS_DIR = os.path.join(REPO, "runtime", "models")
FONTS_DIR = os.path.join(REPO, "resources", "fonts")
FIXTURE_DIR = os.path.join(REPO, "tests", "fixtures", "third_party_sample")


def _iter_ext_files(root: str, recursive: bool):
    if not os.path.isdir(root):
        return
    if not recursive:
        for name in os.listdir(root):
            path = os.path.join(root, name)
            if os.path.isfile(path) and os.path.splitext(name)[1].lower() in EXTS:
                yield path
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if os.path.splitext(name)[1].lower() in EXTS:
                yield os.path.join(dirpath, name)


def _scan_targets():
    """Return (file_paths, extra_basenames) for the documented scope."""
    have_pack = (
        os.path.isdir(FFMPEG_DIR)
        or os.path.isdir(MODELS_DIR)
        or os.path.isdir(FONTS_DIR)
    )
    paths = []
    extra = []
    if have_pack:
        paths.extend(_iter_ext_files(FFMPEG_DIR, recursive=False))
        paths.extend(_iter_ext_files(MODELS_DIR, recursive=True))
        paths.extend(_iter_ext_files(FONTS_DIR, recursive=True))
        if os.path.isdir(MODELS_DIR):
            extra.extend(os.listdir(MODELS_DIR))
        return paths, extra
    paths.extend(_iter_ext_files(FIXTURE_DIR, recursive=True))
    return paths, extra


def test_third_party_manifest_complete():
    assert os.path.isfile(MANIFEST), "missing repo-root THIRD_PARTY.md"
    text = open(MANIFEST, encoding="utf-8").read()
    assert "GPL" in text
    paths, extra = _scan_targets()
    assert paths or extra, "no sampled binaries; add runtime trees or tests/fixtures/third_party_sample"
    missing = []
    seen = set()
    for path in paths:
        base = os.path.basename(path)
        if base in seen:
            continue
        seen.add(base)
        if base not in text:
            missing.append(base)
    for name in extra:
        if name not in text:
            missing.append(name)
    assert not missing, "basenames missing from THIRD_PARTY.md: %s" % (
        ", ".join(missing[:40]),
    )


def test_third_party_flags_gpl_incompatible_items():
    """Do not hide the STOP items T10a found on disk / official cards."""
    text = open(MANIFEST, encoding="utf-8").read()
    assert "CC-BY-NC-4.0" in text
    assert "cudnn64_9.dll" in text
    assert "GPL 兼容" in text
    assert "否" in text
