# -*- coding: utf-8 -*-
"""把 Orante 按白名单拷到仓库外面的出包目录。只读源目录，只写出包目录。

铁律：本文件里任何 del / rmdir / shutil.rmtree / open(..., "w") 都不得指向
src_root。出包目录已存在时可以整棵删掉重建，但必须先确认路径里含
「Orante-分发版」，且不是源目录。

不带的东西（黑名单已经证明不可靠，所以改成白名单；下面是注释备忘）：
- app\\ / bridge\\ / web\\ / Orante.bat —— Orante 1.0 旧服务端和旧启动器，
  已经被 orante\\ + ui\\ 取代。带上去只会让对方双击错文件看到坏掉的旧界面。
- specs\\ / specs\\reports\\ —— 里面有账号名、额度数字、任务文案，
  reports/img/ 下还有成片截图。最容易被忽略的泄露口。
- data\\ 下任何文件 —— orante.db（账号表、settings 里的 Key 和工作区、
  任务文案）、chrome\\（登录态 cookie = 把 Google / Grok 账号送人）、
  google\\、logs\\、capture\\（抓包里有 XSRF / session / reCAPTCHA token）、
  ahash-cache.json、sha-cache.json、used_images.json、flow_media_cache.json、
  marks.json、lens_job.json、tmp_prompt.txt、clip-dump.html、tmp\\。
- tools\\grok-account.txt、grok-queue.txt、grok-done.txt、grok-post-dump.txt、
  tools\\test_*.py、tools\\probe*.py、tools\\_*.py、tools\\*.html —— 开发用的。
- .venv\\、.pytest_cache\\、任何 __pycache__\\、任何 *.bak-*。
- 说明文档\\Orante-2.0-规划书.md、Flow接口协议-0906.md —— 内部文档，不必给。
- orante/legacy/跑批日志.txt —— 开发机跑批记录，带本机路径。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"

# 老模块里允许带走的非 py 文件（白名单，不是「除了跑批日志都带」）
LEGACY_DATA = {
    "coverage_library.json",
    "prompt_defaults.json",
    "video_types.json",
    "flow_shots.json",
    "grok_dur_probe.json",
    "wenan_words.txt",
}

# 对方真用得上的工具。其余一个都不带。
# 杀毒软件会误杀什么，决定了这里能带什么。见 ROOT_KEEP 上面那段。
# 65 号把快捷方式脚本改成纯 WScript.Shell COM（不 Bypass、不 Add-Type、不 P/Invoke），
# 所以 创建快捷方式.bat 和它的 ASCII 转发 Install-Shortcuts.bat 可以进包。
# tools/launcher.cs、tools/build_launcher.py 留在源目录，不进分发包。
TOOLS_KEEP = {
    "13-Orante-Debug.bat",
    "build_icons.py",
    "build_me_icon.py",          # 把 "Orante me.png" 转成桌面图标用的 .ico
    "创建快捷方式.bat",
    "Install-Shortcuts.bat",
    "悬浮图标-自测.bat",          # 规格 89：在对方机器上看一眼图标好不好使
}

# Orante.exe 不带。它是 csc 现编的、没签名、没有任何信誉记录的 95 KB .NET 小程序，
# 干的事又正好是杀软的红线画像：起 cmd.exe、再用 CreateNoWindow 悄悄拉起另一个进程。
# 实测发给别人一解压就被杀掉。分发包里一个编译产物都不放，全是纯文本
# （.py / .js / .json / .bat / .md / .png），对方双击 Orante2.bat 启动，效果一样。
# 代价：没有那个图标和任务栏身份。要找回来，得买代码签名证书并且等信誉攒够，
# 内测阶段不值得。
# 65 号用对方机器上现生成的 .lnk（指向 pythonw.exe -m orante）找回图标和独立进程，
# 仍然不把 Orante.exe 加回白名单。不许为了图标把任何编译产物放回分发包。
# 0924（规格 90）：Orante.exe 和 tools/launcher.cs、tools/build_launcher.py 已经从项目里删掉，
# 不再是"留在源目录"而已。快捷方式改由 orante/shortcut.py 用 ctypes 调 IShellLink 生成，
# 创建快捷方式.bat 里那条长长的 powershell -Command 也没了——那是另一处杀软重点盯的写法。
ROOT_KEEP = {
    "Orante2.bat",
    "Orante.command",
    "requirements.txt",
    # 规格 89：悬浮图标就是这张图；也是桌面快捷方式那张 .ico 的来源。图片不招杀软。
    "Orante me.png",
}

# 分发包里一个都不许出现。出包时目录和 zip 各扫一遍，命中就拒绝出包。
COMPILED_EXT = {".exe", ".dll", ".pyd"}

EMPTY_DIRS = (
    "data",
    "data/logs",
    "data/chrome",
    "workspace",
)


def _is_bak(name: str) -> bool:
    return ".bak-" in name


def _in_pycache(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return "__pycache__" in path.parts
    return "__pycache__" in rel.parts


def read_version(src_root: Path) -> str:
    p = src_root / "orante" / "__init__.py"
    if not p.is_file():
        return "0.0.0"
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("__version__"):
            return s.split("=", 1)[1].strip().strip("\"'")
    return "0.0.0"


def default_out_dir(src_root: Path, ver: str, day: str) -> Path:
    """%~dp0\\..\\..\\_share\\Orante-分发版-<版本>-<YYYYMMDD>\\ 即仓库外面。"""
    return src_root.resolve().parent / "_share" / f"Orante-分发版-{ver}-{day}"


def default_zip_path(out_dir: Path, ver: str, day: str) -> Path:
    """zip 名里带版本号：内测包（2.0.0-beta.1）不会盖掉同一天出的正式包。"""
    return out_dir.parent / f"Orante-分发版-{ver}-{day}.zip"


def _assert_dest_safe(dest: Path, src_root: Path) -> None:
    """出包目录已存在时才能删：必须含 Orante-分发版，且绝不是源目录。"""
    dest_r = dest.resolve()
    src_r = src_root.resolve()
    if "Orante-分发版" not in dest_r.as_posix() and "Orante-分发版" not in str(dest_r):
        raise SystemExit(f"拒绝删除 {dest_r}：路径里没有「Orante-分发版」")
    if dest_r == src_r:
        raise SystemExit("拒绝删除：出包目录就是源目录")
    try:
        dest_r.relative_to(src_r)
        raise SystemExit(f"拒绝删除：出包目录落在源目录里面 {dest_r}")
    except ValueError:
        pass
    try:
        src_r.relative_to(dest_r)
        raise SystemExit(f"拒绝删除：源目录落在出包目录里面 {dest_r}")
    except ValueError:
        pass


def _wipe_dest(dest: Path) -> None:
    """只删出包目录。调用前必须已经通过 _assert_dest_safe。"""
    shutil.rmtree(dest)


def _copy_file(src_file: Path, dest_file: Path) -> None:
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_file, dest_file)


def collect_copies(src_root: Path) -> list[tuple[Path, str]]:
    """(源文件, 出包内相对路径)。只读 src_root。"""
    out: list[tuple[Path, str]] = []
    src_root = src_root.resolve()

    # 程序包 orante/**/*.py
    pkg = src_root / "orante"
    if pkg.is_dir():
        for p in pkg.rglob("*.py"):
            if _is_bak(p.name) or _in_pycache(p, src_root):
                continue
            rel = p.relative_to(src_root).as_posix()
            out.append((p, rel))

    # 老模块数据文件
    legacy = pkg / "legacy"
    if legacy.is_dir():
        for p in legacy.iterdir():
            if not p.is_file():
                continue
            if _is_bak(p.name) or p.name == "跑批日志.txt":
                continue
            if p.name in LEGACY_DATA:
                out.append((p, p.relative_to(src_root).as_posix()))

    # 多语言 orante/i18n/**
    i18n = pkg / "i18n"
    if i18n.is_dir():
        for p in i18n.rglob("*"):
            if not p.is_file():
                continue
            if _is_bak(p.name) or _in_pycache(p, src_root):
                continue
            out.append((p, p.relative_to(src_root).as_posix()))

    # 界面 ui/**
    ui = src_root / "ui"
    if ui.is_dir():
        for p in ui.rglob("*"):
            if not p.is_file():
                continue
            if _is_bak(p.name) or _in_pycache(p, src_root):
                continue
            out.append((p, p.relative_to(src_root).as_posix()))

    # 启动文件
    for name in ROOT_KEEP:
        p = src_root / name
        if p.is_file():
            out.append((p, name))

    # 工具（只带这几个）
    for name in TOOLS_KEEP:
        p = src_root / "tools" / name
        if p.is_file():
            out.append((p, "tools/" + name))

    # 说明书：HTML 正文 + 分发图。开发手册和使用说明-分发版.md 都不进包。
    manual = src_root / "说明文档" / "使用说明.html"
    if manual.is_file():
        out.append((manual, "说明文档/使用说明.html"))
    shots = src_root / "说明文档" / "分发图"
    if shots.is_dir():
        for p in shots.rglob("*"):
            if p.is_file() and not _is_bak(p.name):
                rel = p.relative_to(src_root).as_posix()
                out.append((p, rel))

    # 去重（legacy py 已由 rglob 带上）
    seen = set()
    uniq = []
    for src, rel in out:
        if rel in seen:
            continue
        seen.add(rel)
        uniq.append((src, rel))
    return uniq


def copy_whitelist(src_root: Path, dest: Path) -> list[tuple[str, int, str]]:
    """拷白名单到 dest，返回 [(相对路径, 字节, sha256 前 16 位)]。"""
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    for src_file, rel in collect_copies(src_root):
        dest_file = dest / Path(*rel.split("/"))
        _copy_file(src_file, dest_file)
        data = dest_file.read_bytes()
        digest = hashlib.sha256(data).hexdigest()[:16]
        copied.append((rel.replace("\\", "/"), len(data), digest))
    for d in EMPTY_DIRS:
        keep = dest / Path(*d.split("/")) / ".keep"
        if not keep.is_file():
            keep.parent.mkdir(parents=True, exist_ok=True)
            keep.write_text("", encoding="utf-8")
            data = keep.read_bytes()
            rel = (d.rstrip("/") + "/.keep").replace("\\", "/")
            copied.append((rel, len(data), hashlib.sha256(data).hexdigest()[:16]))
    copied.sort(key=lambda x: x[0].lower())
    return copied


def write_manifest(dest: Path, ver: str, when: datetime, copied: list) -> Path:
    """写出包目录根下的 分发清单.txt。只写 dest，不写 src_root。"""
    total = sum(n for _, n, _ in copied)
    mb = total / (1024 * 1024)
    lines = [
        "Orante 2.0 分发版",
        f"版本：{ver}   出包时间：{when.strftime('%Y-%m-%d %H:%M')}   "
        f"文件数：{len(copied)}   合计：{mb:.1f} MB",
        "",
    ]
    width = max((len(rel) for rel, _, _ in copied), default=8)
    for rel, n, digest in copied:
        lines.append(f"{rel:<{width}}  {n:>10}  {digest}")
    text = "\n".join(lines) + "\n"
    p = dest / "分发清单.txt"
    p.write_text(text, encoding="utf-8")
    return p


def fingerprint(path: Path) -> str:
    """目录/文件指纹：路径 + 大小 + mtime_ns。用来证明源目录没被碰。"""
    h = hashlib.sha256()
    if not path.exists():
        h.update(b"missing")
        return h.hexdigest()
    if path.is_file():
        data = path.read_bytes()
        h.update(str(path.stat().st_size).encode())
        h.update(data)
        return h.hexdigest()
    files = []
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames.sort()
        filenames.sort()
        for fn in filenames:
            p = Path(dirpath) / fn
            try:
                st = p.stat()
            except OSError:
                continue
            rel = os.path.relpath(p, path).replace("\\", "/")
            files.append((rel, st.st_size, st.st_mtime_ns))
    files.sort()
    for rel, size, mtime in files:
        h.update(rel.encode("utf-8", "replace"))
        h.update(b"\0")
        h.update(str(size).encode())
        h.update(b"\0")
        h.update(str(mtime).encode())
        h.update(b"\n")
    return h.hexdigest()


def tree_sha256(path: Path) -> str:
    """全目录逐字节 sha256（测试用，假源目录很小）。"""
    h = hashlib.sha256()
    if not path.exists():
        return h.hexdigest()
    files = []
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames.sort()
        filenames.sort()
        for fn in filenames:
            files.append(Path(dirpath) / fn)
        for d in dirnames:
            files.append(Path(dirpath) / d)
    files.sort(key=lambda p: str(p).lower())
    for p in files:
        rel = os.path.relpath(p, path).replace("\\", "/")
        h.update(rel.encode("utf-8", "replace"))
        if p.is_file():
            h.update(b" F ")
            h.update(p.read_bytes())
        else:
            h.update(b" D ")
        h.update(b"\n")
    return h.hexdigest()


def run_scan(target: Path) -> None:
    r = subprocess.run(
        [sys.executable, str(TOOLS / "scan_share_leak.py"), str(target)],
        cwd=str(ROOT),
    )
    if r.returncode != 0:
        raise SystemExit(r.returncode or 1)


def run_smoke(dest: Path) -> None:
    r = subprocess.run(
        [sys.executable, str(TOOLS / "smoke_share.py"), str(dest)],
        cwd=str(ROOT),
    )
    if r.returncode != 0:
        raise SystemExit(r.returncode or 1)


def compiled_entries_in_dir(root: Path) -> list[str]:
    """出包目录里的 .exe / .dll / .pyd。空列表才许打 zip。"""
    out: list[str] = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in COMPILED_EXT:
            out.append(p.relative_to(root).as_posix())
    return sorted(out)


def compiled_entries_in_zip(zip_path: Path) -> list[str]:
    """zip 里的 .exe / .dll / .pyd。空列表才算过关。"""
    out: list[str] = []
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if Path(name).suffix.lower() in COMPILED_EXT:
                out.append(name)
    return sorted(out)


def _assert_no_compiled(items: list[str], where: str) -> None:
    if items:
        raise SystemExit(
            f"{where} 含编译产物（不许带 .exe/.dll/.pyd）：\n  "
            + "\n  ".join(items)
        )


def make_zip(dest: Path, zip_path: Path) -> tuple[int, int]:
    """压 dest 成 zip。返回 (字节, 文件数)。"""
    if zip_path.exists():
        # 删的是 _share 下的旧 zip，不是源目录
        if "分发版" not in zip_path.name:
            raise SystemExit(f"拒绝覆盖不像分发包的 zip：{zip_path}")
        zip_path.unlink()
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    nfiles = 0
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(dest):
            dirnames.sort()
            filenames.sort()
            for fn in filenames:
                p = Path(dirpath) / fn
                arc = p.relative_to(dest).as_posix()
                zf.write(p, arc)
                nfiles += 1
    return zip_path.stat().st_size, nfiles


def build(src_root: Path, dest: Path, *, do_scan: bool = True,
          do_smoke: bool = True, do_zip: bool = True) -> dict:
    src_root = src_root.resolve()
    dest = dest.resolve()
    ver = read_version(src_root)
    now = datetime.now()
    day = now.strftime("%Y%m%d")

    # 跑前指纹：证明源目录私人数据没被碰
    fp_before = {
        "db": fingerprint(src_root / "data" / "orante.db"),
        "chrome": fingerprint(src_root / "data" / "chrome"),
        "logs": fingerprint(src_root / "data" / "logs"),
    }

    if dest.exists():
        _assert_dest_safe(dest, src_root)
        _wipe_dest(dest)

    copied = copy_whitelist(src_root, dest)
    write_manifest(dest, ver, now, copied)
    _assert_no_compiled(compiled_entries_in_dir(dest), "出包目录")

    if do_scan:
        print("—— 出包目录泄露体检 ——")
        run_scan(dest)
        print("体检通过")

    if do_smoke:
        print("—— 冒烟 ——")
        run_smoke(dest)
        print("冒烟通过")

    zip_path = None
    zip_n = 0
    zip_bytes = 0
    if do_zip:
        zip_path = default_zip_path(dest, ver, day)
        zip_bytes, zip_n = make_zip(dest, zip_path)
        _assert_no_compiled(compiled_entries_in_zip(zip_path), "zip")
        if do_scan:
            print("—— zip 泄露体检 ——")
            run_scan(zip_path)
            print("体检通过")

    fp_after = {
        "db": fingerprint(src_root / "data" / "orante.db"),
        "chrome": fingerprint(src_root / "data" / "chrome"),
        "logs": fingerprint(src_root / "data" / "logs"),
    }
    if fp_before != fp_after:
        raise SystemExit(
            "源目录私人数据在出包过程中变了（这是 bug，zip 已出也作废）：\n"
            f"  前 {fp_before}\n  后 {fp_after}"
        )

    mb = (zip_bytes / (1024 * 1024)) if zip_bytes else 0
    result = {
        "version": ver,
        "dest": str(dest),
        "files": len(copied) + 1,  # + 分发清单
        "zip": str(zip_path) if zip_path else "",
        "zip_bytes": zip_bytes,
        "zip_files": zip_n,
        "fp_before": fp_before,
        "fp_after": fp_after,
        "mb": mb,
    }
    if zip_path:
        print(
            f"已出包 {zip_path}（{mb:.1f} MB，{zip_n} 个文件）"
        )
        print(
            f"体检通过 / 冒烟通过 / 已出包 {zip_path.name}"
            f"（{mb:.1f} MB，{zip_n} 个文件）"
        )
    else:
        print(f"出包目录 {dest}（未打 zip）")
    print("源目录指纹（跑前 = 跑后）：")
    for k in ("db", "chrome", "logs"):
        print(f"  data/{k}: {fp_before[k]}")
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Orante 白名单出包（不动源目录）")
    ap.add_argument("--src", default="", help="源目录，默认本仓库")
    ap.add_argument("--out", default="", help="出包目录（整棵就是这一份）")
    ap.add_argument("--no-scan", action="store_true")
    ap.add_argument("--no-smoke", action="store_true")
    ap.add_argument("--no-zip", action="store_true")
    a = ap.parse_args(argv)

    src_root = Path(a.src).resolve() if a.src else ROOT
    ver = read_version(src_root)
    day = datetime.now().strftime("%Y%m%d")
    dest = Path(a.out).resolve() if a.out else default_out_dir(src_root, ver, day)
    build(
        src_root, dest,
        do_scan=not a.no_scan,
        do_smoke=not a.no_smoke,
        do_zip=not a.no_zip,
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:
        print(f"出包失败：{e}", file=sys.stderr)
        sys.exit(1)
