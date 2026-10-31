#!/usr/bin/env python3
"""The harness inventory must not go blind, which it did twice while looking fine.

`harness_inventory.py` scans for defect signatures this repository actually shipped. Its
comment/docstring filter was wrong twice on 2026-09-07, and BOTH versions produced plausible
output:

  1. Blanking every STRING token blinded it completely -- the greedy-extract signature IS a
     string literal. The scan fell from 41 scripts to 15 and moved three files carrying the
     known bug into "trusted".
  2. Rebuilding the source from tokens inserted separators, turning `re.search(` into
     `re . search (`, so the pattern stopped matching intact code.

Neither raised an error. The only thing that catches this is asserting against files whose
contents are already known -- which is what this does. `docs/CHECKS.md` rule 1: a suite must
prove a failure, not only a pass.
"""
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


out = subprocess.run([sys.executable, str(REPO / "scripts" / "harness_inventory.py"), "--json"],
                     capture_output=True, text=True, timeout=180)
check("the inventory runs", out.returncode, 0)
rows = {r["path"]: r for r in json.loads(out.stdout or "[]")}

# GROUND TRUTH, computed by an INDEPENDENT grep at test time rather than frozen in a list.
#
# It was a frozen list until 2026-09-08, with a comment telling whoever fixed a file to remove
# it "in the same commit". That cannot work when the fix is DELEGATED: coder.py was given
# deliberate.py and only deliberate.py, migrated it correctly onto jsonx, and this suite failed
# it -- five times. A list of today's defects asserted as expected answers is a ratchet that
# locks the bugs in, and the fixer is the one party who cannot see it.
#
# So the property, not the instance: every file whose source actually contains the greedy
# pattern must be flagged, and the grep here is deliberately a different implementation from
# the one in harness_inventory.py, because a detector checked against itself checks nothing.
# The strings and comments have to come out first, or this flags jsonx.py -- whose docstring
# QUOTES the greedy pattern to explain why the module exists. That is prose, not a call.
# harness_inventory.py does it with a regex; this does it with the tokenizer, so the two
# remain genuinely independent implementations rather than one checking itself.
GREEDY_RE = re.compile(r"re\.(?:search|match|findall)\(\s*r?[\"']\\?\{\.\*")


def code_tokens(path):
    r"""Source with DOCSTRINGS removed and every other string kept.

    Removing all strings blinds this completely -- the greedy signature IS a string literal,
    `re.search(r"\{.*\}", ...)`, which is the trap this suite's header warns about and which
    I fell into on 2026-09-08 while trying to avoid a different one. Only bare string
    EXPRESSIONS (docstrings) come out; a string passed as an argument stays.
    """
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
    except Exception:                                        # noqa: BLE001
        return ""
    drop = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            drop.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return "\n".join("" if i in drop else line
                      for i, line in enumerate(src.splitlines(), 1))


GREEDY = sorted(
    str(f.relative_to(REPO))
    for f in REPO.rglob("*.py")
    if not any(part in {".venv", "node_modules", ".git", "tests", "__pycache__"}
               for part in f.parts)
    and GREEDY_RE.search(code_tokens(f))
)
print(f"It sees every greedy-extract that is actually there ({len(GREEDY)} today):")
for path in GREEDY:
    check(f"  greedy-extract in {path.split('/')[-1]}",
          "greedy-extract" in (rows.get(path) or {}).get("faults", []), True)
# NOT "> 0". That asserted the defect still exists, so completing the migration would have
# broken this suite -- the eighth instance of pinning today's bug as the expected answer, and
# the first one Q16 caught rather than me. It was right about the problem and wrong about the
# remedy: it edited this file, which is the one thing a delegated change may not do.
#
# What this check is actually for is that the independent grep still WORKS. Prove that against
# a known string rather than against the tree's current state.
_probe = 'm = re.search(r"\\{.*\\}", raw, re.S)'
check("the independent grep still detects a greedy call", bool(GREEDY_RE.search(_probe)), True)
check("and it does not fire on an innocent one",
      bool(GREEDY_RE.search('re.search(r"\\{[^{}]*\\}", raw)')), False)

print("\nAnd it does not invent them:")
# console.py was read by hand on 2026-09-07: it records done_reason and has no greedy scan.
check("console.py is clean", (rows.get("projects/local-inference/console.py") or {}).get("faults"), [])
# The scanner must not flag its own documentation -- the signatures are quoted in its docstring.
check("the inventory does not flag itself",
      (rows.get("scripts/harness_inventory.py") or {}).get("faults"), [])

print("\nMentioning the stop reason is not using it (t300):")
import tempfile as _tf
sys.path.insert(0, str(REPO / "scripts"))
import harness_inventory as inv  # noqa: E402
_d = Path(_tf.mkdtemp(dir=str(REPO / "scripts")))
try:
    (_d / "rec.py").write_text('import json\nURL = "http://127.0.0.1:11434/api/chat"\n'
                               'def ask(d):\n    return {"text": d["message"], "done_reason": d.get("done_reason")}\n')
    (_d / "act.py").write_text('import json\nURL = "http://127.0.0.1:11434/api/chat"\n'
                               'def ask(d):\n    if d.get("done_reason") == "length":\n        raise ValueError("cut")\n'
                               '    return d["message"]\n')
    check("a file that only RECORDS it is not trusted",
          inv.audit(_d / "rec.py")["faults"], ["stop-reason-recorded-only"])
    check("a file that ACTS on it is", inv.audit(_d / "act.py")["faults"], [])
finally:
    import shutil as _sh
    _sh.rmtree(_d, ignore_errors=True)

print("\nAnd it still sees a realistic amount of the repository:")
check("more than 25 scripts touch a model", len(rows) > 25, True)

print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
