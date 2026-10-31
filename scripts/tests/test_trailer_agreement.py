#!/usr/bin/env python3
"""The hook and the audit must accept exactly the same Agent trailers.

WHY (env3 probe, finding 17)
----------------------------
`audit_compliance.py` carries a comment saying its trailer shape "must match
scripts/hooks/commit-msg exactly. If you change one, change both -- a hook and an audit that
disagree teach people to ignore the audit." Nothing checked that they did, and they did not:
the hook's model half was `[A-Za-z0-9._:-]+` and the audit's was `[A-Za-z0-9._-]+`.

So every commit made by an ollama-tagged model -- `Agent: ollama/qwen3.8:27b-mtp-q4_K_M`, a
colon in the tag -- was WRITTEN with the hook's blessing and then reported by the audit as a
HIGH 'Agent trailer missing'. Eleven of them. An audit that raises HIGH findings against work
its own hook just approved is worse than no audit: it is a reason to stop reading the audit.

A rule stated in a comment is a rule nothing enforces. This asserts the agreement itself --
both patterns are read from the files that hold them, so neither can drift without this
going red.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / "scripts" / "hooks" / "commit-msg"

# Real shapes, plus the ones that must stay refused. The colon cases are the defect.
TRAILERS = [
    ("claude-code/opus-5", True),
    ("ollama/qwen3.8:27b-mtp-q4_K_M", True),
    ("ollama/qwen2.5:0.5b", True),
    ("local/qwen3.6-35b", True),
    ("codex/gpt-5.1", True),
    ("no-slash-at-all", False),
    ("bad agent/with-space", False),
    ("has/two/slashes", False),
]


def hook_pattern():
    """The hook's own regex, read from the hook -- not a copy of it living here."""
    m = re.search(r"grep -qE '\^Agent: (.+?)\[\[:space:\]\]\*\$'", HOOK.read_text())
    return m.group(1) if m else None


def main():
    fails = []
    sys.path.insert(0, str(REPO / "scripts"))
    import audit_compliance as ac

    hp = hook_pattern()
    print("Both patterns are read from the files that hold them:")
    ok = bool(hp)
    print(f"  [{'ok' if ok else 'FAIL'}]   the hook's regex was found in the hook: {hp!r}")
    if not ok:
        fails.append("could not read the hook's trailer regex — this suite is now blind")
        print("test_trailer_agreement: FAILED")
        return 1

    print("\nEvery trailer is judged the same way by both:")
    for trailer, want in TRAILERS:
        line = f"Agent: {trailer}"
        # The hook, through grep, exactly as the hook runs it.
        h = subprocess.run(["grep", "-qE", f"^Agent: {hp}[[:space:]]*$"],
                           input=line, capture_output=True, text=True).returncode == 0
        a = bool(ac.TRAILER_RE.search(line))
        good = (h == a == want)
        if not good:
            fails.append(f"{trailer!r}: hook={h} audit={a}, expected {want}")
        print(f"  [{'ok' if good else 'FAIL'}]   {trailer:<34} hook={h} audit={a}")

    print(f"\ntest_trailer_agreement: {'ok' if not fails else 'FAILED'}")
    for f in fails:
        print(f"  - {f}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
