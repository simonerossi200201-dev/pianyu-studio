# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-C · 蓝绿幕抠像（色差键 + 溢色抑制 + Matte 整理）。纯 numpy / cv2，没有 AI。

参数（dict ``keyer``，缺的按 DEFAULTS）：
    enabled   开关
    color     屏幕色 "#RRGGBB"（吸管取）
    gain      Key 增益 0.5–4：越大抠得越狠
    balance   平衡 0–1：0.5 中性；往 0 偏「青」一侧更容易保留，往 1 偏「品」一侧更容易保留
    despill   溢色抑制强度 0–1（只有 despill_on 打开才生效）
    despill_on    Q165：消除溢色单独开关（默认关 —— 只抠像、不动画面颜色）
    despill_mode  "limit" 屏幕色那个通道压到另外两个通道的平均（标准做法，正常颜色基本不受影响）
                  | "axis" 老算法：沿屏幕色色度方向整体减（绿色衣服 / 植物也会变灰）
    despill_scope "edges" 只处理边缘和半透明处（默认）| "all" 整个画面
    preblur   抠像前先把画面糊一点再算 matte（手机绿幕压缩块多时用），px
    black / white   Matte 黑 / 白裁切 0–1
    gamma     Matte 伽马 0.2–5
    erode     收缩（+）/ 扩张（−）px
    blur      Matte 柔化 px
    despot    去斑点 px（先开后闭）
    view      "comp" 合成 | "matte" 黑白 | "source" 原图 | "spill" 只看溢色抑制

算法（对标 Keylight / Delta Keyer 的色差键思路）：在 YCbCr 色度平面里，把屏幕色的
色度当作一根轴 K；每个像素的色度 P 分成「沿 K 的分量 proj」和「垂直分量 perp」。
proj 大、perp 小 = 像屏幕色 = 透明：matte_raw = (proj − perp_w) / |K|，alpha = 1 − clip(gain · matte_raw)。
balance 给垂直分量两侧不同的权重。溢色抑制 = 把色度沿 K 的正分量按比例减掉。
"""
from __future__ import annotations

import numpy as np

DEFAULTS = {
    "enabled": False,
    "color": "#1EC81E",
    "gain": 1.2,
    "balance": 0.5,
    "despill": 1.0,
    "despill_on": False,
    "despill_mode": "limit",
    "despill_scope": "edges",
    "preblur": 0.0,
    "black": 0.0,
    "white": 1.0,
    "gamma": 1.0,
    "erode": 0.0,
    "blur": 0.0,
    "despot": 0.0,
    "view": "comp",
}
VIEWS = ("comp", "matte", "source", "spill")
DESPILL_MODES = (("limit", "标准（只压屏幕色通道）"), ("axis", "强力（沿屏幕色整体减）"))
DESPILL_SCOPES = (("edges", "只处理边缘 / 半透明处"), ("all", "整个画面"))
# 预设不再顺手打开「消除溢色」（Q165：要不要去溢色由用户自己勾）
PRESETS = {
    "green": {"color": "#1EC81E", "gain": 1.2, "balance": 0.5, "despill": 1.0, "preblur": 0.0, "despot": 0.0},
    "blue": {"color": "#1E3CDC", "gain": 1.2, "balance": 0.5, "despill": 1.0, "preblur": 0.0, "despot": 0.0},
    "phone_green": {"color": "#1EC81E", "gain": 1.4, "balance": 0.5, "despill": 1.0, "preblur": 1.0,
                    "despot": 2.0, "blur": 1.0},
}
PRESET_LABELS = (("green", "标准绿幕"), ("blue", "标准蓝幕"), ("phone_green", "手机绿幕（压缩严重）"))

# BT.601 色度（0–1 浮点）
_KR, _KG, _KB = 0.299, 0.587, 0.114


def spec(host) -> dict:
    """图层上的抠像设置（规范化）。"""
    raw = host.get("keyer") if isinstance(host, dict) else getattr(host, "keyer", None)
    return normalize(raw)


def normalize(raw) -> dict:
    out = dict(DEFAULTS)
    if isinstance(raw, dict):
        for k in DEFAULTS:
            if k in raw and raw[k] is not None:
                out[k] = raw[k]
    out["enabled"] = bool(out.get("enabled"))
    c = str(out.get("color") or DEFAULTS["color"]).strip()
    if not (len(c) == 7 and c[0] == "#"):
        c = DEFAULTS["color"]
    out["color"] = c.upper()
    for k, lo, hi in (("gain", 0.1, 8.0), ("balance", 0.0, 1.0), ("despill", 0.0, 1.0),
                      ("preblur", 0.0, 20.0), ("black", 0.0, 1.0), ("white", 0.0, 1.0),
                      ("gamma", 0.2, 5.0), ("erode", -50.0, 50.0), ("blur", 0.0, 50.0),
                      ("despot", 0.0, 20.0)):
        try:
            out[k] = max(lo, min(hi, float(out[k])))
        except (TypeError, ValueError):
            out[k] = float(DEFAULTS[k])
    if out["white"] <= out["black"]:
        out["white"] = min(1.0, out["black"] + 0.01)
    v = str(out.get("view") or "comp")
    out["view"] = v if v in VIEWS else "comp"
    if _is_legacy(raw):
        # Q165 之前存下来的抠像（完整一份老参数、没有 despill_on）：那时溢色抑制一直开着、沿屏幕色整体减、
        # 整个画面 —— 打开老工程画面不变；用户在面板里取消勾选就按新规矩来。
        out["despill_on"] = out["despill"] > 0.0
        out["despill_mode"] = "axis"
        out["despill_scope"] = "all"
    out["despill_on"] = bool(out.get("despill_on"))
    dm = str(out.get("despill_mode") or "limit")
    out["despill_mode"] = dm if dm in dict(DESPILL_MODES) else "limit"
    sc = str(out.get("despill_scope") or "edges")
    out["despill_scope"] = sc if sc in dict(DESPILL_SCOPES) else "edges"
    return out


_LEGACY_KEYS = ("gain", "balance", "despill", "preblur", "black", "white", "gamma", "erode", "blur", "despot", "view")


def _is_legacy(raw) -> bool:
    """老版本 fx.keyer_set 存的是整份规范化参数（含 view / despot / preblur…），但没有 despill_on。
    零散的几个键（脚本 / 测试 / 新建）不算老工程。"""
    return (isinstance(raw, dict) and "despill_on" not in raw
            and all(k in raw for k in _LEGACY_KEYS))


def is_active(host) -> bool:
    return spec(host)["enabled"]


def signature(k: dict) -> tuple:
    return tuple(k[name] for name in ("enabled", "color", "gain", "balance", "despill", "despill_on",
                                      "despill_mode", "despill_scope", "preblur",
                                      "black", "white", "gamma", "erode", "blur", "despot", "view"))


def _hex_rgb(c: str) -> tuple[float, float, float]:
    try:
        return int(c[1:3], 16) / 255.0, int(c[3:5], 16) / 255.0, int(c[5:7], 16) / 255.0
    except (ValueError, IndexError):
        return 0.118, 0.784, 0.118


_TO_YCC = np.array([[_KR, _KG, _KB],
                    [-_KR / (2.0 * (1.0 - _KB)), -_KG / (2.0 * (1.0 - _KB)), 0.5],
                    [0.5, -_KG / (2.0 * (1.0 - _KR)), -_KB / (2.0 * (1.0 - _KR))]], dtype=np.float32)
_TO_RGB = np.linalg.inv(_TO_YCC).astype(np.float32)


def _chroma(rgb: np.ndarray):
    """(…, 3) RGB 0–1 → (Y, Cb, Cr)（Cb/Cr 以 0 为中心）。小数组 / 测试用；大图走 cv2.transform。"""
    ycc = rgb.reshape(-1, 3) @ _TO_YCC.T
    ycc = ycc.reshape(rgb.shape)
    return ycc[..., 0], ycc[..., 1], ycc[..., 2]


class ChannelPatch:
    """Q180：「标准」溢色抑制只改屏幕色那一个通道 —— 只交回 (通道号, 新通道 float32)，不复制整幅三通道。
    直接调 matte_and_despill 的老调用方拿到的还是整幅图（.full()）。"""
    __slots__ = ("rgb", "dom", "ch")

    def __init__(self, rgb, dom, ch):
        self.rgb, self.dom, self.ch = rgb, int(dom), ch

    def full(self):
        out = self.rgb.copy()
        out[..., self.dom] = self.ch
        return out


def matte_and_despill(rgb: np.ndarray, k: dict, channel_patch: bool = False):
    """rgb: (h, w, 3) float32 RGB 0–1 → (alpha (h, w) float32, rgb_despilled (h, w, 3) float32)。

    全部走 cv2（多线程、无中间大数组）：1080×1920 约 40 ms。
    channel_patch=True：标准溢色抑制时第二个返回值是 ChannelPatch（apply 用，省一次整幅拷贝）。"""
    import cv2
    kr, kg, kb = _hex_rgb(k["color"])
    kycc = _TO_YCC @ np.array([kr, kg, kb], dtype=np.float32)
    kcb, kcr = float(kycc[1]), float(kycc[2])
    kn = max(1e-4, (kcb * kcb + kcr * kcr) ** 0.5)
    ux, uy = kcb / kn, kcr / kn           # 屏幕色方向
    vx, vy = -uy, ux                      # 垂直方向
    rgb = np.ascontiguousarray(rgb, dtype=np.float32)
    src = rgb
    pre = float(k.get("preblur") or 0.0)
    if pre >= 0.5:
        ks = int(pre) * 2 + 1
        src = cv2.GaussianBlur(rgb, (ks, ks), pre / 2.0)
    # 一次矩阵变换直接得到 (proj, perp)：行 = [0, ux, uy]·YCC、[0, vx, vy]·YCC
    m_u = (np.array([[ux, uy]], dtype=np.float32) @ _TO_YCC[1:, :]).astype(np.float32)
    m_v = (np.array([[vx, vy]], dtype=np.float32) @ _TO_YCC[1:, :]).astype(np.float32)
    proj = cv2.transform(src, m_u)          # 单通道、连续（cv2.split 反而慢得多）
    perp = cv2.transform(src, m_v)
    bal = float(k.get("balance", 0.5))
    w_pos, w_neg = 2.0 * bal, 2.0 * (1.0 - bal)
    perp_w = cv2.addWeighted(cv2.max(perp, 0.0), w_pos, cv2.min(perp, 0.0), -w_neg, 0.0)
    gain = float(k["gain"]) / kn
    # alpha = 1 − clip(gain·(proj − perp_w)) = clip(1 + gain·perp_w − gain·proj)
    alpha = cv2.min(cv2.max(cv2.addWeighted(perp_w, gain, proj, -gain, 1.0), 0.0), 1.0)
    # Matte 整理
    lo, hi = float(k["black"]), float(k["white"])
    if lo > 1e-6 or hi < 1.0 - 1e-6:
        alpha = np.clip((alpha - lo) / max(1e-4, hi - lo), 0.0, 1.0)
    g = float(k["gamma"])
    if abs(g - 1.0) > 1e-6:
        alpha = cv2.pow(np.clip(alpha, 0.0, 1.0).astype(np.float32), 1.0 / g)
    alpha = np.ascontiguousarray(alpha, dtype=np.float32)
    despot = float(k.get("despot") or 0.0)
    if despot >= 0.5:
        kk = int(round(despot)) * 2 + 1
        kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kk, kk))
        alpha = cv2.morphologyEx(alpha, cv2.MORPH_OPEN, kern)
        alpha = cv2.morphologyEx(alpha, cv2.MORPH_CLOSE, kern)
    erode = float(k.get("erode") or 0.0)
    if abs(erode) >= 0.5:
        kk = int(round(abs(erode))) * 2 + 1
        kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kk, kk))
        alpha = cv2.erode(alpha, kern) if erode > 0 else cv2.dilate(alpha, kern)
    blur = float(k.get("blur") or 0.0)
    if blur >= 0.5:
        sigma = blur / 2.0
        ks = int(sigma * 3) * 2 + 1
        alpha = cv2.GaussianBlur(alpha, (ks, ks), sigma)
    alpha = np.ascontiguousarray(alpha, dtype=np.float32)
    # 溢色抑制（Q165：单独开关，默认关 —— 只抠像时画面颜色一点不动）
    ds = float(k.get("despill") or 0.0) if k.get("despill_on") else 0.0
    out = rgb
    if ds > 1e-6:
        if k.get("despill_mode") == "axis":
            # 老算法：把沿屏幕色方向的正色度分量按比例减掉（绿衣服 / 植物也会被减灰）
            proj0 = proj if src is rgb else cv2.transform(rgb, m_u)
            proj0 = cv2.max(proj0, 0.0)
            d = (_TO_RGB @ np.array([0.0, ux, uy], dtype=np.float32)) * ds
            spill_rgb = proj0[..., None] * d.astype(np.float32)
        else:
            # 标准：屏幕色的主通道（绿幕 = G，蓝幕 = B）超出另外两个通道平均的那一截才算溢色
            key_rgb = (kr, kg, kb)
            dom = int(np.argmax(key_rgb))
            others = [c for c in range(3) if c != dom]
            limit = cv2.addWeighted(rgb[..., others[0]], 0.5, rgb[..., others[1]], 0.5, 0.0)
            excess = cv2.max(rgb[..., dom] - limit, 0.0) * ds
            # Q180：只有主通道要减 —— 另外两个通道减 0 再钳 [0, 1] 还是原值，不用整幅三通道地算（逐位相同）
            if k.get("despill_scope", "edges") != "all":
                inv = 1.0 - alpha
                sig = max(1.5, 0.004 * max(alpha.shape[:2]))
                ks = int(sig * 3) * 2 + 1
                near = cv2.GaussianBlur(inv, (ks, ks), sig)
                # inv、near 都 ≥ 0，下限钳 0 是空操作；cv2.max / min 逐位等于 np.maximum / clip
                wgt = cv2.min(cv2.max(inv, near * 3.0), 1.0)
                excess = excess * wgt
            ch = rgb[..., dom] - excess
            np.clip(ch, 0.0, 1.0, out=ch)
            patch = ChannelPatch(rgb, dom, ch)
            return alpha, (patch if channel_patch else patch.full())
        if k.get("despill_scope", "edges") != "all":
            # 只在边缘 / 半透明处：权重 = 附近有多透明（实心前景深处 = 0，不动颜色）
            inv = 1.0 - alpha
            sig = max(1.5, 0.004 * max(alpha.shape[:2]))
            ks = int(sig * 3) * 2 + 1
            near = cv2.GaussianBlur(inv, (ks, ks), sig)
            wgt = np.clip(np.maximum(inv, near * 3.0), 0.0, 1.0).astype(np.float32)
            spill_rgb = spill_rgb * wgt[..., None]
        out = rgb - spill_rgb
        np.clip(out, 0.0, 1.0, out=out)
    return alpha, out


def apply(rgb_u8: np.ndarray, k: dict):
    """rgb_u8: (h, w, 3) uint8 RGB → (rgb_u8_out, alpha_u8) 按 view。alpha None = 不改透明度。"""
    k = normalize(k)
    view = k["view"]
    if view == "source":
        return rgb_u8, None
    import cv2
    rgb = np.multiply(rgb_u8, np.float32(1.0 / 255.0), dtype=np.float32)
    alpha, rgb_out = matte_and_despill(rgb, k, channel_patch=True)
    a8 = cv2.convertScaleAbs(alpha, alpha=255.0)
    if view == "matte":
        gray = np.repeat(a8[..., None], 3, axis=-1)
        return gray, None
    if isinstance(rgb_out, ChannelPatch):
        # 没改的两个通道逐位等于原图（x/255×255 四舍五入回 x）：直接拷 u8，只换一个通道
        out8 = rgb_u8.copy()
        out8[..., rgb_out.dom] = cv2.convertScaleAbs(rgb_out.ch, alpha=255.0)
    else:
        out8 = rgb_u8 if rgb_out is rgb else cv2.convertScaleAbs(rgb_out, alpha=255.0)
    if view == "spill":
        return out8, None
    return out8, a8


def sample_color(rgb_u8: np.ndarray, x: int, y: int, radius: int = 3) -> str:
    """吸管：以 (x, y) 为中心 (2r+1)² 范围的平均色 → "#RRGGBB"。"""
    h, w = rgb_u8.shape[:2]
    x0, x1 = max(0, int(x) - radius), min(w, int(x) + radius + 1)
    y0, y1 = max(0, int(y) - radius), min(h, int(y) + radius + 1)
    if x1 <= x0 or y1 <= y0:
        return DEFAULTS["color"]
    m = rgb_u8[y0:y1, x0:x1].reshape(-1, 3).astype(np.float32).mean(axis=0)
    return "#%02X%02X%02X" % tuple(int(round(v)) for v in m)
