# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""About / source-offer text. Internal use; not a public GPL distribution."""
from __future__ import annotations

from cutora.core.brand import APP_DISPLAY_NAME, APP_NAME, APP_VERSION


def about_text() -> str:
    """Plain-text About blurb for a later UI hook."""
    return (
        "%s / %s v%s\n"
        "内部使用，不对外分发。见仓库根 INTERNAL_USE.txt。\n"
        "Third-party notices: THIRD_PARTY.md (repo root)\n"
        % (APP_DISPLAY_NAME, APP_NAME, APP_VERSION)
    )
