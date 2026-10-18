"""A native GKE rollback runs the prior image through the ordinary native pipeline (#2278)."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from astrolift_identity.models import Member
from astrolift_lifecycle.deployment_identity_origin import deployment_origin
from astrolift_lifecycle.models import Deployment
from astrolift_lifecycle.tests.test_native_deployment_origin_2278 import APPROVE, reviewer
from astrolift_lifecycle.tests.test_native_deployment_origin_2278 import world as origin_world
from astrolift_services.tests.test_cluster_model_queries_2213 import grant
from astrolift_services.tests.test_model_connection_2270 import graphql_http, http_token
from core.permissions import Permission
from core.tests.utils.scope_world import bind_role, make_user

pytestmark = pytest.mark.django_db(transaction=True)
world = origin_world
ROLLBACK = (
    "mutation($input:DeploymentByIdInput!){rollbackDeployment(input:$input)"
    "{ok errors{code message} data{id status}}}"
)


def _history(w):
    now = timezone.now()
    prior = Deployment.objects.create(
        registered_app=w.medops_app,
        app_environment=w.env,
        status="superseded",
        image_tag="good-v1",
        image_digest="sha256:" + "1" * 64,
        commit_sha="a" * 40,
    )
    current = Deployment.objects.create(
        registered_app=w.medops_app,
        app_environment=w.env,
        status="running",
        image_tag="bad-v2",
        image_digest="sha256:" + "2" * 64,
    )
    Deployment.objects.filter(pk=prior.pk).update(created_at=now - timedelta(hours=1))
    Deployment.objects.filter(pk=current.pk).update(created_at=now)
    return Deployment.objects.get(pk=prior.pk), Deployment.objects.get(pk=current.pk)


def _rollback(w, client, current, headers=None):
    return graphql_http(client, headers or w.headers, ROLLBACK, {"input": {"id": str(current.guid)}})["data"][
        "rollbackDeployment"
    ]


def test_native_rollback_waits_for_quorum_then_starts_the_native_deploy_workflow(world, client):
    w = world
    grant(w, Permission.APP_ROLLBACK, "APP", w.medops_app.pk)
    w.env.required_approvals = 1
    w.env.save()
    prior, current = _history(w)

    result = _rollback(w, client, current)
    assert result["ok"], result["errors"]
    row = Deployment.objects.get(guid=result["data"]["id"])
    assert row.trigger_kind == "rollback" and row.status == "pending_approval"
    assert (row.image_tag, row.image_digest, row.commit_sha) == (
        prior.image_tag,
        prior.image_digest,
        prior.commit_sha,
    )
    assert row.promoted_from_id == prior.pk and row.approvals_required == 1
    origin = deployment_origin(row)
    assert origin.permission == Permission.APP_DEPLOY.value and origin.actor_user_id == w.user.pk
    # The bad deploy stays live until the rollback is observed running.
    current.refresh_from_db()
    assert current.status == "running" and not w.starts

    _, headers = reviewer(w, "native-rollback-reviewer-")
    approved = graphql_http(client, headers, APPROVE, {"input": {"id": str(row.guid)}})["data"][
        "approveDeployment"
    ]
    assert approved["ok"], approved["errors"]
    [(name, input)] = w.starts
    assert name == "DeployAppWorkflow"
    assert input.identity_authority == origin and input.deployment_id == row.pk
    assert input.trigger_kind == "rollback" and input.image_tags == {"app": prior.image_tag}


def test_native_rollback_without_quorum_starts_at_once_and_never_takes_the_legacy_workflow(world, client):
    w = world
    grant(w, Permission.APP_ROLLBACK, "APP", w.medops_app.pk)
    _, current = _history(w)
    result = _rollback(w, client, current)
    assert result["ok"], result["errors"]
    [(name, input)] = w.starts
    assert name == "DeployAppWorkflow" and input.identity_authority is not None
    assert Deployment.objects.get(guid=result["data"]["id"]).status == "pending"


def test_native_rollback_needs_the_deploy_authority_it_carries(world, client):
    w = world
    operator = make_user("rollback-only-" + uuid4().hex)
    Member.objects.create(user=operator, scope_kind="ORG", scope_id=w.org.pk)
    bind_role(
        operator,
        permissions=[Permission.APP_ROLLBACK, Permission.APP_READ],
        kind="APP",
        scope_id=w.medops_app.pk,
        slug="rollback-only-" + uuid4().hex,
    )
    _, headers = http_token(w, actor=operator, scopes=("read:apps", "write:apps"))
    _, current = _history(w)
    before = Deployment.objects.count()
    reply = graphql_http(client, headers, ROLLBACK, {"input": {"id": str(current.guid)}})
    assert not (reply.get("data") or {}).get("rollbackDeployment", {}).get("ok")
    assert Deployment.objects.count() == before and not w.starts
    current.refresh_from_db()
    assert current.status == "running"
