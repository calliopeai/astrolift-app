"""Cross-org tenant-isolation regression tests for the services + registry
read resolvers (#1042).

Every services read resolver carried ``@tenant_scoped()`` + ``@require_permission``
but never filtered by the caller's org. ``@tenant_scoped()`` only asserts a tenant
context EXISTS — it does NOT filter any queryset. So a resolver could look correctly
decorated, pass the tenancy guardrail, and still hand org B's rows to a caller in
org A by passing org A's slug / guid.

This module proves, for every resolver fixed in the sweep, that:

  * a caller in org B cannot read org A's rows (returns ``[]`` / ``None``), AND
  * the in-org case still returns the right rows.

Each test is constructed so it FAILS if the org filter is removed from the resolver
under test: the leaked org's row is seeded and would come back. A few registry
resolvers fixed in the same sweep (``astrolift_apps`` / ``astrolift_app`` /
``astrolift_workload``) are covered too, to lock the whole sweep end-to-end.

Real Postgres, no DB mocks. The cloud email drivers are stubbed via the
``register_driver_override`` seam (same hermetic approach as
``test_email_observability.py``) so the resolver path is exercised end-to-end.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from _sdk.email import EmailTemplate, TemplateSendStatPoint
from django.contrib.auth import get_user_model
from django.utils import timezone

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_graphql import GUID
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_operations.models import AuditEvent
from astrolift_registry.models import RegisteredApp, Workload
from astrolift_registry.schema.queries import RegistryQuery
from astrolift_services.email_observability import (
    register_driver_override,
    reset_driver_cache,
)
from astrolift_services.models import (
    AppSecretBundleRef,
    EmailEvent,
    EmailEventKind,
    ManagedService,
    SecretBundle,
    SecretChangeProposal,
)
from astrolift_services.schema.queries import ServicesQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


_MANIFEST_TEMPLATE = """\
astrolift_version = 1
name = "{name}"

[env]
{secret_key} = "{secret_val}"

[[workloads]]
name = "web"
kind = "deployment"
"""


# ---- Scaffolding ----------------------------------------------------


def _info(user=None):
    request = SimpleNamespace(user=user, META={})
    return SimpleNamespace(context=SimpleNamespace(user=user, request=request))


def _caller(username: str = "caller-1042"):
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={"email": f"{username}@example.test"},
    )
    return user


def _ctx(graph: _Graph):
    return tenant_context(TenantContext(organization_id=graph.org.id))


@dataclasses.dataclass
class _Graph:
    org: object
    team: object
    project: object
    plugin: object
    cluster: object
    app: object
    env: object


def _make_org_graph(
    suffix: str,
    *,
    secret_key: str = "SHARED_SECRET",
    secret_val: str = "v",
    plugin_slug: str = "aws",
    region: str = "us-east-1",
) -> _Graph:
    """Build a full single-org graph: org → team → project → cluster →
    app → production env. App slug is ``app-<suffix>`` so two graphs in
    one test have distinct, non-colliding slugs.
    """
    org = Organization.objects.create(name=f"Org {suffix}", slug=f"org-{suffix}")
    team = Team.objects.create(organization=org, name=f"Team {suffix}", slug=f"team-{suffix}")
    project = Project.objects.create(
        organization=org, team=team, name=f"Proj {suffix}", slug=f"proj-{suffix}"
    )
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name=plugin_slug.upper(),
                slug=plugin_slug,
                version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ],
        ignore_conflicts=True,
    )
    plugin = ProviderPlugin.objects.get(slug=plugin_slug)
    cluster = TenantCluster.objects.create(
        organization=org,
        slug=f"cluster-{suffix}",
        name=f"Cluster {suffix}",
        provider_plugin=plugin,
        endpoint="http://localhost:8443",
        region=region,
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name=f"App {suffix}",
        slug=f"app-{suffix}",
        provisioning_status="ready",
        manifest_raw=_MANIFEST_TEMPLATE.format(
            name=f"app-{suffix}", secret_key=secret_key, secret_val=secret_val
        ),
    )
    env = AppEnvironment.objects.create(
        registered_app=app,
        name="production",
        tenant_cluster=cluster,
    )
    return _Graph(org, team, project, plugin, cluster, app, env)


def _make_managed_service(
    graph: _Graph,
    *,
    kind,
    name: str,
    variant: str = "",
    config: dict | None = None,
    connection_secret_ref: str = "",
) -> ManagedService:
    return ManagedService.objects.create(
        registered_app=graph.app,
        app_environment=graph.env,
        kind=kind,
        name=name,
        variant=variant,
        status=ManagedService.Status.ACTIVE,
        config=config or {},
        connection_secret_ref=connection_secret_ref,
    )


def _make_bundle(graph: _Graph, *, slug: str, name: str) -> SecretBundle:
    return SecretBundle.objects.create(
        organization=graph.org,
        team=graph.team,
        slug=slug,
        name=name,
        backend_ref=f"vault:/{slug}",
    )


def _make_bundle_ref(graph: _Graph, bundle: SecretBundle, *, prefix: str = "") -> AppSecretBundleRef:
    return AppSecretBundleRef.objects.create(
        registered_app=graph.app,
        app_environment=graph.env,
        secret_bundle=bundle,
        prefix=prefix,
    )


def _make_proposal(graph: _Graph, *, key: str = "API_KEY") -> SecretChangeProposal:
    return SecretChangeProposal.objects.create(
        registered_app=graph.app,
        app_environment=graph.env,
        environment_name=graph.env.name,
        op=SecretChangeProposal.Op.SET,
        payload={"key": key, "value": "x"},
        payload_diff={"op": "set", "summary": "test"},
        required_approver_count=1,
        expires_at=timezone.now() + timedelta(days=7),
    )


def _make_email_event(svc: ManagedService, *, kind: str, recipient: str, message_id: str) -> EmailEvent:
    return EmailEvent.objects.create(
        managed_service=svc,
        message_id=message_id,
        recipient=recipient,
        subject="hi",
        event_kind=kind,
        metadata={},
        occurred_at=timezone.now(),
    )


def _grant_app_read(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)


def _grant_email(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)


@pytest.fixture(autouse=True)
def _reset_driver_cache():
    """Email-driver overrides are process-local; reset around each test."""
    reset_driver_cache()
    yield
    reset_driver_cache()


class _TemplateDriver:
    """Minimal email driver fake covering only the template surfaces the
    template resolvers touch (list / get / stats). Registered against
    org A's cluster so a *removed* org filter would leak A's templates to
    a caller in org B — making the cross-org tests fail-closed."""

    def __init__(self, *, templates=None, stats=None):
        self._templates = list(templates or [])
        self._stats = list(stats or [])

    def list_templates(self):
        return list(self._templates)

    def get_template(self, *, name):
        for t in self._templates:
            if t.name == name:
                return t
        raise RuntimeError("template not found")  # resolver catches → None

    def get_template_send_statistics(self, *, name, days: int = 14):
        return list(self._stats)


# ======================================================================
# astrolift_app_secrets
# ======================================================================


def test_app_secrets_cross_org_returns_empty(permission_resolver):
    a = _make_org_graph("a", secret_key="A_SECRET")
    _b = _make_org_graph("b", secret_key="B_SECRET")
    _grant_app_read(permission_resolver)

    # Caller in org B asking for org A's app slug → nothing (B has no app-a).
    with _ctx(_b):
        leaked = ServicesQuery().astrolift_app_secrets(_info(_caller()), app_slug=a.app.slug)
    assert leaked == []

    # In-org caller sees their own literal.
    with _ctx(a):
        mine = ServicesQuery().astrolift_app_secrets(_info(_caller()), app_slug=a.app.slug)
    assert "A_SECRET" in {s.key for s in mine}


def test_app_secrets_in_org_only_sees_own_literals(permission_resolver):
    a = _make_org_graph("a", secret_key="A_SECRET")
    _grant_app_read(permission_resolver)
    with _ctx(a):
        rows = ServicesQuery().astrolift_app_secrets(_info(_caller()), app_slug=a.app.slug)
    keys = {s.key for s in rows}
    assert "A_SECRET" in keys
    assert "B_SECRET" not in keys


# ======================================================================
# astrolift_app_secret_history
# ======================================================================


def _seed_secret_audit(graph: _Graph, *, key: str, ip: str) -> None:
    AuditEvent.objects.create(
        organization=graph.org,
        actor_kind="user",
        actor_id="0",
        actor_display="op",
        action="app.secret.set",
        decision="ALLOW",
        target_kind="AppSecret",
        target_id=f"{graph.app.slug}:{key}",
        request_ip=ip,
        data={},
    )


def test_app_secret_history_cross_org_returns_empty(permission_resolver):
    a = _make_org_graph("a")
    _b = _make_org_graph("b")
    _seed_secret_audit(a, key="API_KEY", ip="10.0.0.1")
    _grant_app_read(permission_resolver)

    with _ctx(_b):
        leaked = ServicesQuery().astrolift_app_secret_history(
            _info(_caller()), app_slug=a.app.slug, key="API_KEY"
        )
    assert leaked == []

    with _ctx(a):
        mine = ServicesQuery().astrolift_app_secret_history(
            _info(_caller()), app_slug=a.app.slug, key="API_KEY"
        )
    assert len(mine) == 1
    assert mine[0].source_ip == "10.0.0.1"


def test_app_secret_history_same_slug_audit_is_org_scoped(permission_resolver):
    """Both orgs have an app with the SAME slug + key, so the audit
    ``target_id`` ("slug:key") collides. The audit query must still scope
    by org or org B's history would surface org A's rows."""
    a = _make_org_graph("a")
    b = _make_org_graph("b")
    # Force identical slug on B's app so target_id collides across orgs.
    b.app.slug = a.app.slug
    b.app.save(update_fields=["slug", "updated_at", "version"])

    _seed_secret_audit(a, key="API_KEY", ip="10.0.0.1")
    _seed_secret_audit(b, key="API_KEY", ip="10.0.0.2")
    _grant_app_read(permission_resolver)

    with _ctx(b):
        rows = ServicesQuery().astrolift_app_secret_history(
            _info(_caller()), app_slug=a.app.slug, key="API_KEY"
        )
    # B sees exactly its own row, never A's — even though target_id matches.
    assert [r.source_ip for r in rows] == ["10.0.0.2"]


# ======================================================================
# astrolift_secret_bundles
# ======================================================================


def test_secret_bundles_are_org_scoped(permission_resolver):
    a = _make_org_graph("a")
    b = _make_org_graph("b")
    _make_bundle(a, slug="a-bundle", name="A Bundle")
    _make_bundle(b, slug="b-bundle", name="B Bundle")
    _grant_app_read(permission_resolver)

    with _ctx(a):
        a_rows = ServicesQuery().astrolift_secret_bundles(_info(_caller()))
    with _ctx(b):
        b_rows = ServicesQuery().astrolift_secret_bundles(_info(_caller()))

    assert {r.slug for r in a_rows} == {"a-bundle"}
    assert {r.slug for r in b_rows} == {"b-bundle"}


# ======================================================================
# astrolift_app_secret_bundle_attachments
# ======================================================================


def test_bundle_attachments_cross_org_returns_empty(permission_resolver):
    a = _make_org_graph("a")
    _b = _make_org_graph("b")
    bundle = _make_bundle(a, slug="a-bundle", name="A Bundle")
    _make_bundle_ref(a, bundle, prefix="A_")
    _grant_app_read(permission_resolver)

    with _ctx(_b):
        leaked = ServicesQuery().astrolift_app_secret_bundle_attachments(
            _info(_caller()), app_slug=a.app.slug
        )
    assert leaked == []

    with _ctx(a):
        mine = ServicesQuery().astrolift_app_secret_bundle_attachments(_info(_caller()), app_slug=a.app.slug)
    assert len(mine) == 1
    assert mine[0].bundle_slug == "a-bundle"


# ======================================================================
# astrolift_managed_services
# ======================================================================


def test_managed_services_cross_org_returns_empty(permission_resolver):
    a = _make_org_graph("a")
    _b = _make_org_graph("b")
    _make_managed_service(a, kind=ManagedService.Kind.POSTGRES, name="db", variant="rds")
    _grant_app_read(permission_resolver)

    with _ctx(_b):
        leaked = ServicesQuery().astrolift_managed_services(_info(_caller()), app_slug=a.app.slug)
    assert leaked == []

    with _ctx(a):
        mine = ServicesQuery().astrolift_managed_services(_info(_caller()), app_slug=a.app.slug)
    assert {s.name for s in mine} == {"db"}


# ======================================================================
# astrolift_managed_service_objects
# ======================================================================


def test_managed_service_objects_cross_org_returns_none(permission_resolver):
    a = _make_org_graph("a")
    _b = _make_org_graph("b")
    svc = _make_managed_service(
        a,
        kind=ManagedService.Kind.OBJECT_STORE,
        name="bucket",
        config={"recent_objects": [{"key": "f.txt", "size_bytes": 12}]},
    )
    _grant_app_read(permission_resolver)

    with _ctx(_b):
        leaked = ServicesQuery().astrolift_managed_service_objects(
            _info(_caller()), managed_service_id=GUID(str(svc.guid))
        )
    assert leaked is None

    with _ctx(a):
        mine = ServicesQuery().astrolift_managed_service_objects(
            _info(_caller()), managed_service_id=GUID(str(svc.guid))
        )
    assert mine is not None
    assert [o.key for o in mine.objects] == ["f.txt"]


# ======================================================================
# astrolift_managed_service_queue_depth
# ======================================================================


def test_managed_service_queue_depth_cross_org_returns_none(permission_resolver):
    a = _make_org_graph("a")
    _b = _make_org_graph("b")
    svc = _make_managed_service(
        a,
        kind=ManagedService.Kind.QUEUE,
        name="jobs",
        config={"depth_snapshot": {"depth": 7, "in_flight": 3}},
    )
    _grant_app_read(permission_resolver)

    with _ctx(_b):
        leaked = ServicesQuery().astrolift_managed_service_queue_depth(
            _info(_caller()), managed_service_id=GUID(str(svc.guid))
        )
    assert leaked is None

    with _ctx(a):
        mine = ServicesQuery().astrolift_managed_service_queue_depth(
            _info(_caller()), managed_service_id=GUID(str(svc.guid))
        )
    assert mine is not None
    assert mine.depth == 7
    assert mine.in_flight == 3


# ======================================================================
# astrolift_email_service_detail
# ======================================================================


def _make_email_service(graph: _Graph, *, identity: str = "ses.example.com") -> ManagedService:
    return _make_managed_service(
        graph,
        kind=ManagedService.Kind.EMAIL,
        name="ses",
        variant="ses",
        config={"identity": identity},
    )


def test_email_service_detail_cross_org_returns_none(permission_resolver):
    a = _make_org_graph("a")
    _b = _make_org_graph("b")
    svc = _make_email_service(a, identity="a.example.com")
    _grant_email(permission_resolver)
    # Register a driver for A's cluster so a removed org filter WOULD leak.
    register_driver_override(slug="aws", region="us-east-1", driver=_TemplateDriver())

    with _ctx(_b):
        leaked = ServicesQuery().astrolift_email_service_detail(
            _info(_caller()), managed_service_id=GUID(str(svc.guid))
        )
    assert leaked is None

    with _ctx(a):
        mine = ServicesQuery().astrolift_email_service_detail(
            _info(_caller()), managed_service_id=GUID(str(svc.guid))
        )
    assert mine is not None
    assert mine.identity == "a.example.com"


# ======================================================================
# astrolift_email_templates / template / template_stats
# ======================================================================


def _template_driver_with_data() -> _TemplateDriver:
    return _TemplateDriver(
        templates=[
            EmailTemplate(
                name="welcome",
                subject="Welcome",
                html_body="<p>hi</p>",
                text_body="hi",
                created_at=datetime.now(UTC),
            )
        ],
        stats=[
            TemplateSendStatPoint(
                timestamp=datetime.now(UTC),
                sends=10,
                deliveries=9,
                bounces=1,
                complaints=0,
            )
        ],
    )


def test_email_templates_cross_org_returns_empty(permission_resolver):
    a = _make_org_graph("a")
    _b = _make_org_graph("b")
    svc = _make_email_service(a)
    _grant_email(permission_resolver)
    register_driver_override(slug="aws", region="us-east-1", driver=_template_driver_with_data())

    with _ctx(_b):
        leaked = ServicesQuery().astrolift_email_templates(
            _info(_caller()), managed_service_id=GUID(str(svc.guid))
        )
    assert leaked == []

    with _ctx(a):
        mine = ServicesQuery().astrolift_email_templates(
            _info(_caller()), managed_service_id=GUID(str(svc.guid))
        )
    assert {t.name for t in mine} == {"welcome"}


def test_email_template_cross_org_returns_none(permission_resolver):
    a = _make_org_graph("a")
    _b = _make_org_graph("b")
    svc = _make_email_service(a)
    _grant_email(permission_resolver)
    register_driver_override(slug="aws", region="us-east-1", driver=_template_driver_with_data())

    with _ctx(_b):
        leaked = ServicesQuery().astrolift_email_template(
            _info(_caller()), managed_service_id=GUID(str(svc.guid)), name="welcome"
        )
    assert leaked is None

    with _ctx(a):
        mine = ServicesQuery().astrolift_email_template(
            _info(_caller()), managed_service_id=GUID(str(svc.guid)), name="welcome"
        )
    assert mine is not None
    assert mine.name == "welcome"


def test_email_template_stats_cross_org_returns_empty(permission_resolver):
    a = _make_org_graph("a")
    _b = _make_org_graph("b")
    svc = _make_email_service(a)
    _grant_email(permission_resolver)
    register_driver_override(slug="aws", region="us-east-1", driver=_template_driver_with_data())

    with _ctx(_b):
        leaked = ServicesQuery().astrolift_email_template_stats(
            _info(_caller()), managed_service_id=GUID(str(svc.guid)), name="welcome"
        )
    assert leaked == []

    with _ctx(a):
        mine = ServicesQuery().astrolift_email_template_stats(
            _info(_caller()), managed_service_id=GUID(str(svc.guid)), name="welcome"
        )
    assert len(mine) == 1
    assert mine[0].sends == 10


# ======================================================================
# astrolift_email_messages / engagement_metrics
# ======================================================================


def test_email_messages_cross_org_returns_empty(permission_resolver):
    a = _make_org_graph("a")
    _b = _make_org_graph("b")
    svc = _make_email_service(a)
    _make_email_event(svc, kind=EmailEventKind.DELIVERY, recipient="x@a.com", message_id="m1")
    _grant_email(permission_resolver)

    with _ctx(_b):
        leaked = ServicesQuery().astrolift_email_messages(
            _info(_caller()), managed_service_id=GUID(str(svc.guid))
        )
    assert leaked == []

    with _ctx(a):
        mine = ServicesQuery().astrolift_email_messages(
            _info(_caller()), managed_service_id=GUID(str(svc.guid))
        )
    assert {m.message_id for m in mine} == {"m1"}


def test_email_engagement_metrics_cross_org_returns_none(permission_resolver):
    a = _make_org_graph("a")
    _b = _make_org_graph("b")
    svc = _make_email_service(a)
    _make_email_event(svc, kind=EmailEventKind.SEND, recipient="x@a.com", message_id="m1")
    _make_email_event(svc, kind=EmailEventKind.DELIVERY, recipient="x@a.com", message_id="m1")
    _grant_email(permission_resolver)

    with _ctx(_b):
        leaked = ServicesQuery().astrolift_email_engagement_metrics(
            _info(_caller()), managed_service_id=GUID(str(svc.guid))
        )
    assert leaked is None

    with _ctx(a):
        mine = ServicesQuery().astrolift_email_engagement_metrics(
            _info(_caller()), managed_service_id=GUID(str(svc.guid))
        )
    assert mine is not None
    assert mine.total_sends == 1
    assert mine.total_deliveries == 1


# ======================================================================
# astrolift_secret_change_proposals / proposal
# ======================================================================


def test_secret_change_proposals_are_org_scoped(permission_resolver):
    a = _make_org_graph("a")
    b = _make_org_graph("b")
    _make_proposal(a, key="A_KEY")
    _make_proposal(b, key="B_KEY")
    _grant_app_read(permission_resolver)

    with _ctx(a):
        a_rows = ServicesQuery().astrolift_secret_change_proposals(_info(_caller()))
    with _ctx(b):
        b_rows = ServicesQuery().astrolift_secret_change_proposals(_info(_caller()))

    assert {p.payload["key"] for p in a_rows} == {"A_KEY"}
    assert {p.payload["key"] for p in b_rows} == {"B_KEY"}


def test_secret_change_proposals_by_app_slug_cross_org_empty(permission_resolver):
    a = _make_org_graph("a")
    _b = _make_org_graph("b")
    _make_proposal(a, key="A_KEY")
    _grant_app_read(permission_resolver)

    # B caller filtering by A's app slug must not see A's proposals.
    with _ctx(_b):
        leaked = ServicesQuery().astrolift_secret_change_proposals(_info(_caller()), app_slug=a.app.slug)
    assert leaked == []


def test_secret_change_proposal_detail_cross_org_returns_none(permission_resolver):
    a = _make_org_graph("a")
    _b = _make_org_graph("b")
    proposal = _make_proposal(a, key="A_KEY")
    _grant_app_read(permission_resolver)

    with _ctx(_b):
        leaked = ServicesQuery().astrolift_secret_change_proposal(
            _info(_caller()), id=GUID(str(proposal.guid))
        )
    assert leaked is None

    with _ctx(a):
        mine = ServicesQuery().astrolift_secret_change_proposal(_info(_caller()), id=GUID(str(proposal.guid)))
    assert mine is not None
    assert mine.payload["key"] == "A_KEY"


# ======================================================================
# Registry resolvers fixed in the same sweep (lock the whole sweep)
# ======================================================================


def test_registry_apps_list_is_org_scoped(permission_resolver):
    a = _make_org_graph("a")
    b = _make_org_graph("b")
    _grant_app_read(permission_resolver)

    with _ctx(a):
        a_rows = RegistryQuery().astrolift_apps(_info(_caller()))
    with _ctx(b):
        b_rows = RegistryQuery().astrolift_apps(_info(_caller()))

    assert {app.slug for app in a_rows} == {"app-a"}
    assert {app.slug for app in b_rows} == {"app-b"}


def test_registry_app_detail_cross_org_returns_none(permission_resolver):
    a = _make_org_graph("a")
    _b = _make_org_graph("b")
    _grant_app_read(permission_resolver)

    with _ctx(_b):
        leaked = RegistryQuery().astrolift_app(_info(_caller()), slug=a.app.slug)
    assert leaked is None

    with _ctx(a):
        mine = RegistryQuery().astrolift_app(_info(_caller()), slug=a.app.slug)
    assert mine is not None
    assert mine.slug == "app-a"


def test_registry_workload_cross_org_returns_none(permission_resolver):
    a = _make_org_graph("a")
    _b = _make_org_graph("b")
    Workload.objects.create(registered_app=a.app, name="Web", slug="web")
    _grant_app_read(permission_resolver)

    with _ctx(_b):
        leaked = RegistryQuery().astrolift_workload(_info(_caller()), app_slug=a.app.slug, slug="web")
    assert leaked is None

    with _ctx(a):
        mine = RegistryQuery().astrolift_workload(_info(_caller()), app_slug=a.app.slug, slug="web")
    assert mine is not None
    assert mine.slug == "web"
