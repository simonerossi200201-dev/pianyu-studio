# -*- coding: utf-8 -*-
"""relight.py - standalone indoor video relighting core (not part of Robota).

Pipeline per frame (all deterministic, no generative model in the main path):
  1. masks: person (SAM 2.1 read-only weights if available, else OpenCV face
     detector + GrabCut), window (luminance + saturation), background (rest)
  2. depth: Intel DPT-Hybrid (MiDaS 3.0, Apache-2.0) at low res, EMA-smoothed
     over time after scale/shift alignment; normals from depth gradients
  3. shading: Lambert with wrap ("softness") for key / fill / rim lights +
     ambient, applied multiplicatively in linear light, luminance-preserving,
     skin-protected, EMA-smoothed over time
  4. window highlight roll-off inside the window mask
  5. optional IC-Light keyframe refinement (soft, low-frequency blend only)
  6. ffmpeg encode with original audio + side-by-side comparison

Security: no shell=True, no eval/exec, ffmpeg called with an argument list,
paths validated, input never overwritten, no network access at runtime
(HF_HUB_OFFLINE=1 / TRANSFORMERS_OFFLINE=1 are forced).
"""
import argparse, json, math, os, re, subprocess, sys, time
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
import numpy as np
import cv2

LIGHT_DIRS = {  # (x right, y down, z toward camera) of the KEY light
    "left": (-0.8, -0.35, 0.5), "right": (0.8, -0.35, 0.5),
    "top": (0.0, -0.9, 0.45), "front": (0.0, -0.2, 1.0),
    "top-left": (-0.6, -0.65, 0.45), "top-right": (0.6, -0.65, 0.45),
}
SAFE_PATH = re.compile(r"^[^\x00-\x1f\"'`;|&<>$%*?\n\r]+$")

def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)

def check_path(p, must_exist):
    p = os.path.abspath(p)
    if not SAFE_PATH.match(p):
        raise SystemExit("unsafe characters in path: %r" % p)
    if must_exist and not os.path.isfile(p):
        raise SystemExit("file not found: " + p)
    return p

def kelvin_rgb(k):
    """Tanner Helland approximation, returns linear-ish RGB normalised to max 1."""
    t = max(1000.0, min(40000.0, float(k))) / 100.0
    r = 255.0 if t <= 66 else 329.698727446 * ((t - 60) ** -0.1332047592)
    g = (99.4708025861 * math.log(t) - 161.1195681661) if t <= 66 else 288.1221695283 * ((t - 60) ** -0.0755148492)
    b = 255.0 if t >= 66 else (0.0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307)
    c = np.clip(np.array([r, g, b]) / 255.0, 0, 1) ** 2.2
    c = c / (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2])  # unit luminance
    return c.astype(np.float32)

def srgb_to_lin(x):
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)

def lin_to_srgb(x):
    x = np.clip(x, 0, 1)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)

def probe(ffprobe, path):
    out = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,r_frame_rate,nb_frames", "-of", "json", path],
                         capture_output=True, text=True, check=True).stdout
    s = json.loads(out)["streams"][0]
    num, den = s["r_frame_rate"].split("/")
    has_audio = bool(subprocess.run([ffprobe, "-v", "error", "-select_streams", "a", "-show_entries",
                                     "stream=index", "-of", "csv=p=0", path],
                                    capture_output=True, text=True).stdout.strip())
    return int(s["width"]), int(s["height"]), float(num) / float(den), has_audio

# ---------------------------------------------------------------- masks
class Segmenter:
    def __init__(self, sam_ckpt, device, every):
        self.every = max(1, every)
        self.face = None  # OpenCV 5 dropped CascadeClassifier; fall back to a skin-blob face finder
        if hasattr(cv2, "CascadeClassifier") and hasattr(cv2, "data"):
            self.face = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))
        self.sam = None
        self.prev_person = None
        self.last_box = None
        if sam_ckpt and os.path.isdir(sam_ckpt):
            try:  # CUTORA's SAM 2.1 (HF transformers format), loaded read-only
                import torch
                from transformers import Sam2Model, Sam2Processor
                self.proc = Sam2Processor.from_pretrained(sam_ckpt, local_files_only=True)
                self.sam = Sam2Model.from_pretrained(sam_ckpt, local_files_only=True,
                                                     torch_dtype=(torch.float16 if device == "cuda" else torch.float32)).to(device).eval()
                self.dev = device
                log("segmentation: SAM 2.1", sam_ckpt)
            except Exception as e:  # fall back, never fail
                log("SAM 2.1 unavailable (%s: %s) -> OpenCV fallback" % (type(e).__name__, str(e)[:120]))
                self.sam = None
        if self.sam is None:
            log("segmentation: OpenCV face + GrabCut fallback")

    def person_box(self, bgr):
        g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        if self.face is not None:
            faces = self.face.detectMultiScale(g, 1.1, 5, minSize=(g.shape[1] // 30, g.shape[1] // 30))
        else:
            faces = skin_faces(bgr)
        if len(faces) == 0:
            return self.last_box
        h, w = g.shape
        boxes = []
        for (x, y, fw, fh) in faces:  # face -> upper body box heuristic
            boxes.append([max(0, x - 1.6 * fw), max(0, y - 0.6 * fh), min(w, x + 2.6 * fw), min(h, y + 7 * fh)])
        b = np.array(boxes)
        box = np.array([b[:, 0].min(), b[:, 1].min(), b[:, 2].max(), b[:, 3].max()])
        self.last_box = box
        return box

    def person(self, bgr, idx):
        if self.prev_person is not None and idx % self.every:
            return self.prev_person
        box = self.person_box(bgr)
        h, w = bgr.shape[:2]
        m = np.zeros((h, w), np.float32)
        if box is not None:
            if self.sam is not None:
                import torch
                from PIL import Image
                img = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
                inp = self.proc(images=img, input_boxes=[[box.tolist()]], return_tensors="pt").to(self.dev)
                if self.dev == "cuda":
                    inp["pixel_values"] = inp["pixel_values"].half()
                with torch.inference_mode():
                    o = self.sam(**inp, multimask_output=False)
                mk = self.proc.post_process_masks(o.pred_masks.float().cpu(), inp["original_sizes"].cpu())[0]
                m = np.asarray(mk).reshape(-1, h, w)[0].astype(np.float32)
            else:
                x0, y0, x1, y1 = box.astype(int)
                gc = np.full((h, w), cv2.GC_BGD, np.uint8)
                gc[y0:y1, x0:x1] = cv2.GC_PR_FGD
                bm, fm = np.zeros((1, 65)), np.zeros((1, 65))
                small = cv2.resize(bgr, (w // 2, h // 2)); gcs = cv2.resize(gc, (w // 2, h // 2), interpolation=cv2.INTER_NEAREST)
                cv2.grabCut(small, gcs, None, bm, fm, 3, cv2.GC_INIT_WITH_MASK)
                m = cv2.resize(((gcs == 1) | (gcs == 3)).astype(np.float32), (w, h))
        m = cv2.GaussianBlur(m, (0, 0), max(2, w / 300))
        if self.prev_person is not None:
            m = 0.6 * m + 0.4 * self.prev_person
        self.prev_person = m
        return m

def skin_faces(bgr):
    """No-model face finder: largest compact skin-coloured blobs in the upper 70% of frame."""
    ycc = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
    s = ((ycc[..., 1] > 138) & (ycc[..., 1] < 173) & (ycc[..., 2] > 80) & (ycc[..., 2] < 127)
         & (ycc[..., 0] > 50)).astype(np.uint8)
    s = cv2.morphologyEx(s, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    s = cv2.morphologyEx(s, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    h, w = s.shape
    n, lab, st, _ = cv2.connectedComponentsWithStats(s)
    out = []
    for i in range(1, n):
        x, y, bw, bh, ar = st[i]
        if ar < h * w * 0.004 or y > h * 0.7 or not (0.5 < bh / max(bw, 1) < 2.2) or ar < 0.45 * bw * bh:
            continue
        out.append((ar, (x, y, bw, bh)))
    out.sort(reverse=True)
    return [b for _, b in out[:3]]

def skin_mask(bgr, person):
    ycc = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
    s = ((ycc[..., 1] > 135) & (ycc[..., 1] < 175) & (ycc[..., 2] > 85) & (ycc[..., 2] < 130)).astype(np.float32)
    s = cv2.GaussianBlur(s, (0, 0), 3) * np.clip(person * 1.5, 0, 1)
    return s

def window_mask(lin, person, thr, prev=None):
    """v2: bright AND low-saturation (blown sky / net curtain) regions in the background,
    grown from confident seeds, adaptive threshold, temporally smoothed."""
    h, w = lin.shape[:2]
    sw, sh = w // 8, h // 8
    L = cv2.resize(lin, (sw, sh), interpolation=cv2.INTER_AREA)
    P = cv2.resize(person, (sw, sh), interpolation=cv2.INTER_AREA)
    y = 0.2126 * L[..., 0] + 0.7152 * L[..., 1] + 0.0722 * L[..., 2]
    mx, mn = L.max(2), L.min(2)
    sat = (mx - mn) / (mx + 1e-4)
    bg = P < 0.3
    ref = np.percentile(y[bg], 99) if bg.any() else y.max()
    t = min(thr, 0.8 * ref)
    seed = ((y > max(t, 0.92 * ref)) & (sat < 0.25) & bg).astype(np.uint8)
    cand = ((y > 0.7 * t) & (sat < 0.35) & bg).astype(np.uint8)
    cand = cv2.morphologyEx(cand, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(cand)
    keep = np.zeros((sh, sw), np.float32)
    min_area = sh * sw * 0.003
    for i in range(1, n):
        comp = lab == i
        if st[i, cv2.CC_STAT_AREA] >= min_area and seed[comp].any():
            keep[comp] = 1
    # soft weight by how bright it is (only really bright parts are compressed hard)
    keep *= np.clip((y - 0.7 * t) / (0.3 * t + 1e-4), 0, 1) ** 0.5
    if prev is not None:
        keep = 0.6 * prev + 0.4 * keep
    small = keep
    keep = cv2.resize(cv2.GaussianBlur(keep, (0, 0), 1.0), (w, h))
    return np.clip(keep * (1 - person), 0, 1), small

# ---------------------------------------------------------------- depth
class Depth:
    def __init__(self, model_dir, device, res, ema):
        import torch
        from transformers import DPTForDepthEstimation
        self.torch = torch
        self.dev = device
        self.res = res
        self.ema = ema
        self.model = DPTForDepthEstimation.from_pretrained(model_dir, local_files_only=True,
                                                           torch_dtype=(torch.float16 if device == "cuda" else torch.float32)).to(device).eval()
        self.prev = None
        log("depth: DPT-Hybrid on", device, "@ 384x384")

    def __call__(self, rgb01):
        t = self.torch
        h, w = rgb01.shape[:2]
        nh = nw = 384  # DPT-Hybrid has fixed 384x384 position embeddings; aspect restored afterwards
        x = cv2.resize(rgb01, (nw, nh), interpolation=cv2.INTER_AREA)
        x = (x - 0.5) / 0.5
        xt = t.from_numpy(x.transpose(2, 0, 1)[None].astype(np.float16 if self.dev == "cuda" else np.float32)).to(self.dev)
        with t.inference_mode():
            d = self.model(pixel_values=xt).predicted_depth[0].float().cpu().numpy()  # inverse depth
        d = cv2.resize(d, (int(384 * w / max(h, w)) // 2 * 2 or 2, int(384 * h / max(h, w)) // 2 * 2 or 2))
        d = d / (np.percentile(d, 98) + 1e-6)
        if self.prev is not None:  # align scale/shift to previous, then EMA
            A = np.stack([d.ravel(), np.ones(d.size)], 1)
            sc, sh = np.linalg.lstsq(A, self.prev.ravel(), rcond=None)[0]
            d = self.ema * self.prev + (1 - self.ema) * (sc * d + sh)
        self.prev = d
        return d

def normals_from_disparity(disp, blur, person=None, relief=1.0):
    """Normals = clamped depth gradients (fine relief) + rounded 'shape-from-silhouette'
    normals from the blurred person mask (gives the body/face volume a monocular depth
    net under-estimates). Clamping avoids dark halos at depth discontinuities."""
    h, w = disp.shape
    d = cv2.GaussianBlur(disp.astype(np.float32), (0, 0), blur)
    k = w * 1.5 * relief
    gx = cv2.Sobel(d, cv2.CV_32F, 1, 0, ksize=3) * k / 8
    gy = cv2.Sobel(d, cv2.CV_32F, 0, 1, ksize=3) * k / 8
    mag = np.sqrt(gx * gx + gy * gy)
    lim = 0.9
    sc = np.minimum(1, lim / (mag + 1e-6))
    gx, gy = gx * sc, gy * sc
    if person is not None:
        p = cv2.resize(person, (w, h))
        pb = cv2.GaussianBlur(p, (0, 0), w / 14)
        sx = cv2.Sobel(pb, cv2.CV_32F, 1, 0, ksize=3) * w / 8 * 0.35
        sy = cv2.Sobel(pb, cv2.CV_32F, 0, 1, ksize=3) * w / 8 * 0.35
        gx = gx + sx * relief  # mask acts like extra disparity -> outward-facing edges
        gy = gy + sy * relief
    n = np.dstack([-gx, -gy, np.ones_like(d)])
    n /= np.linalg.norm(n, axis=2, keepdims=True)
    return n

def blend_normals(n, extra, alpha):
    out = n * (1 - alpha[..., None]) + extra * alpha[..., None]
    return out / (np.linalg.norm(out, axis=2, keepdims=True) + 1e-6)

def cast_shadow(disp, L, length, softness):
    """Screen-space shadow: march from each pixel toward the light over the disparity
    height field; a sample that rises above the ray occludes. Deterministic, no AI."""
    h, w = disp.shape
    lx, ly, lz = np.asarray(L, np.float32) / np.linalg.norm(L)
    planar = math.hypot(lx, ly) + 1e-6
    dx, dy = lx / planar, ly / planar
    steps = 24
    maxd = length * w
    rise = max(lz, 0.15) / planar  # ray height gain per pixel (disparity units scaled below)
    hscale = w * 0.25  # disparity 1.0 ~ quarter frame width of height
    H0 = disp.astype(np.float32) * hscale
    occ = np.zeros_like(H0)
    xs, ys = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    for k in range(1, steps + 1):
        d = maxd * k / steps
        Hs = cv2.remap(H0, (xs + dx * d).astype(np.float32), (ys + dy * d).astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        above = Hs - (H0 + d * rise * 0.5)
        occ = np.maximum(occ, np.clip(above / (0.02 * hscale + 1e-6), 0, 1) * (1 - 0.5 * k / steps))
    occ = cv2.GaussianBlur(occ, (0, 0), 0.5 + 6 * softness)
    return 1 - occ

# ---------------------------------------------------------------- shading
def lambert(n, L, soft):
    L = np.asarray(L, np.float32); L = L / np.linalg.norm(L)
    ndl = n[..., 0] * L[0] + n[..., 1] * L[1] + n[..., 2] * L[2]
    return np.clip((ndl + soft) / (1 + soft), 0, 1) ** (1 + (1 - soft))

def shading(n, disp, person, a, face=None):
    kx, ky, kz = LIGHT_DIRS[a.light]
    key = lambert(n, (kx, ky, kz), a.softness)
    fill = lambert(n, (-kx, ky * 0.5, 0.8), 1.0)
    rim = lambert(n, (-kx * 0.9, -0.3, -0.6), 0.2) * person
    near = np.clip(disp, 0, 1.2) ** 0.5  # light falloff: near things get more key
    h, w = disp.shape
    xs = np.linspace(-1, 1, w, dtype=np.float32)[None, :]
    ys = np.linspace(-1, 1, h, dtype=np.float32)[:, None]
    side = np.clip(0.5 + 0.5 * (xs * kx + ys * ky * 0.5), 0, 1) ** 1.2  # off-screen source falloff
    side = side * (1 - person) + (0.75 + 0.25 * side) * person  # light the subject, not just the wall
    key = key * (0.35 + 0.65 * side) * (a.bg_key + (1 - a.bg_key) * person)
    if a.shadow > 0:
        key = key * (1 - a.shadow + a.shadow * cast_shadow(disp, (kx, ky, kz), a.shadow_len, a.softness))
    if face is not None and a.face_key > 0:
        # extra side key + rim on the face only (face-mesh normals carry the 3D shape)
        fk = lambert(n, (kx, ky * 0.5, 0.35), 0.15)
        fr = lambert(n, (-kx, -0.2, -0.3), 0.0)
        key = key * (1 - face) + face * (key * 0.4 + fk * 1.2) * (1 + 0.0)
        rim = np.maximum(rim, fr * face * a.face_key)
        key = key * (1 + a.face_key * face * 0.3)
    ck, cf = kelvin_rgb(a.temp), kelvin_rgb(a.fill_temp)
    S = (a.ambient
         + a.key * (key * (0.6 + 0.4 * near))[..., None] * ck
         + a.fill * fill[..., None] * cf
         + a.rim * rim[..., None] * ck)
    return S.astype(np.float32)

def apply_shading(lin, S, skin, strength, skin_keep, contrast):
    Y = lambda x: 0.2126 * x[..., 0] + 0.7152 * x[..., 1] + 0.0722 * x[..., 2]
    S = S / (np.median(Y(S)) + 1e-6)  # keep global exposure
    S = np.exp(contrast * strength * np.log(np.maximum(S, 1e-3)))  # log-domain gain = light/shadow ratio
    S = np.clip(S, 0.25, 2.5)
    # skin: keep the light/shadow (luminance) but drop most of the colour cast and
    # soften it (soft-light style) so skin tones stay natural
    sl = Y(S)[..., None]
    k = skin[..., None] * skin_keep
    S = (1 - k) * S + k * (1 + 0.75 * (sl - 1))
    out = lin * S
    # soft highlight roll-off so added light never clips hard
    return out / (1 + np.maximum(out - 0.8, 0) * 1.5)

def compress_window(lin, wm, amount):
    if amount <= 0:
        return lin
    y = 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]
    target = 0.42 * np.tanh(y / 0.42)  # filmic shoulder
    g = 1 + wm * amount * (target / (y + 1e-6) - 1) * 0.85
    out = lin * g[..., None]
    # pull clipped white toward the room's warm-neutral tint slightly
    return out

# ---------------------------------------------------------------- de-glare (v4)
def lamp_mask(Y, person, thr):
    """Practical lights: near-clipped, compact, mostly outside the person."""
    h, w = Y.shape
    sm = cv2.resize(Y, (w // 4, h // 4), interpolation=cv2.INTER_AREA)
    ps = cv2.resize(person, (w // 4, h // 4))
    m = ((sm > thr) & (ps < 0.5)).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(m)
    keep = np.zeros(sm.shape, np.float32)
    for i in range(1, n):
        if st[i, cv2.CC_STAT_AREA] >= 4 and st[i, cv2.CC_STAT_AREA] < sm.size * 0.08:
            keep[lab == i] = 1
    return cv2.resize(cv2.dilate(keep, np.ones((3, 3), np.uint8)), (w, h))

def deglare(lin, person, a, state):
    """Remove the veiling glare of a practical lamp:
    1) lamp mask, 2) glare = blurred additive layer from the lamp's energy (two scales),
    3) subtract it (bounded so nothing goes below the local dark level), 4) local black-level
    / contrast recovery on the person, 5) compress the lamp core highlight.
    Returns corrected linear image, lamp mask, glare layer (for debug)."""
    if a.deglare <= 0:
        return lin, None, None
    h, w = lin.shape[:2]
    Y = 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]
    lm = lamp_mask(Y, person, a.lamp_thr)
    lm[int(h * 0.15):] = 0  # v6d: ceiling lamp only (top band); bright wall patches are not lamps
    src = (lm * np.maximum(Y, 0))
    sm = cv2.resize(src, (w // 8, h // 8), interpolation=cv2.INTER_AREA)
    g = 0.6 * cv2.GaussianBlur(sm, (0, 0), w / 8 / 12) + 0.4 * cv2.GaussianBlur(sm, (0, 0), w / 8 / 4)
    G = cv2.resize(g, (w, h))
    G = G / (G.max() + 1e-6)
    if state.get("G") is not None:
        G = 0.8 * state["G"] + 0.2 * G
    state["G"] = G
    # glare colour = lamp colour
    if lm.sum() > 0:
        col = (lin * lm[..., None]).reshape(-1, 3).sum(0) / (lm.sum() + 1e-6)
        col = col / (col.max() + 1e-6)
    else:
        col = np.ones(3, np.float32)
    # amplitude: the darkest local level near the glare = how much veil we can safely remove
    dark = cv2.erode(lin.min(2), np.ones((15, 15), np.uint8))
    dark = cv2.GaussianBlur(dark, (0, 0), w / 60)
    amp = np.clip(dark / (G + 1e-3), 0, 1)
    amp = cv2.GaussianBlur(amp, (0, 0), w / 30) * a.deglare
    # v6: only near the lamp (distance-limited), so far walls are never touched (no blotches)
    lms = cv2.resize(lm, (w // 8, h // 8), interpolation=cv2.INTER_AREA)  # low-res: same result, ~100x faster
    k8 = int(w / 8 / 12) | 1  # radius ~ w/24 at full res (kernel is in 1/8-res pixels)
    near = cv2.GaussianBlur(cv2.dilate(lms, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k8, k8))), (0, 0), w / 128)
    near = cv2.resize(near, (w, h))
    near = np.clip(near * 1.5, 0, 1)
    V = (amp * G * near)[..., None] * col[None, None, :]
    out = np.maximum(lin - V * 0.9, 0) / (1 - 0.5 * V)  # subtract veil, re-expand range
    # person: local black-level & contrast recovery (dehaze-like, no AI)
    if a.blacklevel > 0:
        # v6f: hard, eroded person core so the soft/leaky matte never touches the wall (mottling)
        pk = cv2.erode((person > 0.6).astype(np.float32), np.ones((25, 25), np.uint8))
        pk = cv2.GaussianBlur(pk, (0, 0), 6)
        if state.get("pk") is not None and state["pk"].shape == pk.shape:
            pk = 0.75 * state["pk"] + 0.25 * pk  # v6h: temporal smoothing of the hard core (face flicker)
        state["pk"] = pk; person = pk
        d2 = cv2.erode(out.min(2), np.ones((9, 9), np.uint8))
        d2 = cv2.GaussianBlur(d2, (0, 0), w / 40)
        lift = (d2 * a.blacklevel * np.clip(person * 1.2, 0, 1))[..., None]
        out = np.maximum(out - lift, 0) / (1 - lift + 1e-3) ** 0.5
        # mild local contrast on the person (unsharp on log luminance, large radius)
        Yo = 0.2126 * out[..., 0] + 0.7152 * out[..., 1] + 0.0722 * out[..., 2]
        lg = np.log(Yo + 1e-3)
        base = cv2.GaussianBlur(lg, (0, 0), w / 50)
        boost = np.exp((lg - base) * 0.35 * a.blacklevel * person)
        out = out * boost[..., None]
    # lamp core: compress so it stops blooming into neighbours
    Yo = 0.2126 * out[..., 0] + 0.7152 * out[..., 1] + 0.0722 * out[..., 2]
    tgt = 0.6 * np.tanh(Yo / 0.6)
    lmb = cv2.GaussianBlur(lm, (0, 0), w / 200) * min(1.0, a.deglare / 0.8)  # v6e: scales with --deglare
    out = out * (1 + lmb * (tgt / (Yo + 1e-6) - 1))[..., None]
    return out.astype(np.float32), lm, G

# ---------------------------------------------------------------- lenses & hair (v4)
def fix_lenses(lin, lens, amount):
    """Remove the grey veil on spectacle lenses: subtract a smooth local dark level
    (the veil is additive and low-frequency), re-expand, restore chroma lost to the veil.
    Smooth layers only -> eye detail is not blurred."""
    if amount <= 0 or lens is None or lens.max() < 0.05:
        return lin
    h, w = lin.shape[:2]
    m = lens[..., None]
    dark = cv2.erode(lin.min(2), np.ones((5, 5), np.uint8))
    veil = cv2.GaussianBlur(dark, (0, 0), w / 150)[..., None] * 0.85 * amount
    out = np.maximum(lin - veil, 0) / (1 - veil + 1e-3) ** 0.6
    Y = (0.2126 * out[..., 0] + 0.7152 * out[..., 1] + 0.0722 * out[..., 2])[..., None]
    out = Y + (out - Y) * (1 + 0.35 * amount)  # chroma the veil washed out
    return (lin * (1 - m) + out * m).astype(np.float32)

def fix_hair_shadows(lin, person, skin, box, amount):
    """Neutralise magenta/blue cast in dark hair: only on the person, above the chin,
    outside skin, in dark pixels; pull chroma toward a natural dark brown, deepen blacks."""
    if amount <= 0:
        return lin
    h, w = lin.shape[:2]
    reg = np.clip(person, 0, 1) * (1 - np.clip(skin * 2, 0, 1))
    if box is not None and box[0] >= 0:
        x0, y0, x1, y1 = box
        fw = x1 - x0
        yy = np.arange(h, dtype=np.float32)[:, None]; xx = np.arange(w, dtype=np.float32)[None, :]
        reg = reg * np.clip((y1 + 0.15 * fw - yy) / (0.1 * fw), 0, 1) \
                  * np.clip(1 - np.abs(xx - (x0 + x1) / 2) / (1.3 * fw), 0, 1) ** 0.3
    r, g, b = lin[..., 0], lin[..., 1], lin[..., 2]
    Y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    # any cool/magenta drift away from a warm brown (B above ~0.7*G, or R-G-B balance) counts
    purple = np.clip(((b - 0.75 * g) / (Y + 0.02)) * 3 + ((b + r) / 2 - g) / (Y + 0.02) * 2, 0, 1)
    dark = np.clip((0.18 - Y) / 0.12, 0, 1)
    wgt = cv2.GaussianBlur(reg * purple * dark, (0, 0), 1.5)[..., None] * amount
    brown = np.array([1.25, 1.0, 0.72], np.float32)
    brown = brown / (0.2126 * brown[0] + 0.7152 * brown[1] + 0.0722 * brown[2])
    tgt = (Y * 0.85)[..., None] * brown  # natural dark brown, slightly deeper
    return (lin * (1 - wgt) + tgt * wgt).astype(np.float32)

def fix_lips(lin, lip, amount, state):
    """Mute over-red lips: -35% chroma (x amount) and nudge hue from pure red toward a
    muted rose. Mask from MediaPipe lip landmarks, temporally smoothed, feathered."""
    if amount <= 0 or lip is None:
        return lin
    if state.get("lip") is not None and state["lip"].shape == lip.shape:
        lip = 0.6 * state["lip"] + 0.4 * lip
    state["lip"] = lip
    if lip.max() < 0.05:
        return lin
    Y = (0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2])[..., None]
    c = lin - Y
    c = c * (1 - 0.35 * amount)
    rose = np.array([0.86, 0.55, 0.50], np.float32)  # warm muted rose (no blue -> no plum)
    rose = rose - (0.2126 * rose[0] + 0.7152 * rose[1] + 0.0722 * rose[2])
    mag = np.linalg.norm(c, axis=2, keepdims=True)
    hm = 0.45 * amount  # v6g: real hue move toward muted rose (0.12 still read as plum)
    c = c * (1 - hm) + hm * mag * rose / np.linalg.norm(rose)
    out = np.maximum(Y * (1 + 0.10 * amount) + c, 0)  # lips went dark/plum after black-level: small lift
    m = lip[..., None]
    return (lin * (1 - m) + out * m).astype(np.float32)

def face_fill(lin, face_a, face_n, person, a, state):
    """Broadcast-style soft fill: lift shadows evenly on the face, ~4% key direction,
    luminance-only (one gain for R,G,B -> no colour shift), heavily smoothed in time."""
    h, w = lin.shape[:2]
    sw, sh = face_a.shape[1], face_a.shape[0]
    Ls = cv2.resize(lin, (sw, sh), interpolation=cv2.INTER_AREA)
    Y = 0.2126 * Ls[..., 0] + 0.7152 * Ls[..., 1] + 0.0722 * Ls[..., 2]
    Y = cv2.GaussianBlur(Y, (0, 0), 2.0)  # low-frequency shading only, keeps texture
    ps = cv2.resize(person, (sw, sh))
    m = cv2.GaussianBlur(face_a, (0, 0), 6) * np.clip(ps * 1.3, 0, 1)  # wide feather -> no edge glow
    ref = np.percentile(Y[m > 0.5], 75) if (m > 0.5).sum() > 20 else 0.2
    lift = np.clip((ref - Y) / (ref + 1e-4), 0, 1) ** 1.3 * a.face_fill * 0.6  # lift, never erase, shadows  # darker than face highlights -> lift
    g = 1 + lift * m
    if a.fill_key > 0:
        # follow the EXISTING scene key: sign of correlation between face brightness and normal.x
        nx = face_n[..., 0]; sel = m > 0.5
        if sel.sum() > 20:
            c = float(np.mean((Y[sel] - Y[sel].mean()) * nx[sel]))
            d = state.get("keydir", 0.0) * 0.95 + 0.05 * np.sign(c)
            state["keydir"] = d
            g *= 1 + a.fill_key * m * nx * float(np.clip(d * 3, -1, 1))
    if state.get("ffg") is not None:
        g = 0.9 * state["ffg"] + 0.1 * g
    state["ffg"] = g
    G = cv2.resize(g, (w, h), interpolation=cv2.INTER_LINEAR)
    return (lin * G[..., None]).astype(np.float32)

# ---------------------------------------------------------------- IC-Light hook
def iclight_ready(root):
    py = os.path.join(root, "venv", "Scripts", "python.exe")
    run = os.path.join(root, "iclight_run.py")
    return os.path.isfile(py) and os.path.isfile(run), py, run

# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Indoor video relighting core")
    ap.add_argument("input")
    ap.add_argument("-o", "--outdir", default=r"D:\0_LLM\Outputs\relight")
    ap.add_argument("--name", default=None)
    ap.add_argument("--light", choices=sorted(LIGHT_DIRS), default="left")
    ap.add_argument("--temp", type=float, default=4500)
    ap.add_argument("--fill-temp", type=float, default=6500)
    ap.add_argument("--strength", type=float, default=0.6)
    ap.add_argument("--key", type=float, default=1.0)
    ap.add_argument("--fill", type=float, default=0.25)
    ap.add_argument("--rim", type=float, default=0.4)
    ap.add_argument("--ambient", type=float, default=0.25)
    ap.add_argument("--softness", type=float, default=0.5)
    ap.add_argument("--skin-keep", type=float, default=0.6)
    ap.add_argument("--window", type=float, default=0.8, help="window highlight compression 0-1")
    ap.add_argument("--window-thr", type=float, default=0.5)
    ap.add_argument("--contrast", type=float, default=2.5, help="light/shadow ratio gain 0.5-5")
    ap.add_argument("--face-npz", default="", help="face normals from face_pass.py")
    ap.add_argument("--face-weight", type=float, default=0.8)
    ap.add_argument("--normals-npz", default="", help="Marigold keyframe normals from marigold_pass.py")
    ap.add_argument("--mg-weight", type=float, default=0.7)
    ap.add_argument("--bg-key", type=float, default=0.55, help="key light on background vs subject 0-1")
    ap.add_argument("--shadow", type=float, default=0.6, help="cast shadow strength 0-1")
    ap.add_argument("--shadow-len", type=float, default=0.12, help="max shadow length, fraction of width")
    ap.add_argument("--deglare", type=float, default=0.0, help="lamp veiling-glare removal 0-1")
    ap.add_argument("--lamp-thr", type=float, default=0.85, help="linear luminance of a lamp core")
    ap.add_argument("--blacklevel", type=float, default=0.0, help="black-level/contrast recovery on person 0-1")
    ap.add_argument("--face-key", type=float, default=0.0, help="extra side key + rim on face 0-2")
    ap.add_argument("--lens", type=float, default=0.0, help="spectacle-lens veil removal 0-1 (needs --face-npz)")
    ap.add_argument("--lips", type=float, default=0.0, help="mute over-red lips 0-1 (needs --face-npz)")
    ap.add_argument("--face-fill", type=float, default=0.0, help="soft even shadow lift on face 0-1")
    ap.add_argument("--fill-key", type=float, default=0.04, help="slight key direction in face fill 0-0.1")
    ap.add_argument("--hair", type=float, default=0.0, help="neutralise purple hair shadows 0-1")
    ap.add_argument("--relief", type=float, default=1.0, help="3D volume strength 0-2")
    ap.add_argument("--shade-ema", type=float, default=0.8)
    ap.add_argument("--depth-ema", type=float, default=0.7)
    ap.add_argument("--depth-res", type=int, default=512)
    ap.add_argument("--seconds", type=float, default=0)
    ap.add_argument("--max-width", type=int, default=1920)
    ap.add_argument("--sam", default=r"D:\0_Dev\CUTORA\runtime\models\matte_sam21_base_plus",
                    help="SAM 2.1 folder (read-only); empty string = OpenCV fallback")
    ap.add_argument("--seg-every", type=int, default=5)
    ap.add_argument("--depth-model", default=r"D:\0_LLM\tools\relight\models\dpt-hybrid-midas")
    ap.add_argument("--ffmpeg", default=r"D:\0_Dev\CUTORA\runtime\ffmpeg\ffmpeg.exe")
    ap.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto",
                    help="auto = GPU only if >=3 GiB free (GPU is shared)")
    ap.add_argument("--iclight", action="store_true", help="IC-Light keyframe refinement if installed")
    ap.add_argument("--debug", action="store_true", help="also write mask/normal preview")
    a = ap.parse_args()
    for k in ("strength", "softness", "skin_keep", "window", "shade_ema", "depth_ema"):
        setattr(a, k, float(np.clip(getattr(a, k), 0, 1)))
    a.temp = float(np.clip(a.temp, 1800, 12000)); a.fill_temp = float(np.clip(a.fill_temp, 1800, 12000))
    a.relief = float(np.clip(a.relief, 0, 2)); a.face_key = float(np.clip(a.face_key, 0, 2)); a.fill_key = float(np.clip(a.fill_key, 0, 0.1)); a.shadow_len = float(np.clip(a.shadow_len, 0.01, 0.4))
    for k in ("face_weight", "mg_weight", "bg_key", "shadow", "deglare", "blacklevel", "lamp_thr", "lens", "hair", "lips", "face_fill"):
        setattr(a, k, float(np.clip(getattr(a, k), 0, 1)))
    a.contrast = float(np.clip(a.contrast, 0.5, 5))
    for k in ("key", "fill", "rim", "ambient"):
        setattr(a, k, float(np.clip(getattr(a, k), 0, 3)))

    src = check_path(a.input, True)
    os.makedirs(a.outdir, exist_ok=True)
    name = a.name or "%s_%s_%dK" % (os.path.splitext(os.path.basename(src))[0][:24], a.light, a.temp)
    if not re.match(r"^[\w\-.]+$", name):
        raise SystemExit("bad --name")
    out = check_path(os.path.join(a.outdir, name + ".mp4"), False)
    sbs = check_path(os.path.join(a.outdir, name + "_sbs.mp4"), False)
    if os.path.normcase(out) == os.path.normcase(src) or os.path.normcase(sbs) == os.path.normcase(src):
        raise SystemExit("refusing to overwrite input")
    ffmpeg = a.ffmpeg
    ffprobe = os.path.join(os.path.dirname(ffmpeg), "ffprobe.exe" if ffmpeg.lower().endswith(".exe") else "ffprobe")
    W, H, fps, has_audio = probe(ffprobe, src)
    if W > a.max_width:
        H = int(round(H * a.max_width / W / 2) * 2); W = a.max_width
    log("input %dx%d @ %.3f fps, audio=%s" % (W, H, fps, has_audio))

    import torch
    dev = a.device
    if dev == "auto":
        dev = "cpu"
        if torch.cuda.is_available():
            free, _ = torch.cuda.mem_get_info()
            dev = "cuda" if free > 3 * 2**30 else "cpu"
            log("GPU free %.1f GiB -> %s" % (free / 2**30, dev))
    if dev == "cpu":
        torch.set_num_threads(max(1, (os.cpu_count() or 4) // 2))
    depth = Depth(a.depth_model, dev, a.depth_res, a.depth_ema)
    seg = Segmenter(a.sam, dev, a.seg_every)

    rd = [ffmpeg, "-v", "error"]
    if a.seconds > 0:
        rd += ["-t", "%.3f" % a.seconds]
    rd += ["-i", src, "-vf", "scale=%d:%d" % (W, H), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    tmp = out + ".video.mp4"
    wr = [ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "%dx%d" % (W, H),
          "-r", "%.6f" % fps, "-i", "-", "-c:v", "libx264", "-crf", "16", "-preset", "medium",
          "-pix_fmt", "yuv420p", tmp]
    pr = subprocess.Popen(rd, stdout=subprocess.PIPE)
    pw = subprocess.Popen(wr, stdin=subprocess.PIPE)
    fb = W * H * 3
    prevS = None
    prevW = None
    dg_state = {}
    FN = FA = MG = MGI = FL = FB = FLP = None
    if a.face_npz:
        z = np.load(check_path(a.face_npz, True)); FN, FA = z["normals"], z["alpha"]
        FL = z["lens"] if "lens" in z.files else None
        FLP = z["lips"] if "lips" in z.files else None
        FB = z["box"] if "box" in z.files else None
        log("face normals:", FN.shape[0], "frames")
    if a.normals_npz:
        z = np.load(check_path(a.normals_npz, True)); MG, MGI = z["normals"], z["index"]
        log("Marigold keyframes:", len(MGI))
    i = 0
    t0 = time.time()
    dbg = None
    while True:
        buf = pr.stdout.read(fb)
        if len(buf) < fb:
            break
        bgr = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255
        lin = srgb_to_lin(rgb).astype(np.float32)
        person = seg.person(bgr, i)
        lin, lampm, glare = deglare(lin, person, a, dg_state)
        if FL is not None and i < len(FL):
            lin = fix_lenses(lin, cv2.resize(FL[i].astype(np.float32), (W, H)), a.lens)
        if FLP is not None and i < len(FLP):
            lin = fix_lips(lin, cv2.resize(FLP[i].astype(np.float32), (W, H)), a.lips, dg_state)
        if a.face_fill > 0 and FN is not None and i < len(FN):
            lin = face_fill(lin, FA[i].astype(np.float32), FN[i].astype(np.float32), person, a, dg_state)
        if a.hair > 0:
            fbox = FB[i] * (W / (FN.shape[2] * 4)) if FB is not None and i < len(FB) else None
            lin = fix_hair_shadows(lin, person, skin_mask(bgr, person), fbox, a.hair)
        if a.strength > 0:  # relighting; cleanup-only runs skip depth/shading entirely
            disp = depth(rgb)
            n_small = normals_from_disparity(disp, 2.0, person, a.relief)
            ds = cv2.resize(disp, (W // 4, H // 4))
            n = cv2.resize(n_small, (W // 4, H // 4))
            if MG is not None:  # Marigold keyframes, linearly interpolated between neighbours
                j = int(np.searchsorted(MGI, i, side="right") - 1); j = max(0, min(j, len(MGI) - 1))
                j2 = min(j + 1, len(MGI) - 1)
                t = 0.0 if j2 == j else float(np.clip((i - MGI[j]) / max(MGI[j2] - MGI[j], 1), 0, 1))
                m = (1 - t) * MG[j].astype(np.float32) + t * MG[j2].astype(np.float32)
                m = cv2.resize(m, (W // 4, H // 4))
                n = blend_normals(n, m / (np.linalg.norm(m, axis=2, keepdims=True) + 1e-6),
                                  np.full(n.shape[:2], a.mg_weight, np.float32))
            if FN is not None and i < len(FN):
                fa = cv2.resize(FA[i].astype(np.float32), (W // 4, H // 4)) * a.face_weight
                n = blend_normals(n, cv2.resize(FN[i].astype(np.float32), (W // 4, H // 4)), fa)
            n_dbg = n
            fa_full = None
            if FN is not None and i < len(FN):
                fa_full = cv2.resize(FA[i].astype(np.float32), (W // 4, H // 4))
            ps = cv2.resize(person, (W // 4, H // 4))
            S = shading(n, ds, ps, a, fa_full)
            S = cv2.GaussianBlur(S, (0, 0), 1.0 + 4 * a.softness)
            if prevS is not None:
                S = a.shade_ema * prevS + (1 - a.shade_ema) * S
            prevS = S
            Sf = cv2.resize(S, (W, H), interpolation=cv2.INTER_LINEAR)
        else:
            disp = np.zeros((H // 4, W // 4), np.float32)
            n_dbg = np.dstack([disp, disp, disp + 1])
            Sf = np.ones((H, W, 3), np.float32)
        sk = skin_mask(bgr, person)
        o = apply_shading(lin, Sf, sk, a.strength, a.skin_keep, a.contrast)
        wm, prevW = window_mask(lin, person, a.window_thr, prevW)
        o = compress_window(o, wm, a.window)
        ob = (lin_to_srgb(o) * 255 + 0.5).astype(np.uint8)
        pw.stdin.write(cv2.cvtColor(ob, cv2.COLOR_RGB2BGR).tobytes())
        if a.debug and i == 30:
            nv = ((cv2.resize(n_dbg, (W, H)) * 0.5 + 0.5) * 255).astype(np.uint8)
            dv = cv2.applyColorMap((np.clip(cv2.resize(disp, (W, H)), 0, 1) * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
            mv = np.dstack([wm * 255, person * 255, sk * 255]).astype(np.uint8)
            sv = (np.clip(lin_to_srgb(Sf / Sf.max()), 0, 1) * 255).astype(np.uint8)
            top = np.hstack([bgr, cv2.cvtColor(ob, cv2.COLOR_RGB2BGR)])
            bot = np.hstack([cv2.cvtColor(nv, cv2.COLOR_RGB2BGR), dv])
            bot2 = np.hstack([mv, cv2.cvtColor(sv, cv2.COLOR_RGB2BGR)])
            cv2.imwrite(os.path.join(a.outdir, name + "_debug.jpg"), cv2.resize(np.vstack([top, bot, bot2]), None, fx=0.5, fy=0.5))
        i += 1
        if i % 60 == 0:
            vram = torch.cuda.max_memory_allocated() / 2**20 if dev == "cuda" else 0
            log("frame %d  %.2f fps  peak torch VRAM %.0f MiB" % (i, i / (time.time() - t0), vram))
    pw.stdin.close(); pw.wait(); pr.stdout.close(); pr.wait()
    el = time.time() - t0
    log("processed %d frames in %.1fs (%.2f fps)" % (i, el, i / max(el, 1e-6)))

    mux = [ffmpeg, "-v", "error", "-y", "-i", tmp]
    if has_audio:
        if a.seconds > 0:
            mux += ["-t", "%.3f" % a.seconds]
        mux += ["-i", src, "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "192k", "-shortest"]
    mux += ["-c:v", "copy", out]
    subprocess.run(mux, check=True)
    os.remove(tmp)
    sb = [ffmpeg, "-v", "error", "-y"]
    sb += (["-t", "%.3f" % a.seconds] if a.seconds > 0 else []) + ["-i", src, "-i", out, "-filter_complex",
          "[0:v]scale=%d:%d,setsar=1[a];[1:v]setsar=1[b];[a][b]hstack=inputs=2[v]" % (W, H),
          "-map", "[v]"] + (["-map", "1:a?"]) + ["-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy", sbs]
    subprocess.run(sb, check=True)
    if a.iclight:
        ok, _, _ = iclight_ready(r"D:\0_LLM\tools\IC-Light")
        log("IC-Light refinement:", "not implemented in this build (keyframe hook only)" if ok else "venv not ready, skipped")
    log("done:", out); log("side-by-side:", sbs)

if __name__ == "__main__":
    main()
