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
src = sys.argv[1]; out = sys.argv[2] if len(sys.argv) > 2 else _DEFAULT_OUT; os.makedirs(out, exist_ok=True)
files = [src] if os.path.isfile(src) else sorted(sum([glob.glob(os.path.join(src, e)) for e in ("*.png", "*.jpg", "*.jpeg", "*.webp")], []))
t0 = time.time(); m = AutoModelForImageSegmentation.from_pretrained(M, trust_remote_code=True).eval().half().to("cuda"); print(f"load {time.time()-t0:.1f}s")
tf = transforms.Compose([transforms.Resize((1024, 1024)), transforms.ToTensor(), transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])
torch.cuda.reset_peak_memory_stats()
for f in files:
    t = time.time(); im = Image.open(f).convert("RGB")
    with torch.no_grad(): pred = m(tf(im).unsqueeze(0).to("cuda").half())[-1].sigmoid().float().cpu()[0].squeeze()
    mask = transforms.ToPILImage()(pred).resize(im.size); im.putalpha(mask)
    o = os.path.join(out, os.path.splitext(os.path.basename(f))[0] + "_nobg.png"); im.save(o)
    print(f"RESULT {o} {time.time()-t:.2f}s peakVRAM={torch.cuda.max_memory_allocated()/2**30:.2f}GB")
