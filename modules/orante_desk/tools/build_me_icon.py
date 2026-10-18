# -*- coding: utf-8 -*-
r"""把根目录的 "Orante me.png" 做成桌面快捷方式用的 ui/assets/orante-me.ico。

为什么单独一个脚本：build_icons.py 那一套是从 icon.svg 渲染程序图标的（要起浏览器），
这里只是把用户自己那张图规规矩矩地转成多尺寸 ICO，不需要浏览器、不产生任何可执行文件。
.ico 是图片，杀毒软件不会因为它报毒——真正招误报的是 exe，那个已经删掉了。

做法：去掉四周透明边 → 放进正方形画布（留 4% 边距）→ 一次写进 16/24/32/48/64/128/256 七个尺寸。

    python tools/build_me_icon.py [源图] [目标ico]
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "Orante me.png"
DEST = ROOT / "ui" / "assets" / "orante-me.ico"
SIZES = (256, 128, 64, 48, 32, 24, 16)
MARGIN = 0.04           # 四周留一点，贴着边框看着挤


def square(img: Image.Image, margin: float = MARGIN) -> Image.Image:
    """裁掉透明边，居中放进正方形画布。"""
    img = img.convert("RGBA")
    box = img.getbbox()          # 只看非全透明的部分
    if box:
        img = img.crop(box)
    w, h = img.size
    side = int(round(max(w, h) / (1 - 2 * margin)))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - w) // 2, (side - h) // 2), img)
    return canvas


def build(src: Path = SRC, dest: Path = DEST, sizes=SIZES) -> Path:
    if not Path(src).is_file():
        raise SystemExit(f"找不到源图：{src}")
    img = square(Image.open(src))
    big = img.resize((max(sizes), max(sizes)), Image.LANCZOS)
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    big.save(dest, format="ICO", sizes=[(s, s) for s in sizes])
    return Path(dest)


def verify(dest: Path = DEST, sizes=SIZES) -> None:
    """Windows 资源管理器要 16/32/48/256 都在，缺一个就会显示得很糊。"""
    im = Image.open(dest)
    have = {sz for sz in getattr(im, "ico", None).sizes()} if hasattr(im, "ico") else set()
    if not have:
        have = {im.size}
    want = {(s, s) for s in sizes}
    missing = want - have
    if missing:
        raise SystemExit(f"{dest.name} 缺尺寸：{sorted(missing)}")
    print(f"✓ {dest}（{', '.join(str(s) for s in sorted(sizes))}）", flush=True)


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    src = Path(args[0]) if args else SRC
    dest = Path(args[1]) if len(args) > 1 else DEST
    build(src, dest)
    verify(dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
