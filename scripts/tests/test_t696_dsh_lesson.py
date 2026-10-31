#!/usr/bin/env python3
"""t696 oracle: docs/LESSONS.md carries the dsh-local-ai lesson.

Lesson (task title, verbatim): "regenerating the patch layer can
silently re-drop the context/output/idle settings". The audit promise
(audit s15 M2; dsh triage 2026-10-06, rows at :25/:332/:358) was to
"record a LESSONS.md entry"; it was "promised, never written". This
oracle is RED on main because that entry is absent from docs/LESSONS.md;
it goes green when the entry names the adapter (dsh), all three DISTINCT
settings (context, output, idle), the trigger (regenerating the patch
layer), and the effect (silently re-drop).

Run:  python3 scripts/tests/test_t696_dsh_lesson.py
"""
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LESSONS = ROOT / "docs" / "LESSONS.md"


def _text():
    assert LESSONS.is_file(), f"missing {LESSONS}"
    return LESSONS.read_text(encoding="utf-8", errors="replace").lower()


def test_lessons_file_exists():
    assert LESSONS.is_file(), f"missing {LESSONS}"


def test_lesson_is_dsh_local_ai():
    assert "dsh" in _text(), "no mention of the dsh-local-ai adapter"


def test_lesson_names_all_three_settings():
    t = _text()
    assert "context" in t, "missing the 'context' setting"
    assert "output" in t, "missing the 'output' setting"
    assert "idle" in t, "missing the 'idle' setting"


def test_lesson_records_trigger_and_effect():
    t = _text()
    assert "patch" in t, "no mention of the patch layer"
    assert "regenerat" in t, "no mention of regenerating the patch layer"
    assert ("re-drop" in t) or ("redrop" in t) or ("re drop" in t), (
        "no mention of the silent re-drop of the settings"
    )


def _main():
    fns = [
        test_lessons_file_exists,
        test_lesson_is_dsh_local_ai,
        test_lesson_names_all_three_settings,
        test_lesson_records_trigger_and_effect,
    ]
    failures = []
    for fn in fns:
        try:
            fn()
        except AssertionError as e:
            failures.append(f"{fn.__name__}: {e}")
        except Exception:
            failures.append(f"{fn.__name__}: CRASH")
            traceback.print_exc()
    if failures:
        print("FAIL")
        for f in failures:
            print("  -", f)
        raise SystemExit(1)
    print(f"ok {len(fns)} test(s)")


if __name__ == "__main__":
    _main()
