.PHONY: up down logs migrate revision seed test shell pgadmin

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

# Insert the product catalogue (idempotent).
seed:
	docker compose exec api python -m app.features.products.seed

test:
	docker compose exec api pytest

shell:
	docker compose exec api bash

# Start the optional pgAdmin UI on http://localhost:5051
pgadmin:
	docker compose --profile tools up -d pgadmin
