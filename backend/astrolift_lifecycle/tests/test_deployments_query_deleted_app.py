"""
astrolift_deployments hides deregistered apps (#1103).

Deployments are soft-deleted with their app, but the deployment rows
themselves aren't — so a deregistered / torn-down app's deploys kept
surfacing in the list. The list query now filters
``registered_app__deleted_at__isnull=True``. The single-deployment
(by id) query is intentionally unfiltered — an operator holding the
direct link can still open it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_lifecycle.models import Deployment
from astrolift_lifecycle.schema.queries import LifecycleQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _make_running(app, env, tag):
    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind="manual",
        status=Deployment.Status.RUNNING.value,
        image_tag=tag,
    )


def test_deregistered_app_hidden_from_list_but_fetchable_by_id(app, env, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    live = _make_running(app, env, "v1")

    # A separate app that gets deregistered (soft-deleted) so `live` stays
    # visible while the dead app's deploy is filtered out.
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import RegisteredApp

    dead_app = RegisteredApp.objects.create(
        organization=app.organization,
        project=app.project,
        team=app.team,
        name="Gone",
        slug="gone-app",
        provisioning_status="ready",
    )
    dead_env = AppEnvironment.objects.create(
        registered_app=dead_app,
        tenant_cluster=env.tenant_cluster,
        name="prod",
        url="https://gone.example.com",
        required_approvals=0,
    )
    dead = _make_running(dead_app, dead_env, "v0")
    # Deregister soft-deletes the app (and its workloads) but leaves the
    # deployment rows intact — that's exactly the state that flooded the
    # list before the filter.
    dead_app.soft_delete()

    with tenant_context(TenantContext(organization_id=app.organization_id)):
        rows = LifecycleQuery().astrolift_deployments(_info())
        ids = {str(r.id) for r in rows}

        # The live app's deploy shows; the dead app's deploy doesn't.
        assert str(live.guid) in ids
        assert str(dead.guid) not in ids

        # Still fetchable by id (direct link).
        one = LifecycleQuery().astrolift_deployment(_info(), id=str(dead.guid))
        assert one is not None
        assert str(one.id) == str(dead.guid)
