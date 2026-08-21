#!/usr/bin/env bash
# index-status.sh — progress of the judgment indexing job.

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

cd "$PROJECT_ROOT"
RUNNING_PID="$(managed_pid "$INDEX_PID_FILE" "index_judgments" || true)"

echo "${C_BOLD}Judgment indexing${C_RESET}"
echo "────────────────────────────────────────"
python3 - "$RUNNING_PID" <<'PY'
import json, pathlib, sys, time

running_pid = sys.argv[1] if len(sys.argv) > 1 else ""
progress = pathlib.Path(".run/index_progress.json")

if not progress.exists():
    if running_pid:
        # A job is running but writes no progress file: the pre-checkpoint
        # indexer. Report it truthfully rather than claiming NOT STARTED.
        print("Status:    RUNNING (legacy job — no checkpointing)")
        print(f"PID:       {running_pid}")
        print()
        print("This job predates the resumable indexer, so it writes nothing to")
        print("ChromaDB until it finishes and CANNOT be resumed if stopped.")
        print("Stopping it discards its progress:  ./scripts/index-stop.sh")
        print("A later ./scripts/index-resume.sh starts a fresh CHECKPOINTED run.")
    else:
        print("Status:    NOT STARTED")
        print("Start it:  ./scripts/index-judgments.sh")
    raise SystemExit(0)

d = json.loads(progress.read_text())
done, total = d.get("done", 0), d.get("total", 0)
state = d.get("state", "unknown")
if running_pid:
    state = "RUNNING"
elif state == "running":
    state = "INTERRUPTED"
else:
    state = state.upper()

width = 34
filled = int(width * done / total) if total else 0
bar = "█" * filled + "░" * (width - filled)

print(f"Progress:  {done:,} / {total:,} passages ({d.get('percent', 0)}%)")
print(f"           {bar}")
print(f"Status:    {state}")
print(f"Model:     {d.get('model', '?')}")
print(f"Device:    {d.get('device', '?')}")
if d.get("passages_per_second"):
    print(f"Rate:      {d['passages_per_second']} passages/s")
if d.get("eta_seconds") and state == "RUNNING":
    print(f"Remaining: {time.strftime('%Hh %Mm %Ss', time.gmtime(d['eta_seconds']))}")
if running_pid:
    print(f"PID:       {running_pid}")
print(f"Updated:   {d.get('updated_at', '?')}")
print()
if state == "RUNNING":
    print("Stop safely: ./scripts/index-stop.sh")
elif state in ("INTERRUPTED", "STOPPED"):
    print("Resume:      ./scripts/index-resume.sh")
PY
