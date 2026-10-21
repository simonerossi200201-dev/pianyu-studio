# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q143 · 批量操作里「开分屏 + 把轨道放进格子」的共用逻辑。

连线导入、批量加载·视频、中央监视器都用这一份：选一种布局，按顺序把
轨道放进 A、B、C… 格子；格子不够的轨道不分格（计数返回），不会抛错。
"""
from __future__ import annotations

from cutora.render.split_layout import (
    LAYOUT_LABELS,
    LAYOUTS,
    is_valid_layout,
    layout_label_for,
    preset_geometry,
)

SPLIT_OFF = ""
SPLIT_RANDOM_BY_COUNT = "random_by_count"
SPLIT_DEFAULT_LAYOUT = "top_bottom"
# 批量入口给用户选的布局（首项为「按视频数随机分配不同样式（防雷同）」）
SPLIT_CHOICES = (SPLIT_RANDOM_BY_COUNT,) + tuple(l for l in LAYOUTS if l != "custom")
# 右键批量改布局时挂在菜单项上。左右铺满会裁切；左右完整是整幅进画面。
LAYOUT_HINTS = {
    SPLIT_RANDOM_BY_COUNT: "根据每条视频实际对应的轨道数量（1 主 + N 副），从多种对应格子数的直线、斜切、L形与画中画样式库中不重复地随机分配，并打乱副屏顺序，最大程度避免视频形式雷同！",
    "left_right": "左右各一半，铺满格子，超出画面的部分会被裁掉",
    "left_right_fit": "左右各一条，两条都完整放进画面（等大并排），空处垫镜头模糊背景",
    "center_h": "主视频居中占最大横向画面，上方 1 条、下方 1 条外部视频夹心分屏",
    "center_h_tilt": "主视频居中，上下两条横向分割线带倾斜旋转角度，更具动感",
    "top1_bottom2": "主视频在上占最大画面，下方并排 2 个外部加载视频",
    "top1_bottom3": "主视频在上占最大画面，下方并排 3 个外部加载视频",
    "bottom1_top2": "主视频在下占最大画面，上方并排 2 个外部加载视频",
    "bottom1_top3": "主视频在下占最大画面，上方并排 3 个外部加载视频",
    "left1_right2": "主视频在左占最大画面，右侧纵向排 2 个外部加载视频",
    "left1_right3": "主视频在左占最大画面，右侧纵向排 3 个外部加载视频",
    "left1_right4": "主视频在左占最大画面，右侧纵向排 4 个外部加载视频",
    "right1_left2": "主视频在右占最大画面，左侧纵向排 2 个外部加载视频",
    "right1_left3": "主视频在右占最大画面，左侧纵向排 3 个外部加载视频",
    "right1_left4": "主视频在右占最大画面，左侧纵向排 4 个外部加载视频",
    "l_left2_bottom2": "L 形分屏：主视频在右上方，左侧 2 个外部视频、底部 2 个外部视频",
    "l_right2_bottom2": "反 L 形分屏：主视频在左上方，右侧 2 个外部视频、底部 2 个外部视频",
    "l_left2_bottom3": "L 形分屏：主视频在右上方，左侧 2 个外部视频、底部 3 个外部视频",
    "l_left1_bottom2": "L 形分屏：主视频在右上方，左侧 1 个外部视频、底部 2 个外部视频",
    "l_right1_bottom2": "反 L 形分屏：主视频在左上方，右侧 1 个外部视频、底部 2 个外部视频",
    "l_left1_top2": "倒 L 形分屏：主视频在右下方，左侧 1 个外部视频、顶部 2 个外部视频",
    "hex_center_4": "主视频在中央六角形（可调节分段数平滑变圆），四角各放 1 个外部加载视频",
    "shutter_4": "相机快门穿插式分屏：主视频在中央孔径，外部视频呈快门叶片环绕",
    "top1_bottom_auto": "按该视频实际连接的外部视频数量自动分屏：主视频在上方占最大比例，其余外部视频自动排布（4个及以上自动采用L形分屏避免拥挤）",
    "bottom1_top_auto": "按该视频实际连接的外部视频数量自动分屏：主视频在下方占最大比例，其余外部视频自动排布（4个及以上自动采用倒L形分屏避免拥挤）",
    "left1_right_auto": "按该视频实际连接的外部视频数量自动分屏：主视频在左侧占最大比例，其余外部视频在右侧等分",
    "right1_left_auto": "按该视频实际连接的外部视频数量自动分屏：主视频在右侧占最大比例，其余外部视频在左侧等分",
}
BORDER_LABELS = {
    "none": "无", "solid": "实线", "gradient": "渐变", "glass": "毛玻璃", "shadow": "投影",
}


def resolve_auto_layout(layout: str | None, n_secondary: int) -> str:
    """将自动一对多或默认布局按外部视频条数 n_secondary 解析为具体布局名。"""
    n = max(1, int(n_secondary))
    lay = str(layout or SPLIT_DEFAULT_LAYOUT).strip()
    if lay in ("top1_bottom_auto", "top1_bottom4"):
        if n <= 1:
            return "top_bottom"
        if n <= 3:
            return "top1_bottom%d" % n
        nl = max(2, n // 2)
        nb = max(2, n - nl)
        return "l_left%d_bottom%d" % (nl, nb)
    if lay in ("bottom1_top_auto", "bottom1_top4"):
        if n <= 1:
            return "top_bottom"
        if n <= 3:
            return "bottom1_top%d" % n
        nl = max(2, n // 2)
        nb = max(2, n - nl)
        return "l_left%d_top%d" % (nl, nb)
    if lay == "left1_right_auto":
        return "left_right" if n <= 1 else ("left1_right%d" % n)
    if lay == "right1_left_auto":
        return "left_right" if n <= 1 else ("right1_left%d" % n)
    if lay in ("l_shape_auto", "l_left2_bottom3", "l_left1_bottom2", "l_left2_bottom2"):
        nl = max(1, n // 2)
        nb = max(1, n - nl)
        return "l_left%d_bottom%d" % (nl, nb)
    if lay in ("hex_center_auto", "hex_center_4") and n >= 1:
        return "hex_center_%d" % max(2, n)
    if lay in ("shutter_auto", "shutter_4") and n >= 1:
        return "shutter_%d" % max(2, n)
    if lay.startswith("shape_"):
        return "circle_bottom%d" % min(3, n) if n >= 2 else "circle_right1"
    return lay if is_valid_layout(lay) else SPLIT_DEFAULT_LAYOUT


def layout_cells(layout: str) -> list[str]:
    lay = str(layout or SPLIT_DEFAULT_LAYOUT)
    if lay in ("top1_bottom_auto", "bottom1_top_auto", "left1_right_auto", "right1_left_auto", SPLIT_RANDOM_BY_COUNT):
        lay = "left_right" if lay in ("left1_right_auto", "right1_left_auto") else "top_bottom"
    elif lay == "l_shape_auto":
        lay = "l_left2_bottom3"
    elif lay == "hex_center_auto":
        lay = "hex_center_4"
    elif lay == "shutter_auto":
        lay = "shutter_4"
    cells, _divs = preset_geometry(lay)
    return [str(c.get("id")) for c in cells]


def layout_label(layout: str) -> str:
    if str(layout or "") == SPLIT_RANDOM_BY_COUNT:
        return "🎲 按视频数随机分配不同样式（防雷同）"
    return layout_label_for(layout)


def _free_cell(tl, cell_id, keep=None) -> None:
    for tr in getattr(tl, "tracks", []) or []:
        if tr is keep:
            continue
        if str(getattr(tr, "split_cell", None) or "") == str(cell_id):
            tr.split_cell = None


def _candidate_specs_for_count(n_sec: int) -> list[dict]:
    """根据副视频轨道数 n_sec（即总视频数 = 1 主 + n_sec 副）生成严格匹配格子数的多样化分屏样式池。
    默认全部使用干净利落的直线（水平/垂直/微斜切直线），绝不使用弧线；
    且当副视频 >= 4 时，绝不使用 4 个视频挤在同一底排的形式（统一采用 L 形、夹心、侧排、中央孔径等多区布局）。
    """
    n = max(1, int(n_sec))
    if n == 1:
        return [
            {"key": "tb_classic_gold", "label": "✨ 经典黄金直线上下分屏（上主下副）", "layout": "top_bottom",
             "pos": 0.56, "d1_curve": "straight",
             "border": {"style": "gradient", "width_px": 7, "color": "#F3C668", "curve": "straight"}},
            {"key": "tb_classic_amber", "label": "✨ 经典琥珀直线上下分屏（上主下副）", "layout": "top_bottom",
             "pos": 0.55, "d1_curve": "straight",
             "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "slash_tb_pos", "label": "🔪 6° 黄金斜切上下分屏", "layout": "top_bottom",
             "pos": 0.56, "rot_deg": 6.0, "d1_curve": "straight",
             "border": {"style": "gradient", "width_px": 7, "color": "#F3C668", "curve": "straight"}},
            {"key": "slash_tb_neg", "label": "🔪 -6° 反斜切上下分屏", "layout": "top_bottom",
             "pos": 0.56, "rot_deg": -6.0, "d1_curve": "straight",
             "border": {"style": "solid", "width_px": 7, "color": "#E5A93C", "curve": "straight"}},
            {"key": "lr_classic", "label": "📐 经典左右直线分屏（左主右副）", "layout": "left_right",
             "pos": 0.56, "d1_curve": "straight",
             "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "lr_fit", "label": "📐 左右分屏（完整+镜头模糊）", "layout": "left_right_fit",
             "border": {"style": "none", "width_px": 0, "color": "#FFFFFF", "curve": "straight"}},
            {"key": "circle_r1", "label": "⭕ 右下圆润画中画", "layout": "circle_right1",
             "border": {"style": "solid", "width_px": 6, "color": "#F3C668", "curve": "straight"}},
        ]
    if n == 2:
        return [
            {"key": "t1b2", "label": "📐 上 1 下 2 直线分屏（主视频在上）", "layout": "top1_bottom2",
             "pos": 0.56, "d1_curve": "straight",
             "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "t1b2_gold", "label": "✨ 流金上 1 下 2 直线分屏", "layout": "top1_bottom2",
             "pos": 0.57, "d1_curve": "straight",
             "border": {"style": "gradient", "width_px": 7, "color": "#F3C668", "curve": "straight"}},
            {"key": "slash_t1b2_pos", "label": "🔪 6° 斜切上 1 下 2", "layout": "top1_bottom2",
             "pos": 0.56, "rot_deg": 6.0, "d1_curve": "straight",
             "border": {"style": "gradient", "width_px": 7, "color": "#E5A93C", "curve": "straight"}},
            {"key": "slash_t1b2_neg", "label": "🔪 -6° 反斜切上 1 下 2", "layout": "top1_bottom2",
             "pos": 0.56, "rot_deg": -6.0, "d1_curve": "straight",
             "border": {"style": "solid", "width_px": 7, "color": "#F3C668", "curve": "straight"}},
            {"key": "b1t2", "label": "📐 下 1 上 2 直线分屏（主视频在下）", "layout": "bottom1_top2",
             "pos": 0.42, "d1_curve": "straight",
             "border": {"style": "solid", "width_px": 6, "color": "#F3C668", "curve": "straight"}},
            {"key": "l1r2", "label": "📐 左 1 右 2 直线分屏（主视频在左）", "layout": "left1_right2",
             "pos": 0.56, "d1_curve": "straight",
             "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "r1l2", "label": "📐 右 1 左 2 直线分屏（主视频在右）", "layout": "right1_left2",
             "pos": 0.44, "d1_curve": "straight",
             "border": {"style": "solid", "width_px": 6, "color": "#F3C668", "curve": "straight"}},
            {"key": "center_h", "label": "📐 横向直线夹心（上1·中主·下1）", "layout": "center_h",
             "border": {"style": "solid", "width_px": 6, "color": "#F3C668", "curve": "straight"}},
            {"key": "center_h_tilt_pos", "label": "🎞️ 动感斜切夹心 (+6°)", "layout": "center_h_tilt",
             "rot_deg": 6.0,
             "border": {"style": "gradient", "width_px": 7, "color": "#F3C668", "curve": "straight"}},
            {"key": "center_h_tilt_neg", "label": "🎞️ 反斜切夹心 (-6°)", "layout": "center_h_tilt",
             "rot_deg": -6.0,
             "border": {"style": "solid", "width_px": 7, "color": "#E5A93C", "curve": "straight"}},
            {"key": "circle_b2", "label": "⭕ 底部双圆画中画", "layout": "circle_bottom2",
             "border": {"style": "solid", "width_px": 6, "color": "#F3C668", "curve": "straight"}},
            {"key": "circle_r2", "label": "⭕ 右侧双圆画中画", "layout": "circle_right2",
             "border": {"style": "gradient", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
        ]
    if n == 3:
        return [
            {"key": "t1b3", "label": "📐 上 1 下 3 直线分屏（主视频在上）", "layout": "top1_bottom3",
             "pos": 0.56, "d1_curve": "straight",
             "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "t1b3_gold", "label": "✨ 流金上 1 下 3 直线分屏", "layout": "top1_bottom3",
             "pos": 0.57, "d1_curve": "straight",
             "border": {"style": "gradient", "width_px": 7, "color": "#F3C668", "curve": "straight"}},
            {"key": "slash_t1b3", "label": "🔪 6° 黄金斜切上 1 下 3", "layout": "top1_bottom3",
             "pos": 0.56, "rot_deg": 6.0, "d1_curve": "straight",
             "border": {"style": "gradient", "width_px": 7, "color": "#E5A93C", "curve": "straight"}},
            {"key": "slash_t1b3_neg", "label": "🔪 -6° 反斜切上 1 下 3", "layout": "top1_bottom3",
             "pos": 0.56, "rot_deg": -6.0, "d1_curve": "straight",
             "border": {"style": "solid", "width_px": 7, "color": "#F3C668", "curve": "straight"}},
            {"key": "l_left1_bottom2", "label": "📐 L形黄金分屏（右上主+左1下2）", "layout": "l_left1_bottom2",
             "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "l_right1_bottom2", "label": "📐 反L形黄金分屏（左上主+右1下2）", "layout": "l_right1_bottom2",
             "border": {"style": "gradient", "width_px": 6, "color": "#F3C668", "curve": "straight"}},
            {"key": "l_left1_top2", "label": "📐 倒L形黄金分屏（右下主+左1上2）", "layout": "l_left1_top2",
             "border": {"style": "solid", "width_px": 6, "color": "#88E0EF", "curve": "straight"}},
            {"key": "l_right1_top2", "label": "📐 右上L形分屏（左下主+右1上2）", "layout": "l_right1_top2",
             "border": {"style": "gradient", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "b1t3", "label": "📐 下 1 上 3 直线分屏（主视频在下）", "layout": "bottom1_top3",
             "pos": 0.42, "d1_curve": "straight",
             "border": {"style": "solid", "width_px": 6, "color": "#F3C668", "curve": "straight"}},
            {"key": "l1r3", "label": "📐 左 1 右 3 直线分屏（主视频在左）", "layout": "left1_right3",
             "pos": 0.56, "d1_curve": "straight",
             "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "r1l3", "label": "📐 右 1 左 3 直线分屏（主视频在右）", "layout": "right1_left3",
             "pos": 0.44, "d1_curve": "straight",
             "border": {"style": "solid", "width_px": 6, "color": "#F3C668", "curve": "straight"}},
            {"key": "ch_straight_1_2", "label": "📐 横向直线夹心（上1·中主·下2）", "layout": "center_h_1_2",
             "border": {"style": "gradient", "width_px": 7, "color": "#F3C668", "curve": "straight"}},
            {"key": "ch_straight_2_1", "label": "📐 横向直线夹心（上2·中主·下1）", "layout": "center_h_2_1",
             "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "ch_tilt_1_2", "label": "🎞️ 动感斜夹心（上1·中主·下2）", "layout": "center_h_tilt_1_2",
             "rot_deg": 6.0,
             "border": {"style": "gradient", "width_px": 7, "color": "#F3C668", "curve": "straight"}},
            {"key": "ch_tilt_2_1", "label": "🎞️ 反斜切夹心（上2·中主·下1）", "layout": "center_h_tilt_2_1",
             "rot_deg": -6.0,
             "border": {"style": "solid", "width_px": 7, "color": "#E5A93C", "curve": "straight"}},
            {"key": "circle_b3", "label": "⭕ 底部三圆圈画中画", "layout": "circle_bottom3",
             "border": {"style": "solid", "width_px": 6, "color": "#F3C668", "curve": "straight"}},
            {"key": "circle_r3", "label": "⭕ 右侧三圆圈画中画", "layout": "circle_right3",
             "border": {"style": "gradient", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "grid_2x2", "label": "📐 四宫格 2×2", "layout": "grid_2x2",
             "border": {"style": "solid", "width_px": 6, "color": "#F3C668", "curve": "straight"}},
        ]
    if n == 4:
        return [
            {"key": "l_left2_bottom2", "label": "📐 L形黄金分屏（右上主+左2下2）", "layout": "l_left2_bottom2",
             "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "l_right2_bottom2", "label": "📐 反L形黄金分屏（左上主+右2下2）", "layout": "l_right2_bottom2",
             "border": {"style": "gradient", "width_px": 6, "color": "#F3C668", "curve": "straight"}},
            {"key": "l_left2_top2", "label": "📐 倒L形黄金分屏（右下主+左2上2）", "layout": "l_left2_top2",
             "border": {"style": "solid", "width_px": 6, "color": "#88E0EF", "curve": "straight"}},
            {"key": "l_right2_top2", "label": "📐 右上L形分屏（左下主+右2上2）", "layout": "l_right2_top2",
             "border": {"style": "gradient", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "ch_straight_2_2", "label": "📐 横向直线夹心（上2·中主·下2）", "layout": "center_h_2_2",
             "border": {"style": "gradient", "width_px": 7, "color": "#F3C668", "curve": "straight"}},
            {"key": "ch_tilt_2_2_pos", "label": "🎞️ 动感斜夹心 (+6°·上2·中主·下2)", "layout": "center_h_tilt_2_2",
             "rot_deg": 6.0,
             "border": {"style": "gradient", "width_px": 7, "color": "#F3C668", "curve": "straight"}},
            {"key": "ch_tilt_2_2_neg", "label": "🎞️ 反斜切夹心 (-6°·上2·中主·下2)", "layout": "center_h_tilt_2_2",
             "rot_deg": -6.0,
             "border": {"style": "solid", "width_px": 7, "color": "#E5A93C", "curve": "straight"}},
            {"key": "l1r4", "label": "📐 左 1 右 4（主视频在左）", "layout": "left1_right4",
             "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "r1l4", "label": "📐 右 1 左 4（主视频在右）", "layout": "right1_left4",
             "border": {"style": "solid", "width_px": 6, "color": "#F3C668", "curve": "straight"}},
            {"key": "circle_center_4", "label": "⭕ 中央圆窗+四角副屏", "layout": "hex_center_4",
             "sides": 64,
             "border": {"style": "solid", "width_px": 7, "color": "#F5D082", "curve": "straight"}},
            {"key": "hex_center_4", "label": "⬢ 六角中主+四角副屏", "layout": "hex_center_4",
             "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "circle_s4", "label": "⭕ 错落四圆圈画中画", "layout": "circle_scatter4",
             "border": {"style": "gradient", "width_px": 6, "color": "#88E0EF", "curve": "straight"}},
        ]
    # n >= 5 (6 条及以上视频轨道)
    nl = max(2, n // 2)
    nb = max(2, n - nl)
    nt = max(2, n // 2)
    nb_ch = max(2, n - nt)
    return [
        {"key": "l_left%d_bottom%d" % (nl, nb), "label": "📐 L形黄金分屏（右上主+左%d下%d）" % (nl, nb),
         "layout": "l_left%d_bottom%d" % (nl, nb),
         "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
        {"key": "l_right%d_bottom%d" % (nl, nb), "label": "📐 反L形黄金分屏（左上主+右%d下%d）" % (nl, nb),
         "layout": "l_right%d_bottom%d" % (nl, nb),
         "border": {"style": "gradient", "width_px": 6, "color": "#F3C668", "curve": "straight"}},
        {"key": "l_left%d_top%d" % (nl, nb), "label": "📐 倒L形黄金分屏（右下主+左%d上%d）" % (nl, nb),
         "layout": "l_left%d_top%d" % (nl, nb),
         "border": {"style": "solid", "width_px": 6, "color": "#88E0EF", "curve": "straight"}},
        {"key": "l_right%d_top%d" % (nl, nb), "label": "📐 右上L形分屏（左下主+右%d上%d）" % (nl, nb),
         "layout": "l_right%d_top%d" % (nl, nb),
         "border": {"style": "gradient", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
        {"key": "ch_straight_%d_%d" % (nt, nb_ch), "label": "📐 横向直线夹心（上%d·中主·下%d）" % (nt, nb_ch),
         "layout": "center_h_%d_%d" % (nt, nb_ch),
         "border": {"style": "gradient", "width_px": 7, "color": "#F3C668", "curve": "straight"}},
        {"key": "ch_tilt_%d_%d" % (nt, nb_ch), "label": "🎞️ 动感斜夹心（上%d·中主·下%d）" % (nt, nb_ch),
         "layout": "center_h_tilt_%d_%d" % (nt, nb_ch), "rot_deg": 6.0,
         "border": {"style": "gradient", "width_px": 7, "color": "#F3C668", "curve": "straight"}},
        {"key": "l1r%d" % n, "label": "📐 左 1 右 %d（主视频在左）" % n, "layout": "left1_right%d" % n,
         "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
        {"key": "r1l%d" % n, "label": "📐 右 1 左 %d（主视频在右）" % n, "layout": "right1_left%d" % n,
         "border": {"style": "solid", "width_px": 6, "color": "#F3C668", "curve": "straight"}},
        {"key": "circle_s%d" % n, "label": "⭕ 错落%d圆圈画中画" % n, "layout": "circle_scatter%d" % n,
         "border": {"style": "gradient", "width_px": 6, "color": "#88E0EF", "curve": "straight"}},
    ]


def assign_random_split_for_task(
    task: dict,
    tracks: list | None = None,
    *,
    used_history: list[str] | None = None,
    border: dict | None = None,
    feather_px: float | None = None,
    backdrop: dict | None = None,
    shuffle_cells: bool = True,
    face_safe: bool = True,
    rng=None,
) -> dict:
    """根据当前任务的视频轨道数量（1 主 + N 副），随机分配一种严格对应视频数的分屏样式，
    并通过 used_history 保证批量任务之间最大程度不重复（防雷同），同时可随机打乱副屏格子顺序。
    返回 {"layout", "style_key", "style_label", "assigned": {cell: track_id}, "skipped": n, "n_videos": len(valid_tracks)}。
    """
    import random
    from cutora.render.split_layout import (
        CONTAIN_LAYOUTS,
        NEW_BACKDROP,
        _collect_video_tracks_for_split,
        _norm_border,
        apply_layout,
        recompute,
        set_track_cell,
    )
    rgen = rng if rng is not None else random
    if tracks is not None:
        valid_tracks = [t for t in tracks if t is not None]
    else:
        valid_tracks = _collect_video_tracks_for_split(task)
    n_total = len(valid_tracks)
    n_sec = max(1, n_total - 1)
    pool = _candidate_specs_for_count(n_sec)

    prev_sp = task.get("split") if isinstance(task.get("split"), dict) else {}
    prev_key = str(prev_sp.get("random_style_key") or prev_sp.get("layout") or "")
    used_set = set(used_history or [])

    avail = [s for s in pool if s["key"] not in used_set and s["key"] != prev_key]
    if not avail:
        avail = [s for s in pool if s["key"] not in used_set]
    if not avail:
        # 该轨道数的样式池已全部用过一轮：清空同轨道数的已用记录，重新洗牌（仍避开上一条同款）
        pool_keys = {s["key"] for s in pool}
        if isinstance(used_history, list):
            used_history[:] = [k for k in used_history if k not in pool_keys]
        last_used = prev_key
        avail = [s for s in pool if s["key"] != last_used] or list(pool)

    spec = rgen.choice(avail)
    if isinstance(used_history, list):
        used_history.append(spec["key"])

    lay = spec["layout"]
    sp = apply_layout(prev_sp, lay)
    sp["enabled"] = True
    sp["random_style_key"] = spec["key"]
    sp["custom_kind"] = spec["label"]

    # 应用样式专属的几何微调（斜切角度、孔径边数、快门穿插角、主分割线弧线等）
    divs = sp.get("dividers") or []
    if divs:
        d1 = divs[0]
        if "pos" in spec and str(d1.get("axis")) in ("h", "v"):
            d1["pos"] = float(spec["pos"])
        if "rot_deg" in spec:
            d1["rotation_deg"] = float(spec["rot_deg"])
            if len(divs) >= 2 and str(d1.get("ch_role") or "") == "top_h":
                divs[1]["rotation_deg"] = float(spec["rot_deg"])
        if "d1_curve" in spec:
            d1["curve"] = str(spec["d1_curve"])
        if "d2_curve" in spec and len(divs) >= 2:
            divs[1]["curve"] = str(spec["d2_curve"])
        if "sides" in spec and str(d1.get("axis")) == "circle":
            d1["sides"] = int(spec["sides"])
        if "twist_deg" in spec and str(d1.get("axis")) == "circle":
            d1["twist_deg"] = float(spec["twist_deg"])

    f_val = float(feather_px) if (feather_px is not None and float(feather_px) > 0) else 0.0
    for d in divs:
        d["feather_px"] = f_val

    # 边框：若调用方显式指定了非 none 边框则用调用方的，否则使用该样式的推荐精致边框
    user_bstyle = str((border or {}).get("style") or "none")
    if border and user_bstyle != "none":
        merged_b = dict(spec.get("border") or {})
        merged_b.update(border)
        sp["border"] = _norm_border(merged_b)
    else:
        sp["border"] = _norm_border(spec.get("border") or {"style": "solid", "width_px": 6, "color": "#E5A93C"})

    full = isinstance(backdrop, dict) and str(backdrop.get("mode") or "") == "full"
    if isinstance(backdrop, dict) and (lay in CONTAIN_LAYOUTS or full):
        sp["backdrop"] = dict(backdrop)
    elif not isinstance(sp.get("backdrop"), dict):
        sp["backdrop"] = dict(NEW_BACKDROP)

    sp = recompute(sp)
    sp["random_style_key"] = spec["key"]
    sp["custom_kind"] = spec["label"]
    task["split"] = sp

    # 分配格子：主视频固定在 A（若只有 2 格也随机 50% 概率互换 A/B），副视频随机打乱放入 B, C, D...
    cell_ids = [str(c.get("id")) for c in (sp.get("cells") or []) if c.get("id")]
    if shuffle_cells and len(cell_ids) >= 3:
        main_cid = "A" if "A" in cell_ids else cell_ids[0]
        sec_cids = [c for c in cell_ids if c != main_cid]
        rgen.shuffle(sec_cids)
        order = [main_cid] + sec_cids
    else:
        order = list(cell_ids)

    tl = task.get("timeline")
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", "") == "video":
            tr.split_cell = None

    assigned: dict[str, str] = {}
    skipped = 0
    for idx, tr in enumerate(valid_tracks):
        if idx >= len(order):
            tr.split_cell = None
            skipped += 1
            continue
        cid = order[idx]
        _free_cell(tl, cid, keep=tr)
        set_track_cell(task, getattr(tr, "id", ""), cid)
        assigned[cid] = str(getattr(tr, "id", ""))
        for clip in getattr(tr, "clips", []) or []:
            bgf = getattr(clip, "bg_fill", None)
            if isinstance(bgf, dict) and bgf.get("mode") == "stretch":
                bgf["mode"] = "blur"
                bgf["blur_style"] = "lens"
                bgf.setdefault("blur", 45.0)

    if full and order and valid_tracks:
        sp2 = task.get("split") or {}
        if isinstance(sp2.get("backdrop"), dict) and not sp2["backdrop"].get("cell"):
            sp2["backdrop"]["cell"] = order[0]

    face_rep = None
    if face_safe:
        try:
            from cutora.batch.face_safe import calibrate_task_face_safe
            face_rep = calibrate_task_face_safe(task)
            sp_after = task.get("split") or {}
            lay = str(sp_after.get("layout") or lay)
        except Exception:
            pass

    return {
        "layout": lay,
        "style_key": str((task.get("split") or {}).get("random_style_key") or spec["key"]),
        "style_label": str((task.get("split") or {}).get("custom_kind") or spec["label"]),
        "assigned": assigned,
        "skipped": skipped,
        "n_videos": n_total,
        "face_safe": face_rep,
    }


def assign_split(task: dict, layout: str, tracks: list, *, border: dict | None = None,
                 feather_px: float | None = None, cells: list[str] | None = None,
                 backdrop: dict | None = None, used_history: list[str] | None = None,
                 face_safe: bool = True) -> dict:
    """开分屏并按顺序分格子。

    tracks  依次放进 cells（默认是布局里的 A、B、C…）；None 跳过那一格。
    若 layout == SPLIT_RANDOM_BY_COUNT，则按该任务的视频数量随机分配不重复的分屏样式。
    返回 {"layout", "assigned": {cell: track_id}, "skipped": n}。
    """
    if str(layout or "") == SPLIT_RANDOM_BY_COUNT:
        return assign_random_split_for_task(
            task, tracks,
            used_history=used_history,
            border=border,
            feather_px=feather_px,
            backdrop=backdrop,
            shuffle_cells=True,
            face_safe=face_safe,
        )
    from cutora.render.split_layout import apply_split_to_task, set_track_cell
    valid_tracks = [t for t in (tracks or []) if t is not None]
    if layout in ("top1_bottom_auto", "left1_right_auto"):
        lay = resolve_auto_layout(layout, max(1, len(valid_tracks) - 1))
    else:
        lay = layout if is_valid_layout(layout) else SPLIT_DEFAULT_LAYOUT
    from cutora.render.split_layout import CONTAIN_LAYOUTS
    # Q155：「单个视频撑满整幅」任何布局都要写；「每格各自」只有左右完整需要写（其余布局靠片段自己的填充）
    full = isinstance(backdrop, dict) and str(backdrop.get("mode") or "") == "full"
    bd = None
    if isinstance(backdrop, dict) and (lay in CONTAIN_LAYOUTS or full):
        bd = dict(backdrop)
    sp = apply_split_to_task(task, layout=lay, enabled=True, border=border, backdrop=bd)
    actual_lay = str(sp.get("layout") or lay)
    if feather_px is not None and float(feather_px) > 0:
        for d in sp.get("dividers") or []:
            d["feather_px"] = max(0.0, float(feather_px))
        from cutora.render.split_layout import recompute
        task["split"] = recompute(sp)
    order = list(cells or layout_cells(actual_lay))
    tl = task.get("timeline")
    assigned: dict[str, str] = {}
    skipped = 0
    slot = 0
    for tr in tracks:
        if tr is None:
            slot += 1
            continue
        if slot >= len(order):
            tr.split_cell = None
            skipped += 1
            continue
        cid = order[slot]
        slot += 1
        _free_cell(tl, cid, keep=tr)
        set_track_cell(task, getattr(tr, "id", ""), cid)
        assigned[cid] = str(getattr(tr, "id", ""))
    if full and not (bd or {}).get("cell") and order and tracks and tracks[0] is not None:
        # 整幅垫原视频：tracks 第一条就是原视频，它进了哪一格就用哪一格
        sp2 = task.get("split") or {}
        if isinstance(sp2.get("backdrop"), dict):
            sp2["backdrop"]["cell"] = order[0]
    face_rep = None
    if face_safe:
        try:
            from cutora.batch.face_safe import calibrate_task_face_safe
            face_rep = calibrate_task_face_safe(task)
        except Exception:
            pass
    return {"layout": actual_lay, "assigned": assigned, "skipped": skipped, "face_safe": face_rep}

