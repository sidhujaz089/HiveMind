#!/usr/bin/env python3
"""A RE-SCOPE of an enrolled chunk must land in its brief file (t1255).

WHY. `bridge.py` de-duplicated implementation chunks on the (file, target) pair: if a row for
that pair was already in `scripts/autonomy_loop.sh`, the chunk was dropped before the brief was
rendered. A first dispatch is a no-op in that case; a RE-SCOPE is silently thrown away -- exit 0,
"success", the brief on disk left at its old text. The gate that judges briefs
(`scripts/preflight_brief.py`) reads that file, so it objected to bytes no scoper can change.
On t1255 that loop ran four times in three days (workspace/preflight-objections.jsonl: 2026-10-16,
-17, -19, -19), each cycle burning a model lease, and the brief never moved: `sha256sum
workspace/briefs/brief-p672-t1255-session-review.txt` is 71abaf3a... on every date.

The oracle branch of the same function has always compared CONTENT ("identical text = an earlier
run of this block"). This suite pins the implementation branch to the same rule, and keeps the
guarantee the old rule was bought for: an identical rerun writes nothing, and mints no second row.

Model-free: every write goes to a temp repo passed as --repo; the live repo is checked unchanged.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


LOOP_HEAD = """#!/bin/bash
# fixture work list
work_list () { cat <<'LIST'
p51-widget|brief-p51-widget.txt|scripts/widget.py|scripts/tests/test_widget.py
LIST
}
while true; do
    sleep 60
done
"""

OLD_CHANGE = ("Create scripts/widget.py with a spin() that returns 3. "
              "OLD SCOPE, the one the preflight objected to.")
NEW_CHANGE = ("Create scripts/widget.py with a spin() that returns 3, and keep the module's spin "
              "signature. REVISED SCOPE, what the scoper wrote after the objection.")


def impl_chunk(single_change):
    return {"kind": "implementation", "file": "scripts/widget.py",
            "target": "scripts/tests/test_widget.py",
            "single_change": single_change,
            "what_is_wrong": "Nothing spins the widget, so the board shows it stopped.",
            "done_when": "python3 scripts/tests/test_widget.py exits 0",
            "related_entries": ["check-satisfied-by-dead-path"],
            "symbols": ["spin"],
            "behaviour_oracle": "spin() still returns 3 when called twice."}


def answer(chunks):
    return ("## t999 -- scope\n\nprose\n\n"
            f"```chunks\n{json.dumps(chunks, indent=1)}\n```\n")


def fixture(old_brief=OLD_CHANGE):
    """A repo where (scripts/widget.py, scripts/tests/test_widget.py) is ALREADY ENROLLED, with
    the enrolled brief on disk -- the exact state t1255 was stuck in."""
    repo = Path(tempfile.mkdtemp(prefix="bridge-refresh-"))
    (repo / "scripts" / "tests").mkdir(parents=True)
    (repo / "workspace" / "briefs").mkdir(parents=True)
    (repo / "scripts" / "autonomy_loop.sh").write_text(LOOP_HEAD, encoding="utf-8")
    (repo / "scripts" / "tests" / "test_widget.py").write_text("import sys\nsys.exit(0)\n",
                                                               encoding="utf-8")
    (repo / "workspace" / "test-failures-ceiling.json").write_text(
        json.dumps({"allowed_to_fail": []}, indent=2), encoding="utf-8")
    (repo / "workspace" / "briefs" / "brief-p51-widget.txt").write_text(
        "TASK: t999\nSYMBOLS: spin\n\nONE file: scripts/widget.py. " + old_brief + "\n",
        encoding="utf-8")
    return repo


def run(repo, chunks, *extra):
    import bridge
    ans = repo / "answer.md"
    ans.write_text(answer(chunks), encoding="utf-8")
    return bridge.main([str(ans), "--task", "t999", "--repo", str(repo), *extra])


def snapshot(repo):
    return {str(p.relative_to(repo)): p.read_bytes() for p in sorted(repo.rglob("*"))
            if p.is_file() and p.name != "answer.md"}


def work_rows(repo):
    return [l for l in (repo / "scripts" / "autonomy_loop.sh").read_text().splitlines()
            if l.startswith("p") and l.count("|") >= 3]


def main():
    live_loop = (ROOT / "scripts" / "autonomy_loop.sh").read_bytes()
    live_brief = (ROOT / "workspace" / "briefs" / "brief-p672-t1255-session-review.txt").read_bytes()

    try:
        import bridge  # noqa: F401
    except ImportError as e:
        report("scripts/bridge.py is importable", str(e), "importable")
        print(f"\n{PASSED} passed, {FAILED} failed")
        return 1

    # --- 1. A REVISION of an enrolled chunk reaches its brief (the t1255 defect) --------------
    repo = fixture()
    rc = run(repo, [impl_chunk(NEW_CHANGE)], "--apply")
    brief = (repo / "workspace" / "briefs" / "brief-p51-widget.txt")
    text = brief.read_text(encoding="utf-8") if brief.is_file() else ""
    report("a revised chunk for an enrolled pair exits 0", rc, 0)
    report("and its brief file carries the REVISED text", NEW_CHANGE in text, True)
    report("and no longer carries the text that was objected to", OLD_CHANGE in text, False)

    # --- 2. An identical rerun is still a no-op (what the pair rule was bought for) -----------
    repo = fixture()
    rc_first = run(repo, [impl_chunk(NEW_CHANGE)], "--apply")
    after_first = snapshot(repo)
    rc_second = run(repo, [impl_chunk(NEW_CHANGE)], "--apply")
    report("the same block applied twice exits 0 both times", (rc_first, rc_second), (0, 0))
    report("and the second run changes nothing on disk", snapshot(repo), after_first)

    # --- 3. A revision never mints a second row for the same pair -----------------------------
    repo = fixture()
    run(repo, [impl_chunk(NEW_CHANGE)], "--apply")
    rows = work_rows(repo)
    report("the work list keeps exactly the one row for that pair",
           [(len(rows), rows[0].split("|")[0] if rows else None)], [(1, "p51-widget")])
    report("and the brief keeps the ENROLLED chunk's name (no orphan second brief)",
           sorted(p.name for p in (repo / "workspace" / "briefs").glob("*.txt")),
           ["brief-p51-widget.txt"])

    # --- 4. Control: the guard is not over-tightened. A NEW pair still mints a row + brief ----
    repo = fixture()
    new_pair = dict(impl_chunk("Create scripts/gadget.py with a turn() that returns 7."),
                    file="scripts/gadget.py")
    rc = run(repo, [new_pair], "--apply")
    rows = work_rows(repo)
    report("a chunk whose (file, target) is NOT enrolled is planned and rowed",
           (rc, len(rows), rows[-1].split("|")[2] if rows else None),
           (0, 2, "scripts/gadget.py"))
    report("  ...and gets a NEW brief file, not a rewrite of the enrolled one",
           sorted(p.name for p in (repo / "workspace" / "briefs").glob("*.txt")),
           ["brief-p51-widget.txt", "brief-p52-t999-gadget.py.txt".replace(".py.txt", ".txt")])

    # --- 5. The re-written brief is the FULL render, not a stub -------------------------------
    repo = fixture()
    run(repo, [impl_chunk(NEW_CHANGE)], "--apply")
    text = (repo / "workspace" / "briefs" / "brief-p51-widget.txt").read_text(encoding="utf-8")
    report("the rewritten brief keeps the rendered shape (TASK first, oracle named, gate rules)",
           [text.splitlines()[0] if text else None,
            "scripts/tests/test_widget.py" in text and "do not edit" in text.lower(),
            "COMMIT GATE" in text, "EDIT PROTOCOL" in text, "SYMBOLS: spin" in text,
            "REVISED SCOPE" in text, "DONE WHEN" in text],
           ["TASK: t999", True, True, True, True, True, True])

    # --- the live repository was never touched by any of the above ----------------------------
    report("the live autonomy_loop.sh is unchanged",
           (ROOT / "scripts" / "autonomy_loop.sh").read_bytes() == live_loop, True)
    report("the live t1255 brief on disk is unchanged",
           (ROOT / "workspace" / "briefs" / "brief-p672-t1255-session-review.txt").read_bytes()
           == live_brief, True)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
