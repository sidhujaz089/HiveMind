#!/usr/bin/env python3
"""New kinds of failure go to the Manager (Jas, 2026-10-19: "Unknown things should go to the manager").

    python3 scripts/error_unknowns.py            # scan; file one Assigned item per NEW error shape
    python3 scripts/error_unknowns.py --dry-run  # say what it would file, file nothing

The Flow tab's "triage" step promised this and nothing ran it (deleted, change 76): error_scan.py's
findings only reached Claude at session start. Now an ERROR SHAPE -- a finding's kind plus where it
came from (the source file with dates and numbers stripped) -- that has never been seen before becomes
ONE task assigned to the role that owns Assigned items (the Manager), carrying an example finding.
Shapes already seen are never filed again (workspace/run-logs/error-shapes.json), so this cannot fan
out (backlog must shrink). The first run only records today's shapes: those are not new.
Runs hourly from cron -- a full scan takes ~2 minutes, too long for the scheduler's tick.
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SEEN = REPO / "workspace" / "run-logs" / "error-shapes.json"
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "projects" / "app-repo"))


def shape(f):
    """'kind @ where': a ledger by its name, a log or note by its folder; run-specific parts removed."""
    # THE FOLDER AND FILE TYPE, NOT THE NAME: dispatch logs are named after their chunk, so a name-based
    # shape made every new build a "new kind of failure" (first baseline: 520 shapes).
    p = Path(str(f.get("source") or "?"))
    folder = re.sub(r"\d+", "#", p.parent.name) if p.suffix in (".log", ".md") else ""
    stem = "" if folder else re.sub(r"[0-9a-f]{8,}|\d+", "#", p.stem)
    return f"{f.get('kind') or '?'} @ {folder or stem}{p.suffix}"


def new_shapes(findings, seen):
    """{shape: first example} for shapes not in `seen`. Pure."""
    out = {}
    for f in findings:
        k = shape(f)
        if k not in seen and k not in out:
            out[k] = f
    return out


def main(argv):
    import error_scan
    findings = error_scan.scan()
    first_run = not SEEN.is_file()
    seen = set() if first_run else set(json.loads(SEEN.read_text(encoding="utf-8")))
    new = new_shapes(findings, seen)
    if first_run:
        print(f"  error-unknowns: first run -- recorded {len(new)} existing shape(s) as the baseline, filed none")
    elif "--dry-run" in argv:
        for k in new:
            print(f"  would file: {k}")
    else:
        import taskstore
        import task_routing
        import role_store                # no owner: the chart's "rework" seat, never a fixed name
        owner = task_routing.owner(task_routing.load(), "assigned_items") or role_store.escalate("rework")
        for k, f in new.items():
            body = (f"A kind of failure the error scanner has never seen before (error_unknowns.py).\n\n"
                    f"SHAPE: {k}\nEXAMPLE: {json.dumps(f)[:1500]}\n\n"
                    f"Classify it: name what failed and why, and either file the fix or say why it is "
                    f"harmless (then it is added to error_scan.py's healthy list).")
            tid = taskstore.add(f"Unknown error shape: {k}"[:90], area="workspace", body=body,
                                source="error_unknowns", needs=owner)
            print(f"  filed {tid} for {owner}: {k}")
    if not ("--dry-run" in argv):
        SEEN.parent.mkdir(parents=True, exist_ok=True)
        SEEN.write_text(json.dumps(sorted(seen | set(new)), indent=0), encoding="utf-8")
    print(f"  error-unknowns: {len(findings)} finding(s), {len(new)} new shape(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
