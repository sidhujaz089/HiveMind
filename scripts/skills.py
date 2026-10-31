#!/usr/bin/env python3
"""Skills: list them, find one, write one, index them into memory. For ANY model, not just Claude.

    python3 scripts/skills.py list                  name + one line each
    python3 scripts/skills.py show local-model-loading
    python3 scripts/skills.py find "ollama context"  hybrid search, same ranker as memory
    python3 scripts/skills.py new my-skill --description "..."
    python3 scripts/skills.py index                 push every skill into the warm memory tier
    python3 scripts/skills.py package               build .skill zips for uploading

WHY PLAIN DIRECTORIES AND NOT .skill ARCHIVES
---------------------------------------------
They were zips. A zip is opaque to grep, cannot be edited in place, and is unreadable to a
local model with a `read_file` tool -- so "any model can manage them" was false by
construction. Jas, 2026-09-03: *"I want the skills to be managed in a way that any model can
manage them."*

A directory is also the app's NATIVE layout: the skills that load automatically live as
`<name>/SKILL.md` under a synced directory. So a directory here can be copied straight there,
and `package` exists only for uploading.

WHY DESCRIPTIONS ARE NOT INJECTED INTO ANY SYSTEM PROMPT
-------------------------------------------------------
Jas: *"I can see skills building up a lot over time. is there a better way than injecting all
of their descriptions and names?"* Yes, and it is the argument memory.py already made for
itself:

    a description is ~40 tokens. Twenty skills is 800 tokens on every turn, most of them
    irrelevant to the task in hand, and it grows without bound.

So the model is given the DIRECTORY and the search command, and nothing else. Skills are
indexed into the same warm tier as memory, so `memory.py recall` and `skills.py find` both
surface the right one by keyword AND by meaning. The cost is constant no matter how many
exist -- which is the property that lets them build up freely, which is what Jas wants.

`index` is idempotent and should be re-run whenever a skill is added or its description
changes. `session_end.py` calls it.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKILLS = REPO / "docs" / "skills"
INDEX = SKILLS / "INDEX.md"
# t1344 / RCA 433601fc1c12: per-session record of what the skill registry contained
REGISTRY_LEDGER = REPO / "workspace" / "skill-registry.jsonl"


def parse(d):
    """(name, description, extra_files) from a skill directory, or None."""
    md = d / "SKILL.md"
    if not md.is_file():
        return None
    body = md.read_text(encoding="utf-8", errors="replace")
    m = re.match(r"^---\n(.*?)\n---\n", body, re.S)
    if not m:
        return None
    fm = m.group(1)
    name = re.search(r"^name:\s*(.+)$", fm, re.M)
    desc = re.search(r"^description:\s*(.+?)(?=\n[a-z_]+:|\Z)", fm, re.S | re.M)
    kind = re.search(r"^kind:\s*(\S+)", fm, re.M)
    when = re.search(r"^applies_when:\s*(.+)$", fm, re.M)
    extra = sorted(p.relative_to(d).as_posix() for p in d.rglob("*.md")
                   if p.name != "SKILL.md")
    return {"name": (name.group(1).strip() if name else d.name),
            "dir": d.relative_to(REPO).as_posix(),
            "description": " ".join(desc.group(1).split()) if desc else "",
            "extra": extra, "chars": len(body),
            "kind": kind.group(1) if kind else "", "applies_when": when.group(1).strip() if when else ""}


def all_skills():
    return [s for s in (parse(d) for d in sorted(SKILLS.iterdir()) if d.is_dir()) if s]


def _store_skill_names(base):
    """Sorted skill names from a store directory: is_dir + SKILL.md with valid front matter."""
    names = []
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        md = d / "SKILL.md"
        if not md.is_file():
            continue
        try:
            body = md.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"  NOTE: cannot read {md} ({type(e).__name__}: {e}); skipping", file=sys.stderr)
            continue
        if not re.match(r"^---\n(.*?)\n---\n", body, re.S):
            continue
        names.append(d.name)
    return sorted(names)


def registry(session, harness="hivemind", skills_dir=None, ledger=None):
    """Record the skill registry for a session: one JSONL row per skill, each carrying the whole registry.

    t1344 / RCA 433601fc1c12: the recorder that makes 'are find and show querying the same
    registry?' answerable from a file rather than a sentence.
    """
    base = Path(skills_dir or SKILLS)
    if not base.is_dir():
        print(f"  NOTE: skill store {base} does not exist; nothing recorded", file=sys.stderr)
        return []
    names = _store_skill_names(base)
    if not names:
        print(f"  NOTE: no valid skills found in {base}; nothing recorded", file=sys.stderr)
        return []
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    sha = hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest()
    ledger_path = Path(ledger or REGISTRY_LEDGER)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for name in names:
        rows.append({
            "session": session,
            "ts": ts,
            "harness": harness,
            "name": name,
            "resolved": True,
            "registry_source": "docs/skills",
            "registry_count": len(names),
            "registry_names_sha": sha,
            "names": names,
        })
    with ledger_path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return rows


def registry_check(skills_dir=None, ledger=None, session=None):
    """Compare the last recorded registry for a session against the store. Returns divergence strings."""
    if not session:
        return ["CANNOT VERIFY: no session id -- pass --session ID"]
    ledger_path = Path(ledger or REGISTRY_LEDGER)
    if not ledger_path.is_file():
        return ["CANNOT VERIFY: no registry rows recorded for session {} in {}".format(session, ledger_path)]
    last_row = None
    try:
        for line in ledger_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, ValueError) as e:
                print(f"  NOTE: unparseable ledger line ({type(e).__name__}: {e}); continuing", file=sys.stderr)
                continue
            if row.get("session") == session:
                last_row = row
    except OSError as e:
        print(f"  NOTE: cannot read ledger {ledger_path}: {e}", file=sys.stderr)
        return ["CANNOT VERIFY: cannot read ledger ({})".format(type(e).__name__)]
    if last_row is None:
        return ["CANNOT VERIFY: no registry rows recorded for session {} in {}".format(session, ledger_path)]
    recorded = set(last_row.get("names", []))
    base = Path(skills_dir or SKILLS)
    store = set(_store_skill_names(base))
    divs = []
    for name in sorted(recorded - store):
        divs.append("missing: {}".format(name))
    for name in sorted(store - recorded):
        divs.append("extra: {}".format(name))
    return divs


def write_index(skills):
    """INDEX.md -- generated, never hand-edited. A hand-maintained list goes stale (L-046)."""
    lines = ["# Skills", "",
             "**Generated by `scripts/skills.py index`. Do not edit by hand.**", "",
             "Read the one that covers your task BEFORE you start it. They do not load "
             "themselves.", "",
             "    python3 scripts/skills.py find \"<what you are about to do>\"",
             "    python3 scripts/skills.py show <name>", ""]
    for s in skills:
        lines.append(f"## {s['name']}")
        lines.append("")
        lines.append(s["description"])
        lines.append("")
        lines.append(f"`{s['dir']}/SKILL.md`"
                     + (f" — plus {', '.join(s['extra'])}" if s["extra"] else ""))
        lines.append("")
    INDEX.write_text("\n".join(lines))
    return INDEX


def index_into_memory(skills, verbose=True):
    """Every skill becomes a searchable record, so discovery costs nothing until asked for.

    The `see:` field points at the skill directory, which is also what graph.py parses -- so
    a skill joins the same edge graph as lessons, guards and tests rather than sitting in a
    list beside it.
    """
    sys.path.insert(0, str(REPO / "scripts"))
    import memory as mem
    import time

    # ONE LIST, ONE SAVE. This used to call mem.add() -- which does its own load() and
    # save() -- while holding a `records` list read BEFORE those calls, then finish with
    # mem.save(records). The stale list did not contain the freshly added rows, so saving it
    # SILENTLY DELETED THEM. Observed 2026-09-03: two skills were added, written, and wiped
    # in the same command, and it only looked fine because the next run added them again.
    #
    # That is `L-046`'s shape at runtime rather than in prose -- two writers, one file, no
    # coordination -- and it is exactly the "nothing errored, the run looked healthy" failure
    # this repository keeps finding.
    records = mem.load()
    by_see = {r.get("see"): r for r in records if r.get("see")}
    ids = [int(r["id"][1:]) for r in records if str(r.get("id", "")).startswith("m")]
    nxt = max(ids) + 1 if ids else 1
    added = updated = 0
    for s in skills:
        see = f"{s['dir']}/SKILL.md"
        # One sentence, dense, so the ranker has something to match on. The description is
        # the trigger text the author wrote -- it is already shaped like a query.
        #
        # THE WHOLE DESCRIPTION IS INDEXED. It used to be `[:400]`, a number with no recorded
        # reason, and everything past it was invisible to `find` -- 3315 characters across 11
        # skills, which is the trigger words an author deliberately wrote being silently
        # thrown away. It bit twice: on 2026-09-03 a trigger appended to the END of three
        # descriptions changed nothing and `index` correctly reported "0 updated", and again
        # on 2026-09-11 with `D-35` in writing-oracles-for-delegation.
        #
        # MEASURED BEFORE REMOVING IT, both directions (2026-09-11):
        #   - queries built from the 11 discarded tails: capped 4/11 correct, uncapped 11/11.
        #   - the risk is the opposite one -- `keyword_rank` has NO length normalisation, so a
        #     longer record scores higher on term count alone and could crowd out the short
        #     non-skill records (max 164 chars, against ~430 for a capped skill). On 10
        #     general queries the top-1 result changed on 0. No crowding at this corpus size.
        #
        # THAT SECOND NUMBER IS THE ONE TO RE-RUN. It is a function of how many skills exist
        # and how long they are, not a property that was proved. If short memory records start
        # losing to skills, the fix is length normalisation in `keyword_rank`, NOT a cap here:
        # a cap does not rank better, it just deletes the evidence.
        fact = f"SKILL {s['name']}: {s['description']}"
        tags = (["skill"] + re.findall(r"[a-z]{4,}", s["name"]))[:6]
        prev = by_see.get(see)
        if prev:
            if prev.get("fact") != fact or prev.get("tags") != tags:
                prev["fact"], prev["tags"] = fact, tags
                updated += 1
        else:
            # fact, NOT fact[:240] -- it was truncated here and NOT on the update path, so
            # the same skill was indexed at two different lengths depending on whether it
            # was new. Nothing is capped now; both paths write the same `fact`.
            records.append({"id": f"m{nxt:04d}", "tags": tags, "fact": fact,
                            "see": see, "source_run": "", "hot": False, "recalled": 0,
                            "created": time.strftime("%Y-%m-%d")})
            nxt += 1
            added += 1
    if added or updated:
        mem.save(records)

    # THIS WARNING USED TO SAY THE TAIL WAS UNFINDABLE. It is not, since the cap went; saying
    # so would now be false, and a check that states a false reason is worse than no check.
    #
    # What replaces it watches the risk the cap was accidentally holding down. `keyword_rank`
    # scores on raw term count with no length normalisation, so a long record beats a short
    # one on overlap alone. Skill descriptions are the long records in this store; the memory
    # facts they compete with are a few lines. Measured 2026-09-11 at 11 skills: top-1 for 10
    # general queries was unchanged by uncapping. That is a measurement, not a guarantee, and
    # it decays as skills are added -- so the ratio gets reported rather than assumed.
    others = [len(r.get("fact", "")) for r in records
              if not str(r.get("see", "")).startswith("docs/skills/")]
    if others and skills:
        typical = sorted(others)[len(others) // 2]
        worst = max(skills, key=lambda x: len(x["description"]))
        ratio = len(worst["description"]) / max(typical, 1)
        if ratio >= 6:
            print(f"  NOTE: the longest skill description ({worst['name']}, "
                  f"{len(worst['description'])} chars) is {ratio:.0f}x the typical memory "
                  f"record ({typical}).")
            print("        `keyword_rank` has no length normalisation, so skills may be")
            print("        crowding out short memory facts. Re-measure before adding more:")
            print("        the fix is normalisation in memory.py, NOT a cap here.")

    # REBUILD THE VECTOR INDEX, or the new records are findable by keyword only.
    # Adding a skill and then not finding it by meaning is the failure this whole design
    # rests on avoiding -- the first two skills indexed here did not come back for the
    # obvious query until this ran.
    if added or updated:
        try:
            mem.build_index(mem.load(), verbose=False)
        except Exception as e:                               # noqa: BLE001
            print(f"  WARNING: vector index not rebuilt ({e}). Search is keyword-only "
                  f"until `python3 scripts/memory.py index` runs.")
    if verbose:
        print(f"  memory: {added} skill(s) added, {updated} updated "
              f"({len(skills)} total). `skills.py find` now finds them.")
    return added, updated


# ------------------------------------------------------------------ attached skills (plan 3c)
# Jas, 2026-09-29: "What a model gets = its role's skills plus the current task's skills,
# de-duplicated. Only the skills in that set are loaded, and only for that job." Attachments live in
# the stores -- a task's `skills` in models/FLOW.json, a role's `skills` in models/ROLES.json -- and are
# edited on the Flow tab, logged in their changelogs. The SKILL.md never says where it is attached,
# so the two cannot drift (docs/skills/SCHEMA.md).
MODELS = REPO / "projects" / "local-inference" / "models"
SKILL_SHARE = 0.10        # of the role's context window; plan 3c-i "a share of the role's context window"
CHARS_PER_TOKEN = 3.5     # the estimate coder.py and ask_q16 use for prose
LOADS = REPO / "workspace" / "skill-loads.jsonl"
INFERENCE = REPO / "projects" / "local-inference"     # settings.py lives here


def _json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def attached(role, task, flow=None, roles=None):
    """[(name, 'task' | 'role')] -- the task's skills first, then the role's, without duplicates."""
    flow = _json(MODELS / "FLOW.json") if flow is None else flow
    roles = _json(MODELS / "ROLES.json") if roles is None else roles
    t = next((x for x in flow.get("tasks", []) if x.get("id") == task), {}) if task else {}
    r = (roles.get("roles") or {}).get(role, {}) if role else {}
    out, seen = [], set()
    for name, why in [(n, "task") for n in t.get("skills") or []] + [(n, "role") for n in r.get("skills") or []]:
        if name not in seen:
            seen.add(name)
            out.append((name, why))
    return out


def role_window(role, roles=None):
    """The context window of the role's model, or None (a person/agent role, or unknown)."""
    roles = _json(MODELS / "ROLES.json") if roles is None else roles
    model = ((roles.get("roles") or {}).get(role) or {}).get("model")
    if not model:
        return None
    try:
        sys.path.insert(0, str(INFERENCE))
        import settings
        return int(settings.model_ctx(model))
    except Exception as e:                                           # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: skills: window of {model} unknown ({type(e).__name__}); using 32768", file=sys.stderr)
        return 32768


def skills_for(role, task, window=None, can_open=False, flow=None, roles=None, share=SKILL_SHARE,
               log=True, already=()):
    """The skills block for one job: {text, loaded, indexed, missing, budget_tokens}.

    Skills go in WHOLE, task skills first, while they fit the budget (`share` of the window). The
    rest are listed by name, one line and path, and the block SAYS they were not loaded and why --
    nothing is cut silently (never-truncate-a-record). With `can_open` the model is told to read the
    ones it needs (the Console's read_file); without it, the list says they are unavailable to it.
    A skill named in a store but absent from docs/skills/ is listed as MISSING, not skipped.
    Every call is logged to workspace/skill-loads.jsonl, so a job's log can name what it was given.
    """
    names = attached(role, task, flow, roles)
    empty = {"text": "", "loaded": [], "indexed": [], "missing": [], "budget_tokens": 0}
    if not names:
        return empty
    window = window or role_window(role, roles) or 32768
    budget = int(window * share)
    by = {s["name"]: s for s in all_skills()}
    loaded, indexed, missing, parts, used = [], [], [], [], 0
    for name, why in names:
        if name in already:
            loaded.append(name)             # the caller's prompt carries it in full already
            parts.append(f"(SKILL {name} ({why} skill) is given in full further down this prompt.)")
            continue
        s = by.get(name)
        if not s:
            missing.append(name)
            continue
        body = (REPO / s["dir"] / "SKILL.md").read_text(encoding="utf-8", errors="replace")
        cost = int(len(body) / CHARS_PER_TOKEN)
        if used + cost <= budget:
            used += cost
            loaded.append(name)
            parts.append(f"=== SKILL {name} ({why} skill) -- {s['dir']}/SKILL.md ===\n{body.strip()}\n"
                         f"=== END SKILL {name} ===")
        else:
            indexed.append((name, why, s, cost))
    head = (f"SKILLS FOR THIS JOB (role {role or '-'}, task {task or '-'}): the know-how attached to this "
            f"task and role on the Flow tab. Follow them; they carry the evidence for what they say.")
    lines = [head, ""] + parts
    if indexed:
        lines += ["", f"NOT LOADED -- over this job's skill budget of {budget:,} tokens "
                      f"({int(share * 100)}% of a {window:,}-token window):"]
        for name, why, s, cost in indexed:
            lines.append(f"  - {name} ({why} skill, ~{cost:,} tokens): {s['description'][:300]} -- {s['dir']}/SKILL.md")
        lines.append("  Read the ones this job needs with read_file before you start." if can_open else
                     "  You cannot open them in this job; say so if one of them is what you needed.")
    if missing:
        lines += ["", "MISSING -- attached but not in docs/skills/: " + ", ".join(missing)]
    out = {"text": "\n".join(lines).strip() + "\n", "loaded": loaded,
           "indexed": [n for n, *_ in indexed], "missing": missing, "budget_tokens": budget}
    if log:
        try:
            import time
            LOADS.parent.mkdir(parents=True, exist_ok=True)
            with LOADS.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "role": role,
                                    "task": task, **{k: out[k] for k in ("loaded", "indexed", "missing",
                                                                         "budget_tokens")}}) + "\n")
        except OSError as e:
            print(f"  NOTE: skill-loads.jsonl not written: {e}", file=sys.stderr)
    return out


def with_skills(prompt, task, role=None, can_open=False, already=()):
    """(prompt with this job's skills in front of it, skills_for's result). THE ONE CALL a worker
    makes (plan 3c-i: task_worker, the Console, ask_q16 and the built-ins). `role` defaults to the
    role whose task list has the task (task_routing.owner). A failure here never stops the job: it
    is printed, and the prompt goes out as it was."""
    try:
        # WHICH ROLE IS RUNNING THIS JOB: the dispatcher says so in HIVEMIND_ROLE (2026-09-30). Without
        # it the task's first owner in store order was assumed -- the Manager for session review -- so
        # the Senior's session reviews were given the Manager's role skills (skill-loads.jsonl).
        if role is None:
            import os
            role = os.environ.get("HIVEMIND_ROLE") or None
        if role is None:
            import task_routing
            role = task_routing.owner(task_routing.load(), task)
        sk = skills_for(role, task, can_open=can_open, already=already)
    except Exception as e:                                           # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: skills not loaded for task {task} ({type(e).__name__}: {e}); the job runs "
              f"without them", file=sys.stderr)
        return prompt, {"text": "", "loaded": [], "indexed": [], "missing": [], "error": str(e)}
    if sk["loaded"] or sk["indexed"] or sk["missing"]:
        print(f"  skills for {role}/{task}: loaded {', '.join(sk['loaded']) or '-'}"
              + (f"; NOT loaded (budget) {', '.join(sk['indexed'])}" if sk["indexed"] else "")
              + (f"; MISSING {', '.join(sk['missing'])}" if sk["missing"] else ""), file=sys.stderr)
    return ((sk["text"] + "\n=== THE JOB ===\n\n" + prompt) if sk["text"] else prompt), sk


def attachments_of(name, flow=None, roles=None):
    """({task ids}, {role keys}) this skill is attached to -- the card's 'Task skill for / Role skill for'."""
    flow = _json(MODELS / "FLOW.json") if flow is None else flow
    roles = _json(MODELS / "ROLES.json") if roles is None else roles
    return ([t["id"] for t in flow.get("tasks", []) if name in (t.get("skills") or [])],
            [k for k, r in (roles.get("roles") or {}).items() if name in (r.get("skills") or [])])


def find(query, limit=5):
    sys.path.insert(0, str(REPO / "scripts"))
    import memory as mem
    hits = [h for h in mem.recall(query, limit=limit * 3)
            if "skill" in (h.get("tags") or [])]
    return hits[:limit]


def new_skill(name, description):
    d = SKILLS / name
    if d.exists():
        print(f"{name} already exists at {d}", file=sys.stderr)
        return 1
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n"
        f"# {name.replace('-', ' ').capitalize()}\n\n"
        "Write what the next person has to know, and carry the EVIDENCE for every claim --\n"
        "the date, the command, the measured number, the source URL. A skill full of\n"
        "confident unsourced advice is worse than none, because it will be followed.\n\n"
        "The description above is what makes this fire. Write it as *when to reach for this*,\n"
        "listing the words someone would actually use -- not a summary of the contents.\n")
    print(f"created {d}/SKILL.md")
    return 0


def package():
    """Build .skill zips, for uploading to an account where they load automatically."""
    import zipfile
    out = REPO / "workspace" / "skill-packages"
    out.mkdir(parents=True, exist_ok=True)
    made = []
    for s in all_skills():
        d = REPO / s["dir"]
        z = out / f"{s['name']}.skill"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(d.rglob("*")):
                if f.is_file():
                    zf.write(f, f"{d.name}/{f.relative_to(d).as_posix()}")
        made.append(z)
    print(f"  {len(made)} package(s) in {out.relative_to(REPO)}")
    print("  These do NOT install themselves -- upload them where skills are managed for the")
    print("  account. The repository copy is the source; the uploaded copy is what fires.")
    return made


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    s1 = sub.add_parser("show"); s1.add_argument("name")
    s2 = sub.add_parser("find"); s2.add_argument("query"); s2.add_argument("--limit", type=int, default=5)
    s3 = sub.add_parser("new"); s3.add_argument("name"); s3.add_argument("--description", required=True)
    sub.add_parser("index")
    sub.add_parser("package")
    r = sub.add_parser("registry")
    r.add_argument("--session")
    r.add_argument("--harness", default="hivemind")
    r.add_argument("--skills-dir")
    r.add_argument("--ledger")
    r.add_argument("--out")
    r.add_argument("--check", action="store_true")
    a = ap.parse_args()

    if a.cmd == "list":
        for s in all_skills():
            print(f"  {s['name']:26} {s['description'][:110]}")
            if s["extra"]:
                print(f"  {'':26} + {', '.join(s['extra'])}")
        return 0
    if a.cmd == "show":
        d = SKILLS / a.name
        md = d / "SKILL.md"
        if not md.is_file():
            print(f"no skill '{a.name}'. Try: python3 scripts/skills.py list", file=sys.stderr)
            return 1
        print(md.read_text())
        for e in (parse(d) or {}).get("extra", []):
            print(f"\n--- {e} ---\n")
            print((d / e).read_text())
        return 0
    if a.cmd == "find":
        hits = find(a.query, a.limit)
        if not hits:
            print("  nothing matched. `skills.py list` shows everything.")
            return 0
        for h in hits:
            print(f"  {h.get('see', '')}\n    {h.get('fact', '')[:200]}")
        return 0
    if a.cmd == "new":
        return new_skill(a.name, a.description)
    if a.cmd == "index":
        sk = all_skills()
        write_index(sk)
        index_into_memory(sk)
        print(f"  wrote {INDEX.relative_to(REPO)} ({len(sk)} skills)")
        return 0
    if a.cmd == "package":
        package()
        return 0
    if a.cmd == "registry":
        if a.check:
            divs = registry_check(skills_dir=a.skills_dir, ledger=a.ledger, session=a.session)
            if divs:
                for d in divs:
                    print(d, file=sys.stderr)
                return 1
            base = Path(a.skills_dir or SKILLS)
            n = len(_store_skill_names(base))
            print(f"in sync: {n} skill(s) recorded and present")
            return 0
        rows = registry(session=a.session, harness=a.harness, skills_dir=a.skills_dir, ledger=a.ledger)
        ledger_path = Path(a.ledger or REGISTRY_LEDGER)
        print(f"  registry: wrote {len(rows)} row(s) to {ledger_path}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
