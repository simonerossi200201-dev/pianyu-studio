# -*- coding: utf-8 -*-
"""生成片语工作室 9:16 竖屏海报级背景与配套软件圆形徽章图标（4x 超采样抗锯齿）。"""
from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

LTX_ROOT = Path(__file__).resolve().parent.parent
BRAND_DIR = LTX_ROOT / "resources" / "brand"
CUTORA_BRAND = LTX_ROOT / "modules" / "cutora_editor" / "resources" / "brand"
ROBOTA_AVATAR = Path(r"D:\0_Robota\robota\console\static\avatar.png")

WIN_FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"


def _get_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    cands = [
        WIN_FONTS / ("msyhbd.ttc" if bold else "msyh.ttc"),
        WIN_FONTS / "msyh.ttc",
        LTX_ROOT / "modules" / "cutora_editor" / "resources" / "fonts" / "Noto_Sans_SC" / "NotoSansSCwght.ttf",
    ]
    for c in cands:
        if c.is_file():
            try:
                return ImageFont.truetype(str(c), size)
            except Exception:
                pass
    return ImageFont.load_default()


def build_poster_bg_9x16() -> Path:
    """参照 CUTORA splash2.py 的罗马石材与古典金光影算法，生成 1080x1920 (9:16) 竖屏海报底图。"""
    W, H = 1080, 1920
    yy, xx = np.mgrid[0:H, 0:W]
    x, y = xx / W, yy / H

    # 暖玄武岩石材基底 (#141210 -> #241E18)
    base_r = 22 + 16 * (1 - y) + 10 * (1 - x)
    base_g = 19 + 13 * (1 - y) + 7 * (1 - x)
    base_b = 16 + 10 * (1 - y) + 5 * (1 - x)
    a = np.dstack([base_r, base_g, base_b])

    # 顶部中央的古典金穹顶柔光
    d_top = np.sqrt(((x - 0.50) * 1.35) ** 2 + ((y - 0.105) * 2.1) ** 2)
    a += (np.clip(1 - d_top / 0.95, 0, 1) ** 2.2)[..., None] * np.array([82, 58, 26])

    # 中部微弱暖金环境反光
    d_mid = np.sqrt(((x - 0.22) * 1.4) ** 2 + ((y - 0.55) * 1.8) ** 2)
    a += (np.clip(1 - d_mid / 0.90, 0, 1) ** 2.4)[..., None] * np.array([36, 26, 12])

    img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB")

    # 倾斜金光束 (Dusk Shafts)
    shaft_lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shaft_lay)
    for x0, x1, al in [(-80, 240, 18), (120, 480, 12), (380, 760, 8)]:
        sd.polygon(
            [(x0, 0), (x1, 0), (x1 + 420, H), (x0 + 280, H)],
            fill=(242, 210, 148, al),
        )
    shaft_lay = shaft_lay.filter(ImageFilter.GaussianBlur(54))
    img = Image.alpha_composite(img.convert("RGBA"), shaft_lay).convert("RGB")

    # 罗马透视拱廊与金色几何星轨线条
    arc_lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ad = ImageDraw.Draw(arc_lay)

    cx, cy = W // 2, 185
    for rad, al, w_px in [(125, 36, 2), (178, 22, 1), (242, 15, 1), (330, 10, 1)]:
        ad.ellipse(
            [cx - rad, cy - rad, cx + rad, cy + rad],
            outline=(216, 182, 110, al),
            width=w_px,
        )
    ad.line([72, cy, cx - 140, cy], fill=(204, 166, 80, 30), width=1)
    ad.line([cx + 140, cy, W - 72, cy], fill=(204, 166, 80, 30), width=1)

    # 底部透视罗马拱廊（呼应 CUTORA splash_bg.png）
    vpx, vpy = W + 120, 1540
    x0, base0, top0 = -60, 1880, 1360
    k = 1.0
    for _ in range(12):
        xx0 = vpx - (vpx - x0) * k
        nx = vpx - (vpx - x0) * (k * 0.83)
        base = vpy + (base0 - vpy) * k
        top = vpy - (vpy - top0) * k
        w_bay = nx - xx0
        al = int(max(8, 40 * (k ** 0.55)))
        col = (216, 190, 146, al)
        spring = top + w_bay * 0.5
        lw = max(1, int(2 * k))
        ad.line([xx0, spring, xx0, base], fill=col, width=lw)
        ad.line([nx, spring, nx, base], fill=col, width=lw)
        ad.arc([xx0, top, nx, top + w_bay], 180, 360, fill=col, width=lw)
        ad.line([xx0, top + w_bay * 0.5, nx, top + w_bay * 0.5], fill=(216, 190, 146, int(al * 0.45)), width=1)
        k *= 0.83

    for fy in (1900, 1830, 1770, 1720, 1678, 1642, 1612, 1586):
        ad.line([0, fy, vpx, vpy], fill=(216, 190, 146, 15), width=1)

    # CUTORA 金色双引号暗纹水印
    mark_path = CUTORA_BRAND / "mark_1024.png"
    if mark_path.is_file():
        mk = Image.open(mark_path).convert("RGBA")
        mw = 480
        mh = int(mw * mk.height / mk.width)
        mk = mk.resize((mw, mh), Image.Resampling.LANCZOS)
        r, g, b, alpha = mk.split()
        alpha = alpha.point(lambda p: int(p * 0.05))
        mk.putalpha(alpha)
        arc_lay.alpha_composite(mk, ((W - mw) // 2, 700))

    arc_lay = arc_lay.filter(ImageFilter.GaussianBlur(0.6))
    img = Image.alpha_composite(img.convert("RGBA"), arc_lay).convert("RGB")

    # 边缘暗角 + 有机石材微噪点
    v_top = np.clip((0.12 - y) / 0.12, 0, 1) ** 1.5
    v_bot = np.clip((y - 0.78) / 0.22, 0, 1) ** 1.6
    arr = np.asarray(img).astype(np.float32)
    arr *= 1.0 - (v_bot[..., None] * 0.30 + v_top[..., None] * 0.12)

    rng = np.random.default_rng(7)
    arr += rng.normal(0, 3.2, (H, W))[..., None]
    out_img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")

    out_path = BRAND_DIR / "poster_bg_9x16.png"
    out_img.save(out_path, "PNG")
    return out_path


def _create_medallion_base(ss_size: int = 1536, glow_rgb=(204, 166, 80)) -> Image.Image:
    """在超采样分辨率下生成拉丝香槟金外环 + 深邃暖金星芒圆盘底座。"""
    yy, xx = np.mgrid[0:ss_size, 0:ss_size]
    cx, cy = ss_size / 2.0, ss_size / 2.0
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    r_outer = ss_size * 0.472
    r_mid = ss_size * 0.432
    r_inner = ss_size * 0.412

    angle = np.arctan2(yy - cy, xx - cx)
    sheen = 0.5 + 0.5 * np.cos(angle * 2.0 + 0.85)
    sheen2 = 0.5 + 0.5 * np.sin(angle * 3.0 - 0.4)

    # 外圈拉丝金属
    rim_r = 148 + 86 * sheen
    rim_g = 132 + 76 * sheen
    rim_b = 104 + 58 * sheen

    # 中圈金环切面
    bevel_r = 196 + 52 * sheen2
    bevel_g = 158 + 46 * sheen2
    bevel_b = 82 + 34 * sheen2

    # 内盘深邃暖曜石 + 中心柔光
    norm_d = np.clip(dist / r_inner, 0, 1)
    core_r = 24 * (1 - norm_d * 0.48) + glow_rgb[0] * (1 - norm_d) ** 2.1 * 0.46
    core_g = 20 * (1 - norm_d * 0.48) + glow_rgb[1] * (1 - norm_d) ** 2.1 * 0.46
    core_b = 17 * (1 - norm_d * 0.48) + glow_rgb[2] * (1 - norm_d) ** 2.1 * 0.46

    in_inner = dist <= r_inner
    in_mid = (dist > r_inner) & (dist <= r_mid)

    rgb = np.zeros((ss_size, ss_size, 3), dtype=np.float32)
    rgb[..., 0] = np.where(in_inner, core_r, np.where(in_mid, bevel_r, rim_r))
    rgb[..., 1] = np.where(in_inner, core_g, np.where(in_mid, bevel_g, rim_g))
    rgb[..., 2] = np.where(in_inner, core_b, np.where(in_mid, bevel_b, rim_b))

    alpha = np.clip((r_outer - dist) * 0.8, 0, 1) * 255.0
    rgba = np.dstack([np.clip(rgb, 0, 255).astype(np.uint8), alpha.astype(np.uint8)])
    img = Image.fromarray(rgba, "RGBA")

    draw = ImageDraw.Draw(img)
    # 细微金色星轨弧线
    r_arc = ss_size * 0.365
    draw.arc([cx - r_arc, cy - r_arc, cx + r_arc, cy + r_arc], 200, 340, fill=(238, 206, 132, 120), width=6)
    draw.arc([cx - r_arc, cy - r_arc, cx + r_arc, cy + r_arc], 20, 160, fill=(204, 166, 80, 85), width=5)
    return img


def _draw_gold_label(img: Image.Image, text: str, y_pos: int) -> None:
    """绘制带柔和立体投影的金属金字。"""
    ss_size = img.width
    font = _get_font(int(ss_size * 0.088), bold=True)
    draw = ImageDraw.Draw(img)
    tb = draw.textbbox((0, 0), text, font=font)
    tw = tb[2] - tb[0]
    tx = (ss_size - tw) // 2

    # 阴影层
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    sd.text((tx, y_pos + 10), text, font=font, fill=(0, 0, 0, 190))
    sh = sh.filter(ImageFilter.GaussianBlur(12))
    img.alpha_composite(sh)

    draw = ImageDraw.Draw(img)
    draw.text((tx, y_pos + 3), text, font=font, fill=(140, 102, 38, 255))
    draw.text((tx, y_pos), text, font=font, fill=(246, 222, 162, 255))


def build_table_tool_icon() -> Path:
    """生成 4x 超采样『表格工具』圆形金属徽章图标 (table_tool_me.png)。"""
    ss = 1536
    img = _create_medallion_base(ss, glow_rgb=(212, 172, 84))
    fg = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(fg)

    gx0, gy0, gx1, gy1 = 410, 355, 1126, 915
    draw.rounded_rectangle([gx0, gy0, gx1, gy1], radius=68, fill=(36, 30, 24, 242), outline=(238, 204, 124, 255), width=18)
    draw.rounded_rectangle([gx0 + 12, gy0 + 12, gx1 - 12, gy0 + 158], radius=54, fill=(214, 174, 88, 248))

    for ry in (gy0 + 305, gy0 + 435):
        draw.line([gx0 + 48, ry, gx1 - 48, ry], fill=(214, 174, 88, 175), width=14)
    draw.line([gx0 + 275, gy0 + 165, gx0 + 275, gy1 - 48], fill=(214, 174, 88, 175), width=14)

    # 云端同步圆章
    cx, cy, cr = 1015, 835, 162
    draw.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], fill=(52, 92, 74, 255), outline=(244, 214, 142, 255), width=16)
    draw.polygon(
        [
            (cx, cy - 86),
            (cx - 74, cy - 6),
            (cx - 32, cy - 6),
            (cx - 32, cy + 78),
            (cx + 32, cy + 78),
            (cx + 32, cy - 6),
            (cx + 74, cy - 6),
        ],
        fill=(250, 232, 182, 255),
    )

    # 投影合成
    sh = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    sh.paste((0, 0, 0, 160), (0, 18), fg.split()[3])
    sh = sh.filter(ImageFilter.GaussianBlur(22))
    img.alpha_composite(sh)
    img.alpha_composite(fg)

    _draw_gold_label(img, "表格工具", 1055)
    out_img = img.resize((512, 512), Image.Resampling.LANCZOS)
    out = BRAND_DIR / "table_tool_me.png"
    out_img.save(out, "PNG")
    return out


def build_gateway_icon() -> Path:
    """生成 4x 超采样『算力网关』圆形金属徽章图标 (ltx_gateway_me.png)。"""
    ss = 1536
    img = _create_medallion_base(ss, glow_rgb=(224, 180, 88))
    fg = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(fg)

    cx, cy = ss // 2, 650
    for r, col, w in [(285, (216, 176, 92, 235), 16), (212, (242, 212, 138, 245), 13)]:
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=w)

    pts_outer = []
    for i in range(8):
        ang = i * math.pi / 4.0
        r = 188 if i % 2 == 0 else 82
        pts_outer.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    draw.polygon(pts_outer, fill=(238, 204, 124, 250), outline=(255, 242, 198, 255))
    draw.ellipse([cx - 48, cy - 48, cx + 48, cy + 48], fill=(255, 250, 232, 255))

    for ang_deg in (45, 135, 225, 315):
        rad = math.radians(ang_deg)
        nx, ny = cx + 285 * math.cos(rad), cy + 285 * math.sin(rad)
        draw.ellipse([nx - 32, ny - 32, nx + 32, ny + 32], fill=(246, 220, 156, 255))

    sh = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    sh.paste((0, 0, 0, 160), (0, 18), fg.split()[3])
    sh = sh.filter(ImageFilter.GaussianBlur(22))
    img.alpha_composite(sh)
    img.alpha_composite(fg)

    _draw_gold_label(img, "算力网关", 1055)
    out_img = img.resize((512, 512), Image.Resampling.LANCZOS)
    out = BRAND_DIR / "ltx_gateway_me.png"
    out_img.save(out, "PNG")
    return out


def build_robota_icon() -> Path:
    """将 Robota 官方头像抠除亮白底后，融合进深邃暖金星盘底座 (robota_me.png)。"""
    ss = 1536
    img = _create_medallion_base(ss, glow_rgb=(218, 176, 88))
    if ROBOTA_AVATAR.is_file():
        av = Image.open(ROBOTA_AVATAR).convert("RGBA")
        inner_sz = int(ss * 0.824)
        av = av.resize((inner_sz, inner_sz), Image.Resampling.LANCZOS)
        arr = np.asarray(av).astype(np.float32)
        # 识别外围纯白背景并羽化透明，保留人物主体
        r, g, b, a = arr[..., 0], arr[..., 1], arr[..., 2], arr[..., 3]
        yy, xx = np.mgrid[0:inner_sz, 0:inner_sz]
        dc = np.sqrt(((xx - inner_sz / 2) / (inner_sz / 2)) ** 2 + ((yy - inner_sz / 2) / (inner_sz / 2)) ** 2)
        whiteness = np.minimum(np.minimum(r, g), b)
        # 仅在人物外圈高亮度无色彩处透明化
        sat = np.maximum(np.maximum(r, g), b) - whiteness
        bg_score = np.clip((whiteness - 236.0) / 16.0, 0, 1) * np.clip((18.0 - sat) / 18.0, 0, 1) * np.clip((dc - 0.35) / 0.25, 0, 1)
        new_a = a * (1.0 - bg_score)
        # 圆形内盘裁剪
        circle_mask = np.clip((1.0 - dc) * 40.0, 0, 1)
        arr[..., 3] = np.clip(new_a * circle_mask, 0, 255)
        av_clean = Image.fromarray(arr.astype(np.uint8), "RGBA")
        pos = ((ss - inner_sz) // 2, (ss - inner_sz) // 2 + 12)
        img.alpha_composite(av_clean, pos)

    # 叠加内圈金属金环压边
    draw = ImageDraw.Draw(img)
    r_inner = ss * 0.412
    cx, cy = ss / 2.0, ss / 2.0
    draw.ellipse([cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner], outline=(232, 198, 118, 255), width=16)

    out_img = img.resize((512, 512), Image.Resampling.LANCZOS)
    out = BRAND_DIR / "robota_me.png"
    out_img.save(out, "PNG")
    return out


def build_orante_medallion() -> Path:
    """将 Orante 3D 橙子场记板图标 (orante_me.png) 嵌入统一规格的暖金星盘底座 (orante_medallion.png)。"""
    ss = 1536
    img = _create_medallion_base(ss, glow_rgb=(230, 145, 48))
    src = BRAND_DIR / "orante_me.png"
    if src.is_file():
        ora = Image.open(src).convert("RGBA")
        # 保持宽高比缩放到内盘
        target_h = int(ss * 0.72)
        target_w = int(target_h * ora.width / ora.height)
        ora = ora.resize((target_w, target_h), Image.Resampling.LANCZOS)

        # 柔和底部投影
        sh = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
        px, py = (ss - target_w) // 2, (ss - target_h) // 2 - 10
        sh.paste((0, 0, 0, 175), (px, py + 22), ora.split()[3])
        sh = sh.filter(ImageFilter.GaussianBlur(26))
        img.alpha_composite(sh)
        img.alpha_composite(ora, (px, py))

    draw = ImageDraw.Draw(img)
    r_inner = ss * 0.412
    cx, cy = ss / 2.0, ss / 2.0
    draw.ellipse([cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner], outline=(232, 198, 118, 255), width=16)

    out_img = img.resize((512, 512), Image.Resampling.LANCZOS)
    out = BRAND_DIR / "orante_medallion.png"
    out_img.save(out, "PNG")
    return out


if __name__ == "__main__":
    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    p1 = build_poster_bg_9x16()
    p2 = build_table_tool_icon()
    p3 = build_gateway_icon()
    p4 = build_robota_icon()
    p5 = build_orante_medallion()
    print("Generated:", p1, p2, p3, p4, p5)

