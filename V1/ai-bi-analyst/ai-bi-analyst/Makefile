.PHONY: help setup backend frontend test test-backend test-frontend e2e lint samples docker clean

help:
	@echo "setup          install backend and frontend dependencies"
	@echo "backend        run the API on http://localhost:8000"
	@echo "frontend       run the UI on http://localhost:5173"
	@echo "samples        write synthetic sample datasets with known defects"
	@echo "test           run backend and frontend unit/integration tests"
	@echo "e2e            run the Playwright critical-path test"
	@echo "lint           ruff on the backend, tsc on the frontend"
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

e2e:
	cd frontend && npx playwright install --with-deps chromium && npm run e2e

lint:
	cd backend && . .venv/bin/activate && ruff check app tests
	cd frontend && npm run typecheck

docker:
	docker compose up --build

clean:
	rm -rf backend/.workspace backend/.pytest_cache frontend/dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
