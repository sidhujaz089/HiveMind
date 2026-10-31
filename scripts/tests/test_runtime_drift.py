#!/usr/bin/env python3
"""t655: the workspace notices when an interpreter or one of its own services has drifted.

2026-10-02 an Ubuntu update moved python3 from 3.12 to 3.14 and removed 3.12; both venvs symlink /usr/bin/python3, so
every package became invisible and the Chat bridge crash-looped for hours. Hermetic: fixture repos with a fake
interpreter script, and a stubbed list of failed units."""
import io
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from check_workspace import Checker  # noqa: E402

FAILS = []


def ck(name, cond, detail=""):
    print(f"  [{'ok' if cond else 'BAD'}]  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILS.append(name)


def fixture(cfg_version, runs_as, flow_units=("workspace-mcp.service",)):
    d = Path(tempfile.mkdtemp(prefix="drift-"))
    v = d / ".venv"
    (v / "bin").mkdir(parents=True)
    (v / "pyvenv.cfg").write_text(f"home = /usr/bin\nversion = {cfg_version}\n")
    py = v / "bin" / "python3"
    py.write_text(f"#!/bin/sh\necho Python {runs_as}\n" if runs_as else "#!/bin/sh\nexit 1\n")
    py.chmod(0o755)
    f = d / "projects" / "local-inference" / "models"
    f.mkdir(parents=True)
    import json
    (f / "FLOW.json").write_text(json.dumps({"services": [{"id": "x", "unit": u} for u in flow_units]}))
    return d


def run(d, failed=()):
    ck_ = Checker(d)
    ck_._failed_user_units = lambda: list(failed)
    buf = io.StringIO()
    with redirect_stdout(buf):
        ck_.check_runtime_drift()
    return ck_, buf.getvalue()


c, out = run(fixture("3.12.3", "3.14.4"))
ck("a venv built for 3.12 that now runs 3.14 is a FAIL", c.failures == 1 and c.passes == 0, out)
ck("  and names the venv, both versions and the rebuild command",
   all(x in out for x in (".venv", "3.12.3", "3.14.4", "python3 -m venv --clear")), out)
c, out = run(fixture("3.14.4", "3.14.7"))
ck("a patch-level change (3.14.4 -> 3.14.7) is not drift", c.passes == 1 and c.failures == 0, out)
c, out = run(fixture("3.14.4", None))
ck("an interpreter that does not run is a FAIL", c.failures == 1 and "does not run" in out, out)
c, out = run(fixture("3.14.4", "3.14.4"), failed=["workspace-mcp.service"])
ck("one of the workspace's own services failed is a WARN naming it",
   c.warnings == 1 and "workspace-mcp.service" in out, out)
c, out = run(fixture("3.14.4", "3.14.4"), failed=["gnome-software.service"])
ck("a desktop unit failing is not the workspace's business", c.passes == 1 and c.warnings == 0, out)
d = Path(tempfile.mkdtemp(prefix="drift-empty-"))
c, out = run(d)
ck("no venv at all is a SKIP, never a PASS", c.passes == 0 and c.failures == 0 and "SKIP" in out, out)

print("test_runtime_drift:", "FAILED" if FAILS else "ok")
sys.exit(1 if FAILS else 0)
