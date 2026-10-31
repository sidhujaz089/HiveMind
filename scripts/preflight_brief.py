#!/usr/bin/env python3
"""Ask the local model whether a brief is satisfiable BEFORE it starts work.

Jas, 2026-09-09: "local models must review everything before they begin. That also includes
all the mechanical fixtures and scripts that check the oracles."

WHY. Seven failures in two days came from one family: a requirement that cannot be met
honestly will be met dishonestly. Given an impossible instruction the model can only corrupt
something, fail, or invent -- and it did all three. Every one of those defects was in the
BRIEF or the ORACLE, written by the reviewing assistant, not in the model's work.

Twice the model had the information to catch it and its signal was missed, because a refusal
only ever reached us as a failed attempt: five straight rejections of an impossible assertion,
and an ACCEPTED summary whose parenthesis said the test expected something impossible while
the suite showed green. **A review step makes the objection legible instead of something a
human has to infer.**

This runs BEFORE the dispatch and costs one small call. It does not change any file.

    python3 scripts/preflight_brief.py --brief brief.txt --files scripts/x.py \\
                                       --target scripts/tests/test_x.py

Exit 0 = satisfiable, go. Exit 2 = the model objects; read its answer before dispatching.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import roles  # noqa: E402  -- the model behind a role comes from SERVING.json
import modellease                                            # noqa: E402

# THE SENIOR'S OWN SERVER (Jas, 2026-09-28): dispatch_loop runs every preflight on the senior
# role's endpoint and lease (SERVING.json). Unset = the main server and the shared lease, as before.
LEASE = Path(os.environ.get("HIVEMIND_LEASE") or (REPO / "workspace" / "scoping-lease.json"))
OLLAMA = os.environ.get("HIVEMIND_OLLAMA", "http://127.0.0.1:11434").rstrip("/")
# ONE NAME FOR THE VALUE THE REVIEW RUNS AT, so what is sent and what is recorded in the
# ledger cannot drift. Jas, 2026-09-15: if the merge queue starts showing errors we revisit
# this, and that decision needs the level written next to each review, not remembered.
REVIEW_THINK = "low"
QUESTION = """You are the preflight reviewer. Do NOT write code. Review this work order and say whether it can be
carried out honestly.

You are being asked because seven failures in two days came from briefs and oracles that could
not be satisfied. Given an impossible instruction you can only corrupt something, fail, or
invent. Your job here is to say so FIRST, in words, instead of us discovering it from a failed
attempt.

Check, in this order:

  1. Does every path, module, function and test suite named below actually exist in what you
     have been shown? Name anything that does not.
  2. Is the ORACLE self-consistent? Could a single implementation satisfy all of its
     assertions at once, or do two of them contradict? Quote any pair that conflict.
  3. Does the target suite depend on anything MISSING, or on a file that would itself have to
     change for the suite to pass? Depending on a helper that already exists and works is
     NORMAL and is not a problem: the repo-local modules the suite imports are shown to you
     below, and you may not change them because they do not need changing. The work order is
     impossible only if a dependency does not exist at all, or if making the suite green would
     require editing a file outside the permitted list.
  4. Does the oracle assert TODAY'S DEFECT as the expected answer -- so that fixing the bug
     would break the test?
  5. Does anything ask you to change behaviour beyond the stated goal?
  6. What is MISSING that you would need before this can be done honestly?

DO NOT INVENT ANYTHING, HERE OR LATER. If something you need does not exist, that is a finding
about the work order and it is the most valuable thing you can tell us. Naming a gap is
success, not failure. We have lost days to answers that complied by inventing a file, a
function, or a test suite, and every one of those was our fault for asking a question that
could only be answered that way.

A NON-EMPTY "MISSING" LIST STOPS THE WORK. It is not a caveat to note and proceed past. The
brief gets fixed or the gap gets filled first, and then you are asked again.

Answer in this shape and nothing else:

VERDICT: SATISFIABLE | NOT SATISFIABLE
WHY: one paragraph, concrete, quoting the offending line if there is one.
MISSING: a list of what does not exist but would be needed, or exactly "nothing".

--- THE WORK ORDER ---
{brief}

--- FILES I MAY CHANGE ---
{files}

--- THE TARGET SUITE, IN FULL ---
{target}

--- MODULES THE TARGET SUITE IMPORTS (they EXIST; you may not change them) ---
{deps}
"""


# THE IDENTITY OF THE QUESTION ITSELF. An objection is evidence about a brief AND about the
# reviewer that produced it: when the question changes, an old refusal stops being a fact about
# the brief. Thirteen compliance chunks stayed held all day on verdicts from a reviewer that
# could not see the oracle its target imported -- correct answers to a question we have since
# fixed. Recording this sha on every row lets review_state() expire them mechanically instead
# of someone deciding, per chunk, that a refusal is stale.
PROMPT_SHA = hashlib.sha256(QUESTION.encode()).hexdigest()[:12]


# TIMEOUT 1800 -> 3600, 2026-09-14, in the same change that raised _show()'s cap. Raising the
# cap to 130000 is what made this necessary: the review now sees the whole of coder.py
# (92,707 chars, ~25k tokens) instead of 60000 of it, and at think:high the request outran
# the old 1800s socket timeout and died with TimeoutError after ~30 minutes. Verified it was
# not the server: a /api/chat probe answered in 2.2s immediately afterwards, with both models
# resident. A REVIEW THAT TIMES OUT IS NOT AN APPROVAL -- decide() already refuses to read a
# truncated reply as a verdict, and this is the same rule at the socket.
def ask(prompt, model, timeout=None, num_predict=12000):
    # THE CONTRACT, not a hand-built options dict. docs/skills/local-model-loading says
    # "Never build an options dict. Call the contract." I built one here and
    # test_options_contract caught it -- the skill was indexed and findable and I did not
    # search for it before writing this file.
    sys.path.insert(0, str(REPO / "projects" / "local-inference"))
    import settings                                          # noqa: E402
    if timeout is None:
        # DERIVED, 2026-09-14. Was 1800, then 3600, and both were guesses. A 20000-token budget
        # needs 5600s at the 4 tok/s floor this box decodes at, so 3600 would still have killed
        # the very review that exposed the problem. settings.timeout_for() is the one place the
        # number comes from now.
        timeout = settings.timeout_for(num_predict,
                                       harness="preflight_brief.py", model=model)
    # 4000 was sized for a one-file brief. On 2026-09-09 a four-file review spent the
    # whole budget thinking and came back done_reason=length with EMPTY content -- a
    # truncated review, which this script correctly refuses to read as approval. The
    # ceiling has to leave room for the answer after the reasoning, or the safe failure
    # fires on every large brief and the review step stops being usable.
    # think:low FOR THE REVIEW ONLY, Jas 2026-09-15. The "coder" section sends think:high,
    # raised deliberately on 2026-09-12 because Qwen's card warns that lower effort on
    # MULTI-TURN AGENTIC work causes more failures -- that reasoning is about coder.py writing
    # code, and it still stands there. THIS is not that job: a preflight checks a work order
    # against source it has been handed, in one turn, and solves nothing. At high it has never
    # once finished: 24 reviews recorded, zero SATISFIABLE, and on 2026-09-15 two briefs
    # truncated at 25 minutes each with done_reason=length -- one of them a SMALL review (a
    # 1.2 KB stub and a short suite), so prompt size does not explain it. Reasoning ate the
    # whole answer budget before a verdict was written. t305 measured the same thing across
    # four rounds. Overriding here and not in the section leaves production coding alone.
    body = settings.request("coder", model,
                            [{"role": "user", "content": prompt}],
                            num_predict=num_predict, think=REVIEW_THINK)
    _t0 = time.time()
    # ANY ENGINE (docs/ENGINES.md): the job's server (HIVEMIND_OLLAMA) or wherever `model` is served.
    import infer
    d = infer.chat(body, timeout=timeout)
    try:
        sys.path.insert(0, str(REPO / "scripts"))
        import run_telemetry
        run_telemetry.record(harness="preflight_brief.py", model=model,
                             seconds=time.time() - _t0, num_predict=num_predict,
                             eval_count=d.get("eval_count"),
                             prompt_eval_count=d.get("prompt_eval_count"),
                             done_reason=d.get("done_reason"), timeout_s=timeout,
                             load_seconds=(d.get("load_duration") or 0) / 1e9, ok=True)
    except Exception as e:                                       # noqa: BLE001
        print(f"  (telemetry not recorded: {type(e).__name__}: {e})", file=sys.stderr)
    msg = d.get("message") or {}
    content = msg.get("content") or ""
    ask.salvaged = False
    if d.get("done_reason") != "length":
        return content, d.get("done_reason")

    # THE SALVAGE. Jas, 2026-09-15: "Truncation is the issue to solve and not thinking."
    #
    # Reasoning and the answer draw on ONE budget and nothing reserves room for the answer,
    # so a long enough reasoning pass returns done_reason=length with content EMPTY. That is
    # measured in settings.py: at num_predict=100 with thinking on, all 100 went to thinking
    # and the content came back empty every time. Lowering the reasoning level only makes it
    # less likely; a bigger brief buys the same failure back.
    #
    # PORTED, NOT INVENTED. console.py:800 has done this for weeks -- re-ask with reasoning
    # OFF, hand the model back its own unfinished thinking, and the whole budget becomes the
    # reply -- and coder.py does the same as of t238 (2026-09-14). This reviewer was the one
    # caller of the shared idea that never got it, which is L-114's shape, and it cost three
    # truncated reviews and two parked chunks on 2026-09-15.
    thinking = (msg.get("thinking") or "").strip()
    if content.strip() and not thinking:
        # It answered and merely ran on. The answer is what decide() reads; report honestly.
        return content, d.get("done_reason")
    salvage_body = settings.request(
        "coder", model,
        [{"role": "user", "content": prompt},
         {"role": "assistant", "content": content},
         {"role": "user", "content":
          "You ran out of budget while reasoning and never gave the verdict. Reasoning is "
          "OFF now and the whole budget is the reply. Here is how far you got:\n\n"
          + thinking[-6000:] +
          "\n\nAnswer now from what you have, in the required shape: VERDICT, WHY, MISSING. "
          "If you did not get far enough to judge something, say so in MISSING rather than "
          "guessing -- an honest gap is the most valuable thing you can report."}],
        num_predict=num_predict, think=False)
    d2 = infer.chat(salvage_body, timeout=timeout)
    salvaged = (d2.get("message") or {}).get("content") or ""
    if not salvaged.strip():
        # The salvage answered nothing either. That is NOT an approval and must not read as
        # one: report the original length so decide() and record_review refuse it.
        return content, "length"
    ask.salvaged = True
    print(f"  (ran out of budget mid-thought after {len(thinking.split())} words of "
          f"reasoning; answered from what it had)")
    return salvaged, d2.get("done_reason")


# RAISED 60000 -> 130000, 2026-09-14. The 60000 cap cut projects/local-inference/coder.py
# (92,707 chars) and Q16 correctly refused the review for it: "only 60000 of 92707 chars of
# coder.py are shown, so the attempt counter, failure/logging path, and existing failure
# strings needed to satisfy the retry and logging constraints are not visible". The elision
# announcing itself is what let it say that -- the design worked; the number was simply too
# small for the largest file anyone reviews here. 130000 chars is roughly 33k tokens, which
# fits the 65536 window this box serves with room for the brief and the answer.
SHOW_CAP = 130000

# t1230: how many suites a brief's precondition check will RUN before it stops and says so.
PRECONDITION_SUITES_CAP = 4
# t1230: how long one named suite may run before its result is recorded as a timeout.
PRECONDITION_TIMEOUT_S = 90


def _show(rel, cap=SHOW_CAP):
    """A file as shown to the model -- never SILENTLY cut.

    This used to be `read_text()[:6000]`. On 2026-09-09 that sliced
    scripts/gate_callers.py off in the middle of a function and the brief then told the
    model the file was included "in full". Q16 caught it and refused, which is the review
    step working -- but the defect was in the reviewer, and it is the same fixed-width
    slice this project spent two days removing from everywhere else. It is not caught by
    scripts/signature_gate.py, whose RECORD_SLICE matches only dict-key and subscript
    assignments; a slice inside an f-string reads as display.

    A cap still exists, because a 200 KB file would push the real question out of the
    window. But an elision now ANNOUNCES itself, with the count, so the model can name
    what it was not shown instead of reasoning over a hole it cannot see.
    """

    text = (REPO / rel).read_text()
    if len(text) <= cap:
        return f"### {rel}  ({len(text)} chars, complete)\n{text}"
    return (f"### {rel}  ({len(text)} chars, SHOWN TRUNCATED AT {cap} -- "
            f"{len(text) - cap} chars NOT shown to you. Treat anything past the cut as\n"
            f"unseen: say so rather than assuming what it contains.)\n{text[:cap]}")



# ------------------------------------------------------------------ the objection ledger
#
# Jas, 2026-09-14: "When we have tasks that get objected to, is that logged
# anywhere? I think that would be a great thing for future continuous improvement
# projects with the harnesses."
#
# It was not. Every objection this script made went to stdout and died with the
# terminal. WHAT THAT COST, on one brief, in one day: t238 was objected to FOUR
# times and every objection was correct and DIFFERENT --
#
#   1. the reviewer was starved of the file (_show cut coder.py at 60000 of 92707
#      chars, and said so, which is the only reason it could name the gap);
#   2. two real gaps in the brief -- a style it could not see, and an assertion
#      that was underspecified;
#   3. a CONTRADICTION written into the brief: "done_reason OR finish_reason" plus
#      "if they disagree treat as UNKNOWN", with no precedence rule;
#   4. truncation at 49 minutes.
#
# Four lessons about how briefs fail, and the only ones that survived did so
# because they were quoted by hand into commit messages.
#
# TWO RULES THIS LEDGER IS BUILT ON.
#
# A SATISFIABLE REVIEW WRITES A ROW TOO. A ledger that records only failures
# cannot compute a rate, and the question that pays for this file is a rate:
# which KIND of gap does a brief fail on most, and does fixing the brief actually
# stop the next objection. With only failures on disk, "objections went down"
# and "we stopped asking" look identical.
#
# THE OBJECTION IS STORED WHOLE. Not a prefix, not a summary -- the reviewer's
# entire reply. docs/skills/never-truncate-a-record: the value of this corpus is
# in the specific wording of a gap, and a record cut to a convenient width stops
# answering the question it was kept for. These rows are small; a brief review is
# a few KB.
#
# brief_sha256 is what makes "what changed afterwards" answerable without a
# second pass: consecutive rows for the same brief path with DIFFERENT hashes are
# a brief that was edited between objections, and rows with the SAME hash are the
# same brief asked twice.
OBJECTIONS = REPO / "workspace" / "preflight-objections.jsonl"


DEPS_BUDGET = 60000


def target_deps(target, already_shown=(), budget=DEPS_BUDGET):
    """Repo-local modules the target suite imports DIRECTLY, resolved to files.

    WHY THIS EXISTS. A generated compliance suite is two lines -- `import compliance_oracle`
    and `sys.exit(compliance_oracle.run(path, check))` -- so its ENTIRE pass rule lives in a
    file the reviewer was never shown. It answered, correctly and repeatedly, "MISSING: an
    importable compliance_oracle module in scripts/tests/", and thirteen chunks sat parked on
    that for a day while each re-review burned six to seventeen minutes of Q16.

    Resolution order matters: a test does sys.path.insert(0, its own directory) before
    importing its helper, so the target's own directory is tried FIRST. Anything that does not
    resolve to a file in this repository is stdlib or a dependency and is left out -- the point
    is to show what the repo provides, not to teach the model Python.

    BREADTH-FIRST, BOUNDED BY BYTES AND NOT BY DEPTH. Direct imports alone were tried first and
    are not enough: a compliance suite imports compliance_oracle, which delegates the actual
    rule to library_checks, so what the reviewer needs sits TWO hops out. Shown only the first
    hop it answered, exactly and correctly, "the oracle calls library_checks.run(...) and
    scripts/library_checks.py is not shown ... I would be guessing at the oracle's exact
    pattern." Depth was the wrong limit. The real constraint is the model's window, so the
    BUDGET is the limit, and breadth-first ordering spends it on the nearest modules first.

    Unbounded following is still wrong: test_console_pin_reaches_ask.py reaches console.py and
    coder.py within two hops -- 200 KB -- which would push the question itself out of the window
    and truncate the review, the failure that made every review before today unusable (t305).
    What does not fit is NAMED rather than dropped, and only a module that was actually SHOWN is
    followed further, so nothing is pulled in on account of a file the reviewer never saw.

    A file already listed in --files is skipped: showing it twice spends the window to say
    nothing new.
    """
    import ast
    from collections import deque

    def imports_of(rel):
        f = REPO / rel
        if not f.is_file():
            return []
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            return []
        mods = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods += [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                mods.append(node.module.split(".")[0])
        return list(dict.fromkeys(mods))            # de-duplicated, import order preserved

    # THE EARLY EXIT RETURNS THE PAIR. It returned a bare [] until a test unpacked it: the
    # caller does `deps, omitted = target_deps(...)`, so a target that does not exist would have
    # raised ValueError inside the reviewer rather than being reported as the missing target.
    if not (REPO / target).is_file():
        return [], []
    skip = {target, *already_shown}
    out, omitted, spent = [], [], 0
    queue = deque([target])
    while queue:
        rel = queue.popleft()
        for m in imports_of(rel):
            for cand in (f"{Path(rel).parent}/{m}.py", f"scripts/{m}.py",
                         f"scripts/tests/{m}.py", f"projects/local-inference/{m}.py"):
                f = REPO / cand
                if not f.is_file() or cand in skip:
                    continue
                skip.add(cand)
                size = f.stat().st_size
                # OMISSIONS ARE RETURNED, NOT DROPPED. A reviewer told nothing about a gap
                # reasons straight over it; this repository has paid for that twice, and
                # _show()'s own announced elision exists for the same reason.
                if spent + size > budget and out:
                    omitted.append(cand)
                else:
                    out.append(cand)
                    spent += size
                    queue.append(cand)
                break
    return out, omitted


REF_FULL_CAP = 12000       # a named file up to this size is shown whole; a larger one as an outline
REFS_BUDGET = 40000
REF_PATH = re.compile(r"\b((?:scripts|projects|workspace|docs|models)/[\w./-]+\.(?:py|sh|json|md|txt))\b")
REF_MODULE = re.compile(r"\b(?:import|from)\s+([A-Za-z_]\w*)|\b([A-Za-z_]\w*)\.(?:py\b|[a-z_]\w*\()"
                        r"|\b([a-z_]\w*_\w*)\.[a-z_]\w*\b")


def brief_refs(brief_text, skip=()):
    """Repo files the WORK ORDER names, beyond --files and the target's imports.

    Jas, 2026-09-29, 15 briefs held for Claude: six of the Senior's objections were about a file
    the brief names and the reviewer was never shown -- "settings.py is not shown to me and I
    cannot verify that request() is a function in that module" (p34, p53; settings.py:734 has
    it), scheduler_commands (p170), check_enforcement.py (p171), test_error_scan.py (p164). Each
    exists. target_deps follows the target suite's imports only; a brief that says "call
    settings.request" or "lazy-import scheduler_commands" names a dependency no import shows.
    Only files that EXIST are returned: a named file that does not exist is the reviewer's to
    find, and is exactly what it should object to.
    """
    out, spent, skip = [], 0, set(skip)
    cands = [m.group(1) for m in REF_PATH.finditer(brief_text or "")]
    for m in REF_MODULE.finditer(brief_text or ""):
        name = m.group(1) or m.group(2) or m.group(3)
        cands += [f"scripts/{name}.py", f"scripts/tests/{name}.py", f"projects/local-inference/{name}.py"]
    for rel in dict.fromkeys(cands):
        f = REPO / rel
        if rel in skip or not f.is_file():
            continue
        skip.add(rel)
        size = min(f.stat().st_size, REF_FULL_CAP)
        if spent + size > REFS_BUDGET and out:
            continue
        out.append(rel)
        spent += size
    return out

def precondition_findings(brief_text, files=(), target=None, repo=REPO):
    """Run the suites a brief claims are green, and report the claims this tree does not meet (t1230).

    A brief can assert "suite X is green at build time" about a precondition commit that was
    never made. Shown the suite's source and that sentence side by side, the reviewer's honest
    answer is a paragraph -- and prose is not a gate. So RUN the claim: a named suite that exits
    non-zero, times out, or is never run becomes a finding here, in words, before a model
    dispatch spends its minutes rediscovering it.

    The target suite is never checked: it is red by design, which is what the dispatch is for.
    Nor is any file in `files`, which the builder is about to change -- red today, green after.
    A named path that is not on disk is skipped, because main() already reports a path that does
    not exist and this function must not invent a cause for that stop.
    """
    findings = []
    candidates = []
    for m in REF_PATH.finditer(brief_text or ''):
        rel = m.group(1)
        if (rel.startswith('scripts/tests/') and rel.endswith('.py')
                and Path(rel).name.startswith('test_')):
            candidates.append(rel)
    run = 0
    # FIRST-APPEARANCE ORDER, each suite once: a brief that names a file twice must not run it
    # twice, and the findings have to read in the order the brief asserts them.
    for rel in list(dict.fromkeys(candidates)):
        if rel in files or rel == target:
            continue
        p = repo / rel
        if not p.is_file():
            continue
        if run >= PRECONDITION_SUITES_CAP:
            # A check skipped for budget reasons is NAMED, never dropped: a silent gap in a
            # record reads as a pass.
            findings.append(
                'PRECONDITION NOT CHECKED: %s -- not run; this brief names more than %d suites'
                ' (PRECONDITION_SUITES_CAP). Name fewer, or run it and record the result in'
                ' the brief.' % (rel, PRECONDITION_SUITES_CAP))
            continue
        run += 1
        try:
            r = subprocess.run([sys.executable, str(p)], capture_output=True, text=True,
                               timeout=PRECONDITION_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            findings.append('PRECONDITION NOT MET: %s timed out after %s s (PRECONDITION_TIMEOUT_S)'
                            % (rel, PRECONDITION_TIMEOUT_S))
            continue
        except OSError as e:
            # A spawn that fails is a finding, not a skip: an except that records nothing reads
            # as green.
            findings.append('PRECONDITION NOT MET: %s spawn failed: %s' % (rel, e))
            continue
        if r.returncode != 0:
            # WHOLE, stdout falling back to stderr: a stop that does not carry what the suite
            # printed is a record nobody can act on.
            findings.append('PRECONDITION NOT MET: %s exits %d\n%s'
                            % (rel, r.returncode, (r.stdout or r.stderr).strip()))
    return findings



def _outline(rel):
    """A large named file as its top-level signatures with line numbers -- enough to confirm that a
    function the brief names exists and what it takes, without spending the window on its body."""
    import ast
    text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
    if len(text) <= REF_FULL_CAP:
        return _show(rel)
    lines = [f"--- {rel} (OUTLINE: {len(text)} chars; top-level signatures only, with line numbers) ---"]
    try:
        for node in ast.parse(text).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                lines.append(f"{node.lineno}: {text.splitlines()[node.lineno - 1].strip()}")
            elif isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) for t in node.targets):
                lines.append(f"{node.lineno}: {', '.join(t.id for t in node.targets)} = ...")
    except SyntaxError:
        lines += [f"{i}: {ln}" for i, ln in enumerate(text.splitlines(), 1)
                  if re.match(r"\s*(def |class |[A-Za-z_][\w-]*\(\)\s*\{)", ln)]
    return "\n".join(lines)


def evidence(target, files, brief_text):
    """(deps, omitted, refs): everything the reviewer is shown beyond --files. The dispatcher calls
    this too, so a verdict expires when what the reviewer WOULD see changes."""
    deps, omitted = target_deps(target, already_shown=files) if target else ([], [])
    # THE TARGET SUITE COUNTS AS WHAT THE WORK ORDER NAMES: p171's oracle reads
    # scripts/check_enforcement.py at import (NEWER = "..."), which no import statement shows.
    tt = ""
    if target and (REPO / target).is_file():
        tt = (REPO / target).read_text(encoding="utf-8", errors="replace")
    refs = brief_refs(brief_text + "\n" + tt, skip=[*files, *([target] if target else []), *deps])
    return deps, omitted, refs


def _sha(text):
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def record_review(brief_path, brief_text, files, target, model, num_predict,
                  verdict, reply, seconds, done_reason, stopped, note="", deps=()):
    """Append one row per review -- every review, whatever the verdict.

    Never raises. A ledger write that can break the reviewer would be a worse
    defect than the missing ledger: the objection itself still has to reach the
    person running this even if the disk is full.
    """
    try:
        shown = []
        for rel in files:
            try:
                n = len((REPO / rel).read_text(errors="replace"))
            except OSError:
                n = -1
            shown.append({"path": rel, "chars": n, "elided": n > SHOW_CAP,
                          "chars_shown": min(n, SHOW_CAP) if n >= 0 else -1})
        row = {
            "at": time.strftime("%Y-%m-%dT%H%M%SZ", time.gmtime()),
            "brief": str(brief_path),
            "brief_sha256": _sha(brief_text),
            "brief_chars": len(brief_text),
            "files": shown,
            # WHAT THE REVIEWER COULD SEE, not only what it was asked (2026-09-16).
            # prompt_sha expires a verdict when the QUESTION changes. It cannot expire one
            # when the EVIDENCE changes, and the evidence changed underneath twelve verdicts:
            # measured from the run logs, every preflight up to 02:50Z showed 1 target import
            # and every one from 03:10Z showed 5, because target_deps began resolving the
            # oracle's own imports. Those twelve then sat held all day naming a file the
            # reviewer would now be shown -- "MISSING: scripts/library_checks.py" against a
            # bundle that now contains scripts/library_checks.py. An objection is a fact about
            # the brief AND about what was in front of the reviewer, so the second half is
            # recorded here and dispatch_loop.review_state compares it.
            "deps_shown": sorted(deps),
            "target": target,
            "model": model,
            "num_predict": num_predict,
            "think": REVIEW_THINK,
            "prompt_sha": PROMPT_SHA,
            "seconds": round(seconds, 1),
            "done_reason": done_reason,
            "verdict": verdict,
            "stopped": stopped,
            "note": note,
            # WHOLE. See the rules above.
            "reply": reply,
        }
        OBJECTIONS.parent.mkdir(parents=True, exist_ok=True)
        with OBJECTIONS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:                                       # noqa: BLE001
        print(f"  (objection not recorded: {type(e).__name__}: {e})", file=sys.stderr)
    # SCOPE QUALITY (Jas, 2026-09-28): the preflight is a check on the scope that wrote this brief.
    # An objection is provisionally the brief's fault; its RCA may reclassify it (a wrong objection
    # is the reviewer's, not the scope's). A truncated or unreadable review is not a verdict.
    import scope_quality
    # beside the objection ledger: workspace/ when live, the test's temp dir under test
    where = {"ledger": OBJECTIONS.parent / "scope-quality.jsonl",
             "defects": OBJECTIONS.parent / "scope-defects"}
    m = re.search(r"^\s*TASK:\s*(t\d+)", brief_text or "", re.M)
    chunk = re.sub(r"^brief-|\.txt$", "", Path(str(brief_path)).name)
    if verdict == "SATISFIABLE":
        scope_quality.record("preflight", "satisfiable", task=m.group(1) if m else "", chunk=chunk,
                             source="preflight_brief", reviewer=model, **where)
    elif verdict == "NOT SATISFIABLE":
        scope_quality.record("preflight", "objected", task=m.group(1) if m else "", chunk=chunk,
                             defect_class="brief-unsatisfiable", evidence=(note or reply or ""),
                             source="preflight_brief", reviewer=model, **where)



def _verdict_of(reply_text):
    """The verdict the model actually stated, for the record -- not the decision.

    `decide()` returns an exit code and deliberately collapses everything that is
    not a clean approval into "block". The ledger needs the two kept apart: a
    reply that said SATISFIABLE and was overridden by its own MISSING list is a
    different failure from one that said NOT SATISFIABLE, and a corpus that
    records both as "blocked" cannot tell them apart. That override is the exact
    shape this gate exists to catch, so it must stay visible in the record.
    """
    for line in (reply_text or "").split("\n"):
        stripped = line.strip()
        if stripped.upper().startswith("VERDICT:"):
            return stripped.split(":", 1)[1].strip().upper() or "EMPTY"
    return "NO VERDICT LINE"


def decide(reply_text):
    """Return the exit code for a model reply: 0 for a recognised approval, 2 otherwise.

    The verdict is the VALUE on the VERDICT line, not a substring anywhere in the reply.
    An unrecognised verdict blocks -- a gate whose unknown-input behaviour is "allow"
    is not a gate.
    """
    if not reply_text or not reply_text.strip():
        return 2
    verdict = None
    for line in reply_text.split("\n"):
        stripped = line.strip()
        if stripped.upper().startswith("VERDICT:"):
            verdict = stripped.split(":", 1)[1].strip().upper()
            break
    if verdict is None:
        return 2
    if verdict == "NOT SATISFIABLE":
        return 2
    if verdict != "SATISFIABLE":
        return 2
    # SATISFIABLE: the MISSING list still overrides -- read from the MISSING LINE, the same
    # way the verdict is read from the VERDICT line. It used to take the first "missing:"
    # ANYWHERE, case-blind, so prose like "No dependency is missing: the import chain
    # resolves..." was parsed as the gap list and blocked its own approval. Four SATISFIABLE
    # reviews ended stopped=True that way (t287 twice, p19, p24 -- 2026-09-23), each one
    # ending "MISSING: nothing". The last MISSING line is the answer; prose is not.
    # "MISSING:" alone on its line means the list follows on the next lines -- that is a gap.
    gaps = ""
    lines = reply_text.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.upper().startswith("MISSING:"):
            gaps = stripped[8:].strip()
            if not gaps:
                gaps = next((l.strip() for l in lines[i + 1:] if l.strip()), "")
    has_gap = bool(gaps) and gaps.strip().lower().rstrip(".") not in (
        "nothing", "none", "n/a", "-")
    if has_gap:
        return 2
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--brief", required=True, help="a file holding the brief text")
    ap.add_argument("--files", nargs="+", required=True)
    # NOT required. A brief that asks for a DOCUMENT -- a plan, an audit, a judgement --
    # has no suite to make green, and the only way to satisfy a required --target for one
    # is to name a file that is not really the target. That is the pattern this whole
    # script exists to stop, reproduced inside the prevention. Omitting it says so plainly
    # to the model instead, which is what the briefing skill requires of a brief with no
    # suite: "no suite exists" is a sentence you write, not a gap you paper over.
    ap.add_argument("--target", default=None,
                    help="the target suite; omit for a document brief with no suite")
    ap.add_argument("--model", default=roles.for_job("preflight", "qwen3.8:27b-mtp-bf16"))
    ap.add_argument("--num-predict", type=int, default=12000,
                    help="output ceiling; raise it for a brief with several large files")
    a = ap.parse_args()

    if not a.files:
        print("\n  no --files given, so there is nothing to check. Refusing rather than")
        print("  reporting a pass over an empty set (L-146).\n")
        return 2
    # Read ONCE. The ledger hashes exactly the bytes the model was shown; reading
    # the file twice would let an edit between the two reads record a hash for a
    # brief nobody reviewed.
    brief_text = Path(a.brief).read_text()
    checkable = list(a.files) + ([a.target] if a.target else [])
    missing = [p for p in checkable if not (REPO / p).is_file()]
    if missing:
        # The cheapest check does not need a model at all, and catches case 3 of the seven.
        print(f"\n  VERDICT: NOT SATISFIABLE (checked without asking the model)")
        print(f"  These do not exist: {missing}\n")
        # LOGGED LIKE ANY OTHER OBJECTION. This is a real objection to a real brief
        # and one of the most common kinds -- a path that does not exist. Leaving it
        # out because no model was involved would bias the corpus towards whatever
        # the model happens to catch, which is the opposite of what it is for.
        record_review(a.brief, brief_text, a.files, a.target, a.model,
                      a.num_predict, "NOT SATISFIABLE", "", 0.0, None, True,
                      note=f"checked without asking the model; missing: {missing}")
        return 2

    # t1230: EXECUTE THE CLAIMS before the model is asked. A brief that asserts a precondition
    # the tree does not meet stops here, in words, costing no model time and making no model
    # call -- which is why this sits BEFORE the lease is taken.
    findings = precondition_findings(brief_text, a.files, a.target)
    if findings:
        print('  VERDICT: NOT SATISFIABLE (checked without asking the model)')
        print('  Preconditions this brief asserts are not true of the tree:')
        for f in findings:
            print('  ' + f)
        print('')
        # RECORDED LIKE ANY OTHER OBJECTION (2026-10-28, RCA rca-no-verdict-preflight-holds). The
        # missing-files refusal twenty lines above already does this, with the reason written out:
        # "a real objection to a real brief ... leaving it out because no model was involved would bias
        # the corpus". The logging was simply not carried across to this second mechanical refusal, so
        # SIX briefs were held as "the reviewer is broken" when the reviewer had answered and the answer
        # was thrown away: the dispatcher counts runs minus RECORDED rows (dispatch_loop.py:1789-1800),
        # and a refusal that writes nothing is indistinguishable from a crash.
        record_review(a.brief, brief_text, a.files, a.target, a.model, a.num_predict,
                      "NOT SATISFIABLE", "", 0.0, None, True,
                      note="checked without asking the model; preconditions not met: " + "; ".join(findings))
        return 2

    shown = "\n\n".join(_show(p) for p in a.files)
    if a.target:
        # WAS read_text()[:12000] -- a SILENT cut, in the reviewer whose own _show() exists
        # because a silent cut once made it tell the model a file was included "in full".
        # The same defect, in the same file, on the line next to the fix. 2026-09-14.
        target_text = _show(a.target)
    else:
        target_text = (
            "NO TARGET SUITE EXISTS. This brief asks for a document -- a judgement, a plan\n"
            "or an audit -- not for a suite to be made green. Questions 3 and 4 are about a\n"
            "test suite and do not apply; skip them and say so rather than inventing a suite\n"
            "to answer them about. Questions 1, 2, 5 and 6 apply in full, and 6 most of all.")
    deps, omitted, refs = evidence(a.target, a.files, brief_text)
    # NAMED EVEN WHEN THERE ARE NONE. "(none)" is an answer to question 1; an empty section
    # would read as something withheld, and this reviewer is right to distrust a gap.
    deps_text = ("\n\n".join(_show(p) for p in deps) if deps else
                 "(none -- the target suite imports nothing that lives in this repository)")
    if omitted:
        deps_text += ("\n\nNOT SHOWN, to keep this review inside the model's window: "
                      + ", ".join(omitted)
                      + "\nThey exist in the repository. Do not treat them as missing; if your"
                        " answer\ndepends on what is in one of them, say which and say why"
                        " instead of assuming.")
    if refs:
        deps_text += ("\n\n--- FILES THE WORK ORDER NAMES (they EXIST; read-only context, not part"
                      " of the change) ---\n\n" + "\n\n".join(_outline(p) for p in refs))
        print(f"  files the brief names: showing {len(refs)} -- {', '.join(refs)}")
    if deps or omitted:
        print(f"  target imports: showing {len(deps)}"
              + (f", naming {len(omitted)} unshown" if omitted else "")
              + f" -- {', '.join(deps) or 'none'}")
    prompt = QUESTION.format(brief=brief_text, files=shown, target=target_text,
                             deps=deps_text)
    import skills                                   # plan 3c-i; no tools here, so nothing more to open
    prompt, _sk = skills.with_skills(prompt, "preflight")
    # A HELD LEASE IS A WAIT, NOT A REVIEW (t387, p51). Uncaught, Held died with a traceback
    # (exit 1), and the scheduler counted each as a verdictless review: p52 was held "reviewer
    # is broken" on 2026-09-25 after two preflights hit Q4's manager lease. Exit 4 is
    # dispatch_loop's YIELD -- retry next tick, never counted. No objection is written.
    try:
        lease = modellease.acquire(LEASE, model=a.model, purpose="preflight review",
                                   ttl_seconds=2400)
    except modellease.Held as e:
        print(f"HELD: {e}; wait, retry next tick", file=sys.stderr)
        sys.exit(4)
    try:
        t0 = time.time()
        out, why = ask(prompt, a.model, num_predict=a.num_predict)
    finally:
        modellease.release(LEASE, lease)

    print(f"\n{out.strip()}\n")
    elapsed = time.time() - t0
    print(f"  ({elapsed / 60:.0f} min, done_reason={why})")
    if why == "length":
        print("  TRUNCATED -- the review was cut off. Treat as no answer, not as approval.")
        record_review(a.brief, brief_text, a.files, a.target, a.model, a.num_predict,
                      "TRUNCATED", out, elapsed, why, True,
                      note="review cut off at num_predict; not an answer")
        return 2
    # THE STOP IS NOT THE MODEL'S VERDICT ALONE. A reply that lists something missing and
    # still says SATISFIABLE is the exact shape we are trying to stop: complying by inventing.
    # So the gap list is authoritative over the verdict.
    rc = decide(out)
    # EVERY review, not only the ones that stopped. Without the satisfiable rows
    # there is no denominator and no rate can be computed from this file.
    record_review(a.brief, brief_text, a.files, a.target, a.model, a.num_predict,
                  _verdict_of(out), out, elapsed, why, rc != 0, deps=[*deps, *refs])
    if rc != 0:
        print("\n  ==> STOP. Do not dispatch this brief.\n")
        print("  What to do, by what it objected to:")
        print("    a file or suite does not exist   -> create it first as its own step,")
        print("                                        or fix the path in the brief")
        print("    two assertions contradict        -> fix the ORACLE, then ask again")
        print("    the target needs other files     -> split the work, or narrow the oracle")
        print("                                        so one file can make it green")
        print("    the oracle pins today's defect   -> rewrite it as a property, not an")
        print("                                        instance, then ask again")
        print("    a behaviour change is smuggled   -> remove it and file it separately")
        print("    an unrecognised verdict          -> the model's answer is not in the")
        print("                                        required shape; ask again")
        print("\n  Then run this again. Re-asking is cheap; a fabricated answer is not.\n")
        return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
