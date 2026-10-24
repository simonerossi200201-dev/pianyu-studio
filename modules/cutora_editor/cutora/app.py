# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""CUTORA application entry."""
from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# === 启动前环境自检 (防止依赖缺失导致闪退) ===
def self_check_dependencies():
    missing = []
    errors = {}
    # 检查核心依赖 —— 捕获所有异常（不仅是 ImportError），
    # 因为 DLL 加载失败、版本冲突等也会阻止 import。
    for pkg_import, pkg_pip in [
        ("yaml", "PyYAML"),
        ("pandas", "pandas"),
        ("tokenizers", "tokenizers"),
        ("transformers", "transformers"),
    ]:
        try:
            __import__(pkg_import)
        except Exception as _e:
            missing.append(pkg_pip)
            errors[pkg_pip] = str(_e)

    if missing:
        print("=" * 60)
        print("CRITICAL ERROR: Environment is missing packages!")
        print(f"Missing: {', '.join(missing)}")
        for pkg, err in errors.items():
            print(f"  {pkg}: {err}")
        print()
        print("Repair: run CUTORA_Install.bat (runtime pip install is not allowed).")
        print("=" * 60)
        # [v6.1] 只有交互式终端才等用户按回车。
        # 渲染子进程的 stdin 是 DEVNULL，这里若无条件调用 input() 会抛异常
        # 甚至挂起，必须先确认 stdin 真的可用。
        try:
            if sys.stdin is not None and sys.stdin.isatty():
                input("Press Enter to exit...")
        except Exception:
            pass
        sys.exit(1)

def run_self_test():
    from cutora.core.utils import clean_text, format_time_ass
    from cutora.ui.timeline.widget import TimelineWidget

    results = []
    try:
        out = clean_text(" \u200bHello\u200c ")
        results.append(("clean_text basic", out == "Hello"))
    except Exception:
        results.append(("clean_text basic", False))

    try:
        out = format_time_ass(0)
        results.append(("format_time_ass zero", out == "0:00:00.00"))
    except Exception:
        results.append(("format_time_ass zero", False))

    try:
        interval = TimelineWidget._pick_major_interval(20)
        results.append(("timeline major interval", interval in [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60]))
    except Exception:
        results.append(("timeline major interval", False))

    all_ok = True
    for name, ok in results:
        status = "OK" if ok else "FAIL"
        print(f"[SELF-TEST] {name}: {status}")
        if not ok:
            all_ok = False

    print("[SELF-TEST] RESULT: OK" if all_ok else "[SELF-TEST] RESULT: FAIL")
    return 0 if all_ok else 1

def _qt_message_handler(mode, context, message):
    """Kept so older callers still land in the logging handler."""
    from cutora.core.log import qt_message_to_log
    qt_message_to_log(mode, context, message)

_FAULT_LOG_FH = None
_FAULT_SESSION_KEEP = 5


def _fault_session_separator(now=None) -> str:
    """One-line timestamp banner written at each process start."""
    from datetime import datetime
    ts = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    return "===== %s =====\n" % ts


def split_faulthandler_sessions(text: str) -> list:
    """Split a faulthandler log into session blocks (separator + body)."""
    if not text:
        return []
    sessions = []
    current = []
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        if stripped.startswith("===== ") and stripped.endswith(" ====="):
            if current:
                sessions.append("".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        sessions.append("".join(current))
    return sessions


def prepare_faulthandler_log(path, keep=_FAULT_SESSION_KEEP, now=None):
    """Keep the last ``keep-1`` sessions, then write a new timestamp separator."""
    text = ""
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            text = ""
    old = split_faulthandler_sessions(text)
    keep_old = max(0, int(keep) - 1)
    retained = old[-keep_old:] if keep_old else []
    body = "".join(retained)
    if body and not body.endswith("\n"):
        body += "\n"
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
        fh.write(_fault_session_separator(now))
        fh.flush()


def _enable_faulthandler() -> None:
    """Append native crash stacks to USER_DATA/logs/faulthandler.log."""
    global _FAULT_LOG_FH
    if _FAULT_LOG_FH is not None:
        return
    import faulthandler
    from cutora.core.paths import ensure_user_layout, user_logs_dir

    ensure_user_layout()
    log_dir = user_logs_dir()
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "faulthandler.log")
    prepare_faulthandler_log(path, keep=_FAULT_SESSION_KEEP)
    _FAULT_LOG_FH = open(path, "a", encoding="utf-8", newline="\n")
    faulthandler.enable(file=_FAULT_LOG_FH, all_threads=True)

def _git_identity() -> tuple[str, str]:
    """Return (short_hash, branch); ``unknown`` if git is unavailable."""
    from cutora.core.paths import REPO_ROOT
    from cutora.core.proc import run

    def _git(*args: str) -> str:
        try:
            proc = run(
                ["git", *args],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            return "unknown"
        if proc.returncode != 0:
            return "unknown"
        return (proc.stdout or "").strip() or "unknown"

    return _git("rev-parse", "--short", "HEAD"), _git("rev-parse", "--abbrev-ref", "HEAD")

def _pkg_version(mod_name: str) -> str:
    """包版本。Q187：读元数据，不 import —— 以前这里 __import__("torch") 一下就把 2～3 GB 拉进主界面。"""
    import sys as _sys
    mod = _sys.modules.get(mod_name)
    if mod is not None:
        return str(getattr(mod, "__version__", "unknown") or "unknown")
    try:
        from importlib.metadata import version as _ver
        return str(_ver(mod_name) or "unknown")
    except Exception:
        return "unknown"

def _log_startup_identity() -> None:
    """First startup log line: git, package versions, CUDA warmup, MSVCP140."""
    from cutora.core.log import get_logger
    from cutora.core.runtime import cuda_warmup_status, loaded_msvc_runtime_path

    git_hash, git_branch = _git_identity()
    ok, device = cuda_warmup_status()
    if ok is True:
        warmup = "ok"
    elif ok is False:
        warmup = "fail"
    else:
        warmup = "skipped"
    get_logger("cutora.app").info(
        "STARTUP git=%s branch=%s torch=%s ctranslate2=%s transformers=%s "
        "cuda_warmup=%s device=%s msvcp140=%s",
        git_hash,
        git_branch,
        _pkg_version("torch"),
        _pkg_version("ctranslate2"),
        _pkg_version("transformers"),
        warmup,
        device or "-",
        loaded_msvc_runtime_path() or "-",
    )
    try:
        from cutora.core.runtime import mimalloc_status
        get_logger("cutora.app").info("[Runtime] %s", mimalloc_status())
    except Exception:
        pass

def _set_app_user_model_id() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes
        from cutora.core.brand import APP_USER_MODEL_ID
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass

def _apply_app_identity(app) -> None:
    from cutora.core.brand import APP_DISPLAY_NAME, APP_NAME, APP_VERSION, ORGANIZATION_NAME
    from cutora.ui.splash import load_app_icon

    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_DISPLAY_NAME)
    app.setOrganizationName(ORGANIZATION_NAME)
    app.setApplicationVersion(APP_VERSION)
    icon = load_app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

def run_render_job_entry(argv=None):
    """Lightweight --render-job: QApplication + fonts + RenderWorker, no monolith / torch."""
    if argv is not None:
        sys.argv = [sys.argv[0]] + list(argv)
    from cutora.core.flags import mark_render_child
    mark_render_child()
    try:
        from cutora.core.runtime import pin_cv2_num_threads
        pin_cv2_num_threads(1)
    except Exception:
        pass

    _ji = sys.argv.index("--render-job")
    if _ji + 1 >= len(sys.argv):
        print("[RenderJob] 缺少任务文件参数", file=sys.stderr)
        return 2
    _job_file = sys.argv[_ji + 1]

    from PySide6.QtWidgets import QApplication
    from cutora.ui.panels.fonts import (
        FONT_DIR,
        DEFAULT_FONT_FAMILY,
        _iter_font_files,
        get_font_manager,
        load_embedded_fonts,
    )
    from cutora.render.parallel import run_render_job

    _app = QApplication(sys.argv)
    if not os.path.isdir(FONT_DIR) or not _iter_font_files():
        print("[RenderJob] 字体目录不可用: %s" % FONT_DIR, file=sys.stderr)
        return 1
    if not load_embedded_fonts():
        print("[RenderJob] 字体加载失败", file=sys.stderr)
        return 1
    _app.setFont(get_font_manager().get_font(DEFAULT_FONT_FAMILY, 10))
    return run_render_job(_job_file)

def main(argv=None):
    """GUI / late CLI entry. ``--render-job`` is handled in ``__main__`` first."""
    if argv is not None:
        sys.argv = [sys.argv[0]] + list(argv)

    os.chdir(_REPO_ROOT)
    _enable_faulthandler()

    from cutora.core.flags import is_render_child
    from cutora.core.log import setup_logging
    setup_logging()
    if "--render-job" not in sys.argv and not is_render_child():
        _log_startup_identity()
    try:
        from cutora.ai.prefs import migrate_app_settings
        migrate_app_settings()
    except Exception:
        pass

    if not is_render_child():
        self_check_dependencies()

    from PySide6.QtWidgets import QApplication
    try:
        from cutora.render import export_guard as _GUARD
    except Exception:
        _GUARD = None

    # Qt is imported now. The earlier setup_logging() ran before that and
    # deliberately did not pull PySide6 in (torch must load first).
    setup_logging()
    if _GUARD is not None and "--render-job" not in sys.argv:
        try:
            _GUARD.disable_console_quickedit()
        except Exception:
            pass
    if "--self-test" in sys.argv:
        raise SystemExit(run_self_test())
    if "--doctor" in sys.argv:
        from cutora.core.doctor import run_cli
        raise SystemExit(run_cli())

    if "--render-job" not in sys.argv and (
        "--do" in sys.argv or "--run" in sys.argv
    ):
        from cutora.batch import cli as batch_cli
        raise SystemExit(batch_cli.late_dispatch(sys.argv))

    if "--render-job" in sys.argv:
        raise SystemExit(run_render_job_entry())

    if not is_render_child():
        from cutora.core.single_instance import acquire_single_instance_or_exit
        acquire_single_instance_or_exit()

    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=is_render_child())
    print("DEBUG: 已强制开启离线模式 (禁止联网检查模型)")

    _queue_continue = "--queue-continue" in sys.argv
    if _queue_continue:
        sys.argv = [a for a in sys.argv if a != "--queue-continue"]

    _set_app_user_model_id()
    apply_ui_locale()
    app = QApplication(sys.argv)
    _apply_app_identity(app)
    raise SystemExit(run_gui_startup(app, queue_continue=_queue_continue))

def _migrate_user_data_or_log() -> None:
    """First-run import dialog. Runs right after the splash is up (Q106)."""
    from cutora.core.log import get_logger
    from cutora.core.migrate_user_data import prompt_and_import
    try:
        prompt_and_import(parent=None)
    except Exception:
        get_logger("cutora.app").exception("user-data migration failed")

def apply_ui_locale() -> None:
    """Pin number format to C so spinboxes show ``1.00``, not ``1,00``.

    The UI language is Chinese; Windows regional settings (e.g. Italian)
    must not change decimal separators on spinboxes.
    """
    from PySide6.QtCore import QLocale
    QLocale.setDefault(QLocale(QLocale.Language.C))


def startup_error(title: str, text: str) -> None:
    """启动过程里的报错框：必须置顶，不然会被闪屏或主窗口盖住，
    用户只会觉得「又卡住了」。没有 QApplication 时退回写日志。"""
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication, QMessageBox
        if QApplication.instance() is None:
            raise RuntimeError("no QApplication")
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle(title)
        box.setText(text)
        box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        box.show()
        box.raise_()
        box.activateWindow()
        box.exec()
    except Exception:
        from cutora.core.log import get_logger
        get_logger("cutora.app").error("%s: %s", title, text)



def _install_font_probe(w) -> None:
    """Q170：启动字体体检；结果变了 → 清文字缓存重画画布；某个字体被停用 → 状态栏说一声。"""
    from cutora.ui import font_probe
    from cutora.ui.panels import fonts as F
    bridge = font_probe.qt_bridge()
    state = {"gate": font_probe.probe().pending() | font_probe.probe().blocked()}

    def _on_changed():
        p = font_probe.probe()
        now = p.pending() | p.blocked()
        if now == state["gate"]:
            return
        state["gate"] = now
        try:
            from cutora.render.textfx import clear_roi_cache
            clear_roi_cache()
        except Exception:
            pass
        try:
            from cutora.render.text_layer_paint import clear_static_cache
            clear_static_cache()
        except Exception:
            pass
        try:
            from cutora.subtitle.text_utils import _clear_rich_cache_in_task
            for task in list(getattr(w, "tasks", None) or []):
                _clear_rich_cache_in_task(task)
        except Exception:
            pass
        canvas = getattr(w, "video_canvas", None)
        if canvas is not None:
            canvas.update()

    bridge.changed.connect(_on_changed)

    from PySide6.QtCore import QObject, Signal

    class _Note(QObject):
        note = Signal(str)

    note = _Note(w)

    def _show(fam):
        try:
            w.statusBar().showMessage(
                "字体「%s」在这台电脑上一渲染就会卡死，已暂用 %s 显示（工程里的字体设置没改）" % (fam, F.DEFAULT_FONT_FAMILY),
                10000)
        except Exception:
            pass

    note.note.connect(_show)
    F.SUBSTITUTION_LISTENERS.append(note.note.emit)
    font_probe.probe().start_async(delay_s=2.0)

def run_gui_startup(app, queue_continue: bool = False, window_factory=None) -> int:
    """Migration → splash → fonts → GPU → main window. Returns ``app.exec()``.

    ``window_factory`` defaults to ``MainWindow``; tests pass a stub so the
    startup order can be asserted without loading the monolith.
    """
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QStyleFactory
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.splash import make_splash, splash_message

    # Q106: 闪屏第一个上场。以前是先做用户数据迁移、再清波形缓存，
    # 最后才 show()，中间那几秒屏幕上什么都没有 —— 用户以为点了没反应。
    apply_ui_locale()
    splash = make_splash()
    splash.show()
    app.processEvents()

    splash_message(splash, "准备用户数据…")
    _migrate_user_data_or_log()
    splash_message(splash, "清理缓存…")
    try:
        from cutora.audio.waveform import prune_waveform_cache
        prune_waveform_cache()
    except Exception:
        pass

    splash_message(splash, "加载字体…")
    if not load_embedded_fonts():
        splash.close()
        # pythonw 下没有控制台，直接 SystemExit 用户一个字都看不到，
        # 只会看见「点了没反应」。必须弹一个置顶的框告诉他发生了什么。
        startup_error(
            "字体加载失败",
            "内置字体没能装载。\n\n"
            "多半是 resources\\fonts 目录缺失或被杀软隔离了。\n"
            "请重新解压一份安装包，或把这个目录加进杀软白名单。")
        raise SystemExit(1)
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)
    # Q164：全程序的下拉框一展开就看得到全部选项（不再被截掉、要滚轮才看得到）
    from cutora.ui.theme.combo_popup import install as _install_combo_popup
    _install_combo_popup(app)
    try:
        from cutora.ui.wheel_guard import install_wheel_guard as _install_wheel_guard
        _install_wheel_guard(app)
    except Exception:
        pass

    # Q170：字体体检的结果（或者「风险字体还没体检」）要在打开任何工程之前就位 → 会卡死的字体从第一帧起就不画
    try:
        from cutora.ui.font_probe import probe as _font_probe
        _font_probe().prepare()
    except Exception:
        pass

    splash_message(splash, "检测显卡…")
    try:
        from cutora.core.config import detect_gpu_config
        detect_gpu_config(verbose=False)
    except Exception:
        pass

    splash_message(splash, "加载界面…")
    if window_factory is None:
        from cutora.ui.main_window import MainWindow
        window_factory = MainWindow
    try:
        w = window_factory()
    except Exception as exc:
        # pythonw 下主窗口造不出来就是「闪一下什么都没有」。宁可弹个置顶
        # 的框，也别让用户对着空桌面猜。
        splash.close()
        import traceback
        from cutora.core.log import get_logger
        get_logger("cutora.app").exception("main window failed")
        startup_error(
            "启动失败",
            "主界面没能建起来：%s\n\n"
            "详细信息已写进日志。把日志发给开发者即可定位。\n%s"
            % (exc, traceback.format_exc()[-800:]))
        raise
    splash_message(splash, "就绪")
    splash.finish(w)
    w.show()
    w.raise_()
    w.activateWindow()
    _open_proj = os.environ.get("CUTORA_OPEN_PROJECT", "").strip()
    if not _open_proj:
        for _arg in sys.argv[1:]:
            if _arg.lower().endswith(".cutora") and os.path.isfile(_arg):
                _open_proj = _arg
                break
    if _open_proj and os.path.isfile(_open_proj) and hasattr(w, "open_project"):
        QTimer.singleShot(250, lambda p=_open_proj: w.open_project(p))
    # Q131：构造期间不许弹模态框（闪屏置顶，会把框压在下面、整个程序看着像卡死）。
    # 需要提示的先攒着，这里闪屏已经关了、主窗口已经在前台，再弹。
    pending = getattr(w, "show_pending_startup_warnings", None)
    if callable(pending):
        QTimer.singleShot(0, pending)
    # Q181：GPU 像素运算按需拉起（仅当显式开启 CUTORA_GPU_FX_WARMUP=1 时才在启动时后台常驻进程，
    # 避免未用到色度抠像/调整图层时白白常驻一个占数 GB 内存/显存的 torch 子进程）
    if os.environ.get("CUTORA_GPU_FX_WARMUP") == "1":
        try:
            from cutora.fx import gpu as _gpu_fx
            QTimer.singleShot(1500, _gpu_fx.warm_up_async)
        except Exception:
            pass
    # Q183：NVENC 并发上限由本机实测（后台线程，几秒钟，按驱动版本缓存；缓存有效就不跑）
    try:
        from cutora.render.export_guard import ensure_nvenc_probe

        def _probe_nvenc_bg():
            import threading
            from cutora.core.log import get_logger
            _lg = get_logger("cutora.nvenc")
            threading.Thread(target=lambda: ensure_nvenc_probe(log=_lg.info),
                             name="nvenc-probe", daemon=True).start()
        QTimer.singleShot(4000, _probe_nvenc_bg)
    except Exception:
        pass
    # Q187：启动 25 s / 120 s 各记一次主界面的内存构成，之后每 10 分钟一次（稳定态才有可比性）
    try:
        _mem_state = {"cpu": None, "t": None}

        def _log_mem_bg(tag):
            import threading

            def _run():
                try:
                    from cutora.core.log import get_logger
                    import threading as _th
                    import time as _time
                    from cutora.render.export_guard import gui_memory_note
                    try:
                        from cutora.preview.engine import live_decoder_count
                        _dec = live_decoder_count()
                    except Exception:
                        _dec = -1
                    cpu, now = _time.process_time(), _time.monotonic()
                    load = ""
                    hot = ""
                    if _mem_state["cpu"] is not None and now > _mem_state["t"]:
                        cores = (cpu - _mem_state["cpu"]) / (now - _mem_state["t"])
                        load = "；上一次采样以来 CPU 平均 %.2f 核" % cores
                        if cores > 0.15:
                            try:
                                from cutora.render.export_guard import python_hotspots_note
                                hot = "；" + python_hotspots_note()
                            except Exception:
                                hot = ""
                    _mem_state.update(cpu=cpu, t=now)
                    try:
                        from cutora.core.counters import delta_note
                        acts = "；" + delta_note()
                    except Exception:
                        acts = ""
                    get_logger("cutora.memory").info("[Memory] %s：%s；Python 线程 %d 个；解码器池里 %d 个解码器%s%s%s",
                                                     tag, gui_memory_note(), _th.active_count(), _dec, load, hot, acts)
                except Exception:
                    pass
            threading.Thread(target=_run, name="mem-note", daemon=True).start()
        QTimer.singleShot(25000, lambda: _log_mem_bg("启动 25 s 后主界面"))
        QTimer.singleShot(120000, lambda: _log_mem_bg("启动 120 s 后主界面"))
        _mem_timer = QTimer(w)
        _mem_timer.setInterval(600000)
        _mem_timer.timeout.connect(lambda: _log_mem_bg("主界面（每 10 分钟）"))
        _mem_timer.start()
    except Exception:
        pass

    def _maybe_resume_queue():
        try:
            from cutora.batch import cli as _qc
            q = _qc.load_queue()
        except Exception:
            q = None
        if queue_continue or (
            q and q.get("active") and q.get("resume_on_launch") and not q.get("paused")
        ):
            w.resume_project_queue()

    QTimer.singleShot(1600, _maybe_resume_queue)
    QTimer.singleShot(500, lambda: getattr(w, "_offer_autosave_restore", lambda: None)())
    # Q170：界面线程卡住 ≥ 0.5 s 就把卡住时的调用栈写进 cutora.log（「卡死」从此有据可查）
    try:
        from cutora.core.stall_watchdog import install as _install_watchdog
        _install_watchdog()
    except Exception:
        pass
    # Q170：后台把全部字体的 cmap 解好，切字体时界面线程不再解字体文件
    try:
        from cutora.ui.panels.fonts import warm_font_caches_async
        warm_font_caches_async()
    except Exception:
        pass
    # Q170：字体体检（子进程里把每个字体真画一遍，卡死 / 崩溃的停用）
    try:
        _install_font_probe(w)
    except Exception:
        pass

    def _kick_doctor():
        import threading
        from cutora.core.log import get_logger
        log = get_logger("cutora.app")

        def work():
            try:
                from cutora.core.doctor import run_doctor
                findings = run_doctor()
            except Exception as exc:
                log.exception("startup doctor crashed")
                from cutora.core.doctor import Finding
                findings = [Finding("crash", "startup doctor crashed: %s" % exc)]

            def apply():
                fn = getattr(w, "_apply_doctor_findings", None)
                if callable(fn):
                    fn(findings)

            QTimer.singleShot(0, apply)

        threading.Thread(target=work, daemon=True, name="cutora-doctor").start()

    QTimer.singleShot(400, _kick_doctor)
    rc = app.exec()
    try:                                     # Q189h：事件循环结束 = 在退出，拆窗口 / 关子进程不算「卡住」
        from cutora.core.stall_watchdog import current as _wd_current
        _wd = _wd_current()
        if _wd is not None:
            _wd.stop()
    except Exception:
        pass
    try:
        from cutora.fx import gpu as _gpu_fx2
        _gpu_fx2.stop_gui_server()           # Q187：主界面退出，常驻的 GPU 服务跟着退（它自己也盯着 pid）
    except Exception:
        pass
    return rc
