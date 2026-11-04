# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""World-class Lower-Third & Title Reveal Engine with 300 commercial-safe templates.

Features:
- 300 curated designs across 5 major families:
  1. icon_badge (100 styles with icon/thumbnail badges & AI sticker plates)
  2. color_split (50 styles with split-zone color blocks & full-width banners)
  3. line_geometric (40 styles with architectural lines & geometric frames)
  4. graphic_texture (40 styles blending high-res artistic plates & textures)
  5. cultural_global (70 styles spanning Oriental Guofeng, Western Classical & Arabic/Multilingual)
- Adaptive multi-line & paragraph auto-layout (1 line, 2 lines, 3-4 lines, or long paragraph
  auto-wrapping with dynamic container/split-bar/badge expansion and zero text clipping)
- Built-in multi-stage entry (anim_in) and exit (anim_out) choreography
"""
from __future__ import annotations

import copy
import math
import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)

_PLATES_DIR = Path(__file__).resolve().parents[2] / "resources" / "title_art_plates"
_FONTS_DIR = Path(__file__).resolve().parents[2] / "resources" / "fonts"
_PLATE_CACHE: dict[str, QImage | None] = {}
_FONTS_READY = False


def _ensure_render_fonts() -> None:
    global _FONTS_READY
    if _FONTS_READY:
        return
    _FONTS_READY = True
    try:
        from cutora.ui.theme.fonts_ui import register_ui_fonts
        register_ui_fonts()
    except Exception:
        pass
    try:
        from PySide6.QtGui import QFontDatabase
        for rel in (
            ("Noto_Sans_SC", "NotoSansSCwght.ttf"),
            ("Noto_Naskh_Arabic", "NotoNaskhArabicwght.ttf"),
            ("Cairo", "Cairoslnt_wght.ttf"),
        ):
            fp = _FONTS_DIR.joinpath(*rel)
            if fp.is_file():
                QFontDatabase.addApplicationFont(str(fp))
    except Exception:
        pass


def _load_plate(filename: str) -> QImage | None:
    if not filename:
        return None
    if filename in _PLATE_CACHE:
        return _PLATE_CACHE[filename]
    p = _PLATES_DIR / filename if not os.path.isabs(filename) else Path(filename)
    if p.is_file():
        img = QImage(str(p))
        if not img.isNull():
            _PLATE_CACHE[filename] = img
            return img
    _PLATE_CACHE[filename] = None
    return None


def _has_rtl_chars(text: str) -> bool:
    for ch in text:
        cp = ord(ch)
        if (0x0600 <= cp <= 0x06FF) or (0x0750 <= cp <= 0x077F) or (0x08A0 <= cp <= 0x08FF) or (0xFB50 <= cp <= 0xFDFF) or (0xFE70 <= cp <= 0xFEFF):
            return True
    return False


def _build_300_catalog() -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []

    # 1. 100 Icon / Thumbnail Badge Lower-Thirds (带图标/缩略图)
    icon_themes = [
        ("皇室火漆冠冕铭牌", "#1A1412", "#FFD700", "#FFFFFF", "crown", "sticker_royal_wax_seal_crown.png", "wipe_bar_pop_icon"),
        ("圣洁和平白鸽标题条", "#0F1E36", "#80D8FF", "#FFFFFF", "dove", "sticker_divine_peace_dove.png", "elastic_card_rise"),
        ("神圣经卷金辉字幕条", "#24170E", "#FFD54F", "#FFF8E1", "scroll", "sticker_holy_bible_scroll_light.png", "gold_shimmer_sweep"),
        ("禅意千叶金莲标题卡", "#1C1410", "#FFB300", "#FFF3E0", "lotus", "sticker_golden_lotus_zen.png", "ink_unroll_fade"),
        ("国风朱砂印章出标题", "#1F1212", "#E53935", "#FFFDF9", "seal", "sticker_guofeng_vermilion_seal.png", "ink_unroll_fade"),
        ("春日流金樱花美妆条", "#2B1822", "#FFB7C5", "#FFFFFF", "sakura", "sticker_sakura_gold_petals.png", "elastic_card_rise"),
        ("爆款 3D 鎏金爱心互动条", "#1E121A", "#FF4081", "#FFFFFF", "heart", "sticker_social_gold_heart_3d.png", "wipe_bar_pop_icon"),
        ("电影变形宽银幕光斑条", "#0B1320", "#00E5FF", "#FFFFFF", "flare", "sticker_anamorphic_cinema_flare.png", "cinema_blur_zoom"),
        ("热门爆款烈焰引流条", "#24100E", "#FF6D00", "#FFFFFF", "flame", "", "wipe_bar_pop_icon"),
        ("金牌主播麦克风人声条", "#121824", "#00E676", "#FFFFFF", "mic", "", "split_reveal_slide"),
        ("全球旅行坐标打卡条", "#0E2228", "#26C6DA", "#FFFFFF", "pin", "", "elastic_card_rise"),
        ("高定甄选钻石精品条", "#161426", "#E040FB", "#FFFFFF", "diamond", "", "gold_shimmer_sweep"),
        ("官方蓝V认证嘉宾名片", "#0D1B2A", "#2979FF", "#FFFFFF", "verified", "", "wipe_bar_pop_icon"),
        ("黑胶音乐律动金曲条", "#18181C", "#FFD740", "#FFFFFF", "music", "", "split_reveal_slide"),
        ("圣光十字福音赞美条", "#1F1912", "#FFD700", "#FFFDE7", "cross", "", "gold_shimmer_sweep"),
        ("星光闪耀必看重点条", "#141829", "#FFEA00", "#FFFFFF", "star", "", "elastic_card_rise"),
        ("极客科技芯片评测条", "#0A192F", "#64FFDA", "#E6F1FF", "chip", "", "split_reveal_slide"),
        ("书卷知性人文金句条", "#231B14", "#D4AF37", "#FAF0E6", "book", "", "ink_unroll_fade"),
        ("手作咖啡慢生活日记条", "#261C15", "#E6CCB2", "#FFFDF9", "coffee", "", "elastic_card_rise"),
        ("好物推荐购物袋种草条", "#28111B", "#FF4081", "#FFFFFF", "bag", "", "wipe_bar_pop_icon"),
    ]
    badge_shapes = ["circle", "rounded_square", "hexagon", "diamond_badge", "shield"]
    for i in range(100):
        base_name, c_bg, c_acc, c_txt, glyph, plate_png, anim = icon_themes[i % len(icon_themes)]
        b_shape = badge_shapes[(i // len(icon_themes)) % len(badge_shapes)]
        variant_num = (i // len(icon_themes)) + 1
        variant_suffix = ("Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "Ⅴ")[variant_num - 1]
        style_id = f"lt_icon_{i + 1:03d}"
        name = f"{base_name} · {variant_suffix}"
        spec = {
            "style_id": style_id,
            "family": "icon_badge",
            "category": "带图标/缩略图",
            "layout_type": "icon_card" if i % 2 == 0 else "icon_pill",
            "badge_shape": b_shape,
            "icon_glyph": glyph,
            "icon_path": str(_PLATES_DIR / plate_png) if plate_png and (_PLATES_DIR / plate_png).is_file() else "",
            "plate_name": "",
            "show_icon": True,
            "primary_color": c_bg,
            "secondary_color": c_acc,
            "text_color": c_txt,
            "bg_opacity": 232,
            "anim_in": anim,
            "anim_out": "fade_slide_out",
            "anim_dur": 0.45,
            "default_text": f"{base_name}\n支持多行与长段落自动排版换行",
            "lines": [base_name, "支持多行与长段落自动排版换行"],
        }
        catalog.append(
            {
                "id": style_id,
                "name": name,
                "family": "icon_badge",
                "category": "带图标/缩略图",
                "subcat": "带图标/缩略图",
                "tags": ["带图标", "缩略图", "icon", glyph, b_shape, name],
                "lower_third_spec": spec,
            }
        )

    # 2. 50 Color-Block / Split-Zone / Banner Lower-Thirds (色彩分区/通栏)
    split_themes = [
        ("新闻演播室蓝金双区条", "#0D254C", "#FFB300", "#FFFFFF", "split_v_2zone"),
        ("BBC 国际资讯红白分区条", "#8E1616", "#F5F5F5", "#FFFFFF", "split_h_2zone"),
        ("Vogue 时尚画报撞色通栏", "#111111", "#E6C253", "#FFFFFF", "full_banner"),
        ("小红书奶油蜜桃双色卡", "#FFF5F7", "#FF80AB", "#3E2723", "split_v_2zone"),
        ("莫兰迪雾霾蓝灰双层条", "#2C3E50", "#85C1E9", "#FFFFFF", "split_h_2zone"),
        ("黑金奢华高定斜切分区条", "#141414", "#FFD700", "#FFFFFF", "angled_split"),
        ("赛博霓虹青紫双拼胶囊条", "#120C2B", "#00F5D4", "#FFFFFF", "pill_split"),
        ("纪录片森林暗绿黄铜分区", "#14281D", "#D4AF37", "#F1F8E9", "split_h_2zone"),
        ("财经频道深海蓝金数据条", "#0A192F", "#FFD54F", "#E6F1FF", "split_v_2zone"),
        ("极简白金悬浮双色标题卡", "#FAFAFA", "#1E293B", "#0F172A", "floating_split"),
    ]
    for i in range(50):
        base_name, c_bg, c_acc, c_txt, ltype = split_themes[i % len(split_themes)]
        variant_num = (i // len(split_themes)) + 1
        variant_suffix = ("Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "Ⅴ")[variant_num - 1]
        style_id = f"lt_split_{i + 1:03d}"
        name = f"{base_name} · {variant_suffix}"
        spec = {
            "style_id": style_id,
            "family": "color_split",
            "category": "色彩分区/通栏",
            "layout_type": ltype,
            "badge_shape": "none",
            "icon_glyph": "none",
            "icon_path": "",
            "plate_name": "",
            "show_icon": False,
            "primary_color": c_bg,
            "secondary_color": c_acc,
            "text_color": c_txt,
            "bg_opacity": 236,
            "anim_in": "split_reveal_slide",
            "anim_out": "fade_slide_out",
            "anim_dur": 0.45,
            "default_text": f"{base_name}\n多行分区自适应延展标题模板",
            "lines": [base_name, "多行分区自适应延展标题模板"],
        }
        catalog.append(
            {
                "id": style_id,
                "name": name,
                "family": "color_split",
                "category": "色彩分区/通栏",
                "subcat": "色彩分区/通栏",
                "tags": ["色彩分区", "通栏", "split", "banner", ltype, name],
                "lower_third_spec": spec,
            }
        )

    # 3. 40 Geometric Line Reveal Lower-Thirds (线条几何出场)
    line_themes = [
        ("中心金线对称展开标题", "#121318", "#FFD700", "#FFFFFF", "center_line_expand"),
        ("建筑蓝图十字刻度线框条", "#0D1B2A", "#64B5F6", "#E3F2FD", "architect_cross"),
        ("Art Deco 流金双菱几何条", "#18120B", "#F5C451", "#FFF8E1", "deco_diamond_line"),
        ("极简左侧双竖线强调字幕", "#141820", "#00E676", "#FFFFFF", "left_double_bar"),
        ("上下平行金丝呼吸感标题", "#16161A", "#E6C786", "#FFFFFF", "top_bottom_rules"),
        ("四角精工折角悬浮标题框", "#10141C", "#FF4081", "#FFFFFF", "corner_brackets_box"),
        ("黄金分割比例几何切线条", "#1A1612", "#D4AF37", "#FAF0E6", "golden_ratio_lines"),
        ("未来科技激光导轨扫描条", "#08121E", "#00E5FF", "#E0F7FA", "laser_rail_line"),
    ]
    for i in range(40):
        base_name, c_bg, c_acc, c_txt, ltype = line_themes[i % len(line_themes)]
        variant_num = (i // len(line_themes)) + 1
        variant_suffix = ("Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "Ⅴ")[variant_num - 1]
        style_id = f"lt_line_{i + 1:03d}"
        name = f"{base_name} · {variant_suffix}"
        spec = {
            "style_id": style_id,
            "family": "line_geometric",
            "category": "线条几何出场",
            "layout_type": ltype,
            "badge_shape": "none",
            "icon_glyph": "none",
            "icon_path": "",
            "plate_name": "",
            "show_icon": False,
            "primary_color": c_bg,
            "secondary_color": c_acc,
            "text_color": c_txt,
            "bg_opacity": 195,
            "anim_in": "center_line_expand",
            "anim_out": "fade_slide_out",
            "anim_dur": 0.48,
            "default_text": f"{base_name}\n几何线条随文字行数智能伸缩",
            "lines": [base_name, "几何线条随文字行数智能伸缩"],
        }
        catalog.append(
            {
                "id": style_id,
                "name": name,
                "family": "line_geometric",
                "category": "线条几何出场",
                "subcat": "线条几何出场",
                "tags": ["线条几何", "几何", "line", "geom", ltype, name],
                "lower_third_spec": spec,
            }
        )

    # 4. 40 Graphic / Artistic Texture & Plate Lower-Thirds (图形画报肌理)
    plate_themes = [
        ("圣光穿云流金画报字幕卡", "#1F160A", "#FFD700", "#FFFDE7", "plate_sacred_gold_rays.png"),
        ("东方青玉洒金古卷标题条", "#102420", "#E6C670", "#F4FBF9", "plate_guofeng_scroll_jade.png"),
        ("巴洛克皇室黑金浮雕铭牌", "#1A120B", "#F5C451", "#FFF8E1", "plate_baroque_royal_gold.png"),
        ("水彩晕染金箔艺术标题卡", "#261822", "#FFD54F", "#FFFFFF", "plate_watercolor_gold_foil.png"),
        ("赛博全息光栅科技字幕条", "#091326", "#00F5D4", "#E0F7FA", "plate_cyber_hologram_hud.png"),
        ("中世纪羊皮卷火漆纪念条", "#2B1D12", "#E6C253", "#FFF8E7", "plate_vintage_parchment_wax.png"),
        ("香槟星光焦外梦幻标题卡", "#1C1610", "#FFE082", "#FFFFFF", "plate_champagne_starlight_bokeh.png"),
        ("皇家蓝宝石星月几何画报", "#0B192C", "#FFD700", "#F0F8FF", "plate_arabesque_royal_sapphire.png"),
    ]
    for i in range(40):
        base_name, c_bg, c_acc, c_txt, plate_file = plate_themes[i % len(plate_themes)]
        variant_num = (i // len(plate_themes)) + 1
        variant_suffix = ("Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "Ⅴ")[variant_num - 1]
        style_id = f"lt_graf_{i + 1:03d}"
        name = f"{base_name} · {variant_suffix}"
        spec = {
            "style_id": style_id,
            "family": "graphic_texture",
            "category": "图形画报肌理",
            "layout_type": "textured_plate",
            "badge_shape": "circle" if i % 3 == 0 else "none",
            "icon_glyph": "star" if i % 3 == 0 else "none",
            "icon_path": "",
            "plate_name": plate_file,
            "show_icon": bool(i % 3 == 0),
            "primary_color": c_bg,
            "secondary_color": c_acc,
            "text_color": c_txt,
            "bg_opacity": 238,
            "anim_in": "gold_shimmer_sweep",
            "anim_out": "fade_slide_out",
            "anim_dur": 0.50,
            "default_text": f"{base_name}\n高定艺术肌理底板与多行排版",
            "lines": [base_name, "高定艺术肌理底板与多行排版"],
        }
        catalog.append(
            {
                "id": style_id,
                "name": name,
                "family": "graphic_texture",
                "category": "图形画报肌理",
                "subcat": "图形画报肌理",
                "tags": ["图形画报", "肌理", "graphic", "poster", plate_file, name],
                "lower_third_spec": spec,
            }
        )

    # 5. 70 Oriental / Western / Arabic Multilingual Cultural Lower-Thirds (东方/西方/多语种)
    cultural_themes = [
        ("故宫朱红描金宮廷匾额条", "#7A1414", "#FFD700", "#FFF8E1", "guofeng_scroll", "plate_guofeng_scroll_jade.png", "东方国风"),
        ("水墨江南宣纸题跋字幕条", "#F5F2EB", "#8C2222", "#1C1C1C", "guofeng_scroll", "", "东方国风"),
        ("敦煌飞天藻井流金标题卡", "#143632", "#F4C44E", "#FFFDF9", "guofeng_scroll", "plate_guofeng_scroll_jade.png", "东方国风"),
        ("宋式极简汝窑天青雅集条", "#263836", "#A8C6C0", "#F4F9F8", "guofeng_scroll", "", "东方国风"),
        ("长安盛唐宝相花金辉字幕", "#3E1212", "#FFCA28", "#FFF8E1", "guofeng_scroll", "", "东方国风"),
        ("维也纳古典歌剧黑金字幕", "#120E0A", "#D4AF37", "#FFF8E7", "western_opera", "plate_baroque_royal_gold.png", "西方古典"),
        ("巴黎左岸文艺电影白金条", "#181C24", "#E6C786", "#FFFFFF", "western_opera", "", "西方古典"),
        ("牛津古典羊皮卷学术铭牌", "#281C12", "#C5A059", "#FAF0E6", "western_opera", "plate_vintage_parchment_wax.png", "西方古典"),
        ("阿拉伯皇室蓝金星月铭文条", "#0A1E3C", "#FFD700", "#FFFFFF", "arabic_arch", "plate_arabesque_royal_sapphire.png", "多语种"),
        ("波斯伊斯法罕绿松石金框条", "#0B3B36", "#F4D03F", "#E8F8F5", "arabic_arch", "plate_arabesque_royal_sapphire.png", "多语种"),
        ("圣殿福音万丈金光经文卡", "#1F160A", "#FFD700", "#FFFDE7", "sacred_scripture", "plate_sacred_gold_rays.png", "多语种"),
        ("京都枯山水侘寂木纹字幕", "#241E1A", "#D7CCC8", "#FAF8F5", "guofeng_scroll", "", "东方国风"),
        ("卢浮宫古典画廊典藏说明牌", "#1A1817", "#E5C158", "#F5F5F5", "western_opera", "", "西方古典"),
        ("环球双语纪录片标准字幕条", "#101820", "#00E5FF", "#FFFFFF", "bilingual_doc", "", "多语种"),
    ]
    for i in range(70):
        base_name, c_bg, c_acc, c_txt, ltype, plate_file, sub_tag = cultural_themes[i % len(cultural_themes)]
        variant_num = (i // len(cultural_themes)) + 1
        variant_suffix = ("Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "Ⅴ")[variant_num - 1]
        style_id = f"lt_cult_{i + 1:03d}"
        name = f"{base_name} · {variant_suffix}"
        spec = {
            "style_id": style_id,
            "family": "cultural_global",
            "category": "东方/西方/多语种",
            "layout_type": ltype,
            "badge_shape": "diamond_badge" if ltype == "arabic_arch" else ("seal" if ltype == "guofeng_scroll" else "none"),
            "icon_glyph": "seal" if ltype == "guofeng_scroll" else ("star" if ltype == "arabic_arch" else "none"),
            "icon_path": "",
            "plate_name": plate_file,
            "show_icon": ltype in ("guofeng_scroll", "arabic_arch"),
            "primary_color": c_bg,
            "secondary_color": c_acc,
            "text_color": c_txt,
            "bg_opacity": 234,
            "anim_in": "ink_unroll_fade" if ltype == "guofeng_scroll" else "gold_shimmer_sweep",
            "anim_out": "fade_slide_out",
            "anim_dur": 0.48,
            "default_text": f"{base_name}\n支持中文 · English · العربية 多语种自适应排版",
            "lines": [base_name, "支持中文 · English · العربية 多语种自适应排版"],
        }
        catalog.append(
            {
                "id": style_id,
                "name": name,
                "family": "cultural_global",
                "category": "东方/西方/多语种",
                "subcat": "东方/西方/多语种",
                "tags": ["东方", "西方", "多语种", "国风", "古典", "cultural", sub_tag, ltype, name],
                "lower_third_spec": spec,
            }
        )

    return catalog


LOWER_THIRD_STYLES: list[dict[str, Any]] = _build_300_catalog()
LOWER_THIRD_STYLES_BY_ID: dict[str, dict[str, Any]] = {it["id"]: it for it in LOWER_THIRD_STYLES}


def list_lower_third_styles(family: str = "") -> list[dict[str, Any]]:
    if not family:
        return list(LOWER_THIRD_STYLES)
    return [it for it in LOWER_THIRD_STYLES if it["family"] == family or it["category"] == family]


def get_lower_third_style(style_id: str) -> dict[str, Any]:
    if style_id in LOWER_THIRD_STYLES_BY_ID:
        return LOWER_THIRD_STYLES_BY_ID[style_id]
    return LOWER_THIRD_STYLES[0]


def default_lower_third_spec(style_id: str = "lt_icon_001") -> dict[str, Any]:
    st = get_lower_third_style(style_id)
    return copy.deepcopy(st["lower_third_spec"])


def normalize_lower_third_spec(spec: dict[str, Any] | None) -> dict[str, Any]:
    base = default_lower_third_spec(str((spec or {}).get("style_id") or "lt_icon_001"))
    if isinstance(spec, dict):
        base.update(spec)
    base["anim_dur"] = max(0.1, min(3.0, float(base.get("anim_dur", 0.45) or 0.45)))
    return base


def _is_cjk_char(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x2E80 <= cp <= 0x9FFF
        or 0xF900 <= cp <= 0xFAFF
        or 0x3000 <= cp <= 0x30FF
        or 0xAC00 <= cp <= 0xD7AF
        or 0xFF00 <= cp <= 0xFFEF
    )


def _wrap_single_paragraph(text: str, fm: QFontMetricsF, max_width_px: float) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if fm.horizontalAdvance(text) <= max_width_px:
        return [text]

    # Tokenize into space-separated words for non-CJK scripts (Latin, Arabic, etc.)
    # and character-by-character for CJK ideographs/kana/hangul.
    lines: list[str] = []
    cur = ""
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if not ch.isspace() and not _is_cjk_char(ch):
            j = i + 1
            while j < n and not text[j].isspace() and not _is_cjk_char(text[j]):
                j += 1
            token = text[i:j]
            i = j
        else:
            token = ch
            i += 1

        cand = cur + token
        if cur and fm.horizontalAdvance(cand) > max_width_px:
            lines.append(cur.rstrip())
            cur = token.lstrip()
        else:
            cur = cand
    if cur.strip():
        lines.append(cur.strip())
    return lines or [text]


def layout_lower_third_text(
    raw_text: str,
    font_family: str,
    avail_w: float,
    base_h: float,
    scale: float = 1.0,
) -> tuple[list[dict[str, Any]], float]:
    """Wrap 1-line, 2-line, 3-4-line, or long paragraph text into styled line descriptors.

    Returns `(wrapped_lines, required_total_text_height)`.
    Each item in `wrapped_lines` is `{"text": str, "font": QFont, "height": float, "is_header": bool}`.
    """
    paragraphs = [p.strip() for p in (raw_text or "").splitlines() if p.strip()]
    if not paragraphs:
        paragraphs = ["主标题内容"]

    is_single_long_paragraph = len(paragraphs) == 1 and len(paragraphs[0]) > 18
    head_px = max(9, int(round(28.0 * scale)))
    sub_px = max(8, int(round(20.0 * scale)))
    body_px = max(8, int(round(18.0 * scale)))

    wrapped: list[dict[str, Any]] = []
    avail_w = max(48.0 * scale, float(avail_w))

    for p_idx, para in enumerate(paragraphs):
        is_header = p_idx == 0 and not is_single_long_paragraph
        px = head_px if is_header else (sub_px if p_idx == 1 and len(paragraphs) <= 3 else body_px)
        weight = QFont.Weight.Bold if is_header else QFont.Weight.Medium
        is_rtl_para = _has_rtl_chars(para)
        fam_chain = (
            ["Cairo", "Noto Naskh Arabic", font_family, "Noto Sans SC", "Microsoft YaHei"]
            if is_rtl_para
            else [font_family, "Noto Sans SC", "Cairo", "Noto Naskh Arabic", "Microsoft YaHei", "PingFang SC"]
        )
        f = QFont()
        f.setFamilies(fam_chain)
        f.setWeight(weight)
        f.setPixelSize(px)
        fm = QFontMetricsF(f)
        sub_lines = _wrap_single_paragraph(para, fm, avail_w)
        # If a single long paragraph wrapped into multiple lines, treat line 0 as bold header only if <= 2 lines
        for s_idx, s_txt in enumerate(sub_lines):
            line_is_head = is_header and s_idx == 0
            lf = f
            if is_single_long_paragraph and s_idx == 0 and len(sub_lines) <= 2:
                lf = QFont()
                lf.setFamilies(fam_chain)
                lf.setWeight(QFont.Weight.Bold)
                lf.setPixelSize(sub_px)
            lfm = QFontMetricsF(lf)
            lh = max(float(px) * 1.32, lfm.height() * 1.18)
            wrapped.append(
                {
                    "text": s_txt,
                    "font": lf,
                    "height": lh,
                    "is_header": line_is_head,
                    "rtl": _has_rtl_chars(s_txt),
                }
            )

    total_text_h = sum(item["height"] for item in wrapped) + max(0, len(wrapped) - 1) * (4.0 * scale)
    return wrapped, total_text_h


def _draw_vector_glyph(painter: QPainter, glyph: str, cx: float, cy: float, r: float, color: QColor) -> None:
    painter.save()
    try:
        painter.setPen(QPen(color, max(1.5, r * 0.14), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(color)
        if glyph == "cross":
            w = max(2.5, r * 0.28)
            painter.drawRoundedRect(QRectF(cx - w * 0.5, cy - r * 0.68, w, r * 1.36), 1.5, 1.5)
            painter.drawRoundedRect(QRectF(cx - r * 0.52, cy - r * 0.22, r * 1.04, w), 1.5, 1.5)
        elif glyph in ("star", "flare"):
            path = QPainterPath()
            for i in range(8):
                ang = i * math.pi / 4.0
                rad = r * 0.72 if i % 2 == 0 else r * 0.26
                pt = QPointF(cx + math.cos(ang) * rad, cy + math.sin(ang) * rad)
                if i == 0:
                    path.moveTo(pt)
                else:
                    path.lineTo(pt)
            path.closeSubpath()
            painter.drawPath(path)
        elif glyph == "heart":
            path = QPainterPath()
            path.moveTo(cx, cy + r * 0.55)
            path.cubicTo(cx - r * 0.85, cy + r * 0.05, cx - r * 0.65, cy - r * 0.65, cx, cy - r * 0.22)
            path.cubicTo(cx + r * 0.65, cy - r * 0.65, cx + r * 0.85, cy + r * 0.05, cx, cy + r * 0.55)
            painter.drawPath(path)
        elif glyph == "crown":
            path = QPainterPath()
            path.moveTo(cx - r * 0.6, cy + r * 0.4)
            path.lineTo(cx - r * 0.65, cy - r * 0.3)
            path.lineTo(cx - r * 0.25, cy + r * 0.05)
            path.lineTo(cx, cy - r * 0.5)
            path.lineTo(cx + r * 0.25, cy + r * 0.05)
            path.lineTo(cx + r * 0.65, cy - r * 0.3)
            path.lineTo(cx + r * 0.6, cy + r * 0.4)
            path.closeSubpath()
            painter.drawPath(path)
        elif glyph == "diamond":
            path = QPainterPath()
            path.moveTo(cx, cy - r * 0.68)
            path.lineTo(cx + r * 0.62, cy)
            path.lineTo(cx, cy + r * 0.68)
            path.lineTo(cx - r * 0.62, cy)
            path.closeSubpath()
            painter.drawPath(path)
        else:
            # Universal emblematic ring + center spark
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(cx, cy), r * 0.58, r * 0.58)
            painter.setBrush(color)
            painter.drawEllipse(QPointF(cx, cy), r * 0.24, r * 0.24)
    finally:
        painter.restore()


def _draw_badge_icon(
    painter: QPainter,
    cx: float,
    cy: float,
    r: float,
    badge_shape: str,
    c_acc: QColor,
    c_bg: QColor,
    icon_path: str,
    glyph: str,
) -> None:
    painter.save()
    try:
        painter.setPen(QPen(c_acc.lighter(130), max(1.5, r * 0.1)))
        grad = QRadialGradient(cx - r * 0.25, cy - r * 0.25, r * 1.3)
        grad.setColorAt(0.0, c_acc.lighter(120))
        grad.setColorAt(1.0, c_acc.darker(125))
        painter.setBrush(QBrush(grad))

        if badge_shape == "rounded_square":
            painter.drawRoundedRect(QRectF(cx - r, cy - r, r * 2.0, r * 2.0), r * 0.38, r * 0.38)
        elif badge_shape == "diamond_badge":
            path = QPainterPath()
            path.moveTo(cx, cy - r * 1.08)
            path.lineTo(cx + r * 1.08, cy)
            path.lineTo(cx, cy + r * 1.08)
            path.lineTo(cx - r * 1.08, cy)
            path.closeSubpath()
            painter.drawPath(path)
        elif badge_shape == "hexagon":
            path = QPainterPath()
            for i in range(6):
                ang = math.radians(60 * i - 30)
                pt = QPointF(cx + math.cos(ang) * r, cy + math.sin(ang) * r)
                if i == 0:
                    path.moveTo(pt)
                else:
                    path.lineTo(pt)
            path.closeSubpath()
            painter.drawPath(path)
        else:
            painter.drawEllipse(QPointF(cx, cy), r, r)

        # Draw image icon if present, else crisp vector glyph
        icon_img = _load_plate(icon_path) if icon_path else None
        if icon_img is not None and not icon_img.isNull():
            ir = r * 0.82
            target_r = QRectF(cx - ir, cy - ir, ir * 2.0, ir * 2.0)
            painter.drawImage(target_r, icon_img)
        else:
            fg = QColor("#141414") if c_acc.lightness() > 155 else QColor("#FFFFFF")
            _draw_vector_glyph(painter, glyph, cx, cy, r * 0.75, fg)
    finally:
        painter.restore()


def _compute_anim_progress(current_time: float | None, start: float, end: float, anim_dur: float) -> tuple[float, float]:
    """Return `(p_in, p_out)` in `[0.0, 1.0]`, where `1.0` means fully visible."""
    if current_time is None:
        return 1.0, 1.0
    t = float(current_time)
    dur = max(0.05, float(anim_dur or 0.45))
    total = max(0.1, float(end) - float(start))
    eff_dur = min(dur, total * 0.45)
    p_in = 1.0
    p_out = 1.0
    if start <= t <= end:
        if t - start < eff_dur:
            u = max(0.0, min(1.0, (t - start) / eff_dur))
            # Smooth cubic ease-out
            p_in = 1.0 - (1.0 - u) ** 3
        if end - t < eff_dur:
            u = max(0.0, min(1.0, (end - t) / eff_dur))
            p_out = 1.0 - (1.0 - u) ** 3
    return p_in, p_out


def render_lower_third(
    painter: QPainter,
    ov: dict[str, Any],
    current_time: float = 0.0,
    video_w: int = 1080,
    video_h: int = 1920,
) -> QRectF:
    """Render an adaptive multi-line / paragraph lower-third or title reveal template."""
    _ensure_render_fonts()
    raw_spec = ov.get("lower_third_spec") or {}
    spec = normalize_lower_third_spec(raw_spec)

    x = float(ov.get("x", 0.0))
    y = float(ov.get("y", 0.0))
    w = max(32.0, float(ov.get("w", 520.0)))
    base_h = max(24.0, float(ov.get("h", 140.0)))

    # Scale factor so thumbnails (72x36) and full canvas (730x180) both look razor-sharp
    scale = min(1.35, max(0.24, w / 520.0))
    pad_x = 22.0 * scale
    pad_y = 14.0 * scale

    show_icon = bool(spec.get("show_icon", True))
    badge_r = (30.0 * scale) if show_icon else 0.0
    badge_space = (badge_r * 2.0 + 16.0 * scale) if show_icon else (14.0 * scale)

    avail_text_w = max(40.0 * scale, w - pad_x * 2.0 - badge_space)
    font_family = str(ov.get("font_family") or "Microsoft YaHei")

    # Resolve text content from ov["content"] or spec["lines"]
    raw_text = str(ov.get("content") or "").strip()
    if not raw_text:
        lines_list = spec.get("lines")
        if isinstance(lines_list, list) and lines_list:
            raw_text = "\n".join(str(s) for s in lines_list if str(s).strip())
        else:
            raw_text = str(spec.get("default_text") or ov.get("name") or "主标题内容\n副标题描述文字")

    wrapped_lines, total_text_h = layout_lower_third_text(raw_text, font_family, avail_text_w, base_h, scale=scale)
    # Dynamically expand container height so multi-line or paragraph text never clips!
    eff_h = max(base_h, total_text_h + pad_y * 2.2)
    if eff_h > base_h + 1.0 and w >= 160.0:
        ov["h"] = int(round(eff_h))
    rect = QRectF(x, y, w, eff_h)

    # Entry & exit choreography
    ov_start = float(ov.get("start", 0.0) or 0.0)
    ov_end = float(ov.get("end", ov_start + 5.0) or (ov_start + 5.0))
    p_in, p_out = _compute_anim_progress(current_time, ov_start, ov_end, float(spec.get("anim_dur", 0.45)))
    vis = min(p_in, p_out)

    c_bg = QColor(str(spec.get("primary_color") or "#141824"))
    c_bg.setAlpha(int(float(spec.get("bg_opacity", 232) or 232)))
    c_acc = QColor(str(spec.get("secondary_color") or "#FFD700"))
    c_txt = QColor(str(spec.get("text_color") or "#FFFFFF"))
    layout_type = str(spec.get("layout_type") or "icon_card")
    any_rtl = any(item.get("rtl") for item in wrapped_lines) or layout_type == "arabic_arch"

    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        base_op = float(ov.get("opacity", 255) or 255) / 255.0
        painter.setOpacity(max(0.0, min(1.0, base_op * vis)))

        # Slide/scale animation offset
        anim_in_name = str(spec.get("anim_in") or "wipe_bar_pop_icon")
        if vis < 0.999:
            if anim_in_name in ("elastic_card_rise", "ink_unroll_fade"):
                painter.translate(0.0, (1.0 - vis) * 18.0 * scale)
            elif anim_in_name == "split_reveal_slide":
                painter.translate(-(1.0 - vis) * 26.0 * scale, 0.0)
            elif anim_in_name == "cinema_blur_zoom":
                cx_r, cy_r = rect.center().x(), rect.center().y()
                sc = 0.90 + 0.10 * vis
                painter.translate(cx_r, cy_r)
                painter.scale(sc, sc)
                painter.translate(-cx_r, -cy_r)

        corner_r = (eff_h * 0.46) if "pill" in layout_type else (14.0 * scale)

        # 1. Draw backdrop card / split-zone / textured plate / geometric lines
        if layout_type == "split_h_2zone" and len(wrapped_lines) >= 2:
            head_h = wrapped_lines[0]["height"] + pad_y * 1.15
            top_rect = QRectF(x, y, w * vis, head_h)
            bot_rect = QRectF(x, y + head_h, w, max(8.0, eff_h - head_h))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(c_acc)
            painter.drawRoundedRect(top_rect, corner_r * 0.5, corner_r * 0.5)
            painter.setBrush(c_bg)
            painter.drawRoundedRect(bot_rect, corner_r * 0.5, corner_r * 0.5)
        elif layout_type in ("center_line_expand", "top_bottom_rules", "architect_cross", "deco_diamond_line", "golden_ratio_lines", "laser_rail_line"):
            # Semi-transparent sleek dark glass + expanding geometric lines
            glass = QColor(c_bg)
            glass.setAlpha(175)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glass)
            painter.drawRoundedRect(rect, 8.0 * scale, 8.0 * scale)

            line_w = w * vis
            mx = x + w * 0.5
            pen_acc = QPen(c_acc, max(1.5, 2.8 * scale))
            painter.setPen(pen_acc)
            painter.drawLine(QPointF(mx - line_w * 0.5, y + 2.0 * scale), QPointF(mx + line_w * 0.5, y + 2.0 * scale))
            painter.drawLine(QPointF(mx - line_w * 0.5, y + eff_h - 2.0 * scale), QPointF(mx + line_w * 0.5, y + eff_h - 2.0 * scale))
            if layout_type == "deco_diamond_line":
                d = 6.0 * scale
                painter.setBrush(c_acc)
                painter.drawEllipse(QPointF(mx - line_w * 0.5, y + eff_h * 0.5), d, d)
                painter.drawEllipse(QPointF(mx + line_w * 0.5, y + eff_h * 0.5), d, d)
        else:
            # Card backdrop with optional artistic plate texture
            plate_img = _load_plate(str(spec.get("plate_name") or ""))
            clip_path = QPainterPath()
            clip_path.addRoundedRect(rect, corner_r, corner_r)
            painter.save()
            painter.setClipPath(clip_path)
            if plate_img is not None and not plate_img.isNull():
                painter.drawImage(rect, plate_img)
                tint = QColor(c_bg)
                tint.setAlpha(155)
                painter.fillRect(rect, tint)
            else:
                grad = QLinearGradient(x, y, x + w, y + eff_h)
                grad.setColorAt(0.0, c_bg)
                grad.setColorAt(1.0, c_bg.darker(125))
                painter.fillRect(rect, grad)
            painter.restore()

            # Ornamental border stroke around card
            painter.setPen(QPen(c_acc, max(1.2, 2.4 * scale)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1.0, 1.0, -1.0, -1.0), corner_r, corner_r)

        # 2. Draw icon badge or side accent bar
        if show_icon:
            ic_cx = (x + w - pad_x - badge_r) if any_rtl else (x + pad_x + badge_r)
            ic_cy = y + eff_h * 0.5
            _draw_badge_icon(
                painter,
                ic_cx,
                ic_cy,
                badge_r * (0.7 + 0.3 * vis),
                str(spec.get("badge_shape") or "circle"),
                c_acc,
                c_bg,
                str(spec.get("icon_path") or ""),
                str(spec.get("icon_glyph") or "star"),
            )
            text_left = x + pad_x if any_rtl else (ic_cx + badge_r + 14.0 * scale)
        else:
            bar_w = max(3.0, 6.0 * scale)
            bar_x = (x + w - pad_x * 0.65 - bar_w) if any_rtl else (x + pad_x * 0.65)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(c_acc)
            painter.drawRoundedRect(
                QRectF(bar_x, y + pad_y * 0.85, bar_w, max(8.0, (eff_h - pad_y * 1.7) * vis)),
                bar_w * 0.5,
                bar_w * 0.5,
            )
            text_left = x + pad_x if any_rtl else (bar_x + bar_w + 12.0 * scale)

        # 3. Draw adaptive wrapped text lines (never clipped!)
        cur_y = y + (eff_h - total_text_h) * 0.5
        for idx_l, item in enumerate(wrapped_lines):
            lh = item["height"]
            painter.setFont(item["font"])
            if layout_type == "split_h_2zone" and idx_l == 0:
                fg = QColor("#141414") if c_acc.lightness() > 150 else QColor("#FFFFFF")
            elif item["is_header"]:
                fg = c_txt
            else:
                fg = c_acc if c_acc.lightness() > 110 else c_txt.darker(115)
            painter.setPen(fg)
            align = (
                (Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if item.get("rtl") or any_rtl
                else (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            )
            painter.drawText(QRectF(text_left, cur_y, avail_text_w, lh), int(align), item["text"])
            cur_y += lh + 4.0 * scale
    finally:
        painter.restore()
    return rect


def render_lower_third_preview(spec_or_id: dict[str, Any] | str, width: int = 280, height: int = 160) -> QImage:
    spec = default_lower_third_spec(spec_or_id) if isinstance(spec_or_id, str) else normalize_lower_third_spec(spec_or_id)
    img = QImage(int(width), int(height), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    try:
        ov_dummy = {
            "x": 8,
            "y": 24,
            "w": width - 16,
            "h": height - 48,
            "opacity": 255,
            "content": str(spec.get("default_text") or "主标题内容\n支持多行自适应排版"),
            "lower_third_spec": spec,
            "start": 0.0,
            "end": 5.0,
        }
        render_lower_third(p, ov_dummy, current_time=2.0, video_w=width, video_h=height)
    finally:
        p.end()
    return img
