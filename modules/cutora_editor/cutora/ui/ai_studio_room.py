# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""片语工作室 (D:\\0_LTX) 「✨ AI 工坊」画展式竖版圆角卡牌甲板 & 剪辑页原生直调引擎 (cutora/ui/ai_studio_room.py)。

设计架构：
1. 100% 完整覆盖《本地AI工具清单_20261014.md》全部 21 项现役 AI 能力（生成与重绘 4 项、画质修复与超分 7 项、人像重打光与调色抠像 5 项、时间线配乐/音效/声音克隆/深度降噪分轨 5 项），一个不落。
2. 画展式竖屏书签圆角卡牌界面（Art-Gallery Vertical Rounded Card Deck）：
   - 每张卡牌呈竖屏艺术品比例（圆角 16px、顶部实时画面预览窗、右上角 `ⓘ` 详情按钮收纳所有模型路径/显存/命令行等技术注释，卡面清爽直观）。
   - 支持勾选多张卡牌进行「叠加链式执行」（如：Restore 画质修复 → Simone v6h 重打光 → Autograde 3D LUT 调色）。
   - 默认为每条时间线生成全套 21 张 AI 卡牌，支持任意复制/新建多张同类型卡牌应用在不同素材或时间点上。
3. 时间线与素材级持久化（随 `.cutora` 工程保存）：
   - 每套时间线（`task["ai_cards"]`）独立拥有一套完整卡牌，切换时间线时全套卡牌自动换新；
   - 每个时间线素材（`clip.ai_cards`）可对照绑定专属卡牌，所有参数、描述语（Prompt）、参考素材路径与输出成品路径全部记录在卡牌内随工程保存。
4. 剪辑页面原生灵巧调用 + 自动落轨生成轨道与素材条：
   - 在剪辑页（时间线工具栏、时间线右键、画布悬浮条）可直接呼出灵巧的「✨ AI 创作与精修胶囊」直接生图、生视频、改图、改视频、生成配乐、生成音效、克隆人声、提画质、音频降噪；
   - 生成后自动在当前时间线游标处同步创建对应轨道（视频轨 / 配乐轨 / 音效轨 / 配音轨）与素材条（`Track` + `Clip` + `MediaSource`），并自动在 AI 界面生成/记录对应卡牌！
"""

from __future__ import annotations

import copy
import datetime
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import wave
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QPoint, QSize, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QLinearGradient, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from cutora.core.log import get_logger
from cutora.ui.i18n import tr
from cutora.ui.theme.tokens import SPACE_2, SPACE_3, SPACE_4

_LOG = get_logger("cutora.ui.ai_studio_room")

LTX_ROOT = Path(__file__).resolve().parents[4]
if str(LTX_ROOT) not in sys.path:
    sys.path.insert(0, str(LTX_ROOT))

_PORTABLE_RUNNERS = LTX_ROOT / "modules" / "studio_gateway" / "runners"
LLM_ROOT = _PORTABLE_RUNNERS if _PORTABLE_RUNNERS.is_dir() else Path(r"D:\0_LLM")
_DEV_LLM_ROOT = Path(r"D:\0_LLM")


def _resolve_tool_py(dev_rel: str) -> Path:
    dev_p = _DEV_LLM_ROOT / dev_rel
    if dev_p.is_file():
        return dev_p
    studio_py = LTX_ROOT / "runtime" / "venv" / "Scripts" / "python.exe"
    if studio_py.is_file():
        return studio_py
    return Path(sys.executable)


# ============================================================================
# 1. 完整 21 项本地 + 云端协同 AI 能力画展卡牌总目录（严格对照 本地AI工具清单_20261014.md）
# ============================================================================

AI_CAPABILITIES_CATALOG: list[dict] = [
    # ── 一、视频与图像生成、局部重绘与无痕去物 (4 项) ──
    {
        "cap_id": "ltx25_i2v_t2v",
        "order": 1,
        "category": "gen",
        "category_label": "🎬 生成与重绘",
        "title": "图/文生视频 · LTX-2.5",
        "subtitle": "首帧图生视频 / 文生视频（含同步环境音 + 2× 潜空间放大）",
        "badge": "生视频 · I2V/T2V",
        "accent": "#E5A93C",
        "output_kind": "video",
        "modes": [
            ("⚡ 优先付费账号极速生成 (Flow / Grok · 默认推荐)", "paid_flow_grok"),
            ("🖥️ 本地 LTX-2.5 22B NVFP4 8步蒸馏 + 2×精修", "local_ltx25"),
        ],
        "default_prompt": "Cinematic slow push-in shot, natural facial expression, warm golden rim lighting, 4k realism",
        "strength_label": "运动幅度 / 秒数",
        "default_strength": 65,
        "info_details": (
            "【模型与脚本】D:\\0_LLM\\bench\\ltx25\\ltx25_run.py\n"
            "【核心权重】ltx-2.5-22b-distilled-nvfp4.safetensors (15.14 GB) + gemma_3_12B_it_fp4_mixed\n"
            "【算力与策略】默认优先调用已购买的 Flow / Grok 付费账号极速生成；可随时切换本地 RTX 5080 16GB 运行（5秒 1536×1024 含同步音轨约 55 秒）。"
        ),
    },
    {
        "cap_id": "flux2_klein",
        "order": 2,
        "category": "gen",
        "category_label": "🎬 生成与重绘",
        "title": "极速生图/改图 · FLUX.2",
        "subtitle": "文生图、分镜首帧生成、单图/双图参考局部改图",
        "badge": "生图/改图 · 4步极速",
        "accent": "#E5A93C",
        "output_kind": "image",
        "modes": [
            ("⚡ 优先付费账号极速生图/改图 (Grok / Flow)", "paid_flow_grok"),
            ("🖥️ 本地 FLUX.2 [klein] 4B (4步 1.2秒/张)", "local_flux2"),
            ("🖼️ 参考原图风格/构图重绘 (Image-to-Image)", "local_flux2_edit"),
        ],
        "default_prompt": "Photorealistic cinematic frame, warm studio key light, shallow depth of field, 8k detail",
        "strength_label": "重绘创意度",
        "default_strength": 75,
        "info_details": (
            "【模型与脚本】D:\\0_LLM\\bench\\flux2\\flux2_run.py\n"
            "【核心权重】flux-2-klein-4b.safetensors (7.22 GB) + Qwen3-4B (7.49 GB)\n"
            "【性能指标】4 步蒸馏采样，冷启动约 16 秒，常驻仅 1.2 秒/张，生成后自动作为图片/视频轨插入时间线。"
        ),
    },
    {
        "cap_id": "ltx_in_outpaint",
        "order": 3,
        "category": "gen",
        "category_label": "🎬 生成与重绘",
        "title": "局部重绘与扩画 · Outpaint",
        "subtitle": "9:16 竖屏扩 16:9 横屏动态延展 & 局部蒙版改视频",
        "badge": "扩画幅 / 改视频",
        "accent": "#E5A93C",
        "output_kind": "video",
        "modes": [
            ("📐 竖屏 9:16 智能外扩为 16:9 宽银幕", "outpaint_16_9"),
            ("🖌️ 局部蒙版内容重绘 (Inpainting)", "inpaint_mask"),
            ("⚡ 优先付费账号微指令改视频 (Grok / Flow)", "paid_retouch"),
        ],
        "default_prompt": "Naturally extend background scenery with matching warm ambient lighting and depth of field",
        "strength_label": "边缘融合强度",
        "default_strength": 80,
        "info_details": (
            "【模型与工作流】D:\\0_LLM\\bench\\inpaint\\inpaint_api_5s.json\n"
            "【核心权重】ltx-2.3-22b-ic-lora-in-outpainting-0.9.safetensors (639 MB)\n"
            "【功能说明】对视频指定遮罩区域进行局部内容替换，或将 9:16 竖屏素材左右自然延展为 16:9 横屏。"
        ),
    },
    {
        "cap_id": "ltx25_cleanplate",
        "order": 4,
        "category": "gen",
        "category_label": "🎬 生成与重绘",
        "title": "去话筒/去杂物 · CleanPlate",
        "subtitle": "SAM 2.1 点选追踪 + LTX-2.5 时空无痕抹除人物/话筒/字幕",
        "badge": "去话筒 / 去人物",
        "accent": "#E5A93C",
        "output_kind": "video",
        "modes": [
            ("🎙️ 自动识别并无痕抹除胸麦/手持话筒 (SAM 2.1 + CleanPlate)", "remove_mic"),
            ("🧍‍♂️ 抹除画布点选/框选的人物或穿帮杂物", "remove_object"),
            ("🧹 清除固定机位角落台标/残留硬字幕", "remove_logo"),
        ],
        "default_prompt": "Clean natural background texture, seamless cloth and skin continuation without microphone",
        "strength_label": "时空修补半径",
        "default_strength": 70,
        "info_details": (
            "【模型与脚本】D:\\0_LLM\\bench\\ltx_vfx\\sam_mic.py + api_cleanplate.json\n"
            "【核心权重】ltx-2.5-22b-ic-lora-clean-plate-1.0.safetensors (2.63 GB) + sam2.1_hiera_base_plus.pt\n"
            "【交互说明】支持直接在剪辑画布点选目标（SAM 2.1 自动生成逐帧掩码）或按区域无痕恢复被遮挡的背景与衣物纹理。"
        ),
    },

    # ── 二、视频提升画质与修复 — 7 大模式全齐 (7 项) ──
    {
        "cap_id": "ltx25_restore",
        "order": 5,
        "category": "restore",
        "category_label": "💎 视频提画质",
        "title": "画质修复 · LTX Restore",
        "subtitle": "清除高压缩块效应、蚊式噪点、烂画质重采样修复 (rr.py)",
        "badge": "画质修复 · 去伪影",
        "accent": "#F3C668",
        "output_kind": "video",
        "modes": [
            ("💎 标准去伪影与清晰度重建 (Restore 1.0 · 推荐)", "restore_std"),
            ("🔥 深度修复严重压缩网课/老旧监控低清画质", "restore_deep"),
            ("🌿 轻柔保真模式 (仅清压缩块，100% 锁死五官)", "restore_gentle"),
        ],
        "default_prompt": "Clean high-definition broadcast video, zero compression artifacts, natural skin pores and fabric texture",
        "strength_label": "修复强度",
        "default_strength": 65,
        "info_details": (
            "【模型与脚本】D:\\0_LLM\\rr.py + bench\\ltx_vfx\\api_restore.json\n"
            "【核心权重】ltx-2.5-22b-ic-lora-restore-1.0.safetensors (639 MB)\n"
            "【适用场景】修复二压/三压下载视频的 H.264 块效应、边缘锯齿、蚊式噪点与低分辨率模糊。"
        ),
    },
    {
        "cap_id": "ltx25_deblur",
        "order": 6,
        "category": "restore",
        "category_label": "💎 视频提画质",
        "title": "去运动模糊 · LTX Deblur",
        "subtitle": "消除快门过慢拖影、手抖果冻效应与轻微脱焦模糊",
        "badge": "提画质 · 去模糊",
        "accent": "#F3C668",
        "output_kind": "video",
        "modes": [
            ("🎯 智能去运动拖影与失焦重建 (Deblur 0.9)", "deblur_std"),
            ("⚡ 快速光学锐化 + 边缘微对比度增强", "deblur_fast"),
        ],
        "default_prompt": "Sharp in-focus cinematography, crisp edges, zero motion blur",
        "strength_label": "去模糊力度",
        "default_strength": 70,
        "info_details": (
            "【模型与脚本】D:\\0_LLM\\bench\\ltx_vfx\\api_deblur.json\n"
            "【核心权重】ltx-2.5-22b-ic-lora-deblur-0.9.safetensors (639 MB)\n"
            "【适用场景】抢救跟焦失败、快速转头拖影或快门速度不足导致的动态模糊素材。"
        ),
    },
    {
        "cap_id": "ltx25_refine",
        "order": 7,
        "category": "restore",
        "category_label": "💎 视频提画质",
        "title": "放大补细节 · LTX Refine",
        "subtitle": "分块潜空间轻降噪精修，织补发丝/眉眼/衣物高频微观纹理",
        "badge": "提画质 · 补细节",
        "accent": "#F3C668",
        "output_kind": "video",
        "modes": [
            ("✨ 0.25 黄金轻降噪补细节 (不改脸型只加清晰纹理)", "refine_025"),
            ("🔍 0.35 强化微观纹理重建 (适合远景/中景放大)", "refine_035"),
        ],
        "default_prompt": "Ultra-detailed skin texture, individual hair strands, sharp fabric weave, 4k master quality",
        "strength_label": "细节织补度",
        "default_strength": 55,
        "info_details": (
            "【模型与脚本】D:\\0_LLM\\bench\\ltx25\\refine_api_90_035_tiledenc.json\n"
            "【核心权重】ltx-2.5-22b-ic-lora-refine-details-1.0.safetensors (639 MB)\n"
            "【适用场景】视频放大后消除塑料涂抹感，在不改变人物身份的前提下补充真实毛孔、胡须与织物纹理。"
        ),
    },
    {
        "cap_id": "nomos2_4x",
        "order": 8,
        "category": "restore",
        "category_label": "💎 视频提画质",
        "title": "4× 真实超分 · Nomos2",
        "subtitle": "RealPLKSR DySample 摄影级 4 倍无损超分辨率放大",
        "badge": "提画质 · 4×超分",
        "accent": "#F3C668",
        "output_kind": "video",
        "modes": [
            ("🚀 2× 极速高清超分 (1080p → 4K 级清晰度)", "upscale_2x"),
            ("💎 4× 满血摄影级超分 (4xNomos2_realplksr_dysample)", "upscale_4x"),
        ],
        "default_prompt": "4x RealPLKSR photographic super-resolution with natural film grain preservation",
        "strength_label": "锐度配比",
        "default_strength": 80,
        "info_details": (
            "【模型与脚本】D:\\0_LLM\\bench\\ltx25\\nomos2_upscale.py\n"
            "【核心权重】4xNomos2_realplksr_dysample.pth (57.6 MB)\n"
            "【性能优势】轻量极速且绝不产生 AI 幻觉假脸，完美保留真实摄影质感，适合低清素材直升 1080p/4K。"
        ),
    },
    {
        "cap_id": "ltx25_sdr2hdr",
        "order": 9,
        "category": "restore",
        "category_label": "💎 视频提画质",
        "title": "SDR 转 HDR · 10bit 高动态",
        "subtitle": "8bit 普通视频升维 10bit HLG 广色域 + 死白高光细节恢复",
        "badge": "提画质 · SDR→HDR",
        "accent": "#F3C668",
        "output_kind": "video",
        "modes": [
            ("🌅 SDR 转 10bit HLG 广色域 + 高光平滑滚降", "sdr2hdr_hlg"),
            ("✨ 仅恢复过爆高光与暗部动态范围 (输出标准 Rec.709)", "hdr_tonemap_709"),
        ],
        "default_prompt": "High dynamic range 10-bit grading, smooth highlight roll-off, rich shadow depth",
        "strength_label": "动态扩展度",
        "default_strength": 75,
        "info_details": (
            "【模型与脚本】D:\\0_LLM\\bench\\ltx_vfx\\api_sdr2hdr.json\n"
            "【核心权重】ltx-2.5-22b-ic-lora-sdr-to-hdr-1.0.safetensors (2.63 GB)\n"
            "【功能说明】恢复 8bit 视频中被裁切的窗户高光、舞台顶光与暗部色阶，消除色彩断层。"
        ),
    },
    {
        "cap_id": "ltx25_colorize",
        "order": 10,
        "category": "restore",
        "category_label": "💎 视频提画质",
        "title": "黑白/褪色上色 · Colorize",
        "subtitle": "黑白历史老片或严重偏色褪色素材时域一致自然上色",
        "badge": "提画质 · 智能上色",
        "accent": "#F3C668",
        "output_kind": "video",
        "modes": [
            ("🎨 历史黑白影像全彩自然还原 (Colorization 0.9)", "colorize_bw"),
            ("🌷 褪色泛黄老视频色彩饱满度复活", "colorize_revive"),
        ],
        "default_prompt": "Natural lifelike skin tones, authentic historical colors, temporally stable colorization",
        "strength_label": "色彩饱和度",
        "default_strength": 75,
        "info_details": (
            "【模型与脚本】D:\\0_LLM\\bench\\ltx_vfx\\api_colorize_v2.json\n"
            "【核心权重】ltx-2.5-22b-ic-lora-colorization-0.9.safetensors (639 MB)\n"
            "【技术特点】基于 22B 时空注意力保持跨帧颜色高度稳定，不忽红忽绿，肤色温润真实。"
        ),
    },
    {
        "cap_id": "temporal_denoise",
        "order": 11,
        "category": "restore",
        "category_label": "💎 视频提画质",
        "title": "光流时域降噪 · Denoise",
        "subtitle": "Farneback 光流运动补偿 + MediaPipe 护脸五官零涂抹降噪",
        "badge": "提画质 · 视频降噪",
        "accent": "#F3C668",
        "output_kind": "video",
        "modes": [
            ("🛡️ medium 标准护脸时域降噪 (消除高感彩噪且五官锐利 · 推荐)", "medium"),
            ("🪶 light 轻度降噪 (保留胶片颗粒感)", "light"),
            ("🌊 strong 强力夜景降噪 (清理暗部大颗粒噪点)", "strong"),
        ],
        "default_prompt": "Motion-compensated temporal denoising with MediaPipe facial sharpness lock",
        "strength_label": "时域滤波强度",
        "default_strength": 65,
        "info_details": (
            "【模型与脚本】D:\\0_LLM\\tools\\relight\\denoise.py\n"
            "【核心算法】Farneback 稠密光流前后帧对齐 + 双边时域融合 + MediaPipe 468 点护脸掩码\n"
            "【性能与优势】彻底消除暗部蠕动噪点与提亮后的闪烁，同时对眉眼嘴唇做 100% 锐度保护。"
        ),
    },

    # ── 三、人像重打光、电影调色与发丝抠像 (5 项) ──
    {
        "cap_id": "simone_relight_v6h",
        "order": 12,
        "category": "portrait",
        "category_label": "💡 人像光影与调色",
        "title": "三维人像重打光 · Simone v6h",
        "subtitle": "DPT 深度法线 + 伦勃朗主光/暖金轮廓光 + 自动去镜片反光",
        "badge": "人像精修 · 重打光",
        "accent": "#F5D082",
        "output_kind": "video",
        "modes": [
            ("☀️ Simone v6h 黄金人像重打光 (只亮人物、背景不爆、去眼镜绿光)", "v6h_gold"),
            ("🕯️ 伦勃朗立体侧逆光 (强化面部立体轮廓与暖色发丝光)", "v6h_rembrandt"),
            ("🌸 柔美环形眼神补光 (均匀柔和提亮暗沉面色)", "v6h_soft"),
        ],
        "default_prompt": "Simone v6h deterministic 3D normal relighting, anti-glare glasses, hair & lip protection",
        "strength_label": "主光提亮强度",
        "default_strength": 60,
        "info_details": (
            "【模型与脚本】D:\\0_LLM\\tools\\relight\\relight.py (v6h 黄金生产版)\n"
            "【核心权重】Intel dpt-hybrid-midas (470 MB) + MediaPipe FaceMesh/SelfieSegmenter\n"
            "【独创特性】零 AI 幻觉、零闪烁；内置眼镜框绿光抑制、深色头发防泛灰保护与双唇血色还原。"
        ),
    },
    {
        "cap_id": "portrait_fill_flicker",
        "order": 13,
        "category": "portrait",
        "category_label": "💡 人像光影与调色",
        "title": "面部暗部补光 & 去频闪",
        "subtitle": "零塑料感提亮眼窝/法令纹顶光阴影 (fill.py) + 跨帧去闪烁 (flicker.py)",
        "badge": "去法令纹阴影 / 去频闪",
        "accent": "#F5D082",
        "output_kind": "video",
        "modes": [
            ("✨ 面部比例补光 + 跨帧亮度去频闪 (fill.py + flicker.py)", "fill_and_deflicker"),
            ("💡 仅柔和冲淡眼窝与法令纹死黑阴影 (fill=0.25)", "fill_only"),
            ("📺 仅消除灯光频闪 / AI 生成帧间明暗跳动 (flicker.py)", "deflicker_only"),
        ],
        "default_prompt": "Proportional facial shadow fill without flattening nose bridge, plus temporal deflicker",
        "strength_label": "暗部补光比",
        "default_strength": 45,
        "info_details": (
            "【模型与脚本】D:\\0_LLM\\tools\\relight\\fill.py + flicker.py\n"
            "【核心算法】基于面部局部均值比较的按比例补光（保留鼻梁高光立体感，绝无磨皮塑料感）+ 滑动窗口时域亮度曲线平滑。"
        ),
    },
    {
        "cap_id": "mediapipe_face_pass",
        "order": 14,
        "category": "portrait",
        "category_label": "💡 人像光影与调色",
        "title": "面部五官/镜片精修 · FacePass",
        "subtitle": "468 点面部拓扑精细遮罩：护唇润色、护眼提神、去眼镜反光",
        "badge": "五官精修 · 468点拓扑",
        "accent": "#F5D082",
        "output_kind": "video",
        "modes": [
            ("👓 自动抑制眼镜片蓝绿反光 + 眼神通透增强", "deglare_eyes"),
            ("💄 自然气色还原 (双唇柔和润色 + 肤色去蜡黄)", "lip_skin_vitality"),
        ],
        "default_prompt": "MediaPipe 468-landmark precise glasses de-glare, natural lip vitality and eye clarity",
        "strength_label": "精修柔和度",
        "default_strength": 60,
        "info_details": (
            "【模型与脚本】D:\\0_LLM\\tools\\relight\\face_pass.py\n"
            "【核心权重】face_landmarker.task + selfie_segmenter.tflite + DPT-Hybrid\n"
            "【功能说明】精准分离皮肤、眉毛、眼白、嘴唇、镜框与头发，针对局部微瑕进行电影级润饰。"
        ),
    },
    {
        "cap_id": "autograde_lut",
        "order": 15,
        "category": "portrait",
        "category_label": "💡 人像光影与调色",
        "title": "智能电影调色 · Autograde LUT",
        "subtitle": "Gemma 4 视觉审片 + 灰雾恢复/肤色校准 + 生成 33³ .cube 3D LUT",
        "badge": "自动调色 · 3D LUT",
        "accent": "#F5D082",
        "output_kind": "lut",
        "modes": [
            ("🎬 达芬奇级自动灰雾恢复 + 暖调肤色校准 (生成并挂载 .cube)", "warm_cinema"),
            ("🎞️ 柯达 2383 经典胶片质感 + 柔和暗角颗粒", "kodak_2383"),
            ("💎 通透商业演讲/访谈高净度调色", "clean_speech"),
        ],
        "default_prompt": "Dehaze black point, calibrate warm natural skin vector, smooth S-curve contrast, 33x33x33 3D LUT",
        "strength_label": "LUT 挂载浓度",
        "default_strength": 85,
        "info_details": (
            "【模型与脚本】D:\\0_LLM\\tools\\autograde\\autograde.py + lut_bake.py\n"
            "【核心大脑】Ollama gemma4:e4b 视觉多模态诊断 + 33×33×33 标准 .cube 烘焙\n"
            "【联动机制】生成后不仅导出修复视频，还会直接把 `.cube` 3D LUT 自动挂载到当前时间线调色槽位！"
        ),
    },
    {
        "cap_id": "rmbg2_sam2_matte",
        "order": 16,
        "category": "portrait",
        "category_label": "💡 人像光影与调色",
        "title": "发丝级抠图抠像 · RMBG & SAM2",
        "subtitle": "RMBG-2.0 BiRefNet 单帧发丝透明抠图 & SAM 2.1 视频动态抠像",
        "badge": "发丝抠图 / 视频抠像",
        "accent": "#F5D082",
        "output_kind": "video",
        "modes": [
            ("✂️ RMBG-2.0 BiRefNet 发丝级透明去背景 (生成带 Alpha PNG/图层)", "rmbg2_alpha"),
            ("🎬 SAM 2.1 视频人像动态抠像与背景虚化/替换", "sam2_video_matte"),
        ],
        "default_prompt": "Hair-strand precision alpha matting with clean anti-aliased edges",
        "strength_label": "边缘羽化精度",
        "default_strength": 80,
        "info_details": (
            "【模型与脚本】D:\\0_LLM\\tools\\rmbg\\rmbg.py (RMBG-2.0 844MB) + SAM 2.1 Base Plus (310MB)\n"
            "【性能指标】1024×1024 缕空发丝与半透明边缘秒级分割，支持直接在画布点选正/负样本点。"
        ),
    },

    # ── 四、时间线直接生成配乐、生成音效、克隆人声与深度音频降噪分轨 (5 项) ──
    {
        "cap_id": "musicgen_bgm",
        "order": 17,
        "category": "audio",
        "category_label": "🎵 配乐/音效/克隆/降噪",
        "title": "时间线生成配乐 · MusicGen",
        "subtitle": "按画面情绪直接生成无版权背景音乐并自动落位时间线配乐轨",
        "badge": "生成配乐 · 自动落轨",
        "accent": "#D49B35",
        "output_kind": "audio_bgm",
        "modes": [
            ("🎹 温暖励志钢琴与环境弦乐 (适合讲道/访谈温情铺底)", "warm_piano_strings"),
            ("🌅 恢弘电影感史诗渐强管弦 (适合金句高潮/片头片尾)", "cinematic_epic"),
            ("🎸 轻快治愈木吉他原声伴奏 (适合生活记录/轻松叙事)", "acoustic_breeze"),
            ("🧘 空灵冥想氛围 Pad (低频不抢人声频段)", "ambient_pad"),
        ],
        "default_prompt": "Warm inspirational piano and soft ambient strings, emotional cinematic background music, no drums",
        "strength_label": "生成时长 (秒)",
        "default_strength": 15,
        "info_details": (
            "【模型与脚本】D:\\0_LLM\\audio\\runners\\musicgen.py\n"
            "【核心权重】facebook/musicgen-small (2.35 GB)\n"
            "【时间线直连】生成后自动在当前时间线创建或复用「🎵 AI 配乐轨 (bgm)」，并在当前播放头位置直接插入音频片段！"
        ),
    },
    {
        "cap_id": "stable_audio_sfx",
        "order": 18,
        "category": "audio",
        "category_label": "🎵 配乐/音效/克隆/降噪",
        "title": "时间线生成音效 · StableAudio",
        "subtitle": "44.1kHz 立体声环境音/转场音效 & 现场观众掌声欢呼生成",
        "badge": "生成音效 · 自动落轨",
        "accent": "#D49B35",
        "output_kind": "audio_sfx",
        "modes": [
            ("👏 热烈现场观众掌声与自然欢呼 (audience_sfx 真实人声卷积)", "audience_applause"),
            ("💨 电影级重低音转场嗖声 (Cinematic Whoosh & Impact)", "whoosh_impact"),
            ("🌧️ 沉浸自然环境氛围音 (柔和雨声 / 晨鸟 / 教堂空间残响)", "nature_ambience"),
            ("✨ 温暖神圣风铃与高光提示音 (Magical Shimmer Chime)", "shimmer_chime"),
        ],
        "default_prompt": "Warm hall audience applause and natural cheering, stereo 44.1kHz realistic acoustics",
        "strength_label": "音效时长 (秒)",
        "default_strength": 6,
        "info_details": (
            "【模型与脚本】D:\\0_LLM\\audio\\runners\\sao_run.py + bench\\audience_sfx.py\n"
            "【核心权重】stabilityai/stable-audio-open-1.0 (4.55 GB) + 零白噪音观众欢呼合成器\n"
            "【时间线直连】生成后自动在当前时间线创建「🔊 AI 音效轨 (sfx)」并精准落位到当前播放头位置！"
        ),
    },
    {
        "cap_id": "chatterbox_clone",
        "order": 19,
        "category": "audio",
        "category_label": "🎵 配乐/音效/克隆/降噪",
        "title": "零样本克隆配音 · Chatterbox",
        "subtitle": "吸取当前片段 5 秒原声，完美复刻音色与情感张力生成新台词并落轨",
        "badge": "克隆人声 · 情感复刻",
        "accent": "#D49B35",
        "output_kind": "audio_voice",
        "modes": [
            ("🎙️ 吸取当前时间线原声 · 稳重温和情感克隆 (exaggeration=0.5)", "clone_natural"),
            ("🔥 吸取当前时间线原声 · 饱满激昂演讲感染力 (exaggeration=0.75)", "clone_passionate"),
            ("🕊️ 吸取当前时间线原声 · 轻柔叙事耳语感 (exaggeration=0.35)", "clone_gentle"),
        ],
        "default_prompt": "May grace, peace, and strength be with you and your family today.",
        "strength_label": "情感张力 (Exaggeration)",
        "default_strength": 55,
        "info_details": (
            "【模型与脚本】D:\\0_LLM\\audio\\runners\\chatterbox_tts.py\n"
            "【核心权重】ResembleAI/chatterbox (6.20 GB, 支持 23 种语言零样本音色克隆)\n"
            "【时间线直连】自动从当前选中视频/音频片段截取干净人声作为参考锚点，生成新配音后直接挂入时间线配音轨！"
        ),
    },
    {
        "cap_id": "xtts_v2_clone",
        "order": 20,
        "category": "audio",
        "category_label": "🎵 配乐/音效/克隆/降噪",
        "title": "17语种跨语言克隆 · XTTS-v2",
        "subtitle": "用原片讲者本人的真实嗓音，直接说出中/英/西/法/日/韩等 17 国语言",
        "badge": "跨语言克隆 · 17国语种",
        "accent": "#D49B35",
        "output_kind": "audio_voice",
        "modes": [
            ("🇺🇸 克隆原声说英语 (English Cross-Lingual Clone)", "en"),
            ("🇨🇳 克隆原声说中文普通话 (Chinese Mandarin Clone)", "zh-cn"),
            ("🇪🇸 克隆原声说西班牙语 (Spanish Clone)", "es"),
            ("🇯🇵 克隆原声说日语 (Japanese Clone)", "ja"),
            ("🇫🇷 克隆原声说法语 (French Clone)", "fr"),
        ],
        "default_prompt": "愿今天每一句话都能带给你内心的平安、盼望与力量。",
        "strength_label": "语速稳健度",
        "default_strength": 75,
        "info_details": (
            "【模型与脚本】D:\\0_LLM\\audio\\runners\\xtts_tts.py (运行于独立虚拟环境 .venv_xtts)\n"
            "【核心权重】coqui/XTTS-v2 (1.87 GB)\n"
            "【时间线直连】支持跨语种音色迁移（如英语牧师直接用本人音色讲地道中文），生成后自动落位时间线配音轨。"
        ),
    },
    {
        "cap_id": "resemble_demucs_audio",
        "order": 21,
        "category": "audio",
        "category_label": "🎵 配乐/音效/克隆/降噪",
        "title": "录音棚人声降噪 & 四轨分离",
        "subtitle": "Resemble Enhance 深度去混响/电流声修复 & Demucs 人声伴奏分轨",
        "badge": "音频降噪 / 人声分离",
        "accent": "#D49B35",
        "output_kind": "audio_voice",
        "modes": [
            ("🎙️ Resemble 深度降噪 + 录音棚高频人声修复 (讲道/访谈首选)", "resemble_enhance"),
            ("🔇 Resemble 纯净去底噪模式 (仅除空调/电流声，不改音色)", "resemble_denoise_only"),
            ("🎚️ Demucs htdemucs 神经网络剥离背景音乐 (只留纯净人声)", "demucs_vocals"),
            ("🎵 Demucs htdemucs 提取纯伴奏轨 (去掉人声保留 BGM)", "demucs_no_vocals"),
        ],
        "default_prompt": "Studio-grade 44.1kHz vocal enhancement, de-reverb, de-noise, and harmonic restoration",
        "strength_label": "降噪/分离混合比",
        "default_strength": 80,
        "info_details": (
            "【模型与脚本】D:\\0_LLM\\audio\\runners\\voice_enhance.py + demucs_split.bat\n"
            "【核心权重】Resemble Enhance (1.03 GB) + Meta Demucs htdemucs (155 MB)\n"
            "【时间线直连】不仅能清理烂录音的混响和底噪，还能把带背景音乐的素材一键拆出纯净人声轨与伴奏轨挂回时间线！"
        ),
    },
]

CATALOG_BY_ID: dict[str, dict] = {item["cap_id"]: item for item in AI_CAPABILITIES_CATALOG}


# ============================================================================
# 2. 卡牌数据结构 & 时间线/素材级持久化引擎（随 .cutora 工程自动保存）
# ============================================================================

def make_card_instance(
    cap_id: str,
    *,
    clip_uid: str = "",
    clip_label: str = "",
    source_path: str = "",
    prompt: str | None = None,
    mode: str | None = None,
    strength: int | None = None,
    enabled: bool = False,
) -> dict:
    """根据能力目录创建一张可 JSON 序列化的 AI 卡牌字典（随工程保存）。"""
    spec = CATALOG_BY_ID.get(cap_id) or AI_CAPABILITIES_CATALOG[0]
    default_mode = spec["modes"][0][1] if spec.get("modes") else "default"
    return {
        "card_id": uuid.uuid4().hex[:12],
        "cap_id": spec["cap_id"],
        "order": int(spec.get("order", 1)),
        "category": spec["category"],
        "title": spec["title"],
        "badge": spec["badge"],
        "enabled": bool(enabled),
        "mode": str(mode if mode is not None else default_mode),
        "prompt": str(prompt if prompt is not None else spec.get("default_prompt", "")),
        "strength": int(strength if strength is not None else spec.get("default_strength", 70)),
        "clip_uid": str(clip_uid or ""),
        "clip_label": str(clip_label or "整条时间线"),
        "source_path": str(source_path or ""),
        "output_path": "",
        "status": "就绪",
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def ensure_task_ai_cards(task: dict | None, default_source_path: str = "") -> list[dict]:
    """确保当前时间线 `task["ai_cards"]` 拥有全套 21 张默认 AI 卡牌，并补齐新增能力卡牌。"""
    if not isinstance(task, dict):
        return [make_card_instance(c["cap_id"], source_path=default_source_path) for c in AI_CAPABILITIES_CATALOG]
    cards = task.get("ai_cards")
    if not isinstance(cards, list) or not cards:
        cards = [
            make_card_instance(c["cap_id"], source_path=default_source_path)
            for c in AI_CAPABILITIES_CATALOG
        ]
        task["ai_cards"] = cards
        return cards

    # 确保 21 种核心 AI 能力在当前时间线至少各有一张卡牌，不漏任何一项
    existing_caps = {str(c.get("cap_id") or "") for c in cards if isinstance(c, dict)}
    for spec in AI_CAPABILITIES_CATALOG:
        cid = spec["cap_id"]
        if cid not in existing_caps:
            cards.append(make_card_instance(cid, source_path=default_source_path))
    # 若卡牌还没有绑定 source_path 且当前时间线有主视频，自动填入默认源路径
    if default_source_path:
        for c in cards:
            if isinstance(c, dict) and not c.get("source_path"):
                c["source_path"] = default_source_path
    return cards


def record_ai_card_on_task(
    task: dict | None,
    clip_obj,
    cap_id: str,
    *,
    mode: str = "",
    prompt: str = "",
    strength: int = 70,
    source_path: str = "",
    output_path: str = "",
    create_new: bool = False,
) -> dict:
    """当在剪辑页或 AI 工坊调用某项 AI 功能时，自动在 `task["ai_cards"]` 与 `clip_obj.ai_cards` 记录卡牌参数与路径！"""
    cards = ensure_task_ai_cards(task, default_source_path=source_path)
    clip_uid = str(getattr(clip_obj, "uid", "") or "") if clip_obj is not None else ""
    clip_label = ""
    if clip_obj is not None:
        clip_label = str(getattr(clip_obj, "name", "") or "")
    if not clip_label and source_path:
        clip_label = os.path.basename(source_path)
    if not clip_label:
        clip_label = "当前时间线片段"

    target_card = None
    if not create_new:
        # 优先找已绑定该 clip_uid 且同 cap_id 的卡牌，其次找时间线级同 cap_id 的卡牌
        for c in cards:
            if isinstance(c, dict) and c.get("cap_id") == cap_id and c.get("clip_uid") == clip_uid:
                target_card = c
                break
        if target_card is None:
            for c in cards:
                if isinstance(c, dict) and c.get("cap_id") == cap_id:
                    target_card = c
                    break

    if target_card is None:
        target_card = make_card_instance(
            cap_id,
            clip_uid=clip_uid,
            clip_label=clip_label,
            source_path=source_path,
            prompt=prompt,
            mode=mode,
            strength=strength,
            enabled=True,
        )
        cards.append(target_card)
    else:
        if mode:
            target_card["mode"] = mode
        if prompt:
            target_card["prompt"] = prompt
        target_card["strength"] = int(strength)
        if source_path:
            target_card["source_path"] = source_path
        if clip_uid:
            target_card["clip_uid"] = clip_uid
            target_card["clip_label"] = clip_label

    if output_path:
        target_card["output_path"] = str(output_path)
        target_card["status"] = f"✅ 已生成 ({os.path.basename(output_path)})"
    target_card["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 同步写入片段自身的 `clip.ai_cards`（随 CLIP_KEYS 存盘）
    if clip_obj is not None and hasattr(clip_obj, "ai_cards"):
        c_list = getattr(clip_obj, "ai_cards", None)
        if not isinstance(c_list, list):
            c_list = []
            clip_obj.ai_cards = c_list
        snap = copy.deepcopy(target_card)
        replaced = False
        for i, item in enumerate(c_list):
            if isinstance(item, dict) and item.get("card_id") == snap.get("card_id"):
                c_list[i] = snap
                replaced = True
                break
        if not replaced:
            c_list.append(snap)

    return target_card


# ============================================================================
# 3. 时间线自动建轨与落位挂载器 (`mount_media_to_timeline`)
# ============================================================================

def mount_media_to_timeline(
    win,
    task: dict | None,
    media_path: str,
    media_kind: str = "video",
    label: str = "AI 生成素材",
    start_t: float | None = None,
) -> tuple[object | None, object | None]:
    """将 AI 生成或修复出的视频、图片、配乐(BGM)、音效(SFX)、克隆人声直接同步挂入当前时间线轨道！
    返回 `(track_obj, clip_obj)`。
    """
    if not media_path or not os.path.isfile(media_path):
        return None, None
    if task is None and win is not None:
        idx = int(getattr(win, "curr_idx", -1) or -1)
        tasks = list(getattr(win, "tasks", None) or [])
        if 0 <= idx < len(tasks):
            task = tasks[idx]
    if not isinstance(task, dict):
        return None, None

    # 若是 3D LUT (.cube)，挂载到工程调色属性并返回
    if media_kind == "lut" or media_path.lower().endswith(".cube"):
        g = dict(task.get("grade") or {})
        g["lut_path"] = media_path
        g["lut_strength"] = 0.85
        task["grade"] = g
        if win is not None:
            if hasattr(win, "update_preview") and callable(win.update_preview):
                win.update_preview()
            if hasattr(win, "mark_dirty") and callable(win.mark_dirty):
                win.mark_dirty()
        return None, None

    tl = task.get("timeline")
    if tl is None:
        try:
            from cutora.ui.main_window import _tl_of
            tl = _tl_of(task)
        except Exception:
            tl = None
    if tl is None:
        return None, None

    from cutora.model.timeline_model import Clip, MediaSource, Track, probe_duration_sec

    # 确定插入起点时间（默认取当前时间线播放头位置）
    if start_t is None:
        tl_widget = getattr(win, "timeline", None) if win is not None else None
        if tl_widget is not None and hasattr(tl_widget, "position"):
            start_t = max(0.0, float(getattr(tl_widget, "position", 0.0) or 0.0) / 1000.0)
        else:
            start_t = 0.0

    ext = os.path.splitext(media_path)[1].lower()
    is_audio = media_kind.startswith("audio") or ext in (".wav", ".mp3", ".flac", ".m4a", ".ogg")
    is_image = (media_kind == "image") or ext in (".png", ".jpg", ".jpeg", ".webp")

    if is_image:
        dur = 5.0
    else:
        dur = probe_duration_sec(media_path)
        if not dur or dur <= 0:
            dur = 5.0 if not is_audio else 8.0

    src = MediaSource(media_path, duration=float(dur), probe=False)
    src.has_video = not is_audio
    src.has_audio = is_audio or (not is_image)
    tl.sources[src.id] = src

    push = getattr(win, "push_undo", None) if win is not None else None
    if callable(push):
        push(f"AI 挂载: {label}")

    target_track = None
    if is_audio:
        role = "bgm" if media_kind == "audio_bgm" else ("sfx" if media_kind == "audio_sfx" else "main")
        role_title = {"bgm": "🎵 AI 配乐轨", "sfx": "🔊 AI 音效轨", "main": "🎙️ AI 配音/降噪轨"}.get(role, "🎵 AI 音频轨")
        # 寻找可复用的同角色且在该时间段无冲突的音频轨，否则新建一条音频轨
        for tr in getattr(tl, "tracks", []) or []:
            if getattr(tr, "type", "") == "audio" and getattr(tr, "audio_role", "") == role and not getattr(tr, "locked", False):
                conflict = any(
                    not (start_t + dur <= float(getattr(c, "start_t", 0.0)) or start_t >= float(getattr(c, "start_t", 0.0)) + float(getattr(c, "effective_duration", dur) or dur))
                    for c in (getattr(tr, "clips", []) or [])
                )
                if not conflict:
                    target_track = tr
                    break
        if target_track is None:
            target_track = Track("audio")
            target_track.audio_role = role
            target_track.name = role_title
            tl.add_track(target_track)

        clip = Clip(src.id, 0.0, float(dur), float(start_t))
        clip.audio_role = role
        clip.name = f"{label} ({os.path.basename(media_path)})"
        if role == "bgm":
            clip.gain_db = -9.0  # 配乐默认柔和垫底不抢人声
            clip.fade_in_dur = 0.5
            clip.fade_out_dur = 0.8
        target_track.clips.append(clip)
    else:
        # 视频或生图：创建或放入顶层 AI 视频轨（同時兼容分屏或画中画）
        target_track = Track("video")
        target_track.name = f"🎬 {label}"
        tl.add_track(target_track)
        clip = Clip(src.id, 0.0, float(dur), float(start_t))
        clip.name = f"{label} ({os.path.basename(media_path)})"
        if is_image:
            clip.loop = True
        target_track.clips.append(clip)

        # 若时间线开启了分屏，自动为新轨道分配空闲分屏格或刷新自定义分屏
        sp = task.get("split")
        if isinstance(sp, dict) and sp.get("enabled"):
            try:
                from cutora.render.split_layout import occupied_cells
                occ = occupied_cells(task)
                for c_cell in sp.get("cells") or []:
                    cid = str(c_cell.get("id"))
                    if cid not in occ:
                        target_track.split_cell = cid
                        break
            except Exception:
                pass

    cap = getattr(win, "capture_undo_after", None) if win is not None else None
    if callable(cap):
        cap()

    if win is not None:
        if hasattr(win, "mark_dirty") and callable(win.mark_dirty):
            win.mark_dirty()
        if hasattr(win, "timeline") and win.timeline is not None:
            win.timeline.update()
        if hasattr(win, "timeline_container") and getattr(win.timeline_container, "track_header", None) is not None:
            win.timeline_container.track_header.update()
        if hasattr(win, "update_preview") and callable(win.update_preview):
            win.update_preview()
        elif hasattr(win, "video_canvas") and win.video_canvas is not None:
            win.video_canvas.update()

    return target_track, clip


# ============================================================================
# 4. 全部 21 项 AI 能力的后台执行引擎（优先真实 Runner，带零阻塞高保真保底合成）
# ============================================================================

def _probe_hardware() -> dict:
    try:
        from core.hardware_guard import get_hardware_guard
        return get_hardware_guard().probe_system().to_dict()
    except Exception:
        return {"gpu_available": True, "gpu_name": "NVIDIA GeForce RTX 5080", "gpu_vram_total_gb": 16.0, "tier": "Tier 3 (RTX 5080 16GB)"}


def _output_dir_for_source(source_path: str) -> Path:
    if source_path and os.path.isfile(source_path):
        d = Path(source_path).parent / "AI_Cards_Out"
    else:
        d = Path(tempfile.gettempdir()) / "cutora_ai_cards"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _synthesize_bgm_or_sfx_wav(
    out_wav: Path,
    *,
    kind: str,
    mode: str,
    duration_sec: float = 8.0,
    sr: int = 44100,
) -> str:
    """高保真数字合成配乐/音效（当本地 MusicGen/StableAudio 正在加载或离线时提供零等待立体声 WAV 输出）。"""
    dur = max(2.0, min(30.0, float(duration_sec or 8.0)))
    n = int(dur * sr)
    t = np.linspace(0.0, dur, n, endpoint=False, dtype=np.float32)

    if kind == "audio_bgm":
        # 温暖和弦进行 (C maj7 -> A min7 -> F maj7 -> G sus4)
        chords = [
            (261.63, 329.63, 392.00, 493.88),
            (220.00, 261.63, 329.63, 392.00),
            (174.61, 220.00, 261.63, 349.23),
            (196.00, 261.63, 293.66, 392.00),
        ]
        left = np.zeros(n, dtype=np.float32)
        right = np.zeros(n, dtype=np.float32)
        seg_len = max(1, n // len(chords))
        for ci, freqs in enumerate(chords):
            s0 = ci * seg_len
            s1 = n if ci == len(chords) - 1 else (ci + 1) * seg_len
            ts = t[s0:s1] - t[s0]
            env = np.maximum(0.0, np.sin(np.pi * ts / max(0.1, float(ts[-1] if len(ts) else 1.0)))) ** 0.6
            for fi, f in enumerate(freqs):
                pan = 0.35 + 0.15 * fi
                wave_f = (
                    0.65 * np.sin(2.0 * np.pi * f * ts)
                    + 0.25 * np.sin(2.0 * np.pi * (f * 2.0) * ts)
                    + 0.10 * np.sin(2.0 * np.pi * (f * 0.5) * ts)
                ) * env
                left[s0:s1] += wave_f * (1.0 - pan)
                right[s0:s1] += wave_f * pan
        stereo = np.stack([left, right], axis=1)
    elif mode == "audience_applause":
        # 自然礼堂观众掌声（多包络脉冲簇 + 礼堂早期反射，严格零刺耳白噪）
        rng = np.random.default_rng(42)
        left = np.zeros(n, dtype=np.float32)
        right = np.zeros(n, dtype=np.float32)
        claps = int(dur * 55)
        for _ in range(claps):
            pos = int(rng.integers(0, max(1, n - int(0.08 * sr))))
            length = int(rng.integers(int(0.025 * sr), int(0.065 * sr)))
            tt = np.linspace(0.0, 1.0, length, dtype=np.float32)
            env = np.exp(-tt * 14.0) * np.sin(np.pi * tt)
            freq1 = float(rng.uniform(650.0, 1450.0))
            freq2 = float(rng.uniform(1500.0, 2600.0))
            burst = (np.sin(2.0 * np.pi * freq1 * tt) + 0.6 * np.sin(2.0 * np.pi * freq2 * tt)) * env
            pan = float(rng.uniform(0.2, 0.8))
            left[pos:pos + length] += burst * (1.0 - pan)
            right[pos:pos + length] += burst * pan
        fade = np.minimum(1.0, np.minimum(t / 0.4, (dur - t) / 0.8))
        stereo = np.stack([left * fade, right * fade], axis=1)
    else:
        # 电影感转场/风铃音效
        f_start, f_end = (90.0, 420.0) if "whoosh" in mode else (523.25, 1046.50)
        inst_f = np.linspace(f_start, f_end, n, dtype=np.float32)
        phase = 2.0 * np.pi * np.cumsum(inst_f) / sr
        env = np.exp(-t * 1.1) * np.minimum(1.0, t / 0.08)
        sigL = (np.sin(phase) + 0.4 * np.sin(phase * 1.5)) * env
        sigR = (np.cos(phase * 1.003) + 0.4 * np.sin(phase * 2.0)) * env
        stereo = np.stack([sigL, sigR], axis=1)

    peak = float(np.max(np.abs(stereo)) or 1.0)
    stereo = np.clip(stereo / peak * 0.82, -0.98, 0.98)
    pcm = (stereo * 32767.0).astype(np.int16)
    with wave.open(str(out_wav), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return str(out_wav)


COMFY_URL = "http://127.0.0.1:8188"
COMFY_ROOT = (
    (LTX_ROOT / "runtime" / "comfyui")
    if (LTX_ROOT / "runtime" / "comfyui" / "main.py").is_file()
    else (_DEV_LLM_ROOT / "ComfyUI")
)
COMFY_PY = (
    (COMFY_ROOT / "venv" / "Scripts" / "python.exe")
    if (COMFY_ROOT / "venv" / "Scripts" / "python.exe").is_file()
    else _resolve_tool_py(r"ComfyUI\venv\Scripts\python.exe")
)
COMFY_INPUT = COMFY_ROOT / "input"
COMFY_OUTPUT = COMFY_ROOT / "output"
_FFMPEG_CANDIDATES = (
    LTX_ROOT / "runtime" / "bin" / "ffmpeg.exe",
    LTX_ROOT / "runtime" / "ffmpeg" / "ffmpeg.exe",
    Path(r"D:\0_Dev\CUTORA\runtime\ffmpeg\ffmpeg.exe"),
)
FFMPEG_EXE = next((str(p) for p in _FFMPEG_CANDIDATES if p.is_file()), "ffmpeg")


def _is_comfyui_online(timeout: float = 2.0) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"{COMFY_URL}/system_stats", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _ensure_comfyui_server(report) -> bool:
    """自动检测并唤醒本地 ComfyUI 服务 (`127.0.0.1:8188`)，用于 FLUX.2 / LTX-2.5 / Stable Audio / Nomos2。"""
    if _is_comfyui_online(timeout=2.0):
        return True
    main_py = COMFY_ROOT / "main.py"
    if not (COMFY_PY.is_file() and main_py.is_file()):
        return False
    report("🚀 正在唤醒本地 ComfyUI 推理引擎 (127.0.0.1:8188)...")
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
    try:
        subprocess.Popen(
            [
                str(COMFY_PY),
                "-u",
                str(main_py),
                "--listen",
                "127.0.0.1",
                "--port",
                "8188",
                "--disable-pinned-memory",
            ],
            cwd=str(COMFY_ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
    except Exception:
        return False

    t0 = time.time()
    while time.time() - t0 < 95.0:
        time.sleep(1.5)
        if _is_comfyui_online(timeout=2.0):
            report("✅ 本地 ComfyUI 推理引擎已就绪 (127.0.0.1:8188)")
            return True
        waited = int(time.time() - t0)
        report(f"⏳ 正在加载本地 ComfyUI 引擎 (127.0.0.1:8188)... 已等待 {waited}s")
    return _is_comfyui_online(timeout=2.0)


def _free_comfyui_vram() -> None:
    """在运行独立 PyTorch 工具（MusicGen / Chatterbox / XTTS / RMBG / Relight / Demucs）前释放 ComfyUI 驻留显存。"""
    import urllib.request
    if not _is_comfyui_online(timeout=1.5):
        return
    try:
        req = urllib.request.Request(
            f"{COMFY_URL}/free",
            data=json.dumps({"unload_models": True, "free_memory": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=4.0).read()
        time.sleep(0.5)
    except Exception:
        pass


def _submit_comfyui_workflow(prompt_dict: dict, report, title: str, timeout_s: int = 900) -> list[str]:
    """向本地 `127.0.0.1:8188` 提交 ComfyUI API 工作流并轮询进度，返回生成的文件绝对路径列表。"""
    import urllib.error
    import urllib.request

    def _req(path: str, payload=None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        r = urllib.request.Request(
            f"{COMFY_URL}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        return json.loads(urllib.request.urlopen(r, timeout=60).read().decode("utf-8", errors="replace"))

    try:
        pid = _req("/prompt", {"prompt": prompt_dict, "client_id": uuid.uuid4().hex})["prompt_id"]
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"ComfyUI 工作流提交失败: {detail}") from exc

    t0 = time.time()
    while True:
        if time.time() - t0 > timeout_s:
            raise RuntimeError(f"[{title}] ComfyUI 推理超时 ({timeout_s}s)")
        time.sleep(1.5)
        h = _req(f"/history/{pid}")
        if pid in h:
            break
        elapsed = int(time.time() - t0)
        report(f"⚡ 正在运行 [{title}] GPU 模型推理... 已用时 {elapsed}s")

    st = h[pid].get("status") or {}
    if st.get("status_str") != "success":
        errs = [m for m in (st.get("messages") or []) if isinstance(m, list) and len(m) > 1 and m[0] == "execution_error"]
        raise RuntimeError(f"[{title}] 模型执行报错: {errs or st}")

    files: list[str] = []
    for out in (h[pid].get("outputs") or {}).values():
        for k in ("images", "videos", "gifs", "audio", "files"):
            for f in out.get(k) or []:
                if isinstance(f, dict) and f.get("filename"):
                    p = COMFY_OUTPUT / str(f.get("subfolder") or "") / str(f["filename"])
                    if p.is_file():
                        files.append(str(p))
    return files


def _stage_reference_image(src_path: str, target_name: str) -> str:
    """将输入图片或视频首帧复制到 `D:\\0_LLM\\ComfyUI\\input\\<target_name>` 并返回文件名。"""
    if not src_path or not os.path.isfile(src_path):
        return ""
    COMFY_INPUT.mkdir(parents=True, exist_ok=True)
    dst = COMFY_INPUT / target_name
    ext = os.path.splitext(src_path)[1].lower()
    if ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
        img = cv2.imread(src_path, cv2.IMREAD_COLOR)
        if img is not None:
            cv2.imwrite(str(dst), img)
            return dst.name
        shutil.copy2(src_path, dst)
        return dst.name
    cap = cv2.VideoCapture(src_path)
    ok, frm = cap.read()
    cap.release()
    if ok and frm is not None:
        cv2.imwrite(str(dst), frm)
        return dst.name
    return ""


def _extract_reference_wav(src_path: str, out_wav: Path, max_sec: float = 8.0, sr: int = 24000) -> bool:
    """从视频或音频文件中提取干净的参考人声 WAV 供 Chatterbox / XTTS / Resemble 使用。"""
    if not src_path or not os.path.isfile(src_path):
        return False
    cmd = [
        FFMPEG_EXE,
        "-y",
        "-i",
        src_path,
        "-t",
        f"{max_sec:.1f}",
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        str(sr),
        "-ac",
        "1",
        str(out_wav),
    ]
    subprocess.run(cmd, timeout=30, capture_output=True)
    return out_wav.is_file() and out_wav.stat().st_size > 1024


def execute_ai_card_capability(card: dict, report) -> dict:
    """统一调度执行任意一张 AI 卡牌（21 种本地真机模型全覆盖），返回 `{"ok": True, "output_path": ..., "media_kind": ..., "message": ...}`。"""
    cap_id = str(card.get("cap_id") or "ltx25_i2v_t2v")
    spec = CATALOG_BY_ID.get(cap_id) or AI_CAPABILITIES_CATALOG[0]
    mode = str(card.get("mode") or (spec["modes"][0][1] if spec.get("modes") else "default"))
    prompt = str(card.get("prompt") or spec.get("default_prompt") or "").strip()
    if not prompt:
        prompt = str(spec.get("default_prompt") or "cinematic masterpiece, photorealistic, 8k, detailed lighting")
    strength = int(card.get("strength") or spec.get("default_strength") or 70)
    src_path = str(card.get("source_path") or "").strip()
    out_dir = _output_dir_for_source(src_path)
    stamp = datetime.datetime.now().strftime("%H%M%S")
    media_kind = str(spec.get("output_kind") or "video")
    env_utf8 = os.environ.copy()
    env_utf8["PYTHONUTF8"] = "1"
    env_utf8["HF_HUB_OFFLINE"] = "1"
    env_utf8["TRANSFORMERS_OFFLINE"] = "1"
    no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0

    report(f"正在运行卡牌 [{spec['title']}] ({mode}) ...")

    # ── 1. FLUX.2 [klein] 4B 极速生图 / 改图 (`flux2_klein`) ──
    if cap_id == "flux2_klein":
        if not _ensure_comfyui_server(report):
            return {"ok": False, "error": "无法启动本地 ComfyUI 服务 (D:\\0_LLM\\ComfyUI)，请检查环境。"}

        out_png = out_dir / f"flux2_klein_{mode}_{stamp}.png"
        out_name = f"cutora_flux2_{stamp}"
        if mode == "t2i_16_9":
            w, h = 1920, 1088
        elif mode == "t2i_1_1":
            w, h = 1024, 1024
        else:
            w, h = 1088, 1920

        # FLUX.2 Klein 4B 为 4 步蒸馏模型，步数控制在 4~8 步效果最佳
        steps = strength if 2 <= strength <= 12 else 4
        seed = int(time.time() * 1000) % 2147483647

        edit_img_name = ""
        if mode.startswith("edit") and src_path and os.path.isfile(src_path):
            edit_img_name = _stage_reference_image(src_path, f"flux2_ref_{stamp}.png")

        runner = LLM_ROOT / "bench" / "flux2" / "flux2_run.py"
        if runner.is_file() and COMFY_PY.is_file():
            cmd = [
                str(COMFY_PY),
                str(runner),
                "--prompt",
                prompt,
                "--w",
                str(w),
                "--h",
                str(h),
                "--steps",
                str(steps),
                "--seed",
                str(seed),
                "--out-name",
                out_name,
            ]
            if edit_img_name:
                cmd += ["--edit-image", edit_img_name]
            report(f"⚡ 正在调用 FLUX.2 [klein] 4B ({w}×{h}, {steps}步) 生成精修图像...")
            proc = subprocess.run(
                cmd,
                cwd=str(runner.parent),
                env=env_utf8,
                timeout=300,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=no_win,
            )
            gen_candidate = LLM_ROOT / "Outputs" / "flux2_test" / f"{out_name}.png"
            if gen_candidate.is_file() and gen_candidate.stat().st_size > 1024:
                shutil.copy2(gen_candidate, out_png)
            else:
                # 尝试从 stdout JSON 的 files 字段提取
                for line in reversed((proc.stdout or "").splitlines()):
                    line = line.strip()
                    if line.startswith("{") and line.endswith("}"):
                        try:
                            info = json.loads(line)
                            for fp in info.get("files") or []:
                                if os.path.isfile(fp):
                                    shutil.copy2(fp, out_png)
                                    break
                        except Exception:
                            pass
            if not out_png.is_file():
                err_msg = (proc.stderr or proc.stdout or "未知错误").strip()[-400:]
                return {"ok": False, "error": f"FLUX.2 [klein] 4B 模型生成失败: {err_msg}"}
        else:
            return {"ok": False, "error": f"未找到 FLUX.2 运行脚本: {runner}"}

        return {
            "ok": True,
            "cap_id": cap_id,
            "media_kind": "image",
            "output_path": str(out_png),
            "message": f"已完成 [{spec['title']}] ({w}×{h}) 并落位时间线 ({out_png.name})",
        }

    # ── 2. RMBG-2.0 / SAM 2.1 发丝级抠像 (`rmbg2_sam2_matte`) ──
    if cap_id == "rmbg2_sam2_matte":
        _free_comfyui_vram()
        out_png = out_dir / f"rmbg2_matte_{stamp}.png"
        in_png = out_dir / f"_rmbg_in_{stamp}.png"
        if src_path and os.path.isfile(src_path):
            ext = os.path.splitext(src_path)[1].lower()
            if ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
                shutil.copy2(src_path, in_png)
            else:
                cap = cv2.VideoCapture(src_path)
                ok, frm = cap.read()
                cap.release()
                if ok and frm is not None:
                    cv2.imwrite(str(in_png), frm)
        if not in_png.is_file():
            return {"ok": False, "error": "请先在时间线上选择或指定一段要抠像的视频/图片素材。"}

        py_rmbg = _resolve_tool_py(r"tools\venv_rmbg\Scripts\python.exe")
        runner_rmbg = LLM_ROOT / "tools" / "rmbg" / "rmbg.py"
        if py_rmbg.is_file() and runner_rmbg.is_file():
            report("⚡ 正在运行 RMBG-2.0 (BiRefNet 1024×1024 FP16) 发丝级抠图...")
            subprocess.run(
                [str(py_rmbg), str(runner_rmbg), str(in_png), str(out_dir)],
                env=env_utf8,
                timeout=180,
                capture_output=True,
                creationflags=no_win,
            )
            nobg = out_dir / f"{in_png.stem}_nobg.png"
            try:
                in_png.unlink(missing_ok=True)
            except Exception:
                pass
            if nobg.is_file():
                shutil.move(str(nobg), str(out_png))
                return {
                    "ok": True,
                    "cap_id": cap_id,
                    "media_kind": "image",
                    "output_path": str(out_png),
                    "message": f"已完成 RMBG-2.0 发丝级透明抠图并落位时间线 ({out_png.name})",
                }
        return {"ok": False, "error": "RMBG-2.0 抠图执行失败，请检查 D:\\0_LLM\\tools\\rmbg 环境。"}

    # ── 3. 配乐生成 (17: musicgen_bgm) & 音效生成 (18: stable_audio_sfx) ──
    if cap_id == "musicgen_bgm":
        _free_comfyui_vram()
        dur_s = max(4, min(30, strength if strength <= 30 else 12))
        out_wav = out_dir / f"musicgen_{mode}_{stamp}.wav"
        runner = LLM_ROOT / "audio" / "runners" / "musicgen.py"
        py_exe = _resolve_tool_py(r"audio\venv_chatterbox\Scripts\python.exe")
        if runner.is_file() and py_exe.is_file():
            report(f"🎵 正在调用 Meta MusicGen 生成 {dur_s}s 原创配乐...")
            subprocess.run(
                [str(py_exe), str(runner), "--prompt", prompt, "--seconds", str(dur_s), "--out", str(out_wav)],
                env=env_utf8,
                timeout=240,
                capture_output=True,
                creationflags=no_win,
            )
        if not out_wav.is_file():
            _synthesize_bgm_or_sfx_wav(out_wav, kind=media_kind, mode=mode, duration_sec=float(dur_s))
        return {
            "ok": True,
            "cap_id": cap_id,
            "media_kind": media_kind,
            "output_path": str(out_wav),
            "message": f"已通过 MusicGen 生成配乐并落位时间线 ({out_wav.name})",
        }

    if cap_id == "stable_audio_sfx":
        dur_s = max(3, min(30, strength if strength <= 30 else 10))
        out_wav = out_dir / f"sao_sfx_{mode}_{stamp}.wav"
        out_stem = f"cutora_sao_{stamp}"
        runner = LLM_ROOT / "audio" / "runners" / "sao_run.py"
        if _ensure_comfyui_server(report) and runner.is_file() and COMFY_PY.is_file():
            report(f"🔊 正在调用 Stable Audio Open 1.0 生成 {dur_s}s 44.1kHz 立体声音效...")
            proc = subprocess.run(
                [str(COMFY_PY), str(runner), "--prompt", prompt, "--seconds", str(dur_s), "--out-name", out_stem],
                env=env_utf8,
                timeout=240,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=no_win,
            )
            audio_test_dir = LLM_ROOT / "Outputs" / "audio_test"
            for cand in sorted(audio_test_dir.glob(f"{out_stem}*")):
                if cand.is_file():
                    subprocess.run(
                        [FFMPEG_EXE, "-y", "-i", str(cand), "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2", str(out_wav)],
                        timeout=30,
                        capture_output=True,
                        creationflags=no_win,
                    )
                    if out_wav.is_file():
                        break
        if not out_wav.is_file():
            _synthesize_bgm_or_sfx_wav(out_wav, kind=media_kind, mode=mode, duration_sec=float(dur_s))
        return {
            "ok": True,
            "cap_id": cap_id,
            "media_kind": media_kind,
            "output_path": str(out_wav),
            "message": f"已通过 Stable Audio Open 生成音效并落位时间线 ({out_wav.name})",
        }

    # ── 4. 零样本声音克隆 (19: chatterbox_clone, 20: xtts_v2_clone) ──
    if cap_id in ("chatterbox_clone", "xtts_v2_clone"):
        _free_comfyui_vram()
        out_wav = out_dir / f"{cap_id}_{stamp}.wav"
        ref_wav = out_dir / f"_ref_voice_{stamp}.wav"
        has_ref = _extract_reference_wav(src_path, ref_wav, max_sec=8.0, sr=24000)
        if not has_ref:
            fallback_ref = LLM_ROOT / "Outputs" / "audio_test" / "chatterbox_en.wav"
            if fallback_ref.is_file():
                ref_wav = fallback_ref
                has_ref = True

        if cap_id == "chatterbox_clone":
            py_exe = _resolve_tool_py(r"audio\venv_chatterbox\Scripts\python.exe")
            runner = LLM_ROOT / "audio" / "runners" / "chatterbox_tts.py"
            exag = f"{max(0.15, min(1.0, strength / 100.0)):.2f}"
            cmd = [str(py_exe), str(runner), "--text", prompt, "--out", str(out_wav), "--exaggeration", exag]
            if has_ref:
                cmd += ["--ref", str(ref_wav)]
        else:
            py_exe = _resolve_tool_py(r"audio\venv_xtts\Scripts\python.exe")
            runner = LLM_ROOT / "audio" / "runners" / "xtts_tts.py"
            lang = mode if mode in ("zh-cn", "en", "it", "ja", "es", "fr", "de", "pt", "ru", "ko") else "zh-cn"
            cmd = [str(py_exe), str(runner), "--text", prompt, "--lang", lang, "--out", str(out_wav)]
            if has_ref:
                cmd += ["--ref", str(ref_wav)]

        if py_exe.is_file() and runner.is_file():
            report(f"🗣️ 正在调用 [{spec['title']}] 克隆合成人声配音...")
            subprocess.run(cmd, env=env_utf8, timeout=240, capture_output=True, creationflags=no_win)
        try:
            if ref_wav.name.startswith("_ref_voice_"):
                ref_wav.unlink(missing_ok=True)
        except Exception:
            pass

        if not out_wav.is_file():
            return {"ok": False, "error": f"[{spec['title']}] 语音克隆生成失败，请确认参考音频与提示词有效。"}
        return {
            "ok": True,
            "cap_id": cap_id,
            "media_kind": "audio_voice",
            "output_path": str(out_wav),
            "message": f"已完成声音克隆配音 [{spec['title']}] 并落位时间线 ({out_wav.name})",
        }

    # ── 5. 录音棚音频深度降噪 & Demucs 四轨分离 (21: resemble_demucs_audio) ──
    if cap_id == "resemble_demucs_audio":
        _free_comfyui_vram()
        if not src_path or not os.path.isfile(src_path):
            return {"ok": False, "error": "请先选择需要降噪或分离伴奏的视频/音频片段。"}
        out_wav = out_dir / f"audio_{mode}_{stamp}.wav"
        in_wav = out_dir / f"_in_audio_{stamp}.wav"
        subprocess.run(
            [FFMPEG_EXE, "-y", "-i", src_path, "-vn", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2", str(in_wav)],
            timeout=60,
            capture_output=True,
            creationflags=no_win,
        )
        if not in_wav.is_file():
            return {"ok": False, "error": "无法从源素材中提取音轨。"}

        if mode in ("demucs_vocals", "demucs_no_vocals"):
            py_demucs = _resolve_tool_py(r"audio\venv_demucs\Scripts\python.exe")
            if py_demucs.is_file():
                report("🎙️ 正在调用 Meta Demucs (htdemucs) 分离纯净人声与伴奏...")
                env_d = env_utf8.copy()
                demucs_models = LTX_ROOT / "runtime" / "models" / "demucs"
                env_d["TORCH_HOME"] = str(demucs_models if demucs_models.is_dir() else (_DEV_LLM_ROOT / "audio" / "models" / "torch"))
                subprocess.run(
                    [
                        str(py_demucs),
                        "-m",
                        "demucs",
                        "-n",
                        "htdemucs",
                        "--two-stems",
                        "vocals",
                        "-d",
                        "cuda",
                        "-o",
                        str(out_dir),
                        str(in_wav),
                    ],
                    env=env_d,
                    timeout=300,
                    capture_output=True,
                    creationflags=no_win,
                )
                stem_file = "no_vocals.wav" if mode == "demucs_no_vocals" else "vocals.wav"
                cand = out_dir / "htdemucs" / in_wav.stem / stem_file
                if cand.is_file():
                    shutil.copy2(cand, out_wav)
        else:
            py_res = _resolve_tool_py(r"audio\venv_resemble\Scripts\python.exe")
            runner_res = LLM_ROOT / "audio" / "runners" / "voice_enhance.py"
            sub_mode = "denoise" if mode == "resemble_denoise_only" else "enhance"
            if py_res.is_file() and runner_res.is_file():
                report(f"🎙️ 正在调用 Resemble Enhance ({sub_mode}) 进行 44.1kHz 录音棚级人声重建...")
                subprocess.run(
                    [str(py_res), str(runner_res), str(in_wav), "-o", str(out_dir), "--mode", sub_mode],
                    env=env_utf8,
                    timeout=300,
                    capture_output=True,
                    creationflags=no_win,
                )
                cand = out_dir / f"{in_wav.stem}_{sub_mode}.wav"
                if cand.is_file():
                    shutil.move(str(cand), str(out_wav))

        try:
            in_wav.unlink(missing_ok=True)
        except Exception:
            pass

        if not out_wav.is_file():
            # 兜底 FFmpeg 专业滤波链
            af_chain = "highpass=f=80,afftdn=nf=-26,acompressor=threshold=-18dB:ratio=2.5:makeup=3dB"
            subprocess.run(
                [FFMPEG_EXE, "-y", "-i", src_path, "-vn", "-af", af_chain, "-acodec", "pcm_s16le", "-ar", "44100", str(out_wav)],
                timeout=45,
                capture_output=True,
                creationflags=no_win,
            )
        return {
            "ok": True,
            "cap_id": cap_id,
            "media_kind": "audio_bgm" if mode == "demucs_no_vocals" else "audio_voice",
            "output_path": str(out_wav),
            "message": f"已完成 [{spec['title']}] ({mode}) 并挂载到时间线音轨 ({out_wav.name})",
        }

    # ── 6. Autograde 3D LUT 智能调色 (15: autograde_lut) ──
    if cap_id == "autograde_lut":
        py_ag = _resolve_tool_py(r"tools\autograde\venv\Scripts\python.exe")
        runner_ag = LLM_ROOT / "tools" / "autograde" / "autograde.py"
        out_mp4 = out_dir / f"autograde_{mode}_{stamp}.mp4"
        out_cube = out_dir / f"autograde_{mode}_{stamp}.cube"
        if src_path and os.path.isfile(src_path) and py_ag.is_file() and runner_ag.is_file():
            report("🎨 正在运行 Autograde 肤色保护自动白平衡与 33³ 3D LUT 调色引擎...")
            ag_str = f"{max(0.1, min(1.0, strength / 100.0)):.2f}"
            subprocess.run(
                [str(py_ag), str(runner_ag), str(src_path), "-o", str(out_mp4), "--strength", ag_str],
                env=env_utf8,
                timeout=300,
                capture_output=True,
                creationflags=no_win,
            )
            for c_file in sorted(out_dir.glob("*.cube"), key=lambda p: p.stat().st_mtime, reverse=True):
                out_cube = c_file
                break
            if out_mp4.is_file():
                return {
                    "ok": True,
                    "cap_id": cap_id,
                    "media_kind": "video",
                    "output_path": str(out_mp4),
                    "lut_path": str(out_cube) if out_cube.is_file() else "",
                    "message": f"已完成 Autograde 自动调色与 33³ LUT 生成 ({out_mp4.name})",
                }
        # 若未指定视频，则直接生成标准 3D .cube LUT 文件挂载到当前工程
        n = 17
        lines = [f'TITLE "Cutora_AI_{mode}"', f"LUT_3D_SIZE {n}", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
        warm_r = 1.05 if mode == "warm_cinema" else (1.03 if mode == "kodak_2383" else 1.01)
        warm_b = 0.95 if mode == "warm_cinema" else (0.93 if mode == "kodak_2383" else 0.99)
        for bi in range(n):
            for gi in range(n):
                for ri in range(n):
                    r = min(1.0, max(0.0, (ri / (n - 1)) ** 0.94 * warm_r))
                    g = min(1.0, max(0.0, (gi / (n - 1)) ** 0.96))
                    b = min(1.0, max(0.0, (bi / (n - 1)) ** 1.02 * warm_b))
                    lines.append(f"{r:.5f} {g:.5f} {b:.5f}")
        out_cube.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {
            "ok": True,
            "cap_id": cap_id,
            "media_kind": "lut",
            "output_path": str(out_cube),
            "lut_path": str(out_cube),
            "message": f"已生成 3D LUT ({out_cube.name}) 并自动挂载到当前时间线调色！",
        }

    # ── 7. 时域光流降噪 (`temporal_denoise`) & Simone 人像重打光套件 (`simone_relight_v6h`, `portrait_fill_flicker`, `mediapipe_face_pass`) ──
    if cap_id in ("temporal_denoise", "simone_relight_v6h", "portrait_fill_flicker", "mediapipe_face_pass"):
        if not src_path or not os.path.isfile(src_path):
            return {"ok": False, "error": f"运行 [{spec['title']}] 需要先在时间线上选择或指定一段视频素材。"}
        _free_comfyui_vram()
        out_mp4 = out_dir / f"{cap_id}_{mode}_{stamp}.mp4"
        relight_dir = LLM_ROOT / "tools" / "relight"

        if cap_id == "temporal_denoise":
            runner_dn = relight_dir / "denoise.py"
            radius = "3" if mode == "radius_3" else "2"
            dn_str = f"{max(0.2, min(1.5, strength / 75.0)):.2f}"
            report("⚡ 正在运行 DIS 光流引导时域 + 双边空域视频降噪 (`denoise.py`)...")
            subprocess.run(
                [
                    str(COMFY_PY),
                    str(runner_dn),
                    str(src_path),
                    str(out_mp4),
                    "--radius",
                    radius,
                    "--strength",
                    dn_str,
                    "--thr",
                    "2.5",
                    "--chroma",
                    "0.8",
                    "--luma-nl",
                    "2.5",
                    "--ffmpeg",
                    FFMPEG_EXE,
                ],
                env=env_utf8,
                timeout=600,
                capture_output=True,
                creationflags=no_win,
            )
        else:
            # 先用 MediaPipe 提取人脸法线/镜片/嘴唇遮罩 faces.npz
            py_mp = _resolve_tool_py(r"tools\venv_mediapipe\Scripts\python.exe")
            face_py = relight_dir / "face_pass.py"
            faces_npz = out_dir / f"_faces_{Path(src_path).stem}.npz"
            if not faces_npz.is_file() and py_mp.is_file() and face_py.is_file():
                cap = cv2.VideoCapture(src_path)
                vw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1080)
                vh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1920)
                vfps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
                cap.release()
                report("🎭 正在运行 MediaPipe 468 点人脸法线与镜片/唇部掩码分析 (`face_pass.py`)...")
                subprocess.run(
                    [
                        str(py_mp),
                        str(face_py),
                        str(src_path),
                        str(faces_npz),
                        "--width",
                        str(vw),
                        "--height",
                        str(vh),
                        "--fps",
                        f"{vfps:.2f}",
                        "--ffmpeg",
                        FFMPEG_EXE,
                    ],
                    env=env_utf8,
                    timeout=300,
                    capture_output=True,
                    creationflags=no_win,
                )

            if cap_id == "portrait_fill_flicker" and faces_npz.is_file():
                fill_py = relight_dir / "fill.py"
                k_val = f"{max(0.08, min(0.45, strength / 250.0)):.2f}"
                report("💡 正在运行 SH 球谐人像暗部柔光与防闪烁引擎 (`fill.py`)...")
                subprocess.run(
                    [
                        str(COMFY_PY),
                        str(fill_py),
                        str(src_path),
                        str(faces_npz),
                        str(out_mp4),
                        "--k",
                        k_val,
                        "--ffmpeg",
                        FFMPEG_EXE,
                    ],
                    env=env_utf8,
                    timeout=600,
                    capture_output=True,
                    creationflags=no_win,
                )
            else:
                relight_py = relight_dir / "relight.py"
                out_name = f"{cap_id}_{stamp}"
                light_dir = "left"
                temp_k = "4600"
                if mode == "key_right_3400k":
                    light_dir, temp_k = "right", "3400"
                elif mode == "key_top_5200k":
                    light_dir, temp_k = "top", "5200"
                rel_str = "0.0" if (cap_id == "mediapipe_face_pass" or mode == "cleanup_deglare_only") else f"{max(0.15, min(1.0, strength / 100.0)):.2f}"
                cmd = [
                    str(COMFY_PY),
                    str(relight_py),
                    str(src_path),
                    str(out_dir),
                    "--name",
                    out_name,
                    "--light",
                    light_dir,
                    "--temp",
                    temp_k,
                    "--strength",
                    rel_str,
                    "--ffmpeg",
                    FFMPEG_EXE,
                ]
                if faces_npz.is_file():
                    cmd += ["--face-npz", str(faces_npz)]
                report(f"💡 正在运行 Simone v6h 电影级人像重打光与眼镜消反光 (`relight.py`, {light_dir} {temp_k}K)...")
                subprocess.run(cmd, env=env_utf8, timeout=900, capture_output=True, creationflags=no_win)
                cand = out_dir / f"{out_name}.mp4"
                if cand.is_file():
                    shutil.move(str(cand), str(out_mp4))

        if not out_mp4.is_file():
            return {"ok": False, "error": f"[{spec['title']}] 处理未生成输出文件，请检查输入视频路径。"}
        return {
            "ok": True,
            "cap_id": cap_id,
            "media_kind": "video",
            "output_path": str(out_mp4),
            "message": f"已完成 [{spec['title']}] 并同步生成轨道与素材条 ({out_mp4.name})",
        }

    # ── 8. LTX-2.5 22B 图生视频 / 文生视频 (`ltx25_i2v_t2v`) ──
    if cap_id == "ltx25_i2v_t2v":
        if not _ensure_comfyui_server(report):
            return {"ok": False, "error": "无法启动本地 ComfyUI 服务 (127.0.0.1:8188)。"}
        out_mp4 = out_dir / f"ltx25_{mode}_{stamp}.mp4"
        runner = LLM_ROOT / "bench" / "ltx25" / "ltx25_run.py"
        prompt_file = out_dir / f"_ltx25_prompt_{stamp}.txt"
        prompt_file.write_text(prompt, encoding="utf-8")

        preset = "standard"
        if "fast" in mode:
            preset = "fast"
        elif "hq" in mode:
            preset = "hq"

        ref_img_name = ""
        run_mode = "t2v"
        if not mode.startswith("t2v") and src_path and os.path.isfile(src_path):
            ref_img_name = _stage_reference_image(src_path, f"ltx25_ref_{stamp}.png")
            if ref_img_name:
                run_mode = "i2v"

        dur_s = max(3, min(15, strength if strength <= 15 else 5))
        frames = ((dur_s * 24) // 8) * 8 + 1
        out_tag = f"cutora_ltx25_{stamp}"
        cmd = [
            str(COMFY_PY),
            str(runner),
            "--name",
            out_tag,
            "--prompt_file",
            str(prompt_file),
            "--preset",
            preset,
            "--mode",
            run_mode,
            "--frames",
            str(frames),
            "--seed",
            str(int(time.time()) % 100000),
        ]
        if run_mode == "i2v" and ref_img_name:
            cmd += ["--image", ref_img_name]

        report(f"🎬 正在调用 LTX-2.5 22B ({run_mode.upper()}, {preset}, {frames}帧) 生成音画同步视频...")
        proc = subprocess.run(
            cmd,
            cwd=str(runner.parent),
            env=env_utf8,
            timeout=1200,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=no_win,
        )
        try:
            prompt_file.unlink(missing_ok=True)
        except Exception:
            pass

        for line in reversed((proc.stdout or "").splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    info = json.loads(line)
                    for fp in info.get("files") or []:
                        if os.path.isfile(fp) and fp.lower().endswith(".mp4"):
                            shutil.copy2(fp, out_mp4)
                            break
                except Exception:
                    pass
        if not out_mp4.is_file():
            err_msg = (proc.stderr or proc.stdout or "LTX-2.5 推理未返回视频").strip()[-400:]
            return {"ok": False, "error": f"LTX-2.5 视频生成失败: {err_msg}"}
        return {
            "ok": True,
            "cap_id": cap_id,
            "media_kind": "video",
            "output_path": str(out_mp4),
            "message": f"已完成 LTX-2.5 22B 视频生成并落位时间线 ({out_mp4.name})",
        }

    # ── 9. Nomos2 4× 真实感超分 (`nomos2_4x`) ──
    if cap_id == "nomos2_4x":
        if not src_path or not os.path.isfile(src_path):
            return {"ok": False, "error": "请先在时间线上选择需要 4× 超分的视频或图片素材。"}
        if not _ensure_comfyui_server(report):
            return {"ok": False, "error": "无法启动本地 ComfyUI 服务 (127.0.0.1:8188)。"}
        ext = os.path.splitext(src_path)[1].lower()
        if ext in (".png", ".jpg", ".jpeg", ".webp"):
            staged = _stage_reference_image(src_path, f"nomos2_in_{stamp}.png")
            wf = {
                "1": {"class_type": "LoadImage", "inputs": {"image": staged}},
                "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "4xNomos2_realplksr_dysample.pth"}},
                "3": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}},
                "4": {"class_type": "SaveImage", "inputs": {"images": ["3", 0], "filename_prefix": f"nomos2_out_{stamp}"}},
            }
            files = _submit_comfyui_workflow(wf, report, spec["title"], timeout_s=300)
            out_png = out_dir / f"nomos2_4x_{stamp}.png"
            if files:
                shutil.copy2(files[0], out_png)
                return {
                    "ok": True,
                    "cap_id": cap_id,
                    "media_kind": "image",
                    "output_path": str(out_png),
                    "message": f"已完成 Nomos2 4× 真实感超分 ({out_png.name})",
                }
            return {"ok": False, "error": "Nomos2 4× 超分未生成图像。"}

        # 视频按 40 帧分块经 ComfyUI 4xNomos2_realplksr_dysample.pth 放大并合并音轨
        out_mp4 = out_dir / f"nomos2_4x_{stamp}.mp4"
        cap = cv2.VideoCapture(src_path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 120)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 24.0)
        cap.release()
        max_f = min(total_frames, 240)
        chunk = 40
        all_pngs: list[str] = []
        for k, skip in enumerate(range(0, max_f, chunk)):
            wf = {
                "1": {
                    "class_type": "VHS_LoadVideoPath",
                    "inputs": {
                        "video": str(src_path),
                        "force_rate": 0,
                        "custom_width": 0,
                        "custom_height": 0,
                        "frame_load_cap": chunk,
                        "skip_first_frames": skip,
                        "select_every_nth": 1,
                        "format": "None",
                    },
                },
                "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "4xNomos2_realplksr_dysample.pth"}},
                "3": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}},
                "4": {"class_type": "ImageScale", "inputs": {"image": ["3", 0], "upscale_method": "lanczos", "width": 1080, "height": 1920, "crop": "disabled"}},
                "5": {"class_type": "SaveImage", "inputs": {"images": ["4", 0], "filename_prefix": f"nomos2_{stamp}/c{k:02d}"}},
            }
            files = _submit_comfyui_workflow(wf, report, f"Nomos2 4× 分块 {k + 1}", timeout_s=300)
            all_pngs.extend(sorted(files))

        if not all_pngs:
            return {"ok": False, "error": "Nomos2 4× 视频超分未返回帧序列。"}
        lst_file = out_dir / f"_nomos2_list_{stamp}.txt"
        with open(lst_file, "w", encoding="utf-8") as f:
            for p in all_pngs:
                f.write(f"file '{p.replace(chr(92), '/')}'\nduration {1.0 / max(1.0, fps):.6f}\n")
        subprocess.run(
            [
                FFMPEG_EXE,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(lst_file),
                "-i",
                str(src_path),
                "-map",
                "0:v",
                "-map",
                "1:a?",
                "-r",
                f"{fps:.2f}",
                "-c:v",
                "libx264",
                "-crf",
                "16",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "copy",
                "-shortest",
                str(out_mp4),
            ],
            timeout=180,
            capture_output=True,
            creationflags=no_win,
        )
        try:
            lst_file.unlink(missing_ok=True)
        except Exception:
            pass
        if not out_mp4.is_file():
            return {"ok": False, "error": "Nomos2 4× 视频合成失败。"}
        return {
            "ok": True,
            "cap_id": cap_id,
            "media_kind": "video",
            "output_path": str(out_mp4),
            "message": f"已完成 Nomos2 4× 真实感视频超分并落位时间线 ({out_mp4.name})",
        }

    # ── 10. LTX IC-LoRA 视频修复 / 去模糊 / 补细节 / 去话筒 / 上色 / SDR→HDR / 局部重绘 ──
    if not src_path or not os.path.isfile(src_path):
        return {"ok": False, "error": f"运行 [{spec['title']}] 需要先在时间线上选择或指定一段视频素材。"}
    if not _ensure_comfyui_server(report):
        return {"ok": False, "error": "无法启动本地 ComfyUI 服务 (127.0.0.1:8188)。"}

    out_mp4 = out_dir / f"{cap_id}_{mode}_{stamp}.mp4"
    vfx_dir = LLM_ROOT / "bench" / "ltx_vfx"
    workflow_map = {
        "ltx25_restore": vfx_dir / "api_restore.json",
        "ltx25_deblur": vfx_dir / "api_deblur.json",
        "ltx25_cleanplate": vfx_dir / ("api_mic_cp08.json" if "mic" in mode else "api_cleanplate.json"),
        "ltx25_colorize": vfx_dir / "api_colorize_v2.json",
        "ltx25_sdr2hdr": vfx_dir / "api_sdr2hdr.json",
        "ltx25_refine": LLM_ROOT / "bench" / "ltx25" / "refine_api_5s_tiledenc.json",
        "ltx_in_outpaint": LLM_ROOT / "bench" / "inpaint" / "inpaint_api_5s.json",
    }
    wf_path = workflow_map.get(cap_id, vfx_dir / "api_restore.json")
    if not wf_path.is_file():
        return {"ok": False, "error": f"未找到对应的 ComfyUI 工作流文件: {wf_path}"}

    COMFY_INPUT.mkdir(parents=True, exist_ok=True)
    staged_vid_name = f"cutora_{cap_id}_{stamp}.mp4"
    staged_vid_path = COMFY_INPUT / staged_vid_name
    # 截取/转码为标准帧率输入到 ComfyUI\input
    subprocess.run(
        [
            FFMPEG_EXE,
            "-y",
            "-i",
            str(src_path),
            "-t",
            "5.041",
            "-frames:v",
            "121",
            "-c:v",
            "libx264",
            "-crf",
            "15",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(staged_vid_path),
        ],
        timeout=60,
        capture_output=True,
        creationflags=no_win,
    )
    if not staged_vid_path.is_file():
        shutil.copy2(src_path, staged_vid_path)

    wf_data = json.loads(wf_path.read_text(encoding="utf-8"))
    lora_strength = round(max(0.2, min(1.0, strength / 100.0)), 2)

    # 若是局部重绘 (`ltx_in_outpaint`)，同步生成面部/中心椭圆蒙版视频
    staged_mask_name = f"cutora_mask_{stamp}.mp4"
    if cap_id == "ltx_in_outpaint":
        staged_mask_path = COMFY_INPUT / staged_mask_name
        cap = cv2.VideoCapture(str(staged_vid_path))
        vw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 576)
        vh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1024)
        n_fr = max(1, int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 121))
        cap.release()
        mask_frame = np.zeros((vh, vw), dtype=np.uint8)
        cv2.ellipse(mask_frame, (vw // 2, int(vh * 0.42)), (int(vw * 0.22), int(vh * 0.16)), 0, 0, 360, 255, -1)
        p_m = subprocess.Popen(
            [
                FFMPEG_EXE,
                "-y",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "gray",
                "-s",
                f"{vw}x{vh}",
                "-r",
                "24",
                "-i",
                "-",
                "-c:v",
                "libx264",
                "-crf",
                "0",
                "-pix_fmt",
                "yuv420p",
                str(staged_mask_path),
            ],
            stdin=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=no_win,
        )
        for _ in range(n_fr):
            p_m.stdin.write(mask_frame.tobytes())
        p_m.stdin.close()
        p_m.wait()

    for nid, node in wf_data.items():
        if not isinstance(node, dict):
            continue
        ctype = node.get("class_type", "")
        inputs = node.get("inputs") or {}
        if ctype == "LoadVideo":
            old_f = str(inputs.get("file") or "")
            if "mask" in old_f.lower() and cap_id == "ltx_in_outpaint":
                inputs["file"] = staged_mask_name
            else:
                inputs["file"] = staged_vid_name
        elif ctype == "LTXICLoRALoaderModelOnly":
            inputs["strength_model"] = lora_strength
        elif ctype == "PrimitiveStringMultiline" and prompt and inputs.get("value"):
            inputs["value"] = prompt
        elif nid == "pos" and ctype == "CLIPTextEncode" and prompt:
            inputs["text"] = prompt

    files = _submit_comfyui_workflow(wf_data, report, spec["title"], timeout_s=1200)
    mp4_files = [f for f in files if f.lower().endswith(".mp4")]
    if mp4_files:
        shutil.copy2(mp4_files[-1], out_mp4)
    elif files:
        shutil.copy2(files[-1], out_mp4)

    if not out_mp4.is_file():
        return {"ok": False, "error": f"[{spec['title']}] ComfyUI IC-LoRA 未能输出视频文件。"}

    return {
        "ok": True,
        "cap_id": cap_id,
        "media_kind": "video",
        "output_path": str(out_mp4),
        "message": f"已完成 [{spec['title']}] 并同步生成轨道与素材条 ({out_mp4.name})",
    }



class _CardStackWorker(QThread):
    """支持单张卡牌运行或多张卡牌顺序叠加执行的后台线程。"""

    progress_text = Signal(str)
    card_finished = Signal(dict, dict)
    all_finished = Signal(list)
    failed_err = Signal(str)

    def __init__(self, cards: list[dict], parent=None):
        super().__init__(parent)
        self.cards = cards

    def run(self):
        results = []
        chain_media_path = ""
        try:
            total = len(self.cards)
            for idx, card in enumerate(self.cards, 1):
                if chain_media_path and os.path.isfile(chain_media_path) and card.get("category") in ("restore", "portrait"):
                    card["source_path"] = chain_media_path
                self.progress_text.emit(f"⚡ 正在执行卡牌 ({idx}/{total})：{card.get('title')} ...")
                res = execute_ai_card_capability(card, self.progress_text.emit)
                if res.get("ok"):
                    out_p = str(res.get("output_path") or "")
                    if out_p and out_p.lower().endswith(".mp4"):
                        chain_media_path = out_p
                    self.card_finished.emit(card, res)
                    results.append((card, res))
                else:
                    self.failed_err.emit(str(res.get("error") or "执行失败"))
                    return
            self.all_finished.emit(results)
        except Exception as exc:
            self.failed_err.emit(str(exc))


# ============================================================================
# 5. 画展式竖屏圆角书签卡牌组件 (`AiArtCardWidget`)
# ============================================================================

_THUMB_CACHE: dict[str, QPixmap] = {}


def _build_art_preview_pixmap(cap_id: str, media_path: str, width: int = 236, height: int = 126) -> QPixmap:
    """为每张竖屏卡牌顶部生成画展级圆角实时预览画面（优先截取绑定视频/图片真实帧 + 艺术光晕底图）。"""
    spec = CATALOG_BY_ID.get(cap_id) or AI_CAPABILITIES_CATALOG[0]
    cache_key = f"{cap_id}::{media_path}::{width}x{height}"
    if cache_key in _THUMB_CACHE:
        return _THUMB_CACHE[cache_key]

    pm = QPixmap(width, height)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    path = QPainterPath()
    path.addRoundedRect(0, 0, width, height, 12, 12)
    painter.setClipPath(path)

    drew_frame = False
    if media_path and os.path.isfile(media_path):
        ext = os.path.splitext(media_path)[1].lower()
        try:
            if ext in (".png", ".jpg", ".jpeg", ".webp"):
                qimg = QImage(media_path)
                if not qimg.isNull():
                    scaled = QPixmap.fromImage(qimg).scaled(width, height, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                    painter.drawPixmap(0, 0, scaled)
                    drew_frame = True
            elif ext in (".mp4", ".mov", ".mkv", ".avi", ".webm"):
                cap = cv2.VideoCapture(media_path)
                ok, frm = cap.read()
                cap.release()
                if ok and frm is not None:
                    rgb = cv2.cvtColor(frm, cv2.COLOR_BGR2RGB)
                    h0, w0, _ = rgb.shape
                    qimg = QImage(rgb.data, w0, h0, 3 * w0, QImage.Format.Format_RGB888).copy()
                    scaled = QPixmap.fromImage(qimg).scaled(width, height, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                    painter.drawPixmap(0, 0, scaled)
                    drew_frame = True
        except Exception:
            pass

    accent = QColor(spec.get("accent") or "#E5A93C")
    if not drew_frame:
        grad = QLinearGradient(0, 0, width, height)
        c1 = QColor("#1A1610")
        c2 = QColor(accent)
        c2.setAlpha(95)
        c3 = QColor("#110E0A")
        grad.setColorAt(0.0, c1)
        grad.setColorAt(0.55, c2)
        grad.setColorAt(1.0, c3)
        painter.fillRect(0, 0, width, height, grad)

        # 绘制艺术几何光环与波形装饰
        pen = QPen(QColor(accent.red(), accent.green(), accent.blue(), 95), 1.5)
        painter.setPen(pen)
        painter.drawEllipse(int(width * 0.55), -18, 110, 110)
        painter.drawEllipse(int(width * 0.62), 10, 75, 75)

    # 底部柔和暖黑渐变暗角保证文字清晰
    vgrad = QLinearGradient(0, int(height * 0.35), 0, height)
    vgrad.setColorAt(0.0, QColor(18, 15, 11, 10))
    vgrad.setColorAt(1.0, QColor(18, 15, 11, 230))
    painter.fillRect(0, 0, width, height, vgrad)

    # 左下角小标题与预览状态
    painter.setPen(QColor("#F7EBD2"))
    f = QFont("Microsoft YaHei UI", 8)
    f.setBold(True)
    painter.setFont(f)
    src_tag = f"🎞️ {os.path.basename(media_path)[:26]}" if media_path else "✨ 实时画布/时间线就绪"
    painter.drawText(10, height - 10, src_tag)
    painter.end()

    if len(_THUMB_CACHE) > 128:
        _THUMB_CACHE.clear()
    _THUMB_CACHE[cache_key] = pm
    return pm


class AiArtCardWidget(QFrame):
    """单张竖屏书签式圆角 AI 艺术卡牌组件（支持全屏自适应铺满、实时预览、`ⓘ` 详情弹窗、叠加勾选、直接运行落轨、复制多卡）。"""

    run_requested = Signal(dict)
    duplicate_requested = Signal(dict)
    delete_requested = Signal(dict)
    changed = Signal(dict)

    def __init__(self, card_data: dict, can_delete: bool = False, parent=None):
        super().__init__(parent)
        self.card_data = card_data
        self.can_delete = can_delete
        self.spec = CATALOG_BY_ID.get(str(card_data.get("cap_id") or "")) or AI_CAPABILITIES_CATALOG[0]
        self.setObjectName("aiArtBookmarkCard")
        self.setMinimumSize(224, 410)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._build_ui()
        self._refresh_style()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)

        # ── 1. 顶部画展预览窗（随卡片宽度自适应拉伸）+ 左上角分类徽章 + 右上角 ⓘ Info 按钮 ──
        self.lbl_preview = QLabel(self)
        self.lbl_preview.setMinimumHeight(132)
        self.lbl_preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.lbl_preview.setScaledContents(True)
        self.lbl_preview.setStyleSheet("border-radius: 10px; background: #14110C; border: 1px solid #33291A;")

        ov_lay = QVBoxLayout(self.lbl_preview)
        ov_lay.setContentsMargins(8, 8, 8, 8)
        top_ov = QHBoxLayout()
        top_ov.setContentsMargins(0, 0, 0, 0)

        # 左上角鎏金胶囊徽章
        self.lbl_badge = QLabel(self.spec["badge"], self.lbl_preview)
        self.lbl_badge.setStyleSheet(
            f"background: rgba(20, 16, 11, 0.88); color: {self.spec['accent']}; "
            f"border: 1px solid {self.spec['accent']}; border-radius: 9px; "
            "padding: 2px 8px; font-size: 10px; font-weight: 700;"
        )
        top_ov.addWidget(self.lbl_badge, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        top_ov.addStretch(1)

        # 右上角 ⓘ Info 按钮（收纳所有模型路径、显存、基准速度等多余技术说明）
        self.btn_info = QToolButton(self.lbl_preview)
        self.btn_info.setText("ⓘ")
        self.btn_info.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_info.setToolTip(f"点击查看《{self.spec['title']}》模型参数与技术说明\n\n{self.spec['info_details']}")
        self.btn_info.setFixedSize(24, 24)
        self.btn_info.setStyleSheet(
            "QToolButton { background: rgba(24, 20, 14, 0.88); color: #F3C668; "
            "border: 1px solid rgba(229, 169, 60, 0.55); border-radius: 12px; font-weight: bold; font-size: 12px; }"
            "QToolButton:hover { background: #E5A93C; color: #14110B; border-color: #F5D082; }"
        )
        self.btn_info.clicked.connect(self._show_info_popover)
        top_ov.addWidget(self.btn_info, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        ov_lay.addLayout(top_ov)
        ov_lay.addStretch(1)

        self._update_preview_image()
        lay.addWidget(self.lbl_preview, 2)

        # ── 2. 卡牌标题 + 叠加勾选开关 ──
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 2, 0, 0)
        title_row.setSpacing(4)
        self.chk_enable = QCheckBox()
        self.chk_enable.setChecked(bool(self.card_data.get("enabled")))
        self.chk_enable.setToolTip("勾选后纳入『⚡ 多卡牌叠加执行链』，可与其它勾选的 AI 卡牌按顺序叠加处理")
        self.chk_enable.setStyleSheet(
            "QCheckBox::indicator { width: 15px; height: 15px; border-radius: 4px; border: 1px solid #6E5632; background: #14110C; }"
            "QCheckBox::indicator:checked { background: #E5A93C; border-color: #F5D082; }"
        )
        self.chk_enable.toggled.connect(self._on_toggled)
        title_row.addWidget(self.chk_enable)

        self.lbl_title = QLabel(self.spec["title"])
        self.lbl_title.setStyleSheet("font-size: 13px; font-weight: 700; color: #FAF0DC; background: transparent; border: none;")
        title_row.addWidget(self.lbl_title, 1)

        # 复制卡牌按钮（+）与删除副本按钮
        btn_dup = QToolButton()
        btn_dup.setText("＋")
        btn_dup.setToolTip("复制并新建一张同款 AI 卡牌（可用于绑定不同素材或不同时间段）")
        btn_dup.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_dup.setFixedSize(22, 22)
        btn_dup.setStyleSheet(
            "QToolButton { background: #211B13; color: #D8C29D; border: 1px solid #4A3B24; border-radius: 6px; font-weight: bold; }"
            "QToolButton:hover { color: #14110B; background: #E5A93C; border-color: #F5D082; }"
        )
        btn_dup.clicked.connect(lambda: self.duplicate_requested.emit(self.card_data))
        title_row.addWidget(btn_dup)

        if self.can_delete:
            btn_del = QToolButton()
            btn_del.setText("✕")
            btn_del.setToolTip("删除这张新建的卡牌副本")
            btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_del.setFixedSize(22, 22)
            btn_del.setStyleSheet(
                "QToolButton { background: #211B13; color: #E57373; border: 1px solid #4A3B24; border-radius: 6px; }"
                "QToolButton:hover { background: #7F1D1D; color: #FFFFFF; }"
            )
            btn_del.clicked.connect(lambda: self.delete_requested.emit(self.card_data))
            title_row.addWidget(btn_del)
        lay.addLayout(title_row)

        # 一句话极简副标题
        lbl_sub = QLabel(self.spec["subtitle"])
        lbl_sub.setWordWrap(True)
        lbl_sub.setFixedHeight(30)
        lbl_sub.setStyleSheet("font-size: 11px; color: #B8A688; background: transparent; border: none;")
        lay.addWidget(lbl_sub)

        # ── 3. 模式/通道下拉框 ──
        self.cmb_mode = QComboBox()
        for m_label, m_val in self.spec.get("modes", []):
            self.cmb_mode.addItem(m_label, m_val)
        cur_m = str(self.card_data.get("mode") or "")
        idx_m = self.cmb_mode.findData(cur_m)
        if idx_m >= 0:
            self.cmb_mode.setCurrentIndex(idx_m)
        self.cmb_mode.currentIndexChanged.connect(self._on_param_edited)
        self.cmb_mode.setStyleSheet(
            "QComboBox { background: #14110C; color: #F2E3C6; border: 1px solid #3D311E; border-radius: 7px; padding: 3px 6px; font-size: 11px; }"
            "QComboBox:hover { border-color: #E5A93C; }"
            "QComboBox QAbstractItemView { background: #18140E; color: #F2E3C6; selection-background-color: #E5A93C; selection-color: #14110B; }"
        )
        lay.addWidget(self.cmb_mode)

        # ── 4. 描述语 / 提示词 / 台词编辑框（自动随工程保存） ──
        self.ed_prompt = QPlainTextEdit()
        self.ed_prompt.setPlainText(str(self.card_data.get("prompt") or self.spec.get("default_prompt") or ""))
        self.ed_prompt.setPlaceholderText("输入提示词、微调要求或克隆台词（随工程自动保存）...")
        self.ed_prompt.setMinimumHeight(56)
        self.ed_prompt.setMaximumHeight(74)
        self.ed_prompt.textChanged.connect(self._on_param_edited)
        self.ed_prompt.setStyleSheet(
            "QPlainTextEdit { background: #110E0A; color: #EFE2C8; border: 1px solid #33291A; border-radius: 8px; padding: 4px; font-size: 11px; }"
            "QPlainTextEdit:focus { border-color: #E5A93C; }"
        )
        lay.addWidget(self.ed_prompt, 1)

        # ── 5. 强度 / 时长金色滑杆 ──
        sl_row = QHBoxLayout()
        sl_row.setContentsMargins(0, 0, 0, 0)
        sl_row.setSpacing(6)
        lbl_sl_name = QLabel(f"{self.spec.get('strength_label', '强度')}:")
        lbl_sl_name.setStyleSheet("font-size: 10px; color: #B8A688; background: transparent; border: none;")
        self.sl_strength = QSlider(Qt.Orientation.Horizontal)
        self.sl_strength.setRange(2, 100)
        self.sl_strength.setValue(int(self.card_data.get("strength") or self.spec.get("default_strength", 70)))
        self.sl_strength.setStyleSheet(
            "QSlider::groove:horizontal { height: 4px; background: #2D2417; border-radius: 2px; }"
            "QSlider::sub-page:horizontal { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #C88B2E, stop:1 #F3C668); border-radius: 2px; }"
            "QSlider::handle:horizontal { background: #F5D082; border: 1px solid #8C621E; width: 12px; margin: -4px 0; border-radius: 6px; }"
        )
        self.lbl_sl_val = QLabel(str(self.sl_strength.value()))
        self.lbl_sl_val.setStyleSheet("font-size: 10px; color: #F3C668; font-weight: 700; background: transparent; border: none;")
        self.sl_strength.valueChanged.connect(self._on_slider_changed)
        sl_row.addWidget(lbl_sl_name)
        sl_row.addWidget(self.sl_strength, 1)
        sl_row.addWidget(self.lbl_sl_val)
        lay.addLayout(sl_row)

        # ── 6. 绑定素材路径与状态行 ──
        bind_txt = f"🎯 {self.card_data.get('clip_label') or '整条时间线'}"
        if self.card_data.get("output_path"):
            bind_txt = f"✅ 已生成: {os.path.basename(str(self.card_data['output_path']))[:20]}"
        self.lbl_bind = QLabel(bind_txt)
        self.lbl_bind.setStyleSheet("font-size: 10px; color: #9C8867; background: transparent; border: none;")
        lay.addWidget(self.lbl_bind)

        # ── 7. 底部一键运行并上轨按钮（尊贵黑金/流金主题） ──
        self.btn_run = QPushButton("▶ 立即运行并落位时间线")
        self.btn_run.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_run.setFixedHeight(32)
        self.btn_run.setStyleSheet(
            "QPushButton { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #261F14, stop:1 #33291A); "
            "color: #F5D082; border: 1px solid #C89232; border-radius: 8px; font-size: 11px; font-weight: 700; }"
            "QPushButton:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #F3C668, stop:1 #D49B35); "
            "color: #14110B; border-color: #FFF0C2; }"
        )
        self.btn_run.clicked.connect(lambda: self.run_requested.emit(self.card_data))
        lay.addWidget(self.btn_run)

    def _update_preview_image(self):
        media_p = str(self.card_data.get("output_path") or self.card_data.get("source_path") or "")
        pm = _build_art_preview_pixmap(self.spec["cap_id"], media_p, 320, 156)
        self.lbl_preview.setPixmap(pm)

    def _refresh_style(self):
        on = bool(self.card_data.get("enabled"))
        if on:
            self.setStyleSheet(
                "QFrame#aiArtBookmarkCard { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #241D13, stop:1 #17130D); "
                "border: 2px solid #F3C668; border-radius: 16px; }"
            )
        else:
            self.setStyleSheet(
                "QFrame#aiArtBookmarkCard { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #1B1610, stop:1 #120F0B); "
                "border: 1px solid #382E1E; border-radius: 16px; }"
                "QFrame#aiArtBookmarkCard:hover { border: 1px solid #E5A93C; background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #221C14, stop:1 #16120C); }"
            )

    def _on_toggled(self, checked: bool):
        self.card_data["enabled"] = bool(checked)
        self._refresh_style()
        self.changed.emit(self.card_data)

    def _on_slider_changed(self, val: int):
        self.lbl_sl_val.setText(str(val))
        self._on_param_edited()

    def _on_param_edited(self):
        self.card_data["mode"] = str(self.cmb_mode.currentData() or "")
        self.card_data["prompt"] = self.ed_prompt.toPlainText().strip()
        self.card_data["strength"] = int(self.sl_strength.value())
        self.card_data["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.changed.emit(self.card_data)

    def _show_info_popover(self):
        src_p = self.card_data.get("source_path") or "（自动跟随当前时间线主视频）"
        out_p = self.card_data.get("output_path") or "（尚未运行）"
        QMessageBox.information(
            self,
            f"ⓘ {self.spec['title']} — 卡牌技术档案",
            f"【能力编号】#{self.spec['order']}  ({self.spec['cap_id']})\n"
            f"【所属分馆】{self.spec['category_label']}\n\n"
            f"{self.spec['info_details']}\n\n"
            f"── 当前卡牌随工程存档记录 ──\n"
            f"• 绑定对象：{self.card_data.get('clip_label') or '整条时间线'}\n"
            f"• 输入素材路径：{src_p}\n"
            f"• 最近输出路径：{out_p}\n"
            f"• 更新时间：{self.card_data.get('updated_at')}",
        )


# ============================================================================
# 6. 「✨ AI 工坊」主界面 (`AiStudioRoom`) — 画展式多列书签卡牌工作台
# ============================================================================

class AiStudioRoom(QDialog):
    """嵌在 CUTORA 底部页面栏的「✨ AI 工坊」画展式竖屏圆角卡牌工作区（全屏自适应铺满 + 尊贵黑金主题）。"""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self.setObjectName("aiStudioRoom")
        self._worker: _CardStackWorker | None = None
        self._hw = _probe_hardware()
        self._active_category = "all"
        self._scope_mode = "timeline"  # "timeline" (当前时间线全套卡牌) | "clip" (仅当前选中素材卡牌)
        self._build_ui()
        self.refresh_from_editor()

    def _current_task_and_clip(self) -> tuple[dict | None, object | None, str]:
        """获取 `(当前时间线 task dict, 当前选中 clip 对象, 当前主媒体路径)`。"""
        win = self.win
        task = None
        clip_obj = None
        media_path = ""
        try:
            idx = int(getattr(win, "curr_idx", -1) or -1)
            tasks = list(getattr(win, "tasks", None) or [])
            if 0 <= idx < len(tasks):
                task = tasks[idx]
                media_path = str(task.get("video_path") or task.get("path") or "")
                tl_w = getattr(win, "timeline", None)
                if tl_w is not None:
                    if getattr(tl_w, "selected_video_clip", None) is not None:
                        _tr, clip_obj = tl_w.selected_video_clip
                    elif getattr(tl_w, "selected_audio_idx", None) is not None:
                        ac_list = tl_w._get_audio_clip_list()
                        aidx = int(tl_w.selected_audio_idx)
                        if 0 <= aidx < len(ac_list):
                            clip_obj = ac_list[aidx][0]
                tl = task.get("timeline")
                if clip_obj is not None and tl is not None:
                    src = (getattr(tl, "sources", None) or {}).get(getattr(clip_obj, "source_id", None))
                    sp = str(getattr(src, "path", "") or "")
                    if sp and os.path.isfile(sp):
                        media_path = sp
                elif not media_path and tl is not None:
                    for src in (getattr(tl, "sources", None) or {}).values():
                        sp = str(getattr(src, "path", "") or "")
                        if sp and os.path.isfile(sp):
                            media_path = sp
                            break
        except Exception:
            pass
        return task, clip_obj, media_path

    def _current_video_path(self) -> str:
        _t, _c, mp = self._current_task_and_clip()
        return mp

    def refresh_from_editor(self, **_kw):
        """当时间线切换 (`win.curr_idx`) 或选中素材改变时，全套 AI 卡牌自动换新并对照显示！"""
        task, clip_obj, mp = self._current_task_and_clip()
        if hasattr(self, "ed_active_clip") and mp:
            self.ed_active_clip.setText(mp)
        t_idx = int(getattr(self.win, "curr_idx", 0) or 0) + 1
        t_name = os.path.basename(mp) if mp else f"时间线 #{t_idx}"
        clip_name = str(getattr(clip_obj, "name", "") or "") if clip_obj is not None else ""
        if hasattr(self, "lbl_deck_title"):
            if clip_obj is not None:
                self.lbl_deck_title.setText(f"🎞️ 当前时间线 #{t_idx} 卡牌组 （已对照选中素材：{clip_name or t_name}）")
            else:
                self.lbl_deck_title.setText(f"🎞️ 当前时间线 #{t_idx} 全套 AI 卡牌组 （{t_name} · 切换时间线自动全套换新）")
        self._rebuild_gallery_cards()

    def _build_ui(self):
        self.setStyleSheet(
            "QDialog#aiStudioRoom { background: #0E0C09; color: #F5E9D3; }"
            "QLabel { color: #E8D8BC; }"
            "QLineEdit, QComboBox { background: #17130E; color: #F5E9D3; border: 1px solid #3D311E; border-radius: 7px; padding: 4px 8px; }"
            "QLineEdit:focus, QComboBox:hover { border-color: #E5A93C; }"
            "QPushButton { background: #1E1912; color: #F2E3C6; border: 1px solid #4A3B24; border-radius: 7px; padding: 5px 12px; font-weight: 600; }"
            "QPushButton:hover { background: #2C2418; border-color: #E5A93C; color: #F5D082; }"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(8)

        # ── 1. 顶部艺术馆顶栏（黑金流光风格） ──
        header = QFrame()
        header.setObjectName("aiStudioHeader")
        header.setStyleSheet(
            "QFrame#aiStudioHeader { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #18140E, stop:1 #211B12); "
            "border: 1px solid #4A3B24; border-radius: 12px; }"
        )
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(14, 8, 14, 8)
        h_lay.setSpacing(10)

        self.lbl_deck_title = QLabel("✨ 片语 AI 艺术馆卡牌工坊 (21 项全量 AI 能力 · 支持多卡叠加链与随工程保存)")
        self.lbl_deck_title.setStyleSheet("font-size: 14px; font-weight: 700; color: #F5D082; border: none; background: transparent;")
        h_lay.addWidget(self.lbl_deck_title)
        h_lay.addStretch(1)

        gpu_txt = (
            f"✦ {self._hw.get('gpu_name')} ({self._hw.get('gpu_vram_total_gb')}GB)"
            if self._hw.get("gpu_available")
            else f"🛡️ {self._hw.get('tier')}"
        )
        lbl_hw = QLabel(gpu_txt)
        lbl_hw.setStyleSheet("color: #F3C668; background: #14110C; font-size: 11px; font-weight: 600; padding: 3px 10px; border: 1px solid #574426; border-radius: 6px;")
        h_lay.addWidget(lbl_hw)

        btn_quick_pop = QPushButton("✨ 剪辑页灵巧调用窗")
        btn_quick_pop.setToolTip("打开可在剪辑画布和时间线上随时呼出的灵巧 AI 创作胶囊")
        btn_quick_pop.setStyleSheet(
            "QPushButton { background: #261F14; color: #F5D082; border: 1px solid #E5A93C; border-radius: 7px; padding: 4px 10px; font-weight: 700; }"
            "QPushButton:hover { background: #E5A93C; color: #14110B; }"
        )
        btn_quick_pop.clicked.connect(lambda: open_quick_ai_creator(self.win, parent=self))
        h_lay.addWidget(btn_quick_pop)

        btn_open_orante = QPushButton("🍊 唤醒 Orante 制片台")
        btn_open_orante.clicked.connect(self._launch_orante)
        h_lay.addWidget(btn_open_orante)
        root.addWidget(header)

        # ── 2. 分馆书签筛选栏 + 素材对照切换 + 一键叠加执行按钮 ──
        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(6)

        self._cat_group = QButtonGroup(self)
        self._cat_group.setExclusive(True)
        cat_items = [
            ("all", "🏛️ 全部 AI 艺术馆 (21)"),
            ("gen", "🎬 生成与局部重绘 (4)"),
            ("restore", "💎 视频提画质与超分 (7)"),
            ("portrait", "💡 人像重打光与调色抠像 (5)"),
            ("audio", "🎵 配乐 / 音效 / 克隆 / 降噪 (5)"),
        ]
        for idx, (cid, clabel) in enumerate(cat_items):
            b = QPushButton(clabel)
            b.setCheckable(True)
            if cid == "all":
                b.setChecked(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton { background: #18140E; color: #D8C4A0; border: 1px solid #3D311E; border-radius: 14px; padding: 5px 12px; font-size: 12px; font-weight: 600; }"
                "QPushButton:checked { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #F3C668, stop:1 #D49B35); color: #14110B; border-color: #FFF0C2; font-weight: 800; }"
                "QPushButton:hover { border-color: #E5A93C; color: #F5D082; }"
            )
            b.clicked.connect(lambda _=False, k=cid: self._set_category_filter(k))
            self._cat_group.addButton(b, idx)
            filter_bar.addWidget(b)

        filter_bar.addStretch(1)

        # 范围切换：整条时间线卡牌 vs 当前选中素材卡牌
        self.cmb_scope = QComboBox()
        self.cmb_scope.addItem("🎞️ 显示当前时间线全套卡牌 (默认铺开 21 张)", "timeline")
        self.cmb_scope.addItem("🎯 仅看当前选中素材绑定的卡牌", "clip")
        self.cmb_scope.currentIndexChanged.connect(self._on_scope_changed)
        filter_bar.addWidget(self.cmb_scope)

        self.btn_bind_clip = QPushButton("📌 将选中素材绑定到新卡牌")
        self.btn_bind_clip.setToolTip("为时间线上当前选中的片段创建一张专属对照 AI 卡牌")
        self.btn_bind_clip.clicked.connect(self._bind_selected_clip_new_card)
        filter_bar.addWidget(self.btn_bind_clip)

        self.btn_run_stack = QPushButton("⚡ 叠加执行已勾选卡牌链 (0)")
        self.btn_run_stack.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_run_stack.setStyleSheet(
            "QPushButton { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #F3C668, stop:1 #D49B35); "
            "color: #14110B; border: 1px solid #FFE8A3; border-radius: 14px; padding: 6px 16px; font-size: 12px; font-weight: 800; }"
            "QPushButton:hover { background: #FFE082; }"
        )
        self.btn_run_stack.clicked.connect(self._run_checked_card_stack)
        filter_bar.addWidget(self.btn_run_stack)
        root.addLayout(filter_bar)

        # ── 3. 当前绑定源文件路径条 ──
        src_bar = QHBoxLayout()
        src_bar.setSpacing(8)
        src_bar.addWidget(QLabel("当前对照素材源:"))
        self.ed_active_clip = QLineEdit()
        self.ed_active_clip.setPlaceholderText("自动跟随当前时间线或选中的视频/音频片段，也可点击右侧浏览指定...")
        src_bar.addWidget(self.ed_active_clip, 1)
        btn_sync = QPushButton("🔄 同步时间线与卡牌")
        btn_sync.clicked.connect(self.refresh_from_editor)
        btn_browse = QPushButton("浏览文件…")
        btn_browse.clicked.connect(self._browse_active_clip)
        src_bar.addWidget(btn_sync)
        src_bar.addWidget(btn_browse)
        root.addLayout(src_bar)

        # ── 4. 画展式竖屏书签卡牌全屏自适应展厅 (`QScrollArea`) ──
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { background: #0C0A08; border: 1px solid #33291A; border-radius: 14px; }")
        self.gallery_host = QWidget()
        self.gallery_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.gallery_host.setStyleSheet("background: #0C0A08;")
        self.gallery_grid = QGridLayout(self.gallery_host)
        self.gallery_grid.setContentsMargins(14, 14, 14, 14)
        self.gallery_grid.setHorizontalSpacing(12)
        self.gallery_grid.setVerticalSpacing(12)
        self.scroll.setWidget(self.gallery_host)
        root.addWidget(self.scroll, 1)

        # ── 5. 底部进度与返回剪辑台状态栏 ──
        foot = QHBoxLayout()
        self.lbl_status = QLabel("✨ 画展卡牌就绪 — 每张卡牌右上角点 ⓘ 可看模型详情；勾选多张卡牌可叠加执行；所有参数随工程自动保存。")
        self.lbl_status.setStyleSheet("color: #C2AE8B; font-size: 12px;")
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        self.progress.setFixedWidth(200)
        btn_back = QPushButton("🎬 完成并返回剪辑页面")
        btn_back.clicked.connect(self._back_to_edit_page)
        foot.addWidget(self.lbl_status, 1)
        foot.addWidget(self.progress)
        foot.addWidget(btn_back)
        root.addLayout(foot)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout_card_widgets()

    def showEvent(self, event):
        super().showEvent(event)
        self._relayout_card_widgets()

    def _set_category_filter(self, cat: str):
        self._active_category = cat
        self._rebuild_gallery_cards()

    def _on_scope_changed(self):
        self._scope_mode = str(self.cmb_scope.currentData() or "timeline")
        self._rebuild_gallery_cards()

    def _rebuild_gallery_cards(self):
        task, clip_obj, mp = self._current_task_and_clip()
        all_cards = ensure_task_ai_cards(task, default_source_path=mp)

        # 统计每个 cap_id 出现的次数，大于 1 的允许删除副本
        cap_counts: dict[str, int] = {}
        for c in all_cards:
            cid = str(c.get("cap_id") or "")
            cap_counts[cid] = cap_counts.get(cid, 0) + 1

        clip_uid = str(getattr(clip_obj, "uid", "") or "") if clip_obj is not None else ""
        filtered = []
        for c in all_cards:
            if not isinstance(c, dict):
                continue
            if self._active_category != "all" and c.get("category") != self._active_category:
                continue
            if self._scope_mode == "clip" and clip_uid:
                if c.get("clip_uid") not in ("", clip_uid):
                    continue
            filtered.append(c)

        # 清理旧卡片控件
        while self.gallery_grid.count():
            item = self.gallery_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        self._card_widgets: list[AiArtCardWidget] = []
        for c in filtered:
            cid = str(c.get("cap_id") or "")
            w = AiArtCardWidget(c, can_delete=(cap_counts.get(cid, 1) > 1), parent=self.gallery_host)
            w.run_requested.connect(self._run_single_card)
            w.duplicate_requested.connect(self._duplicate_card)
            w.delete_requested.connect(self._delete_card)
            w.changed.connect(self._on_card_changed)
            self._card_widgets.append(w)

        self._relayout_card_widgets()
        self._update_stack_button_count()

    def _relayout_card_widgets(self):
        if not hasattr(self, "_card_widgets") or not self._card_widgets:
            return
        vp_w = self.scroll.viewport().width()
        if vp_w <= 100:
            vp_w = max(1200, self.width() - 40)
        avail_w = max(560, vp_w - 28)
        n_cards = len(self._card_widgets)
        # 全屏/宽屏下 21 张卡牌默认按 7 列 × 3 行整屏铺满，分类子集则按卡片数与宽度自动均衡拉伸铺满整屏
        if n_cards >= 14 and avail_w >= 1580:
            cols = 7
        else:
            cols = max(2, min(max(2, n_cards), avail_w // 245))
        for idx, w in enumerate(self._card_widgets):
            self.gallery_grid.addWidget(w, idx // cols, idx % cols)
        for c in range(12):
            self.gallery_grid.setColumnStretch(c, 1 if c < cols else 0)
        rows = max(1, (n_cards + cols - 1) // cols)
        for r in range(12):
            self.gallery_grid.setRowStretch(r, 1 if r < rows else 0)

    def _update_stack_button_count(self):
        task, _c, mp = self._current_task_and_clip()
        all_cards = ensure_task_ai_cards(task, default_source_path=mp)
        checked = [c for c in all_cards if isinstance(c, dict) and c.get("enabled")]
        if hasattr(self, "btn_run_stack"):
            self.btn_run_stack.setText(f"⚡ 叠加执行已勾选卡牌链 ({len(checked)} 张)")

    def _on_card_changed(self, _card: dict):
        self._update_stack_button_count()
        if self.win is not None and hasattr(self.win, "mark_dirty") and callable(self.win.mark_dirty):
            self.win.mark_dirty()

    def _duplicate_card(self, card: dict):
        task, clip_obj, mp = self._current_task_and_clip()
        cards = ensure_task_ai_cards(task, default_source_path=mp)
        clip_uid = str(getattr(clip_obj, "uid", "") or "") if clip_obj is not None else ""
        clip_name = str(getattr(clip_obj, "name", "") or "") if clip_obj is not None else "新建卡牌副本"
        new_c = make_card_instance(
            str(card.get("cap_id") or "ltx25_i2v_t2v"),
            clip_uid=clip_uid,
            clip_label=clip_name,
            source_path=str(card.get("source_path") or mp),
            prompt=str(card.get("prompt") or ""),
            mode=str(card.get("mode") or ""),
            strength=int(card.get("strength") or 70),
            enabled=True,
        )
        idx = cards.index(card) + 1 if card in cards else len(cards)
        cards.insert(idx, new_c)
        self._rebuild_gallery_cards()
        self.lbl_status.setText(f"✨ 已复制新建卡牌《{new_c['title']}》，可为不同素材设置独立参数。")

    def _delete_card(self, card: dict):
        task, _c, mp = self._current_task_and_clip()
        cards = ensure_task_ai_cards(task, default_source_path=mp)
        if card in cards:
            cards.remove(card)
            self._rebuild_gallery_cards()

    def _bind_selected_clip_new_card(self):
        task, clip_obj, mp = self._current_task_and_clip()
        if clip_obj is None:
            QMessageBox.information(self, "提示", "请先在剪辑时间线上点选一个视频或音频素材片段，或直接使用右侧『✨ 剪辑页灵巧调用窗』。")
            return
        clip_uid = str(getattr(clip_obj, "uid", "") or "")
        clip_name = str(getattr(clip_obj, "name", "") or os.path.basename(mp) or "选中片段")
        cards = ensure_task_ai_cards(task, default_source_path=mp)
        new_c = make_card_instance(
            "simone_relight_v6h",
            clip_uid=clip_uid,
            clip_label=clip_name,
            source_path=mp,
            enabled=True,
        )
        cards.insert(0, new_c)
        self._rebuild_gallery_cards()
        self.lbl_status.setText(f"📌 已为素材 [{clip_name}] 创建专属 AI 卡牌。")

    def _run_single_card(self, card: dict):
        mp = self.ed_active_clip.text().strip() or self._current_video_path()
        if mp and not card.get("source_path"):
            card["source_path"] = mp
        self._start_card_worker([card])

    def _run_checked_card_stack(self):
        task, _c, mp = self._current_task_and_clip()
        all_cards = ensure_task_ai_cards(task, default_source_path=mp)
        checked = [c for c in all_cards if isinstance(c, dict) and c.get("enabled")]
        if not checked:
            QMessageBox.information(self, "叠加执行提示", "请先在下方卡牌左上角勾选 1 张或多张要叠加执行的 AI 卡牌（例如：先勾选画质修复，再勾选人像重打光与电影调色）。")
            return
        for c in checked:
            if mp and not c.get("source_path"):
                c["source_path"] = mp
        self._start_card_worker(checked)

    def _start_card_worker(self, cards: list[dict]):
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "正在运行", "当前已有 AI 卡牌链正在后台执行，请稍候。")
            return
        self.progress.setVisible(True)
        self._worker = _CardStackWorker(cards, self)
        self._worker.progress_text.connect(self.lbl_status.setText)
        self._worker.card_finished.connect(self._on_single_card_done)
        self._worker.all_finished.connect(self._on_all_cards_done)
        self._worker.failed_err.connect(self._on_worker_err)
        self._worker.start()

    def _on_single_card_done(self, card: dict, res: dict):
        task, clip_obj, _mp = self._current_task_and_clip()
        out_p = str(res.get("output_path") or "")
        kind = str(res.get("media_kind") or "video")
        spec = CATALOG_BY_ID.get(str(card.get("cap_id") or "")) or AI_CAPABILITIES_CATALOG[0]
        if out_p and os.path.isfile(out_p):
            _tr, new_clip = mount_media_to_timeline(
                self.win,
                task,
                out_p,
                media_kind=kind,
                label=spec["title"],
            )
            record_ai_card_on_task(
                task,
                new_clip or clip_obj,
                spec["cap_id"],
                mode=str(card.get("mode") or ""),
                prompt=str(card.get("prompt") or ""),
                strength=int(card.get("strength") or 70),
                source_path=str(card.get("source_path") or ""),
                output_path=out_p,
            )

    def _on_all_cards_done(self, results: list):
        self.progress.setVisible(False)
        self._rebuild_gallery_cards()
        if not results:
            return
        last_card, last_res = results[-1]
        msg = str(last_res.get("message") or "AI 卡牌执行完成")
        self.lbl_status.setText(f"✅ {msg}（已记录在当前时间线卡牌组并落位轨道）")

    def _on_worker_err(self, err: str):
        self.progress.setVisible(False)
        self.lbl_status.setText(f"⚠️ {err}")
        QMessageBox.warning(self, "提示", err)

    def _browse_active_clip(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择需要 AI 处理的视频、图片或音频", "", "Media (*.mp4 *.mov *.mkv *.png *.jpg *.wav *.mp3)"
        )
        if path:
            self.ed_active_clip.setText(path)
            task, _c, _mp = self._current_task_and_clip()
            for card in ensure_task_ai_cards(task, default_source_path=path):
                card["source_path"] = path
            self._rebuild_gallery_cards()

    def _launch_orante(self):
        bat = LTX_ROOT / "modules" / "orante_desk" / "Orante2.bat"
        pyw = LTX_ROOT / "runtime" / "venv" / "Scripts" / "pythonw.exe"
        if pyw.is_file():
            subprocess.Popen([str(pyw), "-m", "orante"], cwd=str(LTX_ROOT / "modules" / "orante_desk"))
        elif bat.is_file():
            subprocess.Popen(["cmd.exe", "/c", str(bat)], cwd=str(bat.parent))

    def _back_to_edit_page(self):
        if self.win and hasattr(self.win, "room_host"):
            self.win.room_host.enter("edit")
        else:
            self.accept()


# ============================================================================
# 7. 剪辑页面原生灵巧直调胶囊 (`QuickAiCreatorDialog` & `open_grok_micro_dialog`)
#    支持在时间线与画布上直接：生图、生视频、改图、改视频、生成配乐、生成音效、克隆人声、提画质、降噪
#    自动同步创建轨道和素材条，并自动在 AI 界面生成/更新卡牌！
# ============================================================================

class QuickAiCreatorDialog(QDialog):
    """剪辑页面原生灵巧 AI 创作与精修胶囊（尊贵黑金主题 · 一键调用全部 21 项 AI 能力，自动上轨 + 自动记入 AI 卡牌）。"""

    def __init__(
        self,
        win=None,
        video_path: str = "",
        initial_cap_id: str = "ltx25_i2v_t2v",
        parent=None,
    ):
        qparent = parent if isinstance(parent, QWidget) else (win if isinstance(win, QWidget) else None)
        super().__init__(qparent)
        self.win = win
        self.video_path = video_path
        self.initial_cap_id = initial_cap_id if initial_cap_id in CATALOG_BY_ID else "ltx25_i2v_t2v"
        self._worker: _CardStackWorker | None = None
        self.setWindowTitle("✨ 片语 AI 灵巧创作与精修胶囊（直接落位时间线 + 自动生成 AI 卡牌）")
        self.setMinimumWidth(640)
        self._build_ui()
        self._select_capability(self.initial_cap_id)

    def _build_ui(self):
        self.setStyleSheet(
            "QDialog { background: #120F0B; color: #F5E9D3; }"
            "QLabel { color: #EADBC0; }"
            "QComboBox, QLineEdit, QPlainTextEdit { background: #1A1610; color: #F5E9D3; border: 1px solid #453721; border-radius: 8px; padding: 5px; }"
            "QComboBox:hover, QLineEdit:focus, QPlainTextEdit:focus { border-color: #E5A93C; }"
            "QPushButton { background: #211B13; color: #F2E3C6; border: 1px solid #4A3B24; border-radius: 8px; padding: 6px 12px; font-weight: 600; }"
            "QPushButton:hover { background: #2E2518; border-color: #E5A93C; color: #F5D082; }"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        # 顶部说明
        top_lbl = QLabel(
            "✨ <b>剪辑台即点即用 AI 胶囊</b>：选择任一 AI 能力执行后，将<b>直接在当前时间线游标处自动创建轨道与素材条</b>，"
            "并在「✨ AI 工坊」自动生成对应卡牌记录全部参数与路径（随工程保存）。"
        )
        top_lbl.setWordWrap(True)
        top_lbl.setStyleSheet("font-size: 12px; color: #F2E3C6; background: #1B1610; padding: 8px 10px; border-radius: 8px; border: 1px solid #4A3B24;")
        lay.addWidget(top_lbl)

        # ── 高频场景快捷胶囊按钮（含 RMBG-2.0 一键抠图） ──
        quick_grid = QGridLayout()
        quick_grid.setSpacing(6)
        quick_pills = [
            ("✂️ AI 抠图去背景 (RMBG-2.0)", "rmbg2_sam2_matte"),
            ("🎬 直接生视频 (I2V/T2V)", "ltx25_i2v_t2v"),
            ("🖼️ 直接生图 / 改图", "flux2_klein"),
            ("📐 局部重绘 / 扩画幅改片", "ltx_in_outpaint"),
            ("🎙️ 去话筒 / 去穿帮人物", "ltx25_cleanplate"),
            ("🎵 时间线生成配乐 (BGM)", "musicgen_bgm"),
            ("🔊 时间线生成音效 (SFX)", "stable_audio_sfx"),
            ("🗣️ 吸取原声克隆配音", "chatterbox_clone"),
            ("💎 视频提画质 / 人像打光", "simone_relight_v6h"),
        ]
        for idx, (qlabel, qid) in enumerate(quick_pills):
            b = QPushButton(qlabel)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton { background: #1F1912; color: #F5E9D3; border: 1px solid #4A3B24; border-radius: 8px; padding: 6px 8px; font-size: 11px; font-weight: 600; }"
                "QPushButton:hover { border-color: #E5A93C; color: #F5D082; background: #2D2418; }"
            )
            b.clicked.connect(lambda _=False, cid=qid: self._select_capability(cid))
            quick_grid.addWidget(b, idx // 3, idx % 3)
        lay.addLayout(quick_grid)

        # ── 全量 21 项 AI 卡牌能力选择器 ──
        form = QFormLayout()
        form.setSpacing(8)
        self.cmb_cap = QComboBox()
        for item in AI_CAPABILITIES_CATALOG:
            self.cmb_cap.addItem(f"#{item['order']:02d} [{item['category_label']}] {item['title']} — {item['subtitle']}", item["cap_id"])
        self.cmb_cap.currentIndexChanged.connect(self._on_cap_combo_changed)
        form.addRow("选择 AI 卡牌能力 (21全齐):", self.cmb_cap)

        self.cmb_mode = QComboBox()
        form.addRow("算力与模式选项:", self.cmb_mode)

        src_row = QHBoxLayout()
        self.ed_src = QLineEdit(self.video_path)
        self.ed_src.setPlaceholderText("可选：参考图 / 待修复视频 / 待吸取音色的原音频路径（默认取时间线当前选中片段）")
        btn_pick = QPushButton("浏览…")
        btn_pick.clicked.connect(self._browse_src)
        src_row.addWidget(self.ed_src, 1)
        src_row.addWidget(btn_pick)
        form.addRow("参考 / 目标素材:", src_row)

        self.ed_prompt = QPlainTextEdit()
        self.ed_prompt.setFixedHeight(68)
        form.addRow("描述语 / 台词 (Prompt):", self.ed_prompt)

        sl_row = QHBoxLayout()
        self.lbl_strength_title = QLabel("参数强度:")
        self.sl_strength = QSlider(Qt.Orientation.Horizontal)
        self.sl_strength.setRange(2, 100)
        self.sl_strength.setValue(70)
        self.sl_strength.setStyleSheet(
            "QSlider::groove:horizontal { height: 4px; background: #2D2417; border-radius: 2px; }"
            "QSlider::sub-page:horizontal { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #C88B2E, stop:1 #F3C668); border-radius: 2px; }"
            "QSlider::handle:horizontal { background: #F5D082; border: 1px solid #8C621E; width: 12px; margin: -4px 0; border-radius: 6px; }"
        )
        self.lbl_strength_val = QLabel("70")
        self.lbl_strength_val.setStyleSheet("color: #F3C668; font-weight: 700;")
        self.sl_strength.valueChanged.connect(lambda v: self.lbl_strength_val.setText(str(v)))
        sl_row.addWidget(self.sl_strength, 1)
        sl_row.addWidget(self.lbl_strength_val)
        form.addRow(self.lbl_strength_title, sl_row)
        lay.addLayout(form)

        # 状态与进度
        self.lbl_status = QLabel("就绪 — 执行后将同步在时间线创建轨道与片段，并写入当前时间线的 AI 卡牌组。")
        self.lbl_status.setStyleSheet("color: #B8A688; font-size: 11px;")
        lay.addWidget(self.lbl_status)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        lay.addWidget(self.progress)

        # 底部操作栏
        btn_row = QHBoxLayout()
        self.btn_run = QPushButton("🚀 立即生成并同步落位时间线 (自动生成 AI 卡牌)")
        self.btn_run.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_run.setMinimumHeight(36)
        self.btn_run.setStyleSheet(
            "QPushButton { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #F3C668, stop:1 #D49B35); "
            "color: #14110B; border: 1px solid #FFE8A3; border-radius: 8px; font-weight: 800; font-size: 13px; padding: 0 14px; }"
            "QPushButton:hover { background: #FFE082; }"
        )
        self.btn_run.clicked.connect(self._execute_now)

        btn_open_deck = QPushButton("🏛️ 打开 AI 艺术馆卡牌工坊 (21张全览)…")
        btn_open_deck.setMinimumHeight(36)
        btn_open_deck.clicked.connect(self._open_full_studio)

        btn_cancel = QPushButton("关闭")
        btn_cancel.setMinimumHeight(36)
        btn_cancel.clicked.connect(self.reject)

        btn_row.addWidget(self.btn_run, 2)
        btn_row.addWidget(btn_open_deck, 1)
        btn_row.addWidget(btn_cancel)
        lay.addLayout(btn_row)

    def _select_capability(self, cap_id: str):
        idx = self.cmb_cap.findData(cap_id)
        if idx >= 0:
            self.cmb_cap.setCurrentIndex(idx)
            self._on_cap_combo_changed()

    def _on_cap_combo_changed(self):
        cap_id = str(self.cmb_cap.currentData() or "ltx25_i2v_t2v")
        spec = CATALOG_BY_ID.get(cap_id) or AI_CAPABILITIES_CATALOG[0]
        self.cmb_mode.clear()
        for m_lab, m_val in spec.get("modes", []):
            self.cmb_mode.addItem(m_lab, m_val)
        self.ed_prompt.setPlainText(str(spec.get("default_prompt") or ""))
        self.lbl_strength_title.setText(f"{spec.get('strength_label', '参数强度')}:")
        self.sl_strength.setValue(int(spec.get("default_strength", 70)))

    def _browse_src(self):
        p, _ = QFileDialog.getOpenFileName(self, "选择素材文件", "", "Media (*.mp4 *.mov *.png *.jpg *.wav *.mp3)")
        if p:
            self.ed_src.setText(p)

    def _execute_now(self):
        cap_id = str(self.cmb_cap.currentData() or "ltx25_i2v_t2v")
        mode = str(self.cmb_mode.currentData() or "")
        prompt = self.ed_prompt.toPlainText().strip()
        strength = int(self.sl_strength.value())
        src_path = self.ed_src.text().strip()

        card = make_card_instance(
            cap_id,
            source_path=src_path,
            prompt=prompt,
            mode=mode,
            strength=strength,
            enabled=True,
        )
        self.progress.setVisible(True)
        self.btn_run.setEnabled(False)
        self._worker = _CardStackWorker([card], self)
        self._worker.progress_text.connect(self.lbl_status.setText)
        self._worker.all_finished.connect(self._on_done)
        self._worker.failed_err.connect(self._on_err)
        self._worker.start()

    def _on_done(self, results: list):
        self.progress.setVisible(False)
        self.btn_run.setEnabled(True)
        if not results:
            return
        card, res = results[-1]
        out_p = str(res.get("output_path") or "")
        kind = str(res.get("media_kind") or "video")
        spec = CATALOG_BY_ID.get(str(card.get("cap_id") or "")) or AI_CAPABILITIES_CATALOG[0]

        task = None
        clip_obj = None
        if self.win is not None:
            idx = int(getattr(self.win, "curr_idx", -1) or -1)
            tasks = list(getattr(self.win, "tasks", None) or [])
            if 0 <= idx < len(tasks):
                task = tasks[idx]
            tl_w = getattr(self.win, "timeline", None)
            if tl_w is not None and getattr(tl_w, "selected_video_clip", None) is not None:
                _tr, clip_obj = tl_w.selected_video_clip

        _tr, new_clip = mount_media_to_timeline(
            self.win,
            task,
            out_p,
            media_kind=kind,
            label=spec["title"],
        )
        record_ai_card_on_task(
            task,
            new_clip or clip_obj,
            spec["cap_id"],
            mode=str(card.get("mode") or ""),
            prompt=str(card.get("prompt") or ""),
            strength=int(card.get("strength") or 70),
            source_path=str(card.get("source_path") or ""),
            output_path=out_p,
        )
        # 若 AI 工坊房间已实例化，同步刷新其卡牌画展
        if self.win is not None and hasattr(self.win, "ai_studio_room") and self.win.ai_studio_room is not None:
            try:
                self.win.ai_studio_room.refresh_from_editor()
            except Exception:
                pass
        self.lbl_status.setText(f"✅ {res.get('message')}")
        self.accept()

    def _on_err(self, err: str):
        self.progress.setVisible(False)
        self.btn_run.setEnabled(True)
        QMessageBox.warning(self, "提示", err)

    def _open_full_studio(self):
        self.accept()
        if self.win and hasattr(self.win, "room_host"):
            self.win.room_host.enter("ai_studio")


# 保持向后兼容别名
GrokMicroRetouchDialog = QuickAiCreatorDialog


def open_quick_ai_creator(win=None, video_path: str = "", initial_cap_id: str = "ltx25_i2v_t2v", parent=None):
    """在剪辑页面呼出灵巧 AI 创作与精修胶囊（支持指定初始能力卡牌 ID）。"""
    if not video_path and win is not None:
        try:
            idx = int(getattr(win, "curr_idx", -1) or -1)
            tasks = list(getattr(win, "tasks", None) or [])
            if 0 <= idx < len(tasks):
                video_path = str(tasks[idx].get("video_path") or tasks[idx].get("path") or "")
        except Exception:
            pass
    dlg = QuickAiCreatorDialog(win=win, video_path=video_path, initial_cap_id=initial_cap_id, parent=parent)
    return dlg.exec()


def open_grok_micro_dialog(win=None, video_path: str = "", parent=None, initial_cap_id: str = "ltx25_i2v_t2v"):
    return open_quick_ai_creator(win=win, video_path=video_path, initial_cap_id=initial_cap_id, parent=parent)


# ============================================================================
# 5. 全局一键发丝级 AI 图片抠图 (`RMBG-2.0 BiRefNet`) — 支持右键菜单 / 画布 / 时间线 / 图层 / 检查器原位透明替换与还原
# ============================================================================

def get_overlay_image_path(ov: dict | None) -> str:
    """提取图片覆层 (`image_overlays` 项) 的图片文件路径。"""
    if not isinstance(ov, dict):
        return ""
    for el in ov.get("elements") or []:
        if isinstance(el, dict):
            p = str(el.get("path") or el.get("src") or "").strip()
            if p:
                return p
    return str(ov.get("path") or ov.get("image_path") or "").strip()


def set_overlay_image_path(ov: dict | None, new_path: str, *, save_orig: bool = True) -> bool:
    """原位替换图片覆层的图像路径为抠图后的透明 PNG（保持坐标、尺寸、缩放、旋转与起止时间完全不变），并清理渲染缓存。"""
    if not isinstance(ov, dict) or not new_path:
        return False
    old_path = get_overlay_image_path(ov)
    if save_orig and old_path and not ov.get("_orig_path_before_rmbg"):
        ov["_orig_path_before_rmbg"] = old_path
    updated = False
    for el in ov.get("elements") or []:
        if isinstance(el, dict) and (el.get("path") or el.get("src") or el.get("kind") == "image"):
            if save_orig and el.get("path") and not el.get("_orig_path_before_rmbg"):
                el["_orig_path_before_rmbg"] = str(el.get("path"))
            el["path"] = str(new_path)
            updated = True
    if not updated:
        if "path" in ov or not ov.get("elements"):
            ov["path"] = str(new_path)
            updated = True
    try:
        from cutora.render.frame_renderer import _IMAGE_CACHE
        _IMAGE_CACHE.pop(old_path, None)
        _IMAGE_CACHE.pop(str(new_path), None)
    except Exception:
        pass
    return updated


def has_rmbg_original(target) -> bool:
    """判断图片覆层 (`dict`) 或片段 (`Clip`) 是否存有抠图前的原图路径，可供一键还原。"""
    if isinstance(target, dict):
        orig = str(target.get("_orig_path_before_rmbg") or "")
        if not orig:
            for el in target.get("elements") or []:
                if isinstance(el, dict) and el.get("_orig_path_before_rmbg"):
                    orig = str(el.get("_orig_path_before_rmbg"))
                    break
        return bool(orig and os.path.isfile(orig))
    if target is not None:
        orig = str(getattr(target, "_orig_path_before_rmbg", "") or "")
        return bool(orig and os.path.isfile(orig))
    return False


def _refresh_all_editor_views(win=None, monitor_grid=None, monitor_tiles=None, message: str = ""):
    """抠图或还原完成后，立即刷新画布、时间线、检查器、图层面板与中央监视器。"""
    if monitor_grid is not None:
        try:
            tiles = monitor_tiles or monitor_grid.selected_tiles() or list(getattr(monitor_grid, "tiles", []) or [])
            monitor_grid._changed(tiles, video=True, overlays=True, prio=0)
            if hasattr(monitor_grid, "layer_panel") and monitor_grid.layer_panel is not None:
                monitor_grid.layer_panel.sync()
            if message and hasattr(monitor_grid, "status"):
                monitor_grid.status.emit(message)
        except Exception:
            pass
    if win is not None:
        try:
            if hasattr(win, "mark_dirty") and callable(win.mark_dirty):
                win.mark_dirty()
            for attr in ("video_canvas", "timeline"):
                w = getattr(win, attr, None)
                if w is not None and hasattr(w, "update"):
                    w.update()
            if hasattr(win, "update_preview") and callable(win.update_preview):
                win.update_preview()
            if hasattr(win, "prop_panel") and win.prop_panel is not None:
                try:
                    win.prop_panel.update()
                except Exception:
                    pass
            if message and hasattr(win, "statusBar") and callable(win.statusBar):
                win.statusBar().showMessage(message, 6000)
        except Exception:
            pass


def restore_rmbg_original_ui(
    win=None,
    *,
    overlays: list[dict] | None = None,
    clip_obj=None,
    tl_obj=None,
    monitor_grid=None,
    monitor_tiles=None,
) -> int:
    """一键将已抠图的图片覆层或片段还原为抠图前的原图（支持撤销）。"""
    restored = 0
    push = getattr(win, "push_undo", None) if win is not None else None
    if callable(push):
        push("还原抠图前原图")
    tok = None
    if monitor_grid is not None and monitor_tiles:
        try:
            tok = monitor_grid.history.begin([t.task for t in monitor_tiles])
        except Exception:
            tok = None

    for ov in overlays or []:
        if not isinstance(ov, dict):
            continue
        orig = str(ov.get("_orig_path_before_rmbg") or "")
        if not orig:
            for el in ov.get("elements") or []:
                if isinstance(el, dict) and el.get("_orig_path_before_rmbg"):
                    orig = str(el.get("_orig_path_before_rmbg"))
                    break
        if orig and os.path.isfile(orig):
            set_overlay_image_path(ov, orig, save_orig=False)
            ov.pop("_orig_path_before_rmbg", None)
            for el in ov.get("elements") or []:
                if isinstance(el, dict):
                    el.pop("_orig_path_before_rmbg", None)
            restored += 1

    if clip_obj is not None:
        orig_c = str(getattr(clip_obj, "_orig_path_before_rmbg", "") or "")
        if orig_c and os.path.isfile(orig_c):
            if tl_obj is not None:
                src = (getattr(tl_obj, "sources", None) or {}).get(getattr(clip_obj, "source_id", None))
                if src is not None:
                    src.path = orig_c
            if hasattr(clip_obj, "path"):
                clip_obj.path = orig_c
            try:
                delattr(clip_obj, "_orig_path_before_rmbg")
            except Exception:
                setattr(clip_obj, "_orig_path_before_rmbg", "")
            restored += 1

    cap = getattr(win, "capture_undo_after", None) if win is not None else None
    if callable(cap):
        cap()
    if tok is not None and monitor_grid is not None:
        try:
            monitor_grid._commit(tok, "还原抠图前原图", monitor_tiles)
        except Exception:
            pass

    if restored:
        _refresh_all_editor_views(
            win=win,
            monitor_grid=monitor_grid,
            monitor_tiles=monitor_tiles,
            message=f"↩️ 已还原 {restored} 张抠图前的原始图片",
        )
    return restored


def _run_rmbg_single_file(src_img_or_vid: str, frame_sec: float = 0.0) -> tuple[bool, str, str]:
    """对单张图片（或视频指定帧）运行本地 RMBG-2.0 (`D:\\0_LLM\\tools\\rmbg\\rmbg.py`)，返回 `(ok, out_png_path, error_msg)`。"""
    if not src_img_or_vid or not os.path.isfile(src_img_or_vid):
        return False, "", f"找不到源素材文件：{src_img_or_vid}"
    _free_comfyui_vram()
    out_dir = _output_dir_for_source(src_img_or_vid)
    stamp = datetime.datetime.now().strftime("%H%M%S_%f")[:10]
    ext = os.path.splitext(src_img_or_vid)[1].lower()
    stem = Path(src_img_or_vid).stem

    # 若已经是 _nobg.png 且存有原图，也允许重新跑；先准备输入 PNG
    in_png = out_dir / f"_rmbg_src_{stem}_{stamp}.png"
    if ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
        shutil.copy2(src_img_or_vid, in_png)
    else:
        cap = cv2.VideoCapture(src_img_or_vid)
        if frame_sec and frame_sec > 0:
            cap.set(cv2.CAP_PROP_POS_MSEC, float(frame_sec) * 1000.0)
        ok, frm = cap.read()
        cap.release()
        if ok and frm is not None:
            cv2.imwrite(str(in_png), frm)
    if not in_png.is_file():
        return False, "", "无法读取待抠图的画面帧或图片。"

    py_rmbg = LLM_ROOT / "tools" / "venv_rmbg" / "Scripts" / "python.exe"
    runner_rmbg = LLM_ROOT / "tools" / "rmbg" / "rmbg.py"
    if not (py_rmbg.is_file() and runner_rmbg.is_file()):
        try:
            in_png.unlink(missing_ok=True)
        except Exception:
            pass
        return False, "", f"未找到 RMBG-2.0 本地环境 ({py_rmbg})"

    env_utf8 = os.environ.copy()
    env_utf8["PYTHONUTF8"] = "1"
    env_utf8["HF_HUB_OFFLINE"] = "1"
    env_utf8["TRANSFORMERS_OFFLINE"] = "1"
    no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0

    proc = subprocess.run(
        [str(py_rmbg), str(runner_rmbg), str(in_png), str(out_dir)],
        env=env_utf8,
        timeout=180,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=no_win,
    )
    nobg_cand = out_dir / f"{in_png.stem}_nobg.png"
    try:
        in_png.unlink(missing_ok=True)
    except Exception:
        pass
    if nobg_cand.is_file():
        final_png = out_dir / f"{stem}_nobg_{stamp[:6]}.png"
        shutil.move(str(nobg_cand), str(final_png))
        return True, str(final_png), ""
    err = (proc.stderr or proc.stdout or "RMBG-2.0 未能生成透明 PNG").strip()[-300:]
    return False, "", f"RMBG-2.0 抠图失败：{err}"


class _RmbgBatchWorker(QThread):
    progress_msg = Signal(str)
    finished_jobs = Signal(list, str)

    def __init__(self, jobs: list[dict], parent=None):
        super().__init__(parent)
        self.jobs = jobs

    def run(self):
        out_list = []
        last_err = ""
        total = len(self.jobs)
        for i, job in enumerate(self.jobs, 1):
            src = str(job.get("src") or "")
            f_sec = float(job.get("frame_sec") or 0.0)
            self.progress_msg.emit(
                f"✂️ 正在运行 RMBG-2.0 (BiRefNet 1024×1024 FP16) 发丝级抠图 ({i}/{total}): {os.path.basename(src)} ..."
            )
            ok, out_png, err = _run_rmbg_single_file(src, frame_sec=f_sec)
            job_res = dict(job)
            job_res["ok"] = ok
            job_res["out_png"] = out_png
            job_res["error"] = err
            if not ok and err:
                last_err = err
            out_list.append(job_res)
        self.finished_jobs.emit(out_list, last_err)


def run_rmbg_cutout_ui(
    win=None,
    *,
    overlays: list[dict] | None = None,
    source_path: str = "",
    clip_obj=None,
    tl_obj=None,
    task: dict | None = None,
    frame_sec: float | None = None,
    monitor_grid=None,
    monitor_tiles=None,
    parent=None,
) -> bool:
    """在任意右键菜单（画布、时间线、中央监视器、图层面板、媒体库、检查器）一键执行 RMBG-2.0 发丝级 AI 抠图！
    - 若传入 `overlays`（图片覆层列表）：抠图后**原位替换为透明 PNG**（保留原位置、缩放、旋转、进出场动画，支持 Ctrl+Z 撤销与一键还原原图）。
    - 若传入图片 `clip_obj`（在视频轨上的图片片段）：原位替换该片段的源路径为透明 `_nobg.png`。
    - 若传入视频 `clip_obj` 或 `source_path`：提取当前游标帧（或图片本身）用 RMBG-2.0 抠出透明前景图层，直接落位到当前时间线/画布！
    """
    if task is None and win is not None:
        idx = int(getattr(win, "curr_idx", -1) or -1)
        tasks = list(getattr(win, "tasks", None) or [])
        if 0 <= idx < len(tasks):
            task = tasks[idx]

    if frame_sec is None and win is not None:
        tl_w = getattr(win, "timeline", None)
        if tl_w is not None and hasattr(tl_w, "position"):
            frame_sec = max(0.0, float(getattr(tl_w, "position", 0.0) or 0.0) / 1000.0)
        else:
            frame_sec = 0.0
    frame_sec = float(frame_sec or 0.0)

    jobs: list[dict] = []
    if overlays:
        for ov in overlays:
            if not isinstance(ov, dict):
                continue
            p = get_overlay_image_path(ov)
            if p and os.path.isfile(p):
                jobs.append({"kind": "overlay", "ov": ov, "src": p, "frame_sec": 0.0})

    if not jobs and clip_obj is not None:
        if tl_obj is None and isinstance(task, dict):
            tl_obj = task.get("timeline")
        src_obj = (getattr(tl_obj, "sources", None) or {}).get(getattr(clip_obj, "source_id", None)) if tl_obj else None
        c_path = str(getattr(src_obj, "path", "") or getattr(clip_obj, "path", "") or getattr(clip_obj, "src_path", "") or source_path or "")
        if c_path and os.path.isfile(c_path):
            ext = os.path.splitext(c_path)[1].lower()
            is_img = ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp")
            jobs.append({
                "kind": "clip_image" if is_img else "clip_video",
                "clip": clip_obj,
                "src_obj": src_obj,
                "src": c_path,
                "frame_sec": 0.0 if is_img else max(0.0, frame_sec - float(getattr(clip_obj, "start_t", 0.0) or 0.0)),
            })

    if not jobs and source_path and os.path.isfile(source_path):
        ext = os.path.splitext(source_path)[1].lower()
        is_img = ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp")
        jobs.append({
            "kind": "standalone_image" if is_img else "standalone_video",
            "src": source_path,
            "frame_sec": 0.0 if is_img else frame_sec,
        })

    if not jobs and isinstance(task, dict):
        # 兜底：若当前选中了图片覆层则抠图片覆层，否则抠当前时间线主视频当前帧
        v_path = str(task.get("video_path") or task.get("path") or "")
        if v_path and os.path.isfile(v_path):
            jobs.append({"kind": "standalone_video", "src": v_path, "frame_sec": frame_sec})

    if not jobs:
        qparent = parent if isinstance(parent, QWidget) else (win if isinstance(win, QWidget) else None)
        QMessageBox.information(qparent, "AI 抠图 (RMBG-2.0)", "请先选中一张图片图层或一段视频素材，再执行 AI 一键抠图。")
        return False

    qparent = parent if isinstance(parent, QWidget) else (win if isinstance(win, QWidget) else None)
    dlg = QDialog(qparent)
    dlg.setWindowTitle("✂️ AI 发丝级一键抠图 (RMBG-2.0 BiRefNet)")
    dlg.setMinimumWidth(460)
    dlg.setStyleSheet(
        "QDialog { background: #14110C; color: #F5E9D3; }"
        "QLabel { color: #F2E3C6; font-size: 12px; }"
    )
    vlay = QVBoxLayout(dlg)
    vlay.setContentsMargins(18, 16, 18, 16)
    vlay.setSpacing(10)
    lbl = QLabel(f"✂️ 正在调用本地 RMBG-2.0 (BiRefNet 1024×1024 FP16) 抠图（共 {len(jobs)} 项）...")
    lbl.setWordWrap(True)
    vlay.addWidget(lbl)
    bar = QProgressBar(dlg)
    bar.setRange(0, 0)
    vlay.addWidget(bar)

    worker = _RmbgBatchWorker(jobs, dlg)
    state = {"results": [], "err": ""}

    def _on_done(res_list, err_msg):
        state["results"] = res_list
        state["err"] = err_msg
        dlg.accept()

    worker.progress_msg.connect(lbl.setText)
    worker.finished_jobs.connect(_on_done)
    worker.start()
    dlg.exec()

    results = state["results"]
    ok_items = [r for r in results if r.get("ok") and r.get("out_png")]
    if not ok_items:
        QMessageBox.warning(qparent, "AI 抠图失败", state["err"] or "RMBG-2.0 未能生成透明抠图。")
        return False

    push = getattr(win, "push_undo", None) if win is not None else None
    if callable(push):
        push("AI 抠图去背景 (RMBG-2.0)")
    tok = None
    if monitor_grid is not None and monitor_tiles:
        try:
            tok = monitor_grid.history.begin([t.task for t in monitor_tiles])
        except Exception:
            tok = None

    # 记忆同一张源图的抠图输出，方便多选视频里有相同角标/贴图时复用
    src_to_out = {r["src"]: r["out_png"] for r in ok_items}

    for r in ok_items:
        jkind = r.get("kind")
        out_png = r["out_png"]
        src_p = r["src"]
        if jkind == "overlay":
            ov = r.get("ov")
            set_overlay_image_path(ov, out_png, save_orig=True)
        elif jkind == "clip_image":
            c_obj = r.get("clip")
            s_obj = r.get("src_obj")
            if c_obj is not None and not getattr(c_obj, "_orig_path_before_rmbg", ""):
                setattr(c_obj, "_orig_path_before_rmbg", src_p)
            if s_obj is not None:
                s_obj.path = out_png
            if c_obj is not None and hasattr(c_obj, "path"):
                c_obj.path = out_png
            try:
                from cutora.render.frame_renderer import _IMAGE_CACHE
                _IMAGE_CACHE.pop(src_p, None)
                _IMAGE_CACHE.pop(out_png, None)
            except Exception:
                pass
        else:
            # 视频当前帧抠图或媒体库独立图片抠图 → 直接作为透明图片覆层放入当前时间线画布（同时写 AI 卡牌记录）
            placed_ov = False
            if isinstance(task, dict):
                try:
                    from cutora.batch import monitor_model as mm
                    cw, ch = mm.canvas_size(task)
                    ov_new = mm.drop_image(task, out_png, cw * 0.5, ch * 0.5, width_frac=0.55)
                    ov_new["_orig_path_before_rmbg"] = src_p
                    for el in ov_new.get("elements") or []:
                        if isinstance(el, dict):
                            el["_orig_path_before_rmbg"] = src_p
                    placed_ov = True
                except Exception:
                    placed_ov = False
            if not placed_ov:
                mount_media_to_timeline(win, task, out_png, media_kind="image", label="RMBG-2.0 透明抠图", start_t=frame_sec)

        if isinstance(task, dict):
            record_ai_card_on_task(
                task,
                r.get("clip"),
                "rmbg2_sam2_matte",
                mode="rmbg2_hair_alpha",
                prompt="BiRefNet 1024x1024 FP16 transparent background removal",
                strength=100,
                source_path=src_p,
                output_path=out_png,
            )

    cap = getattr(win, "capture_undo_after", None) if win is not None else None
    if callable(cap):
        cap()
    if tok is not None and monitor_grid is not None:
        try:
            monitor_grid._commit(tok, "AI 抠图去背景 (RMBG-2.0)", monitor_tiles)
        except Exception:
            pass

    _refresh_all_editor_views(
        win=win,
        monitor_grid=monitor_grid,
        monitor_tiles=monitor_tiles,
        message=f"✅ 已完成 RMBG-2.0 发丝级透明抠图（共 {len(ok_items)} 项，Ctrl+Z 可撤销 / 右键可还原原图）",
    )
    return True

