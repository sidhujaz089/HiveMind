#!/usr/bin/env python3
"""The reviewer is shown the modules its target suite imports, and told they exist.

Thirteen compliance chunks were parked for a day on this. A generated compliance suite is two
lines -- `import compliance_oracle` and `sys.exit(compliance_oracle.run(path, check))` -- so its
whole pass rule lives in a file the reviewer was never given. It answered, correctly and six
times in one morning, "MISSING: an importable compliance_oracle module in scripts/tests/".

AND THE QUESTION ITSELF WAS WRONG, which is the part the task did not name. Question 3 read
"Does the target suite depend on files OUTSIDE the ones you are allowed to change? If so ... the
work order is impossible as written." Every suite depends on files outside its edit set.
compliance_oracle.py exists and works. Q16 followed that instruction faithfully to a false
conclusion, so showing the file is necessary but not sufficient -- the rule had to change to
"missing, or would itself have to change".
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

COMPLY = "scripts/tests/test_comply_pass3_no_timeout.py"
ORACLE = "scripts/tests/compliance_oracle.py"


def main():
    import preflight_brief as pb
    fails = []

    def ck(label, got, want):
        ok = got == want
        print(f"  [{'ok' if ok else 'BAD'}]    {label} -> {got}")
        if not ok:
            fails.append(f"{label}: expected {want}, got {got}")

    # 0. THE FIXTURE IS REAL. If these move, the cases below stop meaning anything.
    ck("the compliance suite exists", (REPO / COMPLY).is_file(), True)
    ck("its oracle exists", (REPO / ORACLE).is_file(), True)

    # 1. THE CASE THAT PARKED THIRTEEN CHUNKS.
    deps, omitted = pb.target_deps(COMPLY, already_shown=[
        "projects/good-dictator/dictation-test/pass3.py"])
    ck("the oracle the suite imports is shown", ORACLE in deps, True)
    ck("and nothing was omitted for budget", omitted, [])

    # 2. A FILE ALREADY IN --files IS NOT SHOWN TWICE. The window is the scarce thing: a review
    #    that runs out of it records stopped=True and parks the chunk exactly as an objection
    #    does, which is how every review before today became unusable.
    deps, _ = pb.target_deps("scripts/tests/test_notify.py",
                             already_shown=["scripts/notify.py"])
    ck("a dependency already being shown is skipped", deps, [])
    deps, _ = pb.target_deps("scripts/tests/test_notify.py", already_shown=[])
    ck("and is shown when it is NOT in --files", deps, ["scripts/notify.py"])

    # 3. IT FOLLOWS THE CHAIN, BOUNDED BY BYTES RATHER THAN DEPTH -- reversed 2026-09-16 on
    #    the reviewer's own evidence. This case used to assert direct imports ONLY. Shown just
    #    the first hop, Q16 refused the compliance briefs again and said exactly why: "the
    #    oracle calls library_checks.run(...) and scripts/library_checks.py is not shown ... I
    #    would be guessing at the oracle's exact pattern." The rule it needs is two hops out, so
    #    depth was the wrong limit and the window is the real one.
    deps, _ = pb.target_deps(COMPLY, already_shown=[])
    ck("the oracle is still shown", ORACLE in deps, True)
    ck("and so is the module the oracle delegates the rule to",
       "scripts/library_checks.py" in deps, True)
    ck("all of it fits the budget",
       sum((REPO / d).stat().st_size for d in deps) <= pb.DEPS_BUDGET, True)

    # 4. THE BUDGET IS ENFORCED, AND WHAT IT DROPS IS NAMED. A silent drop would leave the
    #    reviewer reasoning over a hole it cannot see -- the defect _show() already exists for.
    deps, omitted = pb.target_deps("scripts/tests/test_scheduler_commands.py",
                                   already_shown=[], budget=1)
    ck("a tiny budget still shows the first dependency", len(deps), 1)
    ck("and names the rest rather than dropping them", len(omitted) >= 1, True)
    ck("shown and omitted never overlap", set(deps) & set(omitted), set())

    # 5. NOTHING FROM OUTSIDE THE REPOSITORY. stdlib and third-party imports are not the
    #    reviewer's problem and would only spend the window.
    deps, _ = pb.target_deps(COMPLY, already_shown=[])
    ck("no stdlib module is resolved to a file",
       [d for d in deps if Path(d).name in ("sys.py", "json.py", "ast.py", "pathlib.py")], [])
    ck("every shown dependency is a real file in the repo",
       all((REPO / d).is_file() for d in deps), True)

    # 6. A MISSING OR UNPARSEABLE TARGET DOES NOT RAISE. The reviewer already refuses a brief
    #    whose target does not exist; this must not crash before it gets there.
    ck("a target that does not exist yields nothing",
       pb.target_deps("scripts/tests/test_does_not_exist.py"), ([], []))

    # 7. THE PROMPT CARRIES THE SECTION AND THE CORRECTED RULE.
    ck("the prompt has a slot for the dependencies", "{deps}" in pb.QUESTION, True)
    ck("it says the modules EXIST", "they EXIST" in pb.QUESTION, True)
    ck("question 3 no longer calls any outside dependency impossible",
       "depend on files OUTSIDE the ones you are allowed to change? If so" in pb.QUESTION, False)
    ck("and it now says depending on an existing helper is normal",
       "NORMAL" in pb.QUESTION and "impossible only if" in pb.QUESTION, True)

    for x in fails:
        print("FAIL:", x)
    print("test_preflight_shows_deps:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
