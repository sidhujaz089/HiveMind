#!/usr/bin/env python3
"""Oracle: verify_control.ask() must extract model-returned JSON via jsonx.objects(),
not via the old greedy  re.search(r"\\{.*\\}", txt, re.S)  that spans first-{ to
last-} and shreds three shapes models actually emit.

  * prose-then-JSON (brace in the prose)
  * JSON-then-prose  (brace in the trailing prose)
  * two objects in one reply

A bare object (the only shape that survived the greedy regex) must keep working.
A closing brace inside a string value must not terminate the object.
Failure is still exactly  {"parse_error": txt[:200]}  -- no new sentinel.

This file is committed RED, before the migration.  The implementer may not edit it.
"""
import sys
import json
import types
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))

PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1; print(f"  [ok]   {label}: {got!r}")
    else:
        FAILED += 1; print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


# ── import verify_control and stub its network / settings I/O ────────────────
import urllib.request
import verify_control as vc

_content = ""


class _Resp:
    def read(self):
        return json.dumps({"message": {"content": _content}})


def _fake_urlopen(req, timeout=None):
    return _Resp()


vc.settings = types.SimpleNamespace(request=lambda *a, **kw: {})
vc.prompt_for = lambda body: "stub"
urllib.request.urlopen = _fake_urlopen


def ask(content):
    """Call vc.ask with a controlled model reply."""
    global _content
    _content = content
    return vc.ask("model", "body")


# ── 1. shapes that work TODAY must keep working ──────────────────────────────
check("bare object",
      ask('{"status": "ok"}'),
      {"status": "ok"})

check("bare object with nested structure",
      ask('{"status": "ok", "items": [1, 2, 3]}'),
      {"status": "ok", "items": [1, 2, 3]})

# ── 2. shapes the greedy regex BREAKS on ─────────────────────────────────────
check("prose before (brace in prose)",
      ask('The {config} file says: {"action": "commit"}'),
      {"action": "commit"})

check("prose after (brace in prose)",
      ask('{"action": "commit"} Use {braces} carefully.'),
      {"action": "commit"})

check("two objects in one reply",
      ask('{"status": "ok"} {"status": "done"}'),
      {"status": "ok"})

# ── 3. closing brace inside a string value must not end the object ───────────
check("brace in string value",
      ask('{"msg": "use {this} not that"}'),
      {"msg": "use {this} not that"})

check("nested object with braces in strings",
      ask('{"outer": "a {x} b", "inner": {"key": "v}"}}'),
      {"outer": "a {x} b", "inner": {"key": "v}"}})

# ── 4. failure paths return the caller-expected value ────────────────────────
check("no JSON at all",
      ask("I cannot do that."),
      {"parse_error": "I cannot do that."})

check("empty string",
      ask(""),
      {"parse_error": ""})

check("malformed JSON (balanced but not valid)",
      ask("{not valid json}"),
      {"parse_error": "{not valid json}"})

# ── 5. structural: the greedy regex is gone from the source ──────────────────
src = (ROOT / "scripts" / "verify_control.py").read_text()
check("greedy brace-to-brace regex removed",
      bool(re.search(r're\.search\s*\(\s*r["\']\\{.*\\}["\']', src)),
      False)

print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)