#!/usr/bin/env python3
"""The scheduler: when a model is free and enrolled work is waiting, start it.

    python3 scripts/dispatch_loop.py --dry-run   # what a tick would do, and why
    python3 scripts/dispatch_loop.py             # one tick: start what should start, exit
    python3 scripts/dispatch_loop.py --status    # the ledger: running, parked, recent

Jas, 2026-09-14: "Can you write the scheduler path then, so it can dispatch jobs
automatically?" and, earlier the same day: "If any of our models are idle right now,
please dispatch more work to them."

WHAT EXISTED, AND WHY IT WAS NOT ENOUGH. parallel_dispatch.py assigns red chunks to models
correctly, but it runs ONCE and waits; when a lane finishes nothing starts the next one
(the handover said so: "zero loop constructs"). And it never looked at what was ALREADY
running -- which is how, on 2026-09-14, relaunching it after two lanes refused put a second
identical bf16 dispatch beside a live one for three hours.

ONE TICK, THEN EXIT. Meant to be run by a timer every ~10 minutes, not to sit in a loop. A
process that loops forever is a process whose death nobody notices; a tick that runs and
exits leaves a row in the ledger every time, so a missing row IS the alarm.

THE RULES, each one a way this goes wrong:

  1. NOTHING IS STARTED TWICE. A chunk already running on ANY model is skipped, and coder.py
     itself refuses an identical job (running_twin), so even a second scheduler cannot
     double a lane.
  2. ONE LANE PER MODEL. A model with a lane running is not free. Two lanes on one model
     each run at about half speed -- measured: bf16 decoded 8-11 tok/s alone and 2.65 with
     a twin beside it.
  3. ONLY RED TARGETS. A green target means the work is on main; dispatching it spends an
     hour to prove nothing.
  4. REFUSE IF THE INFERENCE PATH IS DOWN (t283), and write the refusal to the ledger.
  5. PARK A BRIEF THAT KEEPS FAILING. After PARK_AFTER consecutive unaccepted runs of the
     SAME brief content, stop dispatching it. This is what stops a scheduler turning one bad
     brief into a day of GPU: p05-errorscan failed seven attempts twice on 2026-09-14 and
     would otherwise be restarted every ten minutes forever. Parking keys on the brief's
     sha256, so EDITING THE BRIEF unparks it -- the fix and the release are the same act.
  6. EVERY JOB READS ITS BRIEF FROM A FILE (--brief-file). A path handed to --brief became
     the model's whole task and cost six GPU-hours (t263).

THIS NEVER MERGES. Delivered branches land in the review queue; Claude Code is the gate
(D-34) and merge_gate.py / merge_accounting.py give the mechanical verdicts.
"""
import argparse
import hashlib
import calendar
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import roles  # noqa: E402  -- the model behind a role comes from SERVING.json
LEDGER = REPO / "workspace" / "dispatch-loop.jsonl"
MAIN_PORT = 11434        # the system Ollama; the dispatcher's priority and production work runs here


def senior_station(serving=None):
    """The senior role's own server, if one is configured and answering: {tag, endpoint, port, lease},
    else None -- and then preflights run on the main server exactly as before.

    Jas, 2026-09-28: "I just wanted to get scoped work to Gemma, so can we dedicate this to
    preflight? Lets call it the Senior builder and it will be a place holder until we decide the
    actual Sr model." The senior role in SERVING.json names the model (Q4 today), the endpoint
    (ollama-station2, :11435) and the lease; this reads them at every tick, so a swap is one edit."""
    import urllib.request
    try:
        d = json.loads(Path(serving or (REPO / "projects" / "local-inference" / "models" /
                                        "SERVING.json")).read_text(encoding="utf-8"))
        r = d.get("roles", {}).get("senior") or {}
    except (OSError, ValueError) as e:
        print(f"  NOTE: SERVING.json unreadable, no senior station: {e}")
        return None
    ep = str(r.get("endpoint") or "").rstrip("/")
    if not (r.get("tag") and ep):
        return None
    try:
        with urllib.request.urlopen(f"{ep}/api/version", timeout=3) as f:
            f.read(200)
    except OSError as e:
        print(f"  NOTE: senior station {ep} not answering ({e}); preflights run on the main server")
        return None
    m = re.search(r":(\d+)$", ep)
    return {"tag": r["tag"], "endpoint": ep, "port": int(m.group(1)) if m else None,
            "lease": str(REPO / (r.get("lease") or "workspace/scoping-lease-station2.json"))}
JOBLOGS = REPO / "workspace" / "run-logs" / "dispatch"
CODER = REPO / "projects" / "local-inference" / "coder.py"

def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def terminal_row(started, *, exit_code, seconds, result_path, at=None):
    """Build the row a finished dispatch owes its `started` row. Builds a dict only: no write, no read.

    The dispatch of RCA 4027ae420d25 left a started row and nothing saying how it ended, with no
    pointer to the log worth reading. result_path is carried WHOLE -- docs/skills/never-truncate-a-record.
    """
    return {
        "event": "finished",
        "kind": started.get("kind", "production"),   # run_job defaults it the same way
        "chunk": started.get("chunk"),
        "model": started.get("model"),
        "brief": started.get("brief"),
        "brief_sha256": started.get("brief_sha256"),
        "key": started.get("key"),
        "pid": started.get("pid"),
        "exit": exit_code,
        # A yield is a stand-down, not a failure: failures()/futile()/parked() skip it, so the row
        # has to read as one to them. accepted is a zero exit and nothing else -- not a delivery.
        "yielded": exit_code in YIELD_EXITS,
        "accepted": exit_code == 0,
        "seconds": seconds,
        "result_path": result_path,
        "at": at if at is not None else now(),
    }


def budget_holder(row):
    """The name a job's promise is booked under -- THE SAME at start and finish, which is the whole trick.

    Derived from the row both sides already have (kind, key-or-chunk, pid), so no second record can drift
    from the first. A pid alone is not enough: one pid can carry two keys, and a re-dispatched lane gets a
    new pid, which is the same reason open_dispatches() matches on the pair.
    """
    row = row or {}
    return f"{row.get('kind') or 'production'}:{row.get('key') or row.get('chunk')}:{row.get('pid')}"


def budget_book(row):
    """BOOK what this job is about to use. Never raises, never blocks a start (step 1 is observation)."""
    try:
        import admission_budget as _b
    except ImportError as e:
        print(f"  NOTE: budget not booked ({e}) -- the start is not affected", file=sys.stderr)
        return None
    kind = row.get("kind") or "production"
    # A BUILD spends HOST BYTES (its clone); everything else spends a SEQUENCE SLOT inside the engine's
    # preallocated pool. Measured: four concurrent inference requests moved MemAvailable by 0.46 GB total,
    # so an inference job's host reservation is left at 0.0 and its cost is the slot.
    host_gb = (build_charge_gib()[0] + ADMIT_MARGIN_GIB) if kind == "production" else 0.0
    slots = 0 if kind == "production" else 1
    return _b.reserve(budget_holder(row), kind, host_gb=host_gb, slots=slots, kv_tokens=0,
                      pid=row.get("pid"))


def budget_close(row, reason="finished"):
    """RELEASE the promise. Reconciled against the pid as well, so a killed job's promise still goes."""
    try:
        import admission_budget as _b
    except ImportError as e:
        print(f"  NOTE: budget not released ({e})", file=sys.stderr)
        return None
    return _b.release(budget_holder(row), reason)


def record_terminal(started, *, exit_code, seconds, result_path, at=None, record=None):
    """Append the ONE terminal row a finished dispatch owes, to the ledger record() appends to."""
    row = terminal_row(started, exit_code=exit_code, seconds=seconds,
                       result_path=result_path, at=at)
    # Resolved at CALL time: this module rebinds the name (see `_ledger_record = record`), so a
    # default bound at def time would capture the function that stood there before the rebind.
    recorder = record if record is not None else globals()["record"]
    recorder(row)
    budget_close(started, reason="finished")
    return row


def open_dispatches(history):
    """Started rows in `history` with no finished row under the SAME (key, pid).

    Matching on the pair, not the key: a re-dispatched lane gets a new pid, and one pid can carry
    two keys, so either half alone closes a dispatch it did not run. Pure over row dicts -- no
    ledger read, no pid probe, no clock, no LOST_AFTER_S: a dead pid is not evidence of an end,
    and a start younger than a timeout is not an answer to what the run produced.
    """
    closed = {(r.get("key"), r.get("pid")) for r in history if r.get("event") == "finished"}
    return [r for r in history
            if r.get("event") == "started" and (r.get("key"), r.get("pid")) not in closed]


def record(row):
    """Append-only. Never raises: a ledger failure must not stop a tick from reporting."""
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": now(), **row}, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"  (ledger not written: {e})", file=sys.stderr)


_ledger_record = record
# THE FULL QUEUE LIVES IN ONE SMALL FILE, THE LEDGER KEEPS COUNTS (t675, 2026-10-05). Every tick row carried
# the whole queued/skipped listing: 65.3 MB of the 67 MB ledger was 4,729 of them, ~14 KB each, re-committed
# to git every session -- and every reader wanted only the NEWEST. Readers take it from here
# (jsonl_cache.latest_tick); the ledger row says how many and of what kind. Rows written before 2026-10-05
# still carry their lists; readers fall back to them when this file is missing.
LATEST_TICK_FILE = REPO / "workspace" / "run-logs" / "dispatch-tick-latest.json"


def write_tick_row(*, running, priority, started, preempted, queued, skipped, idle,
                   record=None, latest_tick_path=None):
    """Write the full tick to its latest-tick file (atomically), then append the SLIM row to the ledger.
    Returns the full row. Never raises: a tick must still report if the file cannot be written."""
    full = {"event": "tick", "running": running, "priority": priority, "started": started,
            "preempted": preempted, "queued": queued, "skipped": skipped, "idle": idle}
    by_kind = {}
    for q in queued:
        k = q.get("kind", "?") if isinstance(q, dict) else "?"
        by_kind[k] = by_kind.get(k, 0) + 1
    slim = {k: v for k, v in full.items() if k not in ("queued", "skipped")}
    slim.update(queued_n=len(queued), skipped_n=len(skipped), queued_by_kind=by_kind)
    path = Path(latest_tick_path or LATEST_TICK_FILE)
    try:
        # A STATE DIRECTORY IS CREATED BY BOOT OR A MIGRATION, NEVER BY A WRITER (mf4): a
        # writer that mkdirs its parent resurrects a renamed layout mid-migration. Missing
        # means report, not create.
        if not path.parent.is_dir():
            raise OSError(f"{path.parent} is missing -- a state directory is created by "
                          f"boot or a migration, never by a writer (mf4)")
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"at": now(), **full}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        print(f"  (latest tick not written: {e})", file=sys.stderr)
    (record or _ledger_record)(slim)
    return full


def rows():
    if not LEDGER.exists():
        return []
    # Parsed once per process, then only the appended lines (jsonl_cache, 2026-10-03): the Hub calls
    # this on every Production-board load and the ledger is 67 MB.
    import jsonl_cache
    out, torn = jsonl_cache.rows(LEDGER, torn=True)
    if torn:
        print(f"  NOTE: {torn} unreadable line(s) in {LEDGER} -- not counted", file=sys.stderr)
    return out


def brief_sha(brief_name):
    p = REPO / "workspace" / "briefs" / brief_name
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


def brief_identity(brief_name):
    """Hash the ask, not the administrative bytes around it.

    Keying a control on brief_sha lets an incidental header line (TASK:, BRIEF-ID:)
    release the control. Drop a leading run of such header lines, normalise the
    remaining text, and hash that projection.
    """
    p = REPO / "workspace" / "briefs" / brief_name
    try:
        raw = p.read_bytes()
    except OSError:
        return None

    text = raw.decode("utf-8", errors="replace")
    lines = text.split("\n")

    start = 0
    while start < len(lines):
        stripped = lines[start].strip()
        first_token = stripped.split(maxsplit=1)[0] if stripped else ""
        if first_token.startswith("TASK:") or first_token.startswith("BRIEF-ID"):
            start += 1
        else:
            break

    kept = []
    for line in lines[start:]:
        normalised = re.sub(r"[ \t]+", " ", line.strip())
        if normalised:
            kept.append(normalised)

    return hashlib.sha256("\n".join(kept).encode("utf-8")).hexdigest()


# PRODUCTION ORDER. Jas, 2026-09-14: "Gemma is the stronger coder but Q16 benchmarks higher in
# reasoning and intelligence." Gemma takes production work first; Q16 is the reviewer before it
# is a coder; Q4 fills in.
# Q4 REMOVED FROM THE LANES, Jas 2026-09-14: "Ok get rid of Q4." Three models do not fit in memory
# together (see FOOTPRINT_GIB): running Q4 as a third lane is what made ollama evict the models under
# running jobs, 15 times in two days. The weights stay on disk -- removed from scheduling, not deleted
# (Jas, t293: keep Qwen3.8 27B on disk).
def _serving(fn, fallback):
    """Read a model list from SERVING.json, falling back to the literal it replaced.

    FOUR PLACES NAMED MODELS AND NOTHING MADE THEM AGREE: REGISTRY.json, this list, ROLES
    below, and memory.py's EMBED_MODEL. Swapping a model meant finding all four, and the
    2026-09-15 precedent is on record -- an instruction applied to one site out of three,
    unnoticed for a day (see test_script_judges_agree.py). SERVING.json is the one source.
    The fallback is deliberate and is NOT belt-and-braces: this module is imported by the
    10-minute cron, and a scheduler that stops dispatching because a JSON file moved is a
    worse failure than one running a slightly stale list. It prints when it falls back, so
    the staleness cannot be silent.
    """
    try:
        import models
        got = fn(models)
        if got:
            return got
        print(f"  NOTE: SERVING.json yielded nothing for {fn.__name__}; using the built-in "
              f"list {fallback}. Check projects/local-inference/models/SERVING.json.",
              file=sys.stderr)
    except Exception as e:                                          # noqa: BLE001
        print(f"  NOTE: SERVING.json unreadable ({type(e).__name__}: {e}); using the "
              f"built-in list {fallback}.", file=sys.stderr)
    return fallback


def _production_models():
    """The Building roles' models. NO ROLE WITH BUILDING MEANS NO BUILDS (2026-10-03): Jas took
    Building off Gemma, the list came back empty, and _serving() treated empty as "file broken" and
    fell back to gemma4:26b + bf16 -- builds would have gone straight back to the model he had just
    taken them from. Only a store that cannot be READ falls back now."""
    try:
        import role_store
        store = role_store.load()
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: role store unreadable ({type(e).__name__}: {e}); production lanes from SERVING.json",
              file=sys.stderr)
        return _serving(lambda m: m.production_tags(), ["gemma4:26b", "qwen3.8:27b-mtp-bf16"])
    return list(dict.fromkeys(store["roles"][r]["model"] for r in role_store.building_roles(store)))


PRODUCTION_MODELS = _production_models()
REVIEWER = roles.for_job("preflight", "qwen3.8:27b-mtp-bf16")
OBJECTIONS = REPO / "workspace" / "preflight-objections.jsonl"
# MAX_SWITCHES. A job that fails on one model moves to one that has not failed it; after this many
# switches it parks instead of touring every model forever (Jas: "a max switch setting that
# prevents a job from switching forever"). 1 = two models tried before parking.
MAX_SWITCHES = 1
# Target suites checked at once per tick: separate CPU processes on a 20-core box whose models use
# the GPU and one core each (measured 2026-10-01: load average 4-6 under full production).
CPU_JOBS = 8


def _alive(pid):
    try:
        stat = Path(f"/proc/{int(pid)}/stat").read_text().rsplit(") ", 1)[1].split()[0]
        return stat != "Z"
    except (OSError, ValueError, IndexError, TypeError):
        return False


def ledger_running(history=None):
    """Jobs this scheduler started that are still alive -- preflights included.

    lane_status reports every kind of lane now, but main() filters it to production -- see the
    comment there -- so a Q16 preflight review would still read as a FREE Q16 and the next
    tick would hand it production work on top. The ledger knows what it started.
    """
    history = rows() if history is None else history
    done = {r.get("pid") for r in history if r.get("event") == "finished"}
    return [{"model": r["model"], "brief": r.get("brief"), "kind": r.get("kind", "production"),
             "key": r.get("key"), "pid": r.get("pid"), "server_port": r.get("server_port", MAIN_PORT),
             "role": r.get("role")}
            for r in history if r.get("event") == "started" and r.get("pid") not in done
            and _alive(r.get("pid"))]


# THE ONE-TIME CUTOFF, in preflight_brief's own timestamp format (%Y-%m-%dT%H%M%SZ). Every
# verdict before this was produced by a reviewer that was never shown the modules its target
# suite imported, and by a question 3 that called ANY outside dependency impossible -- so all
# 28 of them are answers to a question this repository no longer asks. Rows after it carry
# prompt_sha and expire themselves; this constant exists only because the rows before it
# cannot. It should never need moving again: change the question instead.
# MEASURED, NOT GUESSED (2026-09-16). Every preflight run log up to 02:50Z prints
# "target imports: showing 1"; every one from 03:10Z prints "showing 5", because target_deps
# began resolving the oracle's OWN imports rather than only the target suite's. Twelve
# verdicts were recorded in that window by a reviewer which could not see the file it then
# named as missing, and they held twelve chunks all day. The cutoff sits in the gap between
# the last 1-dep run and the first 5-dep one.
REVIEWER_FIXED = "2026-09-16T030000Z"


def _harness_bug_keys():
    """Failure keys the supervisor blamed on the harness. Says so LOUDLY when it cannot tell.

    Both callers -- futile() and failures() -- wrapped this in a bare `except: set()`, which
    is the exact defect this repository hunts: an empty set means "no failure was the
    harness's fault", so an unreadable supervise.py silently RESTORES the behaviour the
    exclusion exists to prevent, and nothing says a word. Chat's RCA of 2026-09-16 caught it
    on read, and it was mine, written twice in one afternoon.

    Failing toward counting is still the right direction -- a scheduler that stops because a
    ledger will not parse is an outage, and a false park is recoverable. What was wrong was
    doing it in silence. The alarm goes to the run log AND to the dispatch ledger, where
    error_scan.py is looking, because a warning printed into a cron log nobody opens is the
    same silence with extra steps.
    """
    try:
        import supervise
        return supervise.harness_bug_keys()
    except Exception as e:                                          # noqa: BLE001
        msg = (f"HARNESS-BUG EXCLUSION IS OFF: supervise.harness_bug_keys() raised "
               f"{type(e).__name__}: {e}. Every harness crash will now be counted against "
               f"the model that was running it, and briefs will be parked for faults that "
               f"are not theirs. This is NOT a quiet fallback.")
        print(f"  {msg}", flush=True)
        try:
            record({"event": "alarm", "what": "harness-bug-exclusion-unavailable",
                    "error": f"{type(e).__name__}: {e}"})
        except Exception as e:                                           # noqa: BLE001
            # the alarm must not become the outage
            print(f"  NOTE: alarm row not recorded ({e})", file=sys.stderr)
        return set()


def review_state(sha, objections=None, deps_now=None):
    """'cleared' | 'objected' | 'unreviewed' for a brief's CURRENT content.

    Keyed on sha256, so an edited brief is a new brief and gets reviewed again -- the same act
    that releases a park releases an objection.
    """
    if objections is None:
        objections = []
        if OBJECTIONS.exists():
            for line in OBJECTIONS.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    objections.append(json.loads(line))
                except ValueError:
                    print(f"  NOTE: unreadable line in {OBJECTIONS} -- not counted", file=sys.stderr)
                    continue
    mine = [o for o in objections if o.get("brief_sha256") == sha]
    # A VERDICT FROM A REVIEWER THAT NO LONGER EXISTS IS NOT BINDING. A refusal is evidence
    # about the brief AND about the question it was asked; change the question and the old
    # answer stops being a fact about the brief. On 2026-09-15 thirteen compliance chunks sat
    # held all day on verdicts that were CORRECT for a reviewer which was never shown the
    # oracle its target suite imported -- and once that was fixed they would have stayed held
    # for ever, because nothing expired a verdict.
    #
    # TWO CLAUSES, because they answer different questions and one cannot do both. A row that
    # DECLARES a different prompt_sha is stale -- that is the permanent mechanism, and every
    # future change to the question expires its own verdicts. Rows written before the field
    # existed cannot answer that, so they get a dated cutoff instead. Treating "no prompt_sha"
    # as stale was tried first and is wrong: it silently voids any row lacking the field,
    # including every test fixture, which is a one-time migration wearing a permanent rule's
    # clothes. An unstamped, undated row stays binding.
    try:
        import preflight_brief
        current = preflight_brief.PROMPT_SHA
    except Exception:                                               # noqa: BLE001
        current = None                  # cannot tell which reviewer asked -- keep every verdict

    def binding(o):
        ps = o.get("prompt_sha")
        if current and ps and ps != current:
            return False
        # THE EVIDENCE IS PART OF THE QUESTION. A verdict reached without seeing a file is
        # not a fact about the brief once that file is in the bundle. Compared only when BOTH
        # sides are known: a row written before deps_shown existed cannot answer this and
        # falls through to the dated cutoff, which is the same migration shape prompt_sha got
        # and for the same reason -- treating "field absent" as stale would silently void
        # every older row and every test fixture.
        if deps_now is not None and o.get("deps_shown") is not None:
            if sorted(o["deps_shown"]) != sorted(deps_now):
                return False
        at = str(o.get("at") or "")
        return not (at and at < REVIEWER_FIXED)

    mine = [o for o in mine if binding(o)]
    if not mine:
        return "unreviewed"
    return "objected" if mine[-1].get("stopped") else "cleared"


def failures(chunk, sha, history=None):
    """Models that FAILED this exact brief, in order, since its last acceptance.

    ONLY exit 1 -- A MODEL THAT TRIED AND FAILED -- AND THAT IS DELIBERATE. coder.py's other
    non-zero exits are not attempts: 2 is a refusal to start (the baseline is not what was
    declared, the target suite is absent, the inference path did not answer) and 3 is
    ACCEPTED-NOT-WRITTEN, where the judgement was fine and the delivery failed. Neither is the
    model's doing, and moving a broken baseline or a broken delivery to another model fails
    there too -- it only spends a second lane to learn the same thing.

    This was widened to "anything not accepted" on 2026-09-15 and put back the same hour.
    p32's nine exit-2 rows looked like nine uncounted failures, and the conclusion that they
    should have switched was wrong: not switching was right, and what was actually missing was
    that nobody was TOLD. That gap is supervise.pending()'s, and it is fixed there instead.

    Yields are not failures either: YIELD_EXITS is a job stood down for priority work.
    """
    history = rows() if history is None else history
    # AND NOT A FAILURE THE SUPERVISOR BLAMED ON THE HARNESS, by the same argument the
    # docstring above makes for exits 2 and 3 (2026-09-16). Those are excluded because they
    # are "not the model's doing", and moving them to another model "only spends a second
    # lane to learn the same thing". A harness crash that happens to land as exit 1 is that
    # case exactly: coder.py raised UnboundLocalError before the model was asked for
    # anything, and six chunks recorded gemma4:26b as having failed them. Gemma never saw
    # them. Left uncorrected it burns the switch budget -- MAX_SWITCHES is 1 -- on a model
    # that never got a turn, and the chunk then reads "no free model that has not already
    # failed it" while a free model sits there.
    _harness = _harness_bug_keys()
    failed = []
    for r in history:
        if r.get("event") != "finished" or r.get("kind", "production") != "production":
            continue
        if r.get("chunk") != chunk or r.get("brief_sha256") != sha:
            continue
        if r.get("accepted"):
            failed = []
        elif (r.get("exit") == 1 and r.get("model") not in failed
                and f"{r.get('at')}|{r.get('chunk')}|{r.get('model')}" not in _harness):
            failed.append(r["model"])
    return failed


# HOLD THE TASK, NEVER THE MODEL. Jas, 2026-09-16: "Cancel this bench routine. I realized
# production will not go because of upstream quality issues. Hold failed tasks but let the models
# run what they can."
#
# This began as a per-MODEL breaker and that was the wrong unit. If a chunk cannot be built
# because its brief or its oracle is wrong -- which is what 12 of the 13 recorded failures were
# -- then benching the model punishes the one part that is working and stops it doing the other
# chunks it could have done. The model was never the problem. On the night it was written the
# breaker benched gemma4:26b for three failures that were all ONE unfixed defect (t315, its
# 16000-token output limit), which would have stopped it building anything else.
#
# IT MEASURES FUTILITY, NOT BLAME, and that is why it does not reuse failures(). failures() asks
# whose fault a failure was, so it counts exit 1 only -- a refusal to start is nobody's, and must
# not move a brief to another model where the same precondition fails again. This asks whether
# this TASK is getting anywhere, and for that a refusal counts: p32 refused or misdelivered nine
# times across seven hours and nothing ever held it.
# ONE ATTEMPT. Jas, 2026-09-16: "we shouldn't have three attempts at a task before failure. It
# should just be one: note the reason, put it back at the beginning of production, and let it go
# through analysis again." Measurably right: coder.py is invoked IDENTICALLY on every attempt --
# the switch history goes to the ledger and is never shown to the model -- so attempts two and
# three cannot discover anything attempt one did not, at a second and third half-hour of GPU
# each. What would make a retry worth having is now written onto the task by
# note_failure_on_task(), which is what the next attempt should be started from.
FUTILE_AFTER = 1

# coder.py's exit for "a precondition failed before any attempt" -- an untrustworthy sandbox
# baseline, an absent target suite, an inference path that did not answer, a live twin job.
# Named because two different accounts have to agree about it: it is nobody's FAULT (failures())
# and it is not the TASK going nowhere (futile()). It is the harness refusing, and it gets its
# own alarm through refusal_storm().
REFUSED_TO_START = 2


LOST_AFTER_S = 120


def lost_jobs(history, now=None, alive=None):
    """Started rows whose process is gone and that have no finished row: jobs that ended unrecorded.

    RUN ACCOUNTING FOR THE SCHEDULER (t230, gate 1): every job it starts must reach exactly one
    terminal row. On 2026-10-02 two RCAs died with a service restart (07:56Z) and left only their
    'started' rows; nothing said they had ended, nothing counted them. A job is lost only when its
    pid is dead AND it started more than LOST_AFTER_S ago (run_job writes its own row on the way out)."""
    now = now or time.time()
    alive = alive or _alive
    done = {r.get("pid") for r in history if r.get("event") == "finished"}
    out = []
    for r in history:
        if r.get("event") != "started" or r.get("pid") in done or not r.get("pid"):
            continue
        try:
            import calendar
            age = now - calendar.timegm(time.strptime(r["at"], "%Y-%m-%dT%H:%M:%SZ"))
        except (KeyError, ValueError) as e:
            print(f"  NOTE: started row without a readable time ({e}); not accounted: {r.get('chunk')}")
            continue
        if age > LOST_AFTER_S and not alive(r.get("pid")):
            out.append(r)
    return out


def _outcome_elsewhere(started):
    """For a script review: the status of a review row written AFTER this job started, or None."""
    if started.get("kind") != "script-review":
        return None
    try:
        sys.path.insert(0, str(REPO / "scripts"))
        import truncation_review
        path = started.get("path") or started.get("chunk")
        rows_after = [x for x in truncation_review.rows()
                      if x.get("path") == path and str(x.get("at", "")) >= str(started.get("at", ""))
                      and (not started.get("brief_sha256") or x.get("sha256") == started.get("brief_sha256"))]
        return rows_after[0].get("status") if rows_after else None
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: review ledger unreadable ({type(e).__name__}: {e}); {started.get('chunk')} accounted lost")
        return None


def account_lost(history=None, write=True):
    """Write the one terminal row each lost job is owed: exit -9 (in YIELD_EXITS, so it never counts
    against the brief and the work is retried), lost=True, and say so. Returns the rows written."""
    history = rows() if history is None else history
    written = []
    for r in lost_jobs(history):
        done_elsewhere = _outcome_elsewhere(r)
        if done_elsewhere is not None:
            # ITS WORK FINISHED; ONLY ITS OWN ROW WAS LOST (2026-10-02: a script review of
            # run_tests.py wrote 'ok' to the review ledger and died before run_job's row, and
            # writing it "lost, not accepted" made the two ledgers disagree). Take the outcome
            # that was recorded.
            row = {"at": now(), "event": "finished", "kind": r.get("kind"), "chunk": r.get("chunk"),
                   "model": r.get("model"), "brief": r.get("brief"), "brief_sha256": r.get("brief_sha256"),
                   "key": r.get("key"), "yielded": False, "pid": r.get("pid"), "exit": 0,
                   "accepted": done_elsewhere == "ok", "lost": True,
                   "why": f"its own finished row was never written; its review row says {done_elsewhere!r}"}
            if write:
                with LEDGER.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row) + "\n")
            written.append(row)
            continue
        row = {"at": now(), "event": "finished", "kind": r.get("kind", "production"),
               "chunk": r.get("chunk"), "model": r.get("model"), "brief": r.get("brief"),
               "brief_sha256": r.get("brief_sha256"), "key": r.get("key"), "yielded": True,
               "pid": r.get("pid"), "exit": -9, "accepted": False, "lost": True,
               "why": f"process gone with no finished row (started {r.get('at')}); accounted by the tick"}
        if write:
            with LEDGER.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        written.append(row)
    return written


def refusal_storm(history=None, since=None, enrolled=None):
    """[(chunk, n, last_reason_model)] for briefs the harness keeps refusing to start.

    A refusal is deterministic: whatever precondition failed will fail again for the next model
    and the next brief, so this is almost always ONE harness fault seen from several chunks at
    once. On 2026-09-16 a single badly-written test suite -- red in every sandbox because it
    asserted live ledger state -- refused four different chunks inside one tick. Reported
    together they read as what they are; reported per task they read as four bad briefs.
    """
    history = rows() if history is None else history
    per = {}
    for r in history:
        if (r.get("event") != "finished" or r.get("kind", "production") != "production"
                or r.get("chunk") != r.get("chunk")):
            continue
        if r.get("accepted"):
            per.pop(r.get("chunk"), None)
        elif r.get("exit") == REFUSED_TO_START and (not since or str(r.get("at", "")) >= since):
            c = per.setdefault(r.get("chunk"), {"n": 0, "model": None, "at": None})
            c["n"] += 1
            c["model"], c["at"] = r.get("model"), r.get("at")
    # ONLY WORK STILL ENROLLED (2026-10-02). p47 and p186 were reported refused for days after
    # their chunks left the enrolment list: a refusal is cleared only by an accepted run, and a
    # chunk that is no longer enrolled never gets one.
    if enrolled is not None:
        per = {k: v for k, v in per.items() if k in enrolled}
    return sorted(((k, v["n"], v["model"], v["at"]) for k, v in per.items()),
                  key=lambda x: -x[1])


def needs_scope(chunk, sha, history):
    """How many exit-2 runs of this brief ended in coder.py's NEEDS-SCOPE refusal."""
    logs = {r.get("pid"): r.get("log") for r in history
            if r.get("event") == "started" and r.get("chunk") == chunk
            and r.get("brief_sha256") == sha and r.get("log")}
    n = 0
    for r in history:
        if (r.get("event") == "finished" and r.get("chunk") == chunk
                and r.get("brief_sha256") == sha and r.get("exit") == REFUSED_TO_START):
            log = logs.get(r.get("pid"))
            try:
                if log and "NEEDS-SCOPE" in (REPO / log).read_text(encoding="utf-8",
                                                                   errors="replace")[-4000:]:
                    n += 1
            except OSError as e:
                print(f"  NOTE: {log} unreadable: {e}")
    return n


HARNESS_PARK_AFTER = 1          # Jas, 2026-09-27: one harness-blamed failure is enough


AUTO_ASSIGNED = REPO / "workspace" / "run-logs" / "auto-assigned.json"
# WHO A PARKED OR HELD CHUNK IS WAITING ON (Jas, 2026-10-08: "If something is parked, it needs to be
# assigned to something other than production. If it's waiting on you it needs to get assigned to
# you."). Every one of these is Claude's today: an objection is read, a harness bug fixed, a brief
# that came to nothing rewritten, a delivered build reviewed. The reason prefix -> the role key.
# The one fixed part of the "waiting for <role> to review the brief" reason: the role is named from the
# store (task_routing.role_name), so code that recognises the reason matches this, never the sentence
# (2026-10-13: it matched "waiting for Q16 to review the brief" word for word).
REVIEW_WAIT_TAIL = "to review the brief"
# WHO is a CLASS on the org chart, not a name (Jas, 2026-10-19): role_store.escalate() reads the
# position from ROLES.json, so renaming or moving a role changes this routing with no code edit.
WAITING_ON = (("HELD FOR CLAUDE", "technical"), ("PARKED", "technical"), ("DELIVERED", "technical"))


def escalate_to(cls, from_role=None):
    """The role key `cls` escalates to (role_store.escalate). Raises when the chart cannot answer."""
    import role_store
    return role_store.escalate(cls, from_role)


def brief_defect(why):
    """True for a hold or park that the BRIEF caused, so the scoper must revise it (t1071): a preflight
    objection, a brief that came to nothing, a target too big for the window. A harness-bug park, a
    supervisor's park and a delivered build are not brief defects."""
    w = str(why)
    return (w.startswith("HELD FOR CLAUDE") and "objected to this brief" in w) \
        or bool(re.match(r"PARKED: \d+ attempt\(s\) in a row came to nothing", w)) \
        or w.startswith("PARKED: NEEDS-SCOPE") \
        or w.startswith("PARKED: STILL RED") \
        or w.startswith("PARKED: LOOP:")


ORACLE_NAMED_RE = re.compile(
    r"\boracle\b|\btest_[\w.]+\.py\b|the target suite", re.I)


def names_the_oracle(*text):
    """True when a hold's evidence NAMES the oracle (t9540 step 4 as revised by R2).

    Jas's known cause for the rework pile: "the scoper cannot edit the oracle". A preflight objection that
    names the oracle -- "the oracle cannot fail", "test_x.py passes already", "the target suite is red on
    main" -- is asking for the ORACLE to be revised, and the scoping stage is the only stage allowed to do
    that. The building job never may: auto_merge's conditions 2 and 5 rest on "the oracle was written before
    the build, never by it", and a builder that can edit its own proof has removed them.

    Deliberately a word match on the evidence rather than a new field: the objection ledger already carries
    the reviewer's words, and a classifier with nothing to cite is a guess.
    """
    return any(ORACLE_NAMED_RE.search(str(t or "")) for t in text)


def holders_of(value):
    """The roles that would work a task whose `needs:` is `value` (t9540).

    `needs:` holds a TIER now (technical / vision / rework / production), so comparing it to a role name --
    `now == prev["to"]` -- is false for every task, and the scheduler would treat its OWN assignments as
    somebody else's. This resolves a tier to its holders and lets a legacy role key name itself, so the same
    comparison works in both vocabularies.
    """
    try:
        import role_store
        return set(role_store.tier_roles(value))
    except Exception as e:                                        # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: could not resolve needs {value!r} to roles ({type(e).__name__}: {e})",
              file=sys.stderr)
        return {value} if value else set()


def route_waiting(skipped, path=None, store=None):
    """Route each task with a parked/held chunk, and hand it back to production once released -- only
    tasks THIS assigned, so a manual assignment is never overridden. One note each handoff.

    BRIEF DEFECTS GO TO THE SCOPER FIRST (t1071; Jas 2026-10-14: "the throughput of our building is
    pretty wasteful ... significantly more was reassigned to you"). On 10-14 146 tasks reached Claude
    as 40 objections, 38 one-attempt parks and 29 harness parks. An objection or a 'came to nothing'
    park is assigned to the role that owns Scoping (its Assigned items step revises the brief) with
    the reason verbatim; the SAME reason again after the scoper handed it back is the second failure
    and comes to Claude; a revised brief (a new reason) goes back to the scoper.
    A RED-ON-MAIN HOLD ASSIGNS NOTHING (Jas 2026-10-14): red_on_main.py files one owner task for the
    suite; ~700 reassignments to Claude in two days were this reason. Everything else waits on Claude.
    Returns lines for the tick log."""
    if store is None:
        sys.path.insert(0, str(REPO / "projects" / "app-repo"))
        import taskstore as store
    path = Path(path or AUTO_ASSIGNED)
    try:
        mine = json.loads(path.read_text())
    except (OSError, ValueError):
        mine = {}
    scoper = task_routing.owner(ROUTING, "scoping")
    now_s = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    waiting, out = {}, []
    for chunk, why in skipped:
        m = re.search(r"-(t\d+)-", f"-{chunk}-")
        w = str(why)
        if not m or w.startswith("HELD: red on main"):
            continue
        to = next((escalate_to(cls) for pre, cls in WAITING_ON if w.startswith(pre)), None)
        if to and w.startswith("DELIVERED"):
            import task_board
            if not task_board._branch_exists(REPO, w):
                to = None                       # merged and its branch deleted: not waiting on anyone
        if to:
            waiting.setdefault(m.group(1), (to, chunk, w))
    for tid, (to, chunk, why) in waiting.items():
        meta, _ = store.find(tid)
        if not meta or meta.get("status") == "done":
            continue
        prev = mine.get(tid)
        now = store.assignee(meta.get("needs"))
        held_by = holders_of(now) | ({now} if now == store.PRODUCTION else set())
        if now != store.PRODUCTION and (prev or {}).get("to") not in held_by:
            continue                            # someone else holds it (Jas, a person): leave it
        if brief_defect(why) and scoper:
            if prev and prev.get("to") == scoper and now == scoper:
                continue                        # the scoper is still revising it
            above = escalate_to("technical", scoper)
            if prev and prev.get("why") == why and prev.get("to") == above:
                continue                        # already escalated on this exact reason
            if prev and prev.get("why") == why:
                dest, text = above, (f"Assigned to {above} by the scheduler: the SECOND identical failure "
                                        f"after the scoper's revision -- chunk {chunk}: {why}. Worked with the "
                                        f"other second failures as a batch, with an RCA.")
            else:
                dest, text = scoper, (f"Assigned to {scoper} (Scoping) by the scheduler: the brief is the "
                                      f"defect -- revise the brief, then answer OUTCOME: done. Chunk {chunk}: "
                                      f"{why}. Evidence: workspace/preflight-objections.jsonl for an objection, "
                                      f"`python3 scripts/supervise.py --failures` for a park."
                                      + (f"\n\nTHE ORACLE IS NAMED IN THE EVIDENCE, so this revision MAY also "
                                         f"change the oracle -- Scoping is the only stage allowed to. State the "
                                         f"new oracle in the brief; the BUILDING job still may not edit it."
                                         if names_the_oracle(why) else ""))
        else:
            if prev:
                continue
            dest, text = to, (f"Assigned to {to} by the scheduler: chunk {chunk} is waiting on {to}. "
                              f"{why} -- It returns to production by itself once released.")
        store.set_meta(tid, needs=dest)
        store.add_note(tid, text, who="dispatch-loop")
        mine[tid] = {"to": dest, "chunk": chunk, "why": why, "at": now_s}
        out.append(f"ASSIGNED  {tid} -> {dest}: {chunk} {why}")
    for tid in [t for t in mine if t not in waiting]:
        meta, _ = store.find(tid)
        now = store.assignee(meta.get("needs")) if meta else ""
        if meta and mine[tid]["to"] in (holders_of(now) | {now}):
            store.set_meta(tid, needs="")
            store.add_note(tid, f"Released by the scheduler: chunk {mine[tid]['chunk']} is no longer parked "
                                f"or held, so this is back with production.", who="dispatch-loop")
            out.append(f"RELEASED  {tid} -> production")
        mine.pop(tid)
    path.write_text(json.dumps(mine, indent=1) + "\n")
    return out


HARNESS_FIXED = REPO / "workspace" / "run-logs" / "harness-fixed.json"


def harness_fixed_at(path=None):
    """ISO time of the last recorded harness fix, or "" -- failures before it no longer park a brief.
    Written by `dispatch_loop.py --harness-fixed "<commit>: why"` when a harness bug is fixed."""
    try:
        return str(json.loads(Path(path or HARNESS_FIXED).read_text()).get("at", ""))
    except (OSError, ValueError):
        return ""


def futile(chunk, sha, history=None):
    """(True, why) when this brief's last FUTILE_AFTER real ATTEMPTS came to nothing.

    A REFUSAL TO START IS NOT AN ATTEMPT, and counting it here was a mistake made and undone
    within the hour on 2026-09-16. coder.py's exit 2 means a precondition failed before the
    model saw anything -- most often a sandbox baseline with an undeclared red suite. That is a
    fact about the HARNESS, not about this brief, and holding the task for it accuses the wrong
    thing: within one tick p17, p18, p19 and p20 were all parked telling their owner "the brief
    or its oracle is the thing to fix" when the real cause was a test THIS session had written
    badly, red in every sandbox on the box. Four false accusations from one bad suite.

    So: exit 1 (tried, suite stayed red) and exit 3 (accepted, nothing delivered) count, because
    in both the model engaged with the work and it came to nothing. Exit 2 does not; a repeated
    refusal is reported as a harness alarm instead, which is a different signal for a different
    reader. Yields never counted.

    An acceptance clears the run, and editing the brief clears it -- a new sha is a new brief,
    the same act that releases a park and an objection.
    """
    history = rows() if history is None else history
    # NEEDS-SCOPE IS THE BRIEF'S FAULT, NOT THE HARNESS'S (2026-09-26). It is an exit 2 like a
    # bad sandbox baseline, but coder.py says exactly what to fix -- "Name the functions to edit
    # on a SYMBOLS: line in the brief" -- and nothing else will change it. Excluding it with the
    # other refusals let p47 and p54 resubmit every tick (p47: 12 times) on an idle model.
    ns = needs_scope(chunk, sha, history)
    if ns:
        return True, (f"NEEDS-SCOPE: the target file does not fit the model's window with this "
                      f"brief ({ns} refusal(s)). Add a SYMBOLS: line naming the functions to edit "
                      f"-- editing the brief releases it.")
    # A FAILURE THE SUPERVISOR BLAMED ON THE HARNESS IS NOT THE TASK GOING NOWHERE.
    # Same rule as REFUSED_TO_START above, applied to the case where the harness broke AFTER
    # the run was counted as started, so it lands as exit 1 and looks like the model trying
    # and failing. On 2026-09-16 a crash in coder.py killed six dispatches in under an hour
    # and parked all six with "the brief or its oracle is the thing to fix"; the briefs were
    # fine and Q16 had already written HARNESS-BUG against every one of them. Reading that
    # decision here is what stops the scheduler accusing the wrong thing twice.
    _harness = _harness_bug_keys()
    fixed_at = harness_fixed_at()
    run, harness_run = [], 0
    for r in history:
        # A HARNESS FIX RELEASES WHAT THE HARNESS PARKED (2026-10-08): failures recorded before the
        # last harness fix (HARNESS_FIXED) are not the brief's -- builds parked on one failure while
        # the yield loop and the reply-budget bug (a0124af5) made every build fail.
        if fixed_at and r.get("at") and str(r["at"]) < fixed_at:
            continue
        if (r.get("event") != "finished" or r.get("kind", "production") != "production"
                or r.get("chunk") != chunk or r.get("brief_sha256") != sha
                or r.get("exit") in YIELD_EXITS or r.get("exit") == REFUSED_TO_START
                or signal_not_timeout(r)):
            continue
        if f"{r.get('at')}|{r.get('chunk')}|{r.get('model')}" in _harness:
            harness_run += 1
            continue
        if r.get("accepted"):
            run, harness_run = [], 0
        else:
            run.append(r)
    # A HARNESS BUG THAT REPEATS IS STILL A REASON TO STOP (2026-09-27). Harness-blamed failures
    # are rightly not charged to the brief -- but excluding them without limit let p47 (t385)
    # rebuild 16 times with the same brief, same model and the same 61,178-token prompt, failing
    # WINDOW FULL identically, and each failure filed another duplicate HARNESS task. Three in a
    # row: park it until the harness is fixed or the brief is edited.
    if harness_run >= HARNESS_PARK_AFTER:
        return True, (f"BLOCKED BY A HARNESS BUG: {harness_run} failures on this brief were the "
                      f"harness's fault (supervise.py --failures), so retrying it cannot help. "
                      f"Fix the harness task, then edit the brief to release it.")
    if len(run) < FUTILE_AFTER:
        return False, ""
    kinds = ", ".join(sorted({f"exit {r.get('exit')}" for r in run[-FUTILE_AFTER:]}))
    models = ", ".join(sorted({str(r.get("model")) for r in run[-FUTILE_AFTER:]}))
    return True, (f"{len(run)} attempt(s) in a row came to nothing ({kinds}; on {models}). "
                  f"The brief or its oracle is the thing to fix -- edit the brief to release it. "
                  f"Evidence: python3 scripts/supervise.py --failures")


def task_closed(brief_name):
    """The brief's task id if that task is filed as done, else None.

    A HOLD ON FINISHED WORK IS NOT A HOLD (2026-10-01). On that morning all 22 chunks reported
    "held as futile" were finished: 18 belonged to tasks Claude had closed by hand during the
    backlog push, and the other 4 had green targets on main. Their failure history outlived the
    work, so session_start kept asking a person to fix 22 briefs nobody needed. A closed task
    is checked here because it costs one stat; a green target costs a suite run, which is why
    classify() checks THAT before dispatching rather than this report doing it.
    """
    tid = task_of(brief_name)
    if tid and any((REPO / "workspace" / "tasks" / "done").glob(f"{tid}--*.md")):
        return tid
    return None


def held_tasks(chunks=None, history=None, red_of=None):
    """[(chunk, why)] for every brief held as futile. Models are never held -- see futile().

    A chunk whose task is closed is never held, and nor is one whose target is green when the
    caller passes red_of (target -> bool|None): the work is done, the old failures are history."""
    history = rows() if history is None else history
    out = []
    for c in (chunks or []):
        sha = brief_sha(c.get("brief"))
        if not sha or task_closed(c.get("brief")):
            continue
        bad, why = futile(c["chunk"], sha, history)
        if bad and not (red_of and red_of(c["target"]) is False):
            out.append((c["chunk"], why))
    return out


def parked(chunk, sha, history=None):
    """(True, why) once a brief has failed on MAX_SWITCHES + 1 models."""
    try:
        import supervise
        if supervise.still_red(chunk, sha):
            return True, (f"STILL RED: the target suite stayed red after every attempt -- revise the brief; "
                          f"the failing output is in the job log (supervision.jsonl names it)")
        if supervise.is_parked(chunk, sha):
            return True, (f"{task_routing.role_name('supervision')}, supervising, parked it "
                          "(workspace/supervision.jsonl)")
    except Exception as e:                                               # noqa: BLE001
        print(f"  NOTE: supervisor parking unreadable ({e}) -- judged on failures alone", file=sys.stderr)
    f = failures(chunk, sha, history)
    if len(f) > MAX_SWITCHES:
        return True, (f"failed on {len(f)} model(s) ({', '.join(f)}); max switches "
                      f"({MAX_SWITCHES}) reached. Edit the brief to release it.")
    # AND HELD IF IT IS SIMPLY GETTING NOWHERE. The switch count above only sees exit 1, so a
    # brief that refuses to start or fails to deliver can repeat for ever without ever being
    # held -- p32 did that nine times in seven hours. This is the task-level hold Jas asked for
    # in place of benching a model.
    bad, why = futile(chunk, sha, history)
    if bad:
        return True, why
    # ONE LOOP BREAKER FOR EVERY STAGE (2026-10-24). futile() above counts build attempts only, so a
    # preflight that said NOT SATISFIABLE in ~1 s (under the crash hold's 5-second floor) ran 67 times
    # in 24 h for p668-t1250. The same result LOOP_N times with the brief unchanged parks it here; a
    # failure the harness is giving everyone is excused there (scripts/loop_breaker.py).
    try:
        import loop_breaker
        looping, why = loop_breaker.chunk_loop(chunk, sha, history if history is not None else rows())
        if looping:
            return True, why
    except Exception as e:                                               # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: loop breaker unreadable ({type(e).__name__}: {e}) -- judged without it", file=sys.stderr)
    return False, ""


# =============================================================== the priority layer
#
# Jas, 2026-09-14: "I want anything to be able to interrupt the manager, whether that's Q16 or
# another model. If Q16 needs to be interrupted for multiple reasons, the interruptions need to be
# queued. It needs a priority queue and production queue. Production items automatically get
# rerouted to another model, unless it was in the middle of something and will resume."
#
# The case that made it concrete, the same evening: the supervisor tried four times to decide on
# p07's failure and timed out every time, because its model (Q16) was holding a production lane
# and a request queues behind the generation in progress.
#
# TWO QUEUES.
#   PRIORITY   work a role owns, ordered by level then arrival:
#                1 supervise      a failed job needs a decision        -> manager
#                2 preflight      a brief needs review before coding   -> manager
#                3 script-review  a registered or changed script       -> auditor
#   PRODUCTION enrolled chunks (plan()).
# RULES.
#   * A model with priority work waiting is RESERVED: it takes no new production.
#   * If it is idle, it starts its first priority item. If it is running PRODUCTION, that lane is
#     interrupted (yield flag, then SIGTERM, which coder.py turns into a checkpoint once p20 lands).
#     If it is running PRIORITY work, the rest wait their turn -- a review is never interrupted.
#   * Production that would have gone to a reserved model goes to another free model.
#   * An interrupted job that left a CHECKPOINT resumes on its own model when that model is free
#     of priority work; one that did not is simply rerouted like any other production item.
# WHO AUDITS AND WHO BUILDS, Jas 2026-09-15, and the principle is about VERIFIABILITY rather
# than difficulty. A script-review has no oracle: nothing can falsify the reviewer's verdict, it
# goes into a report for a person to triage, and its only mechanical guard is that the line it
# cites exists. A production job is checked three times -- a target suite that must go green,
# coder.py refusing to start on a baseline it cannot trust, and a human verdict on the review
# branch. So the weakly-verified work goes to the stronger model and the strictly-verified work
# to the faster one, where being wrong cannot escape. Jas had already moved corpus_scripts.py to
# Q16 for the same reason: Gemma called all 37 scripts "on-demand" where Q16 split them 25/11/1.
ROLES = _serving(lambda m: {r: t for t, rs in m.serving().items() for r in rs},
                 {"manager": "qwen3.8:27b-mtp-bf16", "auditor": "qwen3.8:27b-mtp-bf16"})
# WHO TAKES WHAT, AND IN WHAT ORDER, IS THE ROLE STORE'S (Jas, 2026-09-29, plan step 4). Each kind
# of work goes to the role whose task list has it (task_routing.owner), in that role's list order,
# and only a task whose Interrupt box is on may stop a build. LEVELS is kept for its readers (the
# Hub's Production columns): Jas's cost-of-delay order (2026-09-28), which now only breaks the tie
# between DIFFERENT roles sharing one model. See scripts/task_routing.py.
import task_routing  # noqa: E402
ROUTING = task_routing.load()
LEVELS = task_routing.levels(ROUTING)
YIELD_DIR = REPO / "workspace" / "run-logs" / "yield"
CHECKPOINTS = REPO / "workspace" / "run-logs" / "checkpoints"
YIELD_EXITS = (4, -15, 143, -9)          # coder.py YIELDED, or killed by SIGTERM before p20


def _safe(model):
    return model.replace(":", "_")


def checkpoint_model(brief_name):
    """The model an interrupted job left a checkpoint for, or None. Label = coder.py's brief label."""
    import hashlib
    f = REPO / "workspace" / "briefs" / brief_name
    if not f.is_file() or not CHECKPOINTS.is_dir():
        return None
    label = "brief-" + hashlib.sha256(f.read_text(encoding="utf-8").encode()).hexdigest()[:8]
    for cp in sorted(CHECKPOINTS.glob(f"{label}--*.json")):
        return cp.stem.split("--", 1)[1].replace("_", ":", 1)
    return None


def station_roles(d=None):
    """Roles on a server of their own (the senior station): they walk their own list in senior_slot."""
    d = d or ROUTING
    return {k for k in task_routing.model_roles(d)
            if str(d["roles"][k].get("endpoint") or "").rstrip("/") not in ("", f"http://127.0.0.1:{MAIN_PORT}")}


def main_role_models(d=None):
    """Models of the MAIN server's model roles, whatever is on their lists.

    PRIORITY WORK IS NOT GATED ON BUILDING (2026-10-03). schedule() was handed the production
    models -- the roles with Building on their list -- as "available". When Building came off the
    Manager on the Models tab (2026-10-02 20:04Z), Q4 was still loaded and still owned RCA and
    preflight, but every one of its items read "is not an available model": 30 RCAs held from
    20:31Z until this fix. Builds still go only to building roles (prod_models is unchanged)."""
    d = d or ROUTING
    st = station_roles(d)
    return [d["roles"][k]["model"] for k in task_routing.model_roles(d)
            if k not in st and d["roles"][k].get("model")]


def main_preflights(senior):
    """Does the MAIN server take preflights? When no station is up, or when a main-server role has
    Preflight on its list (it was moved off the senior on the Models tab)."""
    return senior is None or bool(task_routing.owner(ROUTING, "preflight", exclude=station_roles()))


_ROUTED = None


def _share(kind):
    """ANY ROLE WITH THE KIND ON ITS LIST TAKES IT WHEN IT HAS A FREE LANE (Jas, 2026-10-16: "Why wait
    30 min when there are so many other tasks? All roles should get any of the tasks in their list").
    The first version dealt items 4:1 by lane count up front, so the Auditor idled between its
    share while 88 RCAs waited behind the Manager. Now each item goes to the owner with the most
    free lanes left this tick (lanes minus its running ledger jobs minus items already given it);
    when every owner is full it goes to the first, and waits there as before."""
    global _ROUTED
    if _ROUTED is None:
        _ROUTED = {}
        try:
            for r in ledger_running():
                if r.get("role"):
                    _ROUTED[r["role"]] = _ROUTED.get(r["role"], 0) + 1
        except Exception as e:                                      # noqa: BLE001 -- start empty
            print(f"  NOTE: running roles unreadable, lanes counted as free: {e}")
    st = station_roles()
    cands = [k for k in task_routing.owners(ROUTING, kind) if k not in st]
    if not cands:
        return None
    free = {k: int(ROUTING["roles"][k].get("lanes") or 0) - _ROUTED.get(k, 0) for k in cands}
    pick = max(cands, key=lambda k: (free[k], -cands.index(k)))
    if free[pick] <= 0:
        pick = cands[0]
    _ROUTED[pick] = _ROUTED.get(pick, 0) + 1
    return pick


def routed(kind, **fields):
    """A priority item for `kind`, on the role whose list has it. A main-server role is preferred to
    a station role (the station takes its own work in senior_slot); with no role at all it carries
    role=None and schedule() queues it with that reason."""
    role = _share(kind) or task_routing.owner(ROUTING, kind)
    model = ROLES.get(role) if role else None
    return {"kind": kind, "role": role, "model": model or f"(no role has {task_routing.KIND_TASK.get(kind, kind)})",
            "interrupt": task_routing.interrupts(ROUTING, kind, role) if role else False,
            "level": LEVELS.get(kind, task_routing.UNRANKED), **fields}


MANAGER_QUEUE_ALIVE_S = 15 * 60


def scoping_waiting(queue_dir=None, now=None, upcoming=None):
    """Is the manager queue alive and holding a task for Scoping? Alive = no STOP file and its
    progress log written in the last MANAGER_QUEUE_ALIVE_S (it writes on every loop, even when idle
    or yielding). A long scope writes nothing for an hour -- then this says no, and a job that starts
    meets the lease the scope holds and yields, as before."""
    q = Path(queue_dir or (REPO / "workspace" / "run-logs" / "scoping-queue"))
    now = now or time.time()
    log = q / "progress.log"
    if (q / "STOP").exists() or not log.exists() or now - log.stat().st_mtime > MANAGER_QUEUE_ALIVE_S:
        return False
    try:
        if upcoming is None:
            import manager_next
            upcoming = manager_next.upcoming
        return bool(upcoming(1)[0])
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: manager queue unreadable ({type(e).__name__}: {e}); nothing held behind Scoping")
        return False


def scoping_items(running, slots, d=None, live=None, upcoming=None):
    """(stand-in items, running rows) for Scoping in the lane rotation (2026-10-08).

    One stand-in per task the manager queue could start now (manager_next.upcoming, which skips
    claimed tasks), up to the model's lanes; one running row per live worker, so the rotation counts
    them as jobs on the model. Stand-ins carry scoping_standin and are never started by this loop."""
    d = d or ROUTING
    owner = task_routing.owner(d, "scoping")
    if not owner:
        return [], []
    model = (d.get("roles", {}).get(owner) or {}).get("model")
    if not model:
        return [], []
    try:
        import scoping_lanes
        workers = scoping_lanes.live(role=owner) if live is None else live   # its own model's workers only (2026-10-14)
        if upcoming is None:
            import manager_next
            upcoming = manager_next.upcoming
        n = max(0, (slots(model) if slots else 1))
        ready = upcoming(n)[0] if scoping_waiting(upcoming=upcoming) or live is not None else []
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: scoping lanes unknown ({type(e).__name__}: {e})")
        return [], []
    # NOT TWICE (2026-10-08): a worker whose model call has started is already in `running` as a
    # "manager" job; counting its claim as well made 3 scopes fill all 6 lanes and nothing started.
    seen = sum(1 for r in running or () if r.get("kind") == "scoping" and r.get("model") == model)
    rows = [{"kind": "scoping", "model": model, "role": owner, "key": f"scope:{t}", "pid": pid,
             "scoping_worker": True} for t, pid in workers][seen:]
    items = [{"kind": "scoping", "model": model, "role": owner, "key": f"scope:{t}",
              "interrupt": False, "scoping_standin": True} for t in ready]
    return items, rows


def hold_below_scoping(items, d=None, waiting=None, running=(), slots=None):
    """(items to schedule, items held) -- the Scoping owner's tasks BELOW Scoping on its own list wait
    while the manager queue has work. Other roles' items are untouched.

    ONLY FOR THE SLOT SCOPING NEEDS (2026-10-02, -np 4). The hold was written for one slot, where a
    freed Q4 going to a session review cost Scoping the race. With N slots, one is kept for the
    manager queue and the rest are filled: free = slots - jobs already on that model - 1. At 4 slots
    44 session reviews and RCAs sat behind a queue that always has a task, three slots idle."""
    d = d or ROUTING
    owner = task_routing.owner(d, "scoping")
    if not owner:
        return items, []
    # NOT WHEN THE QUEUE IS ON THE SENIOR'S STATION (2026-10-03). manager_queue.sh runs scoping on
    # station 2 when the Senior's list puts Scoping above Preflight (or SCOPE2 is fresh). Holding the
    # main-server owner's RCAs then kept nothing for scoping and left the main Q4 idle.
    try:
        on_station = task_routing.scoping_station(d) == "senior" or (waiting is None and _scope2_fresh())
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: scoping station unknown ({type(e).__name__}: {e}); holding as before")
        on_station = False
    if on_station and owner not in station_roles(d):
        return items, []
    at = task_routing.position(d, owner, "scoping")
    below = [i for i in items if i.get("role") == owner and task_routing.position(d, owner, i["kind"]) > at]
    if not below or not (scoping_waiting() if waiting is None else waiting):
        return items, []
    if slots:
        on = {}
        # THE MAIN SERVER'S JOBS ONLY: station 2 serves the same Q4 tag, and counting its three RCAs
        # here (2026-10-02) left the idle main server "full".
        for r in running or ():
            if r.get("server_port", MAIN_PORT) == MAIN_PORT:
                on[r.get("model")] = on.get(r.get("model"), 0) + 1
        free = {}
        for i in below:
            m = i.get("model")
            free.setdefault(m, max(0, slots(m) - on.get(m, 0) - 1))
        let = []
        for i in below:
            if free[i.get("model")] > 0:
                free[i.get("model")] -= 1
                let.append(i)
        below = [i for i in below if all(i is not x for x in let)]
        if not below:
            return items, []
    ids = {id(i) for i in below}
    return [i for i in items if id(i) not in ids], below


NO_ROLE_TITLE = "No role does "


def no_role_fallback(items, add=None, ordered=None, set_meta=None, add_note=None):
    """FALLBACK TO CLAUDE (plan 3b): "if every role doing a task is deleted, the task's items are Assign
    to: Claude, with the note 'no role does <task>'." Removing a task from a role's list (the Tasks
    page's minus) is not guarded the way deleting a role is, so this is how that work stops waiting in
    silence. One open task per orphaned task, filed once and not again while it is open; an ASSIGNED
    item whose role lacks Assigned items is handed straight to Claude. Returns what it did."""
    import taskstore
    add, ordered = add or taskstore.add, ordered or taskstore.ordered
    set_meta, add_note = set_meta or taskstore.set_meta, add_note or taskstore.add_note
    done, orphans = [], {}
    for i in items:
        if i.get("role"):
            continue
        if i["kind"] == "assigned_items":
            up = escalate_to("technical")
            set_meta(i["key"], needs=up)
            add_note(i["key"], f"It was assigned to {i.get('assigned_to')}, whose task list has no Assigned items, "
                               f"so no model would work it. Assigned to {up} instead (plan 3b fallback); add "
                               "Assigned items to that role on the Models tab to let it take these.",
                     who="dispatch_loop")
            done.append(f"{i['key']} -> {up}")
            continue
        orphans.setdefault(task_routing.KIND_TASK.get(i["kind"], i["kind"]), []).append(i["key"])
    open_titles = {m.get("title", "") for m, _ in ordered() if m.get("status") not in ("done", "cancelled")}
    for task, keys in sorted(orphans.items()):
        title = NO_ROLE_TITLE + task
        if title in open_titles:
            continue
        add(title, area="dispatch", source="dispatch_loop", needs=escalate_to("technical"),
            body=(f"No role has the task '{task}' on its list (models/ROLES.json), so {len(keys)} item(s) wait "
                  f"and nothing will take them: {', '.join(map(str, keys[:20]))}"
                  + (f" and {len(keys) - 20} more" if len(keys) > 20 else "") + ". Put the task back on a "
                  "role's list on the Models tab (or retire the work), then close this. Plan 3b: work no role "
                  "does goes to Claude, never silently nowhere."))
        done.append(f"filed: {title}")
    return done


def priority_items(chunks, running, red_of, history=None, objections=None, preflight=True):
    """Every piece of priority work waiting, in the order the role store gives (task_routing.order).
    preflight=False when the senior station takes the preflights (senior_slot), so the main server
    never runs them too."""
    global _ROUTED
    _ROUTED = None              # each pass deals free lanes afresh (who_works calls this too)
    history = rows() if history is None else history
    items = []
    # (failed builds are no longer priority work for a role: tick() decides them by rule, 2026-10-19)
    need_review, _ready, _skipped = classify(chunks, running, red_of, history, objections)
    for c in (need_review if preflight else []):
        items.append({**c, **routed("preflight", created="", key=c["chunk"])})
    try:
        import truncation_review
        for q in truncation_review.pending_requests():
            items.append(routed("script-review", created=q["at"], key=q["path"], chunk=q["path"],
                                path=q["path"], brief=None, brief_sha256=q["sha256"]))
    except Exception as e:                                          # noqa: BLE001
        print(f"  NOTE: review requests unreadable: {e}")
    # SESSION REVIEW AND ITS RCAs (Jas, 2026-09-28): a finished session is read by the manager for
    # work put off without a task and problems left unfiled; each finding gets an in-depth RCA.
    try:
        import session_review
        for f in session_review.pending_requests():
            items.append(routed("session-review", created="", key=f.name, chunk=f.name, path=str(f),
                                brief=None, brief_sha256=None))
        for f in session_review.pending_findings():
            items.append(routed("rca", created="", key=f.name, chunk=f.name, path=str(f),
                                brief=None, brief_sha256=None))
    except Exception as e:                                          # noqa: BLE001
        print(f"  NOTE: session-review queue unreadable: {e}")
    # SCRIPT AUDITS (t790 step 2): a queued request is priority work for whichever role Jas gave the
    # script_audit task on the Models tab. Nothing names a role here; with none it waits, said.
    try:
        import script_audit
        for f in script_audit.pending_requests():
            items.append(routed("script-audit", created=f.stem, key=f.name, chunk=f.name, path=str(f),
                                brief=None, brief_sha256=None))
    except Exception as e:                                          # noqa: BLE001
        print(f"  NOTE: script-audit queue unreadable: {type(e).__name__}: {e}")
    # MODEL TASKS (plan step 4): a task added on the Flow tab is priority work for the role whose
    # list has it, like any built-in one; task_worker.py works the item.
    try:
        import task_worker
        for tid, f in task_worker.pending():
            items.append(routed(tid, created=f.stem, key=f"{tid}/{f.name}", chunk=f"{tid}--{f.stem}",
                                path=str(f), brief=None, brief_sha256=None, model_task=True))
    except Exception as e:                                          # noqa: BLE001
        print(f"  NOTE: model-task queues unreadable: {type(e).__name__}: {e}")
    # ASSIGNED ITEMS (plan 3b, 2026-09-30): a task Assigned to: a MODEL role is worked by THAT role
    # (assigned_worker.py), not by whichever role owns the kind -- so the role is the assignee, and its
    # own list decides where "Assigned items" sits and whether it may interrupt. A role whose list
    # does not have it gets role=None, and schedule() queues it with that reason.
    try:
        import assigned_worker
        for tid, who in assigned_worker.pending(ROUTING):
            e = task_routing.entry(ROUTING, who, "assigned_items")
            items.append({"kind": "assigned_items", "role": who if e else None,
                          "model": ROLES.get(who) or f"(no model for {who})",
                          "interrupt": bool(e and e.get("interrupt")),
                          "level": LEVELS.get("assigned_items", task_routing.UNRANKED), "created": "",
                          "key": tid, "chunk": f"assigned--{tid}", "path": tid, "brief": None,
                          "brief_sha256": None, "assigned_to": who})
    except Exception as e:                                          # noqa: BLE001
        print(f"  NOTE: assigned items unreadable: {type(e).__name__}: {e}")
    busy_keys = {(r.get("kind"), r.get("key")) for r in running}
    items = [i for i in items if (i["kind"], i["key"]) not in busy_keys]
    # SERIES (Jas, 2026-10-01): a task with a series limit yields once to the next task down
    # its role's list after that many in a row. 0/absent = no limit (every task, until set).
    return task_routing.apply_series(ROUTING, task_routing.order(ROUTING, items),
                                     series_streak(history))


def series_streak(history):
    """{role: (kind, n)}: the kind each role started most recently and how many in a row.
    Started rows carry no role, so the role is the kind's owner in the role store."""
    out, done = {}, set()
    for r in reversed(history or []):
        if r.get("event") != "started" or not r.get("kind"):
            continue
        role = task_routing.owner(ROUTING, r["kind"])
        if not role or role in done:
            continue
        kind, n = out.get(role, (r["kind"], 0))
        if kind == r["kind"]:
            out[role] = (kind, n + 1)
        else:
            done.add(role)
    return out


CHEAP_INTERRUPT_S = 1200

# MEMORY. Measured from ollama's own scheduler log on 2026-09-14 (sched.go "predicted" size with a
# 65,536-token context, total 121.6 GiB): bf16 ~62 GiB, gemma4:26b 29.2, q4 20.4. Three at once is
# ~111 GiB before overhead, and at 21:02 and 21:07 ollama logged "model predicted to exceed
# available memory, evicting" -- it unloaded the models under two RUNNING jobs, which then sat on
# open sockets for 85 minutes waiting for replies that could not come. The "three lanes in
# parallel" configuration had been evicting since it started. Any two fit; three do not.
# ONE PLACE (2026-10-06): the measured footprints now live in REGISTRY.json's `weight_gb`, which had
# cited this table as its source; a static copy here did not know Swift and costed it at the 30 GiB
# default when it holds ~95. A model the registry gives no figure still gets UNKNOWN_FOOTPRINT_GIB.
def _registry_footprints():
    try:
        reg = json.loads((REPO / "projects/local-inference/models/REGISTRY.json").read_text(encoding="utf-8"))
        return {m["tag"]: float(m["weight_gb"]) for m in reg.get("base_models", []) if m.get("weight_gb")}
    except (OSError, ValueError, KeyError) as e:
        print(f"  FOOTPRINT: REGISTRY.json unreadable ({type(e).__name__}); every model costs the default",
              file=sys.stderr)
        return {}


FOOTPRINT_GIB = _registry_footprints()
UNKNOWN_FOOTPRINT_GIB = 30.0
MEMORY_BUDGET_GIB = 108.0
# A lane whose log has been silent this long AND whose model is not loaded is not "mid-attempt":
# nothing is generating for it. It is waiting on a request ollama dropped when it evicted the model.
WEDGE_S = 1800
# REPORT-ONLY BY DEFAULT. Stopping a process is Jas's call until he says otherwise: on 2026-09-14
# Claude's own attempt to stop these two lanes was refused by the permission check, and a timer
# that silently did the same ten minutes later would be that decision taken without him. Set
# WEDGE_ACTION = "stop" (or create workspace/run-logs/wedge-stop.enabled) to let the scheduler
# SIGTERM wedged lanes itself. Either way they are excluded from "running", so their memory and
# models are scheduled as free.
WEDGE_ACTION = "report"


def _device_map():
    """({model: device}, {device: budget GiB}) from REGISTRY's `server` and SERVERS.json's devices.

    ONE BUDGET PER DEVICE (2026-10-12). fits() summed every model against the GX10's 108 GB, so the
    ThinkPad's Q4 (17.5 GB in its OWN 24 GB of VRAM) was charged beside Swift's 95 and every
    script review, RCA and session review queued for it was refused. A model with no `server`
    lives on the GX10; the GX10 keeps MEMORY_BUDGET_GIB, another device gets memory_gb - reserve_gb."""
    try:
        models_dir = REPO / "projects" / "local-inference" / "models"
        reg = json.loads((models_dir / "REGISTRY.json").read_text(encoding="utf-8"))
        srv = json.loads((models_dir / "SERVERS.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"  NOTE: device map unread ({type(e).__name__}); one budget for every model")
        return {}, {}
    where = {m["tag"]: (srv.get("servers", {}).get(m["server"]) or {}).get("device", "gx10")
             for m in reg.get("base_models", []) if m.get("server")}
    budgets = {d: float(v.get("memory_gb", 0)) - float(v.get("reserve_gb", 0))
               for d, v in (srv.get("devices") or {}).items() if d != "gx10"}
    return where, budgets


DEVICE_OF, DEVICE_BUDGET_GIB = _device_map()


def _with_bare_latest(table):
    """`table` plus each 'name:latest' key again as 'name' (2026-10-24). Ollama treats the two as one
    model, and the Models tab can save either: the Auditor saved as 'swift-junior' fell to the GX10
    default here, fits() refused it beside Swift, and no Auditor build started overnight."""
    out = dict(table)
    for k, v in table.items():
        if k.endswith(":latest"):
            out.setdefault(k[: -len(":latest")], v)
    return out


DEVICE_OF = _with_bare_latest(DEVICE_OF)
FOOTPRINT_GIB = _with_bare_latest(FOOTPRINT_GIB)


def fits(model, busy):
    """Would running `model` next to the models in `busy` on the SAME device stay inside that
    device's measured memory budget?"""
    dev = DEVICE_OF.get(model, "gx10")
    need = sum(FOOTPRINT_GIB.get(m, UNKNOWN_FOOTPRINT_GIB) for m in set(busy) | {model}
               if DEVICE_OF.get(m, "gx10") == dev)
    return need <= DEVICE_BUDGET_GIB.get(dev, MEMORY_BUDGET_GIB)


def pid_alive(pid):
    """True when the process exists. A lane whose process is GONE is not running, whatever its model is.

    ESRCH means the process does not exist; EPERM means it does and belongs to someone else -- which is still
    alive. Catching OSError wholesale would read a permission error as a death and free a genuinely busy lane.
    """
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return True                                  # cannot tell: do not free a lane on a guess


def wedged(lanes, resident):
    """Lanes that cannot finish: a DEAD PROCESS, or a log silent past WEDGE_S with its model not in memory.

    THE DEAD-PROCESS CASE WAS MISSING (2026-10-29). The only test was "the model is not resident", so a job
    whose pid had gone counted as running for ever as long as its model stayed loaded -- and Swift's vLLM is
    resident all day on this box. Measured: two lanes (pids 1596355, 1596356) started 22:47Z, no finished row,
    /proc/<pid> absent, wedged() returned [], and the tick reported running: 2 with 0 started and 169 chunks
    "waiting for a free lane" while 18.7 GB sat free. Two phantom lanes were the shortage.
    """
    out = []
    for r in lanes:
        pid = r.get("pid")
        if pid and not pid_alive(pid):
            out.append(r)                     # the process is gone: the lane is free, whatever the model
        elif pid and r.get("model") not in resident and (r.get("log_age_s") or 0) > WEDGE_S:
            out.append(r)
    return out


def coder_can_checkpoint():
    return '"--resume-checkpoint"' in CODER.read_text(encoding="utf-8", errors="replace")


_MEASURED_SLOTS = {}


def model_slots(model):
    """Requests the model's server decodes at once.

    R1, MEASURED CAPACITY (2026-10-29): the stored number is the ENGINE's ceiling, not a capacity decision. The
    measured curve on this box is not monotonic -- 1/2/4 lanes gave 11.4/18.9/25.3 tok/s aggregate at ~3% pressure,
    and 20.1/35.8/35.3 at ~49% -- so four lanes is right at one pressure and wasted at another. When the redesign's
    admission layer is switched on, the lane count comes from the MEASURED curve capped by the engine's ceiling;
    while it is off (the default) this returns exactly what it always did, so the change is reversible.

    ONE SWITCH, NOT TWO: this and the byte admission gate share capacity_model.gate_switch_on(), so "the redesign's
    admission layer is on" is a single statement rather than two that can disagree.
    """
    try:
        import capacity_model as _cm
        if _cm.gate_switch_on():
            if model not in _MEASURED_SLOTS:
                _MEASURED_SLOTS[model] = _cm.inference_lanes()
            n, why = _MEASURED_SLOTS[model]
            # THE SERIAL CAP APPLIES TO THE MEASURED NUMBER TOO (2026-10-30). inference_lanes() comes from a curve
            # measured on the box's parallel engine, so handing 4 lanes to a serial-architecture model is the queueing
            # and the 4x timeouts the serial rule exists to prevent. The cap is about the MODEL, not about who declared
            # the capacity, so it is applied last -- the same rule modellease.slots now applies to the engine's number.
            try:
                import modellease as _ml
                if _ml.is_serial(model) and int(n) > 1:
                    print(f"  capacity: {model} runs 1 lane -- serial architecture caps the measured {n}")
                    return 1
            except Exception as e:                                  # noqa: BLE001 -- said, not swallowed
                print(f"  NOTE: serial-arch check unreadable ({type(e).__name__}: {e}); the measured number stands")
            print(f"  capacity: {model} runs {n} lane(s) -- {why}")
            return max(1, int(n))
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: measured capacity unreadable ({type(e).__name__}: {e}); the stored slots still decide")
    try:
        import modellease
        return modellease.slots(modellease.lease_for(model), model=model)
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: slots for {model} unknown ({type(e).__name__}: {e}); 1 assumed")
        return 1


def _ready_n(build_ready, m):
    """Ready builds for model m: a {model: n} map, or the older set of models (1 each)."""
    if isinstance(build_ready, dict):
        return int(build_ready.get(m, 0))
    return 1 if m in build_ready else 0


def schedule(models, items, running, can_checkpoint=False, slots=None, build_ready=()):
    """Pure (the live slot count is passed in by the tick; the default is 1). -> (starts, preempt_pids, queued, reserved). queued = [(item, why)].

    ONE LANE PER MODEL BECAME ONE LANE PER SLOT for priority work (2026-10-01): the main server runs
    two slots (OLLAMA_NUM_PARALLEL=2) and a second Q4 request decodes at the rate one gets alone, so
    a model doing priority work with a free slot takes the next priority item. Builds keep one lane:
    a build beside a build is what the 0.92x aggregate measurement was about."""
    slots = slots or (lambda m: 1)
    starts, preempt, queued, reserved = [], [], [], set()
    # AN ITEM ALREADY RUNNING IS NOT OFFERED AGAIN (2026-10-19). launch() refuses a live twin (7d304c8a),
    # and in two days it refused 2,618 script reviews and ~230 other jobs the plan had chosen while
    # their first copy still ran -- each refusal a ledger row and a wasted pick. Same identity as launch().
    live = {(r.get("kind"), r.get("key")) for r in running if r.get("key")}
    items = [i for i in items if (i.get("kind"), i.get("key")) not in live]
    by_model = {}
    for i in items:
        by_model.setdefault(i["model"], []).append(i)
    for m, mine in by_model.items():
        if any(not i.get("role", True) for i in mine):
            queued += [(i, f"no role has this task on its list (Models tab)") for i in mine]
            continue
        if m not in models:
            queued += [(i, f"{m} is not an available model") for i in mine]
            continue
        reserved.add(m)
        on_m = [r for r in running if r["model"] == m]
        if not on_m:
            busy = {r["model"] for r in running} | {x["model"] for x in starts}
            if not fits(m, busy):
                queued += [(i, f"waiting for memory: {m} does not fit beside {sorted(busy)}") for i in mine]
                continue
            n = max(1, slots(m)) if all(i.get("kind") != "production" for i in mine) else 1
            picked, won = task_routing.round_robin(ROUTING, mine, {}, n, _ready_n(build_ready, m))
            starts += picked
            if won:
                reserved.discard(m)             # a lane left for the build plan() starts
            queued += [(i, f"queued behind {mine[0]['kind']} on {m}") for i in mine if i not in picked]
        # AN INTERRUPTING TASK TAKES THE LANE OF THE LOWEST-PRIORITY JOB (Jas, 2026-10-12: "I made the
        # console task an interruptible one for Swift so I want one of the lanes to stop what it is
        # doing and jump to this ... the task that is lowest in the priority list is what gets
        # dropped"). With every lane full, the old branches below interrupted only when EVERY lane
        # held a build, and then interrupted ALL of them. Now: for each waiting item whose Interrupt
        # box is on, stop ONE running job -- the one lowest in its role's list (task_routing.position),
        # among jobs that can afford it (a build that checkpoints, or anything under
        # CHEAP_INTERRUPT_S) -- and start the item in that lane.
        elif (slots(m) - len(on_m) <= 0
              and any(i.get("interrupt") and i.get("kind") != "production" for i in mine)):
            urgent = [i for i in mine if i.get("interrupt") and i.get("kind") != "production"]

            def _rank(r):
                k = r.get("kind", "production")
                role = r.get("role") or task_routing.owner(ROUTING, k)
                return task_routing.position(ROUTING, role, k) if role else 99

            victims = sorted([r for r in on_m if r.get("pid") and r["pid"] not in preempt and
                              ((can_checkpoint and r.get("kind", "production") == "production")
                               or (r.get("elapsed_s") or 0) < CHEAP_INTERRUPT_S)],
                             key=lambda r: (-_rank(r), r.get("elapsed_s") or 0))
            for i in urgent:
                if not victims:
                    queued.append((i, f"waiting: every lane on {m} holds work that cannot be "
                                      f"interrupted cheaply"))
                    continue
                v = victims.pop(0)
                preempt.append(v["pid"])
                starts.append(i)
                queued.append(({**v, "key": v.get("key") or v.get("brief"), "kind": v.get("kind", "production")},
                               f"interrupted for {i['kind']}: lowest priority on {m}"))
            queued += [(i, f"queued: {m} is doing priority work") for i in mine if i not in urgent]
        # A MODEL WITH FREE LANES IS NOT TAKEN BY ITS BUILD (2026-10-08). The two branches below --
        # wait for the build, or interrupt it -- were written for one lane, where a build held the
        # whole model. With lanes free they queued every script review "waiting for the build to
        # finish" and kept the model reserved, so no second build started either. They now apply only
        # when the model has no free lane; otherwise the lane rotation below shares the lanes.
        elif (slots(m) - len(on_m) <= 0
              and all(r.get("kind", "production") == "production" for r in on_m)
              and not any(i.get("interrupt", True) for i in mine)):
            # THE INTERRUPT BOX IS OFF for every task waiting here (Models tab): finish first, then
            # switch. The model stays reserved, so no new build starts on it meanwhile.
            queued += [(i, f"waiting for the build on {m} to finish: Interrupt is off for "
                           f"{task_routing.KIND_TASK.get(i['kind'], i['kind'])}") for i in mine]
        elif slots(m) - len(on_m) <= 0 and all(r.get("kind", "production") == "production" for r in on_m):
            # INTERRUPT ONLY WHAT CAN BE AFFORDED. Until coder.py can checkpoint (p20), SIGTERM
            # throws a production run away; doing that to a job hours in would trade hours of
            # GPU for minutes of latency. So: interrupt at once if the job can resume, or if it
            # has run less than CHEAP_INTERRUPT_S; otherwise the priority work queues behind it
            # and the reason says so.
            cheap = [r for r in on_m if r.get("pid") and
                     (can_checkpoint or (r.get("elapsed_s") or 0) < CHEAP_INTERRUPT_S)]
            if cheap and len(cheap) == len(on_m):
                preempt += [r["pid"] for r in cheap]
                queued += [(i, f"interrupting production on {m}") for i in mine]
            else:
                longest = max((r.get("elapsed_s") or 0) for r in on_m)
                queued += [(i, f"queued behind production on {m}: it cannot checkpoint yet and "
                               f"has run {longest // 60} min, so interrupting would lose it") for i in mine]
        else:
            free = slots(m) - len(on_m)
            # A WAITING BUILD NO LONGER ZEROES THE LANES (2026-10-16): this line used to set free=0
            # whenever a production item was in `mine`, so Swift with 4 slots and 1 job held every
            # RCA as "doing priority work". Builds still get one lane; priority work takes the rest.
            if any(i.get("kind") == "production" for i in mine):
                mine = [i for i in mine if i.get("kind") != "production"] + \
                       [i for i in mine if i.get("kind") == "production"][:1]
            # ONE LANE PER TASK FIRST, THEN ROUND AGAIN (Jas, 2026-10-07): task_routing.round_robin.
            held, by_role = {}, {}
            for r in on_m:
                held[r.get("kind", "production")] = held.get(r.get("kind", "production"), 0) + 1
                # Older ledger rows carry no role: the role whose list has that kind owns it.
                rr = r.get("role") or task_routing.owner(ROUTING, r.get("kind", "production"))
                if rr:
                    by_role[rr] = by_role.get(rr, 0) + 1
            picked, won = task_routing.round_robin(ROUTING, mine, held, free,
                                                   _ready_n(build_ready, m),
                                                   running_roles=by_role)
            starts += picked
            if won:
                reserved.discard(m)
            queued += [(i, f"queued: {m} is doing priority work") for i in mine if i not in picked]
    # CAPACITY BELONGS TO THE ROLE, NOT THE MODEL (t9537, Jas 2026-10-28). `reserved` held a WHOLE model
    # back from production whenever priority work was queued for it -- so one priority job, or items the
    # role's Max lanes would not let start anyway, removed a four-lane model from the line. Tick 14:58Z:
    # running 0, 121 items "queued: swift is doing priority work", 174 builds "every model has a lane".
    # A model stays reserved only while the priority work on it fills every lane; a lane the priority
    # roles did not take (their caps, nothing to run) is production's, and plan() counts every lane in use.
    for m in list(reserved):
        used = sum(1 for r in running if r["model"] == m) + sum(1 for x in starts if x["model"] == m)
        if slots(m) - used > 0:
            reserved.discard(m)
    return starts, preempt, queued, reserved


def refused_on(chunk, history):
    """The code state of this chunk's newest run if that run REFUSED TO START (exit 2), else None."""
    starts = {r.get("pid"): r.get("code_state") for r in history or []
              if r.get("event") == "started" and r.get("chunk") == chunk}
    last = None
    for r in history or []:
        if r.get("event") == "finished" and r.get("chunk") == chunk and r.get("kind", "production") == "production":
            last = r
    if last and last.get("exit") == REFUSED_TO_START:
        return starts.get(last.get("pid"))
    return None


def refusal_blockers(history):
    """{suite: chunk} -- the undeclared red suites named by each chunk's NEWEST run, if it refused.

    Read from the refused run's own log (by pid), where coder.py prints "undeclared failure: X".
    A chunk whose newest run was not a refusal contributes nothing, so a later accepted run
    clears what an earlier refusal named."""
    logs = {(r.get("chunk"), r.get("pid")): r.get("log") for r in history or []
            if r.get("event") == "started"}
    last = {}
    for r in history or []:
        if r.get("event") == "finished" and r.get("kind", "production") == "production":
            last[r.get("chunk")] = r
    out = {}
    for chunk, r in last.items():
        if r.get("exit") != REFUSED_TO_START:
            continue
        log = logs.get((chunk, r.get("pid")))
        try:
            text = (REPO / log).read_text(encoding="utf-8", errors="replace") if log else ""
        except OSError as e:
            print(f"  NOTE: refusal log {log} unreadable ({e}); its suites cannot hold builds",
                  file=sys.stderr)
            continue
        for name in re.findall(r"undeclared failure: (test_\w+\.py)", text):
            out.setdefault(name, chunk)
    return out


def tracked_tests(root=None):
    """The set of `scripts/tests/*.py` git TRACKS at `root` (default: this repo's checkout).
    None when git cannot answer -- and None fails open everywhere: an unanswerable question is
    never evidence for a refusal (L-015: "not found" is not "does not exist")."""
    try:
        r = subprocess.run(["git", "-C", str(root or REPO), "ls-files", "scripts/tests"],
                           capture_output=True, text=True, timeout=60)
    except Exception:                                               # noqa: BLE001 -- no git: answer nothing
        return None
    if r.returncode != 0:
        return None
    return set(r.stdout.split())


def untracked_oracles(named, tracked):
    """{suite: chunk} -- the suites a refusal named that git does not track. Empty when `tracked`
    is None (git could not say, and a guess is not evidence)."""
    if tracked is None:
        return {}
    return {suite: chunk for suite, chunk in named.items()
            if f"scripts/tests/{suite}" not in tracked}


def untracked_target(target, tracked):
    """True when a chunk's TARGET is a suite file that is not tracked (D.2.2): the scoper wrote
    its oracle straight into the live checkout, so no sandbox baseline can match it. Only
    scripts/tests/*.py is an oracle; any other target is a file, not a suite."""
    if tracked is None:
        return False
    p = Path(str(target or "")).as_posix()
    name = p.rsplit("/", 1)[-1]
    return (p.startswith("scripts/tests/") and name.startswith("test_") and name.endswith(".py")
            and p not in tracked)


def main_red_hold(history, red_of):
    """A reason to start NO build this tick, or None (t650, Jas 2026-10-02: "do both").

    A SUITE RED ON MAIN REFUSES EVERY BUILD, NOT ONE. Every sandbox copies main, so a suite that
    is red there is in every baseline: on 2026-10-01 three stale suites refused 17 starts in two
    hours across chunks, then test_senior_station 3 more, then systemd 259 broke test_slot_events
    -- each refusal ~5 min of baseline to learn the same thing. So the suites a refusal named are
    checked ON MAIN (red_of, cached per code state) and while any is still red, nothing starts.
    One that cannot be run does not hold: unknown is not red, and a hold nobody can clear is worse
    than one wasted refusal."""
    # DECLARED SINCE IS NOT A BLOCKER (2026-10-07). The refusal named it as UNDECLARED; once it is in
    # the ceiling, every baseline allows it, yet this held all builds on it for hours
    # (test_session_review_crossref, declared in aa33e3ad after the refusal it caused).
    try:
        allowed = set(json.loads((REPO / "workspace" / "test-failures-ceiling.json")
                                 .read_text(encoding="utf-8")).get("allowed_to_fail", []))
    except (OSError, ValueError) as e:
        print(f"  NOTE: ceiling unreadable ({e}); every refusal still holds", file=sys.stderr)
        allowed = set()
    # ONLY A COMMITTED SUITE IS MAIN (2026-10-12). Six oracles a scoping model wrote straight into the
    # live checkout (untracked, never frozen, never on any branch) were "red on main" and held all
    # 193 builds; two of them had been "declared" that morning as if they were on main. An untracked
    # file is not main: it is reported (red_watchdog names it) but it cannot hold the line.
    # REPORTED, NOW WITH A CONSEQUENCE (D.3.1, 2026-10-22): the half that was missing is that an
    # untracked red suite must still produce WORK. On 2026-10-21 an untracked suite refused 19
    # builds across three hours and filed nothing; the refusals are deterministic, so re-dispatching
    # them costs ~5 min of sandbox baseline each to learn the same thing. Untracked suites never
    # enter `red` (the 2026-10-12 rule stands) -- they go to red_on_main.handle_untracked(), which
    # files one owner task per suite. classify() also refuses them AT DISPATCH, naming the file
    # (D.2.2), so the 19 refusals never happen.
    tracked = tracked_tests()
    named = refusal_blockers(history)
    untracked = untracked_oracles(named, tracked)
    red = {suite: chunk for suite, chunk in sorted(named.items())
           if suite not in allowed and f"scripts/tests/{suite}" in (tracked or set())
           and red_of(f"scripts/tests/{suite}") is True}
    # THE MANAGER FIXES IT FIRST, THEN THE LINE KEEPS MOVING (Jas, 2026-10-14; red_on_main.py). On the
    # live tick each red suite gets one front-of-queue task and a ledger row; after red_on_main.WINDOW_S
    # still red it is declared with that task as owner and stops holding. Tests call this without the
    # live flag, so they file nothing.
    holding = list(red)
    if RED_ACTIONS:
        try:
            import red_on_main
            holding = red_on_main.handle(red, is_red=lambda s: red_of(f"scripts/tests/{s}"))
            for suite, task in red_on_main.handle_untracked(untracked):
                print(f"  UNTRACKED SUITE: scripts/tests/{suite} -> owner task {task}", flush=True)
            # RESOLVE: a suite the scheduler filed as untracked that is no longer untracked (the
            # file is committed now, or deleted from the live checkout) closes the event.
            live = tracked_tests()
            for suite, ev in red_on_main.open_untracked().items():
                if live is not None and f"scripts/tests/{suite}" in live:
                    red_on_main.resolve_untracked(suite, ev.get("task"))
        except Exception as e:                                      # noqa: BLE001 -- said; it still holds
            print(f"  NOTE: red-on-main handling failed ({type(e).__name__}: {e}); holding as before",
                  file=sys.stderr)
    HELD_SUITES.clear()
    HELD_SUITES.update(holding)
    still = [f"{suite} (refused {red[suite]})" for suite in holding]
    if still:
        return ("HELD: red on main, so every sandbox baseline would refuse -- fix it first: "
                + ", ".join(still))
    return None


# Set by the live tick only (main(), not --dry-run): red_on_main files tasks and declares suites.
RED_ACTIONS = False
# The suites holding the line this tick: a chunk whose TARGET is one of them is the fix, not held.
HELD_SUITES = set()


def preflight_crashes(history, objections, sha):
    """How many preflight runs for this brief sha RAN and left no recorded objection.

    A REFUSED LAUNCH IS NOT A RUN (2026-10-28, RCA rca-no-verdict-preflight-holds). Six briefs were held
    as "the reviewer is broken" on rows that never had a process: a duplicate launch is written with pid 0,
    no exit and no duration, and the old test here read "no duration" as "ran at least five seconds", so
    refused launches accumulated into a verdict about the reviewer. Measured for one brief
    (p840-t1522-ask-q16): four rows, two of them pid 0 with exit None, and the message said two reviews
    ended without a verdict. The reviewer was never asked.

    One function so the arithmetic can be tested without a tick: these two lists are its only inputs.
    """
    ran = [r for r in (history or []) if r.get("event") == "finished"
           and r.get("kind") == "preflight" and r.get("brief_sha256") == sha
           and r.get("exit") not in YIELD_EXITS
           and (r.get("pid") or 0) != 0                  # no process: a refused launch, not a review
           # UNDER 5 SECONDS IS NOT A REVIEW. Before 8f23694 a held lease crashed the preflight (exit 1,
           # 0 s) instead of yielding (exit 4); those rows held nine briefs "for Claude" on 2026-09-26 as
           # if the reviewer were broken.
           and (r.get("seconds") is None or r.get("seconds") >= 5)]
    recorded = [o for o in (objections or []) if o.get("brief_sha256") == sha]
    return max(0, len(ran) - len(recorded))


def classify(chunks, running, red_of, history=None, objections=None):
    """Sort enrolled chunks into (need_review, ready, skipped). Shared by plan() and the priority layer."""
    # LOAD ONCE IF THE CALLER DID NOT. review_state() reads the ledger itself when given
    # None, so a None here was invisible until something ELSE in this function needed the
    # same rows -- the crash counter, which then saw zero recorded verdicts and called the
    # reviewer broken. Two readers of one argument, one of them handling None and one not.
    if objections is None:
        objections = []
        if OBJECTIONS.exists():
            for line in OBJECTIONS.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    objections.append(json.loads(line))
                except ValueError:
                    print(f"  NOTE: unreadable line in {OBJECTIONS} -- not counted", file=sys.stderr)
                    continue
    busy_briefs = {r.get("brief") for r in running}
    skipped, need_review, ready = [], [], []
    hold = main_red_hold(history, red_of)
    for c in chunks:
        if c["brief"] in busy_briefs:
            skipped.append((c["chunk"], "already running"))
            continue
        sha = brief_sha(c["brief"])
        if sha is None:
            skipped.append((c["chunk"], f"brief {c['brief']} does not exist"))
            continue
        # A CHUNK WHOSE TARGET SUITE IS UNTRACKED REFUSES IN EVERY SANDBOX (D.2.2, 2026-10-22).
        # coder.py copies untracked-not-ignored files into every sandbox (by design), so a suite a
        # scoper wrote straight into the live checkout lands in every baseline, every build refuses
        # to start, and the loop repeats: 2026-10-21, 19 refusals, 0 accepted, 0 tasks filed.
        # Refuse at dispatch with a reason that names the file and the sanctioned route; the owner
        # task is filed by red_on_main.handle_untracked (main_red_hold).
        if untracked_target(c.get("target"), tracked_tests()):
            skipped.append((c["chunk"], f"REFUSED: target {c.get('target')} is UNTRACKED in the live "
                                        "checkout -- it is not on main, so every sandbox baseline "
                                        "refuses it (2026-10-21: 19 builds, 0 tasks). Route it through "
                                        "the task's chunks block (scripts/bridge.py freezes it on a "
                                        "branch) or remove the file from the live checkout"))
            continue
        # A CLOSED TASK HAS NOTHING LEFT TO BUILD, and knowing that costs no suite run.
        closed = task_closed(c["brief"])
        if closed:
            skipped.append((c["chunk"], f"task {closed} is closed -- nothing to build"))
            continue
        # A REFUSAL TO START IS NOT RETRIED ON THE SAME CODE (2026-10-01). t469's build refused 17
        # times in two hours on one undeclared red suite -- each refusal ~5 min of sandbox baseline
        # for an answer that could not change until the code did. Re-dispatch once code changes.
        if hold and Path(c.get("target") or "").name not in HELD_SUITES:
            skipped.append((c["chunk"], hold))
            continue
        stuck = refused_on(c["chunk"], history)
        if stuck and stuck == code_state():
            skipped.append((c["chunk"], "refused to start on this exact code (a red suite in the sandbox "
                                        "baseline); waits for a code change -- see its last job log"))
            continue
        # BEFORE red_of, which runs the target suite: a chunk whose work is already on a
        # review branch needs no suite run to know it is not waiting on a model.
        waiting = delivered_awaiting_review(sha)
        if waiting:
            skipped.append((c["chunk"], "DELIVERED, waiting on Claude's review: "
                                        + ", ".join(waiting)))
            continue
        red = red_of(c["target"])
        if red is None:
            skipped.append((c["chunk"], f"target {c['target']} could not be run"))
            continue
        if not red:
            skipped.append((c["chunk"], "target green -- already on main"))
            continue
        # NO TASK, NO BUILD (Jas, 2026-09-25: "everything should have a task associated with
        # it"). 28 enrolled chunks had none; a build nobody can trace to a task cannot show on
        # the Status board, feed a task session, or be closed against anything.
        if not re.search(r"^\s*TASK:\s*t\d+", (REPO / "workspace" / "briefs" / c["brief"])
                         .read_text(encoding="utf-8", errors="replace"), re.M):
            skipped.append((c["chunk"], "NO TASK: the brief names no task -- add a TASK: line"))
            continue
        # PARKED IS CHECKED LAST OF THE THREE, AND THE ORDER IS THE POINT (2026-09-16).
        # It used to run first, so a chunk that had been parked once stayed PARKED in the
        # report for ever -- even after its work was delivered, reviewed and MERGED. Found
        # live: p26-comply-bench2 and p27-comply-bench3 were both still reported PARKED with
        # "1 attempt came to nothing" while their target suites were green on main, because
        # the park was checked before anything asked whether the job was already done.
        #
        # A park is a claim that this task is GETTING NOWHERE. A green target is proof it got
        # somewhere. When the two disagree the evidence wins, so the cheap questions -- is the
        # work already delivered, is the target already green -- are asked first, and only a
        # chunk that is genuinely unfinished can be reported as held.
        is_parked, why = parked(c["chunk"], sha, history)
        if is_parked:
            skipped.append((c["chunk"], f"PARKED: {why}"))
            continue
        # The bundle the reviewer WOULD get for this chunk today. Computed here because
        # review_state is keyed on a brief sha and has no idea what target the chunk names.
        # Failure is not fatal: deps_now stays None and the dated cutoff decides, which is
        # the behaviour before this comparison existed.
        deps_now = None
        try:
            import preflight_brief as _pb
            if c.get("target"):
                _bt = (REPO / "workspace" / "briefs" / c["brief"]).read_text(encoding="utf-8", errors="replace")
                _d, _, _r = _pb.evidence(c["target"], (c.get("files") or "").split(), _bt)
                deps_now = [*_d, *_r]
        except Exception:                                           # noqa: BLE001
            deps_now = None
        state = review_state(sha, objections, deps_now=deps_now)
        # A RUN THAT RECORDED A VERDICT DID NOT CRASH, even if that verdict has since been
        # expired (2026-09-16). This counted every finished preflight run for the brief and
        # compared it against state == "unreviewed" -- which is also what a verdict looks
        # like AFTER review_state retires it because the evidence bundle changed. So the
        # moment twelve stale verdicts were correctly expired, twelve chunks flipped straight
        # from "Q16 objected" to "the reviewer is broken" and stayed held on a second wrong
        # reason. The reviewer was not broken; it had answered, and the answer had aged out.
        # Subtract the rows it actually wrote: a crash is a run that finished leaving none.
        # A preflight that YIELDED (exit 4: the manager model's lease was held) did not
        # review anything, so it is not a review that "ended without a verdict". Counting it
        # held five briefs overnight 2026-09-23/24 while ask_q16 held the lease (t387).
        crashed = preflight_crashes(history, objections, sha)
        if state == "unreviewed" and crashed >= 2:
            skipped.append((c["chunk"], f"HELD FOR CLAUDE: {crashed} reviews of this brief ended "
                                        "without recording a verdict -- the reviewer is broken"))
            continue
        if state == "objected":
            skipped.append((c["chunk"], f"HELD FOR CLAUDE: {task_routing.role_name('preflight')} objected to this brief "
                                        "(workspace/preflight-objections.jsonl)"))
            continue
        c = {**c, "brief_sha256": sha}
        (need_review if state == "unreviewed" else ready).append(c)

    # HARNESS FIRST (Jas, 2026-10-19: "When a harness bug goes into production it needs to prioritize
    # over the other tasks"). A stable sort: chunk order is kept within each group.
    ready.sort(key=lambda c: not is_harness_brief(c["brief"]))
    need_review.sort(key=lambda c: not is_harness_brief(c["brief"]))
    return need_review, ready, skipped


def is_harness_brief(brief_name):
    """True when the brief's task is a HARNESS task (the supervisor's title prefix), by file name."""
    tid = task_of(brief_name)
    return bool(tid) and next((REPO / "workspace" / "tasks").glob(f"{tid}--harness-*.md"), None) is not None


def delivered_awaiting_review(sha):
    """Review branches already delivered for this brief and still awaiting a verdict.

    WHY THIS EXISTS (2026-09-16). plan() skipped a chunk only when its target was green ON
    MAIN, and a delivered branch does not touch main -- that is the whole point of a review
    branch. So a chunk that had already produced a correct patch stayed eligible and was
    dispatched again on the next free lane, and again after that. Measured the same day:
    p26-comply-bench2-no-timeout delivered FOUR branches (ff5accbe, -2, -3, -4) for one
    one-line change, and p27 delivered two. ~80,000 tokens and four sandbox builds to
    re-derive `timeout=600`.

    coder.py was not at fault and its guard is what made the waste visible: write_branch
    refuses to clobber a branch a human has not seen and writes beside it instead, which is
    where the -2/-3/-4 suffixes come from. It was doing the right thing with work that
    should never have been asked for twice.

    This is the "ACCEPTED is not merged" gap the 2026-09-16 committee named: the pipeline
    had no state between "delivered" and "green on main", so the queue could not tell work
    that was FINISHED from work that had not started. Such a chunk is waiting on Claude, not
    on a model, and dispatching it again cannot make it finish sooner.

    reviewq owns the question "has this been seen" -- read from it rather than reimplement
    it, for the same reason coder.branch_is_pending does (L-114, L-115).

    FAILS TOWARD DISPATCHING, which is the opposite of coder.branch_is_pending and
    deliberate. There, an unreadable queue must not cause committed work to be deleted, so
    it fails toward keeping. Here, an unreadable queue must not silently halt the entire
    production line, so it fails toward running: a duplicate dispatch is waste and
    recoverable, a scheduler that stops on a bad read is an outage.
    """
    if not sha:
        return []
    try:
        import reviewq
        stem = f"coder/brief-{sha[:8]}"
        return sorted(r["branch"] for r in reviewq.pending()
                      if r.get("branch") and (r["branch"] == stem
                                              or r["branch"].startswith(stem + "-")))
    except Exception:                                               # noqa: BLE001
        return []


CHAT_MODEL = roles.tag("manager", "qwen3.8:27b-mtp-bf16")   # kept for readers; see console_models


def console_models(d=None):
    """The models of the roles with Console on their Models-tab list, in store order, read each tick.

    WHO ANSWERS THE CONSOLE IS THE MODELS TAB'S (2026-10-02). It was CHAT_MODEL -- the Manager's tag,
    read once at import -- and then ANY free production model, while the tab gave Console to the
    Senior alone. No role with Console: no console job starts, and the tick says so."""
    d = d or task_routing.load()
    out = []
    for r in task_routing.owners(d, "console"):
        tag = ((d.get("roles") or {}).get(r) or {}).get("model")
        if tag and tag not in out:
            out.append(tag)
    return out


def assign_console(messages, free_models, running, already_started, owners=None, slots=None,
                   interrupt=False, can_checkpoint=False, preempt=None):
    """Pure (given owners). Oldest console messages onto models with a FREE LANE -> (starts, models
    left for chunks).

    Only the models of the roles with Console on their list (console_models), first-listed first.
    A FREE LANE, NOT AN IDLE MODEL (2026-10-12): `busy` used to be "any job on the model", so with 4
    lanes a chat message started only when all four were empty -- in practice it waited behind the
    line. And WHEN EVERY LANE IS FULL and the Console's Interrupt box is on (Jas, 2026-10-12), the
    running job LOWEST in its role's list (task_routing.position) that can afford it -- a build that
    checkpoints, or anything under CHEAP_INTERRUPT_S -- is appended to `preempt` and the message
    takes its lane. With slots=None every model has one lane: the old rule exactly.
    """
    owners = console_models() if owners is None else owners
    slots = slots or (lambda m: 1)
    used = {}
    for r in list(running) + list(already_started):
        used[r["model"]] = used.get(r["model"], 0) + 1
    preempt = preempt if preempt is not None else []
    starts = []
    for msg in messages:
        pick = None
        for m in sorted([m for m in free_models if m in owners], key=owners.index):
            if used.get(m, 0) < slots(m) and fits(m, {x for x in used if x != m}):
                pick = m
                break
        if pick is None and interrupt:
            def _rank(r):
                k = r.get("kind", "production")
                role = r.get("role") or task_routing.owner(ROUTING, k)
                return task_routing.position(ROUTING, role, k) if role else 99
            victims = sorted([r for r in running if r.get("model") in owners and r.get("pid")
                              and r.get("kind") != "console" and r["pid"] not in preempt
                              and ((can_checkpoint and r.get("kind", "production") == "production")
                                   or (r.get("elapsed_s") or 0) < CHEAP_INTERRUPT_S)],
                             key=lambda r: (-_rank(r), r.get("elapsed_s") or 0))
            if victims:
                preempt.append(victims[0]["pid"])
                pick = victims[0]["model"]
        if pick is None:
            break
        used[pick] = used.get(pick, 0) + 1
        starts.append({"kind": "console", "key": msg["id"], "model": pick, "chunk": f"console:{msg['tid']}",
                       "brief": None, "brief_sha256": None})
    return starts, [m for m in free_models if m not in {s["model"] for s in starts}]


# A BUILD NEEDS FREE MEMORY, NOT ONLY A FREE LANE (2026-10-10). Lanes were the only gate: on
# 2026-10-10 the memory guard killed Swift twice (08:08Z, 15:14Z) -- Swift 91-96 GB, the embedder
# 4-5 GB, and builds with their test suites 2-7 GB, started into memory nobody had checked.
# memory-watch.jsonl shows each rise. A build now starts only while /proc's MemAvailable covers
# its measured peak (BUILD_GIB, the largest seen beside Swift that day) plus the memory guard's
# reserve and a margin; each start in the same tick is charged against what is left.
BUILD_GIB = 12.0

_CHARGE = {}


def build_charge_gib():
    """(gib, why) -- the host bytes ONE build is charged, everywhere at once.

    R3, FROM THE MEASURED PEAKS (2026-10-29): BUILD_GIB is 12.0 and the admission adds 4 GB of margin, so a build
    demanded 16 GB free. Measured over 56,001 recorded `job:build` peaks, the distribution is p50 2.99, p90 6.79, p95
    8.06, p99 11.19 GB -- so 12.0 sits ABOVE the 99th percentile and the box was spending four gigabytes of a
    six-gigabyte slack on a shape builds do not have.

    ONE FUNCTION BECAUSE THE NUMBER APPEARS IN FIVE PLACES (the booking, memory_admit's loop, the gate's need and the
    tick's check). If they ever disagreed, the ledger would book one figure and the gate would refuse on another --
    exactly the two-sources-of-truth fault this redesign exists to remove. When the admission layer is off, this
    returns the constant unchanged.
    """
    # A0, REVERTED TO THE CONSTANT (2026-10-30, and the brief asked for exactly this). THE LAYER STAYS ON; only the
    # CHARGE goes back. The measured figure came from capacity_model.sized_build_gib(), whose inputs were found
    # today to be neither peaks nor reliably build memory: those rows are 20-SECOND SAMPLES of the `job:build`
    # group (56,826 of them against 1,051 builds ever started, each the SUM of concurrent work -- A5), and the
    # group itself counts any process whose cmdline holds coder.py, so the coder SUITES feed it (A5 follow-up:
    # measured 1.26 GB across 11 processes, every one a non-dispatched clone). Until per-build peaks exist,
    # capacity_model.per_build_peaks() is how the number gets re-derived -- and 12 GB is what a build is charged
    # until then.
    return (float(BUILD_GIB), f"the {BUILD_GIB:g} GB constant (the measured charge is NOT in force: its inputs are "
                              f"20-second samples of a group that also counts test clones, so it is neither a "
                              f"per-build peak nor reliably build memory -- see capacity_model.per_build_peaks)")
# 6.0 -> 12.0 (2026-10-12, measured): memory-watch's job:build group peaked at 10.39 GB (08:16:58Z) and
# 11.74 GB (19:14:38Z, the build admitted at 19:13 with 6 GB charged -- the memory guard killed Swift).
ADMIT_MARGIN_GIB = 4.0                       # the guard's 2 GB reserve + 2 GB for Swift's own growth


RED_STATE = REPO / "workspace" / "run-logs" / "red-on-main.json"


def red_watchdog(skipped, send=None, state=None):
    """Push-notify Jas once when "red on main" starts holding builds, and once when it clears
    (2026-10-12). Twice the hold stopped the whole line for most of a day -- a66b9859, then t1024's
    oracles from 2026-10-11 -- and both were found by someone reading the Hub. Returns the red suites."""
    state = Path(state or RED_STATE)
    held = sorted({w.split("fix it first: ", 1)[-1].split(" (refused")[0]
                   for _, w in skipped if str(w).startswith("HELD: red on main")})
    try:
        was = json.loads(state.read_text(encoding="utf-8")) if state.exists() else []
    except (OSError, ValueError):
        was = []
    if held != was:
        if send is None:
            import notify
            send = notify.send
        n = sum(1 for _, w in skipped if str(w).startswith("HELD: red on main"))
        if held:
            send("red-on-main", "Builds held: a test is red on main",
                 f"{n} build(s) waiting. Red: {'; '.join(held)}", url="/production")
        else:
            send("red-on-main", "Main is green again", "builds are starting", url="/production")
        state.write_text(json.dumps(held) + "\n", encoding="utf-8")
    return held


def mem_available_gib(meminfo="/proc/meminfo"):
    for line in Path(meminfo).read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1048576
    return None


def swift_pending_gib(watch=None, now=None):
    """GB Swift WILL take that it does not hold yet: its expected peak minus what memory-watch saw.

    THE RACE (2026-10-12, and 2026-10-10 twice). MemAvailable while Swift is loading -- or still
    growing its KV pool under load -- counts memory Swift is about to take. Builds admitted into
    it then meet Swift growing into the same space: 18:39:56Z (Swift 83 -> 91.4 GB after start,
    a build 2.7 -> 8.1 GB) and two builds admitted at ~18:57Z mid-load, leaving 3.3 GB free.
    0 when Swift is not running or starting; the expected peak is serve.need_gib() less its
    reserve (the measured footprint, else serve's conservative fallback)."""
    try:
        state = subprocess.run(["systemctl", "--user", "is-active", "swift.service"],
                               capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return 0.0
    if state not in ("active", "activating"):
        return 0.0
    try:
        sys.path.insert(0, str(REPO / "projects" / "local-inference"))
        import serve
        peak = serve.need_gib() - serve.RESERVE_GIB
    except Exception as e:                                          # noqa: BLE001 -- said
        print(f"  NOTE: Swift's footprint unread ({type(e).__name__}: {e}); no growth charged")
        return 0.0
    held = 0.0
    try:
        # Tail read (t1508): memory-watch.jsonl is ~180 MB and grows all day; read_text() decoded
        # it all to use one ~4 KB row, every tick. Rows are ~4 KB, so the last 64 KiB always hold
        # the final row whole; a torn line at the tail's head is skipped, never parsed. Same number
        # back, not the bytes read.
        with open(watch or (REPO / "workspace" / "memory-watch.jsonl"), "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 65536), 0)
            tail = f.read()
        lines = [l for l in tail.splitlines() if l.strip()]
        if not lines:
            raise IndexError("no non-empty line in the tail")
        r = json.loads(lines[-1].decode("utf-8"))
        import calendar
        age = (now or time.time()) - calendar.timegm(time.strptime(r["at"], "%Y-%m-%dT%H:%M:%SZ"))
        if age <= 120:
            held = float((r.get("groups") or {}).get("swift", {}).get("total_gb") or 0.0)
        else:
            print(f"  NOTE: memory-watch is {age:.0f}s stale; charging Swift's whole footprint")
    except (OSError, ValueError, KeyError, IndexError) as e:
        print(f"  NOTE: memory-watch unread ({type(e).__name__}); charging Swift's whole footprint")
    return max(0.0, peak - held)


def device_hold(p_starts, console_starts, starts, queued, skipped, paused=None):
    """(p_starts, console_starts, starts) less every job whose model lives on a PAUSED DEVICE, each said.

    Jas, 2026-10-13: the GX10's memory guard must not stop the ThinkPad. The guard pauses the GX10
    alone (scheduler_commands.gx10_off); this keeps the ThinkPad's script reviews and RCAs starting
    while Swift is down. Priority and console items go back to `queued`, builds to `skipped`."""
    if paused is None:
        import scheduler_commands
        paused = scheduler_commands.paused_devices()
    if not paused:
        return p_starts, console_starts, starts

    def why(dev):
        n = paused[dev]
        return f"{dev} is paused ({n.get('who')}: {n.get('why')}) -- Resume on the Hub"
    keep = []
    for group in (p_starts, console_starts):
        k = []
        for j in group:
            dev = DEVICE_OF.get(j.get("model"), "gx10")
            (queued.append((j, why(dev))) if dev in paused else k.append(j))
        keep.append(k)
    b = []
    for j in starts:
        dev = DEVICE_OF.get(j.get("model"), "gx10")
        (skipped.append((j["chunk"], why(dev))) if dev in paused else b.append(j))
    return keep[0], keep[1], b


# PRESSURE DECIDES WHETHER A NEW BUILD STARTS (Jas, 2026-10-20: "If we just keep killing processes and not
# dialing down the lanes ... we are just wasting the processes" -- and "I want to maximize" tokens/s).
# Measured, 17,738 Swift calls since 10-08 against the session's memory pressure during each: total Swift
# tok/s across all lanes, median per 5-min window, 59.9 under 5% pressure, 47.2 at 5-15, 40.4 at 15-30,
# 35.9 at 30-45, 28.1 at 45-60, 19.6 at 60-75. So no new build starts while the one-minute pressure is at
# or over PRESSURE_HOLD_ON, until it falls under PRESSURE_HOLD_OFF; running builds finish. The memory
# guard (80 / 60 since 2026-10-23) stays the brake for what is already running.
# 30/20, not 15/10 (Jas, 2026-10-23: "Raise to 30/20"). The one-minute pressure sat at 25-57% all day, so
# at 15 no build started; at 30-45 Swift still measured ~36 tok/s against ~60 under 5%.
PRESSURE_HOLD_ON = float(os.environ.get("DISPATCH_PRESSURE_HOLD_ON", "30"))
PRESSURE_HOLD_OFF = float(os.environ.get("DISPATCH_PRESSURE_HOLD_OFF", "20"))
# How long a tick waits for a pressure dip before giving up and starting no build (2026-10-29).
PATIENCE_S = int(os.environ.get("DISPATCH_PRESSURE_PATIENCE_S", "60"))
PRESSURE_HOLD_STATE = REPO / "workspace" / "run-logs" / "pressure-hold.json"


def wait_for_pressure_release(seconds=60, step=5, read=None, sleep=None, cause=None):
    """Wait up to `seconds` for the pressure hold to release, and say whether it did.

    Jas, 2026-10-29: "if its eratic, can we raise the pressure threshold because it would add another lane and
    high pressure is momentary and overall throughput will go up?" -- the instinct is right about the DEFECT
    and wrong about the fix. Measured on this box: the hold released at 01:32:05Z when avg60 fell to 8.17%,
    and by 01:35Z it was back at 49%, so the dip existed but lasted less than one tick and NO BUILD was
    admitted into it. Raising the threshold would not capture that dip; it would admit builds at 60%, where
    the same experiment measured 4 lanes at 15.15 tok/s against 2 lanes at 28.41 -- more work, less throughput.

    So the hold stays where it is and the tick waits for the dip instead: when the hold is the only thing
    stopping builds and there are builds ready, it re-reads pressure a few times over a minute. Bounded, and
    the wait is reported so a reader can see it happened.

    `read` returns the hold reason or None (pressure_hold by default); `sleep` is injectable for the test.
    """
    read = read or pressure_hold
    sleep = sleep or time.sleep
    deadline = max(0, int(seconds))
    waited = 0
    while waited < deadline:
        held = read()
        if not held:
            return True, waited
        sleep(step)
        waited += step
    return (read() is None), waited


def admission_gate_on(env=None):
    """True when the BYTE admission gate (redesign step 2) is switched on. It decides admission; pressure then
    only annotates."""
    try:
        import capacity_model as _cm
        return bool(_cm.gate_switch_on(env))
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: the admission gate's switch is unreadable ({type(e).__name__}: {e}); pressure keeps "
              f"gating", file=sys.stderr)
        return False


def pressure_build_gate(build_ready, held, patience=None, wait=None):
    """(ready, hold, waited) -- whether this tick's READY BUILDS survive a memory-pressure hold.

    THIS EXISTS BECAUSE THE FIRST VERSION OF THIS FIX WAS WRONG IN A WAY ITS OWN TEST COULD NOT SEE (L1 review,
    2026-10-29): the wait ran, saw the pressure drop, cleared the hold -- and the very next line emptied
    `build_ready` anyway, so no build could start and the wait bought a minute of nothing. The test called
    wait_for_pressure_release() on its own, so it passed while the feature did not work.

    The decision is therefore a pure function returning BOTH the surviving builds and the hold, so a test asserts
    the outcome the tick will act on:
      * no hold            -> the builds survive untouched;
      * a hold, no builds  -> nothing to save, the hold stands;
      * a hold, builds, and the dip RELEASES -> the builds SURVIVE and the hold clears;
      * a hold that never releases -> the builds are dropped and the hold stands.
    """
    if not held:
        return build_ready, None, 0
    if not build_ready:
        return build_ready, str(held), 0
    wait = wait or wait_for_pressure_release
    released, waited = wait(PATIENCE_S if patience is None else patience)
    if released:
        return build_ready, None, waited
    return {}, str(held), waited


def pressure_hold(avg60=None, state=None):
    """A reason to start no build now, or None. Hysteresis: on at PRESSURE_HOLD_ON, off under
    PRESSURE_HOLD_OFF, the state kept in PRESSURE_HOLD_STATE between ticks. Unreadable pressure holds nothing."""
    state = Path(state or PRESSURE_HOLD_STATE)
    if avg60 is None:
        import memory_guard
        avg60 = memory_guard.pressure(window="avg60")
    if avg60 is None:
        return None
    try:
        held = json.loads(state.read_text(encoding="utf-8")).get("held", False) if state.exists() else False
    except (OSError, ValueError):
        held = False
    now_held = avg60 >= PRESSURE_HOLD_ON or (held and avg60 >= PRESSURE_HOLD_OFF)
    if now_held != held:
        if state.parent.is_dir():
            state.write_text(json.dumps({"held": now_held, "avg60": avg60, "at": now()}) + "\n", encoding="utf-8")
        else:
            print(f"  NOTE: {state.parent} is missing -- pressure state not recorded; a state "
                  f"directory is created by boot or a migration, never by a writer (mf4)",
                  file=sys.stderr)
    if not now_held:
        return None
    return (f"waiting for memory pressure: {avg60:.0f}% over the last minute -- no new build starts at or "
            f"over {PRESSURE_HOLD_ON:g}% until it falls under {PRESSURE_HOLD_OFF:g}% (Swift's tok/s falls with it)")


def memory_admit(starts, skipped, avail=None, pending=None):
    """(starts, skipped): production starts beyond what free memory can hold are skipped, said.
    `pending` is memory a starting or growing Swift will still take (swift_pending_gib); read live
    only when `avail` is too, so a caller that passes avail gets a pure decision."""
    live = avail is None                 # a caller that passes avail gets a pure decision: no live pressure either
    if avail is None:
        avail = mem_available_gib()
        if avail is not None and pending is None:
            pending = swift_pending_gib()
    if avail is None:
        return starts, skipped
    if pending:
        print(f"  memory: {avail:.1f} GB free, {pending:.1f} GB of it is Swift's still to come")
        avail -= pending
    kept, left = [], avail
    # THE SECOND PRESSURE LATCH, and it was MINE TO MISS (2026-10-29). R4 demoted pressure to a warning in the
    # TICK's decision, but memory_admit() asks pressure_hold() for itself -- so a build could be planned, admitted
    # by its bytes, and then thrown away HERE by the same stall metric. Measured on this box: with 21.1 GB free
    # against a 12.1 GB need, this line refused a start with "waiting for memory pressure: 39% over the last
    # minute", while the same call with the figure supplied kept it. One switch, one meaning: when the admission
    # layer is on, pressure is a warning here too, and the BYTES decide.
    _bytes_gate = admission_gate_on()
    if live and _bytes_gate and any(j.get("kind", "production") == "production" for j in starts):
        _ph = pressure_hold()
        if _ph:
            print(f"  PRESSURE  {_ph} -- annotated, not refused: the byte gate decides admission")
    held = (None if _bytes_gate else pressure_hold()) \
        if live and any(j.get("kind", "production") == "production" for j in starts) else None
    for j in starts:
        if j.get("kind", "production") == "production":
            if held:
                skipped.append((j["chunk"], held))
                continue
            if left - build_charge_gib()[0] < ADMIT_MARGIN_GIB:
                skipped.append((j["chunk"], f"waiting for memory: {left:.1f} GB free, a build needs "
                                            f"{build_charge_gib()[0]:g} + {ADMIT_MARGIN_GIB:g} GB"))
                continue
            left -= build_charge_gib()[0]
        kept.append(j)
    return kept, skipped


def build_role(model, d=None):
    """The role a build on `model` belongs to: a role serving that model whose list HAS Building (the
    highest on its list first). None when no role does -- no fallback (Jas, 2026-10-18: "I want the
    scheduler to match the assignments of the Roles tab"). BUILDS CARRIED NO ROLE until 2026-10-18,
    so the Hub showed the Auditor's Q4 build under the Manager. The first version tested
    position() < UNRANKED, but position() returns the list's LENGTH for a missing task, so the
    Senior's empty list read as "Building first" and Swift builds were labelled Senior."""
    d = d or ROUTING
    try:
        task = task_routing.KIND_TASK.get(task_routing.BUILD, task_routing.BUILD)
        mine = [k for k in task_routing.model_roles(d) if d["roles"][k].get("model") == model
                and any(t.get("task") == task for t in task_routing.tasks_of(d, k))]
        return min(mine, key=lambda k: task_routing.position(d, k, task_routing.BUILD)) if mine else None
    except Exception as e:                                          # noqa: BLE001 -- said; the build still starts
        print(f"  NOTE: role for a build on {model} unknown ({type(e).__name__}: {e})")
        return None


# The skip reason when no lane is free for a build (t9537). Readers match on this prefix.
# THE REASON NAMES A RESOURCE, NOT A LANE (redesign step 4, 2026-10-29). "Waiting for a free lane" was a phrase
# that could not be checked: a "lane" is an integer in the role store, not a thing the box measures, and on
# 2026-10-28 it was printed 169 times in a tick whose real constraint was host memory. The prefix is kept stable
# because name_build_hold() relabels anything starting with it when a hold is the actual cause.
SLOT_WAIT = "no free sequence slot"
LEGACY_LANE_WAIT = "waiting for a free lane"      # matched, never written: ticks from before step 4 say this


def name_build_hold(skipped, hold):
    """A build skipped for a lane while the tick held EVERY build back says the real reason (2026-10-28).

    When memory or pressure withholds the build lane, Building never enters the rotation, so plan()
    finds no free model and says "waiting for a free lane" -- while lanes sat idle. Tick 22:47Z: 168
    builds read as a lane shortage with 1 job running; the cause was 16.6 GB free against a 12 + 4 GB
    admission. A reason that names the wrong constraint sends the reader to the wrong fix."""
    if not hold:
        return skipped
    return [(n, f"waiting for memory: {hold}"
             if str(w).startswith((SLOT_WAIT, LEGACY_LANE_WAIT)) else w) for n, w in skipped]
BUILD_TASK = "building"


def plan(models, chunks, running, red_of, history=None, objections=None, reviewer=REVIEWER,
         slots=None):
    """Pure decision. Returns (starts, skipped, idle). A start has kind 'preflight' or 'production'.

    ORDER OF WORK, Jas 2026-09-14: "Q16 goes through everything it can schedule before it moves
    to the production line. Then works next to the other 2 until everything is done."

    A LANE IS A FREE SLOT, NOT AN IDLE MODEL (2026-10-06). A model counted as busy while ANY job ran
    on it, so with every role on Swift the manager queue -- which never stops -- held the only model
    and no build could ever start ("waiting -- every model has a lane" with one job running and 4
    slots). A model now takes a build when it has a free slot (`slots`, passed in by the tick like
    schedule()'s) and is not already building: one build per model still holds. With the default of
    1 slot this is exactly the old rule.
    """
    slots = slots or (lambda m: 1)
    busy_models = {r["model"] for r in running}
    _on = {}
    for r in running:
        _on[r["model"]] = _on.get(r["model"], 0) + 1
    # BUILDS UP TO BUILDING'S MAX LANES PER MODEL (2026-10-08; was one build per model). `free`
    # holds a model once per build it may still start: its free slots, capped by Building's Max
    # lanes (not set = 1, the old rule; 0 = no limit) minus the builds it already runs.
    _nbuild = {}
    for r in running:
        if r.get("kind", "production") == "production":
            _nbuild[r["model"]] = _nbuild.get(r["model"], 0) + 1
    _cap = task_routing.max_lanes(ROUTING, "production")
    free = []
    for m in models:
        room = slots(m) - _on.get(m, 0)
        if _cap:
            room = min(room, _cap - _nbuild.get(m, 0))
        free += [m] * max(0, room)
    need_review, ready, skipped = classify(chunks, running, red_of, history, objections)
    starts = []

    # PRODUCTION KEEPS A LANE (t9530, 2026-10-28). The review pass could spend EVERY lane: with a review
    # backlog `free` emptied here, production was offered nothing, and 161 ready chunks were skipped as
    # "every model has a lane" in a tick that reported running: 0. Nothing was running -- the lanes had
    # been given to reviews that had not started either, and production built nothing for an hour while
    # the Hub showed "154 ready to build". A review backlog must delay REVIEWS (visible, on the
    # reviewer's queue); it must not starve the line (invisible, everyone's problem).
    reserve = 1 if ready else 0
    # Q16 REVIEWS FIRST: while anything is unreviewed, the reviewer does reviews, not production -- but
    # never the last lane while a build is waiting for one.
    for c in need_review:
        if reviewer in free and len(free) > reserve:
            starts.append({**c, "model": reviewer, "kind": "preflight"})
            free.remove(reviewer)
        else:
            why = (f"waiting for {task_routing.role_name('preflight')} {REVIEW_WAIT_TAIL}"
                   + (" (one lane is held for production, t9530)" if reviewer in free else ""))
            skipped.append((c["chunk"], why))
    if need_review and reviewer in models and reviewer not in free:
        pass                                    # reviewer is busy or just took a review
    # PRODUCTION: only cleared briefs; a model that failed a brief does not get it again.
    for c in ready:
        failed = failures(c["chunk"], c["brief_sha256"], history)
        resume_on = checkpoint_model(c["brief"])
        if resume_on:
            # IT WAS IN THE MIDDLE OF SOMETHING: it resumes on its own model, not rerouted.
            pick = resume_on if resume_on in free else None
            if pick is None:
                skipped.append((c["chunk"], f"waiting to resume on {resume_on} from its checkpoint"))
                continue
        else:
            pick = next((m for m in free if m not in failed), None)
        if pick is not None:
            busy = busy_models | {x["model"] for x in starts}
            if not fits(pick, busy):
                alt = next((m for m in free if m not in failed and fits(m, busy)), None)
                if alt is None:
                    skipped.append((c["chunk"], f"waiting for memory beside {sorted(busy)}"))
                    continue
                pick = alt
        if pick is None:
            # THE REASON NAMES THE ROLE AND THE RESPONSIBILITY (t9537), never "every model has a lane":
            # that sentence was read as a lane shortage while nothing ran. The role is looked up, so a
            # renamed or replaced role is named correctly with no edit here.
            # NAME THE RESOURCE (step 4): the jobs holding lanes and the slots they hold, both counted from this
            # plan's own inputs, so a reader can check the sentence against the same numbers the planner used.
            _role = task_routing.role_name(BUILD_TASK, ROUTING)
            _on = len(running) + len(starts)
            _slots = sum(int(slots(m) or 0) for m in models) if models else 0
            # NAME THE RESOURCE THAT IS ACTUALLY SHORT (2026-10-29, correcting my own step-4 wording). When `free`
            # is empty the shortage is a MODEL, not a slot: free is the list of models this plan may still hand out,
            # and `free.remove(pick)` empties it after the FIRST start -- so with one build model, plan() admits one
            # build per tick and tells the other 162 "no free sequence slot", which is false and, worse, sends a
            # reader to the slot count. MEASURED: planning the same 176 ready chunks with slots=2 and with slots=4
            # gave IDENTICAL results (starts=1, 162 'no slot'), which is what proved the slot count was not the
            # constraint. The slot sentence now appears only when a model IS free and its slots are the shortage.
            if not free:
                _why = (f"no free model for this build: {_role} ({BUILD_TASK.capitalize()}) has "
                        f"{len(models) or 0} model(s), all taken by {_on} job(s) or failed for this chunk"
                        f" (one build per model per tick -- plan() removes a model once it has one)")
            elif _slots and _on >= _slots:
                _why = (f"{SLOT_WAIT} on {_role} ({BUILD_TASK.capitalize()}): {_on} job(s) hold all "
                        f"{_slots} slot(s) and a build needs a whole one")
            else:
                _why = "every model that could take it has already failed it"
            skipped.append((c["chunk"], _why))
            continue
        free.remove(pick)
        job = {**c, "model": pick, "kind": "production", "role": build_role(pick)}
        if failed:
            job["switch_from"] = failed[-1]
            job["switch_number"] = len(failed)
        starts.append(job)
    return starts, skipped, free


def _preflight_defect(c):
    """A refused preflight is a defect in the scope that wrote the brief: record it once (the defect
    file is keyed by content) so scope_quality.route sends it to the Manager's queue."""
    import scope_quality
    scope_quality.record("preflight", "refused", chunk=c["chunk"], defect_class="brief-unsatisfiable",
                         evidence=f"preflight refused brief {c.get('brief')} (sha {str(c.get('brief_sha256'))[:12]}); "
                                  f"see the preflight log under workspace/run-logs/dispatch/",
                         source="dispatch_loop senior_slot")


SENIOR_TODO = []                    # [(kind, key)]: the senior's queue as senior_slot last sorted it


def senior_kinds(d=None):
    """The senior's task list as dispatcher kinds, in its order (the role store)."""
    return [task_routing.TASK_KIND.get(t["task"], t["task"]) for t in task_routing.tasks_of(d or ROUTING, "senior")]


# What an idle senior takes after its preflights: the rest of its list. Was a literal
# ("session-review", "rca") until the store's list decided it (plan step 4).
SENIOR_HELPS = tuple(k for k in senior_kinds() if k != "preflight")


RED_CACHE = REPO / "workspace" / "run-logs" / "red-cache.json"


def code_state():
    """The committed CODE plus a hash of every uncommitted change: identical code, identical key.

    TREES, NOT HEAD (t650, 2026-10-02). HEAD moves on every commit, and most commits here touch
    only workspace/ logs -- so "refused on this exact code" expired within minutes and the same
    refusal was re-run. The tree hashes of scripts/ and projects/ move only when code does."""
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD:scripts", "HEAD:projects"],
                              capture_output=True, text=True, cwd=str(REPO),
                              timeout=30).stdout.split()
        head = "+".join(x[:12] for x in head)
        diff = subprocess.run(["git", "diff", "HEAD", "--", "scripts", "projects"],
                              capture_output=True, cwd=str(REPO), timeout=60).stdout
        new = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "--",
                              "scripts", "projects"], capture_output=True, cwd=str(REPO),
                             timeout=60).stdout
    except (OSError, subprocess.SubprocessError) as e:
        print(f"  NOTE: code state unreadable ({e}); every target suite will run")
        return None
    return head + ":" + hashlib.sha256(diff + b"\0" + new).hexdigest()[:16]


def cached_red(run, state=None):
    """run(target) -> (red, why), remembered per code state.

    A TARGET'S COLOUR ONLY CHANGES WHEN CODE DOES (2026-10-01). Every tick re-ran every open
    chunk's suite to learn the same answer: test_scope_lint alone cost 61 s a tick, about every
    two minutes, all day. Keyed on HEAD plus the uncommitted diff, so a merge, a local edit or a
    new file is a new key and the suite runs again. A suite that could not run is never cached."""
    state = state if state is not None else code_state()
    try:
        saved = json.loads(RED_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    mine = saved.get(state, {}) if state else {}
    import threading
    lock = threading.Lock()                 # prefetch_red calls this from CPU_JOBS threads

    def red(t):
        if t in mine:
            return mine[t]
        r = run(t)[0]
        if state and r is not None:
            with lock:
                mine[t] = r
                try:
                    if not RED_CACHE.parent.is_dir():
                        raise OSError(f"{RED_CACHE.parent} is missing -- a state directory is "
                                      f"created by boot or a migration, never by a writer (mf4)")
                    RED_CACHE.write_text(json.dumps({state: mine}), encoding="utf-8")  # one state kept
                except OSError as e:
                    print(f"  NOTE: red cache not written: {e}")
        return r
    return red


def chunks_in_task_order(chunks):
    """Enrolled chunks in the TASK QUEUE's order -- the order the Hub's reorder buttons set.

    Jas, 2026-10-01: "add the reorder buttons to all stages of the production flow ... so I can
    reorder a brief if I want to." Builds and preflights were taken in scripts/autonomy_loop.sh's
    row order, which nothing on the Hub can change, so a button there would have moved a card and
    nothing else. Stable: chunks of one task keep their table order, and a chunk whose task is not
    in the queue goes last. Unreadable queue -> table order, said out loud."""
    try:
        sys.path.insert(0, str(REPO / "projects" / "app-repo"))
        import taskstore
        rank = {m.get("id"): n for n, (m, _b) in enumerate(taskstore.ordered())}
    except Exception as e:                                          # noqa: BLE001
        print(f"  NOTE: task queue unreadable ({type(e).__name__}: {e}); chunks in table order")
        return list(chunks)
    return sorted(chunks, key=lambda c: rank.get(task_of(c.get("brief")), len(rank)))


def prefetch_red(chunks, cache, run):
    """Fill `cache` {target: red} for every chunk the tick may test, CPU_JOBS at a time.

    THE SUITE RUNS GO OUT ON IDLE CORES (Jas, 2026-10-01: "dedicate basically everything that isn't
    model work ... through the cpu"). Each is its own process that no model touches. Run one after
    another they were the whole tick: profiled 2026-10-01, 62.6 of 66.4 s was ten target suites in
    senior_queue. Skips a chunk whose task is closed -- classify() never tests those."""
    todo = sorted({c["target"] for c in chunks if c["target"] not in cache
                   and not task_closed(c.get("brief"))})
    if len(todo) < 2:
        return
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(CPU_JOBS, len(todo))) as ex:
        for t, red in zip(todo, ex.map(run, todo)):
            cache[t] = red


def senior_queue(chunks, red_of, history):
    """(todo, held): the preflights waiting for the senior station, in the order it takes them, and
    those held because a preflight already refused that exact brief. No side effects."""
    running_keys = {r.get("key") for r in ledger_running(history)}
    need_review, _ready, _skipped = classify(chunks, ledger_running(history), red_of, history)
    # HEAD-OF-LINE (2026-09-28): a preflight that REFUSED this exact brief is not re-run until the
    # brief changes. p156's brief listed a file the build is meant to create; the mechanical check
    # refused it in <1 s without asking the model, nothing recorded that as a verdict, and it was
    # relaunched every tick at the head of the queue -- ahead of 26 sound briefs.
    refused = {(r.get("chunk"), r.get("brief_sha256")) for r in history
               if r.get("event") == "finished" and r.get("kind") == "preflight"
               and not r.get("accepted") and r.get("exit") not in YIELD_EXITS}
    # ONLY A REFUSAL THAT LEFT NO VERDICT IS HELD HERE (2026-09-29). The guard is for the mechanical
    # refusal (p156: refused in <1 s, no objection row, so nothing ever expires it). A MODEL objection
    # has a row, and review_state already decides whether it still binds -- a chunk is in
    # need_review precisely because its verdict expired (e.g. the reviewer is now shown files it
    # was not). Holding those too kept p34/p53/p164/p170/p171 held after their evidence changed.
    judged = set()
    if OBJECTIONS.exists():
        for line in OBJECTIONS.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
                # A MECHANICAL REFUSAL IS NOT A VERDICT (2026-09-30). preflight_brief.py now writes a
                # row for it too ("checked without asking the model"), which put its sha in `judged`
                # and released the very case this guard is for: p154 was refused in 0 s 62 times,
                # once every tick, because the file its brief creates did not exist yet.
                if not str(row.get("note") or "").startswith("checked without asking the model"):
                    judged.add(row.get("brief_sha256"))
            except ValueError as e:
                print(f"  NOTE: unreadable preflight-objections row: {e}")
    held = [c for c in need_review if (c["chunk"], c.get("brief_sha256")) in refused
            and c.get("brief_sha256") not in judged]
    todo = [c for c in need_review if c["chunk"] not in running_keys and c not in held]
    return todo, held


MQ = REPO / "workspace" / "run-logs" / "scoping-queue"


SCOPE2_FRESH_S = 600


def _scope2_fresh(mq=None):
    f = Path(mq or MQ) / "SCOPE2"
    try:
        return time.time() - f.stat().st_mtime < SCOPE2_FRESH_S
    except FileNotFoundError:
        return False


def _scope2(on):
    """SCOPING BY THE SENIOR'S LIST, NOT A SWITCH (Jas, 2026-10-02: "Its next task is root cause
    analysis, but I put that low ... Something is broken"). manager_queue.sh ran scoping on the
    Senior's station only when Scoping sat ABOVE PREFLIGHT on its list -- a yes/no switch, not a
    rank -- so with Scoping 5th and RCA 9th the Senior did RCAs while scoping starved behind the
    Manager's own RCAs. senior_slot now ranks scoping with everything else and writes this flag when
    it is the Senior's next task; the queue runs its next task on station 2 while it is fresh."""
    f = MQ / "SCOPE2"
    try:
        if on:
            f.write_text(json.dumps({"at": now()}) + "\n")
        elif f.exists():
            f.unlink()
    except OSError as e:
        print(f"  NOTE: SCOPE2 not written: {e}")


def _yield2(on):
    f = MQ / "YIELD2"
    try:
        if on:
            f.write_text(json.dumps({"at": now()}) + "\n")
        elif f.exists():
            f.unlink()
    except OSError as e:
        print(f"  NOTE: YIELD2 not written: {e}")


def scoping_on_station(history, mq=None):
    """{ours, running, pending, streak} for the manager queue on the Senior's station.

    ours     the Senior's list ranks Scoping above Preflight (task_routing.scoping_station)
    running  the task a live station-2 scoping run holds, or None
    pending  the manager queue has a task to start
    streak   scoping starts on station 2 since the Senior last started a job of its own"""
    mq = Path(mq or MQ)
    out = {"ours": False, "running": None, "pending": False, "streak": 0, "rankable": False}
    try:
        out["ours"] = task_routing.scoping_station(ROUTING) == "senior" or _scope2_fresh(mq)
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: scoping station unreadable: {e}")
    try:
        cur = json.loads((mq / "STATION2").read_text())
        if _alive(cur.get("pid")):
            out["running"] = cur.get("task")
    except FileNotFoundError:
        out["running"] = None                       # no station-2 scoping run: the normal case
    except (OSError, ValueError) as e:
        print(f"  NOTE: {mq / 'STATION2'} unreadable ({e}); station treated as free")
    # RANKABLE: the queue has a task and is not mid-task on the main server -- the condition for
    # senior_slot to rank Scoping on the Senior's list (2026-10-02). Live state, read HERE, so a test
    # that pins this function pins it too.
    try:
        import manager_next
        import who_works
        out["rankable"] = (not out["running"] and bool(manager_next.upcoming(1)[0])
                           and not who_works.scoping_queue_now())
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: manager queue unreadable ({type(e).__name__}: {e}); scoping not ranked")
    if not out["ours"]:
        return out
    try:
        import manager_next
        out["pending"] = bool(manager_next.upcoming(1)[0])
    except Exception as e:                                          # noqa: BLE001
        print(f"  NOTE: manager queue unreadable: {e}")
    last = max((str(r.get("at", "")) for r in history if r.get("event") == "started"
                and r.get("server_port") not in (None, MAIN_PORT)), default="")
    try:
        rows = [json.loads(l) for l in (mq / "station2.jsonl").read_text().splitlines() if l.strip()]
    except (OSError, ValueError):
        rows = []
    out["streak"] = sum(1 for r in rows if str(r.get("at", "")) > last)
    return out


def senior_slot(senior, chunks, red_of, history, dry_run=False):
    """Start the next preflight on the senior station if nothing is running there. Returns the job
    started (or planned, on a dry run), or None. One at a time: the station serves one model, one
    slot. It is its own queue, so it never waits for the main server and never blocks it."""
    global SENIOR_TODO
    # ONE JOB PER SLOT, not per station (2026-10-01: station 2 runs OLLAMA_NUM_PARALLEL=2). A live
    # scoping run is one of the slots.
    mine = [r for r in ledger_running(history) if r.get("server_port") == senior["port"]]
    scoping = scoping_on_station(history)
    used = len(mine) + (1 if scoping.get("running") else 0)
    try:
        import modellease
        n_slots = modellease.slots(senior["lease"], model=senior.get("tag"))
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: senior slots unknown ({e}); 1 assumed")
        n_slots = 1
    if used >= n_slots:
        what = [f"{r['kind']} {r.get('key')}" for r in mine] + ([f"scoping {scoping['running']}"]
                                                                 if scoping.get("running") else [])
        print(f"  senior station: busy ({'; '.join(what)})")
        SENIOR_TODO = [("preflight", c["chunk"]) for c in senior_queue(chunks, red_of, history)[0]]
        return None
    todo, held = senior_queue(chunks, red_of, history)
    for c in held:
        _preflight_defect(c)
    if held:
        print(f"  senior station: {len(held)} brief(s) held -- their preflight refused this exact text "
              f"(sent back to the manager as a scope defect): " + ", ".join(c["chunk"] for c in held))
    print(f"  senior station ({senior['tag']} at {senior['endpoint']}): {len(todo)} preflight(s) waiting")
    # THE SENIOR WALKS ITS OWN LIST (the role store, plan step 4): the first task with work waiting
    # whether or not another role is working it (Team Work removed 2026-10-14). Today's
    # list is Preflight > Session review > RCA, which is what this did before as a literal: the
    # preflights first, and when none wait, the session reviews and RCAs (Jas, 2026-09-29: "The Sr
    # is idle").
    kinds = senior_kinds()
    waiting = {"preflight": [dict(c, kind="preflight", key=c["chunk"]) for c in todo]}
    if any(k != "preflight" for k in kinds):
        for i in priority_items(chunks, ledger_running(history), red_of, history, preflight=False):
            if i["kind"] in kinds and i["kind"] != "preflight" \
                    and (i["kind"] != "assigned_items" or i.get("assigned_to") == "senior"):
                waiting.setdefault(i["kind"], []).append(i)
    # SCOPING IS RANKED LIKE ANY OTHER TASK on the Senior's list -- when the manager queue has a task
    # and is not already mid-task on the main server (one queue, one task at a time).
    if "scoping" in [t.get("task") for t in task_routing.tasks_of(ROUTING, "senior")] \
            and scoping.get("rankable"):
        waiting.setdefault("scoping", []).append({"kind": "scoping", "key": "scoping"})
    others_on = {task_routing.KIND_TASK.get(r.get("kind"), r.get("kind")) for r in ledger_running(history)
                 if r.get("server_port") != senior["port"]}
    SENIOR_TODO = [(k, i["key"]) for k in kinds for i in waiting.get(k, [])]
    task = task_routing.next_task(task_routing.tasks_of(ROUTING, "senior"),
                                  {task_routing.KIND_TASK.get(k, k): len(v) for k, v in waiting.items()},
                                  others_on)
    if task is None:
        _yield2(False)
        _scope2(False)
        return None
    if task == "scoping":
        # The manager queue starts its next task here, under the Senior's lease; this tick starts
        # nothing, so the slot is free when it looks (STATION2 then marks the station busy).
        if not dry_run:
            _scope2(True)
            _yield2(False)
        print("  senior station: next on its list is Scoping -- the manager queue runs it here"
              + (" (dry run: flag not written)" if dry_run else ""))
        return None
    _scope2(False)
    # SCOPING RANKS ABOVE THIS ON THE SENIOR'S LIST and the manager queue has a task: the station is
    # scoping's, until the Series limit says another kind gets one turn (Jas's per-task Series box;
    # 0 = no limit). Without the limit nothing downstream of scoping would ever run here.
    # Only when this job would take the LAST free slot -- otherwise scoping can still start beside it.
    if scoping.get("ours") and scoping.get("pending") and not scoping.get("running") and used + 1 >= n_slots:
        n = int((task_routing.entry(ROUTING, "senior", "scoping") or {}).get("series") or 0)
        if not (n > 0 and scoping["streak"] >= n):
            _yield2(False)
            print(f"  senior station: left for scoping ({scoping['streak']} in a row; series {n or 'no limit'})")
            return None
        _yield2(True)
    else:
        _yield2(False)
    kind = task_routing.TASK_KIND.get(task, task)
    i = waiting[kind][0]
    helping = "" if kind == "preflight" else " (idle: helping the manager)"
    job = {**i, "kind": kind, "model": senior["tag"], "key": i["key"], "role": "senior",
           "endpoint": senior["endpoint"], "lease": senior["lease"], "server_port": senior["port"]}
    if dry_run:
        print(f"    SENIOR    {kind} -> {i['key']}{helping} (dry run: not started)")
        return job
    pid, log = launch(job)
    print(f"    SENIOR    {kind} -> {i['key']}{helping}  pid {pid}, log {log.relative_to(REPO)}")
    return {**job, "pid": pid}


def _own_scope(job):
    """A systemd scope of its own for a job, so it outlives whatever ran this tick (2026-10-02).

    manager_queue.sh runs a tick itself when it yields, so a job that tick launched sat in
    manager-queue.service's cgroup -- KillMode=control-group -- and restarting the queue killed it
    with no 'finished' row: two RCAs (t617, t620) died 50 min in at 07:56Z. start_new_session only
    leaves the process group, not the cgroup. systemd-run --scope execs in place, so the pid is the
    job's. No user systemd (a test, a bare shell): run as before."""
    import shutil
    if not shutil.which("systemd-run") or not os.environ.get("XDG_RUNTIME_DIR") \
            or os.environ.get("HIVEMIND_NO_SCOPE"):
        return []
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", f"hm-{job['kind']}-{Path(str(job['chunk'])).name}")[:80]
    return ["systemd-run", "--user", "--scope", "--quiet", "--collect", f"--unit={name}-{int(time.time())}"]


# JOBS' TEMP COPIES GO TO DISK, NOT RAM (2026-10-13). /tmp on the GX10 is tmpfs: a build's sandbox is a
# copy of the repository (5.4 GB measured), a preflight's ~1 GB, and none of it shows as any process's
# memory -- so memory_admit could not count it. At 20:19Z two builds' copies plus their test runs took
# MemAvailable from 14 GB to 1.9 GB in a minute and the guard killed Swift; that morning 23 GB of
# abandoned copies had filled swap. coder.py, preflight and script_audit all use tempfile, which
# honours TMPDIR, so every job the scheduler starts writes its copies here instead.
JOB_TMP = Path.home() / ".cache" / "hivemind-tmp"


def job_tmp():
    JOB_TMP.mkdir(parents=True, exist_ok=True)
    return JOB_TMP


def _refuse_launch(job, reason, duplicate_of=None):
    """Record a launch that did not happen as a terminal row, with its reason, and return (0, log)."""
    JOBLOGS.mkdir(parents=True, exist_ok=True)
    log = JOBLOGS / (f"{Path(str(job['chunk'])).name}--{job['kind']}--{job['model'].replace(':', '_')}"
                     f"--{int(time.time())}--refused.log")
    log.write_text(f"[{now()}] NOT STARTED: {reason}\n", encoding="utf-8")
    record({"event": "finished", "kind": job["kind"], "chunk": job["chunk"], "model": job["model"],
            "key": job.get("key"), "brief": job.get("brief"), "brief_sha256": job.get("brief_sha256"),
            "pid": 0, "exit": None, "accepted": False, "duplicate_of": duplicate_of,
            "reason": reason, "log": str(log.relative_to(REPO))})
    print(f"    REFUSED   {job['kind']} {job['model']} -> {job.get('key')}: {reason}", flush=True)
    return 0, log


# A STORM IS REFUSED (2026-10-19). Four script reviews whose results were never counted (a stale reviewer
# model in truncation_review) were relaunched 318 times in 15 minutes by the fast path: 60 processes,
# 20 GB, and the memory guard killed Swift. Whatever the cause next time, the same job started this
# often in this window is a queue that does not empty, and running it again cannot help.
STORM_LIMIT = 6
STORM_WINDOW_S = 30 * 60


def recent_starts(history, ident, now=None):
    """How many times the job (kind, key, brief_sha256) was started within STORM_WINDOW_S."""
    now = time.time() if now is None else now
    n = 0
    for r in history:
        if r.get("event") != "started" or (r.get("kind"), r.get("key"), r.get("brief_sha256")) != ident:
            continue
        try:
            at = calendar.timegm(time.strptime(str(r.get("at")), "%Y-%m-%dT%H:%M:%SZ"))
        except ValueError as e:
            print(f"  NOTE: started row with an unreadable 'at' not counted for storms ({e})", file=sys.stderr)
            continue
        if now - at <= STORM_WINDOW_S:
            n += 1
    return n


def launch(job, cap=None):
    """Start a job DETACHED through this script's own --run-job, so its end is recorded.

    THE ONE PLACE A JOB STARTS, CHECKED AT THE MOMENT IT STARTS (2026-10-18). Two writers start
    jobs: the tick, from a plan it computed up to two minutes earlier, and a finishing job's fast
    path (retick). Each decides from its own snapshot of the ledger, so the decision is re-checked
    here, under LAUNCH_LOCK -- held for milliseconds, not for a whole tick:
      * the SAME job already running (kind + key + brief_sha256, alive) is refused, with a terminal
        row naming the pid it duplicates (t1169: p516-t1121-coder was started twice on 2026-10-14);
      * with `cap`, a model already running `cap` jobs is refused, so a stale plan cannot put a
        fifth job on a four-lane model.
    A refusal returns pid 0 and the path of a one-line log saying why."""
    import fcntl
    LAUNCH_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(LAUNCH_LOCK, "a+") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            history = rows()
            done = {r.get("pid") for r in history if r.get("event") == "finished"}
            live = [r for r in history if r.get("event") == "started" and r.get("pid") not in done
                    and _alive(r.get("pid"))]
            ident = (job["kind"], job.get("key"), job.get("brief_sha256"))
            twin = next((r for r in live if (r.get("kind"), r.get("key"), r.get("brief_sha256")) == ident), None)
            if twin:
                return _refuse_launch(job, f"already running as pid {twin.get('pid')} (same kind, key and "
                                           f"brief); a second copy would race the first", twin.get("pid"))
            storm = recent_starts(history, ident)
            if storm >= STORM_LIMIT:
                return _refuse_launch(job, f"STORM: started {storm} times in {STORM_WINDOW_S // 60} min -- this "
                                           f"item is not leaving its queue (a result nobody records, a verdict "
                                           f"nobody reads); find why before it runs again")
            if cap is not None:
                on = sum(1 for r in live if r.get("model") == job["model"])
                if on >= cap:
                    return _refuse_launch(job, f"{job['model']} already runs {on} job(s) of its {cap} "
                                               f"lane(s); the plan that chose this was stale")
            # THE ADMISSION GATE (redesign step 2, 2026-10-29). OFF unless explicitly switched on, because it
            # changes behaviour and the numbers it needs (how often a build's real peak exceeds its booking) are
            # still accumulating: GATE_NEEDS_SAMPLES builds of promised-vs-measured data. When it IS on, a build
            # is judged on the budget rather than on a sample of pressure, and an unreadable budget REFUSES.
            try:
                import capacity_model as _cm
                _gate_on = _cm.gate_switch_on()
                if _gate_on:
                    _free = None
                    try:
                        for _line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                            if _line.startswith("MemAvailable"):
                                _free = int(_line.split()[1]) / 2 ** 20
                                break
                    except OSError as e:
                        print(f"  NOTE: MemAvailable unreadable for the gate ({type(e).__name__}: {e})",
                              file=sys.stderr)
                    _allow, _why = _cm.admission_gate(job["kind"], _free, build_charge_gib()[0] + ADMIT_MARGIN_GIB,
                                                      on=True)
                    if not _allow:
                        return _refuse_launch(job, f"admission gate: {_why}")
            except ImportError as e:
                # THE SWITCH IS ON BUT THE BUDGET CANNOT BE READ: that is a REFUSAL, not a pass. A gate that
                # quietly stops gating is the failure mode this whole redesign exists to remove.
                if str(os.environ.get("HIVEMIND_ADMISSION_GATE") or "").strip() in ("1", "true", "yes", "on"):
                    return _refuse_launch(job, f"admission gate is ON but its model is unreadable ({e}) -- "
                                               f"refusing rather than guessing")
            except Exception as e:                                     # noqa: BLE001 -- said, not swallowed
                print(f"  NOTE: admission gate skipped ({type(e).__name__}: {e})", file=sys.stderr)
            JOBLOGS.mkdir(parents=True, exist_ok=True)
            log = JOBLOGS / (f"{Path(str(job['chunk'])).name}--{job['kind']}--{job['model'].replace(':', '_')}"
                             f"--{int(time.time())}.log")
            with log.open("ab") as out:
                p = subprocess.Popen(_own_scope(job) + [sys.executable, str(Path(__file__).resolve()), "--run-job",
                                      json.dumps(job)], stdout=out, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, cwd=str(REPO), start_new_session=True,
                                     env={**os.environ, "TMPDIR": str(job_tmp()),
                                          "HIVEMIND_TICK_TMPDIR": str(Path(TICK_LOCK).parent)})
            row = {"event": "started", "kind": job["kind"], "chunk": job["chunk"], "model": job["model"],
                   "code_state": code_state() if job["kind"] == "production" else None,
                   "brief": job.get("brief"), "brief_sha256": job.get("brief_sha256"), "pid": p.pid,
                   "key": job.get("key"), "log": str(log.relative_to(REPO)),
                   "server_port": job.get("server_port", MAIN_PORT),
                   # THE ROLE THAT TOOK IT (2026-10-02). Started rows carried no role, so every view guessed
                   # it from the port: on the main server everything but a build read as the Manager's.
                   "role": job.get("role")}
            if job.get("switch_from"):
                row.update(switch_from=job["switch_from"], switch_number=job["switch_number"])
            record(row)
            # STEP 1 OF THE REDESIGN, ACTUALLY BOOKING (2026-10-29). The budget shipped able to report and
            # nothing booked, so the promised-vs-measured samples the gate needs could never accrue: a ledger
            # nobody writes to reads as "nothing promised" for ever. Observation only -- it gates nothing.
            budget_book(row)
            return p.pid, log
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


TASK_LINE = re.compile(r"^\s*TASK:\s*(t\d{2,4})\b", re.MULTILINE)
TASK_ANY = re.compile(r"\b(t\d{2,4})\b")


def task_of(brief_name):
    """The task a brief belongs to: a `TASK: tNNN` line, or failing that the first tNNN in it.

    THE DECLARED FORM IS THE ONE TO USE. Most briefs on 2026-09-16 name no task at all -- of
    the twenty enrolled, one mentioned its task id and the rest mention none -- so a failure
    could not be written back to anything. The loose fallback exists only so the briefs that
    happen to mention a task still work; check_workspace names the ones that declare nothing,
    because a chunk whose failures cannot reach its task is a chunk nobody can learn from.
    """
    if not brief_name:
        return None
    p = REPO / "workspace" / "briefs" / brief_name
    if not p.is_file():
        return None
    text = p.read_text(encoding="utf-8", errors="replace")
    m = TASK_LINE.search(text) or TASK_ANY.search(text) or TASK_ANY.search(brief_name)
    return m.group(1) if m else None


def note_failure_on_task(job, rc, seconds, log_rel):
    """Write what went wrong onto the TASK, so the next attempt starts from the record.

    Jas, 2026-09-16: "can all the error messages and any data related to the production of a
    specific task be recorded within the task itself? That way, the next time the task comes by,
    we can look at the failures and learn." Until this existed a retry carried no information at
    all -- coder.py is invoked identically every time and the switch history went only to the
    ledger -- so attempts two and three could do nothing but repeat attempt one.

    NEVER RAISES. A note that cannot be written must not take the dispatch down with it; the
    failure itself is already in the ledger either way.
    """
    try:
        tid = task_of(job.get("brief"))
        if not tid:
            print(f"  NOTE: {job.get('chunk')} has no task id in its brief, so this failure "
                  f"was recorded only in the ledger. Add a 'TASK: tNNN' line to "
                  f"{job.get('brief')}.", flush=True)
            return None
        sys.path.insert(0, str(REPO / "scripts"))
        sys.path.insert(0, str(REPO / "projects" / "app-repo"))
        import supervise
        import taskstore as ts
        kind, meaning = supervise.FAILURE_KINDS.get(
            rc, ("unclassified", f"exit {rc} is not one coder.py documents"))
        from loudcap import cap
        trail, tail = "", ""
        if log_rel:
            lp = REPO / log_rel
            if lp.is_file():
                lines = lp.read_text(encoding="utf-8", errors="replace").splitlines()
                trail = "\n".join(l for l in lines if re.match(r"\s*attempt \d+ ", l))
                # THE END of a log is where the job died, so keep the end and declare the cut.
                tail = cap("\n".join(lines[-40:]), 3000, f"the end of {log_rel}")
        body = (f"**PRODUCTION FAILED** — {kind} (exit {rc})\n\n"
                f"{meaning}\n\n"
                f"chunk `{job.get('chunk')}` on `{job.get('model')}`, {seconds}s, "
                f"brief `{job.get('brief')}` (sha {str(job.get('brief_sha256'))[:12]}), "
                f"target `{job.get('target')}`, files `{job.get('files')}`\n\n"
                f"log: `{log_rel or 'not recorded'}`\n\n"
                f"ATTEMPT TRAIL\n{trail or '(none recorded)'}\n\n"
                f"HOW IT ENDED\n{tail or '(no log)'}\n\n"
                f"Read before re-dispatching: whatever is wrong here, another identical attempt "
                f"cannot discover it.")
        # THE SCHEMA ROW, BEFORE THE NOTE (t366): one writer, so the two cannot disagree.
        try:
            import failure_schema
            sp = REPO / "workspace" / "failure-schema.json"     # REPO, so a test's temp root is used
            if not sp.is_file():
                raise FileNotFoundError(sp)
            cls, how = failure_schema.record_failure(job.get("chunk"), now(), trail + "\n" + tail,
                                                     path=sp)
            body += f"\n\nFailure class: `{cls}` ({how} in workspace/failure-schema.json)."
        except Exception as e:                                          # noqa: BLE001
            print(f"  NOTE: failure-schema row not written: {type(e).__name__}: {e}", flush=True)
        ts.add_note(tid, body, who="dispatch-loop")
        # TO THE FRONT FOR RE-SCOPING (Jas, 2026-09-20: "after 1 failure the reasons for failure
        # need to be noted and the task goes to be scoped again at the front of the queue").
        # The note was written; the move never was, until 2026-10-01.
        try:
            ts.move(tid, "top")
        except Exception as e:                                          # noqa: BLE001
            print(f"  NOTE: {tid} not moved to the front: {type(e).__name__}: {e}", flush=True)
        print(f"  recorded this failure on {tid} and moved it to the front of QUEUE.md", flush=True)
        return tid
    except Exception as e:                                              # noqa: BLE001
        print(f"  NOTE: could not write the failure onto its task: "
              f"{type(e).__name__}: {e}", flush=True)
        return None


def review_status(job):
    """A script-review's OWN verdict, read from the ledger that knows it. None for other kinds.

    truncation_review.py exits 0 whether its reply was usable or not, because the run succeeded
    and only the answer was garbage. On 2026-09-15 that gap recorded 29 unreadable reviews of one
    file as "exit 0 ACCEPTED": --status showed a working reviewer for 7.7 hours while the review
    ledger said "unreadable" every time, and the optimistic ledger is the one a person reads.
    Two ledgers that can disagree must be reconciled by whichever is written second -- this one.
    """
    try:
        sys.path.insert(0, str(REPO / "scripts"))
        import truncation_review
        path = job.get("path") or job.get("chunk")
        mine = [r for r in truncation_review.rows() if r.get("path") == path]
        exact = [r for r in mine if r.get("sha256") == job.get("brief_sha256")]
        row = (exact or mine or [None])[-1]
        return (row or {}).get("status") or "no row written"
    except Exception as e:                                              # noqa: BLE001
        return f"unreadable ledger ({type(e).__name__}: {e})"


# THE TICK LOCK, and it must be the SAME file the crontab names -- a re-plan that took a
# different lock would run beside the scheduled tick instead of standing down for it, which is
# the one thing flock is here to prevent. Built from gettempdir() rather than written as a
# literal: library_checks blocks a hardcoded /tmp path, because coder.py's shared
# /tmp/coder-live.log was truncated by every new lane. Here the sharing IS the mechanism, and
# gettempdir() resolves to the same /tmp/dispatch_loop.lock that cron uses.
# THE SCHEDULER'S TEMP DIR, NOT THE JOB'S (2026-10-15). launch() gives every job TMPDIR=job_tmp(),
# and the re-plan a finishing job starts runs inside it, so gettempdir() named a per-job lock that
# cron's lock never contended with: two ticks ran at once and both started the same RCA
# (d04831a130ea, d1d56244ee4b, d260dd6fb1b3 -- the second copy crashed renaming a finding the first
# had moved to done/). launch() passes the tick's own temp dir down as HIVEMIND_TICK_TMPDIR.
TICK_LOCK = str(Path(os.environ.get("HIVEMIND_TICK_TMPDIR") or tempfile.gettempdir()) / "dispatch_loop.lock")
# THE LAUNCH LOCK (2026-10-18): held only while launch() checks the ledger and starts one job, so
# the fast path never waits for a whole tick. Same directory as TICK_LOCK, for the same reason.
LAUNCH_LOCK = Path(TICK_LOCK).parent / "dispatch_launch.lock"


def retick(finished=None, items=None, running=None, models=None, slots=None, launch=None):
    """A finished job frees its lane NOW; the scheduled tick can be ten minutes away (t1309).

    plan() is a snapshot taken once per cron tick, so a model that finishes a 70-second review
    idles for the rest of the window. Measured 2026-09-15: gemma4:26b ran at an 11% duty cycle
    over six hours, and a CLEARED chunk was skipped four ticks running with "waiting -- every
    model has a lane" while that model had been free for nine and a half minutes.

    AFTER A SUCCESS, AND NOT FOR PRODUCTION. Both limits come from failures seen the same day.
    Re-planning after a failure is a hot loop: p32's baseline refusal returned in 83 s and was
    not counted as a model failure, so nothing would have parked it and it would have re-run at
    machine speed instead of every ten minutes. And production is excluded because a chunk that
    has DELIVERED is re-dispatched until someone merges it -- p32 finished ACCEPTED at 22:39:33
    and the very next tick started it again -- so re-planning on that success would rebuild a
    30-minute job back to back, forever. The waste this fixes is short jobs holding a lane; a
    ten-minute snapshot costs a half-hour build almost nothing.

    IT NO LONGER RUNS A TICK (2026-10-17). This used to Popen 'flock -n TICK_LOCK dispatch_loop.py'
    -- the whole script, memory checks, thinkpad_cycle, intake, enrolment -- and flock -n dropped it
    whenever cron's tick held the lock, which was most of the time. Measured 2026-10-16: the
    Auditor's session reviews ran 1-2.5 min with 2-3.7 min idle between them. A finished job needs
    lanes filled under schedule()'s rules, so that is all this does: read the CURRENT priority
    order (priority_items re-reads the role store, the queues and the ledger on every call; nothing
    is cached, so a task Jas moves up is taken at the next free lane), drop builds, keep the models
    with a genuinely free lane, and launch what schedule() picks -- for EVERY role, not just the
    one that finished (Jas: "Will this speed up apply to all roles?").

    THE KIND ALLOWLIST IS GONE. RETICK_KINDS left out session-review and rca -- the Auditor's work,
    the role measured idle -- so those roles never got a re-plan at all. `kind != 'production'` at
    the gate is the mechanical form of "builds stay on the normal tick".

    NEVER INTERRUPTS A RUNNING JOB: only models with a free lane reach schedule(), so its interrupt
    branches (all of which need slots(m) - len(on_m) <= 0) are unreachable from here. Stopping a
    25-minute build for a 70-second review is the tick's decision, not a finishing job's.

    `finished` is accepted and deliberately not read: the oracle hands it positionally, and the gate
    in run_job calls retick() with no arguments (test_job_finish_contract.py pins that). The ledger
    already counts the finished job as not running, because its started row has a finished row.

    NO TICK LOCK (2026-10-18). It first took cron's tick lock and skipped when it was held (11 of 21
    finished jobs, since a tick holds it ~2 min of every 2), then waited for it (still up to 2 min of
    idle lane). Jas: "the point was adding tasks to the lane as soon as it's free." So it takes no tick
    lock at all: launch() is the one place a job starts, and it re-checks under LAUNCH_LOCK -- held for
    milliseconds -- that the job is not already running and the model still has a free lane.
    """
    running = ledger_running() if running is None else running
    models = (list(dict.fromkeys(list(PRODUCTION_MODELS) + main_role_models()))
              if models is None else list(models))
    slots = slots or model_slots
    launch = launch or globals()["launch"]
    if items is None:
        import parallel_dispatch as pd
        history = rows()
        red_of = cached_red(running)
        items = priority_items(chunks_in_task_order(pd.chunks()), running, red_of, history,
                               preflight=main_preflights(senior_station()))
    items = [i for i in items if i.get("kind") != "production"]
    on = {}
    for r in running:
        on[r.get("model")] = on.get(r.get("model"), 0) + 1
    free = {m for m in models if slots(m) > on.get(m, 0)}
    items = [i for i in items if i.get("model") in free]
    starts, preempt, _queued, _reserved = schedule(models, items, running, slots=slots)
    if preempt:
        print("  NOTE: fast path skipped " + str(len(preempt)) + " interrupt(s); builds are "
              "stopped by the tick, not by a finishing job", flush=True)
        held = {r.get("model") for r in running if r.get("pid") in set(preempt)}
        starts = [j for j in starts if j.get("model") not in held]
    out = []
    for j in starts:
        # launch() re-checks the job and the model's lanes under LAUNCH_LOCK: a tick or another
        # finishing job may have filled the lane since this read the ledger.
        pid, log = (launch(j, cap=slots(j["model"])) if launch is globals()["launch"] else launch(j))
        if not pid:
            continue
        print("    FAST      " + str(j["kind"]) + " " + str(j["model"]) + " -> " + str(j["key"])
              + "  pid " + str(pid) + " log " + str(log), flush=True)
        out.append(j)
    return out



def _role_args(job):
    """--model/--role for a session-review or RCA job: the role the scheduler chose (2026-10-02)."""
    out = ["--model", job["model"]] if job.get("model") else []
    return out + (["--role", job["role"]] if job.get("role") else [])


BUILD_WALL_S = 3600          # see run_job: the wall-clock cap on one production build
BUILD_TIMEOUT_EXIT = 124     # timeout(1)'s code; not 1, so supervise catalogues it rather than judging it
SIGNAL_SLACK_S = 60          # a 124 this far short of BUILD_WALL_S was not the wall clock


def build_exit(rc, elapsed):
    """The exit to record for a production build. A SIGNAL IS NOT THE WALL CLOCK (2026-10-24): the memory
    guard's build tier matches any argv with coder.py in it -- the `timeout` wrapper too -- and a SIGTERM
    to timeout(1) makes it exit 124, its own "timed out" code. Builds stopped by the guard after 92, 212
    and 834 s were recorded as 124 and parked as "came to nothing" before the model ran. A 124 well short
    of BUILD_WALL_S is recorded as 143 (SIGTERM), which YIELD_EXITS already treats as a yield."""
    if rc == BUILD_TIMEOUT_EXIT and elapsed is not None and elapsed < BUILD_WALL_S - SIGNAL_SLACK_S:
        return 143
    return rc


def signal_not_timeout(r):
    """True for a finished row recorded as 124 that ended well short of BUILD_WALL_S: the ledger holds
    such rows from before build_exit(), and they are yields too."""
    secs = r.get("seconds")
    return (r.get("exit") == BUILD_TIMEOUT_EXIT and secs is not None
            and float(secs) < BUILD_WALL_S - SIGNAL_SLACK_S)


def run_job(job):
    """The detached child. Exit code is the verdict; the row names the pid it ran under."""
    t0 = time.time()
    brief = str(REPO / "workspace" / "briefs" / (job.get("brief") or ""))
    if job.get("kind") == "console":
        cmd = [sys.executable, str(REPO / "projects" / "local-inference" / "console.py"),
               "--answer", job["key"], job["model"]]
    elif job.get("kind") == "supervise":
        cmd = [sys.executable, str(REPO / "scripts" / "supervise.py"), "--failure", job["key"],
               *(["--model", job["model"]] if job.get("model") else [])]
    elif job.get("kind") == "session-review":
        cmd = [sys.executable, str(REPO / "scripts" / "session_review.py"), "extract",
               "--request", job["path"], *_role_args(job)]
    elif job.get("kind") == "rca":
        cmd = [sys.executable, str(REPO / "scripts" / "session_review.py"), "rca",
               "--finding", job["path"], *_role_args(job)]
    elif job.get("kind") == "script-audit":
        cmd = [sys.executable, str(REPO / "scripts" / "script_audit.py"), "run", "--request", job["path"],
               *_role_args(job)]
    elif job.get("kind") == "script-review":
        cmd = [sys.executable, str(REPO / "scripts" / "truncation_review.py"), "--path", job["path"],
               *(["--model", job["model"]] if job.get("model") else [])]
    elif job.get("kind") == "assigned_items":
        cmd = [sys.executable, str(REPO / "scripts" / "assigned_worker.py"), "run", job["key"],
               "--role", job["assigned_to"], "--model", job["model"]]
    elif job.get("model_task"):
        cmd = [sys.executable, str(REPO / "scripts" / "task_worker.py"), "run", job["kind"], job["path"],
               "--model", job["model"]]
    elif job.get("kind") == "preflight":
        cmd = [sys.executable, str(REPO / "scripts" / "preflight_brief.py"), "--brief", brief,
               "--files", *job["files"].split(), "--target", job["target"],
               "--model", job["model"]]
    else:
        cmd = [sys.executable, str(CODER), "--brief-file", brief, "--files", *job["files"].split(),
               "--target", job["target"], "--model", job["model"], "--apply"]
        cp = CHECKPOINTS / f"brief-{__import__('hashlib').sha256(Path(brief).read_text(encoding='utf-8').encode()).hexdigest()[:8]}--{_safe(job['model'])}.json"
        if cp.is_file():
            cmd += ["--resume-checkpoint", str(cp)]
    print(f"[{now()}] {' '.join(cmd)}", flush=True)
    env = dict(os.environ)
    if job.get("role"):              # which role this job runs as: its skills are that role's (plan 3c)
        env["HIVEMIND_ROLE"] = job["role"]
    if job.get("endpoint"):          # the senior station: its own server and lease
        env.update(HIVEMIND_OLLAMA=job["endpoint"], HIVEMIND_LEASE=job["lease"])
    kind = job.get("kind", "production")
    if kind == "production":
        # A WALL-CLOCK CAP ON A BUILD (2026-10-19). Three single-file builds ran 2.5-2.8 h (supervisor,
        # 10-19: socket timeouts at 8951 s, 9363 s, 10085 s) because each model call's timeout is derived
        # from its output budget. Since 10-10: accepted builds p50 851 s, p90 1590 s, 1 of 78 over an hour;
        # failed ones 22 of 358 over an hour. GNU timeout signals the whole process group, so the build's
        # suites stop with it, and exits 124 (BUILD_TIMEOUT_EXIT).
        cmd = ["timeout", "--kill-after=60", str(BUILD_WALL_S)] + cmd
    _t0 = time.time()
    rc = subprocess.call(cmd, cwd=str(REPO), stdin=subprocess.DEVNULL, env=env)
    _elapsed = time.time() - _t0
    if kind == "production" and rc == BUILD_TIMEOUT_EXIT and build_exit(rc, _elapsed) != rc:
        rc = build_exit(rc, _elapsed)
        print(f"[{now()}] STOPPED BY A SIGNAL after {_elapsed:.0f} s, not the wall clock ({BUILD_WALL_S} s) -- "
              f"most likely the memory guard (workspace/memory-guard.jsonl). Recorded as a yield (exit {rc}).",
              flush=True)
    timed_out = kind == "production" and rc == BUILD_TIMEOUT_EXIT
    if timed_out:
        print(f"[{now()}] BUILD WALL CLOCK: stopped after {_elapsed:.0f} s (the cap is {BUILD_WALL_S} s, "
              f"dispatch_loop.BUILD_WALL_S)", flush=True)
    # A ZERO EXIT IS NOT A VERDICT FOR EVERY KIND. For a script-review the process succeeds and
    # the reply can still be unusable, so the review's own status decides -- see review_status().
    verdict = review_status(job) if kind == "script-review" else None
    accepted = rc == 0 and verdict in (None, "ok")
    # ONE LOOKUP FOR EVERY FINISHED JOB, not just the production failures. The log path is written
    # on this job's own `started` row, and a finished row that names no artefact is the shape the
    # RCA had to reconstruct by hand. Read once here; the production-failure branch below reuses it.
    mine = [r for r in rows() if r.get("event") == "started"
            and r.get("chunk") == job.get("chunk")
            and r.get("model") == job.get("model")
            and r.get("brief_sha256") == job.get("brief_sha256")]
    log_rel = mine[-1].get("log") if mine else None
    row = {"event": "finished", "kind": kind, "chunk": job["chunk"],
           "model": job["model"], "brief": job.get("brief"), "brief_sha256": job.get("brief_sha256"),
           "key": job.get("key"), "yielded": rc in YIELD_EXITS,
           "pid": os.getpid(), "exit": rc,
           # production: 0 = accepted and delivered to a review branch. preflight: 0 = cleared.
           "accepted": accepted, "seconds": round(time.time() - t0),
           "result_path": log_rel}
    if timed_out:
        row["timed_out_s"] = BUILD_WALL_S
    if verdict is not None:
        row["review_status"] = verdict
    record(row)
    # SCOPE QUALITY (Jas, 2026-09-28): a build is a check on the scope that wrote its brief. The
    # outcome only -- whose fault a failure was is decided by supervise and the RCA, not here.
    if kind == "production" and rc not in YIELD_EXITS:
        import scope_quality
        scope_quality.record("build", "accepted" if accepted else f"failed exit {rc}",
                             chunk=job["chunk"], source="dispatch_loop", builder=job["model"],
                             seconds=row["seconds"], ledger=LEDGER.parent / "scope-quality.jsonl",
                             defects=LEDGER.parent / "scope-defects")
    # EVERY PRODUCTION FAILURE GOES ONTO ITS TASK. A yield is not a failure -- it attempted
    # nothing -- and an acceptance has nothing to learn from. The log path comes from this
    # job's own `started` row, which is the only place it is written.
    if kind == "production" and not accepted and rc not in YIELD_EXITS:
        note_failure_on_task(job, rc, row["seconds"], log_rel)
    # A YIELD RE-PLANS WHATEVER ITS KIND. A yield is the harness saying "I stood down so
    # something else can run", so the lane is free NOW and something is already waiting -- it
    # is the one case where re-planning is unambiguously right. Excluding it cost a measured
    # ten minutes: at 00:20:03 the tick preempted p32 for 25 queued preflights and then started
    # none of them, because the priority work only begins on the tick AFTER the model is
    # actually free. Doing it here rather than in the tick is what makes it safe -- by the time
    # this runs the process is gone and its model lease is released.
    # session-review and rca -- the Auditor's work, ~50% idle on 2026-10-16 -- re-plan now too,
    # which is why the kind allowlist was dropped (t1309). retick() takes NO arguments here.
    if (accepted and kind != "production") or rc in YIELD_EXITS:
        retick()
    return rc


# A COLD LOAD CAN EVICT THE INTERACTIVE MODEL, AND THE PATH PROBE CANNOT SEE IT COMING.
# inference_gate answers "is the path usable"; it says nothing about whether the box has room
# for the model this tick is about to load. On 2026-09-19 22:58 ollama logged "model predicted
# to exceed available memory, evicting", predicted 105.0 GiB at num_ctx 262144 -- one of 74
# such events in workspace/run-logs/ollama-events.jsonl. That is how the interactive session
# gets lost: not a wedged slot, not a warmer, but a dispatch cold-loading a second model into
# a box that cannot hold both. The gate turns probe-down-with-a-resident into WAIT; this turns
# probe-UP-without-room into WAIT too, and names the model it is protecting.
#
# WEIGHTS ARE NOT THE FOOTPRINT. Measured 2026-09-22 on this box: qwen3.8:27b-mtp-bf16 is
# 50.9 GiB of weights but 62.3 GiB resident at 262144 / -np 1, and 71.8 at -np 2, because KV
# is 9.5 GiB PER SLOT (llama_kv_cache 8704 MiB + 1024 MiB at 262144 cells). gemma4:26b runs
# nearer 1.35x its weights because sliding-window attention puts only 8 of its layers at full
# context. FOOTPRINT_FACTOR is the conservative end of that measured range; RESERVE_GB is what
# stays free for the prompt cache (an 8192 MiB per-runner ceiling) and context checkpoints.
FOOTPRINT_FACTOR = 1.5
RESERVE_GB = float(os.environ.get("DISPATCH_RESERVE_GB", "12"))


def _mem_available_gb():
    """MemAvailable in GB, or None if it cannot be read. None means DO NOT GATE."""
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1e6            # kB -> GB
    except (OSError, IndexError, ValueError) as e:
        print(f"  NOTE: memory headroom not checked: {type(e).__name__}: {e}")
    return None


def _tag_sizes_gb():
    """Tag -> on-disk GB from /api/tags. {} if unreachable, which disables the check."""
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=10) as r:
            return {m["name"]: m.get("size", 0) / 1e9 for m in json.load(r).get("models", [])}
    except Exception as e:                                        # noqa: BLE001
        print(f"  NOTE: model sizes unreadable, headroom not checked: {type(e).__name__}: {e}")
        return {}


def memory_block(models, snap, avail_gb=None, sizes=None):
    """Why a cold load must wait, or None when there is room -- or no way to tell.

    Returns None rather than blocking whenever the measurement is unavailable: a gate that
    cannot measure must not invent a reason to stop production. It only ever speaks about a
    model that is NOT already resident, because loading one already in memory costs nothing
    and must never be blocked.
    """
    resident = {m["name"]: m.get("gb", 0) for m in snap.get("resident", [])}
    # Only GX10 models: this gate reads the GX10's MemAvailable. A model on another device (the
    # ThinkPad eGPU) is budgeted by fits() against that device -- 2026-10-15, Frontline's 26b on the
    # ThinkPad sat behind "NO ROOM" here for want of GX10 memory it would never use.
    cold = [m for m in (models or []) if m not in resident and DEVICE_OF.get(m, "gx10") == "gx10"]
    if not cold:
        return None
    avail_gb = _mem_available_gb() if avail_gb is None else avail_gb
    sizes = _tag_sizes_gb() if sizes is None else sizes
    if avail_gb is None or not sizes:
        return None
    biggest = max(cold, key=lambda m: sizes.get(m, 0))
    want = sizes.get(biggest, 0)
    if not want:
        return None                   # an unknown tag is not evidence of a problem
    need = want * FOOTPRINT_FACTOR + RESERVE_GB
    if avail_gb >= need:
        return None
    holder = ",".join(sorted(resident)) or "nothing"
    return {"holder": holder, "model": biggest, "need_gb": round(need, 1),
            "avail_gb": round(avail_gb, 1),
            "say": (f"NO ROOM: loading {biggest} needs about {need:.0f} GB "
                    f"(weights {want:.0f} x{FOOTPRINT_FACTOR} for KV, plus {RESERVE_GB:.0f} GB "
                    f"reserved) and only {avail_gb:.0f} GB is available. Resident: {holder}. "
                    f"Waiting rather than evicting it.")}


def unready_models(models, resolve=None):
    """{model: why} for each model whose server does not answer right now; {} when all do.

    One cheap health call per distinct server (3 s timeout), never a generation, so a busy model
    still counts as ready. resolve() raising (NoServer) is itself the answer."""
    if resolve is None:
        sys.path.insert(0, str(REPO / "projects" / "local-inference"))
        import infer
        resolve = infer.resolve
    down, seen = {}, {}
    for m in models:
        try:
            adapter, _ = resolve(m)
        except Exception as e:                                      # noqa: BLE001 -- NoServer is the answer
            down[m] = f"{type(e).__name__}: {e}"
            continue
        key = getattr(adapter, "url", id(adapter))
        if key not in seen:
            seen[key] = adapter.health()
        if not seen[key]:
            down[m] = f"{key} does not answer (still loading, or stopped)"
    return down


def inference_gate(lanes, running, resident):
    """One answer to 'may this tick dispatch' (p45/t359): {"verdict", "holder", "say"}.

    verdict is "up" | "wait" | "refused". A running job vouches for the path and the
    probe is NEVER called in that branch. A probe-down with NOTHING resident is the
    genuine dead path: refused, unchanged. A probe-down with a non-lane resident
    holding the single slot is WAIT with the holder NAMED: busy is not down (an
    interactive session or a long run holds the slot, the path is fine). 2026-09-20
    08:51-14:20Z, six+ ticks refused production while the holder was the live
    session -- a refusal that names no one ends in a manual Ollama restart; a wait
    that names its holder is a line to read. Contract pinned line-by-line by
    scripts/tests/test_inference_busy.py (frozen; do not edit it to fit)."""
    if lanes or running:
        return {"verdict": "up", "holder": None, "say": "a running job vouches for it"}
    try:
        import inference_health
    except ImportError:
        return {"verdict": "up", "holder": None,
                "say": "inference_health not importable; not checked"}
    h = inference_health.probe()
    if h.get("up"):
        return {"verdict": "up", "holder": None, "say": h.get("say")}
    if resident:
        return {"verdict": "wait", "holder": ",".join(sorted(resident)),
                "say": h.get("say")}
    return {"verdict": "refused", "holder": None, "say": h.get("say")}


def tick(dry_run=False, models=None):
    global RED_ACTIONS
    RED_ACTIONS = not dry_run          # red_on_main acts on the live tick only
    import scheduler_commands
    if scheduler_commands.is_paused():
        # A PAUSED TICK LEAVES A ROW. Until 2026-09-22 it did not, and the flag set on 09-20
        # outlived its builds for two days without a trace: the cron log filled with "PAUSED"
        # while dispatch-loop.jsonl stopped dead at 14:50:01Z, so the ledger read as "the loop
        # died" when it had ticked ~300 times and refused every one. A ledger that goes quiet
        # for the one reason it most needs to report is the reassuring status line this
        # repository keeps rediscovering.
        # The flag's EXISTENCE is the pause; its contents are a courtesy. An unreadable flag
        # therefore still pauses -- but it says so in the row rather than being swallowed,
        # because "who paused this and why" going missing is the whole failure being fixed.
        note, flag_error = {}, None
        try:
            note = json.loads(scheduler_commands.PAUSE_FLAG.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            flag_error = f"{type(e).__name__}: {e}"
        record({"event": "paused", "who": note.get("who"), "why": note.get("why"),
                "paused_at": note.get("at"), "flag_unreadable": flag_error})
        print("  PAUSED -- delete workspace/run-logs/scheduler-paused.enabled, or call "
              "scheduler_commands.resume(), to continue")
        return 0
    import parallel_dispatch as pd
    import lane_status
    models = models or PRODUCTION_MODELS
    history = rows()
    # PRODUCTION LANES ONLY, and the filter is load-bearing (2026-09-16).
    # lane_status.lanes() was widened that day to report every job that occupies a model --
    # console, preflight, supervise, script-review -- because the Status tab showed an idle
    # box while the GPU was busy. The SCHEDULER must not read it the same way: everything
    # here is labelled kind="production" below and counted as a dispatch holding a model.
    # console.py is a persistent server, so within minutes of the widening it appeared as a
    # 19-hour production job on a model called "(default)", and its mere presence also made
    # `lanes or running` true, which silently skipped the inference-health probe under the
    # banner "a running job vouches for it". The display wants every lane; the scheduler
    # wants the ones it dispatched. ledger_running() supplies the rest, as it always has.
    # PINNED BY scripts/tests/test_lane_contract.py (L-152), which asserts both halves: that
    # lanes() still reports more than production, and that this filter is still here.
    # A BUILD IS A BRIEF ON DISK (2026-10-09), the rule who_works.py already applies: test suites
    # launch fake coder.py processes (model "faketest", a sentence for a brief) from /tmp sandboxes.
    # Counted here, one held memory admission for every real job -- "swift does not fit beside
    # ['faketest']" -- while a merge gate ran for three hours, and the line sat at 1 of 4 lanes.
    lanes = [r for r in lane_status.snapshot()["lanes"]
             if r.get("kind", "production") == "production"
             and (REPO / "workspace" / "briefs" / str(r.get("brief") or "")).is_file()]
    # Ollama's journal -> run-logs, so error_scan sees evictions, OOM and runner crashes.
    try:
        subprocess.run([sys.executable, str(REPO / "scripts" / "ollama_events.py")],
                       capture_output=True, text=True, timeout=120)
    except Exception as e:                                          # noqa: BLE001
        print(f"  NOTE: ollama event collection failed: {type(e).__name__}: {e}")
    # t362: slot hold/release ledger -> run-logs, so "who held the slot, for how long"
    # is a line to read (the 2026-09-19 10:47 starvation was reconstructable only from
    # the dead session's transcript; oracle = scripts/tests/test_slot_events.py).
    try:
        subprocess.run([sys.executable, str(REPO / "scripts" / "slot_events.py")],
                       capture_output=True, text=True, timeout=120)
    except Exception as e:                                          # noqa: BLE001
        print(f"  NOTE: slot ledger collection failed: {type(e).__name__}: {e}")
    # AUTO-MERGE (Jas, 2026-10-01: "let's do the auto merge"). Delivered builds that pass every
    # mechanical condition in scripts/auto_merge.py merge themselves; the rest wait for Claude.
    # Detached, because merge_gate runs the suites; auto_merge holds its own lock.
    if not dry_run:
        try:
            log = (REPO / "workspace" / "run-logs" / "auto-merge.log").open("a")
            subprocess.Popen([sys.executable, str(REPO / "scripts" / "auto_merge.py"), "--merge"],
                             cwd=str(REPO), stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                             start_new_session=True)
        except OSError as e:
            print(f"  NOTE: auto-merge not started: {e}")
    snap = lane_status.snapshot()
    resident = {m["name"] for m in snap.get("resident", [])}
    stuck = wedged(lanes, resident)
    stop_wedged = WEDGE_ACTION == "stop" or (REPO / "workspace" / "run-logs" / "wedge-stop.enabled").exists()
    if stuck and not dry_run and not stop_wedged:
        for r in stuck:
            record({"event": "wedged", "pid": r["pid"], "model": r["model"], "brief": r["brief"],
                    "log_age_s": r.get("log_age_s"), "action": "reported only"})
    if stuck and not dry_run and stop_wedged:
        import signal
        for r in stuck:
            try:
                os.kill(int(r["pid"]), signal.SIGTERM)
                record({"event": "wedged", "pid": r["pid"], "model": r["model"], "brief": r["brief"],
                        "log_age_s": r.get("log_age_s"), "why": "model not loaded and log silent"})
            except (OSError, ValueError) as e:
                print(f"    could not stop wedged pid {r['pid']}: {e}")
    for r in stuck:
        print(f"    WEDGED    pid {r['pid']} {r['model']}: silent {r.get('log_age_s', 0) // 60} min, model not loaded")
    lanes = [r for r in lanes if r not in stuck]
    # A LANE WHOSE PROCESS IS GONE CANNOT MAKE ITS BRIEF "ALREADY RUNNING" FOR EVER (2026-10-29). `lanes` is
    # the lane view, and a job the view still lists enters `running`, which makes the next tick skip that
    # brief as "already running" -- so the REASON becomes the EVIDENCE and re-confirms itself every tick.
    # Measured: two dead pids (1596355/1596356) kept 169 chunks waiting and the tick reporting running: 2 with
    # started: 0. ledger_running() has always filtered on liveness; this list did not.
    gone = [r for r in lanes if r.get("pid") and not pid_alive(r["pid"])]
    for r in gone:
        print(f"    LOST      pid {r['pid']} {r.get('model')}: process gone, lane freed "
              f"(no finished row was written)")
    lanes = [r for r in lanes if r not in gone]
    running = ([{"model": r["model"], "brief": r["brief"], "kind": "production", "pid": r["pid"],
                 "key": None, "elapsed_s": r.get("elapsed_s")} for r in lanes]
               + [r for r in ledger_running(history) if r["kind"] != "production"
                  and r.get("server_port", MAIN_PORT) == MAIN_PORT])   # the senior's jobs are its own
    # A LIVE MANAGER RUN IS A RUNNING JOB (2026-09-26). The manager queue runs around the
    # clock and is not in the dispatch ledger, so `running` was empty while Q4 generated;
    # inference_gate then probed, queued behind that generation, timed out after 600 s and
    # returned WAIT -- no build started for anyone, Gemma included, while it sat idle ("Gemma
    # seems stuck"). Counted here (not in inference_gate, whose contract is frozen) it also
    # marks the manager's model busy, which is true: llama-server runs one slot per model.
    # ONLY JOBS ON THE MAIN SERVER OCCUPY ITS MODELS (2026-09-28). The manager queue's station 2
    # (ollama-station2, :11435) runs its own Q4 copy; counting that job here marked the main copy
    # busy, and every preflight, supervision and session review queued behind a model that was idle.
    # NOT TWICE (2026-10-01): a ledger job's OWN model call (an RCA's ask_q16) also shows as a
    # "manager" lane. Counted as a second job it filled the second slot, so the slot never carried
    # work. A manager lane whose process descends from a running ledger job is that job.
    _jobs = {str(r.get("pid")) for r in running if r.get("pid")}
    import check_stale_services as _css
    running += [{"model": r["model"], "brief": r.get("brief"), "kind": "scoping",
                 "pid": r["pid"], "key": None, "elapsed_s": r.get("elapsed_s")}
                for r in lane_status.snapshot()["lanes"]
                if r.get("kind") == "scoping" and r.get("server_port", MAIN_PORT) == MAIN_PORT
                and not (_css._ancestors(str(r.get("pid"))) & _jobs)]
    # THE SENIOR STATION FIRST, AND OUTSIDE THE GATE (Jas, 2026-09-28): preflights run on their own
    # server, so a busy main server -- the gate's WAIT below -- must not stop scoped work reaching
    # the coder. Without a senior station, preflights stay in the main priority queue as before.
    senior = senior_station()
    s_cache = {}                    # which targets are red: measured once per tick, shared below

    def s_red(t):
        if t not in s_cache:
            s_cache[t] = pd.target_is_red(t)[0]
        return s_cache[t]
    prefetch_red(chunks_in_task_order(pd.chunks()), s_cache, cached_red(pd.target_is_red))
    s_job = None
    if senior and DEVICE_OF.get(senior.get("tag"), "gx10") in scheduler_commands.paused_devices():
        print(f"  senior station: its device is paused -- nothing started there")
    elif senior:
        s_job = senior_slot(senior, chunks_in_task_order(pd.chunks()), s_red, history, dry_run=dry_run)
    # WHAT THE SENIOR IS ALREADY DOING IS NOT WAITING. `running` holds main-server jobs only (it is
    # the main models' occupancy), so a review the idle senior took would otherwise start here too.
    on_senior = {(r.get("kind"), r.get("key")) for r in ledger_running(history)
                 if senior and r.get("server_port") == senior["port"]}
    if s_job:
        on_senior.add((s_job.get("kind"), s_job.get("key")))
    # A ROLE WHOSE MODEL IS GONE (Jas, 2026-09-29: "If a role is missing a model ... I should get a
    # push notification"). Once when it goes missing and once when it is back -- never every tick.
    if not dry_run:
        try:
            import role_store
            import notify
            now_missing = sorted(f"{k}={t}" for k, t in role_store.missing_models())
            state = REPO / "workspace" / "run-logs" / "missing-models.json"
            was = json.loads(state.read_text()) if state.exists() else []
            if now_missing != was:
                gone, back = sorted(set(now_missing) - set(was)), sorted(set(was) - set(now_missing))
                if gone:
                    notify.send("model-missing", "A role's model is missing",
                                "; ".join(gone) + " -- that role starts no work until it is fixed", url="/models")
                if back:
                    notify.send("model-missing", "A role's model is back", "; ".join(back), url="/models")
                state.write_text(json.dumps(now_missing) + "\n")
            for m in now_missing:
                print(f"  MODEL MISSING  {m}")
        except Exception as e:                                      # noqa: BLE001
            print(f"  NOTE: missing-model check failed: {type(e).__name__}: {e}")
        # THE WEDGED SCHEDULER (t314): a running job waiting past grace on a model that is not
        # resident, has no load logged and no known holder. Recorded to ollama-events.jsonl, which
        # error_scan and session start surface; never restarts anything itself.
        try:
            import stuck_scheduler
            stuck_scheduler.main([])
        except Exception as e:                                      # noqa: BLE001
            print(f"  NOTE: stuck-scheduler check failed: {type(e).__name__}: {e}")
    # WHO IS WORKING ON WHAT (Jas, 2026-09-29): each role's NOW and NEXT, from the same sorting that
    # decides, written for the Hub's tabs (scripts/who_works.py). Before the gate, so a WAIT tick
    # still refreshes it. Never raises.
    if not dry_run:
        try:
            import who_works
            _chunks = chunks_in_task_order(pd.chunks())
            _, _ready, _ = classify(_chunks, running, s_red, history)
            _items = [i for i in priority_items(_chunks, running, s_red, history, preflight=main_preflights(senior))
                      if (i["kind"], i["key"]) not in on_senior and not (senior and i.get("role") in station_roles())]
            _sp = senior["port"] if senior else None
            who_works.record(
                running + [r for r in ledger_running(history) if _sp and r.get("server_port") == _sp],
                senior=senior, senior_started=s_job,
                senior_waiting=SENIOR_TODO[1 if s_job else 0:],
                # THE MANAGER'S OWN ITEMS ONLY (Jas, 2026-10-02: "The manager role is showing it's
                # going to do session reviews next. I took the session review task away"). This read
                # role != "coder", so work NO role has -- held, never started -- showed as its Next.
                manager_waiting=[(i["kind"], i["key"]) for i in _items if i.get("role") == "manager"],
                frontline_waiting=[("production", c["chunk"]) for c in _ready]
                + [(i["kind"], i["key"]) for i in _items if i.get("role") == "coder"],
                main_port=MAIN_PORT,
                # 0 LANES IS OFF (Jas, 2026-10-19): a tile whose role is off shows no Next.
                off=[who_works.STORE_ROLE.get(k, k) for k in task_routing.model_roles(ROUTING)
                     if task_routing.role_lanes(ROUTING, k) == 0])
        except Exception as e:                                      # noqa: BLE001
            print(f"  NOTE: who-works not recorded: {type(e).__name__}: {e}")
    gate = inference_gate(lanes, running, resident)
    # HEADROOM IS A SECOND QUESTION, ASKED AFTER THE PATH ONE AND KEPT OUT OF THE GATE.
    # inference_gate's contract is pinned line-by-line by a frozen oracle
    # (scripts/tests/test_inference_busy.py), so the room check lives here rather than
    # inside it: a usable path with no room to load is a WAIT for the same reason a busy
    # slot is -- the work is not possible now and something is holding the memory.
    if gate["verdict"] == "up":
        block = memory_block(models, snap)
        if block:
            gate = {"verdict": "wait", "holder": block["holder"], "say": block["say"]}
    if gate["verdict"] == "up":
        print("  inference path: up -- " + gate["say"])
    else:
        print(f"  {gate['verdict'].upper()} -- {gate['say']}"
              + (f" (holder: {gate['holder']})" if gate.get("holder") else ""))
    if gate["verdict"] == "refused":
        record({"event": "tick", "refused": "inference path down", "say": gate["say"]})
        return 2
    if gate["verdict"] == "wait":
        record({"event": "tick", "waited": "busy slot", "holder": gate["holder"],
                "say": gate["say"]})
        return 2
    chunks = chunks_in_task_order(pd.chunks())
    red_cache = s_cache             # the senior slot already ran target_is_red for these; reuse it

    def red_of(t):
        if t not in red_cache:
            red_cache[t] = pd.target_is_red(t)[0]
        return red_cache[t]

    # ---- PRIORITY: queued, interrupting production on the model it needs
    # The session-review backstop: sessions closed without session_end, idle 24 h (a glob, cheap).
    if not dry_run:
        try:
            import session_review
            for n in session_review.discover():
                print(f"  session review queued (backstop): {n}")
        except Exception as e:                                      # noqa: BLE001
            print(f"  NOTE: session-review discover failed: {e}")
        # THE ROUTINE AUDIT OF THE REVIEW SCRIPT (Jas, 2026-10-07): scored on the gold set once a day
        # or when the script changes. DETACHED, ONE AT A TIME (2026-10-09). It was a child with a
        # 300 s timeout on the assumption it took ~20 s; the meaning check makes it ~923 s, so it never
        # finished, never recorded, and -- session_review.py having changed -- ran again EVERY tick,
        # holding the scheduler's lock 5 minutes each time: the line sat at one lane while ticks
        # meant for every 2 minutes skipped. Now it runs in its own session under its own flock and
        # the tick moves on; a second tick finds the lock held and starts nothing.
        # THE THINKPAD eGPU CYCLE (Jas, 2026-10-14; thinkpad_cycle.py): Auditor until 01:00, Frontline
        # until nothing is ready to build, then the Auditor -- one decision per tick, drain before load.
        try:
            import thinkpad_cycle
            print(f"  thinkpad-cycle: {thinkpad_cycle.step()}")
        except Exception as e:                                      # noqa: BLE001 -- said; the tick goes on
            print(f"  NOTE: thinkpad-cycle step failed ({type(e).__name__}: {e})")
        # INTAKE BEFORE SCOPING (Jas, 2026-10-14; intake.py): the embedder maps the next queue tasks to the
        # code they will touch, so scope_batch links them. Detached, one at a time, every 10 min at most.
        try:
            with open(REPO / "workspace" / "run-logs" / "intake.log", "a") as ilog:
                subprocess.Popen(["flock", "-n", str(REPO / "workspace" / "run-logs" / "intake.lock"), sys.executable,
                                  str(REPO / "scripts" / "intake.py"), "tick"],
                                 cwd=str(REPO), stdout=ilog, stderr=ilog, stdin=subprocess.DEVNULL,
                                 start_new_session=True)
        except OSError as e:
            print(f"  NOTE: intake not started ({e})")
        try:
            audit_log = REPO / "workspace" / "run-logs" / "session-review-audit.log"
            with open(audit_log, "a") as alog:
                subprocess.Popen(["flock", "-n", str(REPO / "workspace" / "run-logs" / "session-review-audit.lock"), sys.executable,
                                  str(REPO / "scripts" / "session_review_audit.py"), "tick"],
                                 cwd=str(REPO), stdout=alog, stderr=alog, stdin=subprocess.DEVNULL,
                                 start_new_session=True)
            print("  session-review audit: started in the background (one at a time; "
                  "workspace/run-logs/session-review-audit.log)")
        except OSError as e:
            print(f"  NOTE: session-review audit did not start: {type(e).__name__}: {e}")
        # EVERY STARTED JOB REACHES ONE TERMINAL ROW (t230): a job whose process is gone with no
        # finished row gets one, marked lost, so nothing ends silently. It never raises.
        # THE ADMISSION LEDGER, OBSERVATION ONLY (step 1 of the redesign, 2026-10-29). It books what the box has
        # promised and prints that beside what it measures -- host bytes for builds and suites, sequence slots
        # and KV tokens for inference, which are DIFFERENT resources: measured, four concurrent inference
        # requests moved host MemAvailable by -0.46 GB, because the engine's memory is a preallocated pool.
        # Nothing gates on it yet; the numbers decide the reservations before they decide anything else.
        try:
            import admission_budget as _al
            print(f"    {_al.report()}")
        except Exception as e:                                       # noqa: BLE001 -- said, never fatal
            print(f"    NOTE: admission report skipped: {type(e).__name__}: {e}")
        try:
            for lr in account_lost():
                print(f"  LOST: {lr['kind']} {lr['chunk']} (pid {lr['pid']}) ended with no record -- accounted")
        except Exception as e:                                      # noqa: BLE001
            print(f"  NOTE: lost-job accounting failed: {type(e).__name__}: {e}")
        # THE MEMORY LEDGER (t456): one memory sample per tick, and any new model loads parsed from
        # the Ollama journal. Both cheap (a /proc read, a journalctl since the last cursor).
        try:
            import memory_ledger
            memory_ledger.sample()
            memory_ledger.loads()
        except Exception as e:                                      # noqa: BLE001
            print(f"  NOTE: memory ledger not recorded: {type(e).__name__}: {e}")
        # STANDING PROCESSES ARE RECORDED WHEN THEY CHANGE (t643): cron lines and enabled user units,
        # one row per change in workspace/process-changes.jsonl. It never raises.
        try:
            import process_registry
            ch = process_registry.record()
            if ch:
                print(f"  standing processes changed: +{ch['added']} -{ch['removed']}"
                      + (f"  OFF THE FLOW CHART: {ch['off_chart']}" if ch["off_chart"] else ""))
        except Exception as e:                                      # noqa: BLE001
            print(f"  NOTE: process registry not recorded: {type(e).__name__}: {e}")
        # THE FREEZE CIRCUIT IS THE MANAGER'S (Jas, 2026-10-01): if the bridge's auto-freeze is off
        # for weak oracles, the Manager gets one task to review and reset it. No model call here.
        try:
            import freeze_circuit
            t = freeze_circuit.ensure()
            if t:
                print(f"  freeze circuit: off -- the Manager's task is {t}")
        except Exception as e:                                      # noqa: BLE001
            print(f"  NOTE: freeze-circuit check failed: {type(e).__name__}: {e}")
        # THE SCOPE-QUALITY LOOP (Jas, 2026-09-28): every defect any check recorded goes to the
        # Manager's queue as its class's RCA task (or a note on the open one). No model call.
        try:
            import scope_quality
            for key, action in scope_quality.route():
                print(f"  scope defect {key}: {action}")
        except Exception as e:                                      # noqa: BLE001
            print(f"  NOTE: scope-defect routing failed: {type(e).__name__}: {e}")
        # FAILED BUILDS, DECIDED BY RULE OR ESCALATED BY THE ORG CHART (Jas, 2026-10-19: "Lets get rid of
        # the supervisor"). No model and no role: supervise.decide_pending() applies the rules and sends
        # what they cannot explain to the rework seat (role_store.escalate). No model call.
        try:
            import supervise
            for chunk, dec, action in supervise.decide_pending(history):
                print(f"  failed build {chunk}: {dec}: {action}")
        except Exception as e:                                      # noqa: BLE001
            print(f"  NOTE: failed-build decisions failed: {type(e).__name__}: {e}")
    items = priority_items(chunks, running, red_of, history, preflight=main_preflights(senior))
    # (on_senior, computed above: what the senior already runs is not waiting here)
    items = [i for i in items if (i["kind"], i["key"]) not in on_senior]
    # SESSION REVIEWS AND RCAs ARE THE SENIOR'S (Jas, 2026-09-29: "move the manager to scoping and
    # leave the senior on session reviews"). With a senior station up, the main server does not take
    # them, so they no longer hold the manager queue's YIELD flag and it scopes; the senior runs them
    # after its preflights (senior_slot). No senior station: they stay here, as before.
    if senior:
        items = [i for i in items if i.get("role") not in station_roles()]
    # NO TEAM WORK FILTER (Jas, 2026-10-14): a role works what is on its list even while the senior is
    # working the same kind -- the Team Work box was deleted.
    # BELOW SCOPING WAITS FOR SCOPING (2026-09-30). The manager queue stands down for tasks ABOVE
    # Scoping on the Manager's list (the YIELD flag, below); nothing did the reverse. With Scoping
    # moved above RCA and Session review on the Models tab, a freed Q4 went to the next session review
    # -- which then waits up to 6 h for the lease -- and the scoping queue lost every race. Held only
    # while the queue has a task and is alive, so a stopped queue never holds anything.
    # PARALLEL SCOPING REPLACES THE HOLD (2026-10-08). Scoping takes its turn in the lane rotation
    # like every other task: scoping_items() adds one stand-in per task the queue could scope, and the
    # live workers count as jobs on the model. What Scoping wins is written as the queue's GRANT,
    # never started here. hold_below_scoping (one lane kept for a single serial scoper) is retired.
    behind_scoping = []
    scope_items, scope_running = scoping_items(running, model_slots)
    items = items + scope_items
    if not dry_run:
        try:
            for x in no_role_fallback(items):
                print(f"  NO ROLE  {x}")
        except Exception as e:                                      # noqa: BLE001
            print(f"  NOTE: no-role fallback failed: {type(e).__name__}: {e}")
    try:
        priority_models = list(dict.fromkeys(list(models) + main_role_models()))
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: main-server role models unreadable ({type(e).__name__}: {e}); "
              f"priority work limited to the build models")
        priority_models = list(models)
    # A MODEL THAT DOES NOT ANSWER GETS NO WORK (2026-10-19). inference_gate asks one question for
    # the whole box, and "a running job vouches for it" answers it without a probe -- so after Jas
    # resumed the GX10, a ThinkPad build vouched for Swift while vLLM was still loading (743 s).
    # The 13:17:45 tick handed Swift three jobs, all three died at once with "NoServer: 'swift' is
    # offered by vllm-swift, and none answers", and the Manager's scoping grant went to them. Asked
    # per model here, each tick: a loading model waits for the tick after it answers.
    try:
        _down = unready_models(priority_models)
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: per-model readiness not checked ({type(e).__name__}: {e})")
        _down = {}
    for m, why in _down.items():
        print(f"  NOT READY {m}: {why} -- no work assigned to it this tick")
    if _down:
        priority_models = [m for m in priority_models if m not in _down]
        models = [m for m in models if m not in _down]
    # WHICH MODELS HAVE A BUILD READY, so Building takes its turn in the lane rotation.
    build_hold = None
    try:
        _ready_now = classify(chunks, running, red_of, history)[1]
        # HOW MANY builds are ready, per model (2026-10-08: Building's Max lanes is editable).
        build_ready = {m: len(_ready_now) for m in models} if _ready_now else {}
        # NO BUILD LANE WHEN NO BUILD CAN START (2026-10-16). Building won a lane in the rotation
        # every tick, then memory_admit skipped every build ("a build needs 12 + 4 GB"), so one of
        # Swift's 4 lanes sat idle while 68 RCAs waited. Offer the lane only if a build fits.
        _avail = mem_available_gib()
        if build_ready and _avail is not None and \
                _avail - (swift_pending_gib() or 0) - build_charge_gib()[0] < ADMIT_MARGIN_GIB:
            print(f"  no build lane this tick: {_avail:.1f} GB free cannot admit a build")
            build_hold = (f"{_avail:.1f} GB free cannot admit a build ({build_charge_gib()[0]:.1f} GB + "
                          f"{ADMIT_MARGIN_GIB:.0f} GB margin)")
            build_ready = {}
        # A DIP SMALLER THAN A TICK IS STILL A DIP (2026-10-29). Measured: the hold released at 01:32:05Z at
        # avg60 8.17% and was back at 49% by 01:35Z, so the window existed and no build was admitted into it.
        # The decision is made in one pure place (pressure_build_gate) because doing it inline let a released
        # hold still empty the ready builds -- a bug a test of the wait alone could not see.
        # R4: PRESSURE ANNOTATES, BYTES GATE (2026-10-29). Measured on this box: a tick reported "20.4 GB free"
        # against a 16 GB build need -- bytes admitted the build -- while refusing it because PSI was 73% over
        # the last minute. PSI on this machine measures the stall of getting SWAPPED pages back (26 GB of a 48 GB
        # swap in use), not "there is no room": as a latch it starves the line, as a number it is useful. So once
        # the BYTE gate is switched on, pressure is printed and no longer refuses; while that gate is off, this
        # behaves exactly as before, which is what keeps the change reversible.
        _gated_by_bytes = admission_gate_on()
        _psi = pressure_hold() if (build_ready and not _gated_by_bytes) else None
        if build_ready and _gated_by_bytes:
            _ph = pressure_hold()
            if _ph:
                print(f"    PRESSURE  {_ph} -- annotated, not refused: the byte gate decides admission")
        build_ready, _hold, _waited = pressure_build_gate(build_ready, _psi)
        if _hold:
            print(f"  no build lane this tick: {_hold}")
            build_hold = str(_hold)
        elif _waited:
            print(f"    PRESSURE  released after {_waited}s of waiting -- a build may start")
    except Exception as e:                                          # noqa: BLE001 -- said, builds wait as before
        print(f"  NOTE: ready builds unknown ({type(e).__name__}: {e}); no build lane this tick")
        build_ready = {}
    p_starts, preempt_pids, queued, reserved = schedule(priority_models, items, running + scope_running,
                                                        can_checkpoint=coder_can_checkpoint(),
                                                        slots=model_slots, build_ready=build_ready)
    won = [i for i in p_starts if i.get("scoping_standin")]
    p_starts = [i for i in p_starts if not i.get("scoping_standin")]
    queued = [(i, why) for i, why in queued if not i.get("scoping_standin")]
    if not dry_run and (scope_items or scope_running):
        try:
            import scoping_lanes
            scoping_lanes.write_grant(len(scope_running) + len(won))
            print(f"  scoping: {len(scope_running)} running, {len(won)} more granted")
        except Exception as e:                                      # noqa: BLE001 -- said, not swallowed
            print(f"  NOTE: scoping grant not written ({type(e).__name__}: {e})")
    queued += [(i, "waiting for Scoping: it is above this on the Manager's list and the manager queue "
                   "has a task") for i in behind_scoping]
    # PRIORITY WORK BEHIND THE MANAGER ASKS IT TO YIELD (2026-09-26). The manager queue starts
    # its next task the instant one ends; this tick runs every 10 min, so it lost every race
    # and 11 preflights waited all night behind scoping. The flag tells manager_queue.sh to
    # pause between tasks; the next tick finds the model free. No flag, no pause.
    if not dry_run:
        # Held up while ANY priority item is waiting or starting: once the first preflight
        # runs, the rest read "queued behind preflight", and dropping the flag then would hand
        # the model back to the manager after every single review.
        # SCRIPT REVIEWS DO NOT PAUSE THE MANAGER (2026-09-26): they follow every commit, unblock
        # no build, and on a day of many commits held the manager for ~2-3 h of 5. Preflights
        # (a build waits on them) and supervision (a failure waits on it) still do.
        # FROM THE STORE (plan step 4): the manager stands down for a task ABOVE Scoping on its own
        # list, and for a station role's work folded onto its model while that station is down.
        _yk, _st = task_routing.yield_kinds(ROUTING), station_roles()
        behind = [i for i in [q for q, _ in queued] + p_starts
                  if i.get("kind") in _yk or i.get("role") in _st]
        flag = REPO / "workspace" / "run-logs" / "scoping-queue" / "YIELD"
        # NO YIELD UNDER THE ROTATION (2026-10-08): Scoping now holds only the lanes it was granted,
        # so pausing it for higher work would starve it twice. The flag is only ever cleared.
        behind = []
        try:
            if behind:
                flag.write_text(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                            "waiting": len(behind)}) + "\n")
            elif flag.exists():
                flag.unlink()
        except OSError as e:
            print(f"  NOTE: manager yield flag not written: {e}")
    # ---- CONSOLE MESSAGES: first in line. Jas, 2026-10-12, superseding "I don't want it to stop a
    # production": the Console's Interrupt box decides; on, it takes the lowest-priority job's lane.
    console_starts = []
    prod_models = [m for m in models if m not in reserved]
    try:
        sys.path.insert(0, str(REPO / "projects" / "local-inference"))
        import console as _console
        waiting_msgs = [r for r in _console.pending_messages()
                        if ("console", r["id"]) not in {(x.get("kind"), x.get("key")) for x in running}]
    except Exception as e:                                          # noqa: BLE001
        waiting_msgs = []
        print(f"  NOTE: console queue unreadable: {type(e).__name__}: {e}")
    console_starts, prod_models = assign_console(
        waiting_msgs, prod_models, running + scope_running, p_starts, slots=model_slots,
        interrupt=task_routing.interrupts(ROUTING, "console"), can_checkpoint=coder_can_checkpoint(),
        preempt=preempt_pids)
    # ---- PRODUCTION: only on models no priority work is waiting for
    # The priority jobs just picked hold lanes too, so plan() sees them as running.
    # EVERY LANE IN USE OR PROMISED COUNTS (2026-10-08: 4 builds + 2 scoping workers ran on 4 lanes,
    # swap filled and the box had to be rebooted). plan() saw only the scheduler's own jobs; the
    # scoping workers (scope_running) and the lanes just granted to Scoping (won) were invisible to it,
    # so builds took every lane and the scoping service started its grant on top.
    taken = running + p_starts + scope_running + won
    starts, skipped, idle = plan(prod_models, chunks, taken, red_of, history=history,
                                 reviewer=None, slots=model_slots)
    skipped = [(n, w) for n, w in skipped if not str(w).endswith(REVIEW_WAIT_TAIL)]
    skipped = name_build_hold(skipped, build_hold)
    starts, skipped = memory_admit(starts, skipped)
    p_starts, console_starts, starts = device_hold(p_starts, console_starts, starts, queued, skipped)
    if not dry_run:
        red_watchdog(skipped)
    if not dry_run:
        try:
            for line in route_waiting(skipped):
                print(f"  {line}")
        except Exception as e:                                      # noqa: BLE001 -- said, not swallowed
            print(f"  NOTE: parked/held routing failed ({type(e).__name__}: {e})")
        # SCOPING LOOPS (2026-10-24): the same step declared LOOP_N passes in a row goes to the technical
        # seat once, with the evidence (scripts/loop_breaker.py). Build-side loops park in parked().
        try:
            import loop_breaker
            _pl = loop_breaker.PROGRESS
            _lines = _pl.read_text(encoding="utf-8", errors="replace").splitlines() if _pl.exists() else []
            for tid, step in loop_breaker.apply_scoping(loop_breaker.scoping_loops(_lines)):
                print(f"  LOOP      {tid}: scoped {loop_breaker.LOOP_N}x as {step} -> technical seat")
        except Exception as e:                                      # noqa: BLE001 -- said, not swallowed
            print(f"  NOTE: scoping loop check failed ({type(e).__name__}: {e})")

    print(f"  {len(running)} job(s) running, {len(items)} priority item(s), {len(chunks)} chunk(s) enrolled")
    for r in running:
        print(f"    RUNNING   {r['kind']:13} {r['model']:26} {r.get('brief') or r.get('key') or ''}")
    for i in p_starts:
        print(f"    PRIORITY  {i['kind']:13} {i['model']:26} -> {i['key']}")
    for i in console_starts:
        print(f"    CONSOLE   message       {i['model']:26} -> {i['key']}")
    for i, why in queued:
        print(f"    queued    {i['kind']:13} {i['key']}: {why}")
    for pid in preempt_pids:
        print(f"    INTERRUPT production pid {pid} for priority work")
    for j in starts:
        sw = f"  (switch {j['switch_number']} from {j['switch_from']})" if j.get("switch_from") else ""
        print(f"    START     production    {j['model']:26} -> {j['chunk']}{sw}")
    for name, why in skipped:
        print(f"    skip      {name:18} {why}")
    if dry_run:
        print("  --dry-run: nothing started, interrupted or recorded.")
        return 0

    YIELD_DIR.mkdir(parents=True, exist_ok=True)
    wanted = {i["model"] for i in items}
    for m in models:
        flag = YIELD_DIR / f"{_safe(m)}.flag"
        # YIELD ONLY WHEN THE MODEL HAS ONE LANE (2026-10-08). Written for one lane, where a build
        # held the whole model; with several, the lane rotation already gives priority work its
        # turn, and the flag made every build give its lane back after attempt 1 -- builds looped
        # and none finished (t800, t801, t802 at 4 lanes).
        if (m in wanted and model_slots(m) <= 1
                and any(r["model"] == m and r["kind"] == "production" for r in running)):
            flag.write_text("priority work waiting\n")
        elif flag.exists() and (m not in wanted or model_slots(m) > 1):
            flag.unlink()                       # nothing waiting, or lanes to share: do not yield
    import signal
    for pid in preempt_pids:
        try:
            os.kill(int(pid), signal.SIGTERM)
            record({"event": "preempted", "pid": pid,
                    "model": next((r["model"] for r in running if r.get("pid") == pid), None),
                    "for": [i["key"] for i in items]})
        except (OSError, ValueError) as e:
            print(f"    could not interrupt pid {pid}: {e}")
    # NOBODY RUNS A BUG CHECK BY HAND (Jas, 2026-09-14: "I won't be running scripts to check
    # for bugs. That's something a model needs to do."). Every tick tops up the compliance chunks
    # from the library's checks, up to the WIP limit; the models fix them.
    try:
        import library
        library.enroll_compliance()
    except Exception as e:                                          # noqa: BLE001
        print(f"    NOTE: compliance enrolment skipped: {type(e).__name__}: {e}")
    for j in p_starts + console_starts + starts:
        # Priority work is re-checked against the model's lanes at launch: a finishing job's fast
        # path may have filled one since this tick read the ledger. Builds keep the tick's own rules.
        pid, log = launch(j, cap=None if j.get("kind") == "production" else model_slots(j["model"]))
        if pid:
            print(f"    started pid {pid}, log {log.relative_to(REPO)}")
    write_tick_row(running=len(running), priority=len(items), started=len(p_starts) + len(starts),
                   preempted=preempt_pids,
                   queued=[{"kind": i["kind"], "key": i["key"], "why": w} for i, w in queued],
                   skipped=[{"chunk": n, "why": w} for n, w in skipped], idle=idle)
    return 0


def status():
    h = rows()
    print(f"  ledger: {LEDGER.relative_to(REPO)}  ({len(h)} rows)")
    ticks = [r for r in h if r.get("event") == "tick"]
    print(f"  last tick: {ticks[-1]['at'] if ticks else 'NEVER -- the scheduler has not run'}")
    # A RECORD FROM AN OLDER WRITER IS STILL A RECORD (2026-10-29): every field here is read with .get(),
    # because a finished row written before a field existed took the whole command down with a KeyError --
    # `dispatch_loop.py --status`, the tool anyone reaches for when the line is stuck. A status command that
    # crashes on the very rows it is asked about is worse than one that prints `?`.
    for r in [r for r in h if r.get("event") in ("started", "finished")][-14:]:
        kind = r.get("kind", "production")
        if r.get("event") == "started":
            tail = f"pid {r.get('pid', '?')}" + (f"  SWITCH {r.get('switch_number', '?')} from "
                                                 f"{r.get('switch_from')}"
                                                 if r.get("switch_from") else "")
        else:
            ok = ("CLEARED" if r.get("accepted") else "OBJECTED") if kind == "preflight" else \
                 ("ACCEPTED" if r.get("accepted") else "not accepted")
            tail = f"exit {r.get('exit', '?')} {ok} {r.get('seconds', '?')}s"
        print(f"    {r.get('at', '?')}  {r.get('event', '?'):8} {kind:10} "
              f"{str(r.get('chunk')):18} {str(r.get('model')):24} {tail}")
    import parallel_dispatch as pd
    print("\n  per chunk: failures, switches, state")
    for c in pd.chunks():
        sha = brief_sha(c["brief"])
        if not sha:
            continue
        f = failures(c["chunk"], sha, h)
        sw = len([r for r in h if r.get("event") == "started" and r.get("chunk") == c["chunk"]
                  and r.get("brief_sha256") == sha and r.get("switch_from")])
        state = "PARKED" if parked(c["chunk"], sha, h)[0] else review_state(sha)
        print(f"    {c['chunk']:20} failed on {len(f)} model(s)  switches {sw}/{MAX_SWITCHES}  {state}")
    return 0


# WHY WORK LANDS ON CLAUDE, COUNTED (t1056's oracle, built 2026-10-12). AUTO_ASSIGNED is the
# only machine-readable account of why a chunk reached Claude, and nothing read it back: on
# 2026-10-12 129 of its 175 entries were "HELD: red on main" -- one orphaned ceiling entry, not a
# model failure -- and the only way to see that was counting reason strings by hand. Each class
# is an anchored regex over the opening words the code that emits it writes; anything else is
# "unclassified", never absorbed by its nearest neighbour.
INTAKE_REASONS = (
    ("baseline-refusal", re.compile(r"HELD: red on main, so every sandbox baseline would refuse\b")),
    ("attempt-failed-exit-3", re.compile(r"PARKED: \d+ attempt\(s\) in a row came to nothing \(exit 3;")),
    ("attempt-failed-exit-1", re.compile(r"PARKED: \d+ attempt\(s\) in a row came to nothing \(exit 1;")),
    ("harness-bug-park", re.compile(r"PARKED: BLOCKED BY A HARNESS BUG:")),
    ("preflight-objection", re.compile(r"HELD FOR CLAUDE: .+? objected to this brief\b")),
    ("needs-scope", re.compile(r"PARKED: NEEDS-SCOPE:")),
    # The supervising role's own park (parked(): "<role>, supervising, parked it"). Missing from the
    # classes copied on 2026-10-12 because none was in the ledger that day; 2026-10-14 one was, and
    # test_dispatch_intake_counts held every build as red on main.
    ("supervisor-park", re.compile(r"PARKED: .+?, supervising, parked it\b")),
    ("still-red", re.compile(r"PARKED: STILL RED: the target suite stayed red\b")),
    ("delivered-awaiting-review", re.compile(r"DELIVERED, waiting on Claude's review: \S+")),
)
INTAKE_FIXES = {
    "baseline-refusal": "a red suite on main: give it an open owner in workspace/test-failures-ceiling.json "
                        "(test_ceiling_owned) or make it pass -- one entry holds every build",
    "attempt-failed-exit-3": "the coder produced no change: the brief must name its target -- a SYMBOLS: line "
                             "and the anchor text -- then edit the brief to release it",
    "attempt-failed-exit-1": "the oracle stayed red after the change: send it back to the scoper with the "
                             "failure tail to revise the brief or the oracle (t1071)",
    "harness-bug-park": "fix the harness task named by supervise.py --failures, then record it with "
                        "dispatch_loop.py --harness-fixed",
    "preflight-objection": "return the brief to the scoper with the objection as evidence (t1071); Claude "
                           "only on a second objection",
    "needs-scope": "split the brief or narrow it with SYMBOLS: so the target fits the model's window",
    "supervisor-park": "read the supervisor's decision in workspace/supervision.jsonl; return the brief to "
                       "the scoper with it as evidence (t1071)",
    "still-red": "the scoper revises the brief from the failing output in the job log (supervision rule 3); "
                 "a second identical failure escalates up the org chart",
    "delivered-awaiting-review": "merge or reject the branch; widen auto_merge's tolerances where the "
                                 "review was mechanical (t1072)",
    "unclassified": "a reason nobody has dispositioned is a finding: DISPOSITION it -- add its class "
                    "and fix here, or stop the code that emits it",
}


def classify_reason(why):
    """The intake class of one AUTO_ASSIGNED reason string; 'unclassified' when no class matches."""
    why = str(why or "")
    for label, rx in INTAKE_REASONS:
        if rx.match(why):
            return label
    return "unclassified"


def intake_counts(path=None):
    """{class: count} over the ledger at `path` (default AUTO_ASSIGNED); only classes present."""
    try:
        entries = json.loads(Path(path or AUTO_ASSIGNED).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        entries = {}
    counts = {}
    for v in (entries.values() if isinstance(entries, dict) else []):
        label = classify_reason(v.get("why", "") if isinstance(v, dict) else "")
        counts[label] = counts.get(label, 0) + 1
    return counts


def intake_report(path=None):
    """Print '<count> <class>' biggest first, then '<n> total', then each class's fix."""
    counts = intake_counts(path)
    for label, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"{n} {label}")
    print(f"{sum(counts.values())} total")
    for label, _n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {label}: fix -- {INTAKE_FIXES[label]}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--models", default=None)
    ap.add_argument("--run-job", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--harness-fixed", default=None, metavar="WHY",
                    help="record a harness fix now: failures before it stop parking briefs")
    ap.add_argument("--intake", nargs="?", const="", default=None, metavar="LEDGER",
                    help="count why work reached Claude, by class, with each class's fix")
    a = ap.parse_args()
    if a.intake is not None:
        return intake_report(a.intake or None)
    if a.harness_fixed:
        HARNESS_FIXED.write_text(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                             "why": a.harness_fixed}) + "\n")
        print(f"harness fix recorded: {HARNESS_FIXED.read_text().strip()}")
        return 0
    if a.run_job:
        return run_job(json.loads(a.run_job))
    if a.status:
        return status()
    return tick(a.dry_run, [m.strip() for m in a.models.split(",")] if a.models else None)


if __name__ == "__main__":
    sys.exit(main())
