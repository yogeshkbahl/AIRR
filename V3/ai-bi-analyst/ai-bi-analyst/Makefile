.PHONY: help setup backend frontend test test-backend test-frontend e2e lint audit verify samples docker clean

help:
	@echo "setup          install backend and frontend dependencies"
	@echo "backend        run the API on http://localhost:8000"
	@echo "frontend       run the UI on http://localhost:5173"
	@echo "samples        write synthetic sample datasets with known defects"
	@echo "test           run backend and frontend unit/integration tests"
	@echo "e2e            run the Playwright critical-path test"
	@echo "lint           ruff on the backend, tsc on the frontend"
	@echo "audit          dependency advisories for both stacks"
	@echo "verify         lint + tests + audit, the full pre-ship gate"
	@echo "docker         docker compose up --build"

setup:
	cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
	cd frontend && npm install

backend:
	cd backend && . .venv/bin/activate && uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

samples:
	cd backend && . .venv/bin/activate && python samples/generate_samples.py

test: test-backend test-frontend

test-backend:
	cd backend && . .venv/bin/activate && pytest

test-frontend:
	cd frontend && npm run test

e2e:  ## needs the backend running on :8000
	cd frontend && npx playwright install --with-deps chromium && npm run e2e

lint:
	cd backend && . .venv/bin/activate && ruff check app tests && ruff format --check app tests
	cd frontend && npm run typecheck

audit:  ## dependency advisories, both stacks; fails on a finding
	cd backend && . .venv/bin/activate && pip-audit -r requirements.txt --strict --progress-spinner off
	cd frontend && npm run audit

verify: lint test audit  ## everything that must be green before shipping
	@echo "All gates passed."

docker:
	docker compose up --build

clean:
	rm -rf backend/.workspace backend/.pytest_cache frontend/dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
