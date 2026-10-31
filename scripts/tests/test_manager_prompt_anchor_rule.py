#!/usr/bin/env python3
"""t717 (2026-10-07): the Manager prompt states no rule requiring an anchor on an implementation
chunk, so every scoper must rediscover it the hard way. 28 scopes were refused for brief-unanchored
between 2026-10-07 and 2026-10-11, 25 of them under prompt template 108aed44e7ad -- which is what
manager_prompt.version() returns today, i.e. the prompt in use has never carried the rule.
A rule stated only in prose is one the next dispatch may legally ignore, so this suite pins the
rule in the prompt text itself (docs/skills/writing-oracles-for-delegation), in the RULES section:
naming the class in the read-side list is a list of things that went wrong, not an instruction.

House style: report(name, got, want); run directly with python3 (pytest is not installed).
No subprocess, no model call, no network, no writes: build_prompt only reads workspace files.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]
import manager_prompt as mp  # noqa: E402

PASSED = FAILED = 0

# The rule, verbatim, as it must read in the prompt's own rules. The three anchors are the three
# scope_lint's unanchored() accepts (scope_lint.py:452 symbols, :454 append, :466 the name.py:N
# citation); "5.7-24k tokens" is the measured cost recorded in scope_lint.py:473 for t502.
ANCHOR_RULE = (
    'EVERY `kind: implementation` chunk carries an anchor to its file, chosen from the three '
    'scope_lint accepts: a `SYMBOLS` entry naming a name defined in the file, a real '
    '`file.py:LINE` citation in single_change / what_is_wrong / behaviour_oracle, or '
    '"append": true'
)


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def norm(text):
    return " ".join(text.split())


def rules_section():
    """PREAMBLE + the brief contract: everything before the read-side recitals. A rule placed after
    'KNOWN FAILURE CLASSES' sits in the list of things that went wrong, not in the instructions."""
    return norm(mp.build_prompt("t717", ROOT).split("KNOWN FAILURE CLASSES")[0])


def test_the_rules_require_an_anchor_on_every_implementation_chunk():
    head = rules_section()
    report("the prompt's rules require an anchor on every implementation chunk", ANCHOR_RULE in head, True)


def test_the_rule_names_the_three_anchors_the_lint_accepts():
    """A rule that does not say what to write is advice. These are the real spellings, taken from
    unanchored() in scope_lint.py:452-466, not placeholders."""
    head = rules_section()
    for anchor in ("SYMBOLS", "`file.py:LINE`", '"append": true'):
        report(f"the anchoring rule names {anchor!r}", anchor in head, True)


def test_the_rule_names_its_enforcement_and_its_cost():
    """The rule must name the class it prevents, say the chunk is REFUSED (the mechanism, not the
    suggestion), and carry the measured cost (scope_lint.py:473) so a scoper can price the edit."""
    head = rules_section()
    report("the rule names the class brief-unanchored", "brief-unanchored" in head, True)
    report("the rule says the chunk is refused", "refused" in head, True)
    report("the rule carries the measured cost 5.7-24k tokens", "5.7-24k tokens" in head, True)


def test_the_rule_is_stated_exactly_once():
    """Two copies drift (L-046): the prompt must carry one statement of the rule."""
    got = norm(mp.build_prompt("t717", ROOT)).count(ANCHOR_RULE)
    report("the anchoring rule appears exactly once", got, 1)


def test_the_footer_carries_the_template_id_scope_quality_reads():
    """Carry assertion (green on main, must stay green): scope-quality rows record prompt_version --
    25 of this class's 28 rows record 108aed44e7ad, which is what version() returns now -- so the
    yield of this change is measurable only while the rendered footer equals version()."""
    v = mp.version()
    report("the rendered footer id is manager_prompt.version()",
           mp.build_prompt("t717", ROOT).rstrip().endswith(f"(prompt template {v})"), True)
    report("version() is a 12-hex template id", len(v) == 12 and all(c in "0123456789abcdef" for c in v), True)


if __name__ == "__main__":
    test_the_rules_require_an_anchor_on_every_implementation_chunk()
    test_the_rule_names_the_three_anchors_the_lint_accepts()
    test_the_rule_names_its_enforcement_and_its_cost()
    test_the_rule_is_stated_exactly_once()
    test_the_footer_carries_the_template_id_scope_quality_reads()
    print(f"{PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)
