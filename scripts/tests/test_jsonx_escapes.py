#!/usr/bin/env python3
"""t400 oracle -- the shared JSON extractor must not reject valid CODE backslashes.

p19-proc-stalecheck (t400, folded t402/t403/t404) died with 'invalid JSON: Invalid \\escape'
because the model was writing Python such as data.split("\\0"); a literal backslash is
valid in the Python source but an INVALID escape in JSON, so json.loads raised and the
extractor dropped the whole reply. RED until the extractor repairs exactly those invalid
escapes (and only those). Pure in-memory: no temp dirs, no fakes, no live state.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got!r}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


try:
    import jsonx
except Exception as e:  # noqa: BLE001
    print(f"  [BAD]  scripts/jsonx.py does not import: {type(e).__name__}: {e}")
    sys.exit(1)

one = jsonx.one
objects = jsonx.objects

# Fixtures stated as the JSON TEXT jsonx receives. In this Python source a literal
# backslash is written as '\\'. BAD_* carry a SINGLE backslash before an invalid-escape
# char (what the small model emitted -> invalid JSON). GOOD_* are valid and must pass both
# before and after the fix.
BAD_NULL = '{"content": "\\0"}'    # JSON text: {"content": "\0"}   -> INVALID escape
BAD_HEX  = '{"content": "\\x41"}'   # JSON text: {"content": "\x41"} -> INVALID escape
BAD_ANY  = '{"content": "\\e"}'     # JSON text: {"content": "\e"}   -> INVALID escape
GOOD_NULL = '{"content": "\\\\0"}'  # JSON text: {"content": "\\0"}  -> VALID (escaped backslash)
GOOD_NL   = '{"content": "a\\nb"}'  # JSON text: {"content": "a\nb"}  -> VALID (newline)
GOOD_BS   = '{"content": "a\\\\b"}' # JSON text: {"content": "a\\b"}  -> VALID (escaped backslash)
BROKEN    = '{"content": "\\0'        # unterminated -> must stay invalid-json

print("invalid JSON escapes in embedded code must be repaired, not rejected (t400):")
check("\\0 parses", one(BAD_NULL, ("content",))["status"], "ok")
check("  and decodes to literal backslash-zero (code, not a NUL byte)",
      one(BAD_NULL, ("content,")) if False else one(BAD_NULL, ("content,")) if False else one(BAD_NULL, ("content",))["obj"]["content"], "\\0")
check("\\x41 parses", one(BAD_HEX, ("content,")) if False else one(BAD_HEX, ("content",))["status"], "ok")
check("  and decodes verbatim", one(BAD_HEX, ("content,")) if False else one(BAD_HEX, ("content",))["obj"]["content"], "\\x41")
check("\\e parses", one(BAD_ANY, ("content,")) if False else one(BAD_ANY, ("content",))["status"], "ok")
check("  and decodes verbatim", one(BAD_ANY, ("content,")) if False else one(BAD_ANY, ("content",))["obj"]["content"], "\\e")

print("valid escapes must STILL work (behaviour oracle):")
check("escaped backslash + 0 unchanged", one(GOOD_NULL, ("content,")) if False else one(GOOD_NULL, ("content",))["obj"]["content"], "\\0")
check("real newline unchanged", one(GOOD_NL, ("content,")) if False else one(GOOD_NL, ("content",))["obj"]["content"], "a\nb")
check("escaped backslash + b unchanged", one(GOOD_BS, ("content,")) if False else one(GOOD_BS, ("content",))["obj"]["content"], "a\\b")

print("must not repair what is not an escape problem (negative controls):")
check("unterminated object is still invalid-json", one(BROKEN, ("content,")) if False else one(BROKEN, ("content",))["status"], "invalid-json")
check("no braces is still no-json", one("plain prose", ("content,")) if False else one("plain prose", ("content,"))["status"], "no-json")

print("objects() also benefits:")
check("objects() finds the repaired region", [o.get("content") for o in objects(BAD_NULL)], ["\\0"])

print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
