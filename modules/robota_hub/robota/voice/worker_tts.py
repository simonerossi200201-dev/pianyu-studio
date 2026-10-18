"""TTS worker: Kokoro (runs in D:\\0_Dev\\local_tts\\venv_kokoro, read-only use).

Request: {"text", "voice", "speed", "out": wav_path}  ->  {"seconds", "audio_s", "voice"}
Only voices already in the local HF cache are offered (HF_HUB_OFFLINE=1 set by the gateway).
"""
from __future__ import annotations

import glob
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

REPO = "hexgrad/Kokoro-82M"
# Kokoro voice prefix -> (lang_code, language tag, label)
LANGS = {"a": ("a", "en-US", "English (US)"), "b": ("b", "en-GB", "English (UK)"), "i": ("i", "it", "Italiano"),
         "e": ("e", "es", "Español"), "f": ("f", "fr", "Français"), "p": ("p", "pt-BR", "Português"),
         "h": ("h", "hi", "Hindi"), "j": ("j", "ja", "日本語"), "z": ("z", "zh", "中文")}
_pipes: dict = {}
_device = None


def voices() -> list[dict]:
    hub = os.environ.get("HF_HUB_CACHE") or os.path.join(os.environ.get("HF_HOME", ""), "hub")
    files = glob.glob(os.path.join(hub, "models--hexgrad--Kokoro-82M", "snapshots", "*", "voices", "*.pt"))
    out = []
    for f in sorted(set(os.path.basename(x)[:-3] for x in files)):
        lc = LANGS.get(f[0])
        if lc:
            out.append({"id": f, "lang": lc[1], "label": f"{lc[2]} · {f[3:]} ({'女' if f[1] == 'f' else '男'})"})
    return out


def _pipe(lang_code: str):
    global _device
    if lang_code not in _pipes:
        import torch
        from kokoro import KPipeline
        _device = "cuda" if torch.cuda.is_available() else "cpu"
        _pipes[lang_code] = KPipeline(lang_code=lang_code, repo_id=REPO, device=_device)
    return _pipes[lang_code]


def handle(req: dict) -> dict:
    cmd = req.get("cmd")
    if cmd == "ping":
        return {"loaded": list(_pipes), "device": _device}
    if cmd == "voices":
        return {"voices": voices()}
    import numpy as np
    import soundfile as sf
    voice = req.get("voice") or "af_heart"
    p = _pipe(voice[0])
    t = time.time()
    chunks = [a.numpy() if hasattr(a, "numpy") else a for _, _, a in p(req["text"], voice=voice, speed=float(req.get("speed") or 1.0))]
    if not chunks:
        raise ValueError("no audio produced (empty text?)")
    wav = np.concatenate(chunks)
    sf.write(req["out"], wav, 24000)
    return {"seconds": round(time.time() - t, 2), "audio_s": round(len(wav) / 24000, 2), "voice": voice, "device": _device}


if __name__ == "__main__":
    from _proto import serve
    serve(handle)
