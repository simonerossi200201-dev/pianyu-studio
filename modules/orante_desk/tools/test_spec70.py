# -*- coding: utf-8 -*-
"""规格 70：扩图覆盖库机位/动作改成卡片，只改显示。"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-spec70-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

HAN = re.compile(r"[\u4e00-\u9fff]")
ZH = json.loads((ROOT / "orante" / "i18n" / "zh.json").read_text(encoding="utf-8"))
GEMINI = (ROOT / "ui" / "js" / "panels" / "gemini.js").read_text(encoding="utf-8")
CSS = (ROOT / "ui" / "css" / "app.css").read_text(encoding="utf-8")
PORT = 8770
URL = f"http://127.0.0.1:{PORT}/tools/_cov70.html"


def test_backups_exist():
    for rel in (
        "ui/js/panels/gemini.js.bak-0916covui",
        "ui/css/app.css.bak-0916covui",
        "orante/i18n/zh.json.bak-0916covui",
    ):
        assert (ROOT / rel).is_file(), rel


def test_no_table_in_source():
    assert '<table class="cov-tbl">' not in GEMINI
    assert 'class="cov-list"' in GEMINI
    assert 'class="cov-row"' in GEMINI
    assert 'class="cov-name"' in GEMINI
    assert "function addCam()" in GEMINI
    assert "function addAct()" in GEMINI
    assert "function delRow(" in GEMINI
    assert "function moveRow(" in GEMINI
    assert "onBlockInput('expand')" in GEMINI
    assert "onBlockBlur('expand')" in GEMINI
    assert "v-model=\"row.frontal\"" in GEMINI
    assert "coverage.cameras" in GEMINI
    assert "coverage.actions" in GEMINI


def test_labels_chinese_no_rank():
    assert ZH["gemini.cov.rank"] == "权重"
    assert ZH["gemini.cov.class"] == "类别"
    assert ZH["gemini.cov.phase"] == "阶段"
    assert ZH["gemini.cov.frontal"] == "正脸机位"
    for k in (
        "gemini.cov.id", "gemini.cov.name", "gemini.cov.class",
        "gemini.cov.frontal", "gemini.cov.rank", "gemini.cov.phase",
        "gemini.cov.text",
    ):
        v = ZH[k]
        assert HAN.search(v), k
        assert "rank" not in v.lower(), (k, v)
    assert "主力" in ZH["gemini.cov.class.title"]
    assert "侧面" in ZH["gemini.cov.class.title"]
    assert "空间" in ZH["gemini.cov.class.title"]
    assert "收尾" in ZH["gemini.cov.class.title"]
    assert ZH["gemini.cov.phase.start"] == "起势"
    assert ZH["gemini.cov.phase.develop"] == "展开"
    assert ZH["gemini.cov.phase.hold"] == "保持"
    assert ZH["gemini.cov.phase.resolve"] == "收束"


def test_css_no_inner_scroll_and_type():
    assert ".cov-list" in CSS
    assert "min-width:120px" in CSS
    assert "max-height:360px" not in CSS.split(".cov-list")[0][-80:] + CSS.split(".cov-list")[1][:400]
    assert "cov-tbl-wrap{overflow:auto;max-height:360px" not in CSS.replace(" ", "")
    assert "font-size:12.5px" in CSS


def test_node_ui():
    script = ROOT / "tools" / "test_spec70_ui.js"
    r = subprocess.run(
        ["node", str(script)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert r.returncode == 0, f"node 失败\n{r.stdout}\n{r.stderr}"
    assert "spec70 ui ok" in (r.stdout or "")
    assert "no table.cov-tbl" in (r.stdout or "")


class _Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def log_message(self, *a):
        return


def test_layout_playwright():
    from playwright.sync_api import sync_playwright

    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), _Handler)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(viewport={"width": 900, "height": 900})
            page = ctx.new_page()
            page.goto(URL, wait_until="domcontentloaded")
            page.wait_for_function("() => window.__cov70 && window.__cov70.ready", timeout=15000)

            assert page.locator("table.cov-tbl").count() == 0
            sums = page.locator(".cov-sum")
            assert sums.count() >= 4
            first = sums.nth(0).inner_text()
            assert "04" in first and "观众视角" in first and "B" in first and "权重 3" in first
            assert "rank" not in first.lower()

            page.locator("#btn-cam-toggle").click()
            page.locator("#btn-act-toggle").click()
            page.wait_for_selector(".cov-row .cov-name")

            names = page.locator(".cov-row .cov-name")
            assert names.count() >= 2
            for i in range(names.count()):
                w = names.nth(i).evaluate("el => el.offsetWidth")
                assert w >= 120, f"name[{i}] width {w}"

            rows = page.locator(".cov-list[data-kind='cameras'] .cov-row")
            assert rows.count() >= 1
            card_w, ta_w = rows.nth(0).evaluate(
                """el => {
                  const ta = el.querySelector('textarea.cov-text');
                  return [el.getBoundingClientRect().width, ta.getBoundingClientRect().width];
                }"""
            )
            assert ta_w >= card_w * 0.9, (ta_w, card_w)

            labels = page.locator(".cov-meta span").all_inner_texts()
            joined = " ".join(labels)
            assert "rank" not in joined.lower()
            assert "类别" in joined
            assert "权重" in joined
            assert "阶段" in joined

            cls_title = page.locator(".cov-meta[title*='主力']").first.get_attribute("title")
            assert cls_title and "侧面" in cls_title
            rank_title = page.locator(".cov-meta[title*='看得见']").first.get_attribute("title")
            assert rank_title and "1" in rank_title

            before = page.evaluate("() => window.__cov70.inputCalls")
            ta = page.locator(".cov-list[data-kind='cameras'] textarea.cov-text").first
            ta.click()
            ta.type("加")
            after = page.evaluate("() => window.__cov70.inputCalls")
            assert after > before, (before, after)

            names_before = page.evaluate("() => window.__cov70.coverage.cameras.map(c => c.name)")
            page.locator(".cov-list[data-kind='cameras'] .cov-ops .x").nth(1).click()
            names_after = page.evaluate("() => window.__cov70.coverage.cameras.map(c => c.name)")
            assert names_after[0] == names_before[1], (names_before, names_after)
            n = page.evaluate("() => window.__cov70.coverage.cameras.length")
            page.locator(".cov-list[data-kind='cameras'] .cov-ops .x").nth(2).click()
            n2 = page.evaluate("() => window.__cov70.coverage.cameras.length")
            assert n2 == n - 1

            ls = page.evaluate("() => localStorage.getItem('orante.cov.cameras.open')")
            assert ls == "1"
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function("() => window.__cov70 && window.__cov70.ready", timeout=15000)
            page.wait_for_selector(".cov-list[data-kind='cameras'] .cov-row")
            assert page.locator(".cov-list[data-kind='cameras'] .cov-row").count() >= 1

            page.set_viewport_size({"width": 480, "height": 800})
            page.evaluate("() => { document.getElementById('g-block-expand').style.width = '360px'; }")
            overflow = page.evaluate(
                """() => {
                  const list = document.querySelector('.cov-list[data-kind="cameras"]');
                  return { sw: list.scrollWidth, cw: list.clientWidth, ow: list.offsetWidth };
                }"""
            )
            assert overflow["sw"] <= overflow["cw"] + 1, overflow
            name_w, row_w = page.locator(".cov-list[data-kind='cameras'] .cov-row").first.evaluate(
                """el => {
                  const n = el.querySelector('.cov-name');
                  return [n.getBoundingClientRect().width, el.getBoundingClientRect().width];
                }"""
            )
            assert name_w >= 120, name_w
            assert name_w >= row_w * 0.7, (name_w, row_w)

            browser.close()
    finally:
        httpd.shutdown()
