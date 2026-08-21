#!/usr/bin/env bash
# index-judgments.sh — start judgment indexing (checkpointed, resumable).
#
# Heavy MPS/CPU job. Never started automatically by start.sh.
# Safe to stop at any time with ./scripts/index-stop.sh — progress is persisted.

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

RESET=0
for arg in "$@"; do
  case "$arg" in
    --reset) RESET=1 ;;
    -h|--help) echo "usage: index-judgments.sh [--reset]"; exit 0 ;;
  esac
done

# One indexer at a time. A second process would duplicate MPS load and fight
# over the same checkpoint file.
if pid="$(managed_pid "$INDEX_PID_FILE" "index_judgments")"; then
  echo "  ${C_YELLOW}indexing already running (pid $pid)${C_RESET}"
  echo "  progress: ./scripts/index-status.sh"
  exit 1
fi

rm -f "$PROJECT_ROOT/.run/index.stop"

FLAG=""
[ "$RESET" -eq 1 ] && FLAG="--reset"

echo "${C_BOLD}Starting judgment indexing${C_RESET} ${C_DIM}(BGE-M3 on MPS — this is CPU/GPU heavy)${C_RESET}"
cd "$PROJECT_ROOT"
nohup python3 -m backend.ingestion.index_judgments_resumable $FLAG \
    > "$LOG_DIR/index.log" 2>&1 &
echo $! > "$INDEX_PID_FILE"

sleep 2
echo "  pid $(cat "$INDEX_PID_FILE")  |  log: .run/logs/index.log"
echo "  progress: ./scripts/index-status.sh"
echo "  stop:     ./scripts/index-stop.sh   ${C_DIM}(safe — progress is saved)${C_RESET}"
