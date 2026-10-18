"""P1 probe: Antigravity SDK harness driven by local Ollama (OpenAI-compatible endpoint)."""
import asyncio
import sys
import time

from google.antigravity import Agent, LocalOpenAIAgentConfig

MODEL = sys.argv[1] if len(sys.argv) > 1 else "gemma4:12b"
SANDBOX = r"D:\0_Robota\workspace\sandbox"


async def main() -> None:
    cfg = LocalOpenAIAgentConfig(
        model=MODEL,
        base_url="http://localhost:11434/v1",
        workspaces=[SANDBOX],
    )
    t0 = time.time()
    async with Agent(cfg) as agent:
        resp = await agent.chat(
            f"In the folder {SANDBOX}, create a file named hello.txt containing the text "
            "'Robota is alive'. Then read it back and tell me its content."
        )
        async for ch in resp.chunks:
            kind = type(ch).__name__
            if kind == "ToolCall":
                print(f"\n[tool] {ch.name} {ch.args}", flush=True)
            elif kind == "ToolResult":
                print(f"[result] {str(ch)[:200]}", flush=True)
            else:
                print(getattr(ch, "text", "") or "", end="", flush=True)
        print("\n[stop]", resp.stop_reason, "[usage]", resp.usage_metadata)
    print(f"[time] {time.time() - t0:.1f}s")


asyncio.run(main())
