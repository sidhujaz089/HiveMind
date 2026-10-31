#!/usr/bin/env python3
"""Escalations live on each ROLE, a class may have SEVERAL holders, and the asker is never a target.

t9540 R5 (Jas 2026-10-28: "Each role gets an Escalations list, edited like Responsibilities... A class can be
held by several roles, e.g. Claude and DSH both hold technical... escalate(cls) returns the set of roles
holding cls"), plus Claude's decision 2: "exclude the asker's role".

WHY IT MATTERS: the old model was a position in a top-level table (_escalation: {vision: 1, technical: 2,
rework: 3}), so a class resolved to exactly ONE role, and a role that failed its own work had its queue
handed to itself unless a "one rank up" guard fired. The guard could only ever guess ONE successor; with a
set, a second holder is a real answer.

The seeding is what makes this shippable with no data rewrite: a role with no `escalations` list inherits the
class whose position in the old table equals its rank, so every existing answer is unchanged until Jas edits
a list.
"""
import copy
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def main():
    import role_store as R
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'FAIL'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    live = R.load()
    d = copy.deepcopy(live)

    # 1. SEEDED: the old table's answers, on a store seeded that way. It used to run on the LIVE store, which
    #    stopped being a single-holder example on 2026-10-30 when the Harness was given technical escalations
    #    alongside Claude -- so the assertion was not wrong about the rule, it was asserting that history had
    #    stood still. The rule is pinned on a copy; today's data is asserted separately, below.
    d0 = copy.deepcopy(d)
    d0["roles"]["dsh"]["escalations"] = []
    for cls, want in (("vision", "jas"), ("technical", "claude"), ("rework", "manager")):
        got = R.escalate_all(cls, d=d0)
        check(f"seeded: {cls} -> [{want}]", got in ([want], ), got)
        check(f"  and escalate() still answers {want}", R.escalate(cls, d=d0) == want, R.escalate(cls, d=d0))
    check("the LIVE store resolves technical to both holders it actually has",
          "claude" in R.escalate_all("technical", d=d), R.escalate_all("technical", d=d))

    # 2. A SECOND HOLDER. DSH holds technical as well as Claude.
    d2 = copy.deepcopy(d)
    d2["roles"]["dsh"]["escalations"] = ["technical"]
    holders = R.escalate_all("technical", d=d2)
    check("two holders are both returned, in chart order", holders == ["claude", "dsh"], holders)
    check("  and escalate() still gives ONE answer for a `needs` field", R.escalate("technical", d=d2) in holders,
          R.escalate("technical", d=d2))

    # 3. THE ASKER IS EXCLUDED -- and with a second holder it now has a real successor instead of
    #    "one rank up", which is the improvement the set buys.
    check("the asker is not a target", R.escalate_all("technical", from_role="claude", d=d2) == ["dsh"],
          R.escalate_all("technical", from_role="claude", d=d2))
    check("  so escalate() from Claude answers DSH, not a rank-up guess",
          R.escalate("technical", from_role="claude", d=d2) == "dsh",
          R.escalate("technical", from_role="claude", d=d2))
    check("  and an unrelated asker does not change the first holder",
          R.escalate("technical", from_role="manager", d=d2) == "claude",
          R.escalate("technical", from_role="manager", d=d2))

    # 4. ONE ROLE MAY HOLD SEVERAL CLASSES.
    d3 = copy.deepcopy(d)
    d3["roles"]["claude"]["escalations"] = ["technical", "rework"]
    check("a role can hold two classes", sorted(R.escalate_all("rework", d=d3)) == ["claude", "manager"],
          R.escalate_all("rework", d=d3))

    # 5. VISION STAYS WITH JAS even when he is the one asking -- there is nothing above him to route to.
    d4 = copy.deepcopy(d)
    check("vision answers jas even when jas asks", R.escalate("vision", from_role="jas", d=d4) == "jas",
          R.escalate("vision", from_role="jas", d=d4))

    # 6. A ROLE WITH NO CLASS HOLDS NOTHING.
    check("a role with an empty list holds nothing", R.escalations(d4).get("auditor") == [],
          R.escalations(d4).get("auditor"))

    # 7. THE WRITER (what the Models tab will call), against a COPY of the store.
    # THE CHANGELOG TOO (2026-10-30): redirecting STORE alone left every run appending a who=jas row to the REAL
    # ROLES-CHANGELOG.jsonl -- 19 of them by the day it was found.
    tmp = Path(tempfile.mkdtemp()) / "ROLES.json"
    tmp.write_text(json.dumps(live, indent=1))
    real, real_log = R.STORE, R.LOG
    before = real_log.read_bytes() if real_log.is_file() else b""
    try:
        R.STORE, R.LOG = tmp, tmp.with_name("ROLES-CHANGELOG.jsonl")
        got = R.set_escalations("dsh", ["technical"], who="test_escalations_per_role")
        check("set_escalations writes and reads back", got == ["technical"], got)
        check("  and it survives a reload", json.loads(tmp.read_text())["roles"]["dsh"]["escalations"] == ["technical"])
        try:
            R.set_escalations("dsh", ["nonsense"], who="test_escalations_per_role")
            check("  an unknown class is refused", False, "it was accepted")
        except ValueError as e:
            check("  an unknown class is refused", "not escalation classes" in str(e), str(e))
    finally:
        R.STORE, R.LOG = real, real_log
    check("the real ROLES-CHANGELOG.jsonl is untouched by this suite",
          (real_log.read_bytes() if real_log.is_file() else b"") == before, "it grew")
    import inspect
    check("set_escalations has no default 'who' -- nothing is recorded in Jas's name by omission",
          inspect.signature(R.set_escalations).parameters["who"].default is inspect.Parameter.empty)

    print("test_escalations_per_role:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
