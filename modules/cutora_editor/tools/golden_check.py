#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Compare CUTORA renders against frozen golden expected.mp4 hashes.

Usage:
    python tools/golden_check.py --all
    python tools/golden_check.py --case g02_vertical_fx
    python tools/golden_check.py --all --repeat 3
    python tools/golden_check.py --all --allow-dirty
    python tools/golden_check.py --isolate
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN_ROOT = os.path.join(REPO_ROOT, "tests", "golden")
# Must match record scripts. The Windows DirectWrite plugin exposes variable-font
# named instances as extra families ("SN Pro SemiBold"), which makes
# resolve_font_request take the exact path instead of split+setStyleName.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
GOLDEN_FONTS_DIGEST = os.path.join(GOLDEN_ROOT, "fonts_digest.txt")
FFMPEG_DIR = os.path.join(REPO_ROOT, "runtime", "ffmpeg")
VENV_PY = os.path.join(REPO_ROOT, "runtime", "venv", "Scripts", "python.exe")
FIXTURE_USER_DATA = os.path.join(GOLDEN_ROOT, "_fixture_user_data")
CUTORA_USER_DATA_ENV = "CUTORA_USER_DATA"

NONDET_ROOT = os.path.join(REPO_ROOT, "docs", "plan", "reports", "nondeterminism")
_FIXTURE_TMP = None
_FINGERPRINT_TEXT = ""

DIRTY_BANNER = "DIRTY TREE — 本次结果不代表任何提交"

# Pixel-affecting paths. Untracked reports/build noise must not trip this.
_SOURCE_DIRTY_PREFIXES = (
    "cutora/",
    "tests/golden/",
    "resources/fonts",
    "resources/stylepacks",
    "tools/golden_check.py",
    "tools/golden_gen.py",
)

# Keep in sync with cutora.ui.panels.fonts._iter_font_files skip_dir_names.
_FONT_SCAN_SKIP_DIRS = {"_quarantine", "_extra", "fonts_pending", "Noto_Color_Emoji"}
_FONT_SCAN_EXTS = {".ttf", ".otf", ".ttc"}


def _git(args: list[str]) -> str:
    try:
        r = subprocess.run(
            ["git"] + args,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        return (r.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _porcelain_path(line: str) -> str:
    if len(line) >= 3 and line[2] == " ":
        raw = line[3:]
    elif len(line) >= 2:
        raw = line[2:].lstrip()
    else:
        raw = line
    if " -> " in raw:
        raw = raw.split(" -> ", 1)[-1]
    return raw.strip().replace("\\", "/").strip('"')


def is_source_dirty_path(path: str) -> bool:
    norm = (path or "").replace("\\", "/").lstrip("./")
    for prefix in _SOURCE_DIRTY_PREFIXES:
        if norm == prefix.rstrip("/") or norm.startswith(prefix):
            return True
    return False


def _iter_embedded_font_files() -> list[str]:
    font_dir = os.path.join(REPO_ROOT, "resources", "fonts")
    if not os.path.isdir(font_dir):
        return []
    out = []
    for root, dirs, filenames in os.walk(font_dir):
        dirs[:] = [d for d in dirs if d not in _FONT_SCAN_SKIP_DIRS]
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in _FONT_SCAN_EXTS:
                out.append(os.path.join(root, fn))
    out.sort()
    return out


def _fonts_digest(paths: list[str] | None = None) -> tuple[int, str]:
    """Return (count, sha256) over loaded font files: relpath + size + content hash."""
    if paths is None:
        paths = _iter_embedded_font_files()
    rows = []
    font_root = os.path.join(REPO_ROOT, "resources", "fonts")
    for path in paths:
        rel = os.path.relpath(path, font_root).replace("\\", "/")
        try:
            size = os.path.getsize(path)
            h = hashlib.sha256()
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    h.update(chunk)
            digest = h.hexdigest()
        except OSError as e:
            size = -1
            digest = "error:%s" % e
        rows.append("%s %d %s" % (rel, size, digest))
    rows.sort()
    blob = "\n".join(rows) + ("\n" if rows else "")
    return len(paths), hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _read_recorded_fonts_digest() -> str:
    if not os.path.isfile(GOLDEN_FONTS_DIGEST):
        return ""
    recorded = ""
    with open(GOLDEN_FONTS_DIGEST, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if len(line) == 64 and all(c in "0123456789abcdef" for c in line.lower()):
                recorded = line.lower()
    return recorded


def _pyside6_version() -> str:
    """Package version without importing Qt (MSVCP140 order)."""
    try:
        import importlib.metadata as md
        return md.version("PySide6")
    except Exception as e:
        return "unimported: %s" % e


def collect_fingerprint(qt_version: str | None = None) -> dict:
    head = _git(["rev-parse", "HEAD"]) or "(no git)"
    porcelain = [ln for ln in _git(["status", "--porcelain"]).splitlines() if ln.strip()]
    dirty_files = [ln for ln in porcelain if is_source_dirty_path(_porcelain_path(ln))]
    ffmpeg_bin = os.path.join(FFMPEG_DIR, "ffmpeg.exe")
    if not os.path.isfile(ffmpeg_bin):
        ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
    ffmpeg_line = ""
    try:
        r = subprocess.run(
            [ffmpeg_bin, "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        ffmpeg_line = ((r.stdout or r.stderr or "").splitlines() or [""])[0]
    except (OSError, subprocess.SubprocessError) as e:
        ffmpeg_line = "ffmpeg error: %s" % e

    cv2_ver = numpy_ver = threads = "unimported"
    try:
        import cv2
        import numpy
        cv2_ver = getattr(cv2, "__version__", "?")
        numpy_ver = getattr(numpy, "__version__", "?")
        threads = str(cv2.getNumThreads())
    except Exception as e:
        cv2_ver = "error: %s" % e

    cutora_env = {k: os.environ[k] for k in sorted(os.environ) if k.startswith("CUTORA_")}
    fonts_count, fonts_digest = _fonts_digest()
    recorded = _read_recorded_fonts_digest()
    return {
        "head": head,
        "porcelain": porcelain,
        "dirty_files": dirty_files,
        "source_dirty": bool(dirty_files),
        "ffmpeg": ffmpeg_line,
        "cv2": cv2_ver,
        "numpy": numpy_ver,
        "cv2_threads": threads,
        "pyside6": qt_version or _pyside6_version(),
        "cutora_env": cutora_env,
        "fonts_count": fonts_count,
        "fonts_digest": fonts_digest,
        "fonts_digest_recorded": recorded,
    }


def format_fingerprint(fp: dict) -> str:
    lines = [
        "[golden-fingerprint]",
        "HEAD=%s" % fp.get("head", ""),
        "source_dirty=%s" % ("yes" if fp.get("source_dirty") else "no"),
        "ffmpeg=%s" % fp.get("ffmpeg", ""),
        "cv2=%s" % fp.get("cv2", ""),
        "numpy=%s" % fp.get("numpy", ""),
        "PySide6=%s" % fp.get("pyside6", ""),
        "cv2.getNumThreads()=%s" % fp.get("cv2_threads", ""),
        "fonts_count=%s" % fp.get("fonts_count", ""),
        "fonts_digest=%s" % fp.get("fonts_digest", ""),
    ]
    recorded = str(fp.get("fonts_digest_recorded") or "")
    current = str(fp.get("fonts_digest") or "")
    if recorded and current and recorded != current:
        lines.append(
            "FONT SET CHANGED — baselines recorded with %s, current %s"
            % (recorded, current)
        )
    env = fp.get("cutora_env") or {}
    if env:
        for k, v in env.items():
            lines.append("%s=%s" % (k, v))
    else:
        lines.append("CUTORA_*=(none)")
    porcelain = fp.get("porcelain") or []
    dirty = fp.get("dirty_files") or []
    lines.append("git status --porcelain: %d lines" % len(porcelain))
    lines.append("source-dirty files (%d):" % len(dirty))
    if dirty:
        lines.extend("  %s" % ln for ln in dirty)
    else:
        lines.append("  (none)")
    other = [ln for ln in porcelain if ln not in dirty]
    if other:
        buckets: dict[str, int] = {}
        for ln in other:
            path = _porcelain_path(ln)
            top = path.split("/")[0] if path else "?"
            buckets[top] = buckets.get(top, 0) + 1
        lines.append("other porcelain by top-level (%d):" % len(other))
        for k in sorted(buckets):
            lines.append("  %s: %d" % (k, buckets[k]))
    elif not porcelain:
        lines.append("  (clean)")
    return "\n".join(lines) + "\n"


def print_fingerprint(fp: dict, *, banner: bool) -> None:
    global _FINGERPRINT_TEXT
    _FINGERPRINT_TEXT = format_fingerprint(fp)
    if banner:
        print("=" * 72)
        print(DIRTY_BANNER)
        print("=" * 72)
    sys.stdout.write(_FINGERPRINT_TEXT)
    sys.stdout.flush()


def write_fingerprint_file(dest_dir: str) -> None:
    if not _FINGERPRINT_TEXT:
        return
    path = os.path.join(dest_dir, "fingerprint.txt")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        if DIRTY_BANNER in (_FINGERPRINT_TEXT or "") or "source_dirty=yes" in _FINGERPRINT_TEXT:
            f.write(DIRTY_BANNER + "\n")
        f.write(_FINGERPRINT_TEXT)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def load_manifest(case_dir: str) -> dict:
    path = os.path.join(case_dir, "manifest.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def pin_golden_user_data() -> str:
    """Point USER_DATA at a temp copy of the versioned golden fixture.

    Copying (instead of using the tracked directory in-place) keeps
    ``tests/golden/_fixture_user_data/app_settings.json`` immutable if a
    render path writes settings. Subprocesses inherit ``CUTORA_USER_DATA``.
    """
    global _FIXTURE_TMP
    src = os.path.join(FIXTURE_USER_DATA, "app_settings.json")
    if not os.path.isfile(src):
        raise RuntimeError("missing golden USER_DATA fixture: %s" % src)
    _FIXTURE_TMP = tempfile.mkdtemp(prefix="golden_ud_")
    shutil.copy2(src, os.path.join(_FIXTURE_TMP, "app_settings.json"))
    # Product default is -14 LUFS. Golden pins off in the copy — never
    # rely on the product default to keep hashes still. Inline JSON so
    # this runs before sys.path includes the repo (no import cutora).
    pinned = os.path.join(_FIXTURE_TMP, "app_settings.json")
    with open(pinned, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        data = {}
    data["loudness_mode"] = "off"
    with open(pinned, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    target = os.path.abspath(_FIXTURE_TMP)
    os.environ[CUTORA_USER_DATA_ENV] = target
    paths_mod = sys.modules.get("cutora.core.paths")
    if paths_mod is not None:
        paths_mod.USER_DATA = target
    return target


def prepare_env() -> None:
    os.chdir(REPO_ROOT)
    path = os.environ.get("PATH") or ""
    parts = [os.path.normcase(p) for p in path.split(os.pathsep)]
    extra = [FFMPEG_DIR]
    for d in reversed(extra):
        if os.path.normcase(d) not in parts:
            path = d + os.pathsep + path
            parts.insert(0, os.path.normcase(d))
    os.environ["PATH"] = path
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    pin_golden_user_data()


def boot_host():
    prepare_env()
    from PySide6.QtWidgets import QApplication
    from cutora.ui import main_window as host

    app = QApplication.instance()
    alive = False
    if app is not None:
        try:
            from shiboken6 import isValid
            alive = bool(isValid(app))
        except Exception:
            try:
                app.applicationName()
                alive = True
            except RuntimeError:
                alive = False
    if not alive:
        app = QApplication([])
    if not host.load_embedded_fonts():
        from cutora.ui.panels.fonts import FONT_DIR, LAST_EMBEDDED_FONT_LOAD_COUNT
        raise RuntimeError(
            "font load failed loaded=%s font_dir=%s app_rebuilt=%s"
            % (LAST_EMBEDDED_FONT_LOAD_COUNT, FONT_DIR, not alive))
    return host, app


def load_tasks(host, project_path: str):
    from cutora.batch import cli as batch_cli
    data, tasks = batch_cli.load_project_file(host, project_path)
    return data, tasks


def render_serial(host, tasks, out_dir: str, mode: str, use_gpu: bool):
    os.makedirs(out_dir, exist_ok=True)
    outs = []
    for i, task in enumerate(tasks):
        outp = os.path.join(out_dir, "out_%02d.mp4" % i)
        err = []
        worker = host.RenderWorker(task, outp, mode, bool(use_gpu), export_logs=False)
        worker.error.connect(lambda m: err.append(m))
        worker.run()
        if err or not os.path.isfile(outp):
            raise RuntimeError("serial render failed item %d: %s" % (i, err[:1] or "no file"))
        outs.append(outp)
    return outs


def render_parallel(host, tasks, out_dir: str, mode: str, use_gpu: bool, workers: int):
    from PySide6.QtCore import QEventLoop

    os.makedirs(out_dir, exist_ok=True)
    jobs = []
    for i, task in enumerate(tasks):
        outp = os.path.join(out_dir, "out_%02d.mp4" % i)
        jobs.append((task, outp))
    mgr = host.ParallelExportManager(
        jobs,
        mode=mode,
        use_gpu=bool(use_gpu),
        export_mode=getattr(host, "EXPORT_VIDEO_MODE", "compressed"),
        workers=max(1, int(workers)),
        parent=None,
        gpu_workers=0,
        enc_threads=0,
    )
    done_holder = []
    loop = QEventLoop()

    def _on_finished(idxs):
        done_holder.append(list(idxs or []))
        loop.quit()

    mgr.finished.connect(_on_finished)
    mgr.start()
    if not done_holder:
        loop.exec()
    failed = done_holder[-1] if done_holder else list(range(len(jobs)))
    if failed:
        raise RuntimeError("parallel render failed indices: %s" % failed)
    return [p for _t, p in jobs]


def save_nondeterminism(case_name: str, produced, exp_files, repeat_idx: int) -> str:
    """Copy this run's outputs plus expected files for Claude to inspect."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = "%s_r%02d_%s" % (case_name, int(repeat_idx), ts)
    dest = os.path.join(NONDET_ROOT, folder)
    os.makedirs(dest, exist_ok=True)
    for p in produced or []:
        if p and os.path.isfile(p):
            shutil.copy2(p, os.path.join(dest, "got_" + os.path.basename(p)))
    for p in exp_files or []:
        if p and os.path.isfile(p):
            shutil.copy2(p, os.path.join(dest, "expected_" + os.path.basename(p)))
    write_fingerprint_file(dest)
    print("[golden] saved mismatch dump: %s" % dest)
    return dest


def expected_paths(case_dir: str, manifest: dict):
    names = manifest.get("expected") or ["expected.mp4"]
    paths = []
    for name in names:
        p = name if os.path.isabs(name) else os.path.join(case_dir, name)
        paths.append(p)
    return paths


def read_expected_sha(case_dir: str) -> dict:
    path = os.path.join(case_dir, "expected.sha256")
    mapping = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                mapping[parts[-1].lstrip("*")] = parts[0]
            elif len(parts) == 1:
                mapping["expected.mp4"] = parts[0]
    return mapping


def check_case(host, case_name: str, repeat_idx: int = 1, dump_on_fail: bool = False) -> dict:
    case_dir = os.path.join(GOLDEN_ROOT, case_name)
    manifest = load_manifest(case_dir)
    project = os.path.join(case_dir, manifest.get("project", "project.asproj.json"))
    mode = manifest.get("mode") or "quality"
    use_gpu = bool(manifest.get("gpu", False))
    workers = int(manifest.get("workers") or 1)
    verify_parallel = bool(manifest.get("verify_parallel_eq_serial", False))

    data, tasks = load_tasks(host, project)
    if not tasks:
        raise RuntimeError("no tasks in %s" % project)
    reset_case_caches()

    tmp = tempfile.mkdtemp(prefix="golden_%s_" % case_name)
    produced = []
    exp_files = []
    try:
        if workers > 1 and len(tasks) > 1:
            produced = render_parallel(host, tasks, os.path.join(tmp, "par"), mode, use_gpu, workers)
        else:
            produced = render_serial(host, tasks, os.path.join(tmp, "ser"), mode, use_gpu)

        exp_files = expected_paths(case_dir, manifest)
        sha_map = read_expected_sha(case_dir)
        rows = []
        ok = True
        if len(produced) != len(exp_files):
            ok = False
            rows.append({
                "file": "(count)",
                "status": "FAIL",
                "detail": "produced %d expected %d" % (len(produced), len(exp_files)),
            })
        for i, (got, exp) in enumerate(zip(produced, exp_files)):
            got_hash = sha256_file(got)
            exp_name = os.path.basename(exp)
            exp_hash = sha_map.get(exp_name) or sha_map.get(os.path.relpath(exp, case_dir).replace("\\", "/"))
            if not exp_hash and os.path.isfile(exp):
                exp_hash = sha256_file(exp)
            match = bool(exp_hash) and got_hash.lower() == exp_hash.lower()
            if not match:
                ok = False
            rows.append({
                "file": exp_name,
                "status": "PASS" if match else "FAIL",
                "got": got_hash,
                "expected": exp_hash or "(missing)",
            })

        if verify_parallel:
            ser = render_serial(host, tasks, os.path.join(tmp, "ser_cmp"), mode, use_gpu)
            par_ok = True
            for a, b in zip(produced, ser):
                if sha256_file(a) != sha256_file(b):
                    par_ok = False
                    ok = False
            rows.append({
                "file": "parallel==serial",
                "status": "PASS" if par_ok else "FAIL",
                "detail": "workers=%s vs 1" % workers,
            })
        if dump_on_fail and not ok:
            save_nondeterminism(case_name, produced, exp_files, repeat_idx)
        return {"case": case_name, "ok": ok, "rows": rows, "repeat": repeat_idx}
    except Exception:
        if dump_on_fail:
            save_nondeterminism(case_name, produced, exp_files, repeat_idx)
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def reset_case_caches() -> None:
    """Drop process-global caches that would leak from one case into the next."""
    try:
        from cutora.render.textfx import clear_roi_cache
        clear_roi_cache()
    except Exception:
        pass
    try:
        from cutora.ui.panels.fonts import get_font_manager
        get_font_manager().clear_cache()
    except Exception:
        pass


def _case_got_hashes(result: dict) -> tuple:
    out = []
    for row in result.get("rows") or []:
        got = row.get("got")
        if got:
            out.append(got.lower())
    return tuple(out)


def run_isolate(cases: list[str]) -> int:
    """Solo process vs --all order: catch process-global leaks NOT in reset_case_caches().

    Caches on that list are cleared between cases, so they never reach this
    comparison. --isolate is the net for whatever the list forgot.
    """
    print("[golden] --isolate sequential (--all order, one process)")
    host, _app = boot_host()
    seq = {}
    for name in cases:
        print("[golden] isolate sequential %s ..." % name, flush=True)
        try:
            r = check_case(host, name, repeat_idx=1, dump_on_fail=False)
        except Exception as e:
            traceback.print_exc()
            r = {"case": name, "ok": False, "error": str(e), "rows": []}
        seq[name] = r

    solo = {}
    py = sys.executable
    script = os.path.abspath(__file__)
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = os.environ.get("QT_QPA_PLATFORM") or "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    for name in cases:
        print("[golden] isolate solo %s ..." % name, flush=True)
        r = subprocess.run(
            [py, "-u", script, "--isolate-child", "--case", name, "--allow-dirty"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        payload = None
        for line in reversed((r.stdout or "").splitlines()):
            if line.startswith("ISOLATE_CHILD "):
                payload = json.loads(line[len("ISOLATE_CHILD "):])
                break
        if payload is None:
            solo[name] = {
                "case": name,
                "ok": False,
                "error": "no ISOLATE_CHILD line (code=%s): %s" % (
                    r.returncode, (r.stderr or r.stdout or "")[-400:]),
                "rows": [],
            }
        else:
            solo[name] = payload

    print("")
    print("%-24s %-14s %-14s %-8s %-10s" % (
        "CASE", "SOLO", "IN_ALL", "SOLO==ALL", "VS_EXP"))
    print("-" * 80)
    all_match = True
    only_g10_exp_miss = True
    for name in cases:
        s, a = solo[name], seq[name]
        sh = _case_got_hashes(s)
        ah = _case_got_hashes(a)
        same = sh == ah and bool(sh)
        if not same:
            all_match = False
        exp_ok = True
        for row in (a.get("rows") or []):
            if row.get("got") and row.get("status") == "FAIL":
                exp_ok = False
        if s.get("error") or a.get("error"):
            same = False
            all_match = False
        if not exp_ok and name != "g10_fx_anim":
            only_g10_exp_miss = False
        def pref(hs):
            if not hs:
                return "(none)"
            return "/".join(h[:12] for h in hs)
        print("%-24s %-14s %-14s %-8s %-10s" % (
            name, pref(sh), pref(ah),
            "yes" if same else "NO",
            "yes" if exp_ok else "no",
        ))
        if s.get("error"):
            print("  solo error: %s" % s["error"])
        if a.get("error"):
            print("  all error: %s" % a["error"])
    print("-" * 80)
    if all_match:
        print("RESULT: PASS (solo == --all order)")
        if not only_g10_exp_miss:
            print("NOTE: a case other than g10 mismatches expected")
        return 0
    print("RESULT: FAIL (solo != --all order for at least one case)")
    return 1


# T11g-B: v1 paint removed. These stay on disk with hashes; --all no longer runs them.
RETIRED_V1_CASES = frozenset({
    "g01_vertical_basic", "g02_vertical_fx", "g03_horizontal_it",
    "g04_stitch", "g05_scatter_insert", "g06_parallel", "g07_cli",
    "g09_linebreak_latin",
})

# 十报: msec seek → POS_FRAMES. Old hashes stay on disk; --all runs g19-2 / g19b-2.
RETIRED_G19_CASES = frozenset({
    "g19_stitch_timeline", "g19b_stitch_crossfade",
})


def list_cases():
    if not os.path.isdir(GOLDEN_ROOT):
        return []
    names = []
    for name in sorted(os.listdir(GOLDEN_ROOT)):
        d = os.path.join(GOLDEN_ROOT, name)
        if os.path.isdir(d) and os.path.isfile(os.path.join(d, "manifest.json")):
            if name in RETIRED_V1_CASES or name in RETIRED_G19_CASES:
                continue
            names.append(name)
    return names


def print_table(results, nondeterministic=False):
    print("")
    print("%-28s %-8s %-20s %s" % ("CASE", "STATUS", "FILE", "DETAIL"))
    print("-" * 88)
    all_ok = True
    for r in results:
        if not r.get("ok"):
            all_ok = False
        status = "PASS" if r.get("ok") else "FAIL"
        rep = r.get("repeat")
        case_label = r["case"]
        if rep is not None:
            case_label = "%s r%s" % (r["case"], rep)
        if r.get("error"):
            print("%-28s %-8s %-20s %s" % (case_label, "ERROR", "-", r["error"]))
            continue
        first = True
        for row in r.get("rows") or []:
            detail = row.get("detail") or ""
            if row.get("got") and row.get("expected"):
                if row["status"] == "PASS":
                    detail = row["got"][:12] + "..."
                else:
                    detail = "got=%s exp=%s" % (row.get("got", "")[:12], str(row.get("expected", ""))[:12])
            case = case_label if first else ""
            st = status if first else row["status"]
            print("%-28s %-8s %-20s %s" % (case, st, row.get("file", ""), detail))
            first = False
    print("-" * 88)
    if all_ok:
        print("RESULT: PASS")
    elif nondeterministic:
        print("RESULT: FAIL (nondeterministic)")
    else:
        print("RESULT: FAIL")
    return all_ok


def main(argv=None):
    p = argparse.ArgumentParser(description="CUTORA golden render check")
    p.add_argument("--all", action="store_true")
    p.add_argument("--case", action="append", default=[])
    p.add_argument(
        "--repeat",
        type=int,
        default=1,
        metavar="N",
        help="run every selected case N times; all repeats must PASS",
    )
    p.add_argument(
        "--allow-dirty",
        action="store_true",
        help="run even if cutora/tests.golden/fonts/golden_check are dirty",
    )
    p.add_argument(
        "--isolate",
        action="store_true",
        help="assert each case hashes the same solo vs in --all order",
    )
    p.add_argument("--isolate-child", action="store_true", help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    if args.repeat < 1:
        print("--repeat must be >= 1")
        return 2
    cases = list(args.case)
    if args.all or not cases:
        cases = list_cases()
    if not cases:
        print("no golden cases found under %s" % GOLDEN_ROOT)
        return 2

    fp = collect_fingerprint()
    print_fingerprint(fp, banner=bool(fp.get("source_dirty")))
    if fp.get("source_dirty") and not args.allow_dirty:
        print("refusing dirty tree; pass --allow-dirty to run anyway")
        return 2

    if args.isolate_child:
        if len(cases) != 1:
            print("isolate-child needs exactly one --case")
            return 2
        try:
            host, _app = boot_host()
            r = check_case(host, cases[0], repeat_idx=1, dump_on_fail=False)
        except Exception as e:
            traceback.print_exc()
            print("ISOLATE_CHILD " + json.dumps({
                "case": cases[0], "ok": False, "error": str(e), "rows": []}))
            return 2
        print("ISOLATE_CHILD " + json.dumps({
            "case": r.get("case"), "ok": r.get("ok"),
            "rows": r.get("rows") or [], "error": r.get("error"),
        }))
        return 0

    if args.isolate:
        try:
            return run_isolate(cases)
        finally:
            if _FIXTURE_TMP:
                shutil.rmtree(_FIXTURE_TMP, ignore_errors=True)

    try:
        host, _app = boot_host()
    except Exception as e:
        print("boot failed: %s" % e)
        traceback.print_exc()
        return 2

    try:
        from PySide6 import __version__ as PYSIDE_VERSION_STR
        fp["pyside6"] = PYSIDE_VERSION_STR
    except Exception as e:
        fp["pyside6"] = "error: %s" % e
    try:
        from cutora.ui.panels.fonts import LAST_EMBEDDED_FONT_LOAD_COUNT, _iter_font_files
        fp["fonts_count"] = LAST_EMBEDDED_FONT_LOAD_COUNT
        _n, digest = _fonts_digest(_iter_font_files())
        fp["fonts_digest"] = digest
    except Exception as e:
        fp["fonts_count"] = "error: %s" % e
    global _FINGERPRINT_TEXT
    _FINGERPRINT_TEXT = format_fingerprint(fp)
    print("[golden-fingerprint] PySide6=%s" % fp["pyside6"])
    print("[golden-fingerprint] fonts_count=%s" % fp.get("fonts_count"))
    print("[golden-fingerprint] fonts_digest=%s" % fp.get("fonts_digest"))

    results = []
    dump_on_fail = args.repeat > 1
    try:
        for rep in range(1, args.repeat + 1):
            if args.repeat > 1:
                print("[golden] ===== repeat %d/%d =====" % (rep, args.repeat))
            for name in cases:
                print("[golden] checking %s (r%d/%d) ..." % (name, rep, args.repeat))
                try:
                    results.append(check_case(
                        host, name, repeat_idx=rep, dump_on_fail=dump_on_fail))
                except Exception as e:
                    traceback.print_exc()
                    results.append({
                        "case": name,
                        "ok": False,
                        "error": str(e),
                        "rows": [],
                        "repeat": rep,
                    })
        nondeterministic = (args.repeat > 1) and any(not r.get("ok") for r in results)
        ok = print_table(results, nondeterministic=nondeterministic)
        if fp.get("source_dirty"):
            print("=" * 72)
            print(DIRTY_BANNER)
            print("=" * 72)
        return 0 if ok else 1
    finally:
        if _FIXTURE_TMP:
            shutil.rmtree(_FIXTURE_TMP, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
