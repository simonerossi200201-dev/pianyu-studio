# 100% crops (orig | light | medium) of mouth, cheek, hair edge, wall + temporal noise on the wall. No shell=True.
import sys, subprocess, numpy as np, cv2
ff = r"D:\0_Dev\CUTORA\runtime\ffmpeg\ffmpeg.exe"
A, L, M, npz, outp = sys.argv[1:6]
z = np.load(npz)["box"]
def frame(p, t):
    r = subprocess.run([ff, "-v", "error", "-ss", "%.3f" % t, "-i", p, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8).reshape(1440, 1080, 3)
rows = []
for t in (2.0, 5.0, 8.0, 11.0, 13.5):
    fs = [frame(p, t) for p in (A, L, M)]; x0, y0, x1, y1 = z[min(int(t * 30), len(z) - 1)]; fw = x1 - x0; fh = y1 - y0
    boxes = {"mouth": (x0 + 0.5 * fw, y0 + 0.78 * fh), "cheek": (x0 + 0.22 * fw, y0 + 0.6 * fh), "hair": (x0 - 0.05 * fw, y0 + 0.1 * fh), "wall": (900, 250)}
    for k, (cx, cy) in boxes.items():
        cx, cy = int(np.clip(cx - 110, 0, 1080 - 220)), int(np.clip(cy - 110, 0, 1440 - 220))
        row = np.hstack([cv2.copyMakeBorder(f[cy:cy + 220, cx:cx + 220], 2, 2, 2, 2, 0) for f in fs])
        cv2.putText(row, "%s %.1fs" % (k, t), (6, 18), 0, 0.5, (255, 255, 255), 1); rows.append(row)
for i in range(0, len(rows), 4): cv2.imwrite(outp + "_%d.jpg" % (i // 4), np.vstack(rows[i:i + 4]), [cv2.IMWRITE_JPEG_QUALITY, 95])
def tnoise(p):  # temporal std on a static wall patch, after removing slow drift
    r = subprocess.run([ff, "-v", "error", "-ss", "3", "-t", "2", "-i", p, "-vf", "crop=120:120:900:150", "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True)
    v = np.frombuffer(r.stdout, np.uint8).reshape(-1, 120, 120).astype(np.float32)
    return float((v[1:] - v[:-1]).std() / np.sqrt(2))
print("temporal noise (wall): orig %.2f  light %.2f  medium %.2f" % tuple(tnoise(p) for p in (A, L, M)))
