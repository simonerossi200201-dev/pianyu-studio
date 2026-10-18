# -*- coding: utf-8 -*-
"""T08e / BUG-26: dest folder holds only mp4 under default sidecar flags."""
from __future__ import annotations

import os

from cutora.render.export_dest import unique_mp4_path
from cutora.render.export_sidecar import sidecar_flags, write_all


def test_t08e_dest_folder_only_mp4(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "cutora.render.export_sidecar._load_app_settings", lambda: {})
    dest = tmp_path / "dest"
    dest.mkdir()
    src = tmp_path / "talk.mp4"
    src.write_bytes(b"fake")
    out = unique_mp4_path(str(dest), str(src), name="T023")
    open(out, "wb").write(b"mp4")
    flags = sidecar_flags({})
    assert flags["srt"] is False
    assert flags["cover"] is False
    assert flags["report"] is False
    write_all(out, {"video": str(src), "segments": [], "_export_dest_folder": str(dest)})
    names = sorted(os.listdir(dest))
    assert names == [os.path.basename(out)], names
    assert all(n.endswith(".mp4") for n in names)


def test_unique_mp4_uses_task_name(tmp_path):
    dest = unique_mp4_path(str(tmp_path), r"D:\clips\T0023-s001-p04.mp4", name="T023")
    assert os.path.basename(dest) == "T023_FINAL.mp4"


def test_unique_mp4_collision(tmp_path):
    src = str(tmp_path / "a.mp4")
    first = unique_mp4_path(str(tmp_path), src)
    open(first, "wb").write(b"1")
    second = unique_mp4_path(str(tmp_path), src)
    assert second != first
    assert second.endswith("_2.mp4")
