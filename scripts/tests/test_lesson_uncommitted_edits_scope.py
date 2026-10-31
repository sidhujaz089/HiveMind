import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LESSONS = ROOT / "docs" / "LESSONS.md"

RULE_NEEDLES = ("uncommitted", "enrolled chunk")
TAG_NEEDLES = ("[VERIFIED", "[UNTESTED")
SOURCE_NEEDLES = ("t663", "s11")


def _entries(text):
    matches = list(re.finditer(r"(?m)^\*\*L-0?\d{2,4}\b", text))
    return [text[m.start(): (matches[i + 1].start() if i + 1 < len(matches) else len(text))]
            for i, m in enumerate(matches)]


def _ok(entries, needles):
    return any(all(n.lower() in e.lower() for n in needles) for e in entries)


def test_lesson_file_exists():
    assert LESSONS.is_file(), "docs/LESSONS.md is missing"


def test_entry_states_rule_is_tagged_and_cites_audit():
    entries = _entries(LESSONS.read_text(encoding="utf-8"))
    ok = any(
        all(n.lower() in e.lower() for n in RULE_NEEDLES)
        and any(t in e for t in TAG_NEEDLES)
        and any(s.lower() in e.lower() for s in SOURCE_NEEDLES)
        for e in entries
    )
    assert ok, ("no lesson entry in docs/LESSONS.md both states the rule "
                "('uncommitted' + 'enrolled chunk'), is tagged [VERIFIED]/[UNTESTED], and cites "
                f"the audit {SOURCE_NEEDLES}")


def test_entry_is_tagged():
    entries = _entries(LESSONS.read_text(encoding="utf-8"))
    assert _ok(entries, RULE_NEEDLES) and _ok(entries, TAG_NEEDLES), \
        "a rule entry is missing its [VERIFIED]/[UNTESTED] tag"


def test_entry_cites_the_audit():
    entries = _entries(LESSONS.read_text(encoding="utf-8"))
    assert _ok(entries, RULE_NEEDLES) and _ok(entries, SOURCE_NEEDLES), \
        "a rule entry is missing the audit citation (t663/s11)"


if __name__ == "__main__":
    fns = [test_lesson_file_exists, test_entry_states_rule_is_tagged_and_cites_audit,
           test_entry_is_tagged, test_entry_cites_the_audit]
    failures = []
    for fn in fns:
        try:
            fn()
        except AssertionError as exc:
            failures.append(f"{fn.__name__}: {exc}")
    if failures:
        for f in failures:
            print("FAIL:", f)
        raise SystemExit(1)
    print(f"ok: {len(fns)} test(s) passed")
