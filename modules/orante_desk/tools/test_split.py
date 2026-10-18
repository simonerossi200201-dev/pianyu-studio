# -*- coding: utf-8 -*-
"""验收 split.split_task / preview：拿一篇真实 src.txt 跑 Flow 和 Grok。"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-split-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import db  # noqa: E402
from orante.errors import OranteError  # noqa: E402
from orante.paths import find_legacy_root  # noqa: E402
from orante.services.split import preview, split_task  # noqa: E402

db.init()

_WS = re.compile(r"\s+")


class FakeJob:
    def __init__(self):
        self.lines = []

    def log(self, msg: str) -> None:
        print(f"  {msg}")
        self.lines.append(msg)

    def check(self) -> None:
        pass


def _find_src() -> Path:
    pipe = find_legacy_root()
    if not pipe:
        raise SystemExit("FAIL：找不到流水线目录（下载图片\\），没法拿 src.txt")
    data = pipe / "0-长文案" / "数据"
    if not data.is_dir():
        raise SystemExit(f"FAIL：没有 {data}")
    cands = []
    seen = set()
    for pat in ("*.src.txt", "src.txt"):
        for p in sorted(data.rglob(pat)):
            key = p.resolve()
            if key not in seen:
                seen.add(key)
                cands.append(p)
    if not cands:
        raise SystemExit(f"FAIL：在 {data} 里找不到 src.txt / *.src.txt")
    return cands[0]


def _nospace(s: str) -> str:
    return _WS.sub("", s or "")


def _print_segs(label: str, segs: list[dict], words_key=None) -> None:
    print(f"\n=== {label} · {len(segs)} 段 ===")
    print(f"{'s':>4}  {'时长':>4}  {'词数':>4}  {'字数':>4}  前 40 字")
    from orante.services import split as split_mod
    wc = split_mod.flow_split.word_count
    for sg in segs:
        text = sg["text"]
        words = sg.get("words")
        if words is None:
            words = wc(text)
        chars = sg.get("chars", len(text))
        preview40 = text[:40].replace("\n", " ")
        print(f"{sg['s']:4d}  {sg['duration']:3d}s  {words:4d}  {chars:4d}  {preview40}")


def _assert_conserved(label: str, src: str, segs: list[dict]) -> None:
    joined = "".join(sg["text"] for sg in segs)
    a, b = _nospace(src), _nospace(joined)
    if a != b:
        raise SystemExit(
            f"FAIL {label} 字数守恒：原文去空白 {len(a)}，拆完 {len(b)}")
    if any(not (sg.get("text") or "").strip() for sg in segs):
        raise SystemExit(f"FAIL {label}：有空段")
    print(f"OK {label} 字数守恒：去空白 {len(a)} 字，{len(segs)} 段")


def _assert_segment_shape(label: str, segs: list[dict]) -> None:
    need = {"s", "text", "duration", "image", "role", "shot",
            "prompt_final", "status", "gen_id", "takes", "error"}
    for i, sg in enumerate(segs, 1):
        missing = need - set(sg)
        if missing:
            raise SystemExit(f"FAIL {label} 段 {i} 缺字段 {sorted(missing)}")
        if sg["s"] != i:
            raise SystemExit(f"FAIL {label}：第 {i} 段 s={sg['s']}")
        if sg.get("image"):
            raise SystemExit(f"FAIL {label}：image 应留空，实际 {sg['image']!r}")
        if sg.get("status") != "planned":
            raise SystemExit(f"FAIL {label}：status 应为 planned")


def test_real_src() -> Path:
    src_path = _find_src()
    text = src_path.read_text(encoding="utf-8")
    print(f"原文：{src_path}")
    print(f"长度：{len(text)} 字（去空白 {len(_nospace(text))}）")
    if not text.strip():
        raise SystemExit("FAIL：src.txt 是空的")

    for engine in ("flow", "grok"):
        job = FakeJob()
        task = {"engine": engine, "copy": text, "type": "long"}
        split_rule, segs = split_task(job, task, None)
        rule = (split_rule or {}).get("rule") or {}
        if rule.get("engine") != engine:
            raise SystemExit(f"FAIL {engine}：split_rule.engine={rule.get('engine')!r}")
        _assert_segment_shape(engine, segs)
        _print_segs(engine, segs)
        _assert_conserved(engine, text, segs)

        prev = preview(text, engine)
        if len(prev) != len(segs):
            raise SystemExit(
                f"FAIL preview({engine}) 段数 {len(prev)} ≠ split_task {len(segs)}")
        for a, b in zip(prev, segs):
            if a["text"] != b["text"] or a["duration"] != b["duration"]:
                raise SystemExit(f"FAIL preview({engine}) 与 split_task 不一致：s={a['s']}")
        print(f"OK preview({engine}) 与 split_task 一致")

    return src_path


def test_short() -> None:
    text = (
        "When God is changing your life, He will usually take you through "
        "these four stages first. Step 1 is protection."
    )
    for engine in ("flow", "grok"):
        segs = preview(text, engine)
        _assert_conserved(f"短文案/{engine}", text, segs)
        if not (1 <= len(segs) <= 3):
            print(f"  注：短文案 {engine} 切出 {len(segs)} 段（规格说可能 1~3）")
        _print_segs(f"短文案 {engine}", segs)
    print("OK 短文案也走同一套切法")


def test_empty() -> None:
    try:
        preview("   ", "flow")
    except OranteError as e:
        print(f"OK 空文案报错：{e.message}")
        return
    raise SystemExit("FAIL：空文案应该抛 OranteError")


def main() -> int:
    print(f"ORANTE_DATA = {TEST_DATA}")
    test_real_src()
    test_short()
    test_empty()
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
