"""Robota voice gateway — 127.0.0.1:9611.

Runs in Robota's own venv (fastapi/uvicorn). Spawns two warm worker processes in the user's
existing D:\\0_Dev\\local_tts venvs (used read-only, nothing installed there):
  ASR  venv_asr     faster-whisper large-v3-turbo
  TTS  venv_kokoro  Kokoro-82M (only locally cached voices)
Workers start lazily on first use; POST /unload kills them to free VRAM.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

HERE = Path(__file__).resolve().parent
LTTS = Path(os.environ.get("ROBOTA_LOCAL_TTS", r"D:\0_Dev\local_tts"))
TMP = Path(tempfile.gettempdir()) / "robota_voice"
TMP.mkdir(exist_ok=True)
PORT = int(os.environ.get("ROBOTA_VOICE_PORT", "9611"))
FFMPEG_CANDIDATES = [r"D:\0_Dev\CUTORA\runtime\ffmpeg\ffmpeg.exe", "ffmpeg"]

# target-language words -> Kokoro voice prefix
LANG_VOICE = {"it": "i", "ital": "i", "意大利": "i", "en": "a", "eng": "a", "英": "a", "es": "e", "span": "e", "西班牙": "e",
              "fr": "f", "fren": "f", "franç": "f", "法": "f", "pt": "p", "portu": "p", "葡萄牙": "p", "ja": "j", "japan": "j",
              "日": "j", "zh": "z", "chin": "z", "中": "z", "hi": "h", "hindi": "h", "gb": "b", "brit": "b"}


class Worker:
    def __init__(self, name: str, venv: str, script: str, python_path: Path | None = None):
        self.name, self.venv, self.script = name, venv, script
        self._custom_python = python_path
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()

    @property
    def python(self) -> Path:
        if self._custom_python:
            return self._custom_python
        return LTTS / self.venv / "Scripts" / "python.exe"

    def available(self) -> bool:
        return self.python.exists()

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _start(self) -> None:
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        if self.name != "edge":
            env.update({
                "HF_HOME": str(LTTS / "cache" / "hf"),
                "HF_HUB_CACHE": str(LTTS / "cache" / "hf" / "hub"),
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "TORCH_HOME": str(LTTS / "cache" / "torch"),
            })
        log = open(HERE.parent.parent / "logs" / f"voice_{self.name}.log", "a", encoding="utf-8")
        self.proc = subprocess.Popen([str(self.python), "-u", str(HERE / self.script)], cwd=str(HERE), env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True,
                                     encoding="utf-8", bufsize=1, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        first = self.proc.stdout.readline()
        if '"ready"' not in first:
            self.stop()
            raise RuntimeError(f"{self.name} worker failed to start (see logs\\voice_{self.name}.log)")

    def call(self, req: dict, timeout: float = 600) -> dict:
        with self.lock:
            if not self.running():
                self._start()
            self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
            out: list[str] = []
            t = threading.Thread(target=lambda: out.append(self.proc.stdout.readline()), daemon=True)
            t.start()
            t.join(timeout)
            if not out or not out[0]:
                self.stop()
                raise RuntimeError(f"{self.name} worker timed out or died")
            return json.loads(out[0])

    def stop(self) -> bool:
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(10)
            self.proc = None
            return True
        self.proc = None
        return False


EDGE_PY = Path(r"D:\0_Dev\AudioClone\.venv_cloner\Scripts\python.exe")
ASR = Worker("asr", "venv_asr", "worker_asr.py")
TTS = Worker("tts", "venv_kokoro", "worker_tts.py")
EDGE = Worker("edge", ".venv_cloner", "worker_edge.py", python_path=EDGE_PY)
app = FastAPI(title="Robota voice")
_voices_cache: list[dict] = []


def _voices() -> list[dict]:
    global _voices_cache
    if not _voices_cache:
        out = []
        if EDGE.available():
            try:
                sys.path.insert(0, str(HERE))
                import worker_edge as we
                out.extend(we.list_voices())
            except Exception:
                pass
        if TTS.available():
            try:
                hub = LTTS / "cache" / "hf" / "hub" / "models--hexgrad--Kokoro-82M" / "snapshots"
                sys.path.insert(0, str(HERE))
                import worker_tts as wt
                os.environ.setdefault("HF_HUB_CACHE", str(hub.parent.parent))
                out.extend(wt.voices())
            except Exception:
                pass
        _voices_cache = out
    return _voices_cache


def _pick_voice(text: str, voice: str | None, lang: str | None) -> str:
    ids = [v["id"] for v in _voices()]
    if voice and voice in ids:
        return voice

    # When Edge Neural TTS is available, use unified macro-polyglot teaching voice
    if EDGE.available():
        if voice and voice in ids:
            return voice
        return "macro_polyglot"

    # Fallback to Kokoro
    prefix = None
    if lang:
        low = lang.lower()
        prefix = next((p for k, p in LANG_VOICE.items() if low.startswith(k) or k in low), None)
    if prefix is None:
        prefix = "z" if re.search(r"[\u4e00-\u9fff]", text) else "a"
    for v in ids:
        if v.startswith(prefix + "f"):
            return v
    for v in ids:
        if v.startswith(prefix):
            return v
    raise HTTPException(422, f"no local voice for language '{lang or prefix}'; available: {', '.join(ids)}")


def _to_wav(src: Path) -> Path:
    """Browser recordings (webm/ogg) -> 16k mono wav. faster-whisper can decode via PyAV too; this is a safety net."""
    if src.suffix.lower() == ".wav":
        return src
    dst = src.with_suffix(".wav")
    for ff in FFMPEG_CANDIDATES:
        try:
            r = subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(src), "-ac", "1", "-ar", "16000", str(dst)],
                               capture_output=True, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode == 0 and dst.exists():
                return dst
        except (OSError, subprocess.TimeoutExpired):
            continue
    return src  # let PyAV try


@app.get("/health")
def health() -> dict:
    return {"ok": True, "stt": ASR.available(), "tts": TTS.available(), "edge": EDGE.available(),
            "voices": len(_voices()),
            "loaded": {"asr": ASR.running(), "tts": TTS.running(), "edge": EDGE.running()}}


@app.get("/voices")
def voices() -> list[dict]:
    return _voices()


@app.post("/stt")
async def stt(audio: UploadFile, language: str | None = Form(None)) -> dict:
    ext = Path(audio.filename or "rec.webm").suffix or ".webm"
    src = TMP / f"{uuid.uuid4().hex}{ext}"
    src.write_bytes(await audio.read())
    wav = None
    try:
        t = time.time()
        wav = _to_wav(src)
        lang = (language or "").strip().lower()[:5] or ""
        res = ASR.call({"audio": str(wav), "language": lang})
        if not res.get("ok"):
            raise HTTPException(500, res.get("error"))
        res["total_seconds"] = round(time.time() - t, 2)
        return res
    finally:
        for p in {src, wav}:
            if p:
                p.unlink(missing_ok=True)


@app.post("/tts")
async def tts(req: Request):
    b = await req.json()
    text = (b.get("text") or "").strip()[:4000]
    if not text:
        raise HTTPException(400, "empty text")
    voice = _pick_voice(text, b.get("voice"), b.get("lang"))

    from fastapi import Response
    if (voice == "macro_polyglot" or "Neural" in voice) and EDGE.available():
        out = TMP / f"{uuid.uuid4().hex}.mp3"
        res = EDGE.call({"text": text, "voice": voice, "lang": b.get("lang"), "rate": b.get("rate") or "+0%", "out": str(out),
                         "context": (b.get("context") or "")[:8000]})
        if not res.get("ok"):
            raise HTTPException(500, res.get("error"))
        data = out.read_bytes()
        out.unlink(missing_ok=True)
        return Response(data, media_type="audio/mpeg", headers={"X-Voice": voice, "X-Seconds": str(res.get("seconds"))})
    else:
        out = TMP / f"{uuid.uuid4().hex}.wav"
        res = TTS.call({"text": text, "voice": voice, "speed": b.get("speed") or 1.0, "out": str(out)})
        if not res.get("ok"):
            raise HTTPException(500, res.get("error"))
        data = out.read_bytes()
        out.unlink(missing_ok=True)
        return Response(data, media_type="audio/wav", headers={"X-Voice": voice, "X-Seconds": str(res.get("seconds"))})


@app.post("/unload")
def unload() -> dict:
    global _voices_cache
    _voices_cache = []
    return {"ok": True, "stopped": {"asr": ASR.stop(), "tts": TTS.stop(), "edge": EDGE.stop()}}


@app.on_event("shutdown")
def _bye() -> None:
    ASR.stop()
    TTS.stop()
    EDGE.stop()


def main() -> None:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
