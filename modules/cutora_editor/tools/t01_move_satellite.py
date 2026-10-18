#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T01 step-2 helper: git-mv one satellite module, write a one-line shim,
and rewrite `import oldname` / `from oldname import ...` across the repo.

Does not run git. Prints the commands and the rewritten files.

Usage:
    python tools/t01_move_satellite.py --list
    python tools/t01_move_satellite.py anim_engine --apply
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# old_modname → (src_rel, dest_rel, new_modpath)
MOVES: dict[str, tuple[str, str, str]] = {
    "storage": ("core/storage.py", "cutora/core/storage.py", "cutora.core.storage"),
    "anim_engine": ("core/anim_engine.py", "cutora/render/anim_engine.py", "cutora.render.anim_engine"),
    "audio_spectrum": ("core/audio_spectrum.py", "cutora/audio/spectrum.py", "cutora.audio.spectrum"),
    "spatial_ir": ("core/spatial_ir.py", "cutora/audio/spatial_ir.py", "cutora.audio.spatial_ir"),
    "spatial_audio": ("core/spatial_audio.py", "cutora/audio/spatial.py", "cutora.audio.spatial"),
    "spatial_selftest": ("core/spatial_selftest.py", "cutora/audio/spatial_selftest.py", "cutora.audio.spatial_selftest"),
    "sfx_synth": ("core/sfx_synth.py", "cutora/audio/sfx_synth.py", "cutora.audio.sfx_synth"),
    "sfx_library": ("core/sfx_library.py", "cutora/audio/sfx_library.py", "cutora.audio.sfx_library"),
    "sfx_ai": ("core/sfx_ai.py", "cutora/audio/sfx_ai.py", "cutora.audio.sfx_ai"),
    "sfx_panel": ("core/sfx_panel.py", "cutora/ui/panels/audio_fx.py", "cutora.ui.panels.audio_fx"),
    "waveform": ("core/waveform.py", "cutora/audio/waveform.py", "cutora.audio.waveform"),
    "timeline_model": ("core/timeline_model.py", "cutora/model/timeline_model.py", "cutora.model.timeline_model"),
    "timeline": ("core/timeline.py", "cutora/model/timeline.py", "cutora.model.timeline"),
    "preset_manager": ("core/preset_manager.py", "cutora/subtitle/presets.py", "cutora.subtitle.presets"),
    "export_guard": ("core/export_guard.py", "cutora/render/export_guard.py", "cutora.render.export_guard"),
    "media_guard": ("core/media_guard.py", "cutora/batch/media_guard.py", "cutora.batch.media_guard"),
    "wenan_norm": ("core/wenan_norm.py", "cutora/batch/wenan_norm.py", "cutora.batch.wenan_norm"),
    "script_stitcher": ("core/script_stitcher.py", "cutora/batch/script_stitcher.py", "cutora.batch.script_stitcher"),
    "script_broll_scatter": ("core/script_broll_scatter.py", "cutora/batch/script_broll_scatter.py", "cutora.batch.script_broll_scatter"),
    "script_insert_image": ("core/script_insert_image.py", "cutora/batch/script_insert_image.py", "cutora.batch.script_insert_image"),
    "script_ui": ("core/script_ui.py", "cutora/batch/script_ui.py", "cutora.batch.script_ui"),
    "autosub_cli": ("core/autosub_cli.py", "cutora/batch/cli.py", "cutora.batch.cli"),
    "project_queue_ui": ("core/project_queue_ui.py", "cutora/batch/queue_ui.py", "cutora.batch.queue_ui"),
    "queue_relaunch": ("core/queue_relaunch.py", "cutora/batch/queue_relaunch.py", "cutora.batch.queue_relaunch"),
    "davinci_panels": ("core/davinci_panels.py", "cutora/ui/widgets/davinci_panels.py", "cutora.ui.widgets.davinci_panels"),
    "download_full_models": ("core/download_full_models.py", "cutora/asr/download_full_models.py", "cutora.asr.download_full_models"),
    "download_en_align_model": ("core/download_en_align_model.py", "cutora/asr/download_en_align_model.py", "cutora.asr.download_en_align_model"),
    "config": ("core/config.py", "cutora/core/config.py", "cutora.core.config"),
    "utils": ("core/utils.py", "cutora/core/utils.py", "cutora.core.utils"),
    "debug_print": ("core/debug_print.py", "cutora/core/debug_print.py", "cutora.core.debug_print"),
}

SKIP_DIRS = {
    ".git", "runtime", "build", "tests/golden", "__pycache__",
    "core/whisperX", "core/fonts", "core/user_data", "core/assets",
}


def _is_skipped(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")
    if parts[0] in {".git", "runtime", "build", "__pycache__"}:
        return True
    if rel.replace("\\", "/").startswith("core/whisperX"):
        return True
    if rel.replace("\\", "/").startswith("tests/golden"):
        return True
    return False


def rewrite_imports(text: str, old: str, new_mod: str) -> str:
    """Rewrite import old / from old import X. Keep `as old` so callers
    that use the old module name still work without further edits.
    """
    pkg, _, leaf = new_mod.rpartition(".")
    # from old import a, b
    text = re.sub(
        r"(?m)^(?P<ind>\s*)from %s import (?P<rest>.+)$" % re.escape(old),
        lambda m: "%sfrom %s import %s" % (m.group("ind"), new_mod, m.group("rest")),
        text,
    )
    # import old as alias
    text = re.sub(
        r"(?m)^(?P<ind>\s*)import %s as (?P<alias>\S+)$" % re.escape(old),
        lambda m: "%sfrom %s import %s as %s" % (
            m.group("ind"), pkg, leaf, m.group("alias")) if pkg
        else "%simport %s as %s" % (m.group("ind"), new_mod, m.group("alias")),
        text,
    )
    # import old
    text = re.sub(
        r"(?m)^(?P<ind>\s*)import %s$" % re.escape(old),
        (lambda m: "%sfrom %s import %s as %s" % (m.group("ind"), pkg, leaf, old))
        if pkg else (lambda m: "%simport %s" % (m.group("ind"), new_mod)),
        text,
    )
    return text


def apply_move(old: str, dry_run: bool) -> None:
    src, dest, new_mod = MOVES[old]
    src_p = os.path.join(REPO, src.replace("/", os.sep))
    dest_p = os.path.join(REPO, dest.replace("/", os.sep))
    shim_p = src_p
    if not os.path.isfile(src_p):
        raise SystemExit("missing %s (already moved?)" % src)
    print("git mv %s %s" % (src, dest))
    if not dry_run:
        os.makedirs(os.path.dirname(dest_p), exist_ok=True)
        subprocess.check_call(["git", "mv", "--", src, dest], cwd=REPO)
        with open(shim_p, "w", encoding="utf-8", newline="\n") as f:
            f.write(
                "import os\n"
                "import sys\n"
                "_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
                "if _root not in sys.path:\n"
                "    sys.path.insert(0, _root)\n"
                "from %s import *  # legacy shim, removed in T03\n" % new_mod
            )
        subprocess.check_call(["git", "add", "--", src], cwd=REPO)

    # rewrite imports in tracked-ish py files
    for dirpath, dirnames, filenames in os.walk(REPO):
        rel_dir = os.path.relpath(dirpath, REPO)
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and d != "__pycache__"]
        if _is_skipped(rel_dir):
            continue
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, REPO).replace("\\", "/")
            if rel == dest or rel == src:
                continue
            newline = "" if rel == "core/AutoSub_debug.py" else "\n"
            with open(path, "r", encoding="utf-8", newline=newline or None) as f:
                original = f.read()
            updated = rewrite_imports(original, old, new_mod)
            if updated != original:
                print("rewrite %s" % rel)
                if not dry_run:
                    with open(path, "w", encoding="utf-8", newline=newline or "\n") as f:
                        f.write(updated)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("module", nargs="?")
    p.add_argument("--list", action="store_true")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    if args.list or not args.module:
        for k, (s, d, n) in MOVES.items():
            exists = os.path.isfile(os.path.join(REPO, s.replace("/", os.sep)))
            print("%-24s %s -> %s%s" % (k, s, d, "" if exists else " (src gone)"))
        return 0
    if args.module not in MOVES:
        print("unknown module %s" % args.module)
        return 2
    apply_move(args.module, dry_run=not args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
