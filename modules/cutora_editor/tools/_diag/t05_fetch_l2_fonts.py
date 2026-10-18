# -*- coding: utf-8 -*-
"""Download L2 Noto fonts from one google/fonts commit. Dev-period only."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHA = "809e4d8b8d7e9364a914909bb777679606c178b8"
RAW = "https://raw.githubusercontent.com/google/fonts/%s/" % SHA
STAGING = os.path.join(REPO, "build", "t05_fonts_" + SHA[:12])
PENDING = os.path.join(REPO, "resources", "fonts_pending")
HOSTS = frozenset({"raw.githubusercontent.com"})

# dest_dir under resources/fonts/, ofl slug, ttf filename on github
FAMILIES = [
    ("Noto_Sans", "notosans", "NotoSans[wdth,wght].ttf"),
    ("Noto_Sans_Arabic", "notosansarabic", "NotoSansArabic[wdth,wght].ttf"),
    ("Noto_Sans_Armenian", "notosansarmenian", "NotoSansArmenian[wdth,wght].ttf"),
    ("Noto_Sans_Bengali", "notosansbengali", "NotoSansBengali[wdth,wght].ttf"),
    ("Noto_Sans_Devanagari", "notosansdevanagari", "NotoSansDevanagari[wdth,wght].ttf"),
    ("Noto_Sans_Georgian", "notosansgeorgian", "NotoSansGeorgian[wdth,wght].ttf"),
    ("Noto_Sans_Hebrew", "notosanshebrew", "NotoSansHebrew[wdth,wght].ttf"),
    ("Noto_Sans_JP", "notosansjp", "NotoSansJP[wght].ttf"),
    ("Noto_Sans_KR", "notosanskr", "NotoSansKR[wght].ttf"),
    ("Noto_Sans_Malayalam", "notosansmalayalam", "NotoSansMalayalam[wdth,wght].ttf"),
    ("Noto_Sans_SC", "notosanssc", "NotoSansSC[wght].ttf"),
    ("Noto_Sans_TC", "notosanstc", "NotoSansTC[wght].ttf"),
    ("Noto_Sans_Tamil", "notosanstamil", "NotoSansTamil[wdth,wght].ttf"),
    ("Noto_Sans_Telugu", "notosanstelugu", "NotoSansTelugu[wdth,wght].ttf"),
    ("Noto_Sans_Thai", "notosansthai", "NotoSansThai[wdth,wght].ttf"),
    ("Noto_Naskh_Arabic", "notonaskharabic", "NotoNaskhArabic[wght].ttf"),
    ("Noto_Sans_Gujarati", "notosansgujarati", "NotoSansGujarati[wdth,wght].ttf"),
    ("Noto_Sans_Gurmukhi", "notosansgurmukhi", "NotoSansGurmukhi[wdth,wght].ttf"),
    ("Noto_Sans_Kannada", "notosanskannada", "NotoSansKannada[wdth,wght].ttf"),
    ("Noto_Sans_Khmer", "notosanskhmer", "NotoSansKhmer[wdth,wght].ttf"),
    ("Noto_Sans_Lao", "notosanslao", "NotoSansLao[wdth,wght].ttf"),
    ("Noto_Sans_Sinhala", "notosanssinhala", "NotoSansSinhala[wdth,wght].ttf"),
    ("Noto_Sans_Myanmar", "notosansmyanmar", "NotoSansMyanmar[wdth,wght].ttf"),
    ("Noto_Sans_Ethiopic", "notosansethiopic", "NotoSansEthiopic[wdth,wght].ttf"),
    ("Noto_Serif_Tibetan", "notoseriftibetan", "NotoSerifTibetan[wght].ttf"),
]

# pending filename (as on disk) for families that already exist in pending
PENDING_TTF = {
    "Noto_Sans": "NotoSanswdth_wght.ttf",
    "Noto_Sans_Arabic": "NotoSansArabicwdth_wght.ttf",
    "Noto_Sans_Armenian": "NotoSansArmenianwdth_wght.ttf",
    "Noto_Sans_Bengali": "NotoSansBengaliwdth_wght.ttf",
    "Noto_Sans_Devanagari": "NotoSansDevanagariwdth_wght.ttf",
    "Noto_Sans_Georgian": "NotoSansGeorgianwdth_wght.ttf",
    "Noto_Sans_Hebrew": "NotoSansHebrewwdht_wght.ttf",
    "Noto_Sans_JP": "NotoSansJPwght.ttf",
    "Noto_Sans_KR": "NotoSansKRwght.ttf",
    "Noto_Sans_Malayalam": "NotoSansMalayalamwdth_wght.ttf",
    "Noto_Sans_SC": "NotoSansSCwght.ttf",
    "Noto_Sans_TC": "NotoSansTCwght.ttf",
    "Noto_Sans_Tamil": "NotoSansTamilwdth_wght.ttf",
    "Noto_Sans_Telugu": "NotoSansTeluguwdth_wght.ttf",
    "Noto_Sans_Thai": "NotoSansThaiwdth_wght.ttf",
}


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _get(url: str) -> bytes:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host not in HOSTS:
        raise RuntimeError("host not allowed: %s" % host)
    req = urllib.request.Request(url, headers={"User-Agent": "CUTORA-font-fetch"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        body = resp.read()
    if not body or len(body) < 20:
        raise RuntimeError("empty %s" % url)
    return body


def _safe_name(filename: str) -> str:
    return filename.replace("[", "").replace("]", "").replace(",", "_")


def main() -> int:
    os.makedirs(STAGING, exist_ok=True)
    report = []
    for dest_dir, slug, ttf in FAMILIES:
        folder = os.path.join(STAGING, dest_dir)
        os.makedirs(folder, exist_ok=True)
        ttf_url = RAW + "ofl/%s/%s" % (slug, urllib.parse.quote(ttf, safe=""))
        ofl_url = RAW + "ofl/%s/OFL.txt" % slug
        ttf_dest = os.path.join(folder, _safe_name(ttf))
        ofl_dest = os.path.join(folder, "OFL.txt")
        print("GET", ttf_url)
        raw = _get(ttf_url)
        with open(ttf_dest, "wb") as fh:
            fh.write(raw)
        ofl = _get(ofl_url)
        with open(ofl_dest, "wb") as fh:
            fh.write(ofl)
        digest = hashlib.sha256(raw).hexdigest()
        pending_name = PENDING_TTF.get(dest_dir)
        pending_path = ""
        match = None
        if pending_name:
            # actual pending names (no brackets)
            for fn in os.listdir(os.path.join(PENDING, dest_dir)) if os.path.isdir(os.path.join(PENDING, dest_dir)) else []:
                if fn.lower().endswith((".ttf", ".otf")):
                    pending_path = os.path.join(PENDING, dest_dir, fn)
                    break
            if pending_path and os.path.isfile(pending_path):
                old = _sha256(pending_path)
                match = old == digest
        rec = {
            "family_dir": dest_dir,
            "ofl_slug": slug,
            "github_file": ttf,
            "staged": os.path.relpath(ttf_dest, REPO).replace("\\", "/"),
            "sha256": digest,
            "bytes": len(raw),
            "pending_path": pending_path.replace("\\", "/") if pending_path else "",
            "pending_match": match,
            "action": "keep-pending" if match else "use-new",
        }
        report.append(rec)
        print(" ", rec["action"], dest_dir, digest[:16], "bytes", len(raw), "match", match)
    out = os.path.join(STAGING, "compare.json")
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"google_fonts_sha": SHA, "files": report}, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("WROTE", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
