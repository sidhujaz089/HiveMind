#!/usr/bin/env python3
"""restart_stale.py: a service is stale when ANY repository module it imports is newer than it.

2026-09-30: only the main script was compared, so the Hub kept serving the old Flow chart for an
hour after flow_layout.py (imported inside a function) changed. Fixture files only.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[2] / "scripts")]
import restart_stale as rs  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def main():
    with tempfile.TemporaryDirectory() as tmp:
        app, lib = Path(tmp) / "app", Path(tmp) / "lib"
        app.mkdir()
        (lib / "pkg").mkdir(parents=True)
        (app / "server.py").write_text("import json\nimport helper\n\ndef page():\n    import drawing\n")
        (app / "helper.py").write_text("from pkg import core\n")
        (lib / "drawing.py").write_text("x = 1\n")
        (lib / "pkg" / "__init__.py").write_text("")
        (lib / "pkg" / "core.py").write_text("import deep\n")
        (lib / "deep.py").write_text("")
        got = sorted(p.name for p in rs.code_files(app / "server.py", roots=[lib]))
        report("its code is the script plus every repository module it imports, transitively",
               got, ["__init__.py", "core.py", "deep.py", "drawing.py", "helper.py", "server.py"])
        report("  the standard library is not its code", "json.py" in got, False)
        old = time.time() - 3600
        for f in rs.code_files(app / "server.py", roots=[lib]):
            os.utime(f, (old, old))
        started = time.time() - 60
        report("a service started after all its code is not stale",
               rs.newest(app / "server.py", roots=[lib])[0] <= started, True)
        (lib / "drawing.py").write_text("x = 2\n")     # imported INSIDE a function
        m, f = rs.newest(app / "server.py", roots=[lib])
        report("a change to a module imported inside a function makes it stale, and names the file",
               (m > started, f.name), (True, "drawing.py"))
        (app / "broken.py").write_text("import helper\ndef (:\n")
        report("a file mid-edit does not stop the scan", len(rs.code_files(app / "broken.py", roots=[lib])) >= 1, True)
    real = rs.code_files(rs.SAFE["app-feedback.service"])
    report("on the real Hub, flow_layout.py is part of its code",
           any(p.name == "flow_layout.py" for p in real), True)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
