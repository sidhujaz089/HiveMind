#!/usr/bin/env python3
"""A FLOW.json that this function did not write is refused (C1, t8650).

Jas's instruction was that the chart must be fully derived, and the honest completion of that is not "the
chart is generated" -- it is **"nothing else can change it"**. On 2026-10-26 `grep -rl FLOW.json
scripts/hooks/` returned 0 files, so the rule was a sentence in `flow_change.py`'s docstring.

Worse, the record already showed the rule being broken: `FLOW-CHANGELOG.jsonl` has 82 consecutive rows
and **six do not chain** -- row N's `after` is not row N+1's `before` -- so the chart was changed outside
the function at least six times, and the changelog cannot be replayed to the present state.

This guard is the network map's, applied to the chart: the file on disk must equal the newest row's
`after`. It says nothing about whether the change was good; it says only that the file can be traced to a
row, which is the property a replay needs.

What it refuses:

  * a FLOW.json that differs from the newest changelog row (edited by hand, or by a merge that applied a
    branch's chart without its row);
  * a FLOW.json with no changelog at all (it was never written through the function);
  * an unreadable FLOW.json reading as success.

Hermetic: FLOW and LOG are redirected at the module's own constants, so nothing touches the real chart.
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


def redirect(tmp):
    d = Path(tmp)
    flow_p = d / "FLOW.json"
    log_p = d / "FLOW-CHANGELOG.jsonl"
    shutil.copy(LIVE_FLOW, flow_p)
    shutil.copy(LIVE_LOG, log_p)
    flow_change.FLOW = flow_p
    flow_change.LOG = log_p
    return flow_p, log_p


def main():
    # 0. THE COMMITTED CHART PASSES: it matches the newest row, so the guard is not a standing nuisance.
    flow_change.FLOW, flow_change.LOG = LIVE_FLOW, LIVE_LOG
    check("the committed FLOW.json matches its newest changelog row", flow_change.guard() == [],
          flow_change.guard())

    with tempfile.TemporaryDirectory() as tmp:
        flow_p, log_p = redirect(tmp)
        check("a redirected copy passes too", flow_change.guard() == [], flow_change.guard())

        # 1. THE FINDING: a change the function did not write.
        doc = json.loads(flow_p.read_text(encoding="utf-8"))
        doc["_hand_edited"] = "not through flow_change.py"
        flow_p.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        probs = flow_change.guard()
        check("a hand-edited FLOW.json is refused", len(probs) == 1, probs)
        check("  the refusal names the newest row it disagrees with",
              bool(probs) and "n=" in probs[0], probs[:1])
        check("  and it says how to repair it",
              bool(probs) and any(w in probs[0] for w in ("flow_change", "revert")), probs[:1])

        # 2. NO CHANGELOG AT ALL is also a refusal: the file cannot be traced to anything.
        log_p.write_text("", encoding="utf-8")
        probs = flow_change.guard()
        check("a chart with no changelog is refused",
              len(probs) == 1 and "changelog" in probs[0].lower(), probs)

        # 3. AN UNREADABLE CHART IS A PROBLEM, not a pass.
        flow_p.write_text("{not json", encoding="utf-8")
        check("an unreadable FLOW.json is refused", len(flow_change.guard()) == 1, flow_change.guard())

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
