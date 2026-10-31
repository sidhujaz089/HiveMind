#!/usr/bin/env python3
"""Every verdict computed from a problem-collection must be accounted for.

THE DEFECT CLASS. A verdict computed as "no problems found" passes VACUOUSLY when nothing was
examined. Eight real instances were found here on 2026-08-31/09-01: mutate.py printed [caught]
for a mutation whose suite could not run; backup_corpora.py said "Backup verified" for a
manifest listing zero files; check_deployed.py passed when there was nothing to parse;
check_workspace.py cleared 16 of 38 test suites on an unrelated substring; check_apps.py
treated "whatsNew": "" as present; digests.py reported a CORRUPT sidecar as a first run;
check_workspace.py:687 reported PASS having read zero path constants.

WHY THIS IS A CHECK AND NOT A PARAGRAPH. `docs/LESSONS.md` has carried this lesson for months
as "ZERO PROJECTS IS NOT ALL PROJECTS PASSING", and it had been applied in exactly ONE place.
A lesson that lives in a document is a lesson that expires. This one now fails a session.

WHY A CLEARANCE MUST BE MECHANICAL. Three reviewers audited the same 30 sites and each missed
something different. The local pair cleared 28 with written reasons and one was wrong. A blind
review by the larger model overturned 17 of those, catching that one -- and also the
`sync_facts` family, which Claude Code had cleared by checking the exception path and never
the zero-facts path. Only ONE fact is defined across 351 markdown files, so deleting a single
block makes three checks examine nothing and report PASS. No judge was reliable. So a
clearance is not prose here: it is a demonstration that fails when it stops being true.
"""
import argparse
import ast
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REGISTRY = REPO / "workspace" / "vacuous-registry.json"
SCRIPTS = REPO / "scripts"
KEYS = ("problem", "bad", "fail", "missing", "stale", "broken", "silent", "orphan",
        "dangling", "unparsed", "corrupt", "mismatch", "drift", "gap", "offend", "viol")


def sites():
    """Every `if <collection>:` / `if not <collection>:` that gates a verdict."""
    found, seen = {}, {}
    for f in sorted(SCRIPTS.glob("*.py")):
        if f.name == Path(__file__).name:
            continue
        src = f.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            print(f"  NOTE: {f} does not parse -- not checked for vacuous verdicts", file=sys.stderr)
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            t = node.test
            inner = t.operand if isinstance(t, ast.UnaryOp) and isinstance(t.op, ast.Not) else t
            if isinstance(inner, ast.Name) and any(k in inner.id.lower() for k in KEYS):
                # KEYED BY NAME AND ORDINAL, NOT BY LINE NUMBER.
                # The first version keyed on file:line, and the first real edit broke it:
                # inserting a guard above check_workspace.py:687 moved it to :700 and the
                # check reported SEVEN brand-new unregistered sites that were the same
                # seven sites, one edit later. A registry that invalidates itself whenever
                # anyone edits the file above an entry is a registry nobody will keep.
                # file + collection name + which occurrence it is survives insertion.
                seen[f.name] = seen.get(f.name, {})
                i = seen[f.name].get(inner.id, 0)
                seen[f.name][inner.id] = i + 1
                key = f"scripts/{f.name}::{inner.id}#{i}"
                found[key] = {"line": node.lineno, "collection": inner.id}
    return found


def demonstration_holds(key, entry):
    """A cleared site must still be able to PROVE it. Returns (ok, why_not)."""
    d = entry.get("demonstration")
    if not d:
        return False, "cleared with no demonstration -- prose is not a clearance here"
    if "guard_at" in d:
        # SEARCH THE FILE FOR THE GUARD; the line number is a HINT.
        # Third time line numbers have broken this. Keying broke when an edit shifted a
        # site; adoption broke because the pair cited THEIR line numbers and Live's file
        # had moved. A demonstration must survive ordinary editing or nobody maintains it.
        # What matters is that the guard is still THERE, not that it is on a given row.
        try:
            f, ln = d["guard_at"].rsplit(":", 1)
            lines = (REPO / f).read_text(encoding="utf-8", errors="replace").splitlines()
        except (OSError, ValueError) as e:
            return False, f"guard_at {d['guard_at']} could not be read: {e}"
        want = d.get("must_contain", "")
        if not want:
            return False, "guard_at with no must_contain proves nothing"
        hits = [i + 1 for i, l in enumerate(lines) if want in l]
        if not hits:
            return False, (f"the guard {want!r} is GONE from {f} -- "
                           f"it was cited at line {ln}")
        # PIN THE COUNT WHEN THE ANCHOR IS NOT UNIQUE.
        # Q16's probe 1.3 (substring collision) found three demonstrations here whose
        # must_contain matched more than once -- so deleting the cited guard left another
        # match and the check stayed green. Two of them cannot be fixed with a longer string:
        # check_workspace.py contains the SAME construct at lines 365 and 378, and again at
        # 294 and 1134. A substring cannot tell those apart. Pinning how many times it should
        # appear can: remove one of two and the count drops.
        want_n = d.get("expect_matches")
        if want_n is not None and len(hits) != want_n:
            return False, (f"{want!r} appears {len(hits)} time(s) in {f}, expected {want_n}. "
                           f"One of the guards this clearance rests on has been added or "
                           f"removed at lines {hits}.")
        return True, ""
    if "literal_assert" in d:
        try:
            mod = {"__file__": str(REPO / d["module"]), "__name__": "_vacuous_probe"}
            exec(compile((REPO / d["module"]).read_text(encoding="utf-8"),
                         d["module"], "exec"), mod)
            if not eval(d["literal_assert"], mod):  # noqa: S307 - our own registry
                return False, f"literal_assert failed: {d['literal_assert']}"
        except Exception as e:                       # noqa: BLE001
            return False, f"literal_assert could not run: {e}"
        return True, ""
    return False, "demonstration has neither guard_at nor literal_assert"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    if not REGISTRY.is_file():
        print(f"  [FAIL] {REGISTRY.name} is missing -- nothing accounts for these sites")
        return 1
    reg = json.loads(REGISTRY.read_text())["sites"]
    now = sites()

    # ZERO SITES IS NOT ALL SITES PASSING. If the sweep finds nothing, this file is broken,
    # not the repository clean -- the very shape it exists to catch.
    if not now:
        print("  [FAIL] the sweep found NO sites at all. That is this check being broken, "
              "not the repository being clean.")
        return 1

    unknown = sorted(set(now) - set(reg))
    unresolved = sorted(k for k, v in reg.items() if v.get("status") == "unresolved" and k in now)
    broken = []
    for k, v in reg.items():
        # A DEFECT-FIXED SITE WITH A DEMONSTRATION IS HELD TO IT TOO (t644, 2026-10-02): its guard
        # was added BECAUSE the site passed vacuously, so deleting it must turn this red. Until now
        # only 'cleared' sites were checked, and a fixed site's guard could go unnoticed.
        if k in now and (v.get("status") == "cleared"
                         or (v.get("status") == "defect-fixed" and v.get("demonstration"))):
            ok, why = demonstration_holds(k, v)
            if not ok:
                broken.append((k, why))

    rc = 0
    if unknown:
        rc = 1
        print(f"  [FAIL] {len(unknown)} verdict site(s) are in the code and NOT in the registry.")
        print("         A verdict computed from a problem-collection passes vacuously when")
        print("         nothing was examined. Decide, then record it in")
        print(f"         {REGISTRY.relative_to(REPO)} with a demonstration:")
        for k in unknown[:12]:
            print(f"           {k}  (line {now[k]['line']})")
    if unresolved:
        rc = 1
        print(f"  [FAIL] {len(unresolved)} site(s) are marked UNRESOLVED and still unfixed.")
        print("         These were cleared by one reviewer and overturned by another. An")
        print("         unresolved site is not a passing site.")
        for k in unresolved[:12]:
            print(f"           {k}")
    if broken:
        rc = 1
        print(f"  [FAIL] {len(broken)} cleared or fixed site(s) can no longer prove it:")
        for k, why in broken[:12]:
            print(f"           {k}: {why}")
    if rc == 0 and not a.quiet:
        n = sum(1 for v in reg.values() if v.get("status") == "cleared")
        nf = sum(1 for v in reg.values() if v.get("status") == "defect-fixed" and v.get("demonstration"))
        print(f"  [PASS] {len(now)} verdict site(s), all accounted for "
              f"({n} cleared, {nf} fixed, each with a live demonstration)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
