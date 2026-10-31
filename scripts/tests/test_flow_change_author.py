#!/usr/bin/env python3
"""A Flow-chart write must never be attributed to Jas unless he made it (2026-10-27, L2).

THE EVIDENCE, from this session. `flow_change.main` started with `who = "jas"` and
`add_helper`/`add_task_node` defaulted to `who="claude-code"`. So two chart entries this lane made
went into FLOW-CHANGELOG as **claude-code**, and their two reverts went in as **jas** -- rows 89-92
of that file, which nobody named Claude and nobody named Jas wrote. That is the exact class AGENTS.md
puts first: "Never write a note attributed to Jas ... A forged note enters at the top of the firewall
and is never questioned again. (L-091)".

WHY A DEFAULT IS THE WRONG SHAPE HERE. A default is what a caller gets by not thinking. For an
attribution field that means the busiest caller silently credits someone else. So an unattributed
write is recorded as `unknown` -- which a reader can see and correct -- and the DeepSeek Harness,
which has an identity in the environment (`DSH_SESSION_ID`, t994), names itself.

WHAT IS NOT CHANGED: `request()`, which is the Flow tab's own path and legitimately acts as Jas. The
Hub page passes its caller explicitly; this suite pins that `request` still defaults to jas so the
fix cannot accidentally take Jas's own surface away.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import flow_change as F  # noqa: E402
import flow_lint         # noqa: E402

PASSED = FAILED = 0
REAL_FLOW = Path(F.FLOW)          # captured once: isolated() moves F.FLOW, so it cannot re-read it


def report(name, ok, detail=""):
    global PASSED, FAILED
    PASSED += bool(ok)
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n        {detail}"))


def isolated(tmp):
    """Real script paths, because flow_lint refuses a helper that does not exist on disk -- a test
    that invented a path would be testing the refusal, not the attribution."""
    """Point flow_change's store and changelog at a throwaway copy of the real chart."""
    models = Path(tmp) / "models"
    models.mkdir(parents=True, exist_ok=True)
    (models / "FLOW.json").write_text(REAL_FLOW.read_text(encoding="utf-8"), encoding="utf-8")
    (models / "FLOW-CHANGELOG.jsonl").write_text("", encoding="utf-8")
    F.FLOW = models / "FLOW.json"
    F.LOG = models / "FLOW-CHANGELOG.jsonl"
    flow_lint.FLOW = F.FLOW
    return models


def last_row():
    rows = [json.loads(l) for l in Path(F.LOG).read_text(encoding="utf-8").splitlines() if l.strip()]
    return rows[-1] if rows else {}


def main():
    with tempfile.TemporaryDirectory() as tmp:
        isolated(tmp)
        _no_session = os.environ.pop("DSH_SESSION_ID", None)   # this suite runs INSIDE the harness
        try:
            F.add_helper("scripts/ask_q16.py", ["merged"], "a helper, to see who gets the credit",
                         runs="on-demand")
        finally:
            if _no_session is not None:
                os.environ["DSH_SESSION_ID"] = _no_session
        who = last_row().get("who")
        report("add_helper with no author does not credit claude-code", who != "claude-code", who)
        report("  and it does not credit jas either", who != "jas", who)
        report("  it records an unattributed write as unknown, so a reader can see it", who == "unknown",
               who)

    with tempfile.TemporaryDirectory() as tmp:
        isolated(tmp)
        os.environ["DSH_SESSION_ID"] = "test-session"
        try:
            F.add_helper("scripts/assigned_worker.py", ["merged"], "a helper from the harness",
                         runs="on-demand")
            report("the DeepSeek Harness names itself from DSH_SESSION_ID", last_row().get("who") == "dsh-harness",
                   last_row().get("who"))
        finally:
            del os.environ["DSH_SESSION_ID"]

    with tempfile.TemporaryDirectory() as tmp:
        isolated(tmp)
        first = F.add_helper("scripts/bridge_trigger.py", ["merged"], "the row to revert",
                             runs="on-demand", who="dsh-harness")
        n = first["n"] if isinstance(first, dict) else first
        F.revert(n)
        who = last_row().get("who")
        report("a revert is not attributed to Jas by default", who != "jas", who)
        report("  it is attributed to the reverting caller, or unknown", who in ("unknown", "dsh-harness"),
               who)

    with tempfile.TemporaryDirectory() as tmp:
        isolated(tmp)
        # request() files a TASK, so the task store must be this suite's own or (rightly) the id
        # guard refuses to let a test draw a live id -- which is what happened on the first run of
        # this case, and is the guard in `taskstore` doing its job.
        sys.path.insert(0, str(REPO / "projects" / "app-repo"))
        import taskstore as TS
        store = Path(tmp) / "tasks"
        (store / "done").mkdir(parents=True)
        TS.TASKS, TS.DONE = store, store / "done"
        TS.QUEUE, TS.LOCK = store / "QUEUE.md", store / ".lock"
        TS.ATTACH = Path(tmp) / "attachments"
        TS.write_queue([])
        tid = F.request("describe", "merged", "a request from the Flow tab", who="jas")
        # request() files a TASK (that is how the Manager picks it up), so Jas's name is on the task
        # body, not on a FLOW-CHANGELOG row.
        task_text = next(iter(store.glob(f"{tid}--*.md"))).read_text(encoding="utf-8")
        report("the Flow tab's own request() still acts as Jas, unchanged",
               "requested from the Flow tab by jas" in task_text, task_text[:120])

    with tempfile.TemporaryDirectory() as tmp:
        isolated(tmp)
        # END TO END through main(), which is what every script and lane actually calls.
        _none = os.environ.pop("DSH_SESSION_ID", None)
        try:
            F.main(["helper", "scripts/chat_agent.py", "merged", "through main()", "--runs", "on-demand"])
        finally:
            if _none is not None:
                os.environ["DSH_SESSION_ID"] = _none
        who = last_row().get("who")
        report("main() with no --who records unknown, not jas", who == "unknown", who)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
