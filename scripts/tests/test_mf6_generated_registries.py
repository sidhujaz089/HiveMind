#!/usr/bin/env python3
"""mf6 oracle: registries are generated, not hand-merged.

Seen: a branch could not merge because of conflicts in FLOW.json, FLOW-CHANGELOG.jsonl and
SCRIPTS.md alone -- two checkouts each append `n = max + 1`, so parallel branches claim the
same change number for different changes, and the chart file (rewritten whole on every op)
conflicts with them.

Pinned as behaviour:
- merge_reviewed.merge_changelog: union of both sides' rows (identity ignores `n`), in
  timestamp order, duplicate numbers renumbered past the max -- never a conflict.
- merge_reviewed.replay_flow: the chart is the merged changelog's last `after`.
- merge_reviewed.resolve_generated: a real conflicted merge (fixture git repo where main
  and a branch each append row n=3) is resolved -- four rows, sequential numbers, FLOW.json
  replayed, everything staged.
- merge_reviewed.refresh_generated: a hand-committed FLOW.json brought in by a CLEAN merge
  is rebuilt from the changelog, so a branch cannot commit a drifted registry.

RED before the fix: merge_changelog/replay_flow/refresh_generated do not exist and a FLOW
conflict makes resolve_generated return False.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

FAILS = []
MODELS = "projects/local-inference/models"


def check(label, cond, detail=""):
    line = "  [%s] %s" % ("ok" if cond else "BAD", label)
    if not cond and detail:
        line += " -- " + detail
    print(line)
    if not cond:
        FAILS.append(label)


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True,
                          timeout=60)


def row(n, at, what, before, after):
    return {"n": n, "at": at, "who": "fixture", "what": what, "task": "",
            "ops": [what], "before": before, "after": after}


def write_flow(repo, rows):
    (repo / MODELS / "FLOW-CHANGELOG.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (repo / MODELS / "FLOW.json").write_text(
        json.dumps(rows[-1]["after"], indent=1) + "\n", encoding="utf-8")


def fixture(td):
    repo = Path(td) / "fx"
    (repo / MODELS).mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.invalid")
    git(repo, "config", "user.name", "fixture")
    base = [row(1, "2026-10-01T00:00:00Z", "start", None, {"nodes": ["a"]}),
            row(2, "2026-10-02T00:00:00Z", "add b", {"nodes": ["a"]}, {"nodes": ["a", "b"]})]
    write_flow(repo, base)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "fixture: base chart")
    return repo, base


def main():
    print("mf6 oracle: registries merge as data and regenerate from their sources\n")
    import merge_reviewed as mr

    for name in ("merge_changelog", "replay_flow", "refresh_generated"):
        check(f"merge_reviewed.{name} exists", hasattr(mr, name),
              "a FLOW merge still conflicts by hand")
    if not all(hasattr(mr, n) for n in ("merge_changelog", "replay_flow", "refresh_generated")):
        print("\n%d failed" % len(FAILS))
        return 1

    a3 = row(3, "2026-10-03T01:00:00Z", "add c", {"nodes": ["a", "b"]}, {"nodes": ["a", "b", "c"]})
    b3 = row(3, "2026-10-03T02:00:00Z", "add d", {"nodes": ["a", "b"]}, {"nodes": ["a", "b", "d"]})
    merged = mr.merge_changelog([a3], [b3])
    check("two claims of the same number merge to two rows", len(merged) == 2,
          "%r" % (merged,))
    check("  ...with sequential, non-duplicate numbers",
          sorted(r["n"] for r in merged) == [3, 4] and merged[0]["at"] < merged[1]["at"],
          "%r" % ([(r['n'], r['at']) for r in merged],))
    check("  ...the earliest claim keeps its number", merged[0]["what"] == "add c"
          and merged[0]["n"] == 3, "%r" % (merged[0],))
    check("replay_flow is the last row's after",
          mr.replay_flow(merged) == {"nodes": ["a", "b", "d"]},
          "%r" % (mr.replay_flow(merged),))
    check("merge_changelog is idempotent on one side alone",
          mr.merge_changelog([a3, b3]) == mr.merge_changelog([a3], [b3]))

    with tempfile.TemporaryDirectory() as td:
        # A REAL CONFLICTED MERGE: main and a branch each append their own row 3.
        repo, base = fixture(td)
        git(repo, "checkout", "-q", "-b", "side")
        write_flow(repo, base + [b3])
        git(repo, "commit", "-qam", "side: add d")
        git(repo, "checkout", "-q", "main")
        write_flow(repo, base + [a3])
        git(repo, "commit", "-qam", "main: add c")
        r = git(repo, "merge", "--no-ff", "--no-commit", "side")
        check("the fixture really conflicts (both flow files)", r.returncode != 0,
              r.stdout + r.stderr)
        ok = mr.resolve_generated(repo, "side")
        check("resolve_generated resolves a FLOW-only conflict", ok is True)
        rows = [json.loads(x) for x in (repo / MODELS / "FLOW-CHANGELOG.jsonl")
                .read_text(encoding="utf-8").splitlines() if x.strip()]
        check("  ...the merged changelog holds all four rows, renumbered 1-4",
              [r["n"] for r in rows] == [1, 2, 3, 4], "%r" % ([r["n"] for r in rows],))
        chart = json.loads((repo / MODELS / "FLOW.json").read_text(encoding="utf-8"))
        check("  ...and FLOW.json is replayed to the merged changelog's end",
              chart == {"nodes": ["a", "b", "d"]}, "%r" % (chart,))
        r = git(repo, "diff", "--cached", "--name-only")
        check("  ...and both files are staged", MODELS + "/FLOW.json" in r.stdout
              and MODELS + "/FLOW-CHANGELOG.jsonl" in r.stdout, r.stdout)

        # A CLEAN MERGE CARRYING A HAND EDIT: the branch rewrote FLOW.json with no
        # changelog row; refresh_generated rebuilds it from the changelog.
        repo2, base2 = fixture(td + "/second")
        (repo2 / MODELS / "FLOW.json").write_text(
            json.dumps({"nodes": ["hand-edited"]}, indent=1) + "\n", encoding="utf-8")
        git(repo2, "add", "-A")
        done = mr.refresh_generated(repo2, [MODELS + "/FLOW.json"])
        chart = json.loads((repo2 / MODELS / "FLOW.json").read_text(encoding="utf-8"))
        check("refresh_generated rebuilds a hand-committed FLOW.json from the changelog",
              chart == base2[-1]["after"], "%r" % (chart,))
        check("  ...and says what it refreshed", MODELS + "/FLOW.json" in done,
              "%r" % (done,))

    src = (ROOT / "scripts" / "merge_reviewed.py").read_text(encoding="utf-8")
    a = src.find("def build(")
    check("build() refreshes the generated registries after the merge (live path)",
          a >= 0 and "refresh_generated(" in src[a:a + 6000],
          "no refresh_generated call inside build()")

    n = len(FAILS)
    print("\n%d failed -- %s" % (n, "registries merge as data and regenerate"
                                 if not n else "mf6 is not in place"))
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
