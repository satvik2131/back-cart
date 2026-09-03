# back-cart — Ecommerce Checkout & Rewards Service

Backend for an ecommerce checkout and rewards flow: products with inventory,
carts, one-shot checkout that produces immutable orders, and a milestone-based
coupon reward system, plus a read-only admin report.

This is a take-home assignment. **The primary design goal is correctness under
concurrency, retries, and partial failure — not feature breadth.** Evaluators
care much more that checkout cannot oversell, cannot double-charge on retry, and
cannot double-spend a coupon than about how many endpoints exist. Prefer a
smaller surface that is provably correct over a larger one that is racy.

---

## Tech stack

- **FastAPI** — HTTP layer
- **SQLAlchemy 2.x (async)** — ORM / data access, async engine + `async_sessionmaker`
- **Alembic** — schema migrations (async env, reads `DATABASE_URL` from settings)
- **PostgreSQL 16** — system of record; concurrency guarantees come from here
- **Docker + Docker Compose** — the supported way to run everything
- **pytest + httpx** — async test suite
- **pydantic-settings** — configuration from environment / `.env`

No new runtime or dev dependencies without flagging it first (see House rules).

---

## Project structure

Current scaffold (inspect before assuming — it grows as features land):

```
app/
  main.py            FastAPI app instance; /health and /health/db live here for now
  core/
    config.py        pydantic-settings Settings; DATABASE_URL, APP_ENV, etc.
  db/
    base.py          DeclarativeBase (`Base`) — currently empty, no models
    session.py       async engine, AsyncSessionLocal, get_session() dependency
  api/
    router.py        empty aggregate APIRouter (feature routers get included here)
alembic/
  env.py             async migration environment, pulls DATABASE_URL from settings
  versions/          migration files (none yet)
tests/
  conftest.py        httpx AsyncClient fixture + placeholder for a test-DB fixture
  test_health.py     smoke test
DECISIONS.md         design log — see rule below
```

Directories to add as the domain is implemented, following the same layout:

- `app/models/` — SQLAlchemy ORM models (Product, Cart, CartItem, Order,
  OrderLine, Coupon, IdempotencyKey, …). Import them where Alembic autogenerate
  can see `Base.metadata`.
- `app/schemas/` — Pydantic request/response models. Money fields are integers
  (minor units), never floats.
- `app/api/` — one router module per resource (`products`, `carts`, `checkout`,
  `coupons`, `admin`), each included from `app/api/router.py`.
- `app/services/` (or equivalent) — checkout / coupon transaction logic, kept
  out of the route handlers.

Keep `main.py` thin: app construction and router wiring only.

---

## Invariants — hard constraints

Every change must preserve all of these. They are also what the tests exist to
break. If a feature or a test seems to require violating one, **stop and flag the
conflict** — do not weaken the invariant.

1. **No overselling.** Units sold across all successful orders for a product
   never exceed its seeded inventory; available inventory never goes negative.
2. **Checkout is one-shot.** A cart moves `open → checked_out` exactly once. It
   can never be checked out twice, and its items cannot change after checkout.
3. **Orders are immutable.** Once created, an order's line items, unit prices,
   discount, and totals never change — even if the product's price or inventory
   changes later. The order carries its own full snapshot of the purchase and
   how the total was computed.
4. **Retry-safe checkout.** Repeating a checkout request with the same
   idempotency key produces exactly one order and deducts inventory exactly
   once — never twice. Retries return the original result.
5. **Coupon generation is once-per-milestone.** For a given milestone, at most
   one coupon is ever generated, even under concurrent admin requests. Only
   successfully placed orders count toward milestones.
6. **Coupon redemption is once, and only on success.** A coupon is redeemed by
   at most one successful order. A coupon held by a checkout that then fails is
   released, not burned. Concurrent checkouts cannot both redeem it.
7. **Money is integer minor units.** All money is computed and stored as integer
   minor units (e.g. cents). No floats, no float rounding. Discounts are
   deterministic; net total is `max(0, gross - discount)` and is never negative.
8. **Reports are pure reads.** Admin report endpoints never mutate state, and
   their aggregates always reconcile with the actual orders and coupons.

---

## House rules for code

Applies to anything Claude Code writes or modifies here.

- **Transactions.** Every mutating endpoint — checkout, coupon generation, all
  cart mutations — runs inside a single database transaction. On any failure the
  whole thing rolls back so inventory, coupon state, and cart state revert
  together and the request can be safely retried.
- **Concurrency safety comes from the database.** Use transactions, unique
  constraints, and atomic conditional updates
  (`UPDATE ... SET inventory = inventory - :q WHERE id = :id AND inventory >= :q`,
  checking rows affected). Do **not** use in-process locks, mutexes, or
  in-memory caches for correctness — the design must stay correct if run as
  multiple app instances against one database.
- **Structured errors.** All error responses use the envelope
  `{"error": {"code": "...", "message": "...", "details": {...}}}` with stable,
  machine-readable codes. Prefer a shared exception handler over scattered
  ad-hoc `raise HTTPException`. Canonical codes live in DECISIONS.md section 6.
- **Money.** Integer minor units everywhere — DB columns, computations, and JSON
  request/response bodies. Fixed rounding rule applied once at
  discount-calculation time (see DECISIONS.md section 5).
- **No new dependencies without flagging it first.** Propose it and wait.
- **Never weaken an invariant to make a test pass or a feature work.** If they
  conflict, surface the conflict instead of working around it.
- **Admin endpoints** are unauthenticated but must be clearly marked as
  administrative (path prefix and/or OpenAPI tag). No auth anywhere in scope.

---

## Keep DECISIONS.md current

`DECISIONS.md` is a graded deliverable. Update it **as decisions are made, not
retroactively.** Whenever a material design decision is taken — idempotency
mechanism, locking strategy, coupon redemption mechanics, money/rounding rule,
error model, price/inventory drift handling, payment abstraction, report
strategy — add or fill in its entry in the relevant section (invariants,
ambiguities resolved, material decisions, concurrency/idempotency narrative,
money rules, error model, implemented vs. deferred, scaling notes, AI tool
usage). The AI-tool-usage section needs at least one concrete example of
correcting or rejecting AI output — capture those when they happen.

---

## Running the project

```bash
cp .env.example .env          # first time only
docker compose up --build     # API on http://localhost:8000
```

Health checks:

```bash
curl localhost:8000/health        # {"status": "ok"}
curl localhost:8000/health/db     # {"status": "ok", "db": "connected"}
```

Postgres is published on host port **5434** (to avoid colliding with a local
Postgres on 5432); inside the compose network it is `db:5432`. The `api`
container mounts the project for uvicorn `--reload`, so code edits apply without
a rebuild. Rebuild only after changing `requirements*.txt` or the `Dockerfile`.

## Running tests

```bash
docker compose exec api pytest        # against the running stack
# or, if the stack is down:
docker compose run --rm api pytest
```

At least one test must exercise concurrent / repeated operations (parallel
checkouts on limited inventory, replayed idempotency key, concurrent coupon
generation) — not just sequential happy paths.

## Migrations

```bash
docker compose exec api alembic revision --autogenerate -m "describe change"
docker compose exec api alembic upgrade head
```

Alembic reads `DATABASE_URL` from the same settings as the app. New models must
be imported so `Base.metadata` sees them before autogenerate runs.

Convenience targets exist in the `Makefile` (`make up`, `make test`,
`make migrate`, `make revision m="..."`).

---

## Code Organization

Each business domain is a self-contained feature module under
`app/features/<feature_name>/`, not a flat layer split (`app/models/`,
`app/schemas/`, `app/api/`). A feature module contains:

```
app/features/<feature_name>/
  router.py         FastAPI APIRouter for this feature's endpoints
  models.py         SQLAlchemy ORM models owned by this feature
  schemas.py        Pydantic request/response models
  service.py        transactional business logic (checkout, redemption, …)
  dependencies.py   FastAPI dependencies specific to this feature
```

Expected features for this project: `products`, `carts`, `checkout`, `coupons`,
`admin` (report + coupon generation). Models likely to appear: `Product`,
`Cart`, `CartItem`, `Order`, `OrderLine`, `Coupon`, `IdempotencyKey` — placed in
the feature that owns them (`Order`/`OrderLine` under `checkout`, etc.).

Shared / cross-cutting code stays put:

- `app/core/` — `config.py` (settings), error envelope + shared exception
  handlers, structured logging setup.
- `app/db/` — `base.py` (`Base`), `session.py` (async engine, `get_session`).

Each feature's `router` is registered with `app.include_router(...)` in
`app/main.py`. Model modules must be imported before Alembic autogenerate runs
so `Base.metadata` sees every table.

Do not introduce a different organizational pattern without flagging it first.

---

## API Contract Rules

- Every endpoint's request and response body is a Pydantic model — never a raw
  `dict`.
- Pydantic schemas (`schemas.py`) are always distinct from SQLAlchemy models
  (`models.py`). Never return an ORM instance directly from a handler; map it to
  a response schema.
- Every endpoint that can fail enumerates its failure modes explicitly in the
  route decorator — declared `responses={409: ..., 422: ...}` with the error
  envelope model and the stable codes from DECISIONS.md §6 — not left to a
  catch-all handler. Examples: `POST /carts/{id}/checkout` →
  `CART_ALREADY_CHECKED_OUT`, `INSUFFICIENT_INVENTORY`, `COUPON_ALREADY_REDEEMED`,
  `INVALID_COUPON`; `POST /admin/coupons` → `MILESTONE_NOT_REACHED`,
  `MILESTONE_ALREADY_REWARDED`.
- A breaking change to an existing response schema requires flagging before
  making it.

---

## Database Rules

- Every schema change ships with its Alembic migration in the **same commit** as
  the model change — models and migrations never drift.
- Every migration implements a real `downgrade()`. If a change is genuinely
  irreversible, say so explicitly in the migration's docstring.
- No `SELECT *` / whole-entity loads where only a few columns are used — query
  the specific columns the code actually reads.
- Every foreign key sets a deliberate `ON DELETE` behavior. Default expectation:
  `RESTRICT` for references that must not vanish under a live order (e.g.
  `OrderLine.product_id`, `Order.coupon_id`), `CASCADE` only for true
  parent/child ownership (e.g. `CartItem` under `Cart`).
- Indexes are added deliberately, each with a comment naming the query pattern
  it serves (e.g. the unique index on `IdempotencyKey.key`, the index behind the
  per-product sales aggregate in the admin report). No speculative indexes.

---

## Testing Rules

- Every bug fix ships with a regression test that fails before the fix and
  passes after, with a comment naming the invariant (§ "Invariants") it guards.
- Concurrency-sensitive paths — checkout, coupon redemption, coupon generation —
  each have at least one test that fires genuinely concurrent requests via
  `asyncio.gather` (or threads), then asserts the invariant held (exactly one
  order, inventory deducted once, one coupon per milestone). Sequential calls
  labelled "concurrent" do not count.
- Tests assert on resulting state and observable behavior (DB rows, response
  bodies, status codes), not on internal call sequences or private helpers.
- No test depends on execution order or on state left behind by another test;
  each sets up and tears down its own data.

---

## Error Handling & Observability Rules

- No bare `except Exception: pass`. Every caught exception is either handled
  meaningfully or re-raised with added context.
- Every mutating operation (checkout, cart mutation, coupon generation) emits a
  structured log event on both success and failure, including the
  idempotency / correlation key and the affected entity ids.
- Do not log full request bodies containing money amounts or coupon codes
  without a specific reason; log ids and outcome codes instead.
- Expected business failures (`INSUFFICIENT_INVENTORY`, `INVALID_COUPON`,
  `CART_ALREADY_CHECKED_OUT`, …) are distinct from unexpected system failures
  (DB unavailable, bug) in both the error-code namespace and log severity:
  business failures log at `INFO`/`WARNING` and map to 4xx; system failures log
  at `ERROR` and map to 5xx.

---

## Git & Review Discipline

- Each commit is one logical change.
- Commit messages explain *why* when it isn't obvious from the diff, not just
  *what*.
- No commented-out code in commits.
- Self-review every diff as if it were a colleague's PR before calling a feature
  done — check for unhandled edge cases, missing tests, and inconsistent naming.

---

## Dependency & Scope Rules

- Every added dependency comes with a one-line justification (in the commit
  message or DECISIONS.md).
- Prefer the standard library or an already-present dependency over a new one.
- Don't build anything outside the spec or DECISIONS.md "just in case" — record
  it as deferred scope (DECISIONS.md §7) instead.

---

## Naming & Readability Rules

- Names describe intent, not implementation.
- Boolean names are positive: `is_active`, not `is_not_disabled`.
- Every money-carrying name makes its unit unambiguous — `amount_cents: int`,
  `unit_price_cents`, `discount_cents` — never a bare `amount` / `price` /
  `total`.
- A function that can fail in an expected business way raises a specific domain
  exception (or returns a Result-like value) — it never overloads `None` to mean
  both "failed" and "legitimately empty".
