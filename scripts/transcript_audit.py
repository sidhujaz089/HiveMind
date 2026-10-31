#!/usr/bin/env python3
"""For each standing process, the transcript turns that explain it -- and the ones nobody can explain (t414).

    python3 scripts/transcript_audit.py                 # audit the default catalog; write the report
    python3 scripts/transcript_audit.py --show NAME     # the evidence for one process

Jas, 2026-09-25: "Look at transcripts of all sessions and see what we did and why. If the why no longer
exists, do we need the what?" This is the MECHANICAL half: for every standing process -- a workspace
check, a check script, a git hook, a library rule -- it collects the indexed transcript turns
(workspace/transcript-index.jsonl, built by transcripts.py) that mention it by name. A process with NO
findable cause is the finding (t123): something runs every day and nobody on record said why.

IT DOES NOT JUDGE. Keep or retire is a model pass over this evidence, and a retire decision goes to Jas
(AGENTS.md rule 9); a verdict here is only 'evidence-found' or 'no-cause-found'. author_model rides
along so the reviewing pass can be a different model from the one that wrote the process.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INDEX = REPO / "workspace" / "transcript-index.jsonl"
REPORT = REPO / "workspace" / "transcript-audit.jsonl"
FIELDS = ("session", "n", "at", "role", "text")


def find_evidence(index_rows, name):
    """Every indexed turn whose text names `name` (case-insensitive), with where it was said."""
    pat = re.compile(re.escape(name), re.I)
    return [{k: r.get(k) for k in FIELDS} for r in index_rows if pat.search(str(r.get("text") or ""))]


def build_report(index_rows, catalog):
    """One row per catalog process, in catalog order."""
    out = []
    for p in catalog:
        ev = find_evidence(index_rows, p["name"])
        out.append({"process": p["name"], "where": p.get("where", ""), "author_model": p.get("author_model", ""),
                    "evidence_found": bool(ev), "evidence": ev,
                    "verdict": "evidence-found" if ev else "no-cause-found"})
    return out


def write_report(report, path=None):
    """JSONL, one row per process. POINTERS, NOT COPIES: each piece of evidence is written as where it
    was said (session, turn, time, role), never its text -- transcript text copied into the repository
    carried a credential-shaped string the commit gate refused (2026-09-30), and a copy drifts from its
    source (L-046). `--show NAME` reads the text from the index."""
    p = Path(path or REPORT)
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = [{**{k: v for k, v in r.items() if k != "evidence"},
             "evidence": [{k: e.get(k) for k in ("session", "n", "at", "role")} for e in r["evidence"]]}
            for r in report]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return p


def load_index(path=None):
    rows, bad = [], 0
    for line in Path(path or INDEX).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            bad += 1
    if bad:
        print(f"  NOTE: {bad} unreadable index line(s) not searched", file=sys.stderr)
    return rows


def _author(rel):
    """The Agent trailer of the commit that CREATED the file ('' when unknown)."""
    try:
        r = subprocess.run(["git", "log", "--diff-filter=A", "--format=%B", "--", rel], cwd=str(REPO),
                           capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return ""
    m = re.findall(r"^Agent:\s*(\S+)", r.stdout, re.M)
    return m[-1] if m else ""


def default_catalog():
    """The standing processes: check_workspace's checks, scripts/check_*.py, the git hooks, the library rules."""
    cat = []
    cw = (REPO / "scripts" / "check_workspace.py").read_text(encoding="utf-8")
    for m in re.finditer(r"^    def (check_\w+)\(", cw, re.M):
        cat.append({"name": m.group(1), "where": "scripts/check_workspace.py", "author_model": ""})
    for f in sorted((REPO / "scripts").glob("check_*.py")):
        if f.name != "check_workspace.py":
            rel = f"scripts/{f.name}"
            cat.append({"name": f.stem, "where": rel, "author_model": _author(rel)})
    for f in sorted((REPO / "scripts" / "hooks").glob("*")):
        if f.is_file() and not f.name.endswith((".md", ".pyc")):
            rel = f"scripts/hooks/{f.name}"
            cat.append({"name": f.name, "where": rel, "author_model": _author(rel)})
    sys.path.insert(0, str(REPO / "scripts"))
    import library_checks
    for c in library_checks.CHECKS:
        cat.append({"name": c[0], "where": "scripts/library_checks.py", "author_model": ""})
    return cat


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--show")
    a = ap.parse_args(argv)
    index = load_index()
    if a.show:
        for r in find_evidence(index, a.show):
            print(f"  {r['at']}  {r['session']} #{r['n']} {r['role']}: {r['text']}")
        return 0
    report = build_report(index, default_catalog())
    p = write_report(report)
    none = [r["process"] for r in report if not r["evidence_found"]]
    print(f"  {len(report)} standing process(es); {len(report) - len(none)} with evidence, {len(none)} with "
          f"NO findable cause in {len(index)} indexed turns. Report: {p.relative_to(REPO)}")
    for n in none:
        print(f"    no-cause-found  {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
