#!/usr/bin/env python3
"""p17: the hold box becomes a menu -- Hold for Jas, Hold for Claude, or nothing.

Jas, 2026-09-14: "I want a box that works the same as hold, but it's for you and not me ...
Actually it should be a drop menu for hold. I can choose hold for Jas or Claude or nothing."
Hold is the task's `needs` field: jas puts it in his Review tab, claude puts it in Claude's
queue, empty releases it. Anything else from the form is refused to empty, never passed through."""
import re
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {str(got)[:60]}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {str(got)[:60]}")


def code_of(rel):
    return "\n".join(l for l in (ROOT / rel).read_text().splitlines() if not l.strip().startswith("#"))


sys.path.insert(0, str(ROOT / "projects" / "app-repo"))
sys.argv = ["x"]
import feedback as F  # noqa: E402

report("hold_value exists", callable(getattr(F, "hold_value", None)), True)
report("hold_select exists", callable(getattr(F, "hold_select", None)), True)
if callable(getattr(F, "hold_value", None)):
    # 2026-10-03: there is no "nobody" -- anything that is not a role is Production.
    for raw, want in (("jas", "jas"), ("claude", "claude"), ("", "production"), ("1", "production"), ("root", "production"), (None, "production")):
        report(f"hold_value({raw!r})", F.hold_value(raw), want)
if callable(getattr(F, "hold_select", None)):
    h = F.hold_select("claude")
    report("renders a select named hold", '<select name="hold"' in h, True)
    # UPDATED 2026-09-30 to Jas's plan 3b (2026-09-29): "Assign to:" replaced "Hold for Jas / Hold for
    # Claude" and lists EVERY role from the role store -- nobody, then Claude, Jas, then the rest
    # alphabetically. The three-option menu this pinned no longer exists by his decision.
    import json as _json
    roles = _json.loads((ROOT / "projects" / "local-inference" / "models" / "ROLES.json").read_text())["roles"]
    # UPDATED 2026-10-07 (Jas: "I shouldn't see anything else in the assign to list ... dynamic when
    # roles have that task or not"): only roles with an assigned_* task, plus Production.
    takers = [k for k, r in roles.items() if any(str(t.get("task", "")).startswith("assigned_")
                                                 for t in r.get("tasks", []))]
    report("one option per role that takes assigned work, plus Production", h.count("<option"), len(takers) + 1)
    report("a role without an assigned task is not offered",
           all(f'value="{k}"' not in h for k in roles if k not in takers), True)
    report("titled Assign to:", h.startswith("Assign to:"), True)
    report("offers Jas and Claude by name", ('>Jas<' in h, '>Claude<' in h), (True, True))
    order = re.findall(r'<option value="([^"]*)"', h)
    # THE ORG CHART (Jas, 2026-10-19) replaced "Production, Claude, Jas, then the rest": Production,
    # then every role that takes assigned work in its rank order on ROLES.json.
    report("in order: Production, then the takers in org-chart rank", order, ["production"] + takers)
    report("the current value is selected", re.search(r'value="claude"[^>]*selected', h) is not None, True)
    report("and only the current value", re.search(r'value="jas"[^>]*selected', h) is None, True)
    report("empty current selects Production (2026-10-03: blank IS Production)",
           re.search(r'value="production"[^>]*selected', F.hold_select("")) is not None, True)
src = code_of("projects/app-repo/feedback.py")
report("no hold checkbox remains", 'type="checkbox" name="hold"' in src, False)
report("every form uses the menu (3 forms)", src.count("hold_select(") >= 4, True)
report("the note handler no longer maps a tick to jas", 'needs="jas" if one("hold")' in src, False)
report("handlers store the chosen value", src.count('hold_value(one("hold"))') >= 2, True)

print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
