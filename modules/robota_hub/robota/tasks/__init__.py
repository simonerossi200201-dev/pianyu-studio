"""Generic task queue: JSON files in data/queue/, executed serially by a background worker."""
from __future__ import annotations

import asyncio
import json
import secrets
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from robota import config, safety

QDIR = config.DATA / "queue"
LOGDIR = QDIR / "logs"
HALT = QDIR / "_HALT"
KINDS = ("agent", "command", "plugin")
STATES = ("queued", "running", "done", "failed", "stopped", "quarantined")
_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _path(tid: str) -> Path:
    return QDIR / f"{tid}.json"


def save(t: dict[str, Any]) -> None:
    QDIR.mkdir(parents=True, exist_ok=True)
    tmp = _path(t["id"]).with_suffix(".tmp")
    tmp.write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_path(t["id"]))


def get(tid: str) -> dict[str, Any] | None:
    try:
        return json.loads(_path(tid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def all_tasks() -> list[dict[str, Any]]:
    out = []
    for f in sorted(QDIR.glob("*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def add(title: str, *, kind: str = "agent", prompt: str = "", command: str = "", project: str | None = None,
        brain: str = "auto", model: str | None = None, gpu: bool = False, plugin_args: list[str] | None = None) -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if project and not config.project(project):
        raise ValueError(f"unknown project: {project}")
    if kind == "agent" and not prompt.strip():
        raise ValueError("agent task needs a prompt")
    if kind == "command":
        ok, why = safety.check_command(command)
        if not ok:
            raise ValueError(why)
    if kind == "plugin":
        proj = config.project(project)
        if not proj or not proj.plugin:
            raise ValueError("plugin task needs a project with a plugin")
    tid = datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
    t = {"id": tid, "title": title or (prompt or command)[:60], "kind": kind, "project": project,
         "brain": brain, "model": model, "prompt": prompt, "command": command, "gpu": gpu,
         "plugin_args": plugin_args or [], "state": "queued", "created": _now(), "started": None,
         "finished": None, "result": "", "log": str(LOGDIR / f"{tid}.log"), "usage": {}, "route": None}
    with _LOCK:
        save(t)
    return t


def cancel(tid: str) -> dict | None:
    with _LOCK:
        t = get(tid)
        if t and t["state"] == "queued":
            t["state"] = "stopped"
            t["finished"] = _now()
            t["result"] = "已取消"
            save(t)
        return t


def retry(tid: str) -> dict | None:
    with _LOCK:
        t = get(tid)
        if t and t["state"] in ("failed", "stopped", "quarantined"):
            t.update(state="queued", started=None, finished=None, result="")
            save(t)
        return t


def halt(on: bool = True) -> bool:
    if on:
        HALT.write_text(_now(), encoding="utf-8")
    elif HALT.exists():
        HALT.unlink()
    return HALT.exists()


def next_queued() -> dict | None:
    for t in all_tasks():
        if t["state"] == "queued":
            return t
    return None


def summary() -> dict[str, Any]:
    ts = all_tasks()
    counts = {s: 0 for s in STATES}
    for t in ts:
        counts[t["state"]] = counts.get(t["state"], 0) + 1
    running = next((t for t in ts if t["state"] == "running"), None)
    return {"counts": counts, "halted": HALT.exists(), "running": running,
            "recent": list(reversed(ts))[:30]}


# ------------------------------------------------------------------ worker

class Worker:
    def __init__(self, broadcast=None) -> None:
        self.broadcast = broadcast  # async fn(dict) for live UI updates
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.current: dict | None = None
        self._proc: subprocess.Popen | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        # crash recovery: tasks left "running" by a previous process
        for t in all_tasks():
            if t["state"] == "running":
                t.update(state="failed", finished=_now(), result="Robota 重启时发现它停在运行中（上次异常退出）")
                save(t)
        self._thread = threading.Thread(target=self._loop, name="robota-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def kill_current(self) -> bool:
        if self._proc and self._proc.poll() is None:
            self._proc.kill()
            return True
        return False

    def _event(self, ev: dict) -> None:
        if self.broadcast:
            try:
                self.broadcast(ev)
            except Exception:  # noqa: BLE001
                pass

    def _loop(self) -> None:
        loop = asyncio.new_event_loop()
        while not self._stop.is_set():
            if HALT.exists():
                time.sleep(2)
                continue
            with _LOCK:
                t = next_queued()
                if t:
                    t.update(state="running", started=_now())
                    save(t)
            if not t:
                time.sleep(2)
                continue
            self.current = t
            self._event({"type": "task", "task": t})
            try:
                loop.run_until_complete(self._run(t))
            except Exception as e:  # noqa: BLE001
                t.update(state="failed", result=f"{type(e).__name__}: {e}")
            t["finished"] = _now()
            save(t)
            self.current = None
            self._event({"type": "task", "task": t})
            self._notify(t)

    def _notify(self, t: dict) -> None:
        try:
            from robota.tools import notify
            icon = {"done": "✅", "failed": "❌", "quarantined": "⚠"}.get(t["state"], "ℹ")
            notify.notify(f"Robota {icon} {t['state']}", f"{t['title']}\n{(t.get('result') or '')[:180]}")
        except Exception:  # noqa: BLE001
            pass

    async def _run(self, t: dict) -> None:
        log = Path(t["log"])
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"=== {t['id']} {t['title']} start {_now()} ===\n")
        if t.get("gpu"):
            from robota.tools import gpu
            gpu.gpu_free()
        if t["kind"] == "command":
            self._run_command(t, log)
        elif t["kind"] == "plugin":
            self._run_plugin(t, log)
        else:
            await self._run_agent(t, log)

    def _run_command(self, t: dict, log: Path) -> None:
        ok, why = safety.check_command(t["command"])
        if not ok:
            t.update(state="failed", result=why)
            return
        proj = config.project(t.get("project"))
        cwd = proj.root if proj else str(config.ROOT / "workspace")
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        with log.open("a", encoding="utf-8") as fh:
            self._proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", t["command"]], cwd=cwd,
                stdout=fh, stderr=subprocess.STDOUT, creationflags=no_window)
            rc = self._proc.wait()
        tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
        t.update(state="done" if rc == 0 else "failed", result=f"exit {rc}\n" + "\n".join(tail))

    def _run_plugin(self, t: dict, log: Path) -> None:
        proj = config.project(t["project"])
        if proj.plugin == "cutora_queue":
            cmd = [config.PYTHON, "-u", str(config.ROOT / "robota" / "agy_queue.py"), "--root", proj.root,
                   *t.get("plugin_args", [])]
            if t.get("model"):
                cmd += ["--model", t["model"]]
        else:
            t.update(state="failed", result=f"unknown plugin {proj.plugin}")
            return
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        with log.open("a", encoding="utf-8") as fh:
            self._proc = subprocess.Popen(cmd, cwd=str(config.ROOT), stdout=fh, stderr=subprocess.STDOUT,
                                          env={**__import__("os").environ, "PYTHONUTF8": "1"},
                                          creationflags=no_window)
            rc = self._proc.wait()
        tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
        t.update(state="done" if rc == 0 else "failed", result="\n".join(tail))

    async def _run_agent(self, t: dict, log: Path) -> None:
        from robota import router

        async def on_event(ev: dict) -> None:
            with log.open("a", encoding="utf-8") as fh:
                if ev.get("type") in ("tool", "status", "route"):
                    fh.write(f"[{ev['type']}] {ev.get('text') or ev.get('route')}\n")
            if ev.get("type") in ("tool", "status"):
                self._event({"type": "task_progress", "id": t["id"], "text": ev.get("text")})

        prompt = t["prompt"] + (
            "\n\n（这是 Robota 队列里的无人值守任务：没有人能回答问题。完成后用一段话总结做了什么、结果在哪里；"
            "做不到就说明原因。）")
        reply, route = await router.respond(prompt, history=[], choice=t.get("brain") or "auto",
                                            project=t.get("project"), model=t.get("model"), on_event=on_event)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"\n--- reply ({route}, {reply.model}) ---\n{reply.text}\n")
            if reply.error:
                fh.write(f"\n--- error ---\n{reply.error}\n")
        t.update(state="done" if reply.ok else "failed", route=route, usage=reply.usage,
                 result=(reply.text or reply.error)[:4000])
