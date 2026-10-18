# -*- coding: utf-8 -*-
"""锚点 + 接片：有向图调度、自动换锚点、重生联动、双帧素材。

接片不是两阶段串行。主片没有前置，直接进队列；接片 s(n) 只等
s(n-1) 已收且 bridge_ready、以及 s(n+1) 的图已定。主片一收就抽尾帧、
算跨度、必要时自动换下一锚点的图，再把接片推进同一条队列。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .. import config
from ..errors import OranteError
from ..models import Manifest
from . import tails, track as trackmod

MODE_FIRST = "first_frame"
MODE_ANCHOR = "anchor_bridge"
MODE_CHAIN = "tail_chain"
MODES = (MODE_FIRST, MODE_ANCHOR, MODE_CHAIN)
# 任务表 gen_mode 列空着 = 跟随引擎（Grok 逐段首帧，Flow 锚点+接片）
GEN_MODE_FOLLOW = ""
# 尾帧接龙：失败这么多次仍没收片，下一段照常放行（宁可缺一段也不能整条链停）
CHAIN_GIVE_UP = 3
CHAIN_REUSE_DIST = 18
CHAIN_UPSCALE_EVERY = 5
CHAIN_DESC_POLICIES = ("auto", "always", "never")

MAX_SWAP_CANDIDATES = 4
GAP_ENQUEUE = 3          # ≤3 放行（3 标黄，检查台能看见）
GAP_SWAP = 4             # ≥4 先自动换锚点
MISMATCH_STOP = 2        # 接片首帧对不上这么多次才停（检查台可重试）
TIMEOUT_STOP = 3         # 连续超时这么多次才停
THUMB_STOP = 5           # 连续拿到不合格地址这么多次才停

BRIDGE_TMP_REL = "clips/_tmp"

# Grok 接片怎么传图。探针 40：双图时首帧从没低于 41（单图起片是 0~7），
# 所以默认 single_tail——只传上一段尾帧。dual 留给 Grok 修好首帧照抄再切回。
GROK_BRIDGE_MODE = "single_tail"   # "single_tail" | "dual"

# 下面两个只在 GROK_BRIDGE_MODE == "dual" 时有意义。
# 探针 40：start_then_end = 写法④（先起后终）；end_then_start = 写法⑤/⑥。
GROK_BRIDGE_UPLOAD = "start_then_end"
# True = 提示词也改成「后传的是起始、先传的是结束」（写法⑥）。⑤ 只反序不改词。
GROK_BRIDGE_FLIP_PROMPT = False
# 探针 B：Flow 双图对上的距离在 16~30，没对上的 40+。≤ 这一档算对上。
BRIDGE_MATCH_DIST = 30

BRIDGE_PROMPT_DEFAULT = (
    "第一张上传的图片「01_首帧_start.jpg」是视频的起始画面：{start}。\n"
    "第二张上传的图片「02_尾帧_end.jpg」是视频的结束画面：{end}。\n"
    "画面从第一张的状态自然连续地过渡到第二张的状态，中间不切镜、不跳剪。"
)

# 写法⑥：上传已是终→起，提示词跟着说「后传的那张是起始、先传的那张是结束」。
BRIDGE_PROMPT_FLIPPED = (
    "后上传的图片「01_首帧_start.jpg」是视频的起始画面：{start}。\n"
    "先上传的图片「02_尾帧_end.jpg」是视频的结束画面：{end}。\n"
    "画面从后上传那张的状态自然连续地过渡到先上传那张的状态，中间不切镜、不跳剪。"
)

# Grok 单图接片：不要提结束画面、不要提下一张图。{start} 是上一段尾帧姿态。
BRIDGE_PROMPT_SINGLE = (
    "画面从上传图片的这一刻自然延续：{start}。\n"
    "镜头继续当前的运动，人物保持同一姿态和位置，讲话不中断，不切镜、不跳剪。"
)

# 尾帧接龙：每一段都是单图 i2v，从上传图这一刻接着画。不要提第二张、不要提结束画面。
CHAIN_PROMPT_DEFAULT = (
    "画面从上传图片的这一刻自然延续：{start}。\n"
    "镜头保持当前的运动趋势，人物保持同一身份、同一位置，讲话不中断，不切镜、不跳剪、不淡入淡出。"
)


def mode_of(m) -> str:
    if m is None:
        return MODE_FIRST
    if isinstance(m, dict):
        v = m.get("mode")
    else:
        v = getattr(m, "mode", None)
    return v if v in MODES else MODE_FIRST


def is_anchor_mode(m) -> bool:
    return mode_of(m) == MODE_ANCHOR


def is_chain_mode(m) -> bool:
    return mode_of(m) == MODE_CHAIN


def default_mode_for_engine(engine: str) -> str:
    """探针 40：Grok 没有真正的首尾帧入口，双图+提示词三种写法下
    首帧↔起始图距离从没低于 41（单图起片是 0~7），锚点+接片对它只剩
    「延长一段」的半吊子价值。Flow 的首尾帧是原生功能，36 号探针两头都对得上。
    所以跟随引擎时：Grok → 逐段首帧，Flow → 锚点+接片。"""
    eng = (engine or "").strip().lower()
    if eng == "flow":
        return MODE_ANCHOR
    return MODE_FIRST


def parse_gen_mode(raw) -> str:
    """任务表 gen_mode 列 → 空（跟随引擎）或 first_frame / anchor_bridge / tail_chain。"""
    v = (raw or "").strip()
    if v in ("", "follow", "跟随引擎"):
        return GEN_MODE_FOLLOW
    if v in (MODE_FIRST, "逐段首帧"):
        return MODE_FIRST
    if v in (MODE_ANCHOR, "锚点+接片", "锚点 + 接片"):
        return MODE_ANCHOR
    if v in (MODE_CHAIN, "尾帧接龙"):
        return MODE_CHAIN
    return GEN_MODE_FOLLOW


def resolve_gen_mode(task: dict | None) -> str:
    """任务表列解析成真正写进 manifest.mode 的值。显式选过就用显式的。"""
    t = task or {}
    stored = parse_gen_mode(t.get("gen_mode"))
    if stored in MODES:
        return stored
    return default_mode_for_engine(t.get("engine") or "")


def last_used_mode() -> str:
    """全局记忆。已有任务不读这个；新任务表列默认是跟随引擎。
    仍给老调用方当兜底。"""
    v = config.get("generate.mode", MODE_FIRST)
    return v if v in MODES else MODE_FIRST


def remember_mode(mode: str) -> str:
    mode = mode if mode in MODES else MODE_FIRST
    config.set("generate.mode", mode)
    return mode


def _lab(s) -> str:
    try:
        return "s%03d" % int(s)
    except (TypeError, ValueError):
        return str(s)


def _idx(m: Manifest, s: int) -> int:
    for i, sg in enumerate(m.segments or []):
        try:
            if int(sg.get("s")) == int(s):
                return i
        except (TypeError, ValueError):
            continue
    return -1


def prev_of(m: Manifest, sg: dict) -> dict | None:
    i = _idx(m, int(sg.get("s") or 0))
    if i <= 0:
        return None
    return m.segments[i - 1]


def next_of(m: Manifest, sg: dict) -> dict | None:
    i = _idx(m, int(sg.get("s") or 0))
    if i < 0 or i + 1 >= len(m.segments or []):
        return None
    return m.segments[i + 1]


def rebuild_bridge_links(segments: list[dict]) -> None:
    """按当前 role 把 from_seg / to_image 对齐；不抽帧、不算跨度。"""
    n = len(segments or [])
    for i, sg in enumerate(segments or []):
        if sg.get("role") != "bridge":
            continue
        prev = segments[i - 1] if i > 0 else None
        nxt = segments[i + 1] if i + 1 < n else None
        br = sg.get("bridge") if isinstance(sg.get("bridge"), dict) else {}
        br["from_seg"] = int(prev["s"]) if prev else None
        if nxt and nxt.get("image"):
            br["to_image"] = nxt.get("image") or ""
        else:
            br.setdefault("to_image", "")
        br.setdefault("tail_frame", None)
        br.setdefault("tail_pose", None)
        br.setdefault("tail_checks", None)
        br.setdefault("gap_score", None)
        br.setdefault("gap_why", None)
        br.setdefault("auto_swapped_from", br.get("auto_swapped_from"))
        sg["bridge"] = br


def _checks_ok(src_sg: dict, bridge_sg: dict) -> bool:
    """尾帧在、脸拍到 → 可以接。in_frame 是黄旗（近景常被误判成出画），不挡放行。"""
    br = bridge_sg.get("bridge") if isinstance(bridge_sg.get("bridge"), dict) else {}
    rel = br.get("tail_frame")
    if not rel:
        return False
    checks = br.get("tail_checks") if isinstance(br.get("tail_checks"), dict) else {}
    if checks.get("face_ok") is False:
        return False
    return True


def gap_of(bridge_sg: dict) -> int | None:
    br = bridge_sg.get("bridge") if isinstance(bridge_sg.get("bridge"), dict) else {}
    try:
        g = br.get("gap_score")
        if g is None:
            return None
        return int(g)
    except (TypeError, ValueError):
        return None


def refresh_bridge_ready(m: Manifest, src_sg: dict | None, bridge_sg: dict | None) -> bool:
    """尾帧抽好 + 脸过才算 ready。Grok 接片还要跨度 ≤3。

    Flow 接片跨度、in_frame 都只事后复查，不挡放行。检查台人工通过
    （passed）的接片强制 ready，链条接着跑。
    """
    if not src_sg or not bridge_sg:
        if src_sg is not None:
            src_sg["bridge_ready"] = False
        return False
    br = bridge_sg.get("bridge") if isinstance(bridge_sg.get("bridge"), dict) else {}
    if br.get("passed"):
        src_sg["bridge_ready"] = True
        return True
    ok = _checks_ok(src_sg, bridge_sg)
    # advise：跨度照写 gap_score，不参与放行。block：跨度 >3 仍闸住。
    if gate_mode_of(m, bridge_sg) != "advise":
        g = gap_of(bridge_sg)
        if g is None or g > GAP_ENQUEUE:
            ok = False
    src_sg["bridge_ready"] = bool(ok)
    return bool(ok)


def _wait(code: str, text: str, frm=None) -> dict:
    return {"from": frm, "code": code, "text": text}


def _not_ready_reason(m: Manifest, prev: dict, sg: dict) -> dict:
    """prev 已收片但 bridge_ready=False：说 tail_checks 里哪一项不过。

    绝不能再写「等 sXXX 收片」——那句是 prev 真的没收片专用的。
    """
    br = sg.get("bridge") if isinstance(sg.get("bridge"), dict) else {}
    lab = _lab(prev["s"])
    frm = int(prev["s"])
    rel = br.get("tail_frame")
    checks = br.get("tail_checks") if isinstance(br.get("tail_checks"), dict) else {}
    if not rel:
        return _wait("tail_missing",
                     "%s 的尾帧没抽出来（检查台可重抽）" % lab, frm)
    if checks.get("face_ok") is False:
        return _wait("no_face",
                     "%s 尾帧没拍到脸（检查台可换一帧）" % lab, frm)
    failed = []
    for k, v in checks.items():
        if v is False and k != "in_frame":
            failed.append(k)
    g = gap_of(sg)
    if gate_mode_of(m, sg) == "block":
        if g is None:
            failed.append("gap_score")
        elif g > GAP_ENQUEUE:
            failed.append("跨度 %s" % g)
    if not failed:
        failed.append("bridge_ready")
    text = "%s 尾帧没过检查：%s" % (lab, "、".join(failed))
    # Grok 跨度闸仍点名检查台，但不再说「等 sXXX 收片」。
    if (gate_mode_of(m, sg) == "block"
            and g is not None and g >= GAP_SWAP
            and "检查台" not in text):
        text += "（检查台）"
    return _wait("checks_failed", text, frm)


def wait_reason(m: Manifest, sg: dict, task_dir=None) -> dict | None:
    """接片还不能进队列时：{from, text, code}。主片 / 已齐 / 非锚点模式返回 None。

    尾帧接龙也走这里：等上一段收片 + 尾帧文件在。失败用尽的上一段不挡。
    """
    if is_chain_mode(m):
        return _chain_wait_reason(m, sg, task_dir)
    if not is_anchor_mode(m):
        return None
    if sg.get("role") != "bridge":
        return None
    if sg.get("status") not in ("planned", "failed"):
        return None
    # 53：出局闸只认「对不上 / 超时 / 缩略图」各自的账，不读大杂烩 fail_count
    if int(sg.get("mismatch_count") or 0) >= MISMATCH_STOP:
        return _wait("mismatch_stop", "接片两次首帧都对不上（检查台可重试）")
    if int(sg.get("timeout_count") or 0) >= TIMEOUT_STOP:
        return _wait("timeout_stop", "等了太久没出片 ×3")
    if int(sg.get("thumb_count") or 0) >= THUMB_STOP:
        return _wait("thumb_stop", "下载一直不合格")
    prev = prev_of(m, sg)
    nxt = next_of(m, sg)
    if not prev:
        return _wait("no_prev", "没有上一段，接不了片")
    if prev.get("status") != "collected":
        return _wait("prev_not_collected",
                     "等 %s 收片" % _lab(prev["s"]), int(prev["s"]))
    if not prev.get("bridge_ready"):
        # 旧 flag 可能是 in_frame 硬闸留下的。按现行规则当场再判，不盲信。
        if not refresh_bridge_ready(m, prev, sg):
            return _not_ready_reason(m, prev, sg)
    if not nxt or not str(nxt.get("image") or "").strip():
        who = int(nxt["s"]) if nxt else int(sg["s"]) + 1
        return _wait("no_next_image", "等 %s 的锚点图" % _lab(who), who)
    return None


def can_enqueue(m: Manifest, sg: dict, task_dir=None) -> bool:
    if sg.get("status") not in ("planned", "failed"):
        return False
    if is_chain_mode(m):
        if chain_exhausted(sg):
            return False
        return wait_reason(m, sg, task_dir) is None
    if not is_anchor_mode(m):
        return True
    if sg.get("role") != "bridge":
        return True
    return wait_reason(m, sg) is None


def is_waiting(m: Manifest, sg: dict, task_dir=None) -> bool:
    return wait_reason(m, sg, task_dir) is not None


def stamp_waits(m: Manifest, task_dir=None) -> None:
    """把 wait_on / wait_why / wait_code 写到接片段上，给前端空心虚线用。不算失败。"""
    if not is_anchor_mode(m) and not is_chain_mode(m):
        for sg in m.segments or []:
            sg.pop("wait_on", None)
            sg.pop("wait_why", None)
            sg.pop("wait_code", None)
        return
    for sg in m.segments or []:
        why = wait_reason(m, sg, task_dir)
        if why:
            sg["wait_on"] = why.get("from")
            sg["wait_why"] = why.get("text")
            sg["wait_code"] = why.get("code")
            # 等前置不是失败，清掉误标
            if sg.get("status") == "failed" and not sg.get("error"):
                sg["status"] = "planned"
        else:
            sg.pop("wait_on", None)
            sg.pop("wait_why", None)
            sg.pop("wait_code", None)


def iter_ready(m: Manifest, wanted: set[int], busy: set[int],
               task_dir=None) -> list[dict]:
    """wanted 里、不在 busy、前置已齐、状态 planned/failed 的段，按段号。

    尾帧接龙只放行一段：编号最小的、上一段已齐（或失败用尽）的那一段。
    """
    out = []
    for sg in m.segments or []:
        try:
            s = int(sg["s"])
        except (TypeError, ValueError, KeyError):
            continue
        if s not in wanted or s in busy:
            continue
        if can_enqueue(m, sg, task_dir):
            out.append(sg)
            if is_chain_mode(m):
                break
    return out


def mode_switch_hint(m: Manifest, mode: str) -> str:
    """切模式时给人看的说明。不改段状态；role/bridge 留着，切回来还要用。"""
    mode = mode if mode in MODES else MODE_FIRST
    segs = list(m.segments or [])
    if mode == MODE_CHAIN:
        n = len(segs)
        return (
            "尾帧接龙是串行的，一段做完才能做下一段"
            + ("，%d 段大约要 %d × 单段时长" % (n, n) if n else "")
            + "。不会因为画面不像上一段就拒片；只有黑场 / 全糊 / 没有人才会标黄并换参考"
        )
    if not segs:
        if mode == MODE_ANCHOR:
            return "主片锁身份，接片补连贯；点一次全部生成会自己衔上"
        return ""
    if mode == MODE_FIRST:
        n_keep = sum(
            1 for sg in segs
            if sg.get("role") != "bridge" and sg.get("status") == "collected"
        )
        m_bridge = sum(1 for sg in segs if sg.get("role") == "bridge")
        return (
            "已收的 %d 段单图首帧全部保留（本来就是逐段首帧出的）；"
            "%d 段首尾帧接片改用各自的配图重新生成" % (n_keep, m_bridge)
        )
    n_anchor = sum(1 for sg in segs if sg.get("role") != "bridge")
    n_bridge = sum(1 for sg in segs if sg.get("role") == "bridge")
    return (
        "%d 段保持单图首帧；%d 段会变成首尾帧接片（等上一段收片后再进队）"
        % (n_anchor, n_bridge)
    )


def set_mode(m: Manifest, mode: str, remember: bool = True) -> dict:
    """任务级切模式。role / bridge / wait_on 不删（切回来还要用）；
    stamp_waits 在逐段首帧下会清 wait_on / wait_why。段 status 一个不动。"""
    mode = mode if mode in MODES else MODE_FIRST
    old = mode_of(m)
    m.mode = mode
    if remember:
        remember_mode(mode)
    extra = ""
    if mode == MODE_CHAIN:
        ensure_chain(m)
    elif mode == MODE_ANCHOR:
        has = any((sg.get("role") in ("anchor", "bridge")) for sg in (m.segments or []))
        if not has:
            tails.ensure_roles(m)
        rebuild_bridge_links(m.segments)
        tr = dict(m.track or {})
        if not tr.get("no_adjacent_repeat", True):
            tr["no_adjacent_repeat"] = True
            extra = "已打开间隔排列（锚点段配图不连着同一张）"
        else:
            tr["no_adjacent_repeat"] = True
        m.track = tr
    hint = mode_switch_hint(m, mode)
    if extra and extra not in (hint or ""):
        hint = (hint + "；" + extra) if hint else extra
    stamp_waits(m)
    return {"mode": mode, "hint": hint, "old": old}


def toggle_role(m: Manifest, s: int) -> dict:
    """点链条上某一段切换 role。末段强制主片；相邻接片的 from/to 会重算。"""
    if not m.segments:
        raise OranteError("还没有段")
    sg = m.seg(int(s))
    if not sg:
        raise OranteError("没有 %s" % _lab(s))
    n = len(m.segments)
    last = m.segments[-1]
    first = m.segments[0]
    old = sg.get("role") or "anchor"
    hint_bits = []
    if sg is last:
        sg["role"] = "anchor"
        hint_bits.append("末段必须是单图首帧，没改")
    elif sg is first and old == "anchor":
        # 第一段改成接片没有上一段，接不上
        hint_bits.append("第一段改成首尾帧接片没有上一段可接，保持单图首帧")
        sg["role"] = "anchor"
    else:
        sg["role"] = "bridge" if old != "bridge" else "anchor"
        hint_bits.append("%s 改成%s" % (
            _lab(sg["s"]),
            "首尾帧接片" if sg["role"] == "bridge" else "单图首帧"))
    last["role"] = "anchor"
    if last is not sg:
        hint_bits.append("末段保持单图首帧")
    # 清理不再是接片的空 bridge
    for x in m.segments:
        if x.get("role") == "bridge":
            if not isinstance(x.get("bridge"), dict):
                x["bridge"] = {}
        elif isinstance(x.get("bridge"), dict) and not x["bridge"].get("tail_frame"):
            x.pop("bridge", None)
        if x.get("role") != "bridge":
            x.pop("wait_on", None)
            x.pop("wait_why", None)
            x.pop("wait_code", None)
    rebuild_bridge_links(m.segments)
    neighbors = []
    i = _idx(m, int(sg["s"]))
    for j in (i - 1, i + 1):
        if 0 <= j < n and m.segments[j].get("role") == "bridge":
            neighbors.append(_lab(m.segments[j]["s"]))
    if neighbors:
        hint_bits.append("%s 的衔接对象已更新" % "、".join(neighbors))
    stamp_waits(m)
    return {
        "s": int(sg["s"]),
        "role": sg.get("role"),
        "hint": "；".join(hint_bits),
        "roles": [x.get("role") for x in m.segments],
    }


def pin_image(m: Manifest, s: int, p: str) -> None:
    """把 p 钉进这段，同步改 image。已收/已提交的段不改配图（只改 to_image 调用方自己管）。"""
    p = trackmod.fmt_p(p) or p
    sg = m.seg(int(s))
    if not sg:
        return
    if sg.get("status") not in trackmod.LOCKED_STATES:
        sg["image"] = p
        if not sg.get("prompt_locked"):
            sg["prompt_final"] = ""
    tr = dict(m.track or {})
    pins = dict(tr.get("pins") or {})
    pins[str(int(s))] = p
    tr["pins"] = pins
    m.track = tr


def _subject_ok(im: dict) -> bool:
    if not im:
        return False
    if im.get("role") == "空镜":
        return False
    st = im.get("status") or "ok"
    if st in ("blocked", "missing"):
        return False
    return True


def _last_used_s(segments: list[dict], p: str, before_s: int) -> int:
    last = -1
    for sg in segments or []:
        try:
            sn = int(sg.get("s") or 0)
        except (TypeError, ValueError):
            continue
        if sn >= before_s:
            break
        if (sg.get("image") or "") == p:
            last = sn
    return last


def pick_swap_candidates(m: Manifest, dest_sg: dict, current_p: str,
                         k: int = MAX_SWAP_CANDIDATES) -> list[str]:
    """下一锚点能换的图：主体、没 blocked、遵守间隔、最多 k 张。

    优先挑距离上一次被用最远的（没出现过最优先），和 spec 30 同一套直觉。
    """
    dest_s = int(dest_sg.get("s") or 0)
    i = _idx(m, dest_s)
    prev_p = (m.segments[i - 1].get("image") or "") if i > 0 else ""
    next_p = (m.segments[i + 1].get("image") or "") if 0 <= i < len(m.segments) - 1 else ""
    avoid = {current_p, prev_p, next_p}
    avoid.discard("")
    pool = []
    for im in m.images or []:
        p = im.get("p") or ""
        if not p or p in avoid:
            continue
        if not _subject_ok(im):
            continue
        if not (im.get("file") or ""):
            continue
        pool.append(p)
    pool.sort(key=lambda p: (_last_used_s(m.segments, p, dest_s), p))
    return pool[: max(0, int(k))]


def _image_file(task_dir: Path, m: Manifest, p: str) -> Path | None:
    im = m.image(p) if p else None
    if not im:
        return None
    rel = (im.get("file") or "").replace("\\", "/")
    if not rel:
        return None
    fp = Path(task_dir) / rel
    return fp if fp.is_file() else None


def _rescore_bridge(task_dir: Path, m: Manifest, bridge_sg: dict,
                    gemini=None, job=None) -> tuple[int | None, str]:
    br = bridge_sg.get("bridge") if isinstance(bridge_sg.get("bridge"), dict) else {}
    nxt = next_of(m, bridge_sg)
    to_image = (nxt or {}).get("image") or br.get("to_image") or ""
    br["to_image"] = to_image
    rel = br.get("tail_frame")
    tail_file = Path(task_dir) / rel if rel else None
    dest_file = _image_file(task_dir, m, to_image)
    dest_im = m.image(to_image) if to_image else None
    if not (tail_file and tail_file.is_file() and dest_file):
        br["gap_score"] = None
        br["gap_why"] = "缺尾帧或目标图，跨度算不了"
        bridge_sg["bridge"] = br
        return None, br["gap_why"]
    sc, why = tails.score_gap(
        tail_file, dest_file,
        br.get("tail_pose"),
        dest_im.get("pose") if dest_im else None,
        gemini=gemini, job=job)
    br["gap_score"] = sc
    br["gap_why"] = why
    br.pop("passed", None)  # 跨度重算后旧放行作废
    bridge_sg["bridge"] = br
    return sc, why


def auto_swap_anchor(task_dir, m: Manifest, bridge_sg: dict, log=None,
                     gemini=None, job=None) -> dict | None:
    """跨度 ≥4 时，在图池里最多试 4 张，取跨度最低的钉进下一锚点。

    下一锚点已经 submitted/collected 就不动它的配图（片子对不上），换不动。
    返回 {from, to, gap_from, gap_to, tried}；完全没换成返回 None。
    """
    def _log(msg: str) -> None:
        if callable(log):
            try:
                log(msg)
            except Exception:
                pass

    nxt = next_of(m, bridge_sg)
    if not nxt:
        return None
    br = bridge_sg.get("bridge") if isinstance(bridge_sg.get("bridge"), dict) else {}
    old_p = (nxt.get("image") or br.get("to_image") or "")
    old_gap = gap_of(bridge_sg)
    if nxt.get("status") in trackmod.LOCKED_STATES:
        _log("  · %s 的锚点 %s 已%s，没法自动换" % (
            _lab(bridge_sg["s"]), old_p or "—",
            "收片" if nxt.get("status") == "collected" else "提交"))
        return None
    cands = pick_swap_candidates(m, nxt, old_p, MAX_SWAP_CANDIDATES)
    if not cands:
        _log("  · %s 图池换不出锚点（只剩当前这张或都被间隔/blocked 卡住）" %
             _lab(bridge_sg["s"]))
        return None

    tail_rel = br.get("tail_frame")
    tail_file = Path(task_dir) / tail_rel if tail_rel else None
    if not (tail_file and tail_file.is_file()):
        return None

    best_p, best_sc, best_why = old_p, old_gap, br.get("gap_why") or ""
    tried = []
    for p in cands:
        dest = _image_file(task_dir, m, p)
        if not dest:
            continue
        dest_im = m.image(p) or {}
        sc, why = tails.score_gap(
            tail_file, dest, br.get("tail_pose"), dest_im.get("pose"),
            gemini=gemini, job=job)
        tried.append({"p": p, "gap": sc, "why": why})
        if sc is None:
            continue
        if best_sc is None or sc < best_sc:
            best_p, best_sc, best_why = p, sc, why

    if not best_p or best_p == old_p:
        _log("  · %s 试了 %d 张，锚点仍是 %s（跨度 %s）" % (
            _lab(bridge_sg["s"]), len(tried), old_p or "—",
            old_gap if old_gap is not None else "—"))
        return {"from": old_p, "to": old_p, "gap_from": old_gap,
                "gap_to": old_gap, "tried": tried, "changed": False}

    pin_image(m, int(nxt["s"]), best_p)
    br["to_image"] = best_p
    br["auto_swapped_from"] = old_p
    br["gap_score"] = best_sc
    br["gap_why"] = best_why
    bridge_sg["bridge"] = br
    _log("  · %s 的锚点 %s → %s（跨度 %s → %s）" % (
        _lab(bridge_sg["s"]), old_p, best_p,
        old_gap if old_gap is not None else "—",
        best_sc if best_sc is not None else "—"))
    return {"from": old_p, "to": best_p, "gap_from": old_gap,
            "gap_to": best_sc, "tried": tried, "changed": True}


def apply_gap_gate(task_dir, m: Manifest, src_sg: dict, log=None,
                   gemini=None, job=None) -> dict:
    """主片收片/换图之后：必要时自动换锚点，再写 bridge_ready。"""
    nxt = next_of(m, src_sg)
    info = {"swapped": None, "ready": False, "gap": None}
    if not nxt or nxt.get("role") != "bridge":
        src_sg["bridge_ready"] = bool(src_sg.get("status") == "collected")
        return info
    g = gap_of(nxt)
    info["gap"] = g
    if g is not None and g >= GAP_SWAP:
        info["swapped"] = auto_swap_anchor(
            task_dir, m, nxt, log=log, gemini=gemini, job=job)
        g = gap_of(nxt)
        info["gap"] = g
    info["ready"] = refresh_bridge_ready(m, src_sg, nxt)
    return info


def _reset_seg_planned(task_dir: Path, m: Manifest, sg: dict, log=None,
                       why: str = "", stale_from=None) -> None:
    def _log(msg: str) -> None:
        if callable(log):
            try:
                log(msg)
            except Exception:
                pass

    trash = Path(task_dir) / "clips" / "_弃用"
    if sg.get("takes"):
        trash.mkdir(parents=True, exist_ok=True)
        import time
        for tk in sg.get("takes") or []:
            pth = Path(task_dir) / (tk.get("file") or "")
            if pth.is_file():
                try:
                    pth.rename(trash / ("%s-%s" % (int(time.time()), pth.name)))
                except OSError:
                    pass
    sg["takes"] = []
    sg["status"] = "planned"
    sg["gen_id"] = None
    sg["error"] = None
    sg["check"] = {"issues": []}
    sg["mark"] = False
    sg.pop("fresh", None)
    if not sg.get("prompt_locked"):
        sg["prompt_final"] = ""
    if stale_from is not None:
        # 检查台用这个跟「跨度大」区分：这段只要重拍，不用换锚点
        sg["stale_from"] = int(stale_from)
        br = sg.get("bridge") if isinstance(sg.get("bridge"), dict) else {}
        br["stale_tail"] = True
        sg["bridge"] = br
        clear_bridge_acct(sg)
    _log("  · %s 打回 planned%s" % (_lab(sg["s"]), ("（%s）" % why) if why else ""))


def _is_grok_engine(m, sg: dict | None = None) -> bool:
    """这段（或这个任务）是不是 Grok。优先看 bridge_engine，再看任务引擎。
    grok-single / 旧值 grok 都算；flow 明确不是。"""
    if isinstance(sg, dict):
        be = str(sg.get("bridge_engine") or "").lower()
        if be in ("grok", "grok-single"):
            return True
        if be == "flow":
            return False
    eng = getattr(m, "engine", None)
    if eng is None and isinstance(m, dict):
        eng = m.get("engine")
    return str(eng or "").strip().lower() == "grok"


def grok_bridge_is_single(m, sg: dict | None = None) -> bool:
    """Grok 接片走单图起于尾帧。

    已收/已提交的片子按当时怎么传的分档（旧值 grok 视同双图）。
    还没提的跟当前 GROK_BRIDGE_MODE，这样重生会走新路。
    """
    if isinstance(sg, dict):
        be = str(sg.get("bridge_engine") or "").lower()
        st = sg.get("status") or ""
        if st in ("collected", "submitted") and be:
            return be == "grok-single"
        if be == "flow":
            return False
    return GROK_BRIDGE_MODE == "single_tail" and _is_grok_engine(m, sg)


def gate_mode_of(m, sg: dict | None = None) -> str:
    """跨度闸：Flow 接片 advise（先拍再看），Grok 接片 block（事前闸）。

    判据复用 _is_grok_engine / grok_bridge_is_single，不另起一套。
    非接片没有跨度闸，返回 block 当无操作。
    """
    if not isinstance(sg, dict) or sg.get("role") != "bridge":
        return "block"
    if _is_grok_engine(m, sg) or grok_bridge_is_single(m, sg):
        return "block"
    return "advise"


def default_role_at(segments: list[dict], i: int) -> str:
    """任务模式自动分的 role，和 tails.assign_roles 同一套。"""
    n = len(segments or [])
    if n < 3:
        return "anchor"
    if i == n - 1:
        return "anchor"
    if n % 2 == 0 and i == n - 2:
        return "bridge"
    return "anchor" if i % 2 == 0 else "bridge"


def role_word(role: str) -> str:
    """面向用户：anchor → 单图首帧，bridge → 首尾帧接片，chain → 接龙。"""
    if role == "bridge":
        return "首尾帧接片"
    if role == "chain":
        return "接龙"
    return "单图首帧"


def bridge_path_of(m, sg: dict | None = None) -> str:
    """本段这次要走的路：grok-single / grok / flow。"""
    if grok_bridge_is_single(m, sg):
        return "grok-single"
    if _is_grok_engine(m, sg):
        return "grok"
    return "flow"


def _path_lab(p: str) -> str:
    return {"grok": "双图", "grok-single": "单图", "flow": "Flow"}.get(p or "", p or "无")


def migrate_bridge_acct_53(m) -> bool:
    """一次性：旧 grok 双图账本上的对不上次数，在单图新路下清零。幂等。"""
    if GROK_BRIDGE_MODE != "single_tail":
        return False
    changed = False
    for sg in (m.segments or []):
        if sg.get("role") != "bridge":
            continue
        if str(sg.get("bridge_engine") or "") != "grok":
            continue
        br = sg.get("bridge") if isinstance(sg.get("bridge"), dict) else {}
        if br.get("acct_migrated_53"):
            continue
        sg["mismatch_count"] = 0
        br["acct_migrated_53"] = True
        sg["bridge"] = br
        changed = True
    return changed


def settle_bridge_paths(m, log=None) -> list:
    """组队前：路变了就把旧的对不上记录清零，并更新 bridge_engine。

    只动 planned/failed（还没提 / 要重提的）。已收的片子按当时怎么传的分档。
    """
    touched = []
    for sg in (m.segments or []):
        if sg.get("role") != "bridge":
            continue
        if sg.get("status") not in ("planned", "failed"):
            continue
        want = bridge_path_of(m, sg)
        have = str(sg.get("bridge_engine") or "")
        if have == want:
            continue
        if have:
            sg["mismatch_count"] = 0
            if log:
                log("  · %s 接片换了路（%s→%s），旧的对不上记录清零" % (
                    _lab(sg.get("s")), _path_lab(have), _path_lab(want)))
        sg["bridge_engine"] = want
        touched.append(int(sg.get("s") or 0))
    return touched


def clear_bridge_acct(sg: dict) -> None:
    """检查台「重试接片」/ 重生：清对不上、超时、缩略图账。"""
    sg["mismatch_count"] = 0
    sg["timeout_count"] = 0
    sg["thumb_count"] = 0


# ---------------------------------------------------------------- 尾帧接龙
def default_desc_policy() -> str:
    """规格 95：新任务的尾帧描述语策略。默认一直沿用上一段（never）。"""
    try:
        from .. import config as _cfg
        pol = str(_cfg.get("chain.desc_policy", "never") or "never").strip().lower()
    except Exception:
        pol = "never"
    return pol if pol in CHAIN_DESC_POLICIES else "never"


def chain_cfg(m: Manifest) -> dict:
    """manifest.chain 默认值。缺字段当场补上。"""
    ch = getattr(m, "chain", None)
    if not isinstance(ch, dict):
        ch = {}
    ch.setdefault("start_image", "")
    pol = str(ch.get("desc_policy") or default_desc_policy()).strip().lower()
    if not ch.get("policy_v95"):
        # 规格 95 之前的默认是 auto（变化大才重写）——那不是用户选的。
        # 老任务一次性改成现在的默认（沿用）；用户在 ④ 里改过的（非 auto）不动。
        if pol == "auto":
            pol = default_desc_policy()
        ch["policy_v95"] = True
    ch["desc_policy"] = pol if pol in CHAIN_DESC_POLICIES else default_desc_policy()
    try:
        ch["reuse_dist"] = int(ch.get("reuse_dist") if ch.get("reuse_dist") is not None
                               else CHAIN_REUSE_DIST)
    except (TypeError, ValueError):
        ch["reuse_dist"] = CHAIN_REUSE_DIST
    try:
        ch["upscale_every"] = int(ch.get("upscale_every") if ch.get("upscale_every") is not None
                                  else CHAIN_UPSCALE_EVERY)
    except (TypeError, ValueError):
        ch["upscale_every"] = CHAIN_UPSCALE_EVERY
    m.chain = ch
    return ch


def default_start_image(m: Manifest) -> str:
    """默认起头图：expand_ref 勾着的第一张 ok 图；没有就 p01；再没有就第一张 ok。"""
    ok = []
    for im in m.images or []:
        if (im.get("status") or "ok") != "ok":
            continue
        if im.get("role") == "空镜":
            continue
        if _is_tail_im(im):
            continue
        p = im.get("p") or ""
        if not p:
            continue
        ok.append(im)
        if im.get("expand_ref") is True:
            return p
    for im in ok:
        if im.get("p") == "p01":
            return "p01"
    return (ok[0].get("p") or "p01") if ok else "p01"


def ensure_chain(m: Manifest) -> dict:
    """切到尾帧接龙：所有段 role=chain，补 chain 配置，没换图起头的段配图对齐起头图。"""
    cfg = chain_cfg(m)
    start = cfg.get("start_image") or default_start_image(m)
    if start and m.image(start):
        cfg["start_image"] = start
    else:
        start = default_start_image(m)
        cfg["start_image"] = start
    m.chain = cfg
    for sg in m.segments or []:
        sg["role"] = "chain"
        if is_chain_restart(sg) or not (start and m.image(start)):
            continue
        # 规格 95：已经配上上一段尾帧图的段不动（它就该从那张尾帧接）
        if _is_tail_im(m.image(sg.get("image") or "")):
            continue
        sg["image"] = start
    return cfg


def _is_tail_im(im) -> bool:
    from . import tails as tailsmod
    return tailsmod.is_tail_image(im)


def set_chain_settings(m: Manifest, fields: dict | None) -> dict:
    """④ 生成那一行：起头图 / 描述语策略 / 提清间隔。"""
    cfg = chain_cfg(m)
    fields = fields or {}
    if "start_image" in fields:
        p = str(fields.get("start_image") or "").strip()
        if p and m.image(p):
            cfg["start_image"] = p
            for sg in m.segments or []:
                if is_chain_restart(sg):
                    continue
                if _is_tail_im(m.image(sg.get("image") or "")):
                    continue
                sg["image"] = p
        elif not p:
            cfg["start_image"] = default_start_image(m)
    if "desc_policy" in fields:
        pol = str(fields.get("desc_policy") or default_desc_policy()).strip().lower()
        cfg["desc_policy"] = pol if pol in CHAIN_DESC_POLICIES else default_desc_policy()
    if "reuse_dist" in fields:
        try:
            cfg["reuse_dist"] = max(0, int(fields.get("reuse_dist")))
        except (TypeError, ValueError):
            pass
    if "upscale_every" in fields:
        try:
            cfg["upscale_every"] = max(0, int(fields.get("upscale_every")))
        except (TypeError, ValueError):
            pass
    m.chain = cfg
    if is_chain_mode(m):
        ensure_chain(m)
    return dict(cfg)


def is_chain_restart(sg: dict | None) -> bool:
    """这段改成了单图首帧换图起头。role 仍是 chain，下一段还从它的尾帧接。"""
    if not isinstance(sg, dict):
        return False
    gm = str(sg.get("gen_mode") or "").strip()
    return gm in (MODE_FIRST, "single", "逐段首帧")


def chain_exhausted(sg: dict | None) -> bool:
    """多次重试用尽：标 failed 且计数到头。下一段照常放行。"""
    if not isinstance(sg, dict):
        return False
    if sg.get("status") != "failed":
        return False
    if int(sg.get("fail_count") or 0) >= CHAIN_GIVE_UP:
        return True
    if int(sg.get("timeout_count") or 0) >= TIMEOUT_STOP:
        return True
    if int(sg.get("mismatch_count") or 0) >= MISMATCH_STOP:
        return True
    return False


def _chain_tail_exists(task_dir, prev: dict) -> bool:
    from . import tails as tailsmod
    if task_dir:
        tp = tailsmod.tail_path(Path(task_dir), int(prev.get("s") or 0))
        if tp.is_file():
            return True
        return False
    ch = prev.get("chain") if isinstance(prev.get("chain"), dict) else {}
    rel = (ch.get("tail_file") or "").replace("\\", "/")
    return bool(rel)


def _chain_wait_reason(m: Manifest, sg: dict, task_dir=None) -> dict | None:
    if sg.get("status") not in ("planned", "failed"):
        return None
    if chain_exhausted(sg):
        return None
    prev = prev_of(m, sg)
    if not prev:
        return None
    if chain_exhausted(prev):
        return None
    if prev.get("status") != "collected":
        return _wait("prev_not_collected",
                     "等 %s 收片" % _lab(prev["s"]), int(prev["s"]))
    if not _chain_tail_exists(task_dir, prev):
        pch = prev.get("chain") if isinstance(prev.get("chain"), dict) else {}
        why = str(pch.get("tail_error") or "").strip()
        text = "%s 的尾帧没抽出来（检查台可重抽）" % _lab(prev["s"])
        if why:
            text = "%s 的尾帧没抽出来：%s" % (_lab(prev["s"]), why)
        return _wait("tail_missing", text, int(prev["s"]))
    return None


def _image_rel(m: Manifest, p: str) -> str:
    im = m.image(p) if p else None
    if not im:
        return ""
    return (im.get("file") or "").replace("\\", "/")


def resolve_chain_ref(m: Manifest, task_dir, sg: dict) -> dict:
    """本段参考图：s001 / 换图起头用起头图；其余用最近一个没被标严重的尾帧。

    都没有就退回起头图，ref_note 写明为什么退。
    """
    from . import tails as tailsmod
    d = Path(task_dir) if task_dir else None
    cfg = chain_cfg(m)
    start_p = cfg.get("start_image") or default_start_image(m)
    start_rel = _image_rel(m, start_p)
    s = int(sg.get("s") or 0)
    ch = sg.get("chain") if isinstance(sg.get("chain"), dict) else {}

    def _start(note=""):
        return {
            "ref_kind": "start", "ref_from": start_p,
            "ref_file": start_rel, "ref_note": note,
        }

    if is_chain_restart(sg):
        p = sg.get("image") or start_p
        rel = _image_rel(m, p) or start_rel
        return {"ref_kind": "start", "ref_from": p, "ref_file": rel,
                "ref_note": "换图起头"}

    if s <= 1 or prev_of(m, sg) is None:
        return _start()

    skipped = []
    for prev in reversed(list(m.segments or [])):
        try:
            ps = int(prev.get("s") or 0)
        except (TypeError, ValueError):
            continue
        if ps >= s:
            continue
        pch = prev.get("chain") if isinstance(prev.get("chain"), dict) else {}
        if prev.get("status") != "collected":
            if chain_exhausted(prev):
                skipped.append("%s 失败用尽" % _lab(ps))
            continue
        if pch.get("tail_severe"):
            skipped.append("%s 尾帧%s" % (_lab(ps), pch.get("tail_severe") or "严重穿帮"))
            continue
        rel = ""
        if d is not None:
            tp = tailsmod.tail_path(d, ps)
            if tp.is_file():
                try:
                    rel = str(tp.relative_to(d)).replace("\\", "/")
                except ValueError:
                    rel = str(tp)
        if not rel:
            rel = (pch.get("tail_file") or "").replace("\\", "/")
        if not rel:
            skipped.append("%s 没有尾帧文件" % _lab(ps))
            continue
        note = ""
        if skipped:
            note = "；".join(skipped) + "，改从 %s 尾帧接" % _lab(ps)
        elif ps != s - 1:
            note = "上一段不可用，改从 %s 尾帧接" % _lab(ps)
        return {
            "ref_kind": "tail", "ref_from": _lab(ps),
            "ref_file": rel, "ref_note": note,
        }
    note = "；".join(skipped) if skipped else "前面没有可用尾帧"
    return _start(note + "，退回起头图" if note else "退回起头图")


def apply_chain_ref(m: Manifest, task_dir, sg: dict) -> dict:
    """把 resolve_chain_ref 写进 sg['chain']，保留已有 desc / 提清记录。

    规格 95：段的配图跟着真正用的参考走——尾帧登记成的图 / 起头图。
    这样 ③ 编排、成片文件名（-s002-p04.mp4）和实际首帧是一回事。
    """
    info = resolve_chain_ref(m, task_dir, sg)
    ch = sg.get("chain") if isinstance(sg.get("chain"), dict) else {}
    ch.update(info)
    sg["chain"] = ch
    p_ref = chain_ref_image_p(m, ch)
    if p_ref and m.image(p_ref) and sg.get("image") != p_ref:
        sg["image"] = p_ref
    return ch


def chain_ref_image_p(m: Manifest, ch: dict) -> str:
    """参考对应的图号：尾帧 → 登记图（没登记就空）；起头 → ref_from。"""
    from . import tails as tailsmod
    ch = ch or {}
    if ch.get("ref_kind") == "tail":
        lab = str(ch.get("ref_from") or "")
        if lab.startswith("s") and lab[1:].isdigit():
            tim = tailsmod.tail_image_of(m, int(lab[1:]))
            return (tim or {}).get("p") or ""
        return ""
    if ch.get("ref_kind") == "start":
        return str(ch.get("ref_from") or "")
    return ""


def chain_success_count(m: Manifest, before_s: int) -> int:
    """before_s 之前，从最近一次换图起头（含 s001）起已 collected 的段数。失败重试不算。"""
    n = 0
    for sg in m.segments or []:
        try:
            s = int(sg.get("s") or 0)
        except (TypeError, ValueError):
            continue
        if s >= int(before_s or 0):
            break
        if sg.get("status") != "collected":
            continue
        if is_chain_restart(sg):
            n = 1
        else:
            n += 1
    return n


def _last_collected_before(m: Manifest, before_s: int) -> dict | None:
    last = None
    for sg in m.segments or []:
        try:
            s = int(sg.get("s") or 0)
        except (TypeError, ValueError):
            continue
        if s >= int(before_s or 0):
            break
        if sg.get("status") == "collected":
            last = sg
    return last


def maybe_upscale_chain_tail(job, m: Manifest, task_dir, next_sg: dict) -> dict | None:
    """提交下一段之前：每 upscale_every 段把上一段尾帧提一次清。失败不是阻塞。"""
    cfg = chain_cfg(m)
    every = int(cfg.get("upscale_every") or 0)
    if every <= 0:
        return None
    s = int(next_sg.get("s") or 0)
    n = chain_success_count(m, s)
    if n <= 0 or n % every != 0:
        return None
    src_sg = _last_collected_before(m, s)
    if not src_sg:
        return None
    return upscale_chain_tail(job, m, task_dir, int(src_sg["s"]), auto=True,
                              next_s=s)


def upscale_chain_tail(job, m: Manifest, task_dir, s: int, *, auto: bool = False,
                       next_s=None) -> dict:
    """对 tails/sNNN.jpg 跑 66 号提清。过闸写回同一路径；没过闸用原尾帧，链条不停。"""
    from . import expand, tails as tailsmod
    d = Path(task_dir)
    src = tailsmod.tail_path(d, int(s))
    lab = _lab(s)
    nxt_lab = _lab(next_s) if next_s else "下一段"
    empty = {"ok": False, "s": int(s), "reason": "", "used_original": True}

    def _log(msg: str) -> None:
        if job is not None and callable(getattr(job, "log", None)):
            try:
                job.log(msg)
            except Exception:
                pass

    if not src.is_file():
        _log("⚠ %s 尾帧不在，没法提清" % lab)
        return {**empty, "reason": "尾帧不在"}
    try:
        t = None
        try:
            from .. import tasks as taskmod
            if getattr(job, "task_id", None):
                t = taskmod.get(job.task_id)
        except Exception:
            t = None
        # 提清账号跟着扩图走，不跟当前视频账号
        aid = expand.resolve_account_id(t, persist=False) if t else ""
        acc = None
        if aid:
            from .. import accounts
            try:
                acc = accounts.get(aid)
            except Exception:
                acc = None
        acc_name = (acc or {}).get("name") or aid or ""
        old_aid = getattr(job, "account_id", None) if job is not None else None
        if job is not None:
            job.account_id = aid or None
        eng = None
        try:
            eng = expand._connect_engine(job) if job is not None else None
        finally:
            if job is not None:
                job.account_id = old_aid
        try:
            r = expand.upscale_candidate(
                job, src, eng, acc_name,
                work_dir=d / expand.UPSCALE_DIR)
        finally:
            if eng is not None:
                end = getattr(eng, "end_credits", None)
                if callable(end):
                    try:
                        end()
                    except Exception:
                        pass
                close = getattr(eng, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
    except Exception as e:  # noqa: BLE001
        why = str(e)[:160]
        _log("⚠ %s 尾帧提清没过闸（%s），%s 用原尾帧" % (lab, why, nxt_lab))
        return {**empty, "reason": why}

    if not r.get("ok"):
        why = r.get("reason") or "没过闸"
        _log("⚠ %s 尾帧提清没过闸（%s），%s 用原尾帧" % (lab, why, nxt_lab))
        return {**empty, "reason": why, "dist": r.get("dist"),
                "from": r.get("from"), "to": r.get("to")}

    keep = tailsmod.TAIL_OLD_KEEP_CHAIN if is_chain_mode(m) else tailsmod.TAIL_OLD_KEEP
    tailsmod.archive_old_tail(src, keep=keep)
    import shutil
    shutil.copy2(str(r["cand"]), str(src))
    sg = m.seg(int(s))
    ch = sg.get("chain") if sg and isinstance(sg.get("chain"), dict) else {}
    rec = {
        "from": list(r.get("from") or []),
        "to": list(r.get("to") or []),
        "dist": r.get("dist"),
        "acc": acc_name,
    }
    if sg is not None:
        ch["tail_upscaled"] = rec
        sg["chain"] = ch
    # 规格 95：登记成图的副本一起换
    try:
        tailsmod.sync_tail_image(d, m, int(s))
    except Exception:
        pass
    src_wh = rec["from"]
    dst_wh = rec["to"]
    _log("· %s 尾帧提清 %s×%s → %s×%s" % (
        lab,
        src_wh[0] if len(src_wh) > 1 else "?",
        src_wh[1] if len(src_wh) > 1 else "?",
        dst_wh[0] if len(dst_wh) > 1 else "?",
        dst_wh[1] if len(dst_wh) > 1 else "?",
    ))
    return {"ok": True, "s": int(s), "used_original": False, **rec}


def resolve_chain_desc(job, m: Manifest, task_dir, sg: dict) -> dict:
    """提交前决定这段画面描述语：变化不大就沿用，变了才重写。"""
    from . import describe as descmod
    d = Path(task_dir)
    cfg = chain_cfg(m)
    policy = cfg.get("desc_policy") or "auto"
    reuse_dist = int(cfg.get("reuse_dist") or CHAIN_REUSE_DIST)
    ch = sg.get("chain") if isinstance(sg.get("chain"), dict) else {}
    sg["chain"] = ch
    ref_rel = (ch.get("ref_file") or "").replace("\\", "/")
    ref_path = (d / ref_rel) if ref_rel else None
    prev = prev_of(m, sg)
    prev_ch = (prev.get("chain") if prev and isinstance(prev.get("chain"), dict)
               else {}) or {}
    s = int(sg.get("s") or 0)
    lab = _lab(s)

    def _log(msg: str) -> None:
        if job is not None and callable(getattr(job, "log", None)):
            try:
                job.log(msg)
            except Exception:
                pass

    start_p = cfg.get("start_image") or default_start_image(m)
    start_im = m.image(start_p) if start_p else None
    start_desc = (start_im or {}).get("desc") or ""

    if s <= 1 or prev is None or is_chain_restart(sg):
        src_im = m.image(sg.get("image") or start_p) or start_im or {}
        ch["desc"] = src_im.get("desc") or start_desc
        ch["desc_src"] = "start"
        if src_im.get("pose"):
            ch["tail_pose"] = src_im.get("pose")
        who = ch.get("ref_from") or start_p or "起头图"
        _log("· %s 起于 %s" % (lab, who))
        return ch

    prev_desc = prev_ch.get("desc") or start_desc
    prev_ref = (prev_ch.get("ref_file") or "").replace("\\", "/")
    dist = None
    if ref_path and ref_path.is_file() and prev_ref:
        prev_path = d / prev_ref
        if prev_path.is_file():
            dist = _ahash_dist(ref_path, prev_path)
    ch["ref_dist"] = dist

    # 规格 95：参考的尾帧登记成了图（p 号顺延）。那张图上的描述语就是本段要用的：
    # 沿用策略下它抄的是上一段的；每次重写策略下它是收片时 Gemini 现写的；
    # 用户在 ② 多模态 手改过（desc_manual）的一律照用。
    from . import tails as tailsmod
    ref_lab = str(ch.get("ref_from") or "")
    ref_s = None
    if ref_lab.startswith("s") and ref_lab[1:].isdigit():
        ref_s = int(ref_lab[1:])
    tim = tailsmod.tail_image_of(m, ref_s) if ref_s is not None else None
    tdesc = str((tim or {}).get("desc") or "").strip()
    tp = (tim or {}).get("p") or ""

    reuse = False
    if tim and tim.get("desc_manual") and tdesc:
        reuse = True
    elif policy == "never":
        reuse = True
    elif policy == "always":
        reuse = bool(tdesc and (tim or {}).get("desc_from") == "gemini")
    else:
        reuse = dist is not None and dist <= reuse_dist

    if reuse and (tdesc or prev_desc):
        ch["desc"] = tdesc or prev_desc
        if tdesc:
            ch["desc_src"] = "image:%s" % tp
        else:
            ch["desc_src"] = "reuse:%s" % _lab(prev.get("s"))
        if prev_ch.get("tail_pose") and not ch.get("tail_pose"):
            ch["tail_pose"] = prev_ch.get("tail_pose")
        if tim and tim.get("pose") and not ch.get("tail_pose"):
            ch["tail_pose"] = tim.get("pose")
        dist_s = str(dist) if dist is not None else "?"
        if ch.get("ref_kind") == "tail":
            src_txt = "%s 尾帧（%s，距 %s）" % (ref_lab, tp or "—", dist_s)
        else:
            src_txt = "起头图 %s" % (ch.get("ref_from") or start_p)
            if ch.get("ref_note"):
                src_txt += "（%s）" % ch.get("ref_note")
        if tim and tim.get("desc_from") == "gemini":
            _log("· %s 起于 %s，描述语用 %s 上新写的" % (lab, src_txt, tp))
        else:
            _log("· %s 起于 %s，描述语沿用 %s" % (lab, src_txt, _lab(prev.get("s"))))
        return ch

    rewritten = False
    if ref_path and ref_path.is_file():
        try:
            r = descmod.describe_file(job, d, ref_path)
            if r.get("desc"):
                ch["desc"] = r["desc"]
                rewritten = True
            if r.get("pose"):
                ch["tail_pose"] = r["pose"]
            if rewritten and tim is not None and not tim.get("desc_manual"):
                tim["desc"] = r["desc"]
                tim["desc_from"] = "gemini"
                if r.get("pose"):
                    tim["pose"] = r["pose"]
        except Exception as e:  # noqa: BLE001
            _log("· %s 重写描述语失败（%s），沿用上一段" % (lab, str(e)[:80]))
    if rewritten:
        ch["desc_src"] = "gemini"
        dist_s = str(dist) if dist is not None else "?"
        if policy == "always":
            _log("· %s 已给 %s 尾帧（%s）写新描述语" % (lab, _lab(prev.get("s")), tp or "—"))
        else:
            _log("· %s 尾帧变化较大（距 %s），已重写描述语" % (lab, dist_s))
    else:
        ch["desc"] = tdesc or prev_desc
        ch["desc_src"] = "reuse:%s" % _lab(prev.get("s"))
        dist_s = str(dist) if dist is not None else "?"
        _log("· %s 描述语沿用 %s（尾帧距 %s）" % (lab, _lab(prev.get("s")), dist_s))
    from_lab = ch.get("ref_from") or (_lab(prev.get("s")) + " 尾帧")
    _log("· %s 起于 %s" % (lab, from_lab))
    return ch


def mark_chain_upstream_changed(m: Manifest, src_sg: dict, log=None) -> dict:
    """chain 模式：重生后不自动级联，只给下一段打「上游尾帧已变」。"""
    def _log(msg: str) -> None:
        if callable(log):
            try:
                log(msg)
            except Exception:
                pass

    nxt = next_of(m, src_sg)
    src_s = int(src_sg.get("s") or 0)
    if not nxt:
        return {"action": "none", "s": None, "gap": None}
    if nxt.get("status") not in ("collected", "submitted"):
        return {"action": "none", "s": int(nxt.get("s") or 0), "gap": None}
    ch = nxt.get("chain") if isinstance(nxt.get("chain"), dict) else {}
    ch["upstream_changed"] = True
    ch["stale_from"] = src_s
    nxt["chain"] = ch
    nxt["stale_from"] = src_s
    chk = dict(nxt.get("check") or {})
    issues = [i for i in (chk.get("issues") or [])
              if (i.get("code") != "chain_upstream")]
    issues.append({
        "level": "warn", "code": "chain_upstream",
        "text": "上游 %s 尾帧已变，这里可能有跳变" % _lab(src_s),
    })
    chk["issues"] = issues
    nxt["check"] = chk
    _log("· %s 上游 %s 尾帧已变，%s 不动（检查台可从这里往后重跑）" % (
        _lab(nxt["s"]), _lab(src_s), _lab(nxt["s"])))
    return {"action": "keep", "reason": "chain_no_cascade",
            "s": int(nxt["s"]), "gap": None}


def cascade_from(task_dir, m: Manifest, s: int, log=None) -> dict:
    """检查台「从这里往后重跑」：s 及之后已收/已提交的段打回 planned。用户点了才级联。"""
    reset_ss = []
    src_s = int(s)
    for sg in m.segments or []:
        try:
            sn = int(sg.get("s") or 0)
        except (TypeError, ValueError):
            continue
        if sn < src_s:
            continue
        if sg.get("status") in ("collected", "submitted", "failed", "blocked"):
            _reset_seg_planned(task_dir, m, sg, log=log,
                               why="从 %s 往后重跑" % _lab(src_s))
            ch = sg.get("chain") if isinstance(sg.get("chain"), dict) else {}
            ch.pop("upstream_changed", None)
            ch.pop("stale_from", None)
            sg["chain"] = ch
            sg.pop("stale_from", None)
            reset_ss.append(sn)
    stamp_waits(m, task_dir)
    return {"reset": reset_ss, "s": src_s}


def _ahash_dist(a, b) -> int | None:
    """两张图的 aHash 汉明距。算不出返回 None。"""
    from . import verify as verifymod
    if not a or not b:
        return None
    pa, pb = Path(a), Path(b)
    if not pa.is_file() or not pb.is_file():
        return None
    ha = verifymod.ahash_image(pa)
    hb = verifymod.ahash_image(pb)
    if ha is None or hb is None:
        return None
    return bin(ha ^ hb).count("1")


def _clip_of(task_dir, m: Manifest, sg: dict) -> Path | None:
    from . import tails
    return tails.clip_of(task_dir, m, sg)


def score_collected_bridge(task_dir, m: Manifest, sg: dict,
                           work: Path | None = None) -> dict:
    """抽成品第一帧（-ss 0）对 tail_frame、尾帧（3~8 最锐）对 to_image。

    返回 {first_dist, last_dist, first_ok, last_ok, first, last, error}。
    首帧阈值走 verify._thresholds_for（Grok 单图 16 / Flow 双图 30）。
    """
    from . import tails
    from .checks import find_ffmpeg
    from .procutil import run_quiet

    out = {
        "first_dist": None, "last_dist": None,
        "first_ok": False, "last_ok": False,
        "first": None, "last": None, "error": "",
    }
    clip = _clip_of(task_dir, m, sg)
    if not clip or not Path(clip).is_file():
        out["error"] = "成片不在"
        return out
    br = sg.get("bridge") if isinstance(sg.get("bridge"), dict) else {}
    tail_rel = (br.get("tail_frame") or "").replace("\\", "/")
    tail = Path(task_dir) / tail_rel if tail_rel else None
    nxt = next_of(m, sg)
    to_p = (nxt or {}).get("image") or br.get("to_image") or ""
    dest = _image_file(task_dir, m, to_p)
    work = Path(work) if work else (
        Path(task_dir) / BRIDGE_TMP_REL / _lab(sg.get("s")) / "_chk")
    work.mkdir(parents=True, exist_ok=True)
    first = work / "first.jpg"
    ff = find_ffmpeg()
    if not ff:
        out["error"] = "找不到 ffmpeg"
        return out
    r = run_quiet(
        [ff, "-hide_banner", "-loglevel", "error", "-ss", "0",
         "-i", str(clip), "-frames:v", "1", "-an", "-sn", "-dn",
         "-y", str(first)],
        timeout=30)
    if r.returncode != 0 or not first.is_file() or first.stat().st_size < 100:
        out["error"] = "抽第一帧失败"
        return out
    out["first"] = str(first)
    from . import verify as verifymod
    _, soft_th = verifymod._thresholds_for(sg)
    if tail and tail.is_file():
        out["first_dist"] = _ahash_dist(first, tail)
        out["first_ok"] = (
            out["first_dist"] is not None
            and out["first_dist"] <= soft_th)

    scan = work / "scan"
    try:
        cands = tails.scan_tail_candidates(clip, tails.FROM_END_HI, scan)
        picked = tails.pick_sharpest(cands, tails.FROM_END_LO, tails.FROM_END_HI)
    except Exception as e:  # noqa: BLE001
        picked = None
        if not out["error"]:
            out["error"] = "抽尾帧失败：%s" % str(e)[:80]
    if picked and picked.get("path"):
        last = Path(picked["path"])
        out["last"] = str(last)
        if dest and dest.is_file():
            out["last_dist"] = _ahash_dist(last, dest)
            out["last_ok"] = (
                out["last_dist"] is not None
                and out["last_dist"] <= soft_th)
    return out


def _retry_grok_bridge(task_dir: Path, m: Manifest, sg: dict, log=None,
                       why: str = "") -> None:
    """接片首帧没对上：片子挪到 _弃用，段打回 planned，提示词和图都不动。"""
    def _log(msg: str) -> None:
        if callable(log):
            try:
                log(msg)
            except Exception:
                pass

    trash = Path(task_dir) / "clips" / "_弃用"
    if sg.get("takes"):
        trash.mkdir(parents=True, exist_ok=True)
        import time
        for tk in sg.get("takes") or []:
            pth = Path(task_dir) / (tk.get("file") or "")
            if pth.is_file():
                try:
                    pth.rename(trash / ("%s-%s" % (int(time.time()), pth.name)))
                except OSError:
                    pass
    sg["takes"] = []
    sg["status"] = "planned"
    sg["gen_id"] = None
    sg["error"] = None
    sg.pop("fresh", None)
    # 同提示词、不换图：prompt_final / image 都留着
    _log("  · %s 打回 planned%s" % (
        _lab(sg["s"]), ("（%s）" % why) if why else ""))


def _mark_bridge_mismatch_yellow(sg: dict, first_dist, last_dist) -> None:
    """再不对：留下成片，标黄进检查台。用现成黄档（跨度 3），理由写成对不上。"""
    br = sg.get("bridge") if isinstance(sg.get("bridge"), dict) else {}
    br["mismatch"] = True
    br["mismatch_why"] = "接片首帧和上一段对不上"
    br["mismatch_first_dist"] = first_dist
    br["mismatch_last_dist"] = last_dist
    if "mismatch_gap" not in br:
        br["mismatch_gap"] = br.get("gap_score")
    # 跨度 3 是检查台现成黄档；原跨度记在 mismatch_gap，不卡链。
    try:
        g = br.get("gap_score")
        g = int(g) if g is not None else None
    except (TypeError, ValueError):
        g = None
    if g is None or g < GAP_ENQUEUE:
        br["gap_score"] = GAP_ENQUEUE
    old_why = str(br.get("gap_why") or "").strip()
    if "对不上" not in old_why:
        br["gap_why"] = "接片首帧和上一段对不上" + (
            ("；原：" + old_why) if old_why else "")
    sg["bridge"] = br
    chk = dict(sg.get("check") or {})
    issues = [i for i in (chk.get("issues") or [])
              if (i.get("code") != "bridge_mismatch")]
    issues.append({
        "level": "warn",
        "code": "bridge_mismatch",
        "text": "接片首帧和上一段对不上",
    })
    chk["issues"] = issues
    sg["check"] = chk


def check_grok_bridge_after_collect(task_dir, m: Manifest, sg: dict,
                                    log=None) -> dict:
    """Grok 接片收片后：首帧没对上 → 自动重生一次；仍不对 → 留下标黄，不卡链。

    只对 engine=grok 的接片。返回 {action: keep|reset|none, s, first_dist, last_dist}。
    generate 看到 action=reset 会把这一段重新推进队列。
    """
    def _log(msg: str) -> None:
        if callable(log):
            try:
                log(msg)
            except Exception:
                pass

    empty = {"action": "none", "s": int(sg.get("s") or 0) or None,
             "first_dist": None, "last_dist": None}
    if sg.get("role") != "bridge" or not _is_grok_engine(m, sg):
        return empty
    if sg.get("status") != "collected":
        return empty
    br = sg.get("bridge") if isinstance(sg.get("bridge"), dict) else {}
    from_s = br.get("from_seg")
    from_lab = _lab(from_s) if from_s else "上一段"
    try:
        scored = score_collected_bridge(task_dir, m, sg)
    except Exception as e:  # noqa: BLE001
        _log("  · %s 接片首帧自检失败：%s" % (_lab(sg["s"]), str(e)[:120]))
        return empty
    first_d = scored.get("first_dist")
    last_d = scored.get("last_dist")
    br["mismatch_first_dist"] = first_d
    br["mismatch_last_dist"] = last_d
    sg["bridge"] = br
    empty.update({"first_dist": first_d, "last_dist": last_d, "score": scored})
    if scored.get("error") and first_d is None:
        _log("  · %s 接片首帧自检抽帧失败（%s），先留下" % (
            _lab(sg["s"]), scored.get("error")))
        return {**empty, "action": "keep"}
    if scored.get("first_ok"):
        br["mismatch"] = False
        sg["bridge"] = br
        return {**empty, "action": "keep"}

    dist_s = first_d if first_d is not None else "?"
    retried = int(br.get("mismatch_retries") or 0)
    if retried < 1:
        _log("✗ %s 接片首帧和 %s 尾帧对不上（距离 %s），重生一次" % (
            _lab(sg["s"]), from_lab, dist_s))
        br["mismatch"] = True
        br["mismatch_retries"] = retried + 1
        sg["bridge"] = br
        _retry_grok_bridge(task_dir, m, sg, log=log,
                           why="接片首帧对不上，重生")
        return {**empty, "action": "reset", "s": int(sg["s"])}

    _log("✗ %s 接片首帧和 %s 尾帧对不上（距离 %s），留下并标黄" % (
        _lab(sg["s"]), from_lab, dist_s))
    _mark_bridge_mismatch_yellow(sg, first_d, last_d)
    return {**empty, "action": "keep"}


def stale_tail_check(nxt: dict) -> dict:
    """接片成品首帧 vs 现在这张尾帧。返回 {stale, dist, why, log}。

    汉明距 > DIST_SOFT（16）→ 这条片子是拿旧尾帧拍的。
    任一 hash 缺失（老数据、ffmpeg 不在）→ 也当 stale：宁可多拍一条。
    Flow / Grok 接片同一套判据。
    """
    from . import verify as verifymod
    br = nxt.get("bridge") if isinstance(nxt.get("bridge"), dict) else {}
    tail_h = str(br.get("tail_ahash") or "").strip()
    tks = nxt.get("takes") or []
    take_h = ""
    if tks:
        take_h = str((tks[-1] or {}).get("frame_ahash") or "").strip()
    if not tail_h:
        return {"stale": True, "dist": None, "why": "missing_tail",
                "log": "算不出尾帧指纹"}
    if not take_h:
        return {"stale": True, "dist": None, "why": "missing_frame",
                "log": "算不出接片首帧指纹"}
    a = verifymod.ahash_from_hex(tail_h)
    b = verifymod.ahash_from_hex(take_h)
    if a is None or b is None:
        miss_tail = a is None
        return {
            "stale": True, "dist": None,
            "why": "missing_tail" if miss_tail else "missing_frame",
            "log": "算不出尾帧指纹" if miss_tail else "算不出接片首帧指纹",
        }
    dist = verifymod.hamming(a, b)
    if dist > verifymod.DIST_SOFT:
        return {"stale": True, "dist": dist, "why": "stale_tail", "log": ""}
    return {"stale": False, "dist": dist, "why": "ok", "log": ""}


def cascade_cost_of(m: Manifest, src_sg: dict) -> list[int]:
    """重生这段主片会让哪些已收接片一起重拍。走到下一个主片为止。

    给 ⑤ 收片卡片用：点重生之前就把代价写在卡上。
    """
    out = []
    cur = src_sg
    while True:
        nxt = next_of(m, cur)
        if not nxt or nxt.get("role") != "bridge":
            break
        if nxt.get("status") in ("collected", "submitted"):
            out.append(int(nxt["s"]))
        cur = nxt
    return out


def _cascade_downstream(task_dir, m: Manifest, first_reset: dict, src_sg: dict,
                        log=None) -> list[int]:
    """被打回的接片后面如果还有接片，一直打到下一个主片为止。

    这段自己的尾帧已经作废，下游不能再留着用它拍的片子。
    """
    out = []
    cur = first_reset
    src_s = int(src_sg.get("s") or 0)
    while True:
        fol = next_of(m, cur)
        if not fol or fol.get("role") != "bridge":
            break
        if fol.get("status") in ("collected", "submitted", "failed", "blocked"):
            _reset_seg_planned(
                task_dir, m, fol, log=log,
                why="上游重生，尾帧已换", stale_from=src_s)
            out.append(int(fol["s"]))
        cur = fol
    return out


def cascade_after_anchor(task_dir, m: Manifest, src_sg: dict, log=None) -> dict:
    """重生主片 / 换下一锚点图之后：先看接片是不是拿现在这张尾帧拍的，再看跨度。

    血缘不对就打回，跨度小不再当免死金牌。被打回的接片若后面还跟着接片，
    沿链打到下一个主片（主片自带 HD 图，不依赖上游尾帧）。

    Grok 接片刚收时先走首帧自检：对不上就重生一次（action=reset 自己），
    再不对留下并标黄，不卡链。Flow 接片不走那条。

    返回 {action: keep|reset|none|ready|wait, s, gap, reason?, reset?, dist?}。
    """
    def _log(msg: str) -> None:
        if callable(log):
            try:
                log(msg)
            except Exception:
                pass

    # 尾帧接龙：用户说了算，不自动级联。只给下一段打黄旗。
    if is_chain_mode(m):
        return mark_chain_upstream_changed(m, src_sg, log=log)

    # Grok 接片收片：首帧自检。reset 自己时 generate 会把它重新推进队列。
    # keep = 对上了或已标黄留下；都不再拿本段当主片去打回下一段。
    if src_sg.get("role") == "bridge" and _is_grok_engine(m, src_sg):
        chk = check_grok_bridge_after_collect(task_dir, m, src_sg, log=log)
        if chk.get("action") in ("reset", "keep"):
            return chk

    nxt = next_of(m, src_sg)
    if not nxt or nxt.get("role") != "bridge":
        return {"action": "none", "s": None, "gap": None}
    g = gap_of(nxt)
    ready = bool(src_sg.get("bridge_ready"))
    src_s = int(src_sg.get("s") or 0)

    def _log_chain(reset_ss, tag: str) -> None:
        if not reset_ss:
            return
        labs = "、".join(_lab(s) for s in reset_ss)
        _log("  · 重生 %s → 打回 %s（%s）" % (_lab(src_s), labs, tag))

    # 陈旧检查插在 keep 之前：问的是「这条接片是不是拿现在这张尾帧拍的」，
    # 不是「新尾帧和下一张锚点图的跨度大不大」。
    # advise 下跨度条件不生效（防不好看改成事后复查）；指纹检查仍生效。
    gap_ok = g is not None and g <= GAP_ENQUEUE
    keep_ok = (
        nxt.get("status") == "collected" and ready
        and (gate_mode_of(m, nxt) == "advise" or gap_ok)
    )
    if keep_ok:
        chk = stale_tail_check(nxt)
        if chk["stale"]:
            dist = chk.get("dist")
            if chk["why"] in ("missing_tail", "missing_frame"):
                why = "上游重生，%s" % chk["log"]
                _log("  · %s %s，打回重拍" % (_lab(nxt["s"]), chk["log"]))
                tag = chk["log"]
            else:
                why = "上游重生，尾帧已换（首帧距 %s）" % (
                    dist if dist is not None else "?")
                tag = "尾帧已换"
            _reset_seg_planned(
                task_dir, m, nxt, log=log, why=why, stale_from=src_s)
            reset_ss = [int(nxt["s"])]
            reset_ss.extend(_cascade_downstream(
                task_dir, m, nxt, src_sg, log=log))
            _log_chain(reset_ss, tag)
            return {
                "action": "reset", "reason": "stale_tail",
                "s": int(nxt["s"]), "gap": g, "reset": reset_ss, "dist": dist,
            }
        _log("  · %s 上游换了尾帧但几乎没变（距 %s），不动" % (
            _lab(nxt["s"]), chk.get("dist")))
        return {"action": "keep", "s": int(nxt["s"]), "gap": g,
                "dist": chk.get("dist")}
    if nxt.get("status") in ("planned", "failed") and ready:
        return {"action": "ready", "s": int(nxt["s"]), "gap": g}
    if nxt.get("status") in ("collected", "submitted", "failed", "blocked"):
        why = "跨度 %s" % (g if g is not None else "未知")
        _reset_seg_planned(task_dir, m, nxt, log=log, why=why)
        reset_ss = [int(nxt["s"])]
        reset_ss.extend(_cascade_downstream(
            task_dir, m, nxt, src_sg, log=log))
        if len(reset_ss) > 1:
            _log_chain(reset_ss, why)
        return {"action": "reset", "s": int(nxt["s"]), "gap": g,
                "reset": reset_ss}
    return {"action": "wait", "s": int(nxt["s"]), "gap": g}


def after_anchor_event(task_dir, m: Manifest, src_sg: dict, log=None,
                       gemini=None, job=None) -> dict:
    """主片刚收 / 刚换图：抽尾帧（若需要）由调用方先做；这里关门 + 联动。"""
    gate = apply_gap_gate(task_dir, m, src_sg, log=log, gemini=gemini, job=job)
    cas = cascade_after_anchor(task_dir, m, src_sg, log=log)
    stamp_waits(m)
    return {"gate": gate, "cascade": cas}


def on_anchor_image_changed(task_dir, m: Manifest, dest_s: int, log=None,
                            gemini=None, job=None) -> dict | None:
    """s005 换了锚点图 → 重算 s004 跨度，规则同重生主片。"""
    dest = m.seg(int(dest_s))
    if not dest:
        return None
    prev = prev_of(m, dest)
    if not prev or prev.get("role") != "bridge":
        return None
    src = prev_of(m, prev)  # 主片
    _rescore_bridge(task_dir, m, prev, gemini=gemini, job=job)
    if src:
        apply_gap_gate(task_dir, m, src, log=log, gemini=gemini, job=job)
        cas = cascade_after_anchor(task_dir, m, src, log=log)
    else:
        cas = {"action": "none"}
    stamp_waits(m)
    return {"bridge": int(prev["s"]), "cascade": cas, "gap": gap_of(prev)}


def _pose_text(pose) -> str:
    if isinstance(pose, dict):
        t = str(pose.get("text") or "").strip()
        if t:
            return t
        bits = [str(pose.get(k) or "").strip()
                for k in ("head", "eyes", "hands", "body", "expr")]
        return "，".join(b for b in bits if b) or "（姿态未写）"
    return str(pose or "").strip() or "（姿态未写）"


def bridge_template() -> str:
    try:
        from . import prompts
        raw = prompts.get_block("bridge")
        if isinstance(raw, str) and raw.strip():
            return raw
    except Exception:
        pass
    return BRIDGE_PROMPT_DEFAULT


def bridge_single_template() -> str:
    try:
        from . import prompts
        raw = prompts.get_block("bridge_single")
        if isinstance(raw, str) and raw.strip():
            return raw
    except Exception:
        pass
    return BRIDGE_PROMPT_SINGLE


def chain_template() -> str:
    try:
        from . import prompts
        raw = prompts.get_block("chain")
        if isinstance(raw, str) and raw.strip():
            return raw
    except Exception:
        pass
    return CHAIN_PROMPT_DEFAULT


def wrap_chain_prompt(m: Manifest, sg: dict, body: str) -> str:
    """接龙段：延续句 + 常规成品提示词。不提第二张、不提结束画面。"""
    ch = sg.get("chain") if isinstance(sg.get("chain"), dict) else {}
    start = _pose_text(ch.get("tail_pose"))
    if start in ("", "（姿态未写）"):
        desc = str(ch.get("desc") or "").strip()
        if desc:
            start = desc.split("。")[0].strip() or desc[:40]
        if not start:
            start = "当前画面"
    tpl = chain_template()
    try:
        head = tpl.format(start=start)
    except Exception:
        head = tpl.replace("{start}", start)
    body = (body or "").strip()
    return (head.rstrip() + "\n" + body) if body else head


def wrap_bridge_prompt(m: Manifest, sg: dict, body: str) -> str:
    """在常规成品提示词前面拼过渡句。台词已经在 body 里。

    Grok 单图用 BRIDGE_PROMPT_SINGLE（不提结束画面）；Flow / Grok 双图用三句。
    """
    br = sg.get("bridge") if isinstance(sg.get("bridge"), dict) else {}
    start = _pose_text(br.get("tail_pose"))
    nxt = next_of(m, sg)
    to_p = (nxt or {}).get("image") or br.get("to_image") or ""
    dest_im = m.image(to_p) if to_p else None
    end = _pose_text((dest_im or {}).get("pose"))
    if grok_bridge_is_single(m, sg):
        tpl = bridge_single_template()
    elif _is_grok_engine(m, sg) and GROK_BRIDGE_MODE == "dual" and GROK_BRIDGE_FLIP_PROMPT:
        tpl = BRIDGE_PROMPT_FLIPPED
    else:
        tpl = bridge_template()
    try:
        head = tpl.format(start=start, end=end)
    except Exception:
        head = tpl.replace("{start}", start).replace("{end}", end)
    body = (body or "").strip()
    return (head.rstrip() + "\n" + body) if body else head


def final_prompt(m: Manifest, sg: dict) -> str:
    """成品提示词。手写锁定的原样；接片自动在常规词前加过渡三句。"""
    if sg.get("prompt_locked") and sg.get("prompt_final"):
        return sg["prompt_final"]
    from . import prompts
    work = dict(sg)
    if is_anchor_mode(m) and sg.get("role") == "bridge":
        nxt = next_of(m, sg)
        if nxt and nxt.get("image"):
            work["image"] = nxt["image"]
    body = (sg.get("prompt_final") or "").strip()
    if is_chain_mode(m) and not sg.get("prompt_locked"):
        ch = sg.get("chain") if isinstance(sg.get("chain"), dict) else {}
        desc = ch.get("desc")
        # 规格 95：接龙每次现拼。上一轮存下的 prompt_final 已经带了延续句，
        # 再拿它当 body 会拼出两个头；描述语也可能沿用 / 重写过了。
        body = prompts.build_final_prompt(m, work, desc_override=desc)
        if is_chain_restart(sg):
            return body
        return wrap_chain_prompt(m, sg, body)
    if not body or (is_anchor_mode(m) and sg.get("role") == "bridge"
                    and not sg.get("prompt_locked")):
        body = prompts.build_final_prompt(m, work)
    if is_anchor_mode(m) and sg.get("role") == "bridge" and not sg.get("prompt_locked"):
        return wrap_bridge_prompt(m, sg, body)
    return body


def _copy_jpeg(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = Path(src)
    if src.suffix.lower() in (".jpg", ".jpeg"):
        shutil.copy2(src, dest)
        return dest
    from PIL import Image
    im = Image.open(src).convert("RGB")
    im.save(dest, "JPEG", quality=95)
    return dest


def _bridge_tmp(task_dir, sg: dict) -> Path:
    tmp = Path(task_dir) / BRIDGE_TMP_REL / _lab(sg.get("s"))
    if tmp.is_dir():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    return tmp


def _bridge_start_src(task_dir, sg: dict) -> Path:
    br = sg.get("bridge") if isinstance(sg.get("bridge"), dict) else {}
    rel = br.get("tail_frame")
    if not rel:
        raise OranteError("%s 还没有尾帧" % _lab(sg.get("s")), "等上一段收片后再提接片。")
    start_src = Path(task_dir) / rel
    if not start_src.is_file():
        raise OranteError("%s 尾帧文件不在" % _lab(sg.get("s")), str(start_src))
    return start_src


def stage_start_from(task_dir, sg: dict, src: Path) -> tuple[Path, None, Path]:
    """把任意一张参考图写成 clips/_tmp/sNNN/01_首帧_start.jpg。接龙 / Grok 单图共用。"""
    tmp = _bridge_tmp(task_dir, sg)
    p1 = _copy_jpeg(Path(src), tmp / "01_首帧_start.jpg")
    return p1, None, tmp


def stage_bridge_start(task_dir, m: Manifest, sg: dict) -> tuple[Path, None, Path]:
    """只写 clips/_tmp/s004/01_首帧_start.jpg。Grok 单图接片用。返回 (start, None, tmp)。"""
    start_src = _bridge_start_src(task_dir, sg)
    return stage_start_from(task_dir, sg, start_src)


def stage_chain_start(task_dir, m: Manifest, sg: dict) -> tuple[Path, None, Path]:
    """接龙段：参考图（起头图或上一段尾帧）写成 01_首帧_start.jpg。"""
    ch = apply_chain_ref(m, task_dir, sg)
    rel = (ch.get("ref_file") or "").replace("\\", "/")
    if not rel:
        raise OranteError("%s 还没有参考图" % _lab(sg.get("s")), "先选起头图或等上一段收片。")
    src = Path(task_dir) / rel
    if not src.is_file():
        raise OranteError("%s 参考图不在" % _lab(sg.get("s")), str(src))
    return stage_start_from(task_dir, sg, src)


def stage_bridge_images(task_dir, m: Manifest, sg: dict) -> tuple[Path, Path, Path]:
    """写成 clips/_tmp/s004/01_首帧_start.jpg + 02_尾帧_end.jpg。返回 (start, end, tmpdir)。"""
    start_src = _bridge_start_src(task_dir, sg)
    nxt = next_of(m, sg)
    br = sg.get("bridge") if isinstance(sg.get("bridge"), dict) else {}
    to_p = (nxt or {}).get("image") or br.get("to_image") or ""
    end_src = _image_file(task_dir, m, to_p)
    if not end_src:
        raise OranteError("%s 的目标锚点图不在" % _lab(sg.get("s")), to_p or "—")
    tmp = _bridge_tmp(task_dir, sg)
    p1 = _copy_jpeg(start_src, tmp / "01_首帧_start.jpg")
    p2 = _copy_jpeg(end_src, tmp / "02_尾帧_end.jpg")
    return p1, p2, tmp


def cleanup_bridge_tmp(task_dir, s) -> None:
    tmp = Path(task_dir) / BRIDGE_TMP_REL / _lab(s)
    shutil.rmtree(tmp, ignore_errors=True)
    parent = Path(task_dir) / BRIDGE_TMP_REL
    try:
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass


def cell_status(m: Manifest, sg: dict) -> str:
    if is_waiting(m, sg):
        return "waiting"
    return sg.get("status") or "planned"


def sync_bench_from_disk(m: Manifest, task_dir) -> None:
    """检查台可能刚换锚点 / 放行：把盘上的 bridge 字段读回正在跑的 generate 内存对象。

    不碰本轮已经 submitted/collected 的 status / takes / gen_id。
    """
    try:
        fresh = Manifest.load(Path(task_dir))
    except Exception:
        return
    keys = ("passed", "gap_score", "gap_why", "to_image", "auto_swapped_from",
            "tail_frame", "tail_pose", "tail_checks", "bench_acted",
            "tail_from_end", "tail_sharp", "tail_warn", "first_frame",
            "mismatch", "mismatch_why", "mismatch_retries",
            "mismatch_first_dist", "mismatch_last_dist", "mismatch_gap")
    for sg in m.segments or []:
        other = fresh.seg(int(sg.get("s") or 0))
        if not other:
            continue
        if other.get("bridge_ready") is not None:
            sg["bridge_ready"] = other.get("bridge_ready")
        obr = other.get("bridge") if isinstance(other.get("bridge"), dict) else None
        if obr:
            br = sg.get("bridge") if isinstance(sg.get("bridge"), dict) else {}
            for k in keys:
                if k in obr:
                    br[k] = obr[k]
            sg["bridge"] = br
        if other.get("prompt_locked"):
            sg["prompt_locked"] = True
            if other.get("prompt_final"):
                sg["prompt_final"] = other.get("prompt_final")
        # 检查台换了下一锚点的图：还没提交的段跟着改
        if (other.get("image") and other.get("image") != sg.get("image")
                and sg.get("status") not in ("submitted", "collected")):
            sg["image"] = other.get("image")
