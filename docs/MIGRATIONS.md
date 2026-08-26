# Database Migrations

GrowthOS uses [Alembic](https://alembic.sqlalchemy.org/) for schema management.

## How schema is applied per environment

| Environment | Mechanism | Setting |
| --- | --- | --- |
| development | `metadata.create_all` at startup | `DB_AUTO_CREATE` unset (defaults on) |
| staging | Alembic | set `DB_AUTO_CREATE=false` |
| production | Alembic **only** | `DB_AUTO_CREATE` must be unset or `false` — startup fails otherwise |

`create_all` never runs in production regardless of configuration
(`Settings.should_auto_create_tables` returns `False` when `ENVIRONMENT=production`),
and `validate_configuration()` refuses to start if `DB_AUTO_CREATE=true` is set there.

## Configuration

Alembic reads the synchronous URL from `DATABASE_URL_SYNC`:

```bash
export DATABASE_URL_SYNC="postgresql+psycopg2://user:password@host:5432/growthos"
```

This is separate from `DATABASE_URL`, which is the async URL (`postgresql+asyncpg://...`)
used by the application at runtime. Both must point at the same database.

## Fresh installation

```bash
cd apps/api
alembic upgrade head
```

This creates all 39 tables from revision `101a13e5de91` (initial schema).

## Existing database created before Alembic

A database created by the old `create_all` startup path already has the tables but
no `alembic_version` row. Running `upgrade head` there would fail with
"relation already exists". Stamp it once instead:

```bash
cd apps/api
alembic stamp head      # records 101a13e5de91 without running any DDL
alembic current         # confirm
```

Verify the stamped schema matches the models before continuing:

```bash
alembic check           # must print "No new upgrade operations detected."
```

If `alembic check` reports differences, the live schema has drifted. Generate a
corrective migration rather than editing the initial one:

```bash
alembic revision --autogenerate -m "reconcile drift"
```

Review the generated file, then apply it.

## Deploying a schema change

1. Change the SQLAlchemy models.
2. Autogenerate against a database that is already at `head`:

```bash
alembic revision --autogenerate -m "describe the change"
```

3. **Read the generated migration.** Autogenerate does not detect renames (it emits
   drop + add, which loses data), server-default changes, or `CHECK` constraints.
   Rewrite those by hand.
4. Apply and verify no drift remains:

```bash
alembic upgrade head
alembic check
```

5. Commit the migration file with the model change in the same commit.

## Downgrade

The initial migration has a complete, tested `downgrade()`:

```bash
alembic downgrade -1     # one revision back
alembic downgrade base   # drop everything
```

Downgrade is verified in CI-style testing for the initial revision
(upgrade → downgrade → upgrade round-trip). Treat downgrade as a
development and staging tool: **it drops tables and destroys data**. For
production incidents, prefer restoring from a backup and rolling forward.

Before any production migration:

```bash
pg_dump "$DATABASE_URL_SYNC" > backup-$(date +%F-%H%M).sql
```

## Deploy order

Migrations must run before the new application code starts:

```bash
./scripts/migrate.sh && uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The API container's `CMD` deliberately does not run migrations, so that a
multi-replica rollout does not race. Run `migrate.sh` as a one-off release task.

## Verifying a migration locally

```bash
docker run -d --rm --name gos-mig -e POSTGRES_USER=growthos \
  -e POSTGRES_PASSWORD=growthos -e POSTGRES_DB=growthos \
  -p 55432:5432 postgres:16-alpine

cd apps/api
export DATABASE_URL_SYNC="postgresql+psycopg2://growthos:growthos@localhost:55432/growthos"
alembic upgrade head
alembic check
alembic downgrade base
docker stop gos-mig
```
