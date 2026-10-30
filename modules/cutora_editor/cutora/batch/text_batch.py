# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q120 · 批量加文本条：表格粘贴 → 自动定位到字幕时间 → 落到文本轨。

这里是纯逻辑，不碰 Qt：
- ``parse_paste``  Google Sheets / Excel 里 Ctrl+C 出来的那一坨，切成一格一格
- ``locate_text``  一格文字在这条视频的字幕里从哪句开始、到哪句结束
- ``plan_rows``    每一行落在哪个时间段、对不上时的退路
- ``apply_rows``   真往每条视频的时间线上新建文本轨 + 文本条

对时规则（Luca 定的）：一格文字算**一整条**文本 —— 首句在字幕里的开始
到末句的结束。说完这几句这条文本就结束。
"""
from __future__ import annotations

import copy
import csv
import io
import re
import time

from cutora.core.log import get_logger

_LOG = get_logger("cutora.batch.text_batch")

# 句末标点：中英阿都收（英文句点独立处理，避免拆散小数 3.5 或经文 22.14）
_SENT_END = "。！？!?…；;؟\n"
_SENT_SPLIT_RE = re.compile(
    r"(?:(?<=[。！？；؟])|(?<=[!?])[\"'”’)\]]*\s+|(?<=[A-Za-z0-9\"'”’)\]])\.(?![0-9])[\"'”’)\]]*(?:\s+|$)|(?<=…)\s+(?=[A-Z\u4e00-\u9fff]))"
)
# 归一化时丢掉的东西：空白 + 各种标点。文案和 ASR 出来的字幕标点常常对不上，
# 留着只会让匹配失败。
_DROP = re.compile(
    r"[\s，,。．\.、！!？\?；;：:…—－\-~～\"“”'‘’（）()《》〈〉\[\]【】{}｜|/\\]+")

DEFAULT_DURATION = 5.0
# 整句找不到时，退而用句子开头/结尾这么多个字去找
_PARTIAL_MIN = 4
_PARTIAL_MAX = 14


# ----------------------------------------------------------------- 粘贴

def parse_paste(text: str) -> list[str]:
    """把剪贴板里的表格切成一格一格。

    Google Sheets / Excel 复制出来是 TSV：列用 Tab 分，行用换行分，
    **单元格内部的换行被整体包在双引号里**。所以不能按行 split，
    得让 csv 认。一个单元格就是一格内容，里面有几个换行都还是一格。
    """
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not raw.strip():
        return []
    rows = list(csv.reader(io.StringIO(raw), delimiter="\t"))
    out = []
    for row in rows:
        if not row:
            out.append("")
            continue
        # 表里只有一列文本；多列时取第一列，右边的当附注忽略
        out.append(str(row[0] or "").strip("\n"))
    while out and not out[-1].strip():
        out.pop()
    return out


# ------------------------------------------------------------- 文字归一

def normalize(text: str) -> str:
    """去掉空白和标点再比。文案里的「，」字幕里可能没有，不能因此就算没找到。"""
    return _DROP.sub("", str(text or "")).lower()


def split_sentences(text: str) -> list[str]:
    """按句末标点和换行断句，空句丢掉。

    正确处理英文句号 ``.``（不拆小数 ``3.5`` 或章节 ``22.14``），且句中悬停省略号
    ``Did you know… there is``（后接小写词）不误拆成两句。
    """
    out: list[str] = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        for piece in _SENT_SPLIT_RE.split(line):
            piece = piece.strip()
            if piece and normalize(piece):
                out.append(piece)
    return out


def _flatten(segments):
    """字幕拼成一条归一化长串 + 每个字属于哪一条 segment。"""
    chars, owner = [], []
    rows = []
    for i, seg in enumerate(segments or []):
        text = seg.get("text") if isinstance(seg, dict) else getattr(seg, "text", "")
        start = seg.get("start") if isinstance(seg, dict) else getattr(seg, "start", None)
        end = seg.get("end") if isinstance(seg, dict) else getattr(seg, "end", None)
        try:
            start = float(start)
            end = float(end)
        except (TypeError, ValueError):
            continue
        norm = normalize(text)
        if not norm:
            continue
        rows.append({"i": i, "start": start, "end": end})
        for ch in norm:
            chars.append(ch)
            owner.append(len(rows) - 1)
    return "".join(chars), owner, rows


def _find(haystack: str, owner, rows, needle: str, *, from_tail=False):
    """在长串里找 needle，返回 (起 segment, 止 segment)。找不到返回 None。

    整句找不到就退一步：用句子开头（或结尾）的几个字去找 —— 文案和 ASR
    的字幕常常差一两个字，整句比对太脆。
    """
    if not needle or not haystack:
        return None
    pos = haystack.find(needle)
    if pos >= 0:
        return rows[owner[pos]], rows[owner[pos + len(needle) - 1]]
    for n in range(min(_PARTIAL_MAX, len(needle)), _PARTIAL_MIN - 1, -1):
        part = needle[-n:] if from_tail else needle[:n]
        pos = haystack.find(part)
        if pos >= 0:
            return rows[owner[pos]], rows[owner[pos + len(part) - 1]]
    return None


def locate_text(segments, text: str) -> dict:
    """一格文字在口播 / 字幕里的时间段。

    Q190：按词对齐（``title_align.align_title``），数字 / 序数 / 缩写 / 标点 / 大小写全部归一，
    允许个别词听错。返回 {"ok", "start", "end", "how", "why", "score", "diff", "said", …}。how:
      matched = 吻合；partial = 基本吻合（有几个词不同，``diff`` 列出）；
      mismatch = 视频内容与标题文字不符；window_short = 标题说到一半识别范围到头了；
      no_speech = 识别范围里没听到说话；empty = 这一格是空的；no_subs = 这条视频还没有字幕。
    """
    from cutora.batch.title_align import align_title
    rows = [sg for sg in (segments or []) if _seg_ok(sg)]
    if not rows:
        return {"ok": False, "start": None, "end": None, "how": "no_subs",
                "why": "这条视频还没有字幕", "score": 0.0, "diff": [], "said": ""}
    got = align_title(rows, text)
    return got


def _seg_ok(seg) -> bool:
    start = seg.get("start") if isinstance(seg, dict) else getattr(seg, "start", None)
    end = seg.get("end") if isinstance(seg, dict) else getattr(seg, "end", None)
    try:
        float(start)
        float(end)
    except (TypeError, ValueError):
        return False
    return True


def follow_title_span(task, segments, text, duration):
    """图片该从标题结束的那一刻放到视频结尾。

    返回 (start, end, how) 或 None。how 是 ``title``（对上了文案）
    或 ``overlay``（时间线上已经有一条开头的文本）。
    放不下一小段（标题已经顶到结尾）就返回 None，调用方继续用手填的时间。
    """
    try:
        dur = float(duration)
    except (TypeError, ValueError):
        return None
    if dur <= 0.2:
        return None

    def _fit(end):
        try:
            end = float(end)
        except (TypeError, ValueError):
            return None
        if dur <= end + 0.05:
            return None
        return max(0.0, end), dur

    needle = str(text or "").strip()
    if needle and segments:
        found = locate_text(segments, needle)
        if found.get("ok"):
            span = _fit(found.get("end"))
            if span:
                return span[0], span[1], "title"

    # 时间线上已经有开头的文本条。整条视频都挂着的那种（例如「Generated by AI」
    # 角标、水印）不是标题 —— 以前它和标题都从 0 秒开始，它排在前面就被当成标题，
    # 结尾又顶到视频末尾放不下图，整条回退成手填时间（Luca 2026-10-12）。
    best = None
    for ov in (task or {}).get("text_overlays") or []:
        if not isinstance(ov, dict):
            continue
        try:
            start = float(ov.get("start") or 0)
            end = float(ov.get("end") or 0)
        except (TypeError, ValueError):
            continue
        if not (end > start + 0.05 and start <= 30.0):
            continue
        if _is_persistent_label(start, end, dur):
            continue
        if _fit(end) is None:
            continue
        # 最早开始的那条；同时开始的取结束晚的（标题比同时出现的小字长）
        if best is None or start < best[0] - 1e-6 or (
                abs(start - best[0]) <= 1e-6 and end > best[1]):
            best = (start, end)
    if best:
        span = _fit(best[1])
        if span:
            return span[0], span[1], "overlay"
    return None


def _is_persistent_label(start, end, dur) -> bool:
    """整条视频都在的文本（角标 / 水印 / AI 标注）：盖住了视频的大半，一直到结尾。"""
    if dur <= 0:
        return False
    covers = (end - start) / dur
    return covers >= 0.8 or (start <= 0.5 and end >= dur - 0.5)


# ----------------------------------------------------------------- 排期

def plan_rows(tasks, rows, *, auto_locate=True, default_dur=DEFAULT_DURATION,
              default_start=0.0, segments_of=None) -> list[dict]:
    """算出每一行该落在哪个时间段。不改任何数据。

    rows: [{"task_idx": int, "text": str, "start": float|None, "dur": float|None}]
    segments_of: 可选，task -> 字幕列表。界面会传一个「先看 task['segments']，
      没有就看只为定位跑的那份识别缓存」的函数进来。
    对不上的照样加（Luca 定的）：用默认时长，标 warn，界面上标黄。
    """
    plans = []
    for row in rows or []:
        idx = int((row or {}).get("task_idx", -1))
        text = str((row or {}).get("text") or "")
        plan = {"task_idx": idx, "text": text, "how": "manual",
                "warn": "", "ok": bool(text.strip()),
                "tpl": str((row or {}).get("tpl") or "")}
        if not plan["ok"]:
            plan["warn"] = "空文本，跳过"
            plans.append(plan)
            continue
        start = row.get("start")
        dur = row.get("dur")
        start = float(start) if start not in (None, "") else None
        dur = float(dur) if dur not in (None, "") else None
        task = tasks[idx] if 0 <= idx < len(tasks or []) else {}
        pick = row.get("pick")
        if isinstance(pick, (list, tuple)) and len(pick) == 2:
            # Q190：用户在原话里划选了词 → 起止就是那几个词的时间，不再自动找
            try:
                a, b = float(pick[0]), float(pick[1])
                plan.update(how="picked", reason="picked", start=max(0.0, a),
                            end=max(a + 0.2, b), warn="")
                plans.append(plan)
                continue
            except (TypeError, ValueError):
                pass
        if auto_locate:
            if callable(segments_of):
                segs = segments_of(task) or []
            else:
                segs = (task or {}).get("segments") or []
            found = locate_text(segs, text)
            plan["score"] = float(found.get("score") or 0.0)
            plan["diff"] = list(found.get("diff") or [])
            plan["said"] = str(found.get("said") or "")
            plan["reason"] = str(found.get("how") or "")
            if found["ok"]:
                plan["how"] = found["how"]
                plan["start"] = float(found["start"])
                plan["end"] = float(max(found["end"], found["start"] + 0.2))
                plan["warn"] = found.get("why") or ""
                plans.append(plan)
                continue
            plan["warn"] = found.get("why") or "没定位到"
        s = start if start is not None else float(default_start)
        d = dur if dur is not None else float(default_dur)
        plan["start"] = max(0.0, s)
        plan["end"] = plan["start"] + max(0.2, d)
        plan["how"] = "manual" if not auto_locate else "fallback"
        plans.append(plan)
    return plans


# ----------------------------------------------------------------- 模板

# 模板样式里不属于「样式」的键（示例文字、身份、时间……）
STYLE_DROP = frozenset({
    "content", "text", "id", "name", "builtin", "created_at", "updated_at",
    "start", "end", "_track_id", "_preset_name", "source_preset_id",
    "type", "spans", "_rich_cache", "_script", "_canvas_w", "_canvas_h",
    "layout", "animation",
})
FALLBACK_STYLE = {
    "fontsize": 80, "font_family": "Arimo", "bold": True,
    "color": "#FFFFFF", "use_stroke": True,
    "stroke_color": "#000000", "stroke_width": 4, "rotation": 0,
}


def preset_names() -> list[str]:
    """文本层预设的名字（批量表格「套用模板」下拉里的那些）。"""
    from cutora.subtitle import text_preset as TP
    out = []
    try:
        for row in TP.list_presets() or []:
            name = str((row or {}).get("name") or "").strip()
            if name and name not in out:
                out.append(name)
    except Exception:
        _LOG.exception("读文本预设列表失败")
    return out


def match_preset_name(raw: str) -> str | None:
    """粘进来的模板名 → 预设里的正式名字（忽略首尾空白和大小写）；空 = 跟随上方；不认识 = None。"""
    want = str(raw or "").strip()
    if not want or want in ("跟随上方", "跟随上方模板", "↑ 跟随上方"):
        return ""
    names = preset_names()
    if want in names:
        return want
    low = want.lower()
    for n in names:
        if n.lower() == low:
            return n
    return None


def template_for_preset(ref, canvas_w: int, canvas_h: int) -> dict:
    """一个预设 → 批量加文本用的 {"style", "layout", "preset_id", "preset_name"}。

    样式即成品（Q136）：位置、框宽、字号、颜色、背景、缩放都照预设；存过比例框的
    （layout）按每条视频自己的画布再换算。"""
    from cutora.subtitle import text_preset as TP
    style: dict = {}
    layout = None
    preset = None
    try:
        token = str(ref or "").strip()
        preset = TP.resolve_preset(token or TP.get_default_preset_id())
        if preset:
            for key, val in (TP.template_from_preset(preset) or {}).items():
                if key in STYLE_DROP or str(key).startswith("_") or val is None:
                    continue
                style[key] = copy.deepcopy(val)
            raw_layout = preset.get("layout")
            if isinstance(raw_layout, dict):
                layout = raw_layout
    except Exception:
        _LOG.exception("读取文本模板失败")
    if "fontsize" not in style:
        style.update(FALLBACK_STYLE)
    if isinstance(layout, dict):
        try:
            sample = str((preset or {}).get("text") or "") if preset else ""
            x, y, w, h, fs = TP.layout_to_box(layout, int(canvas_w), int(canvas_h), sample or "Sample text")
            style["x"], style["y"], style["w"], style["h"] = float(x), float(y), float(w), float(h)
            if not style.get("fontsize"):
                style["fontsize"] = float(fs)
            try:
                sc = float(layout.get("scale") or 1.0)
            except (TypeError, ValueError):
                sc = 1.0
            if abs(sc - 1.0) > 1e-6:
                style["scale"] = sc
            else:
                style.pop("scale", None)
        except Exception:
            _LOG.exception("模板位置换算失败")
    return {
        "style": style,
        "layout": copy.deepcopy(layout) if isinstance(layout, dict) else None,
        "preset_id": str((preset or {}).get("id") or "") if preset else "",
        "preset_name": str(ref or "").strip(),
    }


# ----------------------------------------------------------------- 落地

def build_overlay(text, start, end, *, width, height, template=None,
                  preset_name=None, track_id=None, ov_id=None,
                  layout=None, preset_id=None) -> dict:
    """一条文本 overlay。

    样式不在这里另起一套：直接用 batch_load.make_text_overlay，它已经会去
    读 T11 那套文本轨预设（就是「批量加载」里那个预设下拉里的东西）。
    ``template`` 是用户在预览里现场调出来的样式，盖在预设之上。
    """
    from cutora.batch.batch_load import make_text_overlay

    ov = make_text_overlay(
        preset_name or None, content=str(text or ""),
        start=float(start), end=float(end),
        dims=(int(width), int(height)))
    if isinstance(template, dict):
        style = dict(template)
        # 模板里带的示例文字不要，用户粘的才是内容
        for k in ("content", "_preset_name", "source_preset_id", "start", "end"):
            style.pop(k, None)
        for k, v in style.items():
            if v is not None:
                ov[k] = v
    ov["content"] = str(text or "")
    ov["start"] = float(start)
    ov["end"] = float(end)
    ov["id"] = int(ov_id if ov_id is not None else time.time() * 1000000) % (1 << 62)
    ov["_track_id"] = track_id
    if isinstance(layout, dict):
        # 模板样式里的 x/y/w/h/字号是套用那一刻那张画布的像素。
        # 每条视频按自己的画布把比例框重算一遍，字号跟短边走。
        from cutora.subtitle.text_preset import apply_layout_to_overlay
        apply_layout_to_overlay(ov, layout, int(width), int(height), ov["content"])
    if preset_id:
        ov["source_preset_id"] = str(preset_id)
    fit_box_to_text(ov, int(width), int(height))
    return ov


# Q141：模板的框是按模板里的示例字存的；粘进来的文字往往更长，框却还是
# 那么窄 —— 一句话被折成好几行挤在小框里（Luca：「还是很小的一个文本框」）。
# 字号 / 颜色 / 位置照模板，框宽按这段文字需要的宽度放开：能一行放下就一行，
# 放不下就放到画布可用宽度再折行。以原框中心为准左右对称地放宽，不出画布。
FIT_MAX_FRAC = 0.92


def fit_box_to_text(ov: dict, canvas_w: int, canvas_h: int) -> dict:
    try:
        from PySide6.QtGui import QGuiApplication
        if QGuiApplication.instance() is None:
            return ov          # 量字要字体；没有 Qt 应用（命令行 / 纯逻辑）就照模板
    except Exception:
        return ov
    from cutora.render.text_layer_paint import layout_for_overlay
    from cutora.subtitle.text_layer import overlay_text_layout
    text = str(ov.get("content") or "").strip()
    if not text or canvas_w <= 0:
        return ov
    if str(ov.get("fit_mode") or "none") in ("none", ""):
        ov["fit_mode"] = "auto_height"      # 高度跟着文字走，不会把第二行裁掉
    if str(ov.get("fit_mode")) == "shrink":
        return ov                            # 用户要的就是「缩进框里」
    try:
        w0 = float(ov.get("w") or 0)
        x0 = float(ov.get("x") or 0)
        sc = float(ov.get("scale") or 1.0) or 1.0
    except (TypeError, ValueError):
        return ov
    pads = overlay_text_layout(ov)
    padx = float(pads.get("left_pad") or 0) + float(pads.get("right_pad") or 0)
    max_w = max(w0, canvas_w * FIT_MAX_FRAC / max(0.1, sc))
    probe = dict(ov)
    probe["w"] = max_w
    probe["x"] = 0.0
    try:
        layout = layout_for_overlay(probe, canvas_w, canvas_h)[0]
    except Exception:
        return ov
    n_wide = len(layout.lines or [])
    need = float(layout.text_w or 0) + padx + 2.0
    if need <= w0 + 0.5:
        return ov
    new_w = min(max_w, need)
    # 量出来的字宽和折行判断之间有零点几像素的出入（描边外扩、字距取整），
    # 按这个宽度再排一次，行数比宽框时多就再放一点，直到一样（最多几次）
    em = float(ov.get("fontsize") or 40)
    for _ in range(6):
        if new_w >= max_w:
            break
        probe["w"] = new_w
        try:
            n = len(layout_for_overlay(dict(probe), canvas_w, canvas_h)[0].lines or [])
        except Exception:
            break
        if n <= n_wide:
            break
        new_w = min(max_w, new_w + em * 0.25)
    cx = x0 + w0 * 0.5
    nx = cx - new_w * 0.5
    # 放宽后仍在画布里（考虑整体缩放后看得见的宽度）
    vis = new_w * sc
    left = cx - vis * 0.5
    if left < 0:
        nx += -left / sc if sc else -left
    elif left + vis > canvas_w:
        nx -= (left + vis - canvas_w) / sc if sc else (left + vis - canvas_w)
    ov["x"] = float(nx)
    ov["w"] = float(new_w)
    if ov.get("h") and ov.get("w"):
        ov["aspect_ratio"] = float(ov["w"]) / max(1.0, float(ov["h"]))
    return ov


def apply_rows(plans, tasks, *, template=None, preset_name=None,
               new_track=True, host=None, layout=None, preset_id=None,
               row_templates=None) -> dict:
    """把排好的文本条加到各自视频的时间线上。返回统计。

    Q153：plan["tpl"] 非空 = 这一行单独套用那个预设；row_templates 是
    {预设名: template_for_preset(...) 的结果}，界面算好传进来（没传就现算）。"""
    from cutora.model.timeline_model import Track
    done = {"tasks": 0, "overlays": 0, "skipped": 0, "tracks": 0}
    touched = set()
    for plan in plans or []:
        if not plan.get("ok"):
            done["skipped"] += 1
            continue
        idx = int(plan.get("task_idx", -1))
        if not (0 <= idx < len(tasks or [])):
            done["skipped"] += 1
            continue
        task = tasks[idx]
        tl = (task or {}).get("timeline")
        if tl is None:
            done["skipped"] += 1
            continue
        track = None
        if new_track:
            track = _fresh_text_track(tl, touched, idx, Track)
        else:
            track = _first_text_track(tl) or _fresh_text_track(tl, touched, idx, Track)
        width = int(getattr(tl, "width", 0) or (task.get("dims") or (1080, 1920))[0])
        height = int(getattr(tl, "height", 0) or (task.get("dims") or (1080, 1920))[1])
        tpl_name = str(plan.get("tpl") or "")
        r_template, r_layout, r_pid, r_pname = template, layout, preset_id, preset_name
        if tpl_name:
            tpl = (row_templates or {}).get(tpl_name)
            if tpl is None:
                tpl = template_for_preset(tpl_name, width, height)
                if row_templates is not None:
                    row_templates[tpl_name] = tpl
            r_template = tpl["style"]
            r_layout = tpl["layout"]
            r_pid = tpl["preset_id"] or None
            r_pname = tpl_name
        ov = build_overlay(
            plan["text"], plan["start"], plan["end"],
            width=width, height=height, template=r_template,
            preset_name=r_pname,
            track_id=getattr(track, "id", None),
            ov_id=int(time.time() * 1000000) + done["overlays"],
            layout=r_layout, preset_id=r_pid)
        task.setdefault("text_overlays", []).append(ov)
        done["overlays"] += 1
        if idx not in touched:
            touched.add(idx)
            done["tasks"] += 1
    return done


def _fresh_text_track(tl, touched, idx, Track):
    """这一批每条视频只新建一条文本轨，同一条视频的多行共用它。"""
    cache = getattr(tl, "_batch_text_track", None)
    if cache is not None and cache in (getattr(tl, "tracks", None) or []):
        return cache
    track = Track("text")
    tl.add_track(track)
    try:
        tl._batch_text_track = track
    except AttributeError:
        pass
    return track


def _first_text_track(tl):
    for tr in getattr(tl, "tracks", None) or []:
        if getattr(tr, "type", None) == "text":
            return tr
    return None
