#!/usr/bin/env python3
"""The dossier (plan 2a): every artifact the pipeline has for a task, found where it lives, never copied.

Jas, 2026-09-29: "Every work item carries a dossier ... that accumulates named artifacts as it flows
... Nothing is lost between steps." A fixture repository with one task through scope, brief,
preflight, a build attempt and supervision; then a model task that carries two named artifacts to
the next one. The real workspace is never read or written.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import dossier  # noqa: E402
import task_worker  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n        got  {got!r}\n        want {want!r}"))


def main():
    with tempfile.TemporaryDirectory() as tmp:
        r = Path(tmp)
        w = r / "workspace"
        for d in ("tasks", "briefs", "run-logs/manager-queue/out", "run-logs/manager-queue/in", "task-sessions/t900",
                  "skill-drafts", "task-queues/review_it/done"):
            (w / d).mkdir(parents=True)
        (r / "scripts" / "tests").mkdir(parents=True)
        (w / "tasks" / "t900--fix-the-thing.md").write_text(
            "---\nid: t900\ntitle: Fix the thing\n---\n\nIt is broken.\n\n## Notes\n\n"
            "**2026-09-30 · jas** — (Jas - Human) please\n\n**2026-09-30 · manager** — (Manager - q4) scoped\n")
        (w / "run-logs/manager-queue/out/t900.md").write_text("x" * 50 + "\n(c) SCOPE\n" + "y" * 200)
        (w / "run-logs/manager-queue/in/t900.txt").write_text("the prompt")
        (w / "briefs/brief-p9-t900-fix.txt").write_text("TASK: t900\n\nONE change")
        (w / "briefs/brief-p8-other.txt").write_text("TASK: t901\n")
        (r / "scripts/autonomy_loop.sh").write_text(
            "p9-t900-fix|brief-p9-t900-fix.txt|scripts/thing.py|scripts/tests/test_thing.py\n"
            "p8-other|brief-p8-other.txt|scripts/other.py|scripts/tests/test_other.py\n")
        (r / "scripts/tests/test_thing.py").write_text("assert True\n")
        brief = str(w / "briefs/brief-p9-t900-fix.txt")
        rows = [{"at": "2026-09-30T01:00:00Z", "event": "started", "kind": "production", "chunk": "p9-t900-fix", "model": "g"},
                {"at": "2026-09-30T01:30:00Z", "event": "finished", "kind": "production", "chunk": "p9-t900-fix",
                 "model": "g", "exit": 1},
                {"at": "2026-09-30T01:31:00Z", "event": "finished", "kind": "production", "chunk": "p8-other", "exit": 0}]
        (w / "dispatch-loop.jsonl").write_text("\n".join(json.dumps(x) for x in rows) + "\nnot json\n")
        (w / "preflight-objections.jsonl").write_text(json.dumps({"at": "2026-09-30T00:50:00Z", "brief": brief,
                                                                  "verdict": "SATISFIABLE"}) + "\n")
        (w / "supervision.jsonl").write_text(json.dumps({"at": "2026-09-30T01:40:00Z", "chunk": "p9-t900-fix",
                                                         "decision": "SWITCH"}) + "\n")
        (w / "task-sessions/t900/20260930T000000Z-q4.jsonl").write_text("{}\n")
        dossier.REPO, dossier.W, dossier.ITEMS = r, w, w / "items"

        d = dossier.gather("t900")
        names = [a["name"] for a in d["artifacts"]]
        report("every stage the task went through is in its dossier",
               [n for n in ["task text", "notes", "scope", "scope prompt", "brief", "oracle", "attempt record",
                            "preflight review", "supervision decision", "manager session"] if n not in names], [])
        att = next(a for a in d["artifacts"] if a["name"] == "attempt record")
        report("  the attempt record counts only THIS task's chunks, and names the newest",
               ("2 row(s)" in att["detail"], "exit 1" in att["detail"]), (True, True))
        report("  another task's brief is not in it", any("p8-other" in (a["path"] or "") for a in d["artifacts"]), False)
        report("  the scope says which step the Manager took", "(c) SCOPE" in next(
            a for a in d["artifacts"] if a["name"] == "scope")["detail"], True)
        report("  notes: counted, newest named", next(a for a in d["artifacts"] if a["name"] == "notes")["detail"],
               "2 note(s), the newest by manager")
        report("an unreadable ledger line is SAID, not dropped", any("unreadable line" in u for u in d["unreadable"]), True)
        report("pointers, not copies: every path is an existing file in the repository",
               all((r / a["path"]).is_file() for a in d["artifacts"] if a["path"]), True)
        text = dossier.content(d, {"supervision decision", "brief", "preflight review"})
        report("content() gives a named file artifact whole and a ledger artifact's rows",
               ("ONE change" in text, '"SWITCH"' in text), (True, True))
        report("  a ledger keyed by BRIEF (preflight) gives its rows too, not nothing",
               '"SATISFIABLE"' in text, True)
        report("  and a cut is said, with where the rest is",
               "more characters not shown" in dossier.content(d, {"scope"}, limit=20), True)
        p = dossier.write(d)
        report("--write saves it under workspace/items/<task>/", p == w / "items" / "t900" / "index.json", True)
        report("an unknown task has an empty dossier, not an error", dossier.gather("t999")["artifacts"], [])

        # ------------------------------------------------ CARRY on a model task's arrow (plan 2a)
        flow = {"tasks": [
            {"id": "draft_it", "name": "Draft", "model_task": True, "outcomes": ["done"], "ends": [], "prompt": "Draft."},
            {"id": "review_it", "name": "Review", "model_task": True, "outcomes": ["ok"], "ends": ["ok"], "prompt": "Review."}],
            "links": [{"from": "draft_it", "outcome": "done", "to": "review_it", "mover": "task_worker.py",
                       "carry": ["brief", "supervision decision"]}]}
        q = w / "task-queues"
        item_p = task_worker.send("draft_it", "draft a fix", root=q, f=flow, ref="t900")
        item = json.loads(item_p.read_text())
        prompt = task_worker.prompt_for(flow["tasks"][0], item)
        report("an item about a task carries its dossier index into the prompt",
               ("THE TASK'S DOSSIER" in prompt, "attempt record" in prompt), (True, True))
        out = task_worker.deliver(flow["tasks"][0], item, "done", "the draft", flow, root=q,
                                  filer=lambda *a, **k: "tX")
        nxt = json.loads(next((q / "review_it").glob("*.json")).read_text())
        report("delivered on: the next item keeps the ref and the arrow's Carry", (out, nxt.get("ref"), nxt.get("carry")),
               ("sent to review_it", "t900", ["brief", "supervision decision"]))
        p2 = task_worker.prompt_for(flow["tasks"][1], nxt)
        report("  and its prompt carries those artifacts' CONTENT", ("ONE change" in p2, '"SWITCH"' in p2), (True, True))
        report("  the previous task's answer is still the item", "the draft" in p2, True)
        plain = task_worker.prompt_for(flow["tasks"][0], {"id": "1", "text": "no ref"})
        report("an item about no task has no dossier section", "DOSSIER" in plain, False)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
