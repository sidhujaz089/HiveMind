#!/usr/bin/env python3
"""Session review cross-references before it reports (Jas, 2026-10-02; t623):
finished tasks cover a finding, a later fix covers it, an offer to Jas goes to his Review list
(not an RCA), and every finding records the task ids it was checked against.
A fake model and temporary folders; the real repository is not touched."""
import json, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(ROOT / "scripts")]
import session_review as sr  # noqa: E402
import skills  # noqa: E402
skills.LOADS = Path(tempfile.mkdtemp(prefix="skill-loads-")) / "skill-loads.jsonl"
sys.path.insert(0, str(ROOT / "scripts" / "tests"))
from test_session_review import point_at, claude_session  # noqa: E402

P = F = 0
def report(name, got, want):
    global P, F
    ok = got == want; P += ok; F += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))

tmp = Path(tempfile.mkdtemp(prefix="sr-xref-"))
point_at(tmp)
sr.FOR_JAS = sr.BASE / "for-jas"
sr._task_finished = lambda t: t == "t168"            # t168 is in done/ (the t623 false positive)
claude_session(tmp, "x1", [("user", "five levels deep RCA please"),
                           ("assistant", "Want me to start it? That is t168's RCA process.")], age_h=30)
sr.discover(idle_h=24)
prompts, notes, filed = [], [], []
ans = json.dumps([
    {"kind": "deferral", "quote": "That is t168's RCA process.", "where": "a", "why_missed": "", "covered_by": ""},
    {"kind": "unresolved", "quote": "the lease bug", "where": "a", "why_missed": "", "covered_by": "fixed:0dfbe47c"},
    {"kind": "for-jas", "quote": "Want me to delete the old image?", "where": "a", "why_missed": "waits on Jas", "covered_by": ""},
    {"kind": "deferral", "quote": "a real dropped item", "where": "a", "why_missed": "none", "covered_by": ""}])
new, cov = sr.extract(sr.PENDING / "claude-x1.json", asker=lambda p, purpose: prompts.append(p) or ans,
                      note_task=lambda t, x: notes.append(t), file_task=lambda t, b: filed.append(t) or "t999")
report("a quote naming a FINISHED task is covered by it, not re-filed (t623)", "t168" in notes, True)
report("a 'fixed:<hash>' answer is covered, not a finding", cov, 2)
report("an offer to Jas is filed once to his Review list", [t[:8] for t in filed], ["Decide: "])
report("  and is never queued for an RCA", sorted(json.loads(f.read_text())["quote"] for f in sr.pending_findings()),
       ["a real dropped item"])
report("  and the same quote is not filed twice", sr._for_jas(
    sr._fkey("claude-x1.json", "Want me to delete the old image?"), {"id": "x1", "harness": "claude"},
    {"quote": "Want me to delete the old image?"}, lambda t, b: filed.append(t)), False)
f = sr.pending_findings()[0]
report("a finding records the task ids it was checked against",
       isinstance(json.loads(f.read_text()).get("candidates_consulted"), list)
       and len(json.loads(f.read_text())["candidates_consulted"]) > 0, True)
p = prompts[0]
report("the prompt lists recently finished tasks", "FINISHED IN THE LAST 14 DAYS" in p, True)
report("the prompt says the harness checks coverage, so the model only lists candidates",
       "searches every task" in p, True)
report("the prompt gives the session's end date", "This session ended on 20" in p, True)
report("the prompt offers the for-jas kind", '"for-jas"' in p, True)

# THE HARNESS SEARCHES, THE MODEL PICKS (Frontline trial 2026-10-02: Gemma made 0 tool calls).
q = ("I'll finish the parts that don't need that permission (documenting the root cause and the "
     "fix), then stop and ask you directly for the systemd edit.")
offered = []
def judge(prompt, purpose):
    offered.append(prompt)
    return "t328"
cov, how = sr.verify_cover({"quote": q}, "2026-09-28", judge)
report("the search offers t328 (the real cover the title list missed)", "t328" in offered[0], True)
report("  and the judge's pick from the offer is the cover", cov, "t328")
report("an answer NOT among the options is refused",
       sr.verify_cover({"quote": q}, "2026-09-28", lambda p, purpose: "t999")[0], "")
report("'none' leaves it a finding", sr.verify_cover({"quote": q}, "2026-09-28", lambda p, purpose: "none")[0], "")
report("no shared terms: the model is not asked at all",
       sr.verify_cover({"quote": "zzqx wwvy"}, "2026-09-28", lambda p, purpose: offered.append("ASKED") or "none"),
       ("", "no task or commit shares its terms"))
report("  (it was not asked)", "ASKED" in offered, False)

# THE ROLE, NOT "THE MANAGER" (Jas, 2026-10-02): Frontline was told "You are the manager" and given
# the Manager's role skill (briefing-the-local-model) because extract never said which role ran it.
report("the prompt names no role", "You are the manager" in p, False)
seen_roles = []
real = skills.with_skills
skills.with_skills = lambda prompt, task, role=None, **k: (seen_roles.append(role), (prompt, {}))[1]
sr.ASK_ROLE = "coder"
claude_session(tmp, "x2", [("user", "go"), ("assistant", "I will build the scoper automation once t300 lands, and test it.")], age_h=30)
sr.discover(idle_h=24)
got = []
sr.extract(sr.PENDING / "claude-x2.json",
           asker=lambda p, purpose: '[{"kind": "deferral", "quote": "the scoper automation", "covered_by": ""}]',
           note_task=lambda t, x: None, file_task=lambda t, b: "t0")
skills.with_skills, sr.ASK_ROLE = real, None
report("skills are chosen for the role running the job", seen_roles, ["coder"])
fs = [json.loads(f.read_text()) for f in sr.pending_findings() if "x2" in json.loads(f.read_text())["request"]]
report("a fragment stays the finding; its whole sentence is kept as context",
       [(f["quote"], f.get("context")) for f in fs],
       [("the scoper automation", "I will build the scoper automation once t300 lands, and test it.")])
# TRIAL v3: fragments of ONE sentence that names a finished task must not be hidden by it.
sr._task_finished = lambda t: t == "t320"
claude_session(tmp, "x3", [("user", "status?"), ("assistant",
    "But t320's parked chunks, the scoper automation, and the memory contention are all still open.")], age_h=30)
sr.discover(idle_h=24)
before = {f.name for f in sr.pending_findings()}
sr.extract(sr.PENDING / "claude-x3.json",
           asker=lambda p, purpose: '[{"kind": "unresolved", "quote": "the scoper automation", "covered_by": ""},'
                                    ' {"kind": "unresolved", "quote": "the memory contention", "covered_by": ""}]',
           note_task=lambda t, x: None, file_task=lambda t, b: "t0")
new = sorted(json.loads(f.read_text())["quote"] for f in sr.pending_findings() if f.name not in before)
report("two fragments of a sentence naming finished t320 stay two findings, not covered by it",
       new, ["the memory contention", "the scoper automation"])
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
