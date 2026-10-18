"""Studio Zero local video API (127.0.0.1:7870, see D:\\0_LLM\\studio_api.py) + ComfyUI queue (8188).

Request schema (POST /v1/videos), from studio_api.validate():
  image_path (abs local path) | image_b64, direction (导演说明) and/or line (台词),
  seconds 1..15, quality fast|balanced|camera (default balanced), aspect '9:16'|'16:9'|'' (follow image),
  end_image_path?, seed?, lock_last (default true), client_ref?
"""
from __future__ import annotations

from pathlib import Path

import httpx

from robota.tools import _http, gpu

STUDIO = "http://127.0.0.1:7870"
COMFY = "http://127.0.0.1:8188"


def studio_health() -> dict:
    """Studio API health: {ok, running, comfy, busy, queued, version, presets}."""
    try:
        res = _http.request("GET", STUDIO + "/v1/health", timeout=5, service="Studio API")
        res = dict(res) if isinstance(res, dict) else {"raw": res}
        res.setdefault("ok", True)
        res["running"] = True
        return res
    except _http.ServiceDown as exc:
        return _http.down("Studio API", Exception(f"{exc}. Start it with D:\\0_LLM\\启动Studio API.bat"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "running": True, "error": str(exc)}


def studio_submit(image_path: str, prompt: str = "", seconds: float = 5, quality: str = "balanced",
                  aspect: str = "", line: str = "", end_image_path: str = "", seed: int | None = None,
                  lock_last: bool = True, client_ref: str = "robota", free_gpu: bool = True) -> dict:
    """Submit an image-to-video job. `prompt` is sent as `direction` (director's note), `line` is spoken text.
    Unloads Ollama models first (free_gpu) so ComfyUI has the VRAM."""
    if not image_path or not Path(image_path).is_file():
        return {"ok": False, "error": f"image not found: {image_path}"}
    if not (prompt or line):
        return {"ok": False, "error": "need prompt (direction) and/or line"}
    freed = gpu.gpu_free() if free_gpu else {"freed": []}
    body = {"image_path": str(Path(image_path).resolve()), "direction": prompt or "", "line": line or "",
            "seconds": seconds, "quality": quality or "balanced", "aspect": aspect or "",
            "lock_last": bool(lock_last), "client_ref": client_ref or ""}
    if end_image_path:
        body["end_image_path"] = str(Path(end_image_path).resolve())
    if seed is not None:
        body["seed"] = seed
    try:
        res = _http.request("POST", STUDIO + "/v1/videos", json=body, timeout=30, service="Studio API")
    except _http.ServiceDown as exc:
        return _http.down("Studio API", exc)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    out = dict(res) if isinstance(res, dict) else {"raw": res}
    out.setdefault("ok", True)
    out["gpu_freed"] = freed.get("freed", [])
    return out


def studio_status(id: str) -> dict:  # noqa: A002
    """Job status: {id, state, progress, error, width, height, frames, seconds_out, seed, elapsed, ...}."""
    try:
        res = _http.request("GET", f"{STUDIO}/v1/videos/{id}", timeout=10, service="Studio API")
        out = dict(res) if isinstance(res, dict) else {"raw": res}
        out.setdefault("ok", True)
        return out
    except _http.ServiceDown as exc:
        return _http.down("Studio API", exc)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def studio_fetch(id: str, dest_path: str) -> dict:  # noqa: A002
    """Download the finished mp4 to dest_path (a file path, or an existing folder -> <id>.mp4)."""
    dest = Path(dest_path)
    if dest.is_dir() or str(dest_path).endswith(("\\", "/")):
        dest = dest / f"{id}.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with httpx.Client(timeout=120, trust_env=False) as c, c.stream("GET", f"{STUDIO}/v1/videos/{id}/file") as r:
            if r.status_code >= 400:
                r.read()
                try:
                    msg = r.json().get("error")
                except ValueError:
                    msg = r.text[:300]
                return {"ok": False, "error": f"Studio API HTTP {r.status_code}: {msg}"}
            with tmp.open("wb") as fh:
                for chunk in r.iter_bytes(1 << 20):
                    fh.write(chunk)
        tmp.replace(dest)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return {"ok": False, "running": False, "error": "Studio API is not running (127.0.0.1:7870)"}
    except Exception as exc:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "path": str(dest), "bytes": dest.stat().st_size}


def comfy_queue() -> dict:
    """ComfyUI queue: counts + prompt ids of running/pending items."""
    try:
        res = _http.request("GET", COMFY + "/queue", timeout=5, service="ComfyUI")
    except _http.ServiceDown as exc:
        return _http.down("ComfyUI", exc)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    run = res.get("queue_running", []) if isinstance(res, dict) else []
    pend = res.get("queue_pending", []) if isinstance(res, dict) else []

    def ids(items):
        return [it[1] for it in items if isinstance(it, list) and len(it) > 1]
    return {"ok": True, "running": True, "n_running": len(run), "n_pending": len(pend),
            "running_ids": ids(run), "pending_ids": ids(pend)}
