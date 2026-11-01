# -*- coding: utf-8 -*-
"""片语工作室 (Orante & CUTORA) — 官方多语种词级对齐模型全自动检测、下载与校验脚本。

功能特性：
1. 支持按文案内容自动识别语种 (`--text "..."`) 或直接指定语种代码 (`--lang fr es pt it de ...`)。
2. 自动检测 `runtime/models/` 下官方对齐模型是否完整并通过 SHA256 严格校验。
3. 若缺少指定语种模型，全自动从 Hugging Face 官方仓库断点续传下载全部权重与配置文件，完成后自动校验 SHA256。
4. 可被 CUTORA / Orante 后台静默调用，也可由用户直接命令行运行。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

ROOT = Path(__file__).resolve().parent
CUTORA_ROOT = ROOT / "modules" / "cutora_editor"
if str(CUTORA_ROOT) not in sys.path:
    sys.path.insert(0, str(CUTORA_ROOT))

from cutora.asr.download import download_model  # noqa: E402
from cutora.asr.models import (  # noqa: E402
    align_row_for_language,
    catalog,
    get_model,
    models_dir,
    verify_model,
)
from cutora.asr.stitch_language import detect_language_from_text  # noqa: E402


def _fmt_mb(num_bytes: float) -> str:
    mb = float(num_bytes or 0) / (1024.0 * 1024.0)
    if mb >= 100:
        return f"{mb:.0f} MB"
    if mb >= 10:
        return f"{mb:.1f} MB"
    return f"{mb:.2f} MB"


def list_align_models(fast: bool = True) -> list[dict]:
    rows = [r for r in catalog() if r.get("kind") == "align"]
    out = []
    for r in rows:
        st = verify_model(r, fast=fast)
        out.append({
            "id": r.get("id"),
            "language": r.get("language") or "en",
            "name": r.get("name") or r.get("id"),
            "repo": r.get("repo") or "(内置 torchaudio)",
            "size_bytes": int(r.get("size_bytes") or 0),
            "status": st,
            "bundled": bool(r.get("bundled")),
            "enabled": bool(r.get("enabled", True)),
        })
    return out


def ensure_language_align_model(lang_code: str, quiet: bool = False) -> dict:
    """确保指定语种 (如 'fr', 'es', 'pt', 'it', 'de', 'en' 等) 的官方对齐模型已就绪并校验通过。"""
    code = (lang_code or "").strip().lower()
    if not code:
        return {"ok": False, "error": "未指定语言代码"}
    if code == "en":
        row = get_model("align-en")
    else:
        row = align_row_for_language(code) or get_model(f"align-{code}")
    if row is None:
        return {
            "ok": True,
            "language": code,
            "mode": "native",
            "message": f"语种 {code} 使用 Whisper 内置多语种词级时间戳对齐，无需额外下载外部模型。",
        }
    model_id = str(row["id"])
    st = verify_model(row, fast=False)
    if st == "ready":
        if not quiet:
            print(f"[✓] {model_id} ({code}) 官方模型已就绪并通过 SHA256 校验 -> {models_dir()}")
        return {"ok": True, "language": code, "model_id": model_id, "status": "ready", "downloaded": False}

    if not quiet:
        print(f"[↓] 检测到 {model_id} ({code}) 状态为 {st}，开始从 Hugging Face 官方仓库下载 ({_fmt_mb(row.get('size_bytes', 0))})...")
        print(f"    官方源: https://huggingface.co/{row.get('repo')}")

    t0 = time.perf_counter()
    last_print = [0.0]

    def _progress(done: int, total: int, fname: str):
        if quiet:
            return
        now = time.perf_counter()
        if now - last_print[0] < 0.5 and done < total:
            return
        last_print[0] = now
        pct = (done * 100.0 / total) if total > 0 else 0.0
        spd = done / max(0.001, now - t0)
        sys.stdout.write(
            f"\r    进度: {pct:5.1f}% ({_fmt_mb(done)} / {_fmt_mb(total)}) · {_fmt_mb(spd)}/s · {fname}        "
        )
        sys.stdout.flush()

    last_err = None
    for attempt in range(1, 4):
        try:
            info = download_model(model_id, progress_cb=_progress)
            if not quiet:
                print()
            st_after = verify_model(row, fast=False)
            if st_after == "ready":
                if not quiet:
                    print(f"[✓] {model_id} ({code}) 下载并校验成功！保存在: {info.get('dir')}")
                return {"ok": True, "language": code, "model_id": model_id, "status": "ready", "downloaded": True, "dir": info.get("dir")}
            last_err = f"下载后校验状态为 {st_after}"
        except Exception as exc:
            if not quiet:
                print(f"\n[!] 第 {attempt}/3 次下载尝试遇到异常: {exc}")
            last_err = str(exc)
            time.sleep(1.5 * attempt)

    return {"ok": False, "language": code, "model_id": model_id, "error": last_err or "下载失败"}


def main() -> int:
    parser = argparse.ArgumentParser(description="片语工作室 — 官方多语种词级对齐模型全自动下载与校验工具")
    parser.add_argument("--list", action="store_true", help="列出所有支持的官方对齐模型及本地状态")
    parser.add_argument("--verify", action="store_true", help="配合 --list 使用，执行严格 SHA256 校验")
    parser.add_argument("--lang", nargs="*", help="要确保就绪/自动下载的语种代码（如 fr es pt it de nl ru ar pl tr vi）")
    parser.add_argument("--text", type=str, default="", help="传入一段文案，自动判定文案语种并确保对应官方对齐模型就绪")
    parser.add_argument("--all-common", action="store_true", help="一键检查并补齐主力语种官方对齐模型 (en, fr, es, pt, it, de)")
    args = parser.parse_args()

    print(f"📦 模型存储根目录: {models_dir()}")

    if args.list:
        items = list_align_models(fast=not args.verify)
        print(f"{'模型ID':<12} {'语种':<6} {'状态':<16} {'体积':<10} {'官方仓库'}")
        print("-" * 82)
        for it in items:
            print(f"{it['id']:<12} {it['language']:<6} {it['status']:<16} {_fmt_mb(it['size_bytes']):<10} {it['repo']}")
        return 0

    langs: list[str] = []
    if args.text:
        det_lang, prob = detect_language_from_text(args.text)
        print(f"📖 从文案自动判定语种: {det_lang} (置信度 {prob * 100:.0f}%)")
        if det_lang:
            langs.append(det_lang)
    if args.lang:
        langs.extend(args.lang)
    if args.all_common:
        for c in ("en", "fr", "es", "pt", "it", "de"):
            if c not in langs:
                langs.append(c)

    if not langs:
        # 默认检查主力语种
        langs = ["en", "fr", "es", "pt", "it", "de"]

    all_ok = True
    for code in langs:
        res = ensure_language_align_model(code)
        if not res.get("ok"):
            all_ok = False
            print(f"[✗] 语种 {code} 模型准备失败: {res.get('error')}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
