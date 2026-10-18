# -*- coding: utf-8 -*-
"""片语 Studio splash — one-point perspective arcade, original vector art."""
import math, os
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1280, 720
CJK_B = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
CJK_M = "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc"
CJK_L = "/usr/share/fonts/opentype/noto/NotoSansCJK-DemiLight.ttc"
GOLD = (204, 166, 80); STONE = (240, 232, 220); STONE2 = (176, 163, 146)
STONE3 = (116, 106, 94); VERD = (150, 174, 160)
VPX, VPY = 1240, 486
F = lambda p, s: ImageFont.truetype(p, s)

def ground():
    yy, xx = np.mgrid[0:H, 0:W]
    x, y = xx / W, yy / H
    a = np.dstack([34 + 16*(1-y) + 12*(1-x), 29 + 12*(1-y) + 9*(1-x), 25 + 9*(1-y) + 7*(1-x)])
    d = np.sqrt(((x - 0.17) * 1.45) ** 2 + ((y - 0.10) * 1.75) ** 2)
    a += (np.clip(1 - d / 1.02, 0, 1) ** 2.1)[..., None] * np.array([78, 56, 27])
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB")

def shaft(img):
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    for i, (x0, x1, al) in enumerate([(-40, 210, 16), (40, 330, 11), (150, 470, 7)]):
        d.polygon([(x0, 0), (x1, 0), (x1 + 300, H), (x0 + 210, H)], fill=(255, 226, 170, al))
    lay = lay.filter(ImageFilter.GaussianBlur(46))
    return Image.alpha_composite(img.convert("RGBA"), lay).convert("RGB")

def arcade(img):
    """A colonnade receding to a single vanishing point. Generic Roman forms."""
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    x0, base0, top0 = -70, 652, 388          # nearest bay
    k = 1.0
    for i in range(14):
        x = VPX - (VPX - x0) * k
        nx = VPX - (VPX - x0) * (k * 0.820)
        base = VPY + (base0 - VPY) * k
        top = VPY - (VPY - top0) * k
        w = nx - x
        al = int(max(10, 60 * (k ** 0.5)))
        col = (216, 190, 146, al)
        spring = top + w * 0.5
        d.line([x, spring, x, base], fill=col, width=max(1, int(3 * k)))
        d.line([nx, spring, nx, base], fill=col, width=max(1, int(3 * k)))
        d.arc([x, top, nx, top + w], 180, 360, fill=col, width=max(1, int(3 * k)))
        d.line([x, top + w * 0.5, nx, top + w * 0.5], fill=(216, 190, 146, int(al * .5)),
               width=max(1, int(2 * k)))
        lw = max(1, int(2 * k))
        d.line([x - w * .07, spring, x + w * .07, spring], fill=col, width=lw)
        d.line([nx - w * .07, spring, nx + w * .07, spring], fill=col, width=lw)
        cy = top - w * .13
        d.line([x - w * .05, cy, nx + w * .05, cy], fill=(216, 190, 146, int(al * .65)), width=lw)
        d.rectangle([x + w * .44, top - w * .02, x + w * .56, top + w * .09],
                    outline=(216, 190, 146, int(al * .8)), width=lw)
        d.line([x, base, nx, base], fill=(216, 190, 146, int(al * .5)), width=lw)
        k *= 0.820
    for fy in (712, 668, 632, 604, 580, 560, 543, 528, 515):     # floor joints -> vanishing point
        d.line([0, fy, VPX, VPY], fill=(216, 190, 146, 17), width=1)
    d.line([0, VPY, W, VPY], fill=(204, 166, 80, 30), width=1)
    lay = lay.filter(ImageFilter.GaussianBlur(0.5))
    return Image.alpha_composite(img.convert("RGBA"), lay).convert("RGB")

def vignette(img):
    yy = np.linspace(0, 1, H)[:, None] * np.ones((1, W))
    v = np.clip((yy - 0.74) / 0.26, 0, 1) ** 1.6
    a = np.asarray(img).astype(np.float32) * (1 - v[..., None] * 0.34)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))

def grain(img, amt=4.2, seed=3):
    rng = np.random.default_rng(seed)
    a = np.asarray(img).astype(np.float32) + rng.normal(0, amt, (H, W))[..., None]
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))

def spaced(d, xy, text, font, fill, extra):
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=font, fill=fill)
        x += d.textlength(ch, font=font) + extra

def build(out="out8/splash.png", chrome=True):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    img = grain(vignette(arcade(shaft(ground()))))
    d = ImageDraw.Draw(img, "RGBA")
    mk = Image.open("final2/mark_1024.png").convert("RGBA")
    mw = 268; mh = int(mw * mk.height / mk.width)
    mk = mk.resize((mw, mh), Image.LANCZOS)
    gl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gl.paste(mk, (132, 268 - mh // 2), mk)
    gl = gl.filter(ImageFilter.GaussianBlur(26))
    gl.putalpha(gl.split()[3].point(lambda p: int(p * 0.42)))
    img = Image.alpha_composite(img.convert("RGBA"), gl).convert("RGB")
    d = ImageDraw.Draw(img, "RGBA")
    img.paste(mk, (132, 268 - mh // 2), mk)
    d.line([452, 176, 452, 400], fill=(204, 166, 80, 52), width=1)
    x = 516
    d.text((x, 168), "片语 Studio", font=F(CJK_B, 62), fill=STONE)
    spaced(d, (x + 3, 258), "CUTORA", F(CJK_M, 25), GOLD, 7)
    d.text((x, 320), "让每一句话，找到它的位置。", font=F(CJK_M, 29), fill=VERD)
    d.text((x + 2, 368), "Every word finds its place.", font=F(CJK_L, 21), fill=STONE3)
    d.text((x + 2, 414), "v7.0.0", font=F(CJK_L, 17), fill=(98, 90, 80))
    if chrome:
        d.text((132, 632), "加载字体…", font=F(CJK_L, 18), fill=STONE2)
        d.rounded_rectangle([132, 668, W - 132, 675], radius=4, fill=(48, 41, 33))
        d.rounded_rectangle([132, 668, 132 + int((W - 264) * 0.34), 675], radius=4, fill=GOLD)
    img.save(out); print("ok", out)

build()
build("out8/splash_bg.png", chrome=False)
