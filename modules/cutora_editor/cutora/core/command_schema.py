# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""JSON Schema helpers for the command bus.

Translate the CLI ``--describe`` mini-schema (``path`` / ``path|list`` / …)
into JSON Schema draft 2020-12. Do not hand-copy op params.
"""
from __future__ import annotations

from typing import Any

SCHEMA_URI = "https://json-schema.org/draft/2020-12/schema"


class SchemaError(ValueError):
    """Params failed JSON Schema validation, or a spec schema is illegal."""


def describe_type_to_jsonschema(type_name: str) -> dict[str, Any]:
    """Map one CLI ``params.*.type`` string to a JSON Schema fragment."""
    raw = (type_name or "string").strip()
    if "|" in raw:
        alts = [p.strip() for p in raw.split("|") if p.strip()]
        if alts == ["int", "null"] or alts == ["integer", "null"]:
            return {"type": ["integer", "null"]}
        if alts == ["bool", "null"] or alts == ["boolean", "null"]:
            return {"type": ["boolean", "null"]}
        if alts == ["number", "null"]:
            return {"type": ["number", "null"]}
        mapped = [_map_atom(p) for p in alts]
        # path|list and string|list become string or array-of-string.
        if any(p in ("list", "path|list") for p in alts) or "list" in alts:
            return {
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ]
            }
        if len(mapped) == 1:
            return mapped[0]
        types = []
        for frag in mapped:
            t = frag.get("type")
            if isinstance(t, list):
                types.extend(t)
            elif t:
                types.append(t)
        uniq = []
        for t in types:
            if t not in uniq:
                uniq.append(t)
        if uniq:
            return {"type": uniq if len(uniq) > 1 else uniq[0]}
        return {"oneOf": mapped}
    return _map_atom(raw)


def _map_atom(atom: str) -> dict[str, Any]:
    if atom in ("path", "text", "text|path"):
        return {"type": "string"}
    if atom == "string":
        return {"type": "string"}
    if atom in ("int", "integer"):
        return {"type": "integer"}
    if atom in ("bool", "boolean"):
        return {"type": "boolean"}
    if atom == "number":
        return {"type": "number"}
    if atom in ("list", "array"):
        return {"type": "array"}
    if atom in ("object", "dict"):
        return {"type": "object"}
    if atom == "null":
        return {"type": "null"}
    return {"type": "string"}


def op_params_to_schema(params_spec: dict | None) -> dict[str, Any]:
    """Build a draft 2020-12 object schema from one OPS_SPEC ``params`` dict."""
    props: dict[str, Any] = {}
    required: list[str] = []
    for name, meta in (params_spec or {}).items():
        if not isinstance(meta, dict):
            props[str(name)] = {"type": "string"}
            continue
        frag = describe_type_to_jsonschema(str(meta.get("type") or "string"))
        desc = meta.get("desc")
        if desc:
            frag = dict(frag)
            frag["description"] = str(desc)
        if "default" in meta:
            frag = dict(frag)
            frag["default"] = meta.get("default")
        props[str(name)] = frag
        if meta.get("required"):
            required.append(str(name))
    schema: dict[str, Any] = {
        "$schema": SCHEMA_URI,
        "type": "object",
        "properties": props,
        "additionalProperties": True,
    }
    if required:
        schema["required"] = required
    return schema


def ops_to_jsonschema(ops_spec: list) -> dict[str, Any]:
    """Translate every CLI op into ``{op_name: params_schema}``."""
    out: dict[str, Any] = {}
    for op in ops_spec or []:
        if not isinstance(op, dict) or not op.get("name"):
            continue
        out[str(op["name"])] = op_params_to_schema(op.get("params") or {})
    return out


def object_schema(
    properties: dict | None = None,
    required: list | None = None,
    additional: bool = True,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "$schema": SCHEMA_URI,
        "type": "object",
        "properties": dict(properties or {}),
        "additionalProperties": additional,
    }
    if required:
        schema["required"] = list(required)
    return schema


def check_schema(schema: dict) -> None:
    """Raise SchemaError if ``schema`` itself is not a valid draft 2020-12 schema."""
    try:
        from jsonschema.validators import Draft202012Validator
    except Exception as exc:
        raise SchemaError("jsonschema 不可用，无法校验命令 schema") from exc
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        raise SchemaError("命令 schema 不合法: %s" % exc) from exc


def validate_params(schema: dict | None, params: dict | None) -> dict:
    """Validate ``params`` against ``schema``. Returns the params dict."""
    payload = params if isinstance(params, dict) else {}
    if not schema:
        return payload
    try:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import ValidationError
    except Exception as exc:
        raise SchemaError("jsonschema 不可用，无法校验命令参数") from exc
    try:
        Draft202012Validator(schema).validate(payload)
    except ValidationError as exc:
        path = ".".join(str(p) for p in exc.absolute_path) or "(root)"
        raise SchemaError("参数校验失败（%s）: %s" % (path, exc.message)) from exc
    except Exception as exc:
        raise SchemaError("参数校验失败: %s" % exc) from exc
    return payload
