.PHONY: dev check

dev:
	uv run uvicorn guard.app:app --reload --port 8000

check:
	FAKE=1 uv run python scripts/check.py
	FAKE=1 uv run python scripts/check_redact.py
	python3 scripts/check_js.py web/index.html web/app.html web/listen-test.html && node --check web/listen.js
