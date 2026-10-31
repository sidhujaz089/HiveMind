import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import reviewq


def test_missing_verdict_names_examined_namespace():
    # t647: a MISSING verdict is illegal unless it records the ref
    # namespace(s) examined. The flat 'branch X MISSING' hides that
    # refs/remotes/origin/X was never checked (L-114 / L-115).
    s = reviewq.missing_branch_status("coder/tT647")
    assert "MISSING" in s, s
    # the examined namespace must be recorded, not a bare flat verdict
    assert "refs/remotes/origin/coder/tT647" in s or "origin/coder/tT647" in s, s
    # the exact flat, namespace-hiding string must NOT be the verdict
    assert s != "branch coder/tT647 MISSING", s


def test_two_distinct_names_are_not_one_placeholder():
    a = reviewq.missing_branch_status("coder/tT647")
    b = reviewq.missing_branch_status("coder/tABSENT")
    assert "tT647" in a and "tABSENT" in b
    assert a != b, "two distinct branches must not map to one reused string"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("ok", fn.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL", fn.__name__, e)
        except Exception as e:
            failed += 1
            print("ERROR", fn.__name__, type(e).__name__, e)
    raise SystemExit(1 if failed else 0)
