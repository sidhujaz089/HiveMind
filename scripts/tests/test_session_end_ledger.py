#!/usr/bin/env python3
"""t358: a handover must carry a thread ledger, and every row of it must be stated.

The refusal this exists for: a thread that is not on a stated row cannot be audited.
A handover without the ledger section, or whose row carries no stated state
(PROPOSED, "awaiting go", a bare title...), refuses session_end -- the same
draft-then-block shape as the t265 unfilled-handover refusal, extended at its seam.
An EMPTY ledger is refused too: zero stated rows is silence, and silence reads as
completeness.

Stated states (Jas, 2026-09-18): CLOSED | FILED | DECLARED-OPEN. CLOSED must carry
a receipt; FILED a task id; DECLARED-OPEN the thread and its cover. The gate checks
the STATE COLUMN mechanically; the receipt and the id are the content the next
worker reads on the row.

All cases run on a temporary root (handover.py's explicit-root contract; its own
guard suite works the same way), so the live handover never moves during the test.
"""
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import handover  # noqa: E402  (the RED run is the pre-t358 shape of this module)

DAY = "2026-09-18"
BASE = (
    "# Handover 2026-09-18\n\n"
    "## What this session was for\nworked\n\n"
    "## What was done\nworked\n\n"
    "## What was measured\nmeasured\n\n"
    "## What is still open\nnone\n\n"
    "## Next step\nnext\n"
)
GOOD_LEDGER = (
    "## Thread ledger\n"
    "| Thread | State | Artefact |\n"
    "|---|---|---|\n"
    "| scoper zero-SCOPED | CLOSED | t248 |\n"
    "| bridge whereabouts | FILED | cbc5a3e |\n"
    "| DSH split | DECLARED-OPEN | F7 |\n"
)
BAD_LEDGER = (
    "## Thread ledger\n"
    "| Thread | State | Artefact |\n"
    "|---|---|---|\n"
    "| builds 1-8 | PROPOSED, awaiting go | F-file |\n"
)
EMPTY_LEDGER = "## Thread ledger\n"

FAILS = []


def ck(label, got, want):
    ok = got == want
    print(f"  [{'ok' if ok else 'BAD'}]    {label} -> {got}")
    if not ok:
        FAILS.append(label)


with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    (root / "workspace").mkdir()
    hf = root / "workspace" / f"HANDOVER-{DAY}.md"

    def write(text):
        hf.write_text(text, encoding="utf-8")

    # 1. NO LEDGER SECTION -> refused, and the refusal names what is missing.
    #    This is the decay case: the refusal must fire WITHOUT the clause.
    write(BASE)
    probs = handover.check(root, DAY)
    ck("a handover with no Thread ledger is refused",
       bool([p for p in probs if "thread ledger" in p.lower()]), True)

    # 2. A ROW WITH NO STATED STATE -> refused, and the row is the one named.
    write(BASE + BAD_LEDGER)
    probs = handover.check(root, DAY)
    ck("an unstated row (PROPOSED) is refused, naming that row",
       any("PROPOSED" in p and "ledger" in p.lower() for p in probs), True)

    # 3. EVERY ROW STATED -> the ledger passes, and a filled file passes as a whole.
    write(BASE + GOOD_LEDGER)
    ck("stated CLOSED/FILED/DECLARED-OPEN rows pass", handover.check(root, DAY), [])

    # 4. AN EMPTY LEDGER is refused: silence must not read as completeness.
    write(BASE + EMPTY_LEDGER)
    probs = handover.check(root, DAY)
    ck("an empty ledger is refused",
       bool([p for p in probs if "ledger" in p.lower() and ("no stated" in p.lower()
                                                            or "row" in p.lower())]), True)

    # 5. THE DRAFT CARRIES THE SECTION, so the refused state is fixable from the skeleton
    #    (draft-then-block, the t265 pattern -- the gate must not demand what it never drafted).
    ck("the draft carries the Thread ledger section", "Thread ledger" in handover.draft(root, DAY),
       True)

    # 6. The ### level (t358's own wording) is accepted exactly like ##.
    write(BASE + GOOD_LEDGER.replace("## Thread ledger", "### Thread ledger"))
    ck("a ###-level ledger is accepted", handover.check(root, DAY), [])

for x in FAILS:
    print("FAIL:", x)
print("test_session_end_ledger:", "FAILED" if FAILS else "ok")
sys.exit(1 if FAILS else 0)
