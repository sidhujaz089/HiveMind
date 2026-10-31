#!/usr/bin/env python3
'''t1031 -- a phase label has ONE owner; every other use is a generated USES copy.

RCA workspace/rca/315bac37c1c5.md: the label Phase 2 was used by STATE.md and
workspace/ROADMAP.md with no declared owner and no cross-reference. scripts/sync_facts.py states
the gap in its own module docstring: a restatement is a copy, and a copy drifts -- and the
FACT/USES mechanism polices only copies that were MARKED. An unmarked restatement typed fresh is
the exact shape of a phase label.

RED TODAY BECAUSE: scripts/sync_facts.py declares no label registry -- it has no
declared_labels(), no label_uses(), no label_problems(), and evaluate() returns only FACT/USES
drift, never an un-owned label use.

THE CONTRACT, in the order the assertions check it:
  1. declared_labels(repo) -> {label: owner_rel}. A label is declared by a FACT block whose id
     matches phase-<digits>, case-insensitive: FACT:phase-2 and FACT:Phase-2 both declare the
     label Phase 2. A FACT with any other id declares no label.
  2. label_uses(repo) -> [(rel, line, label)]. A use is the text 'Phase <N>' (case-insensitive,
     <N> a run of digits NOT followed by a digit) in a tracked .md file that does not own that
     label, counted once per line, outside fenced code blocks, and outside the lines of a USES
     block (from its <!-- USES: marker line through its <!-- /USES --> line, inclusive).
  3. label_problems(repo) -> [str], one string per use, naming the file, the 1-based line, the
     label and its owner.
  4. evaluate() merges them into `problems`, because check_workspace.py fails on a non-empty
     problems list (its lines 2012-2029) -- that is the existing surface this rides in on, and
     the reason there is no check_workspace.py chunk in this scope.
  5. An undeclared label is NOT policed: the approved set is the declared set (the inversion in
     planning-for-sustainment -- enumerate the good, flag everything else, no label blacklist).

Every fixture repo is `git add`-ed, because sync_facts.tracked_markdown reads `git ls-files`: an
untracked fixture exercises nothing and passes vacuously.

Fixtures live in temp dirs. Nothing in the live repository is read or written.
'''
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]      # repo root, from scripts/tests/
sys.path.insert(0, str(ROOT / 'scripts'))

FAILS = []


def ck(label, cond, detail=''):
    ok = bool(cond)
    mark = 'ok' if ok else 'BAD'
    print('  [' + mark + ']  ' + label + ('  -- ' + detail if detail and not ok else ''))
    if not ok:
        FAILS.append(label)


def mkrepo(files):
    '''A temp dir whose .md files are TRACKED. Raises RuntimeError if git refuses.'''
    d = Path(tempfile.mkdtemp()) / 'repo'
    d.mkdir(parents=True)
    for rel, body in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding='utf-8')
    for args in (['-c', 'init.defaultBranch=main', 'init', '-q'], ['add', '-A']):
        r = subprocess.run(['git', '-C', str(d), *args], capture_output=True,
                           text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError('git ' + ' '.join(args) + ' failed: ' + r.stdout + r.stderr)
    return d


# workspace/ROADMAP.md: owns Phase 2 (FACT at lines 3-5) and USES Phase 1 at line 7.
# Line map: 1 title, 3 FACT open, 4 canonical body, 5 FACT close, 7 un-owned Phase 1 use,
# 9 un-owned Phase 2 use, 11 backticked use in the owner (prose), 13 'Phase 20' (not a use).
ROADMAP = '''# Roadmap

<!-- FACT:phase-2 -->
**Phase 2 — fix what breaks.** The dry-run log is the instrument.
<!-- /FACT -->

**Phase 1 — Family on Drive (now).** 20 real operations.

Blocked on Phase 2, per the audit.

`Phase 2` in backticks is prose the owner may write.

Phase 20 is a separate label.
'''

# docs/PLAN.md: declares the label with a capital P, which proves id normalisation.
PLAN = '''# Plan

<!-- FACT:Phase-1 -->
**Phase 1 — Family on Drive (now).** 20 real operations before arming.
<!-- /FACT -->
'''

# docs/OTHER.md: uses at 3, 9 and 11. Line 6 is inside a fenced block (5-7), line 13 is
# 'Phase 20', lines 15-17 are a USES block whose body has been edited away from the owner.
OTHER = '''# Other

**Phase 2 — fix what breaks.** typed fresh.

```
Phase 2 inside a fenced example.
```

Blocked on Phase 2.

The phase 2 label, lowercase, is the same label.

Phase 20 is a separate label.

<!-- USES:phase-2 FROM workspace/ROADMAP.md -->
**Phase 2 — fix what breaks.** generated copy, do not edit.
<!-- /USES -->

Phase 7 is nobody's label.
'''

EXPECTED_OWNERS = {'Phase 2': 'workspace/ROADMAP.md', 'Phase 1': 'docs/PLAN.md'}
EXPECTED_USES = {
    ('docs/OTHER.md', 3, 'Phase 2'),
    ('docs/OTHER.md', 9, 'Phase 2'),
    ('docs/OTHER.md', 11, 'Phase 2'),
    ('workspace/ROADMAP.md', 7, 'Phase 1'),
}


# The CLEAN fixture: an owner document with NO mention of any label it does not own, and a
# consumer whose USES body is byte-identical to the canonical text. It must evaluate to
# ([], []): if ROADMAP mentions Phase 1 here the fixture itself is the defect, because PLAN
# owns that label. This is the false-positive guard.
CLEAN_ROADMAP = '''# Roadmap

<!-- FACT:phase-2 -->
**Phase 2 — fix what breaks.** The dry-run log is the instrument.
<!-- /FACT -->

Blocked on Phase 2, per the audit.
'''

CONSUMER = '''# Consumer

<!-- USES:phase-2 FROM workspace/ROADMAP.md -->
**Phase 2 — fix what breaks.** The dry-run log is the instrument.
<!-- /USES -->
'''

A_MD = '''# A

Phase 7 is not declared, so nothing owns it.
'''
B_MD = '''# B

**Phase 7 — nobody's.** typed fresh.
'''

BODY2 = '**Phase 2 — fix what breaks.** the same canonical sentence.\n'
OWNER_A = '# A\n\n<!-- FACT:phase-2 -->\n' + BODY2 + '<!-- /FACT -->\n'
OWNER_B = '# B\n\n<!-- FACT:phase-2 -->\n' + BODY2 + '<!-- /FACT -->\n'

DEMO_OWNER = '# Owner\n\n<!-- FACT:demo -->\nThe canonical sentence.\n<!-- /FACT -->\n'
DEMO_CONSUMER = ('# Consumer\n\n'
                 '<!-- USES:demo FROM docs/OWNER.md -->\n'
                 'Something edited by hand.\n'
                 '<!-- /USES -->\n')


def as_triples(uses):
    '''Normalise label_uses() to {(rel, line, label)} and RECORD a record that will not unpack.'''
    out = set()
    for u in uses:
        try:
            rel, line, label = tuple(u)
            out.add((str(rel), int(line), str(label)))
        except Exception as e:
            ck('label_uses() yields (rel, line, label) records', False, repr(u) + ' ' + repr(e))
            return out
    return out


def report():
    for f in FAILS:
        print('FAIL: ' + f)
    print('test_sync_phase_labels: ' + ('FAILED' if FAILS else 'ok'))
    return 1 if FAILS else 0


def main():
    try:
        import sync_facts as SF
    except Exception as e:
        ck('scripts/sync_facts.py imports from the repo scripts/ dir', False, repr(e))
        return report()

    have = {n: getattr(SF, n, None) for n in ('declared_labels', 'label_uses', 'label_problems')}
    for name, fn in have.items():
        ck('scripts/sync_facts.py defines ' + name + '()', callable(fn),
           'absent: the label registry is the change this task asks for')

    try:
        empty = mkrepo({})
        if callable(have['declared_labels']):
            got = dict(SF.declared_labels(empty))
            ck('a repo with no markdown declares no labels', got == {}, repr(got))
        if callable(have['label_uses']):
            got = list(SF.label_uses(empty))
            ck('a repo with no markdown has no label uses', got == [], repr(got))
        if callable(have['label_problems']):
            got = list(SF.label_problems(empty))
            ck('a repo with no markdown has no label problems', got == [], repr(got))
    except Exception as e:
        ck('the label API answers an empty repo without raising', False, repr(e))

    r = None
    try:
        r = mkrepo({'workspace/ROADMAP.md': ROADMAP, 'docs/PLAN.md': PLAN,
                    'docs/OTHER.md': OTHER})
    except Exception as e:
        ck('the 3-file label fixture builds', False, repr(e))

    if r is not None and all(callable(fn) for fn in have.values()):
        try:
            got = dict(SF.declared_labels(r))
            ck('declared_labels() reads both labels and their owners from the FACT markers',
               got == EXPECTED_OWNERS,
               'got ' + repr(sorted(got.items())) + ' want ' + repr(sorted(EXPECTED_OWNERS.items())))
        except Exception as e:
            ck('declared_labels() does not raise', False, repr(e))

        try:
            got = as_triples(SF.label_uses(r))
            ck('label_uses() finds exactly the 4 un-owned uses at their real 1-based line numbers, '
               'and none of the prose of the owning document, the fenced example, the USES copy, '
               'the undeclared label, the lowercase form it must catch, or Phase 20',
               got == EXPECTED_USES, 'got ' + repr(sorted(got)))
        except Exception as e:
            ck('label_uses() does not raise', False, repr(e))

        try:
            ps = list(SF.label_problems(r))
            ck('label_problems() returns one plain string per un-owned use',
               len(ps) == len(EXPECTED_USES) and all(isinstance(p, str) for p in ps),
               str(len(ps)) + ' strings for ' + str(len(EXPECTED_USES)) + ' uses: ' + repr(ps))
            for rel, line, label in sorted(EXPECTED_USES):
                owner = EXPECTED_OWNERS[label]
                ck('a finding names the file, the 1-based line, the label and its owner: '
                   + rel + ':' + str(line),
                   any(rel in p and str(line) in p and label in p and owner in p for p in ps),
                   repr(ps))
            ck('a finding can be cited as docs/OTHER.md:3, the shape the RCA asked for',
               any(p.startswith('docs/OTHER.md:3') for p in ps), repr(ps))
        except Exception as e:
            ck('label_problems() does not raise', False, repr(e))

        try:
            probs, drift = SF.evaluate(r)
        except Exception as e:
            probs, drift = [], []
            ck('evaluate() runs on a labelled repo', False, repr(e))
        try:
            labels = set(SF.label_problems(r))
            ck('evaluate() puts every label_problems() finding into `problems`, the field '
               'check_workspace.py fails on', labels <= set(probs),
               'problems = ' + repr(probs))
            ck('evaluate() reports the 4 label findings and the 1 drifted copy, so the six-line '
               'window of check_workspace.py shows them all',
               (len(probs), len(drift)) == (4, 1),
               'got ' + repr((len(probs), len(drift))) + ' want (4, 1)')
            ck('the drifted USES copy is accounted as drift, not swallowed into label problems',
               any(f[0] == 'docs/OTHER.md' and f[1] == 'phase-2' for f in drift), repr(drift))
        except Exception as e:
            ck('label findings reach evaluate()', False, repr(e))

        try:
            n = SF.apply(r)
            ck('apply() rewrites the drifted USES copy', n == 1, 'apply() returned ' + repr(n))
            after = as_triples(SF.label_uses(r))
            ck('apply() leaves every un-owned label use reported: a structural finding is not '
               'auto-fixed, the position sync_facts.main takes when problems is non-empty',
               after == EXPECTED_USES, repr(sorted(after)))
            probs_after = SF.evaluate(r)[0]
            ck('after apply() the label findings survive: the audit cannot self-silence',
               set(SF.label_problems(r)) <= set(probs_after), repr(probs_after))
        except Exception as e:
            ck('apply() runs on a labelled repo', False, repr(e))

    try:
        rc = mkrepo({'workspace/ROADMAP.md': CLEAN_ROADMAP, 'docs/CONSUMER.md': CONSUMER})
        if callable(have['label_problems']):
            got = list(SF.label_problems(rc))
            ck('a declared label used only through a matching USES block is clean',
               got == [], repr(got))
        pair = tuple(SF.evaluate(rc))
        ck('the sanctioned shape reports no problems and no drift: no false positives',
           pair == ([], []), 'got ' + repr(pair))
    except Exception as e:
        ck('the clean fixture builds and evaluates', False, repr(e))

    try:
        ru = mkrepo({'docs/A.md': A_MD, 'docs/B.md': B_MD})
        if callable(have['label_uses']):
            got = list(SF.label_uses(ru))
            ck('an undeclared label produces no label uses: the approved set is the declared set',
               got == [], repr(got))
        if callable(have['label_problems']):
            got = list(SF.label_problems(ru))
            ck('an undeclared label produces no label problems', got == [], repr(got))
        probs = SF.evaluate(ru)[0]
        ck('a repo with no FACT keeps the zero-facts guard wording and adds no label finding',
           len(probs) == 1 and 'no single-sourced facts' in probs[0]
           and not any('Phase 7' in p for p in probs), repr(probs))
    except Exception as e:
        ck('the undeclared fixture builds and evaluates', False, repr(e))

    try:
        rd = mkrepo({'docs/OWNER-A.md': OWNER_A, 'docs/OWNER-B.md': OWNER_B})
        if callable(have['declared_labels']):
            got = dict(SF.declared_labels(rd))
            ck('a label declared twice keeps its first owner: no merge, no raise',
               got == {'Phase 2': 'docs/OWNER-A.md'}, repr(got))
        if callable(have['label_uses']):
            got = as_triples(SF.label_uses(rd))
            ck('and the file that does not own it counts as an un-owned use at its line 4',
               ('docs/OWNER-B.md', 4, 'Phase 2') in got, repr(sorted(got)))
        probs = SF.evaluate(rd)[0]
        ck('the existing duplicate-fact finding is still reported, naming the id and both files',
           any('phase-2' in p and 'OWNER-A.md' in p and 'OWNER-B.md' in p for p in probs),
           repr(probs))
    except Exception as e:
        ck('the two-owner fixture builds and evaluates', False, repr(e))

    try:
        got = SF.strip_examples('a\n```\nx\n```\nb')
        ck('strip_examples still strips a fenced block', got == 'a\n\nb', repr(got))
        got = SF.strip_examples('a `update_file` b\n```\nx\n```\n')
        ck('strip_examples preserves inline code spans, the L-046 canonical-text bug',
           '`update_file`' in got, repr(got))
        fr = mkrepo({'docs/OWNER.md': DEMO_OWNER, 'docs/CONSUMER.md': DEMO_CONSUMER})
        drift = SF.evaluate(fr)[1]
        ck('a hand-edited USES copy is still caught as drift', len(drift) == 1, repr(drift))
    except Exception as e:
        ck('the drift regression runs', False, repr(e))

    return report()


if __name__ == '__main__':
    sys.exit(main())
