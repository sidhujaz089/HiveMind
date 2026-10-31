#!/usr/bin/env python3
"""Ask Q16 something long-form, WITH the repository tools, and recover from a cut-off.

    python3 scripts/ask_q16.py --prompt brief.txt --out answer.md
    python3 scripts/ask_q16.py --prompt brief.txt --out answer.md --no-tools

WHY THIS EXISTS -- TWO DEFECTS IT REMOVES
------------------------------------------
**1. The model had tools and we kept not giving them to it.** Jas, 2026-09-09: *"I thought
Q16 had the shell for greps. it should have the same searching tools as you can Chat."* He was
right. `console.py` has implemented a full tool loop since before this file existed, handing
Q16 `read_file`, `search_repo`, `agent_status`, `dispatch_coder` and `escalate`. But that loop
lives in the console's *thread handler*, so every PROGRAMMATIC dispatch -- a plain `/api/chat`
call -- got no tools at all.

The cost was not theoretical. Q16's panel report opened with *"I was instructed to read eight
files in the repository and to run three scripts... I cannot do either"* and marked the whole
document `[Unverified.]`. That was recorded as the tenth instance of the project's central
pattern, blamed on the brief. The brief was wrong, but the deeper fault was this: the
capability existed one module away and the dispatch path did not reach it. The gate audit that
followed compensated by pasting 41 KB of evidence into the prompt, which works and does not
scale, and leaves the model unable to check anything the author did not think to include.

This reuses `console.TOOLS` and `console.RUN` rather than declaring a second copy. A second
copy is how the two surfaces drift, and `L-046` is what that costs.

**2. A cut-off answer was indistinguishable from a bad one.** `preflight_brief.py` asked for a
four-file review at `num_predict=4000`, spent the entire budget thinking, and returned
`done_reason='length'` with EMPTY content. It correctly refused to read that as approval --
but it had no way to finish the job, so the safe failure fired on every large brief and the
review step became unusable. That is `t238`'s shape: `ask()` never reads `done_reason`, so a
truncated answer is reported as garbage.

**A ceiling that cannot be raised is a ceiling that gets removed.** So this escalates: on
`done_reason == "length"` it retries with a larger budget, up to `--max-escalations`, and
records every step. It never silently returns a short answer as though it were complete -- if
it runs out of escalations it says so, and the caller must treat the result as incomplete.
"""
import argparse
import os
import json
import urllib.error
import sys
import re
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "projects" / "local-inference"))
import roles  # noqa: E402  -- the model behind a role comes from SERVING.json
import modellease                                            # noqa: E402

# HIVEMIND_LEASE: the second manager station holds its own lease (console.py, OLLAMA_CHAT).
LEASE = Path(os.environ.get("HIVEMIND_LEASE") or (REPO / "workspace" / "scoping-lease.json"))
# THE ROLE ASKING (2026-10-02). Tools are offered and refused by role (ROLE-CAPS); this was always
# "manager", so a job routed to another role ran with the Manager's tools whatever its model.
# THE DEFAULT IS LOOKED UP (t9537): whoever holds Scoping -- the work this script runs when no caller
# names a role -- never the literal "manager", so replacing that role does not break the queue.
def _default_role():
    try:
        import task_routing
        return task_routing.owner(task_routing.load(), "scoping") or "manager"
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: role store unreadable ({type(e).__name__}: {e}); asking as 'manager'",
              file=sys.stderr)
        return "manager"


ROLE = _default_role()
MODEL = roles.tag(ROLE, "qwen3.8:27b-mtp-bf16")


def lease_for(model, env=None, manager_model=None):
    """The lease guarding THIS model -- modellease.lease_for, shared with console.ask."""
    return modellease.lease_for(model, env=env, manager_model=manager_model)


# THE WINDOW EVERY OTHER JOB ASKS FOR. The scheduler's preflight, supervision and health probe
# all request the coder profile's num_ctx; Ollama treats a different num_ctx as a different
# load, so a run at the model's full 262144 reloaded under them and they under it (2026-09-23,
# ollama-loads.jsonl). A prompt that does not fit is the scope's problem (window-full), not a
# reason to thrash the shared model.
try:
    import settings as _settings
    JOB_CTX = _settings.load()["coder"]["num_ctx"]
except Exception as _e:                                             # noqa: BLE001
    print(f"  NOTE: settings unreadable ({type(_e).__name__}); ask_q16 uses the model's own "
          f"window, which may reload a shared model", file=sys.stderr)
    JOB_CTX = None
# THE TASK SESSION (t386). When --task is given, every round lands in
# workspace/task-sessions/<task>/ -- the model's messages and reasoning, every tool call with its
# FULL result, the answer -- and the run starts from the digest of the task's earlier sessions,
# whichever model wrote them. Jas, 2026-09-24: "if overnight a lot of Q4's work is bad and we
# switch to Q16, it doesn't start from scratch." None when no task was named.
SESSION = None


def _rec(kind, **fields):
    """Append one row to the task session. A record that cannot be written is SAID and never
    allowed to stop the run it describes."""
    if SESSION is None:
        return
    try:
        import task_sessions
        task_sessions.append(SESSION, kind, **fields)
    except Exception as e:                                          # noqa: BLE001
        print(f"  (session row not written: {type(e).__name__}: {e})", file=sys.stderr)


PRIOR_HEADER = """EARLIER SESSIONS ON THIS TASK (any model; newest last). They are a record of what was
tried and concluded -- NOT verified facts. Where one names a file, line or number, check it
before relying on it; where it was wrong, say so.

"""


# Every dispatch leaves a row here. Added 2026-09-11; see _ledger() for why.
LEDGER = REPO / "workspace" / "agent-runs.jsonl"


# CHARACTERS PER TOKEN for the context guard: conservative. The three requests Ollama refused
# (t616) were 183-198k characters against num_ctx 65536, i.e. the real ratio was ~2.8-3.0.
CHARS_PER_TOKEN = 2.5


def fit_context(messages, num_ctx, reply_tokens):
    """Elide the OLDEST tool results until the conversation fits num_ctx, in place. -> chars elided.

    t616: Ollama answered HTTP 500 "no user query found in messages" 24 times since 09-25. Every
    captured request (ask-q16-server-errors.jsonl) was one user message plus 20-28 tool results,
    183-198k characters -- over the 65,536-token window. Ollama drops the OLDEST messages to make a
    prompt fit, the oldest is the only user message, and Qwen's template then has no user query.
    So the run died at its biggest, most expensive point. The user message and the latest round
    are never touched; an elided result says so, and how to get it back."""
    if not num_ctx:
        return 0
    # A NEGATIVE BUDGET MEANS "UNTIL DONE" (num_predict -1; 2026-10-06, Swift on vLLM): the reply is
    # not capped, so reserve the most this ever reserves -- a third of the window -- not nothing.
    reply = num_ctx // 3 if (reply_tokens or 0) < 0 else min(reply_tokens or 0, num_ctx // 3)
    limit = int((num_ctx - reply) * CHARS_PER_TOKEN)
    total = sum(len(m.get("content") or "") for m in messages)
    last_asst = max((i for i, m in enumerate(messages) if m.get("role") == "assistant"), default=-1)
    elided = 0
    for i, m in enumerate(messages):
        if total <= limit:
            break
        if m.get("role") != "tool" or i > last_asst:
            continue
        n = len(m.get("content") or "")
        if n < 400:
            continue
        stub = (f"[ELIDED by the harness: {n} characters of this earlier tool result were removed so "
                f"the conversation fits the {num_ctx}-token context window. Call the tool again if "
                f"you still need it.]")
        m["content"] = stub
        total -= n - len(stub)
        elided += n - len(stub)
    return elided


def run_tool_rounds(messages, console, max_rounds, budget, log, holder=None,
                    final_instruction=None,
                    timeout=7200):
    """One tool-calling conversation. Returns (final_message, done_reason, rounds_used).

    Mirrors the loop in console.py's thread handler, including the argument-shape fix it
    records: Ollama's native API returns tool arguments as a DICT and the OpenAI-compatible
    route returns a JSON STRING. Calling json.loads() unconditionally made every tool run
    with empty arguments -- read_file with no path, search_repo matching everything -- and
    that read as the model being unable to find files that were plainly there.
    """
    for rnd in range(max_rounds):
        # THE LAST ROUND HAS NO TOOLS, AND SAYS SO.
        #
        # 2026-09-10: all eight sections of the repair plan came back
        # done_reason='tool-round-cap' -- 220 tool calls, thirty minutes on one section, and
        # not a word of the document written. Given tools and no instruction to stop, the
        # model searched forever. The cap reported that honestly instead of inventing, which
        # is right, but an honest report of nothing is still nothing.
        #
        # A budget the model cannot see is a budget it cannot respect, so the final round
        # removes the tools and tells it why. It then has to answer from what it has, which
        # is the outcome we wanted anyway -- research, then write.
        last = rnd == max_rounds - 1
        if last:
            # WHOEVER OWNS THE LEG OWNS ITS LAST WORD. This text was hardcoded to "write the
            # section", which is right for a document run and WRONG for a caller whose leg is
            # meant to end in a checkpoint. On 2026-09-11 explore_loop.py asked for a
            # checkpoint, this overrode it with "write the section", and the leg came back as
            # a document with every checkpoint field empty -- two harnesses giving the model
            # contradictory final instructions, and the model obeying the louder one.
            msg = final_instruction or (
                "STOP SEARCHING AND WRITE THE SECTION NOW. You have used your tool budget "
                f"({max_rounds} rounds) and no tools are available on this turn. Produce the "
                "section from what you already have. If something you wanted to check is "
                "still unverified, write the section anyway and mark that one claim "
                "[unverified] with what you would have needed -- an honest gap in a finished "
                "section is worth far more than a perfect section that does not exist.")
            messages = messages + [{"role": "user", "content": msg}]
            log("    tool budget spent -- final round, tools OFF, must answer")
        cut = fit_context(messages, JOB_CTX, budget)
        if cut:
            log(f"    context guard: elided {cut} chars of older tool results to fit num_ctx {JOB_CTX} (t616)")
            _rec("context-guard", round=rnd, elided_chars=cut)
        obj = console.ask(messages, tools=not last, budget=budget, holder=holder,
                          timeout=timeout, model_name=MODEL, num_ctx=JOB_CTX, role=ROLE)
        m = obj.get("message") or {}
        calls = (m.get("tool_calls") or []) if not last else []
        _rec("model", round=rnd, content=m.get("content") or "", thinking=m.get("thinking") or "",
             tool_calls=calls, done_reason=obj.get("done_reason"))
        if not calls:
            return m, obj.get("done_reason"), rnd
        messages.append({"role": "assistant", "content": m.get("content") or "",
                         "tool_calls": calls})
        for c in calls:
            fn = c.get("function", {})
            name = fn.get("name", "")
            raw = fn.get("arguments")
            args = raw if isinstance(raw, dict) else _loads(raw)
            try:
                runner = getattr(console, "run_tool", None)          # the role switchboard
                result = (runner(name, args, ROLE) if runner else
                          console.RUN.get(name, lambda a: f"ERROR: no such tool: {name}")(args))
            except Exception as e:                          # noqa: BLE001
                result = f"ERROR: {type(e).__name__}: {e}"
            log(f"    tool {name}({json.dumps(args)[:100]}) -> {str(result)[:100]}")
            _rec("tool", round=rnd, tool=name, args=args, result=str(result))
            messages.append({"role": "tool", "tool_call_id": c.get("id", ""),
                             "content": str(result)})
    # SAY IT HAPPENED. A loop that never converges is the failure this reports rather
    # than showing nothing, which is what console.py learned to do.
    return None, "tool-round-cap", max_rounds


def _loads(raw):
    try:
        return json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}


_ASSERT_LINE = re.compile(r"^\s*(?:assert(?![\w])|self\.assert\w+\()")


def count_assertions(text):
    """How many assertions a delivered test suite holds: lines that START with an `assert` statement or a
    `self.assertX(` call. A name containing 'assert', a comment, and a string are not assertions. Pure."""
    return sum(1 for line in (text or "").splitlines() if _ASSERT_LINE.match(line))


def derive_complete(text, min_assertions):
    """`complete` derived from the DELIVERABLE, not declared by the transport (t679, RCA 86c010e0c0a6: a 289-char
    zero-assertion prose reply to 'write a test suite' was recorded complete:true because the model said stop).
    min_assertions <= 0 is no requirement -- today's behaviour, and the default."""
    return True if min_assertions <= 0 else count_assertions(text) >= min_assertions


def ask(prompt, use_tools=True, budget=24000, max_escalations=2, max_rounds=12,
        final_instruction=None,
        verbose=True, holder=None, timeout=7200, min_assertions=0):
    """Ask, and keep asking with a bigger budget if the answer was cut off.

    Returns a dict with the text, the final done_reason, and the full escalation trail.
    `complete` is False when the answer was still truncated at the last attempt -- the
    caller MUST NOT treat that as an answer.
    """
    def log(msg):
        if verbose:
            print(msg, flush=True)

    import console                                          # noqa: E402
    trail = []
    text = ""
    for attempt in range(max_escalations + 1):
        messages = [{"role": "user", "content": prompt}]
        t0 = time.time()
        log(f"  attempt {attempt + 1}: budget={budget} tools={use_tools}")
        # A BEAT PER ATTEMPT (t273). An attempt can run 20 minutes; without this, a
        # supervisor cannot tell a long generation from a dead process, and asking the
        # process is what lies (L-105).
        try:
            import heartbeat
            heartbeat.beat("ask_q16", note=f"attempt {attempt + 1}, budget {budget}")
        except Exception as e:                                    # noqa: BLE001
            print(f"  NOTE: heartbeat not written ({e}) -- the run continues", file=sys.stderr)
        try:
            if use_tools:
                # KEYWORDS FOR BOTH TAILS. `timeout` used to ride in as the 7th positional;
                # inserting final_instruction ahead of it silently rebound them.
                m, why, rounds = run_tool_rounds(messages, console, max_rounds, budget,
                                                 log, holder, timeout=timeout,
                                                 final_instruction=final_instruction)
            else:
                obj = console.ask(messages, tools=False, budget=budget, holder=holder,
                                  timeout=timeout, model_name=MODEL, num_ctx=JOB_CTX, role=ROLE)
                m, why, rounds = obj.get("message") or {}, obj.get("done_reason"), 0
        except urllib.error.HTTPError as e:
            # A SERVER ERROR IS NOT A TIMEOUT (2026-10-01). This branch's OSError caught HTTPError
            # too, so Ollama's 500 "no user query found in messages" -- 22 of them since 09-25,
            # both RCAs that ran beside each other today -- was recorded as "TIMED OUT. Raise
            # --timeout", and nobody looked at the server's own message.
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")
            except OSError as e2:
                body = f"(body unreadable: {e2})"
            log(f"  SERVER ERROR {e.code} after {(time.time() - t0) / 60:.0f} min: {body}")
            # THE REQUEST'S SHAPE IS THE EVIDENCE: roles in order, each content's first 120 chars.
            from loudcap import cap
            try:
                shape = [{"role": x.get("role"), "len": len(str(x.get("content") or "")),
                          "head": cap(str(x.get("content") or ""), 120, "request shape: head only"), "tool_calls": bool(x.get("tool_calls"))}
                         for x in messages]
                with (REPO / "workspace" / "run-logs" / "ask-q16-server-errors.jsonl").open("a") as fh:
                    fh.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                         "code": e.code, "error": body, "messages": shape}) + "\n")
            except OSError as e3:
                log(f"  (request shape not recorded: {e3})")
            trail.append({"attempt": attempt + 1, "budget": budget, "done_reason": f"http-{e.code}",
                          "minutes": round((time.time() - t0) / 60, 1), "chars": 0,
                          "tool_rounds": 0, "error": body})
            return {"text": "", "done_reason": f"http-{e.code}", "complete": False, "trail": trail,
                    "warning": f"SERVER ERROR {e.code}: {body}"}
        except (TimeoutError, OSError) as e:
            # A SOCKET TIMEOUT MUST READ AS A FAILURE, NOT AS A CRASH. The first run of
            # this lost a 50-minute generation to a stack trace, and the meta file left
            # behind still described the PREVIOUS attempt -- so the record on disk said
            # something that had not happened. Report it in the same shape as every other
            # outcome so the caller can tell "no answer" from "a bad answer".
            log(f"  TIMED OUT after {(time.time() - t0) / 60:.0f} min: {e}")
            trail.append({"attempt": attempt + 1, "budget": budget,
                          "done_reason": "timeout", "minutes": round((time.time() - t0) / 60, 1),
                          "chars": 0, "tool_rounds": 0, "error": str(e)})
            return {"text": "", "done_reason": "timeout", "complete": False,
                    "trail": trail,
                    "warning": "TIMED OUT. No answer was produced. Raise --timeout."}
        mins = (time.time() - t0) / 60
        text = ((m or {}).get("content") or "").strip()
        trail.append({"attempt": attempt + 1, "budget": budget, "done_reason": why,
                      "minutes": round(mins, 1), "chars": len(text),
                      "tool_rounds": rounds})
        log(f"  -> done_reason={why} {mins:.0f} min {len(text)} chars "
            f"({rounds} tool round(s))")
        reasoning = ((m or {}).get("thinking") or "").strip()
        if why != "length":
            return {"text": text, "done_reason": why,
                    "complete": (why == "stop") and derive_complete(text, min_assertions),
                    "trail": trail, "thinking": reasoning}
        # CUT OFF: FINISH FROM ITS OWN REASONING FIRST, THINKING OFF (Jas, 2026-09-24). Seven of
        # nine manager answers on 2026-09-23 died with done_reason=length and 0 chars: the tool
        # results filled the window (prompt 60.7k of 65.5k), and doubling the budget cannot add
        # room the window lacks. The reasoning was the work -- coder.py has handed it back since
        # finish_truncated(); this did not, and threw it away. The finish call carries the task
        # and the reasoning and NOT the raw tool output, which is what filled the window.
        if reasoning:
            done = _finish(prompt, reasoning, holder, timeout, log)
            if done:
                trail.append({"attempt": attempt + 1, "done_reason": "finished-from-reasoning",
                              "chars": len(done), "reasoning_chars": len(reasoning)})
                return {"text": done, "done_reason": "finished-from-reasoning",
                        "complete": derive_complete(done, min_assertions),
                        "trail": trail, "thinking": reasoning,
                        "warning": "Written with thinking OFF from the model's own cut-off "
                                   "reasoning; its tool evidence was not re-read."}
        budget *= 2
        log(f"  CUT OFF. Escalating the budget to {budget} and asking again.")

    return {"text": text, "done_reason": "length", "complete": False, "trail": trail,
            "thinking": reasoning,
            "warning": "STILL TRUNCATED after every escalation. This is NOT an answer."}


FINISH_TAIL = """

YOU RAN OUT OF ROOM BEFORE ANSWERING. Your reply was cut off while you were still reasoning.
That reasoning is NOT lost -- it is below.

YOU HAVE NO TOOLS ON THIS TURN AND THERE ARE NO MORE TURNS. Nothing you plan to read, check,
run, dispatch or verify in a follow-up will ever happen: a reply that says what you will do
next is not an answer and is thrown away. Give the final answer to the task above NOW, in the
format the task asks for, from what you already established. If one part of the task truly
cannot be settled from what you have, name that single thing inside the answer and mark it
[unverified] with what you would have needed -- do not promise to go and get it.

Do not start over and do not look anything up again. Where your reasoning did not settle
something, write it anyway and mark that claim [unverified] with what you would have needed.
If the task asks you to declare a step or to END WITH A BLOCK (a ```chunks block, a verdict
line), that is part of the answer: include it -- an answer without the block the task names
is thrown away however good the prose is.

=== YOUR REASONING SO FAR ===
{reasoning}
=== END OF YOUR REASONING ==="""
FINISH_REASONING_CAP = 120_000          # chars; ~30k tokens, which fits beside the task


def _finish(prompt, reasoning, holder, timeout, log):
    """The answer, written with thinking OFF from the model's own reasoning. '' if it fails."""
    import console
    body = reasoning
    if len(reasoning) > FINISH_REASONING_CAP:          # said, not silent: the latest part is kept
        body = (f"[the first {len(reasoning) - FINISH_REASONING_CAP} characters of your reasoning "
                f"are not shown; this is the latest part]\n" + reasoning[-FINISH_REASONING_CAP:])
    log(f"  FINISHING from its own reasoning ({len(reasoning)} chars), thinking OFF")
    try:
        obj = console.ask([{"role": "user", "content": prompt + FINISH_TAIL.format(reasoning=body)}],
                          tools=False, budget=8000, level="off", holder=holder, timeout=timeout,
                          model_name=MODEL, num_ctx=JOB_CTX, role=ROLE)
    except (TimeoutError, OSError) as e:
        log(f"  finish call failed: {e}")
        return ""
    m = obj.get("message") or {}
    text = (m.get("content") or "").strip()
    log(f"  -> finish: done_reason={obj.get('done_reason')} {len(text)} chars")
    if obj.get("done_reason") != "stop":
        return ""
    if not is_deferral(text):
        return text
    # A PROMISE OF WORK IS NOT AN ANSWER (2026-09-30). Ten manager finishes came back as one
    # sentence -- "I'll do a few targeted reads ... then write the answer" -- and were held for
    # Jas. Ask once more, saying plainly that there are no tools and no further turns.
    log(f"  finish only promised more work ({text[:80]!r}); asking once more")
    try:
        obj = console.ask([{"role": "user", "content": prompt + FINISH_TAIL.format(reasoning=body)},
                           {"role": "assistant", "content": text},
                           {"role": "user", "content": DEFERRAL_REASK}],
                          tools=False, budget=8000, level="off", holder=holder, timeout=timeout,
                          model_name=MODEL, num_ctx=JOB_CTX, role=ROLE)
    except (TimeoutError, OSError) as e:
        log(f"  re-ask failed: {e}")
        return ""
    text = ((obj.get("message") or {}).get("content") or "").strip()
    log(f"  -> re-ask: done_reason={obj.get('done_reason')} {len(text)} chars")
    if obj.get("done_reason") != "stop" or is_deferral(text):
        return ""
    return text


DEFERRAL_REASK = (
    "That is not an answer: it says what you would do next. There are NO TOOLS on this turn and "
    "there will be NO MORE TURNS -- nothing you plan to read will ever be read. Write the final "
    "answer now, in the format the task asks for, from what you already established. Mark any "
    "claim you could not verify [unverified].")

_DEFER_RE = re.compile(r"^\s*(i'?ll|i will|i need to|let me|first,? i|i should|i'm going to)\b",
                       re.IGNORECASE)


def is_deferral(text):
    """True when a reply only announces work it will do, instead of being the answer.

    Short AND opening with a promise ("I'll", "Let me", "I need to"). Length matters: a real
    answer may open with a plan sentence and then deliver; the observed deferrals were all
    under 250 characters.
    """
    t = (text or "").strip()
    return len(t) < 600 and bool(_DEFER_RE.match(t))



def _ledger(purpose, r, minutes):
    """Every dispatch leaves a row. A run nobody can find is a run that cannot be audited.

    WHY THIS WAS MISSING AND WHAT IT COST. coder.py has written workspace/agent-runs.jsonl
    since it was built; ask_q16 wrote nothing. So on 2026-09-11 four planning dispatches --
    one of them a 42-minute timeout that produced zero bytes -- existed only as .log files in
    a directory error_scan.py does not read. Jas asked whether the scanner would catch the
    day's errors. For these it could not, because there was nothing to catch.

    THE OUTCOME IS THE done_reason, DELIBERATELY UNMAPPED, BUT NEVER ABSENT (t3578). error_scan.py
    decides health by ENUMERATING THE GOOD (`HEALTHY_STATUS`), so a reason nobody has invented yet
    surfaces on its own, and translating "timeout" into some tidy local vocabulary here would hide
    exactly the outcomes that matter. What it must not do is write NOTHING: done_reason is None
    whenever the model's final reply carries none, and two rows reached the ledger that way
    (2026-10-06T04:51:53Z, 2026-10-14T01:49:54Z), which is a row no reader can classify. A run that
    ends without naming a reason is recorded as "no-done-reason" -- declared in
    reviewq.TRANSPORT_OUTCOMES -- so every row here says how its run ended.
    """
    try:
        row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "task": purpose, "tool": "ask_q16", "model": MODEL,
               "outcome": r.get("done_reason") or "no-done-reason",
               "complete": r.get("complete"),
               "chars": len(r.get("text") or ""), "minutes": round(minutes, 1),
               "attempts": len(r.get("trail") or []),
               "tool_rounds": max((x.get("tool_rounds") or 0)
                                  for x in (r.get("trail") or [{}])),
               "warning": r.get("warning")}
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception as e:                                   # noqa: BLE001
        # A ledger failure must never take the answer with it. Say so and carry on.
        print(f"  (could not write the run ledger: {type(e).__name__}: {e})")

def unreachable(r):
    """True when the model server could not be reached at all (connection refused/reset).

    2026-09-25 20:01: Ollama went down with the machine and every queued manager run
    failed in under a second. The caller must retry the same task, not advance past it.
    """
    blob = " ".join(str(v) for k, v in r.items() if k != "text" and v)
    return not r.get("text") and any(m in blob for m in (
        "Connection refused", "Errno 111", "Connection reset", "Errno 104",
        "Remote end closed", "No route to host", "HTTP Error 500", "HTTP Error 502",
        "HTTP Error 503"))


def write_answer(out, r):
    """Write the answer to OUT -- but NEVER replace a real answer with an empty one.

    2026-09-25: a reboot mid-queue made 22 runs fail instantly, and each one wrote a
    zero-byte file over the answer already on disk; 13 good answers were only recovered
    because git had them. An empty result now goes beside the file as OUT.failed-<utc>,
    and OUT, its .meta.json and .thinking.md are left exactly as they were.
    Returns the path actually written.
    """
    o = Path(out)
    # No text and no prior answer: still no answer FILE -- a zero-byte .md blocks every commit
    # (the pre-commit hook refuses empty markdown; t311, 2026-09-27). The run is recorded in the
    # .failed-<utc> file instead, and manager_note reads a missing answer as "no answer, retry".
    if not r.get("text") and (not o.exists() or o.stat().st_size == 0):
        o.unlink(missing_ok=True)
    if not r.get("text"):
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        side = Path(f"{out}.failed-{stamp}")
        side.write_text(json.dumps({k: v for k, v in r.items() if k != "text"}, indent=2,
                                   default=str))
        if r.get("thinking"):
            Path(f"{side}.thinking.md").write_text(r["thinking"])
        print(f"  this run produced nothing; recorded in {side.name}"
              + (" -- the earlier answer is kept" if o.exists() else ""))
        return str(side)
    o.write_text(r["text"])
    # THE REASONING IS KEPT, always. On 2026-09-23 seven cut-off runs' reasoning was discarded,
    # so nothing could be recovered from hours of model time.
    if r.get("thinking"):
        Path(out + ".thinking.md").write_text(r["thinking"])
    Path(out + ".meta.json").write_text(json.dumps(
        {k: v for k, v in r.items() if k != "text"}, indent=2, default=str))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--prompt", help="file holding the prompt")
    ap.add_argument("--out", help="where to write the answer")
    ap.add_argument("--budget", type=int, default=24000)
    ap.add_argument("--min-assertions", type=int, default=0,
                    help="a delivered test suite with fewer assertions than this is complete=False (t679)")
    ap.add_argument("--max-escalations", type=int, default=2)
    ap.add_argument("--max-rounds", type=int, default=12)
    ap.add_argument("--timeout", type=int, default=7200,
                    help="seconds for ONE call; a long document needs more than\nthe console default of 1800")
    ap.add_argument("--no-tools", action="store_true",
                    help="dispatch without the repository tools")
    ap.add_argument("--purpose", default="ask_q16")
    ap.add_argument("--task", help="task id; keeps a task session and resumes from earlier ones")
    ap.add_argument("--model", help="the model to ask (default: the Manager role's). task_worker.py "
                                    "passes the model of the role that owns the Flow task")
    ap.add_argument("--role", help="the role asking: its tools (ROLE-CAPS) apply "
                                   "(default: the role that holds Scoping)")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    global ROLE
    ROLE = a.role or ROLE
    if a.model:
        global MODEL, LEASE
        MODEL = a.model
        LEASE = lease_for(MODEL)
    # THE WINDOW OF THE MODEL BEING ASKED (2026-10-06). For an Ollama model settings.model_ctx is the
    # same capped number as before (one window per model, so nothing reloads); for an engine that
    # fixes its window at start (vLLM: Swift, 262144) it is that window, and the context guard stops
    # eliding tool results the model needs.
    global JOB_CTX
    if JOB_CTX is not None:
        JOB_CTX = _settings.model_ctx(MODEL)

    if a.self_test:
        return self_test()
    if not a.prompt or not a.out:
        ap.error("--prompt and --out are required unless --self-test")

    prompt = Path(a.prompt).read_text()
    if a.task:
        global SESSION
        try:
            import task_sessions
            prior = task_sessions.digest(a.task)
            if prior:
                prompt = PRIOR_HEADER + prior + "\n\n=== THE TASK NOW ===\n\n" + prompt
            SESSION = task_sessions.start(a.task, MODEL, a.purpose)
            _rec("prompt", text=prompt, prior_chars=len(prior))
            print(f"  task session: {SESSION}" + (f" (resuming from {len(prior)} chars of "
                                                  f"earlier sessions)" if prior else ""))
        except Exception as e:                                      # noqa: BLE001
            print(f"  NOTE: task session unavailable ({type(e).__name__}: {e}); running "
                  f"without one", file=sys.stderr)
    try:
        lease = modellease.acquire(LEASE, model=MODEL, purpose=a.purpose, ttl_seconds=10800)
    except modellease.Held as e:
        # YIELD, DO NOT FAIL (2026-09-26 05:14Z): a preflight held the lease and the manager
        # queue ran 181 tasks to rc=1 in a minute. Exit 4 = nothing was asked; try again later.
        _rec("answer", text="", done_reason="held", complete=False, warning=str(e))
        print(f"  MODEL LEASE HELD -- nothing was asked. Exit 4: retry later. ({e})")
        return 4
    _t0 = time.time()
    try:
        r = ask(prompt, use_tools=not a.no_tools, budget=a.budget,
                max_escalations=a.max_escalations, max_rounds=a.max_rounds,
                holder=lease.get("holder"), timeout=a.timeout, min_assertions=a.min_assertions)
    finally:
        modellease.release(LEASE, lease)
    # THE ROW GOES DOWN WHATEVER HAPPENED, including the timeout that returns no text.
    _ledger(a.purpose, r, (time.time() - _t0) / 60)

    _rec("answer", text=r["text"], done_reason=r.get("done_reason"), complete=r.get("complete"),
         warning=r.get("warning"))
    if r.get("thinking"):
        _rec("reasoning", text=r["thinking"])
    out = write_answer(a.out, r)
    print(f"\n  wrote {out}  ({len(r['text'])} chars, complete={r['complete']})")
    if unreachable(r):
        print("  MODEL SERVER UNREACHABLE -- nothing was asked. Exit 3: retry, do not advance.")
        return 3
    if not r["complete"]:
        print("  INCOMPLETE -- do not read this as an answer.")
        return 2
    return 0


def self_test():
    """Prove the escalation can report failure, not only success.

    A recovery path that has only ever succeeded cannot be told from one that cannot
    fail (docs/CHECKS.md rule 1). No model is called: a fake console drives every branch.
    """
    ok = True

    class FakeConsole:
        RUN = {"read_file": lambda a: f"contents of {a.get('path')}"}

        def __init__(self, script):
            self.script = list(script)
            self.budgets = []
            self.holders = []

        def ask(self, messages, tools=True, budget=None, level=None, holder=None,
                role=None, timeout=None, model_name=None, num_ctx=None):
            self.budgets.append(budget)
            self.holders.append(holder)
            return self.script.pop(0)

    import types
    def patched(script):
        fake = FakeConsole(script)
        sys.modules["console"] = types.SimpleNamespace(
            ask=fake.ask, RUN=fake.RUN, TOOLS=[])
        return fake

    print("A cut-off answer must escalate the budget and say so:")
    fake = patched([{"message": {"content": "short"}, "done_reason": "length"},
                    {"message": {"content": "the full answer"}, "done_reason": "stop"}])
    r = ask("q", budget=1000, max_escalations=2, verbose=False, holder="H1")
    print(f"  [{'ok ' if r['complete'] else 'BAD'}]  recovers on the second attempt: "
          f"complete={r['complete']}")
    ok &= r["complete"]
    print(f"  [{'ok ' if fake.budgets == [1000, 2000] else 'BAD'}]  the budget DOUBLED: "
          f"{fake.budgets}")
    ok &= fake.budgets == [1000, 2000]

    print(f"  [{'ok ' if fake.holders == ['H1', 'H1'] else 'BAD'}]  the HOLDER reached "
          f"console.ask on every attempt: {fake.holders}")
    ok &= fake.holders == ["H1", "H1"]

    print("\n...and when escalation runs out it must REFUSE, not return the stub:")
    patched([{"message": {"content": "s"}, "done_reason": "length"}] * 3)
    r = ask("q", budget=100, max_escalations=2, verbose=False)
    print(f"  [{'ok ' if not r['complete'] else 'BAD'}]  complete is False: {r['complete']}")
    ok &= not r["complete"]
    print(f"  [{'ok ' if 'warning' in r else 'BAD'}]  and it says so in words: "
          f"{r.get('warning', '')[:48]}")
    ok &= "warning" in r

    print("\nA tool call must run and come back as a tool message:")
    fake = patched([
        {"message": {"content": "", "tool_calls": [
            {"id": "1", "function": {"name": "read_file",
                                     "arguments": {"path": "STATE.md"}}}]}},
        {"message": {"content": "done"}, "done_reason": "stop"}])
    r = ask("q", budget=100, max_escalations=0, verbose=False)
    print(f"  [{'ok ' if r['complete'] else 'BAD'}]  the loop continues after a tool call: "
          f"complete={r['complete']}")
    ok &= r["complete"]

    print("\nArguments arriving as a JSON STRING must parse (the console's own bug):")
    got = {}
    class Rec:
        RUN = {"read_file": lambda a: got.update(a) or "x"}
        def ask(self, messages, tools=True, budget=None, level=None, holder=None,
                role=None, timeout=None, model_name=None, num_ctx=None):
            if len(messages) == 1:
                return {"message": {"content": "", "tool_calls": [
                    {"id": "1", "function": {"name": "read_file",
                                             "arguments": '{"path": "AGENTS.md"}'}}]}}
            return {"message": {"content": "done"}, "done_reason": "stop"}
    rec = Rec()
    sys.modules["console"] = types.SimpleNamespace(ask=rec.ask, RUN=Rec.RUN, TOOLS=[])
    ask("q", budget=100, max_escalations=0, verbose=False)
    print(f"  [{'ok ' if got.get('path') == 'AGENTS.md' else 'BAD'}]  string arguments "
          f"reached the tool: {got}")
    ok &= got.get("path") == "AGENTS.md"

    print("\nA SOCKET TIMEOUT must read as a failure, not a crash:")
    class Slow:
        RUN = {}
        def ask(self, messages, tools=True, budget=None, level=None, holder=None,
                role=None, timeout=None, model_name=None, num_ctx=None):
            raise TimeoutError("timed out")
    slow = Slow()
    sys.modules["console"] = types.SimpleNamespace(ask=slow.ask, RUN=Slow.RUN, TOOLS=[])
    r = ask("q", budget=100, max_escalations=0, verbose=False)
    print(f"  [{'ok ' if r['done_reason'] == 'timeout' else 'BAD'}]  done_reason={r['done_reason']}")
    ok &= r["done_reason"] == "timeout"
    print(f"  [{'ok ' if not r['complete'] else 'BAD'}]  complete is False: {r['complete']}")
    ok &= not r["complete"]

    print("\nA model that would search forever must be MADE to write:")
    class Spin:
        RUN = {"read_file": lambda a: "x"}
        def __init__(self):
            self.toolflags = []
            self.seen = []
        def ask(self, messages, tools=True, budget=None, level=None, holder=None,
                role=None, timeout=None, model_name=None, num_ctx=None):
            self.toolflags.append(tools)
            self.seen = list(messages)
            if not tools:                      # forced round: it must answer
                return {"message": {"content": "written under duress"},
                        "done_reason": "stop"}
            return {"message": {"content": "", "tool_calls": [
                {"id": "1", "function": {"name": "read_file", "arguments": {}}}]}}
    spin = Spin()
    sys.modules["console"] = types.SimpleNamespace(ask=spin.ask, RUN=Spin.RUN, TOOLS=[])
    r = ask("q", budget=100, max_escalations=0, max_rounds=3, verbose=False)
    print(f"  [{'ok ' if r['done_reason'] == 'stop' else 'BAD'}]  a model that would search "
          f"forever is FORCED to answer: done_reason={r['done_reason']}")
    ok &= r["done_reason"] == "stop"
    print(f"  [{'ok ' if spin.toolflags[-1] is False else 'BAD'}]  tools were OFF on the last "
          f"round: {spin.toolflags}")
    ok &= spin.toolflags[-1] is False
    print(f"  [{'ok ' if any('STOP SEARCHING' in str(m.get('content','')) for m in spin.seen) else 'BAD'}]"
          f"  ...and it was TOLD why")
    ok &= any("STOP SEARCHING" in str(m.get("content", "")) for m in spin.seen)

    print("\n" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
