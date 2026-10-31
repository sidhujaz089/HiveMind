#!/usr/bin/env python3
"""Oracle for t1344: skills.py records the skill registry and reconciles it with docs/skills.

The task's CHANGE text: add `skills.py registry` emitting rows
{session, ts, harness, name, resolved, registry_source, registry_count, registry_names_sha} and
`skills.py registry --check` exiting non-zero on the diff between docs/skills names and the last
recorded registry for a session, 'red fixture ... (delete a skill dir -> red)'. Checklist item 3
of the task is the control case: a row must carry the WHOLE registry, not the one file it names,
so the row-shape assertions below pin registry_count / names / registry_names_sha against values
THIS test computes, not values it guesses.

RED ON MAIN for the missing change: `skills.py registry` is not a subcommand (the file's subparser
block defines list, show, find, new, index, package only) and REGISTRY_LEDGER / registry /
registry_check are not module members, so every test below fails on the missing feature -- the
drift test's first assertion fires because the CLI exits 2 with argparse's 'invalid choice'.

Live state: none. Every invocation passes --skills-dir and --ledger pointing into a
TemporaryDirectory; the real docs/skills tree is read only to compute an expected count, and
workspace/skill-registry.jsonl is asserted byte-unchanged by the test that exercises defaults.
No model, no network, no fixed /tmp path, every subprocess carries timeout=180.
"""
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILLS_PY = ROOT / "scripts" / "skills.py"
NAMES = ["alpha-skill", "beta-skill", "gamma-skill"]


def run_cli(args, cwd=None):
    return subprocess.run([sys.executable, str(SKILLS_PY)] + [str(a) for a in args],
                          capture_output=True, text=True, timeout=180, cwd=str(cwd) if cwd else None)


def build_store(root, names):
    """A temp docs/skills tree holding exactly `names`, each with distinct real front matter."""
    store = root / "docs" / "skills"
    for i, n in enumerate(names):
        d = store / n
        (d / "references").mkdir(parents=True)
        (d / "references" / "ref.md").write_text("# ref for {}\n".format(n), encoding="utf-8")
        (d / "SKILL.md").write_text(
            "---\nname: {}\ndescription: Use when the {} probe runs.\n"
            "kind: task\napplies_when: probe {} of the t1344 oracle\n---\n\nBody for {}.\n".format(
                n, n, i, n), encoding="utf-8")
    return store


def rows_of(path):
    return [json.loads(ln) for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]


def expected_sha(names):
    return hashlib.sha256("\n".join(sorted(names)).encode("utf-8")).hexdigest()


def test_record_writes_one_row_per_skill_to_the_ledger_it_is_given():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        store = build_store(root, NAMES)
        ledger = root / "ledger.jsonl"
        r = run_cli(["registry", "--session", "s-a", "--skills-dir", store, "--ledger", ledger], ROOT)
        msg = "rc={}\nstdout: {}\nstderr: {}".format(r.returncode, r.stdout[-900:], r.stderr[-900:])
        assert r.returncode == 0, "registry exited {} -- it must record and exit 0. {}".format(r.returncode, msg)
        assert ledger.is_file(), "no ledger written at the --ledger path: {}".format(msg)
        rows = rows_of(ledger)
        assert len(rows) == len(NAMES), "{} rows for a {}-skill store: {}".format(len(rows), len(NAMES), rows)
        assert sorted(x.get("name") for x in rows) == sorted(NAMES), "row names: {}".format([x.get("name") for x in rows])


def test_rows_carry_the_whole_registry_not_the_one_skill():
    """The task's control case: per-file vs whole-registry must be distinguishable."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        store = build_store(root, NAMES)
        ledger = root / "ledger.jsonl"
        r = run_cli(["registry", "--session", "s-a", "--harness", "dsh",
                     "--skills-dir", store, "--ledger", ledger], ROOT)
        assert r.returncode == 0, "rc={}: {}".format(r.returncode, r.stderr[-900:])
        rows = rows_of(ledger)
        assert len(rows) == 3, rows
        for x in rows:
            assert x["session"] == "s-a", x
            assert x["harness"] == "dsh", "harness must come from --harness: {}".format(x)
            assert x["resolved"] is True, "a skill present in the store is resolved: {}".format(x)
            assert x["registry_source"] == "docs/skills", x
            assert x["registry_count"] == len(NAMES), \
                "registry_count={} on a {}-skill store: every row carries the WHOLE registry".format(
                    x.get("registry_count"), len(NAMES))
            assert x["names"] == sorted(NAMES), \
                "names must be the full sorted store, not the row's own skill: {!r}".format(x.get("names"))
            assert x["registry_names_sha"] == expected_sha(NAMES), \
                "registry_names_sha={!r} is not sha256 of the sorted names".format(x.get("registry_names_sha"))
            assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", str(x.get("ts", ""))), \
                "ts {!r} is not UTC yyyy-mm-ddThh:mm:ssZ".format(x.get("ts"))
            for key in ("path", "dir", "file", "chars"):
                assert key not in x, "per-file key {!r} in a whole-registry row: {}".format(key, x)
        assert len({x["ts"] for x in rows}) <= 1, "one registry run must stamp one ts"


def test_check_is_red_when_a_skill_dir_is_deleted():
    """The task's stated red fixture: delete a skill dir -> the check exits non-zero."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        store = build_store(root, NAMES)
        ledger = root / "ledger.jsonl"
        w = run_cli(["registry", "--session", "s-a", "--skills-dir", store, "--ledger", ledger], ROOT)
        assert w.returncode == 0, "the writer step failed: {}".format(w.stderr[-900:])
        import shutil
        shutil.rmtree(str(store / "beta-skill"))
        c = run_cli(["registry", "--check", "--session", "s-a",
                     "--skills-dir", store, "--ledger", ledger], ROOT)
        out = c.stdout + c.stderr
        assert c.returncode != 0, \
            "the check exited 0 with beta-skill deleted; drift must block: " + out[-900:]
        assert "beta-skill" in out, "the check blocked but did not name the divergent skill: " + out[-900:]


def test_check_is_red_when_nothing_is_recorded():
    """A missing record is CANNOT VERIFY and non-zero -- a check that passes on silence is a
    guard that reads as clean while the gap sits under it."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        store = build_store(root, NAMES)
        c = run_cli(["registry", "--check", "--session", "s-none",
                     "--skills-dir", store, "--ledger", root / "absent.jsonl"], ROOT)
        assert c.returncode != 0, \
            "--check exited 0 for a session with no recorded registry: " + (c.stdout + c.stderr)[-900:]
        first = (c.stdout + c.stderr).strip().splitlines()[0] if (c.stdout + c.stderr).strip() else ""
        assert first.startswith("CANNOT VERIFY"), \
            "a missing record must be said, not silently accepted: {!r}".format(first)


def test_check_is_green_when_the_store_and_the_record_agree():
    """The negative assertion: a registry that matches the store must NOT block, and must say
    what it compared -- a gate that always fails is bypassed within a week."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        store = build_store(root, NAMES)
        ledger = root / "ledger.jsonl"
        w = run_cli(["registry", "--session", "s-a", "--skills-dir", store, "--ledger", ledger], ROOT)
        assert w.returncode == 0, w.stderr[-900:]
        c = run_cli(["registry", "--check", "--session", "s-a",
                     "--skills-dir", store, "--ledger", ledger], ROOT)
        assert c.returncode == 0, \
            "--check blocked an in-sync registry: rc={} {}".format(c.returncode, (c.stdout + c.stderr)[-900:])
        assert str(len(NAMES)) in (c.stdout + c.stderr), \
            "the green path must say how many it compared: " + (c.stdout + c.stderr)[-900:]


def test_check_names_the_new_skill_the_store_gained():
    """The other direction of the drift the RCA describes: the store gained a skill the
    recorded registry never saw, and that is a divergence, not a pass."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        store = build_store(root, NAMES)
        ledger = root / "ledger.jsonl"
        w = run_cli(["registry", "--session", "s-a", "--skills-dir", store, "--ledger", ledger], ROOT)
        assert w.returncode == 0, w.stderr[-900:]
        build_store(root, ["delta-skill"])          # one new skill dir, distinct name
        c = run_cli(["registry", "--check", "--session", "s-a",
                     "--skills-dir", store, "--ledger", ledger], ROOT)
        out = c.stdout + c.stderr
        assert c.returncode != 0, "a skill added to the store must show as drift: " + out[-900:]
        assert "delta-skill" in out, "the check must name the skill the store gained: " + out[-900:]


def test_defaults_name_the_repo_store_and_the_repo_ledger():
    """The path a person checks by hand: run with no --skills-dir and no --ledger and the rows
    describe docs/skills, counted AT TEST TIME (no frozen count), and the default ledger is
    workspace/skill-registry.jsonl. The real ledger is asserted untouched -- a registry run must
    not write into the repository behind the caller's back."""
    real_ledger = ROOT / "workspace" / "skill-registry.jsonl"
    before = real_ledger.read_bytes() if real_ledger.is_file() else None
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "OUT.jsonl"
        ledger = Path(td) / "ledger.jsonl"
        r = run_cli(["registry", "--session", "s-cli", "--out", out, "--ledger", ledger], ROOT)
        assert r.returncode == 0, \
            "registry with no --skills-dir/--ledger exited {}: {}".format(r.returncode, r.stderr[-900:])
        n = len([d for d in (ROOT / "docs" / "skills").iterdir()
                 if d.is_dir() and (d / "SKILL.md").is_file()])
        rows = rows_of(ledger)
        assert len(rows) == n, \
            "the default store is docs/skills: {} rows for {} skills there".format(len(rows), n)
        assert n > 0, "docs/skills is empty on main -- the store is not empty, re-read it"
        assert all(x["registry_count"] == n for x in rows), \
            "registry_count must be the store counted from the default SKILLS path"
        assert out.exists() is False, \
            "--out wrote a body file; the registry records a ledger, it stamps nothing"
        assert ledger.is_file()
        spec = importlib.util.spec_from_file_location("skills_under_test", str(SKILLS_PY))
        sk = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sk)
        assert getattr(sk, "REGISTRY_LEDGER", None) is not None, \
            "no REGISTRY_LEDGER constant: the default ledger path is not declared"
        assert sk.REGISTRY_LEDGER.name == "skill-registry.jsonl", \
            "REGISTRY_LEDGER is {!r}, not .../skill-registry.jsonl".format(sk.REGISTRY_LEDGER)
        assert sk.REGISTRY_LEDGER.parent == (ROOT / "workspace"), \
            "REGISTRY_LEDGER must sit beside workspace/skill-loads.jsonl: {}".format(sk.REGISTRY_LEDGER)
        after = real_ledger.read_bytes() if real_ledger.is_file() else None
        assert after == before, \
            "running the registry CLI wrote the repository's own workspace/skill-registry.jsonl"


TESTS = [test_record_writes_one_row_per_skill_to_the_ledger_it_is_given,
         test_rows_carry_the_whole_registry_not_the_one_skill,
         test_check_is_red_when_a_skill_dir_is_deleted,
         test_check_is_red_when_nothing_is_recorded,
         test_check_is_green_when_the_store_and_the_record_agree,
         test_check_names_the_new_skill_the_store_gained,
         test_defaults_name_the_repo_store_and_the_repo_ledger]

if __name__ == "__main__":
    failed = []
    for t in TESTS:
        try:
            t()
            print("  ok   {}".format(t.__name__))
        except Exception as e:            # noqa: BLE001 -- a crash IS a failure; report its cause
            failed.append(t.__name__)
            print("  FAIL {}: {}: {}".format(t.__name__, type(e).__name__, str(e)[:600]))
    print("\n{} of {} passed".format(len(TESTS) - len(failed), len(TESTS)))
    sys.exit(1 if failed else 0)
