#!/usr/bin/env python3
"""t1078 wiring: `serve.py bench` records the tok/s figure it prints.

RED ON MAIN, and red for the change alone. Every test drives the real cmd_bench, and cmd_bench
on main has no `_ledgers()` seam, so `_bench()` raises before it is invoked. That guard is not a
placeholder: on main the tool hard-codes workspace/measurements.jsonl, so running cmd_bench
unpatched would append a row to the LIVE ledger, and an oracle must not touch live state. On main
this oracle therefore runs nothing and fails with that reason stated.

What each test pins once the seam exists -- all of it read off the tool's own output, never
restated by the test:
  * the figure printed on stdout and the `value` in the perf-claims row are the SAME number;
  * the row names the model printed on the tool's own `model` line, not a placeholder;
  * the row carries setting / baseline_value / num_gpu / num_ctx / concurrent / warm / at /
    command, with metric == "tok_s" -- the fields scripts/tests/test_perf_claims.py needs to
    close a claim, and the names record_claim takes (scripts/check_measurements.py:129-130);
  * the task that owns the figure is recorded, taken from BENCH_TASK, and is never empty, because
    an empty task_id makes record_claim refuse the row (scripts/check_measurements.py:136-137);
  * the row bench has always appended to the measurements ledger is still appended, with the
    same value, so the wiring adds a carrier and does not repoint the ledger the figure gate reads;
  * a run the residency gate refuses prints no figure and mints no row.

It imports projects/local-inference/serve.py by path. Checked assumption: serve.py is a CLI whose
entry point sits behind `if __name__ == "__main__":`, so importing it executes imports, constants
and defs only; if a module-level side effect exists, the loader raises here and the failure says
so. Every path written is inside tempfile.TemporaryDirectory. No subprocess, no real network call
(urllib is faked, and the stub asserts the real code passes a timeout), no live server, nothing
left running, no live ledger written.
"""
import contextlib
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "projects" / "local-inference" / "serve.py"

_spec = importlib.util.spec_from_file_location("serve_under_test", MODULE)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"{MODULE} is not a loadable Python module")
serve = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(serve)

MODEL_TAG = "qwen3.8:27b-mtp-bf16"
PROMPT = "Count slowly from 1 to 200, one number per line."
SERVED = [16, 37, 63]        # warm-up round of 16 (discarded) + two DISTINCT measured rounds
MEASURED_TOKENS = 37 + 63     # == 100 == a.tokens, so the printed figure has a traceable source
RATE_RE = re.compile(r"^\s*decode\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*tok/s", re.M)
MODEL_RE = re.compile(r"^\s*model\s*:\s*(\S+)", re.M)


class _Reply:
    """Stand-in for the object urlopen returns: context manager, readable body."""

    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _rows(path):
    if not path.is_file():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


@contextlib.contextmanager
def _env(**pairs):
    saved = {k: os.environ.get(k) for k in pairs}
    for k, v in pairs.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _bench(tmp, task="t1078", resident=True, tokens=100, rounds=2):
    """Run the REAL cmd_bench with the engine, gate, model, endpoint and ledgers faked.

    Returns rc, stdout, stderr, the perf-claims rows, the measurements rows and the timeouts
    the tool passed on each request. Writes only inside `tmp`.
    """
    ledgers = getattr(serve, "_ledgers", None)
    if ledgers is None:
        raise AssertionError(
            "serve.py defines no _ledgers() seam, so `bench` hard-codes the live "
            "workspace/measurements.jsonl and records no perf-claims row. Driving cmd_bench "
            "unpatched would append a row to that live ledger, so this oracle ran nothing: the "
            "missing change is the seam plus the record_claim call in cmd_bench.")

    served = list(SERVED)
    timeouts = []

    def fake_urlopen(req, timeout=None):
        url = str(req.full_url)
        if not url.endswith("/v1/chat/completions"):
            raise AssertionError(f"unexpected request to {url}")
        if not timeout:
            raise AssertionError("cmd_bench issued a request with no timeout")
        if not served:
            raise AssertionError(f"the engine was asked more times than the run needs: {url}")
        timeouts.append(timeout)
        return _Reply(json.dumps({"usage": {"completion_tokens": served.pop(0)}}).encode())

    meas = Path(tmp) / "measurements.jsonl"
    perf = Path(tmp) / "perf-claims.jsonl"
    names = ("target", "loaded_model", "gate", "endpoint", "urllib", "_ledgers")
    saved = {n: getattr(serve, n, None) for n in names}
    serve.target = lambda a: ("vllm-iter6d", None)
    serve.loaded_model = lambda name: MODEL_TAG
    serve.gate = lambda name=None: (bool(resident), "resident" if resident else "NOT RESIDENT")
    serve.endpoint = lambda name=None: "http://127.0.0.1:9/v1"
    serve._ledgers = lambda: (meas, perf)
    serve.urllib = types.SimpleNamespace(request=types.SimpleNamespace(urlopen=fake_urlopen))
    args = types.SimpleNamespace(container="vllm-iter6d", tokens=tokens, rounds=rounds,
                                 prompt=PROMPT)
    out, err = io.StringIO(), io.StringIO()
    env = {} if task is None else {"BENCH_TASK": task}
    try:
        with _env(**env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = serve.cmd_bench(args)
    finally:
        for n in names:
            if saved[n] is None:
                try:
                    delattr(serve, n)
                except AttributeError:
                    pass
            else:
                setattr(serve, n, saved[n])
    return {"rc": rc, "out": out.getvalue(), "err": err.getvalue(),
            "perf": _rows(perf), "meas": _rows(meas), "timeouts": timeouts}


def test_bench_records_the_figure_it_prints():
    with tempfile.TemporaryDirectory() as tmp:
        r = _bench(tmp)
        assert r["rc"] == 0, (
            f"the faked run must reach the end; rc={r['rc']}\nOUT:\n{r['out']}\nERR:\n{r['err']}")
        assert len(r["perf"]) == 1, (
            f"`bench` must append exactly one perf-claims row for its figure; "
            f"got {len(r['perf'])}: {r['perf']}")
        row = r["perf"][0]
        printed = RATE_RE.search(r["out"])
        assert printed, f"bench must keep printing its decode line; stdout was:\n{r['out']}"
        assert row.get("value") == float(printed.group(1)), (
            f"the recorded figure must be the number that was printed: row value="
            f"{row.get('value')!r} printed={printed.group(1)!r}")
        assert row.get("metric") == "tok_s", (
            f"only a tok_s row closes a speed claim (check_measurements.py:212-214): {row}")
        shown = MODEL_RE.search(r["out"])
        assert shown and row.get("model") == shown.group(1), (
            f"the row must name the model that was measured, not a placeholder: "
            f"row model={row.get('model')!r} printed="
            f"{shown.group(1) if shown else None!r}")
        assert str(row.get("task_id") or "").strip() == "t1078", (
            f"the row must carry the task that owns the figure: {row}")
        for key in ("at", "setting", "baseline_value", "num_gpu", "num_ctx", "concurrent",
                    "warm", "command"):
            assert key in row, (
                f"the row is missing {key!r}; scripts/tests/test_perf_claims.py needs it to "
                f"close the claim. Row keys: {sorted(row)}")
        assert isinstance(row["setting"], str) and row["setting"].strip(), (
            f"the row must record the conditions it was taken under: {row['setting']!r}")
        command = str(row["command"])
        assert command.startswith("serve.py bench"), (
            f"the row must name the command that produced it: {command!r}")
        assert len(r["timeouts"]) == 3, (
            f"one request for the discarded warm round plus 2 measured rounds; "
            f"got {r['timeouts']}")


def test_a_recorded_figure_is_positive_and_traceable():
    with tempfile.TemporaryDirectory() as tmp:
        r = _bench(tmp)
        assert len(r["perf"]) == 1, f"one figure, one row: {r['perf']}"
        row = r["perf"][0]
        value = row.get("value")
        assert isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0, (
            f"a recorded figure must be a positive number, not a placeholder: {row}")
        assert isinstance(row.get("at"), str) and row["at"].strip(), (
            f"the row needs the date the figure was taken: {row}")
        assert row.get("warm") is True, (
            f"the 16-token round is discarded, so the figure is a warm figure: {row}")
        assert row.get("concurrent") == 1, f"bench measures a single stream: {row}"
        assert row.get("baseline_value") is None, (
            f"this run supplied no baseline, so the row must say so explicitly: {row}")
        assert MEASURED_TOKENS == 100, f"the stub's own arithmetic: {MEASURED_TOKENS}"


def test_the_existing_measurement_ledger_keeps_its_row():
    with tempfile.TemporaryDirectory() as tmp:
        r = _bench(tmp)
        assert len(r["meas"]) == 1, (
            "the wiring ADDS a carrier; `bench` must keep appending its row to the measurements "
            f"ledger the figure gate reads; got {len(r['meas'])}: {r['meas']}")
        assert len(r["perf"]) == 1, f"and exactly one claim row: {r['perf']}"
        assert r["meas"][0].get("unit") == "tok/s", f"its unit is tok/s: {r['meas'][0]}"
        assert r["meas"][0].get("value") == r["perf"][0].get("value"), (
            f"one figure, recorded identically in both carriers: {r['meas'][0]} vs {r['perf'][0]}")


def test_a_run_that_names_no_task_still_records_an_owner():
    with tempfile.TemporaryDirectory() as tmp:
        r = _bench(tmp, task=None)
        assert r["rc"] == 0, (
            f"an unowned run must not crash the bench: rc={r['rc']}\nERR:\n{r['err']}")
        assert len(r["perf"]) == 1, f"a printed figure must always be recorded: {r['perf']}"
        owner = str(r["perf"][0].get("task_id") or "").strip()
        assert owner and owner.lower() not in ("none", "null"), (
            f"an empty task_id makes record_claim refuse the row, so the fallback must be a "
            f"non-empty owner sentinel; got {r['perf'][0].get('task_id')!r}")


def test_a_refused_benchmark_records_no_figure_and_no_row():
    with tempfile.TemporaryDirectory() as tmp:
        r = _bench(tmp, resident=False)
        assert r["rc"] == 2, f"the residency gate must still refuse with exit 2: rc={r['rc']}"
        assert "REFUSING" in (r["err"] + r["out"]), (
            f"the refusal must still be said out loud:\n{r['err']}{r['out']}")
        assert r["perf"] == [], f"a refused run must mint no claim row: {r['perf']}"
        assert r["meas"] == [], f"a refused run must mint no measurement row: {r['meas']}"
        assert "tok/s" not in r["out"], f"a refused run must print no figure:\n{r['out']}"


if __name__ == "__main__":
    import traceback
    cases = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for case in cases:
        try:
            case()
            print(f"ok   {case.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {case.__name__}")
            traceback.print_exc()
    print(f"\n{len(cases) - failed}/{len(cases)} passed")
    sys.exit(1 if failed else 0)
