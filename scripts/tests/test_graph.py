#!/usr/bin/env python3
"""The graph must parse declared edges and must never invent one.

`D-32`: the whole argument for building this now rather than deferring it with `D-25` is that
an extractor can hallucinate a relation and a parser cannot. Every assertion here defends that
asymmetry -- and the honest failure mode, an edge kept after its target moved, has its own test.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import graph  # noqa: E402

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


def main():
    edges = graph.build()
    rels = {e["rel"] for e in edges}
    print("Declared edges are parsed:")
    for r in ("superseded_by", "enforced_by", "cites", "mutates", "caught_by", "rests_on",
              "see", "run_of", "verdict_on"):
        report(f"  {r}", r in rels, True)

    print("\nThe relations answer the questions they were built for:")
    c = graph.chain("L-116", edges)
    report("L-116 names its guard", any("selfmatch" in g for g in c["enforced_by"]), True)
    report("  and the suite that proves it",
           any("test_selfmatch" in s for s in c["suites"]), True)
    report("L-112 is superseded", c2 := graph.chain("L-112", edges)["superseded_by"], ["L-114"])

    i = graph.impact("projects/local-inference/coder.py", edges)
    report("coder.py has mutations pointed at it", len(i["mutations"]) > 5, True)
    report("  and a suite that must fail", bool(i["suites"]), True)

    print("\nIt does not invent edges:")
    # The refusal that matters: a node nobody declared has no neighbours, and asking about it
    # returns nothing rather than a plausible guess.
    report("an unknown lesson has no chain",
           graph.chain("L-9999", edges)["enforced_by"], [])
    report("an unknown file has no impact",
           graph.impact("scripts/does-not-exist.py", edges)["mutations"], [])
    report("an unknown task has no history", graph.history("t99999", edges), [])
    report("no edge points from a node to itself",
           [e for e in edges if e["from"] == e["to"]], [])

    print("\nThe honest failure mode is detectable:")
    fake = [{"from": "L-1", "rel": "enforced_by", "to": "scripts/gone.py"}]
    report("an edge to a missing file is reported stale", len(graph.stale(fake)), 1)
    real = [{"from": "L-1", "rel": "enforced_by", "to": "scripts/graph.py"}]
    report("  and one to a real file is not", graph.stale(real), [])

    # --- t029: backlinks ---------------------------------------------------------------
    #
    # THE ORACLE FOR t029 DID NOT EXIST. The task note said "the oracle was satisfied before
    # handing over -- test_graph.py reported 20 passed 0 failed", and it reported that
    # because the suite contained no reference to `backlinks` at all. A suite that passes
    # whether or not the feature exists is not a target; that is L-106 exactly, and it is
    # why t029 was ACCEPTED with nothing to show.
    #
    # Fairness verified per L-107: the minimal implementation was written, watched to turn
    # these green, and then reverted.
    print("\nbacklinks(): what points AT a node")
    EDGES = [
        {"from": "L-112", "rel": "cites", "to": "L-114"},
        {"from": "L-118", "rel": "cites", "to": "L-114"},
        {"from": "L-118", "rel": "cites", "to": "L-114"},      # duplicate, must collapse
        {"from": "D-31", "rel": "rests_on", "to": "L-114"},
        {"from": "L-114", "rel": "cites", "to": "L-099"},      # outgoing: must NOT appear
    ]
    if not hasattr(graph, "backlinks"):
        report("graph.backlinks exists", False, True)
    else:
        b = graph.backlinks("L-114", EDGES)
        report("groups sources by relation", b.get("cites"), ["L-112", "L-118"])
        report("  de-duplicates repeated edges", len(b.get("cites", [])), 2)
        report("  keeps relations separate", b.get("rests_on"), ["D-31"])
        report("  an OUTGOING edge is not a backlink",
               "L-099" in str(b.get("cites", [])), False)
        report("  a node nothing points at gives {}", graph.backlinks("nope", EDGES), {})
        import inspect as _i
        report("  reuses index() rather than re-walking",
               "index(" in _i.getsource(graph.backlinks), True)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
