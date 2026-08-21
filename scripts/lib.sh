#!/usr/bin/env bash
# lib.sh — shared helpers for the project process scripts.
#
# SAFETY MODEL
# ------------
# Every process this project starts writes a PID file into .run/. Stopping is
# done by reading that PID, VERIFYING the process command line still looks like
# the thing we started, and only then signalling it.
#
# Broad patterns such as `pkill -f python`, `killall node` or `pkill -f uvicorn`
# are never used. They would happily kill an unrelated Jupyter kernel, another
# project's dev server, or the user's editor language server. A PID file plus a
# command-line check cannot do that: if the PID has been recycled by an
# unrelated process, the command-line check fails and we refuse to signal it.

set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$PROJECT_ROOT/.run"
LOG_DIR="$PROJECT_ROOT/.run/logs"
mkdir -p "$RUN_DIR" "$LOG_DIR"

BACKEND_PID_FILE="$RUN_DIR/backend.pid"
FRONTEND_PID_FILE="$RUN_DIR/frontend.pid"
INDEX_PID_FILE="$RUN_DIR/index.pid"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

# Colours only when attached to a terminal.
if [ -t 1 ]; then
  C_RESET=$'\033[0m'; C_GREEN=$'\033[32m'; C_RED=$'\033[31m'
  C_YELLOW=$'\033[33m'; C_DIM=$'\033[2m'; C_BOLD=$'\033[1m'
else
  C_RESET=""; C_GREEN=""; C_RED=""; C_YELLOW=""; C_DIM=""; C_BOLD=""
fi

# ── process helpers ─────────────────────────────────────────────────────────

# pid_alive <pid> — is the process running at all?
pid_alive() {
  local pid="${1:-}"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

# cmdline_of <pid> — full command line, empty if gone.
cmdline_of() {
  ps -p "${1:-0}" -o command= 2>/dev/null || true
}

# owns_pid <pid> <expected-substring>
# True only when the PID is alive AND its command line still contains the
# expected marker. This is what makes killing safe after a PID is recycled.
owns_pid() {
  local pid="${1:-}" marker="${2:-}"
  pid_alive "$pid" || return 1
  cmdline_of "$pid" | grep -qF "$marker"
}

# read_pid <pidfile>
read_pid() {
  local file="${1:-}"
  [ -f "$file" ] && tr -d '[:space:]' < "$file" || true
}

# managed_pid <pidfile> <marker> — echo the PID if we still own it, else nothing.
# Also cleans up a stale PID file so status output stays truthful.
managed_pid() {
  local file="${1:-}" marker="${2:-}" pid
  pid="$(read_pid "$file")"
  if [ -z "$pid" ]; then return 1; fi
  if owns_pid "$pid" "$marker"; then
    echo "$pid"
    return 0
  fi
  rm -f "$file"
  return 1
}

# stop_managed <pidfile> <marker> <label> [grace-seconds]
# Graceful TERM, wait, then KILL only if it refuses. Never touches anything the
# PID file does not point at.
#
# The grace period matters for the indexer: it finishes the embedding slice it is
# on before exiting so the checkpoint stays consistent, and a slice takes ~30s.
# A 10s grace period forced every stop into a SIGKILL, discarding the in-flight
# slice unnecessarily. Web servers exit promptly and keep the short default.
stop_managed() {
  local file="${1:-}" marker="${2:-}" label="${3:-process}" grace="${4:-10}" pid
  pid="$(managed_pid "$file" "$marker")" || {
    printf '  %s%-22s%s not running\n' "$C_DIM" "$label" "$C_RESET"
    rm -f "$file"
    return 0
  }

  printf '  stopping %-14s (pid %s) ... ' "$label" "$pid"
  kill -TERM "$pid" 2>/dev/null

  local waited=0 limit=$((grace * 10))
  while pid_alive "$pid" && [ "$waited" -lt "$limit" ]; do
    sleep 0.1
    waited=$((waited + 1))
  done

  if pid_alive "$pid"; then
    # Still there after 10s — escalate, but only for this exact PID.
    kill -KILL "$pid" 2>/dev/null
    sleep 0.4
    if pid_alive "$pid"; then
      printf '%sFAILED%s\n' "$C_RED" "$C_RESET"
      return 1
    fi
    printf '%sforced%s\n' "$C_YELLOW" "$C_RESET"
  else
    printf '%sstopped%s\n' "$C_GREEN" "$C_RESET"
  fi

  rm -f "$file"
  return 0
}

# port_listener <port> — PID listening on a TCP port, if any.
port_listener() {
  lsof -nP -iTCP:"${1:-0}" -sTCP:LISTEN -t 2>/dev/null | head -1
}

# port_free <port>
port_free() {
  [ -z "$(port_listener "${1:-0}")" ]
}

status_line() {
  local label="$1" state="$2" detail="${3:-}"
  local colour="$C_RED"
  [ "$state" = "RUNNING" ] && colour="$C_GREEN"
  printf '  %-22s %s%-8s%s %s\n' "$label" "$colour" "$state" "$C_RESET" "$detail"
}
