#!/usr/bin/env python3
"""Oracle for t1427 -- the merge hashes named in RCA d88412000e43 are git-verified in the
record, not merely transcript-attested.

Why a test at all: the RCA's Trailer states that merge hashes 52477331/93e5202e and the t395
id-reuse lead are "[unverified]". A deferral discharged on a transcript quote alone leaves a
kept-promise phantom (RCA section 2). The facts are checkable in git, so the record must carry
them in git's own words.

NOTHING HERE IS COPIED FROM A FROZEN LIST. The full hash, the subject line, the parent count
and reachability from HEAD are all read from `git` at test time, so:
  - the document cannot pass by reciting a string someone typed once; it must quote what the
    object store says TODAY, and
  - if a hash stops existing, stops being reachable, or gains a parent, the suite goes red by
    itself. That is this fixture's decay test.

The git-fact assertions (1-3) pass on main. The document assertions (4-5) are RED on main for
the missing change: workspace/rca/d88412000e43.md has no section 7 and its Trailer still marks
the hashes unverified.

Runs as: python3 scripts/tests/test_rca_d88412000e43_git_verification.py
(plain python3 -- pytest is not installed). Read-only: reads one markdown file and git
metadata. Writes nothing, spawns nothing that outlives a call, touches no live state.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests/ -> repo root
RCA = ROOT / "workspace" / "rca" / "d88412000e43.md"
TIMEOUT = 20                                        # seconds, on every git call

SECTION = "## 7. Git-verification of the transcript's merge hashes (t1427, 2026-10-19)"

# (short hash, what the transcript names it for, is it a two-parent MERGE commit?)
# The flag is the git fact this scope exists to record: of the transcript's "four merges",
# only 93e5202e (t502) and 1527d817 (t646) have two parents. 52477331, 95bca424 and
# 2fea7f9f are single-parent landings, so the label is false for three of the five.
HASHES = [
    ("52477331", "the p242/t647 landing", False),
    ("93e5202e", "the t502 merge", True),
    ("95bca424", "the related_tests hooks fix", False),
    ("2fea7f9f", "the Manager's Next hub fix", False),
    ("1527d817", "the t646 merge", True),
]


def _git(*args):
    """git in the repo root. Never silent: a non-zero exit is reported with git's own words."""
    return subprocess.run(["git"] + list(args), cwd=str(ROOT), capture_output=True,
                          text=True, timeout=TIMEOUT)


def _norm(s):
    """Compare on ASCII-normalised text. git stores straight quotes; markdown may carry
    typographic ones. Normalise for comparison only, never in the record."""
    return (s.replace("\u2019", "'").replace("\u2018", "'")
             .replace("\u201c", '"').replace("\u201d", '"')
             .replace("\u2014", "--").replace("\u2013", "-"))


def _full(short):
    p = _git("rev-parse", "--verify", short + "^{commit}")
    assert p.returncode == 0, "git cannot resolve %s: %s" % (short, p.stderr.strip())
    return p.stdout.strip()


def _subject(full):
    p = _git("log", "-1", "--format=%s", full)
    assert p.returncode == 0, "git has no subject for %s: %s" % (full, p.stderr.strip())
    return p.stdout.rstrip("\n")


def _parents(full):
    p = _git("log", "-1", "--format=%P", full)
    assert p.returncode == 0, "git has no parents for %s: %s" % (full, p.stderr.strip())
    return [x for x in p.stdout.split() if x]


def _doc():
    assert RCA.is_file(), "missing document: %s" % RCA
    return RCA.read_text(encoding="utf-8", errors="replace")


def test_1_hashes_are_commits_ancestral_to_head():
    """The record's reason for existing: the hashes are real commits on this repository's
    history. A hash the transcript invented, or one that fell out of the branch, fails here."""
    for short, label, _ in HASHES:
        full = _full(short)
        assert len(full) == 40 and full.startswith(short), (
            "%s does not resolve to a unique 40-hex commit, it resolves to %r" % (short, full))
        p = _git("merge-base", "--is-ancestor", full, "HEAD")
        assert p.returncode == 0, (
            "%s (%s) is not an ancestor of HEAD, so it is not on this history; "
            "git says: %s" % (short, label, (p.stdout + p.stderr).strip()))
    print("test_1 ok: all 5 hashes resolve and are ancestors of HEAD")


def test_2_git_subjects_are_the_real_ones_and_all_distinct():
    """Distinct subjects, so a record that pastes one subject onto every row cannot pass."""
    subjects = {}
    for short, label, _ in HASHES:
        s = _subject(_full(short))
        assert s.strip(), "git returned an empty subject for %s (%s)" % (short, label)
        assert s not in subjects, (
            "%s and %s share the subject %r; the fixture cannot tell them apart"
            % (short, subjects.get(s), s))
        subjects[s] = short
    print("test_2 ok: 5 distinct git subjects")


def test_3_merge_flag_matches_parent_count():
    """The finding's central claim, pinned by git: two of the five are merges, three are
    single-parent landings -- and the RECORD must say which, not repeat 'the four merges'."""
    text = _norm(_doc())
    for short, label, is_merge in HASHES:
        full = _full(short)
        parents = _parents(full)
        want = 2 if is_merge else 1
        assert len(parents) == want, (
            "HASHES table says %s (%s) is%s a merge commit, git reports %d parent(s): %s"
            % (short, label, "" if is_merge else " NOT", len(parents), " ".join(parents)))
        lines = [ln for ln in text.splitlines() if short in ln and full in ln]
        assert lines, (
            "the record has no single line naming BOTH %s and its full hash %s; the "
            "transcript names short hashes and a reader needs the two tied together"
            % (short, full))
        joined = " ".join(lines)
        if is_merge:
            assert "merge commit" in joined, (
                "the record must state that %s IS a merge commit (git: 2 parents); line(s): %s"
                % (short, joined[:300]))
        else:
            assert "not a merge" in joined, (
                "the record must state that %s (%s) is NOT a merge commit -- git gives it "
                "%d parent(s) %s, so calling it one of 'the four merges' is false: %s"
                % (short, label, len(parents), parents, joined[:300]))
    print("test_3 ok: parent counts match, and the record labels 2 merges / 3 landings")


def test_4_record_carries_a_git_verified_line_per_hash():
    """RED ON MAIN: no section 7 exists yet, and no line pairs a hash with git's subject.

    Each expectation is COMPUTED from git, so this cannot be satisfied by shape: the recorded
    line has to carry git's verbatim subject for that exact commit."""
    text = _norm(_doc())
    assert SECTION in text, (
        "the record has no heading %r; the task's CHANGE is to append a git-verification "
        "section naming each hash with its git subject" % SECTION)
    missing = []
    for short, label, is_merge in HASHES:
        full = _full(short)
        subject = _norm(_subject(full))
        hits = [ln for ln in text.splitlines()
                if short in ln and full in ln and subject in ln]
        if not hits:
            missing.append("%s (%s) -- expected a line containing the short hash, the full "
                           "hash %s and git's subject %r" % (short, label, full, subject))
    assert not missing, (
        "the record does not carry a git-verified line for every hash; it claims verification "
        "the transcript cannot support (kept-promise phantom):\n  " + "\n  ".join(missing))
    print("test_4 ok: the record quotes git for all 5 hashes")


def test_5_trailer_no_longer_marks_verified_hashes_unverified():
    """RED ON MAIN: the Trailer still says the merge hashes and the task-id lead are unverified.

    Scoped to the Trailer line ONLY. The dead-end and thread ledger rows (lines 63, 66, 90,
    91) record what the authoring pass checked -- that is history and stays untouched."""
    text = _doc()
    trailers = [ln for ln in text.splitlines() if ln.startswith("**Trailer:**")]
    assert trailers, "no line starts '**Trailer:**' -- the trailer was renamed; check by hand"
    for ln in trailers:
        low = _norm(ln).lower()
        for short, label, is_merge in HASHES:
            assert not ("unverified" in low and short in ln), (
                "the Trailer still marks %s (%s) unverified; the new section git-verifies it, "
                "so withdraw it from that line: %s" % (short, label, ln.strip()))
        assert not ("unverified" in low and "id reuse" in low), (
            "the Trailer still marks the t395 id-reuse lead unverified; section 7 settles it, "
            "so withdraw it from that line: %s" % ln.strip())
    print("test_5 ok: the trailer's resolved claims were withdrawn")


if __name__ == "__main__":
    import traceback

    fns = [test_1_hashes_are_commits_ancestral_to_head,
           test_2_git_subjects_are_the_real_ones_and_all_distinct,
           test_3_merge_flag_matches_parent_count,
           test_4_record_carries_a_git_verified_line_per_hash,
           test_5_trailer_no_longer_marks_verified_hashes_unverified]
    failures = 0
    for fn in fns:
        try:
            fn()
        except Exception:
            failures += 1
            print("FAIL %s" % fn.__name__)
            traceback.print_exc()
    print("%d/%d passed" % (len(fns) - failures, len(fns)))
    sys.exit(1 if failures else 0)
