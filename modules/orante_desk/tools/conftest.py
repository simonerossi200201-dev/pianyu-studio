# -*- coding: utf-8 -*-
"""pytest 收集任何 tools/test_*.py 之前先把 ORANTE_DATA 指到临时目录。

paths.DATA 在首次 import 时定死；如果某个测试文件没设环境变量就 from orante
import paths，后面再设已经晚了，INSERT 会写进真实 data/orante.db。
setdefault：已经设过的（单文件跑、或测试自己的 mkdtemp）不覆盖。
"""
from __future__ import annotations

import inspect
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("ORANTE_DATA", tempfile.mkdtemp(prefix="orante-pytest-"))

# 这些是双击 .bat 跑的自检脚本（匹配 *_test.py 会被误收）。
# 它们自己造临时库或对着真实工程文件，不走 pytest。
collect_ignore = [
    "aipool_test.py",
    "prio_test.py",
    "prio_test_child.py",
    "verify_test.py",
    "blockre_test.py",
]

# pytest 内置 fixture 名。脚本式 test_*(tid, dest) 要 main() 传参，
# 单独收集会报 fixture not found，不当成用例。
_PYTEST_FIXTURES = {
    "tmp_path", "tmp_path_factory", "tmpdir", "tmpdir_factory",
    "monkeypatch", "capsys", "capfd", "caplog", "recwarn",
    "request", "pytestconfig", "cache", "doctest_namespace",
}


@pytest.fixture(autouse=True)
def _orante_data_per_module(request):
    """每个测试文件有自己的 TEST_DATA/TMP；跑这条时把 ORANTE_DATA 拨回去。

    收集期各文件都会 os.environ['ORANTE_DATA']=自己的临时目录，最后一个赢。
    若不拨回去，后跑的文件会写进别人的库，UNIQUE 冲突，子进程也找不到表。
    """
    mod = getattr(request, "module", None)
    td = None
    if mod is not None:
        td = getattr(mod, "TEST_DATA", None) or getattr(mod, "TMP", None)
    old = os.environ.get("ORANTE_DATA")
    if td is not None:
        os.environ["ORANTE_DATA"] = str(td)
        pmod = sys.modules.get("orante.paths")
        dmod = sys.modules.get("orante.db")
        if pmod is not None and hasattr(pmod, "reset"):
            pmod.reset()
        if dmod is not None:
            loc = getattr(dmod, "_local", None)
            if loc is not None:
                loc.conn = None
    yield
    if old is None:
        os.environ.pop("ORANTE_DATA", None)
    else:
        os.environ["ORANTE_DATA"] = old


def pytest_configure(config) -> None:
    os.environ.setdefault("ORANTE_DATA", tempfile.mkdtemp(prefix="orante-pytest-"))
    mod = sys.modules.get("orante.paths")
    if mod is not None and hasattr(mod, "reset"):
        mod.reset()


def pytest_collection_modifyitems(config, items) -> None:
    keep = []
    dropped = []
    for item in items:
        fn = getattr(item, "function", None)
        if fn is None:
            keep.append(item)
            continue
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            keep.append(item)
            continue
        required = [
            p.name for p in sig.parameters.values()
            if p.default is inspect.Parameter.empty
            and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
            and p.name != "self"
        ]
        # fixturenames 里也会出现 tid/dest 这种并不存在的 fixture，不能拿来判断。
        missing = [n for n in required if n not in _PYTEST_FIXTURES]
        if missing:
            dropped.append(item)
            continue
        keep.append(item)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = keep
