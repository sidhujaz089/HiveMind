#!/usr/bin/env python3
"""B5 part 2: brief-scatter counts EDIT SITES, not citations. A SYMBOLS entry is a site; a file:LINE in
single_change outside every named symbol is a site; a citation in what_is_wrong or behaviour_oracle is evidence.

2026-10-30: 283 of 366 over-6 chunks on disk named 6 or fewer SYMBOLS and were refused for their evidence
citations -- which manager_prompt.py tells the scoper to write.

Run: python3 scripts/tests/test_scope_lint_edit_sites.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scope_lint as L  # noqa: E402

F = "lib/big.py"
TEXT = "".join(f"def f{k}():\n" + "    x = 1\n" * 8 + "\n" for k in range(1, 11))   # f_k spans 10k-9 .. 10k-1
FAILED = 0


def check(name, got, want):
    global FAILED
    ok = got == want
    FAILED += not ok
    print(f"  [{'ok' if ok else 'BAD'}]  {name}: {got!r}" + ("" if ok else f"  (want {want!r})"))


five = ["f1", "f2", "f3", "f4", "f5"]
check("five symbols and four evidence citations in what_is_wrong are five sites",
      L.edit_sites({"single_change": "change f1..f5", "what_is_wrong": "big.py:61 big.py:71 big.py:81 big.py:91"},
                   F, TEXT, five), 5)
check("a citation inside a named symbol is that symbol's site, not a second one",
      L.edit_sites({"single_change": "edit big.py:3 and big.py:13"}, F, TEXT, ["f1", "f2"]), 2)
check("a citation in single_change outside every named symbol is its own site",
      L.edit_sites({"single_change": "edit big.py:3 and big.py:95"}, F, TEXT, ["f1"]), 2)
check("seven single_change citations with no SYMBOLS are seven sites (still scatter)",
      L.edit_sites({"single_change": "edit big.py:100/:900/:1700/:2500/:3300/:4100/:4900"}, F, TEXT, []), 7)
check("seven SYMBOLS are seven sites (still scatter)",
      L.edit_sites({"single_change": "x"}, F, TEXT, [f"f{k}" for k in range(1, 8)]), 7)
check("an unparseable file counts every single_change citation, as before",
      L.edit_sites({"single_change": "big.py:3"}, F, "def (:\n", ["f1"]), 2)
check("the prompt says what the lint counts",
      "Citations in what_is_wrong and behaviour_oracle are evidence" in
      (Path(__file__).resolve().parents[1] / "manager_prompt.py").read_text(), True)

print(f"test_scope_lint_edit_sites: {'ok' if not FAILED else f'{FAILED} failed'}")
sys.exit(1 if FAILED else 0)
