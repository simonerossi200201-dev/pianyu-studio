# -*- coding: utf-8 -*-
"""Silent-failure #17: inner `from X import Y` must not shadow a module-level Y."""
from __future__ import annotations

import ast
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RENDER_DIR = os.path.join(REPO, "cutora", "render")

# (posix path, function name, imported name) -> reason. Keep empty unless a
# site is truly required and cannot use the module-level binding.
WHITELIST = {
    # none: the 675dd32 ExportRenderer shadow was a bug, not an exception.
}


def _imported_names(node: ast.AST) -> list[tuple[int, str]]:
    out = []
    if isinstance(node, ast.ImportFrom):
        for alias in node.names:
            out.append((node.lineno, alias.asname or alias.name))
    elif isinstance(node, ast.Import):
        for alias in node.names:
            out.append((node.lineno, alias.asname or alias.name.split(".")[0]))
    return out


def _module_level_names(tree: ast.Module) -> set[str]:
    names = set()
    for node in tree.body:
        names.update(n for _ln, n in _imported_names(node))
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
    return names


def _walk_functions(tree: ast.Module):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _direct_body_imports(fn: ast.AST) -> list[tuple[int, str]]:
    """Imports in this function's statements, not nested defs."""
    out = []

    def rec(nodes):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            out.extend(_imported_names(node))
            for attr in ("body", "orelse", "finalbody"):
                child = getattr(node, attr, None)
                if isinstance(child, list):
                    rec(child)
            for h in getattr(node, "handlers", []) or []:
                rec(getattr(h, "body", []) or [])

    rec(getattr(fn, "body", []) or [])
    return out


def test_no_function_level_reimport_of_module_globals():
    hits = []
    files = sorted(
        n for n in os.listdir(RENDER_DIR)
        if n.endswith(".py") and n != "__init__.py"
    )
    assert files, RENDER_DIR
    for name in files:
        path = os.path.join(RENDER_DIR, name)
        rel = "cutora/render/" + name
        tree = ast.parse(open(path, encoding="utf-8").read())
        top = _module_level_names(tree)
        for fn in _walk_functions(tree):
            for ln, imported in _direct_body_imports(fn):
                if imported in top:
                    key = (rel, fn.name, imported)
                    if key in WHITELIST:
                        continue
                    hits.append("%s:%d %s() reimports %s" % (
                        rel, ln, fn.name, imported))
    assert hits == [], "function-level import shadows module global:\n" + "\n".join(hits)
