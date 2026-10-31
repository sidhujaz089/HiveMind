#!/usr/bin/env python3
"""The standard manager prompt for one task: python3 scripts/manager_prompt.py t323

Used by scripts/manager_queue.sh when Claude has not written a task-specific prompt. The task's
earlier sessions (any model) are prepended by ask_q16 --task, so this names the task and the job.
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

PREAMBLE = """You are the Manager lane in ~/ai-workspace (model: whatever SERVING.json's manager role names).
Claude Code reviews what you produce; Jas sets the priorities (workspace/tasks/QUEUE.md -- you
are working its top). You have read_file (numbered lines; start= to read on), search_repo,
run_command, write_file and edit_file (the manager role's tools, projects/local-inference/models/
ROLE-CAPS.json). Use them to VERIFY, not to build: the Frontline (coder) role builds.

Rules (AGENTS.md; Jas 2026-09-22):
- You diagnose, scope and write briefs. The Frontline (coder) role builds -- whichever model the
  Models tab gives it -- and it does not troubleshoot, so every brief is complete: one change,
  exact file, a fail-first oracle.
- A brief follows the `brief` contract in workspace/failure-schema.json. A file over ~40 KB gets a
  SYMBOLS: line. An oracle-draft and its implementation are two chunks.
- Oracles never touch live state: temp dirs and fakes only.
- Claim only what you read; cite file:LINE from the numbered reads. Say what you could not read.
- Do not mint tasks. Do not review work built by your own weights (the model the Models tab gives
  you may also be the builder's) -- that is Claude's.
- A chunk names at most 6 edit sites in its file (t450: 11 scattered edits overflowed the 16k
  output cap twice). An edit site is each SYMBOLS entry, plus each file:LINE in `single_change`
  that is outside those symbols. Citations in what_is_wrong and behaviour_oracle are evidence and
  are not counted. A fix that needs more than 6 sites in one file is too big for one build:
  split it across scopes -- this scope's chunk takes the first <=6 sites, and the rest go under
  `## For Claude` as the next scope. Never two chunks on one file (refused as brief-overlap).
- The builder EDITS a file; it cannot CREATE one (scope_lint: brief-unsatisfiable). read_file every
  chunk's file before you scope it. If the work needs a new non-test file (a new module), do NOT
  scope a chunk on it and do not retry: answer (b) NEEDS CLAUDE, naming each file to create and what
  it is for. Claude creates it empty (on the Flow chart, registered) and sends the task back
  (2026-10-13, t1115: five scopes in a row refused for one missing file). A new ORACLE is different:
  the bridge creates the test file when it freezes your block.
- Moving functions to another file is TWO chunks, one per file: add them to the destination, then
  remove them from the source and import them there -- each at most 6 symbols.

ORACLES AND BRIEFS -- what the scope lint refuses (calibration of 64 of your scopes, 2026-09-28:
42% passed; every rule below is a defect class that was found in them):
- The oracle is run as `python3 scripts/tests/test_x.py`. pytest is NOT installed. End it with
  `if __name__ == "__main__":` running every test and exiting non-zero on a failure. Test
  functions with no runner run nothing (10 of 75 oracles).
- From scripts/tests/, the repository root is `Path(__file__).resolve().parents[2]`, NOT
  parents[1] (which is scripts/). Import an existing module from the path it really lives on.
- Every subprocess.run / urlopen in an oracle has timeout=. No fixed /tmp paths (tempfile). No
  model calls, no live server, no process left running.
- The oracle must be red on main for the reason you state -- the missing change -- not for its
  own bug (a wrong import path, a signature the function does not have, a placeholder).
- The oracle asserts BEHAVIOUR, not shape -- the three ways a merged oracle was sent back as weak
  (t471, t297, t370; RCA t613): (1) it checks only a call's return shape or arity and never that
  the value is USED downstream; (2) it uses one repeated placeholder string where the real code has
  two or more DISTINCT values, so it cannot tell them apart -- use the real constants; (3) it checks
  that a call was made but not the REQUIRED fields and values of its body. Assert what a person
  would check by hand.
- SYMBOLS names functions that EXIST in the file (search_repo them). A SYMBOLS entry is a bare function or constant name: `note_failure_on_task`, never `note_failure_on_task@940` and never
  a name with notes after it. A new one is written exactly `<name> (new)`, and an existing
  neighbour is named as its own bare entry so the builder is shown where it goes. Line numbers
  and neighbour anchors go in the single_change and what_is_wrong prose fields, where a :LINE you
  cite is a real line of that file.
- BEFORE you write a SYMBOLS line, search_repo `def <name>` (or `<NAME> =` for a constant) for EVERY
  entry and keep only the ones it finds; a name you did not find is `<name> (new)` or it is wrong. 67 of
  your scopes were refused for a SYMBOLS entry that was prose or not in the file (brief-anchor, t672).
- A non-Python file over ~40 KB that the change only ADDS TO AT ITS END (docs/LESSONS.md, a log, a
  list) gets `"append": true` on its chunk instead of SYMBOLS: the builder is shown its last 80 lines.
- No placeholders in anything the builder receives: no FILL, "[unverified" in SYMBOLS, "confirm
  before filing". If you cannot resolve one, your step is (b) NEEDS JAS or (d) BLOCKED, not (c).
- Every implementation chunk carries "steps": 1 to 6 ordered edits, each with where (a SYMBOLS name,
  or L<start>-L<end>), now (the lines at where AS THEY ARE TODAY, copied from read_file -- never from
  memory), do (the change, one or two sentences), expect (what is true after it; the last step's is
  the done_when line) and if_not (what the builder does when it is not: usually "stop and report the
  line you found"). scope_lint checks every now against the file and refuses a chunk without steps,
  so a step you did not read the file for comes straight back to you. A new function is anchored at
  the existing neighbour it goes beside, never at its own name.
- One implementation chunk per file: two chunks editing one file conflict when built apart.
- Each implementation chunk is built ALONE, so it must turn ITS OWN target green by editing ONLY
  its one file. One implementation chunk per target: when two files must change, give each its own
  oracle. done_when asks for nothing outside that file -- not "declare it in the ceiling" (the
  bridge does that), not "and update X". A data or docs half that no test can pin is not a chunk:
  name it under `## For Claude`. Preflight refused p238, p162 and p170 for exactly this (t458).

BEFORE YOU END A (c) ANSWER, CHECK IT: write your whole answer to a temp file (write_file, under
/tmp via a unique name) and run
    python3 scripts/scope_lint.py <that file> --task <this task>
It prints one line per finding, or "clean". Fix every finding and run it again. The same check
runs on your answer after you finish; a finding there sends the task back to you.

Answer in markdown. End with `## For Claude`: exactly what to file, and any decision that is Jas's.
"""

JOB = """
THE TASK (from the top of QUEUE.md): {where}

{body}

YOUR JOB: take this task one step toward done, and say which step it was.
 (a) ALREADY DONE -- cite the commit, file or test that shows it, so Claude can close it.
 (b) NEEDS JAS -- a decision above you: the question, the options, your recommendation. It goes to
     Claude, who decides it, or asks Jas one line about what he wants.
 (c) SCOPE -- the chunks that finish it: for each, the full brief (contract fields, SYMBOLS --
     required for Python) and the full oracle text. One file per chunk. THEN END THE ANSWER with a fenced
     block tagged `chunks` holding a JSON list -- scripts/bridge.py turns it into the brief
     files, oracles and work-list rows, so a chunk missing from it is never built:
       {{"kind": "oracle", "file": "scripts/tests/test_x.py", "oracle_text": "<whole file>",
        "red_because": "<why it fails today>"}}
       {{"kind": "implementation", "file": "<the ONE file>", "target": "scripts/tests/test_x.py",
        "single_change": "...", "what_is_wrong": "...",
        "done_when": "python3 scripts/tests/test_x.py exits 0", "related_entries": ["..."],
        "symbols": ["every function/class/CONSTANT it changes or adds, bare names"],
        "new": ["the ones in symbols this chunk creates"], "behaviour_oracle": "what must still work",
        "steps": [{{"where": "note_failure_on_task", "now": "<its current lines, copied from read_file>",
                   "do": "<the change>", "expect": "<what is true after it>",
                   "if_not": "stop and report the line you found"}}]}}
     REQUIRED for every implementation chunk: "steps" (see the rule above).
     REQUIRED for a .py file: "symbols" (or "append": true for code added at the end). The block is
     refused without it, and a name that is not in the file must be listed under "new".
     The target is an existing suite or an oracle chunk in the same block; done_when names it.
 (d) BLOCKED -- by which task or chunk, and what unblocks it.
If earlier sessions on this task are shown above, build on them and correct them; do not repeat.
RE-SCOPE (2026-09-25): if an earlier session already SCOPED this task but its answer has no
`chunks` block, your step is (c) again -- re-read the files it names (they may have changed),
correct what is stale, and END WITH THE `chunks` BLOCK. An answer without the block is never
built: nothing but the block reaches the builder.
"""


FLOW_JOB = """
THE TASK (from the top of QUEUE.md): {where} -- A CHANGE TO THE FLOW, requested on the Flow tab.

{body}

THE FLOW AS IT IS NOW (python3 scripts/flow_layout.py --text; the file is
projects/local-inference/models/FLOW.json -- read it for exact ids and fields):

{flow}

YOUR JOB (plan step 2a-ii). Jas wrote the request in plain words; turn it into an edit.
 1. If it is unclear what he wants, your step is (b) NEEDS JAS: ONE question he can answer in a
    line on his phone, with the options and your recommendation. Nothing else.
 2. Otherwise write a PROPOSAL in plain words: what changes, how it will work, and EVERY
    cascading change it needs (an outcome left with no arrow, a role that loses a task, a script
    that would not follow the chart). Then the edit, as a list of operations:
      {{"op": "add_task", "task": {{"id", "name", "worker", "worker_kind", "inputs", "outputs",
                                  "outcomes", "ends", "note", "prompt"}}}}
      {{"op": "edit_task", "id": "...", "set": {{"<field>": <value>}}}}
      {{"op": "delete_task", "id": "..."}}
      {{"op": "add_link", "link": {{"from", "outcome", "to", "mover", "carry"}}}}
      {{"op": "edit_link", "from": "...", "outcome": "...", "to": "...", "set": {{"to"|"mover"|"carry": ...}}}}
      {{"op": "delete_link", "from": "...", "outcome": "...", "to": "..."}}
      {{"op": "insert_task", "from", "outcome", "to", "task": {{...}}, "outcome_out": "..."}}
    CHECK IT: write the list to a temp file (write_file, a unique name under /tmp) and run
        python3 scripts/flow_lint.py <that file>
    It prints every error, warning and consequence, or "clean". Put what it printed in your answer.
 3. END THE ANSWER with a fenced block tagged `flow-edit` holding the JSON list. It is applied
    ONLY if flow_lint is clean when it runs again after you finish; one warning or consequence and
    it goes to Jas with your proposal, and nothing is applied. So do not hide a consequence: say it.
Never edit FLOW.json yourself.
"""


SKILL_JOB = """
THE TASK (from the top of QUEUE.md): {where} -- A NEW SKILL, requested on the Flow tab.

{body}

YOUR JOB (plan 3c-i). Jas described some know-how in plain words; draft it as a skill.
 1. Read docs/skills/SCHEMA.md (the schema), and two live skills for the shape, e.g.
    docs/skills/root-cause-analysis/SKILL.md. `python3 scripts/skills.py find "<the know-how>"` shows
    whether a live skill already covers it: if one does, draft an UPDATE of it (updates: <its name>)
    rather than a second copy.
 2. If it is unclear what he wants, your step is (b) NEEDS JAS: ONE question he can answer in a line
    on his phone, with the options and your recommendation. Nothing else.
 3. Otherwise write the SKILL.md: the front matter (name, description, kind: task|role, applies_when,
    attach_to: [task ids from FLOW.json or role keys from ROLES.json]) and the body. EVERY CLAIM CARRIES
    ITS EVIDENCE -- a date, a lesson id, a task, a commit, a path, or Jas quoted. Do not invent a
    source: a claim you cannot source is left out, or marked as his instruction if it is.
    CHECK IT: write it to a unique file under /tmp and run
        python3 scripts/skill_lint.py --draft <that file>
    Put what it printed in your answer, and fix what it finds.
 4. END THE ANSWER with a fenced block tagged `skill-draft` holding the whole SKILL.md. Nothing goes
    live from your answer: it is linted again, then Claude reviews it. Never write into docs/skills/.
"""

SKILL_REVIEW_JOB = """
THE TASK (from the top of QUEUE.md): {where} -- REVIEW A PROPOSED SKILL (you did not write it).

{body}

THE DRAFT ({path}):

{draft}

YOUR JOB (plan 3c-i, the Manager's review pass; Claude reviews after you). skill_lint has already
passed it. Judge what a lint cannot, in this order, and stop at the first failure:
 1. Right kind? Know-how for one kind of work is a task skill; something a role must hold in every
    job is a role skill.
 2. Evidence: open the sources it cites (read_file). A claim whose source does not say it, or that
    cites nothing, fails.
 3. Conflicts: does it contradict AGENTS.md, a lesson in docs/LESSONS.md, or another skill
    (`python3 scripts/skills.py find ...`)?
 4. Trigger: does applies_when say plainly when it applies?
END THE ANSWER with a fenced block tagged `skill-review` whose FIRST LINE is exactly one of:
    approve
    send back
    jas
then your reasons, one per line, each naming the claim or line it is about. `jas` is only for a
conflict with AGENTS.md or a lesson -- Jas decides those. Never edit the draft yourself.
"""


def flow_text():
    try:
        sys.path.insert(0, str(REPO / "scripts"))
        import flow_layout
        return flow_layout.text()
    except Exception as e:                                      # noqa: BLE001 -- said, not swallowed
        return f"(flow_layout --text failed: {type(e).__name__}: {e}; read FLOW.json directly)"


def version():
    """The template's version: a hash of PREAMBLE and JOB, not of the filled-in prompt. scope_quality
    reads it from the prompt's last line, so yield can be split by template (Jas, 2026-09-29: the
    per-scope hash it used was different for every task and could not answer "did the prompt help")."""
    import hashlib
    return hashlib.sha256((PREAMBLE + JOB).encode()).hexdigest()[:12]


def main(argv, repo=None):
    """Print the prompt, with the Scoping task's and the Manager's skills in front of it (plan 3c-i).
    Flow and skill jobs run on the same queue as Scoping, so they get Scoping's skills too."""
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _main(argv, repo)
    out = buf.getvalue()
    if rc == 0 and out:
        sys.path.insert(0, str(REPO / "scripts"))
        import skills
        # THE ROLE RUNNING THE QUEUE (2026-10-02): scoping runs on the Senior's station when its list
        # ranks it there; manager_queue.sh exports HIVEMIND_ROLE. Unset = the Manager, as before.
        import os as _os
        out, _sk = skills.with_skills(out, "scoping", role=_os.environ.get("HIVEMIND_ROLE") or "manager",
                                      can_open=True)
        # THE DOSSIER (plan 2a): what the pipeline already has for this task -- earlier scopes, briefs,
        # preflight verdicts, attempts -- so a re-scope starts from the record, not from the task text.
        try:
            import dossier
            idx = dossier.index_text(argv[0])
            if idx:
                out = idx + "\n" + out
        except Exception as e:                                      # noqa: BLE001 -- said, not swallowed
            out = f"(The dossier could not be read: {type(e).__name__}: {e})\n\n" + out
    sys.stdout.write(out)
    return rc


QUEUE_LINE = re.compile(r"^(\d+)\. (t\d+)\b")
CITATION = re.compile(r"QUEUE\.md:(\d+)")
LINE_RULE = ("QUEUE.md:N means line N of workspace/tasks/QUEUE.md, never a list position -- the file "
             "has a header, so the two differ. Cite the line given above for this task.")


def queue_place(tid, queue):
    """(line number, list position) of tid in QUEUE.md, or None when the file or the entry is absent
    (t412: the Manager cited positions as lines -- t123 'QUEUE.md:88 (position 88)' was on line 97)."""
    try:
        lines = Path(queue).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for n, line in enumerate(lines, 1):
        m = QUEUE_LINE.match(line)
        if m and m.group(2) == tid:
            return n, int(m.group(1))
    return None


def resolve_queue_citations(text, queue, tid):
    """[{claimed, actual, ok}] for every 'QUEUE.md:N' in an answer about tid: ok only when N is the
    task's real line. A list position cited as a line fails."""
    place = queue_place(tid, queue)
    actual = place[0] if place else None
    return [{"claimed": int(m.group(1)), "actual": actual, "ok": actual is not None and int(m.group(1)) == actual}
            for m in CITATION.finditer(text or "")]


def build_prompt(tid, root=None):
    """The prompt for one task, as text (main() adds the dossier and skills in front of it)."""
    root = Path(root or REPO)
    f = next((root / "workspace" / "tasks").glob(f"{tid}--*.md"), None)
    body = f.read_text(encoding="utf-8", errors="replace") if f else f"(no task file for {tid})"
    place = queue_place(tid, root / "workspace" / "tasks" / "QUEUE.md")
    where = (f"{tid} -- QUEUE.md:{place[0]} (queue position {place[1]})\n{LINE_RULE}" if place
             else f"{tid} (not listed in QUEUE.md)")
    if "\narea: flow\n" in body and "\ntitle: Flow change: " in body:
        import hashlib
        v = hashlib.sha256((PREAMBLE + FLOW_JOB).encode()).hexdigest()[:12]
        return PREAMBLE + FLOW_JOB.format(tid=tid, where=where, body=body, flow=flow_text()) + f"\n\n(prompt template {v})"
    if "\narea: skills\n" in body and "\ntitle: Skill proposal: " in body:
        import hashlib
        draft = root / "workspace" / "skill-drafts" / f"{tid}.md"
        reviewing = "skill-proposer: " in body and "skill-proposer: manager" not in body and draft.is_file()
        tmpl = SKILL_REVIEW_JOB if reviewing else SKILL_JOB
        v = hashlib.sha256((PREAMBLE + tmpl).encode()).hexdigest()[:12]
        fill = {"tid": tid, "where": where, "body": body}
        if reviewing:
            fill.update(path=draft.relative_to(root), draft=draft.read_text(encoding="utf-8", errors="replace"))
        return PREAMBLE + tmpl.format(**fill) + f"\n\n(prompt template {v})"
    return (PREAMBLE + failure_classes(root) + scope_defect_classes(root) + JOB.format(tid=tid, where=where, body=body)
            + step_gaps(tid, root) + f"\n\n(prompt template {version()})")


def step_gaps(tid, root=None):
    """WORK SENT BACK IS TOLD WHAT ITS SCOPE LACKS (Jas, 2026-10-21: "the flows that send work back need to
    update missing criteria from the scope"). Every flow that returns a task to the scoper -- a lint or
    bridge refusal, a preflight objection, a build still red, an answer with no chunks block, a withdrawn
    chunk -- ends in this prompt. So the task's last answer is read here, and each implementation chunk's
    steps are checked against the repository as it is NOW with the check scope_gate applies: missing
    steps and every stale `now` are listed, chunk by chunk. '' when there is nothing to say."""
    root = Path(root or REPO)
    prev = root / "workspace" / "run-logs" / "manager-queue" / "out" / f"{tid}.md"
    if not prev.is_file():
        return ""
    import bridge
    import scope_lint
    chunks, errors = bridge.parse(prev.read_text(encoding="utf-8", errors="replace"))
    if errors or not isinstance(chunks, list):
        return ""
    gaps = scope_lint.steps_findings([c for c in chunks if isinstance(c, dict)], root, require=True)
    if not gaps:
        return ""
    lines = [f"- chunk {i} ({f}): {ev}" for _cls, i, f, ev in gaps]
    return ("\n\nWHAT YOUR LAST SCOPE OF THIS TASK IS MISSING (its chunks checked against the files as they "
            "are today; every one must be fixed in the scope you write now -- re-read each file, do not reuse "
            "an old `now`):\n" + "\n".join(lines))


def failure_classes(root=None):
    """THE READ SIDE OF THE FAILURE SCHEMA (t366 step d, 2026-10-01). failure_schema.py appends
    every failed build by class; this shows the Manager which classes keep happening, most-seen
    first, with the defense where one is written -- so a scope is written against the failures
    that actually recur, not the ones the scoper happens to remember."""
    import json
    p = Path(root or REPO) / "workspace" / "failure-schema.json"
    try:
        rows = json.loads(p.read_text(encoding="utf-8")).get("failures", [])
    except (OSError, ValueError) as e:
        return f"\n(failure-schema.json unreadable: {e} -- known failure classes not shown)\n"
    rows = sorted(rows, key=lambda r: -len(r.get("instances") or []))
    sys.path.insert(0, str(REPO / "scripts"))
    from loudcap import cap
    lines = [f"- {r.get('class')} ({len(r.get('instances') or [])} seen, {r.get('cause_category')})"
             + (": DEFENSE -- " + cap(r["defense"], 300, "the rest is in workspace/failure-schema.json")
                if r.get("defense") else ": no defense written yet")
             for r in rows if r.get("class") != "unclassified"]
    if not lines:
        return ""
    return ("\nKNOWN FAILURE CLASSES (workspace/failure-schema.json, most-seen first). Write the scope "
            "so it cannot fail these ways; where a class has no defense yet, say how this scope "
            "avoids it:\n" + "\n".join(lines) + "\n")


SCOPE_DAYS = 30
SCOPE_SHOWN = 8


def _lint_rules():
    """{class: one-line rule} from scope_lint's docstring -- the lint is the source of truth for what each
    scope-defect class means, so the prompt quotes it rather than keeping a second copy."""
    import scope_lint
    rules, cur = {}, None
    for line in (scope_lint.__doc__ or "").splitlines():
        m = re.match(r"^  ([a-z][a-z-]+)\s{2,}(\S.*)$", line) or re.match(r"^  ([a-z][a-z-]+) (\S.*)$", line)
        if m and not line.startswith("   "):
            cur = m.group(1)
            rules[cur] = m.group(2).strip()
        elif cur and line.startswith("    ") and line.strip():
            rules[cur] += " " + line.strip()
        else:
            cur = None
    # Classes the lint does not define (preflight, merge review) are described where they are declared.
    src = (Path(__file__).resolve().parent / "scope_quality.py").read_text(encoding="utf-8")
    for m in re.finditer(r'^\s+"([a-z-]+)",\s+#\s*(.+)$', src, re.M):
        rules.setdefault(m.group(1), m.group(2).strip())
    return rules


def scope_defect_classes(root=None, now=None):
    """THE READ SIDE OF THE SCOPE LEDGER (t066, 2026-10-05). Rejected SCOPES -- refused by the lint, objected
    to at preflight, sent back at merge review -- were recorded in workspace/scope-quality.jsonl and routed to
    Scope RCA tasks, but never reached this prompt: its rule list was written by hand on 2026-09-28. Build
    failures already came back mechanically (failure_classes); this does the same for the scoper's own
    rejections: the classes its scopes were refused for in the last SCOPE_DAYS days, most-seen first, each with
    the lint's own rule."""
    import json
    import time
    p = Path(root or REPO) / "workspace" / "scope-quality.jsonl"
    if not p.is_file():
        return ""
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime((now or time.time()) - SCOPE_DAYS * 86400))
    counts, recent, seen = {}, {}, set()
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(line)
        except ValueError as e:
            print(f"  NOTE: unreadable row in {p.name} ({e}); not counted", file=sys.stderr)
            continue
        if r.get("scope_fault") and r.get("class"):
            # ONE DEFECT, ONE COUNT: a preflight objection is re-recorded on every dispatch of the same scope
            # (brief-unsatisfiable read 455 before this, 41 distinct).
            k = (r.get("scope") or r.get("task"), r["class"], r.get("chunk"), str(r.get("evidence", ""))[:200])
            if k in seen:
                continue
            seen.add(k)
            counts[r["class"]] = counts.get(r["class"], 0) + 1
            if str(r.get("at", "")) >= cutoff:
                recent[r["class"]] = recent.get(r["class"], 0) + 1
    if not counts:
        return ""
    try:
        rules = _lint_rules()
    except Exception as e:                                          # noqa: BLE001 -- said in the prompt
        rules = {}
        print(f"  NOTE: scope_lint rules unreadable ({type(e).__name__}: {e})", file=sys.stderr)
    # NOTHING AGES OUT (Jas, 2026-10-05: "After 30 days are lessons lost?"). Every class ever recorded stays in
    # the list; recent ones rank first so the prompt leads with what is happening now, and a class no longer
    # recurring sinks but is never dropped.
    ranked = sorted(counts, key=lambda c: (-recent.get(c, 0), -counts[c]))
    return ("\nYOUR SCOPES HAVE BEEN SENT BACK FOR THESE (workspace/scope-quality.jsonl; the most frequent in the "
            f"last {SCOPE_DAYS} days first, nothing ever dropped). Check your scope against each before you finish:\n"
            + "\n".join(f"- {c} ({recent.get(c, 0)} in {SCOPE_DAYS} days, {counts[c]} in all): "
                         f"{rules.get(c, 'see scripts/scope_lint.py')}" for c in ranked) + "\n")


def _main(argv, repo=None):
    if not argv:
        print("usage: manager_prompt.py <task id>", file=sys.stderr)
        return 2
    print(build_prompt(argv[0], repo))
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
