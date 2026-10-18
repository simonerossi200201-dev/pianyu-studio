# Chatterbox (Resemble AI, MIT) CLI. Offline; weights in D:\0_LLM\audio\models\hf
import os, sys, time, argparse
os.environ["HF_HOME"] = r"D:\0_LLM\audio\models\hf"; os.environ["HF_HUB_OFFLINE"] = "1"
import torch, torchaudio
from chatterbox.mtl_tts import ChatterboxMultilingualTTS
ap = argparse.ArgumentParser()
ap.add_argument("--text", required=True); ap.add_argument("--out", required=True)
ap.add_argument("--ref", default=None, help="reference wav for voice cloning")
ap.add_argument("--lang", default="en"); ap.add_argument("--exaggeration", type=float, default=0.5)
ap.add_argument("--cfg", type=float, default=0.5); ap.add_argument("--seed", type=int, default=0)
a = ap.parse_args()
t0 = time.time(); m = ChatterboxMultilingualTTS.from_pretrained(device=torch.device("cuda")); tl = time.time() - t0
torch.manual_seed(a.seed); torch.cuda.reset_peak_memory_stats(); t1 = time.time()
kw = dict(language_id=a.lang, exaggeration=a.exaggeration, cfg_weight=a.cfg)
if a.ref: kw["audio_prompt_path"] = a.ref
wav = m.generate(a.text, **kw); tg = time.time() - t1
torchaudio.save(a.out, wav.cpu(), m.sr)
print(f"RESULT out={a.out} load={tl:.1f}s gen={tg:.1f}s audio={wav.shape[-1]/m.sr:.1f}s peakVRAM={torch.cuda.max_memory_allocated()/2**30:.2f}GB")
