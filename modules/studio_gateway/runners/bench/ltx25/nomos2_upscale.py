# Nomos2 4x upscale of an LTX clip to 1080x1920 in chunks (RAM-safe), then mux original audio with ffmpeg.
import json, os, sys, time, glob, subprocess, threading, urllib.request, uuid
SRC = r"D:\0_LLM\ComfyUI\output\ltx25_test\i2v_576x1024_15s_00001_.mp4"
OUTDIR = r"D:\0_LLM\ComfyUI\output\nomos2_frames"; FINAL = r"D:\0_LLM\Outputs\ltx25_test\upscale_nomos2_1080p.mp4"
FF = glob.glob(r"D:\0_LLM\ComfyUI\venv\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg*.exe")[0]
URL = "http://127.0.0.1:8188"; CH = 40; TOTAL = 361
peak = {"mem": 0}; stop = False
def mon():
    while not stop:
        try:
            o = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10).stdout
            peak["mem"] = max(peak["mem"], int(o.strip()))
        except Exception: pass
        time.sleep(2)
def req(path, data=None):
    r = urllib.request.Request(URL + path, data=json.dumps(data).encode() if data is not None else None, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=60).read())
threading.Thread(target=mon, daemon=True).start()
t0 = time.time()
for k, skip in enumerate(range(0, TOTAL, CH)):
    P = {"1": {"class_type": "VHS_LoadVideoPath", "inputs": {"video": SRC, "force_rate": 0, "custom_width": 0, "custom_height": 0, "frame_load_cap": CH, "skip_first_frames": skip, "select_every_nth": 1, "format": "None"}},
         "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "4xNomos2_realplksr_dysample.pth"}},
         "3": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}},
         "4": {"class_type": "ImageScale", "inputs": {"image": ["3", 0], "upscale_method": "lanczos", "width": 1080, "height": 1920, "crop": "disabled"}},
         "5": {"class_type": "SaveImage", "inputs": {"images": ["4", 0], "filename_prefix": "nomos2_frames/c%02d" % k}}}
    try: pid = req("/prompt", {"prompt": P, "client_id": str(uuid.uuid4())})["prompt_id"]
    except urllib.error.HTTPError as e: print("SUBMIT_ERROR", e.read().decode()[:2000]); sys.exit(2)
    while True:
        time.sleep(3); h = req("/history/" + pid)
        if pid in h: break
    st = h[pid]["status"]["status_str"]; print("chunk", k, skip, st, round(time.time() - t0, 1), flush=True)
    if st != "success": print(json.dumps(h[pid]["status"])[:3000]); sys.exit(3)
t_up = time.time() - t0
frames = sorted(glob.glob(os.path.join(OUTDIR, "c*_*.png")))
lst = os.path.join(OUTDIR, "list.txt")
with open(lst, "w") as f:
    for p in frames: f.write("file '%s'\nduration %f\n" % (p.replace("\\", "/"), 1/24))
cmd = [FF, "-y", "-f", "concat", "-safe", "0", "-i", lst, "-i", SRC, "-map", "0:v", "-map", "1:a", "-r", "24", "-c:v", "libx264", "-crf", "16", "-preset", "slow", "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest", FINAL]
subprocess.run(cmd, check=True, capture_output=True)
stop = True
print(json.dumps({"frames": len(frames), "upscale_s": round(t_up, 1), "total_s": round(time.time() - t0, 1), "peak_vram_mib": peak["mem"], "out": FINAL}))
