"""Router: picks a brain for each message (manual choice > privacy > task type > fallback)."""
from __future__ import annotations

import re
from typing import Any

from robota import config, memory
from robota.brains import BrainReply, EventCB, emit
from robota.brains import agy as agy_brain
from robota.brains import local as local_brain
from robota.brains import providers as prov_brain
from robota.brains import smol as smol_brain

CODE_RX = re.compile(
    r"(代码|bug|报错|修复|修改.*(文件|代码|函数)|实现|重构|函数|单元测试|测试用例|commit|提交代码|写一个.*(脚本|程序|工具)|"
    r"traceback|exception|refactor|implement|fix\b|stack ?trace|任务书|改项目)", re.IGNORECASE)
ACTION_RX = re.compile(
    r"(运行|执行|跑一下|跑一遍|上传|下载|渲染|导出|显存|gpu|队列|列出|查看|看看|整理|状态|通知|监控|等.*完成|"
    r"rclone|网盘|studio|comfy|orante|cutora|项目|文件夹|目录)", re.IGNORECASE)
PRIVATE_TAG = "#私密"


def persona() -> str:
    projs = config.projects()
    lines = [f"- {p.id}: {p.name} — {p.description} (root: {p.root}{', 私密' if p.private else ''})"
             for p in projs.values()]
    profile = memory.memory_read("profile").get("text", "") if hasattr(memory, "memory_read") else ""
    habits = memory.memory_read("habits").get("text", "") if hasattr(memory, "memory_read") else ""
    sop = memory.memory_read("sop").get("text", "") if hasattr(memory, "memory_read") else ""
    return (
        "你是 Robota，用户（Luca）的本地通用代理人。用中文回答，简洁、直接、诚实；不确定就说不确定。\n"
        "你能用工具：读写文件、运行命令（有安全黑名单）、查看显存、释放显存、发系统通知、操作 Robota 队列、"
        "rclone 网盘、Studio Zero 出片、Orante 只读查询、等待文件或日志、读写记忆。\n"
        "需要很久的工作（写代码、渲染、批量处理）请用 queue_add 放进队列，而不是自己干等。\n"
        "登记的项目：\n" + "\n".join(lines) +
        ("\n\n用户档案（长期记忆）：\n" + profile[-2500:] if profile.strip() else "") +
        ("\n\n用户习惯与原则（长期记忆）：\n" + habits[-3000:] if habits.strip() else "") +
        ("\n\n用户的 SOP（长期记忆）：\n" + sop[-4000:] if sop.strip() else "")
    )


def tutor_prompt() -> str:
    t = config.settings().get("tutor", {})
    target = t.get("target_language") or "英语与意大利语"
    notes = memory.memory_read("tutor").get("text", "") if hasattr(memory, "memory_read") else ""
    return (
        f"你是 Luca 的专属外教导师。目标辅导语言：{target}；用户母语：中文；沟通风格：温柔、耐心的真人女老师。\n"
        "【核心教学规则】：\n"
        "1. 倾听与理解：认真倾听 Luca 说的每一句话。无论他是用英文、意大利文，还是中外混杂，或者因为卡壳说了中文，都要先理解他的意图。\n"
        "2. 亲切中文解析与纠错：如果 Luca 出现语法错误、用词生硬不够地道、或者某个句子词汇说不上来，必须首先用温暖耐心的【中文】为他拆解讲解：\n"
        "   - 亲切说明：'你刚才想表达的是……，这里更地道的说法是：【……】。'\n"
        "   - 简明解释背后的原因（语法搭配、语境区别或为什么这么说更地道）。\n"
        "3. 纯正地道示范与意大利语发音指导：清晰给出标准地道的完整外语句子（英语或意大利语）。特别注意：在涉及意大利语时，务必主动贴心标出发音要领（如重音位置、双辅音短暂停顿如 caffè/cornetto、grazie 必须发满三个音节 gra-tsi-e 且尾音 e 绝不吞音、per favore 四个音节等），帮助 Luca 彻底掌握地道纯正的意大利语发音！\n"
        "4. 鼓励与接话：最后用一句简单自然的外语提问或接续话题，鼓励他模仿读一遍或继续对话。\n"
        "5. 回复像面对面聊天一样亲切自然，不要冷冰冰的说教。\n"
        + ("\n这位学生的历史学习记录（常犯错误、薄弱点）：\n" + notes[-3000:] if notes.strip() else "")
    )


def decide(text: str, choice: str, project: str | None) -> str:
    proj = config.project(project)
    private = PRIVATE_TAG in text or bool(proj and proj.private)
    if choice in ("tutor",):
        return "tutor"
    if choice in ("local", "local_agent", "agy", "provider"):
        if private and choice in ("agy", "provider"):
            return "local_agent"
        return choice
    if private:
        return "local_agent" if ACTION_RX.search(text) or CODE_RX.search(text) else "local"

    default_brain = config.settings().get("router", {}).get("default_brain", "agy")
    if default_brain == "agy":
        return "agy"

    if CODE_RX.search(text):
        return "agy"
    if ACTION_RX.search(text):
        return "local_agent"
    return "local"


def _history_prompt(history: list[dict[str, str]], text: str, limit: int = 12) -> str:
    h = history[-limit:]
    if not h:
        return text
    convo = "\n".join(f"[{m['role']}] {m['content'][:2000]}" for m in h)
    return f"之前的对话：\n{convo}\n\n[user] {text}"


async def respond(text: str, *, history: list[dict[str, str]], choice: str = "auto", project: str | None = None,
                  model: str | None = None, agy_conversation: str | None = None,
                  on_event: EventCB | None = None) -> tuple[BrainReply, str]:
    route = decide(text, choice, project)
    proj = config.project(project)
    workdir = proj.root if proj else str(config.ROOT / "workspace")
    await emit(on_event, {"type": "route", "route": route})
    s = config.settings()
    default_brain = s.get("router", {}).get("default_brain", "agy")

    async def run(r: str) -> BrainReply:
        if r == "tutor":
            if default_brain == "agy" and not (proj and proj.private) and PRIVATE_TAG not in text:
                use_m = model or s["agy"]["model"]
                p = tutor_prompt() + "\n\n" + _history_prompt(history, text)
                return await agy_brain.chat(p, cwd=workdir, model=use_m, on_event=on_event)
            return await local_brain.chat(history[-20:] + [{"role": "user", "content": text}],
                                          model=model or s["local"]["fast_model"], system=tutor_prompt(),
                                          on_event=on_event)
        if r == "local":
            return await local_brain.chat(history[-20:] + [{"role": "user", "content": text}],
                                          model=model or s["local"]["fast_model"], system=persona(),
                                          on_event=on_event)
        if r == "local_agent":
            rules = proj.rules_text() if proj else ""
            sysmsg = persona() + (f"\n\n当前项目 {proj.id} 的规则：\n{rules[:6000]}" if rules else "")
            rcfg = s.get("router", {})
            engine = rcfg.get("agent_engine", "smol")
            if engine == "smol" and not (proj and proj.mcp):
                r1 = await smol_brain.agent(_history_prompt(history, text), workdir=workdir,
                                            model=model or rcfg.get("smol_model") or s["local"]["fast_model"],
                                            system=sysmsg, on_event=on_event)
                if r1.ok:
                    return r1
                await emit(on_event, {"type": "status", "text": f"smolagents 引擎失败：{r1.error[:120]}，改用原引擎"})
            return await local_brain.agent(_history_prompt(history, text), workdir=workdir,
                                           model=model or s["local"]["model"], system=sysmsg,
                                           extra_mcp=proj.mcp if proj else None, on_event=on_event)
        if r == "provider":
            return await prov_brain.chat(history[-20:] + [{"role": "user", "content": text}],
                                         system=persona(), on_event=on_event)
        # agy
        use_model = model or (s["agy"]["code_model"] if CODE_RX.search(text) else s["agy"]["model"])
        prompt = text if agy_conversation else (persona() + "\n\n" + _history_prompt(history, text))
        if proj and proj.rules_file and not agy_conversation:
            prompt += f"\n\n（当前项目 {proj.id}，开工前先读规则文件 {proj.rules_file}）"
        return await agy_brain.chat(prompt, cwd=workdir, model=use_model,
                                    conversation_id=agy_conversation, on_event=on_event)

    reply = await run(route)
    if not reply.ok and route in ("local", "local_agent") and not (proj and proj.private) and PRIVATE_TAG not in text:
        await emit(on_event, {"type": "status", "text": f"本地大脑失败（{reply.error[:120]}），自动升级到 agy"})
        route = "agy"
        reply = await run("agy")
    elif not reply.ok and route == "agy":
        fallback_r = "local_agent" if ACTION_RX.search(text) else "local"
        await emit(on_event, {"type": "status", "text": f"agy 响应失败（{reply.error[:120]}），自动降级到本地大脑"})
        route = fallback_r
        reply = await run(fallback_r)
    return reply, route
