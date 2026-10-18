# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Project-queue auto-credit: ``text_preset.apply`` idempotent per task.

GUI queue export and tests share this loop. Default remains off: callers
must pass ``options.auto_credit`` and ``options.credit_preset_id``.
"""
from __future__ import annotations


def apply_auto_credit(bus, tasks, preset_id, *, source="ui"):
    """Same loop the project queue uses: one idempotent apply per media_id."""
    pid = str(preset_id or "").strip()
    if not pid:
        return 0
    n = 0
    for i, task in enumerate(tasks or []):
        if not isinstance(task, dict):
            continue
        bus.execute(
            "text_preset.apply",
            {"id": pid, "idempotent": True, "media_id": i},
            source=source,
        )
        n += 1
    return n


def apply_auto_credit_from_queue_options(bus, tasks, options, *, source="ui"):
    """No-op unless the queue option is on and a preset id is set (default off)."""
    opts = options or {}
    if not (opts.get("auto_credit") and opts.get("credit_preset_id")):
        return 0
    return apply_auto_credit(
        bus, tasks, opts.get("credit_preset_id"), source=source)
