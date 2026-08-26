#!/usr/bin/env bash
#
# Apply database migrations. Safe to run on every deploy.
#
# For a database that predates Alembic (created by metadata.create_all), run
# `alembic stamp head` ONCE first so the initial migration is not re-applied
# over existing tables. See docs/MIGRATIONS.md.
#
set -euo pipefail
cd "$(dirname "$0")/.."

echo "Current revision:"
alembic current

echo "Applying migrations..."
alembic upgrade head

echo "Now at:"
alembic current
