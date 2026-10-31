#!/usr/bin/env python3
"""D.2.2 + D.3.1 (RCA D-production-stall-rca.md): an UNTRACKED suite the scheduler refuses at
dispatch, with a reason naming it, and ONE owner task -- instead of every sandbox refusing.

WHAT HAPPENED (2026-10-21, measured): a scoping model on t451 wrote scripts/tests/test_chat_lane.py
into the live checkout with `cp` (manager-actions.jsonl:31186-31187) -- untracked, never frozen,
never on a branch. coder.py copies untracked-not-ignored files into every sandbox (coder.py:381-384,
by design, 174d2e8d5), the suite was red there, and every build refused to start: **19 refusals,
21:50Z -> 00:49Z, 0 accepted, 0 tasks created** (workspace/run-logs/*.log, "undeclared failure").
dispatch_loop.main_red_hold's 2026-10-12 narrowing correctly stopped an untracked file from HOLDING
the line ("an untracked file is not main") -- and then dropped it in silence: no reason at dispatch
naming the file, no task owning it, 19 x 5 minutes of baseline burned to learn the same thing.

The fix, from the RCA's D.2.2/D.3.1: (a) classify() refuses to enrol a chunk whose TARGET suite is
untracked, naming the file and the sanctioned route (the task's chunks block); (b) main_red_hold
routes untracked suites named by refusals to red_on_main.handle_untracked(), which files ONE owner
task per suite (ledger event "untracked-oracle") -- and they never hold the line.

Isolation: temp log files under the house pattern of test_main_red_hold.py (refusal_blockers
resolves a log path relative to REPO); a stub red_on_main module in sys.modules -- the LIVE ledger
(workspace/red-on-main.jsonl) and the live task store are never written. No real suite is run
(red_of is a stub).
"""
import json
import subprocess
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import dispatch_loop as D  # noqa: E402
import red_on_main as R    # noqa: E402

fails = []


def ck(label, got, want):
    print(f"  [{'ok' if got == want else 'FAIL'}]    {label} -> {got!r}")
    if got != want:
        fails.append(f"{label}: expected {want!r}, got {got!r}")


def main():
    # ---- D.3.1 helper: split a refusal map into untracked suites ------------------------------
    have = {n: hasattr(D, n) for n in ("untracked_oracles", "untracked_target", "tracked_tests")}
    ck("dispatch_loop exposes the untracked helpers (D.2.2/D.3.1)",
       all(have.values()), True)
    if not all(have.values()):
        print(f"       missing: {[n for n, v in have.items() if not v]}")

    if hasattr(D, "untracked_oracles"):
        ck("a suite NOT in git is untracked",
           D.untracked_oracles({"test_zz_probe_untracked.py": "p1"}, {"scripts/tests/test_a.py"}),
           {"test_zz_probe_untracked.py": "p1"})
        ck("a suite tracked in git is not untracked",
           D.untracked_oracles({"test_a.py": "p1"}, {"scripts/tests/test_a.py"}), {})

    if hasattr(D, "untracked_target"):
        ck("classify's predicate: untracked target suite",
           D.untracked_target("scripts/tests/test_zz_probe_untracked.py", {"scripts/tests/test_a.py"}), True)
        ck("  tracked target suite is a real build",
           D.untracked_target("scripts/tests/test_a.py", {"scripts/tests/test_a.py"}), False)
        ck("  a target outside scripts/tests is a file, not an oracle",
           D.untracked_target("scripts/check_workspace.py", set()), False)
        ck("  git cannot answer (None) is not a defect (fail open)",
           D.untracked_target("scripts/tests/test_a.py", None), False)

    # ---- D.3.1: main_red_hold routes untracked suites to red_on_main and holds NOTHING --------
    stub = types.ModuleType("red_on_main")
    seen = {"untracked": [], "resolved": []}
    ledger = []                                    # the stub's ledger rows (never the live one)
    n_task = [0]

    def _stub_hu(untracked, **k):
        out = []
        for suite in sorted(untracked):
            n_task[0] += 1
            out.append((suite, f"t9{n_task[0]}"))
        seen["untracked"].append(dict(untracked))
        return out

    stub.handle_untracked = _stub_hu
    stub.open_untracked = lambda **k: {r["suite"]: r for r in ledger if r.get("event") == "untracked-oracle"}
    stub.resolve_untracked = lambda s, task=None, **k: seen["resolved"].append((s, task))
    stub.handle = lambda red, **k: list(red)
    stub.WINDOW_S = 3600
    real_rom = sys.modules.get("red_on_main")
    sys.modules["red_on_main"] = stub
    real_actions = D.RED_ACTIONS
    try:
        with tempfile.TemporaryDirectory(dir=REPO / "workspace") as d:
            d = Path(d)
            (d / "untracked.log").write_text(
                "  REFUSING TO START -- the sandbox baseline is not what is declared:\n"
                "    undeclared failure: test_zz_probe_untracked.py\n")
            rel = lambda n: str((d / n).relative_to(REPO))
            hist = [{"event": "started", "chunk": "p1", "pid": 1, "log": rel("untracked.log")},
                    {"event": "finished", "chunk": "p1", "pid": 1, "exit": 2, "kind": "production"}]
            if not hasattr(D, "main_red_hold"):
                fails.append("no main_red_hold to test")
                return 1
            D.RED_ACTIONS = True
            held = D.main_red_hold(hist, lambda t: True)
            ck("an untracked named suite does NOT hold the line", held, None)
            # NO GIT, NO VERDICT (2026-10-23): a coder sandbox is a copy, not a checkout, so
            # tracked_tests() is None and every git-dependent check here fails open by design. Run
            # unguarded, these checks refused EVERY build ("undeclared failure: test_untracked_suite.py").
            git_ok = D.tracked_tests() is not None
            if git_ok:
                ck("  and is handed to red_on_main with the refusing chunk",
                   seen["untracked"], [{"test_zz_probe_untracked.py": "p1"}])
            else:
                print("  [skip]  untracked hand-off: git cannot answer here (the coder sandbox)")

            # A TRACKED red suite must still hold (the 2026-10-01/02 rule, unchanged).
            tracked_set = D.tracked_tests() if hasattr(D, "tracked_tests") else None
            allowed = set(json.loads((REPO / "workspace" / "test-failures-ceiling.json")
                                     .read_text(encoding="utf-8")).get("allowed_to_fail", []))
            import re as _re
            tracked_name = sorted(Path(p).name for p in (tracked_set or set())
                                  if _re.fullmatch(r"test_\w+\.py", Path(p).name)
                                  and Path(p).name not in allowed)[:1]
            if tracked_name:
                (d / "tracked.log").write_text(
                    "  REFUSING TO START -- the sandbox baseline is not what is declared:\n"
                    f"    undeclared failure: {tracked_name[0]}\n")
                hist2 = [{"event": "started", "chunk": "p2", "pid": 2, "log": rel("tracked.log")},
                         {"event": "finished", "chunk": "p2", "pid": 2, "exit": 2, "kind": "production"}]
                held2 = D.main_red_hold(hist2, lambda t: True)
                ck("a TRACKED red suite still holds every build (regression)",
                   bool(held2 and tracked_name[0] in held2), True)
                ck("  and is NOT routed as untracked",
                   any(tracked_name[0] in u for u in seen["untracked"]), False)
            else:
                print("  [skip]  tracked-suite hold: git has no tracked tests here (the coder sandbox)")

            # RESOLVE: a suite the scheduler filed (open oracle row in the ledger) that git NOW
            # tracks must be resolved with its task id (the file was committed -- the loop closes).
            ledger.append({"event": "untracked-oracle", "suite": "test_main_red_hold.py", "t": 1000.0,
                           "task": "t999"})

            def _stub_open(**k):
                last = {}
                for r in ledger:
                    if r.get("event") in ("untracked-oracle", "untracked-resolved"):
                        last[r["suite"]] = r
                return {k2: v for k2, v in last.items() if v.get("event") == "untracked-oracle"}

            stub.open_untracked = _stub_open
            D.main_red_hold([], lambda t: True)
            if git_ok:
                ck("a filed suite that git now tracks is RESOLVED, with its task",
                   seen["resolved"], [("test_main_red_hold.py", "t999")])
            else:
                print("  [skip]  resolve on track: git cannot answer here (the coder sandbox)")
    finally:
        D.RED_ACTIONS = real_actions
        sys.modules["red_on_main"] = real_rom if real_rom is not None else stub and None
        sys.modules.pop("red_on_main", None) if real_rom is None else sys.modules.update(red_on_main=real_rom)

    # ---- D.2.2: classify() refuses an untracked TARGET at dispatch, naming the file -----------
    if hasattr(D, "classify") and D.tracked_tests() is None:
        print("  [skip]  classify untracked-target refusal: git cannot answer here (the coder sandbox)")
    elif hasattr(D, "classify"):
        with tempfile.TemporaryDirectory(dir=REPO / "workspace") as d:
            d = Path(d)
            bdir = REPO / "workspace" / "briefs"
            bdir.mkdir(parents=True, exist_ok=True)
            brief_name = f"brief-zz-untracked-suite-test.txt"
            (bdir / brief_name).write_text("TASK: t99999\nSYMBOLS: none\n", encoding="utf-8")
            try:
                chunk = {"chunk": "p99-untracked", "brief": brief_name,
                         "target": "scripts/tests/test_zz_probe_untracked.py", "files": ""}
                _nr, _ready, skipped = D.classify([chunk], [], lambda t: True, history=[])
                why = dict(skipped).get("p99-untracked", "")
                ck("classify refuses a chunk whose target suite is untracked",
                   ("REFUSED" in why, "test_zz_probe_untracked.py" in why, "chunks block" in why),
                   (True, True, True))
                ck("  and starts nothing", _ready, [])
                chunk2 = {"chunk": "p99-tracked", "brief": brief_name,
                          "target": "scripts/tests/test_main_red_hold.py", "files": ""}
                _nr2, _ready2, skipped2 = D.classify([chunk2], [], lambda t: True, history=[])
                why2 = dict(skipped2).get("p99-tracked", "")
                ck("a chunk whose target suite IS tracked is not refused for this reason",
                   "UNTRACKED" in why2, False)
            finally:
                (bdir / brief_name).unlink(missing_ok=True)

    # ---- red_on_main side: ONE owner task per suite, never a second ---------------------------
    have_r = {n: hasattr(R, n) for n in ("handle_untracked", "open_untracked", "resolve_untracked")}
    ck("red_on_main exposes handle_untracked / open_untracked / resolve_untracked",
       all(have_r.values()), True)

    if all(have_r.values()):
        rows, tasks = [], []
        add = lambda row: rows.append(row)
        file_task = lambda suite, chunk: (tasks.append((suite, chunk)), f"t90{len(tasks)}")[1]
        filed = R.handle_untracked({"test_zz_probe_untracked.py": "p1"}, now=1000.0,
                                   history=[], file_task=file_task, append=add)
        ck("files ONE task, named by the refusing chunk",
           [(s_, t_.startswith("t9")) for s_, t_ in filed], [("test_zz_probe_untracked.py", True)])
        ck("  with a ledger row of its own event, naming the task",
           [(r.get("event"), r.get("suite"), r.get("refused_chunk"), bool(r.get("task"))) for r in rows],
           [("untracked-oracle", "test_zz_probe_untracked.py", "p1", True)])
        filed2 = R.handle_untracked({"test_zz_probe_untracked.py": "p1"}, now=2000.0,
                                    history=rows, file_task=file_task, append=add)
        ck("a second refusal of the same suite files NO second task",
           (filed2, len(rows)), ([], 1))
        R.resolve_untracked("test_zz_probe_untracked.py", now=3000.0, append=add)
        ck("resolving appends a resolution row",
           rows[-1].get("event"), "untracked-resolved")
        rows.append({"event": "untracked-resolved", "suite": "test_zz_probe_untracked.py", "t": 3500.0})
        filed3 = R.handle_untracked({"test_zz_probe_untracked.py": "p9"}, now=4000.0,
                                    history=rows, file_task=file_task, append=add)
        ck("after RESOLVED, a new untracked occurrence files again",
           [(s_, t_.startswith("t9")) for s_, t_ in filed3], [("test_zz_probe_untracked.py", True)])
        open_suites = R.open_untracked(rows + [{"event": "closed", "suite": "x"}])
        ck("open_untracked lists suites with a live untracked-oracle row",
           sorted(open_suites), ["test_zz_probe_untracked.py"])

    # The task body must name the file and the sanctioned route (the RCA's "a reason naming it").
    if all(have_r.values()):
        sys.path.insert(0, str(REPO / "projects" / "app-repo"))
        import taskstore
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            tasks_dir = tmp / "tasks"
            tasks_dir.mkdir()
            taskstore.TASKS, taskstore.DONE = tasks_dir, tasks_dir / "done"
            taskstore.QUEUE, taskstore.LOCK = tasks_dir / "QUEUE.md", tasks_dir / ".lock"
            tid = R.file_untracked_task("test_zz_probe_untracked.py", "p1")
            meta, body = taskstore.find(tid)
            ck("the owner task names the suite file, the refusal, and the chunks-block route",
               ("test_zz_probe_untracked.py" in body, "chunks" in body,
                meta["source"] == "red-on-main"), (True, True, True))

    for x in fails:
        print("FAIL:", x)
    print("test_untracked_suite:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
