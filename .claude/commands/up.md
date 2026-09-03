---
description: Build and start the stack, then verify both health endpoints
---

Boot the project and confirm it is healthy.

1. Ensure `.env` exists; if not, `cp .env.example .env`.
2. Run `docker compose up --build -d`.
3. Wait for the `db` healthcheck, then curl `localhost:8000/health` and
   `localhost:8000/health/db`.
4. Report the status of both endpoints. If `/health/db` is not
   `{"status": "ok", "db": "connected"}`, show the api container logs.
