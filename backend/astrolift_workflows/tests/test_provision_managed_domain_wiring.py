"""Tests for the policy the managed-domain provisioning flow now consults.

Two things the flow used to skip:

* it requested the wildcard cert with the DNS driver's ``[<zone>, *.<zone>]``
  default. ``*.<zone>`` matches exactly one label deeper (RFC 6125) while
  preview hostnames are two (``pr-<n>-<app>.pr.<zone>``), so the preview zone
  was never on the cert. The SAN list now comes from
  ``astrolift_clusters.tls_policy.wildcard_sans_for_org``.
* it marked a zone active without ever comparing the zone's public NS records
  against the nameservers the platform handed the operator, so a zone whose
  registrar still pointed at their previous DNS provider went active and apps
  were provisioned at hostnames that never resolved.
  ``astrolift_clusters.dns_layout.evaluate_ns_delegation`` is now the gate in
  front of activation.

Real Postgres for the activity cores; the DNS driver and the NS lookup are
recording fakes (no AWS, no live DNS). The workflow test fakes
``execute_activity`` so the step order is asserted without Temporal.
"""

from __future__ import annotations

import asyncio

import pytest

from astrolift_workflows.activities import provision_managed_domain

_ZONE = "apps.example.com"
_PLATFORM_NS = ["ns-1.awsdns-01.com", "ns-2.awsdns-02.net"]


class _FakeDns:
    """Records the cert calls; the driver's cloud side is not under test."""

    def __init__(self) -> None:
        self.cert_calls: list[dict] = []
        self.revoked: list[str] = []

    def request_wildcard_cert(self, zone, zone_id, sans=None):
        self.cert_calls.append({"zone": zone, "zone_id": zone_id, "sans": sans})
        return {"cert_id": "cert-1", "validation_records": []}

    def ensure_record(self, **kwargs):
        return None

    def provision_zone(self, zone):
        raise AssertionError("an existing managed zone must be reused")

    def revoke_cert(self, zone, cert_id):
        self.revoked.append(cert_id)


def _managed_domain(org, **overrides):
    from astrolift_clusters.models import ManagedDomain

    fields = {
        "zone": _ZONE,
        "organization": org,
        "dns_driver": "test-provider",
        "dns_config": {},
        "is_wildcard_managed": True,
        "provision_nameservers": list(_PLATFORM_NS),
    }
    fields.update(overrides)
    return ManagedDomain.objects.create(**fields)


# ---- wildcard cert SANs ---------------------------------------------------


@pytest.mark.django_db
def test_wildcard_cert_covers_the_preview_zone(monkeypatch, cluster, org):
    from astrolift_clusters.dns_layout import ZoneRegistrationStep

    domain = _managed_domain(org)
    fake = _FakeDns()
    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda c, cap: fake)

    provision_managed_domain._request_wildcard_cert_sync(cluster.pk, _ZONE, "Z1")

    assert fake.cert_calls[0]["sans"] == [
        _ZONE,
        f"*.{_ZONE}",
        f"*.pr.{_ZONE}",
    ]
    domain.refresh_from_db()
    assert domain.provision_state == ZoneRegistrationStep.CONFIGURE_CERT_POLICY.value


@pytest.mark.django_db
def test_reissue_keeps_the_same_san_scope(monkeypatch, cluster, org):
    """A reissue that narrowed the SANs would silently drop preview coverage."""
    _managed_domain(org)
    fake = _FakeDns()
    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda c, cap: fake)

    provision_managed_domain._reissue_cert_sync(cluster.pk, _ZONE, "old-cert", "Z1")

    assert fake.revoked == ["old-cert"]
    assert fake.cert_calls[0]["sans"] == [_ZONE, f"*.{_ZONE}", f"*.pr.{_ZONE}"]


@pytest.mark.django_db
def test_provision_reuses_recorded_zone_instead_of_creating_another(monkeypatch, cluster, org):
    """Restarting revalidation must preserve the operator's delegation."""
    domain = _managed_domain(
        org,
        dns_config={"zone_id": "Z-existing"},
        provision_nameservers=list(_PLATFORM_NS),
    )
    fake = _FakeDns()
    monkeypatch.setattr(
        provision_managed_domain,
        "_get_cluster_and_dns_driver",
        lambda cluster_id: (cluster, fake),
    )

    result = provision_managed_domain._provision_dns_zone_sync(cluster.pk, _ZONE)

    assert result == {"zone_id": "Z-existing", "nameservers": _PLATFORM_NS}
    domain.refresh_from_db()
    assert domain.dns_config["zone_id"] == "Z-existing"


# ---- NS delegation gate ---------------------------------------------------


@pytest.mark.django_db
def test_delegation_gate_fails_when_registrar_points_elsewhere(monkeypatch, cluster, org):
    _managed_domain(org)
    monkeypatch.setattr(
        provision_managed_domain,
        "_observed_nameservers",
        lambda zone: (frozenset({"ns1.previous-provider.example"}), ""),
    )

    out = provision_managed_domain._validate_ns_delegation_sync(cluster.pk, _ZONE)

    assert out["passed"] is False
    assert "ns-1.awsdns-01.com" in out["reason"]


@pytest.mark.django_db
def test_delegation_gate_passes_when_the_zone_is_delegated(monkeypatch, cluster, org):
    _managed_domain(org)
    monkeypatch.setattr(
        provision_managed_domain,
        "_observed_nameservers",
        lambda zone: (frozenset(ns.upper() + "." for ns in _PLATFORM_NS), ""),
    )

    out = provision_managed_domain._validate_ns_delegation_sync(cluster.pk, _ZONE)

    assert out["passed"] is True


@pytest.mark.django_db
def test_delegation_gate_fails_when_the_zone_is_not_delegated_at_all(monkeypatch, cluster, org):
    """A resolver that answers with no NS is a finding, not a skip."""
    _managed_domain(org)
    monkeypatch.setattr(
        provision_managed_domain,
        "_observed_nameservers",
        lambda zone: (frozenset(), ""),
    )

    out = provision_managed_domain._validate_ns_delegation_sync(cluster.pk, _ZONE)

    assert out["passed"] is False
    assert "no NS records observed" in out["reason"]


@pytest.mark.django_db
def test_delegation_gate_skips_when_the_lookup_cannot_run(monkeypatch, cluster, org):
    """An unrunnable check must not strand a correctly delegated zone."""
    _managed_domain(org)
    monkeypatch.setattr(
        provision_managed_domain,
        "_observed_nameservers",
        lambda zone: (None, "all resolvers failed"),
    )

    out = provision_managed_domain._validate_ns_delegation_sync(cluster.pk, _ZONE)

    assert out["passed"] is True
    assert "skipped" in out["reason"]


@pytest.mark.django_db
def test_delegation_gate_not_applicable_for_caller_owned_zones(monkeypatch, cluster, org):
    """is_platform_managed_zone=False never records nameservers, so there is
    no delegation of ours to verify."""
    _managed_domain(org, provision_nameservers=[])

    def _must_not_resolve(zone):  # pragma: no cover - guard
        raise AssertionError("no lookup should happen without platform nameservers")

    monkeypatch.setattr(provision_managed_domain, "_observed_nameservers", _must_not_resolve)

    out = provision_managed_domain._validate_ns_delegation_sync(cluster.pk, _ZONE)

    assert out["passed"] is True


def test_observed_nameservers_separates_no_delegation_from_no_answer(monkeypatch):
    import _sdk._dns_probe as probe

    monkeypatch.setattr(probe, "lookup_ns", lambda qname, **kw: ["NS-1.Example."])
    assert provision_managed_domain._observed_nameservers(_ZONE) == (
        frozenset({"ns-1.example"}),
        "",
    )

    monkeypatch.setattr(probe, "lookup_ns", lambda qname, **kw: [])
    observed, error = provision_managed_domain._observed_nameservers(_ZONE)
    assert observed == frozenset()
    assert error == ""

    def _boom(qname, **kw):
        raise probe.DnsResolveError("all resolvers failed for 'apps.example.com'")

    monkeypatch.setattr(probe, "lookup_ns", _boom)
    observed, error = provision_managed_domain._observed_nameservers(_ZONE)
    assert observed is None
    assert "all resolvers failed" in error


# ---- workflow step order --------------------------------------------------


def _run_workflow(monkeypatch, *, delegation):
    from temporalio import workflow as temporalio_workflow

    from astrolift_workflows.activities import (
        mark_managed_domain_active,
        poll_cert_issuance,
        provision_dns_zone,
        register_managed_domain_row,
        request_wildcard_cert_for_zone,
        validate_ns_delegation,
    )
    from astrolift_workflows.inputs import Actor, ProvisionManagedDomainInput
    from astrolift_workflows.workflows.provision_managed_domain import (
        ProvisionManagedDomainWorkflow,
    )

    called: list[str] = []

    async def _fake_execute_activity(activity_fn, *args, **kwargs):
        called.append(getattr(activity_fn, "__name__", str(activity_fn)))
        if activity_fn is provision_dns_zone:
            return {"zone_id": "Z1", "nameservers": _PLATFORM_NS}
        if activity_fn is request_wildcard_cert_for_zone:
            return "cert-1"
        if activity_fn is poll_cert_issuance:
            return {"status": "issued"}
        if activity_fn is register_managed_domain_row:
            return 1
        if activity_fn is validate_ns_delegation:
            return delegation
        assert activity_fn is mark_managed_domain_active
        return None

    monkeypatch.setattr(temporalio_workflow, "execute_activity", _fake_execute_activity)

    result = asyncio.new_event_loop().run_until_complete(
        ProvisionManagedDomainWorkflow().run(
            ProvisionManagedDomainInput(
                cluster_id=1,
                zone=_ZONE,
                is_platform_managed_zone=True,
                actor=Actor(kind="user", user_id=1, display="tester"),
            )
        )
    )
    return result, called


def test_workflow_refuses_to_activate_an_undelegated_zone(monkeypatch):
    result, called = _run_workflow(
        monkeypatch,
        delegation={"passed": False, "reason": "NS delegation incomplete: ['ns-1.awsdns-01.com']"},
    )

    assert result.ok is False
    assert "NS delegation not in place" in result.message
    assert "ns-1.awsdns-01.com" in result.message
    assert "mark_managed_domain_active" not in called


def test_workflow_activates_once_delegation_checks_out(monkeypatch):
    result, called = _run_workflow(
        monkeypatch,
        delegation={"passed": True, "reason": "NS delegation OK"},
    )

    assert result.ok is True
    assert called.index("validate_ns_delegation") < called.index("mark_managed_domain_active")
