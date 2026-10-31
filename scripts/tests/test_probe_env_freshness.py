#!/usr/bin/env python3
"""t815 -- a probe round must not launch on an environment older than its newest fix.

WHY (workspace/rca/70b9877d980d.md section 5.3)
"Env 3 is stale again -- it predates today's five fixes -- so I'll re-clone before
launching" was carried by a sentence. Nothing records env3's clone stamp and the launcher
does not check it: the session's own ledger (ledger.jsonl:557, as cited by the RCA) shows
round 32 re-reporting defects already patched, because round 32 ran on a pre-fix clone.

WHAT THIS TEST LOCKS
  1. probe_overnight gains PROBE_ENV (the launch record) and three callables:
     env_freshness, record_probe_env, check_env_freshness.
  2. A clone stamped BEFORE a later commit on the probed surface measures fixes_since > 0.
  3. Launching a round on it is REFUSED, and the refusal is written to
     workspace/probe-env.jsonl as {ts, env, path, clone_epoch, head_sha, fixes_since,
     round, verdict} with verdict "refused-stale".
  4. An up-to-date environment is NOT refused (verdict "ok") -- a gate that refuses
     everything is a shutdown, not a gate.
  5. --force-stale is a real escape: allowed, and the row says "launched-stale".
  6. main() consults the check BEFORE it runs probe.py, and a refusal does not launch.

Fixtures are real git repos under tempfile -- the shape scripts/tests/test_probe_overnight.py
already uses (origin + a clone standing in for env3) -- with explicit commit dates so
"older than the fix" is deterministic. No model call, no live server, nothing written
outside the temp dirs, every subprocess carries a timeout.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "projects" / "local-inference" / "probe_overnight.py"

_spec = importlib.util.spec_from_file_location("probe_overnight_under_test", TARGET)
po = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(po)

GIT_TIMEOUT = 60
EARLY = "2026-01-01T00:00:00 +0000"    # the clone's stamp
LATE = "2026-02-01T00:00:00 +0000"     # the fix that landed after it
ROW_KEYS = {"ts", "env", "path", "clone_epoch", "head_sha", "fixes_since", "round", "verdict"}
API = ("env_freshness", "record_probe_env", "check_env_freshness")

RESULTS = []


def ok(msg):
    RESULTS.append((True, msg))


def bad(msg):
    RESULTS.append((False, msg))


def api_ready():
    return all(callable(getattr(po, n, None)) for n in API)


def git(args, cwd, date=None):
    env = dict(os.environ)
    if date:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    r = subprocess.run(
        ["git", "-c", "user.name=t815", "-c", "user.email=t815@example.invalid"] + list(args),
        cwd=str(cwd), capture_output=True, text=True, timeout=GIT_TIMEOUT, env=env)
    if r.returncode:
        raise AssertionError(f"git {' '.join(args)} failed: {r.stderr.strip()[:200]}")
    return r.stdout.strip()


def seed(fix_after_clone):
    """origin with one dated commit, env3 as a clone of it, and -- if asked -- a LATER
    commit on the probed surface (scripts/probe.py) that the clone has not fetched."""
    root = Path(tempfile.mkdtemp(prefix="t815-"))
    origin, env3 = root / "origin", root / "env3"
    (origin / "scripts").mkdir(parents=True)
    git(["init", "-q", "."], cwd=origin)
    git(["-c", "init.defaultBranch=main", "checkout", "-q", "-B", "main"], cwd=origin)
    (origin / "scripts" / "probe.py").write_text("def probe(x):\n    return x\n",
                                                 encoding="utf-8")
    git(["add", "-A"], cwd=origin)
    git(["commit", "-q", "-m", "initial"], cwd=origin, date=EARLY)
    git(["clone", "-q", str(origin), str(env3)], cwd=root)
    if fix_after_clone:
        (origin / "scripts" / "probe.py").write_text(
            "def probe(x):\n    return x if x is not None else []\n", encoding="utf-8")
        git(["add", "-A"], cwd=origin)
        git(["commit", "-q", "-m", "the fix env3 predates"], cwd=origin, date=LATE)
    return root, origin, env3


class Stubbed:
    """Runs probe_overnight.main() with the expensive parts replaced, and records what
    main() did: which (env, round, force_stale) it asked about, and every launch it made."""

    def __init__(self, check_return, extra_argv=()):
        self.ret = check_return
        self.extra = list(extra_argv)
        self.calls = []
        self.launches = []
        self.saved = None

    def __enter__(self):
        self.saved = (po.ENV3, po.PROBE_ENV, po.refresh_env3, po.known_so_far,
                      po.check_env_freshness, po.subprocess.run, po.shutil.disk_usage,
                      list(sys.argv))

        def fake_disk(_path):
            return type("D", (), {"free": 999 * 2**30})()   # never the disk guard

        def fake_check(env, round_n, force_stale=False, repo=None, record=None):
            self.calls.append((str(env), round_n, bool(force_stale)))
            return self.ret

        def fake_run(cmd, *a, **kw):
            self.launches.append([str(x) for x in cmd])
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        po.refresh_env3 = lambda n, name="": None
        po.known_so_far = lambda extra: []
        po.shutil.disk_usage = fake_disk
        po.check_env_freshness = fake_check
        po.subprocess.run = fake_run
        sys.argv = ["probe_overnight.py", "--hours", "0.0002", "--model", "x"] + self.extra
        return self

    def __exit__(self, *exc):
        (po.ENV3, po.PROBE_ENV, po.refresh_env3, po.known_so_far, po.check_env_freshness,
         po.subprocess.run, po.shutil.disk_usage, sys.argv) = self.saved


def test_api_exists():
    for name in API:
        fn = getattr(po, name, None)
        if not callable(fn):
            bad(f"probe_overnight has no callable {name!r} -- no launch-time freshness "
                f"gate exists (rca/70b9877d980d.md section 5.3)")
    rec = getattr(po, "PROBE_ENV", None)
    if rec is None:
        bad("probe_overnight has no PROBE_ENV constant -- nothing records the launch")
    elif Path(rec).name != "probe-env.jsonl":
        bad(f"PROBE_ENV must be the probe-env.jsonl launch record, got {rec!r}")
    if getattr(po, "RUNS", None) is None:
        bad("probe_overnight.LOSS: the probe-runs record disappeared from the launcher")
    if api_ready():
        ok("env_freshness / record_probe_env / check_env_freshness are callable")


def test_stale_clone_measures_fixes_since():
    if not api_ready():
        return
    root, origin, env3 = seed(fix_after_clone=True)
    try:
        m = po.env_freshness(env3, origin)
        if not isinstance(m, dict):
            bad(f"env_freshness must return a dict, got {type(m).__name__}")
            return
        want_epoch = int(git(["log", "-1", "--format=%ct", "HEAD"], cwd=env3))
        want_sha = git(["rev-parse", "HEAD"], cwd=env3)
        for k in ("clone_epoch", "head_sha", "fixes_since"):
            if k not in m:
                bad(f"env_freshness result is missing {k!r}")
        if m.get("clone_epoch") != want_epoch:
            bad(f"clone_epoch {m.get('clone_epoch')!r} != env3's HEAD committer time "
                f"{want_epoch!r} (read from git, not hardcoded)")
        if m.get("head_sha") != want_sha:
            bad(f"head_sha {m.get('head_sha')!r} != env3's HEAD {want_sha!r}")
        if m.get("fixes_since") != 1:
            bad(f"fixes_since should be 1 -- one fix is committed on the probed surface "
                f"AFTER the clone's stamp -- got {m.get('fixes_since')!r}")
        if m.get("fixes_since") == 1:
            ok("a clone stamped before the fix measures fixes_since == 1")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_launch_record_row_shape():
    if not api_ready():
        return
    root, origin, env3 = seed(fix_after_clone=True)
    rec = root / "probe-env.jsonl"
    try:
        row = po.record_probe_env(env3, 7, "refused-stale", repo=origin, record=rec)
        if not isinstance(row, dict):
            bad(f"record_probe_env must return the row it wrote, got {type(row).__name__}")
            return
        if set(row) != ROW_KEYS:
            bad(f"row keys must be exactly {sorted(ROW_KEYS)}, got {sorted(row)}")
        if row.get("env") != "env3":
            bad(f"row['env'] should name the environment ('env3'), got {row.get('env')!r}")
        if row.get("path") != str(env3):
            bad(f"row['path'] should be the environment's path, got {row.get('path')!r}")
        if row.get("round") != 7:
            bad(f"row['round'] should be the pass number 7, got {row.get('round')!r}")
        if row.get("verdict") != "refused-stale":
            bad(f"row['verdict'] should be 'refused-stale', got {row.get('verdict')!r}")
        if not isinstance(row.get("ts"), str) or not row["ts"]:
            bad(f"row['ts'] must be a non-empty timestamp string, got {row.get('ts')!r}")
        if row.get("fixes_since") != 1:
            bad(f"row['fixes_since'] should be 1, got {row.get('fixes_since')!r}")
        if not rec.is_file():
            bad(f"the launch record was not written at {rec}")
            return
        lines = [ln for ln in rec.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if len(lines) != 1:
            bad(f"one launch writes exactly one row, found {len(lines)}")
        elif json.loads(lines[0]) != row:
            bad("the row returned is not the row written to the record file")
        else:
            ok("one JSON row with the eight required fields, returned == written")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_fresh_environment_is_not_refused():
    if not api_ready():
        return
    root, origin, env3 = seed(fix_after_clone=False)
    rec = root / "probe-env.jsonl"
    try:
        allowed, row = po.check_env_freshness(env3, 1, False, repo=origin, record=rec)
        if allowed is not True:
            bad(f"an up-to-date environment must be allowed to launch, got allowed={allowed!r}"
                f" -- a gate that refuses everything is a shutdown, not a gate")
        if (row or {}).get("fixes_since") != 0:
            bad(f"an up-to-date environment must measure fixes_since == 0, "
                f"got {(row or {}).get('fixes_since')!r}")
        if (row or {}).get("verdict") != "ok":
            bad(f"verdict for a fresh environment should be 'ok', got {(row or {}).get('verdict')!r}")
        if rec.is_file():
            ok("fresh environment: fixes_since 0, allowed, verdict 'ok'")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_check_refuses_a_stale_round_and_records_it():
    if not api_ready():
        return
    root, origin, env3 = seed(fix_after_clone=True)
    rec = root / "probe-env.jsonl"
    try:
        allowed, row = po.check_env_freshness(env3, 7, False, repo=origin, record=rec)
        if allowed is not False:
            bad(f"a clone that predates a fix must NOT be allowed to launch, got {allowed!r}")
        if (row or {}).get("verdict") != "refused-stale":
            bad(f"refusal must record verdict 'refused-stale', got {(row or {}).get('verdict')!r}")
        if not rec.is_file() or not rec.read_text(encoding="utf-8").strip():
            bad("the refusal wrote no row -- with no row, 're-clone before launching' "
                "is still carried by a sentence (the defect this test exists for)")
        if allowed is False and (row or {}).get("verdict") == "refused-stale" and rec.is_file():
            ok("stale round refused AND written down")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_force_stale_launches_and_records_launched_stale():
    if not api_ready():
        return
    root, origin, env3 = seed(fix_after_clone=True)
    rec = root / "probe-env.jsonl"
    try:
        allowed, row = po.check_env_freshness(env3, 9, True, repo=origin, record=rec)
        if allowed is not True:
            bad(f"--force-stale must let the round launch, got allowed={allowed!r}")
        if (row or {}).get("verdict") != "launched-stale":
            bad(f"--force-stale must write verdict 'launched-stale', "
                f"got {(row or {}).get('verdict')!r}")
        if not rec.is_file():
            bad("--force-stale wrote no launch row; the escape must be visible in the record")
        if allowed is True and (row or {}).get("verdict") == "launched-stale":
            ok("--force-stale launches and is recorded as launched-stale")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_main_refuses_to_launch_on_a_stale_environment():
    if not api_ready():
        return
    root, origin, env3 = seed(fix_after_clone=True)
    rec = root / "probe-env.jsonl"
    po.PROBE_ENV = rec
    try:
        with Stubbed((False, {"round": 1, "verdict": "refused-stale"})) as s:
            po.ENV3 = env3
            po.main()
        if not s.calls:
            bad("main() launched a probe round without consulting the freshness check -- "
                "the gate exists beside the launch point, not at it")
        elif s.calls[0] != (str(env3), 1, False):
            bad(f"main() must ask check_env_freshness(ENV3, 1, False); got {s.calls[0]!r}")
        if s.launches:
            bad(f"main() launched probe.py on an environment with fixes_since > 0: "
                f"{s.launches[0]}")
        if s.calls and not s.launches:
            ok("main() consulted the check at ENV3/round 1 and did not launch on stale ground")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_main_passes_force_stale_through_to_the_gate():
    if not api_ready():
        return
    root, origin, env3 = seed(fix_after_clone=True)
    rec = root / "probe-env.jsonl"
    po.PROBE_ENV = rec
    try:
        s = Stubbed((True, {"round": 1, "verdict": "launched-stale"}), extra_argv=["--force-stale"])
        with s:
            po.ENV3 = env3
            po.main()
        if not any(c[2] is True for c in s.calls):
            bad("--force-stale on the command line did not reach check_env_freshness "
                "as force_stale=True (the escape hatch is not wired into main)")
        if not s.launches:
            bad("with --force-stale accepted, main() must go on to launch the round")
        elif not any(str(po.PROBE) in cmd for cmd in s.launches):
            bad(f"the launched command is not probe.py: {s.launches[0]}")
        else:
            ok("--force-stale reaches the gate and the round launches")
    finally:
        shutil.rmtree(root, ignore_errors=True)


TESTS = [
    test_api_exists,
    test_stale_clone_measures_fixes_since,
    test_launch_record_row_shape,
    test_fresh_environment_is_not_refused,
    test_check_refuses_a_stale_round_and_records_it,
    test_force_stale_launches_and_records_launched_stale,
    test_main_refuses_to_launch_on_a_stale_environment,
    test_main_passes_force_stale_through_to_the_gate,
]

if __name__ == "__main__":
    for t in TESTS:
        try:
            t()
        except Exception as e:                              # noqa: BLE001
            bad(f"{t.__name__} raised {type(e).__name__}: {e}")
    passed = sum(1 for r in RESULTS if r[0])
    for good, msg in RESULTS:
        print(f"  {'[ok]  ' if good else '[FAIL]'} {msg}")
    print(f"\n{passed} passed, {len(RESULTS) - passed} failed")
    sys.exit(1 if passed != len(RESULTS) else 0)
