"""P1 probe: does the Antigravity SDK work without an API key (i.e. via the Ultra subscription)?"""
import asyncio
import os
import sys

from google.antigravity import Agent, LocalAgentConfig


async def main() -> None:
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        print(f"{k} set: {bool(os.environ.get(k))}")
    cfg = LocalAgentConfig(workspaces=[r"D:\0_Robota\workspace\sandbox"])
    try:
        async with Agent(cfg) as agent:
            resp = await agent.chat("Reply with exactly: PONG")
            async for tok in resp:
                sys.stdout.write(tok)
            print()
    except Exception as e:  # noqa: BLE001
        print("ERROR:", type(e).__name__, e)


asyncio.run(main())
