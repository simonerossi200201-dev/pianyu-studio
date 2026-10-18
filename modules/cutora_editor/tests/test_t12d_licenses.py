# -*- coding: utf-8 -*-
"""T12d 提交 1：许可证白名单是全仓库唯一常量，测试先写先红。"""
from __future__ import annotations

import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WANT = frozenset({
    "MIT",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "CC0-1.0",
    "CC-BY-4.0",
    "Unlicense",
})


def test_whitelist_constant_matches_the_published_set():
    from cutora.core.licenses import ALLOWED_MODEL_LICENSES
    assert ALLOWED_MODEL_LICENSES == WANT
    from cutora.asr import models as M
    assert M.ALLOWED_LICENSES is ALLOWED_MODEL_LICENSES


def test_enabled_json_rows_are_not_gated():
    """Internal use: NC may be enabled. Gated weights stay off."""
    from cutora.core.licenses import ALLOWED_MODEL_LICENSES
    assert ALLOWED_MODEL_LICENSES == WANT
    for rel in ("resources/data/models.json", "resources/data/align_models.json"):
        path = os.path.join(REPO, rel)
        data = json.loads(open(path, encoding="utf-8").read())
        for row in data.get("models") or []:
            if not row.get("enabled"):
                continue
            assert row.get("gated") in (None, False), row.get("id")
            assert row.get("license"), row.get("id")


def test_gated_empty_other_must_be_disabled():
    """Gated / empty / 'other' stay off. NC (internal) may be enabled."""
    for rel in ("resources/data/models.json", "resources/data/align_models.json"):
        path = os.path.join(REPO, rel)
        data = json.loads(open(path, encoding="utf-8").read())
        for row in data.get("models") or []:
            lic = str(row.get("license") or "").strip()
            if row.get("gated") or not lic or lic.lower() == "other":
                assert row.get("enabled") is False, (row.get("id"), lic)


def test_align_sv_enabled_da_hr_gl_vi_still_disabled():
    from cutora.asr.models import catalog, reset_catalog
    reset_catalog()
    found = {r["id"]: r for r in catalog()}
    assert found["align-sv"]["enabled"] is True
    assert found["align-sv"]["license"] == "CC0-1.0"
    for mid in ("align-da", "align-hr", "align-gl", "align-vi"):
        assert found[mid]["enabled"] is False, mid
