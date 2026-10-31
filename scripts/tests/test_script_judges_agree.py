#!/usr/bin/env python3
"""Every place that judges a script must name the SAME model.

There are three, and on 2026-09-15 they disagreed. Jas moved the script certifier to Q16 after
measuring that Gemma called all 37 uncalled scripts "on-demand" where Q16 split them 25
on-demand / 11 one-time / 1 dead. corpus_scripts.py was changed. truncation_review.py -- which
reviews every script registered in or changed in the script library, and is therefore the actual
gate on the library -- was not, and neither was the `auditor` role that routes the work. The
instruction was applied to one of three sites and nobody noticed for a day.

An instruction obeyed in one place out of three is the drift this catches. It asserts agreement
rather than a particular model, so swapping the auditor stays a one-line change -- it just has
to be the same one-line change everywhere.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))


def main():
    import corpus_scripts
    import dispatch_loop
    import truncation_review
    fails = []

    # THE ROUTER IS WHOEVER THE STORE SAYS OWNS SCRIPT REVIEW (t9554), not a role named here: on
    # 2026-10-29 both Manager and Auditor list it and the dispatcher's owner() is the first of them.
    import task_routing
    _router = task_routing.owner(task_routing.load(), "script-review")
    sites = {
        "corpus_scripts.MODEL (describes and classifies a script)": corpus_scripts.MODEL,
        "truncation_review.MODEL (audits a script for defects)": truncation_review.MODEL,
        f"dispatch_loop.ROLES[{_router!r}] (routes the script-review job)":
            dispatch_loop.ROLES[_router],
    }
    for label, model in sites.items():
        print(f"      {model:26s} {label}")

    agreed = set(sites.values())
    ok = len(agreed) == 1
    print(f"  [{'ok' if ok else 'BAD'}]    all three name one model ({sorted(agreed)})")
    if not ok:
        fails.append(f"the script judges disagree: {sites}. Whoever changed one meant to "
                     f"change all three -- that is what happened on 2026-09-15.")

    # AND THE JOB MUST ACTUALLY GO TO THE MODEL THAT DOES THE WORK. dispatch_loop routes by the
    # auditor role and then runs truncation_review.py, which picks its own model: if those two
    # part company, --status reports a review by a model that never saw the file.
    ok = dispatch_loop.ROLES[_router] == truncation_review.MODEL
    print(f"  [{'ok' if ok else 'BAD'}]    the routed model is the one that runs the review")
    if not ok:
        fails.append(f"dispatch_loop routes script-review to {dispatch_loop.ROLES[_router]} "
                     f"but truncation_review.py asks {truncation_review.MODEL}; the ledger "
                     f"would name the wrong model")

    # THE AUDITOR MUST BE A MODEL THE BOX ACTUALLY DECLARES, or the job cannot run at all.
    known = set(dispatch_loop.PRODUCTION_MODELS) | set(dispatch_loop.ROLES.values())
    ok = truncation_review.MODEL in known
    print(f"  [{'ok' if ok else 'BAD'}]    the auditor is a declared model")
    if not ok:
        fails.append(f"{truncation_review.MODEL} is named nowhere else in the scheduler")

    for x in fails:
        print("FAIL:", x)
    print("test_script_judges_agree:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
