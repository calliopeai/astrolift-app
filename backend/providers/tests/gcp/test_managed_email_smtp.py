"""Tests for the vendor-neutral SMTP relay driver (#1453).

The driver provisions nothing, so the properties worth testing are not
"did the cloud call happen". They are the three things it actually decides:

* the binding is the portable email envelope, so a manifest survives a move
  between ``ses``, ``azure_acs`` and this one;
* a credential is never a literal, because the operator hands the driver a
  reference and the driver forwards it untouched;
* one relay serves every app on the install, so an app author cannot send as
  a domain the operator did not allow.
"""

from __future__ import annotations

from typing import Any

import pytest

from _sdk import UnsupportedOperationError
from _sdk.binding_policy import LITERAL, SECRET_REF, violation
from _sdk.managed_service import (
    UPDATE_NOT_SUPPORTED_IN_PLACE,
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    SnapshotHandle,
    UpdateSpec,
)
from gcp.managed.email_smtp import (
    KIND,
    VARIANT,
    SMTPRelayConfig,
    SMTPRelayEmailDriver,
    SMTPRelayError,
)

DRIVER_ID = f"gcp/{KIND}/{VARIANT}"

CONFIGURED = SMTPRelayConfig(
    host="smtp.relay.example",
    username_secret_ref="managed/email/acme/smtp-username",
    password_secret_ref="managed/email/acme/smtp-password",
    default_from_address="noreply@acme.example",
    port=587,
    tls_mode="starttls",
    region="eu-west",
)


@pytest.fixture
def driver() -> SMTPRelayEmailDriver:
    return SMTPRelayEmailDriver(config=CONFIGURED)


def _spec(**overrides: Any) -> ProvisionSpec:
    base: dict[str, Any] = {
        "organization_id": "1",
        "organization_slug": "acme",
        "app_id": "1",
        "app_slug": "api",
        "environment_id": "1",
        "environment_name": "prod",
        "tenant_cluster_id": "gcp-prod",
        "service_handle_hint": "mail",
        "size": "small",
    }
    base.update(overrides)
    return ProvisionSpec(**base)


def _handle(driver: SMTPRelayEmailDriver, **overrides: Any) -> ServiceHandle:
    result = driver.provision(_spec(**overrides))
    assert result.ok, result.message
    return ServiceHandle(handle=result.handle)


# ---- lifecycle ---------------------------------------------------------------


def test_provision_creates_nothing_and_needs_no_readiness_poll(driver: SMTPRelayEmailDriver) -> None:
    """``ready=False`` would park the provision workflow on a status poll for a
    resource that will never change state, because there is no resource."""
    result = driver.provision(_spec())

    assert result.ok
    assert result.ready is True
    assert result.handle == f"{KIND}/acme-api-prod-mail"


def test_provision_refuses_when_the_operator_has_not_configured_a_relay() -> None:
    """The failure has to reach the row as a result, not as an exception: an
    install that never set a relay must see an actionable message rather than a
    driver crash in a Temporal activity."""
    result = SMTPRelayEmailDriver().provision(_spec())

    assert result.ok is False
    assert result.errors == ["invalid_smtp_relay_config"]
    assert "smtp_host" not in result.message, "the message names driver fields, not provider_config keys"
    assert "host" in result.message


def test_provision_refuses_plaintext_smtp() -> None:
    """The binding carries a relay password; a plaintext session puts it on the
    wire. Refusing beats emitting a binding that leaks on first send."""
    relay = SMTPRelayEmailDriver(
        config=SMTPRelayConfig(
            host="smtp.relay.example",
            username_secret_ref="ref/u",
            password_secret_ref="ref/p",
            default_from_address="noreply@acme.example",
            tls_mode="none",
        ),
    )

    result = relay.provision(_spec())

    assert result.ok is False
    assert "tls_mode" in result.message


def test_deprovision_deletes_nothing_and_says_so(driver: SMTPRelayEmailDriver) -> None:
    """Even on the nuke corner. The relay is the operator's account with a third
    party; a teardown that claimed to have destroyed it would be a lie, and the
    campaign's teardown check reads this message."""
    result = driver.deprovision(
        DeprovisionSpec(handle=f"{KIND}/acme-api-prod-mail"),
        delete_data=True,
        force_destroy=True,
    )

    assert result.ok
    assert "operator-owned" in result.message
    assert "not touched" in result.message


def test_status_is_available_for_a_valid_handle_over_a_configured_relay(driver: SMTPRelayEmailDriver) -> None:
    status = driver.status(_handle(driver))

    assert status.state == "available"


def test_status_goes_error_when_the_operator_removes_the_relay() -> None:
    """A booked service whose relay config was deleted is broken, and the row
    must say so. Reporting ``available`` off a syntactically valid handle alone
    would hide it until the first bounce."""
    handle = ServiceHandle(handle=f"{KIND}/acme-api-prod-mail")

    status = SMTPRelayEmailDriver().status(handle)

    assert status.state == "error"
    assert "not configured" in status.message


def test_status_rejects_a_handle_from_another_kind(driver: SMTPRelayEmailDriver) -> None:
    status = driver.status(ServiceHandle(handle="postgres/acme-api-prod-db"))

    assert status.state == "error"


def test_update_refuses_rather_than_reporting_a_change_it_did_not_make(driver: SMTPRelayEmailDriver) -> None:
    """#1376: ``ok=True`` makes the platform copy the desired config onto
    ``applied_config`` and flip the row back to ACTIVE."""
    result = driver.update(UpdateSpec(handle=f"{KIND}/acme-api-prod-mail", config={"from_address": "x@acme.example"}))

    assert result.ok is False
    assert result.retryable is False
    assert result.errors == [UPDATE_NOT_SUPPORTED_IN_PLACE]
    assert driver.editable_fields() == []


@pytest.mark.parametrize("operation", ["snapshot", "restore"])
def test_snapshot_and_restore_are_unsupported(driver: SMTPRelayEmailDriver, operation: str) -> None:
    with pytest.raises(UnsupportedOperationError):
        if operation == "snapshot":
            driver.snapshot(ServiceHandle(handle=f"{KIND}/acme-api-prod-mail"))
        else:
            driver.restore(SnapshotHandle(handle="", snapshot_id="", created_at=""), _spec())


# ---- binding -----------------------------------------------------------------


def test_binding_emits_the_portable_email_envelope(driver: SMTPRelayEmailDriver) -> None:
    """The point of the variant: an app that reads EMAIL_* on SES or ACS reads
    the same four names here, so the manifest is what moves, not the code."""
    env = driver.binding(_handle(driver)).env_vars

    assert env["EMAIL_PROVIDER"].literal == "smtp"
    assert env["EMAIL_FROM_ADDRESS"].literal == "noreply@acme.example"
    assert env["EMAIL_REGION"].literal == "eu-west"
    assert env["EMAIL_API_KEY"].secret_ref == CONFIGURED.password_secret_ref


def test_binding_never_inlines_a_credential(driver: SMTPRelayEmailDriver) -> None:
    """``binding_policy``'s one hard rule, applied to this driver's own output:
    a literal credential lands in ``ManagedServiceBinding.env_value_ref``, a
    plaintext column, and cannot be unwritten."""
    env = driver.binding(_handle(driver)).env_vars

    offences = [
        violation(key, DRIVER_ID, SECRET_REF if value.secret_ref is not None else LITERAL) for key, value in env.items()
    ]

    assert [message for message in offences if message] == []


def test_binding_forwards_operator_references_rather_than_resolving_them(driver: SMTPRelayEmailDriver) -> None:
    """The driver is handed references and never the values, which is why the
    keys are ledgered in ``PASS_THROUGH_REFERENCES``."""
    env = driver.binding(_handle(driver)).env_vars

    assert env["SMTP_USERNAME"].secret_ref == CONFIGURED.username_secret_ref
    assert env["SMTP_PASSWORD"].secret_ref == CONFIGURED.password_secret_ref
    assert env["SMTP_USERNAME"].literal is None
    assert env["SMTP_PASSWORD"].literal is None


def test_binding_carries_the_relay_endpoint_for_a_client_that_dials_it(driver: SMTPRelayEmailDriver) -> None:
    env = driver.binding(_handle(driver)).env_vars

    assert env["SMTP_HOST"].literal == "smtp.relay.example"
    assert env["SMTP_PORT"].literal == "587"
    assert env["SMTP_TLS_MODE"].literal == "starttls"


def test_binding_grants_nothing_because_the_relay_is_not_a_cloud_resource(driver: SMTPRelayEmailDriver) -> None:
    """A grant here would be a role attached to nothing: the relay
    authenticates with a username and password, not a cloud identity."""
    assert driver.binding(_handle(driver)).iam_grants == []


# ---- the sender allowlist ----------------------------------------------------


def test_a_service_may_override_the_sender_inside_the_allowed_domains() -> None:
    relay = SMTPRelayEmailDriver(
        config=SMTPRelayConfig(
            host="smtp.relay.example",
            username_secret_ref="ref/u",
            password_secret_ref="ref/p",
            default_from_address="noreply@acme.example",
            allowed_sender_domains=("acme.example", "mail.acme.example"),
        ),
    )
    handle = _handle(relay)

    env = relay.binding(handle, {"from_address": "billing@mail.acme.example"}).env_vars

    assert env["EMAIL_FROM_ADDRESS"].literal == "billing@mail.acme.example"


def test_a_service_cannot_send_as_a_domain_the_operator_did_not_allow(driver: SMTPRelayEmailDriver) -> None:
    """One relay serves every app on the install, so an unchecked from_address
    lets any tenant send as anyone the relay is authorised for."""
    result = driver.provision(_spec(config={"from_address": "ceo@othercorp.example"}))

    assert result.ok is False
    assert "allowed sender domains" in result.message


def test_the_allowlist_defaults_to_the_operator_default_domain_only(driver: SMTPRelayEmailDriver) -> None:
    """An operator who never listed domains gets the narrow reading, not the
    open one. The fixture sets no ``allowed_sender_domains``."""
    assert driver.provision(_spec(config={"from_address": "support@acme.example"})).ok
    assert not driver.provision(_spec(config={"from_address": "support@acme.example.net"})).ok


def test_a_malformed_sender_is_refused_rather_than_normalised(driver: SMTPRelayEmailDriver) -> None:
    """The domain half is what the allowlist is checked against, so a value the
    parser cannot split must never reach the binding."""
    with pytest.raises(SMTPRelayError):
        driver.binding(_handle(driver), {"from_address": "not-an-address"})


def test_binding_refuses_a_disallowed_sender_even_when_provision_allowed_it(driver: SMTPRelayEmailDriver) -> None:
    """The check is re-run at bind time on purpose: the operator's allowlist can
    narrow after a service was booked, and the binding is what actually reaches
    the workload."""
    handle = _handle(driver)

    with pytest.raises(SMTPRelayError):
        driver.binding(handle, {"from_address": "ceo@othercorp.example"})
