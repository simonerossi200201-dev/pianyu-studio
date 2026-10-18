# Hair hue on a given clip vs v3 (dark hair beside the face, frames 2s/8s/12s). No shell=True.
import sys, subprocess, numpy as np
ff = r"D:\0_Dev\CUTORA\runtime\ffmpeg\ffmpeg.exe"
z = np.load(sys.argv[1])["box"]
for p in sys.argv[2:]:
    out = []
    for t in (2, 8, 12):
        b = z[int(t * 30)] / np.array([1080, 1440, 1080, 1440])
        raw = subprocess.run([ff, "-v", "error", "-ss", str(t), "-i", p, "-frames:v", "1", "-vf", "scale=576:768",
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        im = np.frombuffer(raw, np.uint8).reshape(768, 576, 3).astype(np.float32) / 255
        x0, y0, x1, y1 = (b * [576, 768, 576, 768]).astype(int); fw = x1 - x0
        reg = np.concatenate([im[y0:y1, max(0, x0 - fw // 2):x0].reshape(-1, 3), im[y0:y1, x1:x1 + fw // 2].reshape(-1, 3)])
        Y = reg @ np.array([.2126, .7152, .0722]); out.append(reg[(Y < 0.35) & (Y > 0.03)])
    d = np.concatenate(out); m = d.mean(0)
    import colorsys
    hh, ss, vv = colorsys.rgb_to_hsv(*m)
    print("%-60s R-G=%+.3f B-G=%+.3f hue=%.0f deg sat=%.2f RGB=%s" % (p[-60:], (d[:, 0] - d[:, 1]).mean(),
          (d[:, 2] - d[:, 1]).mean(), hh * 360, ss, np.round(m, 3)))
