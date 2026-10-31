#!/usr/bin/env python3
"""Fail-first oracle for the t019 WER scorer added to the dictation-test harness.

Pins the pure scoring path (normalize / wer) and the thin file-reading seam
(score) that turns an engine's raw transcript into a comparable word-error-rate
number against the committed reference. Oracles never touch live state: the only
files read here are literal-string fakes written to a temp dir.

Fails today because projects/good-dictator/dictation-test/run.py has no
normalize/wer/score functions -- the mechanical scorer t019 is missing.
"""
import sys
import tempfile
from pathlib import Path

_DT = Path(__file__).resolve().parents[2] / "projects" / "good-dictator" / "dictation-test"
sys.path.insert(0, str(_DT))
import run  # noqa: E402


def _close(a, b, tol=1e-9):
    return abs(a - b) <= tol


def test_normalize_case_punct_whitespace():
    assert run.normalize("Hello,  World!") == ["hello", "world"]
    assert run.normalize("  F-Droid  ") == ["f", "droid"]
    assert run.normalize("42  things.") == ["42", "things"]
    assert run.normalize("") == []


def test_wer_perfect_match_is_zero():
    assert _close(run.wer("the cat sat", "the cat sat"), 0.0)
    assert _close(run.wer("The Cat, SAT.", "the cat sat"), 0.0)


def test_wer_single_substitution():
    # reference has 3 tokens; one substitution -> 1/3
    assert _close(run.wer("the dog sat", "the cat sat"), 1 / 3)


def test_wer_single_deletion():
    # hypothesis dropped one of the reference's 3 tokens -> 1/3
    assert _close(run.wer("the sat", "the cat sat"), 1 / 3)


def test_wer_single_insertion():
    # hypothesis has one extra token -> 1 edit over 3 ref tokens -> 1/3
    assert _close(run.wer("the cat dog sat", "the cat sat"), 1 / 3)


def test_wer_empty_hypothesis_is_one():
    # every reference token is an insertion -> 3 edits / 3 = 1.0
    assert _close(run.wer("", "the cat sat"), 1.0)


def test_wer_empty_reference_returns_zero():
    assert _close(run.wer("the cat", ""), 0.0)
    assert _close(run.wer("", ""), 0.0)


def test_score_reads_two_fake_files():
    with tempfile.TemporaryDirectory() as d:
        hyp = Path(d, "hyp.txt")
        ref = Path(d, "ref.txt")
        hyp.write_text("the dog sat", encoding="utf-8")
        ref.write_text("the cat sat", encoding="utf-8")
        assert _close(run.score(hyp, ref), 1 / 3)


def test_score_missing_file_raises_not_swallows():
    with tempfile.TemporaryDirectory() as d:
        hyp = Path(d, "hyp.txt")
        hyp.write_text("the cat sat", encoding="utf-8")
        try:
            run.score(hyp, Path(d, "does_not_exist.txt"))
        except (OSError, FileNotFoundError):
            return
        raise AssertionError("score() swallowed a missing-file error instead of raising")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"ok   {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {type(e).__name__}: {e}")
    if failed:
        print(f"{failed} of {len(fns)} failed")
        sys.exit(1)
    print(f"all {len(fns)} passed")
