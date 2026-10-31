#!/usr/bin/env python3
"""Fixture tests for check_workspace.py.

AGENTS.md rule 9 and the standing rule in workspace/ROADMAP.md: a check is not
trusted until it has been observed FAILING on a fixture built to break it. A
check that has only ever been seen passing is indistinguishable from a check
that cannot fail.

Each test below builds a throwaway git repo, breaks exactly one invariant, and
asserts the corresponding check reports the right status -- so the evidence is
the refusal itself, not a reading of the source.

    python3 scripts/tests/test_check_workspace.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from check_workspace import Checker  # noqa: E402

PASSED = 0
FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)


def make_repo():
    """A minimal repo shaped like the real one, with every invariant satisfied."""
    root = Path(tempfile.mkdtemp())
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")

    (root / "STATE.md").write_text("# STATE\n\n**Last updated:** 2030-01-01\n")
    (root / "workspace").mkdir()
    for box in ("inbox", "outbox", "feedback"):
        d = root / "workspace" / box
        d.mkdir()
        (d / "README.md").write_text(f"# {box}\n")
    (root / "workspace" / "sessions" / "chat").mkdir(parents=True)
    (root / "workspace" / "sessions" / "chat" / "README.md").write_text("# chat\n")
    (root / "workspace" / "PROJECT-INDEX.md").write_text("# index\n")
    (root / "projects" / "demo").mkdir(parents=True)
    (root / "projects" / "demo" / "STATUS.md").write_text("# demo\n")

    hooks = root / "scripts" / "hooks"
    hooks.mkdir(parents=True)
    for h in ("pre-commit", "commit-msg", "pre-push"):
        p = hooks / h
        p.write_text("#!/bin/sh\nexit 0\n")
        p.chmod(0o755)

    git(root, "add", "-A")
    git(root, "-c", "core.hooksPath=", "commit", "-q", "-m", "init", "--no-verify")
    git(root, "config", "core.hooksPath", "scripts/hooks")
    return root


def statuses(root, method, *args):
    """Run one check method on a repo and return {name: status}."""
    c = Checker(root, quick=True)
    out = {}
    c.result = lambda s, n, d=None: out.__setitem__(n, s)
    c.section = lambda t: None
    getattr(c, method)(*args)
    return out


def find(out, needle):
    for name, status in out.items():
        if needle.lower() in name.lower():
            return status
    return None


print("\ncheck_workspace.py fixtures\n")

# --- baseline: an intact repo passes ------------------------------------------------
r = make_repo()
check("intact repo: hooks installed", find(statuses(r, "check_enforcement"), "hooks"), "PASS")
check("intact repo: inbox tracked", find(statuses(r, "check_structure"), "inbox"), "PASS")
check("intact repo with no chat/: says it is absent", find(statuses(r, "check_chat_bridge"), "chat/"), "WARN")
shutil.rmtree(r, ignore_errors=True)

# --- enforcement: the layer that silently did nothing on the Drive mount ------------
r = make_repo()
git(r, "config", "--unset", "core.hooksPath")
check("hooksPath unset is caught", find(statuses(r, "check_enforcement"), "hooks"), "FAIL")
shutil.rmtree(r, ignore_errors=True)

r = make_repo()
for h in ("pre-commit", "commit-msg", "pre-push"):
    (r / "scripts" / "hooks" / h).chmod(0o644)
check("non-executable hooks are caught",
      find(statuses(r, "check_enforcement"), "executable"), "FAIL")
shutil.rmtree(r, ignore_errors=True)

r = make_repo()
(r / "scripts" / "hooks" / "pre-push").unlink()
check("missing hook file is caught", find(statuses(r, "check_enforcement"), "hooks"), "FAIL")
shutil.rmtree(r, ignore_errors=True)

# --- feedback ------------------------------------------------------------------------
r = make_repo()
(r / "workspace" / "feedback" / "note.md").write_text("status: OPEN\n\nsomething\n")
check("open feedback warns", find(statuses(r, "check_enforcement"), "feedback"), "WARN")
shutil.rmtree(r, ignore_errors=True)

# --- secrets -------------------------------------------------------------------------
r = make_repo()
# Built at runtime, never written as a literal: the pre-commit hook scans this
# repository for credential-shaped strings and would (correctly) refuse to commit
# a test fixture that contained one. AGENTS.md rule 9 — fix the content, not the
# check. Verified 2026-08-17 by the hook blocking exactly this line.
# NOT A DECLARED FAKE (2026-10-24). This was `sk-ant-` + 26 A's, whose hash is a long-standing
# entry in check_workspace's declared-fake list -- an entry that was 63 hex characters until
# t1563 repaired it, which is the only reason this fixture ever failed the scan: it was testing
# a value the repository had already declared fake. It now uses an undeclared body, and asserts
# it stays undeclared, so the fixture cannot drift back into a declared value silently.
fake_key = "sk-" + "ant-" + ("Zq7" * 9)
check("the tracked-leak fixture is NOT a declared fake",
      __import__("hashlib").sha256(fake_key.encode()).hexdigest() in
      __import__("check_workspace").KNOWN_FAKE, False)
(r / "leak.md").write_text(f"key = {fake_key}\n")
git(r, "add", "-A")
git(r, "-c", "core.hooksPath=", "commit", "-q", "-m", "x", "--no-verify")
check("secret in tracked file is caught",
      find(statuses(r, "check_secrets"), "secret pattern in tracked"), "FAIL")
shutil.rmtree(r, ignore_errors=True)

# --- file hazards --------------------------------------------------------------------
# The Google-native stub cases (.gdoc and friends) went with the Drive connector on
# 2026-08-31. Nothing can create one here any more, so a check for them would be a check
# that can never fail -- the shape CHECKS.md rule 1 exists to refuse.

r = make_repo()
(r / "empty.md").write_text("")
git(r, "add", "-A")
git(r, "-c", "core.hooksPath=", "commit", "-q", "-m", "x", "--no-verify")
check("zero-byte tracked .md is caught",
      find(statuses(r, "check_file_hazards", []), "zero-byte"), "FAIL")
shutil.rmtree(r, ignore_errors=True)

# --- structure -----------------------------------------------------------------------
r = make_repo()
(r / "projects" / "orphan").mkdir()
check("project without STATUS.md is caught",
      find(statuses(r, "check_structure"), "STATUS.md"), "FAIL")
shutil.rmtree(r, ignore_errors=True)

r = make_repo()
shutil.rmtree(r / "workspace" / "outbox")
check("missing outbox is caught", find(statuses(r, "check_structure"), "outbox"), "FAIL")
shutil.rmtree(r, ignore_errors=True)

# --- chat bridge: D-38, the corpus is tracked and nothing can push it -----------------------------
r = make_repo()
(r / "chat").mkdir()
(r / "chat" / "family.txt").write_text("personal\n")
check("an uncommitted chat/ file is caught (it has no history)",
      find(statuses(r, "check_chat_bridge"), "chat/"), "WARN")
git(r, "add", "chat/family.txt")
git(r, "-c", "core.hooksPath=", "commit", "-q", "-m", "x", "--no-verify")
check("a committed chat/ passes", find(statuses(r, "check_chat_bridge"), "chat/"), "PASS")
check("no remote passes", find(statuses(r, "check_git_state"), "no remote"), "PASS")
git(r, "remote", "add", "origin", "git@example.invalid:x/y.git")
check("a remote is caught -- a push would carry chat/ off the machine",
      find(statuses(r, "check_git_state"), "no remote"), "FAIL")
shutil.rmtree(r, ignore_errors=True)

# The Drive sharing audit was removed on 2026-08-31 with the connector. Its cases lived
# here -- a missing log, a stale log, a real grant, and a SCOPE row not mistaken for one.
# They are in git history rather than commented out: a disabled test is a test nobody runs
# that still reads as coverage.

# --- STATE.md staleness ---------------------------------------------------------------
r = make_repo()
(r / "STATE.md").write_text("# STATE\n\n**Last updated:** 2020-01-01\n")
git(r, "add", "-A")
git(r, "-c", "core.hooksPath=", "commit", "-q", "-m", "x", "--no-verify")
check("stale STATE.md warns", find(statuses(r, "check_drifting_claims"), "STATE.md"), "WARN")
shutil.rmtree(r, ignore_errors=True)

r = make_repo()
(r / "STATE.md").write_text("# STATE\n\nno last-updated line\n")
check("STATE.md with no date line warns",
      find(statuses(r, "check_drifting_claims"), "last updated"), "WARN")
shutil.rmtree(r, ignore_errors=True)

# --- the enforcement ratchet (docs/CHECKS.md) -------------------------------------------
# These are the checks that make a finding become a check. If THEY only ever pass, the
# whole doctrine is theatre -- so each is made to fire here.

def with_lessons(body):
    r = make_repo()
    (r / "docs").mkdir(exist_ok=True)
    (r / "docs" / "LESSONS.md").write_text(body, encoding="utf-8")
    return r

DECLARED = """**L-099 [VERIFIED 2030-01-01] Something was learned.**
Evidence here.
ENFORCEMENT: none — judgement. Cannot be mechanised.
"""
UNDECLARED = """**L-099 [VERIFIED 2030-01-01] Something was learned.**
Evidence here, but nobody said how it is enforced.
"""
OLD_UNDECLARED = """**L-001 [VERIFIED 2020-01-01] An old lesson from before the rule.**
Evidence here. No ENFORCEMENT line, and that is correct -- it predates the requirement.
"""

r = with_lessons(DECLARED)
check("lesson WITH enforcement passes",
      find(statuses(r, "check_enforcement_discipline"), "declare how they are enforced"), "PASS")
shutil.rmtree(r, ignore_errors=True)

r = with_lessons(UNDECLARED)
check("lesson WITHOUT enforcement is caught",
      find(statuses(r, "check_enforcement_discipline"), "declare how they are enforced"), "FAIL")
shutil.rmtree(r, ignore_errors=True)

# The cutoff must actually exempt old entries, or the check becomes one nobody can satisfy
# and everybody learns to silence (AGENTS.md rule 9).
r = with_lessons(OLD_UNDECLARED)
check("pre-cutoff lesson is exempt, not failed",
      find(statuses(r, "check_enforcement_discipline"), "declare how they are enforced"), "PASS")
shutil.rmtree(r, ignore_errors=True)

# --- L-045: escapes echo eats ------------------------------------------------------------
r = with_lessons(DECLARED)
(r / "scripts" / "hooks" / "pre-commit").write_text(
    '#!/bin/sh\necho "key belongs at %USERPROFILE%\\.secrets\\anthropic.env"\n'
)
check("hook message with a mangled escape is caught",
      find(statuses(r, "check_enforcement_discipline"), "mangled escapes"), "FAIL")
shutil.rmtree(r, ignore_errors=True)

# A comment mentioning the escape is not a message a user will read.
r = with_lessons(DECLARED)
(r / "scripts" / "hooks" / "pre-commit").write_text(
    '#!/bin/sh\n# note: \\a is BEL and would be eaten here\necho "safe message"\n'
)
check("escape inside a comment is NOT flagged",
      find(statuses(r, "check_enforcement_discipline"), "mangled escapes"), "PASS")
shutil.rmtree(r, ignore_errors=True)

# --- L-040: the filesystem the repo sits on ----------------------------------------------
r = with_lessons(DECLARED)
fuse_like = r.parent / ("gvfs-" + r.name)
shutil.move(str(r), str(fuse_like))
check("a gvfs-shaped repository path is caught",
      find(statuses(fuse_like, "check_enforcement_discipline"), "local filesystem"), "FAIL")
shutil.rmtree(fuse_like, ignore_errors=True)

# --- L-046: single-sourced facts ---------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sync_facts  # noqa: E402

OWNER = """# Owner

<!-- FACT:demo -->
The canonical sentence.
<!-- /FACT -->
"""

def facts_repo(owner_body, consumer_body=None):
    r = make_repo()
    (r / "docs").mkdir(exist_ok=True)
    (r / "docs" / "OWNER.md").write_text(owner_body, encoding="utf-8")
    if consumer_body is not None:
        (r / "docs" / "CONSUMER.md").write_text(consumer_body, encoding="utf-8")
    git(r, "add", "-A")
    git(r, "-c", "core.hooksPath=", "commit", "-q", "-m", "x", "--no-verify")
    return r

MATCHING = """# Consumer

<!-- USES:demo FROM docs/OWNER.md -->
The canonical sentence.
<!-- /USES -->
"""
DRIFTED = """# Consumer

<!-- USES:demo FROM docs/OWNER.md -->
Something somebody edited by hand.
<!-- /USES -->
"""
EMPTY = """# Consumer

<!-- USES:demo FROM docs/OWNER.md -->
<!-- /USES -->
"""
UNCLOSED = """# Consumer

<!-- USES:demo FROM docs/OWNER.md -->
No closing marker at all.
"""
WRONG_OWNER = """# Consumer

<!-- USES:demo FROM docs/SOMEWHERE-ELSE.md -->
The canonical sentence.
<!-- /USES -->
"""

r = facts_repo(OWNER, MATCHING)
check("matching copy passes", sync_facts.evaluate(r), ([], []))
shutil.rmtree(r, ignore_errors=True)

r = facts_repo(OWNER, DRIFTED)
_, drift = sync_facts.evaluate(r)
check("hand-edited copy is caught as drift", len(drift), 1)
shutil.rmtree(r, ignore_errors=True)

# The bug found 2026-08-17: an EMPTY block did not match the regex, so the tool reported
# "in step" having never seen it. A check that passes by not looking.
r = facts_repo(OWNER, EMPTY)
_, drift = sync_facts.evaluate(r)
check("EMPTY copy is caught, not silently skipped", len(drift), 1)
shutil.rmtree(r, ignore_errors=True)

r = facts_repo(OWNER, UNCLOSED)
probs, _ = sync_facts.evaluate(r)
check("unclosed block is reported as broken", len(probs), 1)
shutil.rmtree(r, ignore_errors=True)

r = facts_repo(OWNER, WRONG_OWNER)
probs, _ = sync_facts.evaluate(r)
check("copy naming the wrong owner is caught", len(probs), 1)
shutil.rmtree(r, ignore_errors=True)

r = facts_repo(OWNER, "# Consumer\n\n<!-- USES:nosuchfact FROM docs/OWNER.md -->\nx\n<!-- /USES -->\n")
probs, _ = sync_facts.evaluate(r)
check("USES of an undefined fact is caught", len(probs), 1)
shutil.rmtree(r, ignore_errors=True)

# Two owners is an error, not a merge.
r = facts_repo(OWNER, OWNER.replace("# Owner", "# Second owner"))
probs, _ = sync_facts.evaluate(r)
check("a fact defined twice is caught", len(probs), 1)
shutil.rmtree(r, ignore_errors=True)

# And the repair actually repairs.
r = facts_repo(OWNER, DRIFTED)
sync_facts.apply(r)
check("sync repairs a drifted copy", sync_facts.evaluate(r), ([], []))
shutil.rmtree(r, ignore_errors=True)

# The bug that nearly shipped: stripping inline code spans removed backticked terms from
# the CANONICAL text, so apply() would have written a mangled copy while reporting success.
RICH_OWNER = """# Owner

<!-- FACT:rich -->
| Tool | Note |
|---|---|
| `update_file` | metadata only |
<!-- /FACT -->
"""
RICH_CONSUMER = """# Consumer

<!-- USES:rich FROM docs/OWNER.md -->
<!-- /USES -->
"""
r = facts_repo(RICH_OWNER, RICH_CONSUMER)
sync_facts.apply(r)
written = (r / "docs" / "CONSUMER.md").read_text(encoding="utf-8")
check("apply() preserves backticked terms", "`update_file`" in written, True)
check("apply() preserves the table row", "| metadata only |" in written, True)
check("round-trip leaves no drift", sync_facts.evaluate(r), ([], []))
shutil.rmtree(r, ignore_errors=True)

# Prose describing the syntax must not be parsed as a real block (L-046/L-049 both do this).
PROSE = """# Notes

The mechanism uses `<!-- USES:demo FROM docs/OWNER.md -->` markers inline, like that.
Nothing here opens a real block.
"""
r = facts_repo(OWNER, PROSE)
probs, drift = sync_facts.evaluate(r)
check("inline prose mention is not parsed as a block", (probs, drift), ([], []))
shutil.rmtree(r, ignore_errors=True)

# A fenced worked example must not satisfy or confuse it either.
FENCED = """# Notes

```
<!-- USES:demo FROM docs/OWNER.md -->
an example
<!-- /USES -->
```
"""
r = facts_repo(OWNER, FENCED)
probs, drift = sync_facts.evaluate(r)
check("fenced example is not parsed as a block", (probs, drift), ([], []))
shutil.rmtree(r, ignore_errors=True)

# --- not a repo at all -----------------------------------------------------------------
d = Path(tempfile.mkdtemp())
check("no .git exits 2", Checker(d, quick=True).run(), 2)
shutil.rmtree(d, ignore_errors=True)


# --- the three checks that catch harness bugs (L-054, orphaned checks, doctrine) --------
# Each is made to fire, because a check that has only passed is the thing they exist to
# catch. That is not a joke -- six of the bugs found on 2026-08-17/18 were in checking
# code rather than in the code being checked.

def repo_with(files):
    r = make_repo()
    for rel, body in files.items():
        p = r / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return r

ONLY_PASSES = 'check("everything is fine", 2 + 2, 4)\n'
HAS_NEGATIVE = 'check("a bad thing is caught", verdict(), "FAIL")\n'

r = repo_with({"scripts/tests/test_x.py": ONLY_PASSES})
check("a suite with no failure-expectation warns",
      find(statuses(r, "check_enforcement_discipline"), "proves a failure"), "WARN")
shutil.rmtree(r, ignore_errors=True)

r = repo_with({"scripts/tests/test_x.py": HAS_NEGATIVE})
check("a suite that expects a FAIL passes",
      find(statuses(r, "check_enforcement_discipline"), "proves a failure"), "PASS")
shutil.rmtree(r, ignore_errors=True)

# An unreferenced script is the check-transcripts.py case: it worked, nothing ran it.
r = repo_with({"scripts/lonely_checker.py": "# nothing references this\n",
               "scripts/tests/test_x.py": HAS_NEGATIVE})
check("an orphaned script is caught",
      find(statuses(r, "check_enforcement_discipline"), "orphaned"), "WARN")
shutil.rmtree(r, ignore_errors=True)

r = repo_with({"scripts/wired_in.py": "# ref\n",
               "docs/USES.md": "we run `wired_in` at session start\n",
               "scripts/tests/test_x.py": HAS_NEGATIVE})
check("a referenced script is not called an orphan",
      find(statuses(r, "check_enforcement_discipline"), "orphaned"), "PASS")
shutil.rmtree(r, ignore_errors=True)

# t029/L-072: a dangling reference must FAIL unless it is declared.
# check_doc_references reads `git ls-files`, so the fixture has to TRACK its files -- a
# thing worth knowing, since an untracked fixture silently exercises nothing and passes.
def tracked_repo(files):
    r = repo_with(files)
    subprocess.run(["git", "-C", str(r), "add", "-A"], capture_output=True)
    return r

r = tracked_repo({"docs/a.md": "See `docs/missing-thing.md` for details.",
                  "scripts/tests/test_x.py": HAS_NEGATIVE})
check("an UNDECLARED dangling reference fails",
      find(statuses(r, "check_doc_references", []), "declared"), "FAIL")
shutil.rmtree(r, ignore_errors=True)

# And the same reference, declared, is quiet -- otherwise the registry buys nothing.
r = tracked_repo({"docs/a.md": "See `docs/missing-thing.md` for details.",
                  "docs/EXTERNAL-POINTERS.md": "| x | `docs/missing-thing.md` | retired |",
                  "scripts/tests/test_x.py": HAS_NEGATIVE})
check("the same reference, declared, passes",
      find(statuses(r, "check_doc_references", []), "declared"), "PASS")
shutil.rmtree(r, ignore_errors=True)

# L-054 itself: $? straight after a pipe.
MASKED = '#!/bin/sh\npython3 check.py | tail -5\nif [ $? -ne 0 ]; then exit 1; fi\n'
SAFE = '#!/bin/sh\npython3 check.py | tail -5\nif [ ${PIPESTATUS[0]} -ne 0 ]; then exit 1; fi\n'
DIRECT = '#!/bin/sh\npython3 check.py\nif [ $? -ne 0 ]; then exit 1; fi\n'

r = repo_with({"scripts/hooks/pre-push": MASKED, "scripts/tests/test_x.py": HAS_NEGATIVE})
check("$? after a pipe is caught",
      find(statuses(r, "check_enforcement_discipline"), "straight after a pipeline"), "FAIL")
shutil.rmtree(r, ignore_errors=True)

# t113: the same defect OUTSIDE scripts/hooks. The check was scoped to hooks because that is
# where L-054 was found, and a `docker pull | tail` masking exit 1 sat outside that scope. A
# check narrowed to the site of the original bug cannot see its recurrence anywhere else.
r = repo_with({"scripts/deploy.sh": MASKED, "scripts/tests/test_x.py": HAS_NEGATIVE})
check("$? after a pipe OUTSIDE scripts/hooks is caught",
      find(statuses(r, "check_enforcement_discipline"), "straight after a pipeline"), "FAIL")
shutil.rmtree(r, ignore_errors=True)


r = repo_with({"scripts/hooks/pre-push": SAFE, "scripts/tests/test_x.py": HAS_NEGATIVE})
check("PIPESTATUS is accepted",
      find(statuses(r, "check_enforcement_discipline"), "straight after a pipeline"), "PASS")
shutil.rmtree(r, ignore_errors=True)

r = repo_with({"scripts/hooks/pre-push": DIRECT, "scripts/tests/test_x.py": HAS_NEGATIVE})
check("$? with no pipe is fine",
      find(statuses(r, "check_enforcement_discipline"), "straight after a pipeline"), "PASS")
shutil.rmtree(r, ignore_errors=True)

# --- the ledger census, wired into the invariant check -------------------------------
# The fixture that matters is the CONSISTENT truncation: sections dropped, every remaining
# count correct, foot present. That is the shape a census checking a file against its own
# declared counts passes cleanly, and the whole reason the element list lives in code.
LEDGER_PATH = "chat/projects/family/State_Ledger_Family_Project.txt"


def ledger(sections, foot=True, miscount=None):
    out = ["# State Ledger\n\n**Last updated:** August 14, 2026\n\n"]
    for n in sections:
        declared = 3
        actual = miscount if (miscount is not None and n == sections[-1]) else declared
        out.append(f"## {n}. Section {n}  ({declared})\n")
        out += [f"- entry {k}\n" for k in range(actual)]
        out.append("\n")
    if foot:
        out.append("**Load-check value:** `2026-08-14 20:49 MT — cedar`\n")
    return "".join(out)


ALL12 = list(range(1, 13))

r = repo_with({LEDGER_PATH: ledger(ALL12)})
check("a complete ledger passes the census",
      find(statuses(r, "check_drifting_claims"), "section census"), "PASS")
shutil.rmtree(r, ignore_errors=True)

r = repo_with({LEDGER_PATH: ledger(ALL12[:8])})
check("CONSISTENT truncation is caught -- counts all correct, foot present",
      find(statuses(r, "check_drifting_claims"), "section census"), "FAIL")
shutil.rmtree(r, ignore_errors=True)

r = repo_with({LEDGER_PATH: ledger(ALL12, miscount=1)})
check("a count that disagrees with its header is caught",
      find(statuses(r, "check_drifting_claims"), "section census"), "FAIL")
shutil.rmtree(r, ignore_errors=True)

r = repo_with({LEDGER_PATH: ledger(ALL12, foot=False)})
check("a missing canary foot is caught",
      find(statuses(r, "check_drifting_claims"), "section census"), "FAIL")
shutil.rmtree(r, ignore_errors=True)

r = make_repo()
check("no corpus in the clone SKIPs and does not claim a pass",
      find(statuses(r, "check_drifting_claims"), "section census"), "SKIP")
shutil.rmtree(r, ignore_errors=True)

# AN EMPTY projects/ MUST NOT PASS. "Every project has a STATUS.md" is a universal claim,
# and a universal claim over an empty set is vacuously true and operationally a lie. The
# count was in the PASS text ("0 projects") and nobody reads a number inside a green line.
# env3 probe, checks-that-cannot-fail pass, round 30 -- the second of this class in half an
# hour (L-136 was the first).
r = make_repo()
shutil.rmtree(r / "projects", ignore_errors=True)
(r / "projects").mkdir()
check("an empty projects/ does not pass vacuously",
      find(statuses(r, "check_structure"), "STATUS.md"), "FAIL")
shutil.rmtree(r, ignore_errors=True)

r = make_repo()
shutil.rmtree(r / "projects", ignore_errors=True)
check("an absent projects/ does not pass vacuously",
      find(statuses(r, "check_structure"), "STATUS.md"), "FAIL")
shutil.rmtree(r, ignore_errors=True)

# A FILE THE SCANNER COULD NOT READ MUST NOT BE REPORTED AS CLEAN.
#
# `git grep` exits 1 both when it found nothing and when it could not stat a file. The check
# tested `rc == 0 and hits` and fell through to PASS, so a tracked file holding a real
# credential the process cannot read was reported clean. env3 probe, finding 26 -- the
# examined-nothing false pass, inside the secret scanner.
#
# The token is BUILT rather than written literally. A literal one in this file would trip the
# very scanner under test and block every commit, which is exactly what happened to
# workspace/probe-runs.jsonl when the prober logged its own fixture.
# NOT A DECLARED FAKE (2026-10-24). This fixture used to be `ghp_` + 36 A's, whose sha256 is
# in check_workspace's declared-fake list -- so it only ever failed the tracked scan because
# that scan did not consult the list. Giving both scans one list (t1563) made this guard's
# premise false: a value the repository had already declared fake was standing in for "a real
# credential". The body is now arbitrary and undeclared, and the assertion below keeps it that
# way, so the fixture cannot drift back into a declared value silently.
FAKE_TOKEN = "ghp_" + "Zx7Qm2Vt9KpLr4Bn8WsYd6Hf3JcT5Gu1EaZz"   # exactly 36: ghp_[A-Za-z0-9]{36}
check("the fixture credential is NOT a declared fake",
      __import__("hashlib").sha256(FAKE_TOKEN.encode()).hexdigest() in
      __import__("check_workspace").KNOWN_FAKE, False)

r = make_repo()
(r / "leak.txt").write_text(FAKE_TOKEN + "\n")
git(r, "add", "-A")
git(r, "commit", "-m", "add a credential")
check("a readable secret is caught",
      find(statuses(r, "check_secrets"), "secret pattern"), "FAIL")
(r / "leak.txt").chmod(0o000)
check("an UNREADABLE file is not called clean",
      find(statuses(r, "check_secrets"), "secret pattern"), "FAIL")
(r / "leak.txt").chmod(0o644)
git(r, "rm", "-q", "-f", "leak.txt")
git(r, "commit", "-m", "remove it")
check("and a genuinely clean tree still passes",
      find(statuses(r, "check_secrets"), "secret pattern"), "PASS")
shutil.rmtree(r, ignore_errors=True)

# ---- THE MEMORY GATE'S WAIT RATE (2026-09-22) -------------------------------------------
# Jas: "when do we stop watching? how will we be triggered to review?" Watching depends on
# somebody remembering, so the ledger is read at session start instead. A wait is not a
# fault -- the gate working correctly produces waits -- so the trigger is a RATE, and the
# fixtures below are the two sides of that line plus the two ways of having measured nothing.
from datetime import datetime, timedelta, timezone   # noqa: E402

r = Path(tempfile.mkdtemp()) / "repo"
(r / "workspace").mkdir(parents=True)
led = r / "workspace" / "dispatch-loop.jsonl"


def _ticks(noroom, plain, hours_ago=1):
    """A ledger of `noroom` memory-waits and `plain` ordinary ticks, all inside the window."""
    at = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [{"at": at, "event": "tick", "waited": "busy slot", "say": "NO ROOM: loading x"}] * noroom
    rows += [{"at": at, "event": "tick", "started": 1}] * plain
    led.write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")


import json  # noqa: E402

_ticks(noroom=8, plain=2)
check("a loop mostly waiting for memory is surfaced",
      find(statuses(r, "check_headroom_waits"), "over-waiting"), "WARN")
_ticks(noroom=1, plain=9)
check("an occasional wait is the gate working, not a finding",
      find(statuses(r, "check_headroom_waits"), "over-waiting"), "PASS")
_ticks(noroom=0, plain=6)
check("no waits at all passes", find(statuses(r, "check_headroom_waits"), "over-waiting"), "PASS")
# A stale ledger must NOT read as healthy. Absence of ticks is absence of evidence -- the
# repository's own recurring defect is a status line that reassures when it measured nothing.
_ticks(noroom=8, plain=2, hours_ago=48)
check("a ledger older than the window is SKIP, never a pass",
      find(statuses(r, "check_headroom_waits"), "over-waiting"), "SKIP")
# A torn line is a tick the check CANNOT see, so the rate is over an incomplete record and
# the report has to say so rather than quietly divide by a smaller number.
_ticks(noroom=8, plain=2)
with led.open("a", encoding="utf-8") as _f:
    _f.write("{not json\n")
_out = {}
_c = Checker(r, quick=True)
_c.result = lambda s, n, d=None: _out.__setitem__(n, (s, d or ""))
_c.section = lambda t: None
_c.check_headroom_waits()
check("a torn ledger line is declared, not skipped past",
      any("unreadable ledger line" in d for _s, d in _out.values()), True)

led.unlink()
check("a missing ledger is SKIP, never a pass",
      find(statuses(r, "check_headroom_waits"), "over-waiting"), "SKIP")
shutil.rmtree(r.parent, ignore_errors=True)

# t390: the closing lines NAME every failed check, not just count them.
_n = Checker(Path(tempfile.mkdtemp()), quick=True)
_n.result("FAIL", "the widget agrees with the disk")
check("a failed check is remembered by name for the summary",
      getattr(_n, "failed_names", []), ["the widget agrees with the disk"])
_src = (Path(__file__).resolve().parent.parent / "check_workspace.py").read_text()
check("and the summary prints each name", 'f"  FAILED: {n}"' in _src, True)
print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
