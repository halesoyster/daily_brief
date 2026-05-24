#!/usr/bin/env bash
# daily_brief — LaunchAgent wrapper.
#
# Loads env vars (from a project's load_env.sh if present, otherwise expects
# them exported in the environment), then runs daily-brief against a project.
#
# Invoked by:
#   - launchd (com.daily-brief) — daily at 9am
#   - Manually: DAILY_BRIEF_PROJECT=~/Projects/moon_baby bash run.sh
#
# Configuration (set as env vars or edit defaults below):
#   DAILY_BRIEF_PROJECT   — absolute path to the project to brief (required)
#
# If the project has a scripts/load_env.sh (e.g. moon_baby's Keychain loader),
# this wrapper sources it automatically. Otherwise export CLAUDE_API_KEY,
# BRIEF_EMAIL_FROM, BRIEF_EMAIL_TO, BRIEF_EMAIL_APP_PASSWORD before invoking.

set -euo pipefail

DAILY_BRIEF_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DAILY_BRIEF_PROJECT="${DAILY_BRIEF_PROJECT:-/Users/halesoyster/Projects/moon_baby}"
LOG_DIR="${HOME}/Library/Logs/daily-brief"
LOG_FILE="${LOG_DIR}/run.log"

mkdir -p "${LOG_DIR}"

{
  echo "──────────────────────────────────────────────"
  echo "[$(date -Iseconds)] daily_brief run starting"
  echo "  DAILY_BRIEF_ROOT=${DAILY_BRIEF_ROOT}"
  echo "  DAILY_BRIEF_PROJECT=${DAILY_BRIEF_PROJECT}"

  # Source project's Keychain loader if present (moon_baby pattern).
  # load_env.sh expects cwd = project root, so pushd first.
  LOAD_ENV="${DAILY_BRIEF_PROJECT}/scripts/load_env.sh"
  if [[ -f "${LOAD_ENV}" ]]; then
    pushd "${DAILY_BRIEF_PROJECT}" > /dev/null
    # shellcheck disable=SC1090
    source "${LOAD_ENV}"
    popd > /dev/null
  else
    echo "  (no load_env.sh found — expecting env vars already exported)"
  fi

  DAILY_BRIEF_BIN="${DAILY_BRIEF_ROOT}/.venv/bin/daily-brief"
  echo "  DAILY_BRIEF_BIN=${DAILY_BRIEF_BIN}"

  # Default args when invoked by launchd: email the brief, don't open browser.
  if [[ "$#" -eq 0 ]]; then
    set -- --project "${DAILY_BRIEF_PROJECT}" --email --no-open
  fi

  "${DAILY_BRIEF_BIN}" "$@"

  echo "[$(date -Iseconds)] daily_brief run complete"
} >> "${LOG_FILE}" 2>&1
