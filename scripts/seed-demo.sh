#!/usr/bin/env bash
#
# Seed demo data — DEVELOPMENT / STAGING ONLY.
#
# This writes fake organizations, clients, leads and analytics.
# It is refused when ENVIRONMENT=production (see app/demo/seed.py:assert_seeding_allowed).
#
# Usage:
#   ./scripts/seed-demo.sh            # seed the Docker API container
#   LOCAL=1 ./scripts/seed-demo.sh    # seed a local venv install
#
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "${ENVIRONMENT:-development}" == "production" ]]; then
  echo "REFUSED: ENVIRONMENT=production — demo data must never touch a production database." >&2
  exit 2
fi

if [[ "${LOCAL:-0}" == "1" ]]; then
  cd apps/api
  # shellcheck disable=SC1091
  source .venv/bin/activate
  PYTHONPATH=. python -m app.demo.seed
else
  docker compose exec -T api python -m app.demo.seed
fi

echo
echo "Demo data seeded."
echo "Login: demo@growthos.ai / demo1234  (development credentials only)"
