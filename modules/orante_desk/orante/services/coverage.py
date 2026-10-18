# -*- coding: utf-8 -*-
"""机位 × 动作覆盖库：配对、均匀轮转、成品提示词拼装。

库文件：orante/legacy/coverage_library.json（打包默认，用户改动走 prompts.expand）。
配对规则：0 <= 相机 rank - 动作 rank <= 1（看不见的动作等于没改）。
no_frontal=True 时排除 cameras[].frontal 机位（关掉正脸）。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from .. import paths
from ..errors import OranteError

_DEFAULT_CACHE: dict | None = None

# rank：画面里看得见多少。1 只看得见脸 / 2 上半身 / 3 手也在画面里 / 4 全身
RANK_MIN, RANK_MAX = 1, 4
CLASSES = ("A", "B", "C", "D")
PHASES = ("start", "develop", "hold", "resolve")


def default_path() -> Path:
    return paths.LEGACY / "coverage_library.json"


def default_lib() -> dict:
    """打包那份，深拷贝。中文句子一个字都不改。"""
    global _DEFAULT_CACHE
    if _DEFAULT_CACHE is None:
        p = default_path()
        if not p.is_file():
            raise OranteError(
                "找不到覆盖库",
                f"没有 {p.name}，扩图没法挑机位。")
        try:
            _DEFAULT_CACHE = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            raise OranteError("覆盖库文件坏了", str(e))
        if not isinstance(_DEFAULT_CACHE, dict):
            raise OranteError("覆盖库不是 JSON 对象")
    return copy.deepcopy(_DEFAULT_CACHE)


def is_lib(obj) -> bool:
    return isinstance(obj, dict) and isinstance(obj.get("cameras"), list)


def _as_int(v, default: int = 1) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(RANK_MIN, min(RANK_MAX, n))


def _as_id(v, fallback: str) -> str:
    s = str(v or "").strip()
    return s if s else fallback


def _as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v or "").strip().lower()
    return s in ("1", "true", "yes", "on", "是")


def _clean_row(row, i: int, kind: str) -> dict:
    row = row if isinstance(row, dict) else {}
    fid = _as_id(row.get("id"), f"{i + 1:02d}")
    out = {
        "id": fid,
        "name": str(row.get("name") or fid),
        "rank": _as_int(row.get("rank"), 2),
        "text": str(row.get("text") or ""),
    }
    if kind == "camera":
        cls = str(row.get("class") or "A").strip().upper()[:1] or "A"
        out["class"] = cls if cls in CLASSES else "A"
        # v2 用 frontal；旧库 front 当同义，读到就算
        if "frontal" in row:
            out["frontal"] = _as_bool(row.get("frontal"))
        else:
            out["frontal"] = _as_bool(row.get("front"))
    else:
        ph = str(row.get("phase") or "hold").strip().lower()
        out["phase"] = ph if ph in PHASES else "hold"
    return out


def sanitize(lib) -> dict:
    """用户表可能缺字段、id 撞车、rank 越界：收成能配对的形状，不丢文字。"""
    src = lib if isinstance(lib, dict) else {}
    base = default_lib()
    cams, seen_c = [], set()
    for i, row in enumerate(src.get("cameras") if isinstance(src.get("cameras"), list) else []):
        c = _clean_row(row, i, "camera")
        if c["id"] in seen_c:
            c["id"] = f"{c['id']}-{i + 1}"
        seen_c.add(c["id"])
        cams.append(c)
    acts, seen_a = [], set()
    for i, row in enumerate(src.get("actions") if isinstance(src.get("actions"), list) else []):
        a = _clean_row(row, i, "action")
        if a["id"] in seen_a:
            a["id"] = f"{a['id']}-{i + 1}"
        seen_a.add(a["id"])
        acts.append(a)
    return {
        "version": int(src.get("version") or base.get("version") or 1),
        "note": str(src.get("note") if src.get("note") is not None else base.get("note") or ""),
        "head": str(src.get("head") if src.get("head") is not None else base.get("head") or ""),
        "tail": str(src.get("tail") if src.get("tail") is not None else base.get("tail") or ""),
        "cameras": cams,
        "actions": acts,
    }


def pair_ok(cam_rank: int, act_rank: int) -> bool:
    """合法 ⟺ 0 <= 相机 rank - 动作 rank <= 1。"""
    try:
        d = int(cam_rank) - int(act_rank)
    except (TypeError, ValueError):
        return False
    return 0 <= d <= 1


def legal_pairs(lib: dict, no_frontal: bool = False) -> list[tuple[dict, dict]]:
    lib = lib if isinstance(lib, dict) else {}
    cams = [c for c in (lib.get("cameras") or []) if isinstance(c, dict)]
    if no_frontal:
        cams = [c for c in cams if not c.get("frontal")]
    acts = [a for a in (lib.get("actions") or []) if isinstance(a, dict)]
    out = []
    for c in cams:
        for a in acts:
            if pair_ok(c.get("rank"), a.get("rank")):
                out.append((c, a))
    return out


def legal_count(lib: dict, no_frontal: bool = False) -> int:
    return len(legal_pairs(lib, no_frontal=no_frontal))


def pair_key(cam, act) -> tuple[str, str]:
    cid = str((cam or {}).get("id") or "") if isinstance(cam, dict) else str(cam or "")
    aid = str((act or {}).get("id") or "") if isinstance(act, dict) else str(act or "")
    return (cid, aid)


def used_pairs(images) -> set[tuple[str, str]]:
    """已经写进 manifest.images[].cov 的二元组（跨轮次记忆）。"""
    out: set[tuple[str, str]] = set()
    for im in images or []:
        if not isinstance(im, dict):
            continue
        cov = im.get("cov") or {}
        if not isinstance(cov, dict):
            continue
        cid = str(cov.get("camera") or "").strip()
        aid = str(cov.get("action") or "").strip()
        if cid and aid:
            out.add((cid, aid))
    return out


def compose(lib: dict, cam: dict, act: dict, clause: str = "") -> str:
    """成品提示词 = head + [clause] + camera.text + ， + action.text + tail。"""
    lib = lib if isinstance(lib, dict) else {}
    cam = cam if isinstance(cam, dict) else {}
    act = act if isinstance(act, dict) else {}
    head = str(lib.get("head") or "")
    tail = str(lib.get("tail") or "")
    ct = str(cam.get("text") or "")
    at = str(act.get("text") or "")
    cl = str(clause or "")
    return head + cl + ct + "，" + at + tail


def cov_meta(cam: dict, act: dict) -> dict:
    cam = cam if isinstance(cam, dict) else {}
    act = act if isinstance(act, dict) else {}
    return {
        "camera": str(cam.get("id") or ""),
        "camera_name": str(cam.get("name") or ""),
        "class": str(cam.get("class") or ""),
        "frontal": bool(cam.get("frontal")),
        "rank": int(cam.get("rank") or 0) or 0,
        "action": str(act.get("id") or ""),
        "action_name": str(act.get("name") or ""),
        "phase": str(act.get("phase") or ""),
        "action_rank": int(act.get("rank") or 0) or 0,
    }


def cov_distance(a: dict | None, b: dict | None) -> int:
    """两个 cov（景别 + 角度 + 动作）差多少。越大越远。

    景别看 camera rank / class；角度看 camera id 和 frontal；动作看 action / phase。
    """
    a = a if isinstance(a, dict) else {}
    b = b if isinstance(b, dict) else {}
    d = 0
    ra = a.get("rank")
    rb = b.get("rank")
    try:
        if ra is not None and rb is not None:
            d += abs(int(ra) - int(rb))
    except (TypeError, ValueError):
        pass
    ara = a.get("action_rank")
    arb = b.get("action_rank")
    try:
        if ara is not None and arb is not None:
            d += abs(int(ara) - int(arb))
    except (TypeError, ValueError):
        pass
    if (a.get("class") or "") != (b.get("class") or ""):
        d += 3
    if bool(a.get("frontal")) != bool(b.get("frontal")):
        d += 2
    if (a.get("camera") or "") != (b.get("camera") or ""):
        d += 2
    if (a.get("action") or "") != (b.get("action") or ""):
        d += 1
    if (a.get("phase") or "") != (b.get("phase") or ""):
        d += 1
    return d


def pick_farthest(lib: dict, n: int, from_cov: dict | None, *, used_across=None,
                  avoid=None, clause: str = "", no_frontal: bool = False) -> list[dict]:
    """合法组合里挑和 from_cov 差异最大的 n 条。形状和 pick() 一样。"""
    lib = sanitize(lib) if is_lib(lib) else default_lib()
    n = max(0, int(n or 0))
    pairs = legal_pairs(lib, no_frontal=no_frontal)
    if n <= 0 or not pairs:
        return []
    avoid = set(avoid or [])
    from_cov = from_cov if isinstance(from_cov, dict) else {}
    scored = []
    for cam, act in pairs:
        key = pair_key(cam, act)
        if key in avoid:
            continue
        meta = cov_meta(cam, act)
        scored.append((-cov_distance(from_cov, meta), str(cam.get("id") or ""),
                       str(act.get("id") or ""), cam, act, meta, key))
    scored.sort()
    out: list[dict] = []
    for item in scored:
        if len(out) >= n:
            break
        _neg, _cid, _aid, cam, act, meta, key = item
        avoid.add(key)
        out.append({
            "camera": cam,
            "action": act,
            "prompt": compose(lib, cam, act, clause),
            "cov": meta,
            "key": key,
        })
    return out


def _score(cam: dict, act: dict, *, used_c: set, used_a: set,
           used_across: set, avoid: set) -> tuple | None:
    key = pair_key(cam, act)
    if key in avoid:
        return None
    # 跨任务没用过的排前面；本轮没用过的机位/动作再往前
    across = 0 if key not in used_across else 1
    fresh_c = 0 if cam.get("id") not in used_c else 1
    fresh_a = 0 if act.get("id") not in used_a else 1
    return (across, fresh_c, fresh_a, str(cam.get("id") or ""), str(act.get("id") or ""))


def pick(lib: dict, n: int, *, used_across=None, avoid=None,
         clause: str = "", no_frontal: bool = False) -> list[dict]:
    """在合法组合里均匀轮转 n 条。同一轮二元组不重复。

    优先：本轮还没用过的机位 × 本轮还没用过的动作，且跨任务没用过。
    两个维度都轮完一遍，才允许开始复用机位或动作。
    class 不参与过滤。no_frontal=True 时排除 frontal 机位。
    """
    lib = sanitize(lib) if is_lib(lib) else default_lib()
    n = max(0, int(n or 0))
    pairs = legal_pairs(lib, no_frontal=no_frontal)
    if n <= 0 or not pairs:
        return []
    used_across = set(used_across or [])
    avoid = set(avoid or [])
    # 本轮已经拿过的机位/动作（含重生换下来的）先记上，别一重生又回到 01
    used_c: set[str] = {k[0] for k in avoid if isinstance(k, tuple) and k}
    used_a: set[str] = {k[1] for k in avoid if isinstance(k, tuple) and len(k) > 1}
    out: list[dict] = []
    for _ in range(n):
        best = None
        best_s = None
        for cam, act in pairs:
            s = _score(cam, act, used_c=used_c, used_a=used_a,
                       used_across=used_across, avoid=avoid)
            if s is None:
                continue
            if best_s is None or s < best_s:
                best_s = s
                best = (cam, act)
        if best is None:
            break
        cam, act = best
        key = pair_key(cam, act)
        avoid.add(key)
        used_c.add(str(cam.get("id") or ""))
        used_a.add(str(act.get("id") or ""))
        meta = cov_meta(cam, act)
        out.append({
            "camera": cam,
            "action": act,
            "prompt": compose(lib, cam, act, clause),
            "cov": meta,
            "key": key,
        })
    return out
