#!/usr/bin/env python3
"""Skills on tasks and roles (plan 3c) and the path a new skill takes before it goes live (3c-i).

Jas, 2026-09-29: "What a model gets = its role's skills plus the current task's skills,
de-duplicated"; "If not [within budget], the prompt gets an index ... Nothing is silently cut";
a draft goes lint -> similar? -> Manager -> Claude, and "an incomplete or rejected draft goes back
to that model ... not to Jas. Only the two stop-conditions (cross-kind overlap, conflict with the
rules) go to Jas." Everything runs on temporary copies: the real stores, task queue and
docs/skills/ are never touched.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "app-repo")]
import flow_change  # noqa: E402
import flow_lint  # noqa: E402
import role_store  # noqa: E402
import skill_change as sc  # noqa: E402
import skill_lint  # noqa: E402
import skills  # noqa: E402
import taskstore  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n        got  {got!r}\n        want {want!r}"))


def refused(fn):
    try:
        fn()
    except ValueError as e:
        return str(e)
    return None


def meta(tid):
    return taskstore.find(tid)[0] or {}


def notes(tid):
    return taskstore.find(tid)[1] or ""


DRAFT = """---
name: {name}
description: {desc}
kind: {kind}
applies_when: {when}
attach_to: [{att}]
---

# {name}

Measured 2026-09-30 (t477): the thing held, see scripts/coder.py.
"""


def draft(name="checking-a-queue", desc="Check a work queue for stuck items before reporting that it is empty.",
          kind="task", when="a queue looks empty", att="rca"):
    return DRAFT.format(name=name, desc=desc, kind=kind, when=when, att=att)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        tasks = tmp / "tasks"
        tasks.mkdir()
        taskstore.TASKS, taskstore.DONE = tasks, tasks / "done"
        taskstore.QUEUE, taskstore.LOCK = tasks / "QUEUE.md", tasks / ".lock"
        models = tmp / "models"
        models.mkdir()
        for f in ("FLOW.json", "ROLES.json", "SERVING.json"):
            shutil.copy2(role_store.MODELS / f, models / f)
        # START FROM NO ATTACHMENTS: the copies are of the live stores, which carry real ones.
        for f, key in (("FLOW.json", "tasks"), ("ROLES.json", "roles")):
            d = json.loads((models / f).read_text())
            for x in (d[key] if key == "tasks" else d[key].values()):
                x.pop("skills", None)
            (models / f).write_text(json.dumps(d, indent=1) + "\n")
        flow_lint.FLOW = flow_change.FLOW = models / "FLOW.json"
        flow_lint.ROLES = models / "ROLES.json"
        flow_change.LOG = models / "FLOW-CHANGELOG.jsonl"
        role_store.STORE, role_store.SERVING = models / "ROLES.json", models / "SERVING.json"
        role_store.LOG, role_store.FLOW = models / "ROLES-CHANGELOG.jsonl", models / "FLOW.json"
        skill_lint.FLOW, skill_lint.ROLES = models / "FLOW.json", models / "ROLES.json"
        skills.MODELS = models
        skills.LOADS = tmp / "skill-loads.jsonl"
        sk = tmp / "docs" / "skills"
        shutil.copytree(skills.SKILLS, sk)
        skills.REPO = sc.REPO = tmp
        skills.SKILLS = sc.SKILLS = skill_lint.SKILLS = flow_lint.SKILLS_DIR = sk
        skills.INDEX = sk / "INDEX.md"
        skills.index_into_memory = lambda *a, **k: (0, 0)
        sc.DRAFTS = tmp / "drafts"
        real_flow = (role_store.MODELS / "FLOW.json").read_text()

        # ---------------------------------------------------------------- attaching
        out = sc.attach("root-cause-analysis", "rca", True, who="jas")
        report("a task skill attaches to its task, logged as a Flow change", "Flow change 1" in out, True)
        report("  FLOW.json carries it", next(t for t in flow_lint.load(flow_lint.FLOW)["tasks"]
                                              if t["id"] == "rca").get("skills"), ["root-cause-analysis"])
        report("  and Revert on the Flow tab undoes it (the changelog has the before)",
               json.loads((models / "FLOW-CHANGELOG.jsonl").read_text().splitlines()[-1])["what"],
               "skill root-cause-analysis attached to " + next(t["name"] for t in flow_lint.load(flow_lint.FLOW)["tasks"]
                                                                  if t["id"] == "rca"))
        report("attaching twice changes nothing", "already" in sc.attach("root-cause-analysis", "rca"), True)
        report("a task skill is refused on a role", "only a task skill" in (refused(
            lambda: sc.attach("root-cause-analysis", "manager")) or ""), False)
        report("  (a task skill on a role: refused by kind)",
               bool(refused(lambda: sc.attach("root-cause-analysis", "manager"))), True)
        report("a role skill is refused on a task",
               bool(refused(lambda: sc.attach("never-truncate-a-record", "rca"))), True)
        out = sc.attach("never-truncate-a-record", "senior", True, who="jas")
        report("a role skill attaches to its role, in the role changelog", "role changelog entry" in out, True)
        report("  ROLES.json carries it", role_store.load()["roles"]["senior"].get("skills"),
               ["never-truncate-a-record"])
        report("  role_store check stays clean", role_store.check(), [])
        report("an unknown skill is refused", bool(refused(lambda: sc.attach("no-such-skill", "rca"))), True)
        report("an unknown target is refused", bool(refused(lambda: sc.attach("root-cause-analysis", "nowhere"))), True)
        report("the real FLOW.json was not touched", (role_store.MODELS / "FLOW.json").read_text(), real_flow)

        # ---------------------------------------------------------------- the Flow tab's Attach form
        sys.path.insert(0, str(ROOT / "projects" / "app-repo"))
        import flow_pages
        form = {"skill": ["writing-oracles-for-delegation"], "on": ["scoping", "preflight"]}
        where, err = flow_pages.handle_post("/flow/skills/attach", lambda k: (form.get(k) or [""])[0], form)
        report("Attach to... ticks two tasks: both attached", (err, skills.attachments_of("writing-oracles-for-delegation")[0]),
               (None, ["scoping", "preflight"]))
        form["on"] = ["preflight"]
        flow_pages.handle_post("/flow/skills/attach", lambda k: (form.get(k) or [""])[0], form)
        report("  unticking one detaches exactly that one", skills.attachments_of("writing-oracles-for-delegation")[0],
               ["preflight"])
        form = {"skill": ["writing-oracles-for-delegation"], "on": ["manager"]}
        where, err = flow_pages.handle_post("/flow/skills/attach", lambda k: (form.get(k) or [""])[0], form)
        report("  a task skill ticked onto a role is refused with the reason", bool(err and "task skill" in err), True)
        form["on"] = []
        flow_pages.handle_post("/flow/skills/attach", lambda k: (form.get(k) or [""])[0], form)

        # ---------------------------------------------------------------- what a job gets
        got = skills.attached("senior", "rca")
        report("task skills first, then the role's", got, [("root-cause-analysis", "task"),
                                                            ("never-truncate-a-record", "role")])
        full = skills.skills_for("senior", "rca", window=200_000)
        report("both fit a big window: both loaded whole, none indexed", (full["loaded"], full["indexed"]),
               (["root-cause-analysis", "never-truncate-a-record"], []))
        report("  the skill text is really there", "thread ledger" in full["text"].lower(), True)
        small = skills.skills_for("senior", "rca", window=50_000)
        report("over budget: what fits is loaded, the rest is LISTED, not dropped",
               (small["loaded"], small["indexed"]), (["root-cause-analysis"], ["never-truncate-a-record"]))
        report("  and the block says so, with the budget", ("NOT LOADED" in small["text"], "5,000 tokens" in small["text"]),
               (True, True))
        report("  without a tool it says the model cannot open them", "cannot open them" in small["text"], True)
        report("  with read_file it says to read them",
               "read_file" in skills.skills_for("senior", "rca", window=50_000, can_open=True)["text"], True)
        rows = [json.loads(x) for x in skills.LOADS.read_text().splitlines()]
        report("every load is logged with what was and was not given", rows[-1]["indexed"],
               ["never-truncate-a-record"])
        fl = flow_lint.load(flow_lint.FLOW)
        next(t for t in fl["tasks"] if t["id"] == "rca")["skills"].append("ghost-skill")
        report("an attached skill that does not exist is MISSING, said",
               skills.skills_for("senior", "rca", window=200_000, flow=fl, log=False)["missing"], ["ghost-skill"])
        dup = skills.skills_for("senior", "rca", window=200_000, already=["root-cause-analysis"], log=False)
        report("a skill the caller already embeds is not given twice",
               (dup["text"].count("=== SKILL root-cause-analysis"), "given in full further down" in dup["text"]),
               (0, True))
        p, info = skills.with_skills("DO THE JOB", "rca", role="senior")
        report("with_skills puts the skills in front of the job", (p.endswith("DO THE JOB"), "=== THE JOB ===" in p),
               (True, True))
        p, info = skills.with_skills("DO THE JOB", "building", role="coder")
        report("  a job with no skills goes out unchanged", p, "DO THE JOB")
        # THE RUNNING ROLE, from the dispatcher (HIVEMIND_ROLE): the Senior's session reviews were given
        # the Manager's role skills because the task's first owner was assumed (2026-09-30).
        import os
        os.environ["HIVEMIND_ROLE"] = "senior"
        try:
            p, info = skills.with_skills("DO THE JOB", "rca")
        finally:
            del os.environ["HIVEMIND_ROLE"]
        report("with no role given, the dispatcher's HIVEMIND_ROLE decides whose role skills load",
               ("never-truncate-a-record" in info["loaded"] + info["indexed"],
                "briefing-the-local-model" in info["loaded"] + info["indexed"]), (True, False))
        report("  and the dispatcher sets it for every job with a role",
               'env["HIVEMIND_ROLE"] = job["role"]' in (ROOT / "scripts" / "dispatch_loop.py").read_text(), True)

        # ---------------------------------------------------------------- a new skill
        tid = sc.request("check the queue for stuck items before saying it is empty", who="jas")
        report("+ Add skill becomes a Skill proposal at the top of the Backlog", taskstore.read_queue()[:1], [tid])
        report("  recognised as one", sc.is_skill(tid), True)
        a = tmp / "answer.md"
        a.write_text("Here it is.\n```skill-draft\n" + draft().replace("applies_when: a queue looks empty\n", "")
                     + "```\n")
        r = sc.answer(tid, a)
        report("the Manager's draft missing applies_when goes BACK TO THE MANAGER, not to Jas",
               (r, meta(tid).get("needs", "")), (("queued", "production"), "production"))
        report("  with the lint output", "[applies_when]" in notes(tid), True)
        a.write_text("```skill-draft\n" + draft() + "```\n")
        r = sc.answer(tid, a)
        report("a clean Manager draft goes to Claude (no Manager self-review)", r, ("queued", "claude"))
        report("  nothing is live yet", (sk / "checking-a-queue").exists(), False)
        d = sc.approve(tid, who="claude-code")
        report("approve writes the skill", (d / "SKILL.md").is_file(), True)
        live = (d / "SKILL.md").read_text()
        report("  without attach_to (attachments live in the stores)", "attach_to" in live, False)
        report("  and it lints as a live skill", skill_lint.lint(live, dirname=d.name), [])
        report("  its attachment applied", "checking-a-queue" in next(
            t for t in flow_lint.load(flow_lint.FLOW)["tasks"] if t["id"] == "rca")["skills"], True)
        report("  the task is done", meta(tid).get("status"), "done")
        report("  and INDEX.md lists it", "checking-a-queue" in skills.INDEX.read_text(), True)

        f = tmp / "claude-draft.md"
        f.write_text(draft(name="reading-a-long-log", desc="Read a long job log from the end, then the first error, "
                           "before deciding what failed.", when="a job log is long"))
        tid2, status, who = sc.propose(f, who="claude-code")
        report("a draft Claude wrote goes to the MANAGER for its review pass first", (status, who), ("queued", ""))
        a.write_text("```skill-review\nsend back\nclaim 1 cites nothing that says it\n```\n")
        r = sc.answer(tid2, a)
        report("the Manager sends it back to the proposer (Claude), not to Jas", r, ("queued", "claude"))
        a.write_text("```skill-review\napprove\nevery claim checked\n```\n")
        r = sc.answer(tid2, a)
        report("the Manager approves -> Claude reviews", r, ("queued", "claude"))
        sc.send_back(tid2, "the evidence line does not support step 2", who="claude-code")
        report("Claude sends it back to whoever drafted it", (meta(tid2).get("needs"), "SENT BACK" in notes(tid2)),
               ("claude", True))

        rca = next(s for s in skills.all_skills() if s["name"] == "root-cause-analysis")
        f.write_text(draft(name="why-it-crashed", desc=rca["description"], when=rca["applies_when"], kind="role",
                           att="senior"))
        tid3, status, who = sc.propose(f, who="claude-code")
        report("a ROLE skill overlapping a live TASK skill stops for Jas", (status, who), ("queued", "jas"))
        report("  naming the overlapping skill", "root-cause-analysis" in notes(tid3), True)
        f.write_text(draft(name="why-it-crashed", desc=rca["description"], when=rca["applies_when"]))
        tid4, status, who = sc.propose(f, who="claude-code")
        report("the same kind overlapping becomes an UPDATE of the live skill",
               "updates: root-cause-analysis" in (sc.DRAFTS / f"{tid4}.md").read_text(), True)
        # APPROVING THAT UPDATE (2026-09-30 system test): it must keep the live skill's name and
        # directory -- it used to write 'name: why-it-crashed' into root-cause-analysis/ and fail.
        taskstore.add_note(tid4, "MANAGER REVIEW: approve. ok", who="manager")
        d4 = sc.approve(tid4, who="claude-code")
        live4 = (d4 / "SKILL.md").read_text()
        report("  approving an update replaces the live skill in place, under its own name",
               (d4.name, "name: root-cause-analysis" in live4, skill_lint.lint(live4, dirname=d4.name)),
               ("root-cause-analysis", True, []))
        report("  and no stray directory for the draft's name", (sk / "why-it-crashed").exists(), False)
        similar = sc.similar(draft(desc="Run two instances of a model that probe and repair each other so no agent "
                                         "verifies its own hypothesis.", when="setting up two model instances to "
                                         "probe and repair each other"))
        report("a near-copy of adversarial-model-pair is found as similar", similar[:1] and similar[0][0],
               "adversarial-model-pair")
        report("  an unrelated draft is similar to nothing", sc.similar(draft(desc="Water the office plants on "
               "Mondays and log which ones drooped.", when="the plants look dry")), [])
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
