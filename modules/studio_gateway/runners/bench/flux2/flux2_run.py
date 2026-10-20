# FLUX.2 [klein] 4B (distilled, fp8) runner for the local ComfyUI on 127.0.0.1:8188.
# Flattened from the official ComfyUI templates image_flux2_klein_text_to_image.json (distilled branch)
# and image_flux2_klein_image_edit_4b_distilled.json (single-reference edit).
import argparse, json, os, shutil, subprocess, sys, threading, time, urllib.request, uuid

ap = argparse.ArgumentParser()
ap.add_argument("--prompt", required=True)
ap.add_argument("--w", type=int, default=1088)
ap.add_argument("--h", type=int, default=1920)
ap.add_argument("--seed", type=int, default=0)
ap.add_argument("--steps", type=int, default=4)
ap.add_argument("--out-name", dest="out_name", required=True)
ap.add_argument("--out-dir", dest="out_dir", default=r"D:\0_LLM\Outputs\flux2_test", help="output directory for generated PNG")
ap.add_argument("--edit-image", dest="edit_image", default=None, help="file name in ComfyUI\\input or any local file path to edit")
ap.add_argument("--unet", default="flux-2-klein-4b-nvfp4.safetensors")
ap.add_argument("--clip", default="qwen_3_4b_fp8_mixed.safetensors")
a = ap.parse_args()

# Auto-align width and height to multiples of 16
a.w = max(256, (int(a.w) // 16) * 16)
a.h = max(256, (int(a.h) // 16) * 16)

URL = "http://127.0.0.1:8188"
DEFAULT_OUT = r"D:\0_LLM\Outputs\flux2_test"
OUT = a.out_dir or DEFAULT_OUT
COMFY_INPUT_DIRS = [r"D:\0_LLM\ComfyUI\input", r"D:\0_LTX\runtime\comfyui\input"]

edit_image_name = a.edit_image
if a.edit_image:
    cand_path = os.path.abspath(a.edit_image) if (os.path.isabs(a.edit_image) or os.path.isfile(a.edit_image)) else None
    if cand_path and os.path.isfile(cand_path):
        ext = os.path.splitext(cand_path)[1].lower() or ".png"
        unique_name = f"flux2_edit_{uuid.uuid4().hex[:10]}{ext}"
        for inp_dir in COMFY_INPUT_DIRS:
            if os.path.isdir(os.path.dirname(inp_dir)):
                os.makedirs(inp_dir, exist_ok=True)
                try:
                    shutil.copy2(cand_path, os.path.join(inp_dir, unique_name))
                except Exception:
                    pass
        edit_image_name = unique_name

P = {
 "1": {"class_type": "UNETLoader", "inputs": {"unet_name": a.unet, "weight_dtype": "default"}},
 "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": a.clip, "type": "flux2", "device": "default"}},
 "3": {"class_type": "VAELoader", "inputs": {"vae_name": "flux2-vae.safetensors"}},
 "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": a.prompt}},
 "5": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}},
 "7": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
 "8": {"class_type": "RandomNoise", "inputs": {"noise_seed": a.seed}},
 "13": {"class_type": "SaveImage", "inputs": {"images": ["12", 0], "filename_prefix": "flux2_test/" + a.out_name}},
}
if edit_image_name:
    P.update({
     "20": {"class_type": "LoadImage", "inputs": {"image": edit_image_name}},
     "21": {"class_type": "ImageScaleToTotalPixels", "inputs": {"image": ["20", 0], "upscale_method": "lanczos", "megapixels": round(a.w * a.h / 1e6, 2), "resolution_steps": 16}},
     "22": {"class_type": "GetImageSize", "inputs": {"image": ["21", 0]}},
     "23": {"class_type": "VAEEncode", "inputs": {"pixels": ["21", 0], "vae": ["3", 0]}},
     "24": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["4", 0], "latent": ["23", 0]}},
     "25": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["5", 0], "latent": ["23", 0]}},
    })
    W, H, pos, neg = ["22", 0], ["22", 1], ["24", 0], ["25", 0]
else:
    W, H, pos, neg = a.w, a.h, ["4", 0], ["5", 0]
P.update({
 "6": {"class_type": "CFGGuider", "inputs": {"model": ["1", 0], "positive": pos, "negative": neg, "cfg": 1.0}},
 "9": {"class_type": "Flux2Scheduler", "inputs": {"steps": a.steps, "width": W, "height": H}},
 "10": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": W, "height": H, "batch_size": 1}},
 "11": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["8", 0], "guider": ["6", 0], "sampler": ["7", 0], "sigmas": ["9", 0], "latent_image": ["10", 0]}},
 "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
})
peak = {"mem": 0}; stop = False
def mon():
    while not stop:
        try: peak["mem"] = max(peak["mem"], int(subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10).stdout.strip()))
        except Exception: pass
        time.sleep(1)
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
def req(path, data=None):
    r = urllib.request.Request(URL + path, data=json.dumps(data).encode() if data is not None else None, headers={"Content-Type": "application/json"})
    return json.loads(_NO_PROXY_OPENER.open(r, timeout=60).read())
threading.Thread(target=mon, daemon=True).start(); t0 = time.time()
try: pid = req("/prompt", {"prompt": P, "client_id": str(uuid.uuid4())})["prompt_id"]
except urllib.error.HTTPError as e: print("SUBMIT_ERROR", e.read().decode()[:3000]); sys.exit(2)
while True:
    time.sleep(1); h = req("/history/" + pid)
    if pid in h: break
stop = True; st = h[pid]["status"]; files = []
for out in h[pid].get("outputs", {}).values():
    for f in out.get("images", []) or []:
        for out_root in (r"D:\0_LLM\ComfyUI\output", r"D:\0_LTX\runtime\comfyui\output"):
            cand_out = os.path.join(out_root, f.get("subfolder", ""), f["filename"])
            if os.path.isfile(cand_out):
                files.append(cand_out)
                break
        else:
            files.append(os.path.join(r"D:\0_LLM\ComfyUI\output", f.get("subfolder", ""), f["filename"]))

os.makedirs(OUT, exist_ok=True)
os.makedirs(DEFAULT_OUT, exist_ok=True)
out_png_path = os.path.join(OUT, a.out_name + ".png")
for f in files:
    if os.path.isfile(f):
        shutil.copy2(f, out_png_path)
        if os.path.normcase(os.path.abspath(OUT)) != os.path.normcase(os.path.abspath(DEFAULT_OUT)):
            try:
                shutil.copy2(f, os.path.join(DEFAULT_OUT, a.out_name + ".png"))
            except Exception:
                pass
res = {"name": a.out_name, "w": a.w, "h": a.h, "steps": a.steps, "seed": a.seed, "edit": a.edit_image, "out_file": out_png_path, "status": st.get("status_str"), "wall_s": round(time.time() - t0, 1), "peak_vram_mib": peak["mem"], "files": files}
if st.get("status_str") != "success": res["error"] = [m for m in st.get("messages", []) if m[0] == "execution_error"]
open(os.path.join(OUT, "results.jsonl"), "a", encoding="utf-8").write(json.dumps(res)[:4000] + "\n")
print(json.dumps(res)[:4000])
