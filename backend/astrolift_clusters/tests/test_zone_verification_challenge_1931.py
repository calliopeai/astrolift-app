"""Proof of control before a registered zone name that already exists gets a
DNS write, a cert, or a hostname (#1931).

#2025 and #2033 pinned every DNS write to a zone the platform itself
created. This covers the other half: ``createManagedDomain`` naming a zone
that already exists (or explicitly adopting one via ``dns_config``) now
stores the row ``pending`` instead of provisioning it, and the resolution
helpers treat a pending row as unregistered until ``verifyManagedDomain``
confirms the caller published the TXT challenge.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_clusters.models import ManagedDomain, ProviderPlugin
from astrolift_clusters.models.managed_domain import (
    managed_domain_for_zone,
    resolve_managed_domain,
)
from astrolift_clusters.schema import mutations as mutations_module
from astrolift_clusters.schema.mutations import (
    ClustersMutation,
    CreateManagedDomainInput,
    VerifyManagedDomainInput,
    _zone_exists_in_provider,
)
from astrolift_clusters.tests.test_heartbeat import _make_cluster  # noqa: F401 (helper, not a fixture)
from astrolift_identity.models import Organization
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, g: None))


def _info(user=None):
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user), user=user))


def _tenant_admin():
    return User.objects.create(username=f"admin-{uuid.uuid4().hex[:6]}")


def _denied(result) -> bool:
    return result.ok is False and result.errors[0].code == "PERMISSION_DENIED"


def _org(prefix: str) -> Organization:
    return Organization.objects.create(name=prefix, slug=f"{prefix}-{uuid.uuid4().hex[:6]}")


def _plugin(prefix: str = "aws") -> ProviderPlugin:
    [p] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name=prefix,
                slug=f"{prefix}-1931-{uuid.uuid4().hex[:6]}",
                capabilities_manifest={},
                config_schema={},
            )
        ]
    )
    return p


def _create(org, zone, **kwargs):
    with tenant_context(TenantContext(organization_id=org.id)):
        return ClustersMutation().create_managed_domain(
            _info(), CreateManagedDomainInput(zone=zone, dns_driver="route53", **kwargs)
        )


def _verify(org, zone, user=None):
    with tenant_context(TenantContext(organization_id=org.id)):
        return ClustersMutation().verify_managed_domain(_info(user), VerifyManagedDomainInput(zone=zone))


def _spy_start_workflow(monkeypatch, calls):
    monkeypatch.setattr(mutations_module, "start_workflow", lambda *a, **k: calls.append((a, k)))


# ---- createManagedDomain: when a challenge is required ----------------


def test_adopting_a_zone_id_via_dns_config_is_pending_and_starts_no_workflow(
    permission_resolver, monkeypatch
):
    permission_resolver.grant(Permission.PROVIDER_PLUGIN_CONFIGURE)
    calls: list = []
    _spy_start_workflow(monkeypatch, calls)
    org = _org("adopt")

    result = _create(org, "shared.example", dns_config={"zone_id": "Z123EXISTING"})

    assert result.ok, result.errors
    domain = ManagedDomain.objects.get(organization=org)
    assert domain.verification_state == ManagedDomain.VerificationState.PENDING
    assert len(domain.verification_token) == 32
    assert domain.verified_at is None
    assert calls == []
    assert result.data.verification_state == "pending"
    assert result.data.challenge_record_name == "_astrolift-challenge.shared.example"
    assert result.data.challenge_record_value == domain.verification_token


def test_a_name_that_already_exists_in_the_provider_is_pending_and_starts_no_workflow(
    permission_resolver, monkeypatch
):
    permission_resolver.grant(Permission.PROVIDER_PLUGIN_CONFIGURE)
    monkeypatch.setattr(mutations_module, "_zone_exists_in_provider", lambda cluster, zone: True)
    calls: list = []
    _spy_start_workflow(monkeypatch, calls)
    org = _org("dup")

    result = _create(org, "install.example")

    assert result.ok, result.errors
    domain = ManagedDomain.objects.get(organization=org)
    assert domain.verification_state == ManagedDomain.VerificationState.PENDING
    assert domain.verification_token
    assert calls == []


def test_a_brand_new_zone_needs_no_challenge(permission_resolver, monkeypatch):
    permission_resolver.grant(Permission.PROVIDER_PLUGIN_CONFIGURE)
    monkeypatch.setattr(mutations_module, "_zone_exists_in_provider", lambda cluster, zone: False)
    org = _org("fresh")

    result = _create(org, "fresh.example")

    assert result.ok, result.errors
    domain = ManagedDomain.objects.get(organization=org)
    assert domain.verification_state == ManagedDomain.VerificationState.NOT_REQUIRED
    assert domain.verification_token == ""
    assert result.data.verification_state == "not_required"
    assert result.data.challenge_record_name == ""


def test_a_brand_new_zone_with_a_registered_cluster_still_starts_provisioning(
    permission_resolver, monkeypatch
):
    """Regression: the pending branch must not swallow the existing #1673
    happy path once a DNS cluster is available."""
    permission_resolver.grant(Permission.PROVIDER_PLUGIN_CONFIGURE)
    monkeypatch.setattr(mutations_module, "_zone_exists_in_provider", lambda cluster, zone: False)
    calls: list = []
    _spy_start_workflow(monkeypatch, calls)
    org = _org("fresh-cluster")
    plugin = _plugin()
    _make_cluster(org, plugin)

    result = _create(org, "fresh-cluster.example")

    assert result.ok, result.errors
    assert len(calls) == 1
    (name, *_rest), kwargs = calls[0]
    assert name == "ProvisionManagedDomainWorkflow"
    assert kwargs["args"][0].is_platform_managed_zone is True


# ---- the _zone_exists_in_provider helper --------------------------------


def test_zone_exists_in_provider_uses_the_optional_driver_hook(monkeypatch):
    fake_driver = SimpleNamespace(zone_exists=lambda zone: zone == "taken.example")
    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda cluster, capability: fake_driver)

    assert _zone_exists_in_provider(SimpleNamespace(), "taken.example") is True
    assert _zone_exists_in_provider(SimpleNamespace(), "free.example") is False


def test_zone_exists_in_provider_defaults_to_false(monkeypatch):
    assert _zone_exists_in_provider(None, "zone.example") is False

    monkeypatch.setattr(
        "core.app_deploy.driver_for_capability", lambda cluster, capability: SimpleNamespace()
    )
    assert _zone_exists_in_provider(SimpleNamespace(), "zone.example") is False

    def _boom(cluster, capability):
        raise RuntimeError("no credentials configured")

    monkeypatch.setattr("core.app_deploy.driver_for_capability", _boom)
    assert _zone_exists_in_provider(SimpleNamespace(), "zone.example") is False


# ---- resolve_managed_domain / managed_domain_for_zone skip pending rows --


def test_managed_domain_for_zone_skips_a_pending_row_until_verified():
    org = _org("pending-zone")
    pending = ManagedDomain.objects.create(
        organization=org,
        zone="pending.example",
        dns_driver="route53",
        verification_state=ManagedDomain.VerificationState.PENDING,
        verification_token="tok",
    )

    assert managed_domain_for_zone("pending.example", org.pk) is None

    pending.verification_state = ManagedDomain.VerificationState.VERIFIED
    pending.save()
    assert managed_domain_for_zone("pending.example", org.pk) == pending


def test_resolve_managed_domain_skips_a_pending_shared_zone():
    org = _org("resolve-pending")
    ManagedDomain.objects.create(
        zone="shared-pending.example",
        dns_driver="route53",
        default_for=ManagedDomain.DefaultFor.TENANT_APPS,
        verification_state=ManagedDomain.VerificationState.PENDING,
        verification_token="tok",
    )

    assert resolve_managed_domain(org, for_preview=False) is None


def test_resolve_managed_domain_skips_a_pending_org_default():
    org = _org("resolve-pending-default")
    pending = ManagedDomain.objects.create(
        organization=org,
        zone="own-pending.example",
        dns_driver="route53",
        verification_state=ManagedDomain.VerificationState.PENDING,
        verification_token="tok",
    )
    org.default_managed_domain = pending
    org.save()

    assert resolve_managed_domain(org, for_preview=False) is None


# ---- verifyManagedDomain -------------------------------------------------


def _pending_domain(org, zone="pending.example", token="feedfacefeedfacefeedfacefeedface"):
    return ManagedDomain.objects.create(
        organization=org,
        zone=zone,
        dns_driver="route53",
        verification_state=ManagedDomain.VerificationState.PENDING,
        verification_token=token,
    )


def test_verify_answers_not_found_for_an_unregistered_zone(permission_resolver):
    permission_resolver.grant(Permission.PROVIDER_PLUGIN_CONFIGURE)
    org = _org("verify-404")

    result = _verify(org, "nope.example")

    assert result.ok is False
    assert result.errors[0].code == "NOT_FOUND"


def test_verify_is_a_noop_success_when_no_challenge_is_pending(permission_resolver):
    permission_resolver.grant(Permission.PROVIDER_PLUGIN_CONFIGURE)
    org = _org("verify-noop")
    domain = ManagedDomain.objects.create(organization=org, zone="plain.example", dns_driver="route53")
    assert domain.verification_state == ManagedDomain.VerificationState.NOT_REQUIRED

    result = _verify(org, domain.zone)

    assert result.ok, result.errors
    assert result.data.verified is True


def test_verify_marks_verified_when_the_txt_record_matches(permission_resolver, monkeypatch):
    permission_resolver.grant(Permission.PROVIDER_PLUGIN_CONFIGURE)
    org = _org("verify-ok")
    domain = _pending_domain(org, zone="verify-ok.example")
    monkeypatch.setattr(
        "_sdk._dns_probe.lookup_txt",
        lambda name, timeout=3.0: [f"astrolift-challenge={domain.verification_token}"],
    )
    calls: list = []
    _spy_start_workflow(monkeypatch, calls)

    result = _verify(org, domain.zone)

    assert result.ok, result.errors
    assert result.data.verified is True
    domain.refresh_from_db()
    assert domain.verification_state == ManagedDomain.VerificationState.VERIFIED
    assert domain.verified_at is not None
    # No DNS cluster registered in this test -- nothing to kick off, but the
    # verified state itself must not depend on one being present.
    assert calls == []


def test_verify_starts_provisioning_unpinned_once_a_cluster_exists(permission_resolver, monkeypatch):
    permission_resolver.grant(Permission.PROVIDER_PLUGIN_CONFIGURE)
    org = _org("verify-cluster")
    plugin = _plugin()
    _make_cluster(org, plugin)
    domain = _pending_domain(org, zone="verify-cluster.example")
    monkeypatch.setattr(
        "_sdk._dns_probe.lookup_txt",
        lambda name, timeout=3.0: [domain.verification_token],
    )
    calls: list = []
    _spy_start_workflow(monkeypatch, calls)

    result = _verify(org, domain.zone)

    assert result.ok, result.errors
    assert result.data.verified is True
    assert len(calls) == 1
    (name, *_rest), kwargs = calls[0]
    assert name == "ProvisionManagedDomainWorkflow"
    started = kwargs["args"][0]
    # The platform did not create this zone -- adopting it after proof of
    # control only requests the cert and writes validation records, it
    # never calls provision_zone (#1931).
    assert started.is_platform_managed_zone is False
    assert started.zone == domain.zone


def test_verify_stays_pending_when_the_txt_value_does_not_match(permission_resolver, monkeypatch):
    permission_resolver.grant(Permission.PROVIDER_PLUGIN_CONFIGURE)
    org = _org("verify-mismatch")
    domain = _pending_domain(org, zone="mismatch.example")
    monkeypatch.setattr("_sdk._dns_probe.lookup_txt", lambda name, timeout=3.0: ["unrelated-value"])

    result = _verify(org, domain.zone)

    assert result.ok, result.errors  # the call executed; the finding is what's false
    assert result.data.verified is False
    domain.refresh_from_db()
    assert domain.verification_state == ManagedDomain.VerificationState.PENDING


def test_verify_stays_pending_when_the_txt_lookup_fails(permission_resolver, monkeypatch):
    permission_resolver.grant(Permission.PROVIDER_PLUGIN_CONFIGURE)
    from _sdk._dns_probe import DnsResolveError

    org = _org("verify-dnserror")
    domain = _pending_domain(org, zone="dnserror.example")

    def _boom(name, timeout=3.0):
        raise DnsResolveError("all resolvers failed")

    monkeypatch.setattr("_sdk._dns_probe.lookup_txt", _boom)

    result = _verify(org, domain.zone)

    assert result.ok, result.errors
    assert result.data.verified is False
    domain.refresh_from_db()
    assert domain.verification_state == ManagedDomain.VerificationState.PENDING


def test_a_tenant_cannot_verify_a_shared_pending_zone(permission_resolver):
    """Shared (org-NULL) rows are the platform operator's to verify, same as
    every other shared-zone write (#1918, #1929)."""
    permission_resolver.grant(Permission.PROVIDER_PLUGIN_CONFIGURE)
    org = _org("verify-shared-denied")
    shared = _pending_domain(None, zone="shared-verify.example")

    result = _verify(org, shared.zone, user=_tenant_admin())

    assert _denied(result), result
    shared.refresh_from_db()
    assert shared.verification_state == ManagedDomain.VerificationState.PENDING
