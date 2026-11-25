.PHONY: setup setup-backend setup-glasses setup-notion smoke-live backend glasses sim test typecheck build pack

setup: setup-backend setup-glasses

setup-backend:
	python3 -m venv backend/.venv
	backend/.venv/bin/pip install --upgrade pip
	backend/.venv/bin/pip install -e "backend[dev]"

setup-glasses:
	cd glasses && npm install

setup-notion:
	PYTHONPATH=backend backend/.venv/bin/python -m app.scripts.setup_notion --write-env

smoke-live:
	PYTHONPATH=backend backend/.venv/bin/python -m app.scripts.smoke_live

backend:
	PYTHONPATH=backend backend/.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

glasses:
	cd glasses && npm run dev

sim:
	cd glasses && npm run sim

test:
	PYTHONPATH=backend backend/.venv/bin/pytest backend/tests -v
	cd glasses && npm test

typecheck:
	cd glasses && npm run typecheck

build:
	cd glasses && npm run build

pack:
	cd glasses && npm run pack
