#!/usr/bin/env python3
"""t122 -- workspace/TRANSCRIPT-INDEX.md is GENERATED from the transcript corpora.

THE CONTRACT THIS ORACLE PINS, all of it on scripts/chat_transcripts.py, the module that
already renders Chat captures:

    scan_transcripts(root) -> list of dicts, keys exactly: date, slug, subject, size, path
    render_index(entries)  -> markdown string; newest date first, ties by slug ascending
    write_index(root)      -> writes <root>/workspace/TRANSCRIPT-INDEX.md, returns its Path

    date    = the leading YYYY-MM-DD of the file stem, '' when the stem has no date
    slug    = the file stem with that date prefix removed
    subject = the first non-empty line of the file, leading '#' and whitespace stripped,
              VERBATIM -- never truncated. For a .jsonl capture: the first record's "text".
    size    = os.path.getsize(path), bytes on disk -- not lines, not rounded
    path    = repo-relative posix path

Only chat/captures/ and chat/projects/*/transcripts/ are scanned: artifacts/, summaries/ and a
project with no transcripts/ directory must not appear.

RED ON MAIN because scripts/chat_transcripts.py defines one public name, assemble, and
scan_transcripts / render_index / write_index / _subject_of raise AttributeError.

No live state is touched: every fixture lives under tempfile.TemporaryDirectory and the index
is written inside that temp tree, never into the repository.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def make_fixture(root):
    """A repo-shaped tree under a temp dir. Returns the files the tests cite."""
    for rel in ("chat/captures", "chat/projects/family/transcripts",
                "chat/projects/family/artifacts", "chat/projects/family/summaries",
                "chat/projects/work/transcripts", "chat/projects/empty"):
        (root / rel).mkdir(parents=True)
    fx = {}
    fx["cap"] = (root / "chat" / "captures"
                 / "2026-08-20__bridge-structure-and-session-capture.jsonl")
    fx["cap"].write_text(
        json.dumps({"session": "bridge-structure-and-session-capture", "role": "note",
                    "text": "CAPTURE STARTS HERE. Turns before this line were not captured live."})
        + "\n"
        + json.dumps({"session": "bridge-structure-and-session-capture", "role": "user",
                      "text": "Ok code replied"}) + "\n",
        encoding="utf-8")
    fx["rec"] = (root / "chat" / "captures"
                 / "2026-08-20__bridge-structure-and-session-capture--RECONSTRUCTION.md")
    fx["rec"].write_text("# RECONSTRUCTION (not a manifest) -- backfill for 2026-08-20\n\nbody\n",
                         encoding="utf-8")
    fx["fam"] = (root / "chat" / "projects" / "family" / "transcripts"
                 / "2026-03-02__duration-of-relationship-rough-patches.txt")
    fx["fam"].write_text("Duration of relationship, rough patches\n\nmore\n", encoding="utf-8")
    fx["fam2"] = (root / "chat" / "projects" / "family" / "transcripts"
                  / "2026-05-11__managing-milan-s-disruptive-behavior.txt")
    fx["fam2"].write_text("# Managing Milan's disruptive behavior\n\nbody\n", encoding="utf-8")
    fx["work"] = (root / "chat" / "projects" / "work" / "transcripts"
                  / "2026-06-28__continuity-system-redesign.txt")
    fx["work"].write_text(
        "Continuity system redesign and product mechanics for the family project\n",
        encoding="utf-8")
    fx["undated"] = (root / "chat" / "projects" / "work" / "transcripts"
                     / "with_Earl_Tyler_Coder_Sessions.jsonl")
    fx["undated"].write_text(
        json.dumps({"role": "note", "text": "Undated capture: date is empty, sorts last."})
        + "\n", encoding="utf-8")
    fx["artifact"] = (root / "chat" / "projects" / "family" / "artifacts"
                      / "McDonald_s_transcript.txt")
    fx["artifact"].write_text("not a transcript -- must not be indexed\n", encoding="utf-8")
    fx["summary"] = (root / "chat" / "projects" / "family" / "summaries"
                     / "2026-06-28__summary.txt")
    fx["summary"].write_text("a summary, not a transcript\n", encoding="utf-8")
    return fx


def test_scan_finds_both_corpora_and_nothing_else(root, fx, ct):
    entries = ct.scan_transcripts(root)
    pairs = sorted((e["date"], e["slug"]) for e in entries)
    want = [
        ("", "with_Earl_Tyler_Coder_Sessions"),
        ("2026-03-02", "duration-of-relationship-rough-patches"),
        ("2026-05-11", "managing-milan-s-disruptive-behavior"),
        ("2026-06-28", "continuity-system-redesign"),
        ("2026-08-20", "bridge-structure-and-session-capture"),
        ("2026-08-20", "bridge-structure-and-session-capture--RECONSTRUCTION"),
    ]
    print("  entries scanned: %r" % (pairs,))
    assert pairs == want, "scan_transcripts: expected %r, got %r" % (want, pairs)
    keys = {k for e in entries for k in e}
    assert keys == {"date", "slug", "subject", "size", "path"}, "entry keys: %r" % (sorted(keys),)
    strays = [e["path"] for e in entries if "artifacts" in e["path"]
              or "summaries" in e["path"] or "/empty/" in e["path"]]
    assert strays == [], "artifacts/summaries are not transcripts: %r" % (strays,)


def test_subject_size_and_path_are_the_real_ones(root, fx, ct):
    by_slug = {e["slug"]: e for e in ct.scan_transcripts(root)}
    cases = [
        ("duration-of-relationship-rough-patches", fx["fam"],
         "Duration of relationship, rough patches"),
        ("managing-milan-s-disruptive-behavior", fx["fam2"],
         "Managing Milan's disruptive behavior"),
        ("continuity-system-redesign", fx["work"],
         "Continuity system redesign and product mechanics for the family project"),
        ("bridge-structure-and-session-capture", fx["cap"],
         "CAPTURE STARTS HERE. Turns before this line were not captured live."),
        ("bridge-structure-and-session-capture--RECONSTRUCTION", fx["rec"],
         "RECONSTRUCTION (not a manifest) -- backfill for 2026-08-20"),
    ]
    for slug, path, subject in cases:
        entry = by_slug[slug]
        print("  %-44s %r" % (slug, entry["subject"]))
        assert entry["subject"] == subject, "subject of %s: expected %r, got %r" % (
            slug, subject, entry["subject"])
        assert entry["size"] == os.path.getsize(path), (
            "size of %s must be os.path.getsize: expected %r, got %r"
            % (slug, os.path.getsize(path), entry["size"]))
    rel = "chat/projects/work/transcripts/2026-06-28__continuity-system-redesign.txt"
    assert by_slug["continuity-system-redesign"]["path"] == rel, (
        "path is repo-relative posix: expected %r, got %r"
        % (rel, by_slug["continuity-system-redesign"]["path"]))
    assert by_slug["with_Earl_Tyler_Coder_Sessions"]["date"] == "", "undated stem has date ''"


def test_render_orders_by_date_and_keeps_the_subject_whole(root, fx, ct):
    entries = ct.scan_transcripts(root)
    md = ct.render_index(entries)
    order = ["bridge-structure-and-session-capture",
             "bridge-structure-and-session-capture--RECONSTRUCTION",
             "continuity-system-redesign",
             "managing-milan-s-disruptive-behavior",
             "duration-of-relationship-rough-patches"]
    pos = [md.index("| %s |" % s) for s in order]
    print("  row positions: %r" % (pos,))
    assert pos == sorted(pos), "newest date first, ties by slug ascending: %r" % (pos,)
    long_subject = "Continuity system redesign and product mechanics for the family project"
    assert long_subject in md, "the 457-byte transcript's subject must appear verbatim"
    assert "[TRUNCATED" not in md, "the index must not truncate a subject"
    assert "…" not in md, "the index must not elide a subject"
    assert str(os.path.getsize(fx["cap"])) in md, "the size column is bytes on disk"
    assert "McDonald_s_transcript" not in md, "artifacts must not be listed"
    assert "2026-06-28__summary" not in md, "summaries must not be listed"
    rows = [ln for ln in md.splitlines() if ln.startswith("|") and "---" not in ln]
    assert len(rows) == len(entries) + 1, (
        "one row per entry plus a header: expected %d, got %d" % (len(entries) + 1, len(rows)))
    assert "Transcript index" in md, "the index says what it is"


def test_write_index_writes_the_file_and_regenerates_it(root, fx, ct):
    assert not (root / "workspace").exists(), "the fixture starts with no workspace/"
    out = ct.write_index(root)
    assert Path(out) == root / "workspace" / "TRANSCRIPT-INDEX.md", (
        "write_index must return <root>/workspace/TRANSCRIPT-INDEX.md, got %r" % (out,))
    text = Path(out).read_text(encoding="utf-8")
    assert "duration-of-relationship-rough-patches" in text, "a real transcript is listed"
    assert text == ct.render_index(ct.scan_transcripts(root)), (
        "the file on disk is exactly what render_index produces")
    again = Path(ct.write_index(root)).read_text(encoding="utf-8")
    assert again == text, "regenerating over the same tree is idempotent"
    new = root / "chat" / "projects" / "work" / "transcripts" / "2026-10-04__a-new-session.txt"
    new.write_text("A new session added after the first generation\n", encoding="utf-8")
    third = Path(ct.write_index(root)).read_text(encoding="utf-8")
    assert "2026-10-04__a-new-session" in third, "a transcript added later appears on regenerate"
    assert "A new session added after the first generation" in third, "and its subject too"


def main():
    try:
        import chat_transcripts as ct
    except ImportError as exc:
        print("FAIL import chat_transcripts: %s" % exc)
        return 1
    tests = [test_scan_finds_both_corpora_and_nothing_else,
             test_subject_size_and_path_are_the_real_ones,
             test_render_orders_by_date_and_keeps_the_subject_whole,
             test_write_index_writes_the_file_and_regenerates_it]
    bad = 0
    for fn in tests:
        with tempfile.TemporaryDirectory(prefix="t122-") as td:
            root = Path(td)
            fx = make_fixture(root)
            print("\n%s" % fn.__name__)
            try:
                fn(root, fx, ct)
                print("  [ok] all checks passed")
            except (AttributeError, KeyError, ValueError, AssertionError) as exc:
                print("  [BAD] %s: %s" % (type(exc).__name__, exc))
                bad += 1
    print("\n%d passed, %d failed" % (len(tests) - bad, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
