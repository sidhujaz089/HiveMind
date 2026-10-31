"""The lint gate INSIDE the scoping session: a scope leaves the session only once scope_lint is clean,
or after MAX_ROUNDS rewrites.

    python3 scripts/scope_gate.py <answer.md> --task tNNN [--model M] [--role R] [--rounds 2]

Jas, 2026-10-19: "6% pass on first pass is horrible. Let's aim for 90%." The prompt already tells the
scoper to run scope_lint on its own answer (manager_prompt.py), and since 10-15 about half the sessions
never did: Swift 307 of 590, Q4 141 of 247. The rest often submitted with findings open. So the rule
moves from the prompt into the harness: after the answer is written, this runs scope_lint on it and,
on findings, resumes THE SAME task session (ask_q16 --task) with the findings and asks for the whole
answer again. The bridge still lints afterwards, so nothing here can let a bad scope through -- it only
stops a fixable one from costing a full round trip.

Exit 0 always (the gate never blocks the loop); every round is a row in workspace/scope-rewrites.jsonl:
task, model, round, lint exit, the findings, and whether the rewrite came back. Rounds are NOT recorded
to scope_quality -- the scope the session finally hands over is what the bridge records.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LEDGER = REPO / "workspace" / "scope-rewrites.jsonl"
MAX_ROUNDS = 2
# Handing findings to a model may be bounded (never-truncate-a-record), and the bound says so.
FINDINGS_LIMIT = 12000


def lint(answer, task):
    """(exit code, output) of scope_lint on the answer. 0 clean, 2 findings, 1 no chunks block."""
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "scope_lint.py"), str(answer), "--task", task,
                        "--require-steps"],
                       cwd=REPO, capture_output=True, text=True, timeout=1800)
    return r.returncode, (r.stdout + r.stderr).strip()


def clip(text):
    if len(text) <= FINDINGS_LIMIT:
        return text
    return (text[:FINDINGS_LIMIT] + f"\n\n[TRUNCATED after {FINDINGS_LIMIT} of {len(text)} characters. More "
            f"findings follow that you cannot see; run scope_lint yourself to read them all.]")


def fix_prompt(task, findings, rnd):
    return (f"scope_lint REFUSED your answer for {task} (rewrite {rnd} of {MAX_ROUNDS}). Its findings:\n\n"
            f"```\n{clip(findings)}\n```\n\n"
            "Fix EVERY finding, then write your WHOLE answer again -- the complete chunks block and "
            "everything else the original answer carried, not only the changed parts. A finding that "
            "says split a chunk means more chunks, each within the limit. Run scope_lint on your "
            "draft before you finish:\n"
            f"    python3 scripts/scope_lint.py <your draft> --task {task}\n")


def ask(prompt_file, answer, task, model, role):
    """Resume the task session with the fix prompt; the rewrite replaces the answer file. -> exit code."""
    # PRESSING PAUSE HOLDS THIS (Jas, 2026-10-30). This is where a model actually starts, so the pause is asked
    # HERE and not only in whatever loop called in: must_hold answers for the production screen, for ALL devices, and
    # for the device this work would use.
    try:
        import scheduler_commands as _sc
        _hold, _why = _sc.must_hold()
        if _hold:
            return 1
    except Exception as _e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: could not read the pause flags ({type(_e).__name__}: {_e}) -- continuing")
    cmd = [sys.executable, str(REPO / "scripts" / "ask_q16.py"), "--prompt", str(prompt_file),
           "--out", str(answer), "--budget", "16000", "--max-escalations", "0", "--max-rounds", "6",
           "--timeout", "5400", "--purpose", f"manager: {task} (scope_gate rewrite)", "--task", task]
    if model:
        cmd += ["--model", model]
    if role:
        cmd += ["--role", role]
    return subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=5700).returncode


def row(**r):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **r}) + "\n")


def gate(answer, task, model="", role="", rounds=MAX_ROUNDS, lint=lint, ask=ask, row=row):
    """Lint, and on findings resume the session for a rewrite, up to `rounds` times. -> final lint exit."""
    answer = Path(answer)
    rc = 1
    for rnd in range(rounds + 1):
        rc, out = lint(answer, task)
        if rc != 2:                       # 0 clean, 1 no chunks block (not a scope): nothing to fix
            row(task=task, model=model, round=rnd, lint_exit=rc, findings=out if rc else "",
                outcome="clean" if rc == 0 else "not-a-scope")
            return rc
        if rnd == rounds:
            row(task=task, model=model, round=rnd, lint_exit=rc, findings=out, outcome="gave-up")
            return rc
        before = answer.read_bytes() if answer.is_file() else b""
        p = answer.with_name(f"{answer.stem}.fix{rnd + 1}.txt")
        p.write_text(fix_prompt(task, out, rnd + 1), encoding="utf-8")
        arc = ask(p, answer, task, model, role)
        changed = answer.is_file() and answer.stat().st_size > 0 and answer.read_bytes() != before
        row(task=task, model=model, round=rnd, lint_exit=rc, findings=out,
            outcome="rewritten" if changed else f"no-rewrite (ask exit {arc})")
        if not changed:
            if before:
                answer.write_bytes(before)    # a failed rewrite never costs the original answer
            return rc
    return rc


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("answer")
    ap.add_argument("--task", required=True)
    ap.add_argument("--model", default="")
    ap.add_argument("--role", default="")
    ap.add_argument("--rounds", type=int, default=MAX_ROUNDS)
    a = ap.parse_args(argv)
    rc = gate(a.answer, a.task, a.model, a.role, a.rounds)
    print(f"scope_gate {a.task}: final lint exit {rc}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
