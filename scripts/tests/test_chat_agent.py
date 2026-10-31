#!/usr/bin/env python3
"""Fixture tests for chat_agent.py.

D-21 and BRIDGE-PLAN.md Step 3: every guard in this script was originally
verified by being MADE TO FIRE against seven fixture manifests, not by reading
the source. The port is re-verified the same way, against the same seven, plus
the anchored-replace cases and a proof that DRY RUN is genuinely inert.

    python3 scripts/tests/test_chat_agent.py
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import chat_agent  # noqa: E402
from chat_agent import test_operation, resolve_repo_path, delete_allowed  # noqa: E402

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


def make_root():
    root = Path(tempfile.mkdtemp())
    (root / "workspace" / "inbox").mkdir(parents=True)
    (root / "workspace" / "commands" / "applied").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "chat" / "projects" / "family").mkdir(parents=True)
    (root / "workspace" / "inbox" / "msg.md").write_text("a message\n")
    (root / "docs" / "LESSONS.md").write_text("lessons\n")
    (root / "chat" / "projects" / "family" / "State_Ledger_Family_Project.txt").write_text("ledger\n")
    return root


def verdict(op, root):
    return test_operation(op, root)[0]


def rule(op, root):
    return test_operation(op, root)[1]


print("\nchat_agent.py fixtures\n")
R = make_root()

# --- fixture 1: path escapes the repository -----------------------------------------
check("../../.. escape refused",
      rule({"op": "delete", "path": "workspace/inbox/../../../../etc/passwd"}, R),
      "path-escapes-repo")

# A sibling directory must not be mistaken for the repo root.
ok, _, _ = resolve_repo_path(R, "../" + R.name + "-other/x.txt")
check("sibling dir with same prefix refused", ok, False)

# --- fixture 2: delete outside the allowlist ----------------------------------------
check("delete under docs/ refused",
      rule({"op": "delete", "path": "docs/LESSONS.md"}, R), "delete-outside-allowlist")
check("delete of the family ledger refused",
      rule({"op": "delete", "path": "chat/projects/family/State_Ledger_Family_Project.txt"}, R),
      "delete-outside-allowlist")
check("delete inside workspace/inbox/ allowed",
      verdict({"op": "delete", "path": "workspace/inbox/msg.md"}, R), "ALLOW")
check("delete of a missing in-bounds file refused",
      rule({"op": "delete", "path": "workspace/inbox/ghost.md"}, R), "target-missing")

# --- fixture 3: rooted paths, refused UP FRONT and under the right rule -------------
# The 2026-08-13 bug: these were refused as target-missing, not as the escape they were.
# A refusal logged against the wrong rule is worse than a silent one (D-21, amendment 4).
check("unix absolute path refused as path-not-relative",
      rule({"op": "delete", "path": "/etc/passwd"}, R), "path-not-relative")
check("windows drive path refused as path-not-relative",
      rule({"op": "delete", "path": "C:/Windows/System32/config"}, R), "path-not-relative")
check("other-drive path refused as path-not-relative",
      rule({"op": "write", "path": "G:/somewhere/else.txt"}, R), "path-not-relative")
check("UNC path refused as path-not-relative",
      rule({"op": "write", "path": "\\\\server\\share\\x"}, R), "path-not-relative")

# --- fixture 4: operations outside the schema ---------------------------------------
check("op=exec refused", rule({"op": "exec", "path": "x"}, R), "op-not-allowlisted")
check("op=chmod refused", rule({"op": "chmod", "path": "x"}, R), "op-not-allowlisted")
check("missing op field refused", rule({"path": "x"}, R), "missing-field")

# --- create / write / append boundaries ----------------------------------------------
check("create over an existing file refused",
      rule({"op": "create", "path": "docs/LESSONS.md", "text": "x"}, R), "target-exists")
check("create of a new in-bounds file allowed",
      verdict({"op": "create", "path": "docs/NEW.md", "text": "x"}, R), "ALLOW")
check("append to a missing file refused",
      rule({"op": "append", "path": "docs/GHOST.md", "text": "x"}, R), "target-missing")
check("append to an existing file allowed",
      verdict({"op": "append", "path": "docs/LESSONS.md", "text": "x"}, R), "ALLOW")

# --- move / rename --------------------------------------------------------------------
check("move from a missing source refused",
      rule({"op": "move", "from": "docs/GHOST.md", "to": "docs/X.md"}, R), "source-missing")
check("move out of the repo refused",
      rule({"op": "move", "from": "docs/LESSONS.md", "to": "../escaped.md"}, R),
      "path-escapes-repo")
check("in-bounds move allowed",
      verdict({"op": "move", "from": "docs/LESSONS.md", "to": "docs/MOVED.md"}, R), "ALLOW")

# --- replace: the anchor must match EXACTLY once --------------------------------------
(R / "ledger.txt").write_text("alpha\nbeta\nMARKER-ONE: fixture-value\nbeta\n")
check("anchor matching once is allowed",
      verdict({"op": "replace", "path": "ledger.txt",
               "old": "MARKER-ONE: fixture-value", "new": "MARKER-ONE: new"}, R), "ALLOW")
check("anchor matching zero times refused",
      rule({"op": "replace", "path": "ledger.txt",
            "old": "MARKER-ONE: rotated-away", "new": "x"}, R), "anchor-not-found")
check("ambiguous anchor refused",
      rule({"op": "replace", "path": "ledger.txt", "old": "beta", "new": "x"}, R),
      "anchor-not-unique")
check("replace with no old refused",
      rule({"op": "replace", "path": "ledger.txt", "new": "x"}, R), "missing-field")
check("replace with no new refused",
      rule({"op": "replace", "path": "ledger.txt", "old": "alpha"}, R), "missing-field")

shutil.rmtree(R, ignore_errors=True)

# --- fixtures 5 and 6, and the inertness proof, need a full run -----------------------
# check_workspace is stubbed: this is a fixture tree, not a workspace, and what is under
# test is the agent's own decisions rather than the invariant check it gates on.
R = make_root()
(R / "scripts").mkdir()
(R / "scripts" / "check_workspace.py").write_text("import sys; sys.exit(0)\n")
queue = R / "workspace" / "commands"


def write_manifest(name, obj):
    p = queue / name
    p.write_text(obj if isinstance(obj, str) else json.dumps(obj))
    return p


write_manifest("01-no-agent.json", {"intent": "apply", "ops": [{"op": "delete", "path": "x"}]})
write_manifest("02-broken.json", "{ this is not json")
write_manifest("03-wrong-intent.json",
               {"issued-by": "chat", "intent": "propose", "ops": [{"op": "delete", "path": "x"}]})
write_manifest("04-empty.json", {"issued-by": "chat", "intent": "apply", "ops": []})
write_manifest("05-EXPECT-REFUSALS-probe.json",
               {"issued-by": "chat", "intent": "apply",
                "ops": [{"op": "delete", "path": "/etc/passwd"}]})
write_manifest("06-good.json",
               {"issued-by": "chat", "intent": "apply",
                "ops": [{"op": "append", "path": "docs/LESSONS.md", "text": "APPENDED\n"}]})

before = (R / "docs" / "LESSONS.md").read_text()
agent = chat_agent.Agent(R)
import io
import contextlib
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = agent.run()
out = buf.getvalue()

check("run exits 0", rc, 0)
check("armed constant is False (DRY RUN)", chat_agent.ARMED, False)
check("manifest with no issued-by refused", "no-issuing-agent" in out, True)
check("unparseable manifest refused", "manifest-unparseable" in out, True)
check("intent!=apply refused", "intent-not-apply" in out, True)
check("empty ops refused", "no-operations" in out, True)
check("probe manifest is tagged PROBE", "DRY PROBE" in out, True)

# The inertness proof: in DRY RUN nothing may change on disk.
check("DRY RUN did not perform the append",
      (R / "docs" / "LESSONS.md").read_text(), before)
check("DRY RUN did not consume the queue",
      len(list(queue.glob("*.json"))), 6)
check("DRY RUN created no applied copies",
      len(list((queue / "applied").glob("*.json"))), 0)
check("heartbeat written even so", "| RUN |" in (R / "workspace" / "chat-agent-log.txt").read_text(), True)

shutil.rmtree(R, ignore_errors=True)


# --- L-055: the arming counter must not inflate when the machine changes ----------------
# D-21's floor is 20 DISTINCT judgements. Counting log lines let two runs read as 27
# (amendment 5). Then keying on the absolute path let the Windows->Linux migration double
# the count with zero new evidence. A decision's identity is (manifest, op, repo-relative
# path), not the machine it ran on.
from chat_agent import distinct_operations  # noqa: E402

WIN = (r"2026-08-14T15:00:16Z | DRY | manifest=m1.json | agent=chat/opus-5 | op=delete | "
       r"path=H:\My Drive\AI\workspace\inbox\note.md | REFUSE | rule=x")
NIX = ("2026-08-18T15:00:21Z | DRY | manifest=m1.json | agent=chat/opus-5 | op=delete | "
       "path=/home/user/ai-workspace/workspace/inbox/note.md | REFUSE | rule=x")
OTHER = ("2026-08-18T15:00:21Z | DRY | manifest=m2.json | agent=chat/opus-5 | op=append | "
         "path=/home/user/ai-workspace/docs/NOTE.md | ALLOW")
PROBE = ("2026-08-18T15:00:21Z | DRY PROBE | manifest=p.json | agent=chat/opus-5 | "
         "op=delete | path=/etc/passwd | REFUSE | rule=path-not-relative")
RUN = "2026-08-18T15:00:21Z | DRY | RUN | manifests=3 | decided=14 | refused=13"

check("same judgement on two machines counts ONCE", len(distinct_operations([WIN, NIX])), 1)
check("a different judgement still counts", len(distinct_operations([WIN, NIX, OTHER])), 2)
check("probes are excluded from the floor", len(distinct_operations([WIN, PROBE])), 1)
check("heartbeats are not operations", len(distinct_operations([RUN])), 0)
check("re-running a manifest does not inflate", len(distinct_operations([NIX, NIX, NIX])), 1)
# This asserted `== 2` against the LIVE agent log, which grows every time the agent runs.
# It passed on the day it was written and failed from the moment real traffic arrived --
# by 2026-08-25 the log held 9 distinct judgements and the suite had a permanent red line
# in it. A test that fails for the correct behaviour of the system it watches teaches
# people to ignore the suite. The property worth asserting is DEDUPLICATION, so assert
# that against the live log without pinning the absolute number.
_real = open("/home/user/ai-workspace/workspace/chat-agent-log.txt").read().split("\n")
_judged = [l for l in _real if "| DRY |" in l or "| ARMED |" in l]
check("the real log deduplicates rather than counting raw lines",
      len(distinct_operations(_real)) <= len(_judged), True)
check("and it yields at least the two judgements it started with",
      len(distinct_operations(_real)) >= 2, True)

print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
