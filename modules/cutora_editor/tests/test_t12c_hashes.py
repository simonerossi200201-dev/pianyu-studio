# -*- coding: utf-8 -*-
"""T12c 提交 2：revision + 逐文件 sha256 钉死；不匹配拒绝安装。"""
from __future__ import annotations

import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_hf_official_cdn_redirect_is_allowed():
    from cutora.core.net import NetPolicyError, assert_url_allowed
    import pytest
    assert assert_url_allowed("https://cdn-lfs.huggingface.co/file") == "cdn-lfs.huggingface.co"
    with pytest.raises(NetPolicyError):
        assert_url_allowed("https://us.aws.cdn.hf.co/file")
    assert assert_url_allowed(
        "https://us.aws.cdn.hf.co/file",
        via_redirect_from="https://huggingface.co/x/resolve/main/f",
    ) == "us.aws.cdn.hf.co"


def test_pin_script_exists():
    path = os.path.join(REPO, "tools", "pin_model_hashes.py")
    assert os.path.isfile(path)
    src = open(path, encoding="utf-8").read()
    assert "hf-lfs-oid" in src
    assert "huggingface.co/api/models" in src
    assert "expand=1" in src


def test_asr_catalog_has_pinned_revision_and_file_hashes():
    from cutora.asr.models import catalog, reset_catalog
    reset_catalog()
    need = ("asr-small", "asr-medium", "asr-large-v3", "asr-large-v3-turbo")
    by_id = {r["id"]: r for r in catalog()}
    for mid in need:
        row = by_id[mid]
        rev = str(row.get("revision") or "")
        assert len(rev) >= 40, mid
        assert rev != "main", mid
        files = row.get("files") or []
        assert files, mid
        for item in files:
            assert isinstance(item, dict), mid
            assert item.get("filename")
            sha = str(item.get("sha256") or "")
            assert len(sha) == 64, (mid, item.get("filename"))
            assert item.get("source") in ("hf-lfs-oid", "computed"), item
        weight = str(row.get("weight_file") or "model.bin")
        w = next(i for i in files if i["filename"] == weight)
        assert w["source"] == "hf-lfs-oid"
        assert row.get("sha256") == w["sha256"]


def test_download_rejects_sha256_mismatch_and_deletes(tmp_path, monkeypatch):
    from cutora.asr import download as D
    from cutora.core.commands import CommandError

    def fake_download_to_file(url, dest, **kwargs):
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(b"not-the-model")
        return {"bytes": 13, "resumed": False}

    monkeypatch.setattr(D, "download_to_file", fake_download_to_file)
    dest = tmp_path / "model.bin"
    with pytest.raises(CommandError) as err:
        D.download_hf_file(
            "Systran/faster-whisper-small", "model.bin", str(dest),
            revision="deadbeef", expected_sha256="0" * 64)
    msg = str(err.value)
    assert "拒绝安装" in msg
    assert "SHA256" in msg
    assert not dest.exists()


def test_download_model_refuses_without_pinned_sha(monkeypatch):
    from cutora.asr import download as D
    from cutora.core.commands import CommandError

    monkeypatch.setattr(D, "get_model", lambda _id: {
        "id": "asr-fake", "repo": "Systran/faster-whisper-small",
        "license": "MIT", "enabled": True, "revision": "abc",
        "dirname": "asr_fake", "weight_file": "model.bin",
        "files": [{"filename": "model.bin", "sha256": None, "source": "computed"}],
    })
    with pytest.raises(CommandError) as err:
        D.download_model("asr-fake")
    assert "sha256" in str(err.value).lower()
