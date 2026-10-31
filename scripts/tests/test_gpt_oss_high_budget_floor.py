"""t1286: settings.py's output-budget floor under-protects gpt-oss:20b at think:high.

Measured: gpt-oss:20b at think=high consumed 41,075 tokens before it would answer at all
(STATE.md:543; workspace/HANDOVER-2026-09-04.md:20). workspace/DUO-REPAIR-PLAN.md:67 records
the result as defect D2: "gpt-oss:20b at think=high returns an EMPTY reply on the 26-item
paper. It cannot currently be sat, so the SENIOR decision has no data."

THINK_FLOOR caps "high" at 16000 and budget_findings() is silent at 16000 -- the profile
passes, the model starves. That is the missing change this suite pins: a floor per model
where a model has been measured above the global one.

RED ON MAIN: test_gpt_oss_at_the_high_floor_is_flagged (no model floor exists). The other
three pass on main and pin the additive shape.
Import pattern is the existing one, scripts/tests/test_settings_save_writes_ledger.py:41-46.
No live state: settings are passed in explicitly, so settings.load() is never called.
No subprocess, no urlopen, no model call, no fixed /tmp path, nothing left running.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import settings  # noqa: E402

GPT = "gpt-oss:20b"
QWEN = "qwen3.8:27b-mtp-bf16"
FAILED = []


def check(name, ok, detail=""):
    if not ok:
        FAILED.append(f"{name} -- {detail}")


def test_gpt_oss_at_the_high_floor_is_flagged():
    # 16000 is today's "high" floor. For this model it is the starve threshold, not the
    # finish threshold. A profile sitting ON the old floor must be reported, and the report
    # must say the model, the level, the budget under test, and the number it is sourced from.
    f = settings.budget_findings({"chat": {"model": GPT, "think": "high",
                                           "num_predict": 16000}})
    check("reported", bool(f), f"budget_findings() is silent at 16000 for {GPT}: {f}")
    joined = " | ".join(f)
    check("names the model", "gpt-oss" in joined, f"finding does not name {GPT}: {f}")
    check("names the level", "high" in joined, f"finding does not name the reasoning level: {f}")
    check("names the budget", "16000" in joined,
          f"finding does not name the budget under test: {f}")
    check("names the measured floor", "41075" in joined,
          f"finding does not name 41075, the measured requirement: {f}")


def test_gpt_oss_above_the_measured_floor_is_clean():
    # 48000 clears 41075. Flagging it would fire the check on the only setting that works --
    # the over-correction that gets a guard routed around within a week.
    f = settings.budget_findings({"chat": {"model": GPT, "think": "high",
                                           "num_predict": 48000}})
    check("clean above the floor", f == [], f"48000 should clear 41075, got: {f}")


def test_global_high_floor_still_fires_for_every_other_model():
    # Additive, not a replacement: 4000 under the global "high" floor (16000) is reported
    # exactly as it is today, for a model with no per-model entry.
    f = settings.budget_findings({"chat": {"model": QWEN, "think": "high",
                                           "num_predict": 4000}})
    check("qwen 4000 still reported", bool(f), f"global floor stopped reporting {QWEN}: {f}")
    check("qwen keeps the global floor", "16000" in " | ".join(f),
          f"the finding lost the 16000 it is under: {f}")


def test_gpt_oss_low_is_unchanged():
    # Scope is the level the model was measured at. A gpt-oss profile at "low" on 4000 is
    # AT the global low floor (not under it) and must not be reported: a per-model entry
    # applies to the levels it lists, not to every level.
    f = settings.budget_findings({"chat": {"model": GPT, "think": "low",
                                           "num_predict": 4000}})
    check("gpt-oss at low unchanged", f == [], f"low/4000 must stay clean, got: {f}")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        try:
            t()
        except AssertionError as e:
            FAILED.append(f"{t.__name__} -- raised AssertionError: {e}")
    for line in FAILED:
        print(f"FAIL {line}")
    print(f"{len(tests) - len(FAILED)}/{len(tests)} passed")
    sys.exit(1 if FAILED else 0)
