#!/usr/bin/env python3
"""Possible escapes: a defect reported in a file a kept model build changed (t208).

    python3 scripts/escape_watch.py            # candidates, newest first
    python3 scripts/escape_watch.py --days 14
    python3 scripts/escape_watch.py dismiss <task> <merge> --why '...'   # not an escape; stop asking

An ESCAPE is a bug in work the review gate KEPT. reviewq.py records one when someone says so
(`reviewq.py escaped`), and session_start counts them -- "Recorded escapes so far: 0. If that is
really true across 116 kept patches, the pipeline is perfect. Check." Nothing ever checked: an
escape was only ever as visible as someone's memory. With auto-merge live (D-36), builds now reach
main that no person read.

THE RULE, mechanical and deliberately over-inclusive: for every merge of a coder/* branch in the
window, take the files it changed; any OPEN task CREATED AFTER that merge whose text names one of
those files is a candidate. A candidate is a question for Claude, not a verdict: confirm it with
`reviewq.py escaped <task> --why ...` or dismiss it. Files every task names (QUEUE.md, ledgers)
are ignored.
"""
import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DISMISSED = REPO / "workspace" / "escape-dismissals.jsonl"
VERDICTS = REPO / "workspace" / "session-reviews" / "escape-verdicts.jsonl"
VERDICT_WORDS = ("confirmed-escaped", "dismissed", "expired")
IGNORE = {"workspace/tasks/QUEUE.md", "scripts/autonomy_loop.sh", "scripts/library.json"}


def git(*a):
    return subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True, timeout=120).stdout


def kept_merges(days):
    """[(sha, date_iso, branch, task, [files])] for coder/* merges into main in the window."""
    out = []
    for line in git("log", "--merges", f"--since={days} days ago", "--format=%H|%cI|%s").splitlines():
        sha, at, subj = line.split("|", 2)
        m = re.search(r"Merge (coder/brief-[0-9a-f]+)(?:\s*\((t\d+)\))?", subj)
        if not m:
            continue
        files = [f for f in git("diff", "--name-only", f"{sha}^1", sha).split()
                 if f not in IGNORE and not f.startswith("workspace/") and "/tests/" not in f]
        if files:
            out.append((sha[:8], at, m.group(1), m.group(2) or "", files))
    return out


def open_tasks():
    sys.path.insert(0, str(REPO / "projects" / "app-repo"))
    import taskstore
    return [(m, b) for m, b in taskstore.ordered() if m.get("status") not in ("done", "cancelled")]


def dismissed():
    """{(task, merge)} Claude has judged NOT an escape, with a reason (2026-10-09). Without this the
    same 39 candidates were re-listed every session and never answered -- a question with no way to
    answer it is noise. A dismissal is per (task, merge): a NEW merge to the same file asks again."""
    import json
    out = set()
    if DISMISSED.is_file():
        for line in DISMISSED.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                out.add((r["task"], r["merge"]))
            except (ValueError, KeyError) as e:
                print(f"  NOTE: unreadable dismissal row skipped ({e}): {line[:80]}", file=sys.stderr)
    return out


def dismiss(task, merge, why, who="claude-code"):
    import json
    if not why.strip():
        raise SystemExit("a dismissal needs --why")
    with open(DISMISSED, "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "task": task,
                            "merge": merge[:8], "who": who, "why": why}) + "\n")
    # The dismissal row stays exactly as it is: a verdict row is ADDED, so a judged candidate is
    # visible in the ledger that counts judgements, not only in the file that stops the asking.
    record_verdict({"task": task, "merge": merge, "files": [], "why": why}, "dismissed", actor=who)


def candidate_id(task, merge) -> str:
    """The key a verdict is filed under: the task and the first 8 chars of the merge sha -- the
    same shape dismiss() writes, so a verdict names one (task, merge), not one task."""
    return f"{task}@{str(merge)[:8]}"


def record_verdict(cand, verdict, actor="claude-code"):
    """Append the verdict a candidate received, so "0 recorded escapes" is evidence and not an
    unjudged list. Every check runs BEFORE the file is opened: a refusal writes nothing."""
    import json
    if verdict not in VERDICT_WORDS:
        raise ValueError(f"unknown verdict {verdict!r}: use one of {', '.join(VERDICT_WORDS)}")
    for key in ("task", "merge"):
        if not str(cand.get(key) or "").strip():
            raise ValueError(f"candidate carries no {key}: {cand!r}")
    files = cand.get("files")
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "candidate_id": candidate_id(cand["task"], cand["merge"]),
           "merge_sha": str(cand["merge"])[:8],
           "task": cand["task"],
           "files": files if isinstance(files, str) else list(files or []),
           "verdict": verdict,
           "actor": actor}
    if cand.get("why") and str(cand["why"]).strip():
        row["why"] = cand["why"]
    ledger = VERDICTS                                 # read the global at call time: callers rebind it
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row


def verdicts() -> list:
    """The ledger rows, newest last. A line json.loads cannot parse is kept as
    {"_unreadable": <the whole raw line>}, reported and never dropped -- the same rule dismissed()
    follows for its own rows."""
    import json
    path = VERDICTS                                   # read the global at call time: callers rebind it
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError as e:
            print(f"  NOTE: unreadable verdict row reported, not dropped ({e}): {line}", file=sys.stderr)
            out.append({"_unreadable": line})
    return out


def check_verdicts(days=14, cands=None, rows=None) -> tuple:
    """(exit code, lines): every candidate candidates() printed owes a verdict row, keyed on its own
    (task, merge). An unreadable ledger row fails the check closed -- a ledger that cannot be read is
    not evidence that its candidates were judged."""
    if cands is None:
        cands = candidates(days)
    if rows is None:
        rows = verdicts()
    judged = {r.get("candidate_id") for r in rows if r.get("verdict") in VERDICT_WORDS}
    lines = []
    for c in cands:
        cid = candidate_id(c.get("task"), c.get("merge"))
        if cid in judged:
            continue
        files = c.get("files")
        files = files if isinstance(files, str) else ", ".join(str(f) for f in (files or []))
        lines.append(f"  unjudged {cid} ({c.get('task')}): merge {c.get('merge')} changed {files}")
    for r in rows:
        if "_unreadable" in r:
            lines.append(f"  unreadable verdict row, reported whole: {r['_unreadable']}")
    return (1, lines) if lines else (0, [])


def candidates(days=14):
    merges = kept_merges(days)
    gone = dismissed()
    found = []
    for meta, body in open_tasks():
        if str(meta.get("title", "")).startswith("Scope RCA:"):
            continue                            # a defect-class collector, about scopes, not code
        created = str(meta.get("created") or "")
        stamp = re.sub(r"(\d{4}-\d\d-\d\d)T(\d\d)(\d\d)(\d\d)Z", r"\1T\2:\3:\4+00:00", created)
        text = f"{meta.get('title', '')}\n{body}"
        for sha, at, branch, task, files in merges:
            if stamp and stamp < at:
                continue                        # reported before the merge: not about it
            hit = [f for f in files if f in text or Path(f).name in text]
            if hit and meta.get("id") != task and (meta.get("id"), sha) not in gone:
                found.append({"task": meta["id"], "title": meta.get("title", ""), "merge": sha,
                              "branch": branch, "built_for": task, "files": hit})
    return found


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    if argv is None:
        argv = sys.argv[1:]
    if argv[:1] == ["dismiss"]:
        d = argparse.ArgumentParser(prog="escape_watch.py dismiss")
        d.add_argument("task"); d.add_argument("merge"); d.add_argument("--why", required=True)
        x = d.parse_args(argv[1:])
        dismiss(x.task, x.merge, x.why)
        print(f"  dismissed {x.task} x {x.merge[:8]}")
        return 0
    ap.add_argument("--days", type=int, default=14)
    a = ap.parse_args(argv)
    c = candidates(a.days)
    print(f"  {len(c)} possible escape(s) in the last {a.days} days")
    for x in c:
        print(f"    {x['task']} names {', '.join(x['files'])} -- changed by {x['branch']} "
              f"({x['built_for'] or '?'}, merge {x['merge']}): {x['title'][:80]}")
    if c:
        print("  Confirm: python3 scripts/reviewq.py escaped <built_for task> --why '...'   or dismiss.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
