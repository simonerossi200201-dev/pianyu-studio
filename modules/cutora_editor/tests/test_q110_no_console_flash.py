# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""不许再有裸 subprocess：每一个外部进程都要走 core.proc 的不开控制台版本。

主程序是 pythonw（自己没有控制台）。Windows 会给每一个控制台子进程现开一个
窗口 —— 用户看到的就是「导入一条视频闪两下黑框」。仓库里本来就有
cutora/core/proc.py 专门带上 CREATE_NO_WINDOW，但有 53 处调用点绕过了它。
这条测试是那次扫尾的护栏：以后谁再写裸 subprocess.run / Popen，这里当场红。
"""
import ast
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(REPO, "cutora")
# 它自己就是那个封装，里面必须直接调 subprocess
ALLOWED = {os.path.join("cutora", "core", "proc.py")}
BANNED = {"run", "Popen", "call", "check_output", "check_call"}


def _py_files():
    for base, _dirs, names in os.walk(PKG):
        for name in names:
            if name.endswith(".py"):
                yield os.path.join(base, name)


def _bare_calls(path):
    """返回 [(行号, 'subprocess.xxx')]，只看真正的调用，不看常量和类型标注。"""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not isinstance(fn, ast.Attribute):
            continue
        owner = fn.value
        if isinstance(owner, ast.Name) and owner.id == "subprocess":
            if fn.attr in BANNED:
                hits.append((node.lineno, "subprocess.%s" % fn.attr))
    return hits


def test_no_bare_subprocess_in_product_code():
    offenders = []
    for path in _py_files():
        rel = os.path.relpath(path, REPO).replace("\\", "/")
        if rel in {p.replace("\\", "/") for p in ALLOWED}:
            continue
        for lineno, what in _bare_calls(path):
            offenders.append("%s:%d %s" % (rel, lineno, what))
    assert not offenders, (
        "这些地方直接调了 subprocess，在 pythonw 下会弹黑框。\n"
        "改成 from cutora.core.proc import run/popen：\n  " + "\n  ".join(offenders))


def test_proc_helper_sets_no_window_on_windows():
    """封装本身必须真的带上 CREATE_NO_WINDOW，否则上面那条测试是空的。"""
    from cutora.core.proc import creationflags
    import subprocess as sp
    if os.name == "nt":
        assert creationflags() & sp.CREATE_NO_WINDOW
    else:
        assert creationflags() == 0
        assert creationflags(8) == 8


def test_allowed_list_is_minimal():
    """白名单只该有 core/proc.py 一个。多一个都要有人解释。"""
    assert ALLOWED == {os.path.join("cutora", "core", "proc.py")}
