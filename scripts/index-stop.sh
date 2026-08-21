#!/usr/bin/env bash
# index-stop.sh — stop judgment indexing gracefully. Progress is preserved.
#
# Writes a stop-request file first so the indexer can finish the slice it is on
# and leave a consistent checkpoint, then sends TERM to that exact PID.

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

if ! pid="$(managed_pid "$INDEX_PID_FILE" "index_judgments")"; then
  echo "  judgment indexing is not running"
  rm -f "$PROJECT_ROOT/.run/index.stop"
  exit 0
fi

echo "${C_BOLD}Stopping judgment indexing${C_RESET} (pid $pid)"
touch "$PROJECT_ROOT/.run/index.stop"
echo "  stop requested — waiting for the current slice to finish ..."

stop_managed "$INDEX_PID_FILE" "index_judgments" "indexing" 90
rm -f "$PROJECT_ROOT/.run/index.stop"

echo
python3 - <<'PY'
import json, pathlib
p = pathlib.Path(".run/index_progress.json")
if p.exists():
    d = json.loads(p.read_text())
    print(f"  Persisted: {d.get('done', 0):,} / {d.get('total', 0):,} passages ({d.get('percent', 0)}%)")
    print("  Resume with: ./scripts/index-resume.sh")
else:
    print("  no progress file found")
PY
