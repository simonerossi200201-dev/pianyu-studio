# -*- coding: utf-8 -*-
"""T25: Explorer folder enum (injectable) + missing-grade colors."""
from __future__ import annotations

from cutora.batch.explorer_windows import list_open_explorer_folders, location_url_to_path
from cutora.batch.script_stitcher import (
    GRADE_COLOR,
    MISSING_HOLE,
    find_missing,
    transcript_incomplete,
)


def test_explorer_inject_two_windows(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()

    class W:
        def __init__(self, url):
            self.LocationURL = url

    def factory():
        ua = a.as_uri()
        ub = b.as_uri()
        return [W(ua), W(ua), W(ub)]

    got = list_open_explorer_folders(factory)
    assert len(got) == 2
    assert os_norm(got[0]) == os_norm(str(a))
    assert os_norm(got[1]) == os_norm(str(b))


def os_norm(p):
    import os
    return os.path.normcase(os.path.normpath(p))


def test_location_url_file():
    p = location_url_to_path("file:///D:/1_PROJECT/demo")
    assert p == r"D:\1_PROJECT\demo"


def test_missing_grade_three_colors():
    toks = ["a", "b", "c", "d", "e", "f"]
    missing = find_missing(
        [{"keep": True, "s_runs": [(0, 2), (5, 6)]}],
        toks, toks, ignore_scripture_refs=False)
    assert missing and missing[0]["grade"] == "missing"
    suspected = []
    find_missing(
        [{"keep": True, "s_runs": [(0, 2), (4, 5)]}],
        ["grace", "mercy", "and", "freedom", "so"],
        ["grace", "mercy", "and", "freedom", "so"],
        ignore_scripture_refs=False, suspected=suspected)
    assert suspected and suspected[0]["grade"] == "suspected"
    assert GRADE_COLOR["missing"].startswith("#E2")
    assert GRADE_COLOR["suspected"].startswith("#E8")
    assert GRADE_COLOR["incomplete"].startswith("#9A")
    assert MISSING_HOLE == 3
    assert transcript_incomplete([("a", 0.0, 1.0)], 8.0, env=None) is False
