# -*- coding: utf-8 -*-
"""T04f-6b2 re-record gate: weight proof, case stills, g09 line breaks.

Does not rewrite golden expected.* files.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

IMG = os.path.join(REPO, "docs", "plan", "reports", "img")
REP = os.path.join(REPO, "docs", "plan", "reports")
FFMPEG = os.path.join(REPO, "runtime", "ffmpeg", "ffmpeg.exe")
FFPROBE = os.path.join(REPO, "runtime", "ffmpeg", "ffprobe.exe")
GOLDEN = os.path.join(REPO, "tests", "golden")
DUMP = os.path.join(REPO, "docs", "plan", "reports", "nondeterminism")

# Latest after-2.2 dumps (r1).
GOT = {
    "g01_vertical_basic": os.path.join(DUMP, "g01_vertical_basic_r01_20260913_163840", "got_out_00.mp4"),
    "g02_vertical_fx": os.path.join(DUMP, "g02_vertical_fx_r01_20260913_163850", "got_out_00.mp4"),
    "g03_horizontal_it": os.path.join(DUMP, "g03_horizontal_it_r01_20260913_163922", "got_out_00.mp4"),
    "g04_stitch": os.path.join(DUMP, "g04_stitch_r01_20260913_163927", "got_out_00.mp4"),
    "g05_scatter_insert": os.path.join(DUMP, "g05_scatter_insert_r01_20260913_163938", "got_out_00.mp4"),
    "g06_parallel": os.path.join(DUMP, "g06_parallel_r01_20260913_164107", "got_out_00.mp4"),
    "g07_cli": os.path.join(DUMP, "g07_cli_r01_20260913_164116", "got_out_00.mp4"),
    "g09_linebreak_latin": os.path.join(DUMP, "g09_linebreak_latin_r01_20260913_164127", "got_out_00.mp4"),
}
EXP = {
    "g01_vertical_basic": os.path.join(GOLDEN, "g01_vertical_basic", "expected.mp4"),
    "g02_vertical_fx": os.path.join(GOLDEN, "g02_vertical_fx", "expected.mp4"),
    "g03_horizontal_it": os.path.join(GOLDEN, "g03_horizontal_it", "expected.mp4"),
    "g04_stitch": os.path.join(GOLDEN, "g04_stitch", "expected_00.mp4"),
    "g05_scatter_insert": os.path.join(GOLDEN, "g05_scatter_insert", "expected.mp4"),
    "g06_parallel": os.path.join(GOLDEN, "g06_parallel", "expected_00.mp4"),
    "g07_cli": os.path.join(GOLDEN, "g07_cli", "expected.mp4"),
    "g09_linebreak_latin": os.path.join(GOLDEN, "g09_linebreak_latin", "expected.mp4"),
}

SENTENCE = "The hill is lit."
G01_LINE = "They waited in the shade of the old"
PT = 35


def _save(img, name):
    os.makedirs(IMG, exist_ok=True)
    path = os.path.join(IMG, name)
    img.save(path, "PNG")
    print("wrote", path, flush=True)
    return path


def _gray(qimg):
    import numpy as np
    from cutora.render.textfx.raster import as_bgra
    _, bgra = as_bgra(qimg)
    # BGRA; luminance
    b = bgra[..., 0].astype(np.float32)
    g = bgra[..., 1].astype(np.float32)
    r = bgra[..., 2].astype(np.float32)
    a = bgra[..., 3].astype(np.float32)
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return lum, a


def _stem_and_ink(lum, a=None, xh_frac=0.52):
    import numpy as np
    ink = lum > 40
    if a is not None:
        ink = ink & (a > 40)
    total = int(ink.sum())
    ys = np.where(ink.any(axis=1))[0]
    xs = np.where(ink.any(axis=0))[0]
    if len(ys) == 0 or len(xs) == 0:
        return 0.0, total, (0, 0, 0, 0)
    y0, y1 = int(ys[0]), int(ys[-1])
    x0, x1 = int(xs[0]), int(xs[-1])
    y = y0 + int(round((y1 - y0) * xh_frac))
    y = min(max(y, y0), y1)
    row = ink[y]
    runs = []
    n = 0
    for v in row.tolist():
        if v:
            n += 1
        elif n:
            runs.append(n)
            n = 0
    if n:
        runs.append(n)
    stems = [w for w in runs if 2 <= w <= 28]
    stem = float(sorted(stems)[len(stems) // 2]) if stems else 0.0
    return stem, total, (x0, y0, x1 - x0 + 1, y1 - y0 + 1)


def _probe(name, style_or_family, sentence, pt):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QFontInfo, QImage, QPainter
    from cutora.ui.panels.fonts import get_font_manager, resolve_font_request

    img = QImage(1080, 240, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor("#0B1A33"))
    p = QPainter(img)
    if style_or_family in ("Regular", "SemiBold", "Bold"):
        font = QFont("SN Pro")
        font.setPointSize(int(pt))
        font.setStyleName(style_or_family)
        rec = {"kind": "explicit", "family": "SN Pro", "style": style_or_family}
    else:
        rec = resolve_font_request(style_or_family)
        font = get_font_manager().get_font(style_or_family, int(pt), bold=True, italic=False)
    info = QFontInfo(font)
    p.setFont(font)
    p.setPen(QColor("#FFFFFF"))
    p.drawText(img.rect(), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), "    " + sentence)
    p.end()
    lum, a = _gray(img)
    stem, ink, bbox = _stem_and_ink(lum, a)
    print(
        "PROBE", name,
        "family=", info.family(),
        "styleName=", info.styleName(),
        "weight=", info.weight(),
        "exactMatch=", info.exactMatch(),
        "resolve=", rec,
        "stem=", stem,
        "ink=", ink,
        "bbox=", bbox,
        flush=True,
    )
    _save(img, "T04f-6b2_gate_weight_%s.png" % name)
    return {
        "name": name, "family": info.family(), "styleName": info.styleName(),
        "weight": int(info.weight()), "exactMatch": bool(info.exactMatch()),
        "stem": stem, "ink": ink, "bbox": bbox, "resolve": rec,
    }


def _frame_bgr(path, n):
    import cv2
    cap = cv2.VideoCapture(path)
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(n))
    ok, fr = cap.read()
    cap.release()
    if not ok or fr is None:
        raise RuntimeError("frame %s #%d" % (path, n))
    return fr


def _bgr_to_qimage(fr):
    from PySide6.QtGui import QImage
    import numpy as np
    rgb = fr[:, :, ::-1].copy()
    h, w, _ = rgb.shape
    return QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


def _white_bbox(fr, thresh=200, band="lower"):
    import numpy as np
    b, g, r = fr[:, :, 0], fr[:, :, 1], fr[:, :, 2]
    lum = 0.2126 * r.astype(np.float32) + 0.7152 * g.astype(np.float32) + 0.0722 * b.astype(np.float32)
    h, w = lum.shape
    ink = lum > thresh
    if band == "lower":
        ycut = int(h * 0.58) if h > w else int(h * 0.55)
        ink = ink.copy()
        ink[:ycut, :] = False
    ys, xs = np.where(ink)
    if len(ys) == 0:
        return None, 0, lum, ink
    return (int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)), int(ink.sum()), lum, ink


def _side_by_side(old, new, name, frame_n=30):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QFont
    import cv2
    import numpy as np

    a = _frame_bgr(old, frame_n)
    b = _frame_bgr(new, frame_n)
    ha, wa = a.shape[:2]
    hb, wb = b.shape[:2]
    h = min(ha, hb)
    # scale to same height if needed
    if ha != hb:
        scale = h / ha
        a = cv2.resize(a, (int(wa * scale), h), interpolation=cv2.INTER_AREA)
        scale = h / hb
        b = cv2.resize(b, (int(wb * scale), h), interpolation=cv2.INTER_AREA)
    gap = 16
    # Prefer absdiff in the lower band so rainbow stripes don't inflate the box.
    import numpy as np
    d = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2)
    h = a.shape[0]
    w = a.shape[1]
    ycut = int(h * 0.58) if h > w else int(h * 0.50)
    dmask = d > 18
    dmask[:ycut, :] = False
    ys, xs = np.where(dmask)
    if len(ys):
        box_d = (int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1))
        box_a = box_d
        box_b = box_d
    else:
        box_a, ink_a, lum_a, inkmask_a = _white_bbox(a)
        box_b, ink_b, lum_b, inkmask_b = _white_bbox(b)
    _, ink_a, _, _ = _white_bbox(a)
    _, ink_b, _, _ = _white_bbox(b)

    def crop4x(fr, box, max_w):
        if not box:
            return None
        x, y, w, h = box
        pad = 12
        x0 = max(0, x - pad)
        y0 = max(0, y - pad)
        x1 = min(fr.shape[1], x + w + pad)
        y1 = min(fr.shape[0], y + h + pad)
        crop = fr[y0:y1, x0:x1]
        tw = min(max_w, crop.shape[1] * 4)
        th = int(round(crop.shape[0] * (tw / max(1, crop.shape[1]))))
        return cv2.resize(crop, (tw, th), interpolation=cv2.INTER_NEAREST)

    col_w = a.shape[1]
    ca = crop4x(a, box_a, col_w)
    cb = crop4x(b, box_b, b.shape[1])
    ch = 0
    if ca is not None:
        ch = max(ch, ca.shape[0])
    if cb is not None:
        ch = max(ch, cb.shape[0])
    top_h = a.shape[0]
    canvas_w = a.shape[1] + gap + b.shape[1]
    canvas_h = top_h + 24 + (ch if ch else 0) + 40
    out = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
    out[:] = (18, 22, 28)
    out[0:a.shape[0], 0:a.shape[1]] = a
    out[0:b.shape[0], a.shape[1] + gap:a.shape[1] + gap + b.shape[1]] = b
    y = top_h + 24
    if ca is not None:
        hh, ww = ca.shape[:2]
        out[y:y + hh, 0:ww] = ca
    if cb is not None:
        xoff = a.shape[1] + gap
        hh, ww = cb.shape[:2]
        out[y:y + hh, xoff:xoff + ww] = cb
    q = _bgr_to_qimage(out)
    p = QPainter(q)
    p.setPen(QColor("#F5D77A"))
    p.setFont(QFont("Arimo", 14))
    p.drawText(12, 22, "OLD  " + name)
    p.drawText(a.shape[1] + gap + 12, 22, "NEW  " + name)
    p.end()
    _save(q, "T04f-6b2_gate_%s.png" % name)
    return {"old_white_bbox": box_a, "new_white_bbox": box_b, "old_ink": ink_a, "new_ink": ink_b}


def _ffprobe(path):
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,width,height,nb_frames,duration,avg_frame_rate",
         "-show_entries", "format=duration",
         "-of", "json", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    data = json.loads(r.stdout or "{}")
    st = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}
    return {
        "codec": st.get("codec_name"),
        "width": st.get("width"),
        "height": st.get("height"),
        "nb_frames": st.get("nb_frames"),
        "stream_duration": st.get("duration"),
        "format_duration": fmt.get("duration"),
        "avg_frame_rate": st.get("avg_frame_rate"),
    }


def _psnr(old, new):
    import cv2
    import numpy as np
    ca = cv2.VideoCapture(old)
    cb = cv2.VideoCapture(new)
    rows = []
    n = 0
    while True:
        oa, fa = ca.read()
        ob, fb = cb.read()
        if not oa or not ob:
            break
        a = fa.astype(np.float64)
        b = fb.astype(np.float64)
        mse = np.mean((a - b) ** 2)
        p = 99.0 if mse < 1e-12 else float(10.0 * np.log10(255.0 * 255.0 / mse))
        rows.append((n, p))
        n += 1
    ca.release()
    cb.release()
    if not rows:
        return {"n": 0, "diff_frames": 0, "min_psnr": None, "worst_n": None, "mean_psnr": None}
    diff = [(i, p) for i, p in rows if p < 60]
    worst = min(rows, key=lambda t: t[1])
    mean = sum(p for _, p in rows) / len(rows)
    return {
        "n": len(rows),
        "diff_frames": len(diff),
        "min_psnr": worst[1],
        "worst_n": worst[0],
        "mean_psnr": mean,
    }


def _diff_bbox(old, new, n, thresh=12):
    import numpy as np
    a = _frame_bgr(old, n).astype(np.int16)
    b = _frame_bgr(new, n).astype(np.int16)
    d = np.abs(a - b).max(axis=2)
    mask = d > thresh
    ys, xs = np.where(mask)
    if len(ys) == 0:
        return None, 0, a.shape
    bbox = (int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1))
    return bbox, int(mask.sum()), a.shape


def _g09_lines():
    path = os.path.join(GOLDEN, "g09_linebreak_latin", "project.asproj.json")
    data = json.loads(open(path, encoding="utf-8").read())
    task = data["tasks"][0]
    stored = []
    for i, seg in enumerate(task.get("segments") or []):
        stored.append({
            "i": i,
            "text": seg.get("text"),
            "start": seg.get("start"),
            "end": seg.get("end"),
            "lines": list(seg.get("lines") or []),
        })
    from cutora.subtitle.layout import break_lines, _rules_from_task, _pixel_cap_lines, LayoutRules
    rules = _rules_from_task(task)
    fresh = []
    for i, seg in enumerate(task.get("segments") or []):
        local = LayoutRules(
            max_chars_per_line=rules.max_chars_per_line,
            max_lines=rules.max_lines,
            balance_lines=rules.balance_lines,
            lang=seg.get("lang") or rules.lang,
            line_break=rules.line_break,
        )
        lines = break_lines(seg, local)
        capped = _pixel_cap_lines(seg, lines, task, local)
        fresh.append({"i": i, "lines": list(capped)})
    return stored, fresh, task


def _visual_rows(path, frame_n, thresh=180):
    import numpy as np
    fr = _frame_bgr(path, frame_n)
    box, ink, lum, inkmask = _white_bbox(fr, thresh)
    if box is None:
        return 0
    x, y, w, h = box
    band = inkmask[y:y + h, x:x + w]
    proj = band.mean(axis=1)
    on = proj > 0.02
    rows = 0
    prev = False
    for v in on.tolist():
        if v and not prev:
            rows += 1
        prev = v
    return rows


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_gate_"))
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from PySide6.QtWidgets import QApplication
    from cutora.ui.panels.fonts import load_embedded_fonts, get_font_manager, resolve_font_request
    from PySide6.QtGui import QFontInfo

    app = QApplication.instance() or QApplication(["t04f6b2-gate"])
    load_embedded_fonts()
    _ = app

    rec = resolve_font_request("SN Pro SemiBold")
    font = get_font_manager().get_font("SN Pro SemiBold", PT, bold=True)
    info = QFontInfo(font)
    print("RESOLVE", rec, "QFontInfo", info.family(), info.styleName(), info.weight(), info.exactMatch(), flush=True)

    probes = []
    for style in ("Regular", "SemiBold", "Bold"):
        probes.append(_probe(style.lower(), style, SENTENCE, PT))
    probes.append(_probe("resolved", "SN Pro SemiBold", SENTENCE, PT))
    # Four-up contact: Regular | SemiBold | Bold | resolved (2.2)
    from PySide6.QtGui import QImage, QPainter, QColor, QFont
    from PySide6.QtCore import Qt
    tiles = []
    for nm in ("regular", "semibold", "bold", "resolved"):
        tiles.append(QImage(os.path.join(IMG, "T04f-6b2_gate_weight_%s.png" % nm)))
    tw, th = tiles[0].width() // 2, tiles[0].height()
    contact = QImage(tw * 2 + 8, th * 2 + 36, QImage.Format.Format_ARGB32_Premultiplied)
    contact.fill(QColor("#0B1A33"))
    pc = QPainter(contact)
    labels = ["Regular", "SemiBold", "Bold", "resolved (2.2)"]
    for i, (im, lab) in enumerate(zip(tiles, labels)):
        x, y = (i % 2) * (tw + 8), (i // 2) * (th + 18)
        pc.drawImage(x, y + 16, im.scaled(tw, th, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation))
        pc.setPen(QColor("#F5D77A"))
        pc.setFont(QFont("Arimo", 11))
        pc.drawText(x + 8, y + 14, lab)
    pc.end()
    _save(contact, "T04f-6b2_gate_weight_contact.png")

    # Golden crop of first-line frame (t=1.0s, frame 30) as 4th visual.
    got_fr = _frame_bgr(GOT["g01_vertical_basic"], 30)
    box, ink, lum, inkmask = _white_bbox(got_fr, thresh=200, band="lower")
    if box:
        x, y, w, h = box
        lum_c = lum[y:y + h, x:x + w]
        stem_g, ink_g, bbox_g = _stem_and_ink(lum_c)
    else:
        stem_g, ink_g, bbox_g = 0.0, 0, (0, 0, 0, 0)
    print("GOLDEN_CROP stem", stem_g, "ink", ink_g, "bbox", bbox_g, "white_bbox", box, flush=True)
    from PySide6.QtGui import QImage
    import cv2
    if box:
        x, y, w, h = box
        pad = 16
        x0, y0 = max(0, x - pad), max(0, y - pad)
        crop = got_fr[y0:y + h + pad, x0:x + w + pad]
        q = _bgr_to_qimage(crop)
        _save(q, "T04f-6b2_gate_weight_golden_crop.png")

    sb = next(p for p in probes if p["name"] == "semibold")
    rg = next(p for p in probes if p["name"] == "regular")
    bd = next(p for p in probes if p["name"] == "bold")
    rs = next(p for p in probes if p["name"] == "resolved")
    d_sb_rg = abs(sb["stem"] - rg["stem"])
    d_rs_sb = abs(rs["stem"] - sb["stem"])
    d_rs_rg = abs(rs["stem"] - rg["stem"])
    ink_sb_rg = abs(sb["ink"] - rg["ink"])
    ink_rs_sb = abs(rs["ink"] - sb["ink"])
    ink_rs_rg = abs(rs["ink"] - rg["ink"])
    weight_ok = (
        sb["styleName"].lower() == "semibold"
        and rs["styleName"].lower() == "semibold"
        and rs["family"] == "SN Pro"
        and d_sb_rg >= 1.0
        and d_rs_sb <= d_rs_rg
        and ink_rs_sb <= ink_rs_rg
        and sb["ink"] > rg["ink"]
        and bd["ink"] >= sb["ink"]
    )
    print("WEIGHT_OK", weight_ok,
          "stem_reg", rg["stem"], "stem_sb", sb["stem"], "stem_bold", bd["stem"],
          "stem_resolved", rs["stem"],
          "ink_reg", rg["ink"], "ink_sb", sb["ink"], "ink_bold", bd["ink"],
          "ink_resolved", rs["ink"],
          flush=True)

    # §2 stills
    stills = {}
    for case, frame in (
        ("g01_vertical_basic", 30),
        ("g02_vertical_fx", 30),
        ("g03_horizontal_it", 30),
        ("g04_stitch", 15),
        ("g05_scatter_insert", 30),
        ("g06_parallel", 30),
        ("g07_cli", 30),
        ("g09_linebreak_latin", 30),
    ):
        stills[case] = _side_by_side(EXP[case], GOT[case], case, frame)

    extra = {}
    for case in ("g01_vertical_basic", "g09_linebreak_latin"):
        old, new = EXP[case], GOT[case]
        psnr = _psnr(old, new)
        worst = psnr.get("worst_n") or 0
        db, ndiff, shape = _diff_bbox(old, new, worst)
        h, w = shape[:2]
        sub_y0 = int(h * 0.55)
        outside = False
        if db:
            x, y, bw, bh = db
            # allow a little margin; subtitle sits in lower half for vertical
            if case.startswith("g03"):
                outside = y < int(h * 0.35)
            else:
                outside = y + bh < sub_y0 and y < int(h * 0.4)
        extra[case] = {
            "ffprobe_old": _ffprobe(old),
            "ffprobe_new": _ffprobe(new),
            "psnr": psnr,
            "diff_bbox_worst": db,
            "diff_pixels": ndiff,
            "frame_shape": [w, h],
            "white_bbox_old": stills[case]["old_white_bbox"],
            "white_bbox_new": stills[case]["new_white_bbox"],
            "outside_subtitle": outside,
        }
        print("EXTRA", case, json.dumps(extra[case], default=str), flush=True)

    stored, fresh, task = _g09_lines()
    g09_rows = []
    changed = False
    for s, f in zip(stored, fresh):
        mid = 0.5 * (float(s["start"]) + float(s["end"]))
        fn = int(round(mid * 30.0))
        vis_old = _visual_rows(EXP["g09_linebreak_latin"], fn)
        vis_new = _visual_rows(GOT["g09_linebreak_latin"], fn)
        same = list(s["lines"]) == list(f["lines"])
        if not same:
            changed = True
        g09_rows.append({
            "i": s["i"],
            "text": s["text"],
            "stored_n": len(s["lines"]),
            "stored": s["lines"],
            "fresh_n": len(f["lines"]),
            "fresh": f["lines"],
            "same_text": same,
            "visual_old": vis_old,
            "visual_new": vis_new,
            "frame": fn,
        })
        print("G09_SEG", s["i"], "stored", s["lines"], "fresh", f["lines"],
              "vis", vis_old, vis_new, "same", same, flush=True)

    report = {
        "weight_ok": weight_ok,
        "probes": probes,
        "golden_crop_stem": stem_g,
        "golden_crop_ink": ink_g,
        "stills": stills,
        "extra": extra,
        "g09": g09_rows,
        "g09_linebreak_changed": changed,
        "outside": {k: v.get("outside_subtitle") for k, v in extra.items()},
    }
    outp = os.path.join(REP, "T04f-6b2_rerecord_gate.json")
    with open(outp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("wrote", outp, flush=True)
    print("GATE_WEIGHT_OK", weight_ok, "GATE_G09_CHANGED", changed,
          "GATE_OUTSIDE", report["outside"], flush=True)
    return 0 if weight_ok and not changed else 2


if __name__ == "__main__":
    raise SystemExit(main())
