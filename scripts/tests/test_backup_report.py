#!/usr/bin/env python3
'''t704 Step 1: a read-only survey of the dated bundle folders under a root, including the
ones the tool is blind to.

The only listing backup_corpora has is do_prune, and it appends a folder to its candidates
only when the directory it resolves to holds MANIFEST.sha256. A dated folder with no manifest
therefore appears in NO line of the prune dry-run: not KEEP, not FAILED VERIFY, not 'WOULD
REMOVE'. The seven old folders this task is about are exactly those, so the report's first
bullet cannot be written from the tool today. survey_bundles must report every dated bundle
folder, manifest present or not, with the four facts the report needs -- name, manifest
position, file count, total size -- and a verifies/partial/unreadable verdict.

No live state: a temp-dir fake NAS built with tempfile.mkdtemp, no subprocess, no model call,
and the verdicts come from the real do_verify, so a 'verifies' row is a real verification.
Red on main for one reason: backup_corpora has no survey_bundles().
'''
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backup_corpora as B

VERDICTS = ('verifies', 'partial', 'unreadable')
NAMES = ('ai-workspace-corpora-2026-08-26', 'ai-workspace-corpora-2026-09-02',
         'ai-workspace-corpora-2026-09-05', 'ai-workspace-corpora-2026-09-10')
ok = 0
fail = 0


def check(label, cond):
    global ok, fail
    print('  [%s] %s' % ('ok' if cond else 'FAIL', label))
    if cond:
        ok += 1
    else:
        fail += 1


def dated(root, name, files, manifest='nested', manifest_files=None, sidecars=False):
    '''A dated NAS folder in the shape do_backup writes: name/ai-workspace-corpora/payload
    when manifest is nested; payload and manifest directly in name/ when top; payload with NO
    manifest at all when none. The manifest carries the real sha256 of the bytes written, so a
    verifies verdict is a real verification, not a stub.'''
    d = root / name
    body = d / B.BUNDLE if manifest == 'nested' else d
    for rel, content in files.items():
        p = body / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding='utf-8')
    listed = sorted(files) if manifest_files is None else manifest_files
    if manifest != 'none':
        (body / B.MANIFEST).write_text(
            # A listed file that was never written (a manifest naming a lost file) gets a
            # placeholder digest: hashing it would crash the fixture before the code under test runs.
            ''.join('%s  %s\n' % (B.sha256(body / rel) if (body / rel).exists() else '0' * 64, rel)
                    for rel in listed),
            encoding='utf-8')
    if sidecars:
        (body / '.AppleDouble').mkdir(parents=True, exist_ok=True)
        (body / '.AppleDouble' / 'a.md').write_text('resource fork', encoding='utf-8')
        (body / '.DS_Store').write_text('finder junk', encoding='utf-8')
    return d


def run():
    global ok, fail
    root = Path(tempfile.mkdtemp(prefix='t704-'))
    dated(root, NAMES[0], {'chat/a.md': 'hello', 'library/b.pdf': 'x' * 13},
          manifest='nested', sidecars=True)
    dated(root, NAMES[1], {'chat/a.md': 'hello'}, manifest='top',
          manifest_files=['chat/a.md', 'library/gone.pdf'])
    dated(root, NAMES[2], {'chat/a.md': 'hello'}, manifest='top')
    (root / NAMES[2] / 'chat' / 'a.md').write_text('CHANGED BY A LATER RUN', encoding='utf-8')
    dated(root, NAMES[3], {'chat/a.md': 'hello', 'library/b.pdf': 'x' * 13}, manifest='none')
    live = root / B.LIVE_NAME
    (live / B.BUNDLE).mkdir(parents=True)
    (live / B.BUNDLE / B.MANIFEST).write_text('deadbeef  chat/a.md\n', encoding='utf-8')
    (live / B.BUNDLE / 'a.md').write_text('the live rolling copy', encoding='utf-8')
    (root / 'not-a-bundle').mkdir()
    (root / 'not-a-bundle' / 'notes.md').write_text('not a backup', encoding='utf-8')

    physical = [p for p in (root / NAMES[0]).rglob('*') if p.is_file()]
    check('the 2026-08-26 fixture physically holds 5 files (2 payload, the manifest, 2 NAS sidecars)',
          len(physical) == 5)

    has = hasattr(B, 'survey_bundles')
    if not has:
        print('  backup_corpora has no survey_bundles(): every survey check below is red for that reason')
    real = {}
    if has:
        for row in B.survey_bundles(root, verify=lambda b: B.do_verify(b, strict_cold=False) == 0):
            real[row.get('name')] = row
    names = sorted(real)
    check('every dated bundle folder is reported, including the folder with NO manifest, in name order',
          names == list(NAMES))
    check('the live rolling folder named BUNDLE is never reported', B.LIVE_NAME not in real)
    check('a sibling directory that is not a bundle is never reported', 'not-a-bundle' not in real)
    check('manifest position per folder follows the existing rule: nested, top, top, none',
          [real.get(n, {}).get('manifest') for n in names] == ['nested', 'top', 'top', None])
    check('file count counts the payload only, not the manifest and not the NAS sidecars',
          [real.get(n, {}).get('files') for n in names] == [2, 1, 1, 2])
    check('total size is the bytes on disk: the file rewritten after the backup counts at 22',
          [real.get(n, {}).get('bytes') for n in names] == [18, 5, 22, 18])
    check('verdicts come from the real do_verify: intact, manifest-lists-a-missing-file, corrupted-bytes, no-manifest',
          [real.get(n, {}).get('verdict') for n in names] == ['verifies', 'partial', 'partial', 'unreadable'])
    check('no row carries a verdict outside verifies/partial/unreadable',
          bool(names) and all(real[n].get('verdict') in VERDICTS for n in names))
    always = {}
    never = {}
    if has:
        for row in B.survey_bundles(root, verify=lambda b: True):
            always[row.get('name')] = row
        for row in B.survey_bundles(root, verify=lambda b: False):
            never[row.get('name')] = row
    check('a caller-supplied verify= is honoured: every folder with a manifest is verifies under a verifier that always passes',
          [always.get(n, {}).get('verdict') for n in NAMES[:3]] == ['verifies', 'verifies', 'verifies'])
    check('  ... and a folder with NO manifest is never reported as verifies, even under a verifier that always passes',
          [always.get(n, {}).get('verdict') for n in NAMES[3:]] == ['unreadable'])
    check('a failing verify= maps every folder that has a manifest to partial',
          [never.get(n, {}).get('verdict') for n in NAMES[:3]] == ['partial', 'partial', 'partial'])
    check('survey_bundles deletes nothing: all six directories are still under the root',
          sorted(p.name for p in root.iterdir() if p.is_dir()) ==
          [B.LIVE_NAME, NAMES[0], NAMES[1], NAMES[2], NAMES[3], 'not-a-bundle'])
    check('  and it leaves the payload bytes untouched (the rewritten file is unmodified)',
          (root / NAMES[2] / 'chat' / 'a.md').read_text(encoding='utf-8') == 'CHANGED BY A LATER RUN')
    print('')
    print('%d passed, %d failed' % (ok, fail))
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(run())
