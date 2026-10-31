#!/usr/bin/env python3
"""The routine must never write over an existing backup."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import corpus_routine as cr

DAY = "2026-08-26"


def bundle(nas, name, manifest=None):
    """manifest: None = no manifest, 'inner' = nested, 'flat' = top level."""
    d = nas / name
    (d / "ai-workspace-corpora").mkdir(parents=True)
    if manifest == "inner":
        (d / "ai-workspace-corpora" / "MANIFEST.sha256").write_text("x")
    elif manifest == "flat":
        (d / "MANIFEST.sha256").write_text("x")
    return d


def main():
    fails = []

    def check(label, got, want):
        if got != want:
            fails.append(f"{label}: got {got!r}, wanted {want!r}")

    with tempfile.TemporaryDirectory() as td:
        nas = Path(td)

        # Nothing there yet -> use the plain dated name.
        dest, _ = cr.choose_dest(nas, DAY)
        check("empty NAS uses the dated name", dest.name, f"ai-workspace-corpora-{DAY}")

        # A bundle WITH a manifest means today is done. The manifest lives one level in,
        # and checking only the top level is what caused a verified 478-file bundle to be
        # picked as a destination and truncated to 177.
        good = bundle(nas, f"ai-workspace-corpora-{DAY}", manifest="inner")
        dest, why = cr.choose_dest(nas, DAY)
        check("a nested MANIFEST counts as done", dest, None)
        check("  and says which bundle", f"ai-workspace-corpora-{DAY}" in why, True)
        check("has_manifest finds the nested one", cr.has_manifest(good), True)

        # Older bundles are flat; both layouts must count.
        with tempfile.TemporaryDirectory() as t2:
            n2 = Path(t2)
            bundle(n2, f"ai-workspace-corpora-{DAY}", manifest="flat")
            check("a flat MANIFEST counts too", cr.choose_dest(n2, DAY)[0], None)

    with tempfile.TemporaryDirectory() as td:
        nas = Path(td)
        # A PARTIAL -- no manifest -- must be left alone and the name must not be reused.
        part = bundle(nas, f"ai-workspace-corpora-{DAY}", manifest=None)
        dest, why = cr.choose_dest(nas, DAY)
        check("a partial is not reused", dest.name, f"ai-workspace-corpora-{DAY}-2")
        check("  and the partial still exists", part.is_dir(), True)
        check("  and the note explains why", "partial" in why, True)

        # Once a suffixed bundle carries a manifest, TODAY IS DONE. Checking only the base
        # name made every later run write -3, -4, ... while a good bundle sat beside it.
        bundle(nas, f"ai-workspace-corpora-{DAY}-2", manifest="inner")
        check("a suffixed bundle with a manifest ends the day",
              cr.choose_dest(nas, DAY)[0], None)

        # And with only partials, keep walking rather than colliding.
        with tempfile.TemporaryDirectory() as t3:
            n3 = Path(t3)
            bundle(n3, f"ai-workspace-corpora-{DAY}")
            bundle(n3, f"ai-workspace-corpora-{DAY}-2")
            check("two partials -> -3", cr.choose_dest(n3, DAY)[0].name,
                  f"ai-workspace-corpora-{DAY}-3")

    for f in fails:
        print("FAIL:", f)
    print("test_corpus_routine:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
