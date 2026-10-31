#!/usr/bin/env python3
"""Phone notifications: the toggles gate them, and a dead device is dropped rather than retried.

Nothing here touches the network or the real keypair -- every path is redirected into a temp
directory, which is also what proves the guard that keeps keys out of the repository.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))


def main():
    import notify
    # The sender test exercises the real signing path, which imports pywebpush -- venv-only
    # (PEP 668), exactly as notify.py's own --send path deals with it: re-exec once under the
    # repo venv so `python3 scripts/tests/test_notify.py` works from system python too.
    if notify.VENV.is_file() and sys.executable != str(notify.VENV):
        try:
            import pywebpush                                          # noqa: F401
        except ImportError:
            if not os.environ.get("NOTIFY_TEST_REEXEC"):
                os.environ["NOTIFY_TEST_REEXEC"] = "1"
                raise SystemExit(subprocess.call(
                    [str(notify.VENV), str(Path(__file__).resolve())], env=os.environ))
    fails = []

    def ck(label, got, want):
        ok = got == want
        print(f"  [{'ok' if ok else 'BAD'}]    {label} -> {got}")
        if not ok:
            fails.append(f"{label}: expected {want}, got {got}")

    # THE GUARD FIRST. A VAPID private key or a device credential inside the repo would be
    # pushed to the remote, so this has to refuse before anything else is trusted.
    try:
        notify._outside_repo(REPO / "workspace" / "keys.json")
        ck("a repo path is refused for secrets", "allowed", "refused")
    except SystemExit:
        ck("a repo path is refused for secrets", "refused", "refused")
    ck("a path outside the repo is allowed",
       bool(notify._outside_repo(Path("/tmp/x.json"))), True)

    real = (notify.PREFS, notify.SUBS, notify.VAPID)
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        notify.PREFS, notify.SUBS, notify.VAPID = (tmp / "prefs.json", tmp / "subs.json",
                                                   tmp / "vapid.json")
        try:
            # --- the toggles ---------------------------------------------------------
            ck("every event defaults to ON", sorted(k for k, v in notify.prefs().items() if v),
               sorted(notify.EVENTS))
            notify.set_pref("held", False)
            ck("a toggle persists", notify.prefs()["held"], False)
            ck("the others are untouched", notify.prefs()["console"], True)
            notify.set_pref("held", True)
            ck("it toggles back", notify.prefs()["held"], True)
            try:
                notify.set_pref("not-an-event", True)
                ck("an unknown event is refused", "allowed", "refused")
            except SystemExit:
                ck("an unknown event is refused", "refused", "refused")

            # A NEW EVENT DEFAULTS ON even though the saved file predates it: an alert nobody
            # has opted out of should be loud, not silently absent.
            notify.PREFS.write_text(json.dumps({"held": False}))
            ck("an event missing from the file defaults ON", notify.prefs()["console"], True)
            ck("and one present in the file keeps its value", notify.prefs()["held"], False)
            notify.set_pref("held", True)

            # --- refusing to send, with a reason -------------------------------------
            sent, dropped, why = notify.send("held", "t", "b", sender=lambda s, p: 201)
            ck("no keypair means nothing is sent", (sent, "VAPID" in why), (0, True))

            notify.init_keys()
            ck("a keypair is made outside the repo", notify.VAPID.is_file(), True)
            sent, dropped, why = notify.send("held", "t", "b", sender=lambda s, p: 201)
            ck("no subscribed device means nothing is sent", (sent, "subscribed" in why),
               (0, True))

            sub = {"endpoint": "https://push.example/aaa", "keys": {"auth": "A", "p256dh": "B"}}
            notify.add_subscription(sub)
            notify.set_pref("held", False)
            sent, dropped, why = notify.send("held", "t", "b", sender=lambda s, p: 201)
            ck("an event switched OFF sends nothing", (sent, "switched off" in why), (0, True))
            notify.set_pref("held", True)

            # --- sending -------------------------------------------------------------
            calls = []
            sent, dropped, why = notify.send("held", "t", "b",
                                             sender=lambda s, p: calls.append(s["endpoint"]) or 201)
            ck("an enabled event reaches the device", (sent, dropped), (1, 0))
            ck("the payload went to the right endpoint", calls, ["https://push.example/aaa"])

            # --- subscriptions -------------------------------------------------------
            notify.add_subscription(sub)
            ck("re-subscribing the same endpoint is not a duplicate", len(notify.subscriptions()), 1)
            notify.add_subscription({"endpoint": "https://push.example/bbb",
                                     "keys": {"auth": "C", "p256dh": "D"}})
            ck("a second device is added", len(notify.subscriptions()), 2)
            try:
                notify.add_subscription({"endpoint": "https://push.example/ccc"})
                ck("a subscription with no keys is refused", "allowed", "refused")
            except SystemExit:
                ck("a subscription with no keys is refused", "refused", "refused")

            # A DEAD DEVICE IS DROPPED, NOT RETRIED. A revoked or reinstalled browser answers
            # 410 for ever, and a list that only grows makes every future alert pay for it.
            def gone(s, p):
                return 410 if s["endpoint"].endswith("aaa") else 201
            sent, dropped, why = notify.send("held", "t", "b", sender=gone)
            ck("a 410 drops that device and the other still gets it", (sent, dropped), (1, 1))
            ck("the dropped endpoint is gone from the file",
               [s["endpoint"] for s in notify.subscriptions()], ["https://push.example/bbb"])

            sent, dropped, why = notify.send("held", "t", "b", sender=lambda s, p: 404)
            ck("a 404 drops it too", dropped, 1)
            ck("and the list is now empty", notify.subscriptions(), [])

            # A REFUSAL FROM THE PUSH SERVICE THAT IS NOT 404/410 KEEPS THE DEVICE -- a 500 is
            # the service having a bad day, not the phone being gone.
            notify.add_subscription(sub)
            sent, dropped, why = notify.send("held", "t", "b", sender=lambda s, p: 500)
            ck("a 500 neither sends nor drops", (sent, dropped), (0, 0))
            ck("the device is kept", len(notify.subscriptions()), 1)

            # THE REAL SIGNING PATH, which no fake sender above ever exercised. The bug it
            # pins: installed py_vapid's from_string expects raw base64, notify.py handed it
            # a PEM, and EVERY push died with 'ValueError: Could not deserialize key data ...
            # ASN.1 parsing error' (measured 2026-09-18 via --test, phone subscribed).
            # A VALID encryption pair is supplied so the payload step cannot mask the result;
            # the discarded port then fails at TRANSPORT. The old signing bug is unmistakable
            # by message: 'deserializ' / 'ASN.1' appears in NO legitimate failure of this call.
            import base64
            from cryptography.hazmat.primitives.asymmetric import ec
            sk = ec.generate_private_key(ec.SECP256R1())
            pt = sk.public_key().public_numbers()
            p256dh = base64.urlsafe_b64encode(pt.x.to_bytes(32, "big")
                                              + pt.y.to_bytes(32, "big")).rstrip(b"=").decode()
            auth = base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode()
            go = notify._real_sender(notify.keys())
            err = None
            try:
                go({"endpoint": "https://127.0.0.1:9/push", "keys": {"auth": auth,
                                                                      "p256dh": p256dh}}, "{}")
            except Exception as e:
                err = e
            # The old bug's signature is in the message; no legitimate failure of this call
            # carries it. Anything else (a transport error, or this box's egress proxy
            # answering the request) means signing AND encryption both ran clean.
            signing_ok = not (err is not None and ("deserializ" in str(err).lower()
                                                   or "ASN.1" in str(err)))
            ck("sender signs a payload with the real keypair",
               "signing ok" if signing_ok else (str(err) or "")[:80], "signing ok")

            # KEYS ARE NEVER REGENERATED over an existing file: every device subscribed against
            # the current public key, and replacing it silently orphans all of them.
            before = notify.keys()["public_key"]
            _, made = notify.init_keys()
            ck("init does not overwrite an existing keypair", (made, notify.keys()["public_key"]),
               (False, before))
        finally:
            notify.PREFS, notify.SUBS, notify.VAPID = real

    for x in fails:
        print("FAIL:", x)
    print("test_notify:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
