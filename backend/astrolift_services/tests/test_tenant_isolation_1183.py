"""Cross-org tenant-isolation regression tests for the services *mutation*
surface (#1183).

Sibling of ``test_tenant_isolation_1042.py`` (which covers the read
resolvers). ``@tenant_scoped()`` only asserts a tenant context EXISTS — it
does NOT filter any queryset, and ``@require_permission`` checks the
caller's role in their OWN org. So a mutation that fetches its target by
guid/slug without an org clause will happily read *and write* another
tenant's row.

Each test proves, for a mutation fixed in the sweep, that:

  * a caller in org B cannot act on org A's row (NOT_FOUND, no side effect
    on A's data, no plaintext disclosed), AND
  * the in-org caller still succeeds.

The tests are constructed to FAIL if the org filter is removed from the
mutation under test — e.g. the cross-org ``approve_secret_change`` case
seeds an app with an EMPTY approver set, so without the org clause org B's
SECRET_APPROVE holder would be eligible and the proposal would be APPLIED
cross-org (the exact bug #1183 flags). Real Postgres, no DB mocks.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_graphql import GUID
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_manifest.env_edit import read_app_env
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedService, SecretChangeProposal
from astrolift_services.schema.mutations import (
    ApproveSecretChangeInput,
    DeprovisionManagedServiceInput,
    RevealAppSecretInput,
    ServicesMutation,
)
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
    if user is None:
        request = SimpleNamespace(user=None, META={})
    else:
        request = SimpleNamespace(user=user, META={})
    return SimpleNamespace(context=SimpleNamespace(user=user, request=request))


def _make_user(username: str):
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={"email": f"{username}@example.test"},
    )
    return user


def _ctx(graph, *, actor=None):
    return tenant_context(
        TenantContext(
            organization_id=graph.org.id,
            actor_user_id=actor.pk if actor else None,
        )
    )


class _Graph:
    def __init__(self, org, team, project, plugin, cluster, app, env):
        self.org = org
        self.team = team
        self.project = project
        self.plugin = plugin
        self.cluster = cluster
        self.app = app
        self.env = env


def _make_org_graph(
    suffix: str,
    *,
    secret_key: str = "API_KEY",
    secret_val: str = "topsecret",
    requires_secret_approval: bool = False,
    min_approvals: int = 1,
) -> _Graph:
    """Build a full single-org graph. App slug is ``app-<suffix>`` so two
    graphs in one test have distinct, non-colliding slugs."""
    org = Organization.objects.create(name=f"Org {suffix}", slug=f"org-{suffix}")
    team = Team.objects.create(organization=org, name=f"Team {suffix}", slug=f"team-{suffix}")
    project = Project.objects.create(
        organization=org, team=team, name=f"Proj {suffix}", slug=f"proj-{suffix}"
    )
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="AWS",
                slug="aws",
                plugin_version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ],
        ignore_conflicts=True,
    )
    plugin = ProviderPlugin.objects.get(slug="aws")
    cluster = TenantCluster.objects.create(
        organization=org,
        slug=f"cluster-{suffix}",
        name=f"Cluster {suffix}",
        provider_plugin=plugin,
        endpoint="http://localhost:8443",
        region="us-east-1",
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
        requires_secret_approval=requires_secret_approval,
        secret_minimum_approvals=min_approvals,
    )
    env = AppEnvironment.objects.create(
        registered_app=app,
        name="production",
        tenant_cluster=cluster,
    )
    return _Graph(org, team, project, plugin, cluster, app, env)


def _make_managed_service(graph: _Graph, *, name: str = "db") -> ManagedService:
    return ManagedService.objects.create(
        registered_app=graph.app,
        app_environment=graph.env,
        kind=ManagedService.Kind.POSTGRES,
        name=name,
        variant="rds",
        status=ManagedService.Status.ACTIVE,
        config={},
    )


def _make_proposal(
    graph: _Graph, *, proposer, key: str = "PROPOSED_KEY", value: str = "v"
) -> SecretChangeProposal:
    return SecretChangeProposal.objects.create(
        registered_app=graph.app,
        app_environment=graph.env,
        environment_name=graph.env.name,
        proposer=proposer,
        op=SecretChangeProposal.Op.SET,
        payload={"key": key, "value": value},
        payload_diff={"op": "set", "summary": "test"},
        required_approver_count=1,
        expires_at=timezone.now() + timedelta(days=7),
        created_by=proposer,
        updated_by=proposer,
    )


# ======================================================================
# reveal_app_secret — plaintext disclosure must be org-scoped (#424/#1183)
# ======================================================================


def test_reveal_app_secret_cross_org_no_plaintext(permission_resolver):
    a = _make_org_graph("a", secret_key="API_KEY", secret_val="a-plaintext")
    b = _make_org_graph("b")
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.SECRET_READ)

    # Caller in org B asks to reveal org A's literal by A's slug + id.
    with _ctx(b):
        leaked = ServicesMutation().reveal_app_secret(
            _info(user=_make_user("b-op")),
            input=RevealAppSecretInput(
                app_slug=a.app.slug,
                secret_id=f"literal:{a.env.name}:API_KEY",
            ),
        )
    assert leaked.ok is False
    assert leaked.errors[0].code == "NOT_FOUND"
    # The plaintext must NEVER be handed to a cross-org caller.
    assert leaked.data is None


def test_reveal_app_secret_same_org_returns_plaintext(permission_resolver):
    a = _make_org_graph("a", secret_key="API_KEY", secret_val="a-plaintext")
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.SECRET_READ)

    with _ctx(a):
        mine = ServicesMutation().reveal_app_secret(
            _info(user=_make_user("a-op")),
            input=RevealAppSecretInput(
                app_slug=a.app.slug,
                secret_id=f"literal:{a.env.name}:API_KEY",
            ),
        )
    assert mine.ok, mine.errors
    assert mine.data.value == "a-plaintext"
    assert mine.data.key == "API_KEY"


# ======================================================================
# deprovision_managed_service — destructive, must be org-scoped (#320/#1183)
# ======================================================================


def test_deprovision_managed_service_cross_org_not_found(permission_resolver):
    a = _make_org_graph("a")
    b = _make_org_graph("b")
    svc = _make_managed_service(a)
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(b), patch("astrolift_workflows.client.start_workflow") as start_wf:
        leaked = ServicesMutation().deprovision_managed_service(
            _info(user=_make_user("b-op")),
            input=DeprovisionManagedServiceInput(id=GUID(str(svc.guid))),
        )
    assert leaked.ok is False
    assert leaked.errors[0].code == "NOT_FOUND"
    # No deprovision workflow may be started for another tenant's service…
    start_wf.assert_not_called()
    # …and A's service must be untouched (status not flipped).
    svc.refresh_from_db()
    assert svc.status == ManagedService.Status.ACTIVE
    assert svc.deleted_at is None


def test_deprovision_managed_service_same_org_works(permission_resolver):
    a = _make_org_graph("a")
    svc = _make_managed_service(a)
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(a), patch("astrolift_workflows.client.start_workflow") as start_wf:
        mine = ServicesMutation().deprovision_managed_service(
            _info(user=_make_user("a-op")),
            input=DeprovisionManagedServiceInput(id=GUID(str(svc.guid))),
        )
    assert mine.ok, mine.errors
    start_wf.assert_called_once()
    svc.refresh_from_db()
    assert svc.status == ManagedService.Status.DEPROVISIONING


# ======================================================================
# approve_secret_change — a cross-org proposal must NEVER be applied
# ======================================================================


def test_approve_secret_change_cross_org_not_applied(permission_resolver):
    """The #1183 headline bug: org B's SECRET_APPROVE holder approving
    org A's proposal. With an EMPTY approver set on A's app, any
    SECRET_APPROVE holder is eligible — so WITHOUT the org filter the
    proposal would be found, pass eligibility, and be APPLIED cross-org.
    The fix makes it NOT_FOUND and leaves A's proposal untouched."""
    a = _make_org_graph("a", requires_secret_approval=True, min_approvals=1)
    b = _make_org_graph("b")
    a_proposer = _make_user("a-proposer")
    b_approver = _make_user("b-approver")
    proposal = _make_proposal(a, proposer=a_proposer, key="A_ONLY", value="a-secret")
    permission_resolver.grant(Permission.SECRET_APPROVE)

    with _ctx(b, actor=b_approver):
        leaked = ServicesMutation().approve_secret_change(
            _info(user=b_approver),
            input=ApproveSecretChangeInput(proposal_id=GUID(str(proposal.guid))),
        )
    assert leaked.ok is False
    assert leaked.errors[0].code == "NOT_FOUND"
    # The proposal must stay pending — never applied by a foreign tenant.
    proposal.refresh_from_db()
    assert proposal.status == SecretChangeProposal.Status.PENDING.value
    # And A's manifest must not have been mutated with the proposed key.
    a.app.refresh_from_db()
    merged = read_app_env(a.app.manifest_raw_staged or a.app.manifest_raw)
    assert "A_ONLY" not in merged


def test_approve_secret_change_same_org_applies(permission_resolver):
    a = _make_org_graph("a", requires_secret_approval=True, min_approvals=1)
    a_proposer = _make_user("a-proposer")
    a_approver = _make_user("a-approver")
    proposal = _make_proposal(a, proposer=a_proposer, key="A_ONLY", value="a-secret")
    permission_resolver.grant(Permission.SECRET_APPROVE)

    with _ctx(a, actor=a_approver):
        mine = ServicesMutation().approve_secret_change(
            _info(user=a_approver),
            input=ApproveSecretChangeInput(proposal_id=GUID(str(proposal.guid))),
        )
    assert mine.ok, mine.errors
    proposal.refresh_from_db()
    assert proposal.status == SecretChangeProposal.Status.APPLIED.value
    a.app.refresh_from_db()
    assert read_app_env(a.app.manifest_raw_staged)["A_ONLY"] == "a-secret"
