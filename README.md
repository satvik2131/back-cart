# back-cart

Bare-bones FastAPI + PostgreSQL skeleton. No business logic — just enough to
prove the stack boots and talks to the database.

## Stack

- FastAPI + Uvicorn
- SQLAlchemy 2.x (async) + asyncpg
- Alembic (async migrations, zero models)
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
as the app. There are no models yet, so `upgrade head` is a clean no-op that
just creates the `alembic_version` bookkeeping table.

Run inside the running `api` container:

```bash
# Apply migrations
docker compose exec api alembic upgrade head

# Create a new migration once you add models
docker compose exec api alembic revision --autogenerate -m "add something"
docker compose exec api alembic upgrade head
```

(Equivalent Make targets: `make migrate`, `make revision m="..."`.)

## Tests

A single smoke test asserts `GET /health` returns 200 — it only proves the
test harness works.

```bash
docker compose exec api pytest
```

## Project layout

```
app/
  main.py            FastAPI app + /health and /health/db
  core/config.py     pydantic-settings configuration
  db/base.py         empty declarative Base
  db/session.py      async engine + session factory + get_session dependency
  api/router.py      empty aggregate router placeholder
alembic/             async migration environment (no versions yet)
tests/               conftest stub + health smoke test
```

## Make targets

| Target | Action |
| --- | --- |
| `make up` | `docker compose up --build` |
| `make down` | stop and remove containers |
| `make logs` | tail the api logs |
| `make migrate` | `alembic upgrade head` in the container |
| `make revision m="..."` | autogenerate a migration |
| `make test` | run pytest in the container |
| `make shell` | shell into the api container |
