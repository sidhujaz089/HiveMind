#!/usr/bin/env python3
"""mf1 oracle: a test must run the code, not read it.

Seen in review (week of 2026-10-27): test_gate_timeout_message passed while the hook could
not read its setting -- it asserted strings ('echo 1800') in the hook's TEXT, and one check
could never fail ('rc -eq 1' is a substring of 'rc -eq 124').

Two halves, both pinned as BEHAVIOUR (functions are imported and run, never grepped):

(a) merge_reviewed.require_oracle_for_guarded(paths, oracle): a merge whose branch touches
    one of role_gate's six guarded files REFUSES without --oracle; with an oracle, or with
    no guarded file touched, it passes. build() must consult it.

(b) scripts/text_assert_lint.py: offenders(root) flags a test that reads a file under
    scripts/ (scripts/hooks/ included) and asserts a string literal `in` that text, unless
    the assertion line carries '# text-assert: <reason>'. Run against fixture trees in a
    temp dir -- the live offenders are REPORTED by the CLI, not rewritten.

RED before the fix: neither require_oracle_for_guarded nor text_assert_lint exists.
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


def refuses(fn, *args):
    """(refused, message) -- a refusal is SystemExit or a falsy ok from an (ok, why) pair."""
    try:
        out = fn(*args)
    except SystemExit as e:
        return True, str(e)
    if isinstance(out, tuple) and len(out) == 2:
        return (not out[0]), str(out[1])
    return False, repr(out)


def main():
    print("mf1 oracle: tests must run code, and guarded merges need an oracle\n")

    import merge_reviewed
    has_req = hasattr(merge_reviewed, "require_oracle_for_guarded")
    check("merge_reviewed.require_oracle_for_guarded exists", has_req,
          "a guarded merge can still land with no oracle")
    if has_req:
        f = merge_reviewed.require_oracle_for_guarded
        r, why = refuses(f, ["scripts/role_gate.py"], None)
        check("a guarded file with NO oracle is refused", r, "it passed")
        check("  ...and the refusal names --oracle", "--oracle" in why or "oracle" in why.lower(),
              repr(why)[:90])
        r, _ = refuses(f, ["scripts/hooks/commit-msg"], None)
        check("a file under scripts/hooks/ (directory guard) is refused too", r, "it passed")
        r, _ = refuses(f, ["scripts/role_gate.py"], "scripts/tests/test_x.py")
        check("a guarded file WITH an oracle passes", not r)
        r, _ = refuses(f, ["scripts/pysource.py", "docs/README.md"], None)
        check("no guarded file touched: no oracle required", not r)
        src = (ROOT / "scripts" / "merge_reviewed.py").read_text(encoding="utf-8")
        a = src.find("def build(")
        check("build() consults require_oracle_for_guarded (the live path, not a dead helper)",
              a >= 0 and "require_oracle_for_guarded(" in src[a:a + 4000],
              "no call inside build()")

    try:
        import text_assert_lint
        has_lint = True
    except ImportError as e:
        has_lint = False
        check("scripts/text_assert_lint.py exists and imports", False, str(e))
    if has_lint:
        check("scripts/text_assert_lint.py exists and imports", True)
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            tdir = td / "scripts" / "tests"
            tdir.mkdir(parents=True)
            (td / "scripts" / "hooks").mkdir()
            (td / "scripts" / "x.py").write_text("SETTING = 1800\n", encoding="utf-8")
            (td / "scripts" / "hooks" / "commit-msg").write_text("rc=124\n", encoding="utf-8")

            (tdir / "test_bad.py").write_text(
                'from pathlib import Path\n'
                'text = Path("scripts/x.py").read_text()\n'
                'assert "echo 1800" in text\n', encoding="utf-8")
            (tdir / "test_marked.py").write_text(
                'from pathlib import Path\n'
                'text = Path("scripts/hooks/commit-msg").read_text()\n'
                'assert "rc=124" in text  # text-assert: the hook is shell; rc flow needs a string pin\n',
                encoding="utf-8")
            (tdir / "test_clean.py").write_text(
                'import subprocess, sys\n'
                'r = subprocess.run([sys.executable, "-c", "print(1)"], capture_output=True,\n'
                '                   text=True, timeout=60)\n'
                'assert "1" in r.stdout\n', encoding="utf-8")

            hits = text_assert_lint.offenders(td)
            files = {Path(f).name for f, *_ in hits}
            check("a test asserting a literal in a scripts/ file's text is flagged",
                  "test_bad.py" in files, "offenders: %r" % (hits,))
            check("a '# text-assert: <reason>' marker exempts the assertion",
                  "test_marked.py" not in files, "offenders: %r" % (hits,))
            check("a test that RUNS the code and asserts on its output is not flagged",
                  "test_clean.py" not in files, "offenders: %r" % (hits,))
            bad = [h for h in hits if Path(h[0]).name == "test_bad.py"]
            check("the finding names the file and the line",
                  bad and isinstance(bad[0][1], int) and bad[0][1] == 3,
                  "got %r" % (bad,))

        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "text_assert_lint.py")],
                           capture_output=True, text=True, timeout=300, cwd=str(ROOT))
        check("the CLI runs over the live repo and exits 0 or 1 (report, not crash)",
              r.returncode in (0, 1),
              "rc=%d stderr=%s" % (r.returncode, r.stderr[-200:]))

    n = len(FAILS)
    print("\n%d failed -- %s" % (n, "text-asserts are gated and guarded merges need an oracle"
                                 if not n else "mf1 is not in place"))
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
