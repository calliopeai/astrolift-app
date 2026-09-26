"""Per-service config threading for the SES driver (#1038).

``provisionManagedService(config={base_domain: ...})`` threads the
operator-supplied base_domain into ``spec.config``; the SES driver must
honour it as an override of the install-level base_domain, and fold its
``from_name`` / ``reply_to`` / ``env_senders`` keys into the binding.

These tests inject lightweight recording fakes (no moto) so they run in
any environment.
"""

from __future__ import annotations

from typing import Any

import pytest

from _sdk.managed_service import ProvisionSpec, ServiceHandle
from aws.managed._base import ManagedServiceError
from aws.managed.email_ses import AmazonSESDriver, SESEmailConfig


class _NotFoundException(Exception):
    def __init__(self) -> None:
        super().__init__("NotFoundException")
        self.response = {"Error": {"Code": "NotFoundException"}}


class _RecordingSES:
    """Minimal SES stand-in covering only the calls ``binding`` makes."""

    def __init__(self, *, verified: dict[str, str] | None = None) -> None:
        self._verified = verified or {}

    def get_identity_verification_attributes(
        self,
        *,
        Identities: list[str],  # noqa: N803  (boto3 API kwarg)
    ) -> dict[str, Any]:
        return {
            "VerificationAttributes": {
                ident: {"VerificationStatus": self._verified[ident]} for ident in Identities if ident in self._verified
            },
        }


class _RecordingSESv2:
    """Minimal SESv2 stand-in covering only the calls ``binding`` makes."""

    def __init__(self, *, verified: dict[str, str] | None = None) -> None:
        self._verified = verified or {}

    def get_email_identity(self, *, EmailIdentity: str) -> dict[str, Any]:  # noqa: N803
        if EmailIdentity not in self._verified:
            raise _NotFoundException()
        return {"VerifiedForSendingStatus": self._verified[EmailIdentity] == "Success"}


def _driver(*, base_domain: str = "", verified: dict[str, str] | None = None) -> AmazonSESDriver:
    return AmazonSESDriver(
        config=SESEmailConfig(region="us-east-1", base_domain=base_domain),
        ses_client=_RecordingSES(verified=verified),
        sesv2_client=_RecordingSESv2(verified=verified),
        secrets_client=object(),
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


def test_per_service_base_domain_overrides_missing_install_default() -> None:
    """No install base_domain, caller supplies it via config -> derives a
    domain instead of raising. Falsifiable: before the fix, _identity_for
    read only self._config.base_domain and raised here."""
    driver = _driver(base_domain="")
    identity = driver._identity_for(  # type: ignore[attr-defined]
        spec=_spec(config={"base_domain": "tenant.example"}),
    )
    assert identity.endswith(".tenant.example")
    assert identity == identity.lower()


def test_per_service_base_domain_wins_over_install_default() -> None:
    driver = _driver(base_domain="install.example")
    identity = driver._identity_for(  # type: ignore[attr-defined]
        spec=_spec(config={"base_domain": "tenant.example"}),
    )
    assert identity.endswith(".tenant.example")
    assert "install.example" not in identity


def test_install_base_domain_still_used_when_config_absent() -> None:
    driver = _driver(base_domain="install.example")
    identity = driver._identity_for(  # type: ignore[attr-defined]
        spec=_spec(),
    )
    assert identity.endswith(".install.example")


def test_still_raises_when_neither_install_nor_config_supplies_base_domain() -> None:
    driver = _driver(base_domain="")
    with pytest.raises(ManagedServiceError) as exc:
        driver._identity_for(spec=_spec())  # type: ignore[attr-defined]
    assert "base_domain" in str(exc.value)


def test_binding_folds_config_driven_sender_fields() -> None:
    """SES binding reads from_name / reply_to / env_senders from the
    threaded config. Falsifiable: drop the config arg and these env_vars
    vanish."""
    identity = "astrolift-acme-api-prod.tenant.example"
    driver = _driver(verified={identity: "Success"})
    binding = driver.binding(
        ServiceHandle(handle=f"email/{identity}"),
        config={
            "from_name": "Acme Notifications",
            "reply_to": "support@tenant.example",
            "env_senders": {"production": "noreply@tenant.example"},
        },
    )
    assert binding.env_vars["EMAIL_FROM_NAME"].literal == "Acme Notifications"
    assert binding.env_vars["EMAIL_REPLY_TO"].literal == "support@tenant.example"


def test_binding_omits_sender_fields_when_config_empty() -> None:
    identity = "astrolift-acme-api-prod.tenant.example"
    driver = _driver(verified={identity: "Success"})
    binding = driver.binding(ServiceHandle(handle=f"email/{identity}"))
    assert "EMAIL_FROM_NAME" not in binding.env_vars
    assert "EMAIL_REPLY_TO" not in binding.env_vars
