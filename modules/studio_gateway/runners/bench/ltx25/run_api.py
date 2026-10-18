# Generic ComfyUI API runner: submit a prompt JSON, wait, record wall time + peak VRAM, copy outputs.
import json, sys, time, uuid, urllib.request, subprocess, threading, shutil, os
P = json.load(open(sys.argv[1], encoding="utf-8")); tag = sys.argv[2]
URL = "http://127.0.0.1:8188"; peak = {"mem": 0, "pw": 0.0, "sm": 0}; stop = False
def mon():
    while not stop:
        try:
            o = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,power.draw,clocks.sm", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10).stdout.strip().split(",")
            peak["mem"] = max(peak["mem"], int(float(o[0]))); peak["pw"] = max(peak["pw"], float(o[1])); peak["sm"] = max(peak["sm"], int(float(o[2])))
        except Exception: pass
        time.sleep(2)
def req(path, data=None):
    r = urllib.request.Request(URL + path, data=json.dumps(data).encode() if data is not None else None, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=60).read())
threading.Thread(target=mon, daemon=True).start(); t0 = time.time()
try: pid = req("/prompt", {"prompt": P, "client_id": str(uuid.uuid4())})["prompt_id"]
except urllib.error.HTTPError as e: print("SUBMIT_ERROR", e.read().decode()[:4000]); sys.exit(2)
while True:
    time.sleep(10); h = req("/history/" + pid)
    if pid in h: break
stop = True; st = h[pid]["status"]; files = []
for nid, out in h[pid].get("outputs", {}).items():
    for k in ("images", "videos", "gifs", "files"):
        for f in out.get(k, []) or []: files.append(os.path.join(r"D:\0_LLM\ComfyUI\output", f.get("subfolder", ""), f["filename"]))
res = {"tag": tag, "status": st.get("status_str"), "wall_s": round(time.time() - t0, 1), "peak_vram_mib": peak["mem"], "peak_power_w": peak["pw"], "peak_sm_mhz": peak["sm"], "files": files}
if st.get("status_str") != "success": res["error"] = [m for m in st.get("messages", []) if m[0] == "execution_error"]
for f in files:
    if os.path.exists(f): shutil.copy2(f, r"D:\0_LLM\Outputs\ltx25_test")
open(r"D:\0_LLM\Outputs\ltx25_test\results.jsonl", "a", encoding="utf-8").write(json.dumps(res)[:6000] + "\n")
print(json.dumps(res)[:6000])
