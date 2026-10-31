#!/usr/bin/env python3
"""Fixtures for the append tool (Chat's handover §4, approved 2026-08-21).

append() is the only writing tool with no read-modify-write, which is what makes it safe to
call forty times a session. That same property means nothing bounds a file except this code,
so every bound is asserted here.

The assertions that matter are the ones where a bug looks like success: a path outside
workspace/ accepted, chat/ accepted, an existing byte lost, or two concurrent writers
overwriting each other. "Valid append rejected" announces itself; none of these would.
"""
import concurrent.futures as cf
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from workspace_mcp.core import (  # noqa: E402
    Workspace, Denied, HTTP_TOOLS, STDIO_TOOLS, MAX_APPEND_BYTES,
)

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


def refused(fn, *a):
    """The rule name of the refusal, or ACCEPTED if it went through."""
    try:
        fn(*a)
        return "ACCEPTED"
    except Denied as e:
        return e.rule


def make_ws():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "workspace" / "chat-live").mkdir(parents=True)
    (tmp / "chat" / "projects").mkdir(parents=True)
    (tmp / "chat" / "projects" / "ledger.txt").write_text("PRECIOUS\n")
    (tmp / "docs").mkdir()
    (tmp / "docs" / "LESSONS.md").write_text("lessons\n")
    (tmp / "STATE.md").write_text("state\n")
    (tmp / "workspace" / "private").mkdir()
    return Workspace(tmp, caller="fixture"), tmp


def main():
    ws, tmp = make_ws()
    print("Scope — everything outside workspace/ must refuse:")
    report("chat/ (the corpus)",
           refused(ws.append, "chat/projects/ledger.txt", "x"), "path-read-only")
    report("docs/",  refused(ws.append, "docs/LESSONS.md", "x"), "append-outside-workspace")
    report("STATE.md", refused(ws.append, "STATE.md", "x"), "append-outside-workspace")
    report("workspace/private/",
           refused(ws.append, "workspace/private/x.md", "x"), "path-denied")
    # Refuses as path-escapes-repo rather than path-not-relative: the path IS relative, it
    # just resolves outside the root. The fixture originally expected the wrong rule name --
    # the code was right. Asserting the exact rule matters because the rule name is what a
    # refused caller is told, and a wrong one sends them fixing the wrong thing.
    report("traversal out of the repo",
           refused(ws.append, "../../etc/passwd", "x"), "path-escapes-repo")
    report("traversal back in via ..",
           refused(ws.append, "workspace/../STATE.md", "x"), "append-outside-workspace")
    report("bare 'workspace'", refused(ws.append, "workspace", "x"),
           "append-outside-workspace")
    report("a lookalike sibling directory",
           refused(ws.append, "workspace-other/x.md", "x"), "append-outside-workspace")

    print("\nContent bounds:")
    report("empty text", refused(ws.append, "workspace/chat-live/a.jsonl", "   "),
           "empty-text")
    report("oversized chunk",
           refused(ws.append, "workspace/chat-live/a.jsonl", "z" * (MAX_APPEND_BYTES + 1)),
           "append-too-large")

    print("\nBehaviour:")
    f = "workspace/chat-live/2026-08-21__a.jsonl"
    r1 = ws.append(f, '{"turn": 1}')
    report("creates the file when absent", (tmp / f).is_file(), True)
    ws.append(f, '{"turn": 2}')
    body = (tmp / f).read_text()
    report("both lines present", body.count("\n"), 2)
    report("the first line survives the second", '{"turn": 1}' in body, True)
    report("a missing newline is supplied", body.endswith("}\n"), True)
    report("server reports arrival time", "received_at" in r1, True)

    # Nothing already written may be destroyed. This is THE property that makes append safe
    # over http where write is not, so it is asserted rather than assumed.
    before = (tmp / f).read_text()
    ws.append(f, "later")
    report("no existing byte is lost", (tmp / f).read_text().startswith(before), True)

    print("\nConcurrency — 40 turns, as a real session would:")
    def one(i):
        ws.append("workspace/chat-live/busy.jsonl", f'{{"turn": {i}}}')
    with cf.ThreadPoolExecutor(16) as ex:
        list(ex.map(one, range(40)))
    lines = (tmp / "workspace/chat-live/busy.jsonl").read_text().splitlines()
    report("every line arrived", len(lines), 40)
    report("no line is torn", all(l.startswith('{"turn": ') and l.endswith("}")
                                 for l in lines), True)
    report("all 40 turns distinct", len(set(lines)), 40)

    print("\nTransport scoping:")
    report("exposed over http", "append" in HTTP_TOOLS, True)
    report("exposed over stdio", "append" in STDIO_TOOLS, True)
    # append EXISTS because write was withheld over http -- a narrow, path-scoped way for
    # Chat to add without being able to overwrite. write and replace were granted on
    # 2026-08-31 (t194), gated through the commit hook, so that is no longer the reason.
    #
    # append is still the right tool for adding a line to a log and still the only one that
    # is atomic under concurrent callers, which is what these assertions are actually about.
    # What changed is the JUSTIFICATION, so the assertion changed with it rather than being
    # deleted: a test that pins a policy outlives the policy and then fails on a decision
    # rather than on a defect.
    report("write is now over http too, gated by the commit hook",
           "write" in HTTP_TOOLS, True)
    report("and append remains, because atomicity is its own reason",
           "append" in HTTP_TOOLS, True)

    print("\nThe call log records it:")
    log = (tmp / "workspace" / "mcp-calls.jsonl").read_text()
    report("append calls are logged", log.count('"tool": "append"') > 0, True)
    report("refusals are logged too", '"outcome": "refused"' in log, True)

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
