#!/usr/bin/env python3
"""The preflight objection ledger: one row per review, whatever the verdict.

WHY. Jas, 2026-09-14: "When we have tasks that get objected to, is that logged
anywhere? I think that would be a great thing for future continuous improvement
projects with the harnesses." It was not. Objections went to stdout and died with
the terminal -- including four correct and DIFFERENT objections to t238's brief in
one day, which survive only because they were quoted by hand into commit messages.

THE ASSERTION THAT MATTERS MOST is that a SATISFIABLE review writes a row too.
t309 names it explicitly, and it is the one a careless implementation gets wrong,
because "log the objections" sounds like it means "log the objections". Without
the approvals there is no denominator: "objections went down" and "we stopped
asking" are the same picture. So this fixture pins BOTH, and a ledger that only
recorded failures would pass every other assertion here.

Second: the reply is stored WHOLE. The value of this corpus is the specific
wording of a gap -- "only 60000 of 92707 chars of coder.py are shown, so the
attempt counter is not visible" is useful; "brief incomplete" is not. A prefix
would satisfy a naive length check and destroy the thing being collected
(docs/skills/never-truncate-a-record). Asserted with a reply longer than any
plausible cut.

Third: a stated SATISFIABLE that was OVERRIDDEN by its own MISSING list must be
recorded as what it said, not as what was decided. That override is the exact
failure the gate exists to catch -- a model complying by inventing -- and a
record that files it as "NOT SATISFIABLE" loses it.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import preflight_brief as PB   # noqa: E402

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {str(got)[:60]}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {str(got)[:60]}")


def rows(path):
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def main():
    tmp = Path(tempfile.mkdtemp())
    led = tmp / "preflight-objections.jsonl"
    real, PB.OBJECTIONS = PB.OBJECTIONS, led

    LONG = ("The brief is not satisfiable. " + "x" * 9000 +
            " and the assertion at line 5 is underspecified.")
    try:
        # ---- a blocking review
        PB.record_review("workspace/briefs/b.txt", "brief one", ["scripts/error_scan.py"],
                         "scripts/tests/test_error_scan.py", "m", 12000,
                         "NOT SATISFIABLE", LONG, 61.0, "stop", True)
        # ---- an APPROVING review. The row that makes a rate computable.
        PB.record_review("workspace/briefs/b.txt", "brief two", ["scripts/error_scan.py"],
                         "scripts/tests/test_error_scan.py", "m", 12000,
                         "SATISFIABLE", "VERDICT: SATISFIABLE\nMISSING: nothing",
                         42.0, "stop", False)

        r = rows(led)
        report("both reviews wrote a row", len(r), 2)
        report("the approval is on disk, not only the objection",
               sorted(x["verdict"] for x in r), ["NOT SATISFIABLE", "SATISFIABLE"])
        report("a rate is computable from the file",
               sum(1 for x in r if x["stopped"]) / len(r), 0.5)

        obj = [x for x in r if x["stopped"]][0]
        report("the objection is stored whole", obj["reply"] == LONG, True)
        report("nothing was cut", len(obj["reply"]), len(LONG))
        report("the tail of the objection survived",
               obj["reply"].endswith("underspecified."), True)

        # ---- the hash is what makes "did the brief change" answerable
        report("a changed brief gets a different hash",
               r[0]["brief_sha256"] != r[1]["brief_sha256"], True)
        PB.record_review("workspace/briefs/b.txt", "brief two", ["scripts/error_scan.py"],
                         None, "m", 12000, "SATISFIABLE", "ok", 1.0, "stop", False)
        r = rows(led)
        report("the same brief asked twice gets the same hash",
               r[1]["brief_sha256"] == r[2]["brief_sha256"], True)

        # ---- file sizes, so a starved reviewer is visible in the record
        report("the file's size is recorded",
               obj["files"][0]["chars"] > 0, True)
        report("elision is recorded as a field, not inferred later",
               "elided" in obj["files"][0], True)

        # ---- the stated verdict is kept apart from the decision
        overridden = "VERDICT: SATISFIABLE\nMISSING: the target suite does not exist"
        report("decide() blocks an overridden approval", PB.decide(overridden), 2)
        report("the ledger records what was SAID, not what was decided",
               PB._verdict_of(overridden), "SATISFIABLE")
        report("a reply with no verdict line is named, not guessed",
               PB._verdict_of("I think it is fine"), "NO VERDICT LINE")

        # ---- a ledger failure must never take the reviewer down with it
        PB.OBJECTIONS = Path("/nonexistent-dir-xyz/nope/ledger.jsonl")
        try:
            PB.record_review("b", "t", [], None, "m", 1, "SATISFIABLE", "x",
                             1.0, "stop", False)
            crashed = False
        except Exception:                                        # noqa: BLE001
            crashed = True
        report("an unwritable ledger does not break the review", crashed, False)
    finally:
        PB.OBJECTIONS = real

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
