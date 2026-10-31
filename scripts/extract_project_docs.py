#!/usr/bin/env python3
"""Rescue project-knowledge documents out of an export's projects/*.json into real files.

    python3 scripts/extract_project_docs.py
    python3 scripts/extract_project_docs.py --apply

WHY THIS IS URGENT RATHER THAN TIDY
-----------------------------------
`chat/PROTOCOL.txt` carried this for five days, about the MMSP and Entrepreneur ledgers:

    NOT YET MIGRATED ... Its last known home was claude.ai project knowledge, which Jas is
    deleting. GET IT OUT BEFORE THAT PROJECT IS DELETED.

Jas deleted the projects on 2026-08-25. Neither ledger had been migrated. They survived only
because the 2026-08-24 export happened first and captured project knowledge as well as
conversations -- `State_Ledger_Entrepreneur_Project.txt` (38,045 chars) inside the JSHC
project, `MMSP-State-Ledger.txt` (14,504 chars) inside Work. A document sitting inside a
702 KB JSON blob is backed up but not *findable*: nothing searches it, the bridge cannot
serve it, and no census counts it.

WHAT IT SKIPS, AND WHY THAT IS THE INTERESTING PART
---------------------------------------------------
Most `docs` entries in a project are not knowledge at all -- they are exported copies of
conversations that are already in the corpus, named `<date>__<title>__<uuid>.md`. Writing
those out would duplicate 60-odd transcripts under new names, which is precisely the
"two records of one conversation, where the worse one is indistinguishable by filename"
that t079 was about.

So a doc whose filename ends in a UUID already present in `chat/` is skipped as a duplicate.
That is a real key, not a similarity guess. Docs with no UUID in the name -- the ledgers,
the briefs, the primers -- are the genuine project knowledge, and those get written.

Duplicates ACROSS projects are real too: the same document is attached to a project more
than once, byte-for-byte. Written once, and the repeat reported rather than silently
dropped.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CORPUS = REPO / "chat"
DEST = CORPUS / "projects"

UUID_IN_NAME = re.compile(r"__([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                          r"[0-9a-f]{4}-[0-9a-f]{12})\.md$")



def _first_line(p):
    """The first line of a file, read as a line -- not a fixed-width slice of the whole."""
    with p.open(encoding="utf-8", errors="replace") as fh:
        return fh.readline()

def corpus_uuids(root=CORPUS):
    found = set()
    if not root.is_dir():
        return found
    for p in root.rglob("*.txt"):
        head = _first_line(p)            # the UUID line is the first line
        m = re.match(r"UUID:\s*([0-9a-f-]{36})", head)
        if m:
            found.add(m.group(1))
    return found


def safe_name(name):
    """Keep the document's own filename; strip anything that escapes the directory."""
    return re.sub(r"[^A-Za-z0-9._ -]", "_", Path(name).name).strip() or "untitled.txt"


def newest_export(root=CORPUS / "export" / "raw"):
    batches = sorted(d for d in root.iterdir() if d.is_dir()) if root.is_dir() else []
    return batches[-1] if batches else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--batch", default=None, help="export batch dir (default: newest)")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)

    batch = Path(a.batch) if a.batch else newest_export()
    if not batch or not (batch / "projects").is_dir():
        print("No export with a projects/ directory found.")
        return 1

    have = corpus_uuids()
    print(f"Corpus holds {len(have)} conversation UUID(s)\n")

    written = skipped = repeated = 0
    seen = {}
    for f in sorted((batch / "projects").glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        for pr in (data if isinstance(data, list) else [data]):
            name = pr.get("name") or "unnamed"
            docs = pr.get("docs") or []
            print(f"{name} — {len(docs)} doc(s)")
            for doc in docs:
                fn = doc.get("filename") or ""
                content = doc.get("content") or ""
                m = UUID_IN_NAME.search(fn)
                if m and m.group(1) in have:
                    skipped += 1
                    continue
                digest = hashlib.sha256(content.encode()).hexdigest()
                target = DEST / name.lower().replace(" ", "-") / safe_name(fn)
                if digest in seen:
                    repeated += 1
                    print(f"    (repeat of {seen[digest].name}, not written again)")
                    continue
                seen[digest] = target
                print(f"  {'write' if a.apply else 'would write'} "
                      f"{target.relative_to(CORPUS.parent)}  ({len(content):,} chars)")
                if a.apply:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                    back = target.read_text(encoding="utf-8")
                    if hashlib.sha256(back.encode()).hexdigest() != digest:
                        print(f"    FAILED verify: {target}")
                        return 1
                written += 1
            print()

    print("-" * 70)
    print(f"{written} document(s) {'written' if a.apply else 'to write'}, "
          f"{skipped} skipped as conversations already in the corpus, "
          f"{repeated} cross-project repeat(s)")
    if not a.apply:
        print("DRY RUN -- nothing touched. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
