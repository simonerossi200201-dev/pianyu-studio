# Official LTX-2.3 Relight IC-LoRA graph (LTX-2.3_Relight_ICLoRA_SingleStage_Distilled.json) converted to API form.
# Deviations: SphereLightNode (third-party Sphere-Light-Render, not installed) replaced by the ball PNG rendered by that node and
# embedded in the official workflow (rot 149.7, el 15.4 => "back-right, low sun"); nvfp4 checkpoint + Comfy-Org gemma fp4_mixed;
# side-by-side via ffmpeg instead of KJNodes ImageConcanate. Talks only to 127.0.0.1:8188.
import json, sys, time, urllib.request, subprocess, threading, argparse, os, shutil
a = argparse.ArgumentParser()
a.add_argument("--video", required=True); a.add_argument("--frames", type=int, default=121)
a.add_argument("--prompt", default="relight the video to match the light-direction ball. hard low-angle sunlight from the back-right, low sun")
a.add_argument("--ball", default="relight_ball_backright_low.png"); a.add_argument("--validate-only", action="store_true")
a.add_argument("--width", type=int, default=1280); a.add_argument("--height", type=int, default=704)
a.add_argument("--prefix", default="ltx_vfx/relight_official")
A = a.parse_args()
CK = "ltx-2.3-22b-dev-nvfp4.safetensors"
W = {
 "ckpt": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CK}},
 "avae": {"class_type": "LTXVAudioVAELoader", "inputs": {"ckpt_name": CK}},
 "dlora": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["ckpt", 0], "lora_name": "ltx-2.3-22b-distilled-lora-384-1.1.safetensors", "strength_model": 0.5}},
 "iclora": {"class_type": "LTXICLoRALoaderModelOnly", "inputs": {"model": ["dlora", 0], "lora_name": "ltx-2.3-22b-ic-lora-relight-1.0.safetensors", "strength_model": 1.0}},
 "te": {"class_type": "LTXAVTextEncoderLoader", "inputs": {"text_encoder": "gemma_3_12B_it_fp4_mixed.safetensors", "ckpt_name": CK, "device": "default"}},
 "pos": {"class_type": "CLIPTextEncode", "inputs": {"text": A.prompt, "clip": ["te", 0]}},
 "neg": {"class_type": "CLIPTextEncode", "inputs": {"text": "pc game, console game, video game, cartoon, childish, ugly, blurry, deformed\n", "clip": ["te", 0]}},
 "cond": {"class_type": "LTXVConditioning", "inputs": {"positive": ["pos", 0], "negative": ["neg", 0], "frame_rate": 24.0}},
 "lv": {"class_type": "LoadVideo", "inputs": {"file": A.video}},
 "slice": {"class_type": "Video Slice", "inputs": {"video": ["lv", 0], "start_time": 0.0, "duration": A.frames / 24.0, "strict_duration": False}},
 "gvc": {"class_type": "GetVideoComponents", "inputs": {"video": ["slice", 0]}},
 "fit": {"class_type": "ImageScale", "inputs": {"image": ["gvc", 0], "upscale_method": "lanczos", "width": A.width, "height": A.height, "crop": "center"}},
 "ball": {"class_type": "LoadImage", "inputs": {"image": A.ball}},
 "ball143": {"class_type": "ImageScale", "inputs": {"image": ["ball", 0], "upscale_method": "area", "width": 143, "height": 143, "crop": "disabled"}},
 "comp": {"class_type": "ImageCompositeMasked", "inputs": {"destination": ["fit", 0], "source": ["ball143", 0], "x": A.width - 143 - 22, "y": 22, "resize_source": False}},
 "empty": {"class_type": "EmptyLTXVLatentVideo", "inputs": {"width": A.width, "height": A.height, "length": A.frames, "batch_size": 1}},
 "guide": {"class_type": "LTXAddVideoICLoRAGuide", "inputs": {"positive": ["cond", 0], "negative": ["cond", 1], "vae": ["ckpt", 2], "latent": ["empty", 0], "image": ["comp", 0],
           "frame_idx": 0, "strength": 1.0, "latent_downscale_factor": ["iclora", 1], "crop": "disabled", "use_tiled_encode": False, "tile_size": 256, "tile_overlap": 64}},
 "alat": {"class_type": "LTXVEmptyLatentAudio", "inputs": {"frames_number": A.frames, "frame_rate": 24, "batch_size": 1, "audio_vae": ["avae", 0]}},
 "cat": {"class_type": "LTXVConcatAVLatent", "inputs": {"video_latent": ["guide", 2], "audio_latent": ["alat", 0]}},
 "noise": {"class_type": "RandomNoise", "inputs": {"noise_seed": 42}},
 "cfg": {"class_type": "CFGGuider", "inputs": {"model": ["iclora", 0], "positive": ["guide", 0], "negative": ["guide", 1], "cfg": 1.0}},
 "ks": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler_ancestral_cfg_pp"}},
 "sig": {"class_type": "ManualSigmas", "inputs": {"sigmas": "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"}},
 "samp": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["noise", 0], "guider": ["cfg", 0], "sampler": ["ks", 0], "sigmas": ["sig", 0], "latent_image": ["cat", 0]}},
 "sep": {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["samp", 0]}},
 "crop": {"class_type": "LTXVCropGuides", "inputs": {"positive": ["guide", 0], "negative": ["guide", 1], "latent": ["sep", 0]}},
 "dec": {"class_type": "LTXVTiledVAEDecode", "inputs": {"vae": ["ckpt", 2], "latents": ["crop", 2], "horizontal_tiles": 2, "vertical_tiles": 2, "overlap": 6, "last_frame_fix": False, "working_device": "auto", "working_dtype": "auto"}},
 "cv": {"class_type": "CreateVideo", "inputs": {"images": ["dec", 0], "fps": 24.0, "bit_depth": 8}},
 "save": {"class_type": "SaveVideo", "inputs": {"video": ["cv", 0], "filename_prefix": A.prefix, "format": "mp4", "codec": "h264"}},
 "cvref": {"class_type": "CreateVideo", "inputs": {"images": ["comp", 0], "fps": 24.0, "bit_depth": 8}},
 "saveref": {"class_type": "SaveVideo", "inputs": {"video": ["cvref", 0], "filename_prefix": A.prefix + "_ref", "format": "mp4", "codec": "h264"}},
}
here = os.path.dirname(os.path.abspath(__file__))
json.dump(W, open(os.path.join(here, "api_relight_ltx23_official.json"), "w"), indent=1)
U = "http://127.0.0.1:8188"
def post(p, d): return json.load(urllib.request.urlopen(urllib.request.Request(U + p, json.dumps(d).encode(), {"Content-Type": "application/json"})))
if A.validate_only:
    oi = json.load(urllib.request.urlopen(U + "/object_info"))
    miss = sorted({v["class_type"] for v in W.values() if v["class_type"] not in oi}); print("MISSING_NODES", miss); sys.exit(1 if miss else 0)
peak = [0]; stop = [False]
def mon():
    while not stop[0]:
        try: peak[0] = max(peak[0], int(subprocess.check_output(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"]).split()[0]))
        except Exception: pass
        time.sleep(1)
base = int(subprocess.check_output(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"]).split()[0])
threading.Thread(target=mon, daemon=True).start()
t0 = time.time(); r = post("/prompt", {"prompt": W}); pid = r["prompt_id"]; print("queued", pid, flush=True)
while True:
    h = json.load(urllib.request.urlopen(f"{U}/history/{pid}"))
    if pid in h and h[pid].get("status", {}).get("completed") is not None: break
    if pid in h and h[pid].get("status", {}).get("status_str") == "error": break
    time.sleep(3)
stop[0] = True; dt = time.time() - t0
st = h[pid]["status"]; print("STATUS", st.get("status_str"), f"time={dt:.1f}s", f"gpu_mem_before={base}MiB peak_gpu_mem={peak[0]}MiB", flush=True)
if st.get("status_str") == "error": print(json.dumps(st.get("messages"))[-3000:]); sys.exit(2)
outs = {k: v for k, v in h[pid]["outputs"].items()}
print(json.dumps(outs))
