#!/usr/bin/env python3
"""IDENTITY IS PER-INSTANCE: both DSHs run their commands against this one repository (t7625).

The bug this suite exists for, found by the GX10 DSH on 2026-10-26: `workspace/identity.json` said
`"instance": "ThinkPad DSH"`, and `session_start.py` printed that to **both** instances. The GX10 DSH
read it, saw a claim about a machine it was not on, and correctly refused to believe it -- which is
the right behaviour and a wasted session. A single-instance file cannot serve two instances that share
a checkout.

**Hostname cannot tell them apart.** For the ThinkPad DSH the harness is on the ThinkPad but every
command lands on the GX10, so `hostname` answers `HiveMind` in both sessions. `DSH_HOME` can: the
harness exports it into the shell it spawns, and it is `C:\\Users\\user\\.dsh` on the ThinkPad and
`/home/user/.dsh` on the GX10.

What this refuses:

  * the file going back to one top-level instance -- the exact regression;
  * an entry missing the facts a session needs, because "model: present but blank" reads as an answer;
  * a window that is neither a positive integer nor an explicit null WITH a source, so nobody has to
    guess whether a missing number means "unknown" or "zero";
  * resolution that guesses: an unfamiliar or absent DSH_HOME must produce a problem, never another
    instance's facts;
  * the print being dropped, moved after section 0, or silently skipped when resolution fails.

Hermetic: reads the repository's own files and calls the resolver with an injected env.
"""
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import session_start as ss  # noqa: E402

IDENT = ROOT / "workspace" / "identity.json"
START = ROOT / "scripts" / "session_start.py"
REQUIRED = ("instance", "model", "harness_host", "shell", "workspace",
            "context_window_from", "context_window_source")
FAILS = []


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


def main():
    doc = json.loads(IDENT.read_text(encoding="utf-8"))
    inst = doc.get("instances") or {}

    # 1. TWO ENTRIES, and the file is not a single-instance claim any more. This is the regression.
    check("identity.json carries an instances map, not one top-level instance",
          isinstance(inst, dict) and "instance" not in doc, list(doc)[:5])
    for key in ("thinkpad-dsh", "gx10-dsh"):
        check(f"{key} is present", key in inst, sorted(inst))

    # 2. Each entry carries the facts, and they are not the same entry copied twice.
    for key, e in inst.items():
        missing = [k for k in REQUIRED if not e.get(k)]
        check(f"{key} names every fact a session needs", not missing, missing)
        win = e.get("context_window_tokens")
        check(f"{key}'s window is an integer or an explicit null with a source",
              (isinstance(win, int) and win > 0) or (win is None and "context_window_source" in e), win)
    check("the two instances are not the same entry",
          inst.get("thinkpad-dsh", {}).get("instance") != inst.get("gx10-dsh", {}).get("instance"),
          "both entries name the same instance -- a copy, not a resolution")

    # 3. RESOLUTION, two ways. DSH_HOME is the only discriminator available.
    k, e, prob = ss.resolve_identity({"DSH_HOME": "C:\\Users\\user\\.dsh"})
    check("a Windows DSH_HOME resolves to the ThinkPad",
          (k, e.get("instance"), prob) == ("thinkpad-dsh", "ThinkPad DSH", ""), (k, prob))
    k, e, prob = ss.resolve_identity({"DSH_HOME": "/home/user/.dsh"})
    check("a POSIX DSH_HOME resolves to the GX10",
          (k, e.get("instance"), prob) == ("gx10-dsh", "GX10 DSH", ""), (k, prob))

    # 4. IT DOES NOT GUESS. No DSH_HOME, or a home nobody has described, is a problem -- never a
    #    fallback to whichever entry happens to be first.
    for env in ({}, {"DSH_HOME": ""}, {"DSH_HOME": "X:/weird"}):
        k, e, prob = ss.resolve_identity(env)
        check(f"{env} is refused rather than guessed", (k, e, bool(prob)) == ("", {}, True),
              (k, e.get("instance")))

    # 5. An entry missing from the file is a problem naming the key, not a silent fallback.
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "identity.json"
        f.write_text(json.dumps({"instances": {"gx10-dsh": {"instance": "GX10 DSH"}}}), encoding="utf-8")
        k, e, prob = ss.resolve_identity({"DSH_HOME": "C:\\x"}, path=f)
        check("an instance with no entry says so by name", "thinkpad-dsh" in prob, prob)

    # 6. The print happens, names the RESOLVED instance, and comes before the first numbered section.
    src = START.read_text(encoding="utf-8", errors="replace")
    check("session_start resolves the identity by DSH_HOME",
          "DSH_HOME" in src and "resolve_identity" in src)
    check("the resolution failure is loud, not skipped", "IDENTITY UNRESOLVED" in src)
    i_id = src.find("resolve_identity()")
    i_hooks = src.find("# --- 0. Install git hooks")
    check("the identity prints before the first section",
          i_id != -1 and i_hooks != -1 and i_id < i_hooks, f"identity at {i_id}, hooks at {i_hooks}")

    # 7. THE PROMPT MUST NOT RESTATE THE FACTS EITHER (2026-10-27). The same defect one layer up: the
    #    DSH standing prompt named a model and a window per host, the GX10 moved to a hosted
    #    1,000,000-token route, and a session read its own 150,000 tokens as 92% of the LOCAL Swift's
    #    163,800 -- and stopped with 78% of its window unused. identity.json is the source and
    #    context_watch.py is the measurement; the prompt may say how to measure, never what the answer is.
    prompt = ROOT / "docs" / "dsh" / "STARTUP-PROMPT.md"
    text = prompt.read_text(encoding="utf-8", errors="replace") if prompt.is_file() else ""
    check("the DSH startup prompt exists where the harness copies it from", bool(text))
    check("the prompt tells a session to MEASURE its window", "context_watch.py" in text)
    stale = re.findall(r"[Cc]ontext window\s+[\d,]+\s*tokens", text)
    check("the prompt hardcodes no instance's window", not stale, stale)
    named = re.findall(r"Model (?:Swift|Q4|Q16) \(", text)
    check("the prompt names no model as the one this session is running", not named, named)

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
