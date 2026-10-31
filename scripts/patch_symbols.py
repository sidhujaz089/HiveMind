#!/usr/bin/env python3
"""The SYMBOLS a patch touches -- the input A8's classifier needs and never had (2026-10-30).

`oracle_conflict.classify()` takes the target suite, the collateral suites and **the patch's own symbols**. coder's
ops carry PATHS, so nothing could call it, and it has been merged-but-unwired since `dd1badeef` for exactly that
reason. This is the missing input.

    python3 scripts/patch_symbols.py                      # the working tree against HEAD
    python3 scripts/patch_symbols.py --path scripts/x.py  # only the diff for those paths

WHY THE DIFF AND NOT THE FILE. A symbol the patch did not touch must not appear: in a 3000-line module every function
would become a candidate, and the classifier would start reporting "conflicts" between the patch and code it never
went near. Definitions only -- `def`, `class`, an assignment at the left margin -- because a bare call is not a
definition, and treating one as a symbol is how a comparison gets evidence it does not have.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# A DEFINITION on an added or removed line. `def`, `class`, and a module-level assignment (`NAME =`), which is the
# shape a cap or a constant takes -- t1432's MAX_TOKENS_FIRST is exactly that.
_DEF = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)")
_CLASS = re.compile(r"^\s*class\s+([A-Za-z_]\w*)")
_CONST = re.compile(r"^([A-Z][A-Z0-9_]{1,})\s*(?::[^=]+)?=")


def from_diff(diff_text):
    """{symbol} defined on a line the diff ADDS or REMOVES."""
    out = set()
    for line in (diff_text or "").splitlines():
        if not line or line[0] not in "+-":
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue                        # file headers, not code
        body = line[1:]
        for rx in (_DEF, _CLASS, _CONST):
            m = rx.match(body)
            if m:
                out.add(m.group(1))
    return out


def working_tree(repo=None, paths=None, rev="HEAD"):
    """{symbol} the working tree changes against `rev`, for `paths` when given. Empty when git cannot answer."""
    repo = Path(repo or REPO)
    args = ["git", "-C", str(repo), "diff", "--no-color", rev]
    if paths:
        args += ["--", *paths]
    try:
        r = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError) as e:              # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: could not read the working tree's diff ({type(e).__name__}: {e}) -- no symbols", file=sys.stderr)
        return set()
    if r.returncode:
        print(f"  NOTE: git diff failed (rc={r.returncode}): {r.stderr.strip()[:200]}", file=sys.stderr)
        return set()
    return from_diff(r.stdout)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--path", action="append", default=[], help="restrict to these paths (repeatable)")
    ap.add_argument("--repo", default=None)
    a = ap.parse_args(argv)
    syms = sorted(working_tree(a.repo, a.path or None))
    if not syms:
        print("  no symbols defined in this diff")
        return 0
    print(f"  {len(syms)} symbol(s) the patch defines:")
    for s in syms:
        print(f"    {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
