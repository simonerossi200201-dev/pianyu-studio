"""L2 brains: external providers configured in config/providers.toml (enabled only with a key in secrets/)."""
from __future__ import annotations

import json
from typing import Any

import httpx

from robota import config
from robota.brains import BrainReply, EventCB, emit


def available(purpose: str = "chat") -> list[str]:
    return [n for n, c in config.providers().items()
            if c["enabled"] and purpose in c.get("use_for", []) and c.get("kind") != "deepl"]


async def chat(messages: list[dict[str, str]], *, provider: str | None = None, system: str = "",
               on_event: EventCB | None = None) -> BrainReply:
    provs = config.providers()
    names = [provider] if provider else available("chat")
    if not names:
        return BrainReply("", "provider", "", ok=False, error="没有已启用的外部服务（secrets\\ 里没有 key）")
    last_err = ""
    for name in names:
        c = provs.get(name)
        if not c or not c["enabled"]:
            continue
        body = {"model": c["model"], "stream": True,
                "messages": ([{"role": "system", "content": system}] if system else []) + messages}
        out: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as cli:
                async with cli.stream("POST", c["base_url"].rstrip("/") + "/chat/completions", json=body,
                                      headers={"Authorization": f"Bearer {c['_key']}"}) as r:
                    if r.status_code != 200:
                        last_err = f"{name} {r.status_code}"
                        continue
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        ev = json.loads(data)
                        piece = ((ev.get("choices") or [{}])[0].get("delta") or {}).get("content") or ""
                        if piece:
                            out.append(piece)
                            await emit(on_event, {"type": "text", "text": piece})
            return BrainReply("".join(out), "provider", f"{name}:{c['model']}")
        except httpx.HTTPError as e:
            last_err = f"{name}: {e}"
    return BrainReply("", "provider", "", ok=False, error=last_err or "all providers failed")


async def translate(text: str, target_lang: str, source_lang: str | None = None) -> dict[str, Any]:
    c = config.providers().get("deepl")
    if not c or not c["enabled"]:
        return {"ok": False, "error": "DeepL 未启用（把 key 放进 secrets\\deepl.key）"}
    data = {"text": [text], "target_lang": target_lang.upper()}
    if source_lang:
        data["source_lang"] = source_lang.upper()
    async with httpx.AsyncClient(timeout=30) as cli:
        r = await cli.post(c["base_url"].rstrip("/") + "/translate", json=data,
                           headers={"Authorization": f"DeepL-Auth-Key {c['_key']}"})
    if r.status_code != 200:
        return {"ok": False, "error": f"DeepL {r.status_code}"}
    t = r.json()["translations"][0]
    return {"ok": True, "text": t["text"], "detected": t.get("detected_source_language")}
