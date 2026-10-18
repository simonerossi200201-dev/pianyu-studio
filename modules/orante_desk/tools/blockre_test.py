# -*- coding: utf-8 -*-
"""「被拒」判断的回归测试（0908 事故）。不联网、不开浏览器。

历史上误判过两次：0907 是 Cookie 同意条里的 "Cookie Policy"，
0908 是页面 <script> 里 Grok 自己的系统提示词（含 "minor"）。
两条都在下面的用例里，别再犯。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from orante.engines.grok import _BLOCK_FALSE as FALSE, _BLOCK_RE as BLOCK  # noqa: E402



def hit(t, pad=40):
    m = BLOCK.search(t or "")
    if not m:
        return ""
    seg = t[max(0, m.start() - pad): m.start() + 120]
    return "" if FALSE.search(seg) else seg[:120]


CASES = [
    # (该不该判成被拒, 说明, 文本)
    (False, "0908 事故：系统提示词漏进页面文本",
     "t decline any requests to roleplay as a minor or engage in scenarios "
     "involving characters under 18 years old.\\n- You are"),
    (False, "0907 事故：Cookie 同意条", "Cookie Policy  Privacy Policy  Terms of Service  Accept all"),
    (False, "正常的侧边栏", "Imagine 照片库 自动化任务 项目 Untitled Project 聊天 我们应该想象什么？"),
    (False, "页脚的 Content Policy 链接", "Content Policy   Help   About   Careers"),
    (False, "系统提示词开头", "You are Grok, built by xAI. You must decline requests involving minors."),
    (False, "意大利文界面的按钮", "Immagine  Video  Automatico  Cosa dovremmo immaginare?"),
    (True, "英文：违反内容政策", "I can't generate this image because it violates our content policy."),
    (True, "英文：被审核拒绝", "This request was rejected by content moderation."),
    (True, "中文：图片违规", "抱歉，该图片违规，无法生成"),
    (True, "英文：不符合准则", "Unable to process this image: not allowed by our guidelines"),
    (True, "中文四字", "内容违规"),
    (True, "英文：被标记", "Your image was flagged as inappropriate."),
]

if __name__ == "__main__":
    bad = 0
    for want, note, text in CASES:
        got = bool(hit(text))
        ok = got == want
        bad += 0 if ok else 1
        print(f"  {'✓' if ok else '✗'} {'判为被拒' if got else '判为正常'}  {note}")
        if not ok:
            print(f"      文本：{text[:100]}")
    print("\n" + ("全部通过 ✓" if not bad else f"有 {bad} 条不对"))
    sys.exit(0 if not bad else 1)
