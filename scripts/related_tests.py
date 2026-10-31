#!/usr/bin/env python3
"""Run the suites that use the staged scripts; exit 1 if one is newly red. Called by pre-commit.

A COMMIT MUST NOT TURN MAIN RED UNNOTICED (t650b, Jas 2026-10-02: "do both"). Every coder sandbox
copies main, so one red suite refuses EVERY build until someone notices: 10-01 three suites went
stale on a commit and refused 17 starts in two hours; 10-02 test_senior_station refused 3 more.
Each was red from the commit that broke it, and nothing ran it then.

    python3 scripts/related_tests.py            # the staged .py files
    python3 scripts/related_tests.py a.py b.py  # named files (for testing this)

Which suites: a staged test itself, plus every scripts/tests/test_*.py that imports a staged
module or names its file. Run in parallel on the idle cores (coder suites serially -- they drive
coder.py themselves); a failure is re-run once ALONE before it is believed, the same flake rule
as coder.py's baseline. A suite in test-failures-ceiling.json is known debt: reported, not
blocking. It tests the WORKING TREE, which is what the staged files were copied from; a partial
stage (git add -p) can differ, and that is accepted rather than paying for a checkout per commit.
"""
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TESTS = REPO / "scripts" / "tests"
CEILING = REPO / "workspace" / "test-failures-ceiling.json"
TIMEOUT = 300
JOBS = max(1, min(8, (os.cpu_count() or 2) - 4))
# CORES ARE NOT THE LIMIT HERE, MEMORY IS (2026-10-12). A suite holds ~1.1 GB while it runs; 8 of them
# beside Swift (90+ GB) and two builds took MemAvailable under the memory guard's 2 GB reserve at
# 08:17:08Z and the guard killed Swift. So the pool also fits in what is free, keeping the guard's
# reserve plus a 4 GB margin untouched (the same margin dispatch_loop.memory_admit keeps for builds).
SUITE_GIB = 1.2


def _mem_jobs(avail_gib=None):
    if avail_gib is None:
        try:
            for line in open("/proc/meminfo"):
                if line.startswith("MemAvailable:"):
                    avail_gib = int(line.split()[1]) / 1048576
                    break
        except OSError:
            return JOBS
    if avail_gib is None:
        return JOBS
    return max(1, min(JOBS, int((avail_gib - 6.0) // SUITE_GIB)))


def staged():
    out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                         capture_output=True, text=True, cwd=str(REPO), timeout=60).stdout
    return [p for p in out.split() if p.endswith(".py") and "/.venv/" not in p
            and p.startswith(("scripts/", "projects/"))]


def related(paths):
    tests, stems = set(), set()
    for p in paths:
        p = Path(p)
        if p.parent == TESTS.relative_to(REPO) and p.name.startswith("test_"):
            if (REPO / p).is_file():
                tests.add(p.name)
        else:
            stems.add(p.stem)
    if stems:
        pats = [re.compile(rf"^\s*(from|import)\s+[\w., ]*\b{re.escape(s)}\b|\b{re.escape(s)}\.py\b", re.M)
                for s in stems]
        for t in TESTS.glob("test_*.py"):
            text = t.read_text(encoding="utf-8", errors="replace")
            if any(p.search(text) for p in pats):
                tests.add(t.name)
    return sorted(tests)


# GIT'S HOOK ENVIRONMENT IS NOT THE SUITES' (2026-10-02). Inside pre-commit, git exports
# GIT_INDEX_FILE (and GIT_DIR and friends); a suite that runs git in a throwaway repo inherited
# them and failed -- "git worktree add failed: .git/index: index file open failed" -- blocking a
# commit that broke nothing. Run every suite as it runs outside a hook.
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _cap(name):
    """The sweep's own per-suite limit (run_tests.TIMEOUT_FOR), else TIMEOUT."""
    try:
        sys.path.insert(0, str(REPO / "scripts"))
        import run_tests
        return run_tests.TIMEOUT_FOR.get(name, run_tests.TIMEOUT)
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: run_tests limits unreadable ({e}); {TIMEOUT}s", file=sys.stderr)
        return TIMEOUT


def run(name):
    try:
        r = subprocess.run([sys.executable, str(TESTS / name)], capture_output=True, text=True,
                           cwd=str(REPO), timeout=_cap(name), stdin=subprocess.DEVNULL, env=CLEAN_ENV)
    except subprocess.TimeoutExpired:
        return 1, f"TIMEOUT after {_cap(name)}s"
    return r.returncode, (r.stdout + r.stderr)[-800:]


def main(argv):
    paths = argv or staged()
    tests = related(paths)
    if not tests:
        return 0
    try:
        _ceiling = json.loads(CEILING.read_text(encoding="utf-8"))
        known = set(_ceiling.get("allowed_to_fail") or []) | set(_ceiling.get("_oracles_awaiting_their_fix") or [])
    except (OSError, ValueError) as e:
        print(f"  NOTE: {CEILING.name} unreadable ({e}); no failure is treated as known debt")
        known = set()
    # THE CODER SUITES ARE NOT RUN HERE (2026-10-02). Each builds a full clone and runs coder.py;
    # serially they took past half an hour for one coder.py commit -- and bridge freezes commit
    # through this hook under a 30-min limit. The sweep and coder.py's own sandbox baseline run them.
    heavy = [t for t in tests if "coder" in t]
    tests = [t for t in tests if t not in heavy]
    if heavy:
        print(f"  not run at commit (clone-building, covered by the sweep): {', '.join(heavy)}")
    light = tests
    # ASK THE CACHE FIRST (2026-10-30). The gate is not negotiable; paying it again for byte-identical inputs is
    # what made one session end cost three hours -- 04:35 to 04:50 paying it, then again from 05:08, for a tree
    # that had not changed. A hit returns the exit codes measured for THIS tree state, so a red stays red and the
    # hook still blocks; a miss runs everything as before. Keyed on HEAD, every dirty or untracked .py under
    # scripts/ and projects/, and this suite list, so a changed module cannot inherit an old verdict.
    key, cached = None, None
    try:
        import test_verdict_cache as tvc
        key = tvc.state_digest(REPO, tests, ceiling=CEILING)
        cached = tvc.get(key)
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: verdict cache unavailable ({type(e).__name__}: {e}); every suite runs", file=sys.stderr)
    verdicts, texts, reused = {}, {}, False
    if cached and all(t in cached for t in tests):
        verdicts = {t: int(cached[t]) for t in tests}
        reused = True
    else:
        with ThreadPoolExecutor(max_workers=_mem_jobs()) as ex:
            first = dict(zip(light, ex.map(run, light)))
        for t in tests:
            rc, text = first[t] if t in first else run(t)
            if rc:
                rc, text = run(t)                   # alone, before it is believed
            verdicts[t], texts[t] = int(rc), text
        if key:
            try:
                import test_verdict_cache as tvc
                tvc.put(key, verdicts, tests)
                tvc.prune()
            except Exception as e:                                  # noqa: BLE001 -- said, not swallowed
                print(f"  NOTE: verdict cache not written ({type(e).__name__}: {e})", file=sys.stderr)
    red = []
    for t in tests:
        rc = verdicts[t]
        if rc and t in known:
            print(f"  known debt, not blocking: {t}")
        elif rc:
            red.append((t, texts.get(t, "(cached verdict -- this exact tree state already ran red)")))
    print(f"  related suites: {len(tests)} "
          + ("reused from the verdict cache for this exact tree state" if reused else "run")
          + f", {len(red)} red")
    for t, text in red:
        print(f"\n  RED: {t}\n" + "\n".join("    " + l for l in text.strip().splitlines()[-12:]))
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
