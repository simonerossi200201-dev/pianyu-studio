# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q181 · 像素运算的 torch 版（GPU 合成第一步）。

把每帧最重的几类逐像素运算搬到显卡上：蓝绿幕抠像 + 遮罩 + 预乘，调整图层的效果链（镜头模糊 / 高斯模糊 /
马赛克 / 调色）和区域混合。算法和 numpy / cv2 版（fx/keyer.py、render/adjust_layer.py、render/fill_bg.py、
render/grade.py）一一对应，结果差在浮点舍入这一级（测试里和 CPU 版逐像素比，误差 ≤ 几级）。

全部函数都只收 numpy 数组、返回 numpy 数组，``device`` 决定在哪算 —— CPU 上也能跑（测试用），
真正的提速来自 cuda。这里**不**做任何 IPC / 缓存 / 回退：那是 fx/gpu.py 的事。

golden 基线里没有抠像 / 调整图层 / 遮罩 / 混合模式，这些算法允许和 CPU 版有舍入级差别。
"""
from __future__ import annotations

import math

import numpy as np

_torch = None
_F = None


def torch_mod():
    """惰性 import torch（渲染子进程默认一行 torch 都不碰；只有真用 GPU 的进程才会走到这里）。"""
    global _torch, _F
    if _torch is None:
        import torch
        import torch.nn.functional as F
        _torch = torch
        _F = F
    return _torch


# ───────────────────────────── 小工具 ─────────────────────────────

def _gauss_taps(sigma: float, ksize: int | None = None):
    """和 cv2.getGaussianKernel 一样的一维高斯权重（float32，和为 1）。"""
    import cv2
    if ksize is None:
        ksize = int(sigma * 3) * 2 + 1
    ksize = max(1, int(ksize) | 1)
    return cv2.getGaussianKernel(ksize, float(sigma), ktype=cv2.CV_32F).reshape(-1)


def _pad_reflect101(x, pad_h: int, pad_w: int):
    """cv2.BORDER_REFLECT_101（gfedcb|abcdefgh|gfedcba）= torch 的 'reflect'。pad 超过边长时分几次。"""
    torch_mod()
    F = _F
    while pad_h > 0 or pad_w > 0:
        ph = min(pad_h, x.shape[-2] - 1)
        pw = min(pad_w, x.shape[-1] - 1)
        x = F.pad(x, (pw, pw, ph, ph), mode="reflect")
        pad_h -= ph
        pad_w -= pw
    return x


def gaussian_blur(x, sigma: float, ksize: int | None = None):
    """x: (C, H, W) 或 (N, C, H, W) float。可分离卷积，边界 reflect_101（cv2 默认）。"""
    torch = torch_mod()
    F = _F
    if sigma <= 0:
        return x
    taps = const_tensor(_gauss_taps(sigma, ksize), x.device, x.dtype)
    k = taps.numel()
    if k <= 1:
        return x
    squeeze = x.dim() == 3
    if squeeze:
        x = x.unsqueeze(0)
    n, c, h, w = x.shape
    half = k // 2
    xp = _pad_reflect101(x, half, half)
    kx = taps.view(1, 1, 1, k).expand(c, 1, 1, k)
    ky = taps.view(1, 1, k, 1).expand(c, 1, k, 1)
    y = F.conv2d(xp, kx, groups=c)
    y = F.conv2d(y, ky, groups=c)
    return y.squeeze(0) if squeeze else y


def conv2d_reflect(x, kernel2d):
    """x: (C, H, W)，kernel2d: (kh, kw) numpy，边界 reflect_101（cv2.filter2D BORDER_REFLECT 相当）。"""
    torch = torch_mod()
    F = _F
    k = const_tensor(np.asarray(kernel2d, dtype=np.float32), x.device, x.dtype)
    kh, kw = k.shape
    c = x.shape[0]
    # cv2.filter2D 是相关（不翻转核）；F.conv2d 也是相关
    xp = _pad_reflect101(x.unsqueeze(0), kh // 2, kw // 2)
    y = F.conv2d(xp, k.view(1, 1, kh, kw).expand(c, 1, kh, kw), groups=c)
    return y.squeeze(0)


def _ellipse_pool(x, radius_px: int, mode: str):
    """cv2.morphologyEx(MORPH_ELLIPSE) 的近似：方核 max/min 池化（GPU 上没有椭圆核；差别在角上一两个像素）。"""
    F = _F
    k = int(radius_px) * 2 + 1
    if k <= 1:
        return x
    xp = F.pad(x.unsqueeze(0), (k // 2,) * 4, mode="replicate")
    if mode == "max":
        y = F.max_pool2d(xp, k, stride=1)
    else:
        y = -F.max_pool2d(-xp, k, stride=1)
    return y.squeeze(0)


def _u8(x):
    """float [0,1] → uint8（四舍五入，饱和），和 cv2.convertScaleAbs(x, alpha=255) 一致。"""
    torch = torch_mod()
    return torch.clamp(x * 255.0 + 0.5, 0.0, 255.0).to(torch.uint8)


def _to_dev(a, device, dtype=None):
    """numpy → 目标设备上的张量。已经是张量的直接用（服务进程把共享内存登记成页锁定后
    自己上传，这里不再碰）。"""
    torch = torch_mod()
    if isinstance(a, torch.Tensor):
        t = a
        if device is not None and t.device.type != torch.device(device).type:
            t = t.to(device, non_blocking=True)
        return t if dtype is None else t.to(dtype)
    t = torch.from_numpy(np.ascontiguousarray(a))
    if device is not None and str(device) != "cpu":
        t = t.pin_memory().to(device, non_blocking=True) if t.numel() > 65536 else t.to(device)
    return t if dtype is None else t.to(dtype)


_CONST = {}
_CONST_LOCK = __import__("threading").Lock()


def const_tensor(arr, device, dtype=None):
    """小常量（卷积核、矩阵、颜色、查找表）→ 设备张量，按内容缓存。
    每次调用都重新 as_tensor 等于每次一次同步的小拷贝（几十 µs 且握着 GIL），
    而且 CUDA graph 录制期间不允许从普通内存拷贝 —— 常量必须事先在显卡上。"""
    torch = torch_mod()
    a = np.ascontiguousarray(arr)
    key = (str(device), str(dtype), a.dtype.str, a.shape, a.tobytes())
    t = _CONST.get(key)
    if t is None:
        t = torch.as_tensor(a, device=device)
        if dtype is not None:
            t = t.to(dtype)
        with _CONST_LOCK:
            if len(_CONST) > 512:
                _CONST.clear()
            _CONST[key] = t
    return t


# ───────────────────────────── 抠像 + 遮罩 + 预乘 ─────────────────────────────

_KR, _KG, _KB = 0.299, 0.587, 0.114
_TO_YCC = np.array([[_KR, _KG, _KB],
                    [-_KR / (2.0 * (1.0 - _KB)), -_KG / (2.0 * (1.0 - _KB)), 0.5],
                    [0.5, -_KG / (2.0 * (1.0 - _KR)), -_KB / (2.0 * (1.0 - _KR))]], dtype=np.float32)
_TO_RGB = np.linalg.inv(_TO_YCC).astype(np.float32)


def _hex_rgb(c: str):
    try:
        return int(c[1:3], 16) / 255.0, int(c[3:5], 16) / 255.0, int(c[5:7], 16) / 255.0
    except (ValueError, IndexError):
        return 0.118, 0.784, 0.118


def key_layer(frame_u8, keyer: dict | None, mask_f32, *,
              device="cpu", bgr: bool = True, as_tensor: bool = False):
    """一步做完 layer_fx.apply_arrays + _to_qimage：(H, W, 3) BGR/RGB uint8 或 (H, W, 4) BGRA →
    (H, W, 4) BGRA **预乘** uint8。keyer=None 或没启用 → 只套遮罩 + 预乘。mask_f32: (H, W) 0–1 或 None。
    view 不是 "comp" 的（查看 matte / 溢色）由调用方走 CPU。
    输入可以是 numpy 或已在设备上的张量；as_tensor=True 时返回设备上的张量（服务进程自己写回共享内存）。"""
    torch = torch_mod()
    h, w = int(frame_u8.shape[0]), int(frame_u8.shape[1])
    ch = int(frame_u8.shape[2]) if len(frame_u8.shape) == 3 else 1
    src = _to_dev(frame_u8, device)                                  # (H, W, C) uint8
    f = src.to(torch.float32) * (1.0 / 255.0)
    if ch == 4:
        rgb = f[..., :3].flip(-1) if bgr else f[..., :3]
        alpha = f[..., 3]
    else:
        rgb = f.flip(-1) if bgr else f
        alpha = None
    k = keyer if isinstance(keyer, dict) and keyer.get("enabled") else None
    out_rgb = rgb
    if k is not None:
        a_key, out_rgb = _matte_and_despill(rgb, k)
        alpha = a_key if alpha is None else alpha * a_key
    if mask_f32 is not None:
        m = _to_dev(mask_f32 if isinstance(mask_f32, torch.Tensor) else np.ascontiguousarray(mask_f32, dtype=np.float32),
                    device, torch.float32)
        alpha = m if alpha is None else alpha * m
    rgb8 = _u8(out_rgb)                                              # 和 CPU 一样先量化到 8 位
    if alpha is None:
        a8 = torch.full((h, w), 255, dtype=torch.uint8, device=rgb8.device)
        pre = rgb8
    else:
        a8 = _u8(alpha)
        # cv2.multiply(rgb, a, scale=1/255) = saturate(round(rgb·a/255))
        pre = ((rgb8.to(torch.int32) * a8.to(torch.int32).unsqueeze(-1) + 127) // 255).to(torch.uint8)
    out = torch.empty((h, w, 4), dtype=torch.uint8, device=rgb8.device)
    out[..., 0] = pre[..., 2]
    out[..., 1] = pre[..., 1]
    out[..., 2] = pre[..., 0]
    out[..., 3] = a8
    return out if as_tensor else out.cpu().numpy()


def _matte_and_despill(rgb, k: dict):
    """fx/keyer.matte_and_despill 的 torch 版。rgb: (H, W, 3) float [0,1]。返回 (alpha (H,W), rgb_out)。"""
    torch = torch_mod()
    kr, kg, kb = _hex_rgb(k["color"])
    kycc = _TO_YCC @ np.array([kr, kg, kb], dtype=np.float32)
    kcb, kcr = float(kycc[1]), float(kycc[2])
    kn = max(1e-4, (kcb * kcb + kcr * kcr) ** 0.5)
    ux, uy = kcb / kn, kcr / kn
    vx, vy = -uy, ux
    dev = rgb.device
    src = rgb
    pre = float(k.get("preblur") or 0.0)
    if pre >= 0.5:
        ks = int(pre) * 2 + 1
        src = gaussian_blur(rgb.permute(2, 0, 1), pre / 2.0, ks).permute(1, 2, 0)
    m_u = (np.array([[ux, uy]], dtype=np.float32) @ _TO_YCC[1:, :]).astype(np.float32).reshape(3)
    m_v = (np.array([[vx, vy]], dtype=np.float32) @ _TO_YCC[1:, :]).astype(np.float32).reshape(3)
    proj = src @ const_tensor(m_u, dev)
    perp = src @ const_tensor(m_v, dev)
    bal = float(k.get("balance", 0.5))
    w_pos, w_neg = 2.0 * bal, 2.0 * (1.0 - bal)
    perp_w = torch.clamp(perp, min=0.0) * w_pos - torch.clamp(perp, max=0.0) * w_neg
    gain = float(k["gain"]) / kn
    alpha = torch.clamp(1.0 + gain * perp_w - gain * proj, 0.0, 1.0)
    lo, hi = float(k["black"]), float(k["white"])
    if lo > 1e-6 or hi < 1.0 - 1e-6:
        alpha = torch.clamp((alpha - lo) / max(1e-4, hi - lo), 0.0, 1.0)
    g = float(k["gamma"])
    if abs(g - 1.0) > 1e-6:
        alpha = torch.clamp(alpha, 0.0, 1.0) ** (1.0 / g)
    despot = float(k.get("despot") or 0.0)
    if despot >= 0.5:
        r = int(round(despot))
        a1 = alpha.unsqueeze(0)
        a1 = _ellipse_pool(_ellipse_pool(a1, r, "min"), r, "max")      # open
        a1 = _ellipse_pool(_ellipse_pool(a1, r, "max"), r, "min")      # close
        alpha = a1.squeeze(0)
    erode = float(k.get("erode") or 0.0)
    if abs(erode) >= 0.5:
        r = int(round(abs(erode)))
        alpha = _ellipse_pool(alpha.unsqueeze(0), r, "min" if erode > 0 else "max").squeeze(0)
    blur = float(k.get("blur") or 0.0)
    if blur >= 0.5:
        sigma = blur / 2.0
        alpha = gaussian_blur(alpha.unsqueeze(0), sigma, int(sigma * 3) * 2 + 1).squeeze(0)
    ds = float(k.get("despill") or 0.0) if k.get("despill_on") else 0.0
    out = rgb
    if ds > 1e-6:
        if k.get("despill_mode") == "axis":
            proj0 = proj if src is rgb else rgb @ const_tensor(m_u, dev)
            proj0 = torch.clamp(proj0, min=0.0)
            d = (_TO_RGB @ np.array([0.0, ux, uy], dtype=np.float32)) * ds
            spill = proj0.unsqueeze(-1) * const_tensor(d.astype(np.float32), dev)
            spill_rgb = spill
        else:
            dom = int(np.argmax((kr, kg, kb)))
            others = [c for c in range(3) if c != dom]
            limit = 0.5 * rgb[..., others[0]] + 0.5 * rgb[..., others[1]]
            excess = torch.clamp(rgb[..., dom] - limit, min=0.0) * ds
            spill_rgb = torch.zeros_like(rgb)
            spill_rgb[..., dom] = excess
        if k.get("despill_scope", "edges") != "all":
            inv = 1.0 - alpha
            sig = max(1.5, 0.004 * max(alpha.shape[:2]))
            near = gaussian_blur(inv.unsqueeze(0), sig, int(sig * 3) * 2 + 1).squeeze(0)
            wgt = torch.clamp(torch.maximum(inv, near * 3.0), 0.0, 1.0)
            spill_rgb = spill_rgb * wgt.unsqueeze(-1)
        out = torch.clamp(rgb - spill_rgb, 0.0, 1.0)
    return alpha, out


# ───────────────────────────── 调整图层 ─────────────────────────────

def _lens_blur_t(bgra, amount: float, highlights: float, long_side: float):
    """render/fill_bg.lens_blur 的 torch 版。bgra: (4, H, W) float [0,1]（预乘）。返回同尺寸 (4, H, W)。"""
    torch = torch_mod()
    F = _F
    from cutora.render.fill_bg import LENS_CURVE, LENS_MAX_FRAC, _LENS_WORK_MAX, _LENS_WORK_R, hex_kernel
    a = max(0.0, min(100.0, float(amount or 0.0))) / 100.0
    radius = float(long_side) * LENS_MAX_FRAC * (a ** LENS_CURVE)
    _c, h, w = bgra.shape
    if radius < 0.35:
        return bgra
    s = min(1.0, _LENS_WORK_R / radius, _LENS_WORK_MAX / float(max(w, h)))
    sw = max(2, int(round(w * s)))
    sh = max(2, int(round(h * s)))
    full = bgra.unsqueeze(0)
    small = F.interpolate(full, size=(sh, sw), mode="area") if (sw, sh) != (w, h) else full
    rw = radius * (sw / float(w))
    kern = hex_kernel(rw)
    col = small[0, :3]
    lin = col * col                                                   # γ2 线性光（和 _lens_luts 的 v² 一致）
    hl = float(highlights or 0.0)
    if hl > 0.0:
        if (sw, sh) != (w, h):
            kk = max(2, int(math.ceil(w / float(sw))))
            pl, pr = kk // 2, kk - 1 - kk // 2                        # cv2.dilate 的锚点在核中心
            hi = F.max_pool2d(F.pad(full, (pl, pr, pl, pr), mode="replicate"), kk, stride=1)  # dilate
            hi = F.interpolate(hi, size=(sh, sw), mode="nearest")[0, :3]
        else:
            hi = col
        gray = 0.299 * hi[2] + 0.587 * hi[1] + 0.114 * hi[0]          # BGR → 灰（cv2.COLOR_BGR2GRAY 系数）
        t = torch.clamp((gray - 0.7) / 0.3, 0.0, 1.0)
        m = t * t * (3.0 - 2.0 * t)
        extra = hl * 40.0 * m * m                                     # wt − 1
        hot = hi * hi * extra.unsqueeze(0) + lin
        num = conv2d_reflect(hot, kern)
        den = conv2d_reflect((extra + 1.0).unsqueeze(0), kern)
        out = num / den
    else:
        out = conv2d_reflect(lin, kern)
    res = torch.empty((4, sh, sw), dtype=bgra.dtype, device=bgra.device)
    res[:3] = torch.clamp(torch.sqrt(torch.clamp(out, min=0.0)), 0.0, 1.0)
    alpha_s = small[0, 3]
    # CPU 版在 alpha 全 255 时跳过这一步；这里不看 alpha 的值（看一眼就是一次显卡→主机同步，
    # 既打断流水又让 CUDA graph 录不了）。核和为 1，alpha 全 1 时卷出来还是 1（误差 1e-7，8 位后一样）。
    a2 = conv2d_reflect(alpha_s.unsqueeze(0), kern)[0]
    res[3] = a2
    res[:3] = torch.minimum(res[:3], a2.unsqueeze(0))
    if (sw, sh) != (w, h):
        res = F.interpolate(res.unsqueeze(0), size=(h, w), mode="bilinear", align_corners=False)[0]
    return res


def _blur_t(bgra, sigma: float):
    if sigma < 0.3:
        return bgra
    return gaussian_blur(bgra, sigma)


def _mosaic_t(bgra, cell: int):
    F = _F
    _c, h, w = bgra.shape
    cell = max(2, int(cell))
    sw, sh = max(1, w // cell), max(1, h // cell)
    tiny = F.interpolate(bgra.unsqueeze(0), size=(sh, sw), mode="area")
    return F.interpolate(tiny, size=(h, w), mode="nearest")[0]


_GRADE_T = {}                    # device → (lin_t, srgb_t)，一起发布（多连接线程同时首次进来也安全）


def _grade_tables(device):
    torch = torch_mod()
    key = str(device)
    t = _GRADE_T.get(key)
    if t is None:
        with _CONST_LOCK:
            t = _GRADE_T.get(key)
            if t is None:
                from cutora.render.grade import _affine_tables
                lin_lut, srgb_u16 = _affine_tables()
                t = (torch.as_tensor(lin_lut[:, 0, 0].copy(), device=device),                 # (256,) float32
                     torch.as_tensor(srgb_u16.astype(np.float32) / 255.0, device=device))      # (65536,)
                _GRADE_T[key] = t
    return t


def _grade_t(bgra, grade: dict):
    """调色：仿射的直接算（和 grade.apply_affine_u8 同一套表和矩阵）；其余三线性采样 33³ 表（= Pillow 路径）。"""
    torch = torch_mod()
    F = _F
    from cutora.render.grade import _LUT_CACHE, affine_of_stack, is_identity, normalize_grade
    g = normalize_grade(grade)
    if is_identity(g):
        return bgra
    dev = bgra.device
    rgb8 = torch.clamp(bgra[:3] * 255.0 + 0.5, 0.0, 255.0).to(torch.int64)        # (3, H, W) BGR
    aff = affine_of_stack([g])
    if aff is not None:
        M, b = aff
        P = np.array([[0, 0, 1], [0, 1, 0], [1, 0, 0]], dtype=np.float64)
        Mb = const_tensor((P @ M @ P).astype(np.float32), dev)
        bb = const_tensor((P @ b).astype(np.float32), dev)
        lin_t, srgb_t = _grade_tables(dev)
        lin = lin_t[rgb8]                                                           # (3, H, W)
        out = torch.einsum("ij,jhw->ihw", Mb, lin) + bb.view(3, 1, 1)
        idx = torch.clamp(out, 0.0, 1.0) * 65535.0 + 0.5
        enc = srgb_t[idx.to(torch.int64)]
    else:
        lut = _LUT_CACHE.get_or_build([g])                                          # BakedLut，(n,n,n,3) RGB 序
        vol = const_tensor(lut.table, dev)          # [r, g, b, 3]
        vol = vol.permute(3, 0, 1, 2).unsqueeze(0)                                  # (1, 3, R, G, B)
        rgb = bgra[:3].flip(0)                                                      # (3, H, W) RGB
        # grid_sample 的坐标顺序是 (x=最后一维=B, y=G, z=R)，align_corners=True 时 −1..1 对应 0..n−1
        grid = torch.stack([rgb[2], rgb[1], rgb[0]], dim=-1) * 2.0 - 1.0            # (H, W, 3)
        grid = grid.view(1, 1, *grid.shape)                                         # (1, 1, H, W, 3)
        samp = F.grid_sample(vol, grid, mode="bilinear", padding_mode="border", align_corners=True)
        enc = torch.clamp(samp[0, :, 0], 0.0, 1.0).flip(0)                          # → BGR
    out = bgra.clone()
    out[:3] = enc
    return out


def adjust_region(crop_bgra_u8, eff: dict, long_side: float, *, ox: int, oy: int,
                  shape_u8, mix: float, fill=None, border=None, device="cpu", as_tensor: bool = False):
    """render/adjust_layer.apply_item 里「效果 + 填充 + 边框」那一段的 torch 版。

    crop_bgra_u8：画布上外接框（含模糊余量）那一块，(H, W, 4) BGRA 预乘 uint8；
    (ox, oy)：形状在 crop 里的偏移；shape_u8：(sh, sw) 形状遮罩；mix = 效果混合 × 整体不透明度；
    fill = (bgra 颜色 4 元, alpha) 或 None；border = (border_u8 (sh, sw), bgra 颜色, alpha) 或 None。
    返回形状那一块 (sh, sw, 4) 混合好的 BGRA uint8（调用方贴回画布）。"""
    torch = torch_mod()
    from cutora.render.adjust_layer import blur_sigma_px, mosaic_cell_px
    src8 = _to_dev(crop_bgra_u8, device)
    x = src8.permute(2, 0, 1).to(torch.float32) * (1.0 / 255.0)          # (4, H, W)
    sh, sw = int(shape_u8.shape[0]), int(shape_u8.shape[1])
    cur = x
    kinds = list((eff or {}).get("kinds") or [])
    for k in kinds:
        amt = float(eff.get("amount_" + k, eff.get("amount", 0.0)) or 0.0)
        if k == "lens_blur":
            cur = _lens_blur_t(cur, amt, float(eff.get("highlights", 0.35) or 0.0), long_side)
        elif k == "blur":
            cur = _blur_t(cur, blur_sigma_px(amt, long_side))
        elif k == "mosaic":
            cur = _mosaic_t(cur, int(round(mosaic_cell_px(amt, long_side))))
        elif k == "grade":
            cur = _grade_t(cur, dict(eff.get("grade") or {}))
    region = x[:, oy:oy + sh, ox:ox + sw]
    fx = cur[:, oy:oy + sh, ox:ox + sw]
    m = _to_dev(shape_u8, device).to(torch.float32) * (float(mix) / 255.0)
    # 和 CPU 一样：效果先量化到 8 位再按遮罩混（cv2.blendLinear 在 uint8 上做）
    fx8 = _u8(fx).to(torch.float32)
    reg8 = _u8(region).to(torch.float32)
    out = torch.floor(reg8 * (1.0 - m) + fx8 * m + 0.5)               # 每一步都回到 8 位（和 CPU 逐步混合一样）
    if fill is not None:
        col, alpha = fill
        c = const_tensor(np.asarray(col, np.float32), out.device).view(4, 1, 1)
        mf = _to_dev(shape_u8, device).to(torch.float32) * (float(alpha) / 255.0)
        out = torch.floor(out * (1.0 - mf) + c * mf + 0.5)
    if border is not None:
        bmask, col, alpha = border
        c = const_tensor(np.asarray(col, np.float32), out.device).view(4, 1, 1)
        mb = _to_dev(bmask, device).to(torch.float32) * (float(alpha) / 255.0)
        out = torch.floor(out * (1.0 - mb) + c * mb + 0.5)
    out8 = torch.clamp(out, 0.0, 255.0).to(torch.uint8).permute(1, 2, 0).contiguous()
    return out8 if as_tensor else out8.cpu().numpy()
