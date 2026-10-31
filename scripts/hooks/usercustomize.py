"""Runtime guard for the script library. Linked into this user's Python site-packages by
`python3 scripts/library.py install`; Python imports it before any script runs.

If the script about to run is a .py file inside the ai-workspace repository and is not in
scripts/library.json, refuse with exit 3. Everything else -- other directories, `python3 -c`,
`python3 -m`, the interactive prompt -- is untouched. Fails closed on an unreadable library,
except for scripts/library.py itself, so the library can always be repaired.

Kept small and dependency-free: it runs on EVERY Python start for this user.
"""
import json as _json
import os as _os
import sys as _sys


def _guard():
    argv0 = _sys.argv[0] if _sys.argv else ""
    if not argv0.endswith(".py") or argv0.startswith("-"):
        return
    repo = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.realpath(__file__))))
    path = _os.path.realpath(argv0)
    if not path.startswith(repo + _os.sep):
        return
    rel = path[len(repo) + 1:]
    # A GIT WORKTREE OF THIS REPO IS THE SAME REPO, SO MAP IT BACK RATHER THAN EXEMPT IT.
    # `repo` comes from this file's INSTALLED symlink, which points into the main checkout, so
    # a worktree under .claude/worktrees/<name>/ yields a rel of
    # ".claude/worktrees/<name>/scripts/x.py" -- which matches nothing in library.json, and
    # every script in every worktree was refused (found 2026-09-22, after three round trips
    # of exporting patches for a human to apply). Stripping the prefix is the fix rather than
    # adding ".claude" to the exclusions below: a worktree copy of a REGISTERED script is that
    # same script and should run, while an UNREGISTERED one must still be refused. Exempting
    # the directory would have made a worktree the one place the library gate does not apply.
    bits = rel.split(_os.sep)
    if bits[:2] == [".claude", "worktrees"] and len(bits) > 3:
        # ...and read the WORKTREE's library.json, not main's. Reading main's meant a script
        # registered on a branch could not run on that branch -- a new test was refused in the
        # very worktree that registered it, so it could not be shown failing before its fix
        # (found 2026-09-23). The worktree's registry is the one its commit will carry.
        # A tree with no registry of its own (not a real checkout) keeps main's.
        wt_root = _os.path.join(repo, *bits[:3])
        if _os.path.isfile(_os.path.join(wt_root, "scripts", "library.json")):
            repo = wt_root
        rel = _os.sep.join(bits[3:])
    parts = set(rel.split(_os.sep))
    if parts & {".git", ".venv", "node_modules", "__pycache__", "chat", "build", ".gradle"}:
        return
    if rel == _os.path.join("scripts", "library.py"):
        return
    try:
        with open(_os.path.join(repo, "scripts", "library.json"), encoding="utf-8") as f:
            registered = _json.load(f)["scripts"]
    except Exception as e:  # noqa: BLE001
        _sys.stderr.write(f"REFUSED: the script library is unreadable ({type(e).__name__}); "
                          f"no repository script runs until it is fixed.\n"
                          f"  python3 scripts/library.py verify\n")
        _os._exit(3)
    if rel not in registered and not _os.path.isfile(_os.path.join(repo, rel)):
        # NOT FOUND IS NOT "UNREGISTERED" (t481): a DSH model ran scripts/taskstore.py, was told
        # to register it, and never learned the real one is projects/app-repo/taskstore.py.
        base = _os.path.basename(rel)
        near = [r for r in registered if _os.path.basename(r) == base]
        _sys.stderr.write(f"REFUSED: {rel} does not exist."
                          + (f" Did you mean: {', '.join(sorted(near))}" if near else "") + "\n")
        _os._exit(3)
    if rel not in registered:
        _sys.stderr.write(f"REFUSED: {rel} is not registered in the script library "
                          f"(scripts/library.json), so it cannot run.\n"
                          f"  python3 scripts/library.py register {rel} \"what it does\"\n")
        _os._exit(3)


try:
    _guard()
except SystemExit:
    raise
except Exception as e:  # noqa: BLE001
    print(f"  NOTE: the script-library guard itself failed ({e}) -- this launch was NOT checked", file=_sys.stderr)
