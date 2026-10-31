#!/usr/bin/env python3
"""Batch scoping: tasks that change the same file are scoped in ONE Manager call (t1177).

    python3 scripts/scope_batch.py pick <first>                 # ids to scope together with <first>
    python3 scripts/scope_batch.py prompt <id> [<id> ...]       # the batched prompt, on stdout
    python3 scripts/scope_batch.py split <answer.md> <outdir> <id> [<id> ...]   # one answer per task

WHY (Jas, 2026-10-14: "Batching should be a task before scoping"). t1055 measured the backlog: tasks
that name the same target files are common -- scoping them together saves 23% of target-file reads and
36% of the bytes, because the shared files (coder.py, dispatch_loop.py, bridge.py ...) are the big ones.
A Manager call reads each file it needs once per CALL, so the saving only exists if the tasks share a
call. Sessions (task_sessions) are a written record, not a live context, so they cannot carry it.

HOW, reusing everything downstream:
  1. The queue picks its next task exactly as before (manager_next). pick() adds up to BATCH_MAX - 1
     other tasks the queue would also take that share a target file WITH IT (named in their
     descriptions, not their notes). A flow or skill task is never batched (its own prompt).
  2. The worker claims every member or none (scoping_lanes.claim_batch), so two scoping lanes can
     never hold overlapping batches.
  3. ONE prompt: the first task's full standard prompt (skills, dossier, rules), then each other task's
     text and dossier, and the rule: one section per task, each ending in its own `chunks` block.
  4. split() cuts the answer into workspace/run-logs/manager-queue/out/<task>.md per task, and each
     goes through manager_note and the bridge EXACTLY as a single scope does. A task whose section is
     missing gets no file and is simply scoped again later on its own.
OFF SWITCH: workspace/run-logs/manager-queue/BATCH_OFF (any content) -- pick() then returns <first> alone.
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "scripts")]
Q = REPO / "workspace" / "run-logs" / "manager-queue"
TASKS = REPO / "workspace" / "tasks"
BATCH_MAX = 3            # a 32k-token answer holds about three full scopes with their oracles
PATH = re.compile(r"\b((?:scripts|projects)/[\w./-]+\.(?:py|sh|js|json))\b")
HEADER = re.compile(r"^=== TASK (t\d+) ===\s*$", re.M)


def _body(tid, root=None):
    f = next(((Path(root) if root else TASKS)).glob(f"{tid}--*.md"), None)
    return f.read_text(encoding="utf-8", errors="replace") if f else ""


def special(body):
    """A flow-change or skill-proposal task: it has its own prompt template and is never batched."""
    return (("\narea: flow\n" in body and "\ntitle: Flow change: " in body)
            or ("\narea: skills\n" in body and "\ntitle: Skill proposal: " in body))


def target_files(body, root=None):
    """The target files a task's DESCRIPTION names (the text above '## Notes'): existing repository
    code, test suites excluded. Notes are left out on purpose: build failures, RCAs and dispatch notes
    name dispatch_loop.py or coder.py in passing on almost every task, and counting them put 39 of the
    40 next tasks into batches on 2026-10-14 -- a batch of tasks that do not change the same file."""
    root = Path(root or REPO)
    desc = (body or "").split("\n## Notes", 1)[0]
    return {f for f in PATH.findall(desc) if "/tests/" not in f and (root / f).is_file()}


def files_of(tid, body, root=None, intake_rows=None):
    """The files a task will change: its INTAKE row's files when one is current (intake.py: the
    embedder and the symbol index, read from the task's description), else the paths its description
    names. 2026-10-14: the regex alone linked nothing for a task written in plain words."""
    try:
        import intake
        r = intake.current(tid, body, intake_rows)
        if r and r.get("files"):
            return {intake.main_file(r)}              # link on the strongest match, not all five
    except Exception as e:                                          # noqa: BLE001 -- said; regex instead
        print(f"  NOTE: intake row for {tid} unreadable ({type(e).__name__}: {e})", file=sys.stderr)
    return target_files(body, root)


def members(first, candidates, body_of, max_size=BATCH_MAX, root=None, files=None):
    """[first, ...]: up to max_size tasks, first always first, then candidates IN THEIR ORDER that
    share a target file WITH THE FIRST TASK. Not a chain (build_batches joins t1-t2-t3): in one call a
    chain shares no file across all three, so the read it was meant to save is not saved. Pure given
    body_of. A task with no target file, or a special one, is scoped alone."""
    files_of_ = files or (lambda tid, body: target_files(body, root))
    b0 = body_of(first)
    files = files_of_(first, b0)
    if not files or special(b0):
        return [first]
    out = [first]
    for c in candidates:
        if len(out) >= max_size:
            break
        if c in out:
            continue
        bc = body_of(c)
        fc = files_of_(c, bc)
        if fc & files and not special(bc):
            out.append(c)
    return out


def batching_on(q=None):
    return not (Path(q or Q) / "BATCH_OFF").exists()


def pick(first):
    if not batching_on():
        return [first]
    import manager_next
    picked, _skipped = manager_next.upcoming(n=40)
    import intake
    have = intake.rows()
    return members(first, [t for t in picked if t != first], _body,
                   files=lambda tid, body: files_of(tid, body, intake_rows=have))


BATCH_RULES = """
=====================================================================================================
THIS IS A BATCH: {n} TASKS THAT CHANGE THE SAME FILES ({shared}). Scope ALL of them in this one
answer -- read each shared file ONCE and use what you read for every task it touches. The first task
is above, with every rule that applies to all of them. The others follow.

ANSWER SHAPE, REQUIRED -- the answer is cut at these lines and each part is handled on its own:
{headers}
Under each header, that task's whole answer, exactly as for a single task: its step (a)/(b)/(c)/(d),
and for (c) its own fenced `chunks` block at the end of ITS section. One task's chunks never go in
another's block. Two tasks may NOT both edit the same file in their chunks: if they must, scope the
second as (d) BLOCKED by the first, naming it. Nothing goes before the first header.
=====================================================================================================
"""


def prompt(ids):
    """The batched prompt: the first task's full standard prompt, then the others, then the rules."""
    import contextlib
    import io
    import manager_prompt
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        manager_prompt.main([ids[0]])
    out = [buf.getvalue()]
    shared = sorted(set.intersection(*[target_files(_body(t)) for t in ids])) if len(ids) > 1 else []
    shared = shared or sorted(target_files(_body(ids[0])))
    for t in ids[1:]:
        try:
            import dossier
            idx = dossier.index_text(t) or ""
        except Exception as e:                                      # noqa: BLE001 -- said, not swallowed
            idx = f"(The dossier for {t} could not be read: {type(e).__name__}: {e})\n"
        place = manager_prompt.queue_place(t, TASKS / "QUEUE.md")
        where = f"{t} -- QUEUE.md:{place[0]}" if place else t
        out.append(f"\n\n----- ALSO IN THIS BATCH: {where} -----\n{idx}\n{_body(t)}")
    out.append(likely_code(ids))
    out.append(BATCH_RULES.format(n=len(ids), shared=", ".join(shared[:6]),
                                  headers="\n".join(f"  === TASK {t} ===" for t in ids)))
    return "".join(out)


def likely_code(ids):
    """Each task's intake targets, for the scoper to open first -- labelled as a guess."""
    try:
        import intake
        have = intake.rows()
    except Exception as e:                                          # noqa: BLE001 -- said; no section
        return f"\n(Intake rows unreadable: {type(e).__name__}: {e})\n"
    lines = []
    for t in ids:
        r = intake.current(t, _body(t), have)
        if not r:
            continue
        allf = [f"{x['file']}:{x['function']}" for x in r.get("targets", []) if x.get("function")]
        fns, more = allf[:8], len(allf) - 8      # the 8 nearest; the rest are in workspace/intake.jsonl
        lines.append(f"  {t}: files {', '.join(r.get('files') or [])}"
                     + (f"; functions {', '.join(fns)}" if fns else "")
                     + (f" (+{more} more in workspace/intake.jsonl)" if more > 0 else ""))
    if not lines:
        return ""
    return ("\n\nLIKELY CODE, from intake (the embedder matched each task's description to the code; no model "
            "judged it). A GUESS -- verify, do not trust; open these first and correct them:\n"
            + "\n".join(lines) + "\n")


def split(text, ids):
    """{task: its section} for every member whose header is present; the text before the first header
    is dropped (the rule says nothing goes there). A header naming a task NOT in the batch is ignored
    with the section it opens, so a model cannot scope a task nobody claimed. Pure."""
    marks = [(m.group(1), m.start(), m.end()) for m in HEADER.finditer(text or "")]
    out = {}
    for i, (tid, _s, e) in enumerate(marks):
        end = marks[i + 1][1] if i + 1 < len(marks) else len(text)
        if tid in ids and tid not in out:
            out[tid] = text[e:end].strip() + "\n"
    return out


def main(argv):
    if argv[:1] == ["pick"] and len(argv) == 2:
        print(" ".join(pick(argv[1])))
        return 0
    if argv[:1] == ["prompt"] and len(argv) >= 2:
        sys.stdout.write(prompt(argv[1:]))
        return 0
    if argv[:1] == ["split"] and len(argv) >= 4:
        text = Path(argv[1]).read_text(encoding="utf-8", errors="replace")
        parts = split(text, argv[3:])
        outdir = Path(argv[2])
        for tid, part in parts.items():
            (outdir / f"{tid}.md").write_text(part, encoding="utf-8")
        print(" ".join(parts))
        missing = [t for t in argv[3:] if t not in parts]
        if missing:
            print(f"scope_batch: no section for {' '.join(missing)} -- left for a later scope",
                  file=sys.stderr)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
