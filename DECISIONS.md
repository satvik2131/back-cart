# DECISIONS.md

## 0. Summary

An async FastAPI + PostgreSQL backend for an ecommerce checkout and rewards
service. The design goal throughout was **correctness under concurrency,
retries, and partial failure** rather than feature breadth.

**What's built:** products (read-only catalogue + seed), carts (create / view /
add / update / remove, live-computed totals), checkout (idempotent, single
transaction, atomic inventory decrement, immutable snapshotted orders), coupons
(milestone generation guarded by a unique constraint; redemption wired into the
checkout transaction), and a live admin report. 51 tests, including 5 that fire
genuinely concurrent requests (`asyncio.gather`) and assert an invariant held —
each verified to fail when its guard is removed.

**Where correctness comes from:** the database. Every concurrency guarantee is a
row lock (`SELECT ... FOR UPDATE`), a unique constraint, an atomic conditional
`UPDATE ... WHERE ...` with a rows-affected check, or a CHECK constraint — never
an in-process lock. The design stays correct run as N app instances against one
database.

**Effort:** roughly a focused day, built in an AI-pair-programmed session
(see §9), phase by phase: scaffold → products → carts → checkout/orders →
coupons → admin report → polish, with the test suite green and a commit series
at each phase boundary.

**Not built (see §7):** product write/restock API, a payment-failure
abstraction, auth, pagination, structured request logging — each deferred with a
reason.

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
| 1 | Does checkout use the price at add-to-cart time or the current product price? | **Current price.** Cart items store no price; the cart view computes every line subtotal and the cart total live from `Product.unit_price_cents` at read time. The price snapshot is taken only when the order is created at checkout (invariant 3). See §3 "Cart Items Hold No Price". |
| 2 | What happens if inventory drops below a cart's requested quantity before checkout? | Adding/updating a cart item runs a **soft** inventory check (409 `INSUFFICIENT_INVENTORY` if the resulting quantity exceeds current stock) but reserves nothing. The **authoritative** check is an atomic conditional decrement inside the checkout transaction; a cart that was fine at add-time can still fail checkout with `INSUFFICIENT_INVENTORY`, and the client must reduce quantity and retry. No partial fulfilment. See §3 "Soft Inventory Check at Add-Time". |
| 3 | Can a coupon be applied to a cart below some minimum order value? | **No minimum.** `discount_cents = floor(gross × percent / 100)`, clamped to `gross`; `net = gross - discount ≥ 0`. `discount_percent` is CHECK-constrained to 1..100, so net can't go negative even at 100%. |
| 4 | Is a coupon single-use globally or single-use per customer? | **Single-use globally** (no customer identity in scope). First successful checkout to win the `WHERE status='available'` race consumes it; `code` is UNIQUE. |
| 5 | Does a milestone count all created orders or only successful ones? | **Only `orders` rows with `status='success'`.** A failed checkout rolls back and writes no order, so "created" and "successful" coincide here — but the report and milestone query both filter on `status='success'` explicitly. |
| 6 | What is the idempotency key — client-supplied header, or derived from cart state? | **Client-supplied `Idempotency-Key` HTTP header**, required (missing → 422). The full response body + status are stored keyed by it; a replay returns them verbatim. See §3 "Idempotency Strategy for Checkout". |
| 9 | What is the coupon milestone cadence and discount size? | Config: `COUPON_MILESTONE_EVERY` (default 5) and `COUPON_DISCOUNT_PERCENT` (default 10). Milestones are `n, 2n, 3n, …`; `generate` always targets the latest one reached. |
| 10 | Does the test suite run against the app database? | **No.** The suite provisions a separate `<db>_test` database (schema from ORM metadata) so it never mutates dev data. Override with `TEST_DATABASE_URL`. |
| 7 | Can an item quantity of 0 be added, or must it be removed instead? | **Quantity must be ≥ 1 on `POST /carts/{id}/items`** (rejected 422 otherwise, enforced by both the Pydantic schema and a `quantity > 0` DB CHECK). On `PATCH .../items/{item_id}`, **quantity 0 means remove** the line; negative is 422. |
| 8 | `product_id` that is malformed vs. well-formed but unknown when adding an item. | Malformed (not a UUID) → **422 `VALIDATION_ERROR`** (schema). Well-formed but no such product → **404 `NOT_FOUND`**. Both are rejected; neither is silently accepted. |

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

**Choice:** Client-supplied `Idempotency-Key` **HTTP header**, required on
`POST /carts/{id}/checkout` (missing → 422). On success the full response body
and status code are stored in an `idempotency_keys` table whose **primary key is
the key itself** (so it is unique and indexed). A request whose key is already
present returns the stored body + status verbatim without re-running anything.

**Why:**
- *Header, not derived from cart content:* a derived key would change if the cart
  were edited between the original attempt and the retry, defeating the purpose.
  A client-owned key is stable across retries by construction.
- *"Cart already checked out" is not enough on its own:* if the first attempt has
  not committed when the retry arrives, the cart still looks `open` and the retry
  would proceed. The key table closes that window — and the checkout also does a
  second key lookup after it acquires the cart lock, so a retry that lost the
  race still replays the winner's response instead of getting a 409.
- *Key as primary key:* the DB rejects a concurrent duplicate insert; no
  application-level check-then-insert race.
- Storing the whole response (not just the order id) means a replay is a single
  row read with zero risk of re-deriving a different body.

**Consequences:** Clients must generate and persist a key across retries
(documented on the endpoint). One extra table and one indexed lookup on the hot
path. A key is bound to the cart it was first used with — presenting it for a
different cart is a client bug and returns `409 IDEMPOTENCY_KEY_REUSED` rather
than silently replaying the wrong order. Keys are never garbage-collected in
scope (small, and needed for the lifetime of retry windows); a TTL sweep is a
production follow-up.

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

**Choice:** **Atomic conditional `UPDATE`**, one per cart line, inside the
checkout transaction:

```sql
UPDATE products SET inventory = inventory - :qty
WHERE id = :product_id AND inventory >= :qty
```

`rowcount == 0` means the guard failed → raise `InsufficientInventoryError`
(409, naming the product) → the whole transaction rolls back.

**Why:** The `WHERE inventory >= :qty` predicate is evaluated by Postgres *after*
it takes the row lock (READ COMMITTED re-reads the row), so two concurrent
checkouts for the last unit cannot both pass — the second sees the decremented
value and affects zero rows. No explicit `SELECT ... FOR UPDATE` on products, no
version column / retry loop. Invariant 1 is enforced at the one statement that
changes inventory. A regression test (product seeded with 1 unit, two concurrent
checkouts) confirms exactly one 201 + one 409 and final inventory 0; swapping in
a naive read-then-write decrement makes that test fail (both succeed).

**Consequences:** Multi-item carts decrement product-by-product within the single
transaction; if line 3 fails, lines 1–2 (and everything else) roll back, so
inventory is never left partially decremented — covered by a dedicated test.
Decrements on *different* products don't contend. A deadlock is theoretically
possible if two carts hold the same two products in opposite order; ordering the
decrements by `product_id` (or product name, as done here for a stable total)
avoids it.

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

**Choice:**
- *Generation:* `coupons.milestone_number` is UNIQUE. `generate_coupon` counts
  successful orders, computes the latest milestone
  (`(count // n) * n`), and **just inserts** the coupon. A unique-violation is
  caught and translated to `409 MILESTONE_ALREADY_REWARDED`. No pre-check.
- *Redemption:* a single atomic transition, **no reservation state**. Inside the
  checkout transaction, after inventory has been secured:
  `UPDATE coupons SET status='redeemed', redeemed_at=now(),
  redeemed_by_order_id=:id WHERE id=:id AND status='available'`; `rowcount != 1`
  → `409 COUPON_ALREADY_REDEEMED`, transaction aborts.

**Why:**
- A check-then-insert for generation has a TOCTOU window — two concurrent admin
  calls both see "no coupon for milestone 10" and both insert. The unique
  constraint is evaluated atomically by the DB; the loser's insert simply fails.
  A regression test fires two concurrent generate calls and asserts exactly one
  coupon row.
- A `reserved` state would need its own cleanup path (what releases a
  reservation if the app crashes mid-checkout?). Because redemption lives *in*
  the checkout transaction, "release on failure" is free — a rollback reverts
  the status change along with the inventory decrements and the (uncommitted)
  order. The `WHERE status='available'` + `rowcount` check is the same
  concurrency primitive used for inventory: the second of two concurrent
  checkouts using one coupon re-reads the row after the lock, sees `redeemed`,
  and aborts. A regression test confirms exactly one of two concurrent
  same-coupon checkouts succeeds with the discount.
- Redemption runs **after** the inventory loop, so a checkout that 409s on stock
  never issues the coupon UPDATE at all — the coupon stays `available`
  (invariant 6). Tested explicitly.

**Consequences:** Coupon status transitions are part of the checkout
transaction's atomicity boundary — rollback reverts them for free. A coupon's
lifecycle is strictly `available → redeemed` (one-way); there is no un-redeem.
`redeemed_by_order_id` (FK, RESTRICT) records which order consumed it, and a
CHECK keeps `status`/`redeemed_at`/`redeemed_by_order_id` mutually consistent.

---

### Decision: Money Representation and Rounding

**Context:** Discount calculations must never introduce floating-point
error, and totals must never go negative.

**Options considered:**
- Store prices as floats/decimals directly.
- Store all money as integer minor units (cents/paise).
- Use a `Decimal` type throughout with explicit precision.

**Choice:** **Integer minor units (cents) everywhere** — DB columns
(`Integer`), all arithmetic, and JSON request/response fields. No `float`, no
`Decimal`, no strings. Field names always carry the unit
(`unit_price_cents`, `gross_total_cents`, `discount_cents`, …).

**Why:** Integer arithmetic has no rounding error to reason about. The only place
rounding happens is the discount, with one fixed rule applied once:
`discount_cents = floor(gross_total_cents * discount_percent / 100)` (Python `//`
on ints). `net_total_cents = gross_total_cents - discount_cents`, and because
`discount_percent` is constrained to `1..100` (Phase 2) the discount can never
exceed the gross, so `net` is always ≥ 0. The `orders` table backs this with
CHECK constraints: `discount_cents >= 0`, `discount_cents <= gross_total_cents`,
`net_total_cents = gross_total_cents - discount_cents` — invariant 7 cannot be
violated by any write, not even a manual one.

**Consequences:** API money fields are integers, which clients must not treat as
dollars — called out on every schema. `discount_cents` stored on an order is the
*effective, applied* discount (already clamped), so the stored `net` is exactly
`gross - discount` with no separate "requested vs applied" bookkeeping.

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
constraint to keep in mind for later modules. Genuine-concurrency tests can't
use this fixture (one shared transaction can't exercise row locking); they use a
second `committing_client` fixture with real per-request sessions and clean up
their own rows.

---

### Decision: Cart Items Hold No Price — Totals Computed Live

**Context:** A cart is edited over time while product prices may change. Where
does the price a customer "sees" come from — a value copied into the cart line
when the item was added, or the product's current price?

**Options considered:**
- Snapshot `unit_price_cents` onto `CartItem` at add-time; cart total is the sum
  of snapshots.
- Store no price on `CartItem`; compute every subtotal and the cart total from
  `Product.unit_price_cents` at read/checkout time.
- Store the snapshot *and* a live price, exposing a `price_changed` flag.

**Choice:** `CartItem` has only `cart_id`, `product_id`, `quantity`, timestamps —
**no price column.** `GET /carts/{id}` joins to `products` and computes
`line_subtotal_cents = product.unit_price_cents * quantity` and
`total_cents = sum(...)` on every read.

**Why:** Invariant 3 requires the *order* to be an immutable snapshot; it says
nothing about the cart, which is mutable scratch space by nature. Keeping the
cart price-free means there is exactly one place a snapshot is taken (order
creation at checkout), so there's no risk of a stale cart snapshot disagreeing
with what checkout actually charges. It also removes a whole class of "cart says
X, checkout charged Y" bugs. The price a customer sees in the cart is always the
price checkout will use (subject to change between the two calls, which is
inherent and also true of the snapshot approach).

**Consequences:** The cart view is a small join + arithmetic rather than a column
read — negligible at this scale. A customer can see their cart total move if a
price changes before they check out; acceptable and arguably more honest. The
checkout module is solely responsible for snapshotting price onto `OrderLine`.

---

### Decision: Soft Inventory Check at Add-Time, Authoritative Check at Checkout

**Context:** "Product has enough inventory available (409 if not)" is required on
add-to-cart, but carts are not orders and adding to a cart must not reserve
stock (that would let an abandoned cart deny inventory to real buyers, and there
is no cart expiry in scope).

**Options considered:**
- Reserve inventory on add (decrement now, restore on remove / expiry).
- Soft check only: compare requested quantity to current stock, 409 on failure,
  but change no inventory.
- No check at add-time; surface everything at checkout.

**Choice:** **Soft check.** `POST`/`PATCH` items compares the *resulting* line
quantity against `Product.inventory` and returns
`409 INSUFFICIENT_INVENTORY` if it exceeds it, but never touches `inventory`.
The authoritative guarantee is the atomic conditional decrement
(`UPDATE products SET inventory = inventory - :q WHERE id = :id AND inventory >= :q`)
inside the checkout transaction (a later module).

**Why:** Gives immediate feedback for the common case (you can't add 100 of a
thing there are 3 of) without the correctness burden and lifecycle complexity of
reservations. Because it's advisory, a mild race here is harmless — two carts
can both "pass" the soft check for the last unit; exactly one will win at
checkout, which is where invariant 1 (no overselling) is actually enforced.

**Consequences:** A cart can pass every add-time check and still fail checkout
with `INSUFFICIENT_INVENTORY` — documented (§2 row 2) and the reason the same
error code is shared by both layers. The soft check is best-effort, not a
guarantee, and tests treat it as such.

---

### Decision: Cart Status as a Native Postgres Enum

**Context:** `Cart.status` is `open` or `checked_out` and gates every mutation.
A typo'd or unexpected status would silently disable the checkout-once guard.

**Options considered:**
- `VARCHAR` + application-level validation.
- `VARCHAR` + a CHECK constraint on the allowed set.
- A native Postgres `ENUM` type (`cart_status`).

**Choice:** Native `ENUM` type `cart_status` with values `open`, `checked_out`
(stored lowercase via SQLAlchemy `values_callable`). The Alembic migration
creates the type on upgrade and drops it on downgrade.

**Why:** The database rejects any value outside the two labels — no code path,
migration, or manual `UPDATE` can invent a third state. Consistent with the
project's "the database is the correctness authority" stance (cf. the CHECK
constraints on `products`).

**Consequences:** Adding a future status (e.g. `abandoned`) needs an
`ALTER TYPE ... ADD VALUE` migration rather than a code change — an acceptable
and deliberate cost for the safety.

---

### Decision: A Cart-Row Lock Serialises Cart Mutations

**Context:** Concurrent `POST /carts/{id}/items` for the same product must not
create duplicate rows or lose an increment, and no mutation may proceed against
a cart that a concurrent checkout is turning into `checked_out`.

**Options considered:**
- Application mutex / in-process lock (rejected by house rules; wrong under
  multiple app instances).
- `INSERT ... ON CONFLICT (cart_id, product_id) DO UPDATE SET quantity = ... + EXCLUDED.quantity`
  — lock-free atomic upsert.
- `SELECT ... FOR UPDATE` on the `carts` row at the start of every mutation,
  serialising all mutations of that cart, with the unique constraint as backstop.

**Choice:** `SELECT ... FOR UPDATE` on the cart row (via
`get_open_cart_for_update`), held for the whole request transaction, plus the
`(cart_id, product_id)` unique constraint and `quantity > 0` CHECK as structural
guarantees.

**Why:** One lock covers everything a cart mutation needs to be correct: the
status check can't go stale, concurrent adds of the same product serialise into
one row + one summed quantity, and a future checkout that also takes this lock
will naturally exclude concurrent edits. It's a database mechanism, so it stays
correct across app instances. The upsert alternative handles the duplicate-row
case elegantly but doesn't help the status-stability or future-checkout
concerns, so a single consistent mechanism was preferred. A regression test
fires 15 genuinely parallel adds and asserts one row with the summed quantity;
with the lock removed it fails with a unique-constraint violation.

**Consequences:** Mutations on the *same* cart serialise (fine — a single cart is
not a contention hotspot); different carts are unaffected. The lock is a plain
`FOR UPDATE`, not `SKIP LOCKED` / `NOWAIT`, so a slow mutation briefly queues
others on that cart.

---

### Decision: Checkout Is One Transaction, With an Explicit Rollback

**Context:** Checkout does several writes that must all happen or none: N
inventory decrements, an order header, N order lines, the cart status flip, the
idempotency record. Invariant 4 needs a failed attempt to leave *nothing*
behind so the client can retry the same key from a clean slate.

**Options considered:**
- One DB transaction per checkout, commit at the end, rely on the session /
  connection-pool reset to roll back on error.
- One DB transaction with an **explicit** `rollback()` in the endpoint's
  exception path.
- Multiple smaller transactions (decrement, then order, then …) with
  compensating writes on failure.

**Choice:** A single transaction for the whole flow — the request's session,
committed once at the very end by the router. The checkout endpoint wraps the
service call in `try / except: await session.rollback(); raise`. Step order:
key lookup → lock cart → validate open → load lines → **decrement inventory** →
compute totals → insert order + snapshotted lines → flip cart to `checked_out` →
write idempotency row → commit.

**Why:** Smaller transactions with compensation are exactly the hand-rolled
distributed-transaction logic the house rules push back on — one transaction
gets atomicity and rollback for free from Postgres. The rollback is written
explicitly rather than left to pool-return semantics because (a) it makes the
guarantee obvious in the code, and (b) the test suite's shared-transaction
fixture doesn't return connections to a pool between requests, so an implicit
reset wouldn't fire there — the "insufficient inventory leaves nothing partially
decremented" test would give a false pass. Inventory is decremented *before* the
order is built so the common failure (not enough stock) costs the least work,
and — relevant to Phase 2 — so a coupon is only ever touched after inventory has
already succeeded.

**Consequences:** The whole checkout holds the cart-row lock and the affected
product-row locks for its duration; at this scale that's sub-millisecond. If
checkout grew expensive (e.g. a real payment call), the payment step would want
to sit outside the DB transaction with its own idempotency handling — noted for
future work.

---

## 4. Transaction, Concurrency, and Idempotency Strategy

> One consolidated narrative tying the above decisions together — walk
> through what actually happens, step by step, during a checkout call, and
> point to exactly where each invariant is enforced.

**Checkout, step by step (as implemented):**

1. Client calls `POST /carts/{id}/checkout` with header `Idempotency-Key: <key>`
   (missing → `422 VALIDATION_ERROR`), optional body `{"coupon_code": "..."}`.
2. Fast path: look up `<key>` in `idempotency_keys`. If present and bound to
   this cart → return the stored body + status verbatim (invariant 4). If
   present but bound to a different cart → `409 IDEMPOTENCY_KEY_REUSED`.
3. Otherwise the request's single transaction runs:
   a. `SELECT ... FOR UPDATE` the cart row. Missing → 404.
   b. If the cart is not `open`: look up `<key>` again (a concurrent request
      with the same key may have just committed) — replay if found, else
      `409 CART_ALREADY_CHECKED_OUT`. *(enforces invariant 2, invariant 4)*
   c. Load cart lines joined to products, ordered by product name. Empty →
      `422 EMPTY_CART`.
   d. For each line: `UPDATE products SET inventory = inventory - qty WHERE id = ?
      AND inventory >= qty`. `rowcount == 0` → `409 INSUFFICIENT_INVENTORY`
      (transaction aborts). *(enforces invariant 1)*
   e. `gross_total_cents` = Σ(current `unit_price_cents` × qty).
   f. If `coupon_code` given: load the coupon. Missing → `422 INVALID_COUPON`;
      not `available` → `409 COUPON_ALREADY_REDEEMED`. Compute
      `discount_cents = min(floor(gross × percent / 100), gross)`. No write yet —
      this is *after* (d), so a stock failure never touches a coupon.
   g. `net_total_cents = max(0, gross_total_cents - discount_cents)`.
   h. Insert the `Order` (with totals) and one `OrderItem` per line, each
      carrying `product_name_snapshot` + `unit_price_cents_snapshot`.
      *(invariant 3; invariant 7 also DB-enforced by CHECK)*
   i. If a coupon was quoted: `UPDATE coupons SET status='redeemed',
      redeemed_at=now(), redeemed_by_order_id=<order.id>
      WHERE id=<coupon.id> AND status='available'`; `rowcount != 1` →
      `409 COUPON_ALREADY_REDEEMED`. *(enforces invariant 6)*
   j. Flip the cart to `checked_out`.
   k. Insert the `idempotency_keys` row (key, cart_id, response body, 201).
   l. Commit.
4. On any exception in step 3 the endpoint calls `session.rollback()` and
   re-raises — inventory, order rows, cart status and coupon status all revert
   together, so the client can safely retry with the same key and a coupon on a
   doomed checkout is never burned.

---

## 5. Money and Rounding Rules

> Restate concretely, in one place, so it's easy to audit.

- All money stored and computed as `int` **minor units** (cents). No floats, no
  `Decimal`, no strings, anywhere.
- Every money field name carries the unit: `*_cents`.
- Discount = `floor(gross_total_cents * discount_percent / 100)` — Python integer
  `//`. Applied once, at checkout, only after inventory succeeds.
- `net_total_cents = gross_total_cents - discount_cents`. Never negative:
  `discount_percent` is bounded `1..100` so the discount can't exceed the gross,
  and the `orders` table has CHECK constraints
  (`discount_cents <= gross_total_cents`,
  `net_total_cents = gross_total_cents - discount_cents`) that reject any write
  that would break invariant 7.
- API request/response money fields are integers, not floats or strings.

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
| `CONFLICT` | 409 | Generic state conflict (base for the specific 409s below) |
| `CART_ALREADY_CHECKED_OUT` | 409 | Mutation or checkout attempted on a checked-out cart |
| `INSUFFICIENT_INVENTORY` | 409 | Requested quantity exceeds availability (checkout: authoritative; cart: soft) |
| `EMPTY_CART` | 422 | Checkout attempted on a cart with no items |
| `IDEMPOTENCY_KEY_REUSED` | 409 | `Idempotency-Key` already used for a different cart |
| `INVALID_COUPON` | 422 | Coupon code doesn't exist or isn't eligible |
| `COUPON_ALREADY_REDEEMED` | 409 | Coupon was already spent |
| `MILESTONE_NOT_REACHED` | 422 | Admin requested coupon gen before milestone hit |
| `MILESTONE_ALREADY_REWARDED` | 409 | Coupon already generated for this milestone |
| `METHOD_NOT_ALLOWED` | 405 | Wrong HTTP method for the route |
| `DB_UNAVAILABLE` | 503 | `/health/db` connectivity probe failed |
| `INTERNAL_ERROR` | 500 | Unexpected server error (logged; no detail leaked) |

Framework-raised errors (unmatched route, wrong method, malformed path param)
are wrapped in the same envelope by shared handlers in `app/core/errors.py`, so
**every** error response — from a business rule or from FastAPI itself — has this
shape.

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
- **Carts module.** `Cart` (native `cart_status` enum) and `CartItem`
  (unique `(cart_id, product_id)`, `quantity > 0` CHECK, `ON DELETE CASCADE`
  from cart / `RESTRICT` to product, no price column). `POST /carts`,
  `GET /carts/{id}` with live-computed line subtotals + total, and
  `POST`/`PATCH`/`DELETE` on `/carts/{id}/items` — re-adding a product
  increments quantity, `PATCH` to quantity 0 removes, every mutation takes a
  `SELECT ... FOR UPDATE` lock on the cart row and rejects a checked-out cart
  with `409 CART_ALREADY_CHECKED_OUT`. Soft add-time inventory check
  (`409 INSUFFICIENT_INVENTORY`). `ConflictError` + `CartAlreadyCheckedOutError`
  / `InsufficientInventoryError` added to `app/core/errors.py`. Tests include a
  15-way genuinely-concurrent add asserting no lost updates or duplicate rows.
  Checkout itself is **not** part of this module.
- **Checkout & Orders module (Phase 1).** `Order` (immutable, `order_status`
  enum, invariant-7 CHECK constraints), `OrderItem` (name + unit price
  snapshotted at checkout, `line_total` CHECK, unique per product), and
  `idempotency_keys` (key = PK, stores full response body + status).
  `POST /carts/{id}/checkout` — required `Idempotency-Key` header, verbatim
  replay of a stored response, single transaction with explicit rollback:
  `FOR UPDATE` cart lock → atomic conditional `UPDATE ... WHERE inventory >= qty`
  per line → order + snapshot lines → cart `checked_out` → idempotency row.
  `GET /orders/{id}` returns only snapshots, never re-derived. Error codes
  `EMPTY_CART`, `IDEMPOTENCY_KEY_REUSED` added. Seven tests including two real
  concurrency tests (two carts / one unit → exactly one 201 + one 409, final
  inventory 0; same cart + same key ×8 concurrently → exactly one order);
  verified both fail if the atomic decrement / idempotency guard is removed.
- **Coupons module (Phase 2).** `Coupon` (native `coupon_status` enum; UNIQUE
  `milestone_number` and `code`; CHECK keeping `status`/`redeemed_*` consistent;
  `discount_percent` CHECK 1..100). `POST /admin/coupons/generate` — counts
  successful orders, targets the latest milestone, insert-and-catch on the
  UNIQUE constraint (`422 MILESTONE_NOT_REACHED` / `409
  MILESTONE_ALREADY_REWARDED`). Redemption wired into the **same** checkout
  transaction: quote discount after inventory succeeds, then atomic
  `UPDATE ... WHERE status='available'` after the order row exists. Config
  `COUPON_MILESTONE_EVERY` / `COUPON_DISCOUNT_PERCENT`. 10 tests including two
  concurrency tests (concurrent generate → one coupon; concurrent same-coupon
  checkout → one wins with discount, other 409), plus "stock failure leaves the
  coupon available" and "100% coupon → net 0".
- **Admin report (Phase 3).** `GET /admin/report` — a single pass of live
  aggregate queries over `orders`/`order_items`/`coupons`, no writes, no cache:
  successful order count, gross/discount/net revenue, coupons
  generated/available/redeemed, quantity sold per product. 3 tests: empty state
  → all zeros, exact reconciliation against seeded data (incl. net vs. per-order
  sum), and "call twice → identical, nothing mutated".
- **Test database.** The pytest suite provisions and uses `<db>_test` (schema
  built from ORM metadata), so it never touches the dev database.

**Intentionally deferred (with reasoning):**
- **Structured log events on mutations.** The house rules call for a structured
  log event on every mutating operation (cart mutation, checkout, coupon
  generation) with the correlation/idempotency key. No logging infrastructure
  exists in `app/core` yet, and doing it properly (JSON formatter, request-id
  middleware, uvicorn log config) is its own slice of work. Deferred to a
  dedicated step so it lands once, consistently, across carts + checkout +
  coupons rather than ad hoc per module. The carts endpoints are otherwise
  observable via the error envelope + HTTP status.
- **Payment.** Treated as implicitly successful — a committed checkout *is* a
  successful payment. No fake-failure payment abstraction; if one were added, a
  persisted `FAILED` order state and a payment-idempotency layer outside the DB
  transaction would need designing (noted in §3 "Checkout Transaction Boundary").
- **Product write API (create / update / delete / restock).** Out of scope per
  the spec — products are fixed by the seed script. The `Product` model,
  CHECK constraints and admin framing are all in place, so a
  `POST/PATCH /admin/products` would be a thin addition, but building it now
  would be scope creep. Inventory only ever *decreases*, at checkout.
- **Multi-instance distributed locking** beyond what Postgres provides —
  unnecessary: every concurrency guard here is a DB row lock, unique constraint,
  or conditional `UPDATE`, all of which stay correct across N app instances
  sharing one database (see §8).
- **Customer identity / auth** — explicitly out of scope per spec; admin
  endpoints are unauthenticated but clearly namespaced and tagged.
- **Pagination** on `GET /products` and the report — not needed at seed-data
  scale within the timebox.

---

## 8. Scaling to Multiple Instances / Production Database

> Explain what changes if you go from one app instance + one DB to N
> instances behind a load balancer.

- **Nothing changes for correctness.** Every concurrency guard is in Postgres:
  the `FOR UPDATE` cart lock, the `products.inventory >= qty` conditional
  update, the `coupons.milestone_number` / `code` unique constraints, the
  `coupons.status = 'available'` conditional update, the `idempotency_keys` PK,
  and the invariant-7 CHECK constraints. All of these are evaluated by the
  database under its own locking, so they hold identically whether one app
  process or fifty are talking to the one database.
- **No in-process state is load-bearing.** There is no in-memory idempotency
  cache, no `asyncio.Lock`, no module-level mutable state used for correctness —
  a grep for `Lock(`/`Semaphore(` in `app/` turns up nothing. The idempotency
  record is a table row, not a dict.
- **Connection pooling** (PgBouncer) becomes relevant at higher instance counts
  so N app pools don't exhaust `max_connections`. The app already sets
  `pool_pre_ping=True`.
- **Read scaling:** `GET /products` and `GET /admin/report` are pure reads and
  could move to a read replica once the write/read ratio justifies it. Not
  needed at this scale.
- **Migrations:** a rolling deploy needs `alembic upgrade head` to run (as a
  job / init container) before new instances serve traffic, and migrations kept
  backward-compatible for the window where both versions run.
- **The one thing to watch:** long-held row locks. The checkout transaction
  holds the cart-row lock and the affected product-row locks for its whole
  duration. That's sub-millisecond today; if a real (slow) payment call were
  added it would need to move outside the DB transaction with its own
  idempotency, so it doesn't serialise every checkout of the same products.

---

## 9. AI Tool Usage

> Required. Be specific and honest — a concrete correction example is worth
> more than a generic "AI helped me write code faster" statement.

- **Tools used:** Claude Code (Anthropic) as a pair-programmer for the whole
  build — scaffolding, model/endpoint drafts, first-pass tests, and DECISIONS
  prose — with every diff reviewed against the invariant list and the house
  rules before committing, phase by phase.
- **What AI accelerated:** the Docker/Compose/Alembic scaffold, the repetitive
  shape of each feature module (model → schema → service → router → tests), the
  migration boilerplate, and turning a verbal concurrency argument into a
  running `asyncio.gather` regression test.

- **Correction 1 — enum storage (subtle, caught in review).** The first
  `Cart.status` model used `Enum(CartStatus, name="cart_status")` with
  `server_default="open"`. SQLAlchemy, given a Python enum, stores the member
  **name** (`OPEN`), not the value (`open`) — so the generated `CREATE TYPE`
  would have had labels `OPEN`/`CHECKED_OUT` while the server default was the
  string `open`, and inserts would have failed. Fixed by adding
  `values_callable=lambda e: [m.value for m in e]` to every enum column and
  regenerating the migration. Verified against the live `\dT+ cart_status`.

- **Correction 2 — test isolation had a bad side effect.** The AI's first
  concurrency-test setup ran committing requests against the app's configured
  database and cleaned up with `DELETE`. Running the suite wiped the dev
  catalogue (and a `RESTRICT` FK from `order_items` made the cleanup itself
  fail after a manual smoke test). Redesigned: `conftest.py` now provisions a
  dedicated `<db>_test` database from ORM metadata; the dev database is never
  touched.

- **Correction 3 — concurrency test that didn't actually test concurrency.**
  An early version asserted `[201, 409]` but, on inspection, the two requests
  were being awaited sequentially. Rewrote them to fire through one
  `asyncio.gather`, and — as a standing check — deliberately replaced the
  atomic `UPDATE ... WHERE inventory >= qty` with a naive read-then-write and
  confirmed the test then fails (both checkouts succeed, overselling). The
  same break-it check is documented for the idempotency and coupon guards.

- **Correction 4 — ORM lifecycle in failure-path tests.** Several tests
  accessed an ORM object (`widget.id`) *after* the checkout endpoint had
  called `session.rollback()` on the shared test session, which expires
  attributes and triggered a sync lazy-load (`MissingGreenlet`). Fixed by
  capturing scalar ids before the call rather than re-reading through the ORM.

---

## 10. What I'd Examine First With Two More Hours

> Shows you know your own gaps.

1. **Structured request logging** (the one house-rule gap). A JSON formatter, a
   request-id middleware, and one structured event per mutation
   (checkout / cart change / coupon generation) keyed by the idempotency /
   correlation id, at `INFO` for business outcomes and `ERROR` for the
   unexpected. The `_unhandled_exception_handler` already logs 500s; this
   extends it to the success and expected-failure paths.
2. **Higher-concurrency soak.** Current concurrency tests use N = 2–15. I'd run
   50–100 simultaneous checkouts against a small inventory and a shared coupon
   to watch for lock contention and, specifically, deadlocks between the cart
   lock and multiple product-row locks (decrements are already ordered by
   product name to avoid the classic ABBA case, but I'd want to see it under
   load, not just argue it).
3. **Idempotency-key retention.** Keys are stored forever. I'd add a
   `created_at`-based TTL sweep (a periodic job) and decide the retention window
   from realistic client retry behaviour.
4. **Report grouping under product renames.** `quantity_sold_by_product` groups
   by `(product_id, product_name_snapshot)`, so a product renamed mid-history
   shows as two rows. I'd decide whether to group by `product_id` alone and
   join `products` for the current name, and add a test for that case.
5. **Payment abstraction with a simulated failure rate**, to exercise a
   persisted `FAILED` order path and confirm the rollback story holds when the
   failure is *after* inventory and coupon have been touched.