#!/usr/bin/env python3
"""Contract lives in scripts/tests/test_gate_closures.py, which is FROZEN.

A gate is closed ONLY when workspace/gate-closures.json carries a complete entry for it.
STATE.md is compared AGAINST that ledger, never parsed for words like "closed" -- line 27 of
the real STATE.md reads "**NOT CLOSED.**", which any such grep scores as a closure.
"""
import json
import re
from pathlib import Path

GATES = ("1", "2", "3", "4", "5", "6")

MARKER_RE = re.compile(r"^<!-- gate-closures: ([\d,\s]+) -->\s*$", re.MULTILINE)


def check(root: Path) -> list[str]:
    """Problems with the gate-closure claims under `root`. Empty list means all are backed."""
    problems: list[str] = []

    # 1. Read the ledger
    ledger_path = root / "workspace" / "gate-closures.json"
    ledger: dict = {}
    if not ledger_path.exists():
        problems.append("workspace/gate-closures.json is missing")
    else:
        # A CORRUPT LEDGER IS A FINDING, NOT A CRASH. Taken from coder/brief-b698d36b, which
        # was refused as a whole (it deleted MARKER_RE and never compared STATE.md to the
        # ledger, so the frozen oracle fails on it) but got THIS right where main did not:
        # json.loads() raised straight out of check(), so an unparseable ledger killed the
        # caller instead of being reported. A checker that dies on bad input tells you nothing
        # about the thing it was checking. Credit where it is due, 2026-09-14.
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            problems.append(f"workspace/gate-closures.json cannot be read: {e}")
            ledger = {}
        if not isinstance(ledger, dict):
            problems.append(f"workspace/gate-closures.json is a {type(ledger).__name__}, "
                            f"not an object of gate entries")
            ledger = {}

    # 2. Validate each entry
    for gate_id, entry in ledger.items():
        if gate_id not in GATES:
            problems.append(f"gate {gate_id} is not a known gate")
            continue

        wiring = entry.get("wiring")
        if not isinstance(wiring, dict):
            problems.append(f"gate {gate_id} is missing wiring")
        else:
            for field in ("file", "line", "input"):
                if field not in wiring:
                    problems.append(f"gate {gate_id}: wiring missing '{field}'")

        correctness = entry.get("correctness")
        if not isinstance(correctness, dict):
            problems.append(f"gate {gate_id} is missing correctness")
        else:
            for field in ("positive_refused", "negative_passed"):
                if field not in correctness:
                    problems.append(f"gate {gate_id}: correctness missing '{field}'")

    # 3. Determine the backed set (known gates with complete wiring and correctness)
    backed: set[str] = set()
    for gate_id, entry in ledger.items():
        if gate_id not in GATES:
            continue
        wiring = entry.get("wiring")
        correctness = entry.get("correctness")
        if (isinstance(wiring, dict)
                and all(f in wiring for f in ("file", "line", "input"))
                and isinstance(correctness, dict)
                and all(f in correctness for f in ("positive_refused", "negative_passed"))):
            backed.add(gate_id)

    # 4. Read STATE.md and extract the machine-readable marker
    state_path = root / "STATE.md"
    if not state_path.exists():
        problems.append("STATE.md is missing")
    else:
        state_text = state_path.read_text(encoding="utf-8")
        if state_text.strip():
            match = MARKER_RE.search(state_text)
            if match is None:
                problems.append("STATE.md is missing the gate-closures: marker")
            else:
                claimed = {g.strip() for g in match.group(1).split(",") if g.strip()}
                for gate_id in sorted(claimed - backed):
                    problems.append(
                        f"gate {gate_id} is claimed in STATE.md marker but not backed by ledger"
                    )

    return problems


def main(argv: list[str]) -> int:
    """Exit non-zero if any gate closure claim is not backed by the ledger.

    The oracle (scripts/tests/test_gate_closures.py, FROZEN) imports check() directly and
    never runs this entry point, so nothing pins it. It exists because a checker with no
    __main__ is the built-not-called failure that kept gates 3 and 6 open: before this,
    `python3 scripts/check_gate_closures.py` printed nothing and exited 0 with no ledger
    on disk at all.
    """
    root = Path(argv[1]).resolve() if len(argv) > 1 else Path(__file__).resolve().parents[1]
    problems = check(root)
    for problem in problems:
        print(f"NOT BACKED: {problem}")
    if problems:
        print(f"{len(problems)} gate-closure problem(s) under {root}")
        return 1
    print(f"all gate-closure claims are backed by the ledger ({root})")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv))
