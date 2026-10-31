#!/usr/bin/env python3
"""Where each task is in the pipeline, derived from the files -- the Hub's Status board.

    python3 scripts/task_board.py            # the board as text
    python3 scripts/task_board.py --json

Jas, 2026-09-24: "the tile for those tasks move to the Status tab and out of Plan ... tasks that
are scoped appear there. Then as the task goes beyond that to pending product, or in production,
I can see that. It's like a kanban that moves from bottom to top."

STAGES, bottom to top. A task sits in the HIGHEST stage it has evidence for:
  scoped      the manager answered (c) SCOPE -- briefs drafted, waiting on Claude's review
  needs-jas   the manager answered (b) NEEDS JAS
  blocked     the manager answered (d) BLOCKED
  done-check  the manager answered (a) ALREADY DONE -- Claude verifies and closes
  briefed     an enrolled build chunk carries TASK: <id> and has not been delivered
  preflight   a chunk of it is queued for, or under, the preflight review that clears a brief
              for building (Jas, 2026-10-01: "Preflight seems to be a stage between briefed and
              building")
  building    a production dispatch for that chunk has started and not finished
  supervision its newest build FAILED (exit 1) and no supervision decision -- retry, switch model
              or park -- is recorded yet (FLOW.json: building --failed--> supervision)
  in-review   a delivered branch for it waits on Claude's review
  merged      a branch for it was kept in the last 7 days

DERIVED ON READ, never from a status field -- the rule the Status tab already states. Every
source is a file a mechanism writes: the manager's answers, scripts/autonomy_loop.sh, the
dispatch ledger, the review ledger.
"""
import hashlib
import json
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STAGES = ["scoped", "needs-jas", "blocked", "done-check", "parked", "held-claude", "held-red", "briefed",
          "preflight", "supervision",
          "building", "in-review",
          "merged"]
STEP = {"a": "done-check", "b": "needs-jas", "c": "scoped", "d": "blocked"}
KEPT_DAYS = 7


def _step(text):
    """The step letter the answer declares, or None.

    NO STEP IS NOT A SCOPE (Jas, 2026-09-29: "I see 43 scoped"). This used to return "c" when no
    step was found, so a triage note from another prompt (t387, t272), an answer with no step
    (t393) and "step (a): ALREADY DONE" -- the colon broke the match (t235) -- all sat in Scoped.
    """
    head = "\n".join(text.splitlines()[:25])
    m = re.search(r"\(([abcd])\)[\s:*\u2014-]*(ALREADY DONE|NEEDS JAS|SCOPE|BLOCKED)", head, re.I)
    return m.group(1).lower() if m else None


def manager_stages(repo=REPO):
    """{task: (stage, answer path)} from the manager's answers (latest wins)."""
    out = {}
    dirs = [repo / "workspace" / "manager-answers", repo / "workspace" / "run-logs" / "manager-queue" / "out"]
    files = []
    for d in dirs:
        if d.is_dir():
            files += [f for f in d.rglob("*.md") if not f.name.endswith(".thinking.md")]
    for f in sorted(files, key=lambda p: p.stat().st_mtime):
        m = re.search(r"(t\d{3})", f.stem)
        if not m or f.stat().st_size < 200:
            continue
        step = _step(f.read_text(encoding="utf-8", errors="replace"))
        if step is None:                  # no step: it has not left the Backlog (and a newer
            out.pop(m.group(1), None)     # answer without one supersedes an older step)
            continue
        out[m.group(1)] = (STEP[step],
                           str(f.relative_to(repo)))
    # A NEEDS JAS ANSWER IS ANSWERED WHEN JAS RELEASES THE HOLD. Jas, 2026-09-25: "what about
    # the ones I did unblock? They still show in my review list." The stage came from the
    # manager's answer alone, so clearing `needs: jas` on the task changed nothing here -- 11
    # released tasks stayed in his column. His hold is the newer word; the task goes back to
    # the queue for the manager's next pass.
    for tid, (stage, _) in list(out.items()):
        if stage == "needs-jas" and _tier(_needs(repo, tid)) != "vision":
            del out[tid]
    # A DONE-CHECK THAT FOUND IT NOT DONE IS ANSWERED TOO (2026-10-28). The column was left only
    # by closing the task, so a verified "not done" (DS's 16 of 19) kept the task waiting on Claude
    # for ever. A note carrying NOT_DONE_MARK, written after the manager's answer, is the newer
    # word: the task goes back to the queue.
    for tid, (stage, ans) in list(out.items()):
        if stage == "done-check" and _not_done_after(repo, tid, repo / ans):
            del out[tid]
    # A TASK RE-ROUTED TO PRODUCTION AFTER THE ANSWER HAS LEFT ITS COLUMN (Claude, 2026-10-29). Claude
    # triaged t774 and t697 out of "BLOCKED" into production with a reason, and the Review tab still
    # listed both as waiting on Claude, because only a NEWER MANAGER ANSWER moved the stage. The
    # reassignment is the newer word, exactly as a released hold is for needs-jas above.
    for tid, (stage, ans) in list(out.items()):
        if stage in ("blocked", "done-check") and _rerouted_after(repo, tid, repo / ans):
            del out[tid]
    return out


def _rerouted_after(repo, tid, answer):
    """True when the task's LATEST note is a reassignment to production and the file is newer than `answer`."""
    f = next((repo / "workspace" / "tasks").glob(f"{tid}--*.md"), None)
    if f is None:
        return False
    notes = [l for l in f.read_text(encoding="utf-8", errors="replace").splitlines() if l.startswith("**20")]
    if not notes or not re.search(r"Reassigned \S+ -> production\b", notes[-1]):
        return False
    try:
        return f.stat().st_mtime > answer.stat().st_mtime
    except OSError:
        return True


NOT_DONE_MARK = "DONE-CHECK: NOT DONE"


def _not_done_after(repo, tid, answer):
    """True when the task's notes carry NOT_DONE_MARK and the task file is newer than `answer`."""
    f = next((repo / "workspace" / "tasks").glob(f"{tid}--*.md"), None)
    if f is None or NOT_DONE_MARK not in f.read_text(encoding="utf-8", errors="replace"):
        return False
    try:
        return f.stat().st_mtime > answer.stat().st_mtime
    except OSError:
        return True


def _tier(v):
    """'jas' and 'vision' are the same hold after the tier migration (Claude, 2026-10-29)."""
    try:
        import role_store
        return role_store.as_tier(v)
    except Exception:                                     # noqa: BLE001 -- the legacy word is kept
        return "vision" if v == "jas" else v


def _needs(repo, tid):
    f = next((repo / "workspace" / "tasks").glob(f"{tid}--*.md"), None)
    if f is None:                             # finished or cancelled: nothing waits on him
        return "" if any((repo / "workspace" / "tasks").glob(f"*/{tid}--*.md")) else "jas"
    m = re.search(r"^(?:assigned|needs):[ \t]*(\S*)", f.read_text(encoding="utf-8", errors="replace")
                  .split("\n---", 1)[0], re.M)
    return m.group(1) if m else ""


def brief_tasks(repo=REPO):
    """({chunk: task}, {brief sha8: task}) from the enrolled chunks' briefs."""
    by_chunk, by_sha = {}, {}
    loop = repo / "scripts" / "autonomy_loop.sh"
    if not loop.is_file():
        return by_chunk, by_sha
    # A chunk whose TASK line must wait for a review is declared here with its task.
    try:
        pending = json.loads((repo / "workspace" / "briefs" / "task-links-pending.json")
                             .read_text(encoding="utf-8")).get("pending", {})
    except (OSError, ValueError):
        pending = {}
    for line in loop.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(p\d+-[A-Za-z0-9-]+)\|([^|]+)\|", line)   # p154 was named in capitals
        if not m:
            continue
        b = repo / "workspace" / "briefs" / m.group(2)
        if not b.is_file():
            continue
        raw = b.read_bytes()
        t = re.search(rb"^\s*TASK:\s*(t\d+)", raw, re.M)
        # A chunk whose brief names no task still shows, under its own name: Gemma built p19
        # (no TASK line) for 42 minutes on 2026-09-25 and the board's Building column was empty.
        tid = t.group(1).decode() if t else pending.get(m.group(1), {}).get("task") or m.group(1)
        by_chunk[m.group(1)] = tid
        by_sha[hashlib.sha256(raw).hexdigest()[:8]] = tid
    return by_chunk, by_sha


def _jsonl(p):
    # Parsed once per process, then only the appended lines (jsonl_cache: the board re-read 67 MB
    # of dispatch-loop.jsonl from the top on every Hub load, 2026-10-03). A torn row still counts.
    import jsonl_cache
    rows, torn = jsonl_cache.rows(p, torn=True)
    if torn:
        print(f"  NOTE: {torn} unreadable row(s) in {p.name}", file=sys.stderr)
    return rows


def _alive(pid):
    """True when pid is a live, non-zombie process."""
    try:
        stat = Path(f"/proc/{int(pid)}/stat").read_text()
    except (OSError, ValueError, TypeError):
        return False
    return stat.rsplit(")", 1)[-1].split()[0] != "Z"


def _branch_exists(repo, why):
    """A DELIVERED reason names coder/brief-XXXX; once Claude merged it the branch is deleted and the
    reason is stale -- the chunk is merged, not waiting on review."""
    import re
    import subprocess
    m = re.search(r"(coder/brief-[0-9a-f]+)", why)
    if not m:
        return True
    r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "-q", m.group(1)],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return False
    # MERGED BUT NOT DELETED IS MERGED (2026-10-14): ~20 tasks read "waiting on Claude's review" for
    # branches already in main, because only deletion was treated as merged.
    merged = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", m.group(1), "HEAD"],
                            capture_output=True, text=True, timeout=30)
    return merged.returncode != 0


def pipeline_stages(repo=REPO, now=None):
    """{task: stage} from the build pipeline: briefed, building, in-review, merged."""
    now = now or time.time()
    by_chunk, by_sha = brief_tasks(repo)
    out = {}

    def up(tid, stage):
        if not tid:
            return
        if out.get(tid) == "building":          # live work wins over every other stage
            return
        if stage == "building" or tid not in out or STAGES.index(stage) > STAGES.index(out[tid]):
            out[tid] = stage

    import jsonl_cache
    tick = jsonl_cache.latest_tick(repo / "workspace" / "dispatch-loop.jsonl")      # t675: the full queue
    green = set()
    if tick:
        for s in tick.get("skipped", []):
            c, why = (s[0], s[1]) if isinstance(s, (list, tuple)) else (s.get("chunk"), s.get("why", ""))
            if str(why).startswith("target green"):
                green.add(c)
    # WHAT IT IS REALLY WAITING ON (Jas, 2026-10-08: "The other items need to be categorized more
    # accurately to what they are really waiting on"). "briefed" was every chunk with a brief; the
    # dispatcher's own reason for each skipped chunk now places it: parked, held for Claude (a
    # preflight objection), held because a suite is red on main, or delivered (in review). What is
    # left in "briefed" is ready to build and waiting for a build lane.
    why_of = {}
    if tick:
        for s_ in tick.get("skipped", []):
            c, why = (s_[0], s_[1]) if isinstance(s_, (list, tuple)) else (s_.get("chunk"), s_.get("why", ""))
            why_of[c] = str(why)
    for c, tid in by_chunk.items():
        if c in green:
            continue
        why = why_of.get(c, "")
        if why.startswith("task ") and "closed" in why:
            continue
        stage = ("parked" if why.startswith("PARKED") else
                 "held-claude" if why.startswith("HELD FOR CLAUDE") else
                 "held-red" if why.startswith("HELD: red on main") else
                 "in-review" if why.startswith("DELIVERED") and _branch_exists(repo, why) else
                 None if why.startswith("DELIVERED") else "briefed")
        if stage:
            up(tid, stage)
    # PREFLIGHT: the last tick's queue says which briefs wait for review; a live preflight run says
    # which is being reviewed now. Both are mechanism-written, like everything else here.
    if tick:
        for q in tick.get("queued", []) or []:
            if isinstance(q, dict) and q.get("kind") == "preflight":
                up(by_chunk.get(q.get("key")), "preflight")
    live_pf = {}
    for r in _jsonl(repo / "workspace" / "dispatch-loop.jsonl"):
        if r.get("kind") != "preflight":
            continue
        if r.get("event") == "started":
            live_pf[r.get("pid")] = r.get("key") or r.get("chunk")
        elif r.get("event") == "finished":
            live_pf.pop(r.get("pid"), None)
    for pid, c in live_pf.items():
        if _alive(pid):
            up(by_chunk.get(c), "preflight")
    open_runs = {}
    for r in _jsonl(repo / "workspace" / "dispatch-loop.jsonl"):
        if r.get("kind") != "production":
            continue
        if r.get("event") == "started":
            open_runs[r.get("pid")] = r.get("chunk")
        elif r.get("event") == "finished":
            open_runs.pop(r.get("pid"), None)
    # A "started" with no "finished" is only building while its process LIVES. 2026-09-26: a
    # gemma4:26b run of p14 started 2026-09-17 died without its finished row, and t277 showed
    # as "building" for nine days -- Jas: "Gemma seems stuck." A dead run shows nothing here;
    # the chunk falls back to briefed and the dispatcher retries it.
    for pid, c in open_runs.items():
        if _alive(pid):
            up(by_chunk.get(c), "building")
    # SUPERVISION (2026-10-01, a chart stage the board did not show): supervise.pending() is the
    # same list the supervisor works from, so the board and the decision cannot disagree.
    try:
        sys.path.insert(0, str(repo / "scripts"))
        import supervise
        for r in supervise.pending(_jsonl(repo / "workspace" / "dispatch-loop.jsonl"),
                                   _jsonl(repo / "workspace" / "supervision.jsonl")):
            up(by_chunk.get(r.get("chunk")), "supervision")
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: supervision stage unreadable: {type(e).__name__}: {e}", file=sys.stderr)
    pend = set()
    for r in _jsonl(repo / "workspace" / "agent-runs.jsonl"):
        b = str(r.get("branch") or "")
        m = re.match(r"coder/brief-([0-9a-f]{8})", b)
        if m and r.get("outcome") == "ACCEPTED":
            pend.add((b, by_sha.get(m.group(1))))
    decided = {}
    for v in _jsonl(repo / "workspace" / "review-verdicts.jsonl"):
        if v.get("branch"):
            decided[v["branch"]] = v
    for b, tid in pend:
        v = decided.get(b)
        if v is None:
            up(tid, "in-review")
        elif v.get("verdict") == "kept":
            try:
                age = now - time.mktime(time.strptime(str(v.get("at", ""))[:19], "%Y-%m-%dT%H:%M:%S"))
            except ValueError:
                age = 0
            if age <= KEPT_DAYS * 86400:
                up(tid, "merged")
    out.pop(None, None)
    return out


def held_for_jas(repo=REPO):
    """Open tasks whose own file says `needs: jas` -- exactly what the Review tab lists."""
    out = set()
    for f in (repo / "workspace" / "tasks").glob("t*--*.md"):
        m = re.match(r"(t\d+)--", f.name)
        if m and _tier(_needs(repo, m.group(1))) == "vision":
            out.add(m.group(1))
    return out


def board(repo=REPO, now=None):
    """{stage: [(task, detail)]} -- each task once, in its highest evidenced stage."""
    mgr = manager_stages(repo)
    pipe = pipeline_stages(repo, now)
    cols = {s: [] for s in STAGES}
    # PRIORITY ORDER, not task number (Jas, 2026-09-25): within a column the next task is on
    # top -- its position in QUEUE.md. A task no longer in the queue (done) sorts last.
    q = repo / "workspace" / "tasks" / "QUEUE.md"
    pos = {t: i for i, t in enumerate(re.findall(r"^\d+\. (t\d+) ", q.read_text(encoding="utf-8"), re.M))} \
        if q.is_file() else {}
    # ONE RULE FOR "NEEDS JAS", THE SAME ONE THE REVIEW TAB USES: the task's own `needs: jas`.
    # Jas, 2026-09-25: "The criteria for it showing needs jas needs to be the same for both
    # tabs." Review counted 18 held tasks and this column 9, because a hold with no NEEDS JAS
    # answer behind it never reached the board. A held task waits on him whatever else is true
    # of it, so the hold overrides every other stage.
    held = held_for_jas(repo)
    # A FINISHED OR CANCELLED TASK IS ON NO COLUMN (2026-09-27): its old manager answer kept it
    # in "blocked" -- t332, t379, t397 were merged duplicates shown as blocked.
    open_ids = {re.match(r"(t\d+)--", f.name).group(1)
                for f in (repo / "workspace" / "tasks").glob("t*--*.md") if re.match(r"t\d+--", f.name)}
    mgr = {t: v for t, v in mgr.items() if t in open_ids}
    # AND NO CLOSED TASK WAITS FOR A BUILD (2026-10-01): Production showed "Briefed (78)" and all 78
    # were closed -- their chunks are still in the loop table. Merged and in-review stay: history.
    pipe = {t: v for t, v in pipe.items() if t in open_ids or v in ("merged", "in-review")}
    meta = _metas(repo)
    for tid in sorted(set(mgr) | set(pipe) | held, key=lambda t: (pos.get(t, 10 ** 6), t)):
        cands = [s for s in (mgr.get(tid, (None,))[0], pipe.get(tid)) if s]
        # LIVE WORK WINS (2026-09-27): t323 had p52 merged and p58 building on Gemma; "merged"
        # outranked "building", so the board said Building (0) while Gemma was mid-build.
        stage = ("needs-jas" if tid in held else "building" if "building" in cands
                 else max(cands, key=STAGES.index))
        m = meta.get(tid, {})
        # PRODUCTION IS WORK THAT IS MOVING (Jas, 2026-10-08: "Anything waiting on you, me, or a local
        # model needs to be out of the production tab and into the review tab."). A task assigned to
        # anyone -- Claude, Jas, a model role -- is in that person's Review section, not on a column;
        # only a build running right now stays visible, and merged history stays.
        who = (m.get("assigned") or m.get("needs") or "production").strip() or "production"
        if who not in ("production", "model") and stage not in ("building", "merged") and stage != "needs-jas":
            continue
        # SENT BACK FOR SCOPING = THE BACKLOG (Jas, 2026-10-08: "Scope check having things that need
        # rescoping should just go back to the backlog since it's the same task to move them up").
        # A scoped task whose status is queued again is waiting for the scoper like any backlog task.
        if stage == "scoped" and m.get("status", "queued") == "queued":
            continue
        # SCOPE CHECK IS A SCOPE THAT EXISTS: a bridge branch waiting to merge, or a scoping worker on
        # it now. A "doing" task with neither had its scope refused and was never re-queued (t785,
        # t486, t456 on 2026-10-08) -- nothing moves it from here, so it is not shown as in progress.
        if stage == "scoped" and not _scope_live(repo, tid):
            continue
        cols[stage].append((tid, mgr.get(tid, (None, None))[1]))
    return cols


def _scope_live(repo, tid):
    import subprocess
    if (repo / "workspace" / "run-logs" / "manager-queue" / "claims" / tid).exists():
        return True
    r = subprocess.run(["git", "-C", str(repo), "rev-parse", "-q", "--verify", f"bridge/{tid}"],
                       capture_output=True, text=True, timeout=30)
    return r.returncode == 0


def _metas(repo):
    """{task: front matter} for every open task (status, assigned/needs), read straight from the files."""
    out = {}
    for f in (repo / "workspace" / "tasks").glob("t*--*.md"):
        m = re.match(r"(t\d+)--", f.name)
        if not m:
            continue
        try:
            head = f.read_text(encoding="utf-8", errors="replace").split("---", 2)[1]
        except (OSError, IndexError) as e:
            print(f"  NOTE: {f.name} front matter unreadable ({type(e).__name__}); not placed", file=sys.stderr)
            continue
        out[m.group(1)] = dict(l.split(": ", 1) for l in head.strip().splitlines() if ": " in l)
    return out


def main(argv):
    cols = board()
    if "--json" in argv:
        print(json.dumps(cols, indent=1))
        return 0
    for s in reversed(STAGES):
        print(f"{s:<11} {len(cols[s]):>3}  {' '.join(t for t, _ in cols[s])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
