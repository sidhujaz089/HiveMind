#!/usr/bin/env python3
"""A suite red on main: the Manager fixes it first, then the line keeps moving (Jas, 2026-10-14).

    python3 scripts/red_on_main.py status       # open and recent events
    python3 scripts/red_on_main.py backfill     # past holds from the cron log + suite-state.jsonl

WHY THE LINE STOPS. Every build copies main into a sandbox and runs the suites first; coder.py
refuses to start when a suite is red that nobody declared, because it cannot tell its own breakage
from one that was already there. So dispatch_loop.main_red_hold stops starting builds rather than
burn them. 2026-10-12..14 that cost ~700 task reassignments to Claude and 67 refused builds against
12 accepted, and nothing recorded WHY each suite went red.

THE FLOW, per red suite (driven by dispatch_loop.main_red_hold on the live tick only):
  1. First seen red -> an OPEN row in workspace/red-on-main.jsonl (the commit that turned it red, its
     Agent: trailer, the files it changed) and ONE task "Red on main: <suite>" at the FRONT of the
     queue, for production -- the Manager takes it as its next scope. The build that fixes it (a chunk
     whose target is the red suite) is exempt from the hold, so the fix is not held by its own cause.
  2. Green again -> a CLOSED row (fixed_by: line) and a note on the task; the line restarts.
  3. Still red after WINDOW_S -> the suite is DECLARED in the ceiling with the task as owner, the task
     goes to Claude, a DECLARED row is written, and the line resumes. Claude works these as a batch,
     with one RCA across them, from this ledger.
  4. UNTRACKED (2026-10-22, D.3.1): a suite a refusal named that git does not track is not main --
     it never holds the line -- but it is not silence either: one owner task "Untracked suite in the
     live checkout: <suite>" with event "untracked-oracle", resolved by "untracked-resolved" when the
     file becomes tracked or is deleted. handle_untracked()/open_untracked()/resolve_untracked().
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LEDGER = REPO / "workspace" / "red-on-main.jsonl"
CEILING = REPO / "workspace" / "test-failures-ceiling.json"
SUITE_STATE = REPO / "workspace" / "suite-state.jsonl"
WINDOW_S = 3600
REASONS = "_oracles_awaiting_their_fix"


def _now():
    return time.time()


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def rows(path=None):
    p = Path(path or LEDGER)
    out = []
    if p.is_file():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                print(f"  NOTE: unreadable line in {p.name} skipped", file=sys.stderr)
    return out


def open_events(history):
    """{suite: its OPEN row} -- the newest row per suite, when that row is 'open'."""
    last = {}
    for r in history:
        last[r.get("suite")] = r
    return {s: r for s, r in last.items() if r.get("event") == "open"}


def _append(row, path=None):
    p = Path(path or LEDGER)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def turned_red(suite, state=None):
    """(commit, ts) of the first red verdict after the suite's last green one in suite-state.jsonl,
    or (None, None) when the ledger has no green-to-red transition for it."""
    p = Path(state or SUITE_STATE)
    last_green, first_red = None, None
    if p.is_file():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            if f'"{suite}"' not in line:
                continue
            try:
                r = json.loads(line)
            except ValueError as e:
                print(f"  NOTE: unreadable row in {p.name} skipped ({e})", file=sys.stderr)
                continue
            if r.get("suite") != suite:
                continue
            if r.get("exit_code") == 0:
                last_green, first_red = r, None
            elif first_red is None:
                first_red = r
    return (first_red.get("commit"), first_red.get("ts")) if (first_red and last_green) else (None, None)


def commit_facts(commit):
    """{'agent', 'files', 'subject'} for a commit, or {} when git cannot say."""
    if not commit:
        return {}
    r = subprocess.run(["git", "-C", str(REPO), "show", "-s", "--format=%s%n%b", commit],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return {}
    agent = next((m.group(1) for m in re.finditer(r"^Agent:\s*(\S+)", r.stdout, re.M)), "")
    f = subprocess.run(["git", "-C", str(REPO), "show", "--name-only", "--format=", commit],
                       capture_output=True, text=True, timeout=60)
    return {"subject": r.stdout.splitlines()[0] if r.stdout else "", "agent": agent,
            "files": [x for x in f.stdout.split() if x]}


def declare(suite, task, since, path=None):
    """Put `suite` in the ceiling, owned by `task` (test_ceiling_owned: the reason starts with it)."""
    p = Path(path or CEILING)
    d = json.loads(p.read_text(encoding="utf-8"))
    if suite not in d.setdefault("allowed_to_fail", []):
        d["allowed_to_fail"].append(suite)
    d.setdefault(REASONS, {})[suite] = (
        f"{task} ({_iso(_now())[:10]}): red on main since {since}; the Manager did not turn it green "
        f"within {WINDOW_S // 60} min, so the scheduler declared it to keep the line moving "
        f"(scripts/red_on_main.py). Remove when {task} makes it pass.")
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1) + "\n", encoding="utf-8")
    tmp.replace(p)


def _file_task(suite, chunk, facts, commit):
    sys.path.insert(0, str(REPO / "projects" / "app-repo"))
    import taskstore
    body = (f"scripts/tests/{suite} is RED ON MAIN, and an undeclared red suite stops every build: each "
            f"sandbox baseline would refuse (first named by the refusal of {chunk}).\n\n"
            f"Turned red at: {commit or 'unknown -- suite-state.jsonl has no green-to-red transition'}"
            + (f" -- \"{facts.get('subject', '')}\" (Agent: {facts.get('agent') or '?'}); files: "
               f"{', '.join(facts.get('files') or [])}" if facts else "") + "\n\n"
            f"FIX IT AS YOUR NEXT SCOPE: one chunk whose TARGET is scripts/tests/{suite} -- that build is "
            f"exempt from the hold. If the suite reads live state (role store, ledgers, the Hub) it is "
            f"the test that is wrong: make it use a fixture.\n\n"
            f"If it is not green within {WINDOW_S // 60} min the scheduler declares it in the ceiling "
            f"with this task as owner, the line resumes, and this task goes to Claude "
            f"(workspace/red-on-main.jsonl).\n\n<!-- red-on-main: {suite} -->\n")
    return taskstore.add(f"Red on main: {suite}"[:90], area="workspace", body=body,
                         source="red-on-main", at_top=True, kind="build")


def _note(task, text):
    sys.path.insert(0, str(REPO / "projects" / "app-repo"))
    import taskstore
    taskstore.add_note(task, text, who="dispatch-loop")


# AN UNTRACKED SUITE IS NOT MAIN, AND IT MUST NOT BE SILENT (D.3.1, RCA 2026-10-22).
# dispatch_loop's 2026-10-12 narrowing is right: an untracked file cannot hold the build line.
# What was missing is the rest of the sentence it wrote -- "it is reported (red_watchdog names
# it)". Naming was prose in a build log. On 2026-10-21 a scoping model's `cp` left
# scripts/tests/test_chat_lane.py untracked in the live checkout; coder.py copies untracked
# files into every sandbox, every build refused to start (19 refusals, 21:50Z -> 00:49Z,
# 0 accepted, 0 tasks). This files ONE owner task per suite, like the red-on-main flow, with
# its own ledger event -- and it never holds, so the line keeps moving.
def file_untracked_task(suite, chunk):
    """The owner task for an untracked suite a build refusal named (a scoping artefact, not work)."""
    sys.path.insert(0, str(REPO / "projects" / "app-repo"))
    import taskstore
    body = (f"scripts/tests/{suite} exists in the live checkout UNTRACKED (never committed, never on a "
            f"branch). coder.py copies untracked files into every sandbox, so the suite ran in every "
            f"baseline and every build refused to start (first named by the refusal of {chunk}).\n\n"
            f"An untracked suite is NOT main: it cannot hold the build line, and it cannot be scoped "
            f"from. FIX: the task that owns this work adds the suite to its **chunks block** "
            f"(kind: oracle) so bridge.py freezes it and a builder commits it to a branch; then merge "
            f"it, or delete the file from the live checkout. Never write a suite straight into "
            f"~/ai-workspace: the live checkout is a service working tree (AGENTS.md -- build in a "
            f"worktree).\n\n"
            f"This event is ledger-only (workspace/red-on-main.jsonl, event \"untracked-oracle\"); it "
            f"holds no build. When the file is tracked, or gone, the scheduler records "
            f"\"untracked-resolved\" and notes this task.\n\n<!-- untracked-oracle: {suite} -->\n")
    return taskstore.add(f"Untracked suite in the live checkout: {suite}"[:90], area="workspace",
                         body=body, source="red-on-main", at_top=True, kind="build")


def open_untracked(history=None):
    """{suite: row} -- untracked suites with a LIVE oracle row: the newest untracked-* row for the
    suite is its open event. untracked-resolved closes it (the file is tracked now, or deleted),
    so a later untracked copy opens fresh and files a task again."""
    hist = rows() if history is None else history
    last = {}
    for r in hist:
        if r.get("event") in ("untracked-oracle", "untracked-resolved"):
            last[r.get("suite")] = r
    return {s: r for s, r in last.items() if r.get("event") == "untracked-oracle"}


def resolve_untracked(suite, task=None, now=None, append=_append):
    """Record that an untracked suite is no longer untracked (tracked now, or deleted)."""
    t = _now() if now is None else now
    append({"event": "untracked-resolved", "suite": suite, "at": _iso(t), "t": t, "task": task})
    if task:
        _note(task, f"scripts/tests/{suite} is no longer an untracked file in the live checkout -- "
                    f"resolved at {_iso(t)}. Close this task on that evidence.")


def handle_untracked(untracked, now=None, history=None, file_task=file_untracked_task, append=_append,
                     open_fn=open_untracked):
    """untracked: {suite: refusing chunk} -- suites a build refusal named that git does not track.
    Files ONE owner task per suite on first sighting; a re-refusal of the same suite is silence,
    exactly like a second refusal of a red-on-main suite. NEVER holds the line.
    Returns [(suite, task)] filed this tick. Side effects only through the callables."""
    now = _now() if now is None else now
    hist = rows() if history is None else history
    opened = open_fn(hist)
    filed = []
    for suite, chunk in sorted(untracked.items()):
        if suite in opened:
            continue
        task = file_task(suite, chunk)
        append({"event": "untracked-oracle", "suite": suite, "at": _iso(now), "t": now, "task": task,
                "refused_chunk": chunk})
        filed.append((suite, task))
    return filed


def _to_claude(task):
    sys.path.insert(0, str(REPO / "projects" / "app-repo"))
    import taskstore
    import role_store                    # the org chart's "technical" position (Jas, 2026-10-19)
    taskstore.set_meta(task, needs=role_store.escalate("technical"))


def handle(red, now=None, history=None, file_task=_file_task, note=_note, to_claude=_to_claude,
           declare_fn=declare, append=_append, facts_of=commit_facts, turned=turned_red, is_red=None):
    """red: {suite: refused chunk} -- the undeclared suites red on main this tick.
    Returns the suites that STILL hold the line (declared ones are released). Side effects only
    through the callables, so a test drives it with fakes."""
    now = _now() if now is None else now
    hist = rows() if history is None else history
    opened = open_events(hist)
    holding = []
    for suite, chunk in sorted(red.items()):
        ev = opened.get(suite)
        if ev is None:
            commit, _ts = turned(suite)
            facts = facts_of(commit)
            task = file_task(suite, chunk, facts, commit)
            append({"event": "open", "suite": suite, "at": _iso(now), "t": now, "task": task,
                    "refused_chunk": chunk, "turned_red_commit": commit, **{f"commit_{k}": v
                                                                        for k, v in facts.items()}})
            holding.append(suite)
        elif now - ev.get("t", now) >= WINDOW_S:
            declare_fn(suite, ev.get("task", ""), ev.get("at", ""))
            to_claude(ev.get("task", ""))
            note(ev.get("task", ""), f"Declared in the failure ceiling at {_iso(now)}: red on main for "
                                     f"{int((now - ev.get('t', now)) // 60)} min, so the line resumed. "
                                     f"Assigned to Claude for the red-on-main batch and its RCA.")
            append({"event": "declared", "suite": suite, "at": _iso(now), "t": now,
                    "task": ev.get("task"), "held_min": int((now - ev.get("t", now)) // 60)})
        else:
            holding.append(suite)
    for suite, ev in opened.items():
        # CLOSED ONLY WHEN SEEN GREEN: a suite can drop out of `red` because the chunk whose refusal
        # named it was withdrawn, while it is still red. is_red(suite) False is the evidence.
        if suite not in red and is_red is not None and is_red(suite) is False:
            note(ev.get("task", ""), f"Green on main at {_iso(now)} -- the line restarted. The "
                                     f"done-check can close this on that evidence.")
            append({"event": "closed", "suite": suite, "at": _iso(now), "t": now, "task": ev.get("task"),
                    "fixed_by": "line", "held_min": int((now - ev.get("t", now)) // 60)})
    return holding


def backfill(log=None, since="2026-10-12"):
    """Past holds rebuilt from the scheduler's cron log: one 'backfill' row per suite with the first and
    last tick it held the line, how many ticks, and the commit that turned it red (suite-state.jsonl).
    The log prints the tick time as '[<iso>]' and each held chunk with 'fix it first: <suites>'."""
    log = Path(log or REPO / "workspace" / "run-logs" / "dispatch-loop-cron.log")
    tick, seen = "", {}
    with log.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("[20"):
                tick = line[1:21]
                continue
            if tick < since or "fix it first:" not in line:
                continue
            for suite in re.findall(r"(test_\w+\.py) \(refused", line.split("fix it first:", 1)[1]):
                s = seen.setdefault(suite, {"first": tick, "last": tick, "ticks": set()})
                s["last"] = tick
                s["ticks"].add(tick)
    out = []
    for suite, s in sorted(seen.items(), key=lambda kv: -len(kv[1]["ticks"])):
        commit, _ = turned_red(suite)
        out.append({"event": "backfill", "suite": suite, "first_held": s["first"], "last_held": s["last"],
                    "held_ticks": len(s["ticks"]), "turned_red_commit": commit,
                    **{f"commit_{k}": v for k, v in commit_facts(commit).items()}})
    return out


def main(argv):
    if argv[:1] == ["backfill"]:
        for r in backfill():
            _append(r)
            print(f"  {r['held_ticks']:5} ticks  {r['first_held']} .. {r['last_held']}  {r['suite']}"
                  f"  red at {(r.get('turned_red_commit') or '?')[:8]} {r.get('commit_agent', '')}"
                  f"  {r.get('commit_subject', '')}")
        return 0
    if argv[:1] == ["status"]:
        for r in rows()[-30:]:
            print(f"  {r.get('at')}  {r.get('event'):8} {r.get('suite')}  {r.get('task') or ''}"
                  f"  {r.get('held_min', '')}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
