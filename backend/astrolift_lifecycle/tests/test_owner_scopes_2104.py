"""Lifecycle records authorize their live app, independent of selected headers."""

from contextlib import contextmanager
from uuid import uuid4

import pytest

from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken
from astrolift_lifecycle import scopes
from astrolift_lifecycle.models import (
    AppEnvironment,
    CommandRun,
    CustomDomain,
    Deployment,
    DeployToken,
    PreviewEnvironment,
    ScheduledJobRun,
    TaskRun,
)
from astrolift_registry.models import Workload
from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind, check_permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_search(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, row: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, row: None))


@pytest.fixture
def world():
    world = ScopeWorld("lifecycle2104")
    world.user = make_user("lifecycle2104")
    cluster = make_cluster(world, "lifecycle2104")
    world.rows = {}
    for prefix in ("medops", "platform"):
        app = getattr(world, prefix + "_app")
        env = AppEnvironment.objects.create(registered_app=app, name="production", tenant_cluster=cluster)
        workload = Workload.objects.create(registered_app=app, name=prefix, slug=prefix, kind="task")
        world.rows[prefix] = {
            "deployment": Deployment.objects.create(registered_app=app, app_environment=env),
            "environment": env,
            "custom_domain": CustomDomain.objects.create(
                registered_app=app, hostname=prefix + ".example.test"
            ),
            "deploy_token": DeployToken.objects.create(registered_app=app, name=prefix, token_hash="unused"),
            "preview_environment": PreviewEnvironment.objects.create(
                registered_app=app, app_environment=env, branch=prefix
            ),
            "scheduled_job_run": ScheduledJobRun.objects.create(workload=workload, app_environment=env),
            "command_run": CommandRun.objects.create(registered_app=app, workload=workload),
            "task_run": TaskRun.objects.create(workload=workload, app_environment=env),
        }
    return world


@contextmanager
def subject(world, token=None):
    marker = set_current_api_token(token)
    try:
        with tenant_context(
            TenantContext(
                organization_id=world.org.pk,
                team_id=world.platform.pk,
                project_id=world.platform_project.pk,
                actor_user_id=world.user.pk,
            )
        ):
            yield
    finally:
        reset_current_api_token(marker)


FAMILIES = (
    "deployment",
    "environment",
    "custom_domain",
    "deploy_token",
    "preview_environment",
    "scheduled_job_run",
    "command_run",
    "task_run",
)


def authorize(family, guid):
    factory = getattr(scopes, family + "_app_scope")("id", permission=Permission.APP_READ)
    scope = factory({"id": str(guid)})
    check_permission(Permission.APP_READ, scope=scope)
    return scope


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM", "ORG"])
def test_owner_grants_ignore_selected_sibling_headers(world, family, kind):
    owner = {
        "APP": world.medops_app,
        "PROJECT": world.medops_project,
        "TEAM": world.medops,
        "ORG": world.org,
    }[kind]
    bind_role(world.user, permissions=[Permission.APP_READ], kind=kind, scope_id=owner.pk, slug="owner")
    with subject(world):
        assert authorize(family, world.rows["medops"][family].guid) == PermissionScope(
            kind=ScopeKind.APP, id=world.medops_app.pk
        )
        if kind == "ORG":
            authorize(family, world.rows["platform"][family].guid)
        else:
            with pytest.raises(PermissionDenied):
                authorize(family, world.rows["platform"][family].guid)
        if kind == "ORG":
            assert authorize(family, uuid4()) == PermissionScope(kind=ScopeKind.ORG, id=world.org.pk)
        else:
            with pytest.raises(PermissionDenied):
                authorize(family, uuid4())


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize(
    "invalid", ["deleted-row", "deleted-app", "deleted-project", "foreign-project", "incoherent-team"]
)
def test_stale_record_owners_do_not_borrow_selected_team_grants(world, family, invalid):
    bind_role(
        world.user, permissions=[Permission.APP_READ], kind="TEAM", scope_id=world.medops.pk, slug="owner"
    )
    row = world.rows["medops"][family]
    if invalid == "deleted-row":
        row.soft_delete()
    elif invalid == "deleted-app":
        world.medops_app.soft_delete()
    elif invalid == "deleted-project":
        world.medops_project.soft_delete()
    elif invalid == "foreign-project":
        foreign = ScopeWorld("foreign-lifecycle2104")
        world.medops_app.project = foreign.medops_project
        world.medops_app.save(update_fields=["project", "updated_at", "version"])
    else:
        world.medops_app.team = world.platform
        world.medops_app.save(update_fields=["team", "updated_at", "version"])
    with subject(world), pytest.raises(PermissionDenied):
        authorize(family, row.guid)


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("operator", [False, True])
def test_team_bearer_cannot_borrow_its_owners_broader_authority(world, family, operator):
    bind_role(
        world.user, permissions=[Permission.APP_READ], kind="ORG", scope_id=world.org.pk, slug="org-reader"
    )
    if operator:
        world.user.is_superuser = True
        world.user.save(update_fields=["is_superuser"])
    token = ApiToken.objects.create(
        organization=world.org,
        team=world.medops,
        user=world.user,
        name="owner",
        token_hash="unused",
        scopes=["read:apps"],
    )
    with subject(world, token):
        authorize(family, world.rows["medops"][family].guid)
        with pytest.raises(PermissionDenied):
            authorize(family, world.rows["platform"][family].guid)
        with pytest.raises(PermissionDenied):
            authorize(family, uuid4())
