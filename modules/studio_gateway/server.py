"""片语工作室统一本地 AI 算力与跨窗口信号网关 (127.0.0.1:7870)。

核心职责：
1. 100% 兼容 Orante 2.0 `StudioEngine` 的 `/v1/health`, `/v1/videos`, `/v1/videos/{id}`, `/v1/videos/{id}/file` 协议，
   并将底层本地视频生成引擎全面切换为 **LTX-2.5 22B (NVFP4 + 同步音轨)**（按要求不包含 MiniMax H3 权重，仅保留接口参数兼容）。
2. 将 `D:\\0_LLM` 中已验证的全部本地工具链统一封装为标准 REST 接口供 Orante、CUTORA 与 Robota 调用：
   - 视频生成：LTX-2.5 22B (`i2v` / `t2v`, `fast` / `standard` / `hq`)
   - 视频特效：LTX-2.5 IC-LoRA 7件套 (`restore`, `deblur`, `clean_plate`, `colorization`, `inpaint`, `sdr2hdr`, `refine`)
   - 人像修复：Simone 标准人像精修管线 (`face_pass` -> `relight v6h` -> `fill` -> `denoise medium`)
   - 自动调色：`autograde.py`（直接产出调色成片 + `.cube` 3D LUT 供 CUTORA 加载）
   - 视频超分：`Nomos2` 4x 逐帧放大
   - 图片生成与抠图：`FLUX.2 [klein] 4B` 生图/改图、`RMBG-2.0` 一键抠图
   - 音频 AI 六件套：`Resemble Enhance` 降噪增强、`Demucs` 分轨、`Chatterbox`/`XTTS` 克隆配音、`Stable Audio`/`MusicGen` 配乐
3. 内置 `HardwareGuard`（32GB+CPU 自动降级与友好提示）与 `VRAMBroker`（16GB 显存互斥清场锁）。
4. 挂载 `SpineDB` 跨窗口信号总线接口（任务表直通、缺片回流、出片重命名上传）。
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

LTX_ROOT = Path(__file__).resolve().parents[2]
if str(LTX_ROOT) not in sys.path:
    sys.path.insert(0, str(LTX_ROOT))

from core import BIN_DIR, DATA_DIR, load_studio_config  # noqa: E402
from core.hardware_guard import (  # noqa: E402
    check_ram_watermark,
    evaluate_all_models,
    evaluate_model_capability,
    probe_hardware,
)
from core.spine_db import SpineDB  # noqa: E402
from core.vram_broker import vram_exclusive_session  # noqa: E402

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
HOST = "127.0.0.1"
PORT = int(os.environ.get("STUDIO_API_PORT", "7870"))
COMFY_URL = "http://127.0.0.1:8188"
OUT_DIR = DATA_DIR / "gateway_outputs"
_JID_RE = re.compile(r"^[a-f0-9]{8,32}$")


def _resolve_comfy_root() -> Path:
    portable = LTX_ROOT / "runtime" / "comfyui"
    if (portable / "main.py").is_file():
        return portable
    return Path(r"D:\0_LLM\ComfyUI")


def _resolve_comfy_py() -> Path:
    c_root = _resolve_comfy_root()
    cand = c_root / "venv" / "Scripts" / "python.exe"
    if cand.is_file():
        return cand
    dev_cand = Path(r"D:\0_LLM\ComfyUI\venv\Scripts\python.exe")
    if dev_cand.is_file():
        return dev_cand
    studio_venv = LTX_ROOT / "runtime" / "venv" / "Scripts" / "python.exe"
    if studio_venv.is_file():
        return studio_venv
    return Path(sys.executable)


def _validate_local_file(raw_path: Any, label: str = "输入文件") -> Path:
    """防御 UNC/SMB 哈希泄露与非法路径穿越，确保输入为有效本地文件。"""
    s = str(raw_path or "").strip()
    if not s:
        raise ValueError(f"{label}路径为空")
    if s.startswith("\\\\") or s.startswith("//"):
        raise ValueError(f"安全拦截：禁止访问远程 UNC 共享路径 ({label})")
    p = Path(s).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"{label}不存在: {p}")
    return p


COMFY_ROOT = _resolve_comfy_root()
COMFY_PY = _resolve_comfy_py()
COMFY_INPUT = COMFY_ROOT / "input"

# LTX-2.5 质量预设映射 (完美兼容 Orante 传入的 fast/balanced/camera/high/best 参数)
QUALITY_TO_LTX25_PRESET = {
    "fast": "fast",
    "balanced": "standard",
    "standard": "standard",
    "camera": "standard",
    "high": "hq",
    "best": "hq",
    "hq": "hq",
}
WALL_PER_SEC = {"fast": 14, "balanced": 15, "standard": 15, "camera": 16, "high": 25, "best": 26, "hq": 26}

log = logging.getLogger("studio_gateway")
SPINE = SpineDB()
_COMFY_LAUNCH_LOCK = threading.Lock()


def comfy_alive(timeout: float = 2.0) -> bool:
    try:
        req = urllib.request.Request(f"{COMFY_URL}/system_stats", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def ensure_comfy_running(wait_seconds: float = 95.0) -> bool:
    """若本地 ComfyUI (127.0.0.1:8188) 未启动，则自动拉起并挂载 runtime/models 权重目录。"""
    if comfy_alive(timeout=2.0):
        return True
    c_root = _resolve_comfy_root()
    c_py = _resolve_comfy_py()
    main_py = c_root / "main.py"
    if not (c_py.is_file() and main_py.is_file()):
        return False
    with _COMFY_LAUNCH_LOCK:
        if comfy_alive(timeout=2.0):
            return True
        log.info("正在唤醒本地 ComfyUI 推理引擎 (127.0.0.1:8188)...")
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        cmd = [
            str(c_py),
            "-u",
            str(main_py),
            "--listen",
            "127.0.0.1",
            "--port",
            "8188",
            "--disable-pinned-memory",
            "--preview-method",
            "none",
        ]
        extra_paths_yaml = LTX_ROOT / "config" / "comfy_extra_model_paths.yaml"
        if extra_paths_yaml.is_file():
            cmd.extend(["--extra-model-paths-config", str(extra_paths_yaml)])
        try:
            subprocess.Popen(
                cmd,
                cwd=str(c_root),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception as exc:
            log.warning("启动 ComfyUI 失败: %s", exc)
            return False

    if wait_seconds <= 0:
        return False
    t0 = time.time()
    while time.time() - t0 < wait_seconds:
        time.sleep(1.5)
        if comfy_alive(timeout=2.0):
            log.info("本地 ComfyUI 推理引擎已就绪 (127.0.0.1:8188)")
            return True
    return comfy_alive(timeout=2.0)



def seconds_to_ltx25_frames(seconds: float, fps: int = 24) -> int:
    """LTX-2.5 帧数规则：必须是 8n + 1（例如 15s @ 24fps = 361 帧）。"""
    raw = max(25, int(round(seconds * fps)))
    n = max(3, int(round((raw - 1) / 8.0)))
    return min(361, 8 * n + 1)


class GatewayJobStore:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.q: "queue.Queue[str]" = queue.Queue()
        self.running_id: str | None = None

    def enqueue(self, kind: str, params: dict[str, Any], model_id: str) -> dict[str, Any]:
        verdict = evaluate_model_capability(model_id)
        jid = uuid.uuid4().hex[:12]
        rec = {
            "id": jid,
            "kind": kind,
            "model_id": model_id,
            "engine": "ltx25" if kind == "video" else model_id,
            "state": "queued" if verdict.can_run else "failed",
            "progress": 0.0,
            "error": "" if verdict.can_run else verdict.user_message,
            "execution_device": verdict.execution_device,
            "hardware_badge": verdict.badge,
            "user_hint": verdict.user_message,
            "cloud_fallback_available": verdict.cloud_fallback_available,
            "width": params.get("width", 640),
            "height": params.get("height", 1152),
            "frames": params.get("frames", 193),
            "seconds_out": round(params.get("frames", 193) / 24.0, 2),
            "seed": int(params.get("seed") or 0),
            "quality": params.get("quality", "balanced"),
            "aspect": params.get("aspect", "9:16"),
            "client_ref": params.get("client_ref", ""),
            "created": time.time(),
            "started": None,
            "finished": None if verdict.can_run else time.time(),
            "file": None,
            "extra_outputs": {},
            "_params": params,
            "_cancel": False,
        }
        with self.lock:
            self.jobs[jid] = rec
        if verdict.can_run:
            self.q.put(jid)
        return rec

    def get(self, jid: str) -> dict[str, Any] | None:
        with self.lock:
            return self.jobs.get(jid)

    def queued_count(self) -> int:
        with self.lock:
            return sum(1 for r in self.jobs.values() if r["state"] == "queued")


JOBS = GatewayJobStore()


def _public_job(rec: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    started = rec.get("started")
    elapsed = (rec.get("finished") or now) - started if started else 0.0
    out = {k: v for k, v in rec.items() if not k.startswith("_")}
    out["elapsed"] = round(elapsed, 1)
    out["waited"] = round((started or now) - rec["created"], 1)
    return out


def _resolve_runner(rel_path: str, dev_fallback: str) -> Path:
    local_p = LTX_ROOT / "modules" / "studio_gateway" / "runners" / rel_path
    if local_p.is_file():
        return local_p
    return Path(dev_fallback)


def _resolve_tool_py(dev_venv_py: str) -> Path:
    dev_p = Path(dev_venv_py)
    if dev_p.is_file():
        return dev_p
    return _resolve_comfy_py()


def _run_ltx25_video(rec: dict[str, Any]) -> None:
    """通过 VRAMBroker 清场后调用 LTX-2.5 22B 生成带同步音轨的视频。"""
    import random
    ensure_comfy_running(wait_seconds=95.0)
    p = rec["_params"]
    preset = QUALITY_TO_LTX25_PRESET.get(p["quality"], "standard")
    if p.get("aspect") == "16:9" and not preset.endswith("-land"):
        preset = f"{preset}-land"
    frames = seconds_to_ltx25_frames(float(p.get("seconds", 8.0)))
    rec["frames"] = frames
    rec["seconds_out"] = round(frames / 24.0, 2)

    comfy_py = _resolve_comfy_py()
    comfy_input = _resolve_comfy_root() / "input"
    runner = _resolve_runner("bench/ltx25/ltx25_run.py", r"D:\0_LLM\bench\ltx25\ltx25_run.py")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    prompt_file = OUT_DIR / f"prompt_{rec['id']}.txt"
    prompt_text = p.get("prompt") or f"{p.get('direction', '')}\n{p.get('line', '')}".strip()
    prompt_file.write_text(prompt_text, encoding="utf-8")

    raw_seed = p.get("seed")
    run_seed = int(raw_seed) if (raw_seed and int(raw_seed) > 0) else random.randint(10000, 99999999)
    rec["seed"] = run_seed

    mode = "i2v" if p.get("image_path") else "t2v"
    cmd = [
        str(comfy_py),
        str(runner),
        "--name", f"ltx_{rec['id']}",
        "--prompt_file", str(prompt_file),
        "--preset", preset,
        "--mode", mode,
        "--frames", str(frames),
        "--seed", str(run_seed),
    ]
    if mode == "i2v" and p.get("image_path"):
        src_img = _validate_local_file(p["image_path"], "参考首帧图")
        comfy_input.mkdir(parents=True, exist_ok=True)
        staged_name = f"gw_ref_{rec['id']}{src_img.suffix.lower() or '.png'}"
        shutil.copy2(src_img, comfy_input / staged_name)
        cmd.extend(["--image", staged_name])

    with vram_exclusive_session(f"ltx25:{rec['id']}", required_gb=14.5):
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["HF_HUB_OFFLINE"] = "1"
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            creationflags=CREATE_NO_WINDOW,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"LTX-2.5 执行失败: {(proc.stderr or proc.stdout)[-400:]}")

    # 查找最新生成的 mp4 (优先便携输出目录，回退 D:\0_LLM\Outputs)
    search_dirs = [
        OUT_DIR / "ltx25_test",
        _resolve_comfy_root() / "output" / "ltx25_test",
        Path(r"D:\0_LLM\Outputs\ltx25_test"),
    ]
    candidates: list[Path] = []
    for sdir in search_dirs:
        if sdir.is_dir():
            candidates.extend(sdir.glob(f"*ltx_{rec['id']}*.mp4"))
    candidates.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    if candidates:
        rec["file"] = str(candidates[0])
        rec["progress"] = 1.0
    else:
        raise RuntimeError("未找到 LTX-2.5 输出成片文件")


def _run_portrait_restore_v6h(rec: dict[str, Any]) -> None:
    """运行 Simone 批准的人像修复标准流程：face_pass -> relight (v6h) -> fill -> denoise (medium)。
    支持在 32GB 内存 + CPU 电脑上运行！
    """
    p = rec["_params"]
    input_video = _validate_local_file(p.get("video_path"), "输入视频")

    work_dir = OUT_DIR / f"portrait_{rec['id']}"
    work_dir.mkdir(parents=True, exist_ok=True)
    faces_npz = work_dir / "faces.npz"
    relight_out = work_dir / "step1_relight.mp4"
    fill_out = work_dir / "step2_fill.mp4"
    final_out = work_dir / f"{input_video.stem}_v6h_denoised.mp4"

    mp_py = _resolve_tool_py(r"D:\0_LLM\tools\venv_mediapipe\Scripts\python.exe")
    comfy_py = _resolve_comfy_py()
    local_relight = LTX_ROOT / "modules" / "studio_gateway" / "runners" / "tools" / "relight"
    relight_dir = local_relight if local_relight.is_dir() else Path(r"D:\0_LLM\tools\relight")

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["HF_HUB_OFFLINE"] = "1"

    # Step 1: MediaPipe face_pass.py (CPU 极快)
    rec["progress"] = 0.15
    subprocess.run(
        [str(mp_py), str(relight_dir / "face_pass.py"), str(input_video), str(faces_npz)],
        check=True,
        env=env,
        creationflags=CREATE_NO_WINDOW,
    )

    # Step 2: relight.py (v6h 标准参数：调镜片/头发/嘴唇/去眩光/黑电平，显存不足3GB自动走CPU)
    rec["progress"] = 0.40
    subprocess.run(
        [
            str(comfy_py),
            str(relight_dir / "relight.py"),
            str(input_video),
            "-o", str(relight_out),
            "--face-npz", str(faces_npz),
            "--strength", "0",
            "--window", "0",
            "--face-key", "0",
            "--deglare", "0.001",
            "--blacklevel", "0.2",
            "--lens", "0.6",
            "--hair", "1.0",
            "--lips", "1.0",
        ],
        check=True,
        env=env,
        creationflags=CREATE_NO_WINDOW,
    )

    # Step 3: fill.py (比例图脸部补光)
    rec["progress"] = 0.65
    subprocess.run(
        [
            str(comfy_py),
            str(relight_dir / "fill.py"),
            str(relight_out),
            str(faces_npz),
            str(fill_out),
            "--k", "0.3",
            "--rmax", "1.5",
            "--gain", "1.17",
        ],
        check=True,
        env=env,
        creationflags=CREATE_NO_WINDOW,
    )

    # Step 4: denoise.py (medium 标准光流时域降噪，纯 OpenCV/CPU 原生)
    rec["progress"] = 0.85
    subprocess.run(
        [
            str(comfy_py),
            str(relight_dir / "denoise.py"),
            str(fill_out),
            str(final_out),
            "--radius", "2",
            "--strength", "1.0",
            "--thr", "2.5",
            "--chroma", "0.8",
            "--luma-nl", "2.5",
            "--face-npz", str(faces_npz),
            "--face-strength", "0.5",
        ],
        check=True,
        env=env,
        creationflags=CREATE_NO_WINDOW,
    )

    rec["file"] = str(final_out)
    rec["extra_outputs"] = {"faces_npz": str(faces_npz)}
    rec["progress"] = 1.0


def _run_autograde(rec: dict[str, Any]) -> None:
    """运行 autograde.py 自动调色并产出 .cube 3D LUT（可直接喂入 CUTORA 调色轨）。"""
    p = rec["_params"]
    input_video = _validate_local_file(p.get("video_path"), "输入视频")

    out_video = OUT_DIR / f"graded_{rec['id']}_{input_video.name}"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ag_py = _resolve_tool_py(r"D:\0_LLM\tools\autograde\venv\Scripts\python.exe")
    ag_script = _resolve_runner("tools/autograde/autograde.py", r"D:\0_LLM\tools\autograde\autograde.py")

    cmd = [
        str(ag_py),
        str(ag_script),
        str(input_video),
        "-o", str(out_video),
        "--strength", str(float(p.get("strength", 0.8))),
        "--deflicker",
    ]
    if p.get("reference_image"):
        ref_img = _validate_local_file(p["reference_image"], "参考图")
        cmd.extend(["--reference", str(ref_img)])

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    subprocess.run(cmd, check=True, env=env, creationflags=CREATE_NO_WINDOW)
    rec["file"] = str(out_video)
    rec["progress"] = 1.0


def _run_flux2_image(rec: dict[str, Any]) -> None:
    """运行 FLUX.2 [klein] 4B 生图或改图。"""
    ensure_comfy_running(wait_seconds=95.0)
    p = rec["_params"]
    comfy_py = _resolve_comfy_py()
    runner = _resolve_runner("bench/flux2/flux2_run.py", r"D:\0_LLM\bench\flux2\flux2_run.py")
    out_name = f"flux2_{rec['id']}"

    cmd = [
        str(comfy_py),
        str(runner),
        "--prompt", str(p.get("prompt") or ""),
        "--out-name", out_name,
        "--w", str(int(p.get("width", 1088))),
        "--h", str(int(p.get("height", 1920))),
        "--steps", str(int(p.get("steps", 4))),
        "--seed", str(int(p.get("seed", 0))),
    ]
    if p.get("edit_image"):
        edit_img = _validate_local_file(p["edit_image"], "待编辑图像")
        cmd.extend(["--edit-image", str(edit_img)])

    with vram_exclusive_session(f"flux2:{rec['id']}", required_gb=12.5):
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["HF_HUB_OFFLINE"] = "1"
        subprocess.run(cmd, check=True, env=env, creationflags=CREATE_NO_WINDOW)

    for search_dir in (
        OUT_DIR / "flux2_test",
        _resolve_comfy_root() / "output",
        Path(r"D:\0_LLM\Outputs\flux2_test"),
        Path(r"D:\0_LLM\ComfyUI\output"),
    ):
        if search_dir.is_dir():
            cands = sorted(search_dir.glob(f"*{out_name}*.png"), key=lambda x: x.stat().st_mtime, reverse=True)
            if cands:
                rec["file"] = str(cands[0])
                break
    rec["progress"] = 1.0


def _run_ic_lora(rec: dict[str, Any]) -> None:
    """运行 LTX-2.5 IC-LoRA 7件套视频修复/特效（restore / deblur / clean_plate / colorization / inpaint / sdr2hdr / refine）。"""
    ensure_comfy_running(wait_seconds=95.0)
    p = rec["_params"]
    comfy_py = _resolve_comfy_py()
    runner = _resolve_runner("bench/ltx25/ltx25_v2v_run.py", r"D:\0_LLM\bench\ltx25\ltx25_v2v_run.py")
    allowed_modes = {"restore", "deblur", "clean_plate", "colorization", "inpaint", "sdr2hdr", "refine"}
    task_mode = str(p.get("mode") or "restore")
    if task_mode not in allowed_modes:
        task_mode = "restore"
    input_video = _validate_local_file(p.get("video_path"), "输入视频")
    cmd = [
        str(comfy_py),
        str(runner),
        "--video", str(input_video),
        "--mode", task_mode,
        "--name", f"iclora_{task_mode}_{rec['id']}",
        "--prompt", str(p.get("prompt") or "high quality clear video, natural lighting, sharp details"),
    ]
    if p.get("mask_path"):
        mask_p = _validate_local_file(p["mask_path"], "遮罩文件")
        cmd.extend(["--mask", str(mask_p)])
    with vram_exclusive_session(f"iclora:{rec['id']}", required_gb=14.5):
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["HF_HUB_OFFLINE"] = "1"
        subprocess.run(cmd, check=True, env=env, creationflags=CREATE_NO_WINDOW)
    for out_search in (OUT_DIR / "ltx25_test", _resolve_comfy_root() / "output", Path(r"D:\0_LLM\Outputs\ltx25_test")):
        if out_search.is_dir():
            cands = sorted(out_search.glob(f"*iclora_{task_mode}_{rec['id']}*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True)
            if cands:
                rec["file"] = str(cands[0])
                break
    rec["progress"] = 1.0


def _run_nomos2_upscale(rec: dict[str, Any]) -> None:
    """运行 Nomos2 4x 视频或图片超分放大。"""
    p = rec["_params"]
    comfy_py = _resolve_comfy_py()
    runner = _resolve_runner("tools/upscale/upscale_video.py", r"D:\0_LLM\tools\upscale\upscale_video.py")
    inp = _validate_local_file(p.get("input_path") or p.get("video_path") or p.get("image_path"), "待超分文件")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUT_DIR / f"nomos2_{rec['id']}_{inp.name}"
    with vram_exclusive_session(f"nomos2:{rec['id']}", required_gb=6.0):
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        subprocess.run(
            [str(comfy_py), str(runner), str(inp), str(out_file)],
            check=True,
            env=env,
            creationflags=CREATE_NO_WINDOW,
        )
    rec["file"] = str(out_file)
    rec["progress"] = 1.0


def _run_audio_enhance(rec: dict[str, Any]) -> None:
    """运行 Resemble Enhance 人声去混响与降噪增强。"""
    p = rec["_params"]
    re_py = _resolve_tool_py(r"D:\0_LLM\tools\venv_resemble\Scripts\python.exe")
    re_script = _resolve_runner("tools/audio_ai/enhance_voice.py", r"D:\0_LLM\tools\audio_ai\enhance_voice.py")
    inp = _validate_local_file(p.get("audio_path") or p.get("video_path"), "输入音视频")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUT_DIR / f"enhanced_{rec['id']}.wav"
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    subprocess.run(
        [str(re_py), str(re_script), str(inp), str(out_file)],
        check=True,
        env=env,
        creationflags=CREATE_NO_WINDOW,
    )
    rec["file"] = str(out_file)
    rec["progress"] = 1.0


def _run_rmbg(rec: dict[str, Any]) -> None:
    """运行 RMBG-2.0 抠图去背景。"""
    p = rec["_params"]
    inp = _validate_local_file(p.get("image_path"), "待抠图图片")
    out_dir = OUT_DIR / f"rmbg_{rec['id']}"
    out_dir.mkdir(parents=True, exist_ok=True)
    rmbg_py = _resolve_tool_py(r"D:\0_LLM\tools\venv_rmbg\Scripts\python.exe")
    rmbg_script = _resolve_runner("tools/rmbg/rmbg.py", r"D:\0_LLM\tools\rmbg\rmbg.py")

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["HF_HUB_OFFLINE"] = "1"
    subprocess.run(
        [str(rmbg_py), str(rmbg_script), str(inp), str(out_dir)],
        check=True,
        env=env,
        creationflags=CREATE_NO_WINDOW,
    )
    pngs = list(out_dir.glob("*_nobg.png"))
    if pngs:
        rec["file"] = str(pngs[0])
    rec["progress"] = 1.0


def _worker_loop() -> None:
    """单显卡串行队列执行器，严格保障显存与 32GB 内存安全。"""
    while True:
        try:
            jid = JOBS.q.get(timeout=0.5)
        except queue.Empty:
            continue
        rec = JOBS.get(jid)
        if not rec or rec["state"] != "queued":
            continue
        rec["state"] = "running"
        rec["started"] = time.time()
        JOBS.running_id = jid
        try:
            kind = rec["kind"]
            if kind == "video":
                _run_ltx25_video(rec)
            elif kind == "portrait_restore":
                _run_portrait_restore_v6h(rec)
            elif kind == "autograde":
                _run_autograde(rec)
            elif kind == "flux2_image":
                _run_flux2_image(rec)
            elif kind == "ic_lora":
                _run_ic_lora(rec)
            elif kind == "nomos2_upscale":
                _run_nomos2_upscale(rec)
            elif kind == "audio_enhance":
                _run_audio_enhance(rec)
            elif kind == "rmbg":
                _run_rmbg(rec)
            rec["state"] = "done"
        except Exception as exc:
            rec["state"] = "failed"
            rec["error"] = str(exc)[:600]
            log.warning("Job %s (%s) failed: %s", jid, rec["kind"], rec["error"])
        finally:
            rec["finished"] = time.time()
            JOBS.running_id = None


def build_app() -> FastAPI:
    app = FastAPI(title="片语工作室 · 本地 AI 算力与信号网关 (LTX-2.5 Edition)", version="3.0.0")

    @app.middleware("http")
    async def localhost_security_guard(request: Request, call_next):
        """防御跨站请求伪造 (CSRF) 与 DNS Rebinding 攻击，仅允许本机进程与扩展访问。"""
        host_hdr = (request.headers.get("host") or "").split(":")[0].lower()
        if host_hdr and host_hdr not in ("127.0.0.1", "localhost", "testserver"):
            return JSONResponse({"ok": False, "error": "Forbidden host header"}, status_code=403)
        origin = (request.headers.get("origin") or "").lower()
        if origin and not (
            origin.startswith("http://127.0.0.1")
            or origin.startswith("http://localhost")
            or origin.startswith("chrome-extension://")
        ):
            return JSONResponse({"ok": False, "error": "Forbidden cross-origin request"}, status_code=403)
        return await call_next(request)

    # ---------------- 1. 兼容 Orante StudioEngine 的标准接口 ----------------
    @app.get("/v1/health")
    def health() -> dict[str, Any]:
        prof = probe_hardware()
        comfy_ok = comfy_alive()
        return {
            "ok": True,
            "status": "ok",
            "version": "3.0.0-ltx25",
            "active_video_engine": "ltx-2.5-22b",
            "minimax_h3_bundled": False,  # 明确排除 MiniMax H3 打包
            "comfy": comfy_ok,
            "comfy_reachable": comfy_ok,
            "comfy_root": str(_resolve_comfy_root()),
            "busy": JOBS.running_id is not None,
            "queued": JOBS.queued_count(),
            "presets": list(QUALITY_TO_LTX25_PRESET.keys()),
            "hardware": prof.to_dict(),
        }

    @app.post("/v1/videos")
    async def submit_video(req: Request) -> JSONResponse:
        try:
            body = await req.json()
        except Exception:
            body = {}
        # 无论上游 Orante 传什么旧参数，统一映射到 LTX-2.5 22B（不运行 MiniMax H3）
        rec = JOBS.enqueue("video", body, model_id="ltx-2.5-22b")
        status_code = 200 if rec["state"] == "queued" else 409
        return JSONResponse(_public_job(rec), status_code=status_code)

    @app.get("/v1/videos/{jid}")
    def video_status(jid: str) -> JSONResponse:
        if not _JID_RE.fullmatch(jid):
            return JSONResponse({"ok": False, "error": "非法任务 ID"}, status_code=400)
        rec = JOBS.get(jid)
        if not rec:
            return JSONResponse({"ok": False, "error": "任务不存在"}, status_code=404)
        return JSONResponse(_public_job(rec))

    @app.get("/v1/videos/{jid}/file")
    def video_file(jid: str):
        if not _JID_RE.fullmatch(jid):
            return JSONResponse({"ok": False, "error": "非法任务 ID"}, status_code=400)
        rec = JOBS.get(jid)
        if not rec or rec["state"] != "done" or not rec.get("file") or not Path(rec["file"]).is_file():
            return JSONResponse({"ok": False, "error": "成片尚未就绪"}, status_code=409)
        return FileResponse(rec["file"], media_type="video/mp4", filename=f"{jid}.mp4")

    # ---------------- 2. 硬件自适应分级与 32GB+CPU 体检接口 ----------------
    @app.get("/v1/hardware")
    def hardware_matrix() -> dict[str, Any]:
        from core.pack_manager import evaluate_all_packs
        res = evaluate_all_models()
        res["ram_watermark"] = check_ram_watermark()
        res["packs"] = evaluate_all_packs()
        return res

    @app.get("/v1/packs")
    def list_packs() -> dict[str, Any]:
        from core.pack_manager import evaluate_all_packs
        return evaluate_all_packs()

    @app.post("/v1/packs/install")
    async def install_pack_endpoint(req: Request) -> JSONResponse:
        from core.pack_manager import find_inbox_archive, install_pack_archive
        body = await req.json() if req.headers.get("content-length") else {}
        archive_path = str(body.get("archive_path") or "").strip()
        pack_id = str(body.get("pack_id") or "").strip()
        if not archive_path and pack_id:
            found = find_inbox_archive(pack_id)
            if found:
                archive_path = str(found)
        if not archive_path:
            return JSONResponse({"ok": False, "error": "请提供扩展包路径 archive_path 或将 .ltxpack 放入 packs/ 目录"}, status_code=400)
        try:
            valid_arch = _validate_local_file(archive_path, "扩展包归档")
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        res = install_pack_archive(valid_arch)
        return JSONResponse(res, status_code=200 if res.get("ok") else 400)

    @app.post("/v1/packs/auto_install")
    def auto_install_inbox_packs() -> dict[str, Any]:
        from core.pack_manager import scan_and_auto_install_inbox
        installed = scan_and_auto_install_inbox()
        return {"ok": True, "installed": installed}

    # ---------------- 3. 本地视觉与音频 AI 工具链接口 ----------------
    @app.post("/v1/vfx/portrait_restore")
    async def submit_portrait_restore(req: Request) -> dict[str, Any]:
        body = await req.json()
        rec = JOBS.enqueue("portrait_restore", body, model_id="portrait-restore-v6h")
        return _public_job(rec)

    @app.post("/v1/vfx/autograde")
    async def submit_autograde(req: Request) -> dict[str, Any]:
        body = await req.json()
        rec = JOBS.enqueue("autograde", body, model_id="autograde-lut")
        return _public_job(rec)

    @app.post("/v1/vfx/ic_lora")
    async def submit_ic_lora(req: Request) -> dict[str, Any]:
        body = await req.json()
        rec = JOBS.enqueue("ic_lora", body, model_id="ltx-2.5-22b")
        return _public_job(rec)

    @app.post("/v1/vfx/nomos2_upscale")
    async def submit_nomos2_upscale(req: Request) -> dict[str, Any]:
        body = await req.json()
        rec = JOBS.enqueue("nomos2_upscale", body, model_id="nomos2-4x")
        return _public_job(rec)

    @app.post("/v1/audio/enhance")
    async def submit_audio_enhance(req: Request) -> dict[str, Any]:
        body = await req.json()
        rec = JOBS.enqueue("audio_enhance", body, model_id="resemble-enhance")
        return _public_job(rec)

    @app.post("/v1/images")
    async def submit_flux2_image(req: Request) -> dict[str, Any]:
        body = await req.json()
        rec = JOBS.enqueue("flux2_image", body, model_id="flux2-klein-4b")
        return _public_job(rec)

    @app.post("/v1/images/rmbg")
    async def submit_rmbg(req: Request) -> dict[str, Any]:
        body = await req.json()
        rec = JOBS.enqueue("rmbg", body, model_id="rmbg-2.0")
        return _public_job(rec)

    # ---------------- 3b. Image Studio (AI 视觉制图与批量精修工坊) REST 接口 ----------------
    @app.post("/v1/images/studio/batch_gen")
    async def image_studio_batch_gen_endpoint(req: Request) -> JSONResponse:
        from core import image_studio
        body = await req.json() if req.headers.get("content-length") else {}
        proj_dir = body.get("project_dir") or str(image_studio.DEFAULT_WORKSPACE_ROOT)
        try:
            res = image_studio.generate_items(
                proj_dir=proj_dir,
                prompts_text=str(body.get("prompts_text") or body.get("prompt") or ""),
                items_spec=body.get("items_spec"),
                aspects=body.get("aspects") or [body.get("aspect", "9:16")],
                count_per_prompt=int(body.get("count_per_prompt") or 1),
                engine=str(body.get("engine") or "flux2"),
                default_typography=body.get("typography"),
                run_now=bool(body.get("run_now", True)),
                steps=int(body.get("steps") or 4),
                mock=bool(body.get("mock", False)),
            )
            if body.get("send_to_orante_task"):
                res["orante_export"] = image_studio.export_to_orante_task(
                    proj_dir=proj_dir,
                    task_id_or_dir=str(body["send_to_orante_task"]),
                    use_composed=bool(body.get("use_composed", False)),
                )
            return JSONResponse(res)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.post("/v1/images/studio/inpaint")
    async def image_studio_inpaint_endpoint(req: Request) -> JSONResponse:
        from core import image_studio
        body = await req.json() if req.headers.get("content-length") else {}
        proj_dir = body.get("project_dir") or str(image_studio.DEFAULT_WORKSPACE_ROOT)
        try:
            res = image_studio.inpaint_item(
                proj_dir=proj_dir,
                item_id=str(body["item_id"]),
                mask_b64_or_path=body.get("mask") or body.get("mask_b64") or body.get("mask_path") or "",
                prompt=str(body.get("prompt") or ""),
                mode=str(body.get("mode") or "inpaint"),
                denoise=float(body.get("denoise") or 0.75),
                use_ai=bool(body.get("use_ai", True)),
                mock=bool(body.get("mock", False)),
            )
            return JSONResponse(res)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.post("/v1/images/studio/outpaint")
    async def image_studio_outpaint_endpoint(req: Request) -> JSONResponse:
        from core import image_studio
        body = await req.json() if req.headers.get("content-length") else {}
        proj_dir = body.get("project_dir") or str(image_studio.DEFAULT_WORKSPACE_ROOT)
        try:
            if body.get("item_ids"):
                res = image_studio.batch_outpaint(
                    proj_dir=proj_dir,
                    item_ids=body.get("item_ids"),
                    target_aspect=str(body.get("target_aspect") or "9:16"),
                    align=str(body.get("align") or "center"),
                    prompt=str(body.get("prompt") or ""),
                    use_ai=bool(body.get("use_ai", False)),
                    mock=bool(body.get("mock", False)),
                )
            else:
                res = image_studio.outpaint_item(
                    proj_dir=proj_dir,
                    item_id=str(body["item_id"]),
                    target_aspect=str(body.get("target_aspect") or "9:16"),
                    target_w=int(body.get("target_w") or 0),
                    target_h=int(body.get("target_h") or 0),
                    align=str(body.get("align") or "center"),
                    prompt=str(body.get("prompt") or ""),
                    pad_top=int(body.get("pad_top") or 0),
                    pad_bottom=int(body.get("pad_bottom") or 0),
                    pad_left=int(body.get("pad_left") or 0),
                    pad_right=int(body.get("pad_right") or 0),
                    use_ai=bool(body.get("use_ai", False)),
                    mock=bool(body.get("mock", False)),
                )
            return JSONResponse(res)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.post("/v1/images/studio/matte")
    async def image_studio_matte_endpoint(req: Request) -> JSONResponse:
        from core import image_studio
        body = await req.json() if req.headers.get("content-length") else {}
        proj_dir = body.get("project_dir") or str(image_studio.DEFAULT_WORKSPACE_ROOT)
        try:
            if body.get("preview_only") and body.get("points"):
                img_p = body.get("image_path") or (image_studio.load_project(proj_dir) and image_studio.ImageStudioManager(proj_dir).get_item(str(body["item_id"])))
                res = image_studio.segment_points_preview(img_p, points=body["points"], proj_dir=proj_dir)
            else:
                res = image_studio.matte_item(
                    proj_dir=proj_dir,
                    item_id=str(body["item_id"]),
                    mode=str(body.get("mode") or "rmbg"),
                    points=body.get("points"),
                    bg_mode=str(body.get("bg_mode") or "transparent"),
                    bg_color=str(body.get("bg_color") or "#000000"),
                    prefer_fast=bool(body.get("prefer_fast", False)),
                )
            return JSONResponse(res)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.post("/v1/images/studio/typography")
    async def image_studio_typography_endpoint(req: Request) -> JSONResponse:
        from core import image_studio
        body = await req.json() if req.headers.get("content-length") else {}
        proj_dir = body.get("project_dir") or str(image_studio.DEFAULT_WORKSPACE_ROOT)
        try:
            overrides = body.get("typography") if isinstance(body.get("typography"), dict) else {
                k: v for k, v in body.items() if k not in ("project_dir", "item_id")
            }
            res = image_studio.render_typography(
                proj_dir=proj_dir,
                item_or_id=str(body["item_id"]),
                overrides=overrides,
                save_project_file=True,
            )
            return JSONResponse(res)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.post("/v1/images/studio/inspect")
    async def image_studio_inspect_endpoint(req: Request) -> JSONResponse:
        from core import image_studio
        body = await req.json() if req.headers.get("content-length") else {}
        try:
            if body.get("image_path"):
                valid_img = _validate_local_file(body["image_path"], "待检测图片")
                res = image_studio.detect_faces_and_qc(valid_img, expected_aspect=str(body.get("aspect") or ""))
                res["ok"] = True
                return JSONResponse(res)
            proj_dir = body.get("project_dir") or str(image_studio.DEFAULT_WORKSPACE_ROOT)
            data = image_studio.load_project(proj_dir)
            if body.get("item_id"):
                it = image_studio.ImageStudioManager(proj_dir).get_item(str(body["item_id"]))
                return JSONResponse({"ok": True, "project_dir": str(proj_dir), "item": it})
            return JSONResponse({"ok": True, "project_dir": str(proj_dir), "project": data})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/v1/jobs/{jid}")
    def any_job_status(jid: str) -> JSONResponse:
        if not _JID_RE.fullmatch(jid):
            return JSONResponse({"ok": False, "error": "非法任务 ID"}, status_code=400)
        rec = JOBS.get(jid)
        if not rec:
            return JSONResponse({"ok": False, "error": "任务不存在"}, status_code=404)
        return JSONResponse(_public_job(rec))

    @app.get("/v1/jobs/{jid}/file")
    def any_job_file(jid: str):
        if not _JID_RE.fullmatch(jid):
            return JSONResponse({"ok": False, "error": "非法任务 ID"}, status_code=400)
        rec = JOBS.get(jid)
        if not rec or rec["state"] != "done" or not rec.get("file") or not Path(rec["file"]).is_file():
            return JSONResponse({"ok": False, "error": "输出文件尚未就绪"}, status_code=409)
        fp = Path(rec["file"])
        return FileResponse(str(fp), filename=fp.name)


    # ---------------- 4. 跨窗口实时信号总线接口 (Orante ⇄ CUTORA ⇄ 表格工具 ⇄ Robota) ----------------
    @app.get("/v1/spine/events")
    def get_spine_events(since_id: int = 0, target: str = "*") -> dict[str, Any]:
        return {"ok": True, "events": SPINE.poll_events(since_id=since_id, target=target)}

    @app.post("/v1/spine/stitch_table")
    async def export_orante_table_for_stitch(req: Request) -> dict[str, Any]:
        body = await req.json() if req.headers.get("content-length") else {}
        bdir = Path(body["batch_dir"]) if body.get("batch_dir") else None
        return SPINE.export_batch_for_cutora_stitch(
            batch_dir=bdir,
            batch_date=str(body.get("batch_date") or ""),
            batch_no=str(body.get("batch_no") or ""),
        )

    @app.post("/v1/spine/gap_report")
    async def push_gap_report_from_cutora(req: Request) -> dict[str, Any]:
        body = await req.json()
        return SPINE.report_stitch_gaps_to_orante(
            task_id=str(body["task_id"]),
            task_dir=Path(str(body["task_dir"])),
            missing_entries=body.get("missing_entries") or [],
            missing_segments=body.get("missing_segments") or [],
            auto_regenerate=body.get("auto_regenerate"),
        )

    @app.post("/v1/spine/render_completed")
    async def notify_render_completed(req: Request) -> dict[str, Any]:
        body = await req.json()
        return SPINE.on_cutora_render_completed(
            task_id=str(body.get("task_id") or ""),
            exported_video_path=Path(str(body["exported_video_path"])),
            target_filename=str(body.get("target_filename") or ""),
            drive_folder_url=str(body.get("drive_folder_url") or ""),
        )

    _ensure_background_services()
    return app


_SERVICES_STARTED = False
_SERVICES_LOCK = threading.Lock()


def _ensure_background_services() -> None:
    global _SERVICES_STARTED
    with _SERVICES_LOCK:
        if _SERVICES_STARTED:
            return
        _SERVICES_STARTED = True
        logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        print("================================================================", flush=True)
        print("  片语工作室 本地 AI 算力网关 (LTX-2.5 22B / FLUX.2 / Simone v6h / 音频 AI)", flush=True)
        print(f"  监听地址: http://{HOST}:{PORT}  (联动 ComfyUI: {COMFY_URL})", flush=True)
        print("================================================================", flush=True)
        threading.Thread(target=_worker_loop, daemon=True, name="ltx-gateway-worker").start()
        threading.Thread(target=lambda: ensure_comfy_running(wait_seconds=95.0), daemon=True, name="comfy-warmup").start()


app = build_app()


def main() -> None:
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
