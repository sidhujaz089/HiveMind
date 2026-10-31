#!/usr/bin/env python3
"""Three backlog items, scoped by Claude, for the local model.

WHY THIS IS SEPARATE FROM probe_batch.py
----------------------------------------
`probe_batch.py` routes findings the PROBE already scoped -- each arrives with a named file,
a reproduction and evidence. Nothing in the general backlog arrives that way, and
`MECHANICAL-FIXES.md` records what happens when it is dispatched anyway: three of four
blocked runs on 2026-08-26 were my scoping, not the model. So the briefs here are written by
hand, and each one names the file, the target suite, and what "done" looks like.

WHAT SCOPING ACTUALLY TURNED UP, and it is most of the value
------------------------------------------------------------
Jas asked for three backlog tasks. Working out whether they COULD be delegated rejected two
of the first three candidates and closed a fourth outright:

* **t174 is already done.** "Get rid of ready to merge" is `D-34`, implemented today. Closed
  rather than dispatched.
* **t025 cannot be delegated at all.** It needs the family ledger, and `coder.py` excludes
  `chat/` from the sandbox AND refuses any op on a `chat/` path. A brief for it would have
  burned three attempts on a file the model cannot see.
* **t155 cannot be built as written.** Jas asked for finished tasks "sorted by order they
  were completed". Task files record `created` and nothing else, so that ordering has no
  data source -- only file mtime, which any touch destroys. Recording the timestamp is the
  prerequisite, it is mechanical, and it is item 1 below. The listing and the phone surface
  are mine afterwards.
* **Probe finding 6 is REFUTED on Live.** It says `reviewq.branch_exists` misses branches
  that exist only as remote-tracking refs. True in the Sandbox; here `git ls-remote origin
  'coder/*'` returns nothing, because review branches never leave this machine. The triage
  flagged it as a likely Sandbox artefact and it was right.

Each item below names an EXISTING target suite. A run with no target only means nothing
already green broke.
"""
import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CODER = REPO / "projects" / "local-inference" / "coder.py"
DISPATCH_LEDGER = REPO / "workspace" / "dispatch-loop.jsonl"
JOBLOGS = REPO / "workspace" / "run-logs" / "dispatch"

ITEMS = [
    {
        "id": "t155-a",
        "files": ["projects/app-repo/taskstore.py"],
        "target": "scripts/tests/test_task_queue.py",
        "brief": """\
Record WHEN a task was completed, so finished tasks can be ordered by completion.

THE DEFECT. `taskstore.py status <id> done` moves the task to workspace/tasks/done/ and
sets `status: done`, but writes no timestamp. The frontmatter carries `created` and nothing
else. So "show me finished tasks in the order they were completed" -- which is what Jas
asked for in t155 -- has no data behind it. File mtime is the only remaining signal and any
touch destroys it.

WHAT DONE LOOKS LIKE.
1. When `status` sets a task to `done`, add a `completed:` line to the frontmatter holding a
   UTC timestamp in the same format `created` already uses. Match the existing format
   exactly; do not invent a second one.
2. Setting a task to `done` when it is ALREADY done must not overwrite the original
   `completed` value. The first completion is the one that happened.
3. Moving a task OUT of done (back to queued or doing) must remove the line, so a stale
   `completed` cannot outlive the state it describes.
4. Do NOT backfill the 75 existing done tasks. They have no honest value to write.

Add assertions to scripts/tests/test_task_queue.py for all three behaviours, including the
re-completion case, which is the one that will look like it works either way.""",
    },
    {
        "id": "finding-36",
        "files": ["scripts/reviewq.py"],
        "target": "scripts/tests/test_reviewq.py",
        "brief": """\
The keep rate counts approvals of runs that delivered nothing.

THE DEFECT, found by the env3 probe (finding 36). reviewq.py's Pareto keep-rate counts a
`kept` verdict as an approval even when the run it was recorded against FAILED and delivered
nothing. A verdict may legitimately be recorded against a failed run -- the code says so and
means it, because whether the cause was the model or the harness is the useful thing. But a
`kept` on a run with no branch is not work that was approved, and counting it overstates how
much of the model's output was good.

That number governs the next batch size. Overstating it grows the batch on evidence that
does not exist.

WHAT DONE LOOKS LIKE. The keep rate counts only verdicts on runs that actually delivered
something reviewable -- `reviewq.delivered()` already defines exactly that and must be
reused, not reimplemented (L-114, L-115: the fix is always to delete the copy). Verdicts on
undelivered runs stay in the ledger and stay visible in the Pareto's cause breakdown; they
are excluded from the RATE only.

Add an assertion to scripts/tests/test_reviewq.py with a fixture holding one kept verdict on
a delivered run and one on a failed run, and assert the rate counts one of them. Assert the
cause breakdown still sees both.""",
    },
    {
        "id": "mcp-caller",
        "files": ["scripts/workspace_mcp/core.py"],
        "target": "scripts/tests/test_workspace_mcp.py",
        "brief": """\
Every tool-call row in the MCP log says caller "unknown", including rows that came through
the authenticated guard.

THE EVIDENCE, measured on the live log 2026-08-31 over the last 400 rows: `caller` is
"unknown" on 389 of them. Meanwhile `via` is populated -- 251 admin-bearer, 111
oauth:wsmcp_..., 36 direct, 2 chatui. So those requests DID pass the guard that sets the
identity, and the tool row still recorded nothing.

WHY IT MATTERS. The comment above this code says attribution used to rest on matching
timestamps by eye, calls that "inference dressed as record", and introduces a contextvar to
fix it. The contextvar is not reaching the tool rows, so the field reads as a deliberate
refusal to guess when it is actually a value that was never propagated. That is worse than
an empty column: it looks like the design working.

WHAT DONE LOOKS LIKE. A tool call that arrives through the ASGI guard records the caller the
guard established. A call that did NOT come through the guard -- stdio, a background task --
must still record "unknown"; do not guess, and do not inherit whatever the previous request
left behind. Both of those refusals are deliberate and must survive.

Add assertions to scripts/tests/test_workspace_mcp.py for both directions: a call inside a
guarded request records the caller, and a call outside one records "unknown". The second is
the one that matters, because a fix that makes everything look attributed is the failure
mode here.""",
    },
]


def dirty_for(paths):
    """Uncommitted changes in the FILES A RUN WILL EDIT -- not in the whole tree.

    The delivery guard compares only the paths the ops name, so a whole-tree check was
    broader than the thing it was protecting. It also made the batch unrunnable twice in a
    row: coder.py appends every run to workspace/agent-runs.jsonl, so finishing one batch
    dirties the tree and the next one refuses on its own ledger rows. The scope was wider
    than the argument that justified it, which is the shape of L-137 and L-142 -- committed,
    this time, by the script written to route their fixes.
    """
    out = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--", *paths],
                         capture_output=True, text=True, timeout=60).stdout
    return [l for l in out.splitlines() if l.strip()]

def endpoint():
    sys.path.insert(0, str(REPO / "projects" / "local-inference"))
    import coder
    return coder.GATEWAY


def default_model():
    ep = endpoint()
    if "11434" not in ep:
        return "local"
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=5) as f:
            m = (json.load(f).get("models") or [])
        if m:
            return m[0]["name"]
    except Exception as e:
        print(f"  NOTE: /api/ps unreadable ({e}) -- model recorded as 'local'", file=sys.stderr)
    return "local"


def preflight(model):
    body = json.dumps({"model": model, "stream": False, "max_tokens": 5,
                       "messages": [{"role": "user", "content": "ok"}]}).encode()
    req = urllib.request.Request(endpoint(), data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as f:
            json.load(f)
        return True, "the endpoint answered for this model"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read()[:200].decode('utf-8', 'replace')}"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:200]}"


def _utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def rejection_reason(exit_code, output):
    """Why this item was not delivered, in the child's own words. Never empty."""
    if exit_code == 0:
        return ""
    text = (output or "").strip()
    if not text:
        return f"coder.py exited {exit_code} with no output"
    return text


def dispatch_row(item, *, model, attempts, exit_code, output, log_path):
    """One ledger row for one item. Builds a dict only: no write, no read, no print."""
    return {"at": _utc_now(), "source": "scoped_batch", "item_id": item["id"],
            "model": model, "attempts": attempts, "exit": exit_code,
            "outcome": "accepted" if exit_code == 0 else "rejected",
            "rejection_reason": rejection_reason(exit_code, output),
            "log": str(log_path)}


def write_dispatch_rows(ledger_path, rows):
    """Append every row as one JSON line. Whole rows, append mode, never truncate."""
    ledger_path = Path(ledger_path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--model", default=None)
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()

    items = [i for i in ITEMS if not a.only or i["id"] in a.only]
    if a.list:
        for i in items:
            print(f"[{i['id']}] {i['files'][0]}  target={i['target']}")
            print("      " + i["brief"].splitlines()[0])
        return 0

    model = a.model or default_model()
    print(f"endpoint  : {endpoint()}\nmodel     : {model}")
    ok, detail = preflight(model)
    print(f"preflight : {detail}")
    if not ok:
        print("\nREFUSING: the endpoint will not answer for that model.")
        return 3

    dirty = dirty_for(sorted({p for i in items for p in i['files']}))
    if dirty:
        print("\nREFUSING: the working tree is dirty. coder.py verifies in a sandbox copied")
        print("from the working tree and delivers to a branch cut from HEAD; while those")
        print("differ the delivery guard refuses every run.")
        for l in dirty[:20]:
            print(f"  {l}")
        return 2

    results = []
    rows = []
    JOBLOGS.mkdir(parents=True, exist_ok=True)
    for i in items:
        print(f"\n{'=' * 72}\n[{i['id']}] {i['files'][0]}\n{'=' * 72}")
        item_model = f"{i['id']}--{model}"
        safe = re.sub(r'[^A-Za-z0-9._-]', '_', item_model)
        log_path = JOBLOGS / f"{safe}--{int(time.time())}.log"
        with log_path.open("wb") as log_fh:
            r = subprocess.run(
                [sys.executable, str(CODER), "--brief", i["brief"], "--files", *i["files"],
                 "--target", i["target"], "--model", model,
                 "--attempts", str(a.attempts), "--apply"], cwd=str(REPO), timeout=None,
                stdout=log_fh, stderr=subprocess.STDOUT)
        output = log_path.read_text(encoding="utf-8", errors="replace")
        print(output, end="")
        print(f"  log: {log_path}")
        results.append((i["id"], r.returncode))
        rows.append(dispatch_row(i, model=model, attempts=a.attempts, exit_code=r.returncode,
                                 output=output, log_path=log_path))
    write_dispatch_rows(DISPATCH_LEDGER, rows)

    print(f"\n{'=' * 72}\nDONE\n{'=' * 72}")
    for n, rc in results:
        print(f"  [{n}] {'accepted and written' if rc == 0 else f'not delivered (exit {rc})'}")
    print(f"  rows: {len(rows)} -> {DISPATCH_LEDGER}")
    print("\nNothing is merged. Waiting for review:  python3 scripts/reviewq.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
