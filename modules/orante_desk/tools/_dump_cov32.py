# -*- coding: utf-8 -*-
import os, sys
os.environ.setdefault("ORANTE_DATA", str(__import__("pathlib").Path(__file__).resolve().parent.parent / "data"))
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from orante import db
db.init()
from orante.services import coverage, prompts
lib = prompts.get_coverage()
print("legal", coverage.legal_count(lib))
print("head", (lib.get("head") or "")[:50], "...")
print("--- 12 picks ---")
picks = coverage.pick(lib, 12)
for i, p in enumerate(picks, 1):
    c = p["cov"]
    print("%02d  %s %s  x  %s %s  class=%s phase=%s" % (
        i, c["camera"], c["camera_name"], c["action"], c["action_name"],
        c["class"], c["phase"]))
print("--- delete 01,02 add 99 ---")
lib2 = dict(lib)
lib2["cameras"] = [c for c in lib["cameras"] if c["id"] not in ("01", "02")]
lib2["cameras"].append({
    "id": "99", "name": "测试机位", "class": "C", "front": False, "rank": 4,
    "text": "拍摄距离拉到全身，相机在人物正前方平视",
})
print("legal after edit", coverage.legal_count(lib2))
picks2 = coverage.pick(lib2, 8)
ids = [p["cov"]["camera"] for p in picks2]
print("picked cameras", ids)
print("has 99", "99" in ids)
print("has 01", "01" in ids)
print("--- sample prompt ---")
print(picks[0]["prompt"][:200])
print("--- refill ---")
from orante.services import expand
print(expand._prompt_block(True)[:80])
