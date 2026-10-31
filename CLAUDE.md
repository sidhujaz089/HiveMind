# CLAUDE.md: working on HiveMind's harness tasks

This repository is an EXPORT of the build harness from a private workspace. Finished work is reviewed there and merged
into the real repository by its own reviewer. Your branch is the deliverable.

## What is here

- `scripts/`: the harness. The dispatcher, scoping and bridge, supervision, review, gates and checks.
- `scripts/tests/`: plain Python suites. Run one with `python3 scripts/tests/test_x.py`; exit 0 is a pass.
- `projects/local-inference/coder.py`, `infer.py`, `settings.py`, `role_caps.py`, `models/`: the builder, and the
  model and flow configuration it reads.
- `tasks/`: one file per task, plus `tasks/INDEX.md`. Each starts with its ORACLE.

The live services (local models on ports 8000/11434, systemd units, a second machine) are NOT here. A suite that
needs them will fail here whatever you do. Tasks whose oracle needs them were left out of `tasks/`.

## Order

Start with `tasks/MECHANICAL-FIXES.md` (mf1 to mf8, in the order it gives), then the tasks in `tasks/INDEX.md`.
For an mf item, the branch is `cloud/mfN` and the task file is that section.

## How to do a task

1. **One task, one branch: `cloud/tNNN`**, from `main`. Never more than one task per branch.
2. **Run the oracle first and record that it is RED, with its output.** If it is already green, stop and say so in
   `tasks/tNNN.md`'s result section. Do not "fix" a passing task.
3. **If the task has no oracle, write it first:** `scripts/tests/test_tNNN_<what>.py`, red for the reason the task
   states, before you change any code.
4. **Make the smallest change that turns the oracle green.** Change only what the task names. Do not edit an existing
   test except the task's own new oracle. Do not raise or loosen a timeout, a ceiling or a gate to make something pass.
5. **Run the oracle, plus every suite that imports the module you changed** (`grep -l "import <module>"
   scripts/tests/*.py`). Report the before and after for each one. A suite that was red before you touched anything is
   not yours, so name it and leave it.
6. **Commit** with a message that says what was wrong, what changed, and the oracle's red then green output. End it
   with `Agent: claude-code-cloud/<model>`.
7. **Push the branch. Do not merge to `main`.**

## Rules that cost the most when broken

- Claim only what you ran. Quote the command and its last line.
- A test that checks source TEXT instead of behaviour is not an oracle. Run the code.
- `BUILD SUCCESSFUL` or a green run that executed nothing is not a pass. Check that the checks ran.
- No new top-level dependencies. Standard library plus what is already imported.
- Never put a secret, an address or a personal name in a commit.
