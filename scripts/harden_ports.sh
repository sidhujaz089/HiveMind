#!/bin/bash
# harden_ports.sh -- close the HiveMind's ports to everything except the tailnet and the home LAN.
#
#   sudo bash ~/ai-workspace/scripts/harden_ports.sh            # show what it will do, change nothing
#   sudo bash ~/ai-workspace/scripts/harden_ports.sh --apply    # do it
#
# Jas, 2026-09-14: "Harden the ports if there are no downside." Findings that day (t111):
#   * `ufw status` was INACTIVE -- nothing filtered anything.
#   * ssh listened on every interface with password logins at the default (on). The last 30
#     days of logs show ONLY public-key logins, from his laptop, so turning passwords off
#     costs nothing he uses.
#   * The box has a GLOBAL IPv6 address (2001:56a:...). IPv6 has no router NAT, so ports on
#     [::] can be reachable from the internet regardless of port forwarding.
#   * ttyd (the Hub's web shell, writable, no password) was started with --interface tailscale0
#     but listens on 0.0.0.0:8094 -- any device on the home Wi-Fi could open a shell.
#   * GNOME remote desktop on 3389/3390, every interface.
#
# WHAT "NO DOWNSIDE" MEANS HERE, rule by rule:
#   allow everything on tailscale0     -- the Hub, ssh, RDP, web shell keep working over the tailnet
#   allow everything from the home LAN -- ssh from the laptop, RDP, printing and discovery unchanged
#     (IPv4 192.168.1.0/24, the current IPv6 LAN /64, and link-local fe80::/10)
#   EXCEPT the web shell (8094) from the LAN -- it has no password; the Hub reaches it over the
#     tailnet, which stays allowed. This is the one thing a LAN-only device loses.
#   allow 41641/udp                    -- lets tailscale make direct connections (speed)
#   deny all other incoming, allow all outgoing
#   ssh: PasswordAuthentication no, KbdInteractiveAuthentication no, PermitRootLogin no
#
# Existing ssh sessions are not dropped. Nothing listening is stopped or reconfigured.
# The ssh change is validated with `sshd -t` and removed again if it does not validate.
#
# UNDO:
#   sudo ufw disable
#   sudo rm /etc/ssh/sshd_config.d/10-hivemind-hardening.conf
set -u
APPLY=0; [ "${1:-}" = "--apply" ] && APPLY=1
if [ "$(id -u)" -ne 0 ]; then echo "Run with sudo:  sudo bash $0 ${1:-}"; exit 1; fi

LAN4="192.168.1.0/24"
V6ADDR=$(ip -6 -o addr show dev wlP9s9 scope global 2>/dev/null | awk '{print $4}' | grep -v '^fd' | head -1)
LAN6=""
if [ -n "$V6ADDR" ]; then
    LAN6=$(python3 -c "import ipaddress,sys;print(ipaddress.ip_interface(sys.argv[1]).network)" "$V6ADDR")
fi
DROPIN=/etc/ssh/sshd_config.d/10-hivemind-hardening.conf

echo "== BEFORE =="
ufw status verbose 2>&1 | head -5
echo
echo "== PLAN =="
echo "  ufw default deny incoming / allow outgoing"
echo "  ufw allow in on tailscale0"
echo "  ufw deny from $LAN4 to any port 8094 proto tcp   (web shell, no password)"
[ -n "$LAN6" ] && echo "  ufw deny from $LAN6 to any port 8094 proto tcp"
echo "  ufw allow from $LAN4"
[ -n "$LAN6" ] && echo "  ufw allow from $LAN6   (current IPv6 LAN; if the ISP changes the prefix, LAN devices fall back to IPv4)"
echo "  ufw allow from fe80::/10"
echo "  ufw allow 41641/udp"
echo "  ufw enable"
echo "  $DROPIN: PasswordAuthentication no, KbdInteractiveAuthentication no, PermitRootLogin no"

if [ $APPLY -ne 1 ]; then
    echo; echo "Nothing changed. To apply:  sudo bash $0 --apply"; exit 0
fi

echo; echo "== APPLYING =="
# ssh first, validated, so a bad config never lands
mkdir -p /run/sshd
cat > "$DROPIN" <<'CONF'
# Written by ai-workspace/scripts/harden_ports.sh, 2026-09-14. Keys only; logs showed no password logins.
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
CONF
if ! sshd -t; then
    echo "  sshd -t REJECTED the change -- removing it; ssh is unchanged."
    rm -f "$DROPIN"
    exit 2
fi
echo "  ssh: keys only (validated). New connections use it; open sessions are untouched."

# firewall: add the allow rules BEFORE enabling, so nothing in use is ever blocked mid-way
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow in on tailscale0 >/dev/null
ufw deny from "$LAN4" to any port 8094 proto tcp >/dev/null
[ -n "$LAN6" ] && ufw deny from "$LAN6" to any port 8094 proto tcp >/dev/null
ufw allow from "$LAN4" >/dev/null
[ -n "$LAN6" ] && ufw allow from "$LAN6" >/dev/null
ufw allow from fe80::/10 >/dev/null
ufw allow 41641/udp >/dev/null
ufw --force enable

echo; echo "== AFTER =="
ufw status numbered
echo
echo "Done. Undo:  sudo ufw disable ; sudo rm $DROPIN"
