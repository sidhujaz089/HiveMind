#!/usr/bin/env python3
"""t1101: scripts/task_routing.py answers "which role does task X" ONLY from the role store.

Today load() catches every failure to read the store and routes by a built-in DEFAULT
(scripts/task_routing.py:97-110) that is already wrong: it gives `building` to a role named
`coder` and `session_review`/`rca` to a `senior` role, and projects/local-inference/models/
ROLES.json has no `coder` role. Jas, 2026-10-13: "I dont want any task to have a fallback. If
the task has no model assigned to it, it doesn't get done, which is fine."

Fakes only: the store is replaced in sys.modules for the duration of one call and restored in a
finally. Nothing on disk is written, read as an oracle input, or deleted; no model, no server.
"""
import contextlib
import io
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import task_routing as tr  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}"
          + ("" if ok else f"  (got {got!r}, want {want!r})"))


def _stub_role_store():
    """Install a role_store whose load() raises. task_routing.load() does `import role_store`
    INSIDE the function, so sys.modules is the seam. Returns the previous entry to restore."""
    stub = types.ModuleType("role_store")

    def load():
        raise OSError("simulated: the store file is not there")

    stub.load = load
    prev = sys.modules.get("role_store")
    sys.modules["role_store"] = stub
    return prev


def _restore(prev):
    if prev is None:
        sys.modules.pop("role_store", None)
    else:
        sys.modules["role_store"] = prev


def test_no_builtin_copy():
    """The module keeps no built-in copy of who does what: DEFAULT is gone."""
    report("task_routing exports no DEFAULT store", hasattr(tr, "DEFAULT"), False)


def test_unreadable_store_routes_nothing():
    prev = _stub_role_store()
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            d = tr.load()
    finally:
        _restore(prev)
    report("an unreadable store returns a store-shaped answer",
           sorted(d) if isinstance(d, dict) else None, ["roles"])
    report("  with NO roles in it", dict(d.get("roles") or {}), {})
    report("  so model_roles is empty", tr.model_roles(d), [])
    report("  and every kind has no owner",
           {k: tr.owner(d, k) for k in sorted(tr.KIND_TASK)},
           {k: None for k in sorted(tr.KIND_TASK)})
    report("  and no role has any task", tr.tasks_of(d, "manager"), [])
    report("  and the manager queue yields for nothing", tr.yield_kinds(d), set())
    report("  and levels adds no kind from a copy of the store",
           sorted(set(tr.levels(d)) - set(tr.COST_OF_DELAY)), [])
    e = err.getvalue()
    report("it says the role store is unreadable", "role store" in e.lower(), True)
    report("  naming the file, ROLES.json, not just the exception type", "ROLES.json" in e, True)
    report("  quoting what it caught (OSError)", "OSError" in e, True)
    report("  and saying no work is routed", "nothing" in e.lower(), True)
    report("  and it does not route by a built-in default", "default" not in e.lower(), True)


def main():
    test_no_builtin_copy()
    test_unreadable_store_routes_nothing()
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
