#!/usr/bin/env python3
r"""The export-owned identity is ONE predicate, not two.

scripts/import_export.py stamps a transcript's own uuid into its first line as
`UUID: <uuid>` (render(), import_export.py:139), and its dedupe reader accepts any token
there (existing_uuids(), :119 -- r"UUID:\s*(\S+)", skipping the literal "(not"). Its
supersede reader is stricter: chat_emitted() (:186) calls a file "chat's own" only when the
header does NOT match r"UUID:\s*[0-9a-f-]{36}". A transcript stamped with a non-canonical
uuid -- "z" -- is owned to one reader and unowned to the other, and the second reader hands
it to the supersede block, which copies it to chat/superseded/ and then unlinks the only
copy (:340-341). `chat/` is gitignored under D-22, so there is no rollback.
RCA workspace/rca/db4603a558f1.md sections 2 and 5.

The fixtures are rendered by render() itself, so what is tested is the writer's own output.
"""
import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
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


def msg(date):
    return {"uuid": "m", "text": "hello", "content": [], "sender": "human",
            "created_at": f"{date}T18:00:00.000000Z",
            "updated_at": f"{date}T18:00:00.000000Z",
            "files": [], "attachments": []}


def conv(uuid, name, date):
    return {"uuid": uuid, "name": name, "created_at": f"{date}T18:00:00.000000Z",
            "updated_at": f"{date}T18:00:00.000000Z", "account": {}, "summary": "",
            "chat_messages": [msg(date)]}


def write_file(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def build(tmp, convs):
    batch = tmp / "data-x-batch-0000"
    batch.mkdir(parents=True, exist_ok=True)
    (batch / "conversations.json").write_text(json.dumps(convs), encoding="utf-8")
    return batch


def run_cli(batch, corpus, *extra):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "import_export.py"),
         "--zip", str(batch), "--corpus", str(corpus), *extra],
        capture_output=True, text=True, timeout=120)


def main():
    tmp = Path(tempfile.mkdtemp())
    try:
        print("is_export_owned accepts what render() stamps, whatever the uuid shape:")
        report("is_export_owned exists on import_export", hasattr(ie, "is_export_owned"), True)

        def owned(p):
            fn = getattr(ie, "is_export_owned", None)
            return None if fn is None else bool(fn(p))

        c1 = tmp / "c1"
        stamped = write_file(c1 / "staging" / "transcripts" / "2026-08-01__z.txt",
                             ie.render(conv("z", "Z", "2026-08-01"), "2026-08-21"))
        canonical = write_file(c1 / "staging" / "transcripts" / "2026-08-02__real.txt",
                               ie.render(conv("11111111-2222-3333-4444-555555555555", "Z",
                                              "2026-08-02"), "2026-08-21"))
        live = write_file(c1 / "staging" / "transcripts" / "2026-08-03__live.txt",
                          "UUID: (not available)\nSOURCE: chat\n")
        headerless = write_file(c1 / "staging" / "transcripts" / "2026-08-04__plain.txt",
                                "no uuid here\n")
        ledger = write_file(c1 / "projects" / "family" / "State_Ledger_Family_Project.txt",
                            "## 1. People (3)\n")

        report("a transcript stamped with the non-canonical uuid 'z' is export-owned",
               owned(stamped), True)
        report("a canonical uuid is export-owned", owned(canonical), True)
        report("UUID: (not available) is NOT export-owned", owned(live), False)
        report("a transcript with no UUID header is NOT export-owned", owned(headerless), False)
        report("a project file is NOT export-owned", owned(ledger), False)

        print("\nchat_emitted() asks that predicate -- a stamped transcript is never a candidate:")
        report("stamped by a previous export -> not a supersede candidate",
               stamped in ie.chat_emitted(c1), False)
        report("a live-chat transcript (UUID: (not available)) IS a candidate",
               live in ie.chat_emitted(c1), True)
        report("a headerless transcript IS a candidate", headerless in ie.chat_emitted(c1), True)
        report("a project file is never a candidate", ledger in ie.chat_emitted(c1), False)

        print("\nEnd to end: an import must not supersede a transcript it owns:")
        c2 = tmp / "c2"
        target = write_file(c2 / "staging" / "transcripts" / "2026-08-01__z.txt",
                            ie.render(conv("z", "Z", "2026-08-01"), "2026-08-21"))
        batchA = build(tmp / "eA", [conv("z", "Z", "2026-08-01")])
        r = run_cli(batchA, c2, "--apply")
        report("exit 0", r.returncode, 0)
        report("dedupe sees it as already present",
               "0 conversation(s) to import" in r.stdout, True)
        report("it is still in staging/transcripts/", target.exists(), True)
        report("nothing landed in superseded/",
               sorted(p.name for p in (c2 / "superseded").glob("*.txt")), [])

        print("\nBelt-and-suspenders: the destructive unlink refuses a current-run file.")
        # Nominate the file THIS run writes, by name, so the guard is tested independently
        # of the pre-mutation snapshot's ordering. (The existing suite reaches into the
        # module this way already, for write()/copy().)
        c3 = tmp / "c3"
        batchB = build(tmp / "eB", [conv("z", "Z", "2026-08-01")])
        dest = c3 / "staging" / "transcripts" / "2026-08-01__z.txt"
        saved = dict(CORPUS=ie.CORPUS, TRANSCRIPTS=ie.TRANSCRIPTS, NO_CONTENT=ie.NO_CONTENT,
                     FILES_ONLY=ie.FILES_ONLY, RAW=ie.RAW,
                     PERSONALIZATION=ie.PERSONALIZATION, SUPERSEDED=ie.SUPERSEDED)
        real_chat_emitted = ie.chat_emitted
        out = io.StringIO()
        rc = None
        ie.chat_emitted = lambda root=None: [dest]
        try:
            with contextlib.redirect_stdout(out):
                rc = ie.main(["--zip", str(batchB), "--corpus", str(c3), "--apply"])
        finally:
            ie.chat_emitted = real_chat_emitted
            for k, v in saved.items():
                setattr(ie, k, v)
        text = out.getvalue()
        report("--apply reports the refusal in its own words", "REFUSED supersede" in text, True)
        report("the file this run wrote survives", dest.exists(), True)
        report("and is not moved into superseded/",
               sorted(p.name for p in (c3 / "superseded").glob("*.txt")), [])
        report("a clean run still exits 0", rc, 0)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
