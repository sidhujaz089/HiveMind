#!/usr/bin/env python3
"""One gate: may this row be used as evidence?

Two checks, one decision, deliberately in one place.

**Was it produced by a script we believe?** `scripts/harness_inventory.py` marks 13 of 36
model-touching scripts `quarantined`, and nothing at all stops their output feeding a
decision. Draft four admitted it -- "a label with nothing behind it" -- and all nine outside
reviewers picked that sentence out. Grok stated the consequence: **detection without repair
is just documentation.**

**Was it produced by a collector we believe?** Anything from before Phase 0 came out of a
collector with silent 400-byte truncation, a greedy extractor, and rows that could vanish
entirely. Those numbers still sit in the record looking sound.

These are the same question asked about the script and about the collector version, so they
are one gate. Building two modules that each guard reads would be `L-150` -- the lesson
learned in one place and not carried to the other -- and `scripts/tests/test_scope_extract.py`
now carries a test against exactly that shape of fork, so making it here would be
embarrassing as well as wrong.

TWO RULES THAT LOOK LIKE DETAILS AND ARE NOT:

  * **Refused, not filtered.** A file with one bad row is refused whole. Silently returning
    the good rows is a partial answer that looks like a complete one, which is the precise
    failure mode this phase exists to close.
  * **Inadmissible, not deleted.** ChatGPT's rule: invalid measurements must stay ACCESSIBLE
    but must not be CONSUMABLE by automation. Deleting them would destroy evidence, and
    destroying evidence is the defect being repaired -- so they stay readable behind an
    explicit flag and stop being usable by default.

`status()` asks the inventory rather than remembering its answer. A second hand-kept copy of
a verdict is the same fork again.

**The contract lives in `scripts/tests/test_evidence.py` and is enforced there, not here.**
"""

import json
from pathlib import Path

from harness_inventory import REPO, audit as _inventory_audit, inventory as _inventory

# The four states a collector version can be in
VALIDITY = {"valid", "invalid", "superseded", "unknown"}

_COLLECTOR_RULES = {
    "phase0-2026-09-08": "valid",
    "pre-phase0-broken": "invalid",
    "scope_queue-v1": "valid",
}

_AUDIT_LOG = []


class Refused(Exception):
    pass


_INVENTORY_CACHE = None


def status(script):
    """Ask the inventory; do not keep a second copy of its verdict."""
    global _INVENTORY_CACHE
    p = Path(script)
    if not p.is_absolute():
        p = REPO / p
    result = _inventory_audit(p)
    if result is not None:
        return result["status"]
    if _INVENTORY_CACHE is None:
        _INVENTORY_CACHE = {r["path"]: r["status"] for r in _inventory()}
    rel = p.relative_to(REPO).as_posix()
    if rel in _INVENTORY_CACHE:
        return _INVENTORY_CACHE[rel]
    return "not-applicable"


def validity(collector_version):
    if collector_version is None:
        return "unknown"
    return _COLLECTOR_RULES.get(collector_version, "unknown")


def _refuse(caller, producer, reason):
    _AUDIT_LOG.append({"caller": caller, "producer": producer, "reason": reason})
    raise Refused(f"{caller}: refused to read output from {producer} ({reason})")


def read_rows(path, caller, allow_quarantined=False, allow_invalid=False):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    for row in rows:
        producer = row.get("producer")
        if producer is not None:
            st = status(producer)
            if st == "quarantined" and not allow_quarantined:
                _refuse(caller, producer, "producer is quarantined")
            # A producer the inventory cannot classify gets NO benefit of the doubt. It answers
            # "not-applicable" for a path that resolves to no script, and for a real file that
            # touches no model -- and either way the quarantine question was never actually asked.
            # Admitting those was the other half of the gap closed for collector versions on
            # 2026-09-08; Jas caught that only one branch had been fixed.
            if st == "not-applicable" and not allow_quarantined:
                _refuse(caller, producer, "producer is not in the harness inventory, so its "
                                          "quarantine status was never established")
        elif not allow_invalid:
            _refuse(caller, "<unlabelled>", "no producer recorded")
        cv = row.get("collector_version")
        if cv is None:
            if not allow_invalid:
                _refuse(caller, producer, "no collector_version stamped")
        elif not allow_invalid:
            v = validity(cv)
            if v in ("invalid", "unknown"):
                _refuse(caller, producer, f"collector version {cv} is not admissible (validity: {v})")

    return rows


def audit_log():
    return list(_AUDIT_LOG)
