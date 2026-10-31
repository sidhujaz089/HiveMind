#!/usr/bin/env python3
"""A renamed function that still has an old caller is a crash waiting for the next page load (2026-10-30).

The rename of manager_queue.sh -> scoping_queue.sh also renamed who_works.manager_queue_now -> scoping_queue_now, and
dispatch_loop.py -- a GUARDED file I could not edit -- still called the old name at one site. `git grep` did not catch it
because the caller is a dotted attribute, and the suite that exercises that path does not reach it.

The fix is an ALIAS in the unguarded module, so main never carries a call to a name that does not exist, and the alias
can be deleted the moment the guarded caller is renamed with its signature.

This suite fails if the alias disappears while a caller still needs it, and fails if a caller is left calling a name
that nothing defines.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
fails = []


def check(name, ok, detail=""):
    print(f"  {'ok ' if ok else 'FAIL'}  {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        fails.append(name)


sys.path.insert(0, str(HERE / "scripts"))
import who_works          # noqa: E402

check("the new name exists", hasattr(who_works, "scoping_queue_now"))
check("the OLD name still resolves, because a guarded caller uses it", hasattr(who_works, "manager_queue_now"))
check("  and it is the same function, not a copy", who_works.manager_queue_now is who_works.scoping_queue_now)

callers = []
for p in sorted((HERE / "scripts").glob("*.py")):
    text = p.read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r"who_works\.(\w+)\(", text):
        callers.append((p.name, m.group(1)))
missing = [(f, n) for f, n in callers if not hasattr(who_works, n)]
check("every who_works.<name>( call site resolves", not missing, str(missing))

print("test_rename_left_no_broken_caller:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
