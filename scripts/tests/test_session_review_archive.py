#!/usr/bin/env python3
"""t623: the deferral detector must check the done/ archive, not only the open queue.

A finding whose work was already tracked in a done/ task must be recognised as covered by
that task (covered count + a note on it, no new finding file), not re-filed as new. Runs
against a throwaway tree only; the real store is never touched. Red on main because
session_review._keep has no done/ archive lookup and no _archive_cover yet."""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(ROOT / "scripts")]
sys.path.insert(0, str(ROOT / "projects" / "app-repo"))
import session_review as sr  # noqa: E402
import taskstore  # noqa: E402

PASSED = 0
FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += 1 if ok else 0
    FAILED += 0 if ok else 1
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="sr-archive-test-"))
    # Point the taskstore AND session_review at a throwaway tree.
    tasks = tmp / "tasks"
    taskstore.TASKS = tasks
    taskstore.DONE = tasks / "done"
    taskstore.QUEUE = tasks / "QUEUE.md"
    taskstore.LOCK = tasks / ".lock"
    taskstore.TASKS.mkdir(parents=True, exist_ok=True)
    taskstore.DONE.mkdir(parents=True, exist_ok=True)
    taskstore.QUEUE.write_text("# queue\n\n", encoding="utf-8")

    # AN IDENTIFIER, not an English word (2026-10-02): the Frontline trial showed a plain rare word
    # ("documenting") making a false cover, so only file/symbol-shaped tokens count as references.
    marker = "deferral_coverage_probe.py"
    (taskstore.DONE / "t168--create-a-root-cause-analysis-process.md").write_text(
        "---\n"
        "id: t168\n"
        "title: Create the RCA process for the harness\n"
        "area: workspace\n"
        "kind: build\n"
        "status: done\n"
        "assigned: claude\n"
        "source: session\n"
        "created: 2026-01-01T000000Z\n"
        "completed: 2026-01-02T000000Z\n"
        "---\n\n"
        f"The plan for the {marker} work is captured here.\n\n## Notes\n",
        encoding="utf-8")

    sr.FINDINGS = tmp / "findings"
    sr.LEDGER = tmp / "ledger.jsonl"
    sr.FINDINGS.mkdir(parents=True, exist_ok=True)

    quote = f"we must ship the {marker} before release"
    found = [{"kind": "deferral", "quote": quote, "where": "assistant",
              "why_missed": "no open task named it", "covered_by": ""}]
    req = {"harness": "claude", "id": "test-sid"}
    req_file = tmp / "pending" / "claude-test-sid.json"
    notes = []
    new, cov = sr._keep(req_file, req, found,
                        note_task=lambda t, x: notes.append((t, x)))

    print("t623: deferral coverage against the done/ archive")
    report("a finding whose work lives in a done/ task is covered by THAT task (not new)",
           (new, cov), (0, 1))
    report("  and the note lands on the done task t168", (len(notes), notes[0][0] if notes else None),
           (1, "t168"))
    report("  and it is NOT re-filed as a new finding file",
           len(list(sr.FINDINGS.glob("*.json"))), 0)
    report("  the archive lookup itself resolves the id for a matching quote",
           sr._archive_cover(quote), "t168")
    report("  and resolves nothing for a quote no done task shares a key word with",
           sr._archive_cover("unrelated zebra completely"), "")
    report("  and a rare ENGLISH word is not a reference (the trial's false cover)",
           sr._archive_cover("documenting the plan for the deferral coverage"), "")

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
