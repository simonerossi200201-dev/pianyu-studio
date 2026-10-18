# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q170 · 选字体不卡死：覆盖查表 / 新字体选择器 / 卡顿看门狗 / 子进程字体体检 + 会卡死的字体自动停用。"""
from __future__ import annotations

import json
import os
import sys
import textwrap
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent, QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox, QStyleFactory, QWidget  # noqa: E402


def _app():
    app = QApplication.instance() or QApplication(["q170"])
    app.setStyle(QStyleFactory.create("Fusion"))
    from cutora.ui.theme.apply import apply_theme
    apply_theme("roma-gold")
    from cutora.ui.panels import fonts as F
    F.load_embedded_fonts()
    return app


def _pump(ms: int = 50) -> None:
    app = QApplication.instance()
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


@pytest.fixture
def fresh_probe(monkeypatch, tmp_path):
    """每个用例一个干净的 FontProbe，缓存写到临时目录。"""
    from cutora.ui import font_probe as P
    monkeypatch.setattr(P, "_cache_path", lambda: str(tmp_path / "results.json"))
    monkeypatch.setitem(P._STATE, "probe", None)
    monkeypatch.setitem(P._BRIDGE, "obj", None)
    yield P
    P._STATE["probe"] = None
    P._BRIDGE["obj"] = None


# ───────────────────────────── 覆盖查表 ─────────────────────────────

def test_latin_coverage_json_matches_cmap():
    """拉丁语种的「缺不缺字」查预先算好的 JSON，结果必须和真解 cmap 一模一样。"""
    _app()
    from cutora.subtitle import font_coverage as C
    from cutora.ui.panels import fonts as F
    checked = 0
    for code in ("en", "fr", "de", "it", "es", "pt", "pl", "tr", "vi"):
        charset = C.charset_for_language(code)
        for fam in F.ALLOWED_FONTS:
            fast = C._latin_coverage_from_json(fam, code)
            if fast is None:
                continue
            slow = not C.missing_chars(fam, charset)
            assert fast == slow, (fam, code)
            checked += 1
    assert checked > 1000

def test_font_combo_populate_is_fast_and_parses_no_cmap(monkeypatch):
    """下拉框填字体（含「缺本语种」标注）不再在界面线程里解字体文件。"""
    _app()
    from cutora.subtitle import font_coverage as C
    from cutora.ui.panels import fonts as F
    calls = {"n": 0}
    real = C._cmap_cps

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(C, "_cmap_cps", counting)
    cb = QComboBox()
    t0 = time.perf_counter()
    F.register_font_combo(cb)
    ms = (time.perf_counter() - t0) * 1000.0
    # Shipped allow-list is 193 faces (240 files on disk, some filtered out).
    assert cb.count() >= 190
    assert ms < 1500, ms
    assert calls["n"] == 0


# ───────────────────────────── 选择器 ─────────────────────────────

def _open_picker(current="Arimo", sample="When God is leading"):
    from cutora.ui.panels import fonts as F
    from cutora.ui.widgets.font_picker import open_font_picker
    host = QWidget()
    host.resize(400, 300)
    host.show()
    got = []
    pop = open_font_picker(host, list(F.ALLOWED_FONTS), current, got.append, sample_text=sample)
    _pump(30)
    return host, pop, got


def test_picker_opens_fast_filters_and_picks(monkeypatch, tmp_path):
    _app()
    from cutora.ui.widgets import font_picker as FP
    monkeypatch.setattr(FP, "_recent_path", lambda: str(tmp_path / "font_recent.json"))
    t0 = time.perf_counter()
    host, pop, got = _open_picker("Arimo")
    # Quiet run 3.55s / 193 faces. Isolate child next to a 5 GB parent suite
    # measured 6.8s; keep a 10s cap so contention does not fail the gate.
    assert (time.perf_counter() - t0) < 10.0
    assert pop.isVisible()
    assert pop.objectName() == "fontPicker"
    # 当前字体选中
    idx = pop.view.currentIndex()
    assert idx.data(FP.ROLE_FAMILY) == "Arimo"
    # 打字过滤：开头匹配排前面
    pop.search.setText("te")
    first = pop.model.index(0, 0).data(FP.ROLE_FAMILY)
    assert first.lower().startswith("te")
    # ↓ + Enter 选中
    QApplication.sendEvent(pop.search, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier))
    want = pop.view.currentIndex().data(FP.ROLE_FAMILY)
    QApplication.sendEvent(pop.search, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
    assert got == [want]
    assert FP.load_recent()[0] == want
    host.close()


def test_picker_previews_render_off_gui_thread():
    """列表里的字形预览是后台线程画的；拿到之前先用界面字体写名字（不等）。"""
    _app()
    from cutora.ui.widgets import font_picker as FP
    host, pop, _got = _open_picker("Arimo")
    cache = FP.preview_cache()
    assert cache._renderer is not None and cache._renderer.isRunning()
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and not cache._pix:
        _pump(40)
    assert cache._pix, "后台没有画出任何预览"
    pop.close()
    host.close()


def test_combo_opens_picker_and_swallows_wheel():
    _app()
    from cutora.ui.panels import fonts as F
    cb = QComboBox()
    F.register_font_combo(cb)
    cb.setCurrentText("Arimo")
    cb.show()
    _pump(20)
    before = cb.currentText()
    ev = QWheelEvent(QPointF(5, 5), QPointF(cb.mapToGlobal(QPoint(5, 5))), QPoint(0, 0), QPoint(0, -120),
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(cb, ev)
    assert cb.currentText() == before          # 滚轮不再一格一格切字体（每一格都是一次重排版）
    F.open_font_picker_for_combo(cb)
    _pump(20)
    pops = [w for w in QApplication.topLevelWidgets() if w.objectName() == "fontPicker" and w.isVisible()]
    assert pops
    pops[-1].picked.emit("Teko")
    assert cb.currentText() == "Teko"
    for p in pops:
        p.close()
    cb.close()


# ───────────────────────────── 看门狗 ─────────────────────────────

def test_stall_watchdog_records_gui_stack():
    _app()
    from cutora.core.stall_watchdog import StallWatchdog
    wd = StallWatchdog(threshold_ms=200, poll_ms=20)
    wd.start(qt=True)
    try:
        _pump(150)

        def _stuck_here():
            time.sleep(0.6)

        _stuck_here()
        _pump(300)
        assert wd.stalls, "卡了 0.6 秒没被记下来"
        rec = wd.stalls[-1]
        assert rec["seconds"] >= 0.4
        assert "_stuck_here" in rec["stack"]
    finally:
        wd.stop()


# ───────────────────────────── 字体体检 ─────────────────────────────

FAKE_CHILD = textwrap.dedent('''
    import json, os, sys, time
    progress, todo = sys.argv[1], sys.argv[2]
    fams = json.load(open(todo, encoding="utf-8"))
    hang, crash = sys.argv[3], sys.argv[4]
    with open(progress, "a", encoding="utf-8") as out:
        for i, fam in enumerate(fams):
            out.write("S\\t%d\\t%s\\n" % (i, fam)); out.flush()
            if fam == hang:
                time.sleep(60)
            if fam == crash:
                os._exit(3)
            out.write("D\\t%d\\t%s\\t%.1f\\n" % (i, fam, 5.0)); out.flush()
        out.write("END\\n")
''')


def _fake_probe(P, tmp_path, monkeypatch, fams, hang="", crash=""):
    script = tmp_path / "fake_child.py"
    script.write_text(FAKE_CHILD, encoding="utf-8")
    monkeypatch.setattr(P, "HANG_S", 1.5)
    pr = P.probe()
    pr._sig = "test"
    pr._prepared = True
    pr._order = list(fams)
    pr._risky = frozenset(fams[:2])
    pr._refresh_blocked()
    monkeypatch.setattr(pr, "_child_cmd", lambda progress, todo: (
        [sys.executable, str(script), progress, todo, hang, crash], str(tmp_path)))
    return pr


def test_probe_marks_hang_and_crash_and_finishes(fresh_probe, tmp_path, monkeypatch):
    P = fresh_probe
    fams = ["Arimo", "Bitcount Prop Single Ink", "Nabla", "Teko", "Inter"]
    pr = _fake_probe(P, tmp_path, monkeypatch, fams, hang="Bitcount Prop Single Ink", crash="Nabla")
    assert pr.is_blocked("Arimo") and pr.health("Bitcount Prop Single Ink") == "checking"   # 风险字体查清之前先挡住
    seen = []
    pr.listeners.append(lambda: seen.append(1))
    t0 = time.monotonic()
    pr._run(0.0)
    assert time.monotonic() - t0 < 20
    assert pr.complete
    assert pr.status("Bitcount Prop Single Ink") == "hang"
    assert pr.status("Nabla") == "crash"
    assert pr.status("Teko") == "ok" and pr.status("Inter") == "ok" and pr.status("Arimo") == "ok"
    assert pr.blocked() == {"Bitcount Prop Single Ink", "Nabla"}
    assert pr.pending() == frozenset()
    assert pr.health("Nabla") == "blocked" and not pr.is_blocked("Teko")
    assert seen
    data = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert data["complete"] and data["results"]["Nabla"]["status"] == "crash"


def test_probe_cache_is_reused(fresh_probe, tmp_path, monkeypatch):
    P = fresh_probe
    (tmp_path / "results.json").write_text(json.dumps({
        "sig": "SIG", "complete": True,
        "results": {"Bitcount Prop Single Ink": {"status": "hang"}, "Teko": {"status": "slow", "ms": 2000}}}),
        encoding="utf-8")
    monkeypatch.setattr(P, "font_signature", lambda: "SIG")
    pr = P.probe()
    pr.start_async(delay_s=0)
    assert pr._thread is None                     # 签名没变：不再起子进程
    assert P.is_blocked("Bitcount Prop Single Ink")
    assert P.health("Teko") == "slow"


def test_probe_gives_up_when_child_cannot_run(fresh_probe, tmp_path, monkeypatch):
    """体检本身坏了 → 不能让风险字体永远用不了。"""
    P = fresh_probe
    fams = ["Nabla", "Teko"]
    pr = _fake_probe(P, tmp_path, monkeypatch, fams)
    monkeypatch.setattr(pr, "_child_cmd", lambda progress, todo: (
        [sys.executable, "-c", "import sys; sys.exit(4)"], str(tmp_path)))
    assert pr.is_blocked("Nabla")
    pr._run(0.0)
    assert pr.gave_up
    assert not pr.is_blocked("Nabla")


def test_real_child_renders_every_font(tmp_path):
    """真子进程把 213 个字体都画一遍（走画布 / 导出同一条文字渲染路），一个不漏。"""
    _app()
    prog = tmp_path / "p.txt"
    import subprocess
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    todo = tmp_path / "todo.json"
    from cutora.ui.font_probe import probe_order as _po
    todo.write_text(json.dumps(_po(), ensure_ascii=False), encoding="utf-8")
    rc = subprocess.run([sys.executable, "-u", "-m", "cutora.ui.font_probe", "--child", str(prog), str(todo)],
                        cwd=REPO, env=env, timeout=600, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    assert rc == 0
    rows = prog.read_text(encoding="utf-8").splitlines()
    from cutora.ui.panels import fonts as F
    done = {r.split("\t")[2] for r in rows if r.startswith(("D\t", "E\t"))}
    assert done == set(F.ALLOWED_FONTS)
    assert rows[-1] == "END"
    # 风险字体排最前
    from cutora.ui.font_probe import probe_order, risky_families
    risky = set(risky_families())
    assert "Bitcount Prop Single Ink" in risky and "Nabla" in risky
    order = probe_order()
    assert set(order[:len(risky)]) == risky


def test_blocked_font_renders_with_default(fresh_probe):
    _app()
    P = fresh_probe
    from cutora.ui.panels import fonts as F
    pr = P.probe()
    pr.results = {"Bitcount Prop Single Ink": {"status": "hang"}}
    pr.complete = True
    pr._refresh_blocked()
    got = []
    F.SUBSTITUTION_LISTENERS.append(got.append)
    try:
        F._SUBSTITUTED.clear()
        f = F.get_font_manager().get_font("Bitcount Prop Single Ink", 40)
        d = F.get_font_manager().get_font(F.DEFAULT_FONT_FAMILY, 40)
        assert f.family() == d.family()
        assert F.render_font_family("Bitcount Prop Single Ink") == F.DEFAULT_FONT_FAMILY
        assert F.sanitize_font_family("Bitcount Prop Single Ink") == "Bitcount Prop Single Ink"   # 工程数据不改
        assert got == ["Bitcount Prop Single Ink"]
        F.get_font_manager().get_font("Bitcount Prop Single Ink", 41)
        assert got == ["Bitcount Prop Single Ink"]     # 只提示一次
        assert F.get_font_manager().get_font("Teko", 40).family() != d.family()
    finally:
        F.SUBSTITUTION_LISTENERS.remove(got.append)
        F._SUBSTITUTED.clear()


def test_picker_disables_blocked_and_tags_checking(fresh_probe, monkeypatch, tmp_path):
    _app()
    P = fresh_probe
    from cutora.ui.widgets import font_picker as FP
    monkeypatch.setattr(FP, "_recent_path", lambda: str(tmp_path / "font_recent.json"))
    pr = P.probe()
    pr.results = {"Bitcount Prop Single Ink": {"status": "hang"}, "Teko": {"status": "slow", "ms": 2400}}
    pr._risky = frozenset({"Nabla", "Bitcount Prop Single Ink"})
    pr._refresh_blocked()
    assert pr.health("Nabla") == "checking"
    host, pop, got = _open_picker("Arimo")
    m = pop.model
    row = m.row_of("Bitcount Prop Single Ink")
    idx = m.index(row, 0)
    assert idx.data(FP.ROLE_HEALTH) == "blocked"
    assert not (m.flags(idx) & Qt.ItemFlag.ItemIsSelectable)
    assert not m.is_font_row(row)
    pop._on_clicked(idx)
    assert got == []                                  # 停用的点了也不选
    assert m.index(m.row_of("Nabla"), 0).data(FP.ROLE_HEALTH) == "checking"
    assert m.index(m.row_of("Teko"), 0).data(FP.ROLE_HEALTH) == "slow"
    # 画一遍（含标签 / 样张说明）不报错
    pop.sample.set_family("Bitcount Prop Single Ink")
    pop.grab()
    pop.sample.set_family("Nabla")
    pop.grab()
    # 体检出结果 → 弹窗跟着刷新
    pr.results["Nabla"] = {"status": "ok", "ms": 30.0}
    P.qt_bridge().changed.emit()
    pr._notify()
    _pump(30)
    assert m.index(m.row_of("Nabla"), 0).data(FP.ROLE_HEALTH) == "ok"
    pop.close()
    host.close()


def test_probe_blocks_whole_font_file_after_one_hang(fresh_probe, tmp_path, monkeypatch):
    """Windows 会把可变字体的每个具名实例登记成一个家族（Bitcount 有几十个）：一个卡死，同文件的全部一起停用，不再挨个等 12 秒。"""
    P = fresh_probe
    fams = ["Bitcount A", "Teko", "Bitcount B", "Bitcount C", "Inter"]
    pr = _fake_probe(P, tmp_path, monkeypatch, fams, hang="Bitcount A")
    monkeypatch.setattr(pr, "_file_of", staticmethod(lambda f: "bitcount.ttf" if f.startswith("Bitcount") else f))
    t0 = time.monotonic()
    pr._run(0.0)
    assert time.monotonic() - t0 < 12                # 只等了一次 HANG_S
    assert pr.complete
    assert pr.blocked() == {"Bitcount A", "Bitcount B", "Bitcount C"}
    assert pr.results["Bitcount B"].get("same_file_as") == "Bitcount A"
    assert pr.status("Teko") == "ok" and pr.status("Inter") == "ok"


def test_child_env_is_render_child(fresh_probe, tmp_path, monkeypatch):
    """体检子进程按「渲染子进程」起：不加载 torch / CUDA。"""
    P = fresh_probe
    pr = _fake_probe(P, tmp_path, monkeypatch, ["Teko"])
    seen = {}
    import cutora.core.proc as proc_mod
    real = proc_mod.popen

    def spy(cmd, **kw):
        seen["env"] = kw.get("env") or {}
        return real(cmd, **kw)

    monkeypatch.setattr(proc_mod, "popen", spy)
    pr._run(0.0)
    from cutora.core.flags import RENDER_CHILD_ENV
    assert seen["env"].get(RENDER_CHILD_ENV) == "1"
    assert pr.complete


def test_rich_editor_uses_safe_family_but_keeps_real_name(fresh_probe):
    """点一下画面 = 选中文字 = 属性面板的文字框用这个字体排版 —— Q170 真正卡死的地方。停用字体在编辑框里用默认字体画，存的仍是原名。"""
    _app()
    P = fresh_probe
    from cutora.ui.panels import fonts as F
    from cutora.ui.panels.text_rich import _RT_FONT_FAMILY_PROP, _base_char_format
    from cutora.ui.panels.text_editors import _set_display_family, _stored_family
    from PySide6.QtGui import QTextCharFormat
    pr = P.probe()
    pr.results = {"Bitcount Prop Single Ink": {"status": "hang"}}
    pr.complete = True
    pr._refresh_blocked()
    fmt = _base_char_format({"font_family": "Bitcount Prop Single Ink", "fontsize": 40})
    assert list(fmt.fontFamilies()) == [F.DEFAULT_FONT_FAMILY]
    assert fmt.property(_RT_FONT_FAMILY_PROP) == "Bitcount Prop Single Ink"
    fmt2 = QTextCharFormat()
    _set_display_family(fmt2, "Bitcount Prop Single Ink")
    assert fmt2.fontFamily() == F.DEFAULT_FONT_FAMILY
    assert _stored_family(fmt2) == "Bitcount Prop Single Ink"
    ok = _base_char_format({"font_family": "Teko", "fontsize": 40})
    assert list(ok.fontFamilies()) == ["Teko"]
    F._SUBSTITUTED.clear()


def test_watchdog_reports_while_still_stuck(monkeypatch):
    """真卡死不恢复也要留下「此刻卡在哪」（以前只在恢复后才写，卡死到底就什么都没有）。"""
    _app()
    from cutora.core import stall_watchdog as SW
    monkeypatch.setattr(SW, "LIVE_MARKS", (0.4,))
    wd = SW.StallWatchdog(threshold_ms=150, poll_ms=20)
    live = []
    monkeypatch.setattr(wd, "_report_live", lambda sec, stack: live.append((sec, stack)))
    wd.start(qt=True)
    try:
        _pump(120)

        def _stuck_forever_here():
            time.sleep(0.9)

        _stuck_forever_here()
        assert live, "卡着的时候没写日志"
        assert "_stuck_forever_here" in live[0][1]
        _pump(200)
    finally:
        wd.stop()


def test_open_probes_source_sizes_once_in_parallel(monkeypatch, tmp_path):
    """打开工程问「画布和素材尺寸不一致」之前要知道每个素材的宽高：去重 + 并行 + 记住，不再串行卡界面。"""
    import cutora.ui.main_window as MW
    calls = []

    def fake_probe(path):
        calls.append(path)
        time.sleep(0.2)
        return {"width": 1080, "height": 1920}

    import cutora.render.readers as R
    monkeypatch.setattr(R, "probe_bitrates", fake_probe)
    MW._SOURCE_WH_CACHE.clear()
    files = []
    for i in range(6):
        f = tmp_path / ("v%d.mp4" % i)
        f.write_bytes(b"x" * (i + 1))
        files.append(str(f))

    class _W:
        _probe_source_wh = MW.MainWindow._probe_source_wh
        _probe_sources_wh = MW.MainWindow._probe_sources_wh

    w = _W()
    t0 = time.perf_counter()
    got = w._probe_sources_wh(files + files)          # 同一素材出现两次只探一次
    dt = time.perf_counter() - t0
    assert set(got.values()) == {(1080, 1920)}
    assert len(calls) == 6
    assert dt < 0.9, dt                                # 6 × 0.2 s 串行要 1.2 s
    calls.clear()
    w._probe_sources_wh(files)
    assert calls == []                                  # 第二次全走缓存
    MW._SOURCE_WH_CACHE.clear()


def test_render_child_reads_probe_cache(fresh_probe, tmp_path, monkeypatch):
    """导出子进程也要认主程序体检出来的停用字体，否则导出时照样卡死。"""
    _app()
    P = fresh_probe
    monkeypatch.setattr(P, "font_signature", lambda: "SIG")
    (tmp_path / "results.json").write_text(json.dumps({
        "sig": "SIG", "complete": True, "results": {"Nabla": {"status": "hang"}}}), encoding="utf-8")
    from cutora.core.flags import RENDER_CHILD_ENV
    monkeypatch.setenv(RENDER_CHILD_ENV, "1")
    monkeypatch.delenv("CUTORA_FONT_PROBE_CHILD", raising=False)
    monkeypatch.setitem(P._STATE, "child_tried", False)
    assert P.is_blocked("Nabla")
    assert not P.is_blocked("Teko")
    P._STATE["child_tried"] = False


def test_watchdog_marks_cpp_stall():
    from cutora.core.stall_watchdog import _annotate
    assert "C++" in _annotate("    app.py:576 run_gui_startup\n    app.py:331 main")
    assert "C++" not in _annotate("    readers.py:702 _open_video_capture\n    app.py:576 run_gui_startup")
