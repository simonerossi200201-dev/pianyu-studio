# -*- coding: utf-8 -*-
"""读最新（或指定）的 flow_capture_*.jsonl，把 ogiZ0b / eb1hJf 的模型 id、
响应 [1]、请求数量/尺寸，以及前后 nzlxg 打成表。

用法：
  python tools/flow_capture_parse.py [jsonl路径]
  不给路径就用 data/capture 里最新的 flow_capture_*.jsonl。
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAP = ROOT / "data" / "capture"


def _latest(path: Path | None = None) -> Path:
    if path:
        p = Path(path)
        if not p.is_file():
            raise SystemExit(f"找不到抓包文件：{p}")
        return p
    files = sorted(CAP.glob("flow_capture_*.jsonl"), key=lambda x: x.stat().st_mtime)
    if not files:
        raise SystemExit(f"{CAP} 里没有 flow_capture_*.jsonl")
    return files[-1]


def _parse_inner(post: str):
    if not post:
        return None
    qs = urllib.parse.parse_qs(post, keep_blank_values=True)
    freq = (qs.get("f.req") or [None])[0]
    if not freq:
        return None
    try:
        outer = json.loads(freq)
        inner_s = outer[0][0][1]
        if isinstance(inner_s, str):
            return json.loads(inner_s)
        return inner_s
    except Exception:
        return None


def _parse_payload(body: str, rpc: str):
    if not isinstance(body, str) or "wrb.fr" not in body:
        return None
    for ln in body.split("\n"):
        if not ln.startswith("[["):
            continue
        try:
            arr = json.loads(ln)
        except Exception:
            continue
        for it in arr:
            if isinstance(it, list) and it and it[0] == "wrb.fr" and len(it) > 2:
                if rpc and it[1] != rpc:
                    continue
                raw = it[2]
                if isinstance(raw, str):
                    try:
                        return json.loads(raw)
                    except Exception:
                        return raw
                return raw
    return None


def _first_int(obj):
    if isinstance(obj, bool):
        return None
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float) and obj.is_integer():
        return int(obj)
    if isinstance(obj, list):
        for x in obj:
            n = _first_int(x)
            if n is not None:
                return n
    return None


def _g(o, *path, default=None):
    for p in path:
        try:
            o = o[p]
        except (IndexError, KeyError, TypeError):
            return default
        if o is None:
            return default
    return o


def _rpc_of(rec: dict) -> str:
    url = rec.get("url") or ""
    m = re.search(r"rpcids=([^&]+)", url)
    if m:
        return m.group(1)
    return ""


def _eb1_fields(inner) -> dict:
    model = _g(inner, 0, 0, 1)
    count = _g(inner, 0, 0, 2)
    return {"model": model, "count": count, "size": None}


def _ogi_fields(inner) -> dict:
    model = _g(inner, 1, 0, 5)
    count = _g(inner, 2)
    size = _g(inner, 1, 0, 4)  # 0907 抓包这一位是 2
    seed = _g(inner, 1, 0, 3)
    return {"model": model, "count": count, "size": size, "seed": seed}


def parse_file(path: Path) -> dict:
    rows = []
    events = []
    for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        rpc = _rpc_of(rec)
        if rpc not in ("ogiZ0b", "eb1hJf", "nzlxg"):
            continue
        inner = _parse_inner(rec.get("postData") or "")
        payload = _parse_payload(rec.get("body") or "", rpc)
        slot1 = _g(payload, 1) if isinstance(payload, list) else None
        nz = _first_int(payload) if rpc == "nzlxg" else None
        fields = {}
        if rpc == "eb1hJf":
            fields = _eb1_fields(inner)
        elif rpc == "ogiZ0b":
            fields = _ogi_fields(inner)
        ev = {
            "line": i,
            "rpc": rpc,
            "model": fields.get("model"),
            "count": fields.get("count"),
            "size": fields.get("size"),
            "slot1": slot1 if rpc != "nzlxg" else nz,
            "nzlxg": nz,
            "t": rec.get("t"),
        }
        events.append(ev)

    # 给每条 ogiZ0b / eb1hJf 配上前后最近的 nzlxg
    table = []
    for i, ev in enumerate(events):
        if ev["rpc"] not in ("ogiZ0b", "eb1hJf"):
            continue
        before = after = None
        for j in range(i - 1, -1, -1):
            if events[j]["rpc"] == "nzlxg" and events[j]["nzlxg"] is not None:
                before = events[j]["nzlxg"]
                break
        for j in range(i + 1, len(events)):
            if events[j]["rpc"] == "nzlxg" and events[j]["nzlxg"] is not None:
                after = events[j]["nzlxg"]
                break
        table.append({
            "line": ev["line"],
            "rpc": ev["rpc"],
            "model": ev["model"],
            "count": ev["count"],
            "size": ev["size"],
            "slot1": ev["slot1"],
            "nzlxg_before": before,
            "nzlxg_after": after,
        })
        rows.append(ev)

    return {"file": str(path), "events": events, "table": table}


def render_md(parsed: dict) -> str:
    lines = [
        f"# Flow 抓包解析 · `{Path(parsed['file']).name}`",
        "",
        "| 行 | rpc | 模型 id | 数量 | 尺寸位 | 响应[1] | 前 nzlxg | 后 nzlxg |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in parsed["table"]:
        lines.append(
            f"| {r['line']} | {r['rpc']} | `{r['model']}` | {r['count']} | {r['size']} "
            f"| {r['slot1']} | {r['nzlxg_before']} | {r['nzlxg_after']} |"
        )
    if not parsed["table"]:
        lines.append("| — | — | 没有 ogiZ0b / eb1hJf | — | — | — | — | — |")
    nz = [e for e in parsed["events"] if e["rpc"] == "nzlxg"]
    lines += ["", f"nzlxg 出现 {len(nz)} 次：" + (", ".join(str(e['nzlxg']) for e in nz) or "无")]
    models = sorted({r["model"] for r in parsed["table"] if r.get("model")})
    lines += ["", "出现的模型 id："]
    if models:
        for m in models:
            lines.append(f"- `{m}`")
    else:
        lines.append("- （无）")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    path = _latest(Path(argv[0]) if argv else None)
    parsed = parse_file(path)
    md = render_md(parsed)
    out = CAP / "flow_capture_parsed.md"
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"→ 已写 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
