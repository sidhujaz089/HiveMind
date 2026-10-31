#!/usr/bin/env python3
"""Session-review trial (t663): a cheap model scans, code searches, the model answers yes/no.

Jas, 2026-10-03: "have Gemma ... do a quick cursory look through of stuff that looks like it was
missed. Then that list would go through a search engine of sorts that would use grep or semantic
searching or other types of pattern matching to generate a list of things. And then Gemma would
review that list and just give simple yes, no answers on if they were in fact missed."

Three stages, graded against Claude's reference list (a gold file):
  1. SCAN   -- the model reads the session and lists sentences that look like unfinished work.
  2. SEARCH -- code, not the model: session_review.task_candidates / commit_candidates.
  3. JUDGE  -- per item, one call: "MISSED" or "COVERED tNNN|HASH", from the offered list only.

Nothing is written to the review ledger and no task is filed: it is a trial.

    python3 scripts/session_review_trial.py workspace/session-reviews/trials/gold-007b3f63.json \
        --model gemma4:26b
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import session_review as sr  # noqa: E402
import session_candidates as sc  # noqa: E402

SCAN = """You are reading a transcript of a working session between Jas and his coding assistant.

List every sentence that looks like work that might have been dropped: something deferred
("later", "tomorrow", "left it for you", "I'll handle it"), a problem noticed but not fixed, a
question to Jas that may not have been answered, or a follow-up promised. Be generous -- a later
step checks each one; missing one is worse than listing one too many.

Answer with ONLY a JSON list, one object per item:
[{{"quote": "<the sentence, copied exactly from the transcript>", "why": "<a few words>"}}]

TRANSCRIPT:
{text}
"""

JUDGE = """One question. Answer in ONE line.

In a working session that ended on {since}, this was said:
  "{quote}"
{context}
A search found these existing tasks and later commits that share words with it:
{options}

Is the work in that sentence MISSED (none of these covers it), or COVERED by one of them?
Answer exactly one of:
  MISSED
  COVERED <task id or commit hash from the list>
"""


METHOD = """You are reviewing a finished working session between Jas and his coding assistant. The
point of a session review is to make sure nothing important from the session was lost.

Do the review your own way. There is no required format and no list of categories -- we want to
see how YOU think about it.

1. FLAGS. List everything that flags for you. For each: the sentence it comes from (copied
   exactly), a short category name of your own choosing, and one or two lines on why it flagged.
2. CATEGORIES. List the categories you used and what each one means.
3. METHOD. Describe, step by step, how you went through the transcript: what you read first,
   what words or patterns you looked for, how you decided something mattered, and what you
   deliberately ignored.

TRANSCRIPT (part {part} of {parts}):
{text}
"""


KEEP = """You are checking sentences pulled from a work session between Jas and his coding assistant.
Code found them because they contain words like "I'll", "later", "worth noting", "left it for you".
Many are fine. Your job: for each one, say whether it holds something that could be FORGOTTEN --
work promised or deferred, a problem noticed but not fixed, a question or decision for Jas, a rule
Jas set. Not a summary. Not praise or criticism.

DROP a sentence if it is:
- progress narration ("I'll check back", "running now", "still waiting")
- something the next lines show was done in the same session
- only about pushing commits (pushing is turned off on this machine)
- a plain statement of fact with nothing left to do or decide

For each KEPT sentence, write in under 12 words what needs doing, deciding or remembering.
Use only the sentence and its neighbours. Do not invent task numbers, names or dates.

Answer with one JSON object per line, nothing else:
{{"n": <number>, "keep": true, "what": "<under 12 words>"}}
{{"n": <number>, "keep": false}}
One line for EVERY number below.

SENTENCES (number, type, the sentence, then the sentences around it):
{items}
"""


JUDGE3 = """One question. Answer in ONE line.

In a working session that ended on {since}, this was said:
  "{quote}"
  (what it means: {what})

What the session said NEXT, after that line:
  "{later}"

A search found these existing tasks and later commits that may deal with it:
{options}

Answer exactly one of:
  DONE-IN-SESSION        -- the text after it shows it was done, filed, or answered
  COVERED <id or hash>   -- one of the listed tasks or commits deals with THIS thing (not just shares words)
  MISSED                 -- none of the above
"""


def ask(prompt, model, purpose, budget=8000, timeout=1800):
    """One model call through ask_q16.py (the lease-aware harness), tools off."""
    # PRESSING PAUSE HOLDS THIS (Jas, 2026-10-30: "when I press pause it stops every model from working on
    # tasks"). This function is where a model actually starts, so the pause is asked HERE and not only in whatever
    # loop called in: scheduler_commands.must_hold answers for the production screen, for ALL devices, and for the
    # device this work would use.
    try:
        import scheduler_commands as _sc
        _hold, _why = _sc.must_hold()
        if _hold:
            return (1, f"PAUSED: {_why}")
    except Exception as _e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: could not read the pause flags ({type(_e).__name__}: {_e}) -- continuing")
    with tempfile.TemporaryDirectory() as d:
        p, o = Path(d) / "prompt.txt", Path(d) / "out.txt"
        p.write_text(prompt, encoding="utf-8")
        t0 = time.time()
        while True:
            r = subprocess.run([sys.executable, str(REPO / "scripts" / "ask_q16.py"), "--prompt", str(p),
                                "--out", str(o), "--model", model, "--no-tools", "--budget", str(budget),
                                "--timeout", str(timeout), "--purpose", purpose],
                               capture_output=True, text=True, timeout=timeout * 3, cwd=str(REPO))
            # EXIT 4 = THE LEASE IS HELD, nothing was asked (ask_q16.py). Wait for it, up to 4 h:
            # a trial that records "no answer" because an RCA held the model measures nothing.
            if r.returncode != 4 or time.time() - t0 > 4 * 3600:
                break
            time.sleep(60)
        ans = o.read_text(encoding="utf-8", errors="replace") if o.is_file() else ""
        if not ans.strip():
            print(f"  NOTE: {purpose}: empty answer (exit {r.returncode}): "
                  f"(last 400 chars, cut) {(r.stderr or r.stdout).strip()[-400:]}", file=sys.stderr)
        return ans, round(time.time() - t0)


def _named_tasks(text):
    """Tasks the item's own sentence or the text after it names by number -- shown first (trial v2:
    t327 covered two items but the term search never offered it)."""
    out = []
    root = REPO / "workspace" / "tasks"
    for tid in dict.fromkeys(re.findall(r"\bt(\d{3})\b", text)):
        f = next(iter(list(root.glob(f"t{tid}--*.md")) + list((root / "done").glob(f"t{tid}--*.md"))), None)
        if f:
            head = f.read_text(encoding="utf-8", errors="replace").split("\n---", 1)[0]   # front matter only
            st = (re.search(r"^status:\s*(\S+)", head, re.M) or [None, "?"])[1]
            ti = (re.search(r"^title:\s*(.*)$", head, re.M) or [None, ""])[1]
            out.append((f"t{tid}", st, ti))
    return out


def options(quote, since, nearby=""):
    named = _named_tasks(nearby)
    tasks = named + [t[:3] for t in sr.task_candidates(quote) if t[0] not in {n[0] for n in named}]
    try:
        commits = sr.commit_candidates(quote, since)
    except subprocess.TimeoutExpired as e:
        # `git log -S` reads every diff since the session; on a loaded box it ran past 120 s
        # (2026-10-03, -SPAUSE_FLAG) and took the whole trial down. Messages only, said so.
        print(f"  NOTE: diff search timed out ({e.cmd[-1]}); searching commit messages only", file=sys.stderr)
        commits = []
        for t in [t for t in sr._terms(quote) if re.search(r"[_./]|\d", t)][:3]:
            r = subprocess.run(["git", "-C", str(REPO), "log", f"--since={since} 00:00:00", "--format=%h %ad %s",
                                "--date=short", "-n", "3", "-i", f"--grep={t}"],
                               capture_output=True, text=True, timeout=60)
            commits += [l for l in r.stdout.splitlines() if l and l not in commits]
        commits = commits[:3]
    lines = [f"  {t[0]} [{t[1]}] {t[2]}" for t in tasks[:7]] + [f"  {c}" for c in commits]
    tasks = tasks[:7]
    offered = [t[0] for t in tasks] + [c.split()[0] for c in commits]
    return lines, offered


def verdict(ans, offered):
    """('missed'|'covered'|'unusable', cover id). An id not offered is not believed."""
    line = next((l.strip() for l in ans.splitlines() if l.strip()), "").upper()
    if line.startswith("MISSED"):
        return "missed", ""
    if line.startswith("DONE"):
        return "covered", "in-session"
    m = re.match(r"COVERED\s+(\S+)", line)
    if m:
        got = m.group(1).strip(".,").lower()
        hit = next((o for o in offered if o.lower() == got or o.lower().startswith(got[:7])), None)
        return ("covered", hit) if hit else ("unusable", f"named {got}, not offered")
    return "unusable", line


def matches(item, quote):
    q = quote.lower()
    return any(a.lower() in q for a in item["anchors"])


def grade(gold, rows):
    """Attribute each gold item to at most one review row, and say on what basis (t2112, 2026-10-25).

    THE OLD RULE MADE THE REVIEW LOOK WORSE THAN IT IS. It took the FIRST row whose quote contained
    one of the item's anchors -- a single hand-picked SENTENCE -- and nothing else. Measured on the
    s22 gold session: 46 of 56 items "not found", though 41 of those 46 shared subject with a row that
    already said `verdict: missed`, because the review had worded the same deferral differently. And
    because the rule was "first match", ONE row was the nearest match for TWO gold items, so a single
    row answered two of them.

    Three bases now, strongest first, and each row is SPENT AT MOST ONCE:
      anchor -- an anchor sentence appears in the row (the old rule; unchanged, still strongest)
      topic  -- enough distinctive words are shared; weaker, and reported as `basis: topic` so a
                reader can see how much of a recall number rests on it
      None   -- no row, a genuine miss
    """
    import session_candidates as sc

    def score(g, r):
        text = f'{r.get("quote") or ""} {r.get("context") or ""}'
        if matches(g, text):
            return 2, 1.0
        want = sc.topic_words(f'{g.get("about") or ""} {" ".join(g.get("anchors") or [])}')
        have = sc.topic_words(text)
        if not want:
            return 0, 0.0
        shared = want & have
        share = len(shared) / len(want)
        # TWO shared distinctive words AND a fifth of the item's vocabulary: measured on s22, a
        # genuine-but-reworded match shared one to three words of eight to fifteen, so a stricter
        # threshold recovers nothing while a looser one attributes coincidence.
        return (1, share) if len(shared) >= 2 and share >= 0.2 else (0, 0.0)

    # Greedy allocation, strongest first, so a strong match is never spent on a weak item.
    pairs = []
    for gi, g in enumerate(gold["items"]):
        for ri, r in enumerate(rows):
            rank, share = score(g, r)
            if rank:
                pairs.append((rank, share, gi, ri))
    pairs.sort(key=lambda x: (-x[0], -x[1], x[2]))
    taken_g, taken_r, picked = set(), set(), {}
    for rank, share, gi, ri in pairs:
        if gi in taken_g or ri in taken_r:
            continue
        picked[gi] = (ri, "anchor" if rank == 2 else "topic")
        taken_g.add(gi)
        taken_r.add(ri)

    out = []
    for gi, g in enumerate(gold["items"]):
        ri, basis = picked.get(gi, (None, None))
        hit = rows[ri] if ri is not None else None
        said = hit["verdict"] if hit else None
        ok = (hit is not None and (g["missed"] == "either"
                                   or (g["missed"] == "yes") == (said == "missed")))
        out.append({"id": g["id"], "about": g["about"], "gold": g["missed"], "found": bool(hit),
                    "basis": basis, "said": said, "cover": hit.get("cover") if hit else None,
                    "right": ok})
    return out


def _jsonl_rows(ans):
    out = []
    for line in ans.splitlines():
        line = line.strip().strip(",")
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except ValueError:
                # an unreadable line: its number goes unanswered, and run_v2 KEEPS unanswered rows
                print(f"  NOTE: unreadable keep line: {line!r}", file=sys.stderr)
    return out


def run_v2(a, gold, req, ts, since, batch=20):
    """Code finds candidates; the model keeps/drops them; code searches; the model says MISSED/COVERED."""
    cands = sc.candidates(ts)
    secs = {"keep": 0, "judge": 0}
    kept = []
    for i in range(0, len(cands), batch):
        part = cands[i:i + batch]
        items = "\n".join(f'{c["n"]}. [{c["type"]}] "{c["quote"]}"\n   before: "{c["before"]}"\n'
                          f'   after: "{c["after"]}"' for c in part)
        ans, s_ = ask(KEEP.format(items=items), a.model, f"trial v2 keep {i // batch + 1}", budget=6000)
        secs["keep"] += s_
        said = {r.get("n"): r for r in _jsonl_rows(ans) if isinstance(r, dict)}
        for c in part:
            r = said.get(c["n"])
            if r is None:                                   # unanswered: kept, so nothing silently drops
                kept.append({**c, "what": "", "unanswered": True})
            elif r.get("keep"):
                kept.append({**c, "what": str(r.get("what", ""))})
        print(f"  keep {i // batch + 1}: {len(part)} asked, {len(said)} answered, {s_}s", flush=True)
    rows = []
    for c in kept:
        q = c["quote"]
        lines, offered = options(f'{q} {c["what"]} {c["before"]} {c["after"]}', since,
                                 nearby=f'{q} {c["before"]} {c["after"]} {c.get("later", "")}')
        r = {"quote": q, "context": f'{c["before"]} {c["after"]}', "type": c["type"], "what": c["what"],
             "offered": offered}
        if not offered and not c.get("later"):
            r["verdict"], r["cover"] = "missed", ""
        else:
            ans, s_ = ask(JUDGE3.format(since=since, quote=q, what=c["what"] or "-", later=c.get("later") or "(nothing)",
                                        options="\n".join(lines) or "  (nothing found)"),
                          a.model, "trial v2 judge", budget=2000, timeout=600)
            secs["judge"] += s_
            r["verdict"], r["cover"] = verdict(ans, offered)
            r = sr.prove_cover(r)
        rows.append(r)
    g = grade(gold, rows)
    found, right = sum(x["found"] for x in g), sum(x["right"] for x in g)
    stamp = time.strftime("%Y-%m-%dT%H%M%SZ", time.gmtime())
    out = REPO / "workspace" / "session-reviews" / "trials" / f"{stamp}-{req['id'][:8]}-{a.model.replace(':', '_')}-v2.json"
    out.write_text(json.dumps({"model": a.model, "mode": "v2", "candidates": len(cands), "kept": len(kept),
                               "seconds": secs, "found": found, "right": right, "of": len(g), "grade": g,
                               "rows": rows}, indent=1) + "\n", encoding="utf-8")
    print(f"\n{a.model} v2: {len(cands)} candidates, {len(kept)} kept; gold found {found}/{len(g)}, "
          f"judged right {right}/{len(g)}; keep {secs['keep']}s, judge {secs['judge']}s")
    for x in g:
        print(f"  {'ok ' if x['right'] else 'BAD'} {x['id']} gold={x['gold']:<6} found={x['found']!s:<5} "
              f"said={x['said'] or '-':<8} {x['cover'] or ''}  {x['about'][:60]}")
    print(f"  -> {out.relative_to(REPO)}")
    return 0


def run_triage(a, gold, req, ts, since, cands):
    """The live scripted path (sr.scripted_items + sr.triage) with the Manager's model, graded. Drops are
    kept in `rows` (verdict 'drop') but not graded as findings: a dropped item was not surfaced."""
    t0 = time.time()
    items = sr.scripted_items(ts, since, as_of=a.as_of)
    t1 = time.time()
    rows = sr.triage(items, lambda prompt, why: ask(prompt, a.model, why, budget=6000, timeout=1800)[0],
                     req["harness"], req["id"], since)
    secs = {"code": round(t1 - t0), "manager": round(time.time() - t1)}
    shown = [r for r in rows if r["verdict"] != "drop"]
    gr = grade(gold, shown)
    found, right = sum(x["found"] for x in gr), sum(x["right"] for x in gr)
    stamp = time.strftime("%Y-%m-%dT%H%M%SZ", time.gmtime())
    out = REPO / "workspace" / "session-reviews" / "trials" / (
        f"{stamp}-{req['id'][:8]}-{gold['session']}-triage-{a.model.replace(':', '_')}.json")
    out.write_text(json.dumps({"model": a.model, "mode": "triage", "candidates": len(cands), "items": len(items),
                               "seconds": secs, "found": found, "right": right, "of": len(gr), "grade": gr,
                               "rows": rows}, indent=1) + "\n", encoding="utf-8")
    c = {v: sum(r["verdict"] == v for r in rows) for v in ("missed", "covered", "drop")}
    print(f"triage {a.model}: {len(cands)} candidates -> {len(items)} items; manager says {c}; "
          f"surfaced {found}/{len(gr)}, right {right}/{len(gr)}; code {secs['code']}s, manager {secs['manager']}s")
    for x in gr:
        print(f"  {'ok ' if x['right'] else 'BAD'} {x['id']} gold={x['gold']:<6} found={x['found']!s:<5} "
              f"said={x['said']} cover={x['cover']}")
    return 0


SCRIPTED_SHARE = 0.5     # the scripted judge's bar for "this task IS that item" (it has no model to ask)


def request_path(rel):
    """The gold file's review request, wherever the live queue has since moved it. The audit set names
    pending/<id>.json; once session review runs live it moves to done/ or failed/ (2026-10-07: s01 and
    s12 vanished from the audit the afternoon Swift started reviewing)."""
    p = REPO / rel
    if not p.exists():
        for d in ("done", "failed"):
            alt = REPO / "workspace" / "session-reviews" / d / p.name
            if alt.exists():
                return alt
    return p


def scripted_rows(ts, as_of=""):
    """The scripted review's rows for one transcript: code only, no model, nothing written.
    Shared by run_scripted and session_review_audit.py (the routine audit of this script)."""
    groups = sc.group(sc.dropped(ts))
    rows = []
    for g in groups:
        c = g["rows"][0]
        q = c["quote"]
        near = [t for t in sr.task_candidates(f'{q} {c["before"]} {c["after"]}', limit=12)
                if not as_of or sr.created(t[0]) < as_of][:5]
        q_words = sc.topic_words(q) or sc.topic_words(f'{c["before"]} {c["after"]}')
        best, best_share = "", 0.0
        for tid, _st, _ti, _hit in near:
            share = len(q_words & sc.topic_words(sr.task_text(tid))) / max(1, len(q_words))
            if re.search(rf"\b{tid}\b", q):
                share = 1.0
            if share > best_share:
                best, best_share = tid, share
        covered = best_share >= SCRIPTED_SHARE
        sem = None
        if not covered and SEMANTIC_COVER:
            best_sem = _semantic_cover(q, as_of)
            if best_sem:
                covered, best, sem = True, best_sem[1], round(best_sem[0], 3)
        rows.append({"quote": q, "context": f'{c["before"]} {c["after"]}', "type": c["type"],
                     "repeats": [r["quote"] for r in g["rows"][1:]],
                     "offered": [f"{t[0]} [{t[1]}] {t[2]}" for t in near],
                     "verdict": "covered" if covered else "missed", "cover": best if covered else "",
                     "share": round(best_share, 2), **({"sem": sem} if sem else {})})
    import semantic
    semantic.save()
    return rows


SEMANTIC_COVER = True     # "already covered?" by meaning as well (measured: embed-eval/RESULTS.md)
_TASKS = {}


def _semantic_cover(quote, as_of=""):
    """(cosine, tid) of the task closest in meaning to the quote, if within semantic.COVER_AT. Only
    tasks created before as_of when it is given (a graded run must not be covered by later filings)."""
    import semantic
    if as_of not in _TASKS:
        root = REPO / "workspace" / "tasks"
        got = {}
        for f in list(root.glob("t*.md")) + list((root / "done").glob("t*.md")):
            tid = f.name.split("--")[0]
            c = sr.created(tid)
            if c and (not as_of or c < as_of):
                got[tid] = sr.task_text(tid)[:2000]
        _TASKS[as_of] = (list(got), [got[t] for t in got])
    ids, texts = _TASKS[as_of]
    hit = semantic.best(quote, texts)
    return (hit[0], ids[hit[1]]) if hit and hit[0] >= semantic.COVER_AT else None


def run_scripted(a, gold, req, ts, since):
    """No model (Jas, 2026-10-05: try the scripted approach before a slower model). Code finds the
    candidates, keeps those the session never came back to (sc.dropped), collapses repeats, then searches
    EVERY task -- open and finished -- and commits since the session. COVERED only when a task shares
    SCRIPTED_SHARE of the item's words or the item names a task; everything else is MISSED, with the
    nearest tasks attached as evidence for whoever reads it."""
    t0 = time.time()
    cands = sc.candidates(ts)
    if a.triage:
        return run_triage(a, gold, req, ts, since, cands)
    rows = scripted_rows(ts, a.as_of)
    gr = grade(gold, rows)
    found, right = sum(x["found"] for x in gr), sum(x["right"] for x in gr)
    secs = round(time.time() - t0)
    stamp = time.strftime("%Y-%m-%dT%H%M%SZ", time.gmtime())
    out = REPO / "workspace" / "session-reviews" / "trials" / f"{stamp}-{req['id'][:8]}-scripted.json"
    out.write_text(json.dumps({"model": None, "mode": "scripted", "candidates": len(cands), "kept": len(rows),
                               "seconds": secs, "found": found, "right": right, "of": len(gr), "grade": gr,
                               "rows": rows}, indent=1) + "\n", encoding="utf-8")
    print(f"scripted: {len(cands)} candidates, {len(rows)} dropped (grouped), "
          f"{sum(r['verdict'] == 'missed' for r in rows)} MISSED; gold found {found}/{len(gr)}, "
          f"judged right {right}/{len(gr)}; {secs}s")
    for x in gr:
        print(f"  {'ok ' if x['right'] else 'BAD'} {x['id']} gold={x['gold']:<6} found={x['found']!s:<5} "
              f"said={x['said']} cover={x['cover']}")
    print(f"  -> {out.relative_to(REPO)}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("gold")
    ap.add_argument("--model", default="gemma4:26b")
    ap.add_argument("--v2", action="store_true",
                    help="code-first: session_candidates finds sentences, the model keeps/drops, code searches")
    ap.add_argument("--scripted", action="store_true",
                    help="no model: never-mentioned-again filter, grouped, every task searched (t663)")
    ap.add_argument("--triage", action="store_true",
                    help="with --scripted: the live pipeline, the Manager (--model) triages the dropped items")
    ap.add_argument("--as-of", default="",
                    help="grade as of this time (2026-10-04T000000Z): ignore tasks created later -- the audit's "
                         "own misses were filed afterwards and would otherwise cover themselves")
    ap.add_argument("--method", action="store_true",
                    help="open-ended review: the model's own flags, categories and method (no grading)")
    a = ap.parse_args(argv)
    gold = json.loads(Path(a.gold).read_text(encoding="utf-8"))
    req = json.loads(request_path(gold["request"]).read_text(encoding="utf-8"))
    ts = sr.turns(req["harness"], req["path"])[0:gold.get("to_turn") or None]
    parts = sr.chunks(ts)
    since = time.strftime("%Y-%m-%d", time.gmtime(req.get("ended_epoch") or time.time()))
    rows, secs = [], {"scan": 0, "judge": 0}
    if a.scripted:
        return run_scripted(a, gold, req, ts, since)
    if a.v2:
        return run_v2(a, gold, req, ts, since)
    if a.method:
        stamp = time.strftime("%Y-%m-%dT%H%M%SZ", time.gmtime())
        out = REPO / "workspace" / "session-reviews" / "trials" / (
            f"{stamp}-{req['id'][:8]}-{a.model.replace(':', '_')}-method.md")
        body = [f"# {a.model}: open session review of {req['id']} (t663 method trial)\n"]
        for i, text in enumerate(parts, 1):
            ans, s = ask(METHOD.format(part=i, parts=len(parts), text=text), a.model,
                         f"trial method {i}/{len(parts)}", budget=12000)
            body.append(f"\n## Part {i}/{len(parts)} ({s}s)\n\n{ans.strip() or '(no answer)'}\n")
            out.write_text("".join(body), encoding="utf-8")      # each part kept as it lands
            print(f"  method part {i}/{len(parts)}: {len(ans)} chars, {s}s", flush=True)
        print(f"  -> {out.relative_to(REPO)}")
        return 0

    for i, text in enumerate(parts, 1):                            # 1. SCAN
        ans, s = ask(SCAN.format(text=text), a.model, f"trial scan {i}/{len(parts)}")
        secs["scan"] += s
        got = sr._json_list(ans) or []
        print(f"  scan part {i}/{len(parts)}: {len(got)} item(s), {s}s")
        for r in got:
            if isinstance(r, dict) and r.get("quote"):
                sentence, found = sr._anchor(r["quote"], text)
                rows.append({"quote": r["quote"], "why": r.get("why", ""), "anchored": found,
                             **({"context": sentence} if found and sentence != r["quote"] else {})})

    scan_file = REPO / "workspace" / "session-reviews" / "trials" / (
        f"{time.strftime('%Y-%m-%dT%H%M%SZ', time.gmtime())}-{req['id'][:8]}-{a.model.replace(':', '_')}-scan.json")
    scan_file.write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")   # kept before judging
    for r in rows:                                                 # 2. SEARCH, 3. JUDGE
        lines, offered = options(r["quote"] + " " + r.get("context", ""), since)
        r["offered"] = offered
        if not offered:
            r["verdict"], r["cover"] = "missed", ""                 # nothing shares a term: no call
            continue
        ctx = f'  (the whole sentence: "{r["context"]}")\n' if r.get("context") else ""
        ans, s = ask(JUDGE.format(since=since, quote=r["quote"], context=ctx, options="\n".join(lines)),
                     a.model, "trial judge", budget=2000, timeout=600)
        secs["judge"] += s
        r["verdict"], r["cover"] = verdict(ans, offered)

    g = grade(gold, rows)
    found = sum(x["found"] for x in g)
    right = sum(x["right"] for x in g)
    stamp = time.strftime("%Y-%m-%dT%H%M%SZ", time.gmtime())
    out = REPO / "workspace" / "session-reviews" / "trials" / f"{stamp}-{req['id'][:8]}-{a.model.replace(':', '_')}.json"
    out.write_text(json.dumps({"model": a.model, "gold": a.gold, "seconds": secs, "found": found,
                               "right": right, "of": len(g), "grade": g, "rows": rows}, indent=1) + "\n",
                   encoding="utf-8")
    print(f"\n{a.model}: scanned {len(rows)} item(s); gold found {found}/{len(g)}, "
          f"judged right {right}/{len(g)}; scan {secs['scan']}s, judge {secs['judge']}s")
    for x in g:
        print(f"  {'ok ' if x['right'] else 'BAD'} {x['id']} gold={x['gold']:<6} found={x['found']!s:<5} "
              f"said={x['said'] or '-':<8} {x['cover'] or ''}  {x['about'][:60]}")
    print(f"  -> {out.relative_to(REPO)}")
    return 0


# t097: the workflow gate. The approved plan says a workflow cannot be put on a role until its
# L2 (practice) score is recorded, and until now that rule lived only in prose -- the same defect
# class this repo recorded for the STATE.md gate. These four names read the file so the rule can
# be checked by code; the move to a dedicated workflow_gate module is the next scope.
WF_SECTIONS = ("Spec", "L1 Reaction", "L2 Learning", "L3 Behaviour", "L4 Results")


def wf_sections(text: str) -> dict:
    """Split a workflow file into {name: body} for the names in WF_SECTIONS.

    A '## ' line opens a section named by the rest of the line, and its body runs to the next
    '## ' line, stripped of blank lines at both ends. A heading outside WF_SECTIONS is ignored,
    and a '# ' title line is the workflow's title: not a section, and its text belongs to no body.
    """
    def trimmed(body):
        while body and not body[0].strip():
            body.pop(0)
        while body and not body[-1].strip():
            body.pop()
        return "\n".join(body)

    sections = {}
    name = None
    body = []
    for line in text.splitlines():
        if line.startswith("## "):
            if name is not None:
                sections[name] = trimmed(body)
            label = line[3:].strip()
            name = label if label in WF_SECTIONS else None
            body = []
        elif line.startswith("# "):
            # A title closes whatever section was open; its own text goes to no body.
            if name is not None:
                sections[name] = trimmed(body)
            name = None
            body = []
        elif name is not None:
            body.append(line)
    if name is not None:
        sections[name] = trimmed(body)
    return sections


def wf_l2_score(text: str) -> str:
    """The recorded L2 score as '<a>/<b>', read ONLY from the L2 Learning body.

    The search runs over that one body and never over the whole document, so a score written in
    any other section is not the L2 score. '' when the body carries no int/int score -- prose
    like 'recorded after the pilot' is not a score.
    """
    found = re.search(r"L2\s+score:\s*(\d+)\s*/\s*(\d+)",
                      wf_sections(text).get("L2 Learning", ""))
    if not found:
        return ""
    return "%s/%s" % (found.group(1), found.group(2))


def wf_ready(path) -> dict:
    """May workflows/<name>.md go on a role? Only with all five sections and a real L2 score.

    Reads the file and never raises: an absent, unreadable or empty file answers ok=False with
    the path named in why, every section missing and no score. '0/8' is a real score, so ok keys
    on the score parsing, never on the string being truthy.
    """
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        # Unreadable and absent get the same answer: the file itself is what is missing.
        return {"name": target.stem, "missing": tuple(WF_SECTIONS), "l2": "",
                "ok": False, "why": "no workflow file at %s" % path}
    sections = wf_sections(text)
    missing = tuple(n for n in WF_SECTIONS if n not in sections)
    l2 = wf_l2_score(text)
    parsed = bool(l2)  # wf_l2_score returns '' when nothing parses, so '0/8' still counts
    if "L2 Learning" in sections and not parsed:
        why = "L2 Learning has no recorded score"
    elif missing:
        why = "missing sections: " + ", ".join(missing)
    else:
        why = ""
    return {"name": target.stem, "missing": missing, "l2": l2,
            "ok": (not missing) and parsed, "why": why}


if __name__ == "__main__":
    sys.exit(main())
