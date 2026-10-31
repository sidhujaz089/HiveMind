#!/usr/bin/env python3
"""Break the code on purpose, and require the suites to notice.

    python3 scripts/mutate.py            run every mutation
    python3 scripts/mutate.py --list     what it will break, and what should catch it
    python3 scripts/mutate.py --only sweep

WHY (docs/CHECKS.md, backlog item 2)
------------------------------------
The standing rule here is that a check is not trusted until it has been observed FAILING.
That has been done by hand, once, at the moment each check was written -- which proves it
failed that day and says nothing about today. Checks rot: a refactor moves a constant, a
scope narrows, a branch stops being reached, and the check goes on reporting PASS.

**This repository's own history is the argument.** Roughly twenty-one defects have been found
in checking code against two in the code being checked, including a mutation test that did
not mutate (`L-080`), a census structurally unable to see its own failure, a search reporting
`ok` having scanned no files, and -- this week -- a check that was reported as built, closed
as a task, and did not exist.

WHAT IT DOES
------------
For each mutation: copy the repo to a temp directory, apply one small edit that a real bug
would look like, run the suite that is supposed to catch it, and require it to FAIL. Then
throw the copy away. **Nothing here ever writes to the working tree.**

A mutation that is NOT caught is the finding. It means the guard has a hole, and the output
says which guard and what got past it.
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# (name, file, find, replace, suite, what a real bug of this shape would be)
MUTATIONS = [
    ("vacuous-clearance-loses-its-guard",
     "scripts/check_apps.py",
     "if not apks:",
     "if False:  # mutation",
     "scripts/check_vacuous.py",
     "A site cleared in the vacuous registry must PROVE it, not assert it. Remove the guard "
     "its demonstration names and check_vacuous.py must go red. Without this, a clearance is "
     "prose again -- and prose is what let check_ledger.py:154 through three reviewers."),

    ("stale-check-blind-to-stray-processes", "scripts/check_stale_services.py",
     '        if changed_ago < int(etimes):',
     '        if False:',
     "scripts/tests/test_stale_services.py",
     "seeing only systemd units, so a process started by hand or left behind by a session can "
     "run stale code indefinitely while every check reports green. An orphaned "
     "context_watch.py ran for FOUR DAYS this way, executing a code path that had since been "
     "retired and could no longer refuse to start"),

    ("secret-scan-passes-on-a-file-it-could-not-read", "scripts/check_workspace.py",
     '        elif unreadable:',
     '        elif False:',
     "scripts/tests/test_check_workspace.py",
     "reporting 'No secret pattern in tracked files' when git grep could not READ a tracked "
     "file. git grep exits 1 both for no-match and for Permission denied, so a real "
     "credential in an unreadable file was reported clean -- the examined-nothing false pass, "
     "inside the one check whose silence is trusted most"),

    ("probe-gate-verdict-never-reaches-the-exit-code", "projects/local-inference/probe.py",
     '    return 0 if ok else 4',
     '    return 0',
     "scripts/tests/test_probe_overnight.py",
     "computing the env3 gate verdict, printing it, logging it, and then returning 0 anyway "
     "-- so a FAILED gate fails nothing and every caller trusting the exit status is told a "
     "run was clean even when it broke the mutation registry, the single thing the gate "
     "exists to prevent"),

    ("overnight-leaves-fixes-only-in-the-sandbox", "projects/local-inference/probe_overnight.py",
     '    if names:\n        r = g("bundle", "create", str(bundle), *names)',
     '    if False:\n        r = g("bundle", "create", str(bundle), *names)',
     "scripts/tests/test_probe_overnight.py",
     "keeping the run's only copy of its fixes inside env3. A branch there survives a reset "
     "and a restart, but env3's contract is that it is DISPOSABLE -- rebuilding a sandbox is "
     "routine, and it would discard every fix the run produced. It cannot be pushed to the "
     "live repo either: the pre-push hook correctly refuses unreviewed output"),

    ("coder-clobbers-a-branch-awaiting-review", "projects/local-inference/coder.py",
     '    if branch_is_pending(branch):',
     '    if False:',
     "scripts/tests/test_coder_branch_delivery.py",
     "deleting a review branch unconditionally, so a second run on the same label destroys "
     "the first run's work while a human is still deciding on it. For --brief the label is "
     "always the literal 'brief', so any two runs collide"),

    ("env3-gate-ignores-skipped-mutations", "projects/local-inference/probe.py",
     '    if skipped:',
     '    if False:',
     "scripts/tests/test_probe_overnight.py",
     "reporting the env3 gate OK while mutations were SKIPPED rather than caught. An anchor "
     "stops matching precisely when the model patches the code that mutation guards, so the "
     "property stops being proven at the moment it most needs proving -- the quiet version "
     "of deleting a mutation, which this gate already refuses"),

    ("overnight-drops-the-models-fixes-from-the-tree", "projects/local-inference/probe_overnight.py",
     '    reb = g("rebase", "origin/main")',
     '    reb = g("reset", "--hard", "origin/main")',
     "scripts/tests/test_probe_overnight.py",
     "resetting env3 instead of rebasing, so the model's fixes leave the tree it probes. It "
     "then re-encounters defects it already repaired -- and since the brief tells it to "
     "assume nothing on the known list is fixed and that finding one still present IS a "
     "finding, the reset manufactures an endless supply of false-but-validated findings"),

    ("overnight-a-dead-pass-ends-the-whole-run", "projects/local-inference/probe_overnight.py",
     '            fails += 1',
     '            break',
     "scripts/tests/test_probe_overnight.py",
     "letting one failed pass end an 87-hour run. Nobody is watching until Monday, so a "
     "death at hour six spends the remaining 80 hours idle and the first anyone knows is an "
     "empty log"),

    ("overnight-fills-the-disk-without-checking", "projects/local-inference/probe_overnight.py",
     '        if free_gb < 40:',
     '        if False:',
     "scripts/tests/test_probe_overnight.py",
     "starting passes with no disk headroom. One fix here once produced 279 repo copies and "
     "55 GB of /tmp (L-128); a full disk does not merely lose a pass, it takes down the "
     "journal that would explain what happened"),

    ("stale-services-passes-when-nothing-is-running", "scripts/check_stale_services.py",
     '        return 2\n    if not svcs:',
     '        return 0\n    if not svcs:',
     "scripts/tests/test_stale_services.py",
     "returning success when systemctl could not be queried at all. An empty answer then "
     "reads as 'nothing stale', which is the same verdict a healthy machine gets"),

    ("status-md-check-passes-over-an-empty-set", "scripts/check_workspace.py",
     '        elif not project_dirs:',
     '        elif False:',
     "scripts/tests/test_check_workspace.py",
     "letting 'Every project has a STATUS.md' pass when projects/ is absent or empty. A "
     "universal claim over an empty set is vacuously true and operationally a lie, and the "
     "only hint was the number inside the green line"),

    ("apps-index-check-passes-having-examined-nothing", "scripts/check_apps.py",
     '            elif not examined:',
     '            elif False:',
     "scripts/tests/test_check_apps.py",
     "reporting PASS -- 'the index carries release notes to the phone' -- for an index with "
     "no packages at all, because the loop never ran and an empty `silent` list read as "
     "success. The check exists because Jas twice saw no release notes on his phone, and it "
     "is the fourth time the catalogue has been green next to a phone that disagreed"),

    ("coder-note-calls-a-failed-write-a-dry-run", "projects/local-inference/coder.py",
     '                        if outcome == "ACCEPTED-NOT-WRITTEN"',
     '                        if False',
     "scripts/tests/test_coder_branch_delivery.py",
     "inferring \"dry run\" from the absence of a branch, so a run where --apply was given "
     "and the branch write FAILED is described to Jas as a dry run in the task note he reads "
     "to decide whether to merge. Third consumer of the same implicit outcome contract"),

    ("reviewq-queues-a-run-that-delivered-nothing", "scripts/reviewq.py",
     '            and r.get("outcome") not in NOTHING_TO_REVIEW]',
     '            ]',
     "scripts/tests/test_reviewq.py",
     "letting ACCEPTED-NOT-WRITTEN through the startswith(\"ACCEPTED\") filter, so a run "
     "whose branch write FAILED is queued as awaiting review and displayed as 'DRY RUN -- "
     "re-run with --apply' to someone who passed --apply and whose write failed. Producer "
     "and consumer drifting: coder.py gained the outcome and reviewq.py was not told"),

    ("sweep-blind-to-a-deleted-tracked-file", "scripts/sweep.py",
     '        if (REPO / s).exists():\n            keep.append(s)\n            continue',
     '        if True:\n            keep.append(s) if (REPO / s).exists() else None\n            continue',
     "scripts/tests/test_sweep.py",
     "filtering the swept paths to what EXISTS, so a tracked file that has been DELETED "
     "leaves the pathspec and sweep reports '0 path(s)' while git reports ' D'. Deletion is "
     "the most dangerous state an audit trail can be in and it was the one state the guard "
     "could not see -- introduced by the fix for L-131 and found six hours later"),

    ("sweep-recomputes-the-path-set-mid-commit", "scripts/sweep.py",
     '            ["git", "-C", str(REPO), "ls-files", "--with-tree=HEAD", "--", s],',
     '            ["git", "-C", str(REPO), "ls-files", "--", s],',
     "scripts/tests/test_sweep.py",
     "reading the INDEX rather than HEAD, so a path vanishes from the swept set the instant "
     "its deletion is staged. cmd_commit then diffs a different set than it added and prints "
     "'Nothing staged (all ignored?)' without committing the deletion"),

    ("sweep-resolves-the-symlink-it-is-about-to-delete", "scripts/sweep.py",
     '    return str(Path(os.path.abspath(path)).relative_to(REPO)).replace("\\\\", "/")',
     '    return str(Path(path).resolve().relative_to(REPO)).replace("\\\\", "/")',
     "scripts/tests/test_sweep.py",
     "following symlinks when asking where a file lives, so is_committed() hashes the TARGET "
     "while cmd_done() unlinks the LINK. An uncommitted symlink is then deleted with the "
     "message 'its committed copy remains in history', about a file git never held -- a route "
     "around the one guard standing between the inbox and the 2026-08-12 incident"),

    ("sweep-cannot-see-its-own-receipt", "scripts/sweep.py",
     '         "workspace/receipts.jsonl"]',
     '         ]',
     "scripts/tests/test_sweep.py",
     "dropping receipts.jsonl from the swept set, so `--receipt` writes a row and the very "
     "next sweep reports 'Uncommitted in the channels -- 0 path(s)' while listing that "
     "receipt below it, and --commit never picks it up. The receipt is the ONLY way Chat can "
     "tell 'my manifest applied' from 'it was never moved out of the inbox', and an "
     "uncommitted audit trail survives exactly until the next checkout"),

    ("coder-ignores-a-branch-that-was-never-written", "projects/local-inference/coder.py",
     '            if not ok:\n                # `ok` USED TO BE DISCARDED.',
     '            if False:\n                # `ok` USED TO BE DISCARDED.',
     "scripts/tests/test_coder_branch_delivery.py",
     "throwing away write_branch's ok, so a run whose branch was never created still records "
     "ACCEPTED with a branch name and exits 0. Every caller that trusts returncode == 0 -- "
     "reviewq.py and any human reading the ledger -- then cannot tell a delivered change "
     "from one that vanished"),

    ("coder-leaves-the-half-made-review-branch", "projects/local-inference/coder.py",
     '        sh("git", "-C", str(REPO), "branch", "-D", branch)\n        return False, branch, f"could not create the worktree: {r.stderr.strip()[:200]}"',
     '        return False, branch, f"could not create the worktree: {r.stderr.strip()[:200]}"',
     "scripts/tests/test_coder_branch_delivery.py",
     "leaving the ref that `git worktree add -b` creates before it fails. The branch then "
     "exists, carries none of the change, and looks exactly like work waiting for review -- "
     "the state abandon()'s own comment says is worse than no branch at all"),

    ("coder-sandboxes-can-nest", "projects/local-inference/coder.py",
     '    if any(str(REPO).startswith(os.path.join(t, "coder-")) for t in (_tempfile.gettempdir(), "/tmp")):',
     '    if False:',
     "scripts/tests/test_coder_guards.py",
     "letting a sandbox be built from inside a sandbox. Each generation copies the whole "
     "repository, and the copy's own tests spawn coder.py again -- 279 copies and 55 GB the "
     "first time, then 80 more after a fix that lived in the very guard a mutation removes. "
     "This check cannot be mutated away by disabling something else: it asks where the "
     "process is running from"),

    ("retired-watcher-can-be-armed-again", "scripts/context_watch.py",
     '    if a.watch and not a.allow_retired_watch:',
     '    if False:',
     "scripts/tests/test_context_watch.py",
     "letting the retired blocking watcher start again. Both of its forms failed SILENTLY -- "
     "nohup wrote the banner into a file nobody opens, and the harness background task was "
     "killed with exit 144 after a session had reported it armed. A guard believed to be "
     "armed is worse than none, so the retired path must refuse for itself"),

    ("session-start-recommends-the-retired-watcher", "scripts/session_start.py",
     '                print(_c("90", "  wind-down guard: UserPromptSubmit hook, active. Nothing to arm."))',
     '                print(_c("36", "    python3 scripts/context_watch.py --watch --every 300"))',
     "scripts/tests/test_context_watch.py",
     "the exact defect this fix exists for: L-122 landed in settings.json, LESSONS.md and "
     "PROJECT-INDEX.md but NOT in session_start.py, the one file every session reads first. "
     "It kept recommending the retired command for hours and the next session obeyed it"),

    ("coder-builds-a-sandbox-before-refusing", "projects/local-inference/coder.py",
     '    if target and not (REPO / target).is_file():',
     '    if target and not (sandbox() / target).is_file():',
     "scripts/tests/test_coder_guards.py",
     "copying the whole repository before deciding to refuse. Every refusal then abandons a "
     "sandbox, and since the guard's own test spawns coder.py, a suite running inside a "
     "sandbox spawns that sandbox's coder.py and builds another -- 279 copies and 55 GB of "
     "/tmp the first time this shipped"),

    ("context-hook-speaks-every-turn", "scripts/context_watch.py",
     '        elif pct >= notice:',
     '        elif True:',
     "scripts/tests/test_context_watch.py",
     "warning on every single prompt regardless of occupancy. A guard that speaks constantly "
     "is one that gets tuned out, and then it is not there at the one moment it matters"),

    ("context-hook-can-block-a-prompt", "scripts/context_watch.py",
     '    except Exception as e:\n        # a broken guard must never cost Jas a prompt\n'
     '        print(f"  NOTE: context guard failed ({e}) -- no reading this turn", file=sys.stderr)',
     '    except Exception as e:\n        raise',
     "scripts/tests/test_context_watch.py",
     "letting the guard raise out of a UserPromptSubmit hook. It runs before every message, "
     "so a failure here costs Jas the ability to type -- a worse outcome than the compaction "
     "it exists to prevent"),

    ("backup-overwrites-an-existing-bundle", "scripts/corpus_routine.py",
     '    stem, n = dest, 2',
     '    return dest, ""   # reuse the name',
     "scripts/tests/test_corpus_routine.py",
     "reusing the dated bundle name when something is already there. That is how a VERIFIED "
     "478-file backup became the destination of a fresh copy and was truncated to 177 when "
     "the mount dropped -- the routine must never write over existing data on the NAS"),

    ("backup-manifest-checked-at-the-wrong-level", "scripts/corpus_routine.py",
     '    return (d / "MANIFEST.sha256").is_file() or \\',
     '    return (d / "MANIFEST.sha256").is_file() and \\',
     "scripts/tests/test_corpus_routine.py",
     "looking for the manifest only at the top level. It lives inside the bundle's inner "
     "directory, so a good backup reads as 'no backup here' and becomes a target to "
     "overwrite -- the precise mistake that destroyed one"),

    ("batch-grows-from-an-excluded-verdict", "scripts/reviewq.py",
     '    prev = next((v["batch"] for v in reversed(judged) if v.get("batch")), BATCH_START)',
     '    prev = next((v["batch"] for v in reversed(vs) if v.get("batch")), BATCH_START)',
     "scripts/tests/test_reviewq.py",
     "taking the previous batch from every verdict ever written, including the SUPERSEDED and "
     "harness rows deliberately excluded from the keep rate. A run that was REPLACED then sets "
     "the base the rate multiplies -- measured at 4 -> 12, tripling unattended work on the "
     "strength of a verdict that has no say in the judgement"),

    ("coder-runs-with-a-target-that-does-not-exist", "projects/local-inference/coder.py",
     '    if target and not (REPO / target).is_file():',
     '    if False:',
     "scripts/tests/test_coder_guards.py",
     "starting a run whose named target suite is absent. The suite list is a GLOB, so a "
     "missing target is never in `failed`, nothing forces it into new_failures, and the run "
     "is ACCEPTED while the note claims the target suite is green -- about a suite that was "
     "never executed once"),

    ("context-guard-ignores-cached-tokens", "scripts/context_watch.py",
     '        latest = (u.get("input_tokens", 0)',
     '        latest = (0 * u.get("input_tokens", 0)',
     "scripts/tests/test_context_watch.py",
     "counting only the uncached tokens. Nearly the whole window is cache_read on a long "
     "session, so occupancy reads as a few thousand when it is near a million and the guard "
     "NEVER fires -- the exact silent compaction it exists to prevent"),

    ("context-guard-reports-the-peak", "scripts/context_watch.py",
     '        latest = (u.get("input_tokens", 0)',
     '        latest = max(latest, u.get("input_tokens", 0)',
     "scripts/tests/test_context_watch.py",
     "reporting the highest occupancy ever seen instead of the current one. After a "
     "compaction the guard would stay pinned at 'full' forever, so every later session "
     "is told to wind down immediately and learns to ignore it"),

    ("deployed-roster-shrinks-to-defaults-only", "scripts/check_deployed.py",
     '    for tool in sorted(declared - set(served)):',
     '    for tool in sorted(set(src) - set(served)):',
     "scripts/tests/test_check_deployed.py",
     "comparing the roster against the defaults-only set again -- three of the ten tools the "
     "server declares. A tool with no default argument could stop being served and the check "
     "would print PASS, which is the exact staleness this file exists to catch"),

    ("jas-reply-answered-by-a-model-run", "scripts/jas_replies.py",
     '    if any(a in ANSWERERS for _, a, _ in ns[last_jas + 1:]):',
     '    if any(True for _, a, _ in ns[last_jas + 1:]):',
     "scripts/tests/test_jas_replies.py",
     "treating ANY later note as a reply to Jas, so a local-model run clears his question. "
     "The model never read it. This silently empties the check exactly where it matters, "
     "because a task attracts model runs precisely while it sits waiting for him"),

    ("jas-reply-ordered-by-date-not-position", "scripts/jas_replies.py",
     '    last_jas = max((i for i, n in enumerate(ns) if n[1] == HUMAN), default=None)',
     '    last_jas = 0 if any(n[1] == HUMAN for n in ns) else None',
     "scripts/tests/test_jas_replies.py",
     "anchoring on his FIRST note rather than his latest, so an old answered question keeps "
     "the task flagged while today's real one is invisible behind it"),

    ("graph-invents-a-self-edge", "scripts/graph.py",
     '    if src and dst and src != dst:',
     '    if True:',
     "scripts/tests/test_graph.py",
     "letting a node link to itself and empty edges through -- a lesson citing its own number "
     "becomes a relation, and the graph starts asserting things nobody declared, which is the "
     "one property that distinguishes a parser from an extractor (D-32)"),

    ("graph-stale-edges-go-unreported", "scripts/graph.py",
     '            if not (REPO / t).exists():',
     '            if False:',
     "scripts/tests/test_graph.py",
     "never reporting an edge whose target has moved. A parser cannot invent an edge but it "
     "can keep a dead one, and that is this design's only honest failure mode"),

    ("memory-embeds-queries-without-the-instruction", "scripts/memory.py",
     '    return [QUERY_INSTRUCTION + t for t in texts] if is_query else list(texts)',
     '    return list(texts)',
     "scripts/tests/test_memory.py",
     "embedding a query as raw text. Qwen3-Embedding is asymmetric and without the "
     "instruction prefix it barely discriminates -- measured, the query 'indentation' "
     "ranked a record about prompt injection above the record about whitespace"),

    ("memory-vector-failure-takes-down-recall", "scripts/memory.py",
     '    if not vec:\n        return kw[:limit]',
     '    if not vec:\n        return []',
     "scripts/tests/test_memory.py",
     "letting an unavailable embedding model empty the results instead of falling back to "
     "keyword. Degrading to worse-but-working beats failing"),

    ("deployed-check-skips-an-undeployed-tool", "scripts/check_deployed.py",
     '    for tool in sorted(declared - set(served)):',
     '    for tool in sorted(set()):',
     "scripts/tests/test_check_deployed.py",
     "silently skipping a tool the source declares and the bridge does not serve -- exactly "
     "the fix-on-disk-process-from-yesterday failure this checker was written for, and it "
     "would have passed the addition of recall and why without a restart"),

    ("stale-check-ignores-sibling-modules", "scripts/check_stale_services.py",
     '        cand = script.parent / f"{name}.py"',
     '        cand = script.parent / "nope.py"',
     "scripts/tests/test_stale_services.py",
     "checking only the service's entry point for staleness -- feedback.py imports "
     "taskstore.py, and a fix in the sibling is exactly as invisible to a process that "
     "started before it"),

    ("reviewq-grows-from-the-largest-batch-ever", "scripts/reviewq.py",
     '    prev = next((v["batch"] for v in reversed(judged) if v.get("batch")), BATCH_START)',
     '    prev = max((v.get("batch") or 0) for v in judged) or BATCH_START',
     "scripts/tests/test_reviewq.py",
     "sizing the next batch from the largest batch ever run rather than the most recent -- "
     "the size can never climb back from a historical peak and the message describes a "
     "halving that never happened"),

    ("reviewq-escaped-defect-does-not-reset-trust", "scripts/reviewq.py",
     '    if escaped:',
     '    if False:',
     "scripts/tests/test_reviewq.py",
     "letting a KEPT patch that was later found defective keep growing the batch size -- the "
     "one failure the review gate exists to prevent stops costing anything, and the model is "
     "trusted with MORE unattended work immediately after it got something past me"),

    ("reviewq-grows-on-retrospective-history", "scripts/reviewq.py",
     '    batched = [v for v in scored if v.get("batch")]',
     '    batched = list(scored)',
     "scripts/tests/test_reviewq.py",
     "counting ad-hoc and retrospective reviews as completed batches -- trust gets inherited "
     "from history that predates the policy and was never tested by it"),

    ("reviewq-hides-a-rerun-behind-an-old-verdict", "scripts/reviewq.py",
     '    seen = {(v.get("task"), v.get("at")) for v in (vs if vs is not None else verdicts())}',
     '    seen = {v.get("task") for v in (vs if vs is not None else verdicts())}',
     "scripts/tests/test_reviewq.py",
     "keying the queue on task alone -- a second, unreviewed run of an already-vetted task "
     "never appears, so the queue reports empty while finished work sits unvetted"),

    ("reviewq-drops-unreadable-ledger-lines", "scripts/reviewq.py",
     '            out.append({"_unreadable": line})',
     '            continue',
     "scripts/tests/test_reviewq.py",
     "silently skipping a malformed ledger row instead of reporting it -- the queue then "
     "under-reports and says empty when it is not, the same defect t146 fixed in receipts"),

    ("selfmatch-blind-to-its-own-shape", "scripts/selfmatch.py",
     '        if hit:',
     '        if False:',
     "scripts/tests/test_selfmatch.py",
     "the self-match detector never reporting a hit -- every watcher loop that greps for a "
     "string in its own command line passes review and then spins forever (L-105, L-116)"),

    ("selfmatch-approves-a-runtime-pattern", "scripts/selfmatch.py",
     '        if VAR.search(pat):',
     '        if False:',
     "scripts/tests/test_selfmatch.py",
     "treating `pgrep -f \"$NAME\"` as checkable instead of UNKNOWN -- a pattern built at "
     "runtime gets silently approved, which is the 'cannot tell reported as fine' failure "
     "gpu_gate.py was written to avoid"),

    ("coder-review-command-misreads-a-moved-main", "projects/local-inference/coder.py",
     'f"    Review: git diff {branch}~1..{branch}\\n"',
     'f"    Review: git diff main..{branch}\\n"',
     "scripts/tests/test_coder_guards.py",
     "telling the reviewer to diff against main -- once main advances past the branch base, "
     "every newer commit renders as a deletion by the model, and merging the branch really "
     "would revert them"),

    ("coder-parse-counts-braces-in-strings", "scripts/jsonx.py",
     # coder.parse() is on jsonx since 2026-10-01 (t259): the string-awareness lives here now.
     '                elif in_string:\n                    if ch == ',
     '                elif False:\n                    if ch == ',
     "scripts/tests/test_coder_parse_jsonx.py",
     "counting braces inside string values, so a patch whose `new` or `why` contains a "
     "stray `}` -- which is most code -- ends the JSON object early and valid output is "
     "reported UNPARSEABLE. Found by the env3 probe"),

    ("coder-claims-a-revert-without-doing-it", "projects/local-inference/coder.py",
     '    restored = []\n    for rel, text in baseline.items():',
     '    restored = []\n    for rel, text in []:',
     "scripts/tests/test_coder_guards.py",
     "telling the model 'That change has been REVERTED' without reverting -- the next "
     "attempt anchors against the listing it was shown while the file on disk has moved, so "
     "a correct anchor is reported not-found. The REFUSED bar on the Pareto"),

    ("coder-secret-guard-blocks-source-files", "projects/local-inference/coder.py",
     '        if SECRET_WORD.match(name) and not name.lower().endswith(CODE_EXT):',
     '        if SECRET_WORD.match(name):',
     "scripts/tests/test_coder_guards.py",
     "refusing any file whose name looks secret-ish even when it is source code -- Token.kt "
     "and tokenise.py were both blocked. A guard that blocks legitimate work gets worked "
     "around, which is how a guard stops guarding"),

    ("coder-diff-hides-files-outside-the-extension-list", "projects/local-inference/coder.py",
     '    candidates |= {root / rel for rel in named if (root / rel).is_file()}',
     '    candidates |= set()',
     "scripts/tests/test_coder_guards.py",
     "showing the reviewer a diff that omits files outside SOURCE_EXT. The verdict says a "
     "change was made and the evidence shown for it is nothing, so the change is approved "
     "unread. Fourth appearance of the extension-list bug, in the half called display-only"),

    ("coder-noop-decided-by-an-extension-list", "projects/local-inference/coder.py",
     '    if ops and touched(root, baseline, ops):\n        return True\n    return bool(diff_of(root, baseline, ops).strip())',
     '    return bool(diff_of(root, baseline).strip())',
     "scripts/tests/test_coder_guards.py",
     "putting an extension allow-list back in charge of whether a change is visible. Third "
     "appearance of one bug: L-114 was the baseline narrower than diff_of's list, the probe "
     "found .toml missing, widening to 24 extensions moved the boundary, and ten more were "
     "still invisible at round 64"),

    ("coder-note-claims-a-suite-that-was-never-named", "projects/local-inference/coder.py",
     '                     if target else',
     '                     if True else',
     "scripts/tests/test_coder_guards.py",
     "telling Jas 'The target suite is green' on a run that named no suite. The terminal "
     "says acceptance is WEAKER and the note he actually reads says it is stronger -- a "
     "false claim in the one channel written for him. Found by the env3 probe"),

    ("coder-no-python-syntax-oracle", "projects/local-inference/coder.py",
     '            ok_py, py_err = py_syntax(root, obj["ops"])',
     '            ok_py, py_err = (True, "")',
     "scripts/tests/test_coder_guards.py",
     "removing the Python syntax check, so a patch that breaks indentation reaches the "
     "suites and returns an import traceback three frames deep instead of a line number "
     "and a reason. Line-range edits make the model supply indentation, so this is the "
     "failure that shape invites"),

    ("coder-excerpt-cap-too-small-for-the-context", "projects/local-inference/coder.py",
     'EXCERPT_CAP = 210_000',
     'EXCERPT_CAP = 14_000',
     "scripts/tests/test_coder_guards.py",
     "sizing the file excerpt for a small-context model when the served window is 262,144 "
     "tokens -- a 54 KB file then takes five slices to read, and both models spent six of "
     "seven attempts reading instead of editing. t096's rated-versus-served context"),

    ("coder-floods-the-model-with-every-note", "projects/local-inference/coder.py",
     '        brief = f"{title}\\n\\n{latest_brief(body)}"',
     '        brief = f"{title}\\n\\n{body}"',
     "scripts/tests/test_coder_guards.py",
     "handing the model every note ever written on a task, including its own run records and "
     "every rejected approach -- t159's note reached 9,916 chars and the run spent 16,000 "
     "tokens reasoning without emitting JSON. The flooding twin of the L-114 starvation"),

    ("coder-repeat-request-serves-the-same-window", "projects/local-inference/coder.py",
     '                        more = unseen(root, rel, shown_to.get(rel, 0))',
     '                        more = None',
     "scripts/tests/test_coder_guards.py",
     "answering a repeated file request with the same excerpt -- the model asks for more "
     "because the capped window did not contain what it needed, gets the identical text, "
     "and loops. t159 burned seven attempts on a target at line 1,078 of a file shown to 1,001"),

    ("coder-shadows-the-baseline-suite-list", "projects/local-inference/coder.py",
     '                repeats = []      # NOT `already` -- that name holds the baseline suite list',
     '                already = []',
     "scripts/tests/test_coder_guards.py",
     "reusing `already` for the repeat-file list, shadowing the baseline suite list -- after "
     "the first file request every suite that was ALREADY failing gets blamed on the model, "
     "which is the exact false negative that made codeval read 0/25"),

    ("coder-loses-files-after-a-parse-failure", "projects/local-inference/coder.py",
     '    return base + files + tail',
     '    return base + tail',
     "scripts/tests/test_coder_guards.py",
     "dropping the opened files from the prompt on every branch except ASKED -- the model "
     "then alternates forever between asking for files and failing without them, which is "
     "exactly how t017 burned seven attempts twice"),

    ("coder-file-requests-do-not-accumulate", "projects/local-inference/coder.py",
     '             + "\\n\\n".join(opened[r] for r in sorted(opened))) if opened else ""',
     '             + "\\n\\n".join(list(opened.values())[:1])) if opened else ""',
     "scripts/tests/test_coder_guards.py",
     "rebuilding the prompt from only the current round's files -- a second request produces "
     "a byte-identical prompt, which at temperature 0 is deterministic, so the run repeats "
     "the same reply until the attempt budget is gone. t159 burned six attempts this way"),

    ("coder-sends-vllm-flag-to-ollama", "projects/local-inference/coder.py",
     '                "think": (_s["think"] if think else False),',
     '                "think": False,',
     "scripts/tests/test_coder_guards.py",
     "sending chat_template_kwargs to Ollama, which ignores it -- the model then reasons "
     "without bound and spends the entire output budget before emitting any JSON: measured "
     "16,000 tokens, 178s, empty result. The mutation shown here is the tempting WRONG fix: "
     "silencing reasoning to make the JSON appear, which trades D-30 away to fix a parse"),

    ("coder-branch-forgets-line-range-ops", "projects/local-inference/coder.py",
     '    ok, detail = apply_ops(wt, ops)',
     '    ok, detail = (all(op.get("old") for op in ops), "inline")',
     "scripts/tests/test_coder_guards.py",
     "write_branch reimplementing op application instead of delegating -- it knew only the "
     "anchored shape and died with KeyError: 'old' on the line-range ops from t145, so an "
     "accepted and verified patch could never reach a review branch"),

    ("coder-baseline-narrower-than-diff", "projects/local-inference/coder.py",
     '           for e in SOURCE_EXT for p in root.rglob(f"*{e}") if p.is_file()}',
     '           for e in (".py", ".md") for p in root.rglob(f"*{e}") if p.is_file()}',
     "scripts/tests/test_coder_guards.py",
     "narrowing the baseline below the set diff_of walks -- diff_of skips any file the "
     "baseline lacks, so every Kotlin edit becomes unmeasurable and reports NO-OP whatever "
     "it did. This is L-114, and it is the whole of the Android result in L-112"),

    ("coder-attributes-runs-to-the-smoke-model", "projects/local-inference/coder.py",
     '        if "0.5B" not in served:',
     '        if True:',
     "scripts/tests/test_coder_guards.py",
     "attributing a coding run to whichever vLLM container answers first -- so a result gets "
     "recorded against the 0.5B smoke model resident beside the real one, and t151's "
     "model-vs-model comparison silently compares nothing"),

    ("serve-guesses-a-container", "projects/local-inference/serve.py",
     "    if len(names) > 1:",
     "    if False:",
     "scripts/tests/test_serve_guards.py",
     "picking one of several serving containers instead of refusing -- so bench, stop and the "
     "residency verdict silently address a container the caller did not name"),

    ("serve-gates-by-name-not-pid", "projects/local-inference/serve.py",
     '        sel = ["--pid", pid]',
     '        sel = ["--match", "VLLM::EngineCore"]',
     "scripts/tests/test_serve_guards.py",
     "reverting to the name match, which SUMS every vLLM engine -- a smoke model then clears "
     "any floor on the memory of the large model resident beside it"),

    ("sweep-allows-uncommitted", "scripts/sweep.py",
     "    if not is_committed(f):", "    if False:",
     "scripts/tests/test_sweep.py",
     "the guard that stops --done deleting an uncommitted message"),

    ("sweep-tracked-not-bytes", "scripts/sweep.py",
     'return disk.returncode == 0 and disk.stdout.strip() == head.stdout.strip()',
     'return head.returncode == 0',
     "scripts/tests/test_sweep.py",
     "comparing 'is tracked' instead of 'are these bytes committed' -- the subtle version"),

    ("captures-move-before-verify", "scripts/sweep_captures.py",
     "        if after != before:", "        if False:",
     "scripts/tests/test_sweep_captures.py",
     "removing the hash check before the source file is deleted"),

    ("captures-overwrite-corpus", "scripts/sweep_captures.py",
     "        if target.exists() and sha(target) != sha(f):",
     "        if False:",
     "scripts/tests/test_sweep_captures.py",
     "letting a name collision overwrite a different file in the corpus"),

    ("harness-chain-ignored", "projects/local-inference/harness.py",
     '            if rec.get("prev") != prev:', "            if False:",
     "scripts/tests/test_harness.py",
     "the hash chain no longer detecting a tampered log"),

    ("harness-missing-log-passes", "projects/local-inference/harness.py",
     '            return False, f"no log for session \'{self.session}\' -- nothing was verified"',
     '            return True, "chain intact"',
     "scripts/tests/test_harness.py",
     "verify() reporting 'intact' for a log that does not exist -- the real bug found 2026-08-24"),

    ("uploads-drop-non-images", "projects/app-repo/feedback.py",
     "        ext = safe_ext(client_name)",
     "        ext = safe_ext(client_name)\n        if ext not in IMAGE_EXT: continue",
     "scripts/tests/test_uploads.py",
     "restoring L-087 exactly: silently discarding non-image uploads"),

    ("queue-lock-removed", "projects/app-repo/taskstore.py",
     "        fcntl.flock(fh, fcntl.LOCK_EX)", "        pass",
     "scripts/tests/test_task_queue.py",
     "concurrent writers losing each other's notes"),

    ("search-default-misses-corpus", "scripts/workspace_mcp/core.py",
     '    def search(self, query, glob="**/*", limit=40):',
     '    def search(self, query, glob="**/*.md", limit=40):',
     "scripts/tests/test_search_scope.py",
     "restoring the default that could not see the corpus (Chat's section 9)"),

    ("append-escapes-workspace", "scripts/workspace_mcp/core.py",
     '        if not (rel.startswith(APPEND_ROOT) and rel != APPEND_ROOT.rstrip("/")):',
     "        if False:",
     "scripts/tests/test_append.py",
     "append writing outside workspace/ -- e.g. appending to STATE.md"),

    # --- added 2026-08-24: every check built this session gets a mutation, because a
    # --- fixture that never reaches its code passes silently (L-093).

    ("forge-jas-allowed", "projects/app-repo/taskstore.py",
     '    if who == "jas" and not _from_form:', "    if False:",
     "scripts/tests/test_task_queue.py",
     "Code able to write notes in Jas's voice again (L-091)"),

    ("staleness-ignores-hash", "scripts/check_read_staleness.py",
     "        elif now != r[\"sha256\"]:", "        elif False:",
     "scripts/tests/test_read_staleness.py",
     "a file that changed since Chat read it no longer being reported"),

    ("staleness-missing-file-ok", "scripts/check_read_staleness.py",
     "        if now is None:", "        if False:",
     "scripts/tests/test_read_staleness.py",
     "a file deleted since it was read passing silently"),

    ("captures-epoch-ignored", "scripts/check_captures.py",
     "        if end < CAPTURE_POSSIBLE_FROM:", "        if True:",
     "scripts/tests/test_check_captures.py",
     "the capture-gap check skipping every window, i.e. never reporting a gap"),

    ("captures-any-window-flagged", "scripts/check_captures.py",
     "MIN_CALLS = 4", "MIN_CALLS = 0",
     "scripts/tests/test_check_captures.py",
     "flagging a two-call lookup as an uncaptured session -- the cry-wolf failure"),

    ("prose-check-blind", "scripts/check_workspace.py",
     'claim = re.search(r"([^.]*\\b" + re.escape(path) + r"[^.]*not served[^.]*\\.)", prose)',
     "claim = None",
     "scripts/tests/test_served_prose.py",
     "the MCP server able to claim chat/ is not served again (L-085)"),

    ("pointers-undeclared-ok", "scripts/check_workspace.py",
     "        undeclared = sorted(d for d in dangling if d.split(\" -> \", 1)[1] not in declared)",
     "        undeclared = []",
     "scripts/tests/test_check_workspace.py",
     "an undeclared dangling reference passing again (L-072)"),

    ("pipeline-check-narrow", "scripts/check_workspace.py",
     'for pattern in ("scripts/*.sh", "scripts/**/*.sh", "projects/**/*.sh"):',
     "for pattern in ():",
     "scripts/tests/test_check_workspace.py",
     "the $?-after-pipeline check narrowed back to scripts/hooks only (t113)"),

    # The import writes into chat/, which has no git rollback under D-22. The guard that
    # matters is the one that DECLINES to supersede on a weak match: a wrong supersede
    # moves the only copy of a session the export never contained.
    ("import-supersedes-on-title-alone", "scripts/import_export.py",
     'if (c.get("created_at") or "")[:10] != date:',
     'if False:',
     "scripts/tests/test_import_export.py",
     "the date half of the supersede match dropped, so any same-title conversation "
     "from any month would displace a Chat-emitted transcript"),

    ("import-dedupe-disabled", "scripts/import_export.py",
     'fresh = [c for c in convs if c["uuid"] not in have]',
     'fresh = list(convs)',
     "scripts/tests/test_import_export.py",
     "UUID dedupe bypassed, re-importing all 260 conversations over the corpus"),

    ("import-writes-unverified", "scripts/import_export.py",
     "    if want != got:",
     "    if False:",
     "scripts/tests/test_import_export.py",
     "a write reported as verified without re-reading it -- the same shape as the "
     "warm-cache verify that proved nothing (L-060)"),

    # A session log that passes its own check while still holding placeholders is worse
    # than no log: the audit goes quiet and the record becomes a skeleton nobody filled.
    ("session-log-accepts-placeholders", "scripts/session_log.py",
     "    if n:",
     "    if False:",
     "scripts/tests/test_session_log.py",
     "--check accepting an unfilled log, which is how three days of work ended up "
     "with no narrative record"),

    ("session-log-ignores-status", "scripts/session_log.py",
     'elif f"- {day} —" not in status.read_text(encoding="utf-8"):',
     "elif False:",
     "scripts/tests/test_session_log.py",
     "the STATUS.md staleness guard disabled -- the fourteen findings the audit raised "
     "on 2026-08-25 in one line"),

    # The cache-speed guard IS L-060. If it stops covering the medium actually in use, a
    # verify served entirely from RAM reports a good backup that may not exist on disk.
    ("backup-cache-guard-misses-network", "scripts/backup_corpora.py",
     'if p.startswith(REMOVABLE_PREFIXES) or p.startswith(NETWORK_PREFIXES):',
     "if p.startswith(REMOVABLE_PREFIXES):",
     "scripts/tests/test_backup_corpora.py",
     "the L-060 speed guard blind to network mounts again, which is how the first NAS "
     "backup would have accepted a cache-served verify"),

    # The library default was already mutated ("search-default-misses-corpus"), and the
    # SERVED default went wrong anyway and stayed wrong for five days. Mutating one layer
    # of a two-layer default proves nothing about the layer callers actually reach.
    ("search-served-default-misses-corpus", "scripts/workspace_mcp/server.py",
     'def search(query: str, glob: str = "**/*") -> dict:',
     'def search(query: str, glob: str = "**/*.md") -> dict:',
     "scripts/tests/test_search_scope.py",
     "the MCP tool signature back to **/*.md, hiding every .txt in the corpus from Chat "
     "including all three State Ledgers (found by Chat 2026-08-25, not by a check)"),

    ("search-counter-lies-out-of-scope", "scripts/workspace_mcp/core.py",
     '                    skipped_archive if glob in ("**/*", "**/*.md") else None),',
     '                    skipped_archive),',
     "scripts/tests/test_search_scope.py",
     "skipped_frozen_export reporting 0 for a glob that could never reach the export -- "
     "the did-not-look case reported as looked-and-skipped-nothing"),

    # The receipt parser must REPORT what it cannot read. A bare `continue` here is L-087
    # in a different file: a receipt with a typo vanishes and the index calls it
    # unacknowledged forever, with nothing saying a line was unreadable.
    ("receipts-swallow-malformed", "scripts/outbox_index.py",
     "            MALFORMED.append(line)\n            continue",
     "            continue",
     "scripts/tests/test_outbox_index.py",
     "silently discarding an unreadable receipt line -- the first defect a local model "
     "fixed end to end (t146)"),

    # The page check is the only thing standing between a heredoc accident and a dead UI
    # that every API test still calls healthy (L-108).
    ("jscheck-blind-to-line-break-in-string", "scripts/jscheck.py",
     '                if src[j] == "\\n":\n                    raise JsError(line, f"unterminated {quote} string',
     '                if False:\n                    raise JsError(line, f"unterminated {quote} string',
     "scripts/tests/test_chatui_page.py",
     "the tokeniser no longer noticing a line break inside a quoted string -- the exact "
     "defect that killed the chat page and was invisible to every endpoint test"),

    # pysource replaced three regex parsers at once. Both halves need a guard: the value
    # it reads, and the fact that it REPORTS what it could not read.
    ("pysource-constant-blind", "scripts/pysource.py",
     "                    return ast.literal_eval(node.value)",
     "                    return default",
     "scripts/tests/test_served_prose.py",
     "constants read as empty, so the MCP prose check compares its claims against nothing "
     "and passes -- test_check_workspace does not reach this path, test_served_prose does"),

    ("pysource-drops-unparseable", "scripts/pysource.py",
     '                unparsed.append(f"{node.name}({arg.arg}=) {ast.unparse(d)}")',
     "                pass",
     "scripts/tests/test_check_deployed.py",
     "a default it cannot parse dropped silently again -- the L-087 shape that t148 was "
     "raised to remove"),

    # A patch that changes nothing satisfied every acceptance rule (L-111). The guard has
    # to be mutated too, or it is exactly the kind of check nobody has watched fail.
    ("coder-accepts-noop-patch", "projects/local-inference/coder.py",
     "    return bool(diff_of(root, baseline, ops).strip())",
     "    return True",
     "scripts/tests/test_coder_guards.py",
     "an edit that applies and changes nothing being ACCEPTED again -- no suite can see it, "
     "because nothing broke, because nothing happened"),

    ("backup-swallows-content-errors", "scripts/backup_corpora.py",
     "    shutil.copyfile(src, dst)",
     "    try:\n        shutil.copyfile(src, dst)\n    except OSError:\n        pass",
     "scripts/tests/test_backup_corpora.py",
     "metadata tolerance widened until it swallows real copy failures, turning a lost "
     "file into a silent success"),
]


def run_suite(root, suite):
    r = subprocess.run([sys.executable, str(root / suite)],
                       capture_output=True, text=True, cwd=str(root), timeout=600)
    return r.returncode, (r.stdout + r.stderr)[-400:]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--only", help="substring of a mutation name")
    a = ap.parse_args()

    muts = [m for m in MUTATIONS if not a.only or a.only in m[0]]

    if a.list:
        for name, f, _, _, suite, why in muts:
            print(f"  {name}\n      breaks : {f}\n      i.e.   : {why}\n"
                  f"      caught by: {suite}")
        return 0

    caught = missed = broken = 0
    print(f"{len(muts)} mutation(s). Each must make its suite FAIL.\n")
    for name, rel, find, repl, suite, why in muts:
        tmp = Path(tempfile.mkdtemp())
        root = tmp / "repo"
        try:
            shutil.copytree(REPO, root, symlinks=True,
                            ignore=shutil.ignore_patterns(
                                ".git", "chat", ".venv", "node_modules", "__pycache__",
                                "sessions", "attachments", "*.jsonl"))
            target = root / rel
            src = target.read_text(encoding="utf-8")
            if src.count(find) != 1:
                print(f"  [SKIP] {name}: anchor matches {src.count(find)}x -- the code moved, "
                      f"so this mutation no longer describes a real bug")
                broken += 1
                continue
            # Pristine baseline: the suite must pass on unmutated code in this
            # environment. Without this guard, a suite that cannot run here (e.g.
            # needs .git, which the temp copy excludes) makes every mutation
            # against it a vacuous [caught].
            rc0, tail0 = run_suite(root, suite)
            if rc0 != 0:
                print(f"  [SKIP] {name}: suite fails on pristine code in the temp environment (rc={rc0})")
                broken += 1
                continue

            target.write_text(src.replace(find, repl), encoding="utf-8")

            rc, tail = run_suite(root, suite)
            if rc != 0:
                caught += 1
                print(f"  [caught] {name}")
            else:
                missed += 1
                print(f"  [MISSED] {name}\n           {why}\n"
                      f"           {suite} still passes with this broken. "
                      f"The guard has a hole.")
        except Exception as e:
            broken += 1
            print(f"  [SKIP] {name}: {type(e).__name__}: {e}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{caught} caught, {missed} MISSED, {broken} skipped")
    if missed:
        print("\nA missed mutation is the finding. The suite reports PASS on code that is "
              "broken\nin exactly the way it exists to prevent.")
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
