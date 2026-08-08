#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose up --build -d api
echo "API: http://localhost:8000/docs"
echo "Demo login: demo@growthos.ai / demo1234"
