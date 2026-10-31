#!/usr/bin/env python3
"""session_review.py: finished sessions (Claude and DeepSeek) are queued, read by the manager for
misses, and each finding gets an RCA whose proposed tasks land at the top of QUEUE.md (2026-09-28).
A fake model and temporary folders; the real repository is not touched."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(ROOT / "scripts")]
import session_review as sr  # noqa: E402
import skills  # noqa: E402
# The workers' skill loads (plan 3c) go to a scratch file, never the real workspace/skill-loads.jsonl:
# this suite wrote 'rca' rows into the live log until 2026-09-30.
skills.LOADS = Path(tempfile.mkdtemp(prefix="skill-loads-")) / "skill-loads.jsonl"

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def point_at(tmp):
    base = tmp / "sr"
    sr.BASE, sr.PENDING, sr.DONE, sr.FINDINGS = base, base / "pending", base / "done", base / "findings"
    sr.LEDGER, sr.RCA_DIR = base / "ledger.jsonl", tmp / "rca"
    sr.FAILED = base / "failed"
    sr.CLAUDE_DIR, sr.DSH_DIR = tmp / "claude", tmp / "dsh"
    sr.CLAUDE_DIR.mkdir()
    sr.DSH_DIR.mkdir()
    sr.DISCOVER_SINCE = 0


def claude_session(tmp, sid, text_pairs, age_h=0):
    f = sr.CLAUDE_DIR / f"{sid}.jsonl"
    rows = []
    for role, t in text_pairs:
        rows.append({"type": "user", "message": {"content": t}} if role == "user" else
                    {"type": "assistant", "message": {"content": [{"type": "text", "text": t},
                                                                  {"type": "tool_use", "name": "Bash"}]}})
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    old = time.time() - age_h * 3600
    os.utime(f, (old, old))
    return f


def dsh_session(tmp, sid, age_h=0):
    d = sr.DSH_DIR / "--home-x--" / sid
    d.mkdir(parents=True)
    rows = [{"type": "session", "id": sid},
            {"type": "user/message", "data": {"content": [{"type": "text", "text": "fix the thing"}]}},
            {"type": "assistant/message", "data": {"message": {"content": [
                {"type": "reasoning", "text": "hidden thoughts"},
                {"type": "text", "text": "I'll leave the retry logic for later."}]}}}]
    raw = d / "session.v4.jsonl"
    raw.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    subprocess.run(["zstd", "-q", "--rm", str(raw)], check=True, timeout=30)
    f = d / "session.v4.jsonl.zstd"
    os.rename(d / "session.v4.jsonl.zst", f)
    old = time.time() - age_h * 3600
    os.utime(f, (old, old))
    return f


def main():
    tmp = Path(tempfile.mkdtemp(prefix="sr-test-"))
    try:
        point_at(tmp)
        c = claude_session(tmp, "abc", [("user", "add a chat lane?"),
                                         ("assistant", "A chat lane is noted as later work.")], age_h=30)
        d = dsh_session(tmp, "session-d1", age_h=30)
        fresh = claude_session(tmp, "live", [("user", "hi")], age_h=0)

        report("Claude text: user and assistant text only, tool calls dropped",
               sr.turns("claude", c), [("user", "add a chat lane?"),
                                       ("assistant", "A chat lane is noted as later work.")])
        report("DeepSeek text: decompressed, reasoning dropped",
               sr.turns("dsh", d), [("user", "fix the thing"),
                                    ("assistant", "I'll leave the retry logic for later.")])
        big = [("assistant", "x" * 40)] * 5
        report("chunks split at turn boundaries, nothing cut", [len(p) for p in sr.chunks(big, 120)],
               [len("### ASSISTANT\n" + "x" * 40 + "\n") * 2] * 2 + [len("### ASSISTANT\n" + "x" * 40 + "\n")])

        q = sorted(sr.discover(idle_h=24))
        report("backstop queues idle sessions from both harnesses, not the live one",
               q, ["claude-abc.json", "dsh-session-d1.json"])
        report("  and never queues them twice", sr.discover(idle_h=24), [])

        notes = []
        ans_extract = ('Reviewed.\n[{"kind": "deferral", "quote": "A chat lane is noted as later work.", '
                       '"where": "assistant", "why_missed": "no task given", "covered_by": ""},'
                       ' {"kind": "unresolved", "quote": "old bug", "where": "user", "why_missed": "x",'
                       ' "covered_by": "t452"}]')
        new, cov = sr.extract(sr.PENDING / "claude-abc.json", asker=lambda p, purpose: ans_extract,
                              note_task=lambda t, x: notes.append(t))
        report("extract: one new finding, one already covered", (new, cov), (1, 1))
        report("  the covered one is noted on its task, not re-filed", notes, ["t452"])
        report("  the request moves to done", (sr.DONE / "claude-abc.json").exists(), True)
        report("  one finding file waits for its RCA", len(sr.pending_findings()), 1)

        nf, nc = sr.extract(sr.PENDING / "dsh-session-d1.json", asker=lambda p, purpose: "I could not decide.")
        report("an unusable reply records nothing and keeps the request", (nf, nc,
               (sr.PENDING / "dsh-session-d1.json").exists()), (0, 0, True))
        report("  and the attempt is counted ON the request",
               len(json.loads((sr.PENDING / "dsh-session-d1.json").read_text()).get("unusable", [])), 1)
        gave_up = []
        sr.extract(sr.PENDING / "dsh-session-d1.json", asker=lambda p, purpose: "",
                   file_task=lambda title, body: gave_up.append((title, body)) or "t999")
        report("a SECOND unusable attempt: out of the queue, not retried forever (claude-1ff24a44 ran 10 times)",
               ((sr.PENDING / "dsh-session-d1.json").exists(), (sr.FAILED / "dsh-session-d1.json").exists()),
               (False, True))
        report("  ONE task for Claude, naming both attempts and where the item went",
               (len(gave_up), "2 unusable attempts" in gave_up[0][1], "failed/dsh-session-d1.json" in gave_up[0][1]),
               (1, True, True))
        import subprocess as _sp
        real_run = _sp.run

        def slow(*a, **k):
            raise _sp.TimeoutExpired(cmd="ask_q16", timeout=6000)
        _sp.run = slow
        try:
            got = sr.ask("prompt", "session review: x part 1/1", wait_s=0)
        finally:
            _sp.run = real_run
        report("a model call past the time limit is an unusable answer, not a crash", got, "")
        report("a given-up session is not queued again by the backstop", "dsh-session-d1.json" in sr.discover(idle_h=24),
               False)

        filed = []
        f = sr.pending_findings()[0]
        rca_ans = ("# RCA\nAll causes...\n[{\"title\": \"Make deferrals carry a task\", \"kind\": \"solution\","
                   " \"why\": \"a preference lapsed\", \"change\": \"...\"}, {\"title\": \"Log deferral phrasing\","
                   " \"kind\": \"data-collection\", \"why\": \"no data\", \"change\": \"...\"}]")
        seen_prompt = []
        bodies = []
        ids = sr.rca(f, asker=lambda p, purpose, budget=0: seen_prompt.append(p) or rca_ans,
                     file_task=lambda title, body: filed.append(title) or bodies.append(body) or f"t{900 + len(filed)}")
        report("RCA: the whole skill is in the prompt", "Rule 4" in seen_prompt[0] and "Rule 5" in seen_prompt[0], True)
        report("  ONE task for the finding: the first (most upstream) fix", filed,
               ["Make deferrals carry a task"])
        report("  with the data-collection plan as its checklist",
               "- [ ] [data-collection] Log deferral phrasing" in bodies[0], True)
        real_open = sr._task_open
        sr._task_open = lambda tid: tid == "t777"
        noted, filed2 = [], []
        fake = {"quote": "q", "key": "kk", "kind": "deferral", "request": "r"}
        ff = sr.FINDINGS / "kk.json"
        ff.write_text(json.dumps(fake))
        sr.rca(ff, asker=lambda p, purpose, budget=0: '[{"title": "A", "kind": "solution", "covered_by": "t777"}]',
               file_task=lambda t, b: filed2.append(t) or "t999", note_task=lambda t, x: noted.append(t))
        sr._task_open = real_open
        report("  an item an open task covers is a note on it, not a new task", (noted, filed2), (["t777"], []))
        for x in sr.RCA_DIR.glob("kk.md"):
            x.unlink()
        report("  report written", len(list(sr.RCA_DIR.glob("*.md"))), 1)
        report("  finding retired", len(sr.pending_findings()), 0)

        sr2 = sr.pending_findings()
        report("status counts it all", {k: sr.status()[k] for k in ("reviewed", "rcas", "tasks")},
               {"reviewed": 1, "rcas": 2, "tasks": 2})   # the second RCA is the covered_by case: a note, no new task
        # a session that RESUMES after the backstop reviewed it: only the new turns are queued
        c2 = claude_session(tmp, "resumed", [("user", "a"), ("assistant", "b")], age_h=30)
        first = sr.discover(idle_h=24)
        report("resumed session: first review queued", first, ["claude-resumed.json"])
        sr.extract(sr.PENDING / "claude-resumed.json", asker=lambda p, purpose: "[]")
        with open(c2, "a") as fh:
            fh.write(json.dumps({"type": "user", "message": {"content": "more work"}}) + "\n")
        again = sr.enqueue("claude", "resumed", c2)
        report("  when it grows, only the new part is queued", again, "claude-resumed--from-2.json")
        req = json.loads((sr.PENDING / again).read_text())
        report("  covering exactly the new turns", (req["from_turn"], req["to_turn"]), (2, 3))
        report("  and nothing is queued when it has not grown", sr.enqueue("claude", "abc", c), None)
        import dispatch_loop as dl
        L = dl.LEVELS
        report("priority order: preflight < supervise < session review = rca < script review",
               L["preflight"] < L["supervise"] < L["session-review"] == L["rca"] < L["script-review"], True)

        # t606: a part that answers is KEPT; a retry resumes at the first part that did not.
        claude_session(tmp, "many", [("user", "p" * 50_000), ("assistant", "q" * 50_000),
                                     ("user", "r" * 50_000)], age_h=30)
        sr.discover(idle_h=24)
        rq = sr.PENDING / "claude-many.json"
        asked = []

        def flaky(p, purpose):
            asked.append(purpose)
            return "" if "part 2/" in purpose else ('[{"kind": "deferral", "quote": "Q-%d", "where": "a",'
                                                    ' "why_missed": "x", "covered_by": ""}]' % len(asked))
        n1, _ = sr.extract(rq, asker=flaky)
        report("t606: part 1's finding is written though part 2 failed", n1, 1)
        report("  and the request remembers part 1 is done", json.loads(rq.read_text()).get("parts_done"), 1)
        asked.clear()
        sr.extract(rq, asker=lambda p, purpose: asked.append(purpose) or "[]")
        report("  the retry starts at part 2, never asking part 1 again",
               [x.rsplit(" ", 1)[-1] for x in asked], ["2/3", "3/3"])
        report("  and finishes the request", (sr.DONE / "claude-many.json").exists(), True)
        report("extract carries the open tasks inline and caps its rounds",
               ("{open_tasks}" in sr.EXTRACT, 0 < sr.EXTRACT_ROUNDS <= 6), (True, True))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
