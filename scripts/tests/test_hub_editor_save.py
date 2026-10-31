#!/usr/bin/env python3
'''t082 step 1 (FILES): the Hub's guarded WRITE path, pinned.

The task's first step (workspace/tasks/t082--journaling-app.md, 2026-10-05) is a browser file
editor whose "Save is a write through the same guarded path the Hub already uses (no edits
outside the workspace)."

ON DISK TODAY (read 2026-10-15): the READ guard is real -- projects/app-repo/feedback.py:168
def _repo_file(rel) resolves a relative path under the repo root, refuses the _FILE_DENY
subtrees defined at feedback.py:165, and returns None for anything that does not land inside
the repository. The WRITE guard does not exist: `grep -rn save_repo_file --include=*.py .`
returns nothing, and the only writes in feedback.py go to paths the code names itself --
save_uploads (feedback.py:1037) writes into taskstore's attachment directory and takes no
client-supplied path. So the editor has nothing to save through, and a save route written
without a guard can write anywhere the Hub's user can reach.

Every write here goes into a tempdir passed as `root`: nothing under the repository is touched.
'''
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'projects' / 'app-repo'))

FAILS = []


def ck(label, ok):
    print(f"  [{'ok' if ok else 'BAD'}]  {label}")
    if not ok:
        FAILS.append(label)


def _F():
    import feedback as F
    return F


def test_writes_the_exact_bytes_inside_the_root():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        body = '# Note\n\nkept -- verbatim, with a trailing newline.\n'
        got = _F().save_repo_file('library/notes/note-1.md', body, root=root)
        ck('the save is not refused', got is not None)
        f = root / 'library' / 'notes' / 'note-1.md'
        ck('it created the parent directories the path names (library/notes/ is what t082 saves into)',
           f.parent.is_dir())
        ck('the file is on disk', f.is_file())
        if f.is_file():
            ck('the bytes on disk are EXACTLY the bytes sent',
               f.read_bytes() == body.encode('utf-8'))
        if got is not None:
            ck('it hands back the path it wrote, so a caller can link to it',
               Path(str(got)).resolve() == f.resolve())


def test_saving_twice_keeps_the_last_save():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _F().save_repo_file('notes/a.md', 'first', root=root)
        _F().save_repo_file('notes/a.md', 'second', root=root)
        f = root / 'notes' / 'a.md'
        ck('an editor can save the same file twice', f.is_file())
        ck('and the file holds the LAST text sent', f.read_bytes() == b'second' if f.is_file() else False)


def test_a_benign_relative_walk_inside_the_root_still_saves():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        got = _F().save_repo_file('library/notes/./a/../b.md', 'ok', root=root)
        ck('a walk that stays inside the root is a save, not a refusal', got is not None)
        f = root / 'library' / 'notes' / 'b.md'
        ck('and it lands at the normalised path', f.is_file())
        ck('and with the bytes sent', f.read_bytes() == b'ok' if f.is_file() else False)


def test_refuses_a_path_that_climbs_out_of_the_root():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / 'hub'
        root.mkdir()
        got = _F().save_repo_file('a/../../escape.md', 'x', root=root)
        ck('a relative path that climbs out is refused', got is None)
        ck('and it created nothing outside the root', not (root.parent / 'escape.md').exists())


def test_refuses_an_absolute_path():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / 'hub'
        root.mkdir()
        other = Path(td) / 'elsewhere'
        got = _F().save_repo_file(str(other / 'abs.md'), 'x', root=root)
        ck('an absolute path is refused', got is None)
        ck('and it created nothing at the absolute target', not (other / 'abs.md').exists())


def test_refuses_the_denied_subtrees_the_reader_already_refuses():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        F = _F()
        deny = getattr(F, '_FILE_DENY', None)
        ck('the module has the deny list the read guard uses, including the private subtree',
           isinstance(deny, (tuple, list)) and 'workspace/private/' in deny)
        if not isinstance(deny, (tuple, list)):
            return
        for bad in deny:
            rel = bad + 'note.md'
            ck(f'  a denied subtree is refused: {rel}', F.save_repo_file(rel, 'x', root=root) is None)
            ck(f'  and created nothing: {rel}', not (root / rel).exists())


def test_refuses_an_empty_path():
    with tempfile.TemporaryDirectory() as td:
        ck('an empty path is refused, not a write to the root itself',
           _F().save_repo_file('', 'x', root=Path(td)) is None)


def test_refuses_a_symlink_that_leaves_the_root():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / 'hub'
        root.mkdir()
        outside = Path(td) / 'outside'
        outside.mkdir()
        (root / 'link').symlink_to(outside)
        got = _F().save_repo_file('link/outside.md', 'x', root=root)
        ck('a symlink inside the root that points out is refused', got is None)
        ck('and it created nothing through the symlink', not (outside / 'outside.md').exists())


TESTS = [
    test_writes_the_exact_bytes_inside_the_root,
    test_saving_twice_keeps_the_last_save,
    test_a_benign_relative_walk_inside_the_root_still_saves,
    test_refuses_a_path_that_climbs_out_of_the_root,
    test_refuses_an_absolute_path,
    test_refuses_the_denied_subtrees_the_reader_already_refuses,
    test_refuses_an_empty_path,
    test_refuses_a_symlink_that_leaves_the_root,
]

if __name__ == '__main__':
    for _t in TESTS:
        try:
            _t()
        except Exception as e:
            ck(f'{_t.__name__} raised {type(e).__name__}: {e}', False)
    print('FAILURES: ' + ('; '.join(FAILS) if FAILS else 'none'))
    sys.exit(1 if FAILS else 0)
