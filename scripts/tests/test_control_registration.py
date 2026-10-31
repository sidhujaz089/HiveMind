#!/usr/bin/env python3
"""test_control_registration.py -- oracle for t1060.

02a3b580 made a wedged Ollama restart itself through a sudoers rule. Nothing in the tree recorded
that a control was made live: no task id, no flow node, no machine-readable row naming the unit and
the argv. Detection came 6.6 days later, from the session review. The fix is a gate at the commit
edge -- the one surface that runs by itself -- so the registry has to exist at write time.

This suite runs the REAL scripts/hooks/pre-commit inside throwaway repos laid out like production
(core.hooksPath=scripts/hooks, sibling checks stubbed to a clean pass, as in t452's
test_precommit_record_sweep.py), so what is under test is the thing that will run on every commit.
Red on main: the hook has no control-registration section, so every unregistered control commits.

Rules the assertions encode, all of them behaviours a person would check by hand:
  * a staged diff that makes a control act on a live service is REFUSED when workspace/controls.jsonl
    (read from HEAD, so a commit cannot register the controls it adds) has no row for that unit;
  * the refusal names the unit and prints the row to append, in json.dumps form;
  * a row for unit "nas-backup" does not license a control acting on "ollama";
  * a row must carry task tNNN, a non-empty flow_node and a non-empty exec_argv_sha256;
  * a row added by the same commit does not license that commit (HEAD, not the staged blob);
  * markdown, scripts/tests/ and workspace/controls.jsonl itself are NOT control-acting code -- a gate
    that fires on prose gets --no-verify'd inside a week.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / "scripts" / "hooks" / "pre-commit"
REGISTRY = "workspace/controls.jsonl"

CONTROL_CODE = (
    "import subprocess\n"
    "\n"
    "\n"
    "def unstick(now):\n"
    '    subprocess.run(["sudo", "-n", "/usr/bin/systemctl", "restart", "ollama"],\n'
    "                   timeout=120)\n"
    "    return now\n"
)
CONTROL_CODE_SERVICE = CONTROL_CODE.replace('"restart", "ollama"', '"restart", "ollama.service"')

TIMER_UNIT = "ollama-watch.timer"
TIMER_BODY = "[Unit]\nDescription=watch ollama\n\n[Timer]\nOnUnitActiveSec=5min\n"

DOC_BODY = "# Runbook\n\nIf the server wedges, run `sudo systemctl restart ollama` by hand.\n"

TEST_BODY = ('"""test_ollama_units.py -- exercises `systemctl restart ollama` against a fake."""\n'
             "import sys\n\nsys.exit(0)\n")


def row(unit="ollama", task="t1060", flow_node="ollama-restart", argv_sha="a" * 64):
    return json.dumps({"ts": "2026-10-01T06:14:01Z", "control": "restart a wedged Ollama",
                       "kind": "restart", "target_unit": unit, "exec_argv_sha256": argv_sha,
                       "task": task, "flow_node": flow_node,
                       "actor": "claude-code/opus-5.5", "source_commit": "02a3b580"})


def _git(repo, *args, timeout=120):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, timeout=timeout)


def _write(repo, rel, body):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def _make_repo(head_rows):
    repo = Path(tempfile.mkdtemp(prefix="t1060-"))
    g = _git(repo, "init", "-q")
    if g.returncode != 0:
        raise RuntimeError("git init failed: " + (g.stdout or "") + (g.stderr or ""))
    _git(repo, "config", "user.email", "t1060@test.local")
    _git(repo, "config", "user.name", "t1060 oracle")
    hooks = repo / "scripts" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "pre-commit").write_text(HOOK.read_text(encoding="utf-8"), encoding="utf-8")
    (hooks / "pre-commit").chmod(0o755)
    # The hook's sibling checks, stubbed to a clean pass so only the control section is under test.
    (hooks.parent / "signature_gate.py").write_text("import sys; sys.exit(0)\n", encoding="utf-8")
    (hooks.parent / "library.py").write_text("import sys; sys.exit(0)\n", encoding="utf-8")
    (hooks.parent / "tests").mkdir(exist_ok=True)
    (hooks.parent / "tests" / "test_sampling_single_source.py").write_text(
        "import sys; sys.exit(0)\n", encoding="utf-8")
    _git(repo, "config", "core.hooksPath", "scripts/hooks")
    _write(repo, REGISTRY, "".join(r + "\n" for r in head_rows))
    _write(repo, "README.md", "# t1060 fixture repo\n")
    a = _git(repo, "add", "-A")
    c = _git(repo, "commit", "-m", "chore: baseline\n\nAgent: human/jas")
    if a.returncode != 0 or c.returncode != 0:
        raise RuntimeError("baseline commit failed: " + (a.stderr or "") + (c.stdout or "")
                           + (c.stderr or ""))
    return repo


def _scenario(name, files, head_rows, staged_rows, expect_refused, must_say=()):
    repo = None
    try:
        repo = _make_repo(head_rows)
        for rel, body in files.items():
            _write(repo, rel, body)
        if staged_rows:
            with (repo / REGISTRY).open("a", encoding="utf-8") as fh:
                for r in staged_rows:
                    fh.write(r + "\n")
        a = _git(repo, "add", "-A")
        if a.returncode != 0:
            print("[FAIL] " + name + ": git add failed: " + (a.stdout or "") + (a.stderr or ""))
            return False
        g = _git(repo, "commit",
                 "-m", "feat(scripts): a control that acts on a live service\n\nAgent: human/jas")
        out = (g.stdout or "") + (g.stderr or "")
        refused = g.returncode != 0
        ok = (refused == expect_refused)
        for frag in must_say:
            if frag not in out:
                ok = False
        print(("[PASS] " if ok else "[FAIL] ") + name
              + ": rc=%d refused=%s want_refused=%s" % (g.returncode, refused, expect_refused))
        if not ok:
            for line in out.splitlines():
                print("    | " + line)
        return ok
    finally:
        if repo is not None:
            shutil.rmtree(repo, ignore_errors=True)


def main():
    results = [
        _scenario("unregistered systemctl control is refused",
                  {"scripts/ollama_ctl.py": CONTROL_CODE}, [], [], True,
                  ('"target_unit": "ollama"', REGISTRY)),
        _scenario("a registered ollama row lets the control through",
                  {"scripts/ollama_ctl.py": CONTROL_CODE}, [row()], [], False),
        _scenario('a row for "ollama" covers a control that says "ollama.service"',
                  {"scripts/ollama_ctl.py": CONTROL_CODE_SERVICE}, [row()], [], False),
        _scenario("a row for a DIFFERENT unit (nas-backup) does not license the ollama control",
                  {"scripts/ollama_ctl.py": CONTROL_CODE}, [row("nas-backup")], [], True,
                  ('"target_unit": "ollama"',)),
        _scenario("a row whose task is not a tNNN is refused",
                  {"scripts/ollama_ctl.py": CONTROL_CODE}, [row(task="tbd")], [], True,
                  ('"target_unit": "ollama"',)),
        _scenario("a row with an empty flow_node is refused",
                  {"scripts/ollama_ctl.py": CONTROL_CODE}, [row(flow_node="")], [], True,
                  ('"target_unit": "ollama"',)),
        _scenario("a row with an empty exec_argv_sha256 is refused",
                  {"scripts/ollama_ctl.py": CONTROL_CODE}, [row(argv_sha="")], [], True,
                  ('"target_unit": "ollama"',)),
        _scenario("a commit cannot register the control it adds (registry read from HEAD)",
                  {"scripts/ollama_ctl.py": CONTROL_CODE}, [], [row()], True,
                  ('"target_unit": "ollama"',)),
        _scenario("markdown that names systemctl is not control-acting code",
                  {"docs/ops-runbook.md": DOC_BODY}, [], [], False),
        _scenario("appending a registry row alone is accepted",
                  {}, [], [row()], False),
        _scenario("a new test that quotes systemctl is not control-acting code",
                  {"scripts/tests/test_ollama_units.py": TEST_BODY}, [], [], False),
        _scenario("a new deploy/systemd timer with a row for its own unit is accepted",
                  {"deploy/systemd/" + TIMER_UNIT: TIMER_BODY}, [row(TIMER_UNIT)], [], False),
        _scenario("a new deploy/systemd timer is refused when the row names a different unit",
                  {"deploy/systemd/" + TIMER_UNIT: TIMER_BODY}, [row("ollama")], [], True,
                  ('"target_unit": "ollama-watch.timer"',)),
    ]
    if all(results):
        print("t1060 oracle: all %d scenarios pass" % len(results))
        return 0
    print("t1060 oracle: FAILURES in the scenarios above")
    return 1


if __name__ == "__main__":
    sys.exit(main())
