#!/usr/bin/env python3
"""Does this delivery contain anything the brief did not ask for?

Jas, 2026-09-14: "The coverage loss one sounds analogous to when we have to back
up on the network storage, where there is a routine that checks for completion.
Perhaps, if these are the same things, that routine can be adapted to our
production workflows."

THEY ARE THE SAME SHAPE. D-29: a backup that reports success is not a backup that
is complete, which is why backups here are dated, never overwritten, and DIFFED
against the previous copy before the old one is touched -- "a backup only written
is a hope". A merge that passes merge_gate.py is not a merge without regression,
for the identical reason: green means NO DECLARED FAILURE, not no loss.

THE CASE THIS IS FOR. coder/brief-8f25f27d passed its frozen suite AND
merge_gate, merged as 5f0971a, and quietly raised the shortest truncation the
gate could detect from 10 characters to 100 (t285). The brief had asked for ONE
change -- widen the value side. TWO were delivered; the second narrowed the digit
floor and nothing caught it. A hand audit the next day did.

THE INVERSION, and it is the whole design. Do NOT enumerate the ways a patch can
regress: that set is unbounded, and a checker built from it is a term library
that grows forever and is never finished. Enumerate what the brief ASKED FOR --
which is short, written down, and already on disk in workspace/briefs -- and flag
everything else in the diff as UNACCOUNTED. The approved set is small and
nameable; the set of possible regressions is not. Same inversion as the
gate-closures ledger.

WHAT THIS IS NOT. It is not a bug finder and does not judge whether a change is
correct or good. An unaccounted change is not necessarily wrong -- it is
necessarily UNREVIEWED against the thing that authorised the work. The verdict is
"nobody asked for this", and the answer is either to widen the brief or to drop
the change, both of which are decisions for a person.

TWO STAGES, cheapest first.

  A. FILE LEVEL, no model. A changed file the brief never names is unaccounted.
     Catches the whole class of "touched something nobody mentioned" for free,
     and needs no judgement.
  B. HUNK LEVEL, reviewer model. For a file the brief DOES name, each hunk is put
     to the model against the brief text: does this implement part of what was
     asked, or is it an additional change? 8f25f27d lives here -- both of its
     changes were in a file the brief named, so stage A alone would have passed it.

This script never merges and never reverts. It reports. Claude is the gate (D-34).
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import roles  # noqa: E402  -- the model behind a role comes from SERVING.json
BRIEFS = REPO / "workspace" / "briefs"
LEDGER = REPO / "workspace" / "merge-accounting.jsonl"


def git(*args):
    r = subprocess.run(["git", *args], cwd=str(REPO), capture_output=True,
                       text=True, timeout=300)
    return r.returncode, r.stdout, r.stderr


def brief_for_branch(branch):
    """The brief a coder branch was cut from, or None.

    coder.py labels a branch "brief-" + sha256(BRIEF PATH)[:8] -- the path string,
    not the contents. So the mapping is recovered by hashing the candidate paths.
    (That it hashes the path means two different briefs written to one path share a
    label; worth fixing separately, and it does not affect this lookup.)
    """
    m = re.search(r"brief-([0-9a-f]{8})", branch or "")
    if not m:
        return None
    want = m.group(1)
    for p in sorted(BRIEFS.glob("*")):
        if not p.is_file():
            continue
        # CONTENT first: since t263, --brief-file labels a branch by what the brief SAYS,
        # the same as --brief text always did. The path hashes stay for branches cut
        # before that, when parallel_dispatch passed the path as the task.
        try:
            content = p.read_text(encoding="utf-8")
        except OSError:
            content = None
        cands = ([content] if content is not None else []) + [str(p), str(p.resolve())]
        for cand in cands:
            if hashlib.sha256(cand.encode()).hexdigest()[:8] == want:
                return p
    return None


def brief_from_commit(branch):
    """The brief coder.py wrote into the branch's commit body (t311), or None."""
    rc, out, _ = git("log", "-1", "--format=%B", branch)
    if rc != 0 or "--- BRIEF AS DISPATCHED ---" not in out:
        return None
    return out.split("--- BRIEF AS DISPATCHED ---", 1)[1].split("--- END BRIEF ---", 1)[0].strip()


def changed_files(base, head):
    rc, out, _ = git("diff", "--name-only", f"{base}...{head}")
    return [x for x in out.splitlines() if x.strip()] if rc == 0 else []


def hunks(base, head, path):
    """(header, body) per hunk. Zero context, so a hunk is the CHANGE and not its
    neighbourhood -- with context, one asked-for edit drags the lines around it into
    the same hunk and an unrequested change hides inside an accounted one."""
    rc, out, _ = git("diff", "-U0", f"{base}...{head}", "--", path)
    if rc != 0:
        return []
    out_h, cur, head_line = [], [], None
    for line in out.splitlines():
        if line.startswith("@@"):
            if head_line is not None:
                out_h.append((head_line, "\n".join(cur)))
            head_line, cur = line, []
        elif head_line is not None:
            cur.append(line)
    if head_line is not None:
        out_h.append((head_line, "\n".join(cur)))
    return out_h


def reindented(body):
    """Lines a hunk removed and re-added with ONLY their indentation changed.

    Session-review finding da178845913e (t563): twice a delivered diff re-indented lines it
    was not asked to touch -- "a pattern rather than a slip, and mechanically detectable".
    A model judging the hunk reads it as part of the asked-for edit; this does not judge,
    it counts. Blank lines are ignored."""
    gone = {}
    for line in body.splitlines():
        if line.startswith("-") and not line.startswith("---") and line[1:].strip():
            gone.setdefault(line[1:].strip(), set()).add(len(line[1:]) - len(line[1:].lstrip()))
    out = []
    for line in body.splitlines():
        if line.startswith("+") and not line.startswith("+++") and line[1:].strip():
            s, ind = line[1:].strip(), len(line[1:]) - len(line[1:].lstrip())
            if s in gone and ind not in gone[s]:
                out.append(s)
    return out


JUDGE = """A brief authorised a change. Below is the brief, and ONE hunk from what was
delivered. Decide only this: does the hunk implement part of what the brief asked for?

You are NOT judging whether the change is correct, good, or safe. A change can be an
excellent idea and still be unaccounted -- "unaccounted" means the brief did not ask
for it, nothing more.

Answer in exactly this shape, two lines:
ACCOUNTED: yes | no
WHY: one sentence, naming the part of the brief it implements, or naming what it does
that the brief never mentions.

If you cannot tell, answer no. An unaccounted hunk costs a person one glance; an
unrequested change merged silently is the defect this exists to catch.

=== THE BRIEF ===
{brief}

=== THE FILE ===
{path}

=== THE HUNK ===
{hunk}
"""


def judge(brief_text, path, hunk, model, ask=None):
    """(accounted: bool, why: str). `ask` is injected by the fixtures."""
    prompt = JUDGE.format(brief=brief_text, path=path, hunk=hunk)
    reply = ask(prompt, model) if ask else _ask_model(prompt, model)
    accounted, why = None, ""
    for line in (reply or "").splitlines():
        s = line.strip()
        if s.upper().startswith("ACCOUNTED:"):
            accounted = s.split(":", 1)[1].strip().lower().startswith("yes")
        elif s.upper().startswith("WHY:"):
            why = s.split(":", 1)[1].strip()
    # UNREADABLE MEANS UNACCOUNTED. A judge whose unknown-input behaviour is "pass"
    # is not a judge -- the same rule preflight_brief.decide() is built on.
    if accounted is None:
        return False, f"unreadable verdict from the model: {(reply or '')[:200]!r}"
    return accounted, why or "(no reason given)"


def _ask_model(prompt, model):
    import urllib.request
    sys.path.insert(0, str(REPO / "projects" / "local-inference"))
    import settings                                             # noqa: E402
    # think "low" (t330): the answer is two lines, ACCOUNTED and WHY. The coder profile's
    # think:high is for writing code across many turns, and at 1,200 tokens it can spend the
    # whole budget reasoning and return nothing -- which judge() then reads as "unaccounted".
    body = settings.request("coder", model, [{"role": "user", "content": prompt}],
                            num_predict=1200, think="low")
    # Through infer (2026-10-06): the role's model is Swift on vLLM, which Ollama does not serve.
    import infer                                                # noqa: E402
    d = infer.chat(body, timeout=1800)
    if d.get("done_reason") == "length":   # t238: a cut-off reply is said, not misread
        print(f"  NOTE: reply hit its num_predict limit (done_reason=length) -- it is cut off",
              file=sys.stderr)
    return (d.get("message") or {}).get("content") or ""


def account(base, head, brief_text, model=None, ask=None,
            skip_hunks=False):
    """The report. Every changed file and hunk lands in exactly one bucket."""
    model = model or roles.tag("manager", "qwen3.8:27b-mtp-bf16")
    findings = []
    for path in changed_files(base, head):
        # STAGE A -- mechanical. Does the brief name this file at all?
        named = path in brief_text or Path(path).name in brief_text
        if not named:
            findings.append({"file": path, "hunk": None, "accounted": False,
                             "stage": "file",
                             "why": "the brief never names this file"})
            continue
        # STAGE A2 -- mechanical, before any model: lines whose only change is indentation.
        for header, body in hunks(base, head, path):
            moved = [s for s in reindented(body) if s not in brief_text]
            if moved:
                findings.append({"file": path, "hunk": header, "accounted": False,
                                 "stage": "indent",
                                 "why": f"{len(moved)} line(s) re-indented with no other change, "
                                        f"e.g. {moved[0][:60]!r} -- the brief does not name them"})
        if skip_hunks:
            findings.append({"file": path, "hunk": None, "accounted": True,
                             "stage": "file", "why": "named by the brief"})
            continue
        # STAGE B -- per hunk, against the brief.
        for header, body in hunks(base, head, path):
            ok, why = judge(brief_text, path, f"{header}\n{body}", model, ask=ask)
            findings.append({"file": path, "hunk": header, "accounted": ok,
                             "stage": "hunk", "why": why})
    return findings


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--branch", required=True)
    ap.add_argument("--base", default=None,
                    help="merge base; defaults to the base of --branch and main")
    ap.add_argument("--brief", default=None, help="override the brief lookup")
    ap.add_argument("--model", default=roles.tag("manager", "qwen3.8:27b-mtp-bf16"))
    ap.add_argument("--files-only", action="store_true",
                    help="stage A only -- no model, instant, catches less")
    a = ap.parse_args()

    brief_path = Path(a.brief) if a.brief else brief_for_branch(a.branch)
    kept = None if (brief_path and brief_path.is_file()) else brief_from_commit(a.branch)
    if kept is not None:            # the file is gone or rewritten; the branch kept its brief (t311)
        print(f"  brief file not found; using the brief the branch carries in its commit")
        brief_path = Path(tempfile.mkdtemp(prefix="brief-")) / "kept.txt"
        brief_path.write_text(kept, encoding="utf-8")
    if not brief_path or not brief_path.is_file():
        print(f"\n  NO BRIEF FOUND for {a.branch}.")
        print("  Refusing rather than accounting against nothing: with no brief every")
        print("  change is trivially 'accounted' and the check would report a clean")
        print("  pass over an empty standard (L-146). Pass --brief.\n")
        return 2

    base = a.base
    if not base:
        rc, out, _ = git("merge-base", "main", a.branch)
        if rc != 0:
            print(f"  cannot find a merge base for {a.branch}")
            return 2
        base = out.strip()

    brief_text = brief_path.read_text(errors="replace")
    t0 = time.time()
    findings = account(base, a.branch, brief_text, a.model,
                       skip_hunks=a.files_only)

    bad = [f for f in findings if not f["accounted"]]
    print(f"\n  brief: {brief_path}")
    print(f"  base:  {base[:12]}   head: {a.branch}")
    print(f"  {len(findings)} change(s) examined, {len(bad)} unaccounted"
          f"  ({time.time()-t0:.0f}s)\n")

    for f in bad:
        where = f"{f['file']}  {f['hunk']}" if f["hunk"] else f["file"]
        print(f"  UNACCOUNTED  {where}")
        print(f"               {f['why']}")

    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "at": time.strftime("%Y-%m-%dT%H%M%SZ", time.gmtime()),
                "branch": a.branch, "base": base, "brief": str(brief_path),
                "model": a.model, "files_only": a.files_only,
                "examined": len(findings), "unaccounted": len(bad),
                "findings": findings}, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"  (not recorded: {e})", file=sys.stderr)

    if not findings:
        # NOTHING EXAMINED IS NOT "EVERY CHANGE ACCOUNTED FOR" (L-146). A branch with no diff against
        # its base, or a base that is wrong, yields zero findings -- and used to print the clean verdict.
        print("  NO CHANGES FOUND between base and head -- nothing was accounted. Check the base.\n")
        return 2
    if not bad:
        # SILENT ON A CLEAN DELIVERY, on purpose. A checker that says something
        # every time trains the reader to skip it, and then it is decoration. The
        # oracle pins this half too: a checker that flags everything scores green
        # on "it catches the bad one".
        print("  Every change is accounted for by the brief.\n")
        return 0
    print("\n  ==> A person decides: widen the brief, or drop the change.")
    print("      Unaccounted does not mean wrong. It means nobody asked for it.\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
