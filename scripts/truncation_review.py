#!/usr/bin/env python3
"""Gemma reviews every script on the master list for truncation, and reports to Claude.

    python3 scripts/truncation_review.py            # review what has not been reviewed yet
    python3 scripts/truncation_review.py --report   # rewrite the report from what is recorded

Jas, 2026-09-14: "Is there a master list of all scripts ... can we get Gemma to review them all
for truncation? Then report to you the findings." The list is workspace/SCRIPTS.md
(script_census.py). The instruction behind it is his from the same day: remove truncation on the
coding side; a cap that must survive has to declare what it dropped.

RESUMABLE AND IDEMPOTENT. Each file is keyed by its sha256; a file already reviewed at its current
content is skipped, so this can be killed and re-run, and an edited file is reviewed again.

FINDINGS ARE CHECKED, NOT TRUSTED. The model cites a line NUMBER and nothing else; this reads the
code back off disk, so a finding can never display a line that is not in the file, and one citing
a line out of range is kept but marked unverified. It used to be asked for the EXACT code as its
own proof, which on 2026-09-15 cost 7.7 hours: escaping a line of Python that already contained
quotes and backslashes broke the JSON 40 times on one file, identically, and a retry loop with no
cap re-sent it every ten minutes. A model that must escape source code inside JSON will eventually
fail to, and a proof that is expensive to produce is not worth what it proves.

The model sees each file WHOLE when it fits; a file too large for the window is shown with a
declared cut (loudcap), and the finding row records that the review was partial.
"""
import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "projects" / "local-inference"))
import roles  # noqa: E402  -- the model behind a role comes from SERVING.json
LEDGER = REPO / "workspace" / "truncation-review.jsonl"
REPORT = REPO / "workspace" / "truncation-review.md"
# Q16, NOT GEMMA, from 2026-09-15. Jas had already made Q16 the script certifier in
# corpus_scripts.py and this is the same judgement on the same files: a defect audit has no
# oracle, so a wrong verdict here is invisible, where a wrong patch is caught by its target
# suite. Gemma gets the oracle-gated coding instead. Expect reviews to take longer -- bf16
# decodes at ~8.9 tok/s against gemma4:26b's ~20.7.
MODEL = roles.for_job("script_review", "qwen3.8:27b-mtp-bf16")
# THE SERVER THE SCHEDULER CHOSE (2026-10-02). This posted to 127.0.0.1:11434 whatever the job, so a
# script review the Senior "took" ran on the Manager's server beside the Manager's work while station
# 2 sat idle. The dispatcher sets HIVEMIND_OLLAMA for a station job; unset means the main server.
OLLAMA = os.environ.get("HIVEMIND_OLLAMA", "http://127.0.0.1:11434").rstrip("/")
FILE_CAP = 120_000

def prompt_for(path, body):
    """The rubric is library_checks.MODEL_CHECKS, so adding a judgement check needs no edit here."""
    import library_checks
    rubric = "\n".join(f"  {cid:22} {what}" for cid, what, _ev in library_checks.MODEL_CHECKS)
    return f"""You are auditing ONE source file for defects this repository has already paid for.
Report only these, each under its id:
{rubric}

For each finding give the check id, the LINE NUMBER, whether it is SILENT (nothing records the
problem) or DECLARED, a severity (high = silent and on content a model reads or a record stores;
medium = hides items or can mislead a later check; low = display or declared), and one sentence
why. Do NOT report parsing slices (line[3:]), fixed formats (timestamp[:10], sha[:12]) or
protocol headers as truncation.

Give the line NUMBER only. Do NOT quote the code: the file below is already numbered and this
tool reads the line back off disk, so a quoted line adds nothing and its escaping is the single
thing most likely to make your reply unusable.

Reply with ONE JSON object and nothing else:
{{"findings": [{{"check": "truncation", "line": 123, "silent": true,
                "severity": "high|medium|low", "why": "..."}}]}}
If there are none: {{"findings": []}}

=== FILE: {path} ===
{body}
"""




def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def rows():
    out = []
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                out.append({})
    return out


def targets():
    import script_census
    return [p for p in script_census.scripts() if "/tests/" not in f"/{p}"]


def ask(prompt):
    import settings
    body = settings.request("coder", MODEL, [{"role": "user", "content": prompt}], num_predict=6000)
    body["think"] = False
    # ANY ENGINE (docs/ENGINES.md): the job's server (HIVEMIND_OLLAMA) or wherever MODEL is served.
    import infer
    d = infer.chat(body, timeout=settings.timeout_for(6000, model=MODEL))
    return (d.get("message") or {}).get("content") or "", d.get("done_reason")


def read_reply(raw):
    """jsonx.one's verdict on a review reply, after the two recoveries below. Also used to decide
    whether a past unreadable reply is STILL unreadable (unreadable_attempts)."""
    import jsonx
    r = jsonx.one(raw, ["findings"])
    if r["status"] != "ok" and '\\"' in raw:
        # A REPLY WRITTEN INSIDE ITS OWN STRING (2026-10-02). gateway.py was given up after three
        # identical "invalid-json" replies: the model opened {"findings": [{... "why": "..., never
        # closed that string, and wrote its whole re-check AND its final ```json answer inside it,
        # escaped (\" and \n). Unescaping once exposes the real answer; nothing else is guessed.
        r2 = jsonx.one(raw.replace('\\n', '\n').replace('\\"', '"'), ["findings"])
        if r2["status"] == "ok":
            r = r2
    if r["status"] != "ok":
        # AN UNCLOSED OUTER OBJECT (2026-10-02, auto_merge.py given up after 3 identical replies): the
        # model reasoned inside the last "why" and ended with "}] -- the closing } never came.
        # jsonx.repair_brackets (the coder's repair, structure-only) closes it; used only if it parses.
        fixed = jsonx.repair_brackets(raw)
        r3 = jsonx.one(fixed, ["findings"]) if fixed else {"status": r["status"]}
        if r3["status"] == "ok":
            r = r3
    return r


def review(rel, ask_fn=ask):
    import jsonx
    from loudcap import cap
    p = REPO / rel
    text = p.read_text(encoding="utf-8", errors="replace")
    numbered = "\n".join(f"{i:5d}  {l}" for i, l in enumerate(text.splitlines(), 1))
    partial = len(numbered) > FILE_CAP
    import skills                                   # plan 3c-i; a bare /api/chat, nothing more to open
    prompt, _sk = skills.with_skills(prompt_for(rel, cap(numbered, FILE_CAP, "file too large for one review")),
                                     "script_review")
    raw, why = ask_fn(prompt)
    r = read_reply(raw)
    if r["status"] != "ok" or not isinstance(r["obj"].get("findings"), list):
        return {"status": f"unreadable ({r['status']}, done_reason={why})", "reply": raw,
                "partial": partial, "findings": []}
    lines = text.splitlines()
    found = []
    for f in r["obj"]["findings"]:
        if not isinstance(f, dict):
            continue
        try:
            n = int(f.get("line"))
        except (TypeError, ValueError):
            n = 0
        # The model cites a line NUMBER and this reads the code back off disk, so the report can
        # no longer display a line that is not in the file. What a model can still get wrong is
        # WHICH line, and that is visible on sight now: the real source sits beside its own `why`.
        in_range = 1 <= n <= len(lines)
        f["line"], f["code"], f["verified"] = n, (lines[n - 1].strip() if in_range else ""), in_range
        found.append(f)
    found = off_flow(rel) + found
    return {"status": "ok", "partial": partial, "findings": found, "done_reason": why}


def off_flow(rel):
    """[finding] when a permanent script is not on the Flow chart -- decided mechanically, never by
    the model (Jas, 2026-10-01: "any permanent script must be represented in the flowchart, and if
    it is not documented there, it should not be eligible for approval or deployment"). The finding
    is verified by construction; library.py refuses to register such a script in the first place."""
    try:
        import flow_coverage
        if "/tests/" in str(rel) or flow_coverage.on_chart(str(rel)):
            return []
        why = "not on the Flow chart -- not eligible for approval or deployment until it is"
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        why = f"Flow chart could not be checked ({type(e).__name__}: {e}) -- not approved on a guess"
    return [{"check": "off-flow", "line": 0, "silent": False, "why": why, "code": "", "verified": True}]


def report():
    latest = {}
    for r in rows():
        if r.get("path"):
            latest[r["path"]] = r
    real = [(p, f) for p, r in latest.items() for f in r.get("findings", []) if f.get("verified")]
    unver = [(p, f) for p, r in latest.items() for f in r.get("findings", []) if not f.get("verified")]
    bad = [p for p, r in latest.items() if r.get("status") != "ok"]
    order = {"high": 0, "medium": 1, "low": 2}
    real.sort(key=lambda x: (order.get(x[1].get("severity"), 3), x[0], x[1].get("line") or 0))
    total = len(targets())
    md = ["# Truncation review — Gemma, every script on workspace/SCRIPTS.md",
          "", "**Generated by `scripts/truncation_review.py --report`. For Claude to triage.**", "",
          f"{len(latest)} of {total} scripts reviewed. {len(real)} verified finding(s), "
          f"{len(unver)} unverified (code not found in the file -- check before trusting), "
          f"{len(bad)} file(s) whose review came back unreadable.", ""]
    for sev in ("high", "medium", "low"):
        rows_ = [x for x in real if x[1].get("severity") == sev]
        md += [f"## {sev} ({len(rows_)})", ""]
        md += [f"- `{p}:{f.get('line')}` [{f.get('check') or f.get('kind')}, {'silent' if f.get('silent') else 'declared'}] "
               f"`{str(f.get('code'))}` — {f.get('why')}" for p, f in rows_]
        md.append("")
    md += [f"## Unverified ({len(unver)})", ""] + [f"- `{p}:{f.get('line')}` `{f.get('code')}`" for p, f in unver]
    md += ["", f"## Unreadable reviews ({len(bad)})", ""] + [f"- `{p}` — {latest[p].get('status')}" for p in bad]
    gone = abandoned()
    md += ["", f"## Given up on after {UNREADABLE_CAP} unreadable replies ({len(gone)})", "",
           "Out of the queue, so they hold nothing back -- and nothing will review them either "
           "until the file changes or a person looks. That is the trade the cap makes.", ""]
    md += [f"- `{a['path']}` — {a['attempts']} attempt(s) on this exact content" for a in gone]
    REPORT.write_text("\n".join(md) + "\n")
    print(f"{REPORT.relative_to(REPO)}: {len(latest)}/{total} reviewed, {len(real)} verified, "
          f"{len(unver)} unverified, {len(bad)} unreadable, {len(gone)} given up on")


REQUESTS = REPO / "workspace" / "review-requests.jsonl"

# HOW MANY UNREADABLE REPLIES BEFORE A REQUEST IS GIVEN UP ON, and why there is a cap at all.
# The scheduler takes the OLDEST pending request every tick, so a request that can never be read
# is not merely a waste -- it holds the head of the queue and nothing behind it is ever reached.
# On 2026-09-15 scripts/check_workspace.py failed identically 40 times over 7.7 hours, 29 of them
# inside six hours, and the two requests behind it were never once attempted. Three is enough to
# tell a flake from a constant: an identical failure is a bug, not a capability ceiling, and the
# fourth attempt has never told anyone anything the third did not.
UNREADABLE_CAP = 3


def reviewer_models():
    """The models whose review counts: MODEL, and the model of every role with script_review on its
    task list in ROLES.json -- read each time, never a fixed name.

    2026-10-19: script_review moved to the Manager (Swift) with the Flow-sourced role lists, but this
    still counted only the Auditor's model. Every Swift review was ignored, the same nine scripts
    stayed pending, and the dispatcher's fast path relaunched them 318 times in 15 minutes -- 60
    processes, 20 GB, and the memory guard killed Swift."""
    out = {MODEL}
    try:
        import task_routing
        d = task_routing.load()
        out |= {d["roles"][k].get("model") for k in task_routing.model_roles(d)
                if any(t.get("task") == "script_review" for t in task_routing.tasks_of(d, k))}
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: role store unreadable ({type(e).__name__}: {e}); only {MODEL}'s reviews count",
              file=sys.stderr)
    return {m for m in out if m}


def _counts(r, models=None):
    return r.get("model") is None or r.get("model") in (models if models is not None else reviewer_models())


def audited_by_current():
    models = reviewer_models()
    return {(r.get("path"), r.get("sha256")) for r in rows() if r.get("status") == "ok" and _counts(r, models)}


def unreadable_attempts():
    """{(path, sha256): n} -- how many times a review of that exact content came back unreadable."""
    tally = {}
    models = reviewer_models()
    for r in rows():
        # STILL UNREADABLE BY TODAY'S READER (2026-10-02): a reply the reader has since learned to read
        # is not evidence that the request is broken, so it does not count toward giving up -- and a
        # file given up on before a reader fix goes back into the queue for a fresh review.
        if str(r.get("status") or "").startswith("unreadable") and r.get("path") and _counts(r, models) \
                and read_reply(r.get("reply") or "")["status"] != "ok":
            key = (r["path"], r.get("sha256"))
            tally[key] = tally.get(key, 0) + 1
    return tally


def abandoned():
    """Requests given up on: capped out, and the file still has the content that could not be read.

    Reported by check_workspace.py. A request dropped from the queue and mentioned nowhere is the
    failure of t324 -- a thing skipped silently is a thing nobody fixes.
    """
    out = []
    for (path, sha256), n in sorted(unreadable_attempts().items()):
        f = REPO / path
        if n >= UNREADABLE_CAP and f.is_file() and sha(f) == sha256:
            out.append({"path": path, "sha256": sha256, "attempts": n})
    return out


def pending_requests():
    """Review requests (registration, a commit that changed a script) not yet reviewed at that content."""
    done = audited_by_current()
    capped = {k for k, n in unreadable_attempts().items() if n >= UNREADABLE_CAP}
    latest = {}
    if REQUESTS.exists():
        for line in REQUESTS.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                q = json.loads(line)
            except ValueError:
                q = {}
            if q.get("path"):
                latest[q["path"]] = q
    out = []
    try:
        lib = json.loads((REPO / "scripts" / "library.json").read_text(encoding="utf-8"))["scripts"]
    except (OSError, ValueError):
        lib = {}
    for path, q in latest.items():
        # Test suites carry defects on purpose and are never audited -- filtered here as well as
        # at request time, so a request filed before that rule existed cannot slip one through.
        if (lib.get(path) or {}).get("kind") in ("test", "oracle-helper") or "/tests/" in f"/{path}":
            continue
        f = REPO / path
        if (f.is_file() and sha(f) == q.get("sha256") and (path, q["sha256"]) not in done
                and (path, q["sha256"]) not in capped):
            out.append(q)
    return sorted(out, key=lambda q: q["at"])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--path", default=None, help="review one script (a priority job from the scheduler)")
    ap.add_argument("--model", default=None, help="the model of the role the scheduler gave this job")
    a = ap.parse_args()
    if a.model:
        global MODEL
        MODEL = a.model
    if a.report:
        report()
        return 0
    # ONE REVIEWER AT A TIME. It runs hourly from cron to review new and changed scripts, and a
    # full pass can take longer than an hour; a second copy would review the same files twice.
    import fcntl
    lock = open(REPO / "workspace" / "run-logs" / ".truncation-review.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another truncation review is running; exiting")
        return 0
    done = audited_by_current()
    todo = ([Path(a.path)] if a.path else
            [p for p in targets() if (str(p), sha(REPO / p)) not in done])
    if a.limit:
        todo = todo[:a.limit]
    print(f"{len(todo)} script(s) to review with {MODEL}", flush=True)
    for i, rel in enumerate(todo, 1):
        t0 = time.time()
        try:
            res = review(rel)
        except Exception as e:                                      # noqa: BLE001
            res = {"status": f"error: {type(e).__name__}: {e}", "findings": []}
        row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "path": str(rel),
               "sha256": sha(REPO / rel), "model": MODEL, "seconds": round(time.time() - t0), **res}
        with LEDGER.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"[{i}/{len(todo)}] {rel}: {res['status']}, {len(res['findings'])} finding(s), "
              f"{row['seconds']}s", flush=True)
        if i % 10 == 0:
            report()
    report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
