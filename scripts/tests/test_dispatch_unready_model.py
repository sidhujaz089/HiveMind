#!/usr/bin/env python3
"""A model whose server does not answer gets no work (2026-10-19).

After Jas resumed the GX10, vLLM took 743 s to load Swift. A ThinkPad build "vouched for" the
inference path, so the 13:17:45 tick gave Swift three jobs that died at once with NoServer, and
the Manager's scoping grant went to them. dispatch_loop.unready_models() asks each model's own
server; the tick drops the ones it names.

Run: python3 scripts/tests/test_dispatch_unready_model.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dispatch_loop  # noqa: E402


class NoServer(Exception):
    pass


class Fake:
    def __init__(self, url, up):
        self.url, self.up, self.calls = url, up, 0

    def health(self):
        self.calls += 1
        return self.up


def test_a_loading_server_is_named():
    swift = Fake("http://vllm", False)
    got = dispatch_loop.unready_models(["swift"], resolve=lambda m: (swift, "x"))
    assert list(got) == ["swift"], got
    assert "does not answer" in got["swift"], got


def test_noserver_is_not_ready():
    def resolve(m):
        raise NoServer(f"'{m}' is offered by vllm-swift, and none answers")
    got = dispatch_loop.unready_models(["swift"], resolve=resolve)
    assert "none answers" in got["swift"], got


def test_an_answering_server_keeps_its_work():
    tp = Fake("http://thinkpad", True)
    assert dispatch_loop.unready_models(["qwen"], resolve=lambda m: (tp, "x")) == {}


def test_one_probe_per_server_and_only_the_down_one_is_dropped():
    tp, swift = Fake("http://thinkpad", True), Fake("http://vllm", False)
    srv = {"qwen": tp, "gemma": tp, "swift": swift}
    got = dispatch_loop.unready_models(["qwen", "gemma", "swift"], resolve=lambda m: (srv[m], "x"))
    assert set(got) == {"swift"}, got
    assert tp.calls == 1 and swift.calls == 1, (tp.calls, swift.calls)


def test_the_tick_uses_it_before_scheduling():
    src = Path(dispatch_loop.__file__).read_text(encoding="utf-8")
    i, j = src.find("_down = unready_models(priority_models)"), src.find("= schedule(priority_models")
    assert 0 < i < j, "tick() must drop unready models before schedule() hands out lanes"


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    bad = 0
    for n, f in tests:
        try:
            f()
            print(f"PASS {n}")
        except Exception as e:                                      # noqa: BLE001
            bad += 1
            print(f"FAIL {n}: {type(e).__name__}: {e}")
    print(f"{len(tests) - bad} passed, {bad} failed")
    sys.exit(1 if bad else 0)
