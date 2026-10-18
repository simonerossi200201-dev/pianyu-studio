# -*- coding: utf-8 -*-
"""试一次 Grok 生图：连账号 → 传参考图 → 生 1 张 → 落盘。

    python tools\test_grok_image.py [账号名] [参考图路径] [张数]
"""
from __future__ import annotations
import io, os, sqlite3, sys
if "pytest" not in sys.modules:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orante import db, paths, accounts          # noqa: E402
from orante.engines.base import make_engine, BlockedImage   # noqa: E402
from orante.errors import OranteError                        # noqa: E402

PROMPT = ("保持上传图像中的人物形象服装和动作都不变，背景场景也保持不变，"
          "只更换拍摄距离为脸部和肩膀的近景，拍摄角度更改为略微仰拍的拍摄角度，"
          "更换人物的朝向为朝右并偏向侧面（四分之三侧面，脸部清楚可见），"
          "最终成品图片必须保持画面有且只有一个完整的高清真实色彩漂亮清晰好看真实的"
          "获奖级别的画质的整幅摄影图片。")


def find_image():
    for r in db.rows("SELECT dir FROM tasks WHERE dir IS NOT NULL AND dir<>'' ORDER BY id DESC LIMIT 8"):
        d = os.path.join(r["dir"], "images")
        if os.path.isdir(d):
            for n in sorted(os.listdir(d)):
                if n.lower().endswith((".jpg", ".jpeg", ".png")) and not n.startswith("_"):
                    return os.path.join(d, n)
    return ""


def main():
    db.init()
    want = sys.argv[1] if len(sys.argv) > 1 else ""
    img = (sys.argv[2] if len(sys.argv) > 2 and sys.argv[2].strip() else "") or find_image()
    n = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].strip() else 2
    accs = [a for a in accounts.list_all() if a["engine"] == "grok" and a["enabled"]]
    acc = None
    for a in accs:
        if not want or want.lower() in (a["id"].lower(), a["name"].lower()):
            acc = a
            break
    if acc is None:
        raise SystemExit(f"没有可用的 grok 账号（要 {want!r}）")
    print(f"账号 {acc['name']}（{acc['id']}，端口 {acc['port']}）")
    print(f"参考图 {img}")
    eng = make_engine(acc, lambda m: print(m))
    eng.connect()
    dest = paths.DATA / "tmp" / "grok_test"
    try:
        out = eng.gen_images(PROMPT, ref=img, n=n, dest_dir=dest)
    except BlockedImage as e:
        print("被拒：", e); out = []
    except OranteError as e:
        print("出错：", e.message); print("详情：", e.hint); out = []
    finally:
        eng.close()
    print(f"\n结果 {len(out)} 张：")
    for p in out:
        print("   ", p, os.path.getsize(p), "字节")
    print("✓ 完成" if out else "✗ 一张都没出来")


if __name__ == "__main__":
    main()
