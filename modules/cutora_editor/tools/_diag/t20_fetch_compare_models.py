# -*- coding: utf-8 -*-
"""Download comparison-table weights. Product default is not swapped."""
from __future__ import annotations

import json
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
os.chdir(REPO)

LOG = os.path.join(REPO, "docs", "plan", "reports", "model_compare_work", "fetch.log")


def _log(msg: str) -> None:
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    line = msg.rstrip() + "\n"
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line)
    print(msg, flush=True)


def fetch_catalog(mid: str) -> None:
    from cutora.asr.download import download_model
    from cutora.asr.models import get_model, status_row
    row = get_model(mid)
    if row is None:
        raise SystemExit("unknown %s" % mid)
    st = status_row(row)
    if st.get("status") == "ready":
        _log("READY %s %s" % (mid, st.get("path")))
        return
    _log("DOWNLOAD %s" % mid)
    info = download_model(mid)
    _log("DONE %s %s" % (mid, json.dumps(info, ensure_ascii=False)[:400]))


def pin_and_fetch_xlsr() -> None:
    from cutora.asr.download import download_hf_file
    from cutora.asr.models import get_model, model_folder, reset_catalog
    from tools.pin_model_hashes import fetch_tree, pin_one_file

    reset_catalog()
    row = get_model("align-it-xlsr")
    if row is None:
        raise SystemExit("align-it-xlsr missing from catalog")
    repo = row["repo"]
    revision = row.get("revision") or "main"
    folder = model_folder(row)
    os.makedirs(folder, exist_ok=True)
    weight = os.path.join(folder, str(row.get("weight_file") or "pytorch_model.bin"))
    if os.path.isfile(weight) and os.path.getsize(weight) > 1_000_000:
        _log("READY align-it-xlsr %s" % weight)
        return
    _log("PIN+DOWNLOAD align-it-xlsr %s@%s" % (repo, revision))
    tree = fetch_tree(repo, revision)
    names = []
    for item in row.get("files") or []:
        names.append(item["filename"] if isinstance(item, dict) else str(item))
    tmp = tempfile.mkdtemp(prefix="pin_xlsr_")
    for name in names:
        dest = os.path.join(folder, name)
        if os.path.isfile(dest) and os.path.getsize(dest) > 0:
            _log("  have %s" % name)
            continue
        meta = pin_one_file(repo, revision, name, tree, tmp)
        sha = meta.get("sha256")
        _log("  fetch %s sha=%s" % (name, (sha or "")[:12]))
        download_hf_file(repo, name, dest, revision=revision, expected_sha256=sha)
    _log("DONE align-it-xlsr %s" % folder)


def fetch_mms() -> None:
    """CTC forced-aligner built on MMS-300M (CC-BY-NC, internal use).

    facebook/mms-1b-all is adapter-sharded (1000+ files, paginated tree) and
    is the wrong shape for a drop-in wav2vec2 align. This checkpoint is the
    alignment-specific CTC head.
    """
    from cutora.asr.download import download_hf_file
    from cutora.core.paths import models_root
    from tools.pin_model_hashes import fetch_revision, fetch_tree, pin_one_file

    repo = "MahmoudAshraf/mms-300m-1130-forced-aligner"
    folder = os.path.join(models_root(), "align_mms")
    os.makedirs(folder, exist_ok=True)
    marker = os.path.join(folder, "model.safetensors")
    if os.path.isfile(marker) and os.path.getsize(marker) > 1_000_000_000:
        _log("READY align_mms %s" % folder)
        return
    _log("DOWNLOAD %s -> %s" % (repo, folder))
    revision = fetch_revision(repo)
    tree = fetch_tree(repo, revision)
    names = []
    for cand in (
        "config.json", "preprocessor_config.json", "tokenizer_config.json",
        "vocab.json", "special_tokens_map.json", "alphabet.json",
        "model.safetensors", "pytorch_model.bin", "model.bin",
    ):
        if cand in tree:
            names.append(cand)
    # language adapters live under adapter_files / or lang-specific
    for extra in list(tree):
        low = extra.lower()
        if extra.endswith(".safetensors") or extra.endswith(".bin"):
            if any(tag in low for tag in ("/eng/", "/ita/", "eng.", "ita.", "adapter.eng", "adapter.ita")):
                names.append(extra)
        if extra in ("eng.pt", "ita.pt", "adapter_eng.bin", "adapter_ita.bin"):
            names.append(extra)
    # HF MMS stores adapters as lang_code/ directories sometimes
    for lang in ("eng", "ita"):
        for cand in (
            "%s.safetensors" % lang,
            "adapters/%s.safetensors" % lang,
            "%s/model.safetensors" % lang,
        ):
            if cand in tree and cand not in names:
                names.append(cand)
    tmp = tempfile.mkdtemp(prefix="pin_mms_")
    _log("  revision %s files %s" % (revision[:12], names))
    for name in names:
        dest = os.path.join(folder, name.replace("/", os.sep))
        os.makedirs(os.path.dirname(dest) or folder, exist_ok=True)
        if os.path.isfile(dest) and os.path.getsize(dest) > 0:
            _log("  have %s" % name)
            continue
        meta = pin_one_file(repo, revision, name, tree, tmp)
        sha = meta.get("sha256")
        _log("  fetch %s sha=%s size=%s" % (name, (sha or "")[:12], meta.get("size")))
        download_hf_file(repo, name, dest, revision=revision, expected_sha256=sha)
    with open(os.path.join(folder, "REVISION.txt"), "w", encoding="utf-8") as fh:
        fh.write(revision + "\n")
    _log("DONE align_mms %s" % folder)


def main() -> int:
    want = sys.argv[1:] or ["asr-large-v3", "asr-large-v3-turbo", "xlsr", "mms"]
    for item in want:
        try:
            if item == "xlsr":
                pin_and_fetch_xlsr()
            elif item == "mms":
                fetch_mms()
            else:
                fetch_catalog(item)
        except Exception as exc:
            _log("FAIL %s %s" % (item, exc))
            return 1
    _log("ALL_OK %s" % want)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
