# SPDX-License-Identifier: GPL-3.0-or-later
"""Q96 片段散落：素材可以是整个文件夹，也可以是单条视频。"""
import os

import pytest

from cutora.batch import script_broll_scatter as sc


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"0" * 32)
    return path


def test_resolve_sources_folder(tmp_path):
    d = tmp_path / "clips"
    _touch(str(d / "b.mp4"))
    _touch(str(d / "a10.mp4"))
    _touch(str(d / "a2.mp4"))
    _touch(str(d / "note.txt"))
    _touch(str(d / "_scatter" / "a2a.mp4"))
    got = [os.path.basename(p) for p in sc.resolve_sources([str(d)])]
    assert got == ["a2.mp4", "a10.mp4", "b.mp4"]


def test_resolve_sources_single_files(tmp_path):
    d = tmp_path / "clips"
    one = _touch(str(d / "one.mov"))
    two = _touch(str(d / "two.mp4"))
    _touch(str(d / "three.mp4"))
    got = sc.resolve_sources([two, one])
    assert got == [two, one]


def test_resolve_sources_mixed_dedupes(tmp_path):
    d = tmp_path / "clips"
    one = _touch(str(d / "one.mp4"))
    _touch(str(d / "two.mp4"))
    got = sc.resolve_sources([one, str(d), one])
    assert [os.path.basename(p) for p in got] == ["one.mp4", "two.mp4"]


def test_resolve_sources_ignores_junk(tmp_path):
    d = tmp_path / "clips"
    _touch(str(d / "a.txt"))
    assert sc.resolve_sources([str(d), "", str(tmp_path / "nope")]) == []


def test_default_out_dir(tmp_path):
    d = tmp_path / "clips"
    one = _touch(str(d / "one.mp4"))
    assert sc.default_out_dir([str(d)]) == os.path.join(str(d), "_scatter")
    assert sc.default_out_dir([one]) == os.path.join(str(d), "_scatter")
    assert sc.default_out_dir([]) == ""


def test_piece_stems_disambiguates_same_name(tmp_path):
    a = _touch(str(tmp_path / "A" / "clip.mp4"))
    b = _touch(str(tmp_path / "B" / "clip.mp4"))
    c = _touch(str(tmp_path / "A" / "other.mp4"))
    stems = sc.piece_stems([a, b, c])
    assert stems[a] != stems[b]
    assert stems[c] == "other"
    assert len(set(stems.values())) == 3


def test_run_scatter_needs_sources(tmp_path):
    with pytest.raises(ValueError):
        sc.run_scatter({})
    d = tmp_path / "empty"
    d.mkdir()
    with pytest.raises(ValueError):
        sc.run_scatter({"clips": [str(d)]})
