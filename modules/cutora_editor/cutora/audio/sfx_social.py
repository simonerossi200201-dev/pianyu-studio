# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""短视频与场景音效包（200 条）与爆款柔和配乐床（20 首）：
结合本地 Stable Audio Open 1.0 条件生成与高精度物理/谐波声学合成及多频段谱维纳降噪，
实现零底噪、高保真、自带平滑缓入缓出的专业级音频资产库。

* 许可证：Stability AI Community License（年营收低于 100 万美元免费商用，详见 resources/audio/sfx_social/LICENSE.txt）。
* 规格：48 kHz / 立体声 / 16 bit WAV，峰值 ≤ -1.2 dBFS，无直流，首尾平滑 S 曲线缓入缓出。
* 60 条转场音效（"转场音效"）：时长 2.0 ~ 3.0 秒，自带平滑缓入（>= 0.30s）与缓出（>= 0.50s）。
* 140 条常用场景与环境拟音音效（"自然天气"、"江河流水"、"鸟鸣生灵"、"掌声欢笑"、"城市火焰"、"拟音场景"）：
  时长 >= 8.2 秒（8.5 ~ 9.6 秒），自带平滑缓入（0.40s）与缓出（0.60s）。
* 20 首柔和悦耳爆款配乐床（BGM_CATALOG）：时长 12.0 ~ 15.5 秒无缝循环，综合响度 -16.0 LUFS。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import wave

import numpy as np
from scipy.ndimage import minimum_filter1d, uniform_filter1d
from scipy.signal import butter, lfilter, sosfilt

from cutora.core.log import get_logger
from cutora.core.paths import RESOURCES

_log = get_logger("cutora.audio.sfx_social")

SR = 48000
PARAMS_VERSION = "3-sao-1.0-studio-200sfx-20bgm"
LICENSE = "Stability AI Community License (Stable Audio Open 1.0, commercial use permitted < $1M USD revenue)"
GROUP_LABEL = "短视频与场景音效"
SOCIAL_DIR = os.path.join(RESOURCES, "audio", "sfx_social")
BGM_DIR = os.path.join(RESOURCES, "audio", "bgm_beds")
PROVENANCE_NAME = "PROVENANCE.json"
LICENSE_FILE_NAME = "LICENSE.txt"

TARGET_MMAX_LUFS = -14.0      # 最响 400 ms 窗口的目标响度（音效）
TARGET_BGM_LUFS = -16.0       # 综合响度目标（配乐）
PEAK_CEIL_DB = -1.2           # 采样峰值上限（留 0.2 dB 给 16 bit 取整）
MAX_LIMIT_DB = 4.0            # 限幅器最多压 4 dB

TRANSITION_SFX_GAIN_DB = -10.0
TRANSITION_SFX_FADE_IN = 0.35
TRANSITION_SFX_FADE_OUT = 0.55
TRANSITION_SFX_SETTING = "transition_sfx_enabled"
PRE_TRANSITION_SFX_SETTING = "transition_pre_sfx_id"
TRANSITION_SFX_NAME_PREFIX = "转场音效·"

_PRE_TRANSITION_SFX_ID = "auto"


# ═══════════════════════════════════════════════════════════════════════════
# 响度与音频读写工具（ITU-R BS.1770-4，48 kHz K 计权系数）
# ═══════════════════════════════════════════════════════════════════════════

_K1_B = [1.53512485958697, -2.69169618940638, 1.19839281085285]
_K1_A = [1.0, -1.69065929318241, 0.73248077421585]
_K2_B = [1.0, -2.0, 1.0]
_K2_A = [1.0, -1.99004745483398, 0.99007225036621]


def _n(sec: float) -> int:
    return int(round(float(sec) * SR))


def _sos(kind: str, freq, order: int = 2):
    nyq = SR * 0.5
    if isinstance(freq, (tuple, list)):
        lo = min(max(5.0, float(freq[0])), nyq * 0.95)
        hi = min(max(lo * 1.01, float(freq[1])), nyq * 0.95)
        return butter(order, [lo / nyq, hi / nyq], btype=kind, output="sos")
    f = min(max(5.0, float(freq)), nyq * 0.95)
    return butter(order, f / nyq, btype=kind, output="sos")


def lowpass(x, f, order=2):
    return sosfilt(_sos("lowpass", f, order), x, axis=0)


def highpass(x, f, order=2):
    return sosfilt(_sos("highpass", f, order), x, axis=0)


def bandpass(x, lo, hi, order=2):
    return sosfilt(_sos("bandpass", (lo, hi), order), x, axis=0)


def raised_fade(n, fade_in, fade_out):
    """平滑 S 曲线（Raised-Cosine）淡入淡出包络。"""
    env = np.ones(n, dtype=np.float64)
    ai = min(n // 2, max(1, _n(fade_in)))
    ao = min(n // 2, max(1, _n(fade_out)))
    env[:ai] *= 0.5 - 0.5 * np.cos(np.linspace(0.0, math.pi, ai))
    env[-ao:] *= 0.5 + 0.5 * np.cos(np.linspace(0.0, math.pi, ao))
    return env


def spectral_flatness(x) -> float:
    """计算信号的几何均值/算术均值功率谱平坦度（越小代表越纯净悦耳、越少白噪音嘶声）。"""
    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim == 2:
        arr = np.mean(arr, axis=1)
    if len(arr) < 256:
        return 0.0
    spec = np.abs(np.fft.rfft(arr * np.hanning(len(arr)))) ** 2 + 1e-18
    # 聚焦 200 Hz - 16 kHz 人耳敏感频段
    freqs = np.fft.rfftfreq(len(arr), 1.0 / SR)
    mask = (freqs >= 200.0) & (freqs <= 16000.0)
    if np.any(mask):
        spec = spec[mask]
    gmean = float(np.exp(np.mean(np.log(spec))))
    amean = float(np.mean(spec))
    return gmean / max(amean, 1e-18)


def _k_power(x):
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    y = lfilter(_K2_B, _K2_A, lfilter(_K1_B, _K1_A, x, axis=0), axis=0)
    return np.sum(y ** 2, axis=1)


def _block_loudness(x, win=0.4, hop=0.1):
    p = _k_power(x)
    nw, nh = _n(win), _n(hop)
    if len(p) < nw:
        p = np.concatenate([p, np.zeros(nw - len(p))])
    c = np.concatenate([[0.0], np.cumsum(p)])
    starts = np.arange(0, len(p) - nw + 1, nh)
    ms = (c[starts + nw] - c[starts]) / nw
    return ms


def _lufs(ms):
    return -0.691 + 10.0 * np.log10(np.maximum(ms, 1e-20))


def integrated_lufs(x) -> float:
    """BS.1770-4 门限积分响度（-70 绝对门限 + -10 相对门限）。短于 400 ms 时补零。"""
    ms = _block_loudness(x)
    lk = _lufs(ms)
    g1 = ms[lk > -70.0]
    if len(g1) == 0:
        return -120.0
    rel = _lufs(np.mean(g1)) - 10.0
    g2 = ms[(lk > -70.0) & (lk > rel)]
    if len(g2) == 0:
        return -120.0
    return float(_lufs(np.mean(g2)))


def momentary_max_lufs(x) -> float:
    ms = _block_loudness(x, 0.4, 0.01)
    return float(_lufs(np.max(ms))) if len(ms) else -120.0


def peak_dbfs(x) -> float:
    m = float(np.max(np.abs(x))) if np.size(x) else 0.0
    return 20.0 * math.log10(m) if m > 1e-12 else -120.0


def limiter(x, ceil_lin, lookahead=0.0015, release=0.06):
    """前瞻峰值限幅：保持 + 释放 + 平滑。保证输出采样峰值 ≤ ceil_lin。"""
    st = x if x.ndim == 2 else x[:, None]
    pk = np.max(np.abs(st), axis=1)
    g = np.minimum(1.0, ceil_lin / np.maximum(pk, 1e-12))
    if float(np.min(g)) >= 1.0:
        return x
    la = max(1, _n(lookahead))
    h = minimum_filter1d(g, size=2 * la + 1, mode="nearest")
    a = math.exp(-1.0 / max(1.0, release * SR))
    hl = h.tolist()
    e = 1.0
    out = [0.0] * len(hl)
    for i, v in enumerate(hl):
        if v < e:
            e = v
        else:
            e = v + (e - v) * a
        out[i] = e
    env = uniform_filter1d(np.asarray(out), size=la + 1, mode="nearest")
    env = np.minimum(env, h)
    y = st * env[:, None]
    y = np.clip(y, -ceil_lin, ceil_lin)
    return y if x.ndim == 2 else y[:, 0]


def to_pcm16(st) -> bytes:
    pcm = np.clip(np.round(np.asarray(st) * 32767.0), -32767, 32767).astype("<i2")
    return pcm.tobytes()


def write_wav16(path, st):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(to_pcm16(st))


def read_wav16(path):
    with wave.open(path, "rb") as w:
        nch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    data = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32767.0
    return data.reshape(-1, nch), sr, nch, sw


# ═══════════════════════════════════════════════════════════════════════════
# 200 条专业级音效目录（60 条转场音效 + 140 条常用场景与环境拟音音效）
# ═══════════════════════════════════════════════════════════════════════════

G_TRANS = "转场音效"
G_WEATHER = "自然天气"
G_WATER = "江河流水"
G_BIRDS = "鸟鸣生灵"
G_CROWD = "掌声欢笑"
G_CITY_FIRE = "城市火焰"
G_FOLEY = "拟音场景"

GROUPS = [G_TRANS, G_WEATHER, G_WATER, G_BIRDS, G_CROWD, G_CITY_FIRE, G_FOLEY]

_SPECS = [
    # ─── 1. 转场音效 (60 条，2.0s ~ 3.0s，自带平滑缓入 >=0.30s 与缓出 >=0.50s) ───
    # 兼容全部 28 个既有 transition_catalog 引用 ID + 32 个高颜值爆款转场音效
    ("whoosh_light",              "丝滑轻风呼啸",        G_TRANS, 2.20, 1.05, True),
    ("whoosh_heavy",              "深沉电影重呼啸",      G_TRANS, 2.50, 1.20, True),
    ("whoosh_rise",               "上扬加速呼啸",        G_TRANS, 2.40, 1.35, True),
    ("whoosh_drop",               "下潜俯冲呼啸",        G_TRANS, 2.30, 0.95, True),
    ("swish_fast",                "利落柔顺甩镜风",      G_TRANS, 2.10, 1.00, True),
    ("air_whoosh",                "空灵气流掠过",        G_TRANS, 2.30, 1.15, True),
    ("zoom_in_swell",             "镜头推近涌动",        G_TRANS, 2.40, 1.30, True),
    ("zoom_out_swell",            "镜头拉远舒展",        G_TRANS, 2.40, 1.05, True),
    ("riser_short",               "空灵弦乐上扬",        G_TRANS, 2.60, 1.70, True),
    ("riser_long",                "宏大交响渐强转场",    G_TRANS, 2.90, 1.95, True),
    ("downlifter",                "温暖低频下潜",        G_TRANS, 2.60, 0.95, True),
    ("reverse_cymbal",            "柔和反向金镲",        G_TRANS, 2.50, 1.65, True),
    ("impact_low",                "电影质感低频定格",    G_TRANS, 2.50, 0.95, True),
    ("impact_punch",              "弹性律动重拍",        G_TRANS, 2.20, 0.90, True),
    ("impact_metal",              "清澈水晶钟磬转场",    G_TRANS, 2.60, 0.95, True),
    ("bass_drop",                 "深海温暖Sub下潜",     G_TRANS, 2.60, 0.95, True),
    ("boom_cinematic",            "史诗温暖低频共鸣",    G_TRANS, 2.80, 1.05, True),
    ("shutter_click",             "清脆单反快门流转",    G_TRANS, 2.10, 0.95, True),
    ("cam_focus",                 "镜头自动对焦微鸣",    G_TRANS, 2.20, 1.05, False),
    ("sparkle_shimmer",           "星光水晶风铃流转",    G_TRANS, 2.60, 1.20, False),
    ("ding_bright",               "清透泛音叮声转场",    G_TRANS, 2.40, 0.90, True),
    ("pop_bubble",                "清润水滴气泡转场",    G_TRANS, 2.10, 0.95, True),
    ("click_soft",                "温润木质轻叩转场",    G_TRANS, 2.05, 0.90, True),
    ("page_flip",                 "柔顺书页翻动流转",    G_TRANS, 2.20, 1.05, True),
    ("snap_finger",               "空间混响清脆响指",    G_TRANS, 2.10, 0.95, True),
    ("heartbeat",                 "深沉双跳心跳转场",    G_TRANS, 2.50, 1.10, True),
    ("tick_tock",                 "精致怀表节奏流转",    G_TRANS, 2.40, 1.15, False),
    ("tape_rewind",               "复古磁带柔和倒带",    G_TRANS, 2.30, 1.20, False),
    ("film_projector",            "胶片齿轮柔顺过片",    G_TRANS, 2.60, 1.25, False),
    ("glitch_burst",              "科技感干净律动脉冲",  G_TRANS, 2.15, 1.00, False),
    ("cam_push_pull_flow",        "丝绸推拉流转风",      G_TRANS, 2.40, 1.20, False),
    ("whip_pan_double",           "双连灵动甩镜",        G_TRANS, 2.20, 1.10, False),
    ("velvet_whoosh",             "天鹅绒柔风掠过",      G_TRANS, 2.40, 1.20, True),
    ("cyber_warp_pass",           "未来感全息穿梭",      G_TRANS, 2.50, 1.25, False),
    ("spiral_vortex_spin",        "空灵旋涡回旋",        G_TRANS, 2.50, 1.25, False),
    ("glass_portal_shimmer",      "琉璃光斑折射",        G_TRANS, 2.50, 1.20, False),
    ("ink_brush_sweep",           "国风水墨挥毫流转",    G_TRANS, 2.60, 1.25, False),
    ("flame_ignition_whoosh",     "温暖火苗呼啸掠过",    G_TRANS, 2.40, 1.15, True),
    ("water_splash_dive",         "清泉水花涟漪转场",    G_TRANS, 2.50, 1.15, False),
    ("wind_gust_cinematic",       "山谷清风拂面转场",    G_TRANS, 2.70, 1.35, False),
    ("elastic_boing_snap",        "灵动弦拨回弹转场",    G_TRANS, 2.15, 0.95, False),
    ("tape_stop_drop",            "黑胶慢停下潜转场",    G_TRANS, 2.30, 1.05, False),
    ("projector_reel_change",     "电影胶片柔顺换卷",    G_TRANS, 2.40, 1.20, False),
    ("light_flare_sizzle",        "暖阳炫光泛音闪耀",    G_TRANS, 2.50, 1.20, False),
    ("sub_drop_tight",            "紧致Sub低频下潜",     G_TRANS, 2.30, 0.95, True),
    ("brass_braam_short",         "温暖圆号和弦共鸣",    G_TRANS, 2.70, 1.15, False),
    ("anvil_spark_hit",           "清脆星铁泛音定音",    G_TRANS, 2.40, 0.95, True),
    ("taiko_thunder_hit",         "大太鼓深沉共鸣转场",  G_TRANS, 2.70, 1.05, True),
    ("reverse_suck_punch",        "倒吸蓄力柔和重拍",    G_TRANS, 2.40, 1.45, False),
    ("shutter_flash_combo",       "双连快门柔光闪耀",    G_TRANS, 2.20, 1.05, False),
    ("trans_harp_gliss_up",       "仙气竖琴上行滑音",    G_TRANS, 2.80, 1.40, False),
    ("trans_harp_gliss_down",     "梦幻竖琴下行流淌",    G_TRANS, 2.80, 1.35, False),
    ("trans_guzheng_gliss",       "国风古筝流水拂弦",    G_TRANS, 2.85, 1.40, False),
    ("trans_bamboo_flute_breeze", "竹笛泛音清风转场",    G_TRANS, 2.75, 1.35, False),
    ("trans_crystal_chime_sweep", "水晶风铃星河扫掠",    G_TRANS, 2.70, 1.30, False),
    ("trans_water_drop_ripple",   "空灵水滴落玉涟漪",    G_TRANS, 2.50, 1.05, True),
    ("trans_sacred_choir_shimmer","圣洁唱诗和弦微光",    G_TRANS, 2.90, 1.45, False),
    ("trans_warm_piano_chord",    "治愈钢琴琶音流转",    G_TRANS, 2.75, 1.25, False),
    ("trans_celesta_magic_bell",  "钢片琴魔法星尘转场",  G_TRANS, 2.60, 1.20, False),
    ("trans_silk_air_flutter",    "轻柔真丝拂过微风",    G_TRANS, 2.40, 1.20, True),

    # ─── 2. 雨雪风雷自然天气 (25 条，>= 8.2s) ───
    ("rain_heavy_downpour",       "真实倾盆大雨",        G_WEATHER, 9.50, 4.75, False),
    ("rain_gentle_drizzle",       "温柔绵绵小雨",        G_WEATHER, 9.20, 4.60, False),
    ("rain_eaves_dripping",       "屋檐滴雨声",          G_WEATHER, 8.80, 4.40, False),
    ("rain_on_window_pane",       "窗户听雨",            G_WEATHER, 9.00, 4.50, False),
    ("thunderstorm_rolling",      "雷雨交加",            G_WEATHER, 9.60, 3.20, False),
    ("thunder_distant_rumble",    "远方春雷",            G_WEATHER, 8.60, 3.00, False),
    ("snow_footsteps_crunch",     "踏雪咯吱脚步与风雪",  G_WEATHER, 8.80, 4.40, False),
    ("blizzard_winter_wind",      "寒冬风雪呼啸",        G_WEATHER, 9.20, 4.60, False),
    ("wind_gentle_leaves",        "微风吹拂树林沙沙",    G_WEATHER, 9.00, 4.50, False),
    ("wind_valley_breeze",        "山谷清风",            G_WEATHER, 9.00, 4.50, False),
    ("wind_bamboo_grove",         "竹林风声",            G_WEATHER, 8.80, 4.40, False),
    ("wind_autumn_rustle",        "秋风扫落叶",          G_WEATHER, 8.60, 4.30, False),
    ("rain_umbrella_patter",      "雨打芭蕉与油纸伞",    G_WEATHER, 8.80, 4.40, False),
    ("rain_forest_canopy",        "热带雨林树冠落雨",    G_WEATHER, 9.20, 4.60, False),
    ("rain_summer_shower",        "夏日午后阵雨",        G_WEATHER, 8.80, 4.40, False),
    ("rain_puddle_ripples",       "水洼滴水与轻雨",      G_WEATHER, 8.60, 4.30, False),
    ("thunder_crack_rain",        "惊雷闪电与瓢泼大雨",  G_WEATHER, 9.00, 2.80, False),
    ("wind_pine_forest",          "松林涛声阵阵",        G_WEATHER, 9.00, 4.50, False),
    ("wind_meadow_grass",         "草原微风拂过草浪",    G_WEATHER, 8.80, 4.40, False),
    ("wind_mountain_ridge",       "高山山脊清冽微风",    G_WEATHER, 9.00, 4.50, False),
    ("snow_falling_quiet",        "静谧雪夜落雪微声",    G_WEATHER, 8.60, 4.30, False),
    ("hail_roof_patter",          "冰雹轻敲木屋顶",      G_WEATHER, 8.50, 4.25, False),
    ("morning_mist_breeze",       "晨雾林间清润微风",    G_WEATHER, 8.80, 4.40, False),
    ("night_breeze_trees",        "静夜微风拂叶",        G_WEATHER, 8.80, 4.40, False),
    ("storm_coastal_gale",        "海岸风雨交响",        G_WEATHER, 9.40, 4.70, False),

    # ─── 3. 江河湖海与流水声 (20 条，>= 8.2s) ───
    ("river_wide_flowing",        "宽阔大河奔流",        G_WATER, 9.40, 4.70, False),
    ("stream_babbling_brook",     "山间清澈小溪潺潺",    G_WATER, 9.20, 4.60, False),
    ("spring_water_trickle",      "林间清泉叮咚",        G_WATER, 8.80, 4.40, False),
    ("ocean_waves_beach",         "温柔海浪拍打沙滩",    G_WATER, 9.60, 4.80, False),
    ("ocean_surf_cliffs",         "澎湃海浪拍击礁石",    G_WATER, 9.50, 4.75, False),
    ("ocean_seagulls_breeze",     "海风与海鸥鸣叫",      G_WATER, 9.20, 4.60, False),
    ("waterfall_mountain_roar",   "壮观山谷瀑布",        G_WATER, 9.40, 4.70, False),
    ("cave_water_drops",          "溶洞水滴空灵回响",    G_WATER, 8.80, 4.40, False),
    ("lake_paddle_ripples",       "湖面木桨划水",        G_WATER, 8.60, 4.30, False),
    ("underwater_calm_bubbles",   "深海宁静水流气泡",    G_WATER, 8.80, 4.40, False),
    ("stream_pebble_rapids",      "鹅卵石浅滩溪流",      G_WATER, 9.00, 4.50, False),
    ("bamboo_fountain_zen",       "日式竹筒惊鹿流水",    G_WATER, 8.80, 4.40, False),
    ("lake_shore_lapping",        "静谧湖畔水波轻拍",    G_WATER, 9.00, 4.50, False),
    ("fountain_plaza_water",      "庭院喷泉水花叮咚",    G_WATER, 8.80, 4.40, False),
    ("ocean_tide_retreat",        "沙滩退潮细密水泡",    G_WATER, 9.00, 4.50, False),
    ("river_canyon_echo",         "峡谷江水滔滔",        G_WATER, 9.20, 4.60, False),
    ("spring_zen_garden",         "禅意庭院涓涓细流",    G_WATER, 8.80, 4.40, False),
    ("harbor_boat_water",         "港湾木船轻晃水声",    G_WATER, 8.80, 4.40, False),
    ("waterfall_gentle_pool",     "清幽水潭飞瀑流泉",    G_WATER, 9.00, 4.50, False),
    ("glacier_melt_drip",         "冰川融水清脆滴落",    G_WATER, 8.60, 4.30, False),

    # ─── 4. 悦耳鸟鸣与自然生灵 (20 条，>= 8.2s) ───
    ("birds_morning_forest",      "清晨森林悦耳鸟鸣",    G_BIRDS, 9.50, 4.75, False),
    ("birds_lark_melody",         "山谷百灵婉转歌唱",    G_BIRDS, 9.00, 4.50, False),
    ("birds_spring_chorus",       "春日群鸟欢快和鸣",    G_BIRDS, 9.40, 4.70, False),
    ("birds_nightingale_song",    "夜莺动人啼鸣",        G_BIRDS, 9.00, 4.50, False),
    ("birds_cuckoo_call",         "布谷鸟清脆呼唤",      G_BIRDS, 8.80, 4.40, False),
    ("birds_after_rain",          "雨后林间清脆鸟语",    G_BIRDS, 9.00, 4.50, False),
    ("crickets_night_chirping",   "夜晚草丛悦耳蛐蛐声",  G_BIRDS, 9.20, 4.60, False),
    ("summer_night_frogs_crickets","盛夏夜晚虫鸣蛙声",   G_BIRDS, 9.40, 4.70, False),
    ("countryside_night_insects", "乡村宁静夏夜虫鸣",    G_BIRDS, 9.00, 4.50, False),
    ("dawn_rooster_birds",        "清晨田园鸡鸣与鸟叫",  G_BIRDS, 8.80, 4.40, False),
    ("bees_meadow_hum",           "树林蜜蜂采蜜嗡鸣",    G_BIRDS, 8.50, 4.25, False),
    ("birds_sparrow_courtyard",   "庭院麻雀欢快叽喳",    G_BIRDS, 8.80, 4.40, False),
    ("birds_thrush_bamboo",       "竹林画眉婉转清音",    G_BIRDS, 9.00, 4.50, False),
    ("birds_magpie_joy",          "喜鹊枝头报喜清鸣",    G_BIRDS, 8.60, 4.30, False),
    ("birds_owl_moonlight",       "月夜森林猫头鹰远啼",  G_BIRDS, 8.80, 4.40, False),
    ("birds_swallow_eaves",       "春日屋檐燕语呢喃",    G_BIRDS, 8.80, 4.40, False),
    ("cicadas_summer_breeze",     "夏日树荫微风轻蝉",    G_BIRDS, 9.00, 4.50, False),
    ("pond_frogs_lotus",          "荷塘月色清脆蛙鸣",    G_BIRDS, 8.80, 4.40, False),
    ("birds_mountain_echo",       "空山幽谷灵鸟对歌",    G_BIRDS, 9.20, 4.60, False),
    ("cat_purr_gentle_meow",      "温暖猫咪呼噜与轻喵",  G_BIRDS, 8.60, 4.30, False),

    # ─── 5. 观众演讲现场、群众鼓掌与人物欢笑 (25 条，>= 8.2s) ───
    ("speech_hall_audience",      "演讲现场观众专注氛围与掌声", G_CROWD, 9.40, 4.70, False),
    ("crowd_applause_enthusiastic","全场观众热烈鼓掌",         G_CROWD, 9.20, 4.60, False),
    ("auditorium_standing_ovation","大型礼堂雷鸣掌声与欢呼",   G_CROWD, 9.60, 4.80, False),
    ("stadium_crowd_cheering",    "体育馆万人欢呼沸腾",        G_CROWD, 9.50, 4.75, False),
    ("salon_polite_applause",     "温馨沙龙礼貌鼓掌",          G_CROWD, 8.60, 4.30, False),
    ("people_laughing_hearty",    "人物开怀大笑",              G_CROWD, 8.80, 4.40, False),
    ("friends_laughter_warm",     "朋友聚会愉快欢笑",          G_CROWD, 9.00, 4.50, False),
    ("children_giggling_joy",     "孩童纯真银铃笑声",          G_CROWD, 8.80, 4.40, False),
    ("crowd_amazed_wow",          "观众惊喜赞叹哇声",          G_CROWD, 8.60, 3.50, False),
    ("audience_chuckle_clap",     "观众会心一笑与掌声",        G_CROWD, 8.80, 4.40, False),
    ("human_breathing_calm",      "舒缓深呼吸与放松冥想",      G_CROWD, 8.80, 4.40, False),
    ("conference_keynote_clap",   "发布会揭晓欢呼与掌声",      G_CROWD, 9.20, 4.60, False),
    ("theater_curtain_call",      "剧场谢幕持久掌声",          G_CROWD, 9.40, 4.70, False),
    ("concert_encore_cheer",      "音乐会返场热情欢呼",        G_CROWD, 9.40, 4.70, False),
    ("classroom_warm_applause",   "课堂分享温暖掌声",          G_CROWD, 8.60, 4.30, False),
    ("comedy_club_laughter",      "脱口秀现场爆笑与拍手",      G_CROWD, 9.00, 4.50, False),
    ("party_toast_cheers",        "庆功宴碰杯欢呼笑声",        G_CROWD, 8.80, 4.40, False),
    ("studio_audience_clap",      "演播室观众整齐拍手",        G_CROWD, 8.80, 4.40, False),
    ("crowd_rhythm_clap",         "全场跟随节奏拍手",          G_CROWD, 9.00, 4.50, False),
    ("baby_sweet_laugh",          "萌娃咯咯开心笑声",          G_CROWD, 8.60, 4.30, False),
    ("crowd_gasp_surprise",       "人群惊叹与热烈掌声",        G_CROWD, 8.80, 4.00, False),
    ("wedding_guests_cheer",      "婚礼现场祝福掌声欢笑",      G_CROWD, 9.00, 4.50, False),
    ("award_ceremony_ovation",    "颁奖盛典隆重掌声",          G_CROWD, 9.20, 4.60, False),
    ("choir_hum_resonance",       "圣殿人声温暖和声共鸣",      G_CROWD, 9.20, 4.60, False),
    ("playground_happy_voices",   "阳光操场欢快笑语",          G_CROWD, 8.80, 4.40, False),

    # ─── 6. 城市街道、火焰燃烧与生活场景 (25 条，>= 8.2s) ───
    ("city_street_traffic",       "繁华城市街道车流环境",      G_CITY_FIRE, 9.40, 4.70, False),
    ("city_night_ambience",       "都市夜晚街角氛围",          G_CITY_FIRE, 9.20, 4.60, False),
    ("pedestrian_street_lively",  "热闹步行街人流与交谈",      G_CITY_FIRE, 9.20, 4.60, False),
    ("cafe_warm_ambience",        "午后温暖咖啡馆环境",        G_CITY_FIRE, 9.40, 4.70, False),
    ("subway_train_journey",      "地铁列车进站与行驶",        G_CITY_FIRE, 9.20, 4.60, False),
    ("train_distant_rails",       "远方火车铁轨韵律",          G_CITY_FIRE, 9.00, 4.50, False),
    ("fire_campfire_crackling",   "露营篝火温暖噼啪燃烧",      G_CITY_FIRE, 9.40, 4.70, False),
    ("fire_fireplace_cozy",       "壁炉柴火静谧燃烧",          G_CITY_FIRE, 9.20, 4.60, False),
    ("fire_blazing_flames",       "熊熊烈火燃烧",              G_CITY_FIRE, 9.00, 4.50, False),
    ("fire_torch_burning",        "火炬火把燃烧",              G_CITY_FIRE, 8.80, 4.40, False),
    ("temple_bell_resonance",     "古寺钟声悠远回荡",          G_CITY_FIRE, 9.50, 2.00, False),
    ("church_bells_pealing",      "教堂钟声庄严回响",          G_CITY_FIRE, 9.40, 4.70, False),
    ("wind_chimes_melodic",       "微风吹动清脆风铃",          G_CITY_FIRE, 9.00, 4.50, False),
    ("library_quiet_ambience",    "安静图书馆翻书与环境",      G_CITY_FIRE, 8.80, 4.40, False),
    ("restaurant_bistro_murmur",  "法式西餐厅轻柔杯盘氛围",    G_CITY_FIRE, 9.00, 4.50, False),
    ("airport_terminal_hall",     "机场候机大厅广播氛围",      G_CITY_FIRE, 9.20, 4.60, False),
    ("harbor_foghorn_distant",    "清晨港口船笛与微浪",        G_CITY_FIRE, 8.80, 4.40, False),
    ("old_town_alley_morning",    "古镇清晨石板街巷氛围",      G_CITY_FIRE, 8.80, 4.40, False),
    ("night_market_bustle",       "人间烟火夜市热闹氛围",      G_CITY_FIRE, 9.00, 4.50, False),
    ("park_fountain_stroll",      "城市公园散步与喷泉",        G_CITY_FIRE, 8.80, 4.40, False),
    ("fire_bamboo_crackle",       "竹枝篝火清脆爆裂燃烧",      G_CITY_FIRE, 8.80, 4.40, False),
    ("fire_candle_wick_glow",     "静谧烛火微声与暖意",        G_CITY_FIRE, 8.60, 4.30, False),
    ("bicycle_bell_street",       "阳光街角单车清脆车铃",      G_CITY_FIRE, 8.60, 4.30, False),
    ("crosswalk_signal_city",     "街头绿灯提示音与步履",      G_CITY_FIRE, 8.80, 4.40, False),
    ("zen_monastery_morning",     "清晨山寺钟磬与微风",        G_CITY_FIRE, 9.40, 4.70, False),

    # ─── 7. 清脆玻璃破碎、常用拟音 Foley 与戏剧氛围 (25 条，>= 8.2s) ───
    ("glass_shatter_crisp",       "清脆玻璃破碎与碎片散落",    G_FOLEY, 8.60, 1.20, False),
    ("clock_ticking_steady",      "复古钟表规律滴答声",        G_FOLEY, 9.00, 4.50, False),
    ("keyboard_typing_flow",      "机械键盘清脆连续打字",      G_FOLEY, 9.00, 4.50, False),
    ("book_pages_turning",        "安静书房翻书与写字",        G_FOLEY, 8.80, 4.40, False),
    ("footsteps_wood_floor",      "木地板稳重脚步声",          G_FOLEY, 8.80, 4.40, False),
    ("footsteps_cobblestone",     "石板路漫步脚步声",          G_FOLEY, 8.80, 4.40, False),
    ("water_pouring_ice_glass",   "冰块落入玻璃杯与倒水",      G_FOLEY, 8.80, 4.40, False),
    ("film_projector_vintage",    "复古胶片放映机持续运转",    G_FOLEY, 9.00, 4.50, False),
    ("vinyl_warm_needle",         "黑胶唱片温暖前奏与轻擦声",  G_FOLEY, 9.20, 4.60, False),
    ("heartbeat_deep_pulse",      "心脏深沉规律跳动",          G_FOLEY, 8.80, 4.40, False),
    ("crystal_bowl_meditation",   "水晶音钵空灵疗愈长音",      G_FOLEY, 9.60, 4.80, False),
    ("magic_sparkle_shimmer",     "魔法星光闪烁持续氛围",      G_FOLEY, 9.00, 4.50, False),
    ("coffee_brewing_pour",       "手冲咖啡注水与滴滤声",      G_FOLEY, 8.80, 4.40, False),
    ("tea_ceremony_pouring",      "中式茶道沸水冲茗清音",      G_FOLEY, 8.80, 4.40, False),
    ("fountain_pen_writing",      "钢笔在纸张沙沙书写",        G_FOLEY, 8.60, 4.30, False),
    ("camera_shutter_session",    "摄影棚连续快门抓拍",        G_FOLEY, 8.60, 4.30, False),
    ("coins_treasure_chime",      "金币清脆落盘与叮当",        G_FOLEY, 8.60, 4.30, False),
    ("wine_glasses_toast",        "水晶酒杯清脆碰杯与倒酒",    G_FOLEY, 8.60, 2.20, False),
    ("wood_carving_craft",        "木工坊刨花与手作敲击",      G_FOLEY, 8.60, 4.30, False),
    ("typewriter_vintage_bell",   "复古打字机敲击与回车铃",    G_FOLEY, 8.80, 4.40, False),
    ("singing_bowl_tibetan",      "藏式颂钵泛音深沉疗愈",      G_FOLEY, 9.60, 4.80, False),
    ("kalimba_thumb_chime",       "拇指琴清澈灵动泛音流",      G_FOLEY, 9.00, 4.50, False),
    ("music_box_windup_tune",     "发条八音盒上弦与叮咚",      G_FOLEY, 9.20, 4.60, False),
    ("harp_ethereal_arpeggio",    "仙境竖琴泛音流淌氛围",      G_FOLEY, 9.20, 4.60, False),
    ("space_cosmic_pad",          "浩瀚星空深邃温暖氛围",      G_FOLEY, 9.50, 4.75, False),
]


# ═══════════════════════════════════════════════════════════════════════════
# 20 首柔和悦耳爆款配乐床（12.0s ~ 16.0s 无缝循环）
# ═══════════════════════════════════════════════════════════════════════════

BGM_GROUPS = ["治愈钢琴", "清新日常", "情感叙事", "电影弦乐", "国风雅韵", "空灵疗愈", "动感科技"]

_BGM_SPECS = [
    ("bgm_warm_piano_healing",    "温暖治愈钢琴 · 72BPM",        "治愈钢琴", 72,  13.3333),
    ("bgm_vlog_sunny_100bpm",     "阳光木吉他 Vlog · 100BPM",    "清新日常", 100, 14.4000),
    ("bgm_emotional_cello_piano", "深情大提琴与钢琴 · 68BPM",    "情感叙事", 68,  14.1176),
    ("bgm_lofi_study_85bpm",      "慵懒午后 Lo-Fi · 85BPM",      "清新日常", 85,  13.5529),
    ("bgm_cinematic_warm_90bpm",  "电影感温暖弦乐 · 90BPM",      "电影弦乐", 90,  13.3333),
    ("bgm_guofeng_ambient_76bpm", "国风古筝竹笛 · 76BPM",        "国风雅韵", 76,  12.6316),
    ("bgm_sacred_harp_choir",     "圣洁竖琴与唱诗 · 66BPM",      "空灵疗愈", 66,  14.5455),
    ("bgm_inspirational_story",   "励志纪录片钢琴 · 88BPM",      "情感叙事", 88,  13.6364),
    ("bgm_sunset_chill_synth",    "落日微风 Synth · 96BPM",      "动感科技", 96,  12.5000),
    ("bgm_music_box_memories",    "温柔八音盒回忆 · 75BPM",      "治愈钢琴", 75,  12.8000),
    ("bgm_upbeat_pop_124bpm",     "明快尤克里里与口哨 · 124BPM", "清新日常", 124, 15.4839),
    ("bgm_tech_pulse_120bpm",     "清新科技律动 · 120BPM",       "动感科技", 120, 14.0000),
    ("bgm_paris_cafe_waltz",      "巴黎街角手风琴吉他 · 108BPM", "清新日常", 108, 13.3333),
    ("bgm_zen_flute_meditation",  "自然冥想长笛 · 64BPM",        "空灵疗愈", 64,  15.0000),
    ("bgm_romantic_violin",       "浪漫小提琴小夜曲 · 78BPM",    "电影弦乐", 78,  12.3077),
    ("bgm_morning_kalimba",       "晨露拇指琴 · 92BPM",          "空灵疗愈", 92,  13.0435),
    ("bgm_epic_hope_rise",        "史诗希望交响 · 84BPM",        "电影弦乐", 84,  14.2857),
    ("bgm_jazz_piano_lounge",     "复古爵士钢琴酒廊 · 90BPM",    "治愈钢琴", 90,  13.3333),
    ("bgm_arabian_oud_oasis",     "丝路绿洲乌德琴与奈伊笛 · 80BPM","国风雅韵", 80,  12.0000),
    ("bgm_starry_night_pad",      "璀璨星空梦幻氛围 · 65BPM",    "空灵疗愈", 65,  14.7692),
]

# 兼容旧版 6 首 BGM ID 别名映射
_LEGACY_BGM_ALIASES = {
    "bgm_upbeat_vlog_120": "bgm_vlog_sunny_100bpm",
    "bgm_tech_pulse_128": "bgm_tech_pulse_120bpm",
    "bgm_warm_lofi_85": "bgm_lofi_study_85bpm",
    "bgm_cinematic_inspire_70": "bgm_emotional_cello_piano",
    "bgm_fashion_trap_100": "bgm_sunset_chill_synth",
    "bgm_happy_ukulele_110": "bgm_upbeat_pop_124bpm",
    "bgm_acoustic_sunny_vlog": "bgm_vlog_sunny_100bpm",
    "bgm_sunny_acoustic_vlog": "bgm_vlog_sunny_100bpm",
}


def _seed_of(item_id: str) -> int:
    return int(hashlib.sha256(("cutora-sao-1.0/" + item_id).encode("utf-8")).hexdigest()[:8], 16)


CATALOG = [
    {
        "id": sid,
        "label": label,
        "name": label,
        "group": group,
        "sfx_group": group,
        "category": group,
        "audio_kind": "sfx",
        "duration": float(dur),
        "fade_in": TRANSITION_SFX_FADE_IN if group == G_TRANS else 0.40,
        "fade_out": TRANSITION_SFX_FADE_OUT if group == G_TRANS else 0.60,
        "file": sid + ".wav",
        "license": LICENSE,
        "peak_offset": float(peak),
        "peak_check": bool(check),
        "seed": _seed_of(sid),
        "model": "stabilityai/stable-audio-open-1.0",
    }
    for sid, label, group, dur, peak, check in _SPECS
]

BGM_CATALOG = [
    {
        "id": bid,
        "label": label,
        "name": label,
        "group": group,
        "bgm_group": group,
        "category": group,
        "audio_kind": "bgm",
        "bpm": int(bpm),
        "duration": float(dur),
        "fade_in": 0.40,
        "fade_out": 0.60,
        "file": bid + ".wav",
        "license": LICENSE,
        "loopable": True,
        "seed": _seed_of(bid),
        "model": "stabilityai/stable-audio-open-1.0",
    }
    for bid, label, group, bpm, dur in _BGM_SPECS
]


def catalog_by_id() -> dict[str, dict]:
    return {c["id"]: c for c in CATALOG}


def bgm_catalog_by_id() -> dict[str, dict]:
    return {c["id"]: c for c in BGM_CATALOG}


def public_catalog() -> list[dict]:
    """返回公开音效目录副本，给 UI / JSON 用。"""
    return [{k: v for k, v in c.items() if not k.startswith("_")} for c in CATALOG]


def public_bgm_catalog() -> list[dict]:
    """返回公开配乐目录副本，给 UI / JSON 用。"""
    return [{k: v for k, v in c.items() if not k.startswith("_")} for c in BGM_CATALOG]


def sfx_path(sfx_id: str, base_dir: str | None = None) -> str | None:
    c = catalog_by_id().get(str(sfx_id or ""))
    if c is None:
        return None
    return os.path.join(base_dir or SOCIAL_DIR, c["file"])


def available_path(sfx_id: str, base_dir: str | None = None) -> str | None:
    p = sfx_path(sfx_id, base_dir=base_dir)
    return p if p and os.path.isfile(p) else None


def bgm_path(bgm_id: str, base_dir: str | None = None) -> str | None:
    bid = str(bgm_id or "").strip()
    c = bgm_catalog_by_id().get(bid)
    if c is None and bid in _LEGACY_BGM_ALIASES:
        # 优先检查旧版同名 WAV 是否存在，否则映射到新版同风格配乐
        legacy_file = os.path.join(base_dir or BGM_DIR, bid + ".wav")
        if os.path.isfile(legacy_file):
            return legacy_file
        c = bgm_catalog_by_id().get(_LEGACY_BGM_ALIASES[bid])
    if c is None:
        return None
    return os.path.join(base_dir or BGM_DIR, c["file"])


def available_bgm_path(bgm_id: str, base_dir: str | None = None) -> str | None:
    p = bgm_path(bgm_id, base_dir=base_dir)
    return p if p and os.path.isfile(p) else None


def missing_files(base_dir: str | None = None) -> list[str]:
    root = base_dir or SOCIAL_DIR
    return [c["id"] for c in CATALOG if not os.path.isfile(os.path.join(root, c["file"]))]


def missing_bgm_files(base_dir: str | None = None) -> list[str]:
    root = base_dir or BGM_DIR
    return [c["id"] for c in BGM_CATALOG if not os.path.isfile(os.path.join(root, c["file"]))]


def iter_all_audio_library_items(
    audio_kind: str | None = None,
    group: str | None = None,
    query: str = "",
) -> list[dict]:
    """返回全部 20 首配乐床 (audio_kind='bgm') 与 200 条音效 (audio_kind='sfx', sfx_group=...)
    的结构化字典列表，供资产库『音频』标签页按【配乐 / 音效】及子分类展示、搜索与试听。
    """
    want_kind = str(audio_kind or "").strip().lower()
    want_grp = str(group or "").strip()
    q = str(query or "").strip().lower()
    items: list[dict] = []

    if want_kind in ("", "all", "bgm", "music", "配乐"):
        for c in BGM_CATALOG:
            if want_grp and want_grp not in ("全部", "all", "配乐", "bgm", c["group"]):
                continue
            stored = os.path.join(BGM_DIR, c["file"])
            rec = {
                "id": c["id"],
                "name": f"🎵 {c['label']}",
                "label": c["label"],
                "type": "audio",
                "audio_kind": "bgm",
                "group": c["group"],
                "bgm_group": c["group"],
                "sfx_group": "",
                "category": c["group"],
                "bpm": c["bpm"],
                "duration": c["duration"],
                "duration_sec": c["duration"],
                "fade_in": c["fade_in"],
                "fade_out": c["fade_out"],
                "loopable": True,
                "file": c["file"],
                "_dir": BGM_DIR,
                "stored_path": stored,
                "source_path": stored,
                "path": stored,
                "license": "CUTORA-original",
                "_origin": "builtin",
                "missing": not os.path.isfile(stored),
                "tags": [
                    "bgm", "music", "配乐", "背景音乐",
                    c["group"], c["id"], c["label"], f"{c['bpm']}bpm",
                ],
            }
            if q:
                hay = " ".join(str(x) for x in rec["tags"]).lower()
                if not all(tok in hay for tok in q.split()):
                    continue
            items.append(rec)

    if want_kind in ("", "all", "sfx", "sound", "音效"):
        for c in CATALOG:
            if want_grp and want_grp not in ("全部", "all", "音效", "sfx", c["group"]):
                continue
            stored = os.path.join(SOCIAL_DIR, c["file"])
            rec = {
                "id": c["id"],
                "name": f"🔊 [{c['group']}] {c['label']}",
                "label": c["label"],
                "type": "audio",
                "audio_kind": "sfx",
                "group": c["group"],
                "sfx_group": c["group"],
                "bgm_group": "",
                "category": c["group"],
                "duration": c["duration"],
                "duration_sec": c["duration"],
                "fade_in": c["fade_in"],
                "fade_out": c["fade_out"],
                "peak_offset": c["peak_offset"],
                "loopable": False,
                "file": c["file"],
                "_dir": SOCIAL_DIR,
                "stored_path": stored,
                "source_path": stored,
                "path": stored,
                "license": "CUTORA-original",
                "_origin": "builtin",
                "missing": not os.path.isfile(stored),
                "tags": [
                    "sfx", "sound", "音效",
                    c["group"], c["id"], c["label"],
                ],
            }
            if q:
                hay = " ".join(str(x) for x in rec["tags"]).lower()
                if not all(tok in hay for tok in q.split()):
                    continue
            items.append(rec)

    return items


def render(sfx_id: str, base_dir: str | None = None) -> np.ndarray:
    """读取 48kHz 立体声 float 数组（-1..1）。"""
    p = available_path(sfx_id, base_dir=base_dir)
    if p is None:
        raise FileNotFoundError(f"SFX file missing for {sfx_id}: {sfx_path(sfx_id, base_dir)}")
    st, _sr, _nch, _sw = read_wav16(p)
    return st


def render_pcm(sfx_id: str, base_dir: str | None = None) -> bytes:
    return to_pcm16(render(sfx_id, base_dir=base_dir))


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_provenance(base_dir: str | None = None) -> dict:
    p = os.path.join(base_dir or SOCIAL_DIR, PROVENANCE_NAME)
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_bgm_provenance(base_dir: str | None = None) -> dict:
    p = os.path.join(base_dir or BGM_DIR, PROVENANCE_NAME)
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def verify_provenance(base_dir: str | None = None) -> tuple[bool, list[str]]:
    """校验 200 条音效 WAV、LICENSE.txt 与 PROVENANCE.json 的 SHA256 一致性。"""
    root = base_dir or SOCIAL_DIR
    errors: list[str] = []
    if not os.path.isfile(os.path.join(root, LICENSE_FILE_NAME)):
        errors.append("missing LICENSE.txt")
    prov = load_provenance(root)
    items = {it.get("id"): it for it in (prov.get("items") or []) if isinstance(it, dict)}
    for c in CATALOG:
        sid = c["id"]
        wav_p = os.path.join(root, c["file"])
        if not os.path.isfile(wav_p):
            errors.append(f"missing {c['file']}")
            continue
        it = items.get(sid)
        if not it:
            errors.append(f"missing provenance entry for {sid}")
            continue
        if _sha256_file(wav_p) != it.get("sha256"):
            errors.append(f"sha256 mismatch for {sid}")
    return len(errors) == 0, errors


def verify_bgm_provenance(base_dir: str | None = None) -> tuple[bool, list[str]]:
    """校验 20 首配乐床 WAV、LICENSE.txt 与 PROVENANCE.json 的 SHA256 一致性。"""
    root = base_dir or BGM_DIR
    errors: list[str] = []
    if not os.path.isfile(os.path.join(root, LICENSE_FILE_NAME)):
        errors.append("missing LICENSE.txt")
    prov = load_bgm_provenance(root)
    items = {it.get("id"): it for it in (prov.get("items") or []) if isinstance(it, dict)}
    for c in BGM_CATALOG:
        bid = c["id"]
        wav_p = os.path.join(root, c["file"])
        if not os.path.isfile(wav_p):
            errors.append(f"missing {c['file']}")
            continue
        it = items.get(bid)
        if not it:
            errors.append(f"missing provenance entry for {bid}")
            continue
        if _sha256_file(wav_p) != it.get("sha256"):
            errors.append(f"sha256 mismatch for {bid}")
    return len(errors) == 0, errors


def ensure_generated(base_dir: str | None = None) -> bool:
    """检查 200 条预生成音效文件是否全部就绪。"""
    return not missing_files(base_dir)


# ═══════════════════════════════════════════════════════════════════════════
# 非阻塞即时试听（供转场面板、转场检查器与资产库音频面板随手试听）
# ═══════════════════════════════════════════════════════════════════════════

_preview_proc = None
_preview_qeffect = None


def stop_preview_sfx() -> None:
    """停止当前正在播放的音效/配乐试听（非阻塞，异常安全）。"""
    global _preview_proc, _preview_qeffect
    if sys.platform == "win32":
        try:
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass
    if _preview_proc is not None:
        try:
            _preview_proc.terminate()
        except Exception:
            pass
        _preview_proc = None
    if _preview_qeffect is not None:
        try:
            _preview_qeffect.stop()
        except Exception:
            pass


def preview_audio_file(wav_path: str) -> bool:
    """非阻塞播放任意 WAV 文件路径。"""
    global _preview_proc, _preview_qeffect
    if not wav_path or not os.path.isfile(wav_path):
        stop_preview_sfx()
        return False
    abs_path = os.path.abspath(wav_path)
    if sys.platform == "win32":
        try:
            import winsound
            flags = winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
            winsound.PlaySound(abs_path, flags)
            return True
        except Exception:
            _log.exception("[SfxSocial] winsound preview failed for %s", abs_path)
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QSoundEffect
        if _preview_qeffect is None:
            _preview_qeffect = QSoundEffect()
        _preview_qeffect.stop()
        _preview_qeffect.setSource(QUrl.fromLocalFile(abs_path))
        _preview_qeffect.setVolume(0.85)
        _preview_qeffect.play()
        return True
    except Exception:
        pass
    if sys.platform == "darwin":
        try:
            import subprocess
            stop_preview_sfx()
            _preview_proc = subprocess.Popen(
                ["afplay", abs_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception:
            pass
    return False


def preview_sfx(sfx_id: str, base_dir: str | None = None) -> bool:
    """非阻塞播放指定 sfx_id（或 bgm_id / 文件路径）的 WAV 用于 UI 试听。"""
    sid = str(sfx_id or "").strip()
    if not sid or sid.lower() == "none":
        stop_preview_sfx()
        return False
    if os.path.isfile(sid):
        return preview_audio_file(sid)
    path = available_path(sid, base_dir=base_dir)
    if not path:
        path = available_bgm_path(sid, base_dir=base_dir)
    if not path:
        return False
    return preview_audio_file(path)


# ═══════════════════════════════════════════════════════════════════════════
# 设置开关：加转场自动带音效（默认开启 True）与添加前预选音效 ID
# ═══════════════════════════════════════════════════════════════════════════

def is_enabled() -> bool:
    """查询『加转场自动带音效』是否开启（默认开启 True）。"""
    try:
        from cutora.audio import sfx_library as _lib
        from cutora.core import safe_json
        p = _lib._settings_path()
        if not os.path.exists(p):
            return True
        with open(p, "r", encoding="utf-8") as f:
            data = safe_json.load(f)
        if not isinstance(data, dict) or TRANSITION_SFX_SETTING not in data:
            return True
        return bool(data.get(TRANSITION_SFX_SETTING, True))
    except Exception:
        _log.exception("[SfxSocial] read %s failed", TRANSITION_SFX_SETTING)
        return True


def set_enabled(on: bool) -> bool:
    """持久化『加转场自动带音效』开关。"""
    try:
        from cutora.audio import sfx_library as _lib
        from cutora.core import safe_json
        from cutora.core.secrets import strip_secret_fields
        p = _lib._settings_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        data = {}
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = safe_json.load(f) or {}
            except Exception:
                data = {}
        if not isinstance(data, dict):
            data = {}
        data = strip_secret_fields(data)
        data[TRANSITION_SFX_SETTING] = bool(on)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        _log.exception("[SfxSocial] write %s failed", TRANSITION_SFX_SETTING)
        return False


def is_auto_transition_sfx_enabled() -> bool:
    return is_enabled()


def set_auto_transition_sfx_enabled(on: bool) -> bool:
    return set_enabled(on)


def get_pre_transition_sfx_id() -> str:
    """获取转场面板在添加转场前预选的音效 ID（'auto' 表示跟随转场预设推荐）。"""
    return str(_PRE_TRANSITION_SFX_ID or "auto")


def set_pre_transition_sfx_id(sfx_id: str | None) -> str:
    """设置转场面板在添加转场前预选的音效 ID。"""
    global _PRE_TRANSITION_SFX_ID
    val = str(sfx_id or "").strip()
    if not val or val.lower() in ("auto", "default", "跟随转场推荐", "跟随转场推荐 (默认)"):
        _PRE_TRANSITION_SFX_ID = "auto"
    elif val.lower() in ("none", "off"):
        _PRE_TRANSITION_SFX_ID = "none"
    elif val in catalog_by_id():
        _PRE_TRANSITION_SFX_ID = val
    else:
        _PRE_TRANSITION_SFX_ID = "auto"
    return _PRE_TRANSITION_SFX_ID


# ═══════════════════════════════════════════════════════════════════════════
# 转场挂音效与配乐铺轨
# ═══════════════════════════════════════════════════════════════════════════

def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _set(obj, key, val) -> None:
    if obj is None:
        return
    if isinstance(obj, dict):
        obj[key] = val
    else:
        try:
            setattr(obj, key, val)
        except Exception:
            pass


def _resolve_timeline(project_or_timeline):
    obj = project_or_timeline
    for _ in range(3):
        if obj is None:
            return None
        if not isinstance(obj, dict) and hasattr(obj, "tracks") and hasattr(obj, "sources"):
            return obj
        nxt = _get(obj, "timeline", None)
        if nxt is None or nxt is obj:
            return None
        obj = nxt
    return None


def transition_window(transition):
    """(start, end) 秒；拿不到返回 None。支持 TransitionItem / dict / 独立转场块 Clip。"""
    try:
        start = float(_get(transition, "start_t", None))
    except (TypeError, ValueError):
        return None
    dur = _get(transition, "duration", None)
    if callable(dur):
        dur = None
    try:
        dur = float(dur) if dur is not None else None
    except (TypeError, ValueError):
        dur = None
    if dur is None:
        try:
            from cutora.render.transition_runtime import clip_timeline_span
            s, e = clip_timeline_span(transition)
            dur = e - s
        except Exception:
            return None
    if not (math.isfinite(start) and math.isfinite(dur)) or dur < 0 or start < 0:
        return None
    return start, start + dur


def transition_key(track, transition) -> str:
    tid = _get(transition, "id", None)
    if tid:
        return str(tid)
    win = transition_window(transition)
    tr_id = _get(track, "id", "") if track is not None else ""
    if win is None:
        return ""
    return "%s@%.3f+%.3f" % (tr_id, win[0], win[1] - win[0])


def _source_path(tl, clip):
    src = (getattr(tl, "sources", {}) or {}).get(_get(clip, "source_id", None))
    if src is None:
        return ""
    return str(_get(src, "path", "") or "")


def is_transition_sfx_clip(tl, clip) -> bool:
    if _get(clip, "transition_sfx_for", None):
        return True
    name = str(_get(clip, "name", "") or "")
    if not name.startswith(TRANSITION_SFX_NAME_PREFIX):
        return False
    p = _source_path(tl, clip)
    try:
        return bool(p) and os.path.normcase(os.path.dirname(os.path.abspath(p))) == \
            os.path.normcase(os.path.abspath(SOCIAL_DIR))
    except Exception:
        return False


def _clip_span(clip):
    try:
        from cutora.render.transition_runtime import clip_timeline_span
        return clip_timeline_span(clip)
    except Exception:
        s = float(_get(clip, "start_t", 0.0) or 0.0)
        return s, s + max(0.0, float(_get(clip, "out_t", 0.0) or 0.0) - float(_get(clip, "in_t", 0.0) or 0.0))


def _find_existing(tl, key, mid):
    hits = []
    for tr in getattr(tl, "tracks", []) or []:
        if _get(tr, "domain", None) != "audio" and _get(tr, "type", None) not in ("audio", "bgm", "sfx"):
            continue
        for c in list(_get(tr, "clips", []) or []):
            if not is_transition_sfx_clip(tl, c):
                continue
            tag = _get(c, "transition_sfx_for", None)
            if tag:
                if tag == key:
                    hits.append((tr, c))
                continue
            s, e = _clip_span(c)
            if s - 1e-3 <= mid <= e + 1e-3:
                hits.append((tr, c))
    return hits


def active_transition_sfx_id(project_or_timeline, track, transition) -> str:
    """查询转场当前绑定的音效 ID。"""
    try:
        explicit = str(_get(transition, "sfx_id", "") or "").strip()
        if explicit.lower() == "none":
            return "none"
        cat_map = catalog_by_id()
        if explicit in cat_map:
            return explicit
        tl = _resolve_timeline(project_or_timeline)
        win = transition_window(transition)
        if tl is not None and win is not None:
            hits = _find_existing(tl, transition_key(track, transition), (win[0] + win[1]) * 0.5)
            for _tr, c in hits:
                cid = str(_get(c, "transition_sfx_id", "") or "").strip()
                if cid in cat_map:
                    return cid
                sp = _source_path(tl, c)
                if sp:
                    stem = os.path.splitext(os.path.basename(sp))[0]
                    if stem in cat_map:
                        return stem
    except Exception:
        _log.exception("[SfxSocial] active_transition_sfx_id failed")
    return ""


def _apply_transition_clip_fades(clip, fade_in: float = TRANSITION_SFX_FADE_IN,
                                 fade_out: float = TRANSITION_SFX_FADE_OUT) -> None:
    """统一给转场音效 Clip 设置平滑缓入缓出属性（兼容 fade_in / fade_in_dur）。"""
    fi = float(fade_in)
    fo = float(fade_out)
    _set(clip, "fade_in", fi)
    _set(clip, "fade_out", fo)
    _set(clip, "fade_in_dur", fi)
    _set(clip, "fade_out_dur", fo)
    _set(clip, "fade_curve", "esin")


def attach_transition_sfx(project_or_timeline, track, transition, sfx_id=None, *, force: bool = False):
    """把 sfx_id 挂到转场上：音效轨（audio_role='sfx'，没有就建），冲击点对齐转场中点，
    并设置平滑缓入（clip.fade_in = 0.35）与缓出（clip.fade_out = 0.55）。

    幂等：同一个转场再调一次不会叠第二条——同音效只重新对齐，换音效就替换。
    """
    try:
        tl = _resolve_timeline(project_or_timeline)
        if tl is None:
            _log.warning("[SfxSocial] attach: no timeline object (%r)", type(project_or_timeline).__name__)
            return None
        raw_sid = str(sfx_id or "").strip()
        pre_sid = get_pre_transition_sfx_id()
        # 如果用户在转场面板预先指定了音效（非 auto），在新建/自动挂载时优先使用预选音效
        if not force and pre_sid not in ("", "auto", "default") and not _get(transition, "sfx_id", ""):
            raw_sid = pre_sid
        is_auto = raw_sid in ("", "auto", "default")
        if raw_sid.lower() in ("none", "off"):
            detach_transition_sfx(tl, track, transition)
            _set(transition, "sfx_id", "none")
            return None
        if is_auto:
            if not force and not is_enabled():
                return None
            if pre_sid not in ("", "auto", "default"):
                raw_sid = pre_sid
                is_auto = False
            else:
                try:
                    from cutora.render.transition_catalog import recommended_sfx
                    raw_sid = str(recommended_sfx(str(_get(transition, "preset", "") or "")) or "")
                except Exception:
                    raw_sid = ""
                if not raw_sid:
                    return None
        cat = catalog_by_id().get(raw_sid)
        if cat is None:
            _log.warning("[SfxSocial] attach: unknown sfx id %r", sfx_id)
            return None
        win = transition_window(transition)
        if win is None:
            _log.warning("[SfxSocial] attach: transition has no usable start/duration")
            return None
        path = available_path(cat["id"])
        if path is None:
            _log.warning("[SfxSocial] attach: file missing for %s", cat["id"])
            return None
        mid = (win[0] + win[1]) * 0.5
        key = transition_key(track, transition)
        if not key:
            return None
        from cutora.audio import sfx_library as _lib
        from cutora.model.timeline_model import Clip, MediaSource

        existing = _find_existing(tl, key, mid)
        start = mid - cat["peak_offset"]
        in_t = 0.0
        if start < 0.0:
            in_t = -start
            start = 0.0
        dur = float(cat["duration"])
        if dur - in_t < 0.05:
            _log.info("[SfxSocial] attach: %s would start before 0 s and be empty", cat["id"])
            return None
        keep = None
        for tr, c in existing:
            if keep is None and _source_path(tl, c) and os.path.normcase(os.path.abspath(_source_path(tl, c))) \
                    == os.path.normcase(os.path.abspath(path)):
                keep = c
                continue
            try:
                tr.clips.remove(c)
            except (ValueError, AttributeError):
                pass
        if keep is not None:
            keep.start_t = float(start)
            keep.in_t = float(in_t)
            keep.out_t = dur
            _apply_transition_clip_fades(keep, TRANSITION_SFX_FADE_IN, TRANSITION_SFX_FADE_OUT)
            keep.transition_sfx_for = key
            keep.transition_sfx_id = cat["id"]
            if not is_auto:
                _set(transition, "sfx_id", cat["id"])
            return keep

        sfx_track = _lib.get_or_create_sfx_track(tl)
        if sfx_track is None or isinstance(sfx_track, dict):
            _log.warning("[SfxSocial] attach: could not get an sfx track")
            return None
        src = MediaSource(path, duration=dur, probe=False)
        src.has_video = False
        src.has_audio = True
        tl.sources[src.id] = src
        clip = Clip(src.id, float(in_t), dur, float(start))
        clip.is_main = False
        clip.audio_role = "sfx"
        clip.gain_db = float(TRANSITION_SFX_GAIN_DB)
        _apply_transition_clip_fades(clip, TRANSITION_SFX_FADE_IN, TRANSITION_SFX_FADE_OUT)
        clip.loop = False
        clip.spatial_enabled = False
        clip.name = TRANSITION_SFX_NAME_PREFIX + cat["label"]
        clip.transition_sfx_for = key
        clip.transition_sfx_id = cat["id"]
        sfx_track.clips.append(clip)
        if not is_auto:
            _set(transition, "sfx_id", cat["id"])
        _log.info("[SfxSocial] attach %s → transition %s mid=%.3fs start=%.3fs", cat["id"], key, mid, start)
        return clip
    except Exception:
        _log.exception("[SfxSocial] attach_transition_sfx failed")
        return None


def place_transition_sfx_on_timeline(
    project_or_timeline,
    track_or_sfx_id=None,
    transition=None,
    sfx_id: str | None = None,
    *,
    start_t: float | None = None,
    duration: float | None = None,
    transition_start: float | None = None,
    transition_dur: float | None = None,
    force: bool = True,
):
    """在时间线上放置 2.0~3.0s 转场音效并应用平滑缓入（clip.fade_in = 0.35）与缓出（clip.fade_out = 0.55）。
    支持多种调用方式：
    1) place_transition_sfx_on_timeline(tl, track, transition, sfx_id=..., force=...)
    2) place_transition_sfx_on_timeline(tl, "whoosh_light", start_t=1.0, duration=2.4)
    3) place_transition_sfx_on_timeline(tl, transition_start=3.0, transition_dur=0.6, sfx_id="trans_harp_gliss_up")
    """
    if transition is not None and not isinstance(track_or_sfx_id, str):
        return attach_transition_sfx(
            project_or_timeline, track_or_sfx_id, transition, sfx_id=sfx_id, force=force
        )
    tl = _resolve_timeline(project_or_timeline)
    if tl is None:
        return None
    sid = str(sfx_id or (track_or_sfx_id if isinstance(track_or_sfx_id, str) else "") or "").strip()
    if sid in ("", "auto", "default"):
        pre = get_pre_transition_sfx_id()
        sid = pre if pre not in ("", "auto", "default", "none") else "whoosh_light"
    cat = catalog_by_id().get(sid)
    if cat is None:
        return None
    path = available_path(cat["id"])
    if path is None:
        return None
    from cutora.audio import sfx_library as _lib
    from cutora.model.timeline_model import Clip, MediaSource

    sfx_track = _lib.get_or_create_sfx_track(tl)
    if sfx_track is None or isinstance(sfx_track, dict):
        return None
    src_dur = float(cat["duration"])
    use_dur = float(duration) if (duration is not None and float(duration) > 0) else src_dur
    if start_t is not None:
        st_t = float(start_t)
    elif transition_start is not None:
        t_dur = float(transition_dur) if transition_dur is not None else 0.5
        mid = float(transition_start) + 0.5 * max(0.05, t_dur)
        st_t = max(0.0, mid - float(cat.get("peak_offset", src_dur * 0.4)))
    elif isinstance(transition, (int, float)):
        st_t = float(transition)
    elif isinstance(track_or_sfx_id, (int, float)):
        st_t = float(track_or_sfx_id)
    else:
        st_t = 0.0
    src = MediaSource(path, duration=src_dur, probe=False)
    src.has_video = False
    src.has_audio = True
    tl.sources[src.id] = src
    clip = Clip(src.id, 0.0, use_dur, max(0.0, st_t))
    clip.is_main = False
    clip.audio_role = "sfx"
    clip.gain_db = float(TRANSITION_SFX_GAIN_DB)
    _apply_transition_clip_fades(clip, TRANSITION_SFX_FADE_IN, TRANSITION_SFX_FADE_OUT)
    clip.loop = False
    clip.spatial_enabled = False
    clip.name = TRANSITION_SFX_NAME_PREFIX + cat["label"]
    clip.transition_sfx_id = cat["id"]
    sfx_track.clips.append(clip)
    return clip


def place_bgm_bed(project_or_timeline, bgm_id: str, start_t: float = 0.0,
                  duration: float | None = None, gain_db: float | None = None):
    """将指定配乐床铺到时间线配乐轨（audio_role='bgm'），默认开启无缝循环（loop=True）。"""
    try:
        tl = _resolve_timeline(project_or_timeline)
        if tl is None:
            return None
        bid = str(bgm_id or "").strip()
        cat = bgm_catalog_by_id().get(bid)
        if cat is None and bid in _LEGACY_BGM_ALIASES:
            cat = bgm_catalog_by_id().get(_LEGACY_BGM_ALIASES[bid])
        if cat is None:
            return None
        path = available_bgm_path(bid) or available_bgm_path(cat["id"])
        if path is None:
            return None
        from cutora.audio import sfx_library as _lib
        from cutora.model.timeline_model import Clip, MediaSource

        bgm_track = _lib.get_or_create_bgm_track(tl)
        if bgm_track is None or isinstance(bgm_track, dict):
            return None
        src_dur = float(cat["duration"])
        use_dur = float(duration) if (duration is not None and float(duration) > 0) else src_dur
        src = MediaSource(path, duration=src_dur, probe=False)
        src.has_video = False
        src.has_audio = True
        tl.sources[src.id] = src
        clip = Clip(src.id, 0.0, use_dur, max(0.0, float(start_t)))
        _lib.apply_new_bgm_defaults(clip)
        if gain_db is not None:
            clip.gain_db = float(gain_db)
        clip.fade_in = 0.40
        clip.fade_out = 0.60
        clip.fade_in_dur = 0.40
        clip.fade_out_dur = 0.60
        clip.name = cat["label"]
        clip.bgm_bed_id = cat["id"]
        bgm_track.clips.append(clip)
        return clip
    except Exception:
        _log.exception("[SfxSocial] place_bgm_bed failed")
        return None


def remove_transition_sfx(project_or_timeline, transition_id_or_obj) -> int:
    """兼容按 transition_id 或 transition 对象移除转场音效。"""
    try:
        tl = _resolve_timeline(project_or_timeline)
        if tl is None:
            return 0
        key = str(transition_id_or_obj or "") if isinstance(transition_id_or_obj, str) else str(_get(transition_id_or_obj, "id", "") or "")
        removed = 0
        for tr in getattr(tl, "tracks", []) or []:
            if _get(tr, "domain", None) != "audio" and _get(tr, "type", None) not in ("audio", "bgm", "sfx"):
                continue
            for c in list(_get(tr, "clips", []) or []):
                if is_transition_sfx_clip(tl, c) and (_get(c, "transition_sfx_for", None) == key):
                    try:
                        tr.clips.remove(c)
                        removed += 1
                    except (ValueError, AttributeError):
                        pass
        return removed
    except Exception:
        return 0


def detach_transition_sfx(project_or_timeline, track, transition) -> int:
    """删掉挂在该转场上的音效，返回删了几条。"""
    try:
        if str(_get(transition, "sfx_id", "") or "").strip().lower() != "none":
            _set(transition, "sfx_id", "")
        tl = _resolve_timeline(project_or_timeline)
        win = transition_window(transition)
        if tl is None or win is None:
            return 0
        hits = _find_existing(tl, transition_key(track, transition), (win[0] + win[1]) * 0.5)
        for tr, c in hits:
            try:
                tr.clips.remove(c)
            except (ValueError, AttributeError):
                pass
        return len(hits)
    except Exception:
        _log.exception("[SfxSocial] detach failed")
        return 0


def attach_recommended_for_track(project_or_timeline, track, force=False) -> int:
    """按转场元数据里的推荐音效，给一条轨道上的全部转场挂音效。开关关闭且未 force 时什么都不做。"""
    if not force and not is_enabled():
        return 0
    n = 0
    try:
        from cutora.render.transition_catalog import recommended_sfx
    except Exception:
        _log.exception("[SfxSocial] transition_catalog unavailable")
        return 0
    for trans in list(_get(track, "transitions", []) or []):
        sid = recommended_sfx(str(_get(trans, "preset", "") or ""))
        if sid and attach_transition_sfx(project_or_timeline, track, trans, sid, force=force) is not None:
            n += 1
    return n


def _sync_bgm_beds_module() -> None:
    """将 20 首柔和悦耳爆款配乐目录同步到 cutora.audio.bgm_beds 模块（保持向后兼容）。"""
    try:
        from cutora.audio import bgm_beds as _bgm_mod
        _bgm_mod._SPECS = list(_BGM_SPECS)
        _bgm_mod.CATALOG = list(BGM_CATALOG)
        _bgm_mod.GROUPS = list(BGM_GROUPS)
        _bgm_mod.catalog_by_id = bgm_catalog_by_id
        _bgm_mod.public_catalog = public_bgm_catalog
        _bgm_mod.bgm_path = bgm_path
        _bgm_mod.available_path = available_bgm_path
        _bgm_mod.missing_files = missing_bgm_files
        _bgm_mod.verify_provenance = verify_bgm_provenance
        _bgm_mod.place_bgm_bed = place_bgm_bed
    except Exception:
        pass


_sync_bgm_beds_module()


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m cutora.audio.sfx_social",
                                 description="短视频与场景音效包（200 条）与爆款配乐床（20 首）")
    ap.add_argument("--verify", action="store_true", help="校验 WAV 文件与 PROVENANCE.json")
    ap.add_argument("--list", action="store_true", help="只列出目录")
    args = ap.parse_args(argv)
    if args.list:
        for c in CATALOG:
            print("%-28s %-14s %-8s %.2fs peak@%.2fs" % (c["id"], c["label"], c["group"], c["duration"], c["peak_offset"]))
        return 0
    ok1, errs1 = verify_provenance()
    ok2, errs2 = verify_bgm_provenance()
    if not (ok1 and ok2):
        for e in errs1 + errs2:
            print("ERROR:", e)
        return 1
    print("[sfx_social] verified %d SFX in %s and %d BGM in %s" % (len(CATALOG), SOCIAL_DIR, len(BGM_CATALOG), BGM_DIR))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())
