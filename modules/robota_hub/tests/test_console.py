"""End-to-end console test: chat over WebSocket in several modes + queue tasks. Run with console up."""
import asyncio
import json
import sys
import time

import httpx
import websockets

BASE = "http://127.0.0.1:9600"


async def chat(text, mode, model="", project=""):
    t0 = time.time()
    async with websockets.connect("ws://127.0.0.1:9600/ws/chat", max_size=None) as ws:
        await ws.send(json.dumps({"session": None, "text": text, "mode": mode, "model": model, "project": project}))
        tools = []
        while True:
            ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=900))
            if ev["type"] in ("tool", "status"):
                tools.append(ev["text"])
            if ev["type"] == "done":
                print(f"\n=== [{mode}] {text}\n route={ev['route']} model={ev['model']} ok={ev['ok']} "
                      f"{time.time()-t0:.1f}s tools={tools[:6]}\n reply: {ev['text'][:400]}")
                return ev


async def main():
    which = sys.argv[1:] or ["local", "agent", "agy", "queue"]
    if "local" in which:
        await chat("用一句话介绍你自己", "local")
    if "agent" in which:
        await chat("用工具列出登记的项目，只回答项目 id", "local_agent", model="gemma4:12b")
    if "agy" in which:
        await chat("用 robota 的工具查看显存状态，用一句话告诉我已用多少 MB", "agy", model="gemini-3.8-flash-low")
    if "auto" in which:
        await chat("今天天气怎么样？随便聊一句", "auto")
    if "queue" in which:
        async with httpx.AsyncClient() as c:
            r = await c.post(BASE + "/api/queue", json={"title": "测试命令", "kind": "command", "command": "git --version", "project": "studio"})
            print("\nqueue add:", r.json()["ok"])
            bad = await c.post(BASE + "/api/queue", json={"title": "危险命令", "kind": "command", "command": "Remove-Item x"})
            print("blocked add:", bad.status_code, bad.json().get("error"))
            tid = r.json()["task"]["id"]
            for _ in range(60):
                await asyncio.sleep(2)
                t = (await c.get(BASE + "/api/queue")).json()
                cur = next(x for x in t["recent"] if x["id"] == tid)
                if cur["state"] not in ("queued", "running"):
                    print("task:", cur["state"], cur["result"][:200])
                    break


asyncio.run(main())
