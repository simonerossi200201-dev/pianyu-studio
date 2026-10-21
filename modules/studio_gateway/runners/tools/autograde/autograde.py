#!/usr/bin/env python
"""autograde - transparent, deterministic auto colour grading.

Pipeline (all steps are plain numpy/OpenCV math, every number is in report.json):
  1. Shot detection (HSV histogram distance)                       ~ Resolve scene-cut detection
  2. Per shot: robust measurements (ignores clipped + saturated px)
  3. Per shot: auto tone (percentile black/white, gamma to mid-grey) ~ Resolve Auto Color / Lumetri Auto Tone
     robust white balance (gray-world x white-patch)                ~ FCP Balance Color
     skin-tone guard (OpenCV Haar faces / YCrCb skin, hue toward the skin line)
     highlight roll-off, shadow lift, gentle S-curve, vibrance
  4. Shot matching: every shot is pulled toward the median look    ~ Resolve Shot Match / FCP Match Color
  5. Optional reference image: Reinhard mean/std transfer in Lab   ~ Lumetri Color Match
  6. Each shot baked into ONE fixed 33^3 .cube LUT -> ffmpeg lut3d (constant per shot = no flicker)
  7. Optional --regions: per-frame masks (faces + blown-window highlights), temporally smoothed,
     window-only highlight compression that keeps local detail, skin protection
  8. Optional Rec.709 legal-range check of the output
  9. Optional --llm / --llm-vision: local Ollama returns numbers only (strict schema, clamped)

SECURITY: no shell, argv lists only, fixed filter templates, every number clamped,
LLM text is never executed nor put in a filter, network only to localhost Ollama,
input is never overwritten, all outputs stay inside the output folder.
"""
import argparse, base64, ipaddress, json, math, os, subprocess, sys, time, urllib.parse, urllib.request
import numpy as np, cv2

_LTX_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
FF_CANDIDATES = [
    os.path.join(_LTX_ROOT, "runtime", "bin", "ffmpeg.exe"),
    os.path.join(_LTX_ROOT, "runtime", "ffmpeg", "ffmpeg.exe"),
    r"D:\0_Dev\CUTORA\runtime\ffmpeg\ffmpeg.exe",
]   # fixed binary, no PATH lookup
LUT_N = 33
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".mts", ".webm"}
LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)

# Hard bounds for every grading parameter (min, max, neutral). Everything passes clampp().
BOUNDS = dict(wb_r=(0.80, 1.25, 1.0), wb_g=(0.80, 1.25, 1.0), wb_b=(0.80, 1.25, 1.0),
              black=(0.0, 0.10, 0.0), white=(0.60, 1.0, 1.0), gamma=(0.6, 1.6, 1.0),
              exposure_ev=(-0.6, 0.6, 0.0), temp=(-0.08, 0.08, 0.0), tint=(-0.06, 0.06, 0.0),
              contrast=(-0.15, 0.40, 0.0), vibrance=(-0.2, 0.5, 0.0), saturation=(0.85, 1.25, 1.0),
              highlight_rolloff=(0.0, 1.0, 0.0), shadow_lift=(0.0, 0.08, 0.0))
# What the LLM may send per shot: bounded DELTAS only.
LLM_SCHEMA = dict(exposure_ev=(-0.3, 0.3), temp=(-0.05, 0.05), tint=(-0.04, 0.04),
                  contrast=(-0.1, 0.1), vibrance=(-0.15, 0.15), highlight_rolloff=(-0.3, 0.3),
                  shadow_lift=(-0.03, 0.03))

def num(v, lo, hi, default):
    """Accept only real finite numbers (no bools/strings), clamp to [lo, hi]."""
    if isinstance(v, bool) or not isinstance(v, (int, float)): return default
    v = float(v)
    return default if not math.isfinite(v) else min(hi, max(lo, v))

def clampp(p):
    return {k: round(num(p.get(k, BOUNDS[k][2]), *BOUNDS[k]), 5) for k in BOUNDS}

# ---------------- safety helpers ----------------
def ffbin():
    for c in FF_CANDIDATES:
        if os.path.isfile(c): return c
    sys.exit("ffmpeg not found at fixed path")

def run(cmd, **kw):
    assert isinstance(cmd, list) and all(isinstance(c, str) for c in cmd)
    r = subprocess.run(cmd, shell=False, capture_output=True, **kw)
    if r.returncode: raise RuntimeError("ffmpeg failed:\n" + r.stderr[-3000:].decode("utf-8", "replace"))
    return r

def filter_path(p):
    """Path used INSIDE an ffmpeg filter string: reject every filter metacharacter."""
    s = os.path.abspath(p).replace("\\", "/")
    if any(ch in s for ch in "'\"[],;=%\n\r\t"): sys.exit(f"unsafe characters in path: {s}")
    return s.replace(":", "\\:")

def local_host(url):
    """Only http://127.0.0.1|localhost|::1 allowed."""
    u = urllib.parse.urlparse(url)
    if u.scheme != "http" or u.username or u.password or u.path not in ("", "/"): sys.exit("--llm-host must be http://127.0.0.1:PORT")
    h = u.hostname or ""
    try: ok = h == "localhost" or ipaddress.ip_address(h).is_loopback
    except ValueError: ok = False
    if not ok: sys.exit("--llm-host rejected: only localhost/127.0.0.1 allowed")
    port = u.port or 11434
    return f"http://127.0.0.1:{port}" if h == "localhost" else f"http://{'[%s]' % h if ':' in h else h}:{port}"

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None   # never follow redirects off localhost

# ---------------- analysis ----------------
def read_small(path, width=320):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened(): sys.exit("cannot open input")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if not (1 <= fps <= 240): fps = 30.0
    frames = []
    while len(frames) < 30 * 60 * 30:                    # hard cap: 30 min @30fps
        ok, f = cap.read()
        if not ok: break
        h, w = f.shape[:2]
        f = cv2.resize(f, (width, max(2, int(h * width / w))), interpolation=cv2.INTER_AREA)
        frames.append(cv2.cvtColor(f, cv2.COLOR_BGR2RGB))
    cap.release()
    return frames, fps

def detect_shots(frames, fps, thr=0.45, min_len_s=0.5):
    cuts, prev = [0], None
    for i, f in enumerate(frames):
        h = cv2.calcHist([cv2.cvtColor(f, cv2.COLOR_RGB2HSV)], [0, 1, 2], None, [16, 8, 8], [0, 180, 0, 256, 0, 256])
        cv2.normalize(h, h)
        if prev is not None and cv2.compareHist(prev, h, cv2.HISTCMP_BHATTACHARYYA) > thr and (i - cuts[-1]) >= min_len_s * fps:
            cuts.append(i)
        prev = h
    cuts.append(len(frames))
    return [(cuts[k], cuts[k + 1]) for k in range(len(cuts) - 1)]

_FACE = None
def faces(rgb):
    """OpenCV's bundled Haar cascade (ships inside opencv, no download)."""
    global _FACE
    if _FACE is None: _FACE = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))
    g = cv2.equalizeHist(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY))
    return _FACE.detectMultiScale(g, 1.1, 5, minSize=(max(16, rgb.shape[1] // 25),) * 2)

def skin_mask_ycc(rgb):
    ycc = cv2.cvtColor(rgb, cv2.COLOR_RGB2YCrCb)
    return (ycc[..., 1] > 135) & (ycc[..., 1] < 175) & (ycc[..., 2] > 85) & (ycc[..., 2] < 130) & (ycc[..., 0] > 50)

def measure(frames):
    x = np.concatenate([f.reshape(-1, 3) for f in frames]).astype(np.float32) / 255.0
    y = x @ LUMA; mx, mn = x.max(1), x.min(1)
    sat = (mx - mn) / np.maximum(mx, 1e-4)
    clipped = mx >= 0.98
    skin = np.concatenate([skin_mask_ycc(f).reshape(-1) for f in frames])
    # face pixels (more reliable skin sample than the colour rule)
    face_px = []
    for f in frames[:: max(1, len(frames) // 6)]:
        for (fx, fy, fw, fh) in faces(f):
            c = f[fy + fh // 4: fy + 3 * fh // 4, fx + fw // 4: fx + 3 * fw // 4].reshape(-1, 3)
            face_px.append(c[skin_mask_ycc(c.reshape(-1, 1, 3)).reshape(-1)])
    face_px = np.concatenate(face_px).astype(np.float32) / 255 if face_px and sum(len(a) for a in face_px) > 50 else None
    # robust WB sample: not clipped, not dark, not strongly saturated, not skin
    valid = (~clipped) & (mn > 0.03) & (sat < 0.6) & (~skin)
    if valid.sum() < 500: valid = ~clipped
    v = x[valid]; gw = v.mean(0)
    vy = v @ LUMA; bright = v[vy >= np.percentile(vy, 97)]
    wp = bright.mean(0) if len(bright) > 20 else gw
    p = np.percentile(y, [0.5, 1, 5, 25, 50, 75, 95, 99, 99.5])
    skin_rgb = face_px.mean(0) if face_px is not None else (x[skin].mean(0) if skin.sum() > 200 else None)
    return dict(
        pct=dict(zip(["p0.5", "p1", "p5", "p25", "p50", "p75", "p95", "p99", "p99.5"], [round(float(a), 4) for a in p])),
        mean_luma=round(float(y.mean()), 4), contrast_std=round(float(y.std()), 4),
        clip_high_pct=round(float(clipped.mean() * 100), 3), clip_low_pct=round(float((y <= 0.02).mean() * 100), 3),
        skin_pct=round(float(skin.mean() * 100), 2), faces_found=face_px is not None,
        skin_rgb=None if skin_rgb is None else [round(float(a), 4) for a in skin_rgb],
        wb_grayworld=[round(float(a), 4) for a in gw], wb_whitepatch=[round(float(a), 4) for a in wp],
        wb_estimate=[round(float(a), 4) for a in np.sqrt(gw * wp)],
        sat_mean=round(float(sat[~clipped].mean()), 4) if (~clipped).any() else 0.0)

def lab_stats(rgb01):
    lab = cv2.cvtColor(rgb01.reshape(-1, 1, 3).astype(np.float32), cv2.COLOR_RGB2Lab).reshape(-1, 3)
    return lab.mean(0), lab.std(0)

def skin_hue(rgb01):
    L, a, b = cv2.cvtColor(np.array(rgb01, np.float32).reshape(1, 1, 3), cv2.COLOR_RGB2Lab).reshape(3)
    return math.degrees(math.atan2(b, a))

# ---------------- correction model ----------------
def compute_params(m, s):
    g = np.array(m["wb_estimate"]); gains = g.mean() / np.maximum(g, 1e-4)
    gains = np.clip(gains, 0.82, 1.22); gains = 1 + (gains - 1) * s * 0.85
    gains /= gains @ LUMA                                  # keep luminance
    guard = None
    if m["skin_rgb"] is not None:                          # skin guard: back off WB only if it pushes skin hue
        dist = lambda h: max(0.0, 35 - h, h - 75)          # further from the 35-75 deg skin band than before
        h0 = skin_hue(np.array(m["skin_rgb"])); d0 = dist(h0)
        for k in range(10):
            h = skin_hue(np.clip(np.array(m["skin_rgb"]) * gains, 0, 1))
            if dist(h) <= d0 + 0.5: break
            gains = 1 + (gains - 1) * 0.7; gains /= gains @ LUMA
        guard = dict(skin_hue_before=round(h0, 1), skin_hue_after=round(h, 1), wb_backoff_steps=k)
    black = min(m["pct"]["p0.5"], 0.08) * s
    white = m["pct"]["p99.5"] if m["clip_high_pct"] < 0.5 else 1.0
    white = 1 - (1 - max(white, 0.6)) * s
    mid = (m["pct"]["p50"] - black) / max(white - black, 1e-3)
    gamma = float(np.clip(np.log(0.40) / np.log(np.clip(mid, 0.05, 0.95)), 0.6, 1.6)); gamma = 1 + (gamma - 1) * s
    contrast = float(np.clip((0.21 - m["contrast_std"]) * 2.0, -0.15, 0.3)) * s
    vib = float(np.clip((0.32 - m["sat_mean"]) * 1.5, -0.1, 0.4)) * s
    hl = float(np.clip(m["clip_high_pct"] / 3.0 + (m["pct"]["p95"] > 0.9) * 0.3, 0, 1)) * s
    lift = float(np.clip(0.03 - m["pct"]["p5"], 0, 0.04)) * s if m["clip_low_pct"] > 1 else 0.0
    return clampp(dict(wb_r=gains[0], wb_g=gains[1], wb_b=gains[2], black=black, white=white, gamma=gamma,
                       contrast=contrast, vibrance=vib, highlight_rolloff=hl, shadow_lift=lift)), guard

def smooth(plist, w=0.2):
    out = []
    for i, p in enumerate(plist):
        nb = [plist[j] for j in (i - 1, i + 1) if 0 <= j < len(plist)]
        out.append(p if not nb else clampp({k: (1 - w) * p[k] + w * np.mean([q[k] for q in nb]) for k in p}))
    return out

def transform(rgb, p, lab_xfer=None):
    """The whole grade as one pure function of RGB in [0,1] -> baked into the LUT."""
    x = rgb.astype(np.float32) * np.array([p["wb_r"], p["wb_g"], p["wb_b"]], np.float32)
    x = x * np.array([1 + p["temp"], 1 - p["tint"], 1 - p["temp"]], np.float32) * (2.0 ** p["exposure_ev"])
    x = np.clip((x - p["black"]) / max(p["white"] - p["black"], 1e-3), 0, None)
    x = np.power(x, p["gamma"])                                   # mid^gamma = 0.40
    y = (x @ LUMA)[..., None]                                     # luminance-ratio ops keep hue
    k = 0.72 - 0.12 * p["highlight_rolloff"]; top = 1.0 - 0.06 * p["highlight_rolloff"]
    yc = np.where(y > k, k + (top - k) * np.tanh((y - k) / (top - k)), y)
    L = p["shadow_lift"]; yc = yc + L * (1 - yc) * np.exp(-yc / 0.12)   # lift only deep shadows
    x = np.clip(x * (yc / np.maximum(y, 1e-5)), 0, 1)
    c = p["contrast"]; x = np.clip(x + c * (x * x * (3 - 2 * x) - x), 0, 1)
    y = (x @ LUMA)[..., None]
    sat = (x.max(-1, keepdims=True) - x.min(-1, keepdims=True)) / np.maximum(x.max(-1, keepdims=True), 1e-4)
    f = p["saturation"] * (1 + p["vibrance"] * (1 - sat))        # vibrance: muted colours more
    if p["vibrance"] > 0:                                         # skin protection inside vibrance
        r, g, b = x[..., :1], x[..., 1:2], x[..., 2:]
        skinish = ((r > g) & (g > b) & (r - b > 0.06) & (r - b < 0.45)).astype(np.float32)
        f = f * (1 - 0.6 * skinish) + 0.6 * skinish
    x = np.clip(y + (x - y) * f, 0, 1)
    if lab_xfer is not None:                                      # Reinhard mean/std in Lab (shot match + reference)
        sm, ss, tm, ts, t = lab_xfer
        lab = cv2.cvtColor(x.reshape(-1, 1, 3), cv2.COLOR_RGB2Lab).reshape(-1, 3)
        ratio = np.clip(ts / np.maximum(ss, 1e-3), 0.7, 1.4); ratio[0] = np.clip(ratio[0], 0.85, 1.15)
        new = (lab - sm) * ratio + tm
        lab = lab + (new - lab) * np.asarray(t, np.float32)
        x = cv2.cvtColor(lab.reshape(-1, 1, 3).astype(np.float32), cv2.COLOR_Lab2RGB).reshape(x.shape)
    return np.clip(x, 0, 1)

def write_cube(path, p, lab_xfer=None, n=LUT_N):
    r = np.linspace(0, 1, n, dtype=np.float32)
    B, G, R = np.meshgrid(r, r, r, indexing="ij")              # .cube order: R fastest
    out = transform(np.stack([R, G, B], -1).reshape(-1, 3), p, lab_xfer)
    with open(path, "w", newline="\n", encoding="ascii") as f:
        f.write(f"TITLE \"autograde\"\nLUT_3D_SIZE {n}\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n")
        np.savetxt(f, out, fmt="%.6f")
    return out.reshape(n, n, n, 3)                              # [b,g,r] -> rgb, for --regions

# ---------------- optional local LLM ----------------
def contact_sheet(frames, shots, path):
    tiles = [frames[(a + b) // 2] for a, b in shots[:12]]
    tiles = [cv2.resize(t, (240, int(240 * t.shape[0] / t.shape[1]))) for t in tiles]
    h = max(t.shape[0] for t in tiles)
    tiles = [cv2.copyMakeBorder(t, 0, h - t.shape[0], 0, 0, cv2.BORDER_CONSTANT) for t in tiles]
    tiles += [np.zeros_like(tiles[0])] * (-len(tiles) % 4)
    img = np.vstack([np.hstack(tiles[i:i + 4]) for i in range(0, len(tiles), 4)])
    cv2.imwrite(path, cv2.cvtColor(img, cv2.COLOR_RGB2BGR)); return path

def ask_llm(meas, params, sheet, model, host, nshots):
    prompt = ("You are a film colorist. Measurements (0..1 luma percentiles, clipping %, WB estimates) and automatic "
              "parameters per shot follow. Optionally suggest small DELTAS. Reply ONLY JSON "
              '{"shots":[{"index":0,' + ",".join(f'"{k}":0' for k in LLM_SCHEMA) + "}]}. Bounds: "
              + json.dumps(LLM_SCHEMA) + ". Numbers only, no text.\n"
              + json.dumps([{"index": i, "measure": m, "auto": p} for i, (m, p) in enumerate(zip(meas, params))]))
    body = {"model": model, "prompt": prompt, "stream": False, "format": "json", "options": {"temperature": 0}}
    if sheet:
        with open(sheet, "rb") as f: body["images"] = [base64.b64encode(f.read()).decode()]
    req = urllib.request.Request(host + "/api/generate", json.dumps(body).encode(), {"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)   # no proxy, no redirect
    with opener.open(req, timeout=900) as r: raw = r.read(1_000_000)
    resp = json.loads(json.loads(raw)["response"])
    # strict schema: dict -> "shots" list -> dicts of known numeric keys; everything else dropped
    clean = {}
    if isinstance(resp, dict) and isinstance(resp.get("shots"), list):
        for d in resp["shots"][:nshots]:
            if not isinstance(d, dict): continue
            i = d.get("index")
            if isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < nshots: continue
            clean[i] = {k: num(d.get(k), lo, hi, 0.0) for k, (lo, hi) in LLM_SCHEMA.items()}
    return clean

# ---------------- optional region-aware pass ----------------
def apply_lut_np(img01, lut):
    """Trilinear 3D-LUT lookup in numpy (lut indexed [b,g,r])."""
    n = lut.shape[0]; c = np.clip(img01, 0, 1) * (n - 1)
    i0 = np.floor(c).astype(np.int32).clip(0, n - 2); f = c - i0
    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]; fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]
    out = 0
    for db in (0, 1):
        for dg in (0, 1):
            for dr in (0, 1):
                w = (fr if dr else 1 - fr) * (fg if dg else 1 - fg) * (fb if db else 1 - fb)
                out = out + w * lut[b0 + db, g0 + dg, r0 + dr]
    return out

def render_regions(ff, inp, out, luts, shots, enc, strength, fps):
    """Per frame: LUT grade + temporally smoothed masks -> raw frames piped to ffmpeg (argv list)."""
    cap = cv2.VideoCapture(inp); w = int(cap.get(3)); h = int(cap.get(4)); w2, h2 = w - w % 2, h - h % 2
    p = subprocess.Popen([ff, "-y", "-hide_banner", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w2}x{h2}", "-r", f"{fps:.6f}",
                          "-i", "-", "-i", inp, "-map", "0:v", "-map", "1:a?", *enc, "-c:a", "copy",
                          "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-shortest", out],
                         shell=False, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    win_m = skin_m = None; a = 0.2; stats = dict(frames=0, mean_window_mask=0.0, mean_face_mask=0.0)
    si = 0; i = 0
    while True:
        ok, f = cap.read()
        if not ok: break
        while si < len(shots) - 1 and i >= shots[si][1]: si += 1
        src = cv2.cvtColor(f[:h2, :w2], cv2.COLOR_BGR2RGB).astype(np.float32) / 255
        g = apply_lut_np(src, luts[si])
        y = src @ LUMA
        wm = cv2.GaussianBlur(cv2.dilate((y > 0.92).astype(np.float32), np.ones((9, 9))), (0, 0), 6)  # blown windows
        fm = np.zeros_like(y)
        if i % 3 == 0 or skin_m is None:
            for (x0, y0, fw, fh) in faces((src * 255).astype(np.uint8)):
                cv2.ellipse(fm, (int(x0 + fw / 2), int(y0 + fh / 2)), (int(fw * 0.6), int(fh * 0.8)), 0, 0, 360, 1.0, -1)
            fm = cv2.GaussianBlur(fm, (0, 0), 5)
        else: fm = skin_m
        win_m = wm if win_m is None else (1 - a) * win_m + a * wm            # temporal EMA (no flicker)
        skin_m = fm if skin_m is None or i % 3 else (1 - a) * skin_m + a * fm
        # window-only highlight compression on the BASE layer, detail layer kept (local contrast preserved)
        gy = g @ LUMA; base = cv2.GaussianBlur(gy, (0, 0), 8); det = gy - base
        comp = np.where(base > 0.6, 0.6 + 0.4 * np.tanh((base - 0.6) / 0.4 * 1.5) / np.tanh(1.5), base) * (1 - 0.08 * strength)
        ny = np.clip(comp + det, 0, 1); k = (win_m * strength)[..., None]
        g = g * (1 - k) + g * (ny / np.maximum(gy, 1e-4))[..., None] * k
        # skin protection: keep source chroma on faces, graded luminance
        gy = (g @ LUMA)[..., None]; sy = (src @ LUMA)[..., None]
        prot = gy + (src - sy) * (gy / np.maximum(sy, 1e-4)).clip(0.5, 2)
        km = (skin_m * 0.5 * strength)[..., None]; g = g * (1 - km) + prot * km
        p.stdin.write((np.clip(g, 0, 1) * 255 + 0.5).astype(np.uint8).tobytes())
        stats["mean_window_mask"] += float(win_m.mean()); stats["mean_face_mask"] += float(skin_m.mean()); stats["frames"] += 1; i += 1
    cap.release(); p.stdin.close(); rc = p.wait()
    if rc: raise RuntimeError("ffmpeg (regions) failed")
    n = max(stats["frames"], 1); stats["mean_window_mask"] = round(stats["mean_window_mask"] / n, 4); stats["mean_face_mask"] = round(stats["mean_face_mask"] / n, 4)
    return stats

# ---------------- render / checks ----------------
def venc(ff, cpu=False):
    if not cpu:
        try:
            run([ff, "-hide_banner", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1", "-c:v", "h264_nvenc", "-f", "null", "-"])
            return ["-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", "16", "-b:v", "0", "-pix_fmt", "yuv420p"]
        except Exception: pass
    return ["-c:v", "libx264", "-preset", "slow", "-crf", "15", "-pix_fmt", "yuv420p"]

def legal_check(ff, path):
    """Rec.709 video-range check on the decoded Y plane (16..235 legal)."""
    r = run([ff, "-hide_banner", "-v", "error", "-i", path, "-vf", "format=yuv420p,extractplanes=y", "-f", "rawvideo", "-pix_fmt", "gray", "-"])
    y = np.frombuffer(r.stdout, np.uint8)
    if y.size == 0: return None
    return dict(below16_pct=round(float((y < 16).mean() * 100), 3), above235_pct=round(float((y > 235).mean() * 100), 3),
                y_min=int(y.min()), y_max=int(y.max()))

def main():
    ap = argparse.ArgumentParser(description="Transparent per-shot auto colour grading -> .cube LUT + ffmpeg lut3d")
    ap.add_argument("input"); ap.add_argument("-o", "--out", help="output .mp4 (all side files go next to it)")
    ap.add_argument("--strength", type=float, default=0.8)
    ap.add_argument("--reference"); ap.add_argument("--ref-strength", type=float, default=0.7)
    ap.add_argument("--no-shot-match", action="store_true"); ap.add_argument("--regions", action="store_true")
    ap.add_argument("--llm", action="store_true"); ap.add_argument("--llm-model", default="gemma4:12b")
    ap.add_argument("--llm-host", default="http://127.0.0.1:11434"); ap.add_argument("--llm-vision", action="store_true")
    ap.add_argument("--deflicker", action="store_true"); ap.add_argument("--scene-thr", type=float, default=0.45)
    ap.add_argument("--no-sbs", action="store_true"); ap.add_argument("--no-legal-check", action="store_true")
    ap.add_argument("--cpu-encode", action="store_true")
    a = ap.parse_args()
    t0 = time.time(); ff = ffbin()
    s = num(a.strength, 0, 1, 0.8); ref_s = num(a.ref_strength, 0, 1, 0.7); thr = num(a.scene_thr, 0.1, 0.9, 0.45)
    host = local_host(a.llm_host) if a.llm else None
    if a.llm and not all(ch.isalnum() or ch in ".:-_/" for ch in a.llm_model): sys.exit("bad --llm-model")
    inp = os.path.realpath(a.input)
    if not os.path.isfile(inp) or os.path.splitext(inp)[1].lower() not in VIDEO_EXT: sys.exit("input must be an existing video file")
    out = os.path.realpath(a.out or os.path.splitext(inp)[0] + "_graded.mp4")
    if os.path.splitext(out)[1].lower() != ".mp4": sys.exit("output must end in .mp4")
    outdir = os.path.dirname(out); base = os.path.splitext(out)[0]
    work = base + "_autograde"; sbs = base + "_before_after.mp4"; rp = base + "_report.json"
    for pth in (out, sbs, rp, work):                       # never touch the input, stay in outdir
        if os.path.normcase(pth) == os.path.normcase(inp): sys.exit("refusing to overwrite the input")
        if os.path.normcase(os.path.dirname(pth)) != os.path.normcase(outdir): sys.exit("path escapes output folder")
    filter_path(work); os.makedirs(work, exist_ok=True)   # validate BEFORE creating anything
    ref_img = None
    if a.reference:
        r = cv2.imread(os.path.realpath(a.reference))
        if r is None: sys.exit("cannot read reference image")
        ref_img = cv2.cvtColor(r, cv2.COLOR_BGR2RGB)

    timings = {}; frames, fps = read_small(inp); timings["decode_s"] = round(time.time() - t0, 2)
    if not frames: sys.exit("no frames decoded")
    shots = detect_shots(frames, fps, thr)
    samples = [[frames[i] for i in np.unique(np.linspace(st, en - 1, min(24, en - st)).astype(int))] for st, en in shots]
    meas, params, guards = [], [], []
    for smp in samples:
        m = measure(smp); p, gd = compute_params(m, s); meas.append(m); params.append(p); guards.append(gd)
    llm_info = None
    if a.llm:
        t1 = time.time()
        try:
            sheet = contact_sheet(frames, shots, os.path.join(work, "contact_sheet.jpg")) if a.llm_vision else None
            deltas = ask_llm(meas, params, sheet, a.llm_model, host, len(shots))
            for i, d in deltas.items():
                q = dict(params[i])
                for k, v in d.items(): q[k] = q[k] + v * s
                params[i] = clampp(q)
            llm_info = {"model": a.llm_model, "vision": bool(a.llm_vision), "validated_deltas": deltas}
        except Exception as e:
            llm_info = {"model": a.llm_model, "error": type(e).__name__, "note": "LLM skipped, automatic values used"}
        timings["llm_s"] = round(time.time() - t1, 2)
    params = smooth(params)

    # Lab stats of each shot AFTER the base grade -> shot match target (median) and/or reference
    graded_lab = [lab_stats(np.concatenate([transform(f.reshape(-1, 3) / 255.0, p) for f in smp[::2]])) for smp, p in zip(samples, params)]
    xfers = []
    med = (np.median([g[0] for g in graded_lab], 0), np.median([g[1] for g in graded_lab], 0))
    ref = lab_stats(ref_img.reshape(-1, 3) / 255.0) if ref_img is not None else None
    for gm, gs in graded_lab:
        if ref is not None: xfers.append((gm, gs, ref[0], ref[1], np.array([0.5, 0.8, 0.8]) * ref_s * s))
        elif len(shots) > 1 and not a.no_shot_match: xfers.append((gm, gs, med[0], med[1], np.array([0.4, 0.6, 0.6]) * s))
        else: xfers.append(None)
    luts, lut_arrays = [], []
    for i, (p, x) in enumerate(zip(params, xfers)):
        lp = os.path.join(work, f"shot{i:03d}.cube"); lut_arrays.append(write_cube(lp, p, x)); luts.append(lp)

    t2 = time.time(); enc = venc(ff, a.cpu_encode); region_stats = None
    if a.regions:
        region_stats = render_regions(ff, inp, out, lut_arrays, shots, enc, s, fps); graph = "python per-frame LUT + masks (see region_stats)"
    else:
        pre = "deflicker=size=5:mode=pm," if a.deflicker else ""
        n = len(shots)
        parts = [f"[0:v]{pre}format=rgb24,split={n}" + "".join(f"[s{i}]" for i in range(n)) if n > 1 else f"[0:v]{pre}format=rgb24,null[s0]"]
        for i, ((st, en), lp) in enumerate(zip(shots, luts)):
            end = "" if i == n - 1 else f":end_frame={int(en)}"
            parts.append(f"[s{i}]trim=start_frame={int(st)}{end},setpts=PTS-STARTPTS,lut3d=file='{filter_path(lp)}':interp=tetrahedral[g{i}]")
        parts.append("".join(f"[g{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0,format=yuv420p[v]")
        graph = ";".join(parts); gfile = os.path.join(work, "filter_graph.txt")
        with open(gfile, "w", encoding="utf-8") as f: f.write(graph)
        run([ff, "-y", "-hide_banner", "-i", inp, "-filter_complex_script", gfile, "-map", "[v]", "-map", "0:a?", *enc, "-c:a", "copy",
             "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-movflags", "+faststart", out])
    timings["render_s"] = round(time.time() - t2, 2)
    if not a.no_sbs:
        run([ff, "-y", "-hide_banner", "-i", inp, "-i", out, "-filter_complex",
             "[0:v]setpts=PTS-STARTPTS,scale=trunc(iw/2)*2:trunc(ih/2)*2[a];[1:v]setpts=PTS-STARTPTS,scale=trunc(iw/2)*2:trunc(ih/2)*2[b];[a][b]hstack=inputs=2[v]",
             "-map", "[v]", "-map", "0:a?", *enc, "-c:a", "copy", "-shortest", sbs])
    else: sbs = None
    legal = None if a.no_legal_check else legal_check(ff, out)
    timings["total_s"] = round(time.time() - t0, 2)
    rep = dict(input=inp, output=out, before_after=sbs, fps=fps, frames=len(frames), strength=s, reference=a.reference,
               shot_match=xfers[0] is not None and ref is None, regions=region_stats, deflicker=a.deflicker, encoder=enc[1],
               rec709_legal_check=legal, llm=llm_info, timings=timings, ffmpeg_filter_graph=graph,
               shots=[dict(index=i, start_frame=st, end_frame=en, start_s=round(st / fps, 3), end_s=round(en / fps, 3), measure=m,
                           skin_guard=gd, params=p, lut=os.path.basename(l))
                      for i, ((st, en), m, p, gd, l) in enumerate(zip(shots, meas, params, guards, luts))])
    with open(rp, "w", encoding="utf-8") as f: json.dump(rep, f, ensure_ascii=False, indent=2, default=float)
    print(json.dumps(dict(output=out, before_after=sbs, report=rp, shots=len(shots), legal=legal, timings=timings), ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
