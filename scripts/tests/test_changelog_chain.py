#!/usr/bin/env python3
"""The Flow changelog must be replayable: row N's `after` is row N+1's `before` (C2, t8651).

A changelog that cannot be replayed is a log, not a ledger. `FLOW-CHANGELOG.jsonl` has 86 rows and **six
do not chain** -- measured 2026-10-26: (17,18), (35,36), (37,38), (50,51), (79,80), (82,83). At each one
the file on disk was not what the previous logged change wrote, so the chart was changed outside
`flow_change.py`. C1 now refuses that going forward; this is the other half -- the history already on
disk.

The breaks cannot be RECONSTRUCTED: the state that should have followed row 17 is not recoverable from
anything here. So they are DECLARED, one entry each, with what actually diverged -- and the check fails on
any break that is not declared. That is the shape `docs/EXTERNAL-POINTERS.md` already uses for dangling
document references: a declared absence is quiet, an undeclared one is a finding.

Why not simply ignore the six: because a check with six standing exceptions is one nobody reads. Why not
"fix" them by rewriting the rows: because that would be fabricating a history, and the rows are the only
record of what was actually applied.

What this refuses:

  * an undeclared break (a seventh would otherwise arrive looking exactly like the six);
  * a declaration for a break that no longer exists -- a stale excuse, which is how an exception list
    rots into fiction;
  * a declaration with no reason, or with no `diverged` record of what was compared;
  * and it must still PASS on the current file, or the guard gets disabled rather than obeyed.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_workspace as cw  # noqa: E402

LIVE_LOG = ROOT / "projects" / "local-inference" / "models" / "FLOW-CHANGELOG.jsonl"
LIVE_DECL = ROOT / "workspace" / "flow-changelog-declared.json"
FAILS = []


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


def main():
    f = cw.changelog_breaks

    # 1. A CHAIN IS CLEAN.
    clean = [{"n": 1, "before": None, "after": {"v": 1}},
             {"n": 2, "before": {"v": 1}, "after": {"v": 2}},
             {"n": 3, "before": {"v": 2}, "after": {"v": 3}}]
    check("a contiguous changelog has no breaks", f(clean) == [], f(clean))

    # 2. A BREAK IS FOUND, and it is named by the pair of rows.
    broken = [{"n": 1, "before": None, "after": {"v": 1}},
              {"n": 2, "before": {"v": 99}, "after": {"v": 2}}]
    got = f(broken)
    check("a row whose before is not the previous after is a break", got == [(1, 2)], got)

    # 3. THE LIVE FILE'S SIX BREAKS ARE FOUND, and only those.
    live = [json.loads(x) for x in LIVE_LOG.read_text(encoding="utf-8").splitlines() if x.strip()]
    got = f(live)
    check("the committed changelog has exactly six breaks", len(got) == 6, got)
    check("and they are the six measured on 2026-10-26",
          got == [(17, 18), (35, 36), (37, 38), (50, 51), (79, 80), (82, 83)], got)

    # 4. THE DECLARATION FILE COVERS EXACTLY THEM. A declared absence is quiet; an undeclared one is the
    #    finding, and a stale declaration is the other half of the same rot.
    declared = json.loads(LIVE_DECL.read_text(encoding="utf-8")).get("declared") or []
    keys = {tuple(d.get("between") or []) for d in declared}
    check("every live break is declared", set(got) <= keys, sorted(keys))
    check("and nothing is declared that is not a break", keys <= set(got), sorted(keys - set(got)))
    check("every declaration says why",
          all(str(d.get("why") or "").strip() for d in declared),
          [d.get("between") for d in declared if not d.get("why")])
    check("every declaration records what diverged",
          all(str(d.get("diverged") or "").strip() for d in declared),
          [d.get("between") for d in declared if not d.get("diverged")])

    # 5. THE CHECK ITSELF PASSES ON THE COMMITTED FILE -- a guard that fails today gets disabled.
    problems = cw.changelog_problems(LIVE_LOG, LIVE_DECL)
    check("the committed changelog passes with its declarations", problems == [], problems[:3])

    # 6. AND IT FAILS WHEN A SEVENTH ARRIVES. That is the whole point of the declaration list.
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "log.jsonl"
        extra = live + [{"n": 999, "before": {"v": "not what row 86 wrote"}, "after": {"v": "x"}}]
        p.write_text("\n".join(json.dumps(r) for r in extra) + "\n", encoding="utf-8")
        problems = cw.changelog_problems(p, LIVE_DECL)
        check("an undeclared break is a problem", len(problems) == 1, problems[:2])
        check("  and it names the pair", bool(problems) and "999" in problems[0], problems[:1])

        # 7. A DECLARATION WITH NO REASON IS NOT A DECLARATION.
        bad_decl = Path(d) / "decl.json"
        bad_decl.write_text(json.dumps({"declared": [{"between": [999, 1000], "why": "", "diverged": "x"}]}),
                            encoding="utf-8")
        problems = cw.changelog_problems(p, bad_decl)
        # The fixture also carries the six real breaks (the reasonless file covers none of them), so the
        # property is that the 999 break is STILL REPORTED -- not a count.
        check("a reasonless declaration does not cover a break",
              any("999" in x for x in problems), [x[:80] for x in problems[:2]])

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
