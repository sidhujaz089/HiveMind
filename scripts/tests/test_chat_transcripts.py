#!/usr/bin/env python3
"""t121 — assemble chat captures into something a human reads.

THE ORACLE, AND WHY EACH ASSERTION IS HERE
------------------------------------------
Chat appends one JSON object per turn and `sweep_captures.py` files it under
`chat/captures/`. Nothing renders it. Jas, 2026-08-24: *"it would send every message to me
to the bridge as well for the transcript to be assembled by a routine on the HiveMind."*

Four properties, and two of them are the ones that have actually gone wrong here before:

1. **Grouped by session slug** — captures from different sessions must not interleave.
2. **Ordered by the SERVER'S ARRIVAL ORDER, never by a sequence the model declared.** This is
   the documented failure the task names explicitly, so the fixture carries a `seq` field
   that DISAGREES with arrival order. An implementation that sorts by it fails.
3. **Rendered with roles**, so a reader can tell who said what.
4. **The backfill seam is marked.** A reconstruction is not a capture and must not read like
   one — turns before "CAPTURE STARTS HERE" were never captured live.

Contract: `assemble(records) -> {slug: text}`. Records are dicts with `session`, `role`,
`text`. Unknown keys are ignored. An empty input gives `{}`, not an error.

Fairness verified per L-107: the minimal implementation was written, watched to turn these
green, and reverted. A correct patch exists, so any failure here is real.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    ok = got == want
    print(f"  [{'ok' if ok else 'BAD'}]   {label}: {got if ok else f'expected {want}, got {got}'}")
    if ok:
        PASSED += 1
    else:
        FAILED += 1


def refused(x):
    """True when the code REFUSED rather than inventing an answer."""
    return bool(x)


RECORDS = [
    {"session": "s1", "role": "note", "seq": 99,
     "text": "CAPTURE STARTS HERE. Turns before this line were not captured live."},
    {"session": "s1", "role": "user", "seq": 3, "text": "first live turn"},
    {"session": "s1", "role": "assistant", "seq": 2, "text": "second live turn"},
    {"session": "s2", "role": "user", "seq": 1, "text": "other session"},
]


def main():
    try:
        import chat_transcripts as ct
    except ImportError:
        report("scripts/chat_transcripts.py exists and imports", False, True)
        print(f"\n{PASSED} passed, {FAILED} failed")
        return 1

    if not hasattr(ct, "assemble"):
        report("chat_transcripts.assemble exists", False, True)
        print(f"\n{PASSED} passed, {FAILED} failed")
        return 1

    try:
        out = ct.assemble(RECORDS)
    except NotImplementedError:
        # The seeded stub. Not an error in the suite -- the feature is simply absent.
        report("assemble() is implemented", False, True)
        print(f"\n{PASSED} passed, {FAILED} failed")
        return 1

    print("grouping:")
    report("one entry per session", sorted(out), ["s1", "s2"])
    report("  a session's turns do not leak into another",
           "other session" in out.get("s1", ""), False)

    print("\norder is the server's, not the model's:")
    s1 = out["s1"]
    report("arrival order is preserved despite a disagreeing `seq`",
           s1.index("first live turn") < s1.index("second live turn"), True)

    print("\nrendering:")
    report("the speaker is identifiable", "user" in s1.lower(), True)
    report("  and the text survives", "first live turn" in s1, True)

    print("\nthe backfill seam is marked:")
    report("the capture-start boundary is visible",
           refused([m for m in ("CAPTURE STARTS", "not captured live", "RECONSTRUCTION")
                    if m.lower() in s1.lower()]), True)

    print("\nempty input:")
    report("gives {} rather than raising", ct.assemble([]), {})

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
