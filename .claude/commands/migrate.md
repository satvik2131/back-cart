---
description: Autogenerate and apply an Alembic migration inside Docker
---

Create and apply a schema migration. `$ARGUMENTS` is the migration message.

1. Make sure new/changed models are imported so `Base.metadata` sees them.
2. Generate: `docker compose exec api alembic revision --autogenerate -m "$ARGUMENTS"`
3. Open the generated file under `alembic/versions/` and check the `upgrade` /
   `downgrade` ops match the intended change — remove any spurious operations.
4. Apply: `docker compose exec api alembic upgrade head`
5. Report the revision id and what it changed.
