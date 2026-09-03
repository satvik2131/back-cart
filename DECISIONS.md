# Decisions

## Scaffold (FastAPI + Postgres + Docker)

- **Dependency file:** `requirements.txt` (+ `requirements-dev.txt`) rather than
  `pyproject.toml` — simpler for a container-only workflow, all versions pinned.
- **No `psycopg2-binary`:** Alembic runs through the async engine (`asyncpg`),
  so the sync driver is not needed.
- **DB host port 5434:** host 5432 is already used by another local Postgres.
  In-network the service is still `db:5432`.
- **`DATABASE_URL` is the single source of truth** for both the app and Alembic;
  `alembic/env.py` pulls it from `app.core.config.settings`, `alembic.ini`
  leaves `sqlalchemy.url` blank.
- **Empty `Base`, no models, no migration files.** `alembic upgrade head` is a
  clean no-op (only creates `alembic_version`).
- **Dev deps in the image:** keeps `docker compose exec api pytest` working for a
  scaffold; revisit with a multi-stage build if image size matters later.
