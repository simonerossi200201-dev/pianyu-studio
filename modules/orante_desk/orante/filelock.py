# -*- coding: utf-8 -*-
"""跨进程文件锁。manifest 写入用，补进程内 _io_lock 够不到的那一段。

Windows 走 msvcrt.locking（锁文件头 1 字节，非阻塞 + 短睡重试）；
其它平台走 fcntl.flock。拿不到就等到 timeout，超时报错。
选 msvcrt 而不是「原子创建 + 心跳」：Windows 上这是现成的强制锁，
进程被强杀操作系统会放，不会留下 stale 文件把门焊死。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from .errors import OranteError

MANIFEST_LOCK_SEC = 10.0
_LOCK_NAME = ".manifest.lock"
_POLL = 0.05


class FileLock:
    def __init__(self, path, timeout: float = MANIFEST_LOCK_SEC):
        self.path = Path(path)
        self.timeout = float(timeout)
        self._fh = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+b")
        if self._fh.tell() == 0:
            self._fh.write(b"\0")
            self._fh.flush()
        deadline = time.time() + self.timeout
        while True:
            try:
                self._lock()
                return self
            except OSError:
                if time.time() >= deadline:
                    self._close()
                    raise OranteError(
                        "拿不到文件锁",
                        f"{self.path.name} 被另一个窗口占着，等了 {int(self.timeout)} 秒。",
                    )
                time.sleep(_POLL)

    def _lock(self) -> None:
        self._fh.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(self) -> None:
        if not self._fh:
            return
        try:
            self._fh.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass

    def _close(self) -> None:
        if self._fh:
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None

    def release(self) -> None:
        self._unlock()
        self._close()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *a):
        self.release()


def manifest_lock(task_dir, timeout: float = MANIFEST_LOCK_SEC) -> FileLock:
    """写 manifest.json 之前拿 <任务目录>/.manifest.lock。"""
    return FileLock(Path(task_dir) / _LOCK_NAME, timeout=timeout)
