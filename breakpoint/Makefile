SEED ?= 42
FROM ?= 2026-07-01
TO ?= 2026-10-23
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
	BP_LLM=claude $(PY) -m pytest -q -m llm --count 3

eval:
	$(PY) -m bp.eval

api:
	$(PY) -m uvicorn bp.api.app:app --port 8000 --reload

web:
	cd web && npm install && npm run dev

demo:
	$(PY) -m bp.demo
