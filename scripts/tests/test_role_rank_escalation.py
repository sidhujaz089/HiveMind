#!/usr/bin/env python3
"""The org chart (Jas, 2026-10-19): a role's rank is its position in ROLES.json, escalation goes
only UP, to a position kept in the same file, and nothing names a role in code.

Every case runs on a fixture chart, so renaming or deleting a role is shown to change routing with
no code edit.

Run: python3 scripts/tests/test_role_rank_escalation.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import role_store as rs  # noqa: E402

ORG = ["jas", "claude", "dsh", "manager", "auditor", "coder", "embedder"]


KIND = {"jas": "person", "claude": "person", "dsh": "agent"}


def chart(order=ORG, esc=None):
    d = {"roles": {k: {"name": k.title(), "kind": KIND.get(k, "model"), "tasks": []} for k in order}}
    if esc is not None:
        d["_escalation"] = esc
    return d


def raises(fn):
    try:
        fn()
    except rs.NoEscalation:
        return True
    return False


def test_rank_is_the_file_order():
    assert rs.rank(chart()) == ORG
    assert rs.position("jas", chart()) == 1 and rs.position("embedder", chart()) == 7


def test_every_class_goes_to_the_drop_down_role():
    # Jas, 2026-10-27: "Lets make it simple. All rework, technical, or vision escalations go to the
    # role in the drop down." This replaces the 2026-10-20 "+1 from the rank that built it" rule.
    d = chart()
    assert rs.escalate("vision", "auditor", d) == "jas"
    # Jas, 2026-10-27: "All rework, technical, or vision escalations go to the role in the drop down."
    # This used to assert "manager" (one rank up from the auditor). The drop-down says technical is
    # position 2, so it is Claude -- including when an auditor asks.
    assert rs.escalate("technical", "auditor", d) == "claude"
    assert rs.escalate("rework", "auditor", d) == "manager"
    assert rs.escalate("rework", "coder", d) == "manager"      # was "auditor" under the +1 rule


def test_with_no_asker_the_class_position_holds():
    d = chart()
    assert (rs.escalate("vision", None, d), rs.escalate("technical", None, d)) == ("jas", "claude")


def test_never_back_to_the_same_seat():
    # the Manager's own failed answer: rework is position 4 = the Manager, so it goes up -- past the
    # DeepSeek Harness (an agent) to Claude (Jas, 2026-10-19)
    assert rs.escalate("rework", "manager", chart()) == "claude"


def test_the_drop_down_wins_even_when_it_is_below_the_asker():
    # rework is position 3 = Manager; before 2026-10-27 an asker above it was sent to the nearest role
    # ABOVE instead (this asserted "jas"). The drop-down is now the answer.
    assert rs.escalate("rework", "claude", chart()) == "manager"


def test_at_the_top_a_technical_escalation_still_goes_to_the_drop_down_role():
    # Jas is position 1. His own technical escalation goes to Claude (position 2); his VISION question
    # stays with him, because there is nowhere above him and that is the point of the class.
    assert rs.escalate("technical", "jas", chart()) == "claude"
    assert rs.escalate("vision", "jas", chart()) == "jas"


def test_a_renamed_role_is_followed_without_code():
    # the chart is followed by POSITION, never by name: this fixture stores rework at position 4, and
    # "lead" is whoever sits there
    order = ["jas", "claude", "dsh", "lead", "auditor", "coder", "embedder"]
    assert rs.escalate("rework", "auditor", chart(order)) == "lead"


def test_a_position_past_the_end_raises_rather_than_guess():
    assert raises(lambda: rs.escalate("rework", None, chart(["jas", "claude"])))


def test_an_unknown_class_raises():
    assert raises(lambda: rs.escalate("budget", "auditor", chart()))


def test_a_role_not_on_the_chart_raises():
    assert raises(lambda: rs.escalate("vision", "senior", chart()))


def test_the_stored_table_wins_over_the_default_when_no_role_asks():
    d = chart(esc={"vision": 1, "technical": 3, "rework": 5})
    assert rs.escalate("technical", None, d) == "dsh"
    assert rs.escalate("rework", None, d) == "auditor"


def test_moves_reorder_like_tasks():
    assert rs._reordered(ORG, "embedder", "top") == ["embedder"] + ORG[:-1]
    assert rs._reordered(ORG, "auditor", "up")[3] == "auditor"
    assert rs._reordered(ORG, "jas", "down")[:2] == ["claude", "jas"]
    assert rs._reordered(ORG, "coder", "before", "manager")[3] == "coder"


def test_assign_order_is_rank_order():
    assert rs.assign_order(chart()) == ORG


def test_a_new_role_lands_at_the_bottom():
    src = Path(rs.__file__).read_text(encoding="utf-8")
    body = src[src.index("def _add_role"):src.index("def delete_role")]
    assert 'd["roles"][key] = r' in body, "a new role must be appended (the bottom of the chart)"


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    bad = 0
    for n, f in tests:
        try:
            f()
            print(f"PASS {n}")
        except Exception as e:                                      # noqa: BLE001
            bad += 1
            print(f"FAIL {n}: {type(e).__name__}: {e}")
    print(f"{len(tests) - bad} passed, {bad} failed")
    sys.exit(1 if bad else 0)
