# XTTS-v2 (Coqui, CPML: non-commercial only) CLI. Offline; weights in runtime/models/audio/tts or D:\0_LLM\audio\models
import os, sys, time, argparse
_LTX_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
_TTS_DIR = os.path.join(_LTX_ROOT, "runtime", "models", "audio")
os.environ["TTS_HOME"] = _TTS_DIR if os.path.isdir(_TTS_DIR) else r"D:\0_LLM\audio\models"
os.environ.setdefault("COQUI_TOS_AGREED", "1")
import torch
from TTS.api import TTS
ap = argparse.ArgumentParser()
ap.add_argument("--text", required=True); ap.add_argument("--out", required=True)
ap.add_argument("--ref", default=None, help="reference wav for cloning; else built-in speaker")
ap.add_argument("--speaker", default="Ana Florence"); ap.add_argument("--lang", default="en")
a = ap.parse_args()
dev = "cuda" if torch.cuda.is_available() else "cpu"
t0 = time.time(); tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(dev); tl = time.time() - t0
if dev == "cuda":
    torch.cuda.reset_peak_memory_stats()
t1 = time.time()
if a.ref: tts.tts_to_file(text=a.text, speaker_wav=a.ref, language=a.lang, file_path=a.out)
else: tts.tts_to_file(text=a.text, speaker=a.speaker, language=a.lang, file_path=a.out)
vram = (torch.cuda.max_memory_allocated() / 2**30) if dev == "cuda" else 0.0
print(f"RESULT out={a.out} load={tl:.1f}s gen={time.time()-t1:.1f}s device={dev} peakVRAM={vram:.2f}GB")
