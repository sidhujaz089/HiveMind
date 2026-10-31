#!/usr/bin/env python3
"""The symbols a patch touches, so the oracle-conflict classifier can actually fire (A8, 2026-10-30).

A8's classifier needs two things: the target suite, the collateral suites, and **the symbols the patch changed**.
coder's ops carry PATHS, not symbols, so the classifier could never be called -- which is why it is merged and not
wired, in as many words. This is the missing input.

It reads the DIFF, not the whole file: a symbol the patch did not touch must not appear, or every unrelated function
in a 3000-line file becomes a candidate and the classifier starts claiming conflicts between things the patch never
went near.

RED ON MAIN: scripts/patch_symbols.py does not exist.
"""
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "scripts"))
import patch_symbols as ps          # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"  {'ok ' if ok else 'FAIL'}  {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        fails.append(name)


diff = """diff --git a/lib.py b/lib.py
--- a/lib.py
+++ b/lib.py
@@ -1,4 +1,9 @@
 def keep_me():
     pass
+
+
+def first_budget(prompt, window):
+    return window - prompt
+MAX_TOKENS_FIRST = 2048
+class Clock:
+    pass
-    old_call()
"""
got = ps.from_diff(diff)
check("a function the patch ADDS is a symbol", "first_budget" in got, str(got))
check("a constant it adds is a symbol", "MAX_TOKENS_FIRST" in got, str(got))
check("a class it adds is a symbol", "Clock" in got, str(got))
check("a function the patch does NOT touch is not", "keep_me" not in got, str(got))
check("a bare call is not a definition", "old_call" not in got, str(got))

# removals count: a patch that deletes a symbol is touching it
check("a REMOVED definition is a symbol too", "gone_helper" in ps.from_diff("--- a/x.py\n+++ b/x.py\n-def gone_helper():\n"),
      str(ps.from_diff("--- a/x.py\n+++ b/x.py\n-def gone_helper():\n")))

# the file header lines must not become symbols
check("the diff header is not a symbol", not any(s in got for s in ("lib.py", "b/lib")), str(got))

# nothing at all is an empty set, never a guess
check("an empty diff is empty", ps.from_diff("") == set(), str(ps.from_diff("")))

# and it can read a real working tree
with tempfile.TemporaryDirectory() as td:
    r = Path(td)
    (r / "scripts").mkdir()
    (r / "scripts" / "m.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    import subprocess
    subprocess.run(["git", "init", "-q", "."], cwd=r, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=r, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=r, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=r, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "x"], cwd=r, capture_output=True)
    (r / "scripts" / "m.py").write_text("def a():\n    return 1\n\n\ndef added_one():\n    return 2\n", encoding="utf-8")
    got = ps.working_tree(r)
    check("it reads the patch from a real working tree", "added_one" in got, str(got))
    check("  and not the untouched function", "a" not in got, str(got))
    check("  scoped to the paths it is given", ps.working_tree(r, paths=["scripts/other.py"]) == set(),
          str(ps.working_tree(r, paths=["scripts/other.py"])))

print("test_patch_symbols:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
