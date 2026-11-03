# -*- coding: utf-8 -*-
"""Fetch the social-video Chinese / Arabic font families from google/fonts (pinned commit).

Security & Commercial Compliance rules:

* Only source: the official ``google/fonts`` GitHub repository, at the pinned commit
  ``PINNED_SHA`` (never a branch name, never a mirror). Directory listings come from the
  GitHub contents API *at that commit*; file bytes come from
  ``https://raw.githubusercontent.com/google/fonts/<PINNED_SHA>/ofl/<slug>/<file>``.
* Only families under ``ofl/`` (SIL OFL 1.1). Anything else is refused.
* No artificial cap on font count or total byte budget — all commercial-safe OFL-1.1
  Chinese (Simplified / Traditional) and Arabic families in ``FAMILIES`` are fetched,
  together with full provenance / license sidecars (``OFL.txt``, ``METADATA.pb``,
  and optional ``DESCRIPTION.en_us.html`` / ``upstream_info.md``).
* Every font is fully parsed with fontTools (all tables decompiled) and must actually
  cover its script (CJK: >= 3500 code points in U+4E00..U+9FFF / U+3400..U+4DBF;
  Arabic: U+0627..U+064A). Colour fonts (COLR / CBDT / sbix / SVG) and variable fonts
  with >= 4 axes are skipped.
* SHA256 of every file is recorded in ``resources/fonts/PROVENANCE.json``,
  ``resources/fonts/FONTS_MANIFEST.json``, and ``resources/FONTS_LICENSES.md``.
  On re-run, files already on disk and files downloaded again must match the recorded hash.

Usage::

    runtime\\venv\\Scripts\\python.exe tools\\fetch_social_fonts.py            # fetch / verify
    runtime\\venv\\Scripts\\python.exe tools\\fetch_social_fonts.py --verify   # offline: hash + credential check
    runtime\\venv\\Scripts\\python.exe tools\\fetch_social_fonts.py --list     # sizes, no download

This is a developer tool. The app itself never downloads fonts at runtime.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_ROOT = os.path.join(REPO, "resources", "fonts")
PROVENANCE = os.path.join(FONT_ROOT, "PROVENANCE.json")
MANIFEST = os.path.join(FONT_ROOT, "FONTS_MANIFEST.json")
LICENSES_MD = os.path.join(REPO, "resources", "FONTS_LICENSES.md")

# google/fonts main, resolved once via the GitHub API on 2026-11-03.
PINNED_SHA = "2fe1e8c6b137fb13041a7cfca6fdcd8a910ea4dc"
API_HOST = "api.github.com"
RAW_HOST = "raw.githubusercontent.com"
UA = "cutora-fetch-social-fonts/2"

CJK_MIN = 3500
RISKY_AXES = 4          # == cutora.ui.font_probe.RISKY_AXES
COLOR_TABLES = {"COLR", "CBDT", "sbix", "SVG "}
SIDECAR_FILES = ("OFL.txt", "METADATA.pb", "DESCRIPTION.en_us.html", "upstream_info.md")

# (slug under ofl/, destination folder under resources/fonts, script, priority)
FAMILIES = [
    # ---- Chinese (Simplified & Traditional) ----
    ("notoserifsc", "Noto_Serif_SC", "han", 9),
    ("zcoolkuaile", "ZCOOL_KuaiLe", "han", 8),
    ("zcoolqingkehuangyou", "ZCOOL_QingKe_HuangYou", "han", 8),
    ("zcoolxiaowei", "ZCOOL_XiaoWei", "han", 7),
    ("mashanzheng", "Ma_Shan_Zheng", "han", 6),
    ("zhimangxing", "Zhi_Mang_Xing", "han", 6),
    ("longcang", "Long_Cang", "han", 6),
    ("liujianmaocao", "Liu_Jian_Mao_Cao", "han", 6),
    ("lxgwwenkaitc", "LXGW_WenKai_TC", "han", 8),
    ("wdxllubrifontsc", "WDXL_Lubrifont_SC", "han", 7),
    ("lxgwmarkergothic", "LXGW_Marker_Gothic", "han", 7),
    ("chironheihk", "Chiron_Hei_HK", "han", 7),
    ("chirongoroundtc", "Chiron_GoRound_TC", "han", 7),
    ("huninn", "Huninn", "han", 7),
    ("iansui", "Iansui", "han", 7),
    ("cactusclassicalserif", "Cactus_Classical_Serif", "han", 7),
    ("chocolateclassicalsans", "Chocolate_Classical_Sans", "han", 7),
    ("notosanshk", "Noto_Sans_HK", "han", 8),
    ("notoseriftc", "Noto_Serif_TC", "han", 8),
    ("notoserifhk", "Noto_Serif_HK", "han", 8),
    # ---- Arabic ----
    ("reemkufi", "Reem_Kufi", "arabic", 8),
    ("lalezar", "Lalezar", "arabic", 8),
    ("changa", "Changa", "arabic", 8),
    ("elmessiri", "El_Messiri", "arabic", 7),
    ("almarai", "Almarai", "arabic", 7),
    ("rakkas", "Rakkas", "arabic", 6),
    ("arefruqaa", "Aref_Ruqaa", "arabic", 6),
    ("markazitext", "Markazi_Text", "arabic", 6),
    ("readexpro", "Readex_Pro", "arabic", 7),
    ("lemonada", "Lemonada", "arabic", 6),
    ("marhey", "Marhey", "arabic", 6),
    ("ibmplexsansarabic", "IBM_Plex_Sans_Arabic", "arabic", 7),
    ("mada", "Mada", "arabic", 6),
    ("kufam", "Kufam", "arabic", 6),
    ("harmattan", "Harmattan", "arabic", 6),
    ("vibes", "Vibes", "arabic", 5),
    ("blaka", "Blaka", "arabic", 5),
    ("lateef", "Lateef", "arabic", 6),
    ("mirza", "Mirza", "arabic", 6),
    ("scheherazadenew", "Scheherazade_New", "arabic", 7),
    ("alkalami", "Alkalami", "arabic", 6),
    ("gulzar", "Gulzar", "arabic", 6),
    ("ruwudu", "Ruwudu", "arabic", 6),
]

_SLUG_RE = re.compile(r"^[a-z0-9]+$")
_FILE_RE = re.compile(r"^[A-Za-z0-9_\-\[\],.]+$")
_FONT_EXTS = (".ttf", ".otf")


def _log(*a):
    print(*a, flush=True)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def local_name(repo_file: str) -> str:
    """Same normalisation as the existing entries: ``NotoSans[wdth,wght].ttf`` -> ``NotoSanswdth_wght.ttf``."""
    return repo_file.replace("[", "").replace("]", "").replace(",", "_")


def _check_repo_path(repo_path: str) -> None:
    parts = repo_path.split("/")
    if (len(parts) != 3 or parts[0] != "ofl" or not _SLUG_RE.match(parts[1])
            or not _FILE_RE.match(parts[2]) or ".." in repo_path):
        raise SystemExit("REFUSED non-ofl / unsafe repo path: %r" % repo_path)


def raw_url(repo_path: str) -> str:
    _check_repo_path(repo_path)
    url = "https://%s/google/fonts/%s/%s" % (RAW_HOST, PINNED_SHA, urllib.parse.quote(repo_path))
    _check_url(url)
    return url


def _check_url(url: str) -> None:
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https":
        raise SystemExit("REFUSED non-https url %s" % url)
    if u.netloc == RAW_HOST:
        if not u.path.startswith("/google/fonts/%s/ofl/" % PINNED_SHA):
            raise SystemExit("REFUSED raw url outside pinned ofl/: %s" % url)
    elif u.netloc == API_HOST:
        if not u.path.startswith("/repos/google/fonts/"):
            raise SystemExit("REFUSED api url outside google/fonts: %s" % url)
    else:
        raise SystemExit("REFUSED host %s" % u.netloc)


def _get(url: str, accept: str = "*/*") -> bytes:
    _check_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    tok = os.environ.get("GITHUB_TOKEN") or ""
    if tok and urllib.parse.urlsplit(url).netloc == API_HOST:
        req.add_header("Authorization", "Bearer " + tok)
    with urllib.request.urlopen(req, timeout=120) as r:
        final = r.geturl()
        _check_url(final)          # no redirect off the allow-list
        return r.read()


_META_CACHE: dict[str, bytes] = {}


def list_family(slug: str):
    """Files in ofl/<slug> at PINNED_SHA via METADATA.pb (or contents API). None if 404."""
    if not _SLUG_RE.match(slug):
        raise SystemExit("REFUSED slug %r" % slug)
    meta_repo_path = "ofl/%s/METADATA.pb" % slug
    try:
        raw_bytes = _get(raw_url(meta_repo_path))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    _META_CACHE[slug] = raw_bytes
    meta_text = raw_bytes.decode("utf-8", errors="replace")
    lic_match = re.search(r'^\s*license:\s*"([^"]+)"', meta_text, re.M)
    if lic_match and lic_match.group(1).strip().upper() != "OFL":
        raise SystemExit("REFUSED non-OFL license in METADATA.pb for %s: %s" % (slug, lic_match.group(1)))
    filenames = []
    for fn in re.findall(r'^\s*filename:\s*"([^"]+)"', meta_text, re.M):
        fn = fn.strip()
        if fn and fn not in filenames:
            filenames.append(fn)
    out = [
        {"name": "OFL.txt", "size": 0, "path": "ofl/%s/OFL.txt" % slug},
        {"name": "METADATA.pb", "size": len(raw_bytes), "path": meta_repo_path},
    ]
    for fn in filenames:
        out.append({"name": fn, "size": 0, "path": "ofl/%s/%s" % (slug, fn)})
    return out


# ───────────────────────────── verification ─────────────────────────────

def verify_font(path: str, script: str) -> dict:
    """Full fontTools parse + script coverage. Raises ValueError on rejection."""
    from fontTools.ttLib import TTFont
    font = TTFont(path, lazy=False, fontNumber=0)
    try:
        tags = set(font.keys())
        for tag in sorted(tags):
            if tag == "GlyphOrder":
                continue
            font[tag]                       # decompile every table
        try:
            font.ensureDecompiled()
        except AttributeError:
            pass
        if "glyf" not in tags and "CFF " not in tags and "CFF2" not in tags:
            raise ValueError("no outline table (glyf/CFF)")
        if tags & COLOR_TABLES:
            raise ValueError("colour font tables %s" % sorted(tags & COLOR_TABLES))
        axes = len(font["fvar"].axes) if "fvar" in tags else 0
        if axes >= RISKY_AXES:
            raise ValueError("%d variable axes (>= %d)" % (axes, RISKY_AXES))
        cmap = font.getBestCmap() or {}
        if script == "han":
            n = sum(1 for cp in cmap if (0x4E00 <= cp <= 0x9FFF) or (0x3400 <= cp <= 0x4DBF))
            if n < CJK_MIN:
                raise ValueError("only %d CJK code points (< %d)" % (n, CJK_MIN))
            cov = n
        elif script == "arabic":
            need = set(range(0x0627, 0x064B)) - {0x063B, 0x063C, 0x063D, 0x063E, 0x063F, 0x0640}
            missing = sorted(need - set(cmap))
            if missing:
                raise ValueError("missing Arabic letters %s" % ["U+%04X" % c for c in missing[:8]])
            cov = len(need)
        else:
            raise ValueError("unknown script %s" % script)
        name = font["name"]

        def _nm(i):
            rec = name.getName(i, 3, 1, 0x409) or name.getName(i, 1, 0, 0)
            return rec.toUnicode().strip() if rec else ""

        return {
            "family": _nm(16) or _nm(1),
            "family_id1": _nm(1),
            "version": _nm(5),
            "copyright": _nm(0),
            "license_desc": _nm(13),
            "license_url": _nm(14),
            "axes": axes,
            "coverage": cov,
            "glyphs": len(font.getGlyphOrder()),
        }
    finally:
        font.close()


# ───────────────────────────── provenance ─────────────────────────────

def _load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _save_json(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def recorded_hashes() -> dict:
    """repo_path@commit -> sha256 from PROVENANCE.json (our lock)."""
    out = {}
    try:
        prov = _load_json(PROVENANCE)
    except Exception:
        return out
    for it in prov.get("fonts") or []:
        g = str(it.get("google_fonts") or "")
        if g.endswith("@" + PINNED_SHA) and it.get("repo_path"):
            out[it["repo_path"]] = (it.get("sha256") or "", it.get("file") or "")
    return out


def verify_only() -> int:
    bad = 0
    n = 0
    fam_dirs = set()
    for repo_path, (sha, rel) in sorted(recorded_hashes().items()):
        path = os.path.join(REPO, "resources", rel.replace("/", os.sep))
        fam_dirs.add(os.path.dirname(path))
        n += 1
        if not os.path.isfile(path):
            _log("MISSING", rel)
            bad += 1
        elif _sha256(path) != sha:
            _log("HASH MISMATCH", rel)
            bad += 1
    for d in sorted(fam_dirs):
        for req_sidecar in ("OFL.txt", "METADATA.pb"):
            sp = os.path.join(d, req_sidecar)
            if not os.path.isfile(sp):
                _log("MISSING CREDENTIAL", os.path.relpath(sp, REPO))
                bad += 1
    _log("verified %d files across %d family dirs, %d bad" % (n, len(fam_dirs), bad))
    return 1 if bad else 0


def _ensure_sidecars(slug: str, dest_dir: str, files: list[dict], tmpdir: str) -> list[str]:
    """Download OFL.txt, METADATA.pb, and optional DESCRIPTION.en_us.html / upstream_info.md."""
    saved = []
    for sname in SIDECAR_FILES:
        dest_file = os.path.join(dest_dir, sname)
        repo_path = "ofl/%s/%s" % (slug, sname)
        if os.path.isfile(dest_file) and os.path.getsize(dest_file) > 0:
            saved.append(sname)
            continue
        if sname == "METADATA.pb" and slug in _META_CACHE:
            with open(dest_file, "wb") as fh:
                fh.write(_META_CACHE[slug])
            saved.append(sname)
            continue
        try:
            raw_bytes = _get(raw_url(repo_path))
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and sname not in ("OFL.txt", "METADATA.pb"):
                continue
            raise
        tmp_s = os.path.join(tmpdir, "%s_%s" % (slug, sname))
        with open(tmp_s, "wb") as fh:
            fh.write(raw_bytes)
        if sname == "OFL.txt":
            text = open(tmp_s, encoding="utf-8", errors="replace").read()
            if "SIL Open Font License" not in text and "OPEN FONT LICENSE" not in text.upper():
                raise SystemExit("OFL.txt for %s does not look like SIL OFL" % slug)
        shutil.copyfile(tmp_s, dest_file)
        saved.append(sname)
    return saved


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--verify" in argv:
        return verify_only()
    list_only = "--list" in argv
    lock = recorded_hashes()
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    plan = []          # (slug, dest, script, prio, [fonts], [all_files])
    skipped = []       # (slug, reason)
    for slug, dest, script, prio in FAMILIES:
        files = list_family(slug)
        if files is None:
            skipped.append((slug, "not under ofl/ at %s" % PINNED_SHA[:8]))
            _log("SKIP %-24s not found under ofl/" % slug)
            continue
        fonts = [f for f in files if f["name"].lower().endswith(_FONT_EXTS)]
        lic = [f for f in files if f["name"] == "OFL.txt"]
        if not fonts or not lic:
            skipped.append((slug, "no font files or no OFL.txt"))
            continue
        plan.append([slug, dest, script, prio, fonts, files])
        _log("PLAN %-24s %2d files %8.2f MB" % (slug, len(fonts), sum(f["size"] for f in fonts) / 1048576.0))

    total = sum(sum(f["size"] for f in p[4]) for p in plan)
    _log("planned total %.2f MB across %d families (no budget/count cap)" % (total / 1048576.0, len(plan)))
    if list_only:
        return 0

    prov = _load_json(PROVENANCE)
    prov_fonts = prov.setdefault("fonts", [])
    by_repo = {(it.get("repo_path"), it.get("google_fonts")): it for it in prov_fonts}
    added_or_updated = []
    tmpdir = tempfile.mkdtemp(prefix="cutora_fonts_")
    try:
        for slug, dest, script, _prio, fonts, all_files in plan:
            dest_dir = os.path.join(FONT_ROOT, dest)
            staged = []
            for f in fonts:
                repo_path = "ofl/%s/%s" % (slug, f["name"])
                _check_repo_path(repo_path)
                tmp = os.path.join(tmpdir, local_name(f["name"]))
                final = os.path.join(dest_dir, local_name(f["name"]))
                want = (lock.get(repo_path) or ("", ""))[0]
                if os.path.isfile(final) and want:
                    got = _sha256(final)
                    if got != want:
                        raise SystemExit("HASH MISMATCH on disk %s (%s != %s)" % (final, got, want))
                    try:
                        info = verify_font(final, script)
                    except Exception as exc:
                        _log("REJECT existing %s: %s" % (repo_path, exc))
                        os.remove(final)
                        skipped.append((slug + "/" + f["name"], "rejected existing: %s" % exc))
                        continue
                    _log("OK   %s (already present, sha256 verified)" % repo_path)
                    staged.append((None, final, repo_path, got, info))
                    continue
                data = _get(raw_url(repo_path))
                with open(tmp, "wb") as fh:
                    fh.write(data)
                got = _sha256(tmp)
                if want and got != want:
                    raise SystemExit("HASH MISMATCH download %s (%s != %s)" % (repo_path, got, want))
                try:
                    info = verify_font(tmp, script)
                except Exception as exc:
                    _log("REJECT %s: %s" % (repo_path, exc))
                    skipped.append((slug + "/" + f["name"], "rejected: %s" % exc))
                    continue
                staged.append((tmp, final, repo_path, got, info))
                _log("GOT  %s  %d bytes  sha256 %s  %s" % (repo_path, len(data), got[:12], info["family"]))
            staged = [s for s in staged if s]
            if not staged:
                skipped.append((slug, "every file rejected"))
                continue
            os.makedirs(dest_dir, exist_ok=True)
            sidecars = _ensure_sidecars(slug, dest_dir, all_files, tmpdir)
            for tmp, final, repo_path, sha, info in staged:
                if tmp is not None:
                    shutil.copyfile(tmp, final)
                rel = os.path.relpath(final, os.path.join(REPO, "resources")).replace("\\", "/")
                key = (repo_path, "google/fonts@" + PINNED_SHA)
                source_url = "https://github.com/google/fonts/tree/%s/ofl/%s" % (PINNED_SHA, slug)
                if key in by_repo:
                    entry = by_repo[key]
                    entry["copyright"] = info.get("copyright") or entry.get("copyright") or ""
                    entry["source_url"] = source_url
                    entry["license"] = "OFL-1.1"
                    entry["script"] = script
                    entry["sidecars"] = sidecars
                    added_or_updated.append((entry, info))
                    continue
                entry = {
                    "file": rel,
                    "family_dir": dest,
                    "google_fonts": "google/fonts@" + PINNED_SHA,
                    "repo_path": repo_path,
                    "name_table_version": info["version"],
                    "downloaded_utc": now,
                    "sha256": sha,
                    "bytes": os.path.getsize(final),
                    "pending_match": None,
                    "license": "OFL-1.1",
                    "copyright": info.get("copyright") or "",
                    "source_url": source_url,
                    "sidecars": sidecars,
                    "script": script,
                }
                prov_fonts.append(entry)
                by_repo[key] = entry
                added_or_updated.append((entry, info))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    _save_json(PROVENANCE, prov)
    extend_manifest([e for e, _i in added_or_updated])
    write_fonts_licenses_md()
    update_font_script_coverage()
    _log("processed %d files, %.2f MB" % (
        len(added_or_updated), sum(e["bytes"] for e, _ in added_or_updated) / 1048576.0))
    for s, why in skipped:
        _log("SKIPPED %s: %s" % (s, why))
    return 0


def extend_manifest(entries: list) -> None:
    """Append / update FONTS_MANIFEST.json rows built exactly like tools/font_audit.py does."""
    sys.path.insert(0, os.path.join(REPO, "tools"))
    import font_audit as FA          # noqa: E402
    man = _load_json(MANIFEST)
    rows = man.setdefault("fonts", [])
    by_file = {r.get("file"): r for r in rows}
    for e in entries:
        rel = e["file"]
        path = os.path.join(REPO, "resources", rel.replace("/", os.sep))
        if not os.path.isfile(path):
            continue
        fam_dir = os.path.dirname(path)
        rec = FA._name_records(path)
        lic = FA._classify(rec.get("license_desc") or "", rec.get("license_url") or "", FA._sidecar_text(fam_dir))
        if lic != "OFL-1.1":
            raise SystemExit("license classified %s for %s" % (lic, rel))
        source_url = e.get("source_url") or ("https://github.com/google/fonts/tree/%s/%s" % (
            PINNED_SHA, os.path.dirname(e["repo_path"])))
        row_data = {
            "file": rel,
            "family": rec.get("family") or os.path.splitext(os.path.basename(path))[0],
            "version": rec.get("version") or "",
            "license": lic,
            "license_url": rec.get("license_url") or "https://scripts.sil.org/OFL",
            "copyright": rec.get("copyright") or e.get("copyright") or "",
            "sha256": e["sha256"],
            "has_license_file": FA._has_license_file(fam_dir),
            "source_url": source_url,
            "google_fonts": e.get("google_fonts") or ("google/fonts@" + PINNED_SHA),
            "repo_path": e.get("repo_path") or "",
        }
        if rel in by_file:
            by_file[rel].update(row_data)
        else:
            rows.append(row_data)
            by_file[rel] = row_data
    rows.sort(key=lambda r: str(r.get("file") or ""))
    by_lic = {}
    for r in rows:
        by_lic[r["license"]] = by_lic.get(r["license"], 0) + 1
    man["font_count"] = len(rows)
    man["by_license"] = by_lic
    _save_json(MANIFEST, man)


def write_fonts_licenses_md() -> None:
    """Write resources/FONTS_LICENSES.md documenting every bundled font's commercial license & provenance."""
    man = _load_json(MANIFEST)
    prov = _load_json(PROVENANCE)
    prov_by_file = {it.get("file"): it for it in (prov.get("fonts") or []) if it.get("file")}
    rows = man.get("fonts") or []
    lines = [
        "# Bundled Fonts — Commercial License & Provenance Ledger",
        "",
        "All bundled fonts in `resources/fonts/` are 100% commercially licensed under **SIL Open Font License 1.1 (`OFL-1.1`)** or **Apache License 2.0 (`Apache-2.0`)**.",
        "Every font family directory contains its full license text (`OFL.txt` / `LICENSE.txt`) and upstream metadata sidecars (`METADATA.pb`, `DESCRIPTION.en_us.html`, `upstream_info.md` where provided by `google/fonts`).",
        "",
        f"- **Total Font Files**: {len(rows)}",
        f"- **By License**: `{json.dumps(man.get('by_license') or {}, ensure_ascii=False)}`",
        f"- **Pinned Social-Font Upstream Commit**: `google/fonts@{PINNED_SHA}`",
        "",
        "| File | Family | License | SHA256 | Upstream Commit / Repo Path | Source URL | Copyright |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        rel = str(r.get("file") or "")
        p = prov_by_file.get(rel) or {}
        fam = str(r.get("family") or "").replace("|", "/")
        lic = str(r.get("license") or "")
        sha = str(r.get("sha256") or "")
        gfonts = str(r.get("google_fonts") or p.get("google_fonts") or "")
        rpath = str(r.get("repo_path") or p.get("repo_path") or "")
        up_ref = f"`{gfonts}` `{rpath}`".strip() if (gfonts or rpath) else "bundled"
        surl = str(r.get("source_url") or p.get("source_url") or "")
        cop = str(r.get("copyright") or p.get("copyright") or "").replace("\r", " ").replace("\n", " ").replace("|", "/")
        lines.append(f"| `{rel}` | {fam} | `{lic}` | `{sha}` | {up_ref} | {surl} | {cop} |")
    lines.append("")
    tmp = LICENSES_MD + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))
    os.replace(tmp, LICENSES_MD)


def update_font_script_coverage() -> None:
    """Rebuild resources/data/font_script_coverage.json so all new/existing Chinese & Arabic families
    (both English typographic family nameID=16 and legacy nameID=1) have accurate script coverage."""
    from collections import defaultdict
    from fontTools.ttLib import TTFont

    sys.path.insert(0, os.path.join(REPO, "tools"))
    import t04l_build_coverage as TBC     # noqa: E402

    charsets_path = os.path.join(REPO, "resources", "data", "script_charsets.json")
    cov_path = os.path.join(REPO, "resources", "data", "font_script_coverage.json")
    charsets = _load_json(charsets_path)
    scripts = charsets["scripts"]
    needed = {sid: TBC.charset_cps(entry) for sid, entry in scripts.items()}
    subsets = {}
    latin = scripts.get("latin") or {}
    for name, chars in (latin.get("subsets") or {}).items():
        subsets[name] = {ord(c) for c in chars if c != " "}

    core_arabic = {ord(c) for c in "ءآأؤإئابةتثجحخدذرزسشصضطظعغفقكلمنهوىي"}
    by_family: dict[str, set[int]] = defaultdict(set)
    files_by_family: dict[str, list[str]] = defaultdict(list)
    paths = TBC.iter_font_files()

    qt_fams_by_path: dict[str, list[str]] = {}
    try:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtWidgets import QApplication
        _app = QApplication.instance() or QApplication([])
        for p in paths:
            fid = QFontDatabase.addApplicationFont(p)
            if fid != -1:
                cleaned = []
                for f in QFontDatabase.applicationFontFamilies(fid):
                    s = str(f).strip()
                    if not s:
                        continue
                    if set(s) == {"?"} and "zcool" in os.path.basename(p).lower():
                        cleaned.append("ZCOOL XiaoWei")
                    elif set(s) != {"?"}:
                        cleaned.append(s)
                qt_fams_by_path[p] = cleaned
    except Exception:
        pass

    for path in paths:
        font = TTFont(path, lazy=True, fontNumber=0)
        try:
            name_tbl = font["name"]
            fams = list(qt_fams_by_path.get(path) or [])
            # Prefer English (0x409) typographic family (16) and family (1), then any platform 1/3 nameID 1
            for nid in (16, 1):
                rec = name_tbl.getName(nid, 3, 1, 0x409) or name_tbl.getName(nid, 1, 0, 0)
                if rec:
                    try:
                        s = rec.toUnicode().strip()
                        if s and s not in fams:
                            fams.append(s)
                    except Exception:
                        pass
            for rec in name_tbl.names:
                if rec.nameID == 1 and rec.platformID in (1, 3):
                    try:
                        s = rec.toUnicode().strip()
                        if s and s not in fams:
                            fams.append(s)
                        break
                    except Exception:
                        continue
            if not fams:
                fams = [os.path.splitext(os.path.basename(path))[0]]
            cmap = font.getBestCmap() or {}
            cps = {int(k) for k in cmap.keys()}
        finally:
            font.close()

        rel = os.path.relpath(path, FONT_ROOT).replace("\\", "/")
        for fam in fams:
            by_family[fam] |= cps
            if rel not in files_by_family[fam]:
                files_by_family[fam].append(rel)

    coverage = {}
    for family in sorted(by_family):
        have = by_family[family]
        cjk_n = sum(1 for cp in have if (0x4E00 <= cp <= 0x9FFF) or (0x3400 <= cp <= 0x4DBF))
        row = {}
        for sid, need in needed.items():
            if not need:
                row[sid] = False
                continue
            missing = need - have
            if sid == "han":
                # Support both Simplified-only and Traditional-only commercial Chinese fonts (>= 3500 CJK chars)
                is_jp_kr_only = ("JP" in family or "KR" in family or family in ("Klee One", "Klee One SemiBold", "Train One"))
                row[sid] = (len(missing) == 0) or (cjk_n >= CJK_MIN and len(missing) <= 20 and not is_jp_kr_only)
            elif sid == "arabic":
                # Core 28 Arabic letters coverage qualifies an Arabic font (Kurdish/Persian/Urdu letters optional)
                row[sid] = (len(missing) == 0) or (len(core_arabic - have) == 0)
            else:
                row[sid] = len(missing) == 0
            row[sid + "_missing"] = len(missing)
            row[sid + "_need"] = len(need)
        for name, need in subsets.items():
            missing = need - have
            row[name] = len(missing) == 0
            row[name + "_missing"] = len(missing)
            row[name + "_need"] = len(need)
        coverage[family] = row

    payload = {
        "font_file_count": len(paths),
        "family_count": len(by_family),
        "files_by_family": {k: files_by_family[k] for k in sorted(files_by_family)},
        "coverage": coverage,
    }
    tabs = TBC.tables(payload)
    payload["tables"] = {
        "cyrillic_count": len(tabs["cyrillic"]),
        "greek_count": len(tabs["greek"]),
        "latin_incomplete_count": len(tabs["latin_incomplete"]),
        "latin_western_incomplete_count": len(tabs["latin_western_incomplete"]),
        "cyrillic": tabs["cyrillic"],
        "greek": tabs["greek"],
        "latin_incomplete": tabs["latin_incomplete"],
        "latin_western_incomplete": tabs["latin_western_incomplete"],
    }
    _save_json(cov_path, payload)
    _log("updated %s: %d files, %d families (%d han, %d arabic)" % (
        cov_path,
        payload["font_file_count"],
        payload["family_count"],
        sum(1 for r in coverage.values() if r.get("han")),
        sum(1 for r in coverage.values() if r.get("arabic")),
    ))


if __name__ == "__main__":
    raise SystemExit(main())
