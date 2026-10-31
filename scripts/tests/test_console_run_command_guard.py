#!/usr/bin/env python3
"""t1069's gap, closed (D.1.2): the live-checkout scripts/tests guard covers run_command too.

The frozen oracle (test_console_refuse_tests_writes.py, 2026-10-12) guards write_file/edit_file.
On 2026-10-21 the Manager on t451 wrote scripts/tests/test_chat_lane.py with **run_command**
(`cp /tmp/t451_oracle/... scripts/tests/`) -- manager-actions.jsonl:31186-31187 -- and every
write tool stayed silent: 19 builds refused, 0 tasks. A guard that covers two of the three
write doors is a guard with a door open.

This suite runs the REAL tool_run_command -- with a temp-dir guard target for the allowed cases
(a temp scripts/tests tree is not the live checkout) and a probe path under console.LIVE_TESTS
for the refused ones. The probe name is unique and unlinked in finally; no real command runs
when a write is refused (the refusal returns before subprocess). The audit log is redirected to
a temp file, exactly as the frozen oracle does (console._action appends to the tracked
manager-actions.jsonl in-process).

Run: python3 scripts/tests/test_console_run_command_guard.py
"""
import contextlib
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import console  # noqa: E402

PASS = FAIL = 0


def ck(label, got, want):
    global PASS, FAIL
    ok = got == want
    PASS += ok
    FAIL += not ok
    print(f"  [{'ok' if ok else 'FAIL'}]    {label}" + ("" if ok else f"  got {got!r} want {want!r}"))


@contextlib.contextmanager
def quiet_actions():
    saved = console.ACTIONS
    with tempfile.TemporaryDirectory(prefix="t1069rc-") as d:
        console.ACTIONS = Path(d) / "manager-actions.jsonl"
        try:
            yield Path(d)
        finally:
            console.ACTIONS = saved


PROBES = []                     # created probe files, removed at the end


def probe_path(name):
    return console.LIVE_TESTS / name


def run(cmd, d):
    return console.tool_run_command({"command": cmd, "timeout_s": 60})


def refused(out, label, d):
    ck(label, (out.startswith("REFUSED"), "scripts/tests" in out, "chunks" in out), (True, True, True))
    rows = []
    log = d / "manager-actions.jsonl"
    if log.exists():
        rows = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
    ck(label + " -- and records no audit row", rows, [])


def main():
    stamp = int(time.time())
    with quiet_actions() as d:
        # 1. The exact incident shape: cp INTO the live scripts/tests (t451).
        n1 = f"test_zz_t1069_rc_cp_{stamp}.py"
        src = d / "oracle.txt"
        src.write_text("# probe\n", encoding="utf-8")
        out = run(f"cp {src} {console.REPO}/scripts/tests/{n1}", d)
        refused(out, f"cp into the live scripts/tests is refused", d)
        ck("  and wrote no file", probe_path(n1).exists(), False)

        # 2. Redirection into the live scripts/tests.
        n2 = f"test_zz_t1069_rc_redir_{stamp}.py"
        out = run(f"echo x > {console.REPO}/scripts/tests/{n2}", d)
        refused(out, "a redirection into the live scripts/tests is refused", d)
        ck("  and wrote no file", probe_path(n2).exists(), False)

        # 3. touch / tee / mv -- the verbs the shell uses to create.
        n3 = f"test_zz_t1069_rc_touch_{stamp}.py"
        out = run(f"touch {console.REPO}/scripts/tests/{n3}", d)
        refused(out, "touch inside the live scripts/tests is refused", d)
        n4 = f"test_zz_t1069_rc_mv_{stamp}.py"
        s4 = d / "s4.py"
        s4.write_text("x\n", encoding="utf-8")
        out = run(f"mv {s4} {console.REPO}/scripts/tests/{n4}", d)
        refused(out, "mv into the live scripts/tests is refused", d)
        ck("  and the source file is untouched by a refused mv", s4.exists(), True)

        # 4. python -c open(...,'w') -- the heredoc shape.
        n5 = f"test_zz_t1069_rc_py_{stamp}.py"
        out = run(f"python3 -c \"open('{console.REPO}/scripts/tests/{n5}','w').write('x')\"", d)
        refused(out, "python open(...,'w') on the live scripts/tests is refused", d)
        ck("  and wrote no file", probe_path(n5).exists(), False)

        # 5. The t451 form, verbatim (cp from a /tmp-style dir into scripts/tests).
        n6 = f"test_zz_t1069_rc_t451_{stamp}.py"
        out = run(f"cp /tmp/t1069_probe/{n6} scripts/tests/{n6}", d)
        refused(out, "cp <src> scripts/tests/<name> (t451's shape) is refused", d)

        # 6. NOT refusals: the guard is a scope, not a ban. Every one of these runs for real.
        mirror = d / "scripts" / "tests"
        mirror.mkdir(parents=True)
        (mirror / "x.py").write_text("a\n", encoding="utf-8")
        out = run(f"cp {mirror / 'x.py'} {mirror / 'y.py'}", d)
        ck("a temp-dir scripts/tests mirror is writable (write happened)",
           (not out.startswith("REFUSED"), (mirror / "y.py").read_text(encoding="utf-8")),
           (True, "a\n"))
        out = run(f"cat {console.REPO}/scripts/tests/test_console_refuse_tests_writes.py | head -1", d)
        ck("reading a live suite is not refused (the Manager runs suites every day)",
           (out.startswith("exit: 0"), "#!/usr/bin/env python3" in out), (True, True))
        out = run(f"grep -c def {console.REPO}/scripts/tests/test_console_refuse_tests_writes.py", d)
        ck("grep inside the live scripts/tests is not refused", out.startswith("exit: 0"), True)
        out = run(f"python3 {console.REPO}/scripts/tests/test_console_refuse_tests_writes.py 2>/dev/null | tail -1", d)
        ck("RUNNING a live suite is not refused", out.startswith("exit: 0"), True)

        # 7. The refusal names WHAT: the target, the directory, and the sanctioned route.
        n7 = f"test_zz_t1069_rc_named_{stamp}.py"
        out = run(f"touch {console.REPO}/scripts/tests/{n7}", d)
        ck("the refusal names the file it refused", n7 in out, True)

    for pth in PROBES:
        pth.unlink(missing_ok=True)
    # No probe file survived anywhere under the live scripts/tests.
    leftovers = [p.name for p in console.LIVE_TESTS.glob(f"test_zz_t1069_rc_*_{stamp}.py")]
    ck("no probe file exists under the live scripts/tests", leftovers, [])
    print(f"{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
