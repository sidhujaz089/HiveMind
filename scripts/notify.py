#!/usr/bin/env python3
"""Push a notification to Jas's phone from the box, when a local model needs him.

    python3 scripts/notify.py --status                  what is on, and how many devices
    python3 scripts/notify.py --init                    make the VAPID keypair (once)
    python3 scripts/notify.py --public-key              the key the browser needs to subscribe
    python3 scripts/notify.py --set held off            toggle one event
    python3 scripts/notify.py --send held "title" "body"  [--url PATH]
    python3 scripts/notify.py --test                    send one to every subscribed device

Jas, 2026-09-15: "Sometimes when I go to a website, it asks me if I want to receive
notifications. Can the HiveMind Hub enable notifications so if it needs to alert me from the
local models, it does ping my phone?" Samsung Z Fold, Samsung Internet -- Chromium-based, so
Web Push works from an ordinary tab and no Home Screen install is needed.

WHY THE PHONE AT ALL. The models run unattended, and every alert this replaces was a line in a
cron log: on 2026-09-15 the same review failed 40 times over 7.7 hours, thirteen chunks sat
parked for a day, and a delivered branch was rebuilt for half an hour because nothing told
anyone. Claude cannot be the channel -- the runs that most need an alert happen overnight when
no session is open.

WHERE THE SECRETS LIVE, and they are NOT in this repository. The VAPID private key signs every
push; a subscription is a credential that lets its holder push to that device. Both live in
~/.secrets/ beside the other keys, and _outside_repo() refuses to write either anywhere else.
Only the on/off toggles are in the repo, because they are booleans.

SENDING NEEDS THE REPO VENV. pywebpush is installed in .venv and cannot go in system python
(PEP 668). Rather than make every caller know that, this re-executes itself under .venv when
the import is missing, so `python3 scripts/notify.py --send ...` works from anywhere.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SECRETS = Path.home() / ".secrets"
VAPID = SECRETS / "webpush-vapid.json"
SUBS = SECRETS / "webpush-subscriptions.json"
PREFS = REPO / "workspace" / "notify-prefs.json"
VENV = REPO / ".venv" / "bin" / "python"
if not VENV.is_file():
    # A WORKTREE HAS NO .venv (it is gitignored), so the re-exec under the venv silently did not happen and
    # the real signing path died on `import py_vapid` -- red in every delivery worktree (t9550, 2026-10-29).
    # The main checkout's venv is the same environment: find it through git's common dir.
    try:
        import subprocess as _sp
        _common = _sp.run(["git", "-C", str(REPO), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                          capture_output=True, text=True, timeout=10).stdout.strip()
        if _common:
            _main = Path(_common).parent / ".venv" / "bin" / "python"
            if _main.is_file():
                VENV = _main
    except Exception as _e:                                  # noqa: BLE001 -- said, the repo path stays
        print(f"  NOTE: main checkout venv not found ({type(_e).__name__}: {_e})", file=sys.stderr)

# THE EVENTS, and every one of them is something that stalled silently today. The key is what
# callers pass to --send; the text is what the Status tab shows beside its toggle.
EVENTS = {
    "held": "A task was held after repeated failures -- its brief or oracle needs fixing",
    "console": "A local model asked a question and is waiting for you",
    "review-ready": "A review branch is built and waiting to be merged",
    "overnight": "An overnight or long-running job finished, or died",
    "model-missing": "A role's model is no longer installed (and again when it is back)",
    "needs-you": "A model decided it needs your answer and put a task on your Review list",
}
DEFAULTS = {k: True for k in EVENTS}


def _outside_repo(p):
    """Refuse to put a key or a device credential anywhere that git can reach."""
    try:
        Path(p).resolve().relative_to(REPO)
    except ValueError:
        return Path(p)
    raise SystemExit(f"REFUSING: {p} is inside {REPO}. Keys and subscriptions live in "
                     f"{SECRETS}, which is not a git repository.")


def prefs():
    if PREFS.is_file():
        try:
            on = json.loads(PREFS.read_text(encoding="utf-8"))
        except ValueError:
            on = {}
    else:
        on = {}
    # An event added after the file was written defaults to ON: a new alert nobody has opted
    # out of should be louder than silent, and the Status tab shows it immediately.
    return {k: bool(on.get(k, DEFAULTS[k])) for k in EVENTS}


def set_pref(event, on):
    if event not in EVENTS:
        raise SystemExit(f"unknown event {event!r}; known: {', '.join(sorted(EVENTS))}")
    cur = prefs()
    cur[event] = bool(on)
    PREFS.parent.mkdir(parents=True, exist_ok=True)
    PREFS.write_text(json.dumps(cur, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return cur


def subscriptions():
    _outside_repo(SUBS)
    if not SUBS.is_file():
        return []
    try:
        return json.loads(SUBS.read_text(encoding="utf-8"))
    except ValueError:
        return []


def save_subscriptions(subs):
    _outside_repo(SUBS)
    SECRETS.mkdir(parents=True, exist_ok=True)
    SUBS.write_text(json.dumps(subs, indent=1) + "\n", encoding="utf-8")
    SUBS.chmod(0o600)


def add_subscription(sub):
    """Store a browser's subscription. Keyed on endpoint, so re-subscribing is not a duplicate."""
    if not (isinstance(sub, dict) and sub.get("endpoint") and (sub.get("keys") or {}).get("auth")):
        raise SystemExit("REFUSED: that is not a push subscription (needs endpoint and keys)")
    subs = [s for s in subscriptions() if s.get("endpoint") != sub["endpoint"]]
    subs.append({"endpoint": sub["endpoint"], "keys": sub["keys"],
                 "added": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "agent": sub.get("agent", "")})
    save_subscriptions(subs)
    return len(subs)


def drop_subscription(endpoint):
    subs = [s for s in subscriptions() if s.get("endpoint") != endpoint]
    save_subscriptions(subs)
    return len(subs)


def keys():
    _outside_repo(VAPID)
    if not VAPID.is_file():
        return None
    return json.loads(VAPID.read_text(encoding="utf-8"))


def init_keys():
    """Make the VAPID keypair once. Existing keys are never overwritten: every device that has
    subscribed did so against the current public key, and replacing it silently orphans them."""
    _outside_repo(VAPID)
    if VAPID.is_file():
        return keys(), False
    import base64
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    priv = ec.generate_private_key(ec.SECP256R1())
    pem = priv.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()
    raw = priv.public_key().public_bytes(serialization.Encoding.X962,
                                         serialization.PublicFormat.UncompressedPoint)
    pub = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    SECRETS.mkdir(parents=True, exist_ok=True)
    VAPID.write_text(json.dumps({"private_pem": pem, "public_key": pub,
                                 "subject": "mailto:owner@example.invalid",
                                 "made": time.strftime("%Y-%m-%d")}, indent=1) + "\n",
                     encoding="utf-8")
    VAPID.chmod(0o600)
    # Key generation deliberately uses `cryptography` only, which system python has. Making it
    # need the venv would mean --init failed on a box that can still perfectly well hold a key.
    return keys(), True


def send(event, title, body, url="/", sender=None):
    """Push to every subscribed device, if this event is enabled. Returns (sent, dropped, why).

    A dead subscription is REMOVED, not retried. A browser that has revoked permission or been
    reinstalled answers 404 or 410 for ever, and a list that only grows would mean every future
    alert pays for every phone that has ever subscribed.
    """
    on = prefs()
    if not on.get(event, False):
        return 0, 0, f"{event} is switched off in the Status tab"
    k = keys()
    if not k:
        return 0, 0, "no VAPID keypair yet -- run: python3 scripts/notify.py --init"
    subs = subscriptions()
    if not subs:
        return 0, 0, "no device has subscribed yet -- open the Hub Status tab on the phone"
    payload = json.dumps({"title": title, "body": body, "url": url, "event": event})
    push = sender or _real_sender(k)
    sent = dropped = 0
    for s in list(subs):
        code = push(s, payload)
        if code in (404, 410):
            drop_subscription(s["endpoint"])
            dropped += 1
        elif 200 <= code < 300:
            sent += 1
    return sent, dropped, ""


def _real_sender(k):
    from py_vapid import Vapid
    from pywebpush import webpush, WebPushException

    # The installed py_vapid.from_string expects raw base64 (it b64urldecodes the whole
    # string), so feeding it this PEM died before any network call: 'ValueError: Could not
    # deserialize key data ... ASN.1 parsing error' -- measured 2026-09-18, every push
    # failed, phone subscribed. pywebpush accepts a Vapid instance outright (its first
    # branch), and from_pem is the path that handles PEM, so sign from the instance.
    v = Vapid.from_pem(k["private_pem"].encode("utf8"))

    def go(sub, payload):
        try:
            r = webpush(subscription_info={"endpoint": sub["endpoint"], "keys": sub["keys"]},
                        data=payload, vapid_private_key=v,
                        vapid_claims={"sub": k["subject"]}, timeout=20)
            return getattr(r, "status_code", 201)
        except WebPushException as e:
            return getattr(getattr(e, "response", None), "status_code", 0)
    return go


def _reexec_in_venv():
    """pywebpush lives in .venv only, so a caller on system python is sent there once."""
    if os.environ.get("NOTIFY_REEXEC") or not VENV.is_file():
        return
    try:
        import pywebpush                                                # noqa: F401
    except ImportError:
        os.environ["NOTIFY_REEXEC"] = "1"
        raise SystemExit(subprocess.call([str(VENV), str(Path(__file__).resolve())] + sys.argv[1:],
                                         env=os.environ))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--public-key", action="store_true")
    ap.add_argument("--set", nargs=2, metavar=("EVENT", "ON|OFF"))
    ap.add_argument("--subscribe", metavar="JSON")
    ap.add_argument("--send", nargs=3, metavar=("EVENT", "TITLE", "BODY"))
    ap.add_argument("--url", default="/")
    ap.add_argument("--test", action="store_true")
    a = ap.parse_args()

    if a.init:
        k, made = init_keys()
        print(f"  VAPID keypair {'created' if made else 'already present'}: {VAPID}")
        print(f"  public key: {k['public_key']}")
        return 0
    if a.public_key:
        k = keys()
        if not k:
            print("no keypair yet -- run --init", file=sys.stderr)
            return 1
        print(k["public_key"])
        return 0
    if a.set:
        event, val = a.set
        cur = set_pref(event, str(val).lower() in ("on", "true", "1", "yes"))
        print(f"  {event} -> {'on' if cur[event] else 'off'}")
        return 0
    if a.subscribe:
        n = add_subscription(json.loads(a.subscribe))
        print(f"  subscribed; {n} device(s) registered")
        return 0
    if a.send or a.test:
        _reexec_in_venv()
        if a.test:
            ev, title, body = "held", "HiveMind test", "If you can read this, push works."
        else:
            ev, title, body = a.send
        sent, dropped, why = send(ev, title, body, a.url)
        print(f"  sent {sent}, dropped {dropped}" + (f" -- {why}" if why else ""))
        return 0 if (sent or not why) else 1

    on = prefs()
    print(f"\n  VAPID keypair: {'present' if keys() else 'NOT MADE -- run --init'}")
    print(f"  devices subscribed: {len(subscriptions())}")
    print("\n  events:")
    for k in sorted(EVENTS):
        print(f"    {'ON ' if on[k] else 'off'}  {k:14s} {EVENTS[k]}")
    print(f"\n  toggles: {PREFS.relative_to(REPO)}   keys and devices: {SECRETS}/\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
