# -*- coding: utf-8 -*-
"""Grok 时长档位 · 从老流水线原样抽出，数值一个都没改。

来源：
  grok_pipe._pick_duration  →  grok_batch.pick_duration
  表格自动下载器.py 注释：170 = 10s 一段，250 = 15s 一段
  grok_batch 默认折算：6s ≤112 ／ 10s ≤187 ／ 其余 15s
      （DUR_RATE=17.0 = 170÷10s，DUR_TOLERANCE=0.10）

本文件只给 services/split.py 当对照；拆段服务按规格用字符数分档，
阈值写在 config.DEFAULTS 的 split.grok.* 里。
"""
import os
import re

# —— 以下自 grok_batch.py 照抄，不改数值 ————————————————————————

SMART_DURATION = True  # 按段智能选 6s/10s/15s；False = 固定点 FALLBACK_DUR
FALLBACK_DUR = "10s"  # 关闭智能匹配或识别失败时使用的固定时长
DUR_RATE = 17.0       # 每秒可容纳的有效字符数（170 ÷ 10s）
DUR_TOLERANCE = 0.10  # 时长判定容差（与拆分工具的 10% 一致）
DUR_CHOICES = (6, 10, 15)  # Grok 页面提供的时长档位（秒）
# 时长档位分界（有效长度）。留空 = 按 DUR_RATE 折算（≤112→6s ≤187→10s 其余15s）。
# 环境变量 GROK_DUR_THRESHOLDS="150,250" 可覆盖：≤150→6s ≤250→10s 其余→15s
# 接力救援的「文件夹认领」模式会自动注入这个变量。
DUR_THRESHOLDS = os.environ.get("GROK_DUR_THRESHOLDS", "").strip()

# 与 表格自动下载器.py 的"有效长度"完全同一套算法：
# 字符数 + 标点停顿等效（逗号≈10、句号≈5），数字内部的 3.5 / 1,000 不算停顿

_SENT_PUNCT = set(".!?…。！？")
_COMMA_PUNCT = set(",;:，、；：")
_DIGIT_PUNCT_RE = re.compile(r"(?<=\d)[.,:](?=\d)")
PAUSE_COMMA = 10
PAUSE_SENT = 5


def norm_text(s):
    return re.sub(r"\s+", " ", s).strip()


def eff_len(text):
    """段落的有效长度：字符数 + 标点停顿等效字符数"""
    t = norm_text(text)
    w = len(t)
    for ch in _DIGIT_PUNCT_RE.sub("", t):
        if ch in _SENT_PUNCT:
            w += PAUSE_SENT
        elif ch in _COMMA_PUNCT:
            w += PAUSE_COMMA
    return w


def _parse_thresholds(s):
    """把 "150,250" 解析成 [150.0, 250.0]；格式不对返回 None"""
    try:
        v = [float(x) for x in s.replace("，", ",").split(",") if x.strip()]
    except ValueError:
        return None
    return v if len(v) == len(DUR_CHOICES) - 1 else None


def pick_duration(seg_text):
    """选能容纳该段的最短时长档位（含容差），都装不下就用最长档。

    默认按 DUR_RATE 折算；若设了 GROK_DUR_THRESHOLDS（如 "150,250"），
    则改用固定分界：有效长度 < 150 → 6s，≤ 250 → 10s，其余 → 15s。
    """
    if not SMART_DURATION:
        return FALLBACK_DUR
    L = eff_len(seg_text)
    cut = _parse_thresholds(DUR_THRESHOLDS) if DUR_THRESHOLDS else None
    if cut:
        # cut[i] = 第 i 档与第 i+1 档的分界；等于分界值时归上一档（更保险）
        if L < cut[0]:
            return f"{DUR_CHOICES[0]}s"
        for i in range(1, len(cut)):
            if L <= cut[i]:
                return f"{DUR_CHOICES[i]}s"
        return f"{DUR_CHOICES[-1]}s"
    for sec in DUR_CHOICES:
        if L <= sec * DUR_RATE * (1 + DUR_TOLERANCE):
            return f"{sec}s"
    return f"{max(DUR_CHOICES)}s"


def _pick_duration(seg_text):
    """grok_pipe._pick_duration 的原样入口：转给 pick_duration。"""
    return pick_duration(seg_text)
