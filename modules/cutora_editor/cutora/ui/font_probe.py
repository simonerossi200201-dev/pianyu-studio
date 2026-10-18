# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q170 · 字体体检：在**单独的进程**里把每个内置字体实际渲染一遍，找出在这台电脑上会卡死 / 崩溃 / 特别慢的字体。

为什么要单独进程：某些字体（彩色 COLRv1、十几个可变轴的字体）在 Windows 的 DirectWrite 上渲染可能
一卡就是几分钟，卡在 C++ 里，Python 没法打断。在主程序里试 = 主程序卡死；在子进程里试，卡住了就杀掉
子进程、记下这个字体、从下一个接着试，主程序完全不受影响。

* 子进程：``python -m cutora.ui.font_probe --child <进度文件> <待测清单.json>``，每个字体先写 ``S\\t序号\\t家族``，
  渲染完写 ``D\\t序号\\t家族\\t毫秒``。渲染走和画布 / 导出同一条路：普通 drawText、QPainterPath.addText、
  完整的文本覆层（textfx：背景框、强调词、按框缩小）。
* 父进程（后台线程）：盯着进度文件；某个字体开始后 ``HANG_S`` 秒还没写完 = 卡死 → 杀掉子进程、标记、从下一个继续；
  子进程中途退出 = 崩溃 → 标记、继续。结果按（字体文件 + Qt 版本）的签名缓存，字体不变就不再重测。
* 结果：``ok`` / ``slow``（≥ ``SLOW_MS``）/ ``hang`` / ``crash``。``hang`` / ``crash`` 的字体：
  字体选择器里停用并说明原因；老工程里用了它 → 渲染时暂用默认字体代替（日志 + 状态栏说明），绝不卡死。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time

HANG_S = 12.0
SLOW_MS = 1500.0
STARTUP_S = 90
SAMPLE = "When God is leading 字幕 AaBbGg 123"
VERSION = 1

_STATE = {"probe": None}


# ───────────────────────────── 子进程 ─────────────────────────────

def _child_main(progress_path: str, todo_path: str) -> int:
    os.environ.setdefault("CUTORA_FONT_PROBE_CHILD", "1")
    if hasattr(os, "nice"):
        try:
            os.nice(10)
        except OSError:
            pass
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["cutora-font-probe"])
    _ = app
    from cutora.ui.panels import fonts as F
    F.load_embedded_fonts()
    try:
        with open(todo_path, encoding="utf-8") as fh:
            fams = [str(f) for f in json.load(fh)]
    except Exception:
        fams = probe_order()
    from cutora.model.factory_defaults import overlay_defaults
    from cutora.render.frame_renderer import FrameRenderer
    ov = overlay_defaults("text", canvas_w=1080, canvas_h=1920)
    ov.update({"content": SAMPLE, "start": 0.0, "end": 5.0, "fontsize": 54, "fit_mode": "shrink",
               "x": 23, "y": 821, "w": 1033, "h": 321})
    fx = ov.setdefault("fx", {})
    fx["bg"] = {"enabled": True, "type": "box", "color": "#00557F", "alpha": 0.55, "texture": "paper",
                "pad_em": [0.5, 0.25], "radius_em": 0.4}
    fx["emphasis"] = {"words": ["God"], "colors": ["#FFFF00"], "color": "#FFFF00", "scale": 1.25,
                      "type": "solid", "font": "emphasis"}
    task = {"text_overlays": [ov], "image_overlays": [], "segments": [], "style": {}, "dims": (1080, 1920)}
    with open(progress_path, "a", encoding="utf-8") as out:
        for i in range(len(fams)):
            fam = fams[i]
            out.write("S\t%d\t%s\n" % (i, fam))
            out.flush()
            t0 = time.perf_counter()
            try:
                font = F.get_font_manager().get_font(fam, 72)
                img = QImage(1400, 220, QImage.Format.Format_ARGB32_Premultiplied)
                img.fill(0)
                p = QPainter(img)
                try:
                    p.setFont(font)
                    p.setPen(QColor("#FFFFFF"))
                    p.drawText(QRectF(0, 0, 1400, 220), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                               SAMPLE)
                    path = QPainterPath()
                    path.addText(10, 150, F.get_font_manager().get_font(fam, 68), SAMPLE)
                    p.fillPath(path, QColor("#FFFFFF"))
                    p.setFont(F.get_font_manager().get_font(fam, 15))   # 字体列表里那一行的大小
                    p.drawText(QRectF(0, 190, 600, 30), Qt.AlignmentFlag.AlignLeft, fam)
                finally:
                    p.end()
                ov["font_family"] = fam
                fx["fonts"] = {"body": fam, "emphasis": fam}
                frame = QImage(1080, 1920, QImage.Format.Format_ARGB32_Premultiplied)
                frame.fill(0)
                p = QPainter(frame)
                try:
                    FrameRenderer.draw_layers(p, task, 1080, 1920, 1.0, None, render_order=["text"])
                finally:
                    p.end()
                ms = (time.perf_counter() - t0) * 1000.0
                out.write("D\t%d\t%s\t%.1f\n" % (i, fam, ms))
            except Exception as exc:  # 渲染报错不算卡死，记下来
                out.write("E\t%d\t%s\t%s\n" % (i, fam, str(exc).replace("\t", " ").replace("\n", " ")[:200]))
            out.flush()
        out.write("END\n")
        out.flush()
    return 0


# ───────────────────────────── 风险字体 / 体检顺序 ─────────────────────────────

RISKY_AXES = 4


def risky_families(fams=None) -> list:
    """彩色字体（COLR / CBDT / sbix / SVG 表）或可变轴 ≥ ``RISKY_AXES`` 的字体：最可能在某台电脑上卡死的那几个。
    只读表目录（fontTools lazy），213 个字体约 0.1 秒。"""
    from cutora.ui.panels import fonts as F
    out = []
    for fam in (fams if fams is not None else F.ALLOWED_FONTS):
        for path in (F.FONT_FILES.get(fam) or [])[:1]:
            try:
                from fontTools.ttLib import TTFont
                t = TTFont(path, lazy=True, fontNumber=0)
                tags = set(t.keys())
                axes = len(t["fvar"].axes) if "fvar" in tags else 0
                t.close()
            except Exception:
                continue
            if tags & {"COLR", "CBDT", "sbix", "SVG "} or axes >= RISKY_AXES:
                out.append(fam)
    return out


def probe_order(fams=None) -> list:
    """体检顺序：风险字体排最前（它们查清之前暂不渲染），其余按原顺序。父子进程用同一个函数。"""
    from cutora.ui.panels import fonts as F
    fams = list(fams if fams is not None else F.ALLOWED_FONTS)
    risky = set(risky_families(fams))
    return [f for f in fams if f in risky] + [f for f in fams if f not in risky]


# ───────────────────────────── 父进程 ─────────────────────────────

def _cache_path() -> str:
    from cutora.core.paths import user_cache_dir
    d = os.path.join(user_cache_dir(), "font_probe")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "results.json")


def font_signature() -> str:
    """字体文件（路径 + 大小 + 修改时间）+ Qt 版本 + 体检版本。变了就重测。"""
    from PySide6 import __version__ as pyside_ver
    from cutora.ui.panels import fonts as F
    h = hashlib.sha1()
    h.update(("v%d|%s|" % (VERSION, pyside_ver)).encode())
    for fam in sorted(F.ALLOWED_FONTS):
        h.update(fam.encode("utf-8"))
        for path in (F.FONT_FILES.get(fam) or [])[:1]:
            try:
                st = os.stat(path)
                h.update(("|%s|%d|%d" % (os.path.basename(path), st.st_size, int(st.st_mtime))).encode("utf-8"))
            except OSError:
                pass
    return h.hexdigest()


class FontProbe:
    def __init__(self):
        self.results: dict = {}
        self.complete = False
        self.running = False
        self._lock = threading.Lock()
        self._thread = None
        self._sig = ""
        self.listeners = []           # callable() — 有新结果时（在后台线程里调，界面那边自己转到主线程）
        self._blocked = frozenset()   # get_font 每次都查 → 预先算好的不可变集合，查询不加锁
        self._gate = frozenset()      # 风险字体里还没体检的（体检完之前暂用默认字体渲染）
        self._risky = frozenset()
        self._order: list = []
        self._prepared = False
        self.gave_up = False

    # ---- 查询（任何线程）----
    def status(self, family: str) -> str:
        with self._lock:
            rec = self.results.get(str(family or ""))
        return str(rec.get("status")) if rec else ""

    def blocked(self) -> frozenset:
        return self._blocked

    def pending(self) -> frozenset:
        return self._gate

    def is_blocked(self, family: str) -> bool:
        """渲染时要不要换成默认字体：体检判定会卡死 / 崩溃的，或风险字体还没查到的。"""
        fam = str(family or "")
        return fam in self._blocked or fam in self._gate

    def health(self, family: str) -> str:
        """给界面看：``blocked`` / ``checking`` / ``slow`` / ``ok`` / ``""``（不知道）。"""
        fam = str(family or "")
        if fam in self._blocked:
            return "blocked"
        if fam in self._gate:
            return "checking"
        st = self.status(fam)
        return "slow" if st == "slow" else ("ok" if st else "")

    def _refresh_blocked(self) -> None:
        with self._lock:
            self._blocked = frozenset(f for f, r in self.results.items() if r.get("status") in ("hang", "crash"))
            if self.complete or self.gave_up:
                self._gate = frozenset()
            else:
                self._gate = frozenset(f for f in self._risky if f not in self.results)

    def slow(self) -> set:
        with self._lock:
            return {f for f, r in self.results.items() if r.get("status") == "slow"}

    # ---- 缓存 ----
    def _load(self) -> bool:
        try:
            with open(_cache_path(), encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            return False
        if data.get("sig") != self._sig:
            return False
        with self._lock:
            self.results = dict(data.get("results") or {})
            self.complete = bool(data.get("complete"))
        self._refresh_blocked()
        return True

    def _save(self) -> None:
        with self._lock:
            data = {"sig": self._sig, "complete": self.complete, "results": dict(self.results),
                    "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        try:
            tmp = _cache_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, _cache_path())
        except Exception:
            pass

    def _notify(self) -> None:
        self._refresh_blocked()
        for fn in list(self.listeners):
            try:
                fn()
            except Exception:
                pass

    # ---- 运行 ----
    def prepare(self) -> None:
        """界面线程上调一次（字体装好以后、打开工程以前）：读缓存；没测完就把风险字体先挡住。约 0.1 秒。"""
        if self._prepared:
            return
        self._prepared = True
        try:
            self._sig = font_signature()
        except Exception:
            self.gave_up = True
            return
        if self._load() and self.complete:
            return
        if getattr(sys, "frozen", False) or os.environ.get("CUTORA_FONT_PROBE_CHILD"):
            self.gave_up = True          # 打包成单个 exe 时没法 python -m 起子进程 → 不挡
            return
        try:
            self._order = probe_order()
            self._risky = frozenset(risky_families())
        except Exception:
            self._order, self._risky = [], frozenset()
        self._refresh_blocked()

    def start_async(self, delay_s: float = 3.0) -> None:
        if self._thread is not None:
            return
        self.prepare()
        if self.complete or self.gave_up:
            self._notify()
            return
        self._thread = threading.Thread(target=self._run, args=(delay_s,), name="cutora-font-probe", daemon=True)
        self._thread.start()

    def _give_up(self, log, why: str) -> None:
        """体检本身跑不起来：不再挡风险字体（不能因为体检坏了就让字体永远用不了）。"""
        log.warning("字体体检没法完成（%s），风险字体不再暂挡", why)
        self.gave_up = True
        self._notify()

    def _child_cmd(self, progress: str, todo: str):
        from cutora.core.paths import REPO_ROOT
        return [sys.executable, "-u", "-m", "cutora.ui.font_probe", "--child", progress, todo], REPO_ROOT

    @staticmethod
    def _file_of(family: str) -> str:
        try:
            from cutora.ui.panels import fonts as F
            files = F.FONT_FILES.get(family) or []
            return os.path.normcase(os.path.abspath(files[0])) if files else ""
        except Exception:
            return ""

    def _mark_bad(self, family: str, rec: dict, fams: list) -> list:
        """一个字体卡死 / 崩溃 → 同一个字体文件里的其它家族（可变字体在 Windows 上会登记出几十个具名实例）一起停用，不再一个个试。"""
        src = self._file_of(family)
        hit = [family]
        if src:
            hit += [f for f in fams if f != family and self._file_of(f) == src]
        with self._lock:
            for f in hit:
                if f not in self.results or f == family:
                    self.results[f] = dict(rec, **({"same_file_as": family} if f != family else {}))
        return hit

    def _run(self, delay_s: float) -> None:
        from cutora.core.flags import apply_render_child
        from cutora.core.log import get_logger
        from cutora.core.proc import popen
        from cutora.ui.panels import fonts as F
        log = get_logger("cutora.font_probe")
        time.sleep(max(0.0, delay_s))
        self.running = True
        fams = list(self._order or F.ALLOWED_FONTS)
        progress = _cache_path() + ".progress"
        todo_path = _cache_path() + ".todo.json"
        try:
            while True:
                with self._lock:
                    todo = [f for f in fams if f not in self.results]
                if not todo:
                    break
                try:
                    os.remove(progress)
                except OSError:
                    pass
                with open(todo_path, "w", encoding="utf-8") as fh:
                    json.dump(todo, fh, ensure_ascii=False)
                cmd, cwd = self._child_cmd(progress, todo_path)
                env = apply_render_child(dict(os.environ))      # 子进程不碰 torch / CUDA（启动快、不占显卡）
                env["CUTORA_FONT_PROBE_CHILD"] = "1"
                kw = {}
                if os.name == "nt":
                    from cutora.core.proc import creationflags
                    kw["creationflags"] = creationflags() | 0x00004000   # BELOW_NORMAL_PRIORITY_CLASS：不跟剪辑抢 CPU
                try:
                    proc = popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
                except Exception as exc:
                    self._give_up(log, "子进程起不来：%s" % exc)
                    return
                launched = time.monotonic()
                cur = None            # (family, started_at)
                seen = 0
                progressed = False
                exited = False
                while True:
                    time.sleep(0.2)
                    lines = []
                    try:
                        with open(progress, encoding="utf-8") as fh:
                            lines = fh.read().splitlines()
                    except OSError:
                        pass
                    ended = False
                    for ln in lines[seen:]:
                        parts = ln.split("\t")
                        if parts[0] == "S" and len(parts) >= 3:
                            cur = (parts[2], time.monotonic())
                        elif parts[0] in ("D", "E") and len(parts) >= 3:
                            fam = parts[2]
                            if parts[0] == "D":
                                ms = float(parts[3]) if len(parts) > 3 else 0.0
                                rec = {"status": "slow" if ms >= SLOW_MS else "ok", "ms": round(ms, 1)}
                            else:
                                rec = {"status": "ok", "ms": 0.0, "error": parts[3] if len(parts) > 3 else ""}
                            with self._lock:
                                self.results[fam] = rec
                            progressed = True
                            if rec["status"] != "ok":
                                log.warning("字体体检：%s 渲染 %.0f ms（慢）", fam, rec.get("ms", 0))
                            cur = None
                        elif parts[0] == "END":
                            ended = True
                    if seen != len(lines):
                        seen = len(lines)
                        self._save()
                        self._notify()
                    if ended:
                        break
                    if not lines and time.monotonic() - launched > STARTUP_S:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        self._give_up(log, "子进程 %d 秒还没开始" % STARTUP_S)
                        return
                    if cur is not None and time.monotonic() - cur[1] > HANG_S:
                        fam = cur[0]
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        hit = self._mark_bad(fam, {"status": "hang", "ms": HANG_S * 1000.0}, fams)
                        progressed = True
                        log.warning("字体体检：%s 渲染超过 %.0f 秒没完成 → 这台电脑上停用它%s", fam, HANG_S,
                                    ("（同一字体文件的 %d 个具名实例一起停用）" % (len(hit) - 1)) if len(hit) > 1 else "")
                        self._save()
                        self._notify()
                        break
                    if exited:
                        # 子进程已退出、剩下的行也读完了：有字体开始了没结束 = 它把进程弄崩了
                        if cur is not None:
                            fam = cur[0]
                            hit = self._mark_bad(fam, {"status": "crash", "ms": 0.0, "code": proc.returncode}, fams)
                            progressed = True
                            log.warning("字体体检：渲染 %s 时子进程崩溃（退出码 %s）→ 这台电脑上停用它%s", fam,
                                        proc.returncode,
                                        ("（同一字体文件的 %d 个具名实例一起停用）" % (len(hit) - 1)) if len(hit) > 1 else "")
                            self._save()
                            self._notify()
                        else:
                            self._give_up(log, "子进程意外退出，退出码 %s" % proc.returncode)
                            return
                        break
                    if proc.poll() is not None:
                        exited = True     # 再读一轮进度文件（退出前最后写的行）再下结论
                        time.sleep(0.2)
                if not progressed:
                    self._give_up(log, "子进程一个字体都没测出结果")
                    return
            with self._lock:
                self.complete = True
            self._save()
            self._notify()
            bad = sorted(self.blocked())
            if bad:
                log.warning("字体体检完成：%d 个字体在这台电脑上会卡死 / 崩溃，已停用：%s", len(bad), "、".join(bad))
            else:
                log.info("字体体检完成：%d 个字体都正常", len(self.results))
        finally:
            self.running = False
            for path in (progress, todo_path):
                try:
                    os.remove(path)
                except OSError:
                    pass


def probe() -> FontProbe:
    if _STATE["probe"] is None:
        _STATE["probe"] = FontProbe()
    return _STATE["probe"]


def blocked_families() -> frozenset:
    p = _STATE.get("probe")
    return p.blocked() if p is not None else frozenset()


def _render_child_probe():
    """导出 / 渲染子进程：主程序的体检结果在磁盘缓存里 → 读一次（只读，不起体检、不挡风险字体）。
    否则主程序里停用的字体到了导出子进程里照样画 = 导出卡死。"""
    if _STATE.get("probe") is not None or _STATE.get("child_tried"):
        return _STATE.get("probe")
    if os.environ.get("CUTORA_FONT_PROBE_CHILD"):
        _STATE["child_tried"] = True
        return None
    try:
        from cutora.core.flags import is_render_child
        if not is_render_child():
            _STATE["child_tried"] = True    # 普通进程（主程序之外的测试 / 命令行）：不读
            return None
        from cutora.ui.panels import fonts as F
        if not F.FONT_FILES:
            return None                     # 字体还没装好：下次再试（签名要用到字体文件）
    except Exception:
        return None
    _STATE["child_tried"] = True
    pr = FontProbe()
    try:
        pr._sig = font_signature()
        pr._prepared = True
        pr.gave_up = True                   # 子进程里不挡「还没体检」的字体，只认已经查出来的
        pr._load()
    except Exception:
        return None
    _STATE["probe"] = pr
    return pr


def is_blocked(family: str) -> bool:
    p = _STATE.get("probe") or _render_child_probe()
    return p is not None and p.is_blocked(family)


def health(family: str) -> str:
    p = _STATE.get("probe") or _render_child_probe()
    return p.health(family) if p is not None else ""


_BRIDGE = {"obj": None}


def qt_bridge():
    """界面线程里建的 QObject：``changed`` 在体检结果变了时发（从后台线程排队到界面线程）。"""
    if _BRIDGE["obj"] is None:
        from PySide6.QtCore import QObject, Signal

        class _Bridge(QObject):
            changed = Signal()

        b = _Bridge()
        probe().listeners.append(b.changed.emit)
        _BRIDGE["obj"] = b
    return _BRIDGE["obj"]


def slow_families() -> set:
    p = _STATE.get("probe")
    return p.slow() if p is not None else set()


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--child":
        sys.exit(_child_main(sys.argv[2], sys.argv[3]))
    print("usage: python -m cutora.ui.font_probe --child <progress-file> <todo.json>")
    sys.exit(2)
