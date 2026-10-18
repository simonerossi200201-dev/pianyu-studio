# -*- coding: utf-8 -*-
"""face_pass.py - per-frame face normal maps from MediaPipe Face Landmarker (Google, Apache-2.0).
Run with D:\\0_LLM\\tools\\venv_mediapipe python. Offline: uses a local .task model file.
Writes an .npz with float16 normals (N,h,w,3) and alpha (N,h,w) at 1/4 resolution, which
relight.py blends into its depth normals. No shell=True, no eval; ffmpeg via argument list."""
import argparse, os, subprocess, sys, time
import numpy as np, cv2
import mediapipe as mp
from mediapipe.tasks import python as mpt
from mediapipe.tasks.python import vision

ap = argparse.ArgumentParser()
ap.add_argument("input"); ap.add_argument("out")
ap.add_argument("--model", default=r"D:\0_LLM\tools\relight\models\mediapipe\face_landmarker.task")
ap.add_argument("--ffmpeg", default=r"D:\0_Dev\CUTORA\runtime\ffmpeg\ffmpeg.exe")
ap.add_argument("--width", type=int, required=True); ap.add_argument("--height", type=int, required=True)
ap.add_argument("--fps", type=float, default=30.0); ap.add_argument("--seconds", type=float, default=0)
ap.add_argument("--ema", type=float, default=0.5)
a = ap.parse_args()
W, H = a.width, a.height; w4, h4 = W // 4, H // 4
opts = vision.FaceLandmarkerOptions(base_options=mpt.BaseOptions(model_asset_path=a.model),
                                    running_mode=vision.RunningMode.VIDEO, num_faces=2)
lm = vision.FaceLandmarker.create_from_options(opts)
cmd = [a.ffmpeg, "-v", "error"] + (["-t", "%.3f" % a.seconds] if a.seconds > 0 else []) + \
      ["-i", a.input, "-vf", "scale=%d:%d" % (W, H), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
pr = subprocess.Popen(cmd, stdout=subprocess.PIPE)
LEYE = [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246]
REYE = [263, 249, 390, 373, 374, 380, 381, 382, 362, 398, 384, 385, 386, 387, 388, 466]
LIPS_OUT = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270, 269, 267, 0, 37, 39, 40, 185]
LIPS_IN = [78, 95, 88, 178, 87, 14, 317, 402, 318, 324, 308, 415, 310, 311, 312, 13, 82, 81, 80, 191]
lipsl = []
tri = None; prev = {}; normals = []; alphas = []; lenses = []; boxes = []; i = 0; t0 = time.time(); hits = 0
while True:
    buf = pr.stdout.read(W * H * 3)
    if len(buf) < W * H * 3: break
    rgb = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
    res = lm.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), int(i * 1000 / a.fps))
    nmap = np.zeros((h4, w4, 3), np.float32); amap = np.zeros((h4, w4), np.float32)
    lip = np.zeros((H // 2, W // 2), np.float32)  # half res for clean lip edges
    lmap = np.zeros((h4, w4), np.float32); box = np.array([-1, -1, -1, -1], np.float32)
    for fi, face in enumerate(res.face_landmarks or []):
        P = np.array([[p.x * w4, p.y * h4, -p.z * w4] for p in face], np.float32)
        if fi in prev and prev[fi].shape == P.shape:
            P = a.ema * prev[fi] + (1 - a.ema) * P
        prev[fi] = P
        if tri is None:  # triangulate once on the first face (topology is fixed in MediaPipe)
            sub = cv2.Subdiv2D((-w4, -h4, 3 * w4, 3 * h4))
            for x, y, _ in P: sub.insert((float(x), float(y)))
            pts = {(round(float(x), 3), round(float(y), 3)): k for k, (x, y, _) in enumerate(P)}
            T = []
            for t in sub.getTriangleList():
                ks = [pts.get((round(float(t[j]), 3), round(float(t[j + 1]), 3))) for j in (0, 2, 4)]
                if None not in ks: T.append(ks)
            tri = np.array(T, np.int32)
        A, B, C = P[tri[:, 0]], P[tri[:, 1]], P[tri[:, 2]]
        fn = np.cross(B - A, C - A); fn[fn[:, 2] < 0] *= -1
        vn = np.zeros_like(P)
        for j in range(3): np.add.at(vn, tri[:, j], fn)
        vn /= np.linalg.norm(vn, axis=1, keepdims=True) + 1e-6
        tn = vn[tri].mean(1); tn /= np.linalg.norm(tn, axis=1, keepdims=True) + 1e-6
        for k, t in enumerate(tri):
            poly = P[t, :2].astype(np.int32)
            cv2.fillConvexPoly(nmap, poly, tn[k].tolist()); cv2.fillConvexPoly(amap, poly, 1.0)
        for E in (LEYE, REYE):  # spectacle lens ~ ellipse around each eye (larger than the eye)
            e = P[E, :2]; c = e.mean(0); ew = np.ptp(e[:, 0])
            cv2.ellipse(lmap, (int(c[0]), int(c[1] + 0.1 * ew)), (int(ew * 0.95), int(ew * 0.8)), 0, 0, 360, 1.0, -1)
        cv2.fillPoly(lip, [(P[LIPS_OUT, :2] * 2).astype(np.int32)], 1.0)
        cv2.fillPoly(lip, [(P[LIPS_IN, :2] * 2).astype(np.int32)], 0.0)  # keep teeth / mouth interior
        if fi == 0:
            box = np.array([P[:, 0].min(), P[:, 1].min(), P[:, 0].max(), P[:, 1].max()], np.float32) * 4
        hits += 1
    sig = max(1.0, w4 / 120)
    nmap = cv2.GaussianBlur(nmap, (0, 0), sig); amap = cv2.GaussianBlur(amap, (0, 0), sig * 2)
    nmap /= np.linalg.norm(nmap, axis=2, keepdims=True) + 1e-6
    lmap = cv2.GaussianBlur(lmap, (0, 0), sig)
    normals.append(nmap.astype(np.float16)); alphas.append(amap.astype(np.float16))
    lip = cv2.GaussianBlur(lip, (0, 0), max(1.0, W / 900))  # feathered edge
    lipsl.append(lip.astype(np.float16))
    lenses.append(lmap.astype(np.float16)); boxes.append(box); i += 1
pr.stdout.close(); pr.wait()
np.savez_compressed(a.out, normals=np.stack(normals), alpha=np.stack(alphas), lens=np.stack(lenses), lips=np.stack(lipsl),
                    box=np.stack(boxes))
print("frames %d, faces found in %d, %.1fs" % (i, hits, time.time() - t0))
