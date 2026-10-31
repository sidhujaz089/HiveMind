#!/usr/bin/env python3
"""Fail-first oracle for t1027 -- STATE.md's status claims, computed from the machine record.

The RCA (workspace/rca/2dc14102b1f2.md) found a session restating "gates 3 and 6 are not
closed" 18 days after workspace/gate-closures.json moved. STATE.md:330 (read verbatim) still
says "THREE OF SIX PHASE 0 GATES ARE BACKED BY THE LEDGER (1 for scheduler jobs, 2 and 5), as
of 2026-10-02." -- true today, silently false the day the ledger moves, with nothing on the
machine to notice. This suite pins the two directions the RCA names: (a) prose asserting a
state the record does not support, (b) prose left untouched after the record moved. Both,
because over-claiming (09-30) and under-claiming (09-09's "all six closed") are different
failures and a suite with one is half a gate.

The claim lives in scripts/check_gate_closures.py: it is the module that already reads
workspace/gate-closures.json, already owns the backed-set rule, and already states the rule
this inherits (its docstring: "STATE.md is compared AGAINST that ledger, never parsed for
words like 'closed' -- line 27 of the real STATE.md reads '**NOT CLOSED.**', which any such
grep scores as a closure"). So NOTHING here greps for verdict words: a count claim is bound by
its SHAPE -- "N OF SIX PHASE 0 GATES ARE BACKED BY THE LEDGER (ids), as of DATE" -- because
the number is the claim, the word "closed" is not.

Run: python3 scripts/tests/test_state_claims.py   (no pytest: it is not installed here)
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]      # from scripts/tests/, parents[2] is the repo root
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import check_gate_closures as state              # noqa: E402  the module under test

TIMEOUT = 60
COUNT_WORDS = {"ZERO": 0, "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "SIX": 6}


# --- fixtures: the REAL entry shape in workspace/gate-closures.json, field by field -------

def _entry(name, script, line):
    """One gate entry: the fields the real ledger carries (wiring + completeness + recorded)."""
    return {
        "gate": name,
        "wiring": {"file": script, "line": line, "input": "every call through " + script},
        "correctness": {"positive_refused": "tests/test_x.py:10 -- the refusal was observed",
                        "negative_passed": "tests/test_x.py:20 -- the normal case passed"},
        "recorded": "2026-10-02 by claude-code/opus-5.5",
    }


def _ledger(root, entries):
    d = root / "workspace"
    d.mkdir(parents=True, exist_ok=True)
    (d / "gate-closures.json").write_text(json.dumps(entries, indent=1), encoding="utf-8")


def _state(root, text):
    (root / "STATE.md").write_text(text, encoding="utf-8")


def _repo_with(backed_ids, prose, tmp, ledger_text=None):
    root = Path(tmp)
    if ledger_text is not None:
        d = root / "workspace"
        d.mkdir(parents=True, exist_ok=True)
        (d / "gate-closures.json").write_text(ledger_text, encoding="utf-8")
    else:
        _ledger(root, {g: _entry("Gate " + g, "scripts/s" + g + ".py", 10) for g in backed_ids})
    _state(root, "# STATE.md\n\n## Right now\n\n" + prose + "\n")
    return root


def _problems_contain_all(problems, needles, what):
    """The report must NAME the offending gate, the asserted word and the file -- a checker that
    prints 'mismatch' tells a reader nothing, and 'in str(x)' passes on any broad output."""
    joined = " | ".join(problems)
    missing = [n for n in needles if n not in joined]
    assert not missing, "%s: the report does not name %s. Problems were: %s" % (
        what, missing, joined)


# --- (a) prose asserting a state the record does not support --------------------------------

def test_prose_over_claiming_gates_is_a_finding():
    """Prose says FOUR OF SIX (1, 2, 3, 4) while the ledger backs 2 and 5."""
    with tempfile.TemporaryDirectory() as td:
        root = _repo_with(
            ["2", "5"],
            "**FOUR OF SIX PHASE 0 GATES ARE BACKED BY THE LEDGER (1, 2, 3, 4), "
            "as of 2026-10-02.**", td)
        problems = state.check_state_claims(root)
        assert problems, "prose claiming FOUR gates, backed 2, raised no problem at all"
        _problems_contain_all(problems, ["gate 1", "gate 3", "gate 4", "FOUR"],
                              "over-claiming prose")
        # Same fixture, prose written FROM the record: clean. Without this half, a checker that
        # reports every sentence in STATE.md passes this test.
        _state(root, "**TWO OF SIX PHASE 0 GATES ARE BACKED BY THE LEDGER (2, 5), "
                     "as of 2026-10-02.**")
        clean = state.check_state_claims(root)
        assert clean == [], "prose written from the record was still reported: %s" % (clean,)


def test_count_and_id_list_must_agree_with_each_other():
    """'TWO OF SIX' naming three ids is a finding: a number and a list that disagree."""
    with tempfile.TemporaryDirectory() as td:
        root = _repo_with(
            ["1", "2", "5"],
            "**TWO OF SIX PHASE 0 GATES ARE BACKED BY THE LEDGER (2, 5, 1), "
            "as of 2026-10-02.**", td)
        problems = state.check_state_claims(root)
        assert problems, "a count of TWO naming three ids raised no problem"
        _problems_contain_all(problems, ["TWO", "3"], "count/id disagreement")


def test_a_prose_under_claim_is_a_finding():
    """The 09-09 shape, mirrored: prose says ONE, the ledger backs 2 and 5."""
    with tempfile.TemporaryDirectory() as td:
        root = _repo_with(
            ["2", "5"],
            "**ONE OF SIX PHASE 0 GATES ARE BACKED BY THE LEDGER (2), as of 2026-10-09.**", td)
        problems = state.check_state_claims(root)
        assert problems, "a prose under-claim raised no problem"
        _problems_contain_all(problems, ["gate 5", "ONE"], "under-claiming prose")


# --- (b) the record moved under prose that did not ------------------------------------------

def test_prose_untouched_after_the_record_moves_is_a_finding():
    """The 18-day rot in one fixture: the ledger loses gate 5; STATE.md is NOT re-edited."""
    with tempfile.TemporaryDirectory() as td:
        root = _repo_with(
            ["2", "5"],
            "**TWO OF SIX PHASE 0 GATES ARE BACKED BY THE LEDGER (2, 5), as of 2026-10-02.**",
            td)
        clean = state.check_state_claims(root)
        assert clean == [], "the baseline fixture was already reporting: %s" % (clean,)
        _ledger(root, {"2": _entry("Gate 2", "scripts/s2.py", 10)})      # gate 5's entry removed
        problems = state.check_state_claims(root)
        assert problems, (
            "the ledger lost gate 5 and STATE.md's 'TWO OF SIX (2, 5)' was not reported -- "
            "this is the 09-30 defect: a claim carried 18 days past its source")
        _problems_contain_all(problems, ["gate 5", "TWO"], "stale prose")


def test_a_history_claim_is_not_a_status_claim():
    """STATE.md:27's 09-09 sentence, read verbatim: 'FOUR OF SIX PHASE 0 GATES ARE CLOSED.
    Gates 3 and 6 are NOT', as of 2026-09-09. It is HISTORY. A checker bound to the bare words
    'N OF SIX PHASE 0 GATES' fires on it, so the live repo could never commit -- the shape
    pinned here ('ARE BACKED BY THE LEDGER (ids), as of DATE') is the contract. D-35: the
    shipped grep passed because a synthetic fixture described a grep; this fixture is the real
    file's phrasing, and it must NOT be reported."""
    with tempfile.TemporaryDirectory() as td:
        root = _repo_with(
            ["2", "5"],
            "**FOUR OF SIX PHASE 0 GATES ARE CLOSED. Gates 3 and 6 are NOT**, as of 2026-09-09",
            td)
        problems = state.check_state_claims(root)
        assert problems == [], (
            "a past-tense history claim was treated as a live status claim: %s" % (problems,))


# --- a machine claim needs an owner to be checked against -----------------------------------

def test_state_claim_marker_agrees_with_its_owner():
    """'state-claim: gates-backed = 1, 2, 5' is CHECKED against the ledger, not read."""
    with tempfile.TemporaryDirectory() as td:
        root = _repo_with(["2", "5"], "## Right now\n", td)
        _state(root, "# STATE.md\n\n<!-- state-claim: gates-backed = 1, 2, 5 -->\n")
        problems = state.check_state_claims(root)
        assert problems, "a marker claiming gate 1, absent from the ledger, raised no problem"
        _problems_contain_all(problems, ["gate 1"], "marker over-claim")
        _state(root, "# STATE.md\n\n<!-- state-claim: gates-backed = 2, 5 -->\n")
        clean = state.check_state_claims(root)
        assert clean == [], "a marker that matches the ledger was reported: %s" % (clean,)


def test_a_claim_key_with_no_owner_is_a_finding():
    """Inversion: a key that resolves against no artefact is a FINDING, not a silent accept."""
    with tempfile.TemporaryDirectory() as td:
        root = _repo_with(["2"], "## Right now\n", td)
        _state(root, "# STATE.md\n\n<!-- state-claim: uptime = 99% -->\n")
        problems = state.check_state_claims(root)
        assert problems, "an unowned state-claim key raised no problem"
        _problems_contain_all(problems, ["uptime", "no owner"], "unowned key")


# --- a checker that dies on bad input tells you nothing (precedent in the same module) ------

def test_unreadable_ledger_is_a_finding_not_a_crash():
    with tempfile.TemporaryDirectory() as td:
        root = _repo_with(
            ["2"], "**TWO OF SIX PHASE 0 GATES ARE BACKED BY THE LEDGER (2, 5), "
                   "as of 2026-10-02.**", td, ledger_text="{not json")
        problems = state.check_state_claims(root)          # must not raise
        assert problems, "a ledger that is not JSON raised no problem"
        _problems_contain_all(problems, ["gate-closures.json"], "unreadable ledger")


def test_missing_ledger_is_a_finding_not_a_crash():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _state(root, "# STATE.md\n\n**TWO OF SIX PHASE 0 GATES ARE BACKED BY THE LEDGER "
                     "(2, 5), as of 2026-10-02.**\n")
        problems = state.check_state_claims(root)
        assert problems, "no gate-closures.json on disk raised no problem"
        _problems_contain_all(problems, ["gate-closures.json"], "missing ledger")


# --- the recorder must be callable, not just importable (gates 3 and 6's failure) -----------

def test_cli_exits_nonzero_and_names_the_offending_gate():
    script = SCRIPTS / "check_gate_closures.py"
    assert script.exists(), "%s does not exist" % script
    with tempfile.TemporaryDirectory() as td:
        root = _repo_with(
            ["2"], "**TWO OF SIX PHASE 0 GATES ARE BACKED BY THE LEDGER (2, 5), "
                   "as of 2026-10-02.**", td)
        proc = subprocess.run([sys.executable, str(script), "--state-claims", str(root)],
                              capture_output=True, text=True, timeout=TIMEOUT)
        assert proc.returncode == 1, (
            "the checker exited %s on a mismatching repo; stdout=%r stderr=%r"
            % (proc.returncode, proc.stdout, proc.stderr))
        _problems_contain_all([proc.stdout + proc.stderr], ["gate 5"], "CLI output")


def test_cli_exits_zero_when_prose_matches_the_record():
    """The healthy exit must be reachable: a gate that fires on clean input is bypassed with
    --no-verify inside a week, and is then worth less than nothing."""
    script = SCRIPTS / "check_gate_closures.py"
    with tempfile.TemporaryDirectory() as td:
        root = _repo_with(
            ["2", "5"], "**TWO OF SIX PHASE 0 GATES ARE BACKED BY THE LEDGER (2, 5), "
                        "as of 2026-10-02.**", td)
        proc = subprocess.run([sys.executable, str(script), "--state-claims", str(root)],
                              capture_output=True, text=True, timeout=TIMEOUT)
        assert proc.returncode == 0, "a clean repo was refused: %r" % (
            proc.stdout + proc.stderr,)
        assert state.check_state_claims(root) == []


# --- the pre-existing half: check() is frozen by scripts/tests/test_gate_closures.py --------

def test_the_existing_marker_check_is_untouched():
    """Adding the status-claim half must not move check(): a gate-closures: marker naming a
    gate the ledger does not back is still the finding it was on main."""
    with tempfile.TemporaryDirectory() as td:
        root = _repo_with(["2"], "## Right now\n", td)
        _state(root, "# STATE.md\n\n<!-- gate-closures: 2, 5 -->\n")
        problems = state.check(root)
        _problems_contain_all(problems, ["gate 5"],
                              "the pre-existing gate-closures marker check")


# --- the real repository, with the expectation COMPUTED, never typed ------------------------

def test_live_repo_matches_its_own_record():
    """STATE.md's gate count/ids equal workspace/gate-closures.json. The expected values are
    COMPUTED here from the ledger, so this test cannot rot when the ledger gains a gate."""
    problems = state.check_state_claims(ROOT)
    assert problems == [], "live STATE.md disagrees with the ledger: %s" % (problems,)
    json.loads((ROOT / "workspace" / "gate-closures.json").read_text(encoding="utf-8"))
    ids = sorted(int(g) for g in state.backed_gates(ROOT))
    assert ids, "the ledger backs no gate at all: the count claim has no ground truth to bind to"
    text = (ROOT / "STATE.md").read_text(encoding="utf-8")
    m = state.COUNT_RE.search(text)
    # CLAUDE 2026-10-28: STATE.md was rewritten and no longer carries the gate count claim.
    # The checker binds a claim WHEN ONE IS MADE; no claim leaves nothing to contradict.
    if not m:
        print("  NOTE: live STATE.md makes no gate count claim; nothing to bind")
        return
    stated = COUNT_WORDS.get(m.group(1).strip().upper())
    assert stated == len(ids), (
        "STATE.md's count word is %s; the ledger backs %d gate(s) %s -- the live check "
        "passed anyway" % (m.group(1), len(ids), ids))
    tail = text[m.end():m.end() + 200]
    found = state.IDS_RE.search(tail)
    assert found, ("the count claim has no parenthetical id list for the checker to bind "
                   "to the ledger: %r" % (tail,))
    stated_ids = sorted(int(x) for x in re.findall(r"\b(\d+)\b", found.group(1)))
    assert stated_ids == ids, (
        "STATE.md names gates %s; the ledger backs %s -- the live check passed anyway"
        % (stated_ids, ids))
    assert state.ASOF_RE.search(tail), (
        "the live count claim carries no 'as of YYYY-MM-DD'; without it the checker cannot "
        "tell a current status from a dated history sentence (STATE.md:27)")


if __name__ == "__main__":
    failed = []
    for name in sorted(k for k in list(globals()) if k.startswith("test_")):
        try:
            globals()[name]()
            print("PASS %s" % name)
        except Exception as exc:                       # noqa: BLE001 -- report, never swallow
            failed.append(name)
            print("FAIL %s: %s" % (name, exc))
    print("%d passed, %d failed" % (
        len([k for k in globals() if k.startswith("test_")]) - len(failed), len(failed)))
    sys.exit(1 if failed else 0)
