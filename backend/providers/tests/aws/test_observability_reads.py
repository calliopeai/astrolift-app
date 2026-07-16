"""Tests for the new operator-facing read methods on the AWS drivers
(#377): ``Route53Driver.list_records_for_app``,
``ACMDriver.list_certificates``, ``IRSADriver.describe_identity``.

These cover happy path + empty + driver-side error paths for each new
method. moto is the AWS-side fake; the SDK protocol defaults
(NotImplementedError) are exercised separately in the SDK package
contract via the non-AWS plugins."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from _sdk.dns import DnsRecord
from _sdk.identity import IdentityBinding
from _sdk.tls import CertificateInfo
from aws._errors import ProviderError
from aws.dns_route53 import Route53Config, Route53Driver
from aws.identity_irsa import IRSAConfig, IRSADriver
from aws.tls_acm import ACMConfig, ACMDriver

# ---------------------------------------------------------------------------
# Route53Driver.list_records_for_app
# ---------------------------------------------------------------------------


@pytest.fixture
def route53_with_zone(route53_client) -> tuple[Route53Driver, str]:
    response = route53_client.create_hosted_zone(
        Name="acme.platform.example.",
        CallerReference="obs-test",
    )
    zone_id = response["HostedZone"]["Id"].rsplit("/", 1)[-1]
    driver = Route53Driver(
        config=Route53Config(),
        client=route53_client,
    )
    return driver, zone_id


def test_list_records_for_app_by_zone_returns_dnsrecord_shape(
    route53_with_zone,
    route53_client,
) -> None:
    """Happy path: passing a zone name returns DnsRecord items with the
    propagation field populated (even if "unknown")."""
    driver, _ = route53_with_zone
    driver.ensure_record(
        zone="acme.platform.example",
        name="api",
        type="A",
        value="192.0.2.1",
    )
    records = driver.list_records_for_app("acme.platform.example")

    assert all(isinstance(r, DnsRecord) for r in records)
    api = next((r for r in records if r.name.startswith("api")), None)
    assert api is not None
    assert api.type == "A"
    assert api.value == "192.0.2.1"
    assert api.propagation_status in {"propagated", "pending", "unknown"}


def test_list_records_for_app_empty_zone(route53_with_zone) -> None:
    """Zone with no records returns the NS / SOA defaults Route53
    auto-creates; the DnsRecord list shouldn't be empty unless the
    caller filters. We assert the call returns *some* shape rather
    than a synthetic empty so the empty-state UX has accurate data."""
    driver, _ = route53_with_zone
    records = driver.list_records_for_app("acme.platform.example")
    # Route53 always seeds NS + SOA records for a new zone.
    assert all(isinstance(r, DnsRecord) for r in records)
    assert any(r.type == "SOA" for r in records)


def test_list_records_for_app_unknown_input_raises(route53_client) -> None:
    """Bare driver, no matching zone or app-slug tag — raises
    NotFoundError, surfaces to the caller, FE turns it into the
    "not yet supported / not configured" empty state via the
    backend's translation layer."""
    from aws._errors import NotFoundError

    driver = Route53Driver(client=route53_client)
    with pytest.raises(NotFoundError):
        driver.list_records_for_app("never-existed-zone.example")


def test_list_records_for_app_falls_back_to_app_slug_tag(
    route53_with_zone,
    route53_client,
) -> None:
    """When the input doesn't match a zone name, the driver scans
    hosted zones for the ``astrolift.io/app-slug`` tag."""
    driver, zone_id = route53_with_zone
    route53_client.change_tags_for_resource(
        ResourceType="hostedzone",
        ResourceId=zone_id,
        AddTags=[{"Key": "astrolift.io/app-slug", "Value": "acme-api"}],
    )
    driver.ensure_record(
        zone="acme.platform.example",
        name="web",
        type="A",
        value="192.0.2.5",
    )
    records = driver.list_records_for_app("acme-api")
    assert any(r.value == "192.0.2.5" for r in records)


def test_list_records_for_app_propagates_client_errors(
    route53_with_zone,
) -> None:
    """A boto3 client error during the inner paginator surfaces as an
    ProviderError (via map_client_error). The resolver layer catches that
    and renders an empty card."""
    driver, _ = route53_with_zone

    # Force the paginator path to error out by replacing get_paginator.
    def _bad_paginator(name):
        raise RuntimeError("boom")

    driver._r53.get_paginator = _bad_paginator  # type: ignore[assignment]
    with pytest.raises((ProviderError, RuntimeError)):
        driver.list_records_for_app("acme.platform.example")


# ---------------------------------------------------------------------------
# ACMDriver.list_certificates
# ---------------------------------------------------------------------------


@pytest.fixture
def acm_driver(acm_client) -> ACMDriver:
    return ACMDriver(
        config=ACMConfig(region="us-east-1"),
        client=acm_client,
    )


def test_list_certificates_returns_certificateinfo_shape(
    acm_driver: ACMDriver,
) -> None:
    cert = acm_driver.ensure_certificate(domain="api.acme.platform.example")
    infos = acm_driver.list_certificates()
    assert any(isinstance(i, CertificateInfo) for i in infos)
    found = next((i for i in infos if i.id == cert.id), None)
    assert found is not None
    assert found.hostname == "api.acme.platform.example"
    # ACM-issued certs default to renewal_status="auto" (no
    # RenewalSummary yet, but Type=AMAZON_ISSUED).
    assert found.renewal_status in {"auto", "unknown"}
    assert found.days_until_expiry >= 0


def test_list_certificates_empty(acm_driver: ACMDriver) -> None:
    """Account with no ACM certs returns an empty list — the FE
    renders the empty state with the deep-link to "Issue cert"."""
    assert acm_driver.list_certificates() == []


def test_list_certificates_filter_hostname(acm_driver: ACMDriver) -> None:
    """Filter is substring match against domain + SANs so a wildcard
    cert lands in a query for the bare hostname."""
    acm_driver.ensure_certificate(
        domain="acme.platform.example",
        sans=["*.acme.platform.example"],
    )
    acm_driver.ensure_certificate(domain="other.unrelated.example")
    matches = acm_driver.list_certificates(filter_hostname="acme.platform")
    assert len(matches) == 1
    assert matches[0].hostname == "acme.platform.example"


def test_list_certificates_skips_deleted_between_list_and_describe(
    acm_driver: ACMDriver,
    acm_client,
) -> None:
    """Race: cert disappears between the list_certificates pagination
    and the per-id describe_certificate call. We swallow the
    ResourceNotFoundException so a transient delete doesn't break the
    whole operator-facing card."""
    cert = acm_driver.ensure_certificate(domain="api.acme.platform.example")
    # Replace describe_certificate to simulate a 'gone since list'.
    real_describe = acm_client.describe_certificate

    def _gone_describe(*, CertificateArn):  # noqa: N803
        if CertificateArn == cert.id:
            raise acm_client.exceptions.ResourceNotFoundException(
                {"Error": {"Code": "ResourceNotFoundException", "Message": "x"}},
                "DescribeCertificate",
            )
        return real_describe(CertificateArn=CertificateArn)

    acm_client.describe_certificate = _gone_describe  # type: ignore[assignment]
    out = acm_driver.list_certificates()
    assert all(i.id != cert.id for i in out)


def test_list_certificates_propagates_client_errors(
    acm_driver: ACMDriver,
) -> None:
    """A boto3 error on the initial pagination surfaces — distinct from
    the per-cert RNF skip (above), which is benign."""

    def _bad_paginator(name):
        raise RuntimeError("acm boom")

    acm_driver._acm.get_paginator = _bad_paginator  # type: ignore[assignment]
    with pytest.raises((ProviderError, RuntimeError)):
        acm_driver.list_certificates()


def test_list_certificates_days_until_expiry_clamps_negative(
    acm_driver: ACMDriver,
    acm_client,
) -> None:
    """An expired cert must report 0 days, not a negative number."""
    cert = acm_driver.ensure_certificate(domain="expired.example")
    # Patch describe_certificate to return a NotAfter in the past.
    past = datetime.now(UTC) - timedelta(days=10)

    real_describe = acm_client.describe_certificate

    def _expired_describe(*, CertificateArn):  # noqa: N803
        resp = real_describe(CertificateArn=CertificateArn)
        if CertificateArn == cert.id:
            resp["Certificate"]["NotAfter"] = past
        return resp

    acm_client.describe_certificate = _expired_describe  # type: ignore[assignment]
    infos = acm_driver.list_certificates()
    found = next((i for i in infos if i.id == cert.id), None)
    assert found is not None
    assert found.days_until_expiry == 0


def test_list_certificates_renewal_failed_status(
    acm_driver: ACMDriver,
    acm_client,
) -> None:
    """ACM RenewalSummary FAILED is mapped to ``renewal_status="failed"``."""
    cert = acm_driver.ensure_certificate(domain="failing.example")
    real_describe = acm_client.describe_certificate

    def _failing_describe(*, CertificateArn):  # noqa: N803
        resp = real_describe(CertificateArn=CertificateArn)
        if CertificateArn == cert.id:
            resp["Certificate"]["RenewalSummary"] = {
                "RenewalStatus": "FAILED",
                "DomainValidationOptions": [],
            }
        return resp

    acm_client.describe_certificate = _failing_describe  # type: ignore[assignment]
    infos = acm_driver.list_certificates()
    found = next((i for i in infos if i.id == cert.id), None)
    assert found is not None
    assert found.renewal_status == "failed"


def test_list_certificates_imported_is_manual(
    acm_driver: ACMDriver,
    acm_client,
) -> None:
    """Cert with Type=IMPORTED is renewal_status='manual' (operator
    brought their own — no auto renewal)."""
    cert = acm_driver.ensure_certificate(domain="byoc.example")
    real_describe = acm_client.describe_certificate

    def _imported_describe(*, CertificateArn):  # noqa: N803
        resp = real_describe(CertificateArn=CertificateArn)
        if CertificateArn == cert.id:
            resp["Certificate"]["Type"] = "IMPORTED"
            resp["Certificate"].pop("RenewalSummary", None)
        return resp

    acm_client.describe_certificate = _imported_describe  # type: ignore[assignment]
    infos = acm_driver.list_certificates()
    found = next((i for i in infos if i.id == cert.id), None)
    assert found is not None
    assert found.renewal_status == "manual"


# ---------------------------------------------------------------------------
# IRSADriver.describe_identity
# ---------------------------------------------------------------------------


@pytest.fixture
def irsa_driver(iam_client) -> IRSADriver:
    return IRSADriver(
        config=IRSAConfig(
            region="us-east-1",
            account_id="123456789012",
            cluster_oidc_issuer="oidc.eks.us-east-1.amazonaws.com/id/ABC123",
        ),
        iam_client=iam_client,
    )


def test_describe_identity_happy_path(irsa_driver: IRSADriver) -> None:
    """Role exists + has a single SA bound — returns IdentityBinding
    with a one-subject summary."""
    irsa_driver.create_identity_role(name="astrolift-acme-api", permissions=[])
    irsa_driver.bind_service_account(
        cluster="x",
        namespace="acme",
        sa_name="api",
        identity_role="astrolift-acme-api",
    )
    binding = irsa_driver.describe_identity("acme-api")
    assert isinstance(binding, IdentityBinding)
    assert binding.kind == "irsa"
    assert "astrolift-acme-api" in binding.role_arn_or_principal
    assert "system:serviceaccount:acme:api" in binding.trust_policy_summary


def test_describe_identity_missing_role_returns_none(
    irsa_driver: IRSADriver,
) -> None:
    """No role provisioned for the app yet — returns None so the FE
    renders the "Configure WI" empty state."""
    assert irsa_driver.describe_identity("never-provisioned") is None


def test_describe_identity_multiple_subjects_summary(
    irsa_driver: IRSADriver,
) -> None:
    """A role bound to 2+ SAs reports a count rather than a single
    subject — the card stays one line."""
    irsa_driver.create_identity_role(name="astrolift-shared", permissions=[])
    irsa_driver.bind_service_account(
        cluster="x",
        namespace="acme",
        sa_name="api",
        identity_role="astrolift-shared",
    )
    irsa_driver.bind_service_account(
        cluster="x",
        namespace="acme",
        sa_name="worker",
        identity_role="astrolift-shared",
    )
    binding = irsa_driver.describe_identity("shared")
    assert binding is not None
    assert "service account(s)" in binding.trust_policy_summary
    assert "2" in binding.trust_policy_summary


def test_describe_identity_propagates_client_errors(
    irsa_driver: IRSADriver,
) -> None:
    """An unexpected boto3 error (not NoSuchEntity) surfaces — the
    resolver layer catches ProviderError and degrades the card."""

    def _explode(**_kwargs: Any) -> None:
        raise RuntimeError("iam api boom")

    irsa_driver._iam.get_role = _explode  # type: ignore[assignment]
    with pytest.raises((ProviderError, RuntimeError)):
        irsa_driver.describe_identity("acme-api")


def test_describe_identity_no_last_used_yet(
    irsa_driver: IRSADriver,
    iam_client,
) -> None:
    """Freshly-created role has no RoleLastUsed — last_used_at is
    None, the UI renders that as '—' rather than now()."""
    irsa_driver.create_identity_role(name="astrolift-fresh", permissions=[])
    binding = irsa_driver.describe_identity("fresh")
    assert binding is not None
    assert binding.last_used_at is None


def test_describe_identity_with_last_used(
    irsa_driver: IRSADriver,
    iam_client,
) -> None:
    """When IAM populates RoleLastUsed, we surface the ISO timestamp."""
    irsa_driver.create_identity_role(name="astrolift-used", permissions=[])
    when = datetime.now(UTC) - timedelta(hours=2)
    real_get_role = iam_client.get_role

    def _patched_get_role(*, RoleName):  # noqa: N803
        resp = real_get_role(RoleName=RoleName)
        resp["Role"]["RoleLastUsed"] = {"LastUsedDate": when, "Region": "us-east-1"}
        return resp

    iam_client.get_role = _patched_get_role  # type: ignore[assignment]
    binding = irsa_driver.describe_identity("used")
    assert binding is not None
    assert binding.last_used_at is not None
    # ISO-8601 timestamp shape — we're not strict on tz here.
    assert "T" in binding.last_used_at


# ---------------------------------------------------------------------------
# Trust-policy summary helper
# ---------------------------------------------------------------------------


def test_summarize_trust_empty() -> None:
    from aws.identity_irsa import _summarize_trust

    assert "empty" in _summarize_trust(
        {},
        "oidc.eks.us-east-1.amazonaws.com/id/X",
    )


def test_summarize_trust_single_subject() -> None:
    from aws.identity_irsa import _summarize_trust

    doc = {
        "Statement": [
            {
                "Effect": "Allow",
                "Condition": {
                    "StringEquals": {
                        "oidc.eks.us-east-1.amazonaws.com/id/X:sub": "system:serviceaccount:ns:sa",
                    },
                },
            }
        ],
    }
    summary = _summarize_trust(doc, "oidc.eks.us-east-1.amazonaws.com/id/X")
    assert summary == "OIDC trust: system:serviceaccount:ns:sa"


def test_summarize_trust_no_oidc_subject_falls_back() -> None:
    from aws.identity_irsa import _summarize_trust

    doc = {"Statement": [{"Effect": "Allow", "Action": "sts:AssumeRole"}]}
    summary = _summarize_trust(doc, "oidc.eks.us-east-1.amazonaws.com/id/X")
    assert "1 statement" in summary


# ---------------------------------------------------------------------------
# Module-level shape: imports succeed, dataclasses are frozen
# ---------------------------------------------------------------------------


def test_dataclasses_are_frozen() -> None:
    """Operator-facing DTOs are frozen so a resolver can't mutate the
    backing instance and confuse a downstream consumer."""
    rec = DnsRecord(name="x", type="A", value="1.2.3.4", ttl=60)
    info = CertificateInfo(
        id="i",
        hostname="h",
        issuer="i",
        not_after="2030",
        days_until_expiry=1,
    )
    binding = IdentityBinding(
        kind="irsa",
        role_arn_or_principal="arn:x",
        trust_policy_summary="x",
    )
    for obj, attr in (
        (rec, "name"),
        (info, "hostname"),
        (binding, "kind"),
    ):
        with pytest.raises((AttributeError, TypeError)):
            setattr(obj, attr, "mutated")


# Used in test_summarize_trust_single_subject so json import is in fact used.
_ = json  # silence unused-import lint when only the helper imports use it
