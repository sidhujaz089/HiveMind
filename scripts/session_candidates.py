#!/usr/bin/env python3
"""Session review, step 1, in code: pull the candidate sentences out of a session (t663).

Jas, 2026-10-03 (after the three-way trial and DeepSeek's critique): a local model does not
"review" a transcript. Code finds the sentences that can hold dropped work -- by a trigger table --
and the model only says keep or drop for each, with a few words of what it is. Code then searches
tasks and commits, and the model answers MISSED or COVERED from what the search found.

The TRIGGER TABLE IS THE INTELLIGENCE. Every false positive or miss in a trial is fixed here.

    candidates(turns) -> [{"n", "turn", "type", "quote", "before", "after"}]
"""
import re
import sys

# Assistant sentences. Each type: phrases that put a sentence up for review. Kept narrow on purpose:
# "fixed", "works", "push", "must" matched 47 lines of one session (2026-10-03 measurement).
TRIGGERS = {
    "PROMISED": r"\bI'?ll\b(?! (?:check|look|see|wait|report|confirm)\b)|\bI will\b|\blater\b|\btomorrow\b|"
                r"\bnext session\b|\bnot yet\b|\bnot started\b|\bholding off\b|\bonce .{3,40} lands\b|"
                r"\bstill queued\b|\bdeliberately not\b|\bI'?m (?:deliberately )?not going to\b|"
                # audit 2026-10-03 (s04, s09): promises not phrased with "I'll"
                r"\bnext piece\b|\bcan come out\b|\bone-line fix\b|\bleft (?:it )?for (?:the )?(?:next|handover)\b|"
                r"\b(?:isn't|aren't|wasn't|hasn't been|haven't been) (?:\w+ ){0,3}yet\b",
    "FOR-JAS":  r"\bleft (?:it|that|them) for you\b|\byour call\b|\bfor you to decide\b|\bneeds your\b|"
                r"\ba trade for you\b|\bsay the word\b|\bwant me to\b|\bshould I\b|\bup to you\b|"
                # audit 2026-10-03 (s01, s02, s07): offers and questions in other words
                r"\bsay (?:if|so if|when) you\b|\btell me (?:which|if|whether|to|what)\b|\bdo you want\b|"
                r"\bshall I\b|\bif you want\b|\?\s*$|"
                # held-out audit 2026-10-03 (s10): offers phrased "I can also ..."
                r"\bI can (?:also )?(?:make|set up|add|build|file|write|do)\b",
    "NOTICED":  r"\bworth noting\b|\bI'?d flag\b|\bworth flagging\b|\bslightly\b|\bmisleading\b|\bsame class as\b|"
                r"\ba gap\b|\bnot dismissing\b|\bnothing (?:else )?reads\b|\bseen by nobody\b|\bsilently\b|"
                r"\bwhich nothing\b|\bnobody (?:has )?(?:looked|noticed)\b|"
                # audit 2026-10-03 (s02, s06, s07, s08, s09)
                r"\bworth a (?:task|look)\b|\b(?:isn't|not) fixed\b|\bdoesn't fix\b|\bhaven't (?:looked|checked)\b|"
                r"\bload-sensitive\b|\bunder load\b|\bno explanation\b|\bunexplained\b|"
                r"\bstill (?:doesn't|don't|isn't|aren't|has no|have no|says|shows|reads)\b|\bcan't identify\b|"
                r"\bdoesn't touch\b|\bnothing (?:runs|calls|checks|enforces)\b|"
                # held-out audit 2026-10-03 (s10, s11, s17)
                r"\bcould be (?:load|a real|either)\b|\bmiscalibrated\b|\b(?:small |real )+bug\b|\bone word to fix\b|"
                r"\bworth writing\b|\bnot written(?: yet)?\b|\bthe durable fix\b|\bneeds? a ceiling\b",
    "PARTIAL":  r"\bonly part\b|\bthe rest\b|\b(?:two|three|four|five|six) of the (?:seven|six|five|four)\b|"
                r"\bonly (?:UI|ui) wiring\b|\bneed only\b|\bhandle (?:it )?directly myself\b|"
                r"\bwhat it doesn't cover\b|\bonly covers\b|\baren't sorted\b",
    "UNKNOWN":  r"\bcan'?t (?:yet )?separate\b|\bdon'?t know yet\b|\bunclear\b|\bwill tell us\b|"
                r"\bfirst question\b|\bcan'?t attribute\b|\bnot yet (?:known|measured)\b|\bnot verified\b",
    "CORRECTION": r"\bI had that wrong\b|\bwalk back\b|\bcorrecting myself\b|\bI was wrong\b|"
                  r"\bmy (?:earlier )?framing was wrong\b|\bnot flattering\b",
    "NEEDS-OK": r"\bsudo(?:ers)?\b|\bdelete (?:the|all|every)\b|\bwipe\b|\bforce[- ]push\b",
}
# Jas's own sentences: decisions, rules and asks. Task notifications are not his words.
USER = r"\bI want\b|\bI don'?t want\b|\bmake sure\b|\balways\b|\bnever\b|\bcan you\b|\bcould you\b|" \
       r"\bplease\b|\bshould\b|\?\s*$|\bok(?:ay)?,? (?:so|let'?s|can|queue|make)\b|\bqueue\b|\bmake (?:a|the)\b"
CHATTER = r"^(?:let me|now let me|now the|good[,.]|ok[,.]|checking|confirmed[,.]|running|still (?:running|waiting))\b"

# NOT JAS'S WORDS (audit 2026-10-03): DSH sessions carry harness-injected "user" turns -- the whole
# of AGENTS.md in a <system-reminder>, runtime context, the approval policy, model-change notices,
# pasted prompts. In s20 29 of 30 candidates came from them. A user turn that opens like one of
# these is skipped whole.
INJECTED = re.compile(r"^\s*(?:<|\[model changed|Current runtime co|The approval polic|Here is your last|"
                      r"You are Q16|You are the |Caveat: The messages)", re.I)

_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z*`\"(])|\n+")


def sentences(text):
    out = []
    for s in _SPLIT.split(text or ""):
        s = s.strip(" -*#>|\t")
        if len(s) >= 12 and not s.startswith("```"):
            out.append(s)
    return out


DONE_WORDS = re.compile(r"\bt\d{3}\b|\bcommitted\b|\bfiled\b|\bmerged\b|\bnow (?:position|at)\b|"
                        r"\bqueued as\b|\bdone\b|\b[0-9a-f]{7,8}\b")


def later_text(turns, ti, limit=1500):
    """What the session said AFTER turn ti: the next assistant turns up to the next user message,
    cut at `limit` chars (said so). The yes/no step needs it: an item settled 20 minutes later in
    the same session is not dropped work (trial v2, 2026-10-03: 'port the salvage?' was done)."""
    out = []
    for turn in turns[ti + 1:]:
        role, text = turn[0], turn[1]      # a turn's third slot, when it has one, is its tool events
        if role == "user" and not text.lstrip().startswith("<task-notification>"):
            break
        if role == "assistant":
            out.append(text)
    t = " ".join(out)
    return t if len(t) <= limit else t[:limit] + " [... cut at %d chars]" % limit


def answered(turns, ti):
    """Did the assistant's reply to Jas's turn ti name a task, a commit, or say it was done/filed?"""
    return bool(DONE_WORDS.search(later_text(turns, ti, limit=10_000)))


def topic(row):
    """Words that identify what a row is about, for grouping repeats (5 rows on 'Status-tab buttons')."""
    words = re.findall(r"[A-Za-z][A-Za-z0-9_.-]{3,}", row["quote"].lower())
    stop = {"that", "this", "with", "from", "have", "will", "what", "your", "they", "them", "were", "into",
            "just", "only", "need", "needs", "then", "when", "once", "before", "after", "still", "worth"}
    return {w for w in words if w not in stop}


def group(rows, overlap=0.5):
    """Collapse rows about the same thing: a row joins a group when half its topic words are shared."""
    groups = []
    for r in rows:
        t = topic(r)
        for g in groups:
            if t and len(t & g["topic"]) / max(1, min(len(t), len(g["topic"]))) >= overlap:
                g["rows"].append(r)
                g["topic"] |= t
                break
        else:
            groups.append({"topic": set(t), "rows": [r]})
    return groups


# OFF (REPORT_MIN = None). Measured on the 20-session audit, 2026-10-03: with the held-out phrasings
# added to TRIGGERS it caught 0 more open items and added 390 candidates (818 -> 1208, +48%) for the
# keep step to wade through. Kept, switched off, so a later audit can turn it on with evidence.
REPORT_MIN = None          # chars: a report turn, not narration; None = net off
REPORT_WORDS = re.compile(r"\b(?:fix|bug|lesson|follow[- ]?up|next step|still open|open item|should|needs?|"
                          r"recommend|propos\w*|left|remaining|not yet|later|todo|defect|gap)\b", re.I)


def candidates(turns):
    rows = []
    for ti, turn in enumerate(turns):
        role, text = turn[0], turn[1]      # a turn's third slot, when it has one, is its tool events
        if role == "user" and INJECTED.match(text or ""):
            continue
        ss = sentences(text)
        for si, s in enumerate(ss):
            low = s.lower()
            if role == "user":
                # An ask the very next reply answered with a task number or a commit was not dropped.
                kinds = ["JAS-SAID"] if re.search(USER, s, re.I) and not answered(turns, ti) else []
            else:
                if re.search(CHATTER, low):
                    continue
                kinds = [k for k, rx in TRIGGERS.items() if re.search(rx, s, re.I)]
                # SAFETY NET (held-out audit 2026-10-03): the trigger table caught 81% of open items on the
                # sessions it was tuned on and 45% on eight it had not seen -- the misses sat in long report
                # turns, phrased in ways no list predicts. In a report turn, a sentence naming a fix, bug,
                # lesson or follow-up is a candidate too; the keep step drops what isn't.
                if not kinds and REPORT_MIN and len(text) >= REPORT_MIN and REPORT_WORDS.search(s):
                    kinds = ["REPORT"]
            for k in kinds[:1]:                      # one row per sentence; the first type wins
                rows.append({"n": len(rows) + 1, "turn": ti, "role": role, "type": k, "quote": s,
                             "later": later_text(turns, ti),
                             "before": ss[si - 1] if si else "", "after": ss[si + 1] if si + 1 < len(ss) else ""})
    return rows



# DROPPED (t663, 2026-10-05): the one signal code has that separates dropped work from settled work.
# On the 19-session audit, 11 of 13 candidates of real unfiled misses were NEVER MENTIONED AGAIN after
# they were said; what was settled came back ("done", a task id, a hash) or was discussed. Topic overlap
# of half the sentence's distinctive words is "mentioned". Cuts 1177 candidates to ~790 (-33%) and keeps
# 8 of the 10 real misses -- Q4's keep+judge on the same sessions surfaced 4. It does NOT get noise down
# to a list a person reads in a minute (~40 rows/session); see AUDIT-Q4-GRADE-2026-10-05.md.
TOPIC_STOP = frozenset("""that this with from have will what your they them were into just only need needs then
when once before after still worth would could should about there their which while being been also does
done more than some like here want make made each other over these those first next last onto because
since until where right left take back look file files task tasks session work line lines model models
said says sure think know already again thing things going really tell okay yeah""".split())


def topic_words(text):
    words = (w.rstrip(".-") for w in re.findall(r"[a-z0-9][a-z0-9_.-]{3,}", (text or "").lower()))
    return {w for w in words if len(w) >= 4 and w not in TOPIC_STOP and not w.isdigit()}


def mentioned_later(turns, row, share=0.5):
    """Does any later sentence, by either side, share `share` of the row's topic words? A short line
    ("Say if you want that added.") borrows its neighbours' words, or it would match nothing."""
    q = topic_words(row["quote"])
    if len(q) < 2:
        q |= topic_words(f'{row["before"]} {row["after"]}')
    if not q:
        return False
    later = []
    for turn in turns[row["turn"] + 1:]:
        text = turn[1]                      # a turn's third slot, when it has one, is its tool events
        for s in sentences(text):
            if len(q & topic_words(s)) / len(q) >= share:
                return True
            later.append(s)
    # BY MEANING TOO (2026-10-07, measured first: workspace/embed-eval/RESULTS.md). A promise kept in
    # other words ("I'll add the prune later" ... "Filed t687 -- the NAS prune is in the nightly
    # sweep") shares too few words to count. At cosine 0.70 this took the audit set's false flags
    # 576 -> 473 with every real miss kept. Embedder down -> words alone, said once.
    if SEMANTIC and later:
        import semantic
        hit = semantic.best(row["quote"], later)
        if hit and hit[0] >= semantic.RESOLVE_AT:
            return True
    return False


SEMANTIC = True     # the switch: False gives the word-only script the audit set first measured


# THE COMPLETION CHANNEL (t1039): a promise is closed by a RECORD of the work, not by a word in a
# sentence. turns() (scripts/session_review.py:65) hands this script (role, text) pairs and drops the
# tool traffic at the door, so a promise kept by RUNNING the suite left nothing to see: no DONE_WORDS
# token, and its proof sits in the SAME assistant reply, seconds after the sentence, where no forward
# text scan ever reaches. A turn may now carry a THIRD element -- ("assistant", text, [event, ...]),
# the events the harness recorded for that reply. A record closes a row only when it is a SUCCESS by
# its own fields (exit_code 0, and green counts when it carries any) AND it is ABOUT that row's quote.
# A malformed record is RECORDS-not-swallowed: it is simply not a completion, and tool_events hands
# every entry back to whoever wants it. Nothing here raises on a turn that carries no third slot.
_PATH_IN_QUOTE = re.compile(r"[\w./-]+\.(?:py|md|json|sh|yaml|yml)")
_ID_IN_QUOTE = re.compile(r"\bt\d{3,4}\b")
_HASH_IN_QUOTE = re.compile(r"\b[0-9a-f]{7,40}\b")


def tool_events(turns, ti):
    """The events recorded inside turn ti, every entry returned unchanged. A turn with no third slot, a
    third slot that is not a list, and an out-of-range ti all read the same way: this turn recorded no
    tool traffic."""
    if isinstance(ti, bool) or not isinstance(ti, int) or not 0 <= ti < len(turns):
        return []
    events = turns[ti][2] if len(turns[ti]) > 2 else []
    return events if isinstance(events, list) else []


def _int(value):
    """(True, n) when str(value) reads as an int, (False, None) when it does not. A count a record
    wrote as prose, as a float, as a bool, or not at all is not evidence, and saying so must not
    raise; int(str(v)) is the coercion the harness's records need, whose counts arrive as text."""
    try:
        return True, int(str(value))
    except ValueError:
        return False, None


def _success(ev):
    """What the event's own record says about itself: exit_code 0 -- required, not decoration, since a
    test event that recorded no exit code proved nothing -- and, when it carries test counts at all,
    zero failed and at least one passed."""
    ok, code = _int(ev.get("exit_code"))
    if not ok or code != 0:
        return False
    if "failed" in ev:
        ok, failed = _int(ev["failed"])
        if not ok or failed != 0:
            return False
    if "passed" in ev:
        ok, passed = _int(ev["passed"])
        if not ok or passed <= 0:
            return False
    return True


def _is_test_run(ev):
    """A passing test run: both counts present, readable, and green. A file write carries no counts, so
    it can never stand in for the verification a promise asked for -- the channel is per sentence."""
    ok_p, passed = _int(ev["passed"]) if "passed" in ev else (False, 0)
    ok_f, failed = _int(ev["failed"]) if "failed" in ev else (False, 0)
    return ok_p and ok_f and passed > 0 and failed == 0


def _artefacts(quote):
    """(file paths, task ids, commit hashes) a quote names for itself: the verifiable things its own
    discharge has to name. A quote that names none can only be closed by a passing test run."""
    q = quote if isinstance(quote, str) else ""
    return (set(_PATH_IN_QUOTE.findall(q)), set(_ID_IN_QUOTE.findall(q)),
            set(_HASH_IN_QUOTE.findall(q)))


def _named_files(ev):
    """The paths an event says it touched. A files_written that is not a list is a malformed record: it
    names no file, which is said by the absence of a match, never raised."""
    files = ev.get("files_written")
    return [str(f) for f in files] if isinstance(files, (list, tuple)) else []


def _about(ev, quote):
    """Is this record about THIS row's quote? A file path the quote names has to appear in the record's
    own files_written, output or summary -- so a quote naming test_coder_guards.py is matched by a
    record naming scripts/tests/test_coder_guards.py -- and a tNNN or a hash the quote names has to
    equal the record's task or commit. A file the quote never named and an id the quote never named
    close nothing. A green test run counts only for a quote that names no artefact of its own, so one
    passing suite cannot discharge every sentence in the reply it ran in."""
    paths, ids, hashes = _artefacts(quote)
    named = _named_files(ev) + [str(ev[k]) for k in ("output", "summary") if k in ev]
    if any(p in t for p in paths for t in named):
        return True
    if any(ev.get("task") == i for i in ids) or any(ev.get("commit") == h for h in hashes):
        return True
    return not (paths or ids or hashes) and _is_test_run(ev)


def discharged_in_reply(turns, row):
    """The FIRST event recorded in the row's OWN reply that closes it, returned whole (kind, counts,
    files, output, the lot) so a reviewer can read the proof itself. Same reply only: a tool result in
    a LATER turn is that turn's record, not this promise's discharge -- the promise and its proof
    share a reply, which is exactly why no forward scan of text has ever found one."""
    ti = row.get("turn") if isinstance(row, dict) else None
    for ev in tool_events(turns, ti):
        if isinstance(ev, dict) and _success(ev) and _about(ev, row.get("quote", "")):
            return ev
    return None


def closed_in_later_text(turns, row):
    """The text channel, kept: a LATER assistant sentence that both shares the row's subject and
    carries a DONE_WORDS token ("filed", a tNNN, a hash) has discharged the promise. A shared word
    alone is narration about the same topic, and a DONE word alone is about something else -- t719's
    fixture C keeps a NOTICED row flagged against a DONE line about a different subject, so this asks
    for both. It is the narrow lexical net that works with the embedder off (SEMANTIC = False);
    mentioned_later() stays the wider, measured net it is."""
    ti = row.get("turn") if isinstance(row, dict) else None
    if isinstance(ti, bool) or not isinstance(ti, int) or not 0 <= ti < len(turns):
        return False
    q = topic_words(row.get("quote", ""))
    if len(q) < 2:
        # A short line borrows its neighbours' words, as mentioned_later() does, or it matches nothing.
        q |= topic_words(f'{row.get("before", "")} {row.get("after", "")}')
    if not q:
        return False
    for turn in turns[ti + 1:]:
        for s in sentences(turn[1]):
            # TWO shared subject words, not one (Claude, merge review): one word is coincidence --
            # "Committed abc1234: the lesson count script now runs." shares only "lesson" with a
            # promise to write a lesson, and would have closed it.
            if DONE_WORDS.search(s) and len(q & topic_words(s)) >= min(2, len(q)):
                return True
    return False


RESOLVE_MIN_WORDS = 5
RESOLVE_ANY = 2


def resolved_in_session(turns, row):
    """The promise closed in the SAME block, before Jas speaks again (t719): an answer after
    Jas's next turn is outside the window, so a later unrelated echo must not hide a miss."""
    ti = row.get("turn") if isinstance(row, dict) else None
    if isinstance(ti, bool) or not isinstance(ti, int) or not 0 <= ti < len(turns):
        return False
    q = topic_words(row.get("quote", ""))
    if len(q) < 2:
        q |= topic_words(f'{row.get("before", "")} {row.get("after", "")}')
    if not q:
        return False
    need = min(RESOLVE_ANY, len(q))
    for turn in turns[ti + 1:]:
        role = turn[0] if turn else None
        text = turn[1] if len(turn) > 1 else ""
        text = text if isinstance(text, str) else ""
        if role == "user" and not text.lstrip().startswith("<task-notification>"):
            break
        if role == "assistant" and any(
                DONE_WORDS.search(s) and len(q & topic_words(s)) >= need for s in sentences(text)):
            return True
    return False


def dropped(turns, share=0.5):
    """The assistant's candidates that the session never came back to -- the scripted review's list.
    Jas's own lines are left out: his asks are answered or not by the very next reply (answered()).
    A promise its own reply discharged with a tool result is left out too, and the record that closed
    it rides on the row as completed_by (t1039): the case that made this is a sentence at 23:02:02
    and "136 passed, 0 failed" at 23:02:11 -- six seconds apart, in one reply, with no channel to see
    the proof, so the lane filed a 3.5-day-old false deferral."""
    out = []
    for r in candidates(turns):
        if r["role"] != "assistant":
            continue
        if mentioned_later(turns, r, share) or closed_in_later_text(turns, r):
            continue
        closed = discharged_in_reply(turns, r)
        if closed is not None:
            r["completed_by"] = closed
            continue
        out.append(r)
    return out

if __name__ == "__main__":
    import json
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    import session_review as sr
    req = json.loads(open(sys.argv[1]).read())
    ts = sr.turns(req["harness"], req["path"])[req.get("from_turn", 0):req.get("to_turn") or None]
    for r in candidates(ts):
        print(f"{r['n']:3} t{r['turn']:<3} {r['type']:<10} {r['quote'][:110]}")
