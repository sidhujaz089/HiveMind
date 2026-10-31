"""Shared oracle: one script must have no findings for one library check.

Used by generated compliance chunks. When a check in scripts/library_checks.py is added and the
audit finds existing violations, each violating script becomes a chunk whose target is simply
"this check finds nothing in this file" -- the check IS the oracle, so a fix cannot satisfy a
test written separately from the rule it is meant to enforce.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def run(rel, check):
    import library_checks
    found = library_checks.run(rel, (ROOT / rel).read_text(encoding="utf-8", errors="replace"), only=[check])
    for _c, _s, line, code in found:
        print(f"  [BAD]  {check} at {rel}:{line}  {code}")
    if not found:
        print(f"  [ok]   {rel} passes {check}")
    print(f"\n{0 if found else 1} passed, {len(found)} failed")
    return 1 if found else 0
