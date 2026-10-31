#!/usr/bin/env python3
"""Oracle for the jsonx migration of probe.parse().

The greedy call re.search(r"\\{.*\\}", raw, re.S) at line 428 of
projects/local-inference/probe.py spans from the first '{' to the last '}'
in the entire reply.  Measured against shapes models actually produce it
fails on three of four:

  * prose containing braces *before* the JSON object
  * prose containing braces *after* the JSON object
  * two JSON objects in a single reply

Only a bare object (no competing braces) survives the greedy match.
The migration to scripts/jsonx.py::objects() must fix all three while
preserving the existing contract: return the first parsed object or None.

This oracle is committed RED.  It is never edited by the implementer.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))

PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got!r}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


# ---------------------------------------------------------------------------
# Import the function under test (signature and return type must not change)
# ---------------------------------------------------------------------------
import probe
parse = probe.parse

# ---------------------------------------------------------------------------
# 1.  Shapes that work TODAY must keep working (bare object)
# ---------------------------------------------------------------------------
check("bare object",
      parse('{"a": 1, "b": "x"}'),
      {"a": 1, "b": "x"})

check("bare object with list value",
      parse('{"items": [1, 2, 3]}'),
      {"items": [1, 2, 3]})

check("bare object, nested",
      parse('{"outer": {"inner": true}}'),
      {"outer": {"inner": True}})

# ---------------------------------------------------------------------------
# 2a.  Prose BEFORE the object (greedy breaks when prose has braces)
# ---------------------------------------------------------------------------
check("prose before object (no extra braces)",
      parse('Here is the result: {"key": "value"}'),
      {"key": "value"})

check("prose before object (brace in prose)",
      parse('The {old} config says: {"key": "value"}'),
      {"key": "value"})

# ---------------------------------------------------------------------------
# 2b.  Prose AFTER the object (greedy breaks when prose has braces)
# ---------------------------------------------------------------------------
check("prose after object (no extra braces)",
      parse('{"key": "value"} Done.'),
      {"key": "value"})

check("prose after object (brace in prose)",
      parse('{"key": "value"} See {docs} for more.'),
      {"key": "value"})

# ---------------------------------------------------------------------------
# 2c.  Two objects in one reply – return the first
# ---------------------------------------------------------------------------
check("two objects, first wins",
      parse('{"a": 1} {"b": 2}'),
      {"a": 1})

# ---------------------------------------------------------------------------
# 3.  A closing brace inside a string value must not terminate the object
# ---------------------------------------------------------------------------
check("closing brace inside string value",
      parse('{"msg": "a}b", "x": 1}'),
      {"msg": "a}b", "x": 1})

check("opening brace inside string value",
      parse('{"msg": "a{b", "x": 1}'),
      {"msg": "a{b", "x": 1})

# ---------------------------------------------------------------------------
# 4.  Failure modes – callers expect None; do not invent a new sentinel
# ---------------------------------------------------------------------------
check("no JSON at all",
      parse("there is no object in this reply"),
      None)

check("empty string",
      parse(""),
      None)

check("malformed JSON",
      parse('{"key": }'),
      None)

check("malformed JSON surrounded by prose",
      parse('result: {bad json} end'),
      None)

check("only braces, no valid object",
      parse('{not json at all}'),
      None)

# ---------------------------------------------------------------------------
# 5.  Structural: the greedy regex must be gone from probe.py source
# ---------------------------------------------------------------------------
src = (ROOT / "projects" / "local-inference" / "probe.py").read_text()
# The offending call, as literal characters in the file:
#   re.search(r"\{.*\}", raw, re.S)
# We flag any line containing that exact regex argument.
greedy_marker = 're.search(r"\\{.*\\}"'
check("greedy regex call removed from probe.py",
      greedy_marker in src,
      False)

# ---------------------------------------------------------------------------
print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)