#!/usr/bin/env python3
"""t460 oracle: the brief-anchor symbol check must NORMALIZE the two annotation forms the
manager uses in SYMBOLS -- a trailing "@LINE" (e.g. plan@40) and a new-symbol marker with
extra annotation (e.g. MAX_SWITCHES (new, anchor plan@40)). Today's check (scope_lint.static)
extracts the name as s.strip().split(".")[-1].split("(")[0], which KEEPS the "@LINE" (so a real
symbol is reported 'not defined') and exempts only the exact substring "(new)" (so "(new, ... )"
is not exempted and a genuinely-new symbol is flagged). Both are false positives on t324's nine
symbols. A genuinely-missing clean symbol must STILL be flagged (t264 '_is_model_source'), and a
present clean symbol must STILL pass (the existing test_scope_lint 'g'/'nope' case).

Fail-first against today's scripts/scope_lint.py: static() raises brief-anchor on 'plan@40'
(a real function) and on 'MAX_SWITCHES (new, anchor plan@40)' (a new constant).
House style: report(name, got, want); run directly with python3 (done_when: exits 0).
Temp dirs and fakes only; no live state, no network, no model calls, no subprocess.
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
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def anchor_classes(symbols, repo):
    chunk = {
        "kind": "implementation", "file": "lib/mod.py",
        "target": "scripts/tests/test_mod.py",
        "single_change": "add parked-flag handling near plan",
        "what_is_wrong": "parked chunks are not flagged",
        "done_when": "python3 scripts/tests/test_mod.py exits 0",
        "related_entries": ["t324", "t460"], "symbols": symbols,
    }
    return sorted({c for c, *_ in sl.static([chunk], repo)})


def main():
    tmp = Path(tempfile.mkdtemp(prefix="lint-anchor-"))
    try:
        repo = tmp / "repo"
        (repo / "lib").mkdir(parents=True)
        (repo / "lib" / "mod.py").write_text(
            "def plan(models, chunks):\n    return chunks\n\n"
            "def task_of(brief_name):\n    return 't324'\n\n"
            "MAX_SWITCHES = 1\n")
        # 1. a REAL symbol with a trailing @LINE must NOT be brief-anchor (C1 fix)
        report("a REAL symbol with a trailing @LINE is not brief-anchor",
               "brief-anchor" in anchor_classes(["plan@40"], repo), False)
        # 2. a NEW symbol marked '(new, anchor ...)' must NOT be brief-anchor (C2 fix)
        report("a NEW symbol marked '(new, anchor ...)' is not brief-anchor",
               "brief-anchor" in anchor_classes(["MAX_SWITCHES (new, anchor plan@40)"], repo), False)
        # 3. the real check is preserved: a genuinely-missing clean symbol IS brief-anchor
        report("a genuinely-missing clean symbol is still brief-anchor (t264 '_is_model_source')",
               "brief-anchor" in anchor_classes(["_is_model_source"], repo), True)
        # 4. a present clean symbol still passes (existing test_scope_lint 'g')
        report("a present clean symbol still passes",
               "brief-anchor" in anchor_classes(["plan"], repo), False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
