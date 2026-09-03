# DECISIONS.md


---

## 1. System Invariants

> List the properties that must hold true at all times, regardless of
> concurrency, retries, or failure. Be specific — "inventory is correct" is
> too vague; "sum of reserved + available inventory never exceeds seeded
> inventory" is useful. These are the things your tests should be trying to
> break.

1. **Inventory:** Available inventory for a product never goes negative; the
   sum of units sold across all successful orders for a product never
   exceeds its seeded inventory.
2. **Cart lifecycle:** A cart transitions `open → checked_out` exactly once;
   it can never be checked out twice, and items cannot be modified after
   checkout.
3. **Order immutability:** Once created, an order's line items, unit prices,
   and total are immutable, even if the underlying product's price or
   inventory later changes.
4. **Checkout idempotency:** Submitting the same checkout request (same
   idempotency key) any number of times produces exactly one order and
   deducts inventory exactly once.
5. **Coupon generation:** For a given milestone (e.g., the 5th, 10th, 15th
   successful order), at most one coupon is ever generated, regardless of
   how many concurrent admin requests ask for it.
6. **Coupon redemption:** A coupon is redeemed by at most one successful
   order. A coupon consumed by a checkout that subsequently fails is
   released, not burned.
7. **Order total correctness:** `net_total = gross_total - discount`, always
   computed in integer minor units (cents/paise), and `net_total` is never
   negative.
8. **Report consistency:** The admin report is a pure read — repeated calls
   never mutate state — and its aggregates always reconcile with the actual
   set of orders and coupons in the system at query time.

---

## 2. Ambiguities Found and Semantics Chosen

> The spec deliberately leaves gaps. List each one you found, the options
> you considered, and which you picked. This is where the evaluator sees you
> noticing problems rather than accidentally avoiding them.

| # | Ambiguity | Semantics chosen |
|---|-----------|-------------------|
| 1 | Does checkout use the price at add-to-cart time or the current product price? | _e.g. Current price at checkout time; cart view shows current price with a `price_changed` flag if it differs from when the item was added._ |
| 2 | What happens if inventory drops below a cart's requested quantity before checkout? | _e.g. Checkout fails with `409 INSUFFICIENT_INVENTORY`; client must adjust cart quantity and retry — no automatic partial fulfillment._ |
| 3 | Can a coupon be applied to a cart below some minimum order value? | _e.g. No minimum; discount simply cannot make total negative (clamped at zero)._ |
| 4 | Is a coupon single-use globally or single-use per customer? | _e.g. Single-use globally (no customer identity in scope per spec) — first successful redemption consumes it._ |
| 5 | Does a milestone count all created orders or only ones that survive checkout validation? | _e.g. Only orders that reach `SUCCESS` status count toward milestones — abandoned/failed checkouts don't count._ |
| 6 | What is the idempotency key — client-supplied header, or derived from cart state? | _e.g. Client-supplied `Idempotency-Key` header, required on checkout; server stores request hash + response for replay detection._ |
| 7 | Can an item quantity of 0 be added, or must it be removed instead? | _e.g. Quantity must be ≥1 on add; setting quantity to 0 via update is treated as remove._ |

_Add rows for anything else you had to decide unilaterally._

---

## 3. Material Design Decisions

> Minimum five, using the structure below. These should be the decisions
> that would actually change behavior if reversed — not filler. Good
> candidates: idempotency mechanism, concurrency/locking strategy, coupon
> redemption mechanics, money representation, error model, price/inventory
> drift handling, in-memory vs. DB persistence, payment abstraction.

### Decision: Idempotency Strategy for Checkout

**Context:** Clients may retry checkout after a timeout without knowing if
the original request succeeded. A retry must not double-charge inventory or
create a duplicate order.

**Options considered:**
- Client-supplied idempotency key stored in a dedicated table, checked
  before processing.
- Deriving a deterministic key from cart ID + cart content hash (no client
  cooperation required).
- No idempotency handling; rely on "cart already checked out" as an
  implicit guard.

**Choice:** _e.g. Client-supplied `Idempotency-Key` header, required,
stored alongside the resulting order/response in an `idempotency_keys`
table with a unique constraint._

**Why:** _Explain trade-offs — e.g. relying solely on "cart already checked
out" fails if the first attempt hadn't committed yet when the retry
arrives; a dedicated key table with a unique constraint gives an atomic,
race-safe check at the DB level._

**Consequences:** _e.g. Requires clients to generate and persist a key
across retries (documented in API docs); adds one extra table and a lookup
on the hot path; makes replay detection trivial and safe under concurrency._

---

### Decision: Concurrency Control for Inventory Deduction

**Context:** Two customers may check out simultaneously for the last unit
of a limited-inventory product.

**Options considered:**
- Optimistic concurrency (version column, retry on conflict).
- Pessimistic row-level locking (`SELECT ... FOR UPDATE`) inside a
  transaction.
- Atomic conditional update (`UPDATE products SET inventory = inventory - 1
  WHERE id = ? AND inventory >= ?`), checking rows-affected.
- Application-level mutex (only viable single-instance, in-memory).

**Choice:** _State your pick._

**Why:** _e.g. Atomic conditional update avoids taking explicit locks and
scales better under contention than `FOR UPDATE`, at the cost of needing to
handle a "zero rows affected" case explicitly as a business error rather
than a DB error._

**Consequences:** _e.g. Simple and DB-portable; but if checkout does
multiple inventory-affecting operations, you lose true multi-row atomicity
unless wrapped in a transaction — document how you handled multi-item
carts specifically._

---

### Decision: Coupon Generation and Redemption Safety

**Context:** Coupon generation must not double-fire for one milestone under
concurrent admin requests; redemption must not double-spend under
concurrent checkouts; a failed checkout must release its coupon.

**Options considered:**
- Unique constraint on `(milestone_number)` in a coupons table, letting the
  DB reject duplicate generation.
- Application-level check-then-insert (race-prone without a lock).
- Coupon status state machine (`available → reserved → redeemed`, with
  `reserved` rolled back on checkout failure) vs. direct
  `available → redeemed` transition inside the checkout transaction.

**Choice:** _State your pick._

**Why:** _Explain why a DB-level unique constraint beats an
application-level check for generation; explain whether you used a
reservation step or a single atomic transition for redemption, and why._

**Consequences:** _e.g. Coupon status transitions are now part of the
checkout transaction's atomicity boundary — if checkout rolls back, coupon
status rolls back with it for free._

---

### Decision: Money Representation and Rounding

**Context:** Discount calculations must never introduce floating-point
error, and totals must never go negative.

**Options considered:**
- Store prices as floats/decimals directly.
- Store all money as integer minor units (cents/paise).
- Use a `Decimal` type throughout with explicit precision.

**Choice:** _State your pick (integer minor units is the common, defensible
choice)._

**Why:** _e.g. Integer arithmetic sidesteps float rounding entirely; a
fixed rounding rule (e.g., discount = floor(gross * pct / 100)) is applied
once at discount-calculation time and documented; net total is clamped at
`max(0, gross - discount)`._

**Consequences:** _e.g. All API request/response bodies use integers for
money fields (documented in API docs) rather than floats, which is a
deliberate departure from naive JSON conventions — worth calling out
explicitly._

---

### Decision: Error Model

**Context:** API clients need to distinguish validation errors, conflict
errors (e.g., insufficient inventory), and not-found errors programmatically,
not just by HTTP status code.

**Options considered:**
- Plain HTTP status codes with free-text messages.
- A structured error envelope with a stable machine-readable `code` field
  plus a human message.
- RFC 7807 Problem Details.

**Choice:** _State your pick._

**Why:** _e.g. A structured `{"error": {"code": "INSUFFICIENT_INVENTORY",
"message": "...", "details": {...}}}` envelope lets clients branch on
`code` without parsing message text, while still mapping cleanly onto HTTP
status codes (409 for conflicts, 422 for validation, 404 for not found)._

**Consequences:** _e.g. Every error path in the codebase must produce this
shape consistently — worth a shared exception-handling middleware rather
than ad hoc `raise HTTPException` calls scattered through handlers._

---

### Decision: Persistence Choice (In-Memory vs. Database)

**Context:** The spec allows either; the choice affects how much
concurrency-safety work is "free" versus hand-built.

**Options considered:**
- Pure in-memory store with manual locks/mutexes.
- Embedded DB (SQLite) with transactions.
- Full RDBMS (Postgres) with transactions and row-level locking.

**Choice:** _State your pick._

**Why:** _Explain why you leaned on the database's own transactional
guarantees instead of reimplementing them — usually the stronger, more
defensible choice given the timebox, since it removes an entire class of
hand-rolled concurrency bugs._

**Consequences:** _e.g. Requires Docker/Postgres to run instead of "clone
and go"; in exchange, correctness under concurrency is largely inherited
from the DB rather than something you have to independently prove correct._

_(Add a sixth+ decision if you have one — payment abstraction, cart vs.
order boundary, report computation strategy (live query vs. materialized),
etc.)_

---

### Decision: Primary Key Type — UUID, not Auto-Increment Integer

**Context:** Every table needs a primary key. The choice propagates: carts,
orders, and coupons will reference products and each other, and IDs appear in
URLs and error `details`.

**Options considered:**
- Auto-increment `BIGINT` — compact, human-readable, trivially ordered.
- Server-generated `UUID` (v4) primary keys.
- Client-supplied external IDs / SKUs.

**Choice:** Server-generated `UUID` (SQLAlchemy `Uuid`, `default=uuid.uuid4`),
stored as a native Postgres `uuid`.

**Why:** IDs are exposed in URLs; sequential integers leak catalogue size and
invite enumeration of carts/orders. UUIDs are generated app-side without a
round-trip or a shared sequence, which stays correct if the app runs as
multiple instances. Milestone ordering ("every nth order") will use a dedicated
monotonic column on `orders`, not the PK, so losing integer ordering costs
nothing.

**Consequences:** 16 bytes vs 8 per key and per FK; slightly larger indexes.
All ID fields in API payloads are UUID strings. Not human-memorable in logs —
acceptable, since logs correlate on structured fields.

---

### Decision: Non-Negativity Enforced by DB CHECK Constraints

**Context:** Invariant 1 (no overselling) and invariant 7 (net total never
negative) both depend on `inventory` and `unit_price_cents` never going
negative. Application validation alone can be bypassed by a bug, a migration,
or a manual `UPDATE`.

**Options considered:**
- Pydantic / service-layer validation only.
- `CHECK (inventory >= 0)` and `CHECK (unit_price_cents >= 0)` at the table.
- Unsigned domain types.

**Choice:** Named CHECK constraints on the table
(`ck_products_inventory_non_negative`, `ck_products_unit_price_cents_non_negative`).

**Why:** The database is already the concurrency authority (see the persistence
decision); making it the correctness authority for the same values is
consistent and cheap. A conditional decrement like
`UPDATE products SET inventory = inventory - :q WHERE inventory >= :q` plus the
CHECK gives defence in depth: even a logic error cannot persist a negative row.
Named constraints so migrations can drop/recreate them deterministically.

**Consequences:** A violating write fails with a `CheckViolationError` that must
be translated to the structured envelope (a business error, not a 500) when it
can be triggered by user input in later modules.

---

### Decision: Seed Data as a Standalone Script, Not a Data Migration

**Context:** The spec requires at least 5 seeded products (one low-inventory,
one out-of-stock). This data has to be reproducible for local dev and tests.

**Options considered:**
- Alembic data migration that `INSERT`s the rows.
- Standalone idempotent script run on demand.
- Fixtures created only in test code.

**Choice:** Standalone script `app/features/products/seed.py`
(`python -m app.features.products.seed`, also `make seed`); idempotent by
skipping names that already exist. Tests build their own catalogue in a
rolled-back transaction and do not depend on the script.

**Why:** Migrations should be purely structural and reversible — mixing in
seed rows means the catalogue can't change without a new migration, and a
`downgrade()` would have to delete real data. A script keeps migrations clean,
lets the catalogue evolve freely, and makes re-seeding an explicit action
rather than a side effect of schema upgrade.

**Consequences:** Booting the stack does not populate products automatically;
the README documents the one extra command. Production seeding would be a
deliberate ops step, which is the desired behaviour.

---

### Decision: Test Isolation via Per-Test Transaction Rollback

**Context:** Endpoint tests need a real database (async SQLAlchemy, Postgres
CHECK constraints), must not leak state between tests, and must not depend on
the seed script having run.

**Options considered:**
- Separate test database, created/migrated/dropped per session.
- `TRUNCATE` between tests.
- Each test opens one transaction, the overridden `get_session` dependency
  shares it, and it is rolled back on teardown.

**Choice:** Per-test transaction rollback. `conftest.py` opens a connection +
outer transaction, binds an `AsyncSession` with
`join_transaction_mode="create_savepoint"`, and overrides `get_session` to
yield that session; teardown rolls back. A dedicated `NullPool` engine keeps
pooled connections from outliving pytest-asyncio's per-test event loop.

**Why:** No schema management in the test harness, fast, and total isolation —
a test may even `DELETE FROM products` to assert exact contents without
touching committed data. Matches the CLAUDE.md rule that no test depends on
another's leftover state.

**Consequences:** Tests run against whatever database `DATABASE_URL` points at
and require `alembic upgrade head` to have run there. Code that opens its own
session/engine instead of the injected one would escape the rollback — a
constraint to keep in mind for later modules.

---

## 4. Transaction, Concurrency, and Idempotency Strategy

> One consolidated narrative tying the above decisions together — walk
> through what actually happens, step by step, during a checkout call, and
> point to exactly where each invariant is enforced.

_e.g.:_

1. Client calls `POST /carts/{id}/checkout` with `Idempotency-Key: <key>`.
2. Server checks the `idempotency_keys` table for `<key>`; if found, returns
   the stored response verbatim (no re-processing).
3. Otherwise, opens a DB transaction:
   a. Locks/re-validates the cart (must be `open`).
   b. For each line item, attempts the atomic conditional inventory
      decrement; any zero-rows-affected result aborts the transaction with
      `INSUFFICIENT_INVENTORY`.
   c. If a coupon code was supplied, validates and atomically transitions
      its status to `redeemed`, scoped to this order; failure aborts.
   d. Computes gross total, discount, net total in integer cents.
   e. Inserts the order + order line items (price-snapshotted).
   f. Marks the cart `checked_out`.
   g. Records the idempotency key + response.
   h. Commits.
4. On any failure inside the transaction, the whole thing rolls back —
   inventory, coupon status, and cart status all revert together, so a
   failed checkout can be safely retried.

---

## 5. Money and Rounding Rules

> Restate concretely, in one place, so it's easy to audit.

- All money stored and computed as `int` **minor units** (e.g., cents).
- Discount = `floor(gross_total * discount_pct / 100)`.
- Net total = `max(0, gross_total - discount)`.
- API request/response money fields are integers, not floats or strings —
  documented explicitly in API docs to avoid client confusion.

---

## 6. Error Model

> Restate the envelope shape and the canonical error codes you use, as a
> quick reference.

```json
{
  "error": {
    "code": "INSUFFICIENT_INVENTORY",
    "message": "Only 2 units of SKU-123 are available.",
    "details": { "product_id": "SKU-123", "requested": 5, "available": 2 }
  }
}
```

| Code | HTTP status | Meaning |
|------|-------------|---------|
| `VALIDATION_ERROR` | 422 | Malformed or invalid request body |
| `NOT_FOUND` | 404 | Cart/product/order/coupon does not exist |
| `CART_ALREADY_CHECKED_OUT` | 409 | Checkout attempted on a closed cart |
| `INSUFFICIENT_INVENTORY` | 409 | Requested quantity exceeds availability |
| `INVALID_COUPON` | 422 | Coupon code doesn't exist or isn't eligible |
| `COUPON_ALREADY_REDEEMED` | 409 | Coupon was already spent |
| `MILESTONE_NOT_REACHED` | 422 | Admin requested coupon gen before milestone hit |
| `MILESTONE_ALREADY_REWARDED` | 409 | Coupon already generated for this milestone |

---

## 7. Implemented vs. Deferred

> Be direct here. This is not a weakness to hide — the spec explicitly asks
> for it, and vague or overclaiming answers read worse than an honest list.

**Implemented:**
- **Products module.** `Product` model (UUID PK, `name`, `unit_price_cents`,
  `inventory`, `created_at`/`updated_at`) with DB-level CHECK constraints
  forbidding negative price or inventory; Alembic migration with a working
  `downgrade()`; idempotent seed script (6 products, incl. one at 2 units and
  one at 0); read-only `GET /products` and `GET /products/{id}` (structured
  404 envelope on miss). Shared error-envelope infrastructure
  (`app/core/errors.py`: `AppError`/`NotFoundError`/`ValidationError` +
  handlers) was built here as the first consumer.
- _e.g. Full checkout transaction with idempotency, inventory locking,
  coupon redemption, order snapshotting._
- _e.g. Admin coupon generation with milestone uniqueness constraint._
- _e.g. Admin report as a live aggregate query._

**Intentionally deferred (with reasoning):**
- _e.g. Multi-instance distributed locking beyond what Postgres provides
  natively — deferred because a single-instance deployment with DB-level
  transactions satisfies the stated invariants; see Section 8 for how this
  would change with horizontal scaling._
- _e.g. Real payment gateway integration — used a fake payment abstraction
  that always succeeds, per the spec's explicit allowance._
- _e.g. Customer identity / auth — explicitly out of scope per spec._
- _e.g. Pagination on report/order listing endpoints — not required at this
  data scale within the timebox._

---

## 8. Scaling to Multiple Instances / Production Database

> Explain what changes if you go from one app instance + one DB to N
> instances behind a load balancer.

_e.g.:_
- Row-level locking and unique constraints already live in Postgres, so
  they continue to work correctly across multiple app instances without
  modification — the DB is the single source of truth for concurrency
  control, not in-process memory.
- If an in-memory idempotency cache or mutex were used instead of DB-backed
  ones, this would break under multiple instances; confirm your
  implementation avoids this (or flag it if it doesn't).
- Connection pooling (e.g., PgBouncer) becomes relevant at higher instance
  counts to avoid exhausting Postgres connections.
- Read-heavy endpoints (report, product listing) become candidates for a
  read replica or cache (e.g., Redis) once write/read ratios justify it —
  not necessary at this scale.
- Migrations (Alembic) need a deployment strategy that ensures schema
  changes are applied before new instances start serving traffic (e.g., a
  migration job/init container ahead of the rolling deploy).

---

## 9. AI Tool Usage

> Required. Be specific and honest — a concrete correction example is worth
> more than a generic "AI helped me write code faster" statement.

- **Tools used:** _e.g. Claude for scaffolding, design discussion, and
  reviewing my concurrency approach._
- **What AI got right / accelerated:** _e.g. Boilerplate FastAPI/Docker
  scaffolding, Alembic setup, first-draft test structure._
- **Example of correcting/rejecting AI output:** _e.g. "AI's first draft of
  the checkout transaction used optimistic concurrency with a version
  column and a retry loop on conflict. I rejected this because it adds
  client-visible retry complexity and doesn't compose cleanly with the
  idempotency-key mechanism; I replaced it with an atomic conditional
  UPDATE inside the same transaction as the idempotency check, which is
  simpler to reason about and doesn't require the client to ever see a
  'try again' response for a problem the server can just solve
  atomically."_
- **Example 2 (if applicable):** _e.g. AI's first draft of coupon
  redemption redeemed the coupon before validating inventory, which meant
  a failed checkout due to inventory would still burn the coupon — caught
  this while reviewing the invariant list and reordered the transaction
  steps._

---

## 10. What I'd Examine First With Two More Hours

> Shows you know your own gaps.

1. _e.g. Load-test the inventory decrement under higher concurrency (50+
   simultaneous requests) rather than the small N used in current tests, to
   surface any lock-contention or deadlock behavior under Postgres._
2. _e.g. Add pagination and filtering to the admin report and order-listing
   endpoints._
3. _e.g. Replace the fake payment abstraction with a slightly richer
   interface (e.g., simulate a payment failure rate) to exercise the "order
   creation succeeds but payment fails" rollback path more thoroughly._