#!/usr/bin/env python3
r"""t717 (2026-10-07): the Manager prompt's scope-defect rule list is BUILT FROM scope_lint's own
docstring -- manager_prompt._lint_rules (scripts/manager_prompt.py:322-340) parses the
`WHAT IS CHECKED` block with the regex at scripts/manager_prompt.py:328
(`^  ([a-z][a-z-]+)\s{2,}(\S.*)$`, continuation lines at four spaces, :332), and
scope_defect_classes renders `rules.get(c, 'see scripts/scope_lint.py')`
(scripts/manager_prompt.py:387). A check the lint RUNS with no docstring entry therefore reaches
the scoper as a pointer to read a 530-line file: advice, not a rule. Run against main today,
_lint_rules() has no entry for brief-unanchored, the class this task exists for, and none for
brief-shared-target -- those are the two gaps, verified by execution.

House style: report(name, got, want); run directly with python3 (pytest is not installed).
No subprocess, no model call, no network, no writes: imports and dict reads only.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]
import manager_prompt as mp  # noqa: E402
import scope_lint as sl  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def test_the_unanchored_class_has_a_rule_the_scoper_can_act_on():
    """Red on main: rules.get('brief-unanchored') is the empty string, so the prompt renders the
    fallback 'see scripts/scope_lint.py' for the class its scopes are refused for most often."""
    report("brief-unanchored has a rule in scope_lint's docstring", bool(mp._lint_rules().get("brief-unanchored", "")), True)


def test_the_rule_names_the_three_anchors_the_lint_accepts():
    """Not a shape check: the rule must tell the scoper what to WRITE. unanchored() accepts exactly
    three things -- a symbols entry (scope_lint.py:452), append true (scope_lint.py:454), or a
    `name.py:N` citation in the prose (scope_lint.py:466) -- so the documented rule has to name them.
    Real words, not any sentence that mentions anchoring."""
    got = mp._lint_rules().get("brief-unanchored", "")
    for word in ("SYMBOLS", "append", "no line"):
        report(f"the brief-unanchored rule names {word!r}", word in got, True)


def test_the_shared_target_class_is_documented_too():
    """The second class the run reports as undocumented (t458). Named by the run, not invented here:
    a class the lint records with no rule is a class the prompt can only point at the source file."""
    report("brief-shared-target has a rule", bool(mp._lint_rules().get("brief-shared-target", "")), True)


def test_every_class_the_lint_records_is_described_where_the_prompt_reads():
    """THE DECAY TEST for the whole doc-to-prompt mechanism (t066): CHECK_NAMES is the list record()
    writes events for (scope_lint.py:493-495, used at :504-510). Any check added to the lint without
    a matching docstring entry reaches every future scoper as 'see scripts/scope_lint.py'.
    Computed at test time, not stored -- nothing here freezes today's list."""
    rules = mp._lint_rules()
    missing = sorted(c for c in sl.CHECK_NAMES if not rules.get(c))
    report("every CHECK_NAMES class has a documented rule", missing, [])


if __name__ == "__main__":
    test_the_unanchored_class_has_a_rule_the_scoper_can_act_on()
    test_the_rule_names_the_three_anchors_the_lint_accepts()
    test_the_shared_target_class_is_documented_too()
    test_every_class_the_lint_records_is_described_where_the_prompt_reads()
    print(f"{PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)
