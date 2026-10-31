#!/usr/bin/env python3
"""coder.parse() is on the ONE extractor, scripts/jsonx.py (t259, t474, t261).

t474: p19 failed three attempts on "Invalid \\escape" on 2026-09-30, a day after t400 taught
jsonx to repair exactly that -- because coder.py had its own scanner and never used jsonx.
These cases are the three failures of that kind, plus the two behaviours the migration must
keep (braces inside strings, broken JSON refused) and the one it must add (name the fault).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import coder  # noqa: E402

FAILED = 0


def report(label, got, want):
    global FAILED
    ok = got == want
    FAILED += not ok
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {str(got)[:80]}" + ("" if ok else f"  (want {want!r})"))


def main():
    src = (ROOT / "projects" / "local-inference" / "coder.py").read_text()
    body = src[src.find("def parse(raw):"):src.find("def _indent_of(")]
    report("parse() uses jsonx", "jsonx.scan(" in body, True)
    o, e = coder.parse('{"ops":[{"path":"a.py","old":"re.sub(r\\"\\d+\\", x)","new":"y"}]}')
    report("an invalid \\d escape in code is repaired, not refused (t474)", o is not None, True)
    o, e = coder.parse('Example: {"a": 1}. Answer: {"ops":[{"path":"a","from":1,"to":1,"new":"x"}]}')
    report("the object with ops is chosen over an example before it", (o or {}).get("ops", [{}])[0].get("path"), "a")
    o, e = coder.parse("{'ops': [{'path': 'a.py', 'old': 'x', 'new': 'y'}]}")
    report("a Python dict is refused", o, None)
    report("and the reason names double quotes (t261)", "DOUBLE quotes" in (e or ""), True)
    o, e = coder.parse('{"why": "close the brace }", "ops": [{"path":"a.py","old":"x","new":"y"}]}')
    report("a brace inside a string still parses", o is not None, True)
    o, e = coder.parse('{"why": "unterminated')
    report("broken JSON is still refused", (o, e), (None, "unterminated JSON object"))
    # 2026-10-01: the list left open before the next key, from a real UNPARSEABLE reply
    o, e = coder.parse('{"ops": [{"path": "a.py", "old": "x", "new": "y"}, "why": "z"}')
    report("an ops list left open before the next key is repaired", (o or {}).get("why"), "z")
    o, e = coder.parse('{"why": "no edits"}')
    report("an object with no ops says so", e, "no ops")
    print("test_coder_parse_jsonx:", "FAILED" if FAILED else "ok")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
