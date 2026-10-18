# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Segment.lines + words → LineBox / WordRun in video-pixel coordinates."""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

from PySide6.QtCore import QRectF
from PySide6.QtGui import QFont, QFontMetricsF

from cutora.render.textfx.anim import layout_tracking_em, rng_for
from cutora.render.textfx.emphasis import EmphasisHit, detect_emphasis, hit_map
from cutora.render.textfx.spec import POP_SIDE_EM, FxSpec
from cutora.ui.panels.fonts import get_font_manager


@dataclass
class GlyphBox:
    ch: str
    x: float
    y: float
    w: float
    h: float


@dataclass
class WordRun:
    text: str
    font_family: str
    fontsize_px: float
    tracking_em: float
    fill_override: str | None
    emphasized: bool
    word_index: int
    t_start: float
    t_end: float
    x: float
    y: float
    w: float
    h: float
    baseline: float
    rot_deg: float = 0.0
    pad_px: float = 0.0
    glyphs: list[GlyphBox] = field(default_factory=list)
    qfont: QFont | None = None
    underline: bool = False
    orient_deg: float = 0.0
    glyph_ox: float = 0.0
    glyph_oy: float = 0.0


@dataclass
class LineBox:
    text: str
    x: float
    y: float
    w: float
    h: float
    baseline: float
    runs: list[WordRun] = field(default_factory=list)


@dataclass
class LayoutResult:
    lines: list[LineBox]
    em: float
    cx: float
    cy: float
    text_x: float
    text_y: float
    text_w: float
    text_h: float
    origin: tuple[int, int]
    roi_w: int
    roi_h: int
    hit_x: float
    hit_y: float
    hit_w: float
    hit_h: float
    seg_id: str
    n_units: int


def segment_id(event: dict) -> str:
    if event.get("id") not in (None, ""):
        return str(event.get("id"))
    return "%s|%s" % (event.get("start"), event.get("text") or "")


def _apply_case(text: str, spec: FxSpec) -> str:
    if spec.letter_case == "upper":
        return text.upper()
    return text


def _line_strings(event: dict) -> list[str]:
    preset = event.get("lines")
    if isinstance(preset, list) and any(str(x).strip() for x in preset):
        return [str(x) for x in preset if str(x).strip() or x == ""]
    text = event.get("edited_text")
    if text is None:
        text = event.get("text") or ""
    text = str(text)
    if "\n" in text:
        return text.split("\n")
    return [text] if text else [""]


def _join_source_text(event: dict, lines: list[str]) -> str:
    text = event.get("edited_text")
    if text is None:
        text = event.get("text")
    if text:
        return str(text).replace("\r\n", "\n").replace("\r", "\n")
    return " ".join(str(x).strip() for x in lines if str(x).strip())


def _char_wrap_lines(event: dict, style: dict, video_w: int, lines: list[str]) -> list[str]:
    from cutora.subtitle.stylepack import wrap_for_canvas
    text = _join_source_text(event, lines).replace("\n", " ")
    if not text.strip():
        return lines or [""]
    video_h = int(style.get("_video_h") or video_w)
    lang = event.get("lang")
    if not lang:
        rules = style.get("layout_rules") if isinstance(
            style.get("layout_rules"), dict) else {}
        lang = rules.get("lang")
    wrapped = wrap_for_canvas(text, style, video_w, video_h, lang)
    return wrapped or lines


def _pixel_wrap_one(
    line: str, fm, max_w: float, stops: frozenset,
) -> list[str]:
    from cutora.subtitle.layout import _bad_end
    words = str(line).split()
    if not words:
        return [line]
    out: list[str] = []
    cur: list[str] = []
    for tok in words:
        cand = cur + [tok]
        text = " ".join(cand)
        if cur and float(fm.horizontalAdvance(text)) > max_w:
            if _bad_end(cur[-1], stops) and len(cur) > 1:
                moved = cur.pop()
                out.append(" ".join(cur))
                cur = [moved, tok]
                if float(fm.horizontalAdvance(" ".join(cur))) > max_w:
                    out.append(moved)
                    cur = [tok]
            else:
                out.append(" ".join(cur))
                cur = [tok]
        else:
            cur.append(tok)
    if cur:
        out.append(" ".join(cur))
    return out


def _pixel_wrap_lines(
    lines: list[str], family: str, em: float, spec: FxSpec, video_w: int,
    bold: bool, italic: bool, extra_pad: float = 0.0, lang: str | None = None,
    max_lines: int | None = None, style: dict | None = None,
) -> list[str]:
    from cutora.subtitle.layout import load_stopwords
    from cutora.subtitle.stylepack import line_max_width_px
    max_w = line_max_width_px(style, video_w, em=em, extra_pad=extra_pad)
    font = _qfont(
        family, em, bold, italic, layout_tracking_em(spec),
        style_name=_font_style_name(style))
    fm = _metrics(font)
    stops = load_stopwords(lang or "en")
    cap = max(1, int(max_lines)) if max_lines else None
    out: list[str] = []
    for line in lines:
        if cap is not None and len(out) >= cap:
            out[-1] = (out[-1] + " " + str(line)).strip()
            continue
        if float(fm.horizontalAdvance(line)) <= max_w:
            out.append(line)
            continue
        parts = _pixel_wrap_one(line, fm, max_w, stops)
        if cap is None or len(out) + len(parts) <= cap:
            out.extend(parts)
            continue
        room = max(1, cap - len(out))
        out.extend(parts[:room])
        extra = parts[room:]
        if extra:
            out[-1] = (out[-1] + " " + " ".join(extra)).strip()
        out = out[:cap]
    if cap is not None and len(out) > cap:
        out = out[:cap]
    return out or lines


def _style_max_lines(style: dict) -> int:
    from cutora.subtitle.stylepack import effective_max_lines
    return effective_max_lines(style)


def _extra_line_pad(style: dict, spec: FxSpec, em: float) -> float:
    from cutora.render.textfx.spec import expansion_px
    if style.get("_overlay_box"):
        # Q129：文本层的框就是文字的折行范围；背景 / 阴影 / 发光是往框外长的装饰，
        # 不许反过来把文字挤窄（Canva 语义：背景跟着文本走，不是文本被背景限制）。
        return float(style.get("box_padding_x") or 0)
    return expansion_px(spec, em, for_wrap=True) + float(style.get("box_padding_x") or 0)


def _line_avail_width(
    style: dict, video_w: int, em: float, extra_pad: float,
) -> float:
    from cutora.subtitle.stylepack import SAFE_MARGIN, line_max_width_px
    packed = float(line_max_width_px(
        style, video_w, em=em, extra_pad=extra_pad))
    if style.get("_overlay_box"):
        # Q127：文本层的框就是排版宽度（Canva 语义），不吃字幕的安全边距。
        # 字幕样式没有 _overlay_box，golden 走的仍是下面那行。
        return max(1.0, packed)
    margin = SAFE_MARGIN
    if style.get("safe_margin_pct") is not None:
        # Q137：用户在「字幕排版」里自己定的左右安全边距。样式没这个键（老工程、
        # golden）就是原来的 8%，一个字节不变。
        try:
            margin = max(0.0, min(0.45, float(style.get("safe_margin_pct")) / 100.0))
        except (TypeError, ValueError):
            margin = SAFE_MARGIN
    inset = (float(max(1, int(video_w))) * (1.0 - 2.0 * margin)
             - 2.0 * float(extra_pad))
    return max(1.0, min(packed, inset))


def _shrink_em_to_fit(
    lines: list[str], family: str, em: float, spec: FxSpec, video_w: int,
    bold: bool, italic: bool, extra_pad: float, style: dict,
) -> tuple[float, str | None, list[str]]:
    from cutora.subtitle.layout import fit_lines_to_width
    max_w = _line_avail_width(style, video_w, em, extra_pad)

    def fm_factory(scale):
        return _metrics(_qfont(
            family, em * float(scale), bold, italic, layout_tracking_em(spec),
            style_name=_font_style_name(style)))

    new_lines, scale, warn = fit_lines_to_width(lines, fm_factory, max_w)
    return em * float(scale), warn, new_lines


def _pop_side_pad(spec: FxSpec, em: float, emphasized: bool) -> float:
    if emphasized and spec.emphasis.anim == "pop":
        return POP_SIDE_EM * float(em)
    return 0.0


def _token_advance(
    token: str, spec: FxSpec, style: dict, em: float, family: str,
    word_index: int, seg_id: str, emphasized: bool,
) -> float:
    emph_fam = spec.fonts.emphasis or family
    fam = emph_fam if (emphasized and spec.emphasis.font == "emphasis") else family
    bold, italic, style_name = _weight_args(style)
    size = _token_size_px(spec, em, word_index, seg_id, emphasized)
    font = _qfont(
        fam, size, bold, italic, layout_tracking_em(spec),
        style_name=style_name)
    adv = float(_metrics(font).horizontalAdvance(token))
    return adv + 2.0 * _pop_side_pad(spec, em, emphasized)


def _line_advance_weighted(
    line: str, spec: FxSpec, style: dict, em: float, family: str,
    hits: dict[int, EmphasisHit], seg_id: str, start_index: int,
) -> float:
    toks = [t for t in str(line).split() if t] if str(line).strip() else [""]
    total = 0.0
    for k, tok in enumerate(toks):
        idx = start_index + k
        hit = hits.get(idx)
        piece = tok if k == len(toks) - 1 else tok + " "
        total += _token_advance(
            piece, spec, style, em, family, idx, seg_id, bool(hit))
    return total


def _pixel_wrap_weighted(
    tokens: list[str], spec: FxSpec, style: dict, em: float, family: str,
    hits: dict[int, EmphasisHit], seg_id: str, max_w: float, max_lines: int,
) -> list[str]:
    from cutora.subtitle.layout import load_stopwords, _bad_end
    lang = style.get("lang") if isinstance(style, dict) else None
    stops = load_stopwords(lang or "en")
    cap = max(1, int(max_lines))
    lines: list[list[int]] = [[]]
    for i, tok in enumerate(tokens):
        cand = lines[-1] + [i]
        text = " ".join(tokens[j] for j in cand)
        start = cand[0]
        width = _line_advance_weighted(
            text, spec, style, em, family, hits, seg_id, start)
        if lines[-1] and width > max_w:
            if _bad_end(tokens[lines[-1][-1]], stops) and len(lines[-1]) > 1:
                moved = lines[-1].pop()
                if len(lines) >= cap:
                    lines[-1].append(moved)
                    lines[-1].append(i)
                    continue
                lines.append([moved, i])
                moved_text = " ".join(tokens[j] for j in lines[-1])
                if _line_advance_weighted(
                        moved_text, spec, style, em, family, hits, seg_id,
                        lines[-1][0]) > max_w:
                    lines[-1] = [moved]
                    if len(lines) >= cap:
                        lines[-1].append(i)
                    else:
                        lines.append([i])
            else:
                if len(lines) >= cap:
                    lines[-1].append(i)
                else:
                    lines.append([i])
        else:
            lines[-1].append(i)
    out = [" ".join(tokens[j] for j in ln) if ln else "" for ln in lines]
    return out[:cap] or [""]


def _shrink_weighted(
    lines: list[str], spec: FxSpec, style: dict, em: float, family: str,
    hits: dict[int, EmphasisHit], seg_id: str, video_w: int, extra: float,
) -> tuple[float, str | None, list[str]]:
    from cutora.subtitle.layout import fit_lines_to_width
    max_w = _line_avail_width(style, video_w, em, extra)
    index_at = []
    cursor = 0
    for ln in lines:
        index_at.append(cursor)
        cursor += len(str(ln).split())

    def fm_factory(scale):
        class _FM:
            def horizontalAdvance(_self, text, _ln=None):
                # Match by line identity; fallback to first line start.
                start = 0
                for src, idx in zip(lines, index_at):
                    if src == text:
                        start = idx
                        break
                return _line_advance_weighted(
                    text, spec, style, em * float(scale), family,
                    hits, seg_id, start)
        return _FM()

    floor = 0.5 / max(float(spec.emphasis.scale), 1.0)
    if spec.scatter.enabled:
        floor = floor / max(1.0 + float(spec.scatter.size_jitter), 1.0)
    floor = max(0.15, min(0.5, floor))
    new_lines, scale, warn = fit_lines_to_width(
        lines, fm_factory, max(1.0, max_w - 8.0), floor=floor)
    return em * float(scale), warn, new_lines


def _fit_event_lines(
    event: dict, style: dict, spec: FxSpec, em: float, family: str, video_w: int,
    hits: dict[int, EmphasisHit] | None = None, seg_id: str = "",
) -> tuple[list[str], float, str | None]:
    max_l = _style_max_lines(style)
    preset = event.get("lines")
    has_preset = isinstance(preset, list) and any(str(x).strip() for x in preset)
    hit_map_ = hits or {}
    weighted = _emph_active(spec) or _scatter_active(spec)
    if has_preset:
        lines = [str(x) for x in preset if str(x).strip() or x == ""]
        if len(lines) > max_l:
            lines = lines[:max_l]
    else:
        lines = _line_strings(event)
        if video_w > 0 and not weighted and not event.get("_preserve_newlines"):
            lines = _char_wrap_lines(event, style, video_w, lines)
    if video_w <= 0:
        return (lines[:max_l] or [""], em, None)
    extra = _extra_line_pad(style, spec, em)
    lang = event.get("lang")
    bold, italic, _style_name = _weight_args(style)
    if weighted and not has_preset:
        tokens = tokenize_event(event)
        max_w = _line_avail_width(style, video_w, em, extra)
        lines = _pixel_wrap_weighted(
            tokens, spec, style, em, family, hit_map_, seg_id, max_w, max_l)
    elif not has_preset:
        lines = _pixel_wrap_lines(
            lines, family, em, spec, video_w, bold, italic,
            extra_pad=extra, lang=lang, max_lines=max_l, style=style)
    lines = lines[:max_l] or [""]
    if style.get("_overlay_box") and not has_preset:
        # Q127：文本层不缩字号、不打省略号 —— 一个词比框还宽就让它伸出去，
        # 用户看得见，自己拉宽框或调小字号；要缩放走 fit_mode="shrink"。
        return lines, em, None
    if weighted:
        new_em, warn, lines = _shrink_weighted(
            lines, spec, style, em, family, hit_map_, seg_id, video_w, extra)
    else:
        new_em, warn, lines = _shrink_em_to_fit(
            lines, family, em, spec, video_w, bold, italic, extra, style)
    return lines, new_em, warn


def tokenize_event(event: dict) -> list[str]:
    from cutora.render.textfx.emphasis import tokenize
    text = _join_source_text(event, _line_strings(event))
    return tokenize(text)


def _word_entries(event: dict) -> list[dict]:
    raw = event.get("words") or []
    out = []
    for i, item in enumerate(raw):
        if isinstance(item, dict):
            out.append({
                "word": str(item.get("word") or item.get("text") or ""),
                "start": float(item.get("start") or 0.0),
                "end": float(item.get("end") or 0.0),
                "index": i,
            })
        else:
            out.append({"word": str(item), "start": 0.0, "end": 0.0, "index": i})
    return out


def has_word_times(event: dict) -> bool:
    """这一段有没有真的逐词时间戳。"""
    for item in (event.get("words") or []):
        if not isinstance(item, dict):
            continue
        try:
            if float(item.get("end") or 0.0) > float(item.get("start") or 0.0):
                return True
        except (TypeError, ValueError):
            continue
    return False


def spread_word_times(lines: list, t0: float, t1: float) -> None:
    """没有逐词时间戳时，按字宽把整段时长平摊给每个词（就地改）。

    卡拉OK / 当前词高亮 / 逐字翻牌 这些都靠 run.t_start~t_end
    判断「现在念到哪个词」。ASR 出来的字幕带 words，手打的、
    导入 SRT 的没有 —— 那种情况下每个 run 的时间戳都等于整段，
    current_run() 永远命中第一个词，看上去就是「套了卡拉OK风格
    却一动不动」。这里给它补一份按字宽等速推进的时间轴。
    """
    runs = [r for line in lines for r in getattr(line, "runs", [])
            if str(getattr(r, "text", "") or "").strip()]
    span = float(t1) - float(t0)
    if not runs or span <= 1e-6:
        return
    weights = [max(1.0, float(getattr(r, "w", 0.0) or 0.0)) for r in runs]
    total = sum(weights) or float(len(runs))
    acc = 0.0
    for run, w in zip(runs, weights):
        run.t_start = float(t0) + span * (acc / total)
        acc += w
        run.t_end = float(t0) + span * (acc / total)


def _needs_word_times(spec) -> bool:
    """这套 fx 里有没有按词走时间的动画。"""
    from cutora.render.textfx.word_fx import WORD_COLOR
    anim = getattr(spec, "anim", None)
    if anim is None:
        return False
    names = []
    for attr in ("in_name", "loop", "out_name", "in_", "out"):
        val = getattr(anim, attr, None)
        if isinstance(val, str) and val:
            names.append(val.split("+")[0])
    timed = set(WORD_COLOR) | {
        "typewriter", "flip_clock", "bullet_reveal", "outline_to_fill",
        "line_bar", "scroll",
    }
    return any(n in timed for n in names)


def _font_style_name(style: dict | None) -> str:
    if not isinstance(style, dict):
        return ""
    return str(style.get("font_style") or "").strip()


def _weight_args(style: dict | None) -> tuple[bool, bool, str]:
    from cutora.ui.panels.fonts import style_weight_args
    bold, italic, name = style_weight_args(style)
    return bold, italic, name or ""


def _script_of(style: dict | None) -> str:
    raw = ((style or {}).get("script") or "").strip().lower()
    if raw:
        return raw
    try:
        from cutora.asr.languages import get_language
        from cutora.core import config as cfg
        lang = get_language(getattr(cfg, "LANGUAGE", "en"))
        return str((lang or {}).get("script") or "latin")
    except Exception:
        return "latin"


def _qfont(family: str, px: float, bold: bool, italic: bool, tracking_em: float,
           style_name: str = "") -> QFont:
    font = get_font_manager().get_font(
        family or "Arimo",
        max(8, int(round(px))),
        bold=bold,
        italic=italic,
        style=style_name or None,
    )
    from cutora.subtitle.font_coverage import apply_no_font_merging
    apply_no_font_merging(font)
    # Always set spacing on this instance. The manager used to hand out a
    # cached QFont; mutating letterSpacing leaked into later roles (g08
    # fx_fonts tracking_em=0.08 made fx_grad's next export 79 px wider).
    if tracking_em:
        font.setLetterSpacing(
            QFont.SpacingType.AbsoluteSpacing, tracking_em * px)
    else:
        font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 100.0)
    return font


def _qfont_fallback(
    base: QFont, family: str, px: float, italic: bool, tracking_em: float,
    style_name: str, bold: bool,
) -> QFont:
    """Fallback face: inherit size / italic / letterSpacing from ``base``."""
    font = get_font_manager().get_font(
        family or "Arimo",
        max(8, int(round(px))),
        bold=bold,
        italic=italic if italic else base.italic(),
        style=style_name or None,
    )
    font.setPixelSize(base.pixelSize() or max(8, int(round(px))))
    font.setItalic(base.italic())
    font.setLetterSpacing(base.letterSpacingType(), base.letterSpacing())
    return font


def _metrics(font: QFont) -> QFontMetricsF:
    return QFontMetricsF(font)


def _split_line_runs(
    line: str,
    words: list[dict],
    cursor: int,
) -> tuple[list[tuple[str, dict | None]], int]:
    """Split a line into (token, word_entry|None) consuming `words` in order."""
    parts = line.split(" ")
    runs: list[tuple[str, dict | None]] = []
    for i, part in enumerate(parts):
        token = part if i == len(parts) - 1 else part + " "
        info = None
        if cursor < len(words) and part:
            info = words[cursor]
            cursor += 1
        runs.append((token, info))
    return runs, cursor


def _run_size(text: str, font: QFont) -> tuple[float, float, float, float]:
    fm = _metrics(font)
    w = float(fm.horizontalAdvance(text))
    return w, float(fm.height()), float(fm.ascent()), float(fm.descent())


def _fold_word(s: str) -> str:
    """T12e match fold: NFKC + apostrophe/hyphen unify, then casefold."""
    text = unicodedata.normalize("NFKC", s or "")
    for ch in "’‘`´ʼ":
        text = text.replace(ch, "'")
    for ch in "‐‑‒–—―":
        text = text.replace(ch, "-")
    text = text.replace("\u00a0", " ").replace("\u202f", " ").replace("\u2007", " ")
    return text.casefold()


def _emph_active(spec: FxSpec) -> bool:
    return bool(spec.emphasis.words or spec.emphasis.auto)


def _scatter_active(spec: FxSpec) -> bool:
    return bool(spec.scatter.enabled)


def _seg_id_of(event: dict) -> str:
    return segment_id(event)


def _token_size_px(
    spec: FxSpec,
    em: float,
    word_index: int,
    seg_id: str,
    emphasized: bool,
) -> float:
    size = em * (spec.emphasis.scale if emphasized else 1.0)
    if spec.scatter.enabled:
        rng = rng_for(seg_id, word_index, spec.anim.seed)
        r1 = rng.uniform(-1.0, 1.0)
        size = size * (1.0 + spec.scatter.size_jitter * r1)
    return max(8.0, float(size))


def _is_emph_hit(token: str, info: dict | None, hits: dict[int, EmphasisHit]) -> EmphasisHit | None:
    if info is not None and int(info["index"]) in hits:
        return hits[int(info["index"])]
    key = _fold_word(token.strip())
    if not key:
        return None
    for hit in hits.values():
        if _fold_word(hit.token) == key:
            return hit
    return None


def _build_runs(
    line: str,
    spec: FxSpec,
    style: dict,
    em: float,
    words: list[dict],
    cursor: int,
    t0: float,
    t1: float,
    hits: dict[int, EmphasisHit] | None = None,
    seg_id: str = "",
) -> tuple[list, int]:
    family = spec.fonts.body or str(style.get("font_family") or "Arimo")
    emph_fam = spec.fonts.emphasis or family
    bold = bool(style.get("bold", True))
    italic = bool(style.get("italic", False))
    tokens, cursor = _split_line_runs(line, words, cursor)
    hit_map_ = hits or {}
    built = []
    script = _script_of(style)
    for token, info in tokens:
        hit = _is_emph_hit(token, info, hit_map_)
        fam = emph_fam if (hit and spec.emphasis.font == "emphasis") else family
        idx = int(info["index"]) if info else len(built)
        size = _token_size_px(spec, em, idx, seg_id, bool(hit))
        tracking = layout_tracking_em(spec)
        style_name = _font_style_name(style)
        base = _qfont(fam, size, bold, italic, tracking, style_name=style_name)
        try:
            from cutora.subtitle.font_coverage import split_font_runs
            pieces = split_font_runs(fam, token, script=script) or [(token, fam)]
        except Exception:
            pieces = [(token, fam)]
        pad = _pop_side_pad(spec, em, bool(hit))
        fill = hit.color if hit else None
        n = len(pieces)
        for i, (piece, piece_fam) in enumerate(pieces):
            if piece_fam == fam:
                font = QFont(base)
            else:
                font = _qfont_fallback(
                    base, piece_fam, size, italic, tracking, style_name, bold)
            w, _h, asc, desc = _run_size(piece, font)
            left_pad = pad if i == 0 else 0.0
            right_pad = pad if i == n - 1 else 0.0
            w = w + left_pad + right_pad
            built.append((
                piece, info, font, w, asc, desc, piece_fam,
                bool(hit), fill, size, left_pad))
    return built, cursor


def _place_line(
    built: list,
    line_text: str,
    x0: float,
    baseline: float,
    t0: float,
    t1: float,
    spec: FxSpec,
    family: str,
    em: float,
    writing_mode: str = "horizontal",
    y0: float = 0.0,
) -> LineBox:
    if writing_mode != "horizontal":
        return _place_line_vertical(
            built, line_text, x0, y0, t0, t1, spec, family, em)
    runs: list[WordRun] = []
    x = x0
    max_h = em
    for token, info, font, w, asc, desc, fam, hit, fill, size, pad in built:
        idx = int(info["index"]) if info else len(runs)
        ts = float(info["start"]) if info else t0
        te = float(info["end"]) if info else t1
        h = asc + desc
        max_h = max(max_h, h)
        runs.append(WordRun(
            text=token,
            font_family=fam or family,
            fontsize_px=size,
            tracking_em=layout_tracking_em(spec),
            fill_override=fill,
            emphasized=bool(hit),
            word_index=idx,
            t_start=ts,
            t_end=te,
            x=x,
            y=baseline - asc,
            w=w,
            h=h,
            baseline=baseline,
            pad_px=pad,
            qfont=QFont(font) if font is not None else None,
        ))
        x += w
    width = max(0.0, x - x0)
    top = baseline - max((b[4] for b in built), default=em * 0.8)
    return LineBox(
        text=line_text, x=x0, y=top, w=width, h=max_h,
        baseline=baseline, runs=runs)


_VERTICAL_PUNCT_TR = frozenset("。、，．")


def _is_mixed_latin(ch: str) -> bool:
    """Half-width digits and Latin letters — mixed orientation rotates these."""
    if len(ch) != 1:
        return False
    o = ord(ch)
    if 0x30 <= o <= 0x39 or 0x41 <= o <= 0x5A or 0x61 <= o <= 0x7A:
        return True
    if o < 0x00C0 or o > 0x024F:
        return False
    return unicodedata.category(ch).startswith("L")


def _column_avail_height(
    style: dict, video_h: int, em: float, extra_pad: float,
) -> float:
    from cutora.subtitle.stylepack import SAFE_MARGIN, line_max_width_px
    packed = float(line_max_width_px(
        style, video_h, em=em, extra_pad=extra_pad))
    inset = (float(max(1, int(video_h))) * (1.0 - 2.0 * SAFE_MARGIN)
             - 2.0 * float(extra_pad))
    return max(1.0, min(packed, inset))


def _vertical_glyph_offset(ch: str, fm: QFontMetricsF, cell_w: float, cell_h: float,
                           ascent: float) -> tuple[float, float]:
    """Draw offset from (cell_x, cell_y + ascent) so ink sits in the 字格."""
    tbr = fm.tightBoundingRect(ch)
    tw = float(tbr.width()) or 1.0
    th = float(tbr.height()) or 1.0
    tx = float(tbr.x())
    ty = float(tbr.y())
    if ch in _VERTICAL_PUNCT_TR:
        inset = max(2.0, min(cell_w, cell_h) * 0.08)
        ox = cell_w - inset - tx - tw
        oy = inset - ascent - ty
        return ox, oy
    ox = (cell_w - tw) * 0.5 - tx
    oy = (cell_h - th) * 0.5 - ascent - ty
    return ox, oy


def _vertical_cell(ch: str, font: QFont, orientation: str) -> dict:
    fm = _metrics(font)
    adv = max(1.0, float(fm.horizontalAdvance(ch)))
    h = max(1.0, float(fm.height()))
    asc = float(fm.ascent())
    desc = float(fm.descent())
    rotate = orientation == "mixed" and _is_mixed_latin(ch)
    if rotate:
        cell_w, cell_h, orient = h, adv, 90.0
        ox = (cell_w - adv) * 0.5
        oy = (cell_h - h) * 0.5
    else:
        cell_w, cell_h, orient = adv, adv, 0.0
        ox, oy = _vertical_glyph_offset(ch, fm, cell_w, cell_h, asc)
    return {
        "cell_w": cell_w, "cell_h": cell_h, "orient": orient,
        "ox": ox, "oy": oy, "adv": adv, "h": h, "asc": asc, "desc": desc,
    }


def _place_line_vertical(
    built: list,
    line_text: str,
    x0: float,
    y0: float,
    t0: float,
    t1: float,
    spec: FxSpec,
    family: str,
    em: float,
) -> LineBox:
    """One vertical column: cells stack top → bottom, centered in column width."""
    runs: list[WordRun] = []
    col_w = max((float(item.get("cell_w") or em) for item in built), default=em)
    y = y0
    tracking = layout_tracking_em(spec) * em
    for i, item in enumerate(built):
        ch = item["ch"]
        info = item.get("info")
        font = item.get("font")
        cell_w = float(item["cell_w"])
        cell_h = float(item["cell_h"])
        idx = int(info["index"]) if info else len(runs)
        ts = float(info["start"]) if info else t0
        te = float(info["end"]) if info else t1
        x = x0 + (col_w - cell_w) * 0.5
        if i:
            y += tracking
        runs.append(WordRun(
            text=ch,
            font_family=item.get("fam") or family,
            fontsize_px=float(item.get("size") or em),
            tracking_em=layout_tracking_em(spec),
            fill_override=item.get("fill"),
            emphasized=bool(item.get("hit")),
            word_index=idx,
            t_start=ts,
            t_end=te,
            x=x,
            y=y,
            w=cell_w,
            h=cell_h,
            baseline=y + float(item.get("asc") or em * 0.8),
            pad_px=float(item.get("pad") or 0.0),
            qfont=QFont(font) if font is not None else None,
            orient_deg=float(item.get("orient") or 0.0),
            glyph_ox=float(item.get("ox") or 0.0),
            glyph_oy=float(item.get("oy") or 0.0),
        ))
        y += cell_h
    height = max(0.0, y - y0)
    return LineBox(
        text=line_text, x=x0, y=y0, w=col_w, h=height,
        baseline=y0 + (built[0].get("asc") if built else em * 0.8),
        runs=runs)


def _explode_vertical_chars(
    event: dict,
    spec: FxSpec,
    style: dict,
    em: float,
    family: str,
    hits: dict[int, EmphasisHit] | None,
    seg_id: str,
    orientation: str,
) -> list[list[dict]]:
    """Split source text into columns-to-be: a list of char-cell dicts per \\n."""
    raw = _apply_case(_join_source_text(event, _line_strings(event)), spec)
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    words = _word_entries(event)
    t0 = float(event.get("start") or 0.0)
    t1 = float(event.get("end") or (t0 + 1.0))
    hit_map_ = hits or {}
    bold, italic, style_name = _weight_args(style)
    script = _script_of(style)
    tracking = layout_tracking_em(spec)
    paragraphs: list[list[dict]] = [[]]
    wi = 0
    consumed = 0
    n_words = len(words)
    for ch in raw:
        if ch == "\n":
            paragraphs.append([])
            continue
        if ch == "\t":
            ch = " "
        info = words[wi] if wi < n_words else None
        hit = _is_emph_hit(ch, info, hit_map_) if ch.strip() else None
        fam = (spec.fonts.emphasis or family) if (
            hit and spec.emphasis.font == "emphasis") else family
        idx = int(info["index"]) if info else 0
        size = _token_size_px(spec, em, idx, seg_id, bool(hit))
        try:
            from cutora.subtitle.font_coverage import split_font_runs
            pieces = split_font_runs(fam, ch, script=script) or [(ch, fam)]
        except Exception:
            pieces = [(ch, fam)]
        piece, piece_fam = pieces[0]
        font = _qfont(
            piece_fam, size, bold, italic, tracking, style_name=style_name)
        cell = _vertical_cell(piece, font, orientation)
        cell.update({
            "ch": piece, "info": info, "font": font, "fam": piece_fam,
            "size": size, "hit": bool(hit),
            "fill": hit.color if hit else None,
            "pad": _pop_side_pad(spec, em, bool(hit)),
        })
        paragraphs[-1].append(cell)
        if info is not None:
            wtxt = str(info.get("word") or "")
            consumed += 1
            if wtxt and consumed >= len(wtxt):
                wi += 1
                consumed = 0
    if not paragraphs[-1] and len(paragraphs) > 1:
        paragraphs.pop()
    return paragraphs or [[]]


def _pack_vertical_columns(
    paragraphs: list[list[dict]],
    avail: float,
    tracking: float,
    max_cols: int,
) -> list[list[dict]]:
    """Wrap char cells into columns. A paragraph break forces a new column."""
    cap = max(1, int(max_cols))
    columns: list[list[dict]] = []
    for para in paragraphs:
        current: list[dict] = []
        used = 0.0
        for cell in para:
            step = float(cell["cell_h"])
            extra = tracking if current else 0.0
            if current and (used + extra + step) > avail and len(columns) + 1 < cap:
                columns.append(current)
                current = [cell]
                used = step
            else:
                if current:
                    used += extra
                current.append(cell)
                used += step
        if current:
            columns.append(current)
        elif not columns:
            columns.append([])
    if len(columns) > cap:
        overflow = []
        for extra in columns[cap:]:
            overflow.extend(extra)
        columns = columns[:cap]
        columns[-1].extend(overflow)
    return columns or [[]]


def _measure_and_place_vertical(
    event: dict,
    style: dict,
    spec: FxSpec,
    em: float,
    family: str,
    cx: float,
    cy: float,
    hits: dict[int, EmphasisHit] | None,
    seg_id: str,
    mode: str,
) -> tuple[list[LineBox], float]:
    from cutora.subtitle.style_schema import normalize_text_orientation
    orientation = normalize_text_orientation(style.get("text_orientation"))
    extra = _extra_line_pad(style, spec, em)
    video_h = int(style.get("_video_h") or 0)
    avail = _column_avail_height(style, video_h, em, extra) if video_h > 0 else 1e9
    tracking = layout_tracking_em(spec) * em
    gap = float(style.get("line_spacing") or 0)
    if spec.bg.kind() == "bubble":
        gap += spec.bg.line_gap_em * em
    max_cols = _style_max_lines(style)
    paragraphs = _explode_vertical_chars(
        event, spec, style, em, family, hits, seg_id, orientation)
    columns = _pack_vertical_columns(paragraphs, avail, tracking, max_cols)
    measured: list[tuple[str, list, float, float]] = []
    for col in columns:
        text = "".join(c["ch"] for c in col)
        col_w = max((float(c["cell_w"]) for c in col), default=em)
        n = len(col)
        col_h = sum(float(c["cell_h"]) for c in col) + tracking * max(0, n - 1)
        measured.append((text, col, col_w, col_h))
    block_w = (sum(item[2] for item in measured)
               + gap * max(0, len(measured) - 1))
    block_h = max((item[3] for item in measured), default=em)
    y0 = cy - block_h / 2.0
    lines: list[LineBox] = []
    if mode == "vertical_lr":
        x_cursor = cx - block_w / 2.0
        step_sign = 1.0
    else:
        x_cursor = cx + block_w / 2.0
        step_sign = -1.0
    for text, col, col_w, col_h in measured:
        if step_sign < 0:
            x_cursor -= col_w
        x0 = x_cursor
        lines.append(_place_line(
            col, text, x0, y0 + (col[0]["asc"] if col else em * 0.8),
            float(event.get("start") or 0.0),
            float(event.get("end") or 1.0),
            spec, family, em,
            writing_mode=mode, y0=y0))
        if step_sign < 0:
            x_cursor -= gap
        else:
            x_cursor += col_w + gap
    return lines, em


def _apply_scatter(
    lines: list[LineBox], spec: FxSpec, seg_id: str, em: float,
    video_w: int, video_h: int, expand: float,
) -> None:
    """Pos/rot jitter only. Size was baked into wrap + run.fontsize_px."""
    if not spec.scatter.enabled:
        return
    jx, jy = spec.scatter.pos_jitter_em
    planned: list[tuple[WordRun, float, float, float]] = []
    for line in lines:
        for run in line.runs:
            rng = rng_for(seg_id, run.word_index, spec.anim.seed)
            r1, r2, r3, r4 = (rng.uniform(-1.0, 1.0) for _ in range(4))
            _ = r1  # size already applied in _token_size_px
            planned.append((
                run,
                jx * r2 * em,
                jy * r3 * em,
                spec.scatter.rot_jitter_deg * r4,
            ))
    factor = 1.0
    from cutora.subtitle.stylepack import SAFE_MARGIN
    ix = max(float(expand), float(video_w) * SAFE_MARGIN)
    iy = max(float(expand), float(video_h) * SAFE_MARGIN)

    def _apply(f: float) -> None:
        for run, dx, dy, rot in planned:
            run.x += dx * f
            run.y += dy * f
            run.baseline += dy * f
            run.rot_deg = rot * f

    def _revert(f: float) -> None:
        for run, dx, dy, _rot in planned:
            run.x -= dx * f
            run.y -= dy * f
            run.baseline -= dy * f

    for _try in range(8):
        _apply(factor)
        tx, ty, tw, th = _union(lines)
        ok = (tx >= ix and ty >= iy
              and tx + tw <= video_w - ix
              and ty + th <= video_h - iy)
        if ok:
            for ln in lines:
                xs = [r.x for r in ln.runs] or [ln.x]
                ys = [r.y for r in ln.runs] or [ln.y]
                ln.x = min(xs)
                ln.y = min(ys)
                ln.w = max((r.x + r.w for r in ln.runs), default=ln.x + ln.w) - ln.x
                ln.h = max((r.y + r.h for r in ln.runs), default=ln.y + ln.h) - ln.y
            return
        _revert(factor)
        factor *= 0.5
    _apply(factor)
    for ln in lines:
        xs = [r.x for r in ln.runs] or [ln.x]
        ys = [r.y for r in ln.runs] or [ln.y]
        ln.x = min(xs)
        ln.y = min(ys)


def _union(lines: list[LineBox]) -> tuple[float, float, float, float]:
    if not lines:
        return 0.0, 0.0, 1.0, 1.0
    x0 = min(ln.x for ln in lines)
    y0 = min(ln.y for ln in lines)
    x1 = max(ln.x + ln.w for ln in lines)
    y1 = max(ln.y + ln.h for ln in lines)
    for ln in lines:
        for run in ln.runs:
            x0 = min(x0, run.x)
            y0 = min(y0, run.y)
            x1 = max(x1, run.x + run.w)
            y1 = max(y1, run.y + run.h)
    return x0, y0, max(1.0, x1 - x0), max(1.0, y1 - y0)


def _clamp_union(
    lines: list[LineBox],
    x: float,
    y: float,
    w: float,
    h: float,
    video_w: int,
    video_h: int,
    expand: float,
) -> None:
    """Shift the whole layout so bbox stays inside the 8% inset (and on canvas)."""
    from cutora.subtitle.stylepack import SAFE_MARGIN
    ix = max(float(expand), float(video_w) * SAFE_MARGIN)
    iy = max(float(expand), float(video_h) * SAFE_MARGIN)
    dx = 0.0
    dy = 0.0
    if w + 2.0 * ix >= video_w:
        dx = ix - x
    else:
        if x < ix:
            dx = ix - x
        if x + w > video_w - ix:
            dx = (video_w - ix) - (x + w)
    if h + 2.0 * iy >= video_h:
        dy = iy - y
    else:
        if y < iy:
            dy = iy - y
        if y + h > video_h - iy:
            dy = (video_h - iy) - (y + h)
    if dx == 0.0 and dy == 0.0:
        return
    for ln in lines:
        ln.x += dx
        ln.y += dy
        ln.baseline += dy
        for run in ln.runs:
            run.x += dx
            run.y += dy
            run.baseline += dy


def _measure_and_place(
    event: dict,
    style: dict,
    spec: FxSpec,
    em: float,
    family: str,
    cx: float,
    cy: float,
    hits: dict[int, EmphasisHit] | None = None,
    seg_id: str = "",
) -> tuple[list[LineBox], float]:
    from cutora.subtitle.style_schema import normalize_writing_mode
    mode = normalize_writing_mode(style.get("writing_mode"))
    if mode != "horizontal":
        return _measure_and_place_vertical(
            event, style, spec, em, family, cx, cy, hits, seg_id, mode)
    fitted, em, warn = _fit_event_lines(
        event, style, spec, em, family, int(style.get("_video_w") or 0),
        hits=hits, seg_id=seg_id)
    if warn:
        event["layout_warn"] = warn
        event["_fit_warn"] = warn
    else:
        event.pop("layout_warn", None)
        event.pop("_fit_warn", None)
    raw_lines = [_apply_case(x, spec) for x in fitted]
    words = _word_entries(event)
    t0 = float(event.get("start") or 0.0)
    t1 = float(event.get("end") or (t0 + 1.0))
    gap = float(style.get("line_spacing") or 0)
    para_gap = float(style.get("_para_spacing") or 0)
    if spec.bg.kind() == "bubble":
        gap += spec.bg.line_gap_em * em
    npara = str(event.get("text") or event.get("edited_text") or "").count("\n") + 1
    para_start = [False] * len(raw_lines)
    if para_gap > 0 and npara > 1:
        if len(raw_lines) == npara:
            for i in range(1, len(raw_lines)):
                para_start[i] = True
        elif event.get("_preserve_newlines"):
            orig = _line_strings(event)
            fi = 0
            for pi, _para in enumerate(orig):
                if fi >= len(raw_lines):
                    break
                if pi > 0:
                    para_start[fi] = True
                fi += 1
                while fi < len(raw_lines) and not para_start[fi]:
                    # wrapped continuations stay False; advance one fitted line
                    # belonging to this paragraph when counts mismatch.
                    if len(raw_lines) - fi <= len(orig) - pi - 1:
                        break
                    fi += 1
    cursor = 0
    measured: list[tuple[str, list, float, float]] = []
    total_h = 0.0
    extras = []
    for i, text in enumerate(raw_lines):
        built, cursor = _build_runs(
            text, spec, style, em, words, cursor, t0, t1,
            hits=hits, seg_id=seg_id)
        line_w = sum(b[3] for b in built)
        line_h = (max((b[4] for b in built), default=em * 0.8)
                  + max((b[5] for b in built), default=em * 0.2))
        measured.append((text, built, line_w, line_h))
        extra = gap + (para_gap if para_start[i] else 0.0)
        extras.append(extra)
        total_h += line_h
        if i > 0:
            total_h += extras[i]
    y_cursor = cy - total_h / 2.0
    block_w = max((item[2] for item in measured), default=1.0)
    lines: list[LineBox] = []
    for i, (text, built, line_w, line_h) in enumerate(measured):
        x0 = _line_x0(cx, block_w, line_w, spec, i)
        font = built[0][2] if built else _qfont(family, em, True, False, 0)
        baseline = y_cursor + float(_metrics(font).ascent())
        lines.append(_place_line(built, text, x0, baseline, t0, t1, spec, family, em))
        y_cursor += line_h + (extras[i + 1] if i + 1 < len(extras) else 0.0)
    return lines, em


def _line_x0(cx: float, block_w: float, line_w: float, spec: FxSpec, index: int) -> float:
    if spec.bg.kind() != "bubble":
        return cx - line_w / 2.0
    left = cx - block_w / 2.0
    align = spec.bg.align
    if align == "alternate":
        align = "left" if index % 2 == 0 else "right"
    if align == "left":
        return left
    if align == "right":
        return left + block_w - line_w
    return left + (block_w - line_w) / 2.0


def bg_panel_rect(x: float, y: float, w: float, h: float, spec: FxSpec, em: float) -> QRectF:
    """Background panel for one content box (the text union, or one bubble line).

    ``fit_text`` is the historical pad around the glyphs. ``fixed`` is a
    canvas-pixel box centered on that content, plus ``offset_x/y``. A 0
    width or height uses the text size on that axis (not the padded size).
    """
    pad_x = spec.bg.pad_em[0] * em
    pad_y = spec.bg.pad_em[1] * em
    gl, gt, gr, gb = spec.bg.grow_px(em)   # Q129：四边扩展，任何模式都算
    if spec.bg.size_mode != "fixed":
        return QRectF(
            x - pad_x - gl, y - pad_y - gt,
            w + pad_x * 2.0 + gl + gr, h + pad_y * 2.0 + gt + gb)
    bw = float(spec.bg.width_px or 0.0)
    bh = float(spec.bg.height_px or 0.0)
    if bw <= 0.0:
        bw = float(w)
    if bh <= 0.0:
        bh = float(h)
    cx = float(x) + float(w) * 0.5 + float(spec.bg.offset_x or 0.0)
    cy = float(y) + float(h) * 0.5 + float(spec.bg.offset_y or 0.0)
    return QRectF(cx - bw * 0.5 - gl, cy - bh * 0.5 - gt, bw + gl + gr, bh + gt + gb)


def fixed_bg_bounds(lines, tx, ty, tw, th, spec: FxSpec, em: float):
    """Canvas bounds of a fixed-size background, or None for fit_text / off."""
    if spec.bg.size_mode != "fixed" or spec.bg.kind() == "none":
        return None
    if spec.bg.kind() == "bubble":
        rects = [
            bg_panel_rect(line.x, line.y, line.w, line.h, spec, em)
            for line in lines
        ]
    else:
        rects = [bg_panel_rect(tx, ty, tw, th, spec, em)]
    if not rects:
        return None
    x0 = min(r.left() for r in rects)
    y0 = min(r.top() for r in rects)
    x1 = max(r.right() for r in rects)
    y1 = max(r.bottom() for r in rects)
    return (x0, y0, x1, y1)


def visible_hit_box(tx, ty, tw, th, spec: FxSpec, em: float):
    """Clickable area: bg panel if any, else text bbox. No glow/shadow pad."""
    if spec.bg.kind() != "none":
        if spec.bg.size_mode == "fixed":
            rect = bg_panel_rect(tx, ty, tw, th, spec, em)
            return (rect.x(), rect.y(), rect.width(), rect.height())
        gl, gt, gr, gb = spec.bg.grow_px(em)
        if spec.bg.size_mode == "hug_glyphs":
            stroke_px = (
                float(spec.stroke.width_em) * em if spec.stroke.live() else 0.0)
            pad = max(0.0, float(spec.bg.hug_pad_em)) * em + stroke_px
            return (
                tx - pad - gl + float(spec.bg.offset_x or 0.0),
                ty - pad - gt + float(spec.bg.offset_y or 0.0),
                tw + 2.0 * pad + gl + gr,
                th + 2.0 * pad + gt + gb,
            )
        if spec.bg.size_mode == "line_hug":
            pad = float(spec.bg.hug_pad_em) * em
            return (
                tx - pad - gl + float(spec.bg.offset_x or 0.0),
                ty - pad - gt + float(spec.bg.offset_y or 0.0),
                max(0.0, tw + 2.0 * pad + gl + gr),
                max(0.0, th + 2.0 * pad + gt + gb),
            )
        pad_x = spec.bg.pad_em[0] * em
        pad_y = spec.bg.pad_em[1] * em
        return (tx - pad_x - gl, ty - pad_y - gt,
                tw + 2.0 * pad_x + gl + gr, th + 2.0 * pad_y + gt + gb)
    return (tx, ty, tw, th)


def layout_event(
    event: dict,
    style: dict,
    spec: FxSpec,
    video_w: int,
    video_h: int,
) -> LayoutResult:
    """Place runs using T04e center-anchor geometry. ROI is text ∪ effect pad."""
    from cutora.subtitle.stylepack import rendered_center_px, resolve_style_geometry
    from cutora.render.textfx.spec import expansion_px

    s = resolve_style_geometry(style, video_w, video_h)
    s["_video_w"] = int(video_w)
    s["_video_h"] = int(video_h)
    em = float(s.get("fontsize") or 34)
    family = spec.fonts.body or str(s.get("font_family") or "Arimo")
    cx, cy = rendered_center_px(style, video_w, video_h)
    seg = segment_id(event)
    glossary = s.get("_glossary")
    if glossary is None and "glossary" in (spec.emphasis.auto or []):
        from cutora.asr.glossary import combined_terms
        glossary = combined_terms()
    raw_text = _join_source_text(event, _line_strings(event))
    hits_list = detect_emphasis(
        raw_text, spec, glossary=list(glossary or []),
        lines=_line_strings(event))
    hits = hit_map(hits_list)
    built_lines, em = _measure_and_place(
        event, s, spec, em, family, cx, cy, hits=hits, seg_id=seg)
    if not has_word_times(event) and _needs_word_times(spec):
        _t0 = float(event.get("start") or 0.0)
        _t1 = float(event.get("end") or (_t0 + 1.0))
        spread_word_times(built_lines, _t0, _t1)
    expand = expansion_px(spec, em)
    _apply_scatter(built_lines, spec, seg, em, video_w, video_h, expand)
    tx, ty, tw, th = _union(built_lines)
    clamp_expand = expand
    if (not spec.shadow._legacy) and (
            tw + 2.0 * expand >= video_w or th + 2.0 * expand >= video_h):
        from cutora.subtitle.stylepack import SAFE_MARGIN
        clamp_expand = min(
            expand,
            max(float(video_w) * SAFE_MARGIN, max(0.0, (video_w - tw) / 2.0)),
            max(float(video_h) * SAFE_MARGIN, max(0.0, (video_h - th) / 2.0)),
        )
    _clamp_union(built_lines, tx, ty, tw, th, video_w, video_h, clamp_expand)
    tx, ty, tw, th = _union(built_lines)
    pl, pt, pr, pb = spec.max_transform_bounds()
    pad_l, pad_t, pad_r, pad_b = pl * em, pt * em, pr * em, pb * em
    # fit_text keeps the historical text-union ROI. fixed grows it so a
    # box larger than the glyphs is not clipped. The fit_text numbers are
    # the same expressions as before (left == tx, right == tx + tw).
    left, top, right, bot = tx, ty, tx + tw, ty + th
    fixed_bounds = fixed_bg_bounds(built_lines, tx, ty, tw, th, spec, em)
    if fixed_bounds is not None:
        left = min(left, fixed_bounds[0])
        top = min(top, fixed_bounds[1])
        right = max(right, fixed_bounds[2])
        bot = max(bot, fixed_bounds[3])
    ox = max(0, int(left - expand - pad_l))
    oy = max(0, int(top - expand - pad_t))
    x1 = min(video_w, int(right + expand + pad_r + 1))
    y1 = min(video_h, int(bot + expand + pad_b + 1))
    if fixed_bounds is not None:
        hx, hy = fixed_bounds[0], fixed_bounds[1]
        hw = fixed_bounds[2] - fixed_bounds[0]
        hh = fixed_bounds[3] - fixed_bounds[1]
    else:
        hx, hy, hw, hh = visible_hit_box(tx, ty, tw, th, spec, em)
    return LayoutResult(
        lines=built_lines, em=em, cx=cx, cy=cy,
        text_x=tx, text_y=ty, text_w=tw, text_h=th,
        origin=(ox, oy),
        roi_w=max(1, x1 - ox), roi_h=max(1, y1 - oy),
        hit_x=hx, hit_y=hy, hit_w=hw, hit_h=hh,
        seg_id=seg,
        n_units=max(1, sum(len(ln.runs) for ln in built_lines) or 1),
    )
