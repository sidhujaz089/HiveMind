#!/usr/bin/env python3
"""The gate's cap is JAS'S SETTING, and this asserts the VALUE it resolves to -- not the hook's spelling (2026-10-31).

Claude sent the first version of this work back with two findings, both right:

  * the hook called `gate_settings.py get`, WHICH DID NOT EXIST. argparse exited 2, `|| echo 1800` hid it, and the hook
    never read Jas's setting at all -- setting it to 3600 still produced 1800. A probe proved it.
  * this suite asserted the hook's TEXT ('echo 1800' in hook). That is the L-028 shape: green while the thing is broken.

So the cap lives in scripts/gate_cap.sh, which this suite EXECUTES, and the assertion is on the number that comes out.
The hook's own text is checked only for what cannot be executed here: that it sources the resolver and uses $GATE_CAP
rather than a literal, and that a timeout is named as a timeout.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
fails = []


def check(name, ok, detail=""):
    print(f"  {'ok ' if ok else 'FAIL'}  {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        fails.append(name)


def cap_with(settings):
    """The cap the hook would use, by running the resolver the hook runs."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "gate-settings.json"
        if settings is not None:
            path.write_text(settings, encoding="utf-8")
        # RUN it, the way the hook sources it AND the way a shell executes it: /bin/sh, with the scripts directory given
        # explicitly. The first version of this test used bash with BASH_SOURCE, which is a BAD SUBSTITUTION in the
        # /bin/sh the hook actually uses -- the resolver fell back and the cap was still always 1800.
        out = subprocess.run(["sh", str(HERE / "scripts" / "gate_cap.sh"), str(HERE / "scripts")],
                             capture_output=True, text=True, timeout=60,
                             env={"PATH": "/usr/bin:/bin", "HIVEMIND_GATE_SETTINGS": str(path)})
        return out.stdout.strip(), out.stderr


val, err = cap_with('{"timeout_s": 3600}')
check("a saved 3600 IS the cap the hook runs with", val == "3600", f"got {val!r}")
check("  and the fallback note is NOT printed when the setting is readable", "unreadable" not in err.lower(), err)

val, _ = cap_with('{"timeout_s": 900}')
check("the cap follows the setting wherever it is set", val == "900", f"got {val!r}")

val, err = cap_with(None)
check("no setting at all falls back to 1800", val == "1800", f"got {val!r}")
check("  and SAYS it could not apply Jas's setting", "no saved setting" in err.lower(), err)

val, err = cap_with('{"timeout_s": "soon"}')
check("a non-numeric setting is refused, not passed to timeout(1)", val == "1800", f"got {val!r}")
check("  and says which value it refused", "soon" in err, err)

hook = (HERE / "scripts" / "hooks" / "pre-commit").read_text(encoding="utf-8")
check("the hook sources the resolver", 'gate_cap.sh' in hook, "the hook does not use it")
check("  and bounds the gate with $GATE_CAP, not a literal", 'timeout "$GATE_CAP"' in hook
      and not re.search(r"timeout \d+ python3 .*related_tests", hook), "a literal cap is still in the hook")
check("  a timeout is named AS a timeout", "rc\" -eq 124" in hook or 'rc -eq 124' in hook, "rc 124 is not distinguished")
check("  and a failed record is said, not swallowed", "NOT recorded" in hook, "the record failure is hidden")

print("test_gate_timeout_message:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
