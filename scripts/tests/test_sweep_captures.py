#!/usr/bin/env python3
"""Fixtures for the capture sweep.

`chat/` has no git rollback under D-22, so a half-completed move is unrecoverable. The
assertions that matter are the refusals: a mismatched copy must leave the source alone, and
a name collision holding DIFFERENT content must refuse rather than pick a winner.

"It copied a file" was never the risk. "It removed the only copy" is.
"""
import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "scripts" / "sweep_captures.py"
PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


def run(tmp, *args):
    src = SCRIPT.read_text().replace(
        "REPO = Path(__file__).resolve().parent.parent", f"REPO = Path({str(tmp)!r})")
    shim = tmp / "under_test.py"
    shim.write_text(src)
    return subprocess.run([sys.executable, str(shim), *args], capture_output=True, text=True)


def setup(files, existing=None):
    tmp = Path(tempfile.mkdtemp())
    (tmp / "workspace" / "chat-live").mkdir(parents=True)
    (tmp / "chat" / "captures").mkdir(parents=True)
    for n, body in files.items():
        (tmp / "workspace" / "chat-live" / n).write_text(body)
    for n, body in (existing or {}).items():
        (tmp / "chat" / "captures" / n).write_text(body)
    return tmp


def main():
    print("The happy path:")
    tmp = setup({"a.jsonl": '{"role":"user","text":"hi","session":"s1"}\n'})
    r = run(tmp, "--apply")
    live = tmp / "workspace" / "chat-live" / "a.jsonl"
    dest = tmp / "chat" / "captures" / "a.jsonl"
    report("copied into the corpus", dest.is_file(), True)
    report("source removed after verifying", live.exists(), False)
    report("exit 0", r.returncode, 0)
    shutil.rmtree(tmp, ignore_errors=True)

    print("\nReporting without --apply must touch NOTHING:")
    tmp = setup({"a.jsonl": '{"role":"user","text":"hi"}\n'})
    run(tmp)
    report("source still there", (tmp / "workspace/chat-live/a.jsonl").is_file(), True)
    report("nothing written to the corpus",
           list((tmp / "chat/captures").iterdir()), [])
    shutil.rmtree(tmp, ignore_errors=True)

    print("\nA name collision with DIFFERENT content must refuse, not overwrite:")
    tmp = setup({"a.jsonl": "NEW CONTENT\n"}, existing={"a.jsonl": "OLD AND IRREPLACEABLE\n"})
    r = run(tmp, "--apply")
    report("refused", r.returncode, 1)
    report("the existing corpus file is untouched",
           (tmp / "chat/captures/a.jsonl").read_text(), "OLD AND IRREPLACEABLE\n")
    report("and the source is kept", (tmp / "workspace/chat-live/a.jsonl").is_file(), True)
    shutil.rmtree(tmp, ignore_errors=True)

    print("\nA name collision with IDENTICAL content is safe to sweep:")
    same = "SAME BYTES\n"
    tmp = setup({"a.jsonl": same}, existing={"a.jsonl": same})
    r = run(tmp, "--apply")
    report("swept", r.returncode, 0)
    report("source removed", (tmp / "workspace/chat-live/a.jsonl").exists(), False)
    shutil.rmtree(tmp, ignore_errors=True)

    print("\nHidden/partial files are not swept:")
    tmp = setup({".partial.jsonl": "half\n", "real.jsonl": "whole\n"})
    run(tmp, "--apply")
    report("dotfile left alone", (tmp / "workspace/chat-live/.partial.jsonl").is_file(), True)
    report("real file swept", (tmp / "chat/captures/real.jsonl").is_file(), True)
    shutil.rmtree(tmp, ignore_errors=True)

    print("\nA copy that does not match must leave the source alone:")
    # Found by scripts/mutate.py 2026-08-24. Every fixture above exercised the SUCCESS path,
    # so deleting the `if after != before` check changed nothing and the suite still passed.
    # The verify-before-remove guarantee was claimed in a commit message and never tested.
    # It cannot be provoked with real files, so the copy itself is sabotaged.
    tmp = setup({"a.jsonl": "REAL CONTENT\n"})
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("sc_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.REPO = tmp
    mod.LIVE = tmp / "workspace" / "chat-live"
    mod.DEST = tmp / "chat" / "captures"

    real_copy = mod.shutil.copy2
    def corrupting_copy(src, dst):
        real_copy(src, dst)
        Path(dst).write_text("CORRUPTED IN TRANSIT\n")   # what a bad disk looks like
    mod.shutil.copy2 = corrupting_copy
    sys.argv = ["x", "--apply"]
    rc = mod.main()
    mod.shutil.copy2 = real_copy
    report("refused", rc, 1)
    report("the source SURVIVES a bad copy",
           (tmp / "workspace/chat-live/a.jsonl").is_file(), True)
    report("and it still holds the real content",
           (tmp / "workspace/chat-live/a.jsonl").read_text(), "REAL CONTENT\n")
    shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
