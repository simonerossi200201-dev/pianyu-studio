# -*- coding: utf-8 -*-
"""规格 65：图标和独立进程走本机现生成的 .lnk，分发包不带 exe。"""
from __future__ import annotations

import ast
import importlib.util
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-spec65-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

SHORTCUT_BAT = ROOT / "tools" / "创建快捷方式.bat"
FORWARD_BAT = ROOT / "tools" / "Install-Shortcuts.bat"
ORANTE2 = ROOT / "Orante2.bat"
MAIN_PY = ROOT / "orante" / "main.py"

FORBIDDEN = (
    "ExecutionPolicy",
    "Add-Type",
    "P/Invoke",
    "SHGetPropertyStoreFromParsingName",
    "DllImport",
)


def _load(name: str):
    p = ROOT / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ascii_text(path: Path) -> str:
    data = path.read_bytes()
    bad = [i for i, b in enumerate(data) if b >= 0x80]
    assert not bad, f"{path.name} 不是 PURE ASCII，偏移 {bad[:8]}"
    return data.decode("ascii")


def test_shortcut_bat_no_av_bait() -> None:
    assert SHORTCUT_BAT.is_file()
    text = _ascii_text(SHORTCUT_BAT)
    for s in FORBIDDEN:
        assert s not in text, f"创建快捷方式.bat 含杀软敏感字样 {s!r}"
    # 安静模式：文件里就没有 pause / timeout，双击和 --quiet 都不会卡住
    low = text.lower()
    assert "pause" not in low
    assert "timeout" not in low


def test_shortcut_bat_targets_pythonw() -> None:
    """0924（规格 90）：建快捷方式的活搬进了 orante/shortcut.py，

    bat 只剩「把参数转给 Python」。指向 pythonw、图标、工作目录这些改在 Python 里验。
    """
    text = _ascii_text(SHORTCUT_BAT)
    assert "orante.shortcut" in text
    assert "%*" in text                       # --quiet 这类参数原样转过去
    src = (ROOT / "orante" / "shortcut.py").read_text(encoding="utf-8")
    assert "pythonw.exe" in src
    assert "-m orante" in src
    assert "orante-me.ico" in src             # 桌面图标用 "Orante me.png" 转的那张
    assert "orante.ico" in src                # 没转过就退回原来的程序图标
    assert "workdir" in src


def test_missing_venv_exits_1(tmp_path: Path) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    dest = tools / SHORTCUT_BAT.name
    dest.write_bytes(SHORTCUT_BAT.read_bytes())
    r = subprocess.run(
        ["cmd.exe", "/c", str(dest)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert r.returncode == 1
    out = (r.stdout or "") + (r.stderr or "")
    assert "Orante2.bat" in out
    assert "Not installed yet" in out
    # 假根目录上不应长出快捷方式
    assert not (tmp_path / "Orante.lnk").exists()


def test_forwarder_reaches_shortcut(tmp_path: Path) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / SHORTCUT_BAT.name).write_bytes(SHORTCUT_BAT.read_bytes())
    (tools / FORWARD_BAT.name).write_bytes(FORWARD_BAT.read_bytes())
    r = subprocess.run(
        ["cmd.exe", "/c", str(tools / FORWARD_BAT.name)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert r.returncode == 1
    out = (r.stdout or "") + (r.stderr or "")
    assert "Orante2.bat" in out
    assert "SPEC65_SHORTCUT" in _ascii_text(FORWARD_BAT)


def test_whitelist_keeps_shortcut_drops_exe(tmp_path: Path) -> None:
    make = _load("make_share")
    src = tmp_path / "src"
    (src / "orante").mkdir(parents=True)
    (src / "orante" / "__init__.py").write_text(
        '__version__ = "2.0.0"\n', encoding="utf-8",
    )
    (src / "ui" / "assets").mkdir(parents=True)
    (src / "ui" / "assets" / "orante.ico").write_bytes(b"ico-fake")
    (src / "Orante2.bat").write_text("@echo off\n", encoding="ascii")
    (src / "Orante.exe").write_bytes(b"MZ-fake-exe")
    (src / "tools").mkdir()
    (src / "tools" / "创建快捷方式.bat").write_text("@echo off\n", encoding="ascii")
    (src / "tools" / "Install-Shortcuts.bat").write_text("@echo off\n", encoding="ascii")
    (src / "tools" / "launcher.cs").write_text("// cs\n", encoding="utf-8")
    (src / "tools" / "build_launcher.py").write_text("# py\n", encoding="utf-8")
    (src / "tools" / "13-Orante-Debug.bat").write_text("@echo off\n", encoding="ascii")
    dest = tmp_path / "out" / "Orante-分发版-2.0.0-20990101"
    result = make.build(src, dest, do_scan=False, do_smoke=False, do_zip=True)

    assert (dest / "tools" / "创建快捷方式.bat").is_file()
    assert (dest / "tools" / "Install-Shortcuts.bat").is_file()
    assert (dest / "ui" / "assets" / "orante.ico").is_file()
    assert not (dest / "Orante.exe").exists()
    assert not (dest / "tools" / "launcher.cs").exists()
    assert not (dest / "tools" / "build_launcher.py").exists()

    assert make.compiled_entries_in_dir(dest) == []
    zpath = Path(result["zip"])
    assert zpath.is_file()
    assert make.compiled_entries_in_zip(zpath) == []
    with zipfile.ZipFile(zpath) as zf:
        names = zf.namelist()
    assert "tools/创建快捷方式.bat" in names
    assert "ui/assets/orante.ico" in names
    assert "Orante.exe" not in names
    assert "tools/launcher.cs" not in names
    assert "tools/build_launcher.py" not in names


def test_real_collect_has_ico_not_exe() -> None:
    make = _load("make_share")
    rels = {rel.replace("\\", "/") for _, rel in make.collect_copies(ROOT)}
    assert "tools/创建快捷方式.bat" in rels
    assert "tools/Install-Shortcuts.bat" in rels
    assert "ui/assets/orante.ico" in rels
    assets = sorted(r for r in rels if r.startswith("ui/assets/"))
    assert assets, "ui/assets 应随 ui/** 进包"
    assert "Orante.exe" not in rels
    assert "tools/launcher.cs" not in rels
    assert "tools/build_launcher.py" not in rels
    assert "Orante.exe" not in make.ROOT_KEEP
    assert "创建快捷方式.bat" in make.TOOLS_KEEP
    assert "Install-Shortcuts.bat" in make.TOOLS_KEEP
    assert "launcher.cs" not in make.TOOLS_KEEP
    assert "build_launcher.py" not in make.TOOLS_KEEP


def test_aumid_called_before_create_window() -> None:
    src = MAIN_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    run_fn = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_run"
    )

    def calls_of(fn: ast.AST, names: set[str]) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if isinstance(f, ast.Name) and f.id in names:
                out.append((f.id, node.lineno))
            elif isinstance(f, ast.Attribute) and f.attr in names:
                out.append((f.attr, node.lineno))
        return out

    in_run = calls_of(
        run_fn,
        {"_set_app_user_model_id", "open_ui", "show_boot_error_window",
         "create_window"},
    )
    aumid_lines = [ln for name, ln in in_run if name == "_set_app_user_model_id"]
    later = [ln for name, ln in in_run if name != "_set_app_user_model_id"]
    assert aumid_lines, "_run 里必须调用 _set_app_user_model_id"
    assert later, "_run 里必须走到 open_ui / 错误窗口"
    assert min(aumid_lines) < min(later)

    # 定义处：两个 create_window 都在 _set_app_user_model_id 这个函数之外、
    # 由 _run 在登记 ID 之后才调到。
    cw = calls_of(tree, {"create_window"})
    assert len(cw) >= 2
    aumid_def = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_set_app_user_model_id"
    )
    assert aumid_def.lineno > 0


def test_orante2_calls_shortcut_quiet_and_is_ascii() -> None:
    """Orante2.bat 必须 PURE ASCII，所以不能写中文文件名。

    它 call tools\\Install-Shortcuts.bat --quiet，转发到 创建快捷方式.bat。
    退路：删掉 Orante2.bat 第 66 行那次 call，就回到两步流程。
    """
    text = _ascii_text(ORANTE2)
    assert "Install-Shortcuts.bat" in text
    assert "--quiet" in text
    assert "call tools\\Install-Shortcuts.bat --quiet" in text
    assert SHORTCUT_BAT.is_file()
    # 转发 bat 也是 ASCII，靠标记找到中文名那份
    fwd = _ascii_text(FORWARD_BAT)
    assert "SPEC65_SHORTCUT" in fwd
    assert "--quiet" in text
    assert all(b < 0x80 for b in ORANTE2.read_bytes())


def test_quiet_mode_has_no_pause_timeout() -> None:
    text = _ascii_text(SHORTCUT_BAT)
    assert "%*" in text                       # 首次安装时 Orante2.bat 传 --quiet 进来
    assert "pause" not in text.lower()
    assert "timeout" not in text.lower()
    src = (ROOT / "orante" / "shortcut.py").read_text(encoding="utf-8")
    assert "--quiet" in src


def test_compiled_gate_rejects_exe(tmp_path: Path) -> None:
    make = _load("make_share")
    dest = tmp_path / "Orante-分发版-gate"
    dest.mkdir(parents=True)
    (dest / "evil.exe").write_bytes(b"MZ")
    items = make.compiled_entries_in_dir(dest)
    assert items == ["evil.exe"]
    with pytest.raises(SystemExit):
        make._assert_no_compiled(items, "out")
