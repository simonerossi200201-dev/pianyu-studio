# -*- coding: utf-8 -*-
"""T14 appendix C: hard-seek vs sequential catch-up. No Qt."""
from __future__ import annotations

from cutora.render.readers import need_hard_seek


def test_need_hard_seek_backward():
    assert need_hard_seek(1000, 5000, 6000, 5000, False) is True
    assert need_hard_seek(1000, None, None, 5000, True) is True


def test_need_hard_seek_small_forward_is_catchup():
    assert need_hard_seek(1600, 1000, 1400, 1400, False) is False
    assert need_hard_seek(2500, None, None, 1000, True) is False


def test_need_hard_seek_big_forward():
    assert need_hard_seek(4000, 1000, 1400, 1400, False) is True
    assert need_hard_seek(4000, None, None, 1000, True) is True


def test_need_hard_seek_luca_storm_pattern():
    # After jump, UI only ~250ms ahead of queued last frame: must NOT cap.set.
    assert need_hard_seek(150250, 150044, 150080, 150080, False) is False
