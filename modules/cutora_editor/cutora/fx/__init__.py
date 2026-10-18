# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152 · 图层合成效果：遮罩（masks）、蓝绿幕抠像（keyer）。

每个视频片段 / 图片层 / 文本层可以带：
    masks : [ {...}, ... ]   形状遮罩，见 cutora.fx.masks
    keyer : {...}            蓝绿幕抠像，见 cutora.fx.keyer（文本层没有）

预览和导出都只调 cutora.fx.layer_fx.apply_layer_fx —— 一份实现。没有这两个键
（老工程）就原样返回，一个字节不变。
"""
