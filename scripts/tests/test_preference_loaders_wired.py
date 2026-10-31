#!/usr/bin/env python3
"""t787: check_workspace must report FAIL while nothing loads docs/PREFERENCES.md.

docs/PREFERENCES.md:16-21 records its own defect -- "On 2026-08-31 a grep found that
nothing in the repository reads this file at all" -- and RCA 36ba6311a5d1 (D3) says the
today-measurement was never taken: no endpoint records that a reader loaded the file. A
preference written into a file nobody loads reaches no agent, and nothing failed.

RED ON MAIN for the missing change and nothing else: Checker has no
check_preference_loaders method, so test 1 fails on hasattr and every census() call below
raises AttributeError, reported by name.

The census is tested against TEMP trees built here, never against the live repository:
the live census is empty today (that IS the finding), so an oracle asserting it is
non-empty could never go green when this chunk lands.
"""
import contextlib
import io
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from check_workspace import Checker  # noqa: E402

PREF = "docs/PREFERENCES.md"
ANSI = re.compile(r"\033\[[0-9;]*m")
FAILURES = []

# (1) bind the path to a name, read it through that name: the shape a real loader takes.
BIND_READ = '''from pathlib import Path
PREFS = Path(__file__).resolve().parents[2] / "docs" / "PREFERENCES.md"


def load():
    return PREFS.read_text(encoding="utf-8")
'''

# (2) the same binding read a different way (T.open() not T.read_text()) in a different
# directory: a census that hard-codes one idiom or one directory is caught.
BIND_OPEN = '''from pathlib import Path
PREFS = Path(__file__).resolve().parents[2] / "docs" / "PREFERENCES.md"


def load():
    with PREFS.open(encoding="utf-8") as fh:
        return fh.read()
'''

# (3) the inline idiom: the path and the read on the same line.
INLINE = '''def load(root):
    return (root / "docs" / "PREFERENCES.md").read_text(encoding="utf-8")
'''

# The shape projects/local-inference/console.py:1091 has today -- the path is NAMED in a
# string an agent reads, and the file is never opened. Naming is not reading.
PROSE = '''# docs/PREFERENCES.md is how Jas wants to be worked with.
def prompt():
    return "docs/PREFERENCES.md is how Jas wants to be worked with."
'''

# The inverse trap: the path IS bound to a name, and that name is never read. A census
# built from "mentions the path, and opens something somewhere" counts this file -- and
# counts console.py, which opens plenty of files that are not this one.
BOUND_NOT_READ = '''from pathlib import Path
PREFS = Path(__file__).resolve().parents[2] / "docs" / "PREFERENCES.md"
LOG = Path(__file__).resolve().parents[2] / "workspace" / "log.txt"


def note():
    return LOG.read_text(encoding="utf-8")
'''


def report(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name)
    if not ok:
        for line in str(detail).split("\n"):
            print("        " + line)
        FAILURES.append(name)


def census(files):
    """Build a temp repo holding `files` and return the cleaned output of the census."""
    tmp = Path(tempfile.mkdtemp())
    for rel, text in files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        Checker(tmp).check_preference_loaders()
    # check_workspace colours its rows when stdout is a terminal; strip the codes so the
    # suite reports the verdict a pipe and a human see alike.
    return ANSI.sub("", buf.getvalue())


def test_method_exists():
    report("Checker has check_preference_loaders", hasattr(Checker, "check_preference_loaders"),
           "the census method is the change; without it every test below raises")


def test_a_bound_reader_is_counted():
    out = census({"scripts/pref_load.py": BIND_READ})
    report("a script that reads docs/PREFERENCES.md is counted as a loader",
           "[PASS]" in out and "scripts/pref_load.py" in out,
           "out was:\n" + out)


def test_every_loader_is_listed():
    out = census({"scripts/pref_load.py": BIND_READ,
                  "projects/local-inference/pref_open.py": BIND_OPEN,
                  "scripts/skills_pref.py": INLINE})
    report("PASS lists every loader: three directories, three read idioms",
           "[PASS]" in out
           and "scripts/pref_load.py" in out
           and "projects/local-inference/pref_open.py" in out
           and "scripts/skills_pref.py" in out,
           "all three paths must appear on the PASS detail; out was:\n" + out)


def test_naming_the_path_is_not_reading_it():
    out = census({"scripts/pref_mentions.py": PROSE})
    report("a prose mention of the path is NOT counted as a loader",
           "[FAIL]" in out and "scripts/pref_mentions.py" not in out,
           "a census that greps for the string goes green on a prompt string and the sink "
           "stays unread; out was:\n" + out)


def test_a_bound_path_never_read_is_not_a_loader():
    out = census({"scripts/pref_bound.py": BOUND_NOT_READ})
    report("binding the path without reading it is NOT counted as a loader",
           "[FAIL]" in out and "scripts/pref_bound.py" not in out,
           "this file reads a DIFFERENT file; counting it is the false pass that hides the "
           "defect; out was:\n" + out)


def test_a_test_that_reads_it_is_not_a_loader():
    out = census({"scripts/tests/test_pref_terse_model_removed.py": BIND_READ})
    report("a file under scripts/tests/ is NOT counted as a loader",
           "[FAIL]" in out and "scripts/tests/test_pref_terse_model_removed.py" not in out,
           "that is the only file in the repo that opens the file today, and a test feeds no "
           "agent; out was:\n" + out)


def test_empty_census_fails_saying_what_is_missing():
    out = census({})
    report("an empty census FAILS and names the file it looked for",
           "[FAIL]" in out and PREF in out and "loader" in out.lower(),
           "the record must say what was missing, not print an empty header; out was:\n" + out)


def test_the_check_is_dispatched_by_the_runner():
    src = (ROOT / "scripts" / "check_workspace.py").read_text(encoding="utf-8")
    a = src.find("def run(self):")
    b = src.find("def main(")
    report("check_workspace's run() dispatches the census check",
           a != -1 and b != -1 and a < b and "self.check_preference_loaders" in src[a:b],
           "a check nothing calls is prose: it belongs in the dispatch tuple inside run(), "
           "the surface session_start.py and session_end.py execute (main() only builds a "
           "Checker and calls run())")


TESTS = [test_method_exists, test_a_bound_reader_is_counted, test_every_loader_is_listed,
         test_naming_the_path_is_not_reading_it, test_a_bound_path_never_read_is_not_a_loader,
         test_a_test_that_reads_it_is_not_a_loader, test_empty_census_fails_saying_what_is_missing,
         test_the_check_is_dispatched_by_the_runner]

if __name__ == "__main__":
    for fn in TESTS:
        try:
            fn()
        except Exception as e:  # noqa: BLE001 -- a raised test is a failed test, reported
            report("%s raised" % fn.__name__, False, "%s: %s" % (type(e).__name__, e))
    print("\n%d failure(s)" % len(FAILURES))
    sys.exit(1 if FAILURES else 0)
