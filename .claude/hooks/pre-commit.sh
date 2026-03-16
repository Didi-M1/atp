#!/usr/bin/env bash
# Claude Code pre-commit hook for ATP.
#
# Triggered by .claude/settings.json whenever Claude runs a `git commit`.
# Validates Python syntax and test collection before allowing the commit.
# Exit 2 → Claude sees the output as an error and aborts the commit.

set -euo pipefail

# ── Parse the Bash command from Claude's JSON input ───────────────────────────
INPUT=$(cat)
COMMAND=$(printf '%s' "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('command',''))" 2>/dev/null || true)

# Only run checks when Claude is about to do a git commit
if [[ "$COMMAND" != git\ commit* ]]; then
    exit 0
fi

REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"

ERRORS=0

echo ""
echo "════════════════════════════════════════════════"
echo "  ATP pre-commit checks"
echo "════════════════════════════════════════════════"

# ── 1. Python syntax on staged .py files ─────────────────────────────────────
echo ""
echo "  [1/2] Python syntax check"

# Support both normal commits and initial commits (empty HEAD)
if git rev-parse --verify HEAD &>/dev/null; then
    STAGED=$(git diff --cached --name-only --diff-filter=ACM | grep '\.py$' || true)
else
    STAGED=$(git diff --cached --name-only --diff-filter=ACM \
             "$(git hash-object -t tree /dev/null)" | grep '\.py$' || true)
fi

if [ -z "$STAGED" ]; then
    echo "  → No staged .py files"
else
    while IFS= read -r f; do
        if python3 -m py_compile "$f" 2>/tmp/_atp_syn; then
            echo "  ✓  $f"
        else
            echo "  ✗  $f"
            sed 's/^/       /' /tmp/_atp_syn
            ERRORS=$((ERRORS + 1))
        fi
    done <<< "$STAGED"
fi

# ── 2. Test collection ────────────────────────────────────────────────────────
echo ""
echo "  [2/2] pytest --collect-only --profile=example_qemu"

COLLECT=$(python3 -m pytest \
    --collect-only \
    --profile=example_qemu \
    --tb=short \
    -q 2>&1) || true

if echo "$COLLECT" | grep -qE "^ERROR|^ERRORS|error in"; then
    echo "  ✗  Collection errors:"
    echo "$COLLECT" | grep -A10 -E "^ERROR|error in" | sed 's/^/       /'
    ERRORS=$((ERRORS + 1))
else
    SUMMARY=$(echo "$COLLECT" | grep -E "^[0-9]+ test" | tail -1 || echo "collected")
    echo "  ✓  $SUMMARY"
fi

# ── Result ────────────────────────────────────────────────────────────────────
echo ""
if [ "$ERRORS" -gt 0 ]; then
    echo "  ✗  $ERRORS check(s) failed — commit blocked."
    echo "════════════════════════════════════════════════"
    echo ""
    exit 2   # exit 2 = Claude sees this as a blocking error
fi

echo "  ✓  All checks passed."
echo "════════════════════════════════════════════════"
echo ""
exit 0
