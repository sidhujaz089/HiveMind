#!/usr/bin/env python3
"""Oracle for the jsonx migration of parse() in projects/local-inference/exam.py.

The current code extracts JSON with re.search(r"\\{.*\\}", raw, re.S) -- a greedy
match from the first { to the last } in the string.  Against the shapes models
actually produce this breaks on three of four:

  * prose containing a { before the JSON object
  * prose containing a } after the JSON object
  * two separate JSON objects in one reply

In every broken case json.loads raises ValueError, the except swallows it, and
the function silently falls to the regex fallback -- which finds nothing when the
model only answered in JSON.  A parser that returns {} for a perfectly good reply
scores a perfect candidate at zero.

The migration replaces the greedy regex with jsonx.objects() (balanced-brace,
string-aware).  This oracle is committed FIRST, red, and the migration is
delegated against it.

What is pinned:
  1. Every shape that works today (bare object, dict-valued verdicts, case
     mapping) must produce the same dict after migration.
  2. The three broken shapes must now return the correct verdict dict.
  3. A } inside a JSON string value must not terminate the object early.
  4. Truncated / absent JSON must still fall through to the regex fallback and
     return the same dict the callers already expect.
  5. The greedy brace-to-brace regex must be structurally absent from the source.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))

from exam import parse, VALID

PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1; print(f"  [ok]   {label}: {got!r}")
    else:
        FAILED += 1; print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


# ── 1. Shapes that work today and must not regress ──────────────────────────

# Bare object, string verdicts.
check(
    "bare object",
    parse('{"D1": "SOUND", "D2": "DEFECT"}', ["D1", "D2"]),
    {"D1": "SOUND", "D2": "DEFECT"},
)

# Bare object, dict-valued entries (the isinstance(v, dict) branch).
check(
    "bare object, dict values",
    parse('{"D1": {"verdict": "SOUND"}, "D2": {"verdict": "DEFECT"}}', ["D1", "D2"]),
    {"D1": "SOUND", "D2": "DEFECT"},
)

# Case-insensitive key mapping through canon (lowercase ids in JSON, uppercase in paper).
check(
    "lowercase keys mapped via canon",
    parse('{"d1": "SOUND", "d2": "DEFECT"}', ["D1", "D2"]),
    {"D1": "SOUND", "D2": "DEFECT"},
)

# Non-VALID verdict in JSON is stored in got but filtered from the return.
# REMOVED ON REVIEW, 2026-09-08. This asserted that parse() drops a verdict outside the
# valid word list. It does NOT do that today -- the JSON branch stores
# str(verdict).strip().upper() unchecked, and only the regex FALLBACK constrains to
# `words`. So this was a behaviour CHANGE smuggled into a migration whose contract is
# "same behaviour, better extractor". Bundled in, a green suite could hide a migration
# regression behind a new feature. The underlying observation is real and is filed
# separately.


# ── 2. The three shapes the greedy regex breaks on ──────────────────────────

# Prose with a { before the JSON object.
# Old: greedy captures "{D1} is fine. {\"D1\": ...}" → json.loads fails → {}
# New: jsonx.objects finds the single balanced object → correct dict.
check(
    "prose-brace before JSON",
    parse('I think {D1} is fine. {"D1": "SOUND", "D2": "DEFECT"}', ["D1", "D2"]),
    {"D1": "SOUND", "D2": "DEFECT"},
)

# Prose with a } after the JSON object.
# Old: greedy captures "{\"D1\": ...} (as {noted} above)" → json.loads fails → {}
# New: jsonx.objects finds the single balanced object → correct dict.
check(
    "prose-brace after JSON",
    parse('{"D1": "SOUND", "D2": "DEFECT"} (as {noted} above)', ["D1", "D2"]),
    {"D1": "SOUND", "D2": "DEFECT"},
)

# Two separate JSON objects in one reply.
# Old: greedy captures both → json.loads fails → {} (regex fallback finds nothing
#      because the model only answered in JSON structure).
# New: jsonx.objects returns two objects; the loop iterates both → correct dict.
check(
    "two objects in one reply",
    parse('{"D1": "SOUND"}\n{"D2": "DEFECT"}', ["D1", "D2"]),
    {"D1": "SOUND", "D2": "DEFECT"},
)


# ── 3. Closing brace inside a string value ─────────────────────────────────

# A } inside a string must not end the object.  A scanner that naively balances
# braces without respecting JSON strings would truncate the object here.
# D2's value "A}B" is not in VALID and the regex fallback finds no verdict word
# after D2, so only D1 survives.
check(
    "brace inside string value",
    parse('{"D1": "SOUND", "D2": "a}b"}', ["D1", "D2"]),
    {"D1": "SOUND"},
)


# ── 4. Failure shapes: malformed JSON and no JSON at all ───────────────────

# Truncated object: no complete balanced-brace object exists.
# jsonx.objects returns [].  The regex fallback must still recover D1.
check(
    "truncated JSON falls to regex",
    parse('{"D1": "SOUND", "D2":', ["D1", "D2"]),
    {"D1": "SOUND"},
)

# No JSON at all: pure prose with verdict words next to the ids.
check(
    "no JSON, pure regex fallback",
    parse("I think D1 is SOUND and D2 is a DEFECT.", ["D1", "D2"]),
    {"D1": "SOUND", "D2": "DEFECT"},
)

# Empty string: nothing to parse, nothing to regex-match.
check(
    "empty string",
    parse("", ["D1", "D2"]),
    {},
)


# ── 5. Structural: the greedy regex is gone from the source ────────────────

src = (ROOT / "projects" / "local-inference" / "exam.py").read_text()
# The literal characters \{.*\} must not appear anywhere in exam.py.
# (The regex fallback uses \b{re.escape(i)}\b.{{0,120}}? -- different pattern.)
check(
    "greedy \\{.*\\} absent from exam.py",
    r"\{.*\}" in src,
    False,
)

# More specifically: no re.search call with the brace-to-brace pattern.
import re as _re
_hits = _re.findall(
    r're\.search\s*\(\s*r?["\']\\?\{[^"\']*\\?\}["\']',
    src,
)
check(
    "no re.search( …\\{…\\}…) call survives",
    len(_hits),
    0,
)


print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)