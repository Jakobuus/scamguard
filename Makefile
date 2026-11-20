.PHONY: dev check

dev:
	uv run uvicorn guard.app:app --reload --port 8000

check:
	FAKE=1 uv run python scripts/check.py
