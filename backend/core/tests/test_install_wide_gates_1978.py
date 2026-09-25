"""Install-wide operations belong to the platform operator (#1978).

The stock ``org_owner`` and ``org_admin`` roles carry the whole permission
catalogue, ``admin.elevate`` included, and ``cluster_owner`` carries
``cluster.manage``. So a gate that reached the whole install through a
permission alone admitted every organization's admins:

* ``setFeatureFlag`` flipped a Constance flag for every tenant;
* ``resyncAllAstroliftCiWorkflows`` swept every organization's managed apps;
* ``scanCloudOrphans`` read every organization's managed clusters and
  services, and its ``app.delete`` gate also admitted a team owner with the
  team selected;
* ``reapCloudOrphan`` deleted through any organization's managed cluster.

Three ``is_staff`` shortcuts reached as far: every organization's
dispatchers, every organization's name, and the platform workflow
templates. Django staff is not the platform operator either.

Everything runs on real RoleBindings with the stock roles and the real
permission resolver. The install-wide side effects are recorded, not run.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import constance
import pytest
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from graphql import GraphQLError

from astrolift_agents.models import DispatcherInstance
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_identity.api_tokens import (
    SCOPE_ADMIN,
    SCOPE_READ_APPS,
    SCOPE_WRITE_APPS,
    reset_current_api_token,
    set_current_api_token,
)
from astrolift_identity.models import Member, Organization, Role, RoleBinding
from astrolift_identity.schema.queries import IdentityQuery
from astrolift_lifecycle.schema.mutations import LifecycleMutation, ReapCloudOrphanInput
from astrolift_lifecycle.schema.queries import LifecycleQuery
from astrolift_operations.services import orphan_reaper
from astrolift_scm.schema.mutations import ScmMutation
from astrolift_scm.services import ci_workflow_drift
from core import mutations as core_mutations
from core.permissions import PermissionDenied
from core.schema.mutations.feature_flags import FeatureFlagMutations
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, make_info
from workflows.models import WorkflowDefinition
from workflows.schema.mutations import Mutation as LegacyWorkflowMutation

pytestmark = pytest.mark.django_db

User = get_user_model()
RESYNC = importlib.import_module("astrolift_identity.migrations.0034_resync_system_roles_stock_catalogue")


@pytest.fixture
def world():
    w = ScopeWorld("iw1978")
    w.beta = Organization.objects.create(name="Beta", slug="beta-iw1978")
    RESYNC.upsert_system_roles(django_apps, None)
    return w


@pytest.fixture
def effects(monkeypatch):
    """Record each install-wide side effect in place of running it."""

    ran: list[str] = []
    real_config = constance.config

    class _Flags:
        # A flag write is recorded, not applied: the Constance cache is shared
        # by concurrent test runs. Reads still reach the real config, which a
        # new user's post_save hook reads.
        def __getattr__(self, name):
            return getattr(real_config, name)

        def __setattr__(self, name, value):
            ran.append("setFeatureFlag")

    def _sweep(*, limit=None):
        ran.append("resyncAllAstroliftCiWorkflows")
        return ci_workflow_drift.CiWorkflowResyncSummary(
            scanned=0, pushed=0, in_sync=0, repo_drift=0, conflict=0, unknown=0, skipped=0, failed=0
        )

    def _scan(*, kinds=None):
        ran.append("scanCloudOrphans")
        return orphan_reaper.OrphanReport(orphans=[], scanned_kinds=[], incomplete_kinds=[])

    def _reap(*, kind, reap_key, cluster_slug="", force_destroy=False):
        ran.append("reapCloudOrphan")
        return orphan_reaper.ReapResult(ok=True, kind=kind, identifier=reap_key, message="reaped")

    monkeypatch.setattr(constance, "config", _Flags())
    monkeypatch.setattr(ci_workflow_drift, "sweep_ci_workflows", _sweep)
    monkeypatch.setattr(orphan_reaper, "scan_orphans", _scan)
    monkeypatch.setattr(orphan_reaper, "reap_orphan", _reap)
    return ran


def _holder(slug, scope_kind, scope_id):
    user = User.objects.create(username=f"{slug}-iw1978", email=f"{slug}-iw1978@acme.test")
    role = Role.objects.get(slug=slug, is_system=True, organization=None)
    RoleBinding.objects.create(user=user, role=role, scope_kind=scope_kind, scope_id=scope_id)
    return user


def _operator():
    return User.objects.create(
        username="operator-iw1978", email="operator-iw1978@acme.test", is_superuser=True
    )


def _staff():
    return User.objects.create(username="staff-iw1978", email="staff-iw1978@acme.test", is_staff=True)


def _as(world, user, *, team=None):
    return tenant_context(
        TenantContext(
            organization_id=world.org.pk,
            team_id=team.pk if team else None,
            actor_user_id=user.pk,
        )
    )


OPS = {
    "setFeatureFlag": lambda info: FeatureFlagMutations().set_feature_flag(
        info, key="admin.cost_enabled", enabled=True
    ),
    "resyncAllAstroliftCiWorkflows": lambda info: ScmMutation().resync_all_astrolift_ci_workflows(info),
    "scanCloudOrphans": lambda info: LifecycleQuery().scan_cloud_orphans(info),
    "reapCloudOrphan": lambda info: LifecycleMutation().reap_cloud_orphan(
        info,
        input=ReapCloudOrphanInput(kind=orphan_reaper.KIND_IAM_ROLE, reap_key="astrolift-tenant-ghost"),
    ),
}

MUTATIONS = ["reapCloudOrphan", "resyncAllAstroliftCiWorkflows", "setFeatureFlag"]


def _outcome(op, user):
    """``"ran"`` when the operation got past its gates, ``"refused"`` when not.

    The mutations answer a refusal with the PERMISSION_DENIED envelope; the
    scan is a query, so it raises.
    """
    try:
        result = OPS[op](make_info(user))
    except PermissionDenied:
        return "refused"
    if not hasattr(result, "ok") or result.ok:
        return "ran"
    assert [error.code for error in result.errors] == ["PERMISSION_DENIED"], result.errors
    return "refused"


ORG_ROLE_REACH = [
    *[("org_owner", op) for op in sorted(OPS)],
    *[("org_admin", op) for op in sorted(OPS)],
    ("cluster_owner", "reapCloudOrphan"),
]


@pytest.mark.parametrize(("slug", "op"), ORG_ROLE_REACH)
def test_org_roles_are_refused_every_install_wide_operation(world, effects, slug, op):
    holder = _holder(slug, "ORG", world.org.pk)

    with _as(world, holder):
        assert _outcome(op, holder) == "refused"

    assert effects == []


def test_a_team_owner_with_its_team_selected_cannot_scan_the_install(world, effects):
    """``app.delete`` at team scope satisfied the targetless gate once the team
    was selected (a team-scoped token, an ``X-Astrolift-Team`` header)."""
    holder = _holder("team_owner", "TEAM", world.medops.pk)

    with _as(world, holder, team=world.medops):
        assert _outcome("scanCloudOrphans", holder) == "refused"

    assert effects == []


@pytest.mark.parametrize("op", sorted(OPS))
def test_the_platform_operator_runs_every_install_wide_operation(world, effects, op):
    operator = _operator()

    with _as(world, operator):
        assert _outcome(op, operator) == "ran"

    assert effects == [op]


@pytest.mark.parametrize("op", sorted(OPS))
def test_the_operator_s_bearer_token_needs_the_admin_scope(world, effects, op):
    """A CLI token (``read:apps`` + ``write:apps``) lets every ``app.*`` slug
    through the permission ceiling, so it reached the orphan scan. The
    operator's install-wide reach needs the ``admin`` scope (#1949)."""
    operator = _operator()

    marker = set_current_api_token(SimpleNamespace(scopes=[SCOPE_READ_APPS, SCOPE_WRITE_APPS]))
    try:
        with _as(world, operator):
            assert _outcome(op, operator) == "refused"
    finally:
        reset_current_api_token(marker)
    assert effects == []

    marker = set_current_api_token(SimpleNamespace(scopes=[SCOPE_ADMIN]))
    try:
        with _as(world, operator):
            assert _outcome(op, operator) == "ran"
    finally:
        reset_current_api_token(marker)
    assert effects == [op]


@pytest.mark.parametrize("op", MUTATIONS)
def test_an_org_admin_s_refusal_is_audited_as_a_deny(world, effects, op):
    """The refusal reaches ``@mutation_audit`` as its own ``PermissionDenied``.
    Django's would have been recorded as an INTERNAL error."""
    holder = _holder("org_admin", "ORG", world.org.pk)
    captured = []
    original = core_mutations._audit_writer
    core_mutations.register_audit_writer(captured.append)
    try:
        with _as(world, holder):
            OPS[op](make_info(holder))
    finally:
        core_mutations.register_audit_writer(original)

    assert [(entry.decision, entry.error_code) for entry in captured] == [("DENY", "PERMISSION_DENIED")]


# ---------------------------------------------------------------------------
# The is_staff shortcuts
# ---------------------------------------------------------------------------


def test_django_staff_does_not_see_every_organization_s_dispatchers(world):
    for org in (world.org, world.beta):
        DispatcherInstance.objects.create(
            organization=org,
            name=org.name,
            slug=f"{org.slug}-dispatcher",
            endpoint=f"https://{org.slug}.dispatch.invalid",
            cloud=DispatcherInstance.Cloud.AWS,
            backend=DispatcherInstance.Backend.K8S_JOB,
        )

    with pytest.raises(DjangoPermissionDenied):
        AgentsQuery().dispatchers(make_info(_staff()))

    listed = AgentsQuery().dispatchers(make_info(_operator()))
    assert sorted(row.service_url for row in listed) == [
        "https://acme-iw1978.dispatch.invalid",
        "https://beta-iw1978.dispatch.invalid",
    ]


def test_django_staff_lists_only_its_own_organizations(world):
    staff = _staff()
    Member.objects.create(user=staff, scope_kind="ORG", scope_id=world.org.pk)

    assert [org.slug for org in IdentityQuery().astrolift_organizations(make_info(staff))] == [world.org.slug]

    listed = {org.slug for org in IdentityQuery().astrolift_organizations(make_info(_operator()))}
    assert {world.org.slug, world.beta.slug} <= listed


def test_django_staff_cannot_create_platform_templates_or_triggers(world):
    staff = _staff()
    definition = {"name": "Seed", "slug": "seed-iw1978", "model_label": "", "states": [], "transitions": []}

    with _as(world, staff):
        with pytest.raises(DjangoPermissionDenied):
            LegacyWorkflowMutation().create_workflow_definition(make_info(staff), **definition)
        with pytest.raises(DjangoPermissionDenied):
            LegacyWorkflowMutation().create_workflow_trigger(make_info(staff), workflow_slug="seed-iw1978")
    assert not WorkflowDefinition.objects.filter(slug="seed-iw1978").exists()

    operator = _operator()
    with _as(world, operator):
        assert LegacyWorkflowMutation().create_workflow_definition(make_info(operator), **definition).ok
    assert WorkflowDefinition.objects.get(slug="seed-iw1978").organization_id is None


def test_staff_org_admin_edits_its_own_definitions_and_no_template_or_other_org(world):
    """The definition write gate waved Django staff past the platform-template
    and organization checks. An org admin who is also staff keeps its own
    organization's definitions and nothing else; the operator keeps the
    templates."""
    template = WorkflowDefinition.objects.create(
        organization=None, name="Template", slug="template-iw1978", model_label=""
    )
    WorkflowDefinition.objects.create(organization=world.org, name="Own", slug="own-iw1978", model_label="")
    WorkflowDefinition.objects.create(
        organization=world.beta, name="Beta", slug="beta-def-iw1978", model_label=""
    )
    admin = _holder("org_admin", "ORG", world.org.pk)
    admin.is_staff = True
    admin.save(update_fields=["is_staff"])
    mutation = LegacyWorkflowMutation()

    with _as(world, admin):
        own = mutation.update_workflow_definition(make_info(admin), slug="own-iw1978", name="Own, edited")
        refused = mutation.update_workflow_definition(make_info(admin), slug="template-iw1978", name="hacked")
        with pytest.raises(GraphQLError, match="not found"):
            mutation.update_workflow_definition(make_info(admin), slug="beta-def-iw1978", name="hacked")

    assert own.ok, own.errors
    assert not refused.ok
    assert any("platform template" in message for error in refused.errors for message in error.messages)
    template.refresh_from_db()
    assert template.name == "Template"

    operator = _operator()
    with _as(world, operator):
        assert mutation.update_workflow_definition(
            make_info(operator), slug="template-iw1978", name="Seeded"
        ).ok
    template.refresh_from_db()
    assert template.name == "Seeded"
