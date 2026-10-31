"""t1256: a guard suite must not pin the identifier the acceptance block uses.

The dispatch that builds t1113's collateral gate (p541-t1113-coder, 2026-10-16T07:53:25Z)
was refused by scripts/tests/test_coder_guards.py, not by its own target: attempts 4 and 6
report `tests fail (1: test_coder_guards.py)`. That suite asserts, against
projects/local-inference/coder.py's source text, the literal
`"broke": new_failures, "ops": obj.get("ops")` (test_coder_guards.py:436) and the message
fragment `broke {len(new_failures)} test suite` (test_coder_guards.py:595). t1113 moves the
record from every red suite to the ones the brief NAMED -- renaming the local is the change,
not a side effect of it -- so a correct one-file patch has no path to green, and the brief
forbids editing the file that blocks it. A requirement that cannot be met honestly is met
dishonestly: the only door left is deleting the assertion.

The general defect is a source-text pin on a build-side file's local identifier, which blocks
every future dispatch whose contract renames it. The property that is real is the BEHAVIOUR:
the record says which suites broke, and it names the same list the message counts.

This suite reads scripts/tests/test_coder_guards.py as DATA and asserts on it. It imports
nothing from the repository and starts no process.

Run: python3 scripts/tests/test_coder_guards_rename_proof.py
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUARDS = ROOT / "scripts" / "tests" / "test_coder_guards.py"

# Identifiers that live inside coder.py's acceptance/verdict block -- the code that decides
# ACCEPTED vs TESTS-FAILED. Verified 2026-10-27 by reading projects/local-inference/coder.py
# lines 3225-3271: it builds the list at :3230, counts it in the message at :3267, records it
# at :3249 and gates acceptance on its emptiness at :3235. `hard` is the name t1113's
# collateral gate gives the narrowed list, so a pin on it is pinned to the future as well.
ACCEPTANCE_NAMES = ("new_failures", "hard")


def _pins(text):
    """Quoted literals in the guard suite that name an acceptance-block identifier.

    A pin is a string literal (any of the accepted quote styles in this codebase) carrying
    the identifier, on a line that sits within three lines of an `in body` comparison --
    `body` is the name the suite binds to coder.py's source, and the existing pins all use
    it (`report(..., "..." in body, True)`).
    """
    lines = text.splitlines()
    hits = []
    for i, line in enumerate(lines):
        for name in ACCEPTANCE_NAMES:
            if not re.search(r'["\'][^"\']*' + re.escape(name) + r'\b[^"\']*["\']', line):
                continue
            window = "\n".join(lines[max(0, i - 3):i + 4])
            if "in body" in window:
                hits.append((i + 1, name, line.strip()))
    return hits


def test_the_guard_suite_is_readable():
    assert GUARDS.is_file(), ("no guard suite at %s -- if it was moved, update this "
                              "path in this file; do not delete the test" % GUARDS)
    text = GUARDS.read_text(encoding="utf-8")
    assert "in body" in text, ("the guard suite no longer reads coder.py's source; the "
                               "subject of this suite has moved -- find the new home of the "
                               "source-text pins and retarget, do not drop the guard")


def test_no_report_in_the_guard_suite_pins_an_acceptance_identifier():
    hits = _pins(GUARDS.read_text(encoding="utf-8"))
    assert hits == [], (
        "scripts/tests/test_coder_guards.py pins the NAME of a variable that lives inside "
        "coder.py's acceptance block: %r. A one-file dispatch on coder.py cannot edit this "
        "suite, so a patch that renames that variable -- which is what t1113 does -- has no "
        "legal path to green and the run is refused on a suite outside its own contract "
        "(t1256: p541-t1113-coder, attempts 4 and 6, 'tests fail (1: test_coder_guards.py)'). "
        "Pin the behaviour the line protects: which list the record carries, and that it is "
        "the list the message counts. A regex over the record, or a fragment of the message "
        "after the count, says the same thing and survives any rename."
        % (hits,))


def test_the_guard_still_records_ops_and_the_broke_list():
    # The anti-weakening assertion. Making the suite above green by DELETING the pins also
    # passes it, so the assertions being de-identified must survive in the file: a run that
    # ends TESTS-FAILED has to record what the model tried and which suites it broke, and
    # the guard that says so is the record's only defence. Deleting either is the
    # check-satisfied-by-dead-path failure this task exists to stop.
    text = GUARDS.read_text(encoding="utf-8")
    assert '"ops": obj.get("ops")' in text, (
        "the guard asserting that a failed attempt records its ops is gone -- that is the "
        "ledger entry answering 'what did the model actually try?'; restore it, do not "
        "drop it to satisfy the rename rule")
    assert '"broke":' in text, (
        "the guard asserting that a failed attempt names the suites it broke is gone; the "
        "record must say WHICH suites, not only that the attempt failed")


def test_the_detector_finds_a_planted_pin():
    # A detector that matches nothing passes on every codebase and guards nothing -- the
    # vacuous-fixture failure mode. Plant the defect and require the detector to find it.
    planted = ('report("the message the model reads",\n'
               '       "broke {len(new_failures)} test suite",\n'
               '       True)\n'
               'report("x", "y" in body, True)\n')
    got = _pins(planted)
    assert [h[1] for h in got] == ["new_failures"], (
        "the detector misses the exact shape that blocked t1113: %r" % (got,))
    assert got[0][0] == 2, "the detector must report the line the pin is on, not the call: %r" % (got,)
    # And the NEGATIVE case: the rewrite this scope ships must not itself fire.
    behaviour = ('report("  and names the same list the message counts",\n'
                 '       bool(_msg) and bool(_rec) and _msg.group(1) == _rec.group(1), True)\n'
                 'report("x", "y" in body, True)\n')
    assert _pins(behaviour) == [], (
        "the detector fires on a pin that names a behaviour rather than an identifier: %r"
        % (_pins(behaviour),))


if __name__ == "__main__":
    tests = sorted((n, f) for n, f in globals().items()
                    if n.startswith("test_") and callable(f))
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 -- a crashing test is a failure, not a pass
            failures += 1
            print("FAIL %s: %s: %s" % (name, type(exc).__name__, exc))
        else:
            print("ok   %s" % name)
    print("\n%d/%d passed" % (len(tests) - failures, len(tests)))
    raise SystemExit(1 if failures else 0)
