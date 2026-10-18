# -*- coding: utf-8 -*-
"""Retired. T04f-6b3: record one golden case per process.

The old body shared one boot_host() across g01–g07/g09. That is the same
shape as tools/t04f6b2_record_g08_g10.py, which wrote g10's polluted
147f58ee29d4. See tests/golden/README.md.
"""


def main() -> int:
    print(
        "refusing shared-host re-record; one case per process "
        "(tests/golden/README.md, T04f-6b3)",
        flush=True,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
