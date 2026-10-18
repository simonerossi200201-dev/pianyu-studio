# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""cutora_cli ``commands`` / ``cmd`` subcommands. No Qt import at module level."""
from __future__ import annotations

import json
import sys

from cutora.core.command_schema import SchemaError
from cutora.core.commands import CommandError, Context, NeedConfirm, NeedsGui, get_bus


def is_commands_argv(argv: list) -> bool:
    args = list(argv[1:] if argv else [])
    return bool(args) and args[0] in ("commands", "cmd", "recipe")


def _emit(obj: dict) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    out = sys.stdout
    out.write(text)
    if not text.endswith("\n"):
        out.write("\n")
    out.flush()


def _parse_cmd(argv: list) -> tuple[str | None, dict, str | None]:
    args = list(argv[1:] if argv else [])
    if not args or args[0] != "cmd":
        return None, {}, "用法: cutora_cli cmd <command.name> [--json '<params>']"
    if len(args) < 2 or args[1].startswith("-"):
        return None, {}, "cmd 后面需要命令名，例如 cmd app.version"
    name = args[1]
    params: dict = {}
    i = 2
    while i < len(args):
        a = args[i]
        if a in ("--json", "--params"):
            if i + 1 >= len(args):
                return None, {}, "%s 后面需要 JSON 对象" % a
            try:
                obj = json.loads(args[i + 1])
            except Exception as exc:
                return None, {}, "--json 不是合法 JSON: %s" % exc
            if not isinstance(obj, dict):
                return None, {}, "--json 必须是 JSON 对象"
            params = obj
            i += 2
        else:
            return None, {}, "未知参数: %s" % a
    return name, params, None


def _is_light(spec) -> bool:
    run = spec.run if isinstance(spec.run, str) else ""
    if spec.name == "app.gpu_info":
        return False
    if run.startswith("cutora.core.cmds.light:"):
        return True
    if spec.name == "batch.queue.add":
        return True
    return False


def dispatch_early(argv: list) -> int | None:
    """Return exit code if handled; None to continue into the runtime path."""
    args = list(argv[1:] if argv else [])
    if not args:
        return None
    if args[0] == "commands":
        from cutora.core.commands import commands_public_list
        _emit({"commands": commands_public_list()})
        return 0
    if args[0] == "recipe":
        return None
    if args[0] != "cmd":
        return None
    name, params, err = _parse_cmd(argv)
    if err:
        _emit({"ok": False, "error": err})
        return 2
    bus = get_bus()
    try:
        spec = bus.registry.get(name)
    except CommandError as exc:
        _emit({"ok": False, "error": exc.message, "code": exc.code})
        return 2
    if not spec.headless:
        _emit({
            "ok": False,
            "error": "命令 %s 需要图形界面。请先打开片语 Studio。" % name,
            "code": "needs_gui",
        })
        return 1
    if not _is_light(spec):
        return None
    return _execute(bus, name, params)


def dispatch_late(argv: list) -> int:
    args = list(argv[1:] if argv else [])
    if args and args[0] == "recipe":
        return _dispatch_recipe(argv)
    name, params, err = _parse_cmd(argv)
    if err:
        _emit({"ok": False, "error": err})
        return 2
    try:
        from cutora.batch.cli import ensure_qapp
        ensure_qapp()
    except Exception:
        pass
    return _execute(get_bus(), name, params)


def _dispatch_recipe(argv: list) -> int:
    args = list(argv[1:] if argv else [])
    if len(args) < 2 or args[1] != "run":
        _emit({"ok": False, "error": "用法: cutora_cli recipe run <file> --input <folder> --out <dir> [--jobs N]"})
        return 2
    recipe = None
    folder = None
    out_dir = None
    jobs = None
    i = 2
    if i < len(args) and not args[i].startswith("-"):
        recipe = args[i]
        i += 1
    while i < len(args):
        a = args[i]
        if a in ("--input", "--in") and i + 1 < len(args):
            folder = args[i + 1]
            i += 2
        elif a in ("--out", "--out_dir") and i + 1 < len(args):
            out_dir = args[i + 1]
            i += 2
        elif a == "--jobs" and i + 1 < len(args):
            jobs = int(args[i + 1])
            i += 2
        else:
            _emit({"ok": False, "error": "未知参数: %s" % a})
            return 2
    if not recipe or not folder or not out_dir:
        _emit({"ok": False, "error": "recipe run 需要 <file> --input --out"})
        return 2
    from cutora.core.cmds.light import cmd_recipe_apply_folder
    from cutora.core.commands import Context
    try:
        result = cmd_recipe_apply_folder(
            Context(window=None, source="cli"),
            {"recipe": recipe, "folder": folder, "out_dir": out_dir, "jobs": jobs},
        )
        _emit({"ok": True, "result": result})
        return 0
    except Exception as exc:
        _emit({"ok": False, "error": str(exc)})
        return 1


def _execute(bus, name: str, params: dict) -> int:
    try:
        result = bus.execute(name, params, source="cli", ctx=Context(window=None, source="cli"))
        _emit({"ok": True, "result": result})
        return 0
    except NeedsGui as exc:
        _emit({"ok": False, "error": exc.message, "code": exc.code})
        return 1
    except NeedConfirm as exc:
        _emit({"ok": False, "error": exc.message, "code": exc.code})
        return 1
    except SchemaError as exc:
        _emit({"ok": False, "error": str(exc), "code": "schema"})
        return 2
    except CommandError as exc:
        _emit({"ok": False, "error": exc.message, "code": exc.code})
        return 1
    except Exception as exc:
        _emit({"ok": False, "error": str(exc), "code": "error"})
        return 1
