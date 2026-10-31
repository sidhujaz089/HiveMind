#!/usr/bin/env python3
"""Fail-first oracle for production liveness (2026-10-24).

WHAT IT IS FOR. On 2026-10-24 no production dispatch had been accepted since 11:23Z and
nothing said so: eleven refusals, a paused device and ~20 parked chunks were all visible
only to a person who ran `dispatch_loop.py --dry-run`. The data to see it was already in
the ledgers -- `tick` rows carry `running`/`started`/`skipped[{chunk,why}]`, and `finished`
rows carry `kind` + `accepted` -- so what was missing was an assertion, not a recorder.

THE TWO WAYS THIS CHECK COULD BE NOISE, both asserted here:
  - it must NOT fail while a human has deliberately stopped the machine (a paused flag, or
    every device paused by a person is PAUSED, which is a pass that still names who and why);
  - it must NOT fail while work is actually running, even when nothing has been accepted for
    longer than the threshold.
And the one way it could be a false pass: a box that is doing nothing for no declared reason
must be STALLED, not quiet -- including when the only pause was a memory guard stopping
itself, because nobody chose that.

Every case is synthetic rows in temp files: no live ledger is read and none is written.
"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import check_workspace as cw  # noqa: E402

PASSED = FAILED = 0


def check(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def check_true(name, got):
    check(name, bool(got), True)


def rows(*items):
    return list(items)


def tick(at, running=0, started=0, skipped=()):
    return {"at": at, "event": "tick", "running": running, "started": started,
            "skipped": [{"chunk": c, "why": w} for c, w in skipped]}


def finished(at, kind="production", accepted=True, chunk="p1-x", pid=1):
    return {"at": at, "event": "finished", "kind": kind, "chunk": chunk, "pid": pid,
            "exit": 0 if accepted else 1, "accepted": accepted}


def started(at, chunk="p2-y", pid=2):
    return {"at": at, "event": "started", "kind": "production", "chunk": chunk, "pid": pid}


def write_flag(tmp, who, why):
    p = Path(tmp) / "scheduler-paused.enabled"
    p.write_text(json.dumps({"who": who, "why": why, "at": "2026-10-24T19:37:22Z"}), encoding="utf-8")
    return p


def no_flag(tmp):
    return Path(tmp) / "scheduler-paused.enabled"


NOW = "2026-10-24T21:00:00Z"


def main():
    tmp = Path(tempfile.mkdtemp(prefix="liveness-"))
    flag_tmp = Path(tempfile.mkdtemp(prefix="liveness-flag-"))
    try:
        # 1. A human pause is a PASS that says who and why -- not a failure, and not silence.
        #    The flag lives in its OWN directory: written into `tmp`, it would still be there
        #    for every later case and turn all of them into PAUSED (it did, first run).
        v = cw.liveness_verdict(rows(tick("2026-10-24T20:50:00Z")), [], write_flag(flag_tmp, "jas", "building"),
                      now=NOW, threshold_hours=6)
        check("a pause by a person is PAUSED", v["state"], "PAUSED")
        check("  and it is not a failure", v["ok"], True)
        check("  and it names who and why", ("jas" in v["line"] and "building" in v["line"]), True)

        # 2. Ten hours since the last accepted PRODUCTION delivery, nothing running -> STALLED,
        #    with the skip reasons carried out of the ledger.
        dispatch = rows(
            finished("2026-10-24T11:23:00Z", chunk="p542-t1110-coder", pid=9),
            tick("2026-10-24T20:50:00Z", skipped=[
                ("p542-t1110-coder", "thinkpad is paused (jas: paused from the Hub) -- Resume on the Hub"),
                ("p867-t1576-modellease", "PARKED: LOOP: the same result 3 times in a row")]),
        )
        v = cw.liveness_verdict(dispatch, [], no_flag(tmp), now=NOW, threshold_hours=6)
        check("ten hours of no production is STALLED", v["state"], "STALLED")
        check("  and it is a failure", v["ok"], False)
        check_true("  and the line says how long", "9.6" in v["line"] or "10" in v["line"])
        check_true("  and the reasons come from the tick row",
                   "PARKED: LOOP" in v["line"] or "paused from the Hub" in v["line"])

        # 3. A job running now is OK even though nothing has been ACCEPTED for ten hours: the
        #    box is working, the delivery just has not landed yet.
        v = cw.liveness_verdict(rows(finished("2026-10-24T11:23:00Z"), started("2026-10-24T20:55:00Z")),
                      [], no_flag(tmp), now=NOW, threshold_hours=6)
        check("a job started and not finished is OK", v["state"], "OK")
        check("  and it is not a failure", v["ok"], True)

        # 4. A delivery inside the threshold is OK.
        v = cw.liveness_verdict(rows(finished("2026-10-24T20:30:00Z")), [], no_flag(tmp),
                      now=NOW, threshold_hours=6)
        check("a recent accepted delivery is OK", v["state"], "OK")

        # 5. Nothing to read is UNKNOWN, and UNKNOWN is not a pass -- an unexamined state is
        #    exactly the reassuring status line this repository keeps rediscovering.
        v = cw.liveness_verdict([], [], no_flag(tmp), now=NOW, threshold_hours=6)
        check("no rows at all is UNKNOWN", v["state"], "UNKNOWN")
        check("  and UNKNOWN is not a pass", v["ok"], False)

        # 6. Paused by a memory guard is NOT a human decision: it is the box starving itself,
        #    which is what the check exists to say out loud.
        device_rows = [{"at": "2026-10-24T19:42:46Z", "device": "gx10", "action": "pause",
                        "who": "memory-guard", "why": "available 1.75 GB fell below the 2.0 GB reserve"}]
        v = cw.liveness_verdict(rows(finished("2026-10-24T11:23:00Z")), device_rows, no_flag(tmp),
                      now=NOW, threshold_hours=6)
        check("a memory-guard pause is STALLED, not PAUSED", v["state"], "STALLED")
        check_true("  and the guard's reason is named", "1.75 GB" in v["line"])

        # 7. Every device paused by a person is PAUSED: the machine was stopped on purpose.
        device_rows = [
            {"at": "2026-10-24T19:37:22Z", "device": "thinkpad", "action": "pause", "who": "jas",
             "why": "paused from the Hub"},
            {"at": "2026-10-24T19:40:00Z", "device": "gx10", "action": "pause", "who": "jas",
             "why": "paused from the Hub"},
        ]
        v = cw.liveness_verdict(rows(finished("2026-10-24T11:23:00Z")), device_rows, no_flag(tmp),
                      now=NOW, threshold_hours=6)
        check("every device paused by a person is PAUSED", v["state"], "PAUSED")
        check("  and it is not a failure", v["ok"], True)

        # 8. A resume after a pause is not a pause. The LAST action per device decides.
        device_rows = [
            {"at": "2026-10-24T18:00:00Z", "device": "gx10", "action": "pause", "who": "jas", "why": "x"},
            {"at": "2026-10-24T18:30:00Z", "device": "gx10", "action": "resume", "who": "jas", "why": "y"},
        ]
        v = cw.liveness_verdict(rows(finished("2026-10-24T20:30:00Z")), device_rows, no_flag(tmp),
                      now=NOW, threshold_hours=6)
        check("a resumed device is not paused", v["state"], "OK")

        # 8b. THE FLAGS ARE THE STATE, THE LEDGER IS THE HISTORY (found on the live box, 2026-10-24).
        #     Both devices were paused BY JAS; the ledger's last row for gx10 was a memory guard
        #     saying "already-paused", so a check reading only the ledger called a deliberate pause
        #     a STALL. A person's pause must win over the last row in the log.
        v = cw.liveness_verdict(rows(finished("2026-10-24T11:23:00Z")), [], no_flag(tmp),
                      now=NOW, threshold_hours=6,
                      paused_devices={"gx10": {"who": "jas", "why": "paused from the Hub"},
                                      "thinkpad": {"who": "jas", "why": "paused from the Hub"}})
        check("a pause read from the FLAGS is PAUSED", v["state"], "PAUSED")
        check("  and it names the person who set it", "jas" in v["line"], True)
        #     ...and the other way: one device paused by a guard is not every device.
        v = cw.liveness_verdict(rows(finished("2026-10-24T11:23:00Z")), [], no_flag(tmp),
                      now=NOW, threshold_hours=6,
                      paused_devices={"gx10": {"who": "memory-guard", "why": "1.75 GB free"}})
        check("a guard pause on the only device is STALLED", v["state"], "STALLED")

        # 9. A non-production accepted run (an RCA, an assigned item) does NOT count as a
        #    production delivery: those lanes kept working all day while production did not.
        v = cw.liveness_verdict(rows(finished("2026-10-24T20:50:00Z", kind="rca", chunk="abc.json")),
                      [], no_flag(tmp), now=NOW, threshold_hours=6)
        check("an accepted RCA is not a production delivery", v["state"], "STALLED")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(flag_tmp, ignore_errors=True)

    print(f"{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
