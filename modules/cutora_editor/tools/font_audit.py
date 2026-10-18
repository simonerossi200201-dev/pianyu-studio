# -*- coding: utf-8 -*-
"""Audit fonts under resources/fonts/. Writes FONTS_MANIFEST.json.

UNKNOWN / disallowed licenses are moved to resources/fonts/_quarantine/.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys

ALLOWED = ("OFL-1.1", "Apache-2.0", "UFL-1.0", "Bitstream-Vera")

# Direct file URLs (dev-period host; recorded in the manifest, not in net.py).
PENDING_DOWNLOAD_URLS = {
    "Inter": "https://raw.githubusercontent.com/google/fonts/main/ofl/inter/Inter%5Bopsz%2Cwght%5D.ttf",
    "JetBrains_Mono": "https://raw.githubusercontent.com/google/fonts/main/ofl/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf",
    "Noto_Sans": "https://raw.githubusercontent.com/google/fonts/main/ofl/notosans/NotoSans%5Bwdth%2Cwght%5D.ttf",
    "Noto_Sans_SC": "https://raw.githubusercontent.com/google/fonts/main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf",
    "Noto_Sans_TC": "https://raw.githubusercontent.com/google/fonts/main/ofl/notosanstc/NotoSansTC%5Bwght%5D.ttf",
    "Noto_Sans_JP": "https://raw.githubusercontent.com/google/fonts/main/ofl/notosansjp/NotoSansJP%5Bwght%5D.ttf",
    "Noto_Sans_KR": "https://raw.githubusercontent.com/google/fonts/main/ofl/notosanskr/NotoSansKR%5Bwght%5D.ttf",
    "Noto_Sans_Arabic": "https://raw.githubusercontent.com/google/fonts/main/ofl/notosansarabic/NotoSansArabic%5Bwdth%2Cwght%5D.ttf",
    "Noto_Sans_Hebrew": "https://raw.githubusercontent.com/google/fonts/main/ofl/notosanshebrew/NotoSansHebrew%5Bwdth%2Cwght%5D.ttf",
    "Noto_Sans_Devanagari": "https://raw.githubusercontent.com/google/fonts/main/ofl/notosansdevanagari/NotoSansDevanagari%5Bwdth%2Cwght%5D.ttf",
    "Noto_Sans_Bengali": "https://raw.githubusercontent.com/google/fonts/main/ofl/notosansbengali/NotoSansBengali%5Bwdth%2Cwght%5D.ttf",
    "Noto_Sans_Thai": "https://raw.githubusercontent.com/google/fonts/main/ofl/notosansthai/NotoSansThai%5Bwdth%2Cwght%5D.ttf",
    "Noto_Sans_Tamil": "https://raw.githubusercontent.com/google/fonts/main/ofl/notosanstamil/NotoSansTamil%5Bwdth%2Cwght%5D.ttf",
    "Noto_Sans_Telugu": "https://raw.githubusercontent.com/google/fonts/main/ofl/notosanstelugu/NotoSansTelugu%5Bwdth%2Cwght%5D.ttf",
    "Noto_Sans_Malayalam": "https://raw.githubusercontent.com/google/fonts/main/ofl/notosansmalayalam/NotoSansMalayalam%5Bwdth%2Cwght%5D.ttf",
    "Noto_Sans_Georgian": "https://raw.githubusercontent.com/google/fonts/main/ofl/notosansgeorgian/NotoSansGeorgian%5Bwdth%2Cwght%5D.ttf",
    "Noto_Sans_Armenian": "https://raw.githubusercontent.com/google/fonts/main/ofl/notosansarmenian/NotoSansArmenian%5Bwdth%2Cwght%5D.ttf",
    "Source_Han_Sans": "https://raw.githubusercontent.com/adobe-fonts/source-han-sans/release/OTF/SimplifiedChinese/SourceHanSansSC-Regular.otf",
}

SOURCE_URLS = {
    "Inter": "https://github.com/google/fonts/tree/main/ofl/inter",
    "JetBrains_Mono": "https://github.com/google/fonts/tree/main/ofl/jetbrainsmono",
    "Noto_Sans": "https://github.com/google/fonts/tree/main/ofl/notosans",
    "Noto_Sans_SC": "https://github.com/google/fonts/tree/main/ofl/notosanssc",
    "Noto_Sans_TC": "https://github.com/google/fonts/tree/main/ofl/notosanstc",
    "Noto_Sans_JP": "https://github.com/google/fonts/tree/main/ofl/notosansjp",
    "Noto_Sans_KR": "https://github.com/google/fonts/tree/main/ofl/notosanskr",
    "Noto_Sans_Arabic": "https://github.com/google/fonts/tree/main/ofl/notosansarabic",
    "Noto_Sans_Hebrew": "https://github.com/google/fonts/tree/main/ofl/notosanshebrew",
    "Noto_Sans_Devanagari": "https://github.com/google/fonts/tree/main/ofl/notosansdevanagari",
    "Noto_Sans_Bengali": "https://github.com/google/fonts/tree/main/ofl/notosansbengali",
    "Noto_Sans_Thai": "https://github.com/google/fonts/tree/main/ofl/notosansthai",
    "Noto_Sans_Tamil": "https://github.com/google/fonts/tree/main/ofl/notosanstamil",
    "Noto_Sans_Telugu": "https://github.com/google/fonts/tree/main/ofl/notosanstelugu",
    "Noto_Sans_Malayalam": "https://github.com/google/fonts/tree/main/ofl/notosansmalayalam",
    "Noto_Sans_Georgian": "https://github.com/google/fonts/tree/main/ofl/notosansgeorgian",
    "Noto_Sans_Armenian": "https://github.com/google/fonts/tree/main/ofl/notosansarmenian",
    "Source_Han_Sans": "https://github.com/adobe-fonts/source-han-sans/tree/release/OTF/SimplifiedChinese",
}

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_ROOT = os.path.join(REPO, "resources", "fonts")
FONT_EXTRA = os.path.join(REPO, "resources", "fonts_pending")
QUARANTINE = os.path.join(FONT_ROOT, "_quarantine")
MANIFEST = os.path.join(FONT_ROOT, "FONTS_MANIFEST.json")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _name_records(path: str) -> dict:
    from fontTools.ttLib import TTFont
    out = {"family": "", "version": "", "license_desc": "", "license_url": "", "copyright": ""}
    font = TTFont(path, lazy=True)
    try:
        table = font["name"]
        mapping = {1: "family", 5: "version", 13: "license_desc", 14: "license_url", 0: "copyright"}
        for rec in table.names:
            key = mapping.get(rec.nameID)
            if not key or out[key]:
                continue
            try:
                out[key] = rec.toUnicode().strip()
            except Exception:
                try:
                    out[key] = rec.string.decode("latin-1", "replace").strip()
                except Exception:
                    pass
    finally:
        font.close()
    return out


def _classify(license_desc: str, license_url: str, sidecar_text: str) -> str:
    blob = " ".join([license_desc or "", license_url or "", sidecar_text or ""]).lower()
    if "ubuntu font licence" in blob or "ubuntu font license" in blob or "ufl" in blob and "ubuntu" in blob:
        return "UFL-1.0"
    if "bitstream vera" in blob or "dejaVu" in blob.lower() or "dejavu" in blob:
        return "Bitstream-Vera"
    if "apache" in blob:
        return "Apache-2.0"
    if "sil open font license" in blob or "open font license" in blob or "scripts.sil.org/ofl" in blob or "ofl" in blob:
        return "OFL-1.1"
    return "UNKNOWN"


def _sidecar_text(family_dir: str) -> str:
    chunks = []
    if not os.path.isdir(family_dir):
        return ""
    for fn in os.listdir(family_dir):
        low = fn.lower()
        if low in ("ofl.txt", "license", "license.txt", "licence.txt", "apache.txt", "ufl.txt") or "license" in low or "ofl" in low:
            path = os.path.join(family_dir, fn)
            if os.path.isfile(path) and os.path.getsize(path) < 200000:
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        chunks.append(f.read())
                except Exception:
                    pass
    return "\n".join(chunks)


def _has_license_file(family_dir: str) -> bool:
    if not os.path.isdir(family_dir):
        return False
    for fn in os.listdir(family_dir):
        low = fn.lower()
        if low in ("ofl.txt", "license", "license.txt", "licence.txt", "apache.txt", "ufl.txt"):
            return True
        if "license" in low or low.startswith("ofl"):
            return True
    return False


def iter_font_files(root: str):
    skip = {"LICENSES"}  # audit _extra and _quarantine; manager skips those dirs
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
        for fn in filenames:
            if fn.lower().endswith((".ttf", ".otf", ".ttc")):
                yield os.path.join(dirpath, fn)


def audit(move_unknown: bool = True) -> dict:
    fonts = []
    quarantined = []
    os.makedirs(FONT_ROOT, exist_ok=True)
    roots = [FONT_ROOT]
    if os.path.isdir(FONT_EXTRA):
        roots.append(FONT_EXTRA)
    all_files = []
    for root in roots:
        all_files.extend(iter_font_files(root))
    for path in sorted(all_files):
        rel = os.path.relpath(path, os.path.join(REPO, "resources")).replace("\\", "/")
        family_dir = os.path.dirname(path)
        try:
            rec = _name_records(path)
        except Exception as exc:
            rec = {"family": "", "version": "", "license_desc": "", "license_url": "", "copyright": ""}
            rec["_error"] = str(exc)
        sidecar = _sidecar_text(family_dir)
        license_id = _classify(rec.get("license_desc") or "", rec.get("license_url") or "", sidecar)
        item = {
            "file": rel,
            "family": rec.get("family") or os.path.splitext(os.path.basename(path))[0],
            "version": rec.get("version") or "",
            "license": license_id,
            "license_url": rec.get("license_url") or "",
            "copyright": rec.get("copyright") or "",
            "sha256": _sha256(path),
            "has_license_file": _has_license_file(family_dir),
        }
        parts = [p for p in rel.split("/") if p]
        folder = parts[-2] if len(parts) >= 2 else (parts[0] if parts else "")
        if folder in SOURCE_URLS:
            item["source_url"] = SOURCE_URLS[folder]
        extra_root = os.path.normcase(os.path.abspath(FONT_EXTRA))
        abs_path = os.path.normcase(os.path.abspath(path))
        if os.path.isdir(FONT_EXTRA) and (
            abs_path == extra_root or abs_path.startswith(extra_root + os.sep)
        ):
            item["status"] = "staged, not registered"
            if folder in PENDING_DOWNLOAD_URLS:
                item["download_url"] = PENDING_DOWNLOAD_URLS[folder]
        if license_id not in ALLOWED:
            if move_unknown:
                dest_dir = os.path.join(QUARANTINE, os.path.relpath(family_dir, FONT_ROOT))
                os.makedirs(dest_dir, exist_ok=True)
                dest = os.path.join(dest_dir, os.path.basename(path))
                if os.path.abspath(path) != os.path.abspath(dest):
                    shutil.move(path, dest)
                item["file"] = os.path.relpath(dest, FONT_ROOT).replace("\\", "/")
                item["quarantined"] = True
            quarantined.append(item)
        fonts.append(item)

    by_lic = {}
    for item in fonts:
        by_lic[item["license"]] = by_lic.get(item["license"], 0) + 1
    families = {}
    for item in fonts:
        if item.get("quarantined"):
            continue
        families.setdefault(item["family"], []).append(item)
    missing_lic = []
    for fam, items in families.items():
        if not any(i.get("has_license_file") for i in items):
            missing_lic.append(fam)

    manifest = {
        "font_count": len(fonts),
        "by_license": by_lic,
        "quarantined": quarantined,
        "missing_license_file": missing_lic,
        "fonts": fonts,
    }
    with open(MANIFEST, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return manifest


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    move = "--no-move" not in argv
    man = audit(move_unknown=move)
    print("fonts:", man["font_count"])
    print("by_license:", json.dumps(man["by_license"], ensure_ascii=False))
    print("quarantined:", len(man["quarantined"]))
    print("missing_license_file:", len(man["missing_license_file"]))
    print("wrote", MANIFEST)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
