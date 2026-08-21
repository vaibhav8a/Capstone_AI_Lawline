#!/usr/bin/env bash
# status.sh — what this project currently has running.

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

echo "${C_BOLD}LawLine AI — process status${C_RESET}"
echo "────────────────────────────────────────────────────────"

if pid="$(managed_pid "$FRONTEND_PID_FILE" "vite")"; then
  status_line "Frontend" "RUNNING" "pid $pid   http://localhost:$FRONTEND_PORT"
else
  status_line "Frontend" "STOPPED"
fi

if pid="$(managed_pid "$BACKEND_PID_FILE" "backend.main:app")"; then
  health="unreachable"
  curl -fsS --max-time 2 "http://127.0.0.1:$BACKEND_PORT/health" >/dev/null 2>&1 && health="healthy"
  status_line "Backend" "RUNNING" "pid $pid   :$BACKEND_PORT ($health)"
else
  status_line "Backend" "STOPPED"
fi

if pid="$(managed_pid "$INDEX_PID_FILE" "index_judgments")"; then
  detail="pid $pid"
  if [ -f "$PROJECT_ROOT/.run/index_progress.json" ]; then
    detail="$detail   $(python3 -c "
import json;d=json.load(open('$PROJECT_ROOT/.run/index_progress.json'))
print(f\"{d.get('done',0):,}/{d.get('total',0):,} ({d.get('percent',0)}%)\")" 2>/dev/null)"
  fi
  status_line "Judgment indexing" "RUNNING" "$detail"
else
  extra=""
  if [ -f "$PROJECT_ROOT/.run/index_progress.json" ]; then
    extra="$(python3 -c "
import json;d=json.load(open('$PROJECT_ROOT/.run/index_progress.json'))
s=d.get('state','')
print('complete' if s=='complete' else f\"{d.get('percent',0)}% done — resumable\")" 2>/dev/null)"
  fi
  status_line "Judgment indexing" "STOPPED" "$extra"
fi

echo
echo "${C_DIM}Ports${C_RESET}"
for entry in "backend:$BACKEND_PORT" "frontend:$FRONTEND_PORT"; do
  label="${entry%%:*}"; port="${entry##*:}"
  listener="$(port_listener "$port")"
  if [ -n "$listener" ]; then
    printf '  :%-5s listening  pid %-7s %s\n' "$port" "$listener" "$(cmdline_of "$listener" | cut -c1-52)"
  else
    printf '  :%-5s free\n' "$port"
  fi
done
