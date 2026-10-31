#!/usr/bin/env python3
"""t1177: batch scoping. Tasks that change the same file are picked together (sharing a file with the
FIRST task, read from descriptions not notes), a flow/skill task is never batched, the answer is cut
into one section per claimed task, two lanes can never hold overlapping batches, and BATCH_OFF turns
it off. Temp dirs only; nothing live is claimed."""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]
import scope_batch as sb  # noqa: E402
import scoping_lanes as sl  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


A, B = "scripts/run_tests.py", "scripts/scope_queue.py"          # real files, so target_files keeps them
NOTES = "\n## Notes\n\n**2026-10-14 · dispatch-loop** -- failed; files scripts/dispatch_loop.py\n"
bodies = {
    "t1": f"---\nid: t1\ntitle: x\n---\nchange {A}\n" + NOTES,
    "t2": f"---\nid: t2\ntitle: y\n---\nalso {A} and {B}\n",
    "t3": f"---\nid: t3\ntitle: z\n---\nonly {B}\n",                  # shares with t2, NOT with t1
    "t4": "---\nid: t4\ntitle: w\n---\nno files at all\n" + NOTES,     # dispatch_loop only in its notes
    "t5": f"---\nid: t5\narea: flow\ntitle: Flow change: q\n---\n{A}\n",
    "t6": f"---\nid: t6\ntitle: v\n---\n{A}\n",
    "t7": f"---\nid: t7\ntitle: u\n---\n{A}\n",
}
body = bodies.get
report("a task sharing the first task's file joins it", sb.members("t1", ["t2"], body), ["t1", "t2"])
report("a chain does not join: t3 shares only with t2", sb.members("t1", ["t2", "t3"], body), ["t1", "t2"])
report("a file named only in the NOTES does not count", sb.members("t4", ["t1"], body), ["t4"])
report("a flow-change task is never batched", sb.members("t1", ["t5"], body), ["t1"])
report("at most BATCH_MAX, in queue order", sb.members("t1", ["t2", "t6", "t7"], body)[:sb.BATCH_MAX],
       ["t1", "t2", "t6"][:sb.BATCH_MAX])
report("never more than BATCH_MAX", len(sb.members("t1", ["t2", "t6", "t7"], body)) <= sb.BATCH_MAX, True)

answer = ("preamble the rule forbids\n=== TASK t1 ===\n(c) scope one\n```chunks\n[]\n```\n"
          "=== TASK t9 ===\nnot claimed\n=== TASK t2 ===\n(d) blocked by t1\n")
parts = sb.split(answer, ["t1", "t2", "t3"])
report("one section per claimed task", sorted(parts), ["t1", "t2"])
report("a section keeps its own chunks block", "```chunks" in parts["t1"] and "t9" not in parts["t1"], True)
report("an unclaimed task's section is dropped", "not claimed" in "".join(parts.values()), False)
report("text before the first header is dropped", "preamble" in "".join(parts.values()), False)

q = Path(tempfile.mkdtemp())
report("a whole batch is claimed", sl.claim_batch(["t1", "t2"], pid=None, q=q), True)
report("an overlapping batch is refused", sl.claim_batch(["t3", "t2"], pid=None, q=q), False)
report("and the refused batch keeps nothing", (q / "claims" / "t3").exists(), False)
report("the first batch still holds its claims", sorted(p.name for p in (q / "claims").iterdir()), ["t1", "t2"])

R = str(ROOT)
report("a job's --model names its model", sl.lane_model([f"{R}/scripts/ask_q16.py", "--model", "qwen-x"]), "qwen-x")
report("a test sandbox's copy is not real work",
       sl.lane_model(["/home/x/.cache/hivemind-tmp/run-tests-a/ai-workspace/projects/local-inference/coder.py"]), "")
report("a job with neither flag is counted (unknown)", sl.lane_model([f"{R}/scripts/ask_q16.py", "--prompt", "p"]), None)

off = Path(tempfile.mkdtemp())
report("batching is on by default", sb.batching_on(off), True)
(off / "BATCH_OFF").write_text("off\n")
report("BATCH_OFF turns it off", sb.batching_on(off), False)
sh = (ROOT / "scripts" / "manager_queue.sh").read_text(encoding="utf-8")
report("the queue runs batches through scope_batch and claim-batch",
       all(s in sh for s in ("scope_batch.py pick", "claim-batch", "run_batch", "scope_batch.py split")), True)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
