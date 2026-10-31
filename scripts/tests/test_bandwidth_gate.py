#!/usr/bin/env python3
"""t1366 oracle: scripts/bandwidth_gate.py computes a model's decode-bandwidth ceiling BEFORE
it is downloaded, prints the number, records the verdict, and REFUSES (exit non-zero) a model
whose ceiling cannot beat the incumbent.

Red on main: scripts/bandwidth_gate.py does not exist, so the allow case exits 2 and prints
nothing. It is not red for its own bug: it imports nothing from the module (subprocess only),
uses tempfile, sets timeout= on every subprocess, and derives every expected number from the
same arithmetic the gate must implement.

Real constants, quoted from the tree, never placeholders:
  ~273 GB/s decode bandwidth   projects/local-inference/modeleval.py:198, settings.py:701
  49.4 tok/s incumbent (Swift) projects/local-inference/serve.py:83 (measured, MTP 3)
  ~54 GB/token dense bf16 27B  projects/local-inference/settings.py:701
  L-088 (docs/LESSONS.md:1885, enforcement note :2288) is the prose this gate replaces.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "scripts" / "bandwidth_gate.py"

BANDWIDTH_GBS = 273.0
INCUMBENT_TOK_S = 49.4
TIMEOUT = 60


def ceiling(active_params, bytes_per_param, bandwidth_gbs=BANDWIDTH_GBS):
    return bandwidth_gbs * 1e9 / (float(active_params) * float(bytes_per_param))


def run_gate(label, active, bpp, incumbent=INCUMBENT_TOK_S, out=None):
    cmd = [sys.executable, str(GATE),
           "--label", label,
           "--active-params", str(active),
           "--bytes-per-param", str(bpp),
           "--bandwidth-gbs", str(BANDWIDTH_GBS)]
    if incumbent is not None:
        cmd += ["--incumbent-tok-s", str(incumbent)]
    if out is not None:
        cmd += ["--json-out", str(out)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)


def record_at(path):
    assert path.is_file(), f"no record written at {path}: the verdict is not recorded anywhere"
    return json.loads(path.read_text(encoding="utf-8"))


def test_refuses_the_35b_a3b_case_the_rca_is_about():
    # 3.3B active params at 2 bytes = 6.6 GB read per token -> 41.4 tok/s ceiling.
    with tempfile.TemporaryDirectory() as td:
        rec = Path(td) / "gate.json"
        r = run_gate("qwen3-30b-a3b", "3.3e9", "2", out=rec)
        want = ceiling(3.3e9, 2)
        assert r.returncode != 0, (
            f"gate allowed a download whose ceiling is {want:.1f} tok/s against an "
            f"incumbent of {INCUMBENT_TOK_S} tok/s; stdout={r.stdout!r} stderr={r.stderr!r}")
        assert f"{want:.1f} tok/s" in r.stdout, (
            f"stdout does not print the computed ceiling {want:.1f} tok/s: {r.stdout!r}")
        assert "qwen3-30b-a3b" in r.stdout, f"stdout does not name the model: {r.stdout!r}"
        row = record_at(rec)
        assert row["verdict"] == "refused", f"verdict {row.get('verdict')!r}, expected 'refused'"
        assert row["model"] == "qwen3-30b-a3b", f"record names {row.get('model')!r}"
        assert abs(float(row["ceiling_tok_s"]) - want) < 0.05, (
            f"record ceiling {row.get('ceiling_tok_s')!r} != computed {want:.1f}")
        assert float(row["ceiling_tok_s"]) < float(row["incumbent_tok_s"]), (
            "record does not carry the comparison the refusal rests on")
        assert str(INCUMBENT_TOK_S) in str(row.get("detail", "")), (
            f"refusal records no reason naming the incumbent number: {row.get('detail')!r}")


def test_allows_a_model_whose_ceiling_beats_the_incumbent():
    # 1e9 active params at 2 bytes = 2 GB/token -> 136.5 tok/s, above Swift's measured 49.4.
    with tempfile.TemporaryDirectory() as td:
        rec = Path(td) / "gate.json"
        r = run_gate("probe-1b", "1e9", "2", out=rec)
        want = ceiling(1e9, 2)
        assert r.returncode == 0, (
            f"gate refused a model whose ceiling {want:.1f} tok/s beats the incumbent "
            f"{INCUMBENT_TOK_S}: rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}")
        assert f"{want:.1f} tok/s" in r.stdout, (
            f"stdout does not print the computed ceiling {want:.1f} tok/s: {r.stdout!r}")
        row = record_at(rec)
        assert row["verdict"] == "allowed", f"verdict {row.get('verdict')!r}, expected 'allowed'"
        assert abs(float(row["ceiling_tok_s"]) - want) < 0.05, (
            f"record ceiling {row.get('ceiling_tok_s')!r} != computed {want:.1f}")
        assert float(row["ceiling_tok_s"]) > float(row["incumbent_tok_s"]), (
            "an allowed record does not show a ceiling above the incumbent")


def test_refuses_a_dense_27b_and_the_number_is_its_own_not_a_constant():
    # 27B active at 2 bytes = 54 GB/token -> 5.1 tok/s. Same code path as the 35B case,
    # a different number: a gate that prints one fixed ceiling cannot pass this and the test above.
    with tempfile.TemporaryDirectory() as td:
        rec = Path(td) / "gate.json"
        r = run_gate("qwen3-27b-bf16", "27e9", "2", out=rec)
        want = ceiling(27e9, 2)
        assert r.returncode != 0, f"gate allowed a 54 GB/token model; stdout={r.stdout!r}"
        assert f"{want:.1f} tok/s" in r.stdout, (
            f"stdout does not print {want:.1f} tok/s for 27e9 active params at 2 bytes: {r.stdout!r}")
        assert record_at(rec)["verdict"] == "refused"


def test_refuses_when_the_incumbent_is_not_supplied():
    # Fail closed: no default incumbent -- a gate that invents one is advertising again.
    with tempfile.TemporaryDirectory() as td:
        rec = Path(td) / "gate.json"
        r = run_gate("qwen3-30b-a3b", "3.3e9", "2", incumbent=None, out=rec)
        assert r.returncode != 0, (
            f"gate passed a download with no incumbent to compare against: stdout={r.stdout!r}")
        out = (r.stdout + r.stderr).lower()
        assert "incumbent" in out, (
            f"the refusal does not say which input was missing: {out!r}")


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"ok   {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {name}: {e}")
        except Exception as e:                      # noqa: BLE001 -- loud, never silent
            failed += 1
            print(f"FAIL {name}: raised {type(e).__name__}: {e}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
