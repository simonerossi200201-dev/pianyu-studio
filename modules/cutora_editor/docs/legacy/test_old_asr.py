# Minimal ASR load test using AutoSub_v6 (read-only).
import os, sys
OLD = r"D:\0_Dev\AutoSub_v6\core"
os.chdir(OLD)
sys.path.insert(0, OLD)
os.environ["PATH"] = OLD + os.pathsep + os.environ.get("PATH", "")
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
print("import config", flush=True)
import config
print("device", config.DEVICE, config.COMPUTE_TYPE, flush=True)
print("import AutoSub_debug", flush=True)
import AutoSub_debug as host
print("load model", flush=True)
m = host.get_whisperx_model()
print("ok", type(m), flush=True)
