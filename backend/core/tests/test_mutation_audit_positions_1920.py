"""Mutation audit redaction keys on schema positions, not variable names (#1920).

The client names its variables. Before this, ``_redact`` matched the
top-level variable names, so ``updateManifest(input: {rawManifest: $m})``
or ``setAgentSecretValue(value: $v)`` stored the manifest or the secret
verbatim. Several credential-bearing inputs (the step-up credential, PEM
private keys, agent env vars, IdP config) were not matched by any name
rule. GraphQL errors quote rejected variable values and print the source
line they point at, inline literals included; the audit row stored them
and Strawberry's error log printed them as-is.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from graphql import parse

from astrolift_manifest.env_edit import REDACTED_ENV_VALUE
from core.schema.audit import (
    SENSITIVE_INPUT_FIELDS,
    MutationAuditLog,
    redact_operation_variables,
)

_MASKED = "***REDACTED***"
_GUID = "00000000-0000-0000-0000-000000000001"

_MANIFEST = """\
name = "hello"

[env]
API_KEY = "sk-live-position-1"
"""


def _graphql_schema():
    from config.schema import schema

    return schema._schema


def _redact(query: str, variables: dict) -> tuple[dict, bool]:
    return redact_operation_variables(variables, document=parse(query), schema=_graphql_schema())


def test_sensitive_input_fields_exist_in_schema() -> None:
    """A rename that orphans an entry would silently stop masking it."""
    graphql_schema = _graphql_schema()
    for coordinate in SENSITIVE_INPUT_FIELDS:
        type_name, field_name = coordinate.split(".")
        input_type = graphql_schema.get_type(type_name)
        assert input_type is not None, coordinate
        assert field_name in input_type.fields, coordinate


def test_manifest_through_a_client_named_variable_is_masked() -> None:
    redacted, sensitive = _redact(
        "mutation($id: GUID!, $m: String!) { updateManifest(input: {id: $id, rawManifest: $m}) { ok } }",
        {"id": _GUID, "m": _MANIFEST},
    )
    assert "sk-live-position-1" not in redacted["m"]
    assert f'API_KEY = "{REDACTED_ENV_VALUE}"' in redacted["m"]
    assert redacted["id"] == _GUID
    assert sensitive is True


def test_secret_value_through_a_client_named_variable_is_masked() -> None:
    redacted, _ = _redact(
        'mutation($s: String!, $v: String!) { setAgentSecretValue(envSpecSlug: $s, envVar: "API_KEY", value: $v) { ok } }',
        {"s": "emr-triage", "v": "sk-live-position-2"},
    )
    assert redacted == {"s": "emr-triage", "v": _MASKED}


def test_input_object_variable_is_walked_against_its_type() -> None:
    redacted, _ = _redact(
        "mutation($in: UploadCustomDomainCertificateInput!) { uploadCustomDomainCertificate(input: $in) { ok } }",
        {
            "in": {
                "id": _GUID,
                "certificatePem": "-----BEGIN CERTIFICATE-----",
                "privateKeyPem": "pem-secret-3",
            }
        },
    )
    assert redacted["in"] == {
        "id": _GUID,
        "certificatePem": "-----BEGIN CERTIFICATE-----",
        "privateKeyPem": _MASKED,
    }


@pytest.mark.parametrize(
    ("query", "variables", "path"),
    [
        (
            'mutation($c: String!) { elevateAdminSession(input: {method: "password", credential: $c}) { ok } }',
            {"c": "hunter2-position-4"},
            ("c",),
        ),
        (
            "mutation($in: ElevateAdminSessionInput!) { elevateAdminSession(input: $in) { ok } }",
            {"in": {"method": "password", "credential": "hunter2-position-5"}},
            ("in", "credential"),
        ),
        (
            "mutation($in: ConnectExistingGithubAppInput!) { connectExistingGithubApp(input: $in) { ok } }",
            {"in": {"appId": "1", "privateKeyPem": "pem-secret-6"}},
            ("in", "privateKeyPem"),
        ),
        (
            'mutation($in: CreateAgentEnvironmentSpecInput!) { createAgentEnvironmentSpec(input: $in, orgId: "1") { ok } }',
            {"in": {"name": "a", "slug": "a", "agentType": "x", "envVars": {"DB_URL": "postgres://pw-7"}}},
            ("in", "envVars"),
        ),
        (
            "mutation($in: CreateIdentityProviderInput!) { createIdentityProvider(input: $in) { ok } }",
            {"in": {"kind": "saml", "config": {"sp_x509_key": "idp-secret-8"}}},
            ("in", "config"),
        ),
        (
            "mutation($in: RegisterTenantClusterInput!) { registerTenantCluster(input: $in) { ok } }",
            {
                "in": {
                    "slug": "c",
                    "name": "c",
                    "providerPluginSlug": "k8s",
                    "authMethod": "kubeconfig",
                    "authConfig": {"kubeconfig": "client-key-data: kube-secret-9"},
                }
            },
            ("in", "authConfig"),
        ),
    ],
)
def test_credential_bearing_inputs_are_masked(query: str, variables: dict, path: tuple[str, ...]) -> None:
    redacted, sensitive = _redact(query, variables)
    value = redacted
    for key in path:
        value = value[key]
    assert value == _MASKED
    assert sensitive is True


def test_json_scalar_keeps_the_key_name_heuristic() -> None:
    redacted, _ = _redact(
        "mutation($in: ConfigureProviderPluginInput!) { configureProviderPlugin(input: $in) { ok } }",
        {"in": {"pluginSlug": "gcp", "config": {"project": "p", "private_key": "gcp-secret-10"}}},
    )
    assert redacted["in"]["config"] == {"project": "p", "private_key": _MASKED}


def test_unbound_and_unplaceable_variables_are_masked() -> None:
    redacted, sensitive = _redact(
        'mutation($m: String!) { updateManifst(input: {id: "x", rawManifest: $m}) { ok } }',
        {"m": _MANIFEST, "extra": "sk-live-unbound-11"},
    )
    assert redacted == {"m": _MASKED, "extra": _MASKED}
    assert sensitive is True


def test_no_document_masks_everything() -> None:
    assert redact_operation_variables({"a": 1}, document=None, schema=_graphql_schema()) == (
        {"a": _MASKED},
        True,
    )


def test_ordinary_operation_is_recorded_as_is() -> None:
    redacted, sensitive = _redact(
        "mutation($k: String!, $e: Boolean!) { setOrganizationModule(input: {key: $k, enabled: $e}) { ok } }",
        {"k": "chat_studio_integration", "e": True},
    )
    assert redacted == {"k": "chat_studio_integration", "e": True}
    assert sensitive is False


# ---- through the extension -----------------------------------------


def _execute(query: str, variables: dict | None = None) -> MutationAuditLog:
    from config.schema import schema

    schema.execute_sync(
        query, variable_values=variables, context_value=SimpleNamespace(user=None, request=None)
    )
    return MutationAuditLog.objects.order_by("-pk").first()


@pytest.mark.django_db
def test_extension_stores_the_masked_variables() -> None:
    log = _execute(
        "mutation($id: GUID!, $m: String!) { updateManifest(input: {id: $id, rawManifest: $m}) { ok } }",
        {"id": _GUID, "m": _MANIFEST},
    )
    assert "sk-live-position-1" not in str(log.variables)
    assert log.variables["id"] == _GUID


@pytest.mark.django_db
def test_extension_masks_errors_that_quote_a_variable_value() -> None:
    log = _execute(
        'mutation($v: String!) { setAgentSecretValue(envSpecSlug: "x", envVar: "API_KEY", value: $v) { ok } }',
        {"v": {"token": "sk-live-error-12"}},
    )
    assert log.success is False
    assert log.errors
    assert "sk-live-error-12" not in str(log.errors)
    assert "sk-live-error-12" not in str(log.variables)


@pytest.mark.django_db
def test_extension_keeps_errors_for_an_ordinary_operation() -> None:
    """Positive control for the test above: the same kind of error keeps
    its text when nothing sensitive is involved."""
    log = _execute(
        "mutation($in: SetOrganizationModuleInput!) { setOrganizationModule(input: $in) { ok } }",
        {"in": {"key": "chat_studio_integration", "enabled": "not-a-boolean"}},
    )
    assert log.success is False
    assert any("not-a-boolean" in message for message in log.errors)


@pytest.mark.django_db
def test_extension_masks_every_variable_when_the_document_does_not_parse() -> None:
    log = _execute('mutation($v: String!) { setAgentSecretValue(value: $v "x" }', {"v": "sk-live-parse-14"})
    assert log.variables == {"v": _MASKED}


# ---- Strawberry's own error log ------------------------------------


def _error_log(query: str, variables: dict | None = None) -> str:
    """What ``strawberry.execution`` logged for one operation."""
    import logging

    from config.schema import schema

    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    strawberry_logger = logging.getLogger("strawberry.execution")
    handler = _Capture(level=logging.DEBUG)
    strawberry_logger.addHandler(handler)
    try:
        schema.execute_sync(
            query, variable_values=variables, context_value=SimpleNamespace(user=None, request=None)
        )
    finally:
        strawberry_logger.removeHandler(handler)
    assert records, "the operation logged no error, so the assertions below prove nothing"
    return "\n".join(logging.Formatter().format(record) for record in records)


@pytest.mark.django_db
def test_error_log_withholds_an_inline_secret_literal() -> None:
    """The source excerpt Strawberry prints under a validation error is
    the line the secret literal sits on."""
    logged = _error_log(
        'mutation { setAgentSecretValue(envSpecSlug: "x", envVar: "API_KEY", value: "sk-live-inline-13", bogus: 1) { ok } }'
    )
    assert "sk-live-inline-13" not in logged
    assert "message withheld" in logged


@pytest.mark.django_db
def test_error_log_withholds_a_rejected_secret_variable() -> None:
    logged = _error_log(
        'mutation($v: String!) { setAgentSecretValue(envSpecSlug: "x", envVar: "API_KEY", value: $v) { ok } }',
        {"v": {"token": "sk-live-error-16"}},
    )
    assert "sk-live-error-16" not in logged


@pytest.mark.django_db
def test_error_log_withholds_an_unparseable_document() -> None:
    logged = _error_log('mutation { login(username: "u", password: "hunter2-parse-17" }')
    assert "hunter2-parse-17" not in logged


@pytest.mark.django_db
def test_error_log_keeps_an_ordinary_operations_message() -> None:
    """Positive control: an error at a known, non-sensitive position keeps
    its text. (An unknown argument or input field is withheld: the value
    under a misspelt ``valeu:`` is still the secret.)"""
    logged = _error_log(
        'mutation { setOrganizationModule(input: {key: "chat_studio_integration", enabled: "yes"}) { ok } }'
    )
    assert "Boolean cannot represent a non boolean value" in logged
