"""Robota console: FastAPI app on 127.0.0.1:9600 — chat (WebSocket), queue, status, voice proxy, memory."""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from robota import __version__, config, memory, recipes, router, tasks
from robota.tools import gpu

STATIC = Path(__file__).resolve().parent / "static"
CHATS = config.DATA / "chats"
USAGE = config.DATA / "usage" / "brains.jsonl"

app = FastAPI(title="Robota", version=__version__)
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

_clients: set[WebSocket] = set()
_loop: asyncio.AbstractEventLoop | None = None
_status_cache: dict[str, Any] = {"t": 0, "data": None}


# ---------------------------------------------------------------- broadcast

def broadcast(ev: dict) -> None:
    """Thread-safe: push an event to every connected status socket."""
    if _loop is None:
        return
    for ws in list(_clients):
        asyncio.run_coroutine_threadsafe(_safe_send(ws, ev), _loop)


async def _safe_send(ws: WebSocket, ev: dict) -> None:
    try:
        await ws.send_text(json.dumps(ev, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        _clients.discard(ws)


worker = tasks.Worker(broadcast=broadcast)


@app.on_event("startup")
async def _startup() -> None:
    global _loop
    _loop = asyncio.get_running_loop()
    config.ensure_dirs()
    worker.start()


# ---------------------------------------------------------------- pages

@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/mini")
async def mini_page() -> FileResponse:
    return FileResponse(STATIC / "mini.html")


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True, "version": __version__}


# ---------------------------------------------------------------- sessions

def _session_path(sid: str) -> Path:
    if not re.fullmatch(r"[\w\-]+", sid):
        raise HTTPException(400, "bad session id")
    return CHATS / f"{sid}.json"


def load_session(sid: str) -> dict:
    p = _session_path(sid)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"id": sid, "title": "", "created": datetime.now().isoformat(timespec="seconds"),
            "messages": [], "agy_conversation": None, "mode": "auto", "project": None}


def save_session(s: dict) -> None:
    CHATS.mkdir(parents=True, exist_ok=True)
    _session_path(s["id"]).write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")


@app.get("/api/sessions")
async def sessions() -> list[dict]:
    out = []
    for f in sorted(CHATS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:100]:
        try:
            s = json.loads(f.read_text(encoding="utf-8"))
            out.append({"id": s["id"], "title": s.get("title") or "(新对话)", "mode": s.get("mode"),
                        "updated": datetime.fromtimestamp(f.stat().st_mtime).strftime("%m-%d %H:%M")})
        except (OSError, ValueError, KeyError):
            continue
    return out


@app.post("/api/sessions")
async def new_session() -> dict:
    sid = datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
    s = load_session(sid)
    save_session(s)
    return s


@app.get("/api/sessions/{sid}")
async def get_session(sid: str) -> dict:
    return load_session(sid)


@app.delete("/api/sessions/{sid}")
async def del_session(sid: str) -> dict:
    p = _session_path(sid)
    if p.exists():
        p.unlink()
    return {"ok": True}


def _log_usage(route: str, reply) -> None:
    USAGE.parent.mkdir(parents=True, exist_ok=True)
    with USAGE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"time": datetime.now().isoformat(timespec="seconds"), "route": route,
                             "brain": reply.brain, "model": reply.model, "ok": reply.ok,
                             "usage": reply.usage}, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- chat (websocket)

@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            sid = msg.get("session") or (await new_session())["id"]
            s = load_session(sid)
            text = (msg.get("text") or "").strip()
            if not text:
                continue
            mode = msg.get("mode") or "auto"
            project = msg.get("project") or None
            if s.get("project") != project or (s.get("mode") == "tutor") != (mode == "tutor"):
                s["agy_conversation"] = None
            s.update(mode=mode, project=project)
            if not s.get("title"):
                s["title"] = text[:40]
            history = [{"role": m["role"], "content": m["content"]} for m in s["messages"]]
            s["messages"].append({"role": "user", "content": text, "time": time.strftime("%H:%M")})
            save_session(s)

            async def on_event(ev: dict) -> None:
                await ws.send_text(json.dumps({**ev, "session": sid}, ensure_ascii=False))

            reply, route = await router.respond(
                text, history=history, choice=mode, project=project, model=msg.get("model") or None,
                agy_conversation=s.get("agy_conversation") if mode in ("agy",) else None, on_event=on_event)
            if reply.brain == "agy" and reply.conversation_id:
                s["agy_conversation"] = reply.conversation_id
            content = reply.text if reply.ok or reply.text else f"⚠ {reply.error}"
            s["messages"].append({"role": "assistant", "content": content, "route": route, "model": reply.model,
                                  "time": time.strftime("%H:%M"), "usage": reply.usage})
            save_session(s)
            _log_usage(route, reply)
            await ws.send_text(json.dumps({"type": "done", "session": sid, "route": route, "model": reply.model,
                                           "text": content, "ok": reply.ok, "usage": reply.usage},
                                          ensure_ascii=False))
    except WebSocketDisconnect:
        return


@app.websocket("/ws/status")
async def ws_status(ws: WebSocket) -> None:
    await ws.accept()
    _clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        _clients.discard(ws)


# ---------------------------------------------------------------- queue

@app.get("/api/queue")
async def queue_list() -> dict:
    return {"ok": True, **tasks.summary()}


@app.post("/api/queue")
async def queue_add(req: Request) -> dict:
    b = await req.json()
    try:
        t = tasks.add(b.get("title", ""), kind=b.get("kind", "agent"), prompt=b.get("prompt", ""),
                      command=b.get("command", ""), project=b.get("project") or None,
                      brain=b.get("brain", "auto"), model=b.get("model") or None, gpu=bool(b.get("gpu")),
                      plugin_args=b.get("plugin_args") or None)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    broadcast({"type": "task", "task": t})
    return {"ok": True, "task": t}


@app.post("/api/queue/halt")
async def queue_halt() -> dict:
    return {"ok": True, "halted": tasks.halt(True)}


@app.post("/api/queue/resume")
async def queue_resume() -> dict:
    return {"ok": True, "halted": tasks.halt(False)}


@app.post("/api/queue/kill")
async def queue_kill() -> dict:
    return {"ok": True, "killed": worker.kill_current()}


@app.post("/api/queue/{tid}/cancel")
async def queue_cancel(tid: str) -> dict:
    return {"ok": True, "task": tasks.cancel(tid)}


@app.post("/api/queue/{tid}/retry")
async def queue_retry(tid: str) -> dict:
    return {"ok": True, "task": tasks.retry(tid)}


@app.get("/api/queue/{tid}/log")
async def queue_log(tid: str) -> PlainTextResponse:
    t = tasks.get(tid)
    if not t:
        raise HTTPException(404)
    p = Path(t["log"])
    return PlainTextResponse(p.read_text(encoding="utf-8", errors="replace")[-60000:] if p.exists() else "(还没有日志)")


# ---------------------------------------------------------------- status

def _usage_today() -> dict:
    day = datetime.now().strftime("%Y-%m-%d")
    agg: dict[str, Any] = {"messages": 0, "agy_tokens": 0, "local_msgs": 0, "agy_msgs": 0}
    if USAGE.exists():
        for line in USAGE.read_text(encoding="utf-8").splitlines()[-2000:]:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not r["time"].startswith(day):
                continue
            agg["messages"] += 1
            if r["brain"] == "agy":
                agg["agy_msgs"] += 1
                agg["agy_tokens"] += int((r.get("usage") or {}).get("total_tokens") or 0)
            else:
                agg["local_msgs"] += 1
    q = config.LOGS / "agy_usage.jsonl"
    if q.exists():
        for line in q.read_text(encoding="utf-8").splitlines()[-500:]:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("time", "").startswith(day):
                agg["agy_tokens"] += int((r.get("usage") or {}).get("total_tokens") or 0)
    return agg


async def _voice_ok() -> bool:
    port = config.settings()["voice"]["port"]
    try:
        async with httpx.AsyncClient(timeout=1.5) as cli:
            r = await cli.get(f"http://127.0.0.1:{port}/health")
            return r.status_code == 200
    except httpx.HTTPError:
        return False


@app.get("/api/status")
async def status() -> dict:
    now = time.time()
    if _status_cache["data"] and now - _status_cache["t"] < 3:
        return _status_cache["data"]
    g = await asyncio.to_thread(gpu.gpu_status)
    data = {
        "ok": True, "version": __version__, "gpu": g, "queue": tasks.summary(),
        "usage": _usage_today(), "voice": await _voice_ok(), "agy": Path(config.AGY).exists(),
        "providers": [n for n, c in config.providers().items() if c["enabled"]],
        "projects": [{"id": p.id, "name": p.name, "private": p.private} for p in config.projects().values()],
        "tutor": config.settings().get("tutor", {}),
    }
    _status_cache.update(t=now, data=data)
    return data


@app.post("/api/gpu/free")
async def gpu_free() -> dict:
    res = await asyncio.to_thread(gpu.gpu_free)
    if await _voice_ok():
        port = config.settings()["voice"]["port"]
        try:
            async with httpx.AsyncClient(timeout=30) as cli:
                res["voice"] = (await cli.post(f"http://127.0.0.1:{port}/unload")).json().get("stopped")
        except httpx.HTTPError as e:
            res["voice"] = f"unload failed: {e}"
    _status_cache["data"] = None
    return res


@app.get("/api/projects")
async def projects() -> list[dict]:
    return [p.__dict__ for p in config.projects().values()]


# ---------------------------------------------------------------- memory & tutor

@app.get("/api/memory")
async def memory_topics() -> dict:
    return {"topics": memory.memory_topics()}


@app.get("/api/memory/{topic}")
async def memory_get(topic: str) -> dict:
    return memory.memory_read(topic)


@app.post("/api/memory/{topic}")
async def memory_add(topic: str, req: Request) -> dict:
    b = await req.json()
    return memory.memory_append(topic, b.get("text", ""))


@app.post("/api/tutor/language")
async def tutor_language(req: Request) -> dict:
    b = await req.json()
    lang = re.sub(r"[^\w\s\-]", "", b.get("target_language", ""))[:40]
    p = config.CONFIG / "robota.toml"
    txt = p.read_text(encoding="utf-8")
    txt = re.sub(r'(?m)^target_language\s*=.*$', f'target_language = "{lang}"', txt)
    p.write_text(txt, encoding="utf-8")
    _status_cache["data"] = None
    return {"ok": True, "target_language": lang}


@app.post("/api/tutor/summarize/{sid}")
async def tutor_summarize(sid: str) -> dict:
    """Distill the tutor session into memory/tutor.md (mistakes, level, new words)."""
    from robota.brains import local as local_brain
    s = load_session(sid)
    convo = "\n".join(f"[{m['role']}] {m['content']}" for m in s["messages"][-60:])
    if not convo.strip():
        return {"ok": False, "error": "empty session"}
    reply = await local_brain.chat(
        [{"role": "user", "content": "下面是一次语言陪练对话。用中文列出：1) 学生常犯的错误（附正确说法）；"
                                     "2) 新学的词；3) 对学生水平的一句话判断。只输出要点。\n\n" + convo}])
    if reply.ok and reply.text.strip():
        memory.memory_append("tutor", reply.text.strip())
    return {"ok": reply.ok, "text": reply.text, "error": reply.error}


# ---------------------------------------------------------------- avatar & recipes

@app.get("/api/avatar/styles")
async def avatar_styles() -> list[dict]:
    return [
        {"id": "a", "name": "甜美微卷栗发 (Style A)", "url": "/static/avatar_a.png"},
        {"id": "b", "name": "利落短发灵动 (Style B)", "url": "/static/avatar_b.png"},
        {"id": "c", "name": "3D温暖马尾 (Style C)", "url": "/static/avatar_c.png"},
        {"id": "d", "name": "清秀微写实 (Style D)", "url": "/static/avatar_d.png"},
    ]


@app.post("/api/avatar/select")
async def avatar_select(req: Request) -> dict:
    b = await req.json()
    style = b.get("style", "a").lower()
    src = STATIC / f"avatar_{style}.png"
    if not src.exists():
        raise HTTPException(404, f"style {style} not found")
    dest = STATIC / "avatar.png"
    import shutil
    shutil.copyfile(src, dest)
    try:
        from PIL import Image
        im = Image.open(dest)
        w, h = im.size
        size = max(w, h)
        sq = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        sq.paste(im, ((size - w) // 2, (size - h) // 2))
        sq.save(STATIC / "robota.ico", format="ICO", sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
    except Exception:
        pass
    return {"ok": True, "selected": style, "url": f"/avatar.png?v={time.time()}"}


@app.get("/api/recipes")
async def api_recipes() -> list[dict]:
    return recipes.list_recipes()


@app.get("/api/recipes/{name}")
async def api_recipe_get(name: str) -> dict:
    r = recipes.get_recipe(name)
    if not r:
        raise HTTPException(404, "recipe not found")
    return r


@app.post("/api/recipes/save")
async def api_recipe_save(req: Request) -> dict:
    b = await req.json()
    name = b.get("name", "").strip()
    if not name:
        raise HTTPException(400, "name required")
    return recipes.save_recipe(name, b)


@app.delete("/api/recipes/{name}")
async def api_recipe_del(name: str) -> dict:
    ok = recipes.delete_recipe(name)
    return {"ok": ok}


@app.post("/api/recipes/distill")
async def api_recipe_distill(req: Request) -> dict:
    b = await req.json()
    sid = b.get("session")
    name = b.get("name", "").strip()
    if not name:
        raise HTTPException(400, "name required")
    s = load_session(sid) if sid else {"messages": []}
    return await recipes.distill_session_to_recipe(name, s.get("messages", []), project=s.get("project"))


# ---------------------------------------------------------------- voice proxy

_voice_lock = threading.Lock()


def _start_voice() -> None:
    with _voice_lock:
        pyw = Path(config.PYTHON).with_name("pythonw.exe")
        exe = str(pyw if pyw.exists() else config.PYTHON)
        log = open(config.LOGS / "voice.log", "a", encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(config.ROOT), PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        subprocess.Popen([exe, "-m", "robota.voice.server"], cwd=str(config.ROOT), env=env,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


async def _ensure_voice() -> str:
    port = config.settings()["voice"]["port"]
    if await _voice_ok():
        return f"http://127.0.0.1:{port}"
    await asyncio.to_thread(_start_voice)
    for _ in range(60):
        await asyncio.sleep(1)
        if await _voice_ok():
            return f"http://127.0.0.1:{port}"
    raise HTTPException(503, "语音服务启动失败（看 robota\\voice\\README.md）")


@app.post("/api/voice/stt")
async def voice_stt(audio: UploadFile, language: str | None = None) -> dict:
    base = await _ensure_voice()
    data = await audio.read()
    async with httpx.AsyncClient(timeout=300) as cli:
        r = await cli.post(f"{base}/stt", files={"audio": (audio.filename or "rec.webm", data,
                                                            audio.content_type or "audio/webm")},
                           data={"language": language} if language else None)
    return r.json()


@app.post("/api/voice/tts")
async def voice_tts(req: Request) -> Response:
    base = await _ensure_voice()
    b = await req.json()
    async with httpx.AsyncClient(timeout=300) as cli:
        r = await cli.post(f"{base}/tts", json=b)
    return Response(r.content, media_type=r.headers.get("content-type", "audio/wav"), status_code=r.status_code)


@app.get("/api/voice/voices")
async def voice_voices() -> Any:
    base = await _ensure_voice()
    async with httpx.AsyncClient(timeout=30) as cli:
        return (await cli.get(f"{base}/voices")).json()


@app.post("/api/voice/unload")
async def voice_unload() -> Any:
    if not await _voice_ok():
        return {"ok": True, "running": False}
    port = config.settings()["voice"]["port"]
    async with httpx.AsyncClient(timeout=30) as cli:
        return (await cli.post(f"http://127.0.0.1:{port}/unload")).json()


def main() -> None:
    import uvicorn
    port = int(config.settings()["console"]["port"])
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
