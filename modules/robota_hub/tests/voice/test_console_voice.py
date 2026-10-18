"""Console voice proxy test: webm (browser format) -> /api/voice/stt, /api/voice/tts, auto-start of the gateway."""
import subprocess
import sys
import time
from pathlib import Path

import httpx

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).parent
FF = r"D:\0_Dev\CUTORA\runtime\ffmpeg\ffmpeg.exe"
webm = HERE / "tts_it.webm"
subprocess.run([FF, "-y", "-loglevel", "error", "-i", str(HERE / "tts_it.wav"), "-c:a", "libopus", str(webm)], check=True)
c = httpx.Client(timeout=600)
t = time.time()
r = c.post("http://127.0.0.1:9600/api/voice/stt", files={"audio": ("rec.webm", webm.read_bytes(), "audio/webm")})
print(f"console stt webm (incl. autostart): {time.time() - t:.1f}s -> {r.status_code} {r.text[:200]}")
t = time.time()
r = c.post("http://127.0.0.1:9600/api/voice/tts", json={"text": "Bravo! Hai fatto un ottimo lavoro oggi.", "lang": "Italian"})
print(f"console tts it: {time.time() - t:.1f}s -> {r.status_code} {r.headers.get('content-type')} {len(r.content)} bytes")
print("voices:", c.get("http://127.0.0.1:9600/api/voice/voices").json())
print("unload:", c.post("http://127.0.0.1:9600/api/voice/unload").json())
webm.unlink()
