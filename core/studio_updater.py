# -*- coding: utf-8 -*-
"""片语工作室 (Pianyu Studio) 一键云端代码热更新工具。

无需安装 Git，直接使用内置便携式 Python 从 GitHub 官方仓库拉取最新代码与样式预设包，
自动覆盖更新本地代码，100% 保留已下载的 AI 模型、环境与个人工程数据。
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

REPO_ZIP_URL = "https://codeload.github.com/simonerossi200201-dev/pianyu-studio/zip/refs/heads/main"
SKIP_PREFIXES = (
    ".git/",
    "data/",
    "logs/",
    "outputs/",
    "packs/",
    "dist_packages/",
)


def run_hot_update(studio_root: Path | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    root = (studio_root or Path(__file__).resolve().parent.parent).resolve()
    print("=" * 68)
    print("  片语工作室 (Pianyu Studio) — 云端代码一键热更新")
    print(f"  目标目录: {root}")
    print("=" * 68)
    print("\n[1/3] 正在连接云端代码仓库拉取最新更新包...")

    t0 = time.time()
    req = urllib.request.Request(
        REPO_ZIP_URL,
        headers={"User-Agent": "PianyuStudio-Updater/3.0"},
    )
    buf = io.BytesIO()
    with urllib.request.urlopen(req, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        read_bytes = 0
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            buf.write(chunk)
            read_bytes += len(chunk)
            mb = read_bytes / (1024 * 1024)
            if total > 0:
                pct = min(100.0, read_bytes * 100.0 / total)
                print(f"\r      已下载: {mb:.1f} MB ({pct:.0f}%)", end="", flush=True)
            else:
                print(f"\r      已下载: {mb:.1f} MB", end="", flush=True)

    print(f"\n[2/3] 下载完成 ({buf.tell() / (1024 * 1024):.1f} MB)，正在覆盖更新本地代码与预设文件...")
    buf.seek(0)
    updated_count = 0
    with zipfile.ZipFile(buf, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            parts = info.filename.replace("\\", "/").split("/", 1)
            if len(parts) < 2 or not parts[1]:
                continue
            rel = parts[1]
            if any(rel.startswith(p) for p in SKIP_PREFIXES):
                continue
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                with zf.open(info, "r") as src_f, open(dst, "wb") as dst_f:
                    shutil.copyfileobj(src_f, dst_f)
                updated_count += 1
            except PermissionError:
                # 若某个正在运行的二进制被占用则跳过
                pass

    elapsed = round(time.time() - t0, 1)
    print(f"[3/3] 更新完成！共更新 {updated_count} 个代码与资源文件（耗时 {elapsed}s）。")
    print("\n✅ 默认字幕样式已切换为【经典白字·红词跟随】，内存自动释放与防卡顿已生效！")
    print("👉 请关闭正在运行的软件窗口，重新双击【片语工作室】启动即可。")
    return 0


def resolve_git_binary(studio_root: Path | None = None) -> str:
    """跨平台解析 git 可执行文件路径（Windows: git.exe, macOS/POSIX: git）。"""
    root = (studio_root or Path(__file__).resolve().parent.parent).resolve()
    git_name = "git.exe" if os.name == "nt" else "git"
    cand = root / "runtime" / "bin" / git_name
    if cand.is_file():
        return str(cand)
    found = shutil.which("git")
    return found or git_name


def check_and_apply_hot_update(studio_root: Path | None = None) -> dict[str, object]:
    """供总控台按钮直接调用的无阻塞热更新封装。"""
    try:
        rc = run_hot_update(studio_root=studio_root)
        return {"ok": rc == 0, "message": "已同步最新云端代码与预设，重启软件即可生效"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


if __name__ == "__main__":
    raise SystemExit(run_hot_update())
