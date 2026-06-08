#!/usr/bin/env bash
#
# microcodegen demo — PRD text → a running Flask app, end to end.
#
# Requires: python 3.10+, docker (for the Postgres-backed boot), curl.
# Usage:    ./demo.sh [path/to/prd.md]   (defaults to examples/task_manager.md)
#
set -euo pipefail

PRD="${1:-examples/task_manager.md}"
OUT="./my-task-app"

echo "# 1. Generate a Flask + Postgres app from a plain-English PRD"
python microcodegen.py "$PRD" --out "$OUT"

echo
echo "# 2. Boot it — Flask + Postgres, healthcheck-gated, zero edits, zero API keys"
cd "$OUT"
[ -f .env ] || cp .env.example .env
docker compose up -d --build

echo
echo "# waiting for the app to come up..."
until curl -fs http://localhost:5000/api/health >/dev/null; do sleep 1; done

echo
echo "# 3. It's live"
echo "\$ curl localhost:5000/api/health"
curl -s http://localhost:5000/api/health; echo

echo
echo "# Register — issues a JWT in an httpOnly cookie (never localStorage)"
curl -s -c cookies.txt -X POST http://localhost:5000/api/auth/register \
     -H 'Content-Type: application/json' \
     -d '{"email":"you@example.com","password":"hunter22hunter"}'; echo

echo
echo "# Create a project — auth required, row scoped to this user"
curl -s -b cookies.txt -X POST http://localhost:5000/api/projects/ \
     -H 'Content-Type: application/json' \
     -d '{"name":"My first project"}'; echo

echo
echo "# List — returns only your rows"
curl -s -b cookies.txt http://localhost:5000/api/projects/; echo

echo
echo "# Without the cookie, the API refuses"
curl -s http://localhost:5000/api/projects/; echo

echo
echo "# Done. Open http://localhost:5000 for the click-through demo page."
echo "# Tear down with:  cd $OUT && docker compose down -v"
