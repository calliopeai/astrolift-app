.PHONY: fmt lint test typecheck verify clean

fmt:
	ruff format .

lint:
	ruff check .

test:
	python -m pytest tests/ -v

typecheck:
	mypy _sdk/ aws/ gcp/ azure/ k8s_native/

verify: fmt lint typecheck test

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .mypy_cache -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name "*.egg-info" -exec rm -rf {} +
