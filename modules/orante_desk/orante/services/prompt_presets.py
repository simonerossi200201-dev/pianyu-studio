# -*- coding: utf-8 -*-
"""提示词预设管理器 + 最大连续换行间隙台词插入算法 + 智能格式检查器。

功能：
1. `insert_speech_at_max_newlines(template, speech)`：
   在提示词模板中寻找换行符数量最多（`\n` 最多）的连续空白行间隙，将本镜头台词 `speech`
   精准插入该位置（若模板显式写了 `{speech}` 或 `{{speech}}` 则直接替换占位符）。
2. `inspect_preset_template(name, template)`：
   智能检查新添加/修改的预设模板，返回台词插入位置预览、最大连续换行数与格式告警。
3. `ensure_presets_seeded()`：
   幂等入库 Google 表格全部 33 条提示词预设（按 5 大分类归档）以及 Orante 原有固定结语、
   扩图提示词、Gemini 自动写词模板。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .. import db, paths
from ..errors import OranteError

_GAP_RE = re.compile(r"(?:\r?\n[\t ]*){1,}")
_COLON_SPEECH_HINT_RE = re.compile(r"(?:说|讲|口播|祷告|宣告|喊|道)\s*[：: condition]*[“\"']?\s*$")
_RESIDUAL_FOREIGN_RE = re.compile(
    r"(Seigneur,\s*pardonne|sitrano\s+ny\s+ratram-pony|Raha\s+tia\s+ny\s+Ray)",
    re.I,
)


def _find_best_insertion_span(template: str) -> tuple[int, int, int]:
    """寻找最佳台词插入区间 `(start, end, newline_count)`。

    规则：
    1. 优先考虑模板内部（非首尾纯空白）的换行块。
    2. 在所有内部换行块中，选取包含 `\\n` 数量最多的那一段（若并列，优先选前面紧跟“说：/祷告：/口播：”的块，否则选第一个）。
    3. 若最高换行数为 1（没有预留空行），优先选择紧跟在“说：/口播：/祷告：”之后的那个换行。
    """
    if not template:
        return (0, 0, 0)
    rstripped_len = len(template.rstrip(" \t\r\n"))
    lstripped_start = len(template) - len(template.lstrip(" \t\r\n"))

    matches = list(_GAP_RE.finditer(template))
    if not matches:
        return (len(template), len(template), 0)

    interior = [m for m in matches if m.start() >= lstripped_start and m.end() <= rstripped_len]
    candidates = interior if interior else matches

    def _score(m: re.Match) -> tuple[int, int, int]:
        nl_count = m.group(0).count("\n")
        before = template[max(0, m.start() - 30):m.start()]
        has_cue = 1 if _COLON_SPEECH_HINT_RE.search(before) else 0
        # 若已经有 >=2 个换行，严格按换行数最大优先（并列时看 cue 和靠前位置）；
        # 若只有 1 个换行，优先选带“说：”提示的换行位
        return (nl_count, has_cue, -m.start())

    best = max(candidates, key=_score)
    return (best.start(), best.end(), best.group(0).count("\n"))


def insert_speech_at_max_newlines(template: str, speech: str) -> str:
    """在模板中最大连续换行处插入本段台词文案 `speech`。"""
    tpl = template or ""
    sp = (speech or "").strip()
    if not tpl.strip():
        return sp
    if not sp:
        return tpl

    for token in ("{{speech}}", "{speech}", "{{copy}}", "{copy}", "【台词】"):
        if token in tpl:
            return tpl.replace(token, sp)

    start, end, nl_count = _find_best_insertion_span(tpl)
    if nl_count <= 0:
        return f"{tpl.rstrip()}\n{sp}"
    prefix = tpl[:start]
    suffix = tpl[end:]
    return f"{prefix}\n{sp}\n{suffix}"


def inspect_preset_template(
    name: str,
    template: str,
    sample_speech: str = "【此处插入镜头台词文案】",
) -> dict:
    """智能检查提示词预设格式，输出台词插入点预览与潜在问题提示。"""
    tpl = template or ""
    start, end, nl_count = _find_best_insertion_span(tpl)
    prefix = tpl[max(0, start - 36):start].replace("\n", " ")
    suffix = tpl[end:min(len(tpl), end + 36)].replace("\n", " ")
    warnings: list[str] = []

    if nl_count == 0:
        warnings.append("模板中没有换行符，台词将只能追加在末尾；建议在“说：”之后连按2~3次回车留出空行。")
    elif nl_count == 1:
        warnings.append("模板中最大连续换行只有 1 个（无独立空行），已自动定位到说话引导词后；建议留出至少 1 个空行（2个以上换行）以保证最精准插入。")
    if _RESIDUAL_FOREIGN_RE.search(tpl):
        warnings.append("检测到模板内含有固定外语示例句，建议删掉示例句并留空行作为动态台词插入位。")

    sample = insert_speech_at_max_newlines(tpl, sample_speech or "【此处插入镜头台词文案】")
    return {
        "name": (name or "").strip(),
        "valid": nl_count >= 1,
        "max_newline_gap": int(nl_count),
        "max_newlines": int(nl_count),
        "insertion_index": int(start),
        "split_index": int(start),
        "prefix_preview": prefix.strip(),
        "suffix_preview": suffix.strip(),
        "sample_rendered": sample,
        "preview_with_speech": sample,
        "warnings": warnings,
    }


def _builtin_sheet_file() -> Path:
    return Path(__file__).resolve().parent.parent / "legacy" / "builtin_sheet_presets.json"


def _migrated_orante_presets() -> list[dict]:
    """将 Orante 原有的结语尾缀、扩图提示词、Gemini 看图写词系统模板统一纳入预设管理。"""
    tail_long = (
        "现代 4K 数字电影质感，超清晰锐利对焦。极度纯净的镜头，无胶片颗粒，无漏光，无闪烁。"
        "鲜艳自然的色彩，高清晰度。人物超真实皮肤毛孔、细腻面部微表情。口型与语音精准同步，一镜到底。"
    )
    tail_short = (
        "现代 4K 数字电影质感，超清晰锐利对焦，真实自然光影，画面纯净稳定，一镜到底，无字幕无水印。"
    )
    expand_txt = (
        "基于参考图人物与场景进行电影感构图扩展，去除画面上所有文字与字幕水印，"
        "保持人物五官、服装、光影一致，超清晰 4K 电影质感。"
    )
    upscale_txt = (
        "以上传的这张图为唯一参考，原样重绘一遍：同一个人物、同一张脸、同一个姿态和手势、"
        "同一个构图和取景、同一个背景和光线、同一套衣着。只把清晰度和细节做到最高。"
    )
    sys_long = (
        "根据上传的人物参考图与本段台词，生成一段完整的视频拍摄提示词：\n\n\n"
        "要求锁定人物外貌、服装与场景，动作自然连贯，现代 4K 数字电影质感，一镜到底。"
    )
    sys_short = (
        "根据上传的参考图，生成一段纯视觉电影感长镜头画面描述提示词：\n\n\n"
        "要求画面连贯稳定，现代 4K 数字电影质感，仅保留自然环境音。"
    )
    try:
        from . import prompts as pr_mod
        upscale_txt = getattr(pr_mod, "UPSCALE_DEFAULT", upscale_txt) or upscale_txt
    except Exception:
        pass
    return [
        {
            "id": "PR_TAIL_01",
            "name": "长片固定画质与口型结语",
            "category": "固定结语与音效",
            "role": "tail",
            "template": tail_long,
            "sort_order": 101,
        },
        {
            "id": "PR_TAIL_02",
            "name": "短片固定纯画面结语",
            "category": "固定结语与音效",
            "role": "tail",
            "template": tail_short,
            "sort_order": 102,
        },
        {
            "id": "PR_TAIL_03",
            "name": "纯净无背景音乐尾缀 (No BGM)",
            "category": "固定结语与音效",
            "role": "tail",
            "template": "No background music, no soundtrack, no cinematic score, no ambient music, only natural environmental sounds.",
            "sort_order": 103,
        },
        {
            "id": "PR_EXP_01",
            "name": "默认扩图去字与构图重构",
            "category": "扩图提示词预设",
            "role": "expand",
            "template": expand_txt,
            "sort_order": 201,
        },
        {
            "id": "PR_EXP_02",
            "name": "高清修复原图预设",
            "category": "扩图提示词预设",
            "role": "expand",
            "template": upscale_txt,
            "sort_order": 202,
        },
        {
            "id": "PR_GEM_01",
            "name": "长片 Gemini 自动看图写词模板",
            "category": "Gemini自动写词模板",
            "role": "gemini_tpl",
            "template": sys_long,
            "sort_order": 301,
        },
        {
            "id": "PR_GEM_02",
            "name": "短片 Gemini 自动看图写词模板",
            "category": "Gemini自动写词模板",
            "role": "gemini_tpl",
            "template": sys_short,
            "sort_order": 302,
        },
    ]


def ensure_presets_seeded() -> int:
    """幂等写入内置的 33 条 Google Sheet 预设 + 7 条 Orante 原有功能块预设。"""
    sheet_path = _builtin_sheet_file()
    items: list[dict] = []
    if sheet_path.is_file():
        try:
            items.extend(json.loads(sheet_path.read_text(encoding="utf-8")))
        except Exception:
            pass
    items.extend(_migrated_orante_presets())

    existing_map = {r["id"]: r for r in db.rows("SELECT id, category FROM prompt_presets")}
    now = db.now()
    inserted = 0
    for it in items:
        pid = it["id"]
        cat = it.get("category") or "默认分组"
        if pid in existing_map:
            old_cat = existing_map[pid].get("category") or ""
            if "?" in old_cat or not old_cat.strip():
                db.run("UPDATE prompt_presets SET category=?, name=? WHERE id=?", (cat, it["name"], pid))
            continue
        tpl = it.get("template") or ""
        _, _, gap_n = _find_best_insertion_span(tpl)
        db.run(
            """INSERT INTO prompt_presets(
                 id, name, category, role, template, speech_gap_newlines,
                 builtin, sort_order, meta, created, updated
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                pid,
                it["name"],
                it.get("category") or "默认分组",
                it.get("role") or "main",
                tpl,
                int(gap_n),
                1,
                int(it.get("sort_order") or 0),
                db.j({"row": it.get("row")} if it.get("row") else {}),
                now,
                now,
            ),
        )
        inserted += 1
    return inserted


def _decorate_preset(row: dict) -> dict:
    d = dict(row)
    d["meta"] = db.unj(d.get("meta"), {}) or {}
    d["builtin"] = bool(int(d.get("builtin") or 0))
    d["speech_gap_newlines"] = int(d.get("speech_gap_newlines") or 0)
    return d


def list_presets(category: str | None = None, role: str | None = None, kind: str | None = None) -> dict:
    ensure_presets_seeded()
    role_val = role or kind
    wheres, args = [], []
    if category:
        wheres.append("category=?")
        args.append(category)
    if role_val:
        wheres.append("role=?")
        args.append(role_val)
    where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""
    rows = db.rows(f"SELECT * FROM prompt_presets{where_sql} ORDER BY sort_order ASC, created ASC, id ASC", tuple(args))
    presets = [_decorate_preset(r) for r in rows]
    by_cat: dict[str, list[dict]] = {}
    for p in presets:
        by_cat.setdefault(p["category"], []).append(p)
    return {
        "presets": presets,
        "categories": list(by_cat.keys()),
        "by_category": by_cat,
    }


def get_preset(preset_id_or_name: str) -> dict | None:
    if not preset_id_or_name:
        return None
    ensure_presets_seeded()
    key = str(preset_id_or_name).strip()
    r = db.one("SELECT * FROM prompt_presets WHERE id=? OR name=?", (key, key))
    return _decorate_preset(r) if r else None


def save_preset(payload: dict) -> dict:
    ensure_presets_seeded()
    pid = str(payload.get("id") or "").strip()
    name = str(payload.get("name") or "").strip()
    if not name:
        raise OranteError("预设名称不能为空")
    category = str(payload.get("category") or "默认分组").strip() or "默认分组"
    role = str(payload.get("role") or "main").strip() or "main"
    template = str(payload.get("template") or "")
    if not template.strip():
        raise OranteError("预设提示词内容不能为空")

    insp = inspect_preset_template(name, template)
    gap_n = insp["max_newline_gap"]
    now = db.now()

    existing = db.one("SELECT * FROM prompt_presets WHERE id=?", (pid,)) if pid else None
    if existing:
        sort_order = int(payload.get("sort_order") if payload.get("sort_order") is not None else existing.get("sort_order") or 0)
        meta = db.unj(existing.get("meta"), {}) or {}
        if isinstance(payload.get("meta"), dict):
            meta.update(payload["meta"])
        db.run(
            """UPDATE prompt_presets
                  SET name=?, category=?, role=?, template=?, speech_gap_newlines=?,
                      sort_order=?, meta=?, updated=?
                WHERE id=?""",
            (name, category, role, template, gap_n, sort_order, db.j(meta), now, pid),
        )
        out = get_preset(pid) or {}
        out["inspection"] = insp
        return out

    if not pid:
        pid = db.next_id("prompt_preset", "PR_", 4)
    cnt = (db.one("SELECT COUNT(*) n FROM prompt_presets") or {}).get("n") or 0
    sort_order = int(payload.get("sort_order") if payload.get("sort_order") is not None else (cnt + 1))
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    db.run(
        """INSERT INTO prompt_presets(
             id, name, category, role, template, speech_gap_newlines,
             builtin, sort_order, meta, created, updated
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (pid, name, category, role, template, gap_n, 0, sort_order, db.j(meta), now, now),
    )
    out = get_preset(pid) or {}
    out["inspection"] = insp
    return out


def batch_save_presets(items: list[dict]) -> dict:
    saved = []
    for it in (items or []):
        if isinstance(it, dict) and it.get("name") and it.get("template"):
            saved.append(save_preset(it))
    return {"ok": True, "saved_count": len(saved), "presets": saved}


def delete_preset(preset_id: str) -> dict:
    pid = (preset_id or "").strip()
    if not pid:
        raise OranteError("预设 ID 不能为空")
    db.run("DELETE FROM prompt_presets WHERE id=?", (pid,))
    return {"ok": True, "deleted_id": pid}
