# SPDX-License-Identifier: GPL-3.0-or-later
"""真立体声脉冲响应(IR)生成器 —— 给 spatial_audio.py 的卷积混响用。

为什么要有这个文件
------------------
老的"空间立体声"是 `aecho` + `extrastereo`:
  · aecho 是【离散回声】不是混响, 十几个抽头 = 十几个梳状滤波器, 听感金属味;
  · aecho 对所有声道用同一组抽头, 尾响左右完全相关(IACC≈1), 大脑把它听成
    "贴在脸上的一个点", 所以再怎么加混响也不会"大", 只会更糊;
  · extrastereo 放大的是 M/S 里的 S=L-R, 单声道素材 S 恒等于 0 —— 宽度旋钮
    在单声道口播上【一个字节都不改】。讲道/口播素材基本都是单声道。

专业软件(Altiverb / Fairlight / Nuendo 那一挂)做"话筒在舞台上"的真实空间感,
靠的是三件事, 这个文件把三件都做了:

1. 早期反射用【镜像声源法】从真实房间几何算出来 —— 地板、舞台后墙、侧墙的
   反射时间和强度是算的, 不是拍脑袋填的。这是"房间有多大、话筒离人有多远"
   的唯一听觉线索。
2. 左右【两个话筒振膜位置分别算一遍】。真正的舞台立体声就是一对间隔话筒收的,
   左右拿到的反射时间/强度天然不同 —— 空间感就是从这个差异来的。
3. 尾响左右【去相关】。低 IACC(左右耳信号不相关)才是"包围感/大空间"的物理量。
   低频段两声道共用同一份噪声(保持单声道兼容, 手机外放不塌), 300Hz 以上各用
   各的随机序列, 并且分四个频段给不同的 RT60 —— 真实空间高频衰减远快于低频。

输出是 32-bit float 双声道 WAV, 只含【反射】不含直达声(直达声是干声本身),
所以调用方用 afir 卷积出湿声, 再和干声按比例混。
"""
from __future__ import annotations

import math
import os

C_SOUND = 343.0          # m/s
SR = 48000

# 每个预设 = 一个真实房间 + 一对话筒的位置。
#   room     : 房间 (宽 W, 长 L, 高 H), 米
#   src      : 声源(讲者的嘴)坐标
#   mic_d    : 话筒在声源正前方多少米
#   mic_w    : 左右两个振膜的间距(米)。间隔越大低频去相关越强, 也越容易发飘。
#   mic_z    : 话筒高度
#   rt60     : 中频混响时间(秒)
#   hf       : 高频段 RT60 相对中频的比例(空气+表面吸收, 真实空间都远小于 1)
#   lf       : 低频段比例(真实空间低频拖得更长 → 温暖)
#   er_order : 镜像声源阶数(2 = 最多两次反射, 够用且不会太贵)
#   tail     : 尾响长度(秒)
#   er_ms    : 早期反射窗口(毫秒), 超过这个时间交给扩散尾响
ROOMS = {
    # 舞台话筒: 人离话筒很近(直达声干净), 但房间很大 —— 这就是"现场感"的配方。
    "stage":     dict(room=(24.0, 34.0, 11.0), src=(12.0, 7.0, 1.62), mic_d=1.20,
                      mic_w=0.30, mic_z=1.55, rt60=1.45, hf=0.40, lf=1.20,
                      er_order=2, tail=2.0, er_ms=95.0),
    # 口播/讲道: 小房间, 只要一点"不干"的空气感, 绝不能糊字。
    "speech":    dict(room=(9.0, 12.0, 4.0),   src=(4.5, 3.2, 1.60), mic_d=0.90,
                      mic_w=0.22, mic_z=1.55, rt60=0.55, hf=0.38, lf=1.10,
                      er_order=2, tail=0.85, er_ms=60.0),
    "room":      dict(room=(7.5, 10.0, 3.4),   src=(3.7, 2.8, 1.55), mic_d=1.60,
                      mic_w=0.26, mic_z=1.50, rt60=0.70, hf=0.42, lf=1.10,
                      er_order=2, tail=1.05, er_ms=70.0),
    "hall":      dict(room=(26.0, 38.0, 13.0), src=(13.0, 8.0, 1.60), mic_d=4.00,
                      mic_w=0.34, mic_z=1.60, rt60=1.95, hf=0.42, lf=1.22,
                      er_order=2, tail=2.6, er_ms=110.0),
    "cathedral": dict(room=(30.0, 56.0, 19.0), src=(15.0, 11.0, 1.60), mic_d=6.00,
                      mic_w=0.40, mic_z=1.60, rt60=3.00, hf=0.45, lf=1.30,
                      er_order=2, tail=3.4, er_ms=140.0),
}


def _sabine_reflection(room, rt60):
    """由 RT60 反推墙面反射系数。Sabine: RT60 = 0.161 V / (a S)。"""
    w, l, h = room
    v = w * l * h
    s = 2.0 * (w * l + w * h + l * h)
    a = 0.161 * v / (max(0.1, rt60) * s)
    a = min(0.92, max(0.02, a))
    return math.sqrt(max(1e-6, 1.0 - a))


def _image_taps(room, src, mic, refl, order, t_direct, max_t):
    """镜像声源法。返回 [(相对直达声的到达时间 s, 幅度), ...]，不含直达声。"""
    w, l, h = room
    sx, sy, sz = src
    mx, my, mz = mic
    taps = []
    rng = range(-order, order + 1)
    for ix in rng:
        for px in (0, 1):
            x = (1 - 2 * px) * sx + 2 * ix * w
            nx = abs(2 * ix - px)
            if nx > order:
                continue
            for iy in rng:
                for py in (0, 1):
                    y = (1 - 2 * py) * sy + 2 * iy * l
                    ny = abs(2 * iy - py)
                    if nx + ny > order:
                        continue
                    for iz in rng:
                        for pz in (0, 1):
                            z = (1 - 2 * pz) * sz + 2 * iz * h
                            nz = abs(2 * iz - pz)
                            n = nx + ny + nz
                            if n == 0 or n > order:
                                continue
                            d = math.sqrt((x - mx) ** 2 + (y - my) ** 2 + (z - mz) ** 2)
                            t = d / C_SOUND - t_direct
                            if t <= 0 or t > max_t:
                                continue
                            # 1/d 距离衰减 × 反射损失; 反射次数越多越弱
                            taps.append((t, (refl ** n) / max(0.5, d)))
    return taps


def _render_taps(taps, n, sr):
    """把抽头写进缓冲区，用线性插值做小数延迟(整数延迟会有量化噪声)。"""
    import numpy as np
    buf = np.zeros(n, dtype=np.float64)
    for t, g in taps:
        pos = t * sr
        i = int(pos)
        if i < 0 or i + 1 >= n:
            continue
        f = pos - i
        buf[i] += g * (1.0 - f)
        buf[i + 1] += g * f
    return buf


def _band(noise, sr, lo, hi):
    from scipy import signal
    import numpy as np
    ny = sr * 0.5
    if lo is None:
        sos = signal.butter(4, min(0.99, hi / ny), btype="low", output="sos")
    elif hi is None:
        sos = signal.butter(4, min(0.99, lo / ny), btype="high", output="sos")
    else:
        sos = signal.butter(4, [min(0.98, lo / ny), min(0.99, hi / ny)],
                            btype="band", output="sos")
    return signal.sosfilt(sos, noise).astype(np.float64)


def _tail(sr, rt60, hf, lf, dur, seed, shared_low):
    """扩散尾响: 分频段噪声 × 各自的指数衰减包络。

    低频段用 shared_low(两声道共用) —— 真实空间低频波长长, 左右耳本来就毘度相关,
    而且这样单声道叠加不会抵消, 手机外放不塌。300Hz 以上各声道独立随机 → 去相关。
    """
    import numpy as np
    n = int(dur * sr)
    t = np.arange(n) / sr
    rng = np.random.default_rng(seed)
    own = rng.standard_normal(n)
    bands = (
        (None, 300.0, rt60 * lf, shared_low[:n]),
        (300.0, 1500.0, rt60 * 1.05, own),
        (1500.0, 5000.0, rt60 * 0.80, own),
        (5000.0, None, rt60 * hf, own),
    )
    out = np.zeros(n, dtype=np.float64)
    for lo, hi, rt, src in bands:
        env = 10.0 ** (-3.0 * t / max(0.08, rt))
        out += _band(src, sr, lo, hi) * env
    # 回声密度爬升: 真实空间的反射密度是逐渐涨起来的, 一上来就满密度听着假
    out *= 1.0 - np.exp(-t / 0.028)
    return out


def render_ir(preset, sr=SR):
    """生成一条双声道 IR。返回 shape=(n, 2) 的 float32 数组。"""
    import numpy as np
    spec = ROOMS.get(preset) or ROOMS["stage"]
    room, src = spec["room"], spec["src"]
    refl = _sabine_reflection(room, spec["rt60"])
    er_win = spec["er_ms"] / 1000.0
    tail_dur = spec["tail"]
    n = int((er_win + tail_dur + 0.05) * sr)

    sx, sy, sz = src
    half = spec["mic_w"] * 0.5
    mic_l = (sx - half, sy + spec["mic_d"], spec["mic_z"])
    mic_r = (sx + half, sy + spec["mic_d"], spec["mic_z"])
    t_direct = math.dist(src, ((mic_l[0] + mic_r[0]) * 0.5, mic_l[1], mic_l[2]))/ C_SOUND

    # ── 早期反射: 左右振膜【各算一遍】, 时间和强度天然不同 ──
    er = np.zeros((n, 2), dtype=np.float64)
    for ch, mic in enumerate((mic_l, mic_r)):
        taps = _image_taps(room, src, mic, refl, spec["er_order"], t_direct, er_win)
        er[:, ch] = _render_taps(taps, n, sr)
    # 反射面不是理想镜面, 高频吸收更多 → 早期反射整体压一点高频
    from scipy import signal
    sos = signal.butter(2, min(0.99, 8500.0 / (sr * 0.5)), btype="low", output="sos")
    er = signal.sosfilt(sos, er, axis=0)

    # ── 扩散尾响: 左右独立随机序列(去相关), 低频共用(单声道兼容) ──
    tn = int(tail_dur * sr)
    shared_low = np.random.default_rng(20260830).standard_normal(tn)
    seed0 = abs(hash(preset)) % 100000
    tail = np.zeros((n, 2), dtype=np.float64)
    start = int(er_win * 0.55 * sr)          # 和早期反射有一段交叠, 别出现空档
    for ch in range(2):
        seg = _tail(sr, spec["rt60"], spec["hf"], spec["lf"], tail_dur,
                    seed0 + ch * 7919, shared_low)
        end = min(n, start + len(seg))
        tail[start:end, ch] = seg[:end - start]

    # 尾响电平: 让它和早期反射能量相当, 太强会糊字, 太弱就没有"大空间"
    er_e = float(np.sqrt((er ** 2).sum())) or 1.0
    tl_e = float(np.sqrt((tail ** 2).sum())) or 1.0
    ir = er / er_e + (tail / tl_e) * 1.25

    # 掐掉隆隆声: 混响支路一定要高通, 否则低频堆积 = 浑浊。这是专业混音的铁律。
    sos_hp = signal.butter(2, min(0.99, 140.0 / (sr * 0.5)), btype="high", output="sos")
    ir = signal.sosfilt(sos_hp, ir, axis=0)
    # 收尾淡出, 避免 IR 末端被硬切出咔哒声
    fade = int(0.05 * sr)
    if fade > 1 and n > fade:
        ir[-fade:] *= np.linspace(1.0, 0.0, fade)[:, None]

    # 能量归一化: 卷积后的湿声 RMS 大致等于干声 RMS, 这样"湿度"旋钮才线性可控
    e = float(np.sqrt((ir ** 2).sum()))
    if e > 1e-9:
        ir = ir / e
    return ir.astype(np.float32)


def ir_path(preset, base_dir):
    return os.path.join(base_dir, f"ir_{preset}_{SR}.wav")


def ensure_ir(preset, base_dir, sr=SR):
    """IR 文件不在就生成一份。返回路径; 生成不了返回 ""(调用方退回线性链路)。"""
    p = ir_path(preset, base_dir)
    try:
        if os.path.isfile(p) and os.path.getsize(p) > 4096:
            return p
    except OSError:
        pass
    try:
        import soundfile as sf
        os.makedirs(base_dir, exist_ok=True)
        sf.write(p, render_ir(preset, sr), sr, subtype="FLOAT")
        return p
    except Exception:
        return ""


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    for k in ROOMS:
        print(k, "->", ensure_ir(k, out))
