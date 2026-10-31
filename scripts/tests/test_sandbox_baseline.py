#!/usr/bin/env python3
"""The sandbox baseline is DECLARED, not discovered. An undeclared failure stops the run.

WHY (t260). coder.py baselined its sandbox by running every suite and accepting whatever came
back as normal. So six phantom failures sat in every baseline, and a REAL regression a
dispatch introduced was indistinguishable from the scenery. On 2026-09-11 a run announced
"16 suite(s) already failing" and nothing objected -- four of those pass on main and fail in
the sandbox only because .git and .venv are excluded from the copy.

THE INVERSION, which is the point. Do not blacklist the failures we happen to know about --
that needs a librarian, and the failure nobody predicted is the one worth hearing. Enumerate
what MAY fail: test-failures-ceiling.json for real debt, sandbox-baseline.json for what the
sandbox physically cannot run. Anything else failing is a finding that stops the dispatch
BEFORE the model is asked to do anything, because a baseline nobody can trust makes every
later verdict unfalsifiable.

WHAT baseline_findings() IS. A pure function: given the set of suites that failed and the two
declarations, return the problems. Pure so this suite can drive it without building a sandbox
or starting a model -- the thing that made the old behaviour untestable.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "projects" / "local-inference"))


def main():
    fails = []

    def check(label, cond, detail=""):
        print(f"  [{'ok' if cond else 'BAD'}]    {label}{'  ' + detail if detail else ''}")
        if not cond:
            fails.append(f"{label} {detail}")

    # 1. THE DECLARATION EXISTS AND IS HONEST. Every entry carries a reason, because an
    #    entry without one is permission nobody can audit or retire.
    p = REPO / "workspace" / "sandbox-baseline.json"
    check("workspace/sandbox-baseline.json exists", p.is_file(), str(p))
    if not p.is_file():
        print("\nFAIL: no declaration to test against")
        return 1
    decl = json.loads(p.read_text())
    entries = decl.get("cannot_run_in_sandbox", {})
    check("it declares at least one suite", len(entries) > 0, f"{len(entries)}")
    unreasoned = [k for k, v in entries.items() if not str(v).strip()]
    check("every declared suite carries a reason", unreasoned == [], ", ".join(unreasoned))
    # ASSERT THE VALUE: a declared suite must actually exist, or the entry is permission for
    # a file nobody has. A stale name silently widens the allowlist.
    missing = [k for k in entries if not (REPO / "scripts" / "tests" / k).is_file()]
    check("every declared suite is a real file", missing == [], ", ".join(missing))

    import coder
    check("coder exposes baseline_findings()", hasattr(coder, "baseline_findings"))
    if not hasattr(coder, "baseline_findings"):
        print("\nFAIL: nothing to test")
        return 1
    bf = coder.baseline_findings

    ceiling = {"test_known_debt.py"}
    cannot = {"test_needs_git.py"}

    # 2. THE THING IT IS FOR. An undeclared failure is a finding that NAMES the suite.
    probs = bf({"test_surprise.py"}, ceiling, cannot)
    joined = " | ".join(probs)
    check("an undeclared failure is reported", len(probs) > 0, joined)
    check("...and the problem names the suite", "test_surprise.py" in joined, joined)

    # 3. DECLARED FAILURES ARE SILENT -- or the guard fires every run and gets bypassed
    #    within a week, which is G10 and worse than no guard.
    check("a ceiling failure is silent", bf({"test_known_debt.py"}, ceiling, cannot) == [],
          str(bf({"test_known_debt.py"}, ceiling, cannot)))
    check("a sandbox-only failure is silent", bf({"test_needs_git.py"}, ceiling, cannot) == [],
          str(bf({"test_needs_git.py"}, ceiling, cannot)))
    check("both together are silent",
          bf({"test_known_debt.py", "test_needs_git.py"}, ceiling, cannot) == [])
    check("nothing failing is silent", bf(set(), ceiling, cannot) == [])

    # 4. THE RATCHET. A declared suite that PASSES is also a finding -- a stale entry is
    #    permission that outlived its cause, and permission nobody retires is how an
    #    allowlist rots back into a blacklist.
    probs = bf(set(), ceiling, cannot, passed={"test_needs_git.py"})
    joined = " | ".join(probs)
    check("a declared suite that now passes is reported", len(probs) > 0, joined)
    check("...and names it", "test_needs_git.py" in joined, joined)

    # 5. THE DECAY TEST, and the one with teeth: a function that returns [] for everything
    #    passes cases 3 and the negative half of 1. Mixed input must separate them --
    #    report the surprise, stay quiet about the declared one, in the same call.
    probs = bf({"test_surprise.py", "test_known_debt.py", "test_needs_git.py"},
               ceiling, cannot)
    joined = " | ".join(probs)
    check("mixed: the surprise is reported", "test_surprise.py" in joined, joined)
    check("mixed: the declared ones are NOT", "test_known_debt.py" not in joined
          and "test_needs_git.py" not in joined, joined)

    # 6. Bare names and paths must resolve to the same suite, or the guard is defeated by
    #    however run_suites happens to spell its output today.
    probs = bf({"scripts/tests/test_known_debt.py"}, ceiling, cannot)
    check("a path-spelled declared failure is still silent", probs == [], str(probs))

    print()
    if fails:
        print(f"FAIL: {len(fails)} problem(s)")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
