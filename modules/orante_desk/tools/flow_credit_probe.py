# -*- coding: utf-8 -*-
"""规格 57 B 节：在指定账号上量一次真余额。

只许：生图 ×1（GEM_PIX_2）+ 视频 ×1（veo_3_1_i2v_lite_low_priority 8s）。
每步前后 nzlxg，记下 ogiZ0b/eb1hJf 的 [1]。最后换另一个账号只查 nzlxg。

用法：python tools/flow_credit_probe.py [主账号=TIK-D1] [对照账号=TIK-L1]
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orante import accounts, db, paths  # noqa: E402
from orante.engines.flow_api import (  # noqa: E402
    FLOW_FREE_MODEL_IDS,
    FlowEngine,
    IMAGE_MODELS,
    MODEL_FAMILY,
    _first_int,
    _g,
    model_id_for,
)

OUT_DIR = paths.DATA / "capture" / "probe57"
PROMPT_IMG = "同一个人物，同样的服装和姿态，只改变拍摄角度：近景侧面。"
PROMPT_VID = "镜头缓慢向前推进，人物站在画面中央。"


def _acc(name: str) -> dict:
    for a in accounts.list_all(probe=False):
        if a.get("name") == name:
            return a
    names = [a.get("name") for a in accounts.list_all(probe=False)]
    raise SystemExit(f"没有账号 {name}，有：{names}")


def _pick_ref(acc_id: str) -> Path:
    """优先用 cache 里已经为这个账号上传过的本地图。"""
    import hashlib
    cache_file = paths.DATA / "flow_media_cache.json"
    want = set()
    if cache_file.is_file():
        try:
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
            want = {k.split(":", 1)[1] for k in cache if k.startswith(acc_id + ":")}
        except Exception:
            want = set()
    search = []
    for folder in (paths.DATA / "tmp", paths.DATA / "capture"):
        if folder.is_dir():
            search.extend(folder.glob("*.jpg"))
            search.extend(folder.glob("*.jpeg"))
            search.extend(folder.glob("*.png"))
    for p in search:
        try:
            sha = hashlib.sha1(p.read_bytes()).hexdigest()
        except Exception:
            continue
        if sha in want:
            return p
    for p in search:
        if p.is_file() and p.stat().st_size > 10_000:
            return p
    raise SystemExit("data/tmp 和 data/capture 里找不到可用的参考图")


def _costish(obj, out=None, path=""):
    """从响应里捞名字像 cost/credit/quota 的片段。"""
    out = out if out is not None else []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            if re_cost(k):
                out.append((p, v))
            _costish(v, out, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _costish(v, out, f"{path}[{i}]")
    elif isinstance(obj, str) and re_cost(obj) and len(obj) < 80:
        out.append((path, obj))
    return out


def re_cost(s) -> bool:
    t = str(s).lower()
    return any(w in t for w in ("cost", "credit", "crediti", "quota", "balance", "token"))


def _snip(obj, n=400) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False)
    except Exception:
        s = str(obj)
    return s[:n]


def run(primary="TIK-D1", other="TIK-L1") -> dict:
    db.init()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    acc = _acc(primary)
    img = _pick_ref(acc["id"])
    log_lines = []

    def log(msg: str) -> None:
        print(msg, flush=True)
        log_lines.append(msg)

    result = {
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "primary": primary,
        "other": other,
        "ref": str(img),
        "steps": {},
    }

    eng = FlowEngine(acc, log)
    # 探针自己在量，别让守卫把自己拦了
    eng._credit_continue = True
    try:
        log(f"▶ 连接 {primary}")
        eng.connect()
        b0 = eng.credits()
        result["steps"]["0_connect"] = {"B0": b0, "raw": _snip(getattr(eng, "_credits_last", None))}
        log(f"  B0={b0}")

        log(f"▶ 生图 ×1 GEM_PIX_2  参考 {img.name}")
        ref_id = eng.upload_image(img)
        captured = {}

        orig_rpc = eng._rpc

        def wrap_rpc(rpc, inner, retries=2, raise_=True):
            payload = orig_rpc(rpc, inner, retries=retries, raise_=raise_)
            if rpc in ("ogiZ0b", "eb1hJf", "nzlxg"):
                captured[rpc] = payload
            return payload

        eng._rpc = wrap_rpc
        r = eng.gen_image_once(PROMPT_IMG, ref_id, model="pro")
        ogi = captured.get("ogiZ0b")
        b1 = eng.credits()
        cost_hits = _costish(ogi)
        result["steps"]["1_image"] = {
            "B1": b1,
            "ogiZ0b[1]": _g(ogi, 1) if isinstance(ogi, list) else None,
            "media_id": (r or {}).get("media_id"),
            "cost_like": [(p, _snip(v, 80)) for p, v in cost_hits[:12]],
        }
        log(f"  B1={b1}  ogiZ0b[1]={result['steps']['1_image']['ogiZ0b[1]']}")
        log(f"  cost-like: {result['steps']['1_image']['cost_like'] or '无'}")

        b2 = eng.credits()
        log("▶ 等 20 秒再查 nzlxg")
        time.sleep(20)
        b2p = eng.credits()
        result["steps"]["2_delay20"] = {"B2": b2, "B2_prime": b2p}
        log(f"  B2={b2}  B2'={b2p}")

        mid = model_id_for(eng.model_name, 8)
        log(f"▶ 视频 ×1 {mid} 8s")
        captured.clear()
        gid = eng.submit({"s": 1}, img, PROMPT_VID, 8)
        eb = captured.get("eb1hJf")
        b3 = eng.credits()
        slot1 = _g(eb, 1) if isinstance(eb, list) else None
        result["steps"]["3_video_submit"] = {
            "B3": b3, "eb1hJf[1]": slot1, "gen_id": gid, "model": mid,
        }
        log(f"  B3={b3}  eb1hJf[1]={slot1}  gen_id={gid}")

        log("▶ 轮询到完成（状态 3）")
        t0 = time.time()
        last = None
        while time.time() - t0 < 900:
            pol = eng.poll([gid])
            last = pol.get(gid) or {}
            st = last.get("state")
            log(f"  poll {st} raw={last.get('raw')}")
            if st in ("done", "failed"):
                break
            time.sleep(8)
        b4 = eng.credits()
        log("▶ 完成后立刻 nzlxg，再等 60 秒")
        time.sleep(60)
        b4p = eng.credits()
        result["steps"]["4_done"] = {
            "poll": {"state": (last or {}).get("state"), "raw": (last or {}).get("raw")},
            "B4": b4, "B4_prime": b4p,
        }
        log(f"  B4={b4}  B4'={b4p}")
    finally:
        try:
            eng.close()
        except Exception:
            pass

    # 换号只查
    acc2 = _acc(other)
    eng2 = FlowEngine(acc2, log)
    eng2._credit_continue = True
    try:
        log(f"▶ 换到 {other} 只做 nzlxg")
        eng2.connect()
        b5 = eng2.credits()
        result["steps"]["5_other"] = {"B5": b5, "account": other}
        log(f"  B5={b5}")
    finally:
        try:
            eng2.close()
        except Exception:
            pass

    result["conclusion"] = _conclude(result)
    result["family_pool"] = _family(result)
    result["log"] = log_lines
    return result


def _conclude(result: dict) -> str:
    s = result.get("steps") or {}
    b0 = (s.get("0_connect") or {}).get("B0")
    b1 = (s.get("1_image") or {}).get("B1")
    b2p = (s.get("2_delay20") or {}).get("B2_prime")
    b3 = (s.get("3_video_submit") or {}).get("B3")
    b4 = (s.get("4_done") or {}).get("B4")
    b4p = (s.get("4_done") or {}).get("B4_prime")
    slot_img = (s.get("1_image") or {}).get("ogiZ0b[1]")
    slot_vid = (s.get("3_video_submit") or {}).get("eb1hJf[1]")
    chain = [("B0", b0), ("B1", b1), ("B2'", b2p), ("B3", b3), ("B4", b4), ("B4'", b4p)]
    nums = [(k, v) for k, v in chain if isinstance(v, int)]
    dropped = []
    for i in range(1, len(nums)):
        if nums[i][1] < nums[i - 1][1]:
            dropped.append((nums[i - 1][0], nums[i - 1][1], nums[i][0], nums[i][1],
                            nums[i - 1][1] - nums[i][1]))
    b_same = len({v for _, v in nums}) <= 1 if nums else False
    slot_changed = False
    if isinstance(slot_vid, int) and isinstance(b0, int) and slot_vid != b0:
        slot_changed = True
    if isinstance(slot_vid, int) and isinstance(b4p, int) and slot_vid != b4p:
        slot_changed = True
    # ogiZ0b[1] 0907 不是数字，不算「变了」
    if dropped:
        bits = "；".join(f"{a}={av} → {b}={bv}（-{d}）" for a, av, b, bv, d in dropped)
        return f"是额度且某一步扣了：{bits}"
    if b_same and slot_changed:
        return f"[1] 不是额度（B0…B4' 全程 {b0}，eb1hJf[1]={slot_vid}）"
    if b_same:
        return "是额度且 Orante 的生图/视频不扣（B0=B1=B2'=B4'）"
    return f"无法三选一：链={chain} ogi[1]={slot_img} eb1[1]={slot_vid}"


def _family(result: dict) -> str:
    s = result.get("steps") or {}
    b4p = (s.get("4_done") or {}).get("B4_prime")
    b5 = (s.get("5_other") or {}).get("B5")
    if isinstance(b4p, int) and isinstance(b5, int) and b4p == b5:
        return f"B5={b5} 等于 B4'={b4p} → 家庭计划共享一个池"
    return f"B5={b5} 与 B4'={b4p} 不等 → 不像共享池（或对照账号没连上）"


def render_md(result: dict) -> str:
    s = result.get("steps") or {}
    rows = [
        ("0 连上 nzlxg", "B0", (s.get("0_connect") or {}).get("B0"), ""),
        ("1 生图 GEM_PIX_2 ×1", "B1", (s.get("1_image") or {}).get("B1"),
         f"ogiZ0b[1]={(s.get('1_image') or {}).get('ogiZ0b[1]')}; "
         f"cost-like={(s.get('1_image') or {}).get('cost_like') or '无'}"),
        ("2 nzlxg + 20s", "B2 / B2'",
         f"{(s.get('2_delay20') or {}).get('B2')} / {(s.get('2_delay20') or {}).get('B2_prime')}", ""),
        ("3 视频 8s 提交", "B3", (s.get("3_video_submit") or {}).get("B3"),
         f"eb1hJf[1]={(s.get('3_video_submit') or {}).get('eb1hJf[1]')} "
         f"model={(s.get('3_video_submit') or {}).get('model')}"),
        ("4 完成 + 60s", "B4 / B4'",
         f"{(s.get('4_done') or {}).get('B4')} / {(s.get('4_done') or {}).get('B4_prime')}",
         f"poll={(s.get('4_done') or {}).get('poll')}"),
        ("5 对照账号 nzlxg", "B5", (s.get("5_other") or {}).get("B5"),
         (s.get("5_other") or {}).get("account") or ""),
    ]
    lines = [
        f"# 规格 57 探针 · {result.get('at')} · {result.get('primary')} / {result.get('other')}",
        "",
        f"参考图：`{result.get('ref')}`",
        "",
        "| 步 | 记 | 值 | 备注 |",
        "|---|---|---|---|",
    ]
    for a, b, c, d in rows:
        lines.append(f"| {a} | {b} | {c} | {d} |")
    lines += [
        "",
        "## 结论（三选一）",
        "",
        result.get("conclusion") or "（无）",
        "",
        "## 家庭池",
        "",
        result.get("family_pool") or "",
        "",
        "## 常量表（对照用）",
        "",
        f"- MODEL_FAMILY: {list(MODEL_FAMILY)}",
        f"- IMAGE_MODELS: {IMAGE_MODELS}",
        f"- FLOW_FREE_MODEL_IDS: {sorted(FLOW_FREE_MODEL_IDS)}",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    primary = sys.argv[1] if len(sys.argv) > 1 else "TIK-D1"
    other = sys.argv[2] if len(sys.argv) > 2 else "TIK-L1"
    result = run(primary, other)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md = render_md(result)
    (OUT_DIR / "result.md").write_text(md, encoding="utf-8")
    print()
    print(md)
    print(f"→ {OUT_DIR / 'result.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
