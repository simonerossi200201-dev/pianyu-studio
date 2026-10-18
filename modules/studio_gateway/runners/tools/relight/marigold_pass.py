# -*- coding: utf-8 -*-
"""marigold_pass.py - keyframe surface normals with Marigold-Normals v1-1 (ETH Zurich PRS lab;
code Apache-2.0, weights CreativeML OpenRAIL++-M, base Stable Diffusion 2 by Stability AI, UK).
Runs offline from a local folder. Writes (K,h,w,3) float16 normals in relight.py's image frame
(x right, y DOWN, z toward camera) + keyframe indices. No shell=True / eval."""
import argparse, os, subprocess, sys, time
os.environ["HF_HUB_OFFLINE"] = "1"; os.environ["TRANSFORMERS_OFFLINE"] = "1"; os.environ["DIFFUSERS_OFFLINE"] = "1"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "pydeps"))  # diffusers wheel installed here (--target), nothing else touched
import numpy as np, cv2, torch
from PIL import Image
from diffusers import MarigoldNormalsPipeline

ap = argparse.ArgumentParser()
ap.add_argument("input"); ap.add_argument("out")
ap.add_argument("--model", default=os.path.join(HERE, "models", "marigold-normals-v1-1"))
ap.add_argument("--ffmpeg", default=r"D:\0_Dev\CUTORA\runtime\ffmpeg\ffmpeg.exe")
ap.add_argument("--width", type=int, required=True); ap.add_argument("--height", type=int, required=True)
ap.add_argument("--seconds", type=float, default=0); ap.add_argument("--every", type=int, default=3)
ap.add_argument("--steps", type=int, default=4); ap.add_argument("--res", type=int, default=768)
ap.add_argument("--ensemble", type=int, default=1)
a = ap.parse_args()
W, H = a.width, a.height; w4, h4 = W // 4, H // 4
pipe = MarigoldNormalsPipeline.from_pretrained(a.model, variant="fp16", torch_dtype=torch.float16,
                                               local_files_only=True).to("cuda")
pipe.set_progress_bar_config(disable=True)
cmd = [a.ffmpeg, "-v", "error"] + (["-t", "%.3f" % a.seconds] if a.seconds > 0 else []) + \
      ["-i", a.input, "-vf", "scale=%d:%d" % (W, H), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
pr = subprocess.Popen(cmd, stdout=subprocess.PIPE)
out, idx = [], []; i = 0; t0 = time.time(); last = None
while True:
    buf = pr.stdout.read(W * H * 3)
    if len(buf) < W * H * 3: break
    last = (i, buf)
    if i % a.every == 0:
        img = Image.fromarray(np.frombuffer(buf, np.uint8).reshape(H, W, 3))
        g = torch.Generator("cuda").manual_seed(1234)  # fixed seed -> steadier keyframes
        with torch.inference_mode():
            n = pipe(img, num_inference_steps=a.steps, ensemble_size=a.ensemble, processing_resolution=a.res,
                     generator=g, output_type="np").prediction[0]
        n = cv2.resize(n.astype(np.float32), (w4, h4), interpolation=cv2.INTER_AREA)
        n[..., 1] *= -1  # Marigold: y up -> ours: y down
        n /= np.linalg.norm(n, axis=2, keepdims=True) + 1e-6
        out.append(n.astype(np.float16)); idx.append(i)
    i += 1
pr.stdout.close(); pr.wait()
np.savez_compressed(a.out, normals=np.stack(out), index=np.array(idx), frames=i)
print("keyframes %d of %d frames, %.1fs, peak VRAM %.0f MiB" % (len(idx), i, time.time() - t0,
      torch.cuda.max_memory_allocated() / 2**20))
