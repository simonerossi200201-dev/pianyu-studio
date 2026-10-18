# -*- coding: utf-8 -*-
import os, sys, tempfile, importlib.util, shutil
from pathlib import Path
ROOT = Path(r"D:\0_Dev\Orante")
td = tempfile.mkdtemp(prefix="orante-smoke85-")
os.environ["ORANTE_DATA"] = td
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/"orante"/"legacy"))

# load backup as module
bak = ROOT/"orante/legacy/flow_shots.py.bak-0921switches"
assert bak.is_file(), bak
tmp = Path(tempfile.mkdtemp())/"flow_shots_bak85.py"
shutil.copy2(bak, tmp)
spec = importlib.util.spec_from_file_location("flow_shots_bak85", tmp)
bakmod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bakmod)

import flow_shots as cur

# sample cases
cases = [
    dict(desc="一位中年男性站在客厅里，双手自然下垂。", card="缓缓推近", eye="闭眼", scene="常规", seg="Hello world."),
    dict(desc="一位中年男性站在客厅里保持不动，双手自然下垂。", card="缓缓推近", eye="闭眼", scene="常规", seg="Hello."),
    dict(desc="空荡荡的走廊，灯光昏黄。", card="缓慢横移", eye="空镜", scene="常规", seg=""),
    dict(desc="男子坐在驾驶座上。", card="车内环视", eye="睁眼", scene="车内", seg="Drive safe."),
]
ok = True
for i, c in enumerate(cases):
    a = bakmod.build_prompt(**c)
    b = cur.build_prompt(**c)  # default switches
    same = a == b
    print(f"case{i} equal={same} len {len(a)}/{len(b)}")
    if not same:
        ok = False
        # show first diff
        for j, (x, y) in enumerate(zip(a, b)):
            if x != y:
                print(" first diff at", j, repr(a[max(0,j-20):j+20]), "||", repr(b[max(0,j-20):j+20]))
                break
        else:
            print(" length mismatch", repr(a[-50:]), repr(b[-50:]))
print("DEFAULT_OK", ok)

# off quality lock
sw = cur.default_switches()
sw["画质锁"] = False
p_off = cur.build_prompt(**cases[0], switches=sw)
p_on = cur.build_prompt(**cases[0])
print("quality off changes", p_off != p_on)
print("quality text in on", "画质" in p_on or any(x in p_on for x in ["4K","细节","清晰"]))
# find quality block content
shots = cur.load_shots()
q = shots.get("画质锁") or ""
print("quality block nonempty", bool(q), "in on", q[:40] in p_on if q else None, "in off", q[:40] in p_off if q else None)

# desc tidy off
sw2 = cur.default_switches()
sw2["描述语整理"] = False
desc = "人物保持坐着不动，双手放在膝盖上。"
p_tidy = cur.build_prompt(desc, "缓缓推近", "闭眼", "常规", "Hi.", switches=sw2)
print("不动 kept", "保持坐着不动" in p_tidy or "不动" in p_tidy)

# minimal + copy
sw3 = cur.default_switches()
for k in cur.SWITCH_BRICK_KEYS + ("描述语整理",):
    sw3[k] = False
sw3["静音提示"] = True
desc = "人物保持坐着不动。"
seg = "Hello friends."
p_min = cur.build_prompt(desc, "x", "闭眼", "常规", seg, switches=sw3)
print("MINIMAL", repr(p_min))
print("minimal eq", p_min == desc + "\n" + seg)

# minimal no copy + mute
p_mute = cur.build_prompt(desc, "x", "闭眼", "常规", "", switches=sw3)
print("MUTE", repr(p_mute))
print("mute eq", p_mute == desc + "\n" + cur.MUTE_TIP_DEFAULT)

# prompts get_switches
from orante.services import prompts
print("switches", prompts.get_switches())
print("all has switches", "switches" in prompts.all_blocks())