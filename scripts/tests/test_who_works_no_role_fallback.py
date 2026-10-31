#!/usr/bin/env python3
"""t1101: scripts/who_works.py attributes a job ONLY by the role recorded on it.

Two invented roles today: build_role() falls back to "frontline" when no role's list holds
`building`, and role_of() infers a role from the server port and from the job kind, defaulting
to "manager". Both show a job working on a tile its store entry never gave it. Jas, 2026-10-13:
"The roles that do any task should not be hard coded to the who_works function. It should look
at the actual roles that have that task assigned."

Fakes only: task_routing is replaced in sys.modules (who_works imports it inside the functions),
restored in a finally. No store file is written, no model, no server, no process listing.
"""
import inspect
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import who_works as ww  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}"
          + ("" if ok else f"  (got {got!r}, want {want!r})"))


def _stub_task_routing(store):
    """A task_routing that answers from a store of the test's making. Returns the previous
    sys.modules entry to restore. owner() mirrors the real signature (kind, exclude=())."""
    stub = types.ModuleType("task_routing")

    def model_roles(d):
        return [k for k, r in (d.get("roles") or {}).items()
                if (r or {}).get("kind", "model") == "model"]

    def owner(d, kind, exclude=()):
        for k in model_roles(d):
            tasks = {t.get("task") for t in ((d["roles"][k] or {}).get("tasks") or [])}
            if kind in tasks and k not in exclude:
                return k
        return None

    stub.load = lambda: store
    stub.model_roles = model_roles
    stub.owner = owner
    prev = sys.modules.get("task_routing")
    sys.modules["task_routing"] = stub
    return prev


def _restore(prev):
    if prev is None:
        sys.modules.pop("task_routing", None)
    else:
        sys.modules["task_routing"] = prev


NOBODY = {"roles": {}}                    # the store state: no role holds any task
STORE = {"roles": {                       # the store state: the Manager owns every task
    "manager": {"kind": "model", "model": "swift",
                "tasks": [{"task": "supervision"}, {"task": "scoping"},
                          {"task": "console"}, {"task": "building"}]},
}}


def test_no_invented_role():
    """No role recorded + no owner in the store = the job belongs to no tile."""
    prev = _stub_task_routing(NOBODY)
    try:
        report("no role has Building: the build belongs to NO tile", ww.build_role(), None)
        report("  the same through role_of, which is what record() calls",
               ww.role_of("production", 11434, 11435, 11434, None, "swift"), None)
        report("  a port match guesses nothing: a job on the senior station's port",
               ww.role_of("production", 11435, 11435, 11434, None, "swift"), None)
        report("  and the main port is not a role",
               ww.role_of("supervise", 11434, 11435, 11434, None, "swift"), None)
        src = inspect.getsource(ww.build_role)
        report("  the kind is not a role: a build is not the Frontline because it is a build",
               "frontline" in src, False)
    finally:
        _restore(prev)


def test_a_recorded_role_is_still_shown():
    """The store is the source: a job that CARRIES its role keeps its tile."""
    prev = _stub_task_routing(STORE)
    try:
        report("a row recorded as the Manager shows on the Manager tile",
               ww.role_of("supervise", 11434, 11435, 11434, "manager", "swift"), "manager")
        report("  and a build recorded as the coder's stays on the Frontline tile",
               ww.role_of("production", 11434, 11435, 11434, "coder", "gemma"), "frontline")
        report("  Building's tile comes from the store: the Manager owns it in this store",
               ww.build_role(), "manager")
    finally:
        _restore(prev)


def main():
    test_no_invented_role()
    test_a_recorded_role_is_still_shown()
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
