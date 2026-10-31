#!/usr/bin/env python3
"""The SECTION CENSUS, as code rather than as a paragraph.

WHY THIS EXISTS
---------------
The Family Project's completeness guarantee was a prose list inside
`chat/projects/family/INSTRUCTIONS.txt`, fetched over the same transport as the ledger it
validates, and never validated itself. Claude Chat found the hole on 2026-08-19 while
working the census fixtures:

    "The census has a single point of failure and it's the census definition itself. That
    list is a prose paragraph in a file fetched over the same transport, and it is not
    census-checked. A prose list that loses its last four items still reads like a prose
    list."

That is exactly right, and it is the same shape as fixture C, one level up. A file that
declares its own structure can be truncated consistently -- counts matching, nothing
internally wrong -- and the only thing that catches it is an element list held OUTSIDE the
file. Held as prose, that list can be truncated too, and silently, because prose has no
arity.

Held as code it cannot. CANON below is a list literal: a truncated copy is a syntax error
or a wrong length, not a shorter paragraph. That is the whole argument for this file.

This does not replace Chat's census. It is deliberately the INSIDE check, so that the
project has two independent witnesses to the same property, one of which does not depend on
a document surviving a network hop.

Usage:
    python3 scripts/check_ledger.py [path-to-ledger]
    python3 scripts/check_ledger.py --self-test    # run against the census fixtures

Exit codes: 0 complete, 1 incomplete or malformed, 2 could not check.
"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LEDGER = REPO / "chat" / "projects" / "family" / "State_Ledger_Family_Project.txt"
FIXTURES = REPO / "workspace" / "test-fixtures" / "census"

# The canonical elements. Twelve numbered sections, a dated header, a canary foot.
# Held here, in code, for the reason argued at the top of this file. If the ledger's
# structure genuinely changes, this list changes in the same commit -- and the diff makes
# that visible, which a reworded paragraph does not.
CANON = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]

SECTION_RE = re.compile(r"^##\s*(\d+)\.\s*(.+?)\s*\((\d+)\)\s*$", re.M)
ENTRY_RE = "^-\\s+"
HEADER_RE = re.compile(r"^\*\*Last updated:\*\*", re.M)
FOOT_RE = re.compile(r"^\*\*Load-check value:\*\*", re.M)


def census(text):
    """Return (findings, summary). findings empty means complete.

    Every finding names the element it is about, because a refusal that does not say WHICH
    rule it broke puts a wrong reason into the record (D-21 amendment 4).
    """
    findings = []

    if not HEADER_RE.search(text):
        findings.append("header: no '**Last updated:**' line")
    if not FOOT_RE.search(text):
        findings.append("canary foot: no '**Load-check value:**' line")

    found = {}
    for m in SECTION_RE.finditer(text):
        found[int(m.group(1))] = {"name": m.group(2), "declared": int(m.group(3)),
                                  "start": m.end()}

    missing = [n for n in CANON if n not in found]
    if missing:
        findings.append(
            "sections absent entirely: " + ", ".join(str(n) for n in missing)
            + "  <-- this is the failure a self-referential census cannot see")

    extra = [n for n in sorted(found) if n not in CANON]
    if extra:
        findings.append("sections present that CANON does not list: "
                        + ", ".join(str(n) for n in extra))

    # Count the top-level entries between each section header and the next.
    order = sorted(found)
    for i, n in enumerate(order):
        start = found[n]["start"]
        end = found[order[i + 1]]["start"] if i + 1 < len(order) else len(text)
        # Trim the trailing header line of the next section out of the slice.
        body = text[start:end]
        actual = len(re.findall(ENTRY_RE, body, re.M))
        found[n]["actual"] = actual
        if actual != found[n]["declared"]:
            findings.append(
                f"section {n} ({found[n]['name']}): declares {found[n]['declared']} "
                f"entries, contains {actual}")

    summary = {"sections_found": len(found), "sections_expected": len(CANON),
               "header": bool(HEADER_RE.search(text)), "foot": bool(FOOT_RE.search(text))}
    return findings, summary


def load_log(calls_path):
    """Read the JSONL call log and return a list of ledger load status lines."""
    import json
    from pathlib import Path

    rows = []
    with open(calls_path, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f'line {i}: {e}') from None

            if not all(k in row for k in ('ts', 'tool', 'target', 'outcome')):
                raise ValueError(f'line {i}: missing required keys') from None

            if row['tool'] == 'read' and Path(row['target']).name == LEDGER.name:
                rows.append(row)

    rows.sort(key=lambda r: r['ts'])

    entries = []
    for r in rows:
        line = f"- {r['ts']} "
        if r['outcome'] == 'ok':
            line += f"LOADED (bytes {r.get('bytes', 0)})"
        elif r['outcome'] == 'refused':
            line += f"FAILED-LOAD ({r.get('detail', 'unknown')})"
        else:
            continue

        if 'via' in r:
            line += f"; via {r['via']}"
        if 'client' in r:
            line += f"; client {r['client']}"
        entries.append(line)

    return entries


ELEMENT_LIST_SOURCES = ("chat/PROTOCOL.txt", "chat/projects/family/INSTRUCTIONS.txt")


def declared_sections(repo_root):
    """The section names the PROTOCOL says the ledger has, and where it says it.

    Chat's C5, and it is the hole the census cannot see on its own: the census compares each
    section's DECLARED count against its ACTUAL entries. If truncation removed a whole section
    HEADER, every surviving section still matches its own count and the census reports
    COMPLETE. The check certifies completeness using only the file it is checking.

    Closing it needs a second, independent record of what sections should exist -- which is
    the element list in the protocol. Nothing validated that list against the ledger until
    now, so a section could be added and the list not updated, and both would look fine.
    """
    import re
    for rel in ELEMENT_LIST_SOURCES:
        f = repo_root / rel
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"Twelve sections:(.{0,400}?)\.\s", text, re.S)
        if not m:
            continue
        # Collapse whitespace FIRST. The list wraps across lines, so a name like
        # "6 Jas's settled\n reads" was truncated at the newline and then reported as a
        # section missing from the protocol -- a false drift warning from a check whose
        # entire job is spotting drift. Caught on its first run.
        flat = " ".join(m.group(1).split())
        names = re.findall(r"(\d+)\s+([A-Za-z][^,]*?)(?=\s*,|\s*\.|$)", flat)
        if names:
            return {int(n): v.strip() for n, v in names}, rel
    return None, None


def check_element_list(repo_root, ledger_text):
    """(ok, lines). Compare the protocol's declared sections against the ledger's actual ones."""
    import re
    declared, source = declared_sections(repo_root)
    if not declared:
        return True, ["  element list: not found in the protocol -- nothing was cross-checked"]
    actual = {int(m.group(1)): m.group(2).strip()
              for m in re.finditer(r"^## (\d+)\.\s+([^(\n]+)", ledger_text, re.M)}
    problems = []
    for n in sorted(set(declared) - set(actual)):
        problems.append(f"  section {n} ({declared[n]}) is in {source} but NOT in the ledger "
                        f"-- this is the case the census cannot see")
    for n in sorted(set(actual) - set(declared)):
        problems.append(f"  section {n} ({actual[n]}) is in the ledger but not in {source} "
                        f"-- the element list is stale")
    if problems:
        return False, problems
    return True, [f"  element list: {len(declared)} section(s) declared in {source}, "
                  f"all present in the ledger"]


def report(label, text):
    findings, summary = census(text)
    mark = "COMPLETE" if not findings else "INCOMPLETE"
    print(f"\n  {label}")
    print(f"    sections {summary['sections_found']}/{summary['sections_expected']}"
          f"  header={'yes' if summary['header'] else 'NO'}"
          f"  foot={'yes' if summary['foot'] else 'NO'}  -> {mark}")
    for f in findings:
        print(f"      - {f}")

    # Chat's C5. The census above certifies the ledger using only the ledger, so a whole
    # missing section header passes it. This is the independent record.
    ok, lines = check_element_list(Path(__file__).resolve().parent.parent, text)
    for line in lines:
        print(f"    {line.strip()}")
    return not findings and ok


def self_test():
    """The fixtures exist to be failed. A census that passes them is decorative.

    This is docs/CHECKS.md applied to this file: it is not trusted until it has been
    observed failing, so the failing case ships with it rather than being run once by hand.
    """
    if not FIXTURES.is_dir():
        print("  [SKIP] no fixtures at " + str(FIXTURES))
        print("         Nothing was checked -- this is not a pass.")
        return 2
    fixtures = sorted(FIXTURES.glob("fixture-*.txt"))
    if not fixtures:
        print("  [SKIP] no fixture-*.txt files in " + str(FIXTURES))
        print("         Nothing was checked -- this is not a pass.")
        return 2
    print("SELF-TEST -- every fixture below MUST be reported INCOMPLETE.")
    ok = True
    for f in fixtures:
        passed = report(f.name, f.read_text(encoding="utf-8", errors="replace"))
        if passed:
            print(f"      !! {f.name} PASSED the census. The census is broken, not the "
                  f"fixture.")
            ok = False
    print("\n  self-test: " + ("all fixtures correctly rejected" if ok
                               else "A FIXTURE SLIPPED THROUGH"))
    return 0 if ok else 1


def main(argv):
    if "--self-test" in argv:
        return self_test()

    if "--load-log" in argv:
        path = REPO / "workspace" / "mcp-calls.jsonl"
        if len(argv) > 2 and not argv[2].startswith("--"):
            path = Path(argv[2])
        for entry in load_log(path):
            print(entry)
        return 0

    target = Path(argv[1]) if len(argv) > 1 else LEDGER
    if not target.is_file():
        print(f"  [SKIP] {target} is not present on this machine.")
        print("         Nothing was checked -- this is not a pass. chat/ is gitignored by")
        print("         D-22, so a clone without the corpus cannot run this.")
        return 2
    print(f"SECTION CENSUS -- {target}")
    return 0 if report(target.name, target.read_text(encoding="utf-8",
                                                     errors="replace")) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
