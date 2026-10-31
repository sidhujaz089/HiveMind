#!/usr/bin/env python3
"""Oracle for t669.

session_start's console_reconcile section (scripts/session_start.py, the
`if rc.returncode == 1:` branch that re-prints the 'QWEN CLAIMED ACTIONS'
evidence) must collapse to ONE LINE PER THREAD instead of re-printing each
thread's full reply text. The per-thread summary already exists in
console_reconcile.py's stdout (the `  <thread>: N rows, H unbacked claim(s),
...` lines); the section keeps one line per thread and points to
`python3 scripts/console_reconcile.py` for detail (an existing pointer line
this change must not remove).

This oracle tests the NEW pure helper in isolation (the t276 idiom, see
test_session_start_errorscan.py): it takes the raw console_reconcile stdout
string (the same data the block already holds as rc.stdout) and returns the
collapsed section lines. Red on main because
session_start._render_console_reconcile does not exist yet; green once the
helper lands and behaves as specified.

Self-contained: no subprocess, no network, no /tmp, no model, no live server.
"""
import contextlib
import io
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]  # scripts/tests/ -> repo root
sys.path.insert(0, str(REPO / "scripts"))

import session_start  # noqa: E402  (must import cleanly, no side effects)

# A realistic console_reconcile stdout for TWO DISTINCT threads, each with a
# hard-finding block whose 'the reply said, in full:' section is the bloat.
# The per-thread summary lines are what the collapsed section keeps (one line
# per thread); the reply-text lines under 'the reply said, in full:' are what
# it drops.
SAMPLE_STDOUT = "\n".join([
    "  t124: 369 rows, 2 unbacked claim(s), 0 unverified mention(s)",
    "  smoke: 40 rows, 1 unbacked claim(s), 3 unverified mention(s)",
    "",
    "  UNBACKED CLAIM  [t124 2026-09-14T13:30:00Z]  pid-with-no-dispatch",
    "    pid 153120 was never returned by any dispatch anywhere in this thread",
    "    calls actually made in that turn: none",
    "    the reply said, in full:",
    "      Dispatched -- pid 153120.",
    "      I have successfully used the note_task tool ... Task ID: t124",
    "",
    "  UNBACKED CLAIM  [smoke 2026-09-13T09:00:00Z]  pid-with-no-dispatch",
    "    pid 998877 was never returned by any dispatch anywhere in this thread",
    "    calls actually made in that turn: none",
    "    the reply said, in full:",
    "      Dispatched pid 998877 -- Task Audit",
])

# The exact reply-text lines that are the bloat; a correct helper drops every one.
EVIDENCE_LINES = [
    "Dispatched -- pid 153120.",
    "I have successfully used the note_task tool ... Task ID: t124",
    "Dispatched pid 998877 -- Task Audit",
]

THREADS = ["t124", "smoke"]


def _render(sample):
    """Call the helper, tolerating return-list / return-str / print styles."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = session_start._render_console_reconcile(sample)
    if isinstance(out, str):
        text = out
    elif isinstance(out, (list, tuple)):
        text = "\n".join(str(x) for x in out)
    else:
        text = ""
    return text + "\n" + buf.getvalue()


def test_helper_exists():
    assert hasattr(session_start, "_render_console_reconcile"), \
        "session_start must define _render_console_reconcile"


def test_names_each_thread():
    text = _render(SAMPLE_STDOUT)
    for tid in THREADS:
        assert tid in text, f"must name thread {tid!r}"


def test_drops_evidence_reply_text():
    text = _render(SAMPLE_STDOUT).lower()
    for line in EVIDENCE_LINES:
        assert line.lower() not in text, \
            f"evidence reply text re-printed (this is the bloat): {line!r}"


def test_collapses_to_one_line_per_thread():
    text = _render(SAMPLE_STDOUT)
    nonempty = [l for l in text.splitlines() if l.strip()]
    # The sample carries 3 reply-text lines of evidence; a dump re-prints them.
    # A correct helper is far smaller than the evidence it must drop.
    assert len(nonempty) < len(EVIDENCE_LINES) + len(THREADS), \
        f"expected a collapse (one line per thread), got {len(nonempty)} lines"


def test_empty_input_is_safe():
    # A missing/empty stdout must not crash the section (records, no raise).
    text = _render("")
    assert isinstance(text, str)


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
        except Exception as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
        else:
            print(f"PASS {fn.__name__}")
    if failed:
        print(f"{failed} of {len(fns)} test(s) FAILED")
        sys.exit(1)
    print(f"OK ({len(fns)} tests passed)")
