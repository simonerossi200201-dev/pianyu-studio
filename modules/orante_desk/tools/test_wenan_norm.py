# -*- coding: utf-8 -*-
"""规格 48：wenan_norm 古英语不拆、闭引号不插空格、该拆的还拆。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-wenan-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import paths  # noqa: E402

paths.add_legacy_to_sys_path()
import wenan_norm  # type: ignore  # noqa: E402


def _norm(src: str) -> str:
    out, rep = wenan_norm.normalize(src)
    assert wenan_norm.skeleton(out) == wenan_norm.skeleton(src), (
        f"骨架变了：{src!r} → {out!r}"
    )
    assert rep.get("ok") is True, f"ok=False：{src!r} → {rep}"
    return out


def test_archaic_untouched() -> None:
    for src in ("knoweth no man", "cometh", "saith", "hath"):
        out = _norm(src)
        assert out == src, f"古英语被改了：{src!r} → {out!r}"
    print("OK 古英语：knoweth / cometh / saith / hath 原样")


def test_closing_quote_untouched() -> None:
    for src in ('my Father only." So no', 'sorrows." Look'):
        out = _norm(src)
        assert out == src, f"闭引号被插空格：{src!r} → {out!r}"
        assert '. "' not in out, f"出现「句号 空格 闭引号」：{out!r}"
    print("OK 闭引号：only.\" So / sorrows.\" Look 原样")


def test_still_splits() -> None:
    assert _norm("SaulJoseph") == "Saul Joseph"
    assert _norm("end.Then") == "end. Then"
    assert _norm("Ecclesiastes 4:9") == "Ecclesiastes 4:9"
    print("OK 仍拆 SaulJoseph / end.Then；Ecclesiastes 4:9 不动")


def test_skeleton_on_all() -> None:
    samples = (
        "knoweth no man",
        "cometh",
        "saith",
        "hath",
        'my Father only." So no',
        'sorrows." Look',
        "SaulJoseph",
        "end.Then",
        "Ecclesiastes 4:9",
    )
    for src in samples:
        out, rep = wenan_norm.normalize(src)
        assert wenan_norm.skeleton(out) == wenan_norm.skeleton(src), src
        assert rep.get("ok") is True, src
    print("OK skeleton 不变式：%d 条" % len(samples))


def main() -> int:
    test_archaic_untouched()
    test_closing_quote_untouched()
    test_still_splits()
    test_skeleton_on_all()
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
