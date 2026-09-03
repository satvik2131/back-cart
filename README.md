# back-cart

Backend for an ecommerce checkout and rewards service. See DECISIONS.md for
invariants and design decisions. Current state: scaffolding + the Products
module (read-only catalogue).

## Stack

- FastAPI + Uvicorn
- SQLAlchemy 2.x (async) + asyncpg
- Alembic (async migrations)
- pydantic-settings for configuration
- PostgreSQL 16
- Docker + docker compose

## Prerequisites

- Docker & Docker Compose v2 (`docker compose version`)

## Run

```bash
cp .env.example .env
docker compose up --build
```

The API is then reachable at http://localhost:8000.

> The Postgres container publishes on host port **5434** (`localhost:5434`) to
> avoid clashing with a local Postgres on 5432. Inside the compose network the
> `api` service still reaches it as `db:5432`.

## Health checks

```bash
curl localhost:8000/health
# {"status":"ok"}

curl localhost:8000/health/db
# {"status":"ok","db":"connected"}
```

Or open http://localhost:8000/docs.

## Migrations

Alembic is configured for async and reads `DATABASE_URL` from the same settings
as the app.

Run inside the running `api` container:

```bash
# Apply migrations
docker compose exec api alembic upgrade head

# Create a new migration after changing models
docker compose exec api alembic revision --autogenerate -m "add something"
docker compose exec api alembic upgrade head
```

(Equivalent Make targets: `make migrate`, `make revision m="..."`.)

## Seed data

The product catalogue is inserted by an idempotent script (not a migration —
see DECISIONS.md §3). After `alembic upgrade head`:

```bash
docker compose exec api python -m app.features.products.seed   # or: make seed
```

It adds 6 products, including one low-inventory (2 units) and one out-of-stock
(0 units) for later oversell / out-of-stock testing.

## Inspecting the database (optional)

`psql` is always available:

```bash
docker compose exec db psql -U postgres -d back_cart -c "\dt"
```

For a UI, an opt-in pgAdmin lives behind the `tools` compose profile, so the
default `docker compose up` never starts it:

```bash
docker compose --profile tools up -d pgadmin   # or: make pgadmin
```

Open http://localhost:5051 (desktop mode — no pgAdmin login; if prompted,
`admin@example.com` / `admin`). The **back-cart** server is pre-registered;
enter the database password `postgres` on first connect. Stop it with
`docker compose --profile tools down`.

## Tests

```bash
docker compose exec api pytest
```

Each test runs in a transaction that is rolled back on teardown, so the suite
needs the schema applied (`alembic upgrade head`) but not the seed script.

## Project layout

```
app/
  main.py                  FastAPI app + /health and /health/db + router wiring
  core/config.py           pydantic-settings configuration
  core/errors.py           structured error envelope + shared exception handlers
  db/base.py               declarative Base
  db/session.py            async engine + session factory + get_session dependency
  api/router.py            empty aggregate router placeholder
  features/
    products/              first feature module
      models.py            Product ORM model (DB-level CHECK constraints)
      schemas.py           ProductRead response schema
      service.py           read queries
      dependencies.py      get_product_or_404
      router.py            GET /products, GET /products/{id}
      seed.py              idempotent catalogue seed script
alembic/versions/          migrations
tests/                     transaction-rollback fixtures + feature tests
```

## Make targets

| Target | Action |
| --- | --- |
| `make up` | `docker compose up --build` |
| `make down` | stop and remove containers |
| `make logs` | tail the api logs |
| `make migrate` | `alembic upgrade head` in the container |
| `make revision m="..."` | autogenerate a migration |
| `make seed` | insert the product catalogue (idempotent) |
| `make pgadmin` | start the optional pgAdmin UI on :5051 |
| `make test` | run pytest in the container |
| `make shell` | shell into the api container |
