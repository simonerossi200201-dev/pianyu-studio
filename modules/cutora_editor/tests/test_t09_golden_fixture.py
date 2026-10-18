# -*- coding: utf-8 -*-
"""Golden USER_DATA fixture: no secrets, and fx settings follow CUTORA_USER_DATA."""
from __future__ import annotations

import json
import os

from cutora.core import safe_json
from cutora.core.secrets import SECRET_SUFFIXES

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(REPO, "tests", "golden", "_fixture_user_data", "app_settings.json")


def _walk_keys(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = "%s.%s" % (prefix, k) if prefix else str(k)
            yield path, k, v
            yield from _walk_keys(v, path)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_keys(v, "%s[%d]" % (prefix, i))


def test_fixture_exists_and_matches_t00_fx():
    assert os.path.isfile(FIXTURE), FIXTURE
    data = safe_json.load(FIXTURE)
    fx = data["audio_fx"]
    assert fx["spatial_wet"] == 0.1
    assert fx["mic_amount"] == 0.2
    assert fx["mic_texture"] is True
    assert fx["spatial_enabled"] is True
    assert fx["ai_enabled"] is False
    assert data["export_video_mode"] == "compressed"
    assert data["parallel_export_workers"] == 6
    assert all(not v for v in fx["sfx_enabled"].values())


def test_fixture_has_no_secret_fields():
    data = safe_json.load(FIXTURE)
    bad = []
    for path, key, _val in _walk_keys(data):
        lower = str(key).lower()
        if any(lower.endswith(suf) for suf in SECRET_SUFFIXES) or "token" in lower:
            bad.append(path)
    assert not bad, "fixture still has secret-like fields: %s" % bad


def test_load_fx_settings_follows_cutora_user_data(tmp_path, monkeypatch):
    payload = {
        "audio_fx": {
            "mic_amount": 0.9,
            "spatial_wet": 0.1,
            "mic_texture": True,
        }
    }
    (tmp_path / "app_settings.json").write_text(
        json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path))
    from cutora.audio.sfx_library import load_fx_settings
    fx = load_fx_settings()
    assert fx["mic_amount"] == 0.9
    assert fx["spatial_wet"] == 0.1
