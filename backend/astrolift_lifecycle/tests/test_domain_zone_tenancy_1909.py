"""A DNS zone resolves only for its own org or when shared (#1909).

Every path that resolved a zone by name, or from a hostname, found another
org's ``ManagedDomain`` and acted on it. Custom domains marked it
platform-managed and the records activity wrote into it. The provisioning
mutations re-pointed and re-certified it. ``deleteAppDnsRecord`` and the
decommission sweep deleted records in it. Zone names are guessable.

Each path gets a cross-org attempt that is refused with no DNS driver call
and no workflow, answering exactly as a zone nobody registered would. The
org's own zone and a shared (org NULL) zone still work on every path. Real
Postgres; the DNS driver and the Temporal client are recording fakes.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ManagedDomain, managed_domain_for_zone
from astrolift_clusters.schema.mutations import ClustersMutation
from astrolift_graphql import GUID
from astrolift_identity.models import Organization
from astrolift_lifecycle.custom_domain_handshake import resolve_cluster_ingress_target
from astrolift_lifecycle.models import CustomDomain
from astrolift_lifecycle.schema.mutations import (
    AddAppDomainInput,
    AddWildcardDomainInput,
    DeleteAppDnsRecordInput,
    LifecycleMutation,
)
from astrolift_workflows.activities.cluster_decommission_cleanup import _cleanup_dns_records_sync
from astrolift_workflows.activities.custom_domain import _ensure_records_sync
from core.mutations import ErrorCode
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

OWN_ZONE = "acme-apps.example"
FOREIGN_ZONE = "globex-apps.example"
SHARED_ZONE = "shared-apps.example"
UNREGISTERED_ZONE = "nobody.example"


# ---- scaffolding ---------------------------------------------------------


@pytest.fixture
def zones(org):
    """The caller's org owns one zone, another org one, and one is shared."""
    other = Organization.objects.create(name="Globex", slug="globex-1909")
    fields = {"dns_driver": "route53", "is_wildcard_managed": True}
    return SimpleNamespace(
        own=ManagedDomain.objects.create(organization=org, zone=OWN_ZONE, **fields),
        foreign=ManagedDomain.objects.create(
            organization=other,
            zone=FOREIGN_ZONE,
            dns_config={"zone_id": "Z-GLOBEX", "certificate_arn": "arn:globex-cert"},
            provision_state="mark_active",
            provision_nameservers=["ns-1.globex.example"],
            provision_cert_id="arn:globex-cert",
            **fields,
        ),
        shared=ManagedDomain.objects.create(organization=None, zone=SHARED_ZONE, **fields),
    )


@pytest.fixture
def app_on_cluster(app, cluster):
    """The records activity and the DNS deletes act through the app's default cluster."""
    app.default_tenant_cluster = cluster
    app.save(update_fields=["default_tenant_cluster", "updated_at", "version"])
    return app


class _RecordingDns:
    """Every DNS driver resolution and call, in order."""

    def __init__(self) -> None:
        self.resolved: list[str] = []
        self.calls: list[tuple[str, dict]] = []

    def resolver(self, cluster, capability):
        self.resolved.append(capability)
        return self

    def ensure_record(self, **kwargs):
        self.calls.append(("ensure_record", kwargs))

    def delete_record(self, zone, name, type):
        self.calls.append(("delete_record", {"zone": zone, "name": name, "type": type}))


@pytest.fixture
def dns(monkeypatch):
    fake = _RecordingDns()
    monkeypatch.setattr("core.app_deploy.driver_for_capability", fake.resolver)
    monkeypatch.setattr(
        "astrolift_workflows.activities.capability_deprovision._resolve_capability_driver", fake.resolver
    )
    monkeypatch.setattr(
        "astrolift_workflows.activities.cluster_decommission_cleanup._resolve_capability_driver",
        fake.resolver,
    )
    return fake


@pytest.fixture
def temporal(monkeypatch):
    """Workflow starts and signals the zone mutations issue."""
    rec = SimpleNamespace(starts=[], signals=[])

    def _start(name, args, *, workflow_id, task_queue=None):
        from astrolift_workflows.client import WorkflowHandle

        rec.starts.append((name, args, workflow_id))
        return WorkflowHandle(workflow_id=workflow_id, run_id="run-1", enqueued=True)

    def _signal(workflow_id, signal_name, *args):
        rec.signals.append((workflow_id, signal_name))
        return True

    monkeypatch.setattr("astrolift_clusters.schema.mutations.start_workflow", _start)
    monkeypatch.setattr("astrolift_clusters.schema.mutations.signal_workflow", _signal)
    return rec


def _as(org, actor):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id))


def _add_domain(kind: str, app, zone: str, fake_info):
    """Add ``shop.<zone>`` through ``addAppDomain`` or ``addWildcardDomain``."""
    mutation = LifecycleMutation()
    hostname = f"shop.{zone}"
    if kind == "single":
        return mutation.add_app_domain(
            fake_info, input=AddAppDomainInput(app_slug=app.slug, hostname=hostname)
        )
    return mutation.add_wildcard_domain(
        fake_info, input=AddWildcardDomainInput(app_slug=app.slug, hostname=hostname)
    )


def _snapshot(domain: ManagedDomain) -> tuple:
    domain.refresh_from_db()
    return (
        domain.dns_driver,
        domain.dns_config,
        domain.provision_state,
        domain.provision_nameservers,
        domain.provision_cert_id,
        domain.deleted_at,
    )


# ---- the zone rule itself ------------------------------------------------


def test_zone_resolves_for_its_own_org_and_when_shared_only(org, zones):
    assert managed_domain_for_zone(OWN_ZONE, org.id) == zones.own
    assert managed_domain_for_zone(SHARED_ZONE, org.id) == zones.shared
    assert managed_domain_for_zone(FOREIGN_ZONE, org.id) is None
    assert managed_domain_for_zone(UNREGISTERED_ZONE, org.id) is None


def test_zone_rule_fails_closed_without_an_org(zones):
    """With no org the shared branch alone would match every shared zone."""
    assert managed_domain_for_zone(SHARED_ZONE, None) is None


# ---- addAppDomain / addWildcardDomain ------------------------------------


@pytest.mark.parametrize("kind", ["single", "wildcard"])
def test_a_hostname_under_another_orgs_zone_is_self_serve_like_an_unregistered_one(
    kind, org, app_on_cluster, cluster, zones, actor, fake_info, permission_resolver, no_temporal, dns
):
    permission_resolver.grant(Permission.APP_UPDATE)
    with _as(org, actor):
        foreign = _add_domain(kind, app_on_cluster, FOREIGN_ZONE, fake_info)
        unregistered = _add_domain(kind, app_on_cluster, UNREGISTERED_ZONE, fake_info)

    assert foreign.ok, foreign.errors
    assert unregistered.ok, unregistered.errors
    row = CustomDomain.objects.get(hostname=f"shop.{FOREIGN_ZONE}")
    assert row.is_platform_managed_zone is False
    # The CNAME target must not name the other org's zone either: it is the
    # same fallback an unregistered zone gets.
    fallback = resolve_cluster_ingress_target(cluster_slug=cluster.slug, managed_domain_zone=None)
    assert row.expected_cname_target == fallback
    assert foreign.data.is_platform_managed_zone == unregistered.data.is_platform_managed_zone

    # The validation workflow's first step then writes nothing anywhere.
    _ensure_records_sync(row.pk)
    assert dns.calls == []


@pytest.mark.parametrize("kind", ["single", "wildcard"])
@pytest.mark.parametrize("which", ["own", "shared"])
def test_a_hostname_under_the_orgs_own_or_a_shared_zone_is_platform_managed(
    kind, which, org, app_on_cluster, zones, actor, fake_info, permission_resolver, no_temporal, dns
):
    zone = getattr(zones, which).zone
    permission_resolver.grant(Permission.APP_UPDATE)
    with _as(org, actor):
        result = _add_domain(kind, app_on_cluster, zone, fake_info)

    assert result.ok, result.errors
    row = CustomDomain.objects.get(hostname=f"shop.{zone}")
    assert row.is_platform_managed_zone is True
    assert row.expected_cname_target == f"ingress.{zone}"

    _ensure_records_sync(row.pk)
    written = [(c[1]["zone"], c[1]["type"]) for c in dns.calls if c[0] == "ensure_record"]
    assert written == [(zone, "CNAME"), (zone, "TXT")]


# ---- ensure_platform_managed_records -------------------------------------


def test_the_records_activity_writes_nothing_for_a_row_flagged_under_another_orgs_zone(
    app_on_cluster, zones, dns
):
    """Rows added before #1909 carry ``is_platform_managed_zone`` for the
    other org's zone; the activity must not trust the stored flag."""
    row = CustomDomain.objects.create(
        registered_app=app_on_cluster,
        hostname=f"www.{FOREIGN_ZONE}",
        is_platform_managed_zone=True,
        required_dns_records=[
            {"kind": "CNAME", "name": f"www.{FOREIGN_ZONE}.", "value": "ingress.acme.example.", "ttl": 300},
            {
                "kind": "TXT",
                "name": f"_astrolift-challenge.www.{FOREIGN_ZONE}.",
                "value": "token",
                "ttl": 300,
            },
        ],
    )

    result = _ensure_records_sync(row.pk)

    assert dns.resolved == []
    assert dns.calls == []
    assert result["ensured"] == []


# ---- provisionManagedDomain / revalidateManagedDomain / reissueManagedDomainCert


def _zone_mutation(name: str, cluster, zone: str, fake_info):
    mutation = ClustersMutation()
    cluster_id = GUID(str(cluster.guid))
    if name == "provision":
        return mutation.provision_managed_domain(
            fake_info, cluster_id=cluster_id, zone=zone, is_platform_managed_zone=True
        )
    if name == "revalidate":
        return mutation.revalidate_managed_domain(fake_info, cluster_id=cluster_id, zone=zone)
    return mutation.reissue_managed_domain_cert(fake_info, cluster_id=cluster_id, zone=zone)


@pytest.mark.parametrize("name", ["provision", "revalidate", "reissue"])
def test_zone_mutations_refuse_another_orgs_zone_like_an_unregistered_one(
    name, org, cluster, zones, actor, fake_info, permission_resolver, temporal
):
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    before = _snapshot(zones.foreign)
    with _as(org, actor):
        foreign = _zone_mutation(name, cluster, FOREIGN_ZONE, fake_info)
        unregistered = _zone_mutation(name, cluster, UNREGISTERED_ZONE, fake_info)

    assert foreign.ok is False
    assert foreign.errors[0].code == ErrorCode.NOT_FOUND.value
    assert foreign.errors[0].field == "zone"
    assert [(e.code, e.message, e.field) for e in foreign.errors] == [
        (e.code, e.message, e.field) for e in unregistered.errors
    ]
    assert temporal.starts == []
    assert temporal.signals == []
    assert _snapshot(zones.foreign) == before


@pytest.mark.parametrize("name", ["provision", "revalidate", "reissue"])
@pytest.mark.parametrize("which", ["own", "shared"])
def test_zone_mutations_act_on_the_orgs_own_and_shared_zones(
    name, which, org, cluster, zones, actor, fake_info, permission_resolver, temporal
):
    zone = getattr(zones, which).zone
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    with _as(org, actor):
        result = _zone_mutation(name, cluster, zone, fake_info)

    assert result.ok, result.errors
    workflow_id = f"ProvisionManagedDomainWorkflow-{cluster.guid}-{zone.replace('.', '-')}"
    if name == "provision":
        assert [(n, w) for n, _, w in temporal.starts] == [("ProvisionManagedDomainWorkflow", workflow_id)]
        assert temporal.starts[0][1][0].zone == zone
    else:
        assert temporal.signals == [(workflow_id, "revalidate" if name == "revalidate" else "reissue")]


# ---- deleteAppDnsRecord --------------------------------------------------


def _delete_record(app, hostname: str, fake_info):
    return LifecycleMutation().delete_app_dns_record(
        info=fake_info,
        input=DeleteAppDnsRecordInput(app_id=str(app.guid), hostname=hostname, record_type="CNAME"),
    )


def test_delete_app_dns_record_refuses_another_orgs_zone_like_an_unregistered_one(
    org, app_on_cluster, zones, actor, fake_info, permission_resolver, dns
):
    permission_resolver.grant(Permission.APP_DELETE)
    with _as(org, actor):
        foreign = _delete_record(app_on_cluster, f"www.{FOREIGN_ZONE}", fake_info)
        unregistered = _delete_record(app_on_cluster, f"www.{UNREGISTERED_ZONE}", fake_info)

    assert foreign.ok is False
    assert foreign.errors[0].code == ErrorCode.PRECONDITION.value
    # Same refusal as a zone nobody registered, apart from the zone name the
    # caller typed in.
    foreign_message = foreign.errors[0].message.replace(FOREIGN_ZONE, "<zone>")
    unregistered_message = unregistered.errors[0].message.replace(UNREGISTERED_ZONE, "<zone>")
    assert foreign_message == unregistered_message
    assert dns.resolved == []
    assert dns.calls == []


@pytest.mark.parametrize("which", ["own", "shared"])
def test_delete_app_dns_record_acts_in_the_orgs_own_and_shared_zones(
    which, org, app_on_cluster, zones, actor, fake_info, permission_resolver, dns
):
    zone = getattr(zones, which).zone
    permission_resolver.grant(Permission.APP_DELETE)
    with _as(org, actor):
        result = _delete_record(app_on_cluster, f"www.{zone}", fake_info)

    assert result.ok, result.errors
    assert dns.calls == [("delete_record", {"zone": zone, "name": "www", "type": "CNAME"})]


# ---- cluster decommission DNS sweep --------------------------------------


def test_decommission_sweep_deletes_custom_domain_records_only_in_zones_of_the_apps_org(
    app_on_cluster, cluster, zones, dns
):
    for zone in (OWN_ZONE, SHARED_ZONE, FOREIGN_ZONE, UNREGISTERED_ZONE):
        CustomDomain.objects.create(registered_app=app_on_cluster, hostname=f"www.{zone}")

    summary = _cleanup_dns_records_sync(cluster.pk)

    deleted = sorted(c[1]["zone"] for c in dns.calls if c[0] == "delete_record")
    assert deleted == sorted([OWN_ZONE, SHARED_ZONE])
    assert summary["deleted"] == 2
    assert summary["errors"] == []
