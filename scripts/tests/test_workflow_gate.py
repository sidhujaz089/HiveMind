#!/usr/bin/env python3
"""t097 -- a workflow is not assignable to a role until its L2 (practice) score is recorded.

The Kirkpatrick gate from workspace/tasks/t097--workflow-and-skills-planning-process.md
(claude-code's plan of 2026-10-05, approved by Jas with "Go!"): one file per workflow,
workflows/<name>.md, five fixed sections -- Spec, L1 Reaction, L2 Learning, L3 Behaviour,
L4 Results -- and a workflow cannot be put on a role until its L2 score is recorded.
This suite pins what that gate must answer.

It is written against scripts/session_review_trial.py because the builder (coder.py) can
only edit a file that exists: the first scope of this task named scripts/workflow_gate.py,
which is not on disk, and the preflight refused it. session_review_trial.py is the script
the approved plan names as the pilot, and its real L2 evidence is the trial file
workspace/session-reviews/trials/2026-10-05T194227Z-a15c2172-scripted.json, which records
"right": 4, "of": 8. The fixtures below carry those real values, not placeholders.

RED ON MAIN because session_review_trial.py defines none of the four names this suite needs.
Run with plain python3 (no pytest). Touches only temp dirs: no model call, no subprocess,
no network, no write outside a temp dir, nothing left running.
"""
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

try:
    import session_review_trial as srt
except ImportError as exc:
    sys.stderr.write("FAIL: scripts/session_review_trial.py cannot be imported (%s)\n" % exc)
    raise SystemExit(1)

API = ("WF_SECTIONS", "wf_sections", "wf_l2_score", "wf_ready")
_absent = [n for n in API if not hasattr(srt, n)]
if _absent:
    sys.stderr.write(
        "FAIL: scripts/session_review_trial.py does not define %s -- that is the change\n"
        % ", ".join(_absent))
    raise SystemExit(1)

FAILS = []
CHECKS = [0]


def check(cond, msg):
    CHECKS[0] += 1
    if not cond:
        FAILS.append(msg)
        print("FAIL: %s" % msg)


SPEC = """# session-review

## Spec
Scripted session review: code finds candidate sentences, code searches for covering
tasks and commits, a script answers MISSED/COVERED. Lives in scripts/session_review_trial.py
(--scripted) on top of scripts/session_review.py and scripts/session_candidates.py.

## L1 Reaction
gemma4:26b, 2026-10-05: asked in its own words what it will do; answers in the trial log.

## L2 Learning
L2 score: 4/8 (scripted trial 2026-10-05T194227Z, 8 gold items, judged right 4)

## L3 Behaviour
Escapes: a finding this workflow filed that a later hand-triage showed was not a missed item.

## L4 Results
Watch: gold items found per session (found/of), plus findings surfaced per session.
"""

NO_SCORE = "practice set written, the run is not scored yet"
PROSE_SCORE = "L2 score: recorded after the pilot -- see the trial log"
SCORE_LINE = "L2 score: 4/8 (scripted trial 2026-10-05T194227Z, 8 gold items, judged right 4)"


def _write(d, name, text):
    f = Path(d) / name
    f.write_text(text, encoding="utf-8")
    return f


def test_sections_are_the_five_the_spec_names():
    want = ("Spec", "L1 Reaction", "L2 Learning", "L3 Behaviour", "L4 Results")
    check(tuple(srt.WF_SECTIONS) == want,
          "WF_SECTIONS is %r, expected %r" % (tuple(srt.WF_SECTIONS), want))


def test_sections_return_the_file_s_own_body_text():
    got = srt.wf_sections(SPEC)
    check("4/8" in got.get("L2 Learning", ""),
          "L2 Learning must carry the file's own body text, got %r" % (got.get("L2 Learning"),))
    check("Scripted session review" in got.get("Spec", ""),
          "Spec must carry the file's own body text, got %r" % (got.get("Spec"),))


def test_l2_score_is_parsed_from_the_l2_section():
    check(srt.wf_l2_score(SPEC) == "4/8",
          "wf_l2_score gave %r, expected '4/8'" % (srt.wf_l2_score(SPEC),))
    check(srt.wf_l2_score(SPEC.replace("4/8", "7 / 8")) == "7/8",
          "spaces around the slash are the same score, got %r"
          % (srt.wf_l2_score(SPEC.replace("4/8", "7 / 8")),))


def test_a_complete_workflow_is_ready():
    with tempfile.TemporaryDirectory() as d:
        r = srt.wf_ready(_write(d, "session-review.md", SPEC))
    check(r.get("ok") is True, "a complete workflow with a score must be ready: %r" % (r,))
    check(tuple(r.get("missing", ())) == (),
          "a complete workflow reports missing %r" % (r.get("missing"),))
    check(r.get("l2") == "4/8", "the ready row must carry the parsed score, got %r" % (r.get("l2"),))
    check(r.get("why", "") == "", "a ready workflow must give no reason, got %r" % (r.get("why"),))
    check(r.get("name") == "session-review",
          "the row must name the workflow by its file stem, got %r" % (r.get("name"),))


def test_a_score_outside_l2_learning_is_not_the_l2_score():
    # The gate reads L2 from the L2 section. A score that lives in another section is not it.
    text = SPEC.replace(SCORE_LINE, NO_SCORE)
    text = text.replace("## L1 Reaction",
                        "## L1 Reaction\nL2 score: 4/8 (written in the wrong section)")
    with tempfile.TemporaryDirectory() as d:
        r = srt.wf_ready(_write(d, "shifted.md", text))
    check(r.get("l2") == "",
          "a score written outside L2 Learning is not the L2 score, got %r" % (r.get("l2"),))
    check(r.get("ok") is False,
          "a workflow whose L2 section carries no score must not be ready: %r" % (r,))


def test_prose_claiming_the_practice_run_is_not_a_score():
    # The case the gate exists for: the section SAYS, in words, that the practice run happened.
    text = SPEC.replace(SCORE_LINE, PROSE_SCORE)
    with tempfile.TemporaryDirectory() as d:
        r = srt.wf_ready(_write(d, "claimed.md", text))
    check(r.get("l2") == "",
          "prose that only claims a score must not count, got %r" % (r.get("l2"),))
    check(r.get("ok") is False,
          "prose claiming the practice run must not be ready: %r" % (r,))
    check("L2" in str(r.get("why", "")),
          "the reason must name L2, got %r" % (r.get("why"),))


def test_zero_is_a_recorded_score():
    # A real score of 0/8 is a score: the practice ran and went badly. Absent is not the same as zero.
    text = SPEC.replace("4/8", "0/8")
    with tempfile.TemporaryDirectory() as d:
        r = srt.wf_ready(_write(d, "zero.md", text))
    check(r.get("l2") == "0/8", "0/8 is a recorded score, got %r" % (r.get("l2"),))
    check(r.get("ok") is True,
          "a workflow whose practice run scored 0/8 is still evaluated: %r" % (r,))


def test_a_missing_section_is_named_by_its_spec_name():
    text = SPEC.replace("## L3 Behaviour", "## L3 Live")
    with tempfile.TemporaryDirectory() as d:
        r = srt.wf_ready(_write(d, "renamed.md", text))
    m = tuple(r.get("missing", ()))
    check("L3 Behaviour" in m,
          "renaming L3 Behaviour must report it missing, missing=%r" % (m,))
    check("L3 Live" not in m,
          "a heading the spec does not list is not a section: missing=%r" % (m,))
    check(r.get("ok") is False,
          "a workflow with a section missing must not be ready: %r" % (r,))


def test_two_workflows_keep_their_own_scores():
    with tempfile.TemporaryDirectory() as d:
        a = srt.wf_ready(_write(d, "a.md", SPEC))
        b = srt.wf_ready(_write(d, "b.md", SPEC.replace("4/8", "0/8")))
    check((a.get("l2"), b.get("l2")) == ("4/8", "0/8"),
          "each workflow reports its own score, got %r and %r" % (a.get("l2"), b.get("l2")))
    check(a.get("ok") is True and b.get("ok") is True, "both are ready: %r %r" % (a, b))


def test_a_missing_file_is_reported_not_raised():
    with tempfile.TemporaryDirectory() as d:
        try:
            r = srt.wf_ready(Path(d) / "never-written.md")
        except Exception as exc:
            check(False, "wf_ready() on a missing file raised %s: %s -- a caller cannot catch what it cannot read"
                  % (type(exc).__name__, exc))
            return
    check(r.get("ok") is False, "a workflow with no file is not ready: %r" % (r,))
    check("never-written.md" in str(r.get("why", "")),
          "the reason must name the file: %r" % (r.get("why"),))
    check(tuple(r.get("missing", ())) == tuple(srt.WF_SECTIONS),
          "an unreadable workflow is missing all five sections, got %r" % (r.get("missing"),))


def main():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:
            check(False, "%s raised %s: %s" % (name, type(exc).__name__, exc))
    print("%d/%d checks passed" % (CHECKS[0] - len(FAILS), CHECKS[0]))
    if FAILS:
        print("%d FAILED" % len(FAILS))
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
