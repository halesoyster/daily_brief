#!/usr/bin/env bash
# daily_brief — install macOS LaunchAgent.
#
# Usage:
#   bash install.sh                              # brief against current dir
#   bash install.sh /abs/path/to/project         # brief against a specific project
#
# What it does:
#   1. Rewrites com.daily-brief.plist placeholders to absolute paths
#   2. Copies the plist to ~/Library/LaunchAgents/
#   3. Bootstraps the agent (modern launchctl API)
#   4. Prints the kickstart command to test immediately
#
# Important — macOS TCC restriction:
#   launchd-spawned processes cannot read ~/Documents/, ~/Downloads/, or ~/Desktop/.
#   Keep your project in ~/Projects/, ~/dev/, or any non-protected location.

set -euo pipefail

DAILY_BRIEF_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DAILY_BRIEF_PROJECT="${1:-$(pwd)}"
PLIST_SRC="${DAILY_BRIEF_ROOT}/com.daily-brief.plist"
PLIST_DEST="${HOME}/Library/LaunchAgents/com.daily-brief.plist"
LABEL="com.daily-brief"

echo "daily_brief — installing LaunchAgent"
echo "  daily_brief root : ${DAILY_BRIEF_ROOT}"
echo "  project          : ${DAILY_BRIEF_PROJECT}"
echo "  plist dest       : ${PLIST_DEST}"
echo

# Ensure venv exists.
if [[ ! -x "${DAILY_BRIEF_ROOT}/.venv/bin/daily-brief" ]]; then
  echo "✗ .venv/bin/daily-brief not found. Run:"
  echo "    cd ${DAILY_BRIEF_ROOT} && python3 -m venv .venv && .venv/bin/pip install -e ."
  exit 1
fi

# Unload existing agent if present (suppress error if not loaded).
launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true

# Rewrite placeholders and write to LaunchAgents.
mkdir -p "${HOME}/Library/LaunchAgents"
sed \
  -e "s|__DAILY_BRIEF_ROOT__|${DAILY_BRIEF_ROOT}|g" \
  -e "s|__HOME__|${HOME}|g" \
  -e "s|__DAILY_BRIEF_PROJECT__|${DAILY_BRIEF_PROJECT}|g" \
  "${PLIST_SRC}" > "${PLIST_DEST}"

# Bootstrap with modern API (launchctl load is deprecated and silently no-ops on recent macOS).
launchctl bootstrap "gui/$(id -u)" "${PLIST_DEST}"

echo "✓ LaunchAgent installed and registered."
echo
echo "Test it now:"
echo "  launchctl kickstart -p gui/$(id -u)/${LABEL}"
echo
echo "Check logs:"
echo "  tail -f ~/Library/Logs/daily-brief/run.log"
echo
echo "Uninstall:"
echo "  launchctl bootout gui/$(id -u) ${PLIST_DEST} && rm ${PLIST_DEST}"
