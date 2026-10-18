# -*- coding: utf-8 -*-
"""Which commit changed g08's exported audio? Render it at each one.

The cmd version of this broke: `for /f` around a piped command with escaped
redirects parsed wrong and captured nothing, and the final `git checkout main`
failed because an untracked copy of the probe blocked it, leaving the repo on a
detached HEAD. Python does the quoting and always restores the branch.

usage: runtime\\venv\\Scripts\\python.exe tools\\_diag\\bisect_audio.py
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROBE_REL = os.path.join("tools", "_diag", "audio_md5_one.py")
CASE = "g08_fx_static"
SHAS = [
    ("ac520ed", "last green golden"),
    ("e330ab4", "Q18 dead-control census"),
    ("040d39a", "Q29 transcript source"),
    ("aa3613a", "Q30 spatial switch"),
    ("2c892ef", "HEAD (incl. my two fixes)"),
]


LOG = os.path.join(REPO, "docs", "plan", "queue", "_log", "_bisect_audio.log")


def say(msg=""):
    """同时打到控制台和日志。控制台必须实时可见 —— 上一版全部重定向进文件，
    跑的几分钟里窗口一片漆黑，用户不知道它有没有在动。"""
    print(msg, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with io.open(LOG, "a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
    except OSError:
        pass


def git(*args, check=True):
    p = subprocess.run(["git"] + list(args), cwd=REPO,
                       capture_output=True, text=True, errors="replace")
    if check and p.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), p.stderr.strip()[:200]))
    return p.stdout.strip()


def main() -> int:
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        say("已经在游离 HEAD 上，先手动回到分支再跑")
        return 2
    try:
        io.open(LOG, "w", encoding="utf-8").close()
    except OSError:
        pass
    say("起点分支: %s @ %s" % (branch, git("rev-parse", "--short", "HEAD")))
    say("五个提交各渲一次 g08，每个约 30-60 秒，请等 3-5 分钟。")
    say("")

    keep = os.path.join(tempfile.gettempdir(), "cutora_audio_md5_one.py")
    shutil.copy2(os.path.join(REPO, PROBE_REL), keep)

    py = sys.executable
    results = []
    try:
        for sha, label in SHAS:
            dest = os.path.join(REPO, PROBE_REL)
            if os.path.exists(dest):
                os.remove(dest)          # 否则 checkout 会被未跟踪文件挡住
            say("  正在渲染 %s (%s) ..." % (sha, label))
            git("checkout", "-f", sha)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(keep, dest)
            p = subprocess.run([py, PROBE_REL, CASE], cwd=REPO,
                               capture_output=True, text=True, errors="replace")
            md5 = ""
            for line in (p.stdout or "").splitlines():
                if line.startswith("AUDIOMD5"):
                    md5 = line.split()[1]
            if not md5:
                tail = ((p.stderr or "") + (p.stdout or "")).strip().splitlines()
                md5 = "FAILED rc=%s %s" % (p.returncode, tail[-1][:90] if tail else "")
            results.append((sha, label, md5))
            say("  %-9s %-28s %s" % (sha, label, md5))
            if os.path.exists(dest):
                os.remove(dest)
    finally:
        dest = os.path.join(REPO, PROBE_REL)
        if os.path.exists(dest):
            os.remove(dest)
        git("checkout", "-f", branch, check=False)
        say("\n已回到 %s @ %s" % (branch, git("rev-parse", "--short", "HEAD")))

    say("")
    base = results[0][2] if results else ""
    say("=== 结论 ===")
    culprit = None
    for sha, label, md5 in results:
        same = (md5 == base)
        say("  %-9s %-28s %s  %s" % (sha, label, md5, "同基线" if same else "**变了**"))
        if not same and culprit is None and not md5.startswith("FAILED"):
            culprit = (sha, label)
    if culprit:
        say("\n第一个改变音频的提交：%s（%s）" % culprit)
        say("下一步：revert 这个提交让 golden 回绿，队列先跑起来；它的工作重排成新任务。")
    elif all(r[2] == base for r in results):
        say("\n五个点音频全都一样 —— 那差异不在 g08，换个 case 再量。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
