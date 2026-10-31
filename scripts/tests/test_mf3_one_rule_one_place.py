#!/usr/bin/env python3
"""mf3 oracle: one rule, one place -- the Lane trailer's shape lives in role_gate alone.

Seen: the Lane-trailer pattern lived in scripts/hooks/commit-msg's own grep AND in
role_gate.LANE_RE, once with different length caps. Main shares the cap now, but the rule
still has two copies: editing LANE_RE does not change what the hook accepts.

Pinned as behaviour:
(1) `python3 scripts/role_gate.py check-lane <msg>` validates the trailer's FORM from
    LANE_RE: absent trailer or valid label -> 0; over-cap or bad charset -> 1, saying why.
(2) The hook DELEGATES: in a fixture git repo whose scripts/role_gate.py is a stub that
    refuses the (shape-valid) label STUBBED-REFUSE, the real commit-msg hook must refuse it
    too. On main the hook's own grep accepts it, so this is the red that proves two copies.
(3) scripts/regex_dup_lint.py flags the same regex literal appearing in two files under
    scripts/ (re.compile strings and grep -E patterns), reporting, never rewriting.

RED on main: check-lane does not exist (role_gate exits 2), the hook keeps its own grep,
and there is no duplicate-regex lint.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FAILS = []


def check(label, cond, detail=""):
    line = "  [%s] %s" % ("ok" if cond else "BAD", label)
    if not cond and detail:
        line += " -- " + detail
    print(line)
    if not cond:
        FAILS.append(label)


def run(args, cwd=ROOT):
    return subprocess.run(args, capture_output=True, text=True, timeout=120, cwd=str(cwd))


def msgfile(td, text):
    p = Path(td) / f"msg-{abs(hash(text)) % 10_000}.txt"
    p.write_text(text, encoding="utf-8")
    return str(p)


HEAD = "test(workspace): mf3 probe\n\nAgent: claude-code/opus-5\n"

STUB = '''#!/usr/bin/env python3
import sys
def main(argv):
    if len(argv) >= 3 and argv[1] == "check-lane":
        text = open(argv[2], encoding="utf-8").read()
        if "Lane: STUBBED-REFUSE" in text:
            print("stub: refused by check-lane")
            return 1
        return 0
    return 0          # commit-msg, lane-owns: the stub allows everything else
if __name__ == "__main__":
    sys.exit(main(sys.argv))
'''


def main():
    print("mf3 oracle: the Lane rule lives in role_gate alone\n")

    with tempfile.TemporaryDirectory() as td:
        ok = msgfile(td, HEAD + "Lane: L1\n")
        r = run([sys.executable, "scripts/role_gate.py", "check-lane", ok])
        check("check-lane accepts a valid label (exit 0)", r.returncode == 0,
              f"rc={r.returncode}: {(r.stdout + r.stderr)[:120]}")
        none = msgfile(td, HEAD)
        r = run([sys.executable, "scripts/role_gate.py", "check-lane", none])
        check("check-lane accepts a message with NO Lane trailer (it is optional)",
              r.returncode == 0, f"rc={r.returncode}")
        cap24 = msgfile(td, HEAD + "Lane: " + "a" * 24 + "\n")
        r = run([sys.executable, "scripts/role_gate.py", "check-lane", cap24])
        check("check-lane accepts a 24-character label (the cap itself)", r.returncode == 0,
              f"rc={r.returncode}")
        cap25 = msgfile(td, HEAD + "Lane: " + "a" * 25 + "\n")
        r = run([sys.executable, "scripts/role_gate.py", "check-lane", cap25])
        check("check-lane refuses a 25-character label", r.returncode == 1,
              f"rc={r.returncode}")
        check("  ...and the refusal names the cap", "24" in (r.stdout + r.stderr),
              repr((r.stdout + r.stderr)[:120]))
        bad = msgfile(td, HEAD + "Lane: has space\n")
        r = run([sys.executable, "scripts/role_gate.py", "check-lane", bad])
        check("check-lane refuses a label with a space", r.returncode == 1,
              f"rc={r.returncode}")

        # (2) THE HOOK DELEGATES. A stub role_gate that refuses a shape-valid label must make
        # the hook refuse it: the hook's own grep would pass it, which is the two-copies bug.
        fx = Path(td) / "fixture"
        (fx / "scripts" / "hooks").mkdir(parents=True)
        env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
        subprocess.run(["git", "init", "-q", str(fx)], capture_output=True, text=True,
                       timeout=60, env=env)
        (fx / "scripts" / "role_gate.py").write_text(STUB, encoding="utf-8")
        hook = fx / "scripts" / "hooks" / "commit-msg"
        hook.write_text((ROOT / "scripts" / "hooks" / "commit-msg")
                        .read_text(encoding="utf-8"), encoding="utf-8")
        hook.chmod(0o755)
        probe = msgfile(td, HEAD + "Lane: STUBBED-REFUSE\n")
        r = subprocess.run(["sh", str(hook), probe], capture_output=True, text=True,
                           timeout=120, cwd=str(fx), env=env)
        check("the hook delegates the Lane form to role_gate (stub's refusal is honoured)",
              r.returncode == 1,
              "rc=%d -- the hook still uses its own grep, so the rule has two copies"
              % r.returncode)
        probe_ok = msgfile(td, HEAD + "Lane: L2\n")
        r = subprocess.run(["sh", str(hook), probe_ok], capture_output=True, text=True,
                           timeout=120, cwd=str(fx), env=env)
        check("  ...and a label the stub allows still passes the hook", r.returncode == 0,
              f"rc={r.returncode}: {(r.stdout + r.stderr)[:160]}")

        # (3) The duplicate-regex lint.
        try:
            sys.path.insert(0, str(ROOT / "scripts"))
            import regex_dup_lint
        except ImportError as e:
            check("scripts/regex_dup_lint.py exists and imports", False, str(e))
        else:
            check("scripts/regex_dup_lint.py exists and imports", True)
            lr = Path(td) / "lintroot" / "scripts"
            (lr / "hooks").mkdir(parents=True)
            (lr / "a.py").write_text(
                'import re\nPAT = re.compile(r"^Lane:\\s*([A-Za-z0-9_.-]{1,24})$")\n',
                encoding="utf-8")
            (lr / "b.py").write_text(
                'import re\nCOPY = re.compile(r"^Lane:\\s*([A-Za-z0-9_.-]{1,24})$")\n',
                encoding="utf-8")
            (lr / "c.py").write_text(
                'import re\nOTHER = re.compile(r"^Agent:\\s*(\\S+)$")\n', encoding="utf-8")
            dups = regex_dup_lint.duplicates(lr.parent)
            flat = [(Path(f).name, pat) for pat, files in dups for f in files]
            names = {n for n, _ in flat}
            check("the same re.compile literal in two files is flagged",
                  {"a.py", "b.py"} <= names, "got %r" % (dups,))
            check("a pattern appearing once is not flagged", "c.py" not in names,
                  "got %r" % (dups,))
            (lr / "hooks" / "commit-msg").write_text(
                "#!/bin/sh\ngrep -qE '^Agent:\\s*(\\S+)$' \"$1\"\n", encoding="utf-8")
            dups2 = regex_dup_lint.duplicates(lr.parent)
            pats = {pat for pat, _ in dups2}
            check("a grep -E pattern in a hook that copies a .py regex is flagged too",
                  any("Agent" in p for p in pats), "got %r" % (dups2,))
            r = run([sys.executable, "scripts/regex_dup_lint.py"])
            check("the CLI runs over the live repo (report, exit 0 or 1)",
                  r.returncode in (0, 1), f"rc={r.returncode}: {r.stderr[-160:]}")

    n = len(FAILS)
    print("\n%d failed -- %s" % (n, "the Lane rule has one home and duplicate regexes are linted"
                                 if not n else "the rule still lives in more than one place"))
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
