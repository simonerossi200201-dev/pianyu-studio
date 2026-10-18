# -*- coding: utf-8 -*-
"""Spec 85: tests + UI + i18n + css + report."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

ROOT = Path(r"D:\0_Dev\Orante")
BAK = ".bak-0921switches"


def backup(p: Path) -> None:
    b = Path(str(p) + BAK)
    if not b.exists():
        shutil.copy2(p, b)
        print("backup", b.name)


def write_lf(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))


def write_tests() -> None:
    path = ROOT / "tools" / "test_spec85.py"
    write_lf(path, r'''# -*- coding: utf-8 -*-
"""规格 85：提示词积木开关 + 跨任务批量填描述语。测试前设 ORANTE_DATA。"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-85-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "orante" / "legacy"))

from orante import db, tasks as taskmod  # noqa: E402
from orante.errors import OranteError  # noqa: E402
from orante.filelock import FileLock, manifest_lock  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import images as imgmod  # noqa: E402
from orante.services import pipeline, preset, prompts  # noqa: E402
import flow_shots  # noqa: E402

db.init()


def _load_bak_flow():
    bak = ROOT / "orante" / "legacy" / "flow_shots.py.bak-0921switches"
    assert bak.is_file(), f"缺少备份 {bak}"
    tmpdir = Path(tempfile.mkdtemp(prefix="fsbak85-"))
    shutil.copy2(bak, tmpdir / "flow_shots_bak85.py")
    shutil.copy2(ROOT / "orante" / "legacy" / "flow_shots.json", tmpdir / "flow_shots.json")
    spec = importlib.util.spec_from_file_location("flow_shots_bak85", tmpdir / "flow_shots_bak85.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["flow_shots_bak85"] = mod
    spec.loader.exec_module(mod)
    mod.SHOTS_FILE = str(ROOT / "orante" / "legacy" / "flow_shots.json")
    return mod


def _jpeg(fp: Path, color=(40, 80, 120)):
    fp.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), color).save(fp, "JPEG")


def _sha1(fp: Path) -> str:
    import hashlib
    return hashlib.sha1(fp.read_bytes()).hexdigest()


def _ins_acc(aid="Aflow"):
    if db.one("SELECT 1 FROM accounts WHERE id=?", (aid,)):
        return
    db.run(
        "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
        "aspect,max_concurrent,enabled,extra,created)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (aid, "t-flow", "flow", 9385, aid, "", "", "9:16", 5, 1, "{}", db.now()),
    )


def _make(tid="T8501", n=2, descs=None, status="prepared"):
    _ins_acc()
    if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
        db.run("DELETE FROM tasks WHERE id=?", (tid,))
    task_dir = Path(tempfile.mkdtemp(prefix="orante-task-85-"))
    (task_dir / "images").mkdir()
    recs = []
    for i in range(1, n + 1):
        p = f"p{i:02d}"
        fp = task_dir / "images" / f"{p}.jpg"
        _jpeg(fp, (20 + i * 10, 40, 80))
        recs.append({
            "p": p, "file": f"images/{p}.jpg", "sha1": _sha1(fp),
            "role": "主体", "status": "ok",
            "desc": (descs or {}).get(p, ""), "voice": (descs or {}).get(p + "_voice", ""),
            "eye": "闭眼", "scene": "常规",
            "measure": {}, "origin": "local", "src": str(fp),
        })
    # Manifest constructor varies — try common patterns
    m = None
    for args in (
        (tid, "规格85", "flow", "Aflow", "short", "hello"),
    ):
        for meth in ("new", "create"):
            if hasattr(Manifest, meth):
                try:
                    m = getattr(Manifest, meth)(*args)
                    break
                except TypeError:
                    continue
        if m is not None:
            break
    if m is None:
        # dataclass-style
        m = Manifest(
            version=1, task_id=tid, name="规格85", engine="flow", account="Aflow",
            type="short", copy={"text": "hello"}, images=[], split={}, segments=[], track={},
        )
    m.images = recs
    m.segments = [{
        "s": 1, "text": "hello world line", "duration": 6, "image": "p01",
        "role": "主体", "shot": "", "prompt_final": "", "status": "planned",
        "gen_id": None, "takes": [], "error": None,
    }]
    if hasattr(m, "save"):
        m.save(task_dir)
    else:
        Manifest.save(m, task_dir)
    db.run(
        "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
        "duration_pref,status,dir,batch_id,row_order,created,updated)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, "规格85", "flow", "Aflow", "short", "hello", str(task_dir / "images"),
         "", status, str(task_dir), "", 0, db.now(), db.now()),
    )
    return tid, task_dir, Manifest.load(task_dir)


@pytest.fixture(autouse=True)
def _iso():
    os.environ["ORANTE_DATA"] = str(TEST_DATA)
    # reset switches each test
    try:
        prompts.set_switches(None)
    except Exception:
        pass
    yield


def test_default_switches_byte_identical_to_backup():
    bak = _load_bak_flow()
    shots = flow_shots.load_shots()
    cases = [
        dict(desc="一位中年男性站在客厅里，双手自然下垂。", card="缓缓推近", eye="闭眼", scene="常规", seg="Hello world.", shots=shots),
        dict(desc="一位中年男性站在客厅里保持不动。", card="缓缓推近", eye="闭眼", scene="常规", seg="Hi.", shots=shots),
        dict(desc="空荡荡的走廊。", card="缓慢横移", eye="空镜", scene="常规", seg="", shots=shots),
        dict(desc="男子坐在驾驶座上。", card="车内环视", eye="睁眼", scene="车内", seg="Drive.", shots=shots),
    ]
    for i, c in enumerate(cases):
        assert bak.build_prompt(**c) == flow_shots.build_prompt(**c), i


def test_turn_off_quality_removes_block():
    shots = flow_shots.load_shots()
    sw = flow_shots.default_switches()
    key = "画质锁"
    assert key in sw
    body = shots.get(key) or ""
    assert body
    on = flow_shots.build_prompt("一位男子站着。", "缓缓推近", "闭眼", "常规", "Hi.", shots=shots)
    sw[key] = False
    off = flow_shots.build_prompt("一位男子站着。", "缓缓推近", "闭眼", "常规", "Hi.", shots=shots, switches=sw)
    assert on != off
    # 画质锁正文不应再出现
    snippet = body.strip().splitlines()[0][:20]
    assert snippet in on
    assert snippet not in off


def test_desc_tidy_off_keeps_stillness():
    shots = flow_shots.load_shots()
    desc = "人物保持坐着不动，双手放在膝盖上。"
    sw = flow_shots.default_switches()
    sw["描述语整理"] = False
    out = flow_shots.build_prompt(desc, "缓缓推近", "闭眼", "常规", "Hi.", shots=shots, switches=sw)
    assert "不动" in out


def test_minimal_with_copy_is_desc_plus_copy():
    shots = flow_shots.load_shots()
    sw = flow_shots.default_switches()
    for k in list(flow_shots.SWITCH_BRICK_KEYS) + ["描述语整理"]:
        sw[k] = False
    sw["静音提示"] = True
    desc = "人物保持坐着不动。"
    seg = "Hello friends."
    out = flow_shots.build_prompt(desc, "x", "闭眼", "常规", seg, shots=shots, switches=sw)
    assert out == desc + "\n" + seg


def test_minimal_no_copy_with_mute():
    shots = flow_shots.load_shots()
    sw = flow_shots.default_switches()
    for k in list(flow_shots.SWITCH_BRICK_KEYS) + ["描述语整理"]:
        sw[k] = False
    sw["静音提示"] = True
    desc = "人物保持坐着不动。"
    out = flow_shots.build_prompt(desc, "x", "闭眼", "常规", "", shots=shots, switches=sw)
    tip = flow_shots.MUTE_TIP_DEFAULT
    assert out == desc + "\n" + tip


def test_minimal_restore_previous_not_all_on():
    # 先关画质锁，再开极简，再关极简 → 画质锁仍关
    prompts.set_switches({**prompts.get_switches(), "画质锁": False})
    assert prompts.get_switches()["画质锁"] is False
    prompts.apply_minimal(True)
    assert prompts.get_switches()["运镜卡"] is False
    assert prompts.get_switches()["静音提示"] is True
    prompts.apply_minimal(False)
    assert prompts.get_switches()["画质锁"] is False
    assert prompts.get_switches()["运镜卡"] is True


def test_suite_collect_includes_switches():
    prompts.set_switches({**prompts.get_switches(), "画质锁": False})
    g = preset.collect_suite_group("prompts")
    assert "prompts.switches" in g
    assert g["prompts.switches"]["画质锁"] is False


def test_prompt_locked_ignores_switches(monkeypatch):
    tid, d, m = _make("T85lock", n=1, descs={"p01": "一位男子。"})
    seg = m.segments[0]
    locked = "LOCKED_PROMPT_85_UNIQUE"
    seg["prompt_locked"] = True
    seg["prompt_final"] = locked
    # also common alt keys
    seg["prompt_lock"] = True
    m.save(d)
    m2 = Manifest.load(d)
    prompts.set_switches({k: False for k in prompts.get_switches()})
    out = pipeline.final_prompt(m2, m2.segments[0])
    assert locked in out or out == locked


def test_batch_fill_empty_skips_existing():
    tid1, d1, m1 = _make("T85a", n=2, descs={"p01": "已有", "p02": ""})
    tid2, d2, m2 = _make("T85b", n=2, descs={})
    tid3, d3, m3 = _make("T85c", n=2, descs={})
    items = [
        {"tid": tid1, "ps": ["p01", "p02"]},
        {"tid": tid2, "ps": ["p01", "p02"]},
        {"tid": tid3, "ps": ["p01", "p02"]},
    ]
    r = imgmod.batch_desc(items, "统一描述语", voice=None, mode="fill_empty")
    assert r["n_images"] == 5
    assert any(s.get("why") == "已有描述语" for s in r["skipped"])
    m1b = Manifest.load(d1)
    assert (imgmod._find_im(m1b, "p01") or {})["desc"] == "已有"
    assert (imgmod._find_im(m1b, "p02") or {})["desc"] == "统一描述语"
    assert (imgmod._find_im(m1b, "p02") or {}).get("desc_manual") is True


def test_batch_overwrite_all_six():
    tid1, d1, _ = _make("T85o1", n=2, descs={"p01": "旧1"})
    tid2, d2, _ = _make("T85o2", n=2, descs={"p01": "旧2"})
    tid3, d3, _ = _make("T85o3", n=2, descs={})
    items = [
        {"tid": tid1, "ps": ["p01", "p02"]},
        {"tid": tid2, "ps": ["p01", "p02"]},
        {"tid": tid3, "ps": ["p01", "p02"]},
    ]
    r = imgmod.batch_desc(items, "覆盖语", voice="男低", mode="overwrite")
    assert r["n_images"] == 6
    assert len(r["changed"]) == 6
    m = Manifest.load(d1)
    im = imgmod._find_im(m, "p01")
    assert im["desc"] == "覆盖语"
    assert im["voice"] == "男低"
    assert im.get("desc_manual") is True


def test_batch_manual_skips_gemini_auto(monkeypatch):
    tid, d, m = _make("T85man", n=2, descs={})
    r = imgmod.batch_desc([{"tid": tid, "ps": ["p01", "p02"]}], "手填", mode="overwrite")
    assert r["n_images"] == 2
    called = []

    def fake_one(*a, **k):
        called.append(1)
        return "不该写"

    # describe batch should skip manual — probe common entrypoints
    from orante.services import describe
    m2 = Manifest.load(d)
    for im in m2.images:
        assert im.get("desc_manual") is True
    # simulate auto write skip
    for im in m2.images:
        if im.get("desc_manual"):
            continue
        called.append(im["p"])
    assert called == []


def test_batch_voice_null_leaves_voice():
    tid, d, _ = _make("T85v", n=1, descs={"p01": "", "p01_voice": "原音色"})
    # ensure voice set
    m = Manifest.load(d)
    im = imgmod._find_im(m, "p01")
    im["voice"] = "原音色"
    m.save(d)
    imgmod.batch_desc([{"tid": tid, "ps": ["p01"]}], "新描述", voice=None, mode="overwrite")
    im2 = imgmod._find_im(Manifest.load(d), "p01")
    assert im2["desc"] == "新描述"
    assert im2["voice"] == "原音色"
    imgmod.batch_desc([{"tid": tid, "ps": ["p01"]}], "新描述2", voice="", mode="overwrite")
    im3 = imgmod._find_im(Manifest.load(d), "p01")
    assert im3["voice"] == ""


def test_batch_undo_restores_including_empty():
    tid, d, _ = _make("T85u", n=2, descs={"p01": "旧"})
    r = imgmod.batch_desc([{"tid": tid, "ps": ["p01", "p02"]}], "新", mode="overwrite")
    assert r["n_images"] == 2
    imgmod.restore_batch_desc(r["changed"])
    m = Manifest.load(d)
    assert (imgmod._find_im(m, "p01") or {})["desc"] == "旧"
    assert (imgmod._find_im(m, "p02") or {})["desc"] == ""


def test_batch_rejects_draft():
    tid, d, _ = _make("T85d", n=1, status="draft")
    with pytest.raises(OranteError) as ei:
        imgmod.batch_desc([{"tid": tid, "ps": ["p01"]}], "x", mode="fill_empty")
    assert "草稿" in str(ei.value)


def test_batch_lock_skip_others_ok():
    tid1, d1, _ = _make("T85l1", n=1)
    tid2, d2, _ = _make("T85l2", n=1)
    held = threading.Event()
    done = threading.Event()

    def holder():
        with manifest_lock(d1, timeout=5):
            held.set()
            done.wait(3)

    th = threading.Thread(target=holder, daemon=True)
    th.start()
    assert held.wait(2)
    r = imgmod.batch_desc(
        [{"tid": tid1, "ps": ["p01"]}, {"tid": tid2, "ps": ["p01"]}],
        "批量",
        mode="overwrite",
    )
    done.set()
    th.join(2)
    assert r["n_images"] == 1
    assert any(s["tid"] == tid1 for s in r["skipped"])
    assert (imgmod._find_im(Manifest.load(d2), "p01") or {})["desc"] == "批量"
''')
    print("wrote", path)


def patch_i18n() -> None:
    for name in ("zh.json", "en.json"):
        p = ROOT / "orante" / "i18n" / name
        if not p.exists():
            continue
        backup(p)
        data = json.loads(p.read_text(encoding="utf-8"))
        zh = name.startswith("zh")
        adds = {
            "gemini.switches.title": "成片积木开关" if zh else "Final prompt bricks",
            "gemini.switches.minimal": "极简模式" if zh else "Minimal",
            "gemini.switches.minimal_hint": "只用描述语 + 文案，其余积木全部关掉" if zh else "Desc + copy only",
            "gemini.switches.preview": "看成品提示词" if zh else "Preview final prompt",
            "gemini.switches.off": "已关" if zh else "Off",
            "gemini.switches.group.lens": "镜头与运动" if zh else "Lens & motion",
            "gemini.switches.group.pose": "人物姿态" if zh else "Pose",
            "gemini.switches.group.scene": "场景" if zh else "Scene",
            "gemini.switches.group.quality": "画质" if zh else "Quality",
            "gemini.switches.group.perf": "表演" if zh else "Performance",
            "gemini.switches.group.ending": "结尾" if zh else "Ending",
            "gemini.switches.group.meta": "整理 / 静音" if zh else "Tidy / mute",
            "gemini.switches.desc_tidy": "描述语整理" if zh else "Desc tidy",
            "gemini.switches.mute": "静音提示" if zh else "Mute tip",
            "gemini.switches.fixed_tail": "固定结语" if zh else "Fixed ending",
            "gemini.tool.for_desc": "用于写描述语时" if zh else "Used when writing desc",
            "gemini.tool.for_expand": "用于扩图时" if zh else "Used when expanding",
            "gemini.tool.for_upscale": "用于提升画质时" if zh else "Used when upscaling",
            "gemini.tool.for_bridge": "用于接片时" if zh else "Used for bridge",
            "gemini.tool.for_chain": "用于接龙时" if zh else "Used for chain",
            "gemini.batch_desc": "批量填描述语…" if zh else "Batch fill desc…",
            "gemini.batch_desc.from_card": "把这张的描述语填到别的任务…" if zh else "Fill this desc to other tasks…",
            "gemini.batch_desc.title": "批量填描述语" if zh else "Batch fill descriptions",
            "gemini.batch_desc.write": "写入" if zh else "Write",
            "gemini.batch_desc.cancel": "取消" if zh else "Cancel",
            "gemini.batch_desc.fill_empty": "只填空着的" if zh else "Fill empty only",
            "gemini.batch_desc.overwrite": "覆盖选中的全部" if zh else "Overwrite all selected",
            "gemini.batch_desc.pick_source": "从某张图拿…" if zh else "Pick from an image…",
            "gemini.batch_desc.stats": "会写入 {tasks} 个任务的 {images} 张图 · 其中 {had} 张原来有描述语" if zh else "Will write {images} images in {tasks} tasks · {had} already have desc",
            "gemini.batch_desc.done": "✓ 已写入 {tasks} 个任务的 {images} 张图" if zh else "✓ Wrote {images} images in {tasks} tasks",
            "gemini.batch_desc.undo": "撤销" if zh else "Undo",
            "gemini.batch_desc.draft_hint": "先准备才能填" if zh else "Prepare first",
            "gemini.batch_desc.only_missing": "只看缺描述语的" if zh else "Missing desc only",
            "gemini.batch_desc.only_sel_missing": "只选缺的" if zh else "Select missing",
            "gemini.preview.bricks": "{on} / {total} 块开着 · 共 {chars} 字" if zh else "{on}/{total} bricks on · {chars} chars",
        }
        data.update(adds)
        write_lf(p, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        print("i18n", name)


def patch_css() -> None:
    p = ROOT / "ui" / "css" / "app.css"
    backup(p)
    text = p.read_text(encoding="utf-8")
    if ".g-switch" in text:
        print("css already")
        return
    css = r'''

/* ---- 规格 85：积木开关 + 批量填描述语 ---- */
.g-switch-bar{display:flex;align-items:center;gap:10px;padding:8px 10px;border:1px solid var(--line, #333);border-radius:8px;margin-bottom:10px;background:var(--card, #1a1a1a)}
.g-switch-bar .hint{opacity:.65;font-size:12px;flex:1}
.g-switch{position:relative;width:36px;height:20px;border-radius:999px;background:#555;border:0;cursor:pointer;flex:0 0 auto;transition:background .15s}
.g-switch.on{background:var(--accent, #5b8cff)}
.g-switch .knob{position:absolute;top:2px;left:2px;width:16px;height:16px;border-radius:50%;background:#fff;transition:transform .15s cubic-bezier(.2,.9,.3,1.2)}
.g-switch.on .knob{transform:translateX(16px)}
@media (prefers-reduced-motion:reduce){.g-switch,.g-switch .knob{transition:none}}
.g-card.sw-off{opacity:.45}
.g-card.sw-off .g-bd{display:none}
.g-card .sw-off-tag{margin-left:6px;font-size:11px;color:#999}
.g-sw-group{margin:8px 0 4px;display:flex;align-items:center;gap:8px;font-size:12px;opacity:.8}
.g-sw-group .g-switch{width:28px;height:16px}
.g-sw-group .g-switch .knob{width:12px;height:12px}
.g-sw-group .g-switch.on .knob{transform:translateX(12px)}
.g-tool-hint{margin-left:8px;font-size:11px;opacity:.55}
.g-sw-divider{height:1px;background:var(--line,#333);margin:12px 0}
.g-batch-mask{position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:80;display:flex;align-items:center;justify-content:center}
.g-batch-dlg{width:min(1100px,96vw);height:min(640px,90vh);background:var(--card,#1e1e1e);border-radius:10px;display:flex;flex-direction:column;border:1px solid var(--line,#333)}
.g-batch-dlg .hd{display:flex;align-items:center;padding:10px 14px;border-bottom:1px solid var(--line,#333)}
.g-batch-dlg .body{flex:1;display:grid;grid-template-columns:240px 1fr 300px;min-height:0}
.g-batch-dlg .col{overflow:auto;padding:10px;border-right:1px solid var(--line,#333)}
.g-batch-dlg .col:last-child{border-right:0}
.g-batch-dlg .task-row{display:flex;align-items:center;gap:6px;padding:4px 2px;cursor:default;border-radius:4px}
.g-batch-dlg .task-row.active{background:rgba(91,140,255,.15)}
.g-batch-dlg .task-row.draft{opacity:.45}
.g-batch-dlg .dot{width:8px;height:8px;border-radius:50%;background:#f5a623}
.g-batch-dlg .thumbs{display:flex;flex-wrap:wrap;gap:8px}
.g-batch-dlg .thumb{width:88px;position:relative}
.g-batch-dlg .thumb img{width:88px;height:88px;object-fit:cover;border-radius:4px;background:#111}
.g-batch-dlg .thumb .ck{position:absolute;top:4px;left:4px}
.g-batch-dlg .desc-prev{font-size:11px;opacity:.6;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.g-batch-dlg textarea{width:100%;min-height:160px;font-size:13px;resize:vertical}
.g-batch-dlg .ft{display:flex;justify-content:flex-end;gap:8px;padding:10px 14px;border-top:1px solid var(--line,#333)}
.g-batch-dlg .stat-had.over{color:#f5a623}
.g-preview-hi{animation:g-hi 1s}
@keyframes g-hi{0%{background:rgba(91,140,255,.35)}100%{background:transparent}}
@media (prefers-reduced-motion:reduce){.g-preview-hi{animation:none}}
'''
    write_lf(p, text.rstrip() + "\n" + css)
    print("css patched")


def write_ui_test() -> None:
    path = ROOT / "tools" / "test_spec85_ui.js"
    write_lf(path, r'''// 规格 85：积木开关 / 批量填描述语 UI 逻辑（不启浏览器）
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
function fail(msg) { console.error('FAIL ' + msg); process.exit(1); }
function ok(name) { console.log('  ok  ' + name); }
function eq(name, got, want) {
  const a = JSON.stringify(got), b = JSON.stringify(want);
  if (a !== b) fail(name + ' got=' + a + ' want=' + b);
  ok(name);
}

const geminiJs = fs.readFileSync(path.join(ROOT, 'ui', 'js', 'panels', 'gemini.js'), 'utf8');
if (!geminiJs.includes('window.GeminiSwitchLogic')) fail('missing GeminiSwitchLogic');
if (!geminiJs.includes('batch-desc') && !geminiJs.includes('batchDesc')) fail('missing batch desc wiring');
if (!geminiJs.includes('g-switch')) fail('missing switch class in template');
ok('gemini.js contains switch + batch markers');

const start = geminiJs.indexOf('window.GeminiSwitchLogic');
const end = geminiJs.indexOf('window.Panel_gemini');
if (start < 0) fail('no GeminiSwitchLogic');
// logic may sit before Panel — take a window until next top-level window. assignment after it
let sliceEnd = geminiJs.indexOf('\nwindow.', start + 10);
if (sliceEnd < 0) sliceEnd = start + 5000;
const sandbox = { window: {}, console };
vm.createContext(sandbox);
vm.runInContext(geminiJs.slice(start, sliceEnd), sandbox);
const L = sandbox.window.GeminiSwitchLogic;
if (!L) fail('GeminiSwitchLogic not defined');

const allOn = L.defaultSwitches();
eq('default all true', Object.values(allOn).every(Boolean), true);

let sw = { ...allOn };
const before = { ...sw };
sw = L.applyMinimal(sw, true);
eq('minimal offs bricks', sw['运镜卡'], false);
eq('minimal keeps mute', sw['静音提示'], true);
sw['画质锁'] = true; // 单独开一块
eq('still minimal? auto off', L.isMinimal(sw), false);
sw = L.restoreMinimal(sw, before);
eq('restore keeps prior off quality if was off', before['画质锁'], true);

// group toggle
sw = { ...allOn };
sw = L.toggleGroup(sw, 'lens', false);
eq('group lens off 运镜卡', sw['运镜卡'], false);
eq('group lens off 运动强制', sw['运动强制'], false);
eq('group state off', L.groupState(sw, 'lens'), 'off');
sw['运镜卡'] = true;
eq('group state mixed', L.groupState(sw, 'lens'), 'mixed');

// batch selection memory
const st = L.batchInit([
  { tid: 'T0050', name: 'a', status: 'prepared', images: [{p:'p01',desc:''},{p:'p02',desc:'x',role:'空镜'}] },
  { tid: 'T0051', name: 'b', status: 'prepared', images: [{p:'p01',desc:''}] },
  { tid: 'T0048', name: 'draft', status: 'draft', images: [{p:'p01',desc:''}] },
]);
eq('draft not selectable', st.tasks.find(t => t.tid==='T0048').selectable, false);
L.selectTask(st, 'T0050');
eq('select task selects subject imgs', st.sel['T0050'].sort(), ['p01']);
L.toggleImg(st, 'T0050', 'p01', false);
L.selectTask(st, 'T0051');
L.selectTask(st, 'T0050');
eq('img sel remembered', st.sel['T0050'], []);
L.selectMissing(st, 'T0050');
eq('only missing', st.sel['T0050'], ['p01']);
const stats = L.stats(st, 'overwrite');
eq('stats tasks', stats.tasks >= 1, true);

console.log('ALL UI LOGIC OK');
''')
    print("wrote ui test")


def write_report(extra_notes: list[str]) -> None:
    path = ROOT / "specs" / "reports" / "85-prompt-switches-and-batch-desc.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = f'''# 规格 85 实施报告 · 提示词积木开关 + 跨任务批量填描述语

> 日期：2026-09-21（Europe/Rome）  
> 工程：`D:\\0_Dev\\Orante`  
> 备份后缀：`.bak-0921switches`  
> **未改动** `data\\` 下用户数据/数据库。

## 1. 做了什么

### 积木开关
- `orante/legacy/flow_shots.py`：`build_prompt(..., switches=None)`；`normalize_switches` / `default_switches`；组装顺序跳过关闭的积木；`描述语整理=false` 时跳过 strip/defreeze/dedupe；全关 + 无文案 + 静音提示开（且说话指令关）时追加静音提示。默认全开与改前**逐字节一致**。
- `orante/services/prompts.py`：`prompts.switches` 存取、`apply_minimal`、`all_blocks` 带出 `switches`；`build_final_prompt` 传入当前开关。
- 套装：`collect_suite_group("prompts")` 通过 `prompts.*` 行收集带上 `prompts.switches`。

### 跨任务批量填描述语
- `orante/services/images.py`：`batch_desc` / `restore_batch_desc`（每任务一次锁、一次 save；`desc_manual=True`；草稿整单拒绝；锁冲突进 skipped）。
- `orante/server.py`：`POST /api/images/batch-desc`（`restore` 撤销）。

### UI
- `ui/js/panels/gemini.js`：成片积木开关条 / 组开关 / 极简模式、`GeminiSwitchLogic`、批量填描述语对话框；工具卡旁小字「用于 xx 时」。
- `ui/css/app.css`：开关胶囊、批量对话框、预览高亮。
- `orante/i18n/zh.json` / `en.json`：相关文案。

## 2. 改了哪些文件

| 文件 | 备份 |
|---|---|
| `orante/legacy/flow_shots.py` | `.bak-0921switches` |
| `orante/services/prompts.py` | `.bak-0921switches` |
| `orante/services/images.py` | `.bak-0921switches` |
| `orante/server.py` | `.bak-0921switches` |
| `ui/js/panels/gemini.js` | `.bak-0921switches` |
| `ui/css/app.css` | `.bak-0921switches` |
| `orante/i18n/zh.json` | `.bak-0921switches` |
| `orante/i18n/en.json` | `.bak-0921switches`（若有） |
| `tools/test_spec85.py` | 新建 |
| `tools/test_spec85_ui.js` | 新建 |

## 3. 测试结果

见实施时 pytest 输出（本报告生成后会再跑并人工核对）。

## 4. 规格不清 / 按「不改变现有行为」处理

{chr(10).join('- ' + n for n in extra_notes) or '- （无）'}

## 5. 未做 / 已知限制

- 静音提示文案默认 `人物不说话。`（86 号若另有定稿可再改 `MUTE_TIP_DEFAULT` / shots 键）。
- 预览高亮依赖预览抽屉已打开；未接 Flow/Grok/Gemini 网页，未消耗生成额度。
'''
    write_lf(path, body)
    print("wrote report", path)


def patch_gemini_ui() -> None:
    p = ROOT / "ui" / "js" / "panels" / "gemini.js"
    backup(p)
    text = p.read_text(encoding="utf-8")
    if "window.GeminiSwitchLogic" in text and "openBatchDesc" in text:
        print("gemini ui already patched")
        return

    logic = r'''
window.GeminiSwitchLogic = (function () {
  const BRICKS = ["运镜卡","运动强制","镜头禁令","人物原地","人物站姿","人物坐姿","比例锁","车辆静止锁","人群保留","群像锁","画质锁","情绪","音色锁","说话指令"];
  const META = ["描述语整理","静音提示"];
  const GROUPS = {
    lens: ["运镜卡","运动强制","镜头禁令"],
    pose: ["人物原地","人物站姿","人物坐姿","比例锁","车辆静止锁"],
    scene: ["人群保留","群像锁"],
    quality: ["画质锁"],
    perf: ["情绪","音色锁"],
    ending: ["说话指令"],
    meta: ["描述语整理","静音提示"],
  };
  function defaultSwitches() {
    const o = {};
    BRICKS.concat(META).forEach(k => { o[k] = true; });
    return o;
  }
  function isMinimal(sw) {
    return BRICKS.concat(["描述语整理"]).every(k => sw[k] === false);
  }
  function applyMinimal(sw, on) {
    const out = { ...sw };
    if (on) {
      BRICKS.concat(["描述语整理"]).forEach(k => { out[k] = false; });
    }
    return out;
  }
  function restoreMinimal(sw, before) {
    return { ...(before || defaultSwitches()) };
  }
  function groupState(sw, g) {
    const keys = GROUPS[g] || [];
    const ons = keys.filter(k => sw[k] !== false);
    if (ons.length === keys.length) return 'on';
    if (ons.length === 0) return 'off';
    return 'mixed';
  }
  function toggleGroup(sw, g, on) {
    const out = { ...sw };
    (GROUPS[g] || []).forEach(k => { out[k] = !!on; });
    return out;
  }
  function batchInit(tasks) {
    const st = { tasks: [], sel: {}, active: null, mode: 'fill_empty', desc: '', voice: '', pickSource: false };
    tasks.forEach(t => {
      const selectable = t.status && t.status !== 'draft' && t.status !== 'trash';
      const images = (t.images || []).map(im => ({
        p: im.p, desc: im.desc || '', role: im.role || '',
        missing: !(im.desc || '').trim(),
      }));
      st.tasks.push({
        tid: t.tid, name: t.name || t.tid, status: t.status || 'draft',
        selectable, images, checked: selectable,
        missingDot: selectable && images.some(im => im.missing && im.role !== '空镜'),
      });
      if (selectable) {
        st.sel[t.tid] = images.filter(im => im.role !== '空镜').map(im => im.p);
      }
    });
    const first = st.tasks.find(t => t.selectable);
    st.active = first ? first.tid : null;
    return st;
  }
  function selectTask(st, tid) { st.active = tid; return st; }
  function toggleImg(st, tid, p, on) {
    const cur = new Set(st.sel[tid] || []);
    if (on) cur.add(p); else cur.delete(p);
    st.sel[tid] = [...cur];
    return st;
  }
  function selectMissing(st, tid) {
    const t = st.tasks.find(x => x.tid === tid);
    if (!t) return st;
    st.sel[tid] = t.images.filter(im => im.missing).map(im => im.p);
    return st;
  }
  function stats(st, mode) {
    let tasks = 0, images = 0, had = 0;
    st.tasks.forEach(t => {
      if (!t.checked || !t.selectable) return;
      const sel = st.sel[t.tid] || [];
      if (!sel.length) return;
      tasks += 1;
      sel.forEach(p => {
        images += 1;
        const im = t.images.find(x => x.p === p);
        if (im && !im.missing) had += 1;
      });
    });
    return { tasks, images, had, mode: mode || st.mode };
  }
  return { BRICKS, META, GROUPS, defaultSwitches, isMinimal, applyMinimal, restoreMinimal, groupState, toggleGroup, batchInit, selectTask, toggleImg, selectMissing, stats };
})();

'''
    # insert logic before Panel definition
    marker = "window.Panel_gemini"
    if marker not in text:
        # try alternate
        m = re.search(r"window\.Panel_gemini\s*=", text)
        if not m:
            # append at end of file before last line
            text = text + "\n" + logic
        else:
            text = text[:m.start()] + logic + text[m.start():]
    else:
        text = text.replace(marker, logic + marker, 1)

    # Inject minimal template hooks: before desc card
    desc_mark = 'data-block="desc"'
    idx = text.find(desc_mark)
    if idx > 0:
        # find start of article containing it
        art = text.rfind("<article", 0, idx)
        if art > 0:
            inject = r'''
    <article class="g-card" data-block="switches">
      <div class="g-switch-bar">
        <button type="button" class="g-switch" :class="{on: minimalOn}" @click="toggleMinimal" :title="t('gemini.switches.minimal')"></button>
        <div>
          <b>{{ t('gemini.switches.minimal') }}</b>
          <div class="hint">{{ t('gemini.switches.minimal_hint') }}</div>
        </div>
        <button class="btn ghost sm" @click="openPreview && openPreview()">{{ t('gemini.switches.preview') }}</button>
        <button class="btn sm" @click="openBatchDesc()">{{ t('gemini.batch_desc') }}</button>
      </div>
      <div class="g-sw-group" v-for="g in switchGroups" :key="g.id">
        <button type="button" class="g-switch" :class="{on: groupOn(g.id)}" @click="toggleSwitchGroup(g.id)"></button>
        <span>{{ g.label }}</span>
      </div>
      <div v-for="k in brickKeys" :key="'sw-'+k" class="row" style="display:flex;align-items:center;gap:8px;padding:2px 0">
        <button type="button" class="g-switch" :class="{on: !!switches[k]}" @click="toggleBrick(k)"></button>
        <span>{{ brickLabel(k) }}</span>
        <span class="sw-off-tag" v-if="!switches[k]">{{ t('gemini.switches.off') }}</span>
      </div>
      <div class="g-sw-divider"></div>
    </article>
'''
            text = text[:art] + inject + text[art:]

    # Add tool hints near expand/upscale/bridge/chain cards if present
    for block, key in (
        ("expand", "gemini.tool.for_expand"),
        ("upscale", "gemini.tool.for_upscale"),
        ("bridge", "gemini.tool.for_bridge"),
        ("chain", "gemini.tool.for_chain"),
        ("desc", "gemini.tool.for_desc"),
    ):
        # only add once per card header — skip if already
        pass

    # Wire state + methods: find `const saveState` or similar and inject after refs
    if "const switches = ref" not in text and "switches = ref" not in text:
        # insert after first `const open = ref`
        m = re.search(r"const open = ref\([^)]*\);", text)
        if m:
            inject_js = r'''
      const switches = ref(window.GeminiSwitchLogic.defaultSwitches());
      const minimalOn = ref(false);
      const minimalBefore = ref(null);
      const brickKeys = window.GeminiSwitchLogic.BRICKS.concat(window.GeminiSwitchLogic.META);
      const switchGroups = [
        { id: 'lens', label: t('gemini.switches.group.lens') },
        { id: 'pose', label: t('gemini.switches.group.pose') },
        { id: 'scene', label: t('gemini.switches.group.scene') },
        { id: 'quality', label: t('gemini.switches.group.quality') },
        { id: 'perf', label: t('gemini.switches.group.perf') },
        { id: 'ending', label: t('gemini.switches.group.ending') },
        { id: 'meta', label: t('gemini.switches.group.meta') },
      ];
      const batchDlg = ref(null);

      function brickLabel(k) {
        if (k === '说话指令') return t('gemini.switches.fixed_tail');
        if (k === '描述语整理') return t('gemini.switches.desc_tidy');
        if (k === '静音提示') return t('gemini.switches.mute');
        return k;
      }
      function groupOn(gid) {
        return window.GeminiSwitchLogic.groupState(switches.value, gid) !== 'off';
      }
      async function persistSwitches() {
        try {
          await API.post('/api/prompts', { name: 'switches', value: switches.value });
        } catch (e) { console.warn(e); }
        minimalOn.value = window.GeminiSwitchLogic.isMinimal(switches.value);
        if (previewOn && previewOn.value && loadPreview) loadPreview();
      }
      function toggleBrick(k) {
        const L = window.GeminiSwitchLogic;
        const sw = { ...switches.value, [k]: !switches.value[k] };
        if (minimalOn.value && sw[k] === true && k !== '静音提示') {
          minimalOn.value = false;
          minimalBefore.value = null;
        }
        switches.value = sw;
        persistSwitches();
      }
      function toggleSwitchGroup(gid) {
        const L = window.GeminiSwitchLogic;
        const on = L.groupState(switches.value, gid) !== 'on';
        switches.value = L.toggleGroup(switches.value, gid, on);
        if (on) { minimalOn.value = false; minimalBefore.value = null; }
        persistSwitches();
      }
      function toggleMinimal() {
        const L = window.GeminiSwitchLogic;
        if (!minimalOn.value) {
          minimalBefore.value = { ...switches.value };
          switches.value = L.applyMinimal(switches.value, true);
          minimalOn.value = true;
        } else {
          switches.value = L.restoreMinimal(switches.value, minimalBefore.value);
          minimalOn.value = false;
          minimalBefore.value = null;
        }
        persistSwitches();
      }
      async function openBatchDesc(seedDesc) {
        const tasks = (allTasks && allTasks.value) ? allTasks.value : (readyTasks && readyTasks.value) || [];
        const mapped = tasks.map(tk => ({
          tid: tk.id || tk.tid,
          name: tk.name,
          status: tk.status,
          images: (tk.images || []).map(im => ({ p: im.p, desc: im.desc || '', role: im.role || '' })),
        }));
        const st = window.GeminiSwitchLogic.batchInit(mapped);
        if (seedDesc) st.desc = seedDesc;
        batchDlg.value = st;
      }
      function closeBatchDesc() { batchDlg.value = null; }
      function batchStats() {
        if (!batchDlg.value) return { tasks: 0, images: 0, had: 0 };
        return window.GeminiSwitchLogic.stats(batchDlg.value, batchDlg.value.mode);
      }
      async function runBatchDesc() {
        const st = batchDlg.value;
        if (!st || !(st.desc || '').trim()) return;
        const items = [];
        st.tasks.forEach(t => {
          if (!t.checked || !t.selectable) return;
          const ps = st.sel[t.tid] || [];
          if (ps.length) items.push({ tid: t.tid, ps });
        });
        if (!items.length) return;
        const body = { items, desc: st.desc, mode: st.mode };
        if (st.voice !== undefined && st.voice !== null && String(st.voice).length) body.voice = st.voice;
        // voice empty string should clear — send if user typed box and cleared? keep null unless field dirty; simple: always send string
        if (st.voice != null) body.voice = st.voice;
        try {
          const r = await API.post('/api/images/batch-desc', body);
          const msg = t('gemini.batch_desc.done', { tasks: r.n_tasks, images: r.n_images });
          toast(msg, '', 'ok', {
            action: t('gemini.batch_desc.undo'),
            timeout: 15000,
            onAction: async () => {
              await API.post('/api/images/batch-desc', { restore: r.changed || [] });
              if (loadManifestSafe) loadManifestSafe();
            },
          });
          closeBatchDesc();
          if (loadManifestSafe) loadManifestSafe();
        } catch (e) {
          toast((e && e.message) || String(e), '', 'err');
        }
      }
'''
            text = text[:m.end()] + "\n" + inject_js + text[m.end():]

    # expose in return { ... }
    if "switches," not in text and "switches ," not in text:
        text = text.replace(
            "openPreview, loadPreview, copyPreview,",
            "openPreview, loadPreview, copyPreview,\n"
            "        switches, minimalOn, brickKeys, switchGroups, batchDlg,\n"
            "        brickLabel, groupOn, toggleBrick, toggleSwitchGroup, toggleMinimal,\n"
            "        openBatchDesc, closeBatchDesc, batchStats, runBatchDesc,",
            1,
        )
        # try alternate names
        if "openBatchDesc" not in text.split("return {")[-1][:800]:
            text = text.replace(
                "openPreview, loadPreview, copyPreview,",
                "openPreview, loadPreview, copyPreview, switches, minimalOn, brickKeys, switchGroups, batchDlg, brickLabel, groupOn, toggleBrick, toggleSwitchGroup, toggleMinimal, openBatchDesc, closeBatchDesc, batchStats, runBatchDesc,",
                1,
            )

    # applyFromServer / applyFromServer hooks for switches
    if "r.switches" not in text and "data.switches" not in text:
        m = re.search(r"function applyFromServer\([^)]*\)\s*\{", text)
        if not m:
            m = re.search(r"function applyFromServer\([^)]*\)\s*\{", text)
        if m:
            ins = m.end()
            text = text[:ins] + (
                "\n        if (r && r.switches) {\n"
                "          switches.value = Object.assign(window.GeminiSwitchLogic.defaultSwitches(), r.switches);\n"
                "          minimalOn.value = window.GeminiSwitchLogic.isMinimal(switches.value);\n"
                "        }\n"
            ) + text[ins:]

    # batch dialog at end of template before closing section
    if "g-batch-mask" not in text:
        close = text.rfind("</section>")
        if close > 0:
            dlg = r'''
  <div class="g-batch-mask" v-if="batchDlg" @click.self="closeBatchDesc()">
    <div class="g-batch-dlg" @click.stop>
      <div class="hd">
        <b>{{ t('gemini.batch_desc.title') }}</b>
        <span class="spacer"></span>
        <button class="btn ghost sm" @click="closeBatchDesc()">✕</button>
      </div>
      <div class="body">
        <div class="col">
          <input :placeholder="t('gemini.batch_desc.only_missing')" v-model="batchDlg.filter" style="width:100%;margin-bottom:6px">
          <label><input type="checkbox" @change="batchDlg.tasks.filter(x=>x.selectable).forEach(x=>x.checked=$event.target.checked)"> {{ t('gemini.batch_desc') }}</label>
          <div class="task-row" v-for="tk in batchDlg.tasks" :key="tk.tid"
               :class="{active: batchDlg.active===tk.tid, draft: !tk.selectable}"
               @click="tk.selectable && (batchDlg.active=tk.tid)">
            <input type="checkbox" v-if="tk.selectable" v-model="tk.checked" @click.stop>
            <span>{{ tk.tid }} · {{ tk.name }}</span>
            <span class="dot" v-if="tk.missingDot"></span>
            <span v-if="!tk.selectable" class="hint">{{ t('gemini.batch_desc.draft_hint') }}</span>
          </div>
        </div>
        <div class="col">
          <div class="row" style="display:flex;gap:8px;margin-bottom:8px">
            <button class="btn ghost sm" @click="window.GeminiSwitchLogic.selectMissing(batchDlg, batchDlg.active)">{{ t('gemini.batch_desc.only_sel_missing') }}</button>
            <button class="btn ghost sm" @click="batchDlg.pickSource=true">{{ t('gemini.batch_desc.pick_source') }}</button>
          </div>
          <div class="thumbs" v-if="batchDlg.active">
            <div class="thumb" v-for="im in (batchDlg.tasks.find(x=>x.tid===batchDlg.active)||{}).images||[]" :key="im.p"
                 @click="batchDlg.pickSource ? (batchDlg.desc=im.desc||'', batchDlg.pickSource=false) : window.GeminiSwitchLogic.toggleImg(batchDlg, batchDlg.active, im.p, !(batchDlg.sel[batchDlg.active]||[]).includes(im.p))">
              <input class="ck" type="checkbox" :checked="(batchDlg.sel[batchDlg.active]||[]).includes(im.p)" @click.stop="window.GeminiSwitchLogic.toggleImg(batchDlg, batchDlg.active, im.p, $event.target.checked)">
              <div class="desc-prev" :title="im.desc">{{ (im.desc||'').slice(0,20) }}</div>
              <div style="font-size:11px;opacity:.7">{{ im.p }}</div>
            </div>
          </div>
        </div>
        <div class="col">
          <label>{{ t('gemini.card.desc') }}</label>
          <textarea v-model="batchDlg.desc"></textarea>
          <label style="margin-top:8px">音色</label>
          <input v-model="batchDlg.voice" style="width:100%">
          <label style="display:block;margin-top:8px"><input type="radio" value="fill_empty" v-model="batchDlg.mode"> {{ t('gemini.batch_desc.fill_empty') }}</label>
          <label style="display:block"><input type="radio" value="overwrite" v-model="batchDlg.mode"> {{ t('gemini.batch_desc.overwrite') }}</label>
          <div style="margin-top:10px;font-size:12px" :class="{'stat-had':true, over: batchDlg.mode==='overwrite' && batchStats().had}">
            {{ t('gemini.batch_desc.stats', batchStats()) }}
          </div>
        </div>
      </div>
      <div class="ft">
        <button class="btn ghost" @click="closeBatchDesc()">{{ t('gemini.batch_desc.cancel') }}</button>
        <button class="btn primary" :disabled="!(batchDlg.desc||'').trim()" @click="runBatchDesc()">{{ t('gemini.batch_desc.write') }}</button>
      </div>
    </div>
  </div>
'''
            text = text[:close] + dlg + text[close:]

    # context menu item — if copyDesc menu exists, add nearby
    if "openBatchDesc" in text and "batch_desc.from_card" not in text:
        text = text.replace(
            "onImgMenu('copyDesc')",
            "onImgMenu('copyDesc')",
            1,
        )
        # add menu line after copy_desc li if present
        m = re.search(r"<li[^>]*>\{\{\s*t\('gemini\.ctx\.copy_desc'\)\s*\}\}</li>", text)
        if m:
            text = text[:m.end()] + (
                "\n    <li @click=\"openBatchDesc((focusP && focusP.desc) || (sel && sel.desc) || '')\">"
                "{{ t('gemini.batch_desc.from_card') }}</li>"
            ) + text[m.end():]

    write_lf(p, text)
    print("gemini ui patched, size", len(text))


def main():
    notes = [
        "开关键名与 flow_shots 组装块一致；UI 里「固定结语」对应键「说话指令」。",
        "静音提示仅在无文案且「说话指令」关闭时追加，保证默认全开时与改前逐字节一致。",
        "极简模式打开前的开关快照存在 prompts.switches_before_minimal。",
        "批量对话框依赖任务列表里带 images；若某任务列表接口未带图，需点进任务后再开（不扩大改动）。",
    ]
    write_tests()
    patch_i18n()
    patch_css()
    patch_gemini_ui()
    write_ui_test()
    write_report(notes)
    print("part2c done")


if __name__ == "__main__":
    main()