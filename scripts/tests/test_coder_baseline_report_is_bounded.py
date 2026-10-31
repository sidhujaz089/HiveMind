#!/usr/bin/env python3
"""t1000: the sandbox baseline report lists every failing suite name verbatim.

coder.py line 2567-2568 builds the baseline report by joining every failing suite name into
one string: say(f" {len(already)} suite(s) already failing" + (f": {', '.join(...)}")). Line
2566 records the same list whole under _RUN_FACTS["baseline_failing"], which the run's row in
workspace/agent-runs.jsonl carries as its "baseline_failing" key. The join has no bound and the
cut point is invisible: a reader of a dispatch log sees 62 filenames with no statement of how
many there were, no notice of any limit, and no pointer to the record that holds the full list.

never-truncate-a-record: a value handed on may be bounded -- context and log lines are finite --
but the bound must announce itself, say how much is missing, and say where the whole thing
lives. The full list is NOT lost by this change; it stays recorded where it is today. What
changes is the line that gets printed by say (def say at line 275: stdout + the live log).

Tests 1, 2, 3 and 5 are red on main for the missing change: coder.py has no baseline_line, its
baseline report assembles the name list inline, and tests 2 and 3 therefore raise AttributeError.
Test 4 pins that a list nobody has to trim is not clipped, byte for byte.

Run: python3 scripts/tests/test_coder_baseline_report_is_bounded.py
"""
import ast
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests/ -> repo root
CODER_PATH = ROOT / "projects" / "local-inference" / "coder.py"

_SPEC = importlib.util.spec_from_file_location("t1000_coder_under_test", CODER_PATH)
coder = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(coder)


def real_names(n):
    """Suite paths shaped like the real baseline: run_suites returns paths, not basenames."""
    return ["scripts/tests/test_suite_%03d.py" % i for i in range(n)]


def test_baseline_line_exists():
    assert hasattr(coder, "baseline_line"), (
        "coder.py has no baseline_line(); the baseline report at coder.py:2567-2568 joins "
        "every failing suite name into one printed line, verbatim, on every dispatch")


def test_a_long_baseline_list_is_bounded_and_announces_the_cut():
    names = real_names(62)
    line = coder.baseline_line(names, limit=12)
    assert isinstance(line, str), (
        f"baseline_line must return the report line, got {type(line)}")
    assert "suite(s) already failing" in line, (
        f"the report keeps its existing shape: the count leads the line. got {line!r}")
    assert line.count("test_suite_") <= 12, (
        f"a 62-name baseline must not be listed whole; got "
        f"{line.count('test_suite_')} names in {line!r}")
    assert "test_suite_011" in line, (
        f"the first `limit` names are the ones shown; got {line!r}")
    assert "test_suite_061" not in line, (
        f"a bounded line must not leak the names it omitted; got {line!r}")
    assert "TRUNCATED after 12 of 62" in line, (
        "the cut must say it happened and how much is missing, per "
        f"never-truncate-a-record; got {line!r}")
    assert "baseline_failing" in line, (
        'the line must point at where the full list lives -- line 2566 records it under '
        '"baseline_failing"; got {line!r}')


def test_a_short_baseline_list_is_not_clipped():
    names = ["scripts/tests/test_alpha.py", "scripts/tests/test_beta.py"]
    line = coder.baseline_line(names, limit=12)
    expected = " 2 suite(s) already failing: test_alpha.py, test_beta.py"
    assert line == expected, (
        f"a baseline at or under the limit prints byte-identical to today's line; "
        f"expected {expected!r} got {line!r}")
    assert "TRUNCATED" not in line, (
        f"a list nobody has to trim must not grow a truncation notice; got {line!r}")


def test_the_baseline_report_routes_through_the_function():
    """The report SITE, not the definition: any say() that prints the baseline line must build
    it with baseline_line. A rule stated only in the brief is a rule the next dispatch may
    legally ignore -- this is the assertion that makes an unused function fail the suite."""
    src = CODER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "say":
            text = ast.get_source_segment(src, node) or ""
            if "suite(s) already failing" in text and "baseline_line(" not in text:
                offenders.append(text.strip().splitlines()[0].strip())
    assert not offenders, (
        "the baseline report still assembles the suite-name list inline inside say(); route "
        f"it through baseline_line. found: {offenders}")


if __name__ == "__main__":
    _fns = [v for k, v in sorted(globals().items())
            if k.startswith("test_") and callable(v)
            and getattr(v, "__module__", None) == __name__]
    _failed = 0
    for _fn in _fns:
        try:
            _fn()
            print("PASS " + _fn.__name__)
        except Exception as _e:  # a missing symbol is a finding, not a crash
            _failed += 1
            print("FAIL %s: %s: %s" % (_fn.__name__, type(_e).__name__, _e))
    print(f"{len(_fns) - _failed}/{len(_fns)} passed")
    sys.exit(1 if _failed else 0)
