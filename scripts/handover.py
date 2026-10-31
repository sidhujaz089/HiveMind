#!/usr/bin/env python3
"""Draft the handover skeleton, and refuse to call it finished while it is unfilled.

STUB. The contract below is what scripts/tests/test_handover_guard.py holds this module to.
The suite is FROZEN (t265, D-35): the implementation is delegated and may not edit it.

WHY DRAFT-THEN-BLOCK RATHER THAN BLOCK. session_end.py already made this choice once for the
session log, and recorded the evidence at scripts/session_end.py:160 -- "Blocking on a missing
log has been in this script since it was written, and the audit on 2026-08-25 still found
three days with commits and no log -- a blocker does not produce a log, it produces a session
that closes some other way." So the skeleton and every fact git can supply get written
automatically, and only the part no script can write is demanded.

ROOT IS EXPLICIT, AND THAT IS NOT AN ACCIDENT. session_end.py derives REPO_ROOT from __file__
(scripts/session_end.py:25), which cannot be pointed at a temporary workspace. A guard whose
only test is against the live repository is a guard nobody can test, so both functions take
the root they operate on.
"""
import re
from pathlib import Path

TODO = "<!-- TODO: "

# t358 (Jas, 2026-09-18): every investigative thread must end in a stated state. A thread
# that is not on a stated row cannot be audited, and a ledger that is silently absent
# reads the same as a ledger that says everything is fine. The gate: the section must
# exist, and every data row in it must carry ONE of these states.
LEDGER_STATES = ("CLOSED", "FILED", "DECLARED-OPEN")


def _ledger_body(text: str) -> list[str] | None:
    """The lines under the `Thread ledger` heading (## or ###), or None if it is absent."""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if re.match(r"^#{2,3}\s+Thread ledger\s*$", ln.strip()):
            body = []
            for nxt in lines[i + 1:]:
                if nxt.strip().startswith("#"):
                    break                       # the next section owns everything after
                body.append(nxt)
            return body
    return None


def _ledger_rows(body: list[str]) -> list[str]:
    """Data rows only: drop the table separator and the column-name line."""
    out = []
    for ln in body:
        s = ln.strip()
        if not s:
            continue
        if re.fullmatch(r"\|?[\s:|-]+\|?", s):
            continue                            # the |---|---| separator
        if s.startswith("|") and "State" in s:
            continue                            # the column header
        if re.fullmatch(r"Session:\s*\S+", s):
            continue                            # t325: a section's own session line, not a thread
        out.append(s)
    return out


def check_ledger(text: str, rel: str) -> list[str]:
    """Problems with the Thread ledger, in a handover that was supposed to have one."""
    body = _ledger_body(text)
    if body is None:
        return [f"{rel} has no 'Thread ledger' section -- every thread must be in a stated "
                f"state ({' | '.join(LEDGER_STATES)})"]
    rows = _ledger_rows(body)
    if not rows:
        return [f"{rel} has a Thread ledger with no stated rows -- silence does not read as "
                f"completeness; state every thread ({' | '.join(LEDGER_STATES)})"]
    bad = []
    for row in rows:
        if not any(st in row for st in LEDGER_STATES):
            bad.append(row)
    if bad:
        shown = "; ".join(b[:90] for b in bad[:4])
        more = f" (+{len(bad) - 4} more)" if len(bad) > 4 else ""
        return [f"{rel} Thread ledger row(s) carry no stated state "
                f"({' | '.join(LEDGER_STATES)}): {shown}{more}"]
    return []


def draft(root: Path, day: str, session: str | None = None) -> str:
    """Return the skeleton text for workspace/HANDOVER-<day>.md.

    Must contain the literal marker TODO one or more times, in the form
    "<!-- TODO: <what to write here> -->". Those markers are what check() counts, and the
    prompts inside them are the only guidance the next author gets.

    The Thread ledger section is part of the skeleton (t358): draft-then-block, so the
    gate never demands a shape it never showed the author.
    """
    return (
        f"# Handover {day}\n"
        "\n"
        # t325: a non-empty session makes the skeleton name it, the shape check() then demands.
        + (f"Session: {session}\n\n" if session else "")
        + "## What this session was for\n"
        f"{TODO}state the purpose of this session-->\n"
        "\n"
        "## What was done\n"
        f"{TODO}list the concrete actions taken-->\n"
        "\n"
        "## What was measured\n"
        f"{TODO}record any measurements, benchmarks, or test results-->\n"
        "\n"
        "## What is still open\n"
        f"{TODO}list unresolved issues or deferred work-->\n"
        "\n"
        "## Next step\n"
        f"{TODO}state the single next action for the incoming session-->\n"
        "\n"
        "## Thread ledger\n"
        "| Thread | State | Artefact |\n"
        "|---|---|---|\n"
        f"| {TODO}first thread, drop rows you do not have, state every one you do (CLOSED "
        "| FILED | DECLARED-OPEN)--> | DECLARED-OPEN | "
        f"{TODO}its cover: a receipt for CLOSED, a task id for FILED, the cover for "
        "DECLARED-OPEN--> |\n"
    )


def check(root: Path, day: str, session: str | None = None) -> list[str]:
    """Return the problems with workspace/HANDOVER-<day>.md. Empty list means finished.

    Absent          -> non-empty, and at least one problem names the exact relative path
                        "workspace/HANDOVER-<day>.md" so the message says what to create.
    Unfilled        -> non-empty. A file carrying the skeleton's TODO markers is a filename,
                        not a handover.
    No ledger       -> non-empty (t358): no Thread ledger section at all.
    Unstated rows   -> non-empty (t358): a ledger row that carries none of the stated
                        states; an empty ledger is the same (silence is not completeness).
    Filled          -> [].
    A different day -> non-empty. Yesterday's handover must not satisfy today; that is the
                        stale-handover bug this whole task exists for.
    """
    rel = f"workspace/HANDOVER-{day}.md"
    path = root / "workspace" / f"HANDOVER-{day}.md"
    if not path.exists():
        return [f"Missing handover file: {rel}"]
    text = path.read_text(encoding="utf-8")
    if TODO in text:
        return [f"{rel} still contains unfilled TODO markers"]
    problems = check_ledger(text, rel)
    # t325: KEYED ON THE SESSION, NOT ONLY THE DAY. A filled handover written by an EARLIER session
    # the same day satisfied the day-keyed check, so a second session could close having written
    # nothing (09-15: d962bb7c wrote it at 08:19, 007b3f63 worked all afternoon). With a session,
    # the text must name it; presence is enough, so appending a section stays legal.
    if session and f"Session: {session}" not in text:
        problems.append(f"{rel} does not name this session: add your own section to it, with the line "
                        f"'Session: {session}' (the handover there was written by another session)")
    return problems


# t1084 (workspace/rca/9d7774774cef.md): a deferral written as prose dies with the session. The
# 2026-09-02 row parked the interview, the benchmarks and the Manager tier at 86% context, minted
# no task, and six days later the session review had 896 candidates and no tracker to attach. These
# are the phrasings sessions actually use -- the RCA's own words, plus the offers and admissions
# scripts/check_declared_intent.py already treats as parking (its OFFER list, kept in step by hand;
# the two drift when each side holds its own copy of the rule).
#
# NO BARE "deferred" / "deferred work", and that is deliberate: the skeleton's own prompt says
# "list unresolved issues or deferred work", so a bare term flags every handover ever drafted and a
# check that fires on boilerplate is a check nobody reads.
DEFERRALS = re.compile(
    r"\b("
    r"i'?d\s+suggest\s+letting"
    r"|before\s+anything\s+else"
    r"|i'?ll\s+hold"
    r"|i\s+can\s+file\s+it\s+as\s+a\s+task"
    r"|for\s+you\s+to\s+decide\s+on\s+if\s+you\s+want"
    r"|i\s+haven'?t\s+started"
    r"|left\s+for\s+next\s+session"
    r"|waiting\s+for\s+the\s+(?:weekend|next\s+session|you)"
    r"|as\s+you\s+asked"
    r"|parked\s+for\s+you"
    r"|deferred\s+to"
    r"|deferred\s+until"
    r"|(?:next|another)\s+session"
    r")",
    re.IGNORECASE,
)


def deferred_items(text: str) -> list[dict]:
    """Every deferral named in `text`, in the order the lines carry them.

    ONE item per line however many deferral phrases that line contains: a sentence that parks two
    things is one thing the incoming session has to read, and counting phrases would report the
    same line twice. The handle must sit on the deferral's OWN line -- an id named further down
    belongs to whatever that line was talking about, not to this deferral. Pure: it reads the text
    it is handed, touches no disk, prints nothing.
    """
    items = []
    for line in text.splitlines():
        if not DEFERRALS.search(line):
            continue
        task = ""
        handle = re.search(r"\bt\d{3,}\b", line)      # case-sensitive: the tracker's own shape
        if handle:
            task = handle.group(0)
        else:
            # No id on the line. An artefact named on that same line is the closest thing to a
            # cover, and it must be a path: a bare "notes.md" names nothing anyone can open.
            for found in re.finditer(r"[A-Za-z0-9_.\-/]+\.(?:md|py|json|jsonl)", line):
                if "/" in found.group(0):
                    task = found.group(0)
                    break
        items.append({
            "quote": line.strip(),                     # the WHOLE line; a sliced record reads as complete
            "task": task,
            "tracked": bool(task),                     # a real bool, so the row prints true/false
        })
    return items


def deferred_checklist_rows(text: str, rel: str) -> list[str]:
    """One markdown bullet per deferred item, same order, saying whether the work has a number.

    An untracked item gets the instruction spelled out on its own line: `deferred_work_tracked:
    false` alone tells the author the record is orphaned, not what to do about it, and the fix is
    a minted id on that very line -- the only place the next reader will look.
    """
    rows = []
    for item in deferred_items(text):
        flag = "true" if item["tracked"] else "false"
        row = f'- Deferred: "{item["quote"]}" -- deferred_work_tracked: {flag}'
        if not item["tracked"]:
            row += (f" ({rel} names deferred work with no task id: mint one, then put "
                    f"its id on this line)")
        rows.append(row)
    return rows

