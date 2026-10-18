# -*- coding: utf-8 -*-
"""T12b 提交 3：模型管理器、白名单下载、HF_ENDPOINT 忽略。"""
from __future__ import annotations

import os

import pytest


def test_hf_endpoint_unofficial_is_ignored_and_warned(monkeypatch, caplog):
    from cutora.asr.models import hf_base_url, hf_file_url
    monkeypatch.setenv("HF_ENDPOINT", "https://hf-mirror.com")
    import logging
    caplog.set_level(logging.WARNING)
    base = hf_base_url()
    assert base == "https://huggingface.co"
    assert "hf-mirror" not in hf_file_url("Systran/faster-whisper-small", "model.bin")
    text = caplog.text.lower()
    assert "hf-mirror" in text or "hf_endpoint" in text or "忽略" in caplog.text


def test_hf_file_url_uses_official_host_and_net_allowlist():
    from cutora.asr.models import hf_file_url
    from cutora.core.net import assert_url_allowed
    url = hf_file_url("Systran/faster-whisper-small", "model.bin")
    assert url.startswith("https://huggingface.co/")
    assert assert_url_allowed(url) == "huggingface.co"


def test_noncompliant_license_is_disabled():
    from cutora.asr.models import ALLOWED_LICENSES, catalog, is_license_allowed
    assert "MIT" in ALLOWED_LICENSES
    rows = catalog()
    ids = {r["id"] for r in rows}
    assert "asr-small" in ids
    assert "asr-large-v3-turbo" in ids
    for row in rows:
        if row.get("gated"):
            assert row.get("enabled") is False, row["id"]


def test_list_command_schema_and_local_status():
    from cutora.core.command_schema import check_schema
    from cutora.core.commands import get_bus, get_registry
    spec = get_registry().get("asr.models.list")
    assert spec.headless is True
    assert spec.async_job is False
    assert spec.dangerous is False
    assert len(spec.doc_zh) >= 40
    check_schema(spec.params_schema)
    out = get_bus().execute("asr.models.list", {}, source="cli")
    assert "models" in out
    small = next(m for m in out["models"] if m["id"] == "asr-small")
    assert small["status"] in ("ready", "checksum_failed", "missing")
    assert small["license"]
    vads = [m for m in out["models"] if m["kind"] == "vad"]
    py = next((m for m in vads if m["id"] == "vad-segmentation"), None)
    if py is not None:
        assert py["enabled"] is False


def test_download_and_delete_schema_flags():
    from cutora.core.command_schema import check_schema
    from cutora.core.commands import NeedConfirm, get_bus, get_registry
    dl = get_registry().get("asr.models.download")
    assert dl.headless is True
    assert dl.async_job is True
    assert dl.dangerous is False
    assert len(dl.doc_zh) >= 40
    check_schema(dl.params_schema)
    rm = get_registry().get("asr.models.delete")
    assert rm.dangerous is True
    assert rm.headless is True
    assert len(rm.doc_zh) >= 40
    check_schema(rm.params_schema)
    with pytest.raises(NeedConfirm):
        get_bus().execute("asr.models.delete", {"id": "asr-small"}, source="api")


def test_download_goes_through_net_and_resumes(tmp_path, monkeypatch):
    from cutora.asr import download as D
    from cutora.core.net import NetPolicyError

    calls = []

    def fake_download_to_file(url, dest, **kwargs):
        calls.append((url, dest, kwargs.get("resume")))
        if "hf-mirror" in url:
            raise NetPolicyError("主机不在白名单")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(b"hello-model")
        return {"bytes": 11, "resumed": False}

    monkeypatch.setattr(D, "download_to_file", fake_download_to_file)
    monkeypatch.setenv("HF_ENDPOINT", "https://hf-mirror.com")
    dest = tmp_path / "model.bin"
    info = D.download_hf_file(
        "Systran/faster-whisper-small", "model.bin", str(dest), expected_sha256=None)
    assert calls, "must call download_to_file"
    assert calls[0][0].startswith("https://huggingface.co/")
    assert os.path.isfile(dest)
    assert info["bytes"] == 11


def test_sha256_mismatch_marks_checksum_failed(tmp_path):
    from cutora.asr.models import verify_weight
    p = tmp_path / "model.bin"
    p.write_bytes(b"abc")
    st = verify_weight(str(p), expected="0" * 64)
    assert st == "checksum_failed"
    st2 = verify_weight(str(p), expected=None)
    assert st2 == "ready"
