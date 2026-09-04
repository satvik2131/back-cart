# back-cart

Backend for an ecommerce **checkout and rewards** service: products with
inventory, carts, one-shot idempotent checkout that produces immutable orders, a
milestone-based coupon reward, and a live admin report.

The design goal is **correctness under concurrency, retries, and partial
failure** — not feature breadth. Every concurrency guarantee comes from the
database (row locks, unique constraints, atomic conditional updates, CHECK
constraints), so the service stays correct run as multiple instances against one
database. See [`DECISIONS.md`](DECISIONS.md) for the invariants, the
ambiguities resolved, and the material design decisions.

## Stack

FastAPI · SQLAlchemy 2 (async) + asyncpg · Alembic · PostgreSQL 16 ·
pydantic-settings · pytest + httpx · Docker Compose

## Prerequisites

- Docker & Docker Compose v2 (`docker compose version`)

## Run

```bash
cp .env.example .env
docker compose up --build
```

That's the only manual step. It starts three services:

| Service | URL | Notes |
| --- | --- | --- |
| `api` | http://localhost:8000 | runs `alembic upgrade head` + seed (idempotent) on start, then uvicorn |
| `frontend` | http://localhost:5173 | Vite dev server for the demo UI (first build installs npm deps) |
| `db` | `localhost:5434` | PostgreSQL 16 |

```bash
curl localhost:8000/health         # {"status":"ok"}
curl localhost:8000/health/db      # {"status":"ok","db":"connected"}
```

Interactive API docs: **http://localhost:8000/docs** · Demo UI: **http://localhost:5173**

> Postgres publishes on host port **5434** (to avoid clashing with a local
> Postgres on 5432); inside the compose network it's `db:5432`, and the
> frontend reaches the API as `api:8000`.

## API overview

All money is **integer minor units (cents)**. All errors use one envelope:
`{"error": {"code", "message", "details"}}` with stable codes (catalogued in
[`DECISIONS.md` §6](DECISIONS.md)).

| Method & path | Purpose |
| --- | --- |
| `GET /products` | List the seeded catalogue |
| `GET /products/{id}` | One product (404 envelope on miss) |
| `POST /carts` | Create an empty `open` cart |
| `GET /carts/{id}` | Cart with line subtotals + total, computed live |
| `POST /carts/{id}/items` | Add a product (or increment its quantity) |
| `PATCH /carts/{id}/items/{item_id}` | Set quantity (`0` removes the line) |
| `DELETE /carts/{id}/items/{item_id}` | Remove a line |
| `POST /carts/{id}/checkout` | **Idempotent checkout.** Requires header `Idempotency-Key`. Optional body `{"coupon_code": "..."}`. One transaction: atomic inventory decrement → order + price snapshots → coupon redemption → cart closed. Retrying the same key replays the stored response. |
| `GET /orders/{id}` | An order — snapshots only, never re-derived |
| `POST /admin/coupons/generate` | *(admin)* Generate the coupon for the latest reached milestone |
| `GET /admin/report` | *(admin)* Live revenue / discount / order / coupon / per-product totals — read-only |

Admin endpoints are unauthenticated (per spec) but namespaced under `/admin` and
tagged `admin` in the OpenAPI docs.

### A full demo flow

```bash
cp .env.example .env && docker compose up --build -d && sleep 8

BASE=localhost:8000
PID=$(curl -s $BASE/products | python3 -c "import sys,json;print(next(p['id'] for p in json.load(sys.stdin) if p['inventory']>10))")

CART=$(curl -s -X POST $BASE/carts | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
curl -s -X POST $BASE/carts/$CART/items -H 'content-type: application/json' \
  -d "{\"product_id\":\"$PID\",\"quantity\":2}"
curl -s -X POST $BASE/carts/$CART/checkout -H 'Idempotency-Key: demo-1'
curl -s -X POST $BASE/carts/$CART/checkout -H 'Idempotency-Key: demo-1'   # same order, no double effect
curl -s $BASE/admin/report
```

## Migrations

Applied automatically on container start. Manually:

```bash
docker compose exec api alembic upgrade head
docker compose exec api alembic revision --autogenerate -m "describe change"   # after model changes
```

Every schema change ships with its migration in the same commit; every migration
has a working `downgrade()`. Make targets: `make migrate`, `make revision m="..."`.

## Seed data

Run automatically on start. Manually: `docker compose exec api python -m app.features.products.seed`
(or `make seed`). Idempotent — inserts 6 products, including one at 2 units and
one at 0 units so oversell / out-of-stock paths can be exercised. It is a script,
not a data migration ([`DECISIONS.md` §3](DECISIONS.md)).

## Tests

```bash
docker compose exec api pytest        # or: make test
```

51 tests. The suite provisions and uses a **separate `back_cart_test`
database** (schema built from the ORM metadata) — it never touches dev data.
Sequential tests run inside a rolled-back transaction; concurrency tests
(`asyncio.gather`, real parallel requests) run against real committed rows and
clean up after themselves. At least one concurrency test per critical path
(oversell, idempotent-retry race, concurrent coupon generation, concurrent
coupon redemption), each verified to fail when its DB-level guard is removed.

## Frontend demo

A small React + Vite app under [`frontend/`](frontend/) drives the whole flow
from a browser (products → cart → checkout → idempotent retry → coupons →
report), showing real backend responses and errors. It comes up with
`docker compose up` at **http://localhost:5173**.

To run it outside Docker instead (against the compose API):

```bash
cd frontend && npm install && npm run dev
```

See [`frontend/README.md`](frontend/README.md).

## Inspecting the database (optional)

```bash
docker compose exec db psql -U postgres -d back_cart -c "\dt"
```

An opt-in pgAdmin lives behind the `tools` profile (not started by default):

```bash
docker compose --profile tools up -d pgadmin      # or: make pgadmin
```

http://localhost:5051 — desktop mode, no login (if prompted: `admin@example.com`
/ `admin`); the **back-cart** server is pre-registered, DB password `postgres`.

## Project layout

```
app/
  main.py                    app construction, router wiring, /health
  core/
    config.py                pydantic-settings (DB URL, coupon config)
    errors.py                error envelope + all exception handlers
  db/
    base.py  session.py      declarative Base; async engine + get_session
  features/<name>/            one self-contained module per domain
    models.py  schemas.py  service.py  dependencies.py  router.py
    products/   read-only catalogue + seed.py
    carts/      cart lifecycle, live totals, cart-row lock on mutations
    orders/     checkout transaction + idempotency + GET /orders/{id}
    coupons/    Coupon model + redemption (called from the checkout txn)
    admin/      /admin/coupons/generate + /admin/report (no tables of its own)
alembic/versions/            migrations (one per feature)
tests/
  conftest.py                test-DB provisioning, the two client fixtures
  features/                   one test module per feature
DECISIONS.md                 invariants, ambiguities, decisions, scaling, AI usage
```

## Make targets

| Target | Action |
| --- | --- |
| `make up` / `make down` | start (build) / stop the stack |
| `make logs` | tail the api logs |
| `make migrate` / `make revision m="..."` | apply / create a migration |
| `make seed` | re-seed the catalogue |
| `make test` | run pytest |
| `make pgadmin` | start the optional pgAdmin UI on :5051 |
| `make shell` | shell into the api container |
