# SPDX-License-Identifier: GPL-3.0-or-later
# Extracted from the T01 monolith by tools/split_monolith.py.
# Original lines: 545-546, 547-547, 548-548, 549-549, 550-550, 551-551, 552-577, 578-583, 584-584, 585-585, 586-586, 688-701, 703-710, 712-721, 723-738, 740-743, 747-758, 760-771, 773-775, 2073-2171, 2174-2178, 2181-2202, 2204-2209, 2214-2218, 9506-9873

import os
import json
from pathlib import Path
import shutil
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QLabel,
    QSplitter,
    QMessageBox,
    QGroupBox,
    QColorDialog,
    QCheckBox,
    QInputDialog,
    QComboBox,
    QTreeWidget,
    QTreeWidgetItem,
    QLineEdit,
    QDialog,
)
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QPixmap,
    QFontDatabase,
)
from PySide6.QtWidgets import QListView
from PySide6.QtWidgets import (
    QTreeWidget,
    QTreeWidgetItem,
    QMessageBox,
)
from PySide6.QtCore import Qt
# ---- Font Whitelist (NO COPYRIGHT RISK) ----
from cutora.core.log import get_logger
from cutora.core.paths import FONT_DIR
from cutora.ui import font_probe as _font_probe

_FONT_LOG = get_logger("cutora.ui.panels.fonts")

FONT_ROOT = Path(FONT_DIR)

FONT_METADATA_PATH = FONT_ROOT / "metadata.json"

FONT_FILES = {}

FONT_FILE_FAMILIES = {}

# path -> font id from the scan that actually called addApplicationFont.
FONT_FILE_IDS = {}

# Successful registration is per live QApplication. A later call must not
# scan again: addApplicationFont starts returning -1 after the suite has
# already loaded these files, and that empty scan used to report failure
# while the glyphs from the first registration were what rendering needs.
_FONTS_REGISTERED_FOR = None
_FONTS_REGISTERED_DIR = None

FONT_METADATA = {"font_tags": {}}

FONT_MAP = {
    "Arimo": {
        "Regular": FONT_ROOT / "Arimo" / "Arimo-Regular.ttf",
        "Bold": FONT_ROOT / "Arimo" / "Arimo-Bold.ttf",
        "Italic": FONT_ROOT / "Arimo" / "Arimo-Italic.ttf",
        "BoldItalic": FONT_ROOT / "Arimo" / "Arimo-BoldItalic.ttf",
    },
    "Liberation Sans": {
        "Regular": FONT_ROOT / "LiberationSans" / "LiberationSans-Regular.ttf",
        "Bold": FONT_ROOT / "LiberationSans" / "LiberationSans-Bold.ttf",
        "Italic": FONT_ROOT / "LiberationSans" / "LiberationSans-Italic.ttf",
        "BoldItalic": FONT_ROOT / "LiberationSans" / "LiberationSans-BoldItalic.ttf",
    },
    "Roboto": {
        "Regular": FONT_ROOT / "Roboto" / "Roboto-Regular.ttf",
        "Bold": FONT_ROOT / "Roboto" / "Roboto-Bold.ttf",
        "Italic": FONT_ROOT / "Roboto" / "Roboto-Italic.ttf",
        "BoldItalic": FONT_ROOT / "Roboto" / "Roboto-BoldItalic.ttf",
    },
    "Open Sans": {
        "Regular": FONT_ROOT / "Open_Sans" / "OpenSans-Regular.ttf",
        "Bold": FONT_ROOT / "Open_Sans" / "OpenSans-Bold.ttf",
        "Italic": FONT_ROOT / "Open_Sans" / "OpenSans-Italic.ttf",
        "BoldItalic": FONT_ROOT / "Open_Sans" / "OpenSans-BoldItalic.ttf",
    },
}

ALLOWED_FONTS = [
    "Arimo",
    "Liberation Sans",
    "Roboto",
    "Open Sans",
]

DEFAULT_FONT_FAMILY = "Arimo"

FONT_MANAGER = None
LAST_EMBEDDED_FONT_LOAD_COUNT = 0

FONT_COMBOS = []

def load_font_metadata():
    global FONT_METADATA
    try:
        if FONT_METADATA_PATH.exists():
            with open(FONT_METADATA_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    data.setdefault("font_tags", {})
                    FONT_METADATA = data
                    return data
    except Exception:
        pass
    FONT_METADATA = {"font_tags": {}}
    return FONT_METADATA

def save_font_metadata():
    try:
        FONT_METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(FONT_METADATA_PATH, "w", encoding="utf-8") as f:
            json.dump(FONT_METADATA, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False

def _iter_font_files():
    if not os.path.isdir(FONT_DIR):
        return []
    exts = {".ttf", ".otf", ".ttc"}
    skip_dir_names = {"_quarantine", "_extra", "fonts_pending", "Noto_Color_Emoji"}
    files = []
    for root, dirs, filenames in os.walk(FONT_DIR):
        dirs[:] = [d for d in dirs if d not in skip_dir_names]
        for fn in filenames:
            if fn.lower().endswith(tuple(exts)):
                files.append(os.path.join(root, fn))
    return files

def scan_fonts_from_dir():
    """Add each font file once. Returns families plus the ids from that add.

    The fourth value is (walked, add_failed, no_family) so a later empty
    result can say which gate fired.
    """
    family_files = {}
    file_families = {}
    file_ids = {}
    walked = 0
    add_failed = 0
    no_family = 0
    for path in _iter_font_files():
        walked += 1
        font_id = QFontDatabase.addApplicationFont(path)
        if font_id == -1:
            add_failed += 1
            continue
        families = QFontDatabase.applicationFontFamilies(font_id)
        if not families:
            no_family += 1
            continue
        file_ids[path] = font_id
        file_families[path] = list(families)
        for fam in families:
            family_files.setdefault(fam, [])
            if path not in family_files[fam]:
                family_files[fam].append(path)
    return family_files, file_families, file_ids, (walked, add_failed, no_family)


def _log_no_loadable(stats):
    walked, add_failed, no_family = stats
    _FONT_LOG.error(
        "fonts/ has no loadable font files: %s walked=%s add_failed=%s no_family=%s",
        FONT_DIR, walked, add_failed, no_family,
    )


def _mark_fonts_registered():
    global _FONTS_REGISTERED_FOR, _FONTS_REGISTERED_DIR
    _FONTS_REGISTERED_FOR = QApplication.instance()
    _FONTS_REGISTERED_DIR = FONT_DIR


def _already_registered():
    app = QApplication.instance()
    return (
        app is not None
        and _FONTS_REGISTERED_FOR is app
        and _FONTS_REGISTERED_DIR == FONT_DIR
        and FONT_MANAGER is not None
        and FONT_MANAGER._registered
        and os.path.isdir(FONT_DIR)
    )

def _get_default_font_family():
    if DEFAULT_FONT_FAMILY in ALLOWED_FONTS:
        return DEFAULT_FONT_FAMILY
    return ALLOWED_FONTS[0] if ALLOWED_FONTS else DEFAULT_FONT_FAMILY

class _FontComboOpener(QObject):
    """Q170：点字体下拉框 → 打开专业的字体选择器（后台渲染预览，不卡），不弹 Qt 原生列表。

    QComboBox 本身还在：它是数据（全部家族 / 当前值），选完写回 setCurrentText，原来的
    currentTextChanged 处理照旧。"""

    def eventFilter(self, combo, ev):  # noqa: N802
        et = ev.type()
        if et == QEvent.Type.MouseButtonPress:
            if ev.button() == Qt.MouseButton.LeftButton and combo.isEnabled():
                open_font_picker_for_combo(combo)
                return True
        elif et == QEvent.Type.KeyPress:
            k = ev.key()
            if k in (Qt.Key.Key_Space, Qt.Key.Key_F4) or (
                    k in (Qt.Key.Key_Down, Qt.Key.Key_Up) and ev.modifiers() & Qt.KeyboardModifier.AltModifier):
                open_font_picker_for_combo(combo)
                return True
        elif et == QEvent.Type.Wheel:
            # 滚轮在下拉框上一滑就换字体、整个预览重排一次 —— 容易误触，也是「随手一滚就卡」的来源之一
            return True
        return False


_OPENER = {"obj": None}
_PICKER = {"pop": None}


def _opener() -> _FontComboOpener:
    if _OPENER["obj"] is None:
        _OPENER["obj"] = _FontComboOpener()
    return _OPENER["obj"]


def font_sample_text(combo) -> str:
    """字体选择器底部样张用的文字：控件上挂的 fontSample 属性（属性面板会放当前文本内容）。"""
    try:
        v = combo.property("fontSample")
    except Exception:
        v = None
    return str(v or "")


def no_script_families(lang_code: str | None = None) -> set:
    """缺当前语种字符的家族（查预先算好的 JSON，不解 cmap）。"""
    if lang_code is None:
        try:
            from cutora.core import config as cfg
            lang_code = str(getattr(cfg, "LANGUAGE", "en") or "en").strip().lower() or "en"
        except Exception:
            lang_code = "en"
    try:
        from cutora.subtitle.font_coverage import family_covers_language
        return {name for name in ALLOWED_FONTS if not family_covers_language(name, lang_code)}
    except Exception:
        return set()


def open_font_picker_for_combo(combo: QComboBox):
    from cutora.ui.widgets.font_picker import open_font_picker
    old = _PICKER.get("pop")
    if old is not None:
        try:
            old.close()
        except RuntimeError:
            pass
    families = [combo.itemText(i) for i in range(combo.count())]

    def _pick(fam, _combo=combo):
        if not _combo_alive(_combo):
            return
        if _combo.currentText() != fam:
            _combo.setCurrentText(fam)
        _combo.setFocus()

    pop = open_font_picker(combo, families, combo.currentText(), _pick,
                           sample_text=font_sample_text(combo), no_script=no_script_families())
    _PICKER["pop"] = pop
    return pop


def register_font_combo(combo: QComboBox):
    if combo not in FONT_COMBOS:
        FONT_COMBOS.append(combo)
        view = combo.view()
        if isinstance(view, QListView):
            view.setUniformItemSizes(True)
        # Q170：原生弹出列表不用了，点开走 FontPickerPopup
        combo.setProperty("cutoraFontCombo", True)
        combo.installEventFilter(_opener())
    refresh_font_combo(combo)

def _combo_alive(combo) -> bool:
    if combo is None:
        return False
    try:
        combo.objectName()
    except RuntimeError:
        return False
    return True


def refresh_font_combo(combo: QComboBox):
    if not _combo_alive(combo):
        if combo in FONT_COMBOS:
            FONT_COMBOS.remove(combo)
        return
    current = combo.currentText()
    combo.blockSignals(True)
    combo.clear()
    lang_code = "en"
    try:
        from cutora.core import config as cfg
        lang_code = str(getattr(cfg, "LANGUAGE", "en") or "en").strip().lower() or "en"
    except Exception:
        lang_code = "en"
    try:
        from cutora.subtitle.font_coverage import family_covers_language
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QBrush, QColor
        for name in ALLOWED_FONTS:
            combo.addItem(name)
            idx = combo.count() - 1
            if lang_code and not family_covers_language(name, lang_code):
                combo.setItemData(idx, QBrush(QColor("#888888")), Qt.ForegroundRole)
                combo.setItemData(idx, "no-script", Qt.UserRole)
                combo.setItemData(
                    idx,
                    "不支持当前语种文字（%s）" % lang_code,
                    Qt.ToolTipRole,
                )
    except Exception:
        combo.addItems(ALLOWED_FONTS)
    target = current if current in ALLOWED_FONTS else _get_default_font_family()
    if target:
        combo.setCurrentText(target)
    combo.blockSignals(False)

def refresh_all_font_combos():
    for cb in list(FONT_COMBOS):
        if not _combo_alive(cb):
            if cb in FONT_COMBOS:
                FONT_COMBOS.remove(cb)
            continue
        refresh_font_combo(cb)

_SUBSTITUTED: set = set()
SUBSTITUTION_LISTENERS: list = []     # callable(family) — 第一次替换某个字体时（任何线程）


def _note_font_substituted(family: str) -> None:
    state = _font_probe.health(family)
    key = (family, state)
    if key in _SUBSTITUTED:
        return
    _SUBSTITUTED.add(key)
    if state == "checking":
        _FONT_LOG.info("[FONT] 字体「%s」还在体检（彩色 / 多轴可变字体），查清之前暂用 %s 显示", family,
                       DEFAULT_FONT_FAMILY)
        return
    _FONT_LOG.warning("[FONT] 字体「%s」在这台电脑上渲染会卡死，暂用 %s 代替", family, DEFAULT_FONT_FAMILY)
    for fn in list(SUBSTITUTION_LISTENERS):
        try:
            fn(family)
        except Exception:
            pass


class FontManager:
    def __init__(self, font_files):
        import threading
        self.font_files = font_files
        self._family_name_map = {}
        self._font_cache = {}
        self._registered = False
        self._lazy_files_loaded = set()
        self._font_lock = threading.Lock()

    def _ensure_family_loaded(self, family: str) -> str:
        """If running in lazy render-child mode, call addApplicationFont on the family's files on first use."""
        if not family:
            return DEFAULT_FONT_FAMILY
        files = self.font_files.get(family)
        if not files:
            for k, v in self.font_files.items():
                if k.lower() == family.lower():
                    files = v
                    family = k
                    break
        if files:
            for fpath in files:
                norm_p = os.path.normcase(os.path.abspath(fpath))
                if norm_p in self._lazy_files_loaded or fpath in FONT_FILE_IDS:
                    continue
                self._lazy_files_loaded.add(norm_p)
                fid = QFontDatabase.addApplicationFont(fpath)
                if fid != -1:
                    FONT_FILE_IDS[fpath] = fid
                    fams = list(QFontDatabase.applicationFontFamilies(fid) or ())
                    FONT_FILE_FAMILIES[fpath] = fams
                    for fam in fams:
                        if fam:
                            self._family_name_map[fam] = fam
                            if fam not in ALLOWED_FONTS and fam != "Noto Color Emoji":
                                ALLOWED_FONTS.append(fam)
                    if fams and family not in fams:
                        self._family_name_map[family] = fams[0]
        return self._family_name_map.get(family) or family

    def register_lazy_from_manifest(self) -> bool:
        """Render-child fast registration: populate ALLOWED_FONTS & FONT_FILES from font_script_coverage.json,
        and only call QFontDatabase.addApplicationFont on actual used families on demand!
        Saves ~235 addApplicationFont DirectWrite registrations and ~250 MB per render worker process.
        """
        global LAST_EMBEDDED_FONT_LOAD_COUNT, FONT_FILES, FONT_FILE_FAMILIES, FONT_FILE_IDS, ALLOWED_FONTS
        try:
            from cutora.subtitle.font_coverage import load_coverage
            cov = load_coverage() or {}
            by_fam = cov.get("files_by_family") or {}
        except Exception:
            by_fam = {}
        if not by_fam or not os.path.isdir(FONT_DIR):
            return self.register()
        fam_files = {}
        for fam, rels in by_fam.items():
            paths = []
            for r in rels or ():
                p = os.path.join(FONT_DIR, str(r).replace("/", os.sep))
                if os.path.isfile(p):
                    paths.append(p)
            if paths:
                fam_files[fam] = paths
        if not fam_files:
            return self.register()
        self.font_files = fam_files
        FONT_FILES = fam_files
        ALLOWED_FONTS = sorted(f for f in fam_files.keys() if f != "Noto Color Emoji")
        self._family_name_map = {f: f for f in fam_files}
        self._registered = True
        _mark_fonts_registered()
        self._ensure_family_loaded(DEFAULT_FONT_FAMILY)
        self._ensure_family_loaded("Noto Sans")
        LAST_EMBEDDED_FONT_LOAD_COUNT = len(self._lazy_files_loaded)
        _FONT_LOG.info("[FONT] Lazy font manifest ready (%d families, %d preloaded files)", len(fam_files), LAST_EMBEDDED_FONT_LOAD_COUNT)
        return True

    def register(self) -> bool:
        """
        把已经 scan 过的字体记入 family 映射。不再第二次 addApplicationFont。
        """
        global LAST_EMBEDDED_FONT_LOAD_COUNT
        global FONT_FILES, FONT_FILE_FAMILIES, FONT_FILE_IDS, ALLOWED_FONTS
        if not os.path.isdir(FONT_DIR):
            _FONT_LOG.error("[FONT] fonts/ folder missing: %s", FONT_DIR)
            return False
        if not self.font_files or not FONT_FILE_FAMILIES:
            fam_files, file_fams, file_ids, stats = scan_fonts_from_dir()
            self.font_files = fam_files
            FONT_FILES = fam_files
            FONT_FILE_FAMILIES = file_fams
            FONT_FILE_IDS = file_ids
            ALLOWED_FONTS = sorted(
                f for f in fam_files.keys() if f not in ("Noto Color Emoji",))
            if not fam_files:
                _log_no_loadable(stats)
                return False
        self._family_name_map = {}
        for family in self.font_files:
            if family and family not in self._family_name_map:
                self._family_name_map[family] = family
        seen_files = set()
        loaded = 0
        # DirectWrite exposes variable-font named instances as extra family
        # names that all point at the same file. Count unique files only;
        # keep every scan-time family name resolvable. The scan already
        # called addApplicationFont.
        for path, families in FONT_FILE_FAMILIES.items():
            if not path or not os.path.exists(path):
                continue
            key = os.path.normcase(os.path.abspath(path))
            if key in seen_files:
                continue
            seen_files.add(key)
            loaded += 1
            for fam in families or ():
                if fam and fam not in self._family_name_map:
                    self._family_name_map[fam] = fam
        LAST_EMBEDDED_FONT_LOAD_COUNT = loaded
        _FONT_LOG.info("[FONT] Loaded %s embedded font files", loaded)
        if loaded == 0:
            _FONT_LOG.error("fonts/ has no loadable font files: %s", FONT_DIR)
            return False
        if DEFAULT_FONT_FAMILY not in self._family_name_map and self._family_name_map:
            self._family_name_map[DEFAULT_FONT_FAMILY] = next(iter(self._family_name_map.values()))
        self._registered = True
        _mark_fonts_registered()
        return True

    def _style_key(self, bold: bool, italic: bool) -> str:
        if bold and italic:
            return "BoldItalic"
        if bold:
            return "Bold"
        if italic:
            return "Italic"
        return "Regular"

    def register_font_for_style(self, family_name: str, bold: bool = False, italic: bool = False) -> None:
        """
        每次字幕字体变化时强制调用：使用字体文件路径注册，并打印 family + 绝对路径。
        若路径不在 fonts/ 目录内则警告。
        """
        family = sanitize_font_family(family_name)
        style_key = self._style_key(bool(bold), bool(italic))
        files = self.font_files.get(family, [])
        ttf_path = files[0] if files else None
        if not ttf_path:
            _FONT_LOG.warning("[FONT] Font file missing for family=%s style=%s", family, style_key)
            return
        abs_path = str(Path(ttf_path).resolve())
        font_id = QFontDatabase.addApplicationFont(abs_path)
        families = QFontDatabase.applicationFontFamilies(font_id) if font_id != -1 else []
        family_name_loaded = families[0] if families else family
        _FONT_LOG.info("[FontLoaded] family=%s file=%s", family_name_loaded, abs_path)
        try:
            if not os.path.commonpath([abs_path, str(FONT_ROOT.resolve())]) == str(FONT_ROOT.resolve()):
                _FONT_LOG.warning("[FONT] Font path is outside fonts/: %s", abs_path)
        except Exception:
            _FONT_LOG.warning("[FONT] Unable to validate font path: %s", abs_path)

    def resolve_family(self, family_name: str) -> str:
        family = sanitize_font_family(family_name)
        return (
            self._family_name_map.get(family)
            or self._family_name_map.get(DEFAULT_FONT_FAMILY)
            or DEFAULT_FONT_FAMILY
        )

    def get_font(self, family_name: str, size: int, bold: bool = False, italic: bool = False,
                 style: str | None = None) -> QFont:
        from cutora.subtitle.font_coverage import EMOJI_FAMILY, apply_no_font_merging
        with self._font_lock:
            if not self._registered:
                from cutora.core.flags import is_render_child
                if is_render_child():
                    self.register_lazy_from_manifest()
                else:
                    self.register()
            raw = str(family_name or "").strip()
            if raw == EMOJI_FAMILY:
                from cutora.render.stickers import _ensure_font
                resolved = _ensure_font() or EMOJI_FAMILY
                key = (resolved, int(size), False, False, "")
                font = self._font_cache.get(key)
                if font is None:
                    font = QFont(resolved, int(size))
                    apply_no_font_merging(font)
                    self._font_cache[key] = font
                out = QFont(font)
                apply_no_font_merging(out)
                return out
            rec = resolve_font_request(family_name)
            if _font_probe.is_blocked(rec["family"]):
                # Q170：体检发现这个字体在这台电脑上一渲染就卡死 / 崩溃（或是风险字体、体检还没查到它）
                # → 暂用默认字体，绝不卡死。工程里存的字体名不动。
                _note_font_substituted(rec["family"])
                rec = {"family": DEFAULT_FONT_FAMILY, "style": ""}
            self._ensure_family_loaded(rec["family"])
            resolved = (
                self._family_name_map.get(rec["family"])
                or self._family_name_map.get(DEFAULT_FONT_FAMILY)
                or DEFAULT_FONT_FAMILY
            )
            style_name = (str(style).strip() if style else "") or rec.get("style") or ""
            key = (resolved, int(size), bool(bold), bool(italic), style_name)
            font = self._font_cache.get(key)
            if font is None:
                font = QFont(resolved, int(size))
                if style_name:
                    font.setStyleName(str(style_name))
                else:
                    font.setBold(bool(bold))
                    font.setItalic(bool(italic))
                apply_no_font_merging(font)
                self._font_cache[key] = font
            out = QFont(font)
            apply_no_font_merging(out)
            return out

    def clear_cache(self) -> None:
        """Drop cached QFont objects. Golden calls this between cases."""
        self._font_cache.clear()

import threading as _font_threading
_FONT_MGR_LOCK = _font_threading.Lock()

def get_font_manager() -> FontManager:
    global FONT_MANAGER
    if FONT_MANAGER is None:
        with _FONT_MGR_LOCK:
            if FONT_MANAGER is None:
                FONT_MANAGER = FontManager(FONT_FILES)
    return FONT_MANAGER

def load_embedded_fonts(lazy: bool = False) -> bool:
    """
    只加载软件自带 fonts/ 下的字体到 QFontDatabase（应用级）。
    注意：这不会删除系统字体，但我们会把 UI 下拉框和渲染全部限制在白名单。

    同一个 QApplication 里第二次调用直接返回 True，不再 addApplicationFont。
    """
    global ALLOWED_FONTS, FONT_FILES, FONT_FILE_FAMILIES, FONT_FILE_IDS, FONT_MANAGER
    with _FONT_MGR_LOCK:
        if _already_registered():
            return True
        if not os.path.isdir(FONT_DIR):
            _FONT_LOG.error("[FONT] fonts/ folder missing: %s", FONT_DIR)
            return False
        if lazy:
            FONT_MANAGER = FontManager({})
            if FONT_MANAGER.register_lazy_from_manifest():
                load_font_metadata()
                return True
        fam_files, file_fams, file_ids, stats = scan_fonts_from_dir()
        if not fam_files:
            _log_no_loadable(stats)
            return False
        FONT_FILES = fam_files
        FONT_FILE_FAMILIES = file_fams
        FONT_FILE_IDS = file_ids
        ALLOWED_FONTS = sorted(
            f for f in fam_files.keys() if f not in ("Noto Color Emoji",))
        FONT_MANAGER = FontManager(FONT_FILES)
        ok = FONT_MANAGER.register()
        load_font_metadata()
        refresh_all_font_combos()
        return ok


_WARM = {"thread": None}


def warm_font_caches_async(delay_ms: int = 1500) -> None:
    """Q170：启动后在后台把每个字体的 cmap 解一遍（排版时查「这个字这个字体有没有」要用）。
    以前第一次切到某个字体时在界面线程上解，大的 CJK 字体一个就要几十到几百毫秒。"""
    if _WARM["thread"] is not None:
        return

    def _work():
        import time
        time.sleep(max(0.0, delay_ms / 1000.0))
        try:
            from cutora.subtitle import font_coverage as FC
            for fam, files in list(FONT_FILES.items()):
                for path in files[:1]:
                    try:
                        FC._cmap_cps(path)
                    except Exception:
                        pass
                    time.sleep(0.002)          # 让路：别和启动期的别的后台活抢盘
        except Exception:
            _FONT_LOG.exception("font cmap warm-up failed")

    import threading
    t = threading.Thread(target=_work, name="cutora-font-warm", daemon=True)
    _WARM["thread"] = t
    t.start()

# Longest-first. Exact ALLOWED_FONTS match is tried before any split.
_WEIGHT_SUFFIXES = (
    "ExtraBold Italic", "ExtraLight Italic", "SemiBold Italic",
    "Black Italic", "Bold Italic", "Medium Italic", "Light Italic",
    "Extra Bold Italic", "Extra Light Italic", "Semi Bold Italic",
    "ExtraBold", "ExtraLight", "SemiBold",
    "Extra Bold", "Extra Light", "Semi Bold",
    "Black", "Bold", "Medium", "Light", "Regular", "Thin", "Heavy", "Italic",
)

LAST_FONT_RESOLVE = {
    "requested": "",
    "family": "",
    "style": None,
    "kind": "exact",
    "reason": "",
}
_LAST_FALLBACK_LOG = None


def family_style_names(family_name: str) -> list[str]:
    """Real QFontDatabase style names for a registered family. Empty if unknown."""
    rec = resolve_font_request(family_name)
    if rec.get("kind") == "fallback":
        fam = rec.get("family") or DEFAULT_FONT_FAMILY
    else:
        fam = rec.get("family") or DEFAULT_FONT_FAMILY
    try:
        mgr = FONT_MANAGER
        if mgr is not None and hasattr(mgr, "_ensure_family_loaded"):
            fam = mgr._ensure_family_loaded(str(fam))
        names = list(QFontDatabase.styles(str(fam)))
    except Exception:
        names = []
    return [n for n in names if n]


def style_weight_args(style: dict | None) -> tuple[bool, bool, str | None]:
    """(bold, italic, font_style). Non-empty font_style covers bold and italic."""
    if not isinstance(style, dict):
        return True, False, None
    fs = str(style.get("font_style") or "").strip()
    if fs:
        return False, False, fs
    return bool(style.get("bold", True)), bool(style.get("italic", False)), None


LAST_FONT_STYLE_RESOLVE = {
    "requested": "", "family": "", "style": "", "kind": "empty", "reason": "",
}


def resolve_font_style(family_name: str, style_name: str) -> dict:
    """Validate a stored font_style against the family's real styles.

    kind: empty | exact | missing
    Missing is loud — never silent-fallback to Regular.
    """
    global LAST_FONT_STYLE_RESOLVE
    raw = str(style_name or "").strip()
    rec = resolve_font_request(family_name)
    fam = rec.get("family") or DEFAULT_FONT_FAMILY
    if not raw:
        out = {
            "requested": raw, "family": fam, "style": "",
            "kind": "empty", "reason": "",
        }
        LAST_FONT_STYLE_RESOLVE = out
        return out
    names = family_style_names(family_name)
    if raw in names:
        out = {
            "requested": raw, "family": fam, "style": raw,
            "kind": "exact", "reason": "",
        }
        LAST_FONT_STYLE_RESOLVE = out
        return out
    out = {
        "requested": raw, "family": fam, "style": raw,
        "kind": "missing",
        "reason": "unregistered style",
        "available": names,
    }
    LAST_FONT_STYLE_RESOLVE = out
    _FONT_LOG.warning(
        "font_style missing: family=%r style=%r available=%s",
        fam, raw, names)
    return out


def resolve_font_request(name) -> dict:
    """Map a stored font_family string to a registered family (+ optional style).

    kind: exact | split | fallback | empty
    Split of "SN Pro SemiBold" → family SN Pro + style SemiBold is not a fallback.
    """
    global LAST_FONT_RESOLVE, _LAST_FALLBACK_LOG
    raw = str(name or "").strip()
    default = _get_default_font_family()
    if not raw:
        rec = {
            "requested": raw, "family": default, "style": None,
            "kind": "empty", "reason": "empty",
        }
        LAST_FONT_RESOLVE = rec
        return rec
    if raw in ALLOWED_FONTS:
        rec = {
            "requested": raw, "family": raw, "style": None,
            "kind": "exact", "reason": "",
        }
        LAST_FONT_RESOLVE = rec
        return rec
    for suffix in _WEIGHT_SUFFIXES:
        token = " " + suffix
        if len(raw) <= len(token):
            continue
        if raw.endswith(token) or raw.lower().endswith(token.lower()):
            family = raw[: -len(token)].strip()
            if family in ALLOWED_FONTS:
                rec = {
                    "requested": raw, "family": family, "style": suffix,
                    "kind": "split", "reason": "family+weight",
                }
                LAST_FONT_RESOLVE = rec
                return rec
    rec = {
        "requested": raw, "family": default, "style": None,
        "kind": "fallback",
        "reason": "unregistered family",
    }
    LAST_FONT_RESOLVE = rec
    key = (raw, default)
    if key != _LAST_FALLBACK_LOG:
        _FONT_LOG.warning(
            "font fallback: %r -> %r (%s)", raw, default, rec["reason"])
        _LAST_FALLBACK_LOG = key
    return rec


def sanitize_font_family(name):
    """Whitelist family. Unknown names fall back to default (logged)."""
    return resolve_font_request(name)["family"]


def render_font_family(name):
    """真正拿去画的家族名：白名单 + Q170 体检（会卡死 / 还没查清的风险字体 → 默认字体）。不要拿它写回工程。"""
    fam = resolve_font_request(name)["family"]
    if _font_probe.is_blocked(fam):
        _note_font_substituted(fam)
        return DEFAULT_FONT_FAMILY
    return fam

def force_allowed_font(style_or_obj: dict):
    """Resolve for logging / UI hint. Do not rewrite the stored family name."""
    if not isinstance(style_or_obj, dict):
        return style_or_obj
    resolve_font_request(style_or_obj.get("font_family"))
    return style_or_obj

class FontManagerDialog(QDialog):
    def __init__(self, parent=None, refresh_callback=None):
        super().__init__(parent)
        self.setWindowTitle("字体管理")
        self.setMinimumSize(860, 520)
        self.refresh_callback = refresh_callback
        load_font_metadata()
        self._build_ui()
        self._reload_fonts_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # ── Dark theme for the entire dialog ──

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        # 左侧：文件夹树
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        self.folder_tree = QTreeWidget()
        self.folder_tree.setHeaderHidden(True)
        self.folder_tree.currentItemChanged.connect(self._on_folder_selected)
        left_layout.addWidget(QLabel("字体文件夹"))
        left_layout.addWidget(self.folder_tree, 1)

        folder_btn_row = QHBoxLayout()
        self.btn_new_folder = QPushButton("新建文件夹")
        self.btn_rename_folder = QPushButton("重命名文件夹")
        self.btn_delete_folder = QPushButton("删除文件夹")
        self.btn_new_folder.clicked.connect(self._on_new_folder)
        self.btn_rename_folder.clicked.connect(self._on_rename_folder)
        self.btn_delete_folder.clicked.connect(self._on_delete_folder)
        folder_btn_row.addWidget(self.btn_new_folder)
        folder_btn_row.addWidget(self.btn_rename_folder)
        folder_btn_row.addWidget(self.btn_delete_folder)
        left_layout.addLayout(folder_btn_row)

        # 右侧：字体列表 + 标记 + 预览
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        self.font_list = QListWidget()
        self.font_list.currentItemChanged.connect(self._on_font_selected)
        right_layout.addWidget(QLabel("字体列表"))
        right_layout.addWidget(self.font_list, 1)

        font_btn_row = QHBoxLayout()
        self.btn_move_font = QPushButton("移动到文件夹")
        self.btn_move_font.clicked.connect(self._on_move_font)
        font_btn_row.addWidget(self.btn_move_font)
        font_btn_row.addStretch(1)
        right_layout.addLayout(font_btn_row)

        tag_group = QGroupBox("字体标记")
        tag_layout = QVBoxLayout(tag_group)
        self.chk_favorite = QCheckBox("标记为最喜爱")
        self.chk_favorite.toggled.connect(self._on_toggle_favorite)
        tag_layout.addWidget(self.chk_favorite)

        color_row = QHBoxLayout()
        self.btn_add_color = QPushButton("添加颜色")
        self.btn_add_color.clicked.connect(self._on_add_color)
        color_row.addWidget(self.btn_add_color)
        color_row.addStretch(1)
        tag_layout.addLayout(color_row)

        self.color_chip_row = QHBoxLayout()
        tag_layout.addLayout(self.color_chip_row)
        right_layout.addWidget(tag_group)

        preview_group = QGroupBox("颜色比较预览")
        preview_layout = QVBoxLayout(preview_group)
        self.preview_text = QLineEdit("示例文本 Abc 123")
        self.preview_text.textChanged.connect(self._update_preview)
        preview_layout.addWidget(self.preview_text)
        self.preview_area = QVBoxLayout()
        preview_layout.addLayout(self.preview_area)
        right_layout.addWidget(preview_group)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

    def _build_folder_tree(self):
        self.folder_tree.clear()
        root_item = QTreeWidgetItem([FONT_ROOT.name])
        root_item.setData(0, Qt.ItemDataRole.UserRole, str(FONT_ROOT))
        self.folder_tree.addTopLevelItem(root_item)
        self._add_folder_children(root_item, FONT_ROOT)
        self.folder_tree.setCurrentItem(root_item)
        self.folder_tree.expandAll()

    def _add_folder_children(self, parent_item, folder_path: Path):
        try:
            for entry in sorted(folder_path.iterdir(), key=lambda p: p.name.lower()):
                if entry.is_dir():
                    item = QTreeWidgetItem([entry.name])
                    item.setData(0, Qt.ItemDataRole.UserRole, str(entry))
                    parent_item.addChild(item)
                    self._add_folder_children(item, entry)
        except Exception:
            pass

    def _get_selected_folder(self):
        item = self.folder_tree.currentItem()
        if not item:
            return str(FONT_ROOT)
        return item.data(0, Qt.ItemDataRole.UserRole) or str(FONT_ROOT)

    def _on_folder_selected(self, *_args):
        self._refresh_font_list()

    def _refresh_font_list(self):
        self.font_list.clear()
        folder = self._get_selected_folder()
        if not folder or not os.path.isdir(folder):
            return
        for fn in sorted(os.listdir(folder)):
            if not fn.lower().endswith((".ttf", ".otf", ".ttc")):
                continue
            fpath = os.path.join(folder, fn)
            families = FONT_FILE_FAMILIES.get(fpath) or []
            family = families[0] if families else None
            if not family:
                continue
            tag = FONT_METADATA.get("font_tags", {}).get(family, {})
            favorite = bool(tag.get("favorite"))
            label = f"★ {family}" if favorite else family
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, {"path": fpath, "family": family})
            item.setFont(QFont(render_font_family(family)))
            colors = tag.get("colors") or []
            if colors:
                swatch = QColor(colors[0])
                if swatch.isValid():
                    pm = QPixmap(12, 12)
                    pm.fill(swatch)
                    item.setData(Qt.ItemDataRole.DecorationRole, QIcon(pm))
            self.font_list.addItem(item)
        if self.font_list.count() > 0:
            self.font_list.setCurrentRow(0)

    def _get_selected_font_info(self):
        item = self.font_list.currentItem()
        if not item:
            return None
        data = item.data(Qt.ItemDataRole.UserRole) or {}
        return data if isinstance(data, dict) else None

    def _on_font_selected(self, *_args):
        info = self._get_selected_font_info()
        if not info:
            self.chk_favorite.setChecked(False)
            self._rebuild_color_chips([])
            self._update_preview()
            return
        family = info.get("family")
        tag = FONT_METADATA.get("font_tags", {}).get(family, {})
        self.chk_favorite.blockSignals(True)
        self.chk_favorite.setChecked(bool(tag.get("favorite")))
        self.chk_favorite.blockSignals(False)
        self._rebuild_color_chips(tag.get("colors") or [])
        self._update_preview()

    def _on_toggle_favorite(self, checked):
        info = self._get_selected_font_info()
        if not info:
            return
        family = info.get("family")
        tag = FONT_METADATA.setdefault("font_tags", {}).setdefault(family, {})
        tag["favorite"] = bool(checked)
        save_font_metadata()
        self._refresh_font_list()

    def _on_add_color(self):
        info = self._get_selected_font_info()
        if not info:
            return
        family = info.get("family")
        tag = FONT_METADATA.setdefault("font_tags", {}).setdefault(family, {})
        colors = tag.setdefault("colors", [])
        c = QColorDialog.getColor(QColor("#ffffff"), self, "选择颜色")
        if c.isValid():
            hex_c = c.name()
            if hex_c not in colors:
                colors.append(hex_c)
                save_font_metadata()
                self._rebuild_color_chips(colors)
                self._update_preview()
                self._refresh_font_list()

    def _rebuild_color_chips(self, colors):
        while self.color_chip_row.count():
            item = self.color_chip_row.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        for col in colors:
            btn = QPushButton("")
            btn.setFixedSize(22, 22)
            btn.setToolTip(col)
            from cutora.ui.theme.apply import apply_swatch
            apply_swatch(btn, col)
            btn.clicked.connect(lambda _=False, c=col: self._remove_color(c))
            self.color_chip_row.addWidget(btn)
        self.color_chip_row.addStretch(1)

    def _remove_color(self, color_hex):
        info = self._get_selected_font_info()
        if not info:
            return
        family = info.get("family")
        tag = FONT_METADATA.setdefault("font_tags", {}).setdefault(family, {})
        colors = tag.setdefault("colors", [])
        if color_hex in colors:
            colors.remove(color_hex)
            save_font_metadata()
            self._rebuild_color_chips(colors)
            self._update_preview()
            self._refresh_font_list()

    def _update_preview(self):
        while self.preview_area.count():
            item = self.preview_area.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        info = self._get_selected_font_info()
        if not info:
            return
        family = info.get("family")
        tag = FONT_METADATA.get("font_tags", {}).get(family, {})
        colors = tag.get("colors") or ["#ffffff"]
        sample = self.preview_text.text().strip() or "示例文本 Abc 123"
        for col in colors:
            lbl = QLabel(sample)
            from cutora.ui.theme.apply import apply_swatch
            apply_swatch(lbl, col)
            lbl.setFont(QFont(render_font_family(family), 16))
            self.preview_area.addWidget(lbl)

    def _on_new_folder(self):
        base = self._get_selected_folder()
        name, ok = QInputDialog.getText(self, "新建文件夹", "请输入文件夹名称:")
        if not ok or not name.strip():
            return
        new_path = os.path.join(base, name.strip())
        try:
            os.makedirs(new_path, exist_ok=False)
        except Exception as e:
            QMessageBox.warning(self, "错误", f"创建失败: {e}")
            return
        self._reload_fonts_ui()

    def _on_rename_folder(self):
        folder = self._get_selected_folder()
        if os.path.abspath(folder) == str(FONT_ROOT.resolve()):
            QMessageBox.information(self, "提示", "根目录不可重命名")
            return
        base = os.path.dirname(folder)
        old_name = os.path.basename(folder)
        new_name, ok = QInputDialog.getText(self, "重命名文件夹", "请输入新名称:", text=old_name)
        if not ok or not new_name.strip():
            return
        new_path = os.path.join(base, new_name.strip())
        try:
            os.rename(folder, new_path)
        except Exception as e:
            QMessageBox.warning(self, "错误", f"重命名失败: {e}")
            return
        self._reload_fonts_ui()

    def _on_delete_folder(self):
        folder = self._get_selected_folder()
        if os.path.abspath(folder) == str(FONT_ROOT.resolve()):
            QMessageBox.information(self, "提示", "根目录不可删除")
            return
        resp = QMessageBox.question(self, "确认删除", "删除文件夹将移除其下所有字体文件，是否继续？")
        if resp != QMessageBox.StandardButton.Yes:
            return
        try:
            shutil.rmtree(folder)
        except Exception as e:
            QMessageBox.warning(self, "错误", f"删除失败: {e}")
            return
        self._reload_fonts_ui()

    def _on_move_font(self):
        info = self._get_selected_font_info()
        if not info:
            return
        src_path = info.get("path")
        if not src_path:
            return
        folders = []
        for root, dirs, _files in os.walk(FONT_DIR):
            for d in dirs:
                folders.append(os.path.join(root, d))
        folders = sorted(set(folders))
        if not folders:
            QMessageBox.information(self, "提示", "未找到可移动目标文件夹")
            return
        target, ok = QInputDialog.getItem(self, "移动字体", "选择目标文件夹:", folders, 0, False)
        if not ok or not target:
            return
        try:
            shutil.move(src_path, os.path.join(target, os.path.basename(src_path)))
        except Exception as e:
            QMessageBox.warning(self, "错误", f"移动失败: {e}")
            return
        self._reload_fonts_ui()

    def _reload_fonts_ui(self):
        load_embedded_fonts()
        if callable(self.refresh_callback):
            self.refresh_callback()
        self._build_folder_tree()
        self._refresh_font_list()
