"""Brains: local (Ollama via Antigravity SDK), agy (Antigravity CLI, Ultra), providers (L2 HTTP)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

EventCB = Callable[[dict[str, Any]], Awaitable[None] | None]


@dataclass
class BrainReply:
    text: str
    brain: str
    model: str
    ok: bool = True
    error: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    conversation_id: str = ""
    tools: list[dict[str, Any]] = field(default_factory=list)


async def emit(cb: EventCB | None, ev: dict[str, Any]) -> None:
    if cb is None:
        return
    r = cb(ev)
    if hasattr(r, "__await__"):
        await r  # type: ignore[misc]
