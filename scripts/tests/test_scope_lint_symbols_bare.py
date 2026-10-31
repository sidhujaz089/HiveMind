#!/usr/bin/env python3
"""t666 oracle: the scope_lint SYMBOLS check must reject a SYMBOLS entry that is PROSE
(a sentence describing where a change goes), not just a bare name. Today's check
(scope_lint.static, the per-symbol loop) exempts only a `(new` marker and then takes
the first whitespace-delimited word as the name, so a prose entry that STARTS WITH a
real symbol (e.g. "static (the enclosing function)") PASSES -- the name is defined,
so no brief-anchor finding -- even though Gemma receives a sentence it cannot anchor
to. t458's entry was exactly that shape: "the existing finding/emit helper that
_never_fails's branch calls to print the 'oracle-weak' line (name it as its own bare
entry ...)". After the fix, a SYMBOLS entry that is not a bare identifier (optionally
@LINE) and not a `(new` marker is a brief-anchor finding on its own.

Fail-first against today's scripts/scope_lint.py: for a file that defines `static`,
symbols=["static (the enclosing function)"] produces NO finding today, so the primary
assert below fails. The guards stay green before and after (t460's forms: a present
bare name passes, an absent bare name is still brief-anchor, a `(new ...)` marker is
still exempt).

House style: report(name, got, want); run directly with python3 (done_when: exits 0).
Temp dirs and fakes only; no subprocess, no network, no model calls.
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]
import scope_lint as sl  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = (got == want)
    PASSED += ok
    FAILED += (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def classes_for(symbols, repo):
    chunk = {
        "kind": "implementation", "file": "lib/mod.py",
        "target": "scripts/tests/test_mod.py",
        "single_change": "no-op", "what_is_wrong": "no-op",
        "done_when": "python3 scripts/tests/test_mod.py exits 0",
        "related_entries": ["t460", "t666"], "symbols": symbols,
    }
    return sorted({c for c, *_ in sl.static([chunk], repo)})


def main():
    tmp = Path(tempfile.mkdtemp(prefix="lint-symbols-"))
    try:
        repo = tmp / "repo"
        (repo / "lib").mkdir(parents=True)
        (repo / "lib" / "mod.py").write_text(
            "def static(x):\n    return x\n\n"
            "def plan(x):\n    return x\n\n"
            "MAX_SWITCHES = 1\n")
        # PRIMARY (the missing change): a PROSE entry starting with a real symbol
        # must be a brief-anchor finding. Red on main (today it passes).
        report("a PROSE SYMBOLS entry starting with a real symbol is brief-anchor",
               "brief-anchor" in classes_for(["static (the enclosing function)"], repo), True)
        # GUARD (t460): a present bare name still passes.
        report("a present bare name still passes",
               "brief-anchor" in classes_for(["static"], repo), False)
        # GUARD (t460): an absent bare name is still brief-anchor.
        report("an absent bare name is still brief-anchor",
               "brief-anchor" in classes_for(["_is_model_source"], repo), True)
        # GUARD (t460): a `(new ...)` marker is still exempt (not a prose finding).
        report("a `(new ...)` SYMBOLS marker is still exempt",
               "brief-anchor" in classes_for(["MAX_SWITCHES (new, anchor static@40)"], repo), False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
