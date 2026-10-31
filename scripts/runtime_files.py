#!/usr/bin/env python3
"""mf5: do not track files that services write.

Seen: workspace/gate-runs.jsonl was committed while the hook appends to it on every run,
so no merge could land -- every branch conflicted on a file no human edits.

The registry, workspace/runtime-files.json, names every file written at run time
(an entry ending in '/' covers everything under that directory). Three consumers:
  * scripts/hooks/pre-commit refuses to stage a registered file (check-staged);
  * check_workspace verifies each entry is gitignored and untracked (problems());
  * a human reads the registry to learn what must never be committed.

CLI:  python3 scripts/runtime_files.py check-staged <path>...   exit 1 when any is registered
      python3 scripts/runtime_files.py verify                   exit 1 when any entry is tracked
                                                                or not gitignored
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REGISTRY = "workspace/runtime-files.json"


def registered(root=REPO):
    """The registry's entries, or [] when the registry does not exist (and a note saying so)."""
    p = Path(root) / REGISTRY
    if not p.is_file():
        print(f"  NOTE: {REGISTRY} is missing -- no runtime files are registered", file=sys.stderr)
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    return list(data.get("files", []))


def _covers(entry, path):
    return path == entry or (entry.endswith("/") and path.startswith(entry))


def staged_runtime(paths, root=REPO):
    """The subset of `paths` that the registry covers -- what pre-commit refuses to stage."""
    reg = registered(root)
    return [p for p in paths if any(_covers(e, p.replace("\\", "/")) for e in reg)]


def _git(root, *args):
    r = subprocess.run(["git", "-C", str(root), *args],
                       capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout.strip()


def problems(root=REPO):
    """[(entry, why)] for every registry entry that is tracked or not gitignored."""
    out = []
    for e in registered(root):
        probe = e + "x-runtime-probe" if e.endswith("/") else e
        rc, tracked = _git(root, "ls-files", "--", e.rstrip("/") + ("/" if e.endswith("/") else ""))
        if rc == 0 and tracked:
            out.append((e, "TRACKED by git -- a service writes it, so no merge can land: "
                           + tracked.splitlines()[0]
                           + (f" (+{len(tracked.splitlines()) - 1} more)"
                              if len(tracked.splitlines()) > 1 else "")))
            continue
        rc, _ = _git(root, "check-ignore", "-q", probe)
        if rc != 0:
            out.append((e, "not gitignored -- the next 'git add -A' commits it"))
    return out


def main(argv=None):
    args = argv if argv is not None else sys.argv[1:]
    if args and args[0] == "check-staged":
        hits = staged_runtime(args[1:])
        for p in hits:
            print(f"  {p}: registered as a RUNTIME file ({REGISTRY}) -- services write it; "
                  f"it is never committed")
        return 1 if hits else 0
    if args and args[0] == "verify":
        probs = problems()
        for e, why in probs:
            print(f"  {e}: {why}")
        print(f"{len(probs)} runtime-file problem(s)" if probs
              else "clean: every registered runtime file is gitignored and untracked")
        return 1 if probs else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
