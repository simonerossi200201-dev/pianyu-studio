# -*- coding: utf-8 -*-
"""Q95: one style pack, two doors (gallery cards and the preset combo)."""
from __future__ import annotations

import ast
import json
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REMOVED = (
    "load_subtitle_presets",
    "save_named_subtitle_preset",
    "delete_subtitle_preset",
    "get_default_subtitle_preset_name",
    "set_default_subtitle_preset_name",
    "save_default_subtitle_style",
    "export_subtitle_presets",
    "import_subtitle_presets",
    "apply_subtitle_preset",
)
_SKIP_WALK = {
    "build", ".git", "__pycache__", "runtime", ".venv", "venv",
    ".mypy_cache", ".pytest_cache",
}


@pytest.fixture
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q95"])


@pytest.fixture
def private_ud(tmp_path, monkeypatch):
    """Own user-data dir. The isolated runner shares one session dir."""
    ud = tmp_path / "ud"
    ud.mkdir()
    monkeypatch.setenv("CUTORA_USER_DATA", str(ud))
    from cutora.core.paths import refresh_cached_paths
    refresh_cached_paths()
    yield ud
    refresh_cached_paths()


def _presets_root() -> str:
    from cutora.core.paths import user_presets_dir
    root = user_presets_dir()
    os.makedirs(root, exist_ok=True)
    return root


def _write_legacy(presets: dict, default_name: str) -> str:
    root = _presets_root()
    path = os.path.join(root, "subtitle_style_presets.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({
            "default": {},
            "default_preset_name": default_name,
            "presets": presets,
        }, fh, ensure_ascii=False)
    return path


def _host(qapp):
    from PySide6.QtWidgets import QVBoxLayout, QWidget
    from cutora.core.commands import get_bus
    from cutora.ui.panels.style_gallery import StyleGalleryPanel
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel

    host = QWidget()
    host.tasks = [{
        "style": {
            "font_family": "Arimo",
            "fontsize": 40,
            "color_text": "#ABCDEF",
            "anim_in_type": "none",
            "bold": True,
        },
        "segments": [],
        "dims": (1080, 1920),
        "layout_rules": {
            "max_chars_per_line": 32,
            "max_lines": 2,
            "balance_lines": True,
        },
    }]
    host.curr_idx = 0
    host.bus = get_bus()
    host.bus.bind_window(host)
    lay = QVBoxLayout(host)
    panel = SubtitleStylePanel()
    lay.addWidget(panel)
    host.sub_panel = panel
    panel.load_data(host.tasks[0]["style"])
    gal = StyleGalleryPanel(host, host)
    lay.addWidget(gal)
    host.style_gallery = gal
    host.resize(480, 720)
    return host


def _entries():
    from cutora.subtitle.stylepack import list_style_entries
    return list_style_entries()


def _combo_names(panel) -> list[str]:
    combo = panel.sub_preset_combo
    names = []
    for i in range(combo.count()):
        text = combo.itemText(i)
        if text.startswith("【默认】"):
            text = text.replace("【默认】", "", 1).strip()
        names.append(text)
    return names


def _gallery_names(gal) -> list[str]:
    from PySide6.QtCore import Qt
    from cutora.ui.panels.style_thumbs import SAVE_ROLE
    names = []
    for i in range(gal.list.count()):
        item = gal.list.item(i)
        if item.data(Qt.ItemDataRole.UserRole) == SAVE_ROLE:
            continue
        names.append(item.text())
    return names


def _user_pack_dirs() -> list[str]:
    from cutora.subtitle.stylepack import user_dir
    root = user_dir()
    if not os.path.isdir(root):
        return []
    out = []
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if os.path.isfile(os.path.join(path, "style.json")):
            out.append(path)
    return out


def test_q95_migrates_existing_presets(qapp, private_ud):
    legacy = _write_legacy({
        "甲": {"color_text": "#112233", "fontsize": 48, "font_family": "Arimo"},
        "乙": {"color_text": "#445566", "fontsize": 70, "font_family": "Arimo"},
        "丙": {"color_text": "#778899", "fontsize": 30, "font_family": "Arimo"},
    }, "乙")
    host = _host(qapp)
    rows = [r for r in _entries() if not r["builtin"]]
    names = {r["name"] for r in rows}
    assert names == {"甲", "乙", "丙"}, names
    assert len(_user_pack_dirs()) == 3
    defaults = [r for r in rows if r["default"]]
    assert [r["name"] for r in defaults] == ["乙"]
    meta = json.load(open(os.path.join(defaults[0]["path"], "meta.json"), encoding="utf-8"))
    assert meta.get("default") is True
    jia = next(r for r in rows if r["name"] == "甲")
    body = json.load(open(os.path.join(jia["path"], "style.json"), encoding="utf-8"))
    fill = ((body.get("style") or {}).get("fx") or {}).get("fill") or {}
    assert [str(c).lower() for c in (fill.get("colors") or [])][:1] == ["#112233"]
    assert set(_combo_names(host.sub_panel)) == set(_gallery_names(host.style_gallery))
    assert {"甲", "乙", "丙"} <= set(_combo_names(host.sub_panel))
    assert {"甲", "乙", "丙"} <= set(_gallery_names(host.style_gallery))
    shown = [
        host.sub_panel.sub_preset_combo.itemText(i)
        for i in range(host.sub_panel.sub_preset_combo.count())
        if host.sub_panel.sub_preset_combo.itemText(i).startswith("【默认】")
    ]
    assert len(shown) == 1 and "乙" in shown[0]
    assert not os.path.isfile(legacy)
    renamed = os.path.join(_presets_root(), "subtitle_style_presets.json.migrated")
    assert os.path.isfile(renamed)
    marker = os.path.join(_presets_root(), "subtitle_style_presets.migrated.marker")
    assert os.path.isfile(marker)
    data = json.load(open(renamed, encoding="utf-8"))
    data["presets"]["丁"] = {"color_text": "#010101", "fontsize": 20, "font_family": "Arimo"}
    with open(renamed, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    from cutora.subtitle.stylepack import ensure_subtitle_presets_migrated
    ensure_subtitle_presets_migrated()
    host.style_gallery.reload()
    again = {r["name"] for r in _entries() if not r["builtin"]}
    assert again == {"甲", "乙", "丙"}
    assert len(_user_pack_dirs()) == 3
    host.close()


def test_q95_one_object_two_entries(qapp, monkeypatch, private_ud):
    from PySide6.QtWidgets import QInputDialog
    host = _host(qapp)
    before = len(_user_pack_dirs())
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("联通甲", True))
    host.sub_panel._preset_add()
    assert len(_user_pack_dirs()) == before + 1
    assert "联通甲" in _combo_names(host.sub_panel)
    assert "联通甲" in _gallery_names(host.style_gallery)
    assert not os.path.isfile(os.path.join(_presets_root(), "subtitle_style_presets.json"))
    calls = {"n": 0}

    def _two(*_a, **_k):
        calls["n"] += 1
        return ("联通乙", True) if calls["n"] == 1 else ("", True)

    monkeypatch.setattr(QInputDialog, "getText", _two)
    host.style_gallery._save_current()
    assert len(_user_pack_dirs()) == before + 2
    assert {"联通甲", "联通乙"} <= set(_combo_names(host.sub_panel))
    assert {"联通甲", "联通乙"} <= set(_gallery_names(host.style_gallery))
    assert set(_combo_names(host.sub_panel)) == set(_gallery_names(host.style_gallery))
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("联通甲改", True))
    path = next(r["path"] for r in _entries() if r["name"] == "联通甲")
    host.sub_panel.sub_preset_combo.blockSignals(True)
    host.sub_panel.sub_preset_combo.setCurrentIndex(
        host.sub_panel.sub_preset_combo.findData(path))
    host.sub_panel.sub_preset_combo.blockSignals(False)
    host.sub_panel._preset_rename()
    names = {r["name"] for r in _entries() if not r["builtin"]}
    assert "联通甲改" in names and "联通甲" not in names
    assert "联通甲改" in _gallery_names(host.style_gallery)
    assert len(_user_pack_dirs()) == before + 2
    host.close()


def test_q95_delete_from_either_side(qapp, monkeypatch, private_ud):
    from PySide6.QtWidgets import QMessageBox
    from cutora.subtitle.stylepack import save_task_as_pack
    host = _host(qapp)
    save_task_as_pack(host.tasks[0], "删甲", tags="预设")
    save_task_as_pack(host.tasks[0], "删乙", tags="预设")
    host.style_gallery.reload()
    asked = []

    def _ask(_parent, _title, text, *_a, **_k):
        asked.append(str(text))
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", _ask)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_a, **_k: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *_a, **_k: None)
    path = next(r["path"] for r in _entries() if r["name"] == "删甲")
    host.sub_panel.block_signals = True
    host.sub_panel.sub_preset_combo.blockSignals(True)
    host.sub_panel.sub_preset_combo.setCurrentIndex(
        host.sub_panel.sub_preset_combo.findData(path))
    host.sub_panel.sub_preset_combo.blockSignals(False)
    host.sub_panel.block_signals = False
    host.sub_panel._preset_delete()
    assert asked and "风格列表和预设下拉都会移除" in asked[-1]
    left = {r["name"] for r in _entries() if not r["builtin"]}
    assert "删甲" not in left and "删乙" in left
    assert "删甲" not in _combo_names(host.sub_panel)
    assert "删甲" not in _gallery_names(host.style_gallery)
    path_b = next(r["path"] for r in _entries() if r["name"] == "删乙")
    host.style_gallery._delete_pack(path_b)
    assert "风格列表和预设下拉都会移除" in asked[-1]
    left = {r["name"] for r in _entries() if not r["builtin"]}
    assert "删乙" not in left
    assert "删乙" not in _combo_names(host.sub_panel)
    assert "删乙" not in _gallery_names(host.style_gallery)
    host.close()


def test_q95_builtin_not_deletable(qapp, monkeypatch, private_ud):
    from PySide6.QtWidgets import QMessageBox
    from cutora.core.commands import CommandError
    from cutora.subtitle.stylepack import builtin_dir, load_pack
    host = _host(qapp)
    path = os.path.join(builtin_dir(), "minimal-white")
    with pytest.raises(CommandError):
        host.bus.execute("stylepack.delete", {"path": path}, source="ui")
    assert os.path.isfile(os.path.join(path, "style.json"))
    warnings = []

    def _warn(*args, **_k):
        warnings.append(" ".join(str(a) for a in args))

    monkeypatch.setattr(QMessageBox, "warning", _warn)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Yes)
    host.sub_panel.sub_preset_combo.blockSignals(True)
    host.sub_panel.sub_preset_combo.setCurrentIndex(
        host.sub_panel.sub_preset_combo.findData(os.path.abspath(path)))
    host.sub_panel.sub_preset_combo.blockSignals(False)
    host.sub_panel._preset_delete()
    host.style_gallery._delete_pack(path)
    assert os.path.isfile(os.path.join(path, "meta.json"))
    assert any("另存为" in w for w in warnings)
    before = len(_user_pack_dirs())
    host.style_gallery._duplicate_pack(load_pack(path))
    assert len(_user_pack_dirs()) == before + 1
    made = [r for r in _entries() if r["name"] == "极简白 副本"]
    assert len(made) == 1 and not made[0]["builtin"]
    assert "极简白 副本" in _combo_names(host.sub_panel)
    assert "极简白 副本" in _gallery_names(host.style_gallery)
    host.close()


def test_q95_import_export_single_format(qapp, monkeypatch, tmp_path, private_ud):
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    from cutora.core.paths import refresh_cached_paths
    from cutora.subtitle.stylepack import save_task_as_pack
    host = _host(qapp)
    saved = save_task_as_pack(host.tasks[0], "导出甲", tags="预设")
    host.style_gallery.reload()
    dest = tmp_path / "pack.cutorastyle"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        lambda *_a, **_k: (str(dest), "CUTORA style (*.cutorastyle)"))
    monkeypatch.setattr(QMessageBox, "information", lambda *_a, **_k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_a, **_k: None)
    host.sub_panel.sub_preset_combo.blockSignals(True)
    host.sub_panel.sub_preset_combo.setCurrentIndex(
        host.sub_panel.sub_preset_combo.findData(saved["path"]))
    host.sub_panel.sub_preset_combo.blockSignals(False)
    host.sub_panel._preset_export()
    assert dest.is_file() and dest.suffix == ".cutorastyle"
    import zipfile
    with zipfile.ZipFile(dest) as zf:
        names = set(zf.namelist())
    assert "style.json" in names and "meta.json" in names
    host.close()
    clean = tmp_path / "clean_user"
    clean.mkdir()
    monkeypatch.setenv("CUTORA_USER_DATA", str(clean))
    refresh_cached_paths()
    host2 = _host(qapp)
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName",
        lambda *_a, **_k: (str(dest), "CUTORA style (*.cutorastyle *.zip)"))
    host2.sub_panel._preset_import()
    assert "导出甲" in _combo_names(host2.sub_panel)
    assert "导出甲" in _gallery_names(host2.style_gallery)
    assert not os.path.isfile(os.path.join(_presets_root(), "subtitle_style_presets.json"))
    import cutora.subtitle.presets as presets_mod
    tree = ast.parse(open(presets_mod.__file__, encoding="utf-8").read())
    defined = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing = sorted(defined & set(REMOVED))
    assert missing == [], missing
    host2.close()


def test_q95_apply_same_style(qapp, private_ud):
    import copy
    from PySide6.QtCore import Qt
    from cutora.subtitle.stylepack import save_task_as_pack
    host = _host(qapp)
    host.tasks[0]["style"] = {
        "font_family": "Arimo",
        "fontsize": 55,
        "color_text": "#112233",
        "anim_in_type": "fade",
        "anim_in_duration": 0.4,
        "bold": True,
    }
    saved = save_task_as_pack(host.tasks[0], "对照包", tags="预设")
    host.style_gallery.reload()
    start = {
        "font_family": "Arimo",
        "fontsize": 22,
        "color_text": "#ABCDEF",
        "anim_in_type": "metronome",
        "bold": True,
    }
    host.tasks[0]["style"] = copy.deepcopy(start)
    host.tasks[0].pop("stylepack_id", None)
    host.sub_panel.load_data(host.tasks[0]["style"])
    host.style_gallery._load_anim_pref()
    assert host.style_gallery.chk_anim.isChecked() is False
    item = None
    for i in range(host.style_gallery.list.count()):
        cand = host.style_gallery.list.item(i)
        if cand.data(Qt.ItemDataRole.UserRole) == saved["path"]:
            item = cand
            break
    assert item is not None
    host.style_gallery._clicked(item)
    style_gallery = copy.deepcopy(host.tasks[0]["style"])
    host.tasks[0]["style"] = copy.deepcopy(start)
    host.tasks[0].pop("stylepack_id", None)
    host.sub_panel.block_signals = True
    host.sub_panel.load_data(host.tasks[0]["style"])
    host.sub_panel.block_signals = False
    host.sub_panel.sub_preset_combo.blockSignals(True)
    host.sub_panel.sub_preset_combo.setCurrentIndex(0)
    host.sub_panel.sub_preset_combo.blockSignals(False)
    idx = host.sub_panel.sub_preset_combo.findData(saved["path"])
    assert idx > 0
    host.sub_panel.sub_preset_combo.setCurrentIndex(idx)
    style_combo = copy.deepcopy(host.tasks[0]["style"])
    assert style_gallery == style_combo
    assert style_combo.get("anim_in_type") == "metronome"
    fill = ((style_combo.get("fx") or {}).get("fill") or {}).get("colors") or []
    assert [str(c).lower() for c in fill][:1] == ["#112233"]
    assert style_combo.get("fontsize") != start["fontsize"]
    host.close()


def _iter_py():
    for folder in ("cutora", "tests", "tools"):
        root = os.path.join(REPO, folder)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames
                if d not in _SKIP_WALK and not d.startswith(".")
            ]
            for name in filenames:
                if name.endswith(".py"):
                    yield os.path.join(dirpath, name)


def test_q95_no_orphan_callers():
    hits = []
    for path in _iter_py():
        rel = os.path.relpath(path, REPO).replace("\\", "/")
        try:
            tree = ast.parse(open(path, encoding="utf-8").read(), filename=rel)
        except SyntaxError as exc:
            hits.append("%s syntax %s" % (rel, exc))
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in REMOVED:
                    hits.append("%s:%d def %s" % (rel, node.lineno, node.name))
            elif isinstance(node, ast.Name) and node.id in REMOVED:
                hits.append("%s:%d name %s" % (rel, node.lineno, node.id))
            elif isinstance(node, ast.Attribute) and node.attr in REMOVED:
                hits.append("%s:%d attr %s" % (rel, node.lineno, node.attr))
    assert hits == [], "\n".join(hits)
