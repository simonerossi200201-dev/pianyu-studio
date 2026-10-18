# -*- coding: utf-8 -*-
"""规格 72：说明书 9 张配图、按钮名对得上、出包带 HTML 不带旧 md。"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-spec72-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

HTML = ROOT / "说明文档" / "使用说明.html"
SHOT_DIR = ROOT / "说明文档" / "分发图"
ZH = ROOT / "orante" / "i18n" / "zh.json"
SHOTS = (
    "01-tasks.png", "02-gemini.png", "03-track.png", "04-generate.png",
    "05-review.png", "06-wizard-start.png", "07-wizard-accounts.png",
    "08-cards.png", "09-settings.png",
)


def _load(name: str):
    p = ROOT / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_backups_exist():
    for rel in (
        "tools/make_share.py.bak-0917manual",
        "tools/test_spec64.py.bak-0917manual",
        "说明文档/使用说明.html.bak-0917manual",
    ):
        assert (ROOT / rel).is_file(), rel


def test_title_unchanged():
    text = HTML.read_text(encoding="utf-8")
    assert "<title>Orante 使用说明</title>" in text


def test_nine_pngs_width():
    names = sorted(p.name for p in SHOT_DIR.iterdir()
                   if p.is_file() and p.suffix.lower() == ".png" and not p.name.startswith("."))
    assert names == list(SHOTS), names
    for name in SHOTS:
        p = SHOT_DIR / name
        assert p.is_file(), name
        with Image.open(p) as im:
            assert im.format == "PNG", f"{name} 不是 PNG：{im.format}"
            w, _h = im.size
            assert w >= 1400, f"{name} 宽度 {w} < 1400"


def test_data_shot_matches_files():
    text = HTML.read_text(encoding="utf-8")
    shots = re.findall(r'data-shot="([^"]+)"', text)
    assert sorted(shots) == sorted(s[:-4] for s in SHOTS), shots
    for s in shots:
        assert (SHOT_DIR / (s + ".png")).is_file(), s


def test_html_no_external_assets():
    text = HTML.read_text(encoding="utf-8")
    assert not re.search(r'<script[^>]+src=["\']https?://', text, re.I)
    assert not re.search(r'<link[^>]+rel=["\']stylesheet["\'][^>]+href=["\']https?://', text, re.I)
    assert not re.search(r'<link[^>]+href=["\']https?://[^"\']+["\'][^>]*rel=["\']stylesheet["\']', text, re.I)


def test_btn_ref_in_zh_json():
    html = HTML.read_text(encoding="utf-8")
    zh = __import__("json").loads(ZH.read_text(encoding="utf-8"))
    values = [str(v).strip() for v in zh.values() if isinstance(v, str) and str(v).strip()]
    refs = re.findall(r'<span class="btn-ref">(.*?)</span>', html, re.S)
    missing = []
    for raw in refs:
        t = re.sub(r"\s+", " ", raw).strip()
        if not t:
            continue
        if any(t in v or v in t for v in values):
            continue
        missing.append(t)
    assert not missing, "btn-ref 在 zh.json 里找不到：\n" + "\n".join(missing)


def test_make_share_html_not_old_md():
    make = _load("make_share")
    rels = [rel.replace("\\", "/") for _src, rel in make.collect_copies(ROOT)]
    assert "说明文档/使用说明.html" in rels
    for name in SHOTS:
        assert f"说明文档/分发图/{name}" in rels, name
    assert "说明文档/使用说明.md" not in rels
    assert "说明文档/使用说明-分发版.md" not in rels


def test_fake_package_has_html_and_shots(tmp_path: Path):
    make = _load("make_share")
    spec64 = _load("test_spec64")
    src = spec64._fake_src(tmp_path)
    dest = tmp_path / "out" / "Orante-分发版-2.0.0-20990101"
    make.build(src, dest, do_scan=False, do_smoke=False, do_zip=False)
    assert (dest / "说明文档" / "使用说明.html").is_file()
    for name in SHOTS:
        assert (dest / "说明文档" / "分发图" / name).is_file(), name
    assert not (dest / "说明文档" / "使用说明.md").exists()
    assert not (dest / "说明文档" / "使用说明-分发版.md").exists()


def test_scan_shots_clean():
    scan = _load("scan_share_leak")
    needles = scan.build_needles()
    hits = scan.scan_tree(SHOT_DIR, needles)
    assert hits == [], "分发图泄露：\n" + "\n".join(hits[:40])
