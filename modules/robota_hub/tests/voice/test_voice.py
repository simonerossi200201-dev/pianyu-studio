"""Voice service test: health, voices, TTS (en/it), STT round-trip, unload. Run with the gateway on 9611."""
import io
import sys
import time
from pathlib import Path

import httpx

sys.stdout.reconfigure(encoding="utf-8")
B = "http://127.0.0.1:9611"
OUT = Path(__file__).parent
c = httpx.Client(timeout=600)

print("health", c.get(f"{B}/health").json())
print("voices", [v["id"] for v in c.get(f"{B}/voices").json()])
cases = [("en", "Hello Luca, the render queue has finished. Three videos are ready for review."),
         ("it", "Ciao Luca, la coda di rendering è finita. Tre video sono pronti.")]
for lang, text in cases:
    for n in (1, 2):  # second call = warm latency
        t = time.time()
        r = c.post(f"{B}/tts", json={"text": text, "lang": lang})
        assert r.status_code == 200, r.text
        (OUT / f"tts_{lang}.wav").write_bytes(r.content)
        print(f"tts {lang} #{n}: {time.time() - t:.1f}s voice={r.headers.get('x-voice')} bytes={len(r.content)}")
    for n in (1, 2):
        t = time.time()
        r = c.post(f"{B}/stt", files={"audio": (f"tts_{lang}.wav", (OUT / f"tts_{lang}.wav").read_bytes(), "audio/wav")},
                   data={"language": lang})
        j = r.json()
        print(f"stt {lang} #{n}: {time.time() - t:.1f}s -> {j}")
r = c.post(f"{B}/tts", json={"text": "你好"})
print("tts zh:", r.status_code, r.text[:160])
print("unload", c.post(f"{B}/unload").json())
print("health", c.get(f"{B}/health").json())
