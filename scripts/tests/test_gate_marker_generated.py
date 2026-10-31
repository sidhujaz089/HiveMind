#!/usr/bin/env python3
"""Oracle for t1029: STATE.md's gate-closure marker must be GENERATED from the ledger.

Red on main for ONE reason: scripts/check_gate_closures.py has no marker_line(),
render_gate_table(), check_marker_generated(), write_marker() or _ledger_and_backed(),
so every test below that touches them raises AttributeError. The single test that passes
today -- test_the_existing_check_contract_still_catches_an_unbacked_claim -- is a
regression guard on the FROZEN check() contract and is green on purpose.

Why this module: STATE.md's `<!-- gate-closures: ... -->` line is written by the same hand
as the claim it evidences, so a hand-typed marker out-votes workspace/gate-closures.json
(gates 1, 2, 4, 5 were claimed against an empty ledger, 2026-09-12 to 2026-09-30). The
fix is that the marker is GENERATED and the generator is the only writer. See
workspace/rca/2e623c0cc9da.md.

This suite deliberately does NOT parse STATE.md's prose for verdict words: the module's own
docstring says prose parsing is unsound (STATE.md reads "**NOT CLOSED.**", which a grep
scores as a closure). Marker bytes and ledger fields only.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_gate_closures  # exists on main; the five new names are what is red


def _entry(name, file, line, inp, pos, neg, recorded):
    """One ledger entry, shaped exactly like the real workspace/gate-closures.json."""
    return {"gate": name,
            "wiring": {"file": file, "line": line, "input": inp},
            "correctness": {"positive_refused": pos, "negative_passed": neg},
            "recorded": recorded}


def _real_entries():
    """The three real ledger entries, values copied from workspace/gate-closures.json.

    Distinct per gate on purpose: a generator that stamps every gate with one placeholder
    string cannot pass these tests (failure mode 2 of writing-oracles-for-delegation).
    Insertion order 2, 5, 1 -- NOT sorted -- so the marker's ordering is pinned too.
    """
    return {
        "2": _entry("Extractor migration",
                    "scripts/hooks/pre-commit", 111,
                    "every staged .py file on every commit in this clone",
                    "a staged greedy brace pattern exits 1 and names greedy",
                    "an ordinary file exits 0",
                    "2026-09-30 by claude-code/opus-5.5"),
        "5": _entry("Interlock",
                    "projects/local-inference/console.py", 764,
                    "every model call through console.ask",
                    "console.ask returned done_reason lease_held against a held lease",
                    "the lease holder is not refused",
                    "2026-09-30 by claude-code/opus-5.5"),
        "1": _entry("Run accounting",
                    "scripts/dispatch_loop.py", 2184,
                    "every started row in workspace/dispatch-loop.jsonl",
                    "a job with no finished row is accounted lost, once",
                    "a finished job is not accounted lost",
                    "2026-10-02 by claude-code/opus-5.5"),
    }


def _write_ledger(root, ledger):
    (root / "workspace").mkdir(exist_ok=True)
    (root / "workspace" / "gate-closures.json").write_text(
        json.dumps(ledger, indent=1), encoding="utf-8")


def _root_with(tmp, ledger, marker_line="<!-- gate-closures: 1, 2, 5 -->"):
    """A throwaway repo root: STATE.md + workspace/gate-closures.json, nothing else.

    Everything is written under the caller's TemporaryDirectory, so running this suite
    cannot dirty the worktree.
    """
    root = Path(tmp)
    _write_ledger(root, ledger)
    state = "# STATE.md\n\n"
    if marker_line is not None:
        state += marker_line + "\n"
    state += "\n## Right now\n\nProse a generator owns, never a checker.\n"
    (root / "STATE.md").write_text(state, encoding="utf-8")
    return root


def _state(root):
    return (root / "STATE.md").read_text(encoding="utf-8")


def test_marker_line_is_the_ledger_ids_sorted_numerically():
    with tempfile.TemporaryDirectory() as td:
        root = _root_with(td, _real_entries())
        got = check_gate_closures.marker_line(root)
        assert got == "<!-- gate-closures: 1, 2, 5 -->", (
            "marker_line must emit the marker line STATE.md is required to carry, ids "
            "sorted numerically; the ledger backs 1, 2, 5 (inserted 2, 5, 1) and got %r"
            % (got,))


def test_a_gate_whose_ledger_entry_is_incomplete_is_not_in_the_marker():
    with tempfile.TemporaryDirectory() as td:
        ledger = _real_entries()
        # No "correctness" block: a stub entry a writer types to back a gate with no
        # evidence. The completeness rule check() uses (wiring AND correctness fields)
        # must keep it out of the marker.
        ledger["4"] = _entry("Corrupt-ledger finding", "scripts/runledger.py", 120,
                             "each run", "pos", "neg", "2026-10-11 by hand")
        ledger["4"].pop("correctness")
        root = _root_with(td, ledger)
        got = check_gate_closures.marker_line(root)
        assert "4" not in got, (
            "a gate whose ledger entry has no correctness block was marked closed: %r" % (got,))
        assert got == "<!-- gate-closures: 1, 2, 5 -->", "got %r" % (got,)


def test_a_marker_that_is_the_generated_bytes_is_clean():
    with tempfile.TemporaryDirectory() as td:
        root = _root_with(td, _real_entries())
        expected = check_gate_closures.marker_line(root)
        (root / "STATE.md").write_text(
            _state(root).replace("<!-- gate-closures: 1, 2, 5 -->", expected),
            encoding="utf-8")
        problems = check_gate_closures.check_marker_generated(root)
        assert problems == [], (
            "a STATE.md carrying exactly the generated marker was reported as a problem: %s"
            % (problems,))


def test_a_hand_typed_marker_that_only_differs_in_spacing_is_a_finding():
    with tempfile.TemporaryDirectory() as td:
        root = _root_with(td, _real_entries(), marker_line="<!-- gate-closures: 1,2,5 -->")
        problems = check_gate_closures.check_marker_generated(root)
        assert problems, (
            "a marker typed by hand with the ids jammed together (1,2,5) raised no problem; "
            "byte-equality to the generated line is the whole point of t1029")
        joined = " ".join(problems)
        assert "hand-typed" in joined, (
            "the finding must name the defect a person would name -- hand-typed: %s" % (problems,))
        assert "1, 2, 5" in joined, (
            "the finding must state the marker the ledger generates, so the fixer knows "
            "what to write: %s" % (problems,))


def test_a_hand_typed_marker_naming_an_unbacked_gate_is_a_finding():
    with tempfile.TemporaryDirectory() as td:
        root = _root_with(td, _real_entries(),
                          marker_line="<!-- gate-closures: 1, 2, 4, 5 -->")
        problems = check_gate_closures.check_marker_generated(root)
        assert problems, "a marker claiming gate 4, which the ledger does not back, was accepted"
        assert "4" in " ".join(problems), (
            "the finding must name the offending gate: %s" % (problems,))


def test_a_state_md_with_no_marker_line_is_a_finding():
    with tempfile.TemporaryDirectory() as td:
        root = _root_with(td, _real_entries(), marker_line=None)
        problems = check_gate_closures.check_marker_generated(root)
        assert problems, "a STATE.md with no gate-closures marker reported no problem"
        assert any("marker" in p for p in problems), (
            "the finding must name the marker: %s" % (problems,))


def test_a_missing_state_md_is_a_finding_not_a_crash():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_ledger(root, _real_entries())
        problems = check_gate_closures.check_marker_generated(root)
        assert isinstance(problems, list) and problems, (
            "check_marker_generated must report a missing STATE.md, not raise")
        assert any("STATE.md" in p for p in problems), ("got %s" % (problems,))


def test_a_corrupt_ledger_is_a_finding_not_a_crash():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "workspace").mkdir()
        (root / "workspace" / "gate-closures.json").write_text("{not json", encoding="utf-8")
        (root / "STATE.md").write_text(
            "# STATE.md\n\n<!-- gate-closures: 1, 2, 5 -->\n", encoding="utf-8")
        problems = check_gate_closures.check_marker_generated(root)
        assert isinstance(problems, list), "an unparseable ledger raised instead of reporting"
        assert any("gate-closures.json" in p for p in problems), (
            "the handler must RECORD why the marker cannot be computed, not fall back "
            "silently to an empty marker: %s" % (problems,))
        assert check_gate_closures.marker_line(root) == "<!-- gate-closures:  -->", (
            "an unreadable ledger backs no gate, so the generated marker is the empty one")
        assert check_gate_closures.MARKER_RE.match("<!-- gate-closures:  -->") is not None, (
            "the empty generated marker must still be a marker the existing MARKER_RE matches")


def test_the_prose_table_is_rendered_from_the_ledger_and_names_only_backed_gates():
    with tempfile.TemporaryDirectory() as td:
        root = _root_with(td, _real_entries())
        table = check_gate_closures.render_gate_table(root)
        for want in ("Run accounting", "Extractor migration", "Interlock",
                     "scripts/dispatch_loop.py", "2184", "2026-10-02", "2026-09-30",
                     "| Gate 1 |", "| Gate 2 |", "| Gate 5 |"):
            assert want in table, "render_gate_table omitted %r, got:\n%s" % (want, table)
        # NEGATIVE: a gate absent from the ledger must not appear in prose written from it.
        for absent in ("Corrupt-ledger finding", "| Gate 4 |", "| Gate 3 |", "| Gate 6 |"):
            assert absent not in table, (
                "render_gate_table invented gate prose for %r, which the ledger does not "
                "back:\n%s" % (absent, table))


def test_write_marker_rewrites_a_hand_typed_marker_and_renders_the_prose():
    with tempfile.TemporaryDirectory() as td:
        root = _root_with(td, _real_entries(),
                          marker_line="<!-- gate-closures: 1, 2, 4, 5 -->")
        problems = check_gate_closures.write_marker(root)
        assert problems == [], "write_marker reported problems on a writable repo: %s" % (problems,)
        text = _state(root)
        assert "<!-- gate-closures: 1, 2, 5 -->" in text, (
            "write_marker did not put the generated marker in STATE.md:\n%s" % (text,))
        assert "1, 2, 4, 5" not in text, (
            "the hand-typed gate 4 survived the rewrite:\n%s" % (text,))
        assert "Run accounting" in text and "Extractor migration" in text and "Interlock" in text, (
            "write_marker must render the prose gate table in the same write, from the ledger"
            ":\n%s" % (text,))
        assert check_gate_closures.check_marker_generated(root) == [], (
            "STATE.md is still not byte-equal to what the ledger generates after write_marker")


def test_write_marker_refuses_a_state_md_with_no_marker_to_replace():
    with tempfile.TemporaryDirectory() as td:
        root = _root_with(td, _real_entries(), marker_line=None)
        before = _state(root)
        problems = check_gate_closures.write_marker(root)
        assert problems, (
            "write_marker silently succeeded on a STATE.md with no marker line -- appending "
            "a second marker would make the file ambiguous")
        assert any("marker" in p for p in problems), ("got %s" % (problems,))
        assert _state(root) == before, (
            "STATE.md was edited by a write that reported a problem: a refusal must not "
            "change the file")


def test_the_existing_check_contract_still_catches_an_unbacked_claim():
    """check() is the FROZEN contract of scripts/tests/test_gate_closures.py. Green today,
    green after: the new generator may not change it."""
    with tempfile.TemporaryDirectory() as td:
        root = _root_with(td, {"2": _real_entries()["2"]},
                          marker_line="<!-- gate-closures: 1, 2, 5 -->")
        problems = check_gate_closures.check(root)
        assert any("gate 1" in p and "not backed" in p for p in problems), (
            "check() no longer reports a marker claiming a gate the ledger does not back: %s"
            % (problems,))
        assert any("gate 5" in p for p in problems), (
            "check() no longer reports gate 5 as unbacked: %s" % (problems,))


def test_the_live_repo_marker_is_what_the_live_ledger_generates():
    """The real artefacts, read read-only. This is the fixture that survives the session:
    if STATE.md's marker drifts from workspace/gate-closures.json, this goes red.
    Verified on main: STATE.md carries exactly one marker line, reading
    '<!-- gate-closures: 1, 2, 5 -->', and the ledger backs gates 1, 2 and 5.
    """
    text = (ROOT / "STATE.md").read_text(encoding="utf-8")
    found = check_gate_closures.MARKER_RE.findall(text)
    assert len(found) == 1, (
        "STATE.md must carry exactly one '<!-- gate-closures: ... -->' marker line; found %d"
        % (len(found),))
    line = next(ln for ln in text.splitlines() if ln.strip().startswith("<!--")
                and "gate-closures" in ln)
    generated = check_gate_closures.marker_line(ROOT)
    assert line.strip() == generated, (
        "STATE.md's marker is hand-typed: STATE.md reads %r, workspace/gate-closures.json "
        "generates %r -- regenerate STATE.md, do not hand-edit it" % (line.strip(), generated))


if __name__ == "__main__":
    _tests = [v for k, v in sorted(globals().items())
              if k.startswith("test_") and callable(v) and getattr(v, "__module__", None) == __name__]
    assert _tests, "no tests collected"
    _failed = []
    for _t in _tests:
        try:
            _t()
            print("PASS %s" % _t.__name__)
        except Exception as _e:  # a suite that cannot run is a FAIL, never a silent pass
            _failed.append(_t.__name__)
            print("FAIL %s: %s: %s" % (_t.__name__, type(_e).__name__, _e))
    print("%d/%d passed" % (len(_tests) - len(_failed), len(_tests)))
    raise SystemExit(1 if _failed else 0)
