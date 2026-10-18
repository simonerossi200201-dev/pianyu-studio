# Find the frame offset of a clip inside the original (per-frame luma signature). No shell=True.
import sys, subprocess, numpy as np
ff = r"D:\0_Dev\CUTORA\runtime\ffmpeg\ffmpeg.exe"
def sig(p, ss=None, t=None):
    cmd = [ff, "-v", "error"] + (["-ss", str(ss)] if ss is not None else []) + ["-i", p] + (["-t", str(t)] if t else []) + \
          ["-vf", "fps=30,scale=24:32", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    n = len(raw) // 768; return np.frombuffer(raw[:n * 768], np.uint8).reshape(n, 768).astype(np.float32)
A = sig(sys.argv[1], 17, 21); B = sig(sys.argv[2])
A -= A.mean(1, keepdims=True); B -= B.mean(1, keepdims=True)
best = []
for off in range(0, len(A) - len(B) + 1):
    d = np.mean(np.abs(A[off:off + len(B)] - B)); best.append((d, off))
best.sort(); d, off = best[0]
print("best start = %.3f s (offset %d frames from 17s), mean abs diff %.2f; next best %.2f" % (17 + off / 30, off, d, best[1][0]))
