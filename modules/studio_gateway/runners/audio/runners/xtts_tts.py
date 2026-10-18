# XTTS-v2 (Coqui, CPML: non-commercial only) CLI. Offline; weights in D:\0_LLM\audio\models\tts
import os, sys, time, argparse
os.environ["TTS_HOME"] = r"D:\0_LLM\audio\models"; os.environ.setdefault("COQUI_TOS_AGREED", "1")
import torch
from TTS.api import TTS
ap = argparse.ArgumentParser()
ap.add_argument("--text", required=True); ap.add_argument("--out", required=True)
ap.add_argument("--ref", default=None, help="reference wav for cloning; else built-in speaker")
ap.add_argument("--speaker", default="Ana Florence"); ap.add_argument("--lang", default="en")
a = ap.parse_args()
t0 = time.time(); tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to("cuda"); tl = time.time() - t0
torch.cuda.reset_peak_memory_stats(); t1 = time.time()
if a.ref: tts.tts_to_file(text=a.text, speaker_wav=a.ref, language=a.lang, file_path=a.out)
else: tts.tts_to_file(text=a.text, speaker=a.speaker, language=a.lang, file_path=a.out)
print(f"RESULT out={a.out} load={tl:.1f}s gen={time.time()-t1:.1f}s peakVRAM={torch.cuda.max_memory_allocated()/2**30:.2f}GB")
