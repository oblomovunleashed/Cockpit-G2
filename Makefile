.PHONY: setup setup-backend setup-glasses backend glasses sim test typecheck build pack

setup: setup-backend setup-glasses

setup-backend:
	python3 -m venv backend/.venv
	backend/.venv/bin/pip install --upgrade pip
	backend/.venv/bin/pip install -e "backend[dev]"

setup-glasses:
	cd glasses && npm install

backend:
	PYTHONPATH=backend backend/.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

glasses:
	cd glasses && npm run dev

sim:
	npx --yes @evenrealities/evenhub-simulator http://localhost:5173

test:
	PYTHONPATH=backend backend/.venv/bin/pytest backend/tests -v
	cd glasses && npm test

typecheck:
	cd glasses && npm run typecheck

build:
	cd glasses && npm run build

pack: build
	cd glasses && npx --yes @evenrealities/evenhub-cli pack app.json dist -o cockpit.ehpk --sdk-ver 0.0.16
