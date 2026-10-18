# -*- coding: utf-8 -*-
"""Registered command run() bodies must not open Qt dialogs.

Also follow one hop: command_xxx on MainWindow/CommandBodies, then one
layer of self.<method> (catches run_export_worker).

Window methods are parsed from source. Importing MainWindow here would
load PySide6 into the pytest process and break later in-process Qt tests.
"""
from __future__ import annotations

import ast
import inspect
import importlib
import os
import textwrap

FORBIDDEN_NAMES = {"QFileDialog", "QMessageBox", "QInputDialog"}
FORBIDDEN_ATTRS = {"QFileDialog", "QMessageBox", "QInputDialog", "exec"}

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WINDOW_SOURCES = (
    os.path.join(REPO, "cutora", "ui", "command_bodies.py"),
    os.path.join(REPO, "cutora", "ui", "main_window.py"),
)
WINDOW_CLASSES = {"MainWindow", "CommandBodies"}


def _resolve(target):
    if callable(target):
        return target
    mod_name, _, fn_name = str(target).partition(":")
    mod = importlib.import_module(mod_name)
    return getattr(mod, fn_name)


def _scan_tree(tree, label, hits):
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            hits.append("%s: %s" % (label, node.id))
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRS:
            hits.append("%s: .%s" % (label, node.attr))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "exec":
                hits.append("%s: exec()" % label)


def _scan_fn(fn, label, hits):
    try:
        src = textwrap.dedent(inspect.getsource(fn))
    except (OSError, TypeError):
        return
    _scan_tree(ast.parse(src), label, hits)


def _string_args_tree(tree) -> list[str]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.startswith("command_"):
                out.append(node.value)
    return out


def _self_methods_tree(tree) -> list[str]:
    names = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr:
            if isinstance(func.value, ast.Name) and func.value.id == "self":
                names.append(func.attr)
    return names


def _window_methods():
    methods = {}
    for path in WINDOW_SOURCES:
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if node.name not in WINDOW_CLASSES:
                continue
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods[item.name] = item
    return methods


def test_command_run_functions_have_no_dialogs():
    from cutora.core.commands import get_registry
    hits = []
    seen = set()
    methods = _window_methods()
    for spec in get_registry().list():
        fn = _resolve(spec.run)
        try:
            src = textwrap.dedent(inspect.getsource(fn))
            tree = ast.parse(src)
        except (OSError, TypeError):
            continue
        _scan_tree(tree, spec.name, hits)
        bodies = []
        for target in _string_args_tree(tree):
            node = methods.get(target)
            if node is not None:
                bodies.append((target, node))
        for target, node in bodies:
            key = spec.name + "->" + target
            if key in seen:
                continue
            seen.add(key)
            _scan_tree(node, key, hits)
            for meth in _self_methods_tree(node):
                if meth in ("_offer_autosave_restore",
                            "_prompt_canvas_if_sources_mismatch"):
                    continue
                nested = methods.get(meth)
                if nested is None:
                    continue
                nkey = key + "->" + meth
                if nkey in seen:
                    continue
                seen.add(nkey)
                _scan_tree(nested, nkey, hits)
    assert hits == [], "command bodies contain dialogs:\n" + "\n".join(hits)


def test_missing_media_warning_lives_on_the_window():
    """project.save stays quiet. The missing-file warning is MainWindow's.

    The old fallback QMessageBox lived inside _save_project_quiet, so API
    and MCP saves opened a dialog. The words the user sees have to stay
    on _warn_missing_media, which _ui_warning already suppresses unless
    the bus source is ui.
    """
    cmd = open(WINDOW_SOURCES[0], encoding="utf-8").read()
    win = open(WINDOW_SOURCES[1], encoding="utf-8").read()
    quiet = cmd.split("def _save_project_quiet", 1)[1].split("\n    def ", 1)[0]
    assert "QMessageBox" not in quiet
    assert "_warn_missing_media" in quiet
    method = win.split("def _warn_missing_media", 1)[1].split("\n    def ", 1)[0]
    assert "这些时间线上的文件找不到" in method
    assert "_ui_warning" in method
