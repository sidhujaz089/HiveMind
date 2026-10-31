#!/usr/bin/env python3
"""A performance figure in prose must have a recorded measurement behind it.

WHY THIS EXISTS, AND EVERY EXAMPLE IS MINE. On 2026-09-01 I measured a model once, from cold,
got 11 tok/s, and quoted that number all day -- in a plan, in a handover, and as the baseline
a pre-flight check would have used to BLOCK candidates. Warm, the same model on the same box
does 35.7. I also asserted that two models running in parallel would split throughput, and
only measured it when Jas pushed back; it turned out to be 0.92x, so the conclusion held and
the reasoning did not. And I built an exam whose answer key was cut from code that already
contained the fix, so the key said DEFECT while the excerpt was correct.

Three failures, one shape: A NUMBER STATED WITHOUT ITS PROVENANCE. Not carelessness -- each
was true when measured, and each stopped being true the moment the conditions changed and
nobody had written the conditions down.

So: cite a figure, register it. `workspace/measurements.jsonl` records what was measured,
under what conditions, by what command, and when. This check reads the prose, finds the
figures, and fails on any that are not in the ledger.

DELIBERATELY NARROW. It looks only for performance-shaped figures -- tok/s, GB resident,
speedup multiples -- because those are the ones that have cost time. A check that flagged
every number in every document would be ignored within a week, and an ignored check is worse
than none: it reads as coverage the repository does not have.
"""
import datetime
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LEDGER = REPO / "workspace" / "measurements.jsonl"
SCAN = [REPO / "docs", REPO / "workspace"]
SKIP_NAMES = {"measurements.jsonl"}
# tok/s, "N GB resident", and speedup multiples. Nothing else.
# "max 30 GB resident" is a BUDGET -- a constraint we chose, not a claim about this
# hardware. Only assertions need provenance. Getting this wrong would have demanded a
# measurement for every target we set, which is how a check earns its way into being ignored.
BUDGET = re.compile(r"(?:max|maximum|under|below|at most|no more than|budget|cap|<=|\u2264)"
                    r"[^.\n]{0,24}$", re.I)
PATTERNS = [
    re.compile(r"\b(\d+(?:\.\d+)?)\s*tok/s\b", re.I),
    re.compile(r"\b(\d+(?:\.\d+)?)\s*GB\s+resident\b", re.I),
    re.compile(r"\b(\d+(?:\.\d+)?)x\s+(?:speedup|faster|slower|throughput)\b", re.I),
]
REASONING_EFFORT = re.compile(
    r"think\s*=|reasoning_effort|reasoning length|think level|think flag", re.I)
EPOCH = "2026-10-15"


def ledger():
    if not LEDGER.is_file():
        return None
    vals = set()
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            print(f"  NOTE: unreadable line in {LEDGER} -- not counted", file=sys.stderr)
            continue
        if r.get("value") is not None:
            vals.add(str(float(r["value"])))
    return vals


def effort_rows_unstamped(text: str) -> list:
    """t1218 items 4 and 8: a reasoning-effort row must carry its provenance.

    WHY: the ledger today holds row 55, unit "characters", describing how long the
    think lever made the model reason, and this file does not notice. A character
    count and a token count differ by ~4x, and an unstamped ad-hoc curl is
    indistinguishable from the probe's own row. Sits beside ledger() (the value
    collector) and the empty-ledger guard: this is a new rule over the same rows,
    not an edit to either.
    """
    findings = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            print(f"  NOTE: unreadable line in ledger -- not counted", file=sys.stderr)
            continue
        date = row.get("date")
        if date is None or date < EPOCH:
            continue
        what = str(row.get("what") or "")
        figure = str(row.get("figure") or "")
        if not REASONING_EFFORT.search(what) and not REASONING_EFFORT.search(figure):
            continue
        stamped = (row.get("probe") == "probe_think_lever"
                   and row.get("unit") == "tokens"
                   and str(row.get("token_source") or "").strip())
        if not stamped:
            findings.append({"figure": figure, "what": what})
    return findings


def main():
    known = ledger()
    if known is None:
        print(f"  [FAIL] {LEDGER.relative_to(REPO)} is missing -- no figure has a provenance")
        return 1
    if not known:
        # ZERO MEASUREMENTS IS NOT ALL MEASUREMENTS ACCOUNTED FOR.
        print("  [FAIL] the measurement ledger is empty. Nothing was checked; not a pass.")
        return 1

    unregistered, scanned = [], 0
    for root in SCAN:
        if not root.is_dir():
            continue
        for f in sorted(root.rglob("*.md")):
            if f.name in SKIP_NAMES:
                continue
            # A model's raw reasoning (<out>.thinking.md) is a RECORD of what it read, not a
            # claim the repository makes -- and a record is never edited to pass a check.
            # 2026-09-25: Q4 quoted SERVING.json's unmeasured "6.5 tok/s"; the fix went to
            # SERVING.json, the source it read, and the record stays as it was.
            if f.name.endswith(".thinking.md"):
                continue
            # The same holds for the manager's ANSWERS, which are its raw output kept as
            # evidence (2026-09-25: Q4's t152.md quoted 45.0 and 22.5 tok/s). A figure there is
            # what the model said, and the citation audit (t413) is where model claims get
            # checked. A figure Claude carries into a doc from one of these is still scanned.
            rel = f.relative_to(REPO).as_posix()
            if rel.startswith(("workspace/run-logs/manager-queue/", "workspace/manager-answers/")):
                continue
            scanned += 1
            text = f.read_text(encoding="utf-8", errors="replace")
            for pat in PATTERNS:
                for m in pat.finditer(text):
                    before = text[max(0, m.start() - 40):m.start()]
                    if BUDGET.search(before):
                        continue                    # a target, not a claim
                    if str(float(m.group(1))) not in known:
                        line = text[:m.start()].count("\n") + 1
                        unregistered.append((f.relative_to(REPO), line, m.group(0)))

    if unregistered:
        print(f"  [FAIL] {len(unregistered)} performance figure(s) cited with no measurement "
              f"behind them:")
        for path, line, txt in unregistered[:12]:
            print(f"           {path}:{line}  \"{txt}\"")
        print("\n         Every one of these is a claim about this hardware. Record it in")
        print("         workspace/measurements.jsonl with the conditions it was taken under")
        print("         -- warm or cold, context length, the command -- or stop citing it.")
        print("         A figure measured once from cold was quoted here all day and was 3x")
        print("         wrong; the number was never the problem, the missing conditions were.")
        return 1
    unstamped = effort_rows_unstamped(LEDGER.read_text(encoding="utf-8"))
    if unstamped:
        print(f"  [FAIL] {len(unstamped)} reasoning-effort row(s) in the ledger lack "
              f"provenance:")
        for r in unstamped[:12]:
            print(f"           figure={r['figure']}  \"{r['what']}\"")
        print("\n         A character count and a token count differ by ~4x. Record the unit")
        print("         as tokens with its token_source and the probe that stamped it, or")
        print("         the row is indistinguishable from an ad-hoc curl.")
        return 1
    print(f"  [PASS] every performance figure in {scanned} document(s) has a recorded "
          f"measurement ({len(known)} in the ledger)")
    return 0


# t1078: a claim about model SPEED has no carrier in LEDGER, which records memory footprint per
# model, not decode rate. This is a second ledger beside it -- before/after, the conditions the
# number was taken under, and the task that owns it. 11 days of latency had to be hand-assembled
# from three sources that never joined, because nothing joined a figure to its provenance.
PERF_LEDGER = REPO / "workspace" / "perf-claims.jsonl"


def record_claim(ledger_path, *, setting, model, metric, value, baseline_value, num_gpu,
                 num_ctx, concurrent, warm, task_id, command):
    """Append ONE measured performance claim as one JSON line. Append-only, never a rewrite.

    task_id is validated before the file is opened: an ownerless row is how a number gets quoted
    with nobody standing behind it, and a refused claim must write nothing at all.
    """
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError(f"record_claim: a claim row needs a task_id (got {task_id!r})")
    row = {
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "setting": setting,
        "model": model,
        "metric": metric,
        "value": value,
        "baseline_value": baseline_value,
        "num_gpu": num_gpu,
        "num_ctx": num_ctx,
        "concurrent": concurrent,
        "warm": warm,
        "task_id": task_id,
        "command": command,
    }
    path = Path(ledger_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def rows(ledger_path):
    """Every whole row of a claims ledger, in file order. A missing file reads as empty.

    An unreadable line is REPORTED by name, never swallowed: a ledger that quietly drops a row
    makes the claim it would have closed look satisfied, which is the failure this file exists to
    catch. Same shape as ledger() above.
    """
    path = Path(ledger_path)
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            print(f"  NOTE: unreadable line in {path} -- not counted", file=sys.stderr)
    return out


def claims_in(text):
    """The speed claims in a string, each {phrase, model, quote}.

    A claim is a speed word and a model id in the SAME sentence. "the suite is faster" with no
    model named is not a claim about model speed, so it yields nothing -- the carrier exists for
    figures about this hardware, not for every adjective. A model id is matched by shape (name,
    colon, version) so a model loaded tomorrow needs no edit here. The quote is the whole
    sentence, punctuation included, because a fragment is not a claim anyone can check.
    """
    model_id = re.compile(r"[A-Za-z][\w.+-]*:\d+(?:\.\d+)?[a-zA-Z]*")
    speed_word = re.compile(r"\b(faster|slower|speedup)\b", re.I)
    found = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):   # punctuation stays with its sentence
        ids = list(model_id.finditer(sentence))
        if not ids:
            continue
        for word in speed_word.finditer(sentence):
            after = next((m for m in ids if m.start() >= word.end()), None)
            found.append({"phrase": word.group(1).lower(),
                          "model": (after or ids[0]).group(0),
                          "quote": sentence})
    return found


def unmatched_claims(claims, measured):
    """The claims no measured row closes, in order, returned as the dicts passed in.

    Every faster/slower/speedup claim is about decode rate, so only a tok_s row naming the same
    model closes it: a memory_gib row for that model measures a different thing, and a tok_s row
    for another model measures a different model. Identity is kept so a caller reports the claim
    it started with, quote and all.
    """
    return [claim for claim in claims
            if not any(r.get("model") == claim["model"] and r.get("metric") == "tok_s"
                       for r in measured)]


if __name__ == "__main__":
    sys.exit(main())
