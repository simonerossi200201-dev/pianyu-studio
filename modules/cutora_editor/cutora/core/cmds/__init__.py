# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the process-wide command registry from spec modules."""
from __future__ import annotations

from cutora.core.commands import CommandRegistry


def build_registry() -> CommandRegistry:
    from cutora.core.cmds.specs_app import build as build_app
    from cutora.core.cmds.specs_asr import build as build_asr
    from cutora.core.cmds.specs_batch import build as build_batch
    from cutora.core.cmds.specs_edit import build as build_edit

    registry = CommandRegistry()
    for spec in build_app() + build_edit() + build_batch() + build_asr():
        registry.register(spec)
    # Agent/MCP gap-fill commands; never shadow a command registered above.
    from cutora.core.cmds.specs_mcp import build as build_mcp
    taken = set(registry.names())
    for spec in build_mcp():
        if spec.name not in taken:
            registry.register(spec)
    return registry
