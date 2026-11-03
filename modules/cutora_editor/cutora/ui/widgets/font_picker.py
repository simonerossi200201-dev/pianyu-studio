# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q170 · 专业的分组字体选择器：按语系分组、中阿文本地化名称、拼音首字母搜索、OFL 1.1 可商用徽标、后台异步字形预览。

不卡的关键只有一条：**界面线程上永远不解字体、不排版、不画预览**。

* 列表按语系分组展示（``最近使用``、``中文``、``阿拉伯文``、``日文``、``韩文``、``拉丁``、``其他文字``），
  当前任务字幕语言对应的分组自动置顶。
* 为中文字体与阿拉伯文字体提供友好的本地化展示名（如 ``站酷快乐体 ZCOOL KuaiLe``、``思源宋体 Noto Serif SC``、
  ``霞鹜文楷 LXGW WenKai TC`` 等），同时底层 ``font_family`` ID 保持不变以完全向后兼容。
* 搜索框支持匹配中文名、英文 Family 名、拼音首字母（如 ``zkkl``、``syht``、``xwwk``），并展示 ``OFL 1.1 · 可商用`` 徽标。
"""
from __future__ import annotations

import json
import os
import threading
from collections import OrderedDict

from PySide6.QtCore import (
    QAbstractListModel, QEvent, QModelIndex, QObject, QPoint, QRect, QRectF, QSize, QThread, QTimer, Qt, Signal,
)
from PySide6.QtGui import QColor, QFontMetricsF, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QFrame, QLineEdit, QListView, QStyledItemDelegate, QVBoxLayout, QWidget,
)

from cutora.core.log import get_logger
from cutora.ui.i18n import tr

_LOG = get_logger("cutora.ui.font_picker")

ROW_H = 32
NAME_PX = 15               # 列表里家族名的字号（属于 FONT_SCALE {11,12,13,15,17,20}）
SAMPLE_PX = 20             # 底部样张字号
LIST_W = 420
MAX_RECENT = 8
DEFAULT_SAMPLE = "The quick brown fox · 片语 Studio 字幕 · مرحبا 1234"
ROLE_FAMILY = Qt.ItemDataRole.UserRole + 1
ROLE_HEADER = Qt.ItemDataRole.UserRole + 2
ROLE_NOSCRIPT = Qt.ItemDataRole.UserRole + 3
ROLE_HEALTH = Qt.ItemDataRole.UserRole + 4     # 字体体检：blocked / checking / slow / ok / ""
ROLE_DISPLAY = Qt.ItemDataRole.UserRole + 5    # 本地化友好展示名
ROLE_BADGE = Qt.ItemDataRole.UserRole + 6      # 许可证徽标："OFL 1.1 · 可商用"
ROLE_SCRIPT = Qt.ItemDataRole.UserRole + 7     # 语系分组标识 (han / arabic / kana / hangul / latin / other)
FONT_COMMERCIAL_BADGE = "OFL 1.1 · 可商用"

# ───────────────────────────── 中阿文字体本地化名称与拼音首字母 ─────────────────────────────

# (localized_zh_title, row_native_preview_text, pinyin_and_search_keywords)
FONT_DISPLAY_META: dict[str, tuple[str, str, str]] = {
    # ---- 中文字体 (Chinese Simplified / Traditional / HK) ----
    "Noto Sans SC": ("思源黑体 Noto Sans SC", "思源黑体 Noto Sans SC", "syht notosanssc 思源黑体"),
    "Noto Serif SC": ("思源宋体 Noto Serif SC", "思源宋体 Noto Serif SC", "syst notoserifsc 思源宋体"),
    "Noto Sans TC": ("思源黑体繁体 Noto Sans TC", "思源黑體繁體 Noto Sans TC", "syhtft notosanstc 思源黑体繁体 思源黑體繁體"),
    "Noto Serif TC": ("思源宋体繁体 Noto Serif TC", "思源宋體繁體 Noto Serif TC", "systft notoseriftc 思源宋体繁体 思源宋體繁體"),
    "Noto Sans HK": ("思源黑体港版 Noto Sans HK", "思源黑體港版 Noto Sans HK", "syhtgb notosanshk 思源黑体港版 思源黑體港版"),
    "Noto Serif HK": ("思源宋体港版 Noto Serif HK", "思源宋體港版 Noto Serif HK", "systgb notoserifhk 思源宋体港版 思源宋體港版"),
    "Source Han Sans SC": ("思源黑体 Source Han Sans SC", "思源黑体 Source Han Sans SC", "syht sourcehansanssc 思源黑体"),
    "ZCOOL KuaiLe": ("站酷快乐体 ZCOOL KuaiLe", "站酷快乐体 ZCOOL KuaiLe", "zkkl zkklt zcoolkuaile 站酷快乐体"),
    "ZCOOL QingKe HuangYou": ("站酷庆科黄油体 ZCOOL QingKe HuangYou", "站酷庆科黄油体 ZCOOL QingKe HuangYou", "zkqkhy zkqkhyt zcoolqingkehuangyou 站酷庆科黄油体"),
    "ZCOOL XiaoWei": ("站酷小薇体 ZCOOL XiaoWei", "站酷小薇体 ZCOOL XiaoWei", "zkxw zkxwt zcoolxiaowei 站酷小薇体"),
    "Ma Shan Zheng": ("马善政毛笔楷书 Ma Shan Zheng", "马善政毛笔楷书 Ma Shan Zheng", "msz mszmbks mashanzheng 马善政毛笔楷书"),
    "Zhi Mang Xing": ("钟齐志莽行书 Zhi Mang Xing", "钟齐志莽行书 Zhi Mang Xing", "zmx zqzmxs zhimangxing 钟齐志莽行书"),
    "Long Cang": ("有字库龙藏体 Long Cang", "有字库龙藏体 Long Cang", "lct yzklct longcang 有字库龙藏体"),
    "Liu Jian Mao Cao": ("刘建毛草 Liu Jian Mao Cao", "刘建毛草 Liu Jian Mao Cao", "ljmc liujianmaocao 刘建毛草"),
    "LXGW WenKai TC": ("霞鹜文楷 LXGW WenKai TC", "霞鶩文楷 LXGW WenKai TC", "xwwk lxgwwenkaitc 霞鹜文楷 霞鶩文楷"),
    "LXGW WenKai Mono TC": ("霞鹜文楷等宽 LXGW WenKai Mono TC", "霞鶩文楷等寬 LXGW WenKai Mono TC", "xwwkdk lxgwwenkaimonotc 霞鹜文楷等宽"),
    "LXGW Marker Gothic": ("霞鹜漫黑 LXGW Marker Gothic", "霞鹜漫黑 LXGW Marker Gothic", "xwmh lxgwmarkergothic 霞鹜漫黑"),
    "WDXL Lubrifont SC": ("标小智无界黑 WDXL Lubrifont SC", "标小智无界黑 WDXL Lubrifont SC", "bxzwjh wdxllubrifontsc 标小智无界黑"),
    "Chiron Hei HK": ("昭源黑体 Chiron Hei HK", "昭源黑體 Chiron Hei HK", "zyht chironheihk 昭源黑体 昭源黑體"),
    "Chiron GoRound TC": ("昭源圆体 Chiron GoRound TC", "昭源圓體 Chiron GoRound TC", "zyyt chirongoroundtc 昭源圆体 昭源圓體"),
    "jf-openhuninn-2.0": ("海伦圆体 Huninn", "海倫圓體 Huninn", "hlyt huninn jfopenhuninn 海伦圆体 海倫圓體 粉圆"),
    "Huninn": ("海伦圆体 Huninn", "海倫圓體 Huninn", "hlyt huninn jfopenhuninn 海伦圆体 海倫圓體 粉圆"),
    "Iansui": ("芫荽体 Iansui", "芫荽體 Iansui", "yst iansui 芫荽体 芫荽體"),
    "Cactus Classical Serif": ("仙人掌明朝体 Cactus Classical Serif", "仙人掌明朝體 Cactus Classical Serif", "xrzmct cactusclassicalserif 仙人掌明朝体"),
    "Chocolate Classical Sans": ("巧克力黑体 Chocolate Classical Sans", "巧克力黑體 Chocolate Classical Sans", "qklht chocolateclassicalsans 巧克力黑体"),
    # ---- 阿拉伯文字体 (Arabic) ----
    "Cairo": ("开罗现代黑体 Cairo", "Cairo  القاهرة الحديثة", "klxdht cairo 开罗现代黑体"),
    "Cairo Play": ("开罗炫彩体 Cairo Play", "Cairo Play  القاهرة", "klxct cairoplay 开罗炫彩体"),
    "Tajawal": ("塔贾瓦尔圆黑 Tajawal", "Tajawal  خط تجوال", "tjwryh tajawal 塔贾瓦尔圆黑"),
    "Amiri": ("阿米里古典体 Amiri", "Amiri  الخط الأميري", "amlgdt amiri 阿米里古典体"),
    "Noto Sans Arabic": ("思源阿文黑体 Noto Sans Arabic", "Noto Sans Arabic  خط نسخ", "syawht notosansarabic 思源阿文黑体"),
    "Noto Naskh Arabic": ("思源阿文抄书体 Noto Naskh Arabic", "Noto Naskh Arabic  نسخ عربي", "syawcst notonaskharabic 思源阿文抄书体"),
    "Noto Kufi Arabic": ("思源阿文库菲体 Noto Kufi Arabic", "Noto Kufi Arabic  كوفي عربي", "syawkft notokufiarabic 思源阿文库菲体"),
    "Reem Kufi": ("里姆库菲体 Reem Kufi", "Reem Kufi  ريم كوفي", "lmkft reemkufi 里姆库菲体"),
    "Lalezar": ("拉雷扎尔标题体 Lalezar", "Lalezar  عنوان عريض", "llzebtt lalezar 拉雷扎尔标题体"),
    "Changa": ("昌加现代体 Changa", "Changa  خط تشانجا", "cjxdt changa 昌加现代体"),
    "El Messiri": ("梅西里人文体 El Messiri", "El Messiri  المسيري", "mxlrwt elmessiri 梅西里人文体"),
    "Almarai": ("阿尔玛莱体 Almarai", "Almarai  خط المراعي", "aemlt almarai 阿尔玛莱体"),
    "Rakkas": ("拉卡斯花体 Rakkas", "Rakkas  خط رقاص", "lksht rakkas 拉卡斯花体"),
    "Aref Ruqaa": ("阿里夫行书 Aref Ruqaa", "Aref Ruqaa  عارف رقعة", "alfxs arefruqaa 阿里夫行书"),
    "Markazi Text": ("马尔卡齐正文体 Markazi Text", "Markazi Text  نص مركزي", "mekqzwt markazitext 马尔卡齐正文体"),
    "Readex Pro": ("里德克斯现代体 Readex Pro", "Readex Pro  قراءة واضحة", "ldksxdt readexpro 里德克斯现代体"),
    "Lemonada": ("柠檬圆体 Lemonada", "Lemonada  ليمونادة", "nmyt lemonada 柠檬圆体"),
    "Marhey": ("玛尔希手写体 Marhey", "Marhey  خط مرحي", "mexsxt marhey 玛尔希手写体"),
    "IBM Plex Sans Arabic": ("IBM 阿文无衬线 IBM Plex Sans Arabic", "IBM Plex Sans Arabic  عربي", "ibmaw ibmplexsansarabic"),
    "Mada": ("玛达简约体 Mada", "Mada  خط مدى", "mdjyt mada 玛达简约体"),
    "Kufam": ("库法姆几何体 Kufam", "Kufam  خط كوفام", "kfmjht kufam 库法姆几何体"),
    "Harmattan": ("哈马坦传统体 Harmattan", "Harmattan  خط هرمتان", "hmtctt harmattan 哈马坦传统体"),
    "Vibes": ("活力手写体 Vibes", "Vibes  خط فايبز", "hlsxt vibes 活力手写体"),
    "Blaka": ("布拉卡海报体 Blaka", "Blaka  خط بلاكا", "blkhbt blaka 布拉卡海报体"),
    "Lateef": ("拉蒂夫传统体 Lateef", "Lateef  خط لطيف", "ltfctt lateef 拉蒂夫传统体"),
    "Mirza": ("米尔扎艺术体 Mirza", "Mirza  خط ميرزا", "mezyst mirza 米尔扎艺术体"),
    "Scheherazade New": ("天方夜谭古典体 Scheherazade New", "Scheherazade New  شهرزاد", "tfytgdt scheherazadenew 天方夜谭古典体"),
    "Alkalami": ("卡拉米木笔体 Alkalami", "Alkalami  الخط القلمي", "klmmbt alkalami 卡拉米木笔体"),
    "Gulzar": ("古尔扎尔波斯体 Gulzar", "Gulzar  خط گلزار", "glebst gulzar 古尔扎尔波斯体"),
    "Ruwudu": ("鲁伍杜体 Ruwudu", "Ruwudu  خط روض", "lwdt ruwudu 鲁伍杜体"),
    "Katibeh": ("卡蒂贝标题体 Katibeh", "Katibeh  خط كتيبة", "ktbbtt katibeh 卡蒂贝标题体"),
    # ---- 日文 / 韩文 (Japanese / Korean) ----
    "Noto Sans JP": ("思源黑体日文 Noto Sans JP", "Noto Sans JP  日本語字幕", "syhtrw notosansjp 思源黑体日文"),
    "Noto Sans KR": ("思源黑体韩文 Noto Sans KR", "Noto Sans KR  한국어 자막", "syhthw notosanskr 思源黑体韩文"),
}

_APACHE_FAMILIES = frozenset({"Arimo", "Roboto", "Roboto Condensed", "Roboto Mono", "Roboto Slab", "Open Sans"})

GROUP_KEYS = ("han", "arabic", "kana", "hangul", "latin", "other")
GROUP_TITLES = {
    "han": "中文",
    "arabic": "阿拉伯文",
    "kana": "日文",
    "hangul": "韩文",
    "latin": "拉丁",
    "other": "其他文字",
}

_HAN_KEYWORDS = (
    "Noto Sans SC", "Noto Serif SC", "Noto Sans TC", "Noto Serif TC",
    "Noto Sans HK", "Noto Serif HK", "Source Han", "ZCOOL", "Ma Shan Zheng",
    "Zhi Mang Xing", "Long Cang", "Liu Jian Mao Cao", "LXGW", "WDXL",
    "Chiron", "Huninn", "huninn", "Iansui", "Cactus Classical", "Chocolate Classical",
)
_ARABIC_KEYWORDS = (
    "Arabic", "Naskh", "Kufi", "Ruqaa", "Cairo", "Tajawal", "Amiri",
    "Almarai", "Changa", "El Messiri", "Lalezar", "Rakkas", "Markazi",
    "Readex", "Lemonada", "Marhey", "Mada", "Kufam", "Harmattan",
    "Vibes", "Blaka", "Lateef", "Mirza", "Scheherazade", "Alkalami",
    "Gulzar", "Ruwudu",
)


def _match_meta_base(family: str) -> tuple[str, tuple[str, str, str] | None, str]:
    """Return (base_family, meta_tuple_or_None, weight_suffix)."""
    fam = str(family or "").strip()
    if fam in FONT_DISPLAY_META:
        return fam, FONT_DISPLAY_META[fam], ""
    # Check longest matching base prefix for DirectWrite named instances (e.g. "Noto Serif SC SemiBold")
    for base in sorted(FONT_DISPLAY_META.keys(), key=len, reverse=True):
        if fam.startswith(base + " "):
            suffix = fam[len(base):].strip()
            return base, FONT_DISPLAY_META[base], suffix
    return fam, None, ""


def font_display_name(family: str) -> str:
    """Friendly localized display name (Chinese + English), while keeping underlying font_family ID unchanged."""
    fam = str(family or "").strip()
    _base, meta, suffix = _match_meta_base(fam)
    if meta is not None:
        title = meta[0]
        return f"{title} {suffix}".strip() if suffix else title
    return fam


def font_row_preview_text(family: str) -> str:
    """Text rendered in the font's own glyphs inside the picker row (100% covered by the font's own cmap)."""
    fam = str(family or "").strip()
    _base, meta, suffix = _match_meta_base(fam)
    if meta is not None:
        native_preview = meta[1]
        return f"{native_preview} {suffix}".strip() if suffix else native_preview
    return fam


def font_license_badge(family: str) -> str:
    """Commercial license badge string shown in the font picker."""
    fam = str(family or "").strip()
    for ap in _APACHE_FAMILIES:
        if fam == ap or fam.startswith(ap + " "):
            return "OFL 1.1 / Apache 2.0 · 可商用"
    return "OFL 1.1 · 可商用"


def font_search_blob(family: str) -> str:
    """Lowercase searchable string containing English family name, Chinese display name, and Pinyin initials."""
    fam = str(family or "").strip()
    _base, meta, suffix = _match_meta_base(fam)
    if meta is not None:
        return f"{fam} {meta[0]} {meta[1]} {meta[2]} {suffix}".lower()
    return fam.lower()


def font_matches_search(family: str, query: str) -> bool:
    """Return True if query matches English family name, localized Chinese name, or Pinyin initials."""
    q = str(query or "").strip().lower()
    if not q:
        return True
    return q in font_search_blob(family)


def family_script_group(family: str) -> str:
    """Classify a font family into one of GROUP_KEYS: ('han', 'arabic', 'kana', 'hangul', 'latin', 'other')."""
    fam = str(family or "").strip()
    try:
        from cutora.subtitle.font_coverage import load_coverage
        covmap = load_coverage().get("coverage") or {}
        cov = covmap.get(fam)
        if not cov:
            for k, v in covmap.items():
                if k.startswith(fam + " ") and isinstance(v, dict):
                    cov = v
                    break
        if isinstance(cov, dict):
            if cov.get("han"):
                return "han"
            if cov.get("arabic"):
                return "arabic"
            if cov.get("kana"):
                return "kana"
            if cov.get("hangul"):
                return "hangul"
            other_scripts = (
                "hebrew", "devanagari", "bengali", "gurmukhi", "gujarati",
                "tamil", "telugu", "kannada", "malayalam", "sinhala",
                "thai", "lao", "tibetan", "myanmar", "ethiopic", "khmer",
                "armenian", "georgian",
            )
            if any(cov.get(s) for s in other_scripts):
                return "other"
            return "latin"
    except Exception:
        pass
    for kw in _HAN_KEYWORDS:
        if kw.lower() in fam.lower():
            return "han"
    for kw in _ARABIC_KEYWORDS:
        if kw.lower() in fam.lower():
            return "arabic"
    if " JP" in fam or fam.endswith("JP"):
        return "kana"
    if " KR" in fam or fam.endswith("KR"):
        return "hangul"
    return "latin"


def preferred_script_group(lang_code: str | None = None) -> str:
    """Map current task / subtitle language code to the top-pinned script group."""
    if not lang_code:
        try:
            from cutora.core import config as cfg
            lang_code = str(getattr(cfg, "LANGUAGE", "") or "").strip().lower()
        except Exception:
            lang_code = ""
    code = str(lang_code or "").strip().lower()
    if not code or code == "auto":
        return "han"
    if code.startswith("zh") or code in ("yue", "wuu", "nan", "hak", "han"):
        return "han"
    if code.startswith(("ar", "fa", "ur", "ps", "ku", "sd", "ug")) or code == "arabic":
        return "arabic"
    if code.startswith("ja") or code == "kana":
        return "kana"
    if code.startswith("ko") or code == "hangul":
        return "hangul"
    try:
        from cutora.asr.languages import get_language
        row = get_language(code) or {}
        scr = str(row.get("script") or "").strip().lower()
        if scr in GROUP_KEYS:
            return scr
        if scr in ("cyrillic", "greek"):
            return "latin"
        if scr:
            return "other"
    except Exception:
        pass
    return "latin" if code.startswith(("en", "es", "fr", "de", "it", "pt", "ru", "tr", "vi", "id", "ms", "nl", "pl")) else "han"


def ordered_script_groups(lang_code: str | None = None) -> list[str]:
    """Return GROUP_KEYS with the active language's script group pinned at index 0."""
    top = preferred_script_group(lang_code)
    out = [top] if top in GROUP_KEYS else []
    for g in GROUP_KEYS:
        if g not in out:
            out.append(g)
    return out


def _health(family: str) -> str:
    from cutora.ui import font_probe
    return font_probe.health(family)


# ───────────────────────────── 后台预览渲染 ─────────────────────────────

class FontPreviewRenderer(QThread):
    """一条线程画所有预览图。请求放进集合，最新的先画（用户正在看的那几行最先出来）。"""

    ready = Signal(str, QImage)        # (key, image)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cv = threading.Condition()
        self._queue: "OrderedDict[str, tuple]" = OrderedDict()
        self._stop = False
        self.setObjectName("cutora-font-preview")

    def request(self, key: str, family: str, text: str, px: int, fg: str, dpr: float, max_w: int) -> None:
        with self._cv:
            self._queue.pop(key, None)
            self._queue[key] = (family, text, px, fg, dpr, max_w)
            self._cv.notify()

    def forget(self, prefix: str) -> None:
        with self._cv:
            for k in [k for k in self._queue if k.startswith(prefix)]:
                self._queue.pop(k, None)

    def stop(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify_all()

    def run(self) -> None:  # noqa: D401
        while True:
            with self._cv:
                while not self._queue and not self._stop:
                    self._cv.wait()
                if self._stop:
                    return
                key, job = self._queue.popitem(last=True)      # 最新请求优先
            try:
                img = render_preview(*job)
            except Exception:
                _LOG.exception("字体预览渲染失败 %s", key)
                img = QImage()
            self.ready.emit(key, img)


def render_preview(family: str, text: str, px: int, fg: str, dpr: float, max_w: int) -> QImage:
    """在任意线程里画一张透明底的文字图（Qt 6 的光栅引擎支持线程里排版 / 画字）。"""
    from cutora.ui.panels.fonts import get_font_manager
    font = get_font_manager().get_font(family, int(px))
    fm = QFontMetricsF(font)
    text = str(text or "")
    if max_w > 0:
        text = fm.elidedText(text, Qt.TextElideMode.ElideRight, float(max_w))
    w = max(1, int(fm.horizontalAdvance(text) + 4))
    h = max(1, int(fm.height() + 2))
    if max_w > 0:
        w = min(w, int(max_w))
    img = QImage(int(w * dpr), int(h * dpr), QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(dpr)
    img.fill(0)
    p = QPainter(img)
    try:
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setFont(font)
        p.setPen(QColor(fg))
        p.drawText(QRectF(2, 0, w - 2, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
    finally:
        p.end()
    return img


class PreviewCache(QObject):
    """界面线程这边的缓存：key → QPixmap。没有就发请求，画好了 ``changed`` 通知刷新。"""

    changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pix: "OrderedDict[str, QPixmap]" = OrderedDict()
        self._pending: set = set()
        self._cap = 900
        self._renderer = None

    def _thread(self) -> FontPreviewRenderer:
        if self._renderer is None:
            r = FontPreviewRenderer()
            r.ready.connect(self._on_ready)
            r.start(QThread.Priority.LowPriority)
            app = QApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(self.shutdown)
            import atexit
            atexit.register(self.shutdown)      # 没走 aboutToQuit 的退出（测试 / 异常）也要先停线程，否则 Qt 直接 abort
            self._renderer = r
        return self._renderer

    def shutdown(self) -> None:
        r = self._renderer
        if r is not None:
            r.stop()
            r.wait(1500)
            self._renderer = None

    @staticmethod
    def key(family: str, text: str, px: int, fg: str, dpr: float, max_w: int) -> str:
        return "%s|%d|%s|%.2f|%d|%s" % (family, px, fg, dpr, max_w, text)

    def get(self, family: str, text: str, px: int, fg: str, dpr: float, max_w: int):
        k = self.key(family, text, px, fg, dpr, max_w)
        hit = self._pix.get(k)
        if hit is not None:
            self._pix.move_to_end(k)
            return hit
        if k not in self._pending:
            self._pending.add(k)
            self._thread().request(k, family, text, px, fg, dpr, max_w)
        return None

    def _on_ready(self, key: str, img: QImage) -> None:
        self._pending.discard(key)
        if img.isNull():
            return
        pix = QPixmap.fromImage(img)
        pix.setDevicePixelRatio(img.devicePixelRatio())
        while len(self._pix) >= self._cap:
            self._pix.popitem(last=False)
        self._pix[key] = pix
        self.changed.emit(key)

    def clear(self) -> None:
        """体检结果变了（某个字体从「体检中」变成可用 / 停用）→ 预览全部重画（只重画看得见的那几行）。"""
        self._pix.clear()


_CACHE = {"obj": None}


def preview_cache() -> PreviewCache:
    if _CACHE["obj"] is None:
        _CACHE["obj"] = PreviewCache()
    return _CACHE["obj"]


# ───────────────────────────── 最近使用 ─────────────────────────────

def _recent_path() -> str:
    from cutora.core.paths import settings_file
    return settings_file("font_recent.json")


def load_recent() -> list:
    try:
        with open(_recent_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return [str(x) for x in data if isinstance(x, str)][:MAX_RECENT]
    except Exception:
        return []


def push_recent(family: str) -> list:
    fam = str(family or "").strip()
    if not fam:
        return load_recent()
    rows = [r for r in load_recent() if r != fam]
    rows.insert(0, fam)
    rows = rows[:MAX_RECENT]
    try:
        path = _recent_path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False)
    except Exception:
        pass
    return rows


# ───────────────────────────── 模型 / 委托 ─────────────────────────────

class FontListModel(QAbstractListModel):
    def __init__(self, families, recent=(), no_script=(), parent=None, lang_code: str | None = None):
        super().__init__(parent)
        self._all = list(families)
        self._recent = [r for r in (recent or ()) if r in set(self._all)]
        self._no_script = set(no_script or ())
        self._lang_code = lang_code
        self._rows: list = []
        self._filter = ""
        self._search_blobs = {f: font_search_blob(f) for f in self._all}
        self._groups = {}
        for f in self._all:
            g = family_script_group(f)
            self._groups.setdefault(g, []).append(f)
        # Keep localized / curated families at the front of Chinese & Arabic groups
        for g in ("han", "arabic"):
            if g in self._groups:
                self._groups[g].sort(key=lambda fam: (0 if _match_meta_base(fam)[1] is not None else 1, fam.lower()))
        self.rebuild()

    def set_filter(self, text: str) -> None:
        self._filter = str(text or "").strip().lower()
        self.rebuild()

    def set_lang_code(self, lang_code: str | None) -> None:
        self._lang_code = lang_code
        self.rebuild()

    def rebuild(self) -> None:
        self.beginResetModel()
        rows = []
        if self._filter:
            q = self._filter
            hits = [f for f in self._all if q in self._search_blobs.get(f, f.lower())]

            def _rank(f: str):
                disp = font_display_name(f).lower()
                blob = self._search_blobs.get(f, f.lower())
                if f.lower().startswith(q) or disp.startswith(q):
                    return (0, f.lower())
                if any(tok.startswith(q) for tok in blob.split()):
                    return (1, f.lower())
                return (2, f.lower())

            hits.sort(key=_rank)
            rows.extend(("font", f) for f in hits)
        else:
            if self._recent:
                rows.append(("header", tr("最近使用")))
                rows.extend(("font", f) for f in self._recent)
            group_order = ordered_script_groups(self._lang_code)
            for idx, g in enumerate(group_order):
                g_fams = self._groups.get(g) or []
                if not g_fams:
                    continue
                g_title = tr(GROUP_TITLES.get(g, "其他文字"))
                if idx == 0:
                    header_text = "%s（%d）· %s" % (g_title, len(g_fams), tr("当前语种推荐"))
                else:
                    header_text = "%s（%d）" % (g_title, len(g_fams))
                rows.append(("header", header_text))
                rows.extend(("font", f) for f in g_fams)
        self._rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        kind, val = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return font_display_name(val) if kind == "font" else val
        if role == ROLE_HEADER:
            return kind == "header"
        if role == ROLE_FAMILY:
            return val if kind == "font" else ""
        if role == ROLE_DISPLAY:
            return font_display_name(val) if kind == "font" else val
        if role == ROLE_BADGE:
            return font_license_badge(val) if kind == "font" else ""
        if role == ROLE_SCRIPT:
            return family_script_group(val) if kind == "font" else ""
        if role == ROLE_NOSCRIPT:
            return kind == "font" and val in self._no_script
        if role == ROLE_HEALTH:
            return _health(val) if kind == "font" else ""
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        kind, val = self._rows[index.row()]
        if kind == "header" or _health(val) == "blocked":
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def row_of(self, family: str, prefer_all: bool = True) -> int:
        found = [i for i, (k, v) in enumerate(self._rows) if k == "font" and v == family]
        if not found:
            return -1
        return found[-1] if prefer_all else found[0]

    def is_font_row(self, row: int) -> bool:
        """能选的字体行（不是分组标题、也不是体检停用的字体）。"""
        return (0 <= row < len(self._rows) and self._rows[row][0] == "font"
                and _health(self._rows[row][1]) != "blocked")


class FontRowDelegate(QStyledItemDelegate):
    def __init__(self, picker, parent=None):
        super().__init__(parent)
        self._picker = picker

    def sizeHint(self, option, index):  # noqa: N802
        if index.data(ROLE_HEADER):
            return QSize(LIST_W, 24)
        return QSize(LIST_W, ROW_H)

    def paint(self, painter, option, index):  # noqa: N802
        from cutora.ui.theme.tokens import current
        tok = current()
        r = option.rect
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if index.data(ROLE_HEADER):
                painter.fillRect(r, QColor(tok.bg1))
                f = painter.font()
                f.setPixelSize(11)
                f.setBold(True)
                painter.setFont(f)
                painter.setPen(QColor(tok.fg2))
                painter.drawText(r.adjusted(12, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                                 str(index.data(Qt.ItemDataRole.DisplayRole) or ""))
                return
            fam = str(index.data(ROLE_FAMILY) or "")
            disp_name = str(index.data(ROLE_DISPLAY) or fam)
            selected = bool(option.state & option.state.__class__.State_Selected)
            hovered = (self._picker.hover_row == index.row())
            is_current = (fam == self._picker.current_family)
            if selected:
                bg = QColor(tok.accent)
                bg.setAlpha(60)
                painter.fillRect(r, bg)
            elif hovered:
                bg = QColor(tok.fg0)
                bg.setAlpha(14)
                painter.fillRect(r, bg)
            if is_current:
                painter.fillRect(QRect(r.left(), r.top() + 6, 3, r.height() - 12), QColor(tok.accent))
            no_script = bool(index.data(ROLE_NOSCRIPT))
            health = str(index.data(ROLE_HEALTH) or "")
            fg = tok.fg2 if (no_script or health in ("blocked", "checking")) else tok.fg0
            text_x = r.left() + 14
            tag_w = 0
            tag, tag_col = "", tok.fg2
            if health == "blocked":
                tag, tag_col = tr("会卡死·已停用"), tok.err
            elif health == "checking":
                tag = tr("体检中…")
            elif no_script:
                tag = tr("缺本语种字符")
            elif health == "slow":
                tag, tag_col = tr("较慢"), tok.warn
            else:
                tag, tag_col = tr("OFL 1.1 · 可商用"), tok.ok
            if tag:
                f = painter.font()
                f.setPixelSize(11)
                painter.setFont(f)
                tw = painter.fontMetrics().horizontalAdvance(tag) + 10
                right_pad = 24 if is_current else 8
                tag_w = tw + right_pad + 4
                tag_rect = QRectF(r.right() - tw - right_pad, r.top() + (r.height() - 16) / 2, tw, 16)
                painter.setPen(Qt.PenStyle.NoPen)
                c = QColor(tag_col)
                c.setAlpha(36)
                painter.setBrush(c)
                painter.drawRoundedRect(tag_rect, 8, 8)
                painter.setPen(QColor(tag_col))
                painter.drawText(tag_rect, Qt.AlignmentFlag.AlignCenter, tag)
            if is_current:
                f = painter.font()
                f.setPixelSize(12)
                painter.setFont(f)
                painter.setPen(QColor(tok.accent))
                painter.drawText(r.adjusted(0, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, "✓")
            max_w = max(40, r.width() - (text_x - r.left()) - tag_w - 6)
            row_prev = font_row_preview_text(fam)
            # 停用 / 还在体检的字体不拿它自己的字形画（画了也是默认字体，而且停用的就是一画就卡）
            pix = (None if health in ("blocked", "checking")
                   else self._picker.cache.get(fam, row_prev, NAME_PX, fg, self._picker.dpr, max_w))
            if pix is not None and not pix.isNull():
                ph = pix.height() / max(1.0, pix.devicePixelRatio())
                painter.drawPixmap(QPoint(text_x, int(r.top() + (r.height() - ph) / 2)), pix)
            else:
                # 还没画好：先用界面字体写名字（不等、不卡），画好了会刷新这一行
                f = painter.font()
                f.setPixelSize(13)
                painter.setFont(f)
                painter.setPen(QColor(fg))
                painter.drawText(QRect(text_x, r.top(), int(max_w), r.height()),
                                 Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                                 painter.fontMetrics().elidedText(disp_name, Qt.TextElideMode.ElideRight, int(max_w)))
        finally:
            painter.restore()


class SamplePane(QWidget):
    """底部样张：悬停 / 选中的字体写当前文字，并展示本地化名称与 OFL 1.1 · 可商用徽标。"""

    def __init__(self, picker, parent=None):
        super().__init__(parent)
        self._picker = picker
        self.family = ""
        self.setFixedHeight(SAMPLE_PX + 36)

    def set_family(self, fam: str) -> None:
        if fam != self.family:
            self.family = fam
            self.update()

    def paintEvent(self, ev):  # noqa: N802
        from cutora.ui.theme.tokens import current
        tok = current()
        p = QPainter(self)
        try:
            p.fillRect(self.rect(), QColor(tok.bg2))
            p.setPen(QPen(QColor(tok.line), 1))
            p.drawLine(0, 0, self.width(), 0)
            if not self.family:
                return
            f = p.font()
            f.setPixelSize(11)
            p.setFont(f)
            p.setPen(QColor(tok.fg2))
            disp = font_display_name(self.family)
            badge = font_license_badge(self.family)
            header_line = f"{disp}  [{badge}]"
            p.drawText(QRect(12, 4, self.width() - 24, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       header_line)
            health = _health(self.family)
            if health in ("blocked", "checking"):
                f.setPixelSize(13)
                p.setFont(f)
                p.setPen(QColor(tok.err if health == "blocked" else tok.fg2))
                msg = (tr("这个字体在这台电脑上一渲染就会卡死，已停用。老工程里用到它的地方暂用默认字体显示。")
                       if health == "blocked" else tr("正在后台体检这个字体（彩色 / 多轴可变字体），几秒后就能预览。"))
                p.drawText(QRect(12, 20, self.width() - 24, self.height() - 24),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap, msg)
                return
            sample_str = self._picker.sample_text_for(self.family)
            pix = self._picker.cache.get(self.family, sample_str, SAMPLE_PX, tok.fg0,
                                         self._picker.dpr, self.width() - 24)
            if pix is not None and not pix.isNull():
                ph = pix.height() / max(1.0, pix.devicePixelRatio())
                p.drawPixmap(QPoint(12, int(20 + (self.height() - 20 - ph) / 2)), pix)
        finally:
            p.end()


# ───────────────────────────── 弹窗 ─────────────────────────────

class FontPickerPopup(QFrame):
    picked = Signal(str)

    def __init__(self, families, current_family: str, sample_text: str = "", no_script=(), parent=None,
                 lang_code: str | None = None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("fontPicker")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.current_family = str(current_family or "")
        self._custom_sample = str(sample_text or "").replace("\n", " ").strip()[:48]
        self.sample_text = self._custom_sample or DEFAULT_SAMPLE
        self.hover_row = -1
        self.cache = preview_cache()
        self.dpr = float(self.devicePixelRatioF() or 1.0)
        self._accepted = False
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(70)
        self._hover_timer.timeout.connect(self._apply_hover)
        self._hover_pending = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        self.search = QLineEdit(self)
        self.search.setObjectName("fontPickerSearch")
        self.search.setPlaceholderText(tr("搜索 %d 个字体（中文名 / 英文名 / 拼音首字母）…") % len(families))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._on_filter)
        self.search.installEventFilter(self)
        lay.addWidget(self.search)
        self.model = FontListModel(families, load_recent(), no_script, self, lang_code=lang_code)
        self.view = QListView(self)
        self.view.setObjectName("fontPickerList")
        self.view.setModel(self.model)
        self.view.setItemDelegate(FontRowDelegate(self, self.view))
        self.view.setUniformItemSizes(False)
        self.view.setMouseTracking(True)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.clicked.connect(self._on_clicked)
        self.view.entered.connect(self._on_entered)
        self.view.viewport().installEventFilter(self)
        lay.addWidget(self.view, 1)
        self.sample = SamplePane(self, self)
        lay.addWidget(self.sample)
        self.cache.changed.connect(self._on_preview_ready)
        try:
            from cutora.ui import font_probe
            font_probe.qt_bridge().changed.connect(self._on_probe_changed)
        except Exception:
            pass
        self.setFixedWidth(LIST_W)
        self._select_family(self.current_family, scroll=True)
        self.sample.set_family(self.current_family)
        self.search.setFocus()

    def sample_text_for(self, family: str) -> str:
        if self._custom_sample:
            return self._custom_sample
        _base, meta, _suffix = _match_meta_base(family)
        if meta is not None and meta[1]:
            return meta[1]
        return self.sample_text

    # ---- 数据 ----
    def _select_family(self, fam: str, scroll: bool = False) -> None:
        row = self.model.row_of(fam, prefer_all=True) if fam else -1
        if row < 0:
            row = next((i for i in range(self.model.rowCount()) if self.model.is_font_row(i)), -1)
        if row >= 0:
            idx = self.model.index(row, 0)
            self.view.setCurrentIndex(idx)
            if scroll:
                self.view.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtCenter)

    def _on_filter(self, text: str) -> None:
        self.model.set_filter(text)
        self._select_family(self.current_family, scroll=False)
        self.view.scrollToTop()
        idx = self.view.currentIndex()
        if idx.isValid():
            self.sample.set_family(str(idx.data(ROLE_FAMILY) or ""))

    def _on_probe_changed(self) -> None:
        self.cache.clear()
        self.view.viewport().update()
        self.sample.update()

    def _on_preview_ready(self, key: str) -> None:
        self.view.viewport().update()
        self.sample.update()

    # ---- 交互 ----
    def _on_entered(self, index) -> None:
        row = index.row() if index.isValid() else -1
        if row != self.hover_row:
            self.hover_row = row
            self.view.viewport().update()
        fam = str(index.data(ROLE_FAMILY) or "") if index.isValid() else ""
        if fam:
            self._hover_pending = fam
            self._hover_timer.start()

    def _apply_hover(self) -> None:
        if self._hover_pending:
            self.sample.set_family(self._hover_pending)

    def _on_clicked(self, index) -> None:
        fam = str(index.data(ROLE_FAMILY) or "")
        if fam and _health(fam) != "blocked":
            self._accept(fam)

    def _accept(self, fam: str) -> None:
        self._accepted = True
        push_recent(fam)
        self.picked.emit(fam)
        self.close()

    def _move(self, delta: int) -> None:
        cur = self.view.currentIndex().row()
        n = self.model.rowCount()
        row = cur
        for _ in range(n):
            row += delta
            if row < 0 or row >= n:
                break
            if self.model.is_font_row(row):
                idx = self.model.index(row, 0)
                self.view.setCurrentIndex(idx)
                self.view.scrollTo(idx)
                self.sample.set_family(str(idx.data(ROLE_FAMILY) or ""))
                return

    def eventFilter(self, obj, ev):  # noqa: N802
        if obj is self.search and ev.type() == QEvent.Type.KeyPress:
            k = ev.key()
            if k in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                self._move(1 if k == Qt.Key.Key_Down else -1)
                return True
            if k in (Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
                self._move(10 if k == Qt.Key.Key_PageDown else -10)
                return True
            if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                idx = self.view.currentIndex()
                fam = str(idx.data(ROLE_FAMILY) or "") if idx.isValid() else ""
                if fam and _health(fam) != "blocked":
                    self._accept(fam)
                return True
            if k == Qt.Key.Key_Escape:
                self.close()
                return True
        if obj is self.view.viewport() and ev.type() == QEvent.Type.Leave:
            if self.hover_row != -1:
                self.hover_row = -1
                self.view.viewport().update()
        return super().eventFilter(obj, ev)

    def keyPressEvent(self, ev):  # noqa: N802
        if ev.key() == Qt.Key.Key_Escape:
            self.close()
            return
        # 焦点不在搜索框时打字也进搜索框
        self.search.setFocus()
        QApplication.sendEvent(self.search, ev)

    # ---- 定位 ----
    def show_below(self, anchor: QWidget) -> None:
        screen = anchor.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else QRect(0, 0, 1920, 1080)
        top_left = anchor.mapToGlobal(QPoint(0, anchor.height() + 2))
        h = min(560, int(avail.height() * 0.8))
        self.resize(LIST_W, h)
        x = min(max(avail.left(), top_left.x()), avail.right() - LIST_W)
        y = top_left.y()
        if y + h > avail.bottom():
            above = anchor.mapToGlobal(QPoint(0, 0)).y() - h - 2
            y = above if above >= avail.top() else max(avail.top(), avail.bottom() - h)
        self.move(x, y)
        self.show()
        self.search.setFocus()


def open_font_picker(anchor: QWidget, families, current_family: str, on_pick, sample_text: str = "",
                     no_script=(), lang_code: str | None = None) -> FontPickerPopup:
    pop = FontPickerPopup(list(families), current_family, sample_text, no_script, parent=anchor.window(),
                          lang_code=lang_code)
    pop.picked.connect(on_pick)
    pop.show_below(anchor)
    return pop
