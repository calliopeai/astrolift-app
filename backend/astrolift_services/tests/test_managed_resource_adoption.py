"""``adoptManagedResource``: authorization, tenancy, and the audit record (#1365).

The provider suite proves the cloud-side decision (what classifies as what,
what refuses, what gets written). This suite proves the control-plane
guarantees that decision depends on:

* it is behind its own grant, and the grants that provision a service do not
  open it -- the whole reason ``managed_service.adopt`` exists;
* it is fail-closed on the organization on the by-id path, because
  ``@tenant_scoped()`` asserts a tenant and filters nothing;
* the record answers "who adopted what, when, and what was on it before",
  including for a refusal, which is the case somebody will actually go looking
  for.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from django.contrib.auth import get_user_model

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_graphql import GUID
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedResourceAdoption, ManagedService
from astrolift_services.schema.mutations import ServicesMutation
from astrolift_services.schema.mutations.types import AdoptManagedResourceInput
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

SUBSCRIPTION = "00000000-1111-2222-3333-444444444444"
SERVER_ID = (
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-platform-prod"
    "/providers/Microsoft.Cache/redis/astrolift-shop-prod"
)
THEIRS = "another-managed-service"


# ---- scaffolding ----------------------------------------------------


def _info(user=None):
    request = SimpleNamespace(user=user, META={})
    return SimpleNamespace(context=SimpleNamespace(user=user, request=request))


def _user(username: str = "adopter"):
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={"email": f"{username}@example.com"},
    )
    return user


def _azure_cluster(org: Organization) -> TenantCluster:
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="Azure",
                slug="azure",
                plugin_version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ],
        ignore_conflicts=True,
    )
    return TenantCluster.objects.create(
        organization=org,
        slug=f"aks-{org.slug}",
        name="AKS",
        provider_plugin=ProviderPlugin.objects.get(slug="azure"),
        endpoint="https://aks.example.com",
        provider_config={
            "subscription_id": SUBSCRIPTION,
            "resource_group": "rg-platform-prod",
            "location": "eastus2",
            "vault_url": "https://platform-prod.vault.azure.net",
        },
    )


def _scaffold(slug_suffix: str = "a") -> tuple[Organization, ManagedService]:
    org = Organization.objects.create(name=f"Acme {slug_suffix}", slug=f"acme-adopt-{slug_suffix}")
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-adopt-{slug_suffix}")
    project = Project.objects.create(
        organization=org,
        team=team,
        name="Demo",
        slug=f"demo-adopt-{slug_suffix}",
    )
    cluster = _azure_cluster(org)
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Shop",
        slug=f"shop-adopt-{slug_suffix}",
        provisioning_status="ready",
        manifest_raw='astrolift_version = 1\nname = "shop"\n',
    )
    env = AppEnvironment.objects.create(registered_app=app, name="production", tenant_cluster=cluster)
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.REDIS,
        name="cache",
        variant="azure_cache_redis",
        status=ManagedService.Status.ACTIVE,
    )
    return org, svc


def _ctx(org: Organization):
    return tenant_context(TenantContext(organization_id=org.id))


class FakeAdopter:
    """Stands in for the ARM client, and records what was asked of it."""

    def __init__(self, markers: dict[str, str]) -> None:
        self.markers = dict(markers)
        self.stamped: dict[str, str] = {}

    def read_markers(self, ref: Any) -> dict[str, str]:
        return dict(self.markers)

    def stamp(self, ref: Any, envelope: dict[str, str], *, existing: dict[str, str]) -> None:
        self.stamped = dict(envelope)
        self.markers.update(envelope)


def _adopt(svc: ManagedService, markers: dict[str, str], **kwargs: Any):
    from astrolift_services.managed_resource_adoption import adopt_managed_resource

    adopter = FakeAdopter(markers)
    outcome = adopt_managed_resource(
        svc=svc,
        resource_id=SERVER_ID,
        reason=kwargs.pop("reason", "migrating a pre-identity-tag cache off manual teardown"),
        actor=kwargs.pop("actor", None),
        adopter=adopter,
        **kwargs,
    )
    return outcome, adopter


# ---- authorization --------------------------------------------------


def test_adoption_denies_without_its_own_grant(permission_resolver):
    """The grants that book a managed service must not also adopt one.

    Adoption reaches into a resource the platform did not necessarily create.
    If ``app.update`` (which ``provisionManagedService`` takes) opened it, every
    role that can add a database could point one at somebody else's server.
    """
    org, svc = _scaffold("deny")
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.MANAGED_SERVICE_CREATE)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)
    permission_resolver.grant(Permission.MANAGED_SERVICE_DESTROY)

    with _ctx(org):
        result = ServicesMutation().adopt_managed_resource(
            _info(user=_user()),
            input=AdoptManagedResourceInput(
                id=GUID(str(svc.guid)),
                resource_id=SERVER_ID,
                reason="because",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
    assert ManagedResourceAdoption.objects.count() == 0


def test_another_orgs_service_is_not_adoptable(permission_resolver, monkeypatch):
    """Holding the grant in your own org must not reach another org's row.

    ``@tenant_scoped()`` only asserts a tenant exists. A by-id fetch without an
    explicit organization filter would let a caller with the adopt grant point
    somebody else's managed service at a resource -- a cross-tenant takeover
    dressed as a migration.
    """
    org_a, _ = _scaffold("victim-a")
    _, victim = _scaffold("victim-b")
    permission_resolver.grant(Permission.MANAGED_SERVICE_ADOPT)

    called: list[str] = []

    def _never(**kwargs):
        called.append(kwargs["resource_id"])
        raise AssertionError("cross-org adoption reached the cloud")

    monkeypatch.setattr(
        "astrolift_services.managed_resource_adoption.adopt_managed_resource",
        _never,
    )

    with _ctx(org_a):
        result = ServicesMutation().adopt_managed_resource(
            _info(user=_user()),
            input=AdoptManagedResourceInput(
                id=GUID(str(victim.guid)),
                resource_id=SERVER_ID,
                reason="taking this one",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    assert called == []
    assert ManagedResourceAdoption.objects.count() == 0


def test_the_lookup_denies_rather_than_matching_when_there_is_no_tenant():
    """No tenant must resolve to nothing, by the guard and not by luck.

    ``_caller_org_id()`` is ``None`` outside a tenant context. Without the
    explicit ``None`` guard the filter becomes ``organization_id=None``, which
    matches nothing today only because both owner FKs are non-null on live
    rows -- a schema change turns the same expression into "any row in any
    organization".
    """
    from astrolift_services.schema.mutations.managed_services import _managed_service_for_caller

    _, svc = _scaffold("no-tenant")
    assert _managed_service_for_caller(GUID(str(svc.guid))) is None


# ---- the record -----------------------------------------------------


def test_record_captures_prior_markers_and_who_approved_it():
    """The distinction #1365 asks for, persisted.

    "It had no tag" and "it had someone else's tag" have to be recoverable from
    the record, and the cloud stops being able to answer that the moment the
    envelope is merged.
    """
    _, svc = _scaffold("record")
    actor = _user("record-adopter")

    outcome, adopter = _adopt(
        svc,
        {"astrolift-managed-by": "platform", "astrolift-org": "acme", "owner": "customer"},
        actor=actor,
    )

    record = outcome.record
    assert record.status == ManagedResourceAdoption.Status.ADOPTED
    assert record.classification == ManagedResourceAdoption.Classification.UNSTAMPED
    assert record.prior_managed_by == "platform"
    assert record.prior_managed_service_id == ""
    # Every platform marker, and nothing of the operator's own.
    assert record.prior_markers == {"astrolift-managed-by": "platform", "astrolift-org": "acme"}
    assert record.actor_display == actor.email
    assert record.resource_id == SERVER_ID
    assert record.reason.startswith("migrating a pre-identity-tag cache")
    # The envelope really was written, and the record holds it.
    assert adopter.stamped["astrolift-managed-service-id"] == str(svc.guid)
    assert record.stamped_markers["astrolift-adopted"] == "true"


def test_a_foreign_owner_needs_naming_and_the_refusal_is_recorded():
    """A refused takeover is the most interesting row this log will hold."""
    from astrolift_services.managed_resource_adoption import AdoptionRefused

    _, svc = _scaffold("foreign")
    markers = {"astrolift-managed-by": "platform", "astrolift-managed-service-id": THEIRS}

    with pytest.raises(AdoptionRefused):
        _adopt(svc, markers, actor=_user("would-be-adopter"))

    refusal = ManagedResourceAdoption.objects.get()
    assert refusal.status == ManagedResourceAdoption.Status.REFUSED
    assert refusal.prior_managed_service_id == THEIRS
    assert refusal.actor_display == "would-be-adopter@example.com"
    assert THEIRS in refusal.error

    outcome, adopter = _adopt(svc, markers, acknowledged_prior_owner=THEIRS, actor=_user("would-be-adopter"))
    assert outcome.record.classification == ManagedResourceAdoption.Classification.FOREIGN_OWNER
    assert outcome.record.acknowledged_prior_owner == THEIRS
    assert adopter.stamped["astrolift-managed-service-id"] == str(svc.guid)
    assert ManagedResourceAdoption.objects.count() == 2


def test_an_adoption_must_state_a_reason():
    from astrolift_services.managed_resource_adoption import AdoptionRefused

    _, svc = _scaffold("reason")
    with pytest.raises(AdoptionRefused):
        _adopt(svc, {}, reason="   ")


def test_a_non_azure_service_is_refused_rather_than_reported_adopted():
    """Silence would be worse than a refusal here.

    Returning success for a cloud nothing was written to leaves the operator
    with a green record and a resource that still fails teardown.
    """
    from astrolift_services.managed_resource_adoption import AdoptionUnsupported

    org, svc = _scaffold("k8s")
    cluster = svc.app_environment.tenant_cluster
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="K8s Native",
                slug="k8s_native",
                plugin_version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ],
        ignore_conflicts=True,
    )
    cluster.provider_plugin = ProviderPlugin.objects.get(slug="k8s_native")
    cluster.save(update_fields=["provider_plugin", "updated_at", "version"])
    svc.variant = "memcached"
    svc.kind = ManagedService.Kind.CACHE
    svc.save(update_fields=["variant", "kind", "updated_at", "version"])

    with pytest.raises(AdoptionUnsupported):
        _adopt(svc, {})
    assert ManagedResourceAdoption.objects.count() == 0


def test_mutation_returns_the_record_the_caller_just_approved(permission_resolver, monkeypatch):
    org, svc = _scaffold("mutation")
    permission_resolver.grant(Permission.MANAGED_SERVICE_ADOPT)

    real = None

    def _with_fake_adopter(**kwargs):
        return real(**kwargs, adopter=FakeAdopter({"astrolift-managed-by": "platform"}))

    from astrolift_services import managed_resource_adoption as service_module

    real = service_module.adopt_managed_resource
    monkeypatch.setattr(service_module, "adopt_managed_resource", _with_fake_adopter)

    with _ctx(org):
        result = ServicesMutation().adopt_managed_resource(
            _info(user=_user("mutation-adopter")),
            input=AdoptManagedResourceInput(
                id=GUID(str(svc.guid)),
                resource_id=SERVER_ID,
                reason="pre-tag cache, teardown is refusing",
            ),
        )

    assert result.ok, result.errors
    payload = result.data
    assert payload is not None
    assert payload.classification == "unstamped"
    assert payload.resource_id == SERVER_ID
    assert payload.prior_markers == {"astrolift-managed-by": "platform"}
    assert payload.stamped_markers["astrolift-managed-service-id"] == str(svc.guid)
    assert payload.actor_display == "mutation-adopter@example.com"


# ---- adoption stays out of every implicit path ----------------------


def test_nothing_but_the_adopt_mutation_calls_the_adoption_service():
    """#1365's "never implicit", asserted across the control plane.

    The provider suite pins that no *driver* can reach adoption. This pins the
    other side: no workflow, activity, signal handler or other mutation may
    call it either. A single extra call site is all it takes to turn adoption
    back into something that happens to an operator rather than something they
    decide.
    """
    import ast
    from pathlib import Path

    backend = Path(__file__).resolve().parents[2]
    allowed = {
        backend / "astrolift_services" / "managed_resource_adoption.py",
        backend / "astrolift_services" / "schema" / "mutations" / "managed_services.py",
    }
    offenders: list[str] = []
    for path in backend.rglob("*.py"):
        if path in allowed or "/tests/" in str(path) or path.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(path.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("managed_resource_adoption"):
                if any(alias.name == "adopt_managed_resource" for alias in node.names):
                    offenders.append(str(path.relative_to(backend)))
    assert offenders == [], (
        f"{offenders} call the adoption service; adoption must stay reachable only "
        f"through the separately authorized adoptManagedResource mutation"
    )
