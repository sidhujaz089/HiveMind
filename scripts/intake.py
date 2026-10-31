#!/usr/bin/env python3
"""Intake: which code a task will touch, found before scoping -- by the embedder, no model judgement.

    python3 scripts/intake.py refresh            # rebuild the symbol index, embed new/changed functions
    python3 scripts/intake.py run [--n 40]       # intake the next queue tasks that have no current row
    python3 scripts/intake.py show <task>        # one task's intake row
    python3 scripts/intake.py measure            # intake files vs the files scoped chunks actually edit
    python3 scripts/intake.py tick               # the dispatcher's step: when >= INTAKE_MIN tasks wait, refresh + run

WHY (Jas, 2026-10-14). Staged scoping: cheap steps link tasks before an expensive role scopes them.
Batching (scope_batch.py) grouped tasks by file paths a regex found in their text, so a task written in
plain words ("clean up abandoned temp copies") linked to nothing. Jas: "I do not trust the Gemma model
to think critically" -- so this layer is the EMBEDDER ONLY ("add on the complexity one layer at a
time"); a Gemma chooser is the next layer, trialled only when `measure` shows room to improve.

THE MECHANICS, every step mechanical:
  1. The symbol index (symindex.build: every def/class in scripts/ and projects/, tests excluded) gives
     one line per function: "<file> <name>: <signature> -- <first docstring line>".
  2. Each line is embedded once and cached by its text (workspace/intake/vectors.jsonl); a changed
     function is a new line and is embedded again, an unchanged one never is.
  3. A task's DESCRIPTION (title + body above '## Notes': notes name files in passing) is embedded as
     a query; the TOP_K functions nearest in meaning are its targets.
  4. A function the description names that is defined in a file, or a path it names, adds a bonus to
     that file (symindex.files_for, exact hits only -- ordinary words that are also identifiers do not).
  5. Files are ranked by their BEST matching function plus that bonus; FILES_KEPT are kept. The three most similar
     OPEN tasks are recorded as duplicate candidates -- recorded, never acted on.
  6. One row per task in workspace/intake.jsonl, keyed by the sha of its description: re-intaken only
     when the description changes.
A wrong guess costs the scoper one look at a file: scope_batch links on these files, and the batched
prompt shows them as "likely -- verify, do not trust". The embedder falls back to the GX10's when the
ThinkPad's does not answer (memory.embed).
"""
import argparse
import hashlib
import json
import math
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "projects" / "app-repo")]
DIR = REPO / "workspace" / "intake"
VECTORS = DIR / "vectors.jsonl"
ROWS = REPO / "workspace" / "intake.jsonl"
MEASURE = REPO / "workspace" / "intake-measure.jsonl"
TASKS = REPO / "workspace" / "tasks"
TOP_K = 12
FILES_KEPT = 5
BATCH = 16
TICK_EVERY_S = 600
INTAKE_MIN = 50          # Jas 2026-10-14: run intake when this many tasks wait for it
PROBE_MAX_S = 60         # Jas 2026-10-16: "Use the cpu only on either machine" -- CPU is 10-15 s; only a dead embedder skips


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def description(body):
    """Title + body above '## Notes', front matter dropped."""
    body = body or ""
    title = next((l[7:].strip() for l in body.splitlines() if l.startswith("title: ")), "")
    text = re.sub(r"^---\n.*?\n---\n", "", body, count=1, flags=re.S)
    return (title + "\n" + text.split("\n## Notes", 1)[0]).strip()


def catalogue(idx):
    """[(unit id, text)] -- one line per def/class in scripts/ and projects/, tests excluded."""
    out = []
    for name, ds in idx["defs"].items():
        for d in ds:
            f = d["file"]
            if not (f.startswith("scripts/") or f.startswith("projects/")) or "/tests/" in f:
                continue
            out.append((f"{f}:{name}:{d.get('line')}",
                        f"{f} {name}: {d.get('sig') or name} -- {d.get('doc') or ''}".strip()))
    return out


def load_vectors(path=None):
    p = Path(path or VECTORS)
    out = {}
    if p.is_file():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(line)
            except ValueError as e:
                print(f"  NOTE: unreadable vector row skipped ({e})", file=sys.stderr)
                continue
            out[r["k"]] = r["v"]
    return out


def _unit(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def refresh(embed=None, path=None):
    """Rebuild the symbol index and embed every function line not yet cached. -> (lines, new)."""
    import symindex
    idx = symindex.build()
    symindex.INDEX.write_text(json.dumps(idx, indent=1), encoding="utf-8")
    embed = embed or _embed
    have = load_vectors(path)
    units = catalogue(idx)
    todo = [t for _u, t in units if _sha(t) not in have]
    p = Path(path or VECTORS)
    p.parent.mkdir(parents=True, exist_ok=True)
    new = 0
    for i in range(0, len(todo), BATCH):
        part = todo[i:i + BATCH]
        got = embed(part, False)
        if got is None:
            print(f"  NOTE: embedder unavailable after {new} new vector(s); the rest wait for the next refresh",
                  file=sys.stderr)
            break
        with p.open("a", encoding="utf-8") as f:
            for t, v in zip(part, got):
                f.write(json.dumps({"k": _sha(t), "v": [round(x, 5) for x in v]}) + "\n")
        new += len(part)
    return len(units), new


def _embed(texts, query):
    import memory
    return memory.embed(texts, timeout=300, is_query=query)


def candidates(desc, idx, vecs, embed=None, top_k=TOP_K):
    """{'targets': [...], 'files': [...]} for one description. Pure given idx, vecs and embed."""
    embed = embed or _embed
    targets = []
    q = embed([desc], True)
    if q:
        qu = _unit(q[0])
        scored = []
        for uid, text in catalogue(idx):
            v = vecs.get(_sha(text))
            if v:
                scored.append((sum(a * b for a, b in zip(qu, _unit(v))), uid))
        for s, uid in sorted(scored, reverse=True)[:top_k]:
            f, name, line = uid.rsplit(":", 2)
            targets.append({"file": f, "function": name, "line": int(line) if line.isdigit() else None,
                            "score": round(s, 4), "via": "embed"})
    # EXACT HITS COUNT ONLY WHEN THEY ARE EXACT (2026-10-14): files_for also scores ordinary words that
    # happen to be identifiers ("copies", "tmp"), and at a flat 1+ that swamped the embedder -- hub files
    # (feedback.py, dispatch_loop.py) topped every task. Kept: a function the task names that is defined
    # in the file (files_for >= 25: 30 / its definitions) and a path the task names (+50).
    import symindex
    for f, s in symindex.files_for(desc, idx=idx):
        if "/tests/" not in f and (f.startswith("scripts/") or f.startswith("projects/")) and s >= 25:
            targets.append({"file": f, "function": None, "line": None,
                            "score": round(0.3 if s >= 50 else 0.15, 4), "via": "symbol"})
    # A FILE RANKS BY ITS BEST MATCH, NOT THE SUM (2026-10-14): summing made the files with the most
    # functions win -- session_review.py was in 29 of the first 40 rows. A named file or function adds
    # its bonus on top of the file's best embedded match.
    best, bonus = {}, {}
    for t in targets:
        if t["via"] == "embed":
            best[t["file"]] = max(best.get(t["file"], 0.0), t["score"])
        else:
            bonus[t["file"]] = max(bonus.get(t["file"], 0.0), t["score"])
    rank = {f: best.get(f, 0.0) + bonus.get(f, 0.0) for f in set(best) | set(bonus)}
    files = [f for f, _ in sorted(rank.items(), key=lambda kv: -kv[1])[:FILES_KEPT]]
    return {"targets": targets, "files": files}


def cached_vectors(texts, embed=None, path=None):
    """{text: vector} for texts, embedding only those not in the cache (documents, not queries)."""
    embed = embed or _embed
    have = load_vectors(path)
    todo = [t for t in dict.fromkeys(texts) if _sha(t) not in have]
    p = Path(path or VECTORS)
    p.parent.mkdir(parents=True, exist_ok=True)
    for i in range(0, len(todo), BATCH):
        got = embed(todo[i:i + BATCH], False)
        if got is None:
            break
        with p.open("a", encoding="utf-8") as f:
            for t, v in zip(todo[i:i + BATCH], got):
                have[_sha(t)] = v
                f.write(json.dumps({"k": _sha(t), "v": [round(x, 5) for x in v]}) + "\n")
    return {t: have[_sha(t)] for t in texts if _sha(t) in have}


def similar_tasks(tid, desc, open_vecs, n=3):
    """The n open tasks whose descriptions are nearest -- duplicate CANDIDATES, never acted on.
    open_vecs: {task: vector of its description}, every open task (cached_vectors)."""
    q = open_vecs.get(tid)
    if q is None:
        return []
    qu = _unit(q)
    sims = sorted(((sum(a * b for a, b in zip(qu, _unit(v))), t) for t, v in open_vecs.items() if t != tid),
                  reverse=True)
    return [{"task": t, "score": round(s, 4)} for s, t in sims[:n]]


def rows(path=None):
    p = Path(path or ROWS)
    out = {}
    if p.is_file():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(line)
            except ValueError as e:
                print(f"  NOTE: unreadable intake row skipped ({e})", file=sys.stderr)
                continue
            out[r["task"]] = r
    return out


def current(tid, body, have=None):
    """The task's intake row if it matches the task's description today, else None."""
    r = (have if have is not None else rows()).get(tid)
    return r if r and r.get("desc_sha") == _sha(description(body)) else None


def run(n=40, embed=None):
    import manager_next
    import symindex
    import taskstore
    idx = symindex.load() or symindex.build()
    vecs = load_vectors()
    have = rows()
    picked = backlog_ids()[:n]
    allt = taskstore.load_all()
    open_descs = {t: description(b) for t, (m, b, *_r) in allt.items()
                  if m.get("status") not in ("done", "cancelled")}
    by_text = cached_vectors([d for d in open_descs.values() if d], embed)
    open_vecs = {t: by_text[d] for t, d in open_descs.items() if d in by_text}
    done = 0
    for tid in picked:
        body = allt.get(tid, ({}, ""))[1]
        if not body or current(tid, body, have):
            continue
        desc = description(body)
        c = candidates(desc, idx, vecs, embed)
        row = {"task": tid, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "desc_sha": _sha(desc),
               "files": c["files"], "targets": c["targets"],
               "similar": similar_tasks(tid, desc, open_vecs),
               "chosen_by": "embed"}
        with ROWS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        done += 1
        print(f"  {tid}: {', '.join(c['files']) or '(no candidate)'}")
    return done


def main_file(row):
    """The file a task links on: its strongest match. Linking on all FILES_KEPT linked 40 of the first
    40 tasks through hub files they barely touch; on the main file 30 of 40 link, in real clusters
    (14 whose main file is session_review.py, 8 coder.py) -- the batches worth one scope."""
    return (row.get("files") or [None])[0]


def pre_scoping_stages(tids, bodies, waiting=(), have=None):
    """The Production board's stages before Scoping, for the backlog's tasks in queue order
    (Jas, 2026-10-14: "Task Backlog > Embedding > other...? > Waiting for Scoping > Scoping"):
      backlog   no current intake row yet
      intake    intaken (its likely files are known) and sharing no file with another waiting task
      linked    shares its MAIN file with another backlog task -> it will be scoped in a batch
      waiting   the next tasks the scoper takes (manager_next.upcoming), in its order
    Reads the rows only: nothing is embedded on a page load. -> {stage: [task ids]}."""
    have = rows() if have is None else have
    waiting = [t for t in waiting if t in set(tids)]
    files = {}
    for t in tids:
        r = current(t, bodies.get(t, ""), have)
        if r:
            files[t] = set((r.get("files") or [])[:1])    # its MAIN file -- see main_file()
    seen = {}
    for t, fs in files.items():
        for f in fs:
            seen.setdefault(f, []).append(t)
    linked = {t for ts in seen.values() if len(ts) > 1 for t in ts}
    out = {"backlog": [], "intake": [], "linked": [], "waiting": waiting}
    for t in tids:
        if t in waiting:
            continue
        out["backlog" if t not in files else "linked" if t in linked else "intake"].append(t)
    return out


def backlog_ids(n=400):
    """THE BACKLOG THE HUB SHOWS (2026-10-16): Jas saw "Task Backlog (65)" while the trigger counted 18,
    because it read only manager_next.upcoming -- the scoper's next few. Intake now works the Hub's
    own list (feedback.backlog_rows), scoper's order first; upcoming alone if the Hub is unreadable."""
    import manager_next
    picked, _ = manager_next.upcoming(n=n)
    out = list(picked)
    try:
        app = str(REPO / "projects" / "app-repo")
        if app not in sys.path:
            sys.path.insert(0, app)
        import feedback
        out += [m["id"] for m, _b in feedback.backlog_rows() if m["id"] not in set(out)]
    except Exception as e:                                          # noqa: BLE001 -- said; upcoming only
        print(f"  NOTE: Hub backlog unreadable ({type(e).__name__}: {e}); counting the scoper's next only")
    return out


def pending_count(n=400):
    """How many backlog tasks (as the Hub shows them) have no current intake row. No embedding."""
    import taskstore
    have, allt = rows(), taskstore.load_all()
    return sum(1 for t in backlog_ids(n) if t in allt and not current(t, allt[t][1], have))


def chunk_files(loop=None):
    """{task: set(files its enrolled chunks edit)} -- the ground truth a scope wrote."""
    loop = Path(loop or REPO / "scripts" / "autonomy_loop.sh")
    briefs = REPO / "workspace" / "briefs"
    out = {}
    for line in loop.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split("|")
        if not re.match(r"^p\d+-", line) or len(parts) < 4:
            continue
        b = briefs / parts[1].strip()
        m = re.search(r"^\s*TASK:\s*(t\d+)", b.read_text(encoding="utf-8", errors="replace"), re.M) \
            if b.is_file() else None
        if m:
            out.setdefault(m.group(1), set()).update(x for x in parts[2].split() if "/tests/" not in x)
    return out


def measure(have=None, truth=None):
    """Per task scoped after its intake: precision and recall of intake files vs chunk files."""
    have = rows() if have is None else have
    truth = chunk_files() if truth is None else truth
    per = []
    for tid, r in have.items():
        real = truth.get(tid)
        if not real:
            continue
        guess = set(r.get("files") or [])
        hit = len(guess & real)
        per.append({"task": tid, "precision": round(hit / len(guess), 3) if guess else 0.0,
                    "recall": round(hit / len(real), 3), "guess": sorted(guess), "real": sorted(real)})
    n = len(per)
    summary = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "tasks": n,
               "precision": round(sum(p["precision"] for p in per) / n, 3) if n else None,
               "recall": round(sum(p["recall"] for p in per) / n, 3) if n else None,
               "chosen_by": "embed"}
    return summary, per


def measure_daily(path=None):
    """Append one measure() summary per day (the evidence the next layer -- a Gemma chooser, trialled
    with the eGPU stopped -- is chosen on). Nothing when no intaken task has been scoped yet."""
    p = Path(path or MEASURE)
    today = time.strftime("%Y-%m-%d", time.gmtime())
    if p.is_file() and any(l.startswith('{"at": "' + today) for l in p.read_text(encoding="utf-8").splitlines()):
        return None
    summary, _per = measure()
    if summary["tasks"]:
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(summary) + "\n")
    return summary


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["refresh", "run", "show", "measure", "tick"])
    ap.add_argument("task", nargs="?")
    ap.add_argument("--n", type=int, default=40)
    a = ap.parse_args(argv)
    if a.cmd == "tick":
        # THE DISPATCHER'S STEP BEFORE SCOPING: at most every TICK_EVERY_S, refresh then intake the next
        # 20 queue tasks. Started detached under a flock by dispatch_loop.tick, so a slow embedder never
        # holds the scheduler.
        stamp = DIR / "last-tick"
        if stamp.is_file() and time.time() - stamp.stat().st_mtime < TICK_EVERY_S:
            return 0
        # ONLY WHEN THERE IS A BATCH OF WORK (Jas, 2026-10-14: "Intake trigger for now can be 50"; "I
        # don't want to have the Embedder on every 10 minutes"): the tick embeds nothing until at least
        # INTAKE_MIN of the tasks the scoper could take have no current intake row.
        waiting = pending_count()
        if waiting < INTAKE_MIN:
            return 0
        DIR.mkdir(parents=True, exist_ok=True)
        stamp.write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + "\n")
        # AN EMBEDDER ON THE CPU IS NOT AN EMBEDDER FOR THIS (2026-10-14): the ThinkPad was paused by
        # its guard, the GX10's Ollama held the embedder with 0 GB on the GPU, 16 texts took >120 s, and
        # 2,553 functions would have taken hours of CPU. One probe; too slow -> skip this tick, said.
        t0 = time.time()
        import memory
        probe = memory.embed(["intake speed probe"], timeout=PROBE_MAX_S, is_query=True)
        took = time.time() - t0
        # THE FIRST CALL PAYS THE MODEL LOAD (2026-10-16): every SKIPPED at 5.1-5.4 s was a cold load,
        # and 10 min later the embedder had unloaded again, so 50+ waiting tasks never batched.
        # Time a second call on the now-warm model before calling it slow.
        if probe is None or took > PROBE_MAX_S:
            t0 = time.time()
            probe = memory.embed(["intake speed probe"], timeout=PROBE_MAX_S, is_query=True)
            took = time.time() - t0
        if probe is None or took > PROBE_MAX_S:
            print(f"  intake tick SKIPPED: one embedding took {took:.1f} s at {memory.embed_url()} "
                  f"(limit {PROBE_MAX_S} s) -- the embedder is not on a GPU or is busy")
            return 0
        units, new = refresh()
        print(f"  intake tick: {waiting} waiting; {units} function line(s), {new} newly embedded; "
              f"{run(waiting + 10)} task(s) intaken")
        measure_daily()
        return 0
    if a.cmd == "refresh":
        units, new = refresh()
        print(f"  {units} function line(s) in scripts/ and projects/; {new} newly embedded")
    elif a.cmd == "run":
        print(f"  {run(a.n)} task(s) intaken")
    elif a.cmd == "show":
        print(json.dumps(rows().get(a.task), indent=1))
    else:
        summary, per = measure()
        for p in per:
            print(f"  {p['task']}: precision {p['precision']} recall {p['recall']}  guess {p['guess']}"
                  f"  real {p['real']}")
        print(json.dumps(summary))
        if summary["tasks"]:
            with MEASURE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(summary) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
