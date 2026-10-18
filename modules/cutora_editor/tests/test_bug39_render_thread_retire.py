# -*- coding: utf-8 -*-
"""BUG-39: exit 0xC0000409 at the seam between two exports.

RenderWorker(QThread) declares its own ``finished`` and emits it from inside
run() *before* the finally-block cleanup. Replacing ``self.render`` while the
old thread is still in that cleanup destroys a running QThread → Qt abort().

Proof is a subprocess pair: the control (old behaviour) must die; the fixed
path (MainWindow._retire_render_thread) must exit 0. Both use the real
helper from main_window, not a copy.
"""
from __future__ import annotations

import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
if not os.path.isfile(VENV_PY):
    VENV_PY = sys.executable

CHILD = r'''
import sys, time, threading
from cutora.core.runtime import init_torch_runtime
init_torch_runtime(render_child=False)
from PySide6.QtCore import QCoreApplication, QThread, Signal, QTimer, QObject, Slot

MODE = sys.argv[1]

class FakeRender(QThread):
    finished = Signal()          # 和 RenderWorker 一样遮蔽 QThread.finished
    error = Signal(str)
    def __init__(self, n):
        super().__init__(); self.n = n
    def run(self):
        try:
            time.sleep(0.05)
            self.finished.emit()   # 先报“完成”
        finally:
            time.sleep(1.2)        # 再慢慢释放（模拟 24 个 VideoCapture.release）
    def stop(self):
        pass

class Host(QObject):
    def __init__(self):
        super().__init__(); self.render = None; self.n = 0; self.seen = []
        self._render_seq = 0
    if MODE == "fixed":
        from cutora.ui.main_window import MainWindow
        _retire_render_thread = MainWindow._retire_render_thread
        _retire_thread_attr = MainWindow._retire_thread_attr
        _export_outcome_once = MainWindow._export_outcome_once
    def start(self):
        if MODE == "fixed":
            self._retire_render_thread()
        self.n += 1
        self.render = FakeRender(self.n)     # ← 控制组在这里丢掉活线程
        self._render_seq += 1                # 和 run_export_worker 同一套：按次编号
        _seq = self._render_seq
        self.render.finished.connect(lambda _s=_seq: self.on_finished(_s))
        self.render.start()
    def on_finished(self, seq):
        if MODE == "fixed" and not self._export_outcome_once(seq, "ok"):
            self.seen.append(("dup", seq))
            return
        self.seen.append(("fin", seq))
        if self.n < 3:
            QTimer.singleShot(500, self.start)   # 和批量导出一样的 500 ms
        else:
            QTimer.singleShot(200, self.done)
    def done(self):
        if MODE == "fixed":
            self._retire_render_thread()
        QCoreApplication.instance().quit()

app = QCoreApplication(sys.argv)
h = Host()
h.start()
QTimer.singleShot(15000, app.quit)
app.exec()
print("SEEN", h.seen, flush=True)
fins = [n for k, n in h.seen if k == "fin"]
dups = [n for k, n in h.seen if k == "dup"]
if MODE == "fixed":
    assert fins == [1, 2, 3], fins           # 每条只处理一次、按顺序
    # 第二次投递（QThread 自带的 finished / 旧线程的迟到投递）要么被挡住、
    # 要么因为发送方已销毁根本没到 —— 两种都不许变成第二次 fin。
print("ok", flush=True)
'''


def _run(mode):
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    proc = subprocess.run(
        [VENV_PY, "-c", CHILD, mode], cwd=REPO, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180,
    )
    return proc


def test_control_still_crashes():
    """负对照：不等旧线程就替换 → 必须崩（Windows 0xC0000409 / POSIX SIGABRT）。
    这条要是绿了，说明 Qt 行为变了，上面的修法就该重新审视。"""
    proc = _run("control")
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "Destroyed while thread" in (proc.stderr or "") or proc.returncode in (
        -1073740791, 3221226505, -6, 134), (proc.returncode, proc.stderr[-800:])


def test_retire_makes_it_survive():
    proc = _run("fixed")
    assert proc.returncode == 0, "rc=%s\n%s\n%s" % (proc.returncode, proc.stdout, proc.stderr[-2000:])
    assert "ok" in proc.stdout
    assert "Destroyed while thread" not in (proc.stderr or "")


def test_close_event_waits_for_render_thread():
    src = open(os.path.join(REPO, "cutora", "ui", "main_window.py"), encoding="utf-8").read()
    i = src.index("def closeEvent(")
    body = src[i:i + 4500]
    assert '("render", "worker_ai")' in body
    assert "r.wait(" in body
    # 对齐线程同样先等再换
    k = src.index("self.worker_ai = AlignmentWorker(")
    assert 'self._retire_thread_attr("worker_ai")' in src[k - 300:k]
    j = src.index("def run_export_worker(")
    body2 = src[j:src.index("self.render = RenderWorker(", j)]
    assert "self._retire_render_thread()" in body2
