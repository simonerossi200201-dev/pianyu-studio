# -*- coding: utf-8 -*-
"""denoise.py - motion-compensated temporal denoiser (OpenCV only, no AI).
For every frame: align the +-R neighbours with DIS optical flow (half-res, both directions),
reject pixels where the forward-backward flow disagrees or the warped neighbour differs from the
centre by more than a few noise sigmas (occlusion / mouth / hands -> no ghosting), and average the rest.
Then an NL-means spatial pass: chroma-focused, optional gentle luma. Gentler inside the face mask so skin texture stays.
No shell=True, no eval; ffmpeg via argument lists."""
import argparse, os, subprocess, time
from collections import deque
import numpy as np, cv2

ap = argparse.ArgumentParser()
ap.add_argument("input"); ap.add_argument("out")
ap.add_argument("--radius", type=int, default=2, help="neighbours each side (1-3)")
ap.add_argument("--strength", type=float, default=1.0, help="neighbour weight 0-1")
ap.add_argument("--thr", type=float, default=2.5, help="reject when |diff| > thr * noise sigma")
ap.add_argument("--chroma", type=float, default=0.6, help="chroma NL-means strength 0-1")
ap.add_argument("--luma-nl", type=float, default=0.0, help="NL-means h on luma (0=off, 2-4 gentle)")
ap.add_argument("--face-npz", default="", help="faces.npz (alpha) from face_pass.py")
ap.add_argument("--face-strength", type=float, default=0.5, help="multiplier inside the face")
ap.add_argument("--ffmpeg", default=r"D:\0_Dev\CUTORA\runtime\ffmpeg\ffmpeg.exe")
ap.add_argument("--fps", type=float, default=30.0); ap.add_argument("--seconds", type=float, default=0)
a = ap.parse_args()
a.radius = int(np.clip(a.radius, 1, 3)); a.strength = float(np.clip(a.strength, 0, 1))
fp = a.ffmpeg.replace("ffmpeg.exe", "ffprobe.exe")
W, H = [int(v) for v in subprocess.run([fp, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                                         "-of", "csv=p=0", a.input], capture_output=True, text=True, check=True).stdout.strip().split(",")[:2]]
FA = np.load(a.face_npz)["alpha"] if a.face_npz else None
dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
h2, w2 = H // 2, W // 2
gx, gy = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32))

def flow(g0, g1):  # half-res flow, upsampled to full res (pixels)
    f = dis.calc(g0, g1, None)
    return cv2.resize(f, (W, H)) * 2.0

def sigma_of(Y):  # robust noise sigma from the finest Laplacian band
    l = cv2.Laplacian(Y, cv2.CV_32F, ksize=1)[::2, ::2]
    return float(np.median(np.abs(l)) / 0.6745 / 2.0) + 1e-3

rd = [a.ffmpeg, "-v", "error"] + (["-t", "%.3f" % a.seconds] if a.seconds > 0 else []) + ["-i", a.input, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
tmp = a.out + ".tmp.mp4"
wr = [a.ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (W, H), "-r", "%g" % a.fps, "-i", "-",
      "-c:v", "libx264", "-crf", "15", "-preset", "medium", "-pix_fmt", "yuv420p", tmp]
pr = subprocess.Popen(rd, stdout=subprocess.PIPE); pw = subprocess.Popen(wr, stdin=subprocess.PIPE)
buf = deque(); idx = 0; nxt = 0; done = False; t0 = time.time(); rej = []

def read():
    b = pr.stdout.read(W * H * 3)
    if len(b) < W * H * 3: return None
    f = np.frombuffer(b, np.uint8).reshape(H, W, 3).astype(np.float32)
    g = cv2.resize(cv2.cvtColor(f.astype(np.uint8), cv2.COLOR_RGB2GRAY), (w2, h2), interpolation=cv2.INTER_AREA)
    return f, g

def process(c, frames):  # frames: dict offset -> (f, g)
    f0, g0 = frames[0]
    Y0 = cv2.cvtColor(f0, cv2.COLOR_RGB2GRAY)
    sig = sigma_of(Y0)
    fs = 1.0
    if FA is not None:
        fa = cv2.resize(FA[min(c, len(FA) - 1)].astype(np.float32), (W, H))
        fs = 1 - (1 - a.face_strength) * np.clip(fa, 0, 1)
    acc = f0.copy(); wsum = np.ones((H, W), np.float32); r_all = []
    for o, (f1, g1) in frames.items():
        if o == 0: continue
        fw = flow(g0, g1); bw = flow(g1, g0)
        mx, my = gx + fw[..., 0], gy + fw[..., 1]
        warped = cv2.remap(f1, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        bwx = cv2.remap(bw, mx, my, cv2.INTER_LINEAR)                       # forward-backward check
        fb = np.hypot(fw[..., 0] + bwx[..., 0], fw[..., 1] + bwx[..., 1])
        d = cv2.GaussianBlur(np.abs(cv2.cvtColor(warped, cv2.COLOR_RGB2GRAY) - Y0), (0, 0), 1.2)
        dc = cv2.GaussianBlur(np.abs(warped - f0).max(2), (0, 0), 1.2)       # colour diff (lips / hands)
        w = np.exp(-0.5 * (d / (a.thr * sig)) ** 2) * np.exp(-0.5 * (dc / (a.thr * 1.6 * sig)) ** 2)
        w *= np.clip(1.5 - fb, 0, 1)                                         # >1.5 px disagreement -> reject
        w = cv2.erode(w, np.ones((3, 3), np.uint8))                          # conservative at edges
        w *= a.strength * fs * (1.0 if abs(o) == 1 else 0.7)
        acc += warped * w[..., None]; wsum += w; r_all.append(float((w < 0.1 * a.strength).mean()))
    out = acc / wsum[..., None]
    if a.chroma > 0 or a.luma_nl > 0:  # spatial pass: NL-means, chroma-focused (half-res Cr/Cb), gentle luma
        ycc = cv2.cvtColor(np.clip(out + 0.5, 0, 255).astype(np.uint8), cv2.COLOR_RGB2YCrCb)
        res = ycc.astype(np.float32)
        if a.chroma > 0:
            for k in (1, 2):
                ch = cv2.resize(ycc[..., k], (w2, h2), interpolation=cv2.INTER_AREA)
                sm = cv2.resize(cv2.fastNlMeansDenoising(ch, None, h=a.chroma * 6.0, templateWindowSize=5, searchWindowSize=15),
                                (W, H), interpolation=cv2.INTER_CUBIC).astype(np.float32)
                # keep fine chroma edges: only replace the low-amplitude (noise) part of the difference
                dlt = sm - res[..., k]; res[..., k] += dlt * np.clip(1.5 - np.abs(dlt) / 6.0, 0, 1)
        if a.luma_nl > 0:
            sl = cv2.fastNlMeansDenoising(ycc[..., 0], None, h=a.luma_nl, templateWindowSize=5, searchWindowSize=13).astype(np.float32)
            res[..., 0] += (sl - res[..., 0]) * fs * 0.8  # face: fs<1 -> gentler, keeps skin texture
        out = cv2.cvtColor(np.clip(res + 0.5, 0, 255).astype(np.uint8), cv2.COLOR_YCrCb2RGB).astype(np.float32)
    rej.append(np.mean(r_all) if r_all else 0)
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)

frames = []  # simple sliding window over a list of (f, g)
while True:
    # fill window
    while not done and len(frames) <= idx + a.radius:
        r = read()
        if r is None: done = True; break
        frames.append(r)
    if idx >= len(frames): break
    win = {o: frames[idx + o] for o in range(-a.radius, a.radius + 1) if 0 <= idx + o < len(frames)}
    pw.stdin.write(process(idx, win).tobytes())
    if idx - a.radius >= 0: frames[idx - a.radius] = None  # free memory
    idx += 1
    if idx % 60 == 0: print("frame %d  %.2f fps  rejected %.1f%%" % (idx, idx / (time.time() - t0), 100 * np.mean(rej[-60:])), flush=True)
pr.stdout.close(); pr.wait(); pw.stdin.close(); pw.wait()
subprocess.run([a.ffmpeg, "-v", "error", "-y", "-i", tmp, "-i", a.input, "-map", "0:v", "-map", "1:a?", "-c:v", "copy",
                "-c:a", "copy", "-shortest", a.out], check=True)
os.remove(tmp)
el = time.time() - t0
print("done %d frames in %.1fs (%.2f fps), mean rejected %.1f%%" % (idx, el, idx / el, 100 * np.mean(rej)))
