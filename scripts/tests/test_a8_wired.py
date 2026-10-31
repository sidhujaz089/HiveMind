#!/usr/bin/env python3
"""A8, WIRED: the classifier's verdict reaches the routing, instead of dying in a helper nobody calls (2026-10-30).

The classifier (dd1badeef), the class destinations and the symbol extractor (d726b1cc7) all exist. What was missing
is the two joins: coder has to SAY the class in the log, the failure schema has to READ that marker, and the
escalation site has to route on the CLASS before it falls back to the blame. Without all three, A8 is three good
parts and no behaviour.

RED ON MAIN: the schema has no ORACLE-CONFLICT rule and escalate_failure asks only for the blame, so an impossible
patch is routed one-rank-up from the builder and retried.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "scripts"))
import failure_schema as fsch      # noqa: E402
import supervise as S              # noqa: E402
import role_store                  # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"  {'ok ' if ok else 'FAIL'}  {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        fails.append(name)


# --- 1. the schema reads the marker coder writes -------------------------------------------------
conflict = ("attempt 2 TESTS-FAILED\n"
            "ORACLE-CONFLICT on first_budget: the target requires MAX_TOKENS_FIRST and "
            "test_budget_values asserts 144 -- a patch that satisfies one breaks the other")
cls, cause = fsch.classify(conflict)
check("the schema reads a conflict marker", cls == "oracle-conflict", f"{cls}/{cause}")

overlap = "attempt 1 TESTS-FAILED\nORACLE-OVERLAP on first_budget: the collateral only mentions it"
cls2, cause2 = fsch.classify(overlap)
check("the schema reads an overlap marker", cls2 == "oracle-overlap", f"{cls2}/{cause2}")
check("  and the two are not confused", cls != cls2, f"{cls} vs {cls2}")

# --- 2. the class reaches a destination, and never the embedder -----------------------------------
d = role_store.load()
for c in ("oracle-conflict", "oracle-overlap"):
    dest = S.destination_for_class(c, d)
    check(f"{c} names a destination", bool(dest), str(dest))
    check(f"  and it is not the embedder", dest != "embedder", str(dest))

# --- 3. the escalation site asks for the class FIRST ----------------------------------------------
src = (HERE / "scripts" / "supervise.py").read_text(encoding="utf-8")
i = src.index("def escalate_failure(")
site = src[i:i + 1400]
check("escalate_failure tries the CLASS before the blame",
      "destination_for_class(cls)" in site, "it only asks for the blame today")
check("  and still falls back to the blame, then one rank up",
      "destination_for_class(blame)" in site and "escalate_up" in site, "a class with no destination must not break")

# --- 4. coder says it, with the symbols from the patch ------------------------------------------
coder = (HERE / "projects" / "local-inference" / "coder.py").read_text(encoding="utf-8")
check("coder calls the classifier", "oracle_conflict" in coder, "nothing in coder references it")
check("  and gets the symbols from the patch", "patch_symbols" in coder, "the classifier needs the diff's symbols")
# THE MARKER IS BUILT FROM THE CLASS, not typed: coder writes _v["class"].upper(), so the literal
# "ORACLE-CONFLICT" is NOT in the source and grepping for it tests the spelling rather than the join. What matters is
# that the marker coder produces is the marker the schema matches, so the check does exactly that round trip.
check("coder builds the marker from the class it was given", "class'].upper()" in coder,
      "the marker must come from oracle_conflict's own class name, not a second spelling")
for produced, want in (("oracle-conflict", "oracle-conflict"), ("oracle-overlap", "oracle-overlap")):
    got, _ = fsch.classify(f"attempt 1 TESTS-FAILED\n{produced.upper()} on sym: the target requires it")
    check(f"  and the schema classifies what coder writes for {produced}", got == want, got)

print("test_a8_wired:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
