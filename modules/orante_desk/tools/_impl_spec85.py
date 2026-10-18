# -*- coding: utf-8 -*-
"""规格 85 实施脚本：积木开关 + 跨任务批量填描述语。"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(r"D:\0_Dev\Orante")
BAK = ".bak-0921switches"
MUTE_TIP = "人物不说话。"

# 开关键（与 flow_shots 组装块名对齐；固定结语 UI → 说话指令）
BRICK_KEYS = [
    "运镜卡", "运动强制", "镜头禁令",
    "人物原地", "人物站姿", "人物坐姿", "比例锁", "车辆静止锁",
    "人群保留", "群像锁",
    "画质锁",
    "情绪", "音色锁",
    "说话指令",
]
META_KEYS = ["描述语整理", "静音提示"]
ALL_SWITCH_KEYS = BRICK_KEYS + META_KEYS
# 极简模式关掉的键（静音提示除外）
MINIMAL_OFF_KEYS = BRICK_KEYS + ["描述语整理"]


def backup(p: Path) -> None:
    bak = Path(str(p) + BAK)
    if not bak.exists():
        shutil.copy2(p, bak)
        print("backup", bak.name)
    else:
        print("backup exists", bak.name)


def write_lf(p: Path, text: str) -> None:
    data = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    p.write_bytes(data)


def patch_flow_shots() -> None:
    p = ROOT / "orante" / "legacy" / "flow_shots.py"
    backup(p)
    text = p.read_text(encoding="utf-8")
    # 在 build_prompt 前插入开关工具函数（若尚无）
    helper = '''
# --- 规格 85：提示词积木开关 -----------------------------------------------
# 默认全开。缺省键视为 True，保证升级后成品提示词与改前逐字节一致。
SWITCH_BRICK_KEYS = (
    "运镜卡", "运动强制", "镜头禁令",
    "人物原地", "人物站姿", "人物坐姿", "比例锁", "车辆静止锁",
    "人群保留", "群像锁",
    "画质锁",
    "情绪", "音色锁",
    "说话指令",
)
SWITCH_META_KEYS = ("描述语整理", "静音提示")
SWITCH_ALL_KEYS = SWITCH_BRICK_KEYS + SWITCH_META_KEYS
MUTE_TIP_DEFAULT = "人物不说话。"


def default_switches():
    """全部积木默认开。"""
    return {k: True for k in SWITCH_ALL_KEYS}


def normalize_switches(switches=None):
    """合并用户开关；None / 空 → 全开。未知键忽略。"""
    out = default_switches()
    if not switches:
        return out
    if not isinstance(switches, dict):
        return out
    for k, v in switches.items():
        if k in out:
            out[k] = bool(v)
    return out


'''
    if "def normalize_switches" not in text:
        text = text.replace(
            "\ndef build_prompt(",
            helper + "\ndef build_prompt(",
            1,
        )

    # 改签名：加 switches=None
    old_sig = (
        'def build_prompt(desc, card, eye="闭眼", scene="常规", seg="", shots=None,\n'
        '                 seg_idx=None, n_segs=None, voice_lock=""):'
    )
    new_sig = (
        'def build_prompt(desc, card, eye="闭眼", scene="常规", seg="", shots=None,\n'
        '                 seg_idx=None, n_segs=None, voice_lock="", switches=None):'
    )
    if old_sig not in text:
        raise SystemExit("build_prompt signature not found")
    text = text.replace(old_sig, new_sig, 1)

    # 在 shots = shots or load_shots() 后插入 switches 归一化
    anchor = "    shots = shots or load_shots()\n"
    inject = (
        "    shots = shots or load_shots()\n"
        "    sw = normalize_switches(switches)\n"
    )
    # 只改 build_prompt 内第一处
    idx = text.find(new_sig)
    body_start = text.find(anchor, idx)
    if body_start < 0:
        raise SystemExit("shots = shots or load_shots not found in build_prompt")
    if "sw = normalize_switches" not in text[body_start:body_start + 200]:
        text = text[:body_start] + inject + text[body_start + len(anchor):]

    # 描述语整理开关：包住 strip/defreeze/dedupe
    old_clean = '''    body = strip_legacy_tail(desc)
    # 安静模式：A 层里的"别动"全删，统一由「人物原地」那一块说一次。
    # 车内例外 —— 那边不加「人物原地」块，"别动"仍由 defreeze 改写的
    # 「坐在座位上不挪动」承担；但点名脚的句子照删不误（理由见 _FEET）。
    if quiet:
        body = strip_stillness(body) if not is_car else strip_feet(body)
    if not is_car or shots.get("车内允许运镜", True):
        body = defreeze(body, scene)
    # 去掉和程序自己要加的块重复的句子（详见 _DEDUPE 上方的复盘）。
    # 想关掉对照：flow_shots.json 里 身份锁去重 = false。
    if shots.get("身份锁去重", True):
        body = dedupe_body(body)
'''
    # 实际文件可能用不同引号/注释，用更宽松替换
    # 定位 body = strip_legacy_tail
    m = re.search(
        r'(    body = strip_legacy_tail\(desc\)\n)'
        r'(?:.*\n)*?'
        r'(    if shots\.get\("身份锁去重", True\):\n'
        r'        body = dedupe_body\(body\)\n)',
        text[idx:],
    )
    if not m:
        # try alternate quote / name variants from dump
        m = re.search(
            r'(    body = strip_legacy_tail\(desc\)\n)'
            r'(?:.*\n)*?'
            r'(    if shots\.get\(.身份锁去重., True\):\n'
            r'        body = dedupe_body\(body\)\n)',
            text[idx:],
        )
    if not m:
        raise SystemExit("desc cleanup block not found")
    abs_start = idx + m.start()
    abs_end = idx + m.end()
    new_clean = (
        "    # 规格 85：描述语整理关 → 描述语一字不改进身份锁\n"
        "    if sw.get(\"描述语整理\", True):\n"
        "        body = strip_legacy_tail(desc)\n"
        "        if quiet:\n"
        "            body = strip_stillness(body) if not is_car else strip_feet(body)\n"
        "        if not is_car or shots.get(\"车内允许运镜\", True):\n"
        "            body = defreeze(body, scene)\n"
        "        if shots.get(\"身份锁去重\", True):\n"
        "            body = dedupe_body(body)\n"
        "    else:\n"
        "        body = desc\n"
    )
    text = text[:abs_start] + new_clean + text[abs_end:]

    # 过滤组装顺序
    old_parts = "    parts = [blocks.get(k, \"\") for k in order]\n"
    new_parts = (
        "    # 规格 85：跳过开关为 False 的积木；身份锁永远保留\n"
        "    parts = []\n"
        "    for k in order:\n"
        "        if k != \"身份锁\" and not sw.get(k, True):\n"
        "            continue\n"
        "        parts.append(blocks.get(k, \"\"))\n"
    )
    # only replace within build_prompt — first occurrence after idx is fine
    pos = text.find(old_parts, idx)
    if pos < 0:
        raise SystemExit("parts = line not found")
    text = text[:pos] + new_parts + text[pos + len(old_parts):]

    # 返回值：无文案 + 静音提示开 + 说话指令关 → 追加静音提示
    old_ret = '    return f"{head}\\n{seg}" if seg else head\n'
    new_ret = (
        '    if seg:\n'
        '        return f"{head}\\n{seg}" if head else seg\n'
        '    # 静音提示（86）：无文案且说话指令关掉时才追加，避免默认全开改变旧行为\n'
        '    if sw.get("静音提示", True) and not sw.get("说话指令", True):\n'
        '        tip = (shots.get("静音提示") or MUTE_TIP_DEFAULT).strip()\n'
        '        if tip:\n'
        '            return f"{head}\\n{tip}" if head else tip\n'
        '    return head\n'
    )
    pos = text.find(old_ret, idx)
    if pos < 0:
        raise SystemExit("return head line not found")
    text = text[:pos] + new_ret + text[pos + len(old_ret):]

    write_lf(p, text)
    print("patched flow_shots.build_prompt")


def patch_prompts() -> None:
    p = ROOT / "orante" / "services" / "prompts.py"
    backup(p)
    text = p.read_text(encoding="utf-8")

    helpers = '''
# --- 规格 85：prompts.switches ---------------------------------------------
def default_switches() -> dict:
    from orante.legacy import flow_shots as fs
    return fs.default_switches()


def get_switches() -> dict:
    """读 prompts.switches；缺省全开。"""
    from orante.legacy import flow_shots as fs
    raw = _cfg_get("prompts.switches", None)
    return fs.normalize_switches(raw if isinstance(raw, dict) else None)


def set_switches(switches) -> dict:
    """写入 prompts.switches，返回归一化后的字典。"""
    from orante.legacy import flow_shots as fs
    if switches is None:
        _cfg_reset("prompts.switches")
        return fs.default_switches()
    if not isinstance(switches, dict):
        raise OranteError("switches 必须是对象")
    norm = fs.normalize_switches(switches)
    _cfg_set("prompts.switches", norm)
    return norm


def apply_minimal(on: bool) -> dict:
    """极简模式：开=关掉除静音提示外所有积木并记住先前状态；关=恢复。"""
    from orante.legacy import flow_shots as fs
    cur = get_switches()
    if on:
        prev = _cfg_get("prompts.switches_before_minimal", None)
        if not isinstance(prev, dict):
            # 记住当前（极简打开前）
            _cfg_set("prompts.switches_before_minimal", dict(cur))
        off = fs.default_switches()
        for k in fs.SWITCH_BRICK_KEYS + ("描述语整理",):
            off[k] = False
        # 静音提示保持当前值
        off["静音提示"] = bool(cur.get("静音提示", True))
        return set_switches(off)
    # 关极简：恢复记忆，没有记忆则全开
    prev = _cfg_get("prompts.switches_before_minimal", None)
    _cfg_reset("prompts.switches_before_minimal")
    if isinstance(prev, dict):
        return set_switches(prev)
    return set_switches(fs.default_switches())


'''
    # Fix imports - use path that works. prompts already does paths.add_legacy and import flow_shots
    helpers = helpers.replace(
        "from orante.legacy import flow_shots as fs",
        "import flow_shots as fs",
    )

    if "def get_switches" not in text:
        # insert before build_final_prompt
        marker = "\ndef build_final_prompt("
        if marker not in text:
            raise SystemExit("build_final_prompt not found")
        text = text.replace(marker, "\n" + helpers + marker, 1)

    # all_blocks 加 switches
    if '"switches":' not in text and "'switches':" not in text:
        text = text.replace(
            '        "versions": versions,\n    }',
            '        "versions": versions,\n'
            '        "switches": get_switches(),\n'
            '    }',
            1,
        )

    # set_block 支持 name=="switches"
    # 找 set_block 开头加分支
    if 'if name == "switches"' not in text:
        m = re.search(r"def set_block\(name: str, value\) -> None:\n", text)
        if not m:
            m = re.search(r"def set_block\(.*\n", text)
        if not m:
            raise SystemExit("set_block not found")
        insert_at = m.end()
        # skip docstring if any
        rest = text[insert_at:]
        if rest.lstrip().startswith('"""'):
            end = rest.find('"""', 3) + 3
            insert_at = insert_at + end
            if text[insert_at:insert_at+1] == "\n":
                insert_at += 1
        branch = (
            '    if name == "switches":\n'
            '        set_switches(value)\n'
            '        return\n'
            '    if name == "minimal":\n'
            '        apply_minimal(bool(value))\n'
            '        return\n'
        )
        text = text[:insert_at] + branch + text[insert_at:]

    # build_final_prompt 传入 switches
    old_call = (
        "    prompt = flow_shots.build_prompt(\n"
        "        desc, card, eye, scene, seg_text, shots,\n"
        "        seg_idx=seg_idx, n_segs=n_segs, voice_lock=voice)"
    )
    new_call = (
        "    prompt = flow_shots.build_prompt(\n"
        "        desc, card, eye, scene, seg_text, shots,\n"
        "        seg_idx=seg_idx, n_segs=n_segs, voice_lock=voice,\n"
        "        switches=get_switches())"
    )
    if old_call not in text:
        # try voice vs voice_lock naming
        old_call2 = old_call.replace("voice_lock=voice)", "voice_lock=voice)")
        if old_call not in text:
            # dump nearby
            i = text.find("flow_shots.build_prompt(")
            print("CALL SITE:\n", text[i:i+250])
            raise SystemExit("build_prompt call not found")
    text = text.replace(old_call, new_call, 1)

    # _cfg_reset may not exist - check
    if "_cfg_reset" not in text and "def _cfg_reset" not in text:
        # add helper near _cfg_set
        text = text.replace(
            "def _cfg_set(key: str, value) -> None:\n"
            "    from .. import config\n"
            "    config.set(key, value)\n",
            "def _cfg_set(key: str, value) -> None:\n"
            "    from .. import config\n"
            "    config.set(key, value)\n"
            "\n"
            "\n"
            "def _cfg_reset(key: str) -> None:\n"
            "    from .. import config\n"
            "    config.reset(key)\n",
            1,
        )

    write_lf(p, text)
    print("patched prompts.py")


if __name__ == "__main__":
    patch_flow_shots()
    patch_prompts()
    print("part1 done")