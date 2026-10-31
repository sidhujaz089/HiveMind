#!/usr/bin/env python3
"""Pytest-free test script for scripts/check_gate_closures.py.

Run:  python scripts/tests/test_gate_closures.py
Exit 0 if all cases pass, 1 otherwise.
"""

import json
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Make scripts/ importable so `import check_gate_closures` works.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import check_gate_closures  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _write_state(root: Path, text: str = "") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "STATE.md").write_text(text, encoding="utf-8")


def _make_root(tmp: Path) -> Path:
    (tmp / "workspace").mkdir(parents=True, exist_ok=True)
    return tmp


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# AMENDED IN REVIEW -- claude-code/opus-5, 2026-09-11, before the freeze.
#
# Q16's draft called check_gate_closures.check() directly in every case. Against the stub the
# first call raised NotImplementedError and the whole script died on case 1, reporting nothing
# about cases 2-8. A suite that dies on its first case has found nothing and said nothing --
# the same rule error_scan.py states for a malformed row on line 4 of 900.
#
# Q16's own t265 oracle got this right, catching per case. This one did not, and the
# difference only showed when it was run against the stub, which is step 1 of
# writing-oracles-for-delegation and the step that keeps earning its place.
def _check(root):
    """check(), with a failure turned into a reportable value instead of a dead script."""
    try:
        return check_gate_closures.check(root)
    except NotImplementedError:
        return ["<NOT IMPLEMENTED>"]
    except Exception as exc:                                  # noqa: BLE001
        return [f"<RAISED {type(exc).__name__}: {exc}>"]

# ---------------------------------------------------------------------------
# AMENDED 2026-09-12 -- claude-code/opus-5, AFTER the freeze. Recorded because it matters.
#
# Cases 6 and 6b read the LIVE STATE.md on purpose: that is precisely what proves the prose is
# never scraped, and it is the right instinct. But they ALSO assumed the live file carried NO
# marker -- an assumption that held only while the Phase 1 work was undone. The moment STATE.md
# gained the machine-readable marker the plan requires, case 6's appended marker became the
# SECOND in the file and the checker read the first; and case 6b's entire premise ("STATE.md
# has no marker") became unsatisfiable. Both went red the instant the work was done correctly.
#
# THAT IS THE INVERSE OF test_gemma_autonomy.py AND THE SAME DEFECT. That oracle was green
# against stubs; this one was green only against an unfinished repository. Either way the suite
# was measuring the wrong thing, and in both cases the tell is a fixture that is not pinned.
#
# The fixture's INTENT is the real PROSE -- the "NOT CLOSED." line, the "| Gate | Closed by |"
# table, everything a grep would trip over. So strip the marker from it and let each case
# supply the marker condition it is actually testing. Nothing else about either case changed,
# and both still assert exactly what they asserted before.
def _real_state_prose() -> str:
    """The live STATE.md with any gate-closures marker (and its comment block) removed."""
    text = (REPO / "STATE.md").read_text(encoding="utf-8")
    text = check_gate_closures.MARKER_RE.sub("", text)
    return re.sub(r"<!-- The machine-readable claim\..*?-->\n?", "", text, flags=re.DOTALL)


def main() -> int:
    failures: list[str] = []

    # ------------------------------------------------------------------
    # Case 1: EMPTY LEDGER  (transition: bad → empty)
    # Why transition: expected result is [] which a stub also returns,
    #   so we first prove the function CAN complain, then show silence.
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_root(root)

        # Step A – deliberately bad ledger (gate 1 missing wiring)
        _write_json(root / "workspace" / "gate-closures.json",
                    {"1": {"correctness": {"positive_refused": "p", "negative_passed": "n"}}})
        _write_state(root)
        probs_a = _check(root)
        joined_a = "\n".join(probs_a)
        ok_a = len(probs_a) > 0 and "1" in joined_a and "wiring" in joined_a

        # Step B – fix to empty ledger; expect silence
        _write_json(root / "workspace" / "gate-closures.json", {})
        probs_b = _check(root)
        ok_b = probs_b == []

        ok = ok_a and ok_b
        if not ok:
            failures.append("case1")
        print(f"[{'ok' if ok else 'BAD'}] case1  empty ledger (transition bad→empty)")

    # ------------------------------------------------------------------
    # Case 2: MISSING WIRING
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_root(root)
        _write_json(root / "workspace" / "gate-closures.json",
                    {"1": {"correctness": {"positive_refused": "p", "negative_passed": "n"}}})
        _write_state(root)
        probs = _check(root)
        joined = "\n".join(probs)
        ok = len(probs) > 0 and "1" in joined and "wiring" in joined
        if not ok:
            failures.append("case2")
        print(f"[{'ok' if ok else 'BAD'}] case2  missing wiring → expect '1'+'wiring' | got: {joined!r}")

    # ------------------------------------------------------------------
    # Case 3: MISSING CORRECTNESS
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_root(root)
        _write_json(root / "workspace" / "gate-closures.json",
                    {"2": {"wiring": {"file": "a.py", "line": 10, "input": "x"}}})
        _write_state(root)
        probs = _check(root)
        joined = "\n".join(probs)
        ok = len(probs) > 0 and "2" in joined and "correctness" in joined
        if not ok:
            failures.append("case3")
        print(f"[{'ok' if ok else 'BAD'}] case3  missing correctness → expect '2'+'correctness' | got: {joined!r}")

    # ------------------------------------------------------------------
    # Case 4: INCOMPLETE WIRING (no 'input' key)
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_root(root)
        _write_json(root / "workspace" / "gate-closures.json",
                    {"3": {
                        "wiring": {"file": "a.py", "line": 10},
                        "correctness": {"positive_refused": "p", "negative_passed": "n"},
                    }})
        _write_state(root)
        probs = _check(root)
        joined = "\n".join(probs)
        ok = len(probs) > 0 and "3" in joined and "input" in joined
        if not ok:
            failures.append("case4")
        print(f"[{'ok' if ok else 'BAD'}] case4  incomplete wiring → expect '3'+'input' | got: {joined!r}")

    # ------------------------------------------------------------------
    # Case 5: COMPLETE  (transition: incomplete → complete)
    # Why transition: expected result is [] which a stub also returns.
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_root(root)

        # Step A – wiring missing 'input'; expect complaint
        _write_json(root / "workspace" / "gate-closures.json",
                    {"1": {
                        "wiring": {"file": "a.py", "line": 1},
                        "correctness": {"positive_refused": "p", "negative_passed": "n"},
                    }})
        _write_state(root)
        probs_a = _check(root)
        joined_a = "\n".join(probs_a)
        ok_a = len(probs_a) > 0 and "1" in joined_a and "input" in joined_a

        # Step B – add the missing 'input'; expect silence
        _write_json(root / "workspace" / "gate-closures.json",
                    {"1": {
                        "wiring": {"file": "a.py", "line": 1, "input": "x"},
                        "correctness": {"positive_refused": "p", "negative_passed": "n"},
                    }})
        probs_b = _check(root)
        ok_b = probs_b == []

        ok = ok_a and ok_b
        if not ok:
            failures.append("case5")
        print(f"[{'ok' if ok else 'BAD'}] case5  complete entry (transition incomplete→complete)")

    # ------------------------------------------------------------------
    # Case 6: THE DISAGREEMENT -- REWRITTEN IN REVIEW, 2026-09-11.
    #
    # THE ORACLE TAUGHT THE GREP. This case used to write the fixture
    # _write_state(root, "Gate 3 is closed.\n") and assert "3" and "STATE.md" appeared in the
    # problems. The brief said never to scan STATE.md for words like "closed". The ORACLE made
    # doing exactly that the cheapest way to pass, so the dispatch shipped
    # re.finditer(r"Gate\s+(\d+)\s+is\s+closed") and its summary said so plainly: "using a
    # specific 'Gate N is closed' pattern that avoids false-matching 'NOT CLOSED'".
    #
    # It passed, and it is vacuous. Run against the real repository that regex returns False:
    # the actual STATE.md says "**FOUR OF SIX PHASE 0 GATES ARE CLOSED. Gates 3 and 6 are
    # NOT**" and carries a "| Gate | Closed by |" table. The check could never have fired on
    # the one situation it was built for.
    #
    # THE MODEL OPTIMISES AGAINST THE ORACLE, NOT THE BRIEF. A requirement stated only in
    # prose is a suggestion; the suite is the contract.
    #
    # So the fixture is now the REAL STATE.md, read from this repository, and STATE.md's prose
    # is not consulted at all. The claim lives in a machine-readable marker that is compared
    # to the ledger by SET EQUALITY. There is no phrasing to match and nothing to grep.
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_root(root)
        real_state = _real_state_prose()
        # The ledger backs gates 1 and 2. The marker claims 1, 2 and 3 are closed.
        _write_json(root / "workspace" / "gate-closures.json", {
            g: {"wiring": {"file": "a.py", "line": 1, "input": "x"},
                "correctness": {"positive_refused": "p", "negative_passed": "n"}}
            for g in ("1", "2")})
        _write_state(root, real_state + "\n<!-- gate-closures: 1,2,3 -->\n")
        probs = _check(root)
        joined = "\n".join(probs)
        # Assert the VALUE: gate 3 is claimed and unbacked. And assert the prose gates the
        # real file DOES discuss (4, 5, 6) are NOT reported -- the marker is the claim, the
        # prose is not, so a grep that scraped the table would over-report and fail here.
        ok = (len(probs) > 0 and "3" in joined
              and not any(f"gate {g}" in joined.lower() for g in ("4", "5", "6")))
        if not ok:
            failures.append("case6")
        print(f"[{'ok' if ok else 'BAD'}] case6  marker claims 3, ledger backs 1-2, real "
              f"STATE.md prose ignored | got: {joined!r}")

    # ------------------------------------------------------------------
    # Case 6b: THE MARKER IS MANDATORY. Absent must not read as "claims nothing, all fine".
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_root(root)
        _write_json(root / "workspace" / "gate-closures.json", {})
        _write_state(root, _real_state_prose())
        probs = _check(root)
        joined = "\n".join(probs)
        ok = len(probs) > 0 and "gate-closures:" in joined
        if not ok:
            failures.append("case6b")
        print(f"[{'ok' if ok else 'BAD'}] case6b no marker in STATE.md is a finding | "
              f"got: {joined!r}")

    # ------------------------------------------------------------------
    # Case 7: THE INVERSION (unknown gate id "7")
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_root(root)
        _write_json(root / "workspace" / "gate-closures.json",
                    {"7": {
                        "wiring": {"file": "a.py", "line": 1, "input": "x"},
                        "correctness": {"positive_refused": "p", "negative_passed": "n"},
                    }})
        _write_state(root)
        probs = _check(root)
        joined = "\n".join(probs)
        ok = len(probs) > 0 and "7" in joined
        if not ok:
            failures.append("case7")
        print(f"[{'ok' if ok else 'BAD'}] case7  unknown gate '7' → expect '7' | got: {joined!r}")

    # ------------------------------------------------------------------
    # Case 8: NO LEDGER FILE AT ALL
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_root(root)
        # Intentionally do NOT create gate-closures.json
        _write_state(root)
        probs = _check(root)
        joined = "\n".join(probs)
        ok = len(probs) > 0 and "workspace/gate-closures.json" in joined
        if not ok:
            failures.append("case8")
        print(f"[{'ok' if ok else 'BAD'}] case8  no ledger file → expect 'workspace/gate-closures.json' | got: {joined!r}")

    # ------------------------------------------------------------------
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
