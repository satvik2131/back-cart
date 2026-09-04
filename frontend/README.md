# back-cart — frontend demo

A small React + Vite app whose only job is to **visibly prove the backend works
end to end** — real requests, real latency, real errors. Not a polished product;
see the "Frontend Rules" section of [`../.claude/CLAUDE.md`](../.claude/CLAUDE.md).

## Run

### With docker compose (default)

From the repo root, `docker compose up --build` starts this app alongside the
API and database — open **http://localhost:5173**. It runs the Vite dev server
with the source mounted (live reload) and proxies `/api/*` to the `api` service.
The first build installs npm dependencies into the image.

### Standalone (against the compose API)

```bash
npm install
npm run dev                     # http://localhost:5173
```

The dev server proxies `/api/*` to `http://localhost:8000` by default, so the
API needs no CORS. Override with `VITE_API_TARGET` (proxy target) or
`VITE_API_BASE` (client base URL).

```bash
npm run build                   # type-check + production build into dist/
npm run preview                 # serve the built bundle
```

## What it demonstrates

- **Products** — the seeded catalogue with live inventory; a loading skeleton
  while fetching.
- **Cart** — create a cart, add items (quantity capped at displayed inventory,
  but the backend stays the source of truth), update / remove lines. The cart
  total always comes from the backend response, never recomputed client-side.
- **Checkout** — a button that is disabled while the request is in flight (so a
  double-click sends exactly one request). It generates a fresh
  `Idempotency-Key` per new attempt and **reuses the same key** when you retry
  after a failure — the key and "Retry checkout (same key)" state are shown in
  the UI. On success the placed order is shown with its snapshot totals.
- **Errors** — every failure shows the backend's actual `code`, `message`, and
  `details` (e.g. `INSUFFICIENT_INVENTORY`, `COUPON_ALREADY_REDEEMED`,
  `MILESTONE_NOT_REACHED`) — never a generic message.
- **Admin** (visually separated, unauthenticated per spec) — generate a coupon
  (success or the exact error), a coupon-code input used at checkout, and an
  on-demand report that is re-fetched each time, never cached stale.

## Layout

```
src/
  api/         one function per backend endpoint + one fetch wrapper that
               parses the {"error": {code,message,details}} envelope
  hooks/       useAsync (on-mount/deps fetch) and useAction (button-triggered),
               both exposing loading + typed ApiError
  components/  ProductList · Cart · CheckoutButton · AdminPanel · OrderReceipt
               · ErrorBanner · LoadingSpinner  — one responsibility each
  App.tsx      composition + the small amount of shared state (cart id,
               coupon code, refetch signals)
```

No CSS framework, component library, router, or state-management library —
plain CSS and React's built-in state, per the project's dependency rules. No
frontend test suite (the backend tests are what matter).
