#!/bin/bash
# dreaming.sh - the 04:30 nightly Dreaming run (dreaming.py, README.md). Reads the day's notes,
# judges the candidate pairs with one tool-less `claude -p` each, and writes the dream report to
# <state>/<night>/report.md and <state>/latest.md.
#
#   DREAMING_ROOT=~/notes bash dreaming.sh              # the scheduled job
#   DREAMING_ROOT=~/notes bash dreaming.sh --dry-run    # a rehearsal: no claude call, throwaway state
#
# --dry-run makes no claude call and keeps its state in a throwaway directory, so a hand test
# never moves the cursor or the carry that 04:30 reads. Without it the run judges (--judge) and
# writes notes only when the owner has created <state>/state/writes-enabled (--write).
# dreaming.py itself stops the judge at 06:40, checks usage before every call, and does nothing
# at all when a scheduled run starts after 06:00.
#
# Settings come from the environment, or from a `dreaming.env` file next to this script (see
# .env.example). The log is $DREAMING_LOG, default <root>/_meta/briefs/dreaming.log, trimmed to
# its last 400 lines. Exit code: dreaming.py's, except 4 (late start, the report says so) which
# is 0: a skipped night is not a failed one.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$HERE/dreaming.env" ]; then
  set -a; . "$HERE/dreaming.env"; set +a
fi
: "${DREAMING_ROOT:?set DREAMING_ROOT to your notes folder (see README.md)}"
export DREAMING_ROOT
PY="${DREAMING_PYTHON:-python3}"
STATE="${DREAMING_STATE:-$DREAMING_ROOT/_meta/health/dreams}"
LOG="${DREAMING_LOG:-$DREAMING_ROOT/_meta/briefs/dreaming.log}"
mkdir -p "$(dirname "$LOG")"
ARGS=(run --judge)
TMPSTATE=""
if [ "${1:-}" = "--dry-run" ]; then
  TMPSTATE="$(mktemp -d "${TMPDIR:-/tmp}/dreaming-dry.XXXXXX")"
  ARGS=(run --state "$TMPSTATE")
elif [ -e "$STATE/state/writes-enabled" ]; then
  ARGS+=(--write)
fi
rc=0
{
  echo "=== dreaming $(date '+%Y-%m-%d %H:%M:%S %Z') ${1:-} ==="
  "$PY" "$HERE/dreaming.py" "${ARGS[@]}" 2>&1 | tail -40
  rc=${PIPESTATUS[0]}
  [ "$rc" -eq 4 ] && { echo "dreaming skipped: late start"; rc=0; }
  echo "dreaming exit=$rc"
} >> "$LOG" 2>&1          # a brace group, not a subshell, so rc survives it
[ -n "$TMPSTATE" ] && rm -rf "$TMPSTATE"
tail -400 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
exit "$rc"
