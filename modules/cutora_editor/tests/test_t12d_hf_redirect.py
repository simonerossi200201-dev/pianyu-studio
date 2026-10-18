# -*- coding: utf-8 -*-
"""T12d 提交 2：*.hf.co 只允许作为 huggingface.co 的 302 落点。先写先红。"""
from __future__ import annotations

import pytest


def test_direct_hf_co_request_is_rejected():
    from cutora.core.net import NetPolicyError, assert_url_allowed, request
    with pytest.raises(NetPolicyError):
        assert_url_allowed("https://cdn-lfs.hf.co/file")
    with pytest.raises(NetPolicyError):
        request("https://cdn-lfs.hf.co/x", purpose="model")


def test_hf_co_download_ok_only_via_huggingface_redirect():
    from urllib.request import Request
    from cutora.core.net import _GuardRedirect, assert_url_allowed
    src = "https://huggingface.co/Systran/faster-whisper-small/resolve/main/model.bin"
    dst = "https://us.aws.cdn.hf.co/lfs/model.bin"
    host = assert_url_allowed(dst, via_redirect_from=src)
    assert host == "us.aws.cdn.hf.co"
    handler = _GuardRedirect()
    new_req = handler.redirect_request(
        Request(src), None, 302, "Found", {}, dst)
    assert new_req is not None
    assert "hf.co" in (new_req.full_url or "")
