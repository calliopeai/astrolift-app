from core.schema.audit import _is_secret_operation, _redact

_STAGED_TOML = """\
astrolift_version = 1

[env]
API_KEY = "sk-live-super-secret"

[[workloads]]
name = "web"
"""


def test_update_manifest_raw_manifest_variable_masks_env_values() -> None:
    """updateManifest's ``rawManifest`` argument carries the caller's full
    staged manifest text, [env] literals included. #1920's field-level
    fix only redacts the *response*; the mutation audit log persists the
    *input* variables verbatim unless this path is also gated."""
    variables = {"input": {"id": "app-guid", "rawManifest": _STAGED_TOML}}
    redacted = _redact(variables)
    assert "sk-live-super-secret" not in redacted["input"]["rawManifest"]
    assert "API_KEY" in redacted["input"]["rawManifest"]
    assert redacted["input"]["id"] == "app-guid"


def test_register_app_manifest_raw_variable_masks_env_values() -> None:
    """``registerApp``'s inline seed manifest uses the opposite field
    order (``manifestRaw``) from ``updateManifest``'s ``rawManifest`` —
    both must be covered."""
    variables = {"input": {"sourceRepo": "acme/demo", "manifestRaw": _STAGED_TOML}}
    redacted = _redact(variables)
    assert "sk-live-super-secret" not in redacted["input"]["manifestRaw"]
    assert "API_KEY" in redacted["input"]["manifestRaw"]


def test_bulk_import_dotenv_text_variable_masks_values() -> None:
    variables = {"input": {"appSlug": "demo", "dotenvText": "API_KEY=sk-live-super-secret\n"}}
    redacted = _redact(variables)
    assert "sk-live-super-secret" not in redacted["input"]["dotenvText"]
    assert "API_KEY=[REDACTED]" in redacted["input"]["dotenvText"]


def test_secret_operation_redacts_generic_value_recursively():
    payload = {
        "envSpecSlug": "emr-triage",
        "value": "direct-secret",
        "rows": [{"key": "TOKEN", "value": "nested-secret"}],
    }

    assert _redact(payload, secret_operation=True) == {
        "envSpecSlug": "emr-triage",
        "value": "***REDACTED***",
        "rows": [{"key": "TOKEN", "value": "***REDACTED***"}],
    }


def test_generic_values_are_redacted_even_when_operation_name_is_not_secret():
    assert _redact({"value": "display text", "apiToken": "secret"}) == {
        "value": "***REDACTED***",
        "apiToken": "***REDACTED***",
    }


def test_agent_and_bundle_secret_actions_are_classified_sensitive():
    assert _is_secret_operation("agents.secret.set") is True
    assert _is_secret_operation("agents.secret.bundle.key.set") is True
    assert _is_secret_operation("secret_bundle.rotate") is True
    assert _is_secret_operation("app.update") is False
