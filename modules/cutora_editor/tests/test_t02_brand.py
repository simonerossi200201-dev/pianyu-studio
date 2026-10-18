# -*- coding: utf-8 -*-
"""T02 brand, paths, migration, render-child flags."""
from __future__ import annotations

import json
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(REPO, "tests", "golden", "_fixture_user_data")


def test_version_and_names():
    import cutora
    from cutora.core.brand import (
        APP_DISPLAY_NAME,
        APP_NAME,
        APP_VERSION,
        PREDECESSOR,
        TAGLINE_EN,
        TAGLINE_ZH,
        format_window_title,
        is_project_path,
        ensure_save_extension,
    )
    assert cutora.__version__ == "7.0.0"
    assert APP_NAME == "CUTORA"
    assert APP_DISPLAY_NAME == "片语 Studio"
    assert APP_VERSION == "7.0.0"
    assert TAGLINE_ZH == "让每一句话，找到它的位置。"
    assert TAGLINE_EN == "Every word finds its place."
    assert PREDECESSOR == "前身 AutoSub v6"
    assert format_window_title() == "片语 Studio"
    assert format_window_title("D:/x/demo.cutora") == "片语 Studio — demo.cutora"
    assert format_window_title(dirty=True) == "*片语 Studio"
    assert format_window_title(extra="工程队列 1/3") == "片语 Studio — 工程队列 1/3"
    assert is_project_path("a.cutora")
    assert is_project_path("a.asproj.json")
    assert is_project_path("a.asproj")
    assert not is_project_path("a.mp4")
    assert ensure_save_extension("foo") == "foo.cutora"
    assert ensure_save_extension("foo.asproj") == "foo.asproj"


def test_brand_assets_exist():
    brand = os.path.join(REPO, "resources", "brand")
    for name in (
        "app.ico", "icon_16.png", "icon_32.png", "icon_48.png", "icon_64.png",
        "icon_128.png", "icon_256.png", "icon_512.png", "icon_1024.png",
        "icon_512_light.png", "icon_mono_512.png", "mark_1024.png",
        "splash_bg.png", "splash.png",
        os.path.join("source", "mark_source.png"),
    ):
        path = os.path.join(brand, name)
        assert os.path.isfile(path), path
    from PIL import Image
    bg = Image.open(os.path.join(brand, "splash_bg.png"))
    assert bg.size == (1280, 720)
    chrome = Image.open(os.path.join(brand, "splash.png"))
    assert chrome.size == (1280, 720)
    assert os.path.isfile(os.path.join(REPO, "CUTORA_CLI.bat"))
    assert os.path.isfile(os.path.join(REPO, "CUTORA_CreateShortcut.bat"))
    assert os.path.isfile(os.path.join(REPO, "tools", "brand", "splash2.py"))
    assert os.path.isfile(os.path.join(REPO, "tools", "brand", "icon2.py"))
    # Old concept / render pipeline must stay gone.
    for gone in (
        "app.svg", "file.svg", "app_256.png", "cutora_file.ico",
        "concept_1.svg", "concept_2.svg", "concept_3.svg",
    ):
        assert not os.path.isfile(os.path.join(brand, gone)), gone
    assert not os.path.isfile(os.path.join(REPO, "tools", "render_brand.py"))


def test_user_data_priority_env(tmp_path, monkeypatch):
    from cutora.core.paths import refresh_cached_paths, resolve_user_data, settings_file
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path))
    refresh_cached_paths()
    assert os.path.normcase(resolve_user_data()) == os.path.normcase(str(tmp_path))
    # Override layout stays flat so golden fixture app_settings.json is found.
    assert settings_file("app_settings.json") == os.path.join(
        os.path.abspath(str(tmp_path)), "app_settings.json")


def test_user_data_priority_portable(tmp_path, monkeypatch):
    from cutora.core import paths as paths
    fake_root = tmp_path / "app"
    fake_root.mkdir()
    (fake_root / "portable.flag").write_text("", encoding="utf-8")
    monkeypatch.delenv("CUTORA_USER_DATA", raising=False)
    monkeypatch.setattr(paths, "program_dir", lambda: str(fake_root))
    got = paths.resolve_user_data()
    assert os.path.normcase(got) == os.path.normcase(str(fake_root / "UserData"))


def test_user_data_default_localappdata(tmp_path, monkeypatch):
    from cutora.core.paths import default_local_appdata, refresh_cached_paths, resolve_user_data
    monkeypatch.delenv("CUTORA_USER_DATA", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    refresh_cached_paths()
    assert os.path.normcase(resolve_user_data()) == os.path.normcase(
        os.path.join(str(tmp_path), "CUTORA"))
    assert os.path.normcase(default_local_appdata()) == os.path.normcase(
        os.path.join(str(tmp_path), "CUTORA"))


def test_settings_file_prefers_flat_fixture(monkeypatch):
    from cutora.core.paths import refresh_cached_paths, settings_file
    monkeypatch.setenv("CUTORA_USER_DATA", FIXTURE)
    refresh_cached_paths()
    path = settings_file("app_settings.json")
    assert os.path.isfile(path)
    assert os.path.normcase(os.path.dirname(path)) == os.path.normcase(FIXTURE)


def test_isolation_localappdata_does_not_override_env(tmp_path, monkeypatch):
    """%LOCALAPPDATA%\\CUTORA mic_amount=0.9 must not leak into CUTORA_USER_DATA."""
    local = tmp_path / "Local"
    local_cutora = local / "CUTORA" / "settings"
    local_cutora.mkdir(parents=True)
    payload = {"audio_fx": {"mic_amount": 0.9, "spatial_wet": 0.1, "mic_texture": True}}
    (local_cutora / "app_settings.json").write_text(
        json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("CUTORA_USER_DATA", FIXTURE)
    from cutora.core.paths import refresh_cached_paths
    refresh_cached_paths()
    from cutora.audio.sfx_library import load_fx_settings
    fx = load_fx_settings()
    assert fx["mic_amount"] == 0.2


def test_migrate_legacy_davinci_settings_keys(tmp_path, monkeypatch):
    """Old persisted keys containing davinci move to dock_*; new key wins."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "app_settings.json").write_text(json.dumps({
        "davinci_ui_compact": True,
        "export_video_mode": "compressed",
        "nested": {"davinci_panel_open": 1, "keep": "yes"},
        "dock_ui_compact": False,
    }), encoding="utf-8")
    dest = tmp_path / "dest"
    dest.mkdir()
    monkeypatch.setenv("CUTORA_USER_DATA", str(dest))
    from cutora.core.paths import refresh_cached_paths
    refresh_cached_paths()
    from cutora.core.migrate_user_data import import_from
    report = import_from(str(src))
    assert "app_settings.json" in report["files"]
    data = json.loads((dest / "app_settings.json").read_text(encoding="utf-8"))
    assert "davinci_ui_compact" not in data
    assert "davinci_panel_open" not in data.get("nested", {})
    assert data["dock_ui_compact"] is False
    assert data["nested"]["dock_panel_open"] == 1
    assert data["nested"]["keep"] == "yes"
    assert data["export_video_mode"] == "compressed"


def test_load_app_settings_migrates_legacy_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path))
    from cutora.core.paths import refresh_cached_paths, settings_file
    refresh_cached_paths()
    path = settings_file("app_settings.json")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"davinci_ui_compact": True, "export_video_mode": "compressed"}, f)
    from cutora.core.history import _load_app_settings
    data = _load_app_settings()
    assert data["dock_ui_compact"] is True
    assert "davinci_ui_compact" not in data
    on_disk = json.loads(open(path, encoding="utf-8").read())
    assert on_disk["dock_ui_compact"] is True
    assert "davinci_ui_compact" not in on_disk


def test_migrate_skips_secret_fields(tmp_path, monkeypatch):

    src = tmp_path / "src"
    src.mkdir()
    (src / "app_settings.json").write_text(json.dumps({
        "export_video_mode": "compressed",
        "gemini_api_key": "should-not-copy",
        "hf_token": "nope",
        "audio_fx": {"mic_amount": 0.2, "client_secret": "x"},
    }), encoding="utf-8")
    dest = tmp_path / "dest"
    dest.mkdir()
    monkeypatch.setenv("CUTORA_USER_DATA", str(dest))
    from cutora.core.paths import refresh_cached_paths
    refresh_cached_paths()
    from cutora.core.migrate_user_data import import_from
    report = import_from(str(src))
    assert "app_settings.json" in report["files"]
    data = json.loads((dest / "app_settings.json").read_text(encoding="utf-8"))
    assert "gemini_api_key" not in data
    assert "hf_token" not in data
    assert "client_secret" not in data.get("audio_fx", {})
    assert data["audio_fx"]["mic_amount"] == 0.2


def test_render_child_reads_both_names(monkeypatch):
    from cutora.core import flags
    monkeypatch.delenv("CUTORA_RENDER_CHILD", raising=False)
    monkeypatch.delenv("AUTOSUB_RENDER_CHILD", raising=False)
    assert flags.is_render_child() is False
    monkeypatch.setenv("AUTOSUB_RENDER_CHILD", "1")
    assert flags.is_render_child() is True
    monkeypatch.delenv("AUTOSUB_RENDER_CHILD", raising=False)
    monkeypatch.setenv("CUTORA_RENDER_CHILD", "1")
    assert flags.is_render_child() is True


def test_model_paths_under_models():
    from cutora.core.paths import MODELS, get_model_paths, pin_hf_home
    pin_hf_home()
    assert os.environ.get("HF_HOME") == MODELS
    paths = get_model_paths()
    for key in ("asr_path", "align_path"):
        p = paths[key]
        assert os.path.normcase(p).startswith(os.path.normcase(MODELS))
        assert ":" not in os.path.relpath(p, MODELS)


def test_roma_tokens():
    from cutora.ui.theme.tokens import (
        ACCENT, BG0, INFO, TRACK, TRACK_AUDIO, hex_rgb,
    )
    assert BG0 == "#141210"
    assert ACCENT == "#C8A24A"
    assert INFO == "#8FA89B"
    assert TRACK["audio"] == TRACK_AUDIO == "#3E6F53"      # Q169：油画配色 · 绿（中低饱和 / 中低亮度）
    assert hex_rgb(ACCENT) == (200, 162, 74)


def test_launcher_bats_exist():
    for name in ("CUTORA.bat", "CUTORA_Install.bat", "CUTORA_CLI.bat",
                 "CUTORA_CreateShortcut.bat", "cutora_mcp.bat"):
        assert os.path.isfile(os.path.join(REPO, name)), name


def test_paths_point_at_final_assets():
    from cutora.core.paths import (
        APP_ICON, APP_ICON_PNG, FILE_ICON, MARK_PNG, SPLASH_PNG,
    )
    assert APP_ICON.endswith("app.ico")
    assert APP_ICON_PNG.endswith("icon_256.png")
    assert SPLASH_PNG.endswith("splash_bg.png")
    assert MARK_PNG.endswith("mark_1024.png")
    assert FILE_ICON == APP_ICON
    for path in (APP_ICON, APP_ICON_PNG, MARK_PNG, SPLASH_PNG):
        assert os.path.isfile(path), path

