# Before|after crops (face, glasses, hair) from a side-by-side mp4, boxes from faces.npz. No shell=True.
import sys, subprocess, numpy as np
sbs, npz, t, fps, W, outp = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4]), int(sys.argv[5]), sys.argv[6]
ff = r"D:\0_Dev\CUTORA\runtime\ffmpeg\ffmpeg.exe"
z = np.load(npz); i = min(int(t * fps), len(z["box"]) - 1); x0, y0, x1, y1 = z["box"][i]
fw, fh = x1 - x0, y1 - y0
boxes = {"face": (x0 - 0.25 * fw, y0 - 0.3 * fh, 1.5 * fw, 1.5 * fh),
         "glasses": (x0 - 0.05 * fw, y0 + 0.2 * fh, 1.1 * fw, 0.45 * fh),
         "hair": (x0 - 0.6 * fw, y0 - 0.35 * fh, 2.2 * fw, 1.6 * fh),
         "lips": (x0 + 0.15 * fw, y0 + 0.62 * fh, 0.7 * fw, 0.33 * fh),
         "top": (0, 0, W, 0.35 * (y1 + fh))}
for k, (bx, by, bw, bh) in boxes.items():
    bx, by = max(0, int(bx)), max(0, int(by)); bw, bh = int(min(bw, W - bx)) // 2 * 2, int(bh) // 2 * 2
    f = "[0:v]split[a][b];[a]crop=%d:%d:%d:%d[l];[b]crop=%d:%d:%d:%d[r];[l][r]hstack" % (bw, bh, bx, by, bw, bh, W + bx, by)
    subprocess.run([ff, "-v", "error", "-y", "-ss", "%.2f" % t, "-i", sbs, "-frames:v", "1", "-filter_complex", f,
                    outp + "_%s.jpg" % k], check=True)
print("crops ok")
