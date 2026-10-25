SEED ?= 42
FROM ?= 2026-02-02
TO ?= 2026-05-29
SPEED ?= fast
PY = .venv/bin/python

.PHONY: up down setup world clean-world replay test test-llm api web demo eval

setup:
	uv venv -p 3.12 .venv && uv pip install -p .venv -e '.[dev]'

up:
	docker compose up -d --wait

down:
	docker compose down

world:
	$(PY) -m bp.world.generate --seed $(SEED)

clean-world:
	$(PY) -m bp.world.generate --seed $(SEED) --clean

replay:
	$(PY) -m bp.engine.replay --seed $(SEED) --from $(FROM) --to $(TO)

test:
	$(PY) -m pytest -q

test-llm:
	BP_LLM=claude BP_LLM_STRICT=1 $(PY) -m pytest -q -m llm

eval:
	$(PY) -m bp.eval

api:
	cd web && npm install --silent && npx vite build && cd .. && $(PY) -m uvicorn bp.api.app:app --port 8000

web:
	cd web && npm install && npm run dev

demo:
	$(PY) -m bp.demo
