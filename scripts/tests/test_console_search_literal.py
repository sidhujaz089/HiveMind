#!/usr/bin/env python3
"""The console's search_repo finds code by its literal text, and a keyword list by all its words.

2026-09-23: a Q4 run spent its whole 95-minute budget on search_repo calls that returned "no file
contains": `s[:5000]` was read as a regex character class, and "yield flag reviews preemption" as
one exact phrase. The line it wanted was at scripts/scope_queue.py:92. Runs on a throwaway git repo.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "local-inference")]
import console  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def main():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        subprocess.run(["git", "init", "-q", str(repo)], check=True, timeout=30)
        (repo / "a.py").write_text("out.append((real_id, title.strip(), s[:5000]))\n")
        (repo / "b.py").write_text("# the yield flag lets reviews preempt a production job\n")
        (repo / "c.py").write_text("nothing relevant here\n")
        console.REPO = repo
        S = console.tool_search
        report("code with brackets is found by its literal text",
               S({"query": "out.append((real_id, title.strip(), s[:5000]))"}), "a.py")
        report("a slice alone is found", S({"query": "s[:5000]"}), "a.py")
        got = S({"query": "yield flag reviews preempt"})
        report("a keyword list finds the file with all the words", got.splitlines()[-1], "b.py")
        report("  and says it was an all-words match", "ALL of the words" in got, True)
        report("nothing is still nothing", S({"query": "zebra quokka"}).startswith("no file contains"), True)
        report("an empty query is refused", S({"query": "  "}).startswith("REFUSED"), True)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
