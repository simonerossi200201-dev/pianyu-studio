# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""AssetLibrary: unified view over builtin packs + builtin audio + user assets."""
from __future__ import annotations

import json
import os
import re
import wave

from cutora.assets.pack import discover_packs
from cutora.assets.user import load_user_assets
from cutora.core.paths import RESOURCES

CATEGORIES = [
    "title", "lower_third", "frame", "element",
    "sticker", "video", "audio", "animated",
]


_BUILTIN_AUDIO_CACHE: list[dict] | None = None


def reload_cache() -> None:
    """No-op hook for tests/callers expecting cache invalidation."""
    global _BUILTIN_AUDIO_CACHE
    _BUILTIN_AUDIO_CACHE = None
    return None


def _classify_sfx_subcat(c: dict) -> str:
    raw_sub = str(c.get("subcat") or c.get("sfx_group") or c.get("group") or "").strip()
    valid_subcats = {
        "配乐 BGM", "转场音效", "自然天气", "江河流水",
        "鸟鸣生灵", "掌声欢笑", "城市火焰", "拟音场景",
    }
    if raw_sub in valid_subcats:
        return raw_sub
    sid = str(c.get("id") or "").lower()
    label = str(c.get("label") or c.get("name") or "")
    grp = str(c.get("group") or "")
    combined = f"{sid} {label}"

    if any(k in combined for k in ("rain", "thunder", "wind", "storm", "weather", "snow", "雨", "雷", "风", "雪")):
        return "自然天气"
    if any(k in combined for k in ("water", "ocean", "wave", "stream", "river", "underwater", "splash", "pour", "soda", "水", "海", "浪", "溪", "泉", "瀑")):
        return "江河流水"
    if any(k in combined for k in ("bird", "cricket", "forest", "frog", "cicada", "owl", "cuckoo", "seagull", "鸟", "蝉", "蛙", "林", "虫", "鸥", "莺")):
        return "鸟鸣生灵"
    if any(k in combined for k in ("cheer", "clap", "laugh", "crowd", "applause", "viral_", "boing", "trombone", "rimshot", "欢呼", "拍手", "掌声", "笑", "喝彩")):
        return "掌声欢笑"
    if any(k in combined for k in ("city", "street", "subway", "train", "campfire", "fire", "flame", "match", "cyber_neon", "traffic", "siren", "都市", "车流", "地铁", "火", "焰", "街")):
        return "城市火焰"
    if grp in ("转场", "冲击") or any(
        k in sid for k in (
            "whoosh", "swish", "riser", "downlifter", "impact", "boom", "drop",
            "zoom", "whip", "warp", "vortex", "portal", "sweep", "braam", "taiko", "cymbal", "suck",
        )
    ):
        return "转场音效"
    return "拟音场景"


def _probe_wav_duration(path: str, default: float = 3.0) -> float:
    try:
        with wave.open(path, "rb") as w:
            fr = w.getframerate()
            nf = w.getnframes()
            if fr > 0 and nf > 0:
                return round(float(nf) / float(fr), 2)
    except Exception:
        pass
    return default


def _load_builtin_audio_items() -> list[dict]:
    global _BUILTIN_AUDIO_CACHE
    if _BUILTIN_AUDIO_CACHE is not None:
        return [dict(x) for x in _BUILTIN_AUDIO_CACHE]
    items: list[dict] = []
    seen_paths: set[str] = set()

    # 1. Load builtin BGM beds from resources/audio/bgm_beds (and sfx_social.BGM_CATALOG if present)
    bgm_dir = os.path.join(RESOURCES, "audio", "bgm_beds")
    bgm_prov_items: dict[str, dict] = {}
    prov_path = os.path.join(bgm_dir, "PROVENANCE.json")
    if os.path.isfile(prov_path):
        try:
            with open(prov_path, "r", encoding="utf-8") as f:
                pdata = json.load(f)
            for entry in pdata.get("items") or []:
                if isinstance(entry, dict) and entry.get("id"):
                    bgm_prov_items[str(entry["id"])] = entry
        except Exception:
            pass

    try:
        from cutora.audio import sfx_social
        for entry in getattr(sfx_social, "BGM_CATALOG", None) or []:
            if isinstance(entry, dict) and entry.get("id"):
                bgm_prov_items.setdefault(str(entry["id"]), entry)
    except Exception:
        pass

    if os.path.isdir(bgm_dir):
        legacy_bgm_labels = {
            "bgm_cinematic_inspire_70": "电影感温暖励志 · 70BPM",
            "bgm_fashion_trap_100": "时尚动感节拍 · 100BPM",
            "bgm_happy_ukulele_110": "阳光欢快尤克里里 · 110BPM",
            "bgm_ambient_space_65": "静谧星空冥想 · 65BPM",
            "bgm_retro_synth_95": "复古霓虹合成器 · 95BPM",
            "bgm_warm_acoustic_82": "温暖民谣木吉他 · 82BPM",
        }
        for fname in sorted(os.listdir(bgm_dir)):
            if not fname.lower().endswith((".wav", ".mp3", ".flac", ".ogg", ".m4a")):
                continue
            fpath = os.path.abspath(os.path.join(bgm_dir, fname))
            seen_paths.add(os.path.normcase(fpath))
            stem = os.path.splitext(fname)[0]
            meta = bgm_prov_items.get(stem, {})
            label = str(meta.get("label") or meta.get("name") or legacy_bgm_labels.get(stem) or stem)
            dur = float(meta.get("duration_sec") or meta.get("duration") or _probe_wav_duration(fpath, 15.0))
            grp = str(meta.get("group") or "配乐 BGM")
            items.append({
                "id": stem,
                "name": label,
                "type": "audio",
                "audio_kind": "bgm",
                "subcat": "配乐 BGM",
                "group": grp,
                "duration": round(dur, 2),
                "file": fname,
                "source_path": fpath,
                "pack_id": "builtin_bgm",
                "builtin": True,
                "license": str(meta.get("license") or "Stability AI Community License"),
                "tags": ["配乐 BGM", "BGM", "音乐", grp, label],
            })

    # 2. Load builtin SFX from cutora.audio.sfx_social
    try:
        from cutora.audio import sfx_social
        sfx_dir = getattr(sfx_social, "SOCIAL_DIR", os.path.join(RESOURCES, "audio", "sfx_social"))
        for c in getattr(sfx_social, "CATALOG", []) or []:
            if not isinstance(c, dict):
                continue
            sid = str(c.get("id") or "")
            fname = str(c.get("file") or f"{sid}.wav")
            fpath = os.path.abspath(os.path.join(sfx_dir, fname))
            if not os.path.isfile(fpath):
                continue
            seen_paths.add(os.path.normcase(fpath))
            subcat = _classify_sfx_subcat(c)
            label = str(c.get("label") or sid)
            grp = str(c.get("group") or subcat)
            dur = float(c.get("duration") or _probe_wav_duration(fpath, 1.5))
            items.append({
                "id": sid,
                "name": label,
                "type": "audio",
                "audio_kind": "sfx",
                "subcat": subcat,
                "group": grp,
                "duration": round(dur, 2),
                "file": fname,
                "source_path": fpath,
                "pack_id": "builtin_sfx_social",
                "builtin": True,
                "license": str(c.get("license") or getattr(sfx_social, "LICENSE", "CUTORA-original")),
                "tags": [subcat, grp, "音效", "SFX", label, sid],
            })
        # Also include any additional wav files in sfx_social folder not yet in CATALOG
        if os.path.isdir(sfx_dir):
            for fname in sorted(os.listdir(sfx_dir)):
                if not fname.lower().endswith(".wav"):
                    continue
                fpath = os.path.abspath(os.path.join(sfx_dir, fname))
                if os.path.normcase(fpath) in seen_paths:
                    continue
                seen_paths.add(os.path.normcase(fpath))
                stem = os.path.splitext(fname)[0]
                pseudo = {"id": stem, "label": stem}
                subcat = _classify_sfx_subcat(pseudo)
                dur = _probe_wav_duration(fpath, 2.0)
                items.append({
                    "id": stem,
                    "name": stem,
                    "type": "audio",
                    "audio_kind": "sfx",
                    "subcat": subcat,
                    "group": subcat,
                    "duration": round(dur, 2),
                    "file": fname,
                    "source_path": fpath,
                    "pack_id": "builtin_sfx_social",
                    "builtin": True,
                    "license": getattr(sfx_social, "LICENSE", "CUTORA-original"),
                    "tags": [subcat, "音效", "SFX", stem],
                })
    except Exception:
        pass

    for r in items:
        r.setdefault("stored_path", r.get("source_path", ""))
    _BUILTIN_AUDIO_CACHE = items
    return [dict(x) for x in items]


def _normalize_subcat_token(raw: str) -> str:
    if not raw:
        return ""
    s = str(raw).strip()
    # Remove trailing count like "(100)"
    s = re.sub(r"\(\d+\)$", "", s).strip()
    # Remove leading emoji / non-word prefix if separated by space
    parts = s.split(" ", 1)
    if len(parts) == 2 and not any(ch.isalnum() or "\u4e00" <= ch <= "\u9fff" for ch in parts[0]):
        s = parts[1].strip()
    return s


_SUBCAT_SYNONYMS: dict[str, tuple[str, ...]] = {
    "互动号召": ("社交互动", "互动"),
    "社交互动": ("互动号召", "互动"),
    "方向指引": ("指引标注", "指引"),
    "指引标注": ("方向指引", "指引"),
    "徽章印章": ("美学印章", "印章"),
    "美学印章": ("徽章印章", "印章"),
    "动态GIF": ("动图",),
    "动态特效GIF": ("动图",),
    "内置BGM": ("配乐 BGM", "BGM"),
    "配乐 BGM": ("内置BGM", "BGM"),
}


def _matches_subcat(it: dict, subcat: str) -> bool:
    norm = _normalize_subcat_token(subcat)
    if not norm or norm.lower() == "all" or norm.startswith("全部"):
        return True
    raw_tokens = [t.strip() for t in re.split(r"[|/·]", norm) if t.strip()]
    tokens: list[str] = []
    for rt in raw_tokens:
        tokens.append(rt)
        for syn in _SUBCAT_SYNONYMS.get(rt, ()):
            if syn not in tokens:
                tokens.append(syn)
    if not tokens:
        return True

    candidates: list[str] = [
        str(it.get("subcat") or ""),
        str(it.get("category") or ""),
        str(it.get("group") or ""),
        str(it.get("name") or ""),
    ]
    for t in it.get("tags") or []:
        candidates.append(str(t))
    if it.get("is_animated_gif") or str(it.get("file") or "").lower().endswith(".gif"):
        candidates.append("动图")
    lt_spec = it.get("lower_third_spec")
    if isinstance(lt_spec, dict):
        candidates.append(str(lt_spec.get("category") or ""))
        candidates.append(str(lt_spec.get("subcat") or ""))
        candidates.append(str(lt_spec.get("layout") or ""))
        if lt_spec.get("show_icon") or lt_spec.get("icon_type"):
            candidates.append("带图标")
    bd_spec = it.get("border_spec")
    if isinstance(bd_spec, dict):
        candidates.append(str(bd_spec.get("category") or ""))
        candidates.append(str(bd_spec.get("subcat") or ""))
        candidates.append(str(bd_spec.get("style") or ""))

    for tok in tokens:
        for cand in candidates:
            if not cand:
                continue
            if tok == cand or tok in cand or (len(cand) >= 4 and cand in tok):
                return True
    return False


def items_for_category(
    category: str,
    query: str = "",
    project_dir: str | None = None,
    subcat: str = "",
) -> list[dict]:
    out: list[dict] = []
    seen_ids: set[str] = set()

    for p in discover_packs():
        for it in p.get("items", []):
            it_type = it.get("type")
            is_gif = bool(
                it.get("is_animated_gif")
                or str(it.get("file") or it.get("source_path") or "").lower().endswith(".gif")
            )
            match = (it_type == category) or (category == "animated" and is_gif)
            if not match:
                continue
            row = dict(it, pack_id=p["id"], builtin=True)
            if "source_path" not in row and row.get("file") and p.get("_dir"):
                cand = os.path.join(p["_dir"], "items", row["id"], row["file"])
                if os.path.exists(cand):
                    row["source_path"] = cand
                    row.setdefault("stored_path", cand)
            elif "source_path" in row:
                row.setdefault("stored_path", row["source_path"])
            uid = f"{p['id']}:{row.get('id')}"
            if uid not in seen_ids:
                seen_ids.add(uid)
                out.append(row)

    if category == "audio":
        for ait in _load_builtin_audio_items():
            uid = f"{ait.get('pack_id')}:{ait.get('id')}"
            if uid not in seen_ids:
                seen_ids.add(uid)
                out.append(ait)

    for it in load_user_assets(project_dir):
        if it.get("type") == category:
            out.append(dict(it, pack_id="user", builtin=False))

    if subcat:
        out = [it for it in out if _matches_subcat(it, subcat)]

    if query:
        q = query.strip().lower()
        out = [
            it for it in out
            if q in str(it.get("name", "")).lower()
            or q in str(it.get("subcat", "")).lower()
            or any(q in str(t).lower() for t in (it.get("tags") or []))
        ]
    return out
