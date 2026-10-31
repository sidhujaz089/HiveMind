#!/usr/bin/env python3
"""skill_lint refuses each schema breach by its rule id, and passes a skill that meets the schema.

Jas, 2026-09-29 (plan 3c-i): "Every rejection becomes a rule in that fixture." Each case below is a
rule; a review that rejects a skill for a new reason adds a rule to skill_lint.py AND a case here.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import skill_lint as sl  # noqa: E402

PASSED = FAILED = 0
TASKS, ROLES = {"rca", "scoping"}, {"manager", "claude"}
NAMES = {"root-cause-analysis": "root-cause-analysis"}


def report(label, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f" (expected {want!r})"))


def skill(**over):
    fm = {"name": "checking-a-thing", "description": "Check a thing properly before trusting it, with the "
          "evidence for each step.", "kind": "task", "applies_when": "a thing needs checking"}
    fm.update(over)
    head = "".join(f"{k}: {v}\n" for k, v in fm.items() if v is not None)
    body = over.pop("_body", None) or "# Checking\n\nMeasured 2026-09-30 (t477): it held.\n"
    return f"---\n{head}---\n\n{body}"


def rules(text, draft=False, dirname="checking-a-thing"):
    return sorted({r for r, _ in sl.lint(text, draft=draft, dirname=None if draft else dirname,
                                         tasks=TASKS, roles=ROLES, names=NAMES)})


report("a skill that meets the schema is clean", rules(skill()), [])
report("no front matter", rules("# just a body\n"), ["frontmatter"])
report("name not matching its directory", rules(skill(), dirname="other"), ["name"])
report("name not lower-case-hyphenated", rules(skill(name="Checking_A_Thing"), dirname="Checking_A_Thing"), ["name"])
report("description too short to search on", rules(skill(description="short")), ["description"])
report("unknown kind", rules(skill(kind="habit")), ["kind"])
report("no applies_when", rules(skill(applies_when="")), ["applies_when"])
report("over the size limit", "size" in rules(skill() + ("x" * sl.MAX_CHARS)), True)
uncited = skill() .replace("Measured 2026-09-30 (t477): it held.", "It always works. " * 200)
report("uncited body is refused on evidence", rules(uncited), ["evidence"])
report("  an evidence: line with a source counts", rules(uncited.replace(
    "kind: task", "kind: task\nevidence: the Android guide at https://developer.android.com/")), [])
report("a live skill may not carry attach_to (attachments live in the stores)",
       rules(skill(attach_to="[rca]")), ["attach_to"])
report("a draft must name attach_to", rules(skill(), draft=True), ["attach_to"])
report("a draft attached to its task is clean", rules(skill(attach_to="[rca]"), draft=True), [])
report("  [] (search only) is allowed", rules(skill(attach_to="[]"), draft=True), [])
report("a draft attached to something that does not exist", rules(skill(attach_to="[nowhere]"), draft=True),
       ["attach_to"])
report("a task skill attached to a role", rules(skill(attach_to="[manager]"), draft=True), ["kind_mismatch"])
report("a role skill attached to a task", rules(skill(kind="role", attach_to="[rca]"), draft=True),
       ["kind_mismatch"])
report("a draft reusing a live name without updates:",
       rules(skill(name="root-cause-analysis", attach_to="[rca]"), draft=True), ["duplicate"])
report("  with updates: it is an update, and clean",
       rules(skill(name="root-cause-analysis", attach_to="[rca]", updates="root-cause-analysis"), draft=True), [])
report("updates: naming no live skill", rules(skill(attach_to="[rca]", updates="ghost"), draft=True), ["updates"])
fm, _ = sl.frontmatter("---\nname: a\ndescription: one\n  two\nattach_to: [x, 'y']\n---\nbody")
report("front matter: continuation lines and [lists] parse", (fm["description"], fm["attach_to"]),
       ("one two", ["x", "y"]))
live = sl.lint_all()
report("every live skill has kind and applies_when", sorted(n for n, p in live.items()
       if any(r in ("kind", "applies_when", "name", "duplicate", "attach_to") for r, _ in p)), [])
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
