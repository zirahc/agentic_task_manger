#!/usr/bin/env bash
# Start the Agent Dashboard (Linux / macOS counterpart of run.ps1).
#
# Usage:
#   ./run.sh --setup        Create backend/.venv and install dependencies, then exit.
#   ./run.sh --demo         Use the built-in fake CLI instead of the real claude / codex.
#   ./run.sh --port 9000    Port to listen on (default 8765).
set -euo pipefail

setup=0
demo=0
port=8765

while [ $# -gt 0 ]; do
  case "$1" in
    --setup|-Setup) setup=1 ;;
    --demo|-Demo) demo=1 ;;
    --port|-Port) shift; port="${1:?--port needs a value}" ;;
    -h|--help) sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
backend="$root/backend"
python="$backend/.venv/bin/python"

if [ "$setup" = 1 ] || [ ! -x "$python" ]; then
  echo 'Creating backend/.venv and installing dependencies...'
  py="$(command -v python3 || command -v python || true)"
  if [ -z "$py" ]; then
    echo 'python3 not found. Install Python 3.12 or newer.' >&2
    exit 1
  fi
  "$py" -m venv "$backend/.venv"
  "$python" -m pip install --disable-pip-version-check -q -e "$backend[dev]"
  if [ "$setup" = 1 ]; then echo 'Done. Run ./run.sh to start.'; exit 0; fi
fi

export DASH_PORT="$port"
if [ "$demo" = 1 ]; then export DASH_FAKE_CLI=1; else unset DASH_FAKE_CLI; fi

cd "$backend"
exec "$python" -m app
