#!/usr/bin/env python3
"""One loop breaker for every stage (Jas, 2026-10-24: "How can we stop looping items?" -- "Build it").

    python3 scripts/loop_breaker.py          # what is looping now (read-only report)
    python3 scripts/loop_breaker.py --apply  # also route each new scoping loop to the technical seat

THE RULE. The same work, at the same stage, with the same result LOOP_N times in a row and nothing
changed in between, stops, and goes to the technical seat with the evidence. Every stage had grown
its own cap (futile() for builds, the crash hold for preflights, MAX_UNUSABLE for assigned items, the
second-(b) rule for scoping) and the stages without one looped: p668-t1250's preflight said NOT
SATISFIABLE 67 times in 24 h -- each run ~1 s, under the crash hold's 5-second floor -- and the
manager queue scoped t785 46 times, t1059 41, t728 39 (measured 2026-10-24).

TWO SOURCES, ONE RULE.
  * Dispatcher jobs (dispatch-loop.jsonl `finished` rows) are judged per chunk AND brief sha, so
    editing the brief releases it -- the same act that releases every other park. dispatch_loop.parked()
    calls chunk_loop(); the reason starts "PARKED: LOOP:", which route_waiting() already sends to the
    technical seat and brief_defect() hands back to the scoper.
  * Scoping passes (the manager queue's progress.log "tNNN: Manager pass (queue top): **STEP**" lines)
    are judged per task: the same step LOOP_N times in a row, except SCOPED, which is progress.
    --apply (run by the dispatcher's tick) routes the task to the technical seat with one note, and
    records the pass count in LEDGER so it does not act again until LOOP_N MORE identical passes.

THE EXCEPTION, AND WHY. A failure that HARNESS_SPREAD or more OTHER chunks share in the same window is
the harness, not this work: counting refusals once parked four innocent briefs (futile(), 2026-09-16),
and on 2026-10-23 one bad suite refused every build on the box. That case is not a loop here.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROGRESS = REPO / "workspace" / "run-logs" / "scoping-queue" / "progress.log"
LEDGER = REPO / "workspace" / "loop-breaker.jsonl"

LOOP_N = 3                  # identical results in a row that make a loop
HARNESS_SPREAD = 3          # other chunks failing the same way in the window: the harness, not a loop
YIELDS = (4, -15, 143)      # dispatch_loop.YIELD_EXITS less -9: a yield did no work, so it neither
                            # counts nor breaks the run. -9 (the memory guard's kill) is a result.
# A BUILD THAT REFUSED TO START (production exit 2) IS futile()'s CALL, NOT OURS. Its refusal is usually
# the sandbox baseline -- the harness -- and futile() deliberately excludes it (NEEDS-SCOPE, the brief's
# own refusal, it already parks). Counting it here would re-make the 2026-09-16 mistake whenever a bad
# suite is red for hours rather than minutes, which the HARNESS_SPREAD window cannot see.
NOT_OURS = {("production", 2)}
PASS = re.compile(r"^(t\d+): Manager pass \(queue top\): \*\*(.+?)\*\*")
PROGRESS_STEP = "SCOPED"    # a scoping step that is progress, never a loop


def chunk_loop(chunk, sha, history):
    """(True, "LOOP: ...") when `chunk` at brief `sha` ended with the same (stage, exit) LOOP_N times
    in a row, no success between, and the failure is not one the harness is giving everyone."""
    rows = [r for r in history if r.get("event") == "finished" and r.get("chunk") == chunk
            and r.get("brief_sha256") == sha]
    tail = []
    for r in reversed(rows):
        ex = r.get("exit")
        # A 124 well short of the build wall clock was the memory guard's signal, a yield
        # (dispatch_loop.signal_not_timeout, 2026-10-24).
        if ex is None or ex in YIELDS or (ex == 124 and r.get("seconds") is not None
                                          and float(r["seconds"]) < 3600 - 60):
            continue
        if ex == 0:
            break
        tail.append(r)
        if len(tail) == LOOP_N:
            break
    if len(tail) < LOOP_N or len({(r.get("kind"), r.get("exit")) for r in tail}) != 1:
        return False, ""
    kind, ex = tail[0].get("kind"), tail[0].get("exit")
    if (kind, ex) in NOT_OURS:
        return False, ""
    t0, t1 = min(r.get("at", "") for r in tail), max(r.get("at", "") for r in tail)
    others = {r.get("chunk") for r in history
              if r.get("event") == "finished" and r.get("chunk") != chunk and r.get("kind") == kind
              and r.get("exit") == ex and t0 <= r.get("at", "") <= t1}
    if len(others) >= HARNESS_SPREAD:
        return False, ""
    return True, (f"LOOP: the same result {LOOP_N} times in a row with the brief unchanged ({kind} exit "
                  f"{ex}, {t0} .. {t1}); repeating it learns nothing. Edit the brief, or fix what it "
                  f"asserts, to release it (scripts/loop_breaker.py).")


def scoping_loops(lines):
    """{task: {"step", "passes"}} -- tasks whose last LOOP_N scoping passes declared the same step."""
    seen = {}
    for line in lines:
        m = PASS.match(line.strip())
        if m:
            seen.setdefault(m.group(1), []).append(m.group(2).strip().upper())
    out = {}
    for tid, steps in seen.items():
        tail = steps[-LOOP_N:]
        if len(tail) == LOOP_N and len(set(tail)) == 1 and tail[0] != PROGRESS_STEP:
            out[tid] = {"step": tail[0], "passes": len(steps)}
    return out


def due(passes, acted_at):
    """Act on a scoping loop only once per LOOP_N new passes: after a release, three MORE identical
    passes are a new loop; the same three are not."""
    return acted_at is None or passes >= acted_at + LOOP_N


def should_act(meta, in_production):
    """Only a task that can be scoped AGAIN is a live loop: open, and still in Production. One already
    closed, or already with a person or role, has stopped; a note would only add noise there
    (2026-10-24: 31 of the 32 historical loops were already closed or routed)."""
    return bool(meta) and meta.get("status") not in ("done", "cancelled") and bool(in_production)


def _acted(path=LEDGER):
    out = {}
    if Path(path).exists():
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError as e:
                # A torn line (a crash mid-append) is said, then skipped: the worst case is acting on
                # that task once more, which is one extra note, never a lost hold.
                print(f"  NOTE: {path}: unreadable row skipped ({e})", file=sys.stderr)
                continue
            if r.get("source") == "scoping":
                out[r["task"]] = r.get("passes")
    return out


def apply_scoping(loops, ledger=LEDGER, store=None):
    """Route each newly due scoping loop to the technical seat with one note. Returns [(task, step)]."""
    sys.path[:0] = [str(REPO / "projects" / "app-repo"), str(REPO / "scripts")]
    import role_store
    import taskstore
    # THE TIER, NOT A ROLE NAME (t9554): whoever holds Technical on the Models tab works it.
    to = "technical"
    acted = _acted(ledger)
    done = []
    for tid, info in sorted(loops.items()):
        if not due(info["passes"], acted.get(tid)):
            continue
        meta, _body = taskstore.find(tid)
        if not should_act(meta, meta and taskstore.in_production(meta)):
            continue
        taskstore.set_meta(tid, needs=to, who="loop-breaker")
        taskstore.add_note(tid, (f"LOOP: the manager queue scoped this {LOOP_N} times in a row and declared "
                                 f"**{info['step']}** each time ({info['passes']} passes in all). Another pass "
                                 f"would say the same, so it stops here with {to}: decide what changes "
                                 f"(the task, its brief, or what it is blocked on), then send it back to "
                                 f"Production. scripts/loop_breaker.py"), who="loop-breaker")
        with Path(ledger).open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "source": "scoping",
                                "task": tid, "step": info["step"], "passes": info["passes"], "to": to}) + "\n")
        done.append((tid, info["step"]))
    return done


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="route new scoping loops to the technical seat")
    a = ap.parse_args(argv)
    lines = PROGRESS.read_text(encoding="utf-8", errors="replace").splitlines() if PROGRESS.exists() else []
    loops = scoping_loops(lines)
    acted = _acted()
    for tid, info in sorted(loops.items()):
        state = "due" if due(info["passes"], acted.get(tid)) else f"acted at {acted.get(tid)} passes"
        print(f"  scoping loop  {tid}  {info['step']} x{LOOP_N} ({info['passes']} passes)  {state}")
    if a.apply:
        for tid, step in apply_scoping(loops):
            print(f"  LOOP -> technical seat: {tid} ({step})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
