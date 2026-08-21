#!/usr/bin/env bash
# start.sh — start ONLY the web application.
#
# Deliberately does NOT: download models, build or rebuild embeddings, rebuild
# ChromaDB, ingest documents, or index judgments. Those are heavy MPS/CPU jobs
# and every one of them is an explicit, separate command.
#
# The backend loads the already-persisted ChromaDB collections. The embedding
# model is loaded lazily on the FIRST query, not at startup, so an idle chatbot
# does no ML work at all.

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

echo "${C_BOLD}Starting LawLine AI${C_RESET}"
echo

# ── refuse to double-start ──────────────────────────────────────────────────
if pid="$(managed_pid "$BACKEND_PID_FILE" "backend.main:app")"; then
  echo "  ${C_YELLOW}backend already running (pid $pid)${C_RESET} — leaving it alone"
  BACKEND_ALREADY=1
fi
if pid="$(managed_pid "$FRONTEND_PID_FILE" "vite")"; then
  echo "  ${C_YELLOW}frontend already running (pid $pid)${C_RESET} — leaving it alone"
  FRONTEND_ALREADY=1
fi

# ── preflight: warn, don't silently build ───────────────────────────────────
CHROMA_DIR="$PROJECT_ROOT/outputs/chroma_db"
if [ ! -d "$CHROMA_DIR" ]; then
  echo "  ${C_YELLOW}warning:${C_RESET} no ChromaDB at outputs/chroma_db"
  echo "           the app will start but retrieval will fail."
  echo "           build it explicitly:  python -m backend.ingestion.build_production_index"
  echo
fi

# ── backend ─────────────────────────────────────────────────────────────────
if [ -z "${BACKEND_ALREADY:-}" ]; then
  if ! port_free "$BACKEND_PORT"; then
    echo "  ${C_RED}port $BACKEND_PORT is already in use${C_RESET} by pid $(port_listener "$BACKEND_PORT")"
    echo "  that process is not managed by this project; not touching it."
    exit 1
  fi
  printf '  starting backend  ... '
  cd "$PROJECT_ROOT"
  nohup python3 -m uvicorn backend.main:app \
      --host 127.0.0.1 --port "$BACKEND_PORT" --log-level warning \
      > "$LOG_DIR/backend.log" 2>&1 &
  echo $! > "$BACKEND_PID_FILE"
  echo "pid $(cat "$BACKEND_PID_FILE")"
fi

# ── frontend ────────────────────────────────────────────────────────────────
if [ -z "${FRONTEND_ALREADY:-}" ]; then
  if ! port_free "$FRONTEND_PORT"; then
    echo "  ${C_RED}port $FRONTEND_PORT is already in use${C_RESET} by pid $(port_listener "$FRONTEND_PORT")"
    exit 1
  fi
  if [ ! -d "$PROJECT_ROOT/frontend/node_modules" ]; then
    echo "  ${C_RED}frontend/node_modules missing${C_RESET} — run: npm --prefix frontend ci"
    exit 1
  fi
  printf '  starting frontend ... '
  cd "$PROJECT_ROOT/frontend"
  # Run the vite binary directly rather than through `npm run dev`. npm spawns
  # vite as a CHILD, so $! would be the npm wrapper: the PID file would point at
  # a process whose command line never contains "vite", status.sh would report
  # STOPPED while the server was serving, and stop.sh would kill the wrapper and
  # orphan the actual server. One process, one PID, one honest marker.
  nohup ./node_modules/.bin/vite > "$LOG_DIR/frontend.log" 2>&1 &
  echo $! > "$FRONTEND_PID_FILE"
  echo "pid $(cat "$FRONTEND_PID_FILE")"
fi

# ── wait for readiness ──────────────────────────────────────────────────────
echo
printf '  waiting for backend  '
for _ in $(seq 1 60); do
  if curl -fsS --max-time 2 "http://127.0.0.1:$BACKEND_PORT/health" >/dev/null 2>&1; then
    echo "${C_GREEN}ready${C_RESET}"; break
  fi
  printf '.'; sleep 1
done

printf '  waiting for frontend '
for _ in $(seq 1 60); do
  # Vite binds IPv6 localhost, so ask for localhost rather than 127.0.0.1.
  if curl -fsS --max-time 2 "http://localhost:$FRONTEND_PORT/" >/dev/null 2>&1; then
    echo "${C_GREEN}ready${C_RESET}"; break
  fi
  printf '.'; sleep 1
done

echo
echo "  ${C_BOLD}App:${C_RESET}      http://localhost:$FRONTEND_PORT"
echo "  ${C_BOLD}API:${C_RESET}      http://127.0.0.1:$BACKEND_PORT"
echo "  ${C_BOLD}API docs:${C_RESET} http://127.0.0.1:$BACKEND_PORT/docs"
echo
echo "  ${C_DIM}logs:  .run/logs/{backend,frontend}.log${C_RESET}"
echo "  ${C_DIM}stop:  ./scripts/stop.sh${C_RESET}"
