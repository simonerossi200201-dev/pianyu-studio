# -*- coding: utf-8 -*-
"""把 ui/assets/icon.svg（及小尺寸简化版）渲成全套 PNG / ICO / ICNS / 预览图。

渲染走 Playwright 的 Chromium：优先用当前解释器，没有 playwright
就找旁边的 .venv；分发版用 Orante 自己的 .venv。
Chromium 二进制没有就试系统 Chrome，都不行再 playwright install chromium。
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "ui" / "assets"
PNG_DIR = ASSETS / "png"
ICO_SIZES = (256, 128, 64, 48, 32, 16)
PNG_SIZES = (1024, 512, 256, 128, 64, 48, 32, 24, 16)
# icns 块：类型 → PNG 边长（块内容就是 PNG 字节）
ICNS_CHUNKS = (
    ("ic07", 128),
    ("ic08", 256),
    ("ic09", 512),
    ("ic10", 1024),
    ("ic11", 32),
    ("ic12", 64),
    ("ic13", 256),
    ("ic14", 512),
)


def _sibling_venvs() -> list[Path]:
    """开发机上的流水线 venv，以及本仓库 .venv（分发版）。"""
    out: list[Path] = []
    parent = ROOT.parent
    try:
        for d in parent.iterdir():
            for rel in (("Scripts", "python.exe"), ("bin", "python")):
                py = d / ".venv" / rel[0] / rel[1]
                if py.is_file():
                    out.append(py)
    except OSError:
        pass
    for rel in (("Scripts", "python.exe"), ("bin", "python")):
        py = ROOT / ".venv" / rel[0] / rel[1]
        if py.is_file():
            out.append(py)
    return out


def _ensure_playwright():
    try:
        from playwright.sync_api import sync_playwright  # noqa: WPS433
        return sync_playwright
    except ImportError:
        pass
    here = Path(sys.executable).resolve()
    for py in _sibling_venvs():
        if py.resolve() == here:
            continue
        os.execv(str(py), [str(py), str(Path(__file__).resolve()), *sys.argv[1:]])
    sys.exit("需要 playwright：请在本 venv 里 pip install playwright")


def _launch_chromium(p):
    """Playwright 的 Chromium；没有二进制就用系统 Chrome，再不行才下载。"""
    last = None
    try:
        return p.chromium.launch()
    except Exception as e:  # noqa: BLE001
        last = e
    # 系统 Chrome（Playwright 的 chromium 通道）
    try:
        return p.chromium.launch(channel="chrome")
    except Exception as e:  # noqa: BLE001
        last = e
    pf = os.environ.get("ProgramFiles") or ""
    chrome = Path(pf) / "Google" / "Chrome" / "Application" / "chrome.exe" if pf else None
    if chrome and chrome.is_file():
        try:
            return p.chromium.launch(executable_path=str(chrome))
        except Exception as e:  # noqa: BLE001
            last = e
    # 最后才下载官方 Chromium
    import subprocess
    print("Playwright Chromium 不在，正在 install chromium …", flush=True)
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=False,
    )
    try:
        return p.chromium.launch()
    except Exception as e:  # noqa: BLE001
        last = e
    raise SystemExit(f"无法启动 Playwright Chromium：{last}")


def _scale_main_orange(svg: str, diameter_frac: float) -> str:
    """主图标橙子默认直径 72%。48/64 放大到 84%，只缩放橙子，底板不动。"""
    scale = diameter_frac / 0.72
    marker = "<!-- 橙子皮：占底板 72%，中心向右上偏 3% -->"
    if marker not in svg:
        return svg
    head, rest = svg.split(marker, 1)
    close = rest.rfind("</g>")
    if close < 0:
        return svg
    orange, tail = rest[:close], rest[close:]
    wrapped = (
        f"{marker}\n"
        f'    <g transform="translate(512,512) scale({scale:.6f}) translate(-512,-512)">\n'
        f"{orange}"
        f"    </g>\n    "
    )
    return head + wrapped + tail


def _svg_for_size(size: int, main_svg: str, small_svg: str) -> str:
    """≤32 用无底板小图；48/64 主图橙子放大到 84%；其余原样。"""
    if size <= 32:
        return small_svg
    if size in (48, 64):
        return _scale_main_orange(main_svg, 0.84)
    return main_svg


def _render_svg(page, svg_text: str, size: int, dest: Path) -> None:
    import re
    body = svg_text
    if body.lstrip().startswith("<?xml"):
        body = body.split("?>", 1)[-1]
    # 源文件 width/height=1024；不改属性的话截图只拍到左上角一截
    body = re.sub(r'\swidth="[^"]*"', f' width="{size}"', body, count=1)
    body = re.sub(r'\sheight="[^"]*"', f' height="{size}"', body, count=1)
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'><style>"
        "html,body{margin:0;padding:0;background:transparent;overflow:hidden}"
        "svg{display:block}"
        f"</style></head><body>{body}</body></html>"
    )
    page.set_viewport_size({"width": size, "height": size})
    page.set_content(html, wait_until="load")
    page.locator("svg").screenshot(path=str(dest), omit_background=True)


def _write_ico(pngs: dict[int, Path], dest: Path) -> None:
    """Pillow 读各尺寸 PNG，再写成含 6 帧的 ICO（PNG 压缩条目，Vista+）。

    Pillow 自带的 ICO 保存对 append_images 不可靠，只写出第一帧；
    容器格式很简单，这里自己拼，保证 256/128/64/48/32/16 都在。
    """
    import io
    from PIL import Image

    blobs: list[bytes] = []
    for s in ICO_SIZES:
        im = Image.open(pngs[s]).convert("RGBA")
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        blobs.append(buf.getvalue())
        im.close()
    count = len(ICO_SIZES)
    offset = 6 + 16 * count
    header = struct.pack("<HHH", 0, 1, count)
    directory = []
    for s, blob in zip(ICO_SIZES, blobs):
        w = 0 if s >= 256 else s
        h = 0 if s >= 256 else s
        directory.append(struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(blob), offset))
        offset += len(blob)
    dest.write_bytes(header + b"".join(directory) + b"".join(blobs))


def _write_icns(pngs: dict[int, Path], dest: Path) -> None:
    """纯 Python 写 icns：头 + ic07..ic14，每块内容是 PNG 字节。"""
    chunks: list[bytes] = []
    for tag, size in ICNS_CHUNKS:
        payload = pngs[size].read_bytes()
        # 块长 = 8 字节头 + PNG
        chunks.append(tag.encode("ascii") + struct.pack(">I", 8 + len(payload)) + payload)
    body = b"".join(chunks)
    total = 8 + len(body)
    dest.write_bytes(b"icns" + struct.pack(">I", total) + body)


def _write_preview(pngs: dict[int, Path], dest: Path) -> None:
    """1024 / 256 / 64 / 32 / 16 并排贴在深色底上。"""
    from PIL import Image
    show = (1024, 256, 64, 32, 16)
    pad, gap = 56, 40
    width = pad * 2 + sum(show) + gap * (len(show) - 1)
    height = pad * 2 + 1024
    canvas = Image.new("RGBA", (width, height), (0x12, 0x11, 0x10, 255))
    x = pad
    for s in show:
        im = Image.open(pngs[s]).convert("RGBA")
        y = pad + (1024 - s) // 2
        canvas.alpha_composite(im, (x, y))
        im.close()
        x += s + gap
    canvas.save(dest)
    canvas.close()


def _write_check16(src: Path, dest: Path) -> None:
    """16px 放大 8 倍（最近邻），给人眼审是不是橙色圆盘。"""
    from PIL import Image
    im = Image.open(src).convert("RGBA")
    im.resize((128, 128), Image.NEAREST).save(dest)
    im.close()


def _verify(pngs: dict[int, Path]) -> None:
    from PIL import Image

    ico = ASSETS / "orante.ico"
    icns = ASSETS / "orante.icns"
    preview = ASSETS / "preview.png"

    im = Image.open(ico)
    got = set(im.info.get("sizes") or [])
    im.close()
    want = {(s, s) for s in ICO_SIZES}
    if got != want:
        raise SystemExit(f"ICO 尺寸不对：读到 {sorted(got)}，期望 {sorted(want)}")
    # 16px：中心不透明暖色；96% 直径所以靠近边缘的中轴仍是橙子；四角不能是深色底板
    with Image.open(pngs[16]) as im16:
        rgba = im16.convert("RGBA")
        r, g, b, a = rgba.getpixel((8, 8))
        if a < 200 or r < 80 or r <= b:
            raise SystemExit(f"icon-16 中心不像橙色圆盘: rgba=({r},{g},{b},{a})")
        r2, g2, b2, a2 = rgba.getpixel((8, 2))
        if a2 < 160 or r2 < 80 or r2 <= b2:
            raise SystemExit(f"icon-16 未撑满：(8,2) rgba=({r2},{g2},{b2},{a2})")
        r0, g0, b0, a0 = rgba.getpixel((0, 0))
        if a0 > 80 and r0 < 50 and g0 < 50 and b0 < 50:
            raise SystemExit(f"icon-16 四角仍是深色底板: rgba=({r0},{g0},{b0},{a0})")

    data = icns.read_bytes()
    if data[:4] != b"icns":
        raise SystemExit("ICNS 头四字节不是 icns")
    declared = struct.unpack(">I", data[4:8])[0]
    if declared != len(data):
        raise SystemExit(f"ICNS 总长度字段 {declared} != 实际 {len(data)}")

    prev = Image.open(preview)
    pw, ph = prev.size
    prev.close()
    # pad 56*2 + 1024+256+64+32+16 + gap 40*4 = 112 + 1392 + 160 = 1664 × 1136
    if pw < 1600 or ph < 1100:
        raise SystemExit(f"preview.png 尺寸异常 {pw}x{ph}")

    for s in PNG_SIZES:
        with Image.open(pngs[s]) as p:
            if p.size != (s, s):
                raise SystemExit(f"icon-{s}.png 尺寸 {p.size}")


def main() -> int:
    _ensure_playwright()
    from playwright.sync_api import sync_playwright
    from PIL import Image  # noqa: F401  验收也要用

    PNG_DIR.mkdir(parents=True, exist_ok=True)
    main_svg = (ASSETS / "icon.svg").read_text(encoding="utf-8")
    small_svg = (ASSETS / "icon-small.svg").read_text(encoding="utf-8")

    pngs: dict[int, Path] = {s: PNG_DIR / f"icon-{s}.png" for s in PNG_SIZES}

    with sync_playwright() as p:
        browser = _launch_chromium(p)
        context = browser.new_context(device_scale_factor=1)
        page = context.new_page()
        for size in PNG_SIZES:
            svg = _svg_for_size(size, main_svg, small_svg)
            if size <= 32:
                tag = "small-96%"
            elif size in (48, 64):
                tag = "main-84%"
            else:
                tag = "main"
            print(f"  render {size}px  ({tag})", flush=True)
            _render_svg(page, svg, size, pngs[size])
        browser.close()

    print("  ico / icns / preview / check-16x8", flush=True)
    _write_ico(pngs, ASSETS / "orante.ico")
    _write_icns(pngs, ASSETS / "orante.icns")
    _write_preview(pngs, ASSETS / "preview.png")
    _write_check16(pngs[16], PNG_DIR / "check-16x8.png")
    _verify(pngs)
    print("OK", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
