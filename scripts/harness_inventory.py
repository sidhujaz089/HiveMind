#!/usr/bin/env python3
"""Which scripts touch a model, and can their records be trusted?

    python3 scripts/harness_inventory.py            the table
    python3 scripts/harness_inventory.py --json     machine-readable

WHY THIS IS A SCRIPT AND NOT A LIST IN A DOCUMENT
--------------------------------------------------
Four outside reviewers said the same thing about Phase 0 on 2026-09-07: fixing the two known
bugs in `scope_queue.py` is "an audit by proximity", and proximity is the mechanism that let
the earlier harness bugs survive for weeks. One put it as: *Phase 0 catches the known iceberg;
it does not map it.*

A hand-written inventory maps it once and is wrong by the next commit. This recomputes.

WHAT IT LOOKS FOR, AND WHY EACH ONE
------------------------------------
Every signature below is a defect this repository actually shipped, not a style opinion:

  greedy-extract   `re.search(r"\\{.*\\}", ...)` -- first `{` to last `}`. Fails on
                   prose-then-JSON, JSON-then-prose, and two objects; only a bare object
                   survives. Fixed in scope_queue.py on 2026-09-07; SEVEN other files had it.
  truncates-record a fixed-width slice stored INTO a record. `parse_error = answer[:400]`
                   made five of eight failing rows exactly 400 bytes, so nobody could tell
                   whether the model was cut off, wrapped its answer, or emitted junk.
  no-stop-reason   the script calls a model but never records `done_reason`. Without it,
                   "the output ended" cannot be distinguished from "the output was cut off",
                   which is the difference between a model fault and a harness fault.

A slice into a `print()` or a log LINE is display, not a record, and is not flagged. The
distinction is the whole point: shortening what a human reads is fine, shortening what the
system later reasons from is the bug.
"""
import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKIP = {".venv", "node_modules", ".git", "tests", "__pycache__"}

CALLS_MODEL = re.compile(r"11434|/api/(chat|generate|show|ps|tags)\b|\bollama\b", re.I)
GREEDY = re.compile(r"re\.(search|match|findall)\(\s*r?[\"']\\?\{\.\*")
# a slice stored into a record, not printed: assigned, or passed as a kwarg/dict value
RECORD_SLICE = re.compile(
    r"""(?:["']\w+["']\s*:|^\s*\w+(?:\[[^\]]+\])?\s*=|\b\w+\s*=)\s*"""
    r"""\w+\s*\[\s*:\s*\d{2,}\s*\]""", re.M)
DISPLAY = re.compile(r"\b(print|say|log|echo)\s*\(")
STOP_REASON = re.compile(r"done_reason|finish_reason|stop_reason")
# MENTIONING IS NOT USING (t300). Adding telemetry put "done_reason" into coder.py purely to
# RECORD it, and this inventory promoted the file to trusted while nothing acted on a cut-off.
# Trusted needs the stop reason in a decision: a comparison, or a condition that reads it.
STOP_ACTED = re.compile(
    r"\b(?:if|elif|while|and|or|not)\b[^\n]*(?:done_reason|finish_reason|stop_reason)"
    r"|(?:done_reason|finish_reason|stop_reason)[\"'\])]*\s*(?:==|!=|\bin\b|\bnot in\b)"
    # ...or the value compared after being put in a local: `if done == "length":` (chatui).
    r"|(?:==|!=)\s*[\"'](?:length|stop)[\"']")


def sources():
    for p in sorted(REPO.rglob("*.py")):
        if any(part in SKIP for part in p.parts):
            continue
        yield p


def code_only(src):
    """Blank docstrings and comments IN PLACE, leaving every other byte where it was.

    Two wrong versions preceded this one, and both looked fine:

      1. Blanking every STRING token blinded the detector completely -- the greedy-extract
         signature IS a string literal, so the scan fell from 41 scripts to 15 and moved
         three files with the known bug into "trusted".
      2. Reassembling from tokens with separators turned `re.search(` into `re . search (`,
         so the pattern stopped matching even where the code was intact.

    Hence: do not rebuild the source. Overwrite the character ranges of comments and
    docstrings with spaces and change nothing else. A docstring is a string standing alone
    as a statement; a string passed as an argument is code and must survive.
    """
    import io
    import tokenize
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except Exception:                                        # noqa: BLE001
        return src                  # unparseable: match the raw text rather than skip it
    lines = src.splitlines(keepends=True)
    prev = tokenize.NEWLINE
    for tok in toks:
        drop = tok.type == tokenize.COMMENT or (
            tok.type == tokenize.STRING
            and prev in (tokenize.NEWLINE, tokenize.NL, tokenize.INDENT,
                         tokenize.DEDENT, tokenize.ENCODING))
        if tok.type not in (tokenize.NL, tokenize.COMMENT):
            prev = tok.type
        if not drop:
            continue
        (r1, c1), (r2, c2) = tok.start, tok.end
        for r in range(r1 - 1, r2):
            if r >= len(lines):
                break
            line = lines[r]
            lo = c1 if r == r1 - 1 else 0
            hi = c2 if r == r2 - 1 else len(line.rstrip("\n"))
            keep_nl = "\n" if line.endswith("\n") else ""
            body = line.rstrip("\n")
            lines[r] = body[:lo] + " " * max(0, hi - lo) + body[hi:] + keep_nl
    return "".join(lines)


def audit(p):
    try:
        src = code_only(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:                                        # noqa: BLE001
        return None
    if not CALLS_MODEL.search(src):
        return None
    faults = []
    if GREEDY.search(src):
        faults.append("greedy-extract")
    for line in src.splitlines():
        if RECORD_SLICE.search(line) and not DISPLAY.search(line):
            faults.append("truncates-record")
            break
    if not STOP_REASON.search(src):
        faults.append("no-stop-reason")
    elif not STOP_ACTED.search(src):
        faults.append("stop-reason-recorded-only")
    status = "trusted" if not faults else (
        "quarantined" if {"greedy-extract", "truncates-record"} & set(faults) else "monitored")
    return {"path": str(p.relative_to(REPO)), "status": status, "faults": faults}


def _own_faults(p):
    """Run signature fault checks on a file without requiring CALLS_MODEL to match."""
    try:
        src = code_only(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:                                        # noqa: BLE001
        return []
    faults = []
    if GREEDY.search(src):
        faults.append("greedy-extract")
    for line in src.splitlines():
        if RECORD_SLICE.search(line) and not DISPLAY.search(line):
            faults.append("truncates-record")
            break
    if not STOP_REASON.search(src):
        faults.append("no-stop-reason")
    elif not STOP_ACTED.search(src):
        faults.append("stop-reason-recorded-only")
    return faults


def _worst_status(statuses):
    order = {"quarantined": 0, "monitored": 1, "trusted": 2}
    return min(statuses, key=lambda s: order[s])


def inventory():
    """Every script that touches a model, directly or through imports.

    Direct rows are exactly what audit() finds. Indirect rows are scripts that
    transitively import a direct model-touching script but do not themselves
    match the CALLS_MODEL signature. Status is inherited: worst of own faults
    and the statuses of all model-touching files reached.
    """
    from importgraph import reaches as _reaches

    direct_rows = {r["path"]: r for r in (audit(p) for p in sources()) if r}
    model_paths = set(direct_rows.keys())

    all_rows = []
    for path, row in direct_rows.items():
        all_rows.append({
            "path": path,
            "status": row["status"],
            "faults": row["faults"],
            "how": "direct",
            "reaches": [],
            "own_faults": row["faults"],
        })

    for p in sources():
        rel = str(p.relative_to(REPO))
        if rel in model_paths:
            continue
        reached = _reaches(rel)
        model_reached = reached & model_paths
        if not model_reached:
            continue
        own_faults = _own_faults(p)
        own_status = "trusted" if not own_faults else (
            "quarantined" if {"greedy-extract", "truncates-record"} & set(own_faults)
            else "monitored")
        worst = _worst_status([own_status] + [direct_rows[m]["status"] for m in model_reached])
        all_rows.append({
            "path": rel,
            "status": worst,
            "faults": own_faults,
            "how": "indirect",
            "reaches": sorted(model_reached),
            "own_faults": own_faults,
        })

    return all_rows

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    rows = inventory()
    rows.sort(key=lambda r: ({"quarantined": 0, "monitored": 1, "trusted": 2}[r["status"]],
                             r["path"]))
    if a.json:
        print(json.dumps(rows, indent=1))
        return 0

    n = {s: sum(1 for r in rows if r["status"] == s)
         for s in ("quarantined", "monitored", "trusted")}
    nd = sum(1 for r in rows if r["how"] == "direct")
    ni = sum(1 for r in rows if r["how"] == "indirect")
    print(f"\n{len(rows)} script(s) touch a model — "
          f"{nd} direct, {ni} indirect — "
          f"{n['quarantined']} quarantined, {n['monitored']} monitored, {n['trusted']} trusted\n")
    for r in rows:
        mark = {"quarantined": "XX", "monitored": " !", "trusted": " ."}[r["status"]]
        print(f"  {mark}  {r['path']:<52} {', '.join(r['faults']) or '-'}")
    print("\n  XX quarantined — its output must not feed a trigger, a policy decision, or a")
    print("     promotion until the fault is fixed. It may still be read by a human.")
    print("   ! monitored   — usable, but it cannot tell a cut-off answer from a finished one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
