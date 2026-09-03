.PHONY: up down logs migrate revision test shell

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f api

# Apply all migrations inside the running api container.
migrate:
	docker compose exec api alembic upgrade head

# Create a new migration: make revision m="add users table"
revision:
	docker compose exec api alembic revision --autogenerate -m "$(m)"

test:
	docker compose exec api pytest

shell:
	docker compose exec api bash
