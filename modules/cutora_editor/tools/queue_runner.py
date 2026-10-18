#!/usr/bin/env python
"""CUTORA unattended queue runner (v2).

Difference from RUN_GROK_QUEUE.bat v1: a task that hits a red line no longer
halts the whole night.  It is quarantined and the queue moves on.

Per task:
  1. remember HEAD
  2. run grok on the brief, tee output to console + log
  3. pop the brief off the queue
  4. if the task wrote Qnn.stop (or died without writing Qnn.ok):
       run the golden baseline.
         green -> the tree is sane, just skip this task and continue
         red   -> tag a rescue ref, revert exactly this task's commits,
                  run golden again.
                    green -> continue with the next task
                    red   -> write _HALT and stop; a red baseline the runner
                             cannot explain is not something to keep building on
       either way, write docs/plan/queue/_blocked/Qnn.report.md for Claude

Hard boundaries this runner will never cross:
  * it never re-records a golden baseline (Luca's call, nobody else's)
  * it never edits tests/*/expected.sha256
  * it never force-pushes, never rebases, never drops commits - a revert is a
    new commit and the original work stays reachable at refs/tags/rescue/...

Queue file edits are race-free: to add work while the runner is live, drop a
file into docs/plan/queue/_inbox/ (one spec path per line) - the runner drains
the inbox at the top of every iteration.  Never hand-edit line 1.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUEUE = ROOT / "tools" / "grok-queue.txt"
DONE_LIST = ROOT / "tools" / "grok-done.txt"
QDIR = ROOT / "docs" / "plan" / "queue"
QDONE = QDIR / "_done"
QLOG = QDIR / "_log"
QINBOX = QDIR / "_inbox"
QBLOCK = QDIR / "_blocked"
PROGRESS = QDIR / "_progress.txt"
LOCK = QDONE / "_RUNNING.lock"
HALT = QDONE / "_HALT"
BLOCKED_INDEX = QDIR / "_BLOCKED.md"

PY = ROOT / "runtime" / "venv" / "Scripts" / "python.exe"
if not PY.exists():
    PY = Path(sys.executable)


def now() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def say(msg: str = "") -> None:
    print(msg, flush=True)


def progress(msg: str) -> None:
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    with PROGRESS.open("a", encoding="utf-8") as fh:
        fh.write("[%s] %s\n" % (now(), msg))


def git(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=check,
    )


def head() -> str:
    return git("rev-parse", "HEAD").stdout.strip()


# ---------------------------------------------------------------- queue file

def drain_inbox() -> None:
    """Append anything dropped in _inbox/ to the END of the queue, then delete it."""
    if not QINBOX.is_dir():
        return
    for f in sorted(QINBOX.glob("*.txt")):
        try:
            lines = [ln.strip() for ln in f.read_text(encoding="utf-8").splitlines()]
        except OSError:
            continue
        lines = [ln for ln in lines if ln and not ln.startswith("#")]
        if lines:
            with QUEUE.open("a", encoding="utf-8") as fh:
                for ln in lines:
                    fh.write(ln + "\n")
            say("  + queued from inbox: %s" % ", ".join(lines))
            progress("INBOX + %s" % " ".join(lines))
        try:
            f.unlink()
        except OSError:
            pass


def peek() -> str | None:
    if not QUEUE.exists():
        return None
    for ln in QUEUE.read_text(encoding="utf-8", errors="replace").splitlines():
        if ln.strip():
            return ln.strip()
    return None


def pop(spec: str) -> None:
    """Remove the first non-empty line, but only if it is still `spec`."""
    lines = QUEUE.read_text(encoding="utf-8", errors="replace").splitlines()
    out, dropped = [], False
    for ln in lines:
        if not dropped and ln.strip() == spec:
            dropped = True
            continue
        out.append(ln)
    QUEUE.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
    with DONE_LIST.open("a", encoding="utf-8") as fh:
        fh.write(spec + "\n")


# ------------------------------------------------------------------ subtasks

def run_grok(spec: str, log: Path) -> int:
    # 不经过 shell：grok 是真 exe（~/.grok/bin/grok.exe），参数列表直接交给
    # CreateProcess，spec 路径也就不会被 shell 二次解释。
    # tools/security_scan.ps1 全仓禁止经 shell 启动子进程，test_t09_scan 守着这条 ——
    # Q42 早就报过这里红了，是 Claude 查错了文件、回了句「归因写错了」。
    grok = shutil.which("grok") or "grok"
    cmd = [grok, "--prompt-file", spec, "--permission-mode", "bypassPermissions",
           "--output-format", "plain", "--no-alt-screen"]
    with log.open("a", encoding="utf-8", errors="replace") as fh:
        fh.write("--- grok output (%s) ---\n" % now())
        fh.flush()
        proc = subprocess.Popen(
            cmd, cwd=str(ROOT),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            fh.write(line)
        proc.wait()
        fh.write("\n  exit code %d\n" % proc.returncode)
    return proc.returncode


KNOWN_RED = QDIR / "_golden_known_red.txt"


def known_red() -> set:
    """本来就红的用例，不该算在这一轮头上。"""
    try:
        return {ln.strip() for ln in KNOWN_RED.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.startswith("#")}
    except OSError:
        return set()


def parse_failing(out: str) -> set:
    """从 golden 的结果表里读出失败的用例名。

    表格是定宽的：CASE 占 0-27，STATUS 占 28-35。续行的 CASE 列是空的，
    属于上一个用例。
    """
    failing, current = set(), ""
    for line in out.splitlines():
        if len(line) < 36:
            continue
        case, status = line[:28].strip(), line[28:36].strip()
        if case and not case.startswith("-") and case != "CASE":
            current = case.split()[0]          # 去掉 "g08 r1" 里的重复计数
        if status in ("FAIL", "ERROR") and current:
            failing.add(current)
    return failing


def run_golden(log: Path, tag: str) -> tuple:
    """跑一次 golden。返回 (ran, new_failures, raw_output)。

    ran=False 表示**根本没跑成**（例如工作区脏被拒、fixture 缺失、崩了）。
    这和「跑了但哈希变了」是两回事，2026-09-20 就是把这两者混为一谈，
    导致守卫把一条好好的任务回滚掉还写了 _HALT。
    """
    say("  running golden baseline (%s) ..." % tag)
    proc = subprocess.run(
        [str(PY), str(ROOT / "tools" / "golden_check.py"),
         "--all", "--repeat", "1", "--allow-dirty"],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    with log.open("a", encoding="utf-8", errors="replace") as fh:
        fh.write("\n--- golden %s (exit %d) ---\n" % (tag, proc.returncode))
        fh.write(out)
    ran = "RESULT:" in out
    if not ran:
        say("  golden %s -> 没跑成（exit %d）。这不是「基线红了」，是「没测成」。"
            % (tag, proc.returncode))
        return False, set(), out
    failing = parse_failing(out)
    kr = known_red()
    # 名单里允许写简称（g16），也允许写全名（g16_parallel_fx）
    new = {c for c in failing
           if c not in kr and not any(c.startswith(k + "_") for k in kr)}
    if new:
        say("  golden %s -> 新增失败：%s" % (tag, ", ".join(sorted(new))))
    else:
        say("  golden %s -> 无新增失败%s"
            % (tag, ("（%s 是本来就红的）" % ", ".join(sorted(failing))) if failing else ""))
    return True, new, out


# ------------------------------------------------------------------- guard

def _verdict(ran: bool, new: set) -> str:
    if not ran:
        return "**没跑成**（不是基线红了，是没测成）"
    return "无新增失败" if not new else "**新增失败：%s**" % ", ".join(sorted(new))


def quarantine(name: str, spec: str, head_before: str, exit_code: int,
               log: Path) -> bool:
    """一条任务撞了红线。返回 True 表示队列可以继续。"""
    stop_file = QDONE / ("%s.stop" % name)
    stop_text = ""
    if stop_file.exists():
        stop_text = stop_file.read_text(encoding="utf-8", errors="replace")

    head_after = head()
    commits = git("log", "--oneline", "%s..%s" % (head_before, head_after)).stdout.strip()

    ran1, new1, g1_out = run_golden(log, "after %s" % name)
    reverted = ""
    stashed = ""
    ran2, new2, g2_out = None, set(), ""

    if not ran1:
        # golden 自己没跑起来 —— 绝不能据此回滚别人的活。
        reverted = "golden 没跑成，运行器**没有动任何提交**"
        report(name, spec, head_before, head_after, commits, exit_code,
               stop_text, ran1, new1, g1_out, reverted, ran2, new2, g2_out, log)
        say("  golden 没跑成，无法判断基线 —— 不回滚，停下来让人看。")
        progress("HALT  %s (golden 没跑成，无法判定)" % name)
        HALT.write_text(
            "auto-HALT by queue_runner: %s 之后 golden 没能跑起来（不是基线红了）。"
            "看 docs/plan/queue/_blocked/%s.report.md (%s)\n" % (name, name, now()),
            encoding="utf-8")
        return False

    if new1:
        if head_after != head_before and commits:
            rescue = "rescue/%s-%s" % (name, stamp())
            git("tag", rescue, head_after)
            say("  基线新增失败 -> 回滚 %s 的提交（原件留在 %s）" % (name, rescue))
            # 只收任务自己的活。2026-09-20 的教训：整树 stash 会把
            # tools/grok-queue.txt 一起卷走，队列被还原成上次提交的样子，
            # 于是同一批任务无限重跑（Q51→Q46→Q52→Q55→倒带，转了三圈）。
            work_dirty = [ln for ln in git("status", "--porcelain").stdout.splitlines()
                          if ln[3:].startswith(("cutora/", "tests/"))]
            if work_dirty:
                label = "queue-guard %s %s" % (name, stamp())
                st = git("stash", "push", "-m", label, "--", "cutora", "tests")
                if st.returncode == 0 and "No local changes" not in st.stdout:
                    stashed = label
                    say("  未提交的改动存进 stash：%s" % label)
            rv = git("revert", "--no-commit", "%s..%s" % (head_before, head_after))
            if rv.returncode != 0:
                git("revert", "--abort")
                reverted = "revert 冲突，什么都没改"
                say("  revert 失败，保持原样")
            else:
                git("commit", "-m",
                    "queue guard: revert %s (golden 新增失败 %s); 原件在 %s"
                    % (name, ",".join(sorted(new1)), rescue))
                reverted = "已回滚 %s..%s，原件留在 tag %s" % (
                    head_before[:8], head_after[:8], rescue)
                ran2, new2, g2_out = run_golden(log, "after reverting %s" % name)
        else:
            reverted = "这一轮没有提交 —— 新增失败不是它造成的"

    if stashed:
        reverted = (reverted + "；" if reverted else "") + \
            "未提交的改动在 stash「%s」（git stash list 可取回）" % stashed

    report(name, spec, head_before, head_after, commits, exit_code,
           stop_text, ran1, new1, g1_out, reverted, ran2, new2, g2_out, log)

    if not new1:
        say("  基线没有新增失败 —— 跳过 %s，继续下一条。" % name)
        progress("SKIP  %s (stopped, 基线无新增失败)" % name)
        return True
    if ran2 and not new2:
        say("  回滚之后基线恢复 —— 继续下一条。")
        progress("SKIP  %s (stopped, 已回滚, 基线恢复)" % name)
        return True

    say("  基线仍有新增失败 —— 有意停下队列。")
    progress("HALT  %s (基线新增失败且回滚没能清掉)" % name)
    HALT.write_text(
        "auto-HALT by queue_runner: %s 让 golden 新增失败且回滚未能清除 (%s)\n"
        % (name, now()), encoding="utf-8")
    return False


def report(name, spec, head_before, head_after, commits, exit_code, stop_text,
           ran1, new1, g1_out, reverted, ran2, new2, g2_out, log: Path) -> None:
    QBLOCK.mkdir(parents=True, exist_ok=True)
    tail = ""
    try:
        tail = "\n".join(log.read_text(encoding="utf-8", errors="replace")
                         .splitlines()[-160:])
    except OSError:
        pass
    b = []
    b.append("# %s 被隔离 / quarantined\n" % name)
    b.append("- 时间：%s" % now())
    b.append("- 任务书：`%s`" % spec)
    b.append("- grok 退出码：%d" % exit_code)
    b.append("- HEAD before：`%s`" % head_before)
    b.append("- HEAD after ：`%s`" % head_after)
    b.append("- 本来就红的用例（不算数）：%s" % (", ".join(sorted(known_red())) or "(无)"))
    b.append("- golden（出事后）：%s" % _verdict(ran1, new1))
    if ran2 is not None:
        b.append("- golden（回滚后）：%s" % _verdict(ran2, new2))
    if reverted:
        b.append("- 运行器做了什么：%s" % reverted)
    b.append("\n## 这一轮的提交\n")
    b.append("```\n%s\n```" % (commits or "(没有提交)"))
    b.append("\n## Grok 自己写的 .stop\n")
    b.append("```\n%s\n```" % (stop_text.strip() or "(没有 .stop —— 它是崩了或没写标记)"))
    b.append("\n## golden 输出（出事后）\n")
    b.append("```\n%s\n```" % "\n".join(g1_out.splitlines()[-60:]))
    if g2_out:
        b.append("\n## golden 输出（回滚后）\n")
        b.append("```\n%s\n```" % "\n".join(g2_out.splitlines()[-60:]))
    b.append("\n## 日志尾巴\n")
    b.append("```\n%s\n```" % tail)
    b.append("\n---\n")
    b.append("### 给 Claude 的裁决位\n")
    b.append("裁决写进 `docs/plan/queue/_blocked/%s.ruling.md`，" % name)
    b.append("要复跑就运行 `runtime\\venv\\Scripts\\python.exe tools\\queue_requeue.py %s`" % name)
    b.append("（只往队尾追加，永远不碰正在跑的第一行）。\n")
    b.append("**红线：任何人都不许在这里批准重录 golden 基线。**")
    b.append("基线变了只有一个处理方式 —— 停下来问 Luca。")
    (QBLOCK / ("%s.report.md" % name)).write_text("\n".join(b) + "\n", encoding="utf-8")

    with BLOCKED_INDEX.open("a", encoding="utf-8") as fh:
        fh.write("- %s  **%s**  golden %s%s  → `_blocked/%s.report.md`\n" % (
            now(), name, _verdict(ran1, new1),
            ("，回滚后 %s" % _verdict(ran2, new2)) if ran2 is not None else "",
            name))
    say("  报告已写：docs\\plan\\queue\\_blocked\\%s.report.md" % name)


def startup_sweep() -> bool:
    """上一轮有没有留下 .stop？（v1 遇到 .stop 就停机，v2 会接手）"""
    stops = sorted(QDONE.glob("*.stop"))
    if not stops:
        return True
    names = ", ".join(p.stem for p in stops)
    say("  上一轮留下了 stop 标记：%s" % names)
    say("  先验一遍基线再决定动不动。")
    log = QLOG / "_startup_sweep.log"
    ran, new, out = run_golden(log, "startup sweep")
    QBLOCK.mkdir(parents=True, exist_ok=True)
    for sp in stops:
        text = sp.read_text(encoding="utf-8", errors="replace")
        rep = QBLOCK / ("%s.report.md" % sp.stem)
        if not rep.exists():
            rep.write_text(
                "# %s 被隔离 / quarantined（上一轮遗留）\n\n"
                "- 时间：%s\n- golden（开工前复查）：%s\n"
                "- 说明：这个 .stop 是上一轮运行器留下的，本运行器没跑过它，"
                "不知道它改了哪些提交，**不会自动回滚**。\n\n"
                "## Grok 自己写的 .stop\n\n```\n%s\n```\n\n"
                "## golden 输出\n\n```\n%s\n```\n\n---\n\n"
                "裁决写进 `_blocked/%s.ruling.md`，复跑用 "
                "`tools\\queue_requeue.py %s`。\n\n"
                "**红线：任何人都不许在这里批准重录 golden 基线。**\n"
                % (sp.stem, now(), _verdict(ran, new), text.strip(),
                   "\n".join(out.splitlines()[-40:]), sp.stem, sp.stem),
                encoding="utf-8")
        sp.rename(QBLOCK / ("%s.stop.%s" % (sp.stem, stamp())))
        with BLOCKED_INDEX.open("a", encoding="utf-8") as fh:
            fh.write("- %s  **%s**  （上一轮遗留）golden %s  → `_blocked/%s.report.md`\n"
                     % (now(), sp.stem, _verdict(ran, new), sp.stem))
    if not ran:
        say("")
        say("  golden 没能跑起来，开工前的状态判断不了。先把它弄清楚再开队列。")
        progress("RUNNER v2 拒绝开工 —— golden 没跑成")
        return False
    if new:
        say("")
        say("  开工前基线就有新增失败：%s" % ", ".join(sorted(new)))
        say("  在红基线上跑新任务，每个任务的验收数字都没有意义。拒绝开工。")
        progress("RUNNER v2 拒绝开工 —— 基线新增失败 %s" % ",".join(sorted(new)))
        return False
    say("  基线无新增失败 —— 遗留的 stop 已隔离，继续。")
    progress("SWEEP quarantined %s (基线无新增失败)" % names)
    return True


def autocommit_if_needed(name: str, head_before: str) -> None:
    """任务干了活却没提交 -> 替它提交。

    Q42 就是这样：活做对了、.ok 也写了，但一行没提交。没有提交，隔离机制
    就无法归属这一轮改了什么，也就无法回滚；下一条任务还会盖在上面。
    这里只收 cutora/ 和 tests/ —— build/ 之类的产物不入库。
    """
    if head() != head_before:
        return                      # 它自己提交了，不插手
    # 只收源码和测试。mismatch.png 这类比对落图是诊断残渣，不属于任何一条任务 ——
    # 2026-09-21 它们被扫进一个挂 Q36 名字的提交，而 Q36 一行源码都没写。
    dirty = [ln[3:].strip().strip('"') for ln in
             git("status", "--porcelain").stdout.splitlines()
             if ln[3:].startswith(("cutora/", "tests/"))
             and not ln.rstrip().endswith("mismatch.png")]
    if not dirty:
        return
    say("  %s 没有提交自己的改动（%d 个文件）—— 运行器替它提交，"
        "否则这一轮无法归属也无法回滚。" % (name, len(dirty)))
    git("add", "--", *dirty)
    r = git("commit", "-m",
            "%s (auto-commit by queue runner: the task left its work uncommitted)"
            % name)
    if r.returncode == 0:
        say("  已提交为 %s" % head()[:8])
        progress("AUTOCOMMIT %s (%d files)" % (name, len(dirty)))
    else:
        say("  自动提交失败：%s" % (r.stderr or r.stdout).strip()[:200])


# --------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="CUTORA unattended queue runner v2")
    ap.add_argument("--dry-run", action="store_true",
                    help="walk the queue without calling grok")
    ap.add_argument("--no-lock", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    for d in (QDONE, QLOG, QINBOX, QBLOCK):
        d.mkdir(parents=True, exist_ok=True)

    if LOCK.exists() and not args.no_lock:
        say("")
        say("****************************************************************")
        say(" A queue run is already active (or crashed).")
        say(" Lock file: %s" % LOCK)
        say("")
        say(" Do NOT start a second run and do NOT paste tasks into an")
        say(" interactive Grok while this is running - concurrent edits")
        say(" corrupt the tree and make every test number meaningless.")
        say("")
        say(" If you are sure nothing is running, delete that file and retry.")
        say("****************************************************************")
        return 2
    LOCK.write_text("%s queue_runner.py pid %d\n" % (now(), os.getpid()), encoding="utf-8")

    say("")
    say("  CUTORA task queue (v2 - a red line quarantines one task, not the night)")
    say("  progress : docs\\plan\\queue\\_progress.txt")
    say("  logs     : docs\\plan\\queue\\_log\\")
    say("  blocked  : docs\\plan\\queue\\_blocked\\")
    say("  to stop  : create an empty file docs\\plan\\queue\\_done\\_HALT")
    say("")
    progress("RUNNER v2 start")

    if not args.dry_run and not shutil.which("grok"):
        say("")
        say("  找不到 grok 命令（PATH 里没有 grok.exe）。")
        say("  它通常装在 %USERPROFILE%\\.grok\\bin\\ —— 那个目录被改过名或删掉了吗？")
        say("  不开工：否则每条任务都会秒退、被当成失败隔离一遍。")
        progress("RUNNER v2 拒绝开工 —— 找不到 grok")
        try:
            LOCK.unlink()
        except OSError:
            pass
        return 4

    if not args.dry_run and not startup_sweep():
        try:
            LOCK.unlink()
        except OSError:
            pass
        return 3

    skipped: list[str] = []
    runs: dict = {}
    MAX_RUNS = 2
    try:
        while True:
            if HALT.exists():
                say("\n  _HALT found. Queue stopped.")
                progress("HALTED by _HALT")
                break
            drain_inbox()
            spec = peek()
            if not spec:
                say("\n  Queue empty. All done.")
                progress("QUEUE EMPTY - all done")
                break

            name = Path(spec).stem
            runs[name] = runs.get(name, 0) + 1
            if runs[name] > MAX_RUNS:
                say("")
                say("  %s 这一轮已经跑过 %d 次了 —— 队列在打转，有意停下。"
                    % (name, runs[name] - 1))
                say("  队列文件被什么东西还原了，或者有人在往里重复追加。")
                progress("HALT  %s 重复出现 %d 次，疑似队列打转" % (name, runs[name]))
                HALT.write_text(
                    "auto-HALT by queue_runner: %s 在同一轮里第 %d 次出现，"
                    "队列疑似打转 (%s)\n" % (name, runs[name], now()),
                    encoding="utf-8")
                break
            log = QLOG / ("%s.log" % name)
            say("")
            say("================================================================")
            say("  [%s] Grok is working on: %s" % (now(), spec))
            say("  log: docs\\plan\\queue\\_log\\%s.log" % name)
            say("================================================================")
            progress("START %s" % name)

            head_before = head()
            with log.open("a", encoding="utf-8") as fh:
                fh.write("\n=== %s start %s ===\n" % (name, now()))
                fh.write("HEAD %s\n" % head_before)
                fh.write("--- git status before ---\n")
                fh.write(git("status", "--short").stdout)

            if args.dry_run:
                say("  (dry run - not calling grok)")
                rc = 0
            else:
                rc = run_grok(spec, log)

            with log.open("a", encoding="utf-8") as fh:
                fh.write("--- git status after ---\n")
                fh.write(git("status", "--short").stdout)

            pop(spec)
            autocommit_if_needed(name, head_before)
            ok = (QDONE / ("%s.ok" % name)).exists()
            stopped = (QDONE / ("%s.stop" % name)).exists()
            say("  done: %s  (exit %d, ok=%s stop=%s)" % (spec, rc, ok, stopped))
            progress("END   %s (exit %d, ok=%s stop=%s)" % (name, rc, ok, stopped))

            if stopped or (not ok) or rc != 0:
                if args.dry_run:
                    say("  (dry run - skipping the golden guard)")
                    continue
                skipped.append(name)
                if not quarantine(name, spec, head_before, rc, log):
                    break
                time.sleep(1)
    finally:
        try:
            LOCK.unlink()
        except OSError:
            pass

    say("")
    if skipped:
        say("  quarantined this run: %s" % ", ".join(skipped))
        say("  reports are in docs\\plan\\queue\\_blocked\\ - send them to Claude.")
    else:
        say("  nothing was quarantined.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
