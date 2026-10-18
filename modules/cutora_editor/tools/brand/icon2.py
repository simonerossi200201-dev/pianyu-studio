# -*- coding: utf-8 -*-
"""片语 Studio / CUTORA — icon assets from the approved gold mark.

Do not run this unless the mark source changes. Approved bitmaps live in
resources/brand/; this script only regenerates them.
"""
import os
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageChops, ImageEnhance

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
OUT = os.path.join(_ROOT, "resources", "brand")
MARK = Image.open(os.path.join(_HERE, "mark_matte.png")).convert("RGBA")

def tile(w, light=False):
    yy, xx = np.mgrid[0:w, 0:w]
    t = (xx / w * 0.35 + yy / w * 0.65)
    top, bot = ((248, 243, 234), (218, 207, 189)) if light else ((40, 34, 27), (17, 15, 13))
    a = np.array(top) + (np.array(bot) - np.array(top)) * t[..., None]
    if not light:
        d = np.sqrt(((xx / w - 0.24) * 1.45) ** 2 + ((yy / w - 0.15) * 1.6) ** 2)
        a += (np.clip(1 - d, 0, 1) ** 2.2)[..., None] * np.array([70, 50, 24])
    img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    r = Image.new("L", (w, w), 0)
    ImageDraw.Draw(r).rounded_rectangle([0, 0, w - 1, w - 1], radius=int(w * 0.221), fill=255)
    img.putalpha(r)
    return img

def build(size, light=False):
    pad = 0.155 if size >= 128 else (0.075 if size <= 32 else 0.10)
    bw = max(int(size * (1 - 2 * pad)), 1)
    bh = max(int(bw * MARK.height / MARK.width), 1)
    if size <= 32:
        sil = MARK.split()[3].resize((bw, bh), Image.LANCZOS)
        sil = sil.point(lambda v: 255 if v > 96 else 0).filter(ImageFilter.GaussianBlur(0.4))
        yy = np.linspace(0, 1, bh)[:, None] * np.ones((1, bw))
        hi, lo = np.array([238, 205, 132]), np.array([180, 132, 56])
        g = hi + (lo - hi) * yy[..., None]
        m = Image.fromarray(g.astype(np.uint8), "RGB").convert("RGBA")
        m.putalpha(sil)
    else:
        m = MARK.resize((bw, bh), Image.LANCZOS)
        if size <= 64:
            m = ImageEnhance.Sharpness(m).enhance(1.7)
    base = tile(size, light)
    x, y = (size - bw) // 2, int((size - bh) / 2 + size * 0.004)
    if size >= 64:
        sh = Image.new("L", (size, size), 0)
        sh.paste(m.split()[3], (x, y))
        sh = ImageChops.offset(sh.filter(ImageFilter.GaussianBlur(size * 0.020)),
                               0, int(size * 0.016))
        s = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        s.putalpha(sh.point(lambda p: int(p * (0.32 if light else 0.60))))
        base = Image.alpha_composite(base, s)
    out = base.copy(); out.alpha_composite(m, (x, y))
    if size >= 256:
        rng = np.random.default_rng(5)
        arr = np.asarray(out.convert("RGB")).astype(np.float32)
        arr += rng.normal(0, 2.6, arr.shape[:2])[..., None]
        g = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert("RGBA")
        g.putalpha(out.split()[3]); out = g
    out.putalpha(base.split()[3])
    return out

def main():
    os.makedirs(OUT, exist_ok=True)
    for s in (1024, 512, 256, 128, 64, 48, 32, 16):
        build(s).save(f"{OUT}/icon_{s}.png")
    build(512, light=True).save(f"{OUT}/icon_512_light.png")
    MARK.resize((1024, int(1024 * MARK.height / MARK.width)), Image.LANCZOS).save(
        f"{OUT}/mark_1024.png")

    sil = MARK.split()[3].point(lambda v: 255 if v > 128 else 0)
    mono = Image.new("RGB", (512, 512), (255, 255, 255))
    bw = int(512 * 0.70); bh = int(bw * MARK.height / MARK.width)
    blk = Image.new("RGB", (bw, bh), (20, 18, 16))
    mono.paste(blk, ((512 - bw) // 2, (512 - bh) // 2),
               sil.resize((bw, bh), Image.LANCZOS))
    mono.save(f"{OUT}/icon_mono_512.png")

    ico = Image.open(f"{OUT}/icon_256.png")
    ico.save(f"{OUT}/app.ico", format="ICO",
             sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])
    print("ok")


if __name__ == "__main__":
    main()
