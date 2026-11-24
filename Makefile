.PHONY: install test test-unit test-eval lint playground run-cli server terraform-validate

install:
	uv sync --extra dev

test:
	uv run pytest -v

test-unit:
	uv run pytest tests/unit -v

test-eval:
	uv run adk eval app tests/eval/evalsets/travel_concierge_golden.evalset.json \
		--config_file_path=tests/eval/eval_config.json \
		--print_detailed_results

lint:
	uv run ruff check app tests

playground:
	uv run adk web .

run-cli:
	uv run python -m app.cli

server:
	uv run uvicorn app.fast_api_app:app --host 0.0.0.0 --port 8080

terraform-validate:
	cd deployment/terraform && terraform init -backend=false && terraform validate
