#!/usr/bin/env python3
"""A set of chart operations applies atomically, or not at all (P1c, t8718).

Declaring a new step on the chart is never one operation. `flow_lint` refuses a task node that nothing
sends work to, so the node and the link INTO it have to land together -- applying them one at a time would
mean committing an invalid chart in between. And measured on 2026-10-26, adding a node ALSO requires a
role to hold it: *"adds the model task X: no role has it on its task list, so work sent to it would wait
there"*. So a real new step is a three-surface change (chart node + its links + a role's task list), which
is why one atomically-applied ops list is the right primitive rather than a convenience.

What this refuses:

  * an EMPTY or non-list ops set -- "applied nothing" must not look like success;
  * a set that leaves the flow worse than it was, with the lint's own words;
  * and a refusal that writes ANYTHING: the chart and the changelog are byte-identical afterwards.

And what it proves: several operations land as ONE changelog row, so a revert puts all of them back
together. A row per operation would make a half-reverted chart possible, which is the failure the network
map's guard was built to prevent.

Hermetic: FLOW and LOG are redirected at the module's constants.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import flow_change  # noqa: E402

LIVE_FLOW = ROOT / "projects" / "local-inference" / "models" / "FLOW.json"
LIVE_LOG = ROOT / "projects" / "local-inference" / "models" / "FLOW-CHANGELOG.jsonl"
FAILS = []


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
        return ""
    except Exception as e:                       # noqa: BLE001 -- the message is the assertion
        return str(e)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        flow_p, log_p = d / "FLOW.json", d / "FLOW-CHANGELOG.jsonl"
        shutil.copy(LIVE_FLOW, flow_p)
        shutil.copy(LIVE_LOG, log_p)
        flow_change.FLOW, flow_change.LOG = flow_p, log_p
        flow = json.loads(flow_p.read_text(encoding="utf-8"))
        a, b = flow["tasks"][0]["id"], flow["tasks"][1]["id"]
        before_flow = flow_p.read_text(encoding="utf-8")
        before_rows = len(log_p.read_text(encoding="utf-8").splitlines())

        # 1. NOTHING IS NOT SUCCESS.
        check("an empty ops list is refused", bool(refused(flow_change.apply_ops, [])), "")
        check("a non-list is refused", bool(refused(flow_change.apply_ops, {"op": "x"})), "")

        # 2. A LEGAL SET LANDS AS ONE ROW, covering every operation.
        ops = [{"op": "edit_task", "id": a, "set": {"note": "probe note for the apply suite"}},
               {"op": "edit_task", "id": b, "set": {"note": "probe note for the apply suite"}}]
        n = flow_change.apply_ops(ops, what="two edits, one change")
        rows = [json.loads(x) for x in log_p.read_text(encoding="utf-8").splitlines() if x.strip()]
        check("two operations write ONE changelog row", len(rows) == before_rows + 1, (before_rows, len(rows)))
        check("  the row holds both operations", len(rows[-1].get("ops") or []) == 2, rows[-1].get("ops"))
        check("  and the row's after is the chart on disk",
              rows[-1]["after"] == json.loads(flow_p.read_text(encoding="utf-8")), "")

        # 3. A REFUSED SET WRITES NOTHING -- chart and changelog both untouched.
        mid_flow = flow_p.read_text(encoding="utf-8")
        mid_rows = len(log_p.read_text(encoding="utf-8").splitlines())
        msg = refused(flow_change.apply_ops, [{"op": "add_task", "task": {"id": "orphan_step",
                                                                         "name": "Orphan"}}])
        check("a node with nothing feeding it is refused", bool(msg), msg[:80])
        check("  the refusal is the lint's, about the missing link",
              "sends work to it" in msg or "link" in msg.lower(), msg[:200])
        check("  and the chart is byte-identical", flow_p.read_text(encoding="utf-8") == mid_flow)
        check("  and no row was written", len(log_p.read_text(encoding="utf-8").splitlines()) == mid_rows)

        # 4. THE GUARD STILL PASSES over the applied change: it went through the one write path.
        check("the chart still matches its newest row after an apply", flow_change.guard() == [],
              flow_change.guard())

    # 5. THE REAL CHART IS UNTOUCHED by this suite.
    flow_change.FLOW, flow_change.LOG = LIVE_FLOW, LIVE_LOG
    check("the live chart still matches its newest row", flow_change.guard() == [], flow_change.guard())

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
