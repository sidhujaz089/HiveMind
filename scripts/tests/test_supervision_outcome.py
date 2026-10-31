#!/usr/bin/env python3
"""t750: a supervision row must record that a decision CLOSED, not that it was asked.

The ledger (workspace/supervision.jsonl) today records that a supervision decision was ATTEMPTED.
The readable path writes a row with 'decision' (scripts/supervise.py:409); the unreadable path
writes a row with 'outcome': 'unreadable' and NO 'decision' (:405). Both carry the same 'failure'
key, built by key() (:91-92) from the dispatch row's at|chunk|model at WRITE time (:401-403).
The two readers disagree about what such a row means:

  * pending() marks a failure decided from ANY row carrying its key --
    'decided = {s.get("failure") for s in supervision_rows}' (:108, used at :120) -- so every
    unreadable reply removes its failure from the queue for ever; nothing will ask again.
  * print_failures() counts 'kind == "attempt-failed" and not x["decision"]' (:184) for that very
    row, so the count never clears. That is the RCA's unresolved finding -- 11 attempt failures
    with no supervision decision, un-ownable, un-askable, reported the same way every session
    start (workspace/rca/0050360a93af.md) -- and the catalogue row (163-168) has no field that
    could tell them apart from failures nobody ever asked about.

WHAT THIS SUITE ASSERTS, at both endpoints:
  1  an UNREADABLE reply does not close its failure -- it stays askable;
  2  an unreadable row is not a decision (the catalogue's decision stays None);
  3  the catalogue row carries the OUTCOME, so the report can say WHICH failure is undecided;
  4  a failure never decided reports no outcome (None, not the string 'unreadable');
  5  a READABLE decision keeps closing its failure -- the fix is not 're-ask everything';
  6  the catalogue reports that decision ('SWITCH'), not an open count;
  7  a LEGACY row -- a 'failure' key and no 'outcome' field at all, the shape of every row
     already on disk -- keeps closing its failure, so the ledger is not re-asked. This is the
     same contract scripts/tests/test_supervise.py:57-58 asserts; it is the ratchet against a
     fix that can only see a row that carries 'outcome';
  8  the join key is RECOMPUTED from the dispatch row at read time, so a stored key in a format
     the dispatch row no longer produces (id-style '2026-10-07T210256Z' vs '2026-10-07T21:02:56Z')
     still closes the failure it decided -- in pending() (8) and in the catalogue (9);
 10  main()'s base row carries 'failure_at' -- the dispatch stamp the key is recomputed from;
 11  the readable write records 'outcome': 'readable';
 12  the unreadable write records 'outcome': 'unreadable' and NO 'decision' field.

WHY 10-12 READ THE SOURCE (ast.parse) INSTEAD OF RUNNING IT. The write site is inside main(), and
main() takes no arguments (scripts/supervise.py:360 'def main():'; argparse reads sys.argv at 361-369)
AND every path through it calls skills.with_skills (:394), which appends to skills.LOADS =
REPO/'workspace'/'skill-loads.jsonl' (scripts/skills.py:213, 307-311) -- unreachable by rebinding
supervise's own REPO/BRIEFS/LEDGER. Driving a production entry point from an oracle is the defect
scripts/tests/test_t1024_live_entry.py was minted from THIS task's refusal to pin (class
oracle-live-state, 'running it changed: workspace/skill-loads.jsonl'). So this file drives no entry
point, writes no file and creates no directory: assertions 1-9 call the two readers, which are pure
when both arguments are supplied (supervise.py:145-146), and 10-12 assert the three field names the
brief tells the builder to add, in the dicts main() passes to record(), read from the source with
ast.parse. Precedent for asserting a property of this file's source: scripts/tests/test_supervise.py
:112-119, which ast.parses it. 10-12 are shape, not behaviour, and are labelled as such; 8 and 9
pin the field's NAME and MEANING behaviourally, so a build that adds the field to the writer and
not the reader, or vice versa, fails.

No model is asked: nothing in this file imports settings/infer, and no subprocess is run.
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "projects" / "app-repo"))

import supervise as SV          # noqa: E402
import dispatch_loop as DL      # noqa: E402  failure_catalogue imports it at :144; imported here so
                                 # a missing module is a suite bug, not a late ImportError.

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {str(got)[:78]}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {str(got)[:78]}")


def dict_keys(d):
    """The literal keys of an ast.Dict (the key of a **spread is None)."""
    return {k for k in d.keys if isinstance(k, str)}


def literal(d, name):
    """The string a key is assigned in an ast.Dict, or None when absent / not a literal."""
    for k, v in zip(d.keys, d.values):
        if k == name:
            return v.value if isinstance(v, ast.Constant) else None
    return None


def main():
    # ---- the dispatch row a supervision row can speak for. 'at' is the stamp key() (91-92) uses.
    f = {"at": "T2", "event": "finished", "kind": "production", "chunk": "pA",
         "model": "q16", "brief": "b.txt", "brief_sha256": "sha1", "exit": 1}

    # (1)(2)(3) THE ROW THE UNREADABLE PATH WRITES TODAY (supervise.py:405): key, outcome, no decision.
    unreadable = {"at": "T2b", "failure": "T2|pA|q16", "failure_at": "T2", "chunk": "pA",
                  "model": "q16", "outcome": "unreadable", "reply": "I think switch"}
    report("(1) an UNREADABLE reply does not close the failure -- it stays askable",
           [x["at"] for x in SV.pending([f], [unreadable])], ["T2"])
    report("(2) an unreadable row is not a decision",
           SV.failure_catalogue([f], [unreadable])[0]["decision"], None)
    report("(3) and the report can say WHICH: the outcome the supervisor got",
           [x.get("outcome") for x in SV.failure_catalogue([f], [unreadable])], ["unreadable"])

    # (4) the population the RCA cannot see today: asked about, never answered, vs never asked.
    never = {**f, "at": "T9"}
    report("(4) a failure nobody ever asked about has no outcome to report",
           [x.get("outcome") for x in SV.failure_catalogue([f, never], [unreadable])
            if x["at"] == "T9"], [None])

    # (5)(6) THE ROW THE READABLE PATH WRITES TODAY (409) + the outcome the fix adds.
    readable = {"at": "T2b", "failure": "T2|pA|q16", "failure_at": "T2", "chunk": "pA",
                "model": "q16", "decision": "SWITCH", "outcome": "readable",
                "why": "model-specific"}
    report("(5) a READABLE decision does close it -- the fix is not 're-ask everything'",
           SV.pending([f], [readable]), [])
    report("(6) and the catalogue shows the decision it reached",
           SV.failure_catalogue([f], [readable])[0]["decision"], "SWITCH")

    # (7) Every row already in workspace/supervision.jsonl is this shape: a key, a decision, and
    # no 'outcome' field at all. Re-asking them IS the stale-error loop that rule 2 (supervise.py
    # :22-24) exists to stop, and scripts/tests/test_supervise.py:57-58 asserts this same row.
    legacy = {"at": "T2b", "failure": "T2|pA|q16", "chunk": "pA", "model": "q16",
              "decision": "SWITCH"}
    report("(7) a LEGACY row (no outcome, no failure_at) still closes its failure",
           SV.pending([f], [legacy]), [])

    # (8)(9) THE JOIN IS RECOMPUTED, NOT TRUSTED. The stored 'failure' string is in the id-style
    # stamp format (see t750's own 'created: 2026-10-07T210256Z'); the dispatch row's 'at' is the
    # ledger format with colons. key() at :91-92 cannot join these, so today the decision is
    # orphaned in BOTH readers: the failure stays pending for ever AND the catalogue shows no decision.
    drift_f = {**f, "at": "2026-10-07T21:02:56Z"}
    drift_s = {"at": "T2b", "failure": "2026-10-07T210256Z|pA|q16",
               "failure_at": "2026-10-07T21:02:56Z", "chunk": "pA", "model": "q16",
               "decision": "PARK", "outcome": "readable"}
    report("(8) a stored key in a format the dispatch row no longer makes is re-matched",
           SV.pending([drift_f], [drift_s]), [])
    report("(9) and the catalogue shows that decision",
           SV.failure_catalogue([drift_f], [drift_s])[0]["decision"], "PARK")

    # ---- (10)(11)(12) THE WRITE ENDPOINT, ON THE SOURCE. See the docstring: main() cannot be
    # driven from an oracle. These three read the dicts main() passes to record().
    tree = ast.parse((ROOT / "scripts" / "supervise.py").read_text(encoding="utf-8"))
    fn = next((n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
    assert fn is not None, "scripts/supervise.py has no 'def main' -- the write checks have no anchor"

    base = next((n for n in ast.walk(fn)
                 if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "base" for t in n.targets)
                 and isinstance(n.value, ast.Dict)), None)
    assert base is not None, "main() no longer builds a 'base' dict literal -- site (10) has moved"
    report("(10) main()'s base row carries 'failure_at', the dispatch stamp the key is recomputed from",
           "failure_at" in dict_keys(base.value), True)

    recs = [n.args[0] for n in ast.walk(fn)
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "record"
            and n.args and isinstance(n.args[0], ast.Dict)]
    rw = [d for d in recs if "decision" in dict_keys(d)]
    uw = [d for d in recs if "decision" not in dict_keys(d) and "outcome" in dict_keys(d)]
    report("(11) the readable write records the outcome it reached: outcome='readable'",
           [len(rw), literal(rw[0], "outcome") if rw else None], [1, "readable"])
    report("(12) the unreadable write records outcome='unreadable' and NO decision",
           [len(uw), literal(uw[0], "outcome") if uw else None,
            "decision" in dict_keys(uw[0]) if uw else None],
           [1, "unreadable", False])

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
