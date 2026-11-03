# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""性能基准与视觉回归采样脚本（40 款新短视频转场）。

功能：
1. 在 1920×1080 与 960×540 分辨率下测量每款新转场的单帧渲染耗时（median / p95 / max）。
2. 在 t = 0.25, 0.50, 0.75 三个进度点采样渲染对比条带图与分类总览图，输出到指定目录。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QApplication

from cutora.render.transition_catalog import category_of, meta_of, recommended_duration, recommended_sfx
from cutora.render.transitions import apply_transition
from cutora.render.transitions_social import SOCIAL_TRANSITION_LIBRARY


def _ensure_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def make_test_frame_a(w: int, h: int) -> QImage:
    """生成具有丰富色彩、几何结构与文字标识的测试画面 A（暖橙日落街景风格）。"""
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        bg = QLinearGradient(0, 0, w, h)
        bg.setColorAt(0.0, QColor(245, 95, 55))
        bg.setColorAt(0.5, QColor(255, 165, 65))
        bg.setColorAt(1.0, QColor(95, 35, 85))
        p.fillRect(0, 0, w, h, bg)

        # 太阳与同心圆
        sun = QRadialGradient(w * 0.32, h * 0.36, min(w, h) * 0.28)
        sun.setColorAt(0.0, QColor(255, 250, 200, 240))
        sun.setColorAt(0.6, QColor(255, 190, 80, 160))
        sun.setColorAt(1.0, QColor(255, 120, 40, 0))
        p.fillRect(0, 0, w, h, sun)

        # 城市剪影天际线与网格参考线
        p.setPen(QPen(QColor(255, 255, 255, 55), max(1.0, w * 0.002)))
        for gx in range(1, 6):
            x = int(w * gx / 6.0)
            p.drawLine(x, 0, x, h)
        for gy in range(1, 4):
            y = int(h * gy / 4.0)
            p.drawLine(0, y, w, y)

        p.setBrush(QColor(32, 18, 44, 220))
        p.setPen(Qt.PenStyle.NoPen)
        heights = [0.28, 0.42, 0.35, 0.52, 0.38, 0.46, 0.31, 0.40]
        bw = w / float(len(heights))
        for i, ratio in enumerate(heights):
            bh = h * ratio
            p.drawRect(QRectF(i * bw + 2, h - bh, bw - 4, bh))

        # 醒目 A 标识
        f = QFont("Segoe UI", max(12, int(h * 0.14)), QFont.Weight.Bold)
        p.setFont(f)
        p.setPen(QColor(255, 248, 225, 235))
        p.drawText(QRectF(w * 0.06, h * 0.08, w * 0.4, h * 0.25), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "SHOT A")
    finally:
        p.end()
    return img


def make_test_frame_b(w: int, h: int) -> QImage:
    """生成具有冷青赛博霓虹风格与几何特征的测试画面 B。"""
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        bg = QLinearGradient(0, 0, w, h)
        bg.setColorAt(0.0, QColor(12, 38, 95))
        bg.setColorAt(0.55, QColor(18, 135, 175))
        bg.setColorAt(1.0, QColor(25, 220, 185))
        p.fillRect(0, 0, w, h, bg)

        # 霓虹几何环与斜向光带
        p.setPen(QPen(QColor(120, 255, 240, 170), max(2.0, min(w, h) * 0.018)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(w * 0.65, h * 0.46), min(w, h) * 0.26, min(w, h) * 0.26)
        p.setPen(QPen(QColor(255, 80, 210, 160), max(2.0, min(w, h) * 0.012)))
        p.drawEllipse(QPointF(w * 0.65, h * 0.46), min(w, h) * 0.16, min(w, h) * 0.16)

        # 透视地面网格
        p.setPen(QPen(QColor(210, 255, 255, 75), max(1.0, w * 0.002)))
        horizon = int(h * 0.62)
        p.drawLine(0, horizon, w, horizon)
        for i in range(-6, 7):
            p.drawLine(int(w * 0.5 + i * w * 0.04), horizon, int(w * 0.5 + i * w * 0.18), h)

        # 醒目 B 标识
        f = QFont("Segoe UI", max(12, int(h * 0.14)), QFont.Weight.Bold)
        p.setFont(f)
        p.setPen(QColor(225, 255, 250, 240))
        p.drawText(QRectF(w * 0.50, h * 0.08, w * 0.44, h * 0.25), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "SHOT B")
    finally:
        p.end()
    return img


def benchmark_presets(presets: list[tuple[str, str]], rounds: int = 7) -> dict:
    a_1080 = make_test_frame_a(1920, 1080)
    b_1080 = make_test_frame_b(1920, 1080)
    a_540 = make_test_frame_a(960, 540)
    b_540 = make_test_frame_b(960, 540)

    results = {}
    sample_ts = [0.2, 0.35, 0.5, 0.65, 0.8]

    for label, preset in presets:
        # 预热一次
        apply_transition(preset, a_540, b_540, 0.5, 960, 540)
        apply_transition(preset, a_1080, b_1080, 0.5, 1920, 1080)

        times_540 = []
        for r in range(rounds):
            t_val = sample_ts[r % len(sample_ts)]
            t0 = time.perf_counter()
            apply_transition(preset, a_540, b_540, t_val, 960, 540)
            times_540.append((time.perf_counter() - t0) * 1000.0)

        times_1080 = []
        for r in range(rounds):
            t_val = sample_ts[r % len(sample_ts)]
            t0 = time.perf_counter()
            apply_transition(preset, a_1080, b_1080, t_val, 1920, 1080)
            times_1080.append((time.perf_counter() - t0) * 1000.0)

        times_540_sorted = sorted(times_540)
        times_1080_sorted = sorted(times_1080)
        p95_idx = min(len(times_1080_sorted) - 1, int(len(times_1080_sorted) * 0.95))

        results[preset] = {
            "label": label,
            "category": category_of(preset),
            "dur": recommended_duration(preset),
            "sfx": recommended_sfx(preset),
            "hot": bool(meta_of(preset).get("hot")),
            "ms_540p_median": round(statistics.median(times_540_sorted), 2),
            "ms_540p_p95": round(times_540_sorted[p95_idx], 2),
            "ms_1080p_median": round(statistics.median(times_1080_sorted), 2),
            "ms_1080p_p95": round(times_1080_sorted[p95_idx], 2),
            "ms_1080p_max": round(max(times_1080_sorted), 2),
        }
    return results


def render_contact_sheets(presets: list[tuple[str, str]], out_dir: str) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    tw, th = 320, 180
    a = make_test_frame_a(tw, th)
    b = make_test_frame_b(tw, th)

    by_cat: dict[str, list[tuple[str, str]]] = {}
    for label, preset in presets:
        cat = category_of(preset)
        by_cat.setdefault(cat, []).append((label, preset))

    saved_files = []
    ts = (0.25, 0.50, 0.75)

    for cat, items in by_cat.items():
        pad = 14
        label_w = 230
        header_h = 46
        row_h = th + pad
        sheet_w = label_w + len(ts) * (tw + pad) + pad
        sheet_h = header_h + len(items) * row_h + pad

        sheet = QImage(sheet_w, sheet_h, QImage.Format.Format_ARGB32_Premultiplied)
        sheet.fill(QColor(18, 20, 26))
        p = QPainter(sheet)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            f_title = QFont("Microsoft YaHei UI", 12, QFont.Weight.Bold)
            f_row = QFont("Microsoft YaHei UI", 10, QFont.Weight.Bold)
            f_sub = QFont("Consolas", 9)

            p.setFont(f_title)
            p.setPen(QColor(235, 240, 250))
            p.drawText(QRectF(pad, 8, label_w - pad, header_h - 8), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"Category: {cat} ({len(items)})")
            for ti, t_val in enumerate(ts):
                x0 = label_w + ti * (tw + pad)
                p.drawText(QRectF(x0, 8, tw, header_h - 8), Qt.AlignmentFlag.AlignCenter, f"t = {t_val:.2f}")

            for ri, (label, preset) in enumerate(items):
                y0 = header_h + ri * row_h
                p.fillRect(QRectF(8, y0, sheet_w - 16, th + 4), QColor(28, 31, 40))
                p.setFont(f_row)
                p.setPen(QColor(255, 215, 110) if meta_of(preset).get("hot") else QColor(230, 235, 245))
                hot_tag = " [HOT]" if meta_of(preset).get("hot") else ""
                p.drawText(QRectF(pad, y0 + 18, label_w - pad * 1.5, 26), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"{label}{hot_tag}")

                p.setFont(f_sub)
                p.setPen(QColor(160, 175, 195))
                p.drawText(QRectF(pad, y0 + 48, label_w - pad * 1.5, 22), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, preset)
                p.drawText(
                    QRectF(pad, y0 + 72, label_w - pad * 1.5, 22),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    f"dur={recommended_duration(preset):.2f}s | sfx={recommended_sfx(preset) or '-'}",
                )

                for ti, t_val in enumerate(ts):
                    x0 = label_w + ti * (tw + pad)
                    frame = apply_transition(preset, a, b, t_val, tw, th)
                    if frame is not None:
                        p.drawImage(QRectF(x0, y0 + 2, tw, th), frame)
                    p.setPen(QPen(QColor(70, 80, 98), 1))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRect(QRectF(x0, y0 + 2, tw, th))
        finally:
            p.end()

        out_path = os.path.join(out_dir, f"sheet_{cat}.png")
        sheet.save(out_path)
        saved_files.append(out_path)

    return saved_files


def main():
    parser = argparse.ArgumentParser(description="Benchmark & render social transitions.")
    parser.add_argument(
        "--out-dir",
        default=r"C:\Users\Timco\.gemini\antigravity\brain\ef45af19-a792-4dfb-91f8-d7495d8e8127\transition_checks",
        help="Directory to write contact sheets and benchmark JSON.",
    )
    parser.add_argument("--rounds", type=int, default=7, help="Benchmark iterations per resolution.")
    args = parser.parse_args()

    _ensure_app()
    presets = list(SOCIAL_TRANSITION_LIBRARY)
    print(f"Benchmarking {len(presets)} social transitions (rounds={args.rounds})...")
    bench = benchmark_presets(presets, rounds=args.rounds)
    sheets = render_contact_sheets(presets, args.out_dir)

    report_path = os.path.join(args.out_dir, "benchmark_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(bench, f, ensure_ascii=False, indent=2)

    med_1080 = [v["ms_1080p_median"] for v in bench.values()]
    med_540 = [v["ms_540p_median"] for v in bench.values()]
    print(f"540p median range : {min(med_540):.2f} ms ~ {max(med_540):.2f} ms (avg {statistics.mean(med_540):.2f} ms)")
    print(f"1080p median range: {min(med_1080):.2f} ms ~ {max(med_1080):.2f} ms (avg {statistics.mean(med_1080):.2f} ms)")
    for k, v in sorted(bench.items(), key=lambda kv: kv[1]["ms_1080p_median"], reverse=True)[:8]:
        print(f"  {k:24s} ({v['label']}): 540p={v['ms_540p_median']:.2f}ms, 1080p={v['ms_1080p_median']:.2f}ms (p95={v['ms_1080p_p95']:.2f}ms)")
    print("Saved sheets:", sheets)
    print("Saved report:", report_path)


if __name__ == "__main__":
    main()
