# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""色阶默认不改像素；调色滑条范围加宽后，旧数值的公式不变。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_default_levels_leave_identity_and_exposure_bytes():
    import numpy as np
    from cutora.render.grade import (
        apply_grade_u8, grade_changes_picture, identity_grade, is_identity, levels_is_identity,
    )

    g = identity_grade()
    assert is_identity(g)
    assert levels_is_identity(g["levels"])
    assert not grade_changes_picture(g)
    bare = dict(g)
    del bare["levels"]
    assert is_identity(bare)
    assert not grade_changes_picture(bare)

    rgb = np.zeros((2, 2, 3), np.uint8)
    rgb[..., 0] = 40
    rgb[..., 1] = 90
    rgb[..., 2] = 160
    same = apply_grade_u8(rgb, [g])
    assert same is rgb

    exposed = identity_grade()
    exposed["exposure"] = 0.5
    without = dict(exposed)
    del without["levels"]
    a = apply_grade_u8(rgb.copy(), [exposed])
    b = apply_grade_u8(rgb.copy(), [without])
    assert np.array_equal(a, b)
    assert not np.array_equal(a, rgb)


def test_levels_black_point_and_gamma():
    import numpy as np
    from cutora.render.grade import apply_grade_u8, identity_grade

    crush = identity_grade()
    crush["levels"]["in_black"] = 128
    rgb = np.zeros((2, 1, 3), np.uint8)
    rgb[0] = 128
    rgb[1] = 255
    out = apply_grade_u8(rgb, [crush])
    assert int(out[0, 0, 0]) == 0
    assert int(out[1, 0, 0]) == 255

    lift = identity_grade()
    lift["levels"]["gamma"] = 2.2
    mid = np.full((1, 1, 3), 128, np.uint8)
    raised = apply_grade_u8(mid, [lift])
    assert int(raised[0, 0, 0]) > 128


def test_grade_limits_and_editor_match_wider_ranges():
    from PySide6.QtWidgets import QApplication
    from cutora.ui.monitor_layer_ops import GRADE_LIMITS
    from cutora.ui.panels.grade import GradeEditor

    app = QApplication.instance() or QApplication(["q212"])
    assert GRADE_LIMITS["exposure"] == (-4.0, 4.0)
    assert GRADE_LIMITS["contrast"] == (-1.0, 3.0)
    assert GRADE_LIMITS["saturation"][1] == 4.0
    assert GRADE_LIMITS["temperature"] == (-5500.0, 8000.0)
    ed = GradeEditor()
    assert ed.exposure.maximum() == 4.0
    assert ed.contrast.maximum() == 3.0
    assert ed.saturation.maximum() == 4.0
    assert ed.temperature.minimum() == -5500.0
    g = ed.grade()
    assert g["levels"]["in_black"] == 0
    assert g["levels"]["in_white"] == 255
    assert g["levels"]["gamma"] == 1
    ed.lv_in_black.setValue(32)
    ed.lv_gamma.setValue(1.8)
    got = ed.grade()
    assert got["levels"]["in_black"] == 32
    assert abs(got["levels"]["gamma"] - 1.8) < 1e-6
    assert got["exposure"] == 0
    app.processEvents()
