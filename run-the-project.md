# Running the project (first time)

Quick-start only. For everything else (architecture, API overview, invariants,
design decisions) see [`README.md`](README.md) and [`DECISIONS.md`](DECISIONS.md).

## Prerequisites

- Docker & Docker Compose v2 — check with `docker compose version`

## 1. Start everything

```bash
docker compose up --build
```

No `.env` file, no manual config — every setting has a working default baked
into `docker-compose.yml`. This single command starts three services:

| Service | URL | What it does on start |
| --- | --- | --- |
| `db` | `localhost:5434` | PostgreSQL 16 |
| `api` | http://localhost:8000 | runs `alembic upgrade head`, then seeds the product catalogue, then serves the API — **both steps are automatic and idempotent** |
| `frontend` | http://localhost:5173 | demo React UI (first build installs npm deps, so it takes longer the first time) |

No manual migration or seed step is required for a first run — the `api`
container does both before it starts serving.

## 2. Confirm it's up

```bash
curl localhost:8000/health         # {"status":"ok"}
curl localhost:8000/health/db      # {"status":"ok","db":"connected"}
curl localhost:8000/products       # 6 seeded products
```

Or open:
- **http://localhost:8000/docs** — interactive API docs
- **http://localhost:5173** — demo UI

## Re-running the seed script manually

The seed only inserts products that don't already exist (matched by name), so
it's always safe to re-run — e.g. after a fresh migration reset, or if you
want to confirm it's idempotent:

```bash
docker compose exec api python -m app.features.products.seed
# or:
make seed
```

Expected output the first time: `Seeded 6 new product(s); 0 already present, 6 in catalogue.`
Running it again: `Seeded 0 new product(s); 6 already present, 6 in catalogue.`

## Running tests

```bash
docker compose exec api pytest
# or:
make test
```

The suite provisions its own `back_cart_test` database on first run — it
never touches the data seeded above.

## Common commands

| Command | Action |
| --- | --- |
| `docker compose up --build` / `make up` | build + start everything |
| `docker compose down` | stop everything (keeps data) |
| `docker compose down -v` | stop and wipe all data (fresh start) |
| `make seed` | re-run the idempotent product seed |
| `make migrate` | apply migrations manually (`alembic upgrade head`) |
| `make test` | run the backend test suite |
| `make logs` | tail the `api` container logs |
| `make shell` | shell into the `api` container |

## Overriding a setting (optional)

Nothing below is required. If you ever want to change a default (e.g. a
different `COUPON_MILESTONE_EVERY`), either export it before running compose:

```bash
COUPON_MILESTONE_EVERY=3 docker compose up --build
```

or create a `.env` file at the repo root with `KEY=value` lines — Docker
Compose reads it automatically and it takes priority over the baked-in
defaults in `docker-compose.yml`. No `.env` ever needs to exist for the
project to run.

## Starting over from a clean database

```bash
docker compose down -v
docker compose up --build
```

`down -v` removes the Postgres data volume; the next `up` re-creates the
database, re-applies migrations, and re-seeds automatically.
