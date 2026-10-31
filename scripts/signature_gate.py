#!/usr/bin/env python3
"""Refuse a commit that introduces a defect signature that DESTROYS EVIDENCE.

`scripts/harness_inventory.py` counts three signatures across 48 model-touching scripts.
Counting is not preventing: nothing stops a forty-ninth being committed tomorrow, and the
inventory would dutifully report it. Four of the nine reviewers of draft four said the same
thing -- Grok: "treat the inventory as a CI gate, not a report."

Two signatures are worth blocking at commit time, and only two, because they destroy the
evidence needed to diagnose the failure rather than merely producing a wrong answer:

  * the greedy extractor -- `re.search(r"\\{.*\\}", text, re.S)`, first brace to last, which
    mis-parses three of the four shapes a model actually produces
  * a fixed-width slice stored INTO a record -- `parse_error = answer[:400]`, which made five
    of eight failing rows exactly 400 bytes and undiagnosable

**A slice into a `print()` or a log line is DISPLAY, not a record, and must keep passing.**
That distinction is already drawn correctly in `harness_inventory.py` and has to be drawn here
too: a gate that blocks legitimate code is bypassed with `--no-verify` inside a week, and is
then worth less than nothing.

Written as PYTHON and called from `scripts/hooks/pre-commit`, deliberately: two dispatches that
asked a local model to add a section to the POSIX-sh hook both ran an hour and failed, while
every Python brief this week landed in 4-9 minutes.

**The contract lives in `scripts/tests/test_precommit_signature_gate.py`.**

    python3 scripts/signature_gate.py <file> [<file> ...]      # exit 1 if any is refused
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness_inventory import code_only                       # noqa: E402

GREEDY = re.compile(r"re\.(search|match|findall)\(\s*r?[\"']\\?\{\.\*")
DICT_SUBSCRIPT_ASSIGN = re.compile(
    r"""\[\s*["']\w+["']\s*\]\s*=\s*\w+\s*\[\s*:\s*\d{2,}\s*\]""")
# The history of this floor is kept in git and in t285. It said "do not move this number"
# and it was right at the time -- the number could not move while the number was the
# discriminator. It no longer is; see below.
# THE FLOOR IS GONE AND t285 IS CLOSED, 2026-09-14. It was \d{3,} -- a magic number, and the
# task said so: "the real fix is distinguishing a hash slice from a record slice, not picking a
# different magic number." Jas supplied the observation that makes it tractable: truncation
# widths cluster on BINARY DOUBLINGS (16, 32, 64, 128, 256...) as well as round decimals
# (100, 200, 500), *"since those numbers double, those are typical cutoff points as well."*
#
# And that is precisely why no floor can work. The legitimate short slices here are hash and id
# prefixes -- hexdigest()[:16], a sha256 at [:32] or [:64], a uuid at [:8] -- and they sit on
# the SAME numbers. Any floor either lets `line[:80]` through (the old one did) or fires on a
# hash (the reason the old one could not be lowered). The width carries no signal at all.
#
# INVERTED: the approved set is not a range of numbers, it is a short nameable list of
# EXPRESSIONS that legitimately yield a fixed-width prefix. A hash, a digest, a uuid, a token.
# Everything else sliced into a record is truncation, AT ANY WIDTH. The good set is small and
# closed; the set of ways to shorten a string is neither.
HASH_EXPR = re.compile(
    r"(hexdigest|sha\d|sha_|md5|blake2|shake_|\buuid|token_hex|token_urlsafe|\bdigest\s*\()",
    re.IGNORECASE)

# TWO DIGITS, not one, and this is a floor on IDIOM rather than on size. `[:6]`, `[:2]`, `[:1]`
# are list-cardinality idioms ("keep at most six tags"), not evidence destruction, and flagging
# them is how a gate starts refusing ordinary code and gets switched off inside a week
# (AGENTS.md rule 9). From 10 upward a slice into a recorded value is a truncation.
DICT_LITERAL_VALUE = re.compile(
    r"""["']\w+["']\s*:\s*.*\[\s*:\s*\d{2,}\s*\]""")
# THE TAIL FORM, added 2026-09-14. The pattern above sees only HEAD slices, so
#     {"stdout": out[-5000:], "stderr": err[-2000:]}
#     {"stdout": r.stdout[-800:]}
#     {"transcript": transcript[-60:]}
# were invisible to it -- three real record truncations, in three files, found by hand with a
# grep after Jas pointed out the obvious: *"if you rely on the detector you're not going to
# find spots the detector is blind to."* Keeping the last N bytes destroys evidence exactly as
# keeping the first N does; which end you keep is not the question.
#
# TWO DIGITS HERE, not three. The three-digit floor on the head form exists because a short
# HEAD slice is usually a legitimate hash or id prefix (hexdigest()[:16]) -- see t285. There is
# no equivalent legitimate short TAIL slice of a recorded value, so this side can be strict.
# `[-1:]` and `[-2:]` are still exempt, since those are list idioms rather than truncation.
DICT_LITERAL_TAIL = re.compile(
    r"""["']\w+["']\s*:\s*.*\[\s*-\s*\d{2,}\s*:\s*\]""")
DISPLAY = re.compile(r"\b(print|say|log|echo)\s*\(")


BASELINE = Path(__file__).resolve().parent.parent / "workspace" / "signature-baseline.json"


def _exempt():
    """Files that do not fail the commit for a signature they already carry.

    Two kinds, kept in one file because both are exemptions and both must be visible:
    detectors and fixtures that carry the signature AS DATA, and genuine pre-existing
    defects recorded so the gate blocks NEW ones without going red on day one over old
    ones. The second kind is a ratchet, not an amnesty -- workspace/signature-baseline.json
    names every file, its line and its defect, and the list may only shrink.
    """
    try:
        with open(BASELINE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        # NO BASELINE MEANS NO EXEMPTIONS, never "exempt everything". A gate that opens
        # when its config is missing is the fail-open shape this repository keeps finding.
        return set()
    out = set()
    for section in ("carries_the_signature_as_data", "real_and_unfixed"):
        for k in (data.get(section) or {}):
            if not k.startswith("_"):
                out.add(k)
    return out


def line_truncates_a_record(line):
    """Does this ONE line cut a value it is recording? The whole per-line decision, in one
    place, because there were two and they drifted twice in one day.

    2026-09-14. check_file() made this decision inline and
    scripts/tests/test_signature_gate_expressions.py made it again in its own `flags()` helper.
    Adding DICT_LITERAL_TAIL broke the test while the gate was right; adding HASH_EXPR broke it
    again the same way. A COPY OF A DECISION IS A SECOND DECISION. Both now call this, so the
    oracle tests the gate rather than its own duplicate, and a new rule reaches both at once.

    Order matters and each step is load-bearing:
      1. DISPLAY -- printing a shortened value is not recording one. A gate that refuses
         ordinary logging gets switched off inside a week (AGENTS.md rule 9).
      2. HASH_EXPR -- a hash, digest, uuid or token prefix is a fixed-width IDENTIFIER, not a
         cut record. This is what closes t285: it decides on WHAT IS SLICED, so the width no
         longer has to carry a signal it never carried.
      3. the record patterns -- head, tail, and subscript assignment.
    """
    if DISPLAY.search(line):
        return False
    if HASH_EXPR.search(line):
        return False
    return bool(DICT_SUBSCRIPT_ASSIGN.search(line)
                or DICT_LITERAL_VALUE.search(line)
                or DICT_LITERAL_TAIL.search(line))


def check_file(path):
    """Refuse a file for a defect signature. PROSE ABOUT THE DEFECT IS NOT THE DEFECT.

    The first version scanned raw source and refused 18 files already in the repository,
    scripts/jsonx.py among them -- the module the whole extractor migration moved TO,
    whose docstring documents the greedy regex it exists to replace. A gate that refuses
    the fix as though it were the bug gets bypassed with --no-verify inside a week, which
    is what this file's own header warns about.

    code_only() blanks docstrings and comments IN PLACE and leaves every other string
    literal alone. That distinction is load-bearing in both directions: the greedy pattern
    IS a string literal, so blanking all strings would blind the detector completely, and
    harness_inventory.py records two earlier versions that got exactly this wrong.
    """
    rel = str(path)
    root = Path(__file__).resolve().parent.parent
    if Path(path).resolve().is_relative_to(root):     # outside the repo: checked by its own name
        rel = str(Path(path).resolve().relative_to(root))
    if rel in _exempt():
        return []
    # A TEST SUITE IS NOT CHECKED, FOR THE SAME REASON PROSE IS NOT THE DEFECT.
    # library_checks.run() already refuses to check anything under tests/ -- "a fixture that
    # proves a check fails has to contain the defect; checking it would refuse every oracle
    # this repository is built on" -- but this gate never implemented the same rule, and
    # pre-commit runs THIS one first. The result: test_library.py could not be committed by
    # anyone. Its line `write_text('row["parse_error"] = answer[:400]')` is the fixture that
    # proves truncates-record works, and this gate read it as the defect it is testing for.
    # Latent since the fixture was written, and invisible because the gate only sees STAGED
    # files -- nobody had touched that suite since. Found 2026-09-22 by being blocked by it.
    if "/tests/" in f"/{rel}":
        return []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            src = code_only(f.read())
    except OSError:
        return []
    faults = []
    if GREEDY.search(src):
        faults.append("greedy-extract")
    for line in src.splitlines():
        if DISPLAY.search(line):
            continue
        if line_truncates_a_record(line):
            faults.append("truncates-record")
            break
    return faults


def main():
    refused = False
    for path in sys.argv[1:]:
        for fault in check_file(path):
            print(f"  {path}: {fault}")
            refused = True
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
