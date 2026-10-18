# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Helpers for building CommandSpec values."""
from __future__ import annotations

from cutora.core.command_schema import SCHEMA_URI, object_schema
from cutora.core.commands import CommandSpec

RESULT_SCHEMA = {
    "$schema": SCHEMA_URI,
    "type": "object",
    "additionalProperties": True,
}


def doc(text: str) -> str:
    text = (text or "").strip()
    if len(text) < 40:
        raise ValueError("doc_zh 少于 40 字: %r" % text)
    return text


def spec(
    name: str,
    title_zh: str,
    doc_zh: str,
    handler: str,
    *,
    params: dict | None = None,
    required: list | None = None,
    returns: dict | None = None,
    undoable: bool = False,
    dangerous: bool = False,
    requires_project: bool = False,
    headless: bool = False,
    async_job: bool = False,
    extra_properties: bool = True,
    portable: bool = False,
) -> CommandSpec:
    params_schema = object_schema(
        params or {}, required=required, additional=extra_properties)
    return CommandSpec(
        name=name,
        title_zh=title_zh,
        doc_zh=doc(doc_zh),
        params_schema=params_schema,
        returns_schema=returns or RESULT_SCHEMA,
        undoable=undoable,
        dangerous=dangerous,
        requires_project=requires_project,
        run=handler,
        undo=None,
        headless=headless,
        async_job=async_job,
        portable=portable,
    )


def s(typ: str, desc: str | None = None, **extra) -> dict:
    frag: dict = {"type": typ}
    if desc:
        frag["description"] = desc
    frag.update(extra)
    return frag


def arr(item: str = "string", desc: str | None = None) -> dict:
    frag: dict = {"type": "array", "items": {"type": item}}
    if desc:
        frag["description"] = desc
    return frag


def seg_id_param(desc: str | None = None) -> dict:
    return {
        "description": desc or "分段 uuid；整数下标仅迁移旧工程",
        "oneOf": [{"type": "string"}, {"type": "integer"}],
    }


def seg_ids_param(desc: str | None = None) -> dict:
    return {
        "type": "array",
        "description": desc or "分段 uuid 列表；整数下标仅迁移旧工程",
        "items": {"oneOf": [{"type": "string"}, {"type": "integer"}]},
    }
