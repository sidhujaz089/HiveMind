#!/usr/bin/env python3
"""Chunks taken off the build line without editing scripts/autonomy_loop.sh (t1071, 2026-10-14).

    python3 scripts/withdrawals.py list

WHY A FILE OF ITS OWN. A held or parked chunk goes back to the scoper: its task is rescoped and gets
new chunks. Removing the old row from autonomy_loop.sh would be the obvious move, but that file is
tracked and the bridge MERGES into it automatically -- an uncommitted edit by the scheduler would make
every freeze refuse ("local changes would be overwritten"). So the scheduler records the withdrawal
here, and the two readers that decide what runs skip it: parallel_dispatch.chunks() (what a free model
may build) and manager_next.in_production() (whether the Manager may rescope the task).

workspace/run-logs/withdrawn-chunks.json: {chunk: {"task", "why", "at"}}. Never pruned automatically:
a withdrawn chunk stays withdrawn; its brief stays in workspace/briefs/ as the record.
"""
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FILE = REPO / "workspace" / "run-logs" / "withdrawn-chunks.json"


def load(path=None):
    p = Path(path or FILE)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except ValueError as e:
        print(f"  NOTE: {p.name} unreadable ({e}); no chunk counted as withdrawn", file=sys.stderr)
        return {}


def withdraw(chunk, task, why, path=None):
    p = Path(path or FILE)
    d = load(p)
    d[chunk] = {"task": task, "why": why, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1) + "\n", encoding="utf-8")
    tmp.replace(p)


def main(argv):
    if argv[:1] == ["list"]:
        for c, v in sorted(load().items()):
            print(f"  {v.get('at')}  {c}  {v.get('task')}  {v.get('why', '')}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
