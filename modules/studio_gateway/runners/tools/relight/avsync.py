# Audio offset between original (from 18 s) and result audio via cross-correlation. No shell=True.
import subprocess, sys, numpy as np
ff = r"D:\0_Dev\CUTORA\runtime\ffmpeg\ffmpeg.exe"
def pcm(args):
    p = subprocess.run([ff, "-v", "error"] + args + ["-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"], capture_output=True, check=True)
    return np.frombuffer(p.stdout, np.int16).astype(np.float32)
orig = pcm(["-ss", "18", "-t", "19", "-i", sys.argv[1]]); res = pcm(["-i", sys.argv[2]])
def env(x): x = np.abs(x); n = 80; return x[: len(x) // n * n].reshape(-1, n).mean(1)  # 10 ms envelope
eo, er = env(orig), env(res); eo -= eo.mean(); er -= er.mean()
c = np.correlate(eo, er, "valid"); k = int(c.argmax())
print("result audio starts at original %.3f s (corr peak %.3f, 2nd-best ratio %.2f)" % (18 + k * 0.01, c[k] / (np.linalg.norm(er) * np.linalg.norm(eo[k:k + len(er)]) + 1e-9), c[k] / np.sort(c)[-30]))
# per-segment check for drift
for s in range(0, len(er) - 300, 300):
    seg = er[s:s + 300]; cc = np.correlate(eo[max(0, k + s - 50):k + s + 350], seg, "valid"); print("seg %4.1fs offset %+d ms" % (s * 0.01, (int(cc.argmax()) - min(50, k + s)) * 10))
