"""t1243 red fixture: the service-start endpoint-binding and liveness ledger.

Why this suite exists (workspace/rca/d80d12a6e162.md:23, :36): on 2026-08-27
model-chat.service was reported "live" while every vLLM container it dials sat
in "Exited (0)", because nothing on this box records WHICH endpoint a standing
service dials, or WHETHER that endpoint answered.

The contract pinned here:
  workspace/service-endpoint.jsonl -- append-only, one JSON row per service start
  row keys exactly {service, endpoint, model, probe_cmd, probe_exit, at}
  a service with NO row, a STALE row, or a NON-ZERO probe_exit is NOT live.

Red on main: scripts/service_endpoint.py does not exist, so every test below
reports "service_endpoint module is missing" and the runner exits 1.
"""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

IMPORT_ERROR = ""
try:
    import service_endpoint
except Exception as exc:  # recorded, never swallowed
    service_endpoint = None
    IMPORT_ERROR = type(exc).__name__ + ": " + str(exc)

SERVICE = "model-chat.service"
OTHER = "app-feedback.service"
VLLM = "http://127.0.0.1:8021/v1"       # env 1: the direct-dial containers, dead 2026-08-27
OLLAMA = "http://127.0.0.1:11434/v1"    # what the unit was silently re-pointed at
MODEL = "qwen3:32b"
PROBE = "curl -sS -m 5 " + OLLAMA + "/models"
KEYS = {"service", "endpoint", "model", "probe_cmd", "probe_exit", "at"}
T0 = "2026-10-16T05:00:00+00:00"
T_FRESH = "2026-10-16T05:59:00+00:00"
T_NEWER = "2026-10-16T05:59:30+00:00"
T_STALE = "2026-10-16T03:00:00+00:00"
NOW = "2026-10-16T06:00:00+00:00"
MAX_AGE = 3600


def row(service=SERVICE, endpoint=VLLM, at=T_FRESH, probe_exit=0):
    return {"service": service, "endpoint": endpoint, "model": MODEL,
            "probe_cmd": PROBE, "probe_exit": probe_exit, "at": at}


def test_schema_and_full_endpoint_round_trip():
    p = []
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "service-endpoint.jsonl"
        long_ep = VLLM + "/models/" + ("ctx" * 200)          # ~630 chars: must survive whole
        service_endpoint.append_row(path, service=SERVICE, endpoint=long_ep,
                                    model=MODEL, probe_cmd=PROBE, probe_exit=7, at=T0)
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) != 1:
            p.append("append_row wrote " + str(len(lines)) + " lines, expected 1")
            return p
        got = json.loads(lines[0])
        if set(got) != KEYS:
            p.append("row keys " + repr(sorted(got)) + " != " + repr(sorted(KEYS)))
        if got.get("endpoint") != long_ep:
            p.append("endpoint was cut: stored " + str(len(str(got.get("endpoint"))))
                     + " of " + str(len(long_ep)) + " characters")
        if long_ep not in lines[0]:
            p.append("the full endpoint string is absent from the appended line")
        if got.get("service") != SERVICE or got.get("probe_exit") != 7:
            p.append("service/probe_exit not recorded as given: " + json.dumps(got))
        rows, torn = service_endpoint.read_rows(path)
        if torn != 0:
            p.append("clean ledger reported torn=" + repr(torn))
        if not rows or rows[0].get("endpoint") != long_ep:
            p.append("read_rows lost the endpoint the writer stored")
    return p


def test_torn_final_line_is_counted_not_fatal():
    p = []
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "service-endpoint.jsonl"
        good = json.dumps(row(endpoint=OLLAMA, probe_exit=0))
        path.write_text(good + "\n" + good[:40], encoding="utf-8")
        try:
            rows, torn = service_endpoint.read_rows(path)
        except Exception as exc:
            return ["read_rows raised on a torn last line: "
                    + type(exc).__name__ + ": " + str(exc)]
        if len(rows) != 1:
            p.append("expected the 1 intact row, got " + str(len(rows)))
        if torn != 1:
            p.append("torn final line not counted: torn=" + repr(torn))
    return p


def test_missing_ledger_is_no_record():
    p = []
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "service-endpoint.jsonl"          # never written: today's box
        try:
            rows, torn = service_endpoint.read_rows(path)
        except Exception as exc:
            return ["read_rows raised on a missing ledger: "
                    + type(exc).__name__ + ": " + str(exc)]
        if rows != []:
            p.append("missing ledger must read as no rows, got " + repr(rows))
        v = service_endpoint.service_liveness(path, SERVICE, now=NOW, max_age_s=MAX_AGE)
        if v != "no-record":
            p.append("a service with no row reported " + repr(v) + ", expected 'no-record'")
    return p


def test_verdicts_for_the_four_states():
    p = []
    cases = [
        ([], "no-record"),                                   # empty ledger
        ([row(OTHER, OLLAMA, T_FRESH, 0)], "no-record"),    # a row for a DIFFERENT service
        ([row(SERVICE, OLLAMA, T_STALE, 0)], "stale"),      # 7200s old, max_age 3600
        ([row(SERVICE, VLLM, T_FRESH, 7)], "probe-failed"), # fresh row, probe died
        ([row(SERVICE, OLLAMA, T_FRESH, 0)], "live"),       # the only live shape
    ]
    for rows, want in cases:
        got = service_endpoint.liveness(rows, SERVICE, now=NOW, max_age_s=MAX_AGE)
        if got != want:
            p.append("rows " + json.dumps(rows) + " -> " + repr(got)
                     + ", expected " + repr(want))
    return p


def test_newest_row_for_the_service_decides_the_verdict():
    p = []
    dead_last = [row(SERVICE, OLLAMA, T_STALE, 0), row(SERVICE, VLLM, T_FRESH, 7)]
    live_last = [row(SERVICE, VLLM, T_STALE, 7), row(SERVICE, OLLAMA, T_FRESH, 0)]
    got = service_endpoint.liveness(dead_last, SERVICE, now=NOW, max_age_s=MAX_AGE)
    if got != "probe-failed":
        p.append("newest row is a failed probe at 05:59 -- reported " + repr(got))
    got = service_endpoint.liveness(live_last, SERVICE, now=NOW, max_age_s=MAX_AGE)
    if got != "live":
        p.append("newest row is a fresh probe_exit=0 -- reported " + repr(got))
    return p


def test_the_row_written_at_service_start_decides_the_verdict():
    p = []
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "service-endpoint.jsonl"
        service_endpoint.append_row(path, service=SERVICE, endpoint=VLLM, model=MODEL,
                                    probe_cmd=PROBE, probe_exit=7, at=T_FRESH)
        v = service_endpoint.service_liveness(path, SERVICE, now=NOW, max_age_s=MAX_AGE)
        if v != "probe-failed":
            p.append("append_row wrote probe_exit=7, fresh -- reported " + repr(v))
        service_endpoint.append_row(path, service=SERVICE, endpoint=OLLAMA, model=MODEL,
                                    probe_cmd=PROBE, probe_exit=0, at=T_NEWER)
        v = service_endpoint.service_liveness(path, SERVICE, now=NOW, max_age_s=MAX_AGE)
        if v != "live":
            p.append("after the endpoint was re-pointed and the probe answered -- "
                     "reported " + repr(v))
        rows, torn = service_endpoint.read_rows(path)
        if len(rows) != 2:
            p.append("ledger is append-only: 2 starts must leave 2 rows, got " + str(len(rows)))
    return p


TESTS = [
    test_schema_and_full_endpoint_round_trip,
    test_torn_final_line_is_counted_not_fatal,
    test_missing_ledger_is_no_record,
    test_verdicts_for_the_four_states,
    test_newest_row_for_the_service_decides_the_verdict,
    test_the_row_written_at_service_start_decides_the_verdict,
]


def main():
    fails = []
    for fn in TESTS:
        if IMPORT_ERROR:
            fails.append(fn.__name__ + ": service_endpoint module is missing ("
                         + IMPORT_ERROR + ")")
            continue
        try:
            for msg in fn():
                fails.append(fn.__name__ + ": " + msg)
        except Exception as exc:
            fails.append(fn.__name__ + ": raised " + type(exc).__name__ + ": " + str(exc))
    print("service_endpoint oracle: " + str(len(TESTS) - len(fails)) + "/"
          + str(len(TESTS)) + " pass")
    for m in fails:
        print("FAIL " + m)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
