#!/usr/bin/env python3
"""t1218 red fixture (checklist items 4 and 8): a reasoning-effort figure recorded in
workspace/measurements.jsonl must say where its unit came from and which probe stamped it.

Run:  python3 scripts/tests/test_probe_think_lever_ledger.py

RED ON MAIN for one reason: scripts/check_measurements.py validates that a figure cited
in prose HAS a ledger row, and stops there. It never reads the row's own unit. So the
ledger today records the lever's central quantity in *characters* -- row 55 of
workspace/measurements.jsonl: "gpt-oss:20b reasoning length at think='high' on the coding
paper, against 17,250 at think=False and 881 at think='low'", unit "characters", no probe
stamp, status MEASURED -- and the checker calls it clean. A character count and a token
count differ by roughly 4x; closing that is task item 4, and an unstamped ad-hoc curl is
indistinguishable from a probe row, which is task item 8.

So: a NEW reasoning-effort row (dated on/after 2026-10-15, the day t1218 was filed) that
omits unit=="tokens", a non-empty token_source, or the probe stamp is a FINDING. Rows
dated before that date are exempt. Without that floor the rule fires on the ~23 legacy
rows that mention "think" and carry no stamp -- and a gate that fires 23 times on merge is
disabled the same day, which is the failure this repository's own skill names.

No model call, no live server, no fixed /tmp path, no subprocess, no writes: the function
under test takes ledger TEXT, so the fixture hands it strings. That seam is part of the
contract -- it is what keeps the checker unit-testable and the fixture clean.
"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "scripts" / "check_measurements.py"

EPOCH = "2026-10-15"      # the day t1218 was filed; rows dated before it are legacy
FUTURE = "2026-11-01"
LEGACY = "2026-09-03"

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)
        print(f"  [FAIL] {msg}")
    return bool(cond)


def load():
    spec = importlib.util.spec_from_file_location("check_measurements", MODULE)
    if spec is None or getattr(spec, "loader", None) is None:
        raise FileNotFoundError(f"{MODULE} does not exist")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def row(**over):
    """One ledger row in the shape the live ledger uses. Defaults to the ad-hoc shape
    the task exists to make visible: a char-length reasoning figure, stamped by nobody."""
    base = {"figure": "4200", "value": 4200, "unit": "characters",
            "what": "gpt-oss:20b reasoning length at think='high' on the coding paper",
            "date": EPOCH, "by": "ad-hoc curl", "status": "MEASURED"}
    base.update(over)
    return json.dumps(base)


def test_new_reasoning_row_without_a_probe_stamp_is_a_finding():
    mod = load()
    hits = mod.effort_rows_unstamped(row())
    check(len(hits) == 1,
          f"an unstamped reasoning-effort row dated {EPOCH} must be reported "
          f"(task item 8: an ad-hoc row must be visible), got {hits!r}")


def test_a_complete_probe_row_is_not_reported():
    mod = load()
    good = row(unit="tokens", token_source="eval_count",
               probe="probe_think_lever", date=FUTURE)
    check(mod.effort_rows_unstamped(good) == [],
          "a row that carries unit=tokens, a token_source and the probe stamp is the "
          "approved shape; reporting it would fire the gate on the probe's own output")


def test_tokens_unit_without_a_source_is_a_finding():
    mod = load()
    hits = mod.effort_rows_unstamped(row(unit="tokens", probe="probe_think_lever",
                                         date=FUTURE))
    check(len(hits) == 1,
          "unit='tokens' with no token_source does not say WHERE the unit came from; "
          f"the task pins eval_count / completion_tokens, never len()/4. Got {hits!r}")


def test_a_pre_probe_reasoning_row_is_exempt_by_date():
    """Row 55's exact shape, dated before the probe existed: recorded, not clean, and
    NOT this rule's job. A rule that fires here is a rule that gets --no-verify'd."""
    mod = load()
    check(mod.effort_rows_unstamped(row(date=LEGACY)) == [],
          "the rule must not fire on rows dated before the probe; that is 23 legacy "
          "rows on day one")


def test_a_prose_tok_s_row_is_untouched():
    """The checker's existing beat, verbatim in shape from the ledger's calibration rows:
    unit tok/s, no probe stamp, prose saying 'reasoning ON'. Not a lever measurement."""
    mod = load()
    legacy = json.dumps({"figure": "98.8", "value": 98.8, "unit": "tok/s",
                         "what": "gemma4:e2b decode rate under its calibrated config, "
                                 "reasoning ON", "date": LEGACY,
                         "by": "claude-code/opus-5", "status": "MEASURED"})
    check(mod.effort_rows_unstamped(legacy) == [],
          "decode-rate rows are not reasoning-effort rows; flagging them is scope creep "
          "that kills the gate")


def test_it_scans_every_row_of_a_real_ledger():
    """Three rows, one bad: the report names exactly the offender. This is the assertion
    that stops a stub which looks at the first line only, and one that reports the good
    rows too."""
    mod = load()
    text = "\n".join([
        row(date=LEGACY),
        row(),
        row(unit="tokens", token_source="eval_count", probe="probe_think_lever",
            date=FUTURE),
    ])
    hits = mod.effort_rows_unstamped(text)
    check(len(hits) == 1,
          f"exactly the one post-probe unstamped row should be reported, got {hits!r}")
    if hits:
        got = json.dumps(hits[0], sort_keys=True)
        check("4200" in got,
              f"the finding must name the figure it rejects so a reader can act on it; "
              f"got {got}")


def test_malformed_line_is_recorded_not_swallowed():
    """library_checks' own rule, asserted: a broken row must surface. Silently returning
    [] on unparseable JSON is how the ledger becomes unreadable without anyone noticing."""
    mod = load()
    hits = mod.effort_rows_unstamped(row() + "\n" + "{not json")
    check(len(hits) == 1,
          f"the reasoning row before the torn line is still a finding, got {hits!r}")


TESTS = [
    test_new_reasoning_row_without_a_probe_stamp_is_a_finding,
    test_a_complete_probe_row_is_not_reported,
    test_tokens_unit_without_a_source_is_a_finding,
    test_a_pre_probe_reasoning_row_is_exempt_by_date,
    test_a_prose_tok_s_row_is_untouched,
    test_it_scans_every_row_of_a_real_ledger,
    test_malformed_line_is_recorded_not_swallowed,
]

if __name__ == "__main__":
    print(f"\n{MODULE.name}: {len(TESTS)} tests\n")
    for fn in TESTS:
        try:
            fn()
        except Exception as e:                      # noqa: BLE001
            check(False, f"{fn.__name__} raised {type(e).__name__}: {e}")
    print(f"\n{len(FAILS)} failure(s) across {len(TESTS)} test(s)\n")
    sys.exit(1 if FAILS else 0)
