"""L1 brain (engine): smolagents ToolCallingAgent on local Ollama.

Every Robota tool (same list as robota.mcp_server.TOOLS) is wrapped as a smolagents Tool;
every call's string arguments are checked with robota.safety.check_command first.
Local only (Ollama OpenAI-compatible endpoint) -> safe for private mode.
Switch back: config/robota.toml [router] agent_engine = "antigravity".
"""
from __future__ import annotations

import asyncio
import inspect
import json
import time
import typing
from typing import Any

from robota import config, safety
from robota.brains import BrainReply, EventCB, emit

_TM = {str: "string", int: "integer", float: "number", bool: "boolean", dict: "object", list: "array"}


def _jtype(a: Any) -> str:
    if a in _TM:
        return _TM[a]
    o = typing.get_origin(a)
    if o in (list, tuple):
        return "array"
    if o is dict:
        return "object"
    args = [x for x in typing.get_args(a) if x is not type(None)]
    return _jtype(args[0]) if args else "any"


def _strings(v: Any) -> list[str]:
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [s for x in v.values() for s in _strings(x)]
    if isinstance(v, (list, tuple)):
        return [s for x in v for s in _strings(x)]
    return []


def guard(kwargs: dict) -> tuple[bool, str]:
    txt = " ".join(_strings(kwargs))
    if not txt.strip():
        return True, ""
    return safety.check_command(txt)


def wrap(fn, log: list | None = None):
    from smolagents import Tool
    sig = inspect.signature(fn)
    hints = typing.get_type_hints(fn)
    inputs = {}
    for n, p in sig.parameters.items():
        d = {"type": _jtype(hints.get(n, str)), "description": n}
        if p.default is not inspect._empty:
            d["nullable"] = True
            d["description"] += f" (optional, default {p.default!r})"
        inputs[n] = d

    def forward(self, **kw):
        kw = {k: v for k, v in kw.items() if v is not None}
        ok, why = guard(kw)
        if log is not None:
            log.append({"name": fn.__name__, "params": kw, "blocked": not ok})
        if not ok:
            return {"ok": False, "blocked": True, "error": why,
                    "note": "Robota 安全规则拒绝。不要换写法重试，直接向用户说明。"}
        return fn(**kw)

    cls = type(fn.__name__, (Tool,), {
        "name": fn.__name__, "description": (fn.__doc__ or fn.__name__).strip(), "inputs": inputs,
        "output_type": "object", "forward": forward, "skip_forward_signature_validation": True})
    return cls()


def tools(log: list | None = None):
    from robota import mcp_server
    return [wrap(f, log) for f in mcp_server.TOOLS]


def _run_sync(prompt: str, model: str, system: str, log: list, max_steps: int) -> str:
    from smolagents import OpenAIServerModel, ToolCallingAgent
    s = config.settings()["local"]
    m = OpenAIServerModel(model_id=model, api_base=str(s["ollama"]).rstrip("/") + "/v1", api_key="ollama")
    ag = ToolCallingAgent(tools=tools(log), model=m, max_steps=max_steps, verbosity_level=0,
                          instructions=system or None)
    return str(ag.run(prompt))


async def agent(prompt: str, *, workdir: str, model: str | None = None, extra_mcp: list[str] | None = None,
                system: str = "", on_event: EventCB | None = None) -> BrainReply:
    s = config.settings()
    model = model or s["local"]["model"]
    max_steps = int(s.get("router", {}).get("smol_max_steps", 10))
    log: list[dict] = []
    await emit(on_event, {"type": "status", "text": f"smolagents 引擎 · {model} 开始工作"})
    t0 = time.time()
    try:
        text = await asyncio.to_thread(_run_sync, prompt, model, system, log, max_steps)
    except Exception as e:  # noqa: BLE001
        return BrainReply("", "smol", model, ok=False, error=f"{type(e).__name__}: {e}", tools=log)
    for t in log:
        await emit(on_event, {"type": "tool", "text": f"{t['name']}: {json.dumps(t['params'], ensure_ascii=False)[:160]}"
                              + (" [已拦截]" if t["blocked"] else "")})
    await emit(on_event, {"type": "text", "text": text})
    return BrainReply(text, "smol", model, usage={"seconds": round(time.time() - t0, 1)}, tools=log)
