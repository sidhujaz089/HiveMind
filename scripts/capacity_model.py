#!/usr/bin/env python3
"""How many jobs the box can actually run: from MEASURED curves and the ENGINE's own limits, not from a stored 4.

    python3 scripts/capacity_model.py                # what it would decide now, and why

Jas, 2026-10-29: "is there a way to fix the scheduler?... What got us here won't get us there."

TWO CAPACITIES, BECAUSE THEY ARE TWO RESOURCES (a review corrected this, and a measurement settled it):
  * INFERENCE is bounded by the engine, not by host RAM. Verified from `ps`: the server runs
    `--max-num-seqs 4`, `--kv-cache-memory-bytes 12288m`, `--max-model-len 262144` -- a PREALLOCATED pool -- and
    four concurrent requests moved host MemAvailable by only -0.46 GB. So an inference lane costs a SEQUENCE
    SLOT and KV TOKENS inside a pool that is already paid for.
  * BUILDS AND SUITES are bounded by HOST BYTES: they spawn processes and copy the repo. Measured: a build's
    admission is 12 GB + 4 GB margin against ~22 GB free, a slack of ~6 GB.

So `model_slots()` returning 4 is not a capacity decision at all -- it is the engine's ceiling, which is exactly
the number to check against. THE INPUT THAT WAS MISSING IS THE MEASURED CURVE: how much aggregate throughput each
lane count actually produced on this box. Measured 2026-10-29 with scripts/token_throughput.py, medians of three
trials: 1/2/4 lanes = 11.43 / 18.90 / 25.29 tok/s while healthy (more lanes, more throughput), and at ~65% host
pressure the same shape INVERTED (4 lanes 15.15 against 2 lanes 28.41). That inversion is the whole reason this
must be measured rather than assumed.

WHAT THIS MODULE DOES: turns a measured curve plus the engine's ceiling into a lane count, and says UNPROVEN when
there is no curve instead of guessing. It changes nothing on its own.
"""
import json
import os
import sys
from pathlib import Path

CURVE = Path(os.environ.get("HIVEMIND_CAPACITY_CURVE")
             or (Path.home() / ".cache" / "hivemind-tmp" / "throughput-curve.json"))


def load_curve(path=None):
    """{lanes: aggregate_tok_s} from the throughput tool's own record, or {} when there is none."""
    p = Path(path or CURVE)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    pts = data.get("curve") if isinstance(data, dict) else data
    out = {}
    for k, v in (pts or {}).items():
        try:
            if float(v) > 0:
                out[int(k)] = float(v)
        except (TypeError, ValueError):
            print(f"  NOTE: unusable curve point {k!r}: {v!r} -- ignored", file=sys.stderr)
    return out


def inference_lanes(slots_max=None, curve=None):
    """(lanes, why). The lane count with the best MEASURED aggregate throughput, capped by the engine's ceiling.

    NO CURVE IS NOT A REASON TO GUESS: without one this returns the engine's own ceiling and says UNPROVEN, so
    the number is visibly the engine's limit rather than a capacity decision dressed as one.
    """
    curve = load_curve() if curve is None else curve
    if slots_max is None:
        slots_max = engine_slots()
    if not curve:
        return (slots_max, f"UNPROVEN: no measured throughput curve at {CURVE.name}; {slots_max} is the "
                           f"ENGINE's ceiling (--max-num-seqs), not a capacity finding")
    best = max(curve.items(), key=lambda kv: kv[1])
    capped = min(best[0], slots_max) if slots_max else best[0]
    if capped < best[0]:
        return (capped, f"the measured best was {best[0]} lane(s) at {best[1]:.2f} tok/s, capped to {capped} by "
                        f"the engine's --max-num-seqs")
    return (capped, f"the measured best is {best[0]} lane(s) at {best[1]:.2f} tok/s "
                    f"(curve: {', '.join(f'{k}->{v:.1f}' for k, v in sorted(curve.items()))})")


def resolved_slots(declared, live, name=""):
    """(slots, why) -- the ceiling to schedule against, and the sentence that says where it came from.

    THE ENGINE WINS OVER THE FILE (A2, 2026-10-30). SERVERS.json declared vllm-swift slots = 8 while the engine ran
    --max-num-seqs 4, and modellease took the FILE's number as the ceiling -- so the scheduler could admit eight
    sequences into an engine configured to batch four. A file cannot batch; the engine does. The declared number is
    used only when the flag cannot be read, and then it is called what it is: a claim, not a measurement.
    """
    who = f" {name}" if name else ""
    if live is not None:
        try:
            live = int(live)
        except (TypeError, ValueError):
            live = None
        if live is not None:
            try:
                dec = int(declared) if declared is not None else None
            except (TypeError, ValueError):
                dec = None
            if dec is not None and dec != live:
                return live, (f"the file declares {dec} slot(s){who} and the engine runs --max-num-seqs {live}; "
                              f"the ENGINE wins, because admitting more than it batches is not capacity")
            return live, f"the engine's --max-num-seqs is {live}{who}"
    if declared is not None:
        try:
            return int(declared), (f"the engine's flag could not be read, so this is the DECLARED {declared} "
                                   f"slot(s){who} -- a claim, not a measurement")
        except (TypeError, ValueError) as e:                        # noqa: BLE001 -- said, not swallowed
            print(f"  NOTE: the declared slot count for{who or ' this server'} is not a number "
                  f"({declared!r}: {type(e).__name__}) -- ignored, not guessed at", file=sys.stderr)
    return None, f"neither the engine's flag nor a declared number is available{who}"


def engine_slots(default=4):
    """The server's own --max-num-seqs, read from /proc, or `default` with a note when it cannot be read.

    FROM /proc, NOT FROM `ps` COLUMNS (2026-10-29): parsing a `ps` column is a defect class in this repository --
    the columns move, the command line wraps, and a wrong parse is indistinguishable from a correct one. Each
    process's own NUL-separated cmdline is what the kernel holds.
    """
    for pid in sorted(Path("/proc").glob("[0-9]*"), key=lambda p: p.name):
        try:
            raw = (pid / "cmdline").read_bytes()
        except OSError as e:
            # SAID, NOT SWALLOWED: a process that vanished mid-scan is normal, but a scan that silently skips
            # everything would report the default ceiling as though it had been read.
            print(f"  NOTE: cannot read /proc/{pid.name}/cmdline ({type(e).__name__}: {e})", file=sys.stderr)
            continue
        if b"vllm" not in raw and b"serve" not in raw:
            continue
        toks = [t.decode("utf-8", "replace") for t in raw.split(b"\0") if t]
        for i, t in enumerate(toks):
            if t == "--max-num-seqs" and i + 1 < len(toks):
                try:
                    return int(toks[i + 1])
                except ValueError:
                    return default
            if t.startswith("--max-num-seqs="):
                try:
                    return int(t.split("=", 1)[1])
                except ValueError:
                    return default
    print(f"  NOTE: no running server's --max-num-seqs could be read; {default} assumed", file=sys.stderr)
    return default


def builds_that_fit(host_free_gb, need_gb=16.0, cap=None):
    """(n, why) -- how many builds fit in the host bytes free right now. Host bytes, not model capacity."""
    if host_free_gb is None:
        return (0, "UNPROVEN: host free memory unreadable, so no build is admitted")
    n = int(max(0.0, host_free_gb) // max(0.001, need_gb))
    if cap is not None:
        n = min(n, int(cap))
    return (n, f"{host_free_gb:.1f} GB free / {need_gb:.0f} GB per build = {n}")


GATE_ENV = "HIVEMIND_ADMISSION_GATE"
# ABSOLUTE, from this file's own location: a relative switch is missed the moment the caller's cwd is not the
# repo, and a gate whose switch silently does not exist is a gate that is always off (2026-10-29).
REPO = Path(__file__).resolve().parent.parent
GATE_FILE = REPO / "workspace" / "run-logs" / "admission-gate.enabled"

# HOW MANY BUILD PEAKS BEFORE THE GATE MAY BE SWITCHED ON (Jas, 2026-10-29: "state the N builds of
# promised-vs-measured data required"). Twenty, because the reservation being checked is a MAXIMUM and what the
# gate must know is how often a build's real peak EXCEEDS its booking: with 20 recorded builds the largest single
# peak is already visible, and a p95 over 20 samples is crude but adequate for a one-sided test ("does 12 GB ever
# get exceeded?"). Fewer than 20 cannot distinguish a rare overrun from none; more is better and changes nothing.
GATE_NEEDS_SAMPLES = 20


def gate_switch_on(env=None, path=None):
    """True when the admission gate is explicitly switched on. OFF by default, and OFF on any doubt.

    An environment variable or a file, because the switch has to be flippable without editing code: the box can be
    left on the old behaviour while the promised-vs-measured numbers accumulate.
    """
    env = os.environ if env is None else env
    val = str(env.get(GATE_ENV) or "").strip().lower()
    if val in ("1", "true", "yes", "on"):
        return True
    # A SWITCH THAT CAN ONLY BE TURNED ON IS NOT A SWITCH (2026-10-30). The docstring says "OFF by default, and OFF on
    # any doubt", but an explicit 0 fell through to the FILE, so on a box where the flag exists there was no way to
    # turn the gate off short of deleting the file -- and every test that launches a job was silently testing the
    # gated regime (test_dispatch_duplicate_guard was red on main for exactly this: "admission UNPROVEN: preflight
    # needs a sequence slot and the slot count is unreadable", not the duplicate guard it is named for).
    if val in ("0", "false", "no", "off"):
        return False
    return Path(path or GATE_FILE).exists()


def admission_gate(kind, free_gb, need_gb, slots_free=None, slots_max=None, on=False):
    """(allow, reason) -- may this job start, judged on the budget rather than on a sample.

    STEP 2 OF THE REDESIGN, and OFF until the numbers say otherwise. The gate answers one question per kind:
      * a BUILD needs `need_gb` of HOST memory (measured: 12 GB clone + 4 GB margin);
      * an INFERENCE job needs a free SEQUENCE SLOT (measured: 4 concurrent requests moved host MemAvailable by
        only -0.46 GB, because the engine's pool is preallocated).
    An UNREADABLE budget refuses rather than allows: "it probably fits" is the answer that built nothing for a day.
    """
    if not on:
        return (True, "gate off: the old behaviour, recorded but not enforced")
    if kind == "production":
        if free_gb is None:
            return (False, f"admission UNPROVEN: host memory unreadable, so a {need_gb:.0f} GB build cannot be "
                           f"proved to fit")
        if float(free_gb) < float(need_gb):
            return (False, f"needs {need_gb:.0f} GB of host memory, {float(free_gb):.1f} GB free")
        return (True, f"{float(free_gb):.1f} GB free covers the {need_gb:.0f} GB build")
    if slots_free is None or slots_max is None:
        return (False, f"admission UNPROVEN: {kind} needs a sequence slot and the slot count is unreadable")
    if int(slots_free) < 1:
        return (False, f"no free sequence slot for {kind}: {int(slots_max)} in use")
    return (True, f"{int(slots_free)} sequence slot(s) free for {kind}")


def per_build_peaks(samples_path=None, runs_path=None):
    """([(chunk, peak_gb)], report) -- one peak per BUILD, from samples that said whose memory it was.

    WHAT THIS REPLACES, AND WHY IT HAD TO EXIST (A5, 2026-10-30). `build_peaks()` returns one value per 20-SECOND
    SAMPLE, and each value is the `job:build` group's total -- the SUM of every build running at that instant.
    Measured on this box: 56,826 such samples against 1,051 production builds that have ever started, so the
    "p95 of recorded peaks" was a p95 of concurrent usage rather than of any build's peak. The samples carried no
    build identity at all, so no reader could have done better.

    memory_watch now records `build_procs` (pid -> GB) with every sample. This joins those pids to the dispatch
    ledger's `started` rows, which carry pid and chunk, and keeps the maximum per chunk. A pid is matched to the
    LATEST started row at or before the sample, because pids are reused and attributing a late sample to the first
    process that ever had that number would credit one build with another's memory.

    Unattributable samples are REPORTED, never dropped: a peak computed from a quieter subset than it claims is
    the confident wrong answer this whole redesign exists to remove.
    """
    samples_path = Path(samples_path or (Path(__file__).resolve().parent.parent / "workspace" /
                                        "memory-watch.jsonl"))
    runs_path = Path(runs_path or (Path(__file__).resolve().parent.parent / "workspace" /
                                   "dispatch-loop.jsonl"))
    report = {"builds": 0, "samples_without_pids": 0, "unattributed_pids": []}
    # pid -> [(at, chunk)] from the ledger, sorted, so a sample finds the build that owned the pid at that time
    runs = {}
    try:
        for line in runs_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError as e:                                 # noqa: BLE001 -- said, not swallowed
                print(f"  NOTE: unreadable dispatch row skipped ({e}) -- one fewer build attributed", file=sys.stderr)
                continue
            if row.get("event") != "started" or not row.get("pid") or not row.get("chunk"):
                continue
            runs.setdefault(str(row["pid"]), []).append((str(row.get("at") or ""), str(row["chunk"])))
    except OSError as e:                                            # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: the dispatch ledger is unreadable ({type(e).__name__}: {e}); no peak can be attributed",
              file=sys.stderr)
        return [], report
    for v in runs.values():
        v.sort()
    peaks, unknown = {}, set()
    try:
        lines = samples_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:                                            # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: memory-watch is unreadable ({type(e).__name__}: {e}); no peaks", file=sys.stderr)
        return [], report
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError as e:                                     # noqa: BLE001 -- said, not swallowed
            print(f"  NOTE: unreadable memory-watch row skipped ({e}) -- one fewer sample counted", file=sys.stderr)
            continue
        procs = row.get("build_procs")
        if not procs:
            if (row.get("groups") or {}).get("job:build"):
                report["samples_without_pids"] += 1                  # a row from before the sampler existed
            continue
        at = str(row.get("at") or "")
        for pid, gb in procs.items():
            owned = runs.get(str(pid)) or []
            chunk = next((c for t, c in reversed(owned) if t <= at), None)
            if not chunk:
                unknown.add(str(pid))
                continue
            try:
                gb = float(gb)
            except (TypeError, ValueError) as e:                     # noqa: BLE001 -- said, not swallowed
                print(f"  NOTE: unreadable build memory {gb!r} ({type(e).__name__}) -- not counted", file=sys.stderr)
                continue
            if gb > peaks.get(chunk, 0.0):
                peaks[chunk] = round(gb, 2)
    report["builds"] = len(peaks)
    report["unattributed_pids"] = sorted(unknown)
    return sorted(peaks.items()), report


def build_peaks(samples_path=None, limit=None):
    """One value per 20-SECOND SAMPLE of the job:build group, in GB -- NOT a peak per build.

    The line that used to open this docstring ("the peak host memory of every build") was wrong, and the number the
    scheduler charged was read from it: 56,826 samples against 1,051 builds, each sample the SUM of whatever builds
    were running. `per_build_peaks()` is the function that answers the per-build question; this one remains because
    the sample distribution is still the honest picture of CONCURRENT usage.

    THE NUMBER R3 NEEDED WAS ALREADY ON DISK. Until 2026-10-29 the reservation for a build was a CONSTANT (BUILD_GIB
    12 + margin 4), justified by "a clone is about twelve gigabytes". memory_watch has grouped a build's processes
    under `job:build` and recorded rss_gb every 20 s for as long as it has run, so the distribution is measurable
    without waiting for a single new booking. Measured over the whole file (56,004 rows): p50 2.99, p90 6.79,
    p95 8.06, p99 11.19, max 52.84 GB -- so the constant sits ABOVE the 99th percentile, and 16 GB of admission
    was being reserved for a build whose 95th-percentile peak is 8.06 GB.

    CAVEAT, said rather than hidden: the group is the SUM of concurrent builds, so a row with several builds running
    is an aggregate peak, not one build's. The max (52.84 GB) is certainly several; the median is certainly one.
    That is why the sizing rule takes a HIGH PERCENTILE rather than the largest value it has ever seen.
    """
    p = Path(samples_path or (Path(__file__).resolve().parent.parent / "workspace" / "memory-watch.jsonl"))
    out = []
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"  NOTE: memory-watch unreadable ({type(e).__name__}: {e}); no peaks", file=sys.stderr)
        return []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError as e:
            # SAID, not swallowed: an unreadable row is one fewer peak in the distribution, and a silent skip would
            # report a confident p95 that is missing records.
            print(f"  NOTE: unreadable memory-watch row skipped ({e})", file=sys.stderr)
            continue
        g = (row.get("groups") or {}).get("job:build")
        if not g:
            continue
        try:
            v = float(g.get("rss_gb") or 0)
        except (TypeError, ValueError) as e:
            print(f"  NOTE: unreadable job:build peak skipped ({e})", file=sys.stderr)
            continue
        if v > 0:
            out.append(v)
    if limit:
        out = out[-int(limit):]
    return out


def percentile(values, p):
    """The p-th percentile of `values` (0..1), nearest-rank. [] -> None."""
    if not values:
        return None
    xs = sorted(values)
    i = min(len(xs) - 1, max(0, int(round(p * (len(xs) - 1)))))
    return xs[i]


def sized_build_gib(peaks=None, pct=0.95, floor_gb=6.0, cap_gb=12.0):
    """(gib, why) -- what a build should be CHARGED for its clone, from the measured peaks (R3).

    THIS REPLACES A CONSTANT WITH A MEASUREMENT, and the constant is not absurd -- it is merely unpaid-for evidence.
    BUILD_GIB is 12.0 and the admission adds ADMIT_MARGIN_GIB 4.0, so a build needs 16 GB free. Measured over 56,004
    recorded peaks: p50 2.99, p90 6.79, p95 8.06, p99 11.19 GB. So 12.0 is above the 99th percentile, and reserving
    16 GB for a build whose 95th percentile is 8.06 costs four gigabytes of headroom on a box whose whole slack was
    six. Caps at the CURRENT constant: a measurement may lower this number, never quietly raise it.

    The percentile rather than the maximum because the `job:build` group is the sum of concurrent builds (see
    build_peaks); the all-time maximum of 52.84 GB is several builds at once, not one build's shape.
    """
    peaks = build_peaks() if peaks is None else peaks
    if not peaks:
        return (float(cap_gb), f"UNPROVEN: no recorded build peaks, so the {cap_gb:.0f} GB constant stands")
    q = percentile(peaks, pct)
    got = round(max(float(floor_gb), min(float(cap_gb), float(q))), 1)
    return (got, f"p{int(pct * 100)} of {len(peaks):,} recorded build peak(s) is {q:.2f} GB -> {got:.1f} GB charged")


def main(argv):
    lanes, why = inference_lanes()
    print(f"  inference lanes: {lanes} -- {why}")
    try:
        free = None
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable"):
                free = int(line.split()[1]) / 2 ** 20
                break
    except OSError as e:
        print(f"  NOTE: MemAvailable unreadable ({e})", file=sys.stderr)
        free = None
    n, why_b = builds_that_fit(free)
    print(f"  builds that fit: {n} -- {why_b}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
