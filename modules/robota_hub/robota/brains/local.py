"""L1 brain: local Ollama.

Two modes:
  * chat()  – plain streaming chat via Ollama /api/chat (fast; tutor, small talk, summaries)
  * agent() – Antigravity SDK agent loop on Ollama with tools (files, commands, robota-mcp)
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from robota import config, safety
from robota.brains import BrainReply, EventCB, emit


async def chat(messages: list[dict[str, str]], *, model: str | None = None, system: str = "",
               on_event: EventCB | None = None, think: bool | None = False) -> BrainReply:
    s = config.settings()["local"]
    model = model or s["fast_model"]
    msgs = ([{"role": "system", "content": system}] if system else []) + messages
    body: dict[str, Any] = {"model": model, "messages": msgs, "stream": True,
                            "options": {"num_ctx": int(s["num_ctx"])}}
    if think is not None:
        body["think"] = think
    out: list[str] = []
    usage: dict[str, Any] = {}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=5)) as cli:
            async with cli.stream("POST", f"{s['ollama']}/api/chat", json=body) as r:
                if r.status_code != 200:
                    txt = (await r.aread()).decode("utf-8", "replace")
                    return BrainReply("", "local", model, ok=False, error=f"ollama {r.status_code}: {txt[:300]}")
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    ev = json.loads(line)
                    piece = (ev.get("message") or {}).get("content", "")
                    if piece:
                        out.append(piece)
                        await emit(on_event, {"type": "text", "text": piece})
                    if ev.get("done"):
                        usage = {"input_tokens": ev.get("prompt_eval_count"), "output_tokens": ev.get("eval_count"),
                                 "eval_s": round((ev.get("eval_duration") or 0) / 1e9, 1)}
    except httpx.HTTPError as e:
        return BrainReply("".join(out), "local", model, ok=False, error=f"Ollama 连接失败：{e}")
    await emit(on_event, {"type": "usage", "usage": usage, "brain": "local", "model": model})
    return BrainReply("".join(out), "local", model, usage=usage)


def _deny_unsafe(args: Any) -> bool:
    """Policy predicate: True => deny this run_command call."""
    try:
        values = args.values() if isinstance(args, dict) else [str(args)]
        cmd = " ".join(str(v) for v in values if isinstance(v, str))
    except Exception:  # noqa: BLE001
        return True
    ok, _ = safety.check_command(cmd)
    return not ok


async def agent(prompt: str, *, workdir: str, model: str | None = None, extra_mcp: list[str] | None = None,
                system: str = "", on_event: EventCB | None = None) -> BrainReply:
    from google.antigravity import Agent, LocalOpenAIAgentConfig, types
    from google.antigravity import policy as pol

    s = config.settings()["local"]
    model = model or s["model"]
    mcp: list[Any] = [types.McpStdioServer(
        name="robota", command=config.PYTHON, args=["-m", "robota.mcp_server"],
        env={"PYTHONPATH": str(config.ROOT), "PYTHONIOENCODING": "utf-8"})]
    for i, bat in enumerate(extra_mcp or []):
        mcp.append(types.McpStdioServer(name=f"project{i}", command="cmd.exe", args=["/c", bat]))

    policies = [
        pol.deny("run_command", when=_deny_unsafe, name="robota_safety",
                 reason="blocked by Robota safety rules"),
        *pol.workspace_only([workdir]),
        pol.allow_all(),
    ]
    cfg = LocalOpenAIAgentConfig(
        model=model, base_url=f"{s['ollama']}/v1", workspaces=[workdir],
        system_instructions=system or None, mcp_servers=mcp, policies=policies)
    texts: list[str] = []
    tools: list[dict[str, Any]] = []
    await emit(on_event, {"type": "status", "text": f"本地 · {model} 开始工作"})
    try:
        async with Agent(cfg) as ag:
            resp = await ag.chat(prompt)
            async for ch in resp.chunks:
                kind = type(ch).__name__
                if kind == "ToolCall":
                    tools.append({"name": ch.name, "params": ch.args})
                    await emit(on_event, {"type": "tool", "text": f"{ch.name}: {str(ch.args)[:160]}"})
                elif kind == "ToolResult":
                    continue
                else:
                    t = getattr(ch, "text", "") or ""
                    if t:
                        texts.append(t)
                        await emit(on_event, {"type": "text", "text": t})
    except Exception as e:  # noqa: BLE001
        return BrainReply("".join(texts), "local", model, ok=False, error=f"{type(e).__name__}: {e}", tools=tools)
    return BrainReply("".join(texts), "local", model, tools=tools)
