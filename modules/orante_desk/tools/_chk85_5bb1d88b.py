# -*- coding: utf-8 -*-
from pathlib import Path
ROOT = Path(r"D:\0_Dev\Orante")
# what's already done?
for p in [
    ROOT/"orante/services/images.py",
    ROOT/"orante/server.py",
    ROOT/"ui/js/panels/gemini.js",
    ROOT/"ui/css/gemini.css",
    ROOT/"tools/test_spec85.py",
    ROOT/"tools/test_spec85_ui.js",
    ROOT/"specs/reports/85-prompt-switches-and-batch-desc.md",
]:
    print(p.name, "exists", p.exists(), "size", p.stat().st_size if p.exists() else 0)
    if p.exists() and p.suffix in (".py",".js",".css",".md"):
        t = p.read_text(encoding="utf-8", errors="ignore")
        for n in ["batch_desc", "batch-desc", "switches", "极简", "prompt-switch", "bak-0921switches"]:
            if n in t:
                print(" ", n, t.count(n))

# preset collect switches
import os, sys, tempfile
td = tempfile.mkdtemp(); os.environ["ORANTE_DATA"]=td
sys.path.insert(0,str(ROOT))
from orante.services import prompts, preset
prompts.set_switches({**prompts.get_switches(), "画质锁": False})
g = preset.collect_suite_group("prompts")
print("suite has prompts.switches", "prompts.switches" in g, g.get("prompts.switches"))
# restore
prompts.set_switches(None)
print("after reset", all(prompts.get_switches().values()))

# images copy_desc end - where to add batch_desc
im = (ROOT/"orante/services/images.py").read_text(encoding="utf-8")
print("restore_desc at", im.find("def restore_desc"))
print("file end funcs", [l for l in im.splitlines() if l.startswith("def ")][-8:])

# server after copy-desc
sv = (ROOT/"orante/server.py").read_text(encoding="utf-8")
i = sv.find('"/api/tasks/{tid}/images/copy-desc"')
print(sv[i:i+700])

# gemini toolbar - find where to add button (around top actions)
gj = (ROOT/"ui/js/panels/gemini.js").read_text(encoding="utf-8")
print("gemini bak", list((ROOT/"ui/js/panels").glob("gemini.js.bak*"))[:5])
# look for openPreview button area in template
for i,l in enumerate(gj.splitlines()):
    if "previewOn" in l and ("button" in l or "btn" in l) and i>1700:
        print(f"{i+1}:{l[:160]}")