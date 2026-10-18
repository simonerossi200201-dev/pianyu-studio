"""Configuration loading: config/robota.toml, config/projects/*.toml, config/providers.toml."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
DATA = ROOT / "data"
SECRETS = ROOT / "secrets"
LOGS = ROOT / "logs"

AGY = os.environ.get("ROBOTA_AGY") or str(Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe")
PYTHON = str(ROOT / "runtime" / "venv" / "Scripts" / "python.exe")


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


@dataclass
class Project:
    id: str
    name: str
    root: str
    description: str = ""
    rules_file: str = ""
    test_cmd: str = ""
    default_brain: str = "auto"
    private: bool = False
    plugin: str = ""
    mcp: list[str] = field(default_factory=list)
    notes: str = ""

    @property
    def path(self) -> Path:
        return Path(self.root)

    def rules_text(self) -> str:
        if not self.rules_file:
            return ""
        p = self.path / self.rules_file
        try:
            return p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""


def settings() -> dict[str, Any]:
    s = _load(CONFIG / "robota.toml")
    s.setdefault("console", {}).setdefault("port", 9600)
    s.setdefault("voice", {}).setdefault("port", 9611)
    s.setdefault("local", {}).setdefault("model", "muse-glimmer")
    s["local"].setdefault("fast_model", "gemma4:12b")
    s["local"].setdefault("ollama", "http://localhost:11434")
    s["local"].setdefault("num_ctx", 32768)
    s.setdefault("agy", {}).setdefault("model", "claude-sonnet-5-5-high")
    s["agy"].setdefault("code_model", "claude-opus-5-5-high")
    s["agy"].setdefault("timeout_min", 120)
    return s


def projects() -> dict[str, Project]:
    out: dict[str, Project] = {}
    d = CONFIG / "projects"
    for f in sorted(d.glob("*.toml")) if d.is_dir() else []:
        raw = _load(f)
        if not raw.get("id"):
            raw["id"] = f.stem
        known = {k: raw[k] for k in Project.__dataclass_fields__ if k in raw}
        out[raw["id"]] = Project(**known)
    return out


def project(pid: str | None) -> Project | None:
    if not pid:
        return None
    return projects().get(pid)


def providers() -> dict[str, dict[str, Any]]:
    """L2 providers. A provider is 'enabled' only if its key file exists in secrets/."""
    raw = _load(CONFIG / "providers.toml").get("provider", {})
    out = {}
    for name, cfg in raw.items():
        key_file = SECRETS / cfg.get("key_file", f"{name}.key")
        key = ""
        if key_file.exists():
            key = key_file.read_text(encoding="utf-8").strip()
        cfg = dict(cfg)
        cfg["enabled"] = bool(key) and cfg.get("enabled", True)
        cfg["_key"] = key
        out[name] = cfg
    return out


def ensure_dirs() -> None:
    for d in (DATA / "queue" / "logs", DATA / "chats", DATA / "memory", DATA / "usage", LOGS, SECRETS):
        d.mkdir(parents=True, exist_ok=True)
