# -*- coding: utf-8 -*-
"""验收 images.fetch：本地文件夹 / Drive 文件夹公开链接 / 直链 → images/p01…"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 用临时 DATA，避免把正式 used_images.json / token 搞乱
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-images-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante.errors import OranteError  # noqa: E402
from orante.services import images as images_mod  # noqa: E402
from orante.services.images import fetch  # noqa: E402


class FakeJob:
    def __init__(self):
        self.lines = []

    def log(self, msg: str) -> None:
        print(f"  {msg}")
        self.lines.append(msg)

    def check(self) -> None:
        pass


def _make_png(path: Path, w: int, h: int, color) -> None:
    from PIL import Image as PILImage
    path.parent.mkdir(parents=True, exist_ok=True)
    PILImage.new("RGB", (w, h), color).save(path, "PNG")


def _assert_landed(label: str, recs: list, task_dir: Path, expect_n: int | None = None):
    print(f"\n=== {label} 返回 ===")
    print(json.dumps(recs, ensure_ascii=False, indent=2))
    if not recs:
        raise SystemExit(f"FAIL {label}：返回空列表")
    if expect_n is not None and len(recs) != expect_n:
        raise SystemExit(f"FAIL {label}：期望 {expect_n} 张，实际 {len(recs)}")
    for i, rec in enumerate(recs, 1):
        p = rec.get("p") or ""
        want = f"p{i:02d}"
        if p != want:
            raise SystemExit(f"FAIL {label}：第 {i} 张编号是 {p!r}，应为 {want!r}")
        fp = task_dir / rec["file"]
        if not fp.is_file() or fp.stat().st_size == 0:
            raise SystemExit(f"FAIL {label}：{fp} 不存在或空")
        if rec.get("sha1") is None or len(rec.get("sha1") or "") != 40:
            raise SystemExit(f"FAIL {label}：sha1 不对 {rec.get('sha1')!r}")
        if rec.get("origin") not in ("local", "download"):
            raise SystemExit(f"FAIL {label}：origin={rec.get('origin')!r}")
        if not rec.get("src"):
            raise SystemExit(f"FAIL {label}：src 为空")
    print(f"OK {label}：{len(recs)} 张 → " + ", ".join(r["file"] for r in recs))


def test_local() -> None:
    src = TEST_DATA / "src_local"
    # 故意用 p1/p10/p2 检验自然排序
    _make_png(src / "p1.png", 400, 400, (200, 30, 30))
    _make_png(src / "p10.png", 400, 400, (30, 200, 30))
    _make_png(src / "p2.png", 400, 400, (30, 30, 200))
    _make_png(src / "tiny.png", 80, 80, (10, 10, 10))  # 太小：警告但仍收下
    (src / "readme.txt").write_text("不是图", encoding="utf-8")
    task_dir = TEST_DATA / "task_local"
    job = FakeJob()
    recs = fetch(job, {"image_source": str(src), "type": "long"}, task_dir)
    _assert_landed("本地文件夹", recs, task_dir, expect_n=4)
    names = [r["src"] for r in recs]
    if names[:3] != ["p1.png", "p2.png", "p10.png"]:
        raise SystemExit(f"FAIL 自然排序：得到 {names[:3]}，应为 p1, p2, p10")
    if recs[0]["origin"] != "local":
        raise SystemExit("FAIL 本地 origin 应为 local")
    if not any("太小" in ln for ln in job.lines):
        raise SystemExit("FAIL 小图应收下并警告，但日志里没有「太小」")
    print("  自然排序 OK：p1 → p2 → p10")


def test_http() -> None:
    if "pytest" in sys.modules:
        import pytest
        pytest.skip("联网直链，pytest 整目录跑时跳过；python tools/test_images.py 仍跑")
    url = "https://picsum.photos/id/237/400/400"
    task_dir = TEST_DATA / "task_http"
    recs = fetch(FakeJob(), {"image_source": url, "type": "long"}, task_dir)
    _assert_landed("直链", recs, task_dir, expect_n=1)
    if recs[0]["origin"] != "download":
        raise SystemExit("FAIL 直链 origin 应为 download")


def test_drive() -> None:
    if "pytest" in sys.modules:
        import pytest
        pytest.skip("联网 Drive，pytest 整目录跑时跳过；python tools/test_images.py 仍跑")
    # 强制走公开链接，不靠开发机上的 OAuth（分发版用户也没有）
    images_mod.get_access_token = lambda log=None: None  # type: ignore
    folders = [
        # 网上能搜到的公开测试图文件夹（约 50 张 jpg）
        "https://drive.google.com/drive/folders/1ZaBUtKjNWSv9TzJGND3IKCwAxBk4amMX?usp=sharing",
        # 本机流水线里用过的文件夹（若已公开）
        "https://drive.google.com/drive/folders/1Cr0XdmUOAfjy8EwsRlBHSjWo4QSqcM2o",
    ]
    last_err = None
    for url in folders:
        task_dir = Path(tempfile.mkdtemp(dir=str(TEST_DATA), prefix="task_drive-"))
        print(f"\n尝试 Drive 文件夹：{url}")
        try:
            recs = fetch(FakeJob(), {"image_source": url, "type": "short"}, task_dir)
            _assert_landed("Drive 文件夹公开链接", recs, task_dir, expect_n=1)
            if recs[0]["origin"] != "download":
                raise SystemExit("FAIL Drive origin 应为 download")
            return
        except OranteError as e:
            last_err = f"{e.message}（{e.hint}）"
            print(f"  这个文件夹不行：{last_err}")
        except Exception as e:
            last_err = str(e)
            print(f"  这个文件夹异常：{last_err}")
    raise SystemExit(f"FAIL Drive 文件夹公开链接：{last_err}")


def main() -> int:
    print(f"ORANTE_DATA = {TEST_DATA}")
    try:
        test_local()
        test_http()
        test_drive()
        print("\n全部通过。")
        return 0
    finally:
        shutil.rmtree(TEST_DATA, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
