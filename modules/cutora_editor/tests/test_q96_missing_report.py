# SPDX-License-Identifier: GPL-3.0-or-later
"""Q96：「打开缺失报告位置」必须真的能找到那份清单。"""
import os
import time

from cutora.batch import script_stitcher as st


def _write(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def test_dirs_cover_first_folder_and_each_group(tmp_path):
    a, b = str(tmp_path / "A"), str(tmp_path / "B")
    dirs = st.missing_report_dirs(
        os.path.join(a, "_stitched"),
        {"1": os.path.join(a, "_stitched"), "2": os.path.join(b, "_stitched")},
        [a, b])
    assert dirs[0] == os.path.join(a, "_stitched")
    assert os.path.join(b, "_stitched") in dirs
    assert len(dirs) == len(set(dirs))


def test_dirs_survive_empty_inputs():
    assert st.missing_report_dirs("", None, None) == []
    assert st.missing_report_dirs("", {}, ["D:/x"]) == [
        os.path.join("D:/x", "_stitched")]


def test_find_picks_newest(tmp_path):
    old = _write(str(tmp_path / "A" / "_stitched" / st.MISSING_NAME), "old")
    time.sleep(0.02)
    new = _write(str(tmp_path / "B" / "_stitched" / st.MISSING_NAME), "new")
    os.utime(old, (1, 1))
    got = st.find_missing_report([os.path.dirname(old), os.path.dirname(new)])
    assert got == new


def test_find_returns_empty_when_absent(tmp_path):
    assert st.find_missing_report([str(tmp_path)]) == ""
    assert st.find_missing_report([]) == ""
    assert st.find_missing_report([str(tmp_path / "nope")]) == ""


def test_real_layout_is_found(tmp_path):
    """Luca 的真实摆法：<素材目录>\\_stitched\\缺失文案清单.txt"""
    clips = tmp_path / "T0072-李良-0923-v01" / "clips"
    want = _write(str(clips / "_stitched" / st.MISSING_NAME))
    dirs = st.missing_report_dirs(
        os.path.join(str(clips), "_stitched"), None, [str(clips)])
    assert st.find_missing_report(dirs) == want
