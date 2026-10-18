"""Camera-mode end-to-end checks through Studio Zero's own generate function.

python e2e_camera.py            -> camera mode, no end image
python e2e_camera.py END.png    -> camera mode with an end image
"""
import sys
import time

sys.path.insert(0, "D:/0_LLM")
from PIL import Image  # noqa: E402

import studio_zero as sz  # noqa: E402

DIRECTION = (
    "手持跟拍镜头，一镜到底。人物面向镜头说话，随后一边说一边自然地向画面右侧走去，步伐放松。"
    "镜头跟随人物缓慢向右平移，逐渐露出房间更多的样貌。人物发型、服装始终保持不变，画面中只有这一个人。"
)
LINE = "大家好，欢迎来到今天的聚会，我带大家看看现场，这边是我们准备的休息区，请随意坐。"

img = Image.open("D:/0_LLM/acceptance_portrait.jpg") if len(sys.argv) > 2 else Image.open("D:/0_LLM/bench/ref_first.png")
end = Image.open(sys.argv[1]) if len(sys.argv) > 1 else None
t0 = time.time()
out = sz.generate_single_video(img, DIRECTION, LINE, 15, sz.RATIO_FOLLOW, sz.SPEED_CAMERA, False,
                               camera_mode=True, end_image=end, progress=lambda *a, **k: None)
print("OUT", out, "SECONDS", round(time.time() - t0, 1))
