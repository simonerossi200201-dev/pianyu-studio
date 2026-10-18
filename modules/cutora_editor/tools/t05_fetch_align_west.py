# -*- coding: utf-8 -*-
"""Download fr/es/de/pt align models via catalog pin (T05 1b)."""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

IDS = ("align-fr", "align-es", "align-de", "align-pt")


def main() -> int:
    from cutora.asr.download import download_model
    from cutora.asr.models import get_model, reset_catalog, verify_model
    reset_catalog()
    for mid in IDS:
        row = get_model(mid)
        if row is None:
            print("MISSING_CATALOG", mid)
            return 1
        print("START", mid, row.get("repo"), row.get("revision"))
        rec = download_model(mid)
        status = verify_model(get_model(mid))
        print("DONE", mid, status, rec.get("dir"))
        if status != "ready":
            return 1
    print("ALL_READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
