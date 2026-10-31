#!/usr/bin/env python3
"""t1257: the sweep pre-cleans a ceiling-declared flaky suite's scratch dir.

The mechanism this pins is t286's poisoning: a flaky builder suite fails under the
4-at-a-time pool and leaves /tmp/review-brief-<hash> behind (that dir name is the
brief-content hash -- coder.py:1322 and coder.py:2478), so the NEXT run of the same
suite refuses to start on its own leftover. The ceiling declares such suites with
kind=flake; before each sweep, scripts/run_tests.py must remove exactly the scratch
dirs the flake entries name, and must touch nothing else.

Red on main for one reason: scripts/run_tests.py defines no pre_clean_flaky_scratch().

Run: python3 scripts/tests/test_flaky_scratch_preclean.py   (no pytest installed)
"""
import sys
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import run_tests  # noqa: E402

FUNC = "pre_clean_flaky_scratch"

# The real scratch-dir prefix, taken from coder.py's label: coder.py:2478 builds
# "brief-" + sha256(brief)[:8]; the full dir is /tmp/review-brief-<8 hex>.
PREFIX = "review-brief-"

MISSING = f"scripts/run_tests.py defines no {FUNC}() -- the sweep has no pre-clean step, " \
          "so a flaky suite's leftover /tmp/review-brief-* dir blocks its next run (t286)"


def _suite_dir(root, name):
    """A scratch dir that is not empty: rm -rf must take the tree, not just the dir."""
    d = Path(root) / name
    (d / "worktree" / "app").mkdir(parents=True)
    (d / "worktree" / "app" / "main.py").write_text("def f():\n    return 1\n",
                                                    encoding="utf-8")
    (d / "brief.txt").write_text("the brief text", encoding="utf-8")
    return d


def _ceiling(tmp, names):
    """names: {suite: (kind_or_None, scratch_name)} -> the dict the sweep reads."""
    out = {}
    for suite, (kind, scratch) in names.items():
        out[suite] = scratch if kind is None else {"kind": kind, "scratch": scratch}
    return out


def test_preclean_is_defined_in_run_tests():
    assert hasattr(run_tests, FUNC), MISSING


def test_it_removes_the_flake_scratch_and_leaves_every_other_dir_alone():
    fn = getattr(run_tests, FUNC, None)
    assert fn is not None, MISSING
    with tempfile.TemporaryDirectory() as td:
        flake = _suite_dir(td, PREFIX + "6e09edcd")      # t286's own dir name
        defect = _suite_dir(td, PREFIX + "34622d47")     # a real defect suite
        unkinded = _suite_dir(td, PREFIX + "b698d36b")   # entry without kind yet
        stringy = _suite_dir(td, PREFIX + "c80a5fec")    # a string-valued entry
        fn(_ceiling(td, {
            "test_coder_branch_not_clobbered.py": ("flake", PREFIX + "6e09edcd"),
            "test_options_contract.py": ("defect", PREFIX + "34622d47"),
            "test_coder_delivers_what_it_verified.py": (None, PREFIX + "b698d36b"),
            "test_coder_green_count.py": (None, PREFIX + "c80a5fec"),
        }), td)
        assert not flake.exists(), (
            "a kind=flake entry's scratch dir survived the pre-clean -- that suite's "
            "next sweep still refuses to start on its own leftover (t286)")
        for kept, why in ((defect, "kind=defect"),
                          (unkinded, "no kind declared yet"),
                          (stringy, "a string-valued entry")):
            assert kept.exists(), (
                f"{kept.name} was removed, and it is a {why} entry -- the pre-clean "
                "must touch ONLY entries whose kind is exactly 'flake'")


def test_it_reports_what_it_removed():
    fn = getattr(run_tests, FUNC, None)
    assert fn is not None, MISSING
    with tempfile.TemporaryDirectory() as td:
        flake = _suite_dir(td, PREFIX + "6e09edcd")
        _suite_dir(td, PREFIX + "34622d47")
        out = fn(_ceiling(td, {
            "test_coder_branch_not_clobbered.py": ("flake", PREFIX + "6e09edcd"),
            "test_options_contract.py": ("defect", PREFIX + "34622d47"),
        }), td)
        assert isinstance(out, dict), f"expected a dict report, got {type(out).__name__}"
        assert out.get("removed") == [str(Path(td) / (PREFIX + "6e09edcd"))], (
            "the report must name the exact path(s) removed, so the sweep's log line "
            "'pre-cleaned N' is the count of real removals, not of entries seen")
        assert out.get("refused") == [], (
            f"nothing here is out of bounds, so refused must be empty; got {out.get('refused')}")


def test_it_is_idempotent():
    fn = getattr(run_tests, FUNC, None)
    assert fn is not None, MISSING
    with tempfile.TemporaryDirectory() as td:
        _suite_dir(td, PREFIX + "6e09edcd")
        ceiling = _ceiling(td, {"test_coder_branch_not_clobbered.py":
                                ("flake", PREFIX + "6e09edcd")})
        fn(ceiling, td)
        out = fn(ceiling, td)
        assert out["removed"] == [], (
            "the second sweep of a run must report nothing removed -- a dir that was "
            "already gone is not a removal, and the log line must not over-count")
        assert out["refused"] == [], (
            "a declared flake entry whose dir is simply absent is not a refusal; got "
            + repr(out["refused"]))


def test_a_missing_scratch_dir_is_not_an_error():
    fn = getattr(run_tests, FUNC, None)
    assert fn is not None, MISSING
    with tempfile.TemporaryDirectory() as td:
        out = fn(_ceiling(td, {"test_coder_branch_not_clobbered.py":
                               ("flake", PREFIX + "deadbeef")}), td)  # never created
        assert out["removed"] == [], "nothing existed to remove"
        assert out["refused"] == [], (
            "the healthy common case -- a flaky suite that left nothing behind -- "
            f"must not be reported as a problem; got {out['refused']}")


def test_it_refuses_a_name_that_escapes_the_scratch_root():
    """A typo'd ceiling entry must never turn the pre-clean into rm -rf outside the
    scratch root. The ceiling is data; the path it names is not trusted."""
    fn = getattr(run_tests, FUNC, None)
    assert fn is not None, MISSING
    with tempfile.TemporaryDirectory() as td:
        victim = Path(td).parent / (PREFIX + "victim")
        victim.mkdir(parents=True, exist_ok=True)
        try:
            rel = _ceiling(td, {"x.py": ("flake", f"../{victim.name}")})
            out = fn(rel, td)
            assert victim.exists(), (
                "a scratch name containing '..' was followed out of the scratch root "
                "and deleted -- the pre-clean must refuse, not escalate, a bad path")
            assert len(out["refused"]) == 1 and victim.name in out["refused"][0], (
                f"a refused entry must appear in out['refused'] by name so the sweep "
                f"log says what was skipped; got {out['refused']}")
            assert out["removed"] == [], "an out-of-root path must never be removed"

            victim.mkdir(parents=True, exist_ok=True)
            out2 = fn(_ceiling(td, {"y.py": ("flake", str(victim))}), td)
            assert victim.exists(), \
                "an absolute scratch path was deleted; it is outside the scratch root " \
                "the caller passed"
            assert len(out2["refused"]) == 1, (
                f"an absolute scratch path must be reported as refused; got {out2}")
        finally:
            shutil.rmtree(victim, ignore_errors=True)


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as e:
            failed.append(name)
            print(f"FAIL {name}: {e}")
        except Exception as e:
            failed.append(name)
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} passed"
          + (f" -- FAILED: {', '.join(failed)}" if failed else ""))
    sys.exit(1 if failed else 0)
