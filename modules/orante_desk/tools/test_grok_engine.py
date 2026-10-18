# -*- coding: utf-8 -*-
"""Grok 引擎验收。

    python tools/test_grok_engine.py
        离线检查 + 拿 accounts.list_all() 里第一个 grok 账号 connect() → health()
        不真的提交。

    python tools/test_grok_engine.py --live 图片路径 "提示词"
        再提交一段 6s，轮询到出片，下载到 data/tmp/。
"""
from __future__ import annotations

import io
import sys
import time
from pathlib import Path

if "pytest" not in sys.modules:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orante import db, paths, accounts  # noqa: E402
from orante.engines.base import make_engine, BlockedImage  # noqa: E402
from orante.engines.grok import GrokEngine, dur_label, _blocked_reason  # noqa: E402
from orante.engines import grok as grok_mod  # noqa: E402


def _offline() -> None:
    print("=== 离线 ===")
    for name in ("prepare", "click_submit", "capture_anchor", "probe",
                 "goto_own_post", "download_one", "ensure_intact"):
        if not callable(getattr(grok_mod.grok_pipe, name, None)):
            raise SystemExit(f"FAIL grok_pipe 没有 {name}")
    for name in ("pass_tos_gate", "set_params", "verify_tab"):
        if not callable(getattr(grok_mod.grok_batch, name, None)):
            raise SystemExit(f"FAIL grok_batch 没有 {name}")
    print("OK grok_pipe / grok_batch 函数都在")

    mapping = {(6, "6s"), (10, "10s"), (15, "15s"), ("6s", "6s"),
               (4, "6s"), (8, "10s"), (12, "15s")}
    for src, want in mapping:
        got = dur_label(src)
        if got != want:
            raise SystemExit(f"FAIL dur_label({src!r}) = {got!r}，要 {want!r}")
    print("OK dur_label 6/10/15 + Flow 档靠档")

    fake = {"id": "A000", "name": "fake-grok", "engine": "grok",
            "port": 9999, "max_concurrent": 4, "profile": ""}
    eng = GrokEngine(fake, print)
    if eng.capacity() != 4:
        raise SystemExit(f"FAIL capacity 空闲应为 4，实际 {eng.capacity()}")
    eng._active.add("x")
    if eng.capacity() != 3:
        raise SystemExit(f"FAIL capacity 挂 1 条应为 3，实际 {eng.capacity()}")
    print("OK capacity = max_concurrent - inflight")

    try:
        eng.gen_images("x", None)
        raise SystemExit("FAIL gen_images 应该抛 NotImplementedError")
    except NotImplementedError as e:
        if "不负责生图" not in str(e) and "不支持生图" not in str(e):
            raise SystemExit(f"FAIL gen_images 文案不对：{e}")
    print("OK gen_images 未实现")

    row = eng._make_row({"s": 3, "text": "Amen.", "image": "p03"},
                        Path("nope.jpg"), "完整提示词正文", 6)
    for k in ("图片", "完整提示词", "时长", "编号", "第几段"):
        if k not in row:
            raise SystemExit(f"FAIL row 缺键 {k}")
    if row["时长"] != "6s" or row["完整提示词"] != "完整提示词正文":
        raise SystemExit(f"FAIL row 拼得不对：{row}")
    print("OK row 键对齐 grok_pipe.prepare")

    if not _blocked_reason("this image violates our content policy"):
        raise SystemExit("FAIL _blocked_reason 没认 policy")
    if not _blocked_reason("内容违规，无法使用"):
        raise SystemExit("FAIL _blocked_reason 没认 违规")
    if not _blocked_reason("moderation flagged this"):
        raise SystemExit("FAIL _blocked_reason 没认 moderat")
    if _blocked_reason("普通失败：按钮还是灰的"):
        raise SystemExit("FAIL _blocked_reason 误伤普通失败")
    print("OK BlockedImage 关键词")

    made = make_engine(fake, print)
    if not isinstance(made, GrokEngine) or made.name != "grok":
        raise SystemExit(f"FAIL make_engine 没给出 GrokEngine：{type(made)}")
    print("OK make_engine(grok) → GrokEngine")


def _first_grok() -> dict:
    db.init()
    accs = [a for a in accounts.list_all() if a.get("engine") == "grok" and a.get("enabled", 1)]
    if not accs:
        accs = [a for a in accounts.list_all() if a.get("engine") == "grok"]
    if not accs:
        raise SystemExit("FAIL 一个 grok 账号都没有。到「账号」抽屉加一个，或跑一次导入老泳道。")
    acc = accs[0]
    print(f"用账号 {acc['name']}  端口 {acc['port']}  profile={acc.get('profile')}")
    return acc


def _connect(acc: dict) -> GrokEngine:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ModuleNotFoundError:
        raise SystemExit(
            "FAIL 这个 Python 没装 playwright。"
            "用流水线 .venv 跑（跟 tools/Test-Flow.bat 同一个），"
            "或在本 venv 里 pip install playwright。"
        )
    eng = GrokEngine(acc, print)
    eng.connect()
    h = eng.health()
    print("health", h)
    if not h.get("ok") or not h.get("logged_in"):
        eng.close()
        raise SystemExit(f"FAIL health 未登录：{h}")
    print("OK connect + health")
    return eng


def _live(eng: GrokEngine, img: Path, prompt: str) -> None:
    if not img.is_file():
        raise SystemExit(f"FAIL 图片不存在：{img}")
    dest_dir = paths.DATA / "tmp"
    dest_dir.mkdir(parents=True, exist_ok=True)
    print(f"=== live 提交 6s  {img} ===")
    gid = eng.submit({"s": 1, "text": prompt, "image": "p01"}, img, prompt, 6)
    print("gen_id", gid)
    t0 = time.time()
    last = None
    while time.time() - t0 < 900:
        r = eng.poll([gid])
        last = r.get(gid) or {}
        print(" poll", last)
        if last.get("state") != "pending":
            break
        time.sleep(8)
    if not last or last.get("state") != "done":
        raise SystemExit(f"FAIL live 没出片：{last}")
    dest = dest_dir / f"grok-{gid[:8]}.mp4"
    saved = eng.download(gid, dest)
    if not saved.is_file() or saved.stat().st_size < 50_000:
        raise SystemExit(f"FAIL 下载的文件不像视频：{saved}")
    print("saved", saved, saved.stat().st_size, "bytes")
    print("OK live 提交 → 出片 → 下载")


def main() -> None:
    args = sys.argv[1:]
    live = False
    img, prompt = None, ""
    if args and args[0] == "--live":
        live = True
        if len(args) < 3:
            raise SystemExit("用法：python tools/test_grok_engine.py --live 图片路径 \"提示词\"")
        img, prompt = Path(args[1]), args[2]
    _offline()
    acc = _first_grok()
    eng = _connect(acc)
    try:
        if live:
            _live(eng, img, prompt)
        else:
            print("（默认不提交。真要跑一段：--live 图片路径 \"提示词\"）")
    finally:
        eng.close()
    print("ALL OK")


if __name__ == "__main__":
    main()
