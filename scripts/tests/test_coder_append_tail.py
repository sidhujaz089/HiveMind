#!/usr/bin/env python3
"""t672: a brief that APPENDS to a big non-Python file shows the builder the file's tail, with real line numbers."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import coder  # noqa: E402

FAILS = []


def ck(name, cond, detail=""):
    print(f"  [{'ok' if cond else 'BAD'}]  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILS.append(name)


text = "".join(f"line {i}\n" for i in range(1, 5001))
t = coder.sliced_tail(text, n=80)
body = [l for l in t.splitlines() if l[:5].strip().isdigit()]
ck("only the last 80 lines are shown", len(body) == 80, len(body))
ck("  with the file's REAL line numbers", body[0].split()[0] == "4921" and body[-1].split()[0] == "5000",
   (body[0], body[-1]))
ck("  and it says where the change goes", "after line 5000" in t)
ck("a short file is shown whole", len([l for l in coder.sliced_tail("a\nb\n").splitlines()
                                      if l[:5].strip().isdigit()]) == 2)
ck("the 270 KB LESSONS.md becomes a few KB", len(coder.sliced_tail((ROOT / "docs" / "LESSONS.md").read_text())) < 20_000)

print("test_coder_append_tail:", "FAILED" if FAILS else "ok")
sys.exit(1 if FAILS else 0)
