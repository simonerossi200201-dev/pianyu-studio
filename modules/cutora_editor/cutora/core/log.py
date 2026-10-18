# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Logging helpers with secret redaction.

``setup_logging`` is idempotent. It attaches:

* a rotating file under ``user_logs_dir()/cutora.log`` (4 MB × 5, UTF-8)
* an in-memory ring of the last 5000 records
* a Qt message handler, once PySide6 is already imported
* a stand-in for ``sys.stdout`` / ``sys.stderr`` when pythonw left them as None

A real console is left alone. PySide6 is not imported from here: torch must
load before Qt, and this module is imported first.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import queue
import re
import sys
import threading
from collections import deque

_REDACT_RES = (
    re.compile(r"Bearer\s+\S+", re.IGNORECASE),
    re.compile(r"api[_-]?key\s*[=:]\s*\S+", re.IGNORECASE),
    re.compile(r"(?:xai-|sk-|sk-ant-|AIza)[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?:key|token|secret)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{8,}", re.IGNORECASE),
    re.compile(r"key=[A-Za-z0-9_\-]{8,}", re.IGNORECASE),
)

LOG_MAX_BYTES = 4 * 1024 * 1024
LOG_BACKUP_COUNT = 5
RING_CAPACITY = 5000
LOG_FILE_NAME = "cutora.log"

_QT_SKIP = (
    "Painter ended with 2 saved states",
    "QGradient::setColorAt",
    "COLRv1",
    "Painter not active",
    "Paint device returned engine == 0",
)

_FILTER = None  # set after RedactFilter
_RING: "RingBufferHandler | None" = None
_FILE: logging.Handler | None = None
_FILE_FAILED_FOR = ""
_LEVEL_SET = False
_QT_INSTALLED = False
_IN_SETUP = False
_READY = False
_QT_LEVELS = None
_QT_SEEN: set = set()
_QT_SEEN_LOCK = threading.Lock()
_QT_QUEUE: "queue.SimpleQueue" = queue.SimpleQueue()
_QT_DRAIN_STARTED = False
_stdio_guard = threading.local()


def redact_text(text: str) -> str:
    """Replace credential-like substrings with ``***``."""
    if not text:
        return text
    out = text
    for cre in _REDACT_RES:
        out = cre.sub("***", out)
    return out


class RedactFilter(logging.Filter):
    """Filter that redacts secrets in the log message and args."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = redact_text(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        k: redact_text(v) if isinstance(v, str) else v
                        for k, v in record.args.items()
                    }
                else:
                    record.args = tuple(
                        redact_text(a) if isinstance(a, str) else a
                        for a in record.args
                    )
        except Exception:
            pass
        return True


_FILTER = RedactFilter()


class LogSnap:
    """Frozen view of one log record. Safe to keep after the record is reused."""

    __slots__ = ("levelno", "levelname", "name", "message", "created")

    def __init__(self, record: logging.LogRecord):
        self.levelno = int(record.levelno)
        self.levelname = str(record.levelname)
        self.name = str(record.name)
        try:
            self.message = record.getMessage()
        except Exception:
            self.message = str(record.msg)
        self.created = float(getattr(record, "created", 0.0) or 0.0)

    def getMessage(self) -> str:
        return self.message


class RingBufferHandler(logging.Handler):
    """Thread-safe ring of the most recent records (default 5000)."""

    def __init__(self, capacity: int = RING_CAPACITY):
        super().__init__(level=logging.DEBUG)
        self.capacity = int(capacity)
        self._records: deque = deque(maxlen=self.capacity)
        self._lock = threading.Lock()
        self._callbacks: list = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            snap = LogSnap(record)
        except Exception:
            return
        with self._lock:
            self._records.append(snap)
            callbacks = list(self._callbacks)
        for fn in callbacks:
            try:
                fn(snap)
            except Exception:
                pass

    def records(self) -> list:
        """Oldest first. The length never exceeds ``capacity``."""
        with self._lock:
            return list(self._records)

    def add_callback(self, fn) -> None:
        """``fn(snap)`` runs on the logging thread for each new line."""
        if fn is None:
            return
        with self._lock:
            if fn not in self._callbacks:
                self._callbacks.append(fn)

    def remove_callback(self, fn) -> None:
        with self._lock:
            self._callbacks = [c for c in self._callbacks if c is not fn]

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


class _LoggingStream:
    """Stand-in for a missing stdout/stderr. Lines go to logging, never back."""

    encoding = "utf-8"
    errors = "replace"

    def __init__(self, level: int, name: str):
        self._level = int(level)
        self._name = name
        self._buf = ""

    def write(self, s) -> int:
        if s is None:
            return 0
        text = s if isinstance(s, str) else str(s)
        if not text:
            return 0
        if getattr(_stdio_guard, "busy", False):
            return len(text)
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line:
                self._log(line)
        return len(text)

    def flush(self) -> None:
        if self._buf:
            line = self._buf
            self._buf = ""
            self._log(line)

    def _log(self, line: str) -> None:
        _stdio_guard.busy = True
        try:
            logging.getLogger(self._name).log(self._level, "%s", line)
        except Exception:
            pass
        finally:
            _stdio_guard.busy = False

    def isatty(self) -> bool:
        return False

    def writable(self) -> bool:
        return True

    def readable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return False

    def fileno(self) -> int:
        raise OSError("no console")

    def close(self) -> None:
        self.flush()


def log_file_path() -> str:
    """Absolute path of the active log file. Follows ``CUTORA_USER_DATA``."""
    from cutora.core.paths import user_logs_dir
    return os.path.join(user_logs_dir(), LOG_FILE_NAME)


def privacy_note() -> str:
    """One sentence for the help menu and the console window."""
    try:
        path = log_file_path()
    except Exception:
        path = "用户数据目录/logs/cutora.log"
    return (
        "日志写在 %s。单个文件超过 4 MB 会换新文件，旧的最多留 5 份。"
        "密钥、令牌一类内容会写成 ***，不会原文落盘。"
        % path
    )


def ring_buffer() -> RingBufferHandler:
    """The process-wide ring. Creates it on first use."""
    setup_logging()
    assert _RING is not None
    return _RING


def _ensure_filter(root: logging.Logger) -> None:
    if _FILTER not in root.filters:
        root.addFilter(_FILTER)


def _is_console_handler(handler: logging.Handler) -> bool:
    return (
        isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.FileHandler)
    )


def _ensure_console(root: logging.Logger, level: int) -> None:
    """Keep a real stderr. pythonw has no stderr, so do not attach one.

    Never attach a stream handler to ``_LoggingStream``: that handler would
    write back into logging and recurse.
    """
    if sys.stderr is None or isinstance(sys.stderr, _LoggingStream):
        return
    if any(_is_console_handler(h) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    handler.addFilter(_FILTER)
    root.addHandler(handler)


def _wanted_log_path() -> str:
    return os.path.normcase(os.path.abspath(log_file_path()))


def _file_handler_current() -> bool:
    try:
        paths_mod = sys.modules.get("cutora.core.paths")
        if paths_mod is None or not hasattr(paths_mod, "user_logs_dir"):
            return _FILE is not None
        want = _wanted_log_path()
    except Exception:
        return _FILE is not None
    if _FILE is None:
        # A failed open is settled until the directory changes, so get_logger
        # does not retry the same path on every line.
        return bool(_FILE_FAILED_FOR) and _FILE_FAILED_FOR == want
    have = os.path.normcase(os.path.abspath(getattr(_FILE, "baseFilename", "") or ""))
    return have == want


class _SafeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler that tolerates Windows multi-process file locks on rollover."""

    def shouldRollover(self, record: logging.LogRecord) -> int:
        try:
            from cutora.core.flags import is_render_child
            if is_render_child():
                return 0
        except Exception:
            pass
        import time as _time
        if getattr(self, "_rollover_cooldown_until", 0.0) > _time.monotonic():
            return 0
        return super().shouldRollover(record)

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except PermissionError:
            import time as _time
            self._rollover_cooldown_until = _time.monotonic() + 60.0
            if self.stream is None:
                try:
                    self.stream = self._open()
                except Exception:
                    pass


def _ensure_file(root: logging.Logger, level: int) -> None:
    global _FILE, _FILE_FAILED_FOR
    if _file_handler_current():
        return
    try:
        path = log_file_path()
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
    except Exception:
        return
    want = os.path.normcase(os.path.abspath(path))
    if _FILE_FAILED_FOR == want and _FILE is None:
        return
    if _FILE is not None:
        try:
            root.removeHandler(_FILE)
        except Exception:
            pass
        try:
            _FILE.close()
        except Exception:
            pass
        _FILE = None
    try:
        handler = _SafeRotatingFileHandler(
            path,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
    except Exception:
        _FILE_FAILED_FOR = want
        return
    _FILE_FAILED_FOR = ""
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    handler.addFilter(_FILTER)
    handler.name = "cutora.file"
    root.addHandler(handler)
    _FILE = handler


def _ensure_ring(root: logging.Logger) -> None:
    global _RING
    if _RING is not None and _RING in root.handlers:
        return
    handler = RingBufferHandler(RING_CAPACITY)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    handler.addFilter(_FILTER)
    handler.name = "cutora.ring"
    root.addHandler(handler)
    _RING = handler


def _ensure_stdio() -> None:
    if sys.stdout is None:
        sys.stdout = _LoggingStream(logging.INFO, "stdout")
    if sys.stderr is None:
        sys.stderr = _LoggingStream(logging.WARNING, "stderr")


def _qt_level(mode) -> int:
    global _QT_LEVELS
    if _QT_LEVELS is None:
        from PySide6.QtCore import QtMsgType
        _QT_LEVELS = {
            QtMsgType.QtDebugMsg: logging.DEBUG,
            QtMsgType.QtInfoMsg: logging.INFO,
            QtMsgType.QtWarningMsg: logging.WARNING,
            QtMsgType.QtCriticalMsg: logging.ERROR,
            QtMsgType.QtFatalMsg: logging.CRITICAL,
        }
    return _QT_LEVELS.get(mode, logging.WARNING)


def _remember_qt_message(text: str) -> bool:
    """True the first time we see this exact line. Repeats are dropped.

    Export paints the same ``QFont::setPixelSize`` warning once per glyph.
    Writing every copy fills the log and, on Windows, blocks the render
    when stderr's pipe is full.
    """
    with _QT_SEEN_LOCK:
        if text in _QT_SEEN:
            return False
        if len(_QT_SEEN) > 400:
            _QT_SEEN.clear()
        _QT_SEEN.add(text)
        return True


def _attach_qt_logger(logger: logging.Logger) -> None:
    """Qt lines go to the file and the ring, not to stderr.

    ``propagate`` stays off so a full stderr pipe cannot stall export.
    """
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    if _FILE is not None and _FILE not in logger.handlers:
        logger.addHandler(_FILE)
    if _RING is not None and _RING not in logger.handlers:
        logger.addHandler(_RING)


def _write_qt_line(level: int, text: str) -> None:
    logger = logging.getLogger("qt")
    _attach_qt_logger(logger)
    logger.log(level, "%s", text)


def _qt_drain() -> None:
    while True:
        level, text = _QT_QUEUE.get()
        if getattr(_stdio_guard, "qt", False):
            continue
        _stdio_guard.qt = True
        try:
            _write_qt_line(level, text)
        except Exception:
            pass
        finally:
            _stdio_guard.qt = False


def _ensure_qt_drain() -> None:
    global _QT_DRAIN_STARTED
    if _QT_DRAIN_STARTED:
        return
    threading.Thread(target=_qt_drain, name="cutora-qt-log", daemon=True).start()
    _QT_DRAIN_STARTED = True


def qt_message_to_log(mode, context, message) -> None:
    """Qt message handler body. Known painter noise stays dropped.

    Overlay threads must not take the logging lock: the frame thread can be
    inside logging while it waits for them, and export stops on the first frame.
    Those lines are queued. The main thread logs directly so tests see them.
    """
    text = str(message or "")
    if not text:
        return
    for skip in _QT_SKIP:
        if skip in text:
            return
    if not _remember_qt_message(text):
        return
    level = _qt_level(mode)
    if threading.current_thread() is not threading.main_thread():
        _ensure_qt_drain()
        _QT_QUEUE.put((level, text))
        return
    if getattr(_stdio_guard, "qt", False):
        return
    _stdio_guard.qt = True
    try:
        _write_qt_line(level, text)
    except Exception:
        pass
    finally:
        _stdio_guard.qt = False


def _qt_loaded() -> bool:
    return "PySide6" in sys.modules or "PySide6.QtCore" in sys.modules


def _render_child() -> bool:
    try:
        from cutora.core.flags import is_render_child
        return bool(is_render_child())
    except Exception:
        return False


def _try_install_qt_handler() -> None:
    """Install only when Qt is already imported. Never import PySide6 here.

    Render workers keep Qt's own handler. Routing those warnings through
    logging from the overlay threads stalls parallel export on Windows.
    """
    global _QT_INSTALLED
    if _render_child():
        _QT_INSTALLED = True
        return
    if _QT_INSTALLED or not _qt_loaded():
        return
    try:
        from PySide6.QtCore import qInstallMessageHandler
        qInstallMessageHandler(qt_message_to_log)
        _QT_INSTALLED = True
    except Exception:
        pass


def _needs_work() -> bool:
    if sys.stdout is None or sys.stderr is None:
        return True
    if not _file_handler_current():
        return True
    if _RING is None:
        return True
    if _qt_loaded() and not _QT_INSTALLED:
        return True
    return False


def setup_logging(level: int = logging.INFO) -> None:
    """Attach redact, file, ring, Qt hook and null-stdio guards. Idempotent."""
    global _IN_SETUP, _READY
    if _IN_SETUP:
        return
    if _READY and not _needs_work():
        return
    _IN_SETUP = True
    try:
        root = logging.getLogger()
        global _LEVEL_SET
        # Root starts at WARNING. Match the old basicConfig(level=INFO) once,
        # and do not clobber a level somebody already chose.
        if not _LEVEL_SET:
            if root.level in (logging.NOTSET, logging.WARNING):
                root.setLevel(level)
            _LEVEL_SET = True
        _ensure_filter(root)
        _ensure_ring(root)
        _ensure_console(root, level)
        _ensure_file(root, level)
        _ensure_stdio()
        _try_install_qt_handler()
        _READY = not _needs_work()
    finally:
        _IN_SETUP = False


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger. Handlers are configured by the caller."""
    setup_logging()
    logger = logging.getLogger(name)
    if _FILTER not in logger.filters:
        logger.addFilter(_FILTER)
    return logger
