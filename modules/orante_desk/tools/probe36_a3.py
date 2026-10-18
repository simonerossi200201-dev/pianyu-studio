# -*- coding: utf-8 -*-
"""探针 A′ 第三轮：把尾帧 media_id 塞进图片槽的不同下标，看 Flow 认哪一格。

这不是 UI 点击，是用现有 eb1hJf 通道、只改图片那一格。Flow 若接受并返回 genId，
就证明那一格是尾帧位。4s Lite 低优先级，尽量少花额度。
"""
from __future__ import annotations
import json, sys, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orante import db, accounts, paths
from orante.engines.base import make_engine
from orante.engines.flow_api import model_id_for, _crop_box, RC_ACTION_GENERATE, _g

OUT = paths.DATA / "capture" / "probe36"
OUT.mkdir(parents=True, exist_ok=True)


def make_inner(eng, prompt, start_id, end_id, crop, duration, end_index, model=None):
    token = eng._recaptcha(RC_ACTION_GENERATE)
    mid_slot = [None, start_id, None, None, None, crop]
    if end_id is not None and end_index is not None:
        while len(mid_slot) <= end_index:
            mid_slot.append(None)
        mid_slot[end_index] = end_id
    model = model or model_id_for(eng.model_name, duration)
    job = [[None, None, [[[prompt]]]], model, 1, None,
           mid_slot,
           [None, None, eng.collection_id, None,
            str(uuid.uuid4()).upper(), str(uuid.uuid4()).upper()]]
    return [[job], [None, 22, None, None, None, eng.project_id, None, None, None, None,
                    [token, 1]],
            [str(uuid.uuid4()).upper(), 2]], mid_slot, model


def try_one(eng, prompt, start_id, end_id, crop, duration, end_index, model=None):
    captured = {"end_index": end_index, "model": None, "slot": None,
                "ok": False, "gen_id": None, "error": None}

    def inner():
        payload, slot, model_used = make_inner(
            eng, prompt, start_id, end_id, crop, duration, end_index, model)
        captured["slot"] = slot
        captured["model"] = model_used
        captured["payload_head"] = payload[0][0][:5]  # 不含 token
        return payload

    try:
        res = eng._rpc("eb1hJf", inner, raise_=False)
    except Exception as e:
        captured["error"] = f"{type(e).__name__}: {e}"[:300]
        return captured
    captured["rpc"] = {k: (str(res.get(k))[:300] if k != "payload" else None)
                       for k in ("ok", "status", "err", "raw")}
    payload = (res or {}).get("payload")
    gen_id = _g(payload, 3, 0, 0) if payload else None
    captured["gen_id"] = (gen_id or "")[:16] or None
    captured["ok"] = bool(gen_id)
    if not gen_id:
        captured["error"] = str((res or {}).get("err") or (res or {}).get("raw") or "")[:400]
    return captured


def redact_slot(slot):
    out = []
    for v in (slot or []):
        if isinstance(v, str) and len(v) > 12:
            out.append(v[:8] + "…")
        else:
            out.append(v)
    return out


def main():
    db.init()
    acc = [a for a in accounts.list_all() if a["engine"] == "flow" and a["enabled"]][0]
    d = Path(r"D:\1_PROJECT\2026-09-07\B\T0013-李良v04")
    img1 = next((d / "images").glob("p03.jp*g"))
    img2 = next((d / "images").glob("p04.jp*g"))
    print("start", img1.name, "end", img2.name)
    eng = make_engine(acc, print)
    rows = []
    try:
        eng.connect()
        import time as _t
        _t.sleep(2)
        print("health", eng.health(), flush=True)
        mid1 = eng.upload_image(img1)
        print("start media", (mid1 or "")[:12], flush=True)
        mid2 = eng.upload_image(img2)
        print("end media", (mid2 or "")[:12], flush=True)
        crop = _crop_box(img1, acc.get("aspect") or "9:16")
        prompt = "The woman stands and speaks one short sentence. Natural motion, no cut."
        # 先只改下标，模型保持现有 i2v；4s
        for idx in (2, 3, 4):
            print(f"\n=== 尾帧塞下标 {idx} ===", flush=True)
            rec = try_one(eng, prompt, mid1, mid2, crop, 4, idx)
            rec["slot_redacted"] = redact_slot(rec.get("slot"))
            print("  ok", rec["ok"], "gen", rec.get("gen_id"), "err", rec.get("error"), flush=True)
            rows.append(rec)
            if rec["ok"]:
                break
        # 如果都不行，试 _fl 模型 + 下标 2
        if not any(r["ok"] for r in rows):
            for model in ("veo_3_1_i2v_s_lite_4s_low_priority",
                          "veo_3_1_i2v_s_lite_4s_fl_low_priority",
                          "veo_3_1_i2v_s_lite_fl_4s_low_priority"):
                print(f"\n=== 下标 2 + 模型 {model} ===", flush=True)
                rec = try_one(eng, prompt, mid1, mid2, crop, 4, 2, model=model)
                rec["slot_redacted"] = redact_slot(rec.get("slot"))
                print("  ok", rec["ok"], "gen", rec.get("gen_id"), "err", rec.get("error"), flush=True)
                rows.append(rec)
                if rec["ok"]:
                    break
        # 对照：不下尾帧，确认通道本身通
        print("\n=== 对照：只有首帧 ===", flush=True)
        rec = try_one(eng, prompt, mid1, None, crop, 4, None)
        rec["slot_redacted"] = redact_slot(rec.get("slot"))
        print("  ok", rec["ok"], "gen", rec.get("gen_id"), "err", rec.get("error"), flush=True)
        rows.append(rec)
    finally:
        try:
            eng.close()
        except Exception:
            pass
    out = []
    for r in rows:
        out.append({k: v for k, v in r.items() if k not in ("payload_head",)})
    (OUT / "a-slot-probe.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote a-slot-probe.json")


if __name__ == "__main__":
    main()
