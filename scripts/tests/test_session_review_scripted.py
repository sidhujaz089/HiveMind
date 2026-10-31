#!/usr/bin/env python3
"""The scripted session review (t663, 2026-10-05): code finds what the session never came back to, a fake
Manager triages it, code proves every cover. Fake model, temporary folders; nothing real is touched.
Cases are the audit's own: s07's nightly-prune miss (real), a settled offer, and Q4's wrong covers."""
import json, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "scripts" / "tests")]
import session_review as sr  # noqa: E402
from test_session_review import point_at, claude_session  # noqa: E402

FAILS = []
def ck(name, got, want):
    ok = got == want
    print(f"  [{'ok' if ok else 'BAD'}]  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))
    if not ok:
        FAILS.append(name)

# --- prove_cover: a model's COVERED is an assertion ---------------------------------------------
miss = {"quote": "The nightly backup still doesn't prune automatically.", "what": "decide nightly NAS pruning",
        "context": "", "verdict": "covered"}
same = {"quote": "t036 second offsite backup -- routed to Jas", "what": "second offsite backup copy",
        "context": "", "verdict": "covered"}
ck("a task about the SAME work is a proven cover (t036 is the second offsite backup)",
   sr.prove_cover({**same, "cover": "t036"})["verdict"], "covered")
ck("t206 (prune the NAS bundles by hand) is NOT proof the NIGHTLY backup prunes -- the audit's s07 miss",
   sr.prove_cover({**miss, "cover": "t206"})["verdict"], "missed")
r = sr.prove_cover({**miss, "cover": "t328"})
ck("a task about another thing (t328, an enforcement ratchet) is overruled to missed", r["verdict"], "missed")
ck("  and says why, and keeps what the model said", ("overruled" in r, r["model_cover"]), (True, "t328"))
ck("a session close-out commit is never a cover",
   sr.prove_cover({**miss, "cover": "1853bafb"})["verdict"], "missed")
ck("a cover that names no commit at all is overruled", sr.prove_cover({**miss, "cover": "deadbee"})["verdict"], "missed")
ck("'in-session' needs no proof", sr.prove_cover({**miss, "cover": "in-session"})["verdict"], "covered")
ck("a missed verdict passes through untouched", sr.prove_cover({**miss, "verdict": "missed", "cover": ""})["cover"], "")

# --- triage: the Manager answers one line per item -------------------------------------------------
items = [{"n": i, "quote": f"q{i}", "context": "", "type": t, "turn": i, "repeats": [], "near": [], "commits": [],
          "offered": ["t206"]} for i, t in ((1, "NOTICED"), (2, "FOR-JAS"), (3, "PROMISED"), (4, "PROMISED"))]
seen = []
def manager(prompt, why):
    seen.append(prompt)
    return "\n".join(['{"n": 1, "verdict": "missed", "kind": "unresolved", "what": "decide pruning"}',
                      '{"n": 2, "verdict": "drop"}',
                      '{"n": 3, "verdict": "covered", "cover": "t999"}',      # not offered
                      "not json at all"])                                   # n=4 unanswered
rows = sr.triage(items, manager, "claude", "x", "2026-10-04")
ck("one prompt for four items (batch 12)", len(seen), 1)
ck("verdicts follow the Manager", [r["verdict"] for r in rows], ["missed", "drop", "missed", "missed"])
ck("a cover the search never offered is refused, said so", "not among those offered" in rows[2]["overruled"], True)
ck("an item the Manager did not answer is KEPT, flagged unanswered", rows[3].get("unanswered"), True)
ck("an unanswered offer to Jas keeps its kind from its type", sr.triage([items[1]], lambda p, w: "", "c", "x", "d")[0]["kind"], "for-jas")

# --- end to end: extract_scripted files the miss and not the settled offer ------------------------
tmp = Path(tempfile.mkdtemp(prefix="sr-scripted-"))
point_at(tmp)
sr.FOR_JAS = sr.BASE / "for-jas"
claude_session(tmp, "s1", [
    ("user", "how is the backup?"),
    ("assistant", "The nightly backup still doesn't prune automatically. Say if you want that added."),
    ("user", "ok"),
    ("assistant", "The kiosk wallpaper bug is real and I'll leave it for the next session."),
    ("user", "thanks")], age_h=30)
sr.discover(idle_h=24)
prompts = []
def mgr(prompt, why):
    prompts.append(prompt)
    return "\n".join(json.dumps({"n": n, "verdict": "missed", "kind": "deferral", "what": "x"})
                     for n in range(1, 12))
new, cov = sr.extract_scripted(sr.PENDING / "claude-s1.json", asker=mgr, file_task=lambda t, b: "t999")
fs = [json.loads(f.read_text()) for f in sr.pending_findings()]
ck("findings were filed for what the session never came back to", new >= 1 and len(fs) == new, True)
ck("a filed finding carries kind, quote, where and a reason", {"kind", "quote", "where", "why_missed"} <= set(fs[0]), True)
ck("the request moved to done", (sr.DONE / "claude-s1.json").is_file(), True)
ck("the prompt shows the Manager the dropped sentence", "kiosk wallpaper" in prompts[0], True)
ck("the live default is the scripted path: Swift's grade is in, 10/10 real misses (t663, 2026-10-07)", sr.SCRIPTED, True)

print("test_session_review_scripted:", "FAILED" if FAILS else "ok")
sys.exit(1 if FAILS else 0)
