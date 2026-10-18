# -*- coding: utf-8 -*-
"""ParallelExportManager 错峰 / 降级 / 崩溃重试 / 保活窗口。

原为 sys.exit 脚本，T13d 小项改为 pytest 收集。断言与原先六组用例相同。
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EXPORT_GUARD_PY = os.path.join(REPO, "cutora", "render", "export_guard.py")
_QT_READY = False


def _ensure_qt():
    global _QT_READY
    if _QT_READY:
        return
    if "PySide6" not in sys.modules:
        from cutora.core.runtime import init_torch_runtime
        init_torch_runtime(render_child=False)
    _QT_READY = True

FAKE_CHILD = r'''
import json, os, sys, time
i = sys.argv.index("--render-job")
job = json.load(open(sys.argv[i+1], encoding="utf-8"))
items = job["items"]
pp, rp = job["progress_path"], job["result_path"]
job_dir = os.path.dirname(os.path.abspath(sys.argv[i+1]))
crash_after = int(os.environ.get("FAKE_CRASH_AFTER", "0"))
degrade_seen = []
def wp(done, cur):
    tmp = pp + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"done": done, "cur": cur, "total": len(items)}, f)
    for _ in range(30):
        try:
            os.replace(tmp, pp)
            return
        except PermissionError:
            time.sleep(0.02)
    os.replace(tmp, pp)
wp(0, 0)
for n, it in enumerate(items):
    if os.path.exists(os.path.join(job_dir, "degrade.flag")):
        degrade_seen.append(it["index"])
    for pct in (25, 60, 100):
        wp(n, pct); time.sleep(0.12)
    with open(it["output_path"], "wb") as f:
        f.write(b"\x00" * 2048)
    wp(n + 1, 0)
    if crash_after and (n + 1) >= crash_after:
        os._exit(9)          # 模拟子进程整个崩掉：不写 result 文件
json.dump({"failed": [], "completed": [it["index"] for it in items],
           "degraded": degrade_seen}, open(rp, "w"))
sys.exit(0)
'''


def _app():
    _ensure_qt()
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def run_case(n_jobs, workers, gpu_workers, crash_after=0, force_degrade_after=None):
    _ensure_qt()
    from PySide6.QtCore import QTimer
    from cutora.render import export_guard as _GUARD
    from cutora.render.parallel import ParallelExportManager

    tmproot = tempfile.mkdtemp(prefix="guardtest_")
    child = os.path.join(tmproot, "fake_child.py")
    io.open(child, "w", encoding="utf-8").write(FAKE_CHILD)
    outdir = os.path.join(tmproot, "字幕成品")
    os.makedirs(outdir, exist_ok=True)

    jobs = [({"video": f"v{i}.mp4"}, os.path.join(outdir, f"{i+1:02d}_sub.mp4"))
            for i in range(n_jobs)]

    if crash_after:
        os.environ["FAKE_CRASH_AFTER"] = str(crash_after)
    else:
        os.environ.pop("FAKE_CRASH_AFTER", None)

    app = _app()

    class M(ParallelExportManager):
        def _script_path(self):
            return child

        def _start_guard(self):
            state_path = os.path.join(tmproot, _GUARD.STATE_NAME)
            self.state_path = state_path
            self._hb = _GUARD.HeartbeatWriter(state_path, self._out_dirs,
                                              len(self._jobs), len(self._chunks),
                                              self._gpu_workers, self._tmpdir)
            self._hb.update(0, status="running")
            self._guard_proc = None
            self._watchdog = None

    mgr = M(jobs, mode="quality", use_gpu=True, export_mode="compressed",
            workers=workers, gpu_workers=gpu_workers, enc_threads=3,
            spawn_stagger=0.15)

    result = {"failed": None}
    ticks = []

    def on_fin(f):
        result["failed"] = list(f)

    mgr.finished.connect(on_fin)

    beat = QTimer()
    beat.setInterval(50)
    beat.timeout.connect(lambda: ticks.append(time.time()))
    beat.start()

    mgr.start()

    if force_degrade_after is not None:
        def fire():
            if mgr._tmpdir:
                _GUARD.write_degrade(mgr._tmpdir, "测试注入：显存不足")
        QTimer.singleShot(int(force_degrade_after * 1000), fire)

    t0 = time.time()
    while result["failed"] is None and time.time() - t0 < 90:
        app.processEvents()
        time.sleep(0.01)
    beat.stop()

    gaps = [b - a for a, b in zip(ticks, ticks[1:])] or [0]
    state = {}
    try:
        state = json.load(io.open(mgr.state_path, encoding="utf-8"))
    except Exception:
        pass
    return {
        "failed": result["failed"], "outdir": outdir, "tmproot": tmproot,
        "max_gap": max(gaps), "state": state,
        "produced": sorted(f for f in os.listdir(outdir) if f.endswith(".mp4")),
        "lock_exists": os.path.exists(os.path.join(outdir, _GUARD.LOCK_NAME)),
        "done_exists": os.path.exists(os.path.join(outdir, _GUARD.DONE_NAME)),
        "done_text": (io.open(os.path.join(outdir, _GUARD.DONE_NAME), encoding="utf-8").read()
                      if os.path.exists(os.path.join(outdir, _GUARD.DONE_NAME)) else ""),
    }


def test_export_guard_parallel_12():
    r = run_case(12, 3, gpu_workers=2)
    assert r["failed"] == [], r["failed"]
    assert len(r["produced"]) == 12, len(r["produced"])
    assert r["max_gap"] < 1.0, r["max_gap"]
    assert not r["lock_exists"]
    assert r["done_exists"]
    assert "成功        ：12 个" in r["done_text"]
    assert r["state"].get("status") == "finished", r["state"].get("status")


def test_export_guard_degrade_midway():
    r2 = run_case(9, 3, gpu_workers=3, force_degrade_after=0.8)
    assert r2["failed"] == [], r2["failed"]
    assert len(r2["produced"]) == 9, len(r2["produced"])
    assert r2["max_gap"] < 1.0, r2["max_gap"]


def test_export_guard_crash_retries_serial():
    r3 = run_case(8, 2, gpu_workers=0, crash_after=1)
    assert r3["failed"] is not None and len(r3["failed"]) == 8, r3["failed"]


def test_export_guard_lock_file_updates():
    _ensure_qt()
    from cutora.render import export_guard as _GUARD
    tmp = tempfile.mkdtemp(prefix="hbtest_")
    out = os.path.join(tmp, "字幕成品")
    os.makedirs(out)
    hb = _GUARD.HeartbeatWriter(os.path.join(tmp, "state.json"), [out], 80, 6, 3, tmp)
    hb.pids = [111, 222]
    hb.update(0)
    first = io.open(os.path.join(out, _GUARD.LOCK_NAME), encoding="utf-8").read()
    assert "不要重启电脑" in first
    assert "进度        ：0 / 80" in first and "更新时间" in first
    time.sleep(3.2)
    hb.update(37, latest="37_sub.mp4")
    second = io.open(os.path.join(out, _GUARD.LOCK_NAME), encoding="utf-8").read()
    assert "进度        ：37 / 80" in second and "37_sub.mp4" in second
    hb.finish(80, 0, latest="80_sub.mp4")
    assert (
        not os.path.exists(os.path.join(out, _GUARD.LOCK_NAME))
        and os.path.exists(os.path.join(out, _GUARD.DONE_NAME))
    )


def test_export_guard_keepalive_window_starts():
    tmp2 = tempfile.mkdtemp(prefix="guardui_")
    out2 = os.path.join(tmp2, "字幕成品")
    os.makedirs(out2)
    started = time.time() - 600
    for i in range(37):
        open(os.path.join(out2, f"{i+1:02d}_sub.mp4"), "wb").write(b"0")
    state2 = os.path.join(tmp2, "state.json")
    json.dump({"status": "running", "done": 30, "total": 80, "out_dirs": [out2],
               "tmpdir": tmp2, "pids": [os.getpid()], "parent_pid": os.getpid(),
               "started_at": started, "updated_at": time.time() - 40,
               "ui_tick": time.time() - 40, "workers": 6, "gpu_workers": 3},
              io.open(state2, "w", encoding="utf-8"))

    p = subprocess.Popen([sys.executable, EXPORT_GUARD_PY,
                          "--watch", state2],
                         env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(4)
    alive = p.poll() is None
    p.terminate()
    try:
        outtxt = p.communicate(timeout=5)[0]
    except Exception:
        outtxt = ""
    assert alive, outtxt[-400:]

    old_dir = os.path.join(tmp2, "旧成品")
    os.makedirs(old_dir)
    for i in range(5):
        f = os.path.join(old_dir, f"old{i}.mp4")
        open(f, "wb").write(b"0")
        os.utime(f, (started - 5000, started - 5000))
    cnt = 0
    since = started - 30
    for n in os.listdir(old_dir):
        if os.path.getmtime(os.path.join(old_dir, n)) >= since:
            cnt += 1
    assert cnt == 0, cnt


def test_export_guard_plan_parallel_export():
    _ensure_qt()
    from cutora.render import export_guard as _GUARD
    pl = _GUARD.plan_parallel_export(80, 6, True, cpu_count=24)
    assert pl["workers"] >= 1
    assert pl["enc_threads"] >= 2
    pl2 = _GUARD.plan_parallel_export(80, 6, False, cpu_count=4)
    assert pl2["workers"] <= 2, pl2["workers"]
    assert pl2["gpu_workers"] == 0
