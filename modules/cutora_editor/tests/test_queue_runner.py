"""运行器自身的门禁。

2026-09-20 的教训：我重写守卫那一块时，替换的区间覆盖了 `autocommit_if_needed`
的定义，把它删掉了，而调用还在。`py_compile` 查不出来（NameError 是运行期的），
于是队列跑完一条任务之后当场崩掉。

这个文件就是那道门禁：任何一个被调用却没定义的名字，在这里就会红。
运行器是整晚无人值守的那根绳子，它自己不能是唯一没有测试的东西。
"""
from __future__ import annotations

import ast
import builtins
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNNER = ROOT / "tools" / "queue_runner.py"


def _module_scope_names(tree: ast.Module) -> set:
    names = set(dir(builtins)) | {"__name__", "__file__", "__doc__"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                names.add(a.asname or a.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _local_names(fn: ast.AST) -> set:
    local = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            local.add(node.id)
        elif isinstance(node, ast.arg):
            local.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            local.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            local.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                local.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.comprehension) and isinstance(node.target, ast.Name):
            local.add(node.target.id)
    return local


def test_queue_runner_has_no_undefined_names():
    """删掉一个函数定义而忘了删调用 —— 这条会红。"""
    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    known = _module_scope_names(tree)
    missing = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        allowed = known | _local_names(fn)
        for node in ast.walk(fn):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id not in allowed:
                    missing.append("%s() 第 %d 行用了未定义的 %r"
                                   % (fn.name, node.lineno, node.id))
    assert not missing, "queue_runner.py 里有用到但没定义的名字：\n  " + "\n  ".join(missing)


def test_queue_runner_imports():
    import importlib.util
    spec = importlib.util.spec_from_file_location("_qr_smoke", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in ("main", "quarantine", "report", "startup_sweep", "run_golden",
                 "parse_failing", "known_red", "autocommit_if_needed",
                 "drain_inbox", "peek", "pop", "head", "git"):
        assert hasattr(mod, name), "queue_runner.py 少了 %s" % name


def test_parse_failing_reads_the_case_level_status():
    """表格第一行印的是**用例级**状态，后续行才是该行自己的状态。

    2026-09-20 我 grep 了一行就下结论，把用例级的 FAIL 当成了那一行失败。
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("_qr_parse", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    table = (
        "CASE                         STATUS   FILE                 DETAIL\n"
        "-" * 88 + "\n"
        "g08_fx_static r1             PASS     expected.mp4         0c300e9b0656...\n"
        "g16_parallel_fx r1           FAIL     expected_00.mp4      e92bb37279c9...\n"
        "                             PASS     expected_01.mp4      1b47c4d43e9c...\n"
        "                             FAIL     expected_02.mp4      got=237c6e00f384 exp=8eb46a915c9e\n"
        "-" * 88 + "\n"
        "RESULT: FAIL\n"
    )
    assert mod.parse_failing(table) == {"g16_parallel_fx"}


def test_known_red_entries_are_real_case_names():
    """名单里写简称会让差集永远匹配不上（2026-09-20 就是这么拒绝开工的）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_qr_kr", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cases = {p.name for p in (ROOT / "tests" / "golden").iterdir() if p.is_dir()}
    for entry in mod.known_red():
        assert entry in cases or any(c.startswith(entry + "_") for c in cases), \
            "已知红名单里的 %r 不对应任何 golden 用例目录" % entry
