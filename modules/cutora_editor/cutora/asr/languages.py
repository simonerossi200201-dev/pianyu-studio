# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""ASR language catalog: faster-whisper codes + languages.json metadata."""
from __future__ import annotations

import json
import os
from typing import Any

from cutora.core.log import get_logger
from cutora.core.paths import RESOURCES

logger = get_logger("cutora.asr.languages")

# script = 该语言实际使用的文字。line_break = 我们怎么断行（与 script 解耦）。
SCRIPT_ENUM = (
    "latin", "cyrillic", "greek", "armenian", "georgian",
    "arabic", "hebrew", "han", "kana", "hangul",
    "thai", "lao", "khmer", "myanmar",
    "devanagari", "bengali", "gurmukhi", "gujarati",
    "tamil", "telugu", "kannada", "malayalam", "sinhala",
    "ethiopic", "tibetan", "mongolian", "other",
)
SUPPORTED_SCRIPTS = SCRIPT_ENUM
LINE_BREAKS = ("space", "fallback")
EXPERIMENTAL_HINT = "能识别，排版质量不承诺。"

_RAW: dict[str, Any] | None = None
_CATALOG: list[dict[str, Any]] | None = None
_BY_CODE: dict[str, dict[str, Any]] | None = None


def languages_json_path() -> str:
    return os.path.join(RESOURCES, "data", "languages.json")


def whisper_language_codes() -> tuple[str, ...]:
    from faster_whisper.tokenizer import _LANGUAGE_CODES
    return tuple(_LANGUAGE_CODES)


def _load_json() -> dict[str, Any]:
    global _RAW
    if _RAW is not None:
        return _RAW
    path = languages_json_path()
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("languages.json 必须是对象")
    _RAW = data
    return _RAW


def _normalize_row(row: dict) -> dict[str, Any]:
    code = str(row.get("code") or "").strip().lower()
    script = str(row.get("script") or "").strip().lower()
    line_break = str(row.get("line_break") or "").strip().lower()
    complete = (
        line_break == "space"
        and script in ("latin", "cyrillic", "greek")
        and not bool(row.get("experimental"))
    )
    tier = str(row.get("subtitle_tier") or "").strip().lower()
    if tier not in ("complete", "font_only"):
        tier = "complete" if complete else "font_only"
    return {
        "code": code,
        "name_en": str(row.get("name_en") or code),
        "name_native": str(row.get("name_native") or code),
        "name_zh": str(row.get("name_zh") or code),
        "script": script,
        "line_break": line_break,
        "experimental": bool(row.get("experimental")),
        "subtitle_tier": tier,
        "note": str(row.get("note") or ""),
    }


def load_catalog() -> list[dict[str, Any]]:
    global _CATALOG, _BY_CODE
    if _CATALOG is not None:
        return _CATALOG
    rows = []
    for row in list(_load_json().get("languages") or []):
        if isinstance(row, dict) and row.get("code"):
            rows.append(_normalize_row(row))
    _CATALOG = rows
    _BY_CODE = {r["code"]: r for r in rows}
    return _CATALOG


def catalog_by_code() -> dict[str, dict[str, Any]]:
    load_catalog()
    return dict(_BY_CODE or {})


def common_codes() -> list[str]:
    codes = [str(c).strip().lower() for c in (_load_json().get("common") or [])]
    known = catalog_by_code()
    return [c for c in codes if c in known]


def get_language(code: str | None) -> dict[str, Any] | None:
    raw = (code or "").strip().lower()
    if raw == "auto":
        return {
            "code": "auto",
            "name_en": "Auto",
            "name_native": "Auto",
            "name_zh": "自动检测",
            "script": "latin",
            "line_break": "space",
            "experimental": False,
            "subtitle_tier": "complete",
        }
    return catalog_by_code().get(raw)


def is_known_language(code: str | None) -> bool:
    raw = (code or "").strip().lower()
    if raw == "auto":
        return True
    return raw in catalog_by_code()


def whisper_language_arg(code: str | None) -> str | None:
    """Whisper 的 language= 实参：auto / 空 → None（由检测或模型自判）。"""
    raw = (code or "").strip().lower()
    if not raw or raw == "auto":
        return None
    return raw


def is_experimental(code: str | None) -> bool:
    entry = get_language(code)
    if not entry or entry["code"] == "auto":
        return False
    return bool(entry.get("experimental"))


NONE_ALIGN_TOOLTIP = (
    "这种语言能转写成文字，但没有逐词时间对齐，"
    "拼接对不上每个词，所以不能选。"
)


def align_phrase(tier: str, *, where: str = "menu") -> str:
    """Display text for an align tier. The tier itself comes from language_capability."""
    if where == "table":
        return {
            "exact": "精确（专用模型）",
            "fallback": "可用（通用模型，精度略低）",
            "none": "暂不支持",
        }.get(tier or "", "暂不支持")
    return {
        "exact": "逐词精确",
        "fallback": "可用（精度略低）",
        "none": "暂不支持拼接",
    }.get(tier or "", "暂不支持拼接")


def subtitle_phrase(tier: str, *, where: str = "menu") -> str:
    if not tier:
        return "—"
    if where == "table":
        return "完善" if tier == "complete" else "仅字形保证"
    return "排版完善" if tier == "complete" else "仅字形保证"


def capability_table_cells(cap: dict[str, Any]) -> tuple:
    """Six cells for the language-support table, from one capability dict."""
    return (
        str(cap.get("name_zh") or ""),
        str(cap.get("code") or ""),
        "✓" if cap.get("transcribe") else "—",
        align_phrase(str(cap.get("align_tier") or "none"), where="table"),
        subtitle_phrase(str(cap.get("subtitle_tier") or ""), where="table"),
        "✓" if cap.get("font_ok") else "—",
    )


def capability_summary_line() -> str:
    from cutora.asr.models import language_capability
    rows = load_catalog()
    exact = 0
    fallback = 0
    for row in rows:
        tier = language_capability(row["code"])["align_tier"]
        if tier == "exact":
            exact += 1
        elif tier == "fallback":
            fallback += 1
    return "可转写 %d 种；%d 种逐词精确，%d 种可用" % (
        len(rows), exact, fallback)


def picker_label(entry: dict[str, Any]) -> str:
    if entry.get("code") == "auto":
        return str(entry.get("name_zh") or "自动检测")
    from cutora.asr.models import language_capability
    cap = language_capability(str(entry.get("code") or ""))
    name = cap.get("name_zh") or entry.get("name_zh") or entry.get("code")
    return "%s · %s · %s" % (
        name,
        align_phrase(str(cap.get("align_tier") or "none")),
        subtitle_phrase(str(cap.get("subtitle_tier") or "font_only")),
    )


def _picker_row(row: dict[str, Any], section: str) -> dict[str, Any]:
    from cutora.ui.i18n import tr
    hint = EXPERIMENTAL_HINT if row.get("experimental") else ""
    return {
        "code": row["code"],
        "label": picker_label(row),
        "section": section,
        "experimental": bool(row.get("experimental")),
        "tooltip": tr(hint) if hint else "",
        "script": row.get("script") or "other",
        "line_break": row.get("line_break") or "fallback",
    }


def picker_entries() -> list[dict[str, Any]]:
    from cutora.ui.i18n import tr
    entries = [{
        "code": "auto",
        "label": tr("自动检测"),
        "section": "auto",
        "experimental": False,
        "tooltip": tr("听前 30 秒判断语言；置信度低于 0.6 时会提示确认。"),
        "script": "latin",
    }]
    known = catalog_by_code()
    seen: set[str] = set()
    for code in common_codes():
        row = known.get(code)
        if not row:
            continue
        seen.add(code)
        entries.append(_picker_row(row, "common"))
    rest = [r for r in known.values() if r["code"] not in seen]
    rest.sort(key=lambda r: (r.get("name_native") or r["code"]))
    for row in rest:
        entries.append(_picker_row(row, "all"))
    return entries


def set_asr_language(code: str) -> str:
    from cutora.core.commands import CommandError
    raw = (code or "").strip().lower()
    if not is_known_language(raw):
        raise CommandError("未知语言代码 %s" % (code,))
    from cutora.core import config as cfg
    cfg.LANGUAGE = raw
    from cutora.asr.engine import _sync_device_globals
    _sync_device_globals()
    return raw
