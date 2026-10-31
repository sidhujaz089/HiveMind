#!/usr/bin/env python3
"""Q16 supervises: every job that FAILED gets one decision, made from the record, and acted on.

    python3 scripts/supervise.py --dry-run   # what it would decide about, without asking
    python3 scripts/supervise.py             # decide on each new failure and act (one pass)
    python3 scripts/supervise.py --status    # the supervision ledger
    python3 scripts/supervise.py --failures  # every production failure, classified (rule 6)

Jas, 2026-09-14: "Fix the cron job." "Phase 0 was Q16 supervises Gemma." "Let's keep going
through the backlog tracking bugs and improving the harness until we can be sure a supervisor
works."

WHAT THE OLD LOOP DID (timer-gemma.sh -> gemma-decide.py + gemma-execute.py), all observed on
2026-09-14: Gemma, not Q16, decided. It read the tail of autonomy.log, so it re-diagnosed the
same stale "HTTP Error 400" every 15 minutes all day. Its RESTART tried to `kill -9` a pid from
a heartbeat directory that does not exist, and its redispatch looked for
workspace/briefs/<chunk>.md, which also does not exist, so it silently did nothing. Supervision
in name only -- and every one of those is a HARNESS defect, as Jas said, not a model one.

THE RULES:
  1. RULES DECIDE, THE ORG CHART ESCALATES (Jas, 2026-10-19: "Lets get rid of the supervisor"). No
     model and no role: dispatch_loop's tick calls decide_pending(); rule_decision() settles what the
     end of the log shows, and escalate_failure() sends the rest to the rework seat (role_store).
  2. ONE DECISION PER FAILURE. A failure worth a DECISION is a production `finished` row with
     exit 1 -- a model that tried and failed. Once decided it is recorded in
     workspace/supervision.jsonl and never re-read, which is what stops the stale-error loop.
     Exits 2 (refused to start) and 3 (accepted, not written) are NOT judgement calls and get
     no decision; they are catalogued instead -- see rule 6.
  3. EVIDENCE, NOT STATUS. The model is shown the brief, the attempt-by-attempt trail from the
     job's own log, the end of that log, and the brief's failure and switch history.
  4. IT NEVER KILLS OR STARTS A PROCESS. Every action is an artifact the scheduler or a person
     already reads:
       SWITCH         record only -- dispatch_loop already moves a failed brief to a model
                      that has not failed it, up to MAX_SWITCHES
       REWRITE-BRIEF  the old brief is archived under workspace/briefs/archive/, the new one
                      written in place. Its sha changes, so Q16 reviews it before production
                      and its failure history starts clean.
       HARNESS-BUG    a task is filed through taskstore to PRODUCTION at the top of QUEUE.md, or the
                      evidence noted on the open task for that cause, which moves to the top (Jas,
                      2026-10-19: "When a harness bug goes into production it needs to prioritize
                      over the other tasks"). It reaches Claude only when scoping it fails
                      SCOPE_RETRIES times (manager_note.apply) -- a fix that has already failed
       PARK           a park row the scheduler honours; released by editing the brief
  5. AN UNREADABLE DECISION DOES NOTHING and is recorded as unreadable, so it is visible
     instead of retried forever.
  6. EVERY FAILURE IS CATALOGUED, decision or not. Jas, 2026-09-15: "All failures need
     documentation, so when we finally get caught up on the backlog, we can go through the
     failures and understand how to better fix these at the beginning of the pipeline."
     `--failures` classifies every production failure ever recorded by what actually went
     wrong, and check_workspace.py reports the counts at session start and end. This exists
     because nine refusals over seven hours were each one line in a cron log and nobody was
     told; being un-decidable is not the same as being unimportant.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
LEDGER = REPO / "workspace" / "supervision.jsonl"
BRIEFS = REPO / "workspace" / "briefs"
DECISIONS = ("SWITCH", "REWRITE-BRIEF", "HARNESS-BUG", "PARK", "SERVER-DOWN", "DUPLICATE-RUN", "STILL-RED",
             "ESCALATED")
# Whose fault each decision says a failed build was (scope_quality). SWITCH: another model may do
# it, so the builder. PARK: "the brief or its oracle is the thing to fix".
DECISION_CLASS = {"SWITCH": "implementation", "REWRITE-BRIEF": "brief-wrong",
                  "HARNESS-BUG": "harness", "PARK": "brief-wrong", "SERVER-DOWN": "harness",
                  "DUPLICATE-RUN": "harness", "STILL-RED": "brief-wrong",
                  "ESCALATED": "unclassified"}


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _jsonl(path):
    out = []
    if Path(path).exists():
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                out.append({})
    return out


def record(row):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": now(), **row}, ensure_ascii=False) + "\n")


def key(r):
    return f"{r.get('at')}|{r.get('chunk')}|{r.get('model')}"


def pending(dispatch_rows, supervision_rows):
    """Failures with no decision yet, newest per chunk+brief only (older ones are superseded).

    A DECISION IS FOR A MODEL THAT TRIED AND FAILED -- exit 1. Widening this to "anything not
    accepted" on 2026-09-15 was wrong and is recorded here so it is not tried again: coder.py's
    exit 2 is a refusal to START, and its message already says exactly what was wrong ("the
    sandbox baseline is not what is declared: undeclared failure: X"). Asking Q16 to decide
    about a deterministic, self-explaining refusal spends a lease to be told what the log
    already says. Exit 3 is ACCEPTED-NOT-WRITTEN, a delivery fault, equally not a judgement
    call. What those failures needed was to be SEEN, and that is failure_catalogue() below.

    A yield is not a failure either: YIELD_EXITS is a job stood down for priority work.
    """
    decided = {s.get("failure") for s in supervision_rows}
    # The newest TERMINAL outcome per brief, successes included, then supervise only the ones
    # that ended badly. Filtering accepted rows out of this loop instead would leave a failure
    # pending after a later run of the same brief succeeded -- p32 failed at 22:01 and delivered
    # at 22:39, and would have taken a decision for a problem that no longer existed. Acceptance
    # clears the history, exactly as it does in dispatch_loop.failures().
    latest = {}
    for r in dispatch_rows:
        if (r.get("event") == "finished" and r.get("kind", "production") == "production"
                and (r.get("exit") == 1 or r.get("accepted"))):
            latest[(r.get("chunk"), r.get("brief_sha256"))] = r
    return [r for r in latest.values()
            if not r.get("accepted") and key(r) not in decided]


# WHAT WENT WRONG, BY EXIT CODE, and the classification is the whole point. A refusal is a
# precondition the harness failed to meet, a delivery fault is the plumbing, an attempt failure
# is the model. They need three different fixes, and counting them together as "9 failures"
# hides which one you have -- which is how seven hours went by with the same one repeating.
FAILURE_KINDS = {
    1: ("attempt-failed", "the model tried and the target suite did not go green"),
    2: ("refused-to-start", "a precondition failed before any attempt: a baseline that is not "
                            "what was declared, a target suite that is absent, an inference "
                            "path that did not answer, or a live twin job"),
    3: ("accepted-not-written", "the change was judged good and no branch carries it"),
}


def failure_catalogue(dispatch_rows=None, supervision_rows=None):
    """Every production failure ever recorded, classified, newest first. Rule 6.

    Jas asked for this so the backlog can be reviewed once the queue is caught up: the question
    it has to answer is "how do we fix these at the beginning of the pipeline", which needs the
    KIND of failure, not the count. Built from ledgers that already existed -- nothing new is
    written, so it is as complete as the record and no more.
    """
    import dispatch_loop as DL
    dispatch_rows = DL.rows() if dispatch_rows is None else dispatch_rows
    supervision_rows = _jsonl(LEDGER) if supervision_rows is None else supervision_rows
    decided = {s.get("failure"): s.get("decision") for s in supervision_rows}
    # THE FAILED RUN'S OWN LOG, BY PID (t651, 2026-10-02) -- the t398 fix evidence() already has.
    # Keyed by (chunk, model, brief) every failure showed the LAST run's log: p234's 20 refusals
    # all pointed at the accepted 03:00 run, which read as "the catalogue calls a success a refusal".
    logs, by_brief = {}, {}
    for r in dispatch_rows:
        if r.get("event") == "started":
            logs[(r.get("chunk"), r.get("pid"))] = r.get("log")
            by_brief[(r.get("chunk"), r.get("model"), r.get("brief_sha256"))] = r.get("log")
    out = []
    for r in dispatch_rows:
        if (r.get("event") != "finished" or r.get("kind", "production") != "production"
                or r.get("accepted") or r.get("exit") in DL.YIELD_EXITS):
            continue
        kind, meaning = FAILURE_KINDS.get(
            r.get("exit"), ("unclassified", f"exit {r.get('exit')} is not one coder.py documents"))
        out.append({"at": r.get("at"), "chunk": r.get("chunk"), "model": r.get("model"),
                    "exit": r.get("exit"), "kind": kind, "meaning": meaning,
                    "seconds": r.get("seconds"), "brief": r.get("brief"),
                    "decision": decided.get(key(r)),
                    "log": logs.get((r.get("chunk"), r.get("pid"))) if r.get("pid")
                    else by_brief.get((r.get("chunk"), r.get("model"), r.get("brief_sha256")))})
    return sorted(out, key=lambda x: str(x["at"]), reverse=True)


def print_failures():
    from collections import Counter
    cat = failure_catalogue()
    by = Counter(x["kind"] for x in cat)
    print(f"\n  {len(cat)} production failure(s) on record\n")
    for kind, (_c, meaning) in [(k, v) for k, v in
                                sorted({v[0]: v for v in FAILURE_KINDS.values()}.items())]:
        if by.get(kind):
            print(f"  {by[kind]:4d}  {kind}")
            print(f"        {meaning}")
    if by.get("unclassified"):
        print(f"  {by['unclassified']:4d}  unclassified -- an exit code coder.py does not document")
    undecided = [x for x in cat if x["kind"] == "attempt-failed" and not x["decision"]]
    print(f"\n  {len(undecided)} attempt failure(s) still awaiting a supervision decision")
    print(f"  {sum(1 for x in cat if x['kind'] != 'attempt-failed')} need a PERSON: nothing "
          f"else acts on a refusal or a delivery fault\n")
    for x in cat[:25]:
        d = f"  [{x['decision']}]" if x["decision"] else ""
        print(f"  {x['at']}  {x['kind']:20s} {str(x['chunk'])[:26]:26s} {str(x['model'])[:22]:22s} "
              f"{x['seconds']}s{d}")
    if len(cat) > 25:
        print(f"  ... and {len(cat) - 25} older; nothing is dropped from the catalogue itself")
    return 0


def failed_log(fail, dispatch_rows):
    """The whole log of the run that failed ('' when it cannot be found)."""
    # THE RUN THAT FAILED, BY PID (t398, 2026-10-01). Taking the newest matching "started" row read
    # whichever run came LAST -- on 2026-09-24 a re-dispatch of the same chunk on the same model had
    # just begun, so the supervisor read its near-empty log and told Q16 "ATTEMPT TRAIL (none
    # recorded)" about a run whose log held seven attempts. Falls back to the old match only when
    # the failure row carries no pid.
    started = [r for r in dispatch_rows if r.get("event") == "started"
               and r.get("chunk") == fail["chunk"] and r.get("model") == fail["model"]
               and r.get("brief_sha256") == fail.get("brief_sha256")]
    if fail.get("pid") is not None:
        started = [r for r in started if r.get("pid") == fail["pid"]] or started[:0]
    log_text = ""
    if started and started[-1].get("log"):
        lp = REPO / started[-1]["log"]
        log_text = lp.read_text(encoding="utf-8", errors="replace") if lp.is_file() else ""
    return log_text


# RULES FIRST, A MODEL ONLY FOR THE AMBIGUOUS (Jas, 2026-10-19). Since 10-12 the supervisor's 122
# decisions were HARNESS-BUG 85, REWRITE-BRIEF 15, unreadable 15 -- most of them machine states a rule
# can read off the end of the log. Read only the END: a run that lost the server once and recovered
# failed for some other reason.
TAIL_LINES = 30
SERVER_DOWN = re.compile(r"NoServer|none answers|Connection refused|Remote end closed connection"
                         r"|RemoteDisconnected|TimeoutError: timed out"
                         r"|server (?:is )?(?:down|unreachable|not answering)", re.I)
DUPLICATE = re.compile(r"REFUSED: pid \d+ is already running this job")
HTTP_REJECT = re.compile(r"HTTPError: HTTP Error (4\d\d)(?:: ([A-Za-z ]+))?")
BARE_EXC = re.compile(r"^((?:\w+\.)*\w*(?:Error|Exception)): ")
CRASH_FRAME = re.compile(r'File "[^"]*/([\w.]+\.py)", line (\d+)')
CRASH_LAST = re.compile(r"^(\w+(?:Error|Exception|Interrupt)\b.*)$")


TESTS_FAIL = re.compile(r"tests fail \(\d+: ([^,)]+)")
LAST_TESTS_FAILED = re.compile(r"last: TESTS-FAILED|stopping: two identical TESTS-FAILED")
LAST_VERDICT = re.compile(r"^\s*last:\s*([A-Z][A-Z-]{2,})\b(?:.*?([\w./-]+\.py)\s+line\s+(\d+))?")


def rule_decision(log_text, target=None):
    """A decision made from the end of the log with no model, or None for the ambiguous rest.
      DUPLICATE-RUN  a second copy of a running job refused itself: record only
      SERVER-DOWN    the model server did not answer (or timed out): retried when it does; no task, no park
      HARNESS-BUG    the harness crashed or the server rejected the call: ONE task per cause, noted on repeat
      STILL-RED      the run ended TESTS-FAILED with its own target suite still red: the brief goes back
                     to Scoping, with the job log (which holds the failing output whole) as evidence"""
    lines = [ln for ln in (log_text or "").splitlines() if ln.strip()]
    tail = lines[-TAIL_LINES:]
    dup = next((ln for ln in reversed(tail) if DUPLICATE.search(ln)), None)
    if dup:
        return {"decision": "DUPLICATE-RUN", "why": f"a second copy of a running job refused itself: {dup.strip()}"}
    hit = next((ln for ln in reversed(tail) if SERVER_DOWN.search(ln)), None)
    if hit:
        return {"decision": "SERVER-DOWN", "why": f"the model server did not answer: {hit.strip()}"}
    if any("Traceback (most recent call last)" in ln for ln in tail):
        start = max(i for i, ln in enumerate(tail) if "Traceback (most recent call last)" in ln)
        tb = tail[start:]
        frames = [m.groups() for m in (CRASH_FRAME.search(ln) for ln in tb) if m]
        last = next((m.group(1) for m in (CRASH_LAST.match(ln.strip()) for ln in reversed(tb)) if m), None)
        if frames and last:
            where = f"{frames[-1][0]}:{frames[-1][1]}"
            etype = last.split(":")[0]
            return {"decision": "HARNESS-BUG", "why": f"the harness crashed: {last} at {where}",
                    "bug_title": f"{frames[-1][0]} crashed: {etype} at {where}",
                    "bug_evidence": "\n".join(tb)}
    if target and any(LAST_TESTS_FAILED.search(ln) for ln in tail):
        red = [m.group(1).strip() for m in (TESTS_FAIL.search(ln) for ln in lines) if m]
        if red and red[-1] == Path(target).name:
            return {"decision": "STILL-RED", "why": f"the target suite {red[-1]} was still red after every "
                    "attempt -- the brief goes back to Scoping; the failing output is in the job log, whole"}
    rej = next((m for m in (HTTP_REJECT.search(ln) for ln in reversed(tail)) if m), None)
    if rej:
        code = rej.group(1) + (f" {rej.group(2).strip()}" if rej.group(2) else "")
        return {"decision": "HARNESS-BUG", "why": f"the model server rejected the request: HTTP {code}",
                "bug_title": f"model call rejected: HTTP {code}",
                "bug_evidence": "\n".join(tail[-8:])}
    exc = next((m for m in (BARE_EXC.match(ln.strip()) for ln in reversed(tail)) if m), None)
    if exc:
        return {"decision": "HARNESS-BUG", "why": f"the harness raised {exc.group(1)}",
                "bug_title": f"harness raised {exc.group(1)}", "bug_evidence": "\n".join(tail[-8:])}
    last = next((m for m in (LAST_VERDICT.match(ln.strip()) for ln in reversed(tail)) if m), None)
    if last:
        word = last.group(1)
        where = f"{last.group(2)} line {last.group(3)}" if last.group(2) and last.group(3) else ""
        why = f"the run ended on the machine verdict {word}" + (f" at {where}" if where else "")
        return {"decision": "HARNESS-BUG", "why": why,
                "bug_title": f"a build ended {word} and no supervision rule explains it",
                "bug_evidence": "\n".join(tail[-8:])}
    return None


def evidence(fail, dispatch_rows, chunks):
    """What the model is shown. The trail lines are whole; the log tail declares its cut."""
    import dispatch_loop as DL
    chunk = next((c for c in chunks if c["chunk"] == fail["chunk"]), {})
    brief_path = BRIEFS / fail.get("brief", "")
    brief = brief_path.read_text(encoding="utf-8", errors="replace") if brief_path.is_file() else "(brief missing)"
    log_text = failed_log(fail, dispatch_rows)
    trail = "\n".join(l for l in log_text.splitlines() if re.match(r"\s*attempt \d+ …", l))
    tail = "\n".join(l for l in log_text.splitlines() if l.strip())
    if len(tail) > 6000:
        # The END of a log is where a job dies, so keep the end -- and say so.
        dropped = len(tail) - 6000
        tail = (f"[TRUNCATED -- job log: the first {dropped} of {len(tail)} characters are NOT "
                f"shown; the attempt trail above covers them]\n" + tail[dropped:])
    failed = DL.failures(fail["chunk"], fail.get("brief_sha256"), dispatch_rows)
    return (f"CHUNK {fail['chunk']}  model {fail['model']}  exit {fail.get('exit')}  "
            f"{fail.get('seconds')}s\nFILES {chunk.get('files')}\nTARGET SUITE {chunk.get('target')}\n"
            f"MODELS THAT HAVE FAILED THIS EXACT BRIEF: {failed} (max switches {DL.MAX_SWITCHES})\n\n"
            f"=== THE BRIEF ===\n{brief}\n\n=== ATTEMPT TRAIL ===\n{trail or '(none recorded)'}\n\n"
            f"=== END OF THE JOB LOG ===\n{tail}\n")


PROMPT = """You supervise a local coding model. One job just FAILED. Decide the single most useful
next step, from the evidence below only. Most failures here have been HARNESS defects -- the
tooling around the model -- not the model being unable to code. Look for that first.

Choose exactly one:
  SWITCH         the brief is sound and the failure looks model-specific; another model should try
  REWRITE-BRIEF  the brief is unclear, contradictory, or asks for something the files cannot give
  HARNESS-BUG    the tooling failed or misled the model (refusals, wrong context, cut-off output,
                 parse failures the model could not avoid) -- name the defect and its evidence
  PARK           nothing useful can be done without a person

Reply with ONE JSON object and nothing else:
{{"decision": "...", "why": "one or two sentences citing the evidence",
  "new_brief": "the full replacement brief (REWRITE-BRIEF only)",
  "bug_title": "short title (HARNESS-BUG only)", "bug_evidence": "the lines that show it (HARNESS-BUG only)"}}

{evidence}"""


def parse_decision(raw):
    """jsonx.one, never a greedy {.*}: two objects in a reply must be refused, not merged."""
    import jsonx
    r = jsonx.one(raw or "", ["decision"])
    d = r.get("obj") if r.get("status") == "ok" else None
    return d if d and d.get("decision") in DECISIONS else None


# THE JOB'S MODEL AND SERVER (2026-10-02): the scheduler may give supervision to the Senior, whose
# server is station 2 -- this always asked the main server with the supervisor profile's model.
JOB_MODEL = None


def ask_q16(prompt):
    sys.path.insert(0, str(REPO / "projects" / "local-inference"))
    import settings
    prof = settings.load()["supervisor"]
    model = JOB_MODEL or prof["model"]
    body = settings.request("supervisor", model, [{"role": "user", "content": prompt}])
    timeout = settings.timeout_for(prof.get("num_predict") or 6000, model=model)
    # ANY ENGINE (docs/ENGINES.md): the job's server (HIVEMIND_OLLAMA) or wherever `model` is served.
    import infer
    d = infer.chat(body, timeout=timeout)
    if d.get("done_reason") == "length":   # t238: a cut-off reply is said, not misread
        print(f"  NOTE: reply hit its num_predict limit (done_reason=length) -- it is cut off",
              file=sys.stderr)
    return (d.get("message") or {}).get("content") or "", prof["model"]


def execute(fail, decision, store=None):
    """Carry out one decision through artifacts only. Returns a short description."""
    d = decision["decision"]
    if d == "SWITCH":
        return "recorded; dispatch_loop moves the brief to a model that has not failed it"
    if d == "SERVER-DOWN":
        return "recorded; not the brief's fault -- it runs again once the model server answers"
    if d == "STILL-RED":
        return "parked as STILL RED; dispatch_loop sends the brief back to the role that owns Scoping"
    if d == "DUPLICATE-RUN":
        return "recorded; the first copy is the real run -- nothing to fix in the brief"
    if d == "PARK":
        return "parked; dispatch_loop honours this until the brief is edited"
    if d == "REWRITE-BRIEF":
        new = (decision.get("new_brief") or "").strip()
        bp = BRIEFS / fail["brief"]
        if not new:
            return "REFUSED: REWRITE-BRIEF with no new_brief"
        if bp.is_file() and new == bp.read_text(encoding="utf-8").strip():
            return "REFUSED: the rewrite is identical to the brief"
        archive = BRIEFS / "archive"
        archive.mkdir(parents=True, exist_ok=True)
        old = bp.read_text(encoding="utf-8") if bp.is_file() else ""
        (archive / f"{fail['brief']}.{hashlib.sha256(old.encode()).hexdigest()[:12]}").write_text(old)
        bp.write_text(new + "\n", encoding="utf-8")
        import task_routing
        return f"brief rewritten (old one archived); {task_routing.role_name('preflight')} reviews it before production"
    if d == "HARNESS-BUG":
        ts = store
        if ts is None:
            sys.path.insert(0, str(REPO / "projects" / "app-repo"))
            import taskstore as ts
        title = (decision.get("bug_title") or "unnamed harness defect").strip()
        # ONE OPEN TASK PER CHUNK (t564). The supervisor filed a fresh HARNESS task for every
        # failure it judged, so one brief failing three times became three tasks, each scoped
        # by the manager in turn. An open HARNESS task already naming this chunk gets the new
        # evidence as a note instead. Done tasks are not reopened: a new failure after a fix
        # is new information and gets its own task.
        tid = None
        for t_id, (meta, body) in ts.load_all().items():
            if (str(meta.get("title", "")).startswith("HARNESS:")
                    and meta.get("status") not in ("done", "cancelled")
                    and (f"from a failed dispatch: {fail['chunk']} on" in body
                         or str(meta.get("title", "")) == f"HARNESS: {title}")):   # one task per cause
                tid = t_id
                break
        if tid is None:
            tid = ts.add(f"HARNESS: {title}", area="local-inference", source="supervisor",
                         at_top=True, kind="build")
            verb = "filed"
        else:
            verb = "noted on"
            try:
                ts.move(tid, "top")
            except Exception as e:                                  # noqa: BLE001 -- said, not swallowed
                print(f"  NOTE: could not move {tid} to the top ({type(e).__name__}: {e})", file=sys.stderr)
        import task_routing
        # Named from the role store when the note is written (a note records who acted then; 2026-10-13).
        ts.add_note(tid, f"Filed by {task_routing.role_name('supervision')}, supervising, from a failed dispatch: {fail['chunk']} on "
                         f"{fail['model']} at {fail['at']}.\n\nWHY: {decision.get('why', '')}\n\n"
                         f"EVIDENCE:\n{decision.get('bug_evidence', '')}", who="supervisor")
        return f"{verb} {tid}"
    return "no action"


def harness_bug_keys(rows=None):
    """Failure keys this supervisor judged to be the HARNESS's fault, not the task's.

    Exists so dispatch_loop.futile() can stop counting them (2026-09-16). futile() asks
    whether a TASK is getting anywhere and already excludes REFUSED_TO_START for exactly this
    reason -- its own docstring: a precondition failing before the model saw anything "is a
    fact about the HARNESS, not about this brief, and holding the task for it accuses the
    wrong thing."

    An exit 1 can be the same lie. On 2026-09-16 a bug in coder.py (UnboundLocalError on
    `_declared`, introduced in d6d9c88) crashed six dispatches at 4-5 seconds each, after the
    run had been counted as started and so recorded as exit 1 rather than as a refusal. All
    six were then PARKED telling their owner "the brief or its oracle is the thing to fix".
    Nothing was wrong with any of those briefs -- and THIS ledger already knew it, because
    Q16 had classified every one of them HARNESS-BUG and named the variable and the line.

    That is the shape the 2026-09-16 committee called feedback failure: the decision was
    computed, written down, and never read by the thing whose behaviour it should change.
    """
    rows = _jsonl(LEDGER) if rows is None else rows
    return {r.get("failure") for r in rows
            if r.get("decision") in ("HARNESS-BUG", "SERVER-DOWN", "DUPLICATE-RUN") and r.get("failure")}


def still_red(chunk, sha, rows=None):
    """True when rule 3 sent this exact brief back to Scoping (its target stayed red)."""
    rows = _jsonl(LEDGER) if rows is None else rows
    return any(r.get("decision") == "STILL-RED" and r.get("chunk") == chunk
               and r.get("brief_sha256") == sha for r in rows)


def is_parked(chunk, sha, rows=None):
    rows = _jsonl(LEDGER) if rows is None else rows
    return any(r.get("decision") in ("PARK", "STILL-RED", "ESCALATED") and r.get("chunk") == chunk
               and r.get("brief_sha256") == sha for r in rows)


def builder_role(model, d=None):
    """The role key that builds on `model` (its list has building), else None."""
    import role_store
    d = d or role_store.load()
    on_model = [k for k, r in d["roles"].items() if r.get("model") == model]
    # the role that lists building, else any role on that model (Q4 builds without its role listing it)
    return next((k for k in on_model if any(t.get("task") == "building" for t in d["roles"][k].get("tasks", []))),
                on_model[0] if on_model else None)


# THE CLASS DECIDES THE DESTINATION (A7, 2026-10-30). Jas's RCA found that EVERY failure escalated one rank up
# from its builder, so a broken harness and a model's bad answer travelled the same road and rework became a
# funnel. The failure schema already names the class; this says where each class belongs, and it is a TIER rather
# than a rank because a tier is what the Models tab records and what survives the roles being reordered.
#
#   harness-fault          -> technical  our own machinery is broken; a seat that can change the harness fixes it
#   worker-output-rejected -> rework     the answer was wrong; re-brief and try again where scoping lives
#   stale-record           -> rework     the record the build ran against was cut or old; it has to be refreshed
#   external               -> technical  nothing here will fix it by retrying
#   unclassified           -> None       no destination is invented; the caller keeps "one rank up"
CLASS_DESTINATION = {
    "harness-fault": "technical",
    "worker-output-rejected": "rework",
    "stale-record": "rework",
    "external": "technical",
    # A8 (2026-10-30): a patch that satisfies X while breaking a suite asserting NOT-X is IMPOSSIBLE, not failed.
    # It goes to Technical because a person has to decide which oracle is right, and it is never retried -- retrying
    # spends a build to regenerate a patch that cannot exist. oracle-overlap claims less (the collateral merely
    # mentions the symbol) and routes the same way; oracle_conflict.classify decides which of the two it is.
    "oracle-conflict": "technical",
    "oracle-overlap": "technical",
}


def class_of(log_text):
    """(class, cause_category) for a failure's evidence, from the schema's own vocabulary."""
    import failure_schema as fsch
    return fsch.classify(log_text or "")


def blame_of(cause_category):
    """Jas's three (worker-output-rejected / harness-fault / stale-record) plus external and unclassified."""
    import failure_schema as fsch
    return fsch.blame_of(cause_category)


def destination_for_class(blame, d=None):
    """The role a failure of this CLASS goes to, or None when the class names no destination.

    None is deliberate and load-bearing: an unclassified failure must fall back to the old one-rank-up rule
    rather than be routed somewhere invented. A classifier with nothing to cite is a guess.
    """
    import role_store
    tier = CLASS_DESTINATION.get(blame)
    if not tier:
        return None
    d = d or role_store.load()
    holders = role_store.tier_roles(tier, d)
    if "embedder" in holders:
        holders = [h for h in holders if h != "embedder"]      # it can neither build nor review
    return holders[0] if holders else None


_ATTEMPT_RE = re.compile(r"^\s*attempt (\d+) … (.*)$")
_TESTS_FAIL_RE = re.compile(r"^tests fail \(\d+: (.*), \d+s\)\s*$")
_COUNT_RE = re.compile(r"\b\d+ passed, \d+ failed\b")


def target_first(log_text, target):
    """(lead, log_text relabelled): the TARGET suite's own result first, everything else named COLLATERAL.

    B5 (2026-10-30, REWORK-INTERVIEWS-2026-10-29 finding 1). The note used to be a header and the last 60 lines of
    the log, and that tail is whichever suites failed LAST -- on t1432 it was 60 lines of test_coder_first_budget and
    test_coder_slice_room while the target, test_coder_output_budget_clock, had PASSED on that attempt. Swift then
    called a collateral suite "the target" and proposed undoing the fix. coder.py lists a still-red target FIRST in
    `tests fail (...)` (coder.py:3446), so a target missing from the last such line passed on that attempt."""
    name = Path(target).name if target else ""
    lines = (log_text or "").splitlines()
    last_n, last_what, failing = None, "", None
    for ln in lines:
        m = _ATTEMPT_RE.match(ln)
        if m:
            last_n, last_what = m.group(1), m.group(2).strip()
            t = _TESTS_FAIL_RE.match(last_what)
            failing = [s.strip() for s in t.group(1).split(",")] if t else None
    if not name:
        lead = "TARGET SUITE: none recorded for this chunk, so no suite below can be called the target."
    elif last_n is None:
        lead = f"TARGET SUITE {name}: NOT RUN -- the log has no attempt line."
    elif failing is None:
        lead = (f"TARGET SUITE {name}: NOT RUN on the last attempt ({last_n}), which ended '{last_what}' before the "
                f"suites ran.")
    elif name in failing:
        sect, inside = [], False
        for ln in lines:
            if ln.startswith(f"--- {name} output"):
                sect, inside = [], True
            elif ln.startswith(f"--- end {name} ---"):
                inside = False
            elif inside:
                sect.append(ln)
        counts = [ln.strip() for ln in sect if _COUNT_RE.search(ln)]
        lead = f"TARGET SUITE {name}: STILL FAILING on attempt {last_n}" + (f" -- {counts[-1]}." if counts else ".")
    else:
        lead = f"TARGET SUITE {name}: PASSED on attempt {last_n}."
    others = [s for s in (failing or []) if s != name]
    if others:
        lead += (f"\nCOLLATERAL: {len(others)} other suite(s) failed: {', '.join(others)}. They are NOT this "
                 f"task's target; their output below is labelled COLLATERAL.")
    out = [f"--- COLLATERAL {ln[4:]}" if ln.startswith("--- ") and not ln.startswith("--- end ")
           and name and not ln.startswith(f"--- {name} ") else ln for ln in lines]
    return lead, "\n".join(out)


def escalate_failure(fail, log_text, store=None, chart=None, target=None):
    """THE ORG CHART, NOT A SUPERVISOR (Jas, 2026-10-19: "Lets get rid of the supervisor. What would get
    assigned to that task can be escalated in the same way other things are as per the organization
    chart"; 2026-10-20: "from the rank number that built it to +1"). A failed build no rule explains
    goes ONE RANK UP from the role that built it (role_store.escalate_up). The task gets the job's evidence as a note and is assigned there; the brief
    stays parked (is_parked) until it changes. -> (role key, task id or '')."""
    import role_store
    cls, cause = class_of(log_text)
    blame = blame_of(cause)
    # THE CLASS, THEN THE BLAME (2026-10-30, A8). CLASS_DESTINATION carries class keys as well as blame keys, and
    # oracle-conflict/oracle-overlap are classes: their cause has no blame that fits ("the brief asked for two
    # incompatible things" is nobody's fault), so asking only for blame(them) answered "unclassified", the class was
    # discarded, and an impossible patch went one rank up and was retried. Ask the class first; fall back exactly as
    # before when it names nothing.
    by_class = destination_for_class(cls) or destination_for_class(blame)
    to = by_class or (chart or role_store.escalate_up)(builder_role(fail.get("model")))
    why = (f"the failure class ({cls} / {blame}) decides this" if by_class
           else f"the class ({cls} / {blame}) names no destination, so one rank above the builder")
    if store is None:
        sys.path.insert(0, str(REPO / "projects" / "app-repo"))
        import taskstore as store
    import dispatch_loop as DL
    tid = DL.task_of(fail.get("brief") or "") or ""
    if tid:
        lead, labelled = target_first(log_text, target)
        tail = "\n".join([ln for ln in labelled.splitlines() if ln.strip()][-60:])
        store.add_note(tid, f"{lead}\n\nBUILD FAILED -- escalated to {to} because {why}."
                            f" Chunk {fail['chunk']} on {fail['model']} at "
                            f"{fail['at']}, exit {fail.get('exit')}. The brief is parked until it changes.\n\n"
                            f"END OF THE JOB LOG (the last 60 lines; the whole log is the one this job's "
                            f"`started` row in dispatch-loop.jsonl names):\n"
                            f"```\n{tail}\n```", who="dispatch-loop")
        store.set_meta(tid, why=f"Escalated to {to}: a failed build no supervision rule explains",
                       who="dispatch-loop", needs=to)
    return to, tid


def decide_pending(history=None, store=None):
    """Every failed build waiting for a decision, decided by rule or escalated. No model, no role:
    the dispatcher calls this each tick. -> [(chunk, decision, action)]."""
    import dispatch_loop as DL
    import parallel_dispatch as pd
    history = DL.rows() if history is None else history
    out = []
    for fail in pending(history, _jsonl(LEDGER)):
        target = next((c.get("target") for c in pd.chunks() if c["chunk"] == fail["chunk"]), None)
        log_text = failed_log(fail, history)
        ruled = rule_decision(log_text, target)
        base = {"failure": key(fail), "chunk": fail["chunk"], "model": fail["model"],
                "brief": fail.get("brief"), "brief_sha256": fail.get("brief_sha256"),
                "supervisor": "rules", "reply": ""}
        if ruled:
            action = execute(fail, ruled, store=store)
            record({**base, "decision": ruled["decision"], "why": ruled["why"], "action": action})
        else:
            try:
                to, tid = escalate_failure(fail, log_text, store=store, target=target)
                ruled = {"decision": "ESCALATED", "why": f"no rule explains it; escalated to {to}"}
                action = f"escalated {tid or '(no task)'} to {to}"
            except Exception as e:                                  # noqa: BLE001 -- said, retried next tick
                print(f"  NOTE: could not escalate {fail['chunk']}: {type(e).__name__}: {e}", file=sys.stderr)
                continue
            record({**base, "decision": "ESCALATED", "why": ruled["why"], "action": action})
        import scope_quality
        scope_quality.record("supervise", ruled["decision"].lower(), chunk=fail.get("chunk", ""),
                             defect_class=DECISION_CLASS.get(ruled["decision"], "unclassified"),
                             evidence=ruled.get("why", ""), source="supervise", supervisor="rules",
                             action=action, ledger=LEDGER.parent / "scope-quality.jsonl",
                             defects=LEDGER.parent / "scope-defects")
        out.append((fail["chunk"], ruled["decision"], action))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--model", default=None, help="the model of the role the scheduler gave this job")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--failure", default=None,
                    help="decide ONE failure, by key -- how the scheduler runs supervision as priority work")
    ap.add_argument("--failures", action="store_true",
                    help="the catalogue: every production failure ever recorded, classified (rule 6)")
    a = ap.parse_args()
    if a.model:
        global JOB_MODEL
        JOB_MODEL = a.model
    if a.failures:
        return print_failures()
    import dispatch_loop as DL
    import parallel_dispatch as pd
    sup = _jsonl(LEDGER)
    if a.status:
        for r in sup[-20:]:
            print(f"  {r.get('at')}  {r.get('chunk'):20} {r.get('decision') or r.get('outcome'):14} "
                  f"{r.get('action', '')}")
        print(f"  {len(sup)} decision(s) recorded")
        return 0
    if a.dry_run:
        for fail in pending(DL.rows(), sup):
            print(f"    {fail['chunk']} on {fail['model']} at {fail['at']}")
        return 0
    for chunk, dec, action in decide_pending():
        print(f"    {chunk}: {dec}: {action}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
