# -*- coding: utf-8 -*-
"""Zip-slip: extractall must refuse .. and absolute members."""
from __future__ import annotations

import os
import zipfile

import pytest

from cutora.core.zipsafe import ZipSlipError, safe_extractall


def _write_zip(path, name, payload=b"nope"):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(name, payload)


def test_zip_slip_dotdot_raises_and_writes_nothing(tmp_path):
    zpath = tmp_path / "evil.zip"
    _write_zip(zpath, "../x")
    dest = tmp_path / "out"
    dest.mkdir()
    before = sorted(os.listdir(dest))
    with zipfile.ZipFile(zpath, "r") as zf:
        with pytest.raises((ZipSlipError, ValueError)):
            safe_extractall(zf, str(dest))
    assert sorted(os.listdir(dest)) == before
    assert not (tmp_path / "x").exists()


def test_zip_slip_absolute_raises(tmp_path):
    zpath = tmp_path / "abs.zip"
    _write_zip(zpath, "/tmp/cutora_zip_slip_x")
    dest = tmp_path / "out"
    dest.mkdir()
    with zipfile.ZipFile(zpath, "r") as zf:
        with pytest.raises((ZipSlipError, ValueError)):
            safe_extractall(zf, str(dest))
    assert os.listdir(dest) == []


def test_safe_extractall_keeps_nested_file(tmp_path):
    zpath = tmp_path / "ok.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("pack.json", b"{}")
        zf.writestr("items/a/item.json", b"{\"id\":\"a\"}")
    dest = tmp_path / "out"
    dest.mkdir()
    with zipfile.ZipFile(zpath, "r") as zf:
        safe_extractall(zf, str(dest))
    assert (dest / "pack.json").is_file()
    assert (dest / "items" / "a" / "item.json").is_file()
