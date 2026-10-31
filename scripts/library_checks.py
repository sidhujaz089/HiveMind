#!/usr/bin/env python3
"""The compliance checks every registered script must pass -- one per defect this repo has paid for.

Jas, 2026-09-14: "Every time a script gets registered a routine or model meets to check out to
make sure it doesn't have any of the issues we have had in the past, like truncation ... if a
new issue is discovered a check can be created and all scripts are registered for compliance."

HOW IT IS USED (scripts/library.py):
  register   runs every check on the file. A BLOCK finding refuses registration unless the
             registrant records a reason with --accept "why". WARN findings are recorded.
  verify --staged (pre-commit)  runs the BLOCK checks on every staged script and refuses only
             findings that are NEW relative to HEAD -- a ratchet, so an old violation in a file
             does not stop an unrelated edit, and a new one cannot get in.
  audit      runs every check on every registered script and writes
             workspace/LIBRARY-COMPLIANCE.md. ADDING A CHECK IS: append it to CHECKS with its
             evidence, then `library.py audit` -- every script is re-checked against it.
The MODEL check (Gemma reading for truncation a regex cannot judge) is asynchronous:
scripts/truncation_review.py reviews any script whose content it has not seen, on a timer.

Checks read CODE ONLY (signature_gate.code_only blanks docstrings and comments), because prose
about a defect is not the defect. This module is exempt from its own checks: its patterns are
string literals describing the defects, the same reason jsonx.py is exempt from the gate.
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
SELF = "scripts/library_checks.py"


def _code(text):
    try:
        import signature_gate
        return signature_gate.code_only(text)
    except Exception:                                               # noqa: BLE001
        return text


def _grep(pattern, skip_display=False):
    rx = re.compile(pattern)
    disp = re.compile(r"\b(print|say|log|echo)\s*\(")

    def run(text):
        out = []
        for i, line in enumerate(_code(text).splitlines(), 1):
            if skip_display and disp.search(line):
                continue
            if rx.search(line):
                out.append((i, line.strip()))
        return out
    return run


def _truncates_record(text):
    import signature_gate as g
    out = []
    for i, line in enumerate(_code(text).splitlines(), 1):
        if not g.DISPLAY.search(line) and g.line_truncates_a_record(line):
            out.append((i, line.strip()))
    return out


def _swallowed(text):
    lines = _code(text).splitlines()
    out = []
    for i, line in enumerate(lines):
        if re.match(r"\s*except\b.*:\s*$", line) and i + 1 < len(lines) \
                and re.match(r"\s*(pass|continue)\s*(#.*)?$", lines[i + 1]):
            out.append((i + 1, f"{line.strip()} {lines[i + 1].strip()}"))
    return out


def _model_outside_contract(text):
    code = _code(text)
    if ("/api/chat" in code or "/api/generate" in code) and "settings.request" not in code \
            and "settings.options" not in code:
        return [(i, l.strip()) for i, l in enumerate(code.splitlines(), 1)
                if "/api/chat" in l or "/api/generate" in l][:50]
    return []


def _no_timeout(text):
    out = []
    code = _code(text)
    for m in re.finditer(r"\b(urlopen|subprocess\.run|subprocess\.check_output)\(", code):
        depth, j = 0, m.end() - 1
        while j < len(code):
            if code[j] == "(":
                depth += 1
            elif code[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if "timeout" not in code[m.start():j]:
            out.append((code.count("\n", 0, m.start()) + 1, code[m.start():j + 1].split("\n")[0].strip()))
    return out


def _unguarded_main(text):
    code = _code(text)
    if re.search(r"^def main\(", code, re.M) and re.search(r"^(sys\.exit\(|raise SystemExit\()?main\(", code, re.M) \
            and "__name__" not in code:
        return [(0, "main() is called at import time with no __main__ guard")]
    return []


# id, severity, since, evidence, applies-to suffixes, function
CHECKS = [
    ("truncates-record", "block", "2026-09-08",
     "A fixed-width slice stored into a record (`parse_error = answer[:400]`) made five model "
     "failures undiagnosable. signature_gate.py.", (".py",), _truncates_record),
    ("greedy-extract", "block", "2026-09-08",
     "re.search(r'{.*}') merged two JSON objects into one wrong answer; use jsonx.one.",
     (".py",), lambda t: [(0, "greedy {.*} extractor")] if __import__("signature_gate").GREEDY.search(_code(t)) else []),
    ("secret", "block", "2026-08-16",
     "Secrets never enter git; the live key stays in ~/.secrets. check_workspace SECRET_PATTERN.",
     (".py", ".sh"), _grep(r"sk-ant-[A-Za-z0-9_\-]{20,}|AIza[0-9A-Za-z_\-]{35}|ghp_[A-Za-z0-9]{36}|-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("process-match-by-name", "block", "2026-09-07",
     "L-105: matching processes by command-line text finds the shell doing the search; a kill by "
     "name took out its own caller and "
     "dropped a git merge.", (".py", ".sh"), _grep(r"\bp(grep|kill)\s+(-\w+\s+)*-f\b|[\"']p(grep|kill)[\"']\s*,\s*[\"']-f")),
    ("ps-command-column", "block", "2026-09-15",
     "Without a terminal ps cuts lines at 80 columns; inference_health counted two live dispatches "
     "as zero and called a busy box down. Read /proc.", (".py", ".sh"),
     _grep(r"[\"']ps[\"']\s*,\s*[\"']-eo[\"']\s*,\s*[\"'][^\"']*\b(cmd|args|comm)\b|\bps\s+-eo\s+\S*\b(cmd|args|comm)\b")),
    ("silent-content-slice", "block", "2026-09-14",
     "Jas: remove truncation on the coding side. s[:5000] of a task file made the scoper reject "
     "tasks for information past the cut. Use loudcap.cap, or pass it whole.", (".py",),
     _grep(r"(read_text\([^)]*\)|\b(content|prompt|raw|reply|body|brief|output|stdout|stderr|text|answer|thinking))\s*\[\s*:\s*\d{3,}\s*\]", skip_display=True)),
    ("swallowed-exception", "block", "2026-09-09",
     "RC-1, absence read as success: `except: pass/continue` turns a failure into silence.",
     (".py",), _swallowed),
    ("model-call-outside-contract", "block", "2026-08-26",
     "docs/skills/local-model-loading: never build an options dict; call settings.request. Hand-built "
     "bodies sent parameters one backend drops silently (UNPARSEABLE x7).", (".py",), _model_outside_contract),
    ("no-timeout", "block", "2026-09-11",
     "A call with no timeout waits forever on a wedged slot; four dispatches hung 2026-09-11/12.",
     (".py",), _no_timeout),
    ("fixed-tmp-path", "block", "2026-09-14",
     "coder.py's shared /tmp/coder-live.log was truncated by every new lane, wiping a running one.",
     (".py", ".sh"), _grep(r"[\"']/tmp/[\w.\-]+[\"']")),
    ("unguarded-main", "block", "2026-09-08",
     "test_import_is_free: importing a script started a model run. Guard main with __name__.",
     (".py",), _unguarded_main),
]


# NAMED EXEMPTIONS, each with its reason. A detector has to contain the pattern it detects, as a
# string or in a message -- the first audit flagged selfmatch.py, the process-wait hook and
# mutate.py for "using" pgrep -f, when each exists to catch it. Listed here rather than inferred,
# so an exemption is a decision someone can read and revoke.
EXEMPT = {
    ("scripts/selfmatch.py", "process-match-by-name"): "detector: its fixtures and messages quote the pattern",
    ("scripts/hooks/guard_process_waits.py", "process-match-by-name"): "detector: its refusal message quotes the pattern",
    ("scripts/hooks/guard_process_waits.py", "ps-command-column"): "detector: its message shows the safe one-shot form",
    ("scripts/mutate.py", "process-match-by-name"): "mutation harness: injects the defect into copies on purpose",
    ("scripts/mutate.py", "truncates-record"): "mutation harness: injects the defect into copies on purpose",
    ("projects/local-inference/infer.py", "model-call-outside-contract"): "the transport (docs/ENGINES.md): it sends bodies its callers built with settings.request, and builds none itself",
    ("scripts/check_stale_services.py", "process-match-by-name"): "its [STUCK] message prints the bad form as a warning; line 203 is reviewed separately",
}
# TEST SUITES AND ORACLE HELPERS ARE NOT CHECKED. A fixture that proves a check fails has to
# contain the defect; checking it would refuse every oracle this repository is built on.
UNCHECKED_KINDS = ("test", "oracle-helper")


def _no_stop_reason(text):
    code = _code(text)
    # Only calls that GENERATE text can be cut off. The first version matched the port number,
    # so a page that merely lists models (/api/tags, /api/show) was refused for not reading a
    # stop reason that listing calls never have -- the Corpus tab, 2026-09-15.
    if re.search(r"/api/(chat|generate)", code) and not re.search(r"done_reason|finish_reason", code):
        return [(i, l.strip()) for i, l in enumerate(code.splitlines(), 1)
                if re.search(r"/api/(chat|generate)", l)][:20]
    return []


CHECKS += [("model-run-no-stop-reason", "block", "2026-09-15",
               "t238: ask() never read done_reason, so a reply cut off at the token limit was "
               "reported as garbage and the reasoning thrown away; two lanes spent hours on it.",
               (".py",), _no_stop_reason)]

# WHY EVERY CHECK BLOCKS. Jas, 2026-09-14: "Why don't we block the warning scripts?" The reason
# for warn was that 247 existing findings would stop the repository cold. The pre-commit ratchet
# removes that reason: only findings NEW relative to HEAD are refused, and a registration with a
# genuine exception records it with --accept "why". So a new instance of any known defect cannot
# land, and old ones are worked off as compliance chunks instead of blocking unrelated edits.

# MODEL CHECKS -- defects a pattern cannot judge. They are put to the auditor model (Gemma) by
# scripts/truncation_review.py whenever a script is registered or changed, and each finding it
# returns names one of these ids.
MODEL_CHECKS = [
    ("truncation", "Content shortened so a shortened thing could look complete (slices, head/tail, "
     "max_chars, capped reads) that is SILENT -- nothing records how much was dropped.",
     "Jas 2026-09-14: remove truncation on the coding side; a cap must declare what it dropped."),
    ("path-as-text", "A value that can be either a PATH or literal TEXT is accepted without telling "
     "them apart, so a path silently becomes the content (e.g. an argument that is the task text).",
     "t263: coder.py --brief took a file path as the whole task; two lanes lost six GPU-hours."),
    ("import-time-path", "A file or directory path computed once at import and used later for writes, "
     "so a test that redirects the module's other paths still writes the REAL file.",
     "2026-09-14: RESOLVED.md was fixed at import; a fixture overwrote the live file with an empty one."),
    ("absence-as-success", "A check or scan that reports success, 'nothing found' or 'all clear' when it "
     "actually could not look -- missing file, empty input, failed command, zero items scanned.",
     "RC-1 and L-146: absence read as success; a status page trusted a heartbeat that had stopped."),
    ("unlocked-shared-write", "A file written or rewritten by a process that could run concurrently with "
     "another writer, with no lock and no atomic replace.",
     "2026-09-14: concurrent lanes clobbered one live log; taskstore needed a lock to stop lost notes."),
    ("work-lost-on-retry", "Expensive work done in units (per file, per suite, per chunk) with nothing "
     "recorded as each unit completes, in a script that can be interrupted -- so a retry after a kill or "
     "a failure starts again from zero. NOT a finding when repeating the work is cheap: a checkpoint that "
     "never pays for itself is its own defect.",
     "Jas 2026-09-16: 'when it fails and retries does it lose the work that was done? If so it, and all "
     "routines, need the ability to pick up where they failed.' run_tests.py ran all 149 suites from "
     "scratch on every retry -- grep for cache|checkpoint|resume returned 0 -- and seven kills across "
     "09-15/09-16 re-ran already-green suites for ~80 minutes. Fixed in 96066e8; see t329."),
]


def run(rel, text, only=None, severity=None, kind=None):
    """[(check_id, severity, line, code)] for one script."""
    if rel == SELF or kind in UNCHECKED_KINDS or "/tests/" in f"/{rel}":
        return []
    out = []
    for cid, sev, _since, _ev, suffixes, fn in CHECKS:
        if (only and cid not in only) or (severity and sev != severity) or not rel.endswith(suffixes):
            continue
        if (rel, cid) in EXEMPT:
            continue
        try:
            for line, code in fn(text):
                out.append((cid, sev, line, code))
        except Exception as e:                                      # noqa: BLE001
            out.append((cid, "block", 0, f"the check itself failed: {type(e).__name__}: {e}"))
    return out


def new_blocks(rel, before, after):
    """[(check_id, line, code)] -- BLOCK findings `after` has that `before` did not.

    THE ONE DEFINITION OF "a change the commit gate refuses". library.py's pre-commit ratchet
    and coder.py's attempt loop both call this. Until 2026-09-23 only the commit gate ran it,
    and only AFTER coder.py had accepted an attempt, so the model never saw the finding: 31
    runs ended ACCEPTED-NOT-WRITTEN -- good work dropped at the door, the reason written to a
    log the model never reads. Compared by (check, code) and not by line, so moving an old
    violation is not a new one. `before` is None for a file that did not exist."""
    old = {(c, code) for c, _s, _l, code in run(rel, before, severity="block")} \
        if before is not None else set()
    return [(c, line, code) for c, _s, line, code in run(rel, after, severity="block")
            if (c, code) not in old]
