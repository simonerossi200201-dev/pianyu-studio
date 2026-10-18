# -*- coding: utf-8 -*-
"""B 层运镜卡 · 确定性生成器
===============================
把提示词拆成两层，各管各的：

  A 层【身份锁】  图片旁 vNN_desc.txt（旧批次退回 _desc_cache.json），一条视频共用一份，全程不变。
                  人物是谁、口音、手里拿什么、睁眼闭眼、现场环境、服装一致性。
                  这些必须逐段一字不差 —— 一旦跟着镜头一起变，同一条视频里人就漂了。
                  它由 描述语生成器.html 调 Gemini 多模态看图写出来，精准且智能，照旧沿用。

  B 层【运镜卡】  本模块生成。只写镜头运动 + 情绪落点。

这样一段之内【只有一个连续镜头】，不在 8 秒里硬切来切去。

—— 0820 大改版：安静模式 ——
以前 B 层是【逐段不同】的：开场推近、中段环绕、收束拉远，一条视频拼起来花样很多。
做祷告视频时这是错的 —— 一会儿绕圈一会儿拉全景，安静感全被打散，而且每一段都是拿
【同一张起始图】单独生成的 8 秒，景别一段一个样，拼起来就像在硬切转场。

现在默认走「安静模式」（flow_shots.json 的 运镜模式 = "安静"）：
  · 一条视频【所有段落共用同一张运镜卡】，一段就是一条完整连贯的镜头运动 ——
    起幅中景，沿一条略带偏移的路线缓慢推进，落幅近景。立体感靠浅景深 + 机位
    真的在推进 + 路线略带偏移带来的视角变化，不靠绕圈。
    绝不环绕、不拉全景、不切镜、不做任何转场溶接。
  · 段与段之间唯一变化的是【情绪】：起（克制）→ 承（加深）→ 转（最饱满）→
    合（收回）。情绪是表演不是构图，逐段递进不会让拼接处跳。
  · 闭眼（祷告）：人物保持原位，画面里的位移全部由摄影机承担
    （落幅是近景还是面部特写，看这个编号抽到哪张卡，见下面 0829 补丁）。
    睁眼（讲道/演讲）：人物照旧真的走动，镜头是【跟拍推镜】—— 同步跟随的同时
    缓慢推近，落幅正面近景。跟随和推进是同一条运动，不是两条打架的指令。
闭眼和睁眼默认都走安静模式（安静模式适用 = ["闭眼","睁眼"]）。
想让某一类退回老的多镜头轮换：把它从名单里去掉；想全部退回：运镜模式 改成 "多镜头"。

—— 0829 补丁：安静 ≠ 每条都一样 ——
安静模式最初只有一张卡，整批 92 条全是「中景缓慢推近 → 面部特写」。安静是做到了，
但一批片子接起来每一段都在做同一个推镜，观感极其雷同。
所以 安静运镜多样 默认打开：按 v 号从 安静运镜_多样[场景]（睁眼用
安静运镜_多样_睁眼[场景]）里取一张。卡池里推近的只占一半，其余是等距横移、
小幅弧线、缓慢后退、只升降不改景别的机位，另外机位高度也在平视 / 略仰 / 略俯
之间小幅变化 —— 变化全部发生在【编号之间】，一条视频内部仍然只有一条运动。
想退回整批同一张：flow_shots.json 里 安静运镜多样 改成 false。

—— 为什么 B 层不调 AI ——
运镜卡只取决于四个变量：第几段 / 共几段 / 睁眼还是闭眼 / 什么场景。
这四样在分段完成的那一刻就全知道了，不需要再看一次图。改成 AI 逐段生成的话：
  · 0805 这批要多打 1300 多次接口（现在是每张图 1 次，共 260 次）
  · 多一轮限流与格式失败的风险
  · 必须先有分段结果才能跑，工序从一趟变两趟
  · 分段一改，运镜卡就全部作废要重跑
确定性模板零额外成本、零失败率，而且分段变了运镜卡自动跟着变，永不脱节。

—— 手工覆盖 ——
程序会在图片旁边写出 vXX_shot.txt（一行一段）。想给某个编号单独设计镜头，
直接改这个文件即可；只要行数和分段数对得上，程序就用你写的，不再自动生成。
"""

import hashlib
import json
import os
import re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SHOTS_FILE = os.path.join(SCRIPT_DIR, "flow_shots.json")

# A 层描述语结尾的机读标签，如 【闭眼|常规】【睁眼|舞台】【空镜|舞台】【闭眼|车内】【睁眼|围绕】
TAG_RE = re.compile(r"【\s*(闭眼|睁眼|空镜)\s*[|｜]\s*(常规|舞台|车内|围绕|群像)\s*】\s*$")

EYES = ("闭眼", "睁眼", "空镜")
# 围绕 = 人物被一圈人紧紧围住（围圈祷告、按手、合围聆听），双脚挪不开路。
#        和「舞台」的区别是距离：舞台上的人和观众隔着台沿，走得开；围绕走不开。
#        单独成一类是因为它需要两样别的场景不需要的东西 ——
#        ① 温和版走动强制（硬要走三五步，模型会把人清场腾道，见 json 里的说明）
#        ② 人群保留块（起始帧里的其他人默认会被当背景淡掉）
# 群像 = 一群人一起祷告，画面里【没有主角】（围成一圈、一排低头站着都算）。
#        和「围绕」的区别只有一条：围绕有一个明确的中心人物被人围着，群像没有。
#        这一条差别决定了两件完全不同的事 ——
#        ① 群像走画外音（没有人对镜头说话），围绕仍然是主角在说
#        ② 群像的镜头永远不落到某一张脸上，围绕的镜头最终要落到主角脸上
#        由制作人在表格 I 列指定（见 video_types.py），不靠 AI 看图猜。
SCENES = ("常规", "舞台", "车内", "围绕", "群像")

# 群像永远走画外音那条通道：即使描述语的眼睛位写的是「闭眼」也一样。
# 这样制作人只要在 I 列写「群体祷告」，无论 AI 把眼睛位判成什么都不会跑偏。
def is_group(eye, scene):
    return eye == "空镜" or scene == "群像"

_cache = None


def load_shots(force=False):
    global _cache
    if _cache is None or force:
        with open(SHOTS_FILE, "r", encoding="utf-8") as f:
            _cache = json.load(f)
    return _cache


# ---------------- A 层标签解析 ----------------

def parse_tag(desc):
    """从 A 层描述语里取出 (眼睛状态, 场景类型, 去掉标签后的正文)。

    优先读结尾的机读标签 —— 4 个字，100% 可靠。
    读不到才退回从行文里猜（老批次的描述语没有标签，要保持兼容）。
    两条都失败时返回 ("闭眼", "常规")：这是最安全的默认，因为闭眼组不会
    让人物走动，猜错也只是镜头保守一点，不会出现"闭着眼到处走"的穿帮。
    """
    desc = (desc or "").strip()
    first = desc.splitlines()[0].strip() if desc else ""
    m = TAG_RE.search(first)
    if m:
        eye, scene = m.group(1), m.group(2)
        # —— 纠正 描述语生成器 打错的「舞台」——
        # 0811 复盘：v01 正文明写「被众人围在中间」「双脚只在原地小幅活动」，
        # 标签却是【睁眼|舞台】。舞台组会套上「至少三到五步、横跨画面左右」的
        # 硬走动指令，于是正文说别走、指令说必须走 —— 模型两边都不做，人就
        # 杵在那里不动了。那批 7 段全部中招。
        # 标签是 Gemini 写的，会看错；正文里这两类词是它自己描述出来的，更可信。
        # 只做「舞台 → 围绕」这一个方向的纠正：反向纠正的代价不对等（把真舞台
        # 判成围绕只是镜头保守，把真围绕判成舞台会直接清场腾道）。
        if scene == "舞台" and re.search(
                r"被众人围|围在中间|围成一圈|一圈人|簇拥|围拢|"
                r"双脚只在原地|原地小幅|走不开", desc):
            scene = "围绕"
        return eye, scene, TAG_RE.sub("", first).strip()

    # —— 兼容老格式：从行文里推断 ——
    # 顺序有讲究：车内 > 围绕 > 舞台 > 常规。
    # 围绕排在舞台前面，是因为"台上带领众人祷告"这类描述两边关键词都会命中，
    # 而判成围绕只是镜头保守一点、多一句人群保留；判成舞台却会套上
    # "至少三到五步、横跨画面左右"的硬指令，一旦人物其实是被围住的，
    # 模型就会清场腾道。猜错的代价不对等，所以往保守的一边倒。
    scene = "常规"
    if re.search(r"车内|车里|驾驶|方向盘|副驾|车窗", desc):
        scene = "车内"
    elif re.search(r"围成一圈|围成一个圈|手拉手|手牵手|围绕着|众人围|围在中间|"
                   r"周围站满|一圈人|簇拥|按手|围拢", desc):
        scene = "围绕"
    elif re.search(r"舞台|讲台|台上|礼堂|会场|观众席|演讲", desc):
        scene = "舞台"

    # 以正文中【最后一次】提到的眼睛状态为准（和 HTML 里 repairTail 同一套逻辑）
    def last_idx(pattern):
        i = -1
        for mm in re.finditer(pattern, desc):
            i = mm.start()
        return i

    c = last_idx(r"闭着眼|紧闭双眼|双眼紧闭|闭眼|闭上眼")
    o = last_idx(r"睁着眼|睁开眼|睁着双眼|睁眼")
    eye = "睁眼" if o > c else "闭眼"
    return eye, scene, desc


def strip_tag(desc):
    text = (desc or "").strip()
    first = text.splitlines()[0].strip() if text else ""
    return TAG_RE.sub("", first).strip()


# 老格式描述语自带的固定结语。这些内容现在由程序统一附加，如果 A 层里
# 还留着旧的一份，就会出现【两套结语同时存在】的局面：
#   旧的说"人物不要流泪，不要哭泣"，新的说"眼中可以饱含深情但不落泪"
#   旧的说"…说出这几句英文：",  新的又说一遍
# 模型收到两份措辞不同的同类指令只会更混乱。这里在拼装时把旧尾巴切掉，
# 于是【老表格不用重新生成描述语也能直接用新流程】，平滑过渡。
# —— 与 描述语生成器.html 里的 stripLegacyTail() 是同一套规则。
_LEGACY_TAIL_RES = [
    re.compile(r"[，,。;；]?\s*人物不要流泪[\s\S]*$"),
    re.compile(r"[，,。;；]?\s*整个视频禁止[\s\S]*$"),
    re.compile(r"[，,。;；]?\s*(她|他|人物)?必须要(闭着眼|睁着眼)[\s\S]*$"),
    re.compile(r"[，,。;；]?\s*(全程)?(禁止|不要)(添加)?任何背景音乐[\s\S]*$"),
]


def strip_legacy_tail(desc):
    """去掉老格式描述语尾部的固定结语，只留身份锁正文。"""
    t = strip_tag(desc)
    for r in _LEGACY_TAIL_RES:
        t = r.sub("", t)
    return t.strip().rstrip("，,、;；。.:：").strip()


# ── 解冻：把 A 层里"整个画面别动"的说法改成"人物不走动" ──────────────
# 描述语生成器写"人物原地不动"，本意是【双脚不移动位置】（闭眼祷告的人
# 当然不能到处走），但 Veo 会把它理解成【整个画面静止】，结果输出一张
# 不动的图 + 只有嘴在动。0807 那批 230 条里有 203 条带这个词。
# 与其让用户重跑 230 张图，不如在组装时就地改写 —— 只改这一处措辞，
# 人物该不走动还是不走动，但画面不再被冻住。
# 车内【0811 起也解冻】：车内开放小幅度运镜之后，A 层那句"画面保持静止"会
# 直接和运镜卡打架，模型遇到冲突指令就折中成不动，等于运镜卡白写。
# 但车里的人是【坐着】的，套用通用版的"站在原地不走动"会写出"坐在车里站着"，
# 所以车内单独一张替换表，改成"坐在座位上不挪动"。
# 车辆本身停不停，与此无关，由 车辆静止锁 那一块单独钉死。
_DEFREEZE = [
    (re.compile(r"全程保持原地不动"), "站在原地不走动"),
    (re.compile(r"原地不动"), "站在原地不走动"),
    (re.compile(r"保持静止不动"), "站在原地不走动"),
    (re.compile(r"(画面|整个视频|整个画面)保持静止"), r"人物站在原地不走动"),
]

_DEFREEZE_CAR = [
    (re.compile(r"全程保持原地不动"), "坐在座位上不挪动"),
    (re.compile(r"原地不动"), "坐在座位上不挪动"),
    (re.compile(r"保持静止不动"), "坐在座位上不挪动"),
    (re.compile(r"(画面|整个视频|整个画面)保持静止"), r"人物坐在座位上不挪动"),
]


def defreeze(text, scene="常规"):
    """把 A 层里"整个画面别动"的说法改写成"人物不移动位置"。
    scene 省略时按通用版处理（老调用方无需改动）。"""
    for r, rep in (_DEFREEZE_CAR if scene == "车内" else _DEFREEZE):
        text = r.sub(rep, text)
    return text


# ── 去重：把 A 层里和程序自己要加的块【重复的句子】删掉 ─────────────
# 0811 复盘：v01 那批提示词里，"别动"的话被说了五遍、"动"的话只说了一遍 ——
#   ① 身份锁："双手和萨克斯风的位置保持不变"      ← 把手钉死，情绪手势全没了
#   ② 身份锁："双脚只在原地小幅活动：原地转身…"   ← 和 人物走动强制 逐字重复
#   ③ 身份锁："全程完整保留在画面里，不消失、不减少、不变透明" ← 和 人群保留 重复
#   ④ 人群保留（程序加的，说第二遍）
#   ⑤ 人物走动强制（程序加的，说第二遍）
# 模型按权重投票，五比一，于是输出一张静止照片。
#
# 这里只删【程序自己会补上的那几句】，身份特征（长相、服装、场地、人数）
# 一个字不动 —— 那些才是身份锁真正要锁的东西，删了人就漂了。
_DEDUPE = [
    # 注意：手部/道具的「保持不变」【故意不删】。它锁的是手里拿的东西不变形、
    # 不消失（萨克斯、话筒、书），是身份锁真正该管的事，删了道具就漂了。
    # 和 人群保留 逐字重复的那句
    (re.compile(r"[，,]?\s*画面中这些人物全程完整保留在画面里[^。]*?一致性"),
     "，保持同一人物、同一现场空间的一致性"),
    (re.compile(r"[，,]?\s*(这些人物|画面中的人)?全程完整保留在画面里[，,]"
                r"不消失[、,]不减少[、,]不变透明"), ""),
    # 和 人物走动强制 重复的那句（走不走由程序那块统一下指令）
    (re.compile(r"[，,]?\s*双脚只在原地小幅活动[:：][^，,。]*"
                r"([，,][^，,。]*挪动半步)?"), ""),
    (re.compile(r"[，,]?\s*双脚(只能|只)?在原地[^，,。]{0,10}活动"), ""),
]


def dedupe_body(text):
    """删掉 A 层里和程序自己要加的块重复的句子，只留身份特征。"""
    for r, rep in _DEDUPE:
        text = r.sub(rep, text)
    return re.sub(r"[，,]{2,}", "，", text).strip("，, ")


# ── 安静模式专用：把 A 层里所有「别动」的说法整句删掉 ──────────────────
# 安静模式会自己加一块「人物原地」，把「保持原位 + 位移全部由摄影机承担」一次说清。
# 如果 A 层里还留着"全程保持原地不动""画面保持静止"，就变成【只说别动、不说谁来动】
# 被重复三四遍 —— 这正是 0811 那批输出静止照片的成因（五比一的权重投票）。
# defreeze 把它们改写成"站在原地不走动"也一样是重复，所以安静模式下【直接删】，
# 只留「人物原地」那一句权威版本。车内除外：那边不加「人物原地」块，仍走 defreeze。
#
# 「双脚…」那两条必须删干净还有第二个理由：提示词里点名什么身体部位，摄影机就可能
# 去拍什么部位 —— 一个祷告特写里突然切到一双鞋就是这么来的。
# 全库 1476 条描述语里都带着「双脚不移动位置」或「双脚不离开原地」。
_STILLNESS = [
    re.compile(r"[，,、]?\s*(全程)?(保持)?原地不动"),
    re.compile(r"[，,、]?\s*(全程)?保持静止不动"),
    re.compile(r"[，,、]?\s*(画面|整个视频|整个画面)(全程)?保持静止"),
    re.compile(r"[，,、]?\s*(人物|她|他)?站在原地不走动"),
    re.compile(r"[，,、]?\s*(全程)?(保持)?一动不动"),
    # 0829：A 层坐姿档写的那句，由程序的「人物坐姿」块统一说一次
    re.compile(r"[，,、]?\s*(人物|她|他)?坐在原位不(起身|站起来|离开座位)"),
    # 0905：A 层站姿档写的那句，由程序的「人物站姿」块统一说一次。
    # 不删的话就又回到"只说别动、不说谁来动"被重复两遍的老坑。
    re.compile(r"[，,、]?\s*(人物|她|他)?站在原位不(离开原地|走动|移动位置|挪动|移动)"),
    re.compile(r"[，,、]?\s*(人物|她|他)?(留在原位|不离开原地|不移动位置)"),
]

# 「双脚…」单独一组：连【车内】也要删。车内不加「人物原地」块，所以不能套用
# 上面那组（会把"坐着别动"的意思一起删光），但点名身体部位的风险跟场景无关 ——
# 车里更糟，镜头一低就是踏板和鞋。车内的"别动"由 defreeze 改写的
# 「坐在座位上不挪动」承担，本来就不需要这句。
_FEET = [
    re.compile(r"[，,、]?\s*双脚[^，,。；;]{0,12}"
               r"(不移动位置|不离开原地|不挪动|不走动|不移动|只在原地[^，,。；;]{0,10})"),
    re.compile(r"[，,、]?\s*双脚[^，,。；;]{0,6}(不|没有)[^，,。；;]{0,6}(动|移)"),
]


def _tidy(text):
    return re.sub(r"[，,]{2,}", "，", text).strip("，, ")


def strip_feet(text):
    """只删「双脚…不动」这类点名脚的句子（所有场景都删，见上方注释）。"""
    for r in _FEET:
        text = r.sub("", text)
    return _tidy(text)


def strip_stillness(text):
    """删掉 A 层里所有"别动"的说法（安静模式的非车内场景用，见上方注释）。"""
    for r in _STILLNESS:
        text = r.sub("", text)
    return strip_feet(text)


# ── 坐姿识别：这个人是【坐着说话】还是站着 ──────────────────────────
# 0829 复盘：那批 10 张是演播室访谈图，画面里只有一到两个坐在扶手椅里的人。
# A 层的老提示词在「睁眼」这一档只给了两个选择 —— 大幅度来回走动，或者
# 「被众人围在中间、双脚不离开原地」。坐着的人显然走不了，于是十张全被写成
# 「被众人围在中间」，结尾标签跟着判成【围绕】，B 层再按围绕附加人群保留和
# 「贴着人群外缘绕行」的运镜 —— 画面里根本没有那圈人，整批白跑。
# A 层的提示词已经补上「坐着」这一档（描述语生成器 v6），这里是 B 层的对应：
# 坐着的人不能套「走动强制」，运镜也不该用跟拍版。
#
# 只认【主角自己坐着】的写法。特意不收「坐着一位…」「右侧坐着」这类句子 ——
# 那说的是旁边的人，主角可能正站着。
_SEATED = re.compile(
    r"以[^，,。；;]{0,8}坐姿"
    r"|端坐"
    r"|(?:她|他|正)坐在"
    r"|坐在[^，,。；;]{0,12}(?:椅|沙发|凳|座椅|座位|长椅|台阶)")


def is_seated(desc):
    """A 层描述语里，主角是不是坐着说话。用于挑运镜卡和挑「人物…」那一块。"""
    return bool(_SEATED.search(str(desc or "")))


# ---------------- 运镜卡挑选 ----------------

def _pool(shots, eye, scene, key):
    """取某一组的卡池，取不到就层层退回，绝不抛异常。"""
    if scene == "车内":
        # 车内不分睁眼闭眼，只有一个池；开不开放运镜由 车内允许运镜 决定。
        car = shots.get("车内", {})
        grp = ("全部" if shots.get("车内允许运镜", True) else "静止版备用")
        return list(car.get(grp, [])) or list(car.get("全部", [])) or [""]
    grp = shots.get(scene, {}).get(eye)
    if not grp:
        grp = shots.get("常规", {}).get(eye) or shots.get("常规", {}).get("闭眼", {})
    out = list(grp.get(key, []))
    if not out:                       # 该角色池为空 → 借用中段
        out = list(grp.get("中段", []))
    return out or [""]


def _offset(label, n):
    """按 v 号取一个稳定偏移，让不同编号即使段数相同、中段序列也不一样。
    同一个编号每次算出来都一样（确定性），重跑不会换镜头。"""
    if n <= 0:
        return 0
    m = re.search(r"(\d+)", str(label) or "0")
    return (int(m.group(1)) if m else 0) % n


# 运镜卡的书写格式固定是「中文说明。English camera term.」，所以能按最后一个
# 句号切成两半，支持 中英 / 英文 / 中文 三种发送模式，不用手改 60 张卡。
_CARD_SPLIT_RE = re.compile(r"。\s*([A-Za-z][^。]*)\s*$")


def split_card(card):
    """把一张卡拆成 (中文部分, 英文部分)。没有英文就返回 (原文, "")。"""
    m = _CARD_SPLIT_RE.search(card or "")
    if not m:
        return card or "", ""
    return card[:m.start() + 1], m.group(1).strip()


def render_card(card, shots=None):
    """按 flow_shots.json 的「运镜卡语言」渲染。

    默认 中英：中文在前、英文在后。
    —— 为什么默认不是纯英文 ——
    人物台词本身就是英文，提示词里的英文越多，模型越可能把拍摄指令当成
    台词念出来。中文半句是个明确的"这是指令不是台词"信号。
    真要试纯英文，把 json 里的「运镜卡语言」改成 "英文" 即可，
    同时说话指令里已经写死了"上文所有中英文都是拍摄指令，一个字都不要念"。
    """
    shots = shots or load_shots()
    lang = shots.get("运镜卡语言", "中英")
    if lang == "中英" or not card:
        return card
    zh, en = split_card(card)
    if lang == "英文":
        if not en:
            return card
        return f"【Camera】{en}" if not en.startswith("【") else en
    if lang == "中文":
        return zh or card
    return card


# ---------------- 安静模式（0820 大改版） ----------------

def quiet_on(eye, shots=None):
    """这个眼睛状态该不该走安静模式。

    两个条件：运镜模式 = "安静"，且 eye 在 安静模式适用 名单里。
    0820 三修起闭眼和睁眼都在名单里（睁眼走跟拍版，见 quiet_card）。
    """
    shots = shots or load_shots()
    if shots.get("运镜模式", "安静") != "安静":
        return False
    return eye in (shots.get("安静模式适用") or ["闭眼", "睁眼"])


def quiet_card(scene="常规", shots=None, eye="闭眼"):
    """安静模式的那张卡（一条视频从头到尾都用它）。

    闭眼取 安静运镜（人物保持原位，一条推镜）；
    睁眼取 安静运镜_睁眼（0905 起人物也不走动，卡里的跟拍句已删）。
    取不到 _睁眼 版自动退回闭眼版，卡库没升级也不会拼出空白。
    """
    shots = shots or load_shots()
    lib = ((shots.get("安静运镜_睁眼") if eye == "睁眼" else None)
           or shots.get("安静运镜") or {})
    return (lib.get(scene) or lib.get("常规")
            or (shots.get("安静运镜") or {}).get(scene) or "")


def quiet_card_for(label, scene="常规", shots=None, eye="闭眼"):
    """安静模式那张卡，按编号取。

    安静运镜多样 = false：和 quiet_card 完全一样，整批共用同一张（老行为）。
    = true（0829 起的默认）：按 v 号从卡池里挑一张 —— 同一条视频的所有段
    仍然共用这一张（段间景别必须一致，否则拼起来像硬切），变化只发生在
    【编号之间】。取模是确定性的：同一个编号重跑永远是同一张，补拍不换镜头。

    闭眼取 安静运镜_多样[场景]，睁眼取 安静运镜_多样_睁眼[场景]。
    两个池【不能互相顶替】：睁眼池每张都写着「跟拍」（人物真的在走），
    闭眼池写的是人物保持原位；拿错了就会出现「人在走、机位却按不动的人来拍」
    这种自相矛盾的指令。所以取不到对应的池就退回单卡 quiet_card，
    而不是去借另一只眼睛的池子。
    """
    shots = shots or load_shots()
    if shots.get("安静运镜多样"):
        key = "安静运镜_多样_睁眼" if eye == "睁眼" else "安静运镜_多样"
        pool = ((shots.get(key) or {}).get(scene) or [])
        pool = [c for c in pool if isinstance(c, str) and c]
        if pool:
            return pool[_offset(label, len(pool))]
    return quiet_card(scene, shots, eye)


def emotion_stage(seg_idx, n_segs):
    """第 seg_idx 段（1-based，共 n_segs 段）落在情绪弧的哪一段。

        1 段        → 独段（8 秒之内自己走完一条完整的弧）
        第 1 段      → 起
        最后一段     → 合
        中间前半     → 承      中间后半 → 转

    参数缺失时退回「转」：那是中段最常见的强度，猜错也只是少一点起伏，
    不会出现开头就声嘶力竭这种明显穿帮。
    """
    try:
        i, n = int(seg_idx), int(n_segs)
    except (TypeError, ValueError):
        return "转"
    if n <= 0 or i <= 0:
        return "转"
    if n == 1:
        return "独段"
    if i <= 1:
        return "起"
    if i >= n:
        return "合"
    return "承" if (i - 1) <= (n - 2) / 2 else "转"


def emotion_stage_long(i, n):
    """长文案情绪弧。n<=6 走老 emotion_stage；更长按 15/50/85 四分。

    不改 emotion_stage。Normal 短视频继续走 emotion_stage。
    """
    try:
        i, n = int(i), int(n)
    except (TypeError, ValueError):
        return "转"
    if n <= 6:
        return emotion_stage(i, n)
    if n <= 0 or i <= 0:
        return "转"
    r = i / float(n)
    if r <= 0.15:
        return "起"
    if r <= 0.50:
        return "承"
    if r <= 0.85:
        return "转"
    return "合"


def shot_cards(label, n_segs, eye="闭眼", scene="常规", shots=None,
               seated=False):
    """给一条视频的 n 段各配一张运镜卡。

    安静模式（默认，见模块顶部）：n 段【全部用同一张卡】。段与段的景别必须一致，
    否则拼起来就是硬切 —— 每段都是拿同一张起始图单独生成的，这一点没法靠剪辑补救。

    多镜头模式（老行为）：首段【开场·推近建立】→ 中段【环绕/特写/横移轮换】→
    末段【拉开收束】。中段按 (v号偏移 + 段序) 轮换，相邻两段必然不同。
    """
    shots = shots or load_shots()
    n = max(int(n_segs or 0), 0)
    if n == 0:
        return []
    if eye not in EYES:
        eye = "闭眼"
    if scene not in SCENES:
        scene = "常规"

    # 空镜不在「安静模式适用」里是有意为之：不走安静模式那张「推到面部特写」
    # 的卡，自动走多镜头，改用空镜专用池。硬约束：绝不能推成某一个人的面部特写。
    if is_group(eye, scene):
        # 空镜卡池按场景分池：群像取 空镜卡池_群像（以人群为中心的那 8 张），
        # 取不到才退回通用空镜池。两者不能共用 —— 通用池是给教堂听众席写的
        # （侧通道、后排、舞台方向），对着草地上围成一圈的人根本用不上。
        raw = (shots.get(f"空镜卡池_{scene}") or shots.get("空镜卡池") or [])
        pool = [c for c in raw if isinstance(c, str) and c] if isinstance(raw, list) else []
        if not pool:
            pool = [""]
        base = _offset(label, len(pool))
        if scene == "群像":
            # 一条视频的所有段共用同一张卡 —— 和安静模式同一个理由：
            # 每段都是拿同一张起始图单独生成的，段与段景别不一致，拼起来就是硬切。
            # 变化留在【编号之间】：不同 v 号按 _offset 取到不同的卡，一批不会雷同。
            return [pool[base]] * n
        return [pool[(base + k) % len(pool)] for k in range(n)]

    if quiet_on(eye, shots):
        # 坐着说话的人（演播室访谈）按闭眼那一池取卡：睁眼池每张都写着
        # 「摄影机随人物同步跟随」，人根本不起身，跟拍就成了空指令，
        # 模型多半会自己把人拽起来走两步来兑现它。见 is_seated 上方复盘。
        card_eye = "闭眼" if (seated and eye == "睁眼") else eye
        return [quiet_card_for(label, scene, shots, card_eye)] * n

    # 车内：车厢空间小，没有开场/中段/收束的叙事弧可言，所以不分角色，
    # 直接在一个池子里按 (v号偏移 + 段序) 轮换，保证相邻两段机位不同。
    # 车内允许运镜 = false 时池子里只有一张静止卡，轮换结果自然就是全程同一句。
    if scene == "车内":
        pool = _pool(shots, eye, scene, "全部")
        base = _offset(label, len(pool))
        return [pool[(base + k) % len(pool)] for k in range(n)]

    if n == 1:
        pool = _pool(shots, eye, scene, "独段")
        return [pool[_offset(label, len(pool))]]

    open_pool = _pool(shots, eye, scene, "开场")
    close_pool = _pool(shots, eye, scene, "收束")
    mid_pool = _pool(shots, eye, scene, "中段")

    cards = [open_pool[_offset(label, len(open_pool))]]
    base = _offset(label, len(mid_pool))
    for k in range(n - 2):                       # 中段
        cards.append(mid_pool[(base + k) % len(mid_pool)])
    cards.append(close_pool[_offset(label, len(close_pool))])
    return cards


# ---------------- 完整提示词组装 ----------------

def _emotion_block(tail, eye, seg_idx, n_segs):
    """安静模式的情绪块 = 基调（怎么演，全片不变）+ 推进（演多满，逐段变）+ 禁令。

    取不到新键就整块退回老的 情绪_<eye>：卡库没升级也不会拼出空白。
    """
    base = tail.get(f"情绪基调_{eye}") or ""
    # 情绪推进也分闭眼/睁眼两套措辞：闭眼说「整篇祷告」，睁眼说「整段讲话」。
    # 取不到 _睁眼 那套就退回通用（闭眼）那套。
    stage = emotion_stage(seg_idx, n_segs)
    pool = ((tail.get(f"情绪推进_{eye}") if eye != "闭眼" else None)
            or tail.get("情绪推进") or {})
    ramp = pool.get(stage) or (tail.get("情绪推进") or {}).get(stage) or ""
    if not base and not ramp:
        base = tail.get(f"情绪_{eye}") or tail.get("情绪", "")
    return " ".join(p.strip() for p in (base, ramp, tail.get("禁令", "")) if p)


# ---------------- 分段文案里的「停顿 N 秒」 ----------------
# 有些文案本身就写着 Pause for seven seconds and write "Amen."
# / Stay for 5 seconds and comment "Amen." 这类句子。
# 它长得就像一条拍摄指令，模型会照做 —— 真的停住不说话，后面的词一并丢掉。
# 唯一管用的解法是在结语里【把这句原话点名摘出来】，明说它也要念。
#
# 但绝不能写死在结语里：绝大多数文案根本没有这句话，硬加一句「包括…也要读」
# 等于凭空给模型塞了一段台词，它会当真念出来。所以只能【按段扫描、命中才拼】。
#
# 摘的是【原话】而不是改写：结语里的引文必须和下面的台词一字不差，
# 差一个字模型就会以为是两句不同的话，反而多念一遍。
_PAUSE_NUM = (r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|"
              r"eleven|twelve|fifteen|twenty|a few|several)")
_PAUSE_DUR = r"%s[\s\-]*(?:seconds?|secs?|minutes?|mins?)\b" % _PAUSE_NUM
# stay 必须单列：文案大量用 "Stay for 5 seconds and comment Amen"，
# 只写 stay silent 会漏掉，模型把 Stay for N seconds 当停顿指令，整句不念。
# 仍要求后面跟 N seconds/minutes，单独一句 Stay blessed 不会误中。
_PAUSE_VERB = (r"(?:pause|pausing|paused|wait|waiting|hold|holding|"
               r"stay(?:ing)?|stay\s+silent|be\s+silent|remain\s+silent|"
               r"silence|stop)")
_PAUSE_RES = [
    # 「停顿→几秒」和「几秒→停顿」两种语序都收，但限制在同一句之内
    # （中间不许跨过句号或换行），免得把隔壁句子的秒数算到这句头上。
    re.compile(r"\b%s\b[^.!?\n]{0,40}?%s" % (_PAUSE_VERB, _PAUSE_DUR), re.I),
    re.compile(r"%s[^.!?\n]{0,40}?\b%s\b" % (_PAUSE_DUR, _PAUSE_VERB), re.I),
    re.compile(r"(?:停顿|停留|等待|暂停|静默|沉默)[^。！？\n]{0,12}?"
               r"(?:\d+|[一二两三四五六七八九十几]+)\s*(?:秒|分钟)"),
    # "Pause for a moment" —— 实际文案里和"pause for seven seconds"一样常见，
    # 模型照样会真的停住。但必须卡死【动词 + for】这个形状：
    # 文案里还有大量 "If you have a moment, share this message"，那是"有空的话"，
    # 不是让人停下来，误配进去就会凭空多出一句台词。
    re.compile(r"\b%s\b[^.!?\n]{0,20}?\bfor\s+(?:a|one|a few|several|\d+)"
               r"\s+moments?\b" % _PAUSE_VERB, re.I),
]
# 断句：句末标点后面还可能跟着收尾引号（… write “Amen.” ← 句号在引号里面）。
# 用【捕获组】把这串引号抓下来再原样贴回前一句 —— 如果直接把引号写进分隔符，
# re.split 会把它一起吃掉，摘出来的引文就少一个右引号，和下面的台词对不上，
# 模型会当成两句不同的话，多念一遍，正好是这套机制要防的事。
_SENT_SPLIT = re.compile(r'(?<=[.!?。！？])(["”\'’)\]】」』]*)\s+')


def _sentences(line):
    """按句切开，收尾引号跟着它自己的那一句走。"""
    parts = _SENT_SPLIT.split(line)      # [句1, 尾引号1, 句2, 尾引号2, …, 句n]
    out = []
    for i in range(0, len(parts), 2):
        s = (parts[i] + (parts[i + 1] if i + 1 < len(parts) else "")).strip()
        if s:
            out.append(s)
    return out


def find_pause_line(seg):
    """从这一段台词里找出【要求停顿几秒】的那句话，一字不改地返回；没有则返回空串。"""
    for line in (seg or "").splitlines():
        for s in _sentences(line.strip()):
            if any(r.search(s) for r in _PAUSE_RES):
                return s
    return ""


def apply_pause_emphasis(say, seg, shots=None):
    """按这一段的台词填掉结语里的 {停顿强调} 占位符（没命中就填空串）。"""
    if "{停顿强调}" not in (say or ""):
        return say
    shots = shots or load_shots()
    tpl = (shots.get("固定结语", {}) or {}).get("停顿强调", "")
    quote = find_pause_line(seg)
    ins = tpl.replace("{原话}", quote) if (tpl and quote) else ""
    return say.replace("{停顿强调}", ins)


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
MUTE_TIP_DEFAULT = '画面中的人物全程不说话、没有对白，嘴唇保持闭合；可以有自然的表情、眨眼和细微的动作。\nNo dialogue. The person does not speak; lips stay closed. Natural expressions and subtle movements only.'


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



def build_prompt(desc, card, eye="闭眼", scene="常规", seg="", shots=None,
                 seg_idx=None, n_segs=None, voice_lock="", switches=None, no_copy=False):
    """拼出最终提交给 Flow / Grok 的提示词。

        A层身份锁（已去掉机读标签）
        B层运镜卡
        镜头禁令（仅安静模式，一句话钉死「不切镜不转场不绕圈」）
        车辆静止锁（仅「车内」场景，钉死车辆停稳熄火）
        人群保留（仅「围绕」场景；空镜强制加）
        画质锁
        情绪 + 禁令
        音色锁（取不到 voice_lock 时为空串，Normal 拼出来一字不变）
        固定结语（闭眼/睁眼两版；空镜改画外音指令）
        英文文案

    seg_idx / n_segs 只在安静模式下用得上：拿它们算出这一段处在情绪弧的哪个位置
    （起承转合），逐段递进。不传就退回中段强度，行为和老版本一致。

    用换行分隔而不是挤成一行：fill_editor 走的是 keyboard.insert_text，
    不会把换行当成回车提交；verify_editor 的 norm_text 又会把所有空白
    折叠成单空格再比对，所以换行对"逐字一致"校验完全无害。
    """
    shots = shots or load_shots()
    sw = normalize_switches(switches)
    tail = shots.get("固定结语", {})
    is_car = scene == "车内"
    is_ring = scene == "围绕"
    # 群像和空镜共用同一条画外音通道：画面里没有人对镜头说话，文案由画外解说念，
    # 不加比例锁（没有要锁的那张脸），不加人物原地/走动强制（没有那个"人物"）。
    is_group_scene = (scene == "群像")
    is_broll = (eye == "空镜") or is_group_scene
    # 空镜不在「安静模式适用」里是有意为之：不走「推到面部特写」的安静卡。
    quiet = False if is_broll else quiet_on(eye, shots)
    # 坐姿要在删句子之前判：strip_stillness 会把 A 层那句「坐在原位不起身」
    # 删掉（程序下面自己加一句权威版），删完再判就判不出来了。
    seated = is_seated(desc)

    # strip_legacy_tail 而不是 strip_tag：老表格里的描述语自带一份旧结语，
    # 不切掉就会和下面程序附加的新结语打架（详见该函数注释）。
    # 车内开放运镜后也要解冻（用车内那张替换表），否则 A 层的"画面保持静止"
    # 会和车内运镜卡打架；关掉运镜时保持老行为，原样不改。
    # 规格 85：描述语整理关 → 描述语一字不改进身份锁
    if sw.get("描述语整理", True):
        body = strip_legacy_tail(desc)
        if quiet:
            body = strip_stillness(body) if not is_car else strip_feet(body)
        if not is_car or shots.get("车内允许运镜", True):
            body = defreeze(body, scene)
        if shots.get("身份锁去重", True):
            body = dedupe_body(body)
    else:
        body = desc

    blocks = {
        "身份锁": body,
        "运镜卡": render_card(card, shots),
        # —— 0811 撤销：这里曾经有一块「环绕强制」，在运镜卡之后另起一句
        # 「再叠加一层 360 度环绕」。实拍验证是错的，别再加回来：
        # 一段之内出现了【运镜卡后退 + 环绕绕圈 + 运动强制推拉 + 走动强制横跟】
        # 四条互不相容的运动指令，模型折中的结果就是干脆不动，还用假转场糊弄。
        # 正确做法是把大幅度运动【写进运镜卡本身】，一段永远只有一条运动指令。
        # 车辆静止锁：只有车内加。把「摄影机在动」和「车在动」彻底分开 ——
        # 这是车内开放运镜之后唯一真正的风险：模型看到镜头绕行，很容易顺手
        # 让车也跑起来，出现【闭着眼祷告 + 车在路上狂飙】的穿帮。
        # 它在 组装顺序_车内 里紧挨着运镜卡排第二位，两句话贴在一起最不容易
        # 被模型只读到一半。运镜开不开放它都加 —— 车停着和镜头动不动无关。
        "车辆静止锁": ((((shots.get("车辆静止锁_安静") if quiet else None)
                        or shots.get("车辆静止锁")) or "") if is_car else ""),
        # 镜头禁令：只有安静模式加，而且【刻意只有一句】。
        # 老卡库 v2 的教训写在 json 的 _说明 里：230 字符的否定墙不但没挡住转场，
        # 反而因为"越说不要淡化越容易出淡化" + 稀释整段权重，把事情变得更糟。
        # 所以这里只钉最要命的三件事（切镜 / 转场 / 绕圈），其余靠正面描述解决。
        "镜头禁令": (shots.get("镜头禁令") or "") if quiet else "",
        # 通用版那句"景别变化只靠机位推拉走位完成"在车厢里做不到，
        # 照抄会和车辆静止锁打架，所以车内用只保留"一镜到底"的精简版。
        # 安静模式另有一版（去掉了"持续带出新空间"这类催大幅度运动的措辞），
        # 但【默认不启用】—— 组装顺序_安静 里没有这一块，因为"不用变焦 + 纵深分离"
        # 已经写进安静运镜卡本身了。一段只留一条运动指令，是 0811 复盘的铁律。
        # 想拆回独立一块做 A/B：把 "运动强制" 加回 组装顺序_安静 即可。
        "运动强制": ((
            (shots.get("运动强制_车内_安静") or shots.get("运动强制_车内"))
            if is_car else
            (shots.get("运动强制_安静") or shots.get("运动强制"))
        ) if quiet else (
            shots.get("运动强制_车内") if is_car else shots.get("运动强制")
        )) or "",
        # 人物原地：安静模式 + 闭眼 专用。睁眼走下面的「人物站姿」/「人物坐姿」，车内两块都不加
        # （人本来就坐着，由 车辆静止锁 管）。两块在 组装顺序_安静 里都列着，
        # 但同一次只会有一块有内容。
        # 措辞上绝不能写成"画面静止"——见 _DEFREEZE 上方的坑。
        "人物原地": ("" if (is_broll or is_car or not quiet or eye != "闭眼")
                     else (shots.get("人物原地_安静") or "")),
        # 人物站姿：只有【睁眼 + 站着 + 非车内】才加。
        # 闭眼是在祷告走「人物原地」；坐着走下面的「人物坐姿」；车内由 车辆静止锁 管。
        #
        # 0905：这一块以前叫「人物走动强制」，内容是硬要人物迈开脚步走出位移。
        # 现在人物一律不走动 —— 走动的片子拼起来动作乱、模型还会为了腾出走道
        # 把周围的人清场（0808 那条菲律宾牧师视频：起始帧一圈人手拉手祷告，
        # 第 1 秒人全部淡掉，只剩他一个人站在空草地上）。所以改成硬性的
        # 「站在原位不离开原地」。为什么要明写而不是什么都不写：许可式措辞
        # （"可以走动"）模型多半自己发挥，正反两边都得一致地钉住。
        #
        # 「围绕」仍用带「被围在中间」的那一版（措辞更贴合那种图）。
        # 安静模式另有一版：纯中文、不点名"双脚"（点名部位镜头就会去拍那里）。
        # 措辞上绝不能写成"画面静止"——见 _DEFREEZE 上方的坑。
        "人物站姿": ("" if (is_broll or is_car or eye != "睁眼"
                            or seated) else (
            ((shots.get("人物站姿_围绕_安静") if quiet else None)
             or shots.get("人物站姿_围绕")
             or shots.get("人物站姿") or "") if is_ring
            else ((shots.get("人物站姿_安静") if quiet else None)
                  or shots.get("人物站姿") or ""))),
        # 人物坐姿：睁眼 + 坐着（演播室访谈、沙发对谈）。坐着的必须一直坐着 ——
        # 这一块把"不起身、不离开座位"钉死。多镜头模式取不到 _安静 版就退回它，
        # 总之只要判出坐姿就一定有一句，不会漏成空白（0829 那批的病根）。
        "人物坐姿": ("" if (is_broll or is_car
                            or eye != "睁眼" or not seated)
                     else (shots.get("人物坐姿_安静")
                           or shots.get("人物坐姿") or "")),
        # 人群保留：起始帧里除主体外的人，模型默认当可有可无的背景，
        # 开场淡入时顺手就淡掉了。这一块把他们钉死在画面里。
        # 目前只有 组装顺序_围绕 引用它；想给舞台也加，新建 组装顺序_舞台 即可。
        # 下面几块老版本自带一份英文并列。安静模式要求整段【不夹英文】
        # （见 json 的 _纯中文块说明），所以优先取 _安静 版；取不到退回老块，
        # 卡库没升级也不会拼出空白。多镜头模式一律走老块，行为不变。
        # 群像另有一版，措辞更硬（人数/站位/姿势/朝向逐项点名，并明写"不淡入淡出"）：
        # 围成一圈的图只要淡掉一两个人，整个阵型就废了，比单主角场景严重得多。
        "人群保留": ((shots.get("人群保留_群像") if is_group_scene else None)
                     or (shots.get("人群保留_安静") if quiet else None)
                     or shots.get("人群保留") or ""),
        # 群像锁：群像专用，钉死"没人开口 / 没人挪位 / 镜头不落到脸上"三件事。
        # 在 组装顺序_空镜_群像 里紧挨着运镜卡排第二位（和车内的车辆静止锁同理）。
        "群像锁": (shots.get("群像锁") or "") if is_group_scene else "",
        "比例锁": ("" if is_broll else (
            (shots.get("比例锁_安静") if quiet else None)
            or shots.get("比例锁") or "")),
        # 群像另有一版：通用画质锁里那句「强烈的面部微表情」是为主角特写写的，
        # 放进群像提示词里正好和「任何时候都不落成某一个人的面部特写」当面打架 ——
        # 一边说不许拍脸、一边说脸上表情要强烈，模型折中的结果就是推过去拍脸。
        "画质锁": (shots.get("画质锁_群像") if is_group_scene
                   else (shots.get("画质锁_车内") if is_car
                         else shots.get("画质锁"))) or "",
        # 情绪分闭眼/睁眼两版：闭眼祷告是向内的恳切（眉头、气息、声音颤抖），
        # 睁眼演讲是向外的感染力（目光、手势、声音扬起），表现方式完全不同。
        # 取不到分版就退回通用版。
        #
        # 安静模式再拆一层：【怎么演】（情绪基调，全片不变）+【演多满】（情绪推进，
        # 逐段递进）。老写法把两者焊死在一句"强烈饱满"里，每段强度完全一样，
        # N 段拼起来是一条平的直线；运镜安静下来之后，起伏就只能靠这里给了。
        # 空镜/群像的情绪块挪进了 json（情绪_空镜 / 情绪_空镜_群像）。
        # 以前硬写在这里的那句「有人微微点头，有人举手」是给教堂听众席写的，
        # 对着一圈低头闭眼祷告的人是错的 —— 一说"有人举手"，模型真会让人抬手，
        # 队形当场散掉。取不到 json 就退回老那句，卡库没升级也不会拼出空白。
        "情绪": (
            (shots.get(f"情绪_空镜_{scene}") or shots.get("情绪_空镜")
             or "人群氛围：听众专注聆听，有人微微点头，有人举手。不指定任何个人的表情特写。禁止背景音乐、配乐和环境音，只有画外解说声。")
            if is_broll else
            (_emotion_block(tail, eye, seg_idx, n_segs) if quiet
             else ((tail.get(f"情绪_{eye}") or tail.get("情绪", ""))
                   + tail.get("禁令", "")).strip())
        ),
        # 安静版把「上文所有中文和英文」改成「上文所有内容」——上文已经没有英文了，
        # 再点名"英文"会和紧接着那句"以下英文是要念的"撞车。详见 json 里的说明。
        # 空镜：说话指令整块换成画外音指令（钉死没人对着镜头说话 + 画外解说）。
        # {停顿强调} 占位符按这一段的台词现填：文案里真有「停顿 N 秒」才
        # 把那句原话摘进来点名"这句也要念"，没有就填空串（详见 json 说明）。
        # 群像的画外音指令是另写的：通用版里留着一句「嘴型对齐」和结尾的
        # 「这个人物说话的内容」—— 都是从主角版继承来的，而它上一句刚说完
        # 「所有人的嘴保持闭合、不要出现说话的口型」。两句挨在一起，模型只能挑一句听，
        # 挑错就是全场对口型的大穿帮。群像版把这两处彻底拿掉。
        "说话指令": apply_pause_emphasis(
            ((shots.get("画外音指令_群像") if is_group_scene else None)
             or shots.get("画外音指令") or "") if is_broll else
            ((tail.get(f"{eye}_安静") if quiet else None)
             or tail.get(eye) or tail.get("闭眼", "")),
            seg, shots),
        # 音色锁：voice_lock 取不到时为空串，不进 parts（if p 过滤），Normal 一字不变。
        # 群像没有露脸的说话人，解说是谁的声音只能靠这一句锁住，
        # 并再点一次「不属于画面里任何一个人」，防止模型顺手给某张脸配上这个声音。
        "音色锁": (
            ((shots.get("音色锁_群像") if is_group_scene else None)
             or shots.get("音色锁") or "{voice_lock}").replace(
                "{voice_lock}", (voice_lock or "").strip())
            if (voice_lock or "").strip() else ""
        ),
    }
    # 段落顺序可在 flow_shots.json 里调，按 模式 + 场景 逐级退回：
    #   空镜 → 组装顺序_空镜
    #   组装顺序_安静_<场景> → 组装顺序_安静 → 组装顺序_<场景> → 组装顺序
    # 想给某个场景单独排版，直接在 json 里加一条就行，不用改代码。
    # 默认把【运镜卡】放最前面：Veo 对靠前的 token 权重更高，而人物身份
    # 主要由起始帧图片锁定，不依赖文字位置。想 A/B 测试改 json 即可。
    if is_broll:
        order = (shots.get(f"组装顺序_空镜_{scene}")
                 or shots.get("组装顺序_空镜")
                 or ["运镜卡", "身份锁", "人群保留", "画质锁",
                     "情绪", "音色锁", "说话指令"])
    else:
        order = ((shots.get(f"组装顺序_安静_{scene}") or
                  shots.get("组装顺序_安静")) if quiet else None)
        order = (order
                 or shots.get(f"组装顺序_{scene}")
                 or shots.get("组装顺序")
                 or ["运镜卡", "镜头禁令", "运动强制", "身份锁", "比例锁",
                     "画质锁", "情绪", "说话指令"])
    # 规格 85：跳过开关为 False 的积木；身份锁永远保留
    # 规格 93：不说话的任务（no_copy）永远不带「说话指令」和「音色锁」——
    # 那两块是冲着台词去的：一句「人物只需要准确说出下面这一句英文…」后面却没有台词，
    # 紧跟着又是「全程不说话」，自相矛盾，模型多半照样张嘴。开关全开也不许带。
    parts = []
    for k in order:
        if k != "身份锁" and not sw.get(k, True):
            continue
        if no_copy and k in ("说话指令", "音色锁"):
            continue
        parts.append(blocks.get(k, ""))
    head = "\n".join(p for p in parts if p)
    if seg:
        return f"{head}\n{seg}" if head else seg
    # 规格 86：仅当调用方显式传 no_copy=True 时追加静音提示。
    # 不用 seg 是否为空判断——有文案任务里空镜等段 text 也可能为空，必须与现在逐字节一致。
    if no_copy and sw.get("静音提示", True):
        tip = (shots.get("静音提示") or shots.get("silent") or MUTE_TIP_DEFAULT).strip()
        if tip:
            return f"{head}\n{tip}" if head else tip
    return head


# ---------------- vXX_shot.txt 读写 ----------------

# 自动生成的文件带指纹标记；手工改过的文件没有（或指纹对不上）。
# —— 为什么需要这个 ——
# 第一版把"文件存在且行数对得上"直接当成【手工覆盖】。可这个文件本来就是
# 程序自己写出来的，于是跑过一轮之后，卡库再怎么更新都读不进来：
# 0807 那批 230 个编号全部卡在第一版那套保守运镜卡上，改了 flow_shots.json
# 也毫无效果。现在自动生成的文件记下卡库指纹，卡库一变就重算；
# 只有【指纹对不上又不是当前自动结果】的，才认定是人改过的，予以保留。
_AUTO_MARK = "# 自动生成"
# 第一版自动生成的文件头（那时还没有指纹标记）。识别出来一律当作
# "自动生成的旧文件"重算，否则 0807 那 230 个会被误判成手工文件永久保留。
_LEGACY_AUTO_MARK = "改这里可覆盖自动生成的镜头"
_HEADER = ("{mark} 指纹{sig} · 一行一段，共 {n} 段\n"
           "# 想自己设计镜头：直接改下面的正文即可，程序检测到内容变了就不再覆盖。\n"
           "# 想恢复自动：删掉本文件，下次运行会重新生成。")


def _lib_sig(shots=None):
    """卡库指纹：flow_shots.json 的【有效内容】一变就变。

    下划线开头的键是纯文档（_说明 / _组装顺序 / _场景判定 …），程序一律不读，
    所以不计入指纹 —— 否则往说明里补一句话就会把全批 _shot.txt 判成"过期"
    全部重写一遍。改动本身无害（重算结果与原文一致），但白改 230 个文件的
    修改时间，也让"指纹变了"这个信号失去意义。
    """
    shots = shots or load_shots()
    eff = {k: v for k, v in shots.items() if not str(k).startswith("_")}
    raw = json.dumps(eff, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]


def shot_file(folder, label):
    return os.path.join(folder, f"{label}_shot.txt")


def read_shot_file(folder, label, n_segs, auto_cards=None, shots=None):
    """读已有的运镜卡文件，返回 (cards, 是否手工) 或 (None, False)。

    判定顺序：
      · 行数和分段数对不上 → 过期，重算（分段改了运镜卡必须跟着改）
      · 带自动标记且指纹是当前卡库 → 就是当前自动结果，用它
      · 带自动标记但指纹是旧的 → 卡库更新过，重算
      · 没有自动标记 → 人手写的，保留
    """
    p = shot_file(folder, label)
    if not os.path.exists(p):
        return None, False
    try:
        with open(p, "r", encoding="utf-8") as f:
            raw = f.read()
    except Exception:
        return None, False
    lines = [ln.strip() for ln in raw.splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    if len(lines) != n_segs:
        return None, False                      # 段数变了 → 过期
    head = "\n".join(ln for ln in raw.splitlines()
                     if ln.lstrip().startswith("#"))
    if _LEGACY_AUTO_MARK in head:
        return None, False                      # 第一版的自动文件 → 重算
    if _AUTO_MARK in head:
        if f"指纹{_lib_sig(shots)}" in head:
            return lines, False                 # 当前卡库的自动结果
        return None, False                      # 卡库变了 → 重算
    return lines, True                          # 没有标记 = 手工写的


def write_shot_file(folder, label, cards, shots=None):
    p = shot_file(folder, label)
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(_HEADER.format(mark=_AUTO_MARK, sig=_lib_sig(shots),
                                   n=len(cards)) + "\n")
            f.write("\n".join(cards) + "\n")
    except Exception:
        pass
    return p


def cards_for(folder, label, n_segs, desc, shots=None):
    """对外主入口：拿到某编号的运镜卡。
    手工改过的文件优先；否则按当前卡库自动生成并落盘。
    返回 (cards, eye, scene, 是否手工覆盖)。"""
    shots = shots or load_shots()
    eye, scene, _ = parse_tag(desc)
    seated = is_seated(desc)
    existing, manual = read_shot_file(folder, label, n_segs, shots=shots)
    if existing and manual:
        return existing, eye, scene, True       # 人改过的，尊重它
    if existing:
        return existing, eye, scene, False      # 已是当前卡库的自动结果
    cards = shot_cards(label, n_segs, eye, scene, shots, seated=seated)
    write_shot_file(folder, label, cards, shots)
    return cards, eye, scene, False


# ---------------- 自测 ----------------

if __name__ == "__main__":
    s = load_shots()
    demo = [
        ("v01", 3, "这是一位美国的(非裔)中年50岁左右女性基督徒，她紧闭双眼祷告。【闭眼|常规】"),
        ("v02", 5, "这是一位非洲的年轻30岁左右男性基督徒，他睁着眼在台上演讲。【睁眼|舞台】"),
        ("v03", 4, "这是一位美国的(白人)中年女性基督徒，她坐在车内说话。【闭眼|车内】"),
        ("v04", 2, "老格式没有标签，她全程紧闭双眼祷告"),
        ("v05", 1, "老格式没有标签，他睁着眼在礼堂讲台上演讲"),
        ("v06", 4, "这是一位菲律宾的中年男性牧师，他睁着眼带领众人祷告，"
                   "被村民围成一圈手拉手。【睁眼|围绕】"),
        ("v07", 3, "老格式没有标签，他睁着眼在台上带领大家祷告，"
                   "众人围成一圈手拉手围绕着他"),
    ]
    print(f"运镜模式 = {s.get('运镜模式', '安静')}"
          f"   安静模式适用 = {s.get('安静模式适用')}")
    for label, n, desc in demo:
        eye, scene, body = parse_tag(desc)
        cards = shot_cards(label, n, eye, scene, s)
        q = "安静" if quiet_on(eye, s) else "多镜头"
        print(f"\n=== {label}  {n}段  [{eye}|{scene}]  {q}  正文尾: …{body[-14:]}")
        for i, c in enumerate(cards, 1):
            print(f"  {i}. [{emotion_stage(i, n)}] {c[:60]}…")
    print("\n--- 完整提示词样例（v01 第2段 / 共3段）---")
    eye, scene, _ = parse_tag(demo[0][2])
    cards = shot_cards("v01", 3, eye, scene, s)
    print(build_prompt(demo[0][2], cards[1], eye, scene,
                       "Stay for seven seconds and type Amen.", s,
                       seg_idx=2, n_segs=3))
