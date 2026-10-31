#!/usr/bin/env python3
"""Fail-first oracle (t1495): task-sessions writers redact secret-shaped strings before writing to disk.

Defect: scripts/task_sessions.py start() and append() serialize their row with json.dumps
and write it straight to disk, so any secret-shaped value in the row persists in
workspace/task-sessions/**/*.jsonl (the t1335/t1461 leaks). The fix redacts with
check_workspace.SECRET_PATTERN before the write. This oracle drives both writers with a
fake sk-ant- key and asserts the file on disk carries no sk-ant- string and does carry the
redaction marker.

Run: python3 scripts/tests/test_task_sessions_redaction.py
"""
import sys
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import task_sessions

MARKER = "[redacted:secret-pattern]"
FAKE_KEY = "sk-ant-" + "A" * 30   # matches check_workspace.SECRET_PATTERN (sk-ant- + 20+ chars)


def test_start_redacts():
    with tempfile.TemporaryDirectory() as tmp:
        path = task_sessions.start("t9999", "model-x", "purpose",
                                   root=tmp, brief=FAKE_KEY, role="coder")
        text = Path(path).read_text(encoding="utf-8")
    assert "sk-ant-" not in text, f"start() wrote a secret-shaped string to disk: {text[:200]}"
    assert MARKER in text, f"start() redacted nothing; file was: {text[:200]}"
    row = json.loads(text.strip().splitlines()[0])
    assert FAKE_KEY not in json.dumps(row)


def test_append_redacts():
    with tempfile.TemporaryDirectory() as tmp:
        path = task_sessions.start("t9999", "model-x", "purpose", root=tmp)
        task_sessions.append(path, "prompt", text=FAKE_KEY)
        text = Path(path).read_text(encoding="utf-8")
    lines = [json.loads(l) for l in text.strip().splitlines()]
    prompt_row = [r for r in lines if r.get("kind") == "prompt"][0]
    assert FAKE_KEY not in json.dumps(prompt_row), "append() wrote the secret-shaped string verbatim"
    assert MARKER in text, "append() redacted nothing"
    assert "sk-ant-" not in text, "append() left a secret-shaped string on disk"


TESTS = [test_start_redacts, test_append_redacts]


if __name__ == "__main__":
    failed = 0
    for t in TESTS:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    if failed:
        print(f"\n{failed} of {len(TESTS)} failed")
        sys.exit(1)
    print(f"\nall {len(TESTS)} passed")
