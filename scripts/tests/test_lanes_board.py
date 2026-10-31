#!/usr/bin/env python3
"""The lanes board (scripts/lanes.py): claims expire, first claim wins, the board lives where
LANES_HOME says -- never in the repo. Every case runs against a temp board; the live board is never
touched (asserted at the end)."""
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LIVE = Path.home() / ".cache/hivemind-tmp/lanes/board.jsonl"
live_before = LIVE.stat().st_mtime if LIVE.exists() else None
tmp = tempfile.mkdtemp(prefix="lanes-test-")
os.environ["LANES_HOME"] = tmp
sys.path.insert(0, str(REPO / "scripts"))
import lanes  # noqa: E402

fails = 0


def check(label, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))


def refused(fn, *a):
    try:
        fn(*a)
    except SystemExit:
        return True
    return False


check("board is under LANES_HOME", str(lanes.BOARD).startswith(tmp), True)
lanes.claim("L1", "scripts/x.py", "fix")
check("first claim held", lanes.active_claims()["scripts/x.py"]["lane"], "L1")
check("second lane refused", refused(lanes.claim, "L3", "scripts/x.py", "mine"), True)
lanes.claim("L1", "scripts/x.py", "extend", 8)
check("same lane may re-claim", lanes.active_claims()["scripts/x.py"]["why"], "extend")
check("other lane cannot release", refused(lanes.release, "L2", "scripts/x.py"), True)
lanes.release("L1", "scripts/x.py")
check("released", "scripts/x.py" in lanes.active_claims(), False)

rs = [{"kind": "claim", "at": "2026-10-27T00:00:00Z", "lane": "L2", "what": "f", "why": "",
       "until": "2026-10-27T01:00:00Z"}]
later = lanes.parse("2026-10-27T02:00:00Z")
check("expired claim is not held", lanes.active_claims(rs, later), {})
rs.append({"kind": "claim", "at": "2026-10-27T01:30:00Z", "lane": "L4", "what": "f", "why": "",
           "until": "2026-10-27T05:00:00Z"})
check("after expiry the next lane takes it", lanes.active_claims(rs, later)["f"]["lane"], "L4")

# THE FOUR NAMES ARE RETIRED (Jas, 2026-10-30), so an unfamiliar label is no longer refused: a board that only
# admits one sprint's names cannot record the arrangement after it. What is still refused is a label that is not
# a label -- empty, with a space, or long enough to be a sentence.
check("an unfamiliar lane label is accepted now that L1..L4 are retired", lanes.lane_ok("L9"), "L9")
check("a nonsense lane name is still refused", refused(lanes.lane_ok, "not a lane"), True)
check("an empty lane name is still refused", refused(lanes.lane_ok, ""), True)
lanes.main(["ask", "L1", "L2", "is the lock in?"])
qs = lanes.open_questions()
check("question open", len(qs), 1)
lanes.main(["answer", "L2", qs[0]["qid"], "yes"])
check("question answered", lanes.open_questions(), [])
check("view rendered outside the repo", lanes.VIEW.exists() and REPO not in lanes.VIEW.parents, True)
with lanes.BOARD.open("a") as f:
    f.write("not json\n")
check("corrupt line reported, not skipped", any(r["kind"] == "corrupt" for r in lanes.rows()), True)
check("live board untouched", (LIVE.stat().st_mtime if LIVE.exists() else None), live_before)

print(f"\n{'OK' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
