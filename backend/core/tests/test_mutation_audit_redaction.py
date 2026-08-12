from core.schema.audit import _is_secret_operation, _redact


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
