.PHONY: fmt lint test typecheck verify clean coverage-matrix verification-manifests campaign-spend

fmt:
	ruff format .

lint:
	ruff check .

test:
	python -m pytest tests/ -v

# Regenerates docs/managed_service_coverage.md. tests/_sdk/test_coverage.py
# fails when the committed copy drifts from the matrix.
coverage-matrix:
	python -m scripts.coverage_matrix

# Regenerates verification/manifests/{happy-path,per-kind} from the campaign
# ledger. tests/_cert/test_collection.py fails when the committed tree drifts.
verification-manifests:
	python -m _cert.collection

# Regenerates docs/campaign_spend.md, the #1417 spend estimate. Offline by
# construction: it prices nothing, because prices come from the live pricing API.
# tests/_cert/test_spend.py fails when the committed report drifts.
campaign-spend:
	python -m scripts.campaign_spend

typecheck:
	mypy _sdk/ aws/ gcp/ azure/ k8s_native/

verify: fmt lint typecheck test

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .mypy_cache -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name "*.egg-info" -exec rm -rf {} +
