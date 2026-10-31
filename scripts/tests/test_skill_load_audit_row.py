#!/usr/bin/env python3
"""t1436 -- the skill-load endpoint records a lookup audit row.

RCA ee2ce7f456b9, thread A: a local worker attached a skill by name and the harness answered
`skill "root-cause-analysis" is unknown or no longer available`, and after the fact nobody could
decide the thread: nothing recorded, at load time, whether the skill was a HIT or a MISS and WHY
-- is its SKILL.md on disk at all? is the name listed in INDEX.md? did it fall over budget?

The repo-side endpoint that answers this for every job the Frontline runs is skills_for(), which
writes one JSONL row per load to the load ledger. Today that row names the misses in `missing`
and nothing else, so the two causes the RCA names are indistinguishable in the record.

RED ON MAIN FOR EXACTLY ONE REASON: the row written today carries the keys at, role, task,
loaded, indexed, missing, budget_tokens and no `audit` key, so the first assertion below fails on
the missing change. Nothing here imports a helper the brief adds, calls it, or depends on a
signature the brief changes -- skills_for's own signature is used exactly as it is on main.
Everything the fixture writes lives inside its own temp dir: SKILLS, INDEX and the load ledger are
all pointed there before the call, so the live workspace load log is never touched.
"""
import json
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import skills  # noqa: E402

BODY = "Body for {name}. Explained in one short paragraph that fits any budget.\n"
BIG = "evidence " * 4000
FRONT = ("---\n"
         "name: {name}\n"
         "description: A skill created only by this fixture, to be read by tests.\n"
         "kind: task\n"
         "---\n\n")
DIRS = ["good-skill", "name-mismatch-skill"]
FRONT_NAMES = {"name-mismatch-skill": "renamed-skill"}
ATTACHED = ["good-skill", "name-mismatch-skill", "ghost-skill"]
AUDIT_KEYS = {"skill_name", "loaded", "reason", "file_on_disk", "in_INDEX"}
REASONS = {"already-in-prompt", "loaded", "over-budget", "name-mismatch", "not-in-store"}


def _store(root, body):
    """A temp docs/skills tree with the real layout: one directory per skill, SKILL.md inside it
    with front matter, and an INDEX.md listing `## <dir>` headings -- the shape the store's own
    index writer emits. `name-mismatch-skill` carries a front-matter name that differs from its
    directory name, which is exactly how a skill ends up on disk and in INDEX.md while being
    unreachable under the name the job attached."""
    store = root / "store"
    index = root / "INDEX.md"
    lines = ["# Skills", ""]
    for d in DIRS:
        (store / d).mkdir(parents=True, exist_ok=True)
        (store / d / "SKILL.md").write_text(
            FRONT.format(name=FRONT_NAMES.get(d, d)) + body.format(name=d), encoding="utf-8")
        lines += ["## " + d, ""]
    index.write_text("\n".join(lines), encoding="utf-8")
    return store, index


def _run(root, body=BODY, **kw):
    """One skills_for() call, every module-level path pointed inside `root`, returning the single
    row the load wrote plus the dict skills_for returned."""
    store, index = _store(root, body)
    skills.SKILLS = store
    skills.INDEX = index
    skills.LOADS = root / "loads.jsonl"
    flow = {"tasks": [{"id": "t1436", "skills": list(ATTACHED)}]}
    out = skills.skills_for("manager", "t1436", flow=flow, roles={"roles": {}}, **kw)
    rows = [json.loads(l) for l in (root / "loads.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 1, "expected exactly one load row in the temp ledger, got " + str(len(rows))
    return rows[0], out


def _by_name(row):
    return {r["skill_name"]: r for r in row["audit"]}


def test_load_row_carries_one_audit_entry_per_attached_skill():
    with tempfile.TemporaryDirectory() as td:
        row, out = _run(Path(td))
        # the store must have attached the names, else the fixture is silent, not red-for-change
        assert set(out["loaded"]) | set(out["indexed"]) | set(out["missing"]) == set(ATTACHED), (
            "the fixture attached no skills: the flow/roles shape in this fixture does not match "
            "the store reader -- fix the fixture, not the change: " + repr(out))
        assert "audit" in row, "the load row carries no audit list; keys=" + str(sorted(row))
        by = _by_name(row)
        assert set(by) == set(ATTACHED), "audit is not one row per attached skill: " + str(sorted(by))
        assert "renamed-skill" not in by, (
            "audit must record the names the job ASKED for, not the names the store resolved to")
        for r in row["audit"]:
            assert set(r) == AUDIT_KEYS, "audit row keys: " + str(sorted(r))
            assert r["reason"] in REASONS, "unknown reason: " + repr(r["reason"])
        hit = by["good-skill"]
        assert hit["loaded"] is True, hit
        assert hit["reason"] == "loaded", hit
        assert hit["file_on_disk"] is True, hit
        assert hit["in_INDEX"] is True, hit
        mismatch = by["name-mismatch-skill"]
        assert mismatch["loaded"] is False, mismatch
        assert mismatch["reason"] == "name-mismatch", mismatch
        assert mismatch["file_on_disk"] is True, (
            "the SKILL.md IS on disk under the attached directory name; reporting False here is "
            "the exact field the RCA needed and the one that separates 'renamed' from 'gone'")
        assert mismatch["in_INDEX"] is True, mismatch
        ghost = by["ghost-skill"]
        assert ghost["loaded"] is False, ghost
        assert ghost["reason"] == "not-in-store", ghost
        assert ghost["file_on_disk"] is False, ghost
        assert ghost["in_INDEX"] is False, ghost


def test_already_in_prompt_is_recorded_as_its_own_reason():
    with tempfile.TemporaryDirectory() as td:
        row, out = _run(Path(td), already=("good-skill",))
        assert out["loaded"] == ["good-skill"], out
        by = _by_name(row)
        hit = by["good-skill"]
        assert hit["loaded"] is True, hit
        assert hit["reason"] == "already-in-prompt", hit
        assert hit["file_on_disk"] is True, hit
        assert hit["in_INDEX"] is True, hit
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", str(row["at"])), row["at"]


def test_over_budget_is_recorded_as_its_own_reason():
    with tempfile.TemporaryDirectory() as td:
        row, out = _run(Path(td), body=BIG)
        assert out["loaded"] == [], out
        assert "good-skill" in out["indexed"], out
        by = _by_name(row)
        dropped = by["good-skill"]
        assert dropped["loaded"] is False, dropped
        assert dropped["reason"] == "over-budget", dropped
        assert dropped["file_on_disk"] is True, dropped


def test_existing_return_shape_is_unchanged():
    with tempfile.TemporaryDirectory() as td:
        row, out = _run(Path(td))
        assert set(out) == {"text", "loaded", "indexed", "missing", "budget_tokens"}, sorted(out)
        assert out["loaded"] == ["good-skill"], out
        assert out["indexed"] == [], out
        assert sorted(out["missing"]) == ["ghost-skill", "name-mismatch-skill"], out
        assert isinstance(out["text"], str) and "Body for good-skill." in out["text"], out["text"]
        assert row["budget_tokens"] > 0, row


def main():
    tests = sorted((v for k, v in globals().items()
                    if k.startswith("test_") and callable(v)), key=lambda f: f.__name__)
    failed = 0
    for t in tests:
        try:
            t()
            print("PASS " + t.__name__)
        except Exception as e:
            failed += 1
            print("FAIL " + t.__name__ + ": " + type(e).__name__ + ": " + str(e))
    print(str(len(tests) - failed) + "/" + str(len(tests)) + " passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
