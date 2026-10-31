#!/usr/bin/env python3
"""mf2 oracle: no silent fallback, and no call to a subcommand that does not exist.

Seen: scripts/hooks/ called `gate_settings.py get` and `record` before those subcommands
existed. `2>/dev/null || echo 1800` and `|| true` hid argparse's exit 2, so the setting was
never read, no run was recorded, and nothing said so.

Pinned as behaviour on scripts/hook_shell_lint.py, run against fixture trees in a temp dir:
(a) a command substitution whose fallback swallows the failure -- `$(cmd 2>/dev/null ||
    echo <default>)` or `$(cmd || true)` -- is flagged, unless the fallback prints a NOTE;
    a bare `cmd || true` whose output is NOT used stays legal.
(b) `python3 scripts/X.py <word>` is flagged when <word> is not one of X's subcommands
    (argparse add_parser names, choices=, or `argv[1] == "<w>"` comparisons).
Plus the wiring: scripts/hooks/pre-commit runs the lint on staged hook/sh files.

RED before the fix: scripts/hook_shell_lint.py does not exist and pre-commit never lints
its own shell.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

FAILS = []


def check(label, cond, detail=""):
    line = "  [%s] %s" % ("ok" if cond else "BAD", label)
    if not cond and detail:
        line += " -- " + detail
    print(line)
    if not cond:
        FAILS.append(label)


ARGPARSE_TOOL = '''#!/usr/bin/env python3
import argparse, sys
def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("get")
    sub.add_parser("record")
    ap.parse_args()
    return 0
if __name__ == "__main__":
    sys.exit(main())
'''

ARGV_TOOL = '''#!/usr/bin/env python3
import sys
def main(argv):
    if len(argv) >= 2 and argv[1] == "who":
        return 0
    if len(argv) >= 2 and argv[1] == "check-lane":
        return 0
    return 2
if __name__ == "__main__":
    sys.exit(main(sys.argv))
'''


def main():
    print("mf2 oracle: silent fallbacks and phantom subcommands are refused\n")
    try:
        import hook_shell_lint
    except ImportError as e:
        check("scripts/hook_shell_lint.py exists and imports", False, str(e))
        print("\n%d failed" % len(FAILS))
        return 1
    check("scripts/hook_shell_lint.py exists and imports", True)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "scripts" / "hooks").mkdir(parents=True)
        (root / "scripts" / "gate_settings.py").write_text(ARGPARSE_TOOL, encoding="utf-8")
        (root / "scripts" / "role_gate.py").write_text(ARGV_TOOL, encoding="utf-8")

        hook = root / "scripts" / "hooks" / "pre-push"
        hook.write_text(
            '#!/bin/sh\n'
            'CAP=$(python3 scripts/gate_settings.py get 2>/dev/null || echo 1800)\n'       # (a) silent
            'OUT=$(python3 scripts/gate_settings.py record || true)\n'                      # (a) silent
            'SAFE=$(python3 scripts/gate_settings.py get 2>/dev/null'
            ' || { echo "NOTE: gate_settings get failed; defaulting to 1800" >&2; echo 1800; })\n'  # noted
            'python3 scripts/gate_settings.py record || true\n'                             # output unused
            'python3 scripts/gate_settings.py fetch "$1"\n'                                 # (b) phantom
            'python3 scripts/role_gate.py who "x"\n'                                        # (b) real
            'python3 scripts/role_gate.py lane-owns "$1"\n',                                # (b) phantom
            encoding="utf-8")

        hits = hook_shell_lint.offenders(root)
        kinds = [(h[2], h[1]) for h in hits]  # (kind, line)
        silents = [h for h in hits if h[2] == "silent-fallback"]
        phantoms = [h for h in hits if h[2] == "phantom-subcommand"]

        check("a '2>/dev/null || echo <default>' inside $() is flagged",
              any(h[1] == 2 for h in silents), "hits: %r" % (kinds,))
        check("a '|| true' inside $() is flagged", any(h[1] == 3 for h in silents),
              "hits: %r" % (kinds,))
        check("a fallback that prints a NOTE is allowed", not any(h[1] == 4 for h in hits),
              "line 4 flagged: %r" % (kinds,))
        check("a bare '|| true' whose output is unused is allowed",
              not any(h[1] == 5 for h in hits), "line 5 flagged: %r" % (kinds,))
        check("a call to a subcommand argparse does not define is flagged ('fetch')",
              any(h[1] == 6 for h in phantoms), "hits: %r" % (kinds,))
        check("a real argparse subcommand passes ('who' via argv-style too)",
              not any(h[1] == 7 for h in hits), "line 7 flagged: %r" % (kinds,))
        check("an argv-compared script's missing word is flagged ('lane-owns')",
              any(h[1] == 8 for h in phantoms), "hits: %r" % (kinds,))
        check("every finding names the file, the line and a reason",
              all(len(h) >= 4 and h[0] and h[1] and h[3] for h in hits),
              "hits: %r" % (hits,))

        # Fix line 6 and 8, drop the two silents: the lint must go clean.
        hook.write_text(
            '#!/bin/sh\n'
            'CAP=$(python3 scripts/gate_settings.py get 2>/dev/null'
            ' || { echo "NOTE: no setting; 1800" >&2; echo 1800; })\n'
            'python3 scripts/gate_settings.py record\n'
            'python3 scripts/role_gate.py who "x"\n', encoding="utf-8")
        check("a clean hook produces no findings", hook_shell_lint.offenders(root) == [],
              "%r" % (hook_shell_lint.offenders(root),))

    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "hook_shell_lint.py")],
                       capture_output=True, text=True, timeout=300, cwd=str(ROOT))
    check("the CLI runs over the live repo (report, exit 0 or 1)", r.returncode in (0, 1),
          "rc=%d: %s" % (r.returncode, (r.stdout + r.stderr)[-200:]))

    pc = (ROOT / "scripts" / "hooks" / "pre-commit").read_text(encoding="utf-8")
    check("pre-commit is wired to run the lint on staged shell",
          "hook_shell_lint" in pc)  # text-assert: the hook is shell; the wiring line is the only mechanical pin available

    n = len(FAILS)
    print("\n%d failed -- %s" % (n, "silent fallbacks and phantom subcommands are refused"
                                 if not n else "mf2 is not in place"))
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
