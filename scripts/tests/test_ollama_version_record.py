#!/usr/bin/env python3
"""Oracle for t215 -- record the Ollama version and each model's context length.

Red on main because scripts/inference_health.py has no version_row, no
write_version_record, no OLLAMA_VERSION_LEDGER, and its main() takes no runner:
every test opens with require_fns(), which names exactly what is missing, and
prints no traceback.

Once they exist the tests assert BEHAVIOUR, not shape:
  * the version is PARSED out of the real `ollama --version` line
    ("ollama version is 0.33.0" -> "0.33.0"), not the raw line echoed back;
  * each model keeps ITS OWN context length as an int, read from that model's
    own `ollama show` table, with two DISTINCT values (262144 and 32768) so a
    parser that returns one number for every model cannot pass -- the evidence
    filed under this task is a caller asking 32768 against a model resident at
    262144, and those numbers have to stay distinguishable;
  * a model whose `ollama show` fails gets None, never its neighbour's number;
  * models are discovered from `ollama list` and the NAME header is not a model;
  * the parsed values are USED: main() appends them to the ledger it names, so
    a plain run of the health probe records the version with nobody asking --
    "no Ollama version is recorded anywhere" is the finding this closes;
  * a failed ollama call is recorded as a failure carrying the daemon's own
    words, an unparseable version says UNKNOWN, and nothing is cut short.

No live daemon, no model call, no fixed /tmp path, no process left running:
every test passes an injected runner and every write goes under a tempfile dir.
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

try:
    import inference_health as ih
    IMPORT_ERROR = None
except ImportError as exc:
    ih = None
    IMPORT_ERROR = "%s: %s" % (type(exc).__name__, exc)

# Fixtures shaped like the documented CLI output. The two models carry DISTINCT
# context lengths, so a parser that copies one value across every model fails.
VERSION_STDOUT = "ollama version is 0.33.0\n"
SHOW_LLAMA = (
    "  Model\n"
    "      architecture        llama\n"
    "      parameters          8.0B\n"
    "      context length      262144\n"
    "      quantization        Q4_K_M\n"
)
SHOW_QWEN = (
    "  Model\n"
    "      architecture        qwen2\n"
    "      parameters          4.0B\n"
    "      context length      32768\n"
    "      quantization        Q4_K_M\n"
)
LIST_STDOUT = (
    "NAME             SIZE     MODIFIED\n"
    "llama3.1:8b      4.9 GB   2 days ago\n"
    "qwen2:1.5b       934 MB   3 days ago\n"
)


def require_fns():
    assert ih is not None, (
        "scripts/inference_health.py cannot be imported (import failed: %s)" % IMPORT_ERROR
    )
    missing = [n for n in ("version_row", "write_version_record", "OLLAMA_VERSION_LEDGER")
               if not hasattr(ih, n)]
    assert not missing, (
        "scripts/inference_health.py has no %s -- that is the change this brief asks for; "
        "nothing in the repository records the Ollama version today" % ", ".join(missing)
    )


def fixed_runner(argv):
    """Stands in for the ollama CLI. Raises on any argv it was not written to answer,
    so a module that invents a third command is caught, not handed the next reply."""
    argv = list(argv)
    if argv == ["ollama", "--version"]:
        return 0, VERSION_STDOUT, ""
    if argv == ["ollama", "list"]:
        return 0, LIST_STDOUT, ""
    if argv == ["ollama", "show", "llama3.1:8b"]:
        return 0, SHOW_LLAMA, ""
    if argv == ["ollama", "show", "qwen2:1.5b"]:
        return 0, SHOW_QWEN, ""
    raise AssertionError("unexpected argv passed to the runner: %r" % (argv,))


def test_parses_the_version_number_out_of_the_real_output_line():
    require_fns()
    record = ih.version_row(runner=fixed_runner, models=["llama3.1:8b"])
    assert record["version"] == "0.33.0", (
        "expected the parsed version number '0.33.0', got %r" % (record.get("version"),)
    )
    assert record["version"] != VERSION_STDOUT.strip(), (
        "the raw CLI line is not a version; it has to be parsed out, got %r"
        % (record.get("version"),)
    )
    assert set(record) == {"version", "models", "captured_at", "errors"}, sorted(record)


def test_records_each_model_with_its_own_context_length_as_an_int():
    require_fns()
    record = ih.version_row(runner=fixed_runner, models=["llama3.1:8b", "qwen2:1.5b"])
    models = record["models"]
    assert [m["name"] for m in models] == ["llama3.1:8b", "qwen2:1.5b"], repr(models)
    lengths = [m["context_length"] for m in models]
    assert lengths == [262144, 32768], (
        "each model must carry the context length from ITS OWN `ollama show` output; "
        "got %r" % (lengths,)
    )
    assert all(type(m["context_length"]) is int for m in models), (
        "context_length must be an int, not a string: a later reader compares it against "
        "a requested num_ctx; got %r" % (lengths,)
    )
    assert [m["parameters"] for m in models] == ["8.0B", "4.0B"], repr(models)
    assert [m["quantization"] for m in models] == ["Q4_K_M", "Q4_K_M"], repr(models)
    assert [set(m) == {"name", "context_length", "parameters", "quantization"}
            for m in models], repr(models)


def test_models_are_discovered_from_ollama_list_and_the_header_is_skipped():
    require_fns()
    record = ih.version_row(runner=fixed_runner)
    names = [m["name"] for m in record["models"]]
    assert "NAME" not in names, "the `ollama list` header row was recorded as a model"
    assert names == ["llama3.1:8b", "qwen2:1.5b"], repr(names)


def test_a_model_whose_show_fails_gets_none_never_its_neighbour_s_number():
    require_fns()

    def half(argv):
        argv = list(argv)
        if argv == ["ollama", "--version"]:
            return 0, VERSION_STDOUT, ""
        if argv == ["ollama", "show", "llama3.1:8b"]:
            return 0, SHOW_LLAMA, ""
        if argv == ["ollama", "show", "qwen2:1.5b"]:
            return 1, "", "model not found"
        raise AssertionError("unexpected argv passed to the runner: %r" % (argv,))

    record = ih.version_row(runner=half, models=["llama3.1:8b", "qwen2:1.5b"])
    lengths = [m["context_length"] for m in record["models"]]
    assert lengths == [262144, None], (
        "a model whose `ollama show` failed records None; copying a neighbour's context "
        "length is the defect this assertion exists to stop; got %r" % (lengths,)
    )
    assert "model not found" in " ".join(record["errors"]), repr(record["errors"])


def test_a_failed_ollama_call_is_recorded_as_a_failure_with_a_reason():
    require_fns()

    def broken(argv):
        argv = list(argv)
        if argv == ["ollama", "--version"]:
            return 127, "", "ollama: command not found"
        raise AssertionError("unexpected argv passed to the runner: %r" % (argv,))

    record = ih.version_row(runner=broken, models=[])
    assert record["version"] == "UNAVAILABLE", repr(record.get("version"))
    assert record["version"] != "", "an empty version reads as a version that was recorded"
    joined = " ".join(record["errors"])
    assert "command not found" in joined, (
        "a failed ollama call must leave the daemon's own reason in the record, not a "
        "blank; got %r" % (record["errors"],)
    )


def test_an_unparseable_version_says_UNKNOWN_and_never_a_blank():
    require_fns()

    def devel(argv):
        argv = list(argv)
        if argv == ["ollama", "--version"]:
            return 0, "ollama version is (devel)\n", ""
        raise AssertionError("unexpected argv passed to the runner: %r" % (argv,))

    record = ih.version_row(runner=devel, models=[])
    assert record["version"] == "UNKNOWN", (
        "a version line carrying no version number must say UNKNOWN, not echo the line "
        "and not come back empty; got %r" % (record.get("version"),)
    )
    assert record["errors"], (
        "an unparseable version is a finding: it must be recorded with a reason; got %r"
        % (record["errors"],)
    )
    assert "(devel)" in " ".join(record["errors"]), (
        "the record has to say what it could not parse, in the daemon's own words; got %r"
        % (record["errors"],)
    )


def test_write_version_record_appends_complete_rows_to_the_ledger():
    require_fns()
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "nested" / "ollama-versions.jsonl"
        record = ih.version_row(runner=fixed_runner, models=["llama3.1:8b", "qwen2:1.5b"])
        written = ih.write_version_record(record, target)
        assert Path(written) == target, repr(written)
        assert target.is_file(), "the record must land at the path it was handed"
        lines = target.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1, (
            "a .jsonl ledger row is ONE line; a pretty-printed record cannot be read "
            "back by a later reader; got %d lines" % len(lines)
        )
        row = json.loads(lines[0])
        assert row["version"] == "0.33.0", repr(row)
        assert [m["context_length"] for m in row["models"]] == [262144, 32768], repr(row)
        assert "262144" in lines[0] and "32768" in lines[0], (
            "the numbers a later reader compares against num_ctx must survive the write; "
            "a record cut to a prefix is worse than no record"
        )
        assert isinstance(row["captured_at"], str) and "T" in row["captured_at"] \
            and len(row["captured_at"]) >= 10, repr(row.get("captured_at"))
        assert row["errors"] == [], repr(row.get("errors"))
        ih.write_version_record(record, target)
        lines = target.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2, (
            "the ledger is APPENDED to; rewriting it destroys the history of when the "
            "version was recorded; got %d lines" % len(lines)
        )
        for line in lines:
            json.loads(line)


def test_the_ledger_is_the_path_the_task_needs():
    require_fns()
    led = ih.OLLAMA_VERSION_LEDGER
    assert led.name == "ollama-versions.jsonl", repr(str(led))
    assert led.parent.name == "ledgers", repr(str(led))
    assert led.parents[1].name == "workspace", repr(str(led))
    assert ROOT in led.parents, "the default ledger must live in this repository: %s" % led


def test_the_health_probe_records_the_version_without_anyone_asking():
    require_fns()
    with tempfile.TemporaryDirectory() as td:
        led = Path(td) / "ledgers" / "ollama-versions.jsonl"
        real_probe = ih.probe
        real_ledger = ih.OLLAMA_VERSION_LEDGER
        ih.probe = lambda model=None, timeout=None: {
            "up": True, "slow": False, "say": "fake probe: a token came back"}
        ih.OLLAMA_VERSION_LEDGER = led
        argv = sys.argv
        sys.argv = ["inference_health.py"]
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = ih.main(runner=fixed_runner)
        finally:
            sys.argv = argv
            ih.probe = real_probe
            ih.OLLAMA_VERSION_LEDGER = real_ledger
        assert rc == 0, "main() must still exit 0 when the probe is up; got %r" % (rc,)
        out = buf.getvalue()
        assert "0.33.0" in out, (
            "the printed health line has to name the version it recorded, so an operator "
            "reading the probe sees it; got %r" % (out,)
        )
        assert led.is_file(), (
            "running the health probe must RECORD the version -- the finding this task "
            "closes is that no Ollama version is recorded anywhere"
        )
        rows = [json.loads(l) for l in led.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(rows) == 1, repr(rows)
        assert rows[0]["version"] == "0.33.0", repr(rows[0])
        assert [m["context_length"] for m in rows[0]["models"]] == [262144, 32768], repr(rows[0])


if __name__ == "__main__":
    _tests = sorted(
        (name, obj) for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    )
    _failed = 0
    for _name, _fn in _tests:
        try:
            _fn()
            print("PASS %s" % _name)
        except AssertionError as _exc:
            _failed += 1
            print("FAIL %s: %s" % (_name, _exc))
        except Exception as _exc:  # noqa: BLE001 - a crash is a finding, recorded not hidden
            _failed += 1
            print("FAIL %s: %s: %s" % (_name, type(_exc).__name__, _exc))
    print("%d test(s), %d failed" % (len(_tests), _failed))
    sys.exit(1 if _failed else 0)
