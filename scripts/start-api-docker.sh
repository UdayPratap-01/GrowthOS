#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose up --build -d api
echo "API: http://localhost:8000/docs"
echo
echo "The container no longer seeds demo data on startup. To seed a development database:"
echo "  ./scripts/seed-demo.sh"
