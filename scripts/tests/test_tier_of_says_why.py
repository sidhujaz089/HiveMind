#!/usr/bin/env python3
"""tier_of's legacy fallback must SAY why it fired (DS, lanes 6858c4, correction 2; Claude, 2026-10-29).

A bare `except: return {legacy map}` silently restored the role names the refactor removed. Make
role_store unimportable and check the NOTE reaches stderr; delete the print and this goes red.
"""
import contextlib
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "app-repo"))
import taskstore as ts  # noqa: E402

sys.modules["role_store"] = None          # `import role_store` now raises ImportError
err = io.StringIO()
with contextlib.redirect_stderr(err):
    got = ts.tier_of("jas")
ok = got == "vision" and "fell back to the legacy map" in err.getvalue()
print(f"  {'ok ' if ok else 'BAD'}  fallback answers vision AND says why (got {got!r}, stderr {err.getvalue()!r})")
sys.exit(0 if ok else 1)
