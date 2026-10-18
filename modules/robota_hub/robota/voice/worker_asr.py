"""ASR worker: faster-whisper (runs in D:\\0_Dev\\local_tts\\venv_asr, read-only use).

Request: {"audio": path, "language": "it"|""}  ->  {"text", "language", "seconds", "device"}
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from _proto import serve  # noqa: E402

MODEL = os.environ.get("ROBOTA_ASR_MODEL", "mobiuslabsgmbh/faster-whisper-large-v3-turbo")
_model = None
_device = None


def _cuda_dlls() -> None:
    """ctranslate2 needs cublas64_12/cudnn64_9; venv_kokoro's torch ships them. Only add to the DLL search path."""
    lib = os.environ.get("ROBOTA_CUDA_DLLS", r"D:\0_Dev\local_tts\venv_kokoro\Lib\site-packages\torch\lib")
    if os.path.isdir(lib):
        os.add_dll_directory(lib)
        os.environ["PATH"] = lib + os.pathsep + os.environ.get("PATH", "")


def _load():
    global _model, _device
    if _model is not None:
        return _model
    _cuda_dlls()
    from faster_whisper import WhisperModel
    want = os.environ.get("ROBOTA_ASR_DEVICE", "auto")
    tries = [("cuda", "float16"), ("cpu", "int8")] if want in ("auto", "cuda") else [("cpu", "int8")]
    err = None
    for dev, ct in tries:
        try:
            m = WhisperModel(MODEL, device=dev, compute_type=ct)
            # tiny warm-up forces CUDA libs to load now (fails fast if cuBLAS/cuDNN missing)
            import numpy as np
            list(m.transcribe(np.zeros(16000, dtype="float32"), language="en")[0])
            _model, _device = m, dev
            print(f"[asr] loaded {MODEL} on {dev}/{ct}", file=sys.stderr)
            return m
        except Exception as e:  # fall back to CPU
            err = e
            print(f"[asr] {dev} failed: {e}", file=sys.stderr)
    raise RuntimeError(f"cannot load ASR model: {err}")


def handle(req: dict) -> dict:
    if req.get("cmd") == "ping":
        return {"loaded": _model is not None, "device": _device}
    m = _load()
    t = time.time()
    lang = req.get("language") or None
    segs, info = m.transcribe(req["audio"], language=lang, beam_size=5, vad_filter=True)
    text = "".join(s.text for s in segs).strip()
    return {"text": text, "language": info.language, "seconds": round(time.time() - t, 2), "device": _device}


if __name__ == "__main__":
    serve(handle)
