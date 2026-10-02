#!/bin/bash
# install.sh - set Dreaming up on a Mac: check Python, write dreaming.env, run the selftest,
# and (with --launchd) install the 04:30 LaunchAgent. Safe to run again.
#
#   ./install.sh --root ~/notes                 # config and selftest only
#   ./install.sh --root ~/notes --launchd       # also schedule the nightly run
#   ./install.sh --uninstall                    # remove the LaunchAgent
set -eu
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="${DREAMING_LABEL:-com.example.dreaming}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
ROOT=""
LAUNCHD=0
while [ $# -gt 0 ]; do
  case "$1" in
    --root) ROOT="$2"; shift 2 ;;
    --launchd) LAUNCHD=1; shift ;;
    --uninstall)
      launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
      rm -f "$PLIST"; echo "removed $PLIST"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
  || { echo "Dreaming needs Python 3.9 or later" >&2; exit 1; }
command -v git >/dev/null || { echo "Dreaming needs git" >&2; exit 1; }

if [ ! -f "$HERE/dreaming.env" ]; then
  [ -n "$ROOT" ] || { echo "first run: pass --root <your notes folder>" >&2; exit 2; }
  ROOT="$(cd "$ROOT" && pwd)"
  sed "s#^DREAMING_ROOT=.*#DREAMING_ROOT=$ROOT#" "$HERE/.env.example" > "$HERE/dreaming.env"
  echo "wrote $HERE/dreaming.env (edit it to change any setting)"
fi
set -a; . "$HERE/dreaming.env"; set +a

echo "running the selftest..."
python3 "$HERE/selftest.py" | tail -1

if command -v claude >/dev/null || [ -n "${DREAMING_CLAUDE:-}" ]; then
  echo "judge: claude found"
else
  echo "judge: no claude CLI found. Install Claude Code and log in, or set DREAMING_CLAUDE."
  echo "       Until then every live night reports 'judge: failed'."
fi

if [ "$LAUNCHD" -eq 1 ]; then
  LOGDIR="$DREAMING_ROOT/_meta/briefs"
  mkdir -p "$LOGDIR" "$HOME/Library/LaunchAgents"
  sed -e "s#__LABEL__#$LABEL#g" -e "s#__DIR__#$HERE#g" -e "s#__LOGDIR__#$LOGDIR#g" \
    "$HERE/launchd/com.example.dreaming.plist.template" > "$PLIST"
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "loaded $LABEL: runs daily at 04:30 (launchctl kickstart gui/$(id -u)/$LABEL to run now)"
fi
