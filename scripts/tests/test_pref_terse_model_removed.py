"""Oracle for t567: the terse-model preference is removed from docs/PREFERENCES.md.

Jas's disposition on t567 (2026-10-04, "Go ahead", answering "Keep it, or remove it?") is to
drop the 2026-08-26 preference for the terse model and to record the disposition in the file
against the task number.

Red on main -- verified by a numbered read of docs/PREFERENCES.md, 2026-10-07:
  :143  **Prefer the terse model for agent work.** `qwen3.8:27b-mtp` used 828 tokens where
  :144  `qwen3.6:35b-a3b` used 4,871 on the same task, and finished sooner despite decoding
        at a third the rate. Tokens-to-answer is the unit that matters, not tokens per second.
The file names no task number for it.

Run standalone: python3 scripts/tests/test_pref_terse_model_removed.py
Reads one repo file. Writes nothing. No model, no network, no subprocess.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFS = ROOT / "docs" / "PREFERENCES.md"

# Quoted verbatim from the read above, so the assertions test the real text and not a paraphrase.
REMOVED_PREFERENCE = "Prefer the terse model for agent work"
REMOVED_EVIDENCE = "used 828 tokens"

# Text that must SURVIVE: the removal is one paragraph, not a slab.
SECTION_HEADING = "## Working with the local model"
PRECEDING_PREFERENCE = "Reasoning stays on for untrusted input"
FOLLOWING_PREFERENCE = "A role finishes what it started"

DISPOSITION_TASK = "t567"


def _text():
    return PREFS.read_text(encoding="utf-8")


def test_preference_is_gone():
    """The preference's own words must not be in the file -- including inside a withdrawal note.

    A marker that quotes the withdrawn sentence keeps it alive for anyone grepping the file,
    which is the one thing a removal has to achieve.
    """
    assert REMOVED_PREFERENCE not in _text(), (
        "docs/PREFERENCES.md still carries the terse-model preference; t567's disposition "
        "is to remove it"
    )


def test_evidence_of_the_preference_is_gone():
    """Deleting only the bold lead-in leaves the token figures orphaned in the paragraph."""
    assert REMOVED_EVIDENCE not in _text(), (
        "the preference's measurement sentence is still in docs/PREFERENCES.md -- the "
        "paragraph at :143-145 goes as a whole"
    )


def test_disposition_is_recorded_in_the_file():
    """t567's CHANGE: 'if removed, edit docs/PREFERENCES.md and record the number'."""
    assert DISPOSITION_TASK in _text(), (
        "docs/PREFERENCES.md records no task number for this disposition, while the file's own "
        "convention is to name the ref that withdrew a rule (see :106 and :159)"
    )


def test_surrounding_rules_survive():
    """A build that takes the section heading, or a neighbour preference, is a wrong build."""
    text = _text()
    for anchor in (SECTION_HEADING, PRECEDING_PREFERENCE, FOLLOWING_PREFERENCE):
        assert anchor in text, (
            "the removal took text outside its own paragraph: %r is gone" % anchor
        )


if __name__ == "__main__":
    _failures = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print("PASS %s" % _name)
            except AssertionError as _exc:
                _failures += 1
                print("FAIL %s: %s" % (_name, _exc))
    print("%d failure(s) in %s" % (_failures, Path(__file__).name))
    sys.exit(1 if _failures else 0)
