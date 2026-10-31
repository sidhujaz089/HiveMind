#!/usr/bin/env python3
"""scripts/bridge.py turns a manager answer's `chunks` block into dispatchable work (t323, step 4).

WHY. On 2026-09-25, 47 tasks sat in "scoped": Q4 had written each one's chunks -- brief fields
and oracle text -- as prose, and the only route from there to a Gemma build was Claude
hand-writing four things per chunk: the brief file, the oracle, its red declaration in the
failure ceiling, and the work-list row in autonomy_loop.sh. bridge.py writes those four from a
machine-readable block at the end of the answer. It never touches git: Claude reviews what it
wrote and commits it, and that commit is the oracle freeze (D-35).

THE BLOCK (the contract manager_prompt.py hands Q4). The LAST fenced block tagged `chunks` in
the answer, holding a JSON list. Each item is one chunk:
  {"kind": "oracle", "file": "scripts/tests/test_x.py", "oracle_text": "<the whole file>",
   "red_because": "<why it fails today>"}
  {"kind": "implementation", "file": "<the ONE file to change>", "target": "<the oracle suite>",
   "single_change": "...", "what_is_wrong": "...", "done_when": "... <target> exits 0 ...",
   "related_entries": ["..."], "symbols": ["f", "C.m"], "behaviour_oracle": "..."}
symbols and behaviour_oracle are optional; everything else shown is required.

RERUNS ARE NO-OPS. An oracle file that already exists with the block's exact text, a
declaration already present, and a work-list row with the same file and target are "already
applied" and skipped. An existing oracle file with DIFFERENT text is refused -- the bridge never
overwrites a suite, frozen or not.

Model-free. Every write goes to a temp repo passed as --repo; the live repository is checked
unchanged at the end.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


# The real file keeps its rows inside a heredoc in the MIDDLE of the script; a row appended at
# the end of the file would land after the loop, where nothing reads it.
LOOP_HEAD = """#!/bin/bash
# fixture work list
work_list () { cat <<'LIST'
p07-keepalive|brief-t287-keepalive.txt|projects/local-inference/settings.py|scripts/tests/test_keepalive_validation.py
p51-preflight-held-lease|brief-p51-preflight-held-lease.txt|scripts/preflight_brief.py|scripts/tests/test_preflight_held_lease.py
LIST
}
while true; do
    sleep 60
done
"""

CEILING = {"allowed_to_fail": ["test_preflight_held_lease.py"],
           "_oracles_awaiting_their_fix": {"test_preflight_held_lease.py": "t387 oracle (p51)"}}

ORACLE_TEXT = ("#!/usr/bin/env python3\n\"\"\"fixture oracle\"\"\"\nimport sys\n"
               "sys.path.insert(0, 'scripts')\nimport widget  # noqa\nsys.exit(0)\n")

ORACLE = {"kind": "oracle", "file": "scripts/tests/test_widget.py", "oracle_text": ORACLE_TEXT,
          "red_because": "scripts/widget.py does not exist yet"}
IMPL = {"kind": "implementation", "file": "scripts/widget.py",
        "target": "scripts/tests/test_widget.py",
        "single_change": "Create scripts/widget.py with a spin() that returns 3.",
        "what_is_wrong": "Nothing spins the widget, so the board shows it stopped.",
        "done_when": "python3 scripts/tests/test_widget.py exits 0",
        "related_entries": ["check-satisfied-by-dead-path"],
        "symbols": ["spin"],
        "behaviour_oracle": "spin() still returns 3 when called twice."}


def answer(chunks, tag="chunks"):
    return ("## t999 -- scope\n\nSome prose. A decoy block that is not the answer:\n\n"
            "```chunks\n[]\n```\n\n## For Claude\nFile these.\n\n"
            f"```{tag}\n{json.dumps(chunks, indent=1)}\n```\n")


def fixture():
    repo = Path(tempfile.mkdtemp(prefix="bridge-test-"))
    (repo / "scripts" / "tests").mkdir(parents=True)
    (repo / "workspace" / "briefs").mkdir(parents=True)
    (repo / "scripts" / "autonomy_loop.sh").write_text(LOOP_HEAD, encoding="utf-8")
    (repo / "scripts" / "tests" / "test_existing.py").write_text("import sys\nsys.exit(1)\n")
    (repo / "workspace" / "test-failures-ceiling.json").write_text(json.dumps(CEILING, indent=2))
    return repo


def run(repo, text, *extra):
    import bridge
    ans = repo / "answer.md"
    ans.write_text(text, encoding="utf-8")
    return bridge.main([str(ans), "--task", "t999", "--repo", str(repo), *extra])


def snapshot(repo):
    return {str(p.relative_to(repo)): p.read_bytes() for p in sorted(repo.rglob("*"))
            if p.is_file() and p.name != "answer.md"}


def main():
    live_loop = (ROOT / "scripts" / "autonomy_loop.sh").read_bytes()
    live_ceiling = (ROOT / "workspace" / "test-failures-ceiling.json").read_bytes()

    try:
        import bridge  # noqa: F401
    except ImportError as e:
        report("scripts/bridge.py is importable", str(e), "importable")
        print(f"\n{PASSED} passed, {FAILED} failed")
        return 1

    # --- THE FENCE'S INFO STRING (2026-09-30): t264's scope fenced its list as ```json chunks and
    # was held for Jas as "no ```chunks block" with a valid list inside it.
    for info in ("chunks", "json chunks", "chunks json", "CHUNKS"):
        got, errs = bridge.parse(f"prose\n\n```{info}\n[{{\"kind\": \"oracle\"}}]\n```\n")
        report(f"a ```{info} fence is read as the chunks block", (got, errs), ([{"kind": "oracle"}], []))
    got, errs = bridge.parse("```json\n[{\"kind\": \"oracle\"}, {\"kind\": \"implementation\"}]\n```\n")
    report("a plain ```json that can only be the plan (every item a chunk) is read (t259)",
           len(got), 2)
    got, errs = bridge.parse("```json\n[{\"kind\": \"solution\", \"title\": \"x\"}]\n```\n")
    report("  but an RCA's proposal list is not", (got, errs), ([], [bridge.NO_BLOCK]))
    got, errs = bridge.parse("```json\n\"test_x.py\": \"a ceiling note\"\n```\n")
    report("  nor a ceiling declaration", (got, errs), ([], [bridge.NO_BLOCK]))

    # --- the happy path: one oracle chunk + its implementation -------------------------------
    repo = fixture()
    rc = run(repo, answer([ORACLE, IMPL]), "--apply")
    report("a valid block applies (exit 0)", rc, 0)

    oracle = repo / "scripts" / "tests" / "test_widget.py"
    report("the oracle file is written with the block's text, byte for byte",
           oracle.read_text(encoding="utf-8") if oracle.is_file() else None, ORACLE_TEXT)

    ceil = json.loads((repo / "workspace" / "test-failures-ceiling.json").read_text())
    report("the new oracle is declared red in allowed_to_fail",
           "test_widget.py" in ceil.get("allowed_to_fail", []), True)
    report("the old declarations are kept",
           "test_preflight_held_lease.py" in ceil.get("allowed_to_fail", []), True)
    why = ceil.get("_oracles_awaiting_their_fix", {}).get("test_widget.py", "")
    report("its reason names the task", "t999" in why, True)

    rows = [l for l in (repo / "scripts" / "autonomy_loop.sh").read_text().splitlines()
            if l.startswith("p") and l.count("|") >= 3]
    report("exactly one work-list row is added (the oracle chunk is not a build)", len(rows), 3)
    new = rows[-1].split("|") if rows else ["", "", "", ""]
    report("the row takes the next free chunk number (p52 after p51)",
           new[0].startswith("p52-"), True)
    report("the row names the implementation file", new[2], "scripts/widget.py")
    report("the row names the oracle as its target", new[3], "scripts/tests/test_widget.py")
    report("the row's brief file is named after the chunk", new[1], f"brief-{new[0]}.txt")
    lines = (repo / "scripts" / "autonomy_loop.sh").read_text().splitlines()
    at = next((i for i, l in enumerate(lines) if l.startswith("p52-")), None)
    report("the row goes directly after the last existing row, inside the list",
           (lines[at - 1].startswith("p51-"), lines[at + 1]) if at else None, (True, "LIST"))
    report("the rest of the script is untouched", lines[at + 1:] if at else None,
           ["LIST", "}", "while true; do", "    sleep 60", "done"])

    brief_f = repo / "workspace" / "briefs" / new[1]
    brief = brief_f.read_text(encoding="utf-8") if brief_f.is_file() else ""
    report("the brief exists", brief_f.is_file(), True)
    report("the brief's FIRST line is its TASK line (every chunk has a task, 49c841a)",
           brief.splitlines()[0] if brief else "", "TASK: t999")
    report("the brief carries a SYMBOLS: line", "SYMBOLS: spin" in brief, True)
    for field in ("single_change", "what_is_wrong", "done_when", "behaviour_oracle"):
        report(f"the brief carries {field}", IMPL[field] in brief, True)
    report("the brief names the one file", "scripts/widget.py" in brief, True)
    report("the brief names its frozen oracle and says not to edit it",
           "scripts/tests/test_widget.py" in brief and "do not edit" in brief.lower(), True)
    report("the brief carries related_entries", "check-satisfied-by-dead-path" in brief, True)

    # --- running it again enrolls nothing twice ------------------------------------------------
    before = snapshot(repo)
    rc = run(repo, answer([ORACLE, IMPL]), "--apply")
    report("a second run of the same block exits 0", rc, 0)
    report("and changes nothing (no duplicate row, brief or declaration)", snapshot(repo), before)

    # --- without --apply nothing is written ----------------------------------------------------
    repo = fixture()
    before = snapshot(repo)
    rc = run(repo, answer([ORACLE, IMPL]))
    report("a dry run of a valid block exits 0", rc, 0)
    report("and writes nothing", snapshot(repo), before)

    # --- refusals: exit 2 and NOTHING written, not a partial bridge ------------------------------
    def refused(name, text):
        repo = fixture()
        before = snapshot(repo)
        rc = run(repo, text, "--apply")
        report(f"refuses {name} (exit 2)", rc, 2)
        report(f"  ...and writes nothing", snapshot(repo), before)

    refused("an answer with no chunks block", "## t999\n\nprose only\n")
    refused("a chunks block that is not JSON", "```chunks\n[{not json}]\n```\n")
    refused("an empty chunks list", answer([]))
    refused("an implementation missing done_when",
            answer([ORACLE, {k: v for k, v in IMPL.items() if k != "done_when"}]))
    refused("an implementation missing its target",
            answer([ORACLE, {k: v for k, v in IMPL.items() if k != "target"}]))
    refused("an implementation naming two files",
            answer([ORACLE, {**IMPL, "file": "scripts/widget.py scripts/other.py"}]))
    refused("a target that neither exists nor is written by an oracle chunk in the block",
            answer([IMPL]))
    refused("a done_when that does not name the target suite",
            answer([ORACLE, {**IMPL, "done_when": "it works"}]))
    refused("an oracle chunk outside scripts/tests/test_*.py",
            answer([{**ORACLE, "file": "scripts/widget_check.py"}, IMPL]))
    refused("an oracle chunk with no oracle_text", answer([{k: v for k, v in ORACLE.items()
                                                            if k != "oracle_text"}, IMPL]))
    refused("an oracle chunk that would overwrite an existing suite",
            answer([{**ORACLE, "file": "scripts/tests/test_existing.py"},
                    {**IMPL, "target": "scripts/tests/test_existing.py"}]))
    refused("a path that escapes the repository",
            answer([ORACLE, {**IMPL, "file": "../outside.py"}]))
    refused("an unknown kind", answer([{**ORACLE, "kind": "refactor"}, IMPL]))

    # --- an implementation against an EXISTING suite needs no oracle chunk --------------------
    repo = fixture()
    rc = run(repo, answer([{**IMPL, "target": "scripts/tests/test_existing.py",
                            "done_when": "python3 scripts/tests/test_existing.py exits 0"}]),
             "--apply")
    report("an implementation pinned by an existing suite applies", rc, 0)
    ceil = json.loads((repo / "workspace" / "test-failures-ceiling.json").read_text())
    report("  ...and declares nothing new", ceil["allowed_to_fail"], CEILING["allowed_to_fail"])

    # --- the live repository was never touched -------------------------------------------------
    report("the live autonomy_loop.sh is unchanged",
           (ROOT / "scripts" / "autonomy_loop.sh").read_bytes() == live_loop, True)
    report("the live failure ceiling is unchanged",
           (ROOT / "workspace" / "test-failures-ceiling.json").read_bytes() == live_ceiling, True)

    # --- APPEND (t672): a non-Python file edited only at its end gets an APPEND line, not SYMBOLS ------
    repo = fixture()
    (repo / "docs").mkdir()
    (repo / "docs" / "BIG.md").write_text("# lessons\n")
    app = dict(IMPL, file="docs/BIG.md", append=True, symbols=[])
    run(repo, answer([ORACLE, app]), "--apply")
    briefs = [p.read_text(encoding="utf-8") for p in (repo / "workspace" / "briefs").glob("*.txt")]
    report("an append chunk's brief says APPEND and names no SYMBOLS (t672)",
           [("APPEND:" in b, "SYMBOLS:" in b) for b in briefs], [(True, False)])
    repo = fixture()
    run(repo, answer([ORACLE, dict(IMPL, append=True)]), "--apply")
    briefs = [p.read_text(encoding="utf-8") for p in (repo / "workspace" / "briefs").glob("*.txt")]
    report("  but a .py chunk never gets an APPEND line (SYMBOLS slice those)",
           [("APPEND:" in b) for b in briefs], [False])
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    sys.exit(main())
