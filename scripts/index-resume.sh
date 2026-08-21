#!/usr/bin/env bash
# index-resume.sh — continue indexing from the persisted checkpoint.
# Already-embedded passages are skipped; nothing restarts from zero.

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

if pid="$(managed_pid "$INDEX_PID_FILE" "index_judgments")"; then
  echo "  ${C_YELLOW}indexing is already running (pid $pid)${C_RESET} — nothing to resume"
  exit 1
fi

if [ ! -f "$PROJECT_ROOT/.run/index_checkpoint.json" ]; then
  echo "  no checkpoint found — starting a fresh index run"
fi

exec "$(dirname "${BASH_SOURCE[0]}")/index-judgments.sh"
