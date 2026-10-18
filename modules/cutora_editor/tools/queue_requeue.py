#!/usr/bin/env python
"""Put a quarantined task back in the queue - safely, while the runner is live.

    python tools\queue_requeue.py Q17 [Q23 ...]

It never touches the queue file directly.  It drops a file in
docs/plan/queue/_inbox/ which the runner drains at the top of its next
iteration, so line 1 (the task being worked on right now) is never disturbed.
It also clears the task's .stop marker, because a stale marker would make the
runner quarantine the retry the moment it finishes.

It refuses to requeue a task whose report is still unruled: write
docs/plan/queue/_blocked/<Qnn>.ruling.md first, so the next Grok run has an
answer to work from instead of walking into the same wall.  --force overrides.
"""
from __future__ import annotations

import argparse
import datetime as _dt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QDIR = ROOT / "docs" / "plan" / "queue"
QBLOCK = QDIR / "_blocked"
QINBOX = QDIR / "_inbox"
QDONE = QDIR / "_done"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="requeue a quarantined CUTORA task")
    ap.add_argument("names", nargs="+", help="task numbers, e.g. Q17")
    ap.add_argument("--force", action="store_true",
                    help="requeue even without a ruling file")
    args = ap.parse_args(argv)

    QINBOX.mkdir(parents=True, exist_ok=True)
    specs = []
    for raw in args.names:
        name = raw.strip().upper()
        brief = QDIR / ("%s.md" % name)
        if not brief.exists():
            print("  ! no brief: %s" % brief)
            return 1
        ruling = QBLOCK / ("%s.ruling.md" % name)
        if not ruling.exists() and not args.force:
            print("  ! %s has no ruling yet (%s)." % (name, ruling))
            print("    Write the ruling first, or pass --force.")
            return 1
        stop = QDONE / ("%s.stop" % name)
        if stop.exists():
            stop.rename(QBLOCK / ("%s.stop.%s" % (
                name, _dt.datetime.now().strftime("%Y%m%d-%H%M%S"))))
            print("  cleared stale marker: %s" % stop.name)
        specs.append("docs\\plan\\queue\\%s.md" % name)

    out = QINBOX / ("requeue-%s.txt" % _dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    out.write_text("\n".join(specs) + "\n", encoding="utf-8")
    print("  queued at the tail: %s" % ", ".join(specs))
    print("  (the runner picks it up after the current task finishes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
