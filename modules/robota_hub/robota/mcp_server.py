"""robota-mcp: Robota's tool server (MCP over stdio).

    runtime\\venv\\Scripts\\python.exe -m robota.mcp_server

All implementations live in robota.tools.* / robota.memory as plain functions;
this file only registers them. Blocking tools run in a worker thread so the
stdio loop stays responsive.
"""
from __future__ import annotations

import functools
import logging
import sys

import anyio
from mcp.server.mcpserver import MCPServer

from robota import memory
from robota.tools import gpu, notify, orante, projects, queue_api, rclone, shell, studio, watch

logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

INSTRUCTIONS = """Robota local tools for this Windows PC.
- gpu_status / gpu_free: VRAM + Ollama models; free before GPU-heavy work.
- run_in_project: run a PowerShell command in a registered project root (safety blacklist enforced).
- queue_*: Robota task queue (needs the Robota console on 127.0.0.1:9600).
- rclone_*: Google Drive via the 'gdrive' remote (list / copy only).
- studio_* / comfy_queue: local image-to-video (Studio Zero API 7870, ComfyUI 8188).
- orante_*: read-only views of the Orante 制片台 (9400).
- memory_*: persistent notes (topics like sop, habits, tutor).
Services that are not running return {ok:false, running:false, error:...}; do not try to start them."""

mcp = MCPServer("robota-mcp", instructions=INSTRUCTIONS, version="0.1.0")

TOOLS = [
    gpu.gpu_status, gpu.gpu_free,
    notify.notify,
    projects.list_projects, projects.project_info,
    shell.run_in_project,
    queue_api.queue_add, queue_api.queue_list, queue_api.queue_halt,
    rclone.rclone_ls, rclone.rclone_copy,
    studio.studio_health, studio.studio_submit, studio.studio_status, studio.studio_fetch, studio.comfy_queue,
    orante.orante_health, orante.orante_batches, orante.orante_batch, orante.orante_check,
    orante.orante_worklist, orante.orante_jobs,
    watch.watch_file,
    memory.memory_topics, memory.memory_read, memory.memory_append,
]


def _threaded(fn):
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))
    return wrapper


for _fn in TOOLS:
    mcp.tool(name=_fn.__name__, description=(_fn.__doc__ or "").strip() or None)(_threaded(_fn))


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
