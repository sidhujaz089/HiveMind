#!/usr/bin/env python3
"""Red fixture for scripts/check_mutation_coverage.py (t1047).

RED ON MAIN FOR ONE REASON: the file does not exist. Verified 2026-10-11:
  ls scripts/check_mutation_coverage.py  ->  No such file
So this suite prints that sentence and exits 1. It does not crash and it does not
invent the module.

The one sentence this module exists for: a check_workspace check is only trusted when a
mutation names it -- so the tool that decides that must refuse a title it did not see, must
refuse a near-miss, and must never report clean when it read nothing.

Run:  python3 scripts/tests/test_mutation_coverage.py
"""
import contextlib
import importlib.util
import io
import json
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
MOD = REPO / "scripts" / "check_mutation_coverage.py"
CHECKS = REPO / "scripts" / "check_workspace.py"
MUTATIONS = REPO / "scripts" / "mutate.py"
API = ("check_titles", "mutation_texts", "covered", "uncovered", "load_allowlist", "main")

FAILURES = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def load():
    spec = importlib.util.spec_from_file_location("check_mutation_coverage", MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


STUB_CHECKS = '''
import json


class Checker:
    def check_a(self):
        self.result("PASS", "Working tree is clean")
        self.result("FAIL", "Working tree is clean and staged", "detail")
        self.result("WARN", "No secret pattern in tracked files", "x")
        self.result("SKIP", "A check skipped in quick mode", "x")
        self.result("PASS", f"Session review is moving ({self.line})")
        self.result("PASS", variable_title)
        report("PASS", "Not a check -- not self.result")

    def check_b(self):
        name = "manifest op vocabulary matches (ALLOWED_OPS vs README)"
        self.result("PASS", name, f"both list the same {n} ops")

    def check_c(self):
        self.result("PASS", f"{fn.__name__} ran to completion", "a crash is a FAIL")

    def check_d(self):
        self.result("PASS", "Working tree is cleanest on this branch", "x")
'''

STUB_MUTATES = '''
MUTATIONS = [
    ("secret-scan-cannot-read", "scripts/check_workspace.py",
     "        elif unreadable:", "        elif False:",
     "scripts/tests/test_check_workspace.py",
     "reporting 'No secret pattern in tracked files' when git grep could not READ a tracked file"),
    ("working-tree-ignores-staged", "scripts/check_workspace.py",
     "if x:", "if False:",
     "scripts/tests/test_check_workspace.py",
     "reporting 'Working tree is clean and staged' while a staged file is left behind"),
    ("names-a-longer-title", "scripts/other.py", "if y:", "if False:",
     "scripts/tests/test_other.py",
     "reporting Working tree is cleanest on this branch is fine"),
    ("names-the-short-title", "scripts/other.py", "if z:", "if False:",
     "scripts/tests/test_other.py",
     "reporting 'Working tree is clean' in prose about a different file"),
]
'''

ADD_CHECK = '''    def check_a2(self):
        self.result("WARN", "A brand new check nobody can break", "x")

'''

NEW_ENTRY = '''    ("new-check-has-no-falsifier", "scripts/check_workspace.py", "if q:", "if False:",
     "scripts/tests/test_x.py",
     "leaving A brand new check nobody can break unbreakable"),
'''

BLANK = '''def nothing():
    pass
'''


def test_api(mod):
    missing = [n for n in API if not callable(getattr(mod, n, None))]
    check(not missing, "the module exposes " + ", ".join(API) + "; missing: " + ", ".join(missing))


def test_titles(mod):
    titles = mod.check_titles(STUB_CHECKS)
    want = {"Working tree is clean", "Working tree is clean and staged",
            "No secret pattern in tracked files", "Session review is moving",
            "manifest op vocabulary matches (ALLOWED_OPS vs README)",
            "Working tree is cleanest on this branch"}
    got = set(titles)
    check(want <= got, "a titled self.result in the stub yields its title verbatim; missing: "
          + ", ".join(sorted(want - got)))
    check("A check skipped in quick mode" not in got,
          "a self.result with verdict SKIP is not counted -- SKIP is the checked-nothing shape")
    check("Not a check -- not self.result" not in got,
          "a titled report() call is not a check: only self.result titles count")
    check(all(t.strip() == t for t in titles),
          "titles are normalised, no leading or trailing space: " + repr(titles))
    check("Session review is moving" in got,
          "an f-string yields its leading literal, not a blank")
    check("manifest op vocabulary matches (ALLOWED_OPS vs README)" in got,
          "a title built in a local variable is resolved from its assignment")


def test_mutation_texts(mod):
    texts = mod.mutation_texts(STUB_MUTATES)
    check(len(texts) == 4, "one text per MUTATIONS entry: found " + str(len(texts)))
    joined = chr(10).join(texts)
    check("secret-scan-cannot-read" in joined,
          "a text carries the entry id, so a reader can find it in mutate.py")
    check("reporting 'No secret pattern in tracked files' when git grep could not READ a tracked file" in joined,
          "a text carries the entry's what prose verbatim")
    check("scripts/check_workspace.py" in joined, "a text carries the entry's target file")
    check("scripts/tests/test_check_workspace.py" in joined, "a text carries the entry's suite")


def test_coverage_rules(mod):
    titles = mod.check_titles(STUB_CHECKS)
    texts = mod.mutation_texts(STUB_MUTATES)
    got = mod.uncovered(titles, texts)
    want = {"Session review is moving", "manifest op vocabulary matches (ALLOWED_OPS vs README)"}
    check(got == want, "exactly the stub titles that no entry names are uncovered: " + repr(sorted(got)))
    check(mod.covered("No secret pattern in tracked files", texts, titles),
          "an entry that names a title in its what prose clears it")
    staged = [t for t in texts if "staged" in t]
    check(mod.covered("Working tree is clean and staged", staged, titles),
          "the entry naming the longer title clears that title")
    check(not mod.covered("Working tree is clean", staged, titles),
          "and does NOT clear the shorter title it contains: a prefix is not a name")
    cleanest = [t for t in texts if "cleanest" in t]
    check(mod.covered("Working tree is cleanest on this branch", cleanest, titles),
          "the entry naming that title clears it")
    check(not mod.covered("Working tree is clean", cleanest, titles),
          "and not the title that is its prefix")
    check(mod.uncovered(titles, []) == set(titles),
          "no mutations at all leaves every title uncovered: reading nothing is never clean")


def test_add_and_remove(mod):
    titles = mod.check_titles(STUB_CHECKS)
    texts = mod.mutation_texts(STUB_MUTATES)
    new = "A brand new check nobody can break"
    added = STUB_CHECKS.replace("    def check_b(self):", ADD_CHECK + "    def check_b(self):")
    t2 = mod.check_titles(added)
    check(set(t2) - set(titles) == {new},
          "adding a titled check to the file makes it uncovered, and changes no other title")
    check(new in mod.uncovered(t2, texts), "with no mutation naming it, the new check is uncovered (red)")
    mut_added = STUB_MUTATES.replace("MUTATIONS = [" + chr(10),
                                     "MUTATIONS = [" + chr(10) + NEW_ENTRY)
    x2 = mod.mutation_texts(mut_added)
    check(len(x2) == 5, "the new MUTATIONS entry is read as a fifth text: " + str(len(x2)))
    check(mod.uncovered(t2, x2) == mod.uncovered(titles, texts),
          "adding the mutation that names it clears ONLY the new title (green)")
    drop = [t for t in texts if "No secret pattern in tracked files" not in t]
    check("No secret pattern in tracked files" in mod.uncovered(titles, drop),
          "deleting the mutation that named a title makes that title uncovered again")


def test_real_artefacts(mod):
    ck = CHECKS.read_text(encoding="utf-8")
    mt = MUTATIONS.read_text(encoding="utf-8")
    titles = mod.check_titles(ck)
    texts = mod.mutation_texts(mt)
    check(len(titles) >= 50, "the real scripts/check_workspace.py yields " + str(len(titles))
          + " check titles (a reader counted 102 unique titled self.result calls)")
    for t in ("Working tree is clean", "No secret pattern in tracked files",
              "manifest op vocabulary matches (ALLOWED_OPS vs README)"):
        check(t in titles, "the real title " + repr(t) + " is enumerated from the real check_workspace.py")
    check(len(texts) >= 50, "the real scripts/mutate.py yields " + str(len(texts))
          + " mutation texts (a reader counted 101 entries)")
    check(any("No secret pattern in tracked files" in x for x in texts),
          "a real mutate.py entry names a real check title, so the rule is satisfiable on today's data")
    check(not mod.covered("Working tree is clean", texts, titles),
          "on the real files a mutation naming 'Working tree is clean and staged' does not clear 'Working tree is clean'")


def test_cli(mod):
    ck = CHECKS.read_text(encoding="utf-8")
    mt = MUTATIONS.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td)
        cpath = d / "check_workspace.py"
        cpath.write_text(ck, encoding="utf-8")
        mpath = d / "mutate.py"
        mpath.write_text(mt, encoding="utf-8")
        titles = mod.check_titles(ck)
        full = d / "all.json"
        full.write_text(json.dumps(sorted(titles)), encoding="utf-8")
        none = d / "none.json"
        none.write_text("[]", encoding="utf-8")
        frag = d / "frag.json"
        frag.write_text('["Working tree is clean"]', encoding="utf-8")
        blank = d / "blank.py"
        blank.write_text(BLANK, encoding="utf-8")
        base = ["--checks", str(cpath), "--mutations", str(mpath)]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc_full = mod.main(base + ["--allow", str(full)])
        check(rc_full == 0, "an allowlist naming every discovered title is green (rc=" + str(rc_full) + ")")
        out2 = io.StringIO()
        with contextlib.redirect_stdout(out2):
            rc_none = mod.main(base + ["--allow", str(none)])
        t2 = out2.getvalue()
        check(rc_none == 1, "an empty allowlist exits 1 (rc=" + str(rc_none) + ")")
        check("No secret pattern in tracked files" not in t2,
              "a title that a mutation names is not reported as uncovered")
        check("Working tree is clean" in t2,
              "the detail names a title that really exists in the check file, so a reader can act on it")
        check(t2.count("Working tree is clean") >= 1,
              "uncovered titles are printed in full, not summarised")
        out3 = io.StringIO()
        with contextlib.redirect_stdout(out3):
            rc_frag = mod.main(base + ["--allow", str(frag)])
        check(rc_frag == 1, "an allowlist entry that is a fragment of a title does not silence it (rc="
              + str(rc_frag) + ")")
        out4 = io.StringIO()
        with contextlib.redirect_stdout(out4):
            rc_blank = mod.main(["--checks", str(blank), "--mutations", str(mpath), "--allow", str(none)])
        b4 = out4.getvalue().lower()
        check(rc_blank == 1, "a check file with zero titled checks is a finding, never a pass (rc="
              + str(rc_blank) + ")")
        check("title" in b4, "and the report says it found no titles: " + repr(out4.getvalue()))


def test_allowlist_io(mod):
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td)
        p = d / "a.json"
        p.write_text('["Alpha check", "Beta check"]', encoding="utf-8")
        check(mod.load_allowlist(p) == {"Alpha check", "Beta check"},
              "load_allowlist returns the exact strings the file holds")
        check(mod.load_allowlist(d / "missing.json") == set(),
              "a missing allowlist file is an empty set, not a crash")


def main():
    if not MOD.is_file():
        print("FAIL scripts/check_mutation_coverage.py does not exist -- that absence is the red "
              "this fixture declares. Nothing below runs until it exists.")
        sys.exit(1)
    try:
        mod = load()
    except Exception as e:
        print("FAIL scripts/check_mutation_coverage.py cannot be imported: " + type(e).__name__
              + ": " + str(e))
        sys.exit(1)
    for fn in (test_api, test_titles, test_mutation_texts, test_coverage_rules, test_add_and_remove,
               test_real_artefacts, test_cli, test_allowlist_io):
        print("-- " + fn.__name__)
        fn(mod)
    print("")
    print(str(len(FAILURES)) + " failure(s)")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
