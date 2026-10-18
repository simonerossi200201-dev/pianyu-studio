# -*- coding: utf-8 -*-
"""Generate docs/api/commands.md, openapi.json, recipes.md from the registry."""
from __future__ import annotations

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

OUT = os.path.join(REPO, "docs", "api")


def _schema_fields(schema: dict) -> list[tuple[str, str, str, dict]]:
    props = (schema or {}).get("properties") or {}
    required = set((schema or {}).get("required") or [])
    rows = []
    for name, spec in props.items():
        typ = spec.get("type")
        if isinstance(typ, list):
            typ = "|".join(str(t) for t in typ)
        if not typ and spec.get("oneOf"):
            parts = []
            for alt in spec["oneOf"]:
                t = alt.get("type") or "object"
                if t == "array":
                    item = (alt.get("items") or {}).get("type") or ""
                    t = "array[%s]" % item if item else "array"
                parts.append(str(t))
            typ = "|".join(parts)
        desc = spec.get("description") or ""
        flag = "必填" if name in required else "可选"
        rows.append((name, str(typ or "object"), "%s。%s" % (flag, desc), spec))
    return rows


def write_commands_md(specs: list) -> str:
    lines = [
        "# 片语 Studio 命令参考",
        "",
        "由 `tools/gen_api_docs.py` 从命令注册表生成。不要手改。",
        "",
        "本地 API：`POST /v1/commands/{name}`，Header `Authorization: Bearer <token>`。",
        "CLI：`python -m cutora cmd <name> --json '{...}'`。",
        "",
    ]
    groups: dict[str, list] = {}
    for spec in specs:
        ns = spec.name.split(".", 1)[0]
        groups.setdefault(ns, []).append(spec)
    for ns in sorted(groups):
        lines.append("## %s" % ns)
        lines.append("")
        for spec in groups[ns]:
            lines.append("### `%s` — %s" % (spec.name, spec.title_zh))
            lines.append("")
            lines.append(spec.doc_zh)
            lines.append("")
            lines.append("- 可撤销：%s" % ("是" if spec.undoable else "否"))
            lines.append("- 危险：%s" % ("是（API 需 confirm=true）" if spec.dangerous else "否"))
            lines.append("- 无头：%s" % ("是" if spec.headless else "否（需要图形界面）"))
            lines.append("- 异步作业：%s" % ("是" if spec.async_job else "否"))
            lines.append("")
            fields = _schema_fields(spec.params_schema)
            if fields:
                lines.append("| 参数 | 类型 | 说明 |")
                lines.append("|---|---|---|")
                for name, typ, desc, _prop in fields:
                    lines.append("| `%s` | %s | %s |" % (name, typ, desc.replace("|", "\\|")))
                lines.append("")
            example = {name: _example_val(prop) for name, _typ, _d, prop in fields}
            lines.append("CLI：")
            lines.append("")
            lines.append("```")
            lines.append(
                "python -m cutora cmd %s --json '%s'"
                % (spec.name, json.dumps(example, ensure_ascii=False)))
            lines.append("```")
            lines.append("")
            lines.append("curl：")
            lines.append("")
            lines.append("```")
            lines.append(
                "curl -sS -H \"Authorization: Bearer $TOKEN\" "
                "-H \"Content-Type: application/json\" "
                "-d '%s' http://127.0.0.1:47321/v1/commands/%s"
                % (json.dumps(example, ensure_ascii=False), spec.name))
            lines.append("```")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _example_val(spec):
    """Legal JSON example for one parameter. Enums use the first allowed value."""
    if isinstance(spec, dict):
        enum = spec.get("enum")
        if isinstance(enum, list) and enum:
            return enum[0]
        typ = spec.get("type")
        if isinstance(typ, list):
            typ = typ[0] if typ else "string"
    else:
        typ = spec
    if typ in ("integer", "int"):
        return 0
    if typ == "number":
        return 0.0
    if typ == "boolean":
        return True
    if typ == "array":
        return []
    return ""


def write_recipes_md() -> str:
    from cutora.batch.recipe import builtin_recipes, load_recipe
    lines = [
        "# 配方格式",
        "",
        "文件扩展名 `.cutorarecipe`（JSON）。schema 见 `resources/data/recipe.schema.json`。",
        "",
        "- `schema_version`：必须为 `1`。",
        "- `on_error`：`stop` 或 `skip`。",
        "- `skip_if`：可选。`{command, field, equals, params}`，先跑 command，字段等于 equals 则跳过该步。",
        "- 录制只记 `source=ui` 且 `undoable=True`；`app.*` 与 `media.select` 不录。",
        "- `{media}` / `{out_dir}` 在套用到文件夹时替换。",
        "- CLI：`python -m cutora recipe run <file> --input <folder> --out <dir> [--jobs N]`。",
        "",
        "## 内置配方",
        "",
    ]
    for path in builtin_recipes():
        data = load_recipe(path)
        lines.append("### %s" % data["name"])
        lines.append("")
        lines.append(data.get("description") or "")
        lines.append("")
        lines.append("步骤：")
        for step in data.get("steps") or []:
            lines.append("- `%s`" % step.get("command"))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    from cutora.api.openapi import build_openapi
    from cutora.core.commands import get_registry

    os.makedirs(OUT, exist_ok=True)
    specs = get_registry().list()
    commands_path = os.path.join(OUT, "commands.md")
    openapi_path = os.path.join(OUT, "openapi.json")
    recipes_path = os.path.join(OUT, "recipes.md")
    with open(commands_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(write_commands_md(specs))
    spec = build_openapi()
    with open(openapi_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(spec, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    with open(recipes_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(write_recipes_md())
    print("wrote", commands_path)
    print("wrote", openapi_path)
    print("wrote", recipes_path)
    print("commands", len(specs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
