#!/usr/bin/env python3
"""Feed it the scope of a job; it says which guards will judge it -- and writes them INTO the brief.

    python3 scripts/scope_check.py --files scripts/foo.py projects/local-inference/bar.py
    python3 scripts/scope_check.py --brief workspace/briefs/b.txt --files scripts/foo.py
    python3 scripts/scope_check.py --brief b.txt --files scripts/foo.py --emit b.augmented.txt

WHY, 2026-09-14. Jas: *"Is there any way to make it so future scripts have a mechanical
fixture? Perhaps some kind of schema or scoper that you would feed your scope of work into,
which will check to see if there are truncation rules added."*

The problem it solves is the one the whole truncation sweep exposed. Nine files carried a
record truncation. Every one was written by a session that could have read the rule and did
not, because nothing put the rule in front of the author AT THE MOMENT OF WRITING. The gate
catches it at commit -- afterwards, on staged files only, once the work is already shaped
around the defect.

## WHY THIS IS NOT ANOTHER LIBRARY TO MAINTAIN

Jas's standing rule, 2026-09-11: *"if we use grep we are limited to what it searches for in
its set library of terms... then we have to make a process that updates that library."* A
scoper holding a hand-written list of rules is exactly that, and it decays the first time
someone adds a guard and forgets to register it here.

So it does not hold a list. **It DISCOVERS the guards from the repository** -- the hooks in
`scripts/hooks/`, and the gate modules the pre-commit hook actually invokes -- and takes each
one's constraint text from that guard's OWN docstring. Add a guard and it appears here with no
edit to this file. Delete one and it disappears. There is nothing to keep in step, because the
guards ARE the list.

Two consequences worth stating plainly:

* **The rule text is never retyped**, so it cannot drift from the guard that enforces it. Same
  reasoning as `sync_facts.py` (`L-046`): a restatement is a copy, and a copy drifts.
* **A guard with no docstring shows up as a guard with no explanation**, which is a finding
  about that guard rather than something this script should paper over.

## AND IT DOES NOT MERELY CHECK -- IT WRITES THE CONSTRAINTS IN

Checking that a brief *mentions* truncation would need a list of words meaning "truncation",
which is the term library again, one level up. So `--emit` APPENDS the applicable constraints
to the brief instead. The author cannot forget a rule that is already in the work order, and
the delegated model is judged against a brief that states what the gate will enforce -- which
is the whole point of `writing-oracles-for-delegation`: a requirement stated only in prose
somewhere else is one the next dispatch may legally ignore.

It also RUNS the applicable guards against the named files immediately, so a file that already
carries a defect is reported before any work is layered on top of it.
"""
import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / "scripts" / "hooks"
PRECOMMIT = HOOKS / "pre-commit"


def _first_paragraph(text):
    """The docstring's opening paragraph -- WHOLE, never a fixed-width prefix of it.

    This file is a product of the session that removed every such prefix from the repository;
    a `[:200]` here would be the joke telling itself. A long docstring means a long line of
    output, which is a cost worth paying.
    """
    if not text:
        return ""
    out = []
    for line in text.strip().splitlines():
        if not line.strip() and out:
            break
        out.append(line.strip())
    return " ".join(out)


def discover_guards():
    """Every guard this repository actually runs, found rather than listed.

    Two sources, both authoritative in their own way:
      * scripts/hooks/*.py  -- the PreToolUse guards, which fire on an action
      * the modules named inside scripts/hooks/pre-commit -- the commit gates
    A guard is described by its own module docstring, so the text cannot drift from the code.
    """
    guards = {}

    for p in sorted(HOOKS.glob("*.py")):
        try:
            doc = ast.get_docstring(ast.parse(p.read_text(encoding="utf-8")))
        except (OSError, SyntaxError) as e:
            guards[p.name] = {"path": str(p.relative_to(REPO)), "kind": "hook",
                              "says": f"(could not read: {e})"}
            continue
        guards[p.name] = {"path": str(p.relative_to(REPO)), "kind": "hook",
                          "says": _first_paragraph(doc) or "(no docstring -- it explains nothing)"}

    # The commit gates, taken from what pre-commit INVOKES. Reading the hook rather than
    # keeping a parallel list is the same inversion as the rest of this file.
    if PRECOMMIT.is_file():
        text = PRECOMMIT.read_text(encoding="utf-8", errors="replace")
        # INVOCATIONS, NOT MENTIONS. The first version matched `scripts/(\w+)\.py` anywhere in
        # the hook, which found harness_inventory.py -- named only in a COMMENT -- and missed
        # signature_gate.py, the gate the hook actually runs, because it is invoked as
        # "$(dirname "$0")/../signature_gate.py". So a gate that does not run was reported and
        # the one that does was not. Only lines that actually call python3 count now.
        invoked = set()
        for line in text.splitlines():
            if "python3" not in line or line.lstrip().startswith("#"):
                continue
            invoked.update(re.findall(r"([A-Za-z_]\w*)\.py", line))
        for name in sorted(invoked):
            mod = REPO / "scripts" / f"{name}.py"
            if not mod.is_file() or f"{name}.py" in guards:
                continue
            try:
                doc = ast.get_docstring(ast.parse(mod.read_text(encoding="utf-8")))
            except (OSError, SyntaxError):
                doc = None
            guards[f"{name}.py"] = {
                "path": f"scripts/{name}.py", "kind": "commit-gate",
                "says": _first_paragraph(doc) or "(no docstring -- it explains nothing)"}
    return guards


def run_guard_now(guard, files):
    """Run a commit gate against the files as they stand. Returns (status, whole output).

    Only the gates that take file arguments can be run this way; a PreToolUse hook needs a
    tool-call payload on stdin and is reported rather than run.
    """
    if guard["kind"] != "commit-gate":
        return "not-runnable", "a PreToolUse hook -- it fires on an action, not on a file"
    try:
        r = subprocess.run([sys.executable, str(REPO / guard["path"]), *files],
                           capture_output=True, text=True, timeout=300,
                           stdin=subprocess.DEVNULL, cwd=str(REPO))
    except subprocess.TimeoutExpired:
        return "TIMEOUT", "did not finish in 300s"
    except OSError as e:
        return "could-not-run", str(e)
    # WHOLE output, both streams. See the module docstring.
    body = ((r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")).strip()
    # A GATE THAT TAKES NO FILE ARGUMENTS IS NOT A GATE THAT REFUSES THESE FILES. argparse
    # exits 2 with "usage:" / "unrecognized arguments" when handed paths it does not accept,
    # and reporting that as a refusal is a false alarm that trains the reader to ignore the
    # tool -- which is how a guard stops being read at all (AGENTS.md rule 9).
    if r.returncode == 2 and re.search(r"unrecognized arguments|usage:", body):
        return "takes-no-files", ("does not accept file arguments, so it cannot be run against "
                                 "this scope -- it still applies at commit time")
    if r.returncode == 0:
        return "clean", body
    return f"REFUSES (exit {r.returncode})", body


def constraints_block(guards):
    """The text appended to a brief. Generated from the guards, never typed here."""
    lines = ["", "=" * 78,
             "CONSTRAINTS THIS WORK WILL BE JUDGED BY.",
             "Generated by scripts/scope_check.py from the guards in this repository, so this",
             "list cannot drift from what actually refuses a commit. Do not edit it by hand.",
             "=" * 78, ""]
    for name, g in sorted(guards.items()):
        lines.append(f"* {name}  ({g['path']}, {g['kind']})")
        lines.append(f"    {g['says']}")
        lines.append("")
    lines.append("If any constraint above cannot be met honestly, SAY SO AND STOP rather than")
    lines.append("meeting it dishonestly. A named gap is worth more than a wrong assertion.")
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(
        description="Which guards judge this scope of work -- and put them in the brief.")
    ap.add_argument("--files", nargs="+", required=True,
                    help="repo-relative paths the work will touch")
    ap.add_argument("--brief", default=None, help="the work order, if there is one")
    ap.add_argument("--emit", default=None,
                    help="write the brief with the constraints appended to this path")
    ap.add_argument("--quiet", action="store_true", help="only report problems")
    a = ap.parse_args()

    missing = [f for f in a.files if not (REPO / f).exists()]
    guards = discover_guards()

    print(f"\nSCOPE: {len(a.files)} file(s); {len(guards)} guard(s) discovered "
          f"(not listed -- read from scripts/hooks/ and the pre-commit hook)\n")
    if missing:
        # A path that does not exist is the cheapest possible finding and needs no model.
        print("  PATHS THAT DO NOT EXIST -- fix the scope before starting:")
        for m in missing:
            print(f"    {m}")
        print()

    present = [f for f in a.files if (REPO / f).exists()]
    refused = []
    for name, g in sorted(guards.items()):
        status, body = run_guard_now(g, present) if present else ("not-run", "no existing files")
        if status in ("not-runnable", "takes-no-files") and a.quiet:
            continue
        print(f"  [{status}] {name}")
        if not a.quiet:
            print(f"      {g['says']}")
        if status.startswith("REFUSES"):
            refused.append(name)
            for line in body.splitlines():
                print(f"      {line}")
        print()

    if a.brief:
        bp = Path(a.brief)
        if not bp.is_file():
            print(f"  BRIEF NOT FOUND: {a.brief}")
            return 2
        out = Path(a.emit) if a.emit else None
        if out:
            out.write_text(bp.read_text(encoding="utf-8").rstrip() + "\n"
                           + constraints_block(guards), encoding="utf-8")
            print(f"  wrote {out} -- the brief with every constraint above appended.")
            print("  Dispatch THAT, not the original: a rule the work order does not state is")
            print("  one the next dispatch may legally ignore.")
        else:
            print("  (pass --emit PATH to write the brief with these constraints appended)")

    if refused:
        print(f"\n{len(refused)} guard(s) already REFUSE these files as they stand: "
              f"{', '.join(refused)}")
        print("Fix that before layering new work on top, or the first commit fails for a "
              "reason that has nothing to do with the job.")
        return 1
    print("No guard refuses these files as they stand.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
