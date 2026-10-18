#!/usr/bin/env python
"""Robota · agy queue runner — drop-in replacement for Grok in CUTORA's queue_runner v2.

Zero edits to the target repo: this script loads the repo's own
``tools/queue_runner.py`` and swaps exactly one thing — the function that
runs the coding agent (``run_grok``) — for one that runs the Antigravity CLI
(``agy``) on your Ultra login.  Everything else (lock file, _inbox, _HALT,
.ok/.stop, golden guard, quarantine, auto-commit, revert + rescue tags) is the
repo's existing, battle-tested logic, untouched.

Added on top:
  * quota / rate-limit detection -> wait and retry instead of failing the task
  * per-task token usage appended to D:/0_Robota/logs/agy_usage.jsonl
  * permissions come from ~/.gemini/antigravity-cli/settings.json (allowlist),
    never --dangerously-skip-permissions

Usage:
  python agy_queue.py [--root D:/0_Dev/CUTORA] [--model claude-opus-5-5-high]
                      [--timeout-min 180] [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import types
from pathlib import Path

ROBOTA = Path(__file__).resolve().parent.parent
USAGE_LOG = ROBOTA / "logs" / "agy_usage.jsonl"
AGY = shutil.which("agy") or str(Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe")

QUOTA_MARKERS = (
    "quota", "rate limit", "rate-limit", "ratelimit", "resource_exhausted",
    "resource exhausted", "429", "too many requests", "usage limit", "limit reached",
)
MAX_PROMPT_CHARS = 24000          # Windows command line is 32767 UTF-16 chars total
QUOTA_WAIT_S = 30 * 60
QUOTA_MAX_WAITS = 12              # give up after ~6 h of waiting

PREAMBLE = """You are an unattended coding agent working in the repository at:
  {root}
There is NO human available to answer questions. Do not ask; decide, or stop.

Your task brief is the file `{spec}` (full text below).
Before starting, read `docs/plan/queue/_RULES_FOR_EVERY_TASK.md` and obey it strictly,
especially: write `docs/plan/queue/_done/{name}.ok` when done and verified,
write `docs/plan/queue/_done/{name}.stop` (with the reason) if you hit a red line,
and `git commit` only your own changes before you finish (never `git add -A`).
Some shell commands are blocked by policy (push, reset --hard, clean, rebase, deleting
files, downloading, pip install). A blocked command ENDS your run immediately, so:
  * edit files with your file-editing tools, not with shell redirection;
  * run ONE simple command at a time — no `;`, `&&` or `|` chains;
  * if something you need is blocked, write the .stop file explaining it instead.

==================== TASK BRIEF: {spec} ====================
{body}
"""

PREAMBLE_SHORT = """You are an unattended coding agent working in the repository at:
  {root}
There is NO human available. Read the task brief file `{spec}` and execute it exactly.
First read `docs/plan/queue/_RULES_FOR_EVERY_TASK.md` and obey it strictly (.ok / .stop
markers in docs/plan/queue/_done/{name}.*, commit only your own changes).
"""


def now() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_runner(root: Path) -> types.ModuleType:
    path = root / "tools" / "queue_runner.py"
    if not path.exists():
        raise SystemExit("queue_runner.py not found at %s" % path)
    spec = importlib.util.spec_from_file_location("cutora_queue_runner", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def build_prompt(root: Path, spec: str) -> str:
    name = Path(spec).stem
    spec_path = (root / spec) if not Path(spec).is_absolute() else Path(spec)
    try:
        body = spec_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        body = ""
    full = PREAMBLE.format(root=root, spec=spec, name=name, body=body)
    if body and len(full) <= MAX_PROMPT_CHARS:
        return full
    return PREAMBLE_SHORT.format(root=root, spec=spec, name=name)


def looks_like_quota(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in QUOTA_MARKERS)


def record_usage(name: str, model: str, rc: int, result: dict | None, secs: float) -> None:
    USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
    row = {"time": now(), "task": name, "model": model, "exit": rc, "wall_s": round(secs, 1)}
    if result:
        for k in ("status", "num_turns", "duration_seconds", "usage", "denied_actions", "conversation_id"):
            if k in result:
                row[k] = result[k]
    with USAGE_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def make_run_agy(root: Path, model: str, timeout_min: int, say):
    def run_agy(spec: str, log: Path) -> int:
        name = Path(spec).stem
        prompt = build_prompt(root, spec)
        cmd = [AGY, "-p", prompt, "--model", model, "--mode", "accept-edits",
               "--output-format", "stream-json", "--print-timeout", "%dm" % timeout_min,
               "--add-dir", str(root)]
        waits = 0
        while True:
            t0 = time.time()
            captured: list[str] = []
            result: dict | None = None
            steps = 0
            with log.open("a", encoding="utf-8", errors="replace") as fh:
                fh.write("--- agy output (%s, model=%s) ---\n" % (now(), model))
                fh.flush()
                no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                proc = subprocess.Popen(
                    cmd, cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    creationflags=no_window,
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    fh.write(line)
                    captured.append(line)
                    if len(captured) > 400:
                        del captured[:200]
                    s = line.strip()
                    if s.startswith("{"):
                        try:
                            ev = json.loads(s)
                        except ValueError:
                            ev = None
                        if isinstance(ev, dict):
                            kind = ev.get("event") or ev.get("type") or ""
                            if kind == "result" and isinstance(ev.get("result"), dict):
                                result = ev["result"]
                            elif "usage" in ev and "status" in ev:
                                result = ev
                            if kind == "step_update":
                                steps += 1
                                if steps % 10 == 0:
                                    sys.stdout.write("  [agy] %d steps...\n" % steps)
                                    sys.stdout.flush()
                            elif kind:
                                sys.stdout.write("  [agy] %s\n" % kind)
                                sys.stdout.flush()
                            continue
                    sys.stdout.write(line)
                    sys.stdout.flush()
                proc.wait()
                fh.write("\n  exit code %d\n" % proc.returncode)
            secs = time.time() - t0
            record_usage(name, model, proc.returncode, result, secs)

            tail = "".join(captured[-60:])
            done_marker = (root / "docs" / "plan" / "queue" / "_done")
            produced = (done_marker / ("%s.ok" % name)).exists() or (done_marker / ("%s.stop" % name)).exists()
            if proc.returncode != 0 and not produced and looks_like_quota(tail) and waits < QUOTA_MAX_WAITS:
                waits += 1
                say("  agy 额度或频率受限 —— 等 %d 分钟后重试（第 %d/%d 次）"
                    % (QUOTA_WAIT_S // 60, waits, QUOTA_MAX_WAITS))
                with log.open("a", encoding="utf-8") as fh:
                    fh.write("  quota/rate limit detected, waiting %ds (%d/%d)\n"
                             % (QUOTA_WAIT_S, waits, QUOTA_MAX_WAITS))
                halt = done_marker / "_HALT"
                for _ in range(QUOTA_WAIT_S // 10):
                    if halt.exists():
                        return proc.returncode
                    time.sleep(10)
                continue
            if result:
                u = result.get("usage", {})
                say("  agy 用量：%s tokens，%s 轮，%.0f 秒，状态 %s"
                    % (u.get("total_tokens", "?"), result.get("num_turns", "?"), secs, result.get("status", "?")))
                if result.get("denied_actions"):
                    say("  ⚠ 被权限拦下的操作：%s" % result["denied_actions"])
            return proc.returncode
    return run_agy


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Run a CUTORA-style task queue with Antigravity CLI instead of Grok")
    ap.add_argument("--root", default=r"D:\0_Dev\CUTORA", help="repo containing tools/queue_runner.py")
    ap.add_argument("--model", default="claude-opus-5-5-high", help="see `agy models`")
    ap.add_argument("--timeout-min", type=int, default=180, help="per-task time limit")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not Path(AGY).exists():
        print("agy not found (%s). Install the Antigravity CLI first." % AGY)
        return 4
    qr = load_runner(root)
    qr.run_grok = make_run_agy(root, args.model, args.timeout_min, qr.say)

    # queue_runner refuses to start without a `grok` on PATH; point that check at agy.
    real_which = shutil.which
    qr.shutil = types.SimpleNamespace(which=lambda n, *a, **k: AGY if n == "grok" else real_which(n, *a, **k))

    qr.say("  Robota: coding agent = Antigravity CLI (%s), model = %s, timeout = %d min"
           % (AGY, args.model, args.timeout_min))
    qr.progress("ROBOTA agy runner (model=%s)" % args.model)
    return qr.main(["--dry-run"] if args.dry_run else [])


if __name__ == "__main__":
    raise SystemExit(main())
