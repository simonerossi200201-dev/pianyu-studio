# SPDX-License-Identifier: GPL-3.0-or-later
"""视觉 AI：从视频抽帧，判断该铺哪类音效。

支持：
  - Gemini（官方 REST，带图像）
  - 本地 Ollama（llava / qwen2.5vl / gemma3 等视觉模型）
  - SpaceXAI / xAI（OpenAI 兼容，grok-2-vision 等）

只用标准库 urllib，不额外加依赖。失败时返回空结果，调用方保持原设置。
"""
from __future__ import annotations

import json
import os

from cutora.audio.sfx_library import SFX_CATALOG, SCENE_TO_SFX


def _catalog_prompt_lines():
    lines = []
    for c in SFX_CATALOG:
        lines.append(f'- {c["id"]}: {c["label"]} ({c["group"]})')
    return "\n".join(lines)


SYSTEM_PROMPT = """You are a video scene classifier for a subtitle/video editor.
Look at the still frames. Pick background sound effects that would make the soundtrack feel more like being there.

Available sfx ids:
%s

Rules:
- Large indoor speech, sermon, prayer, church, conference, auditorium, stadium → prefer venue_ambience, crowd_applause, crowd_amen.
- crowd_amen is for worship / church / prayer where the audience responds (Amen, Yes). Do NOT pick it for a business talk.
- Outdoor woods/park → birds, wind.
- Beach/ocean → ocean.
- River/creek → river.
- Rainy scene → rain (and wind if stormy).
- Return 1 to 3 sfx ids. Never invent ids.
- Reply with JSON only, no markdown:
{"scene":"church_sermon|conference|outdoor|forest|beach|river|rain|other","sfx":["id",...],"reason":"short English or Chinese"}
""" % _catalog_prompt_lines()


def extract_jpeg_frames(video_path, n_frames=4, max_width=768):
    """返回 JPEG bytes 列表。OpenCV 不可用或视频打不开时返回空列表。"""
    if not video_path or not os.path.isfile(video_path):
        return []
    try:
        import cv2
    except Exception:
        return []
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0) or 25.0
        dur = (total / fps) if total > 0 else 0.0
        if dur <= 0.2:
            positions = [0.0]
        else:
            # 避开片头片尾
            lo, hi = dur * 0.12, dur * 0.88
            if n_frames <= 1:
                positions = [(lo + hi) * 0.5]
            else:
                positions = [lo + (hi - lo) * i / (n_frames - 1) for i in range(n_frames)]
        frames = []
        for t in positions:
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            h, w = frame.shape[:2]
            if w > max_width and w > 0:
                nh = max(1, int(h * max_width / w))
                frame = cv2.resize(frame, (max_width, nh), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
            if ok:
                frames.append(bytes(buf))
        return frames
    finally:
        cap.release()


def _parse_json_obj(text):
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    # 截出第一个 { ... }
    a = text.find("{")
    b = text.rfind("}")
    if a < 0 or b <= a:
        return None
    try:
        obj = json.loads(text[a:b + 1])
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _normalize_result(obj):
    if not obj:
        return {"ok": False, "scene": "other", "sfx": [], "reason": "empty"}
    valid = {c["id"] for c in SFX_CATALOG}
    scene = str(obj.get("scene") or "other").strip().lower()
    if scene not in SCENE_TO_SFX:
        scene = "other"
    raw = obj.get("sfx") or []
    if isinstance(raw, str):
        raw = [raw]
    sfx = []
    for x in raw:
        sid = str(x).strip()
        if sid in valid and sid not in sfx:
            sfx.append(sid)
    if not sfx:
        sfx = list(SCENE_TO_SFX.get(scene) or [])
    return {
        "ok": True,
        "scene": scene,
        "sfx": sfx,
        "reason": str(obj.get("reason") or ""),
    }


def _classify_with_provider(provider_id, frames, model=None, timeout=75):
    """Call the shared provider adapter; behaviour matches the old REST helpers."""
    if not frames:
        return {"ok": False, "reason": "抽帧失败"}
    try:
        from cutora.ai.providers import get_provider
        from cutora.core.net import NetPolicyError
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}
    try:
        text = get_provider(provider_id).chat(
            [{"role": "user", "content": SYSTEM_PROMPT + "\nClassify these video frames."}],
            images=frames,
            model=model,
            timeout=timeout,
        )
    except NetPolicyError as exc:
        return {"ok": False, "reason": str(exc)}
    except Exception as exc:
        return {"ok": False, "reason": "%s: %s" % (provider_id, exc)}
    return _normalize_result(_parse_json_obj(text))


def analyze_gemini(frames, api_key=None, model="gemini-2.0-flash"):
    """Kept for callers; key is read from the credential store, ``api_key`` ignored."""
    return _classify_with_provider("gemini", frames, model=model, timeout=75)


def analyze_ollama(frames, host=None, model="llava"):
    """Kept for callers; host is always loopback (port from AI prefs)."""
    return _classify_with_provider("ollama", frames, model=model, timeout=180)


def analyze_spacexai(frames, api_key=None, model="grok-2-vision-1212"):
    """SpaceXAI（xAI OpenAI 兼容接口）视觉模型。"""
    return _classify_with_provider("xai", frames, model=model, timeout=75)


def analyze_video(video_path, settings):
    """根据 settings['ai_provider'] 分析一条视频，返回标准化结果。"""
    fx = settings or {}
    frames = extract_jpeg_frames(video_path, n_frames=4)
    provider = str(fx.get("ai_provider") or "gemini").lower()
    if provider == "ollama":
        return analyze_ollama(frames, model=fx.get("ollama_model"))
    if provider in ("spacexai", "xai", "grok"):
        return analyze_spacexai(frames, model=fx.get("xai_model"))
    if provider in ("openai", "anthropic"):
        return _classify_with_provider(provider, frames, model=fx.get("%s_model" % provider))
    return analyze_gemini(frames, model=fx.get("gemini_model"))


def apply_ai_result_to_settings(settings, result):
    """把 AI 选中的音效写进 settings['sfx_enabled']，未选中的关掉。返回新 dict。"""
    fx = dict(settings or {})
    enabled = dict(fx.get("sfx_enabled") or {})
    chosen = set(result.get("sfx") or [])
    if not chosen:
        return fx
    for c in SFX_CATALOG:
        enabled[c["id"]] = c["id"] in chosen
    fx["sfx_enabled"] = enabled
    return fx
