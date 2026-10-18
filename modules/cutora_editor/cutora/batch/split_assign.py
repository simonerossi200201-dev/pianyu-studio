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
SPLIT_DEFAULT_LAYOUT = "top_bottom"
# 批量入口给用户选的布局（custom 是拖出来的，不放进下拉）
SPLIT_CHOICES = tuple(l for l in LAYOUTS if l != "custom")
# 右键批量改布局时挂在菜单项上。左右铺满会裁切；左右完整是整幅进画面。
LAYOUT_HINTS = {
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
        return "hex_center_%d" % n
    if lay in ("shutter_auto", "shutter_4") and n >= 1:
        return "shutter_%d" % n
    if lay.startswith("shape_") and "_" in lay[6:]:
        parts = lay.split("_")
        if len(parts) == 3 and parts[2].isdigit():
            return "shape_%s_%d" % (parts[1], n)
    return lay if is_valid_layout(lay) else SPLIT_DEFAULT_LAYOUT


def layout_cells(layout: str) -> list[str]:
    lay = str(layout or SPLIT_DEFAULT_LAYOUT)
    if lay in ("top1_bottom_auto", "left1_right_auto"):
        lay = "top_bottom" if lay == "top1_bottom_auto" else "left_right"
    elif lay == "l_shape_auto":
        lay = "l_left2_bottom3"
    elif lay == "hex_center_auto":
        lay = "hex_center_4"
    elif lay == "shutter_auto":
        lay = "shutter_4"
    cells, _divs = preset_geometry(lay)
    return [str(c.get("id")) for c in cells]


def layout_label(layout: str) -> str:
    return layout_label_for(layout)


def _free_cell(tl, cell_id, keep=None) -> None:
    for tr in getattr(tl, "tracks", []) or []:
        if tr is keep:
            continue
        if str(getattr(tr, "split_cell", None) or "") == str(cell_id):
            tr.split_cell = None


def assign_split(task: dict, layout: str, tracks: list, *, border: dict | None = None,
                 feather_px: float | None = None, cells: list[str] | None = None,
                 backdrop: dict | None = None) -> dict:
    """开分屏并按顺序分格子。

    tracks  依次放进 cells（默认是布局里的 A、B、C…）；None 跳过那一格。
    返回 {"layout", "assigned": {cell: track_id}, "skipped": n}。
    """
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
    if feather_px is not None:
        for d in sp.get("dividers") or []:
            d["feather_px"] = max(0.0, float(feather_px))
        from cutora.render.split_layout import recompute
        task["split"] = recompute(sp)
    order = list(cells or layout_cells(lay))
    tl = task.get("timeline")
    assigned: dict[str, str] = {}
    skipped = 0
    slot = 0
    for tr in tracks:
        if tr is None:
            slot += 1
            continue
        if slot >= len(order):
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
    return {"layout": lay, "assigned": assigned, "skipped": skipped}
