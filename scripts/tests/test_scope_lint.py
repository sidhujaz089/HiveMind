#!/usr/bin/env python3
"""scope_lint.py: the upstream fixture catches each kind of broken scope before the bridge, and passes
a sound one (Jas, 2026-09-28). A throwaway git repository; the real one is not touched."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(ROOT / "scripts")]
import scope_lint as sl  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, timeout=60)


def block(*chunks):
    return "answer\n```chunks\n" + json.dumps(list(chunks)) + "\n```\n"


def impl(target, **kw):
    return {"kind": "implementation", "file": "lib/mod.py", "target": target, "single_change": "add f",
            "what_is_wrong": "f is missing", "done_when": f"python3 {target} exits 0",
            "related_entries": ["x"], **{"symbols": ["f (new)"], **kw}}   # Part B: Python chunks name symbols


# oracle-weak and brief-shared-target (2026-10-02) fire on most of these minimal fixtures, which
# exist to pin OTHER checks; they are asserted on their own in the cases below and in
# test_scope_lint_weak.py, and left out of every other case's expectation.
# brief-unanchored (t656, 2026-10-06) joins them: almost every fixture here uses impl(T), a chunk with
# no anchor, so it would fire everywhere; it is pinned on its own in test_scope_lint_anchors.py.
NEWER = {"oracle-weak", "brief-shared-target", "brief-unanchored"}


def classes(text, repo, execute=True, keep=()):
    return sorted({c for c, *_ in sl.lint(text, repo, execute)[1] if c not in NEWER or c in keep})


def main():
    tmp = Path(tempfile.mkdtemp(prefix="lint-test-"))
    try:
        repo = tmp / "repo"
        (repo / "lib").mkdir(parents=True)
        (repo / "scripts" / "tests").mkdir(parents=True)
        (repo / "lib" / "mod.py").write_text("def g():\n    return 1\n")
        (repo / "scripts" / "helper.py").write_text("X = 1\n")
        (repo / "lib" / "big.py").write_text("def h():\n    pass\n" + "# pad\n" * 9000)
        (repo / "lib" / "BIG.md").write_text("# Lessons\n" + "a line of a long document\n" * 3000)
        git(repo, "init", "-q", "-b", "main")
        git(repo, "add", "-A")
        git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")

        red = ("import sys\nsys.path.insert(0, 'lib')\nimport mod\n"
               "assert hasattr(mod, 'f'), 'f missing'\n")
        T = "scripts/tests/test_mod.py"
        good = block({"kind": "oracle", "file": T, "oracle_text": red, "red_because": "no f"}, impl(T))
        report("a sound scope: red on main, clean, anchors fine -> no findings", classes(good, repo), [])

        green = block({"kind": "oracle", "file": T, "oracle_text": "print('ok')\n"}, impl(T))
        report("an oracle that already passes on main is oracle-green", classes(green, repo), ["oracle-green"])

        dirty = block({"kind": "oracle", "file": T,
                       "oracle_text": "open('lib/leak.txt','w').write('x')\nraise SystemExit(1)\n"}, impl(T))
        # lib/ holds a tracked file, so the new file is ALSO the live-state-leak shape (t699)
        report("an oracle that writes into the repo is oracle-dirty, and a leak into a tracked dir",
               classes(dirty, repo), ["dirty-leaks-to-tracked-dir", "oracle-dirty"])

        broken = block({"kind": "oracle", "file": T, "oracle_text": "def (:\n"}, impl(T))
        report("an oracle that does not parse is oracle-crash", classes(broken, repo), ["oracle-crash"])

        live = block({"kind": "oracle", "file": T,
                      "oracle_text": "open('/tmp/fixed-name.log','w')\nraise SystemExit(1)\n"}, impl(T))
        report("a fixed /tmp path in an oracle is oracle-gate (tests/ is not exempt here)",
               "oracle-gate" in classes(live, repo, execute=False), True)

        anchor = block({"kind": "oracle", "file": T, "oracle_text": red},
                       impl(T, symbols=["g", "nope"], single_change="edit lib/mod.py:40"))
        found = sl.lint(anchor, repo, execute=False)[1]
        declared = block({"kind": "oracle", "file": T, "oracle_text": red}, impl(T, symbols=["g", "f (new)"]))
        report("a symbol declared '(new)' is not a missing anchor", classes(declared, repo, execute=False), [])
        report("a SYMBOLS name not in the file and a line past its end are brief-anchor",
               sorted(ev.split(" ")[0] for c, _i, _f, ev in found if c == "brief-anchor"), ["SYMBOLS", "cites"])

        newfile = block({"kind": "oracle", "file": T, "oracle_text": red}, dict(impl(T), file="lib/new_page.html"))
        report("an implementation chunk on a file that does not exist is brief-unsatisfiable (coder.py "
               "cannot create files; p154 was refused 62 times)",
               classes(newfile, repo, execute=False), ["brief-unsatisfiable"])
        declare = block({"kind": "oracle", "file": T, "oracle_text": red},
                        dict(impl(T), file="workspace/test-failures-ceiling.json"))
        report("a chunk asking the builder to declare the oracle is brief-unsatisfiable (the bridge does it; p158)",
               classes(declare, repo, execute=False), ["brief-unsatisfiable"])
        scattered = block({"kind": "oracle", "file": T, "oracle_text": red},
                          dict(impl(T, symbols=["h"], single_change="edit big.py:100/:900/:1700/:2500/:3300/:4100/:4900"),
                               file="lib/big.py"))
        report("seven scattered edit sites in one chunk is brief-scatter (t450: 11 sites overflowed the "
               "16k output cap)", classes(scattered, repo, execute=False), ["brief-scatter"])
        six = block({"kind": "oracle", "file": T, "oracle_text": red},
                    dict(impl(T, symbols=["h"], single_change="edit big.py:100/:900/:1700/:2500/:3300"), file="lib/big.py"))
        report("  six is within the limit", classes(six, repo, execute=False), [])
        big = block({"kind": "oracle", "file": T, "oracle_text": red}, dict(impl(T, symbols=[]), file="lib/big.py"))
        # Part B (2026-10-19): a Python chunk with no SYMBOLS is also brief-no-symbols (t1072)
        report("a big file with no SYMBOLS is brief-window", classes(big, repo, execute=False),
               ["brief-no-symbols", "brief-window"])
        bigok = block({"kind": "oracle", "file": T, "oracle_text": red},
                      dict(impl(T, symbols=["h"]), file="lib/big.py"))
        report("  and naming SYMBOLS that exist clears it", classes(bigok, repo, execute=False), [])
        bigmd = block({"kind": "oracle", "file": T, "oracle_text": red}, dict(impl(T, symbols=[]), file="lib/BIG.md"))
        report("a big Markdown file with no SYMBOLS is brief-window (t672)", classes(bigmd, repo, execute=False),
               ["brief-window"])
        mdapp = block({"kind": "oracle", "file": T, "oracle_text": red},
                      dict(impl(T, append=True, symbols=[]), file="lib/BIG.md"))
        report("  and `append: true` clears it -- the builder is shown the tail", classes(mdapp, repo, execute=False),
               [])
        pyapp = block({"kind": "oracle", "file": T, "oracle_text": red},
                      dict(impl(T, append=True, symbols=[]), file="lib/big.py"))
        report("  but `append` does not excuse a .py file (SYMBOLS slice those)",
               classes(pyapp, repo, execute=False), ["brief-window"])

        norunner = block({"kind": "oracle", "file": T,
                          "oracle_text": "import sys\nsys.path.insert(0, 'lib')\nimport mod\n"
                                         "def test_f():\n    assert mod.f() == 2\n"}, impl(T))
        report("tests with no __main__ block are oracle-no-runner (python3 runs none of them)",
               "oracle-no-runner" in classes(norunner, repo, execute=False), True)
        ports = block({"kind": "oracle", "file": T, "oracle_text": red},
                      impl(T, what_is_wrong="the host is 127.0.0.1:11434, fallback :4000; see other.py:900"))
        report("ports and other files' lines are not read as this file's line numbers",
               classes(ports, repo, execute=False), [])
        own = block({"kind": "oracle", "file": T, "oracle_text": red},
                    impl(T, what_is_wrong="see mod.py:1/:300"))
        report("  but this file's own chained citations are", classes(own, repo, execute=False), ["brief-anchor"])
        # RED FOR ITS OWN BUG (calibration 2026-09-28: 14 of 64)
        short_root = block({"kind": "oracle", "file": T, "oracle_text":
                            "import sys\nsys.path.insert(0, 'nowhere')\nimport helper\n"}, impl(T))
        report("a module that exists but cannot be imported is oracle-red-reason (sys.path wrong)",
               classes(short_root, repo), ["oracle-red-reason"])
        uses_pytest = block({"kind": "oracle", "file": T, "oracle_text": "import pytest\n"}, impl(T))
        report("importing pytest (not installed) is oracle-red-reason", classes(uses_pytest, repo),
               ["oracle-red-reason"])
        placeholder = block({"kind": "oracle", "file": T,
                             "oracle_text": "raise NotImplementedError('wire me')\n"}, impl(T))
        report("a NotImplementedError placeholder is oracle-red-reason", classes(placeholder, repo),
               ["oracle-red-reason"])
        sig = block({"kind": "oracle", "file": T, "oracle_text":
                     "import sys\nsys.path.insert(0, 'lib')\nimport mod\nmod.g(ids=1)\n"}, impl(T))
        report("a keyword the function lacks and the brief never adds is oracle-red-reason",
               classes(sig, repo), ["oracle-red-reason"])
        sig_ok = block({"kind": "oracle", "file": T, "oracle_text":
                        "import sys\nsys.path.insert(0, 'lib')\nimport mod\nmod.g(ids=1)\n"},
                       impl(T, single_change="give g an ids parameter"))
        report("  but not when the brief adds that parameter", classes(sig_ok, repo), [])
        unresolved = block({"kind": "oracle", "file": T, "oracle_text": red},
                           impl(T, single_change="FILL #1: resolve before dispatch"))
        report("a placeholder in the brief is brief-unresolved",
               classes(unresolved, repo, execute=False), ["brief-unresolved"])
        overlap = block({"kind": "oracle", "file": T, "oracle_text": red}, impl(T), impl(T))
        report("two implementation chunks on one file are brief-overlap",
               classes(overlap, repo, execute=False), ["brief-overlap"])
        # t458 (2026-10-02): one implementation chunk per ORACLE -- built alone, two on one target
        # can never each turn it green (p238).
        other = dict(impl(T), file="scripts/helper.py", symbols=["X"])
        shared = block({"kind": "oracle", "file": T, "oracle_text": red}, impl(T), other)
        report("two implementation chunks on one target are brief-shared-target",
               "brief-shared-target" in classes(shared, repo, execute=False, keep=NEWER), True)
        T2 = "scripts/tests/test_helper.py"
        apart = block({"kind": "oracle", "file": T, "oracle_text": red},
                      {"kind": "oracle", "file": T2, "oracle_text": red}, impl(T), dict(other, target=T2,
                      done_when=f"python3 {T2} exits 0"))
        report("  one target each is not",
               "brief-shared-target" in classes(apart, repo, execute=False, keep=NEWER), False)
        marker = tmp / "leak.pid"
        leaky = block({"kind": "oracle", "file": T, "oracle_text":
                       "import subprocess, sys\n"
                       f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                       f"open({str(marker)!r}, 'w').write(str(p.pid))\nraise SystemExit(1)\n"}, impl(T))
        sl.lint(leaky, repo)
        import os
        pid = int(marker.read_text())
        try:
            os.kill(pid, 0)
            alive = open(f"/proc/{pid}/stat").read().split()[2] != "Z"
        except (ProcessLookupError, FileNotFoundError):
            alive = False
        report("a process the oracle leaves running is killed with its group", alive, False)
        report("a block bridge refuses is chunks-invalid",
               classes(block(impl("scripts/tests/test_absent.py")), repo), ["chunks-invalid"])
        report("the throwaway worktrees are gone afterwards",
               git(repo, "worktree", "list").stdout.count("\n"), 1)
        report("main is untouched", git(repo, "status", "--porcelain").stdout, "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
