.PHONY: help install migrate seed run test test-pg cov lint up down logs shell schema

help:
	@echo "install   Install dev dependencies"
	@echo "migrate   Apply database migrations"
	@echo "seed      Seed catalogue + demo accounts"
	@echo "run       Start the dev server on :8000"
	@echo "test      Run the suite on in-memory SQLite"
	@echo "test-pg   Run the suite against local Postgres"
	@echo "cov       Run the suite with a coverage report"
	@echo "up        docker compose up --build"
	@echo "down      docker compose down -v"
	@echo "schema    Write the OpenAPI schema to openapi.yaml"

install:
	pip install -r requirements-dev.txt

migrate:
	python manage.py migrate

seed:
	python manage.py seed_catalog

run:
	python manage.py runserver 0.0.0.0:8000

test:
	pytest

test-pg:
	DATABASE_URL=postgresql://eve:eve@localhost:5432/eve_diagnostics pytest

cov:
	pytest --cov --cov-report=term-missing

lint:
	ruff check .

up:
	docker compose up --build

down:
	docker compose down -v

logs:
	docker compose logs -f web worker

shell:
	python manage.py shell

schema:
	python manage.py spectacular --file openapi.yaml
