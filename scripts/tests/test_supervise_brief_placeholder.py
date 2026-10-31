#!/usr/bin/env python3
"""t1426 -- a rewritten brief carrying an unfilled slot must never reach the builder.

THE RECORD. The task title says "dispatch template placeholder", and there is no template: a
recursive grep for the string pasted below over the whole tree returns hits in artefacts only
(workspace/briefs, supervision.jsonl, scope-quality.jsonl, scope-defects, the task file, the
manager's own queue logs) and in NOT ONE .py file. The text is written by scripts/supervise.py
execute(), REWRITE-BRIEF branch, which validates that the supervisor's text is non-empty and not
identical to the brief -- never that it is FINISHED. The supervisor that authored it had no way
to finish it: ask_q16 is one infer.chat() call with no tool loop, shown the failed brief by
evidence(), asked for "the full replacement brief". A requirement it cannot meet honestly is met
dishonestly: it copied the instruction to itself into the brief. That is this repository's
recorded rule, from briefing-the-local-model.

WHAT WAS PUBLISHED, VERBATIM. workspace/briefs/brief-p671-t1254-nas-backup.txt line 9 is the
bracketed slot below, ON DISK, under line 5 of the same brief: "Everything you reference must
appear in the source below". It was then dispatched and told to edit lines 31, 33, 49, 50 and 32
of a file it had never been shown. That is why REAL_SLOT below is copied from the artefact and
not invented: a synthetic fixture encodes what the author imagined an unfilled slot looks like.

THE GATE, INVERTED (planning-for-sustainment). A blacklist of phrases needs a librarian, and the
2026-10-18 phrasing proves the library goes stale, so the rule is structural: a brief is a
finished artefact, therefore a bracketed ALL-CAPS directive is a finding. The one bracketed
notice a brief is allowed to carry is the loud truncation marker that never-truncate-a-record
requires, and this very module writes it in MIXED case -- the string built at scripts/supervise.py
in evidence() reads "[TRUNCATED -- job log: the first N of M characters are NOT shown; ...]", so
the lowercase letters after TRUNCATED break an all-caps-only class and the marker cannot match.
Test 3 pins that exemption, because a gate that fires on legitimate briefs gets bypassed with
--no-verify inside a week and is then worth less than nothing.

Run: python3 scripts/tests/test_supervise_brief_placeholder.py   (pytest is not installed)
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent   # scripts/tests/ -> repo root
sys.path.insert(0, str(ROOT / "scripts"))

import supervise as SV   # noqa: E402

# Copied verbatim from workspace/briefs/brief-p671-t1254-nas-backup.txt line 9: the real artefact.
REAL_SLOT = "[PASTE THE ENTIRE FILE CONTENTS HERE]"

# A brief a person would accept: source shown, numbered edits, a target suite, and no bracketed
# all-caps directive anywhere in it. Note the shell tests in it are lowercase -- [ -f ... ] -- and
# must not be flagged.
CLEAN_BRIEF = (
    "TASK: t1254\n\n"
    "ONE file only: scripts/nas_backup.sh. The full source is below.\n\n"
    "FULL SOURCE of scripts/nas_backup.sh (lines 1 through end):\n\n"
    "#!/usr/bin/env bash\n"
    "LEDGER=\"$REPO/workspace/backup-runs.jsonl\"\n"
    "row () { [ -f \"$LEDGER\" ] && printf '%s\\n' \"$1\" >> \"$LEDGER\"; }\n\n"
    "EDIT 1 -- after the closing brace of the row helper, insert sig_name and on_signal.\n"
    "EDIT 2 -- keep the existing EXIT trap and add four signal traps.\n\n"
    "DONE WHEN python3 scripts/tests/test_nas_backup_kill_decay.py exits 0\n"
)

# The one bracketed notice a brief is allowed to carry, in the shape this module's own evidence()
# builds: MIXED case, and it says how much was cut and what to do about it.
CLIP_NOTICE = (
    "TASK: t9\n\nFULL SOURCE of scripts/x.py (lines 1 through end):\n\n"
    "[TRUNCATED -- job log: the first 4000 of 9000 characters are NOT shown; the attempt trail "
    "above covers them]\n\n"
    "EDIT 1 -- replace the second line.\n\n"
    "DONE WHEN python3 scripts/tests/test_x.py exits 0\n"
)

FAIL = {"at": "2026-10-18T05:46:08Z", "chunk": "p671-t1254-nas-backup",
        "model": "qwen3.8:27b-mtp-q4_K_M-56k", "exit": 1, "seconds": 9604,
        "brief": "brief-p671-t1254-nas-backup.txt", "brief_sha256": "923060c8"}


def _briefs(tmp, text):
    """A fake briefs tree under tempfile: never a fixed /tmp path, never workspace/briefs."""
    bdir = Path(tmp)
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / FAIL["brief"]).write_text(text, encoding="utf-8")
    return bdir


def test_1_find_flags_the_slot_from_the_published_brief():
    found = SV.find_brief_placeholder(CLEAN_BRIEF + "\n" + REAL_SLOT + "\n")
    assert isinstance(found, list), f"must return a list, got {type(found)}"
    assert found, "the slot copied verbatim from the published brief was not found"
    assert any(REAL_SLOT in f for f in found), f"{found!r} does not contain the slot"


def test_2_find_is_quiet_on_a_finished_brief():
    assert SV.find_brief_placeholder(CLEAN_BRIEF) == [], \
        "a complete brief was flagged as unfinished"


def test_3_find_does_not_flag_a_declared_truncation():
    found = SV.find_brief_placeholder(CLIP_NOTICE)
    assert found == [], (
        f"the loud truncation marker was flagged ({found!r}): blocking the marker that "
        "never-truncate-a-record requires, and that this module's own evidence() writes, would "
        "make the gate fire on legitimate briefs -- so the all-caps class must exclude mixed case")


def test_4_execute_refuses_to_publish_an_unfinished_brief():
    """THE POINT OF THE TASK: the builder must never be handed a brief with a slot in it."""
    old = "TASK: t1254\n\n(the original brief, which named five edits and no source)\n"
    with tempfile.TemporaryDirectory() as tmp:
        bdir = _briefs(tmp, old)
        real = SV.BRIEFS
        SV.BRIEFS = bdir
        try:
            got = SV.execute(FAIL, {"decision": "REWRITE-BRIEF",
                                    "new_brief": CLEAN_BRIEF + "\n" + REAL_SLOT + "\n"})
        finally:
            SV.BRIEFS = real
        assert "REFUSED" in got, f"an unfinished brief was not refused: {got!r}"
        on_disk = (bdir / FAIL["brief"]).read_text(encoding="utf-8")
        assert on_disk == old, (
            "the rewrite was published over the old brief -- the slot reaches the builder:\n"
            + on_disk[:300])
        assert REAL_SLOT not in on_disk, "the published brief still carries the slot"
        # A refusal must leave no archive litter: the gate fires before the archive is made.
        arch = bdir / "archive"
        assert not arch.is_dir() or not list(arch.iterdir()), \
            "a refused rewrite archived the old brief; the refusal must come first"


def test_5_execute_still_publishes_a_finished_brief():
    """The gate adds a refusal; it must not remove the action. t1254's re-brief needs this."""
    old = "TASK: t1254\n\n(the original brief)\n"
    with tempfile.TemporaryDirectory() as tmp:
        bdir = _briefs(tmp, old)
        real = SV.BRIEFS
        SV.BRIEFS = bdir
        try:
            got = SV.execute(FAIL, {"decision": "REWRITE-BRIEF", "new_brief": CLEAN_BRIEF})
        finally:
            SV.BRIEFS = real
        assert "REFUSED" not in got, f"a complete brief was refused: {got!r}"
        assert (bdir / FAIL["brief"]).read_text(encoding="utf-8") == CLEAN_BRIEF + "\n", \
            "a complete rewrite was not written in place"
        arch = bdir / "archive"
        assert arch.is_dir() and list(arch.iterdir()), "the old brief was not archived"
        kept = [p.read_text(encoding="utf-8") for p in sorted(arch.iterdir())]
        assert old in kept, "the archive does not hold the superseded brief"


def test_6_the_pre_existing_refusals_still_fire():
    with tempfile.TemporaryDirectory() as tmp:
        bdir = _briefs(tmp, "TASK: t1254\n")
        real = SV.BRIEFS
        SV.BRIEFS = bdir
        try:
            empty = SV.execute(FAIL, {"decision": "REWRITE-BRIEF", "new_brief": ""})
            same = SV.execute(FAIL, {"decision": "REWRITE-BRIEF", "new_brief": "TASK: t1254"})
            sw = SV.execute(FAIL, {"decision": "SWITCH"})
            pk = SV.execute(FAIL, {"decision": "PARK"})
        finally:
            SV.BRIEFS = real
        assert "REFUSED" in empty, f"an empty rewrite was not refused: {empty!r}"
        assert "REFUSED" in same, f"an identical rewrite was not refused: {same!r}"
        assert "recorded" in sw, f"SWITCH changed meaning: {sw!r}"
        assert "parked" in pk, f"PARK changed meaning: {pk!r}"


if __name__ == "__main__":
    fails = []
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_") and callable(_f):
            try:
                _f()
                print(f"  ok   {_n}")
            except Exception as e:  # noqa: BLE001 -- a crash is a failure, never a pass
                fails.append(_n)
                print(f"  FAIL {_n}: {type(e).__name__}: {e}")
    print(f"\n{len(fails)} failed")
    sys.exit(1 if fails else 0)
