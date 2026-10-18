"""End-to-end check: call Studio Zero's generate_single_video exactly as the UI does."""
import sys
import time

sys.path.insert(0, "D:/0_LLM")
from PIL import Image  # noqa: E402

import studio_zero as sz  # noqa: E402

PROMPT = (
    "一镜到底，固定机位，画面中始终只有这一个人，不切换镜头，不出现其他人物或画面。"
    "人物安静地坐着，专注地看向镜头前方，像在认真聆听讲道并默默思考，表情沉稳、平静、反思，"
    "眼神专注深沉，嘴巴自然闭合，不说话，不微笑，不皱眉。手指时不时向上指一指。"
    "只有自然眨眼1到2次、轻微呼吸起伏，偶尔微微点头。头部身体保持稳定，背景保持不变，环境安静。"
)

img = Image.open("D:/0_LLM/ComfyUI/input/studio_input.png")
t0 = time.time()
out = sz.generate_single_video(img, PROMPT, "", 15, sz.RATIO_FOLLOW, sz.SPEED_FAST, True,
                               progress=lambda *a, **k: None)
print("OUT", out, "SECONDS", round(time.time() - t0, 1))
