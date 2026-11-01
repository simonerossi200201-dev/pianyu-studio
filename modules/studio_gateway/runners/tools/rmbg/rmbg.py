# RMBG-2.0 (BRIA, CC BY-NC 4.0, BiRefNet architecture). Usage: rmbg.py <image|folder> [outdir]
import sys, os, glob, time, torch
os.environ["HF_HUB_OFFLINE"] = "1"
from PIL import Image
from torchvision import transforms
from transformers import AutoModelForImageSegmentation

_LTX_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
_CAND_MODELS = [
    os.path.join(_LTX_ROOT, "runtime", "models", "vision", "rmbg2"),
    os.path.join(_LTX_ROOT, "runtime", "models", "rmbg2"),
    r"D:\0_LLM\tools\rmbg\models\RMBG-2.0",
]
M = next((c for c in _CAND_MODELS if os.path.isdir(c)), _CAND_MODELS[0])
_DEFAULT_OUT = os.path.join(_LTX_ROOT, "data", "gateway_outputs", "rmbg")
src = sys.argv[1]
out = sys.argv[2] if len(sys.argv) > 2 else _DEFAULT_OUT
os.makedirs(out, exist_ok=True)
files = [src] if os.path.isfile(src) else sorted(sum([glob.glob(os.path.join(src, e)) for e in ("*.png", "*.jpg", "*.jpeg", "*.webp")], []))

if torch.cuda.is_available():
    dev = "cuda"
    use_fp16 = True
elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
    dev = "mps"
    use_fp16 = False
else:
    dev = "cpu"
    use_fp16 = False

t0 = time.time()
m = AutoModelForImageSegmentation.from_pretrained(M, trust_remote_code=True).eval()
if use_fp16:
    m = m.half()
m = m.to(dev)
print(f"load {time.time()-t0:.1f}s device={dev}")
tf = transforms.Compose([
    transforms.Resize((1024, 1024)),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
])
if dev == "cuda":
    torch.cuda.reset_peak_memory_stats()
for f in files:
    t = time.time()
    im = Image.open(f).convert("RGB")
    inp_t = tf(im).unsqueeze(0).to(dev)
    if use_fp16:
        inp_t = inp_t.half()
    with torch.no_grad():
        pred = m(inp_t)[-1].sigmoid().float().cpu()[0].squeeze()
    mask = transforms.ToPILImage()(pred).resize(im.size)
    im.putalpha(mask)
    o = os.path.join(out, os.path.splitext(os.path.basename(f))[0] + "_nobg.png")
    im.save(o)
    vram_gb = (torch.cuda.max_memory_allocated() / 2**30) if dev == "cuda" else 0.0
    print(f"RESULT {o} {time.time()-t:.2f}s device={dev} peakVRAM={vram_gb:.2f}GB")
