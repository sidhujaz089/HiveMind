#!/usr/bin/env python3
"""t671 -- symindex find + skills find, exposed as logged MCP tools (core.py half).

Tests core.Workspace.symindex_find and core.Workspace.skills_find, and that both
names are registered in STDIO_TOOLS and HTTP_TOOLS. Red on main because neither
method exists and neither name is in the tuples. Hermetic: the two find APIs are
faked via sys.modules (the real modules are never loaded), and the call log is
redirected to a temp file, so nothing touches the live index, the live memory, or
workspace/mcp-calls.jsonl.
"""
import sys
import tempfile
import types
import json
from pathlib import Path

# From scripts/tests/ the repo root is parents[2]; scripts/ (where the find
# modules and the workspace_mcp package live) is parents[1].
ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# ---- fakes for the two find seams (injected before core.py's lazy import) ----
# Distinct values so the oracle can tell them apart (no repeated placeholder).
SYM_DEFS = [{"file": "x.py", "line": 7, "kind": "def", "sig": "foo(a, b)", "doc": "does foo"}]
SYM_REFS = ["y.py", "z.py"]
sym_calls = []
def _fake_sym_find(name, *args, **kwargs):
    sym_calls.append({"name": name})
    return (SYM_DEFS, SYM_REFS) if name == "foo" else ([], [])
_fake_sym = types.ModuleType("symindex")
_fake_sym.find = _fake_sym_find
sys.modules["symindex"] = _fake_sym

SKILL_HITS = [
    {"id": "m1", "fact": "skill fact one", "see": "docs/a.md", "tags": ["skill"]},
    {"id": "m2", "fact": "skill fact two", "see": "docs/b.md", "tags": ["skill", "lesson"]},
    {"id": "m3", "fact": "not a skill", "see": "docs/c.md", "tags": ["other"]},
]
skills_calls = []
def _fake_skills_find(query, limit=5):
    skills_calls.append({"query": query, "limit": limit})
    hits = [h for h in SKILL_HITS if "skill" in (h.get("tags") or [])]
    return hits[:limit]
_fake_skills = types.ModuleType("skills")
_fake_skills.find = _fake_skills_find
sys.modules["skills"] = _fake_skills

from workspace_mcp.core import Workspace, STDIO_TOOLS, HTTP_TOOLS

PASSED = 0
FAILED = 0
def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"ok    {label}")
    else:
        FAILED += 1
        print(f"FAIL  {label}\n  got  {got!r}\n  want {want!r}")

tmp = Path(tempfile.mkdtemp(prefix="t671-find-"))
LOG_PATH = tmp / "mcp-calls.jsonl"

def make_ws():
    # Redirect the call log to the temp file so no live state is touched.
    return Workspace(str(ROOT), caller="test", log_path=str(LOG_PATH))

# ---- symindex_find ---------------------------------------------------------
try:
    ws = make_ws()
    r = ws.symindex_find("foo")
    check("symindex_find returns the name it was asked for", r.get("name"), "foo")
    check("symindex_find returns the find API's distinct defs (file/line/kind/sig/doc)",
          r.get("defs"), SYM_DEFS)
    check("symindex_find returns the find API's distinct refs", r.get("refs"), SYM_REFS)
    check("symindex_find passes the name through to the find API", sym_calls[-1], {"name": "foo"})
except Exception as e:
    FAILED += 1
    print(f"FAIL  symindex_find: {type(e).__name__}: {e}")

# ---- skills_find -----------------------------------------------------------
try:
    ws = make_ws()
    s = ws.skills_find("query", limit=1)
    check("skills_find passes the limit through to the find API", skills_calls[-1]["limit"], 1)
    check("skills_find reports the (limit-cut) hit count", s.get("count"), 1)
    check("skills_find shapes the returned hit with id/fact/see/tags",
          s.get("results"),
          [{"id": "m1", "fact": "skill fact one", "see": "docs/a.md", "tags": ["skill"]}])
except Exception as e:
    FAILED += 1
    print(f"FAIL  skills_find: {type(e).__name__}: {e}")

# ---- registry: one entry each in core.py's two tool tuples -----------------
check("symindex_find is in STDIO_TOOLS", "symindex_find" in STDIO_TOOLS, True)
check("symindex_find is in HTTP_TOOLS", "symindex_find" in HTTP_TOOLS, True)
check("skills_find is in STDIO_TOOLS", "skills_find" in STDIO_TOOLS, True)
check("skills_find is in HTTP_TOOLS", "skills_find" in HTTP_TOOLS, True)

# ---- logged in mcp-calls.jsonl (redirected to the temp log) ----------------
if LOG_PATH.is_file():
    rows = [json.loads(l) for l in LOG_PATH.read_text().splitlines() if l.strip()]
    sym_rows = [r for r in rows if r.get("tool") == "symindex_find"]
    skill_rows = [r for r in rows if r.get("tool") == "skills_find"]
    check("symindex_find call is logged with (tool, target, outcome)",
          [(r.get("tool"), r.get("target"), r.get("outcome")) for r in sym_rows],
          [("symindex_find", "foo", "ok")])
    check("skills_find call is logged with (tool, target, outcome)",
          [(r.get("tool"), r.get("target"), r.get("outcome")) for r in skill_rows],
          [("skills_find", "query", "ok")])
else:
    FAILED += 1
    print(f"FAIL  mcp-calls log was not written (expected {LOG_PATH})")

print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
