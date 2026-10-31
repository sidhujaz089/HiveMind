#!/usr/bin/env python3
"""An objection that NAMES the oracle goes to Scoping with permission to revise it -- and the builder never gets it.

t9540 step 4, as revised by R2 (Jas: "the known cause so far is that the scoper cannot edit the oracle"). The
invariant that must survive: the SCOPING stage may revise an oracle, the BUILDING job never may -- auto_merge's
conditions 2 and 5 rest on "the oracle was written before the build, never by it".

This pins the classifier and the sentence it adds to the assignment, in both directions: evidence that names
the oracle gets the permission, evidence that does not must not.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def main():
    import dispatch_loop as D
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'FAIL'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    named = [
        "HELD FOR CLAUDE: Manager objected to this brief (workspace/preflight-objections.jsonl); the oracle cannot fail",
        "PARKED: 2 attempt(s) in a row came to nothing (exit 1; on swift). The oracle is the thing to fix",
        "HELD: red on main: scripts/tests/test_x.py is red on main",
        "the target suite passes already, so the test proves nothing",
    ]
    for t in named:
        check(f"names the oracle: {t[:52]}...", D.names_the_oracle(t), t)

    not_named = [
        "HELD FOR CLAUDE: Manager objected to this brief (workspace/preflight-objections.jsonl)",
        "PARKED: BLOCKED BY A HARNESS BUG: 1 failures on this brief were the harness's fault",
        "waiting for a free lane on Manager (Building)",
    ]
    for t in not_named:
        check(f"does NOT name it: {t[:52]}...", not D.names_the_oracle(t), t)

    check("several pieces of evidence are searched, not only the first",
          D.names_the_oracle("", "nothing here", "the oracle is wrong"), "")
    check("an empty hold names nothing", not D.names_the_oracle(None, ""), "")

    src = (ROOT / "scripts" / "dispatch_loop.py").read_text(encoding="utf-8")
    check("the Scoping assignment carries the permission sentence",
          "THE ORACLE IS NAMED IN THE EVIDENCE" in src, "the note does not grant oracle-write")
    check("  and it says the BUILDING job still may not",
          "the BUILDING job still may not edit it" in src, "")
    print("test_oracle_objection_routes_to_scoping:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
