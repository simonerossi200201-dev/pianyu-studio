# -*- coding: utf-8 -*-
"""悬浮图标：窗口全部最小化之后，Orante 融成屏幕上一个始终置顶的小图标。

一个进程一张图标，多开几个窗口也只有这一张（靠 data/floaticon.lock 抢，心跳过期才换人）。
它自己不认识任务、不碰数据库里的任务数据，只做四件事：

  1. 盯着 winlist 里的窗口：全都最小化了 → 浮出来；任意一个被唤醒 → 自己藏起来。
  2. 双击 → 把所有窗口一起呼出来（多开的也一起）。
  3. 右键 → 呼出全部 / 单独呼出某个 / 改大小 / 贴边隐藏开关 / 退出 Orante。
  4. 拖动摆位置、滚轮缩放，位置和大小记在设置里，下次还在那儿。

为什么用 tkinter：Python 自带，不引入任何依赖、不产生任何二进制文件。
64/65 号规格已经为了杀毒软件把编译产物清干净了，这里不能再带回来。

画法：Windows 上优先用分层窗口（UpdateLayeredWindow）逐像素透明，PNG 的羽化边是干净的；
失败就退回「抠背景色」（-transparentcolor），边上会有一点点毛刺，但一定能显示。

单独跑：pythonw -m orante.floaticon
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from . import winlist

DEFAULT_SIZE = 96
MIN_SIZE = 40
MAX_SIZE = 256
SIZE_STEP = 8
EDGE_MARGIN = 24        # 离屏幕边这么近就算贴边
BADGE_FRAC = 0.34       # 贴边收起来之后缩成多大（占正常大小的比例）
BADGE_MIN = 28          # 徽标再小也不能小过这个，不然点不着
POLL_MS = 400           # 多久看一眼窗口状态
LOCK_BEAT = 5.0         # 图标进程自己的心跳
LOCK_STALE = 15.0       # 心跳过期 = 上一个图标进程没了
IDLE_QUIT = 20.0        # 一个窗口都没有，撑这么久就自己退出
KEY_COLOR = "#ff00fe"   # 退回抠色方案时当透明用的颜色（图里不会有这个色）


# ---------------------------------------------------------------- 纯逻辑
# 下面这几个不碰 tkinter / Win32，Linux 上也能跑测试。

def icon_file() -> Path:
    """用户自己那张 "Orante me.png" 优先；没有就退回程序图标。"""
    from . import paths
    cand = [
        paths.ROOT / "Orante me.png",
        paths.UI / "assets" / "png" / "icon-256.png",
        paths.UI / "assets" / "png" / "icon-128.png",
    ]
    for p in cand:
        if p.is_file():
            return p
    return cand[0]


def clamp_size(n) -> int:
    try:
        n = int(float(n))
    except (TypeError, ValueError):
        n = DEFAULT_SIZE
    return max(MIN_SIZE, min(MAX_SIZE, n))


def parse_pos(raw) -> tuple[int, int] | None:
    """设置里存的 "x,y"。存坏了当没存过。"""
    if not raw:
        return None
    try:
        x, y = str(raw).split(",")[:2]
        return int(float(x)), int(float(y))
    except (TypeError, ValueError):
        return None


def default_pos(size: int, sw: int, sh: int) -> tuple[int, int]:
    """第一次出现的位置：右下角，离边一点点。"""
    return max(0, sw - size - 40), max(0, sh - size - 120)


def fit_on_screen(x: int, y: int, size: int, sw: int, sh: int) -> tuple[int, int]:
    """换了显示器 / 分辨率变小，别让图标跑到屏幕外面找不回来。"""
    x = max(-size // 2, min(int(x), sw - size // 2))
    y = max(0, min(int(y), sh - size // 2))
    return int(x), int(y)


def dock_side(x: int, y: int, size: int, sw: int, sh: int,
              margin: int = EDGE_MARGIN) -> str:
    """松手的地方离哪条边最近且够近 → 往那边贴。都不近就返回空。"""
    left, right = x, sw - (x + size)
    top, bottom = y, sh - (y + size)
    best, side = None, ""
    for name, gap in (("left", left), ("right", right), ("top", top), ("bottom", bottom)):
        if gap <= margin and (best is None or gap < best):
            best, side = gap, name
    return side


def badge_size(size: int) -> int:
    """贴边收起来之后显示多大。

    0924 踩过的坑：原来是把整张图推出屏幕、只留 12 像素。图标是圆的，
    最边上那 12 列几乎全是透明像素——分层窗口的透明处鼠标是穿透的，
    于是图标既看不见也点不着，图放得越大越明显（用户把它放到最大之后"再也找不回来了"）。
    现在改成缩成一个小徽标整个贴在边上：一定看得见、一定点得着。
    """
    return max(BADGE_MIN, min(int(size), int(round(int(size) * BADGE_FRAC))))


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(int(v), max(lo, hi)))


def badge_pos(x: int, y: int, size: int, badge: int, sw: int, sh: int,
              side: str) -> tuple[int, int]:
    """徽标紧贴那条边；另一个方向跟着原来的中心走，整块都在屏幕里。"""
    cx, cy = int(x) + int(size) // 2, int(y) + int(size) // 2
    if side == "left":
        return 0, _clamp(cy - badge // 2, 0, sh - badge)
    if side == "right":
        return sw - badge, _clamp(cy - badge // 2, 0, sh - badge)
    if side == "top":
        return _clamp(cx - badge // 2, 0, sw - badge), 0
    if side == "bottom":
        return _clamp(cx - badge // 2, 0, sw - badge), sh - badge
    return int(x), int(y)


def pull_in(x: int, y: int, size: int, sw: int, sh: int) -> tuple[int, int]:
    """鼠标碰上来要看全貌：整张图都拉进屏幕里。"""
    return _clamp(x, 0, sw - size), _clamp(y, 0, sh - size)


def should_show(states: list[dict]) -> bool:
    """states 是每个活着的窗口一条 {"hwnd", "minimized"}。

    有窗口、而且全都最小化了 → 图标浮出来。句柄还不知道（hwnd=0，比如浏览器模式）
    的窗口按「开着」算，免得把图标叫出来挡着人。
    """
    if not states:
        return False
    for st in states:
        if not int(st.get("hwnd") or 0):
            return False
        if not st.get("minimized"):
            return False
    return True


# ---------------------------------------------------------------- 单实例
def lock_file() -> Path:
    from . import paths
    return paths.data_dir() / "floaticon.lock"


def lock_read() -> dict:
    try:
        return json.loads(lock_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def running(*, now: float | None = None, stale: float = LOCK_STALE) -> bool:
    """已经有一个图标进程在跑（心跳还新鲜）。"""
    rec = lock_read()
    if not rec:
        return False
    now = time.time() if now is None else float(now)
    if now - float(rec.get("beat") or 0) > stale:
        return False
    pid = int(rec.get("pid") or 0)
    return pid > 0 and pid != os.getpid()


def lock_beat() -> None:
    fp = lock_file()
    try:
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(json.dumps({"pid": os.getpid(), "beat": time.time()}),
                      encoding="utf-8")
    except OSError:
        pass


def lock_clear() -> None:
    rec = lock_read()
    if int(rec.get("pid") or 0) == os.getpid():
        try:
            lock_file().unlink()
        except OSError:
            pass


# ---------------------------------------------------------------- Win32
def _u32():
    import ctypes
    return ctypes.windll.user32


def is_minimized(hwnd: int) -> bool:
    """最小化，或者干脆没显示（被藏起来的也算「不在眼前」）。"""
    if not hwnd:
        return False
    try:
        u = _u32()
        if not u.IsWindow(int(hwnd)):
            return False
        return bool(u.IsIconic(int(hwnd))) or not bool(u.IsWindowVisible(int(hwnd)))
    except Exception:  # noqa: BLE001
        return False


def window_alive(hwnd: int) -> bool:
    try:
        return bool(hwnd) and bool(_u32().IsWindow(int(hwnd)))
    except Exception:  # noqa: BLE001
        return False


def restore_window(hwnd: int) -> None:
    """叫醒一个窗口：还原 → 提到最前 → 抢焦点。"""
    if not hwnd:
        return
    try:
        u = _u32()
        hwnd = int(hwnd)
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, 9)        # SW_RESTORE
        else:
            u.ShowWindow(hwnd, 5)        # SW_SHOW
        u.BringWindowToTop(hwnd)
        u.SetForegroundWindow(hwnd)
    except Exception:  # noqa: BLE001
        pass


def close_window(hwnd: int) -> None:
    """请窗口自己关（WM_CLOSE），不杀进程——正在出片的任务要走自己的收尾。"""
    try:
        _u32().PostMessageW(int(hwnd), 0x0010, 0, 0)
    except Exception:  # noqa: BLE001
        pass


def _dpi_aware() -> None:
    import ctypes
    try:    # Win10 1703+：每显示器 v2，拖到不同缩放的屏幕上不糊
        ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
        return
    except Exception:  # noqa: BLE001
        pass
    for fn in ("shcore.SetProcessDpiAwareness", "user32.SetProcessDPIAware"):
        mod, name = fn.split(".")
        try:
            f = getattr(getattr(ctypes.windll, mod), name)
            f(2) if name.endswith("Awareness") else f()
            return
        except Exception:  # noqa: BLE001
            continue


def _bgra_premul(img) -> bytes:
    """PIL 的 RGBA → 预乘 alpha 的 BGRA。UpdateLayeredWindow 只认这个排法。

    走 PIL 的通道运算（C 实现）。滚轮连滚时一秒要画十几帧，
    纯 Python 逐像素乘在 256×256 上要几十毫秒，滚起来会顿。
    """
    try:
        from PIL import Image, ImageChops
        r, g, b, a = img.convert("RGBA").split()
        r = ImageChops.multiply(r, a)      # multiply 正好是 x*a/255
        g = ImageChops.multiply(g, a)
        b = ImageChops.multiply(b, a)
        return Image.merge("RGBA", (b, g, r, a)).tobytes("raw", "RGBA")
    except Exception:  # noqa: BLE001  兜底：慢一点也要画出来
        data = img.tobytes("raw", "RGBA")
        out = bytearray(len(data))
        for i in range(0, len(data), 4):
            r_, g_, b_, a_ = data[i], data[i + 1], data[i + 2], data[i + 3]
            out[i] = b_ * a_ // 255
            out[i + 1] = g_ * a_ // 255
            out[i + 2] = r_ * a_ // 255
            out[i + 3] = a_
        return bytes(out)


def paint_layered(hwnd: int, img) -> bool:
    """把整张带透明的图直接贴到窗口上（逐像素 alpha）。成功返回 True。"""
    import ctypes
    from ctypes import wintypes

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    class BLENDFUNCTION(ctypes.Structure):
        _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                    ("SourceConstantAlpha", ctypes.c_byte), ("AlphaFormat", ctypes.c_byte)]

    u32, gdi = ctypes.windll.user32, ctypes.windll.gdi32
    w, h = img.size
    hdc_screen = gdi_mem = hbmp = old = None
    try:
        ex = u32.GetWindowLongW(hwnd, -20)          # GWL_EXSTYLE
        u32.SetWindowLongW(hwnd, -20, ex | 0x00080000)   # WS_EX_LAYERED
        hdc_screen = u32.GetDC(0)
        gdi_mem = gdi.CreateCompatibleDC(hdc_screen)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h                 # 负 = 自上而下
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0             # BI_RGB
        bits = ctypes.c_void_p()
        hbmp = gdi.CreateDIBSection(gdi_mem, ctypes.byref(bmi), 0,
                                    ctypes.byref(bits), None, 0)
        if not hbmp:
            return False
        buf = _bgra_premul(img)
        ctypes.memmove(bits, buf, len(buf))
        old = gdi.SelectObject(gdi_mem, hbmp)
        size = wintypes.SIZE(w, h)
        src = wintypes.POINT(0, 0)
        blend = BLENDFUNCTION(0, 0, 255, 1)         # AC_SRC_OVER / AC_SRC_ALPHA
        ok = u32.UpdateLayeredWindow(
            hwnd, hdc_screen, None, ctypes.byref(size), gdi_mem,
            ctypes.byref(src), 0, ctypes.byref(blend), 2)   # ULW_ALPHA
        return bool(ok)
    except Exception:  # noqa: BLE001
        return False
    finally:
        try:
            if gdi_mem and old:
                gdi.SelectObject(gdi_mem, old)
            if hbmp:
                gdi.DeleteObject(hbmp)
            if gdi_mem:
                gdi.DeleteDC(gdi_mem)
            if hdc_screen:
                u32.ReleaseDC(0, hdc_screen)
        except Exception:  # noqa: BLE001
            pass


_SRC_CACHE: dict = {}


def load_icon(size: int):
    """读图、等比缩到 size×size（不裁、不拉伸，居中放在方块里）。

    原图解码一次就够了（那张 PNG 有 1.5 MB），缩放才是每次要做的。
    """
    from PIL import Image
    key = str(icon_file())
    img = _SRC_CACHE.get(key)
    if img is None:
        img = Image.open(key).convert("RGBA")
        _SRC_CACHE.clear()
        _SRC_CACHE[key] = img
    w, h = img.size
    scale = min(size / max(1, w), size / max(1, h))
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    img = img.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(img, ((size - nw) // 2, (size - nh) // 2), img)
    return canvas


def flatten_on_key(img, key=(255, 0, 254)):
    """退回抠色方案时用：半透明的边压到抠除色上，全透明的直接填抠除色。"""
    from PIL import Image
    bg = Image.new("RGBA", img.size, key + (255,))
    return Image.alpha_composite(bg, img).convert("RGB")


def cfg(key: str, default=None):
    try:
        from . import config
        val = config.get(key, default)
        return default if val is None else val
    except Exception:  # noqa: BLE001
        return default


def cfg_set(key: str, value) -> None:
    try:
        from . import config
        config.set(key, value)
    except Exception:  # noqa: BLE001
        pass


class Dock:
    """图标本体。只在 Windows 上真正有意义（其他系统退回抠色方案，能跑但没测过）。"""

    def __init__(self, demo: bool = False):
        import tkinter as tk
        _dpi_aware()
        self.demo = bool(demo)      # 自测模式：不管窗口状态，一直显示
        self.tk = tk
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Orante 悬浮图标")
        self.root.overrideredirect(True)          # 无边框、不进任务栏、不进 Alt+Tab
        self.root.attributes("-topmost", True)
        self.sw = int(self.root.winfo_screenwidth())
        self.sh = int(self.root.winfo_screenheight())

        self.size = clamp_size(cfg("ui.float_icon.size", DEFAULT_SIZE))
        self.edge_hide = bool(cfg("ui.float_icon.edge_hide", True))
        pos = parse_pos(cfg("ui.float_icon.pos", "")) or default_pos(self.size, self.sw, self.sh)
        self.x, self.y = fit_on_screen(pos[0], pos[1], self.size, self.sw, self.sh)
        self.side = ""            # 现在贴在哪条边（空 = 没贴）
        self.peeking = False      # 贴边后鼠标碰上来，暂时整个露出来
        self.shown = False
        self.layered = False
        self._img = None
        self._img_cache: dict = {}
        self._keyed_warned = False
        self._size_saved = self.size    # 最后一次由我们自己写进设置的大小
        self._last_geom = None          # 上一次真的画到屏幕上的 (大小, x, y)
        self._photo = None
        self._drag = None
        self._moved = False
        self._empty_since = None
        self._last_beat = 0.0
        self._last_cfg = 0.0
        self._last_top = 0.0
        self._peek_job = None

        self.label = tk.Label(self.root, bd=0, highlightthickness=0,
                              bg=KEY_COLOR, cursor="hand2")
        self.label.pack(fill="both", expand=True)
        for w in (self.root, self.label):
            w.bind("<Button-1>", self._on_press)
            w.bind("<B1-Motion>", self._on_drag)
            w.bind("<ButtonRelease-1>", self._on_release)
            w.bind("<Double-Button-1>", self._on_double)
            w.bind("<Button-3>", self._on_menu)
            w.bind("<MouseWheel>", self._on_wheel)
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)
        self._sync_side(save=False)
        self._paint()

    # ---- 画 ----
    def _display(self) -> tuple[int, int, int]:
        """这一刻该显示多大、摆哪儿。贴边收起来时是个小徽标，别的时候是整张图。"""
        if self.edge_hide and self.side and not self.peeking and self._drag is None:
            badge = badge_size(self.size)
            bx, by = badge_pos(self.x, self.y, self.size, badge, self.sw, self.sh, self.side)
            return badge, bx, by
        x, y = pull_in(self.x, self.y, self.size, self.sw, self.sh) if self.peeking \
            else (self.x, self.y)
        return self.size, int(x), int(y)

    def _img_for(self, size: int):
        """按尺寸出图，带一级缓存（滚轮连滚时不重复解码大图）。"""
        if self._img_cache.get(size) is None:
            try:
                self._img_cache = {size: load_icon(size)}   # 只留当前这一张，省内存
            except Exception as e:  # noqa: BLE001
                _log(f"读图标失败：{e}")
                self._img_cache = {size: None}
        return self._img_cache.get(size)

    def _paint(self) -> None:
        """摆位置 + 画内容。这是唯一一处改窗口几何的地方。"""
        size, x, y = self._display()
        self._last_geom = (size, x, y)
        img = self._img_for(size)
        self.root.geometry(f"{size}x{size}+{int(x)}+{int(y)}")
        self.root.update_idletasks()
        self._img = img
        ok = False
        if img is not None and os.name == "nt":
            try:
                ok = paint_layered(self.root.winfo_id(), img)
            except Exception as e:  # noqa: BLE001
                _log(f"分层窗口没画成，改用抠色：{e}")
                ok = False
        self.layered = ok
        if ok:
            self.label.configure(image="", bg=KEY_COLOR)   # 分层窗口自己画，Label 不出场
            return
        if img is None:
            return
        try:
            from PIL import ImageTk
            self.root.attributes("-transparentcolor", KEY_COLOR)
            self._photo = ImageTk.PhotoImage(flatten_on_key(img))
            self.label.configure(image=self._photo, bg=KEY_COLOR)
        except Exception as e:  # noqa: BLE001
            if not self._keyed_warned:      # 每次重画都喊一遍就把日志刷爆了
                self._keyed_warned = True
                _log(f"抠色方案也没画成：{e}")

    # ---- 显隐 ----
    def show(self) -> None:
        if self.shown:
            return
        self.shown = True
        self._sync_side(save=False)
        self._paint()
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        self._paint()          # deiconify 之后再贴一次，分层内容才稳

    def hide(self) -> None:
        if not self.shown:
            return
        self.shown = False
        self.root.withdraw()

    def _stay_on_top(self) -> None:
        """有些全屏程序会把别人压下去，定时重新宣告一次置顶。"""
        try:
            self.root.attributes("-topmost", True)
            if os.name == "nt":
                # HWND_TOPMOST=-1，不动位置不改大小不抢焦点
                _u32().SetWindowPos(self.root.winfo_id(), -1, 0, 0, 0, 0, 0x0013)
        except Exception:  # noqa: BLE001
            pass

    # ---- 贴边 ----
    def _sync_side(self, *, save: bool = True) -> None:
        """按当前位置重算贴在哪条边（不画）。"""
        self.side = (dock_side(self.x, self.y, self.size, self.sw, self.sh)
                     if self.edge_hide else "")
        if save:
            self._save_pos()

    def _peek_in(self) -> None:
        if not self.side or self.peeking:
            return
        self.peeking = True
        self._paint()

    def _peek_out(self) -> None:
        if not self.peeking:
            return
        self.peeking = False
        self._paint()

    def _save_pos(self) -> None:
        cfg_set("ui.float_icon.pos", f"{int(self.x)},{int(self.y)}")

    def _pointer_over(self) -> bool:
        """鼠标是不是压在图标上。窗口大小一变，tk 的 Enter/Leave 会漏，靠这个兜。"""
        try:
            px, py = self.root.winfo_pointerx(), self.root.winfo_pointery()
            x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
            w, h = self.root.winfo_width(), self.root.winfo_height()
            return x <= px < x + w and y <= py < y + h
        except Exception:  # noqa: BLE001
            return False

    def _sync_peek(self) -> None:
        """每一轮对一次账：该收就收、该展开就展开，屏幕上和算出来的不一致就重画。

        不靠 Enter/Leave 事件——窗口一改大小位置，那两个事件经常就丢了；
        也不怕哪条路忘了重画：这里每 400 毫秒兜一次底。
        """
        if not self.shown or self._drag is not None:
            return
        if self.edge_hide:
            over = self._pointer_over()
            if self.peeking and not over:
                self.peeking = False
            elif not self.peeking and over and self.side:
                self.peeking = True
        else:
            self.peeking = False
        if self._display() != self._last_geom:
            self._paint()

    # ---- 交互 ----
    def _guard(fn):          # noqa: N805
        """回调里出错只记日志。别让一次异常把图标留在看不见的状态里。"""
        def wrapper(self, *a, **k):
            try:
                return fn(self, *a, **k)
            except Exception as e:  # noqa: BLE001
                import traceback
                _log(f"{fn.__name__} 出错：{e}")
                _log(traceback.format_exc())
                try:
                    self.peeking = False
                    self._sync_side(save=False)
                    self._paint()
                except Exception:  # noqa: BLE001
                    pass
        wrapper.__name__ = fn.__name__
        return wrapper

    @_guard
    def _on_enter(self, _e=None) -> None:
        if self._peek_job is not None:
            try:
                self.root.after_cancel(self._peek_job)
            except Exception:  # noqa: BLE001
                pass
            self._peek_job = None
        self._peek_in()

    @_guard
    def _on_leave(self, _e=None) -> None:
        if not self.side or self._drag is not None:
            return
        self._peek_job = self.root.after(700, self._peek_out)

    @_guard
    def _on_press(self, e) -> None:
        self._drag = (e.x_root - self.x, e.y_root - self.y)
        self._moved = False

    @_guard
    def _on_drag(self, e) -> None:
        if self._drag is None:
            return
        ox, oy = self._drag
        nx, ny = e.x_root - ox, e.y_root - oy
        if abs(nx - self.x) + abs(ny - self.y) >= 3:
            self._moved = True
        self.x, self.y = fit_on_screen(nx, ny, self.size, self.sw, self.sh)
        self._paint()

    @_guard
    def _on_release(self, _e=None) -> None:
        if self._drag is None:
            return
        self._drag = None
        if self._moved:
            # 松手的地方决定贴不贴边；鼠标还在图标上，所以先维持整张图（peek）
            self._sync_side(save=True)
            self.peeking = bool(self.side)
            self._paint()

    @_guard
    def _on_double(self, _e=None) -> None:
        self.summon_all()

    @_guard
    def _on_wheel(self, e) -> None:
        step = SIZE_STEP if getattr(e, "delta", 0) > 0 else -SIZE_STEP
        self.set_size(self.size + step)

    @_guard
    def _on_menu(self, e) -> None:
        tk = self.tk
        menu = tk.Menu(self.root, tearoff=0)
        recs = winlist.live()
        menu.add_command(label=f"呼出全部窗口（{len(recs)}）", command=self.summon_all)
        if len(recs) > 1:
            sub = tk.Menu(menu, tearoff=0)
            for r in recs:
                title = r.get("title") or f":{r.get('port')}"
                sub.add_command(label=title,
                                command=lambda h=r.get("hwnd"): self.summon_one(h))
            menu.add_cascade(label="单独呼出", menu=sub)
        menu.add_separator()
        sizes = tk.Menu(menu, tearoff=0)
        for lab, n in (("小 64", 64), ("中 96", 96), ("大 128", 128), ("特大 160", 160)):
            mark = "✓ " if self.size == n else "　"
            sizes.add_command(label=mark + lab, command=lambda n=n: self.set_size(n))
        menu.add_cascade(label="图标大小", menu=sizes)
        menu.add_command(label=("✓ " if self.edge_hide else "　") + "贴边隐藏",
                         command=self.toggle_edge_hide)
        menu.add_separator()
        menu.add_command(label="隐藏图标（本次）", command=self.hide)
        menu.add_command(label="不再使用悬浮图标…", command=self.turn_off)
        menu.add_command(label="退出 Orante（关掉所有窗口）", command=self.quit_orante)
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    # ---- 动作 ----
    def set_size(self, n: int, *, at_pointer: bool = True, save: bool = True) -> None:
        """改大小。

        at_pointer=True 是滚轮 / 右键菜单改的——鼠标就压在图标上，先按整张图显示，
        等鼠标离开再收成徽标。设置面板改的（at_pointer=False）不许碰 peeking，
        否则每次跟随设置都会把贴好边的图标重新撑开（0924 的「贴边不收起」就是这么来的）。
        """
        n = clamp_size(n)
        if n == self.size:
            return
        self.size = n
        self._size_saved = n
        if save:
            cfg_set("ui.float_icon.size", n)
        self.x, self.y = fit_on_screen(self.x, self.y, self.size, self.sw, self.sh)
        self._sync_side(save=save)
        if at_pointer and self.side:
            self.peeking = True
        self._paint()

    def toggle_edge_hide(self) -> None:
        self.edge_hide = not self.edge_hide
        cfg_set("ui.float_icon.edge_hide", self.edge_hide)
        self.peeking = False
        self._sync_side(save=True)
        self._paint()

    def summon_all(self) -> None:
        recs = winlist.live()
        for r in recs:
            restore_window(int(r.get("hwnd") or 0))
        self.hide()

    def summon_one(self, hwnd) -> None:
        restore_window(int(hwnd or 0))
        self.hide()

    def turn_off(self) -> None:
        """用户不想要这个图标了：关掉总开关并退出。设置页可以再打开。"""
        from tkinter import messagebox
        if not messagebox.askyesno(
                "关掉悬浮图标",
                "以后窗口最小化就不再显示这个图标了。\n"
                "想再打开：Orante → 设置 → 界面 → 勾上「悬浮图标」。"):
            return
        cfg_set("ui.float_icon", False)
        self.quit()

    def quit_orante(self) -> None:
        from tkinter import messagebox
        recs = winlist.live()
        if not messagebox.askyesno(
                "退出 Orante", f"要关掉全部 {len(recs)} 个 Orante 窗口吗？\n"
                "正在生成的任务会按各自的方式收尾。"):
            return
        for r in recs:
            close_window(int(r.get("hwnd") or 0))
        self.root.after(1500, self.quit)

    def quit(self) -> None:
        lock_clear()
        try:
            self.root.destroy()
        except Exception:  # noqa: BLE001
            pass

    # ---- 心跳 ----
    def tick(self) -> None:
        """每一轮都兜住异常：循环一旦断掉，图标就再也不出现了。"""
        try:
            if self._tick_once() is False:
                return
        except Exception as e:  # noqa: BLE001
            import traceback
            _log(f"这一轮出错（不影响继续跑）：{e}")
            _log(traceback.format_exc())
        self.root.after(POLL_MS, self.tick)

    def _tick_once(self):
        now = time.time()
        if now - self._last_beat >= LOCK_BEAT:
            self._last_beat = now
            lock_beat()
        if now - self._last_cfg >= 2.0:
            self._last_cfg = now
            if not bool(cfg("ui.float_icon", True)):
                self.quit()
                return False
            size = clamp_size(cfg("ui.float_icon.size", self.size))
            # 只有设置面板那边真的改了（和我们最后写进去的不一样）才跟；
            # 写配置万一失败也不能反过来把用户滚出来的大小顶回去
            if size != self.size and size != self._size_saved:
                self.set_size(size, at_pointer=False, save=False)
                self._size_saved = size
            eh = bool(cfg("ui.float_icon.edge_hide", self.edge_hide))
            if eh != self.edge_hide:
                self.edge_hide = eh
                self.peeking = False
                self._sync_side(save=False)
                self._paint()

        if self.demo:
            self.show()
            self._sync_peek()
            return True
        recs = winlist.live()
        recs = [r for r in recs if not int(r.get("hwnd") or 0) or window_alive(r.get("hwnd"))]
        if not recs:
            self._empty_since = self._empty_since or now
            self.hide()
            if now - self._empty_since >= IDLE_QUIT:
                self.quit()
                return False
        else:
            self._empty_since = None
            states = [{"hwnd": int(r.get("hwnd") or 0),
                       "minimized": is_minimized(int(r.get("hwnd") or 0))} for r in recs]
            if should_show(states):
                self.show()
            else:
                self.hide()
        self._sync_peek()
        if self.shown and now - self._last_top >= 3.0:
            self._last_top = now
            self._stay_on_top()
        return True

    def run(self) -> None:
        lock_beat()
        self.root.after(POLL_MS, self.tick)
        self.root.mainloop()
        lock_clear()


def spawn(force: bool = False) -> bool:
    """主程序调：需要的话把图标进程拉起来。已经有一个在跑就什么都不做。"""
    if os.name != "nt":
        return False
    if not force and not bool(cfg("ui.float_icon", True)):
        return False
    if running():
        return False
    import subprocess
    from . import paths
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    cmd = [str(pyw if pyw.is_file() else exe), "-m", "orante.floaticon"]
    flags = 0x00000008 | 0x08000000       # DETACHED_PROCESS | CREATE_NO_WINDOW
    try:
        subprocess.Popen(cmd, cwd=str(paths.ROOT), creationflags=flags,
                         close_fds=True)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"悬浮图标没拉起来：{e}", flush=True)
        return False


def log_path() -> Path:
    from . import paths
    return paths.logs_dir() / "floaticon.log"


def _log(msg: str) -> None:
    """图标是个没有控制台的独立进程，出事只能往日志里写。"""
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + str(msg)
    print(line, flush=True)
    try:
        fp = log_path()
        fp.parent.mkdir(parents=True, exist_ok=True)
        with fp.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    demo = "--demo" in args        # 自测：不等窗口最小化，直接把图标显示出来
    if not demo and running():
        return 0
    try:
        import tkinter  # noqa: F401
    except Exception as e:  # noqa: BLE001
        _log(f"这套 Python 没带 tkinter，悬浮图标起不来：{e}")
        _log("解决：用 python.org 的安装包重装 Python（默认就带 tcl/tk），再删掉 .venv 重新安装依赖。")
        return 1
    try:
        Dock(demo=demo).run()
    except Exception as e:  # noqa: BLE001
        import traceback
        _log(f"悬浮图标退出了：{e}")
        _log(traceback.format_exc())
        lock_clear()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
