#!/usr/bin/env python3
"""The bridge freeze's safety switch, run by the Manager instead of waiting for a person.

    python3 scripts/freeze_circuit.py status      # on/off, why, and the open circuit task if any
    python3 scripts/freeze_circuit.py ensure      # file the Manager's task if the switch is off
    python3 scripts/freeze_circuit.py reset "why" [--by who]   # what the Manager's decision applies

WHAT IT IS. bridge_trigger.py merges a lint-clean scope into main by the gates (the oracle freeze,
D-35). It switches that off by itself when 3 of the last 20 merge verdicts sent work back for an
oracle defect -- the signal that the gates are passing weak oracles -- and until 2026-10-01 it said
"Claude freezes until Jas resets it". Nobody could: Jas has no reset, and Claude's merges of model
branches are refused as unreviewed. Production ran dry for a day with clean scopes stacked up.

WHAT CHANGED (Jas, 2026-10-01: "Can that step be ... assigned to the Manager to manage? Then it can
review, do an RCA, then reset. I don't need to be in the loop. It can make a task assigned to me
and send my phone a notification about that if it needs me, but I would prefer it use its own
judgement"):
  * when the switch is off, `ensure` files ONE task for the Manager, at the top of the queue, with
    the merge verdicts that tripped it and what is asked: a root-cause analysis, then a decision;
  * the Manager ends its answer with a ```freeze-decision block:
        {"reset": true,  "why": "<the RCA's conclusion>"}            -> switch back on, task closed
        {"reset": false, "why": "...", "needs_jas": "<question>"}    -> stays off, task to Jas +
                                                                        a phone notification
        {"reset": false, "why": "...", "fix": "<what must change first>"} -> stays off, the task
                                                                        stays the Manager's
  * a RESET does not delete history: it records {at, by, why} and the switch counts only merge
    verdicts AFTER it, so the same three cannot trip it again and three NEW ones still can.
"""
import json
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESET = REPO / "workspace" / "run-logs" / "bridge-freeze-reset.json"
TITLE = "Freeze circuit tripped: review the weak oracles, then reset or keep off"
BLOCK = re.compile(r"```[ \t]*freeze-decision[ \t]*\n(.*?)\n```", re.S | re.I)


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def last_reset(path=None):
    try:
        return json.loads(Path(path or RESET).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def reset(why, by="manager", path=None):
    row = {"at": _now(), "by": by, "why": why}
    p = Path(path or RESET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(row, indent=1) + "\n", encoding="utf-8")
    with (p.parent / "bridge-freeze-resets.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row


def _ts():
    sys.path.insert(0, str(REPO / "projects" / "app-repo"))
    import taskstore
    return taskstore


def open_task():
    """The open circuit task's id, or None."""
    for m, _b in _ts().ordered():
        if m.get("title") == TITLE:
            return m.get("id")
    return None


def is_circuit(tid):
    meta, _ = _ts().find(tid)
    return bool(meta) and meta.get("title") == TITLE


def ensure():
    """File the Manager's task when the switch is off for weak oracles. Returns its id, or None."""
    sys.path.insert(0, str(REPO / "scripts"))
    import bridge_trigger
    import scope_quality
    on, why = bridge_trigger.freeze_enabled()
    if on or "oracle defect" not in why:
        return None                       # on, or off by the manual switch file -- not ours to undo
    have = open_task()
    if have:
        return have
    since = last_reset().get("at", "")
    bad = [r for r in scope_quality.rows() if r.get("stage") == "merge"
           and str(r.get("class", "")).startswith("oracle-") and str(r.get("at", "")) > since]
    ev = "\n".join(f"- {r.get('at')} {r.get('task')}: {r.get('class')} -- {r.get('evidence')}" for r in bad)
    body = (f"The bridge's automatic freeze is OFF: {why}.\n\nWhile it is off, every lint-clean scope "
            f"stops as a bridge/<task> branch and nothing reaches the builder.\n\nThe merge verdicts that "
            f"tripped it:\n{ev}\n\nDo a root-cause analysis: why did the gates (scope_lint, bridge.validate) "
            f"pass these oracles, and is that still true? Then end your answer with ONE block:\n\n"
            "```freeze-decision\n{\"reset\": true, \"why\": \"<your conclusion>\"}\n```\n\n"
            "or reset false with \"fix\" (what must change first; the task stays yours) or \"needs_jas\" "
            "(a question only he can answer; he gets the task and a phone notification). Use your own "
            "judgement -- Jas has said he would rather you decide.")
    ts = _ts()
    tid = ts.add(TITLE, "workspace", body=body, source="freeze_circuit.py", at_top=True, kind="harness")
    return tid


def answer(tid, answer_path):
    """Apply the Manager's ```freeze-decision. (status, needs) or None when it decided nothing."""
    text = Path(answer_path).read_text(encoding="utf-8", errors="replace") if Path(answer_path).is_file() else ""
    ts = _ts()
    m = BLOCK.findall(text)
    try:
        d = json.loads(m[-1]) if m else None
    except ValueError:
        d = None
    if not isinstance(d, dict) or "reset" not in d:
        ts.add_note(tid, "The answer had no readable ```freeze-decision block, so nothing was decided. "
                         f"Answer: {answer_path}", who="freeze-circuit")
        return None
    why = str(d.get("why") or "").strip()
    if d.get("reset") is True:
        reset(why or "(no reason given)", by="manager")
        ts.add_note(tid, f"RESET by the Manager: {why}. The freeze counts only merge verdicts after "
                         f"this; undo by deleting {RESET}.", who="freeze-circuit")
        ts.set_status(tid, "done")
        return "done", ""
    if d.get("needs_jas"):
        ts.add_note(tid, f"Kept OFF by the Manager: {why}\nQuestion for Jas: {d['needs_jas']}", who="freeze-circuit")
        ts.set_meta(tid, needs=__import__("role_store").escalate("vision"))
        try:
            sys.path.insert(0, str(REPO / "scripts"))
            import notify
            notify.send("needs-you", "The Manager needs you", str(d["needs_jas"])[:180], url="/review")
        except Exception as e:                                      # noqa: BLE001 -- said, not swallowed
            ts.add_note(tid, f"Phone notification failed: {type(e).__name__}: {e}", who="freeze-circuit")
        return "doing", "jas"
    ts.add_note(tid, f"Kept OFF by the Manager: {why}\nWhat must change first: {d.get('fix') or '(not said)'}",
                who="freeze-circuit")
    return "doing", ""


def main(argv):
    if argv[:1] == ["status"]:
        sys.path.insert(0, str(REPO / "scripts"))
        import bridge_trigger
        on, why = bridge_trigger.freeze_enabled()
        print(f"freeze {'ON' if on else 'OFF'}{': ' + why if why else ''}")
        print(f"last reset: {last_reset() or 'never'}")
        print(f"open task: {open_task() or 'none'}")
        return 0
    if argv[:1] == ["ensure"]:
        print(ensure() or "nothing to file")
        return 0
    if argv[:1] == ["reset"] and len(argv) > 1:
        by = argv[argv.index("--by") + 1] if "--by" in argv else "claude-code"
        print(reset(argv[1], by=by))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
