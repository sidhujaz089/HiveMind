#!/usr/bin/env python3
"""t790 step 1: script_audit's READ->gold mapping and its LOOP, with a fake model and fake scores.

The loop is the part that decides what reaches the script: an edit is KEPT only when better() says
so, a failed edit gets TRIES attempts in all (the model is re-asked with the scores), then goes to
Claude's list and the loop CARRIES ON to the next edit. No model, no git, no live scoring: a temp
directory stands in for the sandbox worktree.
"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import script_audit as A  # noqa: E402

fails = []


def check(name, got, want):
    ok = got == want
    print(f"  {'ok' if ok else 'FAIL'}  {name}" + ("" if ok else f": got {got!r} want {want!r}"))
    if not ok:
        fails.append(name)


# --- READ -> the reference format
rows = [{"kind": "deferral", "quote": "I'll add the prune later", "why_missed": "no task", "covered_by": ""},
        {"kind": "unresolved", "quote": "the 500 is unexplained", "covered_by": "t244"},
        {"kind": "for-jas", "quote": "want me to delete X?"},
        {"kind": "deferral", "quote": "settled after all", "disposition": "resolved-in-session"}]
g = A.to_gold(rows, "workspace/session-reviews/done/x.json", "claude-abcd1234", "swift")
check("one item per row", len(g["items"]), 4)
check("missed: open deferral yes, covered no, for-jas either, settled no",
      [i["missed"] for i in g["items"]], ["yes", "no", "either", "no"])
check("anchors carry the exact quote", g["items"][0]["anchors"], ["I'll add the prune later"])
check("the cover is kept", g["items"][1]["cover"], "t244")
check("by names the model", g["by"], "swift")
check("about is not cut", g["items"][0]["about"], "deferral: no task")

# --- LOOP
wt = Path(tempfile.mkdtemp(prefix="t790-"))
(wt / "scripts").mkdir()
target = "scripts/session_candidates.py"
(wt / target).write_text("A = 1\nB = 2\nC = 3\n", encoding="utf-8")
(wt / "scripts" / "semantic.py").write_text("X = 0\n", encoding="utf-8")


def score(gold, root):
    t = (root / target).read_text()
    real = 1 + ("A = 10" in t) + ("C = 30" in t)          # an edit "surfaces" a real item
    return {"real": 3, "real_surfaced": real, "flagged": 5, "noise": 4}


def set_score(root):
    t = (root / target).read_text()
    return {"real": 9, "real_surfaced": 5 - ("B = 20" in t), "flagged": 9, "noise": 9}   # B loses one


asked = []


def asker(prompt, purpose):
    asked.append(purpose)
    if "retry edit 2" in purpose:                       # the B edit: the model gives up on retry 2
        return "[]" if purpose.endswith("(3/3)") else json.dumps(
            [{"file": target, "old": "B = 2", "new": "B = 20", "why": "again"}])
    return "[]"


edits = [{"file": target, "old": "A = 1", "new": "A = 10", "why": "surface item 1"},
         {"file": target, "old": "B = 2", "new": "B = 20", "why": "loses a real miss in the set"},
         {"file": target, "old": "nope", "new": "x", "why": "old text absent"},
         {"file": target, "old": "C = 3", "new": "C = 30", "why": "surface item 2"}]
rows_logged = []
kept, failed, end, set_end = A.run_loop({"session": "s"}, edits, wt, asker=asker, score=score,
                                        set_scorer=set_score, log=rows_logged.append)
check("the two helpful edits are kept, in order", [k["new"] for k in kept], ["A = 10", "C = 30"])
check("the harmful and the unappliable edits fail", sorted(f["old"] for f in failed), ["B = 2", "nope"])
text = (wt / target).read_text()
check("the sandbox holds the kept edits and not the failed one", text, "A = 10\nB = 2\nC = 30\n")
check("the loop reached the LAST edit after failures", kept[-1]["old"], "C = 3")
b = [f for f in failed if f["old"] == "B = 2"][0]
check("a failing edit was re-asked, with TRIES as the ceiling", b["attempts"] <= A.TRIES and
      any("retry edit 2" in p for p in asked), True)
check("a failure says why", "lost a real miss in the reference set" in b["verdict"], True)
check("every attempt is logged", len(rows_logged) >= 5, True)
check("the end scores are the last kept state", end["real_surfaced"], 3)
for f in fails:
    print("FAIL:", f)
print("test_script_audit_run:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
