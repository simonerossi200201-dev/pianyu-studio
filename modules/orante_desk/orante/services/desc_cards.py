# -*- coding: utf-8 -*-
"""换图描述卡片：出厂四张 + 用户自建。收片「换图」面板点一张就按卡片生图。

只在表完全为空时播种；用户改过的内容以后不再被覆盖。
"""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

from .. import accounts, db, events, tasks as taskmod
from ..engines.base import BlockedImage
from ..engines.flow_api import notify_credit_halt, wait_credit_continue
from ..errors import CreditHalted, OranteError
from ..jobs import register
from ..models import Manifest, sha1_file
from . import coverage as covlib
from . import expand, pipeline, prompts as promptsmod, review as reviewmod

CARD_DIR = "images/_cards"

# 出厂四张。id 固定 C001…C004，只在空表时写入。
FACTORY = (
    {
        "id": "C001",
        "name": "正脸角度",
        "prompt": (
            "正面机位：人物正对镜头，脸部完整、五官清晰，双眼平视前方略低于镜头，肩膀平正。\n"
            "保持与参考图完全相同的人物、衣着、场景和光线。\n"
            "Frontal view, subject facing the camera directly, full face clearly visible, "
            "same person, same outfit, same setting as the reference."
        ),
        "sort": 1,
    },
    {
        "id": "C002",
        "name": "侧脸角度",
        "prompt": (
            "侧面机位：人物身体转向一侧约四十五到六十度，露出清晰的侧脸轮廓，目光看向画面外的一侧。\n"
            "保持与参考图完全相同的人物、衣着、场景和光线。\n"
            "Three-quarter to profile view, subject turned 45–60 degrees, clear jawline, "
            "looking off-frame, same person, outfit and setting as the reference."
        ),
        "sort": 2,
    },
    {
        "id": "C003",
        "name": "人物近景",
        "prompt": (
            "近景：取景从胸口以上到头顶，脸部占画面主要位置，表情和眼神清楚可见，背景虚化。\n"
            "保持与参考图完全相同的人物、衣着、场景和光线。\n"
            "Close-up from chest up, face dominant in frame, expressive eyes, "
            "shallow depth of field, same person, outfit and setting as the reference."
        ),
        "sort": 3,
    },
    {
        "id": "C004",
        "name": "人物中景",
        "prompt": (
            "中景：取景从腰部以上，能看到上半身和手势，人物在画面中占三分之二左右，背景可辨。\n"
            "保持与参考图完全相同的人物、衣着、场景和光线。\n"
            "Medium shot from the waist up, upper body and hand gestures visible, "
            "subject fills about two-thirds of the frame, background recognizable, "
            "same person, outfit and setting as the reference."
        ),
        "sort": 4,
    },
)

FACTORY_BY_ID = {c["id"]: c for c in FACTORY}

# 测试盯：最近一次卡片生图的提示词和 no_frontal。普通扩图不会写这里。
LAST_GEN: dict = {"prompt": "", "no_frontal": None, "ref": None}


def _row(r: dict | None) -> dict | None:
    if not r:
        return None
    out = dict(r)
    out["builtin"] = int(out.get("builtin") or 0)
    out["sort"] = int(out.get("sort") or 0)
    out["name"] = str(out.get("name") or "")
    out["prompt"] = str(out.get("prompt") or "")
    return out


def seed_if_empty() -> int:
    """表完全为空才写入出厂四张。用户改过 / 删过之后再启动不会覆盖。"""
    try:
        n = db.one("SELECT COUNT(*) n FROM desc_cards")
    except Exception:
        return 0
    if n and int(n.get("n") or 0) > 0:
        return 0
    now = db.now()
    for c in FACTORY:
        db.run(
            "INSERT INTO desc_cards(id,name,prompt,sort,builtin,created,updated)"
            " VALUES(?,?,?,?,1,?,?)",
            (c["id"], c["name"], c["prompt"], c["sort"], now, now))
    # 下一个用户自建号从 C005 起
    cur = db.one("SELECT value FROM counters WHERE name=?", ("desc_card",))
    if not cur or int(cur.get("value") or 0) < 4:
        db.run("INSERT OR REPLACE INTO counters(name,value) VALUES(?,?)", ("desc_card", 4))
    return 4


def list_all() -> list[dict]:
    seed_if_empty()
    return [_row(r) for r in db.rows(
        "SELECT * FROM desc_cards ORDER BY sort, id")]


def get(cid: str) -> dict:
    seed_if_empty()
    cid = (cid or "").strip()
    r = db.one("SELECT * FROM desc_cards WHERE id=?", (cid,))
    if not r:
        raise OranteError(f"没有卡片 {cid}")
    return _row(r)


def _next_id() -> str:
    return db.next_id("desc_card", "C", 3)


def _next_sort() -> int:
    r = db.one("SELECT MAX(sort) n FROM desc_cards")
    return int((r or {}).get("n") or 0) + 1


def create(name: str, prompt: str, *, copy_of: str | None = None) -> dict:
    seed_if_empty()
    name = (name or "").strip()
    prompt = prompt if isinstance(prompt, str) else ""
    if not name:
        raise OranteError("卡片要有名字")
    cid = _next_id()
    now = db.now()
    db.run(
        "INSERT INTO desc_cards(id,name,prompt,sort,builtin,created,updated)"
        " VALUES(?,?,?,?,0,?,?)",
        (cid, name, prompt, _next_sort(), now, now))
    return get(cid)


def copy(cid: str) -> dict:
    src = get(cid)
    return create(f"{src['name']} 副本", src["prompt"], copy_of=cid)


def update(cid: str, *, name=None, prompt=None) -> dict:
    cur = get(cid)
    if name is not None:
        name = str(name).strip()
        if not name:
            raise OranteError("卡片要有名字")
        cur["name"] = name
    if prompt is not None:
        cur["prompt"] = str(prompt)
    db.run(
        "UPDATE desc_cards SET name=?, prompt=?, updated=? WHERE id=?",
        (cur["name"], cur["prompt"], db.now(), cur["id"]))
    return get(cid)


def delete(cid: str) -> dict:
    cur = get(cid)
    db.run("DELETE FROM desc_cards WHERE id=?", (cur["id"],))
    return cur


def reorder(ids: list[str]) -> list[dict]:
    seed_if_empty()
    seen = set()
    ordered = []
    for i in ids or []:
        cid = str(i or "").strip()
        if not cid or cid in seen:
            continue
        if not db.one("SELECT 1 FROM desc_cards WHERE id=?", (cid,)):
            continue
        seen.add(cid)
        ordered.append(cid)
    for r in db.rows("SELECT id FROM desc_cards ORDER BY sort, id"):
        if r["id"] not in seen:
            ordered.append(r["id"])
    for i, cid in enumerate(ordered, 1):
        db.run("UPDATE desc_cards SET sort=?, updated=? WHERE id=?",
               (i, db.now(), cid))
    return list_all()


def move(cid: str, delta: int) -> list[dict]:
    """上移 / 下移一张。delta=-1 上移，+1 下移。"""
    rows = list_all()
    ids = [r["id"] for r in rows]
    if cid not in ids:
        raise OranteError(f"没有卡片 {cid}")
    i = ids.index(cid)
    j = i + int(delta)
    if j < 0 or j >= len(ids):
        return rows
    ids[i], ids[j] = ids[j], ids[i]
    return reorder(ids)


def restore_factory() -> list[dict]:
    """出厂四张回来（缺的补、改过的还原）。用户自建的不动。"""
    seed_if_empty()
    now = db.now()
    have = {r["id"] for r in db.rows("SELECT id FROM desc_cards")}
    for c in FACTORY:
        if c["id"] in have:
            db.run(
                "UPDATE desc_cards SET name=?, prompt=?, builtin=1, updated=? WHERE id=?",
                (c["name"], c["prompt"], now, c["id"]))
        else:
            db.run(
                "INSERT INTO desc_cards(id,name,prompt,sort,builtin,created,updated)"
                " VALUES(?,?,?,?,1,?,?)",
                (c["id"], c["name"], c["prompt"], c["sort"], now, now))
    cur = db.one("SELECT value FROM counters WHERE name=?", ("desc_card",))
    if not cur or int(cur.get("value") or 0) < 4:
        db.run("INSERT OR REPLACE INTO counters(name,value) VALUES(?,?)", ("desc_card", 4))
    return list_all()


def dump_rows() -> list[dict]:
    """进预设的那一份。"""
    return [{
        "id": r["id"], "name": r["name"], "prompt": r["prompt"],
        "sort": r["sort"], "builtin": r["builtin"],
    } for r in list_all()]


def apply_rows(incoming: list, mode: str) -> dict:
    """预设导入。同名冲突跟 prompts 一样：merge_preset 以预设为准，merge_local 留本机。"""
    applied = {"overwrite": 0, "add": 0, "skip": 0}
    rows = incoming if isinstance(incoming, list) else []
    if mode == "replace":
        db.run("DELETE FROM desc_cards")
        now = db.now()
        max_n = 0
        for i, raw in enumerate(rows, 1):
            if not isinstance(raw, dict):
                continue
            cid = str(raw.get("id") or "").strip() or f"C{i:03d}"
            name = str(raw.get("name") or "").strip() or cid
            prompt = str(raw.get("prompt") or "")
            sort = int(raw.get("sort") or i)
            builtin = 1 if raw.get("builtin") else 0
            db.run(
                "INSERT INTO desc_cards(id,name,prompt,sort,builtin,created,updated)"
                " VALUES(?,?,?,?,?,?,?)",
                (cid, name, prompt, sort, builtin, now, now))
            applied["add"] += 1
            m = re.match(r"^C(\d+)$", cid, re.I)
            if m:
                max_n = max(max_n, int(m.group(1)))
        if max_n:
            db.run("INSERT OR REPLACE INTO counters(name,value) VALUES(?,?)",
                   ("desc_card", max_n))
        return applied

    local = list_all()
    by_name = {r["name"]: r for r in local}
    by_id = {r["id"]: r for r in local}
    now = db.now()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        prompt = str(raw.get("prompt") or "")
        builtin = 1 if raw.get("builtin") else 0
        sort = raw.get("sort")
        hit = by_name.get(name)
        if hit:
            if mode == "merge_local":
                applied["skip"] += 1
                continue
            db.run(
                "UPDATE desc_cards SET prompt=?, builtin=?, updated=? WHERE id=?",
                (prompt, builtin, now, hit["id"]))
            applied["overwrite"] += 1
            continue
        cid = str(raw.get("id") or "").strip()
        if not cid or cid in by_id:
            cid = _next_id()
        st = int(sort) if sort is not None else _next_sort()
        db.run(
            "INSERT INTO desc_cards(id,name,prompt,sort,builtin,created,updated)"
            " VALUES(?,?,?,?,?,?,?)",
            (cid, name, prompt, st, builtin, now, now))
        applied["add"] += 1
        by_id[cid] = {"id": cid, "name": name}
        by_name[name] = {"id": cid, "name": name}
    return applied


def identity_from_desc(desc: str) -> str:
    """描述语里「谁、衣着、发型」那一段。取不到就空串。"""
    text = (desc or "").strip()
    if not text:
        return ""
    # 去掉末尾机读标签【闭眼|常规】
    text = re.sub(r"【[^】]*】\s*$", "", text).strip()
    text = text.split("\n", 1)[0].strip()
    for sep in ("。", "；"):
        if sep in text:
            text = text.split(sep, 1)[0].strip()
            break
    return text


def expand_tail() -> str:
    """prompts.expand 的通用尾巴（画质、真实感），不走覆盖库随机机位。"""
    raw = promptsmod.get_block("expand")
    if covlib.is_lib(raw):
        return str(covlib.sanitize(raw).get("tail") or "").strip()
    return ""


def build_card_prompt(card_prompt: str, identity: str = "", *,
                      no_frontal: bool = False) -> str:
    """卡片机位 + 人物身份 + expand 尾巴。不走 coverage.pick。"""
    parts = []
    body = (card_prompt or "").strip()
    if body:
        parts.append(body)
    ident = (identity or "").strip()
    if ident:
        parts.append(ident)
    tail = expand_tail()
    if tail:
        t = tail.lstrip("。.;；,， ").strip()
        if t:
            parts.append(t)
    # no_frontal 不进正文，只作为调用约定留给生图路径
    _ = bool(no_frontal)
    return "\n".join(parts)


def resolve_identity_ref(m: Manifest, d: Path, sg: dict) -> dict:
    """这段换图卡片用哪张当身份参考。

    默认 = 这段现在的图；chain 模式下 s002 之后用 sg['chain']['ref_file']；
    再没有就用任务 expand_ref 第一张。
    返回 {path, file, ref, kind, desc}。找不到抛错。
    """
    d = Path(d)

    def _ok(fp: Path, ref: str, kind: str, desc: str = "") -> dict | None:
        if not fp.is_file():
            return None
        try:
            rel = str(fp.relative_to(d)).replace("\\", "/")
        except ValueError:
            rel = str(fp).replace("\\", "/")
        return {
            "path": fp, "file": rel, "ref": ref, "kind": kind, "desc": desc or "",
        }

    ch = sg.get("chain") if isinstance(sg.get("chain"), dict) else {}
    sn = 0
    try:
        sn = int(sg.get("s") or 0)
    except (TypeError, ValueError):
        sn = 0
    # 接龙 s002 之后：段上的 image 只是起头图占位，身份参考用记录的尾帧
    use_tail = (
        pipeline.is_chain_mode(m)
        and sn > 1
        and not pipeline.is_chain_restart(sg)
    )
    if use_tail:
        rel = str(ch.get("ref_file") or "").replace("\\", "/")
        if rel:
            fp = d / rel
            ref = str(ch.get("ref_from") or "")
            desc = str(ch.get("desc") or "")
            hit = _ok(fp, ref, "chain_tail", desc)
            if hit:
                return hit

    p = str(sg.get("image") or "").strip()
    im = m.image(p) if p else None
    if im:
        fp = d / (im.get("file") or "")
        hit = _ok(fp, p, "image", im.get("desc") or "")
        if hit:
            return hit

    rel = str(ch.get("ref_file") or "").replace("\\", "/")
    if rel:
        fp = d / rel
        ref = str(ch.get("ref_from") or "")
        desc = str(ch.get("desc") or "")
        hit = _ok(fp, ref, "chain_tail", desc)
        if hit:
            return hit

    for im in m.images or []:
        if im.get("expand_ref") is True:
            fp = d / (im.get("file") or "")
            hit = _ok(fp, str(im.get("p") or ""), "expand_ref", im.get("desc") or "")
            if hit:
                return hit

    for im in m.images or []:
        if im.get("role") == "主体" and im.get("status") == "ok":
            fp = d / (im.get("file") or "")
            hit = _ok(fp, str(im.get("p") or ""), "expand_ref", im.get("desc") or "")
            if hit:
                return hit

    raise OranteError("没有可用的身份参考图", "这段没有配图，接龙参考也找不到。")


def identity_ref_view(m: Manifest, d: Path, sg: dict) -> dict | None:
    """给收片面板的轻量结构，找不到就 None。"""
    try:
        hit = resolve_identity_ref(m, d, sg)
    except OranteError:
        return None
    fp = hit["path"]
    mtime = 0
    try:
        mtime = int(fp.stat().st_mtime)
    except OSError:
        pass
    return {
        "file": hit["file"], "ref": hit["ref"], "kind": hit["kind"],
        "mtime": mtime,
    }


def _wipe_dir_new(folder: Path, before: set) -> None:
    if not folder.is_dir():
        return
    after = {x.resolve() for x in folder.glob("*") if x.is_file()}
    for leftover in after - before:
        try:
            leftover.unlink()
        except OSError:
            pass


def _gen_card_image(eng, prompt: str, ref: Path, dest_dir: Path, job=None) -> Path:
    """生 1 张。显式 no_frontal=False，引擎不认这个参数就退回原签名。"""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    before = {x.resolve() for x in dest_dir.glob("*") if x.is_file()}
    LAST_GEN["prompt"] = prompt
    LAST_GEN["no_frontal"] = False
    LAST_GEN["ref"] = str(ref)
    try:
        tick = getattr(eng, "tick_credits", None)
        if callable(tick):
            tick()
        try:
            paths = eng.gen_images(
                prompt, ref=ref, n=1, dest_dir=dest_dir, no_frontal=False) or []
        except TypeError:
            paths = eng.gen_images(prompt, ref=ref, n=1, dest_dir=dest_dir) or []
    except BlockedImage:
        _wipe_dir_new(dest_dir, before)
        raise
    for fp in paths:
        fp = Path(fp)
        if fp.is_file():
            return fp
    _wipe_dir_new(dest_dir, before)
    raise OranteError("没出图", "引擎没有返回文件。")


def _ingest_card(job, m: Manifest, d: Path, src: Path, card: dict, card_ref: str) -> dict:
    """新图进图池：新 p 号、images/ 常规位置、不覆盖旧图。"""
    p = expand._next_p(m.images)
    ext = src.suffix or ".jpg"
    rel = f"{expand.IMG_DIR}/{p}{ext}"
    dest = d / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file():
        dest = d / expand.IMG_DIR / f"{p}-{int(time.time())}{ext}"
        rel = dest.relative_to(d).as_posix()
    shutil.copy2(str(src), str(dest))
    try:
        src.unlink()
    except OSError:
        pass
    rec = expand._new_image_rec(
        p, rel, "主体", "ok", f"card:{card['id']}",
        sha1=sha1_file(dest) if dest.is_file() else "")
    rec["card_name"] = card.get("name") or ""
    rec["card_ref"] = card_ref
    m.images.append(rec)
    m.save(d)
    job.log(f"  · 新图进图池 {p} → {rel}（origin=card:{card['id']}）")
    return rec


@register("card_swap", account_bound=True)
def card_swap(job, s, card_id: str, auto_redo: bool = True, redo_account_id: str | None = None):
    """按描述卡片生一张新图，钉进这一段，可选立刻重生。不覆盖图池里任何旧图。"""
    if not job.task_id:
        raise OranteError("card_swap 需要 task_id")
    try:
        sn = int(s)
    except (TypeError, ValueError):
        raise OranteError("段号不对", "body 里要有整数 s。")
    if isinstance(auto_redo, str):
        auto_redo = auto_redo.strip().lower() in ("1", "true", "yes", "on")
    else:
        auto_redo = bool(auto_redo) if auto_redo is not None else True
    card = get(card_id)
    t = taskmod.get(job.task_id)
    d = expand._task_dir(job.task_id)
    m = Manifest.load(d)
    sg = m.seg(sn)
    if not sg:
        raise OranteError(f"没有段 {sn}")

    aid = job.account_id or expand.resolve_account_id(t)
    acc = accounts.get(aid)
    acc_name = acc.get("name") or aid
    job.log(f"账号 {acc.get('engine')} · {acc_name}（{aid}）")

    ident = resolve_identity_ref(m, d, sg)
    job.log(f"身份参考 {ident['ref'] or ident['file']}（{ident['kind']}）→ {ident['file']}")

    ident_text = identity_from_desc(ident.get("desc") or "")
    # 正脸卡片必须绕过 expand.no_frontal，否则永远生不出正脸
    prompt = build_card_prompt(card["prompt"], ident_text, no_frontal=False)
    job.log("完整提示词：\n" + prompt)

    work = d / CARD_DIR
    work.mkdir(parents=True, exist_ok=True)
    before = {x.resolve() for x in work.glob("*") if x.is_file()}

    expand._prog(job, phase="connect", current=f"连接 {acc.get('engine')} · {acc_name}",
                 done=0, total=1)
    eng = expand._connect_engine(job)
    try:
        expand._prog(job, phase="gen", current=f"按「{card['name']}」生图")
        try:
            try:
                pick = _gen_card_image(eng, prompt, ident["path"], work, job=job)
            except CreditHalted as e:
                notify_credit_halt(job, eng, e)
                wait_credit_continue(job, eng)
                pick = _gen_card_image(eng, prompt, ident["path"], work, job=job)
        except BlockedImage as e:
            _wipe_dir_new(work, before)
            raise OranteError(
                f"按「{card['name']}」生图被平台拒收",
                str(e) or "换一张参考图，或换账号再试。")
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

    rec = _ingest_card(job, m, d, Path(pick), card, ident["ref"])
    new_p = rec["p"]

    from . import ai
    try:
        gemini = ai.client(job)
    except Exception:
        gemini = None
    expand._auto_describe(job, m, d, rec, gemini)
    m.save(d)

    reviewmod.set_image(job.task_id, sn, new_p)
    m = Manifest.load(d)
    sg = m.seg(sn)
    if sg:
        sg["status"] = "planned"
        sg["error"] = None
        if pipeline.is_chain_mode(m):
            # 67 号换图起头：这段从新图起，下一段从这段新尾帧接
            sg["gen_mode"] = pipeline.MODE_FIRST
            job.log(f"  · s{sn:03d} 段级模式改为 first_frame（换图起头）")
        m.save(d)
    job.log(f"✓ s{sn:03d} 已换成 {new_p}，段打回 planned")

    redo_job = None
    if auto_redo:
        from .. import jobs as jobmod
        # 规格 98：前端选了别的账号（比如本地模型）就用它重生
        if redo_account_id:
            j = jobmod.start("regenerate", job.task_id, segs=[sn], account_id=str(redo_account_id))
        else:
            j = jobmod.start("regenerate", job.task_id, segs=[sn])
        redo_job = getattr(j, "id", None) or (j.get("id") if isinstance(j, dict) else None)
        job.log(f"  · 已起重生 job {redo_job or ''}")

    events.emit("tasks.changed")
    toast = f"✓ s{sn:03d} 已按「{card['name']}」生成 {new_p} 并换上"
    if auto_redo:
        toast += "，正在重生"
    expand._prog(job, phase="done", current=toast, done=1, total=1)
    job.log(toast)
    return {
        "s": sn, "p": new_p, "card_id": card["id"], "card_name": card["name"],
        "auto_redo": bool(auto_redo), "redo_job": redo_job, "toast": toast,
        "ref": ident["ref"], "prompt": prompt,
    }
