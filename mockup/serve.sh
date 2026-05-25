#!/usr/bin/env bash
# Serve the dashboard mockup on http://localhost:8000
# Static files only — no backend, no DB connection.
cd "$(dirname "$0")"
PORT="${PORT:-8000}"
echo "→ Mockup at http://localhost:${PORT}/"
echo "  Ctrl-C to stop."
exec python3 -m http.server "${PORT}"
