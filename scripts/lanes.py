#!/usr/bin/env python3
"""The four lanes' operational board: status, claims, questions. Lives OUTSIDE git.

    python3 scripts/lanes.py post   L1 "<item> -- DONE|BLOCKED|HANDED-OVER -- <commit or reason>"
    python3 scripts/lanes.py claim  L1 <path|tNNN> "<why>" [--hours 4]
    python3 scripts/lanes.py release L1 <path|tNNN>
    python3 scripts/lanes.py ask    L1 L2 "<question>"          # prints the question id
    python3 scripts/lanes.py answer L2 <qid> "<answer>"
    python3 scripts/lanes.py read   [--since <UTC ISO>] [--lane L1]
    python3 scripts/lanes.py claims                             # unexpired claims only
    python3 scripts/lanes.py status                             # rewrite the view, print its path

DECISIONS GO IN THE FOLDER, OPERATIONS GO ON THE BOARD. `workspace/Claude and DeepSeek/` (01, 02, ...)
is the decision record: numbered, append-only, what the lanes put their names to. This board is
ephemeral and machine-readable: who is doing what, who holds which file, who is waiting on whom.

WHY OUTSIDE GIT (2026-10-26, Claude and DeepSeek, files 03-06): every lane works in its own worktree.
A board inside the repo is either a worktree copy the other three cannot see until it merges, or a
tracked file four branches append to -- a merge conflict on every merge. So the board is one file at
an absolute path, appended under an flock, and the rendered view sits beside it, also outside git.

A claim expires at its `until`: a lane that stops mid-task cannot hold a file forever. First
unexpired claim wins; `release` (at merge) ends it early. Rows are never edited -- a correction is the
next row.
"""

import argparse
import fcntl
import json
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOME = Path(os.environ.get("LANES_HOME") or Path.home() / ".cache/hivemind-tmp/lanes")
BOARD = HOME / "board.jsonl"
VIEW = HOME / "STATUS.md"
# THE FOUR LABELS ARE RETIRED (Jas, 2026-10-30: "These L1, 2, 3, & 4 should be gone. we made those for a
# development sprint but thats over so they should be retired."). They were named instances of ONE sprint; a
# board that only accepts those four cannot describe the next arrangement. Kept here as a LEGACY view so rows
# already on the board still show who wrote them, never as a gate on what may be written now.
LANES = {"L1": "Claude-GX10", "L2": "DSH-ThinkPad", "L3": "Claude-ThinkPad", "L4": "DSH-GX10"}
KINDS = ("post", "claim", "release", "ask", "answer")


def now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


LANE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,23}$")


def lane_ok(lane):
    """Any short label, not one of four. A board that only admits a retired sprint's names is a board that
    cannot record the arrangement after it."""
    lane = str(lane or "").strip()
    if not LANE_NAME.match(lane):
        raise SystemExit(f"a lane name is 1-24 characters of letters, digits, dot, dash or underscore; got {lane!r}")
    return lane


def append(row):
    """Append one row under an exclusive lock. The only writer of the board."""
    HOME.mkdir(parents=True, exist_ok=True)
    with BOARD.open("a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    render()
    return row


def rows():
    """Every row; a line that does not parse is reported, never silently skipped."""
    if not BOARD.exists():
        return []
    out = []
    for n, line in enumerate(BOARD.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            out.append({"kind": "corrupt", "at": "", "lane": "?", "text": f"board line {n} does not parse"})
    return out


def active_claims(rs=None, at=None):
    """{what: claim row} for claims that are unexpired and not released. First claim wins."""
    at = at or now()
    held = {}
    for r in rs if rs is not None else rows():
        what = r.get("what")
        if r.get("kind") == "claim":
            cur = held.get(what)
            if cur is None or cur["lane"] == r.get("lane") or parse(cur["until"]) <= parse(r["at"]):
                held[what] = r
        elif r.get("kind") == "release" and what in held and held[what]["lane"] == r.get("lane"):
            del held[what]
    return {w: c for w, c in held.items() if parse(c["until"]) > at}


def open_questions(rs=None):
    rs = rows() if rs is None else rs
    answered = {r.get("qid") for r in rs if r.get("kind") == "answer"}
    return [r for r in rs if r.get("kind") == "ask" and r.get("qid") not in answered]


def quotes_evidence(text):
    """mf8: True when `text` quotes a command and its output -- a line starting with '$ '
    followed by at least one non-empty line. A claim with no run behind it is a sentence."""
    lines = str(text or "").splitlines()
    for i, l in enumerate(lines):
        if l.strip().startswith("$ ") and any(x.strip() for x in lines[i + 1:]):
            return True
    return False


def require_evidence(kind, text):
    """mf8: an ANSWER or SIGNATURE post that does not quote a command and its output is
    refused -- check before claiming. The quote is a line starting with '$ ' and the
    output under it."""
    if not quotes_evidence(text):
        raise SystemExit(
            f"REFUSED -- a{'n ' + kind if kind == 'answer' else ' ' + kind} post must QUOTE "
            f"the command and its output: a line starting with '$ ' followed by what it "
            f"printed. Check before claiming (mf8).")


def answered_questions(rs=None):
    """[(ask_row, answer_row)] for every question that has an answer."""
    rs = rows() if rs is None else rs
    asks = {r.get("qid"): r for r in rs if r.get("kind") == "ask"}
    return [(asks[r["qid"]], r) for r in rs
            if r.get("kind") == "answer" and r.get("qid") in asks]


def show_answered_for(text, rs=None):
    """mf8: before a post asks for a signature, show any answered question on the same
    branch -- the asker may already have the answer they are about to ask for. A branch is
    any slash-carrying token in the post's text."""
    tokens = set(re.findall(r"[A-Za-z0-9._-]+/[A-Za-z0-9._/-]+", str(text or "")))
    # A quoted command drags file paths into the text; a path is not a branch. Dropping
    # extension-shaped tokens keeps the match on branch names (cloud/t9, bridge/t296).
    tokens = {t for t in tokens
              if not re.search(r"\.(py|sh|md|json|jsonl|txt|log|lock)$", t)}
    if not tokens:
        return []
    hits = [(a, b) for a, b in answered_questions(rs)
            if any(t in (a.get("text", "") + " " + b.get("text", "")) for t in tokens)]
    for a, b in hits:
        print(f"  ANSWERED on this branch already: [{a['qid']}] {a['lane']} -> {a['to']}: "
              f"{a['text']}\n    {b['lane']}: {b['text']}")
    return hits


def claim(lane, what, why, hours=4.0):
    lane_ok(lane)
    cur = active_claims().get(what)
    if cur and cur["lane"] != lane:
        raise SystemExit(f"REFUSED -- {what} is claimed by {cur['lane']} until {cur['until']} "
                         f"({cur.get('why', '')}). Ask them, or wait for it to expire.")
    t = now()
    return append({"kind": "claim", "at": iso(t), "lane": lane, "instance": LANES.get(lane, ""), "what": what,
                   "why": why, "until": iso(t + timedelta(hours=hours))})


def release(lane, what):
    lane_ok(lane)
    cur = active_claims().get(what)
    if not cur or cur["lane"] != lane:
        raise SystemExit(f"{lane} holds no claim on {what}")
    return append({"kind": "release", "at": iso(now()), "lane": lane, "what": what})


def render():
    """Write the human view beside the board. Outside git, so it can never conflict."""
    rs = rows()
    lines = [f"# Lanes board -- rendered {iso(now())}", "",
             f"Board: `{BOARD}` -- read with `python3 scripts/lanes.py read`.",
             "Decisions go in `workspace/Claude and DeepSeek/`; operations go on this board.", "",
             "## Claims (unexpired)", ""]
    cl = active_claims(rs)
    lines += [f"- {c['lane']} holds `{w}` until {c['until']} -- {c.get('why', '')}" for w, c in sorted(cl.items())] \
        or ["- none"]
    lines += ["", "## Open questions", ""]
    lines += [f"- [{q['qid']}] {q['lane']} -> {q['to']}: {q['text']}" for q in open_questions(rs)] or ["- none"]
    lines += ["", "## Last status per lane", ""]
    for lane, inst in LANES.items():
        last = [r for r in rs if r.get("kind") == "post" and r.get("lane") == lane]
        lines.append(f"- {lane} {inst}: " + (f"{last[-1]['at']} {last[-1]['text']}" if last else "no status yet"))
    corrupt = [r for r in rs if r.get("kind") == "corrupt"]
    if corrupt:
        lines += ["", "## Corrupt board lines", ""] + [f"- {r['text']}" for r in corrupt]
    HOME.mkdir(parents=True, exist_ok=True)
    VIEW.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return VIEW


def fmt(r):
    k = r.get("kind")
    if k == "claim":
        return f"{r['at']} {r['lane']} CLAIM {r['what']} until {r['until']} -- {r.get('why', '')}"
    if k == "release":
        return f"{r['at']} {r['lane']} RELEASE {r['what']}"
    if k == "ask":
        return f"{r['at']} {r['lane']} ASK {r['to']} [{r['qid']}] {r['text']}"
    if k == "answer":
        return f"{r['at']} {r['lane']} ANSWER [{r['qid']}] {r['text']}"
    return f"{r.get('at', '')} {r.get('lane', '?')} {k.upper()} {r.get('text', '')}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("post"); p.add_argument("lane"); p.add_argument("text")
    p = sub.add_parser("claim"); p.add_argument("lane"); p.add_argument("what"); p.add_argument("why")
    p.add_argument("--hours", type=float, default=4.0)
    p = sub.add_parser("release"); p.add_argument("lane"); p.add_argument("what")
    p = sub.add_parser("ask"); p.add_argument("lane"); p.add_argument("to"); p.add_argument("text")
    p = sub.add_parser("answer"); p.add_argument("lane"); p.add_argument("qid"); p.add_argument("text")
    p = sub.add_parser("read"); p.add_argument("--since"); p.add_argument("--lane")
    sub.add_parser("claims")
    sub.add_parser("status")
    a = ap.parse_args(argv)
    if a.cmd == "post":
        if "SIGNATURE" in a.text:
            # mf8: before a post asks for a signature, show any answered question on the
            # same branch, then hold the post to the evidence rule.
            show_answered_for(a.text)
            require_evidence("SIGNATURE", a.text)
        print(fmt(append({"kind": "post", "at": iso(now()), "lane": lane_ok(a.lane), "text": a.text})))
    elif a.cmd == "claim":
        if not (0 < a.hours <= 24):
            raise SystemExit("--hours must be between 0 and 24; a longer hold is a re-claim, not a long one")
        print(fmt(claim(a.lane, a.what, a.why, a.hours)))
    elif a.cmd == "release":
        print(fmt(release(a.lane, a.what)))
    elif a.cmd == "ask":
        lane_ok(a.to)
        print(fmt(append({"kind": "ask", "at": iso(now()), "lane": lane_ok(a.lane), "to": a.to,
                          "qid": uuid.uuid4().hex[:6], "text": a.text})))
    elif a.cmd == "answer":
        if a.qid not in {q["qid"] for q in open_questions()}:
            raise SystemExit(f"no open question {a.qid!r}")
        require_evidence("answer", a.text)   # mf8: check before claiming
        print(fmt(append({"kind": "answer", "at": iso(now()), "lane": lane_ok(a.lane), "qid": a.qid,
                          "text": a.text})))
    elif a.cmd == "read":
        since = parse(a.since) if a.since else None
        for r in rows():
            if since and r.get("at") and parse(r["at"]) <= since:
                continue
            if a.lane and a.lane not in (r.get("lane"), r.get("to")):
                continue
            print(fmt(r))
        qs = [q for q in open_questions() if not a.lane or q["to"] == a.lane]
        if qs:
            print(f"\n{len(qs)} open question(s)" + (f" for {a.lane}" if a.lane else "") + ":")
            for q in qs:
                print("  " + fmt(q))
    elif a.cmd == "claims":
        cl = active_claims()
        for w, c in sorted(cl.items()):
            print(fmt(c))
        if not cl:
            print("no unexpired claims")
    elif a.cmd == "status":
        print(render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
