#!/usr/bin/env python3
"""merge_reviewed builds the merge OUTSIDE the live tree and moves main by ref (Jas, 2026-10-29: "Merge on pass").

A throwaway repo stands in for the live one: main, a branch that adds a function, and an oracle that fails on
main and passes on the branch. Checked: a dirty live path refuses; the oracle must fail first; the merge carries
the signature in its last paragraph; main moves; the live file matches; a ledger the branch carried is dropped.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
TMP = Path.home() / ".cache/hivemind-tmp/test-merge-reviewed"
FAILED = 0


def check(name, ok, detail=""):
    global FAILED
    FAILED += not ok
    print(f"  {'ok ' if ok else 'BAD'}  {name}" + ("" if ok else f"  ({detail})"))


def g(*a, cwd):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True).stdout.strip()


shutil.rmtree(TMP, ignore_errors=True)
repo = TMP / "live"
(repo / "workspace").mkdir(parents=True)
g("init", "-q", "-b", "main", cwd=repo)
g("config", "user.email", "t@t", cwd=repo); g("config", "user.name", "t", cwd=repo)
(repo / "lib.py").write_text("def a():\n    return 1\n")
(repo / "oracle.py").write_text("import sys\nimport lib\nsys.exit(0 if hasattr(lib, 'b') else 1)\n")
(repo / "workspace" / "runs.jsonl").write_text('{"row": 1}\n')
g("add", "-A", cwd=repo); g("commit", "-q", "-m", "base", cwd=repo)
# The related-tests gate stand-in, committed into the fixture's base so the SCRATCH worktree inherits it. It is
# switched by an environment variable because the scratch is a separate checkout: editing this file in the live
# stand-in would not change what the merge runs.
(repo / "scripts").mkdir(exist_ok=True)
(repo / "scripts" / "related_tests.py").write_text(
    "import os, sys\nsys.exit(int(os.environ.get('FAKE_GATE_RC', '0')))\n", encoding="utf-8")
g("add", "-A", cwd=repo); g("commit", "-qm", "add the gate stand-in", cwd=repo)
g("checkout", "-q", "-b", "coder/x", cwd=repo)
(repo / "lib.py").write_text("def a():\n    return 1\n\n\ndef b():\n    return 2\n")
(repo / "workspace" / "runs.jsonl").write_text('{"row": 1}\n{"row": "from the branch"}\n')
g("commit", "-qam", "add b", cwd=repo)
g("checkout", "-q", "main", cwd=repo)

os.environ["MERGE_REVIEWED_REPO"] = str(repo)
os.environ["MERGE_REVIEWED_SCRATCH"] = str(TMP / "scratch")
os.environ["LANES_HOME"] = str(TMP / "lanes")
sys.path.insert(0, str(HERE / "scripts"))
import merge_reviewed as M  # noqa: E402
import merge_lock  # noqa: E402

try:
    M.build("coder/x", "why", "oracle.py", "claude-code/test", "claude-code/test"); refused = False
except SystemExit:
    refused = True
check("build() refuses without merge_lock held (the scratch is shared)", refused)
_lock = merge_lock.hold("T", "test"); _lock.__enter__()

# an oracle that already passes on main cannot vouch for the branch
(repo / "always.py").write_text("raise SystemExit(0)\n"); g("add", "always.py", cwd=repo); g("commit", "-qm", "always", cwd=repo)
try:
    M.build("coder/x", "why", "always.py", "claude-code/test", "claude-code/test"); refused = False
except SystemExit:
    refused = True
check("an oracle green on main refuses", refused)

new, paths = M.build("coder/x", "the review read it", "oracle.py", "claude-code/test", "claude-code/test")
body = g("log", "-1", "--format=%B", new, cwd=repo)
check("signature is in the LAST paragraph", body.strip().split("\n\n")[-1].startswith("Reviewed-by: claude-code/test (oracle)"), body)
check("the branch's ledger rows are dropped", "workspace/runs.jsonl" not in paths, paths)
check("no message file is left behind", not list((TMP).glob("merge-reviewed-msg*")))
# ANOTHER RUN'S MESSAGE (DS, lanes 17:36Z): something replaces the message between write and commit. Stand-in:
# a commit-msg hook that rewrites it. build() must read the commit back and refuse.
hook = repo / ".git/hooks/commit-msg"
hook.write_text("#!/bin/sh\nprintf 'Merge coder/t1-x (brief)\\n\\nAgent: claude-code/auto-merge\\n' > \"$1\"\n")
hook.chmod(0o755)
try:
    M.build("coder/x", "the review read it", None, "claude-code/test", "claude-code/test")
    refused = False
except SystemExit:
    refused = True
hook.unlink()
check("a commit carrying another run's message refuses", refused)

# A LIVE FILE THAT ALREADY HOLDS THE MERGE'S CONTENT IS NOT IN THE WAY (2026-10-30). move_main has always
# documented that exception -- "differs from main only by ALREADY HOLDING the new content" -- and it only ever
# worked for a STAGED change: git() strips the whole output, so the first line of `git status --porcelain` lost the
# leading space of " M path", ln[3:] then ate the first letter of the path, REPO/"rojects/..." did not exist, and
# the file was called dirty. An unstaged path holding exactly the merged bytes blocked every merge that touched it.
_base = g("rev-parse", "main", cwd=repo)
(repo / "lib.py").write_text("def a():\n    return 1\n\n\ndef b():\n    return 2\n")   # the merge's content, unstaged
check("the live file already matches the merge", g("diff", "--quiet", new, "--", "lib.py", cwd=repo) == "")
M.move_main(new, paths, locked=True)
check("a live path already holding the merge's content does not block it",
      g("rev-parse", "main", cwd=repo) == new, "main did not move")
# put the fixture back exactly as the later cases expect it: this case MOVED main on purpose
g("update-ref", "refs/heads/main", _base, cwd=repo)
g("reset", "-q", "--hard", _base, cwd=repo)

(repo / "lib.py").write_text("# a live edit\n")
try:
    M.move_main(new, paths, locked=True); refused = False
except SystemExit:
    refused = True
check("a dirty live path refuses to move main", refused and g("rev-parse", "main", cwd=repo) != new)
g("checkout", "--", "lib.py", cwd=repo)

# THE LIVE SYNC IS CHECKED, AND IT HAPPENS BEFORE main MOVES (2026-10-30). A pre-commit hook held
# .git/index.lock for eight minutes on the box; `git checkout -- <paths>` could not take the index, its exit
# code was never read, and move_main returned SUCCESS with main at the merge and the live files still on the
# OLD content. Every reader of git said the merge had landed; the box was running the old code. On this
# repository the lock exists during every commit, so the failure was silent rather than rare.
(repo / ".git" / "index.lock").write_text("")
try:
    M.move_main(new, paths, locked=True); refused = False; why = ""
except SystemExit as e:
    refused = True; why = str(e)
check("a held index.lock refuses to move main", refused and g("rev-parse", "main", cwd=repo) != new)
check("  and the refusal names the paths it could not sync", "lib.py" in why, why[:120])
check("  and the live file is left alone", "def b" not in (repo / "lib.py").read_text())
(repo / ".git" / "index.lock").unlink()

# EVERY MERGE PAYS THE SAME GATE AS A COMMIT (Jas, 2026-10-30: "do the honest one always"). build() used to
# take a branch onto main having run nothing, so a merge was the one route to main that skipped related_tests --
# which is why a merge cost seconds while a direct commit cost 900 s, and why the cheap route was the tempting one.
main_before = g("rev-parse", "main", cwd=repo)
os.environ["FAKE_GATE_RC"] = "1"
try:
    M.build("coder/x", "why", None, "claude-code/test", "claude-code/test"); refused = False; said = ""
except SystemExit as e:
    refused = True; said = str(e)
check("a merge whose related gate is RED is refused", refused, said[:140])
check("  and the refusal says the tests were red", "RED with this branch merged" in said, said[:140])
check("  and main has not moved", g("rev-parse", "main", cwd=repo) == main_before)
os.environ["FAKE_GATE_RC"] = "0"
green_new, _green_paths = M.build("coder/x", "why", None, "claude-code/test", "claude-code/test")
check("a merge whose related gate is green is allowed through",
      green_new != new and len(green_new) == 40, str(green_new)[:60])
del os.environ["FAKE_GATE_RC"]

# A MERGE NAMES THE SERVICES IT MADE STALE (2026-10-30). A merge that touched merge_reviewed.py left swift-proxy
# serving the old code; the next session end spent sixteen minutes failing on it. The merge is the moment that
# becomes true, so the merge is the moment to say it.
check("with nothing to ask, no service is claimed stale", M.stale_services(repo) == [])
(repo / "scripts" / "check_stale_services.py").write_text(
    "print('[ok]   fine.service (started after the last change)')\n"
    "print('[STALE] fake.service')\n"
    "print('        started 50 min ago; merge_reviewed.py changed 18 min ago')\n"
    "print('        fix: systemctl --user restart fake.service')\n", encoding="utf-8")
_got = M.stale_services(repo)
check("a STALE line and its fix are read", _got == [("fake.service", "systemctl --user restart fake.service")],
      str(_got))
import io, contextlib
_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    M.move_main(new, paths, locked=True)
# A PROBE THAT CANNOT ANSWER MUST SAY SO, NOT ANSWER "nothing is stale" (2026-10-30). The report is the whole
# point: an empty list from a broken probe reads exactly like a clean bill of health, and that is the defect class
# this repository keeps finding -- the thing that existed, looked installed, and never ran.
(repo / "scripts" / "check_stale_services.py").write_text(
    "import sys\nprint('cannot reach systemd', file=sys.stderr)\nsys.exit(3)\n", encoding="utf-8")
_err = io.StringIO()
with contextlib.redirect_stderr(_err):
    _quiet = M.stale_services(repo)
check("a probe that exits non-zero still returns no claim", _quiet == [], str(_quiet))
check("  and says it could not ask, instead of implying nothing is stale",
      "could not" in _err.getvalue().lower(), _err.getvalue()[-160:])

check("moving main SAYS which service it left behind", "fake.service" in _buf.getvalue(), _buf.getvalue()[-200:])
(repo / "scripts" / "check_stale_services.py").unlink()

M.move_main(new, paths, locked=True)
check("main moved to the merge", g("rev-parse", "main", cwd=repo) == new)
check("the live file matches main", "def b" in (repo / "lib.py").read_text())
check("the live tree is clean for the merged paths", g("status", "--porcelain", "--", *paths, cwd=repo) == "")

_lock.__exit__(None, None, None)

O = {"a": "orig", "b": "b", "gone": "g"}; T = {"a": "EDITED", "b": "b", "c": "new"}; B = {"a": "orig", "b": "b", "gone": "g"}
check("library merge keeps the branch's EDIT, its addition and its removal",
      M.merge_library(O, T, B) == [] and O == {"a": "EDITED", "b": "b", "c": "new"}, O)
O = {"a": "MAIN"}; T = {"a": "BRANCH"}; B = {"a": "orig"}
check("  and an entry both sides changed is a clash, never a pick", M.merge_library(O, T, B) == ["a"] and O["a"] == "MAIN", O)

src = (HERE / "scripts" / "auto_merge.py").read_text()
check("auto_merge builds through merge_reviewed, never `git merge` in the live tree (t9562)",
      "merge_reviewed.build(" in src and "move_main(new, paths, locked=True)" in src
      and 'git("merge", "--no-ff"' not in src)

shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAILED else 0)
