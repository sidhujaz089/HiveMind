#!/usr/bin/env python3
"""Session review: the manager reads a finished session, finds what slipped through, and runs an
in-depth root-cause analysis on each finding -- in the dispatcher's priority lane.

    python3 scripts/session_review.py enqueue                 # this session (Claude, or DSH via $DSH_SESSION_ID)
    python3 scripts/session_review.py discover [--idle-h 24]  # backstop: queue idle, never-queued sessions
    python3 scripts/session_review.py extract --request F     # priority job: session -> findings
    python3 scripts/session_review.py rca --finding F         # priority job: finding -> RCA report + tasks
    python3 scripts/session_review.py status

Jas, 2026-09-28. What is caught: (a) work put off without a task number; (b) a problem or bug
noticed in the session and neither fixed nor filed. Decisions that shaped it, in his words:
  * "all of this needs to flow through a local model" -- no keyword list, no librarian;
  * "I don't want it to block you" -- nothing here gates a reply or a commit;
  * "My priority isn't speed, but rather quality ... This really could be done by the manager";
  * "When a session closes, it could trigger a task to the manager to review that session";
  * above general production: dispatch_loop runs these as priority work (LEVELS), so the
    manager queue pauses BETWEEN its tasks, never mid-task;
  * "I want it to use the root cause analysis skill and get into all possible causes and create
    data collection plans as per the skill" -- rca() hands the manager the whole skill;
  * tasks for the proposed solutions go to the top of QUEUE.md, unheld; the manager ranks them.
  * DeepSeek-harness sessions too: the DSH model runs session_end.py, which calls enqueue.

Layout (workspace/session-reviews/): pending/<req>.json -> done/<req>.json; findings/<key>.json
(-> findings/done/ once its RCA has run); ledger.jsonl records every step. RCA reports:
workspace/rca/<key>.md. Every model call goes through ask_q16.py (manager role, lease-aware).
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASE = REPO / "workspace" / "session-reviews"
PENDING, DONE, FINDINGS = BASE / "pending", BASE / "done", BASE / "findings"
LEDGER = BASE / "ledger.jsonl"
# The promise ledger is its own file (t950): ledger.jsonl says a review step RAN; this says a promise the
# assistant made in the transcript was discharged by a later action in it. Readers of the old file keep
# parsing what they parse today.
PROMISE_LEDGER = BASE / "promise-ledger.jsonl"
# The cover log is its own file (t1437): ledger.jsonl says a review step RAN; promise-ledger.jsonl says a
# promise the assistant made was discharged by a later action in it; this says what ONE cover search
# consulted -- its terms, its task and commit candidates, the `git log -S` it ran -- and what it answered.
COVER_CHECKS = BASE / "cover-checks.jsonl"
# The RCA's trigger list (I'll / let me / next I'll) plus the phrasings that mean the same commitment.
PROMISE_PHRASES = re.compile(r"\bI'?ll\b|\bI will\b|\bI'?m going to\b|\bI am going to\b|"
                             r"\bnext,?\s+I'?ll\b|\blet me\b", re.I)
PROMISE_WINDOW = 6            # turns searched after a promise for the text that discharged it
RCA_DIR = REPO / "workspace" / "rca"
SKILL = REPO / "docs" / "skills" / "root-cause-analysis" / "SKILL.md"
CLAUDE_DIR = Path.home() / ".claude" / "projects" / ("-" + str(REPO).strip("/").replace("/", "-"))
DSH_DIR = Path.home() / ".dsh" / "sessions"
# The backstop reviews EVERY past session too (Jas, 2026-09-28: "I am fine with it reviewing all
# prior sessions. May as well close all gaps we can." -- ahead of scoping). Sessions active within
# the idle window are still left alone until they go quiet.
DISCOVER_SINCE = 0
CHUNK_CHARS = 60_000        # ~15k tokens of session text per extract call; stated in the prompt


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _log(row):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": _now(), **row}) + "\n")


def record_promise_ledger(ts, session, out_path=None):
    """Append one row per promise sentence in ts to out_path (default PROMISE_LEDGER). Returns the rows appended, in order."""
    sc = _sc()
    at = _now()
    rows = []
    for ti, (role, text) in enumerate(ts):
        if role != "assistant":
            continue
        for s in sc.sentences(text):
            m = PROMISE_PHRASES.search(s)
            if not m:
                continue
            q = sc.topic_words(s)
            # No context matching on a sentence with under two topic words: on that few, a 0.5 share is
            # one shared word, which almost any following turn pays for. A false "executed" hides the
            # only thing this ledger is for -- a commitment the session never came back to.
            matches = []
            if len(q) >= 2:
                for r_role, r_text in ts[ti + 1:ti + 1 + PROMISE_WINDOW]:
                    if r_role == "assistant" and any(
                            len(q & sc.topic_words(x)) / len(q) >= 0.5 for x in sc.sentences(r_text)):
                        matches.append(r_text)
            # Artefacts are read from the MATCHING turns only: an id in an unrelated following turn is
            # not this promise's discharge, and crediting it there invents work that was never tied.
            artefact = []
            for t in matches:
                for a in re.findall(r"[Tt]\d{3,}", t) + re.findall(r"[0-9a-f]{7,40}", t):
                    a = a.lower()
                    if a not in artefact:
                        artefact.append(a)
            rows.append({"session": session, "turn": ti, "quote": s, "promise_phrase": m.group(0),
                         "matching_actions_within_n_turns": len(matches), "artefact": artefact,
                         "verdict": "executed-in-text" if matches else "undischarged",
                         # turns() drops tool traffic, so all this endpoint can ever claim is the text.
                         "evidence_channel": "assistant-text", "at": at})
    if not rows:
        return rows
    path = PROMISE_LEDGER if out_path is None else Path(out_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
    except OSError as e:
        # A ledger that cannot be written must say so: silence here reads as "no promises were made".
        print(f"session_review: promise ledger {path} not written ({type(e).__name__}: {e})",
              file=sys.stderr)
    return rows


# THE COVER LOG (t1437, RCA ee154c612ac9 sec 4 DE-1): 'does git log -S07f933e5 return 07f933e5, and
# what did the search offer instead' can only be answered today by re-running by hand a search that runs
# once, inside a job that logs nothing. This is the record half of that search.
def record_cover_check(key, terms, tasks_seen, commits_seen, git_cmd, answer, examined_count,
                       out_path=COVER_CHECKS):
    """Append what one cover search consulted and what it answered; return the row.

    A row goes in for EVERY search, including one whose lists are all empty -- a search that found
    nothing is the record this file exists for, and a log that records only the searches that found
    something is silent on exactly the rows that need explaining. Every value is stored whole, in the
    order the search produced it: `terms` is the list the term filter kept and `git_cmd` the argv that
    ran, so a later reader can re-run the very search that missed a cover.
    """
    row = {"key": key, "terms": terms, "tasks_seen": tasks_seen, "commits_seen": commits_seen,
           "git_cmd": git_cmd, "answer": answer, "examined_count": examined_count, "at": _now()}
    path = Path(COVER_CHECKS if out_path is None else out_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError as e:
        # Said, never swallowed: a cover log that cannot be written must not read as "nothing was searched".
        print(f"session_review: cover log {path} not written ({type(e).__name__}: {e})", file=sys.stderr)
    return row


# ------------------------------------------------------------------ reading sessions
def turns(harness, path):
    """[(role, text)] of a session's human and assistant text; tool traffic and reasoning dropped."""
    out, bad = [], 0
    if harness == "dsh":
        raw = subprocess.run(["zstdcat", str(path)], capture_output=True, text=True, timeout=120).stdout
    else:
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
    for line in raw.splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            bad += 1
            continue
        t, d = r.get("type"), r.get("data") or {}
        if harness == "dsh":
            if t == "user/message":
                out.append(("user", "\n".join(c.get("text", "") for c in d.get("content") or []
                                             if c.get("type") == "text")))
            elif t == "assistant/message":
                out.append(("assistant", "\n".join(c.get("text", "") for c in
                                                   (d.get("message") or {}).get("content") or []
                                                   if c.get("type") == "text")))
        else:
            msg = r.get("message") or {}
            if t == "user" and isinstance(msg.get("content"), str):
                out.append(("user", msg["content"]))
            elif t == "assistant":
                out.append(("assistant", "\n".join(c.get("text", "") for c in msg.get("content") or []
                                                   if isinstance(c, dict) and c.get("type") == "text")))
    if bad > 1:
        print(f"session_review: {bad} unreadable lines in {path}", file=sys.stderr)
    return [(role, txt) for role, txt in out if txt.strip()]


def chunks(ts, limit=CHUNK_CHARS):
    """Turn-boundary chunks under `limit` chars. A single turn longer than the limit is its own chunk
    (never cut): the prompt says how many chunks there are."""
    out, cur, n = [], [], 0
    for role, txt in ts:
        block = f"### {role.upper()}\n{txt}\n"
        if cur and n + len(block) > limit:
            out.append("".join(cur))
            cur, n = [], 0
        cur.append(block)
        n += len(block)
    if cur:
        out.append("".join(cur))
    return out


# ------------------------------------------------------------------ queueing sessions
def _req_name(harness, sid):
    return f"{harness}-{re.sub(r'[^A-Za-z0-9_.-]', '_', sid)}.json"


def reviewed_to(harness, sid):
    """How many turns of this session earlier reviews already covered (0 if none)."""
    n = 0
    # FAILED COUNTS AS HANDLED: a request given up after MAX_UNUSABLE is with Claude as a task; queueing
    # the same turns again would restart the loop that failed/ exists to stop.
    for f in list(DONE.glob(f"{_req_name(harness, sid)[:-5]}*.json")) + \
            list(PENDING.glob(f"{_req_name(harness, sid)[:-5]}*.json")) + \
            list(FAILED.glob(f"{_req_name(harness, sid)[:-5]}*.json")):
        try:
            n = max(n, int(json.loads(f.read_text(encoding="utf-8")).get("to_turn") or 0))
        except (OSError, ValueError) as e:
            print(f"session_review: unreadable request {f.name}: {e}", file=sys.stderr)
    return n


def enqueue(harness, sid, path):
    """Queue the part of a session no review has covered yet. A session that resumed after an earlier
    review (the 24 h backstop, say) is queued again for ONLY its new turns -- never reviewed twice,
    never left with an unread tail (Jas, 2026-09-28: "will it cause duplicate work if the back stop
    fired?")."""
    total = len(turns(harness, path))
    start = reviewed_to(harness, sid)
    if total <= start:
        return None
    base = _req_name(harness, sid)[:-5]
    name = f"{base}.json" if start == 0 else f"{base}--from-{start}.json"
    PENDING.mkdir(parents=True, exist_ok=True)
    req = {"harness": harness, "id": sid, "path": str(path), "queued": _now(),
           "from_turn": start, "to_turn": total, "ended_epoch": Path(path).stat().st_mtime}
    (PENDING / name).write_text(json.dumps(req, indent=1) + "\n", encoding="utf-8")
    _log({"event": "queued", "request": name, "from_turn": start, "to_turn": total})
    return name


def _dsh_path(sid):
    hits = sorted(DSH_DIR.glob(f"*/{sid}/session.v4.jsonl.zstd")) + \
        sorted(DSH_DIR.glob(f"*/session-{sid}/session.v4.jsonl.zstd"))
    return hits[-1] if hits else None


def enqueue_current():
    """This session: DeepSeek when $DSH_SESSION_ID is set, else the newest Claude Code transcript."""
    sid = os.environ.get("DSH_SESSION_ID")
    if sid:
        p = _dsh_path(sid)
        return enqueue("dsh", sid, p) if p else None
    cl = sorted(CLAUDE_DIR.glob("*.jsonl"), key=lambda f: f.stat().st_mtime)
    return enqueue("claude", cl[-1].stem, cl[-1]) if cl else None


def discover(idle_h=24.0):
    """Backstop: queue sessions (both harnesses) idle >= idle_h and never queued."""
    cut, out = time.time() - idle_h * 3600, []
    for f in CLAUDE_DIR.glob("*.jsonl"):
        if DISCOVER_SINCE <= f.stat().st_mtime < cut:
            n = enqueue("claude", f.stem, f)
            out += [n] if n else []
    for f in DSH_DIR.glob("*/session-*/session.v4.jsonl.zstd"):
        if DISCOVER_SINCE <= f.stat().st_mtime < cut:
            n = enqueue("dsh", f.parent.name, f)
            out += [n] if n else []
    return out


def pending_requests():
    return sorted(PENDING.glob("*.json")) if PENDING.is_dir() else []


MAX_UNUSABLE = 2
FAILED = BASE / "failed"


def _unusable(item_file, why, what, file_task=None):
    """Count one unusable attempt ON the item (a request or a finding). Below MAX_UNUSABLE it stays
    queued for another pass; at it, it moves to failed/ with every attempt recorded, and ONE task is
    filed for Claude -- two identical failures are a fault to find, not a roll to repeat (the
    task_worker rule). Returns True when it was given up."""
    item_file = Path(item_file)
    d = json.loads(item_file.read_text(encoding="utf-8"))
    d.setdefault("unusable", []).append({"at": _now(), "why": why})
    if len(d["unusable"]) < MAX_UNUSABLE:
        item_file.write_text(json.dumps(d, indent=1) + "\n", encoding="utf-8")
        return False
    FAILED.mkdir(parents=True, exist_ok=True)
    dest = FAILED / item_file.name
    dest.write_text(json.dumps(d, indent=1) + "\n", encoding="utf-8")
    item_file.unlink()
    title = f"Session review could not {what}: {item_file.stem}"
    body = (f"{len(d['unusable'])} unusable attempts in a row, so it was taken out of the queue rather than "
            f"retried forever. Each attempt: " + "; ".join(f"{u['at']} {u['why']}" for u in d["unusable"])
            + f". The item and its history: {_rel(dest)}; the job logs are under "
            f"workspace/run-logs/dispatch/. Find why (too long a part for one call? a reply with no JSON "
            f"list?), fix that, then move the file back to {_rel(item_file.parent)}/.")
    tid = (file_task or _file_for_claude)(title, body)
    _log({"event": f"{what}-given-up", "item": item_file.name, "task": tid, "attempts": len(d["unusable"])})
    return True


def _rel(p):
    try:
        return str(Path(p).resolve().relative_to(REPO))
    except ValueError:
        return str(p)


def _file_for_claude(title, body):
    sys.path.insert(0, str(REPO / "projects" / "app-repo"))
    import taskstore
    return taskstore.add(title, area="workspace", body=body, source="session_review",
                         needs=__import__("role_store").escalate("technical"))


def pending_findings():
    """The RCA queue, in order. A failing workspace check (hourly_checks.py, key check-*) goes FIRST
    (t1153, Jas 2026-10-13: "a RCA should be triggered that goes to the top of that queue"): the
    dispatcher keeps this order among RCA items, which carry no `created` to sort by."""
    if not FINDINGS.is_dir():
        return []
    return sorted(FINDINGS.glob("*.json"), key=lambda p: (not p.name.startswith("check-"), p.name))


# ------------------------------------------------------------------ the model
ASK_MODEL = ASK_ROLE = None
RCA_LIMIT_S = 9000


def _tail(purpose, out, keep=40):
    """Put the model call's own log lines into this job's log (2026-10-02). They were captured and
    dropped, so an RCA that ran past 6000 s said only that -- not whether the context guard fired,
    how many tool rounds ran, or which tool it was in. The last `keep` lines, said as the last ones."""
    lines = [l for l in (out or "").splitlines() if l.strip()]
    if not lines:
        return
    shown = lines[-keep:]
    head = (f"  --- {purpose}: model-call log, last {len(shown)} of {len(lines)} lines ---"
            if len(lines) > keep else f"  --- {purpose}: model-call log ({len(lines)} lines) ---")
    print("\n".join([head] + ["    " + l for l in shown]), file=sys.stderr, flush=True)


def ask(prompt_text, purpose, budget=16000, tools=True, wait_s=6 * 3600, rounds=None, limit_s=6000):
    """The manager role via ask_q16.py (lease-aware). Waits while the lease is held or the server is
    down (exits 4 / 3); returns the answer text, '' if none."""
    # PRESSING PAUSE HOLDS THIS (Jas, 2026-10-30: "when I press pause it stops every model from working on
    # tasks"). This is where a model actually starts, so the pause is asked HERE rather than only in the loop that
    # called in: must_hold answers for the production screen, for ALL devices, and for this work's device.
    try:
        import scheduler_commands as _sc
        _hold, _why = _sc.must_hold()
        if _hold:
            print(f"  PAUSED: {_why}")
            return ''
    except Exception as _e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: could not read the pause flags ({type(_e).__name__}: {_e}) -- continuing")
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="session-review-"))
    pf, of = tmp / "prompt.txt", tmp / "answer.md"
    pf.write_text(prompt_text, encoding="utf-8")
    cmd = [sys.executable, str(REPO / "scripts" / "ask_q16.py"), "--prompt", str(pf), "--out", str(of),
           "--budget", str(budget), "--max-escalations", "0", "--timeout", str(limit_s - 600), "--purpose", purpose]
    if not tools:
        cmd.append("--no-tools")
    if rounds:
        cmd += ["--max-rounds", str(rounds)]
    # THE JOB'S ROLE AND MODEL (2026-10-02). The scheduler routes session review to whichever role
    # has it on its list, but this always asked the Manager's model with the Manager's tools.
    if ASK_MODEL:
        cmd += ["--model", ASK_MODEL]
    if ASK_ROLE:
        cmd += ["--role", ASK_ROLE]
    deadline = time.time() + wait_s
    while True:
        try:
            cp = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, timeout=limit_s)
            rc = cp.returncode
            _tail(purpose, (cp.stdout or "") + (cp.stderr or ""))
        except subprocess.TimeoutExpired as e:
            _tail(purpose, (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes)
                  else (e.stdout or ""))
            # A PART THAT RUNS PAST THE LIMIT IS AN UNUSABLE ANSWER, NOT A CRASH (2026-09-30). This
            # raised out of extract(), the request stayed pending, and claude-1ff24a44 was re-run ten
            # times from 09-29 05:40, ~2-3 h of Q4 each. subprocess.run has already killed the child,
            # and a dead holder's lease reads as free (modellease.held).
            print(f"  session_review: {purpose} ran past {limit_s} s -- counted as an unusable answer",
                  file=sys.stderr)
            return ""
        if rc in (3, 4) and time.time() < deadline:
            time.sleep(120)
            continue
        return of.read_text(encoding="utf-8", errors="replace") if of.is_file() else ""


def _json_list(text):
    """The last JSON list in the text, or None when there is no usable one."""
    errors = []
    for m in reversed(list(re.finditer(r"\[\s*(?:\{.*?\}\s*,?\s*)*\]", text or "", re.S))):
        try:
            v = json.loads(m.group(0))
        except ValueError as e:
            errors.append(str(e))
            continue
        if isinstance(v, list):
            return v
    if errors:
        print(f"session_review: no parseable JSON list ({len(errors)} candidates: {errors[0]})",
              file=sys.stderr)
    return None


# ------------------------------------------------------------------ extract: session -> findings
EXTRACT = """You are reviewing a finished working session ({harness} session {sid}),
part {part} of {parts} (the session text is split at turn boundaries, nothing cut inside a turn;
when a session resumed after an earlier review, only its NEW turns are here).

This session ended on {ended}.

Find, reading for MEANING (there is no keyword list):
  (a) "deferral": work someone said would be done later, parked, suggested, or noted -- and no
      task number (tNNN) was given for it;
  (b) "unresolved": a problem, bug or failure noticed in the session that was neither fixed nor
      filed as a task by the end of it.
  (c) "off-flow": a process, workflow or permanent script that was run, created or changed in the
      session and is NOT on the Flow chart below -- the chart is the system of record (Jas,
      2026-10-01), so a new process must be put on the chart before it runs. One-off commands a
      person ran by hand are not processes; a new script, cron line, service, timer, hook, or a
      repeated multi-step procedure is.
  (d) "for-jas": an OFFER or QUESTION put to Jas that waits on his word (a decision, a permission,
      a preference: "want me to delete X?", "shall I add sudo for Y?"). It is not dropped work --
      it goes to his Review list, not to a root-cause analysis. Use (a) only for work nobody asked
      him about.
Do NOT report something done or filed later in the same session.
YOU DO NOT NEED TO CHECK WHETHER A TASK OR A LATER COMMIT COVERS A FINDING. After you answer, the
harness searches every task (open and finished) and every commit since {ended_day} for each quote,
and asks about each one separately. Just list the candidates. Fill "covered_by" only when the
session itself names the task (a tNNN in or next to the quote) or a task title below plainly is it.

OPEN TASKS:
{open_tasks}

FINISHED IN THE LAST 14 DAYS (these cover a finding too):
{done_tasks}

THE FLOW CHART (tasks, then supporting scripts by file name):
{flow}

End your answer with ONLY a JSON list (empty list if nothing):
[{{"kind": "deferral"|"unresolved"|"off-flow"|"for-jas", "quote": "<a whole sentence copied EXACTLY from the session text>",
   "where": "<who said it, roughly when>", "why_missed": "<why no task exists>",
   "covered_by": "tNNN, fixed:<commit hash>, or empty",
   "disposition": "unresolved"|"resolved-in-session"|"deferred"|"for-jas"|"refuted"}}]
("disposition": say "resolved-in-session" or "refuted" when the quote turns out settled or wrong; leave it
 out and the harness fills it from the kind.)

SESSION TEXT:
{text}
"""


# FEWER ROUNDS, THE LIST INLINE (2026-10-01). A part took 25-99 min over 7-11 tool rounds, most of them
# reading QUEUE.md and task files to decide covered_by -- each round re-reading the whole growing
# context. The open-task titles now come with the prompt, and the rounds are capped.
EXTRACT_ROUNDS = 5


def open_tasks():
    try:
        sys.path.insert(0, str(REPO / "projects" / "app-repo"))
        import taskstore
        return "\n".join(f"{m['id']} {m['title']}" for m, _ in taskstore.ordered()
                         if m.get("status") not in ("done", "cancelled"))
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        return f"(open tasks unreadable: {type(e).__name__}: {e} -- read workspace/tasks/QUEUE.md)"


def done_tasks(days=14):
    """Tasks finished or cancelled in the last `days` days, id and title (t623: a finished task
    covers a finding as surely as an open one; the reviewer only ever saw the open queue)."""
    out, cutoff = [], time.time() - days * 86400
    for f in sorted((REPO / "workspace" / "tasks" / "done").glob("t*.md"), reverse=True):
        try:
            if f.stat().st_mtime < cutoff:
                continue
            m = {k: v for k, v in reversed(re.findall(r"^(id|title|status):\s*(.*)$",
                                                     f.read_text(encoding="utf-8", errors="replace"), re.M))}
        except OSError as e:
            print(f"  NOTE: {f} unreadable ({e}); skipped", file=sys.stderr)
            continue
        out.append(f"{m.get('id', f.name[:4])} [{m.get('status', 'done')}] {m.get('title', '')}")
    return "\n".join(out) or "(none)"


def _ids(listing):
    return re.findall(r"^(t\d{3,})\b", listing, re.M)


def flow_summary():
    """The chart in a few lines for the reviewer: task names, then helper script file names."""
    try:
        d = json.loads((REPO / "projects" / "local-inference" / "models" / "FLOW.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return f"(FLOW.json unreadable: {e} -- report nothing as off-flow)"
    tasks = "; ".join(f'{t["id"]} ({t["name"]}: {t.get("command", "")})' for t in d["tasks"])
    helpers = ", ".join(sorted(h["script"].rsplit("/", 1)[-1] for h in d.get("helpers", [])))
    return f"TASKS: {tasks}\nSUPPORTING SCRIPTS: {helpers}"


# A session is reviewed as several part files: workspace/session-reviews/pending/ holds
# "claude-<sid>.json" AND "claude-<sid>--from-244.json". The part suffix records where the
# review resumed; it is not new work, so it must not enter the finding key (t763).
_PART_SUFFIX = re.compile(r"--from-\d+\.json$")


def _req_key_name(req_name):
    """The request file name with its review-part suffix removed (t763)."""
    return _PART_SUFFIX.sub(".json", str(req_name))


def _fkey(req_name, quote):
    return hashlib.sha1(f"{_req_key_name(req_name)}\n{quote}".encode()).hexdigest()[:12]


def extract(req_file, asker=ask, note_task=None, file_task=None, judge=None):
    """Session -> finding files. Returns (new findings, covered). An unusable reply keeps the
    request pending; the parts before it are already written and are not asked again."""
    req_file = Path(req_file)
    req = json.loads(req_file.read_text(encoding="utf-8"))
    ts = turns(req["harness"], req["path"])
    parts = chunks(ts[req.get("from_turn", 0):req.get("to_turn") or len(ts)])
    # EACH PART IS KEPT AS IT LANDS (t606, 2026-10-01). Parts 1-2 of a 6-part session were thrown
    # away when part 3 timed out, and the retry re-read the whole session -- twice, ~3 h of Q4 each
    # time. Now a part's findings are written as soon as it answers, `parts_done` is saved on the
    # request, and a retry starts at the first part that has not answered. The split is stable:
    # the same turns give the same parts, so part numbers mean the same thing on every attempt.
    new = covered = 0
    start = int(req.get("parts_done", 0))
    for i, text in enumerate(parts, 1):
        if i <= start:
            continue
        import skills                               # plan 3c-i; ask() runs with tools
        ended = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(req.get("ended_epoch") or time.time()))
        ot, dt = open_tasks(), done_tasks()
        req["_candidates"] = _ids(ot) + _ids(dt)
        prompt, _sk = skills.with_skills(EXTRACT.format(harness=req["harness"], sid=req["id"], part=i,
                                                        parts=len(parts), text=text, flow=flow_summary(),
                                                        open_tasks=ot, done_tasks=dt, ended=ended,
                                                        ended_day=ended[:10]),
                                         "session_review", role=ASK_ROLE, can_open=True)
        ans = asker(prompt, f"session review: {req_file.stem} part {i}/{len(parts)}", **(
            {"tools": False} if asker is ask else {}))
        rows = _json_list(ans)
        if rows is None:
            _log({"event": "extract-unusable", "request": req_file.name, "part": i, "parts_done": i - 1})
            _unusable(req_file, f"part {i}/{len(parts)}: " + ("no answer (timed out or empty)" if not ans.strip()
                                                               else "no JSON list in the answer"),
                      "review the session", file_task)
            return new, covered
        rows = [r for r in rows if isinstance(r, dict) and r.get("quote")]
        for r in rows:
            # THE SENTENCE IS CONTEXT, NOT THE QUOTE (trial v3, 2026-10-02): replacing fragments with
            # their sentence collapsed three findings into one sentence that also named t320, and the
            # "a finished task the quote names covers it" rule then hid all three. The model's words
            # stay the finding; the sentence only widens the cover search.
            sentence, found = _anchor(r["quote"], text)
            if not found:
                r["quote_unverified"] = True
            elif sentence != r["quote"]:
                r["context"] = sentence
        j = judge or (ask if asker is ask else None)
        if j:
            for r in rows:
                if not str(r.get("covered_by") or "").strip() and r.get("kind") in ("deferral", "unresolved"):
                    r["covered_by"], r["cover_check"] = verify_cover(r, ended[:10], j)
        n, c = _keep(req_file, req, rows, note_task, file_task)
        new, covered = new + n, covered + c
        req["parts_done"] = i
        req.pop("_candidates", None)
        req.pop("unusable", None)           # progress resets the give-up count: stuck means NO progress
        req_file.write_text(json.dumps(req, indent=1) + "\n", encoding="utf-8")
    DONE.mkdir(parents=True, exist_ok=True)
    req_file.rename(DONE / req_file.name)
    _log({"event": "reviewed", "request": req_file.name, "findings": new, "covered": covered})
    return new, covered


def _anchor(quote, text):
    """(the whole sentence of the session text that contains `quote`, True) or (quote, False).

    Frontline trial, 2026-10-02: Gemma quoted fragments ("the scoper automation") where the prompt
    asks for a sentence, which starved the cover search of terms. The session text has the
    sentence, so the code recovers it rather than asking again. Whitespace and case are ignored."""
    q = re.sub(r"\s+", " ", str(quote)).strip().strip('"').strip()
    if not q:
        return quote, False
    for line in text.splitlines():                  # a sentence never crosses a line or a turn
        flat = re.sub(r"\s+", " ", line)
        i = flat.lower().find(q.lower())
        if i < 0:
            continue
        start = max(flat.rfind(". ", 0, i), flat.rfind("? ", 0, i), flat.rfind("! ", 0, i))
        start = 0 if start < 0 else start + 2
        ends = [j for j in (flat.find(". ", i + len(q)), flat.find("? ", i + len(q)),
                            flat.find("! ", i + len(q))) if j >= 0]
        end = (min(ends) + 1) if ends else len(flat)
        return flat[start:end].strip(), True
    if q.lower() in re.sub(r"\s+", " ", text).lower():   # it spans lines: real, kept as given
        return quote, True
    return quote, False


STOP = set("""about above after again against because before being below between could doing during
having himself itself other should their there these those through under until where which while
would whether something someone anything nothing already still might maybe going thing things
first second third later today tomorrow change changed changes check checked directly
really actually probably properly simply instead without within whole every needs needed
think thought looks looked right point parts""".split())


def _terms(quote, n=5):
    """The quote's searchable terms: identifiers first (files, symbols, tNNN), then its longest
    uncommon words. A search is only as good as its terms, so this is code, not the model."""
    ids = [w for w in (x.strip(".,;:()'\"`-/") for x in re.findall(r"[A-Za-z0-9][A-Za-z0-9_./-]{3,}", quote))
           if len(w) >= 4 and re.search(r"[_./]|\d", w)]
    words = sorted({w.lower() for w in re.findall(r"[A-Za-z]{5,}", quote)} - STOP, key=len, reverse=True)
    out = []
    for t in ids + words:
        if t and t.lower() not in [o.lower() for o in out]:
            out.append(t)
    return out[:n]


_GRAMS = {}


def _six_grams(text):
    """Every run of six words in text, as a set, made once per distinct text (t790 step 6)."""
    g = _GRAMS.get(text)
    if g is None:
        w = re.findall(r"[a-z0-9]+", text.lower())
        g = _GRAMS[text] = {tuple(w[j:j + 6]) for j in range(len(w) - 5)}
    return g


def _quotes_finding(quote, text):
    """A task that quotes the finding's words has recorded them, not done the work.

    SET MEMBERSHIP, NOT A NESTED SCAN (t790 step 6, 2026-10-12). This compared every six-word window
    of the quote with every window of the task by slicing lists, for every task, for every candidate:
    cProfile put 814 of a reference-set score's 859 s here (56,376 calls). Same answer -- a shared run
    of six words -- from the task's windows built once."""
    q = re.findall(r"[a-z0-9]+", quote.lower())
    if len(q) < 6:
        return False
    g = _six_grams(text)
    return any(tuple(q[i:i + 6]) in g for i in range(len(q) - 5))


def task_candidates(quote, limit=5):
    """[(tid, status, title, matched terms)] -- tasks, open AND finished, sharing the most terms."""
    terms = _terms(quote)
    if not terms:
        return []
    scored = []
    tasks = REPO / "workspace" / "tasks"
    for f in list(tasks.glob("t*.md")) + list((tasks / "done").glob("t*.md")):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"  NOTE: {f} unreadable ({e}); skipped", file=sys.stderr)
            continue
        low = text.lower()
        hit = [t for t in terms if t.lower() in low]
        ident = any(re.search(r"[_./]|\d", t) for t in hit)
        if len(hit) >= 2 or ident:
            meta = {k: v for k, v in reversed(re.findall(r"^(id|title|status):\s*(.*)$", text, re.M))}
            tid = meta.get("id", f.name[:4])
            named = tid in re.findall(r"\bt\d{3,}\b", quote)          # the quote points at it
            title_hits = sum(1 for t in terms if t.lower() in meta.get("title", "").lower())
            notes = low.rfind("## notes")
            body_low = low[notes + len("## notes"):] if notes != -1 else low
            body_hits = sum(1 for t in terms if t.lower() in body_low)
            body_ident = sum(1 for t in terms if re.search(r"[_./]|\d", t) and t.lower() in body_low)
            quoted = 1 if _quotes_finding(quote, text) else 0
            scored.append((3 * title_hits + 2 * body_ident + body_hits + 0.5 * ident + 10 * named - 4 * quoted,
                           tid, meta.get("status", "?"), meta.get("title", ""), hit))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [(tid, st, ti, hit) for _, tid, st, ti, hit in scored[:limit]]


# A commit that only RECORDS a session cannot cover its items: the handover and session log quote the item,
# and the review's own trial files quote the answer key (2026-10-05: 1ec31f54, the commit adding the
# audit's reference lists, was offered as a cover in most of 19 sessions; Q4 took d04048e2, a handover
# commit, as the cover of an unfiled miss). Their diffs are invisible to the search. Task files are
# searched as TASKS (task_candidates), never as a commit that "fixed" something.
NOT_A_COVER = (":(exclude)workspace/session-reviews", ":(exclude,glob)workspace/HANDOVER-*",
               ":(exclude)workspace/sessions", ":(exclude)workspace/transcript-index.jsonl",
               ":(exclude)workspace/PROJECT-INDEX.md", ":(exclude)workspace/tasks")


# THE TERM FILTER, IN ONE PLACE (t1437): it was a comprehension inline in commit_candidates' for-line,
# so nothing outside that function could say which terms the search ran. The recorded terms have to be
# the terms searched, and a second copy of the rule drifts from the first.
def commit_terms(quote):
    """The identifier-shaped terms worth a pickaxe search, in _terms' order, capped at 3 -- the
    selection commit_candidates has always made, now visible to the code that records it."""
    return [t for t in _terms(quote) if re.search(r"[_./]|\d", t)][:3]


# THE PICKAXE COMMAND, IN ONE PLACE (t1437): built inside commit_candidates the argv was invisible from
# outside it, so the command that ran could never be recorded. It is built here and run there.
def commit_query(term, since, limit=3):
    """The `git log -S<term>` argv, in the order commit_candidates runs it."""
    return ["git", "-C", str(REPO), "log", f"--since={since} 00:00:00", "--format=%h %ad %s",
            "--date=short", "-n", str(limit), f"-S{term}", "--", ".", *NOT_A_COVER]


def commit_candidates(quote, since, limit=3):
    """['hash date subject'] -- commits since the session whose message or diff names a term."""
    out = []
    for t in commit_terms(quote):
        r = subprocess.run(commit_query(t, since, limit), capture_output=True, text=True, timeout=120)
        out += [l for l in r.stdout.splitlines() if l and l not in out]
    return out[:limit]


COVER = """One question, answer in ONE line.

A working session ended on {since}. In it, this was said and looks like unfinished work:
  "{quote}"

These existing tasks and later commits share words with it:
{options}

Does one of them ALREADY cover exactly this work (the same thing, not just the same topic)?
Answer with exactly one of:
  tNNN          -- that task covers it (finished tasks count)
  fixed:HASH    -- that commit did it
  none          -- none of them is this work
"""


def verify_cover(r, since, judge):
    """(covered_by, how) for one candidate finding. The harness searches; the model only picks from
    what was found -- a small model told to search before reporting did not search at all (Frontline
    trial 2026-10-02: 0 tool calls), and a title list missed t328 because 'systemd' was not in it."""
    probe = r["quote"] + (" " + r["context"] if r.get("context") else "")
    tasks = task_candidates(probe)
    # A prose deferral shares no identifier with the task that prevents its class, so the word scan
    # alone misses it (t786: five reviews minted findings an open task already covered). The meaning
    # search is the only thing offered for such a quote, so it runs before the nothing-found return.
    by_words = {tid for tid, *_ in tasks}
    try:
        import session_review_trial as _trial
        sem = _trial._semantic_cover(probe, "")
    except Exception as e:  # noqa: BLE001
        # A dead embeddings server must look like a dead server, not like an unrelated session.
        print(f"  NOTE: meaning search skipped ({type(e).__name__}: {e})", file=sys.stderr)
        sem = None
    if sem and sem[1] not in by_words:
        meta = {k: v for k, v in re.findall(r"^(title|status):\s*(.*)$", task_text(sem[1]), re.M)}
        tasks.append((sem[1], meta.get("status", "?"), meta.get("title", ""), f"meaning {sem[0]:.2f}"))
    # THE SEARCH RECORDS ITSELF (t1437, RCA ee154c612ac9 DE-1): the terms, the candidates it found,
    # the pickaxe argv it ran, and the judge's answer. verify_cover returned from five places and kept
    # none of it, so "what did the search offer instead of 07f933e5" has no artefact to read.
    terms = commit_terms(probe)
    commits = commit_candidates(probe, since)
    if not tasks and not commits:
        # A search that found nothing is the row the RCA asked for, so it is recorded on this path too.
        record_cover_check(key=r.get("key") or "", terms=commit_terms(probe),
                           tasks_seen=[tid for tid, *_ in tasks],
                           commits_seen=[c.split()[0] for c in commits],
                           git_cmd=[commit_query(t, since) for t in terms], answer="",
                           examined_count=len(tasks) + len(commits))
        return "", "no task or commit shares its terms"
    opts = [f"  {tid} [{st}] {ti}" for tid, st, ti, _h in tasks] + [f"  fixed:{c}" for c in commits]
    said = r["quote"] + (f'\n  (in the sentence: "{r["context"]}")' if r.get("context") else "")
    ans = judge(COVER.format(since=since, quote=said[:900], options="\n".join(opts)),
                "session review: cover check", **({"tools": False, "budget": 2000} if judge is ask else {}))
    # One row per search, written before every return below: all four answers share these values.
    record_cover_check(key=r.get("key") or "", terms=commit_terms(probe),
                       tasks_seen=[tid for tid, *_ in tasks],
                       commits_seen=[c.split()[0] for c in commits],
                       git_cmd=[commit_query(t, since) for t in terms], answer=ans,
                       examined_count=len(tasks) + len(commits))
    allowed = {tid for tid, *_ in tasks} | {c.split()[0] for c in commits}
    m = re.search(r"\b(t\d{3,})\b|fixed:\s*([0-9a-f]{7,})|\bnone\b", (ans or "").strip().splitlines()[-1]
                  if (ans or "").strip() else "", re.I)
    if not m or m.group(0).lower() == "none":
        return "", f"judged none of: {', '.join(sorted(allowed))}"
    pick = m.group(1) or m.group(2)
    if pick not in allowed:
        return "", f"answer {pick!r} was not among the options offered"
    if m.group(1) and pick not in by_words:
        # A semantic link is not word-bound, so prove_cover keeps its veto over it: a task the item's
        # own words do not share is coincidence, not coverage. Lexical ids are already vetted upstream.
        prov = prove_cover({"verdict": "covered", "cover": pick, "quote": r["quote"],
                            "context": r.get("context", ""), "what": r.get("what", "")})
        if prov.get("verdict") != "covered":
            return "", f"unproven semantic cover {pick}: shares too few of the item's words"
    return (pick if m.group(1) else f"fixed:{pick}"), f"picked from: {', '.join(sorted(allowed))}"


def _sc():
    sys.path.insert(0, str(REPO / "scripts"))
    import session_candidates
    return session_candidates


# PROVE A COVER (t663, 2026-10-05). Q4 called 5 of the 9 real misses it kept COVERED and named a wrong
# cover every time (t328 twice, t243, t206, and a handover commit): a model's COVERED is an assertion.
# Kept only when the cited task shares COVER_SHARE of the item's topic words; else it is MISSED, said so.
# 0.2 on the audit: rejects all four wrong covers of real misses and three wrong-id covers, keeps t036;
# loses one right cover (t629, 0.14) -- a false MISSED costs a look, a false COVERED loses the item.
COVER_SHARE = 0.2


def task_text(tid):
    root = REPO / "workspace" / "tasks"
    f = next(iter(list(root.glob(f"{tid}--*.md")) + list((root / "done").glob(f"{tid}--*.md"))), None)
    return f.read_text(encoding="utf-8", errors="replace").split("\n## Notes")[0] if f else ""


def created(tid):
    return (re.search(r"^created:\s*(\S+)", task_text(tid), re.M) or [None, ""])[1]


def prove_cover(r):
    """r with verdict/cover settled by code: a task cover must share the item's subject; a commit
    cover must not be a session close-out ('session: ...'), which records everything and fixes nothing."""
    cover = r.get("cover") or ""
    if r.get("verdict") != "covered" or cover == "in-session":
        return r
    if re.fullmatch(r"t\d{3,}", cover):
        q = _sc().topic_words(f'{r["quote"]} {r.get("what", "")}')
        if len(q) < 2:
            q |= _sc().topic_words(r.get("context", ""))
        share = len(q & _sc().topic_words(task_text(cover))) / max(1, len(q))
        if share >= COVER_SHARE:
            return r
        why = f"unproven cover {cover}: shares {share:.2f} of the item's words (< {COVER_SHARE})"
    else:
        subj = subprocess.run(["git", "-C", str(REPO), "log", "-1", "--format=%s", cover],
                              capture_output=True, text=True, timeout=30).stdout.strip()
        if subj and not subj.startswith("session:"):
            return r
        why = f"unproven cover {cover}: " + ("a session close-out commit" if subj else "no such commit")
    return {**r, "verdict": "missed", "cover": "", "model_cover": cover, "overruled": why}




# THE SCRIPTED REVIEW (t663, 2026-10-05; Jas: "try the scripted approach first"). Code reads the whole
# session -- no model does -- and hands the Manager only what the session never came back to, grouped,
# each item with the tasks (open AND finished) and commits that might cover it. The Manager answers one
# short question per item; code proves every COVERED. Measured on 19 audited sessions: the code step
# keeps all 9 real unfiled misses (Q4 reading whole sessions surfaced 4 of 10) in ~40 items a session.
# NOT covered, said so: kind (c) "off-flow" -- a process missing from the Flow chart -- which needed a
# model reading the session against the chart and has no counterpart here.
SCRIPTED = True             # flipped 2026-10-07: Swift's grade on the audit set, 10/10 real misses (t663)
BATCH = 12                  # items per Manager call: short answers, and a retry costs one batch

TRIAGE = """You are triaging sentences from a finished working session ({harness} session {sid}, ended {ended_day}).
Code found each one: the session NEVER CAME BACK to it afterwards (nothing later in the session repeats its
subject). For each, decide whether it is work that could be FORGOTTEN, and whether an existing task already
covers it. Use only what is shown. Do not invent task numbers.

For each item answer with ONE of:
  "drop"     -- not forgettable work: progress narration, a plain fact, praise, something about pushing
                commits (pushing is off on this machine), or already clearly done in the sentences shown
  "covered"  -- one of the LISTED tasks/commits is THE SAME work, not just the same topic; give its id
  "missed"   -- real dropped work and nothing listed is it. Pick its kind:
                  deferral   work put off, parked, promised or suggested, with no task number
                  unresolved a problem or bug noticed and neither fixed nor filed
                  for-jas    an offer or question that waits on Jas's word

Answer with ONE JSON object per line, one line for EVERY number, nothing else:
{{"n": 1, "verdict": "missed", "kind": "deferral", "what": "<under 12 words: what needs doing>"}}
{{"n": 2, "verdict": "covered", "cover": "t123"}}
{{"n": 3, "verdict": "drop"}}

ITEMS:
{items}
"""

_KIND = {"FOR-JAS": "for-jas", "NEEDS-OK": "for-jas", "NOTICED": "unresolved"}


def scripted_items(ts, since, as_of=""):
    """[{n, quote, context, type, repeats, near, commits, offered}] -- the session's dropped, grouped.
    `as_of` (a trial's): ignore tasks created after it, or a review's own later filings cover themselves."""
    sc = _sc()
    out = []
    for g in sc.group(sc.dropped(ts)):
        c = g["rows"][0]
        probe = f'{c["quote"]} {c["before"]} {c["after"]}'
        near = [t for t in task_candidates(probe, limit=12 if as_of else 5)
                if not as_of or created(t[0]) < as_of][:5]
        # THE CLOSEST TASK BY MEANING is offered too (2026-10-07): the word search misses a task that
        # says the same thing in other words. Only offered -- the Manager still decides, and
        # prove_cover() still has to agree.
        try:
            import session_review_trial as _trial
            sem = _trial._semantic_cover(c["quote"], as_of)
        except Exception as e:                                      # noqa: BLE001 -- said, words still searched
            print(f"  NOTE: meaning search skipped ({type(e).__name__}: {e})", file=sys.stderr)
            sem = None
        if sem and sem[1] not in [t[0] for t in near]:
            _meta = dict(re.findall(r"^(title|status):\s*(.*)$", task_text(sem[1]), re.M))
            near.append((sem[1], _meta.get("status", "?"), _meta.get("title", ""), f"meaning {sem[0]:.2f}"))
        try:
            commits = commit_candidates(probe, since)
        except subprocess.TimeoutExpired:
            commits = []                  # the pickaxe is slow on a loaded box; tasks still searched
        commits = [x for x in commits if not re.match(r"\S+ \S+ session:", x)]   # a close-out fixes nothing
        out.append({"n": len(out) + 1, "quote": c["quote"], "context": f'{c["before"]} {c["after"]}'.strip(),
                    "type": c["type"], "turn": c["turn"], "repeats": [r["quote"] for r in g["rows"][1:]],
                    "near": [f"{tid} [{st}] {ti}" for tid, st, ti, _h in near],
                    "commits": commits, "offered": [t[0] for t in near] + [x.split()[0] for x in commits]})
    return out


def _render(items):
    lines = []
    for it in items:
        lines.append(f'{it["n"]}. [{it["type"]}] "{it["quote"]}"\n   around: "{it["context"][:300]}"')
        if it["repeats"]:
            lines.append(f'   (said {len(it["repeats"]) + 1} times)')
        lines.append("   may cover: " + (" | ".join(it["near"] + [f"commit {c}" for c in it["commits"]])
                                         or "(nothing found)"))
    return "\n".join(lines)


def triage(items, asker, harness, sid, since, purpose="session review: triage"):
    """Manager verdicts for `items`, in batches. Returns rows [{..item.., verdict, kind, what, cover}].
    An item the Manager did not answer is kept as MISSED, said so -- nothing silently drops. A cover it
    names must be one that was offered, and prove_cover() then has to agree."""
    rows = []
    for i in range(0, len(items), BATCH):
        part = items[i:i + BATCH]
        ans = asker(TRIAGE.format(harness=harness, sid=sid, ended_day=since, items=_render(part)),
                    f"{purpose} {i // BATCH + 1}/{-(-len(items) // BATCH)}")
        said = {}
        for line in (ans or "").splitlines():
            line = line.strip().strip(",")
            if line.startswith("{"):
                try:
                    r = json.loads(line)
                    said[int(r.get("n"))] = r
                except (ValueError, TypeError):
                    print(f"  NOTE: unreadable triage line: {line[:120]!r}", file=sys.stderr)
        for it in part:
            r = said.get(it["n"])
            row = dict(it)
            if r is None:
                row.update(verdict="missed", kind=_KIND.get(it["type"], "deferral"), what="", cover="",
                           unanswered=True)
            else:
                v = str(r.get("verdict", "")).lower()
                cover = str(r.get("cover") or "").strip()
                if v == "covered" and cover not in it["offered"]:
                    row.update(verdict="missed", kind=_KIND.get(it["type"], "deferral"), what="", cover="",
                               overruled=f"cover {cover!r} was not among those offered")
                else:
                    row.update(verdict=v if v in ("drop", "covered", "missed") else "missed",
                               kind=r.get("kind") if r.get("kind") in ("deferral", "unresolved", "for-jas")
                               else _KIND.get(it["type"], "deferral"),
                               what=str(r.get("what") or ""), cover=cover if v == "covered" else "")
            rows.append(prove_cover(row))
    return rows


def extract_scripted(req_file, asker=None, note_task=None, file_task=None):
    """Session -> findings the scripted way. Same contract as extract(): (new findings, covered)."""
    asker = asker or (lambda prompt, purpose: ask(prompt, purpose, tools=False, budget=6000, limit_s=3000))
    req_file = Path(req_file)
    req = json.loads(req_file.read_text(encoding="utf-8"))
    ts = turns(req["harness"], req["path"])[req.get("from_turn", 0):req.get("to_turn") or None]
    since = time.strftime("%Y-%m-%d", time.gmtime(req.get("ended_epoch") or time.time()))
    items = scripted_items(ts, since)
    # THE PROMISE LEDGER (t950, RCA 05248445e09b D-1): this endpoint reads the transcript, so it is the
    # one place that sees a promise and its discharge side by side -- and the one that has the request's
    # session id. Not inside scripted_items: its signature is depended on by two other files.
    record_promise_ledger(ts, req["id"])
    rows = triage(items, lambda p, why: asker(p, f"{why} ({req_file.stem})"), req["harness"], req["id"], since)
    found = [{"kind": r["kind"], "quote": r["quote"], "context": r["context"],
              "where": f'assistant, turn {r["turn"]} of {len(ts)}',
              "why_missed": r["what"] or "never mentioned again; no covering task found",
              "covered_by": r["cover"], **({"overruled": r["overruled"]} if r.get("overruled") else {})}
             for r in rows if r["verdict"] != "drop"]
    req["_candidates"] = _ids(open_tasks()) + _ids(done_tasks())
    new, covered = _keep(req_file, req, found, note_task, file_task)
    req.pop("_candidates", None)
    req_file.write_text(json.dumps(req, indent=1) + "\n", encoding="utf-8")
    DONE.mkdir(parents=True, exist_ok=True)
    req_file.rename(DONE / req_file.name)
    _log({"event": "reviewed", "mode": "scripted", "request": req_file.name, "items": len(items),
          "dropped_by_manager": sum(r["verdict"] == "drop" for r in rows), "findings": new, "covered": covered})
    return new, covered


# THE FINDING CONTRACT'S BUCKET (t676): a quote the session settled, or that is wrong, used to be forced into
# kind=unresolved and queued for a full RCA (RCA 80095ffd2448). A finding the model did not label is filled
# from its kind, so every record carries one.
DISPOSITIONS = ("unresolved", "resolved-in-session", "deferred", "for-jas", "refuted")
_KIND_DISPOSITION = {"deferral": "deferred", "for-jas": "for-jas"}


REQUIRED_FIELDS = ("command", "budget_s", "exit_code", "completed", "budget_source", "population")
# The six facts a verification claim has to carry before anyone can check it. A consumer
# validates by membership, so budget_s is a field name, not prose to be skimmed.
VERDICT_VERIFIED = "VERIFIED"
VERDICT_UNVERIFIED = "UNVERIFIED"
VERDICT_REFUSED = "REFUSED"
# REFUSED is structural: no measured budget, no population, a budget its own command cannot
# fit. UNVERIFIED is empirical: the command ran and said nothing. t808's dry-run exited 143
# (128+SIGTERM) under a 280s budget, printed none of the grep tokens, and was recorded as a
# pass; these three words are what make that sentence refuseable.


def measured_worst_case(command, measurements):
    """Largest seconds ever measured for this exact command, or None if nothing measured it.

    Rows are read, never mutated, so the writer that records a duration can live in its own
    chunk. A row with no seconds, or with a seconds field that is not a number, is skipped
    rather than counted as zero: an unrecorded duration is not a fast one, and reading it as
    one would let a budget pass on the strength of a truncated row.
    """
    if not isinstance(command, str):
        return None
    worst = None
    for row in measurements or []:
        if not isinstance(row, dict) or row.get("command") != command:
            continue
        seconds = row.get("seconds")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            continue
        value = float(seconds)
        if worst is None or value > worst:
            worst = value
    return worst


def evaluate(claim, measurements):
    """Judge a verification claim against the measurements that are supposed to back it.

    Pure: every fact is read from the two arguments, nothing is opened, spawned, phoned home
    or model-called, and neither argument is mutated. One reason is appended per rule
    violated, never stopping at the first, so the caller sees the whole gap at once. Rules
    (a)-(d) are structural and REFUSE the claim; (e)-(g) mean the command ran and the result
    is UNVERIFIED.
    """
    reasons = []
    claim = claim if isinstance(claim, dict) else {}
    structural = False
    empirical = False

    for field in REQUIRED_FIELDS:
        if field not in claim:
            reasons.append("missing required field: %s" % field)
            structural = True

    source = claim.get("budget_source")
    if source != "measured":
        reasons.append("budget_source=%r is not a measurement: run the command and record "
                       "its seconds" % (source,))
        structural = True

    measured = measured_worst_case(claim.get("command"), measurements)
    if measured is None:
        reasons.append("no measured worst case for %r: run the command and record its seconds"
                       % (claim.get("command"),))
        structural = True

    budget = claim.get("budget_s")
    if measured is not None and isinstance(budget, (int, float)) \
            and not isinstance(budget, bool):
        low, high = measured, 2 * measured
        seconds = float(budget)
        if not low <= seconds <= high:
            side = "below" if seconds < low else "above"
            reasons.append("budget_s=%s is %s the measured worst case %ss (admissible %s-%s)"
                           % (budget, side, measured, low, high))
            structural = True

    code = claim.get("exit_code")
    if code != 0:
        killed = {124: "timed out, SIGTERM", 137: "128+SIGKILL", 143: "128+SIGTERM"}
        label = killed.get(code)
        if label is None and isinstance(code, str) and code.lstrip("-").isdigit():
            label = killed.get(int(code))
        if label:
            reasons.append("exit_code=%s = %s: killed by its budget, not a result"
                           % (code, label))
        else:
            reasons.append("exit_code=%s: the verification command did not succeed" % (code,))
        empirical = True

    if claim.get("completed") is not True:
        reasons.append("completed is not True: the command must finish, not merely start")
        empirical = True

    if code == 0:
        tokens = claim.get("success_tokens")
        named = list(tokens) if isinstance(tokens, (list, tuple, set, frozenset)) else []
        observed = claim.get("observed_tokens")
        hits = 0
        for token in named:
            needle = str(token)
            if isinstance(observed, str):
                if needle in observed:
                    hits += 1
            elif isinstance(observed, (list, tuple, set, frozenset)):
                if any(str(seen) == needle for seen in observed):
                    hits += 1
        if named and hits == 0:
            reasons.append("exit 0 with none of the %d named success tokens in the output: "
                           "a truncated run produces this" % len(named))
            empirical = True

    if structural:
        verdict = VERDICT_REFUSED
    elif empirical:
        verdict = VERDICT_UNVERIFIED
    else:
        verdict = VERDICT_VERIFIED
    return {"verdict": verdict, "reasons": reasons}


# t607: a deferral record is complete only when it states where it is tracked, who owns
# it, how to verify it, how to detect decay, and the intended end-state.
DEFERRAL_FIELDS = ("tracked_as", "owner", "verify_command", "decay_test", "intended_state")


class DeferralContractError(Exception):
    """Raised when a deferral record does not satisfy Jas's t607 contract."""


def deferral_violations(record):
    """Return one violation per required deferral field, [] for non-deferrals."""
    if not (hasattr(record, "get") and callable(record.get)):
        raise TypeError(f"deferral record must be a mapping, got {type(record).__name__}")
    if str(record.get("kind")) != "deferral":
        return []

    violations = []
    for field in DEFERRAL_FIELDS:
        if field not in record:
            violations.append(f"missing {field}")
            continue
        value = record[field]
        if not isinstance(value, str):
            violations.append(f"{field} is not a string")
            continue
        stripped = value.strip()
        if not stripped:
            violations.append(f"{field} is empty")
        elif field == "tracked_as" and not (
            re.fullmatch(r"t\d+", stripped)
            or (
                stripped.startswith("UNTRACKED")
                and stripped[len("UNTRACKED"):].lstrip(" :")
            )
        ):
            violations.append(
                "tracked_as is not a tNNN tracker (t607) "
                "or UNTRACKED with a non-empty reason"
            )
    return violations


def refuse_if_deficient(record):
    """Raise DeferralContractError naming every deficient deferral field; return None."""
    violations = deferral_violations(record)
    if violations:
        raise DeferralContractError("; ".join(violations))
    return None


# THE VERIFICATION LEDGER (t1011). A deferral that promises a check is discharged only when a row
# records that check running -- exit code, the digest of the bytes the command produced, and the
# digest of the artifact as it stood after the check. Prose promises rot: workspace/rca/1190f930bdb9.md
# is a session that said it would check first and provably never did, and workspace/rca/1a7761b3791e.md
# row 3506 is the mirror -- a pass recorded over a mutation that never happened. Both are visible only
# once the row pins those three things; a timestamp alone proves nothing about what the check observed.
VERIFICATION_LEDGER = REPO / "workspace" / "verification-ledger.jsonl"


def record_verification(finding_key, verify_command, artifact="", out=None, timeout=300,
                        target="", ledger=VERIFICATION_LEDGER):
    """Run `verify_command` (or adopt a check that already ran) and append its row.

    Append-only: a row states what was observed at the moment the check ran, so nothing here
    rewrites an older row -- the digest of the artifact keeps the drift visible. out=None spawns
    the command with a timeout; a mapping out supplies exit/stdout/stderr and spawns nothing,
    which is how a check that ran elsewhere gets its row. A check killed by its timeout is
    recorded exit=124 with the output it captured before the kill: the exception is written into
    the row, never re-raised (the caller must get a row) and never swallowed (a swallowed timeout
    is a verification indistinguishable from one that never ran).
    """
    def _as_text(value):
        # A timeout hands back raw bytes; a stream that produced nothing is None.
        if isinstance(value, bytes):
            return value.decode("utf-8", "replace")
        return "" if value is None else str(value)

    if out is None:
        try:
            cp = subprocess.run(verify_command, shell=True, capture_output=True,
                                text=True, timeout=timeout)
            exit_code, stdout, stderr = cp.returncode, cp.stdout, cp.stderr
        except subprocess.TimeoutExpired as exc:
            exit_code, stdout, stderr = 124, exc.stdout, exc.stderr
    else:
        exit_code, stdout, stderr = out["exit"], out.get("stdout"), out.get("stderr")

    output = _as_text(stdout) + _as_text(stderr)      # both streams, whole: never sliced
    artifact_digest = ""
    if target:
        f = Path(target)
        if f.is_file():
            artifact_digest = hashlib.sha256(f.read_bytes()).hexdigest()

    row = {"finding_key": finding_key,
           "verify_command": verify_command,
           "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "exit": int(exit_code),
           "artifact": artifact,
           "sha256_of_output": hashlib.sha256(output.encode("utf-8")).hexdigest(),
           "sha256_of_artifact": artifact_digest}
    ledger = Path(ledger)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def verification_rows(ledger=VERIFICATION_LEDGER):
    """The rows a ledger holds. A ledger that does not exist is [] -- 'no check recorded' has to
    read as an empty record, not as an error, so a caller can ask it the question at all."""
    ledger = Path(ledger)
    if not ledger.is_file():
        return []
    return [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def can_discharge(finding_key, ledger=VERIFICATION_LEDGER):
    """True only when a row names this finding key: a deferral is closed on evidence, not on a
    promise (rca 1190f930bdb9's promise is the case this rule exists to make impossible)."""
    return any(r.get("finding_key") == finding_key for r in verification_rows(ledger))


DEFERRAL_ROW_FIELDS = ("item", "reason", "closer", "session", "time")
DEFERRAL_LEDGER = REPO / "workspace" / "deferrals.jsonl"


def record_deferral(item, reason, closer, session, ledger=DEFERRAL_LEDGER):
    """Append the row that says an item was DEFERRED, not forced: what, why, who closes it, in
    which session, when. A deferral spoken only in chat is a written instruction to a future
    reader (workspace/rca/a61a86bb5cb0.md, turn 255 of 395); this is the record half."""
    values = {"item": item, "reason": reason, "closer": closer, "session": session}
    values = {k: ("" if v is None else str(v)) for k, v in values.items()}
    empty = sorted(k for k, v in values.items() if not v.strip())
    if empty:
        raise ValueError(f"deferral row rejected: {', '.join(empty)} is empty")
    row = dict(values)
    row["time"] = _now()
    with open(ledger, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def deferral_rows(ledger=DEFERRAL_LEDGER):
    """The deferral rows a ledger holds. A ledger that does not exist is [] -- 'no deferral
    recorded' is an empty record, not an error. A torn final line comes back whole under
    _unparsable, so a lost row is visible as a loss rather than silently absent."""
    ledger = Path(ledger)
    if not ledger.is_file():
        return []
    rows = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            rows.append({"_unparsable": line})
    return rows


def _keep(req_file, req, found, note_task=None, file_task=None):
    """Write one part's findings (or note the task that covers them). Returns (new, covered)."""
    FINDINGS.mkdir(parents=True, exist_ok=True)
    new = covered = 0
    cands = req.get("_candidates") or []
    for r in found:
        cov = str(r.get("covered_by") or "").strip()
        # A tNNN THE QUOTE ITSELF NAMES, if that task is finished, covers it (t623): the speaker
        # pointed at the task, and the reviewer saw only the open queue.
        if not cov:
            cov = next((t for t in re.findall(r"\bt\d{3,}\b", r["quote"]) if _task_finished(t)), "")
        if not cov:
            cov = _archive_cover(r["quote"])
        if cov.startswith("fixed:"):
            covered += 1
            _log({"event": "covered", "request": req_file.name, "fixed_by": cov[6:], "quote": r["quote"]})
            continue
        if str(r.get("kind")) == "for-jas":
            k = _fkey(req_file.name, r["quote"])
            if _for_jas(k, req, r, file_task):
                new += 1
            continue
        if re.fullmatch(r"t\d{3,}", cov):
            covered += 1
            (note_task or _note_task)(cov, f"Session review ({req['harness']} {req['id']}) found this "
                                           f"already covered here: \"{r['quote']}\"")
            _log({"event": "covered", "request": req_file.name, "task": cov, "quote": r["quote"]})
            continue
        k = _fkey(req_file.name, r["quote"])
        f = FINDINGS / f"{k}.json"
        if f.exists() or (FINDINGS / "done" / f"{k}.json").exists():
            continue
        r = {**r, "disposition": r["disposition"] if r.get("disposition") in DISPOSITIONS
             else _KIND_DISPOSITION.get(str(r.get("kind")), "unresolved")}
        f.write_text(json.dumps({**r, "key": k, "request": req_file.name, "session": req["id"],
                                 "harness": req["harness"], "found": _now(),
                                 "candidates_consulted": cands}, indent=1) + "\n")
        if str(r.get("kind")) == "deferral":
            try:
                record_deferral(r["quote"], str(r.get("why_missed") or "").strip(),
                                str(r.get("tracked_as") or "").strip()
                                or "UNTRACKED: session-review deferral with no owner recorded",
                                req["id"], ledger=DEFERRAL_LEDGER)
            except Exception as e:
                print(f"  NOTE: deferral row not written for {k}: {type(e).__name__}: {e}",
                      file=sys.stderr)
        _log({"event": "finding", "request": req_file.name, "key": k, "kind": r.get("kind"),
              "quote": r["quote"]})
        new += 1
    return new, covered


def _archive_cover(quote):
    """The id of a FINISHED task that shares a DISTINCTIVE word with the quote, or "" (t623).

    Distinctive is strict on purpose -- a false cover hides a real finding: an identifier-shaped
    token (6+ chars with _ . / or a digit: a file, a symbol, a test), found in at most 2 finished tasks (a word in many is vocabulary, not a reference). The reviewer's
    own search_tasks check is the main defence; this catches the cover it did not look for."""
    sys.path.insert(0, str(REPO / "projects" / "app-repo"))
    import taskstore
    # IDENTIFIERS ONLY (2026-10-02 Frontline trial): rarity is not distinctiveness for English.
    # "documenting" sits in one finished task (t612, a crontab audit) and made it the false cover of
    # a systemd-edit quote. A file name, snake_case, a dotted or digit-bearing token is a reference.
    words = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9_./-]{5,}", quote or "")
             if re.search(r"[_./]|\d", w) and len(w.strip("./-")) >= 6}
    if not words:
        return ""
    docs = []
    for f in Path(taskstore.DONE).glob("t*.md"):
        try:
            docs.append((f.name.split("--")[0], f.read_text(encoding="utf-8", errors="replace").lower()))
        except OSError as e:
            print(f"  NOTE: {f} unreadable ({e}); skipped", file=sys.stderr)
            continue
    for w in sorted(words, key=len, reverse=True):
        holders = [tid for tid, text in docs if w in text]
        if 1 <= len(holders) <= 2:
            return sorted(holders)[0]
    return ""


def _task_finished(tid):
    return any((REPO / "workspace" / "tasks" / "done").glob(f"{tid}--*.md"))


FOR_JAS = BASE / "for-jas"


# THE BACKLOG MUST SHRINK (Jas, 2026-10-09: "The backlog is growing by quite a bit. What is causing
# that?"). On 2026-10-07/08 this file filed 249 tasks -- 157 "Decide:" offers and 92 RCA tasks --
# because both paths filed one task per finding, and the RCA's `covered_by` was the model's guess.
# Now both paths look for an OPEN task carrying the same GROUP marker and add a note to it instead:
#   - a Decide offer groups by SESSION: one task per reviewed session lists all of its open questions;
#   - an RCA groups by the FILE its first fix changes: one open task per file collects every RCA
#     that wants that file changed, and the scoper chunks it.
# Mechanical, not judged: the marker is a line in the task body, read back from the open tasks.
GROUP_RE = r"^GROUP: (\S+)$"
FIX_FILE_RE = re.compile(r"\b((?:scripts|projects|docs|deploy|workspace)/[\w./-]+\.(?:py|sh|json|md|jsonl|service))\b")


def _group_marker(kind, value):
    return f"GROUP: {kind}:{value}"


def _open_group_task(marker, tasks=None):
    """The open task whose body carries `marker` on its own line, else ''."""
    if tasks is None:
        sys.path.insert(0, str(REPO / "projects" / "app-repo"))
        import taskstore
        tasks = [(m, b) for m, b in taskstore.ordered() if m.get("status") not in ("done", "cancelled")]
    for m, b in tasks:
        if re.search(rf"^{re.escape(marker)}$", b or "", re.M):
            return m.get("id", "")
    return ""


def _fix_file(text):
    m = FIX_FILE_RE.search(text or "")
    return m.group(1) if m else ""


def _for_jas(key, req, r, file_task=None):
    """An offer or question waiting on Jas -> ONE task on his Review list (needs: jas), never an RCA.
    Keyed, so the same quote is filed once. Returns True when filed."""
    FOR_JAS.mkdir(parents=True, exist_ok=True)
    mark = FOR_JAS / f"{key}.json"
    if mark.exists():
        return False
    title = "Decide: " + re.sub(r"\s+", " ", r["quote"]).strip()[:80]
    body = (f"From the session review ({req['harness']} {req['id']}): an offer or question that waits "
            f"on your word, so it is on your Review list instead of being analysed.\n\n"
            f"\"{r['quote']}\" -- {r.get('where', '')}\n\n{r.get('why_missed', '')}")
    marker = _group_marker("session", req.get("id") or key)
    tid = "" if file_task else _open_group_task(marker)
    if tid:
        (_note_task)(tid, f"Another open question from the same session:\n\n\"{r['quote']}\" -- "
                          f"{r.get('where', '')}\n\n{r.get('why_missed', '')}")
    else:
        tid = (file_task or _file_for_jas)(title, body + f"\n\n{marker}")
    mark.write_text(json.dumps({**r, "key": key, "task": tid, "found": _now()}, indent=1) + "\n")
    _log({"event": "for-jas", "request": req.get("id"), "key": key, "task": tid, "quote": r["quote"]})
    return True


def _file_for_jas(title, body):
    sys.path.insert(0, str(REPO / "projects" / "app-repo"))
    import taskstore
    # TO PRODUCTION, NOT TO JAS (Jas, 2026-10-03, docs/PREFERENCES.md): "I don't want anything
    # assigned to me for review ... the scoper can flag it for me if it needs my input."
    return taskstore.add(title, area="workspace", body=body, source="session_review", needs="production")


def _note_task(tid, text):
    sys.path.insert(0, str(REPO / "projects" / "app-repo"))
    import taskstore
    taskstore.add_note(tid, text, who="session-review")


# ------------------------------------------------------------------ rca: finding -> report + tasks
RCA = """Run an IN-DEPTH root-cause analysis of the finding below, applying the
root-cause-analysis skill IN FULL (it follows). Jas, 2026-09-28: "I don't want a short one ...
get into all possible causes and create data collection plans as per the skill."

Required, per the skill: every candidate cause, each held open or closed WITH evidence you read
(use your tools: transcripts under ~/.claude/projects, workspace/, scripts/, git history); a
thread ledger; the stop rule; where evidence runs out, a DATA COLLECTION PLAN (Rule 4); and the
fix at the most upstream stage that could have prevented the class (Rule 5). Use the skill's
report shape.

THE FINDING (from session review {request}):
{finding}

THE SKILL:
{skill}

Write the full report. Then END with ONLY a JSON list -- every proposed solution and every
data-collection plan, the MOST UPSTREAM fix FIRST:
[{{"title": "<imperative, under 90 chars>", "kind": "solution"|"data-collection",
   "why": "<the cause it addresses>", "change": "<what to change or build, and where>",
   "covered_by": "<tNNN of an OPEN task that already covers this item, or empty>"}}]
Read workspace/tasks/QUEUE.md first. An item an open task already covers is NOT a new task: name it
in covered_by and it becomes a note there. Everything else becomes ONE task (the first item), with
the rest as its checklist -- one finding, one task. (Jas, 2026-10-01: the backlog must shrink.)
"""


def _task_open(tid):
    return any((REPO / "workspace" / "tasks").glob(f"{tid}--*.md"))


def task_status_from_disk(tid, tasks_dir=None):
    """The owner task's status as it is written on disk (t940).

    A finding is closed when its owner task closes, so this is the fact the dispatcher has to read
    before it sends a key to RCA a second time. Every gap is 'unknown' AND said: a task file that is
    not there, one with no front matter, and front matter with no status: line are three different
    facts, and a silent default is how t235's fix shipped while its finding still read 'deferred'.
    """
    d = Path(tasks_dir) if tasks_dir else (REPO / "workspace" / "tasks")
    tid = str(tid or "")
    for sub, pat in (("", f"{tid}--*.md"), ("", f"{tid}.md"),
                     ("done", f"{tid}--*.md"), ("done", f"{tid}.md")):
        base = d / sub if sub else d
        hits = sorted(base.glob(pat)) if base.is_dir() else []
        if not hits:
            continue
        f = hits[0]
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            print(f"session_review: task {tid}: {f} unreadable ({e}); status 'unknown'", file=sys.stderr)
            return "unknown"
        bounds = [i for i, ln in enumerate(lines) if ln == "---"]
        if len(bounds) < 2:
            print(f"session_review: task {tid}: {f} has no front matter; status 'unknown'", file=sys.stderr)
            return "unknown"
        for ln in lines[bounds[0] + 1:bounds[1]]:
            if ln.startswith("status:"):
                return ln.split(":", 1)[1].strip()
        print(f"session_review: task {tid}: {f} front matter has no status: line; status 'unknown'",
              file=sys.stderr)
        return "unknown"
    print(f"session_review: no task file for {tid!r} under {d}; status 'unknown'", file=sys.stderr)
    return "unknown"


def write_rca_outcome(finding_path, *, outcome_task="", outcome_status=None, rca_report="",
                      disposition=None, covered_by=None, completed_at=None, tasks_dir=None):
    """Record the RCA's outcome ON THE FINDING (t940).

    The ledger row is written by rca() and read by nobody: the dispatch stage reads this record, so an
    RCA that files tasks and leaves the record saying 'deferred' is dispatched again -- key 028b97d00e2a
    spent a manager lane and a model lease twice on a question answered 90 minutes earlier.

    Updates in place: every key read from the file is written back, so the quote, the key, the kind and
    the veto evidence survive. The status is READ, never assumed -- an owner task that is still queued
    is not a closed finding. Never creates a record: a finding file that is not there is a fault, not a
    blank to fill in.
    """
    finding_path = Path(finding_path)
    if not finding_path.is_file():
        raise FileNotFoundError(f"no finding record at {finding_path} -- write_rca_outcome updates a "
                                f"finding, it does not create one")
    rec = json.loads(finding_path.read_text(encoding="utf-8"))
    rec["outcome_task"] = outcome_task
    rec["outcome_status"] = (outcome_status if outcome_status is not None
                             else (task_status_from_disk(outcome_task, tasks_dir) if outcome_task
                                   else "unknown"))
    rec["rca_report"] = rca_report
    if disposition is not None:
        rec["disposition"] = disposition
    if covered_by and not str(rec.get("covered_by") or "").strip():
        # A pointer already on the record was proven by the cover check; a later RCA's guess is not it.
        rec["covered_by"] = covered_by
    rec["completed_at"] = completed_at if completed_at is not None else _now()
    finding_path.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return rec


def rca_outcome_resolved(finding_path, *, tasks_dir=None):
    """True when this finding's RCA has run AND the task it filed is closed (t940).

    A finding carrying no outcome -- every record written before this fix -- is NOT resolved, so history
    is not closed retroactively. Unreadable is not resolved either: a false True re-dispatches work, a
    false False costs one look, and a silent False is what made 'RCA done, no fix found' indistinguishable
    from 'RCA never ran' (017614dd87ea).
    """
    finding_path = Path(finding_path)
    try:
        rec = json.loads(finding_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"session_review: no finding record at {finding_path}; not resolved", file=sys.stderr)
        return False
    except (OSError, ValueError) as e:
        print(f"session_review: {finding_path} unreadable as JSON ({type(e).__name__}: {e}); "
              f"not resolved", file=sys.stderr)
        return False
    if not isinstance(rec, dict):
        print(f"session_review: {finding_path} is not a finding record; not resolved", file=sys.stderr)
        return False
    if not (str(rec.get("rca_report") or "").strip() or str(rec.get("outcome_task") or "").strip()):
        return False
    status = rec.get("outcome_status")
    if not (isinstance(status, str) and status.strip()):
        status = task_status_from_disk(rec.get("outcome_task", ""), tasks_dir)
    return status == "done"


def rca(finding_file, asker=ask, file_task=None, note_task=None):
    """Finding -> workspace/rca/<key>.md + AT MOST ONE new task (the rest a checklist in it, or notes on
    the open tasks that cover them), at the top of QUEUE.md, unheld. Returns the task ids. An unusable reply files nothing and keeps the finding."""
    finding_file = Path(finding_file)
    f = json.loads(finding_file.read_text(encoding="utf-8"))
    import skills                                   # plan 3c-i; this prompt carries the RCA skill itself
    prompt, _sk = skills.with_skills(RCA.format(request=f.get("request"), finding=json.dumps(f, indent=1),
                                                skill=SKILL.read_text(encoding="utf-8")),
                                     "rca", can_open=True, already=["root-cause-analysis"])
    # AN RCA GETS 9000 S (2026-10-02). Accepted RCAs ran 2500-5600 s with a 24000-token budget and up
    # to 12 tool rounds on Q4 (~7 tok/s with both slots busy); the 6000 s cap made the long ones
    # (6ed92e74f7ed, 6a7db0132baf: t617, t620) time out on every attempt and be given up.
    ans = asker(prompt, f"RCA: session-review finding {f['key']}", 24000,
                **({"limit_s": RCA_LIMIT_S} if asker is ask else {}))
    tasks = _json_list(ans)
    if not ans.strip() or tasks is None:
        _log({"event": "rca-unusable", "key": f["key"]})
        _unusable(finding_file, "no answer (timed out or empty)" if not ans.strip() else "no JSON list in the answer",
                  "do the RCA", file_task=None)
        return []
    RCA_DIR.mkdir(parents=True, exist_ok=True)
    report = RCA_DIR / f"{f['key']}.md"
    report.write_text(f"# RCA {f['key']} -- {f.get('kind')}: \"{f['quote'][:120]}\"\n\n"
                      f"Session: {f.get('harness')} {f.get('session')} · found {f.get('found')}\n\n"
                      + ans, encoding="utf-8")
    # ONE FINDING, ONE TASK (2026-10-01). "One task per solution and per data plan" filed 46 tasks on
    # 2026-10-01 alone -- most of them small schema tweaks that restated each other -- so the backlog
    # grew faster than every model together could shrink it. Items an open task covers become notes
    # on it; the rest go into ONE task, the most upstream fix first, the others as its checklist.
    ids, items = [], [t for t in tasks if isinstance(t, dict) and str(t.get("title", "")).strip()]
    head = (f"From the session review's in-depth RCA: workspace/rca/{f['key']}.md\n\n"
            f"FINDING ({f.get('kind')}): \"{f['quote']}\" -- {f.get('where', '')}\n\n")
    fresh = []
    for t in items:
        cov = str(t.get("covered_by") or "").strip()
        line = f"[{t.get('kind', 'solution')}] {str(t['title']).strip()} -- CAUSE: {t.get('why', '')} -- CHANGE: {t.get('change', '')}"
        if re.fullmatch(r"t\d{3,}", cov) and _task_open(cov):
            (note_task or _note_task)(cov, head + "This RCA proposes, and says this task covers:\n- " + line)
            ids.append(cov)
        else:
            fresh.append((t, line))
    if fresh:
        first = fresh[0][0]
        plan = "\n".join(f"- [ ] {line}" for _t, line in fresh)
        target = _fix_file(fresh[0][1])
        marker = _group_marker("file", target) if target else ""
        into = _open_group_task(marker) if (marker and not file_task) else ""
        if into:
            (note_task or _note_task)(into, head + "Another RCA whose fix changes the same file -- its plan:\n" + plan)
            ids.append(into)
        else:
            body = head + "THE FIX, then the rest of the RCA's plan as a checklist:\n" + plan
            ids.append((file_task or _file_task)(str(first["title"]).strip()[:90],
                                                 body + (f"\n\n{marker}" if marker else "")))
    (FINDINGS / "done").mkdir(parents=True, exist_ok=True)
    # THE RENAMED PATH IS WHERE THE RECORD LIVES NOW: the writer opens the finding by path, so the
    # outcome goes to the file's new home -- the one the dispatcher reads -- not to where it was queued.
    finding_file = finding_file.rename(FINDINGS / "done" / finding_file.name)
    rel = report.relative_to(REPO) if report.is_relative_to(REPO) else report
    # THE OUTCOME LANDS ON THE RECORD, NOT ONLY THE LEDGER (t940): key 028b97d00e2a's RCA completed at
    # 07:59:21Z, its report and ledger row exist, and its finding still read covered_by "" /
    # disposition "deferred", so the same key was dispatched for RCA again at 09:20:20Z. The ledger row
    # below is unchanged: the writer is added, not substituted.
    write_rca_outcome(finding_file, outcome_task=(ids[0] if ids else ""), rca_report=str(rel),
                      disposition=("covered" if ids else "unresolved"), covered_by=(ids[0] if ids else ""),
                      completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                      tasks_dir=REPO / "workspace" / "tasks")
    _log({"event": "rca", "key": f["key"], "report": str(rel), "tasks": ids})
    return ids


def _file_task(title, body):
    sys.path.insert(0, str(REPO / "projects" / "app-repo"))
    import taskstore
    # JOINS THE QUEUE, NOT ITS HEAD (2026-10-07). Each RCA moved its task to the top; 39 RCAs in an
    # evening pushed the work Jas had put first (t790, t791) to 12th and 13th, and scoping took the
    # newest RCA task instead. The scheduler finishes them as they come (Jas: "no rush").
    tid = taskstore.add(title, area="workspace", body=body, source="session-rca")
    return tid


def status():
    rows = []
    if LEDGER.is_file():
        for line in LEDGER.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                print("session_review: an unreadable ledger line", file=sys.stderr)
    return {"queued": len(pending_requests()), "findings_waiting": len(pending_findings()),
            "reviewed": sum(r.get("event") == "reviewed" for r in rows),
            "rcas": sum(r.get("event") == "rca" for r in rows),
            "tasks": sum(len(r.get("tasks") or []) for r in rows if r.get("event") == "rca")}

_IMPORT_STDOUT = sys.stdout

def rca_coverage(findings_dir=None, ledger_path=None):
    d = Path(findings_dir) if findings_dir is not None else FINDINGS
    lp = Path(ledger_path) if ledger_path is not None else LEDGER
    
    found_keys = set()
    for p in d.rglob("*.json"):
        found_keys.add(p.stem)
    
    covered_keys = set()
    unreadable = []
    
    if lp.is_file():
        for i, line in enumerate(lp.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            try:
                row = json.loads(line)
                if isinstance(row, dict) and row.get("event") == "rca":
                    covered_keys.add(row.get("key"))
            except ValueError:
                unreadable.append(i)
    
    missing = found_keys - covered_keys
    return (sorted(missing), sorted(unreadable))

def check_rca_coverage(findings_dir=None, ledger_path=None, out=sys.stdout):
    stream = sys.stdout if out is _IMPORT_STDOUT else out
    # ZERO FINDINGS IS NOT "EVERY FINDING IS COVERED" (L-146). With no finding on disk, missing is empty
    # whatever the ledger says, and returning 0 would report coverage of nothing as a pass.
    if not any(Path(findings_dir if findings_dir is not None else FINDINGS).rglob("*.json")):
        print("no findings examined -- nothing was checked, which is not a pass", file=stream)
        return 2
    missing, unreadable = rca_coverage(findings_dir, ledger_path)
    
    if missing:
        print(f"missing: {len(missing)}", file=stream)
        for k in missing:
            print(k, file=stream)
    
    for i in unreadable:
        print(i, file=stream)
        
    return 1 if missing or unreadable else 0


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["enqueue", "discover", "extract", "rca", "status", "rca-coverage"])
    ap.add_argument("--request")
    ap.add_argument("--finding")
    ap.add_argument("--idle-h", type=float, default=24.0)
    ap.add_argument("--scripted", action="store_true", default=None,
                    help="extract the scripted way (code finds, the Manager triages); default is SCRIPTED")
    ap.add_argument("--legacy", dest="scripted", action="store_false", help="the model reads whole sessions")
    ap.add_argument("--model", help="the model of the role the scheduler gave this job")
    ap.add_argument("--role", help="that role (its tools apply)")
    a = ap.parse_args(argv)
    global ASK_MODEL, ASK_ROLE
    ASK_MODEL, ASK_ROLE = a.model, a.role
    if a.cmd == "enqueue":
        print(enqueue_current() or "nothing queued (already queued, or no session found)")
    elif a.cmd == "discover":
        print("\n".join(discover(a.idle_h)) or "nothing new")
    elif a.cmd == "extract":
        use = SCRIPTED if a.scripted is None else a.scripted
        print("findings %d, covered %d" % (extract_scripted if use else extract)(a.request))
    elif a.cmd == "rca":
        print("tasks filed: " + (", ".join(rca(a.finding)) or "none"))
    elif a.cmd == "rca-coverage":
        return check_rca_coverage()
    else:
        print(json.dumps(status()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
