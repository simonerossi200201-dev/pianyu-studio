# Stable Audio Open 1.0 via ComfyUI core nodes (Stability AI Community License). Usage: sao_run.py --prompt ... --seconds 30 --out-name x
import json, sys, time, uuid, urllib.request, argparse, subprocess, threading, os, shutil
ap = argparse.ArgumentParser(); ap.add_argument("--prompt", required=True); ap.add_argument("--neg", default="low quality, distorted, noise, vocals")
ap.add_argument("--seconds", type=float, default=30); ap.add_argument("--steps", type=int, default=50); ap.add_argument("--seed", type=int, default=0)
ap.add_argument("--out-name", dest="out", required=True); a = ap.parse_args()
URL = "http://127.0.0.1:8188"; OUT = r"D:\0_LLM\Outputs\audio_test"
P = {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "stable-audio-open-1.0.safetensors"}},
 "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "t5_base.safetensors", "type": "stable_audio", "device": "default"}},
 "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": a.prompt}},
 "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": a.neg}},
 "5": {"class_type": "ConditioningStableAudio", "inputs": {"positive": ["3", 0], "negative": ["4", 0], "seconds_start": 0.0, "seconds_total": a.seconds}},
 "6": {"class_type": "EmptyLatentAudio", "inputs": {"seconds": a.seconds, "batch_size": 1}},
 "7": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["5", 0], "negative": ["5", 1], "latent_image": ["6", 0], "seed": a.seed, "steps": a.steps, "cfg": 4.98, "sampler_name": "dpmpp_3m_sde_gpu", "scheduler": "exponential", "denoise": 1.0}},
 "8": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["7", 0], "vae": ["1", 2]}},
 "9": {"class_type": "SaveAudio", "inputs": {"audio": ["8", 0], "filename_prefix": "sao/" + a.out}}}
def req(p, d=None):
    r = urllib.request.Request(URL + p, data=json.dumps(d).encode() if d else None, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=60).read())
peak = {"m": 0}; stop = False
def mon():
    while not stop:
        try: peak["m"] = max(peak["m"], int(subprocess.run(["nvidia-smi","--query-gpu=memory.used","--format=csv,noheader,nounits"],capture_output=True,text=True,timeout=10).stdout.strip()))
        except Exception: pass
        time.sleep(1)
threading.Thread(target=mon, daemon=True).start(); t0 = time.time()
try: pid = req("/prompt", {"prompt": P, "client_id": str(uuid.uuid4())})["prompt_id"]
except urllib.error.HTTPError as e: print("SUBMIT_ERROR", e.read().decode()[:3000]); sys.exit(2)
while True:
    time.sleep(3); h = req("/history/" + pid)
    if pid in h: break
stop = True; files = []
for o in h[pid].get("outputs", {}).values():
    for f in o.get("audio", []) or []:
        src = os.path.join(r"D:\0_LLM\ComfyUI\output", f.get("subfolder", ""), f["filename"]); dst = os.path.join(OUT, a.out + os.path.splitext(src)[1]); shutil.copy(src, dst); files.append(dst)
print("RESULT " + json.dumps({"name": a.out, "status": h[pid]["status"].get("status_str"), "wall_s": round(time.time()-t0,1), "peak_vram_mib": peak["m"], "files": files}))
