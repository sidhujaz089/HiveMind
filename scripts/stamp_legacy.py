#!/usr/bin/env python3
"""Stamp historical rows with the collector version that produced them.

GATE 4. `workspace/scoping-proposals.jsonl` holds 70 rows from before Phase 0 and not one
carries a `collector_version`, so `evidence.read_rows` refuses them for having no provenance
rather than for being known-bad. Those are different facts and the record should say which.

ChatGPT's rule, which this obeys: INVALID measurements must remain ACCESSIBLE but must not be
CONSUMABLE as evidence by automation. Deleting them would destroy evidence, and destroying
evidence is the defect being repaired.

**The contract lives in `scripts/tests/test_stamp_legacy.py` and is enforced there, not here.**
In short: never overwrite a version already present, write atomically so a crash leaves the
original rather than half a file, refuse outright on a torn line rather than rewriting a file
that cannot be fully read, and say how many rows were stamped and how many left alone.
"""
import json
import os
import sys
import tempfile
from pathlib import Path


def main():
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <file.jsonl> <version>", file=sys.stderr)
        sys.exit(1)

    path = Path(sys.argv[1])
    version = sys.argv[2]

    raw = path.read_text()
    lines = raw.splitlines(keepends=True)

    stamped = 0
    left_alone = 0
    out_lines = []

    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            out_lines.append(line)
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError:
            print(f"error: line {lineno} is not valid JSON; refusing to modify the file",
                  file=sys.stderr)
            sys.exit(1)
        if "collector_version" in row:
            left_alone += 1
            out_lines.append(line)
        else:
            row["collector_version"] = version
            stamped += 1
            if line.endswith("\n"):
                out_lines.append(json.dumps(row) + "\n")
            else:
                out_lines.append(json.dumps(row))

    new_content = "".join(out_lines)

    # Atomic write: temp file in the same directory, then replace over the target.
    dir_path = path.parent
    fd, tmp_path = tempfile.mkstemp(dir=str(dir_path))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(new_content)
        os.replace(tmp_path, str(path))
    except BaseException:
        os.unlink(tmp_path)
        raise

    print(f"stamped {stamped}, left alone {left_alone}")


if __name__ == "__main__":
    main()

