#!/usr/bin/env python3
"""t1568: an assigned-item answer with a chunks block reaches bridge_trigger.run()."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "projects" / "app-repo"))


def test_assigned_answer_reaches_bridge():
    """run() must call bridge_trigger.run() when the model's answer carries a chunks block."""
    import assigned_worker
    import bridge_trigger
    import taskstore
    import role_store
    import skills

    calls = []

    def fake_bridge(answer_file, task, model, **kw):
        calls.append((str(answer_file), task, model))
        return 0

    def fake_ask(prompt, model, role, tools):
        answer = (
            "Scope for the task.\n\n"
            "```chunks\n"
            '[{"kind": "oracle", "file": "scripts/tests/test_foo.py", '
            '"oracle_text": "print(1)", "red_because": "new"}]\n'
            "```\n\n"
            "OUTCOME: done\n"
        )
        return 0, answer

    task_id = "t99999"
    fake_d = {"roles": {"manager": {"kind": "model", "name": "Manager",
                                     "model": "gemma4:26b", "tools": {}}}}

    orig_find = taskstore.find
    orig_add_note = taskstore.add_note
    orig_set_meta = taskstore.set_meta
    orig_assign_order = role_store.assign_order
    orig_with_skills = skills.with_skills
    orig_bridge_run = bridge_trigger.run
    orig_state = assigned_worker._state
    orig_save = assigned_worker._save_state

    taskstore.find = lambda tid: ({"id": task_id, "needs": "manager", "status": "queued"}, None)
    taskstore.add_note = lambda tid, text, who="": None
    taskstore.set_meta = lambda tid, **kw: None
    role_store.assign_order = lambda d: ["manager", "claude"]
    skills.with_skills = lambda prompt, task, **kw: (prompt, [])
    bridge_trigger.run = fake_bridge
    assigned_worker._state = lambda tid: {"unusable": []}
    assigned_worker._save_state = lambda tid, st: None

    try:
        assigned_worker.run(task_id, "manager", "gemma4:26b", ask=fake_ask, d=fake_d)
    finally:
        taskstore.find = orig_find
        taskstore.add_note = orig_add_note
        taskstore.set_meta = orig_set_meta
        role_store.assign_order = orig_assign_order
        skills.with_skills = orig_with_skills
        bridge_trigger.run = orig_bridge_run
        assigned_worker._state = orig_state
        assigned_worker._save_state = orig_save

    assert calls, ("bridge_trigger.run was never called -- "
                   "an assigned-item chunks block has no route to a builder")
    assert calls[0][1] == task_id, f"bridge called for wrong task: {calls[0]}"


if __name__ == "__main__":
    test_assigned_answer_reaches_bridge()
    print("PASS: assigned-item answer reaches bridge_trigger")
