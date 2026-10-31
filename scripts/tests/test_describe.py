#!/usr/bin/env python3
"""One question the system could not answer: "what is this thing?" (I3a, t8714).

A new session can search (`symindex`, `recall`, `transcripts`, `grep`) but nothing answered *"there is a
thing called X -- what is it, who owns it, what keeps it honest, and what do I run to act on it?"* without
opening a file. That is the gap behind Jas's "progressive disclosure": not a lack of search, a lack of a
cheap lookup.

`describe.py <name>` answers it from the stores that already exist, and it COPIES NOTHING:

  script  -> scripts/library.json (what it is) + the chart (which task it serves)
  skill   -> docs/skills/<name>/SKILL.md front matter (what it is, when it applies)
  task    -> the task file (title, status, assignment)
  page    -> hub_common.PAGES (what it shows, the files it is a view of, what keeps it honest)
  role    -> ROLES.json (model, tasks, tools, what)
  network -> workspace/network.json (how it is reached, its source, its state)

Every answer names WHERE it came from, because an answer with no provenance is the thing this repository
keeps being bitten by. And an unknown name says so -- it does not guess, and it does not return the closest
match as though it were the one asked for.

What this refuses:

  * an unknown name being answered with a near-miss (a lookup that guesses is worse than one that says no);
  * an answer with no source line;
  * a store that cannot be read being reported as "not found" -- those are different facts;
  * and a bare name that is ambiguous across kinds being silently resolved to one of them.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import describe  # noqa: E402

FAILS = []


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


def main():
    # 1. EACH KIND ANSWERS, from a real thing that exists in this repository.
    cases = [
        ("check_workspace.py", "script"),
        ("root-cause-analysis", "skill"),
        ("network", "page"),
        ("manager", "role"),
        ("gx10", "network"),
    ]
    for name, kind in cases:
        d = describe.describe(name)
        check(f"{name!r} resolves as a {kind}", d.get("kind") == kind, d)
        check(f"  and says where the answer came from", bool(d.get("source")), d.get("source"))
        check(f"  and says what it is", bool(str(d.get("what") or "").strip()), d.get("what"))

    # 2. A TASK ID RESOLVES TOO -- the most-asked question of all. Task files are gitignored and live in
    #    the working checkout, so this points the module at a temp directory rather than reading live state.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / "t9999--a-task-the-test-made.md"
        tp.write_text("---\nid: t9999\ntitle: a task the test made\nstatus: done\n"
                      "assigned: production\n---\n\nbody\n", encoding="utf-8")
        describe.TASKS = Path(td)
        d = describe.describe("t9999")
    check("a task id resolves to a task", d.get("kind") == "task", d)
    check("  with its status", d.get("status") == "done", d)
    check("  and its file", "t9999" in str(d.get("source")), d.get("source"))
    d = describe.describe("t8650")


    # 3. AN UNKNOWN NAME SAYS SO. No near-miss, no closest match.
    d = describe.describe("check_workspace_py_typo")
    check("an unknown name is not found", d.get("kind") is None, d)
    check("  and it says so plainly", "not found" in str(d.get("what", "")).lower(), d)

    # 4. A LOOKUP NEVER INVENTS A SOURCE.
    for name, _ in cases:
        d = describe.describe(name)
        check(f"{name!r} names a file or command as its source",
              any(ch in str(d.get("source")) for ch in (".json", ".md", ".py", "command", "task")),
              d.get("source"))

    # 5. THE CLI PRINTS AND EXITS: 0 found, 1 not found.
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = describe.main(["check_workspace.py"])
    check("the CLI returns 0 for a found name", rc == 0, rc)
    check("  and prints the kind first", buf.getvalue().lstrip().startswith("script"), buf.getvalue()[:80])
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = describe.main(["definitely-not-a-thing"])
    check("the CLI returns 1 for an unknown name", rc == 1, rc)
    check("  and says UNKNOWN", "UNKNOWN" in buf.getvalue(), buf.getvalue()[:80])

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
