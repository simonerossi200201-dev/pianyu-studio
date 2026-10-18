# -*- coding: utf-8 -*-
"""规格 64：白名单出包、源目录只读、泄露体检、向导、停用地雷 bat。"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-spec64-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import accounts, config, db, paths  # noqa: E402
from orante.services import setup as setupmod  # noqa: E402

db.init()


def _load(name: str):
    p = ROOT / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tree_sha(root: Path) -> str:
    make = _load("make_share")
    return make.tree_sha256(root)


def _fake_src(tmp: Path) -> Path:
    src = tmp / "src"
    (src / "data" / "chrome").mkdir(parents=True)
    (src / "data" / "orante.db").write_bytes(b"sqlite-fake")
    (src / "data" / "chrome" / "x").write_text("cookie", encoding="utf-8")
    (src / "a.py").write_text("print(1)\n", encoding="utf-8")
    (src / "a.py.bak-0101").write_text("old", encoding="utf-8")
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "a.cpython-310.pyc").write_bytes(b"\0")
    (src / "specs" / "reports" / "img").mkdir(parents=True)
    (src / "specs" / "reports" / "img" / "x.png").write_bytes(b"png")
    (src / "orante" / "legacy").mkdir(parents=True)
    (src / "orante" / "__init__.py").write_text('__version__ = "2.0.0"\n', encoding="utf-8")
    (src / "orante" / "legacy" / "跑批日志.txt").write_text("log\n", encoding="utf-8")
    (src / "orante" / "legacy" / "prompt_defaults.json").write_text(
        '{"desc":"x","camera":{},"tail":{},"problem":{},"expand":{},"refill_clause":""}',
        encoding="utf-8",
    )
    (src / "orante" / "legacy" / "hello.py").write_text("# legacy\n", encoding="utf-8")
    (src / "app").mkdir()
    (src / "app" / "server.py").write_text("# old\n", encoding="utf-8")
    (src / ".venv" / "Scripts").mkdir(parents=True)
    (src / ".venv" / "Scripts" / "python.exe").write_bytes(b"mz")
    (src / "ui").mkdir()
    (src / "ui" / "index.html").write_text("<html></html>", encoding="utf-8")
    (src / "ui" / "index.html.bak-0101").write_text("old", encoding="utf-8")
    (src / "Orante2.bat").write_text("@echo off\n", encoding="utf-8")
    (src / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    (src / "tools").mkdir()
    (src / "tools" / "13-Orante-Debug.bat").write_text("@echo off\n", encoding="utf-8")
    (src / "tools" / "grok-account.txt").write_text("secret\n", encoding="utf-8")
    (src / "说明文档").mkdir()
    (src / "说明文档" / "使用说明-分发版.md").write_text("# 使用说明\n", encoding="utf-8")
    (src / "说明文档" / "使用说明.md").write_text("TIK-L1 开发手册\n", encoding="utf-8")
    (src / "说明文档" / "使用说明.html").write_text(
        "<title>Orante 使用说明</title><p>ok</p>\n", encoding="utf-8")
    shots = src / "说明文档" / "分发图"
    shots.mkdir()
    for i, name in enumerate((
        "01-tasks.png", "02-gemini.png", "03-track.png", "04-generate.png",
        "05-review.png", "06-wizard-start.png", "07-wizard-accounts.png",
        "08-cards.png", "09-settings.png",
    ), 1):
        (shots / name).write_bytes(b"\x89PNG\r\n\x1a\n" + bytes([i]))
    return src


def test_make_share_whitelist_and_source_untouched(tmp_path: Path) -> None:
    make = _load("make_share")
    src = _fake_src(tmp_path)
    before = _tree_sha(src)
    dest = tmp_path / "out" / "Orante-分发版-2.0.0-20990101"
    make.build(src, dest, do_scan=False, do_smoke=False, do_zip=False)
    after = _tree_sha(src)
    assert before == after, "源目录在出包后变了"

    assert (dest / "orante" / "legacy" / "prompt_defaults.json").is_file()
    assert (dest / "orante" / "legacy" / "hello.py").is_file()
    assert (dest / "orante" / "__init__.py").is_file()
    assert (dest / "ui" / "index.html").is_file()
    assert (dest / "Orante2.bat").is_file()
    assert (dest / "tools" / "13-Orante-Debug.bat").is_file()
    assert (dest / "说明文档" / "使用说明.html").is_file()
    assert "TIK-L1" not in (dest / "说明文档" / "使用说明.html").read_text(encoding="utf-8")
    assert (dest / "说明文档" / "分发图" / "01-tasks.png").is_file()
    assert not (dest / "说明文档" / "使用说明.md").exists()
    assert (dest / "data" / ".keep").is_file()
    assert (dest / "data" / "logs" / ".keep").is_file()
    assert (dest / "data" / "chrome" / ".keep").is_file()
    assert (dest / "workspace" / ".keep").is_file()
    assert (dest / "分发清单.txt").is_file()

    assert not (dest / "data" / "orante.db").exists()
    assert not (dest / "data" / "chrome" / "x").exists()
    assert not list(dest.rglob("*.bak-*"))
    assert not list(dest.rglob("__pycache__"))
    assert not (dest / "specs").exists()
    assert not (dest / "orante" / "legacy" / "跑批日志.txt").exists()
    assert not (dest / "app").exists()
    assert not (dest / ".venv").exists()
    assert not (dest / "a.py").exists()
    assert not (dest / "tools" / "grok-account.txt").exists()
    assert not (dest / "说明文档" / "使用说明-分发版.md").exists()


def test_make_share_no_src_writes() -> None:
    text = (ROOT / "tools" / "make_share.py").read_text(encoding="utf-8")
    compact = re.sub(r"\s+", "", text)
    assert "rmtree(src" not in compact
    assert "unlink(src" not in compact
    assert "os.remove(src" not in compact
    assert "os.unlink(src" not in compact
    assert not re.search(r'open\(\s*src_root[^)]*["\']w', text)
    # rmtree 只允许用在 dest（_wipe_dest）
    assert "_wipe_dest" in text
    assert "只删出包目录" in text


def test_manifest_lines_and_sha(tmp_path: Path) -> None:
    make = _load("make_share")
    src = _fake_src(tmp_path)
    dest = tmp_path / "out" / "Orante-分发版-2.0.0-20990101"
    make.build(src, dest, do_scan=False, do_smoke=False, do_zip=False)
    man = (dest / "分发清单.txt").read_text(encoding="utf-8")
    lines = [ln for ln in man.splitlines()[3:] if ln.strip()]
    disk = [p for p in dest.rglob("*") if p.is_file() and p.name != "分发清单.txt"]
    assert len(lines) == len(disk), f"清单 {len(lines)} 行 vs 文件 {len(disk)}"
    # 抽 3 个对 sha256
    parsed = []
    for ln in lines:
        parts = ln.split()
        if len(parts) >= 3:
            parsed.append((parts[0], parts[-1]))
    sample = parsed[:3]
    assert len(sample) == 3
    for rel, digest in sample:
        p = dest / Path(*rel.split("/"))
        got = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        assert got == digest, f"{rel} sha {got} != {digest}"


def test_scan_aiza_and_allow_and_account(tmp_path: Path) -> None:
    scan = _load("scan_share_leak")
    dest = tmp_path / "pkg"
    dest.mkdir()
    (dest / "ok.txt").write_text("hello\n", encoding="utf-8")
    needles = scan.build_needles()
    hits = scan.scan_tree(dest, needles)
    assert hits == []

    (dest / "key.txt").write_text("AIza1234abcdefghijklmnop\n", encoding="utf-8")
    hits = scan.scan_tree(dest, needles)
    assert hits, "含 AIza 应拦下"
    assert any("key.txt" in h for h in hits)

    (dest / "key.txt").unlink()
    (dest / "chrome.txt").write_text(r"C:\Program Files" + "\n", encoding="utf-8")
    hits = scan.scan_tree(dest, needles)
    assert hits == [], f"C:\\Program Files 应放行，却命中 {hits}"

    names = scan.load_account_needles()
    if names:
        (dest / "acc.txt").write_text("账号 " + names[0] + " 在这里\n", encoding="utf-8")
        hits = scan.scan_tree(dest, needles)
        assert hits, f"真实账号名 {names[0]!r} 应拦下"
        assert any("acc.txt" in h for h in hits)


def test_defaults_no_local_paths() -> None:
    bad = ("D:\\", "C:\\Users\\", "下载图片", "1_PROJECT")
    for k, v in config.DEFAULTS.items():
        s = str(v)
        for n in bad:
            # workspace.root 是 Path 算出来的，源码里不是写死 D:\；
            # 运行时在开发机上会变成 D:\0_Dev\Orante\workspace。
            # 规格要的是 DEFAULTS 字面量不像本机路径——除了这条运行时绝对路径。
            if k == "workspace.root" and n in ("D:\\",):
                continue
            assert n not in s, f"{k} 默认值像本机路径：{s}"
        if k == "gemini.api_key":
            assert s == ""
    root = str(config.DEFAULTS.get("workspace.root") or "")
    assert "workspace" in root.replace("\\", "/").lower()
    assert "1_PROJECT" not in root


def test_setup_state_need_setup() -> None:
    db.run("DELETE FROM accounts")
    db.run("DELETE FROM settings WHERE key=?", ("setup.done",))
    st = setupmod.state()
    assert st["need_setup"] is True
    assert st["accounts_n"] == 0

    a = accounts.create("spec64-flow1", "grok")
    config.set("setup.done", True)
    st = setupmod.state()
    assert st["need_setup"] is False
    assert st["setup_done"] is True
    accounts.delete(a["id"])
    config.reset("setup.done")


def test_setup_skips_wizard_on_existing_install() -> None:
    """老装机：有账号但 settings 里没有 setup.done —— 不许弹向导。"""
    db.run("DELETE FROM accounts")
    db.run("DELETE FROM settings WHERE key=?", ("setup.done",))
    a = accounts.create("spec64-old-install", "grok")
    st = setupmod.state()
    assert st["need_setup"] is False
    assert st["setup_done"] is True          # 顺手补登记了
    # 补登记要真的落库，不是只在返回值里假装
    assert setupmod._truthy(config.get("setup.done")) is True
    accounts.delete(a["id"])
    config.reset("setup.done")


def test_legacy_available_false(monkeypatch) -> None:
    monkeypatch.setattr(paths, "find_legacy_root", lambda: None)
    st = setupmod.state()
    assert st["legacy_available"] is False


def test_clean_build_bat_no_delete() -> None:
    p = ROOT / "tools" / "Make-Clean-Build.bat"
    t = p.read_text(encoding="utf-8", errors="replace")
    assert "del " not in t.lower()
    assert "rmdir" not in t.lower()
    assert "15-Make-Share-Package.bat" in t


def test_version_single_source() -> None:
    from orante import __version__
    assert __version__  # 版本号会变（现在是 2.0.0-beta.2），只要求单点定义
    text = (ROOT / "orante" / "__init__.py").read_text(encoding="utf-8")
    assert text.count("__version__") == 1
    assert f'__version__ = "{__version__}"' in text or f"__version__ = '{__version__}'" in text


def test_setup_routes_exist() -> None:
    from orante.server import app
    paths_ = {getattr(r, "path", None) for r in app.routes}
    assert "/api/setup/state" in paths_
    assert "/api/setup/check" in paths_
    assert "/api/setup/complete" in paths_
    assert "/api/health" in paths_
    assert "/api/config" in paths_


def test_env_check_python_ok() -> None:
    r = setupmod.env_check()
    py = next(c for c in r["checks"] if c["id"] == "python")
    assert py["ok"] is True
    deps = next(c for c in r["checks"] if c["id"] == "deps")
    assert deps["ok"] is True
