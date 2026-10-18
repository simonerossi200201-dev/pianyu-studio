# -*- coding: utf-8 -*-
"""规格 63：情绪指令改成温暖可信，激昂预设保留旧行为。"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-spec63-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import config, db  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import prompts, preset  # noqa: E402

db.init()
fail = 0

FORBIDDEN = (
    "下颌用力", "目光炯炯", "直视镜头", "扬声激昂",
    "powerful voice", "emphatic gestures",
)
REQUIRED = ("掌心", "前倾", "从不看镜头", "放轻")
LIVE_KEYS = prompts.EMOTION_LIVE_KEYS
BAK = ROOT / "orante" / "legacy" / "prompt_defaults.json.bak-0913warm"

DESC_OPEN = (
    "这是一位非洲的中年40岁左右女性基督徒，她皮肤带有细腻的岁月痕迹与健康的深色肤质，"
    "声音沉稳醇厚，她以端庄的站姿站在讲台上，双手自然放在身前保持动作不变，"
    "她全程睁着眼，站在原位不离开原地，配合手势和上身动作说话，"
    "她正在用带有非洲口音的英语有声地说话，说话间有呼吸声，"
    "现场是一座采光良好的礼堂，台下坐着听众，保持同一人物、同一现场空间的一致性【睁眼|舞台】"
)
VOICE = "【音色】非洲裔|女|中年|中低音|非洲口音"
SEG_TEXT = "Please listen carefully until the end. God is speaking to your heart today."


def ok(msg):
    print("  OK  ", msg)


def bad(msg):
    global fail
    fail += 1
    print("  FAIL", msg)


def _wipe():
    for k in (
        "prompts.tail", "prompts.desc", "prompts.emotion_preset",
        "prompts.emotion_warm_migrated",
    ):
        try:
            config.reset(k)
        except Exception:
            pass


def _manifest(n_segs=1, eye="睁眼"):
    m = Manifest.new("T0063", "温暖验收", "flow", "luca-1", "long", SEG_TEXT)
    m.images = [{
        "p": "p01", "file": "images/p01.jpg", "sha1": "x",
        "role": "主体", "status": "ok",
        "desc": DESC_OPEN, "voice": VOICE, "eye": eye, "scene": "舞台",
        "measure": {}, "origin": "download", "src": "",
    }]
    m.segments = []
    for i in range(1, n_segs + 1):
        m.segments.append({
            "s": i, "text": SEG_TEXT, "duration": 8, "image": "p01",
            "role": "主体", "shot": "", "prompt_final": "",
            "status": "planned", "gen_id": None, "takes": [], "error": None,
        })
    return m


def test_warm_prompt_words():
    print("== 默认成品词：温暖侧，不含严厉旋钮 ==")
    _wipe()
    m = _manifest(1)
    p = prompts.build_final_prompt(m, m.segments[0])
    missing = [w for w in REQUIRED if w not in p]
    hit = [w for w in FORBIDDEN if w in p]
    if missing:
        bad(f"缺必须出现的词：{missing}")
        return
    if hit:
        bad(f"不该出现的词还在：{hit}")
        return
    if "直视镜头" in p and "从不看镜头" in p:
        bad("直视镜头 和 从不看镜头 同时出现")
        return
    if "powerful voice" in p or "emphatic gestures" in p:
        bad("英文严厉词还在")
        return
    ok(f"成品词 {len(p)} 字，含掌心/前倾/从不看镜头/放轻，不含严厉旋钮")


def test_no_lens_conflict():
    print("== 和 35 号不打架：不能同时直视镜头 + 从不看镜头 ==")
    _wipe()
    m = _manifest(1)
    p = prompts.build_final_prompt(m, m.segments[0])
    a = "直视镜头" in p
    b = "从不看镜头" in p
    if a and b:
        bad("两句同时在成品词里")
        return
    if not b:
        bad("默认温暖词里没有「从不看镜头」")
        return
    if a:
        bad("默认温暖词里还有「直视镜头」")
        return
    ok("只有「从不看镜头」，没有「直视镜头」")


def test_ramp_turn():
    print("== 四段弧的「转」：放轻/放慢，不含扬声 ==")
    _wipe()
    tail = prompts.get_block("tail")
    for key in ("情绪推进_睁眼", "情绪推进"):
        ramp = tail.get(key) or {}
        turn = ramp.get("转") or ""
        if "放轻" not in turn and "放慢" not in turn:
            bad(f"{key}.转 没有放轻/放慢：{turn}")
            return
        if "扬声" in turn:
            bad(f"{key}.转 还在扬声：{turn}")
            return
    m = _manifest(4)
    p = prompts.build_final_prompt(m, m.segments[2])  # s=3 → 转
    if "放轻" not in p and "放慢" not in p:
        bad("第 3/4 段成品词没有放轻/放慢")
        return
    if "扬声" in p:
        bad("第 3/4 段成品词含扬声")
        return
    ok("睁眼/闭眼「转」都是放轻放慢，成品词也对")


def test_fiery_matches_bak():
    print("== 激昂预设 = 改动前原文，成品词逐字相同 ==")
    _wipe()
    if not BAK.is_file():
        bad(f"找不到改前备份 {BAK.name}")
        return
    old_tail = json.loads(BAK.read_text(encoding="utf-8"))["tail"]
    packed = prompts.get_block("tail")["情绪预设"]["激昂"]
    for k in LIVE_KEYS:
        if packed.get(k) != old_tail.get(k):
            bad(f"激昂.{k} 和改前备份不一致")
            return
    m = _manifest(1)
    # 改动前：把备份 live 键铺上去再拼
    tail = prompts.get_block("tail")
    for k in LIVE_KEYS:
        tail[k] = deepcopy(old_tail[k])
    tail["情绪预设"]["当前"] = "激昂"
    prompts.set_block("tail", tail)
    p_old = prompts.build_final_prompt(m, m.segments[0])
    _wipe()
    prompts.apply_emotion_preset("激昂")
    p_new = prompts.build_final_prompt(m, m.segments[0])
    if p_old != p_new:
        bad(f"激昂成品词和改动前不同（{len(p_old)} vs {len(p_new)} 字）")
        return
    # 安静模式拼的是基调+推进，下颌用力在「承」；用 4 段的承核对旧词还在
    m4 = _manifest(4)
    p_cheng = prompts.build_final_prompt(m4, m4.segments[1])
    for w in ("下颌用力", "眉毛扬起"):
        if w not in p_cheng:
            bad(f"激昂「承」缺旧词 {w}")
            return
    if prompts.emotion_preset_name() != "激昂":
        bad(f"当前预设不是激昂：{prompts.emotion_preset_name()}")
        return
    ok(f"激昂成品词 {len(p_new)} 字，与改动前逐字相同；承段仍有下颌用力")


def test_desc_wordlist():
    print("== desc 第2部分：温暖词表在前，大幅度的手势已删 ==")
    _wipe()
    desc = prompts.get_block("desc")
    if "大幅度的手势" in desc:
        bad("desc 里还有「大幅度的手势」")
        return
    if "配合自然幅度的手势" not in desc:
        bad("desc 里没有换成「自然幅度的手势」")
        return
    m = re.search(r"情绪[（(]([^/）)]+)", desc)
    if not m:
        bad("desc 里找不到情绪词表")
        return
    first = m.group(1)
    if first != "温暖":
        bad(f"情绪词表第一个词是 {first!r}，不是温暖")
        return
    if "温暖/亲切/恳切/受鼓舞" not in desc:
        bad("新词表不完整")
        return
    if "优先写松弛温暖的那一种" not in desc:
        bad("缺默认取向那一句")
        return
    ok("desc 词表以温暖开头，手势改成自然幅度")


def test_preset_export():
    print("== 26 号导出带上情绪预设选择 ==")
    _wipe()
    prompts.apply_emotion_preset("激昂")
    rows = preset._prompt_rows()
    if rows.get("prompts.emotion_preset") != "激昂":
        bad(f"prompts.emotion_preset = {rows.get('prompts.emotion_preset')!r}")
        return
    tail = rows.get("prompts.tail") or {}
    cur = (tail.get("情绪预设") or {}).get("当前")
    if cur != "激昂":
        bad(f"tail.情绪预设.当前 = {cur!r}")
        return
    dest = TEST_DATA / "spec63.orpreset"
    r = preset.export(["prompts"], False, "pw-63", str(dest))
    if not dest.is_file() or r.get("bytes", 0) < 100:
        bad("导出没写出文件")
        return
    ok("导出含 prompts.emotion_preset=激昂 且 tail.情绪预设.当前=激昂")


def test_coverage_untouched():
    print("== coverage_library.json 没动（本来就是温的）==")
    lib = (ROOT / "orante" / "legacy" / "coverage_library.json").read_text(encoding="utf-8")
    for w in ("温和微笑", "眼神柔和", "掌心朝上", "上身前倾", "嘴角自然上扬"):
        if w not in lib:
            bad(f"覆盖库缺 {w}，是不是被改坏了")
            return
    ok("覆盖库仍是温的，没改")


def test_ui_wired():
    print("== 多模态固定结语能选预设 ==")
    zh = json.loads((ROOT / "orante" / "i18n" / "zh.json").read_text(encoding="utf-8"))
    for k in ("gemini.emotion_preset", "gemini.emotion_warm", "gemini.emotion_fiery",
              "gemini.emotion_preset_hint"):
        if k not in zh:
            bad(f"缺文案 {k}")
            return
    js = (ROOT / "ui" / "js" / "panels" / "gemini.js").read_text(encoding="utf-8")
    if "setEmotionPreset" not in js or "/api/prompts/emotion-preset" not in js:
        bad("gemini.js 没接预设切换")
        return
    if "情绪预设" not in js:
        bad("gemini.js 没跳过 情绪预设 字典渲")
        return
    srv = (ROOT / "orante" / "server.py").read_text(encoding="utf-8")
    if "/api/prompts/emotion-preset" not in srv:
        bad("server.py 没有切换接口")
        return
    ok("文案、按钮、接口都在")


def test_switch_roundtrip():
    print("== 温暖 ↔ 激昂 往返，手改跟槽走 ==")
    _wipe()
    prompts.apply_emotion_preset("温暖")
    tail = prompts.get_block("tail")
    tail["情绪_睁眼"] = tail["情绪_睁眼"] + "【手改温暖】"
    prompts.set_block("tail", tail)
    prompts.apply_emotion_preset("激昂")
    t2 = prompts.get_block("tail")
    if "【手改温暖】" in str(t2.get("情绪_睁眼") or ""):
        bad("切到激昂后 live 还带着温暖手改")
        return
    if "直视镜头" not in str(t2.get("情绪_睁眼") or ""):
        bad("切到激昂后 live 不是旧词")
        return
    prompts.apply_emotion_preset("温暖")
    t3 = prompts.get_block("tail")
    if "【手改温暖】" not in str(t3.get("情绪_睁眼") or ""):
        bad("切回温暖后手改丢了")
        return
    ok("手改写进当前槽，切换不丢")


def main() -> int:
    print(f"ORANTE_DATA = {TEST_DATA}")
    test_warm_prompt_words()
    test_no_lens_conflict()
    test_ramp_turn()
    test_fiery_matches_bak()
    test_desc_wordlist()
    test_preset_export()
    test_coverage_untouched()
    test_ui_wired()
    test_switch_roundtrip()
    print()
    if fail:
        print(f"失败 {fail} 项")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
