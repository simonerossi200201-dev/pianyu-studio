# -*- coding: utf-8 -*-
"""T04e style packs: zip-slip, apply animation, missing font, list."""
from __future__ import annotations

import json
import os
import sys
import zipfile

import pytest

from cutora.subtitle.stylepack import (
    StylepackError, apply_pack_to_task, export_cutorastyle, import_cutorastyle,
    list_pack_dirs, load_pack, subtitle_bbox, user_has_custom_animation,
)


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_builtin_five_packs_exist():
    ids = {os.path.basename(p) for p in list_pack_dirs() if "resources" in p.replace("\\", "/")}
    for name in ("minimal-white", "bar-black", "amber-karaoke", "chant", "podcast-card"):
        assert name in ids, (name, ids)
        pack = load_pack(os.path.join(REPO, "resources", "stylepacks", name))
        assert pack["meta"]["author"] == "CUTORA"
        assert "style" in pack
        assert pack.get("preview") and os.path.isfile(pack["preview"]), name


def test_apply_keeps_animation_when_unchecked():
    pack = {
        "id": "x",
        "style": {"font_family": "Arimo", "color_text": "#ffffff", "anim_in_type": "fade"},
        "animation": {"anim_in_type": "fade", "anim_in_duration": 0.4},
        "layout_rules": {"max_chars_per_line": 42, "max_lines": 2},
    }
    task = {"style": {"anim_in_type": "metronome", "fontsize": 40}}
    apply_pack_to_task(task, pack, apply_animation=False)
    assert task["style"]["anim_in_type"] == "metronome"
    apply_pack_to_task(task, pack, apply_animation=True)
    assert task["style"]["anim_in_type"] == "fade"
    assert user_has_custom_animation({"anim_in_type": "metronome"}) is True
    assert user_has_custom_animation({"anim_in_type": "none"}) is False


def test_zip_slip_rejected(tmp_path):
    evil = tmp_path / "evil.cutorastyle"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../../evil.txt", b"nope")
        zf.writestr("style.json", b"{}")
        zf.writestr("meta.json", b"{}")
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(StylepackError):
        import_cutorastyle(str(evil), dest_dir=str(dest))
    assert not (tmp_path / "evil.txt").exists()
    assert not list(tmp_path.glob("**/evil.txt"))


def test_export_roundtrip(tmp_path):
    src = os.path.join(REPO, "resources", "stylepacks", "minimal-white")
    zpath = tmp_path / "a.cutorastyle"
    export_cutorastyle(src, str(zpath))
    dest = import_cutorastyle(str(zpath), dest_dir=str(tmp_path))
    pack = load_pack(dest)
    assert pack["meta"]["name"] == "极简白"


def test_security_scan_flags_extractall():
    src = open(
        os.path.join(REPO, "tools", "security_scan.ps1"), encoding="utf-8",
    ).read()
    assert "zip-extractall" in src
    assert "extractall" in src
    pack = open(
        os.path.join(REPO, "cutora", "subtitle", "stylepack.py"), encoding="utf-8",
    ).read()
    assert ".extractall(" not in pack


SYSTEM_FONTS = {
    "Arial", "Times New Roman", "Segoe UI", "Microsoft YaHei", "SimSun",
    "Calibri", "Tahoma", "Helvetica",
}


def test_builtin_packs_use_bundled_fonts_only():
    for name in ("minimal-white", "bar-black", "amber-karaoke", "chant", "podcast-card"):
        pack = load_pack(os.path.join(REPO, "resources", "stylepacks", name))
        fam = str((pack.get("style") or {}).get("font_family") or "")
        assert fam, name
        assert fam not in SYSTEM_FONTS, (name, fam)
        assert "CapCut" not in json.dumps(pack, ensure_ascii=False)
        assert pack["meta"].get("license") == "OFL-1.1"


def test_pack_max_chars_matches_fontsize():
    """Larger type must declare a tighter character cap (chant 38 → 28)."""
    rows = []
    for name in ("minimal-white", "bar-black", "amber-karaoke", "chant", "podcast-card"):
        pack = load_pack(os.path.join(REPO, "resources", "stylepacks", name))
        fs = int((pack.get("style") or {}).get("fontsize") or 0)
        cap = int((pack.get("layout_rules") or {}).get("max_chars_per_line") or 0)
        assert 16 <= cap <= 40, (name, cap)
        rows.append((fs, cap, name))
    chant = load_pack(os.path.join(REPO, "resources", "stylepacks", "chant"))
    assert chant["layout_rules"]["max_chars_per_line"] == 28
    assert int(chant["style"]["fontsize"]) >= 38
    # bigger font → smaller or equal cap
    by_fs = sorted(rows, key=lambda r: r[0])
    assert by_fs[-1][1] <= by_fs[0][1]


def test_bar_black_display_name_is_navy():
    pack = load_pack(os.path.join(REPO, "resources", "stylepacks", "bar-black"))
    assert pack["meta"]["name"] == "字幕条·深蓝"


def test_chant_is_v2_without_holy_glow():
    pack = load_pack(os.path.join(REPO, "resources", "stylepacks", "chant"))
    body = pack.get("style") or {}
    assert int((json.loads(open(
        os.path.join(REPO, "resources", "stylepacks", "chant", "style.json"),
        encoding="utf-8").read()).get("version") or 1)) >= 2
    assert pack["animation"].get("anim_in_type") != "holy_glow"
    fx = body.get("fx") or {}
    assert (fx.get("glow") or {}).get("sigma_em", 0) == 0
    assert fx.get("fill", {}).get("type") == "metal"
    assert body.get("font_family") == "Playfair Display"


def test_amber_karaoke_uses_accent():
    pack = load_pack(os.path.join(REPO, "resources", "stylepacks", "amber-karaoke"))
    fill = (pack["style"].get("fx") or {}).get("fill") or {}
    assert "#E0BE68" in list(fill.get("colors") or [])
    assert pack["style"].get("font_family") == "SN Pro"


def test_legacy_absolute_pack_migrates_onto_landscape():
    pack = {
        "id": "legacy",
        "style": {"font_family": "Arimo", "pos_x": 540, "pos_y": 1450, "fontsize": 34},
        "animation": {},
        "layout_rules": {},
    }
    task = {"timeline": {"width": 1920, "height": 1080}, "style": {}}
    apply_pack_to_task(task, pack)
    assert task["style"].get("anchor") == "bottom-center"
    assert task["style"]["pos_y"] < 1080
    x, y, bw, bh = subtitle_bbox(task["style"], 1920, 1080)
    assert y + bh <= 1080.5


def test_cannot_delete_builtin(tmp_path):
    from cutora.subtitle.stylepack import delete_pack_dir
    src = os.path.join(REPO, "resources", "stylepacks", "minimal-white")
    with pytest.raises(StylepackError):
        delete_pack_dir(src)


def test_pack_bbox_stays_on_three_canvases():
    """Same pack on 1080x1920 / 1920x1080 / 1080x1080: box inside, relative pos match."""
    pack = load_pack(os.path.join(REPO, "resources", "stylepacks", "minimal-white"))
    canvases = ((1080, 1920), (1920, 1080), (1080, 1080))
    rel = []
    for w, h in canvases:
        task = {"timeline": {"width": w, "height": h}, "style": {}}
        apply_pack_to_task(task, pack, apply_animation=True)
        x, y, bw, bh = subtitle_bbox(task["style"], w, h, ["Hello friends."])
        assert x >= -0.5, (w, h, x, y, bw, bh)
        assert y >= -0.5, (w, h, x, y, bw, bh)
        assert x + bw <= w + 0.5, (w, h, x, y, bw, bh)
        assert y + bh <= h + 0.5, (w, h, x, y, bw, bh)
        cy = y + bh / 2.0
        rel.append(cy / float(h))
    assert max(rel) - min(rel) < 0.04, rel


def test_zip_rejects_disallowed_ext(tmp_path):
    zpath = tmp_path / "bad.cutorastyle"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("style.json", b"{}")
        zf.writestr("meta.json", b"{}")
        zf.writestr("payload.exe", b"MZ")
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(StylepackError):
        import_cutorastyle(str(zpath), dest_dir=str(dest))
    assert not (dest / "payload.exe").exists()


def test_gallery_tab_wired_in_main_window():
    src = open(
        os.path.join(REPO, "cutora", "ui", "main_window.py"), encoding="utf-8",
    ).read()
    assert "StyleGalleryPanel" in src
    assert "style_gallery" in src
    assert 'addTab(self.style_gallery' in src
    assert "setCurrentWidget(gallery)" in src or "setCurrentWidget(self.style_gallery)" in src
    gal = open(
        os.path.join(REPO, "cutora", "ui", "panels", "style_gallery.py"),
        encoding="utf-8",
    ).read()
    assert "stylepack.apply" in gal
    assert "CARD_W" in gal or "QSize(CARD_W" in gal
    assert "应用到当前视频" not in gal
    assert "QDialogButtonBox" not in gal
    assert "同时应用该风格的动画" in gal
    assert "Ctrl+Z 撤销" in gal
    assert "stylepackAnimSwitch" in gal
    assert "把当前样式存成风格包" in gal
    assert "存成风格包" in gal
    thumbs = open(
        os.path.join(REPO, "cutora", "ui", "panels", "style_thumbs.py"),
        encoding="utf-8",
    ).read()
    assert "CARD_W = 180" in thumbs
    assert "paint_pack_on_frame" in thumbs
    assert "CARD_ASPECT" in thumbs
    assert "round_card" in thumbs
    assert "dim_and_blur" not in thumbs
    assert "sample_event" in thumbs
    assert "render_pack_card" in thumbs
    gal_src = open(
        os.path.join(REPO, "cutora", "ui", "panels", "style_gallery.py"),
        encoding="utf-8",
    ).read()
    plus_add = gal_src.find("self.list.addItem(plus)")
    pack_loop = gal_src.find("for pack in visible:")
    assert 0 < plus_add < pack_loop
    assert "far_playhead" in gal_src
    assert "gal.reload()" in open(
        os.path.join(REPO, "cutora", "ui", "main_window.py"),
        encoding="utf-8",
    ).read()
    assert "setSingleShot(False)" in gal_src
    assert "gallery_hover_t" in gal_src


def test_paint_event_does_not_swallow_exceptions():
    src = open(
        os.path.join(REPO, "cutora", "ui", "canvas.py"), encoding="utf-8",
    ).read()
    assert 'print("[PaintError]"' not in src
    assert "exception" in src
    assert "CUTORA_PERF" in src


def test_task_from_window_curr_idx_zero():
    from cutora.subtitle.io import _task_from_window, window_curr_idx

    t0, t1 = {"id": "a", "style": {}}, {"id": "b", "style": {}}

    class Win:
        tasks = [t0, t1]
        curr_idx = 0

    assert window_curr_idx(Win()) == 0
    idx, task = _task_from_window(Win(), None, "")
    assert idx == 0
    assert task is t0


def test_stylepack_commands_headless_and_apply(tmp_path):
    import json
    import shutil
    from cutora.core.command_schema import check_schema
    from cutora.core.commands import get_bus, get_registry

    names = (
        "stylepack.list", "stylepack.apply", "stylepack.import_file",
        "stylepack.export_file", "stylepack.save_current",
        "stylepack.delete", "stylepack.rename", "stylepack.update",
        "stylepack.convert_preset",
    )
    reg = get_registry()
    for name in names:
        spec = reg.get(name)
        assert spec.headless is True, name
        assert len(spec.doc_zh) >= 40, name
        check_schema(spec.params_schema)

    bus = get_bus()
    listed = bus.execute("stylepack.list", {}, source="cli")
    assert listed["count"] >= 5
    ids = {row["id"] for row in listed["packs"]}
    assert "amber-karaoke" in ids

    media = tmp_path / "video.mp4"
    src = os.path.join(REPO, "tests", "golden", "_media", "g01", "video.mp4")
    shutil.copy2(src, media)
    proj = tmp_path / "project.asproj.json"
    proj.write_text(json.dumps({
        "version": 2,
        "tasks": [{"video": str(media), "path": str(media),
                   "style": {"anim_in_type": "metronome", "fontsize": 40},
                   "segments": []}],
    }), encoding="utf-8")
    pack_dir = os.path.join(REPO, "resources", "stylepacks", "minimal-white")
    bus.execute("stylepack.apply", {
        "path": pack_dir, "apply_animation": False,
        "media": str(media), "project": str(proj),
    }, source="cli")
    style = json.loads(proj.read_text(encoding="utf-8"))["tasks"][0]["style"]
    assert style["anim_in_type"] == "metronome"
    assert style.get("font_family") == "Arimo"


def _child_env():
    import os as _os
    env = dict(_os.environ)
    env["PYTHONPATH"] = REPO + _os.pathsep + env.get("PYTHONPATH", "")
    env["QT_QPA_PLATFORM"] = "offscreen"
    ffmpeg = _os.path.join(REPO, "runtime", "ffmpeg")
    path = env.get("PATH", "")
    if _os.path.normcase(ffmpeg) not in _os.path.normcase(path):
        env["PATH"] = ffmpeg + _os.pathsep + path
    return env


def _pump(app, ms=40):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


def run_apply_refresh_suite() -> int:
    """Offscreen: apply 圣咏, panel reloads, heartbeat does not revert."""
    import tempfile
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault(
        "CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_t04e_apply_"))
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from cutora.app import apply_ui_locale
    apply_ui_locale()
    from PySide6.QtWidgets import QApplication, QStyleFactory
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
    from cutora.ui.main_window import MainWindow
    from cutora.core.commands import get_bus

    g08 = os.path.join(REPO, "tests", "golden", "g08_stylepack_amber", "project.asproj.json")
    chant = os.path.join(REPO, "resources", "stylepacks", "chant")
    app = QApplication.instance() or QApplication(["t04e-apply"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)
    win = MainWindow()
    win.show()
    _pump(app, 80)
    win.show_editor()
    _pump(app, 40)
    win.command_project_open(path=g08)
    _pump(app, 200)
    assert win.tasks, "g08 did not load"
    assert win.curr_idx == 0, win.curr_idx
    before_font = (win.tasks[0].get("style") or {}).get("font_family")
    bus = get_bus()
    bus.bind_window(win)
    bus.execute("stylepack.apply", {
        "path": chant, "apply_animation": True, "media_id": 0,
    }, source="ui")
    _pump(app, 40)
    style = win.tasks[0]["style"]
    assert style.get("font_family") == "Playfair Display", style.get("font_family")
    assert int(style.get("fontsize") or 0) == 76
    assert before_font != "Playfair Display", before_font
    panel = win.sub_panel
    assert panel.current_data is style
    assert panel.sub_font.currentText() == "Playfair Display"
    assert int(panel.sub_size.value()) == 76
    gal = getattr(win, "style_gallery", None)
    if gal is not None:
        src = open(
            os.path.join(REPO, "cutora", "ui", "panels", "style_gallery.py"),
            encoding="utf-8",
        ).read()
        assert "应用到当前视频" not in src
        assert gal.chk_anim is not None
    for _ in range(20):
        win.sync_ui()
        app.processEvents()
    assert win.tasks[0]["style"]["font_family"] == "Playfair Display"
    assert win.sub_panel.sub_font.currentText() == "Playfair Display"
    print("T04E_APPLY_OK", flush=True)
    try:
        win.media_player.stop()
        win.close()
    except Exception:
        pass
    os._exit(0)


def test_stylepack_apply_reloads_panel_and_survives_heartbeat():
    import subprocess
    py = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
    proc = subprocess.run(
        [py, os.path.join(REPO, "tests", "test_t04e_stylepack.py"), "--apply-refresh"],
        cwd=REPO,
        env=_child_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    assert proc.returncode == 0, out
    assert "T04E_APPLY_OK" in (proc.stdout or ""), out


if __name__ == "__main__":
    if "--apply-refresh" in sys.argv:
        try:
            raise SystemExit(run_apply_refresh_suite())
        except SystemExit:
            raise
        except BaseException:
            import traceback
            traceback.print_exc()
            raise SystemExit(1)
