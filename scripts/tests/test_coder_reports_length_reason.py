"""t1387: an attempt that ran out of output budget must not be recorded as a model failure.

The dispatch this was filed from (p656-t1224-role-store, 2026-10-17T21:07:05Z) logged:

    attempt 3 ... unusable (no JSON object in the reply, 10360 tok, 261s)
    attempt 4 ... unusable (no JSON object in the reply, 9504 tok, 251s)
    stopping: two identical UNPARSEABLE failures

10360 and 9504 tokens are full-budget emissions: a reply that long with no parseable JSON
object in it is the model running out of room mid-object, not a model that misunderstood the
format. ask() records how every call ended (ask.last["done_reason"], coder.py:1076 native,
coder.py:1106 gateway) and the loop keeps that field on the rows that know it ("WINDOW-FULL"
at coder.py:2806, "RESUMED" at coder.py:2850). The UNPARSEABLE row appended at coder.py:2947
is the one row that drops it, so a 10360-token attempt is recorded with the cause "no JSON
object in the reply" -- the same string a 400-token formatting slip produces.

So: one module-level helper that builds that row from (n, tok, secs, err, done_reason, raw).
Its detail starts with the model's own parse error, so the printed line keeps its prefix and
the repeat-signature grouping keeps grouping like with like, and it appends the budget fact
when the call did not end "stop". The row carries "done_reason" under the key record()
already copies into the ledger (coder.py:2318) and "cut_off" so the caller can change what it
tells the model.

Red on main because coder.unusable_attempt does not exist.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests -> repo root
MOD = ROOT / "projects" / "local-inference" / "coder.py"
sys.path.insert(0, str(MOD.parent))                 # coder.py's own siblings
_spec = importlib.util.spec_from_file_location("coder_under_test", MOD)
coder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(coder)

LONG_ERR = "unterminated JSON object" + " detail" * 900


def test_the_row_builder_exists():
    assert hasattr(coder, "unusable_attempt"), (
        "coder.py has no row builder: the UNPARSEABLE row is built inline and records no "
        "done_reason, so a length-truncated reply reads as a model failure")


def test_the_row_is_the_shape_the_ledger_writes():
    row = coder.unusable_attempt(3, 10360, 261, "no JSON object in the reply", "length",
                                 "RAW REPLY WHOLE")
    assert row["outcome"] == "UNPARSEABLE", row
    assert row["n"] == 3, row
    assert row["tokens"] == 10360, row
    assert row["seconds"] == 261, row
    assert row["raw"] == "RAW REPLY WHOLE", row
    assert row.get("done_reason") == "length", row
    assert "no JSON object in the reply" in row["detail"], row
    assert "10360" in row["detail"], row
    assert "261" in row["detail"], row


def test_a_reply_that_hit_its_output_budget_says_so():
    row = coder.unusable_attempt(3, 10360, 261, "no JSON object in the reply", "length", "")
    low = row["detail"].lower()
    assert row["cut_off"] is True, row
    assert row["detail"].startswith("no JSON object in the reply"), row
    assert "done_reason=length" in row["detail"], row
    assert "cut off" in low, row
    assert "budget" in low, row


def test_a_clean_stop_is_not_reported_as_cut_off():
    for reason in ("stop", None, ""):
        row = coder.unusable_attempt(1, 400, 12, "unterminated JSON object", reason, "")
        low = row["detail"].lower()
        assert row["cut_off"] is False, (reason, row)
        assert "cut off" not in low, (reason, row)
        assert "done_reason" not in low, (reason, row)
        assert "unterminated JSON object" in row["detail"], row
        assert "400" in row["detail"], row
        assert "12" in row["detail"], row


def test_any_reason_that_is_not_a_clean_stop_is_named():
    for reason in ("length", "lease_held", "tool_calls", "insufficient_system_memory"):
        row = coder.unusable_attempt(1, 100, 2, "no JSON object in the reply", reason, "")
        assert row["cut_off"] is True, (reason, row)
        assert reason in row["detail"], (reason, row)
        assert row["detail"].startswith("no JSON object in the reply"), (reason, row)


def test_the_models_own_error_is_never_shortened():
    row = coder.unusable_attempt(1, 100, 1, LONG_ERR, "length", "")
    assert LONG_ERR in row["detail"], (
        f"the detail dropped part of the reply's own error ({len(LONG_ERR)} chars)")
    assert "TRUNCATED after" not in row["detail"], row


def main():
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    if not tests:
        print("no tests found")
        return 1
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:                                  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
