#!/usr/bin/env python3
"""t1308: the workspace check's documentation-reference rule, pinned by a test.

BEHAVIOUR (temp dirs only). The real Checker.check_doc_references
(scripts/check_workspace.py:2530) runs over a temp repo built by make_repo(), the
fixture shape copied from scripts/tests/test_check_workspace_tree_state.py:49-82
(which copies scripts/tests/test_check_workspace.py:45-74). A tracked .md naming a .py
that exists nowhere must be reported as an undeclared dangling reference; a line in
that repo's docs/EXTERNAL-POINTERS.md naming that same path must silence it; declaring
one path must NOT silence a second; and a repo with no registry file must fail, not pass.

THE REAL REPO (the ratchet). The same real method runs over the repository this suite
sits in, and must report no undeclared reference. It is red today: on 2026-10-20
`python3 scripts/check_workspace.py --quick` reports six, one of which (a t1236
reference) appeared four days AFTER t1308 was filed -- which is why a count quoted in
prose is not a record, and why this suite COMPUTES its expectation from the checker
instead of storing today's list as the expected answer.

    python3 scripts/tests/test_check_workspace_doc_references.py

pytest is not installed: the __main__ block runs every test and exits non-zero on any
failure. No model call, no server, no fixed /tmp path (tempfile.mkdtemp only), every
subprocess carries timeout=, and nothing is written outside a temp dir.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from check_workspace import Checker  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DANGLER = 'scripts/tests/test_t1308_never_built.py'
SECOND = 'scripts/night_sweep_probe_t1308_fixture.py'

PASSED = 0
FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f'  [ok]   {label}: {got}')
    else:
        FAILED += 1
        print(f'  [BAD]  {label}: expected {want!r}, got {got!r}')


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args],
                          capture_output=True, text=True, timeout=60)


def make_repo():
    """A minimal committed repo, shaped like the real one.

    Copied from scripts/tests/test_check_workspace_tree_state.py:49-82 so this suite
    exercises the fixture the existing suites do, not one I imagined. docs/EXTERNAL-POINTERS.md
    exists because that is the shape under test.
    """
    root = Path(tempfile.mkdtemp())
    git(root, 'init', '-q', '-b', 'main')
    git(root, 'config', 'user.email', 't@example.com')
    git(root, 'config', 'user.name', 't')
    (root / 'workspace' / 'tasks').mkdir(parents=True)
    (root / 'docs').mkdir(parents=True)
    (root / 'docs' / 'EXTERNAL-POINTERS.md').write_text('# pointers\n', encoding='utf-8')
    commit_all(root, 'init')
    return root


def commit_all(root, msg):
    git(root, 'add', '-A')
    git(root, '-c', 'core.hooksPath=', 'commit', '-q', '-m', msg, '--no-verify')


def doc_rows(root):
    """Run the REAL check over a repo and return its result rows verbatim.

    The check reports through self.result (scripts/check_workspace.py:344); collecting
    those calls is how this suite reads the verdict and every path the check named --
    the whole detail, never a slice.
    """
    c = Checker(root, quick=True)
    rows = []
    c.section = lambda title: None
    c.result = lambda status, name, detail=None: rows.append((status, name, str(detail)))
    c.check_doc_references(c.workspace_files())
    return rows


def undeclared(rows):
    """The FAIL rows naming the dangling-reference check, by the name it reports (check_workspace.py:2612)."""
    return [n for (s, n, d) in rows if s == 'FAIL' and 'dangling' in n]


def fail_detail(rows):
    return ' '.join(d for (s, n, d) in rows if s == 'FAIL')


def write_doc(root, name, body):
    (root / 'workspace' / 'tasks' / name).write_text(body, encoding='utf-8')


def test_reference_to_an_absent_file_is_reported():
    r = make_repo()
    write_doc(r, 't1308-fixture.md', f'This brief proposes adding `{DANGLER}`.\n')
    commit_all(r, 'doc')
    check('the fixture note is tracked (git ls-files sees it)',
          't1308-fixture.md' in git(r, 'ls-files').stdout, True)
    check('no file of that name exists in the fixture tree',
          (r / DANGLER).exists(), False)
    rows = doc_rows(r)
    check('the real check reports it as an undeclared dangling reference',
          len(undeclared(rows)), 1)
    check('the report names the absent path', DANGLER in fail_detail(rows), True)
    shutil.rmtree(r, ignore_errors=True)


def test_declaring_the_reference_silences_it():
    r = make_repo()
    write_doc(r, 't1308-fixture.md', f'This brief proposes adding `{DANGLER}`.\n')
    commit_all(r, 'doc')
    check('red before any declaration exists', len(undeclared(doc_rows(r))), 1)
    reg = r / 'docs' / 'EXTERNAL-POINTERS.md'
    reg.write_text(reg.read_text(encoding='utf-8') +
                   f'| `workspace/tasks/t1308-fixture.md` | `{DANGLER}` | '
                   'A test a brief proposes and has not built. |\n', encoding='utf-8')
    commit_all(r, 'declare')
    rows = doc_rows(r)
    check('declaring the path turns the FAIL off', len(undeclared(rows)), 0)
    check('and the check says it accounted for them',
          any(s == 'PASS' and 'dangling reference is declared' in n
              for (s, n, d) in rows), True)
    shutil.rmtree(r, ignore_errors=True)


def test_a_declaration_silences_only_the_path_it_names():
    """Two DISTINCT paths, because one repeated placeholder cannot tell them apart.

    An implementation that mutes everything the moment the registry is non-empty passes
    tests 1-2 and dies here.
    """
    r = make_repo()
    write_doc(r, 't1308-two.md', f'Names `{DANGLER}` and `{SECOND}`.\n')
    commit_all(r, 'doc')
    reg = r / 'docs' / 'EXTERNAL-POINTERS.md'
    reg.write_text(reg.read_text(encoding='utf-8') +
                   f'| `workspace/tasks/t1308-two.md` | `{DANGLER}` | declared. |\n',
                   encoding='utf-8')
    commit_all(r, 'declare one')
    rows = doc_rows(r)
    check('declaring one path leaves the second undeclared', len(undeclared(rows)), 1)
    detail = fail_detail(rows)
    check('and the surviving FAIL names the UNdeclared path, not the declared one',
          (SECOND in detail, DANGLER in detail), (True, False))
    shutil.rmtree(r, ignore_errors=True)


def test_a_repo_with_no_registry_fails_rather_than_passing():
    r = make_repo()
    write_doc(r, 't1308-fixture.md', f'This brief proposes adding `{DANGLER}`.\n')
    commit_all(r, 'doc')
    (r / 'docs' / 'EXTERNAL-POINTERS.md').unlink()
    commit_all(r, 'drop the registry')
    check('an absent registry is not read as a clean bill of health',
          len(undeclared(doc_rows(r))), 1)
    shutil.rmtree(r, ignore_errors=True)


def test_the_real_repo_has_no_undeclared_reference():
    """The ratchet. The expectation is COMPUTED from the checker, so this suite does
    not freeze the six of 2026-10-20 as the answer of record."""
    check('this suite runs inside the repository it checks',
          (REPO / 'docs' / 'EXTERNAL-POINTERS.md').is_file(), True)
    rows = doc_rows(REPO)
    check('the real-repo run produced result rows (a check that said nothing is not a pass)',
          len(rows) > 0, True)
    bad = sorted(n for (s, n, d) in rows if s == 'FAIL')
    check('the real repo reports no undeclared dangling reference', bad, [])
    for (s, n, d) in rows:
        if s == 'FAIL':
            print(f'  [detail] {n}\n{d}')


if __name__ == '__main__':
    print('\ncheck_workspace.py documentation references (t1308)\n')
    test_reference_to_an_absent_file_is_reported()
    test_declaring_the_reference_silences_it()
    test_a_declaration_silences_only_the_path_it_names()
    test_a_repo_with_no_registry_fails_rather_than_passing()
    test_the_real_repo_has_no_undeclared_reference()
    print(f'\ndocumentation references: {PASSED} passed, {FAILED} failed')
    sys.exit(1 if FAILED else 0)
