#!/usr/bin/env python3
"""Build a long document from a local model SECTION BY SECTION, resumable.

    python3 scripts/plan_run.py --spec workspace/plan-spec.json --out-dir workspace/plan
    python3 scripts/plan_run.py --spec ... --out-dir ... --assemble-only
    python3 scripts/plan_run.py --self-test

WHY, AND IT IS A LESSON THIS REPOSITORY ALREADY MEASURED
---------------------------------------------------------
Jas, 2026-09-09, going to bed: *"is it possible to get Q16 to write incrementally so if it
fails it can restart and resume where it left off?"*

Yes, and asking one call for a whole document was the mistake. `docs/skills/
briefing-the-local-model` says it plainly, from measurement: **every small brief landed in
4-9 minutes; both structural rewrites burned 58 minutes and failed.** The repair plan was
dispatched as one call three times. The first died on a lease bug, the second on a 30-minute
socket timeout at ~50 minutes, and each failure lost EVERYTHING, because `settings.request`
sets `stream: False` -- the text exists only in the server's buffer until the call returns.

Streaming would save a partial section. Sections save the whole document. So this splits the
work at the seams the author already knows about and writes each one to its own file the
moment it lands. A failure costs one section, and a restart skips every section already on
disk.

WHAT MAKES A SECTION "DONE"
---------------------------
Its file exists AND its sidecar records `complete: true`. Never the file alone: a section cut
off at the token ceiling still produces a file, and a resume that trusted the file's existence
would skip the truncated one and assemble a document with a hole in the middle -- which is the
silent-truncation defect this repository has spent two days removing, rebuilt as a feature.
`ask_q16.ask()` already reports `complete` honestly for every exit, so this reads that.
"""
import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "projects" / "local-inference"))
import roles  # noqa: E402  -- the model behind a role comes from SERVING.json
import ask_q16                                               # noqa: E402
import modellease                                            # noqa: E402

LEASE = REPO / "workspace" / "scoping-lease.json"
MODEL = roles.for_job("scoping", "qwen3.8:27b-mtp-bf16")   # no Responsibility of its own yet (t9554: Jas to place)


def section_paths(out_dir, n, slug):
    return (out_dir / f"{n:02d}-{slug}.md", out_dir / f"{n:02d}-{slug}.json")


def is_done(out_dir, n, slug):
    """Done means the sidecar SAYS it finished, never that the file exists."""
    md, meta = section_paths(out_dir, n, slug)
    if not md.is_file() or not meta.is_file():
        return False
    try:
        return bool(json.loads(meta.read_text()).get("complete"))
    except (json.JSONDecodeError, OSError):
        return False


def run(spec, out_dir, budget, timeout, max_rounds, use_tools=True, verbose=True):
    out_dir.mkdir(parents=True, exist_ok=True)
    shared = spec.get("shared", "")
    sections = spec["sections"]
    done_already = [s for i, s in enumerate(sections, 1) if is_done(out_dir, i, s["slug"])]
    if verbose and done_already:
        print(f"  resuming: {len(done_already)} of {len(sections)} section(s) already on disk")

    lease = modellease.acquire(LEASE, model=MODEL, purpose=spec.get("purpose", "plan_run"),
                               ttl_seconds=max(timeout * 2, 10800))
    holder = lease.get("holder")
    results = []
    try:
        for n, sec in enumerate(sections, 1):
            slug = sec["slug"]
            md, meta = section_paths(out_dir, n, slug)
            if is_done(out_dir, n, slug):
                if verbose:
                    print(f"  [{n}/{len(sections)}] {slug}: already done, skipping")
                results.append((n, slug, True))
                continue
            if verbose:
                print(f"  [{n}/{len(sections)}] {slug}: asking...", flush=True)
            prompt = f"{shared}\n\n{sec['prompt']}"
            t0 = time.time()
            r = ask_q16.ask(prompt, use_tools=use_tools, budget=budget,
                            max_escalations=1, max_rounds=max_rounds,
                            verbose=verbose, holder=holder, timeout=timeout)
            # WRITE WHATEVER CAME BACK, even when it is incomplete. A failed section that
            # leaves nothing behind cannot be inspected, and "it produced nothing" and "it
            # produced something wrong" need different fixes.
            md.write_text(r["text"] or "")
            meta.write_text(json.dumps(
                {k: v for k, v in r.items() if k != "text"} |
                {"slug": slug, "title": sec["title"],
                 "minutes": round((time.time() - t0) / 60, 1)}, indent=2))
            if verbose:
                print(f"      -> {len(r['text'])} chars, complete={r['complete']}, "
                      f"{(time.time() - t0) / 60:.0f} min")
            results.append((n, slug, r["complete"]))
    finally:
        modellease.release(LEASE, lease)
    return results


def assemble(spec, out_dir, dest):
    """Concatenate the sections, and say IN THE DOCUMENT which ones are missing."""
    # A SPEC WITH NO SECTIONS WOULD ASSEMBLE CLEANLY AND MEAN NOTHING: `missing` would be
    # empty because nothing was examined, and the document would carry no warning banner --
    # a verdict passing over an empty set (L-146). Refuse instead.
    if not spec.get("sections"):
        raise ValueError("assemble: the spec lists no sections, so 'nothing missing' would "
                         "be a verdict over an empty set")
    parts, missing = [], []
    for n, sec in enumerate(spec["sections"], 1):
        md, _ = section_paths(out_dir, n, sec["slug"])
        if is_done(out_dir, n, sec["slug"]):
            parts.append(md.read_text().strip())
        else:
            missing.append(sec["title"])
            got = md.read_text().strip() if md.is_file() else ""
            parts.append(f"## {sec['title']}\n\n**[SECTION INCOMPLETE — "
                         f"{'truncated, shown as far as it got' if got else 'not produced'}.]**"
                         + (f"\n\n{got}" if got else ""))
    header = ""
    if missing:
        header = ("> **THIS DOCUMENT IS INCOMPLETE.** The following sections did not finish: "
                  + "; ".join(missing) + ". They are marked in place. Do not read a gap as "
                  "'nothing to say here'.\n\n")
    dest.write_text(header + "\n\n---\n\n".join(parts) + "\n")
    return missing


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--spec")
    ap.add_argument("--out-dir")
    ap.add_argument("--assemble", help="write the joined document here")
    ap.add_argument("--assemble-only", action="store_true")
    ap.add_argument("--budget", type=int, default=12000)
    ap.add_argument("--timeout", type=int, default=5400)
    ap.add_argument("--max-rounds", type=int, default=10)
    ap.add_argument("--no-tools", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    if a.self_test:
        return self_test()
    if not a.spec or not a.out_dir:
        ap.error("--spec and --out-dir are required unless --self-test")

    spec = json.loads(Path(a.spec).read_text())
    out_dir = Path(a.out_dir)

    if not a.assemble_only:
        results = run(spec, out_dir, a.budget, a.timeout, a.max_rounds,
                      use_tools=not a.no_tools)
        # Same shape one level up: an empty `results` makes `bad` empty and prints
        # "0/0 section(s) complete" as though it had succeeded.
        if not results:
            print("  no sections were run -- refusing to report success over an empty set")
            return 2
        bad = [s for _, s, ok in results if not ok]
        print(f"\n  {len(results) - len(bad)}/{len(results)} section(s) complete")
        if bad:
            print(f"  INCOMPLETE: {', '.join(bad)}")
            print("  Re-run the same command; finished sections are skipped.")

    if a.assemble:
        missing = assemble(spec, out_dir, Path(a.assemble))
        print(f"  assembled -> {a.assemble}"
              + (f"  ({len(missing)} section(s) marked incomplete)" if missing else ""))
        return 2 if missing else 0
    return 0


def self_test():
    """Prove resume skips finished work and REFUSES to skip truncated work."""
    import tempfile
    ok = True
    d = Path(tempfile.mkdtemp())
    spec = {"shared": "S", "sections": [{"slug": "a", "title": "A", "prompt": "p"},
                                        {"slug": "b", "title": "B", "prompt": "p"}]}

    print("A section is done only when its sidecar SAYS so:")
    md, meta = section_paths(d, 1, "a")
    md.write_text("text")
    print(f"  [{'ok ' if not is_done(d, 1, 'a') else 'BAD'}]  file alone is NOT done: "
          f"{is_done(d, 1, 'a')}")
    ok &= not is_done(d, 1, "a")
    meta.write_text(json.dumps({"complete": False}))
    print(f"  [{'ok ' if not is_done(d, 1, 'a') else 'BAD'}]  complete:false is NOT done: "
          f"{is_done(d, 1, 'a')}")
    ok &= not is_done(d, 1, "a")
    meta.write_text(json.dumps({"complete": True}))
    print(f"  [{'ok ' if is_done(d, 1, 'a') else 'BAD'}]  complete:true IS done: "
          f"{is_done(d, 1, 'a')}")
    ok &= is_done(d, 1, "a")
    meta.write_text("{not json")
    print(f"  [{'ok ' if not is_done(d, 1, 'a') else 'BAD'}]  a corrupt sidecar is NOT done: "
          f"{is_done(d, 1, 'a')}")
    ok &= not is_done(d, 1, "a")

    print("\nAssembly must announce a hole rather than hiding it:")
    meta.write_text(json.dumps({"complete": True}))
    dest = d / "joined.md"
    missing = assemble(spec, d, dest)
    body = dest.read_text()
    print(f"  [{'ok ' if missing == ['B'] else 'BAD'}]  the unfinished section is named: {missing}")
    ok &= missing == ["B"]
    print(f"  [{'ok ' if 'THIS DOCUMENT IS INCOMPLETE' in body else 'BAD'}]  "
          f"the document says so at the top")
    ok &= "THIS DOCUMENT IS INCOMPLETE" in body
    print(f"  [{'ok ' if 'SECTION INCOMPLETE' in body else 'BAD'}]  and in place where it is missing")
    ok &= "SECTION INCOMPLETE" in body

    print("\n...and a complete document says nothing of the sort:")
    m2 = section_paths(d, 2, "b")
    m2[0].write_text("btext"); m2[1].write_text(json.dumps({"complete": True}))
    missing = assemble(spec, d, dest)
    print(f"  [{'ok ' if not missing else 'BAD'}]  nothing missing: {missing}")
    ok &= not missing
    print(f"  [{'ok ' if 'INCOMPLETE' not in dest.read_text() else 'BAD'}]  no warning banner")
    ok &= "INCOMPLETE" not in dest.read_text()

    print("\n" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
