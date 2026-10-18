"""L3 brain: Antigravity CLI (`agy`) on the user's Ultra login. Headless, stream-json."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from robota import config
from robota.brains import BrainReply, EventCB, emit

MAX_ARG = 24000


def _summarize_tool(info: dict[str, Any]) -> str:
    params = info.get("parameters") or {}
    for k in ("CommandLine", "AbsolutePath", "TargetFile", "Url", "query", "path"):
        if k in params:
            return f"{info.get('name')}: {str(params[k])[:160]}"
    return str(info.get("name", "tool"))


async def chat(prompt: str, *, cwd: str | None = None, model: str | None = None,
               conversation_id: str | None = None, on_event: EventCB | None = None,
               timeout_min: int | None = None, extra_dirs: list[str] | None = None,
               log_path: Path | None = None) -> BrainReply:
    s = config.settings()["agy"]
    model = model or s["model"]
    timeout_min = timeout_min or int(s["timeout_min"])
    workdir = cwd or str(config.ROOT / "workspace")
    Path(workdir).mkdir(parents=True, exist_ok=True)

    if len(prompt) > MAX_ARG:
        spill = config.DATA / "chats" / "_long_prompt.md"
        spill.write_text(prompt, encoding="utf-8")
        prompt = f"The full instructions are in the file {spill}. Read it completely and follow it."

    cmd = [config.AGY, "-p", prompt, "--model", model, "--mode", "accept-edits",
           "--output-format", "stream-json", "--print-timeout", f"{timeout_min}m"]
    if conversation_id:
        cmd += ["--conversation", conversation_id]
    for d in extra_dirs or []:
        cmd += ["--add-dir", d]

    if not Path(config.AGY).exists():
        return BrainReply("", "agy", model, ok=False, error=f"agy not found at {config.AGY}")

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=workdir, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env,
        creationflags=no_window)
    await emit(on_event, {"type": "status", "text": f"agy · {model} 开始工作"})
    reply = BrainReply("", "agy", model)
    other: list[str] = []
    logf = log_path.open("a", encoding="utf-8") if log_path else None
    try:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace")
            if logf:
                logf.write(line)
            s_ = line.strip()
            if not s_.startswith("{"):
                if s_:
                    other.append(s_)
                continue
            try:
                ev = json.loads(s_)
            except ValueError:
                continue
            kind = ev.get("event")
            if kind == "init":
                reply.conversation_id = ev.get("conversation_id", "")
            elif kind == "step_update":
                su = ev.get("step_update", {})
                if su.get("step_type") == "tool" and su.get("state") in ("ACTIVE", "DONE"):
                    info = su.get("tool_info") or {"name": su.get("tool_name")}
                    if su.get("state") == "ACTIVE":
                        await emit(on_event, {"type": "tool", "text": _summarize_tool(info)})
                    else:
                        reply.tools.append({"name": info.get("name"), "params": info.get("parameters")})
            elif kind == "result":
                r = ev.get("result", {})
                reply.text = r.get("response", "")
                reply.usage = r.get("usage", {})
                reply.conversation_id = r.get("conversation_id", reply.conversation_id)
                if r.get("status") != "SUCCESS":
                    reply.ok = False
                    reply.error = r.get("status", "")
                if r.get("denied_actions"):
                    reply.error = (reply.error + " " if reply.error else "") + f"denied: {r['denied_actions']}"
        await proc.wait()
    finally:
        if logf:
            logf.close()
    if proc.returncode != 0 and not reply.text:
        reply.ok = False
        reply.error = reply.error or ("\n".join(other[-8:]) or f"agy exit {proc.returncode}")
    await emit(on_event, {"type": "usage", "usage": reply.usage, "brain": "agy", "model": model})
    return reply
