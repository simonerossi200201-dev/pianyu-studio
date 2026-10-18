# -*- coding: utf-8 -*-
import os, sys, tempfile, importlib.util, shutil
from pathlib import Path
ROOT = Path(r"D:\0_Dev\Orante")
td = tempfile.mkdtemp(prefix="orante-smoke85-")
os.environ["ORANTE_DATA"] = td
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/"orante"/"legacy"))

bak = ROOT/"orante/legacy/flow_shots.py.bak-0921switches"
tmpdir = Path(tempfile.mkdtemp())
shutil.copy2(bak, tmpdir/"flow_shots_bak85.py")
shutil.copy2(ROOT/"orante/legacy/flow_shots.json", tmpdir/"flow_shots.json")
spec = importlib.util.spec_from_file_location("flow_shots_bak85", tmpdir/"flow_shots_bak85.py")
bakmod = importlib.util.module_from_spec(spec)
# patch SHOTS_FILE after load
spec.loader.exec_module(bakmod)
bakmod.SHOTS_FILE = str(ROOT/"orante/legacy/flow_shots.json")

import flow_shots as cur
print("SWITCH_BRICK", list(getattr(cur, "SWITCH_BRICK_KEYS", ())))
print("SWITCH_META", list(getattr(cur, "SWITCH_META_KEYS", ())))
print("MUTE", getattr(cur, "MUTE_TIP_DEFAULT", None))
print("default_switches keys", sorted(cur.default_switches()))

shots = cur.load_shots()
# pass shots explicitly
cases = [
    dict(desc="一位中年男性站在客厅里，双手自然下垂。", card="缓缓推近", eye="闭眼", scene="常规", seg="Hello world.", shots=shots),
    dict(desc="一位中年男性站在客厅里保持不动，双手自然下垂。", card="缓缓推近", eye="闭眼", scene="常规", seg="Hello.", shots=shots),
    dict(desc="空荡荡的走廊，灯光昏黄。", card="缓慢横移", eye="空镜", scene="常规", seg="", shots=shots),
    dict(desc="男子坐在驾驶座上。", card="车内环视", eye="睁眼", scene="车内", seg="Drive safe.", shots=shots),
]
ok = True
for i, c in enumerate(cases):
    a = bakmod.build_prompt(**c)
    b = cur.build_prompt(**c)
    same = a == b
    print(f"case{i} equal={same} len {len(a)}/{len(b)}")
    if not same:
        ok = False
        for j, (x, y) in enumerate(zip(a, b)):
            if x != y:
                print(" diff@", j, repr(a[max(0,j-30):j+40]))
                print("      ", repr(b[max(0,j-30):j+40]))
                break
        if len(a)!=len(b):
            print(" len", len(a), len(b))
print("DEFAULT_OK", ok)

sw = cur.default_switches()
# turn off 画质锁 if exists
key_q = "画质锁" if "画质锁" in sw else None
for k in sw:
    if "画质" in k:
        key_q = k
print("quality key", key_q)
sw[key_q] = False
p_off = cur.build_prompt(**{**cases[0], "switches": sw})
p_on = cur.build_prompt(**cases[0])
print("changed", p_off != p_on)

# 描述语整理 off
sw2 = cur.default_switches()
sw2["描述语整理"] = False
desc = "人物保持坐着不动，双手放在膝盖上。"
p_tidy = cur.build_prompt(desc, "缓缓推近", "闭眼", "常规", "Hi.", shots=shots, switches=sw2)
p_tidy_on = cur.build_prompt(desc, "缓缓推近", "闭眼", "常规", "Hi.", shots=shots)
print("tidy off keeps 不动", "不动" in p_tidy)
print("tidy on may strip", "不动" in p_tidy_on, "equal?", p_tidy==p_tidy_on)

# minimal
sw3 = cur.default_switches()
for k in list(getattr(cur, "SWITCH_BRICK_KEYS", ())) + ["描述语整理"]:
    sw3[k] = False
mute_key = "静音提示"
for k in sw3:
    if "静音" in k or "静音" in k:
        mute_key = k
sw3[mute_key] = True
desc = "人物保持坐着不动。"
seg = "Hello friends."
p_min = cur.build_prompt(desc, "x", "闭眼", "常规", seg, shots=shots, switches=sw3)
print("MINIMAL", repr(p_min))
print("minimal_ok", p_min == desc+"\n"+seg)
p_mute = cur.build_prompt(desc, "x", "闭眼", "常规", "", shots=shots, switches=sw3)
print("MUTE", repr(p_mute))
print("mute_ok", p_mute == desc+"\n"+cur.MUTE_TIP_DEFAULT)

from orante.services import prompts
print("get_switches sample", list(prompts.get_switches().items())[:3], "...", "all true", all(prompts.get_switches().values()))
print("all_blocks switches", "switches" in prompts.all_blocks())