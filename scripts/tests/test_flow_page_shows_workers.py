#!/usr/bin/env python3
"""t1594's display half: the Flow tab shows WHO WORKS EACH STAGE.

Jas: "if whatever the source of truth is is updated, the flow tab also reflects that." The data
(role_store.flow_workers) and the enforcement (check_workspace "The chart shows who works each
stage") were merged on 2026-10-25 in 1739c17e5; the page did not show it. FLOW.json is his chart and
is not edited to carry this -- models/FLOW-WORKERS.json is generated from ROLES.json on every role
save, and this is the overlay.

Two readers, both tested here without touching live state:
  flow_layout.workers()  the projection, tolerant: missing/unreadable/wrong shape is {} and the
                         chart still draws (the STALE check is check_workspace's job, not a
                         drawing function's)
  feedback.flow_workers_card(d, wmap)  the page card: each stage with its roles, and a stage no role
                         holds said out loud rather than left blank
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "app-repo")]
import flow_layout  # noqa: E402
import feedback      # noqa: E402

FAILS = []


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


D = {"tasks": [{"id": "building", "name": "Building", "worker_kind": "role", "worker": "coder"},
               {"id": "preflight", "name": "Preflight", "worker_kind": "role", "worker": "?"}]}


def main():
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)

        good = t / "FLOW-WORKERS.json"
        good.write_text(json.dumps({"_generated": "x", "workers": {"building": ["coder", "dsh"]}}))
        check("the projection is read as {task: [role keys]}",
              flow_layout.workers(good) == {"building": ["coder", "dsh"]}, flow_layout.workers(good))

        check("a missing projection is {} and does not raise",
              flow_layout.workers(t / "nope.json") == {}, "raised or returned something else")

        bad = t / "bad.json"
        bad.write_text("{not json")
        check("an unreadable projection is {} -- the chart must still draw",
              flow_layout.workers(bad) == {}, "raised or returned something else")

        wrong = t / "wrong.json"
        wrong.write_text(json.dumps(["a", "list"]))
        check("the wrong shape is {} and not a crash", flow_layout.workers(wrong) == {})

        wmap = {"building": ["coder", "dsh"]}
        card = feedback.flow_workers_card(D, wmap)
        check("the card names the stage and the roles that hold it",
              "Building" in card and "coder, dsh" in card, card[:200])
        check("a stage no role holds is SAID, not left blank",
              "no role holds this stage" in card, card[:200])
        check("the card counts what it claims", "1 of 2 stage(s)" in card, card[:200])
        check("it names the generated file it reads, not the chart",
              "FLOW-WORKERS.json" in card and "ROLES.json" in card)
        check("an empty projection still renders (every stage unheld)",
              "0 of 2 stage(s)" in feedback.flow_workers_card(D, {}))

        evil = {"tasks": [{"id": "x", "name": "<script>alert(1)</script>"}]}
        check("a stage name is escaped, not interpolated",
              "<script>" not in feedback.flow_workers_card(evil, {}),
              feedback.flow_workers_card(evil, {})[:200])

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
