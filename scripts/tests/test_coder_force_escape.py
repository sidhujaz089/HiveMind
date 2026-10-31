"""t1163: a stale coder.py twin must not permanently block a dispatch.

running_twin (coder.py:1325-1371) refuses whenever it sees another LIVE
coder.py with the same job, excluding only zombies (state "Z", line 1355).
A hung-but-alive twin is therefore indistinguishable from a working one and
blocks the dispatch forever (t1163, pid 1736188); the only recourse is the
refusal's own "kill it first" (2493) - a manual pid hunt with no override.

This step adds the sanctioned override: a pure predicate twin_refusal the
dispatch calls, honouring an explicit --force. twin_refusal uses only
builtins, so the oracle execs just that one function out of coder.py (never
importing the module, so coder.py's import-time behaviour is irrelevant) and
asserts its behaviour on real values.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]   # scripts/tests/ -> repo root
CODER = REPO / "projects" / "local-inference" / "coder.py"


def _load_twin_refusal():
    """Pull ONLY `def twin_refusal` out of coder.py and exec it in isolation."""
    src = CODER.read_text(encoding="utf-8")
    i = src.find("def twin_refusal")
    assert i != -1, (
        "coder.py has no `def twin_refusal` -- the --force escape hatch "
        "(t1163) has not been added yet"
    )
    rest = src[i:]
    nxt = rest.find("\ndef ")                  # next top-level function
    chunk = rest if nxt == -1 else rest[:nxt]
    ns = {"__name__": "coder_slice"}
    exec(compile(chunk, str(CODER), "exec"), ns)
    return ns["twin_refusal"]


def test_refuses_on_live_twin_by_default():
    twin_refusal = _load_twin_refusal()
    refused, msg = twin_refusal(1736188, force=False)
    assert refused is True, "a live twin must block the dispatch by default"
    assert "1736188" in msg, "the refusal must name the blocking pid"
    assert "already running" in msg, "the refusal must say it is already running"


def test_force_overrides_the_block():
    twin_refusal = _load_twin_refusal()
    refused, msg = twin_refusal(1736188, force=True)
    assert refused is False, "--force must let the operator proceed past a live twin"
    assert "1736188" in msg, "it must still name the pid it is proceeding past"


def test_no_twin_needs_no_force():
    refused, _ = _load_twin_refusal()(None, force=False)
    assert refused is False, "with no live twin the dispatch proceeds"


def test_force_with_no_twin_is_fine():
    refused, _ = _load_twin_refusal()(None, force=True)
    assert refused is False, "force with no twin is a no-op"


if __name__ == "__main__":
    import traceback
    fails = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                fails += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    if fails:
        print(f"\n{fails} test(s) failed")
        sys.exit(1)
    print("\nall tests passed")
