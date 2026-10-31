#!/usr/bin/env python3
"""The Claude review queue — what the local model built that Jas is waiting on me to vet.

WHY IT EXISTS
-------------
`t154`, from Jas: *"We have a Jas review queue. We need a Claude review queue so I can
see what the local models did that is waiting on you to double check."*

The pipeline has two gates, and only one of them is a person:

    local model  ->  [awaiting Claude]  ->  merged  ->  Jas uses it

CHANGED 2026-08-31, by Jas: *"I dont have the technical ability to review anything before a
merge. you're the quality gate on that. I'll report functionality bugs I encounter."*

He is right that he was never the gate he was written down as. A reviewer who cannot read the
diff cannot refuse it, and a gate that always says yes is not a gate -- it is a delay that
looks like assurance, which is the same shape as every check in this repository that passed
having examined nothing.

So the human gate moved from BEFORE the merge to AFTER it, where he has the only evidence he
actually holds: whether the thing works when he uses it. That makes `escaped` the load-bearing
mechanism rather than a footnote -- it is now the ONLY signal that a defect got past me, and
it resets the batch size to 1. Record one the moment he reports a bug in merged work:

    reviewq.py escaped <task|--branch|--at> --why '...'

What this costs: a defect I miss reaches main, and `git revert` is the remedy rather than
`git branch -D`. What it buys: work stops queueing behind a review that was never going to
happen. Vet by RUNNING the change, not by reading the diff -- on 2026-08-31 that is what
separated a finding genuinely fixed from one whose suite never touched the defect. The local model may run ahead and pile work up; that is
the point. Batching review is not only cheaper, it is **better**, because defects that look
random one at a time resolve into a pattern when you read twenty together. Tonight three of
six harness defects were one shape -- a producer and a consumer each holding their own copy
of a rule -- and that was only visible in aggregate.

THE QUEUE IS DERIVED, NOT STORED
--------------------------------
`agent-runs.jsonl` says what the model produced. `review-verdicts.jsonl` says what I decided.
The queue is the difference. There is deliberately no third file holding "pending" state,
because a state file that must be kept in step with two ledgers is exactly the drift that
caused `L-114`, `L-115` and the branch-writer bug. Nothing to sync means nothing to desync.

Usage:
    reviewq.py                       what is waiting on Claude
    reviewq.py --all                 include what is already vetted
    reviewq.py verdict <task> <kept|sent-back|blocked> --why "..." [--category ...]
    reviewq.py pareto                defect categories, worst first
    reviewq.py --self-test
"""
import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNS = REPO / "workspace" / "agent-runs.jsonl"

# ---------------------------------------------------------------------------------------
# TWO VOCABULARIES IN ONE FIELD, NAMED (2026-10-24, t1587)
#
# `agent-runs.jsonl` writes `outcome` from two different vocabularies: the coder lane writes a
# VERDICT (ACCEPTED, TESTS-FAILED, REFUSED-TO-START...), the console lane writes the model's
# TRANSPORT reason (stop, length, timeout, http-500...). Measured on this ledger: 844 coder rows
# over 14 verdicts, 5,603 ask_q16 rows over 6 reasons -- so 70% of `outcome` means "the model
# finished", and any success rate computed from the column as written is meaningless.
#
# History is not rewritten: these rows are the record. Nothing is coerced either --
# ask_q16.py stopped translating reasons into a tidy local vocabulary on purpose, so a reason
# nobody has invented yet surfaces on its own. What is added is the DECLARATION of which
# vocabulary a value belongs to, and a check (check_workspace, "Every run outcome is
# classified") that fails on a value in neither -- a new outcome gets classified the day it
# appears instead of quietly becoming part of a percentage.
PIPELINE_OUTCOMES = frozenset({
    "ACCEPTED", "ACCEPTED-DRYRUN", "ACCEPTED-NOT-WRITTEN", "ASKED", "BAD-SHAPE", "BAD-SYNTAX",
    "LIBRARY-BLOCK", "NEEDS-SCOPE", "NO-OP", "REFUSED", "REFUSED-TO-START", "TESTS-FAILED",
    "UNPARSEABLE", "WINDOW-FULL",
    # coder.py's twin refusal (t1130): a running twin already holds this brief's files, so the model was never
    # invoked and the dispatch exits 2. A pipeline refusal, not a model outcome.
    "LEASE-HELD",
})
TRANSPORT_OUTCOMES = frozenset({
    "stop", "length", "timeout", "http-500", "http-404", "lease_held",
    "finished-from-reasoning", "blind", "ok",   # the last two: explore_loop's, measured here
    # A RUN THAT ENDED WITHOUT SAYING WHY (t3578). ask_q16._ledger wrote r.get("done_reason")
    # straight through, and done_reason is None whenever the model's final reply carries none (a
    # tool-round cap, or a server that answers without one) -- two rows on this ledger reached it
    # that way, 2026-10-06T04:51:53Z and 2026-10-14T01:49:54Z. That is a fact about how the run
    # ended, not a gap to paper over, so it is declared here and _ledger now emits it: no writer
    # leaves an outcome null.
    "no-done-reason",
})
# A VERDICT THAT MEANS THE CHANGE EXISTS. ACCEPTED-DRYRUN is deliberately absent: nothing was
# written, so counting it as a delivery would overstate the line (coder.py returns 3 for
# ACCEPTED-NOT-WRITTEN precisely to stop that being inferred).
DELIVERY_OUTCOMES = frozenset({"ACCEPTED"})


def outcome_class(row):
    """'pipeline' | 'transport' | 'unrecorded' | 'unclassified' for one run row's outcome.

    `unrecorded` is a row with no outcome at all -- a writer emitted a run without a verdict.
    It is reported by the check rather than failed there, because the older rows are historical;
    a NEW unclassified WORD still fails, which is the case that would silently join a percentage.
    The two rows that were unrecorded (2026-10-06, 2026-10-14) are now classified as
    "no-done-reason", which is what they were, so a count of `unrecorded` should now be zero (t3578).
    """
    value = (row or {}).get("outcome")
    if value in (None, ""):
        return "unrecorded"
    if value in PIPELINE_OUTCOMES:
        return "pipeline"
    if value in TRANSPORT_OUTCOMES:
        return "transport"
    return "unclassified"


def is_delivery(row):
    """True when the row records a change that was actually delivered."""
    return (row or {}).get("outcome") in DELIVERY_OUTCOMES

VERDICTS = REPO / "workspace" / "review-verdicts.jsonl"

KEPT, SENT_BACK, BLOCKED, SUPERSEDED = "kept", "sent-back", "blocked", "superseded"
# `superseded` is not bureaucracy. Several runs are dry-run precursors of the same patch that
# later landed on a branch. Recording those as `kept` would inflate the keep rate and, worse,
# make the Pareto count one piece of work several times -- so the chart would measure how
# often a task was retried rather than what goes wrong.
VERDICT_WORDS = (KEPT, SENT_BACK, BLOCKED, SUPERSEDED)

# Categories exist so the Pareto view has something to count. Free text is allowed -- an
# unnamed category is better than a wrong one -- but these cover what has actually occurred.
# Where a defect that reached review SHOULD have been stopped. Tallied by `reviewq.py upfront`.
def _render(text):
    """{role:<task>} -> the role holding it, from the store (one source of truth, 2026-10-13)."""
    import task_routing
    return task_routing.render(text)


UPFRONT = {
    "brief": "the brief was unclear, contradictory or incomplete -- {role:scoping}'s scoping/brief writing",
    "oracle": "the target suite could pass without the change being right",
    "preflight": "{role:preflight}'s brief review should have objected and did not",
    "harness": "coder.py or the scheduler misled or constrained the model",
    "model": "brief, oracle and harness were sound; the model got it wrong",
}

CATEGORIES = {
    "wrong-behaviour": "compiles and passes, but does the wrong thing",
    "regression": "fixes the target and breaks something that worked",
    "incomplete": "addresses part of the task only",
    "unsafe": "crashes on input the tests do not cover",
    "style": "works, but does not match the surrounding code",
    "harness": "the run failed for a reason upstream of the model",
}


# SCOPE QUALITY (Jas, 2026-09-28: score the model that scoped the work, at every check). The merge
# verdict is the last check a scope meets; `should_catch` already says whose fault a defect was.
SHOULD_CATCH_CLASS = {"brief": "brief-wrong", "oracle": "oracle-weak", "preflight": "brief-wrong",
                      "harness": "harness", "model": "implementation"}


def _scope_event(row, escaped=False):
    """One scope_quality event for a verdict. Never breaks the verdict: record() reports its own errors."""
    sys.path.insert(0, str(REPO / "scripts"))
    import scope_quality
    where = {"ledger": VERDICTS.parent / "scope-quality.jsonl",
             "defects": VERDICTS.parent / "scope-defects"}      # beside the verdicts, so a test's stay in its temp dir
    t = str(row.get("task") or "")
    task, chunk = (t, "") if t.startswith("t") else ("", t)
    if escaped:
        return scope_quality.record("escape", "escaped", task=task, chunk=chunk,
                                    defect_class=SHOULD_CATCH_CLASS.get(row.get("should_catch"), "unclassified"),
                                    evidence=row.get("why", ""), source="reviewq escaped", **where)
    v = row.get("verdict")
    if v == SUPERSEDED:
        return None
    outcome = {KEPT: "merged", SENT_BACK: "sent-back", BLOCKED: "rejected"}.get(v, v)
    cls = SHOULD_CATCH_CLASS.get(row.get("should_catch"), "") if (v != KEPT or row.get("repaired")) else ""
    return scope_quality.record("merge", outcome, task=task, chunk=chunk, defect_class=cls,
                                evidence=row.get("why") or "", source="reviewq verdict",
                                category=row.get("category"), branch=row.get("branch"), **where)


def _read(p):
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            # Report, never drop. Silently skipping a malformed row is how a queue lies about
            # being empty -- the same defect t146 fixed in the receipt parser.
            out.append({"_unreadable": line})
    return out


def runs():
    return [r for r in _read(RUNS) if "_unreadable" not in r]


def verdicts():
    return [v for v in _read(VERDICTS) if "_unreadable" not in v]


def unreadable():
    return [r for p in (RUNS, VERDICTS) for r in _read(p) if "_unreadable" in r]


# An ACCEPTED* outcome that produced NOTHING to look at. The prefix test below is a
# startswith, which is how ACCEPTED-NOT-WRITTEN -- added to coder.py the same day for L-130 --
# silently became "reviewable" and was then displayed as a DRY RUN, advising Jas to "re-run
# with --apply" on a run where he HAD passed --apply and the branch write had failed.
#
# Producer and consumer holding their own copy of the same rule, drifting in silence: the
# third instance in this repository (L-114 baseline vs diff_of, L-115 serve.py vs gateway.py),
# and this time I was the producer who added a value and did not tell the consumer.
NOTHING_TO_REVIEW = {"ACCEPTED-NOT-WRITTEN"}


def delivered(rows=None):
    """Runs that produced something reviewable: an outcome the model considers finished.

    "Finished" is not the same as "delivered something". A run whose branch could not be
    written is finished and has nothing for a reviewer to open.
    """
    return [r for r in (rows if rows is not None else runs())
            if str(r.get("outcome", "")).startswith("ACCEPTED")
            and r.get("outcome") not in NOTHING_TO_REVIEW]


def pending(rows=None, vs=None):
    """Delivered runs with no verdict yet, newest first, one per (task, at)."""
    seen = {(v.get("task"), v.get("at")) for v in (vs if vs is not None else verdicts())}
    out = [r for r in delivered(rows) if (r.get("task"), r.get("at")) not in seen]
    return sorted(out, key=lambda r: r.get("at") or "", reverse=True)


def branch_merged(name):
    """(exists, has_unmerged_work) for a branch NAME, asked of git rather than inferred.

    Jas, 2026-10-28: "Claude had 10 items to merge. There really weren't. Can you do an RCA on why I saw
    10 items even though they were already merged? I don't want this bug to resurface."

    The Production tab called a branch "ready to merge" when the REF still existed (`branch_exists`), and
    nothing deletes a branch after a merge, so ten fully-merged branches sat under a heading that said
    "Nothing is merged -- that is your call". The question the page needs answered is not "does this ref
    exist" but "is there work on it that main does not have", and git answers that exactly:
    `git merge-base --is-ancestor <branch> main` succeeds when the branch is already IN main.

    Returns (False, False) for a name that is not a branch, and (True, None) when git could not answer --
    neither is "merged", because an answer nobody could get is not evidence of anything.
    """
    if not name:
        return False, False
    r = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--verify", "--quiet", f"{name}^{{commit}}"],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return False, False
    a = subprocess.run(["git", "-C", str(REPO), "merge-base", "--is-ancestor", name, "main"],
                       capture_output=True, text=True, timeout=60)
    if a.returncode == 0:
        return True, False                      # in main: merged, whatever the ref still does
    if a.returncode == 1:
        return True, True                       # real work main does not have
    return True, None                           # git could not answer: not "merged"


def branch_exists(name):
    if not name:
        return False
    r = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--verify", name],
                       capture_output=True, text=True, timeout=60)
    if r.returncode == 0:
        return True
    r = subprocess.run(["git", "-C", str(REPO), "for-each-ref", "refs/heads", "refs/remotes", "--format=%(refname) %(refname:short)"],
                       capture_output=True, text=True, timeout=60)
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            if line:
                parts = line.split(" ", 1)
                if len(parts) == 2:
                    refname, shortname = parts
                    if refname == f"refs/remotes/origin/{name}" or shortname == name:
                        return True
    return False


def diff_noise(branch):
    """How much of `git diff main..B` is the branch's own work. (t1036)

    A reviewer who types the two-dot form reads other people's commits as the model's:
    rca/4248cb2e2d0e.md -- 10 files / 269 deletions for a 1-file / 135-insertion commit.
    So the queue states the ratio: files touched by `main..B` vs files touched since
    `git merge-base main B`, the anchor the merge tooling already uses.
    """
    def files(rng):
        r = subprocess.run(["git", "-C", str(REPO), "diff", "--name-only", rng],
                           capture_output=True, text=True, timeout=60)
        return len([l for l in r.stdout.splitlines() if l.strip()]) if r.returncode == 0 else 0
    n = subprocess.run(["git", "-C", str(REPO), "rev-list", "--count", f"main..{branch}"],
                       capture_output=True, text=True, timeout=60)
    commits = int(n.stdout.strip()) if n.returncode == 0 and n.stdout.strip().isdigit() else 0
    two = files(f"main..{branch}")
    mb = subprocess.run(["git", "-C", str(REPO), "merge-base", "main", branch],
                        capture_output=True, text=True, timeout=60)
    base = files(f"{mb.stdout.strip()}..{branch}") if (mb.returncode == 0 and mb.stdout.strip()) else two
    return {"commits": commits, "files_two_dot": two, "files_base": base}



# --- how big the next unattended batch should be ---------------------------------------
# Jas, 2026-08-26: scope a batch, let it run unattended, vet that batch before scoping the
# next -- and *"whatever your decision-making process is, leave it in the handover notes...
# that way, when you pick it up tomorrow, you inherit the trust that the model earned in the
# previous session."*
#
# So the policy is COMPUTED FROM THE LEDGER rather than remembered. Prose in a handover drifts
# (`L-046`); a number derived from recorded verdicts cannot. A new session runs `batch` and
# inherits exactly the trust the evidence supports, with no judgement call to re-make.
#
# THE ASYMMETRY IS THE WHOLE DESIGN. Two rates matter and they are not equally expensive
# (`MECHANICAL-FIXES.md`):
#
#   * a patch I send back        -- costs GPU time. Cheap. Governs GROWTH.
#   * a patch I KEEP that was wrong -- lands bad code wearing my approval. Expensive.
#                                      Governs RESET.
#
# So a poor keep rate only slows growth, while a single escaped defect resets to 1. Being
# wrong about what I approved is the failure this gate exists to prevent, and it should cost
# far more than being slow.
BATCH_START = 3
BATCH_CAP = 12          # beyond this my review degrades and the Pareto stops being coherent
GROW_ABOVE = 0.80       # keep rate that earns a doubling
SHRINK_BELOW = 0.60


def _approves_nothing(v, run_keys, delivered_keys):
    """A KEPT verdict on a run we can SEE failed to deliver. Nothing else.

    Two ways to get this wrong, and finding 36's patch took the second:

    Counting a `kept` on a run that produced no branch overstates how much work was
    approved -- that is the finding, and it is real.

    But excluding EVERY verdict on an undelivered run also drops the `sent-back` rows, which
    are genuine evidence about the model and belong in the denominator. Measured on the live
    ledger: it moved the rate UP, from 7/10 to 8/10, by shrinking the denominator. A fix that
    flatters the number it was written to stop flattering.

    And absence is not evidence. A verdict whose run is not in the ledger at all cannot be
    shown to have delivered nothing, so it is left alone -- the same rule the status board
    lives by: "cannot tell" is not "no".
    """
    key = (v.get("task"), v.get("at"))
    return (v.get("verdict") == KEPT
            and key in run_keys
            and key not in delivered_keys)


def batch_size(vs=None):
    """(n, why). The number of tasks to scope into the next unattended batch."""
    vs = verdicts() if vs is None else vs
    scored = [v for v in vs if v.get("verdict") in (KEPT, SENT_BACK, BLOCKED)]
    escaped = [v for v in vs if v.get("escaped")]
    # An escaped defect resets the BASE to 1, but does not lock the batch there forever.
    # The docstring says "trust is re-earned from one, not resumed" — so the growth logic
    # runs from base 1, and clean batches grow it back up: 1 -> 2 -> 4 -> 8.
    # A hard return here made the escape a permanent latch: the same 10 clean verdicts
    # give 8 without the escape, 1 with it, even after 100 more clean batches.
    _escaped_note = None
    if escaped:
        _escaped_note = (f"{len(escaped)} kept patch(es) later found defective "
                         f"({', '.join(sorted({e.get('task','?') for e in escaped}))}). "
                         "A defect that got past the gate is the failure the gate exists "
                         "to prevent; trust is re-earned from one, not resumed.")
    # Growth must be earned by a batch that actually RAN under this policy. The first seven
    # verdicts on record were retrospective -- me writing down hand reviews from earlier
    # sessions -- and letting those justify a doubling would be inheriting trust that was
    # never tested by the process it is supposed to license.
    batched = [v for v in scored if v.get("batch")]
    if not batched:
        return BATCH_START, (f"no batch has completed under this policy yet ({len(scored)} "
                             f"vetted item(s) on record, all from ad-hoc or retrospective "
                             f"review). Starting at {BATCH_START}; growth is earned by a "
                             "batch that ran unattended and survived vetting, not by history "
                             "that predates the rule.")
    if len(scored) < 5:
        return BATCH_START, (f"only {len(scored)} vetted item(s) on record — too few to infer "
                             f"anything. Starting at {BATCH_START}, which is small enough that "
                             "a bad run costs one review sitting.")
    # Harness failures are excluded from the RATE. The policy exists to calibrate trust in
    # the MODEL, and a run that failed because coder.py dropped its files, shadowed the
    # baseline suite list or flooded it with 9,916 characters of notes says nothing about
    # the model at all. Counting those would shrink the batch as punishment for my own bugs
    # -- and on 2026-08-26 that alone took the rate from 3/6 to 3/10.
    #
    # They are NOT excluded from the record: `pareto` still shows them, and the harness bar
    # being tall is the finding. They are excluded from the one number that governs how much
    # unattended work the model is trusted with.
    # THE OTHER HALF OF FINDING 36. cmd_pareto's rate now excludes verdicts on runs that
    # delivered nothing; this rate had the same defect and is the one that actually decides
    # how much unattended work to dispatch. A `kept` on a run with no branch is not work
    # anybody approved. delivered() is reused rather than restated, for the reason L-114 and
    # L-115 give: the fix is always to delete the copy, never to sync it.
    _rows = runs()
    run_keys = {(r.get("task"), r.get("at")) for r in _rows}
    delivered_keys = {(r.get("task"), r.get("at")) for r in delivered(_rows)}
    batched = [v for v in batched if not _approves_nothing(v, run_keys, delivered_keys)]
    judged = [v for v in batched if v.get("category") != "harness"][-10:]
    if not judged:
        return BATCH_START, ("every recorded failure so far was the harness, not the model. "
                             f"Nothing to judge the model on yet; holding at {BATCH_START}.")
    recent = judged
    # A PATCH I HAD TO REPAIR BEFORE MERGING IS NOT EVIDENCE FOR GROWING THE BATCH.
    #
    # Added 2026-08-31, from a batch where two of six landed only after I deleted a line:
    # one printed its section header twice, one carried an unreachable `return`. Both were
    # correct fixes and both passed their target suite, which could not see either defect.
    # Recording them as clean keeps would have raised the number that decides how much
    # unattended work to trust, on the strength of work that needed a human first.
    #
    # The rate measures "usable without rework", because that is the question the batch size
    # is actually asking. A repaired patch still counts as DELIVERED and still appears in the
    # Pareto -- it is excluded from the rate only.
    kept = sum(1 for v in recent if v.get("verdict") == KEPT and not v.get("repaired"))
    repaired = sum(1 for v in recent if v.get("verdict") == KEPT and v.get("repaired"))
    rate = kept / len(recent)
    # The MOST RECENT batch, not the largest ever run. `max` made it announce "halving 3 -> 1"
    # when the last batch had already been 1, so the message described a step that never
    # happened and the size could never climb back from a historical peak.
    # Scan the SAME set the rate is computed from, not every row ever written. `vs` includes
    # verdicts deliberately excluded from the judgement -- superseded runs, harness failures --
    # and taking `prev` from those let a row that has no say in the rate still set the base
    # the rate multiplies. Measured: five kept runs at batch=2 give 4; add one SUPERSEDED row
    # carrying batch=8 and the same five give 12, the cap. A run that was REPLACED tripled the
    # amount of unvetted work. Found by the env3 probe, round 27 of the targeted round 5.
    prev = next((v["batch"] for v in reversed(judged) if v.get("batch")), BATCH_START)
    if escaped:
        prev = 1  # reset the base, but let growth logic run from here
        # ...FROM HERE MEANS AFTER THE ESCAPE (2026-09-30, test_reviewq's one red case since 09-03).
        # Growth resumed on the verdicts that came BEFORE the defect was found, so 20 clean keeps
        # and then an escape gave 2, not 1. Only what was vetted after the latest escape counts.
        last = max(i for i, v in enumerate(vs) if v.get("escaped"))
        after = {id(v) for v in vs[last + 1:]}
        recent = [v for v in recent if id(v) in after]
        if not recent:
            return 1, (f"{_escaped_note} No batch has been vetted since, so it starts again at 1.")
        kept = sum(1 for v in recent if v.get("verdict") == KEPT and not v.get("repaired"))
        repaired = sum(1 for v in recent if v.get("verdict") == KEPT and v.get("repaired"))
        rate = kept / len(recent)
    if rate >= GROW_ABOVE:
        n = min(prev * 2, BATCH_CAP)
        esc_prefix = (f"{_escaped_note} " if _escaped_note else "")
        return n, (esc_prefix + f"keep rate {kept}/{len(recent)} ({rate:.0%}) over the last {len(recent)} "
                   f"vetted (harness failures excluded"
                   + (f"; {repaired} landed only after repair and do not count" if repaired else "")
                   + f"), at or above {GROW_ABOVE:.0%}. "
                   f"Doubling {prev} -> {n}" + (f" (capped at {BATCH_CAP})" if n == BATCH_CAP else ""))
    if rate < SHRINK_BELOW:
        n = max(prev // 2, 1)
        esc_prefix = (f"{_escaped_note} " if _escaped_note else "")
        return n, (esc_prefix + f"keep rate {kept}/{len(recent)} ({rate:.0%}, harness excluded) below "
                   f"{SHRINK_BELOW:.0%}. "
                   f"Halving {prev} -> {n}. This costs GPU time, not correctness — the gate "
                   "still held.")
    esc_prefix = (f"{_escaped_note} " if _escaped_note else "")
    return prev, (esc_prefix + f"keep rate {kept}/{len(recent)} ({rate:.0%}) is between {SHRINK_BELOW:.0%} "
                  f"and {GROW_ABOVE:.0%}. Holding at {prev}.")


def cmd_batch(a):
    n, why = batch_size()
    print(f"  next unattended batch: {n} task(s)\n")
    for line in why.split(" -- "):
        print(f"  {line}")
    print("\n  Scope each task BEFORE dispatching (satisfy the oracle yourself, `L-107`),")
    print("  then vet the whole batch together so the Pareto has something to see.")
    print("  Record an escaped defect with:  reviewq.py escaped <task> --why '...'")
    return 0


def cmd_escaped(a):
    """Mark a KEPT patch as later found defective. This is the reset trigger.

    Load-bearing since 2026-08-31. With Jas reviewing AFTER the merge rather than before,
    this is the only signal that a defect got past the one gate that remains. Record one
    whenever he reports a bug in merged work -- including when the fix is obvious and quick.
    The count is what tells a future session how far to trust this pipeline, and a defect
    nobody recorded reads exactly like a defect that never happened.
    """
    key = a.branch or a.at or a.task
    hit = [v for v in verdicts() if v.get("verdict") == KEPT and (
        (a.branch and v.get("branch") == a.branch)
        or (a.at and v.get("at") == a.at)
        or (a.task and v.get("task") == a.task))]
    if not hit:
        print(f"no kept verdict for {key} — nothing to mark as escaped", file=sys.stderr)
        return 1
    with VERDICTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": hit[-1].get("at"), "task": a.task, "verdict": KEPT,
                            "branch": hit[-1].get("branch"),
                            "escaped": True, "why": a.why,
                            "reviewed_by": "claude-code"}) + "\n")
    _scope_event({"task": a.task or hit[-1].get("task"), "why": a.why,
                  "should_catch": hit[-1].get("should_catch")}, escaped=True)
    print(f"  {key} marked escaped — next batch resets to 1.")
    return 0


def cmd_list(a):
    p = pending()
    bad = unreadable()
    if bad:
        print(f"  [!] {len(bad)} unreadable ledger line(s) — reported, not skipped:")
        for r in bad[:3]:
            print(f"      {r['_unreadable']}")
    if not p:
        print("  Claude review queue is empty — nothing the local model built is unvetted.")
    else:
        print(f"  {len(p)} item(s) awaiting Claude's review:\n")
        for r in p:
            b = r.get("branch")
            d = diff_noise(b) if b and branch_exists(b) else None
            where = (f"git diff {b}~1..{b}" if branch_exists(b)
                     else ("THE BRANCH WRITE FAILED — --apply was given and did not land; "
                           "nothing to review"
                           if r.get("outcome") in NOTHING_TO_REVIEW
                           else "DRY RUN — no branch, re-run with --apply" if not b
                           else f"branch {b} MISSING"))
            # `.get("task", "?")` returns None when the KEY EXISTS with a null value, which
            # is what every --brief run writes, and `None:6` raises TypeError -- so one
            # brief-driven run made the whole queue listing crash for every other item.
            # A queue that cannot be listed is worse than an empty one: the work is there
            # and invisible. Found 2026-08-27 when my own test runs poisoned the list.
            print(f"  {(r.get('task') or '(brief)'):6} {r.get('at') or '?'}  "
                  f"{r.get('served') or '?'}")
            print(f"         {str(r.get('why') or '')[:96]}")
            print(f"         attempts={r.get('attempts')} tokens={r.get('tokens')} · {where}"
                  + (f"  files_two_dot={d['files_two_dot']} files_base={d['files_base']}"
                     if d else "") + "\n")
    # BRIDGE BRANCHES WAIT HERE TOO (t562). A bridge/<task> branch is an oracle the gates did
    # not freeze (bridge_trigger.freeze_branch refused, or the freeze is off): it is left for
    # Claude and was on no list, so it sat unseen -- bridge/t464 was found by hand 2026-10-01.
    br = subprocess.run(["git", "-C", str(REPO), "for-each-ref", "--format=%(refname:short)",
                         "refs/heads/bridge/"], capture_output=True, text=True, timeout=60)
    # Only branches with commits main lacks: twelve fully-merged ones sat here as "waiting" (2026-10-09).
    waiting = [b for b in br.stdout.split() if b and subprocess.run(
        ["git", "-C", str(REPO), "rev-list", "--count", f"main..{b}"],
        capture_output=True, text=True, timeout=60).stdout.strip() not in ("", "0")]
    if waiting:
        print(f"  {len(waiting)} bridge branch(es) the gates left for Claude (oracle freeze):\n")
        for b in waiting:
            mb = subprocess.run(["git", "-C", str(REPO), "merge-base", "main", b],
                                capture_output=True, text=True, timeout=60)
            anchor = f"{mb.stdout.strip()}.." if (mb.returncode == 0 and mb.stdout.strip()) else "main..."
            d = diff_noise(b)
            print(f"  {b.split('/', 1)[1]:6} git diff {anchor}{b}   "
                  f"files_two_dot={d['files_two_dot']} files_base={d['files_base']}   "
                  f"-> bridge_trigger.freeze_branch "
                  f"or merge by hand\n")
    # DSH BRANCHES WAIT HERE TOO (2026-10-09). The DeepSeek Harness cannot commit to main; it builds
    # on dsh/<topic> in a worktree and stops, the way the Manager's work stops at a coder/* branch.
    # Only branches with commits main lacks are listed -- a merged one is done, not waiting.
    br = subprocess.run(["git", "-C", str(REPO), "for-each-ref", "--format=%(refname:short)",
                         "refs/heads/dsh/"], capture_output=True, text=True, timeout=60)
    dsh = [b for b in br.stdout.split() if b and subprocess.run(
        ["git", "-C", str(REPO), "rev-list", "--count", f"main..{b}"],
        capture_output=True, text=True, timeout=60).stdout.strip() not in ("", "0")]
    if dsh:
        print(f"  {len(dsh)} DeepSeek Harness branch(es) waiting for Claude's merge:\n")
        for b in dsh:
            mb = subprocess.run(["git", "-C", str(REPO), "merge-base", "main", b],
                                capture_output=True, text=True, timeout=60)
            anchor = f"{mb.stdout.strip()}.." if (mb.returncode == 0 and mb.stdout.strip()) else "main..."
            d = diff_noise(b)
            print(f"  {b:28} git diff {anchor}{b}   "
                  f"files_two_dot={d['files_two_dot']} files_base={d['files_base']}   "
                  f"-> vet by running it, then git merge --no-ff {b}\n")
    if a.all:
        v = verdicts()
        print(f"  Already vetted: {len(v)}")
        for x in v[-8:]:
            print(f"    {x.get('task','?'):6} {x.get('verdict','?'):9} "
                  f"{x.get('category') or '-':16} {str(x.get('why') or '')[:60]}")
    return 0


def cmd_verdict(a):
    if a.verdict not in VERDICT_WORDS:
        print(f"verdict must be one of {', '.join(VERDICT_WORDS)}", file=sys.stderr)
        return 2
    if a.verdict not in (KEPT, SUPERSEDED) and not a.why:
        # A rejection with no reason cannot be acted on and cannot be counted.
        print("--why is required unless the verdict is 'kept'", file=sys.stderr)
        return 2
    # A verdict may be recorded against ANY run, not only a delivered one. The queue shows
    # ACCEPTED work because that is what needs vetting -- but a run that FAILED still has a
    # cause, and whether that cause was the model or the harness is the single most useful
    # thing the Pareto can say. On 2026-08-26 it was the harness five times out of six, and
    # a chart that cannot record that would have read as the model being unreliable.
    # WHAT HAD TO BE FIXED, AND WHERE IT SHOULD HAVE BEEN CAUGHT. Jas, 2026-09-14: "track the
    # results of the jobs you check to see the ones you need to fix so we can set up Q16 better
    # upfront." 72 of the first 93 verdicts carried no category, so the Pareto could not say
    # what kind of defect got through. Anything that is not a clean keep must now say what it
    # was and which upfront stage should have stopped it.
    if (a.verdict != KEPT or a.repaired) and a.verdict != SUPERSEDED:
        if not a.category or not a.should_catch:
            print("--category and --should-catch are required unless the verdict is a clean "
                  "'kept' -- that record is how the upfront checks get tuned", file=sys.stderr)
            return 2
    if not a.task and not a.branch and not a.at:
        print("give a task id, --branch or --at", file=sys.stderr)
        return 2
    if a.at:
        which, label = (lambda r: r.get("at") == a.at), a.at
    elif a.branch:
        which, label = (lambda r: r.get("branch") == a.branch), a.branch
    else:
        which, label = (lambda r: r.get("task") == a.task), a.task
    match = [r for r in pending() if which(r)]
    if not match and a.at:
        seen = {(v.get("task"), v.get("at")) for v in verdicts()}
        match = [r for r in runs() if which(r) and (r.get("task"), r.get("at")) not in seen]
    if not match:
        seen = {(v.get("task"), v.get("at")) for v in verdicts()}
        match = sorted([r for r in runs()
                        if which(r) and (r.get("task"), r.get("at")) not in seen],
                       key=lambda r: r.get("at") or "", reverse=True)
    if not match:
        print(f"no unvetted run for {label}", file=sys.stderr)
        return 1
    if a.branch and len(match) > 1:
        # Refuse rather than guess. Two runs on one branch means the later one replaced the
        # earlier, and picking silently would vet a patch nobody looked at.
        print(f"{len(match)} unvetted runs name {a.branch}; vet them by task id",
              file=sys.stderr)
        return 2
    r = match[0]
    row = {"at": r.get("at"), "task": a.task or r.get("task"), "verdict": a.verdict,
           "repaired": a.repaired or None,
           "category": a.category, "should_catch": a.should_catch, "why": a.why,
           "served": r.get("served"), "branch": r.get("branch"),
           "batch": a.batch, "outcome": r.get("outcome"), "reviewed_by": "claude-code"}
    if r.get("branch"):
        d = diff_noise(r.get("branch"))
        row["files_two_dot"] = d["files_two_dot"]
        row["files_base"] = d["files_base"]
    with VERDICTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    _scope_event(row)
    nxt = {KEPT: "cleared to merge — Claude is the gate (2026-08-31)",
           SENT_BACK: "sent back to the local model",
           BLOCKED: "blocked, needs Jas",
           SUPERSEDED: "superseded by a later run; not counted in the Pareto"}[a.verdict]
    # `label`, not a.task: a --brief run has no task and this printed "None: kept",
    # which names nothing. A confirmation that cannot say WHAT it just vetted is how
    # you record six verdicts and cannot tell afterwards which patch each one was about.
    print(f"  {label}: {a.verdict} — {nxt}")
    return 0


def cmd_upfront(a):
    """Which upfront stage keeps letting defects through. The number to tune Q16 against."""
    from collections import Counter
    rows = [v for v in verdicts() if v.get("verdict") != SUPERSEDED]
    fixed = [v for v in rows if v.get("verdict") != KEPT or v.get("repaired")]
    tagged = [v for v in fixed if v.get("should_catch")]
    print(f"  {len(rows)} reviewed, {len(fixed)} needed fixing or were refused, "
          f"{len(tagged)} of those say where they should have been caught")
    for stage, n in Counter(v["should_catch"] for v in tagged).most_common():
        print(f"    {n:3d}  {stage:10} {_render(UPFRONT.get(stage, ''))}")
    if len(tagged) < len(fixed):
        print(f"  {len(fixed) - len(tagged)} older verdict(s) predate --should-catch and are not counted")
    return 0


def cmd_pareto(a):
    """Worst-first, because the point is to attack the top of the list, not admire the list."""
    vs = [v for v in verdicts() if v.get("verdict") in (SENT_BACK, BLOCKED)]
    rs = runs()
    print("Why work was sent back (Claude's verdicts)")
    if not vs:
        print("  nothing sent back yet")
    else:
        for cat, n in Counter(v.get("category") or "uncategorised" for v in vs).most_common():
            bar = "#" * min(n * 3, 40)
            print(f"  {cat:18} {n:3}  {bar}  {CATEGORIES.get(cat,'')}")
    blamed = [v for v in verdicts() if v.get("category")]
    if blamed:
        h = sum(1 for v in blamed if v.get("category") == "harness")
        print(f"\nWhose fault, where a cause was recorded ({len(blamed)} judged)")
        print(f"  harness (mine)     {h:3}  {'#' * min(h * 3, 40)}")
        print(f"  model              {len(blamed)-h:3}  {'#' * min((len(blamed)-h) * 3, 40)}")
        print("  A tall harness bar means the measuring apparatus is the bottleneck, and")
        print("  swapping models would change nothing.")
    print("\nHow runs ended (the model's own outcomes)")
    for out, n in Counter(r.get("outcome", "?") for r in rs).most_common():
        print(f"  {out:18} {n:3}  {'#' * min(n * 3, 40)}")
    print("\nWhy individual attempts failed (inside runs)")
    tr = Counter(t.get("outcome") for r in rs for t in (r.get("trail") or [])
                 if t.get("outcome") not in ("ACCEPTED",))
    for out, n in tr.most_common(8):
        print(f"  {out or '?':18} {n:3}  {'#' * min(n * 3, 40)}")
    scored = [v for v in verdicts() if v.get("verdict") != SUPERSEDED]
    # A kept verdict on a run that delivered nothing is not an approval of work.
    # The cause breakdown above still sees it; only the RATE excludes it.
    _rows = runs()
    _run_keys = {(r.get("task"), r.get("at")) for r in _rows}
    _delivered_keys = {(r.get("task"), r.get("at")) for r in delivered(_rows)}
    scored = [v for v in scored if not _approves_nothing(v, _run_keys, _delivered_keys)]
    kept = sum(1 for v in scored if v.get("verdict") == KEPT)
    tot = len(scored)
    if tot:
        print(f"\n  vetted {tot}, kept {kept} ({100*kept//tot}%)")
    print("\n  A category with a tall bar is a systemic problem, not a run that went badly.\n"
          "  Attack the top row; the tail is noise until the head is gone.")
    return 0


def self_test():
    print("reviewq self-test\n")
    bad = 0

    def chk(name, got, want):
        nonlocal bad
        ok = got == want
        bad += not ok
        print(f"  [{'ok' if ok else 'FAIL'}]   {name}: {got!r} (want {want!r})")

    R = [{"at": "1", "task": "t1", "outcome": "ACCEPTED", "branch": "coder/t1"},
         {"at": "2", "task": "t2", "outcome": "NO-OP"},
         {"at": "3", "task": "t3", "outcome": "ACCEPTED-DRYRUN"}]
    chk("only ACCEPTED* runs are reviewable", [r["task"] for r in delivered(R)], ["t1", "t3"])

    # ACCEPTED-NOT-WRITTEN is finished but delivered nothing. It reached here by startswith
    # the day coder.py gained it (L-130) and was shown as "DRY RUN -- re-run with --apply"
    # for a run where --apply HAD been given and the branch write had failed.
    R3 = R + [{"at": "4", "task": "t4", "outcome": "ACCEPTED-NOT-WRITTEN", "branch": None}]
    chk("a run whose branch write FAILED is not reviewable",
        "t4" in [r["task"] for r in delivered(R3)], False)
    chk("  and the ones that did deliver still are",
        [r["task"] for r in delivered(R3)], ["t1", "t3"])
    chk("a failed run is NOT queued for review",
        "t2" in [r["task"] for r in pending(R, [])], False)
    chk("an unvetted run is pending",
        [r["task"] for r in pending(R, [])], ["t3", "t1"])
    chk("a vetted run leaves the queue",
        [r["task"] for r in pending(R, [{"task": "t1", "at": "1"}])], ["t3"])
    # The refusal that matters: matching on task alone would hide a SECOND run of the same task.
    R2 = R + [{"at": "9", "task": "t1", "outcome": "ACCEPTED", "branch": "coder/t1"}]
    chk("a re-run of an already-vetted task is queued again",
        "t1" in [r["task"] for r in pending(R2, [{"task": "t1", "at": "1"}])], True)
    print(f"\n  {5 - bad} passed, {bad} failed")
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    v = sub.add_parser("verdict")
    # A --brief run has NO task id, and every one of them carries task=None. Matching on
    # task alone made the six runs of the 2026-08-31 batch indistinguishable: each verdict
    # would have attached to whichever unvetted run sorted first, so the REASON would be
    # recorded against the wrong patch. A wrong reason is worse than no reason -- it is what
    # the Pareto counts. The branch name is unique per delivered run, so it identifies one.
    v.add_argument("task", nargs="?", default=None,
                   help="task id; omit and use --branch for a --brief run")
    v.add_argument("--branch", default=None,
                   help="identify the run by its review branch instead of a task id")
    # A run that FAILED has no task AND no branch, and those are the runs whose cause the
    # Pareto most needs -- reviewq's own docstring says so. `at` is the only field every run
    # has and the key the verdict ledger already uses, so it identifies any of them.
    v.add_argument("--at", default=None,
                   help="identify the run by its exact 'at' timestamp; the only handle a "
                        "failed run has")
    v.add_argument("verdict", choices=VERDICT_WORDS)
    v.add_argument("--why", default="")
    v.add_argument("--repaired", default=None,
                   help="what you had to fix before it could merge. A repaired patch is "
                        "excluded from the keep rate, because that rate governs how much "
                        "unattended work the model is trusted with")
    v.add_argument("--category", choices=sorted(CATEGORIES))
    v.add_argument("--should-catch", default=None, choices=sorted(UPFRONT),
                   help="the upfront stage that should have stopped this before production")
    v.add_argument("--batch", type=int, default=None,
                   help="size of the unattended batch this item came from; growth is only "
                        "earned by items carrying this")
    v.set_defaults(fn=cmd_verdict)
    sub.add_parser("pareto").set_defaults(fn=cmd_pareto)
    sub.add_parser("upfront").set_defaults(fn=cmd_upfront)
    sub.add_parser("batch").set_defaults(fn=cmd_batch)
    e = sub.add_parser("escaped")
    # Same three handles as `verdict`. A --brief run has no task id, so without these the
    # one signal that a defect reached main could not be recorded against most of the work
    # this pipeline now produces.
    e.add_argument("task", nargs="?", default=None)
    e.add_argument("--branch", default=None)
    e.add_argument("--at", default=None)
    e.add_argument("--why", required=True)
    e.set_defaults(fn=cmd_escaped)
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    return (getattr(a, "fn", None) or cmd_list)(a)


if __name__ == "__main__":
    sys.exit(main())
