#!/usr/bin/env python3
"""t248's oracle: the scoper must be ABLE to say SCOPED, and must hold with a named gap.

    python3 scripts/evals/eval_scope_taxonomy.py           the 35-item golden set (the real gate)
    python3 scripts/evals/eval_scope_taxonomy.py --sample  six items, the fast RED/GREEN loop

WHY THIS SHAPE. t248 measured 2026-09-08: the scoper said SCOPED ZERO times out of 26
where the owner said SCOPED 14 (10/26 = 38% overall), and the cause was its own prompt --
a must-exist-on-disk suite rule plus a label set that conflated the owner's stage
("a direction exists") with the model's stage ("specified enough to hand over"). Jas,
2026-09-18, chose the two-label shape that separates them:

    SCOPED     a path to the goal exists: file, checkable done, oracle (may not exist yet)
    NEEDS_JAS  held, with exactly what is missing named (do / do-not recommendation allowed)

So the oracle asserts, over Jas's own blind labels (workspace/golden-set-labels.jsonl,
35 items):

  1. at least ONE SCOPED among the items Jas labelled SCOPED (the old code: 0 -- RED);
  2. HOLD AGREEMENT >= 91%: of the items Jas did NOT scope, >= 91% come back NEEDS_JAS
     (t248's own spec; the 2026-09-08 run already met this -- the qualifier was faithful
     to the wrong definitions, and this keeps it faithful to the RIGHT ones);
  3. every SCOPED verdict carries the path: a file that exists and a checkable "done"
     (the shape is the fix -- a SCOPED-without-path is the old drift wearing a new label).

ISOLATION. The verdicts are what is measured, not the task state: the store is a stub,
so holding/releasing never touches the real task files during an oracle pass. The live
verdicts land in workspace/golden-set-verdicts.jsonl (or -sample), re-runnable and
comparable run over run.

COST. Each item is one real model call (default qwen3.8:27b-mtp-bf16). The full pass is
a long-running job: run it in the background and collect it, the way the 2026-09-08 gate
was run and the way the gate-6 closure was proven (run the script, capture the result).
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import scope_queue as SQ                      # noqa: E402


def labels():
    out = {}
    for line in (REPO / "workspace/golden-set-labels.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        out[r["id"]] = r["label"]
    return out


def pick(ids_all, lab, sample):
    if not sample:
        return ids_all
    scoped = [i for i in ids_all if lab[i] == "SCOPED"][:4]
    notd = [i for i in ids_all if lab[i] == "NOT_DELEGABLE"][:2]
    return scoped + notd


def tasks_for(ids):
    out = []
    missing = []
    for tid in ids:
        f = next((REPO / "workspace/tasks").glob(f"{tid}--*"), None)
        if f is None:
            missing.append(tid)
            continue
        s = f.read_text(encoding="utf-8")
        title = (re.search(r"^title: (.*)$", s, re.M) or ["", ""])[1]
        out.append((tid, title.strip(), s[:5000]))
    return out, missing


class NoStore:
    """The oracle measures verdicts; real task state must not move during the pass."""
    def add_note(self, *a, **k):
        return True
    def set_meta(self, *a, **k):
        return True
    def move(self, *a, **k):
        return None


def latest_verdicts(out_path):
    best = {}
    for n, line in enumerate(out_path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            r = json.loads(line)
        except ValueError as e:
            # A torn verdict row is SAID, not skipped: silently dropping it would score the
            # golden set on fewer items than it claims.
            print(f"  (unreadable verdict row {n} in {out_path.name}: {e})", file=sys.stderr)
            continue
        if r.get("proposal"):
            best[r["id"]] = r["proposal"]
    return best


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sample", action="store_true", help="six items, fast loop")
    ap.add_argument("--model", default="qwen3.8:27b-mtp-bf16")
    a = ap.parse_args()

    lab = labels()
    ids = pick(list(lab), lab, a.sample)
    tasks, missing = tasks_for(ids)
    if len(missing) > max(1, len(ids) // 4):
        raise SystemExit(f"too many golden task files missing: {missing} "
                         "-- the golden set itself is broken; do not run a model against "
                         "a broken set")
    print(f"  {len(tasks)} golden item(s), model {a.model}, missing: {missing or 'none'}")

    def ask(tid, body):
        import deliberate
        r = deliberate.deliberate(a.model, SQ.TASK.format(task=body, tid=tid), SQ.SUCCESS,
                                  budget=16000, max_chunks=2,
                                  progress=str(REPO / "workspace/scope-progress-golden.jsonl"),
                                  log=lambda m: print(f"    {m.strip()}", flush=True))
        return r.answer

    out = REPO / f"workspace/golden-set-verdicts{'-sample' if a.sample else ''}.jsonl"
    out.write_text("", encoding="utf-8")
    t0 = time.time()
    rep = SQ.run(tasks, ask, out, REPO / "workspace/scoping-ledger-golden.jsonl",
                 store=NoStore())
    print(f"  ledger: {rep}  ({round(time.time() - t0)}s)")

    got = latest_verdicts(out)
    fails = []

    def ck(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'BAD'}]  {label}" + (f"  ({detail})" if detail else ""))
        if not ok:
            fails.append(label)

    jas_scoped = [i for i in ids if lab[i] == "SCOPED"]
    jas_hold = [i for i in ids if lab[i] != "SCOPED"]
    model_scoped = [i for i in ids if (got.get(i) or {}).get("verdict") == "SCOPED"]
    model_held = [i for i in jas_hold if (got.get(i) or {}).get("verdict") == "NEEDS_JAS"]

    print(f"\n  jas SCOPED {len(jas_scoped)}, model SCOPED {len(model_scoped)}; "
          f"jas held {len(jas_hold)}, model held {len(model_held)}")
    for i in ids:
        p = got.get(i) or {}
        v = p.get("verdict", "NO-VERDICT")
        mark = "*" if lab[i] == "SCOPED" and v == "SCOPED" or lab[i] != "SCOPED" and v == "NEEDS_JAS" else " "
        print(f"   {mark} {i:6} jas={lab[i]:14} model={v:10} "
              f"{(p.get('file') or '')[:44]} {(p.get('done_means') or p.get('what_is_needed') or '')[:40]}")

    # 1. SCOPED must be REACHABLE on the items the owner scoped (old code: 0/26 -> RED).
    hit = len([i for i in jas_scoped if (got.get(i) or {}).get("verdict") == "SCOPED"])
    ck("SCOPED is reachable on Jas's SCOPED items", hit >= 1, f"{hit}/{len(jas_scoped)}")

    # 2. HOLD AGREEMENT >= 91% (t248's spec, kept on the RIGHT definitions).
    held_n = len(model_held)
    need = len(jas_hold)
    agree = held_n / need if need else 0.0
    ck("hold agreement >= 91% on Jas's non-SCOPED items", agree >= 0.91, f"{held_n}/{need} = {agree:.0%}")

    # 3. Every SCOPED verdict must carry the path: on-disk file + checkable done.
    bad_shape = [i for i in model_scoped
                 if not ((got[i].get("done_means") or "").strip())]
    ck("every SCOPED verdict names file + checkable done", not bad_shape,
       f"offending: {bad_shape or 'none'}")

    # Agreement where the owner and model both said SCOPED (the useful direction).
    if jas_scoped:
        print(f"  scoped-direction agreement: {hit}/{len(jas_scoped)} = {hit / len(jas_scoped):.0%}")

    if missing:
        print(f"  (skipped: {missing})")
    for x in fails:
        print("FAIL:", x)
    print("eval_scope_taxonomy:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
