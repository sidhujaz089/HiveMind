#!/usr/bin/env python3
"""Import an Anthropic data export into the corpus, without duplicating what is already there.

    python3 scripts/import_export.py --zip workspace/attachments/t079-01.zip
    python3 scripts/import_export.py --zip workspace/attachments/t079-01.zip --apply

Dry run by default. `chat/` is gitignored under D-22, so there is no rollback here and
nothing is written until you have read the plan.

DEDUPE IS BY UUID, AND ONLY BY UUID
-----------------------------------
Every conversation in an export carries a UUID, and every transcript this script writes
carries it back in a `UUID:` header line. So the question "do we already have this
conversation" is a set membership test against the headers on disk, not a guess from the
filename. That matters: on 2026-08-24 the routing.csv row count (125) suggested 135 new
conversations, and the actual answer -- from the headers -- was 6. routing.csv only ever
indexed one directory. **Count from the corpus, not from an index of part of it.**

A Chat-emitted transcript has no UUID (a live session cannot retrieve its own; PROTOCOL.txt
retires the idea). Absence of a UUID is therefore NOT evidence of a duplicate, and this
script never treats it as such -- see SUPERSEDED below.

WHERE A CONVERSATION LANDS
--------------------------
Derived from the export content, and verified against the 254 conversations already placed
by the 2026-08-13 import -- this rule reproduces every one of those placements:

    any message with text        -> chat/staging/transcripts/
    no text, some attachment     -> chat/empty/files-only/
    no text, no attachment       -> chat/empty/no-content/

SUPERSEDED, NOT DELETED
-----------------------
t079 says the raw export wins and Chat's own transcript of the same session goes. It does --
but into `chat/superseded/`, not into nothing. The match between an export entry and a
Chat-emitted transcript is date plus title similarity, which is a guess rather than a key,
and `chat/` has no rollback. A wrong guess would destroy the only copy of a session the
export did not contain. Confirm the folder is right, then delete it by hand.

NO REDACTION
------------
Jas's instruction, 2026-08-24: import unredacted. The scrubber's purpose died with the
auditor. This script has no redaction path and should not grow one.
"""

import argparse
import datetime as dt
import difflib
import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CORPUS = REPO / "chat"
TRANSCRIPTS = CORPUS / "staging" / "transcripts"
NO_CONTENT = CORPUS / "empty" / "no-content"
FILES_ONLY = CORPUS / "empty" / "files-only"
RAW = CORPUS / "export" / "raw"
PERSONALIZATION = CORPUS / "personalization"
SUPERSEDED = CORPUS / "superseded"

# Calgary. The export stamps UTC; the existing transcripts are local wall time with the
# zone named, so a reader can tell 13:37 MST from 13:37 MDT without doing arithmetic.
MST = dt.timezone(dt.timedelta(hours=-7))
MDT = dt.timezone(dt.timedelta(hours=-6))



def _first_line(p):
    """The first line of a file, read as a line -- not a fixed-width slice of the whole."""
    with p.open(encoding="utf-8", errors="replace") as fh:
        return fh.readline()

def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def is_dst(u):
    """US/Canada rule: second Sunday in March to first Sunday in November."""
    y = u.year
    mar = dt.datetime(y, 3, 8, tzinfo=dt.timezone.utc)
    start = mar + dt.timedelta(days=(6 - mar.weekday()) % 7)
    nov = dt.datetime(y, 11, 1, tzinfo=dt.timezone.utc)
    end = nov + dt.timedelta(days=(6 - nov.weekday()) % 7)
    return start <= u < end


def local(ts):
    """UTC ISO string -> (datetime in Calgary local, zone label)."""
    if not ts:
        return None, ""
    u = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if is_dst(u):
        return u.astimezone(MDT), "MDT"
    return u.astimezone(MST), "MST"


def stamp(ts):
    d, z = local(ts)
    return f"{d:%Y-%m-%d %H:%M} {z}" if d else "(no timestamp)"


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return (s[:60].rstrip("-") or "untitled")


def existing_uuids(root=CORPUS):
    """Every UUID already in the corpus, mapped to the file that carries it."""
    found = {}
    if not root.is_dir():
        return found
    for p in sorted(root.rglob("*.txt")):
        head = _first_line(p)            # the UUID line is the first line
        m = re.match(r"UUID:\s*(\S+)", head)
        if m and m.group(1) != "(not":
            found[m.group(1)] = p
    return found


def classify(conv):
    msgs = conv.get("chat_messages") or []
    has_text = any((m.get("text") or "").strip() for m in msgs)
    has_file = any(m.get("attachments") or m.get("files") for m in msgs)
    if has_text:
        return TRANSCRIPTS
    return FILES_ONLY if has_file else NO_CONTENT


def render(conv, exported):
    """Reproduce the transcript format the 2026-08-13 import established."""
    msgs = conv.get("chat_messages") or []
    title = conv.get("name") or "(untitled)"
    out = [
        f"UUID: {conv['uuid']}",
        "SOURCE: export",
        f"EXPORTED: {exported}",
        f"TITLE: {title}",
        f"STARTED: {stamp(conv.get('created_at'))}",
        f"MESSAGES: {len(msgs)}",
        "",
    ]
    for m in msgs:
        who = (m.get("sender") or "?").upper()
        out += ["=" * 70, f"{who}  {stamp(m.get('created_at'))}", "=" * 70]
        text = (m.get("text") or "").rstrip()
        if text:
            out.append(text)
        for f in m.get("files") or []:
            out.append(f"[file: {f.get('file_name', '?')}]")
        for a in m.get("attachments") or []:
            out.append(f"[attachment: {a.get('file_name', '?')}]")
            extracted = a.get("extracted_content")
            if extracted:
                out.append(extracted)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def unique_path(directory, base):
    """`slug.txt`, then `slug-2.txt` -- matching the existing `untitled-3.txt` names."""
    p = directory / f"{base}.txt"
    n = 2
    while p.exists():
        p = directory / f"{base}-{n}.txt"
        n += 1
    return p


def is_export_owned(path):
    """True for a transcript whose first line carries an export UUID.

    One predicate, so the dedupe reader and the supersede reader cannot drift: any non-empty
    token after 'UUID:' counts as owned, except Chat's sentinel 'UUID: (not available)', whose
    first token is '(not'.
    """
    m = re.match(r"UUID:\s*(\S+)", _first_line(path))
    return bool(m and m.group(1) != "(not")


def chat_emitted(root=CORPUS):
    """Transcripts with no UUID header, under any transcripts/ directory.

    Deliberately narrow: PROTOCOL.txt, the ledgers, INSTRUCTIONS.txt and the artifacts also
    have no UUID and are not transcripts of anything. Only a file living in a transcripts/
    directory is a candidate for supersession.
    """
    out = []
    for p in sorted(root.rglob("*.txt")):
        if p.parent.name != "transcripts":
            continue
        if not is_export_owned(p):
            out.append(p)
    return out


def supersede_matches(candidates, convs, threshold=0.75):
    """Pair a Chat-emitted transcript with an export conversation, conservatively.

    Same date AND a title similarity over the threshold. Both, because either alone is
    wrong: several sessions share a date, and titles repeat across months.
    """
    pairs = []
    for p in candidates:
        m = re.match(r"(\d{4}-\d{2}-\d{2})__(.+)\.txt$", p.name)
        if not m:
            continue
        date, name = m.group(1), m.group(2).replace("-", " ")
        best = None
        for c in convs:
            if (c.get("created_at") or "")[:10] != date:
                continue
            r = difflib.SequenceMatcher(
                None, name, (c.get("name") or "").lower()).ratio()
            if r >= threshold and (best is None or r > best[1]):
                best = (c, r)
        if best:
            pairs.append((p, best[0], best[1]))
    return pairs


def load_export(src):
    """Accept a .zip or an already-extracted directory. Returns the batch directory."""
    src = Path(src)
    if src.is_dir():
        batch = src
    else:
        with zipfile.ZipFile(src) as z:
            bad = z.testzip()
            if bad is not None:
                raise SystemExit(f"corrupt entry in {src}: {bad}")
            dest = src.parent / f".{src.stem}-extracted"
            if dest.exists():
                shutil.rmtree(dest)
            z.extractall(dest)
            batch = dest
    if not (batch / "conversations.json").exists():
        inner = [d for d in batch.iterdir() if d.is_dir()]
        if len(inner) == 1:
            batch = inner[0]
    if not (batch / "conversations.json").exists():
        raise SystemExit(f"no conversations.json under {batch}")
    return batch


def write(path, text, apply, report):
    """Write, then re-read and compare. Never trust the write itself."""
    if not apply:
        report.append(("would write", path, len(text.encode())))
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    want = hashlib.sha256(text.encode()).hexdigest()
    got = sha(path)
    if want != got:
        report.append(("FAILED verify", path, 0))
        return False
    report.append(("wrote", path, path.stat().st_size))
    return True


def copy(src, dst, apply, report):
    if not apply:
        report.append(("would copy", dst, src.stat().st_size))
        return True
    dst.parent.mkdir(parents=True, exist_ok=True)
    before = sha(src)
    shutil.copy2(src, dst)
    if sha(dst) != before:
        report.append(("FAILED verify", dst, 0))
        return False
    report.append(("copied", dst, dst.stat().st_size))
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--zip", dest="src", default="workspace/attachments/t079-01.zip",
                    help="the export .zip, or an already-extracted directory")
    ap.add_argument("--apply", action="store_true",
                    help="actually write; without it this only reports")
    ap.add_argument("--corpus", default=None, help="override chat/ (tests use this)")
    a = ap.parse_args(argv)

    if a.corpus:
        globals().update(_retarget(Path(a.corpus)))

    src = Path(a.src)
    if not src.is_absolute():
        src = REPO / src
    batch = load_export(src)
    convs = json.loads((batch / "conversations.json").read_text(encoding="utf-8"))
    exported = max((c.get("updated_at") or "")[:10] for c in convs)

    have = existing_uuids(CORPUS)
    fresh = [c for c in convs if c["uuid"] not in have]
    # Snapshot the supersede candidates BEFORE importing anything. Otherwise the scan runs
    # over a corpus this run has already added to and can nominate a file it just wrote --
    # which is how a fresh import would end up in superseded/. Caught by the fixture on
    # 2026-08-25. Real UUIDs happen not to trigger it; that is luck, not a design.
    candidates = chat_emitted(CORPUS)
    absent = [u for u in have if u not in {c["uuid"] for c in convs}]

    print(f"Export  {batch.name}")
    print(f"        {len(convs)} conversation(s), "
          f"{sum(len(c.get('chat_messages') or []) for c in convs):,} message(s)")
    print(f"Corpus  {len(have)} conversation(s) already carry a UUID header")
    print(f"        {len(convs) - len(fresh)} of the export's UUIDs are already here")
    print(f"        {len(absent)} on disk are absent from the export")
    print(f"\n{len(fresh)} conversation(s) to import:\n")

    report = []
    written_this_run = []
    ok = True
    for c in sorted(fresh, key=lambda x: x.get("created_at") or ""):
        d = classify(c)
        p = unique_path(d, f"{(c.get('created_at') or '')[:10]}__{slug(c.get('name'))}")
        n = len(c.get("chat_messages") or [])
        print(f"  {(c.get('created_at') or '')[:10]}  {n:>3} msg  "
              f"{d.relative_to(CORPUS.parent)}/{p.name}")
        written_this_run.append(p)
        ok &= write(p, render(c, exported), a.apply, report)

    # The raw export, kept whole beside the previous one.
    print(f"\nRaw export -> {RAW.relative_to(CORPUS.parent)}/{batch.name}/")
    for f in sorted(batch.rglob("*")):
        if f.is_file():
            ok &= copy(f, RAW / batch.name / f.relative_to(batch), a.apply, report)

    # Memories and project files -- new in this export, nothing like them on disk.
    mem = batch / "Memories and Project Files"
    if mem.is_dir():
        print(f"\nMemories and project files -> "
              f"{PERSONALIZATION.relative_to(CORPUS.parent)}/")
        for f in sorted(mem.iterdir()):
            if f.is_file():
                ok &= copy(f, PERSONALIZATION / f.name, a.apply, report)

    nominated = supersede_matches(candidates, convs)
    pairs = []
    for p, c, r in nominated:
        if p in written_this_run:
            print(f"  REFUSED supersede: {p} is a transcript this run wrote; not superseded")
        else:
            pairs.append((p, c, r))
    print(f"\n{len(pairs)} Chat-emitted transcript(s) superseded by a raw export entry:")
    for p, c, r in pairs:
        print(f"  {p.relative_to(CORPUS.parent)}")
        print(f"      -> {c['uuid']}  \"{c.get('name')}\"  "
              f"title match {r:.0%}, same date")
        if a.apply:
            SUPERSEDED.mkdir(parents=True, exist_ok=True)
            dest = SUPERSEDED / p.name
            if copy(p, dest, True, report):
                p.unlink()
    if not pairs:
        print("  (none matched confidently -- Chat-emitted transcripts left in place)")

    unmatched = [p for p in candidates if p not in {x[0] for x in pairs}]
    if unmatched:
        print(f"\n{len(unmatched)} Chat-emitted transcript(s) with NO confident match, kept:")
        for p in unmatched:
            print(f"  {p.relative_to(CORPUS.parent)}")

    wrote = sum(1 for k, _, _ in report if k in ("wrote", "copied"))
    failed = [r for r in report if r[0].startswith("FAILED")]
    print("\n" + "-" * 70)
    if not a.apply:
        print(f"DRY RUN -- {len(report)} file operation(s) planned, nothing touched.")
        print("Re-run with --apply.")
        return 0
    print(f"{wrote} file(s) written and verified by sha256, {len(failed)} failed")
    for _, p, _ in failed:
        print(f"  FAILED {p}")
    print("\nchat/ is gitignored under D-22 -- this is ONE copy until\n"
          "python3 scripts/backup_corpora.py --to <usb> has run.")
    return 1 if failed or not ok else 0


def _retarget(root):
    """Point every corpus path at `root`. Only for tests."""
    return dict(
        CORPUS=root,
        TRANSCRIPTS=root / "staging" / "transcripts",
        NO_CONTENT=root / "empty" / "no-content",
        FILES_ONLY=root / "empty" / "files-only",
        RAW=root / "export" / "raw",
        PERSONALIZATION=root / "personalization",
        SUPERSEDED=root / "superseded",
    )


if __name__ == "__main__":
    sys.exit(main())
