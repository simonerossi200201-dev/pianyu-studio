# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""出包门禁 0：每个测试文件都必须有一条「在完全相同的内容上跑绿」的记录。

以前门禁 0 是一口气 ``pytest tests``（约 2500 条、1～2 小时、全在一个进程里），
任何一条计时类测试在负载下抖一下就整轮作废，打包时还要再跑一遍。现在：

1. **一个测试文件一个进程**（复用 ``pytest_each`` 的做法），崩了只算那一个文件。
2. **绿过就记账**：每个文件算一把钥匙 = 产品代码指纹 + 运行环境指纹 + 这个测试文件
   本身的内容（+ 读文档的测试再加文档指纹）。钥匙一样、上次是绿的 → 不再跑。
   任何一个字节变了，钥匙就变，自动重跑。所以「跳过」只发生在「内容完全没变」时。
3. **红了只重跑一次**：第一次红的那几条，单独再起一个干净进程跑一次。
   第二次绿 → 记为「重跑后过」，日志里单独列出来（不藏）；第二次还红 → 真红。
4. 跑每个文件前后各算一次指纹；测试在跑的时候改了仓库里的入库文件，这条不记账并报出路径。

用法（仓库根目录）::

    runtime\\venv\\Scripts\\python.exe tools\\gate0.py            # 跑门禁 0
    runtime\\venv\\Scripts\\python.exe tools\\gate0.py --status   # 只看哪些要跑，不跑
    runtime\\venv\\Scripts\\python.exe tools\\gate0.py tests\\test_xxx.py ...

退出码 0 = 全部文件在当前内容上都有绿记录。记录在
``docs/plan/queue/_log/_gate0_green.jsonl``，可读汇总在 ``_gate0.log``。
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(REPO, "docs", "plan", "queue", "_log")
CACHE = os.path.join(LOG_DIR, "_gate0_green.jsonl")
LOG = os.path.join(LOG_DIR, "_gate0.log")

# 改这里的规则 = 旧记录全部作废（钥匙里带着这个号）。
GATE_VERSION = "gate0-v1"
DEFAULT_TIMEOUT = 1800

# 测试运行时会写、或者根本不是产品输入的路径：不进指纹。
VOLATILE_PREFIXES = (
    "docs/plan/queue/",
    "AI Have To Know/",
    "说明文档/",
    "tools/_diag/",
    "build/",
)
VOLATILE_SUFFIXES = (".autosave.json", ".autosave.json.tmp", "/mismatch.png")
# 证据目录：测试会往里面写 png/json。入库的文件按「已提交的版本」算，
# 工作区里被测试改写的内容和没入库的新文件都不算。
INDEX_ONLY_PREFIXES = ("docs/plan/reports/",)
# 读文档的测试才把文档算进钥匙；不读文档的测试，改 AGENTS.md / 交接报告不会让它重跑。
DOCS_SENSITIVE_RE = re.compile(
    r"docs|\.md\b|os\.walk|rglob|glob\(|ls-files|AGENTS|README|THIRD_PARTY")
_ENV_STAT_DIRS = ("runtime/ffmpeg", "runtime/models", "runtime/mimalloc", "runtime/python")
_ENV_STAT_SKIP = {"__pycache__", ".cache", "huggingface"}
_ENV_FILES = ("runtime/venv/.cutora_ready", "runtime/venv/pyvenv.cfg")
_ENV_VAR_PREFIXES = ("CUTORA_", "QT_", "CUDA_", "PYTHON", "PYTEST")
_SUM = re.compile(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed)")


# ---------------------------------------------------------------------------
# 指纹
# ---------------------------------------------------------------------------

def is_test_file(path: str) -> bool:
    """tests/ 下的 test_*.py：它们各自进自己的钥匙，不进公共指纹。"""
    if not path.startswith("tests/"):
        return False
    leaf = path.rsplit("/", 1)[-1]
    return leaf.startswith("test_") and leaf.endswith(".py")


def classify(path: str) -> str:
    """返回 'skip' / 'docs' / 'code'。"""
    if "__pycache__/" in path or path.endswith(".pyc"):
        return "skip"
    if path.startswith(VOLATILE_PREFIXES) or path.endswith(VOLATILE_SUFFIXES):
        return "skip"
    if is_test_file(path):
        return "skip"
    if path.endswith(".md") and "/" not in path:
        return "docs"            # 仓库根的说明文档
    if path.endswith(".md") and path.startswith("docs/") and not path.startswith("docs/api/"):
        return "docs"            # docs/api 是产品运行时读的，留在 code
    return "code"


def tier_digest(entries: dict) -> str:
    """entries: path -> blob sha。顺序无关。"""
    h = hashlib.sha256()
    for path in sorted(entries):
        h.update(path.encode("utf-8"))
        h.update(b"\0")
        h.update(entries[path].encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def is_docs_sensitive(test_source: str) -> bool:
    return bool(DOCS_SENSITIVE_RE.search(test_source))


def file_key(code_fp: str, env_fp: str, docs_fp: str, test_sha: str,
             docs_sensitive: bool) -> str:
    parts = [GATE_VERSION, code_fp, env_fp, test_sha,
             docs_fp if docs_sensitive else "-"]
    return hashlib.sha256("|".join(parts).encode("ascii")).hexdigest()


def _git(args: list[str]) -> bytes:
    r = subprocess.run(["git", "-c", "core.quotepath=off"] + args, cwd=REPO,
                       capture_output=True, timeout=120,
                       creationflags=_no_window())
    if r.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args), r.stderr.decode("utf-8", "replace")))
    return r.stdout


def _no_window() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0


def _split_z(blob: bytes) -> list[str]:
    return [p.decode("utf-8", "surrogateescape") for p in blob.split(b"\0") if p]


def _hash_objects(paths: list[str]) -> dict:
    """工作区文件的 git blob sha（和入库算法一样）。删掉的文件记 DELETED。"""
    out = {}
    present = [p for p in paths if os.path.isfile(os.path.join(REPO, p))]
    for p in paths:
        if p not in present:
            out[p] = "DELETED"
    if present:
        r = subprocess.run(["git", "hash-object", "--no-filters", "--stdin-paths"], cwd=REPO,
                           input=("\n".join(present) + "\n").encode("utf-8"),
                           capture_output=True, timeout=300, creationflags=_no_window())
        if r.returncode != 0:
            raise RuntimeError("git hash-object: %s" % r.stderr.decode("utf-8", "replace"))
        shas = r.stdout.decode("ascii").split()
        out.update(zip(present, shas))
    return out


def repo_fingerprints() -> dict:
    """{'code': sha, 'docs': sha, 'tests': {path: blob sha}}，全部按工作区当前内容。"""
    index = {}
    for line in _split_z(_git(["ls-files", "-s", "-z"])):
        meta, path = line.split("\t", 1)
        index[path] = meta.split()[1]
    dirty = _split_z(_git(["diff", "--name-only", "-z"]))
    untracked = _split_z(_git(["ls-files", "-o", "--exclude-standard", "-z"]))
    rehash = [p for p in dirty if p in index and not p.startswith(INDEX_ONLY_PREFIXES)]
    rehash += [p for p in untracked if not p.startswith(INDEX_ONLY_PREFIXES)]
    rehash = [p for p in rehash if classify(p) != "skip" or is_test_file(p)]
    current = dict(index)
    current.update(_hash_objects(rehash))
    code, docs, tests = {}, {}, {}
    for path, sha in current.items():
        if is_test_file(path):
            tests[path] = sha
            continue
        kind = classify(path)
        if kind == "code":
            code[path] = sha
        elif kind == "docs":
            docs[path] = sha
    return {"code": tier_digest(code), "docs": tier_digest(docs), "tests": tests,
            "_code_map": code, "_docs_map": docs}


def env_fingerprint() -> str:
    """解释器、依赖锁、运行库目录（ffmpeg / 模型 / 自带 Python）的大小和时间戳。"""
    h = hashlib.sha256()
    h.update(("%s|%s|%s\n" % (sys.version, sys.executable, platform.node())).encode("utf-8"))
    # 会改变测试行为的环境变量（离屏 / 合成并发 / 显卡可见性 / 解释器开关）
    for name in sorted(os.environ):
        if name.upper().startswith(_ENV_VAR_PREFIXES):
            h.update(("env %s=%s\n" % (name.upper(), os.environ[name])).encode("utf-8"))
    for rel in _ENV_FILES:
        p = os.path.join(REPO, rel)
        if os.path.isfile(p):
            with open(p, "rb") as fh:
                h.update(rel.encode("utf-8") + b"\0" + hashlib.sha256(fh.read()).digest())
        else:
            h.update(rel.encode("utf-8") + b"\0MISSING")
    for rel in _ENV_STAT_DIRS:
        root = os.path.join(REPO, rel)
        rows = []
        for dp, dns, fns in os.walk(root):
            dns[:] = sorted(d for d in dns if d not in _ENV_STAT_SKIP)
            for fn in fns:
                if fn.endswith(".pyc"):
                    continue
                full = os.path.join(dp, fn)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                rows.append("%s %d %d" % (os.path.relpath(full, REPO).replace("\\", "/"),
                                          st.st_size, st.st_mtime_ns))
        rows.sort()
        h.update(("\n".join(rows) + "\n").encode("utf-8"))
    return h.hexdigest()


def _changed_paths(before: dict, after: dict) -> list[str]:
    out = []
    for tier in ("_code_map", "_docs_map"):
        a, b = before[tier], after[tier]
        out += sorted(p for p in set(a) | set(b) if a.get(p) != b.get(p))
    return out


# ---------------------------------------------------------------------------
# 运行
# ---------------------------------------------------------------------------

def _crash_name(code: int) -> str:
    u = code & 0xFFFFFFFF
    return {0xC0000005: "访问冲突 0xC0000005", 0xC0000409: "栈溢出/快速失败 0xC0000409",
            0xC0000374: "堆损坏 0xC0000374"}.get(u, "退出码 %d (0x%08X)" % (code, u))


def run_pytest(targets: list[str], timeout: int) -> dict:
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rfE"] + targets
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout,
                              creationflags=_no_window())
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        return {"ok": False, "code": None, "secs": time.time() - t0, "counts": {},
                "fails": [], "note": "超时（%d 秒）" % timeout, "detail": out.splitlines()[-40:]}
    out = (proc.stdout or "") + (proc.stderr or "")
    code = proc.returncode
    counts = {}
    last = out.strip().splitlines()[-1] if out.strip() else ""
    for n, kind in _SUM.findall(last):
        counts[kind] = int(n)
    fails = sorted(set(re.findall(r"^(?:FAILED|ERROR) (\S+)", out, re.M)))
    note = ""
    if code not in (0, 1, 5):    # 0 过 / 1 有失败 / 5 没收集到测试
        note = "进程崩溃：" + _crash_name(code)
    detail = [ln for ln in out.splitlines()
              if ln.startswith(("E ", ">   ", "FAILED ", "ERROR "))][-40:]
    if note and not detail:
        detail = out.splitlines()[-40:]
    ok = code in (0, 5) and not fails and not note
    return {"ok": ok, "code": code, "secs": time.time() - t0, "counts": counts,
            "fails": fails, "note": note, "detail": detail}


def load_cache() -> dict:
    """file -> 最新一条记录（不管红绿）。"""
    out = {}
    if not os.path.isfile(CACHE):
        return out
    with open(CACHE, encoding="utf-8") as fh:
        for ln in fh:
            try:
                rec = json.loads(ln)
            except ValueError:
                continue          # 断电写了半行
            if rec.get("file"):
                out.setdefault(rec["file"], {})[rec.get("key")] = rec
    return out


def _append(rec: dict) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(CACHE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _head() -> str:
    try:
        return _git(["rev-parse", "--short", "HEAD"]).decode().strip()
    except Exception:
        return ""


def discover() -> list[str]:
    files = glob.glob("tests/**/test_*.py", root_dir=REPO, recursive=True)
    return sorted(f.replace("\\", "/") for f in files
                  if "__pycache__" not in f and "_media" not in f)


def _key_for(path: str, fp: dict, env_fp: str) -> str:
    sha = fp["tests"].get(path)
    if sha is None:
        sha = _hash_objects([path]).get(path, "DELETED")
    try:
        with open(os.path.join(REPO, path), encoding="utf-8", errors="replace") as fh:
            sensitive = is_docs_sensitive(fh.read())
    except OSError:
        sensitive = True
    return file_key(fp["code"], env_fp, fp["docs"], sha, sensitive)


def _fmt_counts(c: dict) -> str:
    return " ".join("%s=%d" % kv for kv in sorted((c or {}).items())) or "-"


# 只给导出 golden（门禁 5）用的开关；单测里有断言默认并发数的，不能漏进去。
_GATE0_DROP_ENV = ("CUTORA_COMPOSE_WORKERS",)


def _normalize_env() -> None:
    """手动跑和 pack_internal 里跑要算出同一把钥匙：先把环境摆成一样。"""
    for name in _GATE0_DROP_ENV:
        os.environ.pop(name, None)
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="出包门禁 0（按内容记账的逐文件 pytest）")
    ap.add_argument("files", nargs="*")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--status", action="store_true", help="只列出要跑的文件，不跑")
    ap.add_argument("--no-retry", action="store_true", help="红了不重跑")
    args = ap.parse_args(argv)

    files = [f.replace("\\", "/") for f in args.files] or discover()
    _normalize_env()
    head = _head()
    env_fp = env_fingerprint()
    fp = repo_fingerprints()
    cache = load_cache()

    plan, cached = [], []
    for f in files:
        key = _key_for(f, fp, env_fp)
        rec = cache.get(f, {}).get(key)
        if rec and rec.get("ok"):
            cached.append((f, rec))
        else:
            plan.append((f, key))
    # 以前红过的、从没跑过的先跑，早点看到问题
    plan.sort(key=lambda fk: (0 if any(not r.get("ok") for r in cache.get(fk[0], {}).values())
                              else 1 if fk[0] not in cache else 2, fk[0]))

    print("门禁 0：%d 个测试文件；内容没变、已绿跳过 %d 个；要跑 %d 个（HEAD %s）" % (
        len(files), len(cached), len(plan), head), flush=True)
    if args.status:
        for f, _ in plan:
            print("  要跑 " + f)
        return 0 if not plan else 1

    started = time.strftime("%Y-%m-%d %H:%M:%S")
    results = []          # (file, rec)
    for i, (f, key) in enumerate(plan, 1):
        before = fp
        r = run_pytest([f], args.timeout)
        retried = None
        if not r["ok"] and not args.no_retry:
            targets = r["fails"] if (r["fails"] and not r["note"]) else [f]
            r2 = run_pytest(targets, args.timeout)
            retried = {"targets": targets, "first": {k: r[k] for k in (
                "code", "counts", "fails", "note", "detail", "secs")}}
            counts = r2["counts"]
            if r2["ok"] and targets != [f]:
                # 只重跑了红的那几条：整文件的数字 = 第一次的数字，红的挪到 passed
                counts = dict(r["counts"])
                moved = counts.pop("failed", 0) + counts.pop("error", 0) + counts.pop("errors", 0)
                counts["passed"] = counts.get("passed", 0) + moved
            r = dict(r2, counts=counts)
        after = repo_fingerprints()
        changed = _changed_paths(before, after)
        rec = {"file": f, "key": key, "ok": r["ok"] and not changed, "head": head,
               "time": time.strftime("%Y-%m-%d %H:%M:%S"), "code": r["code"],
               "secs": round(r["secs"], 1), "counts": r["counts"], "fails": r["fails"],
               "note": r["note"], "detail": r["detail"] if not r["ok"] else [],
               "retried": retried}
        if changed:
            rec["note"] = ("跑的时候仓库入库内容变了，不记账：" + ", ".join(changed[:8]))
        _append(rec)
        results.append((f, rec))
        tag = ("绿" if rec["ok"] and not retried else
               "重跑后绿" if rec["ok"] else "红")
        print("[%d/%d] %-52s %s  %s  %.0fs %s" % (
            i, len(plan), os.path.basename(f), tag, _fmt_counts(rec["counts"]),
            rec["secs"], rec["note"]), flush=True)
        fp = after
        if changed:
            # 后面的钥匙按新内容重算
            plan[i:] = [(g, _key_for(g, fp, env_fp)) for g, _ in plan[i:]]

    # 收尾：按「现在」的内容核对一遍，所有文件都有绿记录才算过
    final_fp = repo_fingerprints()
    final_env = env_fingerprint()
    cache = load_cache()
    missing = []
    for f in files:
        rec = cache.get(f, {}).get(_key_for(f, final_fp, final_env))
        if not (rec and rec.get("ok")):
            missing.append(f)
    _write_log(head, started, files, cached, results, missing)
    if missing:
        print("\n门禁 0 没过：%d 个文件在当前内容上没有绿记录。详情：%s" % (len(missing), LOG))
        for f in missing[:30]:
            print("  " + f)
        return 1
    print("\n门禁 0 通过：%d 个文件全部在当前内容上绿（本次跑 %d，跳过 %d）。日志：%s" % (
        len(files), len(results), len(cached), LOG))
    return 0


def _write_log(head, started, files, cached, results, missing) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    flaky = [(f, r) for f, r in results if r["ok"] and r.get("retried")]
    red = [(f, r) for f, r in results if not r["ok"]]
    tmp = LOG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("=== 门禁 0  开始 %s  结束 %s  HEAD=%s\n" % (
            started, time.strftime("%Y-%m-%d %H:%M:%S"), head))
        fh.write("测试文件 %d；内容没变跳过 %d；本次跑 %d；重跑后绿 %d；红 %d；"
                 "收尾核对缺绿记录 %d\n\n" % (len(files), len(cached), len(results),
                                          len(flaky), len(red), len(missing)))
        if flaky:
            fh.write("--- 重跑后绿（第一次红，单独再跑一次过了；反复出现的要查）---\n")
            for f, r in flaky:
                first = r["retried"]["first"]
                fh.write("  %s  第一次：%s %s\n" % (
                    f, first.get("note") or "", " ".join(first.get("fails") or [])))
                for ln in (first.get("detail") or [])[-8:]:
                    fh.write("      " + ln[:300] + "\n")
            fh.write("\n")
        if red:
            fh.write("--- 红 ---\n")
            for f, r in red:
                fh.write("  %s  %s\n" % (f, r.get("note") or ""))
                for t in r.get("fails") or []:
                    fh.write("    FAILED " + t + "\n")
                for ln in r.get("detail") or []:
                    fh.write("      " + ln[:300] + "\n")
            fh.write("\n")
        fh.write("--- 本次跑的文件 ---\n")
        for f, r in results:
            fh.write("  %-60s %s  %s  %.0fs\n" % (
                f, "绿" if r["ok"] else "红", _fmt_counts(r["counts"]), r["secs"]))
        if missing:
            fh.write("\n--- 收尾核对：当前内容上没有绿记录 ---\n")
            for f in missing:
                fh.write("  " + f + "\n")
    os.replace(tmp, LOG)


if __name__ == "__main__":
    sys.exit(main())
