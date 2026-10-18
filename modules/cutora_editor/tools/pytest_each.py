# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""一个测试文件一个进程地跑全量 pytest，最后给一份汇总。

全量一口气跑时，某个文件里 Qt 原生崩溃（0xC0000005）会把整个 pytest 带走，
后面一半测试没跑、前面哪些红了也看不到。这里每个文件单独起一个进程：
崩了只算那一个文件，照样往下跑。汇总写到 docs/plan/queue/_log/_pytest_each.log。

**可以断点续跑**：每跑完一个文件就记进 _pytest_each.jsonl。中途关窗口、重启电脑，
再双击会从没跑完的那个文件接着跑（同一个代码版本才续，代码变了自动从头来）。
要强制从头跑：python tools/pytest_each.py --fresh

用法：python tools/pytest_each.py [--timeout 600] [--fresh] [tests/test_xxx.py ...]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(REPO, "docs", "plan", "queue", "_log")
LOG = os.path.join(LOG_DIR, "_pytest_each.log")
DONE = os.path.join(LOG_DIR, "_pytest_each.jsonl")
_SUM = re.compile(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed)")


def _crash_name(code: int) -> str:
    u = code & 0xFFFFFFFF
    return {0xC0000005: "访问冲突 0xC0000005", 0xC0000409: "栈溢出/快速失败 0xC0000409",
            0xC0000374: "堆损坏 0xC0000374"}.get(u, "退出码 %d (0x%08X)" % (code, u))


def run_one(path: str, timeout: int):
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rfE", path]
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout,
                              creationflags=flags)
        out, code = (proc.stdout or "") + (proc.stderr or ""), proc.returncode
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        return {"file": path, "code": None, "secs": time.time() - t0, "counts": {},
                "fails": [], "note": "超时（%d 秒）" % timeout, "tail": out[-3000:]}
    counts = {}
    for n, kind in _SUM.findall(out.splitlines()[-1] if out.strip() else ""):
        counts[kind] = int(n)
    fails = sorted(set(re.findall(r"^(?:FAILED|ERROR) (\S+)", out, re.M)))
    note = ""
    if code not in (0, 1, 5):   # 0 过 / 1 有失败 / 5 没收集到测试
        note = "进程崩溃：" + _crash_name(code)
    # 有失败时留下断言那几行（E / > 开头）和 -rfE 的简短原因，日志里直接看得到为什么红
    detail = []
    if fails:
        lines = out.splitlines()
        detail = [ln for ln in lines if ln.startswith(("E ", ">   ", "FAILED ", "ERROR "))][-40:]
    return {"file": path, "code": code, "secs": time.time() - t0, "counts": counts,
            "fails": fails, "note": note, "tail": out[-3000:] if note else "", "detail": detail}


def _head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                              capture_output=True, text=True).stdout.strip()
    except Exception:
        return ""


def _load_done(head: str) -> dict:
    """上次跑完的文件（同一个代码版本）。file -> result"""
    out = {}
    if not os.path.isfile(DONE):
        return out
    with open(DONE, encoding="utf-8") as fh:
        for ln in fh:
            try:
                rec = json.loads(ln)
            except ValueError:
                continue          # 断电时写了半行
            if rec.get("head") == head and rec.get("file"):
                out[rec["file"]] = rec
    return out


def _append_done(rec: dict) -> None:
    with open(DONE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _line(i, n, r):
    c = r.get("counts") or {}
    return "[%d/%d] %-52s %s  %.0fs %s" % (
        i, n, os.path.basename(r["file"]),
        " ".join("%s=%d" % kv for kv in sorted(c.items())) or "-",
        float(r.get("secs") or 0), r.get("note") or "")


def _write_log(head, files, done, started, finished):
    """整份日志按已完成的记录重写：续跑前后的结果都在里面。"""
    tmp = LOG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("=== pytest_each 开始 %s  HEAD=%s  文件数=%d  已完成 %d\n" % (
            started, head, len(files), sum(1 for f in files if f in done)))
        for i, f in enumerate(files, 1):
            r = done.get(f)
            if r is None:
                continue
            fh.write(_line(i, len(files), r) + "\n")
            for t in r.get("fails") or []:
                fh.write("    FAILED " + t + "\n")
            if r.get("detail"):
                fh.write("    ---- 失败原因 ----\n")
                fh.write("".join("      " + ln[:300] + "\n" for ln in r["detail"]))
            if r.get("tail"):
                fh.write("    ---- 输出末尾 ----\n")
                fh.write("".join("    " + ln + "\n" for ln in r["tail"].splitlines()[-40:]))
        if finished:
            rs = [done[f] for f in files if f in done]
            bad = [r for r in rs if r.get("fails") or r.get("note")]
            tot = {}
            for r in rs:
                for k, v in (r.get("counts") or {}).items():
                    tot[k] = tot.get(k, 0) + int(v)
            fh.write("\n=== 汇总：%d 个文件，passed=%d failed=%d error=%d skipped=%d；"
                     "有问题的文件 %d 个，崩溃/超时 %d 个\n" % (
                         len(rs), tot.get("passed", 0), tot.get("failed", 0),
                         tot.get("error", 0) + tot.get("errors", 0), tot.get("skipped", 0),
                         len(bad), sum(1 for r in rs if r.get("note"))))
            for r in bad:
                fh.write("  %s  %s\n" % (r["file"], r.get("note") or ("%d 条失败" % len(r.get("fails") or []))))
    os.replace(tmp, LOG)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--fresh", action="store_true", help="不续跑，从头来")
    ap.add_argument("--name", default="pytest_each",
                    help="日志名：_<name>.log / .jsonl（复测用别的名字，不覆盖全量那份）")
    ap.add_argument("files", nargs="*")
    args = ap.parse_args(argv)
    global LOG, DONE
    LOG = os.path.join(LOG_DIR, "_%s.log" % args.name)
    DONE = os.path.join(LOG_DIR, "_%s.jsonl" % args.name)
    files = args.files or sorted(
        f.replace("\\", "/") for f in glob.glob(os.path.join("tests", "test_*.py"), root_dir=REPO))
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    head = _head()
    if args.fresh and os.path.isfile(DONE):
        os.replace(DONE, DONE + ".old")
    done = _load_done(head)
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    left = [f for f in files if f not in done]
    if done and left:
        print("接着上次跑：已完成 %d 个，还剩 %d 个（代码版本 %s）" % (
            len(files) - len(left), len(left), head), flush=True)
    for i, f in enumerate(files, 1):
        if f in done:
            continue
        r = run_one(f, args.timeout)
        r["head"] = head
        done[f] = r
        _append_done(r)
        print(_line(i, len(files), r), flush=True)
        _write_log(head, files, done, started, finished=False)
    _write_log(head, files, done, started, finished=True)
    rs = [done[f] for f in files if f in done]
    bad = [r for r in rs if r.get("fails") or r.get("note")]
    print("\n全部跑完：%d 个文件，有问题的 %d 个。" % (len(rs), len(bad)))
    print("完整记录：" + LOG)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
