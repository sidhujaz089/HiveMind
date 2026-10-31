#!/usr/bin/env python3
"""Does this skill meet the schema? The mechanical check every skill passes before anyone reads it.

    python3 scripts/skill_lint.py --all                     # every live skill in docs/skills/
    python3 scripts/skill_lint.py <SKILL.md>                # one live skill
    python3 scripts/skill_lint.py --draft <SKILL.md>        # a PROPOSED skill (attach_to required)
    python3 scripts/skill_lint.py ... --json                # machine-readable

The schema is docs/skills/SCHEMA.md; each rule below carries the reason it exists.

WHY (Jas, 2026-09-29, plan 3c-i): "Every rejection becomes a rule in that fixture." A review that
rejects a skill for a NEW reason adds that reason here, as a RULES entry with a case in
scripts/tests/test_skill_lint.py, so the next draft is caught mechanically, before any model or
person spends a read on it. The rejections build the schema.

WHAT IT CANNOT DO. It checks that evidence is PRESENT (a density of citations), not that the
evidence is TRUE or that each claim carries its own. Whether a claim is supported, whether the
skill contradicts AGENTS.md or a lesson, and whether it is the right kind are judgements -- the
Manager and Claude make them in review (skill_change.py). A clean lint is a precondition for that
review, never a substitute for it.
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILLS = REPO / "docs" / "skills"
MODELS = REPO / "projects" / "local-inference" / "models"
FLOW = MODELS / "FLOW.json"
ROLES = MODELS / "ROLES.json"

KINDS = ("task", "role")
MAX_CHARS = 20_000          # the largest live skill was 17,195 chars on 2026-09-30 (briefing-the-local-model)
CHARS_PER_CITATION = 2_000  # measured 2026-09-30: every cited live skill is at or under 1,807 chars/citation
MIN_DESCRIPTION = 40
NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# What counts as a citation: a date, a lesson, a task, a commit, a URL, a repository path, a brief,
# or Jas quoted. Deliberately generous -- it measures whether sources are given at all.
CITATION = re.compile(r"20\d\d-\d\d-\d\d|\bL-\d{3}\b|\bt\d{3}\b|\b[0-9a-f]{7,40}\b|https?://"
                      r"|(?:scripts|docs|projects|workspace|models)/[\w./-]+|\b(?:AGENTS|CLAUDE)\.md\b"
                      r"|\bD-\d+\b|\bp\d{2,3}\b|\bJas[,:]")
LIVE_ONLY_FORBIDDEN = ("attach_to",)


def frontmatter(text):
    """({key: value}, body) -- a flat YAML subset: `key: value`, continuation lines appended,
    `[a, b]` read as a list. None when there is no front matter at all."""
    m = re.match(r"^---\n(.*?)\n---\n?", text, re.S)
    if not m:
        return None, text
    fm, key = {}, None
    for line in m.group(1).splitlines():
        k = re.match(r"^([a-z_]+):\s*(.*)$", line)
        if k:
            key = k.group(1)
            fm[key] = k.group(2).strip()
        elif key and line.strip():
            fm[key] = (fm[key] + " " + line.strip()).strip()
    for k, v in fm.items():
        if v.startswith("[") and v.endswith("]"):
            fm[k] = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
    return fm, text[m.end():]


def targets():
    """({task ids}, {role keys}) -- what a skill may be attached to."""
    try:
        tasks = {t["id"] for t in json.loads(FLOW.read_text(encoding="utf-8"))["tasks"]}
    except (OSError, ValueError, KeyError):
        tasks = set()
    try:
        roles = set(json.loads(ROLES.read_text(encoding="utf-8"))["roles"])
    except (OSError, ValueError, KeyError):
        roles = set()
    return tasks, roles


def live_names(exclude_dir=None):
    out = {}
    for d in sorted(SKILLS.iterdir()) if SKILLS.is_dir() else []:
        md = d / "SKILL.md"
        if d.is_dir() and md.is_file() and d != exclude_dir:
            fm, _ = frontmatter(md.read_text(encoding="utf-8", errors="replace"))
            out[(fm or {}).get("name") or d.name] = d.name
    return out


def lint(text, *, draft=False, dirname=None, tasks=None, roles=None, names=None):
    """[(rule id, message)] -- empty when the skill meets the schema."""
    probs = []
    fm, body = frontmatter(text)
    if fm is None:
        return [("frontmatter", "no front matter: the file must start with a --- block")]
    name = fm.get("name", "")
    if not name:
        probs.append(("name", "no name:"))
    elif not NAME.match(name):
        probs.append(("name", f"name {name!r} must be lower-case words joined by hyphens"))
    elif dirname and name != dirname:
        probs.append(("name", f"name {name!r} does not match its directory {dirname!r}"))
    desc = fm.get("description", "")
    if len(desc) < MIN_DESCRIPTION:
        probs.append(("description", f"description is {len(desc)} chars; say what it is for in at least "
                                     f"{MIN_DESCRIPTION} -- it is what search matches on"))
    kind = fm.get("kind", "")
    if kind not in KINDS:
        probs.append(("kind", f"kind must be one of {', '.join(KINDS)} (task = know-how for one kind of work; "
                              f"role = something a role must hold in every job), got {kind!r}"))
    if not str(fm.get("applies_when", "")).strip():
        probs.append(("applies_when", "no applies_when: say plainly when it applies, so it attaches to the "
                                      "right tasks"))
    size = len(text)
    if size > MAX_CHARS:
        probs.append(("size", f"{size:,} chars is over the {MAX_CHARS:,} limit: split it, or it cannot load "
                              f"within a role's skill budget"))
    cites = len(CITATION.findall(body)) + len(CITATION.findall(str(fm.get("evidence", ""))))
    need = max(1, len(body) // CHARS_PER_CITATION)
    if cites < need:
        probs.append(("evidence", f"{cites} citation(s) for {len(body):,} chars; at least {need} needed "
                                  f"(a date, lesson, task, commit, path or URL -- AGENTS.md: every claim "
                                  f"carries its evidence), or an evidence: line naming the sources"))
    if tasks is None or roles is None:
        tasks, roles = targets()
    att = fm.get("attach_to")
    if draft:
        if att is None:
            probs.append(("attach_to", "a draft must name where it attaches: attach_to: [task or role ids] "
                                       "(an empty list means found by search only)"))
        elif not isinstance(att, list):
            probs.append(("attach_to", "attach_to must be a [list]"))
        else:
            for a in att:
                if a not in tasks and a not in roles:
                    probs.append(("attach_to", f"{a!r} is neither a task in FLOW.json nor a role in ROLES.json"))
                elif kind == "task" and a not in tasks:
                    probs.append(("kind_mismatch", f"a task skill is attached to the role {a!r}: attach it to "
                                                   "the task it is know-how for, or make it a role skill"))
                elif kind == "role" and a not in roles:
                    probs.append(("kind_mismatch", f"a role skill is attached to the task {a!r}: attach it to "
                                                   "the role that must always hold it, or make it a task skill"))
        names = live_names() if names is None else names
        upd = fm.get("updates", "")
        if upd and upd not in names:
            probs.append(("updates", f"updates: {upd!r} names no live skill"))
        if name and name in names and upd != name:
            probs.append(("duplicate", f"a skill named {name!r} already exists: propose it as an update "
                                       f"(updates: {name}) or choose another name"))
    else:
        for k in LIVE_ONLY_FORBIDDEN:
            if k in fm:
                probs.append((k, f"{k}: belongs on a draft; a live skill's attachments are in FLOW.json and "
                                 "ROLES.json (the Flow tab), so the two cannot drift"))
    return probs


def lint_all():
    """{skill dir name: [(rule, msg)]} for every live skill, plus duplicate names across them."""
    tasks, roles = targets()
    out, seen = {}, {}
    for d in sorted(SKILLS.iterdir()):
        md = d / "SKILL.md"
        if not (d.is_dir() and md.is_file()):
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        out[d.name] = lint(text, dirname=d.name, tasks=tasks, roles=roles)
        n = (frontmatter(text)[0] or {}).get("name") or d.name
        if n in seen:
            out[d.name].append(("duplicate", f"name {n!r} is also used by {seen[n]}"))
        seen[n] = d.name
    return out


def main(argv):
    as_json = "--json" in argv
    argv = [a for a in argv if a != "--json"]
    if argv[:1] == ["--all"]:
        res = lint_all()
    else:
        draft = "--draft" in argv
        paths = [Path(a) for a in argv if a != "--draft"]
        if not paths:
            print(__doc__)
            return 2
        res = {}
        for p in paths:
            md = p / "SKILL.md" if p.is_dir() else p
            dirname = None if draft else md.parent.name
            res[str(p)] = lint(md.read_text(encoding="utf-8", errors="replace"), draft=draft,
                               dirname=dirname)
    bad = {k: v for k, v in res.items() if v}
    if as_json:
        print(json.dumps({k: [{"rule": r, "message": m} for r, m in v] for k, v in res.items()}, indent=1))
    else:
        for k, v in res.items():
            print(f"  {'FAIL' if v else 'ok  '}  {k}")
            for r, m in v:
                print(f"          [{r}] {m}")
        print(f"  {len(res) - len(bad)}/{len(res)} clean")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
