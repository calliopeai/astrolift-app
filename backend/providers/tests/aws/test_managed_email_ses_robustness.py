"""Robustness tests for the AWS SES managed-service driver (#1038).

The astrolift-local container has no moto, so these exercise the driver
against stateful recording fakes for the SES + Secrets Manager + Route53
clients. They guard the #1038 fix: a freshly-requested-but-unverified SES
domain identity is a normal state that must NOT hard-fail the provision,
and the load-bearing identity-verification step succeeds even when the
ancillary best-effort steps (SMTP placeholder secrets, Route53 publish)
fail.
"""

from __future__ import annotations

from typing import Any

from _sdk.managed_service import ProvisionSpec
from aws.managed._base import parse_handle
from aws.managed.email_ses import (
    KIND,
    AmazonSESDriver,
    SESEmailConfig,
)

# ---- recording fakes -------------------------------------------------


class _AlreadyExistsException(Exception):  # noqa: N818 -- mimics SDK class name
    pass


class FakeSes:
    """Stateful recording fake for the SES v1 control plane surface the
    driver touches. PascalCase kwargs match the real boto3 client so the
    fake is interchangeable."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self._identities: dict[str, dict[str, Any]] = {}
        self._csets: set[str] = set()
        self._dkim: dict[str, list[str]] = {}

    def verify_domain_identity(self, *, Domain: str, **_: Any) -> dict:  # noqa: N803
        self.calls.append(("verify_domain_identity", Domain))
        self._identities[Domain] = {
            "VerificationStatus": "Pending",
            "VerificationToken": f"verifytoken-{Domain}",
        }
        return {"VerificationToken": f"verifytoken-{Domain}"}

    def verify_email_identity(self, *, EmailAddress: str, **_: Any) -> dict:  # noqa: N803
        self.calls.append(("verify_email_identity", EmailAddress))
        self._identities[EmailAddress] = {"VerificationStatus": "Pending"}
        return {}

    def verify_domain_dkim(self, *, Domain: str, **_: Any) -> dict:  # noqa: N803
        self.calls.append(("verify_domain_dkim", Domain))
        tokens = [f"dkimtok{n}{Domain[:3]}" for n in range(3)]
        self._dkim[Domain] = tokens
        return {"DkimTokens": tokens}

    def get_identity_verification_attributes(
        self, *, Identities: list[str], **_: Any,  # noqa: N803
    ) -> dict:
        attrs = {
            i: self._identities[i]
            for i in Identities
            if i in self._identities
        }
        return {"VerificationAttributes": attrs}

    def create_configuration_set(self, *, ConfigurationSet: dict, **_: Any) -> dict:  # noqa: N803
        name = ConfigurationSet["Name"]
        if name in self._csets:
            raise _AlreadyExistsException("ConfigurationSetAlreadyExists")
        self._csets.add(name)
        return {}


class FakeSecretsManager:
    """Recording fake for the bits of Secrets Manager the driver uses.

    ``deny_create`` simulates the platform role lacking
    ``secretsmanager:CreateSecret`` on the SES secret prefix -- the
    failure mode that hard-failed provision before #1038."""

    class _ResourceExistsException(Exception):  # noqa: N818 -- mimics SDK
        pass

    def __init__(self, *, deny_create: bool = False) -> None:
        self.calls: list[tuple] = []
        self._store: dict[str, str] = {}
        self._deny_create = deny_create

    def create_secret(self, *, Name: str, SecretString: str, **_: Any) -> dict:  # noqa: N803
        self.calls.append(("create_secret", Name))
        if self._deny_create:
            raise RuntimeError("AccessDeniedException: not authorized")
        if Name in self._store:
            raise self._ResourceExistsException("ResourceExistsException")
        self._store[Name] = SecretString
        return {}

    def put_secret_value(self, *, SecretId: str, SecretString: str, **_: Any) -> dict:  # noqa: N803
        self._store[SecretId] = SecretString
        return {}


class FakeRoute53Driver:
    """Recording stand-in for ``Route53Driver`` -- records ensure_record
    calls so tests can assert the exact verification records written."""

    def __init__(self, *, raise_on_ensure: bool = False) -> None:
        self.records: list[tuple[str, str, str, str]] = []
        self._raise = raise_on_ensure

    def ensure_record(
        self, *, zone: str, name: str, type: str, value: str, ttl: int = 300,
    ) -> None:
        if self._raise:
            raise RuntimeError("AccessDenied: route53:ChangeResourceRecordSets")
        self.records.append((zone, name, type, value))


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


def _driver(
    *,
    ses: FakeSes | None = None,
    sm: FakeSecretsManager | None = None,
    route53: FakeRoute53Driver | None = None,
    base_domain: str = "astrolift.test",
) -> AmazonSESDriver:
    return AmazonSESDriver(
        config=SESEmailConfig(region="us-east-1", base_domain=base_domain),
        ses_client=ses or FakeSes(),
        secrets_client=sm or FakeSecretsManager(),
        route53=route53 or FakeRoute53Driver(),
    )


# ---- the load-bearing #1038 guarantee --------------------------------


def test_provision_succeeds_for_fresh_pending_domain() -> None:
    """A brand-new domain identity is Pending after verify -- provision
    must return ok=True (not failed) and register the identity."""
    ses = FakeSes()
    d = _driver(ses=ses)
    result = d.provision(_spec())
    assert result.ok is True
    kind, identity = parse_handle(result.handle)
    assert kind == KIND
    assert identity in ses._identities
    assert ses._identities[identity]["VerificationStatus"] == "Pending"


def test_provision_does_not_hard_fail_when_secret_write_denied() -> None:
    """The pre-#1038 crash: create_secret raising (role lacks
    secretsmanager:CreateSecret) bubbled out of provision as an
    unhandled exception -> 'Activity task failed'. Now it's a soft
    warning and the identity still provisions."""
    sm = FakeSecretsManager(deny_create=True)
    d = _driver(sm=sm)
    result = d.provision(_spec())
    assert result.ok is True
    assert "smtp placeholder secrets not stored" in result.message


def test_provision_publishes_dkim_and_verification_dns() -> None:
    """For a domain under the operator's base_domain, provision publishes
    the _amazonses TXT + three Easy-DKIM CNAMEs into Route53 so the
    identity converges to Verified with no manual DNS step."""
    ses = FakeSes()
    r53 = FakeRoute53Driver()
    d = _driver(ses=ses, route53=r53)
    result = d.provision(_spec())
    assert result.ok is True
    _, identity = parse_handle(result.handle)

    txt = [r for r in r53.records if r[2] == "TXT"]
    cnames = [r for r in r53.records if r[2] == "CNAME"]
    assert len(txt) == 1
    assert txt[0][0] == "astrolift.test"  # zone
    assert txt[0][1] == f"_amazonses.{identity}"
    assert txt[0][3] == f'"verifytoken-{identity}"'

    assert len(cnames) == 3
    for zone, name, _type, value in cnames:
        assert zone == "astrolift.test"
        assert name.endswith(f"._domainkey.{identity}")
        assert value.endswith(".dkim.amazonses.com")
    assert "dns records published" in result.message


def test_provision_succeeds_when_route53_publish_denied() -> None:
    """Route53 access failure must not fail provision -- the identity
    stays pending and the operator publishes DNS manually."""
    r53 = FakeRoute53Driver(raise_on_ensure=True)
    d = _driver(route53=r53)
    result = d.provision(_spec())
    assert result.ok is True
    assert "pending dns verification" in result.message


def test_provision_skips_dns_when_no_base_domain_but_explicit_identity() -> None:
    """With no base_domain the driver only provisions via an explicit
    identity; no DNS is published (we don't own the zone) but provision
    still succeeds."""
    r53 = FakeRoute53Driver()
    d = _driver(route53=r53, base_domain="")
    result = d.provision(_spec(config={"identity": "mail.customer.example"}))
    assert result.ok is True
    assert r53.records == []


def test_provision_skips_dns_for_email_identity() -> None:
    """A single-address (email) identity has no DKIM/domain DNS to
    publish."""
    ses = FakeSes()
    r53 = FakeRoute53Driver()
    d = _driver(ses=ses, route53=r53)
    result = d.provision(_spec(config={"identity": "ops@example.com"}))
    assert result.ok is True
    assert r53.records == []
    assert ("verify_domain_dkim", "ops@example.com") not in ses.calls


def test_provision_skips_dns_for_identity_outside_base_domain() -> None:
    """An explicit identity that isn't under base_domain must not have
    records written into the base_domain zone."""
    r53 = FakeRoute53Driver()
    d = _driver(route53=r53)
    result = d.provision(_spec(config={"identity": "other.example.org"}))
    assert result.ok is True
    assert r53.records == []


def test_reentrant_provision_republishes_dns_for_pending_identity() -> None:
    """Re-running provision on an already-registered (still pending)
    identity re-publishes the verification DNS so a previously stuck
    partial provision can self-heal."""
    ses = FakeSes()
    r53 = FakeRoute53Driver()
    d = _driver(ses=ses, route53=r53)
    first = d.provision(_spec())
    _, identity = parse_handle(first.handle)
    r53.records.clear()

    second = d.provision(_spec())
    assert second.ok is True
    assert "already registered" in second.message
    assert any(name == f"_amazonses.{identity}" for _, name, _t, _v in r53.records)


def test_verify_failure_still_reported_as_not_ok() -> None:
    """The load-bearing verify step failing is still a real failure --
    robustness only applies to the ancillary best-effort steps."""
    ses = FakeSes()

    def boom(**_kwargs):
        raise RuntimeError("simulated SES outage")

    ses.verify_domain_identity = boom  # type: ignore[assignment]
    d = _driver(ses=ses)
    result = d.provision(_spec())
    assert result.ok is False
    assert "verify_identity" in result.message
