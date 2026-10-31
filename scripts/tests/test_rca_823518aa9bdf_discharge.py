"""t1063: the RCA for finding 823518aa9bdf must carry the verbatim evidence its
own S4 dead-end ledger says is missing.

D1/D3 asked for the tool_use input.command that discharged the promise at
2026-09-02T01:16Z; D2 asked whether the 10-12 swift RCA for this finding
completed. Both are answerable from files on this box, and both are recorded
in workspace/rca/823518aa9bdf.md as [unverified] (line 90) with nothing under
them. This suite pins that the verbatim text is IN the finding's RCA, and that
the [unverified] marker is retired once it is.

Red on main for the missing append, not for a bug in this test: none of the
six anchors below occurs anywhere in the 105 lines of workspace/rca/823518aa9bdf.md.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RCA = ROOT / "workspace" / "rca" / "823518aa9bdf.md"

# Verbatim substrings of message.content[0].input.command at
# ~/.claude/projects/-home-user-ai-workspace/aeef1f57-5a5c-47b9-9d13-6a9c6fc9b373.jsonl
# row 2751 (type=assistant, name=Bash, id=toolu_01Lyuxpeg6VBKp3R64rPRG7j,
# timestamp 2026-09-02T01:16:37.620Z). Chosen because they are the parts of the
# command that decide what the tool result actually was, and because none of them
# occurs in the file today.
D1_ANCHORS = [
    "toolu_01Lyuxpeg6VBKp3R64rPRG7j",
    "/home/user/env3/sa/workspace/vacuous-registry.json",
    "diff <(git -C ~/env3/sa diff --stat|tail -1) <(git -C ~/env3/sb diff --stat|tail -1)",
]

# Verbatim substrings of the last three lines of
# workspace/run-logs/dispatch/823518aa9bdf.json--rca--swift--1791778943.log
# (the file is 44 lines, so 'tail -80' is the whole of it).
D2_ANCHORS = [
    "done_reason=stop 16 min 22140 chars (11 tool round(s))",
    "wrote /tmp/session-review-lo50pk_2/answer.md  (22140 chars, complete=True)",
    "tasks filed: t607, t1039, t1046, t950, t1028, t1048, t1054, t1041, t815, t1051, t1035, t737, t1063",
]


def _text():
    if not RCA.is_file():
        raise AssertionError(f"missing {RCA}")
    return RCA.read_text(encoding="utf-8")


def test_rca_file_exists():
    assert RCA.is_file(), f"{RCA} is not a file"


def test_discharge_evidence_section_present():
    """The evidence has a findable home, not a scattered sentence."""
    t = _text()
    assert "## 7. Discharge evidence" in t, \
        "no section-7 heading: the verbatim evidence was not appended as a block"


def test_command_that_discharged_the_is_verbatim():
    """D1: the command text, not a paraphrase of it.

    Asserted on the substrings that make the command decidable: the tool_use id
    (unique to this call) and the two paths the command reads and diffs. A brief
    that pasted a paraphrase -- 'ran the registry check on both trees' -- passes
    none of these.
    """
    t = _text()
    for anchor in D1_ANCHORS:
        assert anchor in t, f"D1 verbatim anchor missing from {RCA}: {anchor!r}"


def test_swift_rca_log_tail_is_verbatim():
    """D2: the completion of the 10-12 swift RCA, asserted from the log's own words."""
    t = _text()
    for anchor in D2_ANCHORS:
        assert anchor in t, f"D2 log-tail anchor missing from {RCA}: {anchor!r}"


def test_the_unverified_marker_is_retired():
    """The RCA must stop claiming these two reads are unverified once they are here.

    The whole reason this finding exists is a record that asserted a fact from a
    fragment and left the gap unlabelled. Evidence appended without retiring the
    marker leaves the document asserting two things it has now proved.
    """
    t = _text()
    assert "[unverified]" not in t, \
        "line 90 still marks the discharging command and the RCA log tail as [unverified]"


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failed += 1
                print(f"FAIL {name}: {e}")
    print(f"{failed} failed")
    sys.exit(1 if failed else 0)
