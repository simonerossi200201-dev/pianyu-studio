# -*- coding: utf-8 -*-
"""Which commit made g16 expected_02 hash 237c6e00f384 (g13's file)?

Uses git worktrees so the main working tree stays put.

usage: runtime\\venv\\Scripts\\python.exe tools\\_diag\\bisect_g16.py
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOG = os.path.join(REPO, "docs", "plan", "queue", "_log", "_bisect_g16.log")
WT = os.path.join(REPO, "build", "_q47_g16_wt")
CASE = "g16_parallel_fx"
G13 = "237c6e00f384"
EXP02 = "8eb46a915c9e"

# Sparse enough to finish in one sitting; each render is ~1 min.
SHAS = [
    ("ac520ed", "software-decode rerecord of the twelve"),
    ("2c70ab3", "Q21 optical-looking shadows"),
    ("dd6a682", "Q26 DEPRECATED keys dropped on read"),
    ("3400c86", "Q30 spatial (after the revert)"),
    ("d3644f8", "Q41 optical shadows"),
    ("cf78c0f", "Q35 — already reported g16_02 red"),
    ("HEAD", "current tree"),
]


def say(msg=""):
    print(msg, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with io.open(LOG, "a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
    except OSError:
        pass


def git(*args, cwd=None, check=True):
    p = subprocess.run(
        ["git"] + list(args), cwd=cwd or REPO,
        capture_output=True, text=True, errors="replace",
    )
    if check and p.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), p.stderr.strip()[:400]))
    return p.stdout.strip()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def render_at(sha, label):
    dest = WT
    if os.path.isdir(dest):
        git("worktree", "remove", "--force", dest, check=False)
        shutil.rmtree(dest, ignore_errors=True)
    git("worktree", "add", "--detach", dest, sha)
    env = dict(os.environ)
    env["PYTHONPATH"] = dest + os.pathsep + env.get("PYTHONPATH", "")
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["CUTORA_USER_DATA"] = os.path.join(dest, "tests", "golden", "_fixture_user_data")
    py = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
    # Use HEAD's golden_check if the old tree's script is missing flags,
    # but import cutora from the worktree via PYTHONPATH.
    checker = os.path.join(REPO, "tools", "golden_check.py")
    cmd = [py, "-u", checker, "--case", CASE, "--allow-dirty"]
    say("  rendering %s (%s) ..." % (sha, label))
    p = subprocess.run(
        cmd, cwd=dest, env=env, capture_output=True, text=True, errors="replace",
        timeout=600,
    )
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    # Parse got hashes from the checker output.
    got02 = ""
    for line in out.splitlines():
        if "expected_02" in line.lower() or "expected_02.mp4" in line:
            say("    " + line.strip())
            for tok in line.replace("=", " ").split():
                tok = tok.strip(" ,")
                if len(tok) >= 12 and all(c in "0123456789abcdefABCDEF" for c in tok[:12]):
                    if tok.lower().startswith(G13) or tok.lower().startswith(EXP02) or len(tok) >= 24:
                        got02 = tok.lower()[:12]
        if "FAIL" in line or "PASS" in line:
            if "expected_02" in line or "g16" in line.lower():
                say("    " + line.strip()[:200])
    # Fallback: look for "got="
    if not got02:
        for line in out.splitlines():
            if "got=" in line and "expected_02" in line:
                say("    " + line.strip()[:240])
                after = line.split("got=", 1)[1]
                got02 = after.strip().split()[0][:12].lower()
                break
    if p.returncode not in (0, 1):
        say("    checker exit %s" % p.returncode)
        say("    tail: " + "\n    ".join(out.splitlines()[-12:]))
    git("worktree", "remove", "--force", dest, check=False)
    shutil.rmtree(dest, ignore_errors=True)
    return got02, p.returncode, out


def main() -> int:
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    head = git("rev-parse", "--short", "HEAD")
    if branch == "HEAD":
        say("detached HEAD, abort")
        return 2
    io.open(LOG, "w", encoding="utf-8").close()
    say("bisect g16 expected_02  start %s @ %s" % (branch, head))
    say("looking for switch from %s → %s (g13)" % (EXP02, G13))
    say("")
    results = []
    try:
        for sha, label in SHAS:
            real = git("rev-parse", "--short", sha)
            got, rc, _out = render_at(real, label)
            tag = "?"
            if got.startswith(G13):
                tag = "G13 (current red)"
            elif got.startswith(EXP02):
                tag = "EXP (rerecord hash)"
            elif got:
                tag = "OTHER " + got
            else:
                tag = "NOHASH exit=%s" % rc
            say("  %s %-12s %s" % (real, got or "-", tag))
            results.append((real, label, got, tag))
    finally:
        if os.path.isdir(WT):
            git("worktree", "remove", "--force", WT, check=False)
            shutil.rmtree(WT, ignore_errors=True)
        now = git("rev-parse", "--abbrev-ref", "HEAD")
        say("restored? now on %s (wanted %s)" % (now, branch))
    say("")
    say("summary:")
    for real, label, got, tag in results:
        say("  %s  got=%s  %s  (%s)" % (real, got or "-", tag, label))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
