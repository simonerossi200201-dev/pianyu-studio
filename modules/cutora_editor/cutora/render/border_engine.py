# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""World-class parametric border engine with 205 commercial-safe styles.

Supports:
- 8 aesthetic families (Western Classical, Eastern Guofeng, Floral Botanical,
  Luxury Texture, Modern Minimal, Cinematic Film, Cyber Neon, Sacred Ceremonial)
- Constant-thickness rendering when resizing bounding box (x, y, w, h)
- Edge modes: four_sides, two_sides_h, two_sides_v, corners,
  one_side_top, one_side_bottom, one_side_left, one_side_right
- Parametric length_ratio (5% - 100%), thickness (1 - 80 px), corner_radius,
  and color modes (solid, gold_gradient, dual_gradient, rainbow_neon)
- Auto-border binding to any selected layer or full video canvas
"""
from __future__ import annotations

import copy
import math
import uuid
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)

EDGE_MODES = (
    ("four_sides", "四边环绕 (Four Sides)"),
    ("two_sides_h", "上下双边 (Top & Bottom)"),
    ("two_sides_v", "左右双边 (Left & Right)"),
    ("corners", "四角折角 (Corners Only)"),
    ("one_side_top", "顶部单边 (Top Only)"),
    ("one_side_bottom", "底部单边 (Bottom Only)"),
    ("one_side_left", "左侧单边 (Left Only)"),
    ("one_side_right", "右侧单边 (Right Only)"),
)

COLOR_MODES = (
    ("solid", "单色 (Solid)"),
    ("gold_gradient", "鎏金渐变 (Gold Gradient)"),
    ("dual_gradient", "双色渐变 (Dual Gradient)"),
    ("rainbow_neon", "彩虹霓虹 (Rainbow Neon)"),
)


def _build_catalog() -> list[dict[str, Any]]:
    families = [
        (
            "western_classic",
            "西方古典",
            "vintage_filigree",
            [
                ("巴洛克皇室金箔框", "#FFD700", "#996515", "baroque_scroll", "four_sides", 12, 14),
                ("洛可可卷草雕花框", "#F6D365", "#C99E32", "rococo_curl", "four_sides", 10, 18),
                ("维多利亚蕾丝金框", "#FFF3D1", "#D4AF37", "victorian_lace", "four_sides", 9, 16),
                ("Art Deco 流金几何框", "#F5C451", "#2B2112", "art_deco_step", "four_sides", 11, 4),
                ("哥特彩绘玫瑰花窗框", "#E6B800", "#5C3A21", "gothic_arch", "four_sides", 14, 20),
                ("古希腊回纹神殿框", "#E5C158", "#8B6914", "greek_meander", "four_sides", 12, 2),
                ("文艺复兴卢浮画廊框", "#D4AF37", "#6B4C1B", "gallery_molding", "four_sides", 16, 6),
                ("凡尔赛宫廷双层金框", "#FFE082", "#B8860B", "double_royal", "four_sides", 10, 12),
                ("科林斯柱头桂冠框", "#F3E5AB", "#C5A059", "laurel_corner", "four_sides", 11, 10),
                ("拜占庭宝石镶嵌框", "#FFD54F", "#8D4004", "gem_inlay", "four_sides", 13, 8),
                ("威尼斯古典镜面金框", "#FFF8E1", "#B28428", "venetian_mirror", "four_sides", 12, 22),
                ("新古典主义浮雕角框", "#E6C670", "#7D5A18", "baroque_scroll", "corners", 12, 12),
                ("歌剧院天鹅绒金边框", "#FFCC33", "#4A0E17", "double_royal", "four_sides", 14, 14),
                ("巴黎沙龙画报细金框", "#F0D58C", "#8C6D23", "art_deco_step", "four_sides", 7, 6),
                ("温莎皇家盾徽折角框", "#FFD700", "#7A5210", "laurel_corner", "corners", 13, 10),
                ("罗马万神殿拱券上下框", "#E8C766", "#9A7420", "greek_meander", "two_sides_h", 11, 4),
                ("佛罗伦萨金匠花丝框", "#FFE599", "#B48327", "victorian_lace", "four_sides", 8, 16),
                ("帝国时代雄鹰金章框", "#F7C844", "#785109", "art_deco_step", "four_sides", 13, 4),
                ("爱德华铂金钻饰细框", "#E5E8E8", "#A6ACAF", "gem_inlay", "four_sides", 8, 12),
                ("古典油画内衬斜切框", "#D9B356", "#5E4315", "gallery_molding", "four_sides", 18, 4),
                ("奥赛博物馆流金对称框", "#F4D068", "#9C731E", "rococo_curl", "two_sides_v", 10, 14),
                ("盖茨比流金岁月菱格框", "#FFD700", "#1C160C", "art_deco_step", "four_sides", 12, 6),
                ("波西米亚水晶雕花框", "#F0F8FF", "#93B8D7", "victorian_lace", "four_sides", 10, 18),
                ("凯旋门石雕铭文上下框", "#E2C478", "#846727", "greek_meander", "two_sides_h", 12, 2),
                ("美第奇家族百合徽章框", "#FAD02C", "#8F6111", "baroque_scroll", "four_sides", 11, 14),
                ("维也纳金色大厅乐谱框", "#FFE066", "#AA771C", "double_royal", "four_sides", 9, 10),
                ("格里高利古典手抄本框", "#E5B84B", "#6E3B14", "gothic_arch", "four_sides", 13, 12),
                ("圣彼得堡琥珀宫廷框", "#FFBF00", "#804000", "gem_inlay", "four_sides", 14, 10),
                ("大英图书馆烫金书脊框", "#E6C253", "#593E10", "gallery_molding", "two_sides_v", 11, 4),
                ("古典皇室铭牌顶部冠冕", "#FFD700", "#A67C00", "baroque_scroll", "one_side_top", 14, 12),
            ],
        ),
        (
            "eastern_guofeng",
            "东方国风",
            "guofeng_pattern",
            [
                ("故宫朱金万字不到头框", "#FFD700", "#A81C1C", "cn_fret", "four_sides", 12, 4),
                ("敦煌莫高窟藻井华盖框", "#F4C44E", "#2A6F68", "dunhuang_lotus", "four_sides", 14, 10),
                ("中式古典回纹描金框", "#E8C15A", "#7A4B12", "cn_fret", "four_sides", 10, 2),
                ("宣纸水墨飞白意境框", "#2B2B2B", "#787878", "ink_brush", "four_sides", 14, 8),
                ("元青花缠枝莲纹瓷框", "#1D4E89", "#E8F1F8", "blue_porcelain", "four_sides", 13, 12),
                ("宋代汝窑天青金丝框", "#A8C6C0", "#D4AF37", "song_hairline", "four_sides", 7, 14),
                ("潇湘翠竹竹节风雅框", "#5E8C61", "#C5D8A4", "bamboo_joint", "four_sides", 10, 6),
                ("苏州园林海棠花窗框", "#8C3B2B", "#E2B659", "begonia_window", "four_sides", 12, 20),
                ("景泰蓝掐丝珐琅宝相框", "#1A5276", "#FFD700", "cloisonne", "four_sides", 13, 10),
                ("大唐盛世流金瑞云框", "#FFCC33", "#C0392B", "auspicious_cloud", "four_sides", 12, 16),
                ("波斯皇家蓝金星格框", "#1B4F72", "#F4D03F", "arabesque_star", "four_sides", 13, 8),
                ("阿拉伯吉里赫几何金框", "#D4AF37", "#0E6251", "arabesque_star", "four_sides", 11, 6),
                ("紫禁城雕龙金漆折角框", "#FFD700", "#8B0000", "cn_fret", "corners", 14, 4),
                ("徽派建筑马头墙黛瓦框", "#34495E", "#BDC3C7", "cn_fret", "two_sides_h", 11, 2),
                ("丝绸之路流沙金驼铃框", "#E59866", "#F8C471", "dunhuang_lotus", "four_sides", 11, 12),
                ("京都金阁寺莳绘漆器框", "#F5B041", "#17202A", "song_hairline", "four_sides", 9, 8),
                ("江南烟雨乌篷船木雕框", "#6E472B", "#D2B48C", "begonia_window", "four_sides", 11, 14),
                ("汉代未央宫瓦当朱砂框", "#C0392B", "#F5CBA7", "auspicious_cloud", "corners", 13, 12),
                ("明式黄花梨博古架框", "#873600", "#E59866", "cn_fret", "four_sides", 12, 4),
                ("兰亭集序洒金宣纸框", "#D4AC0D", "#FDEBD0", "ink_brush", "two_sides_v", 10, 6),
                ("千里江山青绿山水框", "#1ABC9C", "#2E86C1", "auspicious_cloud", "four_sides", 12, 14),
                ("长安十二时辰朱雀灯框", "#E74C3C", "#F39C12", "dunhuang_lotus", "four_sides", 13, 10),
                ("敦煌飞天飘带流云上下框", "#F5B041", "#48C9B0", "auspicious_cloud", "two_sides_h", 11, 16),
                ("金石篆刻朱文印章角框", "#B03A2E", "#FDEDEC", "cn_fret", "corners", 12, 2),
                ("大漠敦煌九色鹿金辉框", "#F8C471", "#BA4A00", "dunhuang_lotus", "four_sides", 10, 14),
                ("苏州刺绣双面金线框", "#F7DC6F", "#EC7063", "begonia_window", "four_sides", 8, 18),
                ("龙泉青瓷冰裂纹雅框", "#76D7C4", "#E8F8F5", "song_hairline", "four_sides", 9, 10),
                ("故宫红墙琉璃瓦顶框", "#922B21", "#F4D03F", "cn_fret", "one_side_top", 14, 4),
                ("水墨远山云雾底栏框", "#2C3E50", "#95A5A6", "ink_brush", "one_side_bottom", 14, 8),
                ("东方瑞鹤祥云双侧画轴", "#D4AF37", "#212F3D", "auspicious_cloud", "two_sides_v", 12, 8),
            ],
        ),
        (
            "floral_botanical",
            "花卉藤蔓",
            "floral_vine",
            [
                ("大马士革金玫瑰藤蔓框", "#FFD700", "#E91E63", "rose_garland", "four_sides", 12, 16),
                ("希腊雅典娜橄榄枝桂冠框", "#D4AF37", "#689F38", "olive_wreath", "four_sides", 11, 18),
                ("京都春日垂枝樱花框", "#FFB7C5", "#FFF0F5", "sakura_branch", "four_sides", 12, 20),
                ("普罗旺斯薰衣草紫藤框", "#B39DDB", "#7E57C2", "lavender_vine", "four_sides", 10, 16),
                ("深秋黄金银杏叶舞框", "#FFC107", "#FF8F00", "ginkgo_leaf", "four_sides", 12, 14),
                ("冬日雪松红果圣诞花框", "#2E7D32", "#C62828", "pine_holly", "four_sides", 13, 14),
                ("洛阳国色天香牡丹金框", "#F06292", "#FFD54F", "peony_bloom", "four_sides", 14, 18),
                ("幽谷铃兰珍珠白金框", "#F5F5F5", "#A5D6A7", "lily_pearl", "four_sides", 9, 20),
                ("穆夏新艺术鸢尾花藤框", "#CE93D8", "#FFE082", "iris_nouveau", "four_sides", 13, 22),
                ("森林秘境常春藤缠绕框", "#4CAF50", "#1B5E20", "olive_wreath", "four_sides", 11, 14),
                ("法式香槟白玫瑰婚礼框", "#FFF8E7", "#E6C786", "rose_garland", "four_sides", 11, 18),
                ("江南荷塘月色莲叶框", "#4DB6AC", "#F48FB1", "peony_bloom", "four_sides", 12, 16),
                ("金秋枫叶流丹折角框", "#FF5722", "#FFC107", "ginkgo_leaf", "corners", 13, 12),
                ("莫奈花园睡莲水彩框", "#81D4FA", "#CE93D8", "iris_nouveau", "four_sides", 12, 20),
                ("尤加利叶北欧森系框", "#80CBC4", "#4DB6AC", "olive_wreath", "two_sides_v", 10, 14),
                ("黄金麦穗丰收庆典框", "#FFD54F", "#FFA000", "olive_wreath", "four_sides", 12, 14),
                ("山茶花香奈儿黑白金框", "#FFFFFF", "#D4AF37", "rose_garland", "four_sides", 10, 16),
                ("紫藤花瀑垂落顶部花框", "#BA68C8", "#E1BEE7", "lavender_vine", "one_side_top", 16, 18),
                ("岁寒三友松竹梅雅集框", "#8D6E63", "#D84315", "sakura_branch", "four_sides", 11, 12),
                ("热带龟背竹棕榈度假框", "#26A69A", "#00695C", "olive_wreath", "corners", 14, 16),
                ("满天星干花手账温柔框", "#FFF9C4", "#F8BBD0", "lily_pearl", "four_sides", 8, 18),
                ("红丝绒暗夜玫瑰哥特框", "#B71C1C", "#212121", "rose_garland", "four_sides", 13, 14),
                ("向日葵梵高星空金辉框", "#FFCA28", "#1565C0", "peony_bloom", "four_sides", 13, 16),
                ("晚香玉月光清雅上下框", "#E8F5E9", "#C8E6C9", "lily_pearl", "two_sides_h", 10, 14),
                ("春日桃花灼灼折角花框", "#FF80AB", "#FF4081", "sakura_branch", "corners", 12, 16),
            ],
        ),
        (
            "luxury_texture",
            "材质质感",
            "glow_frame",
            [
                ("24K 皇家锤纹鎏金框", "#FFD700", "#996515", "hammered_gold", "four_sides", 14, 10),
                ("拉丝香槟玫瑰金金属框", "#F7CAC9", "#D4AF37", "brushed_metal", "four_sides", 10, 12),
                ("施华洛世奇水钻星芒框", "#FFFFFF", "#B3E5FC", "crystal_diamond", "four_sides", 11, 14),
                ("深海幻彩珍珠母贝框", "#F3E5F5", "#80DEEA", "mother_of_pearl", "four_sides", 12, 16),
                ("手作毛边棉浆艺术纸框", "#FAF0E6", "#D7CCC8", "deckle_paper", "four_sides", 16, 6),
                ("中世纪火烧羊皮卷古框", "#D7B377", "#5D4037", "parchment_edge", "four_sides", 15, 8),
                ("Apple 磨砂毛玻璃高光框", "#FFFFFF", "#90CAF9", "frosted_glass", "four_sides", 10, 24),
                ("黑金丝绒高定秀场框", "#D4AF37", "#121212", "velvet_gold", "four_sides", 13, 8),
                ("大马士革折叠钢纹硬核框", "#B0BEC5", "#37474F", "brushed_metal", "four_sides", 12, 4),
                ("流沙星钻香槟夜宴框", "#FFE082", "#FF6F00", "crystal_diamond", "four_sides", 12, 16),
                ("复古邮票锯齿纪念框", "#FFF8E1", "#8D6E63", "stamp_perforated", "four_sides", 14, 4),
                ("意大利卡拉拉白大理石框", "#ECEFF1", "#B0BEC5", "mother_of_pearl", "four_sides", 15, 6),
                ("孔雀石绿金奢华珠宝框", "#00695C", "#FFD700", "velvet_gold", "four_sides", 13, 10),
                ("青金石星空金箔画框", "#1A237E", "#FFD54F", "hammered_gold", "four_sides", 13, 10),
                ("铂金拉丝腕表刻度框", "#E0E0E0", "#757575", "brushed_metal", "four_sides", 9, 12),
                ("琥珀蜜蜡暖光温润框", "#FFB300", "#6D4C41", "mother_of_pearl", "four_sides", 12, 16),
                ("撕边水彩画布留白框", "#FFFDF9", "#E0D6C8", "deckle_paper", "four_sides", 18, 4),
                ("红木火漆印章信笺框", "#8E2424", "#E6C687", "parchment_edge", "four_sides", 12, 8),
                ("冰川水晶折射棱镜框", "#E1F5FE", "#4FC3F7", "crystal_diamond", "four_sides", 11, 14),
                ("高定粗花呢金线编织框", "#F5F5DC", "#D4AF37", "hammered_gold", "four_sides", 12, 10),
                ("紫水晶簇晶洞闪耀框", "#9C27B0", "#E1BEE7", "crystal_diamond", "corners", 14, 14),
                ("钛合金枪灰机械拉丝框", "#607D8B", "#263238", "brushed_metal", "two_sides_h", 10, 4),
                ("琉璃渐变霓裳柔光框", "#FF80AB", "#82B1FF", "frosted_glass", "four_sides", 11, 22),
                ("黄金叶碎片手作宣纸框", "#FFD700", "#F5E6CC", "deckle_paper", "four_sides", 14, 8),
                ("皇家丝绒鎏金剧场帷幕框", "#B71C1C", "#FFD700", "velvet_gold", "one_side_top", 18, 12),
            ],
        ),
        (
            "modern_minimal",
            "现代极简",
            "double_line",
            [
                ("Vogue 杂志画报极细双线框", "#FFFFFF", "#D4AF37", "editorial_double", "four_sides", 4, 2),
                ("黄金分割内缩几何细框", "#F5C451", "#FFFFFF", "golden_inset", "four_sides", 5, 6),
                ("瑞士国际主义网格坐标框", "#FFFFFF", "#FF3366", "swiss_grid", "four_sides", 4, 0),
                ("包豪斯红黄蓝构成主义框", "#FFCC00", "#E63946", "bauhaus_block", "four_sides", 8, 2),
                ("极简圆角呼吸感白金框", "#FFFFFF", "#E0E0E0", "minimal_pill", "four_sides", 6, 28),
                ("微距摄影十字刻度准星框", "#00E5FF", "#FFFFFF", "crosshair_ticks", "four_sides", 4, 4),
                ("画廊留白美术馆装裱框", "#FAFAFA", "#BDBDBD", "gallery_mat", "four_sides", 20, 2),
                ("建筑学 45° 斜切倒角框", "#E0E0E0", "#9E9E9E", "chamfer_octagon", "four_sides", 6, 0),
                ("莫兰迪燕麦奶咖柔雾框", "#E6CCB2", "#B08968", "minimal_pill", "four_sides", 8, 18),
                ("北欧极简中心断线呼吸框", "#FFFFFF", "#B0BEC5", "center_gap", "four_sides", 5, 10),
                ("纪实摄影底部信息栏细框", "#FFFFFF", "#FFD700", "editorial_double", "one_side_bottom", 5, 0),
                ("小红书爆款奶油白圆角框", "#FFFDF7", "#F3E5D8", "minimal_pill", "four_sides", 10, 26),
                ("Apple 发布会流光细线框", "#E3F2FD", "#64B5F6", "minimal_pill", "four_sides", 5, 30),
                ("极简四角 L 型精工折角", "#FFFFFF", "#FFD700", "corner_l_clean", "corners", 6, 4),
                ("时尚画册上下平行金线框", "#D4AF37", "#FFF8E1", "editorial_double", "two_sides_h", 5, 0),
                ("画报左右对称竖向导轨框", "#FFFFFF", "#CFD8DC", "editorial_double", "two_sides_v", 5, 0),
                ("高级灰双层错位几何框", "#ECEFF1", "#78909C", "offset_double", "four_sides", 5, 4),
                ("日系杂志清爽细虚线框", "#FFFFFF", "#81D4FA", "dashed_clean", "four_sides", 4, 14),
                ("波点圆润现代艺术细框", "#FFE082", "#FF8A65", "dotted_clean", "four_sides", 6, 18),
                ("建筑蓝图工程尺寸标注框", "#64B5F6", "#E3F2FD", "swiss_grid", "four_sides", 4, 2),
                ("极简顶部高光胶囊横条", "#FFFFFF", "#FFD54F", "minimal_pill", "one_side_top", 6, 12),
                ("左侧强调色视觉引导线", "#FF4081", "#FFFFFF", "editorial_double", "one_side_left", 8, 4),
                ("右侧黄金比例分割竖线", "#FFD700", "#FFFFFF", "editorial_double", "one_side_right", 8, 4),
                ("几何圆点四角锚点极简框", "#FFFFFF", "#00E676", "corner_dots", "four_sides", 4, 8),
                ("极简黑曜石暗调细边款", "#263238", "#90A4AE", "golden_inset", "four_sides", 6, 10),
                ("静奢风老钱香槟细金框", "#E6C786", "#9E7B3B", "editorial_double", "four_sides", 5, 8),
                ("现代主义不对称对角折框", "#FFFFFF", "#FF6D00", "corner_l_clean", "corners", 7, 2),
                ("纯净水滴超窄边圆角框", "#E0F7FA", "#80DEEA", "minimal_pill", "four_sides", 4, 32),
                ("时尚特写中心聚焦折角", "#FFF59D", "#FFFFFF", "corner_l_clean", "corners", 5, 8),
                ("极简主义三线韵律上下框", "#FFFFFF", "#B0BEC5", "editorial_double", "two_sides_h", 6, 2),
            ],
        ),
        (
            "cinematic_film",
            "电影取景",
            "viewfinder_hud",
            [
                ("IMAX 70mm 胶片齿孔边框", "#1A1A1A", "#F5F5F5", "film_sprocket", "two_sides_v", 22, 2),
                ("Super 8 复古 8毫米胶片框", "#212121", "#FFE082", "super8_film", "four_sides", 18, 16),
                ("ARRI 电影机 REC 录制取景器", "#FF1744", "#FFFFFF", "rec_viewfinder", "four_sides", 5, 6),
                ("宝丽来 Polaroid 复古相纸框", "#FAF8F5", "#E0DDD5", "polaroid_frame", "four_sides", 24, 4),
                ("2.39:1 变宽银幕金色遮幅线", "#D4AF37", "#111111", "anamorphic_bars", "two_sides_h", 16, 0),
                ("卓别林默片时代雕花字幕框", "#F5F5F5", "#9E9E9E", "silent_era", "four_sides", 10, 12),
                ("Kodak Portra 400 底片边框", "#FF8F00", "#1A1A1A", "film_sprocket", "two_sides_h", 20, 2),
                ("DJI 无人机航拍遥测 HUD 框", "#00E676", "#FFFFFF", "drone_telemetry", "four_sides", 4, 6),
                ("VHS 90年代磁带扫描线框", "#00E5FF", "#FF4081", "vhs_camcorder", "four_sides", 6, 8),
                ("索尼 Cinema Line 变形宽银幕框", "#FF9100", "#E0E0E0", "rec_viewfinder", "corners", 6, 4),
                ("莱卡 Leica 旁轴取景亮线框", "#FFFFFF", "#FF5252", "leica_brightline", "four_sides", 4, 10),
                ("哈苏中画幅胶片齿槽黑框", "#141414", "#D4AF37", "super8_film", "four_sides", 16, 4),
                ("复古 CRT 显像管弧面暗角框", "#37474F", "#90A4AE", "vhs_camcorder", "four_sides", 14, 28),
                ("电影节金棕榈展映纪念框", "#FFD700", "#212121", "anamorphic_bars", "two_sides_h", 12, 2),
                ("导演监视器安全框与中心十字", "#00B0FF", "#FFFFFF", "rec_viewfinder", "four_sides", 4, 2),
                ("复古幻灯片反转片卡纸框", "#F5F5F0", "#BDBDBD", "polaroid_frame", "four_sides", 20, 10),
                ("富士 Velvia 反转片胶卷框", "#00C853", "#1A1A1A", "film_sprocket", "two_sides_v", 18, 2),
                ("监控摄像头夜视绿光 HUD 框", "#69F0AE", "#00E676", "drone_telemetry", "corners", 5, 4),
                ("王家卫胶片抽帧暖调边框", "#FFB300", "#BF360C", "super8_film", "four_sides", 12, 8),
                ("韦斯·安德森对称糖果画框", "#F48FB1", "#FFF59D", "silent_era", "four_sides", 12, 14),
                ("诺兰 IMAX 巨幕上下黑金遮幅", "#0D0D0D", "#E5C158", "anamorphic_bars", "two_sides_h", 22, 0),
                ("复古胶卷时间码侧边栏框", "#FF6D00", "#212121", "film_sprocket", "one_side_left", 20, 0),
                ("电影片尾滚动字幕金线导轨", "#D4AF37", "#FFFFFF", "leica_brightline", "two_sides_v", 5, 4),
                ("单反光学取景器对焦阵列框", "#FF1744", "#ECEFF1", "drone_telemetry", "corners", 5, 6),
                ("经典黄金好莱坞宽银幕画框", "#FFD54F", "#5D4037", "silent_era", "four_sides", 14, 10),
            ],
        ),
        (
            "cyber_neon",
            "赛博霓虹",
            "cyber_neon",
            [
                ("赛博朋克青紫双色霓虹框", "#00F5D4", "#FF007F", "neon_tube_dual", "four_sides", 10, 14),
                ("机甲战士战术切角 HUD 框", "#00E5FF", "#2979FF", "mecha_chamfer", "four_sides", 8, 0),
                ("全息投影激光扫描线框", "#18FFFF", "#7C4DFF", "holo_scan", "four_sides", 7, 10),
                ("量子脉冲高能电弧边框", "#76FF03", "#00E5FF", "quantum_pulse", "four_sides", 9, 8),
                ("电竞 RGB 幻彩流光溢彩框", "#FF1744", "#00E5FF", "rgb_esports", "four_sides", 11, 16),
                ("黑客帝国矩阵数字流边框", "#00E676", "#004D40", "holo_scan", "two_sides_v", 10, 4),
                ("故障艺术 RGB 色差撕裂框", "#FF0055", "#00F0FF", "glitch_split", "four_sides", 9, 2),
                ("星际飞船舰桥全息控制台框", "#40C4FF", "#FFAB00", "mecha_chamfer", "four_sides", 8, 6),
                ("霓虹灯牌夜店紫金闪耀框", "#E040FB", "#FFD740", "neon_tube_dual", "four_sides", 10, 18),
                ("未来主义钛金蓝光折角框", "#00B0FF", "#E1F5FE", "mecha_chamfer", "corners", 10, 4),
                ("赛博武士赤红激光刀锋框", "#FF1744", "#FF9100", "neon_tube_dual", "four_sides", 9, 6),
                ("AI 神经网络节点脉冲框", "#651FFF", "#00E5FF", "quantum_pulse", "four_sides", 7, 12),
                ("虚拟偶像元宇宙粉蓝光轨框", "#FF80AB", "#80D8FF", "neon_tube_dual", "four_sides", 9, 20),
                ("科幻机甲顶部战术雷达条", "#00E5FF", "#1DE9B6", "mecha_chamfer", "one_side_top", 12, 4),
                ("赛博底部能量充能进度框", "#76FF03", "#00B0FF", "mecha_chamfer", "one_side_bottom", 12, 4),
                ("全息六边形蜂巢护盾边框", "#00E5FF", "#304FFE", "holo_scan", "four_sides", 10, 8),
                ("合成波 Synthwave 落日霓虹框", "#FF4081", "#FFAB00", "neon_tube_dual", "two_sides_h", 10, 12),
                ("量子传送门幽蓝旋涡角框", "#2979FF", "#00E5FF", "quantum_pulse", "corners", 12, 10),
                ("高能粒子对撞流光双侧框", "#FFD600", "#FF3D00", "rgb_esports", "two_sides_v", 9, 8),
                ("东京夜雨霓虹招牌闪烁框", "#FF007F", "#00F5D4", "glitch_split", "four_sides", 10, 10),
            ],
        ),
        (
            "sacred_ceremonial",
            "宗教庆典",
            "sacred_halo",
            [
                ("圣光十字流金荣耀圣框", "#FFD700", "#FFF8E1", "sacred_cross_rays", "four_sides", 12, 14),
                ("巴黎圣母院彩绘玫瑰花窗框", "#FFB300", "#7B1FA2", "stained_glass", "four_sides", 14, 20),
                ("炽天使羽翼金辉守护框", "#FFF3E0", "#FFD54F", "angel_wings", "four_sides", 12, 18),
                ("古卷圣经羊皮纸烫金框", "#E6C253", "#5D4037", "scripture_scroll", "four_sides", 13, 10),
                ("和平鸽与橄榄枝神圣光环框", "#FFFFFF", "#FFD700", "peace_dove_halo", "four_sides", 11, 16),
                ("千叶金莲禅意佛光静心框", "#FFD54F", "#BF360C", "golden_lotus", "four_sides", 12, 18),
                ("星月交辉皇家蓝金拱门框", "#FFD700", "#0D47A1", "crescent_arch", "four_sides", 13, 16),
                ("祈福烛光温暖金辉纪念框", "#FFCA28", "#FF6F00", "candle_blessing", "four_sides", 11, 14),
                ("皇家教堂婚礼誓言白金框", "#FFFDF9", "#D4AF37", "sacred_cross_rays", "four_sides", 10, 18),
                ("奥林匹亚胜利金桂冠荣耀框", "#FFD700", "#FFA000", "peace_dove_halo", "four_sides", 13, 16),
                ("圣殿拱顶哥特金辉顶部框", "#FFD700", "#8D6E63", "crescent_arch", "one_side_top", 16, 18),
                ("禅宗菩提叶金丝静谧框", "#E6C670", "#4E342E", "golden_lotus", "four_sides", 9, 14),
                ("福音金号角四角圣光折角", "#FFD700", "#FFF9C4", "sacred_cross_rays", "corners", 13, 12),
                ("大马士革清真蓝金穹顶框", "#00ACC1", "#FFD700", "crescent_arch", "four_sides", 13, 14),
                ("圣诞平安夜伯利恒之星框", "#FFD700", "#B71C1C", "sacred_cross_rays", "four_sides", 12, 14),
                ("神圣唱诗班竖琴金弦双侧框", "#FFE082", "#D4AF37", "angel_wings", "two_sides_v", 11, 12),
                ("甘露净瓶莲花底座下栏框", "#FFD54F", "#8D6E63", "golden_lotus", "one_side_bottom", 15, 16),
                ("圣洁百合与十字星芒框", "#FFFFFF", "#FFE082", "peace_dove_halo", "four_sides", 10, 16),
                ("金色摩西十诫石板铭文框", "#D4AF37", "#3E2723", "scripture_scroll", "four_sides", 14, 8),
                ("万丈金光神圣天穹环绕框", "#FFD700", "#FF8F00", "candle_blessing", "four_sides", 13, 20),
            ],
        ),
    ]

    catalog: list[dict[str, Any]] = []
    idx_global = 0
    for fam_id, fam_label, prop_style, entries in families:
        for idx_in_fam, (name, c1, c2, recipe, edge_mode, thick, rad) in enumerate(entries, 1):
            idx_global += 1
            style_id = f"bd_{fam_id[:4]}_{idx_in_fam:03d}"
            color_mode = (
                "rainbow_neon"
                if fam_id == "cyber_neon" and idx_in_fam % 3 == 0
                else ("gold_gradient" if "#FFD" in c1 or "#D4A" in c1 or "#F5C" in c1 else "dual_gradient")
            )
            length_ratio = 0.38 if edge_mode == "corners" else 1.0
            spec = {
                "style_id": style_id,
                "style": prop_style,
                "recipe": recipe,
                "family": fam_id,
                "category": fam_label,
                "edge_mode": edge_mode,
                "thickness": int(thick),
                "length_ratio": float(length_ratio),
                "corner_radius": int(rad),
                "color_mode": color_mode,
                "primary_color": c1,
                "secondary_color": c2,
                "corner_ornament": True,
                "glow": fam_id in ("cyber_neon", "sacred_ceremonial", "luxury_texture"),
                "target_link": "none",
            }
            catalog.append(
                {
                    "id": style_id,
                    "name": name,
                    "family": fam_id,
                    "category": fam_label,
                    "subcat": fam_label,
                    "tags": [fam_label, fam_id, prop_style, recipe, edge_mode, name],
                    "border_spec": spec,
                }
            )
    return catalog


BORDER_STYLES: list[dict[str, Any]] = _build_catalog()
BORDER_STYLES_BY_ID: dict[str, dict[str, Any]] = {it["id"]: it for it in BORDER_STYLES}


def list_border_styles(family: str = "") -> list[dict[str, Any]]:
    if not family:
        return list(BORDER_STYLES)
    return [it for it in BORDER_STYLES if it["family"] == family or it["category"] == family]


def get_border_style(style_id: str) -> dict[str, Any]:
    if style_id in BORDER_STYLES_BY_ID:
        return BORDER_STYLES_BY_ID[style_id]
    for it in BORDER_STYLES:
        if it["border_spec"].get("style") == style_id or it["border_spec"].get("recipe") == style_id:
            return it
    return BORDER_STYLES[0]


def default_border_spec(style_id: str = "bd_west_001") -> dict[str, Any]:
    st = get_border_style(style_id)
    return copy.deepcopy(st["border_spec"])


def normalize_border_spec(spec: dict[str, Any] | None) -> dict[str, Any]:
    base = default_border_spec(str((spec or {}).get("style_id") or (spec or {}).get("style") or "bd_west_001"))
    if isinstance(spec, dict):
        base.update(spec)
    base["thickness"] = max(1.0, min(80.0, float(base.get("thickness", 10.0) or 10.0)))
    base["length_ratio"] = max(0.05, min(1.0, float(base.get("length_ratio", 1.0) or 1.0)))
    base["corner_radius"] = max(0.0, min(120.0, float(base.get("corner_radius", 10.0) or 0.0)))
    em = str(base.get("edge_mode") or "four_sides")
    if em not in {k for k, _ in EDGE_MODES}:
        base["edge_mode"] = "corners" if base.get("style") == "corner_brackets" else "four_sides"
    return base


def _make_brush(rect: QRectF, c1: QColor, c2: QColor, color_mode: str, current_time: float = 0.0) -> QBrush:
    if color_mode == "solid":
        return QBrush(c1)
    grad = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.bottom())
    if color_mode in ("rainbow_neon", "neon_glow"):
        phase = (float(current_time or 0.0) * 0.25) % 1.0
        grad.setColorAt(0.0, c1)
        grad.setColorAt((0.33 + phase) % 1.0, QColor("#00F5D4"))
        grad.setColorAt((0.66 + phase) % 1.0, c2)
        grad.setColorAt(1.0, c1)
    elif color_mode in ("gold_gradient", "metallic_gold"):
        hi = QColor(c1).lighter(135)
        sh = QColor(c2).darker(115)
        grad.setColorAt(0.0, sh)
        grad.setColorAt(0.28, c1)
        grad.setColorAt(0.50, hi)
        grad.setColorAt(0.74, c2)
        grad.setColorAt(1.0, sh)
    else:
        grad.setColorAt(0.0, c1)
        grad.setColorAt(0.5, c2)
        grad.setColorAt(1.0, c1)
    return QBrush(grad)


def _draw_centered_segment(painter: QPainter, x1: float, y1: float, x2: float, y2: float, ratio: float) -> None:
    mx = (x1 + x2) * 0.5
    my = (y1 + y2) * 0.5
    hx = (x2 - x1) * 0.5 * ratio
    hy = (y2 - y1) * 0.5 * ratio
    painter.drawLine(QPointF(mx - hx, my - hy), QPointF(mx + hx, my + hy))


def _draw_corner_motif(
    painter: QPainter,
    cx: float,
    cy: float,
    sx: float,
    sy: float,
    arm_x: float,
    arm_y: float,
    thickness: float,
    recipe: str,
    style: str,
    brush: QBrush,
    c1: QColor,
    c2: QColor,
) -> None:
    painter.save()
    try:
        r = max(6.0, min(arm_x, arm_y) * 0.42)
        if "guofeng" in style or recipe in ("cn_fret", "begonia_window", "cloisonne"):
            # Traditional Chinese fretwork (回纹) corner spiral
            step = max(3.5, thickness * 0.85)
            path = QPainterPath()
            path.moveTo(cx + sx * step * 3.2, cy)
            path.lineTo(cx, cy)
            path.lineTo(cx, cy + sy * step * 3.2)
            path.lineTo(cx + sx * step * 2.2, cy + sy * step * 3.2)
            path.lineTo(cx + sx * step * 2.2, cy + sy * step * 1.1)
            path.lineTo(cx + sx * step * 1.1, cy + sy * step * 1.1)
            path.lineTo(cx + sx * step * 1.1, cy + sy * step * 2.1)
            painter.setPen(QPen(brush, max(1.5, thickness * 0.55), Qt.PenStyle.SolidLine, Qt.PenCapStyle.SquareCap, Qt.PenJoinStyle.MiterJoin))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        elif "vintage" in style or recipe in ("baroque_scroll", "rococo_curl", "victorian_lace", "laurel_corner"):
            # Classical Baroque filigree scroll + diamond medallion at corner
            pen = QPen(brush, max(1.5, thickness * 0.55), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(cx + sx * r * 0.45, cy + sy * r * 0.45), r * 0.35, r * 0.35)
            painter.drawArc(
                QRectF(
                    min(cx, cx + sx * r * 1.3),
                    min(cy, cy + sy * r * 1.3),
                    abs(r * 1.3),
                    abs(r * 1.3),
                ),
                0,
                16 * 180,
            )
            # Corner diamond jewel
            jewel = QPainterPath()
            d = max(4.0, thickness * 0.9)
            jx, jy = cx + sx * d * 0.4, cy + sy * d * 0.4
            jewel.moveTo(jx, jy - d)
            jewel.lineTo(jx + d, jy)
            jewel.lineTo(jx, jy + d)
            jewel.lineTo(jx - d, jy)
            jewel.closeSubpath()
            painter.setBrush(c1)
            painter.drawPath(jewel)
        elif "floral" in style or recipe in ("rose_garland", "olive_wreath", "sakura_branch", "peony_bloom", "ginkgo_leaf"):
            # Botanical corner petals & leaves
            painter.setPen(QPen(c2, max(1.2, thickness * 0.35)))
            painter.setBrush(c1)
            leaf_r = max(5.0, thickness * 1.15)
            for ang in (15, 45, 75):
                rad = math.radians(ang)
                px = cx + sx * math.cos(rad) * leaf_r * 1.35
                py = cy + sy * math.sin(rad) * leaf_r * 1.35
                painter.drawEllipse(QPointF(px, py), leaf_r * 0.55, leaf_r * 0.32)
            painter.setBrush(c2)
            painter.drawEllipse(QPointF(cx + sx * leaf_r * 0.4, cy + sy * leaf_r * 0.4), leaf_r * 0.48, leaf_r * 0.48)
        elif "sacred" in style or recipe in ("sacred_cross_rays", "peace_dove_halo", "golden_lotus", "crescent_arch"):
            # Sacred 4-point star / cross emblem at corner
            d = max(5.0, thickness * 1.2)
            px, py = cx + sx * d * 0.5, cy + sy * d * 0.5
            star = QPainterPath()
            for i in range(8):
                ang = i * math.pi / 4.0
                rad_i = d if i % 2 == 0 else d * 0.32
                pt = QPointF(px + math.cos(ang) * rad_i, py + math.sin(ang) * rad_i)
                if i == 0:
                    star.moveTo(pt)
                else:
                    star.lineTo(pt)
            star.closeSubpath()
            painter.setPen(QPen(c2, 1.2))
            painter.setBrush(c1)
            painter.drawPath(star)
        elif "cyber" in style or "viewfinder" in style or recipe in ("mecha_chamfer", "rec_viewfinder", "drone_telemetry"):
            # Tactical HUD corner ticks
            d = max(6.0, thickness * 1.5)
            painter.setPen(QPen(c2, max(1.5, thickness * 0.5)))
            painter.drawLine(QPointF(cx + sx * d, cy + sy * d * 0.35), QPointF(cx + sx * d * 0.35, cy + sy * d * 0.35))
            painter.drawLine(QPointF(cx + sx * d * 0.35, cy + sy * d * 0.35), QPointF(cx + sx * d * 0.35, cy + sy * d))
    finally:
        painter.restore()


def render_border(
    painter: QPainter,
    ov: dict[str, Any],
    current_time: float = 0.0,
    video_w: int = 1080,
    video_h: int = 1920,
    task_data: Any = None,
) -> QRectF:
    """Render a parametric border overlay with constant stroke thickness."""
    raw_spec = ov.get("border_spec") or {}
    spec = normalize_border_spec(raw_spec)

    x = float(ov.get("x", 0.0))
    y = float(ov.get("y", 0.0))
    w = max(8.0, float(ov.get("w", video_w)))
    h = max(8.0, float(ov.get("h", video_h)))
    outer_rect = QRectF(x, y, w, h)

    # Constant thickness independent of w/h scaling (lightly scaled only on tiny thumbnail previews)
    min_dim = min(w, h)
    thumb_scale = min(1.0, max(0.32, min_dim / 260.0)) if min_dim < 180.0 else 1.0
    thickness = max(1.0, float(spec["thickness"]) * thumb_scale)
    length_ratio = float(spec["length_ratio"])
    corner_radius = float(spec["corner_radius"]) * thumb_scale
    edge_mode = str(spec["edge_mode"])
    style = str(spec.get("style") or "solid")
    recipe = str(spec.get("recipe") or "")
    color_mode = str(spec.get("color_mode") or "gold_gradient")
    c1 = QColor(str(spec.get("primary_color") or "#FFD700"))
    c2 = QColor(str(spec.get("secondary_color") or "#B8860B"))

    inset = thickness * 0.5 + 2.0 * thumb_scale
    bx = x + inset
    by = y + inset
    bw = max(4.0, w - inset * 2.0)
    bh = max(4.0, h - inset * 2.0)
    box = QRectF(bx, by, bw, bh)

    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        op = float(ov.get("opacity", 255) or 255) / 255.0
        painter.setOpacity(max(0.0, min(1.0, op)))

        brush = _make_brush(box, c1, c2, color_mode, current_time=current_time)

        # Optional soft outer glow for neon / sacred / luxury frames
        if spec.get("glow") or "cyber" in style or "glow" in style or "sacred" in style:
            glow_c = QColor(c1)
            glow_c.setAlpha(68)
            glow_pen = QPen(glow_c, thickness * 2.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(glow_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if edge_mode == "four_sides" and length_ratio >= 0.98:
                painter.drawRoundedRect(box, corner_radius, corner_radius)

        pen = QPen(brush, thickness, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        if "dashed" in style or "dashed" in recipe:
            pen.setStyle(Qt.PenStyle.DashLine)
        elif "dotted" in style or "dotted" in recipe or recipe == "stamp_perforated":
            pen.setStyle(Qt.PenStyle.DotLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Edge mode geometry
        if edge_mode == "four_sides":
            if length_ratio >= 0.985:
                painter.drawRoundedRect(box, corner_radius, corner_radius)
                if style in ("double_line", "vintage_filigree", "guofeng_pattern", "sacred_halo") or "double" in recipe:
                    gap = max(3.0 * thumb_scale, thickness * 1.15)
                    inner_box = box.adjusted(gap, gap, -gap, -gap)
                    if inner_box.width() > 4 and inner_box.height() > 4:
                        thin_pen = QPen(brush, max(1.0, thickness * 0.38))
                        painter.setPen(thin_pen)
                        painter.drawRoundedRect(inner_box, max(0.0, corner_radius - gap * 0.5), max(0.0, corner_radius - gap * 0.5))
                        painter.setPen(pen)
            else:
                _draw_centered_segment(painter, bx, by, bx + bw, by, length_ratio)
                _draw_centered_segment(painter, bx, by + bh, bx + bw, by + bh, length_ratio)
                _draw_centered_segment(painter, bx, by, bx, by + bh, length_ratio)
                _draw_centered_segment(painter, bx + bw, by, bx + bw, by + bh, length_ratio)
        elif edge_mode == "two_sides_h":
            _draw_centered_segment(painter, bx, by, bx + bw, by, length_ratio)
            _draw_centered_segment(painter, bx, by + bh, bx + bw, by + bh, length_ratio)
            if style in ("double_line", "vintage_filigree", "guofeng_pattern"):
                gap = max(3.0 * thumb_scale, thickness * 1.15)
                painter.setPen(QPen(brush, max(1.0, thickness * 0.4)))
                _draw_centered_segment(painter, bx, by + gap, bx + bw, by + gap, length_ratio * 0.92)
                _draw_centered_segment(painter, bx, by + bh - gap, bx + bw, by + bh - gap, length_ratio * 0.92)
        elif edge_mode == "two_sides_v":
            _draw_centered_segment(painter, bx, by, bx, by + bh, length_ratio)
            _draw_centered_segment(painter, bx + bw, by, bx + bw, by + bh, length_ratio)
            if style in ("double_line", "vintage_filigree", "guofeng_pattern"):
                gap = max(3.0 * thumb_scale, thickness * 1.15)
                painter.setPen(QPen(brush, max(1.0, thickness * 0.4)))
                _draw_centered_segment(painter, bx + gap, by, bx + gap, by + bh, length_ratio * 0.92)
                _draw_centered_segment(painter, bx + bw - gap, by, bx + bw - gap, by + bh, length_ratio * 0.92)
        elif edge_mode == "one_side_top":
            _draw_centered_segment(painter, bx, by, bx + bw, by, length_ratio)
        elif edge_mode == "one_side_bottom":
            _draw_centered_segment(painter, bx, by + bh, bx + bw, by + bh, length_ratio)
        elif edge_mode == "one_side_left":
            _draw_centered_segment(painter, bx, by, bx, by + bh, length_ratio)
        elif edge_mode == "one_side_right":
            _draw_centered_segment(painter, bx + bw, by, bx + bw, by + bh, length_ratio)
        elif edge_mode == "corners":
            arm_x = max(8.0 * thumb_scale, bw * 0.46 * length_ratio)
            arm_y = max(8.0 * thumb_scale, bh * 0.46 * length_ratio)
            path = QPainterPath()
            for cx, cy, sx, sy in (
                (bx, by, 1.0, 1.0),
                (bx + bw, by, -1.0, 1.0),
                (bx, by + bh, 1.0, -1.0),
                (bx + bw, by + bh, -1.0, -1.0),
            ):
                path.moveTo(cx + sx * arm_x, cy)
                path.lineTo(cx, cy)
                path.lineTo(cx, cy + sy * arm_y)
            painter.drawPath(path)

        # Specialized film sprocket holes or REC HUD indicators
        if recipe == "film_sprocket":
            sprocket_sz = max(4.0, thickness * 0.55)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(c2)
            if edge_mode == "two_sides_h":
                step = max(12.0, sprocket_sz * 2.4)
                n_holes = int((bw * length_ratio) // step)
                start_x = bx + (bw - n_holes * step) * 0.5
                for i in range(n_holes):
                    hx = start_x + i * step
                    painter.drawRoundedRect(QRectF(hx, by - sprocket_sz * 0.5, sprocket_sz * 1.2, sprocket_sz * 0.8), 1.5, 1.5)
                    painter.drawRoundedRect(QRectF(hx, by + bh - sprocket_sz * 0.3, sprocket_sz * 1.2, sprocket_sz * 0.8), 1.5, 1.5)
            else:
                step = max(12.0, sprocket_sz * 2.4)
                n_holes = int((bh * length_ratio) // step)
                start_y = by + (bh - n_holes * step) * 0.5
                for i in range(n_holes):
                    hy = start_y + i * step
                    painter.drawRoundedRect(QRectF(bx - sprocket_sz * 0.4, hy, sprocket_sz * 0.8, sprocket_sz * 1.2), 1.5, 1.5)
                    painter.drawRoundedRect(QRectF(bx + bw - sprocket_sz * 0.4, hy, sprocket_sz * 0.8, sprocket_sz * 1.2), 1.5, 1.5)
        elif recipe == "rec_viewfinder" and min_dim >= 120:
            painter.setPen( Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#FF1744"))
            dot_r = max(4.0, thickness * 0.9)
            painter.drawEllipse(QPointF(bx + dot_r * 2.5, by + dot_r * 2.2), dot_r, dot_r)
            f = QFont("Consolas", max(8, int(dot_r * 1.8)), QFont.Weight.Bold)
            painter.setFont(f)
            painter.setPen(c2)
            painter.drawText(QPointF(bx + dot_r * 4.2, by + dot_r * 3.0), "REC")

        # Corner ornaments on 4-side and corner modes
        if spec.get("corner_ornament", True) and edge_mode in ("four_sides", "corners"):
            arm_x = bw * 0.35 * length_ratio
            arm_y = bh * 0.35 * length_ratio
            for cx, cy, sx, sy in (
                (bx, by, 1.0, 1.0),
                (bx + bw, by, -1.0, 1.0),
                (bx, by + bh, 1.0, -1.0),
                (bx + bw, by + bh, -1.0, -1.0),
            ):
                _draw_corner_motif(painter, cx, cy, sx, sy, arm_x, arm_y, thickness, recipe, style, brush, c1, c2)
    finally:
        painter.restore()
    return outer_rect


def render_border_preview(spec_or_id: dict[str, Any] | str, width: int = 240, height: int = 240) -> QImage:
    spec = default_border_spec(spec_or_id) if isinstance(spec_or_id, str) else normalize_border_spec(spec_or_id)
    img = QImage(int(width), int(height), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    try:
        # Subtle dark gallery vignette card so light/gold borders stand out crisply in thumbnails
        bg_rect = QRectF(4, 4, width - 8, height - 8)
        bg_grad = QRadialGradient(width * 0.5, height * 0.5, max(width, height) * 0.6)
        bg_grad.setColorAt(0.0, QColor(28, 30, 38, 210))
        bg_grad.setColorAt(1.0, QColor(14, 15, 20, 235))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg_grad)
        p.drawRoundedRect(bg_rect, 12, 12)

        ov_dummy = {
            "x": 10,
            "y": 10,
            "w": width - 20,
            "h": height - 20,
            "opacity": 255,
            "border_spec": spec,
        }
        render_border(p, ov_dummy, current_time=1.0, video_w=width, video_h=height)
    finally:
        p.end()
    return img


def create_auto_border_for_overlay(
    target_ov: dict[str, Any],
    style_id: str = "bd_west_001",
    canvas_w: int = 1080,
    canvas_h: int = 1920,
) -> dict[str, Any]:
    spec = default_border_spec(style_id)
    tx = int(float(target_ov.get("x", 0)))
    ty = int(float(target_ov.get("y", 0)))
    tw = int(float(target_ov.get("w", canvas_w)))
    th = int(float(target_ov.get("h", canvas_h)))
    if target_ov.get("id"):
        spec["target_link"] = str(target_ov["id"])
    else:
        spec["target_link"] = "selected_layer"
    spec["x"], spec["y"], spec["w"], spec["h"] = tx, ty, tw, th
    start = float(target_ov.get("start", 0.0) or 0.0)
    end = float(target_ov.get("end", start + 5.0) or (start + 5.0))
    return {
        "id": f"bd_{uuid.uuid4().hex[:10]}",
        "name": f"自动边框 · {target_ov.get('name') or style_id}",
        "x": tx,
        "y": ty,
        "w": tw,
        "h": th,
        "start": start,
        "end": end,
        "opacity": 255,
        "elements": [],
        "lock_aspect": False,
        "keep_aspect": False,
        "_track_id": target_ov.get("_track_id"),
        "border_spec": spec,
    }


def create_auto_border_for_canvas(
    style_id: str = "bd_west_001",
    canvas_w: int = 1080,
    canvas_h: int = 1920,
    start: float = 0.0,
    duration: float = 5.0,
    track_id: Any = None,
) -> dict[str, Any]:
    spec = default_border_spec(style_id)
    spec["target_link"] = "canvas"
    spec["x"], spec["y"], spec["w"], spec["h"] = 0, 0, int(canvas_w), int(canvas_h)
    return {
        "id": f"bd_{uuid.uuid4().hex[:10]}",
        "name": f"全屏画框 · {get_border_style(style_id)['name']}",
        "x": 0,
        "y": 0,
        "w": int(canvas_w),
        "h": int(canvas_h),
        "start": float(start),
        "end": float(start) + max(0.2, float(duration)),
        "opacity": 255,
        "elements": [],
        "lock_aspect": False,
        "keep_aspect": False,
        "_track_id": track_id,
        "border_spec": spec,
    }
