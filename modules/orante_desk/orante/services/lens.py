# -*- coding: utf-8 -*-
"""看图写运镜 —— 测量、拼句、体检三层。

【为什么要分成三层】
直接让 AI 写运镜句子，它会写出画面里根本没有的东西：
「摄影机升到高过人头的位置」——> 模型于是在前景摆几个人头。
「两侧的舞台灯光依次向后退去」——> 凭空造出一排灯。
这些不是模型不听话，是**提示词里确实写了那些名词**。

所以分工改成：

    测量   AI 只准回结构化的档位（景别 / 朝向 / 余量 / 人数…），一个字散文都不要
    拼句   句子由程序从白名单模板里拼，AI 碰不到句子
    体检   再扫一遍成品，出现画面里没有的物体名词一律打回

模型物理上没有机会把想象出来的东西写进提示词 —— 它压根不产出句子。

【风格】沿用 flow_shots.json 的「安静模式」：小幅度、一镜到底、不切镜。
    【镜头】一镜到底。手持稳定器起幅于人物中景，机位与人物视线同高，浅景深。
    摄影机…，景别由中景收成近景，落幅是面部特写，节奏舒缓、幅度克制。
"""
from __future__ import annotations

import json
import re

# 景别阶梯，从远到近
LADDER = ["远景", "全景", "中景", "近景", "特写"]
FACING = ("左", "右", "正面", "背面")
ROOM = ("窄", "中", "宽")
LEVEL = ("平视", "略仰", "略俯")


# ------------------------------------------------------------ 一、测量

MEASURE_PROMPT = """你是摄影指导的助手。看这张剧照，只做测量，不做描写。

严格只输出一个 JSON 对象，不要 markdown 代码块，不要任何解释文字。
每个字段只能填给定的选项之一，不许自创词，不许写句子。

{
  "shot_size": "远景|全景|中景|近景|特写",
  "facing": "左|右|正面|背面",
  "room_left": "窄|中|宽",
  "room_right": "窄|中|宽",
  "headroom": "紧|中|宽",
  "depth": "浅|中|深",
  "people": 整数,
  "crowd": true 或 false,
  "seated": true 或 false,
  "level": "平视|略仰|略俯"
}

判定口径：
- shot_size 按主体人物在画幅里的占比：特写=只有头和肩，近景=胸口以上，
  中景=腰以上，全景=全身可见，远景=人物很小、环境占主导。
- facing 是人物身体和视线朝向画面的哪一侧；正对镜头写"正面"。
- room_left / room_right 是人物左右两侧还剩多少可以让镜头横向移动的空间。
- headroom 是头顶到画幅上沿的余量。
- depth 是人物与背景之间看起来有多少纵深，决定镜头还能不能往前走。
- people 是画面里能看清的人数（含主体）。
- crowd 只有在主体被一圈人围住时才是 true。
- seated 只有在主体明显是坐着时才是 true。
- level 是拍摄这张图时相机相对人物视线的高度。

只回 JSON。"""


def parse_measure(text):
    """把模型回的东西读成一份干净的测量。读不出来就抛异常，绝不猜。"""
    raw = (text or "").strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("回的不是 JSON：%s" % raw[:80])
    data = json.loads(m.group(0))

    def pick(key, opts, default):
        v = str(data.get(key) or "").strip()
        return v if v in opts else default

    people = data.get("people")
    try:
        people = max(1, int(people))
    except (TypeError, ValueError):
        people = 1
    return {
        "shot_size": pick("shot_size", LADDER, "中景"),
        "facing": pick("facing", FACING, "正面"),
        "room_left": pick("room_left", ROOM, "中"),
        "room_right": pick("room_right", ROOM, "中"),
        "headroom": pick("headroom", ("紧", "中", "宽"), "中"),
        "depth": pick("depth", ("浅", "中", "深"), "中"),
        "people": people,
        "crowd": bool(data.get("crowd")),
        "seated": bool(data.get("seated")),
        "level": pick("level", LEVEL, "平视"),
    }


# ------------------------------------------------------------ 二、拼句

def _step(size, n=1):
    i = LADDER.index(size)
    return LADDER[max(0, min(len(LADDER) - 1, i + n))]


# ── 拼句用的全部词汇（0901 换口径）──
#
# 实拍反馈里四类说法会直接把画面拍坏，一个都不许出现：
#   「收成/收到近景」 → 被理解成圆环往里收拢，出来是圆形转场
#   「手持稳定器/摇臂/轨道」 → 器材被真的画进画面里，穿帮
#   「沿弧线绕行 90 度/环绕」 → 各种奇怪的形状类切换
#   英文段 → 对出片没意义，还多一份被念出来的风险
# 另外景别词一个都不写进句子 —— 景别只用来决定「能选哪种运动」。
# 全部用正面表述：不写「不要转场」，因为句子里一旦出现那两个字，
# 模型照样可能给你来一个。

HEAD = "【镜头】全片是一整段连续拍下来的画面。"
TAIL = "运动要轻微柔和、明显，不要完全不动的固定镜头。人物始终留在画面中间。"

MOVES = {
    "靠近": "镜头轻微明显地向前推进一点",
    "微靠近": "镜头轻微明显地向前推进一点点",
    "退开": "镜头轻微明显地向后退开一点",
    "左移": "镜头轻微明显地向画面左侧平移",
    "右移": "镜头轻微明显地向画面右侧平移",
    "抬高": "镜头轻微明显地向上抬高一点",
    "降低": "镜头轻微明显地向下降低一点",
    "靠近偏左": "镜头轻微明显地向前推进，同时略微向左平移",
    "靠近偏右": "镜头轻微明显地向前推进，同时略微向右平移",
    "浮动": "镜头只做很小幅度的移动，位置变化不大",
}

CROWD_NOTE = "镜头始终留在人群外围，画面里其他人全程留在原处"
SEAT_NOTE = "人物坐在原位不起身，画面里的位移全部由镜头承担"

# 人群图只允许这几种 —— 全是直线移动，没有任何绕行
CROWD_MOVES = ("微靠近", "靠近", "左移", "右移", "抬高", "降低", "浮动")


def allowed_moves(m):
    """这张图允许哪些运动。景别、余量、纵深、人群，四道一起卡。

    景别只在这里起作用（决定能不能再靠近），不写进句子里。
    """
    size = m["shot_size"]
    out = []

    if size == "特写":
        out.append("微靠近")
    elif size == "近景":
        out += ["微靠近", "靠近"] if m["depth"] != "浅" else ["微靠近"]
    else:
        out += ["靠近", "微靠近"] if m["depth"] != "浅" else ["微靠近"]

    if size in ("特写", "近景", "中景"):
        out.append("退开")

    for side, room, key in (("左", m["room_left"], "左移"),
                            ("右", m["room_right"], "右移")):
        if room == "窄":
            continue
        out.append(key)
        if size not in ("特写",) and m["depth"] != "浅":
            out.append("靠近偏左" if side == "左" else "靠近偏右")

    if m["headroom"] == "宽" and size != "特写":
        out.append("降低")
    if m["level"] == "略仰" and size != "特写":
        out.append("抬高")

    out.append("浮动")

    if m["crowd"]:
        out = [x for x in out if x in CROWD_MOVES]
    seen, uniq = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def compose(m, move):
    """把一次运动拼成一张运镜卡。句子全部来自上面的白名单。"""
    bits = [HEAD, MOVES[move], "。"]
    if m.get("crowd"):
        bits.append(CROWD_NOTE + "。")
    if m.get("seated"):
        bits.append(SEAT_NOTE + "。")
    bits.append(TAIL)
    return "".join(bits)


def fingerprint(m, move):
    """运镜指纹 =（主轴, 方向, 起幅景别）。

    去重比的是这个，不是句子本身 —— 换个说法的同一个运动要算重复。
    """
    axis = {"靠近": "近", "微靠近": "近", "靠近偏左": "近", "靠近偏右": "近",
            "退开": "远", "左移": "移", "右移": "移",
            "抬高": "升降", "降低": "升降", "浮动": "静"}.get(move, move)
    direction = ("左" if "左" in move else
                 "右" if "右" in move else
                 "上" if move == "抬高" else
                 "下" if move == "降低" else "-")
    return (axis, direction, m["shot_size"])


# ------------------------------------------------------------ 三、体检

# 分三档，别把不同性质的问题混在一起报。

# 一、器材词：摇臂、轨道这些东西【永远不在画面里】。写进提示词，
#    模型不知道那是拍摄工具，只会照着画一条轨道、一台摇臂出来。
RIG = ["摇臂", "轨道车", "轨道", "滑轨", "斯坦尼康", "升降臂", "摄影车",
       "吊臂", "云台车", "稳定器", "手持稳定器", "摇臂车"]

# 形状 / 转场词：出来的不是镜头运动，是一个奇怪的形状变化。
# 「收成/收到近景」——「收」被理解成圆环往里收拢；
# 「弧线/绕行/环绕」——出来是绕着转的形状切换。
SHAPE = ["弧线", "绕行", "环绕", "绕过", "绕向", "收成", "收到", "收拢",
         "转场", "切镜", "叠化", "淡入", "淡出", "画面切换", "景别"]

# 二、凭空词：拿画面里没有的东西当参照物。
#    「升到高过人头的位置」——> 模型先给你摆几个人头，好让镜头有得可高过。
INVENT = ["人头", "头顶上方", "过头顶", "高过", "齐肩", "越过前排肩膀",
          "前排肩膀"]

# 三、景物词：可能真在画面里，也可能是瞎写的 —— 要看场景才知道。
SCENE = {
    "舞台": "舞台", "讲台": "舞台", "台面": "舞台", "台下": "舞台",
    "灯光": "*", "射灯": "*", "追光": "*", "顶光": "*", "灯架": "*",
    "桌": "*", "椅": "车内", "座椅": "车内", "沙发": "*",
    "墙": "*", "柱": "*", "门": "*", "窗": "车内", "帘": "*",
    "地板": "*", "天花板": "*", "楼梯": "*",
    "话筒": "*", "音箱": "*", "屏幕": "*", "投影": "*",
    "十字架": "*", "圣坛": "*", "旗": "*",
    "树": "*", "草地": "*", "街道": "*", "建筑": "*",
    "车辆": "车内", "车窗": "车内", "车身": "车内", "车厢": "车内",
    "方向盘": "车内", "挡风玻璃": "车内",
}

# 四、人群词：只有画面里确实有那一圈人时才算数。
CROWD_WORDS = ["人群", "人墙", "信众", "会众", "观众", "一圈人", "人与人"]
SEATED_WORDS = ["坐在", "起身", "落座"]


def lint(card, m=None, scene=None):
    """扫一张运镜卡。

    m: 这张图的测量（有就按测量放行 人群 / 坐姿）
    scene: 这张卡属于哪个场景池（常规 / 舞台 / 围绕 / 车内），
           用来判断景物词是不是本来就该在画面里。

    返回 [{"word", "level", "why"}]，level ∈ rig / invent / scene。
    三档性质完全不同，报告里别混着看。
    """
    text = card or ""
    m = m or {}
    crowd_ok = bool(m.get("crowd")) or scene in ("围绕", "群像")
    seated_ok = bool(m.get("seated")) or scene == "车内"
    hits = []
    for w in RIG:
        if w in text:
            hits.append({"word": w, "level": "rig",
                         "why": "拍摄器材不在画面里，写了模型会照着画一台出来"})
    for w in SHAPE:
        if w in text:
            hits.append({"word": w, "level": "shape",
                         "why": "会被理解成形状变化 / 转场，出来不是镜头运动"})
    import re as _re
    if _re.search(r"[A-Za-z]{4,}", text):
        hits.append({"word": "英文", "level": "shape",
                     "why": "运镜卡不需要英文，去掉"})
    for w in INVENT:
        if w in text:
            hits.append({"word": w, "level": "invent",
                         "why": "拿画面里没有的东西当参照物，模型会先造一个出来"})
    for w, need in SCENE.items():
        if w in text and not (need != "*" and scene == need):
            hits.append({"word": w, "level": "scene",
                         "why": "景物名词 —— 这张图里不一定有，交给模型自由发挥更稳"})
    for w in CROWD_WORDS:
        if w in text and not crowd_ok:
            hits.append({"word": w, "level": "scene",
                         "why": "只有测出画面里确实有那一圈人时才能写"})
    for w in SEATED_WORDS:
        if w in text and not seated_ok:
            hits.append({"word": w, "level": "scene",
                         "why": "只有测出是坐姿时才能写"})
    seen, out = set(), []
    for h in hits:
        if h["word"] in seen:
            continue
        seen.add(h["word"])
        out.append(h)
    return out


def audit_cards(cards, m=None, scene=None):
    """一批卡的体检报告。给现成的卡池用，抓存量问题。"""
    out = []
    for i, c in enumerate(cards):
        hits = lint(c, m, scene)
        if hits:
            out.append({"i": i, "card": c, "hits": hits})
    return out


# ------------------------------------------------------------ 编排

STAGE_BIAS = {
    "开场": ("推一档", "微推", "横移左", "横移右"),
    "中段": ("横移左", "横移右", "弧行左", "弧行右", "推一档", "下降"),
    "收束": ("微推", "浮动", "拉开", "升起"),
}


def assign(items, seed=1):
    """给一串段配运镜卡。

    items: [{"key", "m"(测量), "stage"("开场"/"中段"/"收束")}]
    约束：相邻 3 段的运镜指纹互不相同；同一指纹整批不超过 2 次。
    """
    import random
    rng = random.Random(seed)
    used = {}
    recent = []
    out = []
    for it in items:
        m = it["m"]
        pool = allowed_moves(m)
        if not pool:
            pool = ["浮动"]
        bias = STAGE_BIAS.get(it.get("stage") or "中段", ())
        pref = [x for x in pool if x in bias] or pool

        def score(mv):
            fp = fingerprint(m, mv)
            return (fp in recent, used.get(fp, 0), rng.random())

        best = min(sorted(pref, key=lambda x: rng.random()), key=score)
        fp = fingerprint(m, best)
        used[fp] = used.get(fp, 0) + 1
        recent.append(fp)
        if len(recent) > 3:
            recent.pop(0)
        card = compose(m, best)
        out.append({
            "key": it.get("key"), "move": best, "fp": "|".join(fp),
            "card": card, "lint": lint(card, m),
            "repeat": used[fp],
        })
    return out


def measure(image_path, gemini=None):
    """看图测量：用 Gemini 问 MEASURE_PROMPT，再走 parse_measure 收敛。"""
    from pathlib import Path
    from . import ai
    g = gemini or ai.client()
    raw = g.ask_image(MEASURE_PROMPT, Path(image_path), json_mode=True)
    return parse_measure(raw)
