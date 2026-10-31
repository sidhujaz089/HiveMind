#!/usr/bin/env python3
"""Red fixture for scripts/check_workspace.py (t1047): the mutation-coverage check must be REPORTED.

RED ON MAIN FOR ONE REASON: the Checker class has no check_mutation_coverage method, so the
fixture prints that and exits 1 before touching any assertion.

WHY THE CHECKER IS IMPORTED IN-PROCESS AND NEVER RUN AS A SUBPROCESS. Every run of
scripts/check_workspace.py appends two rows: _record_history (called from run(), writing the
HISTORY path built from Path(__file__), NOT from --repo-root) and _record_tree_state (called
from check_git_state). A subprocess run of the real checker therefore dirties the worktree --
which is what the bridge refused last scope (running it changed
workspace/check-history.jsonl and workspace/ledgers/tree-state.jsonl). Here the module is
imported by path, every existing check method is patched to a no-op, both ledger writers are
patched out, and only the new check runs. Nothing is written anywhere, no subprocess is
started, and no process is left running.

Run:  python3 scripts/tests/test_check_workspace_mutation_coverage_wired.py
"""
import contextlib
import importlib.util
import io
import pathlib
import sys
import tempfile
import unittest.mock as mock

REPO = pathlib.Path(__file__).resolve().parents[2]
CHECKER = REPO / "scripts" / "check_workspace.py"
MUTATIONS = REPO / "scripts" / "mutate.py"
MODULE = "check_mutation_coverage.py"
TITLE = "Every check_workspace check is named by a mutation"
LEDGERS = [REPO / "workspace" / "check-history.jsonl", REPO / "workspace" / "ledgers" / "tree-state.jsonl"]
VERDICTS = ("PASS", "WARN", "FAIL")

FAILURES = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def load():
    spec = importlib.util.spec_from_file_location("cw_wired", CHECKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def quiet(fn):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        fn()
    return out.getvalue()


def rows(text):
    return [ln.strip() for ln in text.splitlines() if TITLE in ln]


def verdict_of(line):
    if not line.startswith("["):
        return ""
    end = line.find("]")
    if end < 0:
        return ""
    return line[1:end]


def titles_in(src):
    out = []
    i = 0
    key = "self.result("
    while True:
        j = src.find(key, i)
        if j < 0:
            return out
        seg = src[j + len(key):j + len(key) + 240]
        parts = seg.split('"')
        if len(parts) >= 4 and parts[1] in VERDICTS and parts[3].strip():
            out.append(parts[3])
        i = j + len(key)


def test_run_reports_the_new_check(cw):
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td)
        (repo / ".git").mkdir()
        c = cw.Checker(repo, quick=True)
        others = [n for n in dir(type(c)) if n.startswith("check_") and n != "check_mutation_coverage"]
        detail = "uncovered: Working tree is clean"
        codes = []
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(cw.Checker, "_record_history", lambda self: None))
            stack.enter_context(mock.patch.object(cw.Checker, "_record_tree_state",
                                                  lambda self, v, f: None))
            stack.enter_context(mock.patch.object(cw.Checker, "check_mutation_coverage",
                                                  lambda self: self.result("WARN", TITLE, detail)))
            for n in others:
                stack.enter_context(mock.patch.object(cw.Checker, n, lambda self, *a: None))

            def go():
                codes.append(c.run())

            text = quiet(go)
    found = rows(text)
    check(len(found) == 1, "run() prints the new check's title exactly once; found "
          + str(len(found)) + ": " + repr(found))
    if not found:
        return
    v = verdict_of(found[0])
    check(v in VERDICTS, "the row carries a verdict token a report can use, never SKIP and never blank: "
          + repr(found[0]))
    check(v == "WARN", "the verdict the check reports is the verdict the printed report carries: "
          + repr(found[0]))
    check(detail in found[0], "the detail the check reports reaches the printed row, so a reader can act on it: "
          + repr(found[0]))
    check(TITLE in found[0][found[0].find("]") + 1:],
          "the title follows the verdict token, the shape every other check prints")
    check(codes == [0], "a WARN from the new check does not fail the close: run() returned " + repr(codes))


def test_the_real_check_reports_the_real_state(cw):
    src = CHECKER.read_text(encoding="utf-8")
    mut = MUTATIONS.read_text(encoding="utf-8")
    c = cw.Checker(REPO, quick=True)
    text = quiet(c.check_mutation_coverage)
    found = rows(text)
    check(len(found) == 1, "called directly on the real repo it prints the title once; found "
          + str(len(found)) + ": " + repr(found))
    v = verdict_of(found[0]) if found else ""
    check(v in VERDICTS, "the real run's verdict token is one of PASS, WARN, FAIL: " + repr(found))
    row = found[0] if found else ""
    check(len(row) > len(TITLE) + 8,
          "the row carries a detail, not a bare verdict: " + repr(row))
    titles = titles_in(src)
    outside = [t.strip() for t in titles if t.strip() and t.strip() not in mut]
    if v == "PASS":
        check(not outside,
              "a PASS claims every check title is named by a mutation, but these real titles appear in "
              "no mutate.py text:")
        for t in outside:
            print("       " + t)
    else:
        named = [t for t in outside if t in row]
        check(bool(named) or MODULE in row,
              "a non-PASS verdict names the titles it cannot clear, or the module it could not read: "
              + repr(row))


def test_it_writes_nothing(cw):
    def snap():
        return [(p.exists(), p.stat().st_size if p.exists() else None) for p in LEDGERS]
    before = snap()
    c = cw.Checker(REPO, quick=True)
    quiet(c.check_mutation_coverage)
    check(before == snap(), "the live ledgers are byte-for-byte unchanged: " + repr((before, snap())))


def main():
    if not CHECKER.is_file():
        print("FAIL " + str(CHECKER) + " not found")
        sys.exit(1)
    try:
        cw = load()
    except Exception as e:
        print("FAIL scripts/check_workspace.py cannot be imported: " + type(e).__name__ + ": " + str(e))
        sys.exit(1)
    if not hasattr(cw.Checker, "check_mutation_coverage"):
        print("FAIL scripts/check_workspace.py defines no check_mutation_coverage method on Checker -- "
              "this is the red this fixture declares. Nothing below runs until it exists.")
        sys.exit(1)
    for fn in (test_run_reports_the_new_check, test_the_real_check_reports_the_real_state,
               test_it_writes_nothing):
        print("-- " + fn.__name__)
        fn(cw)
    print("")
    print(str(len(FAILURES)) + " failure(s)")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
