# -*- coding: utf-8 -*-
"""引擎能力表：时长档位与画幅。规格 84 — 别处不许再写死 4/6/8 或 6/10/15。"""
from __future__ import annotations
from pathlib import Path
import logging
log = logging.getLogger("orante.caps")
# 规格 98：studio = 本地模型（MiniMax H3）。4~15 秒都能出，Flow 的 4/6/8、Grok 的 6/10/15 换过去都原样保留。
DURATIONS = {"flow": (4, 6, 8), "grok": (6, 10, 15), "studio": tuple(range(4, 16))}
ASPECTS = {"flow": ("9:16", "16:9"), "grok": ("follow_image",), "studio": ("9:16", "16:9")}
FLOW_ASPECT_CODE = {"9:16": 1, "16:9": 2}
NO_COPY_DEFAULT = {"flow": 8, "grok": 10, "studio": 10}
# 本地模型（LTX）与 Grok 统一按 6/10/15 秒自动拆段；手选仍兼容 4/6/8/10/15
COPY_TIERS = {"flow": (4, 6, 8), "grok": (6, 10, 15), "studio": (4, 6, 8, 10, 15)}
STUDIO_AUTO_TIERS = (6, 10, 15)

def aspect_for_image(path, engine: str = "flow") -> str:
    """横（w≥h）→ 16:9，竖 → 9:16；正方形按横并打日志。"""
    p = Path(path) if path else None
    if not p or not p.is_file():
        return "9:16"
    try:
        from PIL import Image
        with Image.open(p) as im:
            w, h = im.size
    except Exception:
        return "9:16"
    if w == h:
        log.info("正方形图 %s（%sx%s），按横屏 16:9", p.name, w, h)
        return "16:9"
    return "16:9" if w >= h else "9:16"

def flow_aspect_code(aspect: str) -> int:
    if aspect not in FLOW_ASPECT_CODE:
        raise ValueError(f"Flow 不支持画幅 {aspect!r}，只能是 9:16 或 16:9")
    return FLOW_ASPECT_CODE[aspect]


def snap_duration(engine: str, seconds) -> int:
    """把一段原本的时长换成这个引擎能出的档位（重生时临时换引擎用）。

    取不短于原时长的最短一档（念得完、不缺画面）；原时长比最长一档还长就取最长一档。
    例：Flow 8 秒 → Grok 10 秒；Flow 4/6 秒 → Grok 6 秒；Grok 10/15 秒 → Flow 8 秒；Grok 6 秒 → Flow 6 秒。"""
    eng = (engine or "").strip().lower()
    allowed = sorted(DURATIONS.get(eng) or ())
    if not allowed:
        raise ValueError(f"未知引擎 {engine!r}")
    try:
        d = int(round(float(seconds)))
    except (TypeError, ValueError):
        d = NO_COPY_DEFAULT.get(eng, allowed[-1])
    if d in allowed:
        return d
    longer = [x for x in allowed if x >= d]
    return longer[0] if longer else allowed[-1]
