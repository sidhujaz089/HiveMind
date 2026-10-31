#!/usr/bin/env python3
"""mf7 oracle: the board stamps who posted, and the lane comes from the poster's identity.

Seen: a post from one lane was recorded under another, because lanes.py takes the lane as
an argument -- whatever the caller typed is who the board says posted.

Pinned as behaviour (all against a temp LANES_HOME; the live board is never touched):
- every appended row carries a `poster` stamp (tool@host), whatever else it says;
- with HIVEMIND_LANE set, a lane argument that disagrees is REFUSED, an agreeing or
  matching one is recorded under the identity lane;
- with no HIVEMIND_LANE (a human at a prompt), the argument stands -- existing callers and
  suites keep working -- and the poster stamp still says who really posted.

RED before the fix: lanes has no poster()/resolve_lane(), rows carry no stamp, and a
disagreeing lane argument is accepted.
"""
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="mf7-lanes-")
os.environ["LANES_HOME"] = _tmp
os.environ.pop("HIVEMIND_LANE", None)
os.environ["HIVEMIND_TOOL"] = "mf7-oracle"

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import lanes  # noqa: E402

FAILS = []


def check(label, cond, detail=""):
    line = "  [%s] %s" % ("ok" if cond else "BAD", label)
    if not cond and detail:
        line += " -- " + detail
    print(line)
    if not cond:
        FAILS.append(label)


def run(args):
    out = io.StringIO()
    try:
        with redirect_stdout(out):
            lanes.main(args)
    except SystemExit as e:
        return (e.code if isinstance(e.code, int) else 1), out.getvalue(), str(e)
    return 0, out.getvalue(), ""


def board_rows():
    p = Path(_tmp) / "board.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] \
        if p.is_file() else []


def main():
    print("mf7 oracle: the board stamps who posted\n")

    has = hasattr(lanes, "poster") and hasattr(lanes, "resolve_lane")
    check("lanes.poster and lanes.resolve_lane exist", has,
          "the lane is still whatever the caller typed")

    rc, _o, _e = run(["post", "prod-gx10", "fixture status -- DONE -- abc123"])
    check("a post with no identity env still lands (legacy callers keep working)", rc == 0,
          f"rc={rc}: {_e}")
    rows = board_rows()
    check("the row carries a poster stamp (tool@host)",
          rows and "@" in str(rows[-1].get("poster", "")),
          "last row: %r" % (rows[-1] if rows else None,))
    check("  ...naming the tool", rows and "mf7-oracle" in str(rows[-1].get("poster", "")),
          "poster=%r" % (rows[-1].get("poster") if rows else None,))

    os.environ["HIVEMIND_LANE"] = "prod-gx10"
    try:
        rc, _o, err = run(["post", "another-lane", "spoofed status"])
        check("with an identity set, a DISAGREEING lane argument is refused", rc != 0,
              "the spoof was recorded")
        check("  ...and the refusal names both lanes",
              "prod-gx10" in err and "another-lane" in err, repr(err)[:140])
        check("  ...and nothing was appended",
              all(r.get("text") != "spoofed status" for r in board_rows()))

        rc, _o, _e = run(["post", "prod-gx10", "honest status -- DONE -- def456"])
        check("an AGREEING lane argument passes", rc == 0, _e)
        check("  ...recorded under the identity lane",
              board_rows()[-1].get("lane") == "prod-gx10",
              "%r" % (board_rows()[-1],))

        rc, _o, err = run(["claim", "another-lane", "scripts/x.py", "why"])
        check("a claim under a disagreeing lane is refused too", rc != 0, "claim spoofed")
    finally:
        os.environ.pop("HIVEMIND_LANE", None)

    n = len(FAILS)
    print("\n%d failed -- %s" % (n, "the board stamps who posted"
                                 if not n else "mf7 is not in place"))
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
