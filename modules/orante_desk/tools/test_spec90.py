# -*- coding: utf-8 -*-
"""规格 90：不给杀毒软件送靶子。

项目里不许再有编译产物（Orante.exe 和生成它的 launcher.cs / build_launcher.py 已删）；
快捷方式改由 Python 建，脚本里不许再出现 powershell 那一长串内联命令；
桌面图标用 "Orante me.png" 转出来的 .ico —— 图片不招杀软。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

SKIP_DIRS = {".venv", "_share", "data", "workspace", "__pycache__", ".git",
             ".pytest_cache", "_claude_tmp", "_85b_dump", "_85b_snip",
             "_manual_shots_tmp", "node_modules"}
COMPILED = {".exe", ".dll", ".pyd", ".so", ".cs"}


def _walk_repo():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".tmp")]
        for fn in filenames:
            yield Path(dirpath) / fn


def test_no_compiled_artifacts_in_repo():
    bad = [p.relative_to(ROOT).as_posix() for p in _walk_repo()
           if p.suffix.lower() in COMPILED and ".bak-" not in p.name]
    assert bad == [], f"项目里不许有编译产物 / C# 源：{bad}"


def test_launcher_scripts_are_gone():
    assert not (ROOT / "Orante.exe").exists()
    assert not (ROOT / "tools" / "launcher.cs").exists()
    assert not (ROOT / "tools" / "build_launcher.py").exists()


def test_shortcut_bat_has_no_powershell():
    bat = ROOT / "tools" / "创建快捷方式.bat"
    if not bat.is_file():
        pytest.skip("分发脚本不在这份副本里")
    text = bat.read_text(encoding="ascii")      # 纯 ASCII，读得出来就是过了
    low = text.lower()
    for bad in ("powershell", "-executionpolicy", "bypass", "certutil",
                "bitsadmin", "mshta", "regsvr32"):
        assert bad not in low, f"启动脚本里不该出现 {bad}"
    assert "orante.shortcut" in low            # 活交给 Python 干


# cmd.exe 按字节偏移读 .bat：混进非 ASCII 会让后面每一行都错位。
# 这几支是启动 / 安装路径上会被双击的，必须纯 ASCII。
ASCII_BATS = ("Orante2.bat", "tools/创建快捷方式.bat", "tools/Install-Shortcuts.bat",
              "tools/悬浮图标-自测.bat")


def test_launch_bats_are_pure_ascii():
    for rel in ASCII_BATS:
        p = ROOT / Path(*rel.split("/"))
        name = p.name
        if not p.is_file():
            continue
        try:
            p.read_text(encoding="ascii")
        except UnicodeDecodeError as e:
            raise AssertionError(f"{name} 里有非 ASCII 字符（cmd.exe 会读错位）：{e}")


def test_share_whitelist_carries_icon_png_and_no_exe():
    import make_share
    assert "Orante me.png" in make_share.ROOT_KEEP      # 悬浮图标要这张图
    assert "build_me_icon.py" in make_share.TOOLS_KEEP
    for name in list(make_share.ROOT_KEEP) + list(make_share.TOOLS_KEEP):
        assert not name.lower().endswith((".exe", ".dll", ".pyd"))


# ---------- 桌面图标 ----------

def test_build_me_icon_makes_all_sizes(tmp_path):
    import build_me_icon as bmi
    src = tmp_path / "Orante me.png"
    img = Image.new("RGBA", (982, 1076), (0, 0, 0, 0))
    img.paste(Image.new("RGBA", (600, 900), (243, 178, 15, 255)), (150, 90))
    img.save(src)
    dest = tmp_path / "orante-me.ico"
    bmi.build(src, dest)
    bmi.verify(dest)                                   # 缺尺寸会 SystemExit
    im = Image.open(dest)
    assert sorted(im.ico.sizes()) == sorted((s, s) for s in bmi.SIZES)


def test_build_me_icon_squares_and_trims(tmp_path):
    import build_me_icon as bmi
    img = Image.new("RGBA", (400, 800), (0, 0, 0, 0))
    img.paste(Image.new("RGBA", (100, 200), (255, 0, 0, 255)), (30, 40))
    sq = bmi.square(img)
    assert sq.size[0] == sq.size[1]                    # 正方形
    assert sq.getpixel((1, 1))[3] == 0                 # 四角透明
    assert sq.size[0] >= 200                           # 裁掉透明边之后按内容定边长


def test_repo_icon_if_built_is_valid():
    ico = ROOT / "ui" / "assets" / "orante-me.ico"
    if not ico.is_file():
        pytest.skip("这份副本里还没生成 orante-me.ico")
    im = Image.open(ico)
    have = sorted(im.ico.sizes())
    for want in ((16, 16), (32, 32), (48, 48), (256, 256)):
        assert want in have, f"资源管理器要 {want}，现在只有 {have}"


# ---------- 快捷方式 ----------

def test_shortcut_plan_points_at_pythonw_and_new_icon(tmp_path):
    from orante import shortcut
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    pyw = tmp_path / ".venv" / "Scripts" / "pythonw.exe"
    pyw.write_bytes(b"")
    (tmp_path / "ui" / "assets").mkdir(parents=True)
    ico = tmp_path / "ui" / "assets" / "orante-me.ico"
    ico.write_bytes(b"")
    desk, menu = tmp_path / "Desktop", tmp_path / "Menu"
    items = shortcut.plan(tmp_path, desktop=desk, start_menu=menu)
    assert len(items) == 4                              # 桌面两个 + 开始菜单两个
    for it in items:
        assert it["target"] == pyw                      # 指向 pythonw，不是 bat 也不是 exe
        assert it["icon"] == ico                        # 用新图标
        assert it["workdir"] == tmp_path
    args = sorted(it["args"] for it in items)
    assert args == ["-m orante", "-m orante", "-m orante --new", "-m orante --new"]
    names = sorted(Path(it["path"]).name for it in items)
    assert names.count("Orante.lnk") == 2


def test_shortcut_icon_falls_back(tmp_path):
    from orante import shortcut
    (tmp_path / "ui" / "assets").mkdir(parents=True)
    old = tmp_path / "ui" / "assets" / "orante.ico"
    old.write_bytes(b"")
    assert shortcut.icon_path(tmp_path) == old
    new = tmp_path / "ui" / "assets" / "orante-me.ico"
    new.write_bytes(b"")
    assert shortcut.icon_path(tmp_path) == new


def test_shortcut_create_is_windows_only(monkeypatch):
    from orante import shortcut
    monkeypatch.setattr(os, "name", "posix")
    assert shortcut.create() == []
