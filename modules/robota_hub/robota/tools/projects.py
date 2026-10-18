"""Read-only access to config/projects/*.toml."""
from __future__ import annotations

from dataclasses import asdict

from robota import config


def list_projects() -> list[dict]:
    """Short list of registered projects."""
    return [{"id": p.id, "name": p.name, "root": p.root, "description": p.description,
             "default_brain": p.default_brain, "private": p.private}
            for p in config.projects().values()]


def project_info(id: str) -> dict:  # noqa: A002 - tool argument name
    """Full config of one project (plus whether its root exists and whether it has a rules file)."""
    p = config.project(id)
    if not p:
        return {"ok": False, "error": f"unknown project '{id}'", "known": list(config.projects())}
    d = asdict(p)
    d["ok"] = True
    d["root_exists"] = p.path.is_dir()
    d["has_rules"] = bool(p.rules_file) and (p.path / p.rules_file).is_file()
    return d
