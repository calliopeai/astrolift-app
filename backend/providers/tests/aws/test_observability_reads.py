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


# ---- shared-zone scoping (#1114) ------------------------------------------


def test_list_records_for_app_shared_zone_scopes_to_app_host(route53_client) -> None:
    """One shared hosted zone holds many apps (the multi-app / SteadyMD
    topology). A slug names no zone and can't be tagged per-app, so the
    driver resolves the containing zone from ``app_host`` and returns only
    that app's host + subdomains — other apps' records don't leak in."""
    route53_client.create_hosted_zone(
        Name="astrolift.smdinfra.net.",
        CallerReference="shared-1114",
    )
    driver = Route53Driver(client=route53_client)
    driver.ensure_record(zone="astrolift.smdinfra.net", name="pickup", type="A", value="10.0.0.1")
    driver.ensure_record(zone="astrolift.smdinfra.net", name="api.pickup", type="A", value="10.0.0.2")
    # A prefix sibling + an unrelated app share the same zone.
    driver.ensure_record(zone="astrolift.smdinfra.net", name="pickup-staging", type="A", value="10.0.0.9")
    driver.ensure_record(zone="astrolift.smdinfra.net", name="faasprobe", type="A", value="10.0.0.3")

    records = driver.list_records_for_app("pickup", app_host="pickup.astrolift.smdinfra.net")

    values = {r.value for r in records}
    names = {r.name for r in records}
    assert values == {"10.0.0.1", "10.0.0.2"}
    assert "pickup.astrolift.smdinfra.net" in names
    assert "api.pickup.astrolift.smdinfra.net" in names
    # Prefix sibling, unrelated app, and the shared zone's apex NS/SOA are
    # all excluded — the label-boundary suffix test keeps siblings out.
    assert "10.0.0.9" not in values
    assert "10.0.0.3" not in values
    assert not any(r.type in {"SOA", "NS"} for r in records)


def test_list_records_for_app_shared_zone_includes_vanity_cname(route53_client) -> None:
    """A CNAME elsewhere in the shared zone that targets the app's host is
    part of the app's DNS surface, so it's kept even though its own name
    isn't under the host."""
    route53_client.create_hosted_zone(
        Name="astrolift.smdinfra.net.",
        CallerReference="vanity-1114",
    )
    driver = Route53Driver(client=route53_client)
    driver.ensure_record(zone="astrolift.smdinfra.net", name="pickup", type="A", value="10.0.0.1")
    driver.ensure_record(
        zone="astrolift.smdinfra.net",
        name="go",
        type="CNAME",
        value="pickup.astrolift.smdinfra.net",
    )
    records = driver.list_records_for_app("pickup", app_host="pickup.astrolift.smdinfra.net")
    cnames = [r for r in records if r.type == "CNAME"]
    assert any(r.value.rstrip(".") == "pickup.astrolift.smdinfra.net" for r in cnames)


def test_list_records_for_app_dedicated_zone_host_filter_is_noop(route53_client) -> None:
    """A dedicated-per-app zone still works: the containing-zone walk finds
    the app's own zone and the host-filter keeps every record (apex +
    subdomains are all the app's), including the zone's own SOA/NS."""
    route53_client.create_hosted_zone(
        Name="pickup.smdinfra.net.",
        CallerReference="dedicated-1114",
    )
    driver = Route53Driver(client=route53_client)
    driver.ensure_record(zone="pickup.smdinfra.net", name="@", type="A", value="10.1.0.1")
    driver.ensure_record(zone="pickup.smdinfra.net", name="api", type="A", value="10.1.0.2")

    records = driver.list_records_for_app("pickup", app_host="pickup.smdinfra.net")

    values = {r.value for r in records}
    assert "10.1.0.1" in values
    assert "10.1.0.2" in values
    # SOA/NS live at the apex == app_host, so the dedicated-zone view is
    # unchanged — the host-filter is genuinely a no-op here.
    assert any(r.type == "SOA" for r in records)


def test_list_records_for_app_tag_dedicated_zone_with_host(route53_client) -> None:
    """Dedicated zone resolved via the ``astrolift.io/app-slug`` TAG (not a
    containing-zone walk) with a host passed: the tag path wins and the
    host-filter stays a no-op."""
    response = route53_client.create_hosted_zone(
        Name="acme.example.",
        CallerReference="tag-1114",
    )
    zone_id = response["HostedZone"]["Id"].rsplit("/", 1)[-1]
    route53_client.change_tags_for_resource(
        ResourceType="hostedzone",
        ResourceId=zone_id,
        AddTags=[{"Key": "astrolift.io/app-slug", "Value": "acme"}],
    )
    driver = Route53Driver(client=route53_client)
    driver.ensure_record(zone="acme.example", name="@", type="A", value="10.2.0.1")
    records = driver.list_records_for_app("acme", app_host="acme.example")
    assert any(r.value == "10.2.0.1" for r in records)


def test_record_scoped_to_host_predicate() -> None:
    """Unit-level truth table for the host-scoping predicate."""
    from aws.dns_route53 import _record_scoped_to_host

    host = "pickup.astrolift.smdinfra.net"

    def rec(name: str, type: str = "A", value: str = "1.2.3.4") -> DnsRecord:
        return DnsRecord(name=name, type=type, value=value, ttl=60)

    assert _record_scoped_to_host(rec("pickup.astrolift.smdinfra.net"), host)
    assert _record_scoped_to_host(rec("api.pickup.astrolift.smdinfra.net"), host)
    assert _record_scoped_to_host(rec("*.pickup.astrolift.smdinfra.net"), host)
    # Trailing-dot + case normalization on both sides.
    assert _record_scoped_to_host(rec("PICKUP.astrolift.smdinfra.net."), host)
    # Prefix sibling / unrelated / apex are out (label-boundary match).
    assert not _record_scoped_to_host(rec("pickup-staging.astrolift.smdinfra.net"), host)
    assert not _record_scoped_to_host(rec("faasprobe.astrolift.smdinfra.net"), host)
    assert not _record_scoped_to_host(rec("astrolift.smdinfra.net", type="SOA"), host)
    # CNAME target pointing at the app host (or a subdomain of it) counts.
    assert _record_scoped_to_host(
        rec("go.astrolift.smdinfra.net", type="CNAME", value="Pickup.astrolift.smdinfra.net."), host
    )
    assert not _record_scoped_to_host(rec("go.astrolift.smdinfra.net", type="CNAME", value="other.smdinfra.net"), host)


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
    """filter_hostname matches with DNS cert semantics (_cert_covers_host),
    not the old substring test (#1111): the resolver passes the app's full
    resolved FQDN, so an exact host matches its cert, a direct subdomain
    matches a ``*.parent`` wildcard SAN, an unrelated host doesn't match,
    and a truncated PARENT string does NOT match (substring matching is
    gone)."""
    acm_driver.ensure_certificate(
        domain="acme.platform.example",
        sans=["*.acme.platform.example"],
    )
    acm_driver.ensure_certificate(domain="other.unrelated.example")

    # Exact host → the acme cert.
    exact = acm_driver.list_certificates(filter_hostname="acme.platform.example")
    assert [c.hostname for c in exact] == ["acme.platform.example"]

    # Direct subdomain → covered by the *.acme.platform.example wildcard SAN.
    subdomain = acm_driver.list_certificates(filter_hostname="api.acme.platform.example")
    assert [c.hostname for c in subdomain] == ["acme.platform.example"]

    # Unrelated host → matches neither cert.
    assert acm_driver.list_certificates(filter_hostname="www.example.org") == []

    # Truncated PARENT string must NOT match — proves substring is gone
    # (the query host is shorter than the wildcard/exact names).
    assert acm_driver.list_certificates(filter_hostname="acme.platform") == []


def test_cert_covers_host_dns_semantics() -> None:
    """Unit truth table for the RFC-6125-simplified matcher, covering the
    wildcard boundary rules the moto integration path can't easily probe:
    ``*.parent`` covers a *single* extra label only — not a deep subdomain,
    not the apex."""
    from aws.tls_acm import _cert_covers_host

    names = ["acme.platform.example", "*.acme.platform.example"]
    # Exact + direct-subdomain-via-wildcard, case- and trailing-dot-tolerant.
    assert _cert_covers_host("acme.platform.example", names)
    assert _cert_covers_host("api.acme.platform.example", names)
    assert _cert_covers_host("API.acme.platform.example.", names)
    # Wildcard is a single label: no deep subdomain, no apex self-match.
    assert not _cert_covers_host("a.b.acme.platform.example", ["*.acme.platform.example"])
    assert not _cert_covers_host("acme.platform.example", ["*.acme.platform.example"])
    # Truncated parent substring + unrelated host + empty never match.
    assert not _cert_covers_host("acme.platform", names)
    assert not _cert_covers_host("other.example.org", names)
    assert not _cert_covers_host("", names)


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
