"""Single source of truth for command safety (used by run_in_project, command tasks, local brain policy)."""
from __future__ import annotations

import re

DENY_PATTERNS = [
    r"git\s+push", r"git\s+reset\s+--hard", r"git\s+clean", r"git\s+rebase", r"git\s+filter-branch",
    r"git\s+checkout\s+--\s+\.", r"git\s+stash\s+(drop|clear)", r"git\s+tag\s+-d", r"git\s+branch\s+-D",
    r"rm\s+-rf", r"(^|[\s;|&(])rm\s", r"(^|[\s;|&(])ri\s", r"Remove-Item", r"\brmdir\b", r"\brd\b", r"\bdel\b", r"\berase\b", r"format\s+[a-zA-Z]:",
    r"RERECORD_GOLDEN", r"expected\.sha256",
    r"\bcurl\b", r"\bwget\b", r"Invoke-WebRequest", r"Invoke-RestMethod", r"\birm\b", r"\biwr\b", r"Start-BitsTransfer",
    r"pip\s+(install|uninstall)", r"\\Windows\\", r"System32", r"\\Program Files", r"HKLM:", r"HKCU:",
    r"reg\s+(add|delete)", r"shutdown", r"Stop-Computer", r"Restart-Computer", r"bcdedit", r"diskpart",
]
_DENY = [re.compile(p, re.IGNORECASE) for p in DENY_PATTERNS]


def check_command(cmd: str) -> tuple[bool, str]:
    """Return (allowed, reason)."""
    if not cmd or not cmd.strip():
        return False, "empty command"
    for rx in _DENY:
        if rx.search(cmd):
            return False, f"blocked by safety rule: {rx.pattern}"
    return True, ""
