#!/usr/bin/env python3
"""Skills on tasks and roles: attach and detach, and the path a NEW skill takes before it goes live.

    python3 scripts/skill_change.py attach <skill> <task or role> [--who jas]
    python3 scripts/skill_change.py detach <skill> <task or role> [--who jas]
    python3 scripts/skill_change.py request "<the know-how, in plain words>" [--who jas]
    python3 scripts/skill_change.py propose <draft SKILL.md> [--who claude-code]   # a drafted skill
    python3 scripts/skill_change.py answer <task id> <manager answer file>         # manager_note calls this
    python3 scripts/skill_change.py approve <task id>                              # Claude's review: yes
    python3 scripts/skill_change.py send-back <task id> "<reason>"                 # Claude's review: no

Jas, 2026-09-29 (the Models / Roles / Flow plan, 3c and 3c-i).

ATTACHING is direct, and logged. A tick on a skill card writes the task's `skills` in FLOW.json
(through flow_lint, which must be clean, and FLOW-CHANGELOG.jsonl, so Revert on the Flow tab undoes
it) or the role's `skills` in ROLES.json (ROLES-CHANGELOG.jsonl). It never moves an item or changes
where work goes, which is why it does not need the Manager: the hard rule (2a-ii) is about edits that
can break the flow, and flow_lint proves this one cannot before it is written.

A NEW SKILL is never written straight into docs/skills/ (3c-i: "a wrong skill misleads every future job
that loads it"). It goes:
  1. request -> a Backlog task the Manager drafts into a ```skill-draft block (manager_prompt.SKILL_JOB),
     or `propose` -> a draft Claude or anyone else wrote;
  2. skill_lint --draft -> incomplete: BACK TO THE PROPOSER with the lint output, never to Jas;
  3. similar to a live skill (skills.py find): same kind -> it becomes an UPDATE of that skill and goes
     on; the other kind -> STOP, Assigned to Jas with both side by side (the goal needs a better plan,
     not a second copy);
  4. the Manager's review pass, for a draft the Manager did not write itself (no model reviews its own
     draft -- the observer/executor rule), with a ```skill-review verdict;
  5. Assigned to Claude, who approves (writes the skill and its attachments) or sends it back to the
     proposer with the reason. A conflict with AGENTS.md or a lesson goes to Jas instead.
Every rejection for a reason skill_lint did not catch should become a rule there (SCHEMA.md).
"""
import json
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "projects" / "app-repo")]
import flow_lint  # noqa: E402
import role_store  # noqa: E402
import skill_lint  # noqa: E402
import skills  # noqa: E402

SKILLS = skills.SKILLS
DRAFTS = REPO / "workspace" / "skill-drafts"
AREA = "skills"
TITLE = "Skill proposal: "
DRAFT_BLOCK = re.compile(r"```[ \t]*skill-draft[ \t]*\n(.*?)\n```", re.S | re.I)
REVIEW_BLOCK = re.compile(r"```[ \t]*skill-review[ \t]*\n(.*?)\n```", re.S | re.I)
# "The same know-how": word-overlap cosine of description + applies_when. Measured 2026-09-30 over
# the 18 live skills: the closest DISTINCT pair (adversarial-model-pair / observer-executor-repair)
# scores 0.432, so 0.5 sits above every pair that is really two skills. skills.find() ranks but
# gives no score, which is why this is its own measure. Re-measure as skills are added.
SIMILAR = 0.50
STOP = set("the a an and or of to in on for with it is are be this that when whenever use any its as by "
           "from at into not no so do what who you your their one every model models".split())


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ------------------------------------------------------------------ attach / detach
def _kind_of(name):
    s = next((x for x in skills.all_skills() if x["name"] == name), None)
    if s is None:
        raise ValueError(f"no skill {name!r} in docs/skills/")
    return s["kind"]


def attach(name, target, on=True, who="jas"):
    """Attach (or detach) a skill to a task or a role. Returns a one-line description of what changed."""
    kind = _kind_of(name)
    flow = flow_lint.load(flow_lint.FLOW)
    tasks = {t["id"]: t for t in flow["tasks"]}
    roles = role_store.load()
    if target in tasks:
        if on and kind != "task":
            raise ValueError(f"{name} is a {kind or 'kind-less'} skill; only a task skill attaches to a task "
                             "(attach it to a role instead)")
        cur = list(tasks[target].get("skills") or [])
        new = (cur + [name]) if on and name not in cur else [x for x in cur if x != name] if not on else cur
        if new == cur:
            return f"{name} was already {'on' if on else 'off'} {tasks[target]['name']}"
        import flow_change
        res = flow_lint.lint(flow, [{"op": "edit_task", "id": target, "set": {"skills": new}}], layout=False)
        if not flow_lint.clean(res):
            raise ValueError("not applied: " + flow_lint.report(res))
        what = f"skill {name} {'attached to' if on else 'detached from'} {tasks[target]['name']}"
        n = flow_change._write(res["after"], flow, who, what)
        return f"{what} (Flow change {n}; Revert undoes it)"
    if target in roles["roles"]:
        r = roles["roles"][target]
        if on and kind != "role":
            raise ValueError(f"{name} is a {kind or 'kind-less'} skill; only a role skill attaches to a role "
                             "(attach it to the task it is know-how for instead)")
        cur = list(r.get("skills") or [])
        new = (cur + [name]) if on and name not in cur else [x for x in cur if x != name] if not on else cur
        if new == cur:
            return f"{name} was already {'on' if on else 'off'} {r.get('name', target)}"
        what = f"skill {name} {'attached' if on else 'detached'}"
        e = role_store.update_role(target, who, what, skills=new)
        return f"{r.get('name', target)}: {what} (role changelog entry {e['n'] if e else '?'})"
    raise ValueError(f"{target!r} is neither a task in FLOW.json nor a role in ROLES.json")


# ------------------------------------------------------------------ proposals
def request(text, who="jas"):
    """Jas's '+ Add skill': a Backlog task the Manager drafts. Returns the task id."""
    import taskstore
    text = (text or "").strip()
    if not text:
        raise ValueError("say in plain words what the know-how is")
    body = (f"A new skill, requested from the Flow tab by {who} at {_now()}.\n\n"
            f"IN {who.upper()}'S WORDS: {text}\n\n"
            "HOW THIS IS HANDLED (plan 3c-i): the Manager drafts it as a SKILL.md in a ```skill-draft block, "
            "following docs/skills/SCHEMA.md; scripts/skill_lint.py --draft checks it (anything missing comes "
            "back to the Manager, not to Jas); a similar live skill turns it into an update, or -- if it "
            "overlaps a skill of the other kind -- brings it to Jas; then Claude reviews it before it goes "
            "live.\n\n"
            "skill-request: " + json.dumps({"text": text, "who": who}))
    return taskstore.add(TITLE + text[:70], area=AREA, body=body, source=f"flow tab ({who})", at_top=True,
                         kind="chore")


def is_skill(tid):
    import taskstore
    meta, _ = taskstore.find(tid)
    return bool(meta) and meta.get("area") == AREA and str(meta.get("title", "")).startswith(TITLE)


def extract_draft(text):
    """The last ```skill-draft block, or None."""
    b = DRAFT_BLOCK.findall(text or "")
    return b[-1].strip() + "\n" if b else None


def _draft_path(tid):
    DRAFTS.mkdir(parents=True, exist_ok=True)
    return DRAFTS / f"{tid}.md"


def _vec(text):
    v = {}
    for w in re.findall(r"[a-z]{3,}", text.lower()):
        if w not in STOP:
            v[w] = v.get(w, 0) + 1
    return v


def _cos(a, b):
    import math
    n = sum(x * b.get(k, 0) for k, x in a.items())
    d = math.sqrt(sum(x * x for x in a.values())) * math.sqrt(sum(x * x for x in b.values()))
    return n / d if d else 0.0


def similar(draft_text, exclude=None, live=None):
    """[(name, kind, score)] of live skills at or above SIMILAR, best first."""
    fm, _ = skill_lint.frontmatter(draft_text)
    q = _vec(f"{(fm or {}).get('description', '')} {(fm or {}).get('applies_when', '')}")
    if not q:
        return []
    live = skills.all_skills() if live is None else live
    out = [(s["name"], s["kind"], round(_cos(q, _vec(s["description"] + " " + s["applies_when"])), 3))
           for s in live if s["name"] != exclude]
    return sorted((x for x in out if x[2] >= SIMILAR), key=lambda x: -x[2])


def _assignee(proposer):
    """Who a draft goes back to: '' = the Manager's queue; otherwise the role key it is assigned to."""
    p = (proposer or "manager").lower()
    return "" if p == "manager" else "claude" if p.startswith("claude") else "jas" if p.startswith(("jas", "human")) else p


SKILL_REVIEW_TASK = "skill_review"


def review_holders():
    """The roles that hold the skill-review task, from the role store. [] means nobody does.

    Jas, 2026-10-26: "the draft review should be a task and that task is assigned at the role level on the
    role tab. In the future I can remove the review task from a role and keep it on others." So the answer
    to "who reviews this" is a look-up, not a comparison of who wrote it.
    """
    try:
        import role_store
        roles = role_store.load().get("roles") or {}
    except (OSError, ValueError, KeyError) as e:
        # SAID, NOT SWALLOWED: an unreadable store must not read as "nobody reviews". The caller falls back
        # to the old rule, and this line is how anyone finds out that it did.
        print(f"  note: the role store could not be read ({type(e).__name__}: {e}); "
              f"falling back to the Manager-reviews-a-draft-it-did-not-write rule", file=sys.stderr)
        return []
    return sorted(k for k, r in roles.items()
                  if any(str((t or {}).get("task", "")) == SKILL_REVIEW_TASK
                         for t in (r.get("tasks") or [])))


def review_from(proposer):
    """The role whose answer counts as the REVIEW for this proposer. '' means no review is needed.

    While no role holds the task the OLD rule stands unchanged -- `manager` for anyone but the Manager, and
    nothing for the Manager's own draft. There is a live draft (t7241) in that pipeline, so the default has
    to equal today's behaviour exactly until Jas gives a role the task on the Roles tab.
    """
    holders = review_holders()
    if not holders:
        return "" if (proposer or "").lower() == "manager" else "manager"
    return "" if (proposer or "").lower() in holders else holders[0]


def _route(tid, draft_text, proposer):
    """Steps 2-5 for a draft on task `tid`. Returns (status, assigned)."""
    import taskstore
    p = _draft_path(tid)
    p.write_text(draft_text, encoding="utf-8")
    rel = p.relative_to(REPO)
    probs = skill_lint.lint(draft_text, draft=True)
    back_to = proposer or "manager"
    if probs:
        taskstore.set_status(tid, "queued")
        taskstore.set_meta(tid, needs=_assignee(proposer))
        taskstore.add_note(tid, f"DRAFT INCOMPLETE -- back to {back_to}, nothing is live. skill_lint --draft on "
                                f"{rel}: " + "; ".join(f"[{r}] {m}" for r, m in probs), who="skill_change")
        return "queued", taskstore.find(tid)[0].get("needs", "")
    fm, _ = skill_lint.frontmatter(draft_text)
    sims = similar(draft_text, exclude=fm.get("updates"))
    other = [s for s in sims if s[1] and s[1] != fm.get("kind")]
    same = [s for s in sims if s[1] == fm.get("kind")]
    if other:
        taskstore.set_status(tid, "queued")
        import role_store                # an overlap is a plan choice: the chart's "vision" seat
        up = role_store.escalate("vision")
        taskstore.set_meta(tid, needs=up)
        taskstore.add_note(tid, f"STOPPED FOR YOU: this {fm.get('kind')} skill overlaps the {other[0][1]} skill "
                                f"'{other[0][0]}' (docs/skills/{other[0][0]}/SKILL.md). Two copies of one piece of "
                                f"know-how in two places means the goal needs a better plan: one skill in the right "
                                f"place, or a split. The draft is {rel}. Say which, and it goes back to the Manager.",
                           who="skill_change")
        return "queued", up
    if same and not fm.get("updates"):
        note = (f"Similar to the live skill '{same[0][0]}' (same kind), so this is now an UPDATE of it: "
                f"approving replaces docs/skills/{same[0][0]}/SKILL.md.")
        draft_text = draft_text.replace("\n---\n", f"\nupdates: {same[0][0]}\n---\n", 1)
        p.write_text(draft_text, encoding="utf-8")
        taskstore.add_note(tid, note, who="skill_change")
    reviewer = review_from(proposer)
    if reviewer and not _manager_reviewed(tid):
        taskstore.set_status(tid, "queued")
        taskstore.set_meta(tid, needs="")
        taskstore.add_note(tid, f"LINT CLEAN -- with {reviewer} for its review pass "
                                f"({'the role that holds skill_review' if review_holders() else 'the Manager, because no role holds skill_review yet'}). "
                                f"Draft: {rel}.", who="skill_change")
        return "queued", ""
    taskstore.set_status(tid, "queued")
    import role_store
    up = role_store.escalate("technical")
    taskstore.set_meta(tid, needs=up)
    taskstore.add_note(tid, f"READY FOR CLAUDE'S REVIEW: lint clean{', Manager reviewed' if proposer != 'manager' else ''}. "
                            f"Draft: {rel}. Check each claim's evidence, the kind, conflicts with AGENTS.md and "
                            f"docs/LESSONS.md, and the trigger; then `skill_change.py approve {tid}` or "
                            f"`skill_change.py send-back {tid} \"<reason>\"`.", who="skill_change")
    return "queued", up


def _meta_body(tid):
    import taskstore
    meta, body = taskstore.find(tid)
    return meta or {}, body or ""


def _proposer(tid):
    m = re.search(r"skill-proposer: (\S+)", _meta_body(tid)[1])
    return m.group(1) if m else "manager"


def _manager_reviewed(tid):
    return "MANAGER REVIEW: approve" in _meta_body(tid)[1]


def propose(path, who="claude-code"):
    """A draft written outside the Manager (by Claude, or a model Jas worked it out with)."""
    import taskstore
    text = Path(path).read_text(encoding="utf-8")
    fm, _ = skill_lint.frontmatter(text)
    title = TITLE + ((fm or {}).get("name") or Path(path).parent.name)
    tid = taskstore.add(title, area=AREA, body=(f"A drafted skill, proposed by {who} at {_now()}.\n\n"
                                                f"skill-proposer: {who}\n"), source=who, kind="chore")
    status, assigned = _route(tid, text, who)
    return tid, status, assigned


def answer(tid, answer_path):
    """Route the Manager's answer on a skill task: a draft (its own) or a review (someone else's).
    Returns (status, assigned), or None when nothing was decided (the queue tries again)."""
    import taskstore
    import ask_q16
    p = Path(answer_path)
    text = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
    if not text.strip() or ask_q16.is_deferral(text):
        return None
    proposer = _proposer(tid)
    if review_from(proposer):                               # a REVIEW pass, by role
        b = REVIEW_BLOCK.findall(text)
        verdict = (b[-1].strip().splitlines() or [""])[0].strip().lower() if b else ""
        if verdict.startswith("approve"):
            taskstore.add_note(tid, "MANAGER REVIEW: approve. " + (b[-1].strip()[len("approve"):].strip()[:2000]),
                               who="manager")
            return _route(tid, _draft_path(tid).read_text(encoding="utf-8"), proposer)
        reason = (b[-1].strip() if b else "The Manager answered without a ```skill-review block.")
        taskstore.set_status(tid, "queued")
        taskstore.set_meta(tid, needs=__import__("role_store").escalate("vision") if verdict.startswith("jas") else _assignee(proposer))
        taskstore.add_note(tid, f"MANAGER REVIEW: sent back to {proposer if not verdict.startswith('jas') else 'Jas'}. "
                                f"{reason[:3000]} (answer: {answer_path})", who="manager")
        return "queued", taskstore.find(tid)[0].get("needs", "")
    draft = extract_draft(text)
    if draft is None:
        taskstore.set_status(tid, "queued")
        taskstore.set_meta(tid, needs=__import__("role_store").escalate("technical"))          # a format slip, not a question for Jas
        taskstore.add_note(tid, f"The Manager answered without a ```skill-draft block, so nothing was drafted -- "
                                f"it may need your answer first. Its answer: {answer_path}. Reply here and it goes "
                                "back to the Manager.", who="manager")
        return "queued", "jas"
    return _route(tid, draft, "manager")


def approve(tid, who="claude-code"):
    """Claude's yes: write the skill, apply its attachments, re-index, close the task."""
    import taskstore
    text = _draft_path(tid).read_text(encoding="utf-8")
    probs = skill_lint.lint(text, draft=True)
    if probs:
        raise ValueError("the draft no longer lints clean: " + "; ".join(f"[{r}] {m}" for r, m in probs))
    fm, _ = skill_lint.frontmatter(text)
    att = fm.get("attach_to") or []
    # AN UPDATE KEEPS THE LIVE SKILL'S NAME (found by the 2026-09-30 system test): a draft turned
    # into an update by _route still carried its own name, so approving wrote a file whose name did
    # not match its directory INTO the live skill's directory, then failed the lint, and attached
    # under a name that did not exist. The name is set first, and nothing is written until it lints.
    name = fm.get("updates") or fm["name"]
    live = re.sub(r"^(attach_to|updates):.*\n", "", text, flags=re.M)     # attachments live in the stores
    live = re.sub(r"^name:.*$", f"name: {name}", live, count=1, flags=re.M)
    probs = skill_lint.lint(live, dirname=name)
    if probs:
        raise ValueError(f"not written: as a live skill it would not lint: {probs}")
    d = SKILLS / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(live, encoding="utf-8")
    done = [attach(name, a, True, who=who) for a in att]
    sk = skills.all_skills()
    skills.write_index(sk)
    try:
        skills.index_into_memory(sk, verbose=False)
    except Exception as e:                                           # noqa: BLE001 -- said
        done.append(f"NOTE: not indexed into memory ({type(e).__name__}: {e}); run skills.py index")
    taskstore.add_note(tid, f"APPROVED by {who}: live at {d.relative_to(REPO)}/SKILL.md. " + " ".join(done),
                       who=who)
    taskstore.set_meta(tid, needs="")
    taskstore.set_status(tid, "done")
    return d


def send_back(tid, reason, who="claude-code"):
    """Claude's no: back to whoever drafted it, with the reason. A new kind of reason belongs in skill_lint."""
    import taskstore
    proposer = _proposer(tid)
    taskstore.set_status(tid, "queued")
    taskstore.set_meta(tid, needs=_assignee(proposer))
    taskstore.add_note(tid, f"SENT BACK by {who} to {proposer}: {reason}", who=who)
    print("  If skill_lint could have caught this, add it as a rule there with a case in "
          "scripts/tests/test_skill_lint.py (docs/skills/SCHEMA.md: every rejection becomes a rule).")
    return proposer


def main(argv):
    who = None
    if "--who" in argv:
        i = argv.index("--who")
        who = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    try:
        if len(argv) == 3 and argv[0] in ("attach", "detach"):
            print(attach(argv[1], argv[2], argv[0] == "attach", who=who or "jas"))
            return 0
        if len(argv) == 2 and argv[0] == "request":
            print(request(argv[1], who=who or "jas"))
            return 0
        if len(argv) == 2 and argv[0] == "propose":
            print("  %s: %s, assigned %s" % propose(argv[1], who=who or "claude-code"))
            return 0
        if len(argv) == 3 and argv[0] == "answer":
            r = answer(argv[1], argv[2])
            print(f"{argv[1]}: " + ("nothing decided -- stays queued" if r is None else f"status {r[0]}, assigned {r[1] or '-'}"))
            return 0
        if len(argv) == 2 and argv[0] == "approve":
            print(f"  live: {approve(argv[1], who=who or 'claude-code')}")
            return 0
        if len(argv) == 3 and argv[0] == "send-back":
            print(f"  back to {send_back(argv[1], argv[2], who=who or 'claude-code')}")
            return 0
    except ValueError as e:
        print(f"  REFUSED: {e}")
        return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
