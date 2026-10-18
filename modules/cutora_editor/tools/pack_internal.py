# -*- coding: utf-8 -*-
"""One-command internal pack: PACK_GATE 0–7 + copy + verify + zip.

    runtime\\venv\\Scripts\\python.exe tools/pack_internal.py --version 7.0.0-beta4
    runtime\\venv\\Scripts\\python.exe tools/pack_internal.py --version 7.0.0-beta4 --out D:\\0_Dev\\CUTORA-dist

Any step failure aborts; no zip is written. Do not hardcode model ids —
``cutora.asr.models.packable_model_rows`` is the source of truth.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

DEFAULT_OUT = r"D:\0_Dev\CUTORA-dist"
SYSTEM_PY310 = r"C:\Program Files\Python310"
EXPECTED_FONTS = 240
EXPECTED_FONTS_DIGEST = (
    "80429c0971f116bfb759c58db8db60baa539f3762be8dcd4da46f6b00e510e2f"
)
EXPECTED_LICENSES = 169
FONT_SKIP = {"_quarantine", "_extra", "fonts_pending", "Noto_Color_Emoji"}
FONT_EXTS = {".ttf", ".otf", ".ttc"}
ZIP_STORE_EXTS = {
    ".bin", ".pt", ".pth", ".onnx", ".exe", ".dll", ".pyd", ".so",
    ".zip", ".whl", ".pyd", ".lib",
}

ROOT_FILES = (
    "CUTORA.bat",
    "CUTORA.vbs",
    "CUTORA_console.bat",
    "CUTORA_Install.bat",
    "CUTORA_CLI.bat",
    "CUTORA_CreateShortcut.bat",
    "cutora_mcp.bat",
    "requirements.in",
    "requirements.lock",
    "requirements-dev.in",
    "requirements-dev.lock",
    "README_INTERNAL.md",
    "INTERNAL_USE.txt",
)

DEFAULT_STYLE = {
    "version": 1,
    "style": {
        "pos_x": 548,
        "pos_y": 524,
        "fontsize": 48,
        "font_family": "Arimo",
        "color_text": "#ffffff",
        "color_high": "#ffffff",
        "color_bg": "#000000",
        "color_shadow": "#000000",
        "opacity_bg": 186,
        "opacity_shadow": 100,
        "use_box": True,
        "box_padding_x": 20,
        "box_padding_y": 20,
        "box_radius": 20,
        "box_blur": 0,
        "use_stroke": False,
        "border_width": 2,
        "opacity_outline": 255,
        "color_outline": "#000000",
        "dynamic_box": True,
        "color_high_bg": "#FF0000",
        "opacity_high_bg": 255,
        "dynamic_radius": 12,
        "shadow_blur": 0,
        "rotation": 0,
        "use_underline": False,
        "color_underline": "#000000",
        "high_padding": 5,
        "high_offset_y": 0,
        "wrap_width_percent": 86,
        "wrap_lines": 2,
    },
}

EMPTY_PRESETS = {"version": 1, "presets": []}


class PackError(RuntimeError):
    pass


def assert_venv_ready_marker(root: str | None = None) -> None:
    """Fail the pack unless ``runtime/venv/.cutora_ready`` matches the lock.

    The venv directory is copied as-is, so a marker that says "ready" while
    the hash does not match ``requirements.lock`` would ship a pack that
    skips install and then fails at startup. Missing is the same failure.
    """
    root = os.path.abspath(root or REPO)
    from cutora.core.bootstrap import is_env_ready, ready_marker_path
    marker = ready_marker_path(root)
    if not os.path.isfile(marker):
        raise PackError(
            "ready marker missing: %s — install deps before packing" % marker)
    if not is_env_ready(root):
        raise PackError(
            "ready marker does not match requirements.lock sha256: %s" % marker)


_LOG_FH = None


def _log(msg: str) -> None:
    print(msg, flush=True)
    if _LOG_FH is not None:
        _LOG_FH.write(msg + "\n")
        _LOG_FH.flush()


def _git(args: list[str], timeout: int = 30) -> str:
    r = subprocess.run(
        ["git"] + args,
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if r.returncode != 0:
        raise PackError("git %s failed: %s" % (" ".join(args), r.stderr.strip()))
    return (r.stdout or "").strip()


def _run(cmd: list[str], cwd: str | None = None, env: dict | None = None,
         timeout: int | None = None, capture: bool = False,
         ) -> subprocess.CompletedProcess:
    _log("RUN " + " ".join(cmd))
    kwargs = dict(
        cwd=cwd or REPO, env=env,
        text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )
    if capture:
        kwargs["capture_output"] = True
    return subprocess.run(cmd, **kwargs)


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _rmtree(path: str) -> None:
    if not os.path.exists(path):
        return

    def _onerror(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass

    shutil.rmtree(path, onerror=_onerror)


def _robocopy(src: str, dst: str, extra_xd: list[str] | None = None,
              extra_xf: list[str] | None = None) -> None:
    if not os.path.isdir(src):
        raise PackError("missing source %s" % src)
    os.makedirs(dst, exist_ok=True)
    cmd = [
        "robocopy", src, dst, "/E", "/NFL", "/NDL", "/NJH", "/NJS",
        "/nc", "/ns", "/np",
        "/XD", "__pycache__", ".git", "_diag",
    ]
    for name in extra_xd or []:
        cmd.extend(["/XD", name])
    cmd.extend(["/XF", "*.pyc"])
    for name in extra_xf or []:
        cmd.extend(["/XF", name])
    r = subprocess.run(cmd, cwd=REPO)
    # robocopy: 0–7 success, >=8 failure
    if r.returncode >= 8:
        raise PackError("robocopy failed %s -> %s code=%s" % (
            src, dst, r.returncode))
    _log("COPY_OK %s -> %s code=%s" % (src, dst, r.returncode))


def _venv_python() -> str:
    path = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
    if not os.path.isfile(path):
        raise PackError("missing %s" % path)
    return path


def _fonts_digest(font_dir: str) -> tuple[int, str]:
    files = []
    for root, dirs, names in os.walk(font_dir):
        dirs[:] = [d for d in dirs if d not in FONT_SKIP]
        for fn in names:
            if os.path.splitext(fn)[1].lower() in FONT_EXTS:
                files.append(os.path.join(root, fn))
    files.sort()
    rows = []
    for path in files:
        rel = os.path.relpath(path, font_dir).replace("\\", "/")
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        rows.append("%s %d %s" % (rel, os.path.getsize(path), h.hexdigest()))
    blob = "\n".join(rows) + ("\n" if rows else "")
    return len(files), hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# PACK_GATE 0–7
# ---------------------------------------------------------------------------

def gate_0_pytest(py: str) -> None:
    """Every test file must have a green record on the *current* content.

    ``tools/gate0.py`` runs one process per test file and keys each green
    result by product-code + environment + test-file fingerprints, so files
    already proven green on byte-identical content are not re-run here.
    A first failure gets exactly one retry in a fresh process; the log lists
    every retried file.
    """
    script = os.path.join(REPO, "tools", "gate0.py")
    r = _run([py, script], timeout=None)
    if r.returncode != 0:
        raise PackError("PACK_GATE 0 pytest failed rc=%s (see docs/plan/queue/_log/_gate0.log)"
                        % r.returncode)
    _log("GATE0 pytest OK (gate0.py: every test file green on current content)")


def gate_1_2_boot(py: str) -> None:
    script = os.path.join(REPO, "tools", "t04i1_boot_clicks.py")
    r = _run([py, script], timeout=None)
    if r.returncode != 0:
        raise PackError("PACK_GATE 1–2 boot clicks failed rc=%s" % r.returncode)
    _log("GATE1-2 boot clicks OK")


def gate_3_porcelain() -> dict:
    head = _git(["rev-parse", "HEAD"])
    porcelain = _git(["status", "--porcelain", "--", "tests", "tools"])
    kept = []
    for line in porcelain.splitlines():
        path = line[3:].replace("\\", "/").strip()
        if "/_diag/" in path or path.startswith("tools/_diag/"):
            continue
        if path.endswith(".autosave.json") or path.endswith(".autosave.json.tmp"):
            continue
        kept.append(line)
    if kept:
        raise PackError(
            "PACK_GATE 3 dirty tests/tools:\n%s" % "\n".join(kept))
    _log("GATE3 HEAD=%s porcelain clean" % head)
    return {"head": head, "porcelain": porcelain}


def gate_4_pyside(py: str) -> str:
    r = _run([py, "-c", "from PySide6 import __version__; print(__version__)"],
             timeout=30, capture=True)
    if r.returncode != 0:
        raise PackError("PACK_GATE 4 PySide6 import failed: %s" % (r.stderr or ""))
    lines = [ln for ln in (r.stdout or "").strip().splitlines() if ln.strip()]
    if not lines:
        raise PackError("PACK_GATE 4 PySide6 version empty")
    ver = lines[-1]
    hits = []
    for folder in ("cutora", "tools"):
        root = os.path.join(REPO, folder)
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if d != "__pycache__"]
            for fn in fns:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(dp, fn)
                text = open(path, encoding="utf-8", errors="replace").read()
                for line in text.splitlines():
                    stripped = line.lstrip()
                    if stripped.startswith("import PyQt6") or stripped.startswith("from PyQt6"):
                        hits.append(path)
                        break
    if hits:
        raise PackError("PACK_GATE 4 PyQt6 in: %s" % hits[:8])
    _log("GATE4 PySide6=%s no PyQt6" % ver)
    return ver


def gate_5_golden(py: str) -> None:
    check = os.path.join(REPO, "tools", "golden_check.py")
    r = _run([py, check, "--all", "--repeat", "3"], timeout=None)
    if r.returncode != 0:
        raise PackError("PACK_GATE 5 golden --all --repeat 3 failed")
    r2 = _run([py, check, "--isolate"], timeout=None)
    if r2.returncode != 0:
        raise PackError("PACK_GATE 5 golden --isolate failed")
    _log("GATE5 golden OK")


def gate_6_licenses(stage_or_repo: str) -> tuple[int, str]:
    lic = os.path.join(stage_or_repo, "resources", "licenses")
    n = len([f for f in os.listdir(lic) if os.path.isfile(os.path.join(lic, f))])
    if n != EXPECTED_LICENSES:
        raise PackError("PACK_GATE 6 licenses %d != %d" % (n, EXPECTED_LICENSES))
    third = os.path.join(stage_or_repo, "resources", "THIRD_PARTY_LICENSES.md")
    if not os.path.isfile(third):
        raise PackError("PACK_GATE 6 missing THIRD_PARTY_LICENSES.md")
    count, digest = _fonts_digest(os.path.join(stage_or_repo, "resources", "fonts"))
    if count != EXPECTED_FONTS:
        raise PackError("PACK_GATE 6 fonts count %d != %d" % (count, EXPECTED_FONTS))
    if digest != EXPECTED_FONTS_DIGEST:
        raise PackError("PACK_GATE 6 fonts digest %s != %s" % (
            digest, EXPECTED_FONTS_DIGEST))
    _log("GATE6 licenses=%d fonts=%d digest=%s" % (n, count, digest))
    return count, digest


def gate_7_tag(version: str, head: str) -> str:
    tag = "v" + version if not version.startswith("v") else version
    existing = subprocess.run(
        ["git", "rev-parse", "-q", "--verify", "refs/tags/" + tag],
        cwd=REPO, capture_output=True, text=True, encoding="utf-8")
    if existing.returncode == 0:
        pointed = (existing.stdout or "").strip()
        pointed = _git(["rev-list", "-n", "1", tag])
        if pointed != head:
            raise PackError("PACK_GATE 7 tag %s points at %s not HEAD %s" % (
                tag, pointed, head))
        _log("GATE7 tag %s already at HEAD" % tag)
        return tag
    _git(["tag", tag, head])
    _log("GATE7 created tag %s" % tag)
    return tag


def run_gates(py: str, version: str) -> dict:
    gate_0_pytest(py)
    gate_1_2_boot(py)
    info = gate_3_porcelain()
    pyside = gate_4_pyside(py)
    gate_5_golden(py)
    count, digest = gate_6_licenses(REPO)
    tag = gate_7_tag(version, info["head"])
    return {
        "head": info["head"],
        "pyside": pyside,
        "fonts_count": count,
        "fonts_digest": digest,
        "tag": tag,
    }


# ---------------------------------------------------------------------------
# Copy
# ---------------------------------------------------------------------------

def copy_whitelist(stage: str) -> None:
    assert_venv_ready_marker(REPO)
    _robocopy(os.path.join(REPO, "cutora"), os.path.join(stage, "cutora"))
    _robocopy(
        os.path.join(REPO, "resources"), os.path.join(stage, "resources"),
        extra_xd=["fonts_pending", "_quarantine", "_extra", "_diag"],
        extra_xf=["*.asproj.json"],
    )
    _robocopy(
        os.path.join(REPO, "runtime", "ffmpeg"),
        os.path.join(stage, "runtime", "ffmpeg"),
    )
    _robocopy(
        os.path.join(REPO, "runtime", "venv"),
        os.path.join(stage, "runtime", "venv"),
    )
    _robocopy(os.path.join(REPO, "vendor"), os.path.join(stage, "vendor"))
    for name in ROOT_FILES:
        src = os.path.join(REPO, name)
        if not os.path.isfile(src):
            raise PackError("missing required %s" % name)
        shutil.copy2(src, os.path.join(stage, name))
    gate_md = os.path.join(REPO, "docs", "plan", "phase1_tasks", "PACK_GATE.md")
    shutil.copy2(gate_md, os.path.join(stage, "PACK_GATE.md"))


def copy_models(stage: str) -> list[dict]:
    from cutora.asr.models import (
        PACK_FIXTURE_DIRNAMES,
        iter_files,
        models_dir,
        pack_model_directories,
        packable_model_rows,
        required_pack_dirnames,
        sha256_file,
        verify_weight,
    )
    src_root = models_dir()
    dst_root = os.path.join(stage, "runtime", "models")
    os.makedirs(dst_root, exist_ok=True)
    required = required_pack_dirnames()
    dirs = pack_model_directories()
    _log("MODELS required=%s copy=%s" % (required, dirs))
    for leaf in required:
        if leaf not in dirs:
            raise PackError("required model dirname not packable: %s" % leaf)
        src = os.path.join(src_root, leaf)
        if not os.path.isdir(src):
            raise PackError("required model dir missing on disk: %s" % src)
    manifest = []
    copied = set()
    for leaf in dirs:
        if leaf in PACK_FIXTURE_DIRNAMES:
            raise PackError("refusing to pack fixture %s" % leaf)
        src = os.path.join(src_root, leaf)
        dst = os.path.join(dst_root, leaf)
        if not os.path.isdir(src):
            raise PackError("model dir missing: %s" % src)
        _robocopy(src, dst, extra_xd=[".cache", "huggingface", "asr_fake"])
        copied.add(leaf)
    # pin files (models.json / align_models.json) travel with resources/
    rows = packable_model_rows()
    for row in rows:
        leaf = str(row.get("dirname") or row.get("id"))
        folder = os.path.join(dst_root, leaf)
        files_out = []
        for item in iter_files(row):
            name = str(item.get("filename") or "")
            want = str(item.get("sha256") or "").strip()
            if not name or not want:
                continue
            path = os.path.join(folder, name)
            status = verify_weight(path, want)
            got = sha256_file(path) if os.path.isfile(path) else ""
            rec = {
                "id": row.get("id"),
                "filename": name,
                "expected": want,
                "got": got,
                "status": status,
                "bytes": os.path.getsize(path) if os.path.isfile(path) else 0,
            }
            files_out.append(rec)
            _log("HASH %s %s %s" % (row.get("id"), name, status))
            if status != "ready":
                raise PackError("sha256 mismatch %s/%s expected=%s got=%s" % (
                    leaf, name, want, got))
        manifest.append({
            "id": row.get("id"),
            "dirname": leaf,
            "kind": row.get("kind"),
            "files": files_out,
        })
    # extra files in copied dirs (e.g. align pytorch_model.bin) — record hash
    for leaf in copied:
        folder = os.path.join(dst_root, leaf)
        pinned = set()
        for row in rows:
            if str(row.get("dirname") or row.get("id")) == leaf:
                for item in iter_files(row):
                    pinned.add(str(item.get("filename") or ""))
        extras = []
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames[:] = [d for d in dirnames
                           if d not in ("__pycache__", ".cache", "huggingface")]
            for fn in filenames:
                rel = os.path.relpath(os.path.join(dirpath, fn), folder)
                rel = rel.replace("\\", "/")
                if rel in pinned:
                    continue
                extras.append({
                    "filename": rel,
                    "sha256": sha256_file(os.path.join(dirpath, fn)),
                    "bytes": os.path.getsize(os.path.join(dirpath, fn)),
                    "source": "computed-unpinned",
                })
        if extras:
            names = [e["filename"] for e in extras]
            raise PackError(
                "unpinned extra files in %s: %s — claim them in the catalog "
                "or delete them. Zero unpinned." % (leaf, names))
    src_paths = os.path.join(src_root, "model_paths.json")
    if os.path.isfile(src_paths):
        shutil.copy2(src_paths, os.path.join(dst_root, "model_paths.json"))
    fake = os.path.join(dst_root, "asr_fake")
    if os.path.exists(fake):
        raise PackError("asr_fake leaked into pack")
    cache = os.path.join(dst_root, ".cache")
    if os.path.exists(cache):
        raise PackError(".cache leaked into pack")
    return manifest


def copy_python(stage: str) -> None:
    src = SYSTEM_PY310
    dst = os.path.join(stage, "runtime", "python")
    if not os.path.isfile(os.path.join(src, "python.exe")):
        raise PackError("system Python 3.10 missing: %s" % src)
    _robocopy(src, dst)
    exe = os.path.join(dst, "python.exe")
    if not os.path.isfile(exe):
        raise PackError("bundled python.exe missing after copy")
    lic = os.path.join(dst, "LICENSE.txt")
    if not os.path.isfile(lic):
        raise PackError("bundled PSF LICENSE.txt missing")
    _log("PYTHON copied to %s" % dst)


def write_userdata(stage: str) -> None:
    root = os.path.join(stage, "UserData")
    for rel in ("settings", "presets", "presets/stylepacks", "logs",
                "cache", "autosave"):
        os.makedirs(os.path.join(root, rel.replace("/", os.sep)), exist_ok=True)
    style_path = os.path.join(root, "settings", "default_style.json")
    with open(style_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(DEFAULT_STYLE, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    presets = os.path.join(root, "presets", "text_presets.json")
    with open(presets, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(EMPTY_PRESETS, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    banned = (
        "recent_projects.json", "project_queue.json", "commands.jsonl",
        "export_state.json", "app_settings.json",
    )
    for name in banned:
        for dirpath, _, filenames in os.walk(root):
            if name in filenames:
                raise PackError("UserData contains %s" % os.path.join(dirpath, name))
    for dirpath, _, filenames in os.walk(os.path.join(root, "cache")):
        if filenames:
            raise PackError("UserData/cache not empty: %s" % dirpath)
    for dirpath, _, filenames in os.walk(os.path.join(root, "logs")):
        if filenames:
            raise PackError("UserData/logs not empty: %s" % dirpath)


def write_portable_flag(stage: str, version: str, head: str) -> None:
    path = os.path.join(stage, "portable.flag")
    with open(path, "w", encoding="ascii", newline="\n") as fh:
        fh.write("v%s %s\n" % (version, head))


def write_internal_build_txt(stage: str) -> None:
    path = os.path.join(stage, "INTERNAL_BUILD.txt")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("内部测试版，禁止再分发\n")
    _log("WROTE " + path)


def assert_internal_not_gpl_claim(stage: str) -> None:
    """Internal pack: INTERNAL_USE.txt, no GPL LICENSE claim."""
    for name in ("LICENSE", "LICENSE.md", "COPYING"):
        p = os.path.join(stage, name)
        if os.path.isfile(p):
            raise PackError("internal pack must not include %s" % name)
    use = os.path.join(stage, "INTERNAL_USE.txt")
    if not os.path.isfile(use):
        raise PackError("missing INTERNAL_USE.txt")
    if "内部使用，不对外分发" not in open(use, encoding="utf-8").read():
        raise PackError("INTERNAL_USE.txt missing 内部使用，不对外分发")
    marker = os.path.join(stage, "INTERNAL_BUILD.txt")
    if not os.path.isfile(marker):
        raise PackError("missing INTERNAL_BUILD.txt")
    text = open(marker, encoding="utf-8").read()
    if "内部测试版，禁止再分发" not in text:
        raise PackError("INTERNAL_BUILD.txt missing 内部测试版，禁止再分发")
    hits = []
    for dirpath, dirnames, filenames in os.walk(stage):
        dirnames[:] = [d for d in dirnames if d not in (
            "site-packages", "Lib", "licenses", "__pycache__")]
        if "site-packages" in dirpath.replace("\\", "/"):
            continue
        for fn in filenames:
            if not fn.lower().endswith((".md", ".txt")):
                continue
            full = os.path.join(dirpath, fn)
            try:
                blob = open(full, encoding="utf-8", errors="replace").read(20000)
            except OSError:
                continue
            if "GPL 兼容" in blob:
                hits.append(os.path.relpath(full, stage))
    if hits:
        raise PackError("internal pack still says GPL 兼容: %s" % hits[:8])


def write_build_txt(stage: str, meta: dict) -> None:
    lines = [
        "CUTORA %s internal" % meta["version"],
        "git: %s" % meta["head"],
        "tag: %s" % meta["tag"],
        "PySide6: %s" % meta["pyside"],
        "pack: internal (gates 0-7). Not an external-distribution build.",
        "utc: %s" % meta["utc"],
        "fonts_count: %s" % meta["fonts_count"],
        "fonts_digest: %s" % meta["fonts_digest"],
        "python: runtime/python (CPython 3.10.11, PSF)",
        "models:",
    ]
    for row in meta.get("models") or []:
        lines.append("  - %s  dirname=%s" % (row.get("id"), row.get("dirname")))
        for item in row.get("files") or []:
            lines.append("      %s  sha256=%s  bytes=%s" % (
                item.get("filename"), item.get("got"), item.get("bytes")))
        for item in row.get("extra_files") or []:
            lines.append("      %s  sha256=%s  bytes=%s  (unpinned extra)" % (
                item.get("filename"), item.get("sha256"), item.get("bytes")))
    path = os.path.join(stage, "BUILD.txt")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    _log("WROTE " + path)


def _stripped_env(keep_python_dirs: list[str]) -> dict:
    env = dict(os.environ)
    keep_abs = {os.path.normcase(os.path.abspath(p)) for p in keep_python_dirs}
    parts = []
    for p in env.get("PATH", "").split(os.pathsep):
        if not p:
            continue
        exe = os.path.join(p, "python.exe")
        if os.path.isfile(exe):
            if os.path.normcase(os.path.abspath(p)) not in keep_abs:
                continue
        parts.append(p)
    env["PATH"] = os.pathsep.join(parts)
    env.pop("PYTHONHOME", None)
    env["PYTHONPATH"] = ""
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _venv_alive(stage: str, env: dict) -> tuple[bool, str]:
    exe = os.path.join(stage, "runtime", "venv", "Scripts", "python.exe")
    r = subprocess.run(
        [exe, "-c", "import sys; print(sys.version)"],
        cwd=stage, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    return r.returncode == 0, out


def selftest_portable_and_headless(stage: str) -> None:
    from cutora.core.portable import read_pyvenv_home, rewrite_pyvenv_cfg
    cfg = os.path.join(stage, "runtime", "venv", "pyvenv.cfg")
    bundled = os.path.join(stage, "runtime", "python")
    venv_scripts = os.path.join(stage, "runtime", "venv", "Scripts")
    env = _stripped_env([venv_scripts, bundled])

    rewrite_pyvenv_cfg(
        stage, home=r"C:\CUTORA_PYHOME_MUST_NOT_EXIST",
        require_home_exists=False)
    ok, out = _venv_alive(stage, env)
    _log("SELFTEST wrong-home alive=%s out=%s" % (ok, out[:300]))
    if ok:
        raise PackError(
            "venv still started with a bogus pyvenv.cfg home — rewrite proof failed")

    home = rewrite_pyvenv_cfg(stage)
    got = read_pyvenv_home(cfg)
    if os.path.normcase(os.path.abspath(got or "")) != os.path.normcase(home):
        raise PackError("pyvenv.cfg home not rewritten: %s" % got)
    ok, out = _venv_alive(stage, env)
    _log("SELFTEST rewritten-home alive=%s out=%s" % (ok, out[:300]))
    if not ok:
        raise PackError("venv failed after pyvenv.cfg rewrite: %s" % out)

    venv_py = os.path.join(stage, "runtime", "venv", "Scripts", "python.exe")

    def _child(cmd, env_child, label, timeout=180):
        try:
            r = subprocess.run(
                cmd, cwd=stage, env=env_child, timeout=timeout,
                capture_output=True, text=True, encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired as exc:
            raise PackError("%s timed out: %s" % (label, exc)) from exc
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        for line in out.splitlines():
            _log("  %s" % line)
        if r.returncode != 0:
            raise PackError("%s failed rc=%s\n%s" % (label, r.returncode, out[-1500:]))
        return out

    _child([venv_py, "-m", "cutora", "--selftest"], env, "headless selftest")
    _log("SELFTEST headless OK")

    env_cpu = dict(env)
    env_cpu["CUDA_VISIBLE_DEVICES"] = ""
    cpu_code = (
        "from cutora.core.config import detect_gpu_config\n"
        "d, ct, reason = detect_gpu_config(verbose=False)\n"
        "assert d == 'cpu', (d, ct, reason)\n"
        "print('CPU_FALLBACK_OK', reason)\n"
    )
    _child([venv_py, "-c", cpu_code], env_cpu, "CPU fallback", timeout=120)
    _log("SELFTEST CPU fallback OK")


def zip_stage(stage: str, zip_path: str) -> None:
    if os.path.isfile(zip_path):
        os.remove(zip_path)
    parent = os.path.dirname(stage)
    leaf = os.path.basename(stage)
    _log("ZIP %s" % zip_path)
    with zipfile.ZipFile(zip_path, "w", allowZip64=True) as zf:
        for dirpath, dirnames, filenames in os.walk(stage):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for fn in filenames:
                if fn.endswith(".pyc"):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.join(leaf, os.path.relpath(full, stage))
                ext = os.path.splitext(fn)[1].lower()
                if ext in ZIP_STORE_EXTS or os.path.getsize(full) > 8 * 1024 * 1024:
                    zf.write(full, rel, compress_type=zipfile.ZIP_STORED)
                else:
                    zf.write(full, rel, compress_type=zipfile.ZIP_DEFLATED)
    _log("ZIP_OK %s" % zip_path)


def write_sha256(zip_path: str) -> str:
    digest = _sha256_file(zip_path)
    side = zip_path + ".sha256"
    with open(side, "w", encoding="ascii", newline="\n") as fh:
        fh.write("%s  %s\n" % (digest, os.path.basename(zip_path)))
    return digest


def _assert_no_leaks(stage: str) -> None:
    banned_names = {
        "CUTORA_trace.bat", "tests", "docs", "tools", "track", "build",
        ".git", "user_data",
    }
    for name in banned_names:
        p = os.path.join(stage, name)
        if os.path.exists(p):
            raise PackError("banned path in pack: %s" % name)
    for dirpath, dirnames, filenames in os.walk(stage):
        if "fonts_pending" in dirnames:
            raise PackError("fonts_pending leaked")
        if "_diag" in dirnames:
            raise PackError("_diag leaked")
        for fn in filenames:
            if fn.endswith(".asproj.json"):
                raise PackError("project file leaked: %s" % fn)
            if fn == "CUTORA_trace.bat":
                raise PackError("CUTORA_trace.bat leaked")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--skip-gates", action="store_true",
                        help="dev only; CP4 / release packs must not pass this")
    parser.add_argument("--resume", action="store_true",
                        help="reuse existing stage; recopy cutora/ then selftest+zip")
    args = parser.parse_args(argv)
    version = args.version.strip()
    if not version:
        raise PackError("empty --version")
    assert_venv_ready_marker(REPO)
    py = _venv_python()
    t0 = time.time()
    utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(args.out, exist_ok=True)
    global _LOG_FH
    log_path = os.path.join(args.out, "pack_%s.log" % version)
    _LOG_FH = open(
        log_path, "a" if args.resume else "w", encoding="utf-8", newline="\n")
    _log("PACK start version=%s out=%s utc=%s log=%s" % (
        version, args.out, utc, log_path))

    if args.skip_gates:
        _log("SKIP_GATES (dev). Release packs must run gates 0–7.")
        head = _git(["rev-parse", "HEAD"])
        pyside = gate_4_pyside(py)
        count, digest = _fonts_digest(os.path.join(REPO, "resources", "fonts"))
        tag = "v" + version if not version.startswith("v") else version
        gate_info = {
            "head": head, "pyside": pyside,
            "fonts_count": count, "fonts_digest": digest, "tag": tag,
        }
    else:
        gate_info = run_gates(py, version)

    from cutora.asr.models import (
        ALIGN_NOT_BUNDLED_NOTE,
        align_plan_for_complete_languages,
        packable_model_rows,
    )
    plan = align_plan_for_complete_languages()
    missing = [p for p in plan if not p.get("bundled")]
    _log("ALIGN_PLAN bundled=%d missing=%d note=%s" % (
        sum(1 for p in plan if p.get("bundled")),
        len(missing), ALIGN_NOT_BUNDLED_NOTE))
    for p in plan:
        _log("  align %s  id=%s  bundled=%s  repo=%s" % (
            p["code"], p.get("align_id"), p.get("bundled"), p.get("repo")))

    name = "CUTORA-%s" % version
    stage = os.path.join(args.out, name)
    zip_path = os.path.join(args.out, "%s-internal.zip" % name)
    _log("STAGE %s" % stage)
    if args.resume:
        if not os.path.isdir(stage):
            raise PackError("--resume but stage missing: %s" % stage)
        _log("RESUME recopy cutora/ then selftest+zip")
        _robocopy(os.path.join(REPO, "cutora"), os.path.join(stage, "cutora"))
        for name in ("CUTORA.bat", "CUTORA.vbs", "CUTORA_console.bat",
                     "CUTORA_CLI.bat", "CUTORA_Install.bat",
                     "CUTORA_CreateShortcut.bat",
                     "cutora_mcp.bat", "README_INTERNAL.md"):
            src = os.path.join(REPO, name)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(stage, name))
        write_internal_build_txt(stage)
        assert_internal_not_gpl_claim(stage)
    else:
        _rmtree(stage)
        os.makedirs(stage, exist_ok=True)
        copy_whitelist(stage)
        models = copy_models(stage)
        copy_python(stage)
        write_userdata(stage)
        write_portable_flag(stage, version, gate_info["head"])
        count, digest = gate_6_licenses(stage)
        gate_info["fonts_count"] = count
        gate_info["fonts_digest"] = digest
        write_build_txt(stage, {
            "version": version,
            "head": gate_info["head"],
            "tag": gate_info["tag"],
            "pyside": gate_info["pyside"],
            "utc": utc,
            "fonts_count": count,
            "fonts_digest": digest,
            "models": models,
            "packable": [r.get("id") for r in packable_model_rows()],
        })
        write_internal_build_txt(stage)
        assert_internal_not_gpl_claim(stage)

    _assert_no_leaks(stage)
    selftest_portable_and_headless(stage)
    zip_stage(stage, zip_path)
    digest_zip = write_sha256(zip_path)
    size = os.path.getsize(zip_path)
    elapsed = time.time() - t0
    _log("PACK_DONE")
    _log("ZIP_PATH %s" % zip_path)
    _log("ZIP_SHA256 %s" % digest_zip)
    _log("ZIP_BYTES %s" % size)
    _log("STAGE %s" % stage)
    _log("ELAPSED_SEC %.1f" % elapsed)
    print("")
    print("zip:    %s" % zip_path)
    print("sha256: %s" % digest_zip)
    print("bytes:  %s (%.2f GB)" % (size, size / (1024 ** 3)))
    print("stage:  %s" % stage)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PackError as exc:
        sys.stderr.write("PACK_FAIL %s\n" % exc)
        _log("PACK_FAIL %s" % exc)
        raise SystemExit(1)
    finally:
        if _LOG_FH is not None:
            _LOG_FH.close()
