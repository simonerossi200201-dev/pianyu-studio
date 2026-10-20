# -*- coding: utf-8 -*-
"""kind=generate：把一个任务里所有 planned 段提交给它的账号，轮询、下载、记状态。占账号。

流程（对 Flow / Grok 一样）：
  连引擎 → 逐段：算成品提示词 → engine.submit → 段=submitted(gen_id)
        → 有空位就继续提交，没空位就轮询 → done 的下载改名 → 段=collected
        → blocked 的段记 blocked，当场 expand.refill_one 换图再塞回队列
  每一步落 manifest；取消后已提交的段下次自动续跑（submitted 的先轮询再说）。
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime
from pathlib import Path

from .. import tasks as taskmod, accounts, events, config
from ..jobs import register
from ..models import Manifest
from ..errors import OranteError, NeedRedownload, CreditHalted, FlowThrottled
from ..engines.base import make_engine, BlockedImage, EngineUnavailable
from ..engines.flow_api import notify_credit_halt, wait_credit_continue, format_flow_raw
from . import verify as verifymod
from . import pipeline

POLL_EVERY = 6          # 秒
GEN_TIMEOUT = 15 * 60   # 新提交的段最长等多久
ADOPT_TIMEOUT = 5 * 60  # 续跑接手的段：已经等过一轮，绳子更短
VOID_STOP = 6           # 同一个 gid 本 Job 内作废这么多次 → 无条件 failed
MAX_AUTO_REFILL = 3     # 一张图自动换图最多几次


def is_flow_upload_refused(msg) -> bool:
    """这张图是不是在**上传**这一步就被 Flow 拒了（不是生成时被审核拦下）。
    网页真人操作（"Failed to upload …，连传 2 次都没收"）和接口直连（"缩小后仍被拒……"）
    两条路上传被拒时，引擎抛的 BlockedImage 都以「Flow 拒收了」开头。"""
    return "Flow 拒收了" in str(msg or "")


def flow_refused_text(p: str) -> str:
    """段和图上显示的"被拒原因"：说人话，并且直接告诉该怎么办。"""
    return (f"Flow 不收这张图（{p} 一上传就被拒，Flow 网页手动上传也一样）。"
            f"这张图在 Flow 上用不了：把这个任务换到 Grok 等别的引擎出片，或者换一张图")
# 一个任务里最多换掉几张图。0908：Grok 的「被拒」判断误报，一整轮把 p19/p09/p05/
# p04/p14 挨个换掉、翻转、覆盖，原图全没了——每张图各自的 3 次上限拦不住这种
# 系统性误判。所以再加一道总闸：连着换这么多张还在被拒，说明八成是判断错了，
# 不是图有问题，停下来让人看一眼。
MAX_AUTO_REFILL_TASK = 4
ROUND_MAX = 5                 # 接力总轮数，不是无限循环
RELAY_BACKOFF = (30, 60, 120)  # 每轮之间退避，Flow 拒图有时是瞬时的
STALL_STOP = 2                # 连续这么多轮零进展就停


_IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp")


_ORPHAN_RE = re.compile(r"跳到 post 页[^\n]*?conversation=([0-9a-fA-F-]{36})|跳到 post 页[^\n]*?n=([0-9a-fA-F-]{36})")


def orphan_conv_of(seg_or_msg) -> str:
    """「Grok 提交失败：这一页已经跳到 post 页（…n=<会话>）」= 其实交上去了（T0124 丢了 11 条）。
    取出那个会话 Id；不是这种失败返回 ""。"""
    if isinstance(seg_or_msg, dict):
        c = str(seg_or_msg.get("orphan_conv") or "")
        if c:
            return c
        msg = str(seg_or_msg.get("error") or "")
    else:
        msg = str(seg_or_msg or "")
    m = _ORPHAN_RE.search(msg)
    if not m:
        return ""
    return (m.group(1) or m.group(2) or "").lower()


# 下载时网络抖一下（连接被重置 / 超时）不算这一段失败：地址还在，过一会儿再下
_DL_TRANSIENT = ("ECONNRESET", "ETIMEDOUT", "ECONNREFUSED", "EPIPE", "socket hang up",
                 "net::ERR_", "Timeout", "timed out", "Connection reset", "Remote end closed")
DL_RETRY_MAX = 3

# 整台电脑断网（T0124 12:53：35 段铺页全报 ERR_INTERNET_DISCONNECTED）：不算这些段失败，等网回来
_NET_DOWN = ("ERR_INTERNET_DISCONNECTED", "ERR_NAME_NOT_RESOLVED", "ERR_NETWORK_CHANGED",
             "ERR_NETWORK_IO_SUSPENDED", "ERR_ADDRESS_UNREACHABLE", "ERR_PROXY_CONNECTION_FAILED")
NET_DOWN_WAIT = 20          # 秒：断网时每次等多久再试
NET_DOWN_MAX = 30           # 连着这么多次还没网（约 10 分钟）就停下，段留着待做


def is_net_down(msg: str) -> bool:
    m = str(msg or "")
    return any(k in m for k in _NET_DOWN)


def is_transient_dl_error(msg: str) -> bool:
    m = str(msg or "")
    return any(k.lower() in m.lower() for k in _DL_TRANSIENT)


def _clip_dl_error(msg: str) -> str:
    """下载失败文案只加一次「下载失败：」前缀。"""
    msg = (msg or "").strip() or "下载失败"
    if msg.startswith("下载失败"):
        return msg
    return "下载失败：" + msg


def apply_timeout(seg: dict, *, adopted: bool = False, elapsed: float | None = None) -> bool:
    """超时一次。连续 3 次才停；前两次 status=failed 但仍能进队重试。

    返回 True 表示已经满 3 次、wait_reason 会挡。
    续跑接手的段文案要写明已经累计等多久，别再让人以为是新的 15 分钟。
    """
    n = int(seg.get("timeout_count") or 0) + 1
    seg["timeout_count"] = n
    seg["fail_count"] = int(seg.get("fail_count") or 0) + 1
    seg["status"] = "failed"
    if adopted:
        mins = max(1, int((elapsed or 0) // 60))
        seg["error"] = f"等了太久没出片（续跑接手，已累计 {mins} 分钟）"
        return n >= pipeline.TIMEOUT_STOP
    if n >= pipeline.TIMEOUT_STOP:
        seg["error"] = "等了太久没出片 ×3"
        return True
    seg["error"] = "等了太久没出片"
    return False


def parse_health_ts(s) -> float | None:
    """seg['gen_health']['first_bad_at'] → epoch 秒。解析不了就当没有。"""
    if not s:
        return None
    raw = str(s).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
        return dt.timestamp()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw[:19], fmt).timestamp()
        except ValueError:
            continue
    return None


def wait_origin(seg: dict, started_at: float, *, adopted: bool) -> float:
    """超时和『已等』的起算时刻。续跑且有 first_bad_at 就从那一刻算，不从接手算。"""
    if adopted:
        gh = seg.get("gen_health") if isinstance(seg.get("gen_health"), dict) else None
        t = parse_health_ts((gh or {}).get("first_bad_at"))
        if t:
            return t
    return started_at


def gen_deadline(*, adopted: bool) -> float:
    return ADOPT_TIMEOUT if adopted else GEN_TIMEOUT


def gen_timed_out(seg: dict, *, adopted: bool, started_at: float,
                  now: float | None = None, deadline: float | None = None) -> tuple[bool, float]:
    """这段是否该按超时失败。返回 (到期?, 已等秒数)。

    deadline：引擎自己给的时限（本地模型按出片模式和秒数算，精品模式一条要十几分钟），没有就用默认。"""
    now = time.time() if now is None else now
    t0 = wait_origin(seg, started_at, adopted=adopted)
    elapsed = now - t0
    limit = float(deadline) if deadline else gen_deadline(adopted=adopted)
    return elapsed > limit, elapsed


def fmt_wait(sec: float) -> str:
    """4 分 12 秒。"""
    sec = max(0, int(sec))
    m, s = divmod(sec, 60)
    return f"{m} 分 {s} 秒"


def tick_summary(prev_key, key, n: int) -> tuple[bool, object, int]:
    """汇总行：内容变了立刻打；没变时第 5/10/15… 轮打。

    n 是调用前已过的轮数。返回 (该打?, 新 prev_key, n+1)。
    """
    n += 1
    changed = key != prev_key
    return (changed or n % 5 == 0), key, n


def inflight_summary_line(inflight: dict, queue_n: int, n_done: int,
                          started: dict, adopted: set, now: float | None = None) -> str:
    """… 在生成 1 条（s001，已等 4 分 12 秒），待提交 0，已收 9"""
    now = time.time() if now is None else now
    bits = []
    for gid, sg in inflight.items():
        try:
            tag = f"s{int(sg.get('s') or 0):03d}"
        except (TypeError, ValueError):
            tag = str(sg.get("s") or gid)[:8]
        t0 = wait_origin(sg, started.get(gid, now), adopted=(gid in adopted))
        bits.append(f"{tag}，已等 {fmt_wait(now - t0)}")
    extra = "（" + "；".join(bits) + "）" if bits else ""
    return f"  … 在生成 {len(inflight)} 条{extra}，待提交 {queue_n}，已收 {n_done}"


def _resolve_image(d: Path, im: dict) -> Path:
    """按 manifest 里记的 file 找图；找不到就在同目录按文件名主干匹配任意图片扩展名。

    用户从 _弃用/ 拖回来时经常 .jpeg ↔ .jpg 对不上，这里兜底并把 file 字段改成实际的。
    """
    rel = (im.get("file") or "").replace("\\", "/")
    img = d / rel
    if img.is_file() or not rel:
        return img
    stem = img.stem.lower()
    for cand in sorted(img.parent.glob("*")):
        if cand.is_file() and cand.stem.lower() == stem and cand.suffix.lower() in _IMG_EXTS:
            im["file"] = f"{img.parent.relative_to(d).as_posix()}/{cand.name}"
            return cand
    return img


def _prompt_snap_key(m: Manifest, seg: dict) -> str:
    """提示词定稿时对应的图。图换了 / 描述语改了就不算数，要重拼。"""
    p = seg.get("image") or ""
    im = m.image(p) or {}
    return "|".join([p, str(im.get("file") or ""), str(im.get("desc") or "")[:120]])


def _seg_rev(seg: dict):
    """段上记的提示词世代。没记过返回 None——别用 `or -1`，
    那样第 0 代（全新装机还没改过提示词）会被误判成"没记过"，每轮都白重拼一次。"""
    v = (seg or {}).get("prompt_rev")
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def prompt_rev() -> int:
    """当前提示词世代（积木开关 / 极简 / 可编辑块 / 套装 / 语种包 一改就 +1）。"""
    try:
        from . import prompts as _pm
        return int(_pm.rev())
    except Exception:  # noqa: BLE001
        return 0


def snapshot_prompts(job, m: Manifest, segs: list, *, save=None, rev0=None) -> int:
    """开跑前把这一轮要交的段的提示词先定下来（按当前套装）。

    切套装不再拦生成（两个窗口要能各切各的），所以提示词必须在开跑时定稿，
    免得跑到一半被另一个窗口换掉的提示词影响后面几段。
    接片 / 接龙每段要看前一段的尾帧，只能现拼，这里不碰。
    """
    n = 0
    rev0 = prompt_rev() if rev0 is None else int(rev0)
    for sg in segs:
        if sg.get("prompt_locked"):
            continue
        if sg.get("prompt_final") and _seg_rev(sg) == rev0:
            continue                      # 已经是这一代拼好的，照用
        try:
            sg["prompt_final"] = _final_prompt(m, sg, rev0=rev0)
        except Exception:  # noqa: BLE001  缺图缺描述语等，留给提交时原样报
            continue
        sg["prompt_snap"] = _prompt_snap_key(m, sg)
        sg["prompt_rev"] = rev0
        n += 1
    if n and callable(save):
        save()
    if n:
        job.log(f"  · 提示词已按当前套装定下 {n} 段（这一轮不受切套装影响）")
    return n


def _final_prompt(m: Manifest, seg: dict, *, rev0: int | None = None) -> str:
    if ((pipeline.is_anchor_mode(m) or pipeline.is_chain_mode(m))
            and not seg.get("prompt_locked")):
        # 接片 / 接龙每次现拼；手写锁定的走下面那条
        return pipeline.final_prompt(m, seg)
    if not seg.get("prompt_locked"):
        snap = seg.get("prompt_snap")
        if snap is not None and snap != _prompt_snap_key(m, seg):
            # 定稿之后图换了（补图 / 借图 / 换了描述语）→ 按新图重拼
            seg["prompt_final"] = ""
            seg.pop("prompt_snap", None)
        # 规格 93：提示词本身改了（极简 / 积木开关 / 套装 / 语种包）→ 也要重拼。
        # 比的是"开跑那一刻的世代"，不是当下的——这一轮跑到一半别人换了套装，
        # 已经开跑的这轮不受影响（规格 91 的快照就是干这个的）。
        want = prompt_rev() if rev0 is None else int(rev0)
        if seg.get("prompt_final") and _seg_rev(seg) != want:
            seg["prompt_final"] = ""
            seg.pop("prompt_snap", None)
    if seg.get("prompt_final"):
        return seg["prompt_final"]
    try:
        from . import prompts
        return prompts.build_final_prompt(m, seg)
    except ImportError:
        raise OranteError("提示词服务还没就位（services/prompts.py）", "见 specs/04")


def _never_collected(m: Manifest, p: str) -> bool:
    """这张图有没有过任何一段成功收片（含 takes 里留着旧片的）。"""
    for sg in m.segments:
        if sg.get("image") != p:
            continue
        if sg.get("status") == "collected" or (sg.get("takes") or []):
            return False
    return True


def _gen_fail_of(m: Manifest, p: str) -> int:
    """图上的失败次数：manifest 记的 gen_fail，冷启动用当前 failed/blocked 段数兜底。

    不算 planned 段上残留的 fail_count——换图打回 planned 后那些是旧账，
    不然会刚补完又立刻再标 blocked。
    """
    im = m.image(p) or {}
    stored = int(im.get("gen_fail") or 0)
    n_failed = sum(
        1 for sg in m.segments
        if sg.get("image") == p
        and sg.get("status") in ("failed", "blocked")
        and not (sg.get("takes") or []))
    return max(stored, n_failed)


def _submit_duration(seg: dict, eng) -> int:
    """交给引擎的时长：段本来的时长；这个引擎没有这一档（重生临时换了引擎）就换成它最接近的一档。"""
    d0 = int(seg.get("duration") or 8)
    name = (getattr(eng, "name", "") or "").strip().lower()
    try:
        from ..engines import caps
        if name in caps.DURATIONS:
            return int(caps.snap_duration(name, d0))
    except Exception:
        pass
    return d0


def _eng_label(eng) -> str:
    name = (getattr(eng, "name", "") or "").strip().lower()
    return {"flow": "Flow", "grok": "Grok", "studio": "本地模型"}.get(name, name or "这个引擎")


_CONTENT_RE = re.compile(r"PUBLIC_ERROR_\w*(FILTER|POLICY|PROMINENT|PERSON|MINOR|UNSAFE|SAFETY)", re.I)


def is_content_refusal(err) -> bool:
    """这次失败是不是"Flow 因为图 / 内容不收"（规格 98：只有这种才转交本地模型）。

    算：上传被拒；错误里带"被拒""拒收"；PUBLIC_ERROR_*FILTER/POLICY/PROMINENT/PERSON/MINOR/UNSAFE/SAFETY。
    不算：AUDIO_FILTERED（声音的问题）、UNUSUAL_ACTIVITY（限流）、超时、网络、真人操作没点上、没理由的失败。"""
    s = str(err or "")
    if not s:
        return False
    up = s.upper()
    if "AUDIO_FILTER" in up or "UNUSUAL_ACTIVITY" in up:
        return False
    if is_flow_upload_refused(s) or "被拒" in s or "拒收" in s:
        return True
    return bool(_CONTENT_RE.search(s))


def studio_fallback_on() -> bool:
    """Flow 拒图后自动转交本地模型：优先开关开着即生效（若本地模型未启用则自动开启）。"""
    if not bool(config.get("engine.studio.fallback", True)):
        return False
    if not bool(config.get("engine.studio.enabled", True)):
        config.set("engine.studio.enabled", True, touch_rev=False)
    return True


def _blocked_for_flow(sg: dict) -> bool:
    """算进"仍被拒、要 Flow 接力 / 补图"的段：blocked 且没排给本地模型。"""
    return sg.get("status") == "blocked" and not sg.get("studio_pending")


def _keeps_image(eng) -> bool:
    """这个引擎没有 Flow 那种拒图：失败不换图，用原图再交一次（Grok、本地模型 Studio Zero）。"""
    return (getattr(eng, "name", "") or "") in ("grok", "studio")


def _is_grok(eng) -> bool:
    """出片引擎是不是 Grok。Grok 没有 Flow 那种拒图，失败不能走换图。"""
    return (getattr(eng, "name", "") or "") == "grok"


def _grok_resubmit(seg: dict, err: str, job, queue: list) -> bool:
    """Grok 没收到片子：原图最多再交一次。不标被拒、不换图。已再交过则返回 False。"""
    n = int(seg.get("grok_retry") or 0)
    if n >= 1:
        return False
    seg["grok_retry"] = 1
    seg["status"] = "planned"
    seg["gen_id"] = None
    seg["error"] = None
    seg.pop("gen_health", None)
    job.log(f"  ↻ s{seg['s']:03d} Grok 没收到片子（{err}），用原图再交一次，不换图")
    queued = {int(sg.get("s") or 0) for sg in queue}
    if int(seg.get("s") or 0) not in queued:
        queue.append(seg)
    return True


def _unblock_grok_image(m: Manifest, p: str, keep_refused: bool = False) -> None:
    """Grok / 本地模型出片时把 blocked 收回来，原图继续用。

    keep_refused=True（本地模型）：保留 flow_upload_refused——以后 Flow 再碰到这张图，
    直接知道它传不上去，不再白传一次。"""
    im = m.image(p) if p else None
    if not im or im.get("status") != "blocked":
        return
    im["status"] = "ok"
    im.pop("block_reason", None)
    if not keep_refused:
        im.pop("flow_upload_refused", None)   # 换到 Grok 出片：Flow 不收这张图的事跟 Grok 无关


def _block_image(m: Manifest, p: str, err: str) -> list:
    """图标 blocked，所有未完成（非 collected/submitted）段一并 blocked。"""
    im = m.image(p)
    if im:
        im["status"] = "blocked"
        if err:
            im["block_reason"] = str(err)[:400]
    segs = []
    for sg in m.segments:
        if sg.get("image") != p:
            continue
        if sg.get("status") in ("collected", "submitted"):
            continue
        sg["status"] = "blocked"
        sg["error"] = err
        segs.append(sg)
    return segs


def _fmt_segs(nums) -> str:
    xs = [int(n) for n in nums if n]
    xs.sort()
    return "/".join(f"s{n:03d}" for n in xs) if xs else "（无段）"


def _text_sim(a, b) -> float:
    """很粗的词交集，给借图挑「描述语最接近」用。没有词就 0。"""
    tok = re.compile(r"[\w\u4e00-\u9fff]+")
    wa = set(tok.findall((a or "").lower()))
    wb = set(tok.findall((b or "").lower()))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _neighbor_ps(m: Manifest, sg: dict) -> set[str]:
    segs = m.segments or []
    out: set[str] = set()
    try:
        i = next(n for n, x in enumerate(segs) if int(x.get("s") or 0) == int(sg.get("s") or 0))
    except StopIteration:
        return out
    for j in (i - 1, i + 1):
        if 0 <= j < len(segs):
            p = segs[j].get("image") or ""
            if p:
                out.add(p)
    return out


def _success_usage(m: Manifest) -> dict[str, int]:
    """已经成功出过片的图 → 被用了几次（含 planned 占用）。"""
    used: dict[str, int] = {}
    ok: set[str] = set()
    for sg in m.segments:
        p = sg.get("image") or ""
        if not p:
            continue
        if sg.get("status") == "collected" or (sg.get("takes") or []):
            ok.add(p)
        used[p] = used.get(p, 0) + 1
    return {p: used.get(p, 0) for p in ok}


def borrow_image(m: Manifest, d: Path, sg: dict) -> str | None:
    """从本任务已经成功出过片的图里借一张给这段。不改 images/ 文件。

    优先：① 和本段描述语最接近；② 不和相邻段同一张；③ 用得最少。
    成功返回图号，失败返回 None。调用方负责把段打回 planned。
    """
    if not sg:
        return None
    usage = _success_usage(m)
    cur = sg.get("image") or ""
    usage.pop(cur, None)
    if not usage:
        return None
    avoid = _neighbor_ps(m, sg)
    im_cur = m.image(cur) or {}
    needle = str(im_cur.get("desc") or sg.get("text") or "")
    ranked = []
    for p, n in usage.items():
        im = m.image(p) or {}
        if im.get("role") == "空镜" or im.get("status") == "blocked":
            continue
        rel = im.get("file") or ""
        if rel and not (d / rel).is_file():
            continue
        sim = _text_sim(needle, str(im.get("desc") or "")) if needle else 0.0
        adj = 1 if p in avoid else 0
        ranked.append((adj, -sim, n, p))
    if not ranked:
        return None
    ranked.sort()
    pick = ranked[0][3]
    orig = cur
    pipeline.pin_image(m, int(sg.get("s") or 0), pick)
    sg["borrowed"] = True
    sg["borrowed_from"] = pick
    sg["borrowed_orig"] = orig
    sg["status"] = "planned"
    sg["error"] = None
    sg["gen_id"] = None
    return pick


def blocked_rows(m: Manifest) -> list[dict]:
    """④ 徽章点开用：哪几段、哪几张图、试了几次、最后拒绝原因。"""
    out = []
    for sg in m.segments or []:
        if sg.get("status") != "blocked":
            continue
        p = sg.get("image") or ""
        im = m.image(p) or {}
        out.append({
            "s": int(sg.get("s") or 0),
            "p": p,
            "tries": int(im.get("auto_refill") or 0),
            "reason": (sg.get("error") or im.get("block_reason") or "")[:400],
            "studio_pending": bool(sg.get("studio_pending")),
        })
    return out


def decide_relay(*, blocked_n: int, collected_n: int,
                 prev_blocked, prev_collected, stalled: int, relay_n: int) -> dict:
    """跑到全部出片：要不要再接力一轮。"""
    if int(blocked_n or 0) <= 0:
        return {"chain": False, "stop": "done", "stalled": 0, "relay": int(relay_n or 0),
                "log": "", "backoff": 0}
    progressed = False
    if prev_blocked is None:
        progressed = True
    else:
        try:
            if int(blocked_n) < int(prev_blocked) or int(collected_n) > int(prev_collected or 0):
                progressed = True
        except (TypeError, ValueError):
            progressed = True
    new_stalled = 0 if progressed else int(stalled or 0) + 1
    if new_stalled >= STALL_STOP:
        return {
            "chain": False, "stop": "stalled", "stalled": new_stalled,
            "relay": int(relay_n or 0), "backoff": 0,
            "log": f"连续 2 轮没有进展，剩 {blocked_n} 段仍被拒，停下来等你看一眼",
        }
    if int(relay_n or 0) >= ROUND_MAX:
        return {
            "chain": False, "stop": "max", "stalled": new_stalled,
            "relay": int(relay_n or 0), "backoff": 0,
            "log": f"已接力 {ROUND_MAX} 轮，剩 {blocked_n} 段仍被拒，停下来等你看一眼",
        }
    nxt = int(relay_n or 0) + 1
    delay = RELAY_BACKOFF[min(nxt - 1, len(RELAY_BACKOFF) - 1)]
    return {
        "chain": True, "stop": "", "stalled": new_stalled, "relay": nxt,
        "backoff": delay,
        "log": f"还有 {blocked_n} 段被拒，{delay}s 后接力第 {nxt}/{ROUND_MAX} 轮",
    }


def _progress_line(tid: str, m: Manifest, relay_n: int) -> str:
    total = len(m.segments or [])
    n_got = sum(1 for sg in m.segments if sg.get("status") == "collected")
    blocked = [sg for sg in m.segments if _blocked_for_flow(sg)]
    n_studio = sum(1 for sg in m.segments if sg.get("studio_pending"))
    ps, seen = [], set()
    for sg in blocked:
        p = sg.get("image") or ""
        if p and p not in seen:
            seen.add(p)
            ps.append(p)
    ptxt = "/".join(ps) if ps else "无"
    extra = f"，已转本地模型 {n_studio}" if n_studio else ""
    return (f"任务 {tid}：{total} 段中已收 {n_got}，"
            f"仍被拒 {len(blocked)}（{ptxt}）{extra}，已接力 {int(relay_n or 0)}/{ROUND_MAX} 轮")


def _enqueue_relay(job, relay_n: int) -> None:
    """后端排队 refill → generate，不靠前端 waitRefill。"""
    from ..jobs import start as jobs_start
    jobs_start("refill", job.task_id)
    jobs_start("generate", job.task_id, redo=True, rtc_round=int(relay_n or 0))


def _finish_round(job, t: dict, m: Manifest, d: Path, rtc: bool, rtc_round: int,
                  chain: bool, n_sub: int, n_done: int, n_fail: int,
                  judged: set, refilled_ok: set, gave_up: set | None = None,
                  extra: str = "") -> None:
    """本轮收尾：整体进度 + 必要时排队下一轮。排给本地模型的段不算被拒（不触发接力）。"""
    blocked = [sg for sg in m.segments if _blocked_for_flow(sg)]
    n_got = sum(1 for sg in m.segments if sg.get("status") == "collected")
    job.log(_progress_line(t["id"], m, rtc_round))
    if not rtc or not chain:
        return
    st = taskmod.rtc_state(t)
    dec = decide_relay(
        blocked_n=len(blocked), collected_n=n_got,
        prev_blocked=st.get("blocked") if st else None,
        prev_collected=st.get("collected") if st else None,
        stalled=int(st.get("stalled") or 0),
        relay_n=int(rtc_round or 0),
    )
    ps = sorted({sg.get("image") or "" for sg in blocked if sg.get("image")})
    taskmod.set_rtc_state(t["id"], {
        "blocked": len(blocked),
        "collected": n_got,
        "stalled": dec.get("stalled") or 0,
        "relay": dec.get("relay") or rtc_round,
        "ps": ps,
    })
    if dec.get("log"):
        if dec.get("stop") == "stalled" and ps:
            job.log(dec["log"] + "：" + "/".join(ps))
        else:
            job.log(dec["log"])
    if not dec.get("chain"):
        return
    delay = float(dec.get("backoff") or 0)
    if delay > 0:
        job.sleep(delay)
    try:
        _enqueue_relay(job, dec.get("relay") or (int(rtc_round or 0) + 1))
    except Exception as e:  # noqa: BLE001
        job.log(f"  · 接力排队失败：{e}")


def retry_blocked(tid: str, s: int, account_id: str | None = None) -> dict:
    """④ 徽章「再试一次」：清掉这张图的换图次数，重跑这一段。

    规格 98：传了别的账号（比如本地模型 STUDIO）就改走「换账号重生」，用原图。"""
    from . import broll
    from ..jobs import start as jobs_start
    taskmod.ensure_writable(tid)
    d, m, _sync = broll.load_synced(tid)
    sg = m.seg(int(s))
    if not sg:
        raise OranteError(f"没有 s{int(s):03d}")
    p = sg.get("image") or ""
    im = m.image(p)
    if im:
        im["auto_refill"] = 0
    m.save(d)
    t = taskmod.get(tid)
    if account_id and account_id != t.get("account_id"):
        jobs_start("regenerate", tid, segs=[int(s)], account_id=account_id)
        return {"s": int(s), "p": p, "account_id": account_id}
    jobs_start("generate", tid, only=[int(s)], redo=True)
    return {"s": int(s), "p": p}


def borrow_seg(tid: str, s: int, run: bool = True) -> dict:
    """④ 徽章「借图出片」。"""
    from . import broll
    from ..jobs import start as jobs_start
    taskmod.ensure_writable(tid)
    d, m, _sync = broll.load_synced(tid)
    sg = m.seg(int(s))
    if not sg:
        raise OranteError(f"没有 s{int(s):03d}")
    orig = sg.get("image") or ""
    bp = borrow_image(m, d, sg)
    if not bp:
        raise OranteError("没有可借的图", "要先有别的段成功出过片")
    m.save(d)
    taskmod.remember_counts(d, m)
    events.emit("tasks.changed")
    if run:
        jobs_start("generate", tid, only=[int(s)])
    return {"s": int(s), "from": orig, "to": bp}


def _throttle_plain(acc: dict, network: bool = False) -> str:
    name = (acc or {}).get("name") or "这个账号"
    if network:
        return ("连着两个 Flow 账号都被限流了。多半是同一个网络在短时间里提交太多，"
                "Google 按网络限的，换账号也没用。建议先暂停 30 分钟到 1 小时。")
    return f"{name} 这个 Flow 账号被 Google 暂时限流了（短时间内提交太多）"


def notify_flow_throttle(job, acc: dict, *, network: bool = False) -> dict:
    """④ 生成顶上的横幅 + SSE。跨窗口靠账号 extra / settings。"""
    extra = dict((acc or {}).get("extra") or {})
    nt = accounts.network_throttle_state()
    network = bool(network or nt.get("on"))
    alts = [] if network else accounts.flow_alts((acc or {}).get("id"))
    payload = {
        "account_id": (acc or {}).get("id") or "",
        "account_name": (acc or {}).get("name") or "",
        "until": extra.get("throttled_until") or "",
        "until_ts": extra.get("throttled_until_ts") or 0,
        "network": network,
        "task_id": getattr(job, "task_id", None),
        "job_id": getattr(job, "id", None),
        "alts": [{"id": x["id"], "name": x["name"]} for x in alts],
        "message": _throttle_plain(acc, network),
        # 规格 91：横幅上要能看见"到底为什么被限"，而不是只有一句"账号限流中"
        "why": accounts.throttle_info(acc) if not network else {},
        "net": {"accounts": list(nt.get("accounts") or []),
                "at": nt.get("at") or "",
                "left_min": nt.get("left_min") or 0} if network else {},
        "probe_min": int(_probe_every() // 60),
    }
    if job is not None:
        job.flow_throttle = payload
    events.emit("flow.throttled", **payload)
    events.emit("accounts.changed")
    return payload


def _requeue_after_throttle(job) -> None:
    """冷却一到调度器会自动接着跑。网络限流时不要再排队。

    单测里的 FakeJob 不在调度器里，别真的再起一条。
    """
    if accounts.network_throttled():
        return
    tid = getattr(job, "task_id", None)
    if not tid:
        return
    try:
        from ..scheduler import scheduler as sched
        if sched.get(getattr(job, "id", "") or "") is None:
            return
        from ..jobs import start as jobs_start
        jobs_start("generate", tid)
    except Exception as e:  # noqa: BLE001
        if job is not None:
            job.log(f"  · 限流后重新排队失败：{e}")


def _unfinished_wanted(m, wanted: set) -> int:
    n = 0
    for sg in m.segments:
        try:
            s = int(sg.get("s") or 0)
        except (TypeError, ValueError):
            continue
        if s not in wanted:
            continue
        if sg.get("status") not in ("collected", "submitted"):
            n += 1
    return n


# ---- 规格 87：同账号拼单 ----------------------------------------------------

PACK_ENGINES = ("grok", "flow")   # 0922：Flow 也拼单（额度核对按账号一次性起算，见 shared.EngineProxy.begin_credits）


def packable_manifest(acc: dict, m) -> bool:
    """这个任务能不能和同账号别的任务一起出片（共用引擎）。

    Grok / Flow 的普通任务可以。尾帧接龙、锚点接片（段之间有先后）一律独占。
    """
    try:
        if (acc or {}).get("engine") not in PACK_ENGINES:
            return False
        if pipeline.is_chain_mode(m) or pipeline.is_anchor_mode(m):
            return False
    except Exception:
        return False
    return True


def pack_demand(tid: str, args: dict | None = None, *, running: bool = False, account_id: str | None = None) -> int | None:
    """这个任务要占几个并发位；不能拼单返回 None。

    running=False（还在排队）：按 generate() 的 todo 算——planned / failed（开「跑到全部出片」时含 blocked）
    再加上已经 submitted 的（续跑接手）。running=True（已在跑）：planned + submitted，失败的这一轮不会再提。
    """
    try:
        t = taskmod.get(tid)
        acc = accounts.get(account_id or t["account_id"])
        d = t.get("dir")
        if not d:
            return None
        m = Manifest.load(Path(d))
    except Exception:
        return None
    if not packable_manifest(acc, m):
        return None
    args = dict(args or {})
    rtc = False
    try:
        rtc = taskmod.rtc_on(t)
    except Exception:
        pass
    only = args.get("only") or args.get("segs")
    only_set = {int(x) for x in only} if only else None
    n = 0
    for sg in m.segments or []:
        try:
            s_no = int(sg.get("s") or 0)
        except (TypeError, ValueError):
            continue
        if only_set is not None and s_no not in only_set:
            if sg.get("status") != "submitted":
                continue
        st = sg.get("status")
        if st == "submitted":
            n += 1
        elif st == "planned":
            n += 1
        elif not running and (st == "failed" or ((args.get("redo") or rtc) and _blocked_for_flow(sg))):
            n += 1
    if n == 0 and args.get("segs"):
        n = max(1, len(args.get("segs") or []))
    return n


_LAST_SUBMIT: dict[str, float] = {}
_LAST_SUBMIT_LOCK = threading.Lock()


def note_submit(acc_id: str) -> None:
    """记下这个账号最后一次真的提交出去的时刻。"""
    if not acc_id:
        return
    with _LAST_SUBMIT_LOCK:
        _LAST_SUBMIT[str(acc_id)] = time.time()


def last_submit_at(acc_id: str) -> float:
    with _LAST_SUBMIT_LOCK:
        return float(_LAST_SUBMIT.get(str(acc_id)) or 0.0)


def submit_wait(acc_id: str, gap: float, *, now: float | None = None) -> float:
    """离上一次提交还差多久才够 gap（带抖动）。节奏是**按账号**算的——

    同一个账号上几个任务一起跑（规格 91）时，各算各的就等于没节奏：
    三个任务同时开跑照样一秒连打三发，Google 看到的是同一个账号。
    """
    if gap <= 0:
        return 0.0
    last = last_submit_at(acc_id)
    if not last:
        return 0.0
    import random as _rnd
    now = time.time() if now is None else float(now)
    return max(0.0, gap + _rnd.uniform(0, gap * 0.5) - (now - last))


def submit_gap() -> float:
    """两次提交之间至少隔多久（秒）。0 = 不等。

    规格 94：插件是人一条条点的，Orante 是一秒钟连打好几发——
    reCAPTCHA Enterprise 给的是"这次交互像不像人"的分数，连打最容易被判成
    PUBLIC_ERROR_UNUSUAL_ACTIVITY。隔几秒再交一发，代价是每段多等几秒，
    换来的是不用等 20 分钟冷却。嫌慢的把这项设成 0。
    """
    try:
        v = config.get("engine.flow.submit_gap_sec", None)
        if v is None:
            return 2.0
        return max(0.0, float(v))
    except Exception:  # noqa: BLE001
        return 2.0


def _probe_every() -> float:
    """冷却期间多久探一次路（秒）。0 = 关掉探路，老老实实等满。"""
    try:
        v = config.get("engine.flow.throttle_probe_min", None)
        if v is None:
            return accounts.THROTTLE_PROBE_EVERY
        return max(0.0, float(v) * 60.0)
    except Exception:  # noqa: BLE001
        return accounts.THROTTLE_PROBE_EVERY


def _nudge_scheduler() -> None:
    """收了一段就让调度器看一眼：空出来的并发位可以放下一个小任务进来。"""
    try:
        from ..scheduler import scheduler
        threading.Thread(target=scheduler._pump, daemon=True).start()
    except Exception:
        pass


def _clip_exists(d: Path, m: Manifest, sg: dict) -> bool:
    """检查已 collected 段的成片文件是否真的在磁盘上。"""
    for tk in reversed(sg.get("takes") or []):
        f = (tk.get("file") or "").replace("\\", "/")
        if f and (d / f).is_file() and (d / f).stat().st_size > 1024:
            return True
    c = d / "clips" / m.clip_name(sg)
    return c.is_file() and c.stat().st_size > 1024


@register("generate", account_bound=True)
def generate(job, only: list[int] | None = None, redo: bool = False,
             mark_fresh: bool = False, rtc_round: int = 0, chain: bool = True,
             acc_id: str | None = None):
    """acc_id：只有「重生时临时换账号」会传（见 regenerate），其余一律用任务自己的账号。"""
    t = taskmod.get(job.task_id)
    from . import broll, expand
    d, m, sync_log = broll.load_synced(job.task_id)
    for ln in sync_log:
        job.log(ln)
    acc = accounts.get(acc_id or t["account_id"])
    rtc = taskmod.rtc_on(t)
    try:
        rtc_round = int(rtc_round or 0)
    except (TypeError, ValueError):
        rtc_round = 0
    only_set = {int(x) for x in only} if only is not None else None
    # 只要切换了模型/引擎，尚未收片的任务自动按当前引擎规则重新拆段
    run_eng0 = str((acc or {}).get("engine") or "").strip().lower()
    split_eng0 = str(((m.split or {}).get("rule") or {}).get("engine") or m.engine or "").strip().lower()
    has_col0 = any(sg.get("status") == "collected" and _clip_exists(d, m, sg) for sg in (m.segments or []))
    if run_eng0 in ("flow", "grok", "studio") and split_eng0 and run_eng0 != split_eng0 and not has_col0:
        from . import split as _splitmod
        try:
            _splitmod.resplit_task_for_engine(
                job.task_id, run_eng0, new_account_id=acc.get("id"), log=job.log
            )
            t = taskmod.get(job.task_id)
            d, m, _ = broll.load_synced(job.task_id)
            if only_set is not None:
                only_set = {int(sg["s"]) for sg in (m.segments or [])}
        except Exception as e:  # noqa: BLE001
            job.log(f"  · 自动重拆段提示：{e}")
    # 检查已标 collected 但磁盘上缺片的段：有 asset_url 的先尝试取回，没有或取回失败的重新生成
    missing_with_url: list[dict] = []
    missing_reset = 0
    for s in m.segments:
        if only_set is not None and int(s.get("s") or 0) not in only_set:
            continue
        if s.get("status") == "collected" and not _clip_exists(d, m, s):
            takes = s.get("takes") or []
            last_url = next((tk.get("asset_url") for tk in reversed(takes) if tk.get("asset_url")), "")
            if last_url:
                missing_with_url.append(s)
            else:
                s["status"] = "planned"
                s["takes"] = []
                s["gen_id"] = None
                s["error"] = None
                s.pop("grok_retry", None)
                missing_reset += 1
    if missing_reset:
        job.log(f"  · 检测到 {missing_reset} 段磁盘缺片且无缓存地址，已自动打回待生成")
        m.save(d)
    # 开着「跑到全部出片」或手动开跑（rtc_round==0）时 blocked 也进队，再点开始生成不再假装看不见
    todo = [s for s in m.segments
            if s["status"] in ("planned", "failed")
            or ((redo or rtc or rtc_round == 0) and s["status"] == "blocked")]
    if only_set is not None:
        todo = [s for s in todo if int(s["s"]) in only_set]
    if acc.get("engine") != "studio":
        # 规格 98：已经排给本地模型的段，Flow / Grok 这边不再碰（等这一轮结束自动转交）
        todo = [s for s in todo if not s.get("studio_pending")]
    # 手动点击「开始生成」（rtc_round == 0）时，清掉上一轮残留的 grok_retry / 超时上限，确保失败段能重新提交
    if rtc_round == 0:
        for s in todo:
            s.pop("grok_retry", None)
            if s.get("status") == "failed":
                s["timeout_count"] = 0
                s["thumb_count"] = 0
                s["mismatch_count"] = 0
    inflight = {s["gen_id"]: s for s in m.segments if s["status"] == "submitted" and s.get("gen_id")}
    anchor_mode = pipeline.is_anchor_mode(m)
    chain_mode = pipeline.is_chain_mode(m)
    if chain_mode or anchor_mode:
        # 规格 95：接龙 / 接片全靠 ffmpeg 抽尾帧。没有它就别提交——第一段出了片也接不下去，白烧额度。
        from . import checks as _chk
        ff = _chk.find_ffmpeg()
        if not ff:
            raise OranteError(
                "找不到 ffmpeg，%s抽不了尾帧，先不开跑（免得白烧额度）"
                % ("尾帧接龙" if chain_mode else "锚点接片"),
                _chk.FFMPEG_FIX_HINT)
    if chain_mode:
        pipeline.ensure_chain(m)
        from . import tails as tailsmod
        for sg in m.segments:
            if sg.get("status") != "collected":
                continue
            if tailsmod.tail_path(d, int(sg.get("s") or 0)).is_file():
                continue
            try:
                tailsmod.after_collect(d, m, sg, log=job.log, job=job)
            except Exception as e:  # noqa: BLE001
                job.log(f"  · 补抽尾帧失败：{str(e)[:160]}")
        pipeline.stamp_waits(m, d)
    if anchor_mode:
        from . import tails as tailsmod
        tailsmod.ensure_roles(m)
        pipeline.rebuild_bridge_links(m.segments)
        # 已经收了的主片：补抽尾帧 / 重算跨度，好让接片这轮就能进队
        for sg in m.segments:
            if sg.get("status") != "collected":
                continue
            nxt = pipeline.next_of(m, sg)
            if not nxt or nxt.get("role") != "bridge":
                continue
            br = nxt.get("bridge") if isinstance(nxt.get("bridge"), dict) else {}
            if not br.get("tail_frame"):
                try:
                    tailsmod.after_collect(d, m, sg, log=job.log, job=job)
                except Exception as e:  # noqa: BLE001
                    job.log(f"  · 补抽尾帧失败：{str(e)[:160]}")
            else:
                pipeline.apply_gap_gate(d, m, sg, log=job.log)
        # 53：换路清账要在 wait_reason 之前生效，不然旧双图的 mismatch 会把新路挡死
        pipeline.migrate_bridge_acct_53(m)
        pipeline.settle_bridge_paths(m, log=job.log)
        pipeline.stamp_waits(m)
        save_early = True
    else:
        save_early = False
    wanted = {int(s["s"]) for s in todo}
    if not todo and not inflight and not missing_with_url:
        job.log("没有要生成的段（都已 collected 或 blocked）")
        _finish_round(job, t, m, d, rtc, rtc_round, chain, 0, 0, 0, set(), set())
        return {"submitted": 0}
    for s in todo:
        if not s.get("image") or not m.image(s["image"]):
            raise OranteError(f"段 {s['s']} 还没指定图片", "先到「编排」面板把配图轨应用一下。")
    # 描述语只在出片前要紧。缺就当场拦住并点名，别跑到一半才发现。
    from . import describe as descmod
    from . import simple86 as _s86
    # 没文案 = 空镜头也可以出片。有文案的口播仍不许拿已转空镜的图当主体段。
    no_copy = (not _s86.has_copy(t)) and (not _s86.has_copy(m))
    if no_copy:
        no_desc_nc = _s86.missing_desc_for_no_copy(t, m)
        if no_desc_nc:
            raise OranteError(
                f"还有 {len(no_desc_nc)} 张图没描述语（这个任务没有文案，描述语就是全部提示词）",
                "、".join(no_desc_nc[:12]) + "。到多模态面板补写，或用批量填描述语。")
    else:
        bad = []
        for s in todo:
            im = m.image(s.get("image") or "")
            if im and im.get("role") == "空镜":
                bad.append("s%03d→%s" % (s["s"], im.get("p")))
        if bad:
            raise OranteError(
                "有段配了已转空镜的图，不能生成",
                "到编排面板重排：" + "、".join(bad[:8]))
        no_desc = descmod.missing_subject_ps(m)
        if no_desc:
            raise OranteError(
                f"还有 {len(no_desc)} 张主体图没有描述语，不能出片",
                "、".join(no_desc[:12]) + "。到多模态面板补写。")

    # 规格 82 / 91：冷却中 / 网络限流 → 这一轮不提交，段留 planned。
    # 但冷却是 Orante 自己定的时长，Google 那边常常早就放行了（浏览器里能生成），
    # 所以每隔几分钟放一次"探路"：真的提交一段，成功就当场解除冷却。
    probing = False
    if acc.get("engine") == "flow":
        nt = accounts.network_throttle_state()
        if nt.get("on") and accounts.net_probe_due(every=_probe_every()):
            # 规格 94：全网暂停也探路。成功就把整条暂停一起解除。
            accounts.note_net_probe()
            probing = True
            job.log(f"  · 全网暂停还剩 {nt.get('left_min') or 1} 分钟，但先真的试一次看看（探路）")
            nt = {"on": False}
        if nt.get("on"):
            job.log(f"  ⏸ 全网暂停中 · 还剩 {nt.get('left_min') or 1} 分钟")
            job.log(f"    · 起因：{nt.get('at') or '?'} 起，{len(nt.get('accounts') or [])} 个 Flow 账号"
                    f"接连被限（{'、'.join(nt.get('accounts') or []) or '?'}）")
            job.log("    · 这多半是同一个网络在短时间里提交太多，Google 按网络限的，换账号也没用")
            job.log("    · 浏览器里还能生成？到 ④ 生成 顶部点「现在就试」，立刻解除这条暂停")
            notify_flow_throttle(job, acc, network=True)
            return {"submitted": 0, "collected": 0, "failed": 0,
                    "throttled": True, "pending_throttled": len(todo),
                    "network_throttled": True}
        left = accounts.throttle_remaining(acc)
        if left > 0 and not probing:
            if accounts.probe_due(acc, every=_probe_every()):
                probing = True
                accounts.note_probe(acc["id"])
                mins = max(1, int((left + 59) // 60))
                job.log(f"  · 冷却还剩 {mins} 分钟，但先真的试一次看看（探路）")
                job.log("    Google 那边经常早就放行了；这一次提交成功就当场解除冷却")
            else:
                for ln in accounts.throttle_lines(acc, probe_every=_probe_every()):
                    job.log(ln)
                notify_flow_throttle(job, acc)
                return {"submitted": 0, "collected": 0, "failed": 0,
                        "throttled": True, "pending_throttled": len(todo),
                        "network_throttled": False}

    taskmod.set_status(t["id"], "generating")
    events.emit("tasks.changed")
    # 规格 87：Grok 普通任务用本进程共享引擎，同账号几个小任务可以一起出片、只有一只"手"点 Chrome。
    shared_eng = packable_manifest(acc, m)
    if shared_eng:
        from ..engines import shared as sharedmod
        eng = sharedmod.acquire(acc, job, make=make_engine)   # 用本模块的 make_engine（测试会打补丁）
    else:
        eng = make_engine(acc, job.log)
    try:
        eng.connect()
    except EngineUnavailable as e:
        if shared_eng:
            sharedmod.release(job, acc.get("id"))
        raise OranteError(str(e), ("开好本地模型（双击 D:\\0_LLM\\启动Studio API.bat）再点重生。"
                                   if acc.get("engine") == "studio"
                                   else "到「账号」里点「登录 / 打开」，登好再点开始。"))
    except BaseException:
        if shared_eng:
            sharedmod.release(job, acc.get("id"))
        raise
    begin = getattr(eng, "begin_credits", None)
    if callable(begin):
        begin(job)
    # 磁盘缺片但 manifest 里记有 asset_url 的段：优先直接从旧地址取回，取不回就打回 planned 本轮重出
    if missing_with_url:
        dl_url_fn = getattr(eng, "download_url", None)
        n_recovered = 0
        n_requeued = 0
        for sg in missing_with_url:
            job.check()
            takes = sg.get("takes") or []
            last_tk = next((tk for tk in reversed(takes) if tk.get("asset_url")), None)
            url = (last_tk or {}).get("asset_url") or ""
            pid = (last_tk or {}).get("gen_id") or sg.get("gen_id") or ""
            dest = d / "clips" / m.clip_name(sg)
            ok_dl = False
            if url and callable(dl_url_fn):
                try:
                    ok_dl = bool(dl_url_fn(url, dest, pid))
                except Exception:  # noqa: BLE001
                    ok_dl = False
            if ok_dl and dest.is_file() and dest.stat().st_size > 1024:
                if last_tk is not None:
                    last_tk["file"] = f"clips/{dest.name}"
                n_recovered += 1
            else:
                sg["status"] = "planned"
                sg["takes"] = []
                sg["gen_id"] = None
                sg["error"] = None
                sg.pop("grok_retry", None)
                if sg not in todo:
                    todo.append(sg)
                wanted.add(int(sg["s"]))
                n_requeued += 1
        if n_recovered or n_requeued:
            todo.sort(key=lambda x: int(x.get("s") or 0))
            m.save(d)
            c = taskmod.remember_counts(d, m)
            taskmod.emit_seg_changed(t["id"], c)
            job.log(f"  · 缺片恢复完成：直接从云端取回 {n_recovered} 段，转入重新生成 {n_requeued} 段")
    # 2026-10-06：上一轮「提交失败：已经跳到 post 页」的段其实交上去了（额度已花）。
    # 先在 Grok 的标签页里按会话找回来接着等，别再交一次。
    find_orphan = getattr(eng, "find_orphan", None)
    if callable(find_orphan):
        n_back = 0
        for sg in list(todo):
            conv = orphan_conv_of(sg)
            if not conv:
                continue
            try:
                post = find_orphan(conv)
            except Exception:  # noqa: BLE001
                post = ""
            if post:
                sg["status"] = "submitted"
                sg["gen_id"] = post
                sg["gen_conv"] = conv
                sg["error"] = None
                sg.pop("orphan_conv", None)
                sg.pop("gen_health", None)
                inflight[post] = sg
                todo.remove(sg)
                n_back += 1
                job.log(f"  ↺ s{int(sg['s']):03d} 上次其实已经交上去了（post {post[:8]}…），"
                        f"在标签页里找回来接着等，不重交")
            else:
                sg.pop("orphan_conv", None)
                job.log(f"  · s{int(sg['s']):03d} 上次其实已经交上去了，但那个标签页已经关了、找不回，只能重交")
        if n_back:
            m.save(d)
    # 2026-10-06：以前被首帧指纹误判作废、其实是对的片子（在 _错件 / _弃用 里），结构复核过了就直接收回来
    # 用户点的重生（mark_fresh）/ 重做（redo）：用户就是要新片，绝不往回找旧片
    try:
        back = ([] if (mark_fresh or redo)
                else verifymod.restore_voided(m, d, todo, log=job.log))
    except Exception as e:  # noqa: BLE001
        back = []
        job.log(f"  · 翻错件时出错（不影响出片）：{str(e)[:120]}")
    if back:
        todo[:] = [sg for sg in todo if int(sg.get("s") or 0) not in set(back)]
        if anchor_mode or chain_mode:
            from . import tails as _tails_rb
            for sn in back:
                try:
                    _tails_rb.after_collect(d, m, m.seg(sn), log=job.log, job=job)
                except Exception as e:  # noqa: BLE001
                    job.log(f"  · s{sn:03d} 补抽尾帧失败：{str(e)[:120]}")
        m.save(d)
        taskmod.remember_counts(d, m)
    # 2026-10-16：Grok 仓库（https://grok.com/library）自动找回已生成的视频（标签页被关或 postId 重写后都能拿回）
    rec_lib_fn = getattr(eng, "recover_from_library", None)
    if callable(rec_lib_fn) and not (mark_fresh or redo):
        lib_cands = list(todo) + [sg for sg in inflight.values() if sg not in todo]
        if lib_cands:
            try:
                lib_back = rec_lib_fn(m, d, lib_cands) or []
            except Exception as e:  # noqa: BLE001
                lib_back = []
                job.log(f"  · 从 Grok 仓库找回时出错（不影响后续出片）：{str(e)[:120]}")
            if lib_back:
                lib_set = {int(x) for x in lib_back}
                todo[:] = [sg for sg in todo if int(sg.get("s") or 0) not in lib_set]
                wanted -= lib_set
                for gid2, sg2 in list(inflight.items()):
                    if int(sg2.get("s") or 0) in lib_set:
                        inflight.pop(gid2, None)
                if anchor_mode or chain_mode:
                    from . import tails as _tails_lib
                    for sn in lib_back:
                        try:
                            _tails_lib.after_collect(d, m, m.seg(sn), log=job.log, job=job)
                        except Exception as e:  # noqa: BLE001
                            job.log(f"  · s{sn:03d} 补抽尾帧失败：{str(e)[:120]}")
                m.save(d)
                c_lib = taskmod.remember_counts(d, m)
                taskmod.emit_seg_changed(t["id"], c_lib)
    # 0922：不说话（没文案）的任务在 Flow 上要无声视频。Veo 会连声音一起生成，
    # 声音被审核过滤（PUBLIC_ERROR_AUDIO_FILTERED）就整条不给。有台词的任务照旧带声音。
    want_silent = False
    strip_silent = False
    try:
        from . import simple86 as _s86q
        _no_copy_t = not _s86q.has_copy(t) and not _s86q.has_copy(m)
    except Exception:
        _no_copy_t = False
    if acc.get("engine") in ("flow", "studio"):
        want_silent = _no_copy_t
        if want_silent:
            job.log("  · 不说话的任务：这次按「返回无声视频」提交（不改账号设置）")
    # 下载后去音轨：Flow 出的一律去；本来是 Flow 的不说话任务临时换 Grok 重生，也去掉，
    # 跟任务里其它片子保持一致（都是无声）。
    task_eng = str(t.get("engine") or getattr(m, "engine", "") or "").strip().lower()
    strip_silent = _no_copy_t and (acc.get("engine") in ("flow", "studio") or task_eng in ("flow", "studio"))

    def save():
        m.save(d)
        # 段状态一变就推 counts；折叠行的方块靠这个变色，不拉整份 manifest
        c = taskmod.remember_counts(d, m)
        taskmod.emit_seg_changed(t["id"], c)

    # 切套装不再拦生成：这一轮的提示词现在就定稿，后面换套装也影响不到已开跑的任务。
    # 规格 93：定稿时记下"提示词世代"；上一轮按旧设置拼好的会在这里重拼。
    prompt_rev0 = prompt_rev()
    if not (anchor_mode or chain_mode):
        snapshot_prompts(job, m, todo, save=save, rev0=prompt_rev0)

    def my_share() -> int:
        """同账号好几个任务一起跑时，这个任务最多同时挂几条。

        规格 91：并发位是账号的（Flow 5 / Grok 4），几个任务共用一套引擎。
        不分一分，先起跑的那个会把位子全占满，后面的任务只能干等——
        所以按当前一起跑的任务数平分，至少留 1 个位子给自己。
        """
        if not shared_eng:
            return 10 ** 6
        try:
            from ..engines import shared as _sh
            n = max(1, len(_sh.users(acc.get("id") or "")))
        except Exception:  # noqa: BLE001
            n = 1
        if n <= 1:
            return 10 ** 6
        cap = int(acc.get("max_concurrent") or 4)
        return max(1, cap // n)

    started = {}
    adopted: set[str] = set()        # 本 Job 续跑接手的 gid，超时绳子更短
    job_voids: dict[str, int] = {}   # gid → 本 Job 作废次数
    n_sub = n_done = n_fail = 0
    net_down = 0
    n_throttled = 0
    account_paused = False
    summary_prev = None
    summary_quiet = 0
    judged: set[str] = set()
    refilled_ok: set[str] = set()
    gave_up: set[str] = set()
    if save_early:
        save()

    def promote() -> list[int]:
        """把前置刚齐的接片 / 接龙下一段推进队列。不算失败、不消耗重生。"""
        pipeline.stamp_waits(m, d)
        busy = {sg["s"] for sg in inflight.values()} | {sg["s"] for sg in queue}
        added = []
        for sg in pipeline.iter_ready(m, wanted, busy, task_dir=d):
            queue.append(sg)
            added.append(int(sg["s"]))
            why = sg.pop("wait_why", None) or "前置齐了"
            sg.pop("wait_on", None)
            sg.pop("wait_code", None)
            job.log(f"  → s{sg['s']:03d} {why}，推进队列")
        return added

    if chain_mode:
        waiting = [s for s in todo if not pipeline.can_enqueue(m, s, task_dir=d)]
        for s in waiting:
            job.log(f"  · s{s['s']:03d} {s.get('wait_why') or '等前置'}，先不提")
        queue = pipeline.iter_ready(m, wanted, set(), task_dir=d)
        n = len(m.segments or [])
        job.log("尾帧接龙是串行的，一段做完才能做下一段，%d 段大约要 %d × 单段时长；先提 %d 段"
                % (n, n, len(queue)))
    elif anchor_mode:
        waiting = [s for s in todo if not pipeline.can_enqueue(m, s)]
        for s in waiting:
            job.log(f"  · s{s['s']:03d} {s.get('wait_why') or '等前置'}，先不提")
        queue = pipeline.iter_ready(m, wanted, set())
        job.log(f"锚点模式：先提 {len(queue)} 段主片/已齐接片，{len(waiting)} 段等前置")
    else:
        queue = list(todo)

    def drain(p: str) -> None:
        """队列里同图的其它段先拿掉，避免一张图连着触发好几次换图。"""
        kept = [sg for sg in queue if sg.get("image") != p]
        queue[:] = kept

    def requeue_planned(p: str) -> list[int]:
        inflight_s = {sg["s"] for sg in inflight.values()}
        queued_s = {sg["s"] for sg in queue}
        added = []
        for sg in m.segments:
            if sg.get("image") != p or sg.get("status") != "planned":
                continue
            if sg["s"] in inflight_s or sg["s"] in queued_s:
                continue
            if (anchor_mode or chain_mode) and not pipeline.can_enqueue(m, sg, task_dir=d):
                continue
            queue.append(sg)
            added.append(int(sg["s"]))
        return added

    def try_auto_refill(p: str) -> bool:
        if not config.get("expand.auto_refill", True):
            return False
        im = m.image(p)
        if not im:
            return False
        judged.add(p)
        if not getattr(eng, "can_gen_images", False):
            job.log(f"  · {p} 被拒，当前引擎不能生图，标 blocked 等补图")
            return False
        if len(refilled_ok) >= MAX_AUTO_REFILL_TASK and p not in refilled_ok:
            if "TASK" not in gave_up:
                gave_up.add("TASK")
                job.log(f"  ⚠ 这一轮已经自动换了 {len(refilled_ok)} 张图还在被拒——"
                        f"多半是「被拒」判断错了，不是图的问题。停止自动换图，"
                        f"剩下的标 blocked；旧图都在 images/_replaced/ 里。"
                        f"已停止自动换图以免毁掉图池；确认是图的问题就点「再试一次」，"
                        f"确认是接口抽风就先歇十分钟")
            return False
        n = int(im.get("auto_refill") or 0)
        if n >= MAX_AUTO_REFILL:
            if p not in gave_up:
                job.log(f"  {p} 自动换了 {n} 次仍被拒，先放着")
                gave_up.add(p)
            return False
        preview = expand.preview_ref_p(m, d, p)
        attempt = n + 1
        im["auto_refill"] = attempt
        job.log(f"  ✗ {p} 被 {eng.name} 拒收 → 自动换图（参考 {preview}，第 {attempt}/{MAX_AUTO_REFILL} 次）")
        try:
            result = expand.refill_one(job, m, d, p, eng, None, auto=True, attempt=attempt)
        except OranteError as e:
            job.log(f"  · 自动换图失败：{e.message}")
            result = None
        except BlockedImage as e:
            job.log(f"  · 自动换图仍被拒：{e}")
            result = None
        if result and result.get("ok"):
            refilled_ok.add(p)
            raw = result.get("reset")
            if isinstance(raw, dict):
                reset = list(raw.get("reset") or [])
                kept = list(raw.get("kept") or [])
            else:
                reset = list(raw or [])
                kept = list(result.get("kept") or [])
            extra = requeue_planned(p)
            job.log(f"  ✓ {p} 已换成新图（已翻转）")
            line = expand.fmt_reset_log(p, reset, kept)
            if line:
                job.log("  " + line)
            extra_only = [n for n in extra if int(n) not in set(int(x) for x in reset)]
            if extra_only:
                job.log(f"  · {_fmt_segs(extra_only)} 按新图重提")
            save()
            return True
        if int(im.get("auto_refill") or 0) >= MAX_AUTO_REFILL and p not in gave_up:
            job.log(f"  {p} 自动换了 {im.get('auto_refill')} 次仍被拒，先放着")
            gave_up.add(p)
        save()
        return False

    refused_told: set[str] = set()

    def notify_flow_refused(p: str) -> None:
        """原图传不上 Flow、自动换图也没消化掉：直接告诉用户这张图在 Flow 上用不了、该怎么办。
        每张图只说一次，不刷屏。"""
        if p in refused_told:
            return
        refused_told.add(p)
        job.log(f"  ⛔ {p} 这张图 Flow 不收（上传就被拒，Flow 网页手动上传也一样），"
                f"自动换图也没换成。这张图在 Flow 上用不了——"
                f"把这个任务换到 Grok 等别的引擎出片，或者换一张图。")
        try:
            events.emit("toast", text=f"{p} 这张图 Flow 不收",
                        hint="Flow 网页手动上传也传不上去。把这个任务换到 Grok 等别的引擎，或换一张图。",
                        level="bad")
        except Exception:  # noqa: BLE001
            pass

    studio_told: set = set()

    def route_to_studio(p: str, reason: str = "", *, dry: bool = False) -> bool:
        """规格 98：Flow 不收这张图 → 优先唤醒本地视频模型 LTX（不重新生图），
        并将该图对应的全部未完成段（含 anchor_bridge 模式下的关联段）排给本地模型，自动按本地/Grok规则重分段。"""
        if not studio_fallback_on() or acc.get("engine") != "flow" or chain_mode:
            return False
        im = m.image(p)
        if not im or im.get("role") == "空镜":
            return False
        segs = [sg for sg in m.segments
                if sg.get("image") == p and sg.get("status") in ("blocked", "planned", "failed")]
        if dry:
            return bool(segs)
        if not segs:
            return False
        why = (reason or im.get("block_reason") or "Flow 不收这张图")[:200]
        for sg in segs:
            sg["status"] = "blocked"
            sg["studio_pending"] = True
            sg["studio_reason"] = why
            sg["error"] = "Flow 不收这张图 → 已排给本地模型 LTX（原图，自动重分段）"
        im["studio_route"] = True
        drain(p)
        ss = "、".join(f"s{int(sg['s']):03d}" for sg in segs)
        job.log(f"  ⇢ {p} Flow 不收 → 优先唤醒本地视频模型 LTX（原图不重新生图，{ss} 将自动按本地/Grok 规则重分段出片）")
        if p not in studio_told:
            studio_told.add(p)
            try:
                events.emit("toast", text=f"{p} Flow 不收，优先唤醒本地模型 LTX",
                            hint="不重新生图，自动按本地模型(Grok)规则重分段并用原图出片。", level="warn")
            except Exception:  # noqa: BLE001
                pass
        try:
            from . import studio_handoff
            studio_handoff.remember(t["id"])
        except Exception:  # noqa: BLE001
            pass
        save()
        return True

    def maybe_borrow(p: str) -> bool:
        """三次补图都失败、且开着跑到全部出片、已经是接力轮 → 借一张过关的图。"""
        if not rtc or rtc_round < 1:
            return False
        im = m.image(p)
        if not im or int(im.get("auto_refill") or 0) < MAX_AUTO_REFILL:
            return False
        segs = [sg for sg in m.segments
                if sg.get("image") == p and sg.get("status") == "blocked"]
        n = 0
        for sg in segs:
            orig = p
            bp = borrow_image(m, d, sg)
            if not bp:
                continue
            others = [x for x in m.segments
                      if x.get("image") == bp and x.get("status") == "collected"]
            hit = f"s{others[0]['s']:03d}" if others else "别的段"
            job.log(f"  ⚠ s{sg['s']:03d} 的 {orig} 三次都被拒，借用 {bp} 出片（画面会和 {hit} 重复）")
            inflight_s = {x["s"] for x in inflight.values()}
            queued_s = {x["s"] for x in queue}
            if sg["s"] not in inflight_s and sg["s"] not in queued_s:
                if not ((anchor_mode or chain_mode)
                        and not pipeline.can_enqueue(m, sg, task_dir=d)):
                    queue.append(sg)
            n += 1
        if n:
            save()
        return n > 0

    def maybe_block_for_fails(p: str, *, log_line: bool = True) -> bool:
        """gen_fail>=3 且从没成功 → 标 blocked。返回是否刚标上。"""
        im = m.image(p)
        if not im or im.get("status") == "blocked":
            return False
        gf = _gen_fail_of(m, p)
        if gf > int(im.get("gen_fail") or 0):
            im["gen_fail"] = gf
        if int(im.get("gen_fail") or 0) >= 3 and _never_collected(m, p):
            if log_line:
                job.log(f"  ✗ {p} 连续 {im.get('gen_fail')} 次生成失败、从没成功过 "
                        f"→ 判定被 Flow 拒了，标 blocked；点「补图」换一张")
            _block_image(m, p, "出片反复失败")
            return True
        return False

    try:
        while queue or inflight:
            job.check()
            if anchor_mode or chain_mode:
                # 检查台可能刚放行 / 换了锚点，把盘上的字段读回来再推进接片
                pipeline.sync_bench_from_disk(m, d)
                promote()
            # 1) 提交到满。接龙每个任务并发 = 1，别让 5 个空位把后面的段提前提上去。
            if account_paused:
                queue.clear()
            if not shared_eng and isinstance(getattr(eng, "_active", None), set):
                eng._active.intersection_update(inflight.keys())
            share = my_share()
            gap = submit_gap() if acc.get("engine") == "flow" else 0.0
            while queue and eng.capacity() > 0 and len(inflight) < share:
                if chain_mode and inflight:
                    break
                wait = submit_wait(acc.get("id") or "", gap)
                if wait > 0:
                    job.sleep(min(wait, 15.0))
                seg = queue.pop(0)
                if (anchor_mode or chain_mode) and not pipeline.can_enqueue(m, seg, task_dir=d):
                    why = (pipeline.wait_reason(m, seg, d) or {}).get("text") or "等前置"
                    job.log(f"  · s{seg['s']:03d} {why}，先不提")
                    continue
                is_bridge = anchor_mode and seg.get("role") == "bridge"
                is_chain = chain_mode and not pipeline.is_chain_restart(seg)
                nxt = pipeline.next_of(m, seg) if is_bridge else None
                p = ((nxt.get("image") if nxt else None) or seg.get("image") or "")
                im = m.image(p)
                if im:
                    gf = _gen_fail_of(m, p)
                    if gf > int(im.get("gen_fail") or 0):
                        im["gen_fail"] = gf
                if (not _keeps_image(eng) and im and im.get("status") != "blocked"
                        and im.get("flow_upload_refused") and route_to_studio(p, dry=True)):
                    # 规格 98：这张图上次就被 Flow 拒收过，别再白传一次，直接排给本地模型
                    _block_image(m, p, flow_refused_text(p))
                    if route_to_studio(p, "上次已被 Flow 拒收"):
                        continue
                if (not _keeps_image(eng) and im and im.get("status") != "blocked"
                        and maybe_block_for_fails(p)):
                    save()
                if im and im.get("status") == "blocked":
                    if _keeps_image(eng):
                        # 以前把「找不到 post」误当成拒图。收回标记，仍用原图。
                        # 本地模型：Flow 拒的图正是要交给它的，原图照用，Flow 拒收标记留着。
                        _unblock_grok_image(m, p, keep_refused=(eng.name == "studio"))
                        seg["status"] = "planned"
                        seg["error"] = None
                        if eng.name == "grok":
                            seg["grok_retry"] = max(int(seg.get("grok_retry") or 0), 1)
                        job.log(f"  · s{seg['s']:03d} 用{_eng_label(eng)}出片，不换图，仍用原图 {p}")
                        save()
                    else:
                        content = bool((im or {}).get("flow_upload_refused") or (im or {}).get("content_fail")
                                       or is_content_refusal((im or {}).get("block_reason")))
                        if content and route_to_studio(p, (im or {}).get("block_reason") or ""):
                            continue
                        job.log(f"  s{seg['s']:03d} 的图 {p} 已被拒，先补图")
                        drain(p)
                        if not try_auto_refill(p):
                            if not maybe_borrow(p) and (im or {}).get("flow_upload_refused"):
                                notify_flow_refused(p)
                        continue
                last_image = None
                start_img = None
                if is_chain:
                    try:
                        pipeline.maybe_upscale_chain_tail(job, m, d, seg)
                        pipeline.apply_chain_ref(m, d, seg)
                        pipeline.resolve_chain_desc(job, m, d, seg)
                        start_img, last_image, _tmp = pipeline.stage_chain_start(d, m, seg)
                        last_image = None
                    except OranteError as e:
                        job.log(f"  · s{seg['s']:03d} {e.message}，先不提")
                        continue
                    img = start_img
                    # 规格 95：apply_chain_ref 已把段的配图对齐到真正用的参考（登记的尾帧图 / 起头图）
                    p = str(seg.get("image") or p or "")
                elif is_bridge:
                    try:
                        if pipeline.grok_bridge_is_single(m, seg):
                            start_img, last_image, _tmp = pipeline.stage_bridge_start(d, m, seg)
                            last_image = None
                            seg["bridge_engine"] = "grok-single"
                        else:
                            start_img, last_image, _tmp = pipeline.stage_bridge_images(d, m, seg)
                            if pipeline._is_grok_engine(m, seg):
                                seg["bridge_engine"] = "grok"
                            else:
                                seg["bridge_engine"] = "flow"
                    except OranteError as e:
                        job.log(f"  · s{seg['s']:03d} {e.message}，先不提")
                        continue
                    img = start_img
                else:
                    if not im:
                        job.log(f"  ✗ s{seg['s']:03d} 没有配图")
                        continue
                    old_file = im.get("file")
                    img = _resolve_image(d, im)
                    if not img.is_file():
                        job.log(f"  ✗ s{seg['s']:03d} 的图 {p} 找不到文件：{old_file}"
                                f"（同目录也没有同名的 jpg/jpeg/png/webp）")
                        continue
                    if im.get("file") != old_file:
                        job.log(f"  · 图 {p} 的文件名已改：{old_file} → {im['file']}")
                        save()
                prompt = _final_prompt(m, seg, rev0=prompt_rev0)
                seg["prompt_final"] = prompt
                seg["prompt_snap"] = _prompt_snap_key(m, seg)
                seg["prompt_rev"] = prompt_rev0
                if seg.get("prompt_locked"):
                    head = (prompt or "").replace("\n", " ")[:40]
                    job.log(f"  → s{seg['s']:03d} 用手写提示词（{len(prompt or '')} 字）：{head}")
                elif is_bridge:
                    br = seg.get("bridge") if isinstance(seg.get("bridge"), dict) else {}
                    from_s = br.get("from_seg")
                    from_lab = pipeline._lab(from_s) if from_s else "上一段"
                    if seg.get("bridge_engine") == "grok-single":
                        job.log(f"  → s{seg['s']:03d} 接片(Grok 单图) 首帧={from_lab} 尾帧")
                    elif seg.get("bridge_engine") == "flow":
                        job.log(f"  → s{seg['s']:03d} 接片(Flow 双图) 首帧={from_lab} 尾帧 尾帧图={p}")
                    else:
                        job.log(f"  → s{seg['s']:03d} 接片 首帧={from_lab} 尾帧 尾帧图={p}")
                sub_dur = _submit_duration(seg, eng)
                if sub_dur != int(seg.get("duration") or 8):
                    job.log(f"  · s{seg['s']:03d} 原本 {int(seg.get('duration') or 8)} 秒，"
                            f"{_eng_label(eng)} 没有这一档 → 按 {sub_dur} 秒出")
                try:
                    if isinstance(seg, dict):
                        _cur_sid, _cur_sname = "", ""
                        try:
                            from . import suites as _suites_mod
                            _cur_sid = _suites_mod.current_id()
                            if _cur_sid:
                                _cur_sname = str((_suites_mod.get(_cur_sid) or {}).get("name") or "")
                        except Exception:  # noqa: BLE001
                            pass
                        seg["_orante_signals"] = {
                            "task_id": str(t.get("id") or ""),
                            "task_type": str(t.get("type") or getattr(m, "type", "") or ""),
                            "suite_id": _cur_sid,
                            "suite_name": _cur_sname,
                            "silent": bool(want_silent or _no_copy_t),
                            "voice": str((im or {}).get("voice") or ""),
                            "eye": str((im or {}).get("eye") or ""),
                            "scene": str((im or {}).get("scene") or ""),
                            "pose": dict((im or {}).get("pose") or {}) if isinstance((im or {}).get("pose"), dict) else {},
                            "measure": dict((im or {}).get("measure") or {}) if isinstance((im or {}).get("measure"), dict) else {},
                            "role": str(seg.get("role") or "anchor"),
                            "seg_index": int(seg.get("s") or 1),
                            "seg_total": len(m.segments or []),
                        }
                    try:
                        if want_silent:
                            gid = eng.submit(seg, img, prompt, sub_dur,
                                             last_image=last_image, silent=True)
                        else:
                            gid = eng.submit(seg, img, prompt, sub_dur,
                                             last_image=last_image)
                    finally:
                        if isinstance(seg, dict):
                            seg.pop("_orante_signals", None)
                except CreditHalted as e:
                    notify_credit_halt(job, eng, e)
                    wait_credit_continue(job, eng)
                    queue.insert(0, seg)
                    continue
                except FlowThrottled as e:
                    # 不算失败。这一段和同一账号其余段本轮都不再提交。
                    seg["status"] = "planned"
                    seg["error"] = "账号限流中"
                    n_throttled += 1
                    account_paused = True
                    try:
                        # 规格 95：记下"这轮之前成功交了几发、离上一发多久"——
                        # 第一发就被拒和连打后被拒，是两种病，处方不一样
                        _last = last_submit_at(acc.get("id") or "")
                        accounts.mark_throttled(acc["id"], reason={
                            "code": getattr(e, "flow_code", "") or "PUBLIC_ERROR_UNUSUAL_ACTIVITY",
                            "rpc": "eb1hJf",
                            "err_n": getattr(e, "err_n", None),
                            "raw": getattr(e, "raw", "") or "",
                            "seg": f"s{seg['s']:03d}",
                            "submitted_before": int(n_sub),
                            "since_last_sec": (int(time.time() - _last) if _last else None),
                        })
                        acc = accounts.get(acc["id"])
                    except Exception:
                        pass
                    job.log(f"  ⏸ s{seg['s']:03d} 没提交成：{e.message}")
                    raw = format_flow_raw({
                        "code": getattr(e, "flow_code", "") or "PUBLIC_ERROR_UNUSUAL_ACTIVITY",
                        "rpc": "eb1hJf",
                        "err_n": getattr(e, "err_n", None),
                    })
                    job.log(raw)
                    for ln in accounts.throttle_lines(acc, probe_every=_probe_every()):
                        job.log(ln)
                    notify_flow_throttle(job, acc)
                    pipeline.cleanup_bridge_tmp(d, seg.get("s"))
                    queue.clear()
                    save()
                    continue
                except BlockedImage as e:
                    if _keeps_image(eng):
                        job.log(f"  ✗ s{seg['s']:03d} {_eng_label(eng)}提交没成（{e}），不换图")
                        pipeline.cleanup_bridge_tmp(d, seg.get("s"))
                        if not _grok_resubmit(seg, str(e), job, queue):
                            seg["status"] = "failed"
                            seg["error"] = str(e)
                            n_fail += 1
                        save()
                        continue
                    judged.add(p)
                    refused = is_flow_upload_refused(e)
                    if refused:
                        # 上传这一步就被 Flow 拒了：这张原图在 Flow 上根本传不上去
                        _block_image(m, p, flow_refused_text(p))
                        im_r = m.image(p)
                        if im_r:
                            im_r["flow_upload_refused"] = True
                        job.log(f"  ✗ s{seg['s']:03d} 图 {p} 传不上 Flow（{str(e)[:120]}）→ 标 blocked")
                    else:
                        _block_image(m, p, f"图被拒：{e}"[:300])
                        job.log(f"  ✗ s{seg['s']:03d} 图 {p} 被 {eng.name} 拒了 → 标 blocked")
                    pipeline.cleanup_bridge_tmp(d, seg.get("s"))
                    save()
                    im_c = m.image(p)
                    if im_c is not None:
                        im_c["content_fail"] = int(im_c.get("content_fail") or 0) + 1
                    if route_to_studio(p, str(e)):
                        continue
                    drain(p)
                    if not try_auto_refill(p):
                        if not maybe_borrow(p) and refused:
                            notify_flow_refused(p)
                    continue
                except OranteError as e:
                    _cev = getattr(job, "cancelled", None)
                    if _cev is not None and _cev.is_set():
                        # 用户点了「停止」：浏览器随之关掉，排队的段一个个报「context closed」。
                        # 这不是这些段的错（13:07 那次把 s065~s075 全记成了失败），原样留着待做。
                        pipeline.cleanup_bridge_tmp(d, seg.get("s"))
                        save()
                        from ..scheduler import JobCancelled
                        raise JobCancelled()
                    if is_net_down(e.message):
                        # 断网不是这一段的错：放回队首，等一会儿再试；一直没网就停，段留着待做
                        net_down += 1
                        seg["status"] = "planned"
                        seg["error"] = None
                        pipeline.cleanup_bridge_tmp(d, seg.get("s"))
                        save()
                        if net_down > NET_DOWN_MAX:
                            raise OranteError("网络一直没恢复，先停下（没提交的段都留着待做）",
                                              "网好了再点「开始生成」，接着跑。")
                        if net_down == 1:
                            job.log(f"  ⏸ 网断了（{e.message[:80]}），等网恢复再接着提交，不算失败")
                        queue.insert(0, seg)
                        job.sleep(NET_DOWN_WAIT)
                        break
                    conv0 = orphan_conv_of(e.message)
                    post0 = ""
                    if conv0 and callable(getattr(eng, "find_orphan", None)):
                        try:
                            post0 = eng.find_orphan(conv0)
                        except Exception:  # noqa: BLE001
                            post0 = ""
                    if post0:
                        # 页面晚一步跳到 post 页：其实交上去了，按已提交接着走（不白花额度、不重交）
                        gid = post0
                        job.log(f"  ↺ s{seg['s']:03d} 提交其实成功了（页面晚一步跳到 post 页），"
                                f"按已提交处理 → post {post0[:8]}…")
                    else:
                        seg["status"] = "failed"
                        seg["error"] = e.message
                        if conv0:
                            seg["orphan_conv"] = conv0
                        n_fail += 1
                        job.log(f"  ✗ s{seg['s']:03d} 提交失败：{e.message}")
                        if getattr(e, "hint", ""):
                            job.log(f"        {e.hint}")
                        pipeline.cleanup_bridge_tmp(d, seg.get("s"))
                        save()
                        continue
                if probing:
                    # 探路这一发真的提交成功了：Google 早就放行，冷却是我们自己多等的
                    probing = False
                    try:
                        accounts.clear_throttle(acc["id"], who="probe")   # 连全网暂停一起清
                        acc = accounts.get(acc["id"])
                    except Exception:  # noqa: BLE001
                        pass
                    job.log("  ✓ 探路成功：这个账号现在能用，冷却已解除，继续出片")
                    events.emit("accounts.changed")
                if net_down:
                    job.log("  ▶ 网恢复了，接着提交")
                    net_down = 0
                note_submit(acc.get("id") or "")
                seg["status"] = "submitted"
                seg["gen_id"] = gid
                # 这一条是哪个账号、哪个引擎、几秒出的（重生时可能临时换了账号）
                seg["gen_via"] = {"engine": (getattr(eng, "name", "") or acc.get("engine") or ""),
                                  "account_id": acc.get("id") or "",
                                  "duration": int(sub_dur)}
                conv_of = getattr(eng, "conv_of", None)
                if callable(conv_of):
                    conv = conv_of(gid)
                    if conv:
                        seg["gen_conv"] = conv
                seg["error"] = None
                seg.pop("gen_health", None)  # 新 post，旧的被踢证据作废
                attach_seg = getattr(eng, "attach_seg", None)
                if callable(attach_seg):
                    attach_seg(gid, seg)
                inflight[gid] = seg
                started[gid] = time.time()
                n_sub += 1
                save()
                job.sleep(1.5)
            if not inflight:
                continue
            # 2) 轮询。续跑接手：submitted 段的会话 Id 从 manifest 塞回引擎。
            adopt_fn = getattr(eng, "adopt", None)
            attach_seg = getattr(eng, "attach_seg", None)
            for gid2, seg2 in inflight.items():
                if gid2 not in started:
                    if callable(adopt_fn):
                        adopt_fn(gid2, seg2)
                    started[gid2] = time.time()
                    adopted.add(gid2)
                if callable(attach_seg):
                    attach_seg(gid2, seg2)
            job.sleep(POLL_EVERY)
            try:
                tick = getattr(eng, "tick_credits", None)
                if callable(tick):
                    tick()
            except CreditHalted as e:
                notify_credit_halt(job, eng, e)
                wait_credit_continue(job, eng)
            try:
                res = eng.poll(list(inflight))
            except FlowThrottled as e:
                account_paused = True
                try:
                    accounts.mark_throttled(acc["id"], reason={
                        "code": getattr(e, "flow_code", "") or "PUBLIC_ERROR_UNUSUAL_ACTIVITY",
                        "rpc": "jwpduf",
                        "err_n": getattr(e, "err_n", None),
                        "raw": getattr(e, "raw", "") or "",
                        "seg": "轮询",
                    })
                    acc = accounts.get(acc["id"])
                except Exception:
                    pass
                job.log(f"  ⏸ 轮询时被限流：{e.message}")
                job.log(format_flow_raw({
                    "code": getattr(e, "flow_code", "") or "PUBLIC_ERROR_UNUSUAL_ACTIVITY",
                    "rpc": "jwpduf",
                    "err_n": getattr(e, "err_n", None),
                }))
                for ln in accounts.throttle_lines(acc, probe_every=_probe_every()):
                    job.log(ln)
                notify_flow_throttle(job, acc)
                queue.clear()
                res = {}
            health_dirty = False
            for gid, r in res.items():
                seg = inflight.get(gid)
                if not seg:
                    continue
                # 引擎把被踢 / 空页计数带回来，立刻写进 seg，save 会落盘。
                # 只有结果里带了 gen_health 这个键才同步——Flow 等引擎没有这个键，
                # 不许把续跑从 manifest 带过来的 first_bad_at 误删。
                if isinstance(r, dict) and "gen_health" in r:
                    h = r.get("gen_health")
                    if isinstance(h, dict) and h:
                        if seg.get("gen_health") != h:
                            seg["gen_health"] = dict(h)
                            health_dirty = True
                    elif "gen_health" in seg:
                        seg.pop("gen_health", None)
                        health_dirty = True
                # 超时对所有分支统一生效，作废 continue 不许绕过
                # 续跑接手用 ADOPT_TIMEOUT；有 first_bad_at 从那一刻算，不从接手算
                is_adopted = gid in adopted
                if isinstance(r, dict) and r.get("queued"):
                    # 规格 98：本地模型还在本机排队（比如 7860 界面正在跑别的），排队时间不算超时
                    started[gid] = time.time()
                tf = getattr(eng, "timeout_for", None)
                dl = None
                if callable(tf):
                    try:
                        dl = tf(seg, gid)
                    except Exception:  # noqa: BLE001
                        dl = None
                due, elapsed = gen_timed_out(
                    seg, adopted=is_adopted,
                    started_at=started.get(gid, time.time()), deadline=dl)
                if due:
                    canc = getattr(eng, "cancel", None)
                    if callable(canc):
                        try:
                            canc(gid)      # 规格 98：本地模型那边还没出完的取消掉，别让显卡白跑
                        except Exception:  # noqa: BLE001
                            pass
                    apply_timeout(seg, adopted=is_adopted, elapsed=elapsed)
                    seg.pop("gen_health", None)
                    job.log(f"  ✗ s{seg['s']:03d} {seg.get('error')}")
                    n_fail += 1
                    inflight.pop(gid, None)
                    pipeline.cleanup_bridge_tmp(d, seg.get("s"))
                    if chain_mode:
                        if pipeline.chain_exhausted(seg):
                            job.log("⚠ s%03d 多次重试用尽，下一段照常放行" % int(seg["s"]))
                        pipeline.stamp_waits(m, d)
                        promote()
                    elif anchor_mode and seg.get("role") != "bridge":
                        pipeline.stamp_waits(m)
                    save()
                    continue
                if r["state"] == "done":
                    dest = d / "clips" / m.clip_name(seg)
                    if dest.exists():
                        dest = dest.with_name(dest.stem + f"-{int(time.time())}.mp4")
                    try:
                        eng.download(gid, dest)
                    except NeedRedownload as e:
                        job.log(f"  ↻ s{seg['s']:03d} 下载地址过不了闸，重新取：{e.message}")
                        n_thumb = int(seg.get("thumb_count") or 0) + 1
                        seg["thumb_count"] = n_thumb
                        if n_thumb >= pipeline.THUMB_STOP:
                            seg["status"] = "failed"
                            seg["error"] = "网络层一直只给到缩略图/预览"
                            job.log(f"  ✗ s{seg['s']:03d} 网络层一直只给到缩略图/预览")
                            n_fail += 1
                            inflight.pop(gid, None)
                            pipeline.cleanup_bridge_tmp(d, seg.get("s"))
                        save()
                        continue
                    except OranteError as e:
                        n_dl = int(seg.get("dl_retry") or 0)
                        if is_transient_dl_error(e.message) and n_dl < DL_RETRY_MAX:
                            # 网络抖了一下：片子还在 Grok / Flow 那边，下一轮再下，不算失败
                            seg["dl_retry"] = n_dl + 1
                            job.log(f"  ↻ s{seg['s']:03d} 下载时网络断了一下（{e.message[:60]}），"
                                    f"稍后再下（第 {n_dl + 1}/{DL_RETRY_MAX} 次）")
                            save()
                            continue
                        seg["status"] = "failed"
                        seg["error"] = _clip_dl_error(e.message)
                        seg.pop("dl_retry", None)
                        n_fail += 1
                        inflight.pop(gid, None)
                        save()
                        continue
                    seg["thumb_count"] = 0
                    seg.pop("dl_retry", None)
                    if strip_silent:
                        # 「Return silent videos」只管"声音被过滤时照样给片"，没被过滤的照样带声音；
                        # 不说话的任务一律把音轨去掉（画面不重编码），再做校验、记凭证。
                        try:
                            if verifymod.strip_audio(dest):
                                job.log(f"  · s{seg['s']:03d} 片子带了声音，已去掉音轨（不说话的任务出无声视频）")
                        except Exception:
                            pass
                    url = r.get("url") or ""
                    vr = verifymod.after_download(
                        m, d, seg, dest,
                        asset_url=url, gen_id=gid, log=job.log, apply=True)
                    if vr.get("voided"):
                        eng.forget_url(gid, url)
                        n_void = int(job_voids.get(gid) or 0) + 1
                        job_voids[gid] = n_void
                        sn = int(seg.get("s") or 0)
                        if n_void >= VOID_STOP:
                            seg["status"] = "failed"
                            seg["error"] = "本轮作废已 6 次，无条件失败"
                            job.log(f"  ✗ s{sn:03d} 本轮作废已 6 次，无条件失败")
                            n_fail += 1
                            inflight.pop(gid, None)
                            pipeline.cleanup_bridge_tmp(d, sn)
                            wanted.discard(sn)
                            save()
                            continue
                        st = seg.get("status")
                        if st in ("planned", "failed") or not seg.get("gen_id"):
                            # 片子错了或换 gen_id：丢掉这个 gid，不要再 poll。
                            # forget_url 会把 gid 加回 _active（给「重新取地址」用），这里不要占坑。
                            active = getattr(eng, "_active", None)
                            if isinstance(active, set):
                                active.discard(gid)
                            inflight.pop(gid, None)
                            pipeline.cleanup_bridge_tmp(d, sn)
                            if st == "failed":
                                n_fail += 1
                                if (seg.get("role") == "bridge"
                                        and int(seg.get("mismatch_count") or 0)
                                        >= pipeline.MISMATCH_STOP):
                                    wanted.discard(sn)
                            elif st == "planned":
                                queued_s = {sg["s"] for sg in queue}
                                if sn not in queued_s:
                                    queue.append(seg)
                            save()
                            continue
                        save()
                        continue
                    take = vr.get("take") or {
                        "file": str(dest.relative_to(d)).replace("\\", "/"),
                        "gen_id": gid,
                        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    if seg.get("borrowed"):
                        take["borrowed"] = True
                        if seg.get("borrowed_from"):
                            take["borrowed_from"] = seg.get("borrowed_from")
                    via = seg.get("gen_via") if isinstance(seg.get("gen_via"), dict) else None
                    if via and seg.get("gen_id") == gid:
                        take["engine"] = via.get("engine") or ""
                        take["account_id"] = via.get("account_id") or ""
                        take["duration"] = via.get("duration")
                    seg.setdefault("takes", []).append(take)
                    verifymod._merge_issues(seg, verifymod.issues_from_result(vr))
                    seg["status"] = "collected"
                    # 80：按新图重新收片之后，旧图记号自动消失
                    seg.pop("image_replaced", None)
                    seg.pop("gen_health", None)
                    seg["wrongclip_count"] = 0
                    seg["voided_sha"] = []
                    verifymod.clear_void_streak(seg)
                    seg.pop("stale_from", None)
                    br_clear = seg.get("bridge") if isinstance(seg.get("bridge"), dict) else None
                    if br_clear is not None:
                        br_clear.pop("stale_tail", None)
                    if mark_fresh:
                        # 重生出来的片子打个「新」，收片面板上一眼能认出要复查哪几段
                        seg["fresh"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    n_done += 1
                    im = m.image(seg.get("image") or "")
                    if im:
                        im["gen_fail"] = 0
                    extra = ""
                    if vr.get("verdict") == "soft":
                        extra = f"  ⚠ 首帧距 {vr.get('frame_dist')}"
                    job.log(f"  ✓ s{seg['s']:03d} 收片 {dest.name}{extra}")
                    inflight.pop(gid, None)
                    if shared_eng:
                        _nudge_scheduler()
                    if anchor_mode or chain_mode:
                        try:
                            from . import tails as tailsmod
                            tailsmod.after_collect(d, m, seg, log=job.log, job=job)
                        except Exception as e:  # noqa: BLE001
                            job.log(f"  · 尾帧提取失败：{str(e)[:160]}")
                    if anchor_mode and seg.get("role") == "bridge":
                        try:
                            from . import tails as tailsmod
                            hp = tailsmod.extract_head(d, m, seg)
                            brh = seg.get("bridge") if isinstance(seg.get("bridge"), dict) else {}
                            if hp:
                                brh["first_frame"] = str(hp.relative_to(d)).replace("\\", "/")
                            if vr.get("frame_dist") is not None:
                                brh["mismatch_first_dist"] = vr.get("frame_dist")
                            seg["bridge"] = brh
                        except Exception:  # noqa: BLE001
                            pass
                    if chain_mode:
                        pipeline.cascade_after_anchor(d, m, seg, log=job.log)
                        pipeline.stamp_waits(m, d)
                        promote()
                    elif anchor_mode:
                        cas = pipeline.cascade_after_anchor(d, m, seg, log=job.log)
                        if cas.get("action") == "reset":
                            reset_ss = [int(x) for x in (cas.get("reset") or []) if x]
                            if cas.get("s") is not None:
                                s0 = int(cas["s"])
                                if s0 not in reset_ss:
                                    reset_ss.insert(0, s0)
                            for rs in reset_ss:
                                wanted.add(rs)
                                # 打回的接片如果还在本轮 inflight，拿掉
                                for gid2, sg2 in list(inflight.items()):
                                    if int(sg2.get("s") or 0) == rs:
                                        inflight.pop(gid2, None)
                        promote()
                    pipeline.cleanup_bridge_tmp(d, seg.get("s"))
                    save()
                elif r["state"] == "failed":
                    err = r.get("error") or "生成失败"
                    seg.pop("gen_health", None)
                    p = seg.get("image") or ""
                    raw = str(r.get("raw") or "")
                    inflight.pop(gid, None)
                    pipeline.cleanup_bridge_tmp(d, seg.get("s"))
                    if _keeps_image(eng):
                        # Grok / 本地模型没有拒图。找不到 post / 下不到片子 / 出片失败 = 原图再交一次。
                        job.log(f"  ✗ s{seg['s']:03d} {_eng_label(eng)}没出成片子 {err} {raw[:300]}")
                        if _grok_resubmit(seg, err, job, queue):
                            save()
                            continue
                        seg["status"] = "failed"
                        seg["error"] = err
                        seg["fail_count"] = int(seg.get("fail_count") or 0) + 1
                        n_fail += 1
                        job.log(f"  ✗ s{seg['s']:03d} 原图再交一次仍没收到，先停着，不换图")
                        if chain_mode:
                            if pipeline.chain_exhausted(seg):
                                job.log("⚠ s%03d 多次重试用尽，下一段照常放行" % int(seg["s"]))
                            pipeline.stamp_waits(m, d)
                            promote()
                        elif anchor_mode and seg.get("role") != "bridge":
                            pipeline.stamp_waits(m)
                        save()
                        continue
                    seg["status"] = "failed"
                    seg["error"] = err
                    seg["fail_count"] = int(seg.get("fail_count") or 0) + 1
                    im = m.image(p)
                    if im:
                        im["gen_fail"] = int(im.get("gen_fail") or 0) + 1
                    n_fail += 1
                    job.log(f"  ✗ s{seg['s']:03d} 生成失败 {err} {raw[:300]}")
                    block_now = False
                    if im is not None and is_content_refusal(f"{err} {raw[:300]}"):
                        im["content_fail"] = int(im.get("content_fail") or 0) + 1
                        _block_image(m, p, f"{err} {raw[:120]}".strip())
                        block_now = True
                    elif "被拒" in str(err):
                        _block_image(m, p, err)
                        block_now = True
                    elif maybe_block_for_fails(p):
                        block_now = True
                    # 主片失败：后面的接片保持 planned，显示等前置，不消耗重生
                    if chain_mode:
                        if pipeline.chain_exhausted(seg):
                            job.log("⚠ s%03d 多次重试用尽，下一段照常放行" % int(seg["s"]))
                        pipeline.stamp_waits(m, d)
                        promote()
                    elif anchor_mode and seg.get("role") != "bridge":
                        nxt = pipeline.next_of(m, seg)
                        if nxt and nxt.get("role") == "bridge" and nxt.get("status") not in (
                                "collected", "submitted"):
                            nxt["status"] = "planned"
                            nxt["error"] = None
                        pipeline.stamp_waits(m)
                    save()
                    if block_now:
                        im_b = m.image(p) or {}
                        # 规格 98：只有内容类失败才转本地模型；网络 / 超时这类凑满 3 次照旧换图
                        if im_b.get("content_fail") and route_to_studio(p, str(err)):
                            continue
                        drain(p)
                        if not try_auto_refill(p):
                            maybe_borrow(p)
            if health_dirty:
                save()
            live = len(inflight)
            if live:
                bounce_sum = empty_sum = 0
                ss = []
                for sg_i in inflight.values():
                    try:
                        ss.append(int(sg_i.get("s") or 0))
                    except (TypeError, ValueError):
                        ss.append(0)
                    gh_i = sg_i.get("gen_health") if isinstance(sg_i.get("gen_health"), dict) else {}
                    bounce_sum += int((gh_i or {}).get("bounced") or 0)
                    empty_sum += int((gh_i or {}).get("empty_rounds") or 0)
                key = (live, len(queue), n_done, n_fail, tuple(ss), bounce_sum, empty_sum)
                emit, summary_prev, summary_quiet = tick_summary(
                    summary_prev, key, summary_quiet)
                if emit:
                    job.log(inflight_summary_line(
                        inflight, len(queue), n_done, started, adopted))
    finally:
        end = getattr(eng, "end_credits", None)
        if callable(end):
            try:
                end()
            except Exception:
                pass
        if shared_eng:
            sharedmod.release(job, acc.get("id"))
        else:
            eng.close()
        c = taskmod.remember_counts(d, m)
        if c["total"] and c["collected"] == c["total"]:
            taskmod.set_status(t["id"], "collected")
        # 收尾无条件再推一次，防止节流/SSE 抖动把最后一格吞掉
        taskmod.emit_seg_changed(t["id"], c, force=True)
        events.emit("tasks.changed")
    extra = f"（已自动换图 {len(refilled_ok)}，放弃 {len(gave_up)}）"
    if chain_mode or anchor_mode:
        pipeline.stamp_waits(m, d)
        still = [sg for sg in m.segments if pipeline.is_waiting(m, sg, d)]
        if still:
            bits = "、".join("%s %s" % (pipeline._lab(sg["s"]), sg.get("wait_why") or "等前置")
                            for sg in still[:8])
            job.log("还在等前置：%s" % bits)
            extra += f"，等前置 {len(still)}"
    job.log(f"本轮：提交 {n_sub}，收片 {n_done}，失败 {n_fail}，被拒 {len(judged)}{extra}")
    _finish_round(job, t, m, d, rtc, rtc_round, chain,
                  n_sub, n_done, n_fail, judged, refilled_ok, gave_up, extra)
    pending_throttled = 0
    net = accounts.network_throttled() if acc.get("engine") == "flow" else False
    if n_throttled or account_paused:
        pending_throttled = _unfinished_wanted(m, wanted)
        _requeue_after_throttle(job)
    return {"submitted": n_sub, "collected": n_done, "failed": n_fail,
            "throttled": bool(n_throttled or account_paused),
            "pending_throttled": pending_throttled,
            "network_throttled": bool(net)}


@register("regenerate", account_bound=True)
def regenerate(job, segs: list[int], fill_missing: bool = False):
    """重生：把这些段的旧片挪到 clips/_弃用，段回 planned，然后同一账号重跑。

    图已经反复失败 / 已 blocked 的，先把失败次数写回图上，generate 主循环会自动换图。
    """
    t = taskmod.get(job.task_id)
    d = Path(t["dir"])
    m = Manifest.load(d)
    want0 = {int(x) for x in (segs or [])}
    # 重生时可以临时换一个账号（比如 Flow 的任务这几段改用 Grok 重生）：
    # 账号来自 jobs.start(account_id=…)，就是这个 Job 占着的那个账号。任务本身的账号不改。
    run_acc_id = getattr(job, "account_id", None) or t["account_id"]
    run_acc = accounts.get(run_acc_id)
    override = run_acc_id != t["account_id"]
    run_eng = str((run_acc or {}).get("engine") or t.get("engine") or "").strip().lower()
    split_eng = str(((m.split or {}).get("rule") or {}).get("engine") or m.engine or "").strip().lower()
    if run_eng in ("flow", "grok", "studio") and want0:
        from . import split as _splitmod
        try:
            sp_res = _splitmod.resplit_segments_for_engine(
                job.task_id, want0, run_eng, log=job.log
            )
            if sp_res.get("want"):
                want0 = {int(x) for x in sp_res["want"]}
                segs = sorted(want0)
                m = Manifest.load(d)
        except Exception as e:  # noqa: BLE001
            job.log(f"  · 重生智能分段提示：{e}")
    if override:
        from ..engines import caps as _caps
        task_lab = {"flow": "Flow", "grok": "Grok", "studio": "本地模型"}.get(
            str(t.get("engine") or "").lower(), t.get("engine") or "?")
        run_lab = {"flow": "Flow", "grok": "Grok", "studio": "本地模型"}.get(run_eng, run_eng or "?")
        acc_model = str((run_acc or {}).get("model") or "").strip()
        model_note = f" · {acc_model}" if acc_model else ""
        job.log(f"这次重生改用账号「{(run_acc or {}).get('name') or run_acc_id}」（{run_lab}{model_note}），"
                f"任务本身仍是 {task_lab}，其它已收片段不受影响")
        for sg in m.segments:
            if int(sg.get("s") or 0) not in want0:
                continue
            d0 = int(sg.get("duration") or 8)
            try:
                d1 = int(_caps.snap_duration(run_eng, d0))
            except Exception:
                d1 = d0
            if d1 != d0:
                job.log(f"  · s{int(sg['s']):03d} 原本 {d0} 秒，{run_lab} 按 {d1} 秒出")
    # 当用户主动选用 Flow 账号（如 Flow1 Omni）重生时，先清理之前其它账号留下的旧拒收标记，
    # 让当前 Flow 账号真实尝试上传与生成；若本次仍被 Flow 拒绝，会自动触发 route_to_studio 转回本地模型。
    if run_eng == "flow":
        for sg in m.segments:
            if int(sg.get("s") or 0) not in want0:
                continue
            im = m.image(sg.get("image") or "")
            if im and (im.get("flow_upload_refused") or im.get("status") == "blocked" or im.get("studio_route")):
                im["status"] = "ok"
                im.pop("flow_upload_refused", None)
                im.pop("block_reason", None)
                im.pop("studio_route", None)
                im["gen_fail"] = 0
                im["content_fail"] = 0
        m.save(d)

    def _has_image(sg) -> bool:
        """有图 = manifest 里有这条记录，而且文件真的在盘上（同名别的扩展名也算）。"""
        im = m.image(sg.get("image") or "")
        if not im:
            return False
        return _resolve_image(d, im).is_file()

    no_img = [int(s["s"]) for s in m.segments
              if int(s["s"]) in want0 and not _has_image(s)]
    if no_img:
        # 文件已经不在盘上的图记录：先摘掉，免得替补出来的新图和它撞号
        dead = {s.get("image") for s in m.segments
                if int(s["s"]) in set(no_img) and s.get("image")}
        dead = {p for p in dead if p and (not m.image(p) or not _resolve_image(d, m.image(p)).is_file())}
        if dead:
            job.log(f"这些图的文件已经不在了，先摘掉：{'、'.join(sorted(dead))}")
            m.images = [im for im in m.images if im.get("p") not in dead]
            for sg in m.segments:
                if sg.get("image") in dead:
                    sg["image"] = ""
                    if not sg.get("prompt_locked"):
                        sg["prompt_final"] = ""
                        sg.pop("prompt_snap", None)
                        sg.pop("prompt_rev", None)
            m.save(d)
        # fill_missing=False：报错让前端给「去编排 / 生成新图替补」的选择
        if not fill_missing:
            raise OranteError(
                f"{_fmt_segs(no_img)} 没有图片",
                "到「编排」面板重新配图，或者点「生成新图替补」让 Flow 生新图直接配上。")
        from . import expand
        acc = accounts.get(t["account_id"])
        from ..engines.base import can_gen_images as _cgi
        if override and _cgi(run_eng):
            acc = run_acc       # 这个 Job 占着的是临时换的账号，能生图就用它
        elif not _cgi(str(acc.get("engine") or "")):
            # 本地模型当任务账号：它不能生图，替补图借扩图账号（跟「扩图」同一条规则）
            acc = accounts.get(expand.resolve_account_id(t, persist=False))
            job.log(f"  · 本地模型不能生图，替补图借账号「{acc.get('name') or acc.get('id')}」生")
        eng = make_engine(acc, job.log)
        try:
            eng.connect()
        except EngineUnavailable as e:
            raise OranteError(str(e), "到「账号」里点「登录 / 打开」，登好再点重生。")
        begin = getattr(eng, "begin_credits", None)
        if callable(begin):
            begin(job)
        job.log(f"{_fmt_segs(no_img)} 没有图片 → 先生成新图替补")
        try:
            r = expand.fill_segments(job, m, d, no_img, eng)
        finally:
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
        if r.get("unfilled"):
            job.log(f"  · {_fmt_segs(r['unfilled'])} 没补上，这次先跳过")
            segs = [x for x in want0 if x not in set(r["unfilled"])]
        m = Manifest.load(d)
    trash = d / "clips" / "_弃用"
    trash.mkdir(parents=True, exist_ok=True)
    want = {int(x) for x in (segs or [])}
    seen = set()
    for s in m.segments:
        if s["s"] not in want:
            continue
        p = s.get("image")
        if not p or p in seen:
            continue
        seen.add(p)
        im = m.image(p)
        if not im:
            continue
        gf = _gen_fail_of(m, p)
        if gf > int(im.get("gen_fail") or 0):
            im["gen_fail"] = gf
        if (run_eng not in ("grok", "studio")
                and int(im.get("gen_fail") or 0) >= 3 and _never_collected(m, p)):
            im["status"] = "blocked"
            job.log(f"  ✗ {p} 连续 {im.get('gen_fail')} 次生成失败、从没成功过 "
                    f"→ 判定被 Flow 拒了，标 blocked；点「补图」换一张")
        elif run_eng in ("grok", "studio") and im.get("status") == "blocked":
            im["status"] = "ok"
            im.pop("block_reason", None)
            job.log(f"  · {p} 用{'Grok' if run_eng == 'grok' else '本地模型'}重生，不按拒图换图，仍用原图")
    want_run = []
    for s in m.segments:
        if s["s"] not in want:
            continue
        im = m.image(s.get("image") or "")
        if im and im.get("status") == "blocked":
            job.log(f"  s{s['s']:03d} 的图 {s['image']} 已被拒，先补图")
        disc = [str(x).lower() for x in (s.get("user_discarded") or []) if x]
        for tk in s.get("takes") or []:
            pth = d / tk["file"]
            sha = str(tk.get("sha256") or "").lower()
            if not sha and pth.is_file():
                try:
                    sha = verifymod.sha256_file(pth).lower()
                except OSError:
                    sha = ""
            if sha:
                disc.append(sha)          # 用户亲手重生挪走的：以后谁也不许把它找回来
            if pth.exists():
                pth.rename(trash / f"{int(time.time())}-{pth.name}")
        if disc:
            s["user_discarded"] = disc[-20:]
            # 这些 sha 不再算「校验器误判作废」
            s["voided_sha"] = [x for x in (s.get("voided_sha") or []) if str(x).lower() not in set(disc)]
        s["takes"] = []
        s["status"] = "planned"
        s.pop("image_replaced", None)
        s["gen_id"] = None
        s["fail_count"] = 0
        s.pop("gen_via", None)
        s.pop("studio_job", None)
        # 规格 98：排给本地模型的段，用户手动选了谁重生就用谁（本地模型自己也走这里）
        s.pop("studio_pending", None)
        s.pop("studio_reason", None)
        pipeline.clear_bridge_acct(s)
        if s.get("role") == "bridge":
            # 接片走哪条路按这次用的引擎重新定：先清掉上次的，临时换引擎时直接写上新路
            s.pop("bridge_engine", None)
            if override and pipeline.is_anchor_mode(m):
                if run_eng == "grok":
                    s["bridge_engine"] = ("grok-single" if pipeline.GROK_BRIDGE_MODE == "single_tail"
                                          else "grok")
                elif run_eng == "flow":
                    s["bridge_engine"] = "flow"
        if s.get("prompt_locked"):
            job.log(f"  ⟲ s{s['s']:03d} 打回 planned（保留手写提示词）")
        else:
            s["prompt_final"] = ""      # 按当前提示词设置重拼
            s.pop("prompt_snap", None)
            s.pop("prompt_rev", None)
            job.log(f"  ⟲ s{s['s']:03d} 打回 planned")
        s["error"] = None
        s["check"] = {"issues": []}  # 旧片挪走，旧体检作废
        s["mark"] = False
        s.pop("fresh", None)
        want_run.append(s["s"])
    m.save(d)
    if not want_run:
        return {"submitted": 0}
    # 用户主动重生：片齐 / 生成中要拉回 arranged，再由 generate 推进到 generating。
    # 不带 force 的话单调性会把这次回退吞掉，界面还以为片齐。
    st = (taskmod.get(job.task_id) or {}).get("status") or ""
    if st in ("generating", "collected", "finished"):
        taskmod.set_status(job.task_id, "arranged", force=True)
        events.emit("tasks.changed")
    if override:
        # 临时换账号的这一轮不接力：没做成的段留给任务自己的账号，别让接力拿 Grok 去跑整个任务
        return generate(job, only=list(want_run), mark_fresh=True, chain=False, acc_id=run_acc_id)
    return generate(job, only=list(want_run), mark_fresh=True)
