#!/usr/bin/env bash
# stop.sh — stop the web application (backend + frontend).
#
# Does NOT stop judgment indexing: that is a long job you may deliberately want
# to leave running, and stopping it is ./scripts/index-stop.sh. Pass --all to
# stop everything this project owns.
#
# Only PIDs recorded in .run/*.pid are signalled, and only after their command
# line is verified. No pkill/killall patterns are used anywhere.

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

STOP_INDEX=0
for arg in "$@"; do
  case "$arg" in
    --all) STOP_INDEX=1 ;;
    -h|--help)
      echo "usage: stop.sh [--all]"
      echo "  --all   also stop judgment indexing"
      exit 0 ;;
  esac
done

echo "${C_BOLD}Stopping LawLine AI${C_RESET}"
echo

stop_managed "$FRONTEND_PID_FILE" "vite"             "frontend"
stop_managed "$BACKEND_PID_FILE"  "backend.main:app" "backend"

if [ "$STOP_INDEX" -eq 1 ]; then
  stop_managed "$INDEX_PID_FILE" "index_judgments" "indexing" 90
else
  if pid="$(managed_pid "$INDEX_PID_FILE" "index_judgments")"; then
    echo
    echo "  ${C_YELLOW}note:${C_RESET} judgment indexing is still running (pid $pid)"
    echo "        it will keep using CPU/GPU. Stop it with:"
    echo "        ${C_BOLD}./scripts/index-stop.sh${C_RESET}   (progress is saved and resumable)"
  fi
fi

# ── verify the ports actually released ──────────────────────────────────────
echo
sleep 1
FAILED=0
for entry in "backend:$BACKEND_PORT" "frontend:$FRONTEND_PORT"; do
  label="${entry%%:*}"; port="${entry##*:}"
  listener="$(port_listener "$port")"
  if [ -n "$listener" ]; then
    # Someone is on the port — but is it ours, or an unrelated program?
    echo "  ${C_YELLOW}port $port still listening${C_RESET} (pid $listener)"
    echo "    $(cmdline_of "$listener" | cut -c1-90)"
    echo "    ${C_DIM}not signalled: no project PID file claims it${C_RESET}"
    FAILED=1
  else
    printf '  port %-5s %sreleased%s\n' "$port" "$C_GREEN" "$C_RESET"
  fi
done

echo
if [ "$FAILED" -eq 0 ]; then
  echo "  ${C_GREEN}All project web processes stopped.${C_RESET}"
  if [ "$STOP_INDEX" -eq 1 ] || ! managed_pid "$INDEX_PID_FILE" "index_judgments" >/dev/null; then
    echo "  ${C_GREEN}No project CPU/GPU work remaining.${C_RESET}"
  fi
else
  echo "  ${C_YELLOW}Some ports are held by processes this project does not own.${C_RESET}"
fi
