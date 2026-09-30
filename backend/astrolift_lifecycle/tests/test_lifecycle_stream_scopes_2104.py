# ruff: noqa: F811
"""Persisted lifecycle events use real broker queues, grants and current policies."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async

from astrolift_identity.abac import RequestAttributes, current_attributes, request_attributes
from astrolift_identity.api_tokens import (
    get_current_api_token,
    reset_current_api_token,
    set_current_api_token,
)
from astrolift_identity.models import ApiToken, Member, Policy
from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_lifecycle.tests.test_owner_scopes_2104 import no_search, world  # noqa: F401
from core.permissions import Permission
from core.pubsub import publish, subscriber_count
from core.schema.subscriptions import _CoreSubscription
from core.tenancy import TenantContext, get_current_tenant, tenant_context
from core.tests.utils.scope_world import bind_role

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio]


def _info(world, token=None):
    return SimpleNamespace(
        context=SimpleNamespace(
            user=world.user,
            _ws_tenant=TenantContext(
                organization_id=world.org.pk,
                actor_user_id=world.user.pk,
                team_id=world.platform.pk,
                project_id=world.platform_project.pk,
            ),
            _ws_api_token=token,
        )
    )


def _event(row):
    return {
        "deployment_id": str(row.guid),
        "registered_app_slug": "untrusted-slug",
        "environment_name": "untrusted-environment",
        "status": "running",
        "occurred_at": "2026-09-29T00:00:00Z",
    }


def _grant(world, kind="ORG"):
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    return bind_role(
        world.user,
        permissions=[Permission.APP_READ],
        kind=kind,
        scope_id={"ORG": world.org.pk, "TEAM": world.medops.pk}[kind],
        slug="stream-grant-2104",
    )


async def _registered(topic):
    async def wait():
        while subscriber_count(topic) == 0:
            await asyncio.sleep(0.001)

    await asyncio.wait_for(wait(), 5)


@pytest.mark.parametrize("bearer", [False, True])
async def test_real_broker_filters_sibling_events_normalizes_payload_and_closes_across_tasks(
    world, bearer, monkeypatch
):
    from core.schema import subscriptions

    provenance = []
    real_check = subscriptions._lifecycle_event_for_identity

    def checked(*args):
        provenance.append(args[-1].client_ip)
        return real_check(*args)

    monkeypatch.setattr(subscriptions, "_lifecycle_event_for_identity", checked)
    await sync_to_async(_grant)(world, "TEAM")
    token = (
        await sync_to_async(ApiToken.objects.create)(
            user=world.user,
            organization=world.org,
            team=world.medops,
            name="Stream",
            token_hash="stream-2104",
            scopes=["read:apps"],
        )
        if bearer
        else None
    )
    stream = _CoreSubscription().astrolift_deployment_lifecycle_stream(_info(world, token))
    topic = f"deployment.lifecycle.{world.org.pk}"
    with request_attributes(RequestAttributes(actor_user_id=world.user.pk, client_ip="203.0.113.1")):
        first = asyncio.create_task(anext(stream))
    try:
        await _registered(topic)
        await publish(topic, _event(world.rows["platform"]["deployment"]))
        await publish(topic, _event(world.rows["medops"]["deployment"]))
        event = await asyncio.wait_for(first, 5)
        assert event.deployment_id == str(world.rows["medops"]["deployment"].guid)
        assert event.registered_app_slug == world.medops_app.slug
        assert event.environment_name == "production"
        # A later source iteration runs in a different caller's context.
        unrelated = TenantContext(organization_id=0, actor_user_id=None)

        async def next_from_other_context():
            with (
                tenant_context(unrelated),
                request_attributes(
                    RequestAttributes(
                        actor_user_id=world.user.pk,
                        client_ip="203.0.113.2",
                        environment="caller-only",
                        region="caller-region",
                    )
                ),
            ):
                value = await anext(stream)
                assert get_current_tenant() == unrelated
                assert current_attributes().environment == "caller-only"
                assert get_current_api_token() is None
                return value

        second = asyncio.create_task(next_from_other_context())
        await publish(topic, _event(world.rows["medops"]["deployment"]))
        assert (await asyncio.wait_for(second, 5)).deployment_id == event.deployment_id
    finally:
        if not first.done():
            first.cancel()
        await asyncio.create_task(stream.aclose())
    assert subscriber_count(topic) == 0
    assert provenance == ["203.0.113.1"] * 3


@pytest.mark.parametrize("authority", ["none", "no-actor", "read-ceiling", "named-sibling", "foreign-token"])
async def test_refused_stream_finishes_without_registering_a_broker_queue(world, authority):
    token = None
    if authority != "none":
        await sync_to_async(_grant)(world, "TEAM" if authority == "named-sibling" else "ORG")
    if authority in {"read-ceiling", "foreign-token"}:
        token = await sync_to_async(ApiToken.objects.create)(
            user=world.user,
            organization=world.org,
            name="Denied",
            token_hash="denied-2104",
            scopes=["project:write"] if authority == "read-ceiling" else ["admin"],
        )
        if authority == "foreign-token":
            token.organization_id = world.org.pk + 100000
    info = _info(world, token)
    if authority == "no-actor":
        info.context._ws_tenant = TenantContext(organization_id=world.org.pk)
    marker = set_current_api_token(None)
    try:
        with tenant_context(None):
            stream = _CoreSubscription().astrolift_deployment_lifecycle_stream(
                info, app_slug=world.platform_app.slug if authority == "named-sibling" else None
            )
            assert [row async for row in stream] == []
    finally:
        reset_current_api_token(marker)
    assert subscriber_count(f"deployment.lifecycle.{world.org.pk}") == 0
    assert subscriber_count(f"deployment.lifecycle.app.{world.platform_app.guid}") == 0


@pytest.mark.parametrize(
    "invalid",
    ["deleted-environment", "sibling-environment", "sibling-workload", "foreign-cluster", "deleted-app"],
)
async def test_stream_rejects_persisted_owner_mismatches_and_deleted_rows(world, invalid):
    await sync_to_async(_grant)(world)
    row = world.rows["medops"]["deployment"]

    def corrupt():
        if invalid == "deleted-environment":
            row.app_environment.soft_delete()
        elif invalid == "sibling-environment":
            Deployment.objects.filter(pk=row.pk).update(app_environment=world.rows["platform"]["environment"])
        elif invalid == "sibling-workload":
            Deployment.objects.filter(pk=row.pk).update(
                workload=world.rows["platform"]["command_run"].workload
            )
        elif invalid == "foreign-cluster":
            cluster = row.app_environment.tenant_cluster
            cluster.pk = None
            cluster.guid = uuid4()
            cluster.slug = "foreign-stream-cluster-2104"
            # A real foreign organization instead of a broken FK.
            from astrolift_identity.models import Organization

            cluster.organization = Organization.objects.create(name="Foreign", slug="stream-foreign-2104")
            cluster.save()
            row.app_environment.tenant_cluster = cluster
            row.app_environment.save()
        else:
            world.medops_app.soft_delete()

    await sync_to_async(corrupt)()
    stream = _CoreSubscription().astrolift_deployment_lifecycle_stream(_info(world))
    topic = f"deployment.lifecycle.{world.org.pk}"
    pending = asyncio.create_task(anext(stream))
    try:
        await _registered(topic)
        await publish(topic, _event(row))
        await publish(topic, _event(world.rows["platform"]["deployment"]))
        assert (await asyncio.wait_for(pending, 5)).deployment_id == str(
            world.rows["platform"]["deployment"].guid
        )
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.create_task(stream.aclose())
    assert subscriber_count(topic) == 0


@pytest.mark.parametrize("fact", ["environment", "region"])
async def test_stream_checks_each_persisted_environment_and_region_instead_of_event_labels(world, fact):
    await sync_to_async(_grant)(world)

    def setup():
        Policy.objects.create(
            organization=world.org,
            name="Production deny",
            slug="stream-policy-2104",
            effect="DENY",
            scope_level="ORG",
            action_pattern="app.read",
            resource_pattern={"env": ["production"]} if fact == "environment" else {"region": ["us-east-1"]},
        )
        cluster = world.rows["medops"]["environment"].tenant_cluster
        cluster.region = "us-east-1"
        cluster.save()
        cluster.pk = None
        cluster.guid = uuid4()
        cluster.slug = "allowed-stream-region-2104"
        cluster.region = "us-west-2"
        cluster.save()
        env = AppEnvironment.objects.create(
            registered_app=world.medops_app,
            name="staging",
            tenant_cluster=cluster,
        )
        return Deployment.objects.create(registered_app=world.medops_app, app_environment=env)

    allowed = await sync_to_async(setup)()
    stream = _CoreSubscription().astrolift_deployment_lifecycle_stream(
        _info(world), app_slug=world.medops_app.slug
    )
    topic = f"deployment.lifecycle.app.{world.medops_app.guid}"
    pending = asyncio.create_task(anext(stream))
    try:
        await _registered(topic)
        denied = _event(world.rows["medops"]["deployment"])
        denied["environment_name"] = "staging"
        denied["region"] = "us-west-2"
        await publish(topic, denied)
        await publish(topic, _event(allowed))
        event = await asyncio.wait_for(pending, 5)
        assert event.deployment_id == str(allowed.guid) and event.environment_name == "staging"
    finally:
        if not pending.done():
            pending.cancel()
        await stream.aclose()
    assert subscriber_count(topic) == 0


@pytest.mark.parametrize("revocation", ["binding", "membership", "token", "policy"])
async def test_stream_rechecks_revocation_with_fresh_event_cache(world, revocation, monkeypatch):
    from core.schema import subscriptions

    processed = asyncio.Queue()
    loop = asyncio.get_running_loop()
    real_check = subscriptions._lifecycle_event_for_identity

    def checked(*args):
        result = real_check(*args)
        loop.call_soon_threadsafe(processed.put_nowait, result)
        return result

    monkeypatch.setattr(subscriptions, "_lifecycle_event_for_identity", checked)
    binding = await sync_to_async(_grant)(world)
    token = (
        await sync_to_async(ApiToken.objects.create)(
            user=world.user, organization=world.org, name="Live", token_hash="live-2104", scopes=["read:apps"]
        )
        if revocation == "token"
        else None
    )
    topic = f"deployment.lifecycle.{world.org.pk}"
    stream = _CoreSubscription().astrolift_deployment_lifecycle_stream(_info(world, token))
    first = asyncio.create_task(anext(stream))
    await _registered(topic)
    await publish(topic, _event(world.rows["medops"]["deployment"]))
    await asyncio.wait_for(first, 5)
    assert await asyncio.wait_for(processed.get(), 5) is not None

    def revoke():
        if revocation == "binding":
            binding.soft_delete()
        elif revocation == "membership":
            Member.objects.filter(user=world.user).update(is_active=False)
        elif revocation == "token":
            ApiToken.objects.filter(pk=token.pk).update(is_revoked=True)
        else:
            Policy.objects.create(
                organization=world.org,
                name="Stop",
                slug="stop-2104",
                effect="DENY",
                scope_level="ORG",
                action_pattern="app.read",
            )

    await sync_to_async(revoke)()
    pending = asyncio.create_task(anext(stream))
    await publish(topic, _event(world.rows["medops"]["deployment"]))
    assert await asyncio.wait_for(processed.get(), 5) is None
    assert not pending.done()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    await stream.aclose()
    assert subscriber_count(topic) == 0


async def test_stream_approvals_use_distinct_persisted_voters_and_refresh_between_events(world):
    from astrolift_lifecycle.models import DeploymentApproval
    from core.tests.utils.scope_world import make_user

    await sync_to_async(_grant)(world)
    bad = world.rows["medops"]["deployment"]

    def setup():
        other = make_user("stream-second-voter-2104")
        allowed = Deployment.objects.create(
            registered_app=world.medops_app, app_environment=bad.app_environment
        )
        Deployment.objects.filter(pk=bad.pk).update(approvals_received=99)
        DeploymentApproval.objects.create(deployment=bad, voter_user_id=world.user.pk)
        DeploymentApproval.objects.create(deployment=bad, credential_hash="f" * 64)
        for voter in (world.user.pk, other.pk):
            DeploymentApproval.objects.create(deployment=allowed, voter_user_id=voter)
        Policy.objects.create(
            organization=world.org,
            name="Two approvers",
            slug="stream-approvals-2104",
            effect="DENY",
            scope_level="ORG",
            action_pattern="app.read",
            conditions=[{"kind": "approval_required", "min_approvers": 2}],
        )
        return allowed, other

    allowed, other = await sync_to_async(setup)()
    stream = _CoreSubscription().astrolift_deployment_lifecycle_stream(_info(world))
    topic = f"deployment.lifecycle.{world.org.pk}"
    first = asyncio.create_task(anext(stream))
    try:
        await _registered(topic)
        await publish(topic, _event(bad))
        await publish(topic, _event(allowed))
        assert (await asyncio.wait_for(first, 5)).deployment_id == str(allowed.guid)
        await sync_to_async(DeploymentApproval.objects.create)(deployment=bad, voter_user_id=other.pk)
        second = asyncio.create_task(anext(stream))
        await publish(topic, _event(bad))
        assert (await asyncio.wait_for(second, 5)).deployment_id == str(bad.guid)
    finally:
        if not first.done():
            first.cancel()
        await stream.aclose()
    assert subscriber_count(topic) == 0
