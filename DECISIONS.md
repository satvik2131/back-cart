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
| 1 | Does checkout use the price at add-to-cart time or the current product price? | **Current price.** Cart items store no price; the cart view computes every line subtotal and the cart total live from `Product.unit_price_cents` at read time. The price snapshot is taken only when the order is created at checkout (invariant 3). See §3 "Cart Items Hold No Price". |
| 2 | What happens if inventory drops below a cart's requested quantity before checkout? | Adding/updating a cart item runs a **soft** inventory check (409 `INSUFFICIENT_INVENTORY` if the resulting quantity exceeds current stock) but reserves nothing. The **authoritative** check is an atomic conditional decrement inside the checkout transaction; a cart that was fine at add-time can still fail checkout with `INSUFFICIENT_INVENTORY`, and the client must reduce quantity and retry. No partial fulfilment. See §3 "Soft Inventory Check at Add-Time". |
| 3 | Can a coupon be applied to a cart below some minimum order value? | _e.g. No minimum; discount simply cannot make total negative (clamped at zero)._ |
| 4 | Is a coupon single-use globally or single-use per customer? | _e.g. Single-use globally (no customer identity in scope per spec) — first successful redemption consumes it._ |
| 5 | Does a milestone count all created orders or only ones that survive checkout validation? | _e.g. Only orders that reach `SUCCESS` status count toward milestones — abandoned/failed checkouts don't count._ |
| 6 | What is the idempotency key — client-supplied header, or derived from cart state? | _e.g. Client-supplied `Idempotency-Key` header, required on checkout; server stores request hash + response for replay detection._ |
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

**Checkout, step by step (as implemented — Phase 1; step 3d is Phase 2):**

1. Client calls `POST /carts/{id}/checkout` with header `Idempotency-Key: <key>`
   (missing → `422 VALIDATION_ERROR`).
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
   e. *(Phase 2)* If `coupon_code` given: `UPDATE coupons SET status='redeemed',
      redeemed_by_order_id=? WHERE code=? AND status='available'`; `rowcount==0`
      → `409 COUPON_ALREADY_REDEEMED` / `422 INVALID_COUPON`. Runs **after** (d)
      so a stock failure never touches the coupon. *(enforces invariant 6)*
   f. `gross_total_cents` = Σ(current `unit_price_cents` × qty).
      `discount_cents` = 0 in Phase 1 (Phase 2: `floor(gross × pct / 100)`).
      `net_total_cents = gross_total_cents - discount_cents`. *(invariant 7,
      also DB-enforced by CHECK)*
   g. Insert the `Order` and one `OrderItem` per line, each carrying
      `product_name_snapshot` + `unit_price_cents_snapshot`. *(invariant 3)*
   h. Flip the cart to `checked_out`.
   i. Insert the `idempotency_keys` row (key, cart_id, response body, 201).
   j. Commit.
4. On any exception in step 3 the endpoint calls `session.rollback()` and
   re-raises — inventory, order rows, cart status and (Phase 2) coupon status
   all revert together, so the client can safely retry with the same key.

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
  Coupon redemption is a documented extension point, not yet wired (below).
- _Phase 2:_ Coupons — generation + redemption wired into the checkout
  transaction.
- _Phase 3:_ Admin report as a live aggregate query.

**Intentionally deferred (with reasoning):**
- **Structured log events on mutations.** The house rules call for a structured
  log event on every mutating operation (cart mutation, checkout, coupon
  generation) with the correlation/idempotency key. No logging infrastructure
  exists in `app/core` yet, and doing it properly (JSON formatter, request-id
  middleware, uvicorn log config) is its own slice of work. Deferred to a
  dedicated step so it lands once, consistently, across carts + checkout +
  coupons rather than ad hoc per module. The carts endpoints are otherwise
  observable via the error envelope + HTTP status.
- **Coupon redemption at checkout (until Phase 2).** `POST /carts/{id}/checkout`
  accepts an optional `coupon_code` in the request body *now* (the
  `CheckoutRequest` schema), but Phase 1 ignores it — coupons don't exist yet.
  Phase 2 extends the *same* checkout transaction (step 3e in §4) rather than
  adding a parallel path. Until then every order has `discount_cents = 0`.
- **Payment.** Treated as implicitly successful — a committed checkout *is* a
  successful payment. No fake-failure payment abstraction; if one were added, a
  persisted `FAILED` order state and a payment-idempotency layer outside the DB
  transaction would need designing (noted in §3 "Checkout Transaction Boundary").
- _e.g. Multi-instance distributed locking beyond what Postgres provides
  natively — deferred because a single-instance deployment with DB-level
  transactions satisfies the stated invariants; see Section 8 for how this
  would change with horizontal scaling._
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