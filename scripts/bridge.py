#!/usr/bin/env python3
"""Turn a manager answer's `chunks` block into dispatchable work (t323, step 4 -- scoped -> briefed).

    python3 scripts/bridge.py <answer.md> --task tNNN            # dry run: print the plan
    python3 scripts/bridge.py <answer.md> --task tNNN --apply    # write it

Reads the LAST ```chunks fenced block in the answer (a JSON list; the contract is at the top of
scripts/tests/test_bridge.py) and writes, for each chunk: an oracle file declared red in
workspace/test-failures-ceiling.json, or a brief (TASK: line first) plus its row in
scripts/autonomy_loop.sh. All or nothing: any invalid chunk refuses the whole block (exit 2)
and writes nothing. It never touches git -- bridge_trigger.py commits what it wrote, and with
--freeze merges it once scope_lint passes it: that merge is the oracle freeze (D-35, amended
2026-09-28 -- the gates freeze, Claude reviews oracle and build together at merge).
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


NO_BLOCK = "no ```chunks block in the answer"


def _json_or_none(text):
    """The parsed value, or None when the text is not JSON (the caller treats that as 'not it')."""
    try:
        return json.loads(text)
    except ValueError:
        return None


def parse(text):
    """(chunks, errors) from the answer's last ```chunks block."""
    # ANY FENCE WHOSE INFO STRING HAS THE WORD `chunks` (2026-09-30): t264 wrote ```json chunks
    # around a valid list and was held for Jas as "no ```chunks block". A plain ```json is not
    # enough -- an answer carries other JSON (ceiling declarations) that is not the plan.
    blocks = re.findall(r"```[ \t]*(?:[\w-]+[ \t]+)*chunks(?:[ \t]+[\w-]+)*[ \t]*\n(.*?)\n```",
                        text, re.S | re.I)
    if not blocks:
        # A PLAIN ```json THAT CAN ONLY BE THE PLAN: a list whose every item is an oracle or
        # implementation chunk. t259's scope carried exactly that and was held for Jas as "no
        # block". An RCA's proposal list ("kind": "solution") or a ceiling declaration is not it.
        for b in reversed(re.findall(r"```json[ \t]*\n(.*?)\n```", text, re.S | re.I)):
            v = _json_or_none(b)            # not JSON -> simply not a candidate for the plan
            if isinstance(v, list) and v and all(isinstance(c, dict) and c.get("kind") in
                                                 ("oracle", "implementation") for c in v):
                return v, []
        return [], [NO_BLOCK]
    try:
        return json.loads(blocks[-1]), []
    except ValueError as e:
        return [], [f"the chunks block is not JSON: {e}"]


# THE COMMIT GATE'S RULES, IN EVERY BRIEF (t431, 2026-09-27). A build can pass its test and still
# be refused by the gate (library.py's block checks) for a rule the model never saw: p58 lost two
# complete implementations to `no-timeout`. Stating the rules costs a few lines per brief.
GATE_RULES = ("COMMIT GATE -- the change is refused, even with every test green, if it adds any of: "
              "subprocess.run, subprocess.check_output, subprocess.Popen or urlopen without timeout=; "
              "an except that swallows the error "
              "(bare except, or except ...: pass/continue with no report); a fixed /tmp path "
              "(use tempfile); a silent slice of text or a record ([:N] with no note that it was "
              "cut); finding a process by matching its name or command line (read /proc); a model call "
              "outside the harness contract; a script without an `if __name__ == \"__main__\":` guard.")

# THE EDIT PROTOCOL, IN EVERY BRIEF (t464, 2026-10-01). The sibling of GATE_RULES: gemma4:26b
# failed edits in the four shapes failure-schema.json records as harness-edit-application --
# broken indentation on a line-range edit, an unterminated string, a file re-emitted unchanged, a
# large file cut off mid-emission -- against a protocol no brief ever stated.
EDIT_PROTOCOL = ("EDIT PROTOCOL -- a line-range edit replaces WHOLE lines: the replacement must be "
                 "complete, valid code with the exact indentation of the block it replaces. Prefer "
                 "the smallest range that holds the change. Never re-emit a file unchanged. If a "
                 "file is too large to re-emit, say so instead of truncating it mid-emission.")

ORACLE_NAME = re.compile(r"scripts/tests/test_\w+\.py")
ROW = re.compile(r"^p(\d+)-")


def _inside(repo, p):
    """True when p resolves to somewhere under repo; a path outside is refused, never written."""
    try:
        return (repo / p).resolve().is_relative_to(repo.resolve())
    except (OSError, RuntimeError, ValueError):
        return False


def _empty(v):
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, list) and not v)


# STEPS (Jas, 2026-10-21: "if we can provide a schema this granular that could prevent the amount of
# reworks"). A chunk may carry an ordered list of steps, each saying where to edit, the file's text
# there NOW, the change, what is true after it, and what to do when it is not. scope_lint checks that
# each `now` is the file's real text; scope_gate requires steps of live scopers (--require-steps).
STEP_FIELDS = ("where", "now", "do", "expect", "if_not")
MAX_STEPS = 6                  # scope_lint.MAX_EDIT_SITES: more overflows the builder's output cap (t450)


def steps_shape(steps):
    """Reasons a chunk's `steps` value is malformed; empty when it is well formed."""
    if not isinstance(steps, list) or not steps:
        return ['"steps" must be a non-empty list of {where, now, do, expect, if_not}']
    out = []
    if len(steps) > MAX_STEPS:
        out.append(f'"steps" has {len(steps)} steps, at most {MAX_STEPS} -- split the chunk (t450)')
    for j, s in enumerate(steps, 1):
        if not isinstance(s, dict):
            out.append(f"step {j} is not a JSON object")
            continue
        missing = [f for f in STEP_FIELDS if f != "now" and _empty(s.get(f))]
        if "now" not in s:
            missing.insert(1, "now")       # an empty now is legal for a file the chunk creates
        if missing:
            out.append(f"step {j} is missing {', '.join(missing)}")
    return out


def validate(chunks, repo):
    """The list of reasons this block cannot be applied; empty when it can."""
    if not isinstance(chunks, list):
        return ["the chunks block is not a list"]
    if not chunks:
        return ["the chunks block is an empty list"]
    oracle_files = {c.get("file") for c in chunks if isinstance(c, dict) and c.get("kind") == "oracle"}
    errors = []
    for i, c in enumerate(chunks):
        if not isinstance(c, dict):
            errors.append(f"chunk {i}: not a JSON object")
            continue
        kind = c.get("kind")
        f = c.get("file")
        file_ok = isinstance(f, str) and f.strip() and not any(ch.isspace() for ch in f)
        if not isinstance(f, str) or not f.strip():
            errors.append(f'chunk {i}: missing "file"')
        elif any(ch.isspace() for ch in f):
            errors.append(f'chunk {i}: "file" names two paths: {f!r}')
        elif not _inside(repo, f):
            errors.append(f"chunk {i}: {f!r} is outside the repository")
        if kind == "oracle":
            if file_ok:
                if not ORACLE_NAME.fullmatch(f):
                    errors.append(f"chunk {i}: oracle file must be scripts/tests/test_*.py, got {f!r}. "
                                  f"scripts/tests/ is the ONLY test directory in this repository and "
                                  f"the only one run_tests.py and coder.py's sandbox baseline run, so "
                                  f"an oracle anywhere else would never be executed (t526)")
                else:
                    ot = c.get("oracle_text")
                    if not isinstance(ot, str) or not ot:
                        errors.append(f"chunk {i}: oracle chunk has no oracle_text")
                    else:
                        p = repo / f
                        # never overwrite a suite, frozen or not; identical text is an earlier run of this block
                        if p.is_file() and p.read_bytes() != ot.encode("utf-8"):
                            # SAY HOW TO FIX IT (2026-10-19): 24 of 82 chunks-invalid since 10-12 were a
                            # retry re-writing the oracle an earlier attempt already froze. The scope_gate
                            # now hands this message back to the session, so it must name the way out.
                            errors.append(f"chunk {i}: {f!r} already exists with different text -- a suite is "
                                          f"never overwritten. If it is this task's oracle from an earlier "
                                          f"attempt, DROP this oracle chunk and target the existing file as "
                                          f"it is (read_file it). If it is wrong or another suite, name a NEW "
                                          f"file scripts/tests/test_<name>.py instead")
        elif kind == "implementation":
            for field in ("file", "target", "single_change", "what_is_wrong", "done_when", "related_entries"):
                if _empty(c.get(field)):
                    errors.append(f"chunk {i}: implementation chunk is missing {field}")
            if c.get("steps") is not None:
                errors += [f"chunk {i}: {e}" for e in steps_shape(c.get("steps"))]
            t = c.get("target")
            if isinstance(t, str) and t.strip():
                if not _inside(repo, t):
                    errors.append(f"chunk {i}: target {t!r} is outside the repository")
                elif not (repo / t).exists() and t not in oracle_files:
                    errors.append(f"chunk {i}: target {t!r} neither exists nor is an oracle in this block")
                dw = c.get("done_when")
                if isinstance(dw, str) and Path(t).name not in dw:
                    errors.append(f"chunk {i}: done_when does not name the target suite {Path(t).name!r}")
        else:
            errors.append(f"chunk {i}: unknown kind {kind!r}")
    return errors


# t1117: name the suites in scripts/tests/ that read a file being edited.
def consumer_suites(repo, changed_file):
    tests = Path(repo) / 'scripts' / 'tests'
    if not tests.is_dir():
        return []
    name = Path(str(changed_file)).name
    if not name:
        return []
    # a whole token at the end of a path: app.py must not match inside my_app.py
    hit = re.compile(r'(?<![A-Za-z0-9_.-])' + re.escape(name) + r'(?![A-Za-z0-9_])')
    found = []
    for suite in tests.glob('*.py'):
        if hit.search(suite.read_text(encoding='utf-8', errors='ignore')):
            found.append(suite.name)
    return sorted(found)


# t1117: the brief text, lifted out of main so it can be tested without a model.
def brief_text(task, chunk, repo):
    nl = chr(10)
    consumers = consumer_suites(repo, str(chunk.get('file', '')))
    brief = [f'TASK: {task}']
    syms = chunk.get('symbols') or []
    # a symbol written as SYMBOLS: x by the model would double the label (p60)
    syms = [re.sub(r'^\s*SYMBOLS:\s*', '', str(s)) for s in syms]
    if syms:
        brief.append('SYMBOLS: ' + ', '.join(syms))
    # APPEND-ONLY EDITS TO A BIG NON-PYTHON FILE (t672): SYMBOLS slices only .py files, so a 270 KB
    # docs/LESSONS.md could never satisfy the window rule. append: true says the change is added at
    # the end; the builder is shown the file's tail instead of the whole file.
    if chunk.get('append') and not str(chunk.get('file', '')).endswith('.py'):
        brief.append('APPEND: at the end of the file (the builder is shown its last lines)')
    brief.append('')
    brief.append(f"ONE file: {chunk['file']}. {chunk['single_change']}")
    brief.append('')
    brief.append(f"WHY: {chunk['what_is_wrong']}")
    brief.append('')
    steps = chunk.get('steps')
    if isinstance(steps, list) and steps:
        brief.append("STEPS -- do them in order. Each NOW is the file's text at WHERE today (checked by "
                     "scope_lint); if it is not, stop and say so rather than guess.")
        for j, st in enumerate(steps, 1):
            st = st if isinstance(st, dict) else {}
            brief.append(f"{j}. WHERE: {st.get('where', '')}")
            brief.append('   NOW:')
            brief.extend('   | ' + ln for ln in str(st.get('now') or '(nothing yet -- the file is new)').splitlines())
            brief.append(f"   DO: {st.get('do', '')}")
            brief.append(f"   EXPECT: {st.get('expect', '')}")
            brief.append(f"   IF NOT: {st.get('if_not', '')}")
        brief.append('')
    brief.append(f"DONE WHEN {chunk['done_when']}")
    brief.append('')
    brief.append(f"FROZEN ORACLE: {chunk['target']} -- do not edit it.")
    author = str(chunk.get('behaviour_oracle') or '').strip()
    if author or consumers:
        brief.append('')
        parts = []
        if author:
            parts.append(author)
        if consumers:
            parts.append('CONSUMER SUITES (they read ' + str(chunk.get('file', ''))
                         + '; they must stay green): ' + ', '.join(consumers))
        brief.append('BEHAVIOUR THAT MUST STILL HOLD: ' + ' '.join(parts))
    rel = chunk.get('related_entries')
    if rel:
        brief.append('')
        joined = ', '.join(str(r) for r in rel) if isinstance(rel, list) else str(rel)
        brief.append(f'RELATED: {joined}')
    brief.append('')
    brief.append(GATE_RULES)
    brief.append(EDIT_PROTOCOL)
    return nl.join(brief) + nl

def sync_ceiling_count(repo, note=""):
    """Make the promised count equal the list, in the SAME act that changed the list.

    L1's finding, 2026-10-28: every bridge --freeze appended its new red oracle to
    workspace/test-failures-ceiling.json without moving workspace/test-failures-ceiling-count.json, so
    EVERY freeze in production redded check_workspace twice -- "the ceiling may fall and must not rise"
    (the count lagged the list) and "no recorded verdict" (the oracle had never been run). 0d5526d64
    (t1493) was the first; fixing that instance by hand did not fix the class.

    The count is a promise and the list is the fact; a fact with no number is a silent rise. This
    function moves the promise to the fact and says why.

    IT DOES NOT RECORD A VERDICT. The freeze does not run the oracle -- `red_because` is a supplied
    reason, not a measurement -- so claiming a red verdict here would be an unverified claim, which is
    the thing this repository spends its checks on. The next sweep runs the oracle and records what it
    actually does; until then the entry reads "no recorded verdict", which is honest and is exactly what
    that check exists to say.
    """
    repo = Path(repo)
    ceil_path = repo / "workspace" / "test-failures-ceiling.json"
    count_path = repo / "workspace" / "test-failures-ceiling-count.json"
    try:
        ceil = json.loads(ceil_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    declared = len(ceil.get("allowed_to_fail") or [])
    try:
        cnt = json.loads(count_path.read_text(encoding="utf-8")) if count_path.is_file() else {}
    except ValueError:
        cnt = {}
    if cnt.get("count") == declared:
        return None                      # already true: say nothing, change nothing
    cnt["count"] = declared
    cnt["at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    why = (cnt.get("why") or "").strip()
    add = f"{time.strftime('%Y-%m-%d')} freeze: count set to the {declared} declared"
    if note:
        add += f" ({note})"
    cnt["why"] = (why + " | " + add).strip(" |")
    count_path.write_text(json.dumps(cnt, indent=1) + "\n", encoding="utf-8")
    return declared


def main(argv):
    """0 applied or planned, 2 refused (nothing written)."""
    ap = argparse.ArgumentParser(description="Write a manager answer's chunks block into briefs, oracles, the ceiling and the work list.")
    ap.add_argument("answer", help="the answer file holding the last ```chunks block")
    ap.add_argument("--task", required=True)
    ap.add_argument("--repo", default=str(REPO))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    repo = Path(a.repo)

    chunks, errors = parse(Path(a.answer).read_text(encoding="utf-8"))
    if not errors:
        errors = validate(chunks, repo)
    if errors:
        for e in errors:
            print(f"REFUSED: {e}")
        return 2

    # work-list state, read from under the repo -- never from the live one
    loop_path = repo / "scripts" / "autonomy_loop.sh"
    loop_text = loop_path.read_text(encoding="utf-8") if loop_path.is_file() else ""
    loop_lines = loop_text.split("\n")
    row_idx = [i for i, l in enumerate(loop_lines) if ROW.match(l) and l.count("|") >= 3]
    n = max([int(ROW.match(loop_lines[i]).group(1)) for i in row_idx] or [0])
    done_pairs = set()
    for i in row_idx:
        parts = loop_lines[i].split("|")
        if len(parts) >= 4:
            done_pairs.add((parts[2].strip(), parts[3].strip()))

    plan = []
    for c in chunks:
        if c.get("kind") == "oracle":
            p = repo / c["file"]
            # identical text = an earlier run of this block; different text was refused above
            if p.is_file() and p.read_bytes() == (c.get("oracle_text") or "").encode("utf-8"):
                continue
            plan.append(("oracle", c, None))
        else:
            f, t = c["file"], c["target"]
            if (f, t) in done_pairs:
                continue
            n += 1
            name = f"p{n}-{a.task}-{Path(f).stem.replace('_', '-').lower()}"
            plan.append(("implementation", c, name))

    # NUMBERS THAT NEVER REPEAT (2026-09-28): on --apply the p numbers come from the shared
    # counter (scripts/pnumber.py), not "highest row in this copy + 1" -- a bridge worktree and
    # the compliance enrolment on main both took p60. A dry run claims nothing.
    impl = [i for i, (k, _c, _n) in enumerate(plan) if k == "implementation"]
    if a.apply and impl:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import pnumber
        first = pnumber.claim(repo, n - len(impl), count=len(impl))
        for j, i in enumerate(impl):
            k, c, _old = plan[i]
            # LOWER CASE (2026-09-29): every reader of autonomy_loop.sh matches p\d+-[a-z0-9-]+, so
            # p154-t124-HOW-I-TESTED-THE-MODELS was invisible to the board -- t124 read as Scoped.
            plan[i] = (k, c, f"p{first + j}-{a.task}-{Path(c['file']).stem.replace('_', '-').lower()}")

    for kind, c, name in plan:
        if kind == "oracle":
            print(f"PLAN oracle {c['file']} + failure-ceiling declaration")
        else:
            print(f"PLAN {name}: workspace/briefs/brief-{name}.txt + work-list row")
    if not a.apply:
        return 0

    written_oracles = []
    rows = []
    for kind, c, name in plan:
        if kind == "oracle":
            p = repo / c["file"]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(c["oracle_text"].encode("utf-8"))
            written_oracles.append(c)
        else:
            brief_dir = repo / "workspace" / "briefs"
            brief_dir.mkdir(parents=True, exist_ok=True)
            (brief_dir / f"brief-{name}.txt").write_text(brief_text(a.task, c, repo), encoding="utf-8")
            rows.append(f"{name}|brief-{name}.txt|{c['file']}|{c['target']}")

    if written_oracles:
        ceil_path = repo / "workspace" / "test-failures-ceiling.json"
        ceil = json.loads(ceil_path.read_text(encoding="utf-8")) if ceil_path.is_file() else {}
        today = time.strftime("%Y-%m-%d")
        allowed = ceil.setdefault("allowed_to_fail", [])
        awaiting = ceil.setdefault("_oracles_awaiting_their_fix", {})
        for c in written_oracles:
            name = Path(c["file"]).name
            if name not in allowed:
                allowed.append(name)
            reason = c.get("red_because") or "oracle ahead of implementation"
            awaiting[name] = f"{a.task} ({today}): {reason} Remove when its implementation merges."
        ceil_path.write_text(json.dumps(ceil, indent=1, ensure_ascii=True) + "\n", encoding="utf-8")
        # THE COUNT MOVES WITH THE LIST, IN THE SAME ACT (L1's finding): a freeze that adds an oracle
        # without moving the promised number reds the ceiling check for every lane until a human
        # notices. It does NOT record a verdict -- the freeze never ran the oracle, and the next sweep
        # does that honestly.
        sync_ceiling_count(repo, f"{len(written_oracles)} oracle(s) frozen for {a.task}")

    if rows:
        # the rows sit inside a heredoc in the middle of the script: a line after the loop is read by nothing
        lines = loop_path.read_text(encoding="utf-8").split("\n")
        last = max(i for i, l in enumerate(lines) if ROW.match(l) and l.count("|") >= 3)
        for j, row in enumerate(rows):
            lines.insert(last + 1 + j, row)
        loop_path.write_text("\n".join(lines), encoding="utf-8")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
