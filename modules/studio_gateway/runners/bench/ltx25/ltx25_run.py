# -*- coding: utf-8 -*-
"""LTX-2.5 22B local video runner (two-stage distilled pipeline + Multimodal Gemma-4 Director Prompt Enhancer + DualCFG + STG)."""
import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


ap = argparse.ArgumentParser()
ap.add_argument("--mode", default="i2v")
ap.add_argument("--w", type=int, default=None)
ap.add_argument("--h", type=int, default=None)
PRESETS = {
    "fast": (576, 1024),
    "standard": (640, 1152),
    "hq": (768, 1344),
    "fast-land": (1024, 576),
    "standard-land": (1152, 640),
    "hq-land": (1344, 768),
}
ap.add_argument("--preset", choices=sorted(PRESETS), default=None, help="named resolution; --w/--h override it")
ap.add_argument("--neg_extra", default="", help="extra comma-separated negative prompt terms")
ap.add_argument("--frames", type=int, default=241)
ap.add_argument("--seed", type=int, default=0)
ap.add_argument("--name", required=True)
ap.add_argument("--image", default="ltx25_ref_first.png")
ap.add_argument("--unet", default="ltx-2.5-22b-distilled-transformer-nvfp4.safetensors")
ap.add_argument("--tile", type=int, default=512)
ap.add_argument("--tframes", type=int, default=64)
ap.add_argument("--prompt_file", required=True)
ap.add_argument("--enhance_prompt", type=int, default=1, help="1=use Gemma-4 Multimodal Director Prompt Enhancer, 0=raw prompt")
ap.add_argument("--video_cfg", type=float, default=2.0, help="Stage 1 video CFG scale")
ap.add_argument("--audio_cfg", type=float, default=1.5, help="Stage 1 audio CFG scale")
ap.add_argument("--stg_scale", type=float, default=0.5, help="Spatio-temporal guidance scale for motion coherence")
a = ap.parse_args()

pw, ph = PRESETS[a.preset] if a.preset else (576, 1024)
a.w = a.w if a.w is not None else pw
a.h = a.h if a.h is not None else ph
if a.w % 64 or a.h % 64:
    sys.exit(f"width/height must be multiples of 64 (got {a.w}x{a.h})")
if (a.frames - 1) % 8:
    sys.exit(f"frames must be 8n+1 (got {a.frames})")

if not a.seed or a.seed <= 0:
    a.seed = random.randint(10000, 99999999)

URL = "http://127.0.0.1:8188"
raw_pos = open(a.prompt_file, encoding="utf-8").read().strip()

# Detect if user prompt explicitly forbids speaking / requests silent listening
is_silent_listening = any(
    kw in raw_pos
    for kw in ("禁止人物说话", "不说话", "不要说话", "嘴唇闭合", "嘴巴自然闭合", "无语音", "does not speak", "no speech", "No dialogue")
)

base_neg = (
    "pc game, console game, video game, cartoon, childish, ugly, subtitles, text, watermark, "
    "distorted face, deformed hands, extra fingers, sudden jerky movements, awkward clapping, "
    "robotic motion, frozen statue, motionless photo, blank stare, camera shake, morphing background"
)
if is_silent_listening:
    base_neg += (
        ", speaking, talking, moving lips, parting lips, open mouth, showing teeth, "
        "mouthing words, singing, mumbling, chatter, voiceover, narration, background music"
    )
else:
    base_neg += ", mumbling, garbled speech"

neg = base_neg + (", " + a.neg_extra if a.neg_extra else "")

LTX25_DIRECTOR_SYSTEM_PROMPT = """You are given a REFERENCE IMAGE (the exact first frame of the video) and a user's image-to-video request (often in Chinese). Write a single, highly detailed English audio-visual screenplay paragraph (160-210 words) in the EXACT training caption style of LTX-2.5 that brings the character to life with natural, warm, continuous, lifelike human behavior from the first second to the very last second.

CRITICAL RULES FOR NATURAL HUMAN PERFORMANCE & SEMANTIC FIDELITY:
1. FIRST-FRAME GROUNDING: Inspect the reference image carefully. Begin immediately by describing the exact shot type (e.g. medium shot or medium close-up), camera viewpoint, the character's appearance, glasses, clothing, current hand/arm position, any object they hold or look at (smartphone, tablet, laptop, book), and the background setting. Never use "The scene opens" or "We see".
2. STRICT NEGATION & EXAMPLE HANDLING:
   - If the user says "禁止说话 / 不说话 / 嘴唇闭合 / 无语音" (no speaking / silent listening), state clearly in positive observable terms that the character's lips remain gently closed in a warm, serene, attentive smile throughout the entire clip, never parting to speak. Describe the soundscape as quiet, peaceful interior room tone with no speech, no narration, and no music.
   - If the user gives conditional examples with "比如说...或者...或者..." (e.g., "for example hands together, or resting chin on hand, or hand on chest"), DO NOT perform awkward clapping or sudden jerky gestures! Instead, choose ONLY the single most natural, subtle, continuous movement that flows organically from the character's actual starting hand posture in the reference image (for example, if holding a phone or resting a hand near the face, keep that posture and add gentle, subtle finger/hand relaxation or a light touch to the chin or glasses frame).
3. CONTINUOUS 3-PHASE MICRO-ACTION ARC (NEVER FREEZE LIKE A PHOTO):
   To prevent the video from freezing after the first few seconds, you MUST choreograph a continuous, gentle, unhurried 3-phase progression across the full duration using temporal transitions:
   - Phase 1 ("Initially, ..."): Natural rhythmic breathing, soft warm eye contact with bright, engaged eyes, a gentle natural blink, and a serene closed-mouth smile while attentively watching.
   - Phase 2 ("A moment later, ... / Midway through, ..."): A slow, thoughtful, gentle nod of agreement (1-2 subtle nods), accompanied by a soft micro-expression of warmth and quiet reflection in the eyes and brows, and a very gentle, organic micro-adjustment of the hand, posture, or glasses frame consistent with the starting pose.
   - Phase 3 ("As the moment continues, ... / Gradually toward the end, ..."): Another soft blink, a slight warm shift of the head or shoulders with natural breathing, maintaining an affectionate, reverent, attentive gaze and a gentle closed-mouth smile right up to the final frame.
4. CAMERA & BACKGROUND: If the user requests a static camera ("固定静止镜头") and static background ("背景始终保持静止不动"), explicitly state that the camera remains completely static on a locked-off tripod and the background remains tranquil and still.
5. Output ONLY a single continuous English paragraph (160-210 words) with cinematic lighting and crisp film-grade texture descriptors woven naturally into the prose."""

S1 = "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"
S2 = "0.85, 0.7250, 0.4219, 0.0"
byp = (a.mode == "t2v")

has_cjk = bool(re.search(r"[\u4e00-\u9fff]", raw_pos))
use_enhancer = bool(a.enhance_prompt) and (has_cjk or len(raw_pos.split()) < 90)

P = {
    "1": {"class_type": "LoadImage", "inputs": {"image": a.image}},
    "2": {"class_type": "ImageScale", "inputs": {"image": ["1", 0], "upscale_method": "lanczos", "width": a.w, "height": a.h, "crop": "center"}},
    "3": {"class_type": "LTXVPreprocess", "inputs": {"image": ["2", 0], "img_compression": 12}},
    "4": {"class_type": "UNETLoader", "inputs": {"unet_name": a.unet, "weight_dtype": "default"}},
    "4b": {
        "class_type": "LTXVSpatioTemporalGuidance",
        "inputs": {"model": ["4", 0], "scale": a.stg_scale, "blocks": "29", "start_percent": 0.0, "end_percent": 0.85},
    },
    "5": {
        "class_type": "CLIPLoader",
        "inputs": {"clip_name": "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors", "type": "ltxv", "device": "default"},
    },
    "6": {"class_type": "VAELoader", "inputs": {"vae_name": "ltx-2.5-video-vae-bf16.safetensors"}},
    "7": {"class_type": "VAELoader", "inputs": {"vae_name": "ltx-2.5-audio-vae-bf16.safetensors"}},
    "8": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["5", 0], "text": raw_pos}},
    "9": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["5", 0], "text": neg}},
    "10": {"class_type": "LTXVConditioning", "inputs": {"positive": ["8", 0], "negative": ["9", 0], "frame_rate": 24.0}},
    "11": {"class_type": "EmptyLTXVLatentVideo", "inputs": {"width": a.w // 2, "height": a.h // 2, "length": a.frames, "batch_size": 1}},
    "12": {"class_type": "LTXVImgToVideoInplace", "inputs": {"vae": ["6", 0], "image": ["3", 0], "latent": ["11", 0], "strength": 0.68, "bypass": byp}},
    "13": {"class_type": "LTXVEmptyLatentAudio", "inputs": {"frames_number": a.frames, "frame_rate": 24, "batch_size": 1, "audio_vae": ["7", 0]}},
    "14": {"class_type": "LTXVConcatAVLatent", "inputs": {"video_latent": ["12", 0], "audio_latent": ["13", 0]}},
    "15": {"class_type": "RandomNoise", "inputs": {"noise_seed": a.seed}},
    "16": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler_ancestral"}},
    "17": {"class_type": "ManualSigmas", "inputs": {"sigmas": S1}},
    "18": {
        "class_type": "LTXVDualCFGGuider",
        "inputs": {"model": ["4b", 0], "positive": ["10", 0], "negative": ["10", 1], "video_cfg": a.video_cfg, "audio_cfg": a.audio_cfg},
    },
    "19": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["15", 0], "guider": ["18", 0], "sampler": ["16", 0], "sigmas": ["17", 0], "latent_image": ["14", 0]}},
    "20": {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["19", 0]}},
    "21": {"class_type": "LatentUpscaleModelLoader", "inputs": {"model_name": "ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors"}},
    "22": {"class_type": "LTXVLatentUpsampler", "inputs": {"samples": ["20", 0], "upscale_model": ["21", 0], "vae": ["6", 0]}},
    "23": {"class_type": "LTXVImgToVideoInplace", "inputs": {"vae": ["6", 0], "image": ["3", 0], "latent": ["22", 0], "strength": 0.90, "bypass": byp}},
    "24": {"class_type": "LTXVConcatAVLatent", "inputs": {"video_latent": ["23", 0], "audio_latent": ["20", 1]}},
    "25": {"class_type": "RandomNoise", "inputs": {"noise_seed": a.seed + 1}},
    "26": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler_ancestral"}},
    "27": {"class_type": "ManualSigmas", "inputs": {"sigmas": S2}},
    "28": {
        "class_type": "LTXVDualCFGGuider",
        "inputs": {"model": ["4", 0], "positive": ["10", 0], "negative": ["10", 1], "video_cfg": 1.2, "audio_cfg": 1.0},
    },
    "29": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["25", 0], "guider": ["28", 0], "sampler": ["26", 0], "sigmas": ["27", 0], "latent_image": ["24", 0]}},
    "30": {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["29", 0]}},
    "31": {"class_type": "VAEDecodeTiled", "inputs": {"samples": ["30", 0], "vae": ["6", 0], "tile_size": a.tile, "overlap": 64, "temporal_size": a.tframes, "temporal_overlap": 16}},
    "32": {"class_type": "LTXVAudioVAEDecode", "inputs": {"samples": ["30", 1], "audio_vae": ["7", 0]}},
    "33": {"class_type": "CreateVideo", "inputs": {"images": ["31", 0], "fps": 24.0, "audio": ["32", 0]}},
    "34": {"class_type": "SaveVideo", "inputs": {"video": ["33", 0], "filename_prefix": "ltx25_test/" + a.name, "format": "mp4", "format.codec": "h264"}},
}

if use_enhancer:
    P["5_enh"] = {
        "class_type": "CLIPLoader",
        "inputs": {"clip_name": "gemma4_e2b_it_int8_convrot.safetensors", "type": "ltxv", "device": "default"},
    }
    P["7_sys"] = {
        "class_type": "PrimitiveStringMultiline",
        "inputs": {"value": LTX25_DIRECTOR_SYSTEM_PROMPT},
    }
    enh_inputs = {
        "clip": ["5_enh", 0],
        "prompt": raw_pos,
        "max_length": 260,
        "sampling_mode": {
            "sampling_mode": "on",
            "temperature": 0.35,
            "top_k": 40,
            "top_p": 0.9,
            "min_p": 0.05,
            "repetition_penalty": 1.12,
            "seed": a.seed,
        },
        "thinking": False,
        "use_default_template": True,
        "mtp": "auto",
        "system_prompt": ["7_sys", 0],
    }
    if not byp:
        enh_inputs["image"] = ["2", 0]
    P["7_enh"] = {
        "class_type": "TextGenerateLTX2Prompt",
        "inputs": enh_inputs,
    }
    P["8"]["inputs"]["text"] = ["7_enh", 0]

if byp:
    P["1"]["inputs"]["image"] = a.image  # image is ignored when bypass=True

peak = {"mem": 0, "pw": 0.0, "sm": 0}
stop = False


def mon():
    while not stop:
        try:
            o = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,power.draw,clocks.sm", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip().split(",")
            peak["mem"] = max(peak["mem"], int(float(o[0])))
            peak["pw"] = max(peak["pw"], float(o[1]))
            peak["sm"] = max(peak["sm"], int(float(o[2])))
        except Exception:
            pass
        time.sleep(2)


def req(path, data=None):
    r = urllib.request.Request(
        URL + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(r, timeout=60).read())


th = threading.Thread(target=mon, daemon=True)
th.start()
t0 = time.time()
try:
    pid = req("/prompt", {"prompt": P, "client_id": str(uuid.uuid4())})["prompt_id"]
except urllib.error.HTTPError as e:
    print("SUBMIT_ERROR", e.read().decode()[:3000])
    sys.exit(2)

while True:
    time.sleep(4)
    h = req("/history/" + pid)
    if pid in h:
        st = h[pid].get("status", {})
        break

_LTX_ROOT = Path(__file__).resolve().parents[5]
_PORTABLE_COMFY = _LTX_ROOT / "runtime" / "comfyui"
_COMFY_ROOT = _PORTABLE_COMFY if (_PORTABLE_COMFY / "main.py").is_file() else Path(r"D:\0_LLM\ComfyUI")
_COMFY_OUT = _COMFY_ROOT / "output"
_PORTABLE_OUT = _LTX_ROOT / "data" / "gateway_outputs" / "ltx25_test"
_DEV_OUT = Path(r"D:\0_LLM\Outputs\ltx25_test")

stop = True
wall = time.time() - t0
files = []
for nid, out in h[pid].get("outputs", {}).items():
    for k in ("images", "gifs", "videos", "files"):
        for f in out.get(k, []) or []:
            cand_p = _COMFY_OUT / f.get("subfolder", "") / f["filename"]
            if not cand_p.is_file() and Path(r"D:\0_LLM\ComfyUI\output").is_dir():
                cand_p = Path(r"D:\0_LLM\ComfyUI\output") / f.get("subfolder", "") / f["filename"]
            files.append(str(cand_p))

res = {
    "name": a.name,
    "preset": a.preset,
    "mode": a.mode,
    "w": a.w,
    "h": a.h,
    "frames": a.frames,
    "seed": a.seed,
    "enhanced": use_enhancer,
    "video_cfg": a.video_cfg,
    "stg_scale": a.stg_scale,
    "unet": a.unet,
    "status": st.get("status_str"),
    "wall_s": round(wall, 1),
    "peak_vram_mib": peak["mem"],
    "peak_power_w": peak["pw"],
    "peak_sm_mhz": peak["sm"],
    "files": files,
}
if st.get("status_str") != "success":
    res["error"] = [m for m in st.get("messages", []) if m[0] == "execution_error"]

_PORTABLE_OUT.mkdir(parents=True, exist_ok=True)
target_dirs = [_PORTABLE_OUT]
if _DEV_OUT.parent.is_dir():
    _DEV_OUT.mkdir(parents=True, exist_ok=True)
    target_dirs.append(_DEV_OUT)

for f in files:
    if os.path.exists(f):
        for td in target_dirs:
            try:
                shutil.copy2(f, td)
            except Exception:
                pass
with open(_PORTABLE_OUT / "results.jsonl", "a", encoding="utf-8") as fh:
    fh.write(json.dumps(res) + "\n")
print(json.dumps(res)[:4000])

