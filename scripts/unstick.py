#!/usr/bin/env python3
"""Diagnose why the HiveMind is idle and apply the repairs that are SAFE to automate.

Jas, 2026-09-16: "I need you to put in place something that lets Chat restart an idle queue.
I want to go to chat and tell it the HiveMind is idle, and it's able to fix it."

WHY THIS EXISTS AS A SCRIPT AND NOT AS A SET OF INSTRUCTIONS. Every time the queue went idle
today the cause was different and none of them were guessable from outside: a scheduler left
paused after a kill, six briefs parked for a crash that was the harness's fault, twelve
verdicts held by a reviewer that could not see the file it named as missing, and a review
queue full of work only Claude may merge. A human -- or Chat -- cannot be expected to
rediscover that chain. So the chain is written down here, runs in order, and says what it
did.

THE SAFETY RULE, and it is the whole design: this REPAIRS PLUMBING, IT NEVER DECIDES WORK.

  It may:   resume a paused scheduler, reap beat files whose process is gone, retire a
            stale model lease, and run a scheduler tick.
  It may NOT: merge anything (D-34 -- Claude is the only gate), edit a brief or an oracle,
            release a park that a MODEL genuinely earned, change a ceiling, or touch main.

Anything it may not do is REPORTED instead, by name, so the caller can say what still needs a
person. A tool that silently did the deciding half would be worse than the idleness: idleness
is visible and a wrong merge is not.

IT IS IDEMPOTENT AND SAFE TO RUN WHEN NOTHING IS WRONG. Running it on a healthy box does
nothing and says so, which is what makes it safe to hand to a caller who cannot see the box.

    python3 scripts/unstick.py              diagnose, repair what is safe, tick
    python3 scripts/unstick.py --dry-run    diagnose and report; change nothing
    python3 scripts/unstick.py --json       machine-readable, for the Chat bridge
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
LEDGER = REPO / "workspace" / "unstick-runs.jsonl"


# WHERE THE PROSE GOES. Under --json, stdout must carry the JSON AND NOTHING ELSE: the first
# end-to-end test of the Chat path printed the human report first, json.loads() failed on it,
# and the caller fell back to "the action produced no readable result -- nothing repaired"
# while the scheduler had in fact just been resumed. A report that misreports the repair is
# worse than no report, because the caller then tells Jas the box is still broken.
_OUT = sys.stdout


def _say(lines, text):
    lines.append(text)
    print(text, flush=True, file=_OUT)


def _record(row):
    """Every run leaves a row, including the ones that changed nothing.

    A repair tool with no history cannot be audited, and 'it was run and did nothing' is the
    single most useful line when the next person asks why the box sat idle anyway.
    """
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError as e:
        # SAID, NEVER SWALLOWED. A repair tool whose audit trail fails in silence is one
        # nobody can later prove was run, which is the question this ledger exists to answer.
        print(f"  WARNING: could not write {LEDGER.name}: {e}", file=sys.stderr, flush=True)


def quick_faults():
    """The cheap half of diagnose(): faults answerable without running the planner.

    Exists so a caller can decide WHETHER to act without paying for a full dry-run plan.
    A fault must never be rate-limited away -- the first end-to-end test of the Chat path
    refused to resume a genuinely paused scheduler because another request had arrived 84
    seconds earlier, and then told the caller "no plumbing fault was found". It had not
    looked. This is what it should have called.
    """
    # A PROBE THAT CANNOT LOOK REPORTS A FAULT, not silence. The caller uses this to decide
    # whether to act at all, so "I could not tell" must fail toward acting: not knowing is
    # not the same as being well, and the cost of an extra tick is seconds while the cost of
    # a silent no is the box staying idle with a report saying it is fine.
    faults = []
    try:
        import scheduler_commands as SC
        if SC.is_paused():
            faults.append("scheduler-paused")
    except Exception as e:                                          # noqa: BLE001
        print(f"  probe could not read the scheduler pause flag: {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)
        faults.append("pause-flag-unreadable")
    try:
        import hivestatus
        if any(str(w.get("state", "")).lower() in ("running", "doing") and w.get("process_gone")
               for w in hivestatus.watchers()):
            faults.append("dead-beat-files")
    except Exception as e:                                          # noqa: BLE001
        print(f"  probe could not read the beat files: {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)
        faults.append("beats-unreadable")
    return faults


def diagnose():
    """Everything that can make the queue idle, as data. No repairs, no side effects."""
    d = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    try:
        import scheduler_commands as SC
        d["paused"] = SC.is_paused()
        d["pause_note"] = (SC.PAUSE_FLAG.read_text(encoding="utf-8").strip()
                           if SC.is_paused() and SC.PAUSE_FLAG.exists() else None)
    except Exception as e:                                          # noqa: BLE001
        d["paused"], d["pause_note"] = None, f"could not read: {e}"

    try:
        import lane_status
        snap = lane_status.snapshot()
        d["lanes"] = [{"kind": r.get("kind"), "model": r.get("model"),
                       "brief": r.get("brief"), "elapsed_s": r.get("elapsed_s")}
                      for r in snap.get("lanes", [])]
        d["resident"] = [m.get("name") for m in snap.get("resident", [])]
    except Exception as e:                                          # noqa: BLE001
        d["lanes"], d["resident"] = None, None
        d["lanes_error"] = str(e)

    try:
        import reviewq
        pend = reviewq.pending()
        d["awaiting_review"] = [{"branch": r.get("branch"), "at": r.get("at"),
                                 "model": r.get("model")} for r in pend]
    except Exception as e:                                          # noqa: BLE001
        d["awaiting_review"] = None
        d["review_error"] = str(e)

    # The scheduler's own verdict on every chunk, which is the authoritative "why nothing
    # started". Read by running its planner in dry-run rather than reimplementing it here --
    # a second copy of that logic would drift from the first (L-114, L-115).
    try:
        r = subprocess.run([sys.executable, str(REPO / "scripts" / "dispatch_loop.py"),
                            "--dry-run"], capture_output=True, text=True, timeout=600,
                           cwd=str(REPO), stdin=subprocess.DEVNULL)
        out = r.stdout or ""
        d["plan_ok"] = r.returncode == 0
        reasons = {}
        for line in out.splitlines():
            s = line.strip()
            if s.startswith("skip"):
                rest = s[4:].strip()
                chunk, _, why = rest.partition(" ")
                key = why.strip().split(":")[0].split("--")[0].strip()[:60] or "unknown"
                reasons.setdefault(key, []).append(chunk)
            elif s.startswith("START"):
                reasons.setdefault("WOULD START", []).append(s)
        d["plan_reasons"] = {k: v for k, v in sorted(reasons.items(),
                                                     key=lambda kv: -len(kv[1]))}
        d["would_start"] = len(reasons.get("WOULD START", []))
    except Exception as e:                                          # noqa: BLE001
        d["plan_ok"], d["plan_reasons"], d["would_start"] = None, {}, None
        d["plan_error"] = str(e)

    try:
        import hivestatus
        ws = hivestatus.watchers()
        d["hung_watchers"] = [w.get("name") for w in ws
                              if str(w.get("state", "")).lower() in ("running", "doing")
                              and w.get("stale") and not w.get("process_gone")]
        d["dead_beats"] = [w.get("name") for w in ws
                           if str(w.get("state", "")).lower() in ("running", "doing")
                           and w.get("process_gone")]
    except Exception as e:                                          # noqa: BLE001
        d["hung_watchers"], d["dead_beats"] = None, None
        d["watcher_error"] = str(e)
    return d


def repair(d, dry_run=False):
    """Apply only the safe repairs. Returns (did, still_needs_a_person)."""
    did, blocked, lines = [], [], []

    # 1. A PAUSED SCHEDULER. The single most common cause, and the one a caller outside the
    #    box cannot see at all: the flag is a file, it survives the kill that set it, and
    #    nothing resumes it. It sat paused for nine hours on 2026-09-16 exactly this way.
    if d.get("paused"):
        if dry_run:
            _say(lines, "  WOULD RESUME the scheduler (paused flag is set)")
        else:
            import scheduler_commands as SC
            SC.resume()
            _say(lines, f"  RESUMED the scheduler. It was paused: {d.get('pause_note')}")
        did.append("resume-scheduler")

    # 2. BEAT FILES WHOSE PROCESS IS GONE. Not a cause of idleness on their own, but they
    #    are what makes a status page unreadable while someone is trying to find the cause.
    if d.get("dead_beats"):
        if dry_run:
            _say(lines, f"  WOULD REAP {len(d['dead_beats'])} dead beat(s): "
                        f"{', '.join(d['dead_beats'])}")
        else:
            import hivestatus
            n = 0
            for name in d["dead_beats"]:
                f = REPO / "workspace" / "beats" / f"{name}.json"
                try:
                    b = json.loads(f.read_text(encoding="utf-8"))
                    b["state"] = "ended"
                    b["note"] = (f"reaped by unstick.py at "
                                 f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}: "
                                 f"pid {b.get('pid')} no longer exists. "
                                 f"Previous note: {b.get('note') or '(none)'}")
                    f.write_text(json.dumps(b, indent=1), encoding="utf-8")
                    n += 1
                except (OSError, ValueError) as e:
                    _say(lines, f"  could not reap beat {name}: {type(e).__name__}: {e}")
                    continue
            _say(lines, f"  REAPED {n} beat file(s) whose process is gone")
        did.append("reap-beats")

    # ---- what this tool must NOT do, reported by name ------------------------------------
    #
    # Each of these is a DECISION, and automating a decision is how a repair tool becomes the
    # thing it was meant to rescue. They are listed so the caller can say precisely what needs
    # a person, rather than reporting "idle" a second time.
    pend = d.get("awaiting_review") or []
    if pend:
        blocked.append(
            f"{len(pend)} branch(es) await Claude's review and only Claude may merge them "
            f"(D-34). Until they are reviewed the WIP cap stays full and no new chunk "
            f"enrols. Branches: " + ", ".join(p.get("branch") or "?" for p in pend[:6]))

    parked = [k for k in (d.get("plan_reasons") or {}) if k.startswith("PARKED")]
    n_parked = sum(len(d["plan_reasons"][k]) for k in parked)
    if n_parked:
        blocked.append(
            f"{n_parked} chunk(s) are PARKED after a model genuinely tried and failed. "
            f"Releasing one means editing its brief or its oracle, which is work, not "
            f"plumbing -- this tool will not do it. Run "
            f"`python3 scripts/supervise.py --failures` to see what each one hit.")

    held = [k for k in (d.get("plan_reasons") or {}) if "HELD FOR CLAUDE" in k]
    n_held = sum(len(d["plan_reasons"][k]) for k in held)
    if n_held:
        blocked.append(
            f"{n_held} chunk(s) are HELD FOR CLAUDE on a preflight objection. The reviewer "
            f"named something missing; a person has to decide whether it is right.")

    if d.get("hung_watchers"):
        blocked.append(
            f"{len(d['hung_watchers'])} watcher(s) are HUNG -- the process is alive and has "
            f"stopped beating, so it may still hold a model: "
            f"{', '.join(d['hung_watchers'])}. Stopping a live process is not plumbing.")
    return did, blocked, lines


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--by", default="unknown", help="who asked (chat, jas, claude-code, cron)")
    a = ap.parse_args()

    global _OUT
    if a.json:
        _OUT = sys.stderr                   # keep stdout pure for the caller's parser

    lines = []
    _say(lines, "")
    _say(lines, "UNSTICK -- why is the HiveMind idle, and what can be fixed without a person")
    _say(lines, "=" * 74)
    d = diagnose()

    _say(lines, f"  scheduler paused : {d.get('paused')}")
    _say(lines, f"  lanes running    : {len(d.get('lanes') or [])}")
    for r in (d.get("lanes") or []):
        _say(lines, f"      {r.get('kind')} on {r.get('model')} -- {r.get('brief') or '(none)'}")
    _say(lines, f"  awaiting review  : {len(d.get('awaiting_review') or [])}")
    _say(lines, f"  planner would start: {d.get('would_start')}")
    for why, chunks in (d.get("plan_reasons") or {}).items():
        if why != "WOULD START":
            _say(lines, f"      {len(chunks):3} x {why}")

    _say(lines, "")
    _say(lines, "REPAIRS" + (" (dry run -- nothing changed)" if a.dry_run else ""))
    did, blocked, rl = repair(d, dry_run=a.dry_run)
    lines += rl
    if not did:
        _say(lines, "  nothing to repair -- no plumbing fault found")

    # THE TICK IS THE POINT. Resuming a scheduler that will not be asked to run for another
    # ten minutes is not "restarting the queue"; the caller is told the box is working again
    # and it is not. flock so this can never collide with the cron tick.
    started = None
    if not a.dry_run:
        _say(lines, "")
        _say(lines, "TICK")
        try:
            # THE LOCK PATH COMES FROM ITS OWNER, never spelled again here. It is the
            # contract this tick shares with the cron tick, and two literals that must agree
            # are two literals that will eventually disagree (L-114, L-115).
            import dispatch_loop as _dl
            r = subprocess.run(["flock", "-n", _dl.TICK_LOCK,
                                sys.executable, str(REPO / "scripts" / "dispatch_loop.py")],
                               capture_output=True, text=True, timeout=900,
                               cwd=str(REPO), stdin=subprocess.DEVNULL)
            out = (r.stdout or "")
            started = [l.strip() for l in out.splitlines() if "started pid" in l]
            if r.returncode == 1 and not out.strip():
                _say(lines, "  a tick was already running (lock held) -- not starting a second")
            for l in started:
                _say(lines, f"  {l}")
            if not started:
                _say(lines, "  the tick ran and started nothing new")
        except Exception as e:                                      # noqa: BLE001
            _say(lines, f"  the tick FAILED: {type(e).__name__}: {e}")

    if blocked:
        _say(lines, "")
        _say(lines, "STILL NEEDS A PERSON -- this tool will not decide these")
        for b in blocked:
            _say(lines, f"  * {b}")

    _say(lines, "")
    verdict = ("repaired and ticked" if did else
               "nothing was wrong with the plumbing" if not blocked else
               "no plumbing fault -- what remains needs a person")
    _say(lines, f"VERDICT: {verdict}")
    _say(lines, "")

    _record({"at": d["at"], "by": a.by, "dry_run": a.dry_run, "did": did,
             "blocked": blocked, "started": started or [],
             "paused_was": d.get("paused"), "awaiting_review": len(d.get("awaiting_review") or []),
             "would_start": d.get("would_start")})

    if a.json:
        print(json.dumps({"diagnosis": d, "did": did, "blocked": blocked,
                          "started": started or [], "verdict": verdict,
                          "report": "\n".join(lines)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
