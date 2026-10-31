#!/usr/bin/env python3
"""t4641: a script declared a TOOL is not a check missing its caller -- and the declaration cannot lie.

The orphan scan asks "what runs this?" and that is the right question for a check. For
scripts/plan_run.py it is the wrong one: an agent runs it when a document is wanted, and a schedule
for it would produce documents nobody asked for. So a tool declares itself in
workspace/script-dispositions.json, with the reason, and the scan stops calling it an orphan.

What makes that a DECLARATION and not an exemption list (the difference matters, because an
exemption list would be rule 9 -- a check edited into silence):

  1. a declared tool is still a real script: if scripts/<name> is gone, that is a problem, not a
     quiet no-op;
  2. a declaration must carry a reason, so "tools: {everything: ''}" is not a way to buy the WARN
     down;
  3. a declaration only says no caller is EXPECTED. It does not make the name stop being scanned
     for callers.
  4. a file that exists but cannot be parsed is a problem, and declares nothing -- an unreadable
     declaration must not read as "no tools", which would be the vacuous pass of docs/CHECKS.md.

Hermetic: every case is a TemporaryDirectory with a one-line repo in it.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_workspace as cw  # noqa: E402

FAILS = []
REASON = "An agent runs it when the work needs it; nothing schedules it, by design."


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


def repo_with(tmp):
    """A minimal repository: one script nothing calls, one doc, one contract."""
    r = Path(tmp)
    (r / "scripts").mkdir()
    (r / "docs").mkdir()
    (r / "workspace").mkdir()
    (r / "AGENTS.md").write_text("# contract\n")
    (r / "scripts" / "lonely.py").write_text("def f():\n    return 1\n")
    return r


def declare(r, tools):
    (r / "workspace" / "script-dispositions.json").write_text(
        json.dumps({"_why": "test", "tools": tools}))


def main():
    # 1. A declared tool with a reason is not an orphan, and declares no problems.
    with tempfile.TemporaryDirectory() as d:
        r = repo_with(d)
        declare(r, {"lonely.py": REASON})
        names, problems = cw.declared_tools(r)
        check("a declared tool is read from workspace/script-dispositions.json",
              names == {"lonely.py"}, names)
        check("  with no problem raised", problems == [], problems)
        check("  and the orphan scan stops naming it",
              "lonely.py" not in cw.orphan_scripts(r), cw.orphan_scripts(r))

    # 2. THE GUARD THAT MAKES IT A DECLARATION: an undeclared script is still an orphan, so the
    #    mechanism cannot be read as "orphans are gone now".
    with tempfile.TemporaryDirectory() as d:
        r = repo_with(d)
        (r / "scripts" / "other.py").write_text("def g():\n    return 2\n")
        declare(r, {"lonely.py": REASON})
        orphans = cw.orphan_scripts(r)
        check("an undeclared script nothing calls is still an orphan", "other.py" in orphans, orphans)

    # 3. A declaration for a script that does not exist is a problem -- a stale entry would hide the
    #    real script from the scan while looking like it was dealt with.
    with tempfile.TemporaryDirectory() as d:
        r = repo_with(d)
        declare(r, {"gone.py": REASON})
        names, problems = cw.declared_tools(r)
        check("a declared tool that is not on disk is a problem",
              any("gone.py" in p and "does not exist" in p for p in problems), problems)

    # 4. A declaration with no reason is an exemption, and is refused.
    with tempfile.TemporaryDirectory() as d:
        r = repo_with(d)
        declare(r, {"lonely.py": ""})
        _, problems = cw.declared_tools(r)
        check("a declared tool with no reason is a problem",
              any("lonely.py" in p and "reason" in p for p in problems), problems)

    # 5. An unreadable declaration declares NOTHING and says so. It must not read as "no tools,
    #    therefore every script is a check, therefore fine".
    with tempfile.TemporaryDirectory() as d:
        r = repo_with(d)
        (r / "workspace" / "script-dispositions.json").write_text("{not json")
        names, problems = cw.declared_tools(r)
        check("a broken declaration is a problem", len(problems) == 1, problems)
        check("  and it declares nothing", names == set(), names)
        check("  so its tool is judged as a check again",
              "lonely.py" in cw.orphan_scripts(r), cw.orphan_scripts(r))

    # 6. No file at all is not a problem: nothing is declared, which is the state before t4641.
    with tempfile.TemporaryDirectory() as d:
        r = repo_with(d)
        names, problems = cw.declared_tools(r)
        check("no declaration file is not an error", (names, problems) == (set(), []),
              (names, problems))

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
