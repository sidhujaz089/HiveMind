#!/usr/bin/env python3
"""Every intention stated in chat must carry a task id, or it is not a commitment.

WHY THIS EXISTS. Jas, 2026-09-11, reading a status line that said *"one thing I'll be
checking in its draft"*: **"When you say 'I'll be checking', are you doing something or
declaring you will? This has come up before where you weren't doing something and I thought
you were."** He was right -- nothing had been checked, and there was no way for him to tell.

His fix, in his own words: *"is it possible to show me it won't get missed by queuing things
in the background tasks? I can see those. Or perhaps making a task. Then when you confirm the
task number I will see it won't be missed."*

So the rule is not "stop saying what you intend". It is **an intention stated to Jas must be
addressable**: it gets a task id he can read on his phone, or it gets done in the same turn
and reported in the past tense. Prose in a status paragraph is neither -- it evaporates when
the session ends, and he has been burned by exactly that.

WHAT THIS CANNOT DO, stated plainly because a check that oversells itself is worse than none
(AGENTS.md rule 9). It cannot read intent. It matches PHRASINGS -- first-person future-tense
commitments -- in this session's assistant turns, and asks whether a task id appears near
each one. A sentence that commits without using any of these words is invisible to it, and a
sentence that uses them idly is a false positive. It is a WARN for that reason, never a
blocker: the judgement stays with the session, the visibility does not.

DECAY TEST. `scripts/tests/test_declared_intent.py` feeds it a transcript containing a bare
declaration and requires a finding, AND feeds it the same declaration with a task id and
requires silence. A check that cannot fail is not a check (docs/CHECKS.md, rule 1).
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

# First-person, future-tense, committing. Deliberately narrow: every one of these is a
# sentence where Jas would reasonably believe something is going to happen.
INTENT = re.compile(
    r"\b("
    r"I'?ll\s+(?:be\s+)?\w+"
    r"|I\s+will\s+\w+"
    r"|I'?m\s+going\s+to\s+\w+"
    r"|next\s+I'?ll\b"
    r"|(?:then|after\s+that),?\s+I'?ll\b"
    r"|I\s+plan\s+to\b"
    r"|I\s+intend\s+to\b"
    r"|(?:my\s+)?next\s+step\s+is\b"
    r"|left\s+to\s+do\b"
    r"|still\s+(?:need|needs)\s+to\b"
    r")",
    re.IGNORECASE,
)

# OFFERS AND PARKING (t497, t516, t527; 2026-10-01). The session reviews found 34 deferrals in
# eleven days, nearly all phrased as an offer or a park rather than a promise -- "Say the word
# and I'll...", "I left both alone", "Worth wiring in next session", "Logged, not chased" -- and
# each became a ~1 h RCA on Q4. The disposition the RCAs asked for: a task number, or an
# explicit decline with its reason.
OFFER = re.compile(
    r"\b("
    r"if you(?:'d| would)? (?:want|like)\b"
    r"|say the word\b"
    r"|happy to (?:also|do)\b"
    r"|unless you want\b"
    r"|want me to\b"
    r"|(?:I )?left (?:it|that|them|both|this) (?:alone|for you)\b"
    r"|leave (?:it|that|them|this) for (?:you|later|another)\b"
    r"|worth (?:doing|wiring|fixing|chasing|deciding|looking at)\b"
    r"|(?:logged|noted), not chased\b"
    r"|can be its own decision\b"
    r"|(?:next|another) session\b"
    r")",
    re.IGNORECASE,
)
# An explicit decline is a disposition too: "declined: <reason>" or "not doing X because ...".
DECLINED = re.compile(r"\b(declined?:|not doing (?:it|this|that)\b[^.]*\bbecause\b)", re.IGNORECASE)

# ADMISSIONS of uncompleted work (t674, RCA 7af5a8ef54a7): the INTENT/OFFER lists catch "I'll do X later" and
# miss the same deferral said as a fact -- the 2026-09-12 session's hourly check-in was "isn't wired", "haven't
# started", "still outstanding", "checking manually", "not yet implemented", and 6 of its 7 real deferral lines
# went unseen on verbatim replay. Same handles apply: a task id (ADDRESSED) or a declined: line (DECLINED) in
# the sentence silences it.
ADMISSION = re.compile(r"isn't wired|haven't started|still outstanding|checking manually|not yet implemented")

# What makes a declaration addressable. A task id Jas can look up, a background task the app
# shows him, or an explicit statement that it is happening in this same turn.
ADDRESSED = re.compile(
    r"\b(t\d{3,}|task\s+t?\d{3,}|background\s+task|spawn_task|L-\d{3}|D-\d{2,})\b",
    re.IGNORECASE,
)

# Phrasings that are about to be settled inside the same breath, not deferred.
IMMEDIATE = re.compile(
    r"\b(right now|in this turn|this same turn|before I (?:answer|reply|continue)|"
    r"let me (?:check|look|read|run)|running (?:it|that) now)\b",
    re.IGNORECASE,
)

# The USER side (t1098, RCA b6909ce088c7). The regexes above read the assistant's own words; the
# shape that lost the 10am timer was a five-item USER message answered by one line of prose, which
# leaves no phrasing to grep for. Enumeration is the only handle a user message offers: markers can
# be counted, and a numbered status answer can be counted against them.
ENUMERATION = re.compile(r"^\s*(?:\d{1,2}[.)\]]|[-*•–])\s+\S", re.MULTILINE)

# A numbered line that SAYS what happened to the item. The number alone is not an answer -- "1.
# Merge branches" is the ask restated, which is exactly what a weak build would accept -- so the
# status word is part of the match, not a second pass over it.
STATUS = re.compile(
    r"^\s*\d{1,2}[.)\]]\s+(?=.*\b(?:pending|done|deferred|cancelled|complete|completed"
    r"|blocked|skipped|carried)\b)",
    re.IGNORECASE | re.MULTILINE,
)

# `deferred` is a disposition only if it says WHEN the item comes back. "I'll come back to that"
# is the same evaporation wearing a status word.
RECHECK = re.compile(
    r"re-?check|revisit|next session|cron|at \d{1,2}[:.]\d{2}|by \d{1,2}\b"
    r"|\d{4}-\d{2}-\d{2}|\bt\d{3,}\b",
    re.IGNORECASE,
)



def sentences(text):
    for raw in re.split(r"(?<=[.!?])\s+|\n+", text):
        s = raw.strip()
        if s:
            yield s


def findings(turns):
    """turns: assistant text, in order. Returns the sentences that declare without a handle."""
    out = []
    for text in turns:
        for s in sentences(text):
            if not (INTENT.search(s) or OFFER.search(s) or ADMISSION.search(s)):
                continue
            if ADDRESSED.search(s) or IMMEDIATE.search(s) or DECLINED.search(s):
                continue
            # A code block or a quoted rule is not a promise to Jas.
            if s.lstrip().startswith(("#", "```", ">", "|", "*", "-")) and "I" not in s[:3]:
                continue
            out.append(s if len(s) <= 200 else s[:197] + "...")
    return out


def deferral_findings(turns):
    """turns: (role, text) pairs in transcript order. Returns the user messages whose enumerated
    items were never answered by a numbered status list, plus the `deferred` items carrying no
    re-check time or condition.

    WHY THIS EXISTS. workspace/rca/b6909ce088c7.md, on the 10am timer: Jas's message enumerated
    five items, the assistant answered the merge in prose and deferred the timer with "I'll come
    back to that", and the item was never re-raised in ~2,300 turns. The recorder that RCA asked
    for (section 5) is "the transcript itself; a session review greps for multi-item user messages
    and checks for an adjacent numbered-status response" -- this is that sentence, made executable.
    `findings()` reads only the assistant's phrasing, and a five-item ask met by one line of prose
    contains no phrasing to find, so the loss was invisible on every surface in this repository.

    WHAT THIS CANNOT DO, stated plainly because a check that oversells itself is worse than none
    (AGENTS.md rule 9, the same admission the module docstring makes at lines 18-23). It is a
    MARKER-shaped check: **a multi-item user message written as prose, with no enumeration
    markers, is invisible to it.** A prose answer to an enumerated ask is invisible the same way,
    because the status half matches numbered lines only. It counts lines, so it measures coverage,
    not understanding -- a status list can name every item and still be wrong about all of them.
    WARN only, for the reason `findings()` is WARN: a check that fires on ordinary prose gets
    switched off within a week.
    """
    # The status words a numbered line must carry; STATUS's lookahead is this same list.
    words = ("pending", "done", "deferred", "cancelled", "complete",
             "completed", "blocked", "skipped", "carried")
    out = []
    for i, (role, text) in enumerate(turns):
        if role != "user":
            continue
        n = sum(1 for _ in ENUMERATION.finditer(text))
        if n < 2:
            # One item is a request, not a decomposition; flagging those is how a WARN gets
            # disabled (docs/skills/planning-for-sustainment).
            continue
        # Everything between this user turn and the next one is the answer to it.
        answers = []
        j = i + 1
        while j < len(turns) and turns[j][0] != "user":
            if turns[j][0] == "assistant":
                answers.append(turns[j][1])
            j += 1
        body = "\n".join(answers)
        # STATUS's match ends after the "N. " marker, so the rest of that line is the item's
        # status -- and the text a reader needs in order to act on it.
        items = []
        for m in STATUS.finditer(body):
            end = body.find("\n", m.end())
            items.append(body[m.end():len(body) if end < 0 else end].strip())
        if len(items) < n:
            out.append(f"turn {i}: user message enumerates {n} item(s); "
                       f"numbered status response covers {len(items)}")
            continue
        for item in items:
            low = item.lower()
            pos, word = -1, ""
            for w in words:
                k = low.find(w)
                if k >= 0 and (pos < 0 or k < pos):
                    pos, word = k, w
            if word != "deferred" or RECHECK.search(item[pos + len(word):]):
                continue
            # [:60] caps the finding to one line; the item text is cut nowhere else.
            out.append(f"turn {i}: item {item[:60]!r} is deferred with no re-check time/condition")
    return out


def silence_finding(stats):
    """t267: a session that DID WORK -- commits, or task files touched -- and named no task id is a
    finding, in whatever words its intention was phrased. The phrasing regex is a blacklist, and a
    phrasing nobody listed ("That can wait until after this lands") is invisible to it; this is the
    unknown bucket, inverted onto the session. No work and no ids is silent: a check that fires on a
    conversation that did nothing gets disabled (G10)."""
    worked = (stats.get("commits") or 0) > 0 or (stats.get("task_files") or 0) > 0
    if worked and not (stats.get("task_ids") or 0):
        return (f"silence: this session did work ({stats.get('commits', 0)} commit(s), "
                f"{stats.get('task_files', 0)} task file(s) touched) and named no task id")
    return None


def report(hits, silence):
    """Two halves on their own lines, so the phrasing half going quiet cannot hide the silence half."""
    lines = [f"phrasing: {len(hits)} declaration(s) with no task id"]
    lines += [f"  - {h}" for h in hits]
    lines.append(silence if silence else "silence: none (work done carries a task id, or no work was done)")
    return "\n".join(lines)


def session_stats(texts):
    """Counts over the session's assistant turns, tool commands included: commits made, task files
    written, distinct task ids named."""
    blob = "\n".join(texts)
    return {"commits": len(re.findall(r"\bgit commit\b", blob)),
            "task_files": len(set(re.findall(r"workspace/tasks/(t\d{3,})--", blob))),
            "task_ids": len(set(re.findall(r"\bt\d{3,}\b", blob)))}


def live_turns():
    """This session's own assistant turns. The live transcript is the NEWEST file.

    `transcripts.previous_session()` deliberately SKIPS the live one -- it exists to read the
    session before this one. Here the live one is the whole point, so the newest file is taken
    on purpose. That is `L-148` inverted, not ignored, and the same inversion the docstring in
    `previous_session` describes.
    """
    import transcripts as tr
    d = tr.SESSIONS / tr._workdir_key()
    if not d.is_dir():
        return None
    files = sorted(d.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        return None
    # read_session returns (session_id, rows); each row is already flattened to
    # {"role": <the jsonl `type` field>, "text": ...}. Unpacking it as a bare list was the
    # first thing this got wrong.
    _sid, rows = tr.read_session(files[0])
    out = []
    for r in rows:
        if r.get("role") != "assistant" or not r.get("text"):
            continue
        # `_text` folds TOOL COMMANDS into the same string as prose, and marks only the
        # FIRST line `[Bash] ...` -- a multi-line heredoc keeps its body unmarked. Filtering
        # line by line therefore left the body behind, and this file's own docstring came
        # back as a declaration on the first run. Content blocks arrive in order, prose then
        # tool calls, so everything from the first marker onward is dropped.
        lines = r["text"].splitlines()
        cut = next((i for i, l in enumerate(lines) if re.match(r"\s*\[\w+\]\s", l)), None)
        out.append("\n".join(lines[:cut] if cut is not None else lines))
    return out


def live_full_texts():
    """The session's assistant turns WITH their tool commands (for session_stats)."""
    import transcripts as tr
    d = tr.SESSIONS / tr._workdir_key()
    files = sorted(d.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True) if d.is_dir() else []
    if not files:
        return None
    _sid, rows = tr.read_session(files[0])
    return [r["text"] for r in rows if r.get("role") == "assistant" and r.get("text")]


def main():
    full = live_full_texts()
    silence = silence_finding(session_stats(full)) if full is not None else None
    if silence:
        print(f"  [WARN] {silence}")
    turns = live_turns()
    if turns is None:
        print("  [SKIP] Declared intentions carry a task id")
        print("         No transcript for this working directory. Nothing was checked --")
        print("         this is not a pass.")
        return 0
    hits = findings(turns)
    if not hits:
        print(f"  [PASS] Declared intentions carry a task id ({len(turns)} assistant turn(s))")
    else:
        print(f"  [WARN] {len(hits)} intention(s) stated to Jas with no task id")
        for h in hits[:8]:
            print(f"         • {h}")
        if len(hits) > 8:
            print(f"         ... and {len(hits) - 8} more")
        print()
        print("         Jas cannot see a sentence after the session ends. Either do it now and")
        print("         report it in the PAST tense, or give it a handle he can hold:")
        print("           python3 projects/app-repo/taskstore.py add \"<title>\" <area> claude-code")
        print("           python3 projects/app-repo/taskstore.py note <id> \"<why, and the oracle>\"")
        print("         then tell him the number. WARN, not a blocker: some of these are noise,")
        print("         and only the session can tell which.")
    # t1098 (RCA b6909ce088c7): the user side. live_turns() hands findings() assistant prose with
    # the tool blocks cut, so the user turns have to come from the same newest transcript file --
    # read_session returns (session_id, rows), each row already flattened to {"role", "text"}.
    import transcripts as tr
    d = tr.SESSIONS / tr._workdir_key()
    files = sorted(d.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True) if d.is_dir() else []
    pairs = []
    if files:
        _sid, rows = tr.read_session(files[0])
        pairs = [(r.get("role"), r.get("text") or "") for r in rows if r.get("text")]
    for line in deferral_findings(pairs):
        print(f"  [WARN] {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
