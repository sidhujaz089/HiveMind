#!/usr/bin/env python3
"""slot_paths must hand the model to the `slots` seam. (t1576)

THE CRASH. Dispatch p647-t1178-modellease died five times, TESTS-FAILED, with

    TypeError: <lambda>() takes 1 positional argument but 2 were given
      modellease.py, in acquire:     paths = slot_paths(path, model=model)
      modellease.py, in slot_paths:  ... range(2, slots(p, model) + 1)

`slot_paths(path)` has no room for the model, so a caller that HAS one (acquire L121,
check L446) cannot say which model's server is being counted. The threading is t1178's
work: whether a server decodes two requests at once is decided by the model's
architecture -- serial_archs() and model_arch() -- not by the lease filename, which
_lease_model can resolve for only two models. So the model has to travel with the path.

RED ON MAIN for exactly that reason: scripts/modellease.py:310 is `def slot_paths(path):`,
so slot_paths(path, model="q4") raises TypeError.

NO LIVE STATE. The real slots() reads the 30-day journal (subprocess), the systemd unit
(subprocess) and Ollama's /api/show (network), so this suite replaces the seam with a
recorder -- the same seam scripts/tests/test_modellease.py:201 replaces with
`lambda path: 2`. Leases are written into a temp dir and removed.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import modellease  # noqa: E402

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


def _two_slot_stub(calls):
    """The seam as modellease.slots documents it: slots(path, model=None)."""
    def fake_slots(path, model=None):
        calls.append((str(path), model))
        return 2
    return fake_slots


def test_slot_paths_accepts_a_model_and_passes_it_to_the_seam():
    calls = []
    real = modellease.slots
    modellease.slots = _two_slot_stub(calls)
    try:
        base = Path(tempfile.mkdtemp()) / "q4.json"
        paths = modellease.slot_paths(base, model="q4")
        check("the seam is called once for the lease", len(calls), 1)
        check("the seam is asked about the lease's own path", calls[0][0], str(base))
        check("the seam is given the MODEL, not None", calls[0][1], "q4")
        check("a 2-slot server yields 2 slot files", len(paths), 2)
        check("slot 2 is <name>.slot2.json beside the main lease",
              paths[1].name, "q4.slot2.json")
    finally:
        modellease.slots = real


def test_acquire_hands_the_model_it_was_given_to_the_seam():
    """The exact line in the traceback (acquire L130). The value must be USED: the seam
    has to receive the model acquire was handed, not a filename's guess."""
    calls = []
    real = modellease.slots
    modellease.slots = _two_slot_stub(calls)
    base = Path(tempfile.mkdtemp()) / "q4.json"
    lease = None
    try:
        lease = modellease.acquire(base, model="q4", purpose="t1576 oracle", ttl_seconds=600)
        check("acquire reached the seam", len(calls) >= 1, True)
        check("acquire passed ITS model to the seam", calls[0], (str(base), "q4"))
        check("the lease records the model it guards", lease.get("model"), "q4")
    finally:
        if lease is not None:
            modellease.release(base, lease)
        modellease.slots = real
    check("acquire leaves no lease file behind", base.exists(), False)


def test_slot_paths_default_still_works_with_a_one_argument_seam():
    """The unchanged half: slot_paths(path) against a seam that accepts ONLY a path --
    the signature scripts/tests/test_modellease.py:201 uses today. A build that makes
    the model mandatory, or that calls the seam with two arguments unconditionally, fails
    here: that is the coupling that made t1576 un-buildable."""
    seen = []

    def one_arg_seam(path):
        seen.append(str(path))
        return 1

    real = modellease.slots
    modellease.slots = one_arg_seam
    try:
        base = Path(tempfile.mkdtemp()) / "q4.json"
        paths = modellease.slot_paths(base)
        check("slot_paths(path) is still callable", seen, [str(base)])
        check("a 1-slot lease yields 1 path", len(paths), 1)
    finally:
        modellease.slots = real


if __name__ == "__main__":
    for _fn in (test_slot_paths_accepts_a_model_and_passes_it_to_the_seam,
                test_acquire_hands_the_model_it_was_given_to_the_seam,
                test_slot_paths_default_still_works_with_a_one_argument_seam):
        try:
            _fn()
        except Exception as e:                                      # noqa: BLE001 -- recorded
            FAILED.append(f"{_fn.__name__}: raised {type(e).__name__}: {e}")
            print(f"  FAIL {_fn.__name__}: raised {type(e).__name__}: {e}")
    print(f"\n{PASSED} passed, {len(FAILED)} failed")
    sys.exit(1 if FAILED else 0)
