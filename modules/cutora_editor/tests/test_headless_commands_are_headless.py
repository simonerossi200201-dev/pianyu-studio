# -*- coding: utf-8 -*-
"""headless=True 必须真能无窗口调用。抛 NeedsGui 即 schema 说谎。"""
from __future__ import annotations


def _minimal_params(schema: dict) -> dict:
    props = (schema or {}).get("properties") or {}
    required = list((schema or {}).get("required") or [])
    out = {}
    for name in required:
        spec = props.get(name) or {}
        typ = spec.get("type")
        if isinstance(typ, list):
            typ = typ[0] if typ else "string"
        if not typ and spec.get("oneOf"):
            typ = (spec["oneOf"][0] or {}).get("type") or "string"
        if typ == "integer":
            out[name] = 0
        elif typ == "number":
            out[name] = 0
        elif typ == "boolean":
            out[name] = False
        elif typ == "array":
            out[name] = []
        elif typ == "object":
            out[name] = {}
        else:
            out[name] = ""
    return out


def test_headless_commands_do_not_raise_needs_gui():
    from cutora.core.commands import (
        CommandBus, CommandError, NeedsGui, SchemaError, get_registry,
    )
    bus = CommandBus(get_registry())
    liars = []
    for spec in get_registry().list():
        if not spec.headless:
            continue
        params = _minimal_params(spec.params_schema)
        try:
            bus.execute(spec.name, params, source="cli")
        except NeedsGui:
            liars.append(spec.name)
        except CommandError as exc:
            if getattr(exc, "code", "") == "needs_gui":
                liars.append(spec.name)
            elif getattr(exc, "code", "") == "needs_project":
                pass
        except SchemaError:
            pass
    assert liars == [], "headless=True 却抛 NeedsGui: %s" % liars
