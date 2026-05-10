#!/usr/bin/env bash
set -e

# AgentDock backend development server
# Always runs uvicorn through the backend virtual environment.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"

cd "$BACKEND_DIR"

if [ ! -d ".venv" ]; then
  echo "Backend virtual environment not found."
  echo "Create it with:"
  echo "  cd backend"
  echo "  python3.11 -m venv .venv"
  echo "  source .venv/bin/activate"
  echo "  pip install -r requirements.txt"
  exit 1
fi

source .venv/bin/activate

echo "Starting AgentDock backend..."
echo "Python: $(which python)"
echo "Version: $(python --version)"

python -m uvicorn app.main:app --reload