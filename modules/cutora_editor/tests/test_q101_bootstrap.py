# -*- coding: utf-8 -*-
"""Q101: ready marker, icon sizes, pack gate, desktop shortcut spec."""
from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PNG = os.path.join(REPO, "resources", "brand", "app.png")
BRAND = os.path.join(REPO, "resources", "brand")
ICO_SIZES = {(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)}
PNG_NAMES = (
    "icon_16.png", "icon_32.png", "icon_48.png", "icon_64.png",
    "icon_128.png", "icon_256.png", "icon_512.png", "icon_1024.png",
)


def _root_with_lock(tmp_path, body: bytes = b"lock-v1\n") -> str:
    root = tmp_path / "app"
    (root / "runtime" / "venv").mkdir(parents=True)
    (root / "requirements.lock").write_bytes(body)
    return str(root)


def _icon_mod():
    path = os.path.join(REPO, "tools", "brand", "icon_from_png.py")
    spec = importlib.util.spec_from_file_location("icon_from_png", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _ico_frame(path: str, size: tuple[int, int]):
    """Pillow ICO has no n_frames; pick a size, then load that image."""
    from PIL import Image
    im = Image.open(path)
    if size not in im.info["sizes"]:
        raise AssertionError("missing %s in %s" % (size, sorted(im.info["sizes"])))
    im.size = size
    im.load()
    return im.copy()


def test_ready_marker_missing_is_not_ready(tmp_path):
    from cutora.core.bootstrap import is_env_ready
    root = _root_with_lock(tmp_path)
    assert is_env_ready(root) is False


def test_ready_marker_wrong_hash_is_not_ready(tmp_path):
    from cutora.core.bootstrap import is_env_ready, ready_marker_path
    root = _root_with_lock(tmp_path)
    path = ready_marker_path(root)
    with open(path, "w", encoding="ascii", newline="\n") as fh:
        fh.write("%s %s\n" % ("ab" * 32, "2026-09-24T00:00:00Z"))
    assert is_env_ready(root) is False


def test_ready_marker_write_matches_lock(tmp_path):
    from cutora.core.bootstrap import (
        is_env_ready, lock_digest, ready_marker_path, write_ready_marker,
    )
    root = _root_with_lock(tmp_path, b"requirements-body\n")
    path = write_ready_marker(root)
    assert os.path.isfile(path)
    assert path == ready_marker_path(root)
    line = open(path, encoding="ascii").read().strip()
    digest, stamp = line.split()
    assert digest == lock_digest(root)
    assert len(digest) == 64
    assert stamp.endswith("Z") and "T" in stamp
    assert is_env_ready(root) is True


def test_fast_path_ready_follows_the_marker(tmp_path):
    from cutora.core.bootstrap import fast_path_ready, write_ready_marker
    root = _root_with_lock(tmp_path, b"fast-path\n")
    assert fast_path_ready(root) is False
    write_ready_marker(root)
    assert fast_path_ready(root) is True


def test_fast_path_ready_false_when_pyvenv_rewrite_fails(tmp_path):
    from cutora.core.bootstrap import fast_path_ready, is_env_ready, write_ready_marker
    root = _root_with_lock(tmp_path, b"fast-path-rewrite\n")
    write_ready_marker(root)
    assert is_env_ready(root) is True
    bundled = os.path.join(root, "runtime", "python")
    os.makedirs(bundled, exist_ok=True)
    with open(os.path.join(bundled, "python.exe"), "wb") as fh:
        fh.write(b"not-a-real-python")
    assert fast_path_ready(root) is False


def test_ready_marker_flips_when_lock_byte_changes(tmp_path):
    from cutora.core.bootstrap import is_env_ready, write_ready_marker
    root = _root_with_lock(tmp_path, b"stable-lock\n")
    write_ready_marker(root)
    assert is_env_ready(root) is True
    lock = os.path.join(root, "requirements.lock")
    data = open(lock, "rb").read()
    flipped = data[:-1] + bytes([data[-1] ^ 0x01])
    assert flipped != data
    with open(lock, "wb") as fh:
        fh.write(flipped)
    assert is_env_ready(root) is False


def test_icon_ico_has_seven_sizes_and_alpha(tmp_path):
    mod = _icon_mod()
    out = tmp_path / "brand"
    ico = mod.build_icons(APP_PNG, str(out))
    from PIL import Image
    opened = Image.open(ico)
    assert ICO_SIZES <= set(opened.info["sizes"])
    big = _ico_frame(ico, (256, 256))
    assert big.size == (256, 256)
    assert big.mode == "RGBA"
    assert "A" in big.getbands()
    lo, hi = big.getchannel("A").getextrema()
    assert lo < 255
    assert hi > 0
    for size in ICO_SIZES:
        frame = _ico_frame(ico, size)
        assert frame.size == size
        assert frame.mode == "RGBA"
        assert "A" in frame.getbands()
    for name, side in (
        ("icon_16.png", 16), ("icon_32.png", 32), ("icon_48.png", 48),
        ("icon_64.png", 64), ("icon_128.png", 128), ("icon_256.png", 256),
        ("icon_512.png", 512), ("icon_1024.png", 1024),
    ):
        from PIL import Image
        png = Image.open(out / name)
        assert png.size == (side, side)
        assert png.mode == "RGBA"


def test_icon_build_is_deterministic(tmp_path):
    mod = _icon_mod()
    a = mod.build_icons(APP_PNG, str(tmp_path / "a"))
    b = mod.build_icons(APP_PNG, str(tmp_path / "b"))
    assert _sha(a) == _sha(b)
    for name in PNG_NAMES:
        assert _sha(os.path.join(tmp_path, "a", name)) == _sha(
            os.path.join(tmp_path, "b", name))


def test_builder_does_not_emit_preserved_marks(tmp_path):
    mod = _icon_mod()
    out = tmp_path / "brand"
    mod.build_icons(APP_PNG, str(out))
    names = set(os.listdir(out))
    assert names == {"app.ico", *PNG_NAMES}
    for kept in (
        "icon_512_light.png", "icon_mono_512.png", "mark_1024.png",
        "splash.png", "splash_bg.png",
    ):
        assert kept not in names


def test_shipped_icons_match_builder(tmp_path):
    mod = _icon_mod()
    out = tmp_path / "brand"
    mod.build_icons(APP_PNG, str(out))
    assert _sha(os.path.join(out, "app.ico")) == _sha(os.path.join(BRAND, "app.ico"))
    for name in PNG_NAMES:
        assert _sha(os.path.join(out, name)) == _sha(os.path.join(BRAND, name))


def _pack_root(tmp_path) -> str:
    return _root_with_lock(tmp_path, b"pack-lock-v1\n")


def test_pack_accepts_matching_ready_marker(tmp_path):
    from cutora.core.bootstrap import write_ready_marker
    from tools.pack_internal import assert_venv_ready_marker
    root = _pack_root(tmp_path)
    write_ready_marker(root)
    assert_venv_ready_marker(root)


def test_pack_refuses_tampered_ready_marker(tmp_path):
    from cutora.core.bootstrap import ready_marker_path, write_ready_marker
    from tools.pack_internal import PackError, assert_venv_ready_marker
    root = _pack_root(tmp_path)
    write_ready_marker(root)
    marker = ready_marker_path(root)
    text = open(marker, encoding="ascii").read()
    flipped = ("0" if text[0] != "0" else "1") + text[1:]
    assert flipped != text
    with open(marker, "w", encoding="ascii", newline="\n") as fh:
        fh.write(flipped)
    with pytest.raises(PackError):
        assert_venv_ready_marker(root)


def test_shortcut_spec_targets_wscript_vbs_and_ico(tmp_path):
    from cutora.core.bootstrap import SHORTCUT_DESCRIPTION, SHORTCUT_NAME, shortcut_spec
    root = tmp_path / "CUTORA"
    root.mkdir()
    spec = shortcut_spec(str(root), system_root=r"C:\Windows")
    assert os.path.normcase(spec["TargetPath"]) == os.path.normcase(
        r"C:\Windows\System32\wscript.exe")
    assert spec["TargetPath"].lower().endswith(r"system32\wscript.exe")
    assert spec["Arguments"] == '"%s"' % os.path.join(str(root), "CUTORA.vbs")
    assert "CUTORA.vbs" in spec["Arguments"]
    assert os.path.normcase(spec["IconLocation"]) == os.path.normcase(
        os.path.join(str(root), "resources", "brand", "app.ico"))
    assert spec["IconLocation"].lower().endswith("app.ico")
    assert spec["WorkingDirectory"] == os.path.abspath(str(root))
    assert spec["Name"] == SHORTCUT_NAME == "片语 Studio"
    assert spec["Description"] == SHORTCUT_DESCRIPTION
    assert "让每一句话，找到它的位置。" in spec["Description"]


def test_shortcut_lnk_roundtrip(tmp_path):
    from cutora.core.bootstrap import shortcut_spec, write_shortcut
    root = tmp_path / "CUTORA"
    root.mkdir()
    spec = shortcut_spec(str(root), system_root=os.environ.get("SystemRoot", r"C:\Windows"))
    lnk = tmp_path / "片语 Studio.lnk"
    write_shortcut(spec, str(lnk))
    assert lnk.is_file()
    script = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "$OutputEncoding = [System.Text.Encoding]::UTF8; "
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:CUTORA_LNK); "
        "Write-Output $s.TargetPath; Write-Output $s.Arguments; "
        "Write-Output $s.WorkingDirectory; Write-Output $s.IconLocation; "
        "Write-Output $s.Description"
    )
    env = os.environ.copy()
    env["CUTORA_LNK"] = str(lnk)
    got = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        env=env, check=True, capture_output=True, text=True,
    )
    lines = [ln.strip() for ln in got.stdout.splitlines() if ln.strip()]
    assert len(lines) >= 5
    assert lines[0].lower().endswith(r"system32\wscript.exe")
    assert "CUTORA.vbs" in lines[1]
    assert os.path.normcase(lines[2]) == os.path.normcase(spec["WorkingDirectory"])
    assert "app.ico" in lines[3].lower()
    assert lines[4] == spec["Description"]


def test_shortcut_created_once_not_restored_after_delete(tmp_path):
    from cutora.core.bootstrap import maybe_create_desktop_shortcut
    root = tmp_path / "app"
    (root / "resources" / "brand").mkdir(parents=True)
    (root / "CUTORA.vbs").write_text("", encoding="ascii")
    (root / "resources" / "brand" / "app.ico").write_bytes(b"ico")
    desktop = tmp_path / "desk"
    desktop.mkdir()
    user = tmp_path / "user"
    user.mkdir()
    made = []

    def fake_write(spec, lnk):
        made.append(spec["TargetPath"])
        os.makedirs(os.path.dirname(lnk), exist_ok=True)
        with open(lnk, "wb") as fh:
            fh.write(b"lnk")

    kwargs = dict(
        desktop=str(desktop), user_data=str(user),
        system_root=r"C:\Windows", write_lnk=fake_write,
    )
    assert maybe_create_desktop_shortcut(str(root), **kwargs) == "created"
    assert len(made) == 1
    assert made[0].lower().endswith(r"system32\wscript.exe")
    lnk = desktop / "片语 Studio.lnk"
    assert lnk.is_file()
    lnk.unlink()
    assert maybe_create_desktop_shortcut(str(root), **kwargs) == "skipped"
    assert len(made) == 1
    assert not lnk.is_file()


def test_shortcut_existing_lnk_is_not_rewritten(tmp_path):
    from cutora.core.bootstrap import maybe_create_desktop_shortcut
    root = tmp_path / "app"
    (root / "resources" / "brand").mkdir(parents=True)
    (root / "CUTORA.vbs").write_text("", encoding="ascii")
    (root / "resources" / "brand" / "app.ico").write_bytes(b"ico")
    desktop = tmp_path / "desk"
    desktop.mkdir()
    lnk = desktop / "片语 Studio.lnk"
    lnk.write_bytes(b"already")
    user = tmp_path / "user"
    user.mkdir()
    made = []

    def fake_write(spec, path):
        made.append(path)

    assert maybe_create_desktop_shortcut(
        str(root), desktop=str(desktop), user_data=str(user),
        system_root=r"C:\Windows", write_lnk=fake_write,
    ) == "exists"
    assert made == []
    assert lnk.read_bytes() == b"already"
    stamp = user / "desktop_shortcut.created"
    assert stamp.is_file()
    assert stamp.read_text(encoding="ascii").startswith("existed ")
