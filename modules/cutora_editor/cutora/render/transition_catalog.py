# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""转场统一元数据表（分类 / 推荐时长 / 配套音效 / 搜索关键词）。

所有转场面板、时间线默认时长、音效配对都从这里读，不在 UI 里各写一份。
新增转场只需在 ``TRANSITION_META`` 里补一行。未登记的转场走 ``DEFAULT_META``。
"""
from __future__ import annotations

import re

_SCOPE_SUFFIX_RE = re.compile(r"_(in|out|both)$", re.IGNORECASE)

# 分组 id → 显示名（顺序即面板分组顺序）
CATEGORIES: list[tuple[str, str]] = [
    ("hot", "热门"),
    ("camera", "运镜"),
    ("rhythm", "节奏卡点"),
    ("graphic", "图形"),
    ("light", "光效胶片"),
    ("motion", "推拉滑动"),
    ("wipe", "擦除遮罩"),
    ("dissolve", "溶解渐变"),
    ("fx", "特效"),
    ("audio", "音频"),
]

DEFAULT_META: dict = {
    "category": "fx",
    "dur": 0.35,          # 推荐时长（秒）
    "sfx": "",            # 推荐音效 id（见 cutora.audio.sfx_social），空 = 无
    "keywords": "",       # 额外搜索词（中文 / 拼音首字母 / 英文，空格分隔）
    "hot": False,         # 是否同时出现在「热门」分组
    "sample": "",         # 预览画面对 id（见 resources/transition_samples），空 = 按分组默认
}

# preset → 元数据（只写与 DEFAULT_META 不同的字段）
TRANSITION_META: dict[str, dict] = {
    # ─── A. 运镜类 (camera，12 款新转场 + 既有运镜转场) ─────────────────────
    "cam_crash_push": {
        "category": "camera",
        "dur": 0.65,
        "sfx": "zoom_in_swell",
        "keywords": "急推 冲镜 推进 变焦 拉近 crash push zoom in yj jtcj",
        "hot": True,
        "sample": "camera",
    },
    "cam_pull_back": {
        "category": "camera",
        "dur": 0.70,
        "sfx": "zoom_out_swell",
        "keywords": "急拉 退镜 拉远 广角 pull back zoom out jltj",
        "hot": True,
        "sample": "camera",
    },
    "cam_whip_right": {
        "category": "camera",
        "dur": 0.60,
        "sfx": "swish_fast",
        "keywords": "极速 横甩 甩镜 右甩 摇镜 whip pan right jshs",
        "hot": True,
        "sample": "camera",
    },
    "cam_whip_down": {
        "category": "camera",
        "dur": 0.60,
        "sfx": "whoosh_drop",
        "keywords": "极速 纵甩 下甩 俯仰 甩镜 whip down vertical jszs",
        "hot": False,
        "sample": "camera",
    },
    "cam_whip_diag": {
        "category": "camera",
        "dur": 0.65,
        "sfx": "whoosh_heavy",
        "keywords": "对角 甩镜 斜甩 倾斜 diagonal whip pan djss",
        "hot": False,
        "sample": "camera",
    },
    "cam_roll_cw": {
        "category": "camera",
        "dur": 0.75,
        "sfx": "whoosh_rise",
        "keywords": "顺时针 滚筒 旋转 翻滚 barrel roll cw sszgt",
        "hot": True,
        "sample": "camera",
    },
    "cam_roll_ccw": {
        "category": "camera",
        "dur": 0.75,
        "sfx": "whoosh_drop",
        "keywords": "逆时针 滚筒 旋转 翻滚 barrel roll ccw nszgt",
        "hot": False,
        "sample": "camera",
    },
    "cam_zoom_spin": {
        "category": "camera",
        "dur": 0.80,
        "sfx": "whoosh_heavy",
        "keywords": "推进 旋涡 旋转 螺旋 变焦 zoom spin vortex tjxw",
        "hot": True,
        "sample": "camera",
    },
    "cam_dolly_vertigo": {
        "category": "camera",
        "dur": 0.90,
        "sfx": "riser_short",
        "keywords": "希区柯克 变焦 眩晕 推轨 dolly zoom vertigo hitchcock xqkk",
        "hot": True,
        "sample": "camera",
    },
    "cam_pendulum": {
        "category": "camera",
        "dur": 0.85,
        "sfx": "air_whoosh",
        "keywords": "钟摆 摇镜 摆动 弧线 pendulum swing zb",
        "hot": False,
        "sample": "camera",
    },
    "cam_perspective_tilt": {
        "category": "camera",
        "dur": 0.75,
        "sfx": "whoosh_light",
        "keywords": "透视 俯仰 折转 3D 倾斜 perspective tilt 3d tsfy",
        "hot": False,
        "sample": "camera",
    },
    "cam_flip_jib": {
        "category": "camera",
        "dur": 0.75,
        "sfx": "whoosh_rise",
        "keywords": "摇臂 翻转 升降 垂直 jib flip crane ybfz",
        "hot": False,
        "sample": "camera",
    },
    # 既有运镜类
    "smash_zoom": {
        "category": "camera",
        "dur": 0.60,
        "sfx": "zoom_in_swell",
        "keywords": "突进 冲镜 推进 放大 smash zoom tj",
        "hot": False,
        "sample": "camera",
    },
    "zoom_out_trans": {
        "category": "camera",
        "dur": 0.65,
        "sfx": "zoom_out_swell",
        "keywords": "拉远 缩小 退镜 zoom out ly",
        "hot": False,
        "sample": "camera",
    },
    "zoom_dissolve": {
        "category": "camera",
        "dur": 0.60,
        "sfx": "zoom_in_swell",
        "keywords": "缩放 溶解 变焦 zoom dissolve sfrj",
        "hot": False,
        "sample": "camera",
    },
    "rotate_dissolve": {
        "category": "camera",
        "dur": 0.70,
        "sfx": "whoosh_rise",
        "keywords": "旋转 溶解 转盘 rotate dissolve xzrj",
        "hot": False,
        "sample": "camera",
    },
    "whip_pan_left": {
        "category": "camera",
        "dur": 0.60,
        "sfx": "swish_fast",
        "keywords": "横向 甩镜 左 摇镜 快速 平移 whip pan left hxsj",
        "hot": False,
        "sample": "camera",
    },
    "whip_pan_up": {
        "category": "camera",
        "dur": 0.60,
        "sfx": "whoosh_rise",
        "keywords": "纵向 甩镜 上 仰拍 快速 whip pan up zxsj",
        "hot": False,
        "sample": "camera",
    },
    "spin_in": {
        "category": "camera",
        "dur": 0.70,
        "sfx": "whoosh_heavy",
        "keywords": "旋转 推进 螺旋 spin in xztj",
        "hot": False,
        "sample": "camera",
    },
    "cross_zoom_blur": {
        "category": "camera",
        "dur": 0.65,
        "sfx": "zoom_in_swell",
        "keywords": "穿梭 拉焦 变焦 模糊 cross zoom blur cslj",
        "hot": False,
        "sample": "camera",
    },
    "blur_directional": {
        "category": "camera",
        "dur": 0.60,
        "sfx": "swish_fast",
        "keywords": "方向 模糊 运动 拖影 directional motion blur fxmh",
        "hot": False,
        "sample": "camera",
    },
    "blur_radial": {
        "category": "camera",
        "dur": 0.65,
        "sfx": "zoom_in_swell",
        "keywords": "径向 模糊 中心 放射 radial zoom blur jxmh",
        "hot": False,
        "sample": "camera",
    },

    # ─── B. 节奏卡点类 (rhythm，10 款新转场 + 既有震动/冲击卡点) ────────────
    "beat_bass_pump": {
        "category": "rhythm",
        "dur": 0.40,
        "sfx": "bass_drop",
        "keywords": "重低音 震屏 缩放 鼓点 卡点 bass pump shake zdyzp",
        "hot": True,
        "sample": "rhythm",
    },
    "beat_RGB_split_shake": {
        "category": "rhythm",
        "dur": 0.35,
        "sfx": "impact_punch",
        "keywords": "色差 抖动 卡点 RGB 故障 震屏 chromatic shake scdd",
        "hot": True,
        "sample": "rhythm",
    },
    "beat_flash_zoom": {
        "category": "rhythm",
        "dur": 0.35,
        "sfx": "shutter_click",
        "keywords": "爆闪 冲镜 闪白 卡点 快门 flash zoom bs",
        "hot": True,
        "sample": "rhythm",
    },
    "beat_strobe_cut": {
        "category": "rhythm",
        "dur": 0.40,
        "sfx": "glitch_burst",
        "keywords": "频闪 跳切 闪切 蹦迪 卡点 strobe cut pstq",
        "hot": False,
        "sample": "rhythm",
    },
    "beat_glitch_slice": {
        "category": "rhythm",
        "dur": 0.40,
        "sfx": "glitch_burst",
        "keywords": "故障 错位 切片 撕裂 赛博 glitch slice gzcw",
        "hot": True,
        "sample": "rhythm",
    },
    "beat_elastic_bounce": {
        "category": "rhythm",
        "dur": 0.45,
        "sfx": "pop_bubble",
        "keywords": "弹性 弹跳 回弹 Q弹 卡点 elastic bounce txtt",
        "hot": False,
        "sample": "rhythm",
    },
    "beat_zoom_bounce": {
        "category": "rhythm",
        "dur": 0.45,
        "sfx": "impact_punch",
        "keywords": "二段 缩放 弹跳 连续 卡点 double zoom bounce edsf",
        "hot": True,
        "sample": "rhythm",
    },
    "beat_shutter_frame": {
        "category": "rhythm",
        "dur": 0.40,
        "sfx": "shutter_click",
        "keywords": "快门 定格 抽帧 拍照 取景框 shutter freeze frame kmdg",
        "hot": False,
        "sample": "rhythm",
    },
    "beat_split_3x": {
        "category": "rhythm",
        "dur": 0.45,
        "sfx": "snap_finger",
        "keywords": "三联屏 卡点 三分屏 分屏 抖音 triple split slpkd",
        "hot": True,
        "sample": "rhythm",
    },
    "beat_grid_pop": {
        "category": "rhythm",
        "dur": 0.50,
        "sfx": "pop_bubble",
        "keywords": "九宫格 弹出 网格 卡点 分屏 grid pop 3x3 jggtc",
        "hot": False,
        "sample": "rhythm",
    },
    # 既有节奏卡点类
    "shake_impact": {
        "category": "rhythm",
        "dur": 0.40,
        "sfx": "impact_punch",
        "keywords": "震动 冲击 抖动 震屏 卡点 shake impact zdcj",
        "hot": False,
        "sample": "rhythm",
    },

    # ─── C. 图形遮罩类 (graphic，10 款新转场 + 既有几何图形) ─────────────────
    "shape_heart_iris": {
        "category": "graphic",
        "dur": 0.65,
        "sfx": "sparkle_shimmer",
        "keywords": "爱心 扩瞳 心形 情侣 浪漫 heart iris love axkt",
        "hot": True,
        "sample": "graphic",
    },
    "shape_star_burst": {
        "category": "graphic",
        "dur": 0.65,
        "sfx": "ding_bright",
        "keywords": "星芒 绽放 五角星 星星 闪耀 star burst xmzf",
        "hot": False,
        "sample": "graphic",
    },
    "shape_diagonal_slash": {
        "category": "graphic",
        "dur": 0.55,
        "sfx": "swish_fast",
        "keywords": "斜切 刀光 斩击 对角 分割 diagonal slash cut xqdg",
        "hot": True,
        "sample": "graphic",
    },
    "shape_shutter_bands": {
        "category": "graphic",
        "dur": 0.65,
        "sfx": "click_soft",
        "keywords": "斜向 百叶 条带 交错 条纹 shutter bands blinds xxby",
        "hot": False,
        "sample": "graphic",
    },
    "shape_circle_wave": {
        "category": "graphic",
        "dur": 0.70,
        "sfx": "pop_bubble",
        "keywords": "同心 多环 扩散 圆环 波纹 circle wave rings txdh",
        "hot": False,
        "sample": "graphic",
    },
    "shape_split_slide_4": {
        "category": "graphic",
        "dur": 0.65,
        "sfx": "whoosh_light",
        "keywords": "四分 错位 滑屏 四象限 拼贴 quad split slide sfcw",
        "hot": False,
        "sample": "graphic",
    },
    "shape_clock_dual": {
        "category": "graphic",
        "dur": 0.70,
        "sfx": "tick_tock",
        "keywords": "双指针 风车 扫掠 扇形 时钟 dual clock pinwheel szzfc",
        "hot": False,
        "sample": "graphic",
    },
    "shape_polaroid_stack": {
        "category": "graphic",
        "dur": 0.75,
        "sfx": "shutter_click",
        "keywords": "拍立得 甩入 相框 照片 卡片 polaroid photo stack pldsr",
        "hot": True,
        "sample": "graphic",
    },
    "shape_kaleidoscope": {
        "category": "graphic",
        "dur": 0.75,
        "sfx": "sparkle_shimmer",
        "keywords": "万花筒 镜像 对称 几何 棱镜 kaleidoscope mirror whtjx",
        "hot": False,
        "sample": "graphic",
    },
    "shape_origami_fold": {
        "category": "graphic",
        "dur": 0.70,
        "sfx": "page_flip",
        "keywords": "折纸 展开 翻折 多边形 origami fold paper zzzk",
        "hot": False,
        "sample": "graphic",
    },
    # 既有图形类
    "wipe_circle": {
        "category": "graphic",
        "dur": 0.60,
        "sfx": "whoosh_light",
        "keywords": "圆形 擦除 展开 扩瞳 圆圈 wipe circle iris yxcc",
        "hot": False,
        "sample": "graphic",
    },
    "wipe_clock": {
        "category": "graphic",
        "dur": 0.65,
        "sfx": "tick_tock",
        "keywords": "时钟 擦除 表盘 扇形 wipe clock szcc",
        "hot": False,
        "sample": "graphic",
    },
    "iris_diamond": {
        "category": "graphic",
        "dur": 0.60,
        "sfx": "ding_bright",
        "keywords": "菱形 展开 钻石 几何 iris diamond lxzk",
        "hot": False,
        "sample": "graphic",
    },
    "iris_close": {
        "category": "graphic",
        "dur": 0.60,
        "sfx": "whoosh_drop",
        "keywords": "圆形 收缩 闭合 瞳孔 iris close yxss",
        "hot": False,
        "sample": "graphic",
    },
    "iris_rectangle": {
        "category": "graphic",
        "dur": 0.60,
        "sfx": "whoosh_light",
        "keywords": "矩形 画框 方框 边框 iris rectangle jxhk",
        "hot": False,
        "sample": "graphic",
    },

    # ─── D. 光效胶片类 (light，8 款新转场 + 既有闪光/曝光/光晕) ─────────────
    "light_warm_flare": {
        "category": "light",
        "dur": 0.70,
        "sfx": "reverse_cymbal",
        "keywords": "暖金 漏光 胶片 阳光 温暖 warm light leak flare njlg",
        "hot": True,
        "sample": "light",
    },
    "light_cyber_neon": {
        "category": "light",
        "dur": 0.65,
        "sfx": "riser_short",
        "keywords": "赛博 霓虹 扫光 激光 蓝紫 cyber neon sweep sbnh",
        "hot": True,
        "sample": "light",
    },
    "light_prism_rainbow": {
        "category": "light",
        "dur": 0.70,
        "sfx": "sparkle_shimmer",
        "keywords": "棱镜 彩虹 色散 折射 光斑 prism rainbow dispersion ljch",
        "hot": False,
        "sample": "light",
    },
    "light_film_sprocket": {
        "category": "light",
        "dur": 0.75,
        "sfx": "film_projector",
        "keywords": "复古 齿孔 走片 胶卷 放映机 film strip sprocket roll fgck",
        "hot": True,
        "sample": "light",
    },
    "light_vhs_rewind": {
        "category": "light",
        "dur": 0.65,
        "sfx": "tape_rewind",
        "keywords": "VHS 磁带 倒带 雪花 复古 录像带 vhs tape rewind cd",
        "hot": False,
        "sample": "light",
    },
    "light_dream_glow": {
        "category": "light",
        "dur": 0.80,
        "sfx": "sparkle_shimmer",
        "keywords": "柔焦 梦幻 羽化 唯美 圣光 dream soft glow bokeh rjmh",
        "hot": False,
        "sample": "light",
    },
    "light_gold_dust": {
        "category": "light",
        "dur": 0.80,
        "sfx": "sparkle_shimmer",
        "keywords": "流金 粒子 星尘 闪烁 金粉 gold dust particles ljlz",
        "hot": False,
        "sample": "light",
    },
    "light_ink_wash": {
        "category": "light",
        "dur": 0.85,
        "sfx": "air_whoosh",
        "keywords": "水墨 晕染 国风 墨迹 宣纸 ink wash bleed chinese smyr",
        "hot": False,
        "sample": "light",
    },
    # 既有光效胶片类
    "flash_white": {
        "category": "light",
        "dur": 0.45,
        "sfx": "shutter_click",
        "keywords": "闪白 曝光 闪光灯 白场 flash white sb",
        "hot": False,
        "sample": "light",
    },
    "flash_black": {
        "category": "light",
        "dur": 0.50,
        "sfx": "downlifter",
        "keywords": "黑闪 闪黑 暗场 眨眼 flash black hs",
        "hot": False,
        "sample": "light",
    },
    "exposure_dissolve": {
        "category": "light",
        "dur": 0.65,
        "sfx": "reverse_cymbal",
        "keywords": "曝光 溶解 过曝 强光 exposure dissolve bgrj",
        "hot": False,
        "sample": "light",
    },
    "bloom_dissolve": {
        "category": "light",
        "dur": 0.70,
        "sfx": "sparkle_shimmer",
        "keywords": "光晕 溢出 柔光 辉光 bloom glow dissolve gyyc",
        "hot": False,
        "sample": "light",
    },
    "bokeh_dissolve": {
        "category": "light",
        "dur": 0.75,
        "sfx": "sparkle_shimmer",
        "keywords": "散景 溶解 光斑 焦外 bokeh dissolve sjrj",
        "hot": False,
        "sample": "light",
    },
    "light_leak": {
        "category": "light",
        "dur": 0.70,
        "sfx": "reverse_cymbal",
        "keywords": "胶片 漏光 炫光 光斑 light leak jplg",
        "hot": False,
        "sample": "light",
    },
    "film_burn": {
        "category": "light",
        "dur": 0.70,
        "sfx": "film_projector",
        "keywords": "胶片 烧灼 灼烧 复古 film burn jpsz",
        "hot": False,
        "sample": "light",
    },

    # ─── E. 推拉滑动类 (motion) ─────────────────────────────────────────────
    "push_left": {
        "category": "motion",
        "dur": 0.50,
        "sfx": "whoosh_light",
        "keywords": "推左 推入 左 推镜 平移 push left tz",
        "hot": False,
        "sample": "camera",
    },
    "push_right": {
        "category": "motion",
        "dur": 0.50,
        "sfx": "whoosh_light",
        "keywords": "推右 推入 右 推镜 平移 push right ty",
        "hot": False,
        "sample": "camera",
    },
    "push_up": {
        "category": "motion",
        "dur": 0.50,
        "sfx": "whoosh_rise",
        "keywords": "推上 推入 上 向上 push up ts",
        "hot": False,
        "sample": "camera",
    },
    "push_down": {
        "category": "motion",
        "dur": 0.50,
        "sfx": "whoosh_drop",
        "keywords": "推下 推入 下 向下 push down tx",
        "hot": False,
        "sample": "camera",
    },
    "push_out": {
        "category": "motion",
        "dur": 0.50,
        "sfx": "whoosh_light",
        "keywords": "推出 推开 push out tc",
        "hot": False,
        "sample": "camera",
    },
    "push_out_left": {
        "category": "motion",
        "dur": 0.50,
        "sfx": "whoosh_light",
        "keywords": "推出左 推开 左 push out left tcz",
        "hot": False,
        "sample": "camera",
    },
    "push_out_right": {
        "category": "motion",
        "dur": 0.50,
        "sfx": "whoosh_light",
        "keywords": "推出右 推开 右 push out right tcy",
        "hot": False,
        "sample": "camera",
    },
    "push_out_up": {
        "category": "motion",
        "dur": 0.50,
        "sfx": "whoosh_rise",
        "keywords": "推出上 推开 上 push out up tcs",
        "hot": False,
        "sample": "camera",
    },
    "push_out_down": {
        "category": "motion",
        "dur": 0.50,
        "sfx": "whoosh_drop",
        "keywords": "推出下 推开 下 push out down tcx",
        "hot": False,
        "sample": "camera",
    },
    "push_split": {
        "category": "motion",
        "dur": 0.55,
        "sfx": "swish_fast",
        "keywords": "对推 分裂 双向 推开 push split dt",
        "hot": False,
        "sample": "camera",
    },
    "cover_left": {
        "category": "motion",
        "dur": 0.50,
        "sfx": "whoosh_light",
        "keywords": "左向 覆叠 覆盖 滑入 cover left zxfd",
        "hot": False,
        "sample": "camera",
    },
    "cover_down": {
        "category": "motion",
        "dur": 0.50,
        "sfx": "whoosh_drop",
        "keywords": "下向 覆叠 覆盖 下落 cover down xxfd",
        "hot": False,
        "sample": "camera",
    },
    "split_interleave": {
        "category": "motion",
        "dur": 0.60,
        "sfx": "swish_fast",
        "keywords": "交错 滑入 条带 穿插 split interleave jchr",
        "hot": False,
        "sample": "graphic",
    },
    "curtain_drop": {
        "category": "motion",
        "dur": 0.65,
        "sfx": "whoosh_drop",
        "keywords": "帷幕 垂落 幕布 落下 curtain drop wmcl",
        "hot": False,
        "sample": "graphic",
    },

    # ─── F. 擦除遮罩类 (wipe) ───────────────────────────────────────────────
    "wipe_left": {
        "category": "wipe",
        "dur": 0.50,
        "sfx": "whoosh_light",
        "keywords": "擦 擦除 左 横向 划像 wipe left cc",
        "hot": False,
        "sample": "graphic",
    },
    "wipe_right": {
        "category": "wipe",
        "dur": 0.50,
        "sfx": "whoosh_light",
        "keywords": "擦除 右 横向 划像 wipe right ccy",
        "hot": False,
        "sample": "graphic",
    },
    "wipe_up": {
        "category": "wipe",
        "dur": 0.50,
        "sfx": "whoosh_rise",
        "keywords": "擦除 上 纵向 划像 wipe up ccs",
        "hot": False,
        "sample": "graphic",
    },
    "wipe_down": {
        "category": "wipe",
        "dur": 0.50,
        "sfx": "whoosh_drop",
        "keywords": "擦除 下 纵向 划像 wipe down ccx",
        "hot": False,
        "sample": "graphic",
    },
    "wipe_angle": {
        "category": "wipe",
        "dur": 0.55,
        "sfx": "whoosh_light",
        "keywords": "线性 擦除 斜向 角度 wipe angle xxcc",
        "hot": False,
        "sample": "graphic",
    },
    "wipe_blinds": {
        "category": "wipe",
        "dur": 0.60,
        "sfx": "click_soft",
        "keywords": "百叶窗 条纹 格栅 wipe blinds byc",
        "hot": False,
        "sample": "graphic",
    },
    "checker_wipe": {
        "category": "wipe",
        "dur": 0.60,
        "sfx": "click_soft",
        "keywords": "棋盘格 方格 交替 checker wipe qpg",
        "hot": False,
        "sample": "graphic",
    },
    "barn_door_h": {
        "category": "wipe",
        "dur": 0.55,
        "sfx": "whoosh_light",
        "keywords": "左右 开门 双开门 水平 barn door horizontal zykm",
        "hot": False,
        "sample": "graphic",
    },
    "barn_door_v": {
        "category": "wipe",
        "dur": 0.55,
        "sfx": "whoosh_light",
        "keywords": "上下 开门 双开门 垂直 barn door vertical sxkm",
        "hot": False,
        "sample": "graphic",
    },
    "venetian_diag": {
        "category": "wipe",
        "dur": 0.60,
        "sfx": "click_soft",
        "keywords": "斜向 格栅 百叶 条纹 venetian diagonal xxgs",
        "hot": False,
        "sample": "graphic",
    },
    "luma_melt": {
        "category": "wipe",
        "dur": 0.65,
        "sfx": "air_whoosh",
        "keywords": "流光 穿插 亮度 融化 luma melt lgcc",
        "hot": False,
        "sample": "dissolve",
    },

    # ─── G. 溶解渐变类 (dissolve) ───────────────────────────────────────────
    "cross_dissolve": {
        "category": "dissolve",
        "dur": 0.60,
        "sfx": "",
        "keywords": "交叉 溶解 叠化 渐变 默认 cross dissolve mix jcrj",
        "hot": False,
        "sample": "dissolve",
    },
    "fade_dark": {
        "category": "dissolve",
        "dur": 0.60,
        "sfx": "",
        "keywords": "黑场 暗场 淡入 淡出 fade dark black hc",
        "hot": False,
        "sample": "dissolve",
    },
    "fade_white": {
        "category": "dissolve",
        "dur": 0.60,
        "sfx": "",
        "keywords": "白场 亮场 淡入 淡出 fade white bc",
        "hot": False,
        "sample": "dissolve",
    },
    "blur_dissolve": {
        "category": "dissolve",
        "dur": 0.65,
        "sfx": "air_whoosh",
        "keywords": "模糊 溶解 失焦 虚化 blur defocus dissolve mhrj",
        "hot": False,
        "sample": "dissolve",
    },
    "wave_ripple": {
        "category": "dissolve",
        "dur": 0.75,
        "sfx": "pop_bubble",
        "keywords": "水波 涟漪 波纹 荡漾 wave ripple sbly",
        "hot": False,
        "sample": "dissolve",
    },

    # ─── H. 特效类 (fx) ────────────────────────────────────────────────────
    "page_turn": {
        "category": "fx",
        "dur": 0.70,
        "sfx": "page_flip",
        "keywords": "翻页 卷页 书页 纸张 page turn curl fy",
        "hot": False,
        "sample": "graphic",
    },
    "cube_spin": {
        "category": "fx",
        "dur": 0.65,
        "sfx": "whoosh_heavy",
        "keywords": "立方体 旋转 3D 盒子 cube spin 3d lftxz",
        "hot": False,
        "sample": "camera",
    },
    "rgb_split": {
        "category": "fx",
        "dur": 0.45,
        "sfx": "glitch_burst",
        "keywords": "色散 色差 分离 红蓝 RGB chromatic aberration ss",
        "hot": False,
        "sample": "rhythm",
    },
    "glitch": {
        "category": "fx",
        "dur": 0.45,
        "sfx": "glitch_burst",
        "keywords": "故障 信号 错位 电子 glitch digital gz",
        "hot": False,
        "sample": "rhythm",
    },
    "pixelate": {
        "category": "fx",
        "dur": 0.55,
        "sfx": "click_soft",
        "keywords": "像素化 溶解 马赛克 方块 pixelate mosaic xshrj",
        "hot": False,
        "sample": "graphic",
    },
    "scanlines": {
        "category": "fx",
        "dur": 0.55,
        "sfx": "glitch_burst",
        "keywords": "扫描线 电视 信号 条纹 scanlines crt smx",
        "hot": False,
        "sample": "rhythm",
    },

    # ─── I. 音频转场 (audio) ────────────────────────────────────────────────
    "audio_xfade_equal": {
        "category": "audio",
        "dur": 0.50,
        "sfx": "",
        "keywords": "等功率 交叉 淡化 音频 equal power crossfade dgljcdh",
        "hot": False,
        "sample": "dissolve",
    },
    "audio_xfade_linear": {
        "category": "audio",
        "dur": 0.50,
        "sfx": "",
        "keywords": "线性 交叉 淡化 音频 linear crossfade xxjcdh",
        "hot": False,
        "sample": "dissolve",
    },
    "audio_fade_in": {
        "category": "audio",
        "dur": 0.50,
        "sfx": "",
        "keywords": "淡入 音频 渐入 audio fade in dr",
        "hot": False,
        "sample": "dissolve",
    },
    "audio_fade_out": {
        "category": "audio",
        "dur": 0.50,
        "sfx": "",
        "keywords": "淡出 音频 渐出 audio fade out dc",
        "hot": False,
        "sample": "dissolve",
    },
    "audio_seam_fade": {
        "category": "audio",
        "dur": 0.03,
        "sfx": "",
        "keywords": "接缝 淡化 防爆音 微淡化 audio seam fade jfdh",
        "hot": False,
        "sample": "dissolve",
    },
}


def _lookup_entry(preset: str) -> dict | None:
    key = str(preset or "").strip()
    if not key:
        return None
    if key in TRANSITION_META:
        return TRANSITION_META[key]
    base = _SCOPE_SUFFIX_RE.sub("", key)
    return TRANSITION_META.get(base)


def meta_of(preset: str) -> dict:
    out = dict(DEFAULT_META)
    entry = _lookup_entry(preset)
    if entry:
        out.update(entry)
    return out


def recommended_duration(preset: str, fallback: float | None = None) -> float:
    entry = _lookup_entry(preset)
    if entry and "dur" in entry:
        return float(entry["dur"])
    return float(fallback if fallback is not None else DEFAULT_META["dur"])


def category_of(preset: str) -> str:
    return str(meta_of(preset).get("category") or DEFAULT_META["category"])


def recommended_sfx(preset: str) -> str:
    return str(meta_of(preset).get("sfx") or "")
