# -*- coding: utf-8 -*-
"""T27-b: user asset library import + timeline alpha=True."""
from __future__ import annotations

import os

from q14_media import make_circle_png, make_disposal_gif, make_prores_4444

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_user_asset_import_gif_and_alpha_mov(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    gif = make_disposal_gif(str(tmp_path / "logo.gif"), size=16)
    png = make_circle_png(str(tmp_path / "c.png"), w=32, h=32, radius=10, alpha=160)
    mov = make_prores_4444(png, str(tmp_path / "alpha.mov"), seconds=0.15, profile=4)

    from cutora.assets.library import items_for_category
    from cutora.assets.user import import_user_asset, place_on_timeline

    g = import_user_asset(gif)
    m = import_user_asset(mov)
    assert g["type"] == "animated"
    assert m["type"] == "video"
    assert m.get("alpha") is True
    assert os.path.isfile(g["stored_path"])
    assert os.path.isfile(m["stored_path"])
    assert g.get("license") == "自有"
    assert m.get("license") == "自有"

    anim_ids = {it["id"] for it in items_for_category("animated")}
    vid_ids = {it["id"] for it in items_for_category("video")}
    assert g["id"] in anim_ids
    assert m["id"] in vid_ids
    assert g["id"] not in vid_ids
    assert m["id"] not in anim_ids

    from cutora.model.timeline import Timeline
    tl = Timeline(720, 1280, 30.0)
    clip = place_on_timeline(tl, m, start_t=0.0)
    assert clip is not None
    assert getattr(clip, "alpha", False) is True
    src = tl.sources[clip.source_id]
    assert getattr(src, "alpha", False) is True or (
        isinstance(src, dict) and src.get("alpha") is True
    )

    # Panel tabs list the imported items (offscreen; no Qt in parent).
    import subprocess
    py = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["CUTORA_USER_DATA"] = str(tmp_path / "ud")
    code = (
        "from cutora.core.runtime import init_torch_runtime\n"
        "init_torch_runtime(render_child=False)\n"
        "from PySide6.QtWidgets import QApplication\n"
        "from cutora.ui.panels.asset_library import AssetLibraryPanel, CATEGORIES\n"
        "app = QApplication.instance() or QApplication(['q14'])\n"
        "p = AssetLibraryPanel()\n"
        "keys = [k for k,_ in CATEGORIES]\n"
        "assert 'animated' in keys and 'video' in keys\n"
        "p.tabs.setCurrentIndex(keys.index('animated'))\n"
        "names = [p.list.item(i).text() for i in range(p.list.count())]\n"
        "assert any('logo' in n.lower() or 'gif' in n.lower() or n for n in names), names\n"
        "assert p.list.count() >= 1\n"
        "p.tabs.setCurrentIndex(keys.index('video'))\n"
        "assert p.list.count() >= 1\n"
        "print('ok')\n"
    )
    r = subprocess.run(
        [py, "-c", code], cwd=REPO, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=90,
    )
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    assert r.returncode == 0 and "ok" in (r.stdout or ""), out[-4000:]
