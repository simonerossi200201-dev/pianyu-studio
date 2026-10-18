# -*- coding: utf-8 -*-
"""Translate CLI --describe op param types into JSON Schema draft 2020-12.

Do not hand-copy batch.* command schemas; generate them from OPS_SPEC.
"""
from __future__ import annotations

import json
import sys


def main(argv=None) -> int:
    from cutora.batch.cli import OPS_SPEC
    from cutora.core.command_schema import ops_to_jsonschema

    payload = ops_to_jsonschema(OPS_SPEC)
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
