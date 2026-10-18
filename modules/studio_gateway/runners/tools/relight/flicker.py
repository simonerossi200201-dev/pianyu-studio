# Frame-to-frame luminance stability on face and lamp regions: v3 vs v5. No shell=True.
import sys, subprocess, numpy as np
ff = r"D:\0_Dev\CUTORA\runtime\ffmpeg\ffmpeg.exe"
a_path, b_path, npz = sys.argv[1], sys.argv[2], sys.argv[3]
W, H = 270, 360
def frames(p):
    raw = subprocess.run([ff, "-v", "error", "-t", "15", "-i", p, "-vf", "scale=%d:%d" % (W, H), "-f", "rawvideo",
                          "-pix_fmt", "gray", "-"], capture_output=True).stdout
    n = len(raw) // (W * H); return np.frombuffer(raw[:n * W * H], np.uint8).reshape(n, H, W).astype(np.float32)
A, B = frames(a_path), frames(b_path); n = min(len(A), len(B)); A, B = A[:n], B[:n]
box = np.load(npz)["box"][:n] / 4.0  # 1080 -> 270
mA = A.mean(0); lamp = np.zeros_like(mA, bool); top = int(H * 0.15)
lamp[:top] = mA[:top] > min(235, np.percentile(mA[:top], 99.5) - 1)  # ceiling lamp: brightest part of the top band
ys, xs = np.nonzero(lamp)
def stats(F, name):
    face = np.array([F[i, int(max(0, b[1])):int(b[3]), int(max(0, b[0])):int(b[2])].mean() if b[0] >= 0 else np.nan
                     for i, b in enumerate(box)])
    if len(ys):
        y0, y1, x0, x1 = max(0, ys.min() - 15), ys.max() + 15, max(0, xs.min() - 15), xs.max() + 15
        lam = F[:, y0:y1, x0:x1].mean((1, 2))
    else:
        lam = np.full(n, np.nan)
    df, dl = np.abs(np.diff(face)), np.abs(np.diff(lam))
    print("%-4s face mean %.1f  |dY| mean %.2f p95 %.2f max %.2f   lamp+halo mean %.1f |dY| mean %.2f p95 %.2f" %
          (name, np.nanmean(face), np.nanmean(df), np.nanpercentile(df, 95), np.nanmax(df), np.nanmean(lam),
           np.nanmean(dl), np.nanpercentile(dl, 95)))
print("frames", n, "lamp pixels", int(lamp.sum()), "lamp bbox", (ys.min(), ys.max(), xs.min(), xs.max()) if len(ys) else None)
stats(A, "v3"); stats(B, "v5")
