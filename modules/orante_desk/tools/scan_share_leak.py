# -*- coding: utf-8 -*-
"""分发包泄露体检。出包脚本在打 zip 之前必须调它；不返回 0 就不许有 zip。

对目录（或 zip）里每一个文件、每一个文件名/目录名扫一遍。
命中就打印 文件:行号: 那一行，最后汇总并 sys.exit(1)。

--source 扫源目录本身，只报不拦（提前发现代码里写死的本机路径）。
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import sqlite3
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REAL_DB = ROOT / "data" / "orante.db"

# 放行名单：逐条、每条一句为什么。超过 15 条 = 白名单出了问题。
# 匹配规则：命中片段必须和这条 needle 在同一行上重叠，才算放行。
# 不许用一个大正则把整类字符串一笔带过。
ALLOW: list[tuple[str, str]] = [
    (r"C:\Program Files",
     "chrome.py 查找本机 Google Chrome 的默认安装目录，不是私人路径"),
    (r"C:\Program Files (x86)",
     "chrome.py 查找 32 位 Chrome 的安装目录，不是私人路径"),
    ("encrypted_key",
     "Chrome Local State 的字段名，cookie 搬运要读这个键，源码里没有真实密钥"),
    ("labs.google",
     "Flow 健康检查 / cookie 域过滤：登录后页面可能落在这个域"),
    ("flow.google.com/project/",
     "账号抽屉和向导的占位提示，教对方从地址栏复制项目地址，不是真实项目"),
    ("conversation=",
     "Grok 成片页 URL 参数名，代码用来解析会话，不是真实会话 id"),
    ("XSRF",
     "Flow 页面令牌字段名；拿不到就报错。源码里没有真实 token"),
    ("Bearer ",
     "HTTP Authorization 头的构造前缀，后面的 key 来自用户自己填的接口池"),
    ("x-xai-request-id",
     "Grok API 请求头名字，里面的 xai- 不是 xAI API token"),
    ("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99",
     "Windows IPropertyStore COM 接口 IID，Install-Shortcuts.bat 用来设快捷方式 AppId"),
    # 规格 90：建快捷方式改成 Python 直接调 IShellLink，这三个是微软公开的固定 CLSID/IID，
    # 全世界每台 Windows 都一样，不是谁的私人 id。
    ("00021401-0000-0000-C000-000000000046",
     "Windows ShellLink 的 CLSID（shortcut.py 建 .lnk 用）"),
    ("000214F9-0000-0000-C000-000000000046",
     "Windows IShellLinkW 接口 IID（shortcut.py 建 .lnk 用）"),
    ("0000010B-0000-0000-C000-000000000046",
     "Windows IPersistFile 接口 IID（shortcut.py 存 .lnk 用）"),
    ("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3",
     "Windows PKEY_AppUserModel_ID，同上"),
    (r"D:\Orante",
     "分发版说明书里推荐的解压路径示例（纯英文、盘符根目录），不是本机开发目录"),
    ("BJRU5ErkJggg==",
     "PNG 的 base64 结尾（IEND），ai.py 里嵌了一张 1×1 探测图，不是密钥"),
]

# 本机痕迹（不含账号名，账号名运行时从真实库读）
_LOCAL_NEEDLES = (
    "D:\\",
    "C:\\Users\\",
    "下载图片",
    "1_PROJECT",
    "0_Dev",
    "Timco",
    ".grok-lucy",
)
# sk- / gsk_ / xai- 必须前面不是字母，不然 task-pick 这种标签会误伤
_KEY_RES = (
    ("AIza", re.compile(r"AIza")),
    ("sk-", re.compile(r"(?<![A-Za-z])sk-[A-Za-z0-9]{8,}")),
    ("gsk_", re.compile(r"(?<![A-Za-z])gsk_[A-Za-z0-9]{8,}")),
    ("xai-", re.compile(r"(?<![A-Za-z])xai-")),
    ("Bearer ", re.compile(r"Bearer ")),
    ("XSRF", re.compile(r"XSRF")),
    ("__Secure-", re.compile(r"__Secure-")),
    ("SAPISID", re.compile(r"SAPISID")),
    ("encrypted_key", re.compile(r"encrypted_key")),
)
_BIN_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".icns",
            ".exe", ".dll", ".bin", ".woff", ".woff2", ".ttf", ".otf"}
_URL_NEEDLES = (
    "flow.google.com/project/",
    "labs.google",
    "grok.com/imagine/post/",
    "conversation=",
)
_LANE_NEEDLES = ("TIK-", "LUCA-")

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.\w+")
# 连续 hex ≥ 32；base64 必须带 +/ 或末尾 =，避免把普通英文长词当密钥
_HEX32_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{32,}(?![0-9a-fA-F])")
_B64_RE = re.compile(r"[A-Za-z0-9+/]{32,}={1,2}|[A-Za-z0-9]{8,}[+/][A-Za-z0-9+/=]{20,}")


def load_account_needles(db_path: Path | None = None) -> list[str]:
    """从真实库现读账号名 + extra.account。名单不会过期。"""
    p = db_path or REAL_DB
    out: list[str] = []
    if not p.is_file():
        return out
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    except sqlite3.Error:
        return out
    try:
        for name, extra in con.execute("SELECT name, extra FROM accounts"):
            if name and str(name).strip():
                out.append(str(name).strip())
            if extra:
                try:
                    e = json.loads(extra)
                except Exception:
                    e = {}
                acc = (e or {}).get("account") if isinstance(e, dict) else None
                if acc and str(acc).strip():
                    out.append(str(acc).strip())
    finally:
        con.close()
    # 去重，长的优先（避免短名先匹配把上下文截没）
    seen = set()
    uniq = []
    for n in sorted(set(out), key=lambda s: (-len(s), s)):
        k = n.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(n)
    return uniq


def _machine() -> str:
    """本机名。出包时要确认它没漏进包里。

    ORANTE_SCAN_MACHINE 可以覆盖：在别的机器（或容器）上复核出包时，
    platform.node() 拿到的是那台机器的名字，对这份包毫无意义，
    反而会因为名字太常见（比如就叫 claude）把词库里的普通词报成泄露。
    设成空字符串 = 这一项不查（账号名、邮箱、UUID、Key 这些照查）。
    """
    env = os.environ.get("ORANTE_SCAN_MACHINE")
    if env is not None:
        return env.strip()
    try:
        return (platform.node() or "").strip()
    except Exception:
        return ""


def _overlap_allow(line: str, start: int, end: int) -> bool:
    """命中区间是否和某条 ALLOW needle 在这一行上重叠。"""
    for needle, _why in ALLOW:
        idx = 0
        while True:
            j = line.find(needle, idx)
            if j < 0:
                break
            if not (end <= j or start >= j + len(needle)):
                return True
            idx = j + 1
    return False


def _add_hit(hits: list, rel: str, lineno: int, line: str, start: int, end: int) -> None:
    if _overlap_allow(line, start, end):
        return
    shown = line.replace("\r", "").replace("\t", " ")
    if len(shown) > 200:
        shown = shown[:200] + "…"
    hits.append(f"{rel}:{lineno}: {shown}")


def _find_literal(line: str, needle: str):
    """在 line 里找 needle 的所有出现，返回 (start, end) 列表。"""
    out = []
    idx = 0
    while True:
        j = line.find(needle, idx)
        if j < 0:
            break
        out.append((j, j + len(needle)))
        idx = j + 1
    return out


def _word_spans(line: str, word: str):
    """大小写不敏感的整词匹配。短名（dong/luca）不会误伤 download。"""
    if not word:
        return []
    out = []
    pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(word) + r"(?![A-Za-z0-9_])", re.I)
    for m in pattern.finditer(line):
        out.append((m.start(), m.end()))
    return out


def _scan_literals(rel: str, text: str, needles: dict, hits: list) -> None:
    """账号名、本机痕迹、Key 前缀、泳道、项目 URL。文本和二进制都扫。"""
    lines = text.splitlines()
    if not lines and text:
        lines = [text]
    for i, line in enumerate(lines, 1):
        for name in needles["accounts"]:
            for a, b in _word_spans(line, name):
                _add_hit(hits, rel, i, line, a, b)
        mach = needles["machine"]
        if mach:
            for a, b in _word_spans(line, mach):
                _add_hit(hits, rel, i, line, a, b)
        for n in _URL_NEEDLES:
            for a, b in _find_literal(line, n):
                _add_hit(hits, rel, i, line, a, b)
        for _label, cre in _KEY_RES:
            for m in cre.finditer(line):
                _add_hit(hits, rel, i, line, m.start(), m.end())
        for n in _LOCAL_NEEDLES:
            for a, b in _find_literal(line, n):
                _add_hit(hits, rel, i, line, a, b)
        for n in _LANE_NEEDLES:
            for a, b in _find_literal(line, n):
                _add_hit(hits, rel, i, line, a, b)


def _scan_regexes(rel: str, text: str, hits: list) -> None:
    """UUID / 邮箱 / 长 hex / base64：只对文本文件跑，二进制噪声太多。"""
    lines = text.splitlines()
    if not lines and text:
        lines = [text]
    for i, line in enumerate(lines, 1):
        for m in _UUID_RE.finditer(line):
            _add_hit(hits, rel, i, line, m.start(), m.end())
        for m in _EMAIL_RE.finditer(line):
            _add_hit(hits, rel, i, line, m.start(), m.end())
        for m in _HEX32_RE.finditer(line):
            _add_hit(hits, rel, i, line, m.start(), m.end())
        for m in _B64_RE.finditer(line):
            _add_hit(hits, rel, i, line, m.start(), m.end())


def scan_text(rel: str, text: str, needles: dict, hits: list, *, binary: bool = False) -> None:
    _scan_literals(rel, text, needles, hits)
    if not binary:
        _scan_regexes(rel, text, hits)


def _is_binary(rel: str, data: bytes) -> bool:
    suf = Path(rel).suffix.lower()
    if suf in _BIN_EXT:
        return True
    if b"\x00" in data[:8192]:
        return True
    return False


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", "replace")


def scan_bytes(rel: str, data: bytes, needles: dict, hits: list) -> None:
    binary = _is_binary(rel, data)
    scan_text(rel, _decode(data), needles, hits, binary=binary)


def scan_name(rel: str, needles: dict, hits: list) -> None:
    """文件名 / 目录名也扫。行号记 0。"""
    scan_text(rel + " [name]", rel, needles, hits)


def iter_dir(root: Path, skip_private: bool = False):
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir == ".":
            rel_dir = ""
        rel_dir_posix = rel_dir.replace("\\", "/")
        if skip_private:
            # 别往 data/.venv 里面走，chrome profile 有几千个文件
            dirnames[:] = [d for d in dirnames
                           if not _source_skip((rel_dir_posix + "/" + d).lstrip("/"))]
        # 目录名
        for d in list(dirnames):
            rel = (rel_dir_posix + "/" + d) if rel_dir_posix else d
            yield "dir", rel, None
        for fn in filenames:
            rel = (rel_dir_posix + "/" + fn) if rel_dir_posix else fn
            p = Path(dirpath) / fn
            yield "file", rel, p


def build_needles(db_path: Path | None = None) -> dict:
    return {
        "accounts": load_account_needles(db_path),
        "machine": _machine(),
    }


# --source 时跳过这些：里面是本机私人数据，不是会跟着出包的代码
_SOURCE_SKIP_PREFIX = (
    "data/", ".venv/", ".pytest_cache/", "specs/reports/img/",
    "data\\", ".venv\\",
)


def _source_skip(rel: str) -> bool:
    r = rel.replace("\\", "/")
    if r.startswith("data/") or r == "data":
        return True
    if r.startswith(".venv/") or r == ".venv":
        return True
    if r.startswith(".pytest_cache/"):
        return True
    if r.startswith("specs/reports/img/"):
        return True
    if "/__pycache__/" in "/" + r + "/":
        return True
    if _is_bak(Path(r).name):
        return True
    return False


def _is_bak(name: str) -> bool:
    return ".bak-" in name


def scan_tree(root: Path, needles: dict | None = None, *, skip_private: bool = False) -> list[str]:
    needles = needles or build_needles()
    hits: list[str] = []
    scan_name(root.name, needles, hits)
    for kind, rel, path in iter_dir(root, skip_private=skip_private):
        if skip_private and _source_skip(rel):
            continue
        scan_name(rel, needles, hits)
        if kind == "file" and path is not None:
            try:
                data = path.read_bytes()
            except OSError as e:
                hits.append(f"{rel}:0: 读不出来（{e}）")
                continue
            scan_bytes(rel, data, needles, hits)
    return hits


def scan_zip(zpath: Path, needles: dict | None = None) -> list[str]:
    needles = needles or build_needles()
    hits: list[str] = []
    with zipfile.ZipFile(zpath) as zf:
        for info in zf.infolist():
            rel = info.filename.replace("\\", "/")
            scan_name(rel, needles, hits)
            if info.is_dir():
                continue
            try:
                data = zf.read(info)
            except Exception as e:
                hits.append(f"{rel}:0: zip 读不出来（{e}）")
                continue
            scan_bytes(rel, data, needles, hits)
    return hits


def report(hits: list[str], label: str) -> int:
    n = len(hits)
    if n:
        print(f"【泄露体检】{label} 命中 {n} 处：")
        for h in hits:
            print(h)
        print(f"合计 {n} 处。")
        return 1
    print(f"【泄露体检】{label} 通过（0 处命中）")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Orante 分发包泄露体检")
    ap.add_argument("target", nargs="?", help="要扫的目录或 zip")
    ap.add_argument("--source", action="store_true",
                    help="扫源目录 D:\\0_Dev\\Orante，只报不拦")
    ap.add_argument("--db", default="", help="账号名从这份 sqlite 读（默认真实库）")
    a = ap.parse_args(argv)

    db_path = Path(a.db) if a.db else None
    needles = build_needles(db_path)
    # 白名单只能小步加，每加一条都要在 ALLOW 里写清为什么。
    # 0925：规格 90 把建快捷方式改成直接调 IShellLink，多了三条微软公开的固定 CLSID/IID，
    # 所以上限从 15 提到 18。再要往上加之前，先想想是不是真的该放行。
    if len(ALLOW) > 18:
        print(f"警告：ALLOW 有 {len(ALLOW)} 条（>18），白名单可能出了问题")

    if a.source:
        hits = scan_tree(ROOT, needles, skip_private=True)
        report(hits, f"源目录代码（跳过 data/.venv/pycache） {ROOT}")
        return 0  # 只报不拦

    if not a.target:
        print("请给出要扫的目录或 zip，或用 --source")
        return 2
    target = Path(a.target)
    if not target.exists():
        print(f"找不到 {target}")
        return 2
    if target.is_file() and target.suffix.lower() == ".zip":
        hits = scan_zip(target, needles)
        label = f"zip {target}"
    else:
        hits = scan_tree(target, needles)
        label = f"目录 {target}"
    return report(hits, label)


if __name__ == "__main__":
    sys.exit(main())
