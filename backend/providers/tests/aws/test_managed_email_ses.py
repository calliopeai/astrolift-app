"""Tests for AWS SES managed-service driver (#375).

Covers the full ManagedServiceDriver protocol surface:
provision (idempotent, identity-verification kicked, smtp creds
+ configuration set + deletion-protection marker stored), update,
deprovision four-corner matrix (delete_data x force_destroy),
status state-mapping, binding env-var shape, snapshot + restore.

All cloud calls go through moto; no real AWS access is required.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import boto3
import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    UpdateSpec,
)
from aws.managed._base import ManagedServiceError, parse_handle
from aws.managed.email_ses import (
    KIND,
    AmazonSESDriver,
    SESEmailConfig,
    _from_address_for,
    _generate_smtp_password,
    _safe,
    _smtp_endpoint_for,
)


@pytest.fixture
def aws_mock() -> Generator:
    """Single moto context covering SES + SESv2 + Secrets Manager."""
    from moto import mock_aws

    with mock_aws():
        ses = boto3.client("ses", region_name="us-east-1")
        sesv2 = boto3.client("sesv2", region_name="us-east-1")
        sm = boto3.client("secretsmanager", region_name="us-east-1")
        yield {"ses": ses, "sesv2": sesv2, "sm": sm}


@pytest.fixture
def ses_client(aws_mock) -> Any:
    return aws_mock["ses"]


@pytest.fixture
def sesv2_client(aws_mock) -> Any:
    return aws_mock["sesv2"]


@pytest.fixture
def sm_client(aws_mock) -> Any:
    return aws_mock["sm"]


@pytest.fixture
def driver(aws_mock) -> AmazonSESDriver:
    return AmazonSESDriver(
        config=SESEmailConfig(
            region="us-east-1",
            base_domain="astrolift.test",
        ),
        ses_client=aws_mock["ses"],
        sesv2_client=aws_mock["sesv2"],
        secrets_client=aws_mock["sm"],
    )


def _spec(**overrides: Any) -> ProvisionSpec:
    base = dict(
        organization_id="1",
        organization_slug="acme",
        app_id="1",
        app_slug="api",
        environment_id="1",
        environment_name="prod",
        tenant_cluster_id="aws-prod",
        service_handle_hint="email",
        size="small",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- provision --------------------------------------------------


def test_provision_verifies_domain_identity(
    driver: AmazonSESDriver,
    ses_client,
) -> None:
    result = driver.provision(_spec())
    assert result.ok is True
    kind, identity = parse_handle(result.handle)
    assert kind == KIND
    listed = ses_client.list_identities()
    assert identity in listed["Identities"]


def test_provision_records_pending_state_for_new_domain(
    driver: AmazonSESDriver,
    ses_client,
) -> None:
    result = driver.provision(_spec())
    _, identity = parse_handle(result.handle)
    attrs = ses_client.get_identity_verification_attributes(
        Identities=[identity],
    )["VerificationAttributes"]
    assert identity in attrs


def test_provision_idempotent(driver: AmazonSESDriver) -> None:
    a = driver.provision(_spec())
    b = driver.provision(_spec())
    assert a.ok and b.ok
    assert a.handle == b.handle
    assert "already registered" in b.message


def test_provision_with_explicit_identity_email(
    driver: AmazonSESDriver,
    ses_client,
) -> None:
    result = driver.provision(
        _spec(config={"identity": "ops@example.com"}),
    )
    assert result.ok
    _, identity = parse_handle(result.handle)
    assert identity == "ops@example.com"
    listed = ses_client.list_identities()
    assert "ops@example.com" in listed["Identities"]


def test_provision_with_explicit_identity_domain(
    driver: AmazonSESDriver,
    ses_client,
) -> None:
    result = driver.provision(
        _spec(config={"identity": "explicit.example.com"}),
    )
    assert result.ok
    listed = ses_client.list_identities()
    assert "explicit.example.com" in listed["Identities"]


def test_provision_without_base_domain_or_explicit_identity_errors() -> None:
    from moto import mock_aws

    with mock_aws():
        d = AmazonSESDriver(
            config=SESEmailConfig(region="us-east-1"),
            ses_client=boto3.client("ses", region_name="us-east-1"),
            secrets_client=boto3.client(
                "secretsmanager",
                region_name="us-east-1",
            ),
        )
        result = d.provision(_spec())
        assert not result.ok
        assert "base_domain" in result.message


def test_provision_stores_smtp_secret_pair(
    driver: AmazonSESDriver,
    sm_client,
) -> None:
    result = driver.provision(_spec())
    _, identity = parse_handle(result.handle)
    user_name = f"astrolift/ses/{_safe(identity)}/smtp-access-key-id"
    pass_name = f"astrolift/ses/{_safe(identity)}/smtp-secret-key"
    assert sm_client.get_secret_value(SecretId=user_name)["SecretString"]
    assert sm_client.get_secret_value(SecretId=pass_name)["SecretString"]


def test_provision_creates_configuration_set(
    driver: AmazonSESDriver,
    ses_client,
) -> None:
    result = driver.provision(_spec())
    _, identity = parse_handle(result.handle)
    cset_name = driver._configuration_set_name_for(identity)  # type: ignore[attr-defined]
    desc = ses_client.describe_configuration_set(
        ConfigurationSetName=cset_name,
    )
    assert desc["ConfigurationSet"]["Name"] == cset_name


def test_provision_wires_sns_event_destination_when_arn_configured(
    aws_mock,
) -> None:
    """#756: when ``SESEmailConfig.sns_event_destination_arn`` is set,
    provision wires a ``platform-sns`` configuration-set event
    destination so SES publishes the six event kinds through SNS."""
    arn = "arn:aws:sns:us-east-1:123456789012:astrolift-ses-events"
    d = AmazonSESDriver(
        config=SESEmailConfig(
            region="us-east-1",
            base_domain="astrolift.test",
            sns_event_destination_arn=arn,
        ),
        ses_client=aws_mock["ses"],
        sesv2_client=aws_mock["sesv2"],
        secrets_client=aws_mock["sm"],
    )
    result = d.provision(_spec())
    _, identity = parse_handle(result.handle)
    cset_name = d._configuration_set_name_for(identity)  # type: ignore[attr-defined]

    desc = aws_mock["ses"].describe_configuration_set(
        ConfigurationSetName=cset_name,
        ConfigurationSetAttributeNames=["eventDestinations"],
    )
    destinations = desc.get("EventDestinations") or []
    matching = [d for d in destinations if d.get("Name") == "platform-sns"]
    assert len(matching) == 1
    sns = matching[0].get("SNSDestination") or {}
    assert sns.get("TopicARN") == arn
    expected_event_types = {"send", "delivery", "bounce", "complaint", "open", "click"}
    assert set(matching[0].get("MatchingEventTypes") or []) == expected_event_types


def test_provision_is_idempotent_for_sns_event_destination(aws_mock) -> None:
    """Calling provision twice with the same SNS ARN must not throw —
    EventDestinationAlreadyExists is the happy path."""
    arn = "arn:aws:sns:us-east-1:123456789012:astrolift-ses-events"
    d = AmazonSESDriver(
        config=SESEmailConfig(
            region="us-east-1",
            base_domain="astrolift.test",
            sns_event_destination_arn=arn,
        ),
        ses_client=aws_mock["ses"],
        sesv2_client=aws_mock["sesv2"],
        secrets_client=aws_mock["sm"],
    )
    first = d.provision(_spec())
    second = d.provision(_spec())
    assert first.ok is True
    assert second.ok is True


def test_provision_skips_sns_destination_when_arn_blank(aws_mock) -> None:
    """When neither the dataclass field nor the Django setting carries
    an ARN, provision must NOT wire an event destination."""
    d = AmazonSESDriver(
        config=SESEmailConfig(
            region="us-east-1",
            base_domain="astrolift.test",
            sns_event_destination_arn="",
        ),
        ses_client=aws_mock["ses"],
        sesv2_client=aws_mock["sesv2"],
        secrets_client=aws_mock["sm"],
    )
    result = d.provision(_spec())
    _, identity = parse_handle(result.handle)
    cset_name = d._configuration_set_name_for(identity)  # type: ignore[attr-defined]

    desc = aws_mock["ses"].describe_configuration_set(
        ConfigurationSetName=cset_name,
        ConfigurationSetAttributeNames=["eventDestinations"],
    )
    destinations = desc.get("EventDestinations") or []
    assert not any(d.get("Name") == "platform-sns" for d in destinations)


def test_provision_records_deletion_protection_marker_default_on(
    driver: AmazonSESDriver,
    sm_client,
) -> None:
    result = driver.provision(_spec())
    _, identity = parse_handle(result.handle)
    marker = f"astrolift/ses/{_safe(identity)}/protection"
    resp = sm_client.get_secret_value(SecretId=marker)
    assert resp["SecretString"] == "1"


def test_provision_marker_off_when_disabled(
    driver: AmazonSESDriver,
    sm_client,
) -> None:
    result = driver.provision(
        _spec(config={"deletion_protection": False}),
    )
    _, identity = parse_handle(result.handle)
    marker = f"astrolift/ses/{_safe(identity)}/protection"
    resp = sm_client.get_secret_value(SecretId=marker)
    assert resp["SecretString"] == "0"


def test_provision_surfaces_verify_failure(
    sm_client,
    ses_client,
    sesv2_client,
) -> None:
    d = AmazonSESDriver(
        config=SESEmailConfig(
            region="us-east-1",
            base_domain="astrolift.test",
        ),
        ses_client=ses_client,
        sesv2_client=sesv2_client,
        secrets_client=sm_client,
    )

    def boom(**_kwargs):
        raise RuntimeError("simulated AWS failure")

    sesv2_client.create_email_identity = boom  # type: ignore[assignment]
    result = d.provision(_spec(service_handle_hint="boomer"))
    assert not result.ok
    assert "create_email_identity" in result.message


# ---- ownership (#2029) -------------------------------------------


def test_provision_tags_a_new_identity_with_the_ownership_envelope(
    driver: AmazonSESDriver,
    sesv2_client,
) -> None:
    result = driver.provision(_spec(organization_slug="acme", app_slug="api"))
    assert result.ok
    _, identity = parse_handle(result.handle)
    tags = {t["Key"]: t["Value"] for t in sesv2_client.get_email_identity(EmailIdentity=identity)["Tags"]}
    assert tags["astrolift.io/organization"] == "acme"
    assert tags["astrolift.io/app"] == "api"


def test_provision_refuses_another_orgs_identity(aws_mock) -> None:
    """Org B naming org A's verified identity (explicitly, or by a
    collision in the derived name) must be refused, not adopted (#2029)."""
    shared_identity = {"identity": "shared.example.com"}
    victim = AmazonSESDriver(
        config=SESEmailConfig(region="us-east-1"),
        ses_client=aws_mock["ses"],
        sesv2_client=aws_mock["sesv2"],
        secrets_client=aws_mock["sm"],
    )
    victim.provision(_spec(organization_slug="acme", config=dict(shared_identity)))

    def boom(*_args, **_kwargs):
        raise AssertionError("mutating call must not run once adoption is refused")

    attacker_ses = aws_mock["ses"]
    attacker_sesv2 = aws_mock["sesv2"]
    attacker_sm = aws_mock["sm"]
    attacker_ses.create_configuration_set = boom  # type: ignore[assignment]
    attacker_ses.verify_domain_dkim = boom  # type: ignore[assignment]
    attacker_sesv2.create_email_identity = boom  # type: ignore[assignment]
    attacker_sesv2.tag_resource = boom  # type: ignore[assignment]
    attacker_sm.create_secret = boom  # type: ignore[assignment]
    attacker_sm.put_secret_value = boom  # type: ignore[assignment]
    attacker = AmazonSESDriver(
        config=SESEmailConfig(region="us-east-1"),
        ses_client=attacker_ses,
        sesv2_client=attacker_sesv2,
        secrets_client=attacker_sm,
    )

    result = attacker.provision(
        _spec(organization_slug="villain", app_slug="evil-app", config=dict(shared_identity)),
    )

    assert result.ok is False
    assert result.handle == ""
    assert "refusing to adopt" in result.message


def test_provision_refuses_an_untagged_preexisting_identity(
    driver: AmazonSESDriver,
    sesv2_client,
) -> None:
    """An identity that predates ownership tagging -- or the platform's
    own base sending domain -- carries no astrolift.io tags at all;
    provision must not silently treat it as this service's own (#2029)."""
    sesv2_client.create_email_identity(EmailIdentity="legacy.astrolift.test")

    result = driver.provision(_spec(config={"identity": "legacy.astrolift.test"}))

    assert result.ok is False
    assert "refusing to adopt" in result.message


def test_provision_reentrant_by_managed_service_id_despite_app_slug_drift(
    driver: AmazonSESDriver,
) -> None:
    """The managed_service_id tag is authoritative (#1961 pattern): a
    reprovision with the same managed_service_id succeeds even though the
    app slug resolved differently in between (e.g. the app was renamed)."""
    shared_identity = {"identity": "shared.example.com"}
    first = driver.provision(_spec(managed_service_id="svc-123", config=dict(shared_identity)))
    second = driver.provision(
        _spec(managed_service_id="svc-123", app_slug="renamed-app", config=dict(shared_identity)),
    )
    assert first.ok and second.ok
    assert first.handle == second.handle == "email/shared.example.com"


def test_provision_refuses_when_managed_service_id_tag_mismatches(
    driver: AmazonSESDriver,
) -> None:
    """A managed_service_id tag mismatch refuses even under the same org
    + app: a deleted-and-recreated row must not silently inherit the old
    row's identity."""
    shared_identity = {"identity": "shared.example.com"}
    driver.provision(_spec(managed_service_id="svc-a", config=dict(shared_identity)))

    result = driver.provision(_spec(managed_service_id="svc-b", config=dict(shared_identity)))

    assert result.ok is False
    assert "refusing to adopt" in result.message


# ---- update -----------------------------------------------------


def test_update_deletion_protection_toggle(
    driver: AmazonSESDriver,
    sm_client,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={"deletion_protection": False},
        ),
    )
    assert result.ok
    _, identity = parse_handle(provisioned.handle)
    marker = f"astrolift/ses/{_safe(identity)}/protection"
    resp = sm_client.get_secret_value(SecretId=marker)
    assert resp["SecretString"] == "0"


def test_update_noop_when_nothing_to_change(
    driver: AmazonSESDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(UpdateSpec(handle=provisioned.handle))
    assert result.ok
    assert "no-op" in result.message


# ---- deprovision four-corner matrix -----------------------------


def test_deprovision_default_retains_identity_and_stats(
    driver: AmazonSESDriver,
    ses_client,
) -> None:
    provisioned = driver.provision(_spec())
    _, identity = parse_handle(provisioned.handle)
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
    )
    assert result.ok
    assert "stats=preserved" in result.message
    # Identity still present on retained-data path
    assert identity in ses_client.list_identities()["Identities"]


def test_deprovision_delete_data_with_protection_off_drops_identity(
    driver: AmazonSESDriver,
    ses_client,
) -> None:
    provisioned = driver.provision(
        _spec(config={"deletion_protection": False}),
    )
    _, identity = parse_handle(provisioned.handle)
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    assert result.ok
    assert "stats=discarded" in result.message
    assert identity not in ses_client.list_identities()["Identities"]


def test_deprovision_delete_data_refuses_with_protection_on(
    driver: AmazonSESDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    assert not result.ok
    assert "deletion_protection" in result.message


def test_deprovision_force_destroy_bypasses_protection(
    driver: AmazonSESDriver,
    ses_client,
) -> None:
    provisioned = driver.provision(_spec())
    _, identity = parse_handle(provisioned.handle)
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert "force_destroy=True" in result.message
    assert identity not in ses_client.list_identities()["Identities"]


def test_deprovision_force_destroy_keep_data_drops_marker_only(
    driver: AmazonSESDriver,
    sm_client,
    ses_client,
) -> None:
    provisioned = driver.provision(_spec())
    _, identity = parse_handle(provisioned.handle)
    marker = f"astrolift/ses/{_safe(identity)}/protection"
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=False,
        force_destroy=True,
    )
    assert result.ok
    assert "stats=preserved" in result.message
    # Identity retained
    assert identity in ses_client.list_identities()["Identities"]
    # Marker dropped so a follow-up delete_data=True succeeds without
    # needing force_destroy again
    with pytest.raises(sm_client.exceptions.ResourceNotFoundException):
        sm_client.get_secret_value(SecretId=marker)


def test_deprovision_idempotent_when_already_gone(
    driver: AmazonSESDriver,
) -> None:
    result = driver.deprovision(
        DeprovisionSpec(handle="email/does-not-exist.example.com"),
    )
    assert result.ok
    assert "already gone" in result.message


def test_deprovision_atomic_both_flags(
    driver: AmazonSESDriver,
    ses_client,
    sm_client,
) -> None:
    provisioned = driver.provision(_spec())
    _, identity = parse_handle(provisioned.handle)
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert identity not in ses_client.list_identities()["Identities"]
    # SMTP secrets purged
    user_name = f"astrolift/ses/{_safe(identity)}/smtp-access-key-id"
    with pytest.raises(sm_client.exceptions.ResourceNotFoundException):
        sm_client.get_secret_value(SecretId=user_name)


# ---- status -----------------------------------------------------


def test_status_for_missing_returns_deprovisioned(
    driver: AmazonSESDriver,
) -> None:
    state = driver.status(
        ServiceHandle(handle="email/missing.example.com"),
    )
    assert state.state == "deprovisioned"


def test_status_maps_pending_to_provisioning(
    driver: AmazonSESDriver,
) -> None:
    provisioned = driver.provision(_spec())
    # moto returns Pending after create_email_identity
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state in ("provisioning", "available")


# ---- binding ----------------------------------------------------


def test_binding_returns_connection_envelope(
    driver: AmazonSESDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle),
    )
    env = binding.env_vars
    for key in (
        "EMAIL_PROVIDER",
        "EMAIL_API_KEY",
        "EMAIL_FROM_ADDRESS",
        "EMAIL_REGION",
        "SES_FROM_ADDRESS",
        "SES_REGION",
        "SES_SMTP_ENDPOINT",
        "SES_SMTP_USER",
        "SES_SMTP_PASSWORD",
    ):
        assert key in env
    assert env["EMAIL_PROVIDER"].literal == "ses"
    assert env["EMAIL_API_KEY"].secret_ref is not None
    assert env["EMAIL_API_KEY"].literal is None
    assert env["SES_SMTP_PASSWORD"].secret_ref is not None
    assert env["SES_SMTP_USER"].secret_ref is not None
    assert env["EMAIL_REGION"].literal == "us-east-1"
    assert env["SES_SMTP_ENDPOINT"].literal == ("email-smtp.us-east-1.amazonaws.com")
    assert env["EMAIL_FROM_ADDRESS"].literal.startswith("noreply@")


def test_binding_iam_grants_cover_send_and_secrets(
    driver: AmazonSESDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle),
    )
    actions = {a for g in binding.iam_grants for a in g.actions}
    assert "ses:SendEmail" in actions
    assert "ses:SendRawEmail" in actions
    assert "secretsmanager:GetSecretValue" in actions


def test_binding_for_missing_raises(
    driver: AmazonSESDriver,
) -> None:
    with pytest.raises(ManagedServiceError):
        driver.binding(
            ServiceHandle(handle="email/missing.example.com"),
        )


# ---- binding: config-driven header envs (#637/#638/#639) --------


def test_binding_without_config_omits_optional_envs(
    driver: AmazonSESDriver,
) -> None:
    """Default ``config=None`` must not emit any of the optional
    header / per-env-sender env vars -- workloads rely on
    ``os.getenv(...)`` returning ``None`` for fallback semantics."""
    provisioned = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=provisioned.handle))
    for key in (
        "EMAIL_FROM_NAME",
        "EMAIL_REPLY_TO",
        "EMAIL_RETURN_PATH",
        "EMAIL_FROM_ADDRESS_PRODUCTION",
        "EMAIL_FROM_ADDRESS_PREVIEW",
    ):
        assert key not in binding.env_vars


def test_binding_emits_from_name_reply_to_return_path(
    driver: AmazonSESDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle),
        config={
            "from_name": "Acme Notifications",
            "reply_to": "support@acme.example",
            "return_path": "bounces@acme.example",
        },
    )
    env = binding.env_vars
    assert env["EMAIL_FROM_NAME"].literal == "Acme Notifications"
    assert env["EMAIL_REPLY_TO"].literal == "support@acme.example"
    assert env["EMAIL_RETURN_PATH"].literal == "bounces@acme.example"


def test_binding_emits_per_environment_senders(
    driver: AmazonSESDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle),
        config={
            "env_senders": {
                "production": "prod-bot@acme.example",
                "preview": "preview-bot@acme.example",
            },
        },
    )
    env = binding.env_vars
    assert env["EMAIL_FROM_ADDRESS_PRODUCTION"].literal == "prod-bot@acme.example"
    assert env["EMAIL_FROM_ADDRESS_PREVIEW"].literal == "preview-bot@acme.example"
    # identity-derived EMAIL_FROM_ADDRESS still emitted as the fallback
    assert env["EMAIL_FROM_ADDRESS"].literal.startswith("noreply@")


def test_binding_skips_blank_and_whitespace_only_values(
    driver: AmazonSESDriver,
) -> None:
    """Operators clearing a field through the UI typically send
    ``""`` or whitespace; those must not produce an empty env var
    that overrides a workload-side default."""
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle),
        config={
            "from_name": "   ",
            "reply_to": "",
            "return_path": None,
            "env_senders": {
                "production": "  ",
                "preview": "",
            },
        },
    )
    for key in (
        "EMAIL_FROM_NAME",
        "EMAIL_REPLY_TO",
        "EMAIL_RETURN_PATH",
        "EMAIL_FROM_ADDRESS_PRODUCTION",
        "EMAIL_FROM_ADDRESS_PREVIEW",
    ):
        assert key not in binding.env_vars


def test_binding_tolerates_non_dict_env_senders(
    driver: AmazonSESDriver,
) -> None:
    """Defensive: a malformed ``env_senders`` (list, string, None)
    must not crash the binding render -- the driver simply skips
    the per-environment emit path."""
    provisioned = driver.provision(_spec())
    for bad in (None, "production", ["production"], 42):
        binding = driver.binding(
            ServiceHandle(handle=provisioned.handle),
            config={"env_senders": bad},
        )
        assert "EMAIL_FROM_ADDRESS_PRODUCTION" not in binding.env_vars
        assert "EMAIL_FROM_ADDRESS_PREVIEW" not in binding.env_vars


def test_binding_partial_env_senders_emits_only_set_keys(
    driver: AmazonSESDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle),
        config={"env_senders": {"production": "prod@acme.example"}},
    )
    env = binding.env_vars
    assert env["EMAIL_FROM_ADDRESS_PRODUCTION"].literal == "prod@acme.example"
    assert "EMAIL_FROM_ADDRESS_PREVIEW" not in env


def test_binding_schema_documents_config_driven_envs(
    driver: AmazonSESDriver,
) -> None:
    schema = driver.binding_schema()
    for key in (
        "EMAIL_FROM_NAME",
        "EMAIL_REPLY_TO",
        "EMAIL_RETURN_PATH",
        "EMAIL_FROM_ADDRESS_PRODUCTION",
        "EMAIL_FROM_ADDRESS_PREVIEW",
    ):
        assert key in schema.env_vars


def test_config_schema_documents_new_keys(
    driver: AmazonSESDriver,
) -> None:
    schema = driver.config_schema()
    props = schema["properties"]
    for key in ("from_name", "reply_to", "return_path", "env_senders"):
        assert key in props
    env_senders_props = props["env_senders"]["properties"]
    assert "production" in env_senders_props
    assert "preview" in env_senders_props


# ---- snapshot + restore -----------------------------------------


def test_snapshot_returns_deterministic_id(
    driver: AmazonSESDriver,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    _, identity = parse_handle(provisioned.handle)
    assert snap.snapshot_id.startswith(_safe(identity))


def test_snapshot_for_missing_raises(
    driver: AmazonSESDriver,
) -> None:
    with pytest.raises(ManagedServiceError):
        driver.snapshot(
            ServiceHandle(handle="email/missing.example.com"),
        )


def test_restore_provisions_target_identity(
    driver: AmazonSESDriver,
    ses_client,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))

    restore_spec = _spec(service_handle_hint="restored")
    result = driver.restore(snap, restore_spec)
    assert result.ok
    _, target = parse_handle(result.handle)
    assert target in ses_client.list_identities()["Identities"]


# ---- naming + helpers -------------------------------------------


def test_identity_derivation_from_base_domain(
    driver: AmazonSESDriver,
) -> None:
    identity = driver._identity_for(  # type: ignore[attr-defined]
        spec=_spec(
            organization_slug="ACME!",
            app_slug="My API",
            environment_name="Prod",
        ),
    )
    assert identity.endswith(".astrolift.test")
    assert identity == identity.lower()
    assert "--" not in identity


def test_smtp_endpoint_for_region() -> None:
    assert _smtp_endpoint_for("us-west-2") == ("email-smtp.us-west-2.amazonaws.com")


def test_from_address_for_domain_prepends_noreply() -> None:
    assert _from_address_for(identity="example.com") == ("noreply@example.com")


def test_from_address_for_address_returns_verbatim() -> None:
    assert _from_address_for(identity="hello@example.com") == ("hello@example.com")


def test_safe_strips_unsafe_chars() -> None:
    # `!`/space/etc. -> `-`, consecutive hyphens collapse, lowercased.
    assert _safe("ACME!--My API") == "acme-my-api"
    # dots are preserved (allowed by SES + Secrets Manager paths)
    assert _safe("a.b") == "a.b"


def test_generated_smtp_password_has_entropy() -> None:
    pw = _generate_smtp_password(length=40)
    assert len(pw) == 40
    # All chars from the allowed alphabet
    for c in pw:
        assert c.isalnum() or c in "-_."


def test_default_client_construction_path() -> None:
    """When ses_client / secrets_client aren't injected the driver
    must still build successfully -- boto3 inits lazily, so this
    only needs to confirm no exception during __init__."""
    from moto import mock_aws

    with mock_aws():
        d = AmazonSESDriver(
            config=SESEmailConfig(
                region="us-east-1",
                base_domain="astrolift.test",
            ),
        )
        assert d._ses is not None  # type: ignore[attr-defined]
        assert d._sesv2 is not None  # type: ignore[attr-defined]
        assert d._sm is not None  # type: ignore[attr-defined]


# ---- config + binding schemas -----------------------------------


def test_config_schema_shape(driver: AmazonSESDriver) -> None:
    schema = driver.config_schema()
    assert schema["type"] == "object"
    props = schema["properties"]
    for key in ("identity", "deletion_protection"):
        assert key in props


def test_binding_schema_lists_all_env_vars(
    driver: AmazonSESDriver,
) -> None:
    schema = driver.binding_schema()
    for key in (
        "EMAIL_PROVIDER",
        "EMAIL_API_KEY",
        "EMAIL_FROM_ADDRESS",
        "EMAIL_REGION",
        "SES_FROM_ADDRESS",
        "SES_REGION",
        "SES_SMTP_ENDPOINT",
        "SES_SMTP_USER",
        "SES_SMTP_PASSWORD",
    ):
        assert key in schema.env_vars
