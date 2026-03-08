#!/bin/sh
# atp-reboot-continue.sh
#
# Called by the init system after every reboot to continue an in-progress
# reboot stability test.  Wire this into your init system:
#
#   SysV init (/etc/init.d/):
#     Copy or symlink this file to /etc/init.d/S99atp-reboot
#     Make it executable: chmod +x /etc/init.d/S99atp-reboot
#
#   /etc/rc.local:
#     Add the line:  /path/to/atp/scripts/atp-reboot-continue.sh &
#
#   BusyBox inittab (/etc/inittab):
#     Add the line:  ::once:/path/to/atp/scripts/atp-reboot-continue.sh
#
# This script does nothing if no reboot test is in progress (safe to leave
# installed permanently).

STATE_FILE="${ATP_STATE_FILE:-/var/lib/atp/reboot_state.json}"
LOG_FILE="/var/log/atp-reboot.log"

# Nothing to do if no active reboot test
[ -f "$STATE_FILE" ] || exit 0

# Read profile name and optional profiles_dir from state file
# (requires a minimal JSON parser — we use sed/grep for BusyBox compatibility)
PROFILE=$(grep -o '"profile"[[:space:]]*:[[:space:]]*"[^"]*"' "$STATE_FILE" \
          | sed 's/.*"profile"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/')
PROFILES_DIR=$(grep -o '"profiles_dir"[[:space:]]*:[[:space:]]*"[^"]*"' "$STATE_FILE" \
               | sed 's/.*"profiles_dir"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/')

if [ -z "$PROFILE" ]; then
    echo "$(date): ERROR: Could not read profile name from $STATE_FILE" >> "$LOG_FILE"
    exit 1
fi

# Locate pytest
PYTEST=$(command -v pytest 2>/dev/null || command -v pytest3 2>/dev/null || \
         command -v python3 2>/dev/null && echo "python3 -m pytest")
if [ -z "$PYTEST" ]; then
    echo "$(date): ERROR: pytest not found in PATH" >> "$LOG_FILE"
    exit 1
fi

# Locate the ATP directory (directory containing this script's parent)
ATP_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Build command
ARGS="--profile=$PROFILE"
[ -n "$PROFILES_DIR" ] && ARGS="$ARGS --profiles-dir=$PROFILES_DIR"

echo "$(date): Continuing reboot test for profile '$PROFILE'" >> "$LOG_FILE"

cd "$ATP_DIR" || exit 1
$PYTEST $ARGS tests/test_stability.py >> "$LOG_FILE" 2>&1

EXIT=$?
echo "$(date): pytest exited with code $EXIT" >> "$LOG_FILE"
exit $EXIT
