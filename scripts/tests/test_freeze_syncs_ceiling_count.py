#!/usr/bin/env python3
"""A freeze moves the promised ceiling count with the list, in the same act.

L1's finding, 2026-10-28: every bridge --freeze appended its red oracle to the ceiling list and left the
promised number behind, so every freeze in production redded check_workspace twice -- "the ceiling may
fall and must not rise" and "no recorded verdict". 0d5526d64 (t1493) was the first instance and fixing
it by hand fixed the instance, not the class.

It must NOT invent a verdict: the freeze never runs the oracle, so this pins that it only moves the
number and leaves the "no recorded verdict" question to the sweep that will actually run it.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def main():
    import bridge
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'BAD'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    def fixture(count, allowed):
        d = Path(tempfile.mkdtemp())
        (d / "workspace").mkdir(parents=True)
        (d / "workspace" / "test-failures-ceiling.json").write_text(json.dumps(
            {"allowed_to_fail": allowed, "_oracles_awaiting_their_fix": {}}))
        (d / "workspace" / "test-failures-ceiling-count.json").write_text(json.dumps(
            {"count": count, "at": "x", "why": "earlier"}))
        return d

    d = fixture(3, ["a.py", "b.py", "c.py", "frozen.py"])      # the freeze just added frozen.py
    got = bridge.sync_ceiling_count(d, "1 oracle(s) frozen for t9999")
    cnt = json.loads((d / "workspace" / "test-failures-ceiling-count.json").read_text())
    check("a freeze moves the promised count to the list", cnt["count"] == 4, str(cnt))
    check("  it returns the number it wrote", got == 4, str(got))
    check("  and it says why, in the count's own record", "freeze" in cnt["why"] and "t9999" in cnt["why"],
          cnt["why"])
    check("  the earlier why is kept, not replaced", cnt["why"].startswith("earlier"), cnt["why"])

    d = fixture(4, ["a.py", "b.py", "c.py", "frozen.py"])
    check("an already-true count is left alone (no churn in the record)",
          bridge.sync_ceiling_count(d, "x") is None)

    # THE HONEST LIMIT: no verdict row is written, because the freeze never ran the oracle.
    d = fixture(0, ["frozen.py"])
    bridge.sync_ceiling_count(d, "1 oracle(s) frozen for t9999")
    ledger = (d / "workspace" / "suite-state.jsonl")
    check("it does NOT invent a verdict for an oracle nobody ran", not ledger.exists(),
          "a suite-state row appeared: that would be an unverified claim")

    src = (ROOT / "scripts" / "bridge.py").read_text(encoding="utf-8")
    check("the freeze path calls it", "sync_ceiling_count(repo," in src)
    trig = (ROOT / "scripts" / "bridge_trigger.py").read_text(encoding="utf-8")
    check("  AND the additive merge path calls it -- L1 named both writers, and the count went to 390 "
          "against 352 during the first freeze batch",
          "sync_ceiling_count(repo," in trig)
    print("test_freeze_syncs_ceiling_count:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
