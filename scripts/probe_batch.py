#!/usr/bin/env python3
"""Hand the probe's examined-nothing findings to the local model, one branch each.

WHY A SCRIPT AND NOT A LIST OF COMMANDS
---------------------------------------
The brief is built HERE from `workspace/probe-runs.jsonl`, which is the finding's own text --
what it claims, the evidence it gave, and why it calls the pass false. A batch file with the
briefs pasted into it is a second copy of the evidence, and a second copy drifts (`L-114`,
`L-115`). The finding is the source; this only routes it.

WHAT IT WILL NOT DO
-------------------
* It will not run against a DIRTY working tree. `coder.py` verifies in a sandbox copied from
  the working tree and delivers to a branch cut from HEAD; when those differ the delivery
  guard (probe finding 32) refuses every run, so a dirty tree turns the whole batch into a
  row of refusals that look like model failures.
* It will not merge anything. Every accepted change lands on its own `coder/*` branch and
  waits for a human. `python3 scripts/reviewq.py` lists what is waiting.
* It will not touch `coder.py` itself. Finding 21 is in that file, and a batch that edits the
  tool running the batch is a change nobody can attribute.

Each finding here is still a HYPOTHESIS. An ACCEPTED branch means the sandbox suite went
green, not that the finding was true.
"""
import argparse
import json
import urllib.error
import urllib.request
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNS = REPO / "workspace" / "probe-runs.jsonl"
CODER = REPO / "projects" / "local-inference" / "coder.py"

# number -> (files it names, the suite that must go green or None)
# 24/30 and 22/39 are the same defect reported twice; only one of each pair is listed.
PLAN = [
    # ROUND 2, 2026-08-31 -- the "everything else" group. Round 1 (the examined-nothing
    # twelve) is merged; see docs/PROBE-TRIAGE-2026-08-31.md for which branch each landed on.
    #
    # FIVE OF THESE TOUCH sweep.py. Each branch is cut from HEAD independently, so they will
    # want merging one at a time and may conflict with each other. That is the honest cost of
    # one file holding five separate defects, and it is cheaper than one brief asking for five
    # fixes at once, which gives the oracle nothing sharp to refuse.
    (2,  ["scripts/sweep.py"],                "scripts/tests/test_sweep.py"),
    (3,  ["scripts/sweep.py"],                "scripts/tests/test_sweep.py"),
    (4,  ["scripts/sweep.py"],                "scripts/tests/test_sweep.py"),
    (18, ["scripts/sweep.py"],                "scripts/tests/test_sweep.py"),
    (19, ["scripts/sweep.py"],                "scripts/tests/test_sweep.py"),
    # 11 (reconcile_manifest.py) removed 2026-10-13: the script retired with the Chat manifest (D-37).
    (25, ["scripts/memory.py"],               "scripts/tests/test_memory.py"),
    (38, ["scripts/corpus_routine.py"],       "scripts/tests/test_corpus_routine.py"),
    # Two files, one defect. check_workspace.py has a suite and session_start.py does not,
    # so the suite can only witness half of it -- read the branch against BOTH files.
    (40, ["scripts/check_workspace.py", "scripts/session_start.py"],
                                              "scripts/tests/test_check_workspace.py"),
]

INSTRUCTION = """\
A probe found the defect below in this repository. Fix it.

THE SHAPE OF THE FIX, which is the same for every finding in this group:
  1. The check must REPORT WHAT IT EXAMINED -- the count, and enough to identify it.
  2. It must REFUSE TO PASS ON ZERO. Examining nothing is not a pass; it is a failure to
     examine, and it must be reported as such with a non-zero exit where the check has one.
  3. Do not widen the check's scope, and do not change what it does when it DOES find
     something. The defect is the empty case only.

Change as little as possible. Do not reformat. Do not rename anything a caller depends on.
"""


def dirty_for(paths):
    """Uncommitted changes in the FILES A RUN WILL EDIT -- not in the whole tree.

    The delivery guard compares only the paths the ops name, so a whole-tree check was
    broader than the thing it was protecting. It also made the batch unrunnable twice in a
    row: coder.py appends every run to workspace/agent-runs.jsonl, so finishing one batch
    dirties the tree and the next one refuses on its own ledger rows. The scope was wider
    than the argument that justified it, which is the shape of L-137 and L-142 -- committed,
    this time, by the script written to route their fixes.
    """
    out = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--", *paths],
                         capture_output=True, text=True, timeout=60).stdout
    return [l for l in out.splitlines() if l.strip()]

def endpoint():
    """coder.py's own resolved gateway, not a second copy of the rule (L-114, L-115)."""
    sys.path.insert(0, str(REPO / "projects" / "local-inference"))
    import coder
    return coder.GATEWAY


def default_model():
    """Ask the endpoint what it will answer for, rather than assuming."""
    ep = endpoint()
    if "11434" not in ep:
        return "local", "the gateway role"
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=5) as f:
            m = (json.load(f).get("models") or [])
        if m:
            return m[0]["name"], "loaded in ollama; the gateway on 4000 is down"
    except Exception as e:
        print(f"  NOTE: /api/ps unreadable ({e})", file=sys.stderr)
    return "local", "could not ask ollama what is loaded — this will probably fail"


def preflight(model):
    """One tiny completion. Nine long runs must not be how we discover the name is wrong."""
    body = json.dumps({"model": model, "stream": False, "max_tokens": 5,
                       "messages": [{"role": "user", "content": "ok"}]}).encode()
    req = urllib.request.Request(endpoint(), data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as f:
            json.load(f)
        return True, "the endpoint answered for this model"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read()[:200].decode('utf-8', 'replace')}"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:200]}"


def gated():
    rows = [json.loads(l) for l in RUNS.read_text().splitlines() if l.strip()]
    return [f for r in rows for f in (r.get("findings") or []) if "verified_key" in f]


def brief_for(f, n):
    return (f"{INSTRUCTION}\n"
            f"--- probe finding {n} ---\n"
            f"WHAT: {f['what']}\n\n"
            f"EVIDENCE: {f['evidence']}\n\n"
            f"WHY THE PASS IS FALSE: {f['why_false']}\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="show the plan and exit")
    # No default constant. "local" is a ROLE that only exists on the litellm gateway
    # (port 4000); when that is down, coder.py falls through to ollama on 11434, which has
    # no model called "local" and answers `model 'local' not found` to every request. A
    # constant here would be right for one of those two worlds and silently wrong for the
    # other -- which is exactly what happened on 2026-08-31: the batch was started, every
    # run failed on the model name, and it read as "the model produced nothing".
    ap.add_argument("--model", default=None,
                    help="default: the role 'local' on the gateway, or whatever ollama "
                         "actually has loaded when the gateway is down")
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--only", type=int, nargs="*", help="run only these finding numbers")
    a = ap.parse_args()

    g = gated()
    plan = [p for p in PLAN if not a.only or p[0] in a.only]

    if a.list:
        for n, files, target in plan:
            print(f"[{n:>2}] {', '.join(files):<32} target={target or 'NONE — weaker verdict'}")
            print(f"      {g[n-1]['what'][:150]}")
        return 0

    model, why = a.model, "named on the command line"
    if model is None:
        model, why = default_model()
    print(f"endpoint  : {endpoint()}\nmodel     : {model}  ({why})")

    ok, detail = preflight(model)
    print(f"preflight : {detail}")
    if not ok:
        print("\nREFUSING: the endpoint will not answer for that model, so all nine runs\n"
              "would fail on the model name and be recorded as the model producing nothing.")
        print("\nWhat is loaded right now:")
        subprocess.run(["ollama", "ps"], timeout=30)
        return 3

    d = dirty_for(sorted({p for _, files, _ in plan for p in files}))
    if d:
        print("REFUSING: the working tree is dirty.\n")
        print("coder.py verifies in a sandbox copied from the WORKING TREE and delivers to a")
        print("branch cut from HEAD. While those differ, the delivery guard refuses every run")
        print("and the whole batch reports failures that are not the model's.\n")
        for l in d[:20]:
            print(f"  {l}")
        print("\nCommit or stash, then run this again.")
        return 2

    results = []
    for n, files, target in plan:
        print(f"\n{'=' * 72}\n[{n}] {files[0]}  target={target or 'NONE'}\n{'=' * 72}")
        cmd = [sys.executable, str(CODER), "--brief", brief_for(g[n - 1], n),
               "--files", *files, "--model", model,
               "--attempts", str(a.attempts), "--apply"]
        if target:
            cmd += ["--target", target]
        r = subprocess.run(cmd, cwd=str(REPO), timeout=None)
        results.append((n, r.returncode, target))

    print(f"\n{'=' * 72}\nBATCH DONE\n{'=' * 72}")
    for n, rc, target in results:
        state = "accepted and written" if rc == 0 else f"not delivered (exit {rc})"
        print(f"  [{n:>2}] {state}" + ("" if target else "   <-- NO TARGET SUITE, review harder"))
    print("\nNothing is merged. What is waiting for review:\n"
          "  python3 scripts/reviewq.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
