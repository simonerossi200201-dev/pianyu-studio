"""Resemble Enhance runner (resemble-ai, MIT). Offline, local weights only.

Usage:
  python voice_enhance.py INPUT [-o OUT] [--mode denoise|enhance|both] [--nfe 64] [--lambd 0.9] [--tau 0.5]
INPUT may be an audio file or a folder (wav/flac/mp3/ogg/m4a).

Note on deepspeed: the official package imports deepspeed at module level only
for its *training* code (engine/distributed helpers). Inference never calls it.
We register an inert placeholder module so imports succeed; any attempt to
actually use deepspeed raises RuntimeError. The package itself is not modified.
"""
import argparse, sys, time, types
from pathlib import Path

def _install_deepspeed_placeholder():
    if "deepspeed" in sys.modules:
        return
    def _fail(*a, **k):
        raise RuntimeError("deepspeed is not installed (training-only dependency)")
    class _Fail:
        def __init__(self, *a, **k): _fail()
    names = {
        "deepspeed": dict(init_distributed=_fail, initialize=_fail),
        "deepspeed.accelerator": dict(get_accelerator=_fail),
        "deepspeed.runtime": {},
        "deepspeed.runtime.engine": dict(DeepSpeedEngine=_Fail),
        "deepspeed.runtime.utils": dict(clip_grad_norm_=_fail),
    }
    for n, attrs in names.items():
        m = types.ModuleType(n); m.__dict__.update(attrs); sys.modules[n] = m
    sys.modules["deepspeed"].DeepSpeedConfig = _Fail

_install_deepspeed_placeholder()

import torch, torchaudio, soundfile as sf
from resemble_enhance.enhancer.enhancer import Enhancer
from resemble_enhance.enhancer.hparams import HParams
from resemble_enhance.inference import inference

_LTX_ROOT = Path(__file__).resolve().parents[5]
_PORTABLE_RUN_DIR = _LTX_ROOT / "runtime" / "models" / "resemble_enhance" / "enhancer_stage2"
RUN_DIR = _PORTABLE_RUN_DIR if _PORTABLE_RUN_DIR.is_dir() else Path(r"D:\0_LLM\audio\models\resemble_enhance\enhancer_stage2")
EXTS = {".wav", ".flac", ".mp3", ".ogg", ".m4a"}

def load_model(device):
    import pathlib, os
    _pp = pathlib.PosixPath
    if os.name == "nt":  # hparams.yaml was saved on Linux and tags paths as PosixPath
        pathlib.PosixPath = pathlib.WindowsPath
    try:
        hp = HParams.load(RUN_DIR)
    finally:
        pathlib.PosixPath = _pp
    model = Enhancer(hp)
    ckpt = (RUN_DIR / "ds" / "G" / "default" / "mp_rank_00_model_states.pt").resolve()
    ckpt.relative_to(RUN_DIR.resolve())
    try:
        sd = torch.load(ckpt, map_location="cpu", weights_only=True)["module"]
    except Exception:
        # DeepSpeed checkpoint state dict fallback after strict RUN_DIR path confinement check
        sd = torch.load(ckpt, map_location="cpu", weights_only=False)["module"]
    model.load_state_dict(sd)
    return model.eval().to(device)


def load_audio(p):
    wav, sr = torchaudio.load(str(p))
    return wav.mean(0), sr

@torch.inference_mode()
def process(model, p, out_dir, modes, a, device):
    dwav, sr = load_audio(p)
    res = {}
    for mode in modes:
        t = time.time()
        if mode == "denoise":
            hwav, osr = inference(model=model.denoiser, dwav=dwav, sr=sr, device=device)
        else:
            model.configurate_(nfe=a.nfe, solver=a.solver, lambd=a.lambd, tau=a.tau)
            hwav, osr = inference(model=model, dwav=dwav, sr=sr, device=device)
        out = out_dir / f"{p.stem}_{mode}.wav"
        sf.write(out, hwav.cpu().numpy(), osr)
        res[mode] = time.time() - t
        print(f"[{mode}] {p.name} -> {out}  {res[mode]:.1f}s  ({dwav.numel()/sr:.1f}s audio)", flush=True)
    return res

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input"); ap.add_argument("-o", "--out")
    ap.add_argument("--mode", default="both", choices=["denoise", "enhance", "both"])
    ap.add_argument("--nfe", type=int, default=64)
    ap.add_argument("--solver", default="midpoint", choices=["midpoint", "rk4", "euler"])
    ap.add_argument("--lambd", type=float, default=0.9, help="denoise strength before enhance (0-1)")
    ap.add_argument("--tau", type=float, default=0.5, help="prior temperature (0-1)")
    ap.add_argument("--cpu", action="store_true")
    a = ap.parse_args()
    device = "cpu" if a.cpu or not torch.cuda.is_available() else "cuda"
    inp = Path(a.input)
    files = [inp] if inp.is_file() else sorted(f for f in inp.iterdir() if f.suffix.lower() in EXTS)
    out_dir = Path(a.out) if a.out else (inp.parent if inp.is_file() else inp) / "enhanced"
    out_dir.mkdir(parents=True, exist_ok=True)
    modes = ["denoise", "enhance"] if a.mode == "both" else [a.mode]
    t = time.time(); model = load_model(device); print(f"model loaded {time.time()-t:.1f}s on {device}", flush=True)
    for f in files:
        process(model, f, out_dir, modes, a, device)
    if device == "cuda":
        print(f"peak VRAM {torch.cuda.max_memory_allocated()/2**30:.2f} GB", flush=True)

if __name__ == "__main__":
    main()
