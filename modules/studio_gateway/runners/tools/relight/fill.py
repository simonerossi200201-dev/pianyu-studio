# -*- coding: utf-8 -*-
"""fill.py - broadcast-style face FILL light (v5b), ratio-image method on luminance only.
Method (after Zhang et al., SIGGRAPH 2020 'Portrait Shadow Manipulation', ratio-image idea):
 1 MediaPipe mesh normals (from face_pass.py), One-Euro smoothed over time
 2 least-squares 2nd-order SH (9 coeffs) lighting fit on skin pixels only -> S_old, EMA a=0.15
 3 S_new = S_old + k*max(0, n.d_fill), d_fill mirrored from the fitted key (horizontal, a bit frontal)
 4 R = clamp(S_new/S_old, 1, 1.5)   -> only lifts shadows, never darkens
 5 guided filter of R by luminance, slight blur (subsurface look)
 6 temporal blend with previous R warped by DIS optical flow (0.7), reset where flow inconsistent
 7 feathered MediaPipe+SAM-free face mask; R ~ 1 on eyes/lenses/lips
 Y_out = Y*R applied as one gain on R,G,B in linear light -> chroma unchanged.
Deterministic, offline, no shell=True / eval."""
import argparse, json, os, re, subprocess, time
import numpy as np, cv2

ap = argparse.ArgumentParser()
ap.add_argument("input"); ap.add_argument("faces"); ap.add_argument("out")
ap.add_argument("--k", type=float, default=0.2); ap.add_argument("--rmax", type=float, default=1.5)
ap.add_argument("--soft", type=float, default=0.5, help="lerp S_old toward its blur (shadow edge softening)")
ap.add_argument("--gain", type=float, default=1.0, help="global linear exposure gain 0.5-2 (soft shoulder)")
ap.add_argument("--ffmpeg", default=r"D:\0_Dev\CUTORA\runtime\ffmpeg\ffmpeg.exe")
a = ap.parse_args()
a.k = float(np.clip(a.k, 0, 0.5)); a.rmax = float(np.clip(a.rmax, 1, 2)); a.soft = float(np.clip(a.soft, 0, 1)); a.gain = float(np.clip(a.gain, 0.5, 2))
for p in (a.input, a.faces, a.out):
    if re.search(r"[\"'`;|&<>$%*?\n\r]", p): raise SystemExit("unsafe path")
if os.path.abspath(a.out).lower() == os.path.abspath(a.input).lower(): raise SystemExit("refusing to overwrite input")
ffprobe = os.path.join(os.path.dirname(a.ffmpeg), "ffprobe.exe")
s = json.loads(subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
               "stream=width,height,r_frame_rate", "-of", "json", a.input], capture_output=True, text=True).stdout)["streams"][0]
W, H = int(s["width"]), int(s["height"]); num, den = s["r_frame_rate"].split("/"); fps = float(num) / float(den)
Z = np.load(a.faces); FN, FA, FL, FLP = Z["normals"], Z["alpha"], Z["lens"], Z["lips"]
h4, w4 = FA.shape[1:]
w2 = 360; h2 = int(round(H * w2 / W))  # low working res; R is guided-upsampled to full res

def srgb_to_lin(x): return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
def lin_to_srgb(x):
    x = np.clip(x, 0, 1); return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)
def sh9(n):
    x, y, z = n[..., 0], n[..., 1], n[..., 2]
    return np.stack([np.ones_like(x), x, y, z, x * y, x * z, y * z, x * x - y * y, 3 * z * z - 1], -1)
def guided_coeffs(I, p, r, eps):
    bf = lambda t: cv2.boxFilter(t, -1, (2 * r + 1, 2 * r + 1))
    mI, mp_, cIp, vI = bf(I), bf(p), bf(I * p), bf(I * I)
    A = (cIp - mI * mp_) / (vI - mI * mI + eps); B = mp_ - A * mI
    return bf(A), bf(B)
def guided(I, p, r, eps):
    A, B = guided_coeffs(I, p, r, eps); return A * I + B

LUT_IN = srgb_to_lin(np.arange(256) / 255.0).astype(np.float32)
LUT_OUT = (lin_to_srgb(np.arange(4096) / 4095.0) * 255 + 0.5).astype(np.uint8)
dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM) if hasattr(cv2, "DISOpticalFlow_create") else None
rd = [a.ffmpeg, "-v", "error", "-i", a.input, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
tmp = a.out + ".video.mp4"
wr = [a.ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (W, H), "-r", "%.6f" % fps,
      "-i", "-", "-c:v", "libx264", "-crf", "16", "-preset", "medium", "-pix_fmt", "yuv420p", tmp]
pr = subprocess.Popen(rd, stdout=subprocess.PIPE); pw = subprocess.Popen(wr, stdin=subprocess.PIPE)
coef = None; n_f = None; dn_f = None; prevR = None; prevG = None; i = 0; t0 = time.time(); Rstats = []
while True:
    buf = pr.stdout.read(W * H * 3)
    if len(buf) < W * H * 3: break
    u8 = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
    lin = LUT_IN[u8]
    Y = 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]
    Y2 = cv2.resize(Y, (w2, h2), interpolation=cv2.INTER_AREA)
    gray2 = (np.clip(lin_to_srgb(Y2), 0, 1) * 255).astype(np.uint8)
    R = np.ones((h2, w2), np.float32)
    k = min(i, len(FA) - 1)
    fa = cv2.resize(FA[k].astype(np.float32), (w2, h2))
    if fa.max() > 0.5:
        # (1) One-Euro smoothing of the normal map (adaptive: smooth when still, follow when moving)
        n = cv2.resize(FN[k].astype(np.float32), (w2, h2))
        if n_f is None: n_f, dn_f = n, np.zeros_like(n)
        else:
            dn = (n - n_f); dn_f = 0.7 * dn_f + 0.3 * dn
            cutoff = 1.0 + 0.5 * np.abs(dn_f).mean() * fps
            al = 1 / (1 + fps / (2 * np.pi * cutoff))
            n_f = n_f + al * (n - n_f)
        n = n_f / (np.linalg.norm(n_f, axis=2, keepdims=True) + 1e-6)
        excl = np.clip(cv2.resize(FL[k].astype(np.float32), (w2, h2)) + cv2.resize(FLP[k].astype(np.float32), (w2, h2)), 0, 1)
        excl = cv2.GaussianBlur(cv2.dilate(excl, np.ones((7, 7), np.uint8)), (0, 0), 3)
        skin = (fa > 0.8) & (excl < 0.1)
        ys, xs = np.nonzero(fa > 0.5); fw = max(8, xs.max() - xs.min())
        # (2) SH fit on skin, EMA on coefficients
        if skin.sum() > 200:
            B = sh9(n[skin]); yv = cv2.GaussianBlur(Y2, (0, 0), fw * 0.01 + 0.5)[skin]
            c = np.linalg.lstsq(B, yv, rcond=None)[0]
            coef = c if coef is None else 0.85 * coef + 0.15 * c
        if coef is not None:
            S_old = np.maximum(sh9(n) @ coef, 1e-3)
            if a.soft > 0:
                S_old = (1 - a.soft) * S_old + a.soft * cv2.GaussianBlur(S_old, (0, 0), 0.015 * fw + 0.5)
            key = coef[1:4] / (np.linalg.norm(coef[1:4]) + 1e-6)          # fitted key direction (x,y,z)
            d = np.array([-np.sign(key[0]) * 0.85, 0.0, 0.5], np.float32); d /= np.linalg.norm(d)  # mirrored fill
            S_new = S_old + a.k * np.maximum(0, n @ d) * float(np.median(S_old[skin]) if skin.any() else S_old.mean())
            # (4) only lift
            Rf = np.clip(S_new / S_old, 1.0, a.rmax)
            # (7) mask: feathered face, R->1 on eyes/lenses/lips
            m = cv2.GaussianBlur(fa, (0, 0), max(15, 0.04 * fw) / 2.5) * (1 - excl)
            R = 1 + (Rf - 1) * m
            # (5) guided filter by luminance (radius ~3% face), slight blur
            r = max(2, int(0.03 * fw)); I = gray2.astype(np.float32) / 255
            R = guided(I, R.astype(np.float32), r, 1e-3); R = cv2.GaussianBlur(R, (0, 0), 0.01 * fw + 0.5)
            R = np.clip(R, 1.0, a.rmax)
    # (6) temporal blend with flow-warped previous R
    if prevR is not None:
        if dis is not None:
            fl = dis.calc(gray2, prevG, None)          # current -> previous
            bl = dis.calc(prevG, gray2, None)
            gx, gy = np.meshgrid(np.arange(w2, dtype=np.float32), np.arange(h2, dtype=np.float32))
            mx, my = gx + fl[..., 0], gy + fl[..., 1]
            warped = cv2.remap(prevR, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            back = cv2.remap(bl, mx, my, cv2.INTER_LINEAR)
            err = np.hypot(fl[..., 0] + back[..., 0], fl[..., 1] + back[..., 1])
            wgt = 0.7 * (err < 1.0)
        else:
            warped, wgt = prevR, 0.7
        R = wgt * warped + (1 - wgt) * R
    prevR, prevG = R.astype(np.float32), gray2
    Rstats.append(float(R.max()))
    # fast guided upsample: coefficients from low-res (guide = low-res luma), applied with full-res luma
    if R.max() > 1.0005:
        Il = gray2.astype(np.float32) / 255
        A, B = guided_coeffs(Il, R.astype(np.float32), 2, 1e-3)
        Ifull = cv2.cvtColor(u8, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255
        Rf = cv2.resize(A, (W, H)) * Ifull + cv2.resize(B, (W, H))
        Rf = np.clip(Rf, 1.0, a.rmax)
    else:
        Rf = np.ones((H, W), np.float32)
    Yl = lin[..., 0] * 0.2126 + lin[..., 1] * 0.7152 + lin[..., 2] * 0.0722
    hp = np.clip((Yl - 0.45) / 0.4, 0, 1)                # v6g: no exposure gain on highlights/lamp (flicker)
    out = lin * (Rf * (1 + (a.gain - 1) * (1 - hp)))[..., None]  # one gain for RGB -> chroma unchanged
    out = out / (1 + np.maximum(out - 0.9, 0) * 2)  # never clip highlights
    pw.stdin.write(LUT_OUT[(np.clip(out, 0, 1) * 4095).astype(np.int32)].tobytes()); i += 1
    if i % 60 == 0: print("frame %d %.2f fps  max R %.3f" % (i, i / (time.time() - t0), R.max()), flush=True)
pw.stdin.close(); pw.wait(); pr.stdout.close(); pr.wait()
subprocess.run([a.ffmpeg, "-v", "error", "-y", "-i", tmp, "-i", a.input, "-map", "0:v", "-map", "1:a?", "-c:v", "copy",
                "-c:a", "copy", "-shortest", a.out], check=True)
os.remove(tmp)
print("done %d frames %.1fs, R max mean %.3f, DIS=%s" % (i, time.time() - t0, np.mean(Rstats), dis is not None))
