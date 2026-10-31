#!/usr/bin/env python3
"""2026-10-26: rotating a ledger must lose no row, and must never touch a file it could not save.

`slot-ledger.jsonl` reached 4,421,835,325 bytes / 36,793,533 rows with nothing pruning it, while
`ledger-retention.json` had declared a 1 GB bound and the method all along. Jas, 2026-10-26: "Ok do 1GB
size rotations". The properties this suite pins, in the order they matter:

  1. an archive holds EVERY row of the file it replaced (the live file is emptied only after that);
  2. a ledger inside its bound is left exactly alone;
  3. a retired ledger (declared, not on disk) is not a problem;
  4. archives are pruned to a bounded number, or the bound has only moved to the archive directory;
  5. the trigger is the ledger's OWN declared bound, so the number lives in one place.

Hermetic: a TemporaryDirectory with a two-row retention table. Never the live ledgers.
"""
import gzip
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import rotate_ledger as rl  # noqa: E402

FAILS = []


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


def repo_with(tmp, rows=200):
    r = Path(tmp)
    (r / "workspace").mkdir()
    (r / "workspace" / "big.jsonl").write_text(
        "".join(json.dumps({"i": i, "pad": "x" * 40}) + "\n" for i in range(rows)))
    (r / "workspace" / "small.jsonl").write_text("{}\n")
    (r / "workspace" / "ledger-retention.json").write_text(json.dumps({
        "_what": "test", "ledgers": {
            "workspace/big.jsonl": {"class": "reconstructible", "max_gb": 0.00001},
            "workspace/small.jsonl": {"class": "evidential", "max_gb": 1.0},
            "workspace/retired.jsonl": {"class": "evidential", "max_gb": 0.00001},
        }}))
    return r


def main():
    with tempfile.TemporaryDirectory() as d:
        r = repo_with(d)
        over = rl.oversized(repo=r)
        names = [p.name for p, _, _ in over]
        check("a ledger over its declared bound is found", "big.jsonl" in names, names)
        check("a ledger inside its bound is not", "small.jsonl" not in names, names)
        check("a declared ledger that is not on disk is not a problem",
              "retired.jsonl" not in names, names)

        before = (r / "workspace" / "big.jsonl").read_text().count("\n")
        arch, saved = rl.rotate(r / "workspace" / "big.jsonl", archive_dir=r / "archives")
        after_live = (r / "workspace" / "big.jsonl").read_text()
        check("every row is in the archive", saved == before, (saved, before))
        check("  and the archive reads back with gzip", len(gzip.open(arch, "rb").read().splitlines()) == before)
        check("  and the live file is empty, not deleted", after_live == "" and (r / "workspace" / "big.jsonl").is_file())

        # 4. bounded archives: rotating more times than `keep` must not accumulate.
        for _ in range(4):
            (r / "workspace" / "big.jsonl").write_text("{\"i\": 1}\n")
            rl.rotate(r / "workspace" / "big.jsonl", archive_dir=r / "archives", keep=2)
        kept = list((r / "archives").glob("big-until-*.jsonl.gz"))
        check("archives are pruned to the kept number", len(kept) <= 2, len(kept))

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
