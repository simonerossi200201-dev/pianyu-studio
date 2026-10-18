# -*- coding: utf-8 -*-
"""Download T09 extra OFL fonts from GitHub (development/build period only).

Uses urllib against the host listed in docs/DEV_NETWORK_ALLOWLIST.md.
Does NOT go through cutora.core.net — raw.githubusercontent.com is not on
the product runtime allow-list. Writes to resources/fonts_pending/ and
does not register fonts with QFontDatabase (T12).
"""
from __future__ import annotations

import os
import sys
import urllib.parse
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

FONT_PENDING = os.path.join(REPO, "resources", "fonts_pending")
RAW = "https://raw.githubusercontent.com/google/fonts/main/"
DEV_ALLOWED_HOSTS = frozenset({"raw.githubusercontent.com"})

# (dir_name, github ofl path without ofl/, files)
FAMILIES = [
    ("Inter", "inter", ["OFL.txt", "Inter[opsz,wght].ttf"]),
    ("JetBrains_Mono", "jetbrainsmono", ["OFL.txt", "JetBrainsMono[wght].ttf"]),
    ("Noto_Sans", "notosans", ["OFL.txt", "NotoSans[wdth,wght].ttf"]),
    ("Noto_Sans_SC", "notosanssc", ["OFL.txt", "NotoSansSC[wght].ttf"]),
    ("Noto_Sans_TC", "notosanstc", ["OFL.txt", "NotoSansTC[wght].ttf"]),
    ("Noto_Sans_JP", "notosansjp", ["OFL.txt", "NotoSansJP[wght].ttf"]),
    ("Noto_Sans_KR", "notosanskr", ["OFL.txt", "NotoSansKR[wght].ttf"]),
    ("Noto_Sans_Arabic", "notosansarabic", ["OFL.txt", "NotoSansArabic[wdth,wght].ttf"]),
    ("Noto_Sans_Hebrew", "notosanshebrew", ["OFL.txt", "NotoSansHebrew[wdth,wght].ttf"]),
    ("Noto_Sans_Devanagari", "notosansdevanagari", ["OFL.txt", "NotoSansDevanagari[wdth,wght].ttf"]),
    ("Noto_Sans_Bengali", "notosansbengali", ["OFL.txt", "NotoSansBengali[wdth,wght].ttf"]),
    ("Noto_Sans_Thai", "notosansthai", ["OFL.txt", "NotoSansThai[wdth,wght].ttf"]),
    ("Noto_Sans_Tamil", "notosanstamil", ["OFL.txt", "NotoSansTamil[wdth,wght].ttf"]),
    ("Noto_Sans_Telugu", "notosanstelugu", ["OFL.txt", "NotoSansTelugu[wdth,wght].ttf"]),
    ("Noto_Sans_Malayalam", "notosansmalayalam", ["OFL.txt", "NotoSansMalayalam[wdth,wght].ttf"]),
    ("Noto_Sans_Georgian", "notosansgeorgian", ["OFL.txt", "NotoSansGeorgian[wdth,wght].ttf"]),
    ("Noto_Sans_Armenian", "notosansarmenian", ["OFL.txt", "NotoSansArmenian[wdth,wght].ttf"]),
]

# Source Han Sans SC Regular (OFL) from adobe-fonts official repo.
SOURCE_HAN = (
    "Source_Han_Sans",
    "https://raw.githubusercontent.com/adobe-fonts/source-han-sans/release/OTF/SimplifiedChinese/SourceHanSansSC-Regular.otf",
    "https://raw.githubusercontent.com/adobe-fonts/source-han-sans/release/LICENSE.txt",
)


def _url(ofl_dir: str, filename: str) -> str:
    return RAW + "ofl/%s/%s" % (ofl_dir, urllib.parse.quote(filename, safe=""))


def _save(url: str, dest: str) -> None:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.isfile(dest) and os.path.getsize(dest) > 100:
        print("skip", dest)
        return
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host not in DEV_ALLOWED_HOSTS:
        raise RuntimeError("dev fetch host not allowed: %s" % host)
    print("GET", url)
    req = urllib.request.Request(url, headers={"User-Agent": "CUTORA-font-fetch"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = resp.read()
    if not body or len(body) < 20:
        raise RuntimeError("empty download: %s" % url)
    tmp = dest + ".tmp"
    with open(tmp, "wb") as f:
        f.write(body)
    os.replace(tmp, dest)
    print(" wrote", dest, len(body))


def main() -> int:
    os.makedirs(FONT_PENDING, exist_ok=True)
    errors = []
    for folder, ofl_dir, files in FAMILIES:
        dest_dir = os.path.join(FONT_PENDING, folder)
        for fn in files:
            dest = os.path.join(dest_dir, fn.replace("[", "").replace("]", "").replace(",", "_"))
            try:
                _save(_url(ofl_dir, fn), dest)
            except Exception as exc:
                print("FAIL", folder, fn, exc)
                errors.append("%s/%s: %s" % (folder, fn, exc))
    dest_dir = os.path.join(FONT_PENDING, SOURCE_HAN[0])
    try:
        _save(SOURCE_HAN[1], os.path.join(dest_dir, "SourceHanSansSC-Regular.otf"))
        _save(SOURCE_HAN[2], os.path.join(dest_dir, "LICENSE.txt"))
    except Exception as exc:
        print("FAIL Source Han Sans", exc)
        errors.append("Source_Han_Sans: %s" % exc)
    if errors:
        print("errors:", len(errors))
        for e in errors:
            print(" ", e)
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
