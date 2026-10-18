# -*- coding: utf-8 -*-
"""多镜头扩图 / 被拒补图 / 提升画质。占这个任务的生图账号（见 resolve_account_id）。

expand：主体 ok 图不够就按用户勾的参考图生一批。提示词走 prompts.expand，
每张参考图问 Gemini 一次拿 n 句变体，每句各生 1 张。
refill：blocked 的 pX 用新图覆盖同名文件，配图轨指向不动，被拒段打回 planned。
upscale：拿糊图自己当参考原样重画，三道闸过了才换；不翻转、不重写描述语、不自动打回段。
"""
from __future__ import annotations

import json
import shutil
import time
import random
import re
from datetime import datetime
from pathlib import Path

from .. import accounts, config, db, events, locks, tasks as taskmod
from ..engines.base import BlockedImage, EngineUnavailable, can_gen_images, make_engine
from ..engines.flow_api import notify_credit_halt, wait_credit_continue
from ..errors import CreditHalted, OranteError
from ..jobs import register
from ..models import Manifest, sha1_file
from ..scheduler import JobCancelled
from . import ai, aikeys, broll, coverage, prompts
from .gemini import Gemini

IMG_DIR = "images"
BROLL_DIR = "空镜头"
CAND_DIR = "images/_候选"
DUMP_DIR = "images/_弃用"
REPL_DIR = "images/_replaced"
REJECT_DIR = "images/_不合格"  # 雷同 / 现场校验不合格，挪过来不删
UPSCALE_DIR = "images/_upscale"  # 提升画质候选；没过闸就留在这儿，不删

# 提升画质：新图 vs 原图 16×16 aHash 汉明距离 ≤ 这个才算同一张画面。
# 我们要的就是雷同——模型自己发挥画成别的东西就不换。
# 扩图的 expand.dup_dist 正好相反：那边小于就算雷同、要丢掉。别看混。
UPSCALE_SAME_DIST = 12
UPSCALE_AREA_MIN = 1.2   # 新图像素面积至少是原图的这么多倍，才算更清楚
UPSCALE_SHARP_MIN = 1.3  # 或者拉普拉斯方差至少是原图的这么多倍

# 扩图落盘后一次问完：现场有没有听众席 + 人是看听众还是看镜头
STAGE_PROMPT = (
    "看这张照片，只输出一个 JSON 对象，不要 markdown、不要解释：\n"
    '{"env": true或false, "facing": "听众"或"镜头"}\n\n'
    "env：这张照片里除了讲话的人，能不能看到台下的听众或者观众席的座位？"
    "能看到填 true，看不到填 false。\n"
    "facing：照片里这个人是在面向前方的听众讲话（填「听众」），"
    "还是在直视拍照的镜头（填「镜头」）。"
)

# Gemini 挂了时用的兜底句：不钉死景别，只要求换距离和角度
FALLBACK_SENTENCE = (
    "同一个人物，同样的服装、发型、姿态和表情，"
    "换一个明显不同的拍摄距离和角度，背景延续原图环境。"
)


def _cfg(key, default=None):
    try:
        return config.get(key, default)
    except Exception:
        from ..config import DEFAULTS
        return DEFAULTS.get(key, default)


def _quiet_rescan(tid: str, log=None) -> None:
    """扩图 / 留图之后静默按磁盘重建图库，编号对齐。失败只记日志，不把扩图判失败。"""
    try:
        from . import rescan as rescanmod
        r = rescanmod.apply(tid)
    except Exception as e:  # noqa: BLE001
        msg = f"静默重扫没跑成：{type(e).__name__}: {e}"
        if callable(log):
            log(msg)
        return
    added = len(r.get("added") or [])
    renamed = len(r.get("renamed") or [])
    if callable(log):
        log(f"已静默重扫图库：新增 {added} · 改号 {renamed}")


def _task_dir(tid: str) -> Path:
    t = taskmod.get(tid)
    if not t.get("dir"):
        raise OranteError("这个任务还没准备", "先在任务面板点「准备」。")
    d = Path(t["dir"])
    if not d.is_dir():
        raise OranteError("任务文件夹不在了", str(d))
    return d


def _prog(job, **kw) -> None:
    fn = getattr(job, "set_progress", None)
    if callable(fn):
        fn(**kw)


def _borrow_flow(task: dict, persist: bool = True) -> str:
    """给不能自己生图的引擎借一个 Flow 账号，并固定下来（现在只是兜底：Flow 和
    Grok 都能自己生图了，走到这里说明任务没账号，或它的账号停用了）。

    挑法：先排除此刻正被别的任务占着的（locks.busy()），再挑「被借得最少」的，
    同分按 id 定序。挑中就写进 tasks.expand_account_id，以后这个任务永远用它，
    不会今天一个明天一个。
    """
    flows = [a for a in accounts.list_all()
             if a.get("engine") == "flow" and a.get("enabled")]
    if not flows:
        raise OranteError(
            "没有启用的 Flow 账号",
            f"{task.get('name') or task.get('id')} 是 {task.get('engine')} 任务，"
            "自己不能生图，扩图要借一个 Flow。到右上角「账号」加一个 Flow 并登录。")
    # 「已经有人用」= 别的活任务把它当扩图号借走了，或者本来就是某个 Flow 任务
    # 自己的出片号。两种都算，借的时候尽量挑最闲的那个，别去挤已经有活的账号。
    used: dict[str, int] = {}
    try:
        for col in ("expand_account_id", "account_id"):
            for r in db.rows(
                    f"SELECT {col} aid, COUNT(*) n FROM tasks"
                    f" WHERE {col} IS NOT NULL AND id<>?"
                    " AND status NOT IN ('finished','trash')"
                    f" GROUP BY {col}", (task.get("id"),)):
                used[r["aid"]] = used.get(r["aid"], 0) + r["n"]
    except Exception:
        pass
    taken = locks.busy()
    free = [a for a in flows if a["id"] not in taken] or flows
    free.sort(key=lambda a: (used.get(a["id"], 0), a["id"]))
    aid = free[0]["id"]
    if persist:
        try:
            db.run("UPDATE tasks SET expand_account_id=? WHERE id=?", (aid, task["id"]))
        except Exception:
            pass
    return aid


def resolve_account_id(task: dict, persist: bool = True) -> str:
    """扩图/补图用哪个账号。

    顺序：① 任务自己指定的 expand_account_id ② 任务本身的账号（Flow 用 Flow、
    Grok 用 Grok，绝不去挤别人的 Chrome）③ 它的引擎不能生图或账号停用了 →
    借一个最闲的 Flow 号并记住。0907 那次崩溃就是因为这里第 ② 步不存在，
    四个任务全落到了「第一个启用的 Flow」同一个账号上。
    """
    aid = (task.get("expand_account_id") or "").strip()
    if aid:
        if accounts.is_virtual(aid) and not accounts.studio_enabled():
            config.set("engine.studio.enabled", True)
        a = accounts.get(aid)
        if not can_gen_images(a.get("engine")):
            raise OranteError(
                f"{a.get('name') or aid}（{a.get('engine')}）不能生图",
                "扩图账号换成 Flow 或 Grok。")
        if not a.get("enabled") and not accounts.is_virtual(aid):
            raise OranteError(
                f"扩图账号 {a.get('name') or aid} 已停用",
                "到账号抽屉启用，或清空 expand_account_id 让它自己挑。")
        return a["id"]
    own = (task.get("account_id") or "").strip()
    if own and not accounts.is_virtual(own):
        try:
            a = accounts.get(own)
        except OranteError:
            a = None
        # 优先使用付费购买的 Flow / Grok 账号，保证速度与效率最高；本地 AI 仅在手动选择时使用
        if a and a.get("enabled") and can_gen_images(a.get("engine")):
            return a["id"]
    return _borrow_flow(task, persist)


def account_hint(task) -> dict:
    """给界面看的：这次扩图会落到哪个账号。不落库、不抛错。

    返回 {id, name, engine}；算不出来（没账号、任务没准备）就返回 {}。
    """
    try:
        t = taskmod.get(task) if isinstance(task, str) else task
        a = accounts.get(resolve_account_id(t, persist=False))
        return {"id": a["id"], "name": a.get("name") or a["id"], "engine": a.get("engine")}
    except Exception:  # noqa: BLE001
        return {}


def _acc_brief(a: dict, *, alive=None) -> dict:
    return {
        "id": a["id"],
        "name": a.get("name") or a["id"],
        "engine": a.get("engine") or "",
        "alive": bool(a.get("alive") if alive is None else alive),
    }


def expand_account_state(task_id: str) -> dict:
    """扩图 / 补图 / 提升画质用哪个账号，给下拉用。

    current.source：
      pinned   用户在这里钉过 expand_account_id
      task     跟着任务自己的账号
      borrowed 任务引擎不能生图，系统借的（下拉里要标出来）
    """
    t = taskmod.get(task_id)
    listed = list(accounts.list_all(probe=False))
    st_pub = accounts.studio_public(probe=False)
    st_pub["enabled"] = 1  # 始终作为手动可选项展示在列表末尾，默认仍优先走付费 Flow/Grok
    listed.append(st_pub)
    by_id = {a["id"]: a for a in listed}
    options = []
    for a in listed:
        if not a.get("enabled") and not accounts.is_virtual(a):
            continue
        if not can_gen_images(a.get("engine")):
            continue
        rec = _acc_brief(a)
        rec["can_gen"] = True
        options.append(rec)
    try:
        from .. import locks
        q = locks.queue_view()
        mine = int(locks.OWNER_PORT or 0)
        for rec in options:
            run = (q.get(rec["id"]) or {}).get("running")
            if not run:
                continue
            rec["owner_port"] = run.get("owner_port")
            rec["lock"] = run
            port = int(run.get("owner_port") or 0)
            rec["lock_mine"] = bool(port and port == mine)
    except Exception:
        pass
    pinned = (t.get("expand_account_id") or "").strip()
    own = (t.get("account_id") or "").strip()
    resolved = resolve_account_id(t, persist=False)
    acc = by_id.get(resolved) or accounts.get(resolved)
    if pinned:
        source = "pinned"
    elif own and own == resolved:
        source = "task"
    else:
        source = "borrowed"
    current = _acc_brief(acc)
    current["source"] = source
    task_acc = None
    if own:
        oa = by_id.get(own)
        if oa is None:
            try:
                oa = accounts.get(own)
            except OranteError:
                oa = None
        if oa:
            task_acc = _acc_brief(oa)
    return {"current": current, "options": options, "task_account": task_acc}


def set_expand_account(task_id: str, account_id) -> dict:
    """钉扩图账号。空字符串 / None = 跟随任务账号（把 expand_account_id 置空）。"""
    taskmod.ensure_writable(task_id)
    if account_id is None or (isinstance(account_id, str) and not str(account_id).strip()):
        taskmod.set_expand_account_id(task_id, None)
        return expand_account_state(task_id)
    aid = str(account_id).strip()
    if accounts.is_virtual(aid) and not accounts.studio_enabled():
        config.set("engine.studio.enabled", True)
    a = accounts.get(aid)
    name = a.get("name") or aid
    if not can_gen_images(a.get("engine")):
        raise OranteError(
            f"{name}（{a.get('engine')}）不能生图",
            "扩图账号换成 Flow、Grok 或本地 AI。")
    if not a.get("enabled") and not accounts.is_virtual(aid):
        raise OranteError(
            f"扩图账号 {name} 已停用",
            "到账号抽屉启用，或清空 expand_account_id 让它自己挑。")
    taskmod.set_expand_account_id(task_id, aid)
    return expand_account_state(task_id)


def _ok_subjects(m: Manifest) -> list[dict]:
    return [im for im in m.images
            if im.get("role") == "主体" and im.get("status") == "ok"]


def _ready_subjects(m: Manifest) -> list[dict]:
    """ok + 待审，都算已经占了名额（待审还没进轨，但不必再扩）。"""
    return [im for im in m.images
            if im.get("role") == "主体"
            and im.get("status") in ("ok", "pending_review")]


def _next_p(images) -> str:
    n = 0
    for im in images:
        raw = str(im.get("p") or "")
        if raw.lower().startswith("p"):
            raw = raw[1:]
        try:
            n = max(n, int(raw))
        except (TypeError, ValueError):
            pass
    return f"p{n + 1:02d}"


def _picked_refs(m: Manifest, refs=None) -> tuple[list[dict], str]:
    """扩图参考图：优先用户勾的 expand_ref；一张都没勾就退回全部 ok 主体。"""
    ok = _ok_subjects(m)
    if refs:
        want = {str(x).strip() for x in refs if str(x).strip()}
        picked = [im for im in ok if im.get("p") in want]
        if picked:
            return picked, "args"
    flagged = [im for im in ok if im.get("expand_ref") is True]
    if flagged:
        return flagged, "flag"
    return ok, "all"


def ensure_default_expand_ref(task_id: str) -> None:
    """从没勾过参考图时，默认只勾第一张 ok 主体。用户主动全不勾则不动。"""
    d = _task_dir(task_id)
    m = Manifest.load(d)
    ok = _ok_subjects(m)
    if not ok:
        return
    if any("expand_ref" in im for im in ok):
        return
    ok[0]["expand_ref"] = True
    m.save(d)


def set_expand_ref(task_id: str, p: str, flag) -> dict:
    """勾/取消一张图当扩图参考。只有 ok 主体能勾成 True。"""
    p = (p or "").strip()
    d = _task_dir(task_id)
    m = Manifest.load(d)
    im = m.image(p)
    if not im:
        raise OranteError(f"没有图片 {p}")
    want = flag in (True, 1, "1", "true", "True", "yes", "on")
    if want:
        if im.get("role") != "主体" or im.get("status") != "ok":
            raise OranteError(
                f"{p} 不能当扩图参考",
                "只有状态正常的主体图可以勾。空镜、被拒、待审都不行。")
        im["expand_ref"] = True
    else:
        im["expand_ref"] = False
    m.save(d)
    return im


def _refill_clause(refill: bool) -> str:
    if not refill:
        return ""
    clause = prompts.get_block("refill_clause")
    if not isinstance(clause, str):
        clause = prompts.REFILL_CLAUSE_DEFAULT
    return (clause or "").strip()


def _prompt_block(refill: bool) -> str:
    """永远取 expand 那一块；补位时把 refill_clause 插到 head 之后。

    覆盖库：拼一条成品提示词（验收日志看前 60 字）。
    旧字符串：整块正文，clause 插在 head 后面。
    """
    raw = prompts.get_block("expand")
    if coverage.is_lib(raw):
        clause = _refill_clause(refill)
        picks = coverage.pick(raw, 1, clause=clause, no_frontal=_no_frontal())
        if picks:
            return picks[0]["prompt"]
        lib = coverage.sanitize(raw)
        return str(lib.get("head") or "") + clause + str(lib.get("tail") or "")
    if not isinstance(raw, str) or not raw.strip():
        lib = coverage.default_lib()
        clause = _refill_clause(refill)
        picks = coverage.pick(lib, 1, clause=clause, no_frontal=_no_frontal())
        return picks[0]["prompt"] if picks else (clause or FALLBACK_SENTENCE)
    if refill:
        return prompts.with_refill_clause(raw)
    return raw


def _dup_dist() -> int:
    """扩图雷同阈值：汉明距离小于这个就算雷同、要丢掉。

    注意方向：提升画质的 UPSCALE_SAME_DIST 正好相反——那边要的就是雷同
    （距离 ≤ 12 才换图）。这两个常量含义相反，别看混。
    T0014 实测近孪生 p10↔p11 = 76，取 90 切开那一簇。
    """
    return max(1, int(_cfg("expand.dup_dist", 90) or 90))


def _no_frontal() -> bool:
    """关掉正脸，默认开。"""
    v = _cfg("expand.no_frontal", True)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    return str(v or "").strip().lower() in ("1", "true", "yes", "on", "是")


def _img_hash(fp: Path) -> int | None:
    from .verify import ahash_image
    return ahash_image(fp)


def _pool_hashes(m: Manifest, d: Path) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for im in m.images:
        fp = d / (im.get("file") or "")
        if not fp.is_file():
            continue
        h = _img_hash(fp)
        if h is not None:
            out.append((str(im.get("p") or fp.name), h))
    return out


def _nearest_dup(h: int, pool: list[tuple[str, int]]) -> tuple[str | None, int]:
    from .verify import hamming
    best_p, best_d = None, 999
    for p, ph in pool:
        d = hamming(h, ph)
        if d < best_d:
            best_d, best_p = d, p
    return best_p, best_d


def parse_stage(raw) -> dict:
    """把现场校验 JSON 收成 {env: bool, facing: 听众|镜头}。"""
    text = (raw if isinstance(raw, str) else str(raw or "")).strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    data = None
    try:
        data = json.loads(text) if text else {}
    except Exception:
        data = {}
    if isinstance(data, list):
        data = next((x for x in data if isinstance(x, dict)), {})
    if not isinstance(data, dict):
        data = {}
    env = data.get("env")
    if isinstance(env, str):
        env = env.strip() in ("是", "true", "True", "1", "yes", "YES")
    elif env is None:
        # 模型没按 JSON 填时，从原文里找 是/否
        if re.search(r"env[^，。\n]{0,8}否|看不到", text):
            env = False
        elif re.search(r"env[^，。\n]{0,8}是|能看到", text):
            env = True
        else:
            env = True  # 看不清不误杀
    else:
        env = bool(env)
    facing = str(data.get("facing") or "").strip()
    if "镜头" in facing:
        facing = "镜头"
    elif "听众" in facing:
        facing = "听众"
    else:
        if re.search(r"直视.*镜头|看向镜头|看镜头", text):
            facing = "镜头"
        else:
            facing = "听众"
    return {"env": bool(env), "facing": facing}


def skip_env_check(cov: dict | None) -> bool:
    """手部特写背景本来就该虚化，不查听众席。"""
    cov = cov if isinstance(cov, dict) else {}
    cid = str(cov.get("camera") or "").strip()
    name = str(cov.get("camera_name") or cov.get("name") or "")
    return cid == "11" or "手部特写" in name


def stage_fail_reason(verdict: dict | None, cov: dict | None) -> str | None:
    """不合格原因；合格返回 None。env 优先（硬指标）。"""
    verdict = verdict if isinstance(verdict, dict) else {}
    cov = cov if isinstance(cov, dict) else {}
    if not skip_env_check(cov) and not verdict.get("env"):
        return "看不到听众席"
    frontal = bool(cov.get("frontal"))
    if verdict.get("facing") == "镜头" and not frontal:
        return "直视镜头"
    return None


def check_stage(gemini, img_path, cov: dict | None, log=None) -> str | None:
    """问一次多模态。返回不合格原因；合格或问失败返回 None（问失败不挡流程）。"""
    if gemini is None:
        return None
    try:
        raw = gemini.ask_image(STAGE_PROMPT, Path(img_path), json_mode=True,
                               temperature=0.0)
        verdict = parse_stage(raw)
    except Exception as e:  # noqa: BLE001
        if callable(log):
            try:
                log(f"  · 现场校验没做成：{type(e).__name__}: {e}")
            except Exception:
                pass
        return None
    return stage_fail_reason(verdict, cov)


def _stash_reject(d: Path, fp: Path, p: str, reason: str) -> Path | None:
    """不合格图挪进 images/_不合格/，不删。"""
    fp = Path(fp)
    if not fp.is_file():
        return None
    dest_dir = Path(d) / REJECT_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r'[\\/:*?"<>|\s]+', "", reason or "")[:24] or "不合格"
    dest = dest_dir / f"{p}-{safe}-{stamp}{fp.suffix or '.jpg'}"
    n = 1
    while dest.exists():
        n += 1
        dest = dest_dir / f"{p}-{safe}-{stamp}-{n}{fp.suffix or '.jpg'}"
    _move_file(fp, dest)
    return dest


def _coverage_picks(n: int, m: Manifest, refill: bool, avoid=None,
                    far_from=None) -> list[dict] | None:
    raw = prompts.get_block("expand")
    if not coverage.is_lib(raw):
        return None
    kw = dict(
        used_across=coverage.used_pairs(m.images),
        avoid=avoid,
        clause=_refill_clause(refill),
        no_frontal=_no_frontal(),
    )
    if far_from:
        return coverage.pick_farthest(raw, n, far_from, **kw)
    return coverage.pick(raw, n, **kw)


def _clean_sentence(s) -> str:
    text = (s if isinstance(s, str) else str(s or "")).strip()
    text = text.strip("「」\"'“”").replace("\n", "").strip()
    return text


def _parse_variants(raw, n: int) -> list[str]:
    """从 Gemini 的 JSON 里抽出最多 n 句。"""
    data = raw
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
            text = text.strip()
        try:
            data = json.loads(text)
        except Exception:
            return []
    arr = None
    if isinstance(data, dict):
        for k in ("variants", "sentences", "prompts", "lines"):
            if isinstance(data.get(k), list):
                arr = data[k]
                break
    elif isinstance(data, list):
        arr = data
    if not isinstance(arr, list):
        return []
    out = []
    for x in arr:
        s = _clean_sentence(x)
        if s:
            out.append(s)
        if len(out) >= n:
            break
    return out


# ---------------- 槽位模式 ----------------
_SLOT_RE = re.compile(r"^\s*([^\s:：#|{}]+)\s*[:：]\s*(.+?)\s*$")


def _parse_slots(block: str) -> tuple[str, dict[str, list[tuple[str, float]]]]:
    """把提示词块拆成 (模板, {槽位名: [(选项, 权重), ...]})。

    「名字: a | b*3 | c」这样的行是槽位；# 开头是注释；其余行拼成模板。
    只有模板里真用到（{名字}）的槽位才算数。
    """
    tmpl_lines: list[str] = []
    slots: dict[str, list[tuple[str, float]]] = {}
    for line in (block or "").splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        mm = _SLOT_RE.match(line)
        if mm and "|" in mm.group(2):
            opts: list[tuple[str, float]] = []
            for part in mm.group(2).split("|"):
                part = part.strip()
                if not part:
                    continue
                w = 1.0
                mw = re.match(r"^(.*?)\s*[*×xX]\s*(\d+(?:\.\d+)?)$", part)
                if mw:
                    part, w = mw.group(1).strip(), float(mw.group(2))
                if part:
                    opts.append((part, max(w, 0.0)))
            if opts:
                slots[mm.group(1)] = opts
            continue
        tmpl_lines.append(line.strip())
    tmpl = " ".join(tmpl_lines).strip()
    used = {k: v for k, v in slots.items() if "{" + k + "}" in tmpl}
    return tmpl, used


def _slot_variants(block: str, n: int) -> list[str]:
    """按槽位权重抽 n 个互不相同的组合，填进模板。没有槽位返回 []。"""
    tmpl, slots = _parse_slots(block)
    if not slots:
        return []
    names = list(slots)
    combos: list[tuple[tuple[str, ...], float]] = [((), 1.0)]
    for k in names:
        combos = [(c + (opt,), w * ow) for c, w in combos for opt, ow in slots[k]]
    combos = [(c, w) for c, w in combos if w > 0] or combos
    out: list[str] = []
    pool = list(combos)
    for _ in range(max(1, int(n))):
        if not pool:
            pool = list(combos)  # 组合数不够就允许重复一轮
        weights = [w for _, w in pool]
        pick = random.choices(range(len(pool)), weights=weights, k=1)[0]
        c, _w = pool.pop(pick)
        text = tmpl
        for k, v in zip(names, c):
            text = text.replace("{" + k + "}", v)
        out.append(_clean_sentence(text))
    return out


def _list_lines(block: str) -> list[str]:
    """列表模式：每个非空、非注释、非槽位定义的行就是一条完整提示词。少于 2 行不算列表。"""
    tmpl, slots = _parse_slots(block)
    if slots:
        return []
    out = []
    for line in (block or "").splitlines():
        t = line.strip()
        if not t or t.startswith("#"):
            continue
        t = _clean_sentence(t)
        if t:
            out.append(t)
    return out if len(out) >= 2 else []


def _list_variants(block: str, n: int) -> list[str]:
    """从列表里随机抽 n 条互不相同的（不够就整轮重抽）。没有列表返回 []。"""
    lines = _list_lines(block)
    if not lines:
        return []
    out: list[str] = []
    pool: list[str] = []
    for _ in range(max(1, int(n))):
        if not pool:
            pool = list(lines)
            random.shuffle(pool)
        out.append(pool.pop())
    return out


def _slot_single(block: str) -> str | None:
    v = _slot_variants(block, 1) or _list_variants(block, 1)
    return v[0] if v else None


# 提示词里的 {场景} 占位：按参考图的 scene 标签换成具体的场地说法，让 Flow 死守原背景
_SCENE_TEXT = {
    "舞台": "同一个大型礼堂的舞台，身后是同样的木质墙面、大屏幕和满座的观众席",
    "围绕": "同一个被人群围在中间的现场，周围是同样的那群人",
    "常规": "上传图像里同一个场地",
}


def _scene_text(m: Manifest, img: Path) -> str:
    """找到参考图记录，按它的 scene 给 {场景} 一句具体描述；找不到就用通用说法。"""
    try:
        want = Path(img).name.lower()
        for im in m.images:
            if Path(im.get("file") or "").name.lower() == want:
                sc = str(im.get("scene") or "").strip()
                return _SCENE_TEXT.get(sc) or _SCENE_TEXT["常规"]
    except Exception:
        pass
    return _SCENE_TEXT["常规"]


def _with_scene(prompts: list[str], scene: str | None) -> list[str]:
    if not scene:
        return prompts
    return [p.replace("{场景}", scene) for p in prompts]


def _ask_variants(gemini: Gemini, img: Path, n: int, refill: bool = False,
                  scene: str | None = None) -> tuple[list[str], str]:
    """问 Gemini 一口气给 n 句。返回 (句子列表, 'multi'|'single')。

    Gemini 失败或 JSON 不合格时退回一句兜底，调用方改用「一句生 n 张」。
    """
    n = max(1, int(n))
    block = _prompt_block(refill)
    # 块里有「景别: a | b」这样的槽位行 → 槽位模式（默认）：
    # 每张各抽一个不重复的组合填进模板，各自单独发 Flow 生 1 张，不问 Gemini
    if "{n}" not in block:
        sv = _slot_variants(block, n) or _list_variants(block, n)
        if sv:
            return _with_scene(sv, scene), "multi"
    # 没槽位也没 {n} → 一句直接发给 Flow，一句生 n 张，靠 Flow 自己的随机性
    if "{n}" not in block:
        return _with_scene([_clean_sentence(block) or FALLBACK_SENTENCE], scene), "direct"
    tmpl = block.replace("{n}", str(n))
    try:
        raw = gemini.ask_image(tmpl, img, json_mode=True, temperature=0.7)
        variants = _parse_variants(raw, n)
        if len(variants) >= 2 or (n == 1 and variants):
            return _with_scene(variants[:n], scene), "multi"
        if len(variants) == 1:
            return _with_scene(variants, scene), "single"
    except OranteError:
        pass
    except Exception:
        pass
    return _with_scene([FALLBACK_SENTENCE], scene), "single"


def _move_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() == dest.resolve():
        return
    if dest.is_file():
        dest.unlink()
    if src.is_file():
        shutil.move(str(src), str(dest))


def _lab_ss(nums) -> str:
    """段号列表写成 's003 s005 s008'，给换图日志用。"""
    xs = sorted({int(n) for n in (nums or []) if n})
    return " ".join(f"s{n:03d}" for n in xs)


def fmt_reset_log(p: str, reset, kept) -> str:
    """· 换图 p07：s003 s005 s008 已收片，保留不动；s011 打回重做"""
    bits = []
    if kept:
        bits.append(f"{_lab_ss(kept)} 已收片，保留不动")
    if reset:
        bits.append(f"{_lab_ss(reset)} 打回重做")
    if not bits:
        return ""
    return f"· 换图 {p}：" + "；".join(bits)


def _reset_refs(m: Manifest, p: str, *, force_collected: bool = False,
                only_kept: bool = False, old_file: str = "") -> dict:
    """图被覆盖后：未完成的引用段打回 planned；已收片的默认保留。

    collected = 这一段已经有成片了，而且那条片子是用旧图生成的、旧图还在
    images/_replaced/ 里躺着。图池换了张新图，不该让已经做好的片子作废重来——
    用户为这个白跑过 4 条。只有还没做完的段才需要按新图重做。

    force_collected=True：用户点了「这 N 段也按新图重做」/「打回这 N 段重生」才走。
    only_kept=True：只动带 image_replaced 记号的 collected 段。
    submitted 正在跑，只清提示词不动状态。
    手写锁定的段不清空 prompt_final。
    返回 {"reset": [打回的段号], "kept": [已收片保留的段号]}。
    """
    reset, kept = [], []
    n_locked = 0
    at = datetime.now().isoformat(timespec="seconds")
    old_rel = (old_file or "").replace("\\", "/")
    for sg in m.segments:
        if sg.get("image") != p:
            continue
        if only_kept and not sg.get("image_replaced"):
            continue
        st = sg.get("status")
        sn = int(sg.get("s") or 0)
        if st == "collected" and not force_collected:
            # collected：状态不动、prompt_final 也不清（那是这条片子当时用的提示词）
            sg["image_replaced"] = {"p": p, "at": at, "old_file": old_rel}
            if sn:
                kept.append(sn)
            continue
        if sg.get("prompt_locked"):
            n_locked += 1
        else:
            sg["prompt_final"] = ""
        if st in ("failed", "blocked", "done") or (st == "collected" and force_collected):
            sg["status"] = "planned"
            sg["error"] = None
            sg["fail_count"] = 0
            sg["mismatch_count"] = 0
            sg["timeout_count"] = 0
            sg["thumb_count"] = 0
            sg.pop("image_replaced", None)
            if sn:
                reset.append(sn)
    if n_locked:
        m._n_prompt_locked = n_locked  # type: ignore[attr-defined]
    return {"reset": reset, "kept": kept}


def _new_image_rec(p: str, rel: str, role: str, status: str, origin: str,
                   sha1: str = "", cov: dict | None = None,
                   dup_warn: bool = False, stage_warn: bool = False) -> dict:
    rec = {
        "p": p, "file": rel.replace("\\", "/"), "sha1": sha1,
        "role": role, "status": status, "desc": "", "voice": "",
        "eye": "空镜" if role == "空镜" else "闭眼",
        "scene": "常规", "measure": {}, "origin": origin, "src": "",
        "expand_ref": False, "pose": {},
    }
    if cov:
        rec["cov"] = dict(cov)
    if dup_warn:
        rec["dup_warn"] = True
    if stage_warn:
        rec["stage_warn"] = True
    return rec


def _ingest_new(job, m: Manifest, task_dir: Path, src: Path, gemini: Gemini,
                origin: str = "expand", cov: dict | None = None,
                dup_warn: bool = False, stage_warn: bool = False) -> dict:
    """一张新图：空镜判定 → 搬家 → 写进 manifest。返回新记录。"""
    p = _next_p(m.images)
    judged = broll.judge(gemini, src)
    if judged.get("is_broll"):
        rel = f"{BROLL_DIR}/{p}{src.suffix or '.jpg'}"
        _move_file(src, task_dir / rel)
        rec = _new_image_rec(p, rel, "空镜", "ok", origin,
                             sha1_file(task_dir / rel) if (task_dir / rel).is_file() else "",
                             cov=cov, dup_warn=dup_warn, stage_warn=stage_warn)
        rec["broll_check"] = judged
        rec["broll_reason"] = judged.get("reason") or ""
        m.images.append(rec)
        m.save(task_dir)
        job.log(f"{p} 判为空镜（{judged.get('reason') or ''}），进 {rel}")
        return rec
    rel = f"{CAND_DIR}/{p}{src.suffix or '.jpg'}"
    _move_file(src, task_dir / rel)
    rec = _new_image_rec(p, rel, "主体", "pending_review", origin,
                         sha1_file(task_dir / rel) if (task_dir / rel).is_file() else "",
                         cov=cov, dup_warn=dup_warn, stage_warn=stage_warn)
    m.images.append(rec)
    m.save(task_dir)
    tag = " ⚠雷同" if dup_warn else (" ⚠不合格" if stage_warn else "")
    job.log(f"{p} 待审 → {rel}{tag}")
    return rec


def _item(p, thumb, role, ref, variant, prompt) -> dict:
    return {
        "p": p, "thumb": thumb, "role": role,
        "ref": ref, "variant": variant, "prompt": prompt or "",
    }


def _connect_engine(job):
    """连上这个任务扩图要用的账号（Flow 或 Grok，谁的任务用谁的号）。"""
    t = taskmod.get(job.task_id)
    aid = job.account_id or resolve_account_id(t)
    acc = accounts.get(aid)
    name = acc.get("name") or aid
    if not can_gen_images(acc.get("engine")):
        raise OranteError(f"{name}（{acc.get('engine')}）不能生图", "换一个 Flow 或 Grok 账号。")
    _prog(job, phase="connect",
          current=f"连接 {acc.get('engine')}（账号 {name}）")
    eng = make_engine(acc, job.log)
    try:
        eng.connect()
    except EngineUnavailable as e:
        raise OranteError(str(e), f"到「账号」里点「登录 / 打开」，把 {name} 登好再扩图。")
    begin = getattr(eng, "begin_credits", None)
    if callable(begin):
        begin(job)
    return eng


# 老名字还留着，别的地方引用不至于断
_connect_flow = _connect_engine


def _gen_one(eng, prompt: str, img: Path, cand_dir: Path, job=None,
             seed: int | None = None) -> Path | None:
    """生 1 张。没出图返回 None。参考图上传被拒时抛 BlockedImage，由调用方处理。"""
    kw = {}
    if seed is not None:
        kw["seed"] = int(seed)
    try:
        tick = getattr(eng, "tick_credits", None)
        if callable(tick):
            tick()
        paths = eng.gen_images(prompt, ref=img, n=1, dest_dir=cand_dir, **kw) or []
    except TypeError:
        # 假引擎 / 旧签名不认 seed
        paths = eng.gen_images(prompt, ref=img, n=1, dest_dir=cand_dir) or []
    except CreditHalted as e:
        notify_credit_halt(job, eng, e)
        wait_credit_continue(job, eng)
        try:
            paths = eng.gen_images(prompt, ref=img, n=1, dest_dir=cand_dir, **kw) or []
        except TypeError:
            paths = eng.gen_images(prompt, ref=img, n=1, dest_dir=cand_dir) or []
    for fp in paths:
        fp = Path(fp)
        if fp.is_file():
            return fp
    return None


def _gen_one_cov(job, eng, img: Path, cand_dir: Path, pick: dict,
                 pool: list, m: Manifest, d: Path, refill: bool,
                 avoid: set, gemini=None, seed: int | None = None,
                 far_from=None) -> tuple[Path | None, dict, bool, bool]:
    """按一条 (机位,动作) 生图；太像或现场不合格就换组合重生，最多 2 次。

    不合格图挪进 images/_不合格/，不删。返回 (文件, pick, 雷同标黄, 现场标黄)。
    """
    T = _dup_dist()
    last = pick
    avoid.add(pick["key"])
    cheap = gemini
    for attempt in range(3):
        fp = _gen_one(eng, last["prompt"], img, cand_dir, job=job, seed=seed)
        if fp is None:
            return None, last, False, False
        p_new = _next_p(m.images)
        cov = last.get("cov") or {}
        why = None
        h = _img_hash(fp)
        if h is not None:
            near, dist = _nearest_dup(h, pool)
            if near is not None and dist < T:
                why = f"和 {near} 太像（距离 {dist}）"
        if why is None:
            # FakeGemini 没有 cheap 属性，照用；正式 PooledClient 非 cheap 就另借最便宜那档
            if cheap is None or getattr(cheap, "cheap", True) is False:
                cheap = ai.client(job, cheap=True)
            why = check_stage(cheap, fp, cov, log=getattr(job, "log", None))
        if why is None:
            if h is not None:
                pool.append((p_new, h))
            return fp, last, False, False
        is_dup = "太像" in (why or "")
        if attempt >= 2:
            job.log(f"✗ {p_new} {why}，两次重生仍不合格，留下并标黄")
            if h is not None:
                pool.append((p_new, h))
            return fp, last, is_dup, (not is_dup)
        nxt = _coverage_picks(1, m, refill, avoid=avoid, far_from=far_from)
        if not nxt:
            job.log(f"✗ {p_new} {why}，没有新组合可换，留下并标黄")
            if h is not None:
                pool.append((p_new, h))
            return fp, last, is_dup, (not is_dup)
        nxt0 = nxt[0]
        ncam = (nxt0.get("cov") or {}).get("camera") or "?"
        nact = (nxt0.get("cov") or {}).get("action") or "?"
        job.log(f"✗ {p_new} {why}，换 {ncam}×{nact} 重生")
        _stash_reject(d, fp, p_new, why)
        last = nxt0
        avoid.add(last["key"])
    return None, last, False, False


def _mark_ref_blocked(job, m: Manifest, d: Path, ref: dict, ref_p: str) -> None:
    """参考图被 Flow 拒收：标 blocked，任务继续。"""
    ref["status"] = "blocked"
    m.save(d)
    taskmod.emit_seg_changed(job.task_id, taskmod.remember_counts(d, m))
    job.log(f"  ✗ 参考图 {ref_p} 被平台判违规 → 已标 blocked，跳过它；点「补图」会用 Gemini 换机位重生一张")
    _prog(job, item=_item(None, None, "被拒", ref_p, 0, ""))


def _collected_scores(m: Manifest) -> dict:
    scores = {}
    for sg in m.segments:
        if sg.get("status") == "collected" and sg.get("image"):
            p = sg["image"]
            scores[p] = scores.get(p, 0) + 1
    return scores


def _ref_candidates(m: Manifest, d: Path, p: str, *, prefer_self: bool = False) -> list[dict]:
    """补图参考：优先已有收片的 ok 主体（按成功段数多到少），没有就任意 ok 主体。

    prefer_self=True 时（手动补图），自己那张文件还在就放最前；自动换图不用自己（刚被拒）。
    """
    scores = _collected_scores(m)
    others, own = [], None
    for im in m.images:
        rel = im.get("file") or ""
        fp = d / rel
        if not fp.is_file():
            continue
        if im.get("p") == p:
            own = im
            continue
        if im.get("role") == "主体" and im.get("status") == "ok":
            others.append(im)
    others.sort(key=lambda im: (-scores.get(im.get("p"), 0), str(im.get("p") or "")))
    if prefer_self and own is not None:
        return [own] + others
    return others


def preview_ref_p(m: Manifest, d: Path, p: str) -> str:
    """给日志用的第一候选参考图号。"""
    refs = _ref_candidates(m, d, p, prefer_self=False)
    return str(refs[0].get("p") or "?") if refs else "?"


def _refs_for_attempt(refs: list[dict], attempt: int, last_ref: str | None) -> list[dict]:
    """第 1 次用原顺序；第 2/3 次把上次用过的参考挪到最后。"""
    refs = [r for r in (refs or []) if r]
    if not refs:
        return []
    attempt = max(1, int(attempt or 1))
    if attempt <= 1 or not last_ref:
        return list(refs)
    last = str(last_ref)
    others = [r for r in refs if str(r.get("p") or "") != last]
    same = [r for r in refs if str(r.get("p") or "") == last]
    return others + same


@register("expand", account_bound=True)
def expand(job, target: int | None = None, refs=None):
    """主体 ok 图不够就扩。新图 pending_review，不进配图轨。

    refs：可选，显式指定参考图号列表；否则用勾了 expand_ref 的，再否则全部 ok 主体。
    """
    if not job.task_id:
        raise OranteError("expand 需要 task_id")
    d = _task_dir(job.task_id)
    m = Manifest.load(d)
    if target is None:
        target = int(_cfg("expand.min_images", 6) or 6)
    target = max(1, int(target))
    per_round = max(1, int(_cfg("expand.per_round", 4) or 4))

    ok_n = len(_ok_subjects(m))
    if ok_n >= target:
        job.log(f"主体图已有 {ok_n} 张，不用扩（目标 {target}）")
        _prog(job, phase="done", current="不用扩", done=0, total=0)
        return {"added": 0, "broll": 0, "ok": ok_n, "target": target}

    ready_n = len(_ready_subjects(m))
    if ready_n >= target:
        job.log(f"加上待审已有 {ready_n} 张，先在面板上点「留 / 弃」")
        _prog(job, phase="done", current="先审待审图", done=0, total=0)
        return {"added": 0, "broll": 0, "ok": ok_n, "pending": ready_n - ok_n,
                "target": target}

    need = max(1, target - ready_n)
    _prog(job, phase="connect", current="连接生图账号", done=0, total=need, items=[])
    gemini = ai.client(job)
    cheap = ai.client(job, cheap=True)  # 现场校验走最便宜那档
    eng = _connect_engine(job)
    cand_dir = d / CAND_DIR
    cand_dir.mkdir(parents=True, exist_ok=True)
    added = broll_n = 0
    blocked_refs: set[str] = set()
    produced = False
    job_avoid: set[tuple[str, str]] = set()
    pool = _pool_hashes(m, d)
    try:
        for rnd in range(3):
            job.check()
            if len(_ready_subjects(m)) >= target:
                break
            picked, how = _picked_refs(m, refs)
            if not picked:
                if blocked_refs and added == 0 and broll_n == 0:
                    break
                raise OranteError("没有可用的主体参考图", "先准备几张主体图再扩。")
            if how == "all":
                job.log("没有勾参考图，退回全部 ok 主体图："
                        + "、".join(str(im.get("p") or "") for im in picked))
            else:
                job.log("参考图：" + "、".join(str(im.get("p") or "") for im in picked))
            job.log(f"第 {rnd + 1} 轮，参考 {len(picked)} 张，目标 {target}")
            for ref in picked:
                job.check()
                remaining = target - len(_ready_subjects(m))
                if remaining <= 0:
                    break
                n = min(per_round, remaining)
                rel = ref.get("file") or ""
                img = d / rel
                ref_p = str(ref.get("p") or "?")
                if not img.is_file():
                    job.log(f"  · {ref_p} 文件不在，跳过")
                    continue
                cov_picks = _coverage_picks(n, m, False, avoid=job_avoid)
                if cov_picks:
                    skip_ref = False
                    for vi, pick in enumerate(cov_picks, 1):
                        job.check()
                        if len(_ready_subjects(m)) >= target:
                            break
                        cov = pick.get("cov") or {}
                        prompt = pick.get("prompt") or ""
                        job.log(
                            f"参考 {ref_p} → {cov.get('camera') or '?'} "
                            f"{cov.get('camera_name') or ''} · "
                            f"{cov.get('action_name') or ''}：{prompt}")
                        _prog(job, phase="gen",
                              current=f"参考 {ref_p} · {cov.get('camera_name') or ''} · "
                                      f"{cov.get('action_name') or ''} · 已提交，等出图")
                        try:
                            fp, pick, dup_warn, stage_warn = _gen_one_cov(
                                job, eng, img, cand_dir, pick, pool, m, d,
                                False, job_avoid, gemini=cheap)
                        except BlockedImage:
                            _mark_ref_blocked(job, m, d, ref, ref_p)
                            blocked_refs.add(ref_p)
                            skip_ref = True
                            break
                        job.check()
                        if fp is None:
                            job.log(f"  · 变体 {vi} 被拒或没出图")
                            _prog(job, item=_item(None, None, "被拒", ref_p, vi, prompt))
                            continue
                        produced = True
                        added, broll_n = _take_new(
                            job, m, d, fp, gemini, ref_p, vi, len(cov_picks),
                            pick.get("prompt") or prompt, added, broll_n,
                            cov=pick.get("cov"), dup_warn=dup_warn,
                            stage_warn=stage_warn)
                    if skip_ref:
                        continue
                    continue
                _prog(job, phase="prompt",
                      current=f"参考 {ref_p} · 问 Gemini 写 {n} 句")
                variants, mode = _ask_variants(gemini, img, n, refill=False, scene=_scene_text(m, img))
                if mode in ("single", "direct"):
                    prompt = variants[0]
                    if mode == "single":
                        job.log(f"参考 {ref_p} → Gemini 没给出 {n} 句变体，退回一句生 {n} 张")
                    job.log(f"参考 {ref_p} → 变体 1/{n}：{prompt}")
                    _prog(job, phase="gen",
                          current=f"参考 {ref_p} · 变体 1/{n} · 已提交，等出图")
                    try:
                        paths = eng.gen_images(prompt, ref=img, n=n, dest_dir=cand_dir) or []
                    except CreditHalted as e:
                        notify_credit_halt(job, eng, e)
                        wait_credit_continue(job, eng)
                        paths = eng.gen_images(prompt, ref=img, n=n, dest_dir=cand_dir) or []
                    except BlockedImage:
                        _mark_ref_blocked(job, m, d, ref, ref_p)
                        blocked_refs.add(ref_p)
                        continue
                    job.check()
                    if not paths:
                        job.log("  · 这一轮一张都没出来（可能被拒）")
                        for vi in range(1, n + 1):
                            _prog(job, item=_item(None, None, "被拒", ref_p, vi, prompt))
                        continue
                    produced = True
                    for vi, fp in enumerate(paths, 1):
                        job.check()
                        if len(_ready_subjects(m)) >= target:
                            if Path(fp).is_file():
                                Path(fp).unlink()
                            continue
                        added, broll_n = _take_new(
                            job, m, d, Path(fp), gemini, ref_p, vi, n, prompt,
                            added, broll_n)
                else:
                    skip_ref = False
                    for vi, prompt in enumerate(variants, 1):
                        job.check()
                        if len(_ready_subjects(m)) >= target:
                            break
                        job.log(f"参考 {ref_p} → 变体 {vi}/{len(variants)}：{prompt}")
                        _prog(job, phase="gen",
                              current=f"参考 {ref_p} · 变体 {vi}/{len(variants)} · 已提交，等出图")
                        try:
                            fp = _gen_one(eng, prompt, img, cand_dir, job=job)
                        except BlockedImage:
                            _mark_ref_blocked(job, m, d, ref, ref_p)
                            blocked_refs.add(ref_p)
                            skip_ref = True
                            break
                        job.check()
                        if fp is None:
                            job.log(f"  · 变体 {vi} 被拒或没出图")
                            _prog(job, item=_item(None, None, "被拒", ref_p, vi, prompt))
                            continue
                        produced = True
                        added, broll_n = _take_new(
                            job, m, d, fp, gemini, ref_p, vi, len(variants), prompt,
                            added, broll_n)
                    if skip_ref:
                        continue
    except JobCancelled:
        _prog(job, phase="cancelled", current="已停止")
        raise
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

    ready = len(_ready_subjects(m))
    if added == 0 and broll_n == 0 and blocked_refs and not produced:
        msg = "参考图都被 Flow 拒收了，换张图或点「补图」"
        job.log(msg)
        _prog(job, phase="done", current=msg, done=0, total=need)
        events.emit("tasks.changed")
        return {"added": 0, "broll": 0, "ready": ready, "target": target, "all_blocked": True}
    job.log(f"✓ 扩了 {added} 张待审，空镜 {broll_n} 张；现在主体+待审 {ready} / {target}")
    _quiet_rescan(job.task_id, job.log)
    m = Manifest.load(d)
    ready = len(_ready_subjects(m))
    _prog(job, phase="done",
          current=f"扩了 {added} 张，待审 {added} 张",
          done=added, total=need)
    events.emit("tasks.changed")
    return {"added": added, "broll": broll_n, "ready": ready, "target": target}


def _take_new(job, m, d, fp, gemini, ref_p, vi, n, prompt, added, broll_n,
              cov=None, dup_warn=False, stage_warn=False):
    """出图后判空镜、记账、更新进度。被拒不计 done。"""
    _prog(job, phase="judge",
          current=f"参考 {ref_p} · 变体 {vi}/{n} · 判空镜")
    rec = _ingest_new(job, m, d, Path(fp), gemini, origin="expand",
                      cov=cov, dup_warn=dup_warn, stage_warn=stage_warn)
    role = "空镜" if rec.get("role") == "空镜" else "主体"
    if role == "空镜":
        broll_n += 1
        _prog(job, item=_item(rec.get("p"), rec.get("file"), role, ref_p, vi, prompt))
    else:
        added += 1
        _prog(job, done=added,
              item=_item(rec.get("p"), rec.get("file"), role, ref_p, vi, prompt))
    return added, broll_n


def _flip_to(src: Path, dest: Path) -> None:
    from PIL import Image, ImageOps
    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as pic:
        ImageOps.mirror(pic).convert("RGB").save(dest, format="JPEG", quality=92)


def _auto_describe(job, m: Manifest, d: Path, im: dict, gemini=None) -> bool:
    """新图落盘后马上写描述语 + 测量，失败只记日志（出片时会再提示）。"""
    try:
        from . import describe
        describe.describe_one(job, m, d, im, gemini, save=False)
        return True
    except JobCancelled:
        raise
    except OranteError as e:
        job.log(f"  · {im.get('p')} 描述语没写成：{e.message}（到多模态面板点「写描述语」）")
    except Exception as e:  # noqa: BLE001
        job.log(f"  · {im.get('p')} 描述语没写成：{type(e).__name__}: {e}")
    return False


def _apply_new_file(job, m, d, im, p, pick: Path, *, auto: bool, ref_p: str,
                    pick_vi: int, pick_prompt: str, cov: dict | None = None,
                    dup_warn: bool = False, stage_warn: bool = False) -> dict:
    """新图覆盖同名文件、必须翻转。旧图一律留一份备份。

    0908：自动换图这条路以前是把旧图 unlink 掉的。那天 Grok 的「被拒」判断误报
    （见 engines/grok.py 的 _PAGE_TEXT_JS 注释），好好的图被连着换掉、翻转、
    覆盖，原图没了。判断可以再错，原图不能再丢——所以自动和手动都挪到
    images/_replaced/ 里，不再删。

    80：已收片的段不再自动打回 planned。片子是用旧图生成的，旧图还在
    _replaced/；观众看的是视频，不值得为图池记账白跑 4 条。
    """
    old_rel = (im.get("file") or f"{IMG_DIR}/{p}.jpg").replace("\\", "/")
    old_path = d / old_rel
    dest = d / old_rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bak = d / REPL_DIR / f"{p}-{stamp}{old_path.suffix or '.jpg'}"
    if old_path.is_file():
        _move_file(old_path, bak)
        job.log(f"  · 旧图挪到 {bak.relative_to(d).as_posix()}")
    _flip_to(pick, dest)
    job.log("  · 已左右翻转后覆盖")
    try:
        pick.unlink()
    except OSError:
        pass
    im["sha1"] = sha1_file(dest) if dest.is_file() else ""
    im["status"] = "ok"
    im["origin"] = "auto_refill" if auto else "replace"
    im.pop("flow_upload_refused", None)   # 换成新图了，"原图传不上 Flow"这件事翻篇
    im["flipped"] = True
    im["gen_fail"] = 0
    if cov:
        im["cov"] = dict(cov)
        if not im.get("first_refill_cov"):
            im["first_refill_cov"] = dict(cov)
    im["last_refill_ref"] = str(ref_p or "")
    if dup_warn:
        im["dup_warn"] = True
    elif "dup_warn" in im:
        im.pop("dup_warn", None)
    if stage_warn:
        im["stage_warn"] = True
    elif "stage_warn" in im:
        im.pop("stage_warn", None)
    # 图换了，旧测量不再对。手改过的描述语不清空——那是用户的劳动。
    im["measure"] = {}
    if im.get("desc_manual") and str(im.get("desc") or "").strip():
        im["desc_stale"] = True
        job.log(f"  · {p} 描述语是手改的，图换了仍保留原文（卡片上可点「按新图重写」）")
    else:
        im["desc"] = ""
        im.pop("desc_stale", None)
        _auto_describe(job, m, d, im)
    bak_rel = bak.relative_to(d).as_posix() if bak.is_file() else ""
    info = _reset_refs(m, p, old_file=bak_rel)
    reset = list(info.get("reset") or [])
    kept = list(info.get("kept") or [])
    m.save(d)
    taskmod.emit_seg_changed(job.task_id, taskmod.remember_counts(d, m))
    job.log(f"✓ {p} 已补上")
    line = fmt_reset_log(p, reset, kept)
    if line:
        job.log("  " + line)
    n_locked = int(getattr(m, "_n_prompt_locked", 0) or 0)
    if n_locked:
        job.log(f"  · {n_locked} 段因为锁定没动")
    events.emit("image.replaced", task_id=job.task_id, p=p,
                reset=reset, kept=kept, old_file=bak_rel)
    _prog(job, item=_item(p, old_rel, "主体", ref_p, pick_vi, pick_prompt))
    return {"ok": True, "ref": ref_p, "reset": reset, "kept": kept, "file": old_rel,
            "cov": dict(cov) if cov else None}


def _img_size(fp: Path) -> tuple[int, int]:
    from PIL import Image
    with Image.open(fp) as pic:
        return int(pic.size[0]), int(pic.size[1])


def image_metrics(fp: Path) -> dict:
    """原图 / 候选的宽高、面积、锐度、aHash。闸要用。"""
    from . import tails
    w, h = _img_size(fp)
    sharp = 0.0
    try:
        sharp = float(tails.laplacian_var(fp) or 0)
    except Exception:  # noqa: BLE001
        sharp = 0.0
    return {
        "w": w, "h": h, "area": int(w) * int(h),
        "sharp": sharp, "hash": _img_hash(fp),
    }


def _face_gate(src: Path, cand: Path, log=None) -> tuple[bool, str]:
    """闸 3：人物还是不是同一个。

    复用 tails.inspect_tail（bench / 接片已经在用的 face_ok），不新造人脸识别。
    判不出来（Gemini 挂了、原图本身没脸）就跳过，返回 (True, 说明)。
    原图有脸、新图没脸 → 不换。
    """
    from . import tails
    try:
        src_info = tails.inspect_tail(src)
    except Exception as e:  # noqa: BLE001
        msg = f"人物闸跳过：原图判不出来（{type(e).__name__}: {e}）"
        if callable(log):
            log(msg)
        return True, msg
    if src_info.get("face_ok") is not True:
        msg = "人物闸跳过：原图没判到脸"
        if callable(log):
            log(msg)
        return True, msg
    try:
        cand_info = tails.inspect_tail(cand)
    except Exception as e:  # noqa: BLE001
        msg = f"人物闸跳过：新图判不出来（{type(e).__name__}: {e}）"
        if callable(log):
            log(msg)
        return True, msg
    if cand_info.get("face_ok") is False:
        return False, "人物闸：新图没拍到脸，没换"
    return True, "人物闸过（face_ok）"


def check_upscale_gates(src: Path, cand: Path, log=None) -> dict:
    """三道闸。少一条这个功能就变成随机换图。

    1. 同一张画面：aHash 距离 ≤ UPSCALE_SAME_DIST（要雷同；扩图 dup_dist 正好相反）
    2. 真的更清楚：面积 ≥ 1.2× 或 锐度 ≥ 1.3×
    3. 还是同一个人：复用 tails.inspect_tail 的 face_ok；判不出来就跳过
    """
    from .verify import hamming
    sm = image_metrics(src)
    cm = image_metrics(cand)
    src_wh = (sm["w"], sm["h"])
    dst_wh = (cm["w"], cm["h"])
    dist = None
    if sm["hash"] is not None and cm["hash"] is not None:
        dist = int(hamming(sm["hash"], cm["hash"]))
    out = {
        "ok": False, "gate": 0, "reason": "",
        "dist": dist, "src_wh": src_wh, "dst_wh": dst_wh,
        "src_sharp": sm["sharp"], "dst_sharp": cm["sharp"],
        "src_area": sm["area"], "dst_area": cm["area"],
    }
    dist_s = "—" if dist is None else str(dist)
    if callable(log):
        log(f"  · 闸数字：距离 {dist_s}  "
            f"{src_wh[0]}×{src_wh[1]} → {dst_wh[0]}×{dst_wh[1]}  "
            f"锐度 {sm['sharp']:.0f}→{cm['sharp']:.0f}")
    if dist is None:
        out["gate"] = 1
        out["reason"] = "算不了 aHash，没换"
        return out
    if dist > UPSCALE_SAME_DIST:
        out["gate"] = 1
        out["reason"] = (
            f"不是同一张画面（aHash 距离 {dist} > {UPSCALE_SAME_DIST}），没换")
        return out
    area_ok = cm["area"] >= sm["area"] * UPSCALE_AREA_MIN
    sharp_ok = (sm["sharp"] > 0
                and cm["sharp"] >= sm["sharp"] * UPSCALE_SHARP_MIN)
    if not area_ok and not sharp_ok:
        out["gate"] = 2
        out["reason"] = (
            f"新图没有更清楚（{src_wh[0]}×{src_wh[1]} → {dst_wh[0]}×{dst_wh[1]}，"
            f"锐度 {sm['sharp']:.0f}→{cm['sharp']:.0f}），没换")
        return out
    face_ok, face_msg = _face_gate(src, cand, log=log)
    if not face_ok:
        out["gate"] = 3
        out["reason"] = face_msg
        return out
    out["ok"] = True
    out["reason"] = face_msg
    return out


def _apply_upscale_file(job, m, d, im, p, pick: Path, *, dist, acc_name,
                        src_wh, dst_wh) -> dict:
    """提升画质换图：原样覆盖，不翻转、不重写描述语、不打回段。

    跟补图那条路分开走——那边会左右翻转并重写描述语，翻了就毁了，
    重写描述语还会白烧 Gemini。
    """
    old_rel = (im.get("file") or f"{IMG_DIR}/{p}.jpg").replace("\\", "/")
    old_path = d / old_rel
    dest = d / old_rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bak = d / REPL_DIR / f"{p}-{stamp}{old_path.suffix or '.jpg'}"
    if old_path.is_file():
        _move_file(old_path, bak)
        job.log(f"  · 旧图挪到 {bak.relative_to(d).as_posix()}")
    shutil.copy2(str(pick), str(dest))
    im["sha1"] = sha1_file(dest) if dest.is_file() else ""
    im["w"] = int(dst_wh[0])
    im["h"] = int(dst_wh[1])
    try:
        im["mtime"] = int(dest.stat().st_mtime * 1000)
    except OSError:
        im["mtime"] = 0
    im["origin"] = "upscale"
    im["upscaled"] = {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "from": [int(src_wh[0]), int(src_wh[1])],
        "to": [int(dst_wh[0]), int(dst_wh[1])],
        "dist": None if dist is None else int(dist),
        "acc": acc_name or "",
    }
    # desc / measure 原样留下；flipped 也不要置 True
    refs = [int(sg.get("s") or 0) for sg in m.segments if sg.get("image") == p]
    refs = [s for s in refs if s]
    m.save(d)
    taskmod.emit_seg_changed(job.task_id, taskmod.remember_counts(d, m))
    events.emit("tasks.changed")
    job.log(
        f"✓ {p} 已提清（{src_wh[0]}×{src_wh[1]} → {dst_wh[0]}×{dst_wh[1]}），"
        f"{len(refs)} 段在用，没有自动打回")
    _prog(job, item=_item(p, old_rel, "主体", p, 1, ""))
    return {
        "ok": True, "p": p, "from": [int(src_wh[0]), int(src_wh[1])],
        "to": [int(dst_wh[0]), int(dst_wh[1])],
        "dist": None if dist is None else int(dist),
        "refs": refs, "file": old_rel,
        "replaced": bak.relative_to(d).as_posix() if bak.is_file() else "",
    }


def _upscale_prompt() -> str:
    raw = prompts.get_block("upscale")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return (getattr(prompts, "UPSCALE_DEFAULT", "") or "").strip()


def upscale_candidate(job, src: Path, eng, acc_name: str, *,
                      work_dir: Path | None = None) -> dict:
    """对任意一张图跑 66 号提清：同一套 prompts.upscale、同一套三道闸。

    只生成候选 + 过闸，不写回。调用方决定覆盖哪里（图池走 _apply_upscale_file，
    尾帧接龙走 archive_old_tail + 写回 tail_path）。
    返回 {ok, cand, reason, gate, dist, from, to, acc}。
    """
    src = Path(src)
    empty = {
        "ok": False, "cand": None, "reason": "", "gate": 0,
        "dist": None, "from": [], "to": [], "acc": acc_name or "",
    }
    if not src.is_file():
        return {**empty, "reason": "原图不在"}
    if eng is None:
        return {**empty, "reason": "没有生图引擎"}
    prompt = _upscale_prompt()
    if job is not None and callable(getattr(job, "log", None)):
        job.log("完整提示词：\n" + prompt)
        _prog(job, phase="gen", current="原样重绘")
    work_dir = Path(work_dir) if work_dir else (src.parent / "_upscale")
    work_dir.mkdir(parents=True, exist_ok=True)
    before = {x.resolve() for x in work_dir.glob("*") if x.is_file()}
    try:
        fp = _gen_one(eng, prompt, src, work_dir, job=job)
    except BlockedImage as e:
        after = {x.resolve() for x in work_dir.glob("*") if x.is_file()}
        for leftover in after - before:
            try:
                leftover.unlink()
            except OSError:
                pass
        return {**empty, "reason": "被平台拒收：%s" % (str(e) or "换账号再试")}
    if fp is None or not Path(fp).is_file():
        return {**empty, "reason": "没出图"}
    cand = Path(fp)
    logfn = job.log if job is not None and callable(getattr(job, "log", None)) else None
    if job is not None:
        _prog(job, phase="judge", current="三道闸")
    gate = check_upscale_gates(src, cand, log=logfn)
    if logfn:
        logfn("  · %s" % (gate.get("reason") or ""))
    out = {
        "ok": bool(gate.get("ok")),
        "cand": cand,
        "reason": gate.get("reason") or ("没过闸" if not gate.get("ok") else ""),
        "gate": gate.get("gate") or 0,
        "dist": gate.get("dist"),
        "from": list(gate.get("src_wh") or []),
        "to": list(gate.get("dst_wh") or []),
        "acc": acc_name or "",
    }
    if not out["ok"] and logfn:
        logfn("  · 候选留在 %s" % cand)
    return out


def _upscale_one(job, m, d, p, eng, acc_name: str) -> dict:
    """提升一张图池里的图。过闸换图；不过闸候选留在 _upscale/。"""
    im = m.image(p)
    if not im:
        return {"ok": False, "p": p, "reason": f"没有图片 {p}"}
    if im.get("role") == "空镜" or im.get("status") == "blocked" or im.get("blocked"):
        return {"ok": False, "p": p, "reason": f"{p} 是空镜或被拒，不能提升画质"}
    rel = (im.get("file") or "").replace("\\", "/")
    src = d / rel
    if not src.is_file():
        return {"ok": False, "p": p, "reason": f"{p} 的文件不在"}
    job.log(f"{p} 提升画质，参考就是它自己")
    # 完整提示词 / 三道闸由 upscale_candidate 打（chain 尾帧提清也走同一条）
    r = upscale_candidate(job, src, eng, acc_name, work_dir=d / UPSCALE_DIR)
    if not r.get("ok"):
        cand = r.get("cand")
        rel_c = ""
        if cand:
            cand = Path(cand)
            rel_c = cand.relative_to(d).as_posix() if d in cand.parents else str(cand)
        if "被平台拒收" in str(r.get("reason") or ""):
            raise OranteError(
                f"{p} 被平台拒收，没法提升画质",
                (r.get("reason") or "换一个账号再试，或先换一张不被拒的图。"))
        return {
            "ok": False, "p": p, "reason": r.get("reason") or "没过闸",
            "gate": r.get("gate"), "dist": r.get("dist"),
            "from": list(r.get("from") or []),
            "to": list(r.get("to") or []),
            "cand": rel_c,
        }
    return _apply_upscale_file(
        job, m, d, im, p, Path(r["cand"]),
        dist=r.get("dist"), acc_name=acc_name,
        src_wh=r.get("from") or (0, 0),
        dst_wh=r.get("to") or (0, 0))


@register("upscale", account_bound=True)
def upscale(job, ps=None):
    """右键「提升画质」：拿这张图自己当参考原样重画，过闸才换。"""
    if not job.task_id:
        raise OranteError("upscale 需要 task_id")
    if isinstance(ps, str):
        ps = [ps]
    ps = [str(x).strip() for x in (ps or []) if str(x).strip()]
    if not ps:
        raise OranteError("提升画质要指定图片", "body 里带 ps，比如 [\"p03\"]。")
    d = _task_dir(job.task_id)
    m = Manifest.load(d)
    _prog(job, phase="connect", current="连接生图账号",
          done=0, total=len(ps), items=[])
    eng = _connect_engine(job)
    t = taskmod.get(job.task_id)
    aid = job.account_id or resolve_account_id(t, persist=False)
    acc = accounts.get(aid)
    acc_name = acc.get("name") or aid
    ok_items, failed = [], []
    try:
        for i, p in enumerate(ps, 1):
            job.check()
            _prog(job, phase="gen", current=f"{p} · {i}/{len(ps)}", done=i - 1)
            r = _upscale_one(job, m, d, p, eng, acc_name)
            if r.get("ok"):
                ok_items.append(r)
            else:
                failed.append(r)
                job.log(f"✗ {p} {r.get('reason') or '没过闸'}")
            _prog(job, done=i)
    except JobCancelled:
        _prog(job, phase="cancelled", current="已停止")
        raise
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
    events.emit("tasks.changed")
    job.log(f"✓ 提清 {len(ok_items)} 张，没换 {len(failed)} 张")
    _prog(job, phase="done",
          current=f"提清 {len(ok_items)} 张",
          done=len(ok_items), total=len(ps))
    return {"ok": ok_items, "failed": failed}


def reset_image_refs(task_id: str, p: str, *, only_kept: bool = False) -> dict:
    """用户点「打回这 N 段重生」/「这 N 段也按新图重做」才走这里。

    提升画质和自动换图本身都不自动打回 collected。
    only_kept=True：只动带 image_replaced 记号的已收片段。
    """
    p = (p or "").strip()
    d = _task_dir(task_id)
    m = Manifest.load(d)
    if not m.image(p):
        raise OranteError(f"没有图片 {p}")
    info = _reset_refs(m, p, force_collected=True, only_kept=only_kept)
    reset = list(info.get("reset") or [])
    kept = list(info.get("kept") or [])
    m.save(d)
    taskmod.emit_seg_changed(task_id, taskmod.remember_counts(d, m))
    events.emit("tasks.changed")
    return {"p": p, "reset": reset, "kept": kept}


# ---------------------------------------------------------------- 按描述语补图
# 2026-09-27 用户要求：补图（被拒图的替补）按**每张图自己的描述语**来生成。
# 原来补图用的是套装里「扩图」那块覆盖库（现在的默认套装写的是"同一场室内讲道现场的另一个
# 拍摄机位……讲台、鲜花、投影屏幕"），换成棒球场、停车场之类的任务就完全不对题。
# 描述语是多模态那一步按这张图写的（人物、服装、姿态、现场），拿它来补图才对得上。
# 没有描述语的图照旧走原来的覆盖库那条路，不影响。

def _desc_core(im: dict | None) -> str:
    """描述语正文：去掉末尾【闭眼|围绕】这类标签。"""
    s = str((im or {}).get("desc") or "").strip()
    s = re.sub(r"(\s*【[^】]*】)+\s*$", "", s).strip()
    return s.rstrip("。；;，, ")


def _refill_desc_prompt(desc: str, with_ref: bool) -> str:
    if with_ref:
        clause = _refill_clause(True)
        return (f"以上传的这张图为参考，生成同一个现场的另一张照片。画面内容：{desc}。"
                f"人物的长相、发型、服装、配饰和现场环境都和上传图像保持一致，{clause}"
                "换一个自然的拍摄角度或景别。最终成品是一张完整的、真实自然的现场纪实摄影照片，"
                "构图完整不裁切，画面上不要出现任何文字。")
    return (f"生成一张真实自然的现场纪实摄影照片。画面内容：{desc}。"
            "构图完整不裁切，画面上不要出现任何文字。")


def _gen_one_aspect(eng, prompt: str, img: Path | None, cand_dir: Path, job=None,
                    seed: int | None = None, aspect: str | None = None) -> Path | None:
    """跟 _gen_one 一样生 1 张，多带一个画幅（不带参考图时要靠它定横竖）。"""
    kw = {}
    if aspect:
        kw["aspect"] = aspect
    if seed is not None:
        kw["seed"] = int(seed)

    def _call(kwargs):
        try:
            return eng.gen_images(prompt, ref=img, n=1, dest_dir=cand_dir, **kwargs) or []
        except TypeError:
            kwargs = {k: v for k, v in kwargs.items() if k != "seed"}
            return eng.gen_images(prompt, ref=img, n=1, dest_dir=cand_dir, **kwargs) or []

    tick = getattr(eng, "tick_credits", None)
    if callable(tick):
        tick()
    try:
        paths = _call(dict(kw))
    except CreditHalted as e:
        notify_credit_halt(job, eng, e)
        wait_credit_continue(job, eng)
        paths = _call(dict(kw))
    for fp in paths:
        fp = Path(fp)
        if fp.is_file():
            return fp
    return None


def _gen_pick_desc(job, m, d, eng, gemini, img: Path | None, cand_dir: Path, ref_p: str,
                   desc: str, *, auto: bool, per_round: int, seed: int | None = None,
                   aspect: str | None = None):
    """按描述语补一张。img=None 表示不带参考图、只照描述语生成。
    返回值跟 _gen_pick 一样：(file, prompt, vi, cov, dup_warn, stage_warn)。"""
    prompt = _refill_desc_prompt(desc, with_ref=img is not None)
    tries = 1 if auto else max(1, int(per_round or 1))
    who = f"参考 {ref_p}" if img is not None else "不带参考图"
    for vi in range(1, tries + 1):
        job.check()
        job.log(f"{who} → 按描述语补图 {vi}/{tries}：{prompt}")
        _prog(job, phase="gen", current=f"{who} · 按描述语 · 第 {vi}/{tries} 张 · 等出图")
        fp = _gen_one_aspect(eng, prompt, img, cand_dir, job=job,
                             seed=(seed + vi) if seed is not None else None, aspect=aspect)
        job.check()
        if fp is None:
            _prog(job, item=_item(None, None, "被拒", ref_p or "描述语", vi, prompt))
            continue
        if not auto:
            _prog(job, phase="judge", current=f"{who} · 第 {vi}/{tries} 张 · 判空镜")
            judged = broll.judge(gemini or ai.client(job), fp)
            if judged.get("is_broll"):
                _stash_broll(job, m, d, fp, judged, ref_p or "描述语", vi, prompt)
                continue
        return fp, prompt, vi, None, False, False
    return None, prompt, 0, None, False, False


def refill_one(job, m: Manifest, d: Path, p: str, eng, gemini=None, *,
               auto: bool = False, attempt: int = 1) -> dict | None:
    """补一张被拒的图。成功返回 {ok, ref, reset, kept, file, cov}，失败返回 None。

    auto=True（出片主循环调用）：用补图预设直接生 1 张、必须翻转、旧文件直接删。
    auto=False（「补图」按钮）：可问 Gemini 变体、判空镜；旧图进 _replaced。
    参考图上传被拒就换下一张参考，不把这次任务判失败。

    attempt：第 1 次用 preview 参考；第 2 次换一张参考（没有就换 seed）；
    第 3 次换参考且挑和当前差异最大的景别+角度。
    """
    im = m.image(p)
    if not im:
        return None
    attempt = max(1, int(attempt or 1))
    desc = _desc_core(im)   # 先记下来：换图成功后 _apply_new_file 会按新图重写描述语
    aspect = None
    try:
        from ..engines import caps
        own = d / (im.get("file") or "")
        if own.is_file():
            aspect = caps.aspect_for_image(own, "flow")
    except Exception:  # noqa: BLE001
        aspect = None
    if desc:
        job.log(f"补位提示词：按 {p} 自己的描述语 —— {desc[:60]}")
    else:
        # 验收：打印实际用的补位提示词前 60 字，确认 refill_clause 插在 head 之后
        used = _prompt_block(refill=True) or ""
        first = next((ln.strip() for ln in used.splitlines()
                      if ln.strip() and not ln.strip().startswith("#")), used)
        job.log("补位提示词：" + first[:60])
    refs = _ref_candidates(m, d, p, prefer_self=not auto)
    br = str(im.get("block_reason") or "")
    if "拒收了" in br or "Failed to upload" in br:
        # 这张图本身就是传不上去被拒的：拿它自己当参考只会再被拒一次，不白等
        refs = [r for r in refs if r.get("p") != p]
    last_ref = str(im.get("last_refill_ref") or "")
    refs = _refs_for_attempt(refs, attempt, last_ref)
    cand_dir = d / CAND_DIR
    cand_dir.mkdir(parents=True, exist_ok=True)
    per_round = max(1, int(_cfg("expand.per_round", 4) or 4))
    if not refs:
        job.log(f"  · {p} 没有可用参考图")
        return _refill_no_ref(job, m, d, im, p, eng, gemini, desc, cand_dir,
                              auto=auto, per_round=per_round, aspect=aspect, attempt=attempt)
    first_p = str(refs[0].get("p") or "?")
    if attempt >= 2:
        if first_p != last_ref and last_ref:
            job.log(f"第 {attempt}/3 次：换参考图 {last_ref} → {first_p}")
        elif last_ref and first_p == last_ref:
            job.log(f"第 {attempt}/3 次：没有别的参考图，换 seed")
        else:
            job.log(f"第 {attempt}/3 次：换参考图 → {first_p}")
    far_from = dict(im.get("first_refill_cov") or im.get("cov") or {}) if attempt >= 3 else None
    seed = None
    if attempt >= 2 and first_p == (last_ref or first_p) and len(refs) <= 1:
        seed = (hash(str(p) + str(attempt)) & 0x7FFFFFFF) or 1

    for ref in refs:
        job.check()
        img = d / (ref.get("file") or "")
        if not img.is_file():
            continue
        ref_p = str(ref.get("p") or "?")
        im["last_refill_ref"] = ref_p
        try:
            if desc:
                pick, pick_prompt, pick_vi, cov_meta, dup_warn, stage_warn = _gen_pick_desc(
                    job, m, d, eng, gemini, img, cand_dir, ref_p, desc,
                    auto=auto, per_round=per_round, seed=seed, aspect=aspect)
            else:
                pick, pick_prompt, pick_vi, cov_meta, dup_warn, stage_warn = _gen_pick(
                    job, m, d, eng, gemini, img, cand_dir, ref_p,
                    auto=auto, per_round=per_round, far_from=far_from, seed=seed)
        except BlockedImage:
            if ref.get("p") != p:
                _mark_ref_blocked(job, m, d, ref, ref_p)
            else:
                job.log(f"  · 用 {ref_p} 自己当参考也被拒，换下一张")
            continue
        if pick is None:
            continue
        return _apply_new_file(
            job, m, d, im, p, pick, auto=auto,
            ref_p=ref_p, pick_vi=pick_vi, pick_prompt=pick_prompt,
            cov=cov_meta, dup_warn=dup_warn, stage_warn=stage_warn)
    return _refill_no_ref(job, m, d, im, p, eng, gemini, desc, cand_dir,
                          auto=auto, per_round=per_round, aspect=aspect, attempt=attempt)


def _refill_no_ref(job, m, d, im, p, eng, gemini, desc: str, cand_dir: Path, *,
                   auto: bool, per_round: int, aspect: str | None, attempt: int) -> dict | None:
    """参考图都用不了（这张图自己就是被平台拒收的那张、图池里也没有别的主体图）：
    有描述语就不带参考图、只照描述语生成一张。没有描述语就只能放着。"""
    if not desc:
        return None
    job.log(f"  · 参考图都用不了，按 {p} 自己的描述语直接生成（不带参考图）")
    seed = ((hash(str(p) + "desc" + str(attempt)) & 0x7FFFFFFF) or 1) if attempt >= 2 else None
    try:
        pick, pick_prompt, pick_vi, cov_meta, dup_warn, stage_warn = _gen_pick_desc(
            job, m, d, eng, gemini, None, cand_dir, "", desc,
            auto=auto, per_round=per_round, seed=seed, aspect=aspect)
    except BlockedImage as e:
        job.log(f"  · 只照描述语生成也被拒：{str(e)[:120]}")
        return None
    if pick is None:
        return None
    return _apply_new_file(
        job, m, d, im, p, pick, auto=auto,
        ref_p="描述语", pick_vi=pick_vi, pick_prompt=pick_prompt,
        cov=cov_meta, dup_warn=dup_warn, stage_warn=stage_warn)


def _gen_pick(job, m, d, eng, gemini, img: Path, cand_dir: Path, ref_p: str,
              *, auto: bool, per_round: int, far_from=None, seed: int | None = None):
    """从一张参考生出可用的一张主体图。
    返回 (file, prompt, vi, cov, dup_warn, stage_warn)；没有就 file=None。
    """
    pool = _pool_hashes(m, d)
    avoid: set[tuple[str, str]] = set()
    cov_picks = _coverage_picks(
        1 if auto else per_round, m, True, avoid=avoid, far_from=far_from)
    if cov_picks:
        for vi, pick in enumerate(cov_picks, 1):
            job.check()
            prompt = pick.get("prompt") or ""
            cov = pick.get("cov") or {}
            job.log(
                f"参考 {ref_p} → {cov.get('camera') or '?'} "
                f"{cov.get('camera_name') or ''} · {cov.get('action_name') or ''}：{prompt}")
            _prog(job, phase="gen",
                  current=f"参考 {ref_p} · {cov.get('camera_name') or ''} · 等出图")
            fp, pick, dup_warn, stage_warn = _gen_one_cov(
                job, eng, img, cand_dir, pick, pool, m, d, True, avoid,
                gemini=gemini, seed=seed, far_from=far_from)
            job.check()
            if fp is None:
                _prog(job, item=_item(None, None, "被拒", ref_p, vi, prompt))
                continue
            if not auto:
                _prog(job, phase="judge",
                      current=f"参考 {ref_p} · 变体 {vi}/{len(cov_picks)} · 判空镜")
                judged = broll.judge(gemini or ai.client(job), fp)
                if judged.get("is_broll"):
                    _stash_broll(job, m, d, fp, judged, ref_p, vi, pick.get("prompt") or prompt)
                    continue
            return fp, pick.get("prompt") or prompt, vi, pick.get("cov"), dup_warn, stage_warn
        return None, (cov_picks[0].get("prompt") or ""), 0, None, False, False

    if auto:
        block = _prompt_block(refill=True)
        prompt = FALLBACK_SENTENCE if "{n}" in block else (
            _slot_single(block) or _clean_sentence(block) or FALLBACK_SENTENCE)
        prompt = _with_scene([prompt], _scene_text(m, img))[0]
        _prog(job, phase="gen", current=f"自动换图 · 参考 {ref_p}")
        fp = _gen_one(eng, prompt, img, cand_dir, job=job, seed=seed)
        if fp is None:
            _prog(job, item=_item(None, None, "被拒", ref_p, 1, prompt))
            return None, prompt, 0, None, False, False
        return fp, prompt, 1, None, False, False

    if gemini is None:
        gemini = ai.client(job)
    variants, mode = _ask_variants(gemini, img, per_round, refill=True, scene=_scene_text(m, img))
    if mode in ("single", "direct"):
        prompt = variants[0]
        job.log(f"参考 {ref_p} → 变体 1/{per_round}：{prompt}")
        _prog(job, phase="gen",
              current=f"参考 {ref_p} · 变体 1/{per_round} · 已提交，等出图")
        try:
            paths = eng.gen_images(prompt, ref=img, n=per_round, dest_dir=cand_dir) or []
        except CreditHalted as e:
            notify_credit_halt(job, eng, e)
            wait_credit_continue(job, eng)
            paths = eng.gen_images(prompt, ref=img, n=per_round, dest_dir=cand_dir) or []
        job.check()
        pick = None
        pick_vi = 1
        for vi, fp in enumerate(paths, 1):
            job.check()
            fp = Path(fp)
            if not fp.is_file():
                continue
            _prog(job, phase="judge",
                  current=f"参考 {ref_p} · 变体 {vi}/{per_round} · 判空镜")
            judged = broll.judge(gemini, fp)
            if judged.get("is_broll"):
                _stash_broll(job, m, d, fp, judged, ref_p, vi, prompt)
                continue
            pick, pick_vi = fp, vi
            break
        leftover = [Path(x) for x in paths if Path(x).is_file() and Path(x) != pick]
        for x in leftover:
            try:
                x.unlink()
            except OSError:
                pass
        if not pick:
            for vi in range(1, per_round + 1):
                _prog(job, item=_item(None, None, "被拒", ref_p, vi, prompt))
            return None, prompt, 0, None, False, False
        return pick, prompt, pick_vi, None, False, False

    n = len(variants)
    for vi, prompt in enumerate(variants, 1):
        job.check()
        job.log(f"参考 {ref_p} → 变体 {vi}/{n}：{prompt}")
        _prog(job, phase="gen",
              current=f"参考 {ref_p} · 变体 {vi}/{n} · 已提交，等出图")
        fp = _gen_one(eng, prompt, img, cand_dir, job=job)
        job.check()
        if fp is None:
            _prog(job, item=_item(None, None, "被拒", ref_p, vi, prompt))
            continue
        _prog(job, phase="judge",
              current=f"参考 {ref_p} · 变体 {vi}/{n} · 判空镜")
        judged = broll.judge(gemini, fp)
        if judged.get("is_broll"):
            _stash_broll(job, m, d, fp, judged, ref_p, vi, prompt)
            continue
        return fp, prompt, vi, None, False, False
    return None, (variants[0] if variants else ""), 0, None, False, False


def fill_segments(job, m: Manifest, d: Path, segs: list[int], eng, *, group: int = 3) -> dict:
    """给没有图的段生成新主体图并直接配上（重生时「生成新图替补」）。

    连着的没图段每 `group` 段共用一张新图（和「同一张图管 3±1 段」的规则一致）。
    新图用补图预设从已有主体图生成、左右翻转后存成新的 pNN。
    返回 {filled: [段号], images: [pNN], unfilled: [段号]}。
    """
    want = sorted({int(x) for x in (segs or [])})
    by_s = {int(sg.get("s") or 0): sg for sg in m.segments}
    todo = [s for s in want if s in by_s and not (by_s[s].get("image") and m.image(by_s[s]["image"]))]
    if not todo:
        return {"filled": [], "images": [], "unfilled": []}
    # 连号分组，每组最多 group 段
    runs: list[list[int]] = []
    for s in todo:
        if runs and s == runs[-1][-1] + 1 and len(runs[-1]) < max(1, group):
            runs[-1].append(s)
        else:
            runs.append([s])
    refs = [im for im in m.images
            if im.get("role") == "主体" and im.get("status") == "ok"
            and (d / (im.get("file") or "")).is_file()]
    if not refs:
        raise OranteError("没有可用的主体图当参考", "先到「编排」面板留几张主体图，或者手动配图。")
    scores = _collected_scores(m)
    refs.sort(key=lambda im: (-scores.get(im.get("p"), 0), str(im.get("p") or "")))
    cand_dir = d / CAND_DIR
    cand_dir.mkdir(parents=True, exist_ok=True)
    try:
        # ai.client() 构造不再因缺 key 抛错，空名册要自己拦，才能先出图后补描述语
        if not aikeys.list_all(enabled_only=True):
            raise OranteError("还没有可用的多模态接口")
        gemini = ai.client(job)
    except Exception:  # noqa: BLE001
        gemini = None  # 没接口也先把图生出来，描述语那步会记日志
    filled: list[int] = []
    new_ps: list[str] = []
    unfilled: list[int] = []
    for ri, run in enumerate(runs):
        job.check()
        label = _fmt_run(run)
        pick = None
        prompt = ""
        ref_p = "?"
        cov_meta = None
        dup_warn = False
        stage_warn = False
        for k in range(len(refs)):
            # 参考图轮着用，别每张新图都长得像同一张
            ref = refs[(ri + k) % len(refs)]
            ref_p = str(ref.get("p") or "?")
            img = d / (ref.get("file") or "")
            _prog(job, phase="gen", current=f"替补 {label} · 参考 {ref_p} · 等出图")
            job.log(f"  · {label} 没有图 → 参考 {ref_p} 生一张新图")
            try:
                pick, prompt, _vi, cov_meta, dup_warn, stage_warn = _gen_pick(
                    job, m, d, eng, None, img, cand_dir, ref_p,
                    auto=True, per_round=1)
            except BlockedImage:
                job.log(f"  · 参考 {ref_p} 被拒，换下一张参考")
                continue
            if pick is not None:
                break
        if pick is None:
            job.log(f"  ✗ {label} 没生出可用的图，跳过")
            unfilled.extend(run)
            continue
        p = _next_p(m.images)
        rel = f"{IMG_DIR}/{p}.jpg"
        dest = d / rel
        _flip_to(pick, dest)
        try:
            pick.unlink()
        except OSError:
            pass
        rec = _new_image_rec(p, rel, "主体", "ok", "fill", sha1_file(dest),
                             cov=cov_meta, dup_warn=dup_warn, stage_warn=stage_warn)
        rec["flipped"] = True
        m.images.append(rec)
        _auto_describe(job, m, d, rec, gemini)
        n_kept = 0
        for s in run:
            sg = by_s[s]
            sg["image"] = p
            if sg.get("prompt_locked"):
                n_kept += 1
            else:
                sg["prompt_final"] = ""
        if n_kept:
            job.log(f"  · {n_kept} 段因为锁定没动")
        m.save(d)
        taskmod.emit_seg_changed(job.task_id, taskmod.remember_counts(d, m))
        job.log(f"  ✓ 新图 {p}（已翻转）配给 {label}")
        _prog(job, item=_item(p, rel, "主体", ref_p, 1, prompt))
        filled.extend(run)
        new_ps.append(p)
    events.emit("tasks.changed")
    return {"filled": filled, "images": new_ps, "unfilled": unfilled}


def _fmt_run(run: list[int]) -> str:
    if len(run) == 1:
        return f"s{run[0]:03d}"
    return f"s{run[0]:03d}–s{run[-1]:03d}"


@register("refill", account_bound=True)
def refill(job):
    """被拒补图：覆盖 pX 原文件，配图轨指向不动，被拒段打回 planned。"""
    if not job.task_id:
        raise OranteError("refill 需要 task_id")
    d = _task_dir(job.task_id)
    m = Manifest.load(d)
    blocked = [im for im in m.images if im.get("status") == "blocked"]
    if not blocked:
        job.log("没有被拒的图")
        _prog(job, phase="done", current="没有被拒的图", done=0, total=0)
        return {"replaced": 0, "failed": []}

    if not _ok_subjects(m) and not any(
            (d / (im.get("file") or "")).is_file() for im in blocked):
        raise OranteError(
            "没有可用的主体参考图，没法补被拒的图",
            "先留一张主体图，或到多模态面板改 role。")

    gemini = ai.client(job)
    _prog(job, phase="connect", current="连接生图账号",
          done=0, total=len(blocked), items=[])
    eng = _connect_engine(job)
    replaced, failed = [], []
    try:
        for im in list(blocked):
            job.check()
            p = im.get("p") or "?"
            ok_one = False
            for rnd in range(3):
                job.check()
                r = refill_one(job, m, d, p, eng, gemini, auto=False, attempt=rnd + 1)
                if r and r.get("ok"):
                    replaced.append(p)
                    _prog(job, done=len(replaced))
                    ok_one = True
                    break
            if not ok_one:
                failed.append(p)
                job.log(f"✗ {p} 补了 3 轮还是被拒或空镜，仍标记 blocked")
                if im.get("flow_upload_refused"):
                    job.log(f"  ⛔ {p} 这张图 Flow 不收（上传就被拒，Flow 网页手动上传也一样）。"
                            f"这张图在 Flow 上用不了——把这个任务换到 Grok 等别的引擎出片，或者换一张图。")
                    hint = "这张图 Flow 不收。把这个任务换到 Grok 等别的引擎，或换一张图。"
                else:
                    hint = "换一张参考图再试，或到多模态面板手改"
                events.emit(
                    "toast",
                    text=f"{p} 补了 3 轮还不成功",
                    hint=hint,
                    level="bad")   # 不能叫 kind：emit 的第一个参数就叫 kind，会撞名报 TypeError
    except JobCancelled:
        _prog(job, phase="cancelled", current="已停止")
        raise
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

    events.emit("tasks.changed")
    job.log(f"✓ 补上 {len(replaced)} 张，失败 {len(failed)} 张")
    _prog(job, phase="done",
          current=f"补上 {len(replaced)} 张",
          done=len(replaced), total=len(blocked))
    return {"replaced": replaced, "failed": failed}


def _stash_broll(job, m, d, fp, judged, ref_p, vi, prompt) -> None:
    job.log(f"  · {Path(fp).name} 是空镜，不用")
    bp = _next_p(m.images)
    rel = f"{BROLL_DIR}/{bp}{Path(fp).suffix or '.jpg'}"
    _move_file(fp, d / rel)
    rec = _new_image_rec(
        bp, rel, "空镜", "ok", "expand",
        sha1_file(d / rel) if (d / rel).is_file() else "")
    rec["broll_check"] = judged
    rec["broll_reason"] = judged.get("reason") or ""
    m.images.append(rec)
    m.save(d)
    _prog(job, item=_item(bp, rel, "空镜", ref_p, vi, prompt))


def keep_image(task_id: str, p: str) -> dict:
    """待审图点「留」：status=ok，文件从 _候选/ 挪到 images/。"""
    p = (p or "").strip()
    d = _task_dir(task_id)
    m = Manifest.load(d)
    im = m.image(p)
    if not im:
        raise OranteError(f"没有图片 {p}")
    if im.get("status") != "pending_review":
        raise OranteError(f"{p} 不是待审图", f"当前状态 {im.get('status')}")
    name = Path(im.get("file") or "").name or (p + ".jpg")
    dest_rel = f"{IMG_DIR}/{name}"
    src = d / (im.get("file") or dest_rel)
    dest = d / dest_rel
    if src.is_file():
        _move_file(src, dest)
    im["file"] = dest_rel
    im["status"] = "ok"
    if dest.is_file():
        im["sha1"] = sha1_file(dest)
    m.save(d)
    events.emit("tasks.changed")
    return im


def discard_image(task_id: str, p: str) -> dict:
    """删一张图：记录去掉，文件进 images/_弃用/。待审「弃」和右键删除共用。"""
    p = (p or "").strip()
    d = _task_dir(task_id)
    m = Manifest.load(d)
    im = m.image(p)
    if not im:
        raise OranteError(f"没有图片 {p}")
    name = Path(im.get("file") or "").name or (p + ".jpg")
    dest_rel = f"{DUMP_DIR}/{name}"
    src = d / (im.get("file") or dest_rel)
    if src.is_file():
        _move_file(src, d / dest_rel)
    gone = dict(im)
    m.images = [x for x in m.images if x.get("p") != p]
    for sg in m.segments:
        if sg.get("image") == p:
            sg["image"] = ""
            if sg.get("status") not in ("submitted", "collected") and not sg.get("prompt_locked"):
                sg["prompt_final"] = ""
    tr = dict(m.track or {})
    pins = dict(tr.get("pins") or {})
    tr["pins"] = {k: v for k, v in pins.items() if v != p}
    pools = []
    for z in tr.get("pools") or []:
        imgs = [x for x in (z.get("images") or []) if x != p]
        if imgs:
            pools.append({**z, "images": imgs})
    tr["pools"] = pools
    if tr.get("pool"):
        tr["pool"] = [x for x in tr["pool"] if x != p]
    m.track = tr
    m.save(d)
    events.emit("tasks.changed")
    return gone
