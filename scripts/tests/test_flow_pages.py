#!/usr/bin/env python3
"""The Flow tab's request forms (plan step 3): every form files a REQUEST, none edits FLOW.json.

Renders the real page read-only; the POST path runs against a temporary task store and FLOW.json.
"""
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "app-repo")]
import feedback  # noqa: E402
import flow_change  # noqa: E402
import flow_lint  # noqa: E402
import flow_pages  # noqa: E402
import taskstore  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n        got  {got!r}\n        want {want!r}"))


def main():
    flow_before = flow_lint.FLOW.read_bytes()
    d = json.loads(flow_before)
    html = feedback.flow_page()
    actions = set(re.findall(r'<form method="post" action="([^"]+)"', html))
    # THE ONE DIRECT EDIT is attaching a skill (plan 3c-i, 2026-09-30): it cannot move an item or change
    # where work goes, flow_lint must be clean before it is written, and Revert undoes it. Adding a
    # SKILL is still a request (/flow/skills/request), reviewed before anything goes live.
    report("every form on the Flow tab posts a request, a revert or a skill attachment -- never a flow edit",
           actions <= {"/flow/request", "/flow/revert", "/flow/skills/attach", "/flow/skills/request"}
           and "/flow/request" in actions, True)
    report("a refusal's ?err= goes BEFORE the #anchor, so the banner shows",
           (feedback.with_err("/flow#skill-x", "no"), feedback.with_err("/flow?a=1", "no")),
           ("/flow?err=no#skill-x", "/flow?a=1&err=no"))
    report("the skills library is on the page, one card per skill, each with Attach to",
           html.count("Attach to&hellip;"), len(__import__("skills").all_skills()))
    report("each task card has its own request form",
           html.count("Request a change to this task"), len(d["tasks"]))
    report("each arrow has its own request form", html.count("change this arrow"), len(d["links"]))
    report("+ Add a task and Describe a change are on the page",
           ("+ Add a task" in html, "Describe a change" in html), (True, True))
    report("the page says the rule: applied only with no warning and no knock-on effect",
           "no warning and no knock-on" in html, True)
    report("forms inside a card do not trigger the card's tap-to-filter",
           html.count('onclick="event.stopPropagation()"'), len(d["tasks"]) + len(d["links"]))
    report("rendering the page did not touch FLOW.json", flow_lint.FLOW.read_bytes(), flow_before)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        tasks = tmp / "tasks"
        tasks.mkdir()
        taskstore.TASKS, taskstore.DONE = tasks, tasks / "done"
        taskstore.QUEUE, taskstore.LOCK = tasks / "QUEUE.md", tasks / ".lock"
        flow = tmp / "FLOW.json"
        shutil.copy2(flow_lint.FLOW, flow)
        flow_change.FLOW = flow_lint.FLOW = flow
        flow_change.LOG = tmp / "FLOW-CHANGELOG.jsonl"
        form = {"kind": "edit_link", "target": "intake|filed|backlog", "text": "label it 'filed by taskstore.add'"}
        where, err = flow_pages.handle_post("/flow/request", lambda k: form.get(k, ""))
        tid = where.split("=")[-1]
        report("a posted form files a request and says which task", (err, where.startswith("/flow?requested=t")),
               (None, True))
        meta = taskstore.find(tid)[0] or {}
        report("  at the top of the Backlog, for the Manager (not held)",
               (taskstore.read_queue()[:1], meta.get("needs", "")), ([tid], "production"))
        report("  and FLOW.json is unchanged by it", flow.read_bytes(), flow_before)
        where, err = flow_pages.handle_post("/flow/request", lambda k: {"kind": "describe"}.get(k, ""))
        report("an empty description is refused with a reason", bool(err), True)
        where, err = flow_pages.handle_post("/flow/revert", lambda k: {"n": "7"}.get(k, ""))
        report("reverting a change that does not exist says so", err, "no changelog entry 7")
        report("the requests card lists the open request", tid in flow_pages.changes_card(), True)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
