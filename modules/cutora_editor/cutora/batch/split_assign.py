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
    SPLIT_RANDOM_BY_COUNT: "根据每条视频实际对应的轨道数量（1 主 + N 副），从 20+ 种对应格子数的分屏样式库中不重复地随机分配，并打乱副屏顺序，最大程度避免视频形式雷同！",
    "left_right": "左右各一半，铺满格子，超出画面的部分会被裁掉",
    "left_right_fit": "左右各一条，两条都完整放进画面（等大并排），空处垫镜头模糊背景",
    "center_h": "主视频居中占最大横向画面，上方 1 条、下方 1 条外部视频夹心分屏",
    "center_h_tilt": "主视频居中，上下两条横向分割线带倾斜旋转角度，更具动感",
    "top1_bottom2": "主视频在上占最大画面，下方并排 2 个外部加载视频",
    "top1_bottom3": "主视频在上占最大画面，下方并排 3 个外部加载视频",
    "top1_bottom4": "主视频在上占最大画面，下方并排 4 个外部加载视频",
    "left1_right2": "主视频在左占最大画面，右侧纵向排 2 个外部加载视频",
    "left1_right3": "主视频在左占最大画面，右侧纵向排 3 个外部加载视频",
    "left1_right4": "主视频在左占最大画面，右侧纵向排 4 个外部加载视频",
    "l_left2_bottom3": "L 形分屏：主视频在右上方，左侧 2 个外部视频、底部 3 个外部视频",
    "l_left1_bottom2": "L 形分屏：主视频在右上方，左侧 1 个外部视频、底部 2 个外部视频",
    "hex_center_4": "主视频在中央六角形（可调节分段数平滑变圆），四角各放 1 个外部加载视频",
    "shutter_4": "相机快门穿插式分屏：主视频在中央孔径，外部视频呈快门叶片穿插环绕",
    "shape_heart_3": "心形气泡分屏：主视频铺底，外部视频呈心形画中画",
    "shape_dove_3": "和平鸽形分屏：主视频铺底，外部视频呈展翅鸽子形画中画",
    "shape_clover_3": "四叶幸运草形分屏：主视频铺底，外部视频呈幸运草画中画",
    "shape_olive_3": "橄榄叶形分屏：主视频铺底，外部视频呈橄榄叶画中画",
    "top1_bottom_auto": "按该视频实际连接的外部视频数量自动分屏：主视频在上方占最大比例，其余外部视频在下方等分",
    "left1_right_auto": "按该视频实际连接的外部视频数量自动分屏：主视频在左侧占最大比例，其余外部视频在右侧等分",
}
BORDER_LABELS = {
    "none": "无", "solid": "实线", "gradient": "渐变", "glass": "毛玻璃", "shadow": "投影",
}


def resolve_auto_layout(layout: str | None, n_secondary: int) -> str:
    """将自动一对多或默认布局按外部视频条数 n_secondary 解析为具体布局名。"""
    n = max(1, int(n_secondary))
    lay = str(layout or SPLIT_DEFAULT_LAYOUT).strip()
    if lay == "top1_bottom_auto":
        return "top_bottom" if n <= 1 else ("top1_bottom%d" % n)
    if lay == "left1_right_auto":
        return "left_right" if n <= 1 else ("left1_right%d" % n)
    if lay in ("l_shape_auto", "l_left2_bottom3", "l_left1_bottom2"):
        if lay == "l_shape_auto" or n != (5 if lay == "l_left2_bottom3" else 3):
            nl = max(1, n // 2)
            nb = max(1, n - nl)
            return "l_left%d_bottom%d" % (nl, nb)
    if lay in ("hex_center_auto", "hex_center_4") and n >= 1:
        return "hex_center_%d" % max(2, n)
    if lay in ("shutter_auto", "shutter_4") and n >= 1:
        return "shutter_%d" % max(2, n)
    if lay.startswith("shape_") and "_" in lay[6:]:
        parts = lay.split("_")
        if len(parts) == 3 and parts[2].isdigit():
            return "shape_%s_%d" % (parts[1], n)
    return lay if is_valid_layout(lay) else SPLIT_DEFAULT_LAYOUT


def layout_cells(layout: str) -> list[str]:
    lay = str(layout or SPLIT_DEFAULT_LAYOUT)
    if lay in ("top1_bottom_auto", "left1_right_auto", SPLIT_RANDOM_BY_COUNT):
        lay = "top_bottom" if lay != "left1_right_auto" else "left_right"
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
    每一项包含:
      - key: 唯一样式标识（用于跨任务防雷同去重）
      - label: 中文样式名
      - layout: 基础布局名
      - rot_deg / pos / curve / border / sides / twist_deg: 可选几何与边框修饰
    """
    n = max(1, int(n_sec))
    if n == 1:
        return [
            {"key": "tb_classic", "label": "上下分屏", "layout": "top_bottom",
             "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "lr_classic", "label": "左右分屏（铺满）", "layout": "left_right",
             "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "lr_fit", "label": "左右分屏（完整+镜头模糊）", "layout": "left_right_fit",
             "border": {"style": "none", "width_px": 0, "color": "#FFFFFF", "curve": "straight"}},
            {"key": "slash_tb_pos", "label": "🔪 11° 黄金斜切上下分屏", "layout": "top_bottom",
             "pos": 0.56, "rot_deg": 11.0,
             "border": {"style": "gradient", "width_px": 7, "color": "#F3C668", "curve": "straight"}},
            {"key": "slash_tb_neg", "label": "🔪 -11° 反斜切上下分屏", "layout": "top_bottom",
             "pos": 0.56, "rot_deg": -11.0,
             "border": {"style": "solid", "width_px": 7, "color": "#E5A93C", "curve": "straight"}},
            {"key": "slash_lr_pos", "label": "🔪 12° 斜切左右分屏", "layout": "left_right",
             "pos": 0.55, "rot_deg": 12.0,
             "border": {"style": "shadow", "width_px": 7, "color": "#E5A93C", "curve": "straight"}},
            {"key": "wave_tb", "label": "🌊 波浪流线上下分屏", "layout": "top_bottom",
             "pos": 0.55, "rot_deg": -5.0,
             "border": {"style": "solid", "width_px": 6, "color": "#88E0EF", "curve": "wave"}},
            {"key": "arc_tb", "label": "🌈 电影弧线上下分屏", "layout": "top_bottom",
             "pos": 0.56, "rot_deg": 6.0,
             "border": {"style": "gradient", "width_px": 7, "color": "#F3C668", "curve": "arc"}},
            {"key": "heart_1", "label": "❤️ 心形画中画分屏", "layout": "shape_heart_1",
             "border": {"style": "solid", "width_px": 6, "color": "#FF6B8B", "curve": "straight"}},
            {"key": "dove_1", "label": "🕊️ 和平鸽画中画分屏", "layout": "shape_dove_1",
             "border": {"style": "gradient", "width_px": 6, "color": "#88E0EF", "curve": "straight"}},
            {"key": "clover_1", "label": "🍀 幸运草画中画分屏", "layout": "shape_clover_1",
             "border": {"style": "solid", "width_px": 6, "color": "#59CE8F", "curve": "straight"}},
            {"key": "circle_r1", "label": "⭕ 右侧圆圈画中画", "layout": "circle_right1",
             "border": {"style": "solid", "width_px": 6, "color": "#F3C668", "curve": "straight"}},
        ]
    if n == 2:
        return [
            {"key": "t1b2", "label": "上 1 下 2（主视频在上）", "layout": "top1_bottom2",
             "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "l1r2", "label": "左 1 右 2（主视频在左）", "layout": "left1_right2",
             "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "center_h", "label": "横向夹心（上1·中主·下1）", "layout": "center_h",
             "border": {"style": "solid", "width_px": 6, "color": "#F3C668", "curve": "straight"}},
            {"key": "center_h_tilt_pos", "label": "🎞️ 动感斜夹心 (+9°)", "layout": "center_h_tilt",
             "rot_deg": 9.0,
             "border": {"style": "gradient", "width_px": 7, "color": "#F3C668", "curve": "straight"}},
            {"key": "center_h_tilt_neg", "label": "🎞️ 动感反斜夹心 (-9°)", "layout": "center_h_tilt",
             "rot_deg": -9.0,
             "border": {"style": "solid", "width_px": 7, "color": "#E5A93C", "curve": "straight"}},
            {"key": "center_h_wave", "label": "🌊 斜波浪横向夹心", "layout": "center_h_tilt",
             "rot_deg": -7.0,
             "border": {"style": "solid", "width_px": 6, "color": "#88E0EF", "curve": "wave"}},
            {"key": "slash_t1b2", "label": "🔪 10° 斜切上 1 下 2", "layout": "top1_bottom2",
             "pos": 0.58, "rot_deg": 10.0,
             "border": {"style": "gradient", "width_px": 7, "color": "#E5A93C", "curve": "straight"}},
            {"key": "slash_l1r2", "label": "🔪 -11° 斜切左 1 右 2", "layout": "left1_right2",
             "pos": 0.58, "rot_deg": -11.0,
             "border": {"style": "shadow", "width_px": 7, "color": "#E5A93C", "curve": "straight"}},
            {"key": "heart_2", "label": "❤️ 双心形气泡分屏", "layout": "shape_heart_2",
             "border": {"style": "solid", "width_px": 6, "color": "#FF6B8B", "curve": "straight"}},
            {"key": "dove_2", "label": "🕊️ 双和平鸽异形分屏", "layout": "shape_dove_2",
             "border": {"style": "gradient", "width_px": 6, "color": "#88E0EF", "curve": "straight"}},
            {"key": "clover_2", "label": "🍀 双幸运草创意分屏", "layout": "shape_clover_2",
             "border": {"style": "solid", "width_px": 6, "color": "#59CE8F", "curve": "straight"}},
            {"key": "olive_2", "label": "🌿 双橄榄叶流线分屏", "layout": "shape_olive_2",
             "border": {"style": "gradient", "width_px": 6, "color": "#A6E3E9", "curve": "straight"}},
            {"key": "circle_b2", "label": "⭕ 底部双圆圈画中画", "layout": "circle_bottom2",
             "border": {"style": "solid", "width_px": 6, "color": "#F3C668", "curve": "straight"}},
            {"key": "circle_r2", "label": "⭕ 右侧双圆圈画中画", "layout": "circle_right2",
             "border": {"style": "gradient", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "hex_center_2", "label": "⬢ 六角中主+双副屏", "layout": "hex_center_2",
             "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "shutter_2", "label": "📷 双叶快门穿插分屏", "layout": "shutter_2",
             "border": {"style": "gradient", "width_px": 6, "color": "#F3C668", "curve": "straight"}},
        ]
    if n == 3:
        return [
            {"key": "t1b3", "label": "上 1 下 3（主视频在上）", "layout": "top1_bottom3",
             "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "l1r3", "label": "左 1 右 3（主视频在左）", "layout": "left1_right3",
             "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "l_1_2", "label": "📐 L形黄金分屏（右上主+左1下2）", "layout": "l_left1_bottom2",
             "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "grid_2x2", "label": "四宫格 2×2", "layout": "grid_2x2",
             "border": {"style": "solid", "width_px": 6, "color": "#F3C668", "curve": "straight"}},
            {"key": "ch_tilt_1_2", "label": "🎞️ 动感斜夹心（上1·中主·下2）", "layout": "center_h_tilt_1_2",
             "rot_deg": 9.0,
             "border": {"style": "gradient", "width_px": 7, "color": "#F3C668", "curve": "straight"}},
            {"key": "ch_tilt_2_1", "label": "🎞️ 反斜切夹心（上2·中主·下1）", "layout": "center_h_tilt_2_1",
             "rot_deg": -9.0,
             "border": {"style": "solid", "width_px": 7, "color": "#E5A93C", "curve": "straight"}},
            {"key": "ch_1_2", "label": "横向夹心（上1·中主·下2）", "layout": "center_h_1_2",
             "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "ch_wave_1_2", "label": "🌊 波浪横向夹心（上1·中主·下2）", "layout": "center_h_tilt_1_2",
             "rot_deg": -7.0,
             "border": {"style": "solid", "width_px": 6, "color": "#88E0EF", "curve": "wave"}},
            {"key": "hex_center_3", "label": "⬢ 六角中主+三副屏", "layout": "hex_center_3",
             "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "circle_center_3", "label": "⭕ 光滑圆中主+三副毛玻璃分屏", "layout": "hex_center_3",
             "sides": 64,
             "border": {"style": "glass", "width_px": 9, "color": "#F5D082", "curve": "straight"}},
            {"key": "shutter_3", "label": "📷 相机快门穿插（三叶六角孔径）", "layout": "shutter_3",
             "twist_deg": 38.0,
             "border": {"style": "gradient", "width_px": 6, "color": "#F3C668", "curve": "straight"}},
            {"key": "shutter_iris_3", "label": "📷 旋风弧叶快门（八角虹膜三叶）", "layout": "shutter_3",
             "sides": 8, "twist_deg": 42.0,
             "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "arc"}},
            {"key": "heart_3", "label": "❤️ 浪漫三心形气泡分屏", "layout": "shape_heart_3",
             "border": {"style": "solid", "width_px": 6, "color": "#FF6B8B", "curve": "straight"}},
            {"key": "dove_3", "label": "🕊️ 灵动三和平鸽异形分屏", "layout": "shape_dove_3",
             "border": {"style": "gradient", "width_px": 6, "color": "#88E0EF", "curve": "straight"}},
            {"key": "clover_3", "label": "🍀 四叶幸运草三副分屏", "layout": "shape_clover_3",
             "border": {"style": "solid", "width_px": 6, "color": "#59CE8F", "curve": "straight"}},
            {"key": "olive_3", "label": "🌿 橄榄叶流线三副分屏", "layout": "shape_olive_3",
             "border": {"style": "gradient", "width_px": 6, "color": "#A6E3E9", "curve": "straight"}},
            {"key": "pentagon_3", "label": "⬠ 五边形几何三副分屏", "layout": "shape_pentagon_3",
             "border": {"style": "solid", "width_px": 6, "color": "#F3C668", "curve": "straight"}},
            {"key": "star_3", "label": "⭐ 璀璨五角星三副分屏", "layout": "shape_star_3",
             "border": {"style": "solid", "width_px": 6, "color": "#FFD966", "curve": "straight"}},
            {"key": "flower_3", "label": "🌸 国风海棠花窗三副分屏", "layout": "shape_flower_3",
             "border": {"style": "gradient", "width_px": 6, "color": "#F9B5D0", "curve": "straight"}},
            {"key": "butterfly_3", "label": "🦋 灵动蝴蝶三副分屏", "layout": "shape_butterfly_3",
             "border": {"style": "gradient", "width_px": 6, "color": "#D4ADFC", "curve": "straight"}},
            {"key": "circle_b3", "label": "⭕ 底部三圆圈画中画", "layout": "circle_bottom3",
             "border": {"style": "solid", "width_px": 6, "color": "#F3C668", "curve": "straight"}},
            {"key": "circle_r3", "label": "⭕ 右侧三圆圈画中画", "layout": "circle_right3",
             "border": {"style": "gradient", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "circle_s3", "label": "⭕ 错落三圆圈画中画", "layout": "circle_scatter3",
             "border": {"style": "solid", "width_px": 6, "color": "#88E0EF", "curve": "straight"}},
            {"key": "slash_t1b3", "label": "🔪 11° 黄金斜切上 1 下 3", "layout": "top1_bottom3",
             "pos": 0.58, "rot_deg": 11.0,
             "border": {"style": "gradient", "width_px": 7, "color": "#E5A93C", "curve": "straight"}},
            {"key": "slash_l1r3", "label": "🔪 -12° 反斜切左 1 右 3", "layout": "left1_right3",
             "pos": 0.58, "rot_deg": -12.0,
             "border": {"style": "shadow", "width_px": 7, "color": "#E5A93C", "curve": "straight"}},
        ]
    if n == 4:
        return [
            {"key": "t1b4", "label": "上 1 下 4（主视频在上）", "layout": "top1_bottom4",
             "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "l1r4", "label": "左 1 右 4（主视频在左）", "layout": "left1_right4",
             "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "l_2_2", "label": "📐 L形黄金分屏（右上主+左2下2）", "layout": "l_left2_bottom2",
             "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "l_1_3", "label": "📐 L形黄金分屏（右上主+左1下3）", "layout": "l_left1_bottom3",
             "border": {"style": "gradient", "width_px": 6, "color": "#F3C668", "curve": "straight"}},
            {"key": "hex_center_4", "label": "⬢ 六角中主+四角副屏", "layout": "hex_center_4",
             "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "circle_center_4", "label": "⭕ 光滑圆中主+四角毛玻璃分屏", "layout": "hex_center_4",
             "sides": 64,
             "border": {"style": "glass", "width_px": 9, "color": "#F5D082", "curve": "straight"}},
            {"key": "shutter_4", "label": "📷 相机快门穿插（四叶六角孔径）", "layout": "shutter_4",
             "twist_deg": 38.0,
             "border": {"style": "gradient", "width_px": 6, "color": "#F3C668", "curve": "straight"}},
            {"key": "shutter_iris_4", "label": "📷 旋风弧叶快门（八角四叶虹膜）", "layout": "shutter_4",
             "sides": 8, "twist_deg": 42.0,
             "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "arc"}},
            {"key": "shutter_diamond_4", "label": "📷 菱形反向快门穿插分屏", "layout": "shutter_4",
             "sides": 4, "twist_deg": -36.0,
             "border": {"style": "shadow", "width_px": 7, "color": "#E5A93C", "curve": "straight"}},
            {"key": "ch_tilt_2_2", "label": "🎞️ 动感斜夹心（上2·中主·下2）", "layout": "center_h_tilt_2_2",
             "rot_deg": 9.0,
             "border": {"style": "gradient", "width_px": 7, "color": "#F3C668", "curve": "straight"}},
            {"key": "ch_2_2", "label": "横向夹心（上2·中主·下2）", "layout": "center_h_2_2",
             "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": "heart_4", "label": "❤️ 四心形气泡分屏", "layout": "shape_heart_4",
             "border": {"style": "solid", "width_px": 6, "color": "#FF6B8B", "curve": "straight"}},
            {"key": "dove_4", "label": "🕊️ 四和平鸽异形分屏", "layout": "shape_dove_4",
             "border": {"style": "gradient", "width_px": 6, "color": "#88E0EF", "curve": "straight"}},
            {"key": "clover_4", "label": "🍀 四幸运草创意分屏", "layout": "shape_clover_4",
             "border": {"style": "solid", "width_px": 6, "color": "#59CE8F", "curve": "straight"}},
            {"key": "olive_4", "label": "🌿 四橄榄叶流线分屏", "layout": "shape_olive_4",
             "border": {"style": "gradient", "width_px": 6, "color": "#A6E3E9", "curve": "straight"}},
            {"key": "circle_b4", "label": "⭕ 底部四圆圈画中画", "layout": "circle_bottom4",
             "border": {"style": "solid", "width_px": 6, "color": "#F3C668", "curve": "straight"}},
            {"key": "circle_s4", "label": "⭕ 错落四圆圈画中画", "layout": "circle_scatter4",
             "border": {"style": "gradient", "width_px": 6, "color": "#88E0EF", "curve": "straight"}},
            {"key": "slash_t1b4", "label": "🔪 10° 斜切上 1 下 4", "layout": "top1_bottom4",
             "pos": 0.58, "rot_deg": 10.0,
             "border": {"style": "gradient", "width_px": 7, "color": "#E5A93C", "curve": "straight"}},
        ]
    # n >= 5 (6 条及以上视频轨道)
    nl = max(2, n // 2)
    nb = max(2, n - nl)
    nt = max(2, n // 2)
    nb_ch = max(2, n - nt)
    return [
        {"key": "l_%d_%d" % (nl, nb), "label": "📐 L形黄金分屏（左%d+下%d）" % (nl, nb),
         "layout": "l_left%d_bottom%d" % (nl, nb),
         "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
        {"key": "t1b%d" % n, "label": "上 1 下 %d" % n, "layout": "top1_bottom%d" % n,
         "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
        {"key": "l1r%d" % n, "label": "左 1 右 %d" % n, "layout": "left1_right%d" % n,
         "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
        {"key": "ch_tilt_%d_%d" % (nt, nb_ch), "label": "🎞️ 动感斜夹心（上%d·中主·下%d）" % (nt, nb_ch),
         "layout": "center_h_tilt_%d_%d" % (nt, nb_ch), "rot_deg": 9.0,
         "border": {"style": "gradient", "width_px": 7, "color": "#F3C668", "curve": "straight"}},
        {"key": "ch_%d_%d" % (nt, nb_ch), "label": "横向夹心（上%d·中主·下%d）" % (nt, nb_ch),
         "layout": "center_h_%d_%d" % (nt, nb_ch),
         "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
        {"key": "hex_center_%d" % n, "label": "⬢ 六角中主+%d副屏" % n, "layout": "hex_center_%d" % n,
         "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
        {"key": "shutter_%d" % n, "label": "📷 相机快门穿插（%d叶）" % n, "layout": "shutter_%d" % n,
         "twist_deg": 38.0,
         "border": {"style": "gradient", "width_px": 6, "color": "#F3C668", "curve": "straight"}},
        {"key": "heart_%d" % n, "label": "❤️ %d心形气泡分屏" % n, "layout": "shape_heart_%d" % n,
         "border": {"style": "solid", "width_px": 6, "color": "#FF6B8B", "curve": "straight"}},
        {"key": "dove_%d" % n, "label": "🕊️ %d和平鸽异形分屏" % n, "layout": "shape_dove_%d" % n,
         "border": {"style": "gradient", "width_px": 6, "color": "#88E0EF", "curve": "straight"}},
        {"key": "clover_%d" % n, "label": "🍀 %d幸运草分屏" % n, "layout": "shape_clover_%d" % n,
         "border": {"style": "solid", "width_px": 6, "color": "#59CE8F", "curve": "straight"}},
        {"key": "circle_b%d" % n, "label": "⭕ 底部%d圆圈画中画" % n, "layout": "circle_bottom%d" % n,
         "border": {"style": "solid", "width_px": 6, "color": "#F3C668", "curve": "straight"}},
        {"key": "circle_s%d" % n, "label": "⭕ 错落%d圆圈画中画" % n, "layout": "circle_scatter%d" % n,
         "border": {"style": "gradient", "width_px": 6, "color": "#88E0EF", "curve": "straight"}},
        {"key": "slash_t1b%d" % n, "label": "🔪 10° 斜切上 1 下 %d" % n, "layout": "top1_bottom%d" % n,
         "pos": 0.58, "rot_deg": 10.0,
         "border": {"style": "gradient", "width_px": 7, "color": "#E5A93C", "curve": "straight"}},
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

    # 应用样式专属的几何微调（斜切角度、孔径边数、快门穿插角等）
    divs = sp.get("dividers") or []
    if divs:
        d1 = divs[0]
        if "pos" in spec and str(d1.get("axis")) in ("h", "v"):
            d1["pos"] = float(spec["pos"])
        if "rot_deg" in spec:
            d1["rotation_deg"] = float(spec["rot_deg"])
            if len(divs) >= 2 and str(d1.get("ch_role") or "") == "top_h":
                divs[1]["rotation_deg"] = float(spec["rot_deg"])
        if "sides" in spec and str(d1.get("axis")) == "circle":
            d1["sides"] = int(spec["sides"])
        if "twist_deg" in spec and str(d1.get("axis")) == "circle":
            d1["twist_deg"] = float(spec["twist_deg"])

    if feather_px is not None and float(feather_px) > 0:
        for d in divs:
            d["feather_px"] = float(feather_px)

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

    return {
        "layout": lay,
        "style_key": spec["key"],
        "style_label": spec["label"],
        "assigned": assigned,
        "skipped": skipped,
        "n_videos": n_total,
    }


def assign_split(task: dict, layout: str, tracks: list, *, border: dict | None = None,
                 feather_px: float | None = None, cells: list[str] | None = None,
                 backdrop: dict | None = None, used_history: list[str] | None = None) -> dict:
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
    return {"layout": actual_lay, "assigned": assigned, "skipped": skipped}

