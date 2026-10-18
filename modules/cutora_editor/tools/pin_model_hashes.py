# -*- coding: utf-8 -*-
"""Pin model revision + per-file SHA256 from HuggingFace official metadata.

Hashes come from the publisher:
  GET https://huggingface.co/api/models/{repo}/tree/{revision}?expand=1
  LFS files: entry['lfs']['oid'] is the content SHA256 (source=hf-lfs-oid).
  Non-LFS (config.json etc.): git blob SHA1 is not SHA256 — download at the
  pinned revision and compute SHA256 (source=computed).

Usage:
  runtime/venv/Scripts/python.exe tools/pin_model_hashes.py
  runtime/venv/Scripts/python.exe tools/pin_model_hashes.py --verify-id asr-large-v3-turbo
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from cutora.asr.models import (  # noqa: E402
    hf_file_url, models_json_path, sha256_file,
)
from cutora.core.net import download_to_file, request  # noqa: E402

HF_API = "https://huggingface.co/api/models"
ALIGN_JSON = os.path.join(REPO_ROOT, "resources", "data", "align_models.json")


def _get_json(url: str):
    status, body, _hdrs = request(url, purpose="model", timeout=120)
    if status != 200:
        raise RuntimeError("HTTP %s for %s" % (status, url))
    return json.loads(body.decode("utf-8"))


def fetch_revision(repo: str) -> str:
    data = _get_json("%s/%s" % (HF_API, repo))
    sha = str(data.get("sha") or "").strip()
    if not sha:
        raise RuntimeError("no sha for %s" % repo)
    return sha


def fetch_tree(repo: str, revision: str) -> dict:
    url = "%s/%s/tree/%s?expand=1" % (HF_API, repo, revision)
    rows = _get_json(url)
    out = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if row.get("type") and row.get("type") != "file":
            continue
        path = row.get("path") or row.get("rfilename")
        if not path:
            continue
        out[str(path)] = row
    return out


def fetch_license(repo: str) -> tuple[str, bool]:
    data = _get_json("%s/%s" % (HF_API, repo))
    card = data.get("cardData") or {}
    lic = str(card.get("license") or data.get("license") or "").strip()
    gated = bool(data.get("gated"))
    return lic, gated


ALIGN_FILE_CANDIDATES = (
    "config.json", "preprocessor_config.json", "pytorch_model.bin",
    "model.safetensors", "model.bin",
    "vocab.json", "tokenizer.json", "tokenizer_config.json",
    "special_tokens_map.json", "alphabet.json", "added_tokens.json",
)


def _file_names(row: dict, tree: dict | None = None) -> list[str]:
    files = list(row.get("files") or [])
    names = []
    for item in files:
        if isinstance(item, dict):
            name = str(item.get("filename") or item.get("path") or "").strip()
        else:
            name = str(item).strip()
        if name:
            names.append(name)
    weight = str(row.get("weight_file") or "").strip()
    if weight and weight not in names:
        names.append(weight)
    if (not files or row.get("kind") == "align") and tree:
        for cand in ALIGN_FILE_CANDIDATES:
            if cand in tree and cand not in names:
                names.append(cand)
    if tree:
        names = [n for n in names if n in tree]
    return names


def pin_one_file(repo: str, revision: str, filename: str, tree: dict, tmp: str) -> dict:
    meta = tree.get(filename) or {}
    lfs = meta.get("lfs") if isinstance(meta.get("lfs"), dict) else None
    size = int((lfs or {}).get("size") or meta.get("size") or 0)
    if lfs and lfs.get("oid"):
        oid = str(lfs["oid"]).strip().lower()
        if len(oid) == 64:
            return {
                "filename": filename,
                "size": size,
                "sha256": oid,
                "source": "hf-lfs-oid",
            }
    dest = os.path.join(tmp, filename.replace("/", os.sep))
    os.makedirs(os.path.dirname(dest) or tmp, exist_ok=True)
    url = hf_file_url(repo, filename, revision=revision)
    download_to_file(url, dest, resume=True, purpose="model")
    digest = sha256_file(dest)
    size = os.path.getsize(dest)
    return {
        "filename": filename,
        "size": size,
        "sha256": digest,
        "source": "computed",
    }


def pin_local_row(row: dict) -> dict:
    from cutora.asr.models import models_dir
    folder = os.path.join(models_dir(), str(row.get("dirname") or row.get("id")))
    weight = str(row.get("weight_file") or "")
    path = os.path.join(folder, weight) if weight else ""
    if not path or not os.path.isfile(path):
        return row
    digest = sha256_file(path)
    out = dict(row)
    out["files"] = [{
        "filename": weight,
        "size": os.path.getsize(path),
        "sha256": digest,
        "source": "computed",
    }]
    out["sha256"] = digest
    return out


def pin_model_row(row: dict, tmp: str) -> dict:
    from cutora.asr.models import is_license_allowed
    if row.get("local_only") or not row.get("repo"):
        return pin_local_row(row)
    repo = str(row.get("repo"))
    if row.get("enabled") is False or not is_license_allowed(row.get("license")):
        try:
            out = dict(row)
            out["revision"] = fetch_revision(repo)
            return out
        except Exception as exc:
            print(" skip gated/disabled", row.get("id"), exc)
            return row
    revision = fetch_revision(repo)
    tree = fetch_tree(repo, revision)
    names = _file_names(row, tree)
    weight = str(row.get("weight_file") or "model.bin")
    if weight not in tree:
        for cand in ("pytorch_model.bin", "model.safetensors", "model.bin"):
            if cand in tree:
                weight = cand
                break
    if weight in tree and weight not in names:
        names.append(weight)
    if not names:
        raise RuntimeError("no files to pin for %s" % row.get("id"))
    files = [pin_one_file(repo, revision, name, tree, tmp) for name in names]
    out = dict(row)
    out["revision"] = revision
    out["files"] = files
    out["weight_file"] = weight
    for item in files:
        if item["filename"] == weight:
            out["sha256"] = item["sha256"]
            if item.get("size"):
                out["size_mb"] = int(round(int(item["size"]) / (1024 * 1024)))
            break
    return out


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _save(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def pin_catalog(path: str, tmp: str) -> dict:
    data = _load(path)
    models = []
    for row in list(data.get("models") or []):
        if not isinstance(row, dict):
            continue
        print("pin", row.get("id"), row.get("repo") or "(local)")
        models.append(pin_model_row(row, tmp))
    data["models"] = models
    _save(path, data)
    return data


def verify_download(row: dict, dest_dir: str) -> list[dict]:
    from cutora.asr.models import verify_weight
    repo = str(row.get("repo") or "")
    revision = str(row.get("revision") or "")
    if not repo or not revision:
        raise RuntimeError("model %s missing repo/revision" % row.get("id"))
    os.makedirs(dest_dir, exist_ok=True)
    results = []
    for item in list(row.get("files") or []):
        name = str(item.get("filename") or "")
        want = str(item.get("sha256") or "")
        dest = os.path.join(dest_dir, name)
        print("download", name, "->", dest)
        url = hf_file_url(repo, name, revision=revision)
        download_to_file(url, dest, resume=True, purpose="model")
        status = verify_weight(dest, want)
        got = sha256_file(dest)
        rec = {
            "filename": name,
            "expected": want,
            "got": got,
            "source": item.get("source"),
            "status": status,
            "bytes": os.path.getsize(dest),
        }
        print(" ", status, name, "source=%s" % item.get("source"))
        if status != "ready":
            raise RuntimeError("SHA256 mismatch for %s: expected %s got %s" % (
                name, want, got))
        results.append(rec)
    return results


def _find_row(data: dict, model_id: str) -> dict | None:
    for row in data.get("models") or []:
        if row.get("id") == model_id:
            return row
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-id", default="", help="download+verify this id")
    parser.add_argument("--skip-align", action="store_true")
    args = parser.parse_args(argv)
    tmp = tempfile.mkdtemp(prefix="cutora_pin_")
    print("pin", models_json_path())
    models = pin_catalog(models_json_path(), tmp)
    if os.path.isfile(ALIGN_JSON) and not args.skip_align:
        print("pin", ALIGN_JSON)
        pin_catalog(ALIGN_JSON, tmp)
    verify_id = (args.verify_id or "").strip()
    if verify_id:
        row = _find_row(models, verify_id)
        if row is None and os.path.isfile(ALIGN_JSON):
            row = _find_row(_load(ALIGN_JSON), verify_id)
        if row is None:
            raise SystemExit("unknown id %s" % verify_id)
        dest = os.path.join(tmp, "verify", verify_id)
        recs = verify_download(row, dest)
        report = os.path.join(REPO_ROOT, "docs", "plan", "reports", "T12c_turbo_verify.json")
        os.makedirs(os.path.dirname(report), exist_ok=True)
        with open(report, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"id": verify_id, "files": recs}, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        print("wrote", report)
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
