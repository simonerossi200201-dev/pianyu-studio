# -*- coding: utf-8 -*-
"""T05 P1-A: stylepack.update refuses builtin; 14 builtin files stay put."""
from __future__ import annotations

import hashlib
import json
import os
import shutil

import pytest

from cutora.core.commands import CommandError
from cutora.subtitle.stylepack import (
    StylepackError, load_pack, update_pack_dir,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILTIN = os.path.join(REPO, "resources", "stylepacks")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _builtin_hashes() -> dict[str, str]:
    out = {}
    for root, _, names in os.walk(BUILTIN):
        for fn in names:
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, BUILTIN).replace("\\", "/")
            out[rel] = _sha256(path)
    return out


def test_update_pack_dir_refuses_builtin():
    before = _builtin_hashes()
    src = os.path.join(BUILTIN, "amber-karaoke")
    with pytest.raises(StylepackError) as err:
        update_pack_dir(src, {"version": 1, "style": {"fontsize": 1}})
    assert "内置" in str(err.value)
    src_cmd = open(
        os.path.join(REPO, "cutora", "core", "cmds", "light.py"), encoding="utf-8",
    ).read()
    assert "def cmd_stylepack_update" in src_cmd
    assert "update_pack_dir" in src_cmd
    assert "CommandError" in src_cmd
    after = _builtin_hashes()
    assert after == before


def test_update_user_pack_keeps_id_path_and_sets_updated_at(tmp_path):
    src = os.path.join(BUILTIN, "amber-karaoke")
    dest = tmp_path / "amber-karaoke"
    shutil.copytree(src, dest)
    before = load_pack(str(dest))
    body = {
        "version": before["version"],
        "style": dict(before["style"]),
        "layout_rules": dict(before["layout_rules"]),
        "keyword_rules": list(before["keyword_rules"]),
        "animation": dict(before["animation"]),
    }
    body["style"]["fontsize"] = 77
    rec = update_pack_dir(str(dest), body)
    assert rec["path"] == os.path.abspath(dest)
    assert rec["id"] == before["id"]
    assert rec["updated_at"]
    after = load_pack(str(dest))
    assert after["id"] == before["id"]
    assert after["path"] == before["path"]
    assert after["style"]["fontsize"] == 77
    assert after["meta"].get("updated_at") == rec["updated_at"]
    # builtin tree untouched
    packed = load_pack(src)
    assert packed["style"].get("fontsize") != 77


def test_update_then_load_pack_matches_saved_body(tmp_path):
    src = os.path.join(BUILTIN, "minimal-white")
    dest = tmp_path / "minimal-white"
    shutil.copytree(src, dest)
    pack = load_pack(str(dest))
    body = {
        "version": pack["version"],
        "style": dict(pack["style"]),
        "layout_rules": dict(pack["layout_rules"]),
        "keyword_rules": list(pack["keyword_rules"]),
        "animation": dict(pack["animation"]),
    }
    update_pack_dir(str(dest), body)
    style_path = dest / "style.json"
    on_disk = json.loads(style_path.read_text(encoding="utf-8"))
    dumped = json.dumps(body, ensure_ascii=False, indent=2) + "\n"
    assert style_path.read_text(encoding="utf-8") == dumped
    assert on_disk["style"] == body["style"]


def test_gallery_has_edit_and_duplicate_for_builtin():
    src = open(
        os.path.join(REPO, "cutora", "ui", "panels", "style_gallery.py"),
        encoding="utf-8",
    ).read()
    assert 'tr("编辑")' in src
    assert 'tr("复制为新预设")' in src
    assert "stylepack.update" in src
    assert "_place_edit_btn" in src
    assert "EDIT_BTN_REST_OPACITY = 0.82" in src
    assert "EDIT_BTN_HOVER_OPACITY = 1.0" in src
    assert "_sync_edit_btns" in src
    assert 'getattr(win, "tabs", None)' in src
    assert "stylepack.apply" in src
    assert "enter_pack_edit" in src
    assert "_focus_style_tab" in src
    assert "setOpacity(0.15)" not in src
    assert 'tr("重命名")' in src
    assert 'tr("删除")' in src
    assert 'tr("导出")' in src
    bar = open(
        os.path.join(REPO, "cutora", "ui", "panels", "subtitle_style.py"),
        encoding="utf-8",
    ).read()
    assert "enter_pack_edit" in bar
    assert "正在编辑风格包" in bar
    assert 'setObjectName("packEditBar")' in bar
    assert "self._pack_edit_save.setVisible(not builtin)" in bar


def test_fourteen_builtin_hashes_stable_after_update_attempts():
    before = _builtin_hashes()
    assert len({rel.split("/")[0] for rel in before}) == 30
    src = os.path.join(BUILTIN, "chant")
    with pytest.raises(StylepackError):
        update_pack_dir(src, {"version": 1, "style": {}})
    after = _builtin_hashes()
    assert after == before
