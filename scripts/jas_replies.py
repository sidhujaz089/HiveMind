#!/usr/bin/env python3
"""Tasks where Jas answered and nobody answered him back.

t154 gave the local model's output a surface: `reviewq.py` shows what Claude has
not vetted. The stage AFTER that had none. Jas reads a task, types a note --
"merge", "Approved", "Is this good?" -- and nothing anywhere reports that he
spoke. `reviewq.py` cannot: it is derived from `agent-runs.jsonl` and
`review-verdicts.jsonl`, and Jas writes to neither. He writes notes on task files.

On 2026-08-26 he answered five tasks and the session told him, in the same
breath, that all five were still awaiting his decision. He had to ask "do you not
see my responses?".

The rule is deliberately dumb, because a clever one would need to understand what
he meant: take the notes on a task in file order, look at who wrote the LAST one,
and if it was Jas then the ball is in our court. Approvals, questions and
objections all surface the same way -- the point is that he spoke, not what he
said.
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TASKS = REPO / "workspace" / "tasks"

# `**2026-08-26 · jas** — Approved`. The author is everything between the middot
# and the closing stars; notes are appended, so file order is chronological and
# the last match is the newest. Sorting by the date would tie every note written
# on the same day, which is exactly the case that matters.
NOTE = re.compile(r"^\*\*(\d{4}-\d{2}-\d{2}) · ([^*]+?)\*\*\s*—\s*(.*)$", re.MULTILINE)

HUMAN = "jas"

# ANY SESSION'S ANSWER IS AN ANSWER (Jas, 2026-10-30: "correction that so any answer is an answer").
# This held only claude-code, so a dsh-harness answer left his task on this list -- live on 2026-10-30,
# where t1123 and t693 were answered and routed to the Manager, and he was still told nobody had.
#
# What it must NOT count is a RECORD rather than an answer: a local-model run, or a script's own row
# (manager, swift, local/qwen*, taskstore, bridge, route-review-lanes, dispatch-loop). None of those
# read his question. That is not a hypothetical either -- on t1123 the row
# "Reassigned production -> rework by taskstore.py" landed the same day he spoke, so "any later note
# counts" would have marked his 2026-10-24 instruction answered within hours and buried it.
ANSWERERS = ("claude-code", "claude-chat", "dsh-harness", "dsh", "codex", "gemini-cli", "cursor")


def notes(text):
    """Every note as (date, author, first line), in file order."""
    return [(d, a.strip(), r.strip()) for d, a, r in NOTE.findall(text)]


def unanswered(text):
    """Jas's newest note, if no session has replied since. Else None.

    File order decides, not the date: he answers on the same day as the note he
    is answering, so sorting by date ties exactly when it matters.
    """
    ns = notes(text)
    last_jas = max((i for i, n in enumerate(ns) if n[1] == HUMAN), default=None)
    if last_jas is None:
        return None
    if any(a in ANSWERERS for _, a, _ in ns[last_jas + 1:]):
        return None
    return ns[last_jas]


def waiting(tasks_dir=TASKS):
    """Tasks where Jas spoke and no session has answered, newest first."""
    out = []
    if not tasks_dir.is_dir():
        return out
    for f in sorted(tasks_dir.glob("*.md")):
        note = unanswered(f.read_text(encoding="utf-8", errors="replace"))
        if not note:
            continue
        try:
            where = str(f.relative_to(REPO))
        except ValueError:
            where = str(f)              # a caller passed a directory outside the repo
        out.append({"task": f.name.split("--")[0], "at": note[0],
                    "said": note[2], "file": where})
    return sorted(out, key=lambda r: r["at"], reverse=True)


def main():
    rows = waiting()
    if not rows:
        print("  Jas has replied to nothing that is still unanswered.")
        return 0
    print(f"  {len(rows)} task(s) where JAS SPOKE LAST and nobody answered:\n")
    for r in rows:
        print(f"    {r['task']:6} {r['at']}  {r['said']}")
    print("\n  He is waiting on you, not the other way round. Answer on the task"
          "\n  with taskstore.py note, or act on it -- either one ends the wait.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
