#!/usr/bin/env python3
"""The script audit: a model reads a whole session, compares its own list with the session-review
script's output, and improves the script one edit at a time -- each edit measured, kept only if it helps.

    python3 scripts/script_audit.py file [--session ID] [--by jas]     # queue one (weekly cron, or asked)
    python3 scripts/script_audit.py run --request FILE --model M [--role R]   # the dispatcher's job
    python3 scripts/script_audit.py score --gold FILE                  # one session's score, JSON
    python3 scripts/script_audit.py status

Jas, 2026-10-07, the design: once a week the model takes a reviewed session, writes its own list,
compares it with the script's output, edits the script to close the differences, and re-runs the
script on that session to confirm. Every proposed edit is tried in turn; an edit that fails gets two
more tries, then goes to Claude, and the loop carries on to the end of the list.

ROLE-AGNOSTIC: kind `script-audit`, task `script_audit` (scripts/task_routing.py). It runs on whichever
role Jas gives that task on the Models tab, with that role's model.

Steps of `run`:
  1. READ    the model lists the session's dropped work in the reference format (trials/audit/sNN.json).
  2. COMPARE code grades the script's output on that session against the model's list: real items the
             script did not flag, and script flags that match nothing on the list (noise).
  3. EDITS   the model proposes a list of exact old -> new edits to scripts/session_candidates.py or
             scripts/semantic.py.
  4. LOOP    each edit in turn, on top of those kept, in a sandbox worktree: re-score this session and
             the whole reference set. KEPT if this session improved and no real miss was lost in the set.
  5. END     kept edits committed on bridge/script-audit-<session> for merge review; the model's list
             joins the reference set; one task records the outcome. Ledger: script-audits.jsonl.
"""
import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "projects" / "app-repo")]
BASE = REPO / "workspace" / "session-reviews"
PENDING = BASE / "script-audit" / "pending"
DONE_REQ = BASE / "script-audit" / "done"
LEDGER = BASE / "script-audits.jsonl"
GOLD = BASE / "trials" / "audit"
EDITABLE = ("scripts/session_candidates.py", "scripts/semantic.py")
TRIES = 3              # the first try and the two more Jas allowed
NOISE_SHOWN = 40       # noise examples per prompt; the full count is always stated


# ------------------------------------------------------------------ queueing
def pending_requests():
    return sorted(PENDING.glob("*.json")) if PENDING.is_dir() else []


def _reviewed():
    d = BASE / "done"
    return sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if d.is_dir() else []


def _audited():
    """Session request names already in the reference set or already queued/done here."""
    seen = set()
    for g in GOLD.glob("s*.json"):
        try:
            seen.add(Path(json.loads(g.read_text(encoding="utf-8"))["request"]).name)
        except (OSError, ValueError, KeyError) as e:
            print(f"  NOTE: {g.name} unreadable ({e}); not counted", file=sys.stderr)
    for d in (PENDING, DONE_REQ):
        for f in (d.glob("*.json") if d.is_dir() else []):
            try:
                seen.add(Path(json.loads(f.read_text(encoding="utf-8"))["session_request"]).name)
            except (OSError, ValueError, KeyError) as e:
                print(f"  NOTE: {f.name} unreadable ({e})", file=sys.stderr)
    return seen


def file_request(session=None, by="timer"):
    """Queue one audit. The timer takes the most recent reviewed session not yet audited and queues
    nothing while one is waiting; a named session (asked for) always queues."""
    if session:
        cands = [p for d in ("done", "pending") for p in (BASE / d).glob("*.json") if session in p.name]
        if not cands:
            print(f"script audit: no session request names {session!r} yet. It is queued for review when "
                  f"the session ends -- run this again after session_end.", file=sys.stderr)
            return 1
        pick = sorted(cands, key=lambda p: p.stat().st_mtime)[-1]
    else:
        if pending_requests():
            print(f"script audit: one is already waiting ({pending_requests()[0].name}); none queued")
            return 0
        seen = _audited()
        pick = next((p for p in _reviewed() if p.name not in seen), None)
        if pick is None:
            print("script audit: every reviewed session is already audited; none queued")
            return 0
    PENDING.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out = PENDING / f"{stamp}-{pick.stem[:40]}.json"
    out.write_text(json.dumps({"at": stamp, "session_request": str(pick.relative_to(REPO)),
                               "requested_by": by}, indent=1) + "\n", encoding="utf-8")
    print(f"script audit queued: {pick.name} (by {by}) -> {out.relative_to(REPO)}")
    return 0


# ------------------------------------------------------------------ scoring
def score_here(gold):
    """The script's output on the gold's session, graded against the gold list, with the differences."""
    import session_review as sr
    import session_review_trial as trial
    req = json.loads(trial.request_path(gold["request"]).read_text(encoding="utf-8"))
    ts = sr.turns(req["harness"], req["path"])
    rows = trial.scripted_rows(ts)
    gr = trial.grade(gold, rows)
    real = [x for x in gr if x["gold"] == "yes"]
    unsurfaced = [x for x in real if x["said"] != "missed"]
    noise = [r for r in rows if r["verdict"] == "missed"
             and not any(trial.matches(g, r["quote"]) or trial.matches(g, r.get("context", ""))
                         for g in gold["items"])]
    return {"real": len(real), "real_surfaced": len(real) - len(unsurfaced),
            "flagged": sum(r["verdict"] == "missed" for r in rows), "noise": len(noise),
            "unsurfaced": [{"id": x["id"], "about": x["about"], "found": x["found"], "said": x["said"]}
                           for x in unsurfaced],
            "noise_rows": [{"quote": r["quote"], "type": r.get("type")} for r in noise]}


def _child(root, args):
    # The LIVE vector cache, even from a sandbox (t790 step 6): the sandbox has none, and its tasks are
    # the committed snapshot, so after the first score every later one in the loop is a cache hit.
    env = {**__import__("os").environ,
           "SEMANTIC_CACHE": str(REPO / "workspace" / "session-reviews" / "index_cache" / "vectors.json")}
    r = subprocess.run([sys.executable, *args], capture_output=True, text=True, timeout=3600, cwd=str(root),
                       env=env)
    if r.returncode != 0:
        raise RuntimeError(f"{args[0]} failed: {(r.stderr or r.stdout).strip()[-400:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def session_score(gold, root=REPO):
    """score_here run in a child at `root`, so a sandbox's edited modules are the ones imported."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(gold, f)
    try:
        return _child(root, [str(root / "scripts" / "script_audit.py"), "score", "--gold", f.name])
    finally:
        Path(f.name).unlink(missing_ok=True)


def set_score(root=REPO):
    """The whole reference set, scored by the script at `root`."""
    return _child(root, [str(root / "scripts" / "session_review_audit.py"), "score-json"])


def better(before, after, set_before, set_after):
    """(kept, why). Kept when this session improved and no real miss was lost anywhere in the set."""
    if set_after["real_surfaced"] < set_before["real_surfaced"]:
        return False, (f"lost a real miss in the reference set "
                       f"({set_before['real_surfaced']} -> {set_after['real_surfaced']})")
    if after["real_surfaced"] < before["real_surfaced"]:
        return False, f"lost a real item on this session ({before['real_surfaced']} -> {after['real_surfaced']})"
    if after["real_surfaced"] > before["real_surfaced"] or after["noise"] < before["noise"]:
        return True, (f"this session: real {before['real_surfaced']} -> {after['real_surfaced']}, "
                      f"noise {before['noise']} -> {after['noise']}; set real {set_after['real_surfaced']}")
    return False, f"no change on this session (real {after['real_surfaced']}, noise {after['noise']})"


# ------------------------------------------------------------------ run (t790 step 1)
EDIT_PROMPT = """You are improving the session-review SCRIPT, which flags work a session left open.
You read session {sid} yourself and listed what was really left open. The script, run on the same
session, disagrees with you in the ways below. Propose edits to the script that close these
differences WITHOUT making it worse elsewhere: every edit is applied and re-scored on this session and
on a reference set of {n_set} other sessions, and kept only if no real miss is lost anywhere.

REAL ITEMS THE SCRIPT DID NOT FLAG ({n_uns}):
{unsurfaced}

SCRIPT FLAGS THAT MATCH NOTHING ON YOUR LIST -- NOISE ({n_noise} in all, {n_shown} shown):
{noise}

THE FILES YOU MAY EDIT (only these):
{files}

Answer with ONLY a JSON list, one object per edit, each closing one difference:
[{{"file": "<one of the files above>", "old": "<text copied EXACTLY from that file, appearing once>",
   "new": "<its replacement>", "why": "<which difference it closes, and how>"}}]
An empty list [] if no edit would help."""

RETRY_PROMPT = """Your edit to {file} was tried and NOT kept: {why}.
Scores before -> after on this session: {before} -> {after}; reference set real misses surfaced:
{set_before} -> {set_after}.

The edit was:
{edit}

The file as it stands now (your earlier kept edits applied):
{text}

Propose ONE revised edit for the same difference, or [] to give up. ONLY a JSON list with one object
{{"file", "old", "new", "why"}}; "old" must appear exactly once in the file above."""


def _ask(prompt, purpose):
    import session_review as sr
    return sr.ask(prompt, purpose, tools=False)


def read_session(req, model, asker=None):
    """READ: the model's own list of what the session left open, in the reference format."""
    import session_review as sr
    asker = asker or _ask
    ts = sr.turns(req["harness"], req["path"])
    parts = sr.chunks(ts)
    ended = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(req.get("ended_epoch") or time.time()))
    rows = []
    for i, text in enumerate(parts, 1):
        prompt = sr.EXTRACT.format(harness=req["harness"], sid=req["id"], part=i, parts=len(parts),
                                   text=text, flow=sr.flow_summary(), open_tasks=sr.open_tasks(),
                                   done_tasks=sr.done_tasks(), ended=ended, ended_day=ended[:10])
        got = sr._json_list(asker(prompt, f"script audit: read {req['id']} part {i}/{len(parts)}"))
        if got is None:
            raise RuntimeError(f"part {i}/{len(parts)}: no JSON list in the model's answer")
        rows += [r for r in got if isinstance(r, dict) and str(r.get("quote") or "").strip()]
    return rows, len(parts)


def to_gold(rows, session_request, sid, model):
    """The model's rows as a reference list (trials/audit format), `by` the model."""
    items = []
    for n, r in enumerate(rows, 1):
        cover = str(r.get("covered_by") or "").strip()
        settled = r.get("disposition") in ("resolved-in-session", "refuted")
        missed = ("either" if r.get("kind") == "for-jas" else
                  "no" if (cover or settled) else "yes")
        items.append({"id": f"M{n}", "about": f"{r.get('kind', '?')}: {r.get('why_missed') or r['quote']}",
                      "anchors": [str(r["quote"]).strip()], "missed": missed, "cover": cover})
    return {"session": sid, "request": session_request, "by": model, "items": items}


def _edits(text):
    import session_review as sr
    got = sr._json_list(text) or []
    return [e for e in got if isinstance(e, dict) and e.get("file") in EDITABLE
            and isinstance(e.get("old"), str) and e["old"] and isinstance(e.get("new"), str)]


def propose_edits(gold, diff, n_set, asker=None, root=REPO):
    asker = asker or _ask
    uns = "\n".join(f"- {u['id']}: {u['about']} (the script said: {u['said']})" for u in diff["unsurfaced"]) or "(none)"
    noise = "\n".join(f"- {r['quote'][:240]}" for r in diff["noise_rows"][:NOISE_SHOWN]) or "(none)"
    files = "\n\n".join(f"=== {f} ===\n{(root / f).read_text(encoding='utf-8')}" for f in EDITABLE)
    prompt = EDIT_PROMPT.format(sid=gold["session"], n_set=n_set, n_uns=len(diff["unsurfaced"]),
                                unsurfaced=uns, n_noise=diff["noise"],
                                n_shown=min(NOISE_SHOWN, len(diff["noise_rows"])), noise=noise, files=files)
    return _edits(asker(prompt, f"script audit: edits for {gold['session']}"))


def _apply(root, e):
    """Apply one exact edit in the sandbox. (original text, '') or (None, why it could not apply)."""
    f = root / e["file"]
    text = f.read_text(encoding="utf-8")
    if text.count(e["old"]) != 1:
        return None, f"'old' appears {text.count(e['old'])} times in {e['file']}, not exactly once"
    f.write_text(text.replace(e["old"], e["new"], 1), encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "py_compile", str(f)], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        f.write_text(text, encoding="utf-8")
        return None, f"{e['file']} no longer compiles: {r.stderr.strip()[-200:]}"
    return text, ""


def _small(s):
    return {k: s.get(k) for k in ("real", "real_surfaced", "flagged", "noise")}


def run_loop(gold, edits, wt, asker=None, score=None, set_scorer=None, log=None):
    """LOOP: every edit in turn, on top of those kept; a failed edit gets TRIES in all, then goes to
    Claude's list and the loop carries on. Returns (kept, failed, before, set_before)."""
    asker, score, set_scorer = asker or _ask, score or session_score, set_scorer or set_score
    log = log or (lambda row: None)
    before, set_before = score(gold, root=wt), set_scorer(root=wt)
    kept, failed = [], []
    for n, edit in enumerate(edits, 1):
        e, last = edit, None
        for attempt in range(1, TRIES + 1):
            orig, why = _apply(wt, e)
            if orig is None:
                after, set_after, ok = before, set_before, False
            else:
                after, set_after = score(gold, root=wt), set_scorer(root=wt)
                ok, why = better(before, after, set_before, set_after)
            log({"edit": n, "attempt": attempt, "file": e["file"], "why": e.get("why", ""), "kept": ok,
                 "verdict": why, "before": _small(before), "after": _small(after),
                 "set_before": _small(set_before), "set_after": _small(set_after)})
            if ok:
                kept.append({**e, "verdict": why})
                before, set_before = after, set_after
                break
            if orig is not None:
                (wt / e["file"]).write_text(orig, encoding="utf-8")
            last = {**e, "verdict": why, "attempts": attempt}
            if attempt == TRIES:
                failed.append(last)
                break
            nxt = _edits(asker(RETRY_PROMPT.format(
                file=e["file"], why=why, before=_small(before), after=_small(after),
                set_before=set_before.get("real_surfaced"), set_after=set_after.get("real_surfaced"),
                edit=json.dumps({k: e[k] for k in ("file", "old", "new", "why") if k in e}, indent=1),
                text=(wt / e["file"]).read_text(encoding="utf-8")),
                f"script audit: retry edit {n} ({attempt + 1}/{TRIES})"))
            if not nxt:
                failed.append({**last, "verdict": why + "; the model gave up"})
                break
            e = nxt[0]
    return kept, failed, before, set_before


def _next_gold_path():
    nums = [int(p.stem[1:]) for p in GOLD.glob("s[0-9]*.json") if p.stem[1:].isdigit()]
    return GOLD / f"s{max(nums, default=0) + 1:02d}.json"


def run(request, model, role=None, asker=None):
    """READ, COMPARE, EDITS, LOOP, END for one queued audit request."""
    import session_review as sr
    sr.ASK_MODEL, sr.ASK_ROLE = model, role
    request = Path(request)
    areq = json.loads(request.read_text(encoding="utf-8"))
    session_request = areq["session_request"]
    import session_review_trial as trial
    req = json.loads(trial.request_path(session_request).read_text(encoding="utf-8"))
    sid = f"{req['harness']}-{str(req['id'])[:8]}"
    t0 = time.time()

    def log(row):
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "session": sid,
                                "request": request.name, "model": model, **row}) + "\n")

    rows, parts = read_session(req, model, asker)
    gold = to_gold(rows, session_request, sid, model)
    log({"step": "read", "parts": parts, "items": len(gold["items"]),
         "real": sum(i["missed"] == "yes" for i in gold["items"])})
    diff = score_here(gold)
    log({"step": "compare", **_small(diff), "unsurfaced": len(diff["unsurfaced"])})
    n_set = len(list(GOLD.glob("s*.json")))
    edits = propose_edits(gold, diff, n_set, asker) if (diff["unsurfaced"] or diff["noise"]) else []
    log({"step": "edits", "proposed": len(edits)})
    wt = Path(tempfile.mkdtemp(prefix="script-audit-"))
    branch = f"bridge/script-audit-{sid}"
    subprocess.run(["git", "worktree", "add", "-q", "--detach", str(wt), "HEAD"], cwd=str(REPO),
                   check=True, capture_output=True, timeout=120)
    try:
        kept, failed, end, set_end = run_loop(gold, edits, wt, asker, log=lambda r: log({"step": "loop", **r}))
        if kept:
            subprocess.run(["git", "checkout", "-q", "-b", branch], cwd=str(wt), check=True, timeout=60)
            subprocess.run(["git", "add", *EDITABLE], cwd=str(wt), check=True, timeout=60)
            msg = (f"feat(workspace): script audit of {sid} -- {len(kept)} kept edit(s) to the session-review script\n\n"
                   + "\n".join(f"- {k['file']}: {k.get('why', '')} [{k['verdict']}]" for k in kept)
                   + f"\n\nWritten by scripts/script_audit.py run ({model}). Review: git diff main..{branch}\n\n"
                   f"Agent: ollama/{model}\n")
            subprocess.run(["git", "commit", "-q", "-m", msg], cwd=str(wt), check=True, timeout=1800,
                           capture_output=True)
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=str(REPO),
                       capture_output=True, timeout=120)
    out = _next_gold_path()
    out.write_text(json.dumps(gold, indent=1) + "\n", encoding="utf-8")
    DONE_REQ.mkdir(parents=True, exist_ok=True)
    request.rename(DONE_REQ / request.name)
    summary = (f"Script audit of {sid} by {model} ({parts} part(s), {time.time() - t0:.0f} s): "
               f"{len(gold['items'])} item(s) on the model's list ({out.relative_to(REPO)}); the script surfaced "
               f"{diff['real_surfaced']} of {diff['real']} real, with {diff['noise']} noise. "
               f"{len(edits)} edit(s) proposed, {len(kept)} kept"
               + (f" on {branch}" if kept else "") + f", {len(failed)} failed after {TRIES} tries"
               + (" -- failed edits and their scores are in script-audits.jsonl." if failed else "."))
    log({"step": "end", "kept": len(kept), "failed": len(failed), "gold": str(out.relative_to(REPO)),
         "branch": branch if kept else None, "summary": summary})
    try:
        import taskstore
        # ONE task for the outcome; Claude's only when there is something for Claude to do --
        # kept edits waiting for merge review, or edits that failed their tries (the plan).
        tid = taskstore.add(f"Script audit: session {sid}", "workspace", summary,
                            source=f"scripts/script_audit.py run ({model})",
                            needs=__import__("role_store").escalate("technical") if (kept or failed) else "")
        print(f"  outcome: {tid}")
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: outcome task not filed ({type(e).__name__}: {e}); the ledger has it", file=sys.stderr)
    print(summary)
    return 0


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["file", "score", "status", "run"])
    ap.add_argument("--session")
    ap.add_argument("--by", default="timer")
    ap.add_argument("--gold")
    ap.add_argument("--request")
    ap.add_argument("--model")
    ap.add_argument("--role")
    a = ap.parse_args(argv)
    if a.cmd == "file":
        return file_request(a.session, a.by)
    if a.cmd == "score":
        print(json.dumps(score_here(json.loads(Path(a.gold).read_text(encoding="utf-8")))))
        return 0
    if a.cmd == "status":
        for f in pending_requests():
            print(f"pending: {f.name}")
        return 0
    if not (a.request and a.model):
        print("script audit: run needs --request FILE and --model M", file=sys.stderr)
        return 2
    return run(a.request, a.model, a.role)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
