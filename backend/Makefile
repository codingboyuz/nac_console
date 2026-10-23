UV ?= uv
RUN = $(UV) run

.PHONY: install lint format type test test-unit test-integration cov migrate downgrade run worker beat openapi security ci

install:
	$(UV) sync --all-groups
	$(RUN) pre-commit install

lint:
	$(RUN) ruff check .
	$(RUN) black --check .

format:
	$(RUN) ruff check --fix .
	$(RUN) black .

type:
	$(RUN) mypy app

test:
	$(RUN) pytest

test-unit:
	$(RUN) pytest tests/unit

test-integration:
	$(RUN) pytest tests/integration tests/e2e

cov:
	$(RUN) pytest --cov=app --cov-report=term-missing --cov-report=xml

migrate:
	$(RUN) alembic upgrade head

downgrade:
	$(RUN) alembic downgrade base

run:
	$(RUN) uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --reload

worker:
	$(RUN) celery -A app.workers.celery_app worker -Q config,polling,discovery,alerts,default -l info

beat:
	$(RUN) celery -A app.workers.celery_app beat -l info

openapi:
	$(RUN) python -m app.cli openapi export --output openapi.json

security:
	$(RUN) bandit -c pyproject.toml -r app
	$(RUN) pip-audit

ci: lint type cov security openapi
