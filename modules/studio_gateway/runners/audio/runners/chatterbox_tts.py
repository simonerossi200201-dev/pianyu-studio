# Chatterbox (Resemble AI, MIT) CLI. Offline; weights in runtime/models/audio/hf or D:\0_LLM\audio\models\hf
import os, sys, time, argparse
_LTX_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
_HF_DIR = os.path.join(_LTX_ROOT, "runtime", "models", "audio", "hf")
os.environ["HF_HOME"] = _HF_DIR if os.path.isdir(_HF_DIR) else r"D:\0_LLM\audio\models\hf"
os.environ["HF_HUB_OFFLINE"] = "1"
import torch, torchaudio
from chatterbox.mtl_tts import ChatterboxMultilingualTTS
ap = argparse.ArgumentParser()
ap.add_argument("--text", required=True); ap.add_argument("--out", required=True)
ap.add_argument("--ref", default=None, help="reference wav for voice cloning")
ap.add_argument("--lang", default="en"); ap.add_argument("--exaggeration", type=float, default=0.5)
ap.add_argument("--cfg", type=float, default=0.5); ap.add_argument("--seed", type=int, default=0)
a = ap.parse_args()
dev = "cuda" if torch.cuda.is_available() else ("mps" if hasattr(torch.backends, "mps") and torch.backends.mps.is_available() else "cpu")
t0 = time.time(); m = ChatterboxMultilingualTTS.from_pretrained(device=torch.device(dev)); tl = time.time() - t0
torch.manual_seed(a.seed)
if dev == "cuda":
    torch.cuda.reset_peak_memory_stats()
t1 = time.time()
kw = dict(language_id=a.lang, exaggeration=a.exaggeration, cfg_weight=a.cfg)
if a.ref: kw["audio_prompt_path"] = a.ref
wav = m.generate(a.text, **kw); tg = time.time() - t1
torchaudio.save(a.out, wav.cpu(), m.sr)
vram = (torch.cuda.max_memory_allocated() / 2**30) if dev == "cuda" else 0.0
print(f"RESULT out={a.out} load={tl:.1f}s gen={tg:.1f}s audio={wav.shape[-1]/m.sr:.1f}s device={dev} peakVRAM={vram:.2f}GB")
