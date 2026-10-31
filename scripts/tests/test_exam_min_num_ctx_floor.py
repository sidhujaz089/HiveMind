#!/usr/bin/env python3
"""t1394 (Rung 1): exam.py must refuse to SIT a paper below its contract's num_ctx floor.

RED ON MAIN FOR THE MISSING CHANGE, and nothing else. Today `exam.py` has no contract
reader and no floor check: `main()` resolves ctx (`ctx = a.ctx or (serve.FLAGS["ctx"] if
on_vllm else settings.model_ctx(a.model))`) and walks straight into `ask()`, whose one
request is `urllib.request.urlopen(req, timeout=2400)` at projects/local-inference/exam.py
. This suite replaces `urllib.request.urlopen` with a RECORDING stub that RAISES, so a run
that reaches the model is a FAILURE of the oracle, not a call to a server. The empty-answer
class this closes is exactly that: a paper sat below its context and answered nothing.

Hermetic by construction: the contract is written into tempfile.mkdtemp() and passed to
`contract_floor(role, path)`; the refused run's `OUT` is redirected into the same temp dir
and asserted NOT created. No fixed /tmp path, no model call, no subprocess, no live state.
"""
import contextlib
import io
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]           # scripts/tests -> repo root
sys.path.insert(0, str(REPO / "projects" / "local-inference"))
import exam                                          # noqa: E402

CALLS = []


class _RefuseCall:
    """A urlopen replacement that records the attempt and refuses it. The floor must fire
    BEFORE any model call, so any construction of a request is a failing answer."""

    def __init__(self, *args, **kwargs):
        CALLS.append({"args": args, "kwargs": kwargs})
        raise AssertionError("exam.py contacted a model: the num_ctx floor did not fire")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _contract(tmp, role, floor):
    """The real schema shape from the task: min_prompt_tokens, expected_answer_tokens,
    min_num_ctx. Written here, never read from the live repo file."""
    p = Path(tmp) / "PAPER-CONTRACT.json"
    p.write_text(json.dumps({role: {"min_prompt_tokens": 8000,
                                    "expected_answer_tokens": 8000,
                                    "min_num_ctx": floor}}))
    return p


def test_refuses_below_the_floor_before_any_model_call():
    CALLS.clear()
    tmp = Path(tempfile.mkdtemp(prefix="t1394-"))
    contract = _contract(tmp, "ENDURANCE", 131072)
    out_dir = tmp / "results"
    saved_out = exam.OUT
    saved_urlopen = urllib.request.urlopen
    saved_argv = list(sys.argv)
    saved_stdout = sys.stdout
    buf = io.StringIO()
    code = None
    try:
        exam.CONTRACT = contract
        exam.OUT = out_dir
        urllib.request.urlopen = _RefuseCall
        sys.argv = ["exam.py", "oracle-model", "--role", "ENDURANCE", "--ctx", "32768"]
        with contextlib.redirect_stdout(buf):
            code = exam.main()
    finally:
        exam.OUT = saved_out
        urllib.request.urlopen = saved_urlopen
        sys.argv = saved_argv
        sys.stdout = saved_stdout
    text = buf.getvalue()
    CALLS.clear()

    # The task's Rung-1 words, asserted: exits 0, refusal_classified=true, no model call.
    assert code == 0, f"a below-floor sit must exit 0, got {code!r}; stdout tail: {text[-400:]!r}"
    assert CALLS == [], f"it contacted the model before refusing: {len(CALLS)} request(s) built"
    assert "refusal_classified=true" in text, (
        f"the refusal must be classified in the record; stdout was: {text[-400:]!r}")
    assert "131072" in text, (
        f"the refusal must name the contract floor it refused at; stdout: {text[-400:]!r}")
    # NEGATIVE assertions: a refusal is not a score, and it writes nothing.
    assert "real items" not in text, "a refused sit must not score the paper"
    assert not out_dir.exists(), "a refused sit must not write a result file"


def test_the_floor_is_read_from_the_contract_and_bounds_ctx():
    tmp = Path(tempfile.mkdtemp(prefix="t1394-floor-"))
    p = _contract(tmp, "ENDURANCE", 131072)
    # A number, not a truthy object: a floor that returns the dict passes a loose build.
    assert exam.contract_floor("ENDURANCE", p) == 131072, "must return the int min_num_ctx"
    assert exam.contract_floor("MANAGER", p) is None, (
        "a paper with no entry in the contract has no floor; a missing key must not crash")
    assert exam.contract_floor("ENDURANCE", tmp / "absent.json") is None, (
        "no contract file is an honest gap: no floor, sit allowed, not a crash")
    empty = tmp / "empty.json"
    empty.write_text("")
    assert exam.contract_floor("ENDURANCE", empty) is None, (
        "an unparseable contract is an honest gap: no floor, sit allowed, not a crash")


if __name__ == "__main__":
    failures = []
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print(f"ok   {name}")
        except Exception as exc:
            failures.append((name, f"{type(exc).__name__}: {exc}"))
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
