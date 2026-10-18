"""Queue tools as thin HTTP calls to the Robota console (http://127.0.0.1:9600).

Deliberately decoupled from robota.tasks: the console owns the queue.
"""
from __future__ import annotations

from robota import config
from robota.tools import _http

KINDS = ("agent", "command", "plugin")
BRAINS = ("auto", "local", "agy")


def _base() -> str:
    return f"http://127.0.0.1:{config.settings()['console']['port']}"


def _call(method: str, path: str, **kw) -> dict:
    try:
        res = _http.request(method, _base() + path, service="Robota console", timeout=15, **kw)
        return res if isinstance(res, dict) else {"ok": True, "result": res}
    except _http.ServiceDown as exc:
        return {"ok": False, "running": False,
                "error": f"{exc}. Start the console (Robota.bat) to use the queue."}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def queue_add(title: str, prompt: str = "", command: str = "", kind: str = "agent",
              project: str | None = None, brain: str = "auto", model: str | None = None) -> dict:
    """Add a task to the Robota queue. kind=agent needs prompt; kind=command needs command."""
    kind = (kind or "agent").strip().lower()
    if kind not in KINDS:
        return {"ok": False, "error": f"kind must be one of {KINDS}"}
    if brain not in BRAINS:
        return {"ok": False, "error": f"brain must be one of {BRAINS}"}
    if kind == "agent" and not (prompt or "").strip():
        return {"ok": False, "error": "kind=agent needs a prompt"}
    if kind == "command" and not (command or "").strip():
        return {"ok": False, "error": "kind=command needs a command"}
    if project and not config.project(project):
        return {"ok": False, "error": f"unknown project '{project}'", "known": list(config.projects())}
    body = {"title": title or (prompt or command)[:60], "kind": kind, "project": project or None,
            "brain": brain, "prompt": prompt or "", "command": command or ""}
    if model:
        body["model"] = model
    return _call("POST", "/api/queue", json=body)


def queue_list() -> dict:
    """List tasks in the Robota queue."""
    return _call("GET", "/api/queue")


def queue_halt() -> dict:
    """Halt the Robota queue (the running task finishes/stops; nothing new starts)."""
    return _call("POST", "/api/queue/halt")
