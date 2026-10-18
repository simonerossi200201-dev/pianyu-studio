# -*- coding: utf-8 -*-
"""Silent-failure #16: every stderr=subprocess.PIPE must have a reader."""
from __future__ import annotations

import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PIPE_RE = re.compile(r"stderr\s*=\s*subprocess\.PIPE")
# Same-file readers the catalogue accepts.
READER_RE = re.compile(
    r"communicate\s*\("
    r"|capture_output\s*=\s*True"
    r"|\.stderr\.read"
    r"|_start_readers\s*\("
    r"|_read_stderr"
    r"|drain"
    r"|subprocess\.run\s*\("
    r"|_no_window_run\s*\("
    r"|run_filter_script\s*\("
)

# (repo-relative posix path, 1-based line) -> reason. Empty unless a site
# cannot have a reader and is an intentional exception.
WHITELIST = {
    # tools/_diag and third-party trees are not scanned.
}


SKIP_DIR_NAMES = {
    ".git", "__pycache__", "runtime", "build", "vendor", "venv", ".venv",
    "node_modules", "mcp-sdk-venv", "_diag", "nondeterminism",
}


def _iter_py_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIR_NAMES and not d.startswith(".")
        ]
        for name in filenames:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def test_every_pipe_has_a_reader():
    missing = []
    scanned = 0
    for path in _iter_py_files(REPO):
        rel = os.path.relpath(path, REPO).replace("\\", "/")
        text = open(path, encoding="utf-8", errors="replace").read()
        if "stderr=subprocess.PIPE" not in text and "stderr = subprocess.PIPE" not in text:
            if not PIPE_RE.search(text):
                continue
        lines = text.splitlines()
        file_has_reader = bool(READER_RE.search(text))
        for i, line in enumerate(lines, 1):
            if not PIPE_RE.search(line):
                continue
            scanned += 1
            key = (rel, i)
            if key in WHITELIST:
                continue
            # subprocess.run / capture_output read the pipe themselves.
            window = "\n".join(lines[max(0, i - 8): min(len(lines), i + 8)])
            is_run = (
                "subprocess.run" in window
                or "capture_output=True" in window
                or "capture_output = True" in window
                or (
                    "from cutora.core.proc import run" in text
                    and "run(" in window
                )
            )
            if is_run:
                continue
            if file_has_reader:
                continue
            missing.append("%s:%d: %s" % (rel, i, line.strip()))
    assert scanned > 0, "scanner found no stderr=subprocess.PIPE; pattern broken"
    assert missing == [], (
        "stderr=subprocess.PIPE with no reader in the same file "
        "(drain thread / communicate() / run() / file redirect):\n"
        + "\n".join(missing)
    )
