#!/usr/bin/env python3
"""t1448 -- the Auditor's model-input length must be MEASURABLE and ATTRIBUTABLE.

The finding this pins is the deferral in workspace/rca/f282b5398e8e.md: "I haven't
checked how long they are compared with its earlier work."  The length half is
already recorded -- workspace/run-telemetry.jsonl, written by scripts/run_telemetry.py,
carries prompt_eval_count on 28009 of its 29225 rows.  What no row carries is WHICH
work the call served: a key census of all 29225 rows finds no 'kind' field, so the
comparison the finding defers has no denominator in any record on disk.  A number
that cannot be attributed to an input is the same failure as a number that was never
measured -- the 703 '.json--*' dispatch logs are the proof (f282b5398e8e.md:71/85).

So this suite pins the recorder's contract, not today's numbers: a recorded row that
carries the prompt size AND the kind of work.  It reads the real artefacts (the real
telemetry ledger, the real dispatch directory) so no synthetic string can satisfy it,
and it drives run_telemetry.record() into a temporary directory -- the live ledger is
redirected and restored, so running this suite writes nothing the repository owns.

Red on main for two assertions and only two: the row's kind (test 1) and the ledger's
population split (test 2).  Tests 1-length, 3, 4 and 5 pass on main by design: they
record the measured answer, the ledger's integrity, and the empty-stem regression, so
the suite can never pass vacuously against an empty tree.

Failure details are printed whole. No value in a failure message is sliced.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import run_telemetry  # noqa: E402  the real recorder: scripts/run_telemetry.py

WORKSPACE = ROOT / "workspace"
DISPATCH_DIR = WORKSPACE / "run-logs" / "dispatch"
TELEMETRY_NAME = "run-telemetry" + ".jsonl"

# Distinct real values, taken from the artefacts themselves.  One placeholder tag
# cannot tell an RCA job from an earlier script-review job.
MODEL_56K = "qwen3.8:27b-mtp-q4_K_M-56k"                  # as model= in telemetry rows
TAG_RCA = "--rca--qwen3.8_27b-mtp-q4_K_M-56k--"           # as in the filenames
TAG_EARLIER = "--script-review--qwen3.8_27b-mtp-q4_K_M-56k--"  # as in the filenames

_failures = []


def _report(label, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + label)
    for line in str(detail).splitlines():
        if line:
            print("        " + line)
    if not ok:
        _failures.append(label)


def _rows(path):
    """(rows, unparsable) read from a JSONL ledger. Nothing is sliced: a torn line is
    counted and reported, never hidden -- this ledger's whole job is surviving a cut."""
    rows, unparsable = [], 0
    if not path.is_file():
        return rows, 0
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                unparsable += 1
    return rows, unparsable


def test_recorder_writes_prompt_size_and_kind():
    """The one scenario the recorder exists for: one call, one row, carrying the input
    length and the work it belongs to.  Driven into a temp directory."""
    tmp = Path(tempfile.mkdtemp(prefix="run-telemetry-fixture-"))
    saved = run_telemetry.LEDGER
    run_telemetry.LEDGER = tmp / TELEMETRY_NAME
    try:
        run_telemetry.record(harness="auditor", model=MODEL_56K, seconds=12.5,
                             num_predict=8000, eval_count=612, prompt_eval_count=38091,
                             done_reason="stop", kind="rca")
        rows, unparsable = _rows(run_telemetry.LEDGER)
    finally:
        run_telemetry.LEDGER = saved
    wrote_one = len(rows) == 1 and unparsable == 0
    row = rows[0] if rows else {}
    length = row.get("prompt_eval_count")
    _report("a recorded row carries the prompt size (prompt_eval_count)",
            wrote_one and isinstance(length, int) and length == 38091,
            "rows written: %d, unparsable: %d, row: %s"
            % (len(rows), unparsable, json.dumps(row, sort_keys=True)))
    _report("a recorded row says WHICH work the call was (kind) -- the missing field",
            wrote_one and row.get("kind") == "rca",
            "the deferred check compares the Auditor's RCA jobs with its earlier work. "
            "A row with no kind cannot be put in either population, so the comparison "
            "cannot be computed from the ledger by any session. "
            "row written: %s" % json.dumps(row, sort_keys=True))


def test_the_deferred_comparison_is_computable_from_the_ledger():
    """'How long, RCA jobs vs earlier work' must be two means off one file, not a
    guess joined to filenames."""
    rows, _unparsable = _rows(WORKSPACE / TELEMETRY_NAME)
    mine = [r for r in rows if r.get("model") == MODEL_56K]
    with_len = [r for r in mine
                if isinstance(r.get("prompt_eval_count"), int) and r["prompt_eval_count"] > 0]
    _report("the 56k population has recorded prompt sizes to compare",
            len(with_len) > 0,
            "rows on model %s: %d; of those, carrying a nonzero prompt_eval_count: %d"
            % (MODEL_56K, len(mine), len(with_len)))

    groups = {}
    for r in with_len:
        groups.setdefault(r.get("kind"), []).append(int(r["prompt_eval_count"]))
    named = sorted(str(k) for k in groups if k)
    detail = ["rows with a prompt size: %d" % len(with_len),
              "populations named by the ledger: %d -> %s"
              % (len(named), ", ".join(named) if named
                 else "none: every row's kind is absent, so 'RCA jobs' and "
                      "'earlier work' are not separable from this file"),
              "mean prompt_eval_count per population: " + (
                  ", ".join("%s=%d" % (k, sum(v) // len(v))
                            for k, v in sorted(((str(k), v) for k, v in groups.items())))
                  if groups else "none")]
    _report("the ledger splits into at least 2 named populations, so the deferred "
            "comparison is a number and not a join to filenames",
            len(named) >= 2, "\n".join(detail))


def test_the_telemetry_ledger_has_no_unparsable_rows():
    rows, unparsable = _rows(WORKSPACE / TELEMETRY_NAME)
    _report("the telemetry ledger has no torn rows",
            len(rows) > 0 and unparsable == 0,
            "rows parsed: %d, unparsable: %d -- a torn line is the input a ledger "
            "exists to survive" % (len(rows), unparsable))


def test_the_two_dispatch_populations_exist():
    names = sorted(p.name for p in DISPATCH_DIR.iterdir()) if DISPATCH_DIR.is_dir() else []
    rca = [n for n in names if TAG_RCA in n]
    earlier = [n for n in names if TAG_EARLIER in n]
    _report("the two dispatch populations exist on disk (271 rca / 238 script-review "
            "measured 2026-10-18; asserted non-zero so the suite survives drift)",
            len(rca) > 0 and len(earlier) > 0,
            "%s: %d files; %s: %d files" % (TAG_RCA, len(rca), TAG_EARLIER, len(earlier)))


def test_no_dispatch_log_has_an_empty_stem():
    names = sorted(p.name for p in DISPATCH_DIR.iterdir()) if DISPATCH_DIR.is_dir() else []
    offenders = [n for n in names if n.startswith(".json--")]
    _report("no dispatch filename starts with '.json--' (703 such files at RCA time; "
            "0 measured today -- this guard stops a recurrence)",
            not offenders,
            "offenders: %d -- %s" % (len(offenders), "; ".join(offenders))
            if offenders else "zero offenders")


def main():
    for fn in (test_recorder_writes_prompt_size_and_kind,
               test_the_deferred_comparison_is_computable_from_the_ledger,
               test_the_telemetry_ledger_has_no_unparsable_rows,
               test_the_two_dispatch_populations_exist,
               test_no_dispatch_log_has_an_empty_stem):
        fn()
    print("\n%d checks failed" % len(_failures))
    for label in _failures:
        print("  FAIL: %s" % label)
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
