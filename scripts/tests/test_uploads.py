#!/usr/bin/env python3
"""Fixtures for the feedback tool's upload path.

L-087: this path silently discarded every non-image upload and reported success, which cost
an entire Anthropic data export. The assertion that matters is not "an image works" -- that
never broke. It is that a file the code has no opinion about SURVIVES, because the failure
mode was a validation branch quietly choosing not to write.
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "projects" / "app-repo"))
import feedback  # noqa: E402
import taskstore as ts  # noqa: E402

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


def main():
    tmp = Path(tempfile.mkdtemp())
    ts.TASKS, ts.DONE = tmp / "tasks", tmp / "tasks" / "done"
    ts.QUEUE, ts.LOCK = ts.TASKS / "QUEUE.md", ts.TASKS / ".lock"
    ts.ATTACH = tmp / "attachments"
    try:
        tid = ts.add("upload fixture", "workspace")

        print("Every file type survives -- nothing is dropped:")
        cases = [("export.zip", b"PK\x03\x04binary-ish\x00\xff"),
                 ("conversations.json", b'[{"uuid": "abc"}]'),
                 ("shot.png", b"\x89PNG\r\n\x1a\n"),
                 ("log.txt", b"line one\nline two\n"),
                 ("no-extension", b"still has bytes"),
                 ("weird.TAR.GZ", b"\x1f\x8bcompressed")]
        for name, blob in cases:
            src = ts.ATTACH / f".incoming-{name.replace('/', '_')}"
            src.parent.mkdir(parents=True, exist_ok=True)
            src.write_bytes(blob)
            saved = feedback.save_uploads([("shots", name, src, len(blob))], tid)
            ok = len(saved) == 1 and (ts.ATTACH / saved[0]).read_bytes() == blob
            report(f"{name:<20} stored byte-for-byte", ok, True)

        _, body = ts.find(tid)
        report("every upload is recorded on the task",
               body.count("- " + tid), len(cases))
        report("the original filename is kept", "was export.zip" in body, True)

        print("\nThe extension is sanitised, not trusted:")
        for name, want in [("x.py", ".py"), ("x.service", ".service"),
                           ("../../etc/passwd", ".bin"), ("x.<script>", ".script"),
                           ("noext", ".bin")]:
            report(f"{name:<20} -> {want}", feedback.safe_ext(name), want)

        print("\nA sanitised name cannot escape the attachments directory:")
        src = ts.ATTACH / ".incoming-traversal"
        src.write_bytes(b"x")
        saved = feedback.save_uploads([("shots", "../../../etc/passwd", src, 1)], tid)
        landed = (ts.ATTACH / saved[0]).resolve()
        report("stays inside attachments/", landed.parent == ts.ATTACH.resolve(), True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # --- the page handlers must not raise on any app name ------------------------------
    # t159's accepted patch called `safe_app(app)`. `safe_app` is a LOCAL VARIABLE in a
    # different method, not a function, so the apps page raised
    # `NameError: name 'safe_app' is not defined` -- for exactly the case the patch existed to
    # fix. Twenty-two suites went green because NOTHING exercised _apps(). Caught by review,
    # which is the expensive way; this is the durable version, per the disposable-versus-
    # permanent rule in MECHANICAL-FIXES.
    print("\n_apps() survives every shape of app name:")
    h = feedback.Handler.__new__(feedback.Handler)
    sent = []
    h._send = lambda *a, **k: sent.append(a)
    for name, arg in [("a real app", "good-dictator-app"),
                      ("an app with no release notes", "indoor-voice"),
                      ("an unknown app", "no-such-app"),
                      ("an empty name", ""),
                      ("a path-traversal attempt", "../../etc/passwd"),
                      ("None", None)]:
        try:
            h._apps(app=arg)
            ok = True
        except Exception as e:
            ok = f"{type(e).__name__}: {e}"
        report(f"  {name}", ok, True)
    report("and it actually rendered something", len(sent) > 0, True)

    # A traversal string must never end up as the SELECTED app. t159's third patch validated
    # with `(PROJECTS / app).is_dir()`, and "..", "../.." and "../../.." are all directories,
    # so they passed as real apps. No file disclosure was demonstrated, but a URL parameter
    # reaching a filesystem check unsanitised does not belong in a file that already has a
    # sanitising convention. Asserted behaviourally so it holds for any shape of fix.
    print("\nA traversal string is never the selected app:")
    # ".." alone is not usable as a probe: two dots occur incidentally in rendered HTML
    # (ellipses, relative URLs), so it flags even on correct code. The longer forms are
    # unambiguous and cover the same defect.
    for probe in ("../..", "../../..", "a/../../b"):
        page = []
        h2 = feedback.Handler.__new__(feedback.Handler)
        h2._send = lambda code, body, *a, **k: page.append(body)
        try:
            h2._apps(app=probe)
            rendered = "".join(str(x) for x in page)
            leaked = probe in rendered
        except Exception as e:
            leaked = f"{type(e).__name__}: {e}"
        report(f"  {probe!r} does not reach the page", leaked, False)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
