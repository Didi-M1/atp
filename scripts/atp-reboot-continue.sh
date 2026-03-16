#!/bin/sh
# atp-reboot-continue.sh
#
# Called by the init system after every reboot.  Handles two cases:
#
#   1. Auto-run trigger (.atp-autorun): written by qemu-run.sh --profile when
#      the user wants tests to start automatically on first boot.  Reads the
#      profile name, deletes the trigger, then runs the full test suite.
#
#   2. Reboot-continue (reboot_state.json): written by the stability test when
#      a reboot sequence is in progress.  Resumes test_stability.py.
#
# Wire this into your init system:
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
# This script does nothing if neither trigger file is present (safe to leave
# installed permanently).

STATE_FILE="${ATP_STATE_FILE:-${HOME:-/root}/.atp/reboot_state.json}"
LOG_FILE="/var/log/atp-reboot.log"
ATP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
AUTORUN_FILE="${ATP_DIR}/.atp-autorun"

# When qemu-run.sh --profile is used, expect drives the console directly and
# handles reboots in the foreground.  Step aside so there is no conflict.
if [ -f "${ATP_DIR}/.atp-expect-mode" ]; then
    exit 0
fi

# Nothing to do if no active reboot test and no auto-run trigger
if [ ! -f "$STATE_FILE" ] && [ ! -f "$AUTORUN_FILE" ]; then
    exit 0
fi

# Locate pytest
if command -v pytest >/dev/null 2>&1; then
    PYTEST="pytest"
elif command -v pytest3 >/dev/null 2>&1; then
    PYTEST="pytest3"
elif command -v python3 >/dev/null 2>&1; then
    PYTEST="python3 -m pytest"
else
    echo "$(date): ERROR: pytest not found in PATH" >> "$LOG_FILE"
    exit 1
fi

cd "$ATP_DIR" || exit 1

# ── Case 1: auto-run trigger (first-boot full test suite) ──────────────────
if [ -f "$AUTORUN_FILE" ]; then
    AUTORUN_PROFILE=$(cat "$AUTORUN_FILE")
    rm -f "$AUTORUN_FILE"
    echo "$(date): Auto-running all tests for profile '$AUTORUN_PROFILE'" >> "$LOG_FILE"
    $PYTEST --profile="$AUTORUN_PROFILE" >> "$LOG_FILE" 2>&1
    EXIT=$?
    echo "$(date): pytest exited with code $EXIT" >> "$LOG_FILE"
    exit $EXIT
fi

# ── Case 2: reboot-continue (stability test in progress) ───────────────────
PROFILE=$(grep -o '"profile"[[:space:]]*:[[:space:]]*"[^"]*"' "$STATE_FILE" \
          | sed 's/.*"profile"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/')
PROFILES_DIR=$(grep -o '"profiles_dir"[[:space:]]*:[[:space:]]*"[^"]*"' "$STATE_FILE" \
               | sed 's/.*"profiles_dir"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/')

if [ -z "$PROFILE" ]; then
    echo "$(date): ERROR: Could not read profile name from $STATE_FILE" >> "$LOG_FILE"
    exit 1
fi

ARGS="--profile=$PROFILE"
[ -n "$PROFILES_DIR" ] && ARGS="$ARGS --profiles-dir=$PROFILES_DIR"

echo "$(date): Continuing reboot test for profile '$PROFILE'" >> "$LOG_FILE"
$PYTEST $ARGS tests/test_stability.py >> "$LOG_FILE" 2>&1

EXIT=$?
echo "$(date): pytest exited with code $EXIT" >> "$LOG_FILE"
exit $EXIT
