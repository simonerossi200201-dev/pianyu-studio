# -*- coding: utf-8 -*-
"""规格 69：设置页每个参数都有人话中文名和说明。"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-spec69-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import config  # noqa: E402
from orante.engines import flow_api  # noqa: E402

HAN = re.compile(r"[\u4e00-\u9fff]")
ZH = json.loads((ROOT / "orante" / "i18n" / "zh.json").read_text(encoding="utf-8"))
EN = json.loads((ROOT / "orante" / "i18n" / "en.json").read_text(encoding="utf-8"))
SETTINGS_JS = (ROOT / "ui" / "js" / "parts" / "settings.js").read_text(encoding="utf-8")
STORE_JS = (ROOT / "ui" / "js" / "store.js").read_text(encoding="utf-8")

SELECT_COUNTS = {
    "gemini.mode": 2,
    "flow.image_model_key": 2,
    "track.strip_view": 2,
    "engine.flow.mode": 2,
    "generate.mode": 3,
    "ui.lang": 2,
    "ui.open_mode": 2,
    "ui.bench.density": 3,
    "images.local_mode": 3,
}


def _missing_zh(defaults: dict) -> list[str]:
    """遍历 DEFAULTS：不在 HIDDEN_KEYS 的键必须有非空、含中文的 set.key / set.hint。"""
    bad = []
    for k in defaults:
        if config.is_hidden_key(k):
            continue
        nk, nh = "set.key." + k, "set.hint." + k
        name, hint = ZH.get(nk), ZH.get(nh)
        if not isinstance(name, str) or not name.strip():
            bad.append(f"{k}: 缺 set.key")
            continue
        if not isinstance(hint, str) or not hint.strip():
            bad.append(f"{k}: 缺 set.hint")
            continue
        if not HAN.search(name):
            bad.append(f"{k}: set.key 没有中文: {name!r}")
        if not HAN.search(hint):
            bad.append(f"{k}: set.hint 没有中文: {hint!r}")
        if name.strip() == hint.strip():
            bad.append(f"{k}: set.hint 和 set.key 相同")
    return bad


def test_backups_exist():
    for rel in (
        "orante/config.py.bak-0916labels",
        "orante/i18n/zh.json.bak-0916labels",
        "ui/js/parts/settings.js.bak-0916labels",
        "ui/css/app.css.bak-0916labels",
        "ui/js/store.js.bak-0916labels",
    ):
        assert (ROOT / rel).is_file(), rel


def test_every_defaults_key_has_chinese():
    bad = _missing_zh(config.DEFAULTS)
    assert not bad, "缺中文：\n" + "\n".join(bad)


def test_new_defaults_key_without_zh_is_red():
    fake = dict(config.DEFAULTS)
    fake["brand.new_key"] = 1
    bad = _missing_zh(fake)
    assert any("brand.new_key" in x for x in bad), bad


def test_hidden_keys_match():
    assert "ui.current_task" in config.HIDDEN_KEYS
    assert "setup.done" in config.HIDDEN_KEYS
    assert config.is_hidden_key("ui.current_task")
    assert config.is_hidden_key("setup.done")
    assert config.is_hidden_key("prompts.desc")
    assert config.is_hidden_key("prompts.expand")
    assert not config.is_hidden_key("expand.dup_dist")
    assert "ui.current_task" in SETTINGS_JS
    assert "setup.done" in SETTINGS_JS
    assert "HIDDEN_KEYS" in SETTINGS_JS
    assert "prompts." in SETTINGS_JS


def test_template_no_key_as_label():
    assert "k.split('.').slice(1)" not in SETTINGS_JS
    assert "labelOf(k)" in SETTINGS_JS
    assert "hintOf(k)" in SETTINGS_JS
    assert "kv-key" in SETTINGS_JS
    assert "resetOne" in SETTINGS_JS
    assert "set.reset_one" in SETTINGS_JS
    assert "ctrlOf(k)==='select'" in SETTINGS_JS


def test_group_titles_plain_chinese():
    want = {
        "set.group.workspace": "工作区",
        "set.group.archive": "自动归档",
        "set.group.split": "拆段",
        "set.group.ai": "多模态接口",
        "set.group.gemini": "Gemini",
        "set.group.expand": "多镜头扩图",
        "set.group.track": "配图轨",
        "set.group.engine": "引擎",
        "set.group.ui": "界面",
        "set.group.generate": "生成方式",
    }
    for k, v in want.items():
        assert ZH.get(k) == v, (k, ZH.get(k), v)


def test_select_option_i18n_counts():
    for key, n in SELECT_COUNTS.items():
        prefix = f"set.opt.{key}."
        opts = [k for k in ZH if k.startswith(prefix)]
        assert len(opts) == n, (key, opts, n)
        for ok_ in opts:
            val = ZH[ok_]
            assert isinstance(val, str) and val.strip()
            if key == "ui.lang" and ok_.endswith(".en"):
                continue
            assert HAN.search(val), (ok_, val)
    models = list(flow_api.MODEL_FAMILY)
    assert len(models) >= 1
    assert "Veo 3.1 - Lite [Lower Priority]" in models


def test_i18n_fallback_never_key_name():
    assert "store.i18nZh" in STORE_JS
    assert "set.key." in STORE_JS
    assert "set.hint." in STORE_JS
    # 英文缺的回落到中文：en 没有的键，zh 有
    assert ZH["set.key.expand.dup_dist"]
    assert EN["set.key.expand.dup_dist"]
    assert EN["set.key.expand.dup_dist"] != "expand.dup_dist"
    assert "set.key." not in EN["set.key.expand.dup_dist"]


def test_chain_upscale_not_in_settings():
    assert "chain.upscale_every" not in config.DEFAULTS


def test_en_json_exists_and_valid():
    assert (ROOT / "orante" / "i18n" / "en.json").is_file()
    assert EN.get("set.reset_one")
    assert EN.get("set.pick")


def test_node_ui_render():
    script = ROOT / "tools" / "test_spec69_ui.js"
    r = subprocess.run(
        ["node", str(script)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert r.returncode == 0, f"node 失败\n{r.stdout}\n{r.stderr}"
    assert "spec69 ui ok" in (r.stdout or "")
    assert "labels are Chinese" in (r.stdout or "")
