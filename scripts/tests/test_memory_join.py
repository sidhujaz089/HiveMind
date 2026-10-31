#!/usr/bin/env python3
'''t456 part 2: join memory samples and model loads onto every run-telemetry row.

RED ON MAIN (re-read whole on 2026-10-16): scripts/memory_ledger.py, 231 lines, defines meminfo,
blob_tags, runners, sample, parse_loads, loads, _jsonl, report, fits, main -- and no join_rows
and no write_joined. This suite exits 1 at that check, prints why, and runs nothing else, so it
is red for the missing change and for no bug of its own.

FAKES ONLY. Every input is a literal in this file. Nothing here opens
workspace/run-telemetry.jsonl, workspace/memory-samples.jsonl,
workspace/run-logs/model-loads-memory.jsonl, /proc, the journal, a socket, a model, a
subprocess, or any live state; every path written is under tempfile.mkdtemp().

WHY, from the task text: for each run-telemetry.jsonl row, attach the nearest memory samples
before and after and the peak between them, alongside prompt_eval_count, eval_count and num_ctx
(num_ctx is not recorded per row today -- record it).

THE THREE MEMORY FIELDS, stated once, with no wiggle room:
  mem_available_before_gb -- the available_gb of the LATEST sample whose time is <= the row's
                             (INCLUSIVE: a sample stamped exactly the row's time is the before
                             one, so a call that starts at the instant a sample lands is recorded
                             with that sample's headroom). None when no sample is at or before.
  mem_available_after_gb  -- the available_gb of the EARLIEST sample whose time is STRICTLY >
                             the row's. None when no sample is after it.
  mem_available_min_gb    -- the MINIMUM available_gb over the samples strictly between those
                             two, EXCLUSIVE of both endpoints. That window is the call's work
                             window: the sample the call began at is where it started, the
                             sample after it is where it ended, and what is between them is the
                             peak pressure the call caused. None when there is no sample strictly
                             between them.

WHY THE MIDDLE ONE IS THE POINT. The samples are 30 s apart (the sampler's tick). A call's
prompt eval -- hundreds of tokens a second over tens of thousands of tokens -- runs inside one
30 s tick, so the pressure it causes lands in the sample strictly after it, never in the one it
started at. A "peak" that includes the endpoints reports the call's own start and end, the
weakest readings in the window, and the whole field becomes decorative: on the rows below the
inclusive minimum is 15.5, the exclusive one is 12.25, and 12.25 is the number that says the
call cost something. A version that writes min(before, after) is caught by the assertion on
12.25, which is why the three values 17.0 / 15.5 / 12.25 are deliberately distinct.

WHY "after" IS STRICTLY AFTER. A row stamped at a sample's exact instant takes that sample as
its BEFORE; the first sample strictly after is its AFTER. "Strictly" is what makes those two
never be the same sample, so no row can report the same figure for both ends.

FIELD NAMES come from the ledger own code, not from a guess: sample() writes at / total_gb /
available_gb / runners -- headroom is available_gb, the sample row has no used field -- and
parse_loads() writes at / tag / weights_mib / kv_mib / kv_cells / kv_seqs / kv_per_slot_mib /
compute_mib / predicted_gb / predicted_ctx. The real loads file stamps every one of the 258 rows
read on 2026-10-15 in journal offset form (they end -06:00), which is why the offset case below
is a real case and not a hypothetical: a plain string compare sorts a load stamped 14:50 minus
six hours (that is 20:50Z) BEFORE a 20:30Z telemetry row and picks the wrong context.

WHAT THIS SUITE WAS AND WHY IT CHANGED. The first version (bridge commit 4c6fd8e8) asserted
that a row at 1999-01-01T00:00:00Z gets mem_available_after_gb None. Every sample is dated
2026-10-11, which is strictly after 1999, so the nearest-after rule returns the 20:00:00Z sample
(24.0) and NO implementation following the stated rule can return None there: preflight refused
it as NOT SATISFIABLE, correctly (workspace/preflight-objections.jsonl, 2026-10-16T012209Z and
012610Z). The row now sits after the last sample, where "no sample after" is true by
construction, and the assertions are ordered so every number below follows from the definitions
above. That is the preflight's remedy 1, taken literally.
'''
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import memory_ledger as M

OK = 0
FAIL = 0


def check(name, cond):
    global OK, FAIL
    print('  [%s] %s' % ('ok' if cond else 'FAIL', name))
    OK += 1 if cond else 0
    FAIL += 0 if cond else 1


SAMPLES = [
    {'at': '2026-10-11T20:00:00Z', 'total_gb': 121.62, 'available_gb': 24.0, 'runners': []},
    {'at': '2026-10-11T20:00:30Z', 'total_gb': 121.62, 'available_gb': 17.0, 'runners': []},
    {'at': '2026-10-11T20:00:45Z', 'total_gb': 121.62, 'available_gb': 12.25, 'runners': []},
    {'at': '2026-10-11T20:01:00Z', 'total_gb': 121.62, 'available_gb': 15.5, 'runners': []},
    {'at': '2026-10-11T20:01:30Z', 'total_gb': 121.62, 'available_gb': 19.0, 'runners': []},
]

LOADS = [
    {'at': '2026-10-11T19:50:00Z', 'tag': 'gemma4:26b', 'weights_mib': 26741.1,
     'kv_mib': 24576.0, 'kv_cells': 3072, 'kv_seqs': 2, 'kv_per_slot_mib': 12288.0,
     'compute_mib': 3169.96, 'predicted_gb': 61.2, 'predicted_ctx': 131072},
    {'at': '2026-10-11T20:00:20Z', 'tag': 'qwen3.8:27b-mtp-q4_K_M', 'weights_mib': 16021.47,
     'kv_mib': 256.0, 'kv_cells': 65536, 'kv_seqs': 1, 'kv_per_slot_mib': 256.0,
     'compute_mib': 3169.96, 'predicted_gb': 24.8, 'predicted_ctx': 65536},
    {'at': '2026-10-11T14:50:00-06:00', 'tag': 'gemma4:26b', 'weights_mib': 26741.1,
     'kv_mib': 24576.0, 'kv_cells': 3072, 'kv_seqs': 2, 'kv_per_slot_mib': 12288.0,
     'compute_mib': 3169.96, 'predicted_gb': 61.2, 'predicted_ctx': 32768},
    {'at': '2026-10-11T20:00:00Z', 'tag': 'qwen3.8:27b-mtp-bf16', 'weights_mib': 52634.0,
     'kv_mib': 196608.0, 'kv_cells': 262144, 'kv_seqs': 1, 'kv_per_slot_mib': 196608.0,
     'compute_mib': 4096.0, 'predicted_gb': None, 'predicted_ctx': None},
]

TELE = {'at': '2026-10-11T20:00:40Z', 'harness': 'ask_q16.py',
        'model': 'qwen3.8:27b-mtp-q4_K_M', 'seconds': 12.0, 'num_predict': 500,
        'eval_count': 480, 'prompt_eval_count': 52000, 'done_reason': 'stop',
        'load_seconds': 0.5, 'ok': True, 'tok_s_decode': 40.0}


def main():
    missing = [n for n in ('join_rows', 'write_joined') if not hasattr(M, n)]
    if missing:
        print('FAIL: scripts/memory_ledger.py defines no ' + ' and no '.join(missing)
              + ' -- that is exactly the per-call join t456 part 2 asks for. Nothing below can run.')
        return 1

    def J(rows):
        return M.join_rows(rows, SAMPLES, LOADS)

    out = J([TELE])
    check('one telemetry row in, one joined row out', len(out) == 1)
    r = out[0] if out else {}
    check('the telemetry row is NOT mutated by the join', 'num_ctx' not in TELE)
    check('the joined row is a copy, not the shared dict', r is not TELE)
    for k in ('at', 'harness', 'model', 'seconds', 'num_predict', 'eval_count',
              'prompt_eval_count', 'done_reason', 'load_seconds', 'ok', 'tok_s_decode'):
        check('  work fields carried through: ' + k, r.get(k) == TELE[k])
    check('BEFORE is the latest sample at or before the row (20:00:30Z = 17.0, not 24.0)',
          r.get('mem_available_before_gb') == 17.0)
    check('AFTER is the earliest sample strictly after the row (20:01:00Z = 15.5)',
          r.get('mem_available_after_gb') == 15.5)
    check('PEAK is the minimum strictly between them (20:00:45Z = 12.25, not an endpoint)',
          r.get('mem_available_min_gb') == 12.25)
    check('num_ctx is the model own load: 65536, not gemma 131072',
          r.get('num_ctx') == 65536)

    g = dict(TELE, model='gemma4:26b')
    check('num_ctx is per tag: gemma at 20:00:40Z gets 131072 from its 19:50Z load',
          J([g])[0].get('num_ctx') == 131072)

    mid = dict(TELE, at='2026-10-11T20:30:00Z', model='gemma4:26b')
    check('offset-aware: 20:30Z precedes the 14:50-06:00 (=20:50Z) reload, so 131072 not 32768',
          J([mid])[0].get('num_ctx') == 131072)

    late = dict(TELE, at='2026-10-11T21:00:00Z', model='gemma4:26b')
    check('after the 20:50Z reload the context is the reloaded one: 32768',
          J([late])[0].get('num_ctx') == 32768)

    bf16 = dict(TELE, at='2026-10-11T21:00:00Z', model='qwen3.8:27b-mtp-bf16')
    check('a load with predicted_ctx null gives None, never a guess from kv_cells (262144)',
          J([bf16])[0].get('num_ctx') is None)

    never = dict(TELE, model='swift:latest')
    check('a model with no load before the row gives None, never a guess',
          J([never])[0].get('num_ctx') is None)

    # A row stamped AFTER the last sample: 20:00:00Z is at or before it, 20:01:30Z is the last
    # sample and is also at or before it, so there is nothing strictly after. This is the case
    # the previous version tried to express with a 1999 stamp and could not: every 2026 sample
    # is strictly after 1999, so "no sample after" was false there.
    after_all = dict(TELE, at='2026-10-11T23:00:00Z')
    ar = J([after_all])[0]
    check('row after every sample: BEFORE is the last sample (20:01:30Z = 19.0)',
          ar.get('mem_available_before_gb') == 19.0)
    check('row after every sample: AFTER is None, nothing is strictly after it',
          ar.get('mem_available_after_gb') is None)
    check('row after every sample: PEAK is None, no sample lies between the neighbours',
          ar.get('mem_available_min_gb') is None)

    # A row stamped BEFORE the first sample: nothing is at or before it, so there is no lower
    # bound and no window. The earliest sample is the AFTER one (20:00:00Z = 24.0), which is
    # what the definition gives -- not None, not an extrapolation.
    before_all = dict(TELE, at='1999-01-01T00:00:00Z')
    br = J([before_all])[0]
    check('row before every sample: BEFORE is None, not extrapolated',
          br.get('mem_available_before_gb') is None)
    check('row before every sample: AFTER is the earliest sample (20:00:00Z = 24.0)',
          br.get('mem_available_after_gb') == 24.0)
    check('row before every sample: PEAK is None, the endpoints have nothing between them',
          br.get('mem_available_min_gb') is None)

    # A row between two ADJACENT samples: 20:00:00Z is the latest at or before 20:00:10Z, and
    # 20:00:30Z is the earliest strictly after it. The samples are 30 s apart, so the window
    # holds no sample at all. A peak that reaches its own endpoints reports 24.0 here (the
    # min of before/after); the exclusive rule gives None. One sample of separation, one
    # window: 24.0 is the endpoint the exclusive rule must not fall back to.
    adj = dict(TELE, at='2026-10-11T20:00:10Z')
    adjr = J([adj])[0]
    check('row between ADJACENT samples: BEFORE is 20:00:00Z = 24.0',
          adjr.get('mem_available_before_gb') == 24.0)
    check('row between ADJACENT samples: AFTER is 20:00:30Z = 17.0',
          adjr.get('mem_available_after_gb') == 17.0)
    check('empty window between the neighbours: peak is None, never an endpoint (24.0)',
          adjr.get('mem_available_min_gb') is None)

    # A sample stamped at the row's own instant is the BEFORE one, not the AFTER one.
    exact = dict(TELE, at='2026-10-11T20:01:00Z')
    er = J([exact])[0]
    check('a sample at exactly the row timestamp counts as BEFORE (15.5, not 17.0)',
          er.get('mem_available_before_gb') == 15.5)
    check('  and the next sample is the AFTER one (19.0)', er.get('mem_available_after_gb') == 19.0)
    check('  and the window between them is empty, so the peak is None',
          er.get('mem_available_min_gb') is None)

    junk = dict(TELE, at='not a timestamp')
    jr = J([junk])[0]
    check('an unparseable timestamp yields no values and does not raise',
          jr.get('mem_available_before_gb') is None and jr.get('num_ctx') is None)

    batch = J([TELE, g, late])
    check('a batch keeps input order and each row keeps its own context',
          len(batch) == 3 and batch[0].get('num_ctx') == 65536
          and batch[1].get('num_ctx') == 131072 and batch[2].get('num_ctx') == 32768)

    d = tempfile.mkdtemp()
    p = Path(d) / 'nested' / 'run-telemetry-joined.jsonl'
    check('write_joined returns the number of rows written', M.write_joined(J([TELE]), p) == 1)
    check('write_joined creates the parent directory', p.parent.is_dir())
    rows = [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines() if x.strip()]
    check('the row on disk carries the joined fields',
          len(rows) == 1 and rows[0].get('num_ctx') == 65536
          and rows[0].get('mem_available_min_gb') == 12.25
          and rows[0].get('mem_available_before_gb') == 17.0
          and rows[0].get('mem_available_after_gb') == 15.5)
    check('the row on disk keeps its own work fields',
          rows[0].get('eval_count') == 480 and rows[0].get('prompt_eval_count') == 52000
          and rows[0].get('done_reason') == 'stop' and rows[0].get('seconds') == 12.0)
    check('write_joined APPENDS: a second call leaves both rows',
          M.write_joined(J([g]), p) == 1
          and len([x for x in p.read_text(encoding='utf-8').splitlines() if x.strip()]) == 2)
    check('the appended row is the new one (131072), not a copy of the first',
          json.loads(p.read_text(encoding='utf-8').splitlines()[1])['num_ctx'] == 131072)
    q = Path(d) / 'empty.jsonl'
    check('write_joined of an empty list writes no file and returns 0',
          M.write_joined([], q) == 0 and not q.exists())

    print('\n%d passed, %d failed' % (OK, FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
