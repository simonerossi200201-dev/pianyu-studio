"""PySide6 6.x 的若干 Qt 接口只接受枚举，传整数会在运行期抛 TypeError。

2026-09-21：Q46 新加了数值字重令牌 W_REGULAR/W_MEDIUM/W_SEMI = 400/500/600，
`timeline/header.py` 直接 `font.setWeight(theme_tokens.W_SEMI)`。每次重绘轨道头都抛异常，
被 paintEvent 里的 try/except 吞掉只打一行日志 —— 控制台刷屏，M/S 按钮那段画到一半中断。
golden 和 UI 基线都没抓到：异常被吞了，画面只是「少了一块」。

这条测试是静态的，不需要启动 Qt，所以在任何机器上都能跑。
"""
from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

# 接口名 -> 必须出现在实参里的枚举类型名（任一即可）
ENUM_ONLY = {
    "setWeight": ("Weight",),
}


def _arg_is_enum(node: ast.AST, enum_names) -> bool:
    """实参是 X.Weight.Bold / QFont.Weight(600) / QFont.Weight.Bold 这类就算合规。"""
    src = ast.unparse(node)
    return any(("." + n + ".") in src or ("." + n + "(") in src or src.startswith(n + "(")
               for n in enum_names)


def test_qt_enum_only_calls_get_enums():
    bad = []
    for py in (ROOT / "cutora").rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            enums = ENUM_ONLY.get(node.func.attr)
            if not enums or not node.args:
                continue
            if not _arg_is_enum(node.args[0], enums):
                bad.append("%s:%d  .%s(%s)" % (py.relative_to(ROOT), node.lineno,
                                               node.func.attr, ast.unparse(node.args[0])))
    assert not bad, ("这些调用传的不是枚举，PySide6 6.x 会在运行期抛 TypeError：\n  "
                     + "\n  ".join(bad))
