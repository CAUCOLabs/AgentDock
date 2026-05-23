#!/usr/bin/env bash
set -e

# AisoFlow project health check
# Runs lightweight checks for the current MVP.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"

echo "AisoFlow project check"
echo "Root: $ROOT_DIR"
echo ""

# -----------------------------
# Git status
# -----------------------------
echo "Checking git status..."
cd "$ROOT_DIR"
git status --short
echo ""

# -----------------------------
# Backend environment
# -----------------------------
echo "Checking backend environment..."
cd "$BACKEND_DIR"

if [ ! -d ".venv" ]; then
  echo "ERROR: backend/.venv not found."
  echo "Create it with:"
  echo "  cd backend"
  echo "  python3.11 -m venv .venv"
  echo "  source .venv/bin/activate"
  echo "  pip install -r requirements.txt"
  exit 1
fi

source .venv/bin/activate

echo "Python: $(which python)"
echo "Version: $(python --version)"
echo ""

# -----------------------------
# Python imports
# -----------------------------
echo "Checking Python imports..."
python -c "import fastapi; print('fastapi OK')"
python -c "import pydantic; print('pydantic OK')"
python -c "import pydantic_settings; print('pydantic-settings OK')"
python -c "import git; print('GitPython OK')"
python -c "import httpx; print('httpx OK')"
echo ""

# -----------------------------
# AisoFlow app imports
# -----------------------------
echo "Checking AisoFlow app imports..."
python -c "from app.config import get_settings; s=get_settings(); print(f'config OK: {s.app_name}')"
python -c "from app.models import create_task_id; print(f'models OK: {create_task_id()}')"
python -c "from app.main import app; print(f'api OK: {app.title}')"
echo ""

# -----------------------------
# File structure
# -----------------------------
echo "Checking expected files..."

EXPECTED_FILES=(
  "$ROOT_DIR/README.md"
  "$ROOT_DIR/.gitignore"
  "$BACKEND_DIR/requirements.txt"
  "$BACKEND_DIR/.env.example"
  "$BACKEND_DIR/app/main.py"
  "$BACKEND_DIR/app/config.py"
  "$BACKEND_DIR/app/models.py"
  "$ROOT_DIR/scripts/dev_backend.sh"
  "$ROOT_DIR/scripts/check_project.sh"
)

for file in "${EXPECTED_FILES[@]}"; do
  if [ ! -f "$file" ]; then
    echo "ERROR: missing file: $file"
    exit 1
  fi
done

echo "File structure OK"
echo ""

echo "All checks passed."
