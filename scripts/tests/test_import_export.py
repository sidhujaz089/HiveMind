#!/usr/bin/env python3
"""Fixtures for the export import.

The import is a one-way write into `chat/`, which has no git rollback under D-22. So the
assertions that matter are the ones about NOT writing:

  1. A conversation already on disk is not imported again -- and the test proves that by
     changing only the UUID and watching the count move.
  2. A Chat-emitted transcript is superseded only on a CONFIDENT match. A same-title file on
     a different date, or a same-date file with a different title, must be kept. A wrong
     supersede destroys the only copy of a session the export never had.
  3. Placement is derived from content, and the rule reproduces the placements the
     2026-08-13 import already made.

"It imported the conversations" was never the risk. "It moved a transcript nothing else
holds" is.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import import_export as ie  # noqa: E402

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


def conv(uuid, name, date, msgs):
    return {"uuid": uuid, "name": name, "created_at": f"{date}T18:00:00.000000Z",
            "updated_at": f"{date}T18:00:00.000000Z", "account": {}, "summary": "",
            "chat_messages": msgs}


def msg(text="hello", sender="human", files=None, attachments=None):
    return {"uuid": "m", "text": text, "content": [], "sender": sender,
            "created_at": "2026-08-01T18:00:00.000000Z",
            "updated_at": "2026-08-01T18:00:00.000000Z",
            "files": files or [], "attachments": attachments or []}


def build(tmp, convs):
    batch = tmp / "data-x-batch-0000"
    batch.mkdir(parents=True)
    (batch / "conversations.json").write_text(json.dumps(convs))
    (batch / "users.json").write_text("{}")
    return batch


def run(tmp, batch, corpus, *extra):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "import_export.py"),
         "--zip", str(batch), "--corpus", str(corpus), *extra],
        capture_output=True, text=True)


def main():
    tmp = Path(tempfile.mkdtemp())
    try:
        print("Placement is derived from the messages, not from the filename:")
        report("text -> staging/transcripts",
               ie.classify(conv("a", "t", "2026-08-01", [msg()])).name, "transcripts")
        report("no text but an attachment -> files-only",
               ie.classify(conv("a", "t", "2026-08-01",
                                [msg(text="", attachments=[{"file_name": "x"}])])).name,
               "files-only")
        report("no text and no file -> no-content",
               ie.classify(conv("a", "t", "2026-08-01", [msg(text="")])).name,
               "no-content")

        print("\nThe UUID header round-trips, because dedupe depends on it:")
        c = conv("uuid-1", "A Title", "2026-08-01", [msg()])
        text = ie.render(c, "2026-08-21")
        report("header carries the UUID", text.splitlines()[0], "UUID: uuid-1")
        report("and it is machine-readable back out",
               list(ie.existing_uuids(_corpus(tmp / "rt", {"a.txt": text}))), ["uuid-1"])

        print("\nA conversation already on disk is NOT imported again:")
        corpus = _corpus(tmp / "c1", {"staging/transcripts/old.txt":
                                      ie.render(conv("dupe", "Old", "2026-07-01", [msg()]),
                                                "2026-08-21")})
        batch = build(tmp / "e1", [conv("dupe", "Old", "2026-07-01", [msg()]),
                                   conv("new", "New", "2026-08-01", [msg()])])
        out = run(tmp, batch, corpus).stdout
        report("1 of 2 already present", "1 of the export's UUIDs are already here" in out, True)
        report("only the new one is imported", "1 conversation(s) to import" in out, True)

        print("\n  and the SAME export with a changed UUID imports both -- so the")
        print("  skip above was the UUID doing work, not an accident of the fixture:")
        batch2 = build(tmp / "e2", [conv("dupe-CHANGED", "Old", "2026-07-01", [msg()]),
                                    conv("new", "New", "2026-08-01", [msg()])])
        out2 = run(tmp, batch2, corpus).stdout
        report("now both import", "2 conversation(s) to import" in out2, True)

        print("\nSupersede requires date AND title. Either alone must NOT match:")
        ce = [conv("x", "Interpreting an AI folder without context", "2026-08-12", [msg()])]
        same = _files(tmp / "s1", ["transcripts/2026-08-12__interpreting-an-ai-folder-"
                                   "without-context.txt"])
        report("same date, same title -> matched", len(ie.supersede_matches(same, ce)), 1)
        wrongdate = _files(tmp / "s2", ["transcripts/2026-08-13__interpreting-an-ai-folder-"
                                        "without-context.txt"])
        report("same title, WRONG DATE -> kept",
               len(ie.supersede_matches(wrongdate, ce)), 0)
        wrongtitle = _files(tmp / "s3",
                            ["transcripts/2026-08-12__something-else-entirely.txt"])
        report("same date, WRONG TITLE -> kept",
               len(ie.supersede_matches(wrongtitle, ce)), 0)

        print("\n  a file with a real UUID is never a supersede candidate at all:")
        withuuid = tmp / "s4" / "transcripts"
        withuuid.mkdir(parents=True)
        (withuuid / "2026-08-12__interpreting-an-ai-folder-without-context.txt").write_text(
            ie.render(conv("11111111-2222-3333-4444-555555555555", "x", "2026-08-12",
                           [msg()]), "2026-08-21"))
        report("export-derived transcripts are excluded",
               len(ie.chat_emitted(tmp / "s4")), 0)

        print("\n  and only files under a transcripts/ directory are candidates:")
        other = tmp / "s5" / "projects" / "family"
        other.mkdir(parents=True)
        (other / "State_Ledger_Family_Project.txt").write_text("## 1. People (3)\n")
        report("a ledger is not a transcript", len(ie.chat_emitted(tmp / "s5")), 0)

        print("\nDry run writes nothing:")
        corpus3 = _corpus(tmp / "c3", {})
        batch3 = build(tmp / "e3", [conv("z", "Z", "2026-08-01", [msg()])])
        run(tmp, batch3, corpus3)
        report("no files created", len(list(corpus3.rglob("*.txt"))), 0)

        print("\n--apply writes, and every write is verified by re-reading it:")
        r = run(tmp, batch3, corpus3, "--apply")
        report("exit 0", r.returncode, 0)
        report("transcript landed",
               (corpus3 / "staging" / "transcripts" / "2026-08-01__z.txt").exists(), True)
        report("raw export kept whole",
               (corpus3 / "export" / "raw" / "data-x-batch-0000"
                / "conversations.json").exists(), True)
        report("0 failed", "0 failed" in r.stdout, True)

        print("\nA write whose bytes do NOT match is reported as FAILED, not as written:")
        # Every earlier assertion here exercised the success path, so the verification
        # could have been deleted without a single test noticing -- mutate.py caught
        # exactly that on 2026-08-25 (`import-writes-unverified`). This makes the compare
        # disagree, which is the only way to see the branch that matters. It is the same
        # shape as L-060: a verify that never had the chance to fail proves nothing.
        real_sha = ie.sha
        ie.sha = lambda p: "0" * 64
        try:
            rep = []
            outcome = ie.write(tmp / "verify" / "x.txt", "content", True, rep)
        finally:
            ie.sha = real_sha
        report("write() returns False", outcome, False)
        report("and says FAILED verify", rep[0][0], "FAILED verify")

        print("\n  and copy() refuses on the same mismatch:")
        srcf = tmp / "verify" / "src.txt"
        srcf.write_text("payload")
        ie.sha = lambda p: "0" * 64 if "dst" in str(p) else "1" * 64
        try:
            rep2 = []
            outcome2 = ie.copy(srcf, tmp / "verify" / "dst.txt", True, rep2)
        finally:
            ie.sha = real_sha
        report("copy() returns False", outcome2, False)
        report("and says FAILED verify", rep2[0][0], "FAILED verify")

        print("\nRe-running --apply imports nothing further (idempotent):")
        r2 = run(tmp, batch3, corpus3, "--apply")
        report("0 to import", "0 conversation(s) to import" in r2.stdout, True)

        print("\nA corrupt export refuses rather than importing part of it:")
        bad = tmp / "e4" / "data-y-batch-0000"
        bad.mkdir(parents=True)
        (bad / "conversations.json").write_text("{not json")
        r3 = run(tmp, bad, _corpus(tmp / "c4", {}))
        report("non-zero exit", r3.returncode != 0, True)

        print("\nTimestamps carry the zone, so 13:37 MST is distinguishable from MDT:")
        report("January is MST", ie.stamp("2026-01-15T20:37:00Z"), "2026-01-15 13:37 MST")
        report("July is MDT", ie.stamp("2026-07-15T19:37:00Z"), "2026-07-15 13:37 MDT")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


def _corpus(root, files):
    for name, text in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _files(root, names):
    out = []
    for n in names:
        p = root / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("no uuid here\n")
        out.append(p)
    return out


if __name__ == "__main__":
    sys.exit(main())
