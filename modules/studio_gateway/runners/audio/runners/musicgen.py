# MusicGen (Meta, weights CC-BY-NC-4.0) CLI via transformers. Offline; weights in D:\0_LLM\audio\models\musicgen-small
import os, time, argparse
os.environ["HF_HUB_OFFLINE"] = "1"
import torch, scipy.io.wavfile as wf
from transformers import AutoProcessor, MusicgenForConditionalGeneration
ap = argparse.ArgumentParser()
ap.add_argument("--prompt", required=True); ap.add_argument("--out", required=True)
ap.add_argument("--seconds", type=float, default=30); ap.add_argument("--model", default=r"D:\0_LLM\audio\models\musicgen-small")
ap.add_argument("--guidance", type=float, default=3.0); ap.add_argument("--seed", type=int, default=0)
a = ap.parse_args()
t0 = time.time(); proc = AutoProcessor.from_pretrained(a.model)
m = MusicgenForConditionalGeneration.from_pretrained(a.model, torch_dtype=torch.float16).to("cuda"); tl = time.time() - t0
torch.manual_seed(a.seed); torch.cuda.reset_peak_memory_stats()
inp = proc(text=[a.prompt], padding=True, return_tensors="pt").to("cuda")
tokens = int(a.seconds * m.config.audio_encoder.frame_rate)
t1 = time.time(); audio = m.generate(**inp, do_sample=True, guidance_scale=a.guidance, max_new_tokens=tokens); tg = time.time() - t1
sr = m.config.audio_encoder.sampling_rate
wf.write(a.out, sr, audio[0, 0].float().cpu().numpy())
print(f"RESULT out={a.out} load={tl:.1f}s gen={tg:.1f}s audio={audio.shape[-1]/sr:.1f}s peakVRAM={torch.cuda.max_memory_allocated()/2**30:.2f}GB")
