# -*- coding: utf-8 -*-
"""规格 81：本地文件夹图片来源。先设 ORANTE_DATA。"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-spec81-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from orante.errors import OranteError  # noqa: E402
from orante.models import sha1_file  # noqa: E402
from orante.services import images as imgmod  # noqa: E402
from orante.services.images import classify, fetch, probe_source  # noqa: E402

fail = 0


class FakeJob:
    def __init__(self):
        self.lines = []

    def log(self, msg: str) -> None:
        self.lines.append(str(msg))

    def check(self) -> None:
        pass


def ok(msg):
    print("  OK  ", msg)


def bad(msg):
    global fail
    fail += 1
    print("  FAIL", msg)
    raise AssertionError(msg)


def _png(path: Path, size=(400, 400), color=(40, 80, 120)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "PNG")


def test_classify():
    d = TEST_DATA / "cls-dir"
    d.mkdir()
    kind, payload = classify(str(d))
    if kind != "local_dir":
        bad(f"存在的目录 kind={kind!r}")
    else:
        ok("classify 存在的目录 → local_dir")

    missing = TEST_DATA / "no-such-81-folder"
    try:
        classify(str(missing))
        bad("不存在的路径应该抛 OranteError")
    except OranteError as e:
        if "认不出" not in (e.message or ""):
            bad(f"不存在路径文案没有「认不出」: {e.message!r}")
        else:
            ok("classify 不存在 → 认不出")

    quoted = f'"{d}"'
    kind, payload = classify(quoted)
    if kind != "local_dir":
        bad(f"带引号路径 kind={kind!r}")
    else:
        ok("classify 去掉引号")

    ptype = type(Path("."))

    def fake_is_dir(self):
        s = str(self).replace("/", "\\").rstrip("\\").lower()
        return s.endswith("\\x") or s.endswith("share\\x")

    with patch.object(ptype, "is_dir", fake_is_dir), patch.object(ptype, "is_file", lambda self: False):
        kind, payload = classify(r"\\srv\share\x")
        if kind != "local_dir":
            bad(f"UNC kind={kind!r}")
        else:
            ok("classify UNC → local_dir")


def test_probe_source():
    imgmod.clear_probe_cache()
    src = TEST_DATA / "probe-top"
    src.mkdir()
    for i, name in enumerate(("2.jpg", "10.jpg", "a.png", "b.webp", "c.jpg")):
        _png(src / name, color=(20 + i * 10, 40, 80))
    (src / "readme.txt").write_text("no", encoding="utf-8")
    rec = probe_source(str(src))
    if rec.get("kind") != "local_dir" or not rec.get("ok"):
        bad(f"probe top kind/ok {rec}")
    elif rec.get("n") != 5:
        bad(f"probe n={rec.get('n')} want 5")
    elif len(rec.get("sample") or []) != 4:
        bad(f"sample {rec.get('sample')}")
    elif rec["sample"][0] != "2.jpg" or rec["sample"][1] != "10.jpg":
        bad(f"sample 自然序 {rec['sample']}")
    else:
        ok("probe 真实张数和前 4 个文件名")

    nested = TEST_DATA / "probe-nested"
    nested.mkdir()
    sub = nested / "sub"
    for i in range(18):
        _png(sub / f"n{i:02d}.jpg", color=(i + 10, 20, 30))
    rec = probe_source(str(nested))
    if rec.get("n") != 0 or rec.get("n_deep") != 18:
        bad(f"顶层 0 子目录有：n={rec.get('n')} n_deep={rec.get('n_deep')}")
    elif rec.get("ok"):
        bad("顶层 0 张没开递归应该 ok=false")
    else:
        ok("probe n=0 n_deep=18")

    rec = probe_source(str(TEST_DATA / "no-folder-81"))
    if rec.get("ok"):
        bad("路径不存在还 ok")
    elif "找不到" not in (rec.get("note") or ""):
        bad(f"不存在 note={rec.get('note')!r}")
    else:
        ok("路径不存在 ok:false + 人话")

    called = []

    def boom(*a, **k):
        called.append((a, k))
        raise AssertionError("不该发网络请求")

    with patch.object(imgmod.requests, "get", boom), patch.object(imgmod.requests, "post", boom):
        rec = probe_source("https://drive.google.com/drive/folders/1ZaBUtKjNWSv9TzJGND3IKCwAxBk4amMX")
    if called:
        bad(f"Drive probe 发了网络请求 {len(called)} 次")
    elif rec.get("kind") != "drive_folder" or not rec.get("ok"):
        bad(f"Drive probe {rec}")
    else:
        ok("Drive 链接不发网络请求")


def test_recursive_list():
    root = TEST_DATA / "walk"
    _png(root / "2.jpg")
    _png(root / "10.jpg")
    _png(root / "sub" / "a.jpg")
    _png(root / "sub" / "nested" / "b.jpg")
    _png(root / "sub" / "nested" / "deep" / "c.jpg")
    _png(root / "sub" / "nested" / "deep" / "too" / "d.jpg")  # 第 4 层，不要
    _png(root / "_skip" / "x.jpg")
    _png(root / ".dot" / "y.jpg")
    (root / "readme.txt").write_text("x", encoding="utf-8")
    (root / ".hidden.jpg").write_bytes(b"no")

    top = imgmod._list_local_dir(root, recursive=False)
    names = [p.name for p in top]
    if names != ["2.jpg", "10.jpg"]:
        bad(f"recursive=False 得到 {names}")
    else:
        ok("recursive=False 只拿顶层")

    deep = imgmod._list_local_dir(root, recursive=True)
    names = [p.name for p in deep]
    if "d.jpg" in names or "x.jpg" in names or "y.jpg" in names or ".hidden.jpg" in names:
        bad(f"递归不该拿到跳过的：{names}")
    elif names != ["2.jpg", "10.jpg", "a.jpg", "b.jpg", "c.jpg"]:
        # 自然序按文件名：2, 10, a, b, c
        bad(f"递归列表 {names}")
    else:
        ok("recursive=True 3 层内、跳过 _/. 、自然序")


def test_fetch_empty_top():
    src = TEST_DATA / "empty-top"
    _png(src / "sub" / "a.jpg")
    _png(src / "sub" / "b.jpg")
    job = FakeJob()
    dest = TEST_DATA / "task-empty-top"
    try:
        fetch(job, {"image_source": str(src), "type": "long"}, dest)
        bad("顶层 0 张没开递归不该静默成功")
    except OranteError as e:
        msg = e.message or ""
        if "包含子文件夹" not in msg or "2 张" not in msg:
            bad(f"人话错误不对：{msg!r}")
        else:
            ok("顶层 0 张 + 子目录有图 → 人话错误")


def test_modes():
    def run(mode, src_name):
        src = TEST_DATA / src_name
        _png(src / "p.jpg", color=(90, 20, 20))
        raw = (src / "p.jpg").read_bytes()
        dest = TEST_DATA / f"task-{src_name}"
        job = FakeJob()
        recs = fetch(job, {
            "image_source": str(src), "type": "long",
            "meta": {"image_src_mode": mode},
        }, dest)
        landed = next((dest / "images").glob("p01.*"), None)
        return job, src / "p.jpg", landed, recs, raw

    job, src_p, dest_p, recs, raw = run("copy", "mode-copy")
    if not src_p.is_file() or not dest_p or not dest_p.is_file():
        bad("复制：源或目标丢了")
    elif sha1_file(src_p) != sha1_file(dest_p):
        bad("复制：sha1 不同")
    elif "复制" not in "\n".join(job.lines):
        bad("复制日志没写档位")
    else:
        ok("复制：源还在，sha1 相同")

    job, src_p, dest_p, recs, raw = run("copy_mark", "mode-mark")
    marked = list(src_p.parent.glob("【已使用】*"))
    if src_p.is_file():
        bad("复制并标记：源文件名没改")
    elif not marked:
        bad("复制并标记：没找到【已使用】")
    elif hashlib.sha1(marked[0].read_bytes()).hexdigest() != hashlib.sha1(raw).hexdigest():
        bad("复制并标记：内容变了")
    elif not dest_p or not dest_p.is_file():
        bad("复制并标记：任务目录没图")
    else:
        ok("复制并标记：源改名内容不变")

    job, src_p, dest_p, recs, raw = run("move", "mode-move")
    if src_p.is_file():
        bad("移动：源文件还在")
    elif not dest_p or not dest_p.is_file():
        bad("移动：目标不在")
    elif "移动" not in "\n".join(job.lines):
        bad("移动日志没写档位")
    else:
        ok("移动：源不在了，目标在")

    src = TEST_DATA / "mode-move-busy"
    _png(src / "p.jpg", color=(11, 22, 33))
    dest = TEST_DATA / "task-move-busy"
    job = FakeJob()

    def boom(a, b):
        raise OSError("被占用")

    with patch.object(imgmod.shutil, "move", boom):
        recs = fetch(job, {
            "image_source": str(src), "type": "long",
            "meta": {"image_src_mode": "move"},
        }, dest)
    log = "\n".join(job.lines)
    if not (src / "p.jpg").is_file():
        bad("降级：源文件没了")
    elif not any((dest / "images").glob("p01.*")):
        bad("降级：目标没拷上")
    elif "降级" not in log:
        bad(f"降级日志没有「降级」: {log}")
    elif not recs:
        bad("降级后准备失败了")
    else:
        ok("移动被占用 → 降级成复制，准备没失败")


def test_skip_and_two_tasks():
    src = TEST_DATA / "skip-src"
    _png(src / "keep.jpg")
    (src / "readme.txt").write_text("x", encoding="utf-8")
    (src / ".secret.jpg").write_bytes(b"no")
    dest1 = TEST_DATA / "task-skip-1"
    job = FakeJob()
    recs = fetch(job, {"image_source": str(src), "type": "long"}, dest1)
    log = "\n".join(job.lines)
    if len(recs) != 1:
        bad(f"跳过之后应收 1 张，得到 {len(recs)}")
    elif "跳过" not in log:
        bad(f"日志没报跳过：{log}")
    else:
        ok("非图片 / . 开头被跳过并记日志")

    dest2 = TEST_DATA / "task-skip-2"
    recs2 = fetch(FakeJob(), {"image_source": str(src), "type": "long"}, dest2)
    if len(recs2) != 1:
        bad("第二个任务没拿到图")
        return
    used = imgmod._load_used()
    sha = recs[0]["sha1"]
    rec = used.get(sha) or {}
    n = int(rec.get("count") or 0) if isinstance(rec, dict) else 0
    if n < 2:
        bad(f"used_images 计数 {n}，连开两次应 ≥ 2")
    else:
        ok("同一文件夹连开两个任务，used_images 计数正确")


def test_node_ui():
    script = ROOT / "tools" / "test_spec81_ui.js"
    r = subprocess.run(
        ["node", str(script)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if r.returncode != 0:
        bad(f"node ui\n{r.stdout}\n{r.stderr}")
    else:
        ok("test_spec81_ui.js")
        print(r.stdout.strip())


def main() -> int:
    print(f"ORANTE_DATA = {TEST_DATA}")
    test_classify()
    test_probe_source()
    test_recursive_list()
    test_fetch_empty_top()
    test_modes()
    test_skip_and_two_tasks()
    test_node_ui()
    if fail:
        print(f"\n{fail} FAIL")
        return 1
    print("\nALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
