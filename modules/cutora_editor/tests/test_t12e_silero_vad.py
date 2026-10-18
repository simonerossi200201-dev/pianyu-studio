# -*- coding: utf-8 -*-
"""T12e 提交 2：VAD 组列出内置 Silero，pyannote 仍禁用。"""
from __future__ import annotations

import pytest


def test_silero_vad_is_bundled_ready_and_not_downloadable():
    from cutora.asr.models import catalog, reset_catalog, status_row
    reset_catalog()
    found = {r["id"]: r for r in catalog()}
    row = found["vad-silero"]
    assert row["license"] == "MIT"
    assert row["enabled"] is True
    assert row.get("local_only") is True
    assert row.get("bundled") is True
    assert status_row(row)["status"] == "ready"
    py = found["vad-segmentation"]
    assert py["enabled"] is False
    assert "gated" in (py.get("note") or "") or py.get("gated") is True


def test_silero_vad_download_and_delete_are_refused():
    from cutora.asr.download import delete_model, download_model
    from cutora.core.commands import CommandError
    with pytest.raises(CommandError):
        download_model("vad-silero")
    with pytest.raises(CommandError):
        delete_model("vad-silero")
