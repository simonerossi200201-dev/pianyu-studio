"""Benchmark MiniMax H3 configs against the local ComfyUI.

Usage: python bench.py NAME WIDTH HEIGHT LENGTH STEPS LORA(4|8) SPARSE(none|tau) [last_frame:0|1]
Writes one JSON line per run into bench/results.jsonl with wall time and per-step time
parsed from the ComfyUI log.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

URL = "http://127.0.0.1:8188"
ROOT = Path("D:/0_LLM")
LOG = ROOT / "logs" / "comfyui_bench.log"
RESULTS = ROOT / "bench" / "results.jsonl"

LORAS = {
    "4": "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors",
    "8": "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors",
}
PROMPT = (
    "一镜到底，固定机位，画面中始终只有这一个人，不切换镜头，不出现其他人物或画面。"
    "人物安静地坐着，专注地看向镜头前方，在认真聆听和思考，表情沉稳、平静，嘴巴自然闭合，不说话。"
    "只有自然眨眼、轻微呼吸起伏，偶尔微微点头，手指偶尔向上轻点一下。背景保持不变，环境安静。"
)


def build(width, height, length, steps, lora, sparse, last_frame, seed=12345, prefix="bench/run"):
    model = ["5", 0]
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", "type": "minimax", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_video_vae_int8_convrot.safetensors"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_audio_vae_fp32.safetensors"}},
        "5": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": LORAS[lora], "strength_model": 1.0}},
        "6": {"class_type": "LoadImage", "inputs": {"image": "studio_input.png"}},
        "7": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
            "clip": ["2", 0], "vae": ["3", 0], "prompt": PROMPT,
            "width": width, "height": height, "length": length, "first_frame": ["6", 0]}},
        "8": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "9": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "13": {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["12", 0]}},
        "14": {"class_type": "VAEDecodeTiled", "inputs": {"samples": ["13", 0], "vae": ["3", 0], "tile_size": 512, "overlap": 64, "temporal_size": 16, "temporal_overlap": 4}},
        "15": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["13", 1], "vae": ["4", 0]}},
        "16": {"class_type": "CreateVideo", "inputs": {"images": ["14", 0], "fps": 24.0, "audio": ["15", 0]}},
        "17": {"class_type": "SaveVideo", "inputs": {"video": ["16", 0], "filename_prefix": prefix, "format": "mp4", "format.codec": "h264"}},
    }
    if last_frame:
        wf["7"]["inputs"]["last_frame"] = ["6", 0]
    if sparse != "none":
        wf["20"] = {"class_type": "BlockSparseAttention", "inputs": {
            "model": ["5", 0], "selection": "sol-attn", "selection.tau": float(sparse),
            "start_percent": 0.0, "end_percent": 1.0, "dense_blocks": "", "min_tokens": 12288,
            "extra_tokens": 256, "sink_conditioning": "exact_kv_and_rows", "verbose": True}}
        model = ["20", 0]
    wf["10"] = {"class_type": "BasicScheduler", "inputs": {"model": model, "scheduler": "simple", "steps": steps, "denoise": 1.0}}
    wf["11"] = {"class_type": "BasicGuider", "inputs": {"model": model, "conditioning": ["7", 0]}}
    wf["12"] = {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["8", 0], "guider": ["11", 0], "sampler": ["9", 0], "sigmas": ["10", 0], "latent_image": ["7", 1]}}
    return wf


def post(wf):
    req = urllib.request.Request(f"{URL}/prompt", data=json.dumps({"prompt": wf}).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())["prompt_id"]
    except urllib.error.HTTPError as e:
        raise SystemExit(e.read().decode(errors="replace"))


def wait(pid):
    while True:
        with urllib.request.urlopen(f"{URL}/history/{pid}", timeout=60) as r:
            h = json.loads(r.read())
        if pid in h:
            return h[pid]
        time.sleep(3)


def main():
    name, w, h, length, steps, lora, sparse = sys.argv[1:8]
    last = len(sys.argv) > 8 and sys.argv[8] == "1"
    wf = build(int(w), int(h), int(length), int(steps), lora, sparse, last,
               seed=int(os.environ.get("BENCH_SEED", "12345")), prefix=f"bench/{name}")
    log_start = LOG.stat().st_size
    t0 = time.time()
    entry = wait(post(wf))
    wall = time.time() - t0
    text = LOG.read_bytes()[log_start:].decode("utf-8", errors="replace")
    per_step = [float(x) for x in re.findall(r"([\d.]+)s/it\]", text)]
    status = (entry.get("status") or {}).get("status_str")
    res = {"name": name, "w": w, "h": h, "len": length, "steps": steps, "lora": lora, "sparse": sparse,
           "last": last, "wall_s": round(wall, 1), "s_per_it_last": per_step[-1] if per_step else None, "status": status}
    with RESULTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(res, ensure_ascii=False) + "\n")
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
