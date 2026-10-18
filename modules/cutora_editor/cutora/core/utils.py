# SPDX-License-Identifier: GPL-3.0-or-later
import re
from PySide6.QtGui import QImage

# --- 全局图片缓存 ---
IMAGE_CACHE = {}

def get_cached_image(path):
    if path not in IMAGE_CACHE:
        img = QImage(path)
        if not img.isNull():
            IMAGE_CACHE[path] = img
    return IMAGE_CACHE.get(path)

def clean_text(text):
    # 移除零宽字符等干扰
    text = re.sub(r'[\u200b\u200c\u200d\ufeff]', '', text)
    return text.strip()

def format_time_ass(seconds):
    total_cs = int(round(float(seconds) * 100))
    if total_cs < 0:
        total_cs = 0
    h = total_cs // 360000
    rem = total_cs % 360000
    m = rem // 6000
    rem = rem % 6000
    s = rem // 100
    cs = rem % 100
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"
