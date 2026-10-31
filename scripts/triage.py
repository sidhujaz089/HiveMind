#!/usr/bin/env python3
"""Ask the local model what the UNRECOGNISED findings are, once a day, while Jas is asleep.

    python3 scripts/triage.py                 triage what error_scan cannot classify
    python3 scripts/triage.py --dry-run       show what would be asked, ask nothing
    python3 scripts/triage.py --self-test

WHY THIS EXISTS, AND WHY IT IS NOT A GREP
------------------------------------------
`error_scan.py` matches KNOWN shapes. Jas, 2026-09-10: *"if we use grep we are limited to what
it searches for in its set library of terms... then we have to make a process that updates that
library."* He is right, and a list nobody updates is the watchdog again.

So the scanner enumerates the GOOD and surfaces everything else. `done_reason` is healthy only
when it is `stop`; an outcome string outside the nine the ledger has ever held is unrecognised;
an exit code that is not 0 is unrecognised. A failure shape nobody has seen surfaces as an
UNKNOWN the first time it happens, without anyone having predicted it. That is what this file
triages, and it is the only part of the pipeline that needs a model.

Two failures from 2026-09-10 are the reason the anomaly half exists at all, because no
vocabulary of error strings would have contained either:

    seven attempts, each 616 tokens, each 58 seconds, byte-identical -- a repeat loop
    rc=0 reported three times for dispatches that wrote nothing                 -- a lying wrapper

WHEN IT RUNS, AND WHY NOT AT A SESSION BOUNDARY
-----------------------------------------------
A systemd timer at 02:00, `Persistent=true`, with `Linger=yes` already set on this user -- so
it runs with nobody logged in, and a window missed because the HiveMind was off runs at the
next boot instead of being lost.

NOT at session start: that is 2.5 seconds today and a model call is 4-20 minutes. A slow
session start is one people skip (AGENTS.md rule 9), which is how the watchdog died.

NOT at session end either, and this was Jas's correction: *"most times a session ends because
the context window is full and we start a new one right away."* A 20-minute job at session end
holds the lease into the session that begins seconds later, so the first dispatch of the new
session is refused by its own interlock. That is `t253` in a new costume.

THE TIMER IS NOT TRUSTED TO REPORT ITSELF
-----------------------------------------
A timer that stops silently IS the watchdog failure. So the pairing, not the timer, is the
mechanism: this writes `workspace/triage-state.json` every run, and `session_start.py` reads
its timestamp and says out loud when the last triage is older than the newest finding. The
timer does the work; the surface Jas reads every day owns the question of whether it ran.

WHAT IT MAY NOT DO
------------------
**It may not classify anything as benign by itself.** It writes PROPOSALS. A benign
classification silences a signal permanently, and the party asking for silence is the one least
entitled to grant it -- that is how `--no-verify` habits form. Same boundary as `coder.py`: it
produces something for review, it does not merge. Jas, on exactly this risk: *"could a future
session of you or a local model KNOW a bad thing and sort it as known and break the filter?"*
It could, so it cannot be the one to decide.

THE CONTRACT
------------
    shapes(findings)            -> the unknowns deduplicated BY SHAPE, not per occurrence
    triage(shapes, ask, limit)  -> proposals; `ask` is injected so this is testable offline
    state()                     -> {"last_run": iso, "proposed": n, "pending": n} or None

A shape is one dict per distinct unknown, carrying `key`, `count`, `first_seen`, `last_seen`
and ONE `example` with its full evidence. Two hundred rows of one unrecognised `done_reason`
are ONE shape -- Jas's queue reached 42 items because nothing deduplicated, and a triage queue
that grows with the log instead of with the vocabulary is the same defect.

A proposal is `{key, disposition, why, evidence, proposed_at, proposed_by}` where disposition is
"defect" or "benign". Nothing else. It is written to `workspace/triage-proposals.jsonl` and
read by a human.

IMPLEMENTATION BELOW THIS LINE -- see scripts/tests/test_triage.py for the contract.
"""

STATE = "workspace/triage-state.json"
PROPOSALS = "workspace/triage-proposals.jsonl"

# At most this many shapes per run. Unbounded triage makes every run cost the whole backlog,
# and the run has a machine to itself for a reason -- it should still end before morning.
DEFAULT_LIMIT = 12

DISPOSITIONS = ("defect", "benign")

# Attribution is the process that guessed, not Jas: the human who merges a proposal is a
# different party, and the row says who is accountable for the guess.
PROPOSED_BY = "scripts/triage.py (local model)"

import json
from datetime import datetime, timezone
from pathlib import Path

import jsonx


def _now():
    """UTC now, ISO-8601. The 02:00 timer is judged by these timestamps, so they must not
    drift into local time."""
    return datetime.now(timezone.utc).isoformat()


def shapes(findings):
    """One dict per DISTINCT unknown -- key, count, first_seen, last_seen and one example
    with its evidence intact. Count descending, so the loudest shape is triaged first."""
    groups = {}
    for f in findings:
        g = groups.get(f["key"])
        if g is None:
            g = groups[f["key"]] = {"key": f["key"], "count": 0, "example": f, "ats": []}
        g["count"] += 1
        if f.get("at"):
            g["ats"].append(f["at"])
    out = []
    for g in groups.values():
        first = min(g["ats"]) if g["ats"] else g["example"].get("at")
        last = max(g["ats"]) if g["ats"] else g["example"].get("at")
        out.append({"key": g["key"], "count": g["count"], "first_seen": first,
                    "last_seen": last, "example": g["example"]})
    out.sort(key=lambda x: -x["count"])
    return out


def _prompt(shape):
    # The evidence must reach the model: a triage on a shape it cannot see is the
    # brief-as-a-path failure of 2026-09-10 all over again. The whole example goes in,
    # not just the key.
    return (
        "The error scan found a failure shape it cannot classify. "
        "Decide whether it is a 'defect' (a real failure) or 'benign' (expected behaviour); "
        "those two strings are the only dispositions. "
        f"Shape key: {shape['key']}\n"
        f"Seen {shape['count']} time(s); first {shape['first_seen']}, last {shape['last_seen']}\n"
        "Example finding, evidence intact:\n"
        f"{json.dumps(shape['example'], indent=2)}\n"
        'Reply with one JSON object: {"disposition": "defect" or "benign", "why": "one sentence"}. '
    )


def triage(shapes, ask, limit=DEFAULT_LIMIT):
    """At most `limit` proposals. FAIL CLOSED: a reply that does not parse, or that names
    a disposition outside DISPOSITIONS, yields no proposal for that shape -- an unreadable
    answer is not a classification (the same rule the preflight fail-safe enforces)."""
    proposals = []
    for shape in shapes[:limit]:
        parsed = jsonx.one(ask(_prompt(shape)) or "", ["disposition"])
        if parsed["status"] != "ok":
            continue
        disposition = parsed["obj"].get("disposition")
        if disposition not in DISPOSITIONS:
            continue
        proposals.append({"key": shape["key"], "disposition": disposition,
                          "why": parsed["obj"].get("why"), "evidence": shape["example"],
                          "proposed_at": _now(), "proposed_by": PROPOSED_BY})
    return proposals


def record_state(path, proposed, pending):
    """Write {"last_run", "proposed", "pending"} as JSON. It happens every run, even an
    empty one: session_start reads last_run to say whether the 02:00 timer actually fired,
    and a silent run and a skipped run are the same failure."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"last_run": _now(), "proposed": proposed,
                                "pending": pending}, indent=2) + "\n",
                    encoding="utf-8")


def state():
    """The last run's record, or None when there is none. Unreadable is treated the same as
    missing: the safe reading is 'the timer has not run since you can tell'."""
    try:
        return json.loads(Path(STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
