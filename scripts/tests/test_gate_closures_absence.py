#!/usr/bin/env python3
"""Fail-first oracle for t274: check_gate_closures absence-handling.

Run:  python3 scripts/tests/test_gate_closures_absence.py
Exit 0 if all cases pass, 1 otherwise.

Pins the four absence-handling behaviours the 2026-09-11 note (t274) demands:
  1. malformed ledger JSON  -> reported, not raised
  2. missing STATE.md       -> reported, not silence   (the one still red today)
  3. valid-JSON-but-a-list  -> reported, not raised on .items()
  4. decay: everything present & correct -> [] (a checker that complains about
     everything would fail this, so 1-3 cannot be satisfied by over-reporting)

Every case asserts the VALUE (the path / the exact empty list), not merely
non-emptiness -- a case that only checks "non-empty" passes for the wrong reason
the moment any other rule fires.
"""

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import check_gate_closures  # noqa: E402


def _check(root):
    """check(), with a failure turned into a reportable value instead of a dead script.

    A suite that dies on its first case has found nothing and said nothing -- the
    rule error_scan.py states for a malformed row on line 4 of 900, and the rule
    the 2026-09-11 t274 note cites. So every case below must still be reportable.
    """
    try:
        return check_gate_closures.check(root)
    except Exception as exc:  # noqa: BLE001
        return [f"<RAISED {type(exc).__name__}: {exc}>"]


def _root(tmp):
    (tmp / "workspace").mkdir(parents=True, exist_ok=True)
    return tmp


def main() -> int:
    failures = []

    # Case 1: MALFORMED LEDGER -> reported, not raised. Assert the ledger PATH is
    # named. We do not over-specify the exact words ("could not be parsed" vs the
    # current "cannot be read") so the already-correct report is not forced to
    # reword just to satisfy the pin.
    with tempfile.TemporaryDirectory() as td:
        root = _root(Path(td))
        (root / "workspace" / "gate-closures.json").write_text("{not json", encoding="utf-8")
        probs = _check(root)
        joined = "\n".join(probs)
        ok = len(probs) > 0 and "gate-closures.json" in joined and "<RAISED" not in joined
        if not ok:
            failures.append("case1")
        print(f"[{'ok' if ok else 'BAD'}] case1 malformed ledger -> reported not raised | got: {joined!r}")

    # Case 2: MISSING STATE.md -> reported, not silence. RED TODAY (the fix).
    with tempfile.TemporaryDirectory() as td:
        root = _root(Path(td))
        (root / "workspace" / "gate-closures.json").write_text("{}", encoding="utf-8")
        # deliberately do NOT create STATE.md
        probs = _check(root)
        joined = "\n".join(probs)
        ok = len(probs) > 0 and "STATE.md" in joined
        if not ok:
            failures.append("case2")
        print(f"[{'ok' if ok else 'BAD'}] case2 no STATE.md -> reported not silence | got: {joined!r}")

    # Case 3: VALID JSON BUT A LIST -> reported, not raised on .items().
    with tempfile.TemporaryDirectory() as td:
        root = _root(Path(td))
        (root / "workspace" / "gate-closures.json").write_text("[]", encoding="utf-8")
        probs = _check(root)
        joined = "\n".join(probs)
        ok = len(probs) > 0 and "gate-closures.json" in joined and "<RAISED" not in joined
        if not ok:
            failures.append("case3")
        print(f"[{'ok' if ok else 'BAD'}] case3 list-not-object -> reported not raised | got: {joined!r}")

    # Case 4: DECAY TEST -- complete ledger + matching marker -> []. A function that
    # complains about everything would fail this, so cases 1-3 cannot be met by
    # over-reporting.
    with tempfile.TemporaryDirectory() as td:
        root = _root(Path(td))
        complete = {
            "1": {
                "wiring": {"file": "a.py", "line": 1, "input": "x"},
                "correctness": {"positive_refused": "p", "negative_passed": "n"},
            }
        }
        (root / "workspace" / "gate-closures.json").write_text(json.dumps(complete), encoding="utf-8")
        (root / "STATE.md").write_text("<!-- gate-closures: 1 -->\n", encoding="utf-8")
        probs = _check(root)
        ok = probs == []
        if not ok:
            failures.append("case4")
        print(f"[{'ok' if ok else 'BAD'}] case4 decay: complete+correct -> [] | got: {probs!r}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())