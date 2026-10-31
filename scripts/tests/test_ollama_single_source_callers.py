#!/usr/bin/env python3
"""t1089 item 2: every Ollama caller under projects/local-inference builds its request
through the single keep_alive source -- no raw urllib request to Ollama outside settings.py.

Evidence for the class (workspace/rca/a96a96448cf1.md, section 4-C): deliberate.py was the
instance t243 fixed (deliberate.py:184 calls settings.request, whose profiles set keep_alive,
settings.py:43 / :67 / :112), and the raw-urllib callers left behind are askmemory.py:125,
calibrate.py:78, chatui.py:636, coder.py:1092, codeval.py:121, console.py:1564, exam.py:102.
The lesson family is L-150: the fix written in one caller's comment (coder.py's 2026-08-27
keep_alive note) never reached the adjacent caller, and the HTTP 500 came back.

The rule is INVERTED, per docs/skills/planning-for-sustainment: the approved set is ONE file,
settings.py, which is the single source; every other file that speaks to Ollama with a raw
urllib request is a finding. There is no list of bad keywords to maintain and no librarian to
remember to add a term, so a failure nobody predicted surfaces the first time it happens.

The scan is text-level on purpose: one regex, no AST, no config. The rule is about WHICH FILE
builds the request, not about which expression builds it. The synthetic sources below carry a
timeout on every request call for the same reason the real callers must: a call with no timeout
is its own compliance defect (library_checks, no-timeout), and a fixture that models the class
must not model a second, unrelated one.

Red on main for the defect and for it alone: test_no_raw_ollama_request_outside_the_single_source
reports the raw callers the RCA lists. The other three tests pin THE SCAN ITSELF on synthetic
files, so a green suite is evidence that the scan can see the defect, not evidence that the
tree happens to be clean.

Run: python3 scripts/tests/test_ollama_single_source_callers.py  (pytest is not installed).
No model, no server, no subprocess, no network, no fixed temp path (tempfile only), nothing left
running; the real tree is only ever read. Do not invent a function, a module or a file path:
everything used here is defined in this file.
"""
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "projects" / "local-inference"
SINGLE_SOURCE = "settings.py"
# A file "speaks to Ollama" when its text carries the endpoint port, an api path, the word.
OLLAMA_MARKERS = ("11434", "api/chat", "api/generate", "api/tags", "api/show", "api/embed",
                  "ollama")
# Matches urlopen( / .urlopen( / Request( / .Request(  -- capital R on Request, so the
# legitimate call settings.request( (lowercase) is never a hit.
RAW_CALL = re.compile(r"(?:urlopen|Request)\s*\(")

FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def raw_ollama_callers(package_dir):
    """Every file under package_dir that talks to Ollama with a raw urllib request.

    A file is a finding when all three hold:
      (a) its text mentions Ollama -- the endpoint port, an api/... path, the word, any case;
      (b) it contains a raw urllib request site (RAW_CALL);
      (c) it is not the single source itself. settings.py builds the request on everyone's
          behalf, so it is the one exempt file. A file that ALSO imports settings is still a
          finding: the rule is one source per caller, not one call per source.

    Returns a sorted list of "relpath lines n, n, ..." strings, one per offending file, with
    every line number -- nothing is truncated. A file that cannot be read is reported as a
    finding naming the exception, because an unreadable file is not evidence of compliance.
    """
    findings = []
    for path in sorted(package_dir.rglob("*.py")):
        try:
            rel = str(path.relative_to(ROOT))
        except ValueError:          # synthetic packages built under a temp dir
            rel = path.name
        if path.name == SINGLE_SOURCE:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            findings.append(f"{rel}: unreadable: {exc}")
            continue
        low = text.lower()
        if not any(m in low for m in OLLAMA_MARKERS):
            continue
        hits = [i + 1 for i, line in enumerate(text.splitlines()) if RAW_CALL.search(line)]
        if hits:
            findings.append(f"{rel} lines " + ", ".join(str(n) for n in hits))
            print("  finding: " + findings[-1])
    return findings


# Synthetic sources: RAW_CHAT is the shape the class forbids, ROUTED is what the fix looks
# like, OTHER is a plain request to a non-Ollama host, which must NOT be flagged.
RAW_CHAT = ("import urllib.request\n"
            "url = 'http://127.0.0.1:11434/api/chat'\n"
            "resp = urllib.request.urlopen(url, timeout=30)\n")
ROUTED = ("import settings\n"
          "resp = settings.request('chat', {'model': 'm', 'messages': []})\n")
OTHER = ("import urllib.request\n"
         "resp = urllib.request.urlopen('https://example.com/tags', timeout=30)\n")


def _pkg(files):
    """A throwaway package dir holding the given {name: source} files."""
    tmp = Path(tempfile.mkdtemp(prefix="t1089-"))
    for name, body in files.items():
        target = tmp / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return tmp


def test_no_raw_ollama_request_outside_the_single_source():
    """The durable half: nothing under projects/local-inference builds a raw Ollama request.
    RED on main -- that is the point: askmemory/calibrate/chatui/coder/codeval/console/exam.
    It turns green as each caller is routed through settings.request."""
    if not PKG.is_dir():
        check(False, f"the scanned package exists: {PKG}")
        return
    findings = raw_ollama_callers(PKG)
    if findings:
        print(f"  {len(findings)} file(s) build a raw Ollama request (full list, nothing cut):")
        for item in findings:
            print("    " + item)
    check(not findings,
          "no file under projects/local-inference builds a raw Ollama request "
          f"[{len(findings)} offender(s) listed above, none truncated]")


def test_the_scan_reports_a_raw_caller_it_is_given():
    pkg = _pkg({"asker.py": RAW_CHAT})
    found = raw_ollama_callers(pkg)
    check(any(f.startswith("asker.py") for f in found),
          f"a raw urllib call to 11434/api/chat is reported [scan returned {found!r}]")


def test_the_scan_exempts_only_the_single_source():
    pkg = _pkg({"settings.py": RAW_CHAT, "routed.py": ROUTED, "asker.py": RAW_CHAT})
    found = raw_ollama_callers(pkg)
    check(any(f.startswith("asker.py") for f in found),
          "a raw caller sitting next to the single source is still reported "
          f"[scan returned {found!r}]")
    check(not any(f.startswith("settings.py") for f in found),
          f"settings.py is the one exempt file, the single source [scan returned {found!r}]")
    check(not any(f.startswith("routed.py") for f in found),
          f"a caller that goes through settings.request is clean [scan returned {found!r}]")


def test_the_scan_does_not_flag_a_non_ollama_request():
    pkg = _pkg({"fetcher.py": OTHER})
    found = raw_ollama_callers(pkg)
    check(not found,
          f"a plain request to a non-Ollama host is not a finding [scan returned {found!r}]")


if __name__ == "__main__":
    for fn in (test_no_raw_ollama_request_outside_the_single_source,
               test_the_scan_reports_a_raw_caller_it_is_given,
               test_the_scan_exempts_only_the_single_source,
               test_the_scan_does_not_flag_a_non_ollama_request):
        print(f"--- {fn.__name__}")
        fn()
    print(f"\n{len(FAILS)} failure(s)")
    sys.exit(1 if FAILS else 0)
