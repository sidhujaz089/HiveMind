#!/usr/bin/env python3
"""What the HiveMind is doing right now: models, what is in production, what is stalled.

    python3 scripts/hivestatus.py            # readable
    python3 scripts/hivestatus.py --json     # the same, for the Hub's Status tab

WHY, 2026-09-14. Jas asked for a tab showing "the data you have" -- the resident models and
their context, what is in production, what is in each queue, "in case they are stalled".

That last clause is the whole design. Every number here is DERIVED ON READ from the thing
that would actually be wrong, never from a status field someone remembered to update:

  * MODELS come from a live /api/chat probe and /api/ps, not from a document.
    projects/local-inference/STATUS.md says "A real model is serving" and names a vLLM lane
    that is NOT running (t295). A page that read STATUS.md would repeat that.
  * STALL is computed from the heartbeat FILE'S AGE, not from its `state` field. Right now
    q16 and autonomy-loop both say state="running" and last beat ~48 hours ago. A watcher
    that says it is running is not a watcher that is running (L-105), and this page must
    show the disagreement rather than pick a side.
  * CLAIMS are checked. console_reconcile found 33 pids Qwen reported that no dispatch ever
    returned, so "what is in production" cannot be taken from what anyone SAID was dispatched.
    Branches and agent-runs rows are the evidence.

WHAT IT WILL NOT DO. It will not say "healthy" because a file says so, and it will not go
quiet when it cannot look -- a section it could not read says so, because absence is not a
pass (L-146).
"""
import argparse
import json
import re as _re
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

# A heartbeat older than this is stale no matter what its `state` says. The timers run every
# 15 and 30 minutes, so an hour is two missed beats for the slowest of them -- late enough
# not to cry wolf on a slow run, early enough to catch a dead watcher the same session.
STALE_AFTER_S = 3600

# A CHUNK ROW, matched by SHAPE. This was line.startswith("p0"), which silently
# missed the p10-gemma-autonomy row -- the table had three entries and the Status
# page reported two. A prefix that encodes a counter stops matching the moment the
# counter reaches double digits, and nothing says so. 2026-09-14.
_re_chunk = _re.compile(r"^p\d+-")
PROBE_S = 15   # seconds the status page will wait for five tokens (see models())


def models():
    """Resident models and whether the inference PATH answers. Never from a document."""
    out = {"probe": None, "resident": [], "error": None}
    # A SHORT PROBE, CLASSIFIED (2026-09-25). This was probe() at its 600 s default. With Q4
    # holding the single Qwen slot for 15-minute manager jobs, the five-token probe queued
    # behind them, hivestatus --json hung, and the Hub's Review tab -- which waits on it --
    # never loaded on Jas's phone. A status page reports; it does not wait in the queue it
    # is reporting on. classify() turns the timeout into "busy" when our own work holds the
    # slot and "down" only when nothing does.
    try:
        import inference_health
        out["probe"] = inference_health.classify(timeout=PROBE_S)
    except Exception as e:                                          # noqa: BLE001
        out["error"] = f"health probe unavailable: {type(e).__name__}: {e}"
    try:
        r = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=30,
                           stdin=subprocess.DEVNULL)
        lines = [l for l in (r.stdout or "").splitlines() if l.strip()]
        for line in lines[1:]:                                       # skip the header row
            parts = line.split()
            # PARSED BY SHAPE, NOT BY INDEX. The first version took parts[4] and parts[5]
            # and printed "ctx GPU" -- because `100% GPU` is TWO whitespace-separated tokens,
            # so every column after SIZE was off by one. ollama's column order is not a
            # contract either, so find the fields by what they look like: the size is the
            # number before a unit, the processor is the percent-and-device pair, and the
            # context is the bare integer. The raw line is always kept, so a layout change
            # degrades to "we showed you what it said" rather than to a confident wrong value.
            if len(parts) >= 5:
                unit_i = next((i for i, x in enumerate(parts)
                               if x.upper() in ("GB", "MB", "KB", "TB")), None)
                pct_i = next((i for i, x in enumerate(parts) if x.endswith("%")), None)
                ctx = next((x for x in reversed(parts) if x.isdigit() and len(x) >= 4), "?")
                out["resident"].append({
                    "name": parts[0],
                    "size": (f"{parts[unit_i - 1]} {parts[unit_i]}"
                             if unit_i not in (None, 0) else "?"),
                    # NOT GPU UTILISATION. Jas read "100% GPU" as the GPU being pegged, 2026-09-14, and the
                    # label invited it. ollama's PROCESSOR column is PLACEMENT: what share of
                    # THIS MODEL's layers sit on the GPU rather than spilling to CPU. 100%
                    # means no CPU offload, which is what you want and says nothing about how
                    # busy the card is. nvidia-smi reported utilisation.gpu = 0% at the same
                    # moment all three models read "100% GPU".
                    "layers_on_gpu": (" ".join(parts[pct_i:pct_i + 2])
                                      if pct_i is not None else "?"),
                    "context": ctx,
                    "raw": line.rstrip()})
    except Exception as e:                                          # noqa: BLE001
        out["resident_error"] = f"{type(e).__name__}: {e}"
    return out


def gpu():
    """REAL GPU UTILISATION, and the memory figure that works on unified memory.

    Jas, 2026-09-14: "I don't care about layers on GPU. That is always 100. I want GPU
    utilization." Right on both counts.

    nvidia-smi DOES report it on this GB10 -- it read 0% only because the GPU was genuinely
    idle. Sampled during an inference it read 96%, 94%, 96%. So the field works and the earlier
    zero was the truth, not a gap.

    TWO THINGS THIS IS HONEST ABOUT:
      * utilisation is an INSTANT, not an average. A single sample between two decode steps can
        read low on a busy box, so three samples are taken and all three are shown -- one
        number would invite the same misreading "100% GPU" already caused.
      * memory.used comes back [N/A] here, because GB10 is UNIFIED memory: there is no separate
        VRAM pool to report. The meaningful figure is system RAM, from free(1), so that is what
        is shown and it is labelled as unified rather than as VRAM.
    """
    out = {"samples": [], "note": None}
    for _ in range(3):
        try:
            r = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu",
                                "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, timeout=15,
                               stdin=subprocess.DEVNULL)
            v = (r.stdout or "").strip().splitlines()[0].strip()
            out["samples"].append(int(v) if v.isdigit() else None)
        except Exception as e:                                       # noqa: BLE001
            out["note"] = f"nvidia-smi unavailable: {type(e).__name__}: {e}"
            break
        time.sleep(1)
    try:
        r = subprocess.run(["free", "-g"], capture_output=True, text=True, timeout=15,
                           stdin=subprocess.DEVNULL)
        parts = [l.split() for l in (r.stdout or "").splitlines() if l.startswith("Mem:")]
        if parts:
            out["unified_used_gb"], out["unified_total_gb"] = int(parts[0][2]), int(parts[0][1])
            out["unified_free_gb"] = out["unified_total_gb"] - out["unified_used_gb"]
    except Exception as e:                                           # noqa: BLE001
        out["memory_note"] = f"free(1) unavailable: {type(e).__name__}: {e}"
    got = [x for x in out["samples"] if isinstance(x, int)]
    out["say"] = (f"GPU {'/'.join(str(x) for x in got)}% over {len(got)} samples"
                  + (f"; unified memory {out.get('unified_used_gb')}G used of "
                     f"{out.get('unified_total_gb')}G, {out.get('unified_free_gb')}G free"
                     if "unified_used_gb" in out else "")
                  ) if got else (out.get("note") or "GPU utilisation unavailable")
    return out


def _process_gone(pid, started=None):
    """True when this pid is definitely not the process that wrote the beat.

    /proc, not `pgrep -f`: a name search matches the shell running it and reports a dead
    process as alive (L-105), and this repository's own hook blocks that pattern. A missing
    /proc/<pid> directory is unambiguous, and a zombie has an empty cmdline.

    PID REUSE IS CHECKED, because these beats are days old and pids wrap. If /proc/<pid>
    exists but was created AFTER the beat said it started, it is somebody else's process
    wearing the same number, and the watcher is still gone. Returns None when it cannot tell,
    so an unknown is never reported as a finding.
    """
    if not pid:
        return None
    d = Path("/proc") / str(int(pid))
    if not d.is_dir():
        return True
    try:
        if not (d / "cmdline").read_bytes().strip(b"\0"):
            return True                          # zombie: exists, but is not running
        if started:
            born = d.stat().st_mtime
            claimed = time.mktime(time.strptime(started, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
            if born > claimed + 60:
                return True                      # recycled pid, not the original process
    except (OSError, ValueError, OverflowError):
        return None
    return False


def watchers():
    """Heartbeats, with staleness computed from the FILE, not believed from the field."""
    out = []
    d = REPO / "workspace" / "beats"
    if not d.is_dir():
        return [{"name": "(no beats directory)", "stale": None,
                 "say": "workspace/beats does not exist -- nothing was checked, which is not "
                        "the same as nothing running."}]
    for f in sorted(d.glob("*.json")):
        try:
            b = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            out.append({"name": f.stem, "say": f"unreadable: {e}", "stale": None})
            continue
        age = time.time() - f.stat().st_mtime
        claims_running = str(b.get("state", "")).lower() in ("running", "doing")
        stale = age > STALE_AFTER_S
        gone = _process_gone(b.get("pid"), b.get("started"))
        row = {"name": b.get("name") or f.stem, "state": b.get("state"),
               "note": b.get("note"), "age_minutes": int(age // 60), "stale": stale,
               "beats": b.get("beats"), "pid": b.get("pid"), "process_gone": gone}
        if claims_running and gone:
            # THE PID SETTLES IT, and the beat has carried one all along (2026-09-16).
            # This judged staleness from the mtime alone, which is a sound PROXY -- a file
            # that stopped advancing is a job that stopped -- but it can only ever say "this
            # looks abandoned". /proc says whether the process exists, which is the fact the
            # proxy was standing in for, and heartbeat.beat() has been writing the pid into
            # every beat since it was built. Reading it turns four days of "says running"
            # into "it is gone", which is the difference between a suspicion and a finding.
            row["say"] = (f"ENDED -- pid {b.get('pid')} no longer exists; the last beat was "
                          f"{int(age // 60)} min ago and said {b.get('state')!r}. The process "
                          f"is gone, so this is a leftover file, not a stalled job.")
        elif stale and claims_running:
            # STILL ALIVE AND NOT BEATING is the worse case and keeps the original wording:
            # the process exists, so it is hung rather than finished.
            row["say"] = (f"SAYS RUNNING, last beat {int(age // 60)} min ago -- a watcher that "
                          f"says it is running is not one that is running.")
        elif stale:
            row["say"] = f"idle, last beat {int(age // 60)} min ago"
        else:
            row["say"] = f"alive, last beat {int(age // 60)} min ago"
        out.append(row)
    return out


def production():
    """What is actually in flight or delivered -- from branches and runs, not from claims."""
    out = {}

    def git(*a):
        r = subprocess.run(["git", *a], cwd=str(REPO), capture_output=True, text=True,
                           timeout=60, stdin=subprocess.DEVNULL)
        return r.stdout.strip()

    out["branches_awaiting_review"] = [b for b in git(
        "branch", "--list", "coder/*", "--format=%(refname:short)").splitlines() if b.strip()]

    # Live dispatch processes. Matched on a PATH, never a bare name -- a bare grep matches the
    # shell running it (L-105, hooks/guard_process_waits.py).
    try:
        import lane_status
        in_flight = []
        now = time.time()
        for d in sorted(Path("/proc").glob("[0-9]*")):
            try:
                pid = int(d.name)
                with open(d / "stat", "r") as f:
                    stat_content = f.read()
                state = stat_content.split(") ", 1)[1].split()[0]
                if state == "Z":
                    continue
                argv = (d / "cmdline").read_bytes().decode("utf-8", "replace").split("\0")
                argv = [x for x in argv if x]
                if not argv:
                    continue
                cmd = " ".join(argv)
                if (("local-inference/coder.py" in cmd or "scripts/ask_q16.py" in cmd
                    or "scripts/preflight_brief.py" in cmd) and "grep" not in cmd):
                    start = lane_status._started(pid)
                    if start is None:
                        continue
                    elapsed_s = int(now - start)
                    days, remainder = divmod(elapsed_s, 86400)
                    hours, remainder = divmod(remainder, 3600)
                    minutes, seconds = divmod(remainder, 60)
                    if days:
                        etime = f"{days}-{hours:02d}:{minutes:02d}:{seconds:02d}"
                    elif hours:
                        etime = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                    else:
                        etime = f"{minutes:02d}:{seconds:02d}"
                    in_flight.append({"line": f"{pid} {etime} {cmd}"})
            except OSError as e:
                if d.exists():       # gone = exited mid-scan, normal; still there = said
                    print(f"  NOTE: {d} unreadable ({e}) -- not listed", file=sys.stderr)
                continue
        out["in_flight"] = in_flight
    except Exception as e:                                          # noqa: BLE001
        out["in_flight_error"] = f"{type(e).__name__}: {e}"

    # The chunk list the autonomy loop works through, read from the loop itself.
    chunks = []
    loop = REPO / "scripts" / "autonomy_loop.sh"
    if loop.is_file():
        for line in loop.read_text(encoding="utf-8", errors="replace").splitlines():
            if _re_chunk.match(line) and line.count("|") >= 3:
                name, brief, files, target = line.split("|")[:4]
                t = REPO / target.strip()
                green = _green(target.strip()) if t.is_file() else None
                chunks.append({"chunk": name.strip(), "target": target.strip(),
                               "target_exists": t.is_file(), "target_green": green,
                               # "done" READ AS "READY TO MERGE" and it is the opposite.
                               # Jas, 2026-09-14: "3 of the In Production state they are done.
                               # Does that mean they are ready to merge?" No -- a green target
                               # means the fix is ALREADY ON MAIN, so there is nothing to
                               # dispatch and nothing to merge. Merge-ready work is a BRANCH,
                               # and lives in the review queue. Say which.
                               "say": ("already on main -- target suite green, nothing to "
                                       "dispatch"
                                       if green else
                                       "OUTSTANDING -- target red; a model is working it or "
                                       "can pick it up" if green is False
                                       else "unknown -- could not run the target suite")})
    out["chunks"] = chunks

    # PER-LANE DETAIL, from lane_status.py -- the same function the terminal report
    # uses, so the tab and the command line cannot disagree. Jas, 2026-09-14: "Is the
    # thing you just built able to be in the status tab at all, so I can see it?"
    # The raw `ps` lines above stay: they cover ask_q16 and preflight, which are not
    # coder.py lanes.
    try:
        sys.path.insert(0, str(REPO / "scripts"))
        import lane_status
        out["lanes"] = lane_status.snapshot()["lanes"]
    except Exception as e:                                          # noqa: BLE001
        out["lanes_error"] = f"{type(e).__name__}: {e}"
    return out


def queues():
    """The task queue and the review queue, counted from the files that define them."""
    out = {}
    q = REPO / "workspace" / "tasks" / "QUEUE.md"
    if q.is_file():
        lines = [l for l in q.read_text(encoding="utf-8", errors="replace").splitlines()
                 if l.strip() and l.strip()[0].isdigit() and ". t" in l]
        out["tasks_total"] = len(lines)
        out["tasks_top"] = [l.strip() for l in lines[:8]]
    else:
        out["tasks_error"] = "workspace/tasks/QUEUE.md is missing -- nothing was counted."
    # THE REAL FIELD, NOT MY PROSE. 2026-09-14, Jas: "The Status page has 4 items that need me,
    # but my review page says 1. Can those be aligned? There seems to be manual moving in and out
    # of the review tab when they should be linked to real fields of the work items."
    #
    # He was right and the drift was mine. This counted tasks whose BODY contained the string
    # "NEEDS JAS" -- text I had typed into notes that morning. The Hub's Review page has always
    # used the frontmatter field `needs: jas`, which is what taskstore.py writes and what
    # feedback.py's waiting_chip() reads. Two surfaces, two different definitions of the same
    # thing, and the one I invented was the wrong one: a note is a comment, a field is a state.
    #
    # Now there is ONE definition. Both pages read `needs: jas`, so they cannot disagree, and the
    # four tasks I had only DESCRIBED as needing him now carry the field -- which also makes them
    # appear on his Review page, where he would look for them.
    needs = []
    for f in sorted((REPO / "workspace" / "tasks").glob("t*.md")):
        try:
            head = f.read_text(encoding="utf-8", errors="replace").split("---", 2)
        except OSError as e:
            print(f"  NOTE: {f} unreadable ({e}) -- not checked for needs", file=sys.stderr)
            continue
        front = head[1] if len(head) > 2 else ""
        for line in front.splitlines():
            if line.strip().lower() in ("needs: jas", "assigned: jas", "needs: vision", "assigned: vision"):
                needs.append(f.name)
                break
    out["needs_jas"] = needs
    try:
        r = subprocess.run([sys.executable, str(REPO / "scripts" / "reviewq.py")],
                           capture_output=True, text=True, timeout=300, cwd=str(REPO),
                           stdin=subprocess.DEVNULL)
        out["review"] = (r.stdout or "").strip()
    except Exception as e:                                          # noqa: BLE001
        out["review"] = f"could not read the review queue: {type(e).__name__}: {e}"
    return out



def pending():
    """PENDING PRODUCTION: what a free model will pick up, and what only LOOKS ready.

    Jas, 2026-09-14: "I can't tell if those actually will go into production when the current
    ones move to the merge queue ... These are the items scoped, with oracles, and dispatched.
    As soon as a model is free it will pick these up."

    THE ANSWER TURNED OUT TO BE MOSTLY "NOTHING", and that is why this section is shaped as a
    readiness check rather than a list. The ONLY thing a free model pulls from is the chunk
    table in scripts/autonomy_loop.sh -- two rows. Every other brief on disk is enrolled
    NOWHERE: it has been written, sometimes preflighted, sometimes has a red oracle waiting for
    it, and no process will ever pick it up. Listing those as "pending production" would be the
    same lie as a heartbeat that says running.

    So each brief gets all three of Jas's conditions checked separately, and the section says
    which one is missing:

      SCOPED   a brief file exists
      ORACLE   the target suite it names EXISTS and is RED -- a green target means the work is
               already done and the loop would skip it, which is not "pending"
      ENROLLED the brief is in the autonomy loop's table, so something will actually run it

    Only a row with all three is genuinely pending. The rest are shown as blocked, with the
    reason, because "why will this never start" is the question that cost a weekend.
    """
    loop = REPO / "scripts" / "autonomy_loop.sh"
    enrolled = {}
    if loop.is_file():
        for line in loop.read_text(encoding="utf-8", errors="replace").splitlines():
            if _re_chunk.match(line) and line.count("|") >= 3:
                name, brief, files, target = [x.strip() for x in line.split("|")[:4]]
                enrolled[brief] = {"chunk": name, "target": target}

    def suite_state(target):
        """(exists, green) for a target suite. green=None means it could not be run."""
        if not target:
            return False, None
        t = REPO / target
        if not t.is_file():
            return False, None
        return True, _green(target)

    out = []
    bdir = REPO / "workspace" / "briefs"
    if not bdir.is_dir():
        return [{"brief": "(no briefs directory)", "ready": False,
                 "say": "workspace/briefs does not exist -- nothing was checked."}]
    for b in sorted(bdir.iterdir()):
        if not b.is_file() or b.name.startswith("."):
            continue
        # The .scoped.txt copy is what gets dispatched; do not list it twice.
        if b.name.endswith(".scoped.txt"):
            continue
        text = b.read_text(encoding="utf-8", errors="replace")
        # MATCH THE SCOPED TWIN TOO. scope_check.py --emit writes brief-X.scoped.txt and THAT is
        # what gets enrolled in the loop, because it carries the constraints. This scan skips
        # .scoped.txt files so a brief is not listed twice -- which meant the enrolled file was
        # invisible and its unscoped twin was reported "NOT ENROLLED". t238 was enrolled and the
        # page still said nothing would pick it up. 2026-09-14.
        enr = (enrolled.get(b.name)
               or enrolled.get(b.name.replace(".txt", ".scoped.txt"))
               or enrolled.get(b.name.replace(".md", ".scoped.md")))
        # The target suite: from the loop table if enrolled, else the first test path the brief
        # names. A brief that names no suite has no oracle, which is itself the finding.
        target = enr["target"] if enr else None
        ambiguous = None
        if not target:
            # THE TARGET, WITHOUT GUESSING. The first version took the FIRST test path the brief
            # mentioned, which reported p10-gemma-autonomy.md's target as
            # test_signature_gate_expressions.py -- a suite it only refers to in passing. A brief
            # that discusses several suites has no meaningful "first" one.
            #
            # Prefer a path on a line that DECLARES it (TARGET / DONE WHEN / must go green /
            # exits 0). Failing that, accept it only if the brief names exactly one suite.
            # Otherwise report it AMBIGUOUS rather than picking: a wrong target would aim a
            # dispatch at the wrong oracle, and coder.py trusts the target it is handed.
            import re as _re
            named = []
            for ln in text.splitlines():
                for m in _re.finditer(r"scripts/tests/test_\w+\.py", ln):
                    declared = bool(_re.search(
                        r"TARGET|DONE WHEN|must go green|exits 0|frozen suite", ln, _re.I))
                    named.append((declared, m.group(0)))
            declared_paths = [x for d, x in named if d]
            all_paths = sorted({x for _, x in named})
            if declared_paths:
                target = declared_paths[0]
            elif len(all_paths) == 1:
                target = all_paths[0]
            elif all_paths:
                ambiguous = all_paths
        exists, green = suite_state(target)
        reasons = []
        if ambiguous:
            reasons.append(f"names {len(ambiguous)} suites and declares none as its "
                           f"target ({', '.join(ambiguous)}) -- cannot tell which "
                           f"oracle judges it")
        elif not target:
            reasons.append("names no target suite -- no oracle")
        elif not exists:
            reasons.append(f"its target {target} DOES NOT EXIST -- coder.py refuses an absent target")
        elif green:
            reasons.append(f"its target {target} is already GREEN -- nothing left to do")
        if not enr:
            reasons.append("NOT ENROLLED in scripts/autonomy_loop.sh -- no process will pick it up")
        # CLASSIFY, do not just say "not enrolled". Jas, 2026-09-14: "Almost all the content in
        # Pending Production states that no process will pick it up. Is there an issue that needs
        # me?" No -- and the wording invited the question. A brief whose target is GREEN is SPENT:
        # its work is on main and nobody should pick it up. A plan with no target was never a
        # chunk. Reporting both as "no process will pick it up" made 15 non-problems read as 15
        # problems, which is the same defect as calling an already-merged chunk "done".
        if green:
            kind = "spent"
        elif not target and not ambiguous:
            kind = "not-a-chunk"
        elif target and not exists:
            kind = "needs-an-oracle"
        elif not enr:
            kind = "not-enrolled"
        else:
            kind = "ready"
        ready = not reasons
        out.append({
            "brief": b.name, "chunk": (enr or {}).get("chunk"), "target": target,
            "target_exists": exists, "target_green": green, "enrolled": bool(enr),
            "ready": ready, "kind": kind,
            "say": ("PENDING -- scoped, oracle red, enrolled; a free model will take it"
                    if ready else "; ".join(reasons))})
    # Genuinely pending first, then the near-misses.
    out.sort(key=lambda r: (not r["ready"], r["brief"]))
    return out


_RED = {}


def _green(target):
    """Is this target suite green? From the scheduler's own colour cache, keyed on the code state, so
    a suite runs at most once per change for the scheduler AND this page together (2026-10-01: the
    snapshot ran 154 suites one after another, twice over, and took 295 s). None = could not run."""
    if "fn" not in _RED:
        sys.path.insert(0, str(REPO / "scripts"))
        import dispatch_loop
        import parallel_dispatch
        _RED["fn"] = dispatch_loop.cached_red(parallel_dispatch.target_is_red)
        _RED["pre"] = (dispatch_loop, parallel_dispatch)
    red = _RED["fn"](target)
    return None if red is None else not red


def _prefetch():
    """Every enrolled target's colour, on idle cores, before the parts ask one at a time."""
    _green("")                                                     # builds the cached function
    dl, pd = _RED["pre"]
    dl.prefetch_red([c for c in pd.chunks()], {}, _RED["fn"])


PARTS = {"gpu": lambda: gpu(), "models": lambda: models(), "watchers": lambda: watchers(),
         "production": lambda: production(), "queues": lambda: queues(),
         "pending": lambda: pending()}


def snapshot(only=None):
    """Every part, or just the ones named. The Hub's Review tab needs production, watchers and
    pending -- not the model probe, which waits on Ollama (2026-09-25: Jas, 'Why does the
    review tab need to wait for Ollama? The Plan and status tabs don't')."""
    out = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if only is None or {"production", "pending"} & set(only):
        _prefetch()
    for name, part in PARTS.items():
        if only is None or name in only:
            out[name] = part()
    return out


def main():
    ap = argparse.ArgumentParser(description="What the HiveMind is doing right now.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--only", default=None,
                    help=f"with --json: comma-separated parts to compute ({', '.join(PARTS)})")
    a = ap.parse_args()
    only = None
    if a.only:
        only = {p.strip() for p in a.only.split(",") if p.strip()}
        unknown = only - set(PARTS)
        if unknown or not a.json:
            ap.error(f"--only needs --json and names from {', '.join(PARTS)}; got {sorted(unknown)}")
    s = snapshot(only)
    if a.json:
        print(json.dumps(s, indent=2))
        return 0
    m = s["models"]
    print("\nMODELS")
    p = m.get("probe") or {}
    print(f"  {s['gpu'].get('say')}")
    print(f"  inference path: {p.get('say') or m.get('error') or 'not probed'}")
    for r in m["resident"]:
        print(f"  {r['name']:26} {r['size']:>9}  layers on GPU {r['layers_on_gpu']:>9}  ctx {r['context']}")
    if not m["resident"]:
        print("  (nothing resident)")
    print("\nWATCHERS")
    for w in s["watchers"]:
        print(f"  {str(w.get('name')):16} {w.get('say')}")
    print("\nPRODUCTION")
    for c in s["production"].get("chunks") or []:
        print(f"  {c['chunk']:16} {c['say']}")
    infl = s["production"].get("in_flight") or []
    print(f"  in flight: {len(infl)} dispatch process(es)")
    print(f"  branches awaiting review: "
          f"{len(s['production'].get('branches_awaiting_review') or [])}")
    print("\nPENDING PRODUCTION")
    pend = s.get("pending") or []
    ready = [x for x in pend if x.get("ready")]
    print(f"  {len(ready)} of {len(pend)} brief(s) will actually be picked up by a free model")
    for x in pend:
        flag = "READY " if x.get("ready") else "blocked"
        print(f"  [{flag}] {x['brief']}")
        if not x.get("ready"):
            print(f"            {x['say']}")

    print("\nQUEUES")
    print(f"  tasks: {s['queues'].get('tasks_total')}")
    print(f"  needs Jas: {len(s['queues'].get('needs_jas') or [])}")
    print(f"  review: {(s['queues'].get('review') or '').splitlines()[0].strip() if s['queues'].get('review') else '?'}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
