#!/usr/bin/env python3
"""t682 fail-first oracle: Chat can create, cancel and close a task over the MCP bridge.

Red on main for the change it asks for and for nothing else: scripts/workspace_mcp/core.py
defines _ts (829), _task_id (841), my_tasks (850), task_get (861), task_note (873) and
task_assign (892) -- there is no task_create, task_cancel or task_done on Workspace, so the
first call below raises AttributeError.

EVERYTHING runs under a tempfile root. Workspace._ts() (core.py:829-839) re-points the
imported taskstore module at <root>/workspace/tasks, and taskstore._resolved_path()
(projects/app-repo/taskstore.py:253-264) derives RESOLVED.md from TASKS at CALL time, so
QUEUE.md, done/ and RESOLVED.md all live inside the temp root. Nothing here touches the
repository's workspace/tasks/, starts a server, runs a subprocess or calls a model.

Ids are three digits (t101, t102, t103) because taskstore._next_id() returns f"t{n+1:03d}"
(taskstore.py:352-358). A fixture using t1/t2/t3 would assert a zero-padding the code never
produces; the real ids on disk are shaped like t682.
"""
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workspace_mcp.core import Denied, HTTP_TOOLS, STDIO_TOOLS, Workspace  # noqa: E402

PASSED = 0
FAILED = 0
RULES = set()


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got!r}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


def refusal(fn, *args, **kw):
    """(rule, message) of the Denied a call raises; ('ALLOWED', '') if it raises nothing."""
    try:
        fn(*args, **kw)
        return "ALLOWED", ""
    except Denied as e:
        RULES.add(e.rule)
        return e.rule, str(e)


def fixture():
    root = Path(tempfile.mkdtemp(prefix="t682-mcp-writes-"))
    (root / "workspace" / "tasks").mkdir(parents=True)
    return Workspace(root, caller="claude-chat")


def add_task(root, tid, title, assigned, status, source):
    """A task file in the shape taskstore.parse() reads (taskstore.py:117-144)."""
    slug = title.lower().replace(" ", "-")
    (root / "workspace" / "tasks" / f"{tid}--{slug}.md").write_text(
        f"---\nid: {tid}\ntitle: {title}\narea: hub\nkind: build\nstatus: {status}\n"
        f"assigned: {assigned}\nsource: {source}\ncreated: 2026-10-11T000000Z\n---\n\n"
        "The body a person would read.\n\n## Notes\n\n## Attachments\n",
        encoding="utf-8")


def notes_of(path):
    text = path.read_text(encoding="utf-8")
    return text.partition("## Notes")[2].partition("## Attachments")[0]


def queue_pos(text, tid):
    m = re.search(rf"^\s*(\d+)\. {tid}\b", text, re.M)
    return int(m.group(1)) if m else 999


def ids_in(rows):
    return [r["id"] for r in rows]


def main():
    print("\nworkspace_mcp: task_create / task_cancel / task_done (t682)\n")
    ws = fixture()
    tasks = ws.root / "workspace" / "tasks"
    for spec in (("t101", "Fix the bridge", "production", "queued", ""),
                 ("t102", "Widen the gate", "production", "queued", ""),
                 ("t103", "Deploy the hub", "jas", "queued", "jas - phone"),
                 ("t106", "Retired idea", "production", "done", ""),
                 ("t107", "Widen the hub view", "claude-code", "doing", "")):
        add_task(ws.root, *spec)
    ts = ws._ts()
    check("taskstore is pointed at the fixture, not the repository", ts.TASKS, str(tasks))
    ts.write_queue(["t101", "t102", "t103", "t106", "t107"])

    # ---- 1. task_create ---------------------------------------------------------
    created = ws.task_create("Back up the router",
                             body="The router config is not backed up anywhere.",
                             area="new-idea")
    check("task_create returns the new id, one above the highest on disk",
          created.get("id"), "t108")
    new = tasks / "t108--back-up-the-router.md"
    check("  the id it returns names a file that exists", new.is_file(), True)
    created_text = new.read_text(encoding="utf-8")
    check("  the file carries the body Chat dictated",
          "The router config is not backed up anywhere." in created_text, True)
    check("  it is assigned to production",
          re.search(r"^assigned: production$", created_text, re.M) is not None, True)
    check("  its source is Chat relaying Jas, not Jas himself",
          "source: claude-chat (relaying Jas)" in created_text, True)
    check("  and taskstore.by_jas() does not read that source as Jas's",
          bool(ts.by_jas("claude-chat (relaying Jas)")), False)
    queue = (tasks / "QUEUE.md").read_text(encoding="utf-8")
    check("  QUEUE.md has a numbered line naming it with its title",
          bool(re.search(r"^\d+\. t108 — Back up the router", queue, re.M)), True)
    check("  a machine task lands above the one Jas filed by hand (t264 rule in add)",
          (queue_pos(queue, "t108"), queue_pos(queue, "t103")), (3, 4))

    check("an empty title is refused", refusal(ws.task_create, "   ")[0], "empty-title")
    long_title = ("Back up the router before the next firmware update lands on the "
                  "gateway and reboots the mesh")
    rule, message = refusal(ws.task_create, long_title)
    check(f"a {len(long_title)}-character title is refused", rule, "title-too-long")
    check("  and the refusal states the 80-character limit", "80" in message, True)
    t80 = "Back up the router config " * 3 + "xy"
    check("a title of exactly 80 characters is accepted",
          (len(t80), ws.task_create(t80).get("id")), (80, "t109"))
    check("  and its file is on disk", len(list(tasks.glob("t109--*.md"))), 1)

    # ---- 2. task_cancel ---------------------------------------------------------
    check("cancel with no reason is refused",
          refusal(ws.task_cancel, "t101", "  ")[0], "empty-reason")
    check("cancel of an id that is not on disk is refused",
          refusal(ws.task_cancel, "t999", "superseded by t108")[0], "no-such-task")
    check("cancel of a task already resolved is refused",
          refusal(ws.task_cancel, "t106", "superseded by t108")[0], "already-resolved")
    check("a refused cancel writes no note on the task",
          "· claude-chat**" in notes_of(tasks / "t101--fix-the-bridge.md"), False)
    check("task_cancel returns the id it cancelled",
          ws.task_cancel("t101", "superseded by t108").get("id"), "t101")
    check("  its file left the queue directory",
          (tasks / "t101--fix-the-bridge.md").exists(), False)
    moved = tasks / "done" / "t101--fix-the-bridge.md"
    check("  and is in done/, which is what takes it off the queue", moved.is_file(), True)
    cancelled_text = moved.read_text(encoding="utf-8")
    check("  its front matter says cancelled",
          re.search(r"^status: cancelled$", cancelled_text, re.M) is not None, True)
    note = notes_of(moved)
    check("  a note carries the reason in full",
          "CANCELLED: superseded by t108" in note, True)
    check("  the reason is marked as Chat relaying Jas, not Jas speaking",
          "(via Chat, relaying Jas) CANCELLED: superseded by t108" in note, True)
    check("  the note is signed Claude - Chat", "· claude-chat**" in note, True)
    check("  and never written in Jas's name", "· jas**" in note, False)
    resolved = (tasks / "RESOLVED.md").read_text(encoding="utf-8")
    check("  RESOLVED.md lists it under Cancelled with its title",
          "- **t101** — Fix the bridge" in resolved, True)
    check("  RESOLVED.md carries the full reason, not a cut prefix",
          "why: superseded by t108" in resolved, True)
    queue2 = (tasks / "QUEUE.md").read_text(encoding="utf-8")
    check("  t101 is off the queue", re.search(r"^\s*\d+\. t101\b", queue2, re.M) is None, True)
    check("  and off the list a person reads for production",
          ids_in(ws.my_tasks("production")["tasks"]).count("t101"), 0)
    check("  cancelling it a second time is refused",
          refusal(ws.task_cancel, "t101", "again")[0], "already-resolved")

    # ---- 3. task_done -----------------------------------------------------------
    check("done with no evidence is refused",
          refusal(ws.task_done, "t102", "   ")[0], "empty-evidence")
    check("done on a task whose status is doing is refused",
          refusal(ws.task_done, "t107", "merged p412")[0], "task-in-progress")
    check("done of an id that is not on disk is refused",
          refusal(ws.task_done, "t999", "merged p412")[0], "no-such-task")
    t107 = tasks / "t107--widen-the-hub-view.md"
    check("a refused done writes no note and leaves the task doing",
          ("· claude-chat**" in notes_of(t107),
           re.search(r"^status: doing$", t107.read_text(encoding="utf-8"), re.M) is not None),
          (False, True))
    check("task_done returns the id",
          ws.task_done("t102", "merged p412; 41 tests passed").get("id"), "t102")
    dfile = tasks / "done" / "t102--widen-the-gate.md"
    check("  its file moved to done/", dfile.is_file(), True)
    done_text = dfile.read_text(encoding="utf-8")
    check("  its front matter says done",
          re.search(r"^status: done$", done_text, re.M) is not None, True)
    dnote = notes_of(dfile)
    check("  a note carries the evidence verbatim",
          "merged p412; 41 tests passed" in dnote, True)
    check("  signed Claude - Chat and never in Jas's name",
          ("· claude-chat**" in dnote, "· jas**" in dnote), (True, False))
    resolved2 = (tasks / "RESOLVED.md").read_text(encoding="utf-8")
    check("  RESOLVED.md lists it under Done", "- **t102** — Widen the gate" in resolved2, True)
    check("  and its evidence is NOT filed as a cancellation reason",
          "merged p412" in resolved2, False)
    check("  t102 is off the production list",
          ids_in(ws.my_tasks("production")["tasks"]).count("t102"), 0)

    # ---- 4. transport scope (Chat over HTTP, not the local models over stdio) ----
    http_only = ("task_create", "task_cancel", "task_done")
    for name in http_only:
        check(f"{name} is served over HTTP (Chat)", name in HTTP_TOOLS, True)
        check(f"  and not in STDIO_TOOLS, so a local model cannot close work",
              name in STDIO_TOOLS, False)
    check("nothing else on the http list sits outside stdio",
          sorted(set(HTTP_TOOLS) - set(STDIO_TOOLS)), sorted(http_only))

    # ---- 5. every call is logged, success and refusal ---------------------------
    rows = [json.loads(line) for line in
            ws.log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    task_rows = [r for r in rows if str(r.get("tool", "")).startswith("task_")]
    check("every refusal is logged with the rule it refused",
          sorted((r["tool"], r.get("detail")) for r in task_rows
                 if r.get("outcome") == "refused"),
          sorted([("task_cancel", "empty-reason"), ("task_cancel", "no-such-task"),
                  ("task_cancel", "already-resolved"), ("task_cancel", "already-resolved"),
                  ("task_create", "empty-title"), ("task_create", "title-too-long"),
                  ("task_done", "empty-evidence"), ("task_done", "no-such-task"),
                  ("task_done", "task-in-progress")]))
    check("no task refusal is logged with an empty detail",
          [r["tool"] for r in task_rows
           if r.get("outcome") == "refused" and not r.get("detail")], [])
    check("each of the three tools is logged on success too",
          sorted({r["tool"] for r in task_rows if r.get("outcome") == "ok"}),
          ["task_cancel", "task_create", "task_done"])
    check("every task row records who called it",
          sorted({r.get("caller") for r in task_rows}), ["claude-chat"])

    check("the oracle exercised every refusal code the task names", sorted(RULES),
          ["already-resolved", "empty-evidence", "empty-title", "empty-reason",
           "no-such-task", "task-in-progress", "title-too-long"])

    print(f"\n{PASSED} passed, {FAILED} failed")
    shutil.rmtree(ws.root, ignore_errors=True)
    return FAILED


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
