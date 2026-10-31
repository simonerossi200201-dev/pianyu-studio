# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Command registry and bus. No Qt / torch imports (early CLI path)."""
from __future__ import annotations

import importlib
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from cutora.core.command_audit import append_audit, sanitize_params
from cutora.core.command_schema import SchemaError, check_schema, validate_params
from cutora.core.log import get_logger

logger = get_logger("cutora.core.commands")

VALID_SOURCES = ("ui", "cli", "api", "mcp", "recipe")


class CommandError(RuntimeError):
    """Raised when a command cannot run. ``code`` is a stable machine token."""

    def __init__(self, message: str, code: str = "error"):
        super().__init__(message)
        self.message = str(message)
        self.code = str(code)


class NeedsGui(CommandError):
    """headless=False command ran without a main window."""

    def __init__(self, message: str | None = None):
        super().__init__(
            message or "这条命令需要图形界面。请先打开片语 Studio。",
            code="needs_gui",
        )


class NeedConfirm(CommandError):
    """dangerous command invoked from API/MCP without confirm=true."""

    def __init__(self, name: str):
        super().__init__(
            "危险命令 %s 需要 params.confirm=true 才会执行。" % name,
            code="need_confirm",
        )


@dataclass
class CommandSpec:
    name: str
    title_zh: str
    doc_zh: str
    params_schema: dict
    returns_schema: dict
    undoable: bool
    dangerous: bool
    requires_project: bool
    run: str | Callable
    undo: str | Callable | None = None
    headless: bool = False
    async_job: bool = False
    portable: bool = False


@dataclass
class Context:
    """Per-invocation context. ``window`` is None in headless CLI."""

    window: Any = None
    source: str = "ui"
    command: str = ""
    extra: dict = field(default_factory=dict)


class CommandRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, CommandSpec] = {}

    def register(self, spec: CommandSpec) -> CommandSpec:
        if not spec.name or "." not in spec.name:
            raise CommandError("命令名必须是点分命名空间，收到 %r" % spec.name)
        if len(spec.doc_zh or "") < 40:
            raise CommandError("命令 %s 的 doc_zh 少于 40 字" % spec.name)
        check_schema(spec.params_schema)
        check_schema(spec.returns_schema)
        self._specs[spec.name] = spec
        return spec

    def get(self, name: str) -> CommandSpec:
        spec = self._specs.get(name)
        if spec is None:
            raise CommandError("未知命令 %r" % name, code="unknown")
        return spec

    def list(self) -> list[CommandSpec]:
        return [self._specs[k] for k in sorted(self._specs)]

    def names(self) -> list[str]:
        return sorted(self._specs)

    def public_item(self, spec: CommandSpec) -> dict[str, Any]:
        return {
            "name": spec.name,
            "title_zh": spec.title_zh,
            "doc_zh": spec.doc_zh,
            "params_schema": spec.params_schema,
            "returns_schema": spec.returns_schema,
            "undoable": spec.undoable,
            "dangerous": spec.dangerous,
            "requires_project": spec.requires_project,
            "headless": spec.headless,
            "async_job": spec.async_job,
            "portable": spec.portable,
        }

    def public_list(self) -> list[dict[str, Any]]:
        return [self.public_item(s) for s in self.list()]

    def schema_json(self) -> dict[str, Any]:
        return {s.name: s.params_schema for s in self.list()}


def _resolve(target: str | Callable | None) -> Callable | None:
    if target is None:
        return None
    if callable(target):
        return target
    mod_name, _, fn_name = str(target).partition(":")
    if not mod_name or not fn_name:
        raise CommandError("命令实现路径不合法: %r" % target)
    mod = importlib.import_module(mod_name)
    fn = getattr(mod, fn_name, None)
    if not callable(fn):
        raise CommandError("命令实现 %s 不是可调用对象" % target)
    return fn


class CommandBus:
    """Synchronous bus. Qt invokeMethod wrapping is T13b."""

    def __init__(self, registry: CommandRegistry | None = None) -> None:
        self.registry = registry if registry is not None else get_registry()
        self._window = None
        self._executed_hooks: list[Callable] = []

    def bind_window(self, window: Any) -> None:
        self._window = window

    def on_executed(self, hook: Callable) -> None:
        self._executed_hooks.append(hook)

    def off_executed(self, hook: Callable) -> None:
        self._executed_hooks = [h for h in self._executed_hooks if h is not hook]

    def execute(
        self,
        name: str,
        params: dict | None = None,
        source: str = "ui",
        ctx: Context | None = None,
    ) -> dict:
        spec = self.registry.get(name)
        src = source if source in VALID_SOURCES else "cli"
        raw = dict(params or {})
        confirm = bool(raw.pop("confirm", False))
        payload = validate_params(spec.params_schema, raw)
        window = ctx.window if ctx is not None else self._window
        if spec.dangerous and src in ("api", "mcp") and not confirm:
            raise NeedConfirm(name)
        if not spec.headless and window is None:
            raise NeedsGui()
        if spec.requires_project and not _has_project(window):
            raise CommandError(
                "当前还没有导入视频或打开工程，请先导入视频或新建/打开工程后再使用「%s」功能。" % (spec.title_zh or name),
                code="needs_project",
            )
        env = ctx or Context()
        env.window = window
        env.source = src
        env.command = name
        fn = _resolve(spec.run)
        t0 = time.perf_counter()
        ok = False
        error = None
        result: dict = {}
        pushed = False
        _bb_op_id = None
        _is_heavy_cmd = name.startswith(("export.", "subtitle.", "asr.", "batch.", "silence_cut.", "project.open", "project.pack", "media.import"))
        try:
            from cutora.core.blackbox import get_recorder
            _bb_op_id = get_recorder().begin_op(
                "cmd:%s" % name,
                sanitize_params(payload),
                sync=_is_heavy_cmd,
                include_gpu=_is_heavy_cmd,
            )
        except Exception:
            _bb_op_id = None
        prev_source = getattr(window, "_bus_source", None) if window is not None else None
        if window is not None:
            window._bus_source = src
        if spec.undoable and window is not None:
            # A section reset already took one snapshot and sets _undo_hold so
            # the rows share that single undo instead of one entry per command.
            pushed = _push_undo(window, spec, name, payload, src)
        try:
            out = fn(env, payload) if fn is not None else {}
            if out is None:
                out = {}
            if not isinstance(out, dict):
                out = {"result": out}
            result = out
            ok = True
            if pushed:
                _capture_after(window)
            return result
        except (CommandError, SchemaError) as exc:
            error = str(exc)
            if pushed:
                _pop_undo(window)
            raise
        except Exception as exc:
            if pushed:
                _pop_undo(window)
            error = str(exc)
            logger.exception("命令 %s 执行失败", name)
            raise CommandError(error) from exc
        finally:
            if window is not None:
                window._bus_source = prev_source
            ms = (time.perf_counter() - t0) * 1000.0
            if _bb_op_id is not None:
                try:
                    from cutora.core.blackbox import get_recorder
                    get_recorder().end_op(_bb_op_id, ok=ok, error=error, sync=_is_heavy_cmd or (not ok))
                except Exception:
                    pass
            keys = list(result.keys()) if ok else []
            append_audit(
                source=src,
                command=name,
                params=payload,
                ms=ms,
                ok=ok,
                error=error,
                result_keys=keys,
            )
            if ok:
                event = {
                    "name": name,
                    "params": sanitize_params(payload),
                    "result": result,
                    "source": src,
                }
                for hook in list(self._executed_hooks):
                    try:
                        hook(event)
                    except Exception:
                        logger.exception("bus.executed 回调失败")


def _has_project(window: Any) -> bool:
    if window is None:
        return False
    path = getattr(window, "project_path", None)
    tasks = getattr(window, "tasks", None)
    if path:
        return True
    return bool(tasks)


def _push_undo(window: Any, spec: CommandSpec, name: str, params: dict, source: str) -> bool:
    if getattr(window, "_undo_hold", 0):
        return False
    push = getattr(window, "push_undo", None)
    if not callable(push):
        return False
    try:
        push(
            spec.title_zh,
            command=name,
            params=sanitize_params(params),
            source=source,
        )
    except TypeError:
        push(spec.title_zh)
    except Exception:
        logger.exception("命令 %s 写入撤销快照失败", name)
        return False
    return True


def _pop_undo(window: Any) -> None:
    mgr = getattr(window, "history_mgr", None)
    pop = getattr(mgr, "pop_last", None) if mgr is not None else None
    if not callable(pop):
        return
    try:
        pop()
    except Exception:
        logger.exception("命令失败后弹出撤销快照失败")


def _capture_after(window: Any) -> None:
    if getattr(window, "_undo_hold", 0):
        return
    cap = getattr(window, "capture_undo_after", None)
    if not callable(cap):
        return
    try:
        cap()
    except Exception:
        logger.exception("命令成功后写入 redo 快照失败")


_REGISTRY: CommandRegistry | None = None
_BUS: CommandBus | None = None


def get_registry() -> CommandRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        from cutora.core.cmds import build_registry
        _REGISTRY = build_registry()
    return _REGISTRY


def get_bus() -> CommandBus:
    global _BUS
    if _BUS is None:
        _BUS = CommandBus(get_registry())
    return _BUS


def reset_bus_for_tests() -> None:
    """Test helper: drop the process singletons."""
    global _REGISTRY, _BUS
    _REGISTRY = None
    _BUS = None


def commands_public_list() -> list[dict[str, Any]]:
    return get_registry().public_list()
