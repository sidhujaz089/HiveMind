#!/usr/bin/env python3
"""lane_status.build_progress counts BUILDS as builds, and nothing else.

2026-10-01: the tick's `queued` list held 138 script reviews and 40 RCAs and no build, and
the report said "BUILDS TODAY 1/178 ... 177 waiting, ETA ~21h41m" -- every deferred job timed
at the median BUILD. Jas read the backlog as about a month of model time. These cases pin the
count: other kinds are reported beside it, never inside it.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import lane_status as LS   # noqa: E402

FAILED = 0


def check(label, got, want):
    global FAILED
    ok = got == want
    FAILED += not ok
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: got {got!r}, want {want!r}")


def ledger(rows):
    f = Path(tempfile.mkdtemp()) / "dispatch-loop.jsonl"
    f.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return f


def main():
    tick = {"event": "tick", "at": "2026-10-01T02:00:00Z", "skipped": [],
            "queued": [{"kind": "script-review", "key": f"s{i}", "why": "queued"} for i in range(5)]
            + [{"kind": "rca", "key": "r1", "why": "queued"},
               {"kind": "production", "key": "p1", "why": "queued"}]}
    bp = LS.build_progress(ledger([tick]))
    check("only the production item is a waiting build", bp["waiting"], 1)
    check("the other kinds are reported beside it", bp["other_queued"],
          {"script-review": 5, "rca": 1})
    tick["queued"] = tick["queued"][:6]
    bp = LS.build_progress(ledger([tick]))
    check("no production queued -> zero builds waiting", bp["waiting"], 0)
    print("test_lane_status_build_count:", "FAILED" if FAILED else "ok")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
