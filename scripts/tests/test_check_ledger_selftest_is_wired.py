#!/usr/bin/env python3
"""An embedded --self-test must be in the vacuous-pass registry's universe.

THE MISSING PROPERTY (t1458, from workspace/rca/fb54aed60bbf.md).
docs/CHECKS.md rule 1: a check is not trusted until it has been observed failing.
scripts/check_ledger.py carries self_test() at :218, whose docstring says the fixtures
"exist to be failed" and that the file "is not trusted until it has been observed failing".
Nothing runs it: no scripts/tests/*.py passes --self-test to check_ledger.py.

check_vacuous.sites() (scripts/check_vacuous.py:38) is the machine that answers "which checks
exist". Today it walks the AST for `if <collection>:` verdict nodes only (:50-67) and keys them
`scripts/<name>::<COLLECTION>#<ordinal>` (:66). KEYS (:34-35) has no word matching `fixtures`,
`self_test` or `self-test`, so check_ledger.py:229 `if not fixtures:` -- the guard inside
self_test() that prints "Nothing was checked -- this is not a pass" -- is not a site at all.
A self-test can therefore pass vacuously and be counted clean, which is the schema-level cause
this task names.

THE PROPERTY THIS SUITE STATES (asserted on the code, not on a hand-kept list):
every scripts/*.py whose own text carries `def self_test(` or `--self-test` appears in
check_vacuous.sites() under a key of the form `scripts/<name>::self-test#N`, ordinal starting
at 0. That is what puts an embedded self-test inside the ratchet.

RED ON MAIN, FOR THIS REASON: sites() today emits no key containing `::self-test` for any file,
so every per-script assertion below fails, and `check_ledger.py` -- the census the Family
Project rests on -- is absent from the universe. GREEN after check_vacuous.py sweeps for the
self-test marker and emits those keys.

No live state: this file reads the repository read-only, writes nothing, calls no model, starts
no server, and leaves no process. No pytest: plain functions, run by __main__, exit 1 on any
failure.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_vacuous  # noqa: E402  (the module under test, from the path it lives on)

failures = []


def check(name, cond, detail=""):
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def self_test_scripts():
    """scripts/*.py carrying an embedded self-test, decided by their own text."""
    out = []
    for f in sorted((ROOT / "scripts").glob("*.py")):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "def self_test(" in text or "--self-test" in text:
            out.append(f)
    return out


def test_universe_is_not_empty():
    """An empty universe is not a clean repository -- it is the sweep being broken."""
    scripts = self_test_scripts()
    check("at least one script carries an embedded self-test",
          bool(scripts), f"{[f.name for f in scripts][:8]}")
    return scripts


def test_check_ledger_is_in_the_universe(scripts):
    check("check_ledger.py is in the self-test universe",
          any(f.name == "check_ledger.py" for f in scripts),
          "the census is the check this task is about")


def test_every_self_test_script_has_a_registry_site(scripts, sites):
    for f in scripts:
        prefix = f"scripts/{f.name}::self-test"
        keys = sorted(k for k in sites if k.startswith(prefix))
        check(f"sites() has a {prefix}#N key",
              bool(keys),
              "no such key -- this script's self-test has no ratchet")
        if keys:
            first = f"{prefix}#0"
            check(f"{f.name}: first occurrence is keyed #0",
                  first in sites, f"keys found: {keys}")


def test_existing_verdict_sites_survive(sites):
    """The sweep must ADD, not replace: the 2026-09-01/02 verdict sites stay registered."""
    check("sites() still returns verdict keys (behaviour preserved)",
          any("::self-test" not in k for k in sites),
          f"{len(sites)} key(s) total")


def main():
    print("test_check_ledger_selftest_is_wired")
    scripts = test_universe_is_not_empty()
    test_check_ledger_is_in_the_universe(scripts)
    sites = check_vacuous.sites()
    test_every_self_test_script_has_a_registry_site(scripts, sites)
    test_existing_verdict_sites_survive(sites)
    print("test_check_ledger_selftest_is_wired: "
          + ("FAILED -- " + "; ".join(failures[:3])
             + (f" (+{len(failures) - 3} more)" if len(failures) > 3 else "")
             if failures else "ok"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
