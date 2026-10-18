# SPDX-License-Identifier: GPL-3.0-or-later
"""
dock_panels.py — inspector layout helpers for CUTORA panels.

CollapsibleSection lives in collapsible.py (one class, one arrow).
Declarative rendering lives in inspector.build_inspector.
This module keeps add_fx_row and re-exports the public names tests import.
"""

import os

from cutora.core.log import get_logger
from cutora.ui.widgets.collapsible import CollapsibleSection
from cutora.ui.widgets.inspector import (
    Row,
    Section,
    add_inspect_row,
    build_inspector,
    install_inspector_limits,
    layout_attr_names,
)

_LOG = get_logger("cutora.ui.widgets.dock_panels")

__all__ = (
    "CollapsibleSection",
    "Row",
    "Section",
    "add_fx_row",
    "build_inspector",
    "install_inspector_limits",
    "layout_attr_names",
)


def add_fx_row(form, panel, attr, label=None):
    """Place ``panel.<attr>`` on ``form``. Missing widgets are loud, never silent."""
    widget = getattr(panel, attr, None)
    if widget is None:
        _LOG.error(
            "dock_panels.add_fx_row: panel has no widget %r",
            attr,
        )
        if os.environ.get("CUTORA_STRICT_UI", "").strip() == "1":
            raise AttributeError(
                "CUTORA_STRICT_UI: SubtitleStylePanel has no widget %r" % attr
            )
        return
    def _reset(p=panel, a=attr):
        from cutora.ui.panels.fx_fields import reset_fx_attr
        reset_fx_attr(p, a)
    add_inspect_row(form, label, widget, on_reset=_reset)
