#!/usr/bin/env python3
"""GATE: a freeze that adds a RED oracle declares it, so it cannot hold every build (2026-10-12:
t1024's undeclared suites held the whole line "red on main" from 2026-10-11). Hermetic: a temp repo."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import bridge_trigger as bt

P = F = 0


def check(label, got, want):
    global P, F
    ok = got == want
    P += ok
    F += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f": got {got!r}, want {want!r}"))


def git(r, *a):
    return subprocess.run(["git", "-C", str(r), *a], capture_output=True, text=True)


with tempfile.TemporaryDirectory() as td:
    r = Path(td)
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@x")
    git(r, "config", "user.name", "t")
    (r / "workspace").mkdir()
    (r / "scripts" / "tests").mkdir(parents=True)
    ceil = r / "workspace" / "test-failures-ceiling.json"
    ceil.write_text(json.dumps({"allowed_to_fail": ["old.py"], "_oracles_awaiting_their_fix": {}}))
    (r / "scripts" / "tests" / "test_red.py").write_text("import sys\nsys.exit(1)\n")
    (r / "scripts" / "tests" / "test_green.py").write_text("print('ok')\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "seed")
    got = bt.declare_new_oracles(r, "t9999", ["scripts/tests/test_red.py", "scripts/tests/test_green.py",
                                              "scripts/other.py"], "swift")
    d = json.loads(ceil.read_text())
    check("only the red new oracle is declared", got, ["test_red.py"])
    check("  it is in allowed_to_fail", "test_red.py" in d["allowed_to_fail"], True)
    check("  a green new oracle is left alone", "test_green.py" in d["allowed_to_fail"], False)
    check("  its reason names the owning task", d["_oracles_awaiting_their_fix"]["test_red.py"].startswith("t9999"), True)
    check("  and the declaration is committed", "declare t9999" in git(r, "log", "-1", "--format=%s").stdout, True)
    check("a second call declares nothing new", bt.declare_new_oracles(r, "t9999", ["scripts/tests/test_red.py"]), [])

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
