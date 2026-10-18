# -*- coding: utf-8 -*-
"""Deterministic multi-size icons from ``resources/brand/app.png``.

Center-crops to a square, resizes with LANCZOS, keeps the alpha channel.
Re-running overwrites ``app.ico`` and ``icon_<n>.png`` only.
It does not touch ``icon_512_light.png``, ``icon_mono_512.png``,
``mark_1024.png``, ``splash.png``, or ``splash_bg.png``.
"""
from __future__ import annotations

import os

from PIL import Image

# ICO cannot store above 256. 24 is inside the ico only; the brand
# checklist has no icon_24.png.
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
PNG_SIZES = (16, 32, 48, 64, 128, 256, 512, 1024)


def _root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def square_rgba(im: Image.Image) -> Image.Image:
    im = im.convert("RGBA")
    w, h = im.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    return im.crop((left, top, left + side, top + side))


def render_size(src: Image.Image, size: int) -> Image.Image:
    base = square_rgba(src)
    if base.size == (size, size):
        return base.copy()
    return base.resize((size, size), Image.Resampling.LANCZOS)


def build_icons(src_png: str, out_dir: str) -> str:
    """Write the png sidecars and ``app.ico``. Returns the ico path."""
    os.makedirs(out_dir, exist_ok=True)
    with Image.open(src_png) as raw:
        src = raw.convert("RGBA")
        src.load()
    for size in PNG_SIZES:
        path = os.path.join(out_dir, "icon_%d.png" % size)
        render_size(src, size).save(path, format="PNG")
    # Largest image has to be the primary frame or Pillow drops bigger sizes.
    ico_images = [render_size(src, size) for size in sorted(ICO_SIZES, reverse=True)]
    ico_path = os.path.join(out_dir, "app.ico")
    ico_images[0].save(
        ico_path,
        format="ICO",
        sizes=[(size, size) for size in ICO_SIZES],
        append_images=ico_images[1:],
    )
    return ico_path


def main() -> None:
    root = _root()
    src = os.path.join(root, "resources", "brand", "app.png")
    out = os.path.join(root, "resources", "brand")
    if not os.path.isfile(src):
        raise SystemExit("missing %s" % src)
    print(build_icons(src, out))


if __name__ == "__main__":
    main()
