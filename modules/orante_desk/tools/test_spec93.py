# -*- coding: utf-8 -*-
"""规格 93：极简模式（和任何提示词改动）必须对还没出片的段立刻生效；
不说话的任务永远不带「说话指令」。

起因：用户把没有文案的任务开了极简模式，成品提示词里照样是运镜卡、比例锁、画质锁、
情绪、以及「人物只需要准确说出下面这一句英文…」——后面根本没有台词，
再下一行又写着「全程不说话」。两个原因：
  ① 段上存着上一轮拼好的 prompt_final，改了设置也不会重拼；
  ② 没有文案时「说话指令」这一块本来就不该加。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-93-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "orante" / "legacy"))

from orante import db  # noqa: E402
from orante.services import prompts, generate as genmod  # noqa: E402
import flow_shots  # noqa: E402

db.init()

DESC = "一位中年男性站在客厅里，双手自然下垂。"


def _prompt(no_copy: bool, switches=None, seg=""):
    return flow_shots.build_prompt(
        DESC, "缓缓推近", "闭眼", "常规", seg,
        shots=flow_shots.load_shots(), switches=switches, no_copy=no_copy)


# ---------- 不说话的任务不许带说话指令 ----------

def test_no_copy_drops_speaking_block_even_with_everything_on():
    on = _prompt(no_copy=True, switches=flow_shots.default_switches())
    for bad in ("说出下面", "嘴型对齐", "一字不差", "说话的内容"):
        assert bad not in on, bad                # 后面根本没有台词，这几句纯属自相矛盾
    assert DESC[:12] in on                       # 描述语还在
    assert "不说话" in on                        # 静音提示还在


def test_with_copy_still_has_speaking_block():
    """有台词的任务一个字都不能少。"""
    said = _prompt(no_copy=False, switches=flow_shots.default_switches(), seg="Hello world.")
    assert "Hello world." in said
    assert "说出下面" in said or "嘴型对齐" in said


def test_minimal_leaves_desc_and_silent_only():
    off = dict.fromkeys(flow_shots.SWITCH_ALL_KEYS, False)
    off["静音提示"] = True
    out = _prompt(no_copy=True, switches=off)
    lines = [x for x in out.splitlines() if x.strip()]
    assert lines[0].startswith(DESC[:10])
    assert "【镜头】" not in out                  # 运镜卡
    assert "比例" not in out and "4K" not in out  # 比例锁 / 画质锁
    assert "情绪" not in out
    assert "不说话" in out                        # 只剩描述语 + 静音提示


# ---------- 提示词世代 ----------

def test_rev_bumps_on_every_prompt_change():
    r0 = prompts.rev()
    prompts.set_switches({"画质锁": False})
    r1 = prompts.rev()
    assert r1 > r0
    prompts.apply_minimal(True)
    r2 = prompts.rev()
    assert r2 > r1
    prompts.set_block("chain", "改一句")
    r3 = prompts.rev()
    assert r3 > r2
    prompts.reset_block("chain")
    assert prompts.rev() > r3
    prompts.apply_minimal(False)


def test_suite_apply_bumps_rev():
    from orante.services import preset
    r0 = prompts.rev()
    preset.apply_suite_group("prompts", {"prompts.chain": "套装里的版本"})
    assert prompts.rev() > r0
    prompts.reset_block("chain")


def test_suite_never_eats_internal_prompt_state():
    """世代号、极简前的开关、语种包备份：套装不许带走，也不许覆盖。"""
    from orante import config
    from orante.services import preset
    config.set("prompts.lang_backup", {"blocks": {"chain": "出厂版"}}, touch_rev=False)
    prompts.apply_minimal(True)                  # 会写 switches_before_minimal
    rows = preset._prompt_rows()
    for k in preset.PROMPT_INTERNAL_KEYS:
        assert k not in rows, k
    r_before = prompts.rev()
    preset.apply_suite_group("prompts", {"prompts.chain": "套装版",
                                         "prompts.rev": 0,
                                         "prompts.lang_backup": {}})
    assert prompts.rev() > r_before              # 没被套装里的 0 顶回去
    assert config.get("prompts.lang_backup") == {"blocks": {"chain": "出厂版"}}
    prompts.apply_minimal(False)
    prompts.reset_block("chain")


# ---------- 存着的提示词按世代作废 ----------

class _M:
    """够用就行的假 manifest：_final_prompt 只用到 image() / segments / mode。"""
    mode = "first_frame"
    copy = {"text": ""}

    def __init__(self):
        self.images = [{"p": "p01", "file": "images/p01.jpg", "desc": DESC}]
        self.segments = []

    def image(self, p):
        return next((im for im in self.images if im["p"] == p), None)


def _seg(**kw):
    s = {"s": 1, "text": "", "image": "p01", "status": "planned",
         "prompt_final": "", "takes": [], "gen_id": None}
    s.update(kw)
    return s


def test_stale_prompt_is_rebuilt_next_run(monkeypatch):
    m = _M()
    monkeypatch.setattr("orante.services.prompts.build_final_prompt",
                        lambda mm, sg: "新的提示词")
    seg = _seg(prompt_final="上一轮按老设置拼的", prompt_rev=1,
               prompt_snap=genmod._prompt_snap_key(m, _seg()))
    # 世代还是 1 → 照用
    assert genmod._final_prompt(m, dict(seg), rev0=1) == "上一轮按老设置拼的"
    # 改过提示词（世代变成 2）→ 重拼
    assert genmod._final_prompt(m, dict(seg), rev0=2) == "新的提示词"


def test_locked_prompt_survives_rev_change(monkeypatch):
    m = _M()
    monkeypatch.setattr("orante.services.prompts.build_final_prompt",
                        lambda mm, sg: "不该用到")
    seg = _seg(prompt_final="我自己手写的", prompt_locked=True, prompt_rev=1)
    assert genmod._final_prompt(m, seg, rev0=9) == "我自己手写的"


def test_snapshot_rebakes_stale_and_stamps_rev(monkeypatch):
    m = _M()
    monkeypatch.setattr("orante.services.prompts.build_final_prompt",
                        lambda mm, sg: "第 7 代")
    old = _seg(s=1, prompt_final="第 5 代拼的", prompt_rev=5)
    fresh = _seg(s=2, prompt_final="第 7 代", prompt_rev=7)
    empty = _seg(s=3)
    segs = [old, fresh, empty]

    class J:
        lines = []

        def log(self, msg):
            J.lines.append(msg)

    n = genmod.snapshot_prompts(J(), m, segs, rev0=7)
    assert n == 2                                   # 过期那条 + 空的那条
    assert old["prompt_final"] == "第 7 代" and old["prompt_rev"] == 7
    assert fresh["prompt_final"] == "第 7 代"        # 本来就是这一代，没重拼
    assert empty["prompt_final"] == "第 7 代" and empty["prompt_rev"] == 7


def test_mid_run_suite_change_does_not_disturb_this_round(monkeypatch):
    """规格 91 的保证还在：跑到一半别人改了提示词，这一轮照旧。"""
    m = _M()
    monkeypatch.setattr("orante.services.prompts.build_final_prompt",
                        lambda mm, sg: "别的窗口改完的")
    seg = _seg(prompt_final="本轮开跑时定的", prompt_rev=3,
               prompt_snap=genmod._prompt_snap_key(m, _seg()))
    prompts.set_block("chain", "别的窗口在改")      # 世代往前跑
    assert prompts.rev() != 3
    assert genmod._final_prompt(m, dict(seg), rev0=3) == "本轮开跑时定的"
    prompts.reset_block("chain")


def test_generate_wires_rev_at_job_start():
    src = (ROOT / "orante" / "services" / "generate.py").read_text(encoding="utf-8")
    assert "prompt_rev0 = prompt_rev()" in src
    assert "rev0=prompt_rev0" in src
    assert 'seg["prompt_rev"] = prompt_rev0' in src
