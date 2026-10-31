#!/usr/bin/env python3
"""The `slots` seam's SIGNATURE: a stub must accept the model argument. (t1576)

WHY THIS FILE EXISTS. Dispatch p647-t1178-modellease failed five times, TESTS-FAILED, on a
signature and not on behaviour:

    TypeError: <lambda>() takes 1 positional argument but 2 were given
      modellease.py, in slot_paths:  range(2, slots(p, model) + 1)

The <lambda> is the fixture at scripts/tests/test_modellease.py:201,
`_ml.slots = lambda path: 2`. Threading the model through slot_paths -> slots -- t1178's
work, and chunk 1 of t1576 -- calls that stub with two arguments, and the builder may not
edit the suite it is judged by, so every honest attempt dies on the stub's arity. The fix
is the stub. This file is the ratchet that keeps it fixed: it reads the standing suite's
own `slots = lambda ...` stub out of the source text and CALLS it, so a narrowed seam fails
mechanically instead of living as prose in a brief.

RED ON MAIN: `lambda path: 2` raises TypeError when called with a model argument.
GREEN after scripts/tests/test_modellease.py:201 accepts the model.

NO LIVE STATE: ast + eval over the fixture's source, and inspect.signature on the module's
function. No subprocess, no network, no model call, no file written.
"""
import ast
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import modellease  # noqa: E402

SUITE = ROOT / "scripts" / "tests" / "test_modellease.py"

PASSED = 0
FAILED = []


def check(label, got, want):
    global PASSED
    if got == want:
        PASSED += 1
        print("  ok   " + label)
    else:
        FAILED.append(label)
        print("  FAIL " + label + ": got " + repr(got) + " want " + repr(want))


def _stubs():
    """Every `x.slots = lambda ...` in the standing suite, compiled into callables."""
    tree = ast.parse(SUITE.read_text(encoding="utf-8"), filename=str(SUITE))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Lambda):
            named = [t.attr for t in node.targets if isinstance(t, ast.Attribute)]
            named += [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "slots" in named:
                code = compile(ast.Expression(body=node.value), str(SUITE), "eval")
                out.append((node.lineno, eval(code, {}, {})))
    return out


def test_the_suites_stub_is_callable_as_slots_path_model():
    """A seam stub must survive the call the threaded code makes: slots(path, model)."""
    stubs = _stubs()
    if not stubs:
        FAILED.append("test_the_suites_stub_is_callable_as_slots_path_model: no "
                      "`slots = lambda ...` stub found in scripts/tests/test_modellease.py "
                      "-- the seam was renamed; update this file's contract to the new "
                      "seam's signature, do not delete the assertion")
        return
    for lineno, fn in stubs:
        try:
            got = fn("workspace/leases/q4.json", "q4")
        except TypeError as e:
            FAILED.append(f"test_the_suites_stub_is_callable_as_slots_path_model: the stub "
                          f"at scripts/tests/test_modellease.py:{lineno} is not callable as "
                          f"slots(path, model) -- TypeError: {e} (t1576: modellease threads "
                          "the model into slots for t1178; a one-argument stub crashes the "
                          "suite and makes that work un-buildable)")
            continue
        check(f"the stub at line {lineno} returns the slot count with a model", got, 2)
        try:
            one = fn("workspace/leases/q4.json")
        except TypeError as e:
            FAILED.append(f"test_the_suites_stub_is_callable_as_slots_path_model: the stub "
                          f"at line {lineno} is not callable as slots(path) -- TypeError: {e}")
            continue
        check(f"the stub at line {lineno} returns the slot count with no model", one, 2)


def test_modellease_slots_declares_the_model_parameter():
    """modellease.slots must keep a `model` parameter with a default, so BOTH
    slots(path) and slots(path, model) are legal calls. Signature only -- it is never
    called here, because the real body reads the journal, the systemd unit and Ollama."""
    params = inspect.signature(modellease.slots).parameters
    check("modellease.slots has a model parameter", "model" in params, True)
    check("modellease.slots can be called with just a path",
          params["model"].default is not inspect.Parameter.empty if "model" in params else None,
          True)


if __name__ == "__main__":
    for _fn in (test_the_suites_stub_is_callable_as_slots_path_model,
                test_modellease_slots_declares_the_model_parameter):
        try:
            _fn()
        except Exception as e:                                      # noqa: BLE001 -- recorded
            FAILED.append(f"{_fn.__name__}: raised {type(e).__name__}: {e}")
            print(f"  FAIL {_fn.__name__}: raised {type(e).__name__}: {e}")
    print(f"\n{PASSED} passed, {len(FAILED)} failed")
    sys.exit(1 if FAILED else 0)
