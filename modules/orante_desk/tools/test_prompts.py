# -*- coding: utf-8 -*-
"""验收 prompts / Gemini：默认四块、问题句、成品提示词、Key 空/有。"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-prompts-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import db  # noqa: E402
from orante.errors import OranteError  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import prompts  # noqa: E402
from orante.services.gemini import Gemini  # noqa: E402

db.init()

DESC = (
    "这是一位非洲的中年40岁左右女性基督徒，她皮肤带有细腻的岁月痕迹与健康的深色肤质，"
    "声音沉稳醇厚，她以端庄的站姿站在室内，双手在胸前合十保持动作不变，"
    "她全程紧闭双眼，双脚不移动位置，眉头微蹙、下颌绷紧，神情虔诚恳切，"
    "她正在用带有非洲口音的英语有声地祷告，说话间有呼吸声，"
    "现场是一间采光良好的简朴房屋，保持同一人物、同一现场空间的一致性【闭眼|常规】"
)
VOICE = "【音色】非洲裔|女|中年|中低音|非洲口音"
SEG_TEXT = "Stay for 7 seconds and type Amen. God bless you."


def test_all_blocks() -> dict:
    blocks = prompts.all_blocks()
    for k in ("desc", "camera", "tail", "problem"):
        if k not in blocks or blocks[k] in (None, "", {}, []):
            raise SystemExit(f"FAIL all_blocks 缺默认值：{k} = {blocks.get(k)!r}")
    desc = blocks["desc"]
    if not isinstance(desc, str) or not desc.startswith("【提示词版本 v7"):
        raise SystemExit(f"FAIL desc 不以「【提示词版本 v7」开头：{desc[:40]!r}")
    cam = blocks["camera"]
    for k in ("安静运镜_多样", "空镜卡池", "画外音指令"):
        if k not in cam:
            raise SystemExit(f"FAIL camera 缺键 {k}")
    tail = blocks["tail"]
    if "闭眼_安静" not in tail or "停顿强调" not in tail:
        raise SystemExit(f"FAIL tail 缺键，现有 {list(tail)[:8]}")
    pats = (blocks["problem"] or {}).get("patterns") or []
    if len(pats) < 4:
        raise SystemExit(f"FAIL problem.patterns 只有 {len(pats)} 条")
    if "tail_notes" not in blocks or "versions" not in blocks:
        raise SystemExit("FAIL all_blocks 缺 tail_notes / versions")
    print(f"OK all_blocks：desc {len(desc)} 字，camera {len(cam)} 键，"
          f"tail {len(tail)} 键，problem {len(pats)} 条，版本 {blocks['versions']}")
    return blocks


def test_find_problem() -> None:
    src = "Stay for 7 seconds and type Amen. God bless you."
    got = prompts.find_problem_sentences(src)
    want = ["Stay for 7 seconds and type Amen."]
    if got != want:
        raise SystemExit(f"FAIL find_problem_sentences：{got!r} ≠ {want!r}")
    print(f"OK find_problem_sentences → {got}")

    prompts.set_block("problem", {
        "patterns": [{
            "kind": "phrase", "value": "God bless you", "lang": "en",
            "note": "验收用", "source": "user",
        }],
        "template": "包括{原话}这句话也要读",
    })
    got2 = prompts.find_problem_sentences(src)
    prompts.reset_block("problem")
    if got2 != ["God bless you."]:
        raise SystemExit(f"FAIL phrase 包含匹配：{got2!r}")
    print(f"OK phrase 包含匹配 → {got2}")


def test_build_final() -> str:
    m = Manifest.new("T0007", "测试祷文", "flow", "luca-1", "long", SEG_TEXT)
    m.images = [{
        "p": "p01", "file": "images/p01.jpg", "sha1": "x",
        "role": "主体", "status": "ok",
        "desc": DESC, "voice": VOICE, "eye": "闭眼", "scene": "常规",
        "measure": {}, "origin": "download", "src": "",
    }]
    m.segments = [{
        "s": 1, "text": SEG_TEXT, "duration": 8, "image": "p01",
        "role": "主体", "shot": "", "prompt_final": "",
        "status": "planned", "gen_id": None, "takes": [], "error": None,
    }]
    prompt = prompts.build_final_prompt(m, m.segments[0])
    print("\n========== 成品提示词 ==========")
    print(prompt)
    print("========== 以上全文 ==========\n")

    missing = []
    if "非洲的中年40岁左右女性基督徒" not in prompt:
        missing.append("描述语正文")
    if "【镜头】" not in prompt:
        missing.append("运镜卡")
    if "拍摄指令" not in prompt and "一字不差" not in prompt:
        missing.append("结语")
    if SEG_TEXT not in prompt and "Stay for 7 seconds and type Amen. God bless you." not in prompt:
        missing.append("台词")
    if missing:
        raise SystemExit(f"FAIL build_final_prompt 缺：{'、'.join(missing)}")
    if "Stay for 7 seconds and type Amen." not in prompt:
        print("  注：停顿原句没有出现在结语里（检查 {停顿强调} 填充）")
    else:
        print("OK 停顿强调已填入原句")
    print("OK build_final_prompt 含描述语正文 + 运镜卡 + 结语 + 台词")
    return prompt


def test_reset_roundtrip() -> None:
    orig = prompts.get_block("desc")
    prompts.set_block("desc", orig + "\n# 用户改过")
    if not prompts.get_block("desc").endswith("# 用户改过"):
        raise SystemExit("FAIL set_block 没写进去")
    prompts.reset_block("desc")
    if prompts.get_block("desc") != orig:
        raise SystemExit("FAIL reset_block 没回到默认")
    print("OK set_block / reset_block 往返")


def _prod_key() -> str:
    k = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
    if k:
        return k
    dbp = ROOT / "data" / "orante.db"
    if not dbp.is_file():
        return ""
    try:
        c = sqlite3.connect(f"file:{dbp.as_posix()}?mode=ro", uri=True)
        r = c.execute("SELECT value FROM settings WHERE key=?", ("gemini.api_key",)).fetchone()
        c.close()
    except Exception:
        return ""
    if not r:
        return ""
    v = r[0]
    try:
        v = json.loads(v)
    except Exception:
        pass
    return str(v or "").strip()


def test_gemini() -> None:
    try:
        Gemini(api_key="")
    except OranteError as e:
        blob = (e.message or "") + (e.hint or "")
        if "设置" not in blob:
            raise SystemExit(f"FAIL 没 Key 的错误信息里没有「设置」：{blob!r}")
        print(f"OK 没 Key：{e.message} / {e.hint}")
    else:
        raise SystemExit("FAIL 没 Key 应该抛 OranteError")

    key = _prod_key()
    if not key:
        print("SKIP 无 Key，跳过 Gemini().ask_text 联网")
        return
    g = Gemini(api_key=key)
    text = g.ask_text("回复 OK")
    print(f"Gemini 原文：{text!r}")
    if "OK" not in text.upper() and "ok" not in text.lower():
        raise SystemExit(f"FAIL ask_text 返回不含 OK：{text!r}")
    print("OK Gemini().ask_text 含 OK")


def main() -> int:
    print(f"ORANTE_DATA = {TEST_DATA}")
    test_all_blocks()
    test_find_problem()
    test_reset_roundtrip()
    test_build_final()
    test_gemini()
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
