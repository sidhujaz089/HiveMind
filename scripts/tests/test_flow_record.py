#!/usr/bin/env python3
"""The Flow chart is the system of record (Jas, 2026-10-01).

"any new workflow or process must first be defined in the flowchart before it can be implemented
or executed elsewhere ... any permanent script must be represented in the flowchart, and if it is
not documented there, it should not be eligible for approval or deployment."
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
fails = []


def ck(label, ok):
    print(f"  [{'ok' if ok else 'BAD'}]  {label}")
    if not ok:
        fails.append(label)


def main():
    import flow_coverage as FC
    import flow_lint as FL
    flow = {"tasks": [{"id": "build", "name": "Build", "worker": "coder.py", "command": "coder.py",
                       "queue": "", "note": "", "outcomes": [], "ends": []}], "links": [], "sources": [], "services": [{"id": "svc", "name": "S", "unit": "x.service"}],
            "helpers": [{"script": "scripts/jsonx.py", "serves": ["build"], "what": "parse"}]}
    lib = {"projects/local-inference/coder.py": {"kind": "tool"}, "scripts/jsonx.py": {"kind": "tool"},
           "scripts/orphan.py": {"kind": "tool"}, "scripts/tests/test_x.py": {"kind": "test"}}
    ck("a script a task names is on the chart", FC.on_chart("projects/local-inference/coder.py", flow))
    ck("a helper is on the chart", FC.on_chart("scripts/jsonx.py", flow))
    ck("an unlisted script is not", not FC.on_chart("scripts/orphan.py", flow))
    ck("tests are exempt; the orphan is the one gap", FC.uncovered(flow, lib) == ["scripts/orphan.py"])

    # lint: helper ops
    after = FL.apply(flow, [{"op": "add_helper", "helper": {"script": "scripts/loudcap.py", "serves": ["svc"],
                                                            "what": "caps"}}])
    ck("add_helper adds", any(h["script"] == "scripts/loudcap.py" for h in after["helpers"]))
    try:
        FL.apply(flow, [{"op": "add_helper", "helper": {"script": "scripts/jsonx.py", "serves": [], "what": "x"}}])
        ck("a duplicate helper is refused", False)
    except FL.EditError:
        ck("a duplicate helper is refused", True)
    bad = FL.apply(flow, [{"op": "edit_helper", "script": "scripts/jsonx.py", "set": {"serves": ["nope"]}}])
    ck("serving an unknown task is a problem", any("not a task or service" in p for p in FL.problems(bad)))
    ck("a helper whose file is gone is a problem",
       any("does not exist on disk" in p for p in FL.problems(
           FL.apply(flow, [{"op": "add_helper", "helper": {"script": "scripts/gone.py", "serves": ["build"], "what": "x"}}]))))

    # the gates, end to end on the real tools
    lib_src = (ROOT / "scripts" / "library.py").read_text(encoding="utf-8")
    ck("library.py refuses to register an off-chart script", "is not on the Flow chart" in lib_src
       and "flow_coverage.on_chart(p)" in lib_src)
    import flow_change
    try:
        flow_change.add_helper("scripts/jsonx.py", [], "x")
        ck("a new helper must serve something", False)
    except ValueError:
        ck("a new helper must serve something", True)
    import truncation_review as TR
    ck("script review marks an off-chart script off-flow, without a model",
       TR.off_flow("scripts/definitely_not_on_chart.py")[0]["check"] == "off-flow")
    ck("and a script on the chart gets no such finding", TR.off_flow("scripts/dispatch_loop.py") == [])
    import session_review as SR
    ck("session review asks for off-flow processes", '"off-flow"' in SR.EXTRACT and "{flow}" in SR.EXTRACT)
    ck("and is shown the chart", "SUPPORTING SCRIPTS" in SR.flow_summary())
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "flow_coverage.py")], capture_output=True,
                       text=True, timeout=120)
    ck(f"the live chart covers every permanent script: {r.stdout.splitlines()[0] if r.stdout else r.stderr[-200:]}",
       r.returncode == 0)
    for f in fails:
        print("FAIL:", f)
    print("test_flow_record:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
