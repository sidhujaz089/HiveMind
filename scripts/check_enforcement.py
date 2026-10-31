#!/usr/bin/env python3
"""A lesson without a working guard is a lesson that will expire. This is the ratchet.

    python3 scripts/check_enforcement.py          report
    python3 scripts/check_enforcement.py --ratchet lower the ceiling to today's count

THE RULE, AND WHY IT IS A RATCHET RATHER THAN A BAN
---------------------------------------------------
Jas, 2026-09-03: *"all problems need a hard coded solution. Solutions must be able to function
an infinite number of sessions away. Well after any of us have context of the initial
incident."*

That is the correct standard and it cannot be enforced as an absolute, because some lessons
genuinely resist mechanisation and a check that cannot be made to pass is one people learn to
silence (AGENTS.md rule 9). So the enforceable form is a RATCHET on the count of unenforced
lessons: it may go down, never up. Writing a lesson with `ENFORCEMENT: none` now requires
either mechanising an older one to make room, or deliberately raising a number in a committed
file, which is a decision somebody makes rather than a habit nobody notices.

The ceiling needs no memory of any incident. It is an integer in a file and a check that fails
when it is exceeded. That is the whole design: a future session with no context of today reads
"the ceiling is 26 and you are at 27" and knows what to do.

WHAT ELSE THIS CATCHES, AND WHY EACH IS A REAL FAILURE HERE
-----------------------------------------------------------
* AN ENFORCEMENT THAT NAMES A FILE THAT NO LONGER EXISTS. This is worse than `none`: the
  lesson reads as covered. `exam_bank.py` promised an `endurance.py` that was never written,
  and round 2 of the model interviews had no runner at all for weeks because a docstring said
  otherwise (found 2026-09-03).
* AN ENFORCEMENT THAT NAMES A TEST NOTHING RUNS. `check_workspace.py` already refuses orphaned
  check scripts for the same reason: an unreferenced check reads as coverage the repository
  does not have.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not judge whether a guard is any GOOD -- only that it exists and is reachable. Quality
is what `docs/CHECKS.md` rule 1 covers: a suite must prove a failure, not only a pass. Two
different checks, and neither substitutes for the other.
"""
import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LESSONS = REPO / "docs" / "LESSONS.md"
CEILING = REPO / "workspace" / "enforcement-ceiling.json"


def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text


def lessons():
    """[(id, date, enforcement_text_or_None)] for every lesson in the file."""
    text = LESSONS.read_text(encoding="utf-8", errors="replace")
    parts = re.split(r"^\*\*(L-\d+)\s*\[", text, flags=re.MULTILINE)
    out = []
    for i in range(1, len(parts) - 1, 2):
        lid, body = parts[i], parts[i + 1]
        d = re.match(r"\s*(?:VERIFIED|OBSERVED)\s+(\d{4}-\d{2}-\d{2})", body)
        m = re.search(r"^ENFORCEMENT:(.+?)(?=\n\n|\n\*\*|\Z)", body, re.MULTILINE | re.DOTALL)
        out.append((lid, d.group(1) if d else "", " ".join(m.group(1).split()) if m else None))
    return out


def audit():
    """(unenforced, dangling, orphaned, total_named)."""
    unenforced, dangling, orphaned = [], [], []
    named = 0
    # Everything that could plausibly RUN a test: the protocol, the scripts, the docs.
    # LESSONS.md IS EXCLUDED, AND THAT IS THE WHOLE POINT. It is in docs/, so including it
    # would let a lesson satisfy the orphan check BY NAMING THE TEST IN ITS OWN ENFORCEMENT
    # LINE -- the claim proving itself. Caught by test_enforcement_ratchet.py case 3 on the
    # first run of this checker, which is the argument for writing the negative control
    # before trusting a green result.
    runners = ""
    for p in ([f for f in (REPO / "scripts").glob("*.py") if f.name != "check_enforcement.py"]
              + [f for f in (REPO / "docs").glob("*.md") if f.name != "LESSONS.md"]
              + [REPO / "AGENTS.md", REPO / "CLAUDE.md"]):
        try:
            runners += p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"  NOTE: {p} unreadable ({e}) -- not searched for runners", file=sys.stderr)

    for lid, date, enf in lessons():
        if enf is None:
            continue                       # the "declares one at all" check lives elsewhere
        if enf.lower().startswith("none"):
            unenforced.append((lid, date, enf[:70]))
            continue
        for ref in re.findall(r"`([^`]+)`", enf):
            ref = ref.strip()
            if not (ref.endswith((".py", ".sh", ".json", ".md")) and "/" in ref):
                continue
            named += 1
            hits = [REPO / ref] if (REPO / ref).exists() else list(REPO.rglob(Path(ref).name))
            if not hits:
                dangling.append((lid, ref))
                continue
            # A test nobody runs is not a guard. Its NAME must appear somewhere that could
            # invoke it -- the protocol, a script, or the checks document.
            name = Path(ref).name
            if name.startswith("test_") and runners.count(name) < 1:
                orphaned.append((lid, ref))
    return unenforced, dangling, orphaned, named


def ceiling():
    try:
        return json.loads(CEILING.read_text())
    except Exception:                                        # noqa: BLE001
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ratchet", action="store_true",
                    help="lower the ceiling to today's count (it may never be raised here)")
    a = ap.parse_args()

    unenforced, dangling, orphaned, named = audit()
    cap = ceiling()
    n = len(unenforced)

    print(f"  {named} enforcement(s) name a file; {n} lesson(s) declare none")

    problems = []
    for lid, ref in dangling:
        problems.append(f"{lid} names `{ref}` as its enforcement and that file does not "
                        f"exist. A lesson pointing at a guard that is gone is worse than one "
                        f"with no guard: it reads as covered.")
    for lid, ref in orphaned:
        problems.append(f"{lid} names `{ref}`, which nothing in scripts/, docs/ or AGENTS.md "
                        f"mentions -- so nothing runs it. An unreferenced check reads as "
                        f"coverage the repository does not have.")

    if cap is None:
        problems.append(f"{CEILING.name} is missing, so the ratchet is not set. "
                        f"Create it with: python3 scripts/check_enforcement.py --ratchet")
    else:
        limit = cap["max_unenforced"]
        if a.ratchet:
            if n > limit:
                print(_c("31", f"  REFUSING: {n} unenforced exceeds the ceiling of {limit}. "
                               f"--ratchet only LOWERS it. Mechanise a lesson, or raise the "
                               f"number in {CEILING.name} deliberately and say why."))
                return 1
            CEILING.write_text(json.dumps({**cap, "max_unenforced": n,
                                           "lowered_from": limit}, indent=1) + "\n")
            print(_c("32", f"  ceiling lowered {limit} -> {n}"))
            return 0
        if n > limit:
            problems.append(
                f"{n} lessons declare ENFORCEMENT: none, and the ceiling is {limit}. "
                f"A lesson with no mechanical guard survives exactly as long as somebody "
                f"remembers it. Either mechanise one (then run --ratchet), or raise "
                f"max_unenforced in {CEILING.name} on purpose.\n  " +
                "\n  ".join(f"{i} ({d})" for i, d, _ in unenforced[-6:]))
        else:
            print(_c("32", f"  unenforced {n} <= ceiling {limit}"))
            if n < limit:
                print(_c("33", f"  the ceiling can be lowered to {n}: "
                               f"python3 scripts/check_enforcement.py --ratchet"))

    for p in problems:
        print(_c("31", f"  {p}"))
    print("check_enforcement:", "FAILED" if problems else "ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
