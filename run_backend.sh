#!/bin/bash
set -e

# Always run from the project root so Python finds the 'backend' package
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Starting Legal RAG Backend Services..."

# 1. Start Redis if not running
if ! docker info > /dev/null 2>&1; then
    echo "Docker is not running or accessible. Please start Docker."
    exit 1
fi
docker compose up -d redis

# 2. Activate the virtualenv
VENV="$SCRIPT_DIR/backend/venv"
if [ -f "$VENV/bin/activate" ]; then
    source "$VENV/bin/activate"
    echo "Activated venv at $VENV"
fi

# 3. Start the RQ Background Worker (optional for local testing if running synchronously, but good for full pipeline)
# We can run it in a separate terminal, but let's just print instructions.
echo "Make sure to run the RQ worker in a separate terminal: docker compose up -d rq-worker OR 'rq worker legal-rag'"

# 4. Run the FastAPI server from the project root so 'backend.main:app' resolves
echo "Starting FastAPI on http://localhost:8000 ..."
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
