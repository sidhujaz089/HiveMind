# Mechanical fixes: stop the mistakes reviews keep catching

Eight fixes, each a check that stops a defect seen in review the week of 2026-10-27, whoever writes the code.
Treat each as a task: branch `cloud/mfN`, oracle red first, then green, logged in PROGRESS.md as `mfN | ...`.
Do them in this order: **mf1, mf3**, then **mf2, mf5**, then **mf4, mf6, mf7, mf8**.

mf2, mf3 and parts of mf4 touch `scripts/hooks/` or role_gate's six guarded files. Those will need two signatures
when they are merged back. Build them the same way as the others, and say in the commit which guarded files changed.

## mf1. A test must run the code, not read it
Seen: test_gate_timeout_message passed while the hook could not read its setting. It asserted strings ('echo 1800')
in the hook's text, and one check could never fail: 'rc -eq 1' is a substring of 'rc -eq 124'.
(a) merge_reviewed requires `--oracle` (a suite that fails on main and passes merged) for any change to role_gate's
six guarded files. It already supports --oracle; make it mandatory there.
(b) A lint flags tests that open a file under scripts/ or scripts/hooks/ and assert `"..." in text`. It allows one
only with a `# text-assert: <reason>` marker. Report the current offenders; do not rewrite them.

## mf2. No silent fallback, and no call to a subcommand that does not exist
Seen: the hook called `gate_settings.py get` and `record` before they existed. `|| echo 1800` and `|| true` hid the
argparse exit 2, so the setting was never read and no run was recorded.
A pre-commit lint over scripts/hooks/ and *.sh:
(a) refuses `2>/dev/null || echo <default>` and `|| true` on a command whose output is used, unless the fallback
prints a NOTE;
(b) for every `python3 scripts/X.py <word>`, checks that `<word>` is one of X's argparse choices.

## mf3. One rule, one place
Seen: the Lane-trailer pattern lived in commit-msg and in role_gate.LANE_RE with different length caps. Main now
shares the cap, but there are still two copies.
commit-msg calls `python3 scripts/role_gate.py check-lane` instead of its own grep. Add a lint that flags the same
regex literal appearing in two files under scripts/.

## mf4. A state path cannot be renamed without a migration
Seen: the scoping-queue rename moved the code but not the live directory. write_grant() also created directories
with mkdir(parents=True), which raced the migration (main has fixed write_grant itself).
(a) If a diff changes a path literal under workspace/run-logs/ or ~/.cache/workspace/, and the OLD path exists with
writes in the last 24 hours, pre-commit refuses unless the branch adds a migration script or a test that starts
from the old layout. (Test this with a temporary directory; the live paths do not exist here.)
(b) Find other writers of run-time state that create their parent directory. Make each one report a missing
directory instead of creating it.

## mf5. Do not track files that services write
Seen: gate-runs.jsonl was committed while the hook appends to it on every run, so no merge could land.
Add a registry, workspace/runtime-files.json, of files written at run time. Pre-commit refuses to stage them, and
check_workspace verifies that each one is gitignored.

## mf6. Registries are generated, not hand-merged
Seen: a branch could not merge because of conflicts in FLOW.json, FLOW-CHANGELOG.jsonl and SCRIPTS.md alone.
merge_reviewed regenerates FLOW.json, library.json and SCRIPTS.md from their sources after the merge, and a branch
does not commit them. FLOW-CHANGELOG.jsonl has sequential `n` values, so renumber on merge rather than conflict.

## mf7. The board stamps who posted
Seen: a post from one lane was recorded under another, because lanes.py takes the lane as an argument.
lanes.py takes the lane from the poster's identity (an env var each harness sets at launch, else tool and host),
and refuses a lane argument that disagrees.

## mf8. Check before claiming
lanes.py refuses an ANSWER or SIGNATURE post that does not quote a command and its output (a line starting with
`$ `). Before a post asks for a signature, lanes.py shows any answered question on the same branch.
