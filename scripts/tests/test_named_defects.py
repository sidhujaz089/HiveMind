#!/usr/bin/env python3
"""A defect named with a file:line must acquire a task id in the turn that named it.

WHY THIS EXISTS. workspace/rca/eff7a7555749.md: at 2026-08-27 01:54 a session diagnosed two
real defects out loud -- coder.py:951 writing `git diff main..{branch}`, and the trailer
charset -- with file, line and mechanism (transcript seq 5822, 5824, 5838). Neither ever
became a record. The session closed at 06:52 with a 'final verified state' block (seq 7106,
7521) that did not mention them, and 47 days later coder.py:2217 still writes the form its own
comment forbids. The RCA's rule 5: the defect-naming detector sits at the transcript endpoint
-- for every assistant turn pairing a `file:line` with a fix-intent verb, require a `tNNN` in
that turn's TOOL OUTPUT, print every miss, exit 0 first (the check_declared_intent.py
precedent: a blocker that fires on ordinary prose is disabled within a week).

WHAT THIS PINS, and the two ways a build can fake it.
1. The id must be in the TOOL OUTPUT, not in the sentence. A `tNNN` the assistant writes into
   prose is a claim; a `tNNN` in a tool result is a record that exists on disk (the line
   `taskstore.py add` prints). Case 3 is the only assertion that separates the two.
2. The pairing is PER TURN. A build that asks 'does any tNNN appear anywhere in the session'
   passes every case that puts a number somewhere. Cases 3 and 9 are the answers to that.
3. The vocabulary is the RCA's own, verbatim -- a list of phrases nobody ever wrote reports
   decisions nobody made, and a wider list is the gate everyone routes around. Three verbs,
   four extensions, distinct real citations: one placeholder string cannot satisfy the suite.

AUTHORSHIP: drafted by the manager lane; the bridge freezes it. The implementation is a
separate dispatch and may not edit this file (D-35).

RED ON MAIN FOR ONE REASON: scripts/check_named_defects.py does not exist, so
named_defect_findings() is not callable and the module cannot be run. Nothing else.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MOD_PATH = REPO / "scripts" / "check_named_defects.py"
MODULE_NAME = "check_named_defects"

DEFECT_SENTENCE = (
    "I re-read coder.py:951 -- it writes `git diff main..{branch}`, the exact form I "
    "supposedly fixed for rendering every later commit as a deletion. This needs fixing."
)


def _load():
    if not MOD_PATH.exists():
        return None
    spec = importlib.util.spec_from_file_location(MODULE_NAME, str(MOD_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _turn(text, tool_output=""):
    return {"text": text, "tool_output": tool_output}


def main():
    fails = []

    def check(label, cond, detail=""):
        print(f"  [{'ok' if cond else 'BAD'}]    {label}{'  ' + detail if detail else ''}")
        if not cond:
            fails.append(f"{label} :: {detail}")

    mod = _load()
    if mod is None:
        print(f"FAIL: {MOD_PATH.relative_to(REPO)} does not exist -- this suite is its fixture")
        return 1
    if not hasattr(mod, "named_defect_findings"):
        print("FAIL: check_named_defects defines no named_defect_findings() -- "
              "that function IS the change (turns -> list of findings)")
        return 1
    ndf = mod.named_defect_findings

    # 1. THE THING IT IS FOR: the seq 5827 shape, no id in the turn's tool output.
    out = ndf([_turn(DEFECT_SENTENCE)])
    check("a named defect with no id is reported", len(out) == 1, f"{len(out)} finding(s): {out}")
    check("the finding names the file:line a reader must act on",
          bool(out) and "coder.py:951" in out[0], str(out[:1]))
    check("findings are non-empty strings", all(isinstance(o, str) and o.strip() for o in out),
          str(out[:1]))

    # 2. THE RECORD DISCHARGES IT: a task id in the turn's tool output.
    out = ndf([_turn(DEFECT_SENTENCE,
                     "created workspace/tasks/t1036--record-merge-base-vs-two-dot-diff-noise")])
    check("an id in that turn's tool output silences it", out == [], str(out))

    # 3. A NUMBER IN PROSE IS A CLAIM, NOT A RECORD. The defect text says the number; the
    #    tool output is empty. A build that scans prose+tools together reports 0 and fails.
    out = ndf([_turn(DEFECT_SENTENCE + " Filed it as t1036.", "")])
    check("an id named in the sentence alone does NOT discharge it", len(out) == 1, str(out))

    # 4. NAG GUARD. A fix-intent verb with no file:line is prose, not a defect record.
    out = ndf([_turn("The trailer charset needs fixing in the commit hook.")])
    check("a fix-intent verb with no file:line is silent", out == [], str(out))

    # 5. A citation is not a defect. file:line with no fix-intent verb must stay quiet, or the
    #    WARN fires on every line the session quotes and gets switched off (G10).
    out = ndf([_turn("scripts/session_end.py:1263 is where commitment_debts lives.")])
    check("a bare file:line citation is silent", out == [], str(out))

    # 6. THE RCA'S OWN VERBS, verbatim -- one of each, in its own turn.
    for s in ["projects/local-inference/coder.py:951 needs fixing.",
              "projects/local-inference/coder.py:951 was left with the wrong form.",
              "projects/local-inference/coder.py:951 -- fix applied to one site only."]:
        out = ndf([_turn(s)])
        check(f"verb caught: {s[:46]}  (RCA eff7a7555749 5)",
              len(out) == 1 and "coder.py:951" in (out[0] if out else ""), str(out))

    # 7. THE RCA'S FOUR EXTENSIONS. library/Foo.kt does not exist on disk on purpose: the
    #    defect is in the transcript, so the check must not stat anything.
    for cite in ["projects/local-inference/coder.py:951",
                 "AGENTS.md:191",
                 "scripts/autonomy_loop.sh:12",
                 "library/Foo.kt:7"]:
        out = ndf([_turn(f"{cite} needs fixing.")])
        check(f"extension .{cite.split('.')[-1].split(':')[0].split('/')[-1]}: {cite}",
              len(out) == 1 and cite in out[0], str(out))

    # 8. TWO DISTINCT DEFECTS IN ONE TURN are two records, not one.
    out = ndf([_turn("projects/local-inference/coder.py:951 needs fixing, and "
                     "docs/skills/never-truncate-a-record/SKILL.md:120 needs fixing too.")])
    check("two citations in one turn are two findings", len(out) == 2, f"{len(out)}: {out}")
    check("each finding names its own citation",
          len(out) == 2 and any("coder.py:951" in o for o in out)
          and any("SKILL.md:120" in o for o in out), str(out))

    # 9. THE PAIRING IS PER TURN. The id lands in a LATER turn's tool output; the defect's
    #    own turn is still unrecorded. A session-wide 'any tNNN anywhere' build reports 0 here.
    out = ndf([_turn(DEFECT_SENTENCE, ""),
               _turn("recorded it.", "created workspace/tasks/t1036--x")])
    check("an id in a later turn does not discharge this turn's defect", len(out) == 1, str(out))

    # 10. WARN, NOT A BLOCKER (check_declared_intent.py:327 returns 0 unconditionally). A
    #     gate that blocks session_end on ordinary prose is disabled inside a week.
    r = subprocess.run([sys.executable, str(MOD_PATH)], cwd=str(REPO),
                       capture_output=True, text=True, timeout=120)
    check("running the script exits 0 even when it prints findings", r.returncode == 0,
          f"exit {r.returncode}: {(r.stderr or r.stdout)[:200]}")

    print()
    if fails:
        print(f"FAIL: {len(fails)} problem(s)")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
