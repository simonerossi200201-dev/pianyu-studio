"""Robota recipe (workflow recording and replay) manager."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from robota import config, memory

RECIPES_DIR = config.DATA / "recipes"
RECIPES_DIR.mkdir(parents=True, exist_ok=True)


def _safe_name(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", name).strip() or "未命名流程"


def list_recipes() -> list[dict[str, Any]]:
    RECIPES_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for f in sorted(RECIPES_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            data.setdefault("filename", f.stem)
            out.append(data)
        except Exception:
            continue
    return out


def get_recipe(name: str) -> dict[str, Any] | None:
    f = RECIPES_DIR / f"{_safe_name(name)}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_recipe(name: str, recipe: dict[str, Any]) -> dict[str, Any]:
    RECIPES_DIR.mkdir(parents=True, exist_ok=True)
    clean = _safe_name(name)
    recipe["name"] = clean
    recipe["updated_at"] = datetime.now().isoformat(timespec="seconds")
    recipe.setdefault("created_at", recipe["updated_at"])
    f = RECIPES_DIR / f"{clean}.json"
    f.write_text(json.dumps(recipe, ensure_ascii=False, indent=2), encoding="utf-8")
    
    # Also sync into global SOP memory so all brains know about it
    sop_entry = f"### 工作流: {clean}\n{recipe.get('description', '')}\n步骤:\n"
    for i, step in enumerate(recipe.get("steps", []), 1):
        sop_entry += f"{i}. {step}\n"
    memory.memory_append("sop", sop_entry)
    return recipe


def delete_recipe(name: str) -> bool:
    f = RECIPES_DIR / f"{_safe_name(name)}.json"
    if f.exists():
        f.unlink()
        return True
    return False


async def distill_session_to_recipe(name: str, messages: list[dict], project: str | None = None) -> dict[str, Any]:
    """Use AI to summarize a recorded session into a reusable workflow recipe."""
    from robota.brains import agy as agy_brain
    from robota.brains import local as local_brain

    convo_lines = []
    for m in messages[-40:]:
        role = m.get("role", "user")
        content = m.get("content", "")
        if content:
            convo_lines.append(f"[{role}]: {content[:2000]}")
    convo = "\n".join(convo_lines)

    prompt = (
        f"你是一个高级工作流自动化专家。请根据以下用户刚才与助手交互的操作记录，提炼为一个名为「{name}」的可重复运行的工作流（SOP/Recipe）。\n"
        "请严格只返回一个合法的 JSON 对象，不要包含 markdown 代码块包裹，也不要有任何其他解释说明：\n"
        "{\n"
        f'  "name": "{name}",\n'
        '  "description": "简要说明此流程的目的与解决什么问题",\n'
        f'  "project": "{project or ""}",\n'
        '  "steps": [\n'
        '    "第1步的清晰具体动作",\n'
        '    "第2步的清晰具体动作"\n'
        '  ],\n'
        '  "prompt_template": "未来用户再次执行此工作流时，可直接发送给 AI 代理执行的完整指令模版"\n'
        "}\n\n"
        f"交互记录：\n{convo}"
    )

    recipe_data = None
    try:
        reply = await agy_brain.chat(prompt, model="gemini-3.8-flash-medium")
        if reply.ok and reply.text:
            raw = reply.text.strip()
            # remove ```json ``` if present
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            recipe_data = json.loads(raw)
    except Exception:
        recipe_data = None

    if not recipe_data:
        # Fallback local distillation
        try:
            reply = await local_brain.chat([{"role": "user", "content": prompt}], model="gemma4:12b")
            if reply.ok and reply.text:
                raw = reply.text.strip()
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
                recipe_data = json.loads(raw)
        except Exception:
            recipe_data = None

    if not recipe_data:
        # manual fallback
        recipe_data = {
            "name": name,
            "description": f"由会话记录生成的「{name}」流程",
            "project": project or "",
            "steps": [m["content"][:100] for m in messages if m.get("role") == "user"][-5:],
            "prompt_template": f"请按照我们之前录制的流程「{name}」，执行对应的操作步骤。"
        }

    return save_recipe(name, recipe_data)
