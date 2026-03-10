#!/bin/sh
# atp-wait-peer.sh — wait until the network peer is reachable before tests run.
#
# Usage (inside the DUT guest):
#   /mnt/atp/scripts/qemu/atp-wait-peer.sh <profile-name>
#
# Reads peer_ip from the ATP profile.  If network is disabled or peer_ip is
# not set, exits immediately.  Otherwise pings the peer once per second for
# up to 60 seconds.  Always exits 0 so a slow peer does not block the tests.

PROFILE="${1:-}"
ATP_DIR="$(cd "$(dirname "$0")/../.." && pwd)"

if [ -z "$PROFILE" ]; then
    exit 0
fi

# Read peer_ip from the profile using Python (already available in the guest).
PEER_IP=$(python3 - "$PROFILE" "$ATP_DIR" 2>/dev/null << 'PYEOF'
import sys
sys.path.insert(0, sys.argv[2])
from atp.profile import load_profile
p = load_profile(sys.argv[1])
net = p.get("network", {})
if net.get("enabled") and net.get("peer_ip"):
    print(net["peer_ip"])
PYEOF
)

if [ -z "$PEER_IP" ]; then
    exit 0
fi

echo "Waiting for peer ${PEER_IP}..."
i=0
while [ "$i" -lt 60 ]; do
    if ping -c 1 -W 1 "$PEER_IP" >/dev/null 2>&1; then
        echo "Peer ${PEER_IP} is ready."
        exit 0
    fi
    i=$((i + 1))
    sleep 1
done

echo "Peer ${PEER_IP} not reachable after 60 s — continuing anyway."
