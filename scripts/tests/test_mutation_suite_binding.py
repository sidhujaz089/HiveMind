#!/usr/bin/env python3
"""Red fixture for scripts/check_mutation_suite_binding.py (t1053).

RED ON MAIN FOR ONE REASON, and nothing else: scripts/check_mutation_suite_binding.py
does not exist.  Verified 2026-10-12:
    ls scripts/check_mutation_suite_binding.py  ->  No such file
main() guards on that and exits 1, so no test below runs until the module exists.  The
module is never imported at module scope: an ImportError there is the same red reported
as a crash, which is the oracle-crash defect class.

THE ONE SENTENCE THIS MODULE EXISTS FOR (workspace/rca/75ee06d097f1.md): a mutation is
credited only against a suite that demonstrably READS the file it breaks.  On 2026-08-25
the entry pysource-constant-blind (scripts/mutate.py:796-801) named a suite that does not
reach the mutated path, and mutate.py's only signal is 'the suite went red' -- true of a
suite that goes red for ANY reason.  Nothing ever checked the relation.

TWO FACTS READ OFF THE TREE, BOTH OF WHICH DEFEAT THE EXAMPLE IN THE TASK TEXT:
  * grep -c pysource scripts/tests/test_served_prose.py  ->  0.  That suite reaches
    scripts/pysource.py ONLY transitively, by running scripts/check_workspace.py
    (scripts/tests/test_served_prose.py:51), which imports it
    (scripts/check_workspace.py:41).  So a direct-literal predicate cannot make the task's
    'green' case green, and 'module imports and string-literal repo paths' alone is not
    enough -- depth >= 1 over the repo's own files is required.
  * grep -n 'import' scripts/tests/test_check_workspace.py:24 -> from check_workspace
    import Checker.  That suite reaches check_workspace.py too, and at the same depth
    reaches pysource.py.  So the task's 'red' case (the same entry naming
    test_check_workspace.py) is NOT red: it binds.  Both halves of the worked example in
    the task are unbuildable on real files, so every NEGATIVE assertion here is built on
    stubs inside a temp dir, where 'a suite that reads nothing' can be written at all.

Everything runs in a temp dir with stub files.  No subprocess, no model call, no live
server, no fixed /tmp path, no state outside tempfile.TemporaryDirectory().
"""

import contextlib
import importlib.util
import io
import json
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
MOD = REPO / "scripts" / "check_mutation_suite_binding.py"

# Real, distinct values read off scripts/mutate.py -- never one placeholder string.
# (mutate.py:796-799) and (mutate.py:803-806).
NAME_A = "pysource-constant-blind"
TARGET_A = "scripts/pysource.py"
SUITE_A = "scripts/tests/test_served_prose.py"
NAME_B = "pysource-drops-unparseable"
SUITE_B = "scripts/tests/test_check_deployed.py"
# The pre-2026-08-25 mis-binding this task exists to prevent.  The string appears in
# mutate.py:801 as prose ("test_check_workspace does not reach this path, ..."), so an
# implementation that pairs names by substring over the raw text will produce it.
SUITE_MISBOUND = "scripts/tests/test_check_workspace.py"

FAILURES = []


def check(cond, msg):
    if cond:
        print("  ok    " + msg)
    else:
        FAILURES.append(msg)
        print("  FAIL  " + msg)


def load():
    spec = importlib.util.spec_from_file_location("check_mutation_suite_binding", MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- stub source shapes (the only place a non-reaching suite can be expressed) --------

# mirrors scripts/tests/test_served_prose.py:51 -- a suite that RUNS a checker script
SUITE_RUNNER = (
    "import subprocess, sys\n"
    "from pathlib import Path\n"
    "ROOT = Path(__file__).resolve().parents[2]\n"
    "subprocess.run([sys.executable, str(ROOT / 'scripts' / 'check_workspace.py')],\n"
    "               capture_output=True, text=True, timeout=120)\n"
)
SUITE_RUNNER_UNRELATED = (
    "import subprocess, sys\n"
    "from pathlib import Path\n"
    "ROOT = Path(__file__).resolve().parents[2]\n"
    "subprocess.run([sys.executable, str(ROOT / 'scripts' / 'unrelated.py')],\n"
    "               capture_output=True, text=True, timeout=120)\n"
)
SUITE_EMPTY = "x = 1\n"
SUITE_PREFIX = (
    "TARGET = 'scripts/pysource'\n"
    "SAYS = 'the file scripts/pysource.py.bak is not the mutated file'\n"
)


def stub_repo(root):
    """A repo shaped like the real one at the same relative depths.  Returns root."""
    root = pathlib.Path(root)
    (root / "pysource.py").write_text("import json\n", encoding="utf-8")
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "check_workspace.py").write_text("import pysource\n",
                                                        encoding="utf-8")
    (root / "scripts" / "unrelated.py").write_text("x = 1\n", encoding="utf-8")
    (root / "scripts" / "tests").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "tests" / "test_ok.py").write_text(SUITE_RUNNER, encoding="utf-8")
    (root / "scripts" / "tests" / "test_unrelated.py").write_text(
        SUITE_RUNNER_UNRELATED, encoding="utf-8")
    (root / "scripts" / "tests" / "test_empty.py").write_text(SUITE_EMPTY, encoding="utf-8")
    (root / "scripts" / "tests" / "test_prefix.py").write_text(SUITE_PREFIX,
                                                               encoding="utf-8")
    return root


def stub_mutations():
    """One entry whose suite reaches its target at depth 2, one that reaches nothing."""
    return (
        "MUTATIONS = [\n"
        "    ('good', 'pysource.py', 'if a:', 'if False:',\n"
        "     'scripts/tests/test_ok.py', 'why good'),\n"
        "    ('bad', 'scripts/lonely.py', 'if b:', 'if False:',\n"
        "     'scripts/tests/test_empty.py', 'why bad'),\n"
        "]\n"
    )


# ---- tests ----------------------------------------------------------------------------

def test_api(mod):
    for name in ("suite_read_set", "reach_set", "mutation_reaches", "parse_mutations",
                 "binding_problems", "load_allowlist", "main"):
        check(callable(getattr(mod, name, None)),
              "scripts/check_mutation_suite_binding.py defines " + name + "()")


def test_read_set_on_a_real_suite(mod):
    src = (REPO / "scripts" / "tests" / "test_served_prose.py").read_text(encoding="utf-8")
    rs = set(mod.suite_read_set(src, "scripts/tests"))
    check(TARGET_A + "served"[0:0] == TARGET_A, "guard: target constant intact")
    check("scripts/check_workspace.py" in rs,
          "the real scripts/tests/test_served_prose.py's read set names "
          "scripts/check_workspace.py (it is run as a subprocess at line 51)")
    check(TARGET_A not in rs,
          "AND DOES NOT name scripts/pysource.py -- grep -c pysource on that file is 0, "
          "so an implementation that reports reachability for everything fails this")
    check(all(isinstance(p, str) for p in rs),
          "read-set entries are repo-relative strings, not Path objects")
    check(not any(p.startswith("/") or ".." in p for p in rs),
          "no read-set entry is absolute or escaped the repo: " + repr(sorted(rs)[:6]))
    check(set(mod.suite_read_set(SUITE_EMPTY, "scripts/tests")) == set(),
          "a suite that reads nothing has an EMPTY read set (the 2026-08-25 shape)")
    check("scripts/pysource.py" not in set(mod.suite_read_set(SUITE_PREFIX, "scripts/tests")),
          "a literal that is a PREFIX of the target ('scripts/pysource') and a literal "
          "with a .py.bak suffix do NOT reach it -- prefix matching is the t471 class")


def test_reaches_on_the_real_2026_08_25_pair(mod):
    r = REPO
    check(mod.mutation_reaches("scripts/check_workspace.py", SUITE_A, r, depth=0) is True,
          "test_served_prose.py reaches scripts/check_workspace.py at depth 0 (direct)")
    check(mod.mutation_reaches(TARGET_A, SUITE_A, r, depth=0) is False,
          "the same pair is NOT bound at depth 0: that suite never names pysource, so a "
          "predicate that ignores the depth argument fails this")
    check(mod.mutation_reaches(TARGET_A, SUITE_A, r, depth=1) is True,
          "and it IS bound at depth 1, through scripts/check_workspace.py:41 "
          "'import pysource' -- the real chain behind mutate.py:796-801")
    check(mod.mutation_reaches(TARGET_A, SUITE_A, r, depth=2) is True,
          "and at the default depth")
    check(mod.mutation_reaches(TARGET_A, SUITE_MISBOUND, r, depth=0) is False,
          "scripts/tests/test_check_workspace.py does not reach scripts/pysource.py "
          "directly (it imports check_workspace, not pysource)")
    check(mod.mutation_reaches(TARGET_A, SUITE_MISBOUND, r, depth=1) is True,
          "NOTE: it IS bound at depth 1 -- test_check_workspace.py:24 imports "
          "check_workspace, which imports pysource.  This is why the task's 'red' example "
          "is unbuildable, and why the negatives below use stubs")


def test_reaches_on_stubs(mod):
    with tempfile.TemporaryDirectory() as d:
        root = stub_repo(d)
        T = pathlib.PurePosixPath
        check(mod.mutation_reaches("pysource.py", "scripts/tests/test_ok.py",
                                   root, depth=1) is False,
              "at depth 1 a runner suite lands on scripts/check_workspace.py only, "
              "so a predicate that answers True for every (target, suite) fails this")
        check(mod.mutation_reaches("pysource.py", "scripts/tests/test_ok.py",
                                   root, depth=2) is True,
              "at depth 2 it reaches the module that suite's subprocess target imports")
        check(mod.mutation_reaches("pysource.py", "scripts/tests/test_empty.py",
                                   root, depth=2) is False,
              "a suite that reads nothing reaches nothing at any depth")
        check(mod.mutation_reaches("pysource.py", "scripts/tests/test_unrelated.py",
                                   root, depth=2) is False,
              "transitivity is not 'everything': running scripts/unrelated.py does not "
              "reach the root-level pysource.py")
        check(mod.mutation_reaches("scripts/lonely.py", "scripts/tests/test_ok.py",
                                   root, depth=2) is False,
              "a target nothing reads is unbound -- this is the finding the guard emits")
        rs = mod.reach_set({"scripts/check_workspace.py"}, root, 1)
        check("pysource.py" in set(rs),
              "reach_set expands a seed through that file's own imports")
        check("scripts/check_workspace.py" in set(rs),
              "reach_set keeps the seed itself, so a depth-0 caller is not blinded")
        check(T("pysource.py") == T("pysource.py"),
              "guard: stub layout is repo-relative")


def test_parse_mutations_real(mod):
    src = (REPO / "scripts" / "mutate.py").read_text(encoding="utf-8")
    entries = mod.parse_mutations(src)
    check(isinstance(entries, list) and entries,
          "parse_mutations returns a non-empty list for the real scripts/mutate.py")
    check(len(entries) >= 50,
          "the real scripts/mutate.py yields " + str(len(entries)) +
          " entries (scripts/tests/test_mutation_coverage.py:194 sets the bar at >= 50 "
          "on the same file)")
    by_name = {}
    for e in entries:
        check(isinstance(e, dict) and set(("name", "target", "suite")) <= set(e),
              "each parsed entry carries the keys name, target, suite; got " + repr(e)[:120])
        by_name[e.get("name")] = e
    a = by_name.get(NAME_A)
    check(a is not None,
          "the real entry named " + repr(NAME_A) + " is parsed (scripts/mutate.py:796)")
    if a:
        check(a["target"] == TARGET_A,
              "and its target is the REAL value " + repr(TARGET_A) + ": " + repr(a["target"]))
        check(a["suite"] == SUITE_A,
              "and its suite is the REAL value " + repr(SUITE_A) + ": " + repr(a["suite"]))
        check(a["suite"] != SUITE_MISBOUND,
              "and NOT " + repr(SUITE_MISBOUND) + " -- that string sits in mutate.py:801 as "
              "prose, so a regex that pairs names by substring over the raw text yields it")
    b = by_name.get(NAME_B)
    check(b is not None,
          "the real entry named " + repr(NAME_B) + " is parsed (scripts/mutate.py:803)")
    if b:
        check(b["target"] == TARGET_A and b["suite"] == SUITE_B,
              "and the neighbour's DISTINCT target/suite pair is read, not a placeholder "
              "reused for every entry: " + repr(b))
    check(len(set(str(e.get("suite")) for e in entries)) > 1,
          "the parsed entries name more than one distinct suite, so a single canned pair "
          "cannot satisfy this test")


def test_binding_problems(mod):
    src = (REPO / "scripts" / "mutate.py").read_text(encoding="utf-8")
    found = list(mod.binding_problems(src, REPO, depth=2))
    check(all(isinstance(f, str) and f.strip() for f in found),
          "every finding is a non-empty string")
    joined = "\n".join(found)
    check(NAME_A not in joined,
          "the REAL scripts/mutate.py + the REAL repo report no finding for " + repr(NAME_A) +
          " -- that is the 2026-08-25 fix holding; findings: " + repr(found[:3]))
    with tempfile.TemporaryDirectory() as d:
        root = stub_repo(d)
        probs = list(mod.binding_problems(stub_mutations(), root, depth=2))
        check(len(probs) == 1,
              "a stub mutations file with one reaching entry and one non-reaching entry "
              "yields exactly ONE finding, got " + repr(probs))
        if len(probs) == 1:
            p = probs[0]
            check("bad" in p, "the finding names the entry: " + repr(p))
            check("scripts/lonely.py" in p,
                  "the finding names the target file, so a person can act on it: " + repr(p))
            check("scripts/tests/test_empty.py" in p,
                  "the finding names the suite it is bound to: " + repr(p))
            check("why good" not in p,
                  "the finding does NOT report the entry that binds (no false positive)")
        empty = list(mod.binding_problems("MUTATIONS = []\n", root, depth=2))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = mod.main(["--mutations", write_mutations(root, "m_empty.py", "MUTATIONS = []\n"),
                           "--repo", str(root)])
        text = out.getvalue()
        check(rc == 1,
              "an empty MUTATIONS list exits 1, never clean: rc=" + repr(rc))
        check("entry" in text.lower(),
              "and the output says it read no entries: " + repr(text[-300:]))
        print("  ok    binding_problems on an empty list -> " + str(len(empty)) + " findings")


def write_mutations(root, name, text):
    p = pathlib.Path(root) / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_cli_and_allowlist(mod):
    with tempfile.TemporaryDirectory() as d:
        root = stub_repo(d)
        mpath = write_mutations(root, "mutate_stub.py", stub_mutations())

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = mod.main(["--mutations", mpath, "--repo", str(root)])
        text = out.getvalue()
        check(rc == 1, "findings make the CLI exit 1: rc=" + repr(rc) + " out=" + repr(text[-300:]))
        check("bad" in text, "the output names the un-dispositioned entry: " + repr(text[-300:]))
        check("why bad" in text,
              "the output carries the entry's own reason text, not just its name")

        adir = pathlib.Path(d) / "allow"
        adir.mkdir()
        apath = adir / "allowlist.json"
        apath.write_text(json.dumps(["bad"]), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = mod.main(["--mutations", mpath, "--repo", str(root),
                           "--allow", str(apath)])
        text = out.getvalue()
        check(rc == 0,
              "with 'bad' in the allowlist the same invocation exits 0: rc=" + repr(rc) +
              " out=" + repr(text[-300:]))
        check("why bad" not in text,
              "and the dispositioned entry's text is not reported")

        apath.write_text(json.dumps(["no-such-mutation"]), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = mod.main(["--mutations", mpath, "--repo", str(root),
                           "--allow", str(apath)])
        text = out.getvalue()
        check(rc == 1,
              "an allowlist entry that matches NO finding exits 1 -- the decay test: a "
              "stale allowlist is a finding, not silence. rc=" + repr(rc))
        check("no-such-mutation" in text,
              "and it names the stale entry: " + repr(text[-300:]))

        bad = write_mutations(root, "no_muts.py", "x = 1\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = mod.main(["--mutations", bad, "--repo", str(root)])
        text = out.getvalue()
        check(rc == 1,
              "a mutations file with no MUTATIONS assignment exits 1: rc=" + repr(rc))
        check("MUTATIONS" in text,
              "and says so, naming what it looked for: " + repr(text[-300:]))

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = mod.main(["--mutations", str(pathlib.Path(d) / "absent.py"),
                           "--repo", str(root)])
        check(rc == 1,
              "a mutations path that does not exist exits 1 (never 'clean'): rc=" + repr(rc))
        check(isinstance(rc, int), "main() returns an int exit code, not None")


def test_cli_does_not_write(mod):
    with tempfile.TemporaryDirectory() as d:
        root = stub_repo(d)
        mpath = write_mutations(root, "mutate_stub.py", stub_mutations())
        before = sorted((str(p.relative_to(root)), p.read_text(encoding="utf-8"))
                        for p in root.rglob("*") if p.is_file())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            mod.main(["--mutations", mpath, "--repo", str(root)])
        after = sorted((str(p.relative_to(root)), p.read_text(encoding="utf-8"))
                       for p in root.rglob("*") if p.is_file())
        check(before == after,
              "the audit is READ-ONLY over the tree: an allowlist the tool writes is an "
              "allowlist the checked party can grant itself")


def main():
    if not MOD.is_file():
        print("FAIL scripts/check_mutation_suite_binding.py does not exist -- that absence "
              "is the red this fixture declares. Nothing below runs until it exists.")
        sys.exit(1)
    try:
        mod = load()
    except Exception as e:
        print("FAIL scripts/check_mutation_suite_binding.py cannot be imported: "
              + type(e).__name__ + ": " + str(e))
        sys.exit(1)
    for fn in (test_api, test_read_set_on_a_real_suite, test_reaches_on_the_real_2026_08_25_pair,
               test_reaches_on_stubs, test_parse_mutations_real, test_binding_problems,
               test_cli_and_allowlist, test_cli_does_not_write):
        print("-- " + fn.__name__)
        fn(mod)
    print("")
    print(str(len(FAILURES)) + " failure(s)")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
