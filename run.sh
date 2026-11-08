#!/usr/bin/env bash
cd "$(dirname "$0")"
if ! command -v uv >/dev/null; then
  echo "uv is required. Install it first: https://docs.astral.sh/uv/getting-started/installation/"
  exit 1
fi
echo "Open http://127.0.0.1:8765 in your browser"
exec uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8765
